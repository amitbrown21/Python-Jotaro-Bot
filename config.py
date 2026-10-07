import os
from pathlib import Path

from dotenv import load_dotenv

# Docker/TrueNAS: mount host config dir to /config
# Local: fall back to .env in the project folder
_CONFIG_ENV = Path('/config/.env')
_TRUENAS_PERM_HINT = (
    "Permission denied reading /config/.env (TrueNAS). "
    "On the host run: chown -R 1000:1000 /mnt/TruMedia/discord-bot && "
    "chmod 755 /mnt/TruMedia/discord-bot && chmod 644 /mnt/TruMedia/discord-bot/.env "
    "(container UID 1000), or set Custom User to 1000."
)
try:
    if _CONFIG_ENV.is_file():
        load_dotenv(_CONFIG_ENV)
    else:
        load_dotenv()
except PermissionError as e:
    # Host mount root-owned/mode 700 — botuser cannot stat/read .env
    if not os.getenv('DISCORD_TOKEN'):
        raise ValueError(_TRUENAS_PERM_HINT) from e

TOKEN = os.getenv('DISCORD_TOKEN', '')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required")

_guild_ids_str = os.getenv('GUILD_IDS', '')
if _guild_ids_str:
    guild_ids = [int(gid.strip()) for gid in _guild_ids_str.split(',') if gid.strip()]
else:
    guild_ids = []
