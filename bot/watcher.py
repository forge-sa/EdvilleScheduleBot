"""Background task that polls followed timetables and notifies on changes."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from .edville import EdvilleClient, EdvilleError, Kind, Schedule
from .keyboards import main_menu
from .render import pack, render_changes, render_week
from .storage import Storage

log = logging.getLogger(__name__)


class ScheduleWatcher:
    def __init__(self, bot: Bot, edville: EdvilleClient, db: Storage, interval: int):
        self.bot = bot
        self.edville = edville
        self.db = db
        self.interval = interval
        # A new version is only announced once it has been seen on two polls in
        # a row. The timetable admin edits cells one by one, and without this a
        # half-finished edit would send students several notifications.
        self._pending: dict[tuple[Kind, str], str] = {}

    async def run(self) -> None:
        while True:
            try:
                await self.check_all()
            except Exception:
                log.exception("timetable check failed")
            await asyncio.sleep(self.interval)

    async def check_all(self) -> None:
        for kind, slug in self.db.watched():
            await self.check(kind, slug)

    async def check(self, kind: Kind, slug: str) -> None:
        try:
            new = await self.edville.get_schedule(kind, slug, max_age=0)
        except EdvilleError:
            log.warning("could not fetch /%s/%s/", kind, slug, exc_info=True)
            return

        key = (kind, slug)
        old = self.db.get_snapshot(kind, slug)
        if old is None:
            self.db.save_snapshot(new)
            return
        fingerprint = new.fingerprint()
        if fingerprint == old.fingerprint():
            self._pending.pop(key, None)
            return
        if self._pending.get(key) != fingerprint:
            self._pending[key] = fingerprint
            log.info("/%s/%s/ changed, waiting for it to settle", kind, slug)
            return

        del self._pending[key]
        self.db.save_snapshot(new)
        log.info("/%s/%s/ changed, notifying subscribers", kind, slug)
        await self.notify(old, new)

    async def notify(self, old: Schedule, new: Schedule) -> None:
        messages = pack([render_changes(old, new), *render_week(new)])
        for chat_id in self.db.subscribers(new.kind, new.slug):
            await self._send(chat_id, messages)
            await asyncio.sleep(0.05)  # stay well under Telegram's ~30 messages/second

    async def _send(self, chat_id: int, messages: list[str]) -> None:
        for text in messages:
            for _ in range(3):
                try:
                    await self.bot.send_message(chat_id, text, reply_markup=main_menu())
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                except TelegramForbiddenError as e:
                    # Blocked the bot, left the group, or deleted their account.
                    log.info("dropping chat %s: %s", chat_id, e)
                    self.db.unsubscribe(chat_id)
                    return
                except TelegramBadRequest as e:
                    if "chat not found" in str(e).lower():
                        log.info("dropping chat %s: %s", chat_id, e)
                        self.db.unsubscribe(chat_id)
                    else:
                        log.exception("could not notify chat %s", chat_id)
                    return
                except Exception:
                    # One failing chat must not stop the others from being notified.
                    log.exception("could not notify chat %s", chat_id)
                    return
            else:
                log.warning("gave up notifying chat %s after repeated rate limits", chat_id)
                return
