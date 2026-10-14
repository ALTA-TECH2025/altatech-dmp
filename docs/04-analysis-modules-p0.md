# P0 分析模組規格：事件分析、漏斗分析、行為路徑分析、歸因分析、用戶列表、標籤能力

> 版本 v0.2（2026-10-14）。本文件定義第一優先（P0）六個模組的功能範圍、對標、API、ClickHouse 實作與效能策略、驗收標準。
> 留存、間隔、分布、LTV、Session 降為 P1；熱圖、A/B 為 P2。

## 0. 優先序與依賴

```
標籤能力 ──┬──► 用戶列表（以標籤/分群篩人）
           ├──► 事件分析（以標籤分組/篩選）
           ├──► 漏斗分析（以標籤分組對比）
           ├──► 行為路徑（以分群限定起點人群）
           └──► 歸因分析（以標籤分組看渠道效果）
```

| 模組 | 對標（神策） | 對標（火山 DataFinder） | 依賴 |
|---|---|---|---|
| 事件分析 | 事件分析 | 事件分析 | 語意層、事件表、日聚合 MV |
| 漏斗分析 | 漏斗分析 | 轉化分析 | 事件表（依 one_id 分片）、Bitmap 預篩 |
| 行為路徑分析 | 用戶路徑 | 路徑分析 | 事件表、Session 化、抽樣 |
| 歸因分析 | 歸因分析 | 歸因分析 | 觸點表（預計算）、轉化事件 |
| 用戶列表 | 用戶列表／用戶分群 | 用戶細查列表 | 用戶表、標籤 Bitmap、OneID |
| 標籤能力 | 用戶畫像（標籤） | VeCDP 標籤 | 標籤引擎（Bitmap）、排程、血緣 |

---

## 1. 事件分析

### 1.1 功能
- 指標：`count`、`uniq_users`、`sum/avg/max/min/p50/p90(field)`、`per_user_avg`、比率（分子／分母）、事件組合指標（A+B、A/B）。
- 時間：分鐘（近 24h）／小時／日／週／月；同比、環比；自訂區間。
- 篩選：事件屬性、使用者屬性、標籤值、分群；AND/OR 巢狀條件。
- 分組：最多 3 個維度；Top N + 其他；分組值可搜尋。
- 視覺：折線、長條、堆疊、表格、累計；多指標對比；異常點標記（P1 接異常偵測）。
- 儲存與分享：存為看板卡片、匯出 CSV（審批）、複製為 agent 可讀的語意查詢 JSON。
- 細查入口：任一數據點 → 該日該分組的用戶列表。

### 1.2 API
`POST /v1/analytics/events`，請求體 = `SemanticQuery`（見 openapi.yaml）。回應含 `rows`、`chart_spec`、`sql`、`cost`、`warnings`。

### 1.3 ClickHouse 實作
- 不分組且指標為 count/uniq：優先走 `events_daily_agg`（AggregatingMergeTree，`uniqState(one_id)`）。
- 分鐘／小時粒度或有事件屬性篩選：走 `events` 明細，`PREWHERE event IN (...) AND event_date BETWEEN`。
- `uniq_users` 預設 `uniqCombined64`（誤差 < 1%），報表級精確值用 `uniqExact` 僅限 ≤ 7 天。
- 使用者屬性篩選：`one_id_uint IN (bitmap)`；標籤篩選：`bitmapContains(tag_bitmap, one_id_uint)`。

### 1.4 效能目標（百億級總量、單日 3 億）
| 查詢 | P95 |
|---|---|
| 30 天日粒度、1 指標、不分組 | < 1 s（MV） |
| 30 天、分組 platform × channel、事件屬性篩選 | < 5 s |
| 90 天、uniq_users、標籤篩選 | < 8 s |

---

## 2. 漏斗分析

### 2.1 功能
- 步驟 2–10 步；每步 = 事件 + 屬性條件；可指定「步驟間關聯屬性」（如同一 `order_no`、同一 `symbol`）。
- 視窗：分鐘到 90 天；嚴格順序／寬鬆順序。
- 分組對比（維度或分群）、時間趨勢（每日轉化率）、對比前期。
- 每步可看：轉化率、流失人數、中位耗時；點擊流失 → 用戶列表 → 行為序列。
- 結果可存為分群（某步已做／未做）。

### 2.2 ClickHouse 實作
```sql
SELECT level, count() FROM (
  SELECT one_id,
         windowFunnel(window, 'strict_order')(time, cond1, cond2, ...) AS level
  FROM dmp.events
  PREWHERE event IN (step_events) AND event_date BETWEEN d1 AND d2
    [AND one_id_uint IN (segment_bitmap)]
  GROUP BY one_id
) GROUP BY level
```
- 事件表以 `cityHash64(one_id)` 分片 → 每片獨立算完再合併，無 shuffle。
- 步驟間關聯屬性：用 `sequenceMatch` + `arrayJoin` 的 per-user 陣列版本（Java UDF 不可用時）。
- 大視窗（> 30 天）時先以 Bitmap 篩出「做過第一步」的人，減少掃描。
- 抽樣：對 > 1 億人的群體以 `SAMPLE 1/10`（表定義 `SAMPLE BY one_id_uint`）給出近似，UI 標示。

### 2.3 效能目標
30 天、6 步、1 億事件：P95 < 10 s；抽樣模式 < 3 s。

---

## 3. 行為路徑分析

### 3.1 功能
- 起點或終點事件；方向（之後／之前）；步數 1–10；Session 內或跨 Session。
- 事件合併（將多個 `ga.page_view` 依 `page_group` 視為不同節點；或將次要事件折疊為「其他」）。
- 視覺：桑基圖；節點可展開、可設為新起點；路徑 Top N 列表（路徑、人數、占比、平均耗時）。
- 篩選：分群、使用者屬性、時間。
- 從任一節點或路徑 → 用戶列表。

### 3.2 ClickHouse 實作
```sql
-- 每人取起點後 N 步（Session 內）
WITH arr AS (
  SELECT one_id, session_id,
         arraySort(x -> x.1, groupArray((time, event_node))) AS seq
  FROM dmp.events PREWHERE event_date BETWEEN d1 AND d2 AND event_node != ''
  GROUP BY one_id, session_id
)
SELECT path, count() AS users FROM (
  SELECT one_id, arraySlice(arrayMap(x -> x.2, seq), idx, N+1) AS path
  FROM arr ARRAY JOIN arrayEnumerate(seq) AS idx
  WHERE seq[idx].2 = anchor
) GROUP BY path ORDER BY users DESC LIMIT 100
```
- `event_node` 為物化欄位：由事件字典的「路徑節點規則」產生（例：`ga.page_view` → `page:` + page_group）。
- 一律抽樣（預設 1/10，可調 1/1 到 1/100），神策路徑亦採抽樣；UI 標示抽樣比例與推估人數。
- 僅保留每 Session 前 200 個事件；超長 Session 截斷。
- 結果快取：同參數 15 分鐘。

### 3.3 效能目標
30 天、抽樣 1/10、5 步：P95 < 8 s。

---

## 4. 歸因分析

### 4.1 功能
- 轉化目標：事件（可帶屬性條件、可選金額欄位作為價值）。
- 待歸因觸點：多個事件（曝光、點擊、推播開啟、營業員接觸、搜尋）＋ 可選 UTM 維度拆分。
- 模型：首次、末次、線性、位置（U 型，首末各 40%）、時間衰減（半衰期可設）、**自訂權重**；可同時比較多模型。
- 回溯視窗：1–90 天；是否只計入「直接前序」觸點。
- 輸出：各觸點（× 維度）的歸因轉化數、歸因價值、觸點貢獻占比、平均觸點數；對比模型表。
- 從觸點 → 轉化者用戶列表。

### 4.2 ClickHouse 實作
- 預計算 **觸點表** `dmp.touchpoints`（Flink 或日批 MV）：`(one_id, time, touch_event, touch_dims…)`，只保留字典標記為「觸點」的事件，體積約為事件表 5–10%。
- 查詢：對每個轉化事件，取回溯視窗內該人的觸點序列（`groupArray` 後 `arrayFilter`），在 SQL 中套權重：
  - 線性 `1/n`；位置 `[0.4, 0.2/(n-2)…, 0.4]`；時間衰減 `2^(-(t_conv - t_i)/half_life)` 正規化。
- 轉化人數 > 1,000 萬時抽樣。

### 4.3 效能目標
30 天、5 觸點、3 模型：P95 < 10 s。

---

## 5. 用戶列表

### 5.1 功能
- 條件組合：使用者屬性、標籤值、分群、行為（做過／未做過 事件 × 次數 × 時間窗 × 屬性）、行為序列。
- 即時人數預估（< 1 s，Bitmap 基數）；列表分頁（每頁 50、最多 10 萬筆瀏覽；匯出走審批）。
- 欄位：one_id、代碼化客戶 ID、分公司／營業員、生命週期、價值分級、流失分、最近活躍、自選顯示屬性與標籤。
- 動作：存為分群、推送到觸達渠道（審批）、批次打標籤、匯出（審批 + 浮水印）、進入用戶細查（需理由）。
- 行級權限：營業員僅見所屬客戶；分公司主管僅見分公司。

### 5.2 實作
- 人數預估：各條件各自產生 Bitmap（標籤：直接讀；行為：`groupBitmapState` 子查詢，日粒度預聚合 `user_event_daily`），交併差後 `bitmapCardinality`。
- 列表：`bitmapToArray` 取分頁的 `one_id_uint` 範圍 → JOIN `dmp.users`（`ORDER BY one_id_uint`，分頁用 `WHERE one_id_uint > last`）。
- `user_event_daily`：`(one_id_uint, event_date, event) → count, sum_amount`，AggregatingMergeTree；行為條件 90% 命中此表。

### 5.3 效能目標
人數預估 P95 < 1 s（3 條件以內）；列表翻頁 < 500 ms。

---

## 6. 標籤能力

### 6.1 標籤類型與計算方式
| 類型 | 定義方式 | 計算 | 更新 |
|---|---|---|---|
| 規則標籤 | UI 條件組合（屬性／行為／標籤） | Bitmap 交併差 | 日批／小時 |
| 統計標籤 | 事件 × 聚合（首次／末次／次數／總和／去重／最大） × 時間窗 | `user_event_daily` 聚合 | 日批 |
| 偏好標籤 | 事件 × 屬性 × 排序（Top1 值） | `topK`/`argMax` per user | 日批 |
| SQL 標籤 | 分析師 SQL，輸出 `(one_id, value)` | Trino/CH 沙箱，列級遮罩 | 自訂 |
| 模型標籤 | 外部模型寫回（流失分、偏好聚類） | Spark MLlib / 外部 API | 日批 |
| 即時標籤 | Flink 狀態（近 N 分鐘…） | Flink + Valkey | 秒級 |
| 匯入標籤 | CSV / API | — | 手動 |
| 生命週期標籤 | 狀態機（新客→成長→成熟→沉睡→流失） | 規則 + 統計 | 日批 |

### 6.2 標籤治理
- 標籤定義：名稱、分類、值域（單值／多值／數值分桶）、口徑說明、負責人、更新頻率、敏感等級、血緣、版本。
- 覆蓋率、值分布、歷史版本對比；標籤間相關性（P1）。
- 標籤 = 語意層的一個維度，agent 自動可見（`semantic.search`）。

### 6.3 儲存
```
dmp.user_tags  (tag_id, tag_value, version) → groupBitmap(one_id_uint)      -- 枚舉型標籤：每值一個 Bitmap
dmp.user_tag_values (one_id_uint, tag_id, value_num, value_str, version)    -- 數值／字串型標籤：明細，供顯示與區間篩選
dmp.user_tag_values_bucketed → 數值標籤分桶後也落 Bitmap（如流失分 0–20/20–40/…）
```
- `one_id_uint` 為 **稠密整數 ID**（OneID 服務分配，單調遞增），讓 RoaringBitmap 壓縮率最高；`cityHash64` 僅用於分片。
- 版本化：每次計算產生新 `version`，查詢取最新；保留 7 個版本供對比與回滾。

### 6.4 效能目標（1,500 萬人、2,000 個標籤值）
| 作業 | 目標 |
|---|---|
| 全量標籤日批（統計 + 規則） | < 60 分鐘（Spark/CH SQL，分區並行） |
| 單一規則標籤重算 | < 2 分鐘 |
| 分群預估（5 個 Bitmap 交併差） | < 300 ms |
| 標籤值分布查詢 | < 1 s |

---

## 7. 跨模組共通
- 所有模組的查詢參數皆為語意層 JSON，可被 agent 工具（`metric.query`、`funnel.run`、`path.run`、`attribution.run`、`segment.preview`）原樣使用；UI 的「複製為 agent 查詢」即輸出此 JSON。
- 所有結果附 `sql`、`cost`、`lineage`、`warnings`（抽樣、近似去重、資料未到齊）。
- 任何「人」的落點（流失、觸點、節點、數據點）都能一鍵進入用戶列表，再進入細查（需理由）。

## 8. 驗收（每模組）
1. 功能清單逐項 UI 走查。
2. 口徑一致性：UI 與 agent 工具對同參數回傳相同數字。
3. 效能：在 100 億事件、1,500 萬人的測試集上達到本文件目標。
4. 權限：營業員身份對每個模組的行級過濾生效；匯出走審批。
5. 審計：每次查詢寫 `query.executed`（人）或 `agent.tool_called`（agent）。
