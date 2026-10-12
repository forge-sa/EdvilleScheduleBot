from dataclasses import replace
from pathlib import Path

from bot.edville import Day, Schedule, parse_schedule
from bot.render import MAX_MESSAGE, diff, pack, render_changes, render_day, render_next, render_week
from tests.test_edville import at

FIXTURES = Path(__file__).parent / "fixtures"


def schedule_9a() -> Schedule:
    return parse_schedule((FIXTURES / "class_9a.html").read_text(), "class", "9a")


def edit_lesson(s: Schedule, day: int, num: int, **changes) -> Schedule:
    days = []
    for d in s.days:
        if d.index == day:
            d = Day(d.index, tuple(replace(i, **changes) if getattr(i, "num", None) == num else i for i in d.items))
        days.append(d)
    return replace(s, days=tuple(days))


def drop_lesson(s: Schedule, day: int, num: int) -> Schedule:
    days = [
        Day(d.index, tuple(i for i in d.items if not (d.index == day and getattr(i, "num", None) == num)))
        for d in s.days
    ]
    return replace(s, days=tuple(days))


def test_week_fits_in_one_message():
    messages = render_week(schedule_9a())
    assert len(messages) == 1
    assert "Класс 9А" in messages[0]
    assert "Казахский язык и литература" in messages[0]


def test_day_includes_breaks_and_teachers():
    s = schedule_9a()
    text = render_day(s, s.day(0))
    assert "Завтрак" in text and "Бакытжан Турсынов" in text and "🔟" in text


def test_html_is_escaped():
    s = edit_lesson(schedule_9a(), 0, 1, subject="<script>")
    assert "&lt;script&gt;" in render_day(s, s.day(0))


def test_pack_respects_limit():
    messages = pack(["x" * 3000, "y" * 3000, "z" * 10])
    assert [len(m) for m in messages] == [3000, 3012]
    assert all(len(m) <= MAX_MESSAGE for m in messages)


def test_no_changes():
    assert diff(schedule_9a(), schedule_9a()) == []


def test_subject_change():
    old = schedule_9a()
    new = edit_lesson(old, 0, 3, subject="Геометрия")
    [change] = diff(old, new)
    assert (change.day, change.num) == (0, 3)
    text = render_changes(old, new)
    assert "Пн, 3 урок" in text and "Алгебра" in text and "Геометрия" in text


def test_teacher_change():
    old = schedule_9a()
    new = edit_lesson(old, 1, 2, meta="Новый Учитель")
    assert "👤" in render_changes(old, new)


def test_removed_and_added_lessons():
    old = schedule_9a()
    new = drop_lesson(old, 4, 10)
    assert "➖" in render_changes(old, new)
    assert "➕" in render_changes(new, old)


def schedule_teacher() -> Schedule:
    return parse_schedule((FIXTURES / "teacher_tyler-quinton.html").read_text(), "teacher", "tyler-quinton")


def test_teacher_views_show_classes():
    s = schedule_teacher()
    day = render_day(s, s.day(0))
    assert day.startswith("📚 <b>Tyler Quinton · Понедельник</b>")
    assert "класс 7А" in day
    assert render_week(s)[0].startswith("🗓 <b>Tyler Quinton · расписание на неделю</b>")


def test_teacher_class_change():
    old = schedule_teacher()
    new = edit_lesson(old, 0, 2, meta="класс 9А")
    text = render_changes(old, new)
    assert "Tyler Quinton" in text
    assert "🏫 <b>Пн, 2 урок</b>, Физика: класс 7А → класс 9А" in text


def test_next_lesson_message():
    s = schedule_9a()

    def next_text(day, hhmm):
        now = at(day, hhmm)
        return render_next(s, *s.next_lesson(now), now)

    text = next_text(0, "10:43")
    assert text.startswith("⏭ <b>Следующий урок · Класс 9А</b>")
    assert "4️⃣ <b>Алгебра</b>" in text and "10:55–11:35 · Жасұланбек Бахтияр" in text
    assert text.endswith("⏳ Через 12 мин")
    assert next_text(0, "07:00").endswith("⏳ Через 1 ч 30 мин")
    assert next_text(0, "17:00").endswith("📅 Завтра")
    assert next_text(4, "17:00").endswith("📅 В понедельник")
