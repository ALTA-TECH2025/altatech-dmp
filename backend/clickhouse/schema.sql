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
    late             UInt8 DEFAULT 0
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.events', '{replica}', ingest_time)
PARTITION BY toYYYYMM(event_date)
ORDER BY (project, event_date, event, one_id, time)
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

CREATE TABLE IF NOT EXISTS dmp.items
(
    item_type    LowCardinality(String),   -- account | instrument | agent | branch | campaign | content
    item_id      String,
    props        JSON,
    updated_at   DateTime64(3, 'UTC')
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dmp.items', '{replica}', updated_at)
ORDER BY (item_type, item_id);

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
--   SELECT one_id, windowFunnel(2592000)(time, event='account_open_approved', event='fund_deposit', event='order_filled') AS level
--   FROM dmp.events WHERE event_date >= today()-45 GROUP BY one_id) GROUP BY level ORDER BY level;
