"""Build entity descriptors from a Shelly device's info / config / status.

A descriptor is a small JSON-serialisable dict describing one Remote entity
(platform, unique sub-id, name, where its value lives in the status tree and how
to control it). Descriptors are computed during setup and cached in the config
entry, so entities can be rebuilt after a reboot without a live connection.

The component coverage mirrors the Home Assistant Shelly integration
(``homeassistant/components/shelly``): switches, lights, covers, climate,
power/energy meters, environmental sensors, inputs, virtual components,
scripts, BLU TRV, Wall Display media and the device-level diagnostics.

Value paths address the raw status tree: ``["switch:0", "aenergy", "total"]``
for Gen2+ and ``["meters", 0, "power"]`` for Gen1.
"""

from typing import Any

from uc_intg_shelly.const import (
    BLU_TRV_MAX,
    BLU_TRV_MIN,
    BLU_TRV_STEP,
    GEN1_DUAL_MODE_MODELS,
    GEN1_EFFECT_MODELS,
    GEN1_PLUG_MODELS,
    GEN1_RGBW_MODELS,
    GEN1_TRV_MAX,
    GEN1_TRV_MIN,
    GEN1_TRV_STEP,
    KELVIN_MAX,
    KELVIN_MIN_COLOR,
    KELVIN_MIN_WHITE,
    RPC_THERMOSTAT_MAX,
    RPC_THERMOSTAT_MIN,
    RPC_THERMOSTAT_STEP,
    SHBLB_1_RGB_EFFECTS,
    STANDARD_RGB_EFFECTS,
)

Path = list[str | int]

# Platforms (UC entity types).
P_SWITCH = "switch"
P_LIGHT = "light"
P_COVER = "cover"
P_CLIMATE = "climate"
P_SENSOR = "sensor"
P_BUTTON = "button"
P_SELECT = "select"
P_MEDIA = "media_player"
P_IR = "ir_emitter"

# Sensor device classes (ucapi sensor.DeviceClasses values).
DC_CUSTOM = "custom"
DC_BATTERY = "battery"
DC_CURRENT = "current"
DC_ENERGY = "energy"
DC_HUMIDITY = "humidity"
DC_POWER = "power"
DC_TEMPERATURE = "temperature"
DC_VOLTAGE = "voltage"
DC_BINARY = "binary"

WH_TO_KWH = 0.001
WMIN_TO_KWH = 1 / 60000

# Metering fields shared by switch / light / cct / rgb / rgbw / rgbcct / pm1 / cover.
_METER_FIELDS: list[tuple[Path, str, str, str, float | None, int | None]] = [
    (["apower"], "Power", DC_POWER, "W", None, 1),
    (["voltage"], "Voltage", DC_VOLTAGE, "V", None, 1),
    (["current"], "Current", DC_CURRENT, "A", None, 3),
    (["pf"], "Power Factor", DC_CUSTOM, "", None, 2),
    (["freq"], "Frequency", DC_CUSTOM, "Hz", None, 1),
    (["aenergy", "total"], "Energy", DC_ENERGY, "kWh", WH_TO_KWH, 3),
    (["ret_aenergy", "total"], "Returned Energy", DC_ENERGY, "kWh", WH_TO_KWH, 3),
    (["temperature", "tC"], "Temperature", DC_TEMPERATURE, "°C", None, 1),
]

_EM1_FIELDS: list[tuple[Path, str, str, str, float | None, int | None]] = [
    (["act_power"], "Power", DC_POWER, "W", None, 1),
    (["aprt_power"], "Apparent Power", DC_CUSTOM, "VA", None, 1),
    (["pf"], "Power Factor", DC_CUSTOM, "", None, 2),
    (["voltage"], "Voltage", DC_VOLTAGE, "V", None, 1),
    (["current"], "Current", DC_CURRENT, "A", None, 3),
    (["freq"], "Frequency", DC_CUSTOM, "Hz", None, 1),
]

_EM_TOTAL_FIELDS: list[tuple[Path, str, str, str, float | None, int | None]] = [
    (["total_act_power"], "Total Power", DC_POWER, "W", None, 1),
    (["total_aprt_power"], "Total Apparent Power", DC_CUSTOM, "VA", None, 1),
    (["total_current"], "Total Current", DC_CURRENT, "A", None, 3),
    (["n_current"], "Neutral Current", DC_CURRENT, "A", None, 3),
]

_EMDATA_FIELDS: list[tuple[Path, str]] = [
    (["total_act"], "Total Energy"),
    (["total_act_ret"], "Total Returned Energy"),
    (["a_total_act_energy"], "Phase A Energy"),
    (["b_total_act_energy"], "Phase B Energy"),
    (["c_total_act_energy"], "Phase C Energy"),
    (["a_total_act_ret_energy"], "Phase A Returned Energy"),
    (["b_total_act_ret_energy"], "Phase B Returned Energy"),
    (["c_total_act_ret_energy"], "Phase C Returned Energy"),
    (["total_act_energy"], "Energy"),
    (["total_act_ret_energy"], "Returned Energy"),
]

# BTHome object ids (BLU sensors via a Shelly gateway) -> (label, device class, unit).
_BTHOME_OBJECTS: dict[int, tuple[str, str, str]] = {
    0x01: ("Battery", DC_BATTERY, "%"),
    0x02: ("Temperature", DC_TEMPERATURE, "°C"),
    0x03: ("Humidity", DC_HUMIDITY, "%"),
    0x04: ("Pressure", DC_CUSTOM, "hPa"),
    0x05: ("Illuminance", DC_CUSTOM, "lx"),
    0x0C: ("Voltage", DC_VOLTAGE, "V"),
    0x12: ("CO2", DC_CUSTOM, "ppm"),
    0x1A: ("Door", DC_BINARY, "door"),
    0x21: ("Motion", DC_BINARY, "motion"),
    0x2D: ("Window", DC_BINARY, "window"),
    0x2E: ("Humidity", DC_HUMIDITY, "%"),
    0x3A: ("Button", DC_CUSTOM, ""),
    0x3F: ("Rotation", DC_CUSTOM, "°"),
    0x45: ("Temperature", DC_TEMPERATURE, "°C"),
}

GEN1_INPUT_EVENTS = {
    "S": "single_push",
    "SS": "double_push",
    "SSS": "triple_push",
    "L": "long_push",
    "SL": "short_long_push",
    "LS": "long_short_push",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def resolve(root: Any, path: Path) -> Any:
    """Walk ``path`` through nested dicts/lists. Returns None if any hop is missing."""
    node = root
    for hop in path:
        if isinstance(node, dict):
            node = node.get(hop) if not isinstance(hop, int) else node.get(hop, node.get(str(hop)))
        elif isinstance(node, list) and isinstance(hop, int):
            node = node[hop] if 0 <= hop < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


def _uid(*parts: Any) -> str:
    return "_".join(str(p).replace(":", "_").replace(".", "_").replace(" ", "_") for p in parts).lower()


def _split_key(key: str) -> tuple[str, int | None]:
    if ":" in key:
        comp, _, cid = key.partition(":")
        try:
            return comp, int(cid)
        except ValueError:
            return comp, None
    return key, None


def _sensor(
    uid: str,
    name: str,
    path: Path,
    dc: str,
    unit: str = "",
    scale: float | None = None,
    dec: int | None = None,
    fmt: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    desc: dict[str, Any] = {"p": P_SENSOR, "uid": uid, "name": name, "path": path, "dc": dc, "unit": unit}
    if scale is not None:
        desc["scale"] = scale
    if dec is not None:
        desc["dec"] = dec
    if fmt:
        desc["fmt"] = fmt
    desc.update(extra)
    return desc


def _binary(uid: str, name: str, path: Path, unit: str = "", **match: Any) -> dict[str, Any]:
    """Binary sensor. ``match`` may hold ``contains`` / ``equals`` / ``one_of``; default is truthiness."""
    return {"p": P_SENSOR, "uid": uid, "name": name, "path": path, "dc": DC_BINARY, "unit": unit, "bin": match}


def _meter_sensors(
    root: dict[str, Any], key: str, base: str, fields: list[tuple[Path, str, str, str, float | None, int | None]]
) -> list[dict[str, Any]]:
    out = []
    for sub, label, dc, unit, scale, dec in fields:
        if resolve(root, [key, *sub]) is not None:
            out.append(_sensor(_uid(key, *sub), f"{base} {label}", [key, *sub], dc, unit, scale, dec))
    return out


# ---------------------------------------------------------------------------
# Gen2 / Gen3 / Gen4 (RPC)
# ---------------------------------------------------------------------------
def build_rpc_descriptors(
    device_name: str,
    model: str,
    config: dict[str, Any],
    status: dict[str, Any],
    methods: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the descriptor list for a Gen2+ device."""
    out: list[dict[str, Any]] = []
    methods = methods or []
    keys = sorted(status.keys(), key=lambda k: (_split_key(k)[0], _split_key(k)[1] or 0))
    counts: dict[str, int] = {}
    for key in keys:
        comp, _ = _split_key(key)
        counts[comp] = counts.get(comp, 0) + 1

    sys_cfg = config.get("sys", {}) or {}
    con_types = (sys_cfg.get("ui_data", {}) or {}).get("consumption_types") or []
    relay_in_thermostat = bool((status.get("sys", {}) or {}).get("relay_in_thermostat", False))
    is_plug = "PL" in (model or "").upper().split("-")[0]

    def cfg(key: str) -> dict[str, Any]:
        value = config.get(key)
        return value if isinstance(value, dict) else {}

    def channel_name(key: str, comp: str, cid: int, label: str) -> str:
        custom = cfg(key).get("name")
        if custom:
            return f"{device_name} {custom}"
        if counts.get(comp, 0) <= 1:
            return device_name if label == "" else f"{device_name} {label}"
        return f"{device_name} {label or comp.title()} {cid + 1}".strip()

    def virtual_name(key: str, comp: str, cid: int) -> str:
        custom = cfg(key).get("name")
        return f"{device_name} {custom}" if custom else f"{device_name} {comp.title()} {cid}"

    def view(key: str) -> str:
        return str(((cfg(key).get("meta") or {}).get("ui") or {}).get("view", ""))

    for key in keys:
        comp, cid = _split_key(key)
        st = status.get(key)
        if not isinstance(st, dict):
            continue

        if comp == "switch" and cid is not None:
            is_light = cid < len(con_types) and str(con_types[cid]).lower().startswith("light")
            base = channel_name(key, comp, cid, "")
            if is_light:
                out.append({"p": P_LIGHT, "uid": _uid(key), "name": base, "kind": "rpc", "comp": "Switch",
                            "key": key, "cid": cid, "dim": False, "color": False, "ct": False, "white": False})
            elif not relay_in_thermostat:
                out.append({"p": P_SWITCH, "uid": _uid(key), "name": base, "kind": "rpc_switch", "cid": cid,
                            "path": [key, "output"], "dc": "outlet" if is_plug else "switch"})
            out.extend(_meter_sensors(status, key, base, _METER_FIELDS))
            if "apower" in st or "temperature" in st:
                out.append(_binary(_uid(key, "overtemp"), f"{base} Overheating", [key, "errors"], "problem",
                                   contains="overtemp"))
            if "apower" in st:
                out.append(_binary(_uid(key, "overpower"), f"{base} Overpowering", [key, "errors"], "problem",
                                   contains="overpower"))
            if "voltage" in st:
                out.append(_binary(_uid(key, "overvoltage"), f"{base} Overvoltage", [key, "errors"], "problem",
                                   contains="overvoltage"))
            if "current" in st:
                out.append(_binary(_uid(key, "overcurrent"), f"{base} Overcurrent", [key, "errors"], "problem",
                                   contains="overcurrent"))

        elif comp == "cover" and cid is not None:
            base = channel_name(key, comp, cid, "Cover" if counts.get(comp, 0) > 1 else "")
            slat = bool((cfg(key).get("slat") or {}).get("enable"))
            out.append({"p": P_COVER, "uid": _uid(key), "name": base, "kind": "rpc", "cid": cid,
                        "pos": bool(st.get("pos_control")), "tilt": slat})
            out.extend(_meter_sensors(status, key, base, _METER_FIELDS))
            out.append({"p": P_BUTTON, "uid": _uid(key, "calibrate"), "name": f"{base} Calibrate",
                        "kind": "rpc", "method": "Cover.Calibrate", "params": {"id": cid}})

        elif comp in ("light", "cct", "rgb", "rgbw", "rgbcct") and cid is not None:
            base = channel_name(key, comp, cid, "Light" if counts.get(comp, 0) > 1 else "")
            ct_range = cfg(key).get("ct_range") or [KELVIN_MIN_WHITE, KELVIN_MAX]
            desc = {"p": P_LIGHT, "uid": _uid(key), "name": base, "kind": "rpc",
                    "comp": {"light": "Light", "cct": "CCT", "rgb": "RGB", "rgbw": "RGBW", "rgbcct": "RGBCCT"}[comp],
                    "key": key, "cid": cid, "dim": True,
                    "color": comp in ("rgb", "rgbw", "rgbcct"),
                    "ct": comp in ("cct", "rgbcct"),
                    "white": comp == "rgbw",
                    "ct_min": int(ct_range[0]), "ct_max": int(ct_range[1])}
            out.append(desc)
            out.extend(_meter_sensors(status, key, base, _METER_FIELDS))

        elif comp == "pm1" and cid is not None:
            out.extend(_meter_sensors(status, key, channel_name(key, comp, cid, "Meter"), _METER_FIELDS))

        elif comp == "em1" and cid is not None:
            out.extend(_meter_sensors(status, key, channel_name(key, comp, cid, "Meter"), _EM1_FIELDS))

        elif comp == "em" and cid is not None:
            base = f"{device_name}" if counts.get(comp, 0) <= 1 else f"{device_name} Meter {cid + 1}"
            for phase in ("a", "b", "c"):
                fields = [([f"{phase}_{f[0][0]}"], f"Phase {phase.upper()} {f[1]}", *f[2:]) for f in _EM1_FIELDS]
                out.extend(_meter_sensors(status, key, base, fields))
            out.extend(_meter_sensors(status, key, base, _EM_TOTAL_FIELDS))

        elif comp in ("emdata", "em1data") and cid is not None:
            base = device_name if counts.get(comp, 0) <= 1 else f"{device_name} Meter {cid + 1}"
            for sub, label in _EMDATA_FIELDS:
                if resolve(status, [key, *sub]) is not None:
                    out.append(_sensor(_uid(key, *sub), f"{base} {label}", [key, *sub], DC_ENERGY, "kWh",
                                       WH_TO_KWH, 3))

        elif comp == "temperature" and cid is not None:
            name = channel_name(key, comp, cid, "Temperature")
            out.append(_sensor(_uid(key), name, [key, "tC"], DC_TEMPERATURE, "°C", None, 1))

        elif comp == "humidity" and cid is not None:
            name = channel_name(key, comp, cid, "Humidity")
            out.append(_sensor(_uid(key), name, [key, "rh"], DC_HUMIDITY, "%", None, 0))

        elif comp == "illuminance" and cid is not None:
            name = channel_name(key, comp, cid, "Illuminance")
            out.append(_sensor(_uid(key), name, [key, "lux"], DC_CUSTOM, "lx", None, 0))
            if "illumination" in st:
                out.append(_sensor(_uid(key, "illumination"), f"{name} Level", [key, "illumination"], DC_CUSTOM,
                                   "", fmt="title"))

        elif comp == "devicepower" and cid is not None:
            if isinstance(st.get("battery"), dict):
                out.append(_sensor(_uid(key, "battery"), f"{device_name} Battery", [key, "battery", "percent"],
                                   DC_BATTERY, "%", None, 0))
            if isinstance(st.get("external"), dict):
                out.append(_binary(_uid(key, "external"), f"{device_name} External Power",
                                   [key, "external", "present"], "plug"))

        elif comp == "voltmeter" and cid is not None:
            name = channel_name(key, comp, cid, "Voltmeter")
            out.append(_sensor(_uid(key), name, [key, "voltage"], DC_VOLTAGE, "V", None, 2))
            if "xvoltage" in st:
                unit = str(((cfg(key).get("xvoltage") or {}).get("unit")) or "")
                out.append(_sensor(_uid(key, "xvoltage"), f"{name} Value", [key, "xvoltage"], DC_CUSTOM, unit,
                                   None, 2))

        elif comp == "input" and cid is not None:
            icfg = cfg(key)
            if icfg.get("enable") is False:
                continue
            itype = str(icfg.get("type", "switch"))
            name = virtual_name(key, comp, cid)
            if itype == "switch":
                out.append(_binary(_uid(key), name, [key, "state"]))
            elif itype == "button":
                out.append(_sensor(_uid(key, "event"), f"{name} Last Event", ["_events", key], DC_CUSTOM, "",
                                   fmt="event"))
            elif itype == "analog":
                out.append(_sensor(_uid(key), name, [key, "percent"], DC_CUSTOM, "%", None, 1))
                if "xpercent" in st:
                    unit = str(((icfg.get("xpercent") or {}).get("unit")) or "")
                    out.append(_sensor(_uid(key, "xpercent"), f"{name} Value", [key, "xpercent"], DC_CUSTOM, unit,
                                       None, 2))
            elif itype == "count":
                out.append(_sensor(_uid(key, "counts"), f"{name} Pulse Count", [key, "counts", "total"], DC_CUSTOM,
                                   "", None, 0))
                if resolve(status, [key, "counts", "xtotal"]) is not None:
                    unit = str(((icfg.get("xcounts") or {}).get("unit")) or "")
                    out.append(_sensor(_uid(key, "xcounts"), f"{name} Counter Value", [key, "counts", "xtotal"],
                                       DC_CUSTOM, unit, None, 2))
                if "freq" in st:
                    out.append(_sensor(_uid(key, "freq"), f"{name} Frequency", [key, "freq"], DC_CUSTOM, "Hz",
                                       None, 1))
                if "xfreq" in st:
                    unit = str(((icfg.get("xfreq") or {}).get("unit")) or "")
                    out.append(_sensor(_uid(key, "xfreq"), f"{name} Frequency Value", [key, "xfreq"], DC_CUSTOM,
                                       unit, None, 2))

        elif comp == "smoke" and cid is not None:
            name = channel_name(key, comp, cid, "Smoke")
            out.append(_binary(_uid(key), name, [key, "alarm"], "smoke"))
            out.append(_binary(_uid(key, "mute"), f"{name} Muted", [key, "mute"]))
            out.append({"p": P_BUTTON, "uid": _uid(key, "mute_btn"), "name": f"{name} Mute Alarm",
                        "kind": "rpc", "method": "Smoke.Mute", "params": {"id": cid}})

        elif comp == "flood" and cid is not None:
            name = channel_name(key, comp, cid, "Flood")
            out.append(_binary(_uid(key), name, [key, "alarm"], "moisture"))
            out.append(_binary(_uid(key, "mute"), f"{name} Muted", [key, "mute"]))
            out.append(_binary(_uid(key, "cable"), f"{name} Cable Unplugged", [key, "errors"], "problem",
                               contains="cable_unplugged"))

        elif comp == "presence" and cid is not None:
            name = channel_name(key, comp, cid, "Presence")
            out.append(_binary(_uid(key), name, [key, "num_objects"], "occupancy"))
            out.append(_sensor(_uid(key, "objects"), f"{name} Detected Objects", [key, "num_objects"], DC_CUSTOM,
                               "", None, 0))

        elif comp == "presencezone" and cid is not None:
            name = virtual_name(key, "Zone", cid)
            out.append(_binary(_uid(key), name, [key, "value"], "occupancy"))
            if "num_objects" in st:
                out.append(_sensor(_uid(key, "objects"), f"{name} Detected Objects", [key, "num_objects"],
                                   DC_CUSTOM, "", None, 0))

        elif comp == "motion" and cid is not None:
            if cfg(key).get("enable", True) is False:
                continue
            out.append(_binary(_uid(key), channel_name(key, comp, cid, "Motion"), [key, "motion"], "motion"))

        elif comp == "cb" and cid is not None:
            name = channel_name(key, comp, cid, "Circuit Breaker")
            out.append({"p": P_SWITCH, "uid": _uid(key), "name": name, "kind": "rpc_cb", "cid": cid,
                        "path": [key, "output"], "dc": "switch"})
            out.append(_binary(_uid(key, "safety"), f"{name} Safety", [key, "safety"], "safety"))

        elif comp == "boolean" and cid is not None:
            name = virtual_name(key, comp, cid)
            if view(key) == "toggle":
                out.append({"p": P_SWITCH, "uid": _uid(key), "name": name, "kind": "rpc_boolean", "cid": cid,
                            "path": [key, "value"], "dc": "switch"})
            else:
                out.append(_binary(_uid(key), name, [key, "value"]))

        elif comp == "button" and cid is not None:
            out.append({"p": P_BUTTON, "uid": _uid(key), "name": virtual_name(key, comp, cid), "kind": "rpc",
                        "method": "Button.Trigger", "params": {"id": cid, "event": "single_push"}})

        elif comp == "enum" and cid is not None:
            name = virtual_name(key, comp, cid)
            options = [str(o) for o in (cfg(key).get("options") or [])]
            titles = ((cfg(key).get("meta") or {}).get("ui") or {}).get("titles") or {}
            titles = {str(k): str(v) for k, v in titles.items() if v}
            if view(key) == "dropdown" and options:
                out.append({"p": P_SELECT, "uid": _uid(key), "name": name, "kind": "rpc_enum", "cid": cid,
                            "path": [key, "value"], "options": options, "titles": titles})
            else:
                out.append(_sensor(_uid(key), name, [key, "value"], DC_CUSTOM, "", fmt="enum", titles=titles))

        elif comp in ("number", "text") and cid is not None:
            name = virtual_name(key, comp, cid)
            unit = str(((cfg(key).get("meta") or {}).get("ui") or {}).get("unit") or "")
            out.append(_sensor(_uid(key), name, [key, "value"], DC_CUSTOM, unit,
                               fmt=None if comp == "number" else "text"))

        elif comp == "script" and cid is not None:
            sname = cfg(key).get("name") or f"Script {cid}"
            out.append({"p": P_SWITCH, "uid": _uid(key), "name": f"{device_name} {sname}", "kind": "rpc_script",
                        "cid": cid, "path": [key, "running"], "dc": "switch"})

        elif comp == "thermostat" and cid is not None:
            ttype = str(cfg(key).get("type", "heating"))
            hum_key = f"humidity:{cid}" if f"humidity:{cid}" in status else ""
            out.append({"p": P_CLIMATE, "uid": _uid(key), "name": channel_name(key, comp, cid, "Thermostat"),
                        "kind": "rpc_thermostat", "cid": cid, "key": key, "type": ttype,
                        "invert": bool(cfg(key).get("invert_output", False)), "humidity_key": hum_key,
                        "min": RPC_THERMOSTAT_MIN, "max": RPC_THERMOSTAT_MAX, "step": RPC_THERMOSTAT_STEP})

        elif comp == "blutrv" and cid is not None:
            tname = cfg(key).get("name") or f"BLU TRV {cid}"
            name = f"{device_name} {tname}"
            out.append({"p": P_CLIMATE, "uid": _uid(key), "name": name, "kind": "blutrv", "cid": cid, "key": key,
                        "min": BLU_TRV_MIN, "max": BLU_TRV_MAX, "step": BLU_TRV_STEP})
            if "pos" in st:
                out.append(_sensor(_uid(key, "pos"), f"{name} Valve Position", [key, "pos"], DC_CUSTOM, "%",
                                   None, 0))
            if "battery" in st:
                out.append(_sensor(_uid(key, "battery"), f"{name} Battery", [key, "battery"], DC_BATTERY, "%",
                                   None, 0))
            if "rssi" in st:
                out.append(_sensor(_uid(key, "rssi"), f"{name} Signal", [key, "rssi"], DC_CUSTOM, "dBm", None, 0))
            out.append({"p": P_BUTTON, "uid": _uid(key, "calibrate"), "name": f"{name} Calibrate", "kind": "rpc",
                        "method": "BluTRV.Call",
                        "params": {"id": cid, "method": "Trv.Calibrate", "params": {"id": 0}}})

        elif comp == "bthomedevice" and cid is not None:
            bname = cfg(key).get("name") or f"BLU Device {cid}"
            if "battery" in st:
                out.append(_sensor(_uid(key, "battery"), f"{device_name} {bname} Battery", [key, "battery"],
                                   DC_BATTERY, "%", None, 0))
            if "rssi" in st:
                out.append(_sensor(_uid(key, "rssi"), f"{device_name} {bname} Signal", [key, "rssi"], DC_CUSTOM,
                                   "dBm", None, 0))

        elif comp == "bthomesensor" and cid is not None:
            obj_id = cfg(key).get("obj_id")
            label, dc, unit = _BTHOME_OBJECTS.get(int(obj_id) if isinstance(obj_id, int) else -1,
                                                  ("Sensor", DC_CUSTOM, ""))
            name = cfg(key).get("name") or f"BLU {label} {cid}"
            if dc == DC_BINARY:
                out.append(_binary(_uid(key), f"{device_name} {name}", [key, "value"], unit))
            else:
                out.append(_sensor(_uid(key), f"{device_name} {name}", [key, "value"], dc, unit, None,
                                   None if dc == DC_CUSTOM and not unit else 1))

        elif comp == "media" and cid is None:
            out.append({"p": P_MEDIA, "uid": "media", "name": f"{device_name} Media", "kind": "rpc"})

        elif comp == "wifi" and cid is None:
            if st.get("rssi") is not None:
                out.append(_sensor("wifi_rssi", f"{device_name} WiFi Signal", ["wifi", "rssi"], DC_CUSTOM, "dBm",
                                   None, 0))

        elif comp == "cloud" and cid is None:
            out.append(_binary("cloud", f"{device_name} Cloud", ["cloud", "connected"], "connectivity"))

        elif comp == "sys" and cid is None:
            if "uptime" in st:
                out.append(_sensor("uptime", f"{device_name} Uptime", ["sys", "uptime"], DC_CUSTOM, "",
                                   fmt="duration"))
            if "restart_required" in st:
                out.append(_binary("restart_required", f"{device_name} Restart Required",
                                   ["sys", "restart_required"], "problem"))
            out.append(_binary("firmware_update", f"{device_name} Firmware Update",
                               ["sys", "available_updates", "stable"], "update"))

    # Device-level buttons.
    out.append({"p": P_BUTTON, "uid": "reboot", "name": f"{device_name} Reboot", "kind": "rpc",
                "method": "Shelly.Reboot", "params": {}})
    out.append({"p": P_BUTTON, "uid": "firmware_update_btn", "name": f"{device_name} Update Firmware",
                "kind": "rpc", "method": "Shelly.Update", "params": {"stage": "stable"}})

    if "IR.EmitRaw" in methods:
        out.append({"p": P_IR, "uid": "ir", "name": f"{device_name} IR"})

    return out


# ---------------------------------------------------------------------------
# Gen1 (REST)
# ---------------------------------------------------------------------------
def build_gen1_descriptors(
    device_name: str,
    model: str,
    settings: dict[str, Any],
    status: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the descriptor list for a Gen1 device."""
    out: list[dict[str, Any]] = []
    mode = str(settings.get("mode", "") or "")
    relays = status.get("relays") if isinstance(status.get("relays"), list) else []
    rollers = status.get("rollers") if isinstance(status.get("rollers"), list) else []
    lights = status.get("lights") if isinstance(status.get("lights"), list) else []
    s_relays = settings.get("relays") if isinstance(settings.get("relays"), list) else []
    s_rollers = settings.get("rollers") if isinstance(settings.get("rollers"), list) else []
    s_lights = settings.get("lights") if isinstance(settings.get("lights"), list) else []

    def named(items: list, idx: int, count: int, label: str) -> str:
        custom = items[idx].get("name") if idx < len(items) and isinstance(items[idx], dict) else None
        if custom:
            return f"{device_name} {custom}"
        return device_name if count <= 1 else f"{device_name} {label} {idx + 1}"

    channel_names: list[str] = []

    if mode != "roller":
        for idx, relay in enumerate(relays):
            if not isinstance(relay, dict):
                continue
            name = named(s_relays, idx, len(relays), "Channel")
            channel_names.append(name)
            appliance = str((s_relays[idx] if idx < len(s_relays) else {}).get("appliance_type", "") or "")
            if appliance.lower().startswith("light"):
                out.append({"p": P_LIGHT, "uid": f"relay_{idx}", "name": name, "kind": "gen1",
                            "endpoint": f"relay/{idx}", "path": ["relays", idx], "dim": False, "color": False,
                            "ct": False, "white": False})
            else:
                out.append({"p": P_SWITCH, "uid": f"relay_{idx}", "name": name, "kind": "gen1_relay", "cid": idx,
                            "path": ["relays", idx, "ison"], "dc": "outlet" if model in GEN1_PLUG_MODELS else "switch"})
            if "overpower" in relay:
                out.append(_binary(f"relay_{idx}_overpower", f"{name} Overpowering", ["relays", idx, "overpower"],
                                   "problem"))
    else:
        for idx, roller in enumerate(rollers):
            if not isinstance(roller, dict):
                continue
            name = named(s_rollers, idx, len(rollers), "Cover")
            channel_names.append(name)
            positioning = bool((s_rollers[idx] if idx < len(s_rollers) else {}).get("positioning", False))
            out.append({"p": P_COVER, "uid": f"roller_{idx}", "name": name, "kind": "gen1", "cid": idx,
                        "pos": positioning, "tilt": False})
            if roller.get("power") is not None:
                out.append(_sensor(f"roller_{idx}_power", f"{name} Power", ["rollers", idx, "power"], DC_POWER,
                                   "W", None, 1))

    for idx, lamp in enumerate(lights):
        if not isinstance(lamp, dict):
            continue
        name = named(s_lights, idx, len(lights), "Light")
        if not relays:
            channel_names.append(name)
        color = "red" in lamp and "green" in lamp and "blue" in lamp
        if model == "SHRGBW2":
            endpoint = f"{mode or 'color'}/{idx}"
            color = mode == "color"
        else:
            endpoint = f"light/{idx}"
        ct = "temp" in lamp
        desc = {"p": P_LIGHT, "uid": f"light_{idx}", "name": name, "kind": "gen1", "endpoint": endpoint,
                "path": ["lights", idx], "dim": "brightness" in lamp or "gain" in lamp, "color": color, "ct": ct,
                "white": color and model in GEN1_RGBW_MODELS, "dual": model in GEN1_DUAL_MODE_MODELS,
                "ct_min": KELVIN_MIN_COLOR if color else KELVIN_MIN_WHITE, "ct_max": KELVIN_MAX}
        out.append(desc)
        if model in GEN1_EFFECT_MODELS and "effect" in lamp and color:
            effects = SHBLB_1_RGB_EFFECTS if model == "SHBLB-1" else STANDARD_RGB_EFFECTS
            out.append({"p": P_SELECT, "uid": f"light_{idx}_effect", "name": f"{name} Effect", "kind": "gen1_effect",
                        "endpoint": endpoint, "path": ["lights", idx, "effect"],
                        "options": list(effects.values()), "titles": {str(k): v for k, v in effects.items()}})
        if lamp.get("power") is not None:
            out.append(_sensor(f"light_{idx}_power", f"{name} Power", ["lights", idx, "power"], DC_POWER, "W", None,
                               1))

    for idx, meter in enumerate(status.get("meters") or []):
        if not isinstance(meter, dict):
            continue
        base = channel_names[idx] if idx < len(channel_names) else (
            device_name if len(status.get("meters") or []) <= 1 else f"{device_name} Channel {idx + 1}")
        if meter.get("power") is not None:
            out.append(_sensor(f"meter_{idx}_power", f"{base} Power", ["meters", idx, "power"], DC_POWER, "W",
                               None, 1))
        if meter.get("total") is not None:
            out.append(_sensor(f"meter_{idx}_energy", f"{base} Energy", ["meters", idx, "total"], DC_ENERGY, "kWh",
                               WMIN_TO_KWH, 3))

    emeters = status.get("emeters") or []
    for idx, emeter in enumerate(emeters):
        if not isinstance(emeter, dict):
            continue
        base = device_name if len(emeters) <= 1 else f"{device_name} Phase {idx + 1}"
        for field, label, dc, unit, scale, dec in (
            ("power", "Power", DC_POWER, "W", None, 1),
            ("reactive", "Reactive Power", DC_CUSTOM, "VAR", None, 1),
            ("pf", "Power Factor", DC_CUSTOM, "", None, 2),
            ("voltage", "Voltage", DC_VOLTAGE, "V", None, 1),
            ("current", "Current", DC_CURRENT, "A", None, 3),
            ("total", "Energy", DC_ENERGY, "kWh", WH_TO_KWH, 3),
            ("total_returned", "Returned Energy", DC_ENERGY, "kWh", WH_TO_KWH, 3),
        ):
            if emeter.get(field) is not None:
                out.append(_sensor(f"emeter_{idx}_{field}", f"{base} {label}", ["emeters", idx, field], dc, unit,
                                   scale, dec))

    inputs = status.get("inputs") or []
    for idx, inp in enumerate(inputs):
        if not isinstance(inp, dict):
            continue
        name = f"{device_name} Input {idx + 1}" if len(inputs) > 1 else f"{device_name} Input"
        out.append(_binary(f"input_{idx}", name, ["inputs", idx, "input"]))
        if "event" in inp:
            out.append(_sensor(f"input_{idx}_event", f"{name} Last Event", ["inputs", idx, "event"], DC_CUSTOM, "",
                               fmt="gen1_event"))

    # Environmental / safety sensors.
    if isinstance(status.get("tmp"), dict) and status["tmp"].get("tC") is not None:
        out.append(_sensor("tmp", f"{device_name} Temperature", ["tmp", "tC"], DC_TEMPERATURE, "°C", None, 1))
    if isinstance(status.get("hum"), dict) and status["hum"].get("value") is not None:
        out.append(_sensor("hum", f"{device_name} Humidity", ["hum", "value"], DC_HUMIDITY, "%", None, 0))
    if isinstance(status.get("lux"), dict) and status["lux"].get("value") is not None:
        out.append(_sensor("lux", f"{device_name} Illuminance", ["lux", "value"], DC_CUSTOM, "lx", None, 0))
    if isinstance(status.get("bat"), dict) and status["bat"].get("value") is not None:
        out.append(_sensor("battery", f"{device_name} Battery", ["bat", "value"], DC_BATTERY, "%", None, 0))
    if "flood" in status:
        out.append(_binary("flood", f"{device_name} Flood", ["flood"], "moisture"))
    if "smoke" in status:
        out.append(_binary("smoke", f"{device_name} Smoke", ["smoke"], "smoke"))
    sensor_blk = status.get("sensor") if isinstance(status.get("sensor"), dict) else {}
    if "state" in sensor_blk:
        out.append(_binary("door_window", f"{device_name} Door/Window", ["sensor", "state"], "opening",
                           equals="open"))
    if "motion" in sensor_blk:
        out.append(_binary("motion", f"{device_name} Motion", ["sensor", "motion"], "motion"))
    if "vibration" in sensor_blk:
        out.append(_binary("vibration", f"{device_name} Vibration", ["sensor", "vibration"], "vibration"))
    accel = status.get("accel") if isinstance(status.get("accel"), dict) else {}
    if "tilt" in accel:
        out.append(_sensor("tilt", f"{device_name} Tilt", ["accel", "tilt"], DC_CUSTOM, "°", None, 0))
    if "vibration" in accel:
        out.append(_binary("accel_vibration", f"{device_name} Vibration", ["accel", "vibration"], "vibration"))
    gas = status.get("gas_sensor") if isinstance(status.get("gas_sensor"), dict) else {}
    if "alarm_state" in gas:
        out.append(_binary("gas", f"{device_name} Gas", ["gas_sensor", "alarm_state"], "gas",
                           one_of=["mild", "heavy"]))
        out.append(_sensor("gas_state", f"{device_name} Gas Alarm State", ["gas_sensor", "alarm_state"], DC_CUSTOM,
                           "", fmt="title"))
    if isinstance(status.get("concentration"), dict) and "ppm" in status["concentration"]:
        out.append(_sensor("gas_ppm", f"{device_name} Gas Concentration", ["concentration", "ppm"], DC_CUSTOM,
                           "ppm", None, 0))
    for sid, ext in sorted((status.get("ext_temperature") or {}).items()):
        if isinstance(ext, dict) and "tC" in ext:
            out.append(_sensor(f"ext_temp_{sid}", f"{device_name} External Temperature {int(sid) + 1}",
                               ["ext_temperature", str(sid), "tC"], DC_TEMPERATURE, "°C", None, 1))
    for sid, ext in sorted((status.get("ext_humidity") or {}).items()):
        if isinstance(ext, dict) and "hum" in ext:
            out.append(_sensor(f"ext_hum_{sid}", f"{device_name} External Humidity {int(sid) + 1}",
                               ["ext_humidity", str(sid), "hum"], DC_HUMIDITY, "%", None, 0))
    for sid, ext in sorted((status.get("ext_switch") or {}).items()):
        if isinstance(ext, dict) and "input" in ext:
            out.append(_binary(f"ext_switch_{sid}", f"{device_name} External Switch {int(sid) + 1}",
                               ["ext_switch", str(sid), "input"]))
    for idx, adc in enumerate(status.get("adcs") or []):
        if isinstance(adc, dict) and "voltage" in adc:
            out.append(_sensor(f"adc_{idx}", f"{device_name} ADC {idx + 1}", ["adcs", idx, "voltage"], DC_VOLTAGE,
                               "V", None, 2))
    if isinstance(status.get("temperature"), (int, float)):
        out.append(_sensor("device_temp", f"{device_name} Device Temperature", ["temperature"], DC_TEMPERATURE,
                           "°C", None, 1))
    if "overtemperature" in status:
        out.append(_binary("overtemperature", f"{device_name} Overheating", ["overtemperature"], "problem"))

    thermostats = status.get("thermostats") or []
    for idx, trv in enumerate(thermostats):
        if not isinstance(trv, dict):
            continue
        name = device_name if len(thermostats) <= 1 else f"{device_name} Thermostat {idx + 1}"
        out.append({"p": P_CLIMATE, "uid": f"trv_{idx}", "name": name, "kind": "gen1_trv", "cid": idx,
                    "min": GEN1_TRV_MIN, "max": GEN1_TRV_MAX, "step": GEN1_TRV_STEP})
        if "pos" in trv:
            out.append(_sensor(f"trv_{idx}_pos", f"{name} Valve Position", ["thermostats", idx, "pos"], DC_CUSTOM,
                               "%", None, 0))

    # Diagnostics.
    if isinstance(status.get("wifi_sta"), dict) and status["wifi_sta"].get("rssi") is not None:
        out.append(_sensor("wifi_rssi", f"{device_name} WiFi Signal", ["wifi_sta", "rssi"], DC_CUSTOM, "dBm", None,
                           0))
    if "uptime" in status:
        out.append(_sensor("uptime", f"{device_name} Uptime", ["uptime"], DC_CUSTOM, "", fmt="duration"))
    if isinstance(status.get("cloud"), dict):
        out.append(_binary("cloud", f"{device_name} Cloud", ["cloud", "connected"], "connectivity"))
    if isinstance(status.get("update"), dict):
        out.append(_binary("firmware_update", f"{device_name} Firmware Update", ["update", "has_update"], "update"))

    out.append({"p": P_BUTTON, "uid": "reboot", "name": f"{device_name} Reboot", "kind": "gen1", "endpoint": "reboot",
                "params": {}})
    out.append({"p": P_BUTTON, "uid": "firmware_update_btn", "name": f"{device_name} Update Firmware",
                "kind": "gen1", "endpoint": "ota", "params": {"update": "true"}})
    return out


def entity_id(platform: str, identifier: str, uid: str) -> str:
    """Return the Remote entity id ``<platform>.<device>.<uid>``."""
    return f"{platform}.{identifier}.{uid}"


def by_platform(descriptors: list[dict[str, Any]], platform: str) -> list[dict[str, Any]]:
    return [d for d in descriptors if d.get("p") == platform]
