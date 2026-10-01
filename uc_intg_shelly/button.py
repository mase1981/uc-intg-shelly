"""Shelly button entities - reboot, firmware update, virtual buttons, calibrate, smoke mute."""

import logging
from typing import Any

from ucapi import StatusCodes, button
from ucapi_framework import ButtonEntity

from uc_intg_shelly.client import ShellyConnectionError
from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import P_BUTTON, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)


async def press_button(device: ShellyDevice, desc: dict) -> None:
    """Execute a button descriptor."""
    try:
        if desc["kind"] == "rpc":
            await device.rpc(desc["method"], desc.get("params") or None)
        else:
            await device.gen1(desc["endpoint"], desc.get("params") or None)
    except ShellyConnectionError:
        # Reboot / update drop the connection before answering - that is success.
        if desc["uid"] not in ("reboot", "firmware_update_btn"):
            raise


class ShellyButton(ButtonEntity):
    """One push button."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        super().__init__(
            entity_id(P_BUTTON, device_config.identifier, desc["uid"]),
            desc["name"],
            icon="uc:power-on" if desc["uid"] == "reboot" else None,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        state = button.States.AVAILABLE if self._device.available else button.States.UNAVAILABLE
        self.update({button.Attributes.STATE: state})

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        if cmd_id != button.Commands.PUSH:
            return StatusCodes.NOT_IMPLEMENTED
        try:
            await press_button(self._device, self._desc)
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Button error: %s", entity.id, err)
            return StatusCodes.SERVER_ERROR


def create_buttons(device_config: ShellyConfig, device: ShellyDevice) -> list[ButtonEntity]:
    return [ShellyButton(device_config, device, d) for d in by_platform(device_config.descriptors, P_BUTTON)]
