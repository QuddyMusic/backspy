"""Smoke test against a live instance.

Usage:
    BACKSPACE_URL=https://your-instance BOT_TOKEN=... python scripts/smoke.py <channel_id>
"""
import asyncio
import sys

import backspy


async def main(channel_id: str) -> None:
    bot = backspy.Client()

    me = await bot.login()
    print("[1/7] login ok:", me.username, me.id)

    msg = await bot.send(channel_id, "backspy smoke test")
    print("[2/7] send ok:", msg.id)

    await msg.edit("backspy smoke test (edited)")
    print("[3/7] edit ok")

    await msg.react("ok")
    print("[4/7] react ok")

    history = await bot.history(channel_id=channel_id, limit=5)
    print("[5/7] history ok:", len(history), "messages")

    await msg.delete()
    print("[6/7] delete ok")

    @bot.on("ready")
    async def ready(_):
        print("[7/7] websocket ok, voice seats:", bot.voice_seats)
        await bot.close()

    await bot.start()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke.py <channel_id>")
    asyncio.run(main(sys.argv[1]))