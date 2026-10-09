"""Offline self-checks. Run: python test_offline.py"""
import discord

from dnd_cog import roll_dice_expr
from music_cog import (
    GuildLibrary,
    GuildQueue,
    IDLE_LEAVE_SECONDS,
    QueueControlView,
    SongInfo,
    build_ffmpeg_options,
    library_path,
    music_cog,
    parse_seek,
    pick_related_entry,
    stream_url_stale,
    tracks_from_queue,
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


def _labels(view: QueueControlView) -> list[str]:
    return [child.label for child in view.children if getattr(child, "label", None)]


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
    playing = QueueControlView(cog, 1, show_queue_button=True)
    assert _labels(playing) == ["Pause", "Skip", "Stop", "Queue", "Autoplay: Off"]
    assert playing.timeout == 180
    playing.to_components()

    cog.q.add(_song("next"))
    queued = QueueControlView(cog, 1)
    assert _labels(queued) == [
        "Move up",
        "Move down",
        "To front",
        "Pause",
        "Skip",
        "Stop",
        "Autoplay: Off",
    ]
    assert any(isinstance(child, discord.ui.Select) for child in queued.children)
    queued.to_components()

    both = QueueControlView(cog, 1, show_queue_button=True)
    assert _labels(both) == [
        "Move up",
        "Move down",
        "To front",
        "Pause",
        "Skip",
        "Stop",
        "Queue",
        "Autoplay: Off",
    ]
    both.to_components()


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


if __name__ == "__main__":
    test_roll_dice_expr()
    test_stream_url_stale()
    test_guild_queue()
    test_guild_queue_move_shuffle_loop()
    test_remove_seek_played()
    test_queue_control_view_buttons()
    test_guild_library()
    test_idle_leave_delay_cancel()
    print("ok")
