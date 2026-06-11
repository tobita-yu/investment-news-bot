from types import SimpleNamespace
from unittest import mock

from app import news_fetcher


def test_normalize_removes_symbols_and_space():
    assert news_fetcher._normalize("日銀、6月利上げ！") == news_fetcher._normalize("日銀6月利上げ")


def test_holdings_feeds_builds_query():
    feeds = news_fetcher._holdings_feeds(
        [{"code": "5016", "name": "JX金属", "keywords": ["銅価格"]}]
    )
    assert feeds[0][0] == "保有:JX金属"
    assert "JX" in feeds[0][1] or "%" in feeds[0][1]  # URL エンコード済み


def test_fetch_news_dedupes_titles():
    def fake_parse(url):
        return SimpleNamespace(
            entries=[
                SimpleNamespace(title="同じ ニュース！", summary="a", link="l1"),
                SimpleNamespace(title="同じニュース", summary="b", link="l2"),
                SimpleNamespace(title="別のニュース", summary="c", link="l3"),
            ]
        )

    with mock.patch("feedparser.parse", side_effect=fake_parse):
        items = news_fetcher.fetch_news(holdings=[])
    titles = [i["title"] for i in items]
    # 正規化で同一判定される2件は1件に
    assert "別のニュース" in titles
    assert titles.count("同じ ニュース！") + titles.count("同じニュース") == 1


def test_fetch_news_handles_feed_error():
    with mock.patch("feedparser.parse", side_effect=Exception("boom")):
        items = news_fetcher.fetch_news(holdings=[])
    assert items == []
