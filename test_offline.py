"""Offline self-checks. Run: python test_offline.py"""
from dnd_cog import roll_dice_expr
from music_cog import GuildQueue, SongInfo


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


def test_guild_queue():
    q = GuildQueue()
    assert not q
    assert len(q) == 0
    assert q.skipping is False
    assert q.stopping is False

    dummy = {"before_options": "", "options": ""}
    a = SongInfo("A", "https://a", "http://audio-a", "", dummy)
    b = SongInfo("B", "https://b", "http://audio-b", "", dummy)

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
    assert q.remove_last() is None
    assert q.pop_next() is None


if __name__ == "__main__":
    test_roll_dice_expr()
    test_guild_queue()
    print("ok")
