"""Gateway: websocket connection with auth handshake and auto-reconnect."""

import asyncio
import json
import logging

import aiohttp

from .errors import ConnectionClosed, LoginFailure

log = logging.getLogger(__name__)

_STOP_TYPES = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.ERROR,
)


class Gateway:
    """Websocket connection to <instance>/ws.

    Performs the auth handshake, answers server pings, detects dead links and
    reconnects with backoff. There is no resume: every reconnect starts with a
    fresh `ready` payload.
    """

    def __init__(self, client, ws_url, *, reconnect=True, max_backoff=60.0):
        self.client = client
        self.ws_url = ws_url
        self.reconnect = reconnect
        self.max_backoff = max_backoff
        self._ws = None
        self._closed = False
        self._stop = asyncio.Event()

    @property
    def connected(self):
        return self._ws is not None and not self._ws.closed

    async def send(self, payload):
        if not self.connected:
            raise ConnectionClosed("the websocket is not connected")
        await self._ws.send_json(payload)

    async def close(self):
        self._closed = True
        self._stop.set()
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            await ws.close()

    async def run(self):
        backoff = 1.0
        while not self._closed:
            authed = False
            try:
                authed = await self._connect_once()
            except LoginFailure:
                self._closed = True
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError) as exc:
                if self._closed:
                    break
                log.warning("websocket error (%r), reconnecting", exc)
            if self._closed:
                break
            if not self.reconnect:
                raise ConnectionClosed("websocket closed, reconnect disabled")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = 1.0 if authed else min(backoff * 2, self.max_backoff)
        log.debug("gateway stopped")

    async def _connect_once(self):
        session = await self.client.http.ensure_session()
        log.debug("connecting to %s", self.ws_url)
        try:
            ws = await session.ws_connect(self.ws_url, autoping=True, heartbeat=25.0)
        except aiohttp.WSServerHandshakeError as exc:
            if exc.status == 401:
                raise LoginFailure(f"websocket handshake rejected (401): {exc}") from exc
            raise
        async with ws:
            self._ws = ws
            authed = False
            try:
                await ws.send_json({"type": "auth", "token": self.client.http.token})
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        try:
                            payload = json.loads(message.data)
                        except ValueError:
                            log.warning("non-JSON websocket frame dropped: %r", message.data)
                            continue
                        if not isinstance(payload, dict):
                            continue
                        kind = payload.get("type")
                        if not authed:
                            if kind == "ready":
                                authed = True
                            elif kind == "error":
                                raise LoginFailure(f"websocket auth rejected: {payload}")
                        self.client._schedule_event(payload)
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        continue
                    elif message.type in _STOP_TYPES:
                        break
                log.debug("websocket closed (code=%s)", ws.close_code)
                return authed
            finally:
                self._ws = None