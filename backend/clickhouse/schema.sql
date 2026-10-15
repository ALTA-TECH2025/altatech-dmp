-- AltaTech DMP — ClickHouse 熱層 DDL（摘要）。冷層為 Iceberg on MinIO。
CREATE DATABASE IF NOT EXISTS dmp;

CREATE TABLE IF NOT EXISTS dmp.events
(
    project          LowCardinality(String),
    event_date       Date MATERIALIZED toDate(time, 'Asia/Taipei'),
    time             DateTime64(3, 'UTC'),
    event            LowCardinality(String),
    source           LowCardinality(String),
    one_id           String,
    one_id_uint      UInt64 MATERIALIZED cityHash64(one_id),
    entity_type      LowCardinality(String),
    entity_id        String,
    distinct_id      String,
    session_id       String,
    event_id         String,
    platform         LowCardinality(String),
    app_id           LowCardinality(String),
    symbol           LowCardinality(String),
    side             LowCardinality(String),
    channel          LowCardinality(String),
    amount           Nullable(Float64),
    utm_source       LowCardinality(String),
    utm_campaign     LowCardinality(String),
    props            JSON,
    ingest_time      DateTime64(3, 'UTC'),
    schema_version   UInt16,
    late             UInt8 DEFAULT 0,
    batch_id         LowCardinality(String) DEFAULT ''      -- 載入批次（任務:窗口:嘗試），三方對帳用（docs/23 §4.1）
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.events', '{replica}', ingest_time)
PARTITION BY toYYYYMM(event_date)
ORDER BY (project, event_date, event, one_id, time, event_id)   -- event_id 必須在排序鍵：ReplacingMergeTree 合併時以排序鍵去重，否則同客戶同事件同秒的兩筆會被合併成一筆（e2e 實測）
TTL event_date + INTERVAL 13 MONTH TO VOLUME 'cold';

ALTER TABLE dmp.events ADD INDEX IF NOT EXISTS idx_one_id one_id TYPE bloom_filter(0.01) GRANULARITY 4;
ALTER TABLE dmp.events ADD INDEX IF NOT EXISTS idx_event_id event_id TYPE bloom_filter(0.001) GRANULARITY 1;

CREATE TABLE IF NOT EXISTS dmp.users
(
    one_id       String,
    props        JSON,
    first_seen   DateTime64(3, 'UTC'),
    updated_at   DateTime64(3, 'UTC')
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.users', '{replica}', updated_at)
ORDER BY one_id;

-- 維度表（docs/06 §7.2）：每張一表，主鍵 = 事件／用戶屬性值；< 100 萬列以 JOIN，否則建 dictionary 用 dictGet
CREATE TABLE IF NOT EXISTS dmp.dim_instrument
(
    symbol       String,                   -- 主鍵 = events.symbol
    props        JSON,                     -- name, market, industry, type, is_etf, lot_size
    updated_at   DateTime64(3, 'UTC')
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.dim_instrument', '{replica}', updated_at)
ORDER BY symbol;
-- dim_account（主鍵 account_no，180 萬列 → dictionary）、dim_branch、dim_agent、dim_campaign 同型，由同步任務 upsert／全量覆蓋

-- 登入改寫覆蓋表（docs/19 §2.5）：微批把近 7 天匿名事件的 one_id 改到登入客戶前，查詢層以本表在 SQL 編譯時覆蓋
CREATE TABLE IF NOT EXISTS dmp.oneid_backfill
(
    distinct_id  String,
    from_time    DateTime64(3, 'UTC'),
    to_time      DateTime64(3, 'UTC'),
    anon_one_id  String,
    target_one_id String,
    applied      UInt8 DEFAULT 0,
    created_at   DateTime64(3, 'UTC')
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.oneid_backfill', '{replica}', created_at)
ORDER BY (distinct_id, from_time);
-- 套用：ALTER TABLE dmp.events UPDATE one_id = target, one_id_uint = cityHash64(target)
--       WHERE distinct_id IN (...) AND event_date BETWEEN d-7 AND d AND one_id IN (anon_one_ids)  -- 只動近 8 個日分區

-- 隔離區（docs/06 §3）：清洗未通過的來源列，30 天後丟棄；拒絕列（個資命中）只記原因不記內容
CREATE TABLE IF NOT EXISTS dmp.quarantine
(
    task_id      LowCardinality(String),
    batch_id     LowCardinality(String),
    at           DateTime64(3, 'UTC'),
    reason       String,
    row          String                     -- 來源列 JSON（拒絕列為空）
)
ENGINE = ReplicatedMergeTree('/ch/{shard}/dmp.quarantine', '{replica}')
PARTITION BY toYYYYMMDD(at)
ORDER BY (task_id, at)
TTL toDateTime(at) + INTERVAL 30 DAY;

-- 載入對帳（每批一列）：來源筆數 → 通過 → 載入，差異 > 0.1% 由排程器重跑
CREATE TABLE IF NOT EXISTS dmp.ingest_recon
(
    task_id      LowCardinality(String),
    batch_id     LowCardinality(String),
    at           DateTime64(3, 'UTC'),
    rows_in      UInt64, rows_ok UInt64, rows_quarantined UInt64, rows_rejected UInt64, rows_loaded UInt64
)
ENGINE = ReplicatedMergeTree('/ch/{shard}/dmp.ingest_recon', '{replica}')
ORDER BY (task_id, at);

CREATE TABLE IF NOT EXISTS dmp.id_map
(
    id_type      LowCardinality(String),
    id_hash      String,
    one_id       String,
    confidence   Float32,
    version      UInt32,
    updated_at   DateTime64(3, 'UTC')
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.id_map', '{replica}', version)
ORDER BY (id_type, id_hash);

CREATE TABLE IF NOT EXISTS dmp.user_tags
(
    tag_id       LowCardinality(String),
    tag_value    String,
    version      UInt32,
    users        AggregateFunction(groupBitmap, UInt64)
)
ENGINE = ReplicatedAggregatingMergeTree('/ch/{shard}/dmp.user_tags', '{replica}')
ORDER BY (tag_id, tag_value, version);

CREATE MATERIALIZED VIEW IF NOT EXISTS dmp.events_daily_agg
ENGINE = ReplicatedAggregatingMergeTree('/ch/{shard}/dmp.events_daily_agg', '{replica}')
ORDER BY (project, event_date, event, platform, channel, utm_source)
AS SELECT project, event_date, event, platform, channel, utm_source,
          countState() AS cnt, uniqState(one_id) AS users, sumState(coalesce(amount, 0)) AS amount
FROM dmp.events GROUP BY project, event_date, event, platform, channel, utm_source;

-- 漏斗範例（開戶 → 入金 → 首筆成交，30 天）
-- SELECT level, count() FROM (
--   SELECT one_id, windowFunnel(2592000)(time, event='kh_review_pass', event='fund_deposit', event='bond_deal') AS level
--   FROM dmp.events WHERE event_date >= today()-45 GROUP BY one_id) GROUP BY level ORDER BY level;
