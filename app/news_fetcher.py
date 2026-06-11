"""RSS 取得・重複排除。feedparser を使う。

保有銘柄ごとのフィードは DB から動的生成する。
news_cache と連携して二重配信を防ぐ。
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 固定フィード (ラベル, URL)
RSS_FEEDS: list[tuple[str, str]] = [
    ("NHK経済", "https://www.nhk.or.jp/rss/news/cat5.xml"),
    (
        "日銀",
        "https://news.google.com/rss/search?q=日銀+金融政策&hl=ja&gl=JP&ceid=JP:ja",
    ),
    (
        "FRB",
        "https://news.google.com/rss/search?q=FRB+OR+FOMC&hl=ja&gl=JP&ceid=JP:ja",
    ),
    (
        "米経済指標",
        "https://news.google.com/rss/search?q=米国+CPI+OR+雇用統計&hl=ja&gl=JP&ceid=JP:ja",
    ),
    (
        "地政学",
        "https://news.google.com/rss/search?q=イラン+OR+ホルムズ海峡+OR+中東+原油&hl=ja&gl=JP&ceid=JP:ja",
    ),
    (
        "金相場",
        "https://news.google.com/rss/search?q=金価格+OR+NY金&hl=ja&gl=JP&ceid=JP:ja",
    ),
    (
        "Reuters World",
        "https://news.google.com/rss/search?q=Federal+Reserve+OR+inflation+when:1d&hl=en-US&gl=US&ceid=US:en",
    ),
]

MAX_ITEMS_PER_FEED = 20


def _holdings_feeds(holdings: list[dict]) -> list[tuple[str, str]]:
    """保有銘柄から Google News フィードを動的生成する。"""
    feeds: list[tuple[str, str]] = []
    for h in holdings:
        terms = [h["name"]] + list(h.get("keywords") or [])
        query = " OR ".join(terms)
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(query)
            + "&hl=ja&gl=JP&ceid=JP:ja"
        )
        feeds.append((f"保有:{h['name']}", url))
    return feeds


def _parse_published(entry) -> datetime | None:
    t = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if t is None:
        return None
    try:
        return datetime(*t[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def fetch_news(holdings: list[dict] | None = None) -> list[dict]:
    """全フィードを取得し、記事 dict のリストを返す。

    返す各記事: {"label", "title", "summary", "link", "published"}
    タイトル正規化による feed 内重複排除のみここで行う(DB 重複排除は db.filter_unseen)。
    """
    import feedparser

    holdings = holdings or []
    feeds = RSS_FEEDS + _holdings_feeds(holdings)

    seen_titles: set[str] = set()
    out: list[dict] = []

    for label, url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception:
            logger.warning("RSS 取得失敗 %s", label)
            continue

        for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
            title = (getattr(entry, "title", "") or "").strip()
            if not title:
                continue
            norm = _normalize(title)
            if norm in seen_titles:
                continue
            seen_titles.add(norm)
            out.append(
                {
                    "label": label,
                    "title": title,
                    "summary": (getattr(entry, "summary", "") or "").strip()[:300],
                    "link": getattr(entry, "link", ""),
                    "published": _parse_published(entry),
                }
            )
    return out


def _normalize(title: str) -> str:
    import re

    return re.sub(r"[\s　\W_]+", "", title).lower()
