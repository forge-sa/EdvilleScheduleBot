"""Feed fake Telegram updates through the real dispatcher and check the replies."""

import asyncio
import itertools
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Message, Update

from bot import handlers
from bot.config import Config
from bot.edville import parse_entities
from bot.storage import Storage
from tests.test_edville import load
from tests.test_render import schedule_9a

CHAT = {"id": 42, "type": "private"}
USER = {"id": 42, "is_bot": False, "first_name": "Test"}

# The handlers' router can belong to only one dispatcher, so tests share one
# and swap in fresh dependencies for each test.
DP = Dispatcher()
DP.include_router(handlers.router)


class FakeEdville:
    async def list_entities(self, kind):
        return parse_entities(load("home.html"), kind)

    async def list_classes(self):
        return await self.list_entities("class")

    async def get_schedule(self, kind, slug, max_age=60):
        return schedule_9a()


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=1, date=datetime.now(), chat=CHAT, text=method.text)
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError


class Harness:
    def __init__(self):
        self.ids = itertools.count(1)
        self.session = RecordingSession()
        self.bot = Bot("1:TEST", session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.db = Storage(":memory:")
        config = Config("1:TEST", "http://test", Path(":memory:"), 60, ZoneInfo("Asia/Almaty"))
        DP.workflow_data.update(edville=FakeEdville(), db=self.db, config=config)

    def _feed(self, update: dict) -> list:
        self.session.calls.clear()
        update = Update.model_validate({"update_id": next(self.ids), **update}, context={"bot": self.bot})
        asyncio.run(DP.feed_update(self.bot, update))
        return self.session.calls

    def text(self, text: str) -> list:
        entities = [{"type": "bot_command", "offset": 0, "length": len(text)}] if text.startswith("/") else None
        message = {"message_id": next(self.ids), "date": 0, "chat": CHAT, "from": USER, "text": text}
        return self._feed({"message": {**message, "entities": entities}})

    def press(self, data: str) -> list:
        message = {"message_id": next(self.ids), "date": 0, "chat": CHAT, "text": "В каком ты классе?"}
        query = {"id": str(next(self.ids)), "from": USER, "chat_instance": "x", "data": data, "message": message}
        return self._feed({"callback_query": query})


def texts(calls) -> list[str]:
    return [c.text for c in calls if isinstance(c, (SendMessage, EditMessageText))]


def test_start_asks_for_class():
    h = Harness()
    calls = h.text("/start")
    picker = calls[-1].reply_markup.inline_keyboard
    assert "В каком ты классе?" in texts(calls)
    assert [b.text for row in picker for b in row][:3] == ["(5)", "1А", "2А"]


def test_picking_a_class_subscribes_and_sends_week():
    h = Harness()
    calls = h.press("pick:class:9a")
    assert h.db.get_subscription(42).slug == "9a"
    assert h.db.get_snapshot("class", "9a") is not None
    assert any("расписание на неделю" in t for t in texts(calls))


def test_day_and_week_need_a_class_first():
    h = Harness()
    assert "Сначала выбери класс 👇" in texts(h.text("/today"))
    h.press("pick:class:9a")
    assert "Класс 9А" in texts(h.text("📅 Сегодня"))[0]
    assert "Класс 9А" in texts(h.text("/tomorrow"))[0]
    assert "расписание на неделю" in texts(h.text("/week"))[0]


def test_stop_and_start_toggle_notifications():
    h = Harness()
    h.press("pick:class:9a")
    h.text("/stop")
    assert not h.db.get_subscription(42).notify
    h.text("/start")
    assert h.db.get_subscription(42).notify


def test_unknown_class():
    h = Harness()
    calls = h.press("pick:class:nope")
    assert h.db.get_subscription(42) is None
    assert calls[-1].show_alert
