"""Queue, song and library (playlists, autoplay, saved queue) data. No Discord I/O."""
import asyncio
import json
import os
import random
import re
import tempfile
from typing import Optional

from discord import FFmpegPCMAudio

from music_audio import PlayClock, TrackAudio

def volumes_path() -> str:
    return "/config/volumes.json" if os.path.isdir("/config") else "./volumes.json"


def library_path() -> str:
    return "/config/library.json" if os.path.isdir("/config") else "./library.json"


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

    def dumps(self) -> str:
        return json.dumps(self.guilds)

    @staticmethod
    def write_atomic(path: str, text: str):
        """Temp file in the same dir, then os.replace: readers never see a half-written file."""
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".library-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def save(self, path: str):
        self.write_atomic(path, self.dumps())

    def set_saved_queue(self, guild_id: int, tracks: list, looping: bool = False):
        """Replace the guild's saved queue; an empty track list clears it."""
        if not tracks:
            self.clear_saved_queue(guild_id)
            return
        self._bucket(guild_id)["queue"] = {"tracks": list(tracks), "looping": bool(looping)}

    def get_saved_queue(self, guild_id: int) -> Optional[dict]:
        bucket = self.guilds.get(str(guild_id))
        saved = bucket.get("queue") if isinstance(bucket, dict) else None
        if isinstance(saved, dict) and isinstance(saved.get("tracks"), list) and saved["tracks"]:
            return saved
        return None

    def clear_saved_queue(self, guild_id: int) -> bool:
        """True if something was removed."""
        bucket = self.guilds.get(str(guild_id))
        if isinstance(bucket, dict) and "queue" in bucket:
            del bucket["queue"]
            return True
        return False

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
        self.is_opus = False  # set when yt-dlp resolved an Opus stream (passthrough-able)
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
        self.clock = PlayClock()

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
