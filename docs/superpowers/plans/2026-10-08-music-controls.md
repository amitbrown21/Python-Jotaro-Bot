# Music Controls Implementation Plan

> **For agentic workers:** Implement task-by-task. Checkboxes track progress.

**Goal:** Volume (persisted per VC), nowplaying/queue with move buttons, shuffle, queue-loop — all in `music_cog.py`.

**Architecture:** Extend `SongInfo`/`GuildQueue`; JSON volume store; `PCMVolumeTransformer`; `discord.ui` Select+buttons on queue message.

**Tech Stack:** discord.py, existing yt-dlp path

---

### Task 1: Queue helpers + tests

- [x] Add move_up/move_down/move_to_front, shuffle, looping+history on `GuildQueue`
- [x] Add requester fields on `SongInfo`
- [x] Extend `test_offline.py`; run until green

### Task 2: Volume

- [x] Load/save `/config/volumes.json` (fallback `./volumes.json`)
- [x] `/volume` command; apply via `PCMVolumeTransformer` on play
- [x] VC-member permission helper

### Task 3: Now playing + queue UI

- [x] `/nowplaying`; richer `/queue` embed
- [x] Queue View: select + up/down/to front; refresh message
- [x] Set requester in `create_song_info` / play paths

### Task 4: Shuffle + loop

- [x] `/shuffle`, `/loop` commands
- [x] Wire loop into `play_next` / `pop_next` history restore
- [x] Update README feature list
- [x] Run `python test_offline.py`
