"""Turning schedules (and differences between two schedules) into Telegram HTML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from .edville import DAY_NAMES, Break, Day, Kind, Lesson, Schedule

MAX_MESSAGE = 4096
MAX_CHANGES_SHOWN = 20

_KEYCAPS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_SHORT_DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_ON_DAY = ("В понедельник", "Во вторник", "В среду", "В четверг", "В пятницу", "В субботу", "В воскресенье")


def _num(n: int) -> str:
    return _KEYCAPS[n] if 0 <= n < len(_KEYCAPS) else f"{n}."


def label(kind: Kind, title: str) -> str:
    """HTML-escaped heading: "Класс 9А" for a class, just the name for a teacher."""
    return escape(title) if kind == "teacher" else f"Класс {escape(title)}"


def _heading(schedule: Schedule) -> str:
    return label(schedule.kind, schedule.title)


def render_day(schedule: Schedule, day: Day, note: str = "") -> str:
    lines = []
    if note:
        lines += [note, ""]
    lines += [f"📚 <b>{_heading(schedule)} · {day.name}</b>", ""]
    if not day.lessons:
        lines.append("Уроков нет 🎉")
    for item in day.items:
        if isinstance(item, Break):
            lines.append(f"<i>{escape(item.name)} · {item.time}</i>")
        else:
            lines += _lesson_lines(item)
    return "\n".join(lines).strip()


def _lesson_lines(lesson: Lesson) -> list[str]:
    lines = [f"{_num(lesson.num)} <b>{escape(lesson.subject)}</b>"]
    details = " · ".join(x for x in (lesson.time, escape(lesson.meta)) if x)
    if details:
        lines.append(f"      {details}")
    return lines


def _minutes(minutes: int) -> str:
    hours, minutes = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} ч" if hours else "", f"{minutes} мин" if minutes else "") if part)


def render_next(schedule: Schedule, day: Day, lesson: Lesson, now: datetime, note: str = "") -> str:
    """The lesson found by `Schedule.next_lesson`, with when it starts."""
    lines = [note, ""] if note else []
    lines += [f"⏭ <b>Следующий урок · {_heading(schedule)}</b>", ""]
    lines += _lesson_lines(lesson)
    lines.append("")
    if day.index == now.weekday() and lesson.start > now.time():
        seconds = (lesson.start.hour * 60 + lesson.start.minute) * 60
        seconds -= now.hour * 3600 + now.minute * 60 + now.second
        lines.append(f"⏳ Через {_minutes(-(-seconds // 60))}")
    elif day.index == (now.weekday() + 1) % 7:
        lines.append("📅 Завтра")
    else:
        lines.append(f"📅 {_ON_DAY[day.index]}")
    return "\n".join(lines)


def render_week(schedule: Schedule) -> list[str]:
    """The whole week, compact (no breaks), split into as few messages as possible."""
    blocks = [f"🗓 <b>{_heading(schedule)} · расписание на неделю</b>"]
    for day in schedule.days:
        lines = [f"<b>{day.name.upper()}</b>"]
        if not day.lessons:
            lines.append("Уроков нет")
        for lesson in day.lessons:
            start = lesson.time.split("–")[0]
            line = f"<code>{lesson.num:>2}</code> {start} <b>{escape(lesson.subject)}</b>"
            if lesson.meta:
                line += f" — {escape(lesson.meta)}"
            lines.append(line)
        blocks.append("\n".join(lines))
    return pack(blocks)


def pack(blocks: list[str], sep: str = "\n\n") -> list[str]:
    """Join blocks into messages that fit Telegram's length limit."""
    messages: list[str] = []
    for block in blocks:
        if messages and len(messages[-1]) + len(sep) + len(block) <= MAX_MESSAGE:
            messages[-1] += sep + block
        else:
            messages.append(block[:MAX_MESSAGE])
    return messages


# --- changes -------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    day: int
    num: int
    old: tuple[Lesson, ...]
    new: tuple[Lesson, ...]


def _slots(day: Day | None) -> dict[int, tuple[Lesson, ...]]:
    slots: dict[int, list[Lesson]] = {}
    for lesson in day.lessons if day else []:
        slots.setdefault(lesson.num, []).append(lesson)
    return {num: tuple(lessons) for num, lessons in slots.items()}


def _key(lessons: tuple[Lesson, ...]) -> tuple:
    return tuple((l.subject, l.meta, l.time) for l in lessons)


def diff(old: Schedule, new: Schedule) -> list[Change]:
    """Lesson-by-lesson differences, keyed by (day, lesson number)."""
    changes = []
    for index in range(len(DAY_NAMES)):
        old_slots, new_slots = _slots(old.day(index)), _slots(new.day(index))
        for num in sorted(old_slots.keys() | new_slots.keys()):
            before, after = old_slots.get(num, ()), new_slots.get(num, ())
            if _key(before) != _key(after):
                changes.append(Change(index, num, before, after))
    return changes


def _describe(lessons: tuple[Lesson, ...]) -> str:
    return " / ".join(
        f"{escape(l.subject)} ({escape(l.meta)})" if l.meta else escape(l.subject) for l in lessons
    )


def _render_change(c: Change, kind: Kind) -> str:
    where = f"<b>{_SHORT_DAYS[c.day]}, {c.num} урок</b>"
    if not c.old:
        return f"➕ {where}: {_describe(c.new)}"
    if not c.new:
        return f"➖ {where}: <s>{_describe(c.old)}</s> — урока нет"
    if len(c.old) == len(c.new) == 1:
        o, n = c.old[0], c.new[0]
        if o.subject == n.subject and o.meta == n.meta:
            return f"🕒 {where}, {escape(n.subject)}: {o.time} → {n.time}"
        if o.subject == n.subject:
            # meta is the teacher on a class timetable and the class on a teacher's
            icon = "🏫" if kind == "teacher" else "👤"
            return f"{icon} {where}, {escape(n.subject)}: {escape(o.meta)} → {escape(n.meta)}"
    return f"🔄 {where}: {_describe(c.old)} → {_describe(c.new)}"


def render_changes(old: Schedule, new: Schedule) -> str:
    changes = diff(old, new)
    lines = [f"🔔 <b>Расписание изменилось · {_heading(new)}</b>", ""]
    if not changes:
        lines.append("Изменилось время перемен или другие детали.")
    lines += [_render_change(c, new.kind) for c in changes[:MAX_CHANGES_SHOWN]]
    if len(changes) > MAX_CHANGES_SHOWN:
        lines.append(f"…и ещё {len(changes) - MAX_CHANGES_SHOWN}")
    lines += ["", "Новое расписание 👇"]
    return "\n".join(lines)
