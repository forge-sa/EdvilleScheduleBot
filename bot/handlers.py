from __future__ import annotations

import logging
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from .config import Config
from .edville import Day, EdvilleClient, EdvilleError, Schedule
from .keyboards import BTN_CHANGE, BTN_TODAY, BTN_TOMORROW, BTN_WEEK, Pick, entity_picker, main_menu
from .render import render_day, render_week
from .storage import Storage, Subscription

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "Я показываю расписание EdVille и присылаю новое, когда оно меняется.\n\n"
    "/today — расписание на сегодня\n"
    "/tomorrow — на завтра\n"
    "/week — на всю неделю\n"
    "/class — сменить класс\n"
    "/stop — не присылать изменения\n"
    "/start — включить их снова"
)
SITE_DOWN = "⚠️ Сайт с расписанием сейчас не отвечает, показываю последнюю сохранённую версию."
LOAD_FAILED = "😕 Не получилось загрузить расписание. Попробуй ещё раз чуть позже."


async def ask_class(message: Message, edville: EdvilleClient) -> None:
    try:
        classes = await edville.list_classes()
    except EdvilleError:
        log.exception("could not load the class list")
        await message.answer(LOAD_FAILED)
        return
    await message.answer("В каком ты классе?", reply_markup=entity_picker(classes))


async def _subscription(message: Message, edville: EdvilleClient, db: Storage) -> Subscription | None:
    sub = db.get_subscription(message.chat.id)
    if sub is None:
        await message.answer("Сначала выбери класс 👇")
        await ask_class(message, edville)
    return sub


async def _load(edville: EdvilleClient, db: Storage, sub: Subscription) -> tuple[Schedule, str] | None:
    """The current timetable, or the stored copy (with a warning) if the site is down."""
    try:
        return await edville.get_schedule(sub.kind, sub.slug), ""
    except EdvilleError:
        log.warning("could not load /%s/%s/", sub.kind, sub.slug, exc_info=True)
        snapshot = db.get_snapshot(sub.kind, sub.slug)
        return (snapshot, SITE_DOWN) if snapshot else None


def _next_school_day(schedule: Schedule, after: int) -> Day | None:
    for step in range(1, 8):
        day = schedule.day((after + step) % 7)
        if day and day.lessons:
            return day
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, edville: EdvilleClient, db: Storage) -> None:
    sub = db.get_subscription(message.chat.id)
    if sub is None:
        await message.answer("👋 Привет! Я показываю расписание EdVille и сообщаю, когда оно меняется.")
        await ask_class(message, edville)
        return
    db.set_notify(message.chat.id, True)
    await message.answer(
        f"С возвращением! Твой класс — <b>{escape(sub.title)}</b>.\n"
        "Уведомления об изменениях включены 🔔",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("class"))
@router.message(F.text == BTN_CHANGE)
async def cmd_class(message: Message, edville: EdvilleClient) -> None:
    await ask_class(message, edville)


@router.callback_query(Pick.filter())
async def on_pick(query: CallbackQuery, callback_data: Pick, edville: EdvilleClient, db: Storage) -> None:
    try:
        entities = await edville.list_entities(callback_data.kind)
        entity = next((e for e in entities if e.slug == callback_data.slug), None)
        if entity is None:
            await query.answer("Такого класса больше нет, выбери ещё раз", show_alert=True)
            return
        schedule = await edville.get_schedule(entity.kind, entity.slug)
    except EdvilleError:
        log.exception("could not load /%s/%s/", callback_data.kind, callback_data.slug)
        await query.answer("Сайт с расписанием не отвечает, попробуй ещё раз", show_alert=True)
        return

    chat_id = query.message.chat.id
    already_watched = db.is_watched(entity.kind, entity.slug)
    db.subscribe(chat_id, entity)
    if not already_watched:
        # Nobody followed this timetable until now, so a stored snapshot (if
        # any) may be outdated; start comparing from what the user sees now.
        db.save_snapshot(schedule)

    await query.answer()
    confirmation = f"✅ Класс <b>{escape(entity.title)}</b>. Если расписание изменится, я пришлю новое."
    if isinstance(query.message, Message):
        await query.message.edit_text(confirmation)
    else:
        await query.bot.send_message(chat_id, confirmation)
    for text in render_week(schedule):
        await query.bot.send_message(chat_id, text, reply_markup=main_menu())


async def _send_day(message: Message, edville: EdvilleClient, db: Storage, config: Config, offset: int) -> None:
    sub = await _subscription(message, edville, db)
    if sub is None:
        return
    loaded = await _load(edville, db, sub)
    if loaded is None:
        await message.answer(LOAD_FAILED)
        return
    schedule, note = loaded

    weekday = (datetime.now(config.timezone).weekday() + offset) % 7
    day = schedule.day(weekday)
    if day is None or not day.lessons:
        day = _next_school_day(schedule, weekday)
        if day is None:
            await message.answer("В расписании пока нет уроков.")
            return
        when = "Сегодня" if offset == 0 else "Завтра"
        note = "\n".join(filter(None, [note, f"{when} уроков нет 🎉 Ближайший учебный день:"]))
    await message.answer(render_day(schedule, day, note), reply_markup=main_menu())


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def cmd_today(message: Message, edville: EdvilleClient, db: Storage, config: Config) -> None:
    await _send_day(message, edville, db, config, offset=0)


@router.message(Command("tomorrow"))
@router.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(message: Message, edville: EdvilleClient, db: Storage, config: Config) -> None:
    await _send_day(message, edville, db, config, offset=1)


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def cmd_week(message: Message, edville: EdvilleClient, db: Storage) -> None:
    sub = await _subscription(message, edville, db)
    if sub is None:
        return
    loaded = await _load(edville, db, sub)
    if loaded is None:
        await message.answer(LOAD_FAILED)
        return
    schedule, note = loaded
    if note:
        await message.answer(note)
    for text in render_week(schedule):
        await message.answer(text, reply_markup=main_menu())


@router.message(Command("stop"))
async def cmd_stop(message: Message, db: Storage) -> None:
    if db.get_subscription(message.chat.id) is None:
        await message.answer("Ты ещё не выбрал класс. Начни с /start")
        return
    db.set_notify(message.chat.id, False)
    await message.answer("🔕 Больше не буду присылать изменения расписания. Включить снова — /start")


@router.message(F.chat.type == "private")
async def fallback(message: Message, edville: EdvilleClient, db: Storage) -> None:
    if db.get_subscription(message.chat.id) is None:
        await ask_class(message, edville)
    else:
        await message.answer("Пользуйся кнопками внизу или /help 🙂", reply_markup=main_menu())
