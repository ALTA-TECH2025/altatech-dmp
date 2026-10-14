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

### 4.5 環境指派（已決定）

| 環境 | 檔位 | 常駐 | 說明 |
|---|---|---|---|
| **正式** | **A. 標準 HA** | 6 台（c3-highmem-22 × 2 + n2-standard-16 × 4）+ 臨時 Spot | 完整 HA；Cloud SQL HA、Memorystore Valkey、GCS |
| **測試（SIT／UAT）** | **C. 最小** | 3 台（n2-highmem-32 × 3 同構） | 與正式同一套 Helm chart，只換 values；資料用合成或正式脫敏抽樣（1/10）；效能驗證時可暫時把 ClickHouse 盤擴到 4 TB |
| 開發 | docker compose 或單節點 | 0–1 台 | 本機或 1 台 n2-standard-8 |

測試環境可在非上班時段縮到 0（GKE 節點池 min=0 + Cloud SQL 停機），月費可再降一半以上。

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

## 11. 機房方案：實體機／VM 規格（Linux x86_64，不用 GCP 時）

同一套容器化部署套件（Docker 映像 + Helm）改跑在機房 Kubernetes（RKE2）。GCP 託管服務以叢集內元件取代：Cloud SQL → CloudNativePG、Memorystore → Valkey、GCS → SeaweedFS（或既有 NAS／S3 相容儲存）、Cloud KMS → OpenBao + HSM、Cloud Armor／IAP → 既有 WAF／反向代理。

### 11.1 正式（對應 A 檔，6 台）

| 角色 | 台數 | CPU | 記憶體 | 磁碟 | 網路 | 說明 |
|---|---|---|---|---|---|---|
| **olap（ClickHouse）** | 2 | 2 × 16 核（32 核／64 執行緒），x86_64，3.0 GHz 以上（Intel Xeon 4th gen Sapphire Rapids 或 AMD EPYC 9004） | **256 GB** DDR5 ECC | 系統 2 × 480 GB SSD（RAID1）；**資料 2 × 3.84 TB NVMe**（RAID1 或 ClickHouse 多盤 JBOD；可用 6.4 TB 單盤）；需 ≥ 100k IOPS、≥ 3 GB/s | 2 × 10 GbE（bond）+ 1 GbE 管理 | 對應 c3-highmem-22 × 1.5 倍餘裕；3 年全熱 3.9 TB／副本 |
| **general（Kafka、Keeper、Flink、Airflow／dbt、Collector、CDC、Keycloak、Prometheus、Grafana、全部應用、CloudNativePG、Valkey、SeaweedFS）** | 4 | 1 × 16 核／32 執行緒 | **128 GB** ECC | 系統 2 × 480 GB SSD（RAID1）；**資料 1 × 1.92 TB NVMe**（Kafka、PostgreSQL、Prometheus PVC）；**物件儲存 2 × 8 TB HDD**（SeaweedFS，4 台共 64 TB 原始，EC 或 2 副本後約 24–32 TB 可用） | 2 × 10 GbE + 1 GbE 管理 | 比 GCP n2-standard-16 多配 2 倍記憶體與本地 HDD，因為 PostgreSQL／Valkey／物件儲存改在叢集內跑 |
| 合計 | **6** | 128 核／256 執行緒 | 1,024 GB | NVMe 15 TB + SSD 5.8 TB + HDD 64 TB | 10 GbE 交換器 ×2（冗餘）| |

另需（非運算節點，通常既有）：負載平衡（F5／HAProxy／MetalLB）、備份目標（NAS 20 TB 或既有備份系統）、NTP、DNS、AD／LDAP、WAF；舊資料導入時可借 2–4 台臨時 VM。

### 11.2 測試（對應 C 檔，3 台）

| 角色 | 台數 | CPU | 記憶體 | 磁碟 | 網路 |
|---|---|---|---|---|---|
| all（同構） | 3 | 1 × 16 核／32 執行緒 | **256 GB** ECC | 系統 2 × 480 GB SSD；資料 1 × 3.84 TB NVMe（node-1／node-2 跑 ClickHouse 副本，node-3 跑批次與 PostgreSQL／Valkey）；物件儲存 2 × 4 TB HDD（SeaweedFS，3 台 24 TB 原始） | 2 × 10 GbE |

### 11.3 若用 VM（VMware／Proxmox／KVM）

| 項目 | 要求 |
|---|---|
| vCPU | 依上表核數配 vCPU（1 vCPU = 1 執行緒）；ClickHouse VM 設 CPU 保留 100%，**不要超額配置** |
| 記憶體 | 全部保留（reservation = 配置），關閉 ballooning；ClickHouse 與 Kafka VM 關閉透明大頁 |
| 磁碟 | 資料盤 thick provision；NVMe 直通（PCIe passthrough）或 vSAN／全快閃 datastore，單 VM ≥ 20k IOPS、≥ 1 GB/s；Kafka 與 ClickHouse 不要同一個 datastore |
| 網路 | VMXNET3／virtio，10 GbE；節點間延遲 < 1 ms |
| NUMA | ClickHouse VM 落在單一 NUMA 節點或對齊 vNUMA |
| 反親和 | 同角色 VM 分散在不同實體主機（ClickHouse 兩副本、Kafka 三 broker） |
| 時間 | 與 NTP 同步；VM 時鐘漂移會影響 Kafka 與 Keeper |

### 11.4 作業系統與軟體

| 項目 | 規格 |
|---|---|
| OS | **RHEL 9.4+** 或 **Rocky Linux 9.4+**（x86_64）；最小安裝；SELinux enforcing；RHEL 10／Rocky 10 可用 |
| 核心參數 | `vm.max_map_count=262144`、`fs.file-max=2097152`、`net.core.somaxconn=65535`、`vm.swappiness=1`、關閉 swap、關閉透明大頁（ClickHouse 節點）；時區 UTC，應用層轉 Asia/Taipei |
| 檔案系統 | ClickHouse／Kafka 資料盤 XFS（noatime）；LVM 便於擴容 |
| Kubernetes | **RKE2 v1.30+**（Apache-2.0）；3 個 control-plane 角色與 worker 共存於 6 台（A 檔：general 4 台中選 3 台兼 control-plane；C 檔：3 台皆為 control-plane + worker） |
| 儲存 | Local Path／OpenEBS LocalPV（NVMe 本地盤，ClickHouse／Kafka）；SeaweedFS（Apache-2.0）提供 S3 API 給 Iceberg、備份、同步輸出；若有既有 S3 相容儲存（NetApp／Dell ECS／MinIO 企業版除外）可直接用 |
| 資料庫／快取 | CloudNativePG（PostgreSQL 16，2 實例 + 每日備份到 S3）；Valkey 8（3 節點 sentinel 或 cluster） |
| 密鑰 | OpenBao（MPL-2.0）+ 既有 HSM 或 PKCS#11；TLS 由內部 CA 簽發 |
| 入口 | Ingress-NGINX 或既有 F5／WAF；MetalLB 提供 VIP |
| 映像 | 離線映像倉（Harbor，Apache-2.0）鏡像所有 UBI／distroless 基底與平台映像；弱掃用 Trivy |
| 備份 | ClickHouse `BACKUP TO S3`（每日增量、每週全量）；PostgreSQL WAL 歸檔；Kafka 不備份（可從 Iceberg 回放）；備份桶 Object Lock 或離線磁帶 |
| 監控 | Prometheus + Grafana OSS（叢集內）；告警到既有 Email／LINE |


### 11.6 Dell PowerEdge 採購預估（正式 6 台 + 測試 3 台 = 9 台）

> 以 2026 年台灣 Dell 通路常見價位推估（含 3 年 ProSupport 次工作日到府、iDRAC9 Enterprise、雙電源），**未稅**；實際以經銷商正式報價為準，誤差約 ±20%。

| 角色 | 機型 | 建議配置 | 台數 | 單價（NT$） | 小計（NT$） |
|---|---|---|---|---|---|
| 正式 OLAP（ClickHouse） | **PowerEdge R760**（2U） | 2 × Xeon Gold 16 核（如 6526Y）、256 GB DDR5（8 × 32 GB）、BOSS-N1 2 × 480 GB、2 × 3.84 TB NVMe U.2 Mixed Use、Broadcom 雙埠 10/25GbE、2 × 1,100 W | 2 | 48–65 萬 | 96–130 萬 |
| 正式 general | **PowerEdge R660**（1U） | 1 × Xeon Gold 16 核、128 GB DDR5（8 × 16 GB）、BOSS-N1 2 × 480 GB、1 × 1.92 TB NVMe、2 × 8 TB NL-SAS HDD（HBA355 直通）、雙埠 10/25GbE、2 × 800 W | 4 | 28–38 萬 | 112–152 萬 |
| 測試（同構） | **PowerEdge R760**（2U） | 1 × Xeon Gold 16 核、256 GB DDR5、BOSS-N1 2 × 480 GB、1 × 3.84 TB NVMe、2 × 4 TB NL-SAS HDD、雙埠 10GbE | 3 | 38–48 萬 | 114–144 萬 |
| **9 台主機小計** | | | **9** | | **322–426 萬** |
| 網路 | 2 × 10/25GbE ToR 交換器（Dell S5248F 或同級，含光模組／DAC） | | 2 | 8–15 萬 | 16–30 萬 |
| 機櫃／PDU／線材 | 若無既有機櫃 | | 1 | 5–10 萬 | 5–10 萬 |
| **硬體合計（未稅）** | | | | | **約 343–466 萬** |
| 營業稅 5% | | | | | 17–23 萬 |
| **含稅合計** | | | | | **約 360–490 萬** |

選配與注意：
- 作業系統：Rocky Linux 9 免費；若客戶要求 RHEL 9，約 NT$ 2.5–3.5 萬／台／年，9 台 3 年再加約 70–95 萬。
- 保固升級為 4 小時 Mission Critical：主機價 +10–15%。
- 第 4 年起維保約為硬體價的 8–12%／年。
- 若 general 節點的物件儲存改用既有 NAS／S3，可拿掉每台 2 × 8 TB HDD，約省 1.5–2 萬／台。
- 美國 Dell 官網配置器與第三方經銷的參考：2 × Xeon Gold 6430、256 GB DDR5、2 × 1.92 TB NVMe 的 R760 約 US$ 8,500–10,500（不含台灣通路保固加價）。

### 11.5 與 GCP 的差異
- 機房 A 檔的 general 節點比 GCP 版大一倍記憶體並多 HDD，是因為把 Cloud SQL、Memorystore、GCS 收進叢集。
- 沒有 Spot：舊資料導入用臨時 VM 或在夜間用 general 節點閒置資源分批跑（約 3–5 天）。
- 硬體一次性採購：正式 6 台約 NT$ 210–280 萬、含測試 3 台共 9 台約 NT$ 320–430 萬（未稅，含 3 年保固；見 §11.6）；相較 GCP A 檔 3 年約 NT$ 560–750 萬（CUD 後約 NT$ 570 萬），測試環境另計。

## 12. 對應檔案
- `deploy/helm/altatech-dmp/values-gcp-6n.yaml`（A，正式，預設）、`values-gcp-4n.yaml`（B）、`values-gcp-3n.yaml`（C，測試）。
- `deploy/helm/altatech-dmp/values-onprem-6n.yaml`（機房正式）、`values-onprem-3n.yaml`（機房測試）：叢集內 CloudNativePG／Valkey／SeaweedFS、LocalPV。
- `deploy/tofu/gcp/main.tf`：profile `6n`（預設）、`4n`、`3n`、`30b`、`large`。
- `backend/openapi.yaml`：`/v1/sync/*`。
