# Python-Jotaro-Bot

JoJo-themed Discord bot: music (yt-dlp), D&D helpers, slash commands only.

**Image:** `ghcr.io/amitbrown21/python-jotaro-bot:latest`

## Features

- Music: `/play` `/pause` `/resume` `/skip` `/stop` `/queue` `/clear_queue` `/remove_last` `/leave` `/localplay`
- D&D: `/roll` `/generate_stats` `/initiative` `/generate_character` `/loot` `/weather` `/coinflip`
- Other: `/ping` `/hello` `/sync`

## Config

Copy `.env.example` to `.env` (or put `.env` in a folder you mount at `/config`):

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `GUILD_IDS` | No | Comma-separated guild IDs for fast slash sync |

## Docker (local)

```bash
cp .env.example .env   # fill in values
docker compose up -d --build
docker compose logs -f
```

## TrueNAS SCALE

1. Push this repo to `main` (GitHub Action publishes the image).
2. On GitHub: Packages → `python-jotaro-bot` → Package settings → change visibility to **Public** (or add GHCR credentials in TrueNAS).
3. Put only a `.env` on dataset `/mnt/TruMedia/discord-bot`.
4. Apps → Discover → Custom App **or** Install via YAML.

**Custom App wizard**

| Field | Value |
|-------|--------|
| Application Name | `discord-bot` |
| Repository | `ghcr.io/amitbrown21/python-jotaro-bot` |
| Tag | `latest` |
| Hostname | `discord-bot` |
| Storage | Host `/mnt/TruMedia/discord-bot` → Container `/config` |

No ports. Needs outbound internet.

**YAML:** paste `docker-compose.truenas.yml`.

## Local Python

Python 3.11+, FFmpeg on PATH, then `pip install -r requirements.txt` and `python main.py`.

## Offline check

```bash
python test_offline.py
```

## License

MIT
