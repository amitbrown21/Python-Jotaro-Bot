import asyncio
import sys

import discord
from discord.ext import commands

from admin_cog import admin_cog
from config import TOKEN, guild_ids
from dnd_cog import dnd_cog
from music_cog import music_cog

intents = discord.Intents.default()
intents.voice_states = True

client = commands.Bot(command_prefix=(), intents=intents, help_command=None)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@client.event
async def on_ready():
    print(f"Logged in as {client.user.name} ({client.user.id})")
    await client.change_presence(status=discord.Status.do_not_disturb)

    for name, cog in (
        ('music_cog', music_cog),
        ('admin_cog', admin_cog),
        ('dnd_cog', dnd_cog),
    ):
        try:
            await client.add_cog(cog(client))
            print(f"Loaded cog: {name}")
        except Exception as e:
            print(f"Failed to load {name}: {e}")

    try:
        if guild_ids:
            for guild_id in guild_ids:
                await client.tree.sync(guild=discord.Object(id=guild_id))
            print(f"Synced commands to {len(guild_ids)} guilds")
        else:
            await client.tree.sync()
            print("Synced commands globally")
    except discord.errors.Forbidden as e:
        print(f"Error syncing commands: {e}")

    print('Yare Yare Daze...')
    print("----------------------------------------")


if __name__ == "__main__":
    client.run(TOKEN)
