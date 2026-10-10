"""yt-dlp extraction, stream URL refresh/preload, autoplay lookup. Mixed into music_cog."""
import asyncio
import logging
import time
from typing import Optional

import discord
import yt_dlp

from music_audio import info_is_opus, stream_url_stale, ytdlp_failure_hint
from music_store import SongInfo, pick_related_entry

log = logging.getLogger(__name__)


class SourcesMixin:
    def schedule_preload(self, guild_id: int):
        # ponytail: preload only the next song, once; play_next re-extracts if this dies
        queue = self.get_queue(guild_id)
        if not queue.songs:
            return
        song = queue.songs[0]
        if not song.url or not stream_url_stale(song.audio_url, time.time()):
            return
        if song._preload is not None and not song._preload.done():
            return
        song._preload = asyncio.create_task(self._refresh_stream(song))

    async def _extract(self, url: str):
        async with self._extract_lock:
            return await asyncio.to_thread(self.ytdl.extract_info, url, False)

    async def _extract_flat(self, url: str):
        async with self._extract_lock:
            return await asyncio.to_thread(self.ytdl_flat.extract_info, url, False)

    async def _refresh_stream(self, song: SongInfo) -> bool:
        """Re-extract a direct stream from the stable webpage, never from audio_url."""
        if not song.url:
            return False
        try:
            info = await self._extract(song.url)
        except Exception as e:
            log.warning("refresh stream failed for %s: %s%s", song.url, e, ytdlp_failure_hint(str(e)))
            return False
        if info and info.get("_type") == "playlist":
            info = next((e for e in (info.get("entries") or []) if e), None)
        audio = (info or {}).get("url") or ""
        if not audio:
            return False
        song.audio_url = audio
        song.is_opus = info_is_opus(info)
        song._audio = None
        return True

    async def _ensure_stream(self, song: SongInfo):
        if not stream_url_stale(song.audio_url, time.time()):
            return
        if song._preload is not None and not song._preload.done():
            await song._preload
        if stream_url_stale(song.audio_url, time.time()):
            await self._refresh_stream(song)

    async def related_song(
        self, song: SongInfo, skip_urls: set
    ) -> Optional[SongInfo]:
        """YouTube search on the last title; skip that video and recent plays."""
        info = await self._extract(f"ytsearch5:{song.title}")
        entries = [entry for entry in ((info or {}).get("entries") or []) if entry]
        picked = pick_related_entry(entries, song.url, skip_urls)
        if not picked:
            return None
        page = picked.get("webpage_url") or ""
        if not page.startswith("http"):
            return None
        stream = picked.get("url") or ""
        if not stream or stream_url_stale(stream, time.time()):
            try:
                full = await self._extract(page)
            except Exception as e:
                log.warning("autoplay extract failed: %s%s", e, ytdlp_failure_hint(str(e)))
                return None
            if not full or full.get("_type") == "playlist":
                return None
            picked = full
        related = self.create_song_info(picked, page, None)
        related.requester_name = "Autoplay"
        return related

    async def resolve_source(self, query: str) -> Optional[str]:
        """Resolve a search query or URL to a playable webpage URL."""
        if query.startswith("http://") or query.startswith("https://"):
            return query

        cache_key = query.lower().strip()
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        try:
            # flat: we only need the video URL; play() resolves the stream once
            info = await self._extract_flat(f"ytsearch1:{query}")
        except yt_dlp.utils.DownloadError as e:
            log.warning("resolve_source DownloadError: %s%s", e, ytdlp_failure_hint(str(e)))
            return None
        except Exception as e:
            log.warning("resolve_source failed: %s%s", e, ytdlp_failure_hint(str(e)))
            return None

        entries = (info or {}).get("entries") or []
        entry = next((e for e in entries if e), None)
        if not entry:
            return None

        source = entry.get("webpage_url") or entry.get("url")
        if not source:
            return None

        if len(self._search_cache) >= self._cache_max_size:
            oldest_key = next(iter(self._search_cache))
            del self._search_cache[oldest_key]
        self._search_cache[cache_key] = source
        return source

    def create_song_info(
        self,
        info: dict,
        webpage_url: str = None,
        requester: discord.abc.User = None,
    ) -> SongInfo:
        song = SongInfo(
            title=info.get('title', 'Unknown'),
            url=webpage_url or info.get('webpage_url', ''),
            audio_url=info.get('url', ''),
            thumbnail=info.get('thumbnail', ''),
            ffmpeg_options=self.ffmpeg_options,
            requester_id=requester.id if requester else None,
            requester_name=requester.display_name if requester else None,
        )
        song.is_opus = info_is_opus(info)
        return song
