"""Gemini による一括スコアリング + 要約・編集。

無料枠の RPM 節約のため、1配信あたり API 呼び出しは1回に抑える。
出力は必ず JSON のみを返すよう指示する。
"""
from __future__ import annotations

import json
import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)

# plan は gemini-2.0-flash 指定だが、配布された無料枠キーでは limit:0 のため
# 無料枠が有効な gemini-2.5-flash を使用(2026-06時点で実測確認済み)
MODEL_NAME = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = """あなたは投資・経済ニュースの編集者です。
渡された記事リストを評価し、必ず JSON のみを返してください(前後に説明文やコードブロック記号を付けない)。

各記事に 1〜5 点の重要度を付けます。ルーブリック:
- 5点: 日銀/FRBの政策決定・サプライズ(利上げ/利下げ/緊急声明)、市場急変(主要指数±3%超)
- 4点: 主要経済指標の発表結果(CPI/雇用統計/GDP/PCE)、主要指数±2%超、戦争・紛争の重大進展、保有銘柄の決算・適時開示
- 3点: 中銀総裁・大統領級の要人発言、金/原油/ドル円の大幅変動(±2%超)、保有銘柄の業界ニュース
- 2点: 一般的な企業ニュース、業界動向
- 1点: コラム、相場解説、広告的記事

ブーストルール: 記事に保有銘柄名(銘柄名・コード)が含まれる場合は +1点(上限5点)。

3点以上の記事のみを headlines に含めてください。
summary は30字以内の見出し。**必ず日本語で書くこと。英語など外国語の記事は内容を日本語に翻訳・要約する**(原文の英語をそのまま出さない)。
**具体的に書くこと**: 固有名詞(人名・国・企業・指標名)と数値(%・金額・前年比など)を入れ、新聞の見出しのように情報量を持たせる。
良い例:「米5月CPI +4.2%、3年ぶり高水準」「日銀、6月利上げ観測9割に」。
悪い例(曖昧で禁止):「株価が変動」「金利に関するニュース」「経済に動き」。
同じ出来事の重複見出しは1つにまとめる。category は「金融政策」「経済指標」「地政学」「商品市況」「個別銘柄」などの短い分類。
portfolio_notes は該当ニュースがある保有銘柄のみ、1銘柄1行の一言。
ai_comment は市況全体を踏まえた投資判断の示唆を60字以内で。断定を避け『〜が妥当』『〜に注意』の形にする。

出力スキーマ:
{
  "headlines": [{"score": 5, "summary": "見出し", "category": "金融政策"}],
  "portfolio_notes": ["銘柄に関する一言"],
  "ai_comment": "示唆"
}"""


def _build_user_prompt(
    articles: list[dict],
    market: dict[str, dict],
    holdings: list[dict],
    events: list[dict],
) -> str:
    article_lines = [
        f"{i + 1}. [{a.get('label', '')}] {a['title']} / {a.get('summary', '')}"
        for i, a in enumerate(articles)
    ]
    market_lines = [
        f"{m['name']}: {m['value']} ({m['change_pct']:+.2f}%)"
        for m in market.values()
        if m.get("ok") and m.get("value") is not None and m.get("change_pct") is not None
    ]
    holding_lines = [f"{h['code']} {h['name']}" for h in holdings]
    event_lines = [f"{e['date']} {e['name']}" for e in events]

    return (
        "【記事リスト】\n"
        + "\n".join(article_lines)
        + "\n\n【市況】\n"
        + "\n".join(market_lines)
        + "\n\n【保有銘柄】\n"
        + "\n".join(holding_lines)
        + "\n\n【経済指標予定】\n"
        + ("\n".join(event_lines) if event_lines else "なし")
    )


def _extract_json(text: str) -> dict:
    """モデル出力から JSON を取り出す。コードブロックで囲まれていても対応。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _call_gemini(system: str, user: str) -> str:
    import google.generativeai as genai

    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY が未設定です")
    genai.configure(api_key=s.gemini_api_key)
    model = genai.GenerativeModel(MODEL_NAME, system_instruction=system)
    resp = model.generate_content(
        user,
        generation_config={"response_mime_type": "application/json"},
    )
    return resp.text


def _has_japanese(text: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text)


def _fallback(articles: list[dict]) -> dict:
    """スコアリング不能時: 新しい順に5件をそのまま見出しにする。

    AI翻訳が使えないため、英語タイトルが混ざらないよう日本語記事を優先する。
    """
    def key(a):
        # 日本語記事を優先、その中で新しい順
        return (_has_japanese(a.get("title", "")), a.get("published") or 0)

    top = sorted(articles, key=key, reverse=True)[:5]
    return {
        "headlines": [
            {"score": 3, "summary": a["title"][:30], "category": ""} for a in top
        ],
        "portfolio_notes": [],
        "ai_comment": "(AI要約に失敗したため見出しのみ表示)",
        "_fallback": True,
    }


def score_articles(
    articles: list[dict],
    market: dict[str, dict],
    holdings: list[dict],
    events: list[dict],
) -> dict:
    """記事を一括スコアリングし、整形済みの結果 dict を返す。

    JSON パース失敗時は1回だけ再試行し、それでも失敗したらフォールバック。
    """
    if not articles:
        return {"headlines": [], "portfolio_notes": [], "ai_comment": ""}

    user = _build_user_prompt(articles, market, holdings, events)

    for attempt in range(2):
        try:
            raw = _call_gemini(SYSTEM_INSTRUCTION, user)
            data = _extract_json(raw)
            data.setdefault("headlines", [])
            data.setdefault("portfolio_notes", [])
            data.setdefault("ai_comment", "")
            # score 降順
            data["headlines"] = sorted(
                data["headlines"], key=lambda h: h.get("score", 0), reverse=True
            )
            return data
        except (json.JSONDecodeError, ValueError):
            logger.warning("Gemini JSON パース失敗 (attempt %s)", attempt + 1)
        except Exception:
            logger.exception("Gemini 呼び出し失敗 (attempt %s)", attempt + 1)
            break

    return _fallback(articles)
