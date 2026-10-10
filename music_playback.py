"""Playback engine: audio path choice, play/advance/retry, voice reconnect. Mixed into music_cog."""
import asyncio
import logging
from typing import Optional

import discord

from music_audio import (
    TrackAudio,
    TrackOpusAudio,
    VolumeAudio,
    build_ffmpeg_options,
    choose_audio_path,
    opus_bitrate,
    should_reconnect,
    ytdlp_failure_hint,
)
from music_store import MAX_PLAY_FAILURES, SongInfo
from music_views import QueueControlView

log = logging.getLogger(__name__)


class PlaybackMixin:
    def play_audio(
        self,
        voice: discord.VoiceClient,
        song: SongInfo,
        interaction: discord.Interaction,
        seek: int = 0,
    ):
        # FFmpeg sources cannot be replayed after stop; always build a fresh source.
        song._audio = None
        queue = self.get_queue(voice.guild.id)
        vol_pct = self.get_volume_percent(voice.channel.id)
        opts = build_ffmpeg_options(song.ffmpeg_options, queue.effect, seek)
        path = choose_audio_path(vol_pct, queue.effect, seek, song.is_opus)
        if path == "opus":
            source = TrackOpusAudio(song.audio_url, **opts)
        else:
            source = VolumeAudio(TrackAudio(song.audio_url, **opts), volume=vol_pct / 100.0)
        log.debug("play %r via %s path (vol=%s effect=%s seek=%s)",
                  song.title, path, vol_pct, queue.effect, seek)
        gen = queue.play_gen
        queue.clock.start(seek)
        voice.play(
            source,
            after=lambda error, s=song, g=gen: self.after_play(interaction, error, s, g),
            bitrate=opus_bitrate(getattr(voice.channel, "bitrate", None)),
            expected_packet_loss=0.05,
        )

    def restart_current(
        self,
        voice: discord.VoiceClient,
        interaction: discord.Interaction,
        seek: int = 0,
    ) -> bool:
        """Rebuild the current source. Bump play_gen so the old after-callback does not skip."""
        queue = self.get_queue(interaction.guild_id)
        song = queue.current
        if not song or not voice or not (voice.is_playing() or voice.is_paused()):
            return False
        was_paused = voice.is_paused()
        queue.play_gen += 1
        voice.stop()
        self.play_audio(voice, song, interaction, seek=seek)
        if was_paused:
            voice.pause()
            queue.clock.pause()
        return True

    async def _announce(self, interaction: discord.Interaction, *args, **kwargs):
        """Best-effort announce; interaction followups expire (~15m) and must not block play."""
        try:
            await interaction.followup.send(*args, **kwargs)
            return
        except Exception as e:
            log.warning("announce followup failed: %s", e)
        channel = interaction.channel
        if channel is None and interaction.guild:
            channel = self.find_general_channel(interaction.guild)
        if channel is not None:
            try:
                await channel.send(*args, **kwargs)
            except Exception as e:
                log.warning("announce channel send failed: %s", e)

    def after_play(
        self,
        interaction: discord.Interaction,
        error: Optional[BaseException] = None,
        song: Optional[SongInfo] = None,
        gen: Optional[int] = None,
    ):
        # stopping: queue cleared — do not advance.
        # ffmpeg error: refresh this song once from song.url, then advance.
        # skipping or natural end: clear skip flag, then play_next.
        # gen mismatch: seek/effect rebuilt this source; ignore the old player.
        queue = self.get_queue(interaction.guild_id)
        if gen is not None and gen != queue.play_gen:
            return
        if error:
            log.warning("playback error: %s", error)
        if queue.stopping:
            queue.stopping = False
            queue.skipping = False
            return
        if (
            error
            and song is not None
            and song is queue.current
            and not song.playback_retried
            and not queue.skipping
        ):
            song.playback_retried = True
            asyncio.run_coroutine_threadsafe(
                self._retry_playback(interaction, song),
                self.client.loop,
            )
            return
        if queue.skipping:
            queue.skipping = False
        asyncio.run_coroutine_threadsafe(
            self.play_next(interaction),
            self.client.loop,
        )

    async def begin_playback(
        self,
        voice: discord.VoiceClient,
        song: SongInfo,
        interaction: discord.Interaction,
    ):
        song.playback_retried = False
        self._last_interaction[interaction.guild_id] = interaction
        await self._ensure_stream(song)
        if not song.audio_url:
            # unavailable/private/region-locked: let the caller skip it
            raise RuntimeError(f"no stream for {song.url}")
        try:
            self.play_audio(voice, song, interaction)
        except Exception as e:
            log.warning("play_audio failed: %s", e)
            if song.playback_retried:
                raise
            song.playback_retried = True
            await self._refresh_stream(song)
            self.play_audio(voice, song, interaction)
        self.get_queue(interaction.guild_id).note_played(song)
        self.persist_queue(interaction.guild_id)
        self.schedule_preload(interaction.guild_id)

    async def _retry_playback(self, interaction: discord.Interaction, song: SongInfo):
        queue = self.get_queue(interaction.guild_id)
        voice = interaction.guild.voice_client if interaction.guild else None
        if queue.current is not song or queue.stopping or queue.skipping:
            return
        if not voice or not voice.is_connected():
            await self.play_next(interaction)
            return
        if not await self._refresh_stream(song):
            await self.play_next(interaction)
            return
        if queue.current is not song or queue.stopping or queue.skipping:
            return
        try:
            self.play_audio(voice, song, interaction)
            self.schedule_preload(interaction.guild_id)
        except Exception as e:
            log.warning("retry playback failed: %s", e)
            await self.play_next(interaction)

    def _set_presence(self, title: str):
        async def go():
            try:
                await self.client.change_presence(activity=discord.Game(name=title))
            except Exception as e:
                log.warning("presence failed: %s", e)

        asyncio.create_task(go())

    async def play_next(self, interaction: discord.Interaction, failures: int = 0):
        try:
            song = None
            failed = False
            want_autoplay = False
            finished = None
            lock = self.get_guild_lock(interaction.guild_id)
            async with lock:
                queue = self.get_queue(interaction.guild_id)
                voice = interaction.guild.voice_client if interaction.guild else None
                if not voice or not voice.is_connected():
                    # dropped/disconnected: leave the queue intact (reconnect path resumes it)
                    return

                finished = queue.current
                song = queue.pop_next()
                if song:
                    # Play first so expired interaction tokens cannot leave the bot silent.
                    # Presence is a gateway round-trip; never make the audio wait on it.
                    self._set_presence(song.title)
                    try:
                        await self.begin_playback(voice, song, interaction)
                    except Exception as e:
                        log.warning("begin_playback failed: %s", e)
                        failed = True
                elif (
                    finished
                    and finished.url
                    and self.is_autoplay(interaction.guild_id)
                    and not queue.stopping
                ):
                    want_autoplay = True

            if failed:
                # ponytail: bounded so a looping dead track cannot recurse forever; high enough
                # that a playlist with a few unavailable videos (found lazily) still plays on
                if failures == 0:
                    await self._announce(
                        interaction,
                        f"Can't load **{song.title}**, skipping it, Teme."
                        if song else "Can't load that song, skipping it, Teme.",
                    )
                if failures < MAX_PLAY_FAILURES - 1:
                    await self.play_next(interaction, failures + 1)
                return

            if want_autoplay and song is None and finished is not None:
                skip_urls = {finished.url}
                skip_urls.update(
                    url for _, url in self.get_queue(interaction.guild_id).played
                )
                related = None
                try:
                    related = await self.related_song(finished, skip_urls)
                except Exception as e:
                    log.warning("autoplay lookup failed: %s%s", e, ytdlp_failure_hint(str(e)))
                if related:
                    async with lock:
                        queue = self.get_queue(interaction.guild_id)
                        voice = (
                            interaction.guild.voice_client
                            if interaction.guild
                            else None
                        )
                        busy = bool(
                            voice and (voice.is_playing() or voice.is_paused())
                        )
                        if (
                            voice
                            and voice.is_connected()
                            and not busy
                            and not queue.current
                            and not queue.songs
                            and not queue.stopping
                        ):
                            queue.current = related
                            song = related
                            self._set_presence(song.title)
                            try:
                                await self.begin_playback(voice, song, interaction)
                            except Exception as e:
                                log.warning("autoplay playback failed: %s", e)
                                failed = True
                                song = None

            if failed:
                if failures < MAX_PLAY_FAILURES - 1:
                    await self.play_next(interaction, failures + 1)
                return

            queue = self.get_queue(interaction.guild_id)
            voice = interaction.guild.voice_client if interaction.guild else None
            busy = bool(voice and (voice.is_playing() or voice.is_paused()))
            if song:
                await self._announce(
                    interaction,
                    embed=self.song_embed(song, interaction, "Now Playing..."),
                    view=QueueControlView(
                        self, interaction.guild_id, show_queue_button=True
                    ),
                )
            elif not queue.stopping and not busy and not queue.current and not queue.songs:
                self.clear_queue_state(interaction.guild_id)  # finished: nothing to restore
                try:
                    await self.client.change_presence(
                        status=discord.Status.do_not_disturb
                    )
                except Exception as e:
                    log.warning("play_next presence failed: %s", e)
                await self._announce(
                    interaction, "Owari Da... no more songs in the queue"
                )

        except Exception as e:
            log.exception("play_next failed: %s", e)
            try:
                await self.client.change_presence(status=discord.Status.do_not_disturb)
            except Exception:
                pass

    def _maybe_reconnect(self, guild: discord.Guild, channel: discord.abc.Connectable):
        """Bot left voice. Deliberate exits (/leave, /stop, idle) clear the queue first, so a
        remaining current song means the drop was unexpected."""
        gid = guild.id
        queue = self.get_queue(gid)
        humans = sum(1 for m in channel.members if not m.bot)
        song = queue.current
        if (
            gid in self._reconnecting
            or not should_reconnect(song is not None, queue.stopping, humans)
            or gid not in self._last_interaction
        ):
            return
        elapsed = queue.clock.position()
        log.warning(
            "Unexpected voice disconnect in guild %s (%r at ~%ss); trying to reconnect",
            gid, song.title, elapsed,
        )
        self._reconnecting.add(gid)
        queue.play_gen += 1  # ignore the dead player's after-callback
        asyncio.create_task(self._reconnect_voice(guild, channel, song, elapsed))

    async def _reconnect_voice(self, guild, channel, song: SongInfo, position: int):
        gid = guild.id
        queue = self.get_queue(gid)
        interaction = self._last_interaction.get(gid)
        try:
            for attempt, delay in enumerate((2, 5), start=1):
                await asyncio.sleep(delay)
                if queue.current is not song or queue.stopping:
                    return  # user stopped/cleared meanwhile
                if not any(not m.bot for m in channel.members):
                    log.info("Voice reconnect aborted in guild %s: channel empty", gid)
                    return
                stale = guild.voice_client
                try:
                    if stale is not None:
                        await stale.disconnect(force=True)
                    voice = await channel.connect()
                    await self._ensure_stream(song)
                    if not song.audio_url:
                        raise RuntimeError("no stream to resume")
                    self.play_audio(voice, song, interaction, seek=position)
                    log.info("Voice reconnected in guild %s (attempt %d); resumed %r at %ss",
                             gid, attempt, song.title, position)
                    await self._announce(interaction, "Got disconnected, I'm back. Yare Yare...")
                    return
                except Exception as e:
                    log.warning("Voice reconnect attempt %d failed in guild %s: %s", attempt, gid, e)
            log.error("Voice reconnect gave up in guild %s; saved queue kept for /restore_queue", gid)
            saved = self.library.get_saved_queue(gid)
            if saved:
                self._restorable[gid] = saved
                self._notified_restorable.discard(gid)
            queue.clear()  # in-memory only; library.json keeps the queue
        finally:
            self._reconnecting.discard(gid)
