"""Feed fake Telegram updates through the real dispatcher and check the replies."""

import asyncio
import itertools
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import EditMessageText, GetChatMember, GetMe, SendMessage
from aiogram.types import ChatMemberMember, ChatMemberOwner, Message, Update, User

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
BOT_USER = {"id": 1, "is_bot": True, "first_name": "EdVille", "username": "EdvilleTestBot"}


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
        self.admin = True  # whether the user is an admin of the group chat

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=1, date=datetime.now(), chat={"id": 1, "type": "private"}, text=method.text)
        if isinstance(method, GetMe):
            return User(**BOT_USER)
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="Test")
            return ChatMemberOwner(user=user, is_anonymous=False) if self.admin else ChatMemberMember(user=user)
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError


class Harness:
    def __init__(self, group: bool = False):
        self.chat_id = next(CHAT_IDS)
        self.chat = {"id": self.chat_id, "type": "private"}
        if group:
            self.chat_id = -self.chat_id
            self.chat = {"id": self.chat_id, "type": "supergroup", "title": "9А"}
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

    def added_to_group(self) -> list:
        update = {
            "chat": self.chat,
            "from": self.user,
            "date": 0,
            "old_chat_member": {"status": "left", "user": BOT_USER},
            "new_chat_member": {"status": "member", "user": BOT_USER},
        }
        return self._feed({"my_chat_member": update})

    def upgrade_to_supergroup(self, new_id: int) -> list:
        message = {"message_id": next(self.ids), "date": 0, "chat": self.chat, "from": self.user}
        return self._feed({"message": {**message, "migrate_to_chat_id": new_id}})

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


def test_next_lesson_button():
    h = Harness()
    assert "Сначала выберите" in texts(h.text("⏭ Следующий урок"))[0]
    h.press("pick:class:9a")
    calls = h.text("⏭ Следующий урок")
    assert "Следующий урок · Класс 9А" in texts(calls)[0]
    assert menu(calls[-1])[0] == "⏭ Следующий урок"
    assert "Следующий урок" in texts(h.text("/next"))[0]


# --- settings ---


def labels(call) -> list[str]:
    return [text for text, _ in buttons(call)]


def test_settings_toggles():
    h = Harness()
    h.press("pick:class:9a")
    calls = h.text("/settings")
    assert "Настройки · Класс 9А" in texts(calls)[0]
    assert "⬜️ Расписание на день в 07:30" in labels(calls[-1])

    h.press("set:daily")
    h.press("set:later")
    h.press("set:later")
    calls = h.press("set:earlier")
    assert (h.sub.daily, h.sub.daily_time) == (True, time(7, 45))
    assert "✅ Расписание на день в 07:45" in labels(calls[-1])  # the panel is redrawn in place

    h.press("set:next")
    h.press("set:changes")
    assert (h.sub.next_lesson, h.sub.notify) == (True, False)

    h.press("set:daily")
    assert (h.sub.daily, h.sub.daily_time) == (False, time(7, 45))


def test_settings_need_a_choice_first():
    h = Harness()
    assert "Сначала выберите" in texts(h.text("/settings"))[0]
    assert h.press("set:daily")[-1].show_alert


# --- group chats ---


def test_added_to_group_asks_for_class():
    h = Harness(group=True)
    [call] = h.added_to_group()
    assert "Расписание какого класса" in call.text
    assert ("9А", "pick:class:9a") in buttons(call)
    assert "⬅️ Назад" not in labels(call)


def test_group_picks_a_class_then_sees_settings():
    h = Harness(group=True)
    reply = texts(h.text("/start@EdvilleTestBot"))[0]
    assert "Расписание какого класса" in reply and "ученик" not in reply

    calls = h.press("pick:class:9a")
    assert (h.sub.kind, h.sub.slug) == ("class", "9a")
    assert "Настройки · Класс 9А" in texts(calls)[0]
    assert (h.sub.daily, h.sub.next_lesson) == (False, False)  # off until an admin turns them on
    assert "Настройки" in texts(h.text("/start"))[0]


def test_group_timetable_commands_work_without_scheduled_posts():
    h = Harness(group=True)
    h.press("pick:class:9a")
    for command, expected in [
        ("/today", "Класс 9А"),
        ("/next@EdvilleTestBot", "Следующий урок · Класс 9А"),
        ("/tomorrow", "Класс 9А"),
        ("/week", "расписание на неделю"),
    ]:
        sent = [c for c in h.text(command) if isinstance(c, SendMessage)]
        assert expected in sent[0].text
        assert all(c.reply_markup is None for c in sent)  # no reply keyboard popping up for the whole group
    assert texts(h.text("/today@SomeOtherBot")) == []


def test_only_group_admins_change_things():
    h = Harness(group=True)
    h.session.admin = False
    calls = h.press("pick:class:9a")
    assert h.sub is None and calls[-1].show_alert and "администраторы" in calls[-1].text

    h.session.admin = True
    h.press("pick:class:9a")
    h.session.admin = False
    for action in ["daily", "later", "next", "changes", "change"]:
        assert h.press(f"set:{action}")[-1].show_alert
    assert "администраторы" in texts(h.text("/change"))[0]
    assert "администраторы" in texts(h.text("/stop"))[0]
    assert (h.sub.daily, h.sub.daily_time, h.sub.next_lesson, h.sub.notify) == (False, time(7, 30), False, True)

    # Everyone can still look.
    assert "Класс 9А" in texts(h.text("/today"))[0]
    assert "Настройки" in texts(h.text("/settings"))[0]


def test_group_admin_changes_class_from_settings():
    h = Harness(group=True)
    h.press("pick:class:9a")
    h.press("set:next")
    assert "Расписание какого класса" in texts(h.press("set:change"))[0]
    h.press("pick:class:5")
    assert (h.sub.slug, h.sub.next_lesson) == ("5", True)  # settings survive a class change


def test_group_upgraded_to_supergroup_keeps_its_class():
    h = Harness(group=True)
    h.press("pick:class:9a")
    h.upgrade_to_supergroup(-1009)
    assert h.sub is None
    assert h.db.get_subscription(-1009).slug == "9a"
