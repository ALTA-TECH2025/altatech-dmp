-- 審計日誌與日誌統計物化視圖；row policy 與 masking view 範例
CREATE TABLE IF NOT EXISTS dmp.audit_log
(
    time          DateTime64(3, 'UTC'),
    event_date    Date MATERIALIZED toDate(time, 'Asia/Taipei'),
    category      LowCardinality(String),      -- auth | query | profile | mask | export | change | approval | agent | system
    event         LowCardinality(String),
    user_id       String,
    role          LowCardinality(String),
    org_unit      LowCardinality(String),
    on_behalf_of  String,                      -- agent 委託身份
    module        LowCardinality(String),
    object_id     String,
    reason        String,
    approval_id   String,
    ip_masked     String,
    result        LowCardinality(String),      -- ok | denied | error
    rows          UInt64,
    scan_bytes    UInt64,
    ms            UInt32,
    masked_fields Array(String),
    props         JSON
)
ENGINE = ReplicatedMergeTree('/ch/{shard}/dmp.audit_log', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, category, user_id, time)
TTL event_date + INTERVAL 13 MONTH TO VOLUME 'cold';

-- 日誌統計：小時 × 類別 × 模組 × 使用者
CREATE MATERIALIZED VIEW IF NOT EXISTS dmp.audit_hourly
ENGINE = ReplicatedAggregatingMergeTree('/ch/{shard}/dmp.audit_hourly', '{replica}')
ORDER BY (hour, category, event, module, role, org_unit, user_id)
AS SELECT toStartOfHour(time) AS hour, category, event, module, role, org_unit, user_id,
          countState() AS cnt, sumState(rows) AS rows, sumState(scan_bytes) AS scan_bytes,
          countIfState(result = 'denied') AS denied, quantileState(0.95)(ms) AS p95_ms
FROM dmp.audit_log GROUP BY hour, category, event, module, role, org_unit, user_id;

-- 行級策略範例：營業員只看所屬客戶（由 Policy Engine 以 SETTINGS / 參數化 role 建立）
CREATE ROW POLICY IF NOT EXISTS branch_scope ON dmp.events FOR SELECT
USING entity_id IN (SELECT item_id FROM dmp.items WHERE item_type = 'account' AND props.branch_id IN (SELECT branch FROM dmp.user_scope WHERE user = currentUser()))
TO role_branch_manager, role_sales_agent;

-- 列級遮罩視圖（SQL 沙箱用）
CREATE VIEW IF NOT EXISTS dmp.events_masked AS
SELECT project, event_date, time, event, source,
       concat('oid_•••', substring(one_id, -4)) AS one_id,
       concat('••••', substring(entity_id, -4)) AS entity_id,
       platform, channel, symbol,
       multiIf(amount IS NULL, NULL, amount < 1e5, 0, amount < 5e5, 1e5, amount < 1e6, 5e5, 1e6) AS amount_bucket,
       utm_source, utm_campaign
FROM dmp.events;
GRANT SELECT ON dmp.events_masked TO role_analyst;
REVOKE SELECT ON dmp.events FROM role_analyst;
