# Python-Jotaro-Bot

JoJo-themed Discord bot: music (yt-dlp), D&D helpers, slash commands only.

**Image:** `ghcr.io/amitbrown21/python-jotaro-bot:latest`

## Features

- Music: `/play` `/pause` `/resume` `/skip` `/stop` `/queue` `/nowplaying` `/volume` `/shuffle` `/loop` `/clear_queue` `/remove_last` `/remove` `/seek` `/lyrics` `/history` `/effect` `/leave` `/localplay`
- D&D: `/roll` `/generate_stats` `/initiative` `/generate_character` `/loot` `/weather` `/coinflip`
- Other: `/ping` `/hello` `/sync`

## Config

Copy `.env.example` to `.env` (or put `.env` in a folder you mount at `/config`):

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `GUILD_IDS` | No | Comma-separated guild IDs for fast slash sync |
| `YTDLP_COOKIES` | No | Netscape `cookies.txt` path; fixes YouTube `HTTP 403 Forbidden` on play |
| `YTDLP_PLAYER_CLIENT` | No | Override the YouTube client (e.g. `web_safari,tv`) if 403s persist |

If playback starts failing with `[https] HTTP error 403 Forbidden` / `FFmpeg exited with code 8`,
YouTube is rejecting this host's IP — set `YTDLP_COOKIES` (export cookies from a logged-in
browser while logged into YouTube) and redeploy.

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
4. Fix ownership so the container user can read it (runs as **UID 1000**):

```bash
chown -R 1000:1000 /mnt/TruMedia/discord-bot
chmod 755 /mnt/TruMedia/discord-bot
chmod 644 /mnt/TruMedia/discord-bot/.env
```

Or in the Custom App wizard set **Custom User** / group to `1000`.
5. Apps → Discover → Custom App **or** Install via YAML.

**Custom App wizard**

| Field | Value |
|-------|--------|
| Application Name | `discord-bot` |
| Repository | `ghcr.io/amitbrown21/python-jotaro-bot` |
| Tag | `latest` |
| Hostname | `discord-bot` |
| Storage | Host `/mnt/TruMedia/discord-bot` → Container `/config` |
| Custom User (optional) | `1000` |

No ports. Needs outbound internet.

If you see `Permission denied: '/config/.env'`, re-run the `chown`/`chmod` steps above (or set Custom User to 1000).

**YAML:** paste `docker-compose.truenas.yml`.

## Local Python

Python 3.11+, FFmpeg on PATH, then `pip install -r requirements.txt` and `python main.py`.

## Offline check

```bash
python test_offline.py
```

## License

MIT
