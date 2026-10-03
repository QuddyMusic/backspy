"""Client: events, slash commands and REST helpers in one object."""

import asyncio
import inspect
import logging
import os
from mimetypes import guess_type
from pathlib import Path

from .commands import Command, Option, coerce_value, infer_options
from .errors import BackspaceError, HTTPException, LoginFailure
from .gateway import Gateway
from .http import HTTPClient
from .models import (
    Attachment,
    Interaction,
    Message,
    Object,
    ReactionEvent,
    TypingEvent,
    User,
)

log = logging.getLogger(__name__)

_CTX_NAMES = ("ctx", "context", "interaction", "itx")

_CHANNEL_MESSAGE_EVENTS = ("message_created", "message_updated", "message_deleted")
_DM_MESSAGE_EVENTS = ("dm_message_created", "dm_message_updated", "dm_message_deleted")
_REACTION_EVENTS = ("reaction_added", "reaction_add", "reaction_removed", "reaction_remove")
_TYPING_EVENTS = ("typing", "typing_start", "dm_typing", "dm_typing_start")


def _norm_event(name):
    name = str(name)
    return name[3:] if name.startswith("on_") else name


class Client:
    """A Backspace bot: REST + websocket + events + slash commands.

    Args:
        token: the bot token (falls back to the BOT_TOKEN env var).
        base_url: instance address, e.g. https://chat.example
            (falls back to the BACKSPACE_URL env var).
        sync_commands: register local slash commands on start (default True).
        ignore_self: don't deliver the bot's own messages to handlers
            (default True - a bot that answers messages needs this).
        reconnect: reconnect the websocket automatically (default True).
        session: an existing aiohttp.ClientSession to reuse.
        max_backoff: cap for the reconnect delay, in seconds.
    """

    def __init__(self, token=None, *, base_url=None, sync_commands=True,
                 ignore_self=True, reconnect=True, session=None, max_backoff=60.0):
        self.token = token or os.environ.get("BOT_TOKEN")
        base_url = (base_url or os.environ.get("BACKSPACE_URL") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must look like https://<instance> "
                             "(argument or BACKSPACE_URL env)")
        self.base_url = base_url
        self.sync_commands = sync_commands
        self.ignore_self = ignore_self

        self.http = HTTPClient(base_url, self.token, session=session)
        ws_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"
        self._gateway = Gateway(self, ws_url, reconnect=reconnect, max_backoff=max_backoff)

        self._listeners = {}
        self._waiters = {}
        self._slash_commands = {}
        self._users = {}
        self._tasks = set()
        self._me = None
        self.user = None
        self.voice_seats = set()
        self.raw_ready = None
        self.ready = asyncio.Event()
        self._closed = False

    # ------------------------------------------------------------------- events

    def on(self, event):
        """`@bot.on("message_created")` - any number of handlers per event."""
        event = _norm_event(event)

        def decorator(func):
            self.add_listener(event, func)
            return func

        return decorator

    def event(self, func):
        """`@bot.event` for handlers named `on_<event_name>` (discord.py style)."""
        name = getattr(func, "__name__", "")
        if not name.startswith("on_"):
            raise ValueError("handler must be named on_<event>, or use bot.on('<event>')")
        self.add_listener(name[3:], func)
        return func

    def add_listener(self, event, func):
        self._listeners.setdefault(_norm_event(event), []).append(func)
        return func

    def remove_listener(self, event, func):
        listeners = self._listeners.get(_norm_event(event))
        if listeners and func in listeners:
            listeners.remove(func)

    async def wait_for(self, event, *, check=None, timeout=None):
        """Wait for the next event, optionally matching `check(event) -> bool`."""
        event = _norm_event(event)
        future = asyncio.get_running_loop().create_future()
        entry = (future, check)
        self._waiters.setdefault(event, []).append(entry)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            try:
                self._waiters.get(event, []).remove(entry)
            except ValueError:
                pass

    async def wait_until_ready(self):
        await self.ready.wait()

    # ---------------------------------------------------------------- lifecycle

    def run(self, token=None):
        """Blocking entrypoint: `bot.run("TOKEN")`. Ctrl+C shuts down cleanly."""
        if token is not None:
            self.token = token
            self.http.token = token
        try:
            asyncio.run(self.start())
        except KeyboardInterrupt:
            log.info("interrupted, shutting down")

    async def start(self):
        if not self.token:
            raise LoginFailure("no bot token: pass it to Client(token=...), "
                               "bot.run(token) or set BOT_TOKEN")
        try:
            if self._me is None:
                await self.login()
            if self.sync_commands:
                await self.push_commands()
            await self._gateway.run()
        finally:
            await self.close()

    async def login(self):
        try:
            data = await self.http.fetch_me()
        except HTTPException as exc:
            raise LoginFailure(str(exc)) from exc
        if not isinstance(data, dict) or "id" not in data:
            raise LoginFailure(f"unexpected answer from /api/users/@me: {data!r}")
        self._me = User(data)
        self.user = self._me
        self._cache_user(self._me)
        log.info("logged in as %s (id=%s)", self._me.username, self._me.id)
        return self._me

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            await self._gateway.close()
        except Exception:
            log.debug("gateway close failed", exc_info=True)
        current = asyncio.current_task()
        pending = [t for t in self._tasks if not t.done() and t is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self.http.close()
        log.info("closed")

    @property
    def user_id(self):
        return self._me.id if self._me else None

    def get_user(self, user_id):
        """A user from the cache (filled from events), or None."""
        return self._users.get(user_id)

    # ----------------------------------------------------------------- dispatch

    def _schedule_event(self, payload):
        if self._closed or not isinstance(payload, dict):
            return
        self._spawn(self._dispatch(payload))

    def _spawn(self, coroutine):
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _dispatch(self, payload):
        kind = payload.get("type")
        if not kind:
            return
        if kind == "error":
            log.warning("server error event: %r", payload)
        if kind == "ready":
            self._handle_ready(payload)
        elif kind == "user_updated":
            user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
            if isinstance(user, dict) and user.get("id"):
                self._cache_user(User(user))
        elif kind == "voice_state_update":
            self._handle_voice_state(payload)

        if self._should_skip(kind, payload):
            return

        try:
            data = self._wrap_event(kind, payload)
        except Exception:
            log.exception("could not build a model for %s, passing raw payload", kind)
            data = Object(payload)

        for future, check in list(self._waiters.get(kind, [])):
            if future.done():
                continue
            try:
                matched = bool(check(data)) if check is not None else True
            except Exception:
                log.exception("error in wait_for check (%s)", kind)
                continue
            if matched:
                future.set_result(data)

        for listener in self._listeners.get(kind, []):
            self._spawn(self._run_listener(listener, data, kind))

        if kind == "interaction_created" and isinstance(data, Interaction):
            self._spawn(self._run_slash_command(data))

    async def _run_listener(self, listener, data, kind):
        try:
            result = listener(data)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("error in %r listener", kind)

    def _wrap_event(self, kind, payload):
        if kind in _CHANNEL_MESSAGE_EVENTS:
            return Message(payload, self)
        if kind in _DM_MESSAGE_EVENTS:
            return Message(payload, self, dm=True)
        if kind == "interaction_created":
            return Interaction(payload.get("interaction") or payload, self)
        if kind in _REACTION_EVENTS:
            return ReactionEvent(payload, self)
        if kind in _TYPING_EVENTS:
            return TypingEvent(payload, self)
        return Object(payload)

    def _should_skip(self, kind, payload):
        """The bot's own messages are not delivered, so it can't answer itself."""
        if not self.ignore_self or self.user_id is None:
            return False
        if kind in _CHANNEL_MESSAGE_EVENTS:
            return payload.get("userId") == self.user_id
        if kind in _DM_MESSAGE_EVENTS:
            if payload.get("userId") == self.user_id:
                return True
            return payload.get("type") not in (None, "user")
        return False

    def _handle_ready(self, payload):
        self.raw_ready = payload
        user = payload.get("user")
        if isinstance(user, dict) and user.get("id"):
            self._me = User(user)
            self.user = self._me
            self._cache_user(self._me)
            log.info("ready: %s (id=%s)", self._me.username, self._me.id)
        self.voice_seats = self._scan_voice_states(payload)
        self.ready.set()

    def _scan_voice_states(self, payload):
        seats = set()
        states = payload.get("voiceStates")
        if isinstance(states, list):
            for state in states:
                if (isinstance(state, dict) and state.get("userId") == self.user_id
                        and state.get("channelId")):
                    seats.add(state["channelId"])
        elif isinstance(states, dict):
            for channel_id, members in states.items():
                if isinstance(members, list) and any(
                    isinstance(m, dict) and m.get("userId") == self.user_id
                    for m in members
                ):
                    seats.add(channel_id)
        return seats

    def _handle_voice_state(self, payload):
        channel_id = payload.get("channelId")
        if not channel_id or payload.get("userId") != self.user_id:
            return
        if payload.get("action") == "join":
            self.voice_seats.add(channel_id)
        elif payload.get("action") == "leave":
            self.voice_seats.discard(channel_id)

    def _cache_user(self, user):
        if user and user.id:
            self._users[user.id] = user

    def _resolve_user(self, user_id, data):
        if isinstance(data, dict):
            user = User(data)
            self._cache_user(user)
            return user
        return self._users.get(user_id) or User({"id": user_id})

    # ----------------------------------------------------------- slash commands

    def slash_command(self, name=None, description=None, options=None):
        """Register a slash command.

        Options are inferred from the signature (str/int/float/bool, no default
        = required), or given explicitly via options=[backspy.Option(...)].
        The first callback argument is always the Interaction.
        """

        def decorator(func):
            command = self._build_command(func, name, description, options)
            self._slash_commands[command.name] = command
            return command

        return decorator

    def command(self, name=None, description=None, options=None):
        """Alias for slash_command."""
        return self.slash_command(name, description, options)

    def add_command(self, command):
        if len(self._slash_commands) >= 100:
            raise ValueError("a bot can have at most 100 commands")
        self._slash_commands[command.name] = command
        return command

    def _build_command(self, func, name, description, options):
        command_name = name or func.__name__
        if description is None:
            doc = (inspect.getdoc(func) or "").strip()
            description = doc.splitlines()[0].strip() if doc else f"Command {command_name}"
        if options is None:
            opts = infer_options(func)
        elif isinstance(options, Option):
            opts = [options]
        else:
            opts = list(options)
        command = Command(command_name, description, opts, callback=func)
        self._warn_if_ctx_missing(func, command_name)
        return command

    def _warn_if_ctx_missing(self, func, command_name):
        try:
            parameters = list(inspect.signature(func).parameters.values())
        except (TypeError, ValueError):
            return
        if not parameters:
            log.warning("command /%s: the first parameter must be ctx (Interaction)",
                        command_name)
            return
        first = parameters[0]
        annotation_ok = first.annotation is Interaction or (
            isinstance(first.annotation, str) and first.annotation.endswith("Interaction"))
        if first.name not in _CTX_NAMES and not annotation_ok:
            log.warning("command /%s: the first parameter is the Interaction, not an option",
                        command_name)

    async def push_commands(self):
        """PUT all local slash commands to the instance (idempotent, safe to repeat)."""
        if not self._slash_commands:
            return
        payload = [command.to_payload() for command in self._slash_commands.values()]
        try:
            await self.http.register_commands(payload)
            log.info("registered %d slash command(s)", len(payload))
        except BackspaceError as exc:
            log.error("slash command registration failed: %s", exc)

    async def set_commands(self, commands):
        """Replace the whole command list on the server (PUT)."""
        payload = [c.to_payload() if isinstance(c, Command) else c for c in commands]
        return await self.http.register_commands(payload)

    async def fetch_registered_commands(self):
        data = await self.http.fetch_commands()
        return data.get("commands", []) if isinstance(data, dict) else []

    async def _run_slash_command(self, interaction):
        command = self._slash_commands.get(interaction.command)
        if command is None or command.callback is None:
            return
        func = command.callback
        parameters = list(inspect.signature(func).parameters.values())
        if not parameters:
            return
        args = [interaction]
        kwargs = {}
        for parameter in parameters[1:]:
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                break
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                kwargs[parameter.name] = dict(interaction.options)
                continue
            if parameter.name in interaction.options:
                value = coerce_value(parameter.annotation, interaction.options[parameter.name])
            elif parameter.default is not inspect.Parameter.empty:
                value = parameter.default
            else:
                value = None
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
                kwargs[parameter.name] = value
            else:
                args.append(value)
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            await result

    # -------------------------------------------------------------- REST helpers

    async def fetch_me(self):
        return User(await self.http.fetch_me())

    async def send(self, channel_id, content=None, *, attachments=None, reply_to=None):
        """Send a message to a space channel. Returns the created Message."""
        if content is None and not attachments:
            raise ValueError("send() needs content or attachments")
        data = await self.http.send_channel_message(
            channel_id, content=content, attachments=attachments, reply_to=reply_to)
        return Message(data, self) if isinstance(data, dict) else None

    async def send_dm(self, dm_channel_id, content=None, *, attachments=None, reply_to=None):
        data = await self.http.send_dm_message(
            dm_channel_id, content=content, attachments=attachments, reply_to=reply_to)
        return Message(data, self, dm=True) if isinstance(data, dict) else None

    async def open_dm(self, user_id):
        """Open a 1-on-1 DM with a user; returns the DM channel id."""
        data = await self.http.open_dm(user_id)
        if isinstance(data, dict):
            return data.get("id") or data.get("dmChannelId") or data.get("channelId")
        return None

    async def history(self, *, channel_id=None, dm_channel_id=None, limit=50, before=None):
        """Message history (for catching up after a reconnect)."""
        if bool(channel_id) == bool(dm_channel_id):
            raise ValueError("exactly one of channel_id / dm_channel_id is required")
        if channel_id:
            data = await self.http.fetch_channel_messages(channel_id, before=before, limit=limit)
        else:
            data = await self.http.fetch_dm_messages(dm_channel_id, before=before, limit=limit)
        items = data.get("messages") if isinstance(data, dict) else data
        return [Message(item, self, dm=bool(dm_channel_id)) for item in items or []]

    async def add_reaction(self, message_id, emoji):
        return Object(await self.http.add_reaction(message_id, emoji))

    async def remove_reaction(self, message_id, emoji):
        return Object(await self.http.remove_reaction(message_id, emoji))

    async def join_space(self, invite_code):
        return Object(await self.http.join_space(invite_code))

    async def upload(self, source, *, filename=None, content_type=None):
        """Upload a file (path or bytes) through tus; returns an Attachment."""
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
            name = filename or "upload"
        elif isinstance(source, (str, os.PathLike)):
            path = Path(os.fspath(source))
            data = await asyncio.to_thread(path.read_bytes)
            name = filename or path.name
        else:
            raise TypeError("source must be bytes or a path to a file")
        if content_type is None:
            content_type = guess_type(name)[0] or "application/octet-stream"
        file_id = await self.http.tus_upload(data, filename=name, content_type=content_type)
        return Attachment(file_id, filename=name, content_type=content_type, size=len(data))

    # --------------------------------------------------------------- ws actions

    async def trigger_typing(self, channel_id):
        """Show the typing indicator in a channel."""
        await self._gateway.send({"type": "typing_start", "channelId": channel_id})

    async def trigger_dm_typing(self, dm_channel_id):
        await self._gateway.send({"type": "dm_typing_start", "dmChannelId": dm_channel_id})

    async def join_voice(self, channel_id):
        """Take a voice seat (several channels at once are allowed, up to 25)."""
        await self._gateway.send({"type": "bot_voice_join", "channelId": channel_id})

    async def leave_voice(self, channel_id):
        await self._gateway.send({"type": "bot_voice_leave", "channelId": channel_id})

    async def voice_token(self, channel_id):
        """LiveKit {token, url} for a channel the bot sits in. Audio is your code."""
        return Object(await self.http.livekit_token(channel_id))

    def __repr__(self):
        return f"<backspy.Client {self.base_url} user={self.user!r}>"