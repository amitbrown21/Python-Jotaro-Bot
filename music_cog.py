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
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio[ext=ogg]',
        'buffer_size': 2048*2048,
        'extractor_retries': 3}
        self.ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                               'options': '-vn', }
        self.ytdl = yt_dlp.YoutubeDL(self.yt_dl_opts)
        self.is_skipping = False
        self.is_stopping = False

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
            await interaction.response.send_message("The queue is empty.", ephemeral=True)

    def find_general_channel(self, guild):
        # Iterate through all text channels in the guild
        for channel in guild.text_channels:
            # Check if 'general' is part of the channel name (case insensitive)
            if 'general' in channel.name.lower():
                return channel  # Return the first matching channel
        return None  # Return None if no general channel is found

    async def check_q(self):
        if len(self.queues) != 0:
            return True
        else:
            return False

    def after_play(self, interaction):

        if not self.is_skipping or not self.is_stopping:
            asyncio.run_coroutine_threadsafe(self.play_q(interaction), self.client.loop)
        else:

            self.is_stopping = False
            self.is_skipping = False

    async def c_presence(self, interaction):
        if self.queues == []:
            await self.client.change_presence(status=discord.Status.do_not_disturb, activity=None)

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
            else:
                await self.c_presence(interaction)

        except Exception as e:
            print(f"An error occurred during play_q: {e}")
            await self.client.change_presence(status=discord.Status.do_not_disturb)
            traceback.print_exc()

    def search_yt(self, item):
        if item.startswith("https://"):
            ishttp = True
            return {'source': item}
        ishttp = False
        search = VideosSearch(item, limit=1)
        results = search.result()["result"]

        if not results:
            return None

        first_result = results[0]
        return {'source': first_result["link"]}

    @app_commands.command(name="play", description="Play a song using YouTube search or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        # Defer the interaction immediately
        await interaction.response.defer()

        if interaction.user.voice:
            channel = interaction.user.voice.channel
            voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)

            if voice and voice.is_connected():
                # Bot is already in a voice channel
                pass
            else:
                # Bot is not in a voice channel, connect to the specified channel
                voice = await channel.connect()

            if not voice.is_playing() and not voice.is_paused():
                search_result = self.search_yt(query)
                if not search_result:
                    await interaction.followup.send("No search results found.")
                    return

                info = self.ytdl.extract_info(search_result['source'], download=False)
                if info.get('_type') == 'playlist':
                    # Play the first song from the playlist
                    first_song = info['entries'][0]
                    song_url = first_song['url']
                    url = first_song['webpage_url']
                    title = first_song['title']
                    thumbnail = first_song['thumbnail']

                    # Send the message that contains the song information
                    embed = discord.Embed(title=title, url=url, description="Now Playing...", color=0xffff00)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                    embed.set_thumbnail(url=thumbnail)
                    await interaction.followup.send(embed=embed)
                    await self.client.change_presence(status=discord.Status.online,
                                                      activity=discord.Game(name=title))

                    # Play the first song
                    song = FFmpegPCMAudio(song_url, **self.ffmpeg_options)
                    voice.play(song, after=lambda x=None: asyncio.create_task(self.after_play(interaction)))
                    voice.is_playing()
                    # Add the rest of the playlist to the queue
                    for entry in info['entries'][1:]:
                        song_url = entry['webpage_url']
                        url = entry['url']
                        title = entry['title']
                        thumbnail = entry['thumbnail']
                        song_position = len(self.queues) + 1
                        self.queues[song_position] = {'title': title,
                                                      'audio': FFmpegPCMAudio(url, **self.ffmpeg_options),
                                                      'url': song_url, 'thumbnail': thumbnail}
                    return
                else:
                    title = info['title']
                    thumbnail = info['thumbnail']
                    url = info['url']
                    # Sending the message that contains the song information
                    embed = discord.Embed(title=title, url=search_result['source'],
                                          description="Now Playing...", color=0xffff00)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                    embed.set_thumbnail(url=thumbnail)
                    await interaction.followup.send(embed=embed)
                    await self.client.change_presence(status=discord.Status.do_not_disturb,
                                                      activity=discord.Game(name=title))
                    # Where the song is played
                    voice.is_playing()
                    song = FFmpegPCMAudio(url, **self.ffmpeg_options)
                    voice.play(song, after=lambda x=None: self.after_play(interaction))
                    voice.is_playing()
            else:
                # here we add the song to the queue
                search_result = self.search_yt(query)
                if not search_result:
                    await interaction.followup.send("No search results found.")
                    return

                info = self.ytdl.extract_info(search_result['source'], download=False)
                if info.get('_type') == 'playlist':
                    # Play the first song from the playlist
                    playlist_name = info['title']
                    playlist_length = info['playlist_count']
                    first_song = info['entries'][0]
                    first_song_name = first_song['title']
                    thumbnail = first_song['thumbnail']
                    url = info['webpage_url']

                    # Send the message that contains the song information
                    embed = discord.Embed(title=playlist_name, url=url, description="Now Playing...", color=0xffff00)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                    embed.set_thumbnail(url=thumbnail)
                    embed.set_footer(text=f'Playlist length -{playlist_length}')
                    await interaction.followup.send(embed=embed)
                    await self.client.change_presence(status=discord.Status.do_not_disturb,
                                                      activity=discord.Game(name=first_song_name))
                    # Add the  playlist to the queue
                    for entry in info['entries']:
                        song_url = entry['webpage_url']
                        url = entry['url']
                        title = entry['title']
                        thumbnail = entry['thumbnail']
                        song_position = len(self.queues) + 1
                        self.queues[song_position] = {'title': title,
                                                      'audio': FFmpegPCMAudio(url, **self.ffmpeg_options),
                                                      'url': song_url, 'thumbnail': thumbnail}
                    return
                else:
                    title = info['title']
                    url = info['url']
                    thumbnail = info['thumbnail']
                    song = FFmpegPCMAudio(url, **self.ffmpeg_options)
                    embed = discord.Embed(title=title, url=search_result['source'],
                                          description="Was added to queue", color=0xffff00)
                    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)
                    embed.set_thumbnail(url=thumbnail)
                    await interaction.followup.send(embed=embed)
                    song_position = len(self.queues) + 1
                    self.queues[song_position] = {'title': title, 'audio': song,
                                                  'url': search_result['source'], 'thumbnail': thumbnail}
                    return
        else:
            await interaction.followup.send("You are not in a voice channel.", ephemeral=True)
            return

    @app_commands.command(name="leave", description="Make Jotaro leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
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
            await interaction.response.send_message("No Audio is playing", ephemeral=True)

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
            self.is_stopping = True
            await voice.disconnect()
            self.queues = {}
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
                    await interaction.response.send_message("No songs in queue", ephemeral=True)
                else:
                    self.is_skipping = True
                    await interaction.response.send_message("Skipping..", ephemeral=True)
                    voice.stop()
            else:
                await interaction.response.send_message("Nothing is playing..", ephemeral=True)

        else:
            await interaction.response.send_message("Yaro, Im not in a voice channel..")

    @app_commands.command(name="queue", description="print the current queue")
    async def queue(self, interaction: discord.Interaction):
        await self.print_queue(interaction)

    @app_commands.command(name="clear_queue", description="Clear the current queue")
    async def clear_queue(self, interaction: discord.Interaction):
        self.queues = {}  # Clear the queue by assigning an empty dictionary
        await interaction.response.send_message("Queue cleared.", ephemeral=True)

    @app_commands.command(name="remove_last", description="Remove the last song in the queue")
    async def remove_last(self, interaction: discord.Interaction):
        if self.queues:
            # Use popitem() to remove and return the last item in the dictionary
            last_song = self.queues.popitem()
            await interaction.response.send_message(f"Removed: {last_song[1]['title']} from the queue.")
        else:
            await interaction.response.send_message("The queue is empty.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Check if the bot is the only member in the voice channel
        if before.channel and len(before.channel.members) == 1:
            # If the bot is alone, disconnect from the voice channel
            voice_client = discord.utils.get(self.client.voice_clients, guild=before.channel.guild)
            if voice_client and voice_client.channel:
                self.queues = {}
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

    @app_commands.command(name="localplay", description="Play a local mp3")
    async def localplay(self, interaction: discord.Interaction, filename: str):
        await interaction.response.defer()
        if interaction.user.voice:
            channel = interaction.user.voice.channel
            voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)

            if voice and voice.is_connected():
                # Bot is already in a voice channel
                pass
            else:
                # Bot is not in a voice channel, connect to the specified channel
                voice = await channel.connect()

            song_path = f"{filename}.mp3"  # Assuming mp3 file extension
            try:
                voice.play(discord.FFmpegPCMAudio(song_path, executable="ffmpeg.exe"),
                           after=lambda x=None: self.after_play(interaction))
                voice.is_playing()
                await interaction.followup.send(f"Now playing: {filename}")
            except Exception as e:
                await interaction.followup.send(f"Error playing {filename}: {e}")
        else:
            await interaction.followup.send("You are not in a voice channel.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(music_cog(bot))
