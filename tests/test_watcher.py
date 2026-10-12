import asyncio

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from bot.edville import EdvilleError, Entity
from bot.storage import Storage
from bot.watcher import ScheduleWatcher
from tests.test_render import edit_lesson, schedule_9a

NINE_A = Entity("class", "9a", "9А")


class FakeEdville:
    def __init__(self, schedule):
        self.schedule = schedule
        self.down = False

    async def get_schedule(self, kind, slug, max_age=60):
        if self.down:
            raise EdvilleError("site is down")
        return self.schedule


class FakeBot:
    def __init__(self, blocked=()):
        self.sent: list[tuple[int, str]] = []
        self.blocked = set(blocked)

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.blocked:
            raise TelegramForbiddenError(SendMessage(chat_id=chat_id, text=text), "bot was blocked by the user")
        self.sent.append((chat_id, text))


def make(blocked=()):
    db = Storage(":memory:")
    site = FakeEdville(schedule_9a())
    bot = FakeBot(blocked)
    return ScheduleWatcher(bot, site, db, interval=60), db, site, bot


def poll(watcher):
    asyncio.run(watcher.check_all())


def test_first_poll_only_stores_snapshot():
    watcher, db, site, bot = make()
    db.subscribe(1, NINE_A)
    poll(watcher)
    assert db.get_snapshot("class", "9a") == site.schedule
    assert bot.sent == []


def test_change_is_announced_once_it_settles():
    watcher, db, site, bot = make()
    db.subscribe(1, NINE_A)
    db.subscribe(2, NINE_A)
    poll(watcher)

    site.schedule = edit_lesson(site.schedule, 0, 3, subject="Геометрия")
    poll(watcher)
    assert bot.sent == []  # first sighting: wait in case the admin is mid-edit

    poll(watcher)
    assert {chat for chat, _ in bot.sent} == {1, 2}
    assert "Геометрия" in bot.sent[0][1]
    assert db.get_snapshot("class", "9a") == site.schedule

    bot.sent.clear()
    poll(watcher)
    assert bot.sent == []  # no repeat notifications


def test_edit_in_progress_does_not_notify():
    watcher, db, site, bot = make()
    db.subscribe(1, NINE_A)
    poll(watcher)
    original = site.schedule
    for subject in ["Г", "Ге", "Гео"]:
        site.schedule = edit_lesson(original, 0, 3, subject=subject)
        poll(watcher)
    assert bot.sent == []


def test_reverted_change_is_not_announced():
    watcher, db, site, bot = make()
    db.subscribe(1, NINE_A)
    poll(watcher)
    original = site.schedule
    site.schedule = edit_lesson(original, 0, 3, subject="Геометрия")
    poll(watcher)
    site.schedule = original
    poll(watcher)
    poll(watcher)
    assert bot.sent == []


def test_site_down_is_ignored():
    watcher, db, site, bot = make()
    db.subscribe(1, NINE_A)
    poll(watcher)
    site.down = True
    poll(watcher)
    poll(watcher)
    assert bot.sent == []
    assert db.get_snapshot("class", "9a") is not None


def test_muted_and_blocked_users():
    watcher, db, site, bot = make(blocked={2})
    db.subscribe(1, NINE_A)
    db.subscribe(2, NINE_A)
    db.subscribe(3, NINE_A)
    db.set_notify(3, False)
    poll(watcher)
    site.schedule = edit_lesson(site.schedule, 0, 3, subject="Геометрия")
    poll(watcher)
    poll(watcher)
    assert {chat for chat, _ in bot.sent} == {1}
    assert db.get_subscription(2) is None  # blocked the bot -> forgotten
    assert db.get_subscription(3) is not None  # muted but still remembered
