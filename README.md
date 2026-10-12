# EdVille Schedule Bot

A Telegram bot that shows the EdVille timetable from
[edvilletimetable.pythonanywhere.com](https://edvilletimetable.pythonanywhere.com)
and sends students the new timetable whenever their class's schedule changes.

## What it does

- `/start` asks which class the user is in (the class list is read live from the site).
- After a class is picked, it sends the week's timetable and shows a menu:
  **📅 Сегодня · ➡️ Завтра · 🗓 Неделя · 🔁 Сменить класс**.
- Every few minutes it checks each class that someone has selected. When a class's
  timetable changes, its subscribers get a list of what changed
  (e.g. `🔄 Пн, 3 урок: Алгебра → Геометрия`) followed by the new week.
- `/stop` turns notifications off; `/start` turns them back on.

The bot replies in Russian, the same language as the site.

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
| `TIMEZONE`      | `Asia/Almaty`                                  | Used for "today" and "tomorrow"      |
| `SITE_URL`      | `https://edvilletimetable.pythonanywhere.com`  | Timetable site                       |

## How it works

```
bot/
  edville.py    "API" for the timetable site: downloads pages, parses them into dataclasses
  storage.py    SQLite: which chat follows which timetable, and the last known version of each
  render.py     Telegram message formatting and the lesson-by-lesson diff
  watcher.py    background loop that detects changes and notifies subscribers
  handlers.py   commands, buttons, class picker
  keyboards.py  inline and reply keyboards
  __main__.py   wiring and startup
```

**No JSON API on the site.** The site serves only HTML, so `bot/edville.py`
reads the public pages (`/`, `/class/<slug>/`, `/teacher/<slug>/`). Everything
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

## Adding teachers later

Most of the groundwork is already done:

- Teacher pages use the same markup as class pages, and the parser already handles
  them (`meta` holds the class, e.g. "класс 7А"). `EdvilleClient.list_teachers()`
  returns all teachers.
- Subscriptions store `kind` (`class`/`teacher`) plus `slug`, the watcher handles
  both kinds, and messages use the teacher's name as the heading.
- The `Pick` button callback already includes the kind.

What's left: a "Я учитель" entry point in `handlers.py` and a teacher picker. With
about 58 names, it should search by surname or page through the list instead of
showing one big keyboard. The "Сменить класс" label should also become neutral.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests run offline against saved copies of the site's pages in `tests/fixtures/`.
They cover the parser, formatting and diffs, the change watcher (settling, outages,
muted and blocked users), and the bot's handlers driven through aiogram's dispatcher.
