"""Shelly device - one physical Shelly over HTTP (Gen1) or WebSocket RPC (Gen2+).

Gen2+ devices keep a WebSocket open; the device pushes ``NotifyStatus`` deltas
which are merged into the cached status tree and propagated immediately. A
slow safety-net poll (``Shelly.GetStatus``) runs alongside, and drops to a
fast poll while the WebSocket is down (it is re-opened on the next poll). Gen1
devices are polled over HTTP.

If the device becomes unreachable the poll marks it unavailable and keeps
retrying - recovery == the next successful poll.
"""

import asyncio
import logging
import time
from typing import Any

from ucapi_framework import PollingDevice

from uc_intg_shelly.client import (
    Gen1Client,
    RpcClient,
    ShellyAuthError,
    ShellyConnectionError,
    ShellyError,
)
from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.const import (
    POLL_GEN1,
    POLL_MOVING,
    POLL_RPC_NO_PUSH,
    POLL_RPC_PUSH,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from uc_intg_shelly.descriptors import (
    build_gen1_descriptors,
    build_rpc_descriptors,
    resolve,
)

_LOG = logging.getLogger(__name__)


def _deep_merge(target: dict[str, Any], delta: dict[str, Any]) -> None:
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


class ShellyDevice(PollingDevice):
    """One Shelly device."""

    def __init__(self, device_config: ShellyConfig, **kwargs: Any) -> None:
        poll = POLL_GEN1 if device_config.gen <= 1 else POLL_RPC_NO_PUSH
        super().__init__(device_config, poll_interval=poll, **kwargs)
        self._device_config = device_config
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._state: str = STATE_UNAVAILABLE
        self._gen1: Gen1Client | None = None
        self._rpc: RpcClient | None = None

        self.status: dict[str, Any] = {}
        self.config_data: dict[str, Any] = {}  # RPC Shelly.GetConfig / Gen1 /settings
        self.firmware: str = ""
        self.last_event_ts: float = 0.0
        self._cover_task: asyncio.Task | None = None
        self._events: dict[str, str] = {}  # last input event per component (from NotifyEvent)
        self._ws_check_task: asyncio.Task | None = None

    # -- identity ------------------------------------------------------
    @property
    def identifier(self) -> str:
        return self._device_config.identifier

    @property
    def name(self) -> str:
        return self._device_config.name

    @property
    def address(self) -> str:
        return self._device_config.host

    @property
    def log_id(self) -> str:
        return f"{self.name} ({self.address})"

    @property
    def state(self) -> str:
        return self._state

    @property
    def available(self) -> bool:
        return self._state == STATE_ON

    @property
    def is_rpc(self) -> bool:
        return self._device_config.gen >= 2

    @property
    def model(self) -> str:
        return self._device_config.model

    def value(self, path: list) -> Any:
        """Return the live value at ``path`` in the status tree (None if missing)."""
        if path and path[0] == "_events":
            return resolve(self._events, path[1:])
        return resolve(self.status, path)

    # -- connection lifecycle ------------------------------------------
    def _new_rpc_client(self) -> RpcClient:
        cfg = self._device_config
        return RpcClient(
            cfg.host,
            cfg.port,
            password=cfg.password,
            auth_domain=cfg.auth_domain,
            on_notify=self._on_notify,
        )

    async def establish_connection(self) -> None:
        async with self._connect_lock:
            await self._close_clients()
            cfg = self._device_config
            if self.is_rpc:
                self._rpc = self._new_rpc_client()
                await self._rpc.connect()
                info = await self._rpc.call("Shelly.GetDeviceInfo")
                self.firmware = str((info or {}).get("ver", "") or "")
                self.config_data = await self._rpc.call("Shelly.GetConfig") or {}
                self.status = await self._rpc.call("Shelly.GetStatus") or {}
            else:
                self._gen1 = Gen1Client(cfg.host, cfg.port, cfg.username, cfg.password)
                await self._gen1.connect()
                self.config_data = await self._gen1.get_settings()
                self.status = await self._gen1.get_status()
                self.firmware = str((self.config_data.get("fw") or "") if isinstance(self.config_data, dict) else "")

        self._state = STATE_ON
        await self._refresh_descriptors()
        self._update_poll_interval()
        self.push_update()

    async def _refresh_descriptors(self) -> None:
        """Persist new descriptors if the device's components changed (applies on next start)."""
        try:
            cfg = self._device_config
            if self.is_rpc:
                methods: list[str] = []
                try:
                    result = await self._rpc.call("Shelly.ListMethods") if self._rpc else {}
                    methods = list((result or {}).get("methods", []))
                except ShellyError:
                    pass
                descriptors = build_rpc_descriptors(cfg.name, cfg.model, self.config_data, self.status, methods)
            else:
                descriptors = build_gen1_descriptors(cfg.name, cfg.model, self.config_data, self.status)
            if descriptors != cfg.descriptors:
                old = {d["uid"] for d in cfg.descriptors}
                new = {d["uid"] for d in descriptors}
                if old != new:
                    _LOG.info(
                        "[%s] Device components changed (%d -> %d entities) - restart the integration to apply",
                        self.log_id, len(old), len(new),
                    )
                    self.update_config(descriptors=descriptors)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.debug("[%s] Descriptor refresh skipped: %s", self.log_id, err)

    async def _close_clients(self) -> None:
        if self._rpc is not None:
            await self._rpc.close()
            self._rpc = None
        if self._gen1 is not None:
            await self._gen1.close()
            self._gen1 = None

    async def disconnect(self) -> None:
        for task in (self._cover_task, self._ws_check_task):
            if task is not None and not task.done():
                task.cancel()
        async with self._connect_lock:
            await self._close_clients()
        self._state = STATE_UNAVAILABLE
        await super().disconnect()

    def _any_cover_moving(self) -> bool:
        if self.is_rpc:
            return any(
                isinstance(v, dict) and v.get("state") in ("opening", "closing")
                for k, v in self.status.items()
                if k.startswith("cover:")
            )
        return any(
            isinstance(r, dict) and r.get("state") in ("open", "close")
            for r in (self.status.get("rollers") or [])
        )

    def _update_poll_interval(self) -> None:
        if not self.is_rpc:
            self._poll_interval = POLL_GEN1
        elif self._rpc is not None and self._rpc.is_connected:
            self._poll_interval = POLL_RPC_PUSH
        else:
            self._poll_interval = POLL_RPC_NO_PUSH

    async def poll_device(self) -> None:
        try:
            await self.refresh()
            if self._state != STATE_ON:
                _LOG.info("[%s] Device is reachable again", self.log_id)
            self._state = STATE_ON
        except ShellyAuthError as err:
            _LOG.error("[%s] Authentication failed - re-run setup with the correct password: %s", self.log_id, err)
            self._mark_unavailable()
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.debug("[%s] Poll error: %s", self.log_id, err)
            self._mark_unavailable()
        self._update_poll_interval()
        self.push_update()

    def _mark_unavailable(self) -> None:
        if self._state != STATE_UNAVAILABLE:
            _LOG.warning("[%s] Device unreachable", self.log_id)
        self._state = STATE_UNAVAILABLE

    async def refresh(self) -> None:
        """Fetch the full status (re-opening the RPC WebSocket if it dropped)."""
        if self.is_rpc:
            async with self._connect_lock:
                if self._rpc is None or not self._rpc.is_connected:
                    if self._rpc is not None:
                        await self._rpc.close()
                    self._rpc = self._new_rpc_client()
                    await self._rpc.connect()
                rpc = self._rpc
            self.status = await rpc.call("Shelly.GetStatus") or {}
        else:
            async with self._connect_lock:
                if self._gen1 is None:
                    cfg = self._device_config
                    self._gen1 = Gen1Client(cfg.host, cfg.port, cfg.username, cfg.password)
                gen1 = self._gen1
            self.status = await gen1.get_status()

    async def _check_after_ws_close(self) -> None:
        """Re-open the WebSocket right away so an outage is detected without waiting for the next poll."""
        await asyncio.sleep(1)
        if not self.is_connected:  # integration disconnected meanwhile
            return
        try:
            await self.refresh()
            self._state = STATE_ON
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.debug("[%s] Reconnect after WebSocket close failed: %s", self.log_id, err)
            self._mark_unavailable()
        self._update_poll_interval()
        self.push_update()

    def _on_notify(self, method: str, params: dict[str, Any]) -> None:
        """Handle a pushed RPC notification (runs on the event loop)."""
        if method == "WsClosed":
            _LOG.debug("[%s] WebSocket closed - falling back to fast polling", self.log_id)
            self._poll_interval = POLL_RPC_NO_PUSH
            if self._state == STATE_ON and (self._ws_check_task is None or self._ws_check_task.done()):
                self._ws_check_task = asyncio.create_task(self._check_after_ws_close())
            return
        if method == "NotifyStatus" or method == "NotifyFullStatus":
            delta = {k: v for k, v in params.items() if k != "ts"}
            if method == "NotifyFullStatus":
                self.status.update(delta)
            else:
                _deep_merge(self.status, delta)
            self._state = STATE_ON
            self._ensure_cover_tracking()
            self.push_update()
        elif method == "NotifyEvent":
            changed = False
            for event in params.get("events", []) or []:
                component = event.get("component")
                name = event.get("event")
                if not component or not name:
                    continue
                if component.startswith("input:"):
                    self._events[component] = name
                    changed = True
            if changed:
                self.last_event_ts = time.monotonic()
                self.push_update()

    # -- commands ------------------------------------------------------
    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Call an RPC method, re-opening the WebSocket once on a dropped connection."""
        for attempt in range(2):
            async with self._connect_lock:
                if self._rpc is None or not self._rpc.is_connected:
                    if self._rpc is not None:
                        await self._rpc.close()
                    self._rpc = self._new_rpc_client()
                    await self._rpc.connect()
                rpc = self._rpc
            try:
                return await rpc.call(method, params)
            except ShellyConnectionError as err:
                _LOG.debug("[%s] %s attempt %d failed: %s", self.log_id, method, attempt + 1, err)
                if attempt:
                    raise
        return None

    async def gen1(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a Gen1 HTTP command and refresh the status afterwards."""
        async with self._connect_lock:
            if self._gen1 is None:
                cfg = self._device_config
                self._gen1 = Gen1Client(cfg.host, cfg.port, cfg.username, cfg.password)
            gen1 = self._gen1
        result = await gen1.get(path, params)
        try:
            self.status = await gen1.get_status()
            self._state = STATE_ON
        except ShellyError as err:
            _LOG.debug("[%s] Status refresh after command failed: %s", self.log_id, err)
        self._ensure_cover_tracking()
        self._update_poll_interval()
        self.push_update()
        return result

    def _ensure_cover_tracking(self) -> None:
        """Track cover position every second while moving (position is not pushed mid-move)."""
        if self._any_cover_moving() and (self._cover_task is None or self._cover_task.done()):
            self._cover_task = asyncio.create_task(self._track_covers())

    async def _track_covers(self) -> None:
        try:
            for _ in range(180):  # hard stop after ~3 minutes
                await asyncio.sleep(POLL_MOVING)
                try:
                    await self.refresh()
                    self._state = STATE_ON
                except ShellyError as err:
                    _LOG.debug("[%s] Cover tracking refresh failed: %s", self.log_id, err)
                    break
                self.push_update()
                if not self._any_cover_moving():
                    break
        finally:
            self._update_poll_interval()

    async def after_command(self) -> None:
        """Start cover tracking right after a command that may start movement."""
        await asyncio.sleep(0.5)
        try:
            await self.refresh()
        except ShellyError:
            return
        self._ensure_cover_tracking()
        self.push_update()
