# 投資ニュース LINE 配信 Bot

毎日3回(8:30 / 12:00 / 20:00 JST)、世界と日本の市況・経済ニュースを収集し、
Gemini API で重要度スコアリング+要約して、LINE 公式アカウントからプッシュ配信する Bot。

全て無料枠で運用する(LINE 200通/月、Gemini 無料枠、Supabase Free、Render Free、GitHub Actions)。

## 構成

```
investment-news-bot/
├── app/
│   ├── main.py            # FastAPI: /deliver/{edition} と /webhook(LINE)
│   ├── config.py          # 環境変数(pydantic-settings)
│   ├── market_data.py     # Yahoo chart API を curl_cffi で取得・整形
│   ├── news_fetcher.py    # RSS 取得・重複排除
│   ├── calendar_loader.py # economic_calendar.json 読み込み
│   ├── scorer.py          # Gemini スコアリング+要約
│   ├── formatter.py       # LINE メッセージ整形(4,500字制限)
│   ├── line_client.py     # push送信 / Webhook署名検証 / 保有銘柄コマンド
│   └── db.py              # Supabase クライアント
├── data/economic_calendar.json
├── .github/workflows/scheduler.yml
├── tests/
├── requirements.txt
├── render.yaml
└── .env.example
```

## ローカル開発

```bash
pip install -r requirements.txt
cp .env.example .env   # 値を埋める
uvicorn app.main:app --reload

# メッセージ整形のサンプル確認(外部API不要)
python -m app.formatter --dry-run

# テスト
pytest
```

## セットアップ手順

### 1. Supabase
1. プロジェクトを作成し、SQL Editor で下記スキーマ SQL を実行する。
2. 初期保有銘柄 SQL も実行する。
3. Settings → API から `Project URL` と `service_role` キーを控える。

スキーマ SQL(**2026年5月のセキュリティポリシー変更で GRANT 必須**):

```sql
create table if not exists holdings (
  id bigint generated always as identity primary key,
  code text not null unique,
  name text not null,
  keywords text[] default '{}',
  created_at timestamptz default now()
);

create table if not exists delivery_logs (
  id bigint generated always as identity primary key,
  edition text not null,
  delivered_at timestamptz default now(),
  message_length int,
  headline_count int,
  status text not null,
  error text
);

create table if not exists news_cache (
  id bigint generated always as identity primary key,
  title_hash text not null unique,
  title text not null,
  delivered_at timestamptz default now()
);

grant usage on schema public to anon, authenticated, service_role;
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;

alter table holdings enable row level security;
alter table delivery_logs enable row level security;
alter table news_cache enable row level security;
```

初期保有銘柄:

```sql
insert into holdings (code, name, keywords) values
  ('1997', '暁飯島工業', '{}'),
  ('2840', 'iFreeETF NASDAQ100', '{"ナスダック","NASDAQ"}'),
  ('314A', 'iシェアーズ ゴールド', '{"金価格","NY金","ゴールド"}'),
  ('316A', 'iFreeETF FANG+', '{"FANG","米ハイテク"}'),
  ('424A', 'GXゴールドH', '{"金価格","NY金"}'),
  ('5016', 'JX金属', '{"銅価格","非鉄"}'),
  ('5254', 'Arent', '{"建設DX"}'),
  ('7167', 'めぶきFG', '{"地銀","銀行株"}'),
  ('7581', 'サイゼリヤ', '{"外食"}'),
  ('7779', 'CYBERDYNE', '{"サイバダイン"}')
on conflict (code) do nothing;
```

### 2. LINE Developers
1. Messaging API チャネルを作成。
2. `Channel access token` と `Channel secret` を取得 → `.env` へ。
3. 自分の `userId` を取得し `LINE_USER_ID` へ(複数配信はカンマ区切り)。
4. デプロイ後、Webhook URL を `https://<render-app>.onrender.com/webhook` に設定し、Webhook 利用を ON。

### 3. Gemini API
Google AI Studio で API キーを発行(既存のものを流用可)→ `GEMINI_API_KEY`。

### 4. Render
1. GitHub リポジトリを連携し、`render.yaml` を使って Web Service を作成(Free プラン)。
2. 環境変数を全て設定(`.env.example` 参照)。`TRIGGER_TOKEN` は `python -c "import secrets;print(secrets.token_hex(16))"` で生成。

### 5. GitHub Secrets
リポジトリの Settings → Secrets に登録:
- `APP_URL` … `https://<render-app>.onrender.com`
- `TRIGGER_TOKEN` … Render と同じ値

### 6. 動作確認
- Actions タブ → `deliver` ワークフロー → `Run workflow`(workflow_dispatch)で手動実行し、LINE 受信を確認。
- その後、3配信時刻の自動実行を1日監視する。

## LINE コマンド

トーク画面で以下を送信:

| コマンド | 動作 |
|---|---|
| `保有追加 <コード> <銘柄名> [キーワード...]` | 保有銘柄を登録 |
| `保有削除 <コード>` | 保有銘柄を削除 |
| `保有一覧` | 登録銘柄を表示 |
| `配信テスト` | その場で朝刊フローを実行して内容を返信(push せず) |

## economic_calendar.json の更新

`data/economic_calendar.json` に主要イベントを手動メンテする(月1回想定)。

```json
{"date": "2026-06-16", "name": "日銀金融政策決定会合(結果発表)", "importance": 5}
```

`importance` は 1〜5。当日イベントは【今日の予定】に、直近の次回イベントは「特になし(次回: …)」に表示される。
更新後は git push すれば Render に反映される。

## 配信回ごとの内容

| 回 | 時刻(JST) | 市況セクション |
|---|---|---|
| 朝刊 morning | 8:30 | NY市場の終値・変化率中心 |
| 昼刊 noon | 12:00 | 東京市場(日経平均・TOPIX代用1306.T・ドル円) |
| 夕刊 evening | 20:00 | 東京市場の大引け |

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `/deliver` が 403 | `X-Trigger-Token` ヘッダーと `TRIGGER_TOKEN` 環境変数の一致を確認 |
| LINE が届かない | `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` を確認。失敗時は自分宛てにエラー通知が飛ぶ |
| Webhook が 403 | LINE Developers の Channel secret と `LINE_CHANNEL_SECRET` の一致を確認 |
| 市況が「取得失敗」 | Yahoo の一時的失敗/レート制限。curl_cffi のChrome偽装で回避済み。リトライ3回後も失敗した項目のみ表示し配信は止まらない |
| ヘッドラインが見出しのみ | Gemini の JSON 失敗時フォールバック(新しい順5件)。API キー/レート制限を確認 |
| Render コールドスタート | GitHub Actions が `/health` で起こし、30秒待ってから配信する |

## 制約・注意

- LINE 無料プランは月200通。1人配信で約90通/月。**2人配信は約180通/月で月末枠切れリスクあり**(増やす場合は要確認)。
- Gemini 無料枠の RPM/RPD に配慮し、1配信あたり API 呼び出しは1回(全記事一括)に抑える設計。
- 市況は Yahoo Finance chart API を curl_cffi(Chrome偽装)で取得。Yahoo はデータセンターIP/TLS指紋で429ブロックするため impersonate が必須。取得失敗してもクラッシュせず部分配信する。
- 祝日・休場日のスキップ判定は初版では入れていない(v2 で jpholiday 導入を検討)。
- X(Twitter)のデータ取得は行わない。将来 RSS を追加する場合は `news_fetcher.RSS_FEEDS` に1行追加するだけでよい。
