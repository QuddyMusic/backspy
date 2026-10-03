# examples/echo.py
"""Echo bot: repeats the text of a mention, or the !echo command."""
import backspy

bot = backspy.Client()


@bot.on("ready")
async def ready(_):
    print(f"@{bot.user.username} online (id={bot.user_id})")


@bot.on("message_created")
async def on_message(msg):
    text = msg.content.replace(f"<@{bot.user_id}>", "").strip()
    if bot.user_id in msg.mentions or text == "!echo":
        await msg.reply(text or "...")


bot.run()