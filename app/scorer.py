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
detail は見出しの補足を50字以内の日本語1文で(背景・数値・市場への影響など。見出しの単なる言い換えは禁止)。
**経済指標(CPI・雇用統計・GDP・PCE等)の発表結果が記事にある場合は、可能な限り「実績 vs 市場予想(コンセンサス)」と上振れ/下振れ(サプライズ)、債券・為替・株の反応を detail に必ず盛り込む**(例:「+4.2%、予想+4.0%を上回り利上げ観測強まる」)。記事に予想値が無ければ実績と市場の反応を書く。
source は、その見出しの根拠になった記事の番号(入力リストの先頭の数字)を整数で1つ返す。複数記事をまとめた場合は最も中心的な1件の番号。
portfolio_notes は該当ニュースがある保有銘柄のみ、1銘柄1行の一言。
ai_comment は市況全体を踏まえた投資判断の示唆を60字以内で。断定を避け『〜が妥当』『〜に注意』の形にする。

出力スキーマ:
{
  "headlines": [{"score": 5, "summary": "見出し", "detail": "50字以内の補足", "source": 3, "category": "金融政策"}],
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


def _retry_delay_sec(err: Exception) -> float:
    """429 エラーから推奨待機秒を抽出。取れなければ既定35秒(無料枠の毎分窓を跨ぐ)。"""
    import re as _re

    sec = getattr(getattr(err, "retry_delay", None), "seconds", None)
    if sec:
        return min(float(sec) + 2, 60)
    m = _re.search(r"retry in (\d+(?:\.\d+)?)", str(err))
    if m:
        return min(float(m.group(1)) + 2, 60)
    return 35.0


def _call_gemini(system: str, user: str) -> str:
    import time

    import google.generativeai as genai
    from google.api_core import exceptions as gexc

    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY が未設定です")
    genai.configure(api_key=s.gemini_api_key)
    model = genai.GenerativeModel(MODEL_NAME, system_instruction=system)
    # 無料枠は約5リクエスト/分。429(レート制限)時は推奨秒だけ待って1回再試行する。
    for attempt in range(2):
        try:
            resp = model.generate_content(
                user,
                generation_config={"response_mime_type": "application/json"},
            )
            return resp.text
        except gexc.ResourceExhausted as e:
            if attempt == 0:
                wait = _retry_delay_sec(e)
                logger.warning("Gemini 429。%.0f秒待って再試行", wait)
                time.sleep(wait)
                continue
            raise


def _has_japanese(text: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text)


# ---------------------------------------------------------------------------
# 保有銘柄への影響考察(オンデマンド)
# ---------------------------------------------------------------------------
IMPACT_SYSTEM = """あなたは機関投資家向けの株式アナリストです。
渡された保有銘柄・市況・関連ニュースをもとに、保有ポートフォリオへの影響を
「短期(数日〜数週間)」「中期(数ヶ月)」「長期(1年以上)」の3つの時間軸で合理的に考察します。

ルール:
- 必ず日本語。投機的な断定は避け、根拠(金利・為替・商品市況・金融政策・個別材料など)を明示する。
- 各コメントは40〜70字程度の1〜2文。具体的に書く。
- stocks には、市況やニュースから影響が読み取れる銘柄を最大6件。材料が薄い銘柄は省いてよい。
- 必ず JSON のみを返す(前後に説明やコードブロック記号を付けない)。

出力スキーマ:
{
  "overall": {"short": "短期の全体観", "mid": "中期の全体観", "long": "長期の全体観"},
  "stocks": [
    {"name": "銘柄名", "short": "短期影響", "mid": "中期影響", "long": "長期影響"}
  ]
}"""


def analyze_portfolio_impact(
    holdings: list[dict], market: dict[str, dict], articles: list[dict]
) -> dict | None:
    """保有銘柄への影響を短期/中期/長期で考察する。失敗時 None。"""
    if not holdings:
        return {"overall": {"short": "", "mid": "", "long": ""}, "stocks": []}

    market_lines = [
        f"{m['name']}: {m['value']} ({m['change_pct']:+.2f}%)"
        for m in market.values()
        if m.get("ok") and m.get("value") is not None and m.get("change_pct") is not None
    ]
    holding_lines = [
        f"{h['code']} {h['name']}" + (f" (関連: {', '.join(h.get('keywords') or [])})" if h.get("keywords") else "")
        for h in holdings
    ]
    news_lines = [f"- {a['title']}" for a in articles[:40]]
    user = (
        "【保有銘柄】\n" + "\n".join(holding_lines)
        + "\n\n【市況】\n" + ("\n".join(market_lines) or "(取得失敗)")
        + "\n\n【関連ニュース】\n" + ("\n".join(news_lines) or "なし")
    )
    for attempt in range(2):
        try:
            data = _extract_json(_call_gemini(IMPACT_SYSTEM, user))
            data.setdefault("overall", {})
            data.setdefault("stocks", [])
            return data
        except (json.JSONDecodeError, ValueError):
            logger.warning("影響考察 JSON パース失敗 (attempt %s)", attempt + 1)
        except Exception:
            logger.exception("影響考察 Gemini 呼び出し失敗")
            return None
    return None


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
            {
                "score": 3,
                "summary": a["title"][:40],
                "detail": "",
                "link": a.get("link", ""),
                "source_label": a.get("label", ""),
                "category": "",
            }
            for a in top
        ],
        "portfolio_notes": [],
        "ai_comment": "(AI要約に失敗したため見出しのみ表示)",
        "_fallback": True,
    }


def _attach_sources(headlines: list[dict], articles: list[dict]) -> None:
    """Gemini が返した source 番号から元記事のリンク・取得元を各見出しに紐付ける。"""
    for h in headlines:
        idx = h.get("source")
        if isinstance(idx, int) and 1 <= idx <= len(articles):
            art = articles[idx - 1]
            h["link"] = art.get("link", "")
            h["source_label"] = art.get("label", "")
        else:
            h.setdefault("link", "")
            h.setdefault("source_label", "")


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
            # 元記事リンク・取得元を紐付け
            _attach_sources(data["headlines"], articles)
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
