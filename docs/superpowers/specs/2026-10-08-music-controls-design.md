# Music controls design

Date: 2026-10-08  
Status: approved; implement in `music_cog.py` only (approach 1)

## Scope (ordered)

1. Volume 0–100, per voice channel, persist `/config/volumes.json`
2. `/nowplaying` + richer `/queue` with requesters + move buttons (Select + up/down/to front)
3. Playlist polish: out of scope
4. `/shuffle` (pending queue) + `/loop` (queue loop toggle only)

## Permissions

Anyone in the same voice channel as the bot. Others get ephemeral refuse.

## Volume

- `/volume` optional int 0–100: omit = show current; set = apply + save
- Key by voice channel id; default 100
- Wrap playback with `discord.PCMVolumeTransformer`
- Path: `/config/volumes.json` if `/config` exists, else `./volumes.json`
- JoJo reply on change

## Now playing / queue

- `SongInfo` stores `requester_id`, `requester_name`
- `/nowplaying`: title, url, thumbnail, requester
- `/queue`: now-playing header + numbered list with requesters
- View: Select which pending song, then Move up / Move down / To front
- Refresh embed after move

## Shuffle / loop

- `/shuffle`: shuffle `queue.songs` only
- `/loop`: toggle `queue.looping`; when song ends and queue empty and looping, re-queue finished tracks or rotate so queue restarts
- Simplest correct loop: on `pop_next`, if looping and song finished, append finished song back when advancing; when queue would be empty after finish, restore from a history list OR on pop keep finished song at end if looping

Recommended loop: keep `queue.history` of finished songs this session; when `pop_next` returns None and `looping`, move history back into songs and clear history, then pop.

## Out of scope

Track loop, playlist progress UI, Lavalink, new files beyond tests/README, new dependencies.

## Tests

Extend `test_offline.py` for GuildQueue move / shuffle / loop helpers.
