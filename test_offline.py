"""Offline self-checks. Run: python test_offline.py"""
import os

os.environ.setdefault("DISCORD_TOKEN", "dummy-token-for-offline-tests")  # config.py requires one

import discord

from admin_cog import HELP_SECTIONS, build_help_embed, help_command_names, admin_cog
from dnd_cog import dnd_cog, roll_dice_expr
from log_setup import resolve_level
from ytdlp_check import age_days, parse_version_date
from music_views import ReorderView
from music_cog import (
    GuildLibrary,
    GuildQueue,
    IDLE_LEAVE_SECONDS,
    QueueControlView,
    SongInfo,
    FFMPEG_BEFORE,
    FFMPEG_OPTIONS,
    YTDL_FORMAT,
    build_ffmpeg_options,
    clip_lines,
    opus_bitrate,
    songs_from_tracks,
    track_from_flat_entry,
    tracks_from_flat_entries,
    library_path,
    music_cog,
    parse_seek,
    pick_related_entry,
    stream_url_stale,
    tracks_from_queue,
    PlayClock,
    choose_audio_path,
    info_is_opus,
    should_reconnect,
    ytdlp_failure_hint,
)


def test_roll_dice_expr():
    lines, total = roll_dice_expr("2d6")
    assert len(lines) == 1
    assert "2d6:" in lines[0]
    assert total >= 2 and total <= 12

    lines, total = roll_dice_expr("1d20 1d4")
    assert len(lines) == 2
    assert total >= 2 and total <= 24

    lines, total = roll_dice_expr("nope")
    assert lines == ["Invalid input: nope"]
    assert total == 0

    lines, total = roll_dice_expr("0d6")
    assert lines == ["Invalid input: 0d6"]


def _song(title: str) -> SongInfo:
    return SongInfo(
        title, f"https://{title}", f"http://audio-{title}", "",
        {"before_options": "", "options": ""},
        requester_id=1, requester_name="Jotaro",
    )


def test_stream_url_stale():
    assert stream_url_stale(None, 1000) is True
    assert stream_url_stale("", 1000) is True
    assert stream_url_stale("https://cdn.example/a.mp3", 1000) is False
    fresh = "https://rr.googlevideo.com/videoplayback?expire=2000&id=1"
    dead = "https://rr.googlevideo.com/videoplayback?foo=1&expire=1000"
    assert stream_url_stale(fresh, 1000) is False
    assert stream_url_stale(dead, 1000) is True
    assert stream_url_stale(dead, 999) is False
    assert stream_url_stale("https://www.youtube.com/watch?v=abc", 1000) is True
    assert stream_url_stale("https://youtu.be/abc", 1000) is True
    assert stream_url_stale(
        "https://rr.googlevideo.com/videoplayback?id=1", 1000
    ) is False


def test_guild_queue():
    q = GuildQueue()
    assert not q
    assert len(q) == 0
    assert q.skipping is False
    assert q.stopping is False
    assert q.looping is False

    a = _song("A")
    b = _song("B")

    assert q.add(a) == 1
    assert q.add(b) == 2
    assert len(q) == 2
    assert bool(q)

    removed = q.remove_last()
    assert removed is b
    assert len(q) == 1

    nxt = q.pop_next()
    assert nxt is a
    assert q.current is a
    assert len(q) == 0

    q.add(b)
    q.clear()
    assert len(q) == 0
    assert q.current is None
    assert q.history == []
    assert q.remove_last() is None
    assert q.pop_next() is None


def test_guild_queue_move_shuffle_loop():
    q = GuildQueue()
    a, b, c = _song("A"), _song("B"), _song("C")
    q.add(a)
    q.add(b)
    q.add(c)

    assert q.move_up(1) is False
    assert q.move_up(2) is True
    assert [s.title for s in q.songs] == ["B", "A", "C"]

    assert q.move_down(3) is False
    assert q.move_down(1) is True
    assert [s.title for s in q.songs] == ["A", "B", "C"]

    assert q.move_to_front(1) is False
    assert q.move_to_front(3) is True
    assert [s.title for s in q.songs] == ["C", "A", "B"]

    q.songs = [a, b, c]
    q.shuffle()
    assert sorted(s.title for s in q.songs) == ["A", "B", "C"]
    assert len(q.songs) == 3

    # loop: finish A,B,C then restore history
    q.songs = [a, b, c]
    q.current = None
    q.history = []
    q.looping = True
    assert q.pop_next() is a
    assert q.history == []
    assert q.pop_next() is b
    assert q.history == [a]
    assert q.pop_next() is c
    assert q.history == [a, b]
    assert q.pop_next() is a  # restore + pop
    assert [s.title for s in q.songs] == ["B", "C"]
    assert q.history == []

    q.looping = False
    q.history = [a]
    q.songs = []
    q.current = c
    assert q.pop_next() is None
    assert q.current is None


def test_remove_seek_played():
    q = GuildQueue()
    a, b, c = _song("A"), _song("B"), _song("C")
    q.add(a)
    q.add(b)
    q.add(c)
    assert q.remove_at(0) is None
    assert q.remove_at(4) is None
    assert q.remove_at(2) is b
    assert [s.title for s in q.songs] == ["A", "C"]
    assert q.history == []

    assert parse_seek("90") == 90
    assert parse_seek("1:30") == 90
    assert parse_seek("1:02:03") == 3723
    assert parse_seek("0") == 0
    assert parse_seek("nope") is None
    assert parse_seek("1:60") is None
    assert parse_seek("") is None
    assert parse_seek("1:2:3:4") is None

    base = {"before_options": "-reconnect 1", "options": "-vn -bufsize 1M"}
    night = build_ffmpeg_options(base, "nightcore", 90)
    assert night["before_options"] == "-reconnect 1 -ss 90"
    assert "asetrate=48000*1.25,aresample=48000" in night["options"]
    assert "atempo" not in night["options"]
    assert "bass=g=8" in build_ffmpeg_options(base, "bass")["options"]
    assert "-af" not in build_ffmpeg_options(base, "off")["options"]
    assert base == {"before_options": "-reconnect 1", "options": "-vn -bufsize 1M"}

    assert YTDL_FORMAT.startswith("bestaudio[acodec=opus]/")
    assert "-reconnect_streamed 1" in FFMPEG_BEFORE and "-vn" in FFMPEG_OPTIONS
    default = {"before_options": FFMPEG_BEFORE, "options": FFMPEG_OPTIONS}
    assert "-af" not in build_ffmpeg_options(default)["options"]
    assert "alimiter" in build_ffmpeg_options(default, "bass")["options"]
    assert opus_bitrate(None) == 128
    assert opus_bitrate(64000) == 128
    assert opus_bitrate(256000) == 256
    assert opus_bitrate(384000) == 256

    for title in ["Old", "New"]:
        q.note_played(_song(title))
    assert [t for t, _ in q.played] == ["New", "Old"]
    assert q.history == []
    song = q.requeue_played(1, {"before_options": "", "options": ""})
    assert song is not None and song.title == "New"
    assert q.songs[-1].title == "New"
    assert q.requeue_played(9, {}) is None
    for i in range(12):
        q.note_played(_song(f"S{i}"))
    assert len(q.played) == 10
    assert q.played[0][0] == "S11"
    q.history = [a]
    q.clear()
    assert q.history == []
    assert q.played[0][0] == "S11"


def _labels(view) -> list[str]:
    return [
        child.label or str(child.emoji)
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def test_queue_control_view_buttons():
    class Cog:
        def __init__(self):
            self.q = GuildQueue()
            self.client = self

        def get_queue(self, guild_id):
            return self.q

        def get_guild(self, guild_id):
            return None

        def is_autoplay(self, guild_id):
            return False

    cog = Cog()
    cog.q.current = _song("now")
    pause = "\u23f8\ufe0f"
    reorder = "Change song order"
    playing = QueueControlView(cog, 1, show_queue_button=True)
    assert _labels(playing) == [pause, "Next song", "Stop", "Queue", "Autoplay: Off", reorder]
    assert playing.pause_btn.label is None
    assert playing.timeout == 180
    playing.to_components()

    cog.q.add(_song("next"))
    queued = QueueControlView(cog, 1)
    assert _labels(queued) == [pause, "Next song", "Stop", "Autoplay: Off", reorder]
    assert not any(isinstance(c, discord.ui.Select) for c in queued.children)
    queued.to_components()

    both = QueueControlView(cog, 1, show_queue_button=True)
    assert _labels(both) == [pause, "Next song", "Stop", "Queue", "Autoplay: Off", reorder]
    both.to_components()

    cog.q.add(_song("third"))
    reorder_view = ReorderView(cog, 1)
    assert _labels(reorder_view) == ["Move up", "Move down", "To front"]
    selects = [c for c in reorder_view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1 and len(selects[0].options) == 2
    reorder_view.to_components()
    cog.q.songs.clear()
    reorder_view.selected = 2
    reorder_view._rebuild_select()
    assert reorder_view.selected == 1
    assert not any(isinstance(c, discord.ui.Select) for c in reorder_view.children)

def test_guild_library():
    assert IDLE_LEAVE_SECONDS == 60
    lib = GuildLibrary()
    assert lib.save_playlist(1, "  ", []) == "Give the playlist a name, Teme."
    assert lib.save_playlist(1, "Mix", []) == "Queue is empty, Teme."
    assert lib.save_playlist(
        1, "Mix", [{"title": "A", "url": "https://a", "thumbnail": ""}]
    ) is None
    assert lib.save_playlist(
        1, "mix", [{"title": "B", "url": "https://b", "thumbnail": ""}]
    ) is None
    assert [item["name"] for item in lib.list_playlists(1)] == ["mix"]
    assert lib.get_playlist(1, "MIX")["tracks"][0]["title"] == "B"
    assert lib.get_playlist(2, "mix") is None
    assert lib.autoplay(1) is False
    assert lib.toggle_autoplay(1) is True
    assert lib.autoplay(1) is True

    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        lib.save(path)
        loaded = GuildLibrary.load(path)
    finally:
        os.remove(path)
    assert loaded.autoplay(1) is True
    assert loaded.delete_playlist(1, "MIX") is True
    assert loaded.get_playlist(1, "mix") is None
    assert loaded.delete_playlist(1, "mix") is False
    assert GuildLibrary.load(path + ".missing").list_playlists(9) == []

    queue = GuildQueue()
    assert tracks_from_queue(queue) == []
    queue.current = _song("now")
    queue.add(_song("next"))
    assert [track["title"] for track in tracks_from_queue(queue)] == ["now", "next"]

    picked = pick_related_entry(
        [
            {"webpage_url": "https://youtu.be/aaaaaaaaaaa"},
            {"webpage_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
        ],
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        set(),
    )
    assert picked is not None and "bbbbbbbbbbb" in picked["webpage_url"]
    recent = pick_related_entry(
        [
            {"webpage_url": "https://youtu.be/aaaaaaaaaaa"},
            {"webpage_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
            {"webpage_url": "https://www.youtube.com/watch?v=ccccccccccc"},
        ],
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        {"https://youtu.be/bbbbbbbbbbb"},
    )
    assert recent is not None and "ccccccccccc" in recent["webpage_url"]
    assert pick_related_entry(
        [{"webpage_url": "https://youtu.be/aaaaaaaaaaa"}],
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        set(),
    ) is None
    assert "library.json" in library_path()


def test_idle_leave_delay_cancel():
    """Timer state only: schedule waits, rejoin cancels, a new channel replaces it."""
    import asyncio

    class Host:
        def __init__(self):
            self._idle_tasks = {}
            self._idle_channel = {}
            self.left = []

        _cancel_idle = music_cog._cancel_idle
        _schedule_idle = music_cog._schedule_idle

        async def _idle_leave(self, guild_id, channel_id):
            await asyncio.sleep(0.05)
            self.left.append((guild_id, channel_id))

    async def run():
        host = Host()
        host._schedule_idle(1, 10)
        waiting = host._idle_tasks[1]
        assert host._idle_channel[1] == 10
        host._schedule_idle(1, 10)
        assert host._idle_tasks[1] is waiting

        host._cancel_idle(1)
        assert 1 not in host._idle_tasks
        assert 1 not in host._idle_channel
        try:
            await waiting
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.08)
        assert host.left == []
        assert waiting.cancelled()

        host._schedule_idle(1, 10)
        await asyncio.sleep(0.08)
        assert host.left == [(1, 10)]

        host.left.clear()
        host._schedule_idle(2, 20)
        first = host._idle_tasks[2]
        host._schedule_idle(2, 21)
        assert host._idle_channel[2] == 21
        assert host._idle_tasks[2] is not first
        try:
            await first
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.08)
        assert first.cancelled()
        assert host.left == [(2, 21)]

    asyncio.run(run())


def test_flat_playlist_to_songs():
    flat = [
        {"title": "A", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
         "thumbnails": [{"url": "small"}, {"url": "big"}]},
        {"title": "[Private video]", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
        {"title": "[Deleted video]", "url": "https://www.youtube.com/watch?v=ccccccccccc"},
        None,
        {"title": "no url"},
        {"title": "C", "id": "ddddddddddd", "ie_key": "Youtube", "thumbnail": "t"},
        {"title": "D", "webpage_url": "https://x/d", "url": "https://x/stream"},
    ]
    tracks = tracks_from_flat_entries(flat)
    assert [t["title"] for t in tracks] == ["A", "C", "D"]
    assert tracks[0]["thumbnail"] == "big"
    assert tracks[1] == {
        "title": "C",
        "url": "https://www.youtube.com/watch?v=ddddddddddd",
        "thumbnail": "t",
    }
    assert tracks[2]["url"] == "https://x/d"
    assert track_from_flat_entry({"url": "https://x/y"})["title"] == "Unknown"

    class User:
        id = 7
        display_name = "Jotaro"

    opts = {"before_options": "", "options": ""}
    songs = songs_from_tracks(
        tracks + [{"title": "bad", "url": "nope"}, {}], opts, User()
    )
    assert [s.title for s in songs] == ["A", "C", "D"]
    assert all(s.audio_url == "" and s._audio is None for s in songs)
    assert songs[0].requester_id == 7 and songs[0].requester_name == "Jotaro"
    assert songs[0].thumbnail == "big"
    assert stream_url_stale(songs[0].audio_url, 0) is True  # lazy: needs resolving
    assert songs_from_tracks(None, opts) == []

    text = clip_lines([f"{i}. {'x' * 80}" for i in range(200)])
    assert len(text) <= 4096 and text.endswith("more")
    assert clip_lines(["a", "b"]) == "a\nb"


def test_lazy_stream_resolve():
    """Saved/flat songs have no stream; _ensure_stream resolves one and only when stale."""
    import asyncio

    class Host:
        def __init__(self, result):
            self.result = result
            self.calls = []

        _ensure_stream = music_cog._ensure_stream
        _refresh_stream = music_cog._refresh_stream

        async def _extract(self, url):
            self.calls.append(url)
            return self.result

    async def run():
        opts = {"before_options": "", "options": ""}
        song = songs_from_tracks(
            [{"title": "A", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}], opts
        )[0]
        host = Host({"url": "https://rr.googlevideo.com/videoplayback?expire=9999999999"})
        await host._ensure_stream(song)
        assert host.calls == [song.url]
        assert song.audio_url.startswith("https://rr.googlevideo.com")
        await host._ensure_stream(song)  # fresh now: no second extraction
        assert len(host.calls) == 1

        dead = songs_from_tracks(
            [{"title": "B", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"}], opts
        )[0]
        await Host(None)._ensure_stream(dead)
        assert dead.audio_url == ""  # unresolved; begin_playback raises so play_next skips it

    asyncio.run(run())


def test_audio_path_choice():
    assert choose_audio_path(100, "off", 0, True) == "opus"
    assert choose_audio_path(100, "off", 0, False) == "pcm"
    assert choose_audio_path(80, "off", 0, True) == "pcm"
    assert choose_audio_path(100, "bass", 0, True) == "pcm"
    assert choose_audio_path(100, "nightcore", 0, True) == "pcm"
    assert choose_audio_path(100, "off", 30, True) == "pcm"
    assert choose_audio_path(0, "off", 0, True) == "pcm"
    assert info_is_opus({"acodec": "opus"}) is True
    assert info_is_opus({"acodec": "mp4a.40.2"}) is False
    assert info_is_opus({}) is False and info_is_opus(None) is False


def test_play_clock_and_reconnect_rule():
    c = PlayClock()
    assert c.position(now=5) == 0
    c.start(10, now=100)
    assert c.position(now=130) == 40
    c.pause(now=130)
    assert c.position(now=500) == 40  # frozen while paused
    c.resume(now=200)
    assert c.position(now=220) == 60
    c.start(0, now=0)
    assert c.position(now=3) == 3

    assert should_reconnect(True, False, 2) is True
    assert should_reconnect(False, False, 2) is False  # nothing was playing
    assert should_reconnect(True, True, 2) is False  # deliberate stop
    assert should_reconnect(True, False, 0) is False  # nobody listening

    assert "pip install -U yt-dlp" in ytdlp_failure_hint("ERROR: Unable to extract nsig")
    assert ytdlp_failure_hint("HTTP Error 403: Forbidden") != ""
    assert ytdlp_failure_hint("Video unavailable") == ""
    assert ytdlp_failure_hint("") == ""


def test_saved_queue_roundtrip():
    import json
    import os
    import tempfile

    lib = GuildLibrary()
    tracks = [{"title": "A", "url": "https://a", "thumbnail": ""}]
    assert lib.get_saved_queue(1) is None
    assert lib.clear_saved_queue(1) is False
    lib.set_saved_queue(1, tracks, looping=True)
    lib.set_saved_queue(2, [], looping=False)  # empty -> nothing saved
    assert lib.get_saved_queue(2) is None
    lib.save_playlist(1, "Mix", tracks)  # same bucket as playlists/autoplay
    lib.toggle_autoplay(1)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "library.json")
        lib.save(path)
        assert os.listdir(d) == ["library.json"]  # temp file replaced, none left behind
        json.load(open(path, encoding="utf-8"))
        loaded = GuildLibrary.load(path)
        saved = loaded.get_saved_queue(1)
        assert saved == {"tracks": tracks, "looping": True}
        assert loaded.autoplay(1) is True
        assert loaded.get_playlist(1, "mix") is not None

        assert loaded.clear_saved_queue(1) is True
        loaded.save(path)
        assert GuildLibrary.load(path).get_saved_queue(1) is None
        assert GuildLibrary.load(path).get_playlist(1, "mix") is not None

        # a failed write must leave the old file intact and no temp litter
        try:
            GuildLibrary.write_atomic(os.path.join(d, "missing", "x.json"), "{}")
        except OSError:
            pass
        assert os.listdir(d) == ["library.json"]

    # malformed saved entry is ignored, not crashed on
    bad = GuildLibrary({"1": {"queue": {"tracks": "nope"}}})
    assert bad.get_saved_queue(1) is None

    q = GuildQueue()
    q.current = _song("now")
    q.add(_song("next"))
    rebuilt = songs_from_tracks(tracks_from_queue(q), {"before_options": "", "options": ""})
    assert [s.title for s in rebuilt] == ["now", "next"]


def test_logging_and_ytdlp_age():
    import datetime
    import logging

    assert resolve_level("debug") == logging.DEBUG
    assert resolve_level(" WARNING ") == logging.WARNING
    assert resolve_level("") == logging.INFO
    assert resolve_level("nonsense") == logging.INFO
    assert parse_version_date("2026.08.19") == datetime.date(2026, 8, 19)
    assert parse_version_date("2026.08.19.1") == datetime.date(2026, 8, 19)
    assert parse_version_date("garbage") is None
    assert age_days("2026.08.19", datetime.date(2026, 10, 10)) == 52
    assert age_days("nope", datetime.date(2026, 10, 10)) is None


def test_help_matches_commands():
    actual = set()
    for cog in (music_cog, dnd_cog, admin_cog):
        for cmd in cog.__cog_app_commands__:
            subs = getattr(cmd, "commands", None)
            if subs:
                actual.update(f"{cmd.name} {sub.name}" for sub in subs)
            else:
                actual.add(cmd.name)
    assert help_command_names() == actual, help_command_names() ^ actual
    embed = build_help_embed()
    assert [f.name for f in embed.fields] == list(HELP_SECTIONS)
    assert all(len(f.value) <= 1024 for f in embed.fields)


if __name__ == "__main__":
    test_logging_and_ytdlp_age()
    test_help_matches_commands()
    test_audio_path_choice()
    test_play_clock_and_reconnect_rule()
    test_saved_queue_roundtrip()
    test_flat_playlist_to_songs()
    test_lazy_stream_resolve()
    test_roll_dice_expr()
    test_stream_url_stale()
    test_guild_queue()
    test_guild_queue_move_shuffle_loop()
    test_remove_seek_played()
    test_queue_control_view_buttons()
    test_guild_library()
    test_idle_leave_delay_cancel()
    print("ok")
