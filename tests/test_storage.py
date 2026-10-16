import sqlite3
from datetime import time

from bot.edville import Entity
from bot.storage import Storage

NINE_A = Entity("class", "9a", "9А")
FIVE = Entity("class", "5", "(5)")


def test_database_from_before_the_settings_still_works(tmp_path):
    path = tmp_path / "bot.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE subscriptions (
               chat_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, slug TEXT NOT NULL, title TEXT NOT NULL,
               notify INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
           INSERT INTO subscriptions (chat_id, kind, slug, title) VALUES (1, 'class', '9a', '9А');"""
    )
    old.close()

    db = Storage(path)
    sub = db.get_subscription(1)
    assert (sub.slug, sub.notify, sub.daily, sub.daily_time, sub.next_lesson) == ("9a", True, False, time(7, 30), False)
    db.set_daily(1, True, time(8, 15))
    [sub] = db.announcing()
    assert sub.daily_time == time(8, 15)


def test_announcing_lists_chats_with_scheduled_posts():
    db = Storage(":memory:")
    for chat_id in (1, 2, 3):
        db.subscribe(chat_id, NINE_A)
    db.set_daily(1, True, time(7, 30))
    db.set_next_lesson(2, True)
    assert [s.chat_id for s in db.announcing()] == [1, 2]


def test_move_chat():
    db = Storage(":memory:")
    db.subscribe(-1, NINE_A)
    db.move_chat(-1, -1001)
    assert db.get_subscription(-1) is None and db.get_subscription(-1001).slug == "9a"

    # If the supergroup already picked a timetable, that choice is kept.
    db.subscribe(-2, NINE_A)
    db.subscribe(-1002, FIVE)
    db.move_chat(-2, -1002)
    assert db.get_subscription(-2) is None and db.get_subscription(-1002).slug == "5"
