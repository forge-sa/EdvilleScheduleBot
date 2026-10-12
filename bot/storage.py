"""SQLite storage: who follows which timetable, and the last known version of
each timetable (so changes can be detected and diffed across restarts)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .edville import Entity, Kind, Schedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id    INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL,          -- 'class' or 'teacher'
    slug       TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    notify     INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
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


@dataclass(frozen=True)
class Subscription:
    chat_id: int
    kind: Kind
    slug: str
    title: str
    notify: bool


class Storage:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- subscriptions ---

    def get_subscription(self, chat_id: int) -> Subscription | None:
        row = self._db.execute(
            "SELECT chat_id, kind, slug, title, notify FROM subscriptions WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return Subscription(row[0], row[1], row[2], row[3], bool(row[4])) if row else None

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

    def unsubscribe(self, chat_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))

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
