import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Discord bot token (required)
TOKEN = os.getenv('DISCORD_TOKEN', '')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required")

# Bot command prefix
prefix = os.getenv('BOT_PREFIX', '/')

# Guild IDs for slash command syncing
_guild_ids_str = os.getenv('GUILD_IDS', '')
if _guild_ids_str:
    guild_ids = [int(gid.strip()) for gid in _guild_ids_str.split(',') if gid.strip()]
else:
    guild_ids = []
