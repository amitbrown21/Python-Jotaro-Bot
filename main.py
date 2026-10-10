import asyncio
import logging
import os
import sys

from log_setup import setup_logging

setup_logging()

import discord
from discord.ext import commands

from admin_cog import admin_cog, sync_app_commands
from config import TOKEN
from dnd_cog import dnd_cog
from music_cog import music_cog
from ytdlp_check import check_ytdlp

log = logging.getLogger("jotaro.main")

# Override via BUILD_ID / GIT_COMMIT env so TrueNAS logs prove which image is running.
MUSIC_BUILD = os.environ.get("BUILD_ID") or os.environ.get("GIT_COMMIT") or "2026-10-08-queue-tools"

intents = discord.Intents.default()
intents.voice_states = True

client = commands.Bot(command_prefix=(), intents=intents, help_command=None)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@client.event
async def on_ready():
    log.info("Logged in as %s (%s)", client.user.name, client.user.id)
    log.info("MUSIC_BUILD=%s", MUSIC_BUILD)
    await asyncio.to_thread(check_ytdlp)
    await client.change_presence(status=discord.Status.do_not_disturb)

    for name, cog in (
        ('music_cog', music_cog),
        ('admin_cog', admin_cog),
        ('dnd_cog', dnd_cog),
    ):
        try:
            await client.add_cog(cog(client))
            log.info("Loaded cog: %s", name)
        except Exception as e:
            log.error("Failed to load %s: %s", name, e)

    try:
        await sync_app_commands(client)
    except (discord.errors.Forbidden, discord.HTTPException) as e:
        log.error("Error syncing commands: %s", e)

    log.info('Yare Yare Daze...')
    log.info("----------------------------------------")


if __name__ == "__main__":
    # setup_logging() owns handlers; discord.py would otherwise add a second stream handler.
    client.run(TOKEN, log_handler=None)
