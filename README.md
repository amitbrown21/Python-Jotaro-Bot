# Python-Jotaro-Bot

A JoJo's Bizarre Adventure themed Discord bot with music playback, D&D utilities, and more!

## Features

- 🎵 **Music Commands**
  - `/play` - Play a song from YouTube search or URL (supports playlists!)
  - `/pause` - Pause the currently playing audio
  - `/resume` - Resume paused audio
  - `/skip` - Skip to the next song in the queue
  - `/stop` - Stop playback and clear queue
  - `/queue` - Display the current queue
  - `/clear_queue` - Clear all songs from the queue
  - `/remove_last` - Remove the last song from the queue
  - `/leave` - Make the bot leave the voice channel
  - `/localplay` - Play a local MP3 file

- 🎲 **D&D Commands**
  - `/roll` - Roll multiple dice (e.g., `2d6 1d20`)
  - `/generate_stats` - Generate character stats using different methods
  - `/initiative` - Roll initiative with optional modifier
  - `/generate_character` - Generate a random D&D character
  - `/loot` - Generate random loot by rarity
  - `/weather` - Generate random weather conditions
  - `/coinflip` - Flip a coin

- 📌 **Other Commands**
  - `/ping` - Responds with "Pong @" and your mention
  - `/hello` - Jotaro greets you

## Quick Start

### Option 1: Run with Docker (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/Python-Jotaro-Bot.git
   cd Python-Jotaro-Bot
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your Discord bot token and guild IDs
   ```

3. **Run with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

4. **View logs:**
   ```bash
   docker-compose logs -f
   ```

### Option 2: Run Locally

1. **Prerequisites:**
   - Python 3.11+
   - FFmpeg installed and in PATH

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your Discord bot token and guild IDs
   ```

4. **Run the bot:**
   ```bash
   python main.py
   ```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Your Discord bot token from the [Developer Portal](https://discord.com/developers/applications) |
| `BOT_PREFIX` | No | Command prefix (default: `/`) |
| `GUILD_IDS` | No | Comma-separated list of guild IDs for slash command syncing |

## Docker Commands

```bash
# Build the image
docker build -t jotaro-bot .

# Run with docker-compose
docker-compose up -d

# Stop the bot
docker-compose down

# View logs
docker-compose logs -f jotaro-bot

# Rebuild after code changes
docker-compose up -d --build
```

## Project Structure

```
Python-Jotaro-Bot/
├── main.py           # Bot entry point
├── config.py         # Configuration (loads from environment)
├── music_cog.py      # Music playback commands
├── dnd_cog.py        # D&D utility commands
├── admin_cog.py      # Admin/utility commands
├── requirements.txt  # Python dependencies
├── Dockerfile        # Docker image definition
├── docker-compose.yml# Docker Compose configuration
├── .env.example      # Environment variable template
└── .dockerignore     # Files excluded from Docker build
```

## License

MIT License
