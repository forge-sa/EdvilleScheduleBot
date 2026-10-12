from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot.edville import Break, Day, Lesson, ParseError, Schedule, parse_entities, parse_schedule

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_class_list():
    classes = parse_entities(load("home.html"), "class")
    assert [c.slug for c in classes][:3] == ["5", "1a", "2a"]
    assert classes[-1].title == "11С"
    assert all(c.kind == "class" for c in classes)


def test_teacher_list_normalises_whitespace():
    teachers = parse_entities(load("home.html"), "teacher")
    assert any(t.slug == "tyler-quinton" and t.title == "Tyler Quinton" for t in teachers)
    assert any(t.title == "Адиль Егинбай" for t in teachers)  # double space on the site


def test_class_schedule():
    s = parse_schedule(load("class_9a.html"), "class", "9a")
    assert s.title == "9А"
    assert [d.name for d in s.days] == ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]

    monday = s.day(0)
    assert monday.items[0] == Break("🌅 Завтрак", "08:00–08:30")
    assert monday.lessons[0] == Lesson(1, "08:30–09:10", "Казахский язык и литература", "Бакытжан Турсынов", "lang")
    assert [l.num for l in monday.lessons] == list(range(1, 11))


def test_sparse_class_schedule():
    s = parse_schedule(load("class_5.html"), "class", "5")
    assert s.day(0).lessons[0].num == 6
    assert Break("🍽️ Обед", "12:20–13:10") in s.day(0).items


def test_teacher_schedule_lists_classes():
    s = parse_schedule(load("teacher_tyler-quinton.html"), "teacher", "tyler-quinton")
    assert s.title == "Tyler Quinton"
    assert s.day(0).lessons[0] == Lesson(2, "09:15–09:55", "Физика", "класс 7А", "science")


def test_not_a_timetable_page():
    with pytest.raises(ParseError):
        parse_schedule(load("home.html"), "class", "9a")


def test_roundtrip_and_fingerprint():
    s = parse_schedule(load("class_9a.html"), "class", "9a")
    restored = Schedule.from_dict(s.to_dict())
    assert restored == s
    assert restored.fingerprint() == s.fingerprint()
    assert parse_schedule(load("class_5.html"), "class", "5").fingerprint() != s.fingerprint()


def at(day: int, hhmm: str) -> datetime:
    """A moment in the week of 12 Oct 2026, which starts on a Monday (day 0)."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, 12 + day, hour, minute, tzinfo=ZoneInfo("Asia/Almaty"))


def next_of(schedule, day, hhmm):
    found_day, lesson = schedule.next_lesson(at(day, hhmm))
    return found_day.index, lesson.num, lesson.start.strftime("%H:%M")


@pytest.mark.parametrize(
    "day, now, expected",
    [
        (0, "07:00", (0, 1, "08:30")),  # before school
        (0, "08:15", (0, 1, "08:30")),  # during breakfast
        (0, "08:30", (0, 2, "09:15")),  # lesson 1 just started, so it's not "next"
        (0, "10:10", (0, 4, "10:55")),  # during lesson 3: brunch is skipped
        (0, "10:45", (0, 4, "10:55")),  # during brunch
        (0, "13:20", (0, 7, "13:55")),  # during lunch
        (0, "16:30", (1, 1, "08:30")),  # during the last lesson -> tomorrow
        (4, "17:00", (0, 1, "08:30")),  # Friday evening -> Monday
        (5, "12:00", (0, 1, "08:30")),  # Saturday -> Monday
        (6, "23:59", (0, 1, "08:30")),  # Sunday night -> Monday
    ],
)
def test_next_lesson_for_class(day, now, expected):
    s = parse_schedule(load("class_9a.html"), "class", "9a")
    assert next_of(s, day, now) == expected


def test_next_lesson_skips_free_periods_for_teachers():
    s = parse_schedule(load("teacher_tyler-quinton.html"), "teacher", "tyler-quinton")
    assert next_of(s, 0, "10:00") == (0, 9, "15:25")


def test_next_lesson_with_late_start():
    s = parse_schedule(load("class_5.html"), "class", "5")
    assert next_of(s, 0, "08:00") == (0, 6, "13:10")


def test_no_lessons_at_all():
    s = parse_schedule(load("class_9a.html"), "class", "9a")
    empty = Schedule(s.kind, s.slug, s.title, tuple(Day(d.index, ()) for d in s.days))
    assert empty.next_lesson(at(0, "09:00")) is None
