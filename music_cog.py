import asyncio
import traceback

import discord
import yt_dlp
from discord import FFmpegPCMAudio
from discord import app_commands
from discord.ext import commands
from youtubesearchpython import VideosSearch


class music_cog(commands.Cog):

    def __init__(self, client):
        self.client = client
        self.queues = {}
        self.yt_dl_opts = {
            'format': 'bestaudio',
            'postprocessors': [{'key': 'FFmpegExtractAudio'}]}
        self.ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                               'options': '-vn', }
        self.ytdl = yt_dlp.YoutubeDL(self.yt_dl_opts)
        self.is_skipping = False

    async def print_queue(self, interaction):
        if self.queues:
            # To display the queue
            queue_info = "\n".join(
                [f"{index}. [{song['title']}]({song['url']})" for index, song in
                 enumerate(self.queues.values(), start=1)]
            )
            embed = discord.Embed(title="Current Queue", description=queue_info, color=0x3498db)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("The queue is empty.")

    async def check_q(self):
        if len(self.queues) != 0:
            return True
        else:
            return False

    def after_play(self, interaction):
        if not self.is_skipping:
            asyncio.run_coroutine_threadsafe(self.play_q(interaction), self.client.loop)
        else:
            self.is_skipping = False

    async def play_q(self, interaction):
        try:
            if len(self.queues) != 0:
                voice = interaction.guild.voice_client
                if not voice:
                    return

                # Check if the queue is empty before popping
                if self.queues:
                    next_song_key = min(self.queues.keys())  # Get the key of the next song in the queue
                    song_info = self.queues.pop(next_song_key)
                    title = song_info['title']
                    audio = song_info['audio']
                    url = song_info['url']
                    thumbnail = song_info['thumbnail']

                    await self.client.change_presence(activity=discord.Game(name=title))
                    embed = discord.Embed(title=title, url=url, description="Now Playing...", color=0xffff00)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                    embed.set_thumbnail(url=thumbnail)
                    await interaction.followup.send(embed=embed)

                    voice.play(audio, after=lambda x=None: self.after_play(interaction))
                else:
                    await interaction.followup.send("Owari Da... no more songs in the queue")
                    return

        except Exception as e:
            print(f"An error occurred during play_q: {e}")
            await self.client.change_presence(status=discord.Status.do_not_disturb)
            traceback.print_exc()

    def search_yt(self, item):
        if item.startswith("https://"):
            title = self.ytdl.extract_info(item, download=False)["title"]
            return {'source': item, 'title': title}
        search = VideosSearch(item, limit=1)
        results = search.result()["result"]

        if not results:
            return None

        first_result = results[0]
        return {'source': first_result["link"], 'title': first_result["title"]}

    @app_commands.command(name="play", description="Play a song using YouTube search or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if interaction.user.voice:
            channel = interaction.user.voice.channel
            voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)

            if voice and voice.is_connected():
                # Bot is already in a voice channel
                pass
            else:
                # Bot is not in a voice channel, connect to the specified channel
                voice = await channel.connect()

            if not voice.is_playing():
                search_result = self.search_yt(query)
                if not search_result:
                    await interaction.response.send_message("No search results found.")
                    return
                await interaction.response.defer()
                await interaction.followup.send("Hold on, loading...")
                info = self.ytdl.extract_info(search_result['source'], download=False)
                thumbnail = info['thumbnail']
                url = info['url']
                # Sending the message that contains the song information
                embed = discord.Embed(title=search_result['title'], url=search_result['source'],
                                      description="Now Playing...", color=0xffff00)
                embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                embed.set_thumbnail(url=thumbnail)
                await interaction.followup.send(embed=embed)
                await self.client.change_presence(status=discord.Status.do_not_disturb,
                                                  activity=discord.Game(name=search_result['title']))
                # Where the song is played
                voice.is_playing()
                song = FFmpegPCMAudio(url, **self.ffmpeg_options)
                voice.play(song, after=lambda x=None: self.after_play(interaction))
                voice.is_playing()
            else:
                # here we add the song to the queue
                search_result = self.search_yt(query)
                if not search_result:
                    await interaction.response.send_message("No search results found.")
                    return
                await interaction.response.defer()
                await interaction.followup.send("Hold on, loading...")
                info = self.ytdl.extract_info(search_result['source'], download=False)
                url = info['url']
                thumbnail = info['thumbnail']
                song = FFmpegPCMAudio(url, **self.ffmpeg_options)
                embed = discord.Embed(title=search_result['title'], url=search_result['source'],
                                      description="Was added to queue", color=0xffff00)
                embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                embed.set_thumbnail(url=thumbnail)
                await interaction.followup.send(embed=embed)
                song_position = len(self.queues) + 1
                self.queues[song_position] = {'title': search_result['title'], 'audio': song,
                                              'url': search_result['source'], 'thumbnail': thumbnail}
                return
        else:
            await interaction.response.send_message("You are not in a voice channel.")
            return

    @app_commands.command(name="leave", description="Make Jotaro leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if interaction.guile.voice_client:
            await interaction.voice_client.disconnect()
            await interaction.response.send_message("Yare Yare... I'll be back")
        else:
            await interaction.response.send_message("TEME!!! I'M NOT IN A VOICE CHANNELLL!!!")

    @app_commands.command(name="pause", description="Pause Jotaro's audio")
    async def pause(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice.is_playing():
            voice.pause()
            await interaction.response.send_message("Paused, Baka Yaro")
        else:
            await interaction.response.send_message("No Audio is playing")

    @app_commands.command(name="resume", description="Resume Jotaro's audio")
    async def resume(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice.is_paused():
            voice.resume()
            await interaction.response.send_message("Resuming...")
        else:
            await interaction.response.send_message("No song is paused, Teme")

    @app_commands.command(name="stop", description="Stop Jotaro's audio")
    async def stop(self, interaction: discord.Interaction):
        voice = interaction.guild.voice_client
        if voice and (voice.is_paused() or voice.is_playing()):
            voice.stop()
            await self.client.change_presence(status=discord.Status.do_not_disturb)
            await interaction.response.send_message("Yare Yare... I'll be back")
        else:
            await interaction.response.send_message("No song is playing or paused")

    @app_commands.command(name="skip", description="Skip to the next song in the queue")
    async def skip(self, interaction: discord):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)

        if voice is not None:
            if voice.is_playing():
                boolean = await self.check_q()
                if not boolean:
                    await interaction.response.send_message("No songs in queue")
                else:
                    await interaction.response.defer()
                    self.is_skipping = True
                    voice.stop()
                    await self.play_q(interaction)
            else:
                await interaction.response.send_message("Nothing is playing..")

        else:
            await interaction.response.send_message("Yaro, Im not in a voice channel..")

    @app_commands.command(name="queue", description="print the current queue")
    async def queue(self, interaction: discord.Interaction):
        await self.print_queue(interaction)

    @app_commands.command(name="clear_queue", description="Clear the current queue")
    async def clear_queue(self, interaction: discord.Interaction):
        flag = self.check_q()
        if not flag:
            self.queues = {}  # Clear the queue by assigning an empty dictionary
            await interaction.response.send_message("Queue cleared.")
        else:
            await interaction.response.send_message("The queue is already empty.")

    @app_commands.command(name="remove_last", description="Remove the last song in the queue")
    async def remove_last(self, interaction: discord.Interaction):
        if self.queues:
            # Use popitem() to remove and return the last item in the dictionary
            last_song = self.queues.popitem()
            await interaction.response.send_message(f"Removed: {last_song[1]['title']} from the queue.")
        else:
            await interaction.response.send_message("The queue is empty.")


async def setup(bot):
    await bot.add_cog(music_cog(bot))
