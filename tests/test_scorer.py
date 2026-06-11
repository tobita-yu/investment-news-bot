from unittest import mock

from app import scorer


def test_extract_json_plain():
    assert scorer._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_codeblock():
    text = '```json\n{"a": 1}\n```'
    assert scorer._extract_json(text) == {"a": 1}


def test_score_articles_sorts_by_score():
    fake_json = (
        '{"headlines": [{"score": 3, "summary": "low"}, '
        '{"score": 5, "summary": "high"}], '
        '"portfolio_notes": [], "ai_comment": "x"}'
    )
    with mock.patch.object(scorer, "_call_gemini", return_value=fake_json):
        out = scorer.score_articles(
            [{"title": "t", "summary": "s"}], {}, [], []
        )
    assert [h["score"] for h in out["headlines"]] == [5, 3]


def test_score_articles_fallback_on_bad_json():
    with mock.patch.object(scorer, "_call_gemini", return_value="not json"):
        out = scorer.score_articles(
            [{"title": "記事1", "summary": "s", "published": 1},
             {"title": "記事2", "summary": "s", "published": 2}],
            {}, [], [],
        )
    assert out["_fallback"] is True
    assert len(out["headlines"]) == 2


def test_empty_articles_returns_empty():
    out = scorer.score_articles([], {}, [], [])
    assert out["headlines"] == []
