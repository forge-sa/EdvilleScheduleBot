"""SQLite storage: who follows which timetable, and the last known version of
each timetable (so changes can be detected and diffed across restarts)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from .edville import Entity, Kind, Schedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id     INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL,          -- 'class' or 'teacher'
    slug        TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    notify      INTEGER NOT NULL DEFAULT 1,        -- send the new timetable when it changes
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    daily       INTEGER NOT NULL DEFAULT 0,        -- post the day's timetable at daily_time
    daily_time  TEXT    NOT NULL DEFAULT '07:30',
    next_lesson INTEGER NOT NULL DEFAULT 0         -- post the next lesson when each lesson ends
);
CREATE INDEX IF NOT EXISTS subscriptions_entity ON subscriptions (kind, slug);

CREATE TABLE IF NOT EXISTS snapshots (
    kind       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    data       TEXT NOT NULL,             -- Schedule.to_dict() as JSON
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, slug)
);
"""

# Columns added after the first release, for databases created before them.
ADDED_COLUMNS = {
    "daily": "INTEGER NOT NULL DEFAULT 0",
    "daily_time": "TEXT NOT NULL DEFAULT '07:30'",
    "next_lesson": "INTEGER NOT NULL DEFAULT 0",
}
_SUBSCRIPTION = "chat_id, kind, slug, title, notify, daily, daily_time, next_lesson"


@dataclass(frozen=True)
class Subscription:
    chat_id: int
    kind: Kind
    slug: str
    title: str
    notify: bool
    daily: bool = False
    daily_time: time = time(7, 30)
    next_lesson: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Subscription:
        return cls(
            chat_id=row["chat_id"],
            kind=row["kind"],
            slug=row["slug"],
            title=row["title"],
            notify=bool(row["notify"]),
            daily=bool(row["daily"]),
            daily_time=time.fromisoformat(row["daily_time"]),
            next_lesson=bool(row["next_lesson"]),
        )


class Storage:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        existing = {row["name"] for row in self._db.execute("PRAGMA table_info(subscriptions)")}
        with self._db:
            for name, definition in ADDED_COLUMNS.items():
                if name not in existing:
                    self._db.execute(f"ALTER TABLE subscriptions ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self._db.close()

    # --- subscriptions ---

    def get_subscription(self, chat_id: int) -> Subscription | None:
        row = self._db.execute(f"SELECT {_SUBSCRIPTION} FROM subscriptions WHERE chat_id = ?", (chat_id,)).fetchone()
        return Subscription.from_row(row) if row else None

    def subscribe(self, chat_id: int, entity: Entity) -> None:
        with self._db:
            self._db.execute(
                """INSERT INTO subscriptions (chat_id, kind, slug, title, notify) VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT (chat_id) DO UPDATE SET
                       kind = excluded.kind, slug = excluded.slug, title = excluded.title,
                       notify = 1, updated_at = CURRENT_TIMESTAMP""",
                (chat_id, entity.kind, entity.slug, entity.title),
            )

    def set_notify(self, chat_id: int, notify: bool) -> None:
        with self._db:
            self._db.execute("UPDATE subscriptions SET notify = ? WHERE chat_id = ?", (int(notify), chat_id))

    def set_daily(self, chat_id: int, enabled: bool, at: time) -> None:
        with self._db:
            self._db.execute(
                "UPDATE subscriptions SET daily = ?, daily_time = ? WHERE chat_id = ?",
                (int(enabled), at.strftime("%H:%M"), chat_id),
            )

    def set_next_lesson(self, chat_id: int, enabled: bool) -> None:
        with self._db:
            self._db.execute("UPDATE subscriptions SET next_lesson = ? WHERE chat_id = ?", (int(enabled), chat_id))

    def unsubscribe(self, chat_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))

    def move_chat(self, old_id: int, new_id: int) -> None:
        """A group became a supergroup, which has a new id. If the new chat already
        picked a timetable, that newer choice wins."""
        with self._db:
            self._db.execute("UPDATE OR IGNORE subscriptions SET chat_id = ? WHERE chat_id = ?", (new_id, old_id))
            self._db.execute("DELETE FROM subscriptions WHERE chat_id = ?", (old_id,))

    def announcing(self) -> list[Subscription]:
        """Chats that get the day's timetable or the next lesson posted on a schedule."""
        rows = self._db.execute(f"SELECT {_SUBSCRIPTION} FROM subscriptions WHERE daily = 1 OR next_lesson = 1")
        return [Subscription.from_row(row) for row in rows]

    def watched(self) -> list[tuple[Kind, str]]:
        """Every timetable someone has selected, muted or not.

        Muted ones are still polled so their snapshot stays current; otherwise
        unmuting would produce a diff against a weeks-old version.
        """
        return [(r[0], r[1]) for r in self._db.execute("SELECT DISTINCT kind, slug FROM subscriptions")]

    def is_watched(self, kind: Kind, slug: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM subscriptions WHERE kind = ? AND slug = ? LIMIT 1", (kind, slug)
        ).fetchone() is not None

    def subscribers(self, kind: Kind, slug: str) -> list[int]:
        rows = self._db.execute(
            "SELECT chat_id FROM subscriptions WHERE kind = ? AND slug = ? AND notify = 1", (kind, slug)
        )
        return [r[0] for r in rows]

    # --- snapshots ---

    def get_snapshot(self, kind: Kind, slug: str) -> Schedule | None:
        row = self._db.execute("SELECT data FROM snapshots WHERE kind = ? AND slug = ?", (kind, slug)).fetchone()
        return Schedule.from_dict(json.loads(row[0])) if row else None

    def save_snapshot(self, schedule: Schedule) -> None:
        with self._db:
            self._db.execute(
                """INSERT INTO snapshots (kind, slug, data) VALUES (?, ?, ?)
                   ON CONFLICT (kind, slug) DO UPDATE SET data = excluded.data, updated_at = CURRENT_TIMESTAMP""",
                (schedule.kind, schedule.slug, json.dumps(schedule.to_dict(), ensure_ascii=False)),
            )
