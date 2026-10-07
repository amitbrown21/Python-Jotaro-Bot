import os
from pathlib import Path

from dotenv import load_dotenv

# Docker/TrueNAS: mount host config dir to /config
# Local: fall back to .env in the project folder
if Path('/config/.env').is_file():
    load_dotenv('/config/.env')
else:
    load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN', '')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required")

_guild_ids_str = os.getenv('GUILD_IDS', '')
if _guild_ids_str:
    guild_ids = [int(gid.strip()) for gid in _guild_ids_str.split(',') if gid.strip()]
else:
    guild_ids = []
