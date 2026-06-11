from datetime import date, datetime

from app import formatter
from app.formatter import JST


def _market():
    return {
        "^DJI": {"name": "NYダウ", "value": 49061, "change_pct": -1.88, "ok": True},
        "^IXIC": {"name": "ナスダック", "value": 25169, "change_pct": -1.99, "ok": True},
        "^GSPC": {"name": "S&P500", "value": 6742, "change_pct": -1.62, "ok": True},
        "USDJPY=X": {"name": "ドル円", "value": 158.2, "change_pct": 0.3, "ok": True},
        "GC=F": {"name": "金先物", "value": 4210, "change_pct": -3.6, "ok": True},
        "CL=F": {"name": "WTI原油", "value": 78.4, "change_pct": 1.2, "ok": True},
        "^TNX": {"name": "米10年債", "value": 4.85, "change_pct": None, "ok": True},
        "^VIX": {"name": "VIX", "value": 22.4, "change_pct": None, "ok": True},
        "NIY=F": {"name": "日経先物(CME)", "value": 38500, "change_pct": -0.8, "ok": True},
    }


def _scored():
    return {
        "headlines": [
            {"score": 5, "summary": "見出しA", "category": "地政学"},
            {"score": 3, "summary": "見出しB", "category": "商品市況"},
        ],
        "portfolio_notes": ["JX金属: 銅安が逆風"],
        "ai_comment": "様子見が妥当。",
    }


def test_emoji_mapping():
    msg = formatter.format_message("morning", _market(), _scored(), [], None,
                                   now=datetime(2026, 6, 12, 8, 30, tzinfo=JST))
    assert "🔴 見出しA" in msg
    assert "🟡 見出しB" in msg
    assert "📊 6/12(金) 朝刊 8:30" in msg


def test_failed_ticker_does_not_crash():
    market = _market()
    market["^DJI"] = {"name": "NYダウ", "value": None, "change_pct": None, "ok": False}
    msg = formatter.format_message("morning", market, _scored(), [], None,
                                   now=datetime(2026, 6, 12, 8, 30, tzinfo=JST))
    assert "NYダウ 取得失敗" in msg


def test_schedule_next_event_fallback():
    nxt = {"date": "2026-06-16", "_date": date(2026, 6, 16), "name": "日銀"}
    msg = formatter.format_message("morning", _market(), _scored(), [], nxt,
                                   now=datetime(2026, 6, 12, 8, 30, tzinfo=JST))
    assert "次回: 6/16 日銀" in msg


def test_todays_event_shown():
    todays = [{"date": "2026-06-16", "_date": date(2026, 6, 16), "name": "日銀会合"}]
    msg = formatter.format_message("noon", _market(), _scored(), todays, None,
                                   now=datetime(2026, 6, 16, 12, 0, tzinfo=JST))
    assert "・日銀会合" in msg
    # 昼刊は東京市場(日経平均行が無いので TOPIX 等)。NYダウ行は出ない。
    assert "NYダウ" not in msg


def test_length_limit_trims_low_score():
    headlines = [{"score": 5, "summary": "x" * 100, "category": "c"} for _ in range(100)]
    scored = {"headlines": headlines, "portfolio_notes": [], "ai_comment": "ok"}
    msg = formatter.format_message("morning", _market(), scored, [], None,
                                   now=datetime(2026, 6, 12, 8, 30, tzinfo=JST))
    assert len(msg) <= formatter.MAX_LEN


def test_noon_uses_tokyo_market():
    market = _market()
    market["^N225"] = {"name": "日経平均", "value": 38900, "change_pct": 0.5, "ok": True}
    msg = formatter.format_message("noon", market, _scored(), [], None,
                                   now=datetime(2026, 6, 12, 12, 0, tzinfo=JST))
    assert "日経平均" in msg
