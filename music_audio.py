"""FFmpeg / yt-dlp audio helpers: sources, option builders, URL freshness, lyrics."""
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Optional

from discord import FFmpegPCMAudio, PCMVolumeTransformer

log = logging.getLogger(__name__)

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


# Opus-first: itag 251 (~130-160k VBR) beats AAC 140 (128k); fall back to any audio.
YTDL_FORMAT = "bestaudio[acodec=opus]/bestaudio/best"


# googlevideo fingerprints the HTTP client, and ffmpeg's default "Lavf/.." UA gets
# answered with "HTTP error 403 Forbidden" (discord.py then reports FFmpeg exit code 8
# and the track is skipped). Send the same browser UA yt-dlp extracts with, so the
# extraction request and the media fetch look like one client.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# Input-side read-ahead for HTTP (googlevideo). Reconnect keeps a dropped CDN
# connection alive mid-song; a bounded rw_timeout (microseconds) makes a stalled
# read fail fast so after_play's refresh+resume recovers instead of leaving long
# silence. -thread_queue_size is the demuxer read-ahead. Do NOT add -buffer_size:
# it is a tcp/socket option, and http inputs reject it ("Option buffer_size not
# found") which aborts opening the stream. Do not add +nobuffer either — that
# trades buffer for latency and is what makes Discord bots stutter.
FFMPEG_BEFORE = (
    f'-user_agent "{USER_AGENT}" '
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    "-reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx "
    "-rw_timeout 8000000 -thread_queue_size 512 "
    "-fflags +genpts+discardcorrupt "
    "-nostdin -probesize 1M -analyzeduration 1M"
)


# discord.py fixes the PCM output itself (-f s16le -ar 48000 -ac 2), which is what
# pins a source at any sample rate to Discord's 48kHz — so we only strip non-audio
# streams here and let -af effects get appended per play. Repeating -ar/-ac would
# just make ffmpeg warn about duplicate options on every track.
FFMPEG_OPTIONS = "-vn -sn -dn"

PRELOAD_DEPTH = 2


def opus_bitrate(channel_bitrate: Optional[int]) -> int:
    """kbps for the encoder: never below 128, up to 256 when the channel allows it."""
    kbps = int(channel_bitrate or 0) // 1000
    return max(128, min(256, kbps))


def songs_to_preload(songs, now: float, depth: int = PRELOAD_DEPTH) -> list:
    """Upcoming queue entries whose stream URL is missing or expired."""
    return [
        song
        for song in list(songs)[:depth]
        if getattr(song, "url", None) and stream_url_stale(getattr(song, "audio_url", None), now)
    ]


class PlayClock:
    """Playback position estimate: seek offset + wall time not spent paused."""

    def __init__(self):
        self.offset = 0.0
        self.started: Optional[float] = None
        self.paused_at: Optional[float] = None

    def start(self, offset: float = 0.0, now: Optional[float] = None):
        self.offset = float(offset)
        self.started = time.monotonic() if now is None else now
        self.paused_at = None

    def pause(self, now: Optional[float] = None):
        if self.started is not None and self.paused_at is None:
            self.paused_at = time.monotonic() if now is None else now

    def resume(self, now: Optional[float] = None):
        if self.paused_at is not None and self.started is not None:
            now = time.monotonic() if now is None else now
            self.started += now - self.paused_at
            self.paused_at = None

    def position(self, now: Optional[float] = None) -> int:
        if self.started is None:
            return 0
        now = time.monotonic() if now is None else now
        end = self.paused_at if self.paused_at is not None else now
        return max(0, int(self.offset + end - self.started))


def should_reconnect(was_playing: bool, stopping: bool, humans_in_channel: int) -> bool:
    """Unexpected voice drop: only fight back if a song was live and someone is listening."""
    return bool(was_playing) and not stopping and humans_in_channel > 0


def should_advance(paused: bool, skipping: bool) -> bool:
    """A paused player never reads its source, so it cannot end naturally. An after-callback
    while paused means the voice client died (disconnect/stop): do not burn the next song."""
    return skipping or not paused


_STALE_YTDLP_HINTS = (
    "unable to extract",
    "sign in to confirm",
    "http error 403",
    "nsig",
    "signature",
    "player response",
    "po token",
)


def ytdlp_failure_hint(message: str) -> str:
    """Extra log text when an extraction error looks like an outdated yt-dlp."""
    low = (message or "").lower()
    if any(h in low for h in _STALE_YTDLP_HINTS):
        return " (looks like a stale yt-dlp; rebuild the image or run: pip install -U yt-dlp)"
    return ""


_EFFECT_AF = {
    "bass": "bass=g=8,alimiter=limit=0.95",
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


def ytdl_client_options() -> dict:
    """Client identity plus opt-in escape hatches for hosts YouTube answers with 403.

    Always pins the browser UA so yt-dlp's extraction and the ffmpeg media fetch agree.
    YTDLP_COOKIES points at a Netscape cookies.txt (the reliable fix once YouTube
    blocks the host's IP) and YTDLP_PLAYER_CLIENT overrides the YouTube client, e.g.
    "web_safari,tv". Both stay inert when unset, so a typo cannot poison every fetch.
    """
    opts = {"http_headers": {"User-Agent": USER_AGENT}}
    cookies = os.getenv("YTDLP_COOKIES", "").strip()
    if cookies:
        opts["cookiefile"] = cookies
        log.info("yt-dlp: using cookies from %s", cookies)
    client = os.getenv("YTDLP_PLAYER_CLIENT", "").strip()
    if client:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": [c.strip() for c in client.split(",") if c.strip()]
            }
        }
        log.info("yt-dlp: player_client=%s", client)
    return opts


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


class _StrictExitMixin:
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


class TrackAudio(_StrictExitMixin, FFmpegPCMAudio):
    """PCM path: decode, volume/effects, re-encode by discord.py."""


class VolumeAudio(PCMVolumeTransformer):
    """Player reads _current_error on the wrapper; ffmpeg sets it on the inner source."""

    @property
    def _current_error(self):
        return getattr(self.original, "_current_error", None)
