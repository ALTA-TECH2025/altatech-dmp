# GCP 容量規劃：3 年 300 億事件、每日 1,000 個標籤、API 同步、50 人同時上線

> 版本 v0.3（2026-10-14）。本文件給出 GCP（asia-east1）的機器數量、規格、記憶體與硬碟，並附推導過程；價格為粗估，請以 GCP Pricing Calculator 報價為準。

## 1. 需求換算

| 項目 | 數值 | 推導 |
|---|---|---|
| 總事件量 | 300 億 / 3 年 | 100 億 / 年 ≈ **2,740 萬 / 日**（平均） |
| 尖峰 | 約 1 億 / 日、**1.5 萬 事件/秒** | 證券事件集中在 4.5 小時交易時段，取平均 ×3 為設計尖峰（開盤前 10 分鐘） |
| 客戶數（假設） | 300 萬 OneID | 證券商規模；若為 1,500 萬請依 §7 放大 |
| 標籤 | 1,000 個，每日全量重算 | 平均每標籤 5 個值 → 5,000 個 Bitmap；版本保留 7 份 |
| API 同步 | 每日把標籤／分群結果同步到 CRM、MA、營業員 App 等 | 每日變動量估 5–10%（1.5–3 億個「客戶 × 標籤」變更） |
| 同時上線 | 50 人 | 高峰每秒 5–10 個分析查詢；ClickHouse 併發上限設 100 |

## 2. 儲存量推導

| 資料 | 計算 | 結果 |
|---|---|---|
| ClickHouse 明細（L0）單副本 | 300 億 × 150 B（ZSTD 壓縮後，含物化欄位） | **4.5 TB** |
| MV（L1 人日聚合、L2 日聚合、L3 觸點）+ 索引 + Projection | L0 × 20% | 0.9 TB |
| 標籤 Bitmap | 5,000 值 × 7 版本 × 0.4 MB（300 萬人稠密 ID） | 14 GB |
| users / items / id_map / audit_log（3 年） | — | 0.3 TB |
| **ClickHouse 單副本合計** | | **≈ 5.7 TB**；2 副本 **≈ 11.4 TB** |
| 分層 | 最近 13 個月（約 110 億事件 ≈ 2.1 TB/副本）放 NVMe／Hyperdisk 熱層；13–36 個月（≈ 3.6 TB/副本）以 ClickHouse S3 disk 放 GCS 仍可直接查詢 | 熱層每副本 ≈ 3 TB（含 MV）；冷層 GCS ≈ 7.2 TB（2 副本可降為 1 副本 + GCS 本身冗餘） |
| Kafka（原文 raw + 標準化，保留 7 天） | 2,740 萬 × 7 天 × 0.8 KB × 3 副本 | ≈ 0.5 TB（尖峰月 1.5 TB） |
| GCS 原始事件（Iceberg raw，3 年） | 300 億 × 0.12 KB（JSON zstd） | 3.6 TB |
| GCS 標準化 Iceberg（雙向映射、重算用） | 300 億 × 0.15 KB | 4.5 TB |
| GCS 備份 + 審計 WORM + Flink checkpoint | — | 2 TB |
| **GCS 合計** | | **≈ 17 TB**（含冷層） |
| PostgreSQL（字典、角色、審批、Insight Store、Knowledge Pack） | — | < 200 GB |
| Valkey | 300 萬 OneID 快取 × 200 B + 即時標籤 + 結果快取 | ≈ 4 GB（配 16 GB） |

寫入負載：平均 320 事件/秒、設計尖峰 1.5 萬/秒 → 單一 ClickHouse 節點即可承受（> 10 萬列/秒），瓶頸不在寫入而在查詢併發與標籤批次。

## 3. 建議配置（GCP asia-east1，正式環境）

### 3.1 標準配置（建議）

| 角色 | 機型 | 數量 | vCPU / RAM（每台） | 磁碟（每台） | 說明 |
|---|---|---|---|---|---|
| **ClickHouse**（2 shard × 2 replica） | **c3-highmem-22** | **4** | 22 vCPU / 176 GB | **Hyperdisk Balanced 4 TB**（15,000 IOPS、1,200 MB/s 佈建） | 熱層 13 個月 ≈ 3 TB/副本，留 25% 餘裕；冷層走 GCS S3 disk。每查詢記憶體上限 12 GB，50 併發可同時跑 12 個重查詢（漏斗／路徑）而不互相擠壓 |
| ClickHouse Keeper | e2-standard-2 | 3 | 2 / 8 GB | pd-balanced 50 GB | 可與 data 池共用節點，獨立 Pod |
| **Kafka**（KRaft, Strimzi） | n2-standard-4 | 3 | 4 / 16 GB | pd-balanced 1 TB | 7 天保留；尖峰 1.5 萬/秒遠低於上限 |
| **Flink** | n2-standard-8 | 2（JM ×1 + TM ×2 Pod，HA 用第二節點） | 8 / 32 GB | pd-balanced 200 GB | 標準化、事件化、OneID、Session、CEP；狀態（OneID 300 萬 + Session 窗口）< 10 GB |
| **Airflow + dbt + Spark 執行器** | n2-standard-8 | 2 | 8 / 32 GB | pd-balanced 200 GB | 1,000 標籤日批由 ClickHouse 計算，這裡只跑排程與 dbt；Spark 僅 BigQuery 匯入時彈性加 1–2 台（Spot） |
| **接入**（Collector Go、Debezium/Kafka Connect、GTM SS 代理） | n2-standard-4 | 2 | 4 / 16 GB | pd-balanced 100 GB | Collector 每 Pod 可到 2 萬 req/s；HPA 2–6 |
| **應用**（API、語意層、分群／標籤、OneID、Tool Gateway、Agent Runtime、Web、**Tag Sync**） | n2-standard-8 | 3 | 8 / 32 GB | pd-balanced 100 GB | 50 人同時上線：API 合計約 24 vCPU 足夠；Agent Runtime 每 run 的 LLM 呼叫在外部 |
| 平台（Keycloak、Prometheus、OTel、Grafana OSS） | n2-standard-4 | 2 | 4 / 16 GB | pd-balanced 200 GB | Prometheus 30 天保留 ≈ 100 GB |
| **Cloud SQL for PostgreSQL 16**（HA） | db-custom-4-16384 | 1（區域 HA） | 4 / 16 GB | SSD 500 GB | 控制面 + Insight Store（pgvector） |
| **Memorystore for Valkey** | — | 1（Standard HA） | 16 GB | — | OneID 快取、即時標籤、查詢結果快取 |
| **Cloud Storage** | Standard → 90 天後 Nearline（冷層桶維持 Standard 以利查詢） | — | — | 17 TB | CMEK、Object Lock（審計） |
| 合計 GKE 節點 | | **18 台**（含 Keeper 3 小台） | ≈ 170 vCPU / 1.1 TB RAM | ≈ 21 TB 區塊磁碟 | |

### 3.2 精簡配置（成本優先，仍滿足需求）

| 變更 | 規格 | 取捨 |
|---|---|---|
| ClickHouse 改 **1 shard × 2 replica** | c3-highmem-44 ×2（44 vCPU / 352 GB），Hyperdisk 8 TB | 總算力相同，少 2 台；漏斗／路徑無分片平行，30 天漏斗 P95 由約 5 秒升到 8–10 秒；擴分片需重分佈 |
| Flink 與 Airflow 共用池 | n2-standard-8 ×3 | 日批與串流同節點，尖峰時互相影響 |
| 平台池併入應用池 | n2-standard-8 ×4 | — |
| 合計 | **12 台**，≈ 150 vCPU / 1.1 TB RAM | 月費約降 20% |

### 3.3 非正式環境
- **SIT**：values-small（單節點 ClickHouse c3-highmem-8、1 Kafka、1 TM）共 4 台 n2-standard-8 等級，資料 1/100 抽樣。
- **UAT／效能驗證**：與正式同拓撲但 ClickHouse 改 c3-highmem-8 ×4，用合成資料 300 億跑基準（WS3，見 docs/09 §7）。

## 4. 每日 1,000 個標籤的運算規劃

| 步驟 | 作法 | 時間（標準配置）|
|---|---|---|
| L1 當日分區 | `user_event_daily` 物化視圖隨寫入即時維護，無需重算 | 0 |
| 統計／偏好標籤（約 600 個） | 依「相同事件 × 時間窗」合併成約 120 條 SQL，每條一次掃描 L1 產出多個標籤；8 路並行 | 15–25 分鐘 |
| 規則標籤（約 300 個） | Bitmap 交併差，毫秒級 | < 3 分鐘 |
| SQL／模型標籤（約 100 個） | 沙箱 SQL（限 20 GB 掃描）與 Spark 寫回 | 10–20 分鐘（並行） |
| Bitmap 版本切換、分布統計、血緣 | 原子切換 version | < 2 分鐘 |
| **合計** | 拓撲排序並行（Airflow） | **約 45 分鐘**，排 02:00–03:00，06:00 前完成同步 |

資源：日批期間 ClickHouse 每節點保留 50% CPU 給線上查詢（`max_threads`、workload 設定）；日批失敗只重跑受影響分支。

## 5. 標籤 API 同步到其他系統

| 模式 | 適用 | 設計 | 吞吐 |
|---|---|---|---|
| **增量 Kafka 推送**（主） | MA、營業員 App、CRM 即時更新 | 日批完成後比對前一版本，只送變動：topic `dmp.tag_updates`（Avro：one_id、customer_code、tag_id、old、new、version、ts），分群進出 `dmp.segment_updates` | 1.5–3 億變更 / 日，Kafka 10 萬訊息/秒 → 30–50 分鐘內送完；下游可依自身速度消費 |
| **批次檔案**（大量） | 數倉、神策、報表 | 全量或增量 Parquet 寫到 GCS `dmp-sync/tags/dt=…`，附 manifest 與 checksum；數倉以 Iceberg 直接讀 | 全量 300 萬 × 1,000 = 30 億格 ≈ 6 GB Parquet，5 分鐘 |
| **REST 拉取**（點查／小批） | 客服、營業員 App 查單一客戶 | `GET /v1/sync/tags?one_id=…`、`GET /v1/profiles/{id}/tags`（Valkey 快取 < 10 ms）；`GET /v1/sync/tags/changes?since=…&page=…`（每頁 10,000） | 每 Pod 2,000 req/s；50 人上線場景遠低於此 |
| **Webhook** | 事件型（觸發） | `dmp.triggers` 既有 | — |

權限與隱碼同樣套用：同步輸出只含代碼化 ID 與標籤值，L2 數值標籤依目標系統的合約決定是否區間化；每次同步寫審計 `sync.delivered`。
Tag Sync 服務：應用池 2 個 Pod（各 2 vCPU / 4 GB），Kafka producer 批次 5,000 筆。

## 6. 50 人同時上線的查詢容量

| 項目 | 設定 |
|---|---|
| ClickHouse | `max_concurrent_queries=100`；人機各自 workload：人 `max_memory_usage=12 GB`、`max_execution_time=60 s`；agent `max_memory_usage=8 GB`、掃描預算 20 GB；排程 `priority` 最低 |
| 典型負載 | 50 人 × 每 10 秒 1 次互動 ≈ 5 查詢/秒；90% 命中 L2／Query Cache（< 1 秒），10% 走 L0（漏斗、路徑、歸因 5–10 秒）→ 同時在跑的重查詢約 5–8 個，遠低於 4 節點 × 22 vCPU 的上限 |
| 快取 | ClickHouse Query Cache 5 分鐘 + Valkey 服務層快取（看板卡片 60 秒） |
| API | 3 台 n2-standard-8 上 API 合計 6 Pod（每 Pod 2 vCPU）；FastAPI 單 Pod 1,000+ req/s |
| AI 問答 | 每問 3–5 次工具呼叫；50 人若同時發問 → 250 次工具查詢在 40 秒內分散，占 ClickHouse 併發 < 10 |

## 7. 若假設改變
| 變化 | 調整 |
|---|---|
| 客戶 1,500 萬 | Bitmap ×5（仍 < 100 GB）；Valkey 32 GB；標籤日批約 90 分鐘 → ClickHouse 改 c3-highmem-44 或加 1 組分片 |
| 事件 3 年 1,000 億 | ClickHouse 4 shard × 2（c3-highmem-44，Hyperdisk 8 TB），見 docs/09 |
| 熱層改 36 個月全放 NVMe | 每副本 5.7 TB → Hyperdisk 8 TB ×4；冷層仍建議 GCS 以省成本 |
| 同時上線 200 人 | 應用池加到 5 台；ClickHouse 不變（查詢併發仍在上限內） |

## 8. 月費粗估（asia-east1，隨需價，未稅，USD）

| 項目 | 標準配置 | 精簡配置 |
|---|---|---|
| ClickHouse 節點（c3-highmem-22 ×4 ≈ $1.5/hr）| ≈ 4,400 | ≈ 4,400（c3-highmem-44 ×2） |
| Hyperdisk Balanced 16 TB + 佈建 IOPS | ≈ 1,800 | ≈ 1,800 |
| 其他 GKE 節點（n2-standard-8 ×7、n2-standard-4 ×7、e2 ×3）| ≈ 2,900 | ≈ 2,000 |
| 區塊磁碟（Kafka 3 TB + 其他 1.5 TB） | ≈ 500 | ≈ 450 |
| Cloud SQL HA 4 vCPU/16 GB + 500 GB | ≈ 450 | ≈ 450 |
| Memorystore Valkey 16 GB | ≈ 150 | ≈ 150 |
| GCS 17 TB（含冷層、Nearline 分層） | ≈ 300 | ≈ 300 |
| 網路、Cloud Armor、KMS、Logging | ≈ 400 | ≈ 400 |
| **合計** | **≈ 10,900 / 月**（≈ 35 萬 TWD） | **≈ 9,950 / 月** |
| 1 年 CUD（運算 −37%） | ≈ 8,200 / 月 | ≈ 7,500 / 月 |

備註：價格以 2026 年公開定價粗估，未含 BigQuery 讀取（GA360 匯出每日 < 10 GB，可忽略）、LLM 用量與支援方案；SIT 約為正式的 15%。

## 9. 對應檔案
- `deploy/helm/altatech-dmp/values-gcp-30b.yaml`：本配置的節點選擇、副本數與資源上限。
- `deploy/tofu/gcp/main.tf`：節點池機型與數量已改為本配置；ClickHouse 池使用 Hyperdisk Balanced。
- `backend/openapi.yaml`：`/v1/sync/*` 標籤與分群同步端點；Kafka topic `dmp.tag_updates`、`dmp.segment_updates`。
