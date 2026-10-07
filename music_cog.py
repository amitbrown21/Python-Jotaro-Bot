import asyncio
import os
import traceback
from typing import Dict, Optional

import discord
import yt_dlp
from discord import app_commands, FFmpegPCMAudio
from discord.ext import commands


class SongInfo:
    """Represents a song in the queue with lazy audio loading."""

    def __init__(self, title: str, url: str, audio_url: str, thumbnail: str, ffmpeg_options: dict):
        self.title = title
        self.url = url
        self.audio_url = audio_url
        self.thumbnail = thumbnail
        self.ffmpeg_options = ffmpeg_options
        self._audio: Optional[FFmpegPCMAudio] = None

    @property
    def audio(self) -> FFmpegPCMAudio:
        if self._audio is None:
            self._audio = FFmpegPCMAudio(self.audio_url, **self.ffmpeg_options)
        return self._audio


class GuildQueue:
    """Manages the music queue and skip/stop flags for a single guild."""

    def __init__(self):
        self.songs: list[SongInfo] = []
        self.current: Optional[SongInfo] = None
        self.skipping = False
        self.stopping = False

    def add(self, song: SongInfo) -> int:
        self.songs.append(song)
        return len(self.songs)

    def pop_next(self) -> Optional[SongInfo]:
        if self.songs:
            self.current = self.songs.pop(0)
            return self.current
        self.current = None
        return None

    def clear(self):
        self.songs.clear()
        self.current = None

    def remove_last(self) -> Optional[SongInfo]:
        if self.songs:
            return self.songs.pop()
        return None

    def __len__(self) -> int:
        return len(self.songs)

    def __bool__(self) -> bool:
        return bool(self.songs)


class music_cog(commands.Cog):

    def __init__(self, client: commands.Bot):
        self.client = client
        self.queues: Dict[int, GuildQueue] = {}

        self.yt_dl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio',
            'extract_flat': False,
            'noplaylist': False,
            'quiet': True,
            'no_warnings': True,
            'extractor_retries': 3,
            'socket_timeout': 15,
            'retries': 3,
            'cachedir': os.path.join(os.path.dirname(__file__), '.yt_cache'),
        }

        self.ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn -bufsize 64k',
        }

        self.ytdl = yt_dlp.YoutubeDL(self.yt_dl_opts)
        self._search_cache: Dict[str, str] = {}
        self._cache_max_size = 100

    def get_queue(self, guild_id: int) -> GuildQueue:
        if guild_id not in self.queues:
            self.queues[guild_id] = GuildQueue()
        return self.queues[guild_id]

    def song_embed(
        self,
        song: SongInfo,
        interaction: discord.Interaction,
        description: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=song.title,
            url=song.url,
            description=description,
            color=0xffff00,
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None,
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        return embed

    async def print_queue(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        if queue:
            queue_info = "\n".join(
                f"{index}. [{song.title}]({song.url})"
                for index, song in enumerate(queue.songs, start=1)
            )
            embed = discord.Embed(
                title="Current Queue",
                description=queue_info,
                color=0x3498db,
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "Queue is empty, Teme.", ephemeral=True
            )

    def find_general_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        for channel in guild.text_channels:
            if 'general' in channel.name.lower():
                return channel
        return None

    def after_play(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        if not queue.skipping and not queue.stopping:
            asyncio.run_coroutine_threadsafe(
                self.play_next(interaction),
                self.client.loop,
            )
        else:
            queue.stopping = False
            queue.skipping = False

    async def play_next(self, interaction: discord.Interaction):
        try:
            queue = self.get_queue(interaction.guild_id)
            voice = interaction.guild.voice_client

            if not voice:
                return

            song = queue.pop_next()
            if song:
                await self.client.change_presence(
                    activity=discord.Game(name=song.title)
                )
                await interaction.followup.send(
                    embed=self.song_embed(song, interaction, "Now Playing...")
                )
                voice.play(
                    song.audio,
                    after=lambda x=None: self.after_play(interaction),
                )
            else:
                await self.client.change_presence(status=discord.Status.do_not_disturb)
                await interaction.followup.send("Owari Da... no more songs in the queue")

        except Exception as e:
            print(f"An error occurred during play_next: {e}")
            await self.client.change_presence(status=discord.Status.do_not_disturb)
            traceback.print_exc()

    async def resolve_source(self, query: str) -> Optional[str]:
        """Resolve a search query or URL to a playable webpage URL."""
        if query.startswith("http://") or query.startswith("https://"):
            return query

        cache_key = query.lower().strip()
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        info = await asyncio.to_thread(
            self.ytdl.extract_info, f"ytsearch1:{query}", False
        )
        entries = (info or {}).get("entries") or []
        if not entries:
            return None

        entry = entries[0]
        source = entry.get("webpage_url") or entry.get("url")
        if not source:
            return None

        if len(self._search_cache) >= self._cache_max_size:
            oldest_key = next(iter(self._search_cache))
            del self._search_cache[oldest_key]
        self._search_cache[cache_key] = source
        return source

    def create_song_info(self, info: dict, webpage_url: str = None) -> SongInfo:
        return SongInfo(
            title=info.get('title', 'Unknown'),
            url=webpage_url or info.get('webpage_url', ''),
            audio_url=info.get('url', ''),
            thumbnail=info.get('thumbnail', ''),
            ffmpeg_options=self.ffmpeg_options,
        )

    @app_commands.command(name="play", description="Play a song using YouTube search or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        if not interaction.user.voice:
            await interaction.followup.send(
                "Get in a voice channel first, Teme.", ephemeral=True
            )
            return

        channel = interaction.user.voice.channel
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)

        if not voice or not voice.is_connected():
            voice = await channel.connect()

        queue = self.get_queue(interaction.guild_id)

        source = await self.resolve_source(query)
        if not source:
            await interaction.followup.send("No search results, Yare Yare...")
            return

        try:
            info = await asyncio.to_thread(self.ytdl.extract_info, source, False)
        except Exception as e:
            await interaction.followup.send(f"Error extracting info: {e}")
            return

        if info.get('_type') == 'playlist':
            playlist_name = info.get('title', 'Playlist')
            entries = info.get('entries', [])

            if not entries:
                await interaction.followup.send("Playlist is empty, Teme.")
                return

            first_song = entries[0]
            thumbnail = first_song.get('thumbnail', '')

            embed = discord.Embed(
                title=playlist_name,
                url=info.get('webpage_url', ''),
                description=f"Adding {len(entries)} songs to queue...",
                color=0xffff00,
            )
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None,
            )
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
            embed.set_footer(text=f'Playlist length: {len(entries)}')
            await interaction.followup.send(embed=embed)

            for entry in entries:
                song = self.create_song_info(entry, entry.get('webpage_url', ''))
                queue.add(song)

            if not voice.is_playing() and not voice.is_paused():
                song = queue.pop_next()
                if song:
                    await self.client.change_presence(
                        activity=discord.Game(name=song.title)
                    )
                    voice.play(
                        song.audio,
                        after=lambda x=None: self.after_play(interaction),
                    )
        else:
            song = self.create_song_info(info, source)

            if not voice.is_playing() and not voice.is_paused():
                await interaction.followup.send(
                    embed=self.song_embed(song, interaction, "Now Playing...")
                )
                await self.client.change_presence(
                    activity=discord.Game(name=song.title)
                )
                voice.play(
                    song.audio,
                    after=lambda x=None: self.after_play(interaction),
                )
            else:
                position = queue.add(song)
                await interaction.followup.send(
                    embed=self.song_embed(
                        song, interaction, f"Added to queue (Position: {position})"
                    )
                )

    @app_commands.command(name="leave", description="Make Jotaro leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            queue = self.get_queue(interaction.guild_id)
            queue.clear()
            await interaction.voice_client.disconnect()
            await interaction.response.send_message("Yare Yare... I'll be back")
        else:
            await interaction.response.send_message("TEME!!! I'M NOT IN A VOICE CHANNELLL!!!")

    @app_commands.command(name="pause", description="Pause Jotaro's audio")
    async def pause(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice and voice.is_playing():
            voice.pause()
            await interaction.response.send_message("Paused, Baka Yaro")
        else:
            await interaction.response.send_message("No audio is playing, Teme.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume Jotaro's audio")
    async def resume(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice and voice.is_paused():
            voice.resume()
            await interaction.response.send_message("Resuming...")
        else:
            await interaction.response.send_message("No song is paused, Teme")

    @app_commands.command(name="stop", description="Stop Jotaro's audio")
    async def stop(self, interaction: discord.Interaction):
        voice = interaction.guild.voice_client
        if voice and (voice.is_paused() or voice.is_playing()):
            queue = self.get_queue(interaction.guild_id)
            queue.stopping = True
            queue.clear()
            voice.stop()
            await voice.disconnect()
            await self.client.change_presence(status=discord.Status.do_not_disturb)
            await interaction.response.send_message("Yare Yare... I'll be back")
        else:
            await interaction.response.send_message("Nothing to stop, Teme.")

    @app_commands.command(name="skip", description="Skip to the next song in the queue")
    async def skip(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        queue = self.get_queue(interaction.guild_id)

        if voice is None:
            await interaction.response.send_message("Yaro, Im not in a voice channel..")
            return

        if not voice.is_playing():
            await interaction.response.send_message("Nothing is playing..", ephemeral=True)
            return

        if not queue:
            await interaction.response.send_message("No songs in queue", ephemeral=True)
            return

        queue.skipping = True
        await interaction.response.send_message("Skipping..", ephemeral=True)
        voice.stop()

    @app_commands.command(name="queue", description="Print the current queue")
    async def queue(self, interaction: discord.Interaction):
        await self.print_queue(interaction)

    @app_commands.command(name="clear_queue", description="Clear the current queue")
    async def clear_queue(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        queue.clear()
        await interaction.response.send_message("Queue cleared, Yare Yare.", ephemeral=True)

    @app_commands.command(name="remove_last", description="Remove the last song in the queue")
    async def remove_last(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        song = queue.remove_last()
        if song:
            await interaction.response.send_message(
                f"Removed: {song.title} from the queue."
            )
        else:
            await interaction.response.send_message(
                "Queue is empty, Teme.", ephemeral=True
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if before.channel and len(before.channel.members) == 1:
            voice_client = discord.utils.get(
                self.client.voice_clients,
                guild=before.channel.guild,
            )
            if voice_client and voice_client.channel == before.channel:
                queue = self.get_queue(before.channel.guild.id)
                queue.clear()
                await voice_client.disconnect()
                await self.client.change_presence(status=discord.Status.do_not_disturb)

                general_channel = self.find_general_channel(before.channel.guild)
                if general_channel:
                    await general_channel.send(
                        "I'm alone here, leaving the voice channel. Yare Yare..."
                    )

    @app_commands.command(name="localplay", description="Play a local mp3")
    async def localplay(self, interaction: discord.Interaction, filename: str):
        await interaction.response.defer()

        if not interaction.user.voice:
            await interaction.followup.send(
                "Get in a voice channel first, Teme.", ephemeral=True
            )
            return

        channel = interaction.user.voice.channel
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)

        if not voice or not voice.is_connected():
            voice = await channel.connect()

        song_path = f"{filename}.mp3"
        if not os.path.exists(song_path):
            await interaction.followup.send(f"File not found: {song_path}")
            return

        try:
            voice.play(
                FFmpegPCMAudio(song_path),
                after=lambda x=None: self.after_play(interaction),
            )
            await interaction.followup.send(f"Now playing: {filename}")
        except Exception as e:
            await interaction.followup.send(f"Error playing {filename}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(music_cog(bot))
