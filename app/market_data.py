"""yfinance による市況データ取得・整形。

取得失敗してもクラッシュせず「取得失敗」として部分配信する設計を守る。
"""
from __future__ import annotations

import logging
import time

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


def _fetch_one(ticker: str) -> dict | None:
    """1ティッカー分の直近終値・前日比%を取得する。失敗時は None。"""
    import yfinance as yf

    for attempt in range(1, MAX_RETRY + 1):
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                change_pct = (last - prev) / prev * 100 if prev else 0.0
                return {"value": last, "change_pct": change_pct, "ok": True}
            if len(closes) == 1:
                return {"value": float(closes.iloc[-1]), "change_pct": None, "ok": True}
        except Exception:
            logger.warning("yfinance 取得失敗 %s (attempt %s)", ticker, attempt)
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
