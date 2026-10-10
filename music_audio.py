"""FFmpeg / yt-dlp audio helpers: sources, option builders, URL freshness, lyrics."""
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Optional

from discord import FFmpegOpusAudio, FFmpegPCMAudio, PCMVolumeTransformer

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


FFMPEG_BEFORE = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    "-nostdin -probesize 1M -analyzeduration 1M"
)


FFMPEG_OPTIONS = "-vn -sn -dn"


def opus_bitrate(channel_bitrate: Optional[int]) -> int:
    """kbps for the encoder: never below 128, up to 256 when the channel allows it."""
    kbps = int(channel_bitrate or 0) // 1000
    return max(128, min(256, kbps))


def info_is_opus(info: Optional[dict]) -> bool:
    """True when yt-dlp resolved an Opus audio stream (acodec starts with 'opus')."""
    return str((info or {}).get("acodec") or "").lower().startswith("opus")


def choose_audio_path(volume_pct: int, effect: str, seek: int, is_opus: bool) -> str:
    """'opus' = FFmpeg stream copy (no decode/re-encode); 'pcm' = decode + volume/effects."""
    if is_opus and int(volume_pct) == 100 and (effect or "off") == "off" and not seek:
        return "opus"
    return "pcm"


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


class TrackOpusAudio(_StrictExitMixin, FFmpegOpusAudio):
    """Passthrough path: remux Opus with `-c:a copy`, no decode/re-encode."""

    def __init__(self, source, **kwargs):
        super().__init__(source, codec="copy", **kwargs)


class VolumeAudio(PCMVolumeTransformer):
    """Player reads _current_error on the wrapper; ffmpeg sets it on the inner source."""

    @property
    def _current_error(self):
        return getattr(self.original, "_current_error", None)
