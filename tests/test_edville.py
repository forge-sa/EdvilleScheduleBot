from pathlib import Path

import pytest

from bot.edville import Break, Lesson, ParseError, Schedule, parse_entities, parse_schedule

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
