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

## 跑

```bash
python3 -m backend.integration.modeling          # 建模引擎自測
python3 -m backend.integration.scheduler         # 排程器自測（模擬時鐘：依賴、重試、暫停、手動）
python3 -m backend.integration.e2e_clickhouse    # 乾跑（不連 ClickHouse）
CLICKHOUSE_BIN=/path/to/clickhouse python3 -m backend.integration.e2e_clickhouse   # 真 ClickHouse（單一執行檔，https://builds.clickhouse.com）
```

## 設計要點

- **正式事件名 = 神策事件名**：模型裡寫 `bond_order`、`bond_deal`；別名（`order_submit`）驗證時接受、落地時轉正式名。
- **身分一律代碼化**：`$identity_login_id` 用 `token_v1(k_idm)`（docs/20 的 `v1_` 客戶代碼），帳號 `fpe(k_acct)`；密鑰只以參照出現，執行時由 KMS 注入（`DMP_KEY_<ref>` 環境變數或 `set_key()`）。
- **個資不落地**：`drop_fields` 的欄位不可映射；整列 `pii_scan` 命中直接拒絕，隔離區只記原因不記內容。
- **三方對帳**：每批 `rows_in → rows_ok → rows_loaded`（以 `batch_id` 回查 ClickHouse），差異寫 `dmp.ingest_recon` 並告警。
- **排序鍵含 event_id**：`dmp.events` 是 ReplacingMergeTree，排序鍵 `(project, event_date, event, one_id, time, event_id)`；e2e 實測少了 `event_id` 時，同客戶同事件同秒的兩筆會在合併時被去重成一筆。
- **Schema 變更**：runner 回報來源新欄位；任務策略 `pause` 會停用任務並告警，`auto_map` 記錄後照跑。

## 還沒做（docs/23 §4.1）

Parquet／Trino／BigQuery 來源讀取器（現在只有 CSV／JSONL）、用戶模型的 upsert 路徑、Airflow DAG 包裝、`/v1/integration/*` 的 FastAPI router。
