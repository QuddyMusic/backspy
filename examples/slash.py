# examples/slash.py
"""Slash commands: /roll and /say."""
import random

import backspy

bot = backspy.Client()


@bot.slash_command("roll", "Roll a die", options=[
    backspy.Option("sides", "number of sides", type=backspy.INTEGER),
])
async def roll(ctx, sides: int = 6):
    await ctx.respond(f"{ctx.user.mention} rolled {random.randint(1, sides)}")


@bot.slash_command("say", "Repeat text")
async def say(ctx, text: str):
    await ctx.respond(text)


bot.run()