"""Startup yt-dlp version check. Logs only; never updates anything at runtime."""
import json
import logging
import os
import urllib.request
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 60


def parse_version_date(version: str) -> Optional[date]:
    """yt-dlp versions are dates: '2026.08.19' (optionally '.1' suffix)."""
    try:
        y, m, d = (int(p) for p in (version or "").split(".")[:3])
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def age_days(version: str, today: date) -> Optional[int]:
    released = parse_version_date(version)
    return None if released is None else (today - released).days


def check_ytdlp(today: Optional[date] = None) -> None:
    """Always log the version; warn when old. YTDLP_CHECK_LATEST=1 also asks PyPI (opt-in)."""
    try:
        from yt_dlp.version import __version__ as version
    except Exception as e:
        log.error("yt-dlp is not importable: %s", e)
        return
    today = today or date.today()
    age = age_days(version, today)
    log.info("yt-dlp version %s", version)
    try:
        limit = int(os.environ.get("YTDLP_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS))
    except ValueError:
        limit = DEFAULT_MAX_AGE_DAYS
    if age is not None and age > limit:
        log.warning(
            "yt-dlp is %d days old (limit %d). YouTube breaks old versions; rebuild the image "
            "(docker build --no-cache) or run: pip install -U yt-dlp", age, limit,
        )
    if os.environ.get("YTDLP_CHECK_LATEST", "").strip() in ("1", "true", "yes"):
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": "JotaroBot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                latest = json.load(resp)["info"]["version"]
        except Exception as e:
            log.info("Could not check latest yt-dlp on PyPI: %s", e)
            return
        if latest != version:
            log.warning("A newer yt-dlp exists: %s (running %s)", latest, version)
        else:
            log.info("yt-dlp is the latest release")
