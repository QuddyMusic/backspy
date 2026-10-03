# backspy

Python-библиотека для ботов Backspace. Асинхронная, на aiohttp, в стиле discord.py:
события декораторами, slash-команды обычными функциями, рейт-лимиты,
переподключение и разбор упоминаний — внутри.

Полная документация — в [README.md](README.md) (English). Здесь — короткая версия.

## Установка

    pip install aiohttp
    pip install git+https://github.com/QuddyMusic/backspy.git

    export BACKSPACE_URL=https://your-instance
    export BOT_TOKEN=your-bot-token

## Быстрый старт

```python
import backspy

bot = backspy.Client()

@bot.on("message_created")
async def on_message(msg):
    if bot.user_id in msg.mentions:
        await msg.reply("я тут")

bot.run()
```

## Кратко об остальном

- **События**: `bot.on("message_created")` и т.д. Свои сообщения фильтруются,
  чтобы бот не отвечал сам себе. `bot.wait_for(...)` ждёт конкретное событие.
- **Сообщения**: `bot.send()`, `msg.reply()`, `msg.edit()`, `msg.delete()`,
  `msg.react()`, `bot.history()`, `bot.trigger_typing()`.
- **Slash-команды**: `@bot.slash_command("roll", "Roll a die")` — опции выводятся
  из сигнатуры, ответ — `ctx.respond()`. Регистрируются на старте автоматически.
- **Файлы**: `att = await bot.upload("cat.png")`, дальше `attachments=[att]`.
- **DM**: `dm_id = await bot.open_dm(user_id)`.
- **Голос**: `bot.join_voice(channel_id)` и `bot.voice_token(channel_id)` — сам
  звук публикуется livekit-клиентом.
- **Рейт-лимиты** (5 сообщений/5 с, 10 реакций/5 с и т.д.) и 429 — внутри.
- **Ошибки**: `backspy.HTTPException` с `.status` / `.code`, `LoginFailure` и др.
- **Управление ботами**: `backspy.OwnerClient(JWT)` — создание, токены, спейсы.

Подробности, таблицы и ограничения — в [README.md](README.md).