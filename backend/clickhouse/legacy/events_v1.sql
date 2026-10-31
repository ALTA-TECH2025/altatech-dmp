-- 升級回歸用的舊版 DDL（#74 之前的 main，單機版）：事件表以 one_id_uint 排序／抽樣、沒有 distinct_id_uint；回補表還有 applied、沒有 *_uint。
-- 只給 migrate_selftest 建「舊庫」用，不要拿來部署；其餘表照現行 schema.sql。
CREATE TABLE IF NOT EXISTS dmp.events
(
    project          LowCardinality(String),
    event_date       Date MATERIALIZED toDate(time, 'Asia/Taipei'),
    time             DateTime64(3, 'UTC'),
    event            LowCardinality(String),
    source           LowCardinality(String),
    one_id           String,
    one_id_uint      UInt64 DEFAULT cityHash64(one_id),
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
    batch_id         LowCardinality(String) DEFAULT ''
)
ENGINE = ReplacingMergeTree(ingest_time)
PARTITION BY toYYYYMM(event_date)
ORDER BY (project, event_date, event, one_id_uint, time, event_id)
SAMPLE BY one_id_uint;

ALTER TABLE dmp.events ADD INDEX IF NOT EXISTS idx_one_id one_id TYPE bloom_filter(0.01) GRANULARITY 4;
ALTER TABLE dmp.events ADD INDEX IF NOT EXISTS idx_event_id event_id TYPE bloom_filter(0.001) GRANULARITY 1;

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
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (distinct_id, from_time);
