"""Turning schedules (and differences between two schedules) into Telegram HTML."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .edville import DAY_NAMES, Break, Day, Lesson, Schedule

MAX_MESSAGE = 4096
MAX_CHANGES_SHOWN = 20

_KEYCAPS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_SHORT_DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _num(n: int) -> str:
    return _KEYCAPS[n] if 0 <= n < len(_KEYCAPS) else f"{n}."


def _heading(schedule: Schedule) -> str:
    return escape(schedule.title) if schedule.kind == "teacher" else f"Класс {escape(schedule.title)}"


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
            details = " · ".join(x for x in (item.time, escape(item.meta)) if x)
            lines.append(f"{_num(item.num)} <b>{escape(item.subject)}</b>")
            if details:
                lines.append(f"      {details}")
    return "\n".join(lines).strip()


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


def _render_change(c: Change) -> str:
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
            return f"👤 {where}, {escape(n.subject)}: {escape(o.meta)} → {escape(n.meta)}"
    return f"🔄 {where}: {_describe(c.old)} → {_describe(c.new)}"


def render_changes(old: Schedule, new: Schedule) -> str:
    changes = diff(old, new)
    lines = [f"🔔 <b>Расписание изменилось · {_heading(new)}</b>", ""]
    if not changes:
        lines.append("Изменилось время перемен или другие детали.")
    lines += [_render_change(c) for c in changes[:MAX_CHANGES_SHOWN]]
    if len(changes) > MAX_CHANGES_SHOWN:
        lines.append(f"…и ещё {len(changes) - MAX_CHANGES_SHOWN}")
    lines += ["", "Новое расписание 👇"]
    return "\n".join(lines)
