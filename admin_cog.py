import discord
from discord import Interaction
from discord import app_commands
from discord.ext import commands


class admin_cog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name="ping", description="Jotaro will pong you")
    async def ping(self, interaction: Interaction):
        member = interaction.user.mention
        await interaction.response.send_message("Pong @" + member)

    @app_commands.command(name="hello", description="Jotaro will respond to hello message")
    async def hello(self, interaction: Interaction):
        name = interaction.user.mention
        await interaction.response.send_message("Yare Yare Daze...nanda " + name)




async def setup(bot):
    await bot.add_cog(admin_cog(bot))
