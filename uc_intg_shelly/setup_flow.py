"""Setup flow: discover Shelly devices via mDNS (or enter an IP), validate and cache their entities."""

import logging
import re
from typing import Any

from ucapi import RequestUserInput
from ucapi_framework import BaseSetupFlow

from uc_intg_shelly import discovery
from uc_intg_shelly.client import (
    Gen1Client,
    RpcClient,
    ShellyAuthError,
    ShellyError,
    device_generation,
    get_shelly_info,
)
from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import build_gen1_descriptors, build_rpc_descriptors

_LOG = logging.getLogger(__name__)


class ShellySetupFlow(BaseSetupFlow[ShellyConfig]):
    """Discover Shelly devices and add the chosen one."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._devices: list[dict[str, str]] = []
        self._username: str = ""
        self._password: str = ""

    def get_manual_entry_form(self) -> RequestUserInput:
        return RequestUserInput(
            {"en": "Shelly Device Setup"},
            [
                {
                    "id": "info",
                    "label": {"en": "Shelly Device"},
                    "field": {"label": {"value": {"en": (
                        "Shelly devices are discovered automatically on the network. Leave the IP "
                        "address blank to auto-discover, or enter it manually. If the device is "
                        "password protected, enter its password (Gen1 devices also need the username, "
                        "default 'admin'). Repeat the setup to add more devices."
                    )}}},
                },
                {"id": "host", "label": {"en": "IP Address (optional)"}, "field": {"text": {"value": ""}}},
                {"id": "username", "label": {"en": "Username (Gen1 only, optional)"},
                 "field": {"text": {"value": ""}}},
                {"id": "password", "label": {"en": "Password (optional)"}, "field": {"password": {"value": ""}}},
            ],
        )

    async def query_device(self, input_values: dict[str, Any]) -> ShellyConfig | RequestUserInput:
        host = str(input_values.get("host", "") or "").strip()
        self._username = str(input_values.get("username", "") or "").strip()
        self._password = str(input_values.get("password", "") or "")

        if host:
            port = 80
            if ":" in host and not host.startswith("["):
                host, _, port_str = host.rpartition(":")
                port = int(port_str) if port_str.isdigit() else 80
            return await self._build(host, port)

        _LOG.info("Discovering Shelly devices on the network...")
        self._devices = await discovery.discover(timeout=6.0)
        _LOG.info("Discovered %d Shelly device(s)", len(self._devices))

        if not self._devices:
            raise ValueError(
                "No Shelly devices were found on the network. Make sure the device is powered on "
                "and on the same network as the Remote, then try again and enter the IP address manually."
            )
        if len(self._devices) == 1:
            dev = self._devices[0]
            return await self._build(dev["host"], int(dev.get("port", 80) or 80))

        items = [{"id": d["host"], "label": {"en": f"{d['name']} ({d['host']})"}} for d in self._devices]
        return RequestUserInput(
            {"en": "Select Shelly Device"},
            [{
                "id": "device",
                "label": {"en": "Device"},
                "field": {"dropdown": {"value": items[0]["id"], "items": items}},
            }],
        )

    async def handle_additional_configuration_response(self, msg: Any) -> ShellyConfig | None:
        host = msg.input_values.get("device", "")
        dev = next((d for d in self._devices if d["host"] == host), None)
        if not dev:
            raise ValueError("Selected device not found")
        self._pending_device_config = await self._build(dev["host"], int(dev.get("port", 80) or 80))
        return None

    async def _build(self, host: str, port: int) -> ShellyConfig:
        try:
            info = await get_shelly_info(host, port)
        except ShellyError as err:
            raise ValueError(
                f"Could not reach a Shelly device at {host}. Check the IP address and make sure the "
                "device is powered on and on the same network."
            ) from err

        gen = device_generation(info)
        mac = str(info.get("mac", "")).lower().replace(":", "")
        try:
            if gen >= 2:
                config = await self._query_rpc(host, port, info, mac)
            else:
                config = await self._query_gen1(host, port, info, mac)
        except ShellyAuthError as err:
            raise ValueError(
                f"The Shelly device at {host} is password protected or the password is wrong. "
                "Enter the device password and try again."
            ) from err
        except ShellyError as err:
            raise ValueError(f"Communication with the Shelly device at {host} failed: {err}") from err
        _LOG.info("Shelly %s (%s, Gen%d) at %s: %d entities", config.name, config.model, gen, host,
                  len(config.descriptors) + 1)
        return config

    async def _query_rpc(self, host: str, port: int, info: dict[str, Any], mac: str) -> ShellyConfig:
        auth_domain = str(info.get("auth_domain") or info.get("id") or "")
        if info.get("auth_en") and not self._password:
            raise ShellyAuthError("Password required")
        client = RpcClient(host, port, password=self._password, auth_domain=auth_domain)
        try:
            await client.connect()
            dev_info = await client.call("Shelly.GetDeviceInfo") or {}
            config = await client.call("Shelly.GetConfig") or {}
            status = await client.call("Shelly.GetStatus") or {}
            try:
                methods = list((await client.call("Shelly.ListMethods") or {}).get("methods", []))
            except ShellyError:
                methods = []
        finally:
            await client.close()

        model = str(dev_info.get("model") or info.get("model") or "")
        sys_name = ((config.get("sys") or {}).get("device") or {}).get("name")
        name = str(dev_info.get("name") or sys_name or info.get("name") or f"Shelly {model}").strip()
        descriptors = build_rpc_descriptors(name, model, config, status, methods)
        return ShellyConfig(
            identifier=_identifier(mac or host),
            name=name,
            host=host,
            port=port,
            username="",
            password=self._password,
            gen=device_generation(info),
            model=model,
            mac=mac,
            auth_domain=auth_domain,
            descriptors=descriptors,
        )

    async def _query_gen1(self, host: str, port: int, info: dict[str, Any], mac: str) -> ShellyConfig:
        if info.get("auth") and not self._password:
            raise ShellyAuthError("Password required")
        client = Gen1Client(host, port, self._username or "admin", self._password)
        try:
            settings = await client.get_settings()
            status = await client.get_status()
        finally:
            await client.close()

        model = str(info.get("type") or (settings.get("device") or {}).get("type") or "")
        hostname = (settings.get("device") or {}).get("hostname")
        name = str(settings.get("name") or hostname or f"Shelly {model}").strip()
        descriptors = build_gen1_descriptors(name, model, settings, status)
        return ShellyConfig(
            identifier=_identifier(mac or host),
            name=name,
            host=host,
            port=port,
            username=self._username or ("admin" if self._password else ""),
            password=self._password,
            gen=1,
            model=model,
            mac=mac,
            descriptors=descriptors,
        )


def _identifier(base: str) -> str:
    return f"shelly_{re.sub(r'[^A-Za-z0-9]', '_', base.lower())}"
