"""市況データ取得・整形。

Yahoo Finance の chart API を curl_cffi(ブラウザ偽装)で直接叩く。
yfinance/plain requests はデータセンターIPやTLS指紋で 429/ブロックされるため、
impersonate="chrome" で回避する。
取得失敗してもクラッシュせず「取得失敗」として部分配信する設計を守る。
"""
from __future__ import annotations

import logging
import time
import urllib.parse

logger = logging.getLogger(__name__)

# 朝刊・昼刊・夕刊で共通の基本ティッカー
TICKERS: dict[str, str] = {
    "^DJI": "NYダウ",
    "^IXIC": "ナスダック",
    "^GSPC": "S&P500",
    "^N225": "日経平均",
    "NIY=F": "日経先物(CME)",  # 朝刊向け
    "USDJPY=X": "ドル円",
    "GC=F": "金先物",
    "CL=F": "WTI原油",
    "^TNX": "米10年債利回り",
    "^VIX": "VIX",
}

# 昼刊で当日値を優先表示したいティッカー
NOON_PRIORITY = ["^N225", "1306.T", "USDJPY=X"]

MAX_RETRY = 3
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"


def _fetch_one(ticker: str) -> dict | None:
    """1ティッカー分の直近終値・前日比%を取得する。失敗時は None。"""
    from curl_cffi import requests as creq

    sym = urllib.parse.quote(ticker, safe="")
    url = CHART_URL.format(sym=sym)

    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = creq.get(url, impersonate="chrome", timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            result = r.json()["chart"]["result"][0]
            closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
            if len(closes) >= 2:
                last, prev = float(closes[-1]), float(closes[-2])
                change_pct = (last - prev) / prev * 100 if prev else 0.0
                return {"value": last, "change_pct": change_pct, "ok": True}
            if len(closes) == 1:
                return {"value": float(closes[-1]), "change_pct": None, "ok": True}
        except Exception:
            logger.warning("市況取得失敗 %s (attempt %s)", ticker, attempt)
            time.sleep(1.0 * attempt)
    return None


def fetch_market_data(tickers: dict[str, str] | None = None) -> dict[str, dict]:
    """全ティッカーを取得し、{ticker: {name, value, change_pct, ok}} を返す。

    失敗した項目は ok=False で含める(配信は止めない)。
    """
    tickers = tickers or TICKERS
    out: dict[str, dict] = {}
    for ticker, name in tickers.items():
        data = _fetch_one(ticker)
        if data is None:
            out[ticker] = {"name": name, "value": None, "change_pct": None, "ok": False}
        else:
            out[ticker] = {"name": name, **data}
    return out
