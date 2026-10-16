"""Sending messages nobody asked for: change notifications and scheduled posts."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.types import ReplyKeyboardMarkup

from .storage import Storage

log = logging.getLogger(__name__)


async def deliver(
    bot: Bot, db: Storage, chat_id: int, messages: list[str], reply_markup: ReplyKeyboardMarkup | None = None
) -> None:
    """Send `messages` in order. Chats that are gone are forgotten, and one
    failing chat never raises, so the caller can go on to the next one."""
    for text in messages:
        for _ in range(3):
            try:
                await bot.send_message(chat_id, text, reply_markup=reply_markup)
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramMigrateToChat as e:
                log.info("chat %s became supergroup %s", chat_id, e.migrate_to_chat_id)
                db.move_chat(chat_id, e.migrate_to_chat_id)
                chat_id = e.migrate_to_chat_id
            except TelegramForbiddenError as e:
                # Blocked the bot, removed it from the group, or deleted their account.
                log.info("dropping chat %s: %s", chat_id, e)
                db.unsubscribe(chat_id)
                return
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    log.info("dropping chat %s: %s", chat_id, e)
                    db.unsubscribe(chat_id)
                else:
                    log.exception("could not send to chat %s", chat_id)
                return
            except Exception:
                log.exception("could not send to chat %s", chat_id)
                return
        else:
            log.warning("gave up sending to chat %s after repeated retries", chat_id)
            return
