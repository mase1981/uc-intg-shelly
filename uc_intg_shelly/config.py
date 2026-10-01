"""Configuration dataclass and manager for the Shelly integration.

One config entry represents a single Shelly device. The entity descriptors
discovered during setup are cached so entities can be built without a live
connection (reboot-safe); live values flow in through the device on connect.
"""

from dataclasses import dataclass, field

from ucapi_framework import BaseConfigManager


@dataclass
class ShellyConfig:
    """Configuration for a single Shelly device."""

    identifier: str = ""
    name: str = ""
    host: str = ""
    port: int = 80
    username: str = ""
    password: str = ""
    gen: int = 0
    model: str = ""
    mac: str = ""
    auth_domain: str = ""
    descriptors: list[dict] = field(default_factory=list)


class ShellyConfigManager(BaseConfigManager[ShellyConfig]):
    """Config manager bound to :class:`ShellyConfig`."""
