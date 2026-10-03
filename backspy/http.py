"""HTTP layer: one aiohttp session, auth, rate-limit buckets, typed endpoints."""

import asyncio
import logging
import time
from collections import deque
from urllib.parse import quote

import aiohttp

from .errors import BackspaceError, HTTPException, RateLimited
from .models import Attachment
from .utils import b64

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Limits copied from the API docs; shared where the server's keying is unknown.
BUCKET_LIMITS = {
    "messages": (5, 5),      # POST channel/dm messages + interaction responses
    "reactions": (10, 5),    # PUT/DELETE reactions
    "commands": (10, 300),   # PUT /api/bots/@me/commands
    "interactions": (5, 5),  # POST /api/interactions
}


class _Bucket:
    """Sliding-window limiter so the bot never eats a 429."""

    __slots__ = ("limit", "per", "_hits", "_lock")

    def __init__(self, limit, per):
        self.limit = limit
        self.per = per
        self._hits = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] >= self.per:
                    self._hits.popleft()
                if len(self._hits) < self.limit:
                    self._hits.append(now)
                    return
                delay = self.per - (now - self._hits[0]) + 0.05
            await asyncio.sleep(delay)


def _normalize_attachments(attachments):
    if attachments is None:
        return None
    if isinstance(attachments, (str, Attachment)):
        attachments = [attachments]
    out = []
    for item in attachments:
        if isinstance(item, Attachment):
            out.append(item.id)
        elif isinstance(item, dict):
            out.append(item)
        else:
            out.append(str(item))
    return out


def _message_payload(content, attachments, reply_to):
    payload = {}
    if content is not None:
        payload["content"] = content
    normalized = _normalize_attachments(attachments)
    if normalized is not None:
        payload["attachments"] = normalized
    if reply_to is not None:
        payload["replyToId"] = reply_to
    return payload or None


async def _read_body(resp):
    if resp.content_type == "application/json":
        try:
            return await resp.json(content_type=None)
        except ValueError:
            return None
    text = await resp.text()
    return text or None


async def _retry_after(resp):
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return float(header) + 0.1
        except ValueError:
            pass
    try:
        body = await resp.json(content_type=None)
        if isinstance(body, dict):
            for key in ("retryAfter", "retry_after", "retryIn"):
                value = body.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return value / 1000.0 if value > 15 else float(value)
    except Exception:
        pass
    return 1.0


def _tus_metadata(filename, content_type):
    parts = [f"filename {b64(filename or 'upload')}"]
    if content_type:
        parts.append(f"filetype {b64(content_type)}")
    return ",".join(parts)


class HTTPClient:
    """Low-level REST client. Normally you talk to Client, not to this."""

    def __init__(self, base_url, token, *, auth_scheme="Bot", session=None,
                 timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.auth_scheme = auth_scheme
        self.timeout = timeout
        self._session = session
        self._owns_session = session is None
        self._buckets = {name: _Bucket(limit, per)
                         for name, (limit, per) in BUCKET_LIMITS.items()}

    # ---------------------------------------------------------- session plumbing

    def auth_header(self):
        if not self.token:
            return None
        return f"{self.auth_scheme} {self.token}"

    async def ensure_session(self):
        if self._session is None or self._session.closed:
            if not self._owns_session:
                raise BackspaceError("the aiohttp session you passed has been closed")
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self):
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------- core request

    async def request(self, method, path, *, bucket=None, json=None, params=None,
                      headers=None, data=None, max_attempts=5):
        session = await self.ensure_session()
        url = path if path.startswith(("http://", "https://")) else self.base_url + path
        request_headers = dict(headers or {})
        auth = self.auth_header()
        if auth:
            request_headers["Authorization"] = auth
        params = {k: v for k, v in (params or {}).items() if v is not None}
        network_error = None
        saw_429 = False
        for attempt in range(1, max_attempts + 1):
            if bucket is not None:
                await self._buckets[bucket].acquire()
            try:
                async with session.request(
                    method, url, json=json, params=params or None,
                    data=data, headers=request_headers,
                ) as resp:
                    if resp.status == 429:
                        saw_429 = True
                        delay = await _retry_after(resp)
                        log.warning("429 on %s %s - sleeping %.1fs", method, path, delay)
                        await asyncio.sleep(delay)
                        continue
                    body = await _read_body(resp)
                    if resp.status >= 400:
                        raise HTTPException(resp.status, method, path,
                                            body if isinstance(body, dict) else None)
                    return body
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError) as exc:
                network_error = exc
                if attempt < max_attempts:
                    await asyncio.sleep(0.5 * attempt)
        if saw_429 and network_error is None:
            raise RateLimited(429, method, path, None, retry_after=2.0)
        raise BackspaceError(
            f"{method} {path} failed after {max_attempts} attempts: {network_error!r}")

    # ------------------------------------------------------------------ session

    async def fetch_me(self):
        return await self.request("GET", "/api/users/@me")

    # ----------------------------------------------------------------- messages

    async def send_channel_message(self, channel_id, *, content=None,
                                   attachments=None, reply_to=None):
        payload = _message_payload(content, attachments, reply_to)
        return await self.request("POST", f"/api/channels/{channel_id}/messages",
                                  json=payload, bucket="messages")

    async def send_dm_message(self, dm_channel_id, *, content=None,
                              attachments=None, reply_to=None):
        payload = _message_payload(content, attachments, reply_to)
        return await self.request("POST", f"/api/dm/{dm_channel_id}/messages",
                                  json=payload, bucket="messages")

    async def edit_channel_message(self, message_id, *, content=None, attachments=None):
        payload = _message_payload(content, attachments, None) or {}
        return await self.request("PATCH", f"/api/messages/{message_id}", json=payload)

    async def edit_dm_message(self, message_id, *, content=None, attachments=None):
        payload = _message_payload(content, attachments, None) or {}
        return await self.request("PATCH", f"/api/dm/messages/{message_id}", json=payload)

    async def delete_channel_message(self, message_id):
        return await self.request("DELETE", f"/api/messages/{message_id}")

    async def delete_dm_message(self, message_id):
        return await self.request("DELETE", f"/api/dm/messages/{message_id}")

    async def fetch_channel_messages(self, channel_id, *, before=None, limit=None):
        params = {"before": str(before) if before is not None else None,
                  "limit": str(limit) if limit is not None else None}
        return await self.request("GET", f"/api/channels/{channel_id}/messages", params=params)

    async def fetch_dm_messages(self, dm_channel_id, *, before=None, limit=None):
        params = {"before": str(before) if before is not None else None,
                  "limit": str(limit) if limit is not None else None}
        return await self.request("GET", f"/api/dm/{dm_channel_id}/messages", params=params)

    # ---------------------------------------------------------------- reactions

    async def add_reaction(self, message_id, emoji):
        path = f"/api/messages/{message_id}/reactions/{quote(emoji, safe='')}"
        return await self.request("PUT", path, bucket="reactions")

    async def remove_reaction(self, message_id, emoji):
        path = f"/api/messages/{message_id}/reactions/{quote(emoji, safe='')}"
        return await self.request("DELETE", path, bucket="reactions")

    # ---------------------------------------------------------------------- dm

    async def open_dm(self, user_id):
        return await self.request("POST", "/api/dm", json={"userId": user_id})

    # ------------------------------------------------------------------- spaces

    async def join_space(self, invite_code):
        return await self.request("POST", "/api/spaces/join", json={"inviteCode": invite_code})

    # ----------------------------------------------------------------- commands

    async def register_commands(self, commands):
        return await self.request("PUT", "/api/bots/@me/commands",
                                  json={"commands": commands}, bucket="commands")

    async def fetch_commands(self):
        return await self.request("GET", "/api/bots/@me/commands")

    async def list_chat_commands(self, *, channel_id=None, dm_channel_id=None):
        params = {"channelId": channel_id, "dmChannelId": dm_channel_id}
        return await self.request("GET", "/api/commands", params=params)

    # ------------------------------------------------------------ interactions

    async def create_interaction(self, *, bot_id, command, options=None,
                                 channel_id=None, dm_channel_id=None):
        payload = {"botId": bot_id, "command": command}
        if options:
            payload["options"] = options
        if channel_id is not None:
            payload["channelId"] = channel_id
        if dm_channel_id is not None:
            payload["dmChannelId"] = dm_channel_id
        return await self.request("POST", "/api/interactions",
                                  json=payload, bucket="interactions")

    async def respond_interaction(self, interaction_id, *, content=None, attachments=None):
        payload = {}
        if content is not None:
            payload["content"] = content
        normalized = _normalize_attachments(attachments)
        if normalized is not None:
            payload["attachments"] = normalized
        return await self.request("POST", f"/api/interactions/{interaction_id}/respond",
                                  json=payload, bucket="messages")

    # -------------------------------------------------------------------- voice

    async def livekit_token(self, channel_id):
        return await self.request("POST", "/api/livekit/token",
                                  json={"channelId": channel_id})

    # ---------------------------------------------------------------------- tus

    async def tus_upload(self, data, *, filename, content_type):
        """Upload bytes through tus on /api/files/; returns the upload id."""
        session = await self.ensure_session()
        auth = self.auth_header()
        headers = {
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(len(data)),
            "Upload-Metadata": _tus_metadata(filename, content_type),
        }
        if auth:
            headers["Authorization"] = auth
        async with session.post(f"{self.base_url}/api/files/", headers=headers) as resp:
            if resp.status >= 400:
                body = await _read_body(resp)
                raise HTTPException(resp.status, "POST", "/api/files/",
                                    body if isinstance(body, dict) else None)
            location = resp.headers.get("Location", "")
        if not location:
            raise BackspaceError("tus upload: the server did not return a Location header")
        file_id = location.rstrip("/").rsplit("/", 1)[-1]
        url = location if location.startswith(("http://", "https://")) else self.base_url + location
        patch_headers = {
            "Tus-Resumable": "1.0.0",
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        }
        if auth:
            patch_headers["Authorization"] = auth
        async with session.patch(url, headers=patch_headers, data=data) as resp:
            if resp.status >= 400:
                body = await _read_body(resp)
                raise HTTPException(resp.status, "PATCH", url,
                                    body if isinstance(body, dict) else None)
        return file_id