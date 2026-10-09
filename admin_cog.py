import discord
from discord import Interaction, app_commands
from discord.ext import commands

from config import guild_ids


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
            print(f"Synced {len(synced)} commands to guild {guild_id}")
        client.tree.clear_commands(guild=None)
        await client.tree.sync()
        print("Cleared global slash commands")
        return synced_count

    synced = await client.tree.sync()
    print(f"Synced {len(synced)} commands globally")
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

    @app_commands.command(name="sync", description="Manually sync slash commands to guilds")
    async def sync(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced_count = await sync_app_commands(self.client)
            await interaction.followup.send(
                f"Synced {synced_count} commands. Yorokobe.", ephemeral=True
            )
        except (discord.errors.Forbidden, discord.HTTPException) as e:
            print(f"Error syncing: {e}")
            await interaction.followup.send(
                "Error syncing commands - check permissions", ephemeral=True
            )



async def setup(bot):
    await bot.add_cog(admin_cog(bot))
