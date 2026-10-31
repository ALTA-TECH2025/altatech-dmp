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

| 模組 | 對標（舊平台） | 對標（V 平台 V-埋點分析） | 依賴 |
|---|---|---|---|
| 事件分析 | 事件分析 | 事件分析 | 語意層、事件表、日聚合 MV |
| 漏斗分析 | 漏斗分析 | 轉化分析 | 事件表（依 one_id 分片）、Bitmap 預篩 |
| 行為路徑分析 | 用戶路徑 | 路徑分析 | 事件表、Session 化、抽樣 |
| 歸因分析 | 歸因分析 | 歸因分析 | 觸點表（預計算）、轉化事件 |
| 用戶列表 | 用戶列表／用戶分群 | 用戶細查列表 | 用戶表、標籤 Bitmap、OneID |
| 標籤能力 | 用戶畫像（標籤） | V-CDP 標籤 | 標籤引擎（Bitmap）、排程、血緣 |

---

## 0a. 事件命名：對齊客戶舊平台專案的模擬事件清單（2026-10-15）

客戶提供了舊平台專案 `sbp_family` 的模擬事件清單（112 個元事件：事件名、顯示名、平台、觸發時機）。本系統的事件字典 `backend/semantic/events.yaml` v4 與原型「元事件管理」改以該清單為準（117 個 = 112 + 交易／帳務 CDC 補充 `fund_deposit`、`order_rejected`、`order_cancelled` + 示範用隔離／草稿事件），依前綴歸為業務線。

> 2026-10-19 補充：客戶再提供舊平台埋點規格表（自訂事件 148 個、全埋點 29 個、用戶表 184 欄，只有欄位定義）。字典補上其中 147 個未登記的自訂事件（共 264 個）：94 個走 GA360 埋點、34 個數據中台批次、13 個業務系統、6 個 MA；標 (X)／已下線／過期活動的 17 個為 deprecated，只匯入歷史、不列入 GA 埋點規格。用戶表比導出多的 36 欄見 `backend/migration/legacy_user_properties.csv`（family=design_only）。

本專案各文件中原本使用的事件名改為 **alias**（語意層查詢時等價），對照如下：

| 文件舊名（alias） | 事件名（客戶清單） | 顯示名 |
|---|---|---|
| `ga.page_view` | `$pageview` | Web 瀏覽頁面 |
| `ga.screen_view` | `$AppViewScreen` | App 瀏覽頁面 |
| `quote_view` | `hq_stockdetail_view` | 行情_個股明細瀏覽 |
| `search_symbol` | `search_submit` | 搜索_送出搜索請求 |
| `watchlist_add` | `deal_opt_mani` | 交易_自選操作 |
| `login` | `app_login_success` | APP登入成功 |
| `account_open_start` | `kh_entry_khpg_pv` | 開戶_開戶首頁瀏覽 |
| `account_open_submit` | `kh_apply` | 開戶_完成開戶申請 |
| `account_open_approved` | `kh_review_pass` | 開戶_審核成功 |
| `order_submit` | `bond_order` | 證券_委託 |
| `order_filled` | `bond_deal` | 證券_成交 |
| `fund_subscribe` | `sip_purchase` | 定期定額_申購結果 |
| `push_opened` | `push_click` | Push點擊 |
| `campaign_clicked` | `act_click` | 活動_活動頁按鈕點擊 |

| 業務線 | 事件數 | 例 |
|---|---|---|
| 智能運營（MA） | 16 | `PlanEDMClick`、`PlanEDMShow`、`$PlanSMSArrived`、`$PlanAudienceMarked`、`$JourneyExit`、`$JourneyComponentEnter` … |
| 短鏈 | 2 | `ShortUrlRedirect`、`$ShortUrlRedirect` |
| 業務資料（服務端） | 5 | `Market_Data`、`operation_target`、`market_deal_profile`、`cancel_profile`、`option_apply` |
| 廣告渠道 | 3 | `campaign_callBack`、`act_ad_view`、`act_ad_cost` |
| 登入與導航 | 6 | `test_login`、`my_agreement_sign`、`mainlogin_button_click`、`lower_button_click`、`home_click`、`app_login_success` |
| 交易（App） | 3 | `stock_order_submit`、`deal_opt_mani`、`deal_commision_check` |
| 定期定額 | 8 | `sip_stockpage_view`、`sip_resultpage_button_click`、`sip_purchase`、`sip_listpage_view`、`sip_change_confirm`、`sip_cancel_order` … |
| 搜索 | 3 | `search_submit`、`search_result_click`、`search_box_click` |
| 證券（交易主機） | 9 | `realtime_bond_order`、`realtime_bond_deal`、`bond_portfolio_income`、`bond_portfolio`、`bond_order`、`bond_deal` … |
| Push | 3 | `push_received`、`push_click`、`$AppPushClick` |
| LINE | 6 | `line_unfollow`、`line_sendline_message`、`line_message`、`line_follow`、`line_account_unlink`、`line_account_link` |
| 開戶 | 27 | `kh_steppg_pv`、`kh_steppg_click`、`kh_sign_result`、`kh_sign`、`kh_set_sbank_result`、`kh_set_sbank` … |
| 資訊與資源位 | 4 | `info_report_view`、`info_recomstock_click`、`info_detail_view`、`i_mkt_click` |
| 行情 | 3 | `hq_stockdetail_view`、`hq_stockdetail_click`、`hq_ai_choose` |
| 申購／競拍 | 8 | `auc_stockpage_view`、`auc_resultpage_view`、`auc_purchase_result`、`auc_purchase`、`auc_listpage_view`、`auc_cancel_order` … |
| 活動 | 2 | `act_pageview`、`act_click` |
| App 基礎 | 5 | `AppInstall`、`$AppViewScreen`、`$AppStart`、`$AppEnd`、`$AppClick` |
| Web 基礎 | 2 | `$pageview`、`$WebClick` |
| 帳務（CDC） | 1 | `fund_deposit` |
| 未登記 | 1 | `ga.ab_exposure` |

說明：
- 清單裡的 `$`開頭事件是舊平台預置事件（`$pageview`、`$AppViewScreen`、`$AppStart`…）與智能運營系統事件（`$Plan*`、`$Journey*`）；本系統保留原名，來源分別對應 GA360（Web／App）與 MA 系統事件。
- 證券交易（`bond_order`／`bond_deal`／`realtime_bond_*`）與市場資料（`Market_Data`）是服務端事件，本系統由交易主機 CDC 事件化產生；App 端的 `stock_order_submit`（交易_委託送出）是行為事件，兩者不逐筆對應（docs/20）。
- 開戶流程 `kh_*` 共 27 個事件，是開戶漏斗的素材；`kh_review_pass` 對應原「開戶核准」，是留存與 LTV 的 cohort 事件。

## 0b. 對照舊平台 v3.0 使用指南的補齊清單（2026-10-15，原型 v0.6.1）

依 guide_analytics_event／funnel／retention／path／attribution 原文逐項對照後補齊：

| 模組 | 補齊項目 |
|---|---|
| 事件分析 | 指標：數值人均值、下／上四分位、P90、預定義指標（當月觸發用戶數，僅按天）；自定義指標（指標代號四則運算，百分比／兩位／三位小數／取整，可命名，表達式自動校驗）；兩層嵌套篩選；「事件發生時間」時段（0–23）篩選；分組：數值自訂區間、分群／標籤的數據結果（最新結果／動態匹配／自定義版本）；靜態／動態時間；時間對比（上一段／去年同期／自定義）；合計（快速總和／精準合計）；精確／近似計算（HLL ±2%）；取消查詢；圖表：線／柱／餅／累積，柱狀的數值／百分比與「以分組維度組合為橫坐標」；表格：分層／平鋪、列占比、子合計、環比增長率、Excel 匯出（匯總／明細／橫向日期／透視表條件）；存為書籤／保存至概覽 |
| 漏斗分析 | 漏斗類型：按人數／按次數（計算規則各自說明）；步驟最多 64、步驟別名、每步一個同時顯示指標；窗口期 1 分鐘～3650 天；篩選四類（各步事件屬性、用戶屬性、分群、標籤，分群可動態匹配）；單分組／多分組；事件發生時間必選分組與小時／天／週／月粒度；限制後續事件在時間區間內；對比時間（上一段／去年同期／自定義）；漏斗模板（保存為模板／模板管理／應用）；匯總配置（去重轉化／加權轉化）；圖表：漏斗圖／折線圖／對比圖（≤4）；表格：透視表（展示分組匯總）／二維表、顯示設定；漏斗內外篩選差異與 FAQ 口徑寫進計算規則說明 |
| 留存分析 | 四種模型：特定日期留存、自定義區間留存（左閉右開區間）、連續留存（可含當日）、流失；初始／後續行為篩選與關聯屬性；用戶篩選（用戶屬性、分群／標籤最新或動態匹配、時間型用戶屬性相對初始／後續行為時間）；同時顯示指標（總和／人均／累計總和／階段累計人均值 = LTV）；分組：初始行為時間固定分組、初始／後續事件屬性、用戶屬性、分群／標籤、數值自訂區間、單分組／多分組；窗口期「查看所有」或「僅關鍵窗口（1,3,7）」；匯總配置（加權／去重）；顯示設定（留存率／人數／同時顯示、顯示分組匯總）；格子點擊：用戶列表／添加分群／用戶畫像；留存變化圖／趨勢圖 |
| 用戶路徑 | 數值為會話數（預設）；每事件最多一個分組；虛擬事件分流優先級（起止事件 › 虛擬事件 › 元事件）；Session 間隔切割規則；高亮當前路徑；節點詳情（事件名、分組屬性值、合計、後續事件統計、流失、後續事件列表含會話數與用戶列表下鑽）；導出桑基圖；可配置路徑步數 |
| 歸因分析 | 轉化指標：事件總次數或數值屬性總和；前向關聯條件可多個（或）＋關聯屬性；轉化指標分組（含事件發生時間按小時／天／週／月）；歸因事件分組：公共分組／單事件分組（每屬性一列／合併為分組集合）；用戶篩選與全局篩選分開；歸因窗口期：當天／自定義（1 天 vs 24 小時差異）；結果表依數據口徑：全量歸因事件總次數／用戶數、歸因事件總次數／用戶數、有效歸因事件總次數／用戶數／占比、轉化指標、轉化事件用戶數（僅首次／末次）、轉化率、貢獻度；透視表／二維表與顯示設定 |

未納入（舊平台標為進階或與本系統範圍無關）：多時區、跨專案查詢、業務集市報表。

## 1. 事件分析

### 1.1 功能
- 指標：`count`、`uniq_users`、`sum/avg/max/min/p50/p90(field)`、`per_user_avg`、比率（分子／分母）、事件組合指標（A+B、A/B）。
- 時間：分鐘（近 24h）／小時／日／週／月；同比、環比；自訂區間。
- 篩選：事件屬性、使用者屬性、標籤值、分群；AND/OR 巢狀條件；指標內篩選（每個指標各自的條件）。
- 行為篩選「用戶做過／未做過」：與用戶列表／分群同一套 `behaviors`（事件 × 指標 × 比較 × 期間 × 事件屬性條件，最多 10 條、且／或），只算符合用戶的事件；一律走明細層。漏斗、留存同一套（只作用於進漏斗／進同期群的用戶）。
- 對照組 `cohorts`（最多 10 組）：每組一套篩選（屬性／分群／行為），與全局篩選取交集後各算一次，結果多一欄 `cohort`；可與「對比上期」並用。漏斗、留存同一套。
- 分組：最多 3 個維度；Top N + 其他；分組值可搜尋。
- 視覺：折線、面積、長條、堆疊、百分比堆疊、圓餅、表格（行列轉置、增長％、佔總％）；數值格式（取整／兩位小數／百分比／貨幣）；圖例全選／反選；多指標對比；異常點標記（P1 接異常偵測）。看板卡片存 `chart` 與 `format`，每張卡可臨時改期間與粒度。
- 儲存與分享：存為看板卡片、匯出 CSV（審批）、複製為 agent 可讀的語意查詢 JSON。
- 細查入口：任一數據點 → 該日該分組的用戶列表。

### 1.2 API
`POST /v1/analytics/events`，請求體 = `SemanticQuery`（見 openapi.yaml）。回應含 `rows`、`chart_spec`、`sql`、`cost`、`warnings`。

### 1.3 ClickHouse 實作
- 指標為 count／sum(amount) 且欄位在物化視圖鍵內：走 `events_daily_agg`（AggregatingMergeTree）；用戶數需精確時走明細。
- 分鐘／小時粒度或有事件屬性篩選：走 `events` 明細，`PREWHERE event IN (...) AND event_date BETWEEN`。
- `uniq_users` **預設精確**（`uniqExact`，不抽樣；評分表要求大量資料可不抽樣）；使用者打開「快速估算」才用 `uniqCombined64` 或按用戶抽樣（`SAMPLE`），結果標示。黃金測試以精確值比對（docs/26 衝突 1）。
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
- 分組對比（維度或分群）、對照組（`cohorts`，最多 10 組）、行為篩選（`behaviors`，用戶做過／未做過）、時間趨勢（每日轉化率）、對比前期。
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
- 抽樣：對 > 1 億人的群體以 `SAMPLE 1/10`（表定義 `SAMPLE BY distinct_id_uint`，按裝置分桶）給出近似，UI 標示。

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
- 一律抽樣（預設 1/10，可調 1/1 到 1/100），舊平台路徑亦採抽樣；UI 標示抽樣比例與推估人數。
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
- 權限：行銷只看隱碼列表、不可匯出；數據分析／數位經營可匯出（審批）；資訊管理不可進入；營業員不是本系統使用者（所屬營業員只是顯示欄位）。

### 5.2 實作
- 人數預估：各條件各自產生 Bitmap（標籤：直接讀；行為：`groupBitmapState` 子查詢，日粒度預聚合 `user_event_daily`），交併差後 `bitmapCardinality`。
- 列表：`bitmapToArray` 取分頁的 `one_id_uint` 範圍 → JOIN `dmp.users`（`ORDER BY one_id_uint`，分頁用 `WHERE one_id_uint > last`）。
- `user_event_daily`：`(one_id_uint, event_date, event) → count, sum_amount`，AggregatingMergeTree；行為條件 90% 命中此表。

### 5.3 效能目標
人數預估 P95 < 1 s（3 條件以內）；列表翻頁 < 500 ms。

---

## 6. 標籤能力

> v0.3：分群與標籤的產品功能（創建方式、更新與版本、回收站、血緣、審批、權限）與自定義 SQL 依舊平台文件重做，見 docs/21；本節保留標籤的計算與儲存設計。

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
4. 權限：五種角色對每個模組的功能權限與欄位隱碼生效（行銷區間化、數據分析明文、資訊管理拒絕用戶列表）；匯出走審批。
5. 審計：每次查詢寫 `query.executed`（人）或 `agent.tool_called`（agent）。
