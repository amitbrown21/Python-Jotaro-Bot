import discord
import music_cog
import dnd_cog
import admin_cog
import config
from discord.ext import commands

from admin_cog import admin_cog
from config import TOKEN, prefix, guild_ids
from dnd_cog import dnd_cog
from music_cog import music_cog

intents = discord.Intents.all()
intents.message_content = True

client = commands.Bot(command_prefix=prefix, intents=intents)


@client.command(pass_context=True)
async def set_prefix(ctx, new_prefix: str):
    client.command_prefix = new_prefix
    await ctx.send(f"Prefix is set to `{new_prefix}`, Yorokobe")


@client.command(pass_context=True)
async def sync(ctx):
    try:
        for guild_id in guild_ids:
            synced = await client.tree.sync(guild=discord.Object(id=guild_id))
        print(len(synced))
    except discord.errors.Forbidden as e:
        print(f"Error syncing: {e}")


@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.do_not_disturb)
    await client.add_cog(music_cog(client))
    await client.add_cog(admin_cog(client))
    await client.add_cog(dnd_cog(client))
    try:
        for guild_id in guild_ids:
            await client.tree.sync(guild=discord.Object(id=guild_id))
        print('Synced')
    except discord.errors.Forbidden as e:
        print(f"Error syncing: {e}")
    await client.tree.sync()
    for guild_id in guild_ids:
        client.tree.copy_global_to(guild=discord.Object(id=guild_id))

    print('Yare Yare Daze...')
    print("----------------------------------------")


client.run(TOKEN)
