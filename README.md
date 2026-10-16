# EdVille Schedule Bot

A Telegram bot that shows the EdVille timetable from
[edvilletimetable.pythonanywhere.com](https://edvilletimetable.pythonanywhere.com)
to students and teachers, and sends them the new timetable whenever theirs changes.

## What it does

- `/start` asks **"Вы ученик или учитель?"**
  - **Students** pick their class from the classes listed on the site.
  - **Teachers** pick their name from a paged list (◀️ 1/3 ▶️) or type part of their
    surname to search. Search ignores case and Kazakh letters, so "кудайбергенова"
    finds "Құдайбергенова". The site's placeholder entries ("Study Time 9A",
    "Sport Activity 2", "Вокал 2", "Драма 3", i.e. any name containing a digit) are hidden.
  - Every list has a "⬅️ Назад" button that returns to the first question.
- After the pick, the bot sends the week's timetable and shows a menu:
  **⏭ Следующий урок · 📅 Сегодня · ➡️ Завтра · 🗓 Неделя · 🔁 Сменить класс / Сменить учителя**.
  A student sees each lesson's teacher; a teacher sees each lesson's class.
- **⏭ Следующий урок** (or `/next`) shows the first lesson that starts after the current
  time, with a countdown ("⏳ Через 12 мин"). Breakfast, brunch, lunch and snack are
  skipped, a lesson already in progress doesn't count, and teachers' free periods are
  skipped. After the last lesson it shows the next school day's first lesson
  ("📅 Завтра", "📅 В понедельник").
- Every few minutes it checks each timetable that someone follows. When one changes,
  its followers get a list of what changed (e.g. `🔄 Пн, 3 урок: Алгебра → Геометрия`,
  or `🏫 Пн, 2 урок, Физика: класс 7А → класс 9А` for a teacher), followed by the new week.
- `/change` starts over with the student/teacher question. `/stop` turns notifications
  off and `/start` turns them back on.
- `/settings` turns on posts the bot sends by itself, each with a ✅ checkbox:
  - **📅 Расписание на день**: the day's timetable at a chosen time (07:30 by default,
    ⏪ / ⏩ move it by 15 minutes). Sent only on days that have lessons.
  - **⏭ Следующий урок**: the next lesson as soon as each lesson ends ("⏳ Через 5 мин").
    After the last lesson of the day this is the next school day's first lesson.
  - **🔔 Изменения в расписании**: the change notifications described above.

  The first two are off until someone turns them on. `/today`, `/next` and the other
  commands work whatever the settings are.

### In a group chat

Add the bot to a class's group chat and it asks which class the chat follows (there is
no student/teacher question). After an admin picks one, it shows the `/settings` panel.

- Anyone in the group can use `/today`, `/next`, `/tomorrow`, `/week` and `/settings`
  to look. Only the group's admins can pick the class, press the settings buttons,
  `/change` or `/stop`.
- No menu keyboard in groups: it would pop up for every member. Use the commands
  (`/help` lists them).
- Works with BotFather's default privacy mode on, since the bot only needs commands
  and button presses. In a group with several bots, `/today@your_bot` always reaches
  this one.
- When Telegram turns a group into a supergroup (the chat gets a new id), the bot
  moves the group's class and settings to the new chat.

The bot replies in Russian, the same language as the site, and addresses everyone as "вы".

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Install and configure:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env        # then put your token in BOT_TOKEN
   ```

3. Run:

   ```bash
   .venv/bin/python -m bot
   ```

The bot uses long polling, so it needs no public URL. It only needs to stay running.
Any always-on machine or VPS works, and so does a PythonAnywhere "Always-on task"
on a paid account.

Settings (in `.env`):

| Variable        | Default                                        | Meaning                              |
|-----------------|------------------------------------------------|--------------------------------------|
| `BOT_TOKEN`     | (required)                                     | Token from @BotFather                |
| `POLL_INTERVAL` | `180`                                          | Seconds between change checks        |
| `DB_PATH`       | `data/bot.sqlite3`                             | SQLite file with users and snapshots |
| `TIMEZONE`      | `Asia/Almaty`                                  | "Today", "tomorrow" and post times   |
| `SITE_URL`      | `https://edvilletimetable.pythonanywhere.com`  | Timetable site                       |

## How it works

```
bot/
  edville.py    "API" for the timetable site: downloads pages, parses them into dataclasses
  storage.py    SQLite: which chat follows which timetable, and the last known version of each
  render.py     Telegram message formatting and the lesson-by-lesson diff
  watcher.py    background loop that detects changes and notifies subscribers
  announcer.py  background loop that posts the day's timetable and the next lesson
  delivery.py   sending messages nobody asked for; forgets chats that blocked the bot
  handlers.py   commands, buttons, pickers, teacher search, /settings, group admin checks
  keyboards.py  inline and reply keyboards
  __main__.py   wiring and startup
```

**No JSON API on the site.** The site serves only HTML, so `bot/edville.py`
reads the public pages (`/`, `/class/<slug>/`, `/teacher/<slug>/`). Class and
teacher pages share the same layout, so one parser handles both. Everything
else goes through `EdvilleClient`. If the site ever gets a JSON endpoint, only
that file needs to change. If you control the Django app, adding a small JSON
view would make the bot independent of the page layout.

**Change detection.** The watcher downloads every followed timetable and compares
it to the stored snapshot. A new version is announced only after it appears on
**two checks in a row**. Otherwise a timetable admin editing cells one at a time
would send students a notification after every cell. With the default 3-minute
interval, students hear about a change 3–6 minutes after the last edit. If the
site is down or returns an unexpected page, that check is skipped, so an outage
never looks like "all lessons removed". Users who block the bot are removed from
the list.

If the site is down when someone asks for their timetable, the bot shows the last
saved copy with a warning.

**Scheduled posts.** Every 20 seconds the announcer sends whatever fell due since its
previous check, so each post goes out once. It reads the snapshots the watcher keeps,
so it downloads nothing. Posts that fall due while the bot is stopped are skipped,
not sent late.

## Notes on the site's data

- Class (5) has a different bell schedule, so a teacher's "6 урок" can mean
  12:25 for one class and 13:10 for (5). The bot shows each lesson with its own time.
- Some teachers appear twice under different spellings: "Anuar Kabdulkak" /
  "Ануар Қабдулқақ" and "Kuanyshuly Sapar" / "Куанышулы Сапар". In each pair only one
  entry has most of the lessons, so this is best fixed on the site.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests run offline against saved copies of the site's pages in `tests/fixtures/`.
They cover the parser, formatting and diffs, the change watcher (settling, outages,
muted and blocked users, teachers), the scheduled posts, the database upgrade, and the
full student, teacher and group-chat conversations driven through aiogram's dispatcher.
