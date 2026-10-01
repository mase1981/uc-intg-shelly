"""Shelly IR emitter - devices exposing ``IR.EmitRaw`` (detected via ``Shelly.ListMethods``).

The Remote sends PRONTO hex codes; they are converted to raw mark/space
timings in microseconds plus the carrier frequency, which is what
``IR.EmitRaw`` accepts (``timings``, ``freq``, ``repeats``).
"""

import logging
from typing import Any

from ucapi import StatusCodes, ir_emitter
from ucapi_framework import IREmitterEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import P_IR, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)

_PRONTO_CLOCK = 0.241246  # microseconds per PRONTO frequency unit


def pronto_to_raw(code: str) -> tuple[list[int], int]:
    """Convert a learned (``0000``) PRONTO hex code to (timings_us, carrier_hz)."""
    words = [int(w, 16) for w in code.replace(",", " ").split()]
    if len(words) < 6 or words[0] != 0x0000:
        raise ValueError("Only raw (0000) PRONTO codes are supported")
    if words[1] == 0:
        raise ValueError("Invalid PRONTO frequency")
    freq = int(round(1_000_000 / (words[1] * _PRONTO_CLOCK)))
    once, repeat = words[2], words[3]
    pairs = words[4:4 + 2 * (once + repeat)]
    if len(pairs) != 2 * (once + repeat) or not pairs:
        raise ValueError("PRONTO burst length does not match the header")
    period_us = 1_000_000 / freq
    return [max(1, int(round(w * period_us))) for w in pairs], freq


class ShellyIREmitter(IREmitterEntity):
    """IR blaster on a Shelly device."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        super().__init__(
            entity_id(P_IR, device_config.identifier, desc["uid"]),
            desc["name"],
            [ir_emitter.Features.SEND_IR],
            {ir_emitter.Attributes.STATE: ir_emitter.States.UNKNOWN},
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        state = ir_emitter.States.ON if self._device.available else ir_emitter.States.UNAVAILABLE
        self.update({ir_emitter.Attributes.STATE: state})

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        params = params or {}
        if cmd_id == ir_emitter.Commands.STOP_IR:
            return StatusCodes.OK
        if cmd_id != ir_emitter.Commands.SEND_IR:
            return StatusCodes.NOT_IMPLEMENTED
        fmt = str(params.get("format", "PRONTO")).upper()
        if fmt != "PRONTO":
            return StatusCodes.BAD_REQUEST
        try:
            timings, freq = pronto_to_raw(str(params.get("code", "")))
        except ValueError as err:
            _LOG.warning("[%s] Invalid IR code: %s", entity.id, err)
            return StatusCodes.BAD_REQUEST
        repeats = max(0, int(params.get("repeat", 1) or 1) - 1)
        try:
            await self._device.rpc("IR.EmitRaw", {"timings": timings, "freq": freq, "repeats": repeats})
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] IR send error: %s", entity.id, err)
            return StatusCodes.SERVER_ERROR


def create_ir_emitters(device_config: ShellyConfig, device: ShellyDevice) -> list[IREmitterEntity]:
    return [ShellyIREmitter(device_config, device, d) for d in by_platform(device_config.descriptors, P_IR)]
