from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .edville import Entity

BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "➡️ Завтра"
BTN_WEEK = "🗓 Неделя"
BTN_CHANGE = "🔁 Сменить класс"


class Pick(CallbackData, prefix="pick"):
    # `kind` is already part of the callback so teachers can reuse it later.
    kind: str
    slug: str


def entity_picker(entities: list[Entity], per_row: int = 4) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for e in entities:
        kb.button(text=e.title, callback_data=Pick(kind=e.kind, slug=e.slug))
    kb.adjust(per_row)
    return kb.as_markup()


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_WEEK), KeyboardButton(text=BTN_CHANGE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
