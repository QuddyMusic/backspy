# examples/voice.py
"""Voice seat skeleton: join and leave by command.

This only holds the seat. To play audio, take the LiveKit token
(await bot.voice_token(channel_id)) and publish a track with a LiveKit client.
"""
import backspy

bot = backspy.Client()


@bot.on("message_created")
async def voice(msg):
    if msg.content == "!join":
        await bot.join_voice(msg.channel_id)
        await msg.reply("seated")
    elif msg.content == "!leave":
        await bot.leave_voice(msg.channel_id)
        await msg.reply("left")


bot.run()