"""Async clients for Shelly devices.

Two protocols are used, selected by the device generation:

* **Gen1** - plain HTTP REST (``/status``, ``/settings``, ``/relay/0?turn=on`` ...)
  with optional HTTP Basic auth. Gen1 has no push over HTTP, so it is polled.
* **Gen2 / Gen3 / Gen4** (Plus, Pro, Mini, Wall Display ...) - JSON-RPC 2.0 over
  a WebSocket on ``ws://<host>/rpc``. The device pushes ``NotifyStatus`` /
  ``NotifyFullStatus`` / ``NotifyEvent`` frames to the connected client, which
  gives realtime updates. Password-protected devices use the in-band SHA-256
  digest scheme (the device answers 401 with a challenge, the call is re-sent
  with an ``auth`` object) - the same mechanism the official aioshelly library
  uses.

Both clients use aiohttp only, so the PyInstaller binary stays small and does
not pull in the Bluetooth stack that aioshelly depends on.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
from typing import Any, Callable

import aiohttp

from uc_intg_shelly.const import HTTP_TIMEOUT, RPC_TIMEOUT, RPC_USERNAME, WS_HEARTBEAT

_LOG = logging.getLogger(__name__)

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)


class ShellyError(Exception):
    """Base error for Shelly communication."""


class ShellyConnectionError(ShellyError):
    """Device unreachable or connection dropped."""


class ShellyAuthError(ShellyError):
    """Authentication required or credentials rejected."""


class ShellyRpcError(ShellyError):
    """The device returned an RPC error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message


def _hex_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


_HA2 = _hex_hash("dummy_method:dummy_uri")


def _base_url(host: str, port: int) -> str:
    return f"http://{host}" if int(port or 80) == 80 else f"http://{host}:{port}"


async def get_shelly_info(host: str, port: int = 80, timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """Return the unauthenticated ``/shelly`` identification block.

    Gen2+ devices include ``gen`` (2, 3, 4); Gen1 devices do not. Raises
    :class:`ShellyConnectionError` if the host is not a reachable Shelly.
    """
    url = f"{_base_url(host, port)}/shelly"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise ShellyConnectionError(f"{url} returned HTTP {response.status}")
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as err:
        raise ShellyConnectionError(f"Cannot reach Shelly at {host}: {err}") from err
    if not isinstance(data, dict) or "mac" not in data:
        raise ShellyConnectionError(f"{host} did not answer like a Shelly device")
    return data


def device_generation(info: dict[str, Any]) -> int:
    """Return the device generation from a ``/shelly`` block (Gen1 has no ``gen``)."""
    try:
        return int(info.get("gen", 1) or 1)
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Gen1 (HTTP REST)
# ---------------------------------------------------------------------------
class Gen1Client:
    """HTTP client for one Gen1 Shelly device (single session per lifetime)."""

    def __init__(self, host: str, port: int = 80, username: str = "", password: str = "") -> None:
        self.host = host
        self.port = port
        self._auth = aiohttp.BasicAuth(username or "admin", password) if password else None
        self._session: aiohttp.ClientSession | None = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None and not self._session.closed

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_HTTP_TIMEOUT)

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._session = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``/<path>`` and return the decoded JSON body."""
        await self.connect()
        assert self._session is not None
        url = f"{_base_url(self.host, self.port)}/{path.lstrip('/')}"
        query = {k: _gen1_param(v) for k, v in (params or {}).items() if v is not None}
        try:
            async with self._session.get(url, params=query, auth=self._auth) as response:
                if response.status == 401:
                    raise ShellyAuthError("Gen1 device rejected the credentials")
                if response.status != 200:
                    raise ShellyError(f"GET /{path} returned HTTP {response.status}")
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise ShellyConnectionError(f"GET /{path} failed: {err}") from err
        except ValueError as err:
            raise ShellyError(f"GET /{path} returned invalid JSON") from err
        return data if isinstance(data, dict) else {}

    async def get_status(self) -> dict[str, Any]:
        return await self.get("status")

    async def get_settings(self) -> dict[str, Any]:
        return await self.get("settings")


def _gen1_param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------------------
# Gen2+ (JSON-RPC over WebSocket)
# ---------------------------------------------------------------------------
NotifyCallback = Callable[[str, dict[str, Any]], None]


class RpcClient:
    """WebSocket JSON-RPC client for one Gen2+ Shelly device."""

    def __init__(
        self,
        host: str,
        port: int = 80,
        password: str = "",
        auth_domain: str = "",
        on_notify: NotifyCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._password = password
        self._realm = auth_domain
        self._on_notify = on_notify
        self._src = f"uc-intg-shelly-{secrets.token_hex(4)}"
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._rx_task: asyncio.Task | None = None
        self._calls: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._nonce: str = ""
        self._nc: int = 1

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def set_realm(self, realm: str) -> None:
        self._realm = realm

    async def connect(self) -> None:
        """Open the WebSocket and start the receive loop."""
        if self.is_connected:
            return
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = f"ws://{self.host}:{self.port}/rpc"
        try:
            self._ws = await asyncio.wait_for(
                self._session.ws_connect(url, heartbeat=WS_HEARTBEAT), timeout=HTTP_TIMEOUT
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise ShellyConnectionError(f"WebSocket connect to {url} failed: {err}") from err
        self._rx_task = asyncio.create_task(self._rx_loop())
        _LOG.debug("Connected to %s", url)

    async def close(self) -> None:
        # Detach first so the receive loop does not report an intentional close as a drop.
        ws, self._ws = self._ws, None
        if self._rx_task is not None:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except (asyncio.CancelledError, Exception):  # pylint: disable=broad-exception-caught
                pass
            self._rx_task = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._session = None
        self._fail_pending(ShellyConnectionError("Connection closed"))

    def _fail_pending(self, err: Exception) -> None:
        for future in self._calls.values():
            if not future.done():
                future.set_exception(err)
        self._calls.clear()

    async def _rx_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    try:
                        frame = json.loads(msg.data)
                    except ValueError:
                        _LOG.debug("[%s] Ignoring invalid frame", self.host)
                        continue
                    if isinstance(frame, dict):
                        self._handle_frame(frame)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.debug("[%s] WebSocket receive error: %s", self.host, err)
        finally:
            _LOG.debug("[%s] WebSocket closed", self.host)
            self._fail_pending(ShellyConnectionError("WebSocket closed"))
            if self._on_notify is not None and self._ws is ws:
                try:
                    self._on_notify("WsClosed", {})
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

    def _handle_frame(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        frame_id = frame.get("id")
        if method:
            if frame_id is None and self._on_notify is not None:
                try:
                    self._on_notify(str(method), frame.get("params") or {})
                except Exception as err:  # pylint: disable=broad-exception-caught
                    _LOG.debug("[%s] Notification handler error: %s", self.host, err)
            return
        if frame_id is not None:
            future = self._calls.pop(frame_id, None)
            if future is not None and not future.done():
                future.set_result(frame)

    def _auth_frame(self) -> dict[str, Any]:
        ha1 = _hex_hash(f"{RPC_USERNAME}:{self._realm}:{self._password}")
        cnonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        response = _hex_hash(f"{ha1}:{self._nonce}:{self._nc}:{cnonce}:auth:{_HA2}")
        frame = {
            "realm": self._realm,
            "username": RPC_USERNAME,
            "nonce": self._nonce,
            "nc": self._nc,
            "cnonce": cnonce,
            "response": response,
            "algorithm": "SHA-256",
        }
        self._nc += 1
        return frame

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = RPC_TIMEOUT) -> Any:
        """Call an RPC method and return its ``result``."""
        async with self._lock:
            return await self._call(method, params, timeout)

    async def _call(
        self,
        method: str,
        params: dict[str, Any] | None,
        timeout: float,
        allow_auth_retry: bool = True,
        stale_retry: bool = False,
    ) -> Any:
        if not self.is_connected:
            raise ShellyConnectionError("Not connected")
        assert self._ws is not None
        self._next_id += 1
        call_id = self._next_id
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": call_id, "src": self._src, "method": method}
        if params:
            frame["params"] = params
        if self._password and self._nonce:
            frame["auth"] = self._auth_frame()

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._calls[call_id] = future
        try:
            await self._ws.send_str(json.dumps(frame))
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as err:
            raise ShellyConnectionError(f"{method} timed out") from err
        except (aiohttp.ClientError, ConnectionResetError, OSError, RuntimeError) as err:
            raise ShellyConnectionError(f"{method} failed: {err}") from err
        finally:
            self._calls.pop(call_id, None)

        if "result" in response:
            return response["result"]

        error = response.get("error") or {}
        code = int(error.get("code", -1))
        message = str(error.get("message", ""))
        if code != 401:
            raise ShellyRpcError(code, message)

        # 401 - the message carries the digest challenge.
        try:
            challenge = json.loads(message)
        except ValueError as err:
            raise ShellyAuthError(message) from err
        if not self._password:
            raise ShellyAuthError("Device is password protected - enter the password")
        if challenge.get("algorithm", "SHA-256") != "SHA-256":
            raise ShellyAuthError(f"Unsupported auth algorithm {challenge.get('algorithm')}")
        stale = challenge.get("stale") is True
        if (stale and stale_retry) or (not stale and not allow_auth_retry):
            raise ShellyAuthError("Password rejected by the device")
        self._nonce = str(challenge.get("nonce", ""))
        try:
            self._nc = int(challenge.get("nc", 1))
        except (TypeError, ValueError):
            self._nc = 1
        if not self._realm:
            self._realm = str(challenge.get("realm", ""))
        return await self._call(method, params, timeout, allow_auth_retry=False, stale_retry=stale or stale_retry)
