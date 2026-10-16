from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .edville import Entity, Kind
from .storage import Subscription

BTN_NEXT = "⏭ Следующий урок"
BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "➡️ Завтра"
BTN_WEEK = "🗓 Неделя"
BTN_CHANGE = {"class": "🔁 Сменить класс", "teacher": "🔁 Сменить учителя"}

TEACHERS_PER_PAGE = 16
BACK_TO_ROLE = "back"
NOOP = "noop"


class Role(CallbackData, prefix="role"):
    kind: str  # "class" for students, "teacher" for teachers


class Pick(CallbackData, prefix="pick"):
    kind: str
    slug: str


class TeacherPage(CallbackData, prefix="tpage"):
    page: int


class Setting(CallbackData, prefix="set"):
    action: str  # "daily", "earlier", "later", "next", "changes" or "change" (pick another timetable)


def role_picker() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧑‍🎓 Ученик", callback_data=Role(kind="class"))
    kb.button(text="🧑‍🏫 Учитель", callback_data=Role(kind="teacher"))
    return kb.as_markup()


def _back() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Назад", callback_data=BACK_TO_ROLE)


def entity_picker(entities: list[Entity], per_row: int, back: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for e in entities:
        kb.button(text=e.title, callback_data=Pick(kind=e.kind, slug=e.slug))
    kb.adjust(per_row)
    if back:
        kb.row(_back())
    return kb.as_markup()


def class_picker(classes: list[Entity], back: bool = True) -> InlineKeyboardMarkup:
    return entity_picker(classes, per_row=4, back=back)


def teacher_picker(teachers: list[Entity], page: int = 0) -> InlineKeyboardMarkup:
    """One page of teachers, with ◀️ ▶️ to flip through the rest."""
    pages = max(1, -(-len(teachers) // TEACHERS_PER_PAGE))
    page %= pages
    kb = InlineKeyboardBuilder()
    for e in teachers[page * TEACHERS_PER_PAGE : (page + 1) * TEACHERS_PER_PAGE]:
        kb.button(text=e.title, callback_data=Pick(kind=e.kind, slug=e.slug))
    kb.adjust(2)
    if pages > 1:
        kb.row(
            InlineKeyboardButton(text="◀️", callback_data=TeacherPage(page=(page - 1) % pages).pack()),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=NOOP),
            InlineKeyboardButton(text="▶️", callback_data=TeacherPage(page=(page + 1) % pages).pack()),
        )
    kb.row(_back())
    return kb.as_markup()


def main_menu(kind: Kind) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEXT)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=BTN_CHANGE[kind])],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def chat_menu(chat_id: int, kind: Kind) -> ReplyKeyboardMarkup | None:
    """The menu for a private chat. Groups get none: it would pop up for every
    member, and in privacy mode the bot never sees the button presses anyway."""
    return main_menu(kind) if chat_id > 0 else None


def _checkbox(on: bool) -> str:
    return "✅" if on else "⬜️"


def settings_menu(sub: Subscription) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    daily = f"{_checkbox(sub.daily)} Расписание на день в {sub.daily_time:%H:%M}"
    kb.button(text=daily, callback_data=Setting(action="daily"))
    kb.button(text="⏪ −15 мин", callback_data=Setting(action="earlier"))
    kb.button(text="⏩ +15 мин", callback_data=Setting(action="later"))
    next_lesson = f"{_checkbox(sub.next_lesson)} Следующий урок после каждого урока"
    kb.button(text=next_lesson, callback_data=Setting(action="next"))
    kb.button(text=f"{_checkbox(sub.notify)} Изменения в расписании", callback_data=Setting(action="changes"))
    kb.button(text=BTN_CHANGE[sub.kind], callback_data=Setting(action="change"))
    kb.adjust(1, 2, 1, 1, 1)
    return kb.as_markup()
