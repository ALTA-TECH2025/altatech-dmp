# GCP 容量規劃：3 年 200 億事件（首年 100 億含舊資料導入、年增 50 億）、每日 1,000 個標籤、API 同步、50 人同時上線（常駐 3–6 台）

> 版本 v0.5（2026-10-14）。本版依新假設縮小事件量，並把常駐機器限制在 **3–6 台**：6 台（完整 HA，建議）、4 台（平衡）、3 台（最小）。價格為粗估，以 GCP Pricing Calculator 為準。舊版（300 億、18 台）保留於 §10 供對照。

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

## 3. 模組合併原則（3–6 台）

| 原則 | 說明 |
|---|---|
| 成本跟 vCPU 走，不跟台數走 | 4 台 n2-standard-16 與 2 台 n2-standard-32 價格相同；減少台數主要是減少維運面與授權面，省錢靠「減副本、縮機型、用 Spot」 |
| ClickHouse 永遠 2 副本 | 分析倉是唯一不能單點的服務；2 副本放在 2 台不同機器 |
| 有狀態服務用「3 的倍數」才有 quorum | Kafka（KRaft）與 ClickHouse Keeper 各 3 個 Pod；6 台與 3 台可一機一 Pod；4 台時有一台放 2 個，該台故障會短暫失去 quorum（1–2 分鐘內重新排程） |
| 無狀態服務全部可混跑 | API、語意層、分群標籤、OneID、閘道、Agent、Tag Sync、Web、Collector、CDC、Keycloak、Prometheus、Grafana 以 PriorityClass（online > batch）與 ResourceQuota 共存 |
| 磁碟各自 PVC | Kafka、Prometheus、ClickHouse 的資料盤獨立 PVC；節點故障時 PD／Hyperdisk 可掛到其他節點 |
| 臨時運算用 Spot，不算常駐 | 舊資料導入 50 億、BigQuery 大批匯入、模型訓練：Spot 池 0–4 台，用完歸零 |
| 託管服務不占台數 | Cloud SQL、Memorystore、GCS 為託管；若要再省，可改在叢集內跑 CloudNativePG 與 Valkey（各省約 US$250、80／月，但增加維運） |

## 4. 建議配置（常駐 3–6 台）

### 4.1 三個檔位總覽

| 檔位 | 常駐台數 | 機器 | vCPU / RAM | HA | 隨需月費 | 1 年 CUD |
|---|---|---|---|---|---|---|
| **A. 標準 HA（建議）** | **6** | c3-highmem-22 × 2（olap）+ n2-standard-16 × 4（general，含 app） | 120 / 608 GB | 完整：任一台故障不中斷 | ≈ US$ 6,500 | ≈ 4,900 |
| **B. 平衡** | **4** | c3-highmem-22 × 2（olap）+ n2-standard-32 × 2（general） | 108 / 608 GB | ClickHouse 完整；Kafka／Keeper 一台放 2 個 Pod，該台故障短暫失去 quorum | ≈ US$ 6,500 | ≈ 4,900 |
| **C. 最小** | **3** | n2-highmem-32 × 3（同構，每台都可跑任何服務） | 96 / 768 GB | ClickHouse 2 副本在 2 台；Kafka／Keeper 一機一 Pod；任一台故障仍可服務 | ≈ US$ 6,150 | ≈ 4,600 |
| C-經濟 | 3 | n2-highmem-16 × 3 | 48 / 384 GB | 同 C | ≈ US$ 4,000 | ≈ 3,100 |

四個檔位都滿足：3 年 200 億事件全熱、每日 1,000 標籤、50 人同時上線。C-經濟 的代價是標籤日批約 90 分鐘、30 天漏斗 P95 約 15 秒、尖峰時段查詢與批次互相影響較明顯。

### 4.2 A. 標準 HA：6 台（建議）

| 節點池 | 機型 × 數量 | 每台 vCPU / RAM | 磁碟 | 放什麼 |
|---|---|---|---|---|
| **olap** | c3-highmem-22 × 2 | 22 / 176 GB | Hyperdisk Balanced 4 TB（12,000 IOPS） | ClickHouse 1 shard × 2 replica（3 年全熱 3.9 TB/副本） |
| **general** | n2-standard-16 × 4 | 16 / 64 GB | 系統盤 200 GB；Kafka PVC 500 GB × 3；Prometheus PVC 150 GB | Kafka × 3、Keeper × 3（反親和分散）、Flink JM + TM × 2、Airflow + dbt、Collector × 2、CDC、Keycloak × 2、Prometheus、Grafana、OTel，**以及全部應用**（API × 3、語意層 × 2、分群標籤 × 2、OneID × 2、閘道 × 2、Agent × 2、Tag Sync × 2、Web × 2）→ 約 52 vCPU / 140 GB，占 4 台的 80% |
| spot | n2-standard-8 × 0–4 | — | — | 舊資料導入、大批匯入、模型 |
| 託管 | Cloud SQL db-custom-2-8192 HA、Memorystore Valkey 8 GB、GCS 8 TB | — | — | — |

### 4.3 B. 平衡：4 台
olap 同 A；general 改 **n2-standard-32 × 2**（32 vCPU / 128 GB）。Kafka 3 個 broker 與 Keeper 3 個 Pod 分佈為 2 + 1，PriorityClass 保證 online 服務先排程。適合「台數有限但 vCPU 不想縮」。

### 4.4 C. 最小：3 台（同構）

| 台 | n2-highmem-32（32 vCPU / 256 GB） | 磁碟 |
|---|---|---|
| node-1 | ClickHouse replica-1（20 vCPU / 160 GB）+ Kafka-1 + Keeper-1 + Collector + API/閘道/Agent 各 1 | Hyperdisk 4 TB + Kafka PVC 500 GB |
| node-2 | ClickHouse replica-2（20 vCPU / 160 GB）+ Kafka-2 + Keeper-2 + Collector + API/閘道/Agent 各 1 | Hyperdisk 4 TB + Kafka PVC 500 GB |
| node-3 | Kafka-3 + Keeper-3 + Flink JM/TM + Airflow + dbt + CDC + Keycloak + Prometheus + Grafana + 語意層/分群/OneID/Tag Sync/Web | Kafka PVC 500 GB + Prometheus PVC 150 GB + 預留 Hyperdisk 4 TB（可作 ClickHouse 第三副本或故障接手） |

- 任一台故障：ClickHouse 另一副本續服、Kafka 2/3 仍有 quorum、無狀態 Pod 重新排程到剩餘兩台（記憶體 256 GB 足夠）。
- 日批時段 node-3 跑批次、node-1/2 跑查詢，互不干擾。
- 事件量翻倍：先把 node-3 升級成 ClickHouse 第三副本或第二分片。

### 4.5 非正式環境
- SIT：n2-standard-8 × 2（ClickHouse 單節點 + 其餘一台），資料 1/100。
- UAT／效能驗證：與正式同檔位，驗證後縮回。

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

| 項目 | A. 6 台 | B. 4 台 | C. 3 台 | C-經濟 3 台 |
|---|---|---|---|---|
| 運算（常駐） | c3-highmem-22 × 2 ≈ 2,200 + n2-standard-16 × 4 ≈ 2,280 | 2,200 + n2-standard-32 × 2 ≈ 2,280 | n2-highmem-32 × 3 ≈ 4,260 | n2-highmem-16 × 3 ≈ 2,130 |
| Hyperdisk Balanced（4 TB × 2）+ 佈建 IOPS | ≈ 950 | ≈ 950 | ≈ 950 | ≈ 950 |
| 其他區塊磁碟（Kafka、Prometheus、系統盤） | ≈ 250 | ≈ 200 | ≈ 150 | ≈ 150 |
| Cloud SQL HA 2 vCPU/8 GB + 200 GB | ≈ 250 | ≈ 250 | ≈ 250 | ≈ 250 |
| Memorystore Valkey 8 GB | ≈ 80 | ≈ 80 | ≈ 80 | ≈ 80 |
| GCS 8 TB | ≈ 160 | ≈ 160 | ≈ 160 | ≈ 160 |
| 網路、Cloud Armor、KMS、Logging | ≈ 300 | ≈ 300 | ≈ 300 | ≈ 300 |
| 臨時 Spot（舊資料導入，攤 3 年） | ≈ 20 | ≈ 20 | ≈ 20 | ≈ 20 |
| **合計（隨需）** | **≈ 6,500** | **≈ 6,450** | **≈ 6,150** | **≈ 4,050** |
| **1 年 CUD（運算 −37%）** | **≈ 4,900** | **≈ 4,850** | **≈ 4,600** | **≈ 3,100** |

台數從 6 降到 3 省得有限（約 5%），因為成本主要在 vCPU 與磁碟；要明顯省錢請選 C-經濟（縮機型）並接受效能折衷。對比舊版（300 億、18 台）約 10,900 / 月，A 檔降約 40%。

## 9. 風險與緩解
| 風險 | 緩解 |
|---|---|
| 同一台混跑多種服務，磁碟 IO 與記憶體互相影響 | 資料盤各自 PVC；PriorityClass online > batch；ResourceQuota 與 LimitRange；ClickHouse 設記憶體上限 160 GB |
| 單一節點故障影響多個服務 | Kafka／Keeper／Keycloak 反親和分散；ClickHouse 2 副本在不同台；Flink JM 由 Operator 自動重啟（< 1 分鐘）；4 台檔位接受 Kafka 短暫失去 quorum |
| ClickHouse 單分片，未來事件量翻倍 | 磁碟可線上擴到 8 TB；超過再加分片 |
| 舊資料導入 50 億一次灌入 | 用 Spot 池 + `INSERT ... SELECT` 分區批次；導入期間暫停日批 |

## 10. 舊版對照（300 億事件、18 台）
ClickHouse 2×2 c3-highmem-22（4 TB）、Kafka n2-standard-4 ×3、Flink n2-standard-8 ×2、批次 n2-standard-8 ×2、接入 n2-standard-4 ×2、應用 n2-standard-8 ×3、平台 n2-standard-4 ×2、Keeper e2 ×3；≈ 170 vCPU / 1.1 TB；月費 ≈ 10,900（CUD ≈ 8,200）。對應 `values-gcp-30b.yaml` 與 tofu profile `large-30b`，保留供未來擴張。

## 11. 對應檔案
- `deploy/helm/altatech-dmp/values-gcp-6n.yaml`（A，預設）、`values-gcp-4n.yaml`（B）、`values-gcp-3n.yaml`（C）。
- `deploy/tofu/gcp/main.tf`：profile `6n`（預設）、`4n`、`3n`、`30b`、`large`。
- `backend/openapi.yaml`：`/v1/sync/*`。
