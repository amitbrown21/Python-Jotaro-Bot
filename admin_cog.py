import logging

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from config import guild_ids

log = logging.getLogger(__name__)

# (command, short description). Keep in sync with the cogs; test_offline.py checks it.
HELP_SECTIONS = {
    "Music": [
        ("play", "Play a song by search or URL"),
        ("pause / resume", "Pause or resume audio"),
        ("skip / stop / leave", "Skip, stop and disconnect, or just leave"),
        ("queue / nowplaying", "Show the queue or current song"),
        ("shuffle / loop / remove / remove_last / clear_queue", "Manage the queue"),
        ("seek", "Jump to a time in the current song"),
        ("volume / effect", "Set volume (0-100) or bass/nightcore"),
        ("lyrics / history", "Lyrics, or recently played songs"),
        ("playlist save / play / list / delete", "Saved playlists"),
        ("restore_queue", "Bring back the queue from before a restart"),
        ("localplay", "Play a local mp3"),
    ],
    "D&D": [
        ("roll", "Roll dice, e.g. 2d6 1d20"),
        ("generate_stats", "Generate character stats"),
        ("generate_character", "Random character"),
        ("initiative", "Roll initiative"),
        ("coinflip", "Flip a coin"),
        ("weather", "Random weather"),
        ("loot", "Random loot"),
    ],
    "Admin": [
        ("help", "Show this list"),
        ("ping / hello", "Check that Jotaro is awake"),
        ("sync", "Re-sync slash commands"),
    ],
}


def help_command_names() -> set:
    """Every slash command (groups expanded) named in HELP_SECTIONS."""
    names = set()
    for entries in HELP_SECTIONS.values():
        for label, _ in entries:
            if label.startswith("playlist "):
                names.update(f"playlist {sub.strip()}" for sub in label[9:].split("/"))
            else:
                names.update(part.strip() for part in label.split("/"))
    return names


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(title="Jotaro commands", color=0x3498db)
    for section, entries in HELP_SECTIONS.items():
        embed.add_field(
            name=section,
            value="\n".join(f"`/{label}` - {desc}" for label, desc in entries),
            inline=False,
        )
    return embed


async def sync_app_commands(client) -> int:
    """Publish slash commands without leaving a second global copy.

    With GUILD_IDS set, commands are copied onto those servers. Commands that
    were also published globally show up twice in the picker, so the global
    list is cleared after the guild sync.
    """
    if guild_ids:
        synced_count = 0
        for guild_id in guild_ids:
            guild = discord.Object(id=guild_id)
            client.tree.copy_global_to(guild=guild)
            synced = await client.tree.sync(guild=guild)
            synced_count += len(synced)
            log.info("Synced %d commands to guild %s", len(synced), guild_id)
        client.tree.clear_commands(guild=None)
        await client.tree.sync()
        log.info("Cleared global slash commands")
        return synced_count

    synced = await client.tree.sync()
    log.info("Synced %d commands globally", len(synced))
    return len(synced)


class admin_cog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name="ping", description="Jotaro will pong you")
    async def ping(self, interaction: Interaction):
        await interaction.response.send_message(f"Pong @{interaction.user.mention}")

    @app_commands.command(name="hello", description="Jotaro will respond to hello message")
    async def hello(self, interaction: Interaction):
        await interaction.response.send_message(
            f"Yare Yare Daze... nanda {interaction.user.mention}"
        )

    @app_commands.command(name="help", description="List Jotaro's commands")
    async def help(self, interaction: Interaction):
        await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)

    @app_commands.command(name="sync", description="Manually sync slash commands to guilds")
    async def sync(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced_count = await sync_app_commands(self.client)
            await interaction.followup.send(
                f"Synced {synced_count} commands. Yorokobe.", ephemeral=True
            )
        except (discord.errors.Forbidden, discord.HTTPException) as e:
            log.error("Error syncing: %s", e)
            await interaction.followup.send(
                "Error syncing commands - check permissions", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(admin_cog(bot))
