"""RSS 取得・重複排除。feedparser を使う。

保有銘柄ごとのフィードは DB から動的生成する。
news_cache と連携して二重配信を防ぐ。
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# これより古い記事は鮮度低下とみなして除外する(週末・祝日を跨ぐため広めの4日)
MAX_AGE_DAYS = 4

# 固定フィード (ラベル, URL)
# --- 一次情報(中央銀行・公的統計の公式発表。プロが起点に見る発表元) ---
PRIMARY_FEEDS: list[tuple[str, str]] = [
    ("日銀公式", "https://www.boj.or.jp/rss/whatsnew.xml"),
    ("FRB公式", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("ECB公式", "https://www.ecb.europa.eu/rss/press.html"),
    ("BEA(米統計)", "https://apps.bea.gov/rss/rss.xml"),
    ("後藤達也", "https://note.com/goto_finance/rss"),
    ("日経マーケット", "https://assets.wor.jp/rss/rdf/nikkei/markets.rdf"),
]

RSS_FEEDS: list[tuple[str, str]] = PRIMARY_FEEDS + [
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


# 適時開示(TDnet)は決算など材料性が高く数日経っても重要なので長めの窓
TDNET_MAX_AGE_DAYS = 14


def fetch_tdnet(holdings: list[dict], max_age_days: int = TDNET_MAX_AGE_DAYS) -> list[dict]:
    """保有銘柄の適時開示(TDnet)を yanoshin API から取得する。一次情報。

    決算短信・自己株式・配当・業績修正など公式開示を銘柄ごとに取得する。
    """
    import httpx

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[dict] = []
    with httpx.Client(timeout=20) as client:
        for h in holdings:
            code = h.get("code", "")
            if not code:
                continue
            try:
                r = client.get(
                    f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json",
                    params={"limit": 8},
                )
                items = r.json().get("items", [])
            except Exception:
                logger.warning("TDnet 取得失敗 %s", code)
                continue
            for it in items:
                t = it.get("Tdnet", {})
                title = (t.get("title") or "").strip()
                if not title:
                    continue
                published = _parse_tdnet_date(t.get("pubdate"))
                if published is not None and published < cutoff:
                    continue
                out.append(
                    {
                        "label": f"適時開示:{h['name']}",
                        "title": f"【適時開示】{h['name']}: {title}",
                        "summary": "",
                        "link": t.get("document_url", "") or t.get("url", ""),
                        "published": published,
                    }
                )
    return out


def _parse_tdnet_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            # TDnet は JST。UTC に寄せて比較に使う
            from datetime import timezone as _tz, timedelta as _td

            dt = datetime.strptime(s[: len(fmt) + 2], fmt)
            return dt.replace(tzinfo=_tz(_td(hours=9))).astimezone(timezone.utc)
        except Exception:
            continue
    return None


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


def fetch_news(holdings: list[dict] | None = None, max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    """全フィードを取得し、記事 dict のリストを返す。

    返す各記事: {"label", "title", "summary", "link", "published"}
    - タイトル正規化による feed 内重複排除(DB 重複排除は db.filter_unseen)
    - published が max_age_days より古い記事は除外し、鮮度を担保する
      (Google News 検索フィードは古い記事も返すため。日付不明の記事は残す)
    """
    import feedparser

    holdings = holdings or []
    feeds = RSS_FEEDS + _holdings_feeds(holdings)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

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
            published = _parse_published(entry)
            # 古い記事は除外(日付不明は鮮度判定できないので残す)
            if published is not None and published < cutoff:
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
                    "published": published,
                }
            )

    # 適時開示(TDnet)を追記。一次情報なので重複排除のうえ末尾に足す。
    for item in fetch_tdnet(holdings):
        norm = _normalize(item["title"])
        if norm in seen_titles:
            continue
        seen_titles.add(norm)
        out.append(item)
    return out


def _normalize(title: str) -> str:
    import re

    return re.sub(r"[\s　\W_]+", "", title).lower()
