FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

# Explicit UID so TrueNAS host mounts can chown 1000:1000
RUN useradd -u 1000 --create-home --shell /bin/bash botuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=botuser:botuser main.py config.py admin_cog.py dnd_cog.py music_cog.py ./
RUN mkdir -p /app/.yt_cache && chown botuser:botuser /app/.yt_cache

USER botuser
CMD ["python", "main.py"]
