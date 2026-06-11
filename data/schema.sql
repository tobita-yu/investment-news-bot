-- =============================================================
-- investment-news-bot Supabase スキーマ
-- 新規 Supabase プロジェクトの SQL Editor にこの全文を貼り付けて実行する。
-- 2026年5月のセキュリティポリシー変更により GRANT が必須。
-- =============================================================

-- 保有銘柄
create table if not exists holdings (
  id bigint generated always as identity primary key,
  code text not null unique,          -- 例: '5016', '2840', '314A'
  name text not null,                 -- 例: 'JX金属'
  keywords text[] default '{}',       -- ニュース検索用の追加キーワード
  created_at timestamptz default now()
);

-- 配信ログ
create table if not exists delivery_logs (
  id bigint generated always as identity primary key,
  edition text not null,              -- 'morning' | 'noon' | 'evening'
  delivered_at timestamptz default now(),
  message_length int,
  headline_count int,
  status text not null,               -- 'success' | 'failed'
  error text
);

-- ニュース重複排除キャッシュ(7日より古い行はコード側で削除)
create table if not exists news_cache (
  id bigint generated always as identity primary key,
  title_hash text not null unique,
  title text not null,
  delivered_at timestamptz default now()
);

-- GRANT(必須)
grant usage on schema public to anon, authenticated, service_role;
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;

-- RLS: service_role キーのみで操作するため有効化(全拒否でよい)
alter table holdings enable row level security;
alter table delivery_logs enable row level security;
alter table news_cache enable row level security;

-- =============================================================
-- 初期保有銘柄
-- =============================================================
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
