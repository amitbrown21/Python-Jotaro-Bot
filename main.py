import random

import discord
from discord.ext import commands

from admin_cog import admin_cog
from config import TOKEN, prefix, main_channel_id
from music_cog import music_cog

intents = discord.Intents.all()
intents.message_content = True

client = commands.Bot(command_prefix=prefix, intents=intents)


@client.command(pass_context=True)
async def prefix(ctx, new: str):
    prefix1 = new
    client.command_prefix = new
    await ctx.send("Prefix is {" + prefix1 + '}, Yorokobe')


@client.tree.command(name="coinflip", description="a coin flip game , pick heads or tails")
async def coinflip(interaction: discord.Interaction, answer: str):
    if random.choice(['heads', 'tails']) == answer:
        await interaction.response.send_message("YES YES YES YES YES YES")

    else:
        await interaction.response.send_message("NO NO NO NO NO NO")


@client.command(pass_context=True)
async def sync(ctx):
    synced = await client.tree.sync(guild=discord.Object(id=main_channel_id))
    print(len(synced))


@client.event
async def on_ready():
    try:
        await client.tree.sync(guild=discord.Object(id=main_channel_id))
        print(f'Synced')
    except Exception as e:
        print(e)
    await client.change_presence(status=discord.Status.do_not_disturb)
    await client.add_cog(music_cog(client))
    await client.add_cog(admin_cog(client))
    client.tree.copy_global_to(guild=discord.Object(id=main_channel_id))
    print('Yare Yare Daze...')
    print("----------------------------------------")


client.run(TOKEN)
