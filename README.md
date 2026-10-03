# backspy

Python library for writing Backspace bots. Asynchronous, built on aiohttp, with an
API modeled after discord.py: event handlers via decorators, slash commands defined
as plain functions, rate limiting, reconnection and mention parsing handled
internally.

Written against `docs/systems/bots.md` from the Bot API pull request to Backspace
(QuddyMusic/backspace, branch `bots-api`), which is not part of upstream Backspace
yet. The sections below list what is covered.

Python 3.10+, single dependency: aiohttp. [Документация на русском](README.ru.md)

## Installation

    pip install aiohttp

or from the repository:

    pip install git+https://github.com/QuddyMusic/backspy.git

Two environment variables are picked up automatically:

    export BACKSPACE_URL=https://your-instance
    export BOT_TOKEN=your-bot-token

## Quick start

```python
import backspy

bot = backspy.Client()

@bot.on("ready")
async def ready(_):
    print("online as", bot.user.username)

@bot.on("message_created")
async def on_message(msg):
    if bot.user_id in msg.mentions:
        await msg.reply("present")

bot.run()
```

`bot.run()` blocks and handles Ctrl+C. Each handler runs in its own task, so an
exception in one handler is logged instead of taking the bot down. To see what
the bot is doing:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

## Events

After authentication the bot receives every event a chat member receives.
Subscribe with `@bot.on("event_name")` — as many handlers per event as you like —
or with `@bot.event` on functions named `on_<event_name>`.

| Event | Handler receives |
|---|---|
| `message_created`, `message_updated`, `message_deleted` | `Message` |
| `dm_message_created`, `dm_message_updated`, `dm_message_deleted` | `Message` (`is_dm=True`) |
| `interaction_created` | `Interaction` |
| `reaction_added`, `reaction_removed` | `ReactionEvent` |
| `typing` | `TypingEvent` |
| `ready` | `Object` (the full payload) |
| `error` (e.g. a refused `bot_voice_join`) | `Object` |
| anything else (`member_joined`, `dm_channel_created`, ...) | `Object` |

`Object` is a dot-access view over raw JSON: `event.user.id` works, a missing key
yields `None`. Events not in the table still arrive, so nothing is lost if a name
differs slightly from this list.

Worth knowing:

- The bot's own messages are filtered out by default (otherwise it would answer
  itself); opt out with `Client(ignore_self=False)`.
- `bot.wait_for("message_created", check=..., timeout=60)` waits for one specific
  event.
- Reconnection is automatic with growing backoff. The API has no resume: after a
  reconnect `ready` arrives again, and missed messages are read back with
  `bot.history()`.

## Messages

```python
msg = await bot.send(channel_id, "hello")
await bot.send_dm(dm_id, "hello")

await msg.reply("answer")        # sets replyToId
await msg.edit("edited")
await msg.delete()
await msg.react("👋")             # idempotent
await bot.add_reaction(msg.id, "👋")

await bot.trigger_typing(channel_id)
msgs = await bot.history(channel_id=..., limit=50, before=...)
```

`Message` fields: `id`, `content`, `author`, `user_id`, `channel_id` /
`dm_channel_id`, `is_dm`, `reply_to` (the quoted text), `attachments`,
`created_at`, `raw` (the original payload).

`msg.mentions` is the list of user ids in `<@...>` tokens; mentions inside code
spans and fenced blocks don't count, matching the clients.

## Slash commands

Options are inferred from the function signature:

```python
import random

@bot.slash_command("roll", "Roll a die")
async def roll(ctx: backspy.Interaction, sides: int = 6):
    await ctx.respond(f"{ctx.user.mention} rolled {random.randint(1, sides)}")
```

`str` / `int` / `float` / `bool` map to `string` / `integer` / `number` /
`boolean`. A parameter without a default is required, with a default — optional.
The first argument is always `ctx`. The description falls back to the first line
of the docstring.

Explicit options, for choices or ordering:

```python
@bot.slash_command("gif", "Find a gif", options=[
    backspy.Option("query", "what to search for", required=True),
    backspy.Option("count", "how many", type=backspy.INTEGER,
                   choices=[("one", 1), ("three", 3), ("five", 5)]),
])
async def gif(ctx, query: str, count: int = 1):
    ...
```

The server's rules (names of `a-z 0-9 _ -`, at most 100 commands / 10 options /
25 choices, required options before optional ones, no boolean choices) are
validated locally with clear errors before anything is sent.

On startup the library registers the commands itself (`PUT
/api/bots/@me/commands` — a full-list replace, safe to repeat). Commands are
stored per instance: register on every instance the bot lives on — running the
same code with a different `BACKSPACE_URL` re-registers them there.

Answer with `ctx.respond()` — up to 5 responses while the interaction is alive
(15 minutes). Address the invoker with `<@user.id>`, e.g. `ctx.user.mention`.

## Files

```python
att = await bot.upload("cat.png")            # a path or bytes
await bot.send(channel_id, "here", attachments=[att])
```

Uploads go through tus on `/api/files/`; the resulting id is passed in
`attachments`. The content type is guessed from the filename, or pass it
explicitly.

## Direct messages

```python
dm_id = await bot.open_dm(user_id)
await bot.send_dm(dm_id, "hello")
```

## Voice

```python
await bot.join_voice(channel_id)             # a seat; several channels at once (up to 25)
info = await bot.voice_token(channel_id)     # info.token, info.url
await bot.leave_voice(channel_id)
```

`bot.voice_seats` is the set of channels the bot currently sits in (tracked from
`ready` and `voice_state_update`). The audio itself is your code: the library
hands out the LiveKit token and URL, you connect with a LiveKit client and
publish a track.

## Rate limits

Handled internally; a 429 is waited out and retried automatically:

| Action | Limit |
|---|---|
| messages: channels + DMs + interaction responses (one shared bucket) | 5 per 5 s |
| reactions | 10 per 5 s |
| command registration | 10 per 5 min |
| interaction invocation | 5 per 5 s |

The message bucket is shared — conservative, never above the server's limits.
Bot creation and token regeneration (5 per 15 min, per owner) are enforced by the
server; the library waits those out too.

## Errors

```python
try:
    await bot.send(channel_id, "hello")
except backspy.HTTPException as e:
    print(e.status, e.code)     # e.g. 403, bot_account_required
```

`e.payload`, `e.code` and `e.details` follow the project's error format.
`LoginFailure` — the token was rejected; `ConnectionClosed` — a websocket action
was attempted with no live connection; `RateLimited` — still limited after
retries.

## Managing bots (owner)

A separate class for the human owner's JWT, authorized with `Bearer`:

```python
async with backspy.OwnerClient(HUMAN_JWT) as owner:
    summary, token = await owner.create_bot("helper")
    print(token)                            # shown once, save it
    await owner.add_bot_to_space(summary.id, space_id)
    await owner.regenerate_token(bot_id)    # old tokens and sockets die
    await owner.remove_bot_from_space(bot_id, space_id)
```

A bot can't change its own displayName or avatar — only the owner can, via
`owner.edit_bot()`.

## Known caveats

- On a network error a POST is retried — a message can theoretically be
  duplicated (the request arrived, the response was lost). Rare, but worth
  knowing.
- Slash commands and interactions work only on the instance the bot is connected
  to.
- `isBot` isn't carried by the DM relay: on other instances the bot has no bot
  marker.

## Examples

- `examples/echo.py` — echo bot
- `examples/slash.py` — slash commands
- `examples/voice.py` — voice seat
- `scripts/smoke.py` — end-to-end check against a live instance