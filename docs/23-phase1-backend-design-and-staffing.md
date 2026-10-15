# 第一階段後端代碼設計規劃與分工（v1.0，2026-10-16）

> 依 docs/22 的專案目的（神策 → GA360 遷移、歷史資料銜接、交易結合分析；不做 AI）梳理第一階段**必留功能**，給出後端代碼結構、每個模組的設計要點、六人分工與里程碑交付，最後回答「團隊沒有 ClickHouse 查詢優化與 Flink 狀態管理專家怎麼辦」。本文取代 docs/02 §3 元件清單與 docs/15 §1 分工在第一階段的適用範圍；契約（`backend/openapi.yaml`、`semantic/*.yaml`、`security/*.yaml`）不變。

## 0. 結論先講

1. **第一階段不上 Flink、不以 Kafka 為主幹。** 本案所有來源都是批次或微批（神策歷史一次性、GA360 BigQuery 串流匯出分鐘級、數據中台事實表每 15 分鐘、MA 事件低量），沒有任何需求要求秒級。全部改成「檔案／表 → 暫存表 → ClickHouse SQL 標準化」的微批管線，用 Python + ClickHouse SQL + dbt 完成，Flink 的狀態管理問題在第一階段**不存在**。即時觸發、即時標籤留給第二階段，屆時再上 Flink，且只做 CEP 一個 job。
2. **ClickHouse 的難度用「縮小面積」解決。** 一張事件表、固定排序鍵、五個 SQL 樣板（事件、漏斗、留存、路徑、歸因）、三層物化視圖、一套查詢守門（掃描上限、逾時、併發）。優化工作集中在五個樣板上，每個樣板有 golden 答案集與效能基準，第 4 週起每週跑 1 億筆基準，問題在小資料量就暴露。再買 20 天外部 ClickHouse 顧問做三次設計審查（schema、樣板、容量），不是請人來寫程式。
3. **語言只留 Python（API、編譯器、匯入、排程）、SQL（ClickHouse、dbt）、TypeScript（前端）。** Go Collector 與 Java Flink job 第一階段不寫；`/v1/events` 直送走 FastAPI，量級（MA 每日數百萬）足夠。
4. **六人分工不變，但「AI 層」那個人改做「營運與遷移服務」**（分群／標籤、推送、遷移、元數據、權限）。每個人擁有自己的目錄，契約先行，小 PR 每日合併，規則同 docs/15 §2。
5. 第一階段後端代碼量估計 2.4 萬行（含測試），其中約六成由 AI 產生、人審；人負責 schema、SQL 樣板、權限、口徑與「正確答案」。

## 1. 必留功能清單

依專案目的分三級：**必留**（四件事直接需要）、**簡化**（要有但做最小版本）、**延後**（第二階段）。

| 模組 | 必留 | 簡化 | 延後 |
|---|---|---|---|
| 接入 | 神策歷史匯入（Impala／HDFS／API）、GA360 BigQuery 日批 + 串流匯出、數據中台交易／帳務批次、`/v1/events` 直送（MA、LINE、短鏈）、橋接轉名、去重、隔離區、三方對帳 | CRM 主檔以日批全量取代 CDC | Kafka 流任務、Debezium CDC、Go Collector |
| OneID | id_map、綁定（登入 ID、帳號、裝置）、登入後 7 天改寫（微批）、人工合併／拆分（資訊管理發起、系統管理員審批）、DSAR 刪除 | 改寫以 `oneid_backfill` 覆蓋表 + 每 10 分鐘 lightweight update | 即時歸戶 |
| 語意層與編譯器 | events／metrics／dimensions／user_properties YAML、同一個 `compile()`、成本估算、Policy 注入、血緣 | 虛擬屬性只支援欄位運算與 CASE | 虛擬事件以外的進階表達式 |
| 分析模組 | 事件分析（含公式指標、對比、分組、合計）、漏斗（窗口、每步篩選、分組、趨勢）、留存（四模型）、路徑（會話口徑、抽樣）、歸因（五模型）、用戶列表（Bitmap 估數、分頁、下鑽、存為分群） | 路徑全量查詢需確認；歸因觸點表日批 | 間隔分析、分布分析、LTV 進階 |
| 自定義 SQL | 表目錄依權限、唯讀 SELECT／WITH、遮罩欄位預校驗、1,000 列預覽、下載審批、歷史與已存查詢 | 中間表只支援「存為表」不支援排程更新 | 可視化進階、排程中間表 |
| 分群與標籤 | 規則分群（屬性／事件／分群／行為序列）、匯入分群（神策快照）、結果存檔、交／聯／差、估數、例行更新（日／週／月）、版本、回收站；標籤：自訂、指標、首末次、偏好、RFM、SQL、匯入，版本 1–100，審批 | 函數型規則、生命週期標籤第一版只做模板 | 即時標籤、模型標籤 |
| 名單推送 | Webhook（簽章、批次、重試、DLQ）、GCS 檔案、推送單審批、交付紀錄 | Kafka 目的地只在數據中台有 Kafka 時啟用 | 廣告平台（本就不做） |
| 即時觸發 | — | 以「每 15 分鐘微批規則」取代：規則 → 分群增量 → 推送 | Flink CEP 秒級觸發 |
| 數據治理 | 元事件管理（字典、上線檢查、隔離區、變更單）、用戶表、維度表與字典、**歷史資料遷移**（盤點、橋接表、導出任務、對帳）、資料流與效能頁 | 血緣只做表級 | OpenLineage 欄位級血緣 |
| 系統管理 | 五角色 + 自訂角色、指派、模組矩陣、數據權限、欄位隱碼、審批、審計看板、平台設定 | 臨時提權走審批單，不做自動到期以外的流程 | — |
| AI | — | — | 全部（契約保留） |

判斷原則：能不能用「四件事」之一說明它為什麼存在；不能的就延後。

## 2. 第一階段架構（微批版）

```
來源                         暫存（GCS／機房 SeaweedFS）        ClickHouse                               服務
神策 Impala／HDFS ─ distcp ─► stage/sensors/dt=…/*.parquet ─┐
GA360 BigQuery ── Storage Read API ─► stage/ga360/…      ├─► dmp.raw_events（S3 引擎外部表 / INSERT SELECT）
數據中台 Trino ─── 增量查詢 ─► stage/dwh/…               │     │  normalize.sql（橋接轉名、時間、去重、HMAC、OneID dictGet）
MA／LINE／短鏈 ── POST /v1/events ─► dmp.raw_events ────┘     ▼
                                                          dmp.events（月分區，排序 project/event_date/event/one_id/time）
                                                            ├─ MV L1 日彙總 ─ L2 常用分組 ─ L3 cohort
                                                            ├─ dmp.id_map ─ dmp.oneid_backfill（微批改寫）
                                                            ├─ dmp.users ─ dim_*（dictionary）
                                                            └─ dmp.user_tags（Bitmap）─ segments
Airflow（LocalExecutor）：匯入 DAG（每 5／15 分鐘、每日）、dbt（標籤、觸點表、對帳、MV 補算）、OneID 改寫、推送
FastAPI dmp-api：analytics / users / segments / tags / sql / migration / meta / iam / ops / events 直送
PostgreSQL：控制面（定義、任務、審批、審計索引、分群定義、已存查詢）   Valkey：查詢快取、估數快取
```

與 docs/02 的差異只有一處：計算層的 Flink 換成「ClickHouse SQL + dbt + Airflow 微批」；Kafka 降為選配（數據中台若提供 topic，第一階段仍以批次讀）。延遲口徑：GA 串流匯出 P95 < 15 分鐘（受 GA 限制，本來就不是秒級）、數據中台 < 20 分鐘、`/v1/events` 直送 < 5 分鐘（微批間隔）。這些都符合 docs/17 §3 的目標。

## 3. 代碼結構（第一階段子集）

```
backend/
  api/            FastAPI 應用：routers（analytics, users, profile, segments, tags, sql, migration, meta, iam, ops, events）、
                  deps（auth, policy, budget）、schemas（由 openapi.yaml 生成）
  semantic/       *.yaml（契約）＋ compiler/：parser（語意查詢 → AST）、planner（選 MV 或 L0）、templates/（5 個 SQL 樣板）、
                  policy_inject、cost、lineage；tests/golden/
  security/       rbac.yaml、masking.yaml、policy.py（authorize、row filter、mask plan）
  ingest/         importers/（sensors_export, ga360_bq, dwh_trino, direct）、stage（寫 Parquet 到暫存）、
                  loader（raw_events → events 的 normalize SQL）、dedupe、quarantine、recon
  oneid/          attribution.py（已有）、bind_service、backfill_job（微批）、merge_split（人工）、dsar
  segments/       rule_engine（規則 → SQL）、estimator（Bitmap）、scheduler、versions、import、recycle
  tags/           kinds/（custom, metric, firstlast, preference, rfm, sql, import）、bitmap_builder、approval
  activation/     destinations.yaml、webhook.py（已有）、push_job、deliveries
  migration/      bridge.py（已有）、inventory、export_jobs、recon、ga_spec_export
  governance/     meta_events、user_props、dim_tables、change_requests、audit_query
  clickhouse/     schema.sql、mv/*.sql、dict/*.xml、settings/profiles.xml（查詢守門）
  jobs/
    dbt/          models/（tags, touchpoints, recon, mv_backfill, segments_routine）、tests/
    airflow/      dags/（ingest_ga360, ingest_dwh, ingest_sensors, oneid_backfill, dbt_daily, push）
  tests/          unit/、contract/（schemathesis）、policy/（五角色 × 每個 API）、perf/（k6 + 固定查詢集）
tools/synth/      合成資料產生器（1 千萬 → 1 億 → 10 億）
```

跨模組依賴只允許：`api → semantic/security/segments/tags/...`；`segments/tags/sql → semantic.compiler`；`ingest/oneid → clickhouse`。CI 用 import-linter 擋。

## 4. 模組設計要點

每個模組列：職責、關鍵介面、資料、測試門檻、估計規模（人日，含 AI 協作）。

### 4.1 ingest（接入與載入）— 28 人日

- 職責：把四種來源落成 `dmp.raw_events` 統一格式（`source, event_name_raw, time_ms, distinct_id, login_id_hmac, props JSON, ingest_batch`），再以 `normalize.sql` 寫入 `dmp.events`。
- 關鍵介面：`Importer.run(window) -> Batch`、`Loader.apply(batch) -> ReconRow`；Airflow DAG 每來源一條。
- 標準化全部在 ClickHouse SQL：橋接轉名用 `dictGet('bridge', ...)`（`sensors_ga4_bridge.yaml` 匯成字典）、別名表、時間轉 UTC、`event_id` 去重（`ReplacingMergeTree` + 日批覆蓋串流）、OneID `dictGet('id_map', ...)`、HMAC 在匯入端（Python，金鑰從 KMS／HSM）。
- 對不上的事件名進 `dmp.quarantine` 並登記元事件待辦。
- 測試：每來源一組 fixture（含遲到、重送、未知事件名、拒絕同意）；三方對帳表每批自動寫；差異 > 0.1% 的日分片自動重跑。
- 效能目標：神策歷史 70 億筆以 `INSERT SELECT FROM s3()` 載入，單節點 ≥ 100 萬列/秒。

### 4.2 oneid — 12 人日

- 已有 `attribution.py` 的規則與自測；補 `bind_service`（REST + 批次）、`backfill_job`（每 10 分鐘：查 `oneid_backfill` 待套用 → `ALTER TABLE UPDATE` 限近 8 個日分區 → 標記完成）、`merge_split`（審批單 → 重算 id_map → 審計）、DSAR。
- 測試：docs/19 的 10 個情境 + 改寫的分區邊界（跨月、重複登入、同裝置多帳號）。

### 4.3 semantic.compiler（編譯器）— 35 人日，最關鍵

- 輸入：語意查詢（metric、filters 巢狀且／或、group_by、time、compare、segment）；輸出：SQL、估算 bytes、血緣、命中的 MV 層級。
- 五個樣板（人寫、AI 補變體）：
  - 事件：`events_daily_agg`（L1）優先；分組 ≤ 3 維走 L2；否則 L0 加 `SAMPLE`。
  - 漏斗：`windowFunnel(window)(time, cond1, cond2...)` + 每步篩選；用戶數／次數口徑；分組以 `arrayJoin`。
  - 留存：`retention()`；連續留存用 `arrayReduce` 版本；cohort 走 L3。
  - 路徑：按 `one_id, session_id` 排序的 `groupArray` → 截斷步數 → 展開；預設 `SAMPLE 1/10`。
  - 歸因：觸點表（dbt 日批）× 目標事件；五模型用同一掃描不同權重欄。
- Policy 注入：行級 WHERE、欄位遮罩（`mask_plan`）、K 匿名（分組人數 < 20 → 抑制）、預算（`max_bytes_to_read`、`max_execution_time`、`max_result_rows`）。
- 測試門檻：編譯器單元 ≥ 90%；golden 答案集（固定 1 千萬筆合成資料 × 40 條查詢）誤差 0；每條 SQL `EXPLAIN ESTIMATE` 掃描量不得超過預期 1.5 倍（守住效能回歸）。

### 4.4 api（FastAPI）— 20 人日

- 由 `openapi.yaml` 生成 schema；每個 router 只做：驗證 → Policy → 呼叫服務 → 審計。不在 router 寫 SQL。
- 查詢結果快取 Valkey 5 分鐘（key = 編譯後 SQL hash + 身分）。
- 契約測試 schemathesis；權限測試五角色 × 每個端點，期望值由 `rbac.yaml` 自動展開。

### 4.5 segments／tags — 26 人日

- 規則 → 編譯器的 filter AST → Bitmap SQL（`groupBitmapState(one_id_uint)`）；估數走 1/16 抽樣；交／聯／差在 Bitmap 上做。
- 例行更新由 Airflow 觸發 dbt；版本表保留 N 版；回收站為軟刪除 + 30 天清除。
- 神策分群／標籤匯入：成員名單（login_id → HMAC → one_id）寫入 `import` 型；規則能對應的重建為規則型並比對成員差異 ≤ 1%。
- 標籤七種：每種是一個 dbt macro 模板 + 參數；SQL 型走自定義 SQL 的同一套唯讀檢查。

### 4.6 sql（自定義 SQL）— 8 人日

- 已在 docs/21 定義：SQLGlot 解析 → 只允許 SELECT／WITH → 表目錄白名單 → 遮罩欄位靜態檢查 → 以 `max_result_rows=1000` 執行 → 下載走審批。

### 4.7 activation — 8 人日

- `webhook.py` 已有簽章／批次／重試；補 `push_job`（分群版本 → 分批 → 目的地 → deliveries）、GCS 檔案輸出。

### 4.8 migration — 14 人日

- `inventory`（手動登記 + Impala 統計）、`export_jobs`（分片狀態機：待執行／導出中／校驗／載入／對帳／完成／待人工）、`recon`（T2、T5、T6 三張對帳表）、`ga_spec_export`（CSV）。
- 這個模組是第一階段最早要能跑的：M1 盤點、M2 歷史載入都靠它。

### 4.9 governance／iam／ops — 22 人日

- governance：元事件 CRUD + 上線檢查 + 變更單；維度表 upsert + dictionary 重載；用戶表屬性登記。
- iam：`policy.py` 已有 authorize；補角色 CRUD（admin 可增刪、改派）、指派、審批流（單一表 `approvals`，類型欄區分）、審計查詢與看板 SQL。
- ops：Prometheus 指標（匯入延遲、對帳差異、查詢 P95、批次時長）、`/v1/ops/*` 讀 ClickHouse `system.*` 與 Airflow。

### 4.10 clickhouse（schema 與守門）— 10 人日（人寫為主）

- `schema.sql` 已有；補 MV L1–L3 DDL、dictionary（bridge、id_map、dim_*）、`settings/profiles.xml`：`human`（max_bytes_to_read 50 GB、max_execution_time 30 s、max_concurrent_queries_for_user 4）、`batch`（寬鬆、低優先）、`sql_user`（10 GB、10 s）。
- 分區與排序鍵在 M1 前定稿，之後不改；改要走設計審查。

## 5. 分工（六人，每人一條工作流）

| 工作流 | 人 | 擁有目錄 | M1（W3，11/20） | M2（W7，12/18） | M3（W11，1/22） | M4（W15，2/26） | M5（W19，3/26）→ UAT 6/30 → PD 9/30 |
|---|---|---|---|---|---|---|---|
| **TL／架構** | 1 | `openapi.yaml`、`semantic/*.yaml`、`clickhouse/schema.sql`、`AGENTS.md`、CI | 契約凍結、schema／分區／排序鍵定稿、設計審查 1 | 編譯器樣板 review、golden 口徑定案 | 設計審查 2（效能） | 切換演練 | 設計審查 3（容量）、驗收 |
| **資料管線** | 1 | `ingest/`、`oneid/`、`jobs/airflow`、`jobs/dbt`（匯入與對帳部分） | 神策盤點、`sensors_export` 匯入器、合成資料走通 | 歷史 70 億載入測試環境、三方對帳、OneID 綁定與改寫 | GA360 匯入（日批 + 串流）、橋接轉名、並行對帳 | 數據中台交易匯入、狀態機事件化、最終增量 | 10 億基準、冷層 |
| **後端查詢** | 1 | `semantic/compiler/`、`api/`（analytics、users、sql）、`security/` | 編譯器骨架 + 事件樣板 | 漏斗、留存樣板；事件分析接真資料；Policy 注入 | 路徑、歸因、用戶列表；自定義 SQL；五角色權限測試 | 結合分析五案例的查詢驗證 | 效能回歸、P95 收斂 |
| **營運與遷移服務** | 1 | `segments/`、`tags/`、`activation/`、`migration/`、`governance/`、`api/`（對應 router） | `migration` 盤點與導出任務狀態機、橋接表 API | 元事件／維度表 API、審批流 | 分群規則引擎、估數、版本、回收站 | 標籤七種、神策分群／標籤匯入、推送 | 回收與清理、文件 |
| **前端** | 1 | `prototype/` → `web/` | 工程化（Vite + TS）、登入、總覽、遷移頁接 API | 事件分析頁接 API、治理頁 | 漏斗／留存／路徑／歸因／用戶列表 | 分群與標籤、自定義 SQL、推送 | 打磨、H5 |
| **平台／QA** | 1 | `deploy/`、`.github/`、`tests/`、`tools/synth/` | 測試環境、映像、CI、合成資料 1 千萬 | golden 答案集、契約測試、1 億基準 | e2e、權限回歸、並行對帳自動化 | 效能每 Sprint 回歸 | 機房驗證、DR、驗收報告 |

- 日期以 kick-off 2026-11-02 假設；M5 後到 UAT（2027-06-30）有 3 個月緩衝，到 PD（2027-09-30）再 3 個月修正與正式環境預載（docs/22 §6）。
- 人日合計約 183（後端 165 + 平台／QA 測試 18），六人 × 18 週 ≈ 540 人日，留有餘裕給遷移中必然出現的資料問題。
- 外部：ClickHouse 顧問 20 天（分三次審查 + 隨叫問答），不佔工作流。

## 6. 沒有 ClickHouse／Flink 專家怎麼辦

### 6.1 先把需要專家的地方拿掉

| 原本需要專家 | 第一階段作法 | 結果 |
|---|---|---|
| Flink 狀態管理（OneID 狀態、session 視窗、exactly-once、checkpoint 調校、反壓） | 不上 Flink。全部微批：OneID 用 `id_map` 字典 + 覆蓋表；session 在 dbt 按日重算；去重靠 `ReplacingMergeTree` + 日批覆蓋 | 這一類問題整個消失；代價是延遲從秒級變 5–15 分鐘，本案沒有秒級需求 |
| Kafka 叢集維運 | 降為選配，第一階段不部署 | 少一套有狀態系統 |
| ClickHouse 分散式 JOIN、分片、Distributed 表 | 單分片 2 副本（docs/13 A 檔本來就是），維度表用 dictionary，不做分散式 JOIN | 避開最難的一塊 |
| ClickHouse mutation 效能 | 只在 OneID 改寫用 `ALTER UPDATE`，且限近 8 個日分區、每 10 分鐘一批 | 可控 |
| 任意 SQL 的效能 | 分析查詢只走五個樣板；自定義 SQL 有獨立 profile（10 GB／10 s） | 優化對象從無限縮到五個 |

### 6.2 把剩下的 ClickHouse 工作變成「可學、可測、可審」

1. **設計一次定稿，之後不碰**：排序鍵 `(project, event_date, event, one_id, time)`、月分區、高頻屬性物化欄位、三層 MV。第 2 週用 1 千萬筆合成資料驗證五個樣板的掃描量與耗時，第 3 週請顧問做第一次審查。之後改 schema 要走設計審查。
2. **五個樣板人寫，AI 寫變體**：每個樣板附「為什麼這樣寫」的註解、`EXPLAIN` 期望、golden 結果。AI 產生的每一條 SQL 都要通過 golden 答案集與 `EXPLAIN ESTIMATE` 掃描量門檻（≤ 樣板 1.5 倍），不通過 CI 直接擋。
3. **效能基準提早、每週跑**：第 4 週起 1 億筆、固定 40 條查詢、P95 對 SLA；超標當週處理，不累積到 10 億才發現。
4. **查詢守門在伺服器端**：settings profile 限掃描量、逾時、併發；工作負載隔離（human／batch／sql_user）。即使寫出爛查詢，也只是該查詢逾時，不拖垮系統。
5. **學習路徑具體化（兩週）**：資料管線與後端查詢兩人各花 10 個工作日的 20%：ClickHouse 官方文件的 MergeTree、分區與排序鍵、MV、dictionary、`windowFunnel`／`retention`／`sequenceMatch`、settings 與 profiles；每天用合成資料做一個小實驗寫進 `docs/notes/`。目標不是變專家，是能看懂 `EXPLAIN` 與 `system.query_log`。
6. **外部顧問用在審查，不用在寫碼**：20 天分三次（schema 與樣板、效能、容量與維運）加隨叫問答。ClickHouse 官方或 Altinity 都有這種服務；合約以「審查報告 + 問答時數」計。
7. **維運面用 Operator 與 runbook**：ClickHouse Operator 管副本與 Keeper；備份用 `clickhouse-backup` 到物件儲存；runbook 寫清楚磁碟滿、副本延遲、mutation 卡住、分區合併慢四種情況的處理步驟，第 6 週前演練一次。
8. **開發環境可用 ClickHouse Cloud 跑合成資料**（只放合成資料，不放客戶資料），省掉團隊初期自己架副本的時間；正式與測試環境仍私有化。

### 6.3 第二階段才需要 Flink 時

- 只做一個 job：CEP 規則引擎（即時觸發），用 Flink SQL `MATCH_RECOGNIZE`，不寫 Java。
- 狀態只有規則視窗，不做 OneID 狀態（OneID 仍查字典）。
- 屆時團隊已經有 ClickHouse 經驗與資料口徑，Flink 的學習面小很多；可再用同樣的「顧問審查 + 基準 + 守門」方法。

### 6.4 誠實的代價

- 延遲：秒級變分鐘級。對本案沒有影響；若客戶之後要秒級觸發，第二階段補。
- 架構文件：docs/02 的計算層描述要加「第一階段微批」註記（本文即為該註記）。
- 兩個人要在前兩週擠出時間學 ClickHouse；如果連這個都擠不出來，風險回到人力，不是技術。

## 7. 驗收門檻（每模組的「做完」定義）

| 模組 | 做完的定義 |
|---|---|
| ingest | 四種來源 fixture 全過；三方對帳自動寫；70 億歷史載入測試環境且差異 < 0.1% |
| oneid | 10 情境自測 + 分區邊界測試；改寫延遲 < 10 分鐘 |
| compiler | 五樣板 golden 誤差 0；單元 ≥ 90%；掃描量門檻全過 |
| api | schemathesis 0 違反；五角色 × 端點 0 越權 |
| segments／tags | 神策匯入分群成員一致；規則分群與神策差異 ≤ 1%；500 個日批 < 20 分鐘 |
| migration | T1–T9 全部有自動化產出；切換演練一次 |
| perf | 1 億（M2）、10 億（M5）固定查詢集 P95 符合 docs/04 SLA |
