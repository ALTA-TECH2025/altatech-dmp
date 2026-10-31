# 百億事件量級：標籤運算與查詢效能設計

> 版本 v0.2（2026-10-14）。假設：**總量 100 億事件**（約 13 個月熱資料）、**每日新增 3–5 億**、**1,500 萬客戶**、**2,000 個標籤值**、並發查詢 50。

## 1. 容量與拓撲

| 項目 | 數值 |
|---|---|
| 壓縮後單事件 | 120–180 B（LowCardinality + ZSTD(3) + 物化高頻欄位） |
| 100 億事件熱層 | ≈ 1.5 TB（單副本）→ 3 TB（2 副本） |
| 每日增量 | 5 億 × 150 B ≈ 75 GB |
| ClickHouse | **4 分片 × 2 副本**，每節點 44 vCPU／352 GB／8 TB NVMe（Hyperdisk Extreme 或本地 SSD）；Keeper ×3 |
| 分片鍵 | `cityHash64(one_id)` → 同一人永遠同片；漏斗／路徑／歸因／用戶列表無跨片 shuffle |
| 分區 | 按月（熱）；最近 7 天可按日分區以加速小範圍查詢與 TTL 搬移 |
| 排序鍵 | `(project, event_date, event, distinct_id_uint, time, event_id)`；`SAMPLE BY distinct_id_uint`（docs/23 §4.1，2026-10-22 定案） |

## 2. 稠密使用者 ID（效能基礎）
- OneID 服務在建立 one_id 時同時分配 **`one_id_uint`（UInt32/UInt64 單調遞增）**；事件表與所有 Bitmap 皆用它。
- RoaringBitmap 對稠密整數的壓縮率極高：1,500 萬人的全量 Bitmap 約 2 MB；交併差為毫秒級。
- 合併 OneID 時只改 `id_map`，`one_id_uint` 以「保留高優先者，舊 ID 標記 alias」處理；事件表的 `one_id_uint` 以 `ALTER UPDATE` 分區批次修正（夜間），查詢期間以 `alias_map` 字典補償。

## 3. 多層聚合（讓 90% 查詢不碰明細）

| 層 | 內容 | 粒度 | 體積（相對明細） |
|---|---|---|---|
| L0 明細 `events` | 全部欄位 | 事件 | 100% |
| L1 `user_event_daily` | `(one_id_uint, event_date, event) → cnt, sum_amount, first_time, last_time` | 人×日×事件 | ~8% |
| L2 `events_daily_agg` | `(event_date, event, platform, channel, utm_source, …) → countState, uniqState, sumState` | 日×維度 | < 0.1% |
| L3 `touchpoints` | 觸點事件子集 + 維度 | 事件 | ~5–10% |
| L4 `user_tags` Bitmap | 標籤值 → Bitmap | 標籤值 | 2 MB／值 |
| L5 `users` | 使用者屬性 + 統計快照（最近活躍、累計金額…） | 人 | 1,500 萬列 |

路由規則（語意層編譯器）：
- 無事件屬性篩選、日粒度以上 → L2。
- 使用者層行為條件（做過 N 次、首末次）→ L1。
- 漏斗／路徑／歸因 → L0（或 L3），但先用 L4 Bitmap 縮小人群，再 `PREWHERE one_id_uint IN bitmap`。
- 用戶列表 → L4 交併差 → L5。

## 4. 標籤運算

### 4.1 計算策略
| 標籤類型 | 全量 | 增量 |
|---|---|---|
| 統計／偏好（事件聚合） | 從 L1 聚合（不碰 L0）：1,500 萬人 × 90 天 × 事件 ≈ 數億列，ClickHouse 單查詢分鐘級 | 每日只掃當日 L1，更新 `user_tag_values`；Bitmap 以 `bitmapOr/AndNot` 增量更新 |
| 規則（屬性／標籤組合） | Bitmap 交併差，秒級 | 隨依賴標籤更新 |
| SQL | 沙箱執行，限制掃描量與時間 | 自訂 |
| 模型 | Spark 讀 L1/L5 特徵 → 寫回 | 每日 |
| 即時 | Flink 狀態 → Valkey；每 5 分鐘 flush 到 CH Bitmap | 秒級 |

### 4.2 排程與依賴
- 標籤 DAG：由標籤定義自動推導依賴（規則標籤依賴統計標籤），Airflow 以拓撲序並行。
- 全量日批目標 < 60 分鐘：L1 當日分區 → 統計標籤（並行 8 路）→ 規則標籤 → Bitmap 版本切換（原子）。
- 失敗重跑只重算受影響分支；版本保留 7 份。

### 4.3 儲存
- `user_tags`：`(tag_id, tag_value, version) → AggregateFunction(groupBitmap, UInt64)`；查詢 `bitmapCardinality(groupBitmapMerge(users))`。
- 2,000 標籤值 × 7 版本 × 2 MB ≈ 28 GB，可全部駐留記憶體。
- 數值標籤同時落分桶 Bitmap（例：流失分 10 桶）供區間篩選不碰明細。

## 5. 查詢效能策略（P0 模組）

| 模組 | 策略 | 100 億級預期 P95 |
|---|---|---|
| 事件分析 | L2 優先；明細查詢 `PREWHERE event IN` + 分區裁剪 + 分片平行；`uniqCombined64` | 1–5 s |
| 漏斗 | Bitmap 預篩第一步人群 → 分片內 `windowFunnel` → 合併；> 1 億人抽樣 1/10 | 5–10 s |
| 行為路徑 | 固定抽樣（1/10）、Session 內截斷 200、`event_node` 物化、結果快取 15 分鐘 | 3–8 s |
| 歸因 | L3 觸點表（5–10% 體積）+ 轉化人群 Bitmap；多模型同一次掃描內計算 | 5–10 s |
| 用戶列表 | L4 交併差 → `bitmapToArray` 分頁 → L5 點查 | 預估 < 1 s；翻頁 < 0.5 s |
| 標籤值分布 | L4 基數 | < 1 s |

共通：
- **Projection**：對 `events` 建 `(event, one_id_uint, time)` 的 projection，加速「某事件的所有人」類查詢。
- **跳數索引**：`one_id_uint` minmax／bloom；`symbol`、`order_no` bloom_filter。
- **查詢快取**：ClickHouse Query Cache（同參數 5 分鐘）＋ 服務層結果快取（Valkey）。
- **資源隔離**：Workload（人／agent／排程）分別設 `max_memory_usage`、`max_execution_time`、並發限制；agent 走獨立 quota 並有掃描預算。
- **冷熱**：13 個月後 TTL 到 GCS（S3 disk）；跨冷層查詢自動加 `SAMPLE` 並標示。

## 6. 寫入路徑
- Kafka → Flink → ClickHouse：以 **Kafka 表引擎或 Flink ClickHouse sink** 批次寫入（每批 50 萬列／1 秒），避免小 part。
- 每日 5 億 ≈ 5,800 事件/秒均值、尖峰 5 萬/秒（開盤）；4 分片各 1.3 萬/秒，遠低於單節點上限（> 20 萬/秒）。
- `ReplacingMergeTree` 去重 + `OPTIMIZE FINAL` 僅在夜間對前一日分區執行。

## 7. 基準測試計畫
- 合成資料產生器（WS3）：依事件字典與真實分布產生 100 億事件、1,500 萬人、含 GA 行為與交易事件比例 9:1。
- 固定查詢集 40 條（每模組 6–8 條）+ 標籤全量日批 + 分群預估；每 Sprint 回歸，結果進 Grafana 替代品（Perses）看板。
- 驗收：本文件 §5 目標全數達標，且 50 並發下 P95 不劣化 2 倍以上。

## 8. 若日增量升到 10 億以上
- 分片擴到 8，Keeper 不變；Kafka 分區 ≥ 64；Flink 並行度 ≥ 64。
- L1 改為按週分區 + 兩層 MV；路徑與歸因預設抽樣 1/20。
- 考慮熱層只留 6 個月，其餘走 Iceberg + Trino 的「慢查詢」路徑（UI 標示）。
