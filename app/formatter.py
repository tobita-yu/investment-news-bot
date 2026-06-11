"""LINE メッセージ整形。仕様5章のフォーマット、4,500字制限、絵文字マッピング。

`python -m app.formatter --dry-run` でサンプル出力を確認できる。
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
MAX_LEN = 4500

# 配信回ごとのメタ情報
EDITIONS = {
    "morning": {"label": "朝刊", "time": "8:30"},
    "noon": {"label": "昼刊", "time": "12:00"},
    "evening": {"label": "夕刊", "time": "20:00"},
}

# スコア → 絵文字 (仕様にあるもののみ使用)
SCORE_EMOJI = {5: "🔴", 4: "🟠", 3: "🟡"}

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 朝刊(NY市場中心)で表示する市況行。各行は ticker キーのリスト。
MARKET_ROWS_NY = [
    ["^DJI"],
    ["^IXIC"],
    ["^GSPC"],
    ["USDJPY=X", "GC=F"],
    ["CL=F", "^TNX"],
    ["^VIX", "NIY=F"],
]
# 昼刊・夕刊(東京市場中心)
MARKET_ROWS_TOKYO = [
    ["^N225"],
    ["1306.T"],
    ["USDJPY=X", "GC=F"],
    ["CL=F", "^TNX"],
    ["^VIX"],
]

# 値の表示方法: (整数カンマ / 小数1桁 / 利回り%表示)
_INT_TICKERS = {"^DJI", "^IXIC", "^GSPC", "^N225", "NIY=F", "GC=F", "1306.T"}
_RATE_TICKERS = {"^TNX"}  # 値を % 表示、変化率は出さない
_NOCHANGE_TICKERS = {"^VIX", "^TNX"}  # 変化率を出さない


def _fmt_value(ticker: str, value: float) -> str:
    if ticker in _INT_TICKERS:
        return f"{value:,.0f}"
    if ticker in _RATE_TICKERS:
        return f"{value:.2f}%"
    return f"{value:.1f}"


def _fmt_change(pct: float | None) -> str:
    # 色付き記号で騰落を一目で分かるようにする(緑=上昇 / 赤=下落)
    if pct is None:
        return ""
    if pct > 0:
        return f" 🟢+{pct:.2f}%"
    if pct < 0:
        return f" 🔴{pct:.2f}%"  # pct は負なので符号付きで表示
    return " ⚪0.00%"


def _fmt_ticker(ticker: str, market: dict[str, dict]) -> str | None:
    m = market.get(ticker)
    if m is None:
        return None
    if not m.get("ok") or m.get("value") is None:
        return f"{m['name']} 取得失敗"
    value = _fmt_value(ticker, m["value"])
    change = "" if ticker in _NOCHANGE_TICKERS else _fmt_change(m.get("change_pct"))
    return f"{m['name']} {value}{change}"


def _market_section(edition: str, market: dict[str, dict]) -> str:
    rows = MARKET_ROWS_NY if edition == "morning" else MARKET_ROWS_TOKYO
    lines: list[str] = []
    for row in rows:
        parts = [p for t in row if (p := _fmt_ticker(t, market)) is not None]
        if parts:
            lines.append(" / ".join(parts))
    return "\n".join(lines) if lines else "(市況データ取得失敗)"


def _schedule_section(todays: list[dict], next_ev: dict | None) -> str:
    if todays:
        return "\n".join(f"・{e['name']}" for e in todays)
    if next_ev:
        d = next_ev["_date"] if "_date" in next_ev else None
        when = f"{d.month}/{d.day}" if isinstance(d, date) else next_ev.get("date", "")
        return f"・特になし(次回: {when} {next_ev['name']})"
    return "・特になし"


def _headline_lines(headlines: list[dict]) -> list[str]:
    lines = []
    for h in headlines:
        emoji = SCORE_EMOJI.get(h.get("score", 3), "🟡")
        lines.append(f"{emoji} {h.get('summary', '').strip()}")
    return lines


def _assemble(header: str, sections: list[tuple[str, str]]) -> str:
    blocks = [header]
    for title, body in sections:
        blocks.append(f"【{title}】\n{body}")
    return "\n\n".join(blocks)


def format_message(
    edition: str,
    market: dict[str, dict],
    scored: dict,
    todays_events: list[dict],
    next_event: dict | None,
    now: datetime | None = None,
) -> str:
    """配信用メッセージ文字列を組み立てる。4,500字を超える場合は低スコアから削る。"""
    now = now or datetime.now(JST)
    meta = EDITIONS.get(edition, {"label": edition, "time": ""})
    wd = _WEEKDAYS_JA[now.weekday()]
    header = f"📊 {now.month}/{now.day}({wd}) {meta['label']} {meta['time']}"

    market_body = _market_section(edition, market)
    schedule_body = _schedule_section(todays_events, next_event)
    portfolio = scored.get("portfolio_notes") or []
    portfolio_body = "\n".join(f"・{p}" for p in portfolio) if portfolio else "・該当なし"
    ai_comment = (scored.get("ai_comment") or "").strip()

    headlines = list(scored.get("headlines") or [])

    def build(hls: list[dict]) -> str:
        head_body = "\n".join(_headline_lines(hls)) if hls else "・重要ニュースなし"
        sections = [
            ("市況", market_body),
            ("今日の予定", schedule_body),
            ("ヘッドライン", head_body),
            ("保有銘柄", portfolio_body),
        ]
        if ai_comment:
            sections.append(("AI一言", ai_comment))
        return _assemble(header, sections)

    msg = build(headlines)
    # 4,500字制限: 低スコア(末尾)から削る
    while len(msg) > MAX_LEN and headlines:
        headlines.pop()  # headlines は score 降順前提
        msg = build(headlines)
    return msg


# ---------------------------------------------------------------------------
# dry-run 用サンプル
# ---------------------------------------------------------------------------
def _sample() -> str:
    market = {
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
    scored = {
        "headlines": [
            {"score": 5, "summary": "トランプ氏、イラン発電所攻撃を示唆", "category": "地政学"},
            {"score": 5, "summary": "米5月CPI +4.2%、3年ぶり高水準", "category": "経済指標"},
            {"score": 4, "summary": "日銀6月利上げ観測9割に", "category": "金融政策"},
            {"score": 3, "summary": "銅価格続落、3カ月安値", "category": "商品市況"},
        ],
        "portfolio_notes": [
            "JX金属: 銅安が逆風、本日決算なし",
            "めぶきFG: 地銀株高続く",
        ],
        "ai_comment": "金利上昇×地政学で金は売り優勢。日銀通過まで様子見が妥当。",
    }
    todays: list[dict] = []
    next_ev = {"date": "2026-06-16", "_date": date(2026, 6, 16), "name": "日銀"}
    return format_message("morning", market, scored, todays, next_ev, now=datetime(2026, 6, 12, 8, 30, tzinfo=JST))


if __name__ == "__main__":
    import sys

    if "--dry-run" in sys.argv:
        print(_sample())
    else:
        print("usage: python -m app.formatter --dry-run")
