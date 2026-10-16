import asyncio
from datetime import time

from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardMarkup

from bot.announcer import Announcer, due
from bot.delivery import deliver
from bot.edville import Entity
from bot.storage import Storage, Subscription
from tests.test_edville import at
from tests.test_render import schedule_9a

NINE_A = Entity("class", "9a", "9А")


def sub(**settings) -> Subscription:
    return Subscription(-100, "class", "9a", "9А", True, **settings)


class FakeBot:
    def __init__(self, migrated=None):
        self.sent: list[tuple[int, str, object]] = []
        self.migrated = migrated or {}  # old group id -> supergroup id

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.migrated:
            raise TelegramMigrateToChat(SendMessage(chat_id=chat_id, text=text), "upgraded", self.migrated[chat_id])
        self.sent.append((chat_id, text, reply_markup))


def test_daily_timetable_on_school_days():
    s = schedule_9a()
    [text] = due(sub(daily=True), s, at(0, "07:29"), at(0, "07:30"))
    assert "Класс 9А · Понедельник" in text and "Завтрак" in text
    assert due(sub(daily=True), s, at(5, "07:00"), at(5, "08:00")) == []  # Saturday
    assert due(sub(daily=True, daily_time=time(8)), s, at(0, "07:29"), at(0, "07:30")) == []
    assert due(sub(), s, at(0, "07:00"), at(0, "17:00")) == []  # nothing turned on


def test_next_lesson_after_each_lesson():
    s = schedule_9a()
    posts = due(sub(next_lesson=True), s, at(0, "09:00"), at(0, "10:00"))
    assert len(posts) == 2  # lessons 1 and 2 end at 09:10 and 09:55
    assert "Всемирная история" in posts[0] and "Через 5 мин" in posts[0]
    assert "Алгебра" in posts[1] and "Через 5 мин" in posts[1]

    [after_brunch] = due(sub(next_lesson=True), s, at(0, "10:30"), at(0, "10:45"))
    assert "Через 15 мин" in after_brunch  # lesson 3 ends 10:40, brunch, lesson 4 at 10:55


def test_after_the_last_lesson_the_next_is_tomorrow():
    s = schedule_9a()
    [text] = due(sub(next_lesson=True), s, at(0, "16:50"), at(0, "17:00"))
    assert "📅 Завтра" in text
    [text] = due(sub(next_lesson=True), s, at(4, "16:50"), at(4, "17:00"))
    assert "📅 В понедельник" in text


def test_each_post_falls_in_exactly_one_check():
    s, settings = schedule_9a(), sub(daily=True, next_lesson=True)
    assert len(due(settings, s, at(0, "09:00"), at(0, "09:10"))) == 1
    assert due(settings, s, at(0, "09:10"), at(0, "09:14")) == []


def test_posts_are_in_order_and_cross_midnight():
    s = schedule_9a()
    posts = due(sub(daily=True, daily_time=time(9, 10), next_lesson=True), s, at(6, "23:59"), at(7, "09:10"))
    assert len(posts) == 2
    assert "Понедельник" in posts[0] and "Следующий урок" in posts[1]


def test_announce_skips_the_keyboard_in_groups():
    db = Storage(":memory:")
    db.save_snapshot(schedule_9a())
    for chat_id in (-100, 5, 6):
        db.subscribe(chat_id, NINE_A)
    db.set_daily(-100, True, time(7, 30))
    db.set_daily(5, True, time(7, 30))
    bot = FakeBot()
    asyncio.run(Announcer(bot, db, at(0, "07:30").tzinfo).announce(at(0, "07:29"), at(0, "07:30")))
    markups = {chat_id: markup for chat_id, _, markup in bot.sent}
    assert markups.keys() == {-100, 5}  # chat 6 turned nothing on
    assert markups[-100] is None
    assert isinstance(markups[5], ReplyKeyboardMarkup)


def test_delivery_follows_group_upgrade():
    db = Storage(":memory:")
    db.subscribe(-1, NINE_A)
    bot = FakeBot(migrated={-1: -1001})
    asyncio.run(deliver(bot, db, -1, ["hello"]))
    assert [chat_id for chat_id, _, _ in bot.sent] == [-1001]
    assert db.get_subscription(-1) is None and db.get_subscription(-1001) is not None
