# backend/integration — 數據整合：可視化建模 + 排程（第一階段微批版）

對應 docs/06 §7（數據源 → 數據表 → 同步任務）、docs/23 §4.1；API 契約 `/v1/integration/*`。純 Python 標準函式庫 + PyYAML，ClickHouse 走 HTTP。

| 檔案 | 內容 |
|---|---|
| `connectors.yaml`、`auth.py` | 數據源類型、鑑權方式、密鑰參照、最小權限、連線診斷（既有） |
| `transforms.py` | 欄位轉換純函式（既有）|
| `models.example.yaml`、`models.instrument.yaml` | 建模精靈七步的產物：事件模型（數據中台委託事實表 → bond_order／bond_deal／…）、維度表模型（商品主檔 → dim_instrument） |
| `modeling.py` | 建模引擎：`load / validate / preview / apply / to_ch_row / compile_sql`。驗證對語意層（事件名含別名、L4 禁止、身分必須代碼化、轉換函式）；套用 = 欄位映射（含 `map`、`expr`、`when`）＋清洗（required／enum／range／time_window／pii_scan）；`compile_sql` 產 ClickHouse `INSERT … SELECT`（密鑰轉換留在暫存階段） |
| `scheduler.py` | 排程器：cron／間隔、依賴（上游同窗口成功才跑）、重試退避、增量水位、Schema 變更策略（auto_map／ignore／pause）、執行紀錄、告警；狀態在 SQLite，時鐘可注入 |
| `runner.py` | `LocalRunner`：檔案來源 → 模型 → ClickHouse（JSONEachRow）→ 隔離區 `dmp.quarantine` → 對帳 `dmp.ingest_recon`；`local_ddl()` 把 schema.sql 轉單機版 |
| `e2e_clickhouse.py` | 端到端：啟動單機 ClickHouse、建表、合成 2 萬筆委託 + 300 筆商品、排程器跑三個增量窗口、驗證筆數／隔離／對帳／水位／依賴／關聯屬性 JOIN／漏斗樣板，再以 `compile_sql` 路徑比對 event_id |
| `cli.py` | 命令列：validate／preview／compile；sched add／tick／status／runs／run／enable／disable |
| `legacy_format.py` | 舊平台資料格式檔（FormatImporter 的輸入）→ DMP：Python 串流預處理（登入 ID 代碼化、去 `$ip`／L4、壞行隔離）→ ClickHouse `file()` 讀入 `raw_legacy` → track → events（別名轉正式名）、profile_* → user_prop_log、track_signup → id_map；對帳 |
| `ga4_source.py` | GA360 BigQuery 匯出 → events：三種表（intraday／fresh／daily）、三個任務（串流每 15 分鐘含 30 分鐘重疊窗口、日批到齊才跑、D-2／D-3 列數變了才重載）、GA4 schema 攤平（event_params、device／geo／app_info／流量來源、同意）、橋接表轉事件名、event_id 去重鍵；`BigQueryClient` 正式用、`FakeBigQuery` 測試用 |
| `runner.read_dbapi` | DB-API 2.0 來源（MSSQL 用 pyodbc／pymssql、PostgreSQL 用 psycopg）：增量 `WHERE inc > 水位 ORDER BY inc`、fetchmany 串流；connect 工廠由部署注入 |

## 跑

```bash
python3 -m backend.integration.modeling          # 建模引擎自測
python3 -m backend.integration.scheduler         # 排程器自測（模擬時鐘：依賴、重試、暫停、手動）
python3 -m backend.integration.e2e_clickhouse    # 乾跑（不連 ClickHouse）
CLICKHOUSE_BIN=/path/to/clickhouse python3 -m backend.integration.e2e_clickhouse   # 真 ClickHouse（單一執行檔，https://builds.clickhouse.com）
CLICKHOUSE_URL=http://127.0.0.1:8123 CLICKHOUSE_USER=e2e CLICKHOUSE_PASSWORD=... python3 -m backend.integration.e2e_clickhouse   # 連現成伺服器（CI 用 clickhouse-server 服務容器）
```

## 設計要點

- **正式事件名 = 舊平台事件名**：模型裡寫 `bond_order`、`bond_deal`；別名（`order_submit`）驗證時接受、落地時轉正式名。
- **身分一律代碼化**：`$identity_login_id` 用 `token_v1(k_idm)`（docs/20 的 `v1_` 客戶代碼），帳號 `fpe(k_acct)`；密鑰只以參照出現，執行時由 KMS 注入（`DMP_KEY_<ref>` 環境變數或 `set_key()`）。
- **個資不落地**：`drop_fields` 的欄位不可映射；整列 `pii_scan` 命中直接拒絕，隔離區只記原因不記內容。
- **三方對帳**：每批 `rows_in → rows_ok → rows_loaded`（以 `batch_id` 回查 ClickHouse），差異寫 `dmp.ingest_recon` 並告警。
- **排序鍵含 event_id**：`dmp.events` 是 ReplacingMergeTree，排序鍵 `(project, event_date, event, distinct_id_uint, time, event_id)` 加 `SAMPLE BY distinct_id_uint`（按裝置分桶、一致抽樣；`one_id` 不進排序鍵，事件表只增刪不改寫，docs/23 §4.1）；e2e 實測少了 `event_id` 時，同裝置同事件同毫秒的兩筆會在合併時被去重成一筆。
- **Schema 變更**：runner 回報來源新欄位；任務策略 `pause` 會停用任務並告警，`auto_map` 記錄後照跑。

## GA360 BigQuery：更新頻率與排程（已在 e2e 驗證）

| GA 匯出表 | 更新頻率（GA 口徑） | 我們的任務 | 排程 |
|---|---|---|---|
| `events_intraday_YYYYMMDD` 串流 | 事件後數分鐘持續追加；無遲到回補；日批到齊後 GA 刪掉它 | `ga360_intraday` | 每 15 分鐘；讀 `event_timestamp > 水位 − 30 分鐘`（吃亂序），重讀列靠 event_id 去重 |
| `events_YYYYMMDD` 日批 | 隔日數小時內到齊，GA 只保證 72 小時；72 小時內可能整表重寫 | `ga360_daily` | 每日 06:30 起輪詢，表到齊才整天重載；ingest_time 較新 → 覆蓋串流版本 |
| 同上（D-2、D-3） | 遲到事件造成重寫 | `ga360_restate` | 每日 07:00 比對列數與上次，變了才重載 |
| `events_fresh_YYYYMMDD`（360 Fresh daily） | 當天表一天內多次更新，欄位完整 | 取代 `ga360_intraday` | 每小時；設 `table_kind: fresh` 即可 |

e2e（假 BigQuery 餵 GA4 匯出 schema）：串流三個窗口讀入 119,595 列（含重疊重讀）→ 去重後 60,000 = 串流表筆數；日批到齊前記 skipped；到齊後 60,300 列全部覆蓋串流版本（FINAL 後串流來源 0 筆）；D-2 重寫 +120 → 重載，列數未變不重載；FINAL 後無重複 event_id。
延遲口徑：串流路徑 P95 ≈ GA 串流延遲（數分鐘）+ 15 分鐘窗口；日批完整版 T+1 早上；歸因欄位以日批為準。

## 交易資料的兩條導入路（都已在 e2e 驗證）

**A. 舊平台格式檔（原本餵 FormatImporter 的每日 3.8 GB）**

```python
from backend.integration.legacy_format import LegacyLoader
loader = LegacyLoader(ch)                                   # 建 raw_legacy
r = loader.run_file(Path('/stage/sa_20261016.jsonl'), user_files_dir, batch_id='sa_20261016', key_ref='k_idm')
# r['recon_ok']、r['stage']（行數／通過／壞行／代碼化數／各型別）、r['raw']、r['events']、r['prop_log']、r['id_map']
```
實測 20 萬行／48 MB：10.6 s（預處理佔大半；ClickHouse 載入與標準化約 1 s）→ 3.8 GB 約 14 分鐘單核，分檔平行可壓到 5 分鐘內。壞行 1% 進隔離檔、登入 ID 100% 代碼化、`$ip` 與 L4 欄位 0 筆入倉、別名 0 筆殘留。

**B. MSSQL（或數據中台任何 DB-API 來源）排程抽取 + 清洗**

```python
import pyodbc                                                 # MIT；正式環境由 deploy 安裝，連線字串與密鑰由 auth.py 解析 secret_ref
runner = LocalRunner(ch, db_connections={'dwh_trade': lambda: pyodbc.connect(conn_str)})
s = Scheduler('/var/dmp/sched.db', runner)
s.add_task('sync_orders', 'models.example.yaml', cron='*/15 * * * *', mode='incremental', retries=3, backoff_s=60, schema_change='auto_map',
           source={'kind': 'dbapi', 'connection': 'dwh_trade', 'table': 'dbo.fact_order_hist', 'incremental_field': 'UPDATED_AT', 'paramstyle': '?'})
```
e2e 以 sqlite 當 MSSQL 替身（同為 DB-API）：兩個 15 分鐘窗口各抽 12,000／8,000 列，水位接續、三方對帳一致；模型即 `models.example.yaml`（狀態機事件化、代碼化、清洗、隔離）。MSSQL 鑑權順序與最小權限見 `connectors.yaml`（mssql）。

## 用戶表規模測試（`e2e_clickhouse.py` 的 users bench 段）

`E2E_USERS`（預設 200,000）× `E2E_PROPS`（300）個屬性的寬表、`E2E_EVENTS`（1,000,000）筆事件。流程：未登記屬性 `ALTER ADD COLUMN` → 兩天的寬表 Parquet → `users_snapshot` → `users` 現況 → FINAL 篩選／冷欄位分組／屬性歷史 → 5% 用戶 profile_set 微批合併 → dictionary 載入 → 事件 dictGet 分組、dictGet 篩選的漏斗、冷欄位 JOIN → SAMPLE 1/10 按用戶一致性 → 磁碟與記憶體。

實測數字（本機 4 核 15 GB，ClickHouse 26.10 單機）見下表（由 `E2E_USERS=2000000 E2E_PROPS=300 E2E_EVENTS=10000000` 跑出）：

| 項目 | 實測 | 說明 |
|---|---|---|
| 未登記屬性 ALTER ADD COLUMN（285 欄） | 0.05 s | metadata-only，新增屬性無痛 |
| 日批快照載入（200 萬列 × 300 欄，Parquet 1.2 GB → users_snapshot） | 161–169 s | 約 1.2 萬列/秒；正式 6 台可平行分片載入 |
| 現況表覆蓋（users_snapshot → users，200 萬列） | 70 s | INSERT 不 UPDATE |
| users FINAL 篩選（aum_bucket + is_first_trade） | 0.34 s | 熱欄位 |
| users FINAL 冷欄位分組（p120_enum） | 0.32 s | 不在 dictionary 的屬性也秒內 |
| 屬性歷史查詢（兩天快照按 aum_bucket 分組） | 0.06 s | 舊平台沒有的能力 |
| 即時微批合併（10 萬用戶 profile_set → users，300 欄） | 11.9 s | 每 10 分鐘一批綽綽有餘 |
| dictionary 載入（15 個熱欄位 × 200 萬） | 16.8 s · 1.34 GB | 熱欄位 ≤ 50 時約 4 GB，正式機 32 GB 可承受；超過改 JOIN |
| 事件 dictGet 分組（1,000 萬事件 × 用戶 aum_bucket） | 0.44 s | 等同舊平台查詢時 JOIN Kudu |
| dictGet 篩選的漏斗（7 天窗口，三步） | 0.47 s | |
| 事件 JOIN users FINAL 冷欄位 | 0.53 s | |
| SAMPLE 1/10 一致性 | 比例 0.100，抽到的用戶事件 100% 在樣本內 | 按用戶分桶成立 |
| 路徑型查詢（SAMPLE 1/10，groupArray by user） | 0.16 s | |
| 磁碟：events 1,000 萬筆 | 429 MB | 約 43 B/筆（合成資料；真資料屬性更多） |
| 磁碟：users 兩天快照 + 現況（600 萬列 × 300 欄） | 4.26 GB | 每日快照約 1.4 GB；90 天約 130 GB，建議快照只留 30 天或只留熱欄位歷史 |
| 伺服器常駐記憶體 | 2.3 GB | 含 dictionary |

結論：300 個屬性每天整批覆蓋對 ClickHouse 不是問題；代價是磁碟（屬性歷史）與 dictionary 記憶體，兩者都可用「熱欄位清單」與「歷史保留天數」調整，不需要第二套存儲。

## 還沒做（docs/23 §4.1）

Parquet／Trino／BigQuery 來源讀取器（現在只有 CSV／JSONL）、用戶模型的 upsert 路徑、Airflow DAG 包裝、`/v1/integration/*` 的 FastAPI router。


## 舊平台查詢 API 直連（legacy_api.py）

`python3 -m backend.integration.legacy_api` 自測（假舊平台伺服器）；有金鑰時：

```bash
export LEGACY_API_SECRET='…'   # 只放環境變數
python3 -m backend.integration.legacy_api test      --base-url https://sn3.data-di.com --project taipei_uat            # dns → tcp → tls → auth（自動試 query_token／header）→ privilege → sample_read → clock_skew
python3 -m backend.integration.legacy_api inventory --base-url … --project … --from 2026-10-01 --to 2026-10-07     # 每事件每日筆數（docs/22 盤點）
python3 -m backend.integration.legacy_api columns   --base-url … --project …                                        # events 欄位（屬性攤平）
python3 -m backend.integration.legacy_api export    --base-url … --project … --date 2026-10-01 --out sa_2026-10-01.jsonl   # 按日分頁導出成 FormatImporter 格式
```

導出檔接 `legacy_format.LegacyLoader.run_file`（代碼化、去個資、載入、對帳），與線下格式檔同一條路。沒有 `$is_login_id` 欄位時預設把 distinct_id 視為登入 ID（寧可多代碼化）；`--anonymous-when-missing` 反轉。單機完整流程見 `deploy/quickstart/README.md`。
