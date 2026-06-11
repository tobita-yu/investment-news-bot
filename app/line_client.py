"""LINE Messaging API: push 送信 / Webhook 署名検証 / 保有銘柄コマンド処理。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging

import httpx

from app import db
from app.config import get_settings

logger = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def _auth_headers() -> dict:
    s = get_settings()
    return {
        "Authorization": f"Bearer {s.line_channel_access_token}",
        "Content-Type": "application/json",
    }


def _split_for_line(text: str, limit: int = 4900) -> list[str]:
    """LINE 1メッセージ5000字制限に対する安全分割(基本は分割されない想定)。"""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], text
    while len(cur) > limit:
        cut = cur.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        chunks.append(cur[:cut])
        cur = cur[cut:].lstrip("\n")
    if cur:
        chunks.append(cur)
    return chunks


def push(text: str, user_ids: list[str] | None = None) -> None:
    """指定ユーザー(未指定なら設定の全員)にプッシュ送信する。"""
    s = get_settings()
    targets = user_ids if user_ids is not None else s.line_user_ids
    if not targets:
        raise RuntimeError("LINE_USER_ID が未設定です")
    messages = [{"type": "text", "text": t} for t in _split_for_line(text)]
    with httpx.Client(timeout=30) as client:
        for uid in targets:
            resp = client.post(
                PUSH_URL, headers=_auth_headers(), json={"to": uid, "messages": messages}
            )
            if resp.status_code != 200:
                logger.error("LINE push 失敗 uid=%s %s %s", uid, resp.status_code, resp.text)
                resp.raise_for_status()


def reply(reply_token: str, text: str) -> None:
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            REPLY_URL,
            headers=_auth_headers(),
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )
        if resp.status_code != 200:
            logger.error("LINE reply 失敗 %s %s", resp.status_code, resp.text)


def verify_signature(body: bytes, signature: str) -> bool:
    """X-Line-Signature を検証する。"""
    s = get_settings()
    if not s.line_channel_secret or not signature:
        return False
    mac = hmac.new(s.line_channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# 保有銘柄コマンド処理
# ---------------------------------------------------------------------------
HELP_TEXT = (
    "📖 使い方\n"
    "下のメニューのボタン、または以下を送信:\n\n"
    "🔎 いま … 今の市況・重要ニュースを即取得\n"
    "📈 保有影響 … 保有銘柄への影響を短期/中期/長期で考察\n"
    "📋 保有一覧 … 登録銘柄を表示\n"
    "📅 指標 … 直近の経済指標予定\n"
    "📰 配信テスト … 朝刊プレビュー\n"
    "➕ 保有追加 <コード> <銘柄名> [キーワード...]\n"
    "　例: 保有追加 7203 トヨタ 自動車\n"
    "➖ 保有削除 <コード>\n"
    "　例: 保有削除 7203"
)


def handle_command(text: str) -> str | None:
    """テキストコマンドを処理して返信文字列を返す。対象外なら None。

    対応: 保有追加 / 保有削除 / 保有一覧 / ヘルプ / 指標(配信テストは main 側で処理)
    """
    text = text.strip()
    parts = text.split()
    if not parts:
        return None
    cmd = parts[0]

    if cmd in ("ヘルプ", "へるぷ", "help", "メニュー", "使い方"):
        return HELP_TEXT

    if cmd in ("指標", "カレンダー", "予定"):
        from app import calendar_loader

        events = calendar_loader.upcoming_events(days=45)
        if not events:
            return "📅 今後45日の登録イベントはありません。"
        lines = []
        for e in events:
            d = e["_date"]
            star = "⭐" * int(e.get("importance", 0))
            lines.append(f"{d.month}/{d.day} {e['name']} {star}")
        return "📅 直近の経済指標予定\n" + "\n".join(lines)

    if cmd == "保有一覧":
        holdings = db.list_holdings()
        if not holdings:
            return "保有銘柄は登録されていません。"
        lines = [f"{h['code']} {h['name']}" for h in holdings]
        return "【保有銘柄一覧】\n" + "\n".join(lines)

    if cmd == "保有追加":
        if len(parts) < 3:
            return "形式: 保有追加 <コード> <銘柄名> [キーワード...]"
        code, name = parts[1], parts[2]
        keywords = parts[3:]
        db.add_holding(code, name, keywords)
        kw = f" / キーワード: {', '.join(keywords)}" if keywords else ""
        return f"追加しました: {code} {name}{kw}"

    if cmd == "保有削除":
        if len(parts) < 2:
            return "形式: 保有削除 <コード>"
        code = parts[1]
        ok = db.delete_holding(code)
        return f"削除しました: {code}" if ok else f"見つかりませんでした: {code}"

    return None  # コマンド以外(配信テスト含む)は呼び出し側で処理
