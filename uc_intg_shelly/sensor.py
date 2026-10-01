"""Shelly sensor entities - metering, environmental, diagnostics and binary sensors.

Every sensor is driven by a descriptor (see :mod:`uc_intg_shelly.descriptors`):
the value is read from the cached status tree at ``path``, then scaled /
rounded / formatted. Binary sensors use the ``binary`` device class with the
Home Assistant binary device class (``door``, ``motion``, ``moisture`` ...) in
the ``unit`` attribute and ``on`` / ``off`` as value.
"""

import logging
from typing import Any

from ucapi import sensor
from ucapi_framework import SensorEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import DC_BINARY, DC_CUSTOM, GEN1_INPUT_EVENTS, P_SENSOR, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)

_DEVICE_CLASSES = {
    "custom": sensor.DeviceClasses.CUSTOM,
    "battery": sensor.DeviceClasses.BATTERY,
    "current": sensor.DeviceClasses.CURRENT,
    "energy": sensor.DeviceClasses.ENERGY,
    "humidity": sensor.DeviceClasses.HUMIDITY,
    "power": sensor.DeviceClasses.POWER,
    "temperature": sensor.DeviceClasses.TEMPERATURE,
    "voltage": sensor.DeviceClasses.VOLTAGE,
    "binary": sensor.DeviceClasses.BINARY,
}


def _duration(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "Unknown"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return f"{days}d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"


def _title(value: Any) -> str:
    return str(value).replace("_", " ").title() if value not in (None, "") else "Unknown"


def binary_value(desc: dict, value: Any) -> bool:
    """Evaluate a binary descriptor against a raw status value."""
    match = desc.get("bin") or {}
    if "contains" in match:
        return isinstance(value, (list, tuple)) and match["contains"] in value
    if "equals" in match:
        return value == match["equals"]
    if "one_of" in match:
        return value in match["one_of"]
    if isinstance(value, str):
        return value.lower() in ("on", "true", "1", "open")
    return bool(value)


class ShellySensor(SensorEntity):
    """One descriptor-driven sensor."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        dc = desc.get("dc", DC_CUSTOM)
        self._binary = dc == DC_BINARY
        self._numeric = not self._binary and desc.get("fmt") is None
        options: dict[str, Any] = {}
        attributes: dict[str, Any] = {sensor.Attributes.STATE: sensor.States.UNKNOWN}
        if self._binary:
            attributes[sensor.Attributes.VALUE] = "off"
            if desc.get("unit"):
                attributes[sensor.Attributes.UNIT] = desc["unit"]
        else:
            attributes[sensor.Attributes.VALUE] = 0 if self._numeric else ""
            if dc == DC_CUSTOM:
                options[sensor.Options.CUSTOM_UNIT] = desc.get("unit", "")
            if self._numeric and desc.get("dec") is not None:
                options[sensor.Options.DECIMALS] = int(desc["dec"])
        super().__init__(
            entity_id(P_SENSOR, device_config.identifier, desc["uid"]),
            desc["name"],
            [],
            attributes,
            device_class=_DEVICE_CLASSES.get(dc, sensor.DeviceClasses.CUSTOM),
            options=options or None,
        )
        self.subscribe_to_device(device)

    def _format(self, value: Any) -> Any:
        fmt = self._desc.get("fmt")
        if fmt == "duration":
            return _duration(value)
        if fmt == "event":
            return _title(value)
        if fmt == "gen1_event":
            return _title(GEN1_INPUT_EVENTS.get(str(value), value)) if value else "None"
        if fmt == "enum":
            titles = self._desc.get("titles") or {}
            return titles.get(str(value), str(value)) if value is not None else "Unknown"
        if fmt == "title":
            return _title(value)
        if fmt == "text":
            return str(value)[:255] if value is not None else ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)[:255]
        if self._desc.get("scale") is not None:
            number *= float(self._desc["scale"])
        dec = self._desc.get("dec")
        if dec is not None:
            number = round(number, int(dec))
            if int(dec) == 0:
                return int(number)
        return number

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({sensor.Attributes.STATE: sensor.States.UNAVAILABLE})
            return
        value = self._device.value(self._desc["path"])
        if self._binary:
            on = binary_value(self._desc, value)
            self.update({sensor.Attributes.STATE: sensor.States.ON, sensor.Attributes.VALUE: "on" if on else "off"})
            return
        if value is None and self._desc.get("fmt") != "event":
            self.update({sensor.Attributes.STATE: sensor.States.UNKNOWN})
            return
        if value is None:
            value = "None"
        self.update({sensor.Attributes.STATE: sensor.States.ON, sensor.Attributes.VALUE: self._format(value)})


def create_sensors(device_config: ShellyConfig, device: ShellyDevice) -> list[SensorEntity]:
    return [ShellySensor(device_config, device, d) for d in by_platform(device_config.descriptors, P_SENSOR)]
