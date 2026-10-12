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
from bot.edville import parse_entities, parse_schedule
from bot.handlers import real_teachers, search_teachers
from bot.storage import Storage
from tests.test_edville import load
from tests.test_render import schedule_9a

# The handlers' router can belong to only one dispatcher, so tests share one
# and swap in fresh dependencies for each test. Each test uses its own chat id
# so that conversation state never leaks between tests.
DP = Dispatcher()
DP.include_router(handlers.router)
CHAT_IDS = itertools.count(1000)


class FakeEdville:
    async def list_entities(self, kind):
        return parse_entities(load("home.html"), kind)

    async def list_classes(self):
        return await self.list_entities("class")

    async def get_schedule(self, kind, slug, max_age=60):
        if kind == "teacher":
            return parse_schedule(load("teacher_tyler-quinton.html"), "teacher", "tyler-quinton")
        return schedule_9a()


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=1, date=datetime.now(), chat={"id": 1, "type": "private"}, text=method.text)
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError


class Harness:
    def __init__(self):
        self.chat_id = next(CHAT_IDS)
        self.chat = {"id": self.chat_id, "type": "private"}
        self.user = {"id": self.chat_id, "is_bot": False, "first_name": "Test"}
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
        return list(self.session.calls)

    def text(self, text: str) -> list:
        entities = [{"type": "bot_command", "offset": 0, "length": len(text)}] if text.startswith("/") else None
        message = {"message_id": next(self.ids), "date": 0, "chat": self.chat, "from": self.user, "text": text}
        return self._feed({"message": {**message, "entities": entities}})

    def press(self, data: str) -> list:
        message = {"message_id": next(self.ids), "date": 0, "chat": self.chat, "text": "…"}
        query = {"id": str(next(self.ids)), "from": self.user, "chat_instance": "x", "data": data, "message": message}
        return self._feed({"callback_query": query})

    @property
    def sub(self):
        return self.db.get_subscription(self.chat_id)


def texts(calls) -> list[str]:
    return [c.text for c in calls if isinstance(c, (SendMessage, EditMessageText))]


def buttons(call) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in call.reply_markup.inline_keyboard for b in row]


def menu(call) -> list[str]:
    return [b.text for row in call.reply_markup.keyboard for b in row]


# --- choosing a role ---


def test_start_asks_student_or_teacher():
    h = Harness()
    [call] = h.text("/start")
    assert "Вы ученик или учитель?" in call.text
    assert buttons(call) == [("🧑‍🎓 Ученик", "role:class"), ("🧑‍🏫 Учитель", "role:teacher")]


def test_student_flow():
    h = Harness()
    calls = h.press("role:class")
    assert texts(calls) == ["В каком вы классе?"]
    assert buttons(calls[-1])[:2] == [("(5)", "pick:class:5"), ("1А", "pick:class:1a")]
    assert buttons(calls[-1])[-1] == ("⬅️ Назад", "back")

    calls = h.press("pick:class:9a")
    assert (h.sub.kind, h.sub.slug) == ("class", "9a")
    assert texts(calls)[0].startswith("✅ Класс 9А")
    assert "🔁 Сменить класс" in menu(calls[-1])


def test_teacher_flow():
    h = Harness()
    calls = h.press("role:teacher")
    names = [text for text, _ in buttons(calls[-1])]
    assert "Найдите себя в списке" in texts(calls)[0]
    assert "Tyler Quinton" in names and "Study Time 9A" not in names
    assert "1/3" in names and "⬅️ Назад" in names

    calls = h.press("pick:teacher:tyler-quinton")
    assert (h.sub.kind, h.sub.slug, h.sub.title) == ("teacher", "tyler-quinton", "Tyler Quinton")
    assert texts(calls)[0].startswith("✅ Tyler Quinton")
    assert "Tyler Quinton · расписание на неделю" in texts(calls)[1]
    assert "класс 7А" in texts(calls)[1]
    assert "🔁 Сменить учителя" in menu(calls[-1])


def test_teacher_pages_wrap_around():
    h = Harness()
    first = [t for t, _ in buttons(h.press("role:teacher")[-1])]
    second = [t for t, _ in buttons(h.press("tpage:1")[-1])]
    last = [t for t, _ in buttons(h.press("tpage:2")[-1])]
    assert "2/3" in second and "3/3" in last
    assert set(first[:16]).isdisjoint(second[:16])
    assert ("◀️", "tpage:2") in buttons(h.press("tpage:0")[-1])


def test_teacher_search_by_typing():
    h = Harness()
    h.press("role:teacher")
    calls = h.text("кудайбергенова")  # typed without Kazakh letters
    assert buttons(calls[-1]) == [("Жанар Құдайбергенова", "pick:teacher:zhanar-dajbergenova"), ("⬅️ Назад", "back")]

    calls = h.text("Иванов")
    assert "Никого не нашёл" in texts(calls)[0]
    assert h.sub is None


def test_typing_is_not_a_search_outside_the_teacher_list():
    h = Harness()
    h.press("role:teacher")
    h.press("back")
    assert "Вы ученик или учитель?" in texts(h.text("Мустюкова"))[0]


def test_back_returns_to_role_question():
    h = Harness()
    h.press("role:class")
    assert texts(h.press("back")) == ["Вы ученик или учитель?"]


def test_switching_from_student_to_teacher():
    h = Harness()
    h.press("pick:class:9a")
    assert texts(h.text("/change")) == ["Вы ученик или учитель?"]
    h.press("role:teacher")
    h.press("pick:teacher:tyler-quinton")
    assert h.sub.kind == "teacher"


def test_menu_change_button_opens_own_list():
    h = Harness()
    h.press("pick:teacher:tyler-quinton")
    assert "Найдите себя в списке" in texts(h.text("🔁 Сменить учителя"))[0]


# --- using the timetable ---


def test_day_and_week_need_a_choice_first():
    h = Harness()
    reply = texts(h.text("/today"))[0]
    assert "Сначала выберите" in reply and "Вы ученик или учитель?" in reply
    h.press("pick:class:9a")
    assert "Класс 9А" in texts(h.text("📅 Сегодня"))[0]
    assert "Класс 9А" in texts(h.text("/tomorrow"))[0]
    assert "расписание на неделю" in texts(h.text("/week"))[0]


def test_teacher_day_view():
    h = Harness()
    h.press("pick:teacher:tyler-quinton")
    reply = texts(h.text("/week"))[0]
    assert reply.startswith("🗓 <b>Tyler Quinton")


def test_stop_and_start_toggle_notifications():
    h = Harness()
    h.press("pick:class:9a")
    h.text("/stop")
    assert not h.sub.notify
    assert "Класс 9А" in texts(h.text("/start"))[0]
    assert h.sub.notify


def test_unknown_entry():
    h = Harness()
    calls = h.press("pick:teacher:nobody")
    assert h.sub is None
    assert calls[-1].show_alert


# --- teacher list helpers ---


def test_placeholders_are_not_teachers():
    teachers = real_teachers(parse_entities(load("home.html"), "teacher"))
    titles = {t.title for t in teachers}
    assert {"Tyler Quinton", "Малика", "Еркебулан"} <= titles
    assert not titles & {"Study Time 9A", "Sport Activity 2", "Вокал 2", "Драма 3"}


def test_search_matches_parts_of_name():
    teachers = real_teachers(parse_entities(load("home.html"), "teacher"))
    assert [t.title for t in search_teachers(teachers, "яна")] == ["Яна Мустюкова"]
    assert [t.title for t in search_teachers(teachers, "tyler q")] == ["Tyler Quinton"]
    assert len(search_teachers(teachers, "адиль")) == 2
