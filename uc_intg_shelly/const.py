"""Constants for the Shelly integration."""

STATE_ON = "ON"
STATE_UNAVAILABLE = "UNAVAILABLE"

# Poll intervals (seconds).
POLL_GEN1 = 5  # Gen1 has no push over HTTP - poll fast.
POLL_RPC_PUSH = 30  # Gen2+ with an active WebSocket (push) - safety-net poll.
POLL_RPC_NO_PUSH = 5  # Gen2+ while the WebSocket is down.
POLL_MOVING = 1  # While a cover is moving (position is not pushed while moving).

HTTP_TIMEOUT = 10
RPC_TIMEOUT = 10
WS_HEARTBEAT = 55

# mDNS service types.
MDNS_RPC = "_shelly._tcp.local."
MDNS_HTTP = "_http._tcp.local."

# Gen2+ username is fixed by the firmware.
RPC_USERNAME = "admin"

# Temperature limits (from the Home Assistant Shelly integration).
RPC_THERMOSTAT_MIN, RPC_THERMOSTAT_MAX, RPC_THERMOSTAT_STEP = 5, 35, 0.5
BLU_TRV_MIN, BLU_TRV_MAX, BLU_TRV_STEP = 4, 30, 0.1
GEN1_TRV_MIN, GEN1_TRV_MAX, GEN1_TRV_STEP = 4, 31, 0.5

# Colour temperature limits (Kelvin).
KELVIN_MAX = 6500
KELVIN_MIN_WHITE = 2700
KELVIN_MIN_COLOR = 3000

# Gen1 models (from aioshelly).
GEN1_RGBW_MODELS = ("SHBLB-1", "SHRGBW2")
GEN1_DUAL_MODE_MODELS = ("SHBLB-1", "SHCB-1")
GEN1_EFFECT_MODELS = ("SHBLB-1", "SHCB-1", "SHRGBW2")
GEN1_PLUG_MODELS = ("SHPLG-1", "SHPLG2-1", "SHPLG-S", "SHPLG-U1")
GEN1_DIMMER_MODELS = ("SHDM-1", "SHDM-2")
GEN1_COLOR_TEMP_MODELS = ("SHBDUO-1", "SHCB-1", "SHBLB-1")
GEN1_TRV_MODELS = ("SHTRV-01",)

STANDARD_RGB_EFFECTS = {0: "Off", 1: "Meteor Shower", 2: "Gradual Change", 3: "Flash"}
SHBLB_1_RGB_EFFECTS = {
    0: "Off",
    1: "Meteor Shower",
    2: "Gradual Change",
    3: "Flash",
    4: "Breath",
    5: "On/Off Gradual",
    6: "Red/Green Change",
}

# Media (Wall Display) volume is 0..10 on the device.
MEDIA_VOLUME_MAX = 10

# Attribute values pushed to the Remote are truncated to this length.
MAX_ATTR_LEN = 255
