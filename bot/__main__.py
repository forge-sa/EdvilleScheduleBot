"""Entry point: `python -m bot`."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import handlers
from .config import load_config
from .edville import EdvilleClient
from .storage import Storage
from .watcher import ScheduleWatcher

COMMANDS = [
    BotCommand(command="today", description="Расписание на сегодня"),
    BotCommand(command="tomorrow", description="Расписание на завтра"),
    BotCommand(command="week", description="Расписание на неделю"),
    BotCommand(command="class", description="Сменить класс"),
    BotCommand(command="stop", description="Не присылать изменения"),
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
        watcher = None
        try:
            await bot.set_my_commands(COMMANDS)
            watcher = asyncio.create_task(ScheduleWatcher(bot, edville, db, config.poll_interval).run())
            await dp.start_polling(bot)
        finally:
            if watcher:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            await bot.session.close()
            db.close()


if __name__ == "__main__":
    asyncio.run(main())
