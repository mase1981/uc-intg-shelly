"""Shelly switch entities - relays, plugs, circuit breakers, virtual booleans and scripts."""

import logging
from typing import Any

from ucapi import StatusCodes, switch
from ucapi_framework import SwitchEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import P_SWITCH, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)


async def set_switch(device: ShellyDevice, desc: dict, on: bool | None) -> None:
    """Turn a switch descriptor on / off (``None`` = toggle)."""
    kind, cid = desc["kind"], desc.get("cid")
    if on is None:
        on = not bool(device.value(desc["path"]))
    if kind == "rpc_switch":
        await device.rpc("Switch.Set", {"id": cid, "on": on})
    elif kind == "rpc_boolean":
        await device.rpc("Boolean.Set", {"id": cid, "value": on})
    elif kind == "rpc_script":
        await device.rpc("Script.Start" if on else "Script.Stop", {"id": cid})
    elif kind == "rpc_cb":
        await device.rpc("CB.Set", {"id": cid, "output": on})
    elif kind == "gen1_relay":
        await device.gen1(f"relay/{cid}", {"turn": "on" if on else "off"})
    else:
        raise ValueError(f"Unknown switch kind {kind}")


class ShellySwitch(SwitchEntity):
    """One on/off output."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        super().__init__(
            entity_id(P_SWITCH, device_config.identifier, desc["uid"]),
            desc["name"],
            [switch.Features.ON_OFF, switch.Features.TOGGLE],
            {switch.Attributes.STATE: switch.States.UNKNOWN},
            device_class=switch.DeviceClasses.OUTLET if desc.get("dc") == "outlet" else switch.DeviceClasses.SWITCH,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({switch.Attributes.STATE: switch.States.UNAVAILABLE})
            return
        value = self._device.value(self._desc["path"])
        if value is None:
            self.update({switch.Attributes.STATE: switch.States.UNKNOWN})
            return
        self.update({switch.Attributes.STATE: switch.States.ON if value else switch.States.OFF})

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        try:
            if cmd_id == switch.Commands.ON:
                await set_switch(self._device, self._desc, True)
            elif cmd_id == switch.Commands.OFF:
                await set_switch(self._device, self._desc, False)
            elif cmd_id == switch.Commands.TOGGLE:
                await set_switch(self._device, self._desc, None)
            else:
                return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Switch command error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR


def create_switches(device_config: ShellyConfig, device: ShellyDevice) -> list[SwitchEntity]:
    return [ShellySwitch(device_config, device, d) for d in by_platform(device_config.descriptors, P_SWITCH)]
