# 46 — 錯誤碼與報警設計

> 狀態：**錯誤碼為契約（本 PR 定案）**；報警分級（§5）是設計標準，分級對應的通知管道與回應時限是**建議值，待運維確認**。
> 契約檔：`backend/api/errors.yaml`（錯誤碼目錄）、`backend/openapi.yaml` 的 `components.schemas.Error`、`components.headers.XRequestId`。CI contract job 會檢查三處一致。

## 1. 為什麼要做（2026-10-25 盤點）

| 項目 | 現況 | 問題 |
|---|---|---|
| REST API 錯誤 | 後端約 280 處 `HTTPException`，只回一句中文 `detail` | 前端與客戶系統只能比對文字；改一個字就壞 |
| 下游錯誤 | 查詢、建表、舊平台失敗時把原始例外 `str(e)` 回給使用者 | 可能洩漏 SQL、主機名、內部結構 |
| 追蹤 | 沒有請求編號 | 使用者回報問題時，運維對不到日誌 |
| AI 工具錯誤 | `tools.yaml` 已有 12 個標準碼與可否重試 | 和 REST 是兩套，沒有共用 |
| 報警 | 應用層 11 條（`backend/ops/alerts.yaml`）＋ Docker 主機層 6 條 | 嚴重度兩套寫法；主機層沒有處置說明；缺 PostgreSQL、備份、同步等報警 |

## 2. 錯誤回應格式

所有非 2xx 回應（202 送審除外）一律是：

```json
{
  "detail": "你沒有執行這個操作的權限",
  "code": "AUTHZ_DENIED",
  "retryable": false,
  "request_id": "req-01HZX3K9",
  "params": { "permission": "events.export" },
  "errors": [ { "field": "body.steps[2].event", "code": "NOT_FOUND", "message": "事件不存在" } ]
}
```

- `detail`：給人看的一句話。沿用 FastAPI 原本的欄位名，**舊前端只讀 `detail` 不用改**。
- `code`：固定代碼，程式依它決定行為（§4.6）。
- `retryable`：同一個請求稍後重送是否可能成功。
- `request_id`：每個回應的 `X-Request-Id` 標頭也帶同一個值。
- `params`：組訊息用的值（上限、權限名、被誰引用的前 5 筆）。不得含個資或密鑰。
- `errors`：欄位層級錯誤，用在參數驗證和規則檢查。

## 3. 錯誤碼目錄

完整內容（HTTP 狀態、預設訊息、建議下一步、日誌等級）在 `backend/api/errors.yaml`。

| 類別 | 代碼 |
|---|---|
| 呼叫端可修正 | `INVALID_ARGUMENT`、`RULE_VIOLATION`、`PII_REJECTED`、`PAYLOAD_TOO_LARGE`；工具專用 `AMBIGUOUS`、`CONTRACT_MISMATCH` |
| 身分 | `UNAUTHENTICATED`、`TOKEN_INVALID`、`OTP_INVALID`、`ACCOUNT_LOCKED`、`CSRF_INVALID`、`STEP_UP_REQUIRED`、`AUTHZ_DENIED` |
| 資源狀態 | `NOT_FOUND`、`GONE`、`CONFLICT`、`IN_USE`、`NOT_APPROVED`、`FOUR_EYES_REQUIRED` |
| 容量 | `RATE_LIMITED`、`BUDGET_EXCEEDED`、`DATA_NOT_READY` |
| 下游 | `TIMEOUT`、`NOT_CONFIGURED`、`DEPENDENCY_UNAVAILABLE`、`UPSTREAM_FAILED`；工具專用 `MODEL_ENDPOINT_NOT_CONFIGURED` |
| 平台 | `INTERNAL` |

REST 用 25 個，AI 工具用 12 個（`tools.yaml` 原有的全部保留，代碼與可否重試不變），兩者共用 9 個代碼。

## 4. 規則

### 4.1 選碼
先選最具體的代碼，沒有才用通用的。例如：刪除仍被引用的連線用 `IN_USE`，不用 `CONFLICT`；L4 欄位被拒用 `PII_REJECTED`，不用 `RULE_VIOLATION`。新增代碼要走 contract PR，只能加在目錄後面。

### 4.2 訊息寫法
- 寫給業務使用者看：說發生什麼、下一步怎麼做（`hint`）。
- 不寫內部錯誤原文、SQL、表名以外的結構、主機名、堆疊、個資。
- `NOT_FOUND` 與沒有權限看起來一樣（「找不到這筆資料，或你沒有檢視權限」），不透露資料存不存在。

### 4.3 不洩漏內部錯誤
下游失敗（`UPSTREAM_FAILED`、`DEPENDENCY_UNAVAILABLE`、`INTERNAL`）只回目錄訊息與 `request_id`。原始例外寫在後端日誌，以 `request_id` 查。

### 4.4 請求編號
- F5 或前端帶來的 `X-Request-Id` 符合 `^[A-Za-z0-9._-]{8,64}$` 就沿用，否則 API 產生。
- 寫進每一行應用日誌（Loki 可用 `request_id` 查）與審計事件。
- 前端錯誤畫面顯示「回報代碼：req-…」，使用者截圖或複製給客服。

### 4.5 日誌與審計
- `log: info` 的錯誤（使用者可修正）不記錯誤堆疊，避免洗版。
- `log: error` 的錯誤記完整原因並附 `request_id`。
- 403 已經寫 `authz.denied` 審計；之後一併記 `code`。

### 4.6 前端行為
| 代碼 | 前端行為 |
|---|---|
| `UNAUTHENTICATED` | 導向登入頁，登入後回到原頁 |
| `STEP_UP_REQUIRED` | 跳出再次驗證，完成後自動重送 |
| `CSRF_INVALID` | 提示重新整理頁面 |
| `retryable: true` | 顯示「重試」按鈕 |
| `errors[]` 有值 | 對應欄位標紅並顯示訊息 |
| 不認得的代碼 | 顯示 `detail` 與回報代碼 |

### 4.7 例外對應
參數驗證失敗 → `INVALID_ARGUMENT`（欄位錯誤放 `errors[]`）；`PermissionError` → `AUTHZ_DENIED`；`ValueError` → `INVALID_ARGUMENT`（只回目錄訊息）；逾時 → `TIMEOUT`；其他未處理例外 → `INTERNAL`。

## 5. 報警分級標準（第 4 個 PR 實作）

### 5.1 四級
| 等級 | 定義 | 例子 | 通知管道（建議，待運維確認） | 回應時限（建議，待運維確認） |
|---|---|---|---|---|
| critical | 服務中斷或資料可能遺失 | 主機掛、Keeper 失去法定人數、PostgreSQL 主庫掛、事件停滯 | LINE＋Email，靜音時段也送 | 15 分鐘內有人接手 |
| high | 功能受損，使用者會感受到 | 同步失敗、API 5xx 升高、推送進死信、備份失敗 | Email（上班時段加 LINE） | 當日處理 |
| medium | 逐漸惡化，還沒影響使用者 | 查詢變慢、隔離率升高、磁碟剩 15% | Email 彙整 | 3 個工作天內 |
| low | 參考資訊 | 單次重試成功 | 只在資料流與效能頁顯示 | 不需回應 |

應用層與主機層統一用這四級；主機層現有的 `warning` 改成 `medium` 或 `high`。

### 5.2 每條報警必備
`key`（唯一）、`severity`、`summary`（帶對象與數值）、`runbook`（處置步驟）、`for`（持續多久才發）、去重鍵（同一對象 60 分鐘內不重發，沿用 `notify.yaml`）。沒有處置步驟的報警不上線。

### 5.3 要補的報警
| 報警 | 建議等級 |
|---|---|
| PostgreSQL 主庫掛／複寫落後 | critical／high |
| Redis 主從切換 | medium |
| 備份失敗（ClickHouse、PostgreSQL、rclone） | high |
| 主機 1 → 主機 2 的資料同步失敗 | high |
| 憑證 30 天內到期 | medium |
| Keycloak 掛 | critical |
| SeaweedFS master 少於 2 台／volume 掛 | high |
| Loki／Alloy 停止收日誌 | medium |
| 同時有兩份 worker 在跑（主控租約異常） | critical |

另修：「事件停滯」的處置說明拿掉 Kafka（第一階段不交付，docs/45 D-04）。

## 6. 後續 PR

| PR | 範圍 | 驗收 |
|---|---|---|
| 2 後端 | `X-Request-Id` 中介層；`ApiError` 與例外處理統一輸出 Error；下游失敗不回原始例外；常用模組先換代碼，其餘保留 `detail`、統一補 `code`（依 HTTP 狀態對應通用碼） | 自測：每種代碼的回應符合 Error 結構；502／500 回應不含例外原文；日誌查得到 `request_id` |
| 3 前端 | API 客戶端讀 `code`，依 §4.6 處理；錯誤畫面顯示回報代碼 | vitest：各代碼的行為；Playwright：登入逾時導回登入 |
| 4 報警 | 四級統一；補 §5.3；每條附處置步驟；分級通知依運維確認結果設定 | Prometheus 規則載入驗證；`/v1/ops/alerts` 自測 |
