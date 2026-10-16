from __future__ import annotations

import logging
from datetime import datetime, time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import JOIN_TRANSITION, ChatMemberUpdatedFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message

from .config import Config
from .edville import Day, EdvilleClient, EdvilleError, Entity, Kind, Schedule
from .keyboards import (
    BACK_TO_ROLE,
    BTN_CHANGE,
    BTN_NEXT,
    BTN_TODAY,
    BTN_TOMORROW,
    BTN_WEEK,
    NOOP,
    TEACHERS_PER_PAGE,
    Pick,
    Role,
    Setting,
    TeacherPage,
    chat_menu,
    class_picker,
    entity_picker,
    main_menu,
    role_picker,
    settings_menu,
    teacher_picker,
)
from .render import label, render_day, render_next, render_week
from .storage import Storage, Subscription

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "Я показываю расписание EdVille и присылаю новое, когда оно меняется.\n\n"
    "/next — следующий урок\n"
    "/today — расписание на сегодня\n"
    "/tomorrow — на завтра\n"
    "/week — на всю неделю\n"
    "/settings — присылать расписание на день и следующий урок автоматически\n"
    "/change — выбрать другой класс или учителя\n"
    "/stop — не присылать изменения\n"
    "/start — включить их снова\n\n"
    "Меня можно добавить в чат класса."
)
GROUP_HELP = (
    "Я показываю в этом чате расписание EdVille.\n\n"
    "/next — следующий урок\n"
    "/today — расписание на сегодня\n"
    "/tomorrow — на завтра\n"
    "/week — на всю неделю\n"
    "/settings — что присылать в чат автоматически\n"
    "/change — выбрать другой класс\n\n"
    "Настройки и класс меняют только администраторы чата."
)
GREETING = "👋 Здравствуйте! Я показываю расписание EdVille и сообщаю, когда оно меняется."
ASK_ROLE = "Вы ученик или учитель?"
ASK_CLASS = "В каком вы классе?"
ASK_GROUP_CLASS = "Расписание какого класса показывать в этом чате?"
ASK_TEACHER = "Найдите себя в списке или напишите фамилию 👇"
SITE_DOWN = "⚠️ Сайт с расписанием сейчас не отвечает, показываю последнюю сохранённую версию."
LOAD_FAILED = "😕 Не получилось загрузить расписание. Попробуйте ещё раз чуть позже."
ADMINS_ONLY = "Это могут менять только администраторы чата."
SETTINGS = (
    "⚙️ <b>Настройки · {label}</b>\n\n"
    "Отметьте, что присылать сюда автоматически:\n"
    "📅 расписание на день — в учебные дни, в выбранное время;\n"
    "⏭ следующий урок — как только закончится урок;\n"
    "🔔 новое расписание — когда его поменяют на сайте.\n\n"
    "/today и /next работают всегда."
)


class Picking(StatesGroup):
    teacher = State()  # the teacher list is open, so typed text is a name search


# --- teacher list helpers --------------------------------------------------------

_KAZAKH = str.maketrans("әғқңөұүһіё", "агкноуухие")


def _norm(text: str) -> str:
    # Lets "кудайбергенова" find "Құдайбергенова".
    return text.lower().translate(_KAZAKH)


def real_teachers(entities: list[Entity]) -> list[Entity]:
    """Drop the site's placeholder "teachers" for activities, e.g. "Study Time 9A" or "Вокал 2"."""
    return [e for e in entities if not any(ch.isdigit() for ch in e.title)]


def search_teachers(teachers: list[Entity], query: str) -> list[Entity]:
    words = _norm(query).split()
    return [t for t in teachers if all(w in _norm(t.title) for w in words)]


# --- replying ----------------------------------------------------------------------


async def _reply(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Answer a message, or replace the message whose button was pressed."""
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
        return
    await event.answer()
    if isinstance(event.message, Message):
        try:
            await event.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            # Too old to edit; send a fresh message instead.
    await event.bot.send_message(event.message.chat.id, text, reply_markup=markup)


async def _fail(event: Message | CallbackQuery, text: str) -> None:
    if isinstance(event, Message):
        await event.answer(text)
    else:
        await event.answer(text, show_alert=True)


def _chat_id(event: Message | CallbackQuery) -> int:
    return event.chat.id if isinstance(event, Message) else event.message.chat.id


def _in_group(event: Message | CallbackQuery) -> bool:
    return _chat_id(event) < 0


async def _check_admin(event: Message | CallbackQuery) -> bool:
    """Anyone in a group can look at the timetable, but only its admins can
    change what the group follows or gets sent. Says so to everyone else."""
    if not _in_group(event):
        return True
    chat_id = _chat_id(event)
    if isinstance(event, Message) and event.sender_chat and event.sender_chat.id == chat_id:
        return True  # an anonymous admin, writing as the group
    member = await event.bot.get_chat_member(chat_id, event.from_user.id)
    if member.status in ("creator", "administrator"):
        return True
    await _fail(event, ADMINS_ONLY)
    return False


async def ask_role(event: Message | CallbackQuery, state: FSMContext, edville: EdvilleClient, intro: str = "") -> None:
    """The first question: student or teacher. A group chat follows a class, so it skips straight to the classes."""
    if _in_group(event):
        await ask_entity(event, "class", edville, state, intro=intro)
        return
    await state.clear()
    await _reply(event, f"{intro}\n\n{ASK_ROLE}" if intro else ASK_ROLE, role_picker())


async def ask_entity(
    event: Message | CallbackQuery,
    kind: Kind,
    edville: EdvilleClient,
    state: FSMContext,
    page: int = 0,
    intro: str = "",
) -> None:
    """Show the class list or one page of the teacher list."""
    try:
        entities = await edville.list_entities(kind)
    except EdvilleError:
        log.exception("could not load the %s list", kind)
        await _fail(event, LOAD_FAILED)
        return
    if kind == "class":
        await state.clear()
        group = _in_group(event)
        question = ASK_GROUP_CLASS if group else ASK_CLASS
        await _reply(event, f"{intro}\n\n{question}" if intro else question, class_picker(entities, back=not group))
    else:
        await state.set_state(Picking.teacher)
        await _reply(event, ASK_TEACHER, teacher_picker(real_teachers(entities), page))


async def _subscription(
    message: Message, edville: EdvilleClient, db: Storage, state: FSMContext
) -> Subscription | None:
    sub = db.get_subscription(message.chat.id)
    if sub is None:
        await ask_role(message, state, edville, intro="Сначала выберите, чьё расписание показывать.")
    return sub


def _settings_text(sub: Subscription) -> str:
    return SETTINGS.format(label=label(sub.kind, sub.title))


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


# --- choosing whose timetable to follow ------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, edville: EdvilleClient, db: Storage, state: FSMContext) -> None:
    sub = db.get_subscription(message.chat.id)
    if sub is None:
        await ask_role(message, state, edville, intro=GREETING)
        return
    if _in_group(message):
        await message.answer(_settings_text(sub), reply_markup=settings_menu(sub))
        return
    await state.clear()
    db.set_notify(message.chat.id, True)
    await message.answer(
        f"С возвращением! Ваше расписание: <b>{label(sub.kind, sub.title)}</b>.\n"
        "Уведомления об изменениях включены 🔔",
        reply_markup=main_menu(sub.kind),
    )


@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION), F.chat.type.in_({"group", "supergroup"}))
async def on_added_to_group(event: ChatMemberUpdated, edville: EdvilleClient) -> None:
    try:
        classes = await edville.list_entities("class")
    except EdvilleError:
        log.exception("could not load the class list")
        await event.answer(f"{GREETING}\n\nЧтобы выбрать класс, напишите /start")
        return
    await event.answer(f"{GREETING}\n\n{ASK_GROUP_CLASS}", reply_markup=class_picker(classes, back=False))


@router.message(F.migrate_to_chat_id)
async def on_group_migrated(message: Message, db: Storage) -> None:
    db.move_chat(message.chat.id, message.migrate_to_chat_id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(GROUP_HELP if _in_group(message) else HELP)


@router.message(Command("change"))
async def cmd_change(message: Message, edville: EdvilleClient, state: FSMContext) -> None:
    if await _check_admin(message):
        await ask_role(message, state, edville)


@router.message(F.text.in_(BTN_CHANGE.values()))
async def menu_change(message: Message, edville: EdvilleClient, state: FSMContext) -> None:
    kind: Kind = "teacher" if message.text == BTN_CHANGE["teacher"] else "class"
    await ask_entity(message, kind, edville, state)


@router.callback_query(Role.filter())
async def on_role(query: CallbackQuery, callback_data: Role, edville: EdvilleClient, state: FSMContext) -> None:
    kind: Kind = "teacher" if callback_data.kind == "teacher" else "class"
    await ask_entity(query, kind, edville, state)


@router.callback_query(F.data == BACK_TO_ROLE)
async def on_back(query: CallbackQuery, edville: EdvilleClient, state: FSMContext) -> None:
    await ask_role(query, state, edville)


@router.callback_query(TeacherPage.filter())
async def on_teacher_page(
    query: CallbackQuery, callback_data: TeacherPage, edville: EdvilleClient, state: FSMContext
) -> None:
    await ask_entity(query, "teacher", edville, state, page=callback_data.page)


@router.callback_query(F.data == NOOP)
async def on_noop(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(Pick.filter())
async def on_pick(
    query: CallbackQuery, callback_data: Pick, edville: EdvilleClient, db: Storage, state: FSMContext
) -> None:
    kind: Kind = "teacher" if callback_data.kind == "teacher" else "class"
    if not await _check_admin(query):
        return
    try:
        entities = await edville.list_entities(kind)
        entity = next((e for e in entities if e.slug == callback_data.slug), None)
        if entity is None:
            await query.answer("Этого расписания больше нет на сайте, выберите ещё раз", show_alert=True)
            return
        schedule = await edville.get_schedule(entity.kind, entity.slug)
    except EdvilleError:
        log.exception("could not load /%s/%s/", kind, callback_data.slug)
        await query.answer("Сайт с расписанием не отвечает, попробуйте ещё раз", show_alert=True)
        return

    await state.clear()
    chat_id = query.message.chat.id
    already_watched = db.is_watched(entity.kind, entity.slug)
    db.subscribe(chat_id, entity)
    if not already_watched:
        # Nobody followed this timetable until now, so a stored snapshot (if
        # any) may be outdated; start comparing from what the user sees now.
        db.save_snapshot(schedule)

    if _in_group(query):
        sub = db.get_subscription(chat_id)
        await _reply(query, f"✅ Готово!\n\n{_settings_text(sub)}", settings_menu(sub))
        return
    await _reply(query, f"✅ {label(entity.kind, entity.title)}. Если расписание изменится, я пришлю новое.")
    for text in render_week(schedule):
        await query.bot.send_message(chat_id, text, reply_markup=main_menu(entity.kind))


@router.message(Command("settings"))
async def cmd_settings(message: Message, edville: EdvilleClient, db: Storage, state: FSMContext) -> None:
    sub = await _subscription(message, edville, db, state)
    if sub is not None:
        await message.answer(_settings_text(sub), reply_markup=settings_menu(sub))


def _shift(at: time, minutes: int) -> time:
    total = (at.hour * 60 + at.minute + minutes) % (24 * 60)
    return time(total // 60, total % 60)


@router.callback_query(Setting.filter())
async def on_setting(
    query: CallbackQuery, callback_data: Setting, edville: EdvilleClient, db: Storage, state: FSMContext
) -> None:
    chat_id = query.message.chat.id
    sub = db.get_subscription(chat_id)
    if sub is None:
        await query.answer("Сначала выберите расписание: /start", show_alert=True)
        return
    if not await _check_admin(query):
        return
    action = callback_data.action
    if action == "change":
        await ask_role(query, state, edville)
        return
    if action == "daily":
        db.set_daily(chat_id, not sub.daily, sub.daily_time)
    elif action in ("earlier", "later"):
        db.set_daily(chat_id, sub.daily, _shift(sub.daily_time, -15 if action == "earlier" else 15))
    elif action == "next":
        db.set_next_lesson(chat_id, not sub.next_lesson)
    elif action == "changes":
        db.set_notify(chat_id, not sub.notify)
    sub = db.get_subscription(chat_id)
    await _reply(query, _settings_text(sub), settings_menu(sub))


# --- showing the timetable ----------------------------------------------------------


async def _send_day(
    message: Message, edville: EdvilleClient, db: Storage, state: FSMContext, config: Config, offset: int
) -> None:
    sub = await _subscription(message, edville, db, state)
    if sub is None:
        return
    loaded = await _load(edville, db, sub)
    if loaded is None:
        await message.answer(LOAD_FAILED)
        return
    schedule, note = loaded
    menu = chat_menu(message.chat.id, sub.kind)

    weekday = (datetime.now(config.timezone).weekday() + offset) % 7
    day = schedule.day(weekday)
    if day is None or not day.lessons:
        day = _next_school_day(schedule, weekday)
        if day is None:
            await message.answer("В расписании пока нет уроков.", reply_markup=menu)
            return
        when = "Сегодня" if offset == 0 else "Завтра"
        note = "\n".join(filter(None, [note, f"{when} уроков нет 🎉 Ближайший учебный день:"]))
    await message.answer(render_day(schedule, day, note), reply_markup=menu)


@router.message(Command("next"))
@router.message(F.text == BTN_NEXT)
async def cmd_next(message: Message, edville: EdvilleClient, db: Storage, state: FSMContext, config: Config) -> None:
    sub = await _subscription(message, edville, db, state)
    if sub is None:
        return
    loaded = await _load(edville, db, sub)
    if loaded is None:
        await message.answer(LOAD_FAILED)
        return
    schedule, note = loaded
    menu = chat_menu(message.chat.id, sub.kind)
    now = datetime.now(config.timezone)
    found = schedule.next_lesson(now)
    if found is None:
        await message.answer("В расписании пока нет уроков.", reply_markup=menu)
        return
    await message.answer(render_next(schedule, *found, now, note), reply_markup=menu)


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def cmd_today(message: Message, edville: EdvilleClient, db: Storage, state: FSMContext, config: Config) -> None:
    await _send_day(message, edville, db, state, config, offset=0)


@router.message(Command("tomorrow"))
@router.message(F.text == BTN_TOMORROW)
async def cmd_tomorrow(
    message: Message, edville: EdvilleClient, db: Storage, state: FSMContext, config: Config
) -> None:
    await _send_day(message, edville, db, state, config, offset=1)


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def cmd_week(message: Message, edville: EdvilleClient, db: Storage, state: FSMContext) -> None:
    sub = await _subscription(message, edville, db, state)
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
        await message.answer(text, reply_markup=chat_menu(message.chat.id, sub.kind))


@router.message(Command("stop"))
async def cmd_stop(message: Message, db: Storage) -> None:
    if db.get_subscription(message.chat.id) is None:
        await message.answer("Вы ещё ничего не выбрали. Начните с /start")
        return
    if not await _check_admin(message):
        return
    db.set_notify(message.chat.id, False)
    await message.answer("🔕 Больше не буду присылать изменения расписания. Включить снова — /start")


# --- free text -----------------------------------------------------------------------


@router.message(Picking.teacher, F.text)
async def on_teacher_search(message: Message, edville: EdvilleClient) -> None:
    try:
        teachers = real_teachers(await edville.list_entities("teacher"))
    except EdvilleError:
        log.exception("could not load the teacher list")
        await message.answer(LOAD_FAILED)
        return
    matches = search_teachers(teachers, message.text)
    if not matches:
        await message.answer(
            "Никого не нашёл 🤔 Попробуйте написать фамилию иначе или выберите себя в списке:",
            reply_markup=teacher_picker(teachers),
        )
        return
    text = "Выберите себя 👇"
    if len(matches) > TEACHERS_PER_PAGE:
        text = f"Нашлось много совпадений, показываю первые {TEACHERS_PER_PAGE}. Уточните фамилию или выберите 👇"
    await message.answer(text, reply_markup=entity_picker(matches[:TEACHERS_PER_PAGE], per_row=2))


@router.message(F.chat.type == "private")
async def fallback(message: Message, edville: EdvilleClient, db: Storage, state: FSMContext) -> None:
    sub = db.get_subscription(message.chat.id)
    if sub is None:
        await ask_role(message, state, edville)
    else:
        await message.answer("Пользуйтесь кнопками внизу или /help 🙂", reply_markup=main_menu(sub.kind))
