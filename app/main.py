"""FastAPI アプリ。配信トリガー /deliver/{edition} と LINE Webhook /webhook。"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from app import calendar_loader, db, formatter, line_client, market_data, news_fetcher, scorer
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="investment-news-bot")

VALID_EDITIONS = {"morning", "noon", "evening"}


def run_delivery(edition: str, push: bool = True) -> str:
    """配信フロー全体を実行し、組み立てたメッセージ本文を返す。

    push=True なら LINE 送信と delivery_logs 記録も行う。
    """
    # 1. 市況データ。昼刊・夕刊は TOPIX 代用(1306.T)も取得。
    tickers = dict(market_data.TICKERS)
    if edition in ("noon", "evening"):
        tickers["1306.T"] = "TOPIX(1306)"
    market = market_data.fetch_market_data(tickers)

    # 2. ニュース取得(保有銘柄フィード込み) + DB 重複排除
    holdings = db.list_holdings()
    articles = news_fetcher.fetch_news(holdings)
    fresh = db.filter_unseen(articles)

    # 3. カレンダー照合
    todays = calendar_loader.todays_events()
    nxt = calendar_loader.next_event()
    upcoming = calendar_loader.upcoming_events(days=1)

    # 4. Gemini スコアリング(一括1回)
    scored = scorer.score_articles(fresh, market, holdings, upcoming)

    # 5. 整形
    message = formatter.format_message(edition, market, scored, todays, nxt)

    if not push:
        return message

    # 6. LINE push
    status, error = "success", None
    try:
        line_client.push(message)
    except Exception as e:  # noqa: BLE001
        status, error = "failed", str(e)
        logger.exception("配信失敗 edition=%s", edition)

    # 配信済みニュースを記録(成功時のみ二重配信防止に意味がある)
    if status == "success":
        db.mark_delivered(fresh)
    db.prune_old_cache(days=7)

    # 7. delivery_logs 記録
    db.log_delivery(
        edition=edition,
        message_length=len(message),
        headline_count=len(scored.get("headlines") or []),
        status=status,
        error=error,
    )

    # 失敗時は自分宛てにエラー通知(可能なら)
    if status == "failed":
        try:
            line_client.push(f"⚠️ 配信失敗({edition}): {error}")
        except Exception:
            logger.exception("エラー通知も失敗")

    return message


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/deliver/{edition}")
def deliver(edition: str, x_trigger_token: str | None = Header(default=None)) -> dict:
    s = get_settings()
    if not s.trigger_token or x_trigger_token != s.trigger_token:
        raise HTTPException(status_code=403, detail="invalid trigger token")
    if edition not in VALID_EDITIONS:
        raise HTTPException(status_code=400, detail="invalid edition")
    message = run_delivery(edition, push=True)
    return {"edition": edition, "length": len(message)}


def run_portfolio_impact(user_id: str) -> None:
    """保有銘柄への影響考察を実行し、指定ユーザーに push する(重い処理・背景実行用)。"""
    try:
        holdings = db.list_holdings()
        if not holdings:
            line_client.push("保有銘柄が登録されていません。先に『保有追加』してください。", [user_id])
            return
        market = market_data.fetch_market_data()
        articles = news_fetcher.fetch_news(holdings)
        impact = scorer.analyze_portfolio_impact(holdings, market, articles)
        if impact is None:
            line_client.push("⚠️ 影響考察の生成に失敗しました(AI混雑の可能性)。少し待って再度お試しください。", [user_id])
            return
        line_client.push(formatter.format_portfolio_impact(impact), [user_id])
    except Exception:
        logger.exception("影響考察の実行に失敗")
        line_client.push("⚠️ 影響考察の生成中にエラーが発生しました。", [user_id])


def run_delivery_test(user_id: str) -> None:
    """配信テスト(朝刊プレビュー)を実行し push する(重い処理・背景実行用)。"""
    try:
        msg = run_delivery("morning", push=False)
        line_client.push(msg, [user_id])
    except Exception as e:  # noqa: BLE001
        logger.exception("配信テスト失敗")
        line_client.push(f"配信テスト失敗: {e}", [user_id])


def _current_edition() -> str:
    """現在の JST 時刻から最も近い配信回を選ぶ(いまの状況コマンド用)。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hour = datetime.now(ZoneInfo("Asia/Tokyo")).hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 16:
        return "noon"
    return "evening"


def run_now(user_id: str) -> None:
    """いまの状況(時刻に応じた最新ダイジェスト)を即時に作成して push する。"""
    try:
        msg = run_delivery(_current_edition(), push=False)
        line_client.push(msg, [user_id])
    except Exception as e:  # noqa: BLE001
        logger.exception("いまの状況の取得に失敗")
        line_client.push(f"いまの状況の取得に失敗: {e}", [user_id])


# 重いコマンド(Gemini呼び出し等)→背景実行してpush。キーは正規化後の文字列。
_IMPACT_ACK = "📈 保有銘柄への影響を考察中です…少々お待ちください(30秒ほど)。"
_NOW_ACK = "🔎 いまの市況・ニュースを取得中です…少々お待ちください(30秒ほど)。"
_HEAVY_COMMANDS = {
    "配信テスト": (run_delivery_test, "📰 朝刊プレビューを作成中です…少々お待ちください。"),
    "保有影響": (run_portfolio_impact, _IMPACT_ACK),
    "保有銘柄への影響": (run_portfolio_impact, _IMPACT_ACK),
    "いま": (run_now, _NOW_ACK),
    "今": (run_now, _NOW_ACK),
    "いまの市況": (run_now, _NOW_ACK),
    "状況": (run_now, _NOW_ACK),
}


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str | None = Header(default=None),
) -> Response:
    body = await request.body()
    if not line_client.verify_signature(body, x_line_signature or ""):
        raise HTTPException(status_code=403, detail="invalid signature")

    import json

    payload = json.loads(body or b"{}")
    for event in payload.get("events", []):
        if event.get("type") != "message" or event.get("message", {}).get("type") != "text":
            continue
        text = event["message"]["text"].strip()
        reply_token = event.get("replyToken")
        user_id = (event.get("source") or {}).get("userId")

        heavy = _HEAVY_COMMANDS.get(text)
        if heavy and user_id:
            # 重い処理は背景実行(replyTokenの30秒制限を回避)。即時ackして結果はpush。
            fn, ack = heavy
            background_tasks.add_task(fn, user_id)
            if reply_token:
                line_client.reply(reply_token, ack)
            continue

        reply = line_client.handle_command(text)
        if reply and reply_token:
            line_client.reply(reply_token, reply)

    return Response(status_code=200)
