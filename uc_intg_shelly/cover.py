"""Shelly cover entities - roller shutters, blinds (with slat tilt) and garage doors."""

import asyncio
import logging
from typing import Any

from ucapi import StatusCodes, cover
from ucapi_framework import CoverEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import P_COVER, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)

_BACKGROUND: set[asyncio.Task] = set()

_RPC_STATES = {
    "open": cover.States.OPEN,
    "closed": cover.States.CLOSED,
    "opening": cover.States.OPENING,
    "closing": cover.States.CLOSING,
}


async def move_cover(device: ShellyDevice, desc: dict, action: str, position: int | None = None) -> None:
    """Run ``open`` / ``close`` / ``stop`` / ``position`` / ``tilt`` on a cover descriptor."""
    cid = desc["cid"]
    if desc["kind"] == "rpc":
        if action == "open":
            await device.rpc("Cover.Open", {"id": cid})
        elif action == "close":
            await device.rpc("Cover.Close", {"id": cid})
        elif action == "stop":
            await device.rpc("Cover.Stop", {"id": cid})
        elif action == "position":
            await device.rpc("Cover.GoToPosition", {"id": cid, "pos": int(position or 0)})
        elif action == "tilt":
            await device.rpc("Cover.GoToPosition", {"id": cid, "slat_pos": int(position or 0)})
        task = asyncio.create_task(device.after_command())
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        return
    if action in ("open", "close", "stop"):
        await device.gen1(f"roller/{cid}", {"go": action})
    elif action == "position":
        await device.gen1(f"roller/{cid}", {"go": "to_pos", "roller_pos": int(position or 0)})


class ShellyCover(CoverEntity):
    """One Shelly cover channel."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        features = [cover.Features.OPEN, cover.Features.CLOSE, cover.Features.STOP]
        if desc.get("pos"):
            features.append(cover.Features.POSITION)
        if desc.get("tilt"):
            features.extend([cover.Features.TILT, cover.Features.TILT_STOP, cover.Features.TILT_POSITION])
        super().__init__(
            entity_id(P_COVER, device_config.identifier, desc["uid"]),
            desc["name"],
            features,
            {cover.Attributes.STATE: cover.States.UNKNOWN},
            device_class=cover.DeviceClasses.BLIND,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    def _status(self) -> dict[str, Any]:
        cid = self._desc["cid"]
        path = [f"cover:{cid}"] if self._desc["kind"] == "rpc" else ["rollers", cid]
        value = self._device.value(path)
        return value if isinstance(value, dict) else {}

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({cover.Attributes.STATE: cover.States.UNAVAILABLE})
            return
        st = self._status()
        if not st:
            self.update({cover.Attributes.STATE: cover.States.UNKNOWN})
            return
        position = st.get("current_pos")
        attrs: dict[str, Any] = {}
        if self._desc["kind"] == "rpc":
            state = _RPC_STATES.get(str(st.get("state")))
            if state is None:  # "stopped" / "calibrating"
                if isinstance(position, (int, float)):
                    state = cover.States.CLOSED if position <= 0 else cover.States.OPEN
                else:
                    state = cover.States.CLOSED if st.get("last_direction") == "close" else cover.States.OPEN
            if self._desc.get("tilt") and st.get("slat_pos") is not None:
                attrs[cover.Attributes.TILT_POSITION] = int(st["slat_pos"])
        else:
            raw = st.get("state")
            if raw == "open":
                state = cover.States.OPENING
            elif raw == "close":
                state = cover.States.CLOSING
            elif self._desc.get("pos") and isinstance(position, (int, float)):
                state = cover.States.CLOSED if position <= 0 else cover.States.OPEN
            else:
                state = cover.States.CLOSED if st.get("last_direction") == "close" else cover.States.OPEN
        attrs[cover.Attributes.STATE] = state
        if self._desc.get("pos") and isinstance(position, (int, float)) and position >= 0:
            attrs[cover.Attributes.POSITION] = int(position)
        self.update(attrs)

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        params = params or {}
        try:
            match cmd_id:
                case cover.Commands.OPEN:
                    await move_cover(self._device, self._desc, "open")
                case cover.Commands.CLOSE:
                    await move_cover(self._device, self._desc, "close")
                case cover.Commands.STOP | cover.Commands.TILT_STOP:
                    await move_cover(self._device, self._desc, "stop")
                case cover.Commands.POSITION:
                    await move_cover(self._device, self._desc, "position", int(params.get("position", 0)))
                case cover.Commands.TILT:
                    await move_cover(self._device, self._desc, "tilt", int(params.get("tilt_position", 0)))
                case cover.Commands.TILT_UP:
                    await move_cover(self._device, self._desc, "tilt", 100)
                case cover.Commands.TILT_DOWN:
                    await move_cover(self._device, self._desc, "tilt", 0)
                case _:
                    return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Cover command error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR


def create_covers(device_config: ShellyConfig, device: ShellyDevice) -> list[CoverEntity]:
    return [ShellyCover(device_config, device, d) for d in by_platform(device_config.descriptors, P_COVER)]
