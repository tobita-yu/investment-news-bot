"""economic_calendar.json の読み込みと当日/直近イベント抽出。

日銀・FOMC など日程が固定でないものは JSON に手動登録する(確定値が優先)。
米雇用統計(毎月第1金曜)・米CPI(月中旬)はルールが明確なのでコード側で自動生成し、
毎月の手動メンテを不要にする。JSON に同月の同種イベントがあればそちらを優先(重複しない)。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "economic_calendar.json"

# 自動生成する先の月数(今月から何ヶ月先まで)
GEN_MONTHS_AHEAD = 7


def _load_json_events(path: Path | None = None) -> list[dict]:
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
    return data


# ---------------------------------------------------------------------------
# 自動生成(米雇用統計・米CPI)
# ---------------------------------------------------------------------------
def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0 .. Fri=4 .. Sun=6
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _second_wednesday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_wed = d + timedelta(days=(2 - d.weekday()) % 7)
    return first_wed + timedelta(days=7)


def _prev_month_label(month: int) -> int:
    return 12 if month == 1 else month - 1


def _iter_months(today: date, ahead: int):
    for i in range(ahead + 1):
        idx = (today.month - 1) + i
        yield today.year + idx // 12, idx % 12 + 1


def generated_events(today: date | None = None, ahead: int = GEN_MONTHS_AHEAD) -> list[dict]:
    """米雇用統計(第1金曜)と米CPI(月中旬・暫定)を今月から ahead ヶ月分生成する。"""
    today = today or date.today()
    out: list[dict] = []
    for y, m in _iter_months(today, ahead):
        prev = _prev_month_label(m)
        jobs = _first_friday(y, m)
        out.append({
            "date": jobs.isoformat(), "_date": jobs, "importance": 4,
            "name": f"米雇用統計({prev}月分)", "_type": "jobs", "_gen": True,
        })
        cpi = _second_wednesday(y, m)
        out.append({
            "date": cpi.isoformat(), "_date": cpi, "importance": 4,
            "name": f"米CPI({prev}月分)※暫定", "_type": "cpi", "_gen": True,
        })
    return out


def _event_type(name: str) -> str | None:
    if "雇用統計" in name:
        return "jobs"
    if "CPI" in name:
        return "cpi"
    return None


def _merge_generated(json_events: list[dict], today: date) -> list[dict]:
    # JSON に既にある (年, 月, 種別) は自動生成をスキップ(確定値を優先)
    present = set()
    for e in json_events:
        t = _event_type(e["name"])
        if t:
            present.add((e["_date"].year, e["_date"].month, t))
    merged = list(json_events)
    for g in generated_events(today):
        key = (g["_date"].year, g["_date"].month, g["_type"])
        if key not in present:
            merged.append(g)
    return merged


def load_calendar(path: Path | None = None, today: date | None = None) -> list[dict]:
    """JSON イベント + 自動生成イベントを統合して日付順で返す。"""
    today = today or date.today()
    events = _merge_generated(_load_json_events(path), today)
    return sorted(events, key=lambda e: e["_date"])


def todays_events(today: date | None = None, calendar: list[dict] | None = None) -> list[dict]:
    """当日のイベントを返す。"""
    today = today or date.today()
    cal = calendar if calendar is not None else load_calendar(today=today)
    return [e for e in cal if e["_date"] == today]


def next_event(today: date | None = None, calendar: list[dict] | None = None) -> dict | None:
    """当日より後の直近イベントを1件返す。なければ None。"""
    today = today or date.today()
    cal = calendar if calendar is not None else load_calendar(today=today)
    upcoming = [e for e in cal if e["_date"] > today]
    return upcoming[0] if upcoming else None


def upcoming_events(
    today: date | None = None, days: int = 1, calendar: list[dict] | None = None
) -> list[dict]:
    """当日から days 日先までのイベントを返す(当日含む)。"""
    today = today or date.today()
    end = today + timedelta(days=days)
    cal = calendar if calendar is not None else load_calendar(today=today)
    return [e for e in cal if today <= e["_date"] <= end]
