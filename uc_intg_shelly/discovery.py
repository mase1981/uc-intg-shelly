"""mDNS discovery for Shelly devices.

Gen2+ devices advertise ``_shelly._tcp.local.``; Gen1 devices only advertise
``_http._tcp.local.`` with an instance name starting with ``shelly`` (for
example ``shelly1pm-84CCA8A11B2C``). Both are browsed in one zeroconf session.
"""

import asyncio
import logging
import socket
import time

from uc_intg_shelly.const import MDNS_HTTP, MDNS_RPC

_LOG = logging.getLogger(__name__)


def _scan_sync(timeout: float) -> list[dict[str, str]]:
    """Blocking zeroconf browse. Returns discovered Shelly devices."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        _LOG.warning("zeroconf not available - discovery disabled")
        return []

    devices: dict[str, dict[str, str]] = {}

    class _Listener:
        def remove_service(self, zc, type_, name):  # noqa: D401
            pass

        def update_service(self, zc, type_, name):
            pass

        def add_service(self, zc, type_, name):
            instance = name.replace("." + type_, "") if name else ""
            if type_ == MDNS_HTTP and not instance.lower().startswith("shelly"):
                return
            try:
                info = zc.get_service_info(type_, name, timeout=2000)
            except Exception:  # pylint: disable=broad-exception-caught
                info = None
            if not info or not info.addresses:
                return
            host = socket.inet_ntoa(info.addresses[0])
            port = info.port or 80
            existing = devices.get(host)
            # Prefer the Gen2+ record when a device advertises both services.
            if existing and existing.get("service") == MDNS_RPC:
                return
            devices[host] = {
                "name": instance or "Shelly",
                "host": host,
                "port": str(port),
                "service": type_,
            }

    zc = Zeroconf()
    try:
        listener = _Listener()
        ServiceBrowser(zc, [MDNS_RPC, MDNS_HTTP], listener)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        zc.close()
    return sorted(devices.values(), key=lambda d: d["name"].lower())


async def discover(timeout: float = 6.0) -> list[dict[str, str]]:
    """Discover Shelly devices on the LAN via mDNS (blocking scan in a thread)."""
    try:
        return await asyncio.to_thread(_scan_sync, timeout)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOG.debug("Shelly discovery failed: %s", err)
        return []
