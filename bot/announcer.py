"""Background task for the posts chats turn on in /settings: the day's timetable
every school day, and the next lesson as soon as each lesson ends."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, tzinfo

from aiogram import Bot

from .delivery import deliver
from .edville import Schedule
from .keyboards import chat_menu
from .render import render_day, render_next
from .storage import Storage, Subscription

log = logging.getLogger(__name__)


def due(sub: Subscription, schedule: Schedule, since: datetime, until: datetime) -> list[str]:
    """The posts for `sub` that fall after `since` and no later than `until`, in order."""
    posts: list[tuple[datetime, str]] = []
    date = since.date()
    while date <= until.date():
        day = schedule.day(date.weekday())
        if day and day.lessons:
            if sub.daily:
                moment = datetime.combine(date, sub.daily_time, since.tzinfo)
                if since < moment <= until:
                    posts.append((moment, render_day(schedule, day)))
            if sub.next_lesson:
                for end in sorted({lesson.end for lesson in day.lessons if lesson.end}):
                    moment = datetime.combine(date, end, since.tzinfo)
                    if since < moment <= until and (found := schedule.next_lesson(moment)):
                        posts.append((moment, render_next(schedule, *found, moment)))
        date += timedelta(days=1)
    return [text for _, text in sorted(posts, key=lambda post: post[0])]


class Announcer:
    def __init__(self, bot: Bot, db: Storage, timezone: tzinfo, interval: int = 20):
        self.bot = bot
        self.db = db
        self.timezone = timezone
        self.interval = interval

    async def run(self) -> None:
        # Each check covers the time since the previous one, so nothing is sent
        # twice. Posts that fell due while the bot was down are skipped.
        since = datetime.now(self.timezone)
        while True:
            await asyncio.sleep(self.interval)
            until = datetime.now(self.timezone)
            try:
                await self.announce(since, until)
            except Exception:
                log.exception("scheduled posts failed")
            since = until

    async def announce(self, since: datetime, until: datetime) -> None:
        for sub in self.db.announcing():
            # The watcher keeps snapshots of every followed timetable up to date,
            # so there is no need to download anything here.
            schedule = self.db.get_snapshot(sub.kind, sub.slug)
            if schedule is None:
                continue
            posts = due(sub, schedule, since, until)
            if posts:
                await deliver(self.bot, self.db, sub.chat_id, posts, chat_menu(sub.chat_id, sub.kind))
