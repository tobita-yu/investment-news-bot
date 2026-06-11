"""economic_calendar.json の読み込みと当日/直近イベント抽出。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "economic_calendar.json"


def load_calendar(path: Path | None = None) -> list[dict]:
    path = path or CALENDAR_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("economic_calendar.json が見つかりません: %s", path)
        return []
    except json.JSONDecodeError:
        logger.exception("economic_calendar.json のパースに失敗")
        return []
    for ev in data:
        ev["_date"] = datetime.strptime(ev["date"], "%Y-%m-%d").date()
    return sorted(data, key=lambda e: e["_date"])


def todays_events(today: date | None = None, calendar: list[dict] | None = None) -> list[dict]:
    """当日のイベントを返す。"""
    today = today or date.today()
    cal = calendar if calendar is not None else load_calendar()
    return [e for e in cal if e["_date"] == today]


def next_event(today: date | None = None, calendar: list[dict] | None = None) -> dict | None:
    """当日より後の直近イベントを1件返す。なければ None。"""
    today = today or date.today()
    cal = calendar if calendar is not None else load_calendar()
    upcoming = [e for e in cal if e["_date"] > today]
    return upcoming[0] if upcoming else None


def upcoming_events(
    today: date | None = None, days: int = 1, calendar: list[dict] | None = None
) -> list[dict]:
    """当日から days 日先までのイベントを返す(当日含む)。"""
    today = today or date.today()
    end = today + timedelta(days=days)
    cal = calendar if calendar is not None else load_calendar()
    return [e for e in cal if today <= e["_date"] <= end]
