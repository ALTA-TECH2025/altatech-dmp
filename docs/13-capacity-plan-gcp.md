# GCP 容量規劃：3 年 200 億事件（首年 100 億含舊資料導入、年增 50 億）、每日 1,000 個標籤、API 同步、50 人同時上線

> 版本 v0.4（2026-10-14）。本版依新假設縮小事件量，並把模組合併到 **3 個節點池、7 台機器**（極簡 6 台）。價格為粗估，以 GCP Pricing Calculator 為準。舊版（300 億、18 台）保留於 §10 供對照。

## 1. 需求換算

| 項目 | 數值 | 推導 |
|---|---|---|
| 總事件量 | **200 億 / 3 年** | 第 1 年 100 億（其中約 50 億為舊系統一次性導入）、第 2 年 +50 億、第 3 年 +50 億 |
| 日常增量 | 50 億 / 年 ≈ **1,370 萬 / 日** | 設計尖峰 5,000 萬 / 日、**7,000 事件/秒**（開盤前 10 分鐘） |
| 舊資料導入 | 50 億，一次性 | 以 Spark／dlt 從 GCS Parquet 批次灌入，4 台臨時 Spot 節點約 1–2 天，導入後釋放 |
| 客戶數（假設） | 300 萬 OneID | — |
| 標籤 | 1,000 個，每日全量重算；5,000 個標籤值 Bitmap，版本保留 7 份 | — |
| API 同步 | 每日變動 5–10%（0.75–1.5 億個「客戶 × 標籤」變更） | Kafka 增量 + GCS Parquet + REST 點查 |
| 同時上線 | 50 人；高峰 5 查詢/秒 | ClickHouse 併發上限 100 |

## 2. 儲存量推導

| 資料 | 計算 | 結果 |
|---|---|---|
| ClickHouse 明細（L0）單副本 | 200 億 × 150 B | **3.0 TB** |
| MV（L1/L2/L3）+ 索引 + Projection | L0 × 20% | 0.6 TB |
| 標籤 Bitmap、users／items／id_map、audit_log | — | 0.3 TB |
| **ClickHouse 單副本合計（3 年全熱）** | | **≈ 3.9 TB**；2 副本 ≈ 7.8 TB |
| 分層選項 | 熱 13 個月（約 55 億事件 ≈ 1.0 TB/副本 + MV）→ 每台 2 TB 即可；其餘走 GCS S3 disk | 本版建議 **3 年全熱**（磁碟便宜、查詢一致），每台 Hyperdisk 4 TB 仍有 25% 餘裕；第 4 年起再開冷層 |
| Kafka（7 天） | 1,370 萬 × 7 × 0.8 KB × 3 副本 | 0.25 TB（尖峰月 0.8 TB） |
| GCS 原始事件（Iceberg raw，3 年） | 200 億 × 0.12 KB | 2.4 TB |
| GCS 標準化 Iceberg（雙向映射、重算） | 200 億 × 0.15 KB | 3.0 TB |
| GCS 舊資料導入暫存、備份、審計 WORM、checkpoint | — | 2.5 TB |
| **GCS 合計** | | **≈ 8 TB**（舊資料導入暫存可於導入後刪除） |
| PostgreSQL | 字典、角色、審批、Insight Store、Knowledge Pack | < 100 GB |
| Valkey | OneID 快取 + 即時標籤 + 結果快取 | ≈ 3 GB（配 8 GB） |

寫入：平均 160 事件/秒、尖峰 7,000/秒，單一 ClickHouse 節點上限 > 10 萬列/秒，寫入不是瓶頸。

## 3. 模組合併原則

| 原則 | 說明 |
|---|---|
| OLAP 獨立 | ClickHouse 吃記憶體與磁碟 IO，與其他服務分開，避免查詢被排程作業擠壓 |
| 「資料 + 平台」合併 | Kafka、Keeper、Flink、Airflow／dbt、Collector、CDC、Keycloak、Prometheus、Grafana 都是中低負載，合併到一個 **general 池**；以 Pod 反親和把 Kafka broker 與 Keeper 分散到 3 台 |
| 「應用」合併 | API、語意層、分群標籤、OneID、工具閘道、Agent Runtime、Tag Sync、Web 全是無狀態 Python/Go/靜態服務，合併到 **app 池**；50 人負載下 2 台即可，HPA 再長 |
| 磁碟各自 PVC | Kafka、Prometheus 的資料盤為獨立 PVC（pd-balanced），不與系統盤爭 IO |
| 臨時運算用 Spot | 舊資料導入與 BigQuery 大批匯入用 Spot 節點池，用完歸零 |

## 4. 建議配置

### 4.1 精簡標準配置（建議）：3 個節點池、**7 台**

| 節點池 | 機型 × 數量 | 每台 vCPU / RAM | 磁碟 | 放什麼 |
|---|---|---|---|---|
| **olap** | **c3-highmem-22 × 2**（1 shard × 2 replica） | 22 / 176 GB | **Hyperdisk Balanced 4 TB**（12,000 IOPS、800 MB/s） | ClickHouse（3 年全熱 3.9 TB/副本） |
| **general** | **n2-standard-16 × 3** | 16 / 64 GB | 系統盤 200 GB；Kafka PVC 500 GB ×3；Prometheus PVC 150 GB | Kafka ×3（各 3 vCPU/12 GB）、Keeper ×3（1/2）、Flink JM 1/2 + TM ×2（4/16）、Airflow + dbt（2/8）、Collector ×2（1/1）、Debezium（1/2）、Keycloak ×2（1/2）、Prometheus（2/8）、Grafana（0.5/1）、OTel（1/2）→ 約 32 vCPU / 100 GB，占 3 台 2/3 |
| **app** | **n2-standard-8 × 2** | 8 / 32 GB | 系統盤 100 GB | API ×3（2/4）、語意層 ×2（1/2）、分群標籤 ×2（2/4）、OneID ×2（1/2）、工具閘道 ×2（1/2）、Agent Runtime ×2（1/2）、Tag Sync ×2（2/4）、Web ×2（0.5/0.5）→ 約 20 vCPU / 40 GB，HPA 可長到 3 台 |
| spot（臨時） | n2-standard-8 × 0–4（Spot） | 8 / 32 GB | — | 舊資料導入、BigQuery 大批匯入、模型標籤訓練 |
| **Cloud SQL for PostgreSQL 16** | db-custom-2-8192（區域 HA） | 2 / 8 GB | SSD 200 GB | 控制面、Insight Store |
| **Memorystore for Valkey** | 8 GB（Standard HA） | — | — | 快取 |
| **Cloud Storage** | 8 TB（Standard；備份與 raw 90 天後 Nearline） | — | — | 原始、Iceberg、備份、WORM |
| **合計** | **7 台常駐**（+ 臨時 Spot） | **≈ 108 vCPU / 608 GB** | ≈ 10.5 TB 區塊 + 8 TB GCS | |

### 4.2 極簡配置：**6 台**
app 池併入 general 池 → **general n2-standard-16 × 4**，其餘不變。少 1 台、月費再降約 5%；代價是使用者介面延遲受日批影響的風險略增（以 Pod 優先級與 ResourceQuota 緩解）。

### 4.3 為什麼 ClickHouse 不再分 2 shard
200 億事件單副本 3.9 TB，單節點可穩定承載（ClickHouse 單機百億列常見）；50 人併發下重查詢同時 5–8 個，2 台 × 22 vCPU 足夠。分片帶來的平行加速對 30 天漏斗（約 5 億事件掃描）影響在 2–4 秒內，不值得多 2 台。日後事件量翻倍再加分片（Operator 支援線上擴分片，需重分佈一次）。

### 4.4 非正式環境
- SIT：values-small，2 台 n2-standard-8（ClickHouse 單節點 + 其餘全擠在一台），資料 1/100。
- UAT／效能驗證：與正式同拓撲但 olap 改 c3-highmem-8 × 2，用 200 億合成資料跑基準，驗證後縮回。

## 5. 每日 1,000 個標籤的運算

| 步驟 | 作法 | 時間（2 × c3-highmem-22） |
|---|---|---|
| L1 人日聚合 | MV 隨寫入維護 | 0 |
| 統計／偏好標籤（約 600） | 合併成約 120 條 SQL，6 路並行 | 20–30 分鐘 |
| 規則標籤（約 300） | Bitmap 交併差 | < 3 分鐘 |
| SQL／模型標籤（約 100） | 沙箱 SQL（限 20 GB）；模型在 Spot 節點 | 10–20 分鐘（並行） |
| 版本切換、分布統計、血緣 | 原子切換 | < 2 分鐘 |
| **合計** | Airflow 拓撲並行 | **約 50 分鐘**，02:00–03:00 |

日批期間 ClickHouse `batch` workload 限 10 執行緒、優先級最低，保留一半 CPU 給線上。

## 6. 標籤 API 同步

| 模式 | 吞吐（本配置） |
|---|---|
| Kafka 增量 `dmp.tag_updates`／`dmp.segment_updates` | 0.75–1.5 億變更 / 日，Tag Sync 2 Pod × 2 vCPU，約 20–30 分鐘送完 |
| GCS Parquet 全量／增量 | 300 萬 × 1,000 = 30 億格 ≈ 6 GB，約 5 分鐘 |
| REST `GET /v1/sync/tags`、`/changes` | 每 Pod 2,000 req/s，Valkey 快取 < 10 ms |

輸出只含代碼化 ID；每次同步寫審計 `sync.delivered`。

## 7. 50 人同時上線

| 項目 | 設定 |
|---|---|
| ClickHouse | `max_concurrent_queries=100`；人 12 GB/60 s、agent 8 GB/40 s/20 GB 掃描、batch 最低優先；Query Cache 5 分鐘 |
| 負載 | 5 查詢/秒，九成命中 L2／快取；同時重查詢 5–8 個 → 2 台 × 22 vCPU 餘裕充足 |
| API | app 池 2 台共 16 vCPU，FastAPI 單 Pod 1,000+ req/s |
| AI 問答 | 每問 3–5 次工具呼叫，50 人同時發問占 ClickHouse 併發 < 10 |

## 8. 月費粗估（asia-east1，隨需，未稅，USD）

| 項目 | 精簡標準（7 台） | 極簡（6 台） |
|---|---|---|
| c3-highmem-22 × 2（≈ $1.5/hr） | ≈ 2,200 | ≈ 2,200 |
| Hyperdisk Balanced 8 TB + 佈建 IOPS | ≈ 950 | ≈ 950 |
| n2-standard-16 × 3（≈ $0.78/hr）／× 4 | ≈ 1,700 | ≈ 2,280 |
| n2-standard-8 × 2（≈ $0.39/hr） | ≈ 570 | — |
| 區塊磁碟（Kafka 1.5 TB + Prometheus + 系統盤） | ≈ 250 | ≈ 250 |
| Cloud SQL HA 2 vCPU/8 GB + 200 GB | ≈ 250 | ≈ 250 |
| Memorystore Valkey 8 GB | ≈ 80 | ≈ 80 |
| GCS 8 TB（含 Nearline 分層） | ≈ 160 | ≈ 160 |
| 網路、Cloud Armor、KMS、Logging | ≈ 300 | ≈ 300 |
| 臨時 Spot（舊資料導入，攤 3 年） | ≈ 20 | ≈ 20 |
| **合計** | **≈ 6,500 / 月**（≈ 21 萬 TWD） | **≈ 6,500 / 月**（4 台 n2-standard-16 ≈ 3 台 + 2 台 n2-8，費用相近） |
| 1 年 CUD（運算 −37%） | **≈ 4,900 / 月** | ≈ 4,900 / 月 |

對比舊版（300 億、18 台）約 10,900 / 月：**少 11 台、月費降約 40%**。

## 9. 風險與緩解
| 風險 | 緩解 |
|---|---|
| general 池混跑 Kafka 與 Flink，磁碟 IO 互相影響 | Kafka 獨立 PVC；Flink 狀態小（< 10 GB）；Prometheus 獨立 PVC |
| 單一節點故障影響多個服務 | 3 台 general 以反親和分散 Kafka／Keeper／Keycloak；Flink JM 由 Operator 自動重啟（< 1 分鐘） |
| ClickHouse 單分片，未來事件量翻倍 | 磁碟可線上擴到 8 TB；超過再加分片 |
| 舊資料導入 50 億一次灌入 | 用 Spot 池 + `INSERT ... SELECT` 分區批次；導入期間暫停日批 |

## 10. 舊版對照（300 億事件、18 台）
ClickHouse 2×2 c3-highmem-22（4 TB）、Kafka n2-standard-4 ×3、Flink n2-standard-8 ×2、批次 n2-standard-8 ×2、接入 n2-standard-4 ×2、應用 n2-standard-8 ×3、平台 n2-standard-4 ×2、Keeper e2 ×3；≈ 170 vCPU / 1.1 TB；月費 ≈ 10,900（CUD ≈ 8,200）。對應 `values-gcp-30b.yaml` 與 tofu profile `large-30b`，保留供未來擴張。

## 11. 對應檔案
- `deploy/helm/altatech-dmp/values-gcp-20b.yaml`：3 個節點池的配置（預設）。
- `deploy/tofu/gcp/main.tf`：profile `20b`（預設）、`30b`、`large`。
- `backend/openapi.yaml`：`/v1/sync/*`。
