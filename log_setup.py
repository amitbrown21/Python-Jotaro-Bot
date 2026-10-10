"""Logging for the bot: stdout always, rotating file under /config when it is writable."""
import logging
import logging.handlers
import os

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_FILE = "/config/jotaro.log"


def resolve_level(name: str = "") -> int:
    """LOG_LEVEL env text -> logging level. Unknown/empty falls back to INFO."""
    level = logging.getLevelName((name or "").strip().upper())
    return level if isinstance(level, int) else logging.INFO


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_jotaro_configured", False):
        return
    root._jotaro_configured = True
    root.setLevel(resolve_level(os.environ.get("LOG_LEVEL", "")))
    formatter = logging.Formatter(FORMAT)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    # /config is mounted read-only in the shipped compose files; just skip the file then.
    if os.path.isdir("/config"):
        try:
            handler = logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(formatter)
            root.addHandler(handler)
        except OSError as e:
            logging.getLogger(__name__).warning("No log file at %s (%s); stdout only", LOG_FILE, e)
