"""Shelly light entities - dimmers, bulbs, RGB/RGBW/CCT controllers and relays set to 'light'.

The Remote uses HSV (hue 0..360, saturation 0..255, brightness 0..255) and a
colour-temperature percentage (0 = coldest, 100 = warmest). Shelly uses RGB,
brightness/gain in percent and colour temperature in Kelvin, so values are
converted both ways here.
"""

import colorsys
import logging
from typing import Any

from ucapi import StatusCodes, light
from ucapi_framework import LightEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.const import KELVIN_MAX, KELVIN_MIN_WHITE
from uc_intg_shelly.descriptors import P_LIGHT, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)


def _pct_to_255(pct: Any) -> int:
    try:
        return max(0, min(255, round(float(pct) * 255 / 100)))
    except (TypeError, ValueError):
        return 0


def _255_to_pct(value: Any) -> int:
    return max(1, min(100, round(float(value) * 100 / 255)))


def _kelvin_to_pct(kelvin: Any, k_min: int, k_max: int) -> int:
    try:
        k = float(kelvin)
    except (TypeError, ValueError):
        return 50
    if k_max <= k_min:
        return 50
    return max(0, min(100, round((k_max - k) * 100 / (k_max - k_min))))


def _pct_to_kelvin(pct: Any, k_min: int, k_max: int) -> int:
    return int(round(k_max - (max(0.0, min(100.0, float(pct))) / 100) * (k_max - k_min)))


def _rgb_to_hs(rgb: list | tuple) -> tuple[int, int]:
    r, g, b = (max(0, min(255, int(c))) / 255 for c in rgb[:3])
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return round(h * 360) % 360, round(s * 255)


def _hs_to_rgb(hue: Any, sat: Any) -> list[int]:
    r, g, b = colorsys.hsv_to_rgb((float(hue) % 360) / 360, max(0.0, min(255.0, float(sat))) / 255, 1.0)
    return [round(r * 255), round(g * 255), round(b * 255)]


class LightControl:
    """Status decoding and commands for one light descriptor (shared with the remote)."""

    def __init__(self, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        self.k_min = int(desc.get("ct_min", KELVIN_MIN_WHITE))
        self.k_max = int(desc.get("ct_max", KELVIN_MAX))

    def status(self) -> dict[str, Any]:
        if self._desc["kind"] == "rpc":
            value = self._device.value([self._desc["key"]])
        else:
            value = self._device.value(self._desc["path"])
        return value if isinstance(value, dict) else {}

    def is_on(self, st: dict[str, Any]) -> bool:
        return bool(st.get("output") if self._desc["kind"] == "rpc" else st.get("ison"))

    def gen1_color_mode(self, st: dict[str, Any]) -> bool:
        if not self._desc.get("color"):
            return False
        if self._desc.get("dual"):
            return st.get("mode") == "color"
        return True

    # -- commands -------------------------------------------------------
    async def turn_on(self, params: dict[str, Any] | None = None) -> None:
        params = params or {}
        desc = self._desc
        if desc["kind"] == "rpc":
            comp = desc["comp"]
            call: dict[str, Any] = {"id": desc["cid"], "on": True}
            if comp != "Switch":
                if "brightness" in params and desc.get("dim"):
                    call["brightness"] = _255_to_pct(params["brightness"])
                if desc.get("color") and ("hue" in params or "saturation" in params):
                    st = self.status()
                    hue, sat = _rgb_to_hs(st.get("rgb") or [255, 255, 255])
                    call["rgb"] = _hs_to_rgb(params.get("hue", hue), params.get("saturation", sat))
                    if comp == "RGBCCT":
                        call["mode"] = "rgb"
                elif desc.get("ct") and "color_temperature" in params:
                    call["ct"] = _pct_to_kelvin(params["color_temperature"], self.k_min, self.k_max)
                    if comp == "RGBCCT":
                        call["mode"] = "cct"
            await self._device.rpc(f"{comp}.Set", call)
            return

        query: dict[str, Any] = {"turn": "on"}
        st = self.status()
        color_mode = self.gen1_color_mode(st)
        if desc.get("color") and ("hue" in params or "saturation" in params):
            cur_h, cur_s = _rgb_to_hs([st.get("red", 255), st.get("green", 255), st.get("blue", 255)])
            query["red"], query["green"], query["blue"] = _hs_to_rgb(
                params.get("hue", cur_h), params.get("saturation", cur_s)
            )
            color_mode = True
            if desc.get("dual") and st.get("mode") != "color":
                query["mode"] = "color"
        elif desc.get("ct") and "color_temperature" in params:
            query["temp"] = _pct_to_kelvin(params["color_temperature"], self.k_min, self.k_max)
            color_mode = False
            if desc.get("dual") and st.get("mode") != "white":
                query["mode"] = "white"
        if "brightness" in params and desc.get("dim"):
            key = "gain" if color_mode and "gain" in st else "brightness"
            query[key] = _255_to_pct(params["brightness"])
        await self._device.gen1(desc["endpoint"], query)

    async def turn_off(self) -> None:
        desc = self._desc
        if desc["kind"] == "rpc":
            await self._device.rpc(f"{desc['comp']}.Set", {"id": desc["cid"], "on": False})
        else:
            await self._device.gen1(desc["endpoint"], {"turn": "off"})

    async def toggle(self) -> None:
        if self.is_on(self.status()):
            await self.turn_off()
        else:
            await self.turn_on()

    async def step_brightness(self, delta_pct: int) -> None:
        """Raise / lower brightness by ``delta_pct`` percent (used by the remote)."""
        st = self.status()
        if self._desc["kind"] == "rpc":
            current = st.get("brightness", 50)
        else:
            current = st.get("gain") if self.gen1_color_mode(st) and "gain" in st else st.get("brightness", 50)
        target = max(1, min(100, int(current or 0) + delta_pct))
        await self.turn_on({"brightness": round(target * 255 / 100)})


class ShellyLight(LightEntity):
    """One Shelly light channel."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        self._ctl = LightControl(device, desc)
        features = [light.Features.ON_OFF, light.Features.TOGGLE]
        if desc.get("dim"):
            features.append(light.Features.DIM)
        if desc.get("color"):
            features.append(light.Features.COLOR)
        if desc.get("ct"):
            features.append(light.Features.COLOR_TEMPERATURE)
        super().__init__(
            entity_id(P_LIGHT, device_config.identifier, desc["uid"]),
            desc["name"],
            features,
            {light.Attributes.STATE: light.States.UNKNOWN},
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({light.Attributes.STATE: light.States.UNAVAILABLE})
            return
        st = self._ctl.status()
        if not st:
            self.update({light.Attributes.STATE: light.States.UNKNOWN})
            return
        attrs: dict[str, Any] = {light.Attributes.STATE: light.States.ON if self._ctl.is_on(st) else light.States.OFF}
        desc = self._desc
        if desc["kind"] == "rpc":
            if desc.get("dim") and "brightness" in st:
                attrs[light.Attributes.BRIGHTNESS] = _pct_to_255(st["brightness"])
            if desc.get("color") and isinstance(st.get("rgb"), list) and len(st["rgb"]) >= 3:
                hue, sat = _rgb_to_hs(st["rgb"])
                attrs[light.Attributes.HUE] = hue
                attrs[light.Attributes.SATURATION] = sat
            if desc.get("ct") and st.get("ct") is not None:
                attrs[light.Attributes.COLOR_TEMPERATURE] = _kelvin_to_pct(st["ct"], self._ctl.k_min, self._ctl.k_max)
        else:
            color_mode = self._ctl.gen1_color_mode(st)
            if desc.get("dim"):
                level = st.get("gain") if color_mode and "gain" in st else st.get("brightness")
                if level is not None:
                    attrs[light.Attributes.BRIGHTNESS] = _pct_to_255(level)
            if desc.get("color") and all(c in st for c in ("red", "green", "blue")):
                hue, sat = _rgb_to_hs([st["red"], st["green"], st["blue"]])
                attrs[light.Attributes.HUE] = hue
                attrs[light.Attributes.SATURATION] = sat
            if desc.get("ct") and st.get("temp") is not None:
                attrs[light.Attributes.COLOR_TEMPERATURE] = _kelvin_to_pct(st["temp"], self._ctl.k_min, self._ctl.k_max)
        self.update(attrs)

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        try:
            if cmd_id == light.Commands.ON:
                await self._ctl.turn_on(params)
            elif cmd_id == light.Commands.OFF:
                await self._ctl.turn_off()
            elif cmd_id == light.Commands.TOGGLE:
                await self._ctl.toggle()
            else:
                return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Light command error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR


def create_lights(device_config: ShellyConfig, device: ShellyDevice) -> list[LightEntity]:
    return [ShellyLight(device_config, device, d) for d in by_platform(device_config.descriptors, P_LIGHT)]
