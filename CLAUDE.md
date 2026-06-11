# investment-news-bot

投資ニュースを1日3回 LINE にプッシュ配信する Bot。詳細仕様は `investment-news-bot-plan.md`(親ディレクトリ)、セットアップは `README.md` を参照。

## アーキテクチャ
- FastAPI(Render Free でホスト)。`/deliver/{edition}` を GitHub Actions の cron が叩く。`/webhook` で LINE コマンド受信。
- 配信フロー本体は `app/main.py` の `run_delivery()`。market_data → news_fetcher → calendar_loader → scorer(Gemini一括1回) → formatter → line_client.push → db.log_delivery の順。
- 市況は Yahoo Finance chart API を `curl_cffi` の `impersonate="chrome"` で直叩き(yfinance/plain requests はデータセンターIP・TLS指紋で429ブロックされるため。これが必須の回避策)。
- DB は Supabase。`service_role` キーで操作(RLS 全拒否)。holdings / delivery_logs / news_cache の3テーブル。

## 重要な制約(壊さないこと)
- Gemini API 呼び出しは1配信あたり1回(全記事一括)。RPM 節約のため複数回に分けない。
- 市況 / RSS は失敗してもクラッシュさせず部分配信する。market_data は取得失敗項目を `ok=False` で返す。
- LINE 無料枠は月200通。配信先を2人以上に増やす場合はユーザー確認(月末枠切れリスク)。
- formatter の絵文字は 🔴(5)🟠(4)🟡(3) のみ。仕様外の絵文字を足さない。
- メッセージは4,500字上限。超過時は低スコアヘッドラインから削る。

## 開発
- テスト: `pytest`(外部APIはモック)。`python -m app.formatter --dry-run` で整形サンプル確認。
- 開発・本番とも Python 3.10+ で動作(plan は 3.12 想定だがローカルは 3.10)。
- 経済指標は `data/economic_calendar.json` を手動メンテ(月1回)。
