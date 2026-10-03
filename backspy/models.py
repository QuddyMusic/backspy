"""Data models. Known payloads get real classes; the rest stays an Object."""

from .utils import parse_mentions


class Object:
    """Dot-friendly read-only view of a raw JSON object; missing keys -> None."""

    def __init__(self, data=None):
        self._data = dict(data or {})

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            value = self._data[name]
        except KeyError:
            return None
        return Object(value) if isinstance(value, dict) else value

    def __getitem__(self, key):
        if key in self._data:
            value = self._data[key]
            return Object(value) if isinstance(value, dict) else value
        return None

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def to_dict(self):
        return dict(self._data)

    def __repr__(self):
        return f"Object({self._data!r})"


class User:
    __slots__ = ("id", "username", "display_name", "avatar", "avatar_color", "is_bot", "raw")

    def __init__(self, data=None):
        data = data or {}
        self.id = data.get("id")
        self.username = data.get("username")
        self.display_name = data.get("displayName") or data.get("display_name")
        self.avatar = data.get("avatar")
        self.avatar_color = data.get("avatarColor") or data.get("avatar_color")
        self.is_bot = bool(data.get("isBot") or data.get("is_bot"))
        self.raw = data

    @property
    def mention(self):
        return f"<@{self.id}>" if self.id else None

    def __str__(self):
        return self.username or self.display_name or str(self.id)

    def __repr__(self):
        return f"<User {self} id={self.id}>"


class Attachment:
    """An uploaded file (from Client.upload) usable in send(attachments=...)."""

    __slots__ = ("id", "filename", "content_type", "size")

    def __init__(self, id, *, filename=None, content_type=None, size=None):
        self.id = id
        self.filename = filename
        self.content_type = content_type
        self.size = size

    def __repr__(self):
        return f"<Attachment {self.id} filename={self.filename!r} size={self.size}>"


class Message:
    """A message in a space channel or a DM."""

    __slots__ = (
        "id", "content", "type", "user_id", "channel_id", "dm_channel_id", "space_id",
        "created_at", "updated_at", "reply_to", "attachments", "author", "is_dm",
        "raw", "_client",
    )

    def __init__(self, data, client=None, *, dm=False):
        data = data or {}
        self._client = client
        self.raw = data
        self.id = data.get("id")
        self.content = data.get("content") or ""
        self.type = data.get("type")
        self.user_id = data.get("userId")
        self.channel_id = data.get("channelId")
        self.dm_channel_id = data.get("dmChannelId")
        if not dm and self.channel_id is None and self.dm_channel_id is not None:
            dm = True
        self.is_dm = dm
        self.space_id = data.get("spaceId")
        self.created_at = data.get("createdAt")
        self.updated_at = data.get("updatedAt") or data.get("editedAt")
        reply = data.get("replyTo")
        self.reply_to = Object(reply) if isinstance(reply, dict) else reply
        self.attachments = data.get("attachments") or []
        author_data = data.get("user") or data.get("author")
        if client is not None:
            self.author = client._resolve_user(self.user_id, author_data)
        elif isinstance(author_data, dict):
            self.author = User(author_data)
        else:
            self.author = User({"id": self.user_id})

    @property
    def mentions(self):
        """User ids mentioned in this message (code spans skipped)."""
        return parse_mentions(self.content)

    def mentions_user(self, user_id):
        return user_id in self.mentions

    async def reply(self, content=None, *, attachments=None):
        """Answer this message (replyToId is set)."""
        if self._client is None:
            raise RuntimeError("message is not bound to a client")
        if content is None and not attachments:
            raise ValueError("reply() needs content or attachments")
        http = self._client.http
        if self.is_dm:
            data = await http.send_dm_message(
                self.dm_channel_id, content=content, attachments=attachments, reply_to=self.id)
            return Message(data, self._client, dm=True) if isinstance(data, dict) else None
        data = await http.send_channel_message(
            self.channel_id, content=content, attachments=attachments, reply_to=self.id)
        return Message(data, self._client) if isinstance(data, dict) else None

    async def edit(self, content=None, *, attachments=None):
        if self._client is None:
            raise RuntimeError("message is not bound to a client")
        http = self._client.http
        if self.is_dm:
            data = await http.edit_dm_message(self.id, content=content, attachments=attachments)
        else:
            data = await http.edit_channel_message(self.id, content=content, attachments=attachments)
        if isinstance(data, dict) and data.get("id"):
            return Message(data, self._client, dm=self.is_dm)
        return None

    async def delete(self):
        if self._client is None:
            raise RuntimeError("message is not bound to a client")
        if self.is_dm:
            await self._client.http.delete_dm_message(self.id)
        else:
            await self._client.http.delete_channel_message(self.id)

    async def react(self, emoji):
        """Add a reaction (idempotent)."""
        return Object(await self._client.http.add_reaction(self.id, emoji))

    async def unreact(self, emoji):
        """Remove own reaction."""
        return Object(await self._client.http.remove_reaction(self.id, emoji))

    def __repr__(self):
        where = self.dm_channel_id if self.is_dm else self.channel_id
        return f"<Message id={self.id} user={self.user_id} where={where}>"


class Interaction:
    """A slash-command invocation handed to the bot."""

    __slots__ = ("id", "command", "options", "user", "channel_id", "dm_channel_id",
                 "space_id", "expires_at", "raw", "_client")

    def __init__(self, data, client=None):
        data = data or {}
        self._client = client
        self.raw = data
        self.id = data.get("id")
        self.command = data.get("command")
        self.options = dict(data.get("options") or {})
        self.user = User(data.get("user") or {})
        self.channel_id = data.get("channelId")
        self.dm_channel_id = data.get("dmChannelId")
        self.space_id = data.get("spaceId")
        self.expires_at = data.get("expiresAt")

    @property
    def is_dm(self):
        return self.dm_channel_id is not None

    async def respond(self, content=None, *, attachments=None):
        """Answer the interaction (up to 5 responses within 15 minutes)."""
        if self._client is None:
            raise RuntimeError("interaction is not bound to a client")
        data = await self._client.http.respond_interaction(
            self.id, content=content, attachments=attachments)
        if isinstance(data, dict) and data.get("id"):
            return Message(data, self._client, dm=self.is_dm)
        return None

    def __repr__(self):
        return f"<Interaction /{self.command} id={self.id} user={self.user}>"


class ReactionEvent:
    """reaction_added / reaction_removed."""

    __slots__ = ("raw", "message_id", "emoji", "user_id", "channel_id", "dm_channel_id", "user")

    def __init__(self, data, client=None):
        data = data or {}
        self.raw = data
        self.message_id = data.get("messageId") or data.get("id")
        self.emoji = data.get("emoji")
        self.user_id = data.get("userId")
        self.channel_id = data.get("channelId")
        self.dm_channel_id = data.get("dmChannelId")
        self.user = client.get_user(self.user_id) if client else None

    def __repr__(self):
        return f"<ReactionEvent {self.emoji!r} on {self.message_id} by {self.user_id}>"


class TypingEvent:
    """A typing indicator event."""

    __slots__ = ("raw", "channel_id", "dm_channel_id", "user_id", "user")

    def __init__(self, data, client=None):
        data = data or {}
        self.raw = data
        self.channel_id = data.get("channelId")
        self.dm_channel_id = data.get("dmChannelId")
        self.user_id = data.get("userId")
        self.user = client.get_user(self.user_id) if client else None

    def __repr__(self):
        return f"<TypingEvent user={self.user_id} where={self.dm_channel_id or self.channel_id}>"