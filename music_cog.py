import asyncio
import json
import os
import random
import re
import subprocess
import time
import traceback
import urllib.parse
import urllib.request
from typing import Dict, Optional

import discord
import yt_dlp
from discord import app_commands, FFmpegPCMAudio, PCMVolumeTransformer
from discord.ext import commands

_EXPIRE_RE = re.compile(r"[?&]expire=(\d+)")


def stream_url_stale(url: Optional[str], now: float) -> bool:
    """Missing, a YouTube page, or a googlevideo URL past expire=."""
    if not url:
        return True
    if "googlevideo.com" in url:
        match = _EXPIRE_RE.search(url)
        if not match:
            return False
        return int(match.group(1)) <= now
    return "youtube.com" in url or "youtu.be" in url


_EFFECT_AF = {
    "bass": "bass=g=8",
    "nightcore": "asetrate=48000*1.25,aresample=48000",
}


def parse_seek(text: str) -> Optional[int]:
    """`90`, `1:30`, or `1:02:03` → seconds. None if it isn't a time."""
    parts = (text or "").strip().split(":")
    if not parts or len(parts) > 3:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        return None
    total = 0
    for n in nums:
        total = total * 60 + n
    return total


def build_ffmpeg_options(base: dict, effect: str = "off", seek: int = 0) -> dict:
    """Copy. Shared default stays put."""
    before = base.get("before_options") or ""
    if seek > 0:
        before = f"{before} -ss {int(seek)}".strip()
    options = base.get("options") or "-vn"
    af = _EFFECT_AF.get(effect or "")
    if af:
        options = f"{options} -af {af}"
    return {"before_options": before, "options": options}


def fetch_lyrics(title: str) -> str:
    """Plain lyrics from LRCLIB, else synced. Empty string if nothing matches."""

    def get(url: str):
        req = urllib.request.Request(
            url, headers={"User-Agent": "JotaroBot/1.0 (discord)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)

    def pick(data) -> str:
        if not isinstance(data, dict):
            return ""
        return (data.get("plainLyrics") or data.get("syncedLyrics") or "").strip()

    text = ""
    try:
        text = pick(get(
            "https://lrclib.net/api/get?" + urllib.parse.urlencode({"track_name": title})
        ))
    except Exception:
        text = ""
    if not text:
        try:
            data = get(
                "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": title})
            )
        except Exception:
            data = None
        if isinstance(data, list):
            for hit in data:
                text = pick(hit)
                if text:
                    break
    return text[:2000]


class TrackAudio(FFmpegPCMAudio):
    """Surface a non-zero ffmpeg exit. discord.py checks too early if the process has not reaped."""

    def read(self) -> bytes:
        data = super().read()
        if data or self._current_error is not None:
            return data
        proc = getattr(self, "_process", None)
        if proc is None or not hasattr(proc, "wait"):
            return data
        if proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                return data
            self._check_process_returncode()
        return data


class VolumeAudio(PCMVolumeTransformer):
    """Player reads _current_error on the wrapper; ffmpeg sets it on the inner source."""

    @property
    def _current_error(self):
        return getattr(self.original, "_current_error", None)


def volumes_path() -> str:
    return "/config/volumes.json" if os.path.isdir("/config") else "./volumes.json"


def library_path() -> str:
    return "/config/library.json" if os.path.isdir("/config") else "./library.json"


IDLE_LEAVE_SECONDS = 60

_VIDEO_ID_RE = re.compile(
    r"(?:[?&]v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{6,})"
)


def video_id(url: Optional[str]) -> str:
    if not url:
        return ""
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else ""


def pick_related_entry(
    entries: list,
    origin_url: str,
    skip_urls: set,
) -> Optional[dict]:
    """First search hit that is not the song we just finished or a recent play."""
    origin = video_id(origin_url)
    skip_ids = {video_id(url) for url in skip_urls}
    skip_ids.discard("")
    for entry in entries:
        if not entry:
            continue
        page = entry.get("webpage_url") or ""
        if not page:
            continue
        vid = video_id(page)
        if page in skip_urls or (vid and (vid == origin or vid in skip_ids)):
            continue
        return entry
    return None


def track_from_song(song: "SongInfo") -> Optional[dict]:
    if not song.url:
        return None
    return {
        "title": song.title or "Unknown",
        "url": song.url,
        "thumbnail": song.thumbnail or "",
    }


def tracks_from_queue(queue: "GuildQueue") -> list:
    songs = []
    if queue.current:
        songs.append(queue.current)
    songs.extend(queue.songs)
    tracks = []
    for song in songs:
        track = track_from_song(song)
        if track:
            tracks.append(track)
    return tracks


MAX_PLAYLIST_ENTRIES = 200
MAX_PLAY_FAILURES = 20  # consecutive unplayable songs skipped before play_next gives up
_DEAD_TITLES = ("[private video]", "[deleted video]")


def track_from_flat_entry(entry: Optional[dict]) -> Optional[dict]:
    """Flat yt-dlp playlist entry -> {title, url, thumbnail}. None for private/deleted/no URL."""
    if not isinstance(entry, dict):
        return None
    title = (entry.get("title") or "").strip()
    if title.lower() in _DEAD_TITLES:
        return None
    url = entry.get("webpage_url") or entry.get("url") or ""
    if not url.startswith("http"):
        if entry.get("id") and (entry.get("ie_key") or "").lower() == "youtube":
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        else:
            return None
    thumb = entry.get("thumbnail") or ""
    if not thumb:
        thumbs = [t for t in (entry.get("thumbnails") or []) if isinstance(t, dict)]
        thumb = (thumbs[-1].get("url") or "") if thumbs else ""
    return {"title": title or "Unknown", "url": url, "thumbnail": thumb}


def tracks_from_flat_entries(entries) -> list:
    tracks = []
    for entry in entries or []:
        track = track_from_flat_entry(entry)
        if track:
            tracks.append(track)
    return tracks


def songs_from_tracks(tracks: list, ffmpeg_options: dict, requester=None) -> list:
    """Stored/flat tracks -> unresolved SongInfo (empty audio_url; resolved lazily at play)."""
    songs = []
    for track in tracks or []:
        url = (track or {}).get("url") or ""
        if not url.startswith("http"):
            continue
        songs.append(
            SongInfo(
                track.get("title") or "Unknown",
                url,
                "",
                track.get("thumbnail") or "",
                ffmpeg_options,
                requester_id=getattr(requester, "id", None),
                requester_name=getattr(requester, "display_name", None),
            )
        )
    return songs


def clip_lines(lines: list, limit: int = 4000) -> str:
    """Join lines, cutting at `limit` chars with a '+N more' tail (embed descriptions cap at 4096)."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > limit - 30:
            out.append(f"…and {len(lines) - i} more")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


class GuildLibrary:
    """Per-guild saved playlists and the autoplay toggle. JSON on disk."""

    def __init__(self, guilds: Optional[dict] = None):
        self.guilds = guilds if isinstance(guilds, dict) else {}

    @classmethod
    def load(cls, path: str) -> "GuildLibrary":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return cls()
        return cls(data if isinstance(data, dict) else {})

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.guilds, f)

    def _bucket(self, guild_id: int) -> dict:
        key = str(guild_id)
        bucket = self.guilds.get(key)
        if not isinstance(bucket, dict):
            bucket = {"autoplay": False, "playlists": {}}
            self.guilds[key] = bucket
        if not isinstance(bucket.get("playlists"), dict):
            bucket["playlists"] = {}
        return bucket

    def autoplay(self, guild_id: int) -> bool:
        bucket = self.guilds.get(str(guild_id)) or {}
        return bool(bucket.get("autoplay")) if isinstance(bucket, dict) else False

    def toggle_autoplay(self, guild_id: int) -> bool:
        bucket = self._bucket(guild_id)
        bucket["autoplay"] = not bool(bucket.get("autoplay"))
        return bucket["autoplay"]

    def save_playlist(
        self, guild_id: int, name: str, tracks: list
    ) -> Optional[str]:
        clean = (name or "").strip()
        if not clean:
            return "Give the playlist a name, Teme."
        if len(clean) > 80:
            return "That name is too long, Teme."
        if not tracks:
            return "Queue is empty, Teme."
        self._bucket(guild_id)["playlists"][clean.lower()] = {
            "name": clean,
            "tracks": tracks,
        }
        return None

    def get_playlist(self, guild_id: int, name: str) -> Optional[dict]:
        clean = (name or "").strip().lower()
        if not clean:
            return None
        playlists = self._bucket(guild_id)["playlists"]
        found = playlists.get(clean)
        return found if isinstance(found, dict) else None

    def delete_playlist(self, guild_id: int, name: str) -> bool:
        clean = (name or "").strip().lower()
        if not clean:
            return False
        playlists = self._bucket(guild_id)["playlists"]
        if clean not in playlists:
            return False
        del playlists[clean]
        return True

    def list_playlists(self, guild_id: int) -> list:
        playlists = self._bucket(guild_id)["playlists"]
        items = [item for item in playlists.values() if isinstance(item, dict)]
        items.sort(key=lambda item: str(item.get("name", "")).lower())
        return items


class SongInfo:
    """Represents a song in the queue with lazy audio loading."""

    def __init__(
        self,
        title: str,
        url: str,
        audio_url: str,
        thumbnail: str,
        ffmpeg_options: dict,
        requester_id: Optional[int] = None,
        requester_name: Optional[str] = None,
    ):
        self.title = title
        self.url = url
        self.audio_url = audio_url
        self.thumbnail = thumbnail
        self.ffmpeg_options = ffmpeg_options
        self.requester_id = requester_id
        self.requester_name = requester_name
        self.playback_retried = False
        self._preload: Optional[asyncio.Task] = None
        self._audio: Optional[FFmpegPCMAudio] = None

    @property
    def audio(self) -> FFmpegPCMAudio:
        if self._audio is None:
            self._audio = TrackAudio(self.audio_url, **self.ffmpeg_options)
        return self._audio


class GuildQueue:
    """Manages the music queue and skip/stop flags for a single guild."""

    def __init__(self):
        self.songs: list[SongInfo] = []
        self.history: list[SongInfo] = []
        self.played: list[tuple[str, str]] = []  # (title, url), newest first
        self.current: Optional[SongInfo] = None
        self.skipping = False
        self.stopping = False
        self.looping = False
        self.effect = "off"
        self.play_gen = 0

    def add(self, song: SongInfo) -> int:
        self.songs.append(song)
        return len(self.songs)

    def pop_next(self) -> Optional[SongInfo]:
        if self.looping and self.current is not None:
            self.history.append(self.current)

        if not self.songs and self.looping and self.history:
            self.songs = self.history
            self.history = []

        if self.songs:
            self.current = self.songs.pop(0)
            return self.current
        self.current = None
        return None

    def clear(self):
        self.songs.clear()
        self.history.clear()
        self.current = None

    def remove_last(self) -> Optional[SongInfo]:
        if self.songs:
            return self.songs.pop()
        return None

    def remove_at(self, index: int) -> Optional[SongInfo]:
        """1-based pending index. None if empty or out of range."""
        i = index - 1
        if i < 0 or i >= len(self.songs):
            return None
        return self.songs.pop(i)

    def note_played(self, song: SongInfo):
        self.played.insert(0, (song.title, song.url))
        del self.played[10:]

    def requeue_played(self, index: int, ffmpeg_options: dict) -> Optional[SongInfo]:
        """1 = most recent. Append a copy onto the pending queue."""
        i = index - 1
        if i < 0 or i >= len(self.played):
            return None
        title, url = self.played[i]
        song = SongInfo(title, url, "", "", ffmpeg_options)
        self.add(song)
        return song

    def move_up(self, index: int) -> bool:
        """1-based pending index. Swap with previous."""
        i = index - 1
        if i <= 0 or i >= len(self.songs):
            return False
        self.songs[i - 1], self.songs[i] = self.songs[i], self.songs[i - 1]
        return True

    def move_down(self, index: int) -> bool:
        """1-based pending index. Swap with next."""
        i = index - 1
        if i < 0 or i >= len(self.songs) - 1:
            return False
        self.songs[i], self.songs[i + 1] = self.songs[i + 1], self.songs[i]
        return True

    def move_to_front(self, index: int) -> bool:
        """1-based pending index. Move song to position 1."""
        i = index - 1
        if i <= 0 or i >= len(self.songs):
            return False
        self.songs.insert(0, self.songs.pop(i))
        return True

    def shuffle(self):
        random.shuffle(self.songs)

    def __len__(self) -> int:
        return len(self.songs)

    def __bool__(self) -> bool:
        return bool(self.songs)


class QueueControlView(discord.ui.View):
    """Reorder the pending queue and control playback from one message.

    Buttons listen for 180 seconds, then Discord stops dispatching them.
    Run /queue or /nowplaying again (or start another song) for a fresh row.
    """

    def __init__(
        self,
        cog: "music_cog",
        guild_id: int,
        *,
        show_queue_button: bool = False,
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.selected = 1
        if not show_queue_button:
            self.remove_item(self.queue_btn)
        queue = self.cog.get_queue(guild_id)
        self._set_queue_controls(bool(queue.songs))
        self._sync_pause_label()
        self._sync_autoplay_label()

    def _layout_playback(self, row: int):
        for item in (
            self.pause_btn,
            self.skip_btn,
            self.stop_btn,
            self.queue_btn,
            self.autoplay_btn,
        ):
            item.row = row

    def _set_queue_controls(self, enabled: bool):
        move_items = (self.move_up_btn, self.move_down_btn, self.to_front_btn)
        if not enabled:
            for item in list(self.children):
                if isinstance(item, discord.ui.Select) or item in move_items:
                    self.remove_item(item)
            self._layout_playback(0)
            return

        for item in move_items:
            if item not in self.children:
                self.add_item(item)
        self._layout_playback(2)
        self._rebuild_select()

    def _sync_pause_label(self):
        guild = self.cog.client.get_guild(self.guild_id)
        voice = guild.voice_client if guild else None
        self.pause_btn.label = "Resume" if voice and voice.is_paused() else "Pause"

    def _sync_autoplay_label(self):
        on = bool(self.cog.is_autoplay(self.guild_id))
        self.autoplay_btn.label = "Autoplay: On" if on else "Autoplay: Off"
        self.autoplay_btn.style = (
            discord.ButtonStyle.success if on else discord.ButtonStyle.secondary
        )

    def _rebuild_select(self):
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        queue = self.cog.get_queue(self.guild_id)
        options = [
            discord.SelectOption(
                label=f"{i}. {song.title}"[:100],
                value=str(i),
                description=(song.requester_name or "unknown")[:100],
            )
            for i, song in enumerate(queue.songs[:25], start=1)
        ]
        if not options:
            return

        select = discord.ui.Select(
            placeholder="Pick a song to move...",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

        async def on_select(interaction: discord.Interaction):
            if err := self.cog.same_vc_error(interaction):
                await interaction.response.send_message(err, ephemeral=True)
                return
            self.selected = int(select.values[0])
            await interaction.response.defer()

        select.callback = on_select
        self.add_item(select)

    async def _move(self, interaction: discord.Interaction, action: str):
        if err := self.cog.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.cog.get_queue(self.guild_id)
        if action == "up":
            ok = queue.move_up(self.selected)
            if ok and self.selected > 1:
                self.selected -= 1
        elif action == "down":
            ok = queue.move_down(self.selected)
            if ok:
                self.selected += 1
        else:
            ok = queue.move_to_front(self.selected)
            if ok:
                self.selected = 1

        if not ok:
            await interaction.response.send_message(
                "Can't move that, Teme.", ephemeral=True
            )
            return

        self.cog.schedule_preload(self.guild_id)
        self._set_queue_controls(bool(queue.songs))
        embed = self.cog.build_queue_embed(queue)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Move up", style=discord.ButtonStyle.secondary, row=1)
    async def move_up_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._move(interaction, "up")

    @discord.ui.button(label="Move down", style=discord.ButtonStyle.secondary, row=1)
    async def move_down_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._move(interaction, "down")

    @discord.ui.button(label="To front", style=discord.ButtonStyle.primary, row=1)
    async def to_front_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._move(interaction, "front")

    async def _refuse(self, interaction: discord.Interaction) -> bool:
        if err := self.cog.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return True
        return False

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, row=2)
    async def pause_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        voice = interaction.guild.voice_client
        if voice and voice.is_playing():
            voice.pause()
            text = "Paused, Baka Yaro"
        elif voice and voice.is_paused():
            voice.resume()
            text = "Resuming..."
        else:
            await interaction.response.send_message(
                "No audio is playing, Teme.", ephemeral=True
            )
            return
        self._sync_pause_label()
        await interaction.response.send_message(text, ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception as e:
            print(f"pause button refresh failed: {e}")

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, row=2)
    async def skip_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        voice = interaction.guild.voice_client
        queue = self.cog.get_queue(self.guild_id)
        if not voice or not voice.is_playing():
            await interaction.response.send_message(
                "Nothing is playing..", ephemeral=True
            )
            return
        if not queue:
            await interaction.response.send_message(
                "No songs in queue", ephemeral=True
            )
            return
        queue.skipping = True
        await interaction.response.send_message("Skipping..", ephemeral=True)
        voice.stop()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=2)
    async def stop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        voice = interaction.guild.voice_client
        if voice and (voice.is_paused() or voice.is_playing()):
            queue = self.cog.get_queue(self.guild_id)
            queue.stopping = True
            queue.clear()
            voice.stop()
            await voice.disconnect()
            await self.cog.client.change_presence(
                status=discord.Status.do_not_disturb
            )
            await interaction.response.send_message("Yare Yare... I'll be back")
            return
        await interaction.response.send_message(
            "Nothing to stop, Teme.", ephemeral=True
        )

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, row=2)
    async def queue_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        queue = self.cog.get_queue(self.guild_id)
        if not queue.current and not queue.songs:
            await interaction.response.send_message(
                "Queue is empty, Teme.", ephemeral=True
            )
            return
        embed = self.cog.build_queue_embed(queue)
        view = QueueControlView(self.cog, self.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @discord.ui.button(label="Autoplay: Off", style=discord.ButtonStyle.secondary, row=2)
    async def autoplay_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        on = self.cog.toggle_autoplay(self.guild_id)
        self._sync_autoplay_label()
        text = "Autoplay ON. Yare Yare..." if on else "Autoplay OFF. Owari da."
        await interaction.response.send_message(text, ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception as e:
            print(f"autoplay button refresh failed: {e}")


class music_cog(commands.Cog):
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

        self.yt_dl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio',
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
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn -bufsize 1M',
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
        self.library.save(library_path())
        return on

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
        queue.clear()
        await voice.disconnect()
        try:
            await self.client.change_presence(status=discord.Status.do_not_disturb)
        except Exception as e:
            print(f"idle leave presence failed: {e}")
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

    def play_audio(
        self,
        voice: discord.VoiceClient,
        song: SongInfo,
        interaction: discord.Interaction,
        seek: int = 0,
    ):
        # FFmpegPCMAudio cannot be replayed after stop; always build a fresh source.
        song._audio = None
        queue = self.get_queue(voice.guild.id)
        opts = build_ffmpeg_options(song.ffmpeg_options, queue.effect, seek)
        vol = self.get_volume_percent(voice.channel.id) / 100.0
        source = VolumeAudio(TrackAudio(song.audio_url, **opts), volume=vol)
        gen = queue.play_gen
        voice.play(
            source,
            after=lambda error, s=song, g=gen: self.after_play(interaction, error, s, g),
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
        queue.play_gen += 1
        voice.stop()
        self.play_audio(voice, song, interaction, seek=seek)
        return True

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

    async def _announce(self, interaction: discord.Interaction, *args, **kwargs):
        """Best-effort announce; interaction followups expire (~15m) and must not block play."""
        try:
            await interaction.followup.send(*args, **kwargs)
            return
        except Exception as e:
            print(f"announce followup failed: {e}")
        channel = interaction.channel
        if channel is None and interaction.guild:
            channel = self.find_general_channel(interaction.guild)
        if channel is not None:
            try:
                await channel.send(*args, **kwargs)
            except Exception as e:
                print(f"announce channel send failed: {e}")

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
            print(f"playback error: {error}")
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
            print(f"refresh stream failed: {e}")
            return False
        if info and info.get("_type") == "playlist":
            info = next((e for e in (info.get("entries") or []) if e), None)
        audio = (info or {}).get("url") or ""
        if not audio:
            return False
        song.audio_url = audio
        song._audio = None
        return True

    async def _ensure_stream(self, song: SongInfo):
        if not stream_url_stale(song.audio_url, time.time()):
            return
        if song._preload is not None and not song._preload.done():
            await song._preload
        if stream_url_stale(song.audio_url, time.time()):
            await self._refresh_stream(song)

    async def begin_playback(
        self,
        voice: discord.VoiceClient,
        song: SongInfo,
        interaction: discord.Interaction,
    ):
        song.playback_retried = False
        await self._ensure_stream(song)
        if not song.audio_url:
            # unavailable/private/region-locked: let the caller skip it
            raise RuntimeError(f"no stream for {song.url}")
        try:
            self.play_audio(voice, song, interaction)
        except Exception as e:
            print(f"play_audio failed: {e}")
            if song.playback_retried:
                raise
            song.playback_retried = True
            await self._refresh_stream(song)
            self.play_audio(voice, song, interaction)
        self.get_queue(interaction.guild_id).note_played(song)
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
            print(f"retry playback failed: {e}")
            await self.play_next(interaction)

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
                if not voice:
                    return

                finished = queue.current
                song = queue.pop_next()
                if song:
                    try:
                        await self.client.change_presence(
                            activity=discord.Game(name=song.title)
                        )
                    except Exception as e:
                        print(f"play_next presence failed: {e}")
                    # Play first so expired interaction tokens cannot leave the bot silent.
                    try:
                        await self.begin_playback(voice, song, interaction)
                    except Exception as e:
                        print(f"begin_playback failed: {e}")
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
                    print(f"autoplay lookup failed: {e}")
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
                            try:
                                await self.client.change_presence(
                                    activity=discord.Game(name=song.title)
                                )
                            except Exception as e:
                                print(f"play_next presence failed: {e}")
                            try:
                                await self.begin_playback(voice, song, interaction)
                            except Exception as e:
                                print(f"autoplay playback failed: {e}")
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
                try:
                    await self.client.change_presence(
                        status=discord.Status.do_not_disturb
                    )
                except Exception as e:
                    print(f"play_next presence failed: {e}")
                await self._announce(
                    interaction, "Owari Da... no more songs in the queue"
                )

        except Exception as e:
            print(f"An error occurred during play_next: {e}")
            try:
                await self.client.change_presence(status=discord.Status.do_not_disturb)
            except Exception:
                pass
            traceback.print_exc()

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
                print(f"autoplay extract failed: {e}")
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
            print(f"resolve_source DownloadError: {e}")
            return None
        except Exception as e:
            print(f"resolve_source failed: {e}")
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
        return SongInfo(
            title=info.get('title', 'Unknown'),
            url=webpage_url or info.get('webpage_url', ''),
            audio_url=info.get('url', ''),
            thumbnail=info.get('thumbnail', ''),
            ffmpeg_options=self.ffmpeg_options,
            requester_id=requester.id if requester else None,
            requester_name=requester.display_name if requester else None,
        )

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
                print(f"play DownloadError: {e}")
                await interaction.followup.send("Can't play that, Teme.")
                responded = True
                return
            except Exception as e:
                print(f"play extract_info failed: {e}")
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
                            print(f"playlist first track failed: {e}")
                            first_failed = True
                    else:
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
        except Exception as e:
            print(f"play failed: {e}")
            traceback.print_exc()
        finally:
            if not responded:
                try:
                    await interaction.followup.send("Can't play that, Teme.")
                except Exception:
                    pass

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
        self.schedule_preload(interaction.guild_id)
        await interaction.response.send_message("Shuffled the queue, Yare Yare...")

    @app_commands.command(name="loop", description="Toggle queue loop")
    async def loop(self, interaction: discord.Interaction):
        if err := self.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.get_queue(interaction.guild_id)
        queue.looping = not queue.looping
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
        # Alone in the bot's channel: wait IDLE_LEAVE_SECONDS, then disconnect.
        # Someone joining that channel cancels the wait.
        guild = member.guild
        if guild is None:
            return
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
                        print(f"playlist presence failed: {e}")
                    try:
                        await self.begin_playback(voice, started, interaction)
                    except Exception as e:
                        print(f"playlist first track failed: {e}")
                        first_failed = True
                else:
                    self.schedule_preload(interaction.guild_id)
        except Exception as e:
            print(f"playlist play failed: {e}")
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
