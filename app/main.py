"""FastAPI アプリ。配信トリガー /deliver/{edition} と LINE Webhook /webhook。"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request, Response

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


@app.post("/webhook")
async def webhook(request: Request, x_line_signature: str | None = Header(default=None)) -> Response:
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

        if text == "配信テスト":
            try:
                msg = run_delivery("morning", push=False)
                reply = msg
            except Exception as e:  # noqa: BLE001
                logger.exception("配信テスト失敗")
                reply = f"配信テスト失敗: {e}"
        else:
            reply = line_client.handle_command(text)

        if reply and reply_token:
            line_client.reply(reply_token, reply)

    return Response(status_code=200)
