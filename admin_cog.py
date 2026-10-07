import discord
from discord import Interaction, app_commands
from discord.ext import commands

from config import guild_ids


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
            if guild_ids:
                synced_count = 0
                for guild_id in guild_ids:
                    synced = await self.client.tree.sync(guild=discord.Object(id=guild_id))
                    synced_count += len(synced)
            else:
                synced = await self.client.tree.sync()
                synced_count = len(synced)
            await interaction.followup.send(
                f"Synced {synced_count} commands. Yorokobe.", ephemeral=True
            )
        except discord.errors.Forbidden as e:
            print(f"Error syncing: {e}")
            await interaction.followup.send(
                "Error syncing commands - check permissions", ephemeral=True
            )



async def setup(bot):
    await bot.add_cog(admin_cog(bot))
