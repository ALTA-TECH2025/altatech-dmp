# 資料來源新鮮度設定（即時／日批對照表）

- 狀態：**研發提案，待審**。方向已由 Windy 2026-10-31 決定：「哪些來源是即時、哪些是日批」要能在系統管理或數據源管理設定。放在數據源管理（Windy 2026-10-31 同意，理由見 §3），其餘細節待 §8 確認。
- 起因：#121 的 `data.readiness` 對所有來源都套 `backend/ops/sla.yaml` 的 `behavior_events_min: 15`。日批來源（例如 `ga360_daily`、建模任務）白天最後一筆事件本來就是前一天，因此整天都會被標成 `late`。
- 契約：本 PR 改 `backend/openapi.yaml`（新端點、`SourceFreshness*`、`Readiness` 欄位）與 `backend/mcp/tools.yaml`（只改 `data.readiness` 的說明文字）。後端與前端在契約審過後各開一個 PR。

## 1. 要解決什麼

`data.readiness` 要能分辨兩種情況：

1. 來源真的晚了（即時來源停住、日批過了到齊時間還沒進來）。
2. 來源依設計還沒到（日批白天只有到前一天）。

做法是讓運維按來源設定模式與門檻。**沒設定的來源一律回「未知」，不猜預設值。**

## 2. 每個來源設定什麼

來源 = `events.source` 的值，例如 `legacy_format`、`ga360_intraday`、`ga360_daily`、建模任務名稱 `mdl_*`。格式 `^[a-z0-9_]{1,64}$`。

| 欄位 | 即時 `realtime` | 日批 `daily_batch` | 說明 |
|---|---|---|---|
| `mode` | 必填 | 必填 | 二選一 |
| `max_lag_minutes` | 必填，1–1440 | **不可帶** | 最後一筆事件距今超過幾分鐘算延遲 |
| `ready_at` | **不可帶** | 必填，`HH:MM`（00:00–23:59） | 每日到齊時間，固定 Asia/Taipei |
| `covers_days_back` | **不可帶** | 必填，1–7 | 到齊時間之後應涵蓋到 T-n；之前應涵蓋到 T-(n+1) |
| `note` | 選填，≤ 200 字 | 選填 | 給人看的備註（例如上游排程名稱） |

- 時區固定 `Asia/Taipei`，請求不可帶 `timezone`（回應會帶，方便畫面顯示）。台北沒有日光節約時間。
- 模式與欄位不成對（例如即時帶了 `ready_at`，或日批缺 `covers_days_back`）回 400 `INVALID_ARGUMENT`。這條規則寫在 schema 裡，CI 契約工作會機器驗證（1 個正例、9 個負例）。
- 例：`ga360_daily` 每天 07:00 前進前一天的資料，設定為 `{mode: daily_batch, ready_at: "07:00", covers_days_back: 1}`。06:59 時應涵蓋到 T-2，07:00 起應涵蓋到 T-1。

## 3. 放在哪一頁

**已決定：數據源管理（`web/src/integration/IntegrationPage.tsx`），每個來源一列（Windy 2026-10-31）。**

1. 新鮮度是資料來源本身的性質，跟連線、權限自檢、密鑰輪替放在同一處，運維找得到。
2. 權限現成：`integration.admin` 本來就只有 `it_admin` 有，不用新增角色或動作。
3. 系統管理的 `/v1/settings` 是唯讀的平台設定檢視，沒有寫入端點。放那裡要另開寫入路徑與權限，反而多一套。

系統管理頁之後若要「看」，可以唯讀引用同一個 GET，不另存一份。

## 4. 權限、審計、寫入行為

| 動作 | 端點 | 權限（沿用 `backend/security/rbac.yaml`） | 審計事件 |
|---|---|---|---|
| 看清單 | `GET /v1/integration/source-freshness` | `integration.view`（it_admin、data_analyst、digital_ops） | 不記 |
| 設定／修改 | `PUT /v1/integration/source-freshness/{source}` | `integration.admin`（it_admin） | `integration.freshness.updated`，記修改前後的值 |
| 清除 | `DELETE /v1/integration/source-freshness/{source}` | `integration.admin` | `integration.freshness.cleared`，記清除前的值 |

- 不新增角色、模組或錯誤碼。錯誤碼都是 `backend/api/errors.yaml` 現有的：`INVALID_ARGUMENT`、`AUTHZ_DENIED`、`NOT_FOUND`。
- 個資：設定內容只有系統代號、數字與時間，不含個資，畫面不需隱碼。
- PUT 是整筆取代：切換模式時，舊模式的欄位會一起清掉。同樣內容重送結果相同（冪等），但每次成功都記審計。
- 並行寫入：後寫的覆蓋先寫的，不做版本鎖。兩次寫入都有審計可查。
- 可以預先設定還沒有資料的來源（例如上線前先設好），GET 會列出，`last_event_time` 為 null。
- 清單：GET 回傳「已設定的來源」，加上「近 30 天 `events.source` 出現過但沒設定的來源」（`configured=false`）。新來源自然會出現在清單裡等人設定。
- 存放：控制面資料庫，與連線設定同一處。表結構由後端 PR 決定。

## 5. readiness 怎麼用設定

每個來源先取區間內最後一筆事件時間 `max_event_time`，「現在」以台北時間計。

| 情況 | `status` | 其他欄位 |
|---|---|---|
| 未設定 | `unknown`，並在 envelope 加 `READINESS_PARTIAL` 警示 | `mode`、`expected_lag_minutes`、`expected_through` 都是 null |
| 區間開始後完全沒資料（已設定） | `missing` | 同 #121 |
| 即時：`現在 − max_event_time ≤ max_lag_minutes` | `ok`；超過就是 `late` | `expected_lag_minutes = max_lag_minutes`，`lag_minutes` 照算 |
| 日批：`max_event_time` 的台北日期 ≥ `expected_through` | `ok`；早於就是 `late` | `expected_through` = 到齊時間前 `今天 − (n+1)`、之後 `今天 − n` |

**依日期看是否到齊（`dates[]`）**

- 即時來源：`max_event_time` ≥ 該日台北 24:00，就算該日到了。
- 日批來源：`max_event_time` 的台北日期 ≥ 該日，就算該日到了（前提是日批一次進一整天，見 §8 第 6 項）。
- 有任何未設定的來源時，`complete = null`，並把它們列在 `unknown_sources`。不可當成「到齊」。
- 「依設計還沒到」跟「延遲」分開看：白天的今天，日批來源會出現在該日的 `missing_sources`（事實：今天的資料還沒有），但 `sources[].status` 是 `ok`（管線準時）。

**初始狀態**：上線時沒有任何設定，所以所有來源都是 `unknown`，readiness 帶 `READINESS_PARTIAL`。這是刻意的：運維填完設定之前，系統回答「不知道」，而不是「良好」或「延遲」。

**`sla.yaml` 的 `freshness` 數值**（`behavior_events_min: 15`、`daily_batch_deadline: "07:00"`）：保留作為服務目標的文件，readiness 不再讀它，也不拿它當未設定來源的預設值。

## 6. 契約變更摘要（本 PR）

- 新增 `GET /v1/integration/source-freshness`、`PUT|DELETE /v1/integration/source-freshness/{source}`。
- 新增 schema `SourceFreshnessUpdate`（`additionalProperties: false`，用 if/then 綁定模式與欄位）和 `SourceFreshness`（回應）。
- `Readiness.sources[]` 加 `mode`、`expected_through`；`expected_lag_minutes` 改為可 null；`status` 加 `unknown`。
- `Readiness.dates[]` 的 `complete` 改為可 null，並加 `unknown_sources`。
- `/v1/data/readiness` 與 `tools.yaml` 的 `data.readiness` 說明補上 unknown 規則。
- CI 契約工作加入 `SourceFreshnessUpdate` 的正例與負例。

不在本 PR：後端儲存與 readiness 改寫（#121 分支要跟著改）、數據源管理頁的設定表單。`Readiness.backfill` 在 #121 會回 null，但 schema 不是 nullable。這是另一個既有落差，本 PR 不處理，留給 #121 修。

## 7. 實作順序

1. 本契約 PR（含本規格）。
2. 後端：儲存、三個端點、審計、`data.readiness` 改用設定；自測涵蓋：未設定 → unknown；即時 ok／late；日批在到齊時間前後的邊界（06:59／07:00）；跨午夜；模式切換清欄位。
3. 前端：數據源管理頁加「新鮮度」欄與設定對話框，未設定的來源醒目標示。

## 8. 待確認

| # | 問題 | 誰拍板 | 未確認前的做法 |
|---|---|---|---|
| 1 | 每個來源實際是哪種模式、門檻與到齊時間 | 運維／客戶 | 全部未設定，readiness 回 unknown |
| 2 | 日批遇到週末、國定假日、交易日曆時，`expected_through` 要不要跳過非營業日 | 運維＋客戶 | 一律以日曆日計；遇假日可能誤標 late |
| 3 | `late` 要不要觸發運維告警（與 `backend/ops/sla.yaml` 告警串接） | 運維 | 只在 readiness 呈現，不告警 |
| 4 | 放數據源管理還是系統管理 | Windy | **已決定（2026-10-31）：數據源管理** |
| 5 | 建模任務（`mdl_*`）要逐個任務設定，還是用前綴一次設定 | 研發＋運維 | 逐個 `events.source` 值設定，不支援萬用字元 |
| 6 | 日批是否可能只進半天（分段載入）；若會，日期到齊要改看別的訊號 | 運維 | 假設一次進一整天 |
| 7 | `sla.yaml` 的 `freshness` 數值要保留為文件還是移除，避免兩處不一致 | 運維 | 保留，readiness 不讀 |
| 8 | 一天多批（例如每 6 小時）是否需要第三種模式 | 運維 | 先只有即時／日批；這類來源可暫用即時＋較大門檻 |
