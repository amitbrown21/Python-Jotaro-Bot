"""Offline self-checks. Run: python test_offline.py"""
from dnd_cog import roll_dice_expr
from music_cog import GuildQueue, SongInfo, stream_url_stale


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


if __name__ == "__main__":
    test_roll_dice_expr()
    test_stream_url_stale()
    test_guild_queue()
    test_guild_queue_move_shuffle_loop()
    print("ok")
