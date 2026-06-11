"""Supabase クライアント (supabase-py)。service_role キーで操作する。"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_client() -> Client:
    s = get_settings()
    if not s.supabase_url or not s.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が未設定です")
    return create_client(s.supabase_url, s.supabase_service_role_key)


# ---------------------------------------------------------------------------
# holdings (保有銘柄)
# ---------------------------------------------------------------------------
def list_holdings() -> list[dict]:
    res = get_client().table("holdings").select("*").order("code").execute()
    return res.data or []


def add_holding(code: str, name: str, keywords: list[str] | None = None) -> dict:
    payload = {"code": code, "name": name, "keywords": keywords or []}
    res = (
        get_client()
        .table("holdings")
        .upsert(payload, on_conflict="code")
        .execute()
    )
    return (res.data or [{}])[0]


def delete_holding(code: str) -> bool:
    res = get_client().table("holdings").delete().eq("code", code).execute()
    return bool(res.data)


# ---------------------------------------------------------------------------
# delivery_logs (配信ログ)
# ---------------------------------------------------------------------------
def log_delivery(
    edition: str,
    message_length: int,
    headline_count: int,
    status: str,
    error: str | None = None,
) -> None:
    try:
        get_client().table("delivery_logs").insert(
            {
                "edition": edition,
                "message_length": message_length,
                "headline_count": headline_count,
                "status": status,
                "error": error,
            }
        ).execute()
    except Exception:  # ログ記録失敗で配信を止めない
        logger.exception("delivery_logs への記録に失敗")


# ---------------------------------------------------------------------------
# news_cache (重複排除)
# ---------------------------------------------------------------------------
def title_hash(title: str) -> str:
    """タイトルを正規化(空白・記号除去)してハッシュ化する。"""
    import re

    normalized = re.sub(r"[\s　\W_]+", "", title).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def filter_unseen(items: list[dict]) -> list[dict]:
    """まだ配信していない記事だけを返す。title_hash で判定する。

    items は {"title": ..., ...} を含む dict のリスト。
    記事数が多いと in.(...) クエリの URL が長すぎて PostgREST に弾かれるため、
    ハッシュをチャンクに分けて問い合わせる。
    """
    if not items:
        return []
    hashes = {title_hash(i["title"]): i for i in items}
    all_hashes = list(hashes.keys())
    seen: set[str] = set()
    client = get_client()
    for i in range(0, len(all_hashes), 50):
        chunk = all_hashes[i : i + 50]
        res = client.table("news_cache").select("title_hash").in_("title_hash", chunk).execute()
        seen.update(row["title_hash"] for row in (res.data or []))
    return [item for h, item in hashes.items() if h not in seen]


def mark_delivered(items: list[dict]) -> None:
    """配信済みとして news_cache に記録する。"""
    if not items:
        return
    rows = [{"title_hash": title_hash(i["title"]), "title": i["title"]} for i in items]
    # 重複(同一ハッシュ)を除いてからチャンク投入
    uniq = list({r["title_hash"]: r for r in rows}.values())
    client = get_client()
    for i in range(0, len(uniq), 100):
        try:
            client.table("news_cache").upsert(
                uniq[i : i + 100], on_conflict="title_hash"
            ).execute()
        except Exception:
            logger.exception("news_cache への記録に失敗")


def prune_old_cache(days: int = 7) -> None:
    """指定日数より古いキャッシュを削除する。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        get_client().table("news_cache").delete().lt("delivered_at", cutoff).execute()
    except Exception:
        logger.exception("news_cache の掃除に失敗")
