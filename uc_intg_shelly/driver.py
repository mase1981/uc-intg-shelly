"""Integration driver for Shelly devices."""

from ucapi_framework import BaseIntegrationDriver

from uc_intg_shelly.button import create_buttons
from uc_intg_shelly.climate import create_climates
from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.cover import create_covers
from uc_intg_shelly.device import ShellyDevice
from uc_intg_shelly.ir_emitter import create_ir_emitters
from uc_intg_shelly.light import create_lights
from uc_intg_shelly.media_player import create_media_players
from uc_intg_shelly.remote import create_remote
from uc_intg_shelly.select import create_selects
from uc_intg_shelly.sensor import create_sensors
from uc_intg_shelly.switch import create_switches


class ShellyDriver(BaseIntegrationDriver[ShellyDevice, ShellyConfig]):
    """Builds the entity set for each Shelly device from its cached descriptors."""

    def __init__(self) -> None:
        super().__init__(
            device_class=ShellyDevice,
            entity_classes=[
                create_switches,
                create_lights,
                create_covers,
                create_climates,
                create_media_players,
                create_remote,
                create_selects,
                create_buttons,
                create_ir_emitters,
                create_sensors,
            ],
            driver_id="shelly",
        )
