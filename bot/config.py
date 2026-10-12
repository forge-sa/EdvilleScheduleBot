from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    site_url: str
    db_path: Path
    poll_interval: int
    timezone: ZoneInfo


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    return Config(
        bot_token=token,
        site_url=os.getenv("SITE_URL", "https://edvilletimetable.pythonanywhere.com"),
        db_path=Path(os.getenv("DB_PATH", "data/bot.sqlite3")),
        poll_interval=int(os.getenv("POLL_INTERVAL", "180")),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Asia/Almaty")),
    )
