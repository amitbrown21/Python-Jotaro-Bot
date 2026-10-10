import asyncio
import json
import logging
import os
from typing import Dict, Optional

import discord
import yt_dlp
from discord import FFmpegPCMAudio, app_commands
from discord.ext import commands

from music_audio import (  # noqa: F401  (re-exported: main.py and test_offline.py import from here)
    FFMPEG_BEFORE,
    FFMPEG_OPTIONS,
    YTDL_FORMAT,
    PlayClock,
    TrackAudio,
    TrackOpusAudio,
    VolumeAudio,
    build_ffmpeg_options,
    choose_audio_path,
    fetch_lyrics,
    info_is_opus,
    opus_bitrate,
    parse_seek,
    should_reconnect,
    stream_url_stale,
    ytdlp_failure_hint,
)
from music_store import (  # noqa: F401
    MAX_PLAY_FAILURES,
    MAX_PLAYLIST_ENTRIES,
    GuildLibrary,
    GuildQueue,
    SongInfo,
    clip_lines,
    library_path,
    pick_related_entry,
    songs_from_tracks,
    track_from_flat_entry,
    tracks_from_flat_entries,
    tracks_from_queue,
    video_id,
    volumes_path,
)
from music_playback import PlaybackMixin
from music_sources import SourcesMixin
from music_views import QueueControlView  # noqa: F401

log = logging.getLogger(__name__)

IDLE_LEAVE_SECONDS = 60


class music_cog(PlaybackMixin, SourcesMixin, commands.Cog):
    playlist = app_commands.Group(
        name="playlist",
        description="Save and replay this server's queues",
    )

    def __init__(self, client: commands.Bot):
        self.client = client
        self.queues: Dict[int, GuildQueue] = {}
        self.volumes: Dict[int, int] = self._load_volumes()
        self.library = GuildLibrary.load(library_path())
        self._idle_tasks: Dict[int, asyncio.Task] = {}
        self._idle_channel: Dict[int, int] = {}
        self._last_interaction: Dict[int, discord.Interaction] = {}
        self._reconnecting: set = set()
        # Snapshot of queues saved before this process started; /restore_queue consumes it.
        self._restorable: Dict[int, dict] = {
            int(gid): saved
            for gid in self.library.guilds
            if str(gid).lstrip("-").isdigit()
            and (saved := self.library.get_saved_queue(int(gid)))
        }
        self._notified_restorable: set = set()
        self._lib_dirty = False
        self._lib_task: Optional[asyncio.Task] = None
        if self._restorable:
            log.info("Saved queues found for %d guild(s); waiting for /restore_queue", len(self._restorable))

        self.yt_dl_opts = {
            'format': YTDL_FORMAT,
            'extract_flat': False,
            'noplaylist': False,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,  # bad ytsearch entries -> None, not DownloadError
            'extractor_retries': 3,
            'socket_timeout': 15,
            'retries': 3,
            'cachedir': os.path.join(os.path.dirname(__file__), '.yt_cache'),
        }

        self.ffmpeg_options = {
            'before_options': FFMPEG_BEFORE,
            'options': FFMPEG_OPTIONS,
        }

        self.ytdl = yt_dlp.YoutubeDL(self.yt_dl_opts)
        # Playlist entries come back as bare metadata (title/url/thumbnail); no per-entry
        # format resolution. Single videos are still fully resolved.
        self.ytdl_flat = yt_dlp.YoutubeDL({
            **self.yt_dl_opts,
            'extract_flat': 'in_playlist',
            'playlistend': MAX_PLAYLIST_ENTRIES,
        })
        self._search_cache: Dict[str, str] = {}
        self._cache_max_size = 100
        # ponytail: per-guild lock; split locks if cross-guild contention matters
        self._guild_locks: Dict[int, asyncio.Lock] = {}
        self._extract_lock = asyncio.Lock()

    def _load_volumes(self) -> Dict[int, int]:
        path = volumes_path()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {int(k): int(v) for k, v in data.items()}
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            return {}

    def _save_volumes(self):
        path = volumes_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in self.volumes.items()}, f)

    def get_volume_percent(self, channel_id: int) -> int:
        return self.volumes.get(channel_id, 100)

    def is_autoplay(self, guild_id: int) -> bool:
        return self.library.autoplay(guild_id)

    def toggle_autoplay(self, guild_id: int) -> bool:
        on = self.library.toggle_autoplay(guild_id)
        self.save_library_soon()
        return on

    def save_library_soon(self):
        """Coalesced, off-loop atomic write of library.json."""
        self._lib_dirty = True
        if self._lib_task is None or self._lib_task.done():
            self._lib_task = asyncio.create_task(self._save_library_loop())

    async def _save_library_loop(self):
        while self._lib_dirty:
            self._lib_dirty = False
            path = library_path()
            try:
                text = self.library.dumps()  # snapshot on the loop; only the I/O leaves it
                await asyncio.to_thread(GuildLibrary.write_atomic, path, text)
            except Exception as e:
                log.warning("Could not write %s: %s", path, e)

    def persist_queue(self, guild_id: int):
        """Mirror the live queue into library.json (cleared when nothing is queued)."""
        queue = self.get_queue(guild_id)
        self.library.set_saved_queue(
            guild_id, tracks_from_queue(queue), queue.looping
        )
        self.save_library_soon()

    def clear_queue_state(self, guild_id: int):
        """Deliberate clear (/stop, /leave, /clear_queue, idle leave, finished)."""
        self.get_queue(guild_id).clear()
        self._restorable.pop(guild_id, None)
        if self.library.clear_saved_queue(guild_id):
            self.save_library_soon()

    def _cancel_idle(self, guild_id: int):
        self._idle_channel.pop(guild_id, None)
        task = self._idle_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_idle(self, guild_id: int, channel_id: int):
        existing = self._idle_tasks.get(guild_id)
        if (
            existing
            and not existing.done()
            and self._idle_channel.get(guild_id) == channel_id
        ):
            return
        self._cancel_idle(guild_id)
        self._idle_channel[guild_id] = channel_id
        self._idle_tasks[guild_id] = asyncio.create_task(
            self._idle_leave(guild_id, channel_id)
        )

    async def _idle_leave(self, guild_id: int, channel_id: int):
        try:
            await asyncio.sleep(IDLE_LEAVE_SECONDS)
        except asyncio.CancelledError:
            raise
        guild = self.client.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        if not voice or not voice.channel or voice.channel.id != channel_id:
            return
        if len(voice.channel.members) > 1:
            return
        queue = self.get_queue(guild_id)
        queue.stopping = True
        self.clear_queue_state(guild_id)
        await voice.disconnect()
        try:
            await self.client.change_presence(status=discord.Status.do_not_disturb)
        except Exception as e:
            log.warning("idle leave presence failed: %s", e)
        general_channel = self.find_general_channel(guild)
        if general_channel:
            await general_channel.send(
                "I'm alone here, leaving the voice channel. Yare Yare..."
            )
        self._idle_tasks.pop(guild_id, None)
        self._idle_channel.pop(guild_id, None)

    def same_vc_error(self, interaction: discord.Interaction) -> Optional[str]:
        voice = interaction.guild.voice_client if interaction.guild else None
        if not voice or not voice.channel:
            return "I'm not in a voice channel, Teme."
        if (
            not interaction.user.voice
            or interaction.user.voice.channel != voice.channel
        ):
            return "Get in my voice channel first, Teme."
        return None

    def get_queue(self, guild_id: int) -> GuildQueue:
        if guild_id not in self.queues:
            self.queues[guild_id] = GuildQueue()
        return self.queues[guild_id]

    def get_guild_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._guild_locks:
            self._guild_locks[guild_id] = asyncio.Lock()
        return self._guild_locks[guild_id]

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

    def build_queue_embed(self, queue: GuildQueue) -> discord.Embed:
        lines = []
        if queue.current:
            req = queue.current.requester_name or "unknown"
            lines.append(
                f"**Now playing:** [{queue.current.title}]({queue.current.url}) — {req}"
            )
            lines.append("")
        if queue.songs:
            lines.append("**Up next:**")
            for i, song in enumerate(queue.songs, start=1):
                req = song.requester_name or "unknown"
                lines.append(f"{i}. [{song.title}]({song.url}) — {req}")
        elif not queue.current:
            lines.append("Queue is empty.")
        else:
            lines.append("*Nothing waiting.*")

        if queue.looping:
            lines.append("")
            lines.append("🔁 Queue loop ON")

        return discord.Embed(
            title="Current Queue",
            description=clip_lines(lines),
            color=0x3498db,
        )

    async def print_queue(self, interaction: discord.Interaction):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.get_queue(interaction.guild_id)
        if not queue.current and not queue.songs:
            await interaction.response.send_message(
                "Queue is empty, Teme.", ephemeral=True
            )
            return

        embed = self.build_queue_embed(queue)
        view = QueueControlView(self, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    def find_general_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        for channel in guild.text_channels:
            if 'general' in channel.name.lower():
                return channel
        return None

    @app_commands.command(name="play", description="Play a song using YouTube search or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        responded = False
        try:
            if not interaction.user.voice:
                await interaction.followup.send(
                    "Get in a voice channel first, Teme.", ephemeral=True
                )
                responded = True
                return

            channel = interaction.user.voice.channel

            # yt-dlp outside lock so concurrent /plays can search in parallel
            source = await self.resolve_source(query)
            if not source:
                await interaction.followup.send("Can't play that, Teme.")
                responded = True
                return

            try:
                # flat for playlists (metadata only); single videos still fully resolved
                info = await self._extract_flat(source)
            except yt_dlp.utils.DownloadError as e:
                log.warning("play DownloadError: %s%s", e, ytdlp_failure_hint(str(e)))
                await interaction.followup.send("Can't play that, Teme.")
                responded = True
                return
            except Exception as e:
                log.warning("play extract_info failed: %s%s", e, ytdlp_failure_hint(str(e)))
                await interaction.followup.send("Can't play that, Teme.")
                responded = True
                return

            if not info:
                await interaction.followup.send("Can't play that, Teme.")
                responded = True
                return

            playlist_songs = []
            embed = None
            first_failed = False
            if info.get('_type') == 'playlist':
                raw_entries = [e for e in (info.get('entries') or []) if e]
                tracks = tracks_from_flat_entries(raw_entries)
                if not tracks:
                    await interaction.followup.send("Playlist is empty, Teme.")
                    responded = True
                    return
                # Unresolved songs: stream URLs are fetched lazily (first one on start,
                # the rest via preload as each comes up).
                playlist_songs = songs_from_tracks(
                    tracks, self.ffmpeg_options, interaction.user
                )
                skipped = len(raw_entries) - len(tracks)
                footer = f"Playlist length: {len(tracks)}"
                if skipped:
                    footer += f" ({skipped} unavailable skipped)"
                if len(raw_entries) >= MAX_PLAYLIST_ENTRIES:
                    footer += f" - capped at first {MAX_PLAYLIST_ENTRIES}"
                embed = discord.Embed(
                    title=info.get('title', 'Playlist'),
                    url=info.get('webpage_url', ''),
                    description=f"Adding {len(tracks)} songs to queue...",
                    color=0xffff00,
                )
                embed.set_author(
                    name=interaction.user.display_name,
                    icon_url=(
                        interaction.user.avatar.url
                        if interaction.user.avatar
                        else None
                    ),
                )
                if tracks[0]["thumbnail"]:
                    embed.set_thumbnail(url=tracks[0]["thumbnail"])
                embed.set_footer(text=footer)

            lock = self.get_guild_lock(interaction.guild_id)
            async with lock:
                voice = discord.utils.get(
                    self.client.voice_clients, guild=interaction.guild
                )
                if not voice or not voice.is_connected():
                    voice = await channel.connect()

                queue = self.get_queue(interaction.guild_id)

                if playlist_songs:
                    for song in playlist_songs:
                        queue.add(song)

                    starting = not voice.is_playing() and not voice.is_paused()
                    started = queue.pop_next() if starting else None

                    await interaction.followup.send(
                        embed=embed,
                        view=QueueControlView(
                            self, interaction.guild_id, show_queue_button=True
                        ),
                    )
                    responded = True

                    if started:
                        try:
                            await self.client.change_presence(
                                activity=discord.Game(name=started.title)
                            )
                            await self.begin_playback(voice, started, interaction)
                        except Exception as e:
                            log.warning("playlist first track failed: %s", e)
                            first_failed = True
                    else:
                        self.persist_queue(interaction.guild_id)
                        self.schedule_preload(interaction.guild_id)
                else:
                    song = self.create_song_info(info, source, interaction.user)

                    if not voice.is_playing() and not voice.is_paused():
                        queue.current = song
                        await interaction.followup.send(
                            embed=self.song_embed(
                                song, interaction, "Now Playing..."
                            ),
                            view=QueueControlView(
                                self, interaction.guild_id, show_queue_button=True
                            ),
                        )
                        responded = True
                        await self.client.change_presence(
                            activity=discord.Game(name=song.title)
                        )
                        await self.begin_playback(voice, song, interaction)
                    else:
                        position = queue.add(song)
                        self.persist_queue(interaction.guild_id)
                        self.schedule_preload(interaction.guild_id)
                        await interaction.followup.send(
                            embed=self.song_embed(
                                song,
                                interaction,
                                f"Added to queue (Position: {position})",
                            )
                        )
                        responded = True

            if first_failed:
                # first entry was dead; play_next skips on to the next playable one
                await self.play_next(interaction)
            await self._maybe_hint_restore(interaction)
        except Exception as e:
            log.exception("play failed: %s", e)
        finally:
            if not responded:
                try:
                    await interaction.followup.send("Can't play that, Teme.")
                except Exception:
                    pass

    @app_commands.command(name="leave", description="Make Jotaro leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            self.clear_queue_state(interaction.guild_id)
            await interaction.voice_client.disconnect()
            await interaction.response.send_message("Yare Yare... I'll be back")
        else:
            await interaction.response.send_message("TEME!!! I'M NOT IN A VOICE CHANNELLL!!!")

    @app_commands.command(name="pause", description="Pause Jotaro's audio")
    async def pause(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice and voice.is_playing():
            voice.pause()
            self.get_queue(interaction.guild_id).clock.pause()
            await interaction.response.send_message("Paused, Baka Yaro")
        else:
            await interaction.response.send_message("No audio is playing, Teme.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume Jotaro's audio")
    async def resume(self, interaction: discord.Interaction):
        voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
        if voice and voice.is_paused():
            voice.resume()
            self.get_queue(interaction.guild_id).clock.resume()
            await interaction.response.send_message("Resuming...")
        else:
            await interaction.response.send_message("No song is paused, Teme")

    @app_commands.command(name="stop", description="Stop Jotaro's audio")
    async def stop(self, interaction: discord.Interaction):
        voice = interaction.guild.voice_client
        if voice and (voice.is_paused() or voice.is_playing()):
            queue = self.get_queue(interaction.guild_id)
            queue.stopping = True
            self.clear_queue_state(interaction.guild_id)
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

    @app_commands.command(name="nowplaying", description="Show the currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.get_queue(interaction.guild_id)
        song = queue.current
        if not song:
            await interaction.response.send_message(
                "Nothing playing, Teme.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=song.title,
            url=song.url,
            description=f"Requested by {song.requester_name or 'unknown'}",
            color=0xffff00,
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        await interaction.response.send_message(
            embed=embed,
            view=QueueControlView(
                self, interaction.guild_id, show_queue_button=True
            ),
        )

    @app_commands.command(name="volume", description="Show or set volume (0-100) for this voice channel")
    @app_commands.describe(level="Volume 0-100; omit to show current")
    async def volume(
        self, interaction: discord.Interaction, level: Optional[app_commands.Range[int, 0, 100]] = None
    ):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        channel_id = interaction.guild.voice_client.channel.id
        if level is None:
            current = self.get_volume_percent(channel_id)
            await interaction.response.send_message(f"Volume is {current}%, Teme.")
            return

        self.volumes[channel_id] = int(level)
        self._save_volumes()

        voice = interaction.guild.voice_client
        if voice and voice.source and hasattr(voice.source, "volume"):
            voice.source.volume = int(level) / 100.0
        elif voice and isinstance(voice.source, TrackOpusAudio) and int(level) != 100:
            # Passthrough has no gain stage: rebuild on the PCM path at the same position.
            queue = self.get_queue(interaction.guild_id)
            self.restart_current(voice, interaction, seek=queue.clock.position())

        await interaction.response.send_message(
            f"Volume set to {int(level)}%, Yare Yare..."
        )

    @app_commands.command(name="shuffle", description="Shuffle the pending queue")
    async def shuffle(self, interaction: discord.Interaction):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.get_queue(interaction.guild_id)
        if not queue.songs:
            await interaction.response.send_message(
                "Nothing to shuffle, Teme.", ephemeral=True
            )
            return

        queue.shuffle()
        self.persist_queue(interaction.guild_id)
        self.schedule_preload(interaction.guild_id)
        await interaction.response.send_message("Shuffled the queue, Yare Yare...")

    @app_commands.command(name="loop", description="Toggle queue loop")
    async def loop(self, interaction: discord.Interaction):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.get_queue(interaction.guild_id)
        queue.looping = not queue.looping
        self.persist_queue(interaction.guild_id)
        if queue.looping:
            await interaction.response.send_message(
                "Queue loop ON. Round and round, Yare Yare..."
            )
        else:
            queue.history.clear()
            await interaction.response.send_message("Queue loop OFF. Owari da.")

    @app_commands.command(name="remove", description="Remove a pending song by position")
    @app_commands.describe(position="1-based index in the pending queue")
    async def remove(self, interaction: discord.Interaction, position: int):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return
        song = self.get_queue(interaction.guild_id).remove_at(position)
        if not song:
            await interaction.response.send_message("Can't remove that, Teme.", ephemeral=True)
            return
        self.persist_queue(interaction.guild_id)
        self.schedule_preload(interaction.guild_id)
        await interaction.response.send_message(f"Removed **{song.title}**. Yare Yare.")

    @app_commands.command(name="seek", description="Seek the current song")
    @app_commands.describe(time="Seconds, m:ss, or h:mm:ss")
    async def seek(self, interaction: discord.Interaction, time: str):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return
        seconds = parse_seek(time)
        if seconds is None:
            await interaction.response.send_message("Bad time, Teme.", ephemeral=True)
            return
        voice = interaction.guild.voice_client if interaction.guild else None
        if not voice or not self.restart_current(voice, interaction, seek=seconds):
            await interaction.response.send_message("Nothing is playing, Teme.", ephemeral=True)
            return
        await interaction.response.send_message(f"Seeked to {time}. Yare Yare...")

    @app_commands.command(name="lyrics", description="Lyrics for the current song")
    async def lyrics(self, interaction: discord.Interaction):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return
        song = self.get_queue(interaction.guild_id).current
        if not song:
            await interaction.response.send_message("Nothing playing, Teme.", ephemeral=True)
            return
        await interaction.response.defer()
        text = await asyncio.to_thread(fetch_lyrics, song.title)
        if not text:
            await interaction.followup.send("Yare Yare... no lyrics for this one, Teme.")
            return
        await interaction.followup.send(text[:2000])

    @app_commands.command(name="history", description="Recently played songs")
    @app_commands.describe(requeue="Requeue this entry onto the pending queue (1 = most recent)")
    async def history(
        self, interaction: discord.Interaction, requeue: Optional[int] = None
    ):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return
        queue = self.get_queue(interaction.guild_id)
        if requeue is not None:
            song = queue.requeue_played(requeue, self.ffmpeg_options)
            if not song:
                await interaction.response.send_message(
                    "Can't requeue that, Teme.", ephemeral=True
                )
                return
            self.persist_queue(interaction.guild_id)
            self.schedule_preload(interaction.guild_id)
            await interaction.response.send_message(
                f"Requeued **{song.title}**. Yare Yare..."
            )
            return
        if not queue.played:
            await interaction.response.send_message("No history yet, Teme.", ephemeral=True)
            return
        lines = [
            f"{i}. [{title}]({url})"
            for i, (title, url) in enumerate(queue.played, start=1)
        ]
        await interaction.response.send_message("\n".join(lines)[:2000])

    @app_commands.command(name="effect", description="Bass, nightcore, or off")
    @app_commands.describe(choice="off, bass, or nightcore")
    @app_commands.choices(choice=[
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="bass", value="bass"),
        app_commands.Choice(name="nightcore", value="nightcore"),
    ])
    async def effect(self, interaction: discord.Interaction, choice: str):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return
        queue = self.get_queue(interaction.guild_id)
        queue.effect = choice if choice in ("off", "bass", "nightcore") else "off"
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice:
            self.restart_current(voice, interaction, seek=0)
        await interaction.response.send_message(f"Effect: {queue.effect}. Yare Yare...")

    @app_commands.command(name="clear_queue", description="Clear the current queue")
    async def clear_queue(self, interaction: discord.Interaction):
        self.clear_queue_state(interaction.guild_id)
        await interaction.response.send_message("Queue cleared, Yare Yare.", ephemeral=True)

    @app_commands.command(name="remove_last", description="Remove the last song in the queue")
    async def remove_last(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        song = queue.remove_last()
        if song:
            self.persist_queue(interaction.guild_id)
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
        # Alone in the bot's channel: wait IDLE_LEAVE_SECONDS, then disconnect.
        # Someone joining that channel cancels the wait.
        guild = member.guild
        if guild is None:
            return
        if (
            self.client.user
            and member.id == self.client.user.id
            and before.channel is not None
            and after.channel is None
        ):
            self._maybe_reconnect(guild, before.channel)
        voice = guild.voice_client
        if not voice or not voice.channel:
            self._cancel_idle(guild.id)
            return
        channel = voice.channel
        involved = (
            before.channel is not None and before.channel.id == channel.id
        ) or (after.channel is not None and after.channel.id == channel.id)
        if not involved:
            return
        if len(channel.members) > 1:
            self._cancel_idle(guild.id)
        else:
            self._schedule_idle(guild.id, channel.id)

    @playlist.command(name="save", description="Save the current queue under a name")
    @app_commands.describe(name="Playlist name")
    async def playlist_save(self, interaction: discord.Interaction, name: str):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return
        tracks = tracks_from_queue(self.get_queue(interaction.guild_id))
        error = self.library.save_playlist(interaction.guild_id, name, tracks)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        self.library.save(library_path())
        saved = self.library.get_playlist(interaction.guild_id, name)
        label = saved["name"] if saved else name.strip()
        await interaction.response.send_message(
            f"Saved **{label}** ({len(tracks)} songs). Yare Yare..."
        )

    @playlist.command(name="play", description="Add a saved playlist onto the queue")
    @app_commands.describe(name="Playlist name")
    async def playlist_play(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        if not interaction.user.voice:
            await interaction.followup.send(
                "Get in a voice channel first, Teme.", ephemeral=True
            )
            return
        saved = self.library.get_playlist(interaction.guild_id, name)
        tracks = (saved or {}).get("tracks") or []
        if not saved or not tracks:
            await interaction.followup.send(
                "No playlist with that name, Teme.", ephemeral=True
            )
            return

        # Instant: stored title/url/thumbnail only. Streams resolve lazily at play/preload.
        songs = songs_from_tracks(tracks, self.ffmpeg_options, interaction.user)
        if not songs:
            await interaction.followup.send("Can't play that playlist, Teme.")
            return

        channel = interaction.user.voice.channel
        started = None
        first_failed = False
        try:
            lock = self.get_guild_lock(interaction.guild_id)
            async with lock:
                voice = discord.utils.get(
                    self.client.voice_clients, guild=interaction.guild
                )
                if not voice or not voice.is_connected():
                    voice = await channel.connect()
                queue = self.get_queue(interaction.guild_id)
                starting = not voice.is_playing() and not voice.is_paused()
                for song in songs:
                    queue.add(song)
                started = queue.pop_next() if starting else None
                if started:
                    try:
                        await self.client.change_presence(
                            activity=discord.Game(name=started.title)
                        )
                    except Exception as e:
                        log.warning("playlist presence failed: %s", e)
                    try:
                        await self.begin_playback(voice, started, interaction)
                    except Exception as e:
                        log.warning("playlist first track failed: %s", e)
                        first_failed = True
                else:
                    self.persist_queue(interaction.guild_id)
                    self.schedule_preload(interaction.guild_id)
        except Exception as e:
            log.warning("playlist play failed: %s", e)
            await interaction.followup.send("Can't play that playlist, Teme.")
            return

        skipped = len(tracks) - len(songs)
        label = saved.get("name") or name.strip()
        if first_failed:
            # first entry was dead; play_next skips to the next playable one and announces it
            await self.play_next(interaction)
            started = None
        if started:
            await interaction.followup.send(
                embed=self.song_embed(started, interaction, "Now Playing..."),
                view=QueueControlView(
                    self, interaction.guild_id, show_queue_button=True
                ),
            )
            if len(songs) > 1 or skipped:
                extra = f"Queued {len(songs) - 1} more from **{label}**."
                if skipped:
                    extra += f" Skipped {skipped}."
                await interaction.followup.send(extra)
            return

        text = f"Added {len(songs)} songs from **{label}**. Yare Yare..."
        if skipped:
            text += f" Skipped {skipped}."
        await interaction.followup.send(text)

    async def _maybe_hint_restore(self, interaction: discord.Interaction):
        """Once per guild per process: tell users a pre-restart queue can be restored."""
        gid = interaction.guild_id
        saved = self._restorable.get(gid)
        if not saved or gid in self._notified_restorable:
            return
        self._notified_restorable.add(gid)
        count = len(saved.get("tracks") or [])
        await self._announce(
            interaction,
            f"A queue from before my restart is saved ({count} songs). "
            "Run `/restore_queue` to bring it back, Teme.",
        )

    @app_commands.command(
        name="restore_queue",
        description="Restore the queue saved before the bot restarted",
    )
    async def restore_queue(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        saved = self._restorable.get(gid)
        if not saved:
            await interaction.response.send_message(
                "No saved queue to restore, Teme.", ephemeral=True
            )
            return
        if not interaction.user.voice:
            await interaction.response.send_message(
                "Get in a voice channel first, Teme.", ephemeral=True
            )
            return
        await interaction.response.defer()
        songs = songs_from_tracks(
            saved.get("tracks") or [], self.ffmpeg_options, interaction.user
        )
        if not songs:
            self._restorable.pop(gid, None)
            await interaction.followup.send("The saved queue was empty, Teme.")
            return
        channel = interaction.user.voice.channel
        started = None
        first_failed = False
        try:
            async with self.get_guild_lock(gid):
                voice = discord.utils.get(self.client.voice_clients, guild=interaction.guild)
                if not voice or not voice.is_connected():
                    voice = await channel.connect()
                queue = self.get_queue(gid)
                starting = not voice.is_playing() and not voice.is_paused()
                if starting and saved.get("looping"):
                    queue.looping = True
                for song in songs:
                    queue.add(song)
                self._restorable.pop(gid, None)
                started = queue.pop_next() if starting else None
                if started:
                    self._set_presence(started.title)
                    try:
                        await self.begin_playback(voice, started, interaction)
                    except Exception as e:
                        log.warning("restore first track failed: %s", e)
                        first_failed = True
                else:
                    self.persist_queue(gid)
                    self.schedule_preload(gid)
        except Exception as e:
            log.exception("restore_queue failed: %s", e)
            await interaction.followup.send("Can't restore that queue, Teme.")
            return
        if first_failed:
            await self.play_next(interaction)
            started = None
        if started:
            await interaction.followup.send(
                embed=self.song_embed(started, interaction, "Now Playing..."),
                view=QueueControlView(self, gid, show_queue_button=True),
            )
            await interaction.followup.send(
                f"Restored {len(songs)} songs. Yare Yare..."
            )
            return
        await interaction.followup.send(f"Restored {len(songs)} songs. Yare Yare...")

    @playlist.command(name="list", description="List saved playlists")
    async def playlist_list(self, interaction: discord.Interaction):
        items = self.library.list_playlists(interaction.guild_id)
        if not items:
            await interaction.response.send_message(
                "No playlists saved, Teme.", ephemeral=True
            )
            return
        lines = [
            f"**{item.get('name', '?')}** — {len(item.get('tracks') or [])} songs"
            for item in items
        ]
        await interaction.response.send_message("\n".join(lines)[:2000])

    @playlist.command(name="delete", description="Delete a saved playlist")
    @app_commands.describe(name="Playlist name")
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        if not (name or "").strip():
            await interaction.response.send_message(
                "Give the playlist a name, Teme.", ephemeral=True
            )
            return
        existing = self.library.get_playlist(interaction.guild_id, name)
        if not existing or not self.library.delete_playlist(interaction.guild_id, name):
            await interaction.response.send_message(
                "No playlist with that name, Teme.", ephemeral=True
            )
            return
        self.library.save(library_path())
        await interaction.response.send_message(f"Deleted **{existing.get('name', name)}**.")

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
            vol = self.get_volume_percent(voice.channel.id) / 100.0
            source = discord.PCMVolumeTransformer(
                FFmpegPCMAudio(song_path), volume=vol
            )
            voice.play(
                source,
                after=lambda x=None: self.after_play(interaction),
            )
            await interaction.followup.send(
                f"Now playing: {filename}",
                view=QueueControlView(
                    self, interaction.guild_id, show_queue_button=True
                ),
            )
        except Exception as e:
            await interaction.followup.send(f"Error playing {filename}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(music_cog(bot))
