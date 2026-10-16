"""Client for edvilletimetable.pythonanywhere.com.

The site has no JSON API, so this module *is* the API: it downloads the public
HTML pages and turns them into plain dataclasses. The rest of the bot talks to
the site only through `EdvilleClient`, so if the site ever gets a real JSON
endpoint, this is the only file that has to change.

Class pages (/class/<slug>/) and teacher pages (/teacher/<slug>/) share the same
markup, so one parser handles both. On class pages a lesson's `meta` is the
teacher's name, on teacher pages it is the class ("класс 7А").
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from datetime import time as dtime
from typing import Literal

import aiohttp
from bs4 import BeautifulSoup, Tag

Kind = Literal["class", "teacher"]

DAY_NAMES = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")

_TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*[–—-]\s*(\d{1,2}:\d{2})")


class EdvilleError(Exception):
    """The timetable site could not be reached or returned something unexpected."""


class NotFound(EdvilleError):
    pass


class ParseError(EdvilleError):
    pass


@dataclass(frozen=True)
class Entity:
    """A class or a teacher that has its own timetable page."""

    kind: Kind
    slug: str
    title: str


@dataclass(frozen=True)
class Lesson:
    num: int
    time: str
    subject: str
    meta: str
    category: str

    @property
    def start(self) -> dtime | None:
        m = re.match(r"(\d{1,2}):(\d{2})", self.time)
        return dtime(int(m[1]), int(m[2])) if m else None

    @property
    def end(self) -> dtime | None:
        m = _TIME_RE.fullmatch(self.time)
        return dtime(*map(int, m[2].split(":"))) if m else None


@dataclass(frozen=True)
class Break:
    name: str
    time: str


@dataclass(frozen=True)
class Day:
    index: int  # 0 = Monday
    items: tuple[Lesson | Break, ...]

    @property
    def name(self) -> str:
        return DAY_NAMES[self.index]

    @property
    def lessons(self) -> list[Lesson]:
        return [item for item in self.items if isinstance(item, Lesson)]


@dataclass(frozen=True)
class Schedule:
    kind: Kind
    slug: str
    title: str
    days: tuple[Day, ...]

    def day(self, index: int) -> Day | None:
        return next((d for d in self.days if d.index == index), None)

    def next_lesson(self, now: datetime) -> tuple[Day, Lesson] | None:
        """The first lesson starting after `now`, looking up to a week ahead.

        Only lessons count; breakfast, brunch, lunch and snack are skipped. A
        lesson already in progress is not "next".
        """
        for step in range(8):  # step 7 is the same weekday next week
            day = self.day((now.weekday() + step) % 7)
            if day is None:
                continue
            for lesson in sorted((l for l in day.lessons if l.start), key=lambda l: l.start):
                if step > 0 or lesson.start > now.time():
                    return day, lesson
        return None

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict()["days"], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "slug": self.slug,
            "title": self.title,
            "days": [
                {
                    "index": day.index,
                    "items": [
                        {"type": "lesson" if isinstance(item, Lesson) else "break", **asdict(item)}
                        for item in day.items
                    ],
                }
                for day in self.days
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Schedule:
        days = []
        for day in data["days"]:
            items: list[Lesson | Break] = []
            for raw in day["items"]:
                fields = {k: v for k, v in raw.items() if k != "type"}
                items.append(Lesson(**fields) if raw["type"] == "lesson" else Break(**fields))
            days.append(Day(day["index"], tuple(items)))
        return cls(data["kind"], data["slug"], data["title"], tuple(days))


# --- parsing ---------------------------------------------------------------


def _text(el: Tag) -> str:
    return " ".join(el.get_text(" ").split())


def _norm_time(text: str) -> str:
    m = _TIME_RE.search(text)
    return f"{m.group(1)}–{m.group(2)}" if m else ""


def parse_entities(html: str, kind: Kind) -> list[Entity]:
    """Read the class or teacher dropdown on the home page."""
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", id=f"select-{kind}")
    if select is None:
        raise ParseError(f"no #select-{kind} on the home page")
    entities = []
    for option in select.find_all("option"):
        m = re.fullmatch(rf"/{kind}/([^/]+)/", option.get("value", ""))
        if m:
            entities.append(Entity(kind, m.group(1), _text(option)))
    return entities


def parse_schedule(html: str, kind: Kind, slug: str) -> Schedule:
    """Parse a class or teacher page.

    Uses the mobile "day view" block: it lists each day's lessons and breaks in
    order, which is simpler to read than the desktop table.
    """
    soup = BeautifulSoup(html, "html.parser")
    view = soup.select_one("div.day-view")
    if view is None:
        raise ParseError(f"no day view on /{kind}/{slug}/")
    heading = soup.select_one(".page-head h1")
    title = _text(heading) if heading else slug

    days = []
    for panel in view.select("div.day-panel-wrap[data-panel-for]"):
        index = int(panel["data-panel-for"])
        if not 0 <= index < len(DAY_NAMES):
            continue
        items: list[Lesson | Break] = []
        for el in panel.select("div.day-break-banner, div.day-lesson-card"):
            if "day-break-banner" in el["class"]:
                text = _text(el)
                name, sep, _ = text.rpartition(" · ")
                items.append(Break(name if sep else text, _norm_time(text)))
            else:
                items.extend(_parse_card(el))
        days.append(Day(index, tuple(items)))

    if not days:
        raise ParseError(f"no days on /{kind}/{slug}/")
    return Schedule(kind, slug, title, tuple(days))


def _parse_card(card: Tag) -> list[Lesson]:
    num_el = card.select_one(".day-lesson-num")
    num_text = _text(num_el) if num_el else ""
    m = re.match(r"\d+", num_text)
    num = int(m.group()) if m else 0
    lesson_time = _norm_time(num_text)

    metas = card.select(".lesson-meta")
    lessons = []
    for i, chip in enumerate(card.select(".lesson-chip")):
        category = next((c[4:] for c in chip.get("class", []) if c.startswith("cat-")), "other")
        meta = _text(metas[i]) if i < len(metas) else ""
        lessons.append(Lesson(num, lesson_time, _text(chip), meta, category))
    return lessons


# --- HTTP client -------------------------------------------------------------


class EdvilleClient:
    LIST_TTL = 600  # the list of classes/teachers rarely changes

    def __init__(self, base_url: str, timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._lists: dict[Kind, tuple[float, list[Entity]]] = {}
        self._schedules: dict[tuple[Kind, str], tuple[float, Schedule]] = {}

    async def __aenter__(self) -> EdvilleClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, headers={"User-Agent": "EdvilleScheduleBot/1.0"}
            )
        try:
            async with self._session.get(self.base_url + path) as resp:
                if resp.status == 404:
                    raise NotFound(path)
                resp.raise_for_status()
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise EdvilleError(f"GET {path} failed: {e!r}") from e

    async def list_entities(self, kind: Kind) -> list[Entity]:
        cached = self._lists.get(kind)
        if cached and time.monotonic() - cached[0] < self.LIST_TTL:
            return cached[1]
        html = await self._get("/")
        for k in ("class", "teacher"):
            self._lists[k] = (time.monotonic(), parse_entities(html, k))
        return self._lists[kind][1]

    async def list_classes(self) -> list[Entity]:
        return await self.list_entities("class")

    async def list_teachers(self) -> list[Entity]:
        return await self.list_entities("teacher")

    async def get_schedule(self, kind: Kind, slug: str, max_age: float = 60) -> Schedule:
        """Fetch a timetable. `max_age=0` forces a fresh download."""
        key = (kind, slug)
        cached = self._schedules.get(key)
        if cached and time.monotonic() - cached[0] < max_age:
            return cached[1]
        schedule = parse_schedule(await self._get(f"/{kind}/{slug}/"), kind, slug)
        self._schedules[key] = (time.monotonic(), schedule)
        return schedule
