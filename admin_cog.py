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

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Check if the bot is the only member in the voice channel
        if before.channel and len(before.channel.members) == 1:
            # If the bot is alone, disconnect from the voice channel
            voice_client = discord.utils.get(self.client.voice_clients, guild=before.channel.guild)
            if voice_client and voice_client.channel:
                await voice_client.disconnect()
                await self.client.change_presence(status=discord.Status.do_not_disturb)
                # Automatically find the general channel and send a message
                general_channel = self.find_general_channel(before.channel.guild)
                if general_channel:
                    await general_channel.send("I'm alone here, leaving the voice channel.")
                else:
                    print("General channel not found.")
            else:
                print("I'm not connected to a voice channel.")

    def find_general_channel(self,guild):
        """Finds the general channel in the given guild."""
        return discord.utils.get(guild.text_channels, name="general")


async def setup(bot):
    await bot.add_cog(admin_cog(bot))
