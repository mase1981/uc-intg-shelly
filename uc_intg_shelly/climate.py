"""Shelly climate entities - RPC thermostat (Wall Display / Gen2+), BLU TRV and Gen1 TRV."""

import logging
from typing import Any

from ucapi import StatusCodes, climate
from ucapi_framework import ClimateEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import P_CLIMATE, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)


class ShellyClimate(ClimateEntity):
    """One Shelly thermostat / radiator valve."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        self._kind = desc["kind"]
        self._cooling = self._kind == "rpc_thermostat" and desc.get("type") == "cooling"
        self._last_target: float = 20.0
        features = [
            climate.Features.ON_OFF,
            climate.Features.CURRENT_TEMPERATURE,
            climate.Features.TARGET_TEMPERATURE,
            climate.Features.COOL if self._cooling else climate.Features.HEAT,
        ]
        options = {
            climate.Options.TEMPERATURE_UNIT: "CELSIUS",
            climate.Options.TARGET_TEMPERATURE_STEP: desc.get("step", 0.5),
            climate.Options.MIN_TEMPERATURE: desc.get("min", 5),
            climate.Options.MAX_TEMPERATURE: desc.get("max", 35),
        }
        super().__init__(
            entity_id(P_CLIMATE, device_config.identifier, desc["uid"]),
            desc["name"],
            features,
            {climate.Attributes.STATE: climate.States.UNKNOWN},
            options=options,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    def _status(self) -> dict[str, Any]:
        if self._kind == "gen1_trv":
            value = self._device.value(["thermostats", self._desc["cid"]])
        else:
            value = self._device.value([self._desc["key"]])
        return value if isinstance(value, dict) else {}

    def _target(self, st: dict[str, Any]) -> float | None:
        if self._kind == "gen1_trv":
            target = (st.get("target_t") or {}).get("value")
        else:
            target = st.get("target_C")
        return float(target) if isinstance(target, (int, float)) else None

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({climate.Attributes.STATE: climate.States.UNAVAILABLE})
            return
        st = self._status()
        if not st:
            self.update({climate.Attributes.STATE: climate.States.UNKNOWN})
            return
        target = self._target(st)
        if self._kind == "rpc_thermostat":
            on = bool(st.get("enable", True))
            current = st.get("current_C")
        elif self._kind == "blutrv":
            on = True
            current = st.get("current_C")
        else:
            on = target is not None and target > float(self._desc.get("min", 4))
            current = (st.get("tmp") or {}).get("value")
        if target is not None and on:
            self._last_target = target
        if not on:
            state = climate.States.OFF
        else:
            state = climate.States.COOL if self._cooling else climate.States.HEAT
        attrs: dict[str, Any] = {climate.Attributes.STATE: state}
        if isinstance(current, (int, float)):
            attrs[climate.Attributes.CURRENT_TEMPERATURE] = round(float(current), 1)
        if target is not None:
            attrs[climate.Attributes.TARGET_TEMPERATURE] = target
        self.update(attrs)

    async def _set_target(self, temperature: float) -> None:
        temperature = max(float(self._desc.get("min", 4)), min(float(self._desc.get("max", 35)), temperature))
        cid = self._desc["cid"]
        if self._kind == "rpc_thermostat":
            await self._device.rpc("Thermostat.SetConfig", {"config": {"id": cid, "target_C": temperature}})
        elif self._kind == "blutrv":
            await self._device.rpc(
                "BluTRV.Call",
                {"id": cid, "method": "Trv.SetTarget", "params": {"id": 0, "target_C": temperature}},
            )
        else:
            await self._device.gen1(f"thermostat/{cid}", {"target_t_enabled": 1, "target_t": temperature})

    async def _set_power(self, on: bool) -> None:
        cid = self._desc["cid"]
        if self._kind == "rpc_thermostat":
            await self._device.rpc("Thermostat.SetConfig", {"config": {"id": cid, "enable": on}})
        elif self._kind == "blutrv":
            if not on:
                await self._set_target(float(self._desc.get("min", 4)))
            else:
                await self._set_target(self._last_target)
        else:
            # Gen1 TRV has no off mode - off == minimum setpoint (as in Home Assistant).
            await self._set_target(self._last_target if on else float(self._desc.get("min", 4)))

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        params = params or {}
        try:
            if cmd_id == climate.Commands.ON:
                await self._set_power(True)
            elif cmd_id == climate.Commands.OFF:
                await self._set_power(False)
            elif cmd_id == climate.Commands.HVAC_MODE:
                mode = str(params.get("hvac_mode", "")).upper()
                if mode == "OFF":
                    await self._set_power(False)
                elif mode in ("HEAT", "COOL", "AUTO", "HEAT_COOL"):
                    await self._set_power(True)
                else:
                    return StatusCodes.BAD_REQUEST
            elif cmd_id == climate.Commands.TARGET_TEMPERATURE:
                await self._set_target(float(params.get("temperature", self._last_target)))
            else:
                return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Climate command error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR


def create_climates(device_config: ShellyConfig, device: ShellyDevice) -> list[ClimateEntity]:
    return [ShellyClimate(device_config, device, d) for d in by_platform(device_config.descriptors, P_CLIMATE)]
