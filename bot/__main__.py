"""Entry point: `python -m bot`."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats

from . import handlers
from .announcer import Announcer
from .config import load_config
from .edville import EdvilleClient
from .storage import Storage
from .watcher import ScheduleWatcher

COMMANDS = [
    BotCommand(command="next", description="Следующий урок"),
    BotCommand(command="today", description="Расписание на сегодня"),
    BotCommand(command="tomorrow", description="Расписание на завтра"),
    BotCommand(command="week", description="Расписание на неделю"),
    BotCommand(command="settings", description="Присылать расписание на день и следующий урок"),
    BotCommand(command="change", description="Сменить класс или учителя"),
    BotCommand(command="stop", description="Не присылать изменения"),
    BotCommand(command="help", description="Помощь"),
]
GROUP_COMMANDS = [
    BotCommand(command="next", description="Следующий урок"),
    BotCommand(command="today", description="Расписание на сегодня"),
    BotCommand(command="tomorrow", description="Расписание на завтра"),
    BotCommand(command="week", description="Расписание на неделю"),
    BotCommand(command="settings", description="Что присылать в чат"),
    BotCommand(command="change", description="Сменить класс"),
    BotCommand(command="help", description="Помощь"),
]


async def main() -> None:
    config = load_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Storage(config.db_path)
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async with EdvilleClient(config.site_url) as edville:
        dp = Dispatcher(edville=edville, db=db, config=config)
        dp.include_router(handlers.router)
        tasks: list[asyncio.Task] = []
        try:
            await bot.set_my_commands(COMMANDS)
            await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
            tasks.append(asyncio.create_task(ScheduleWatcher(bot, edville, db, config.poll_interval).run()))
            tasks.append(asyncio.create_task(Announcer(bot, db, config.timezone).run()))
            await dp.start_polling(bot)
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await bot.session.close()
            db.close()


if __name__ == "__main__":
    asyncio.run(main())
