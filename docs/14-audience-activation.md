# 受眾推送（Audience Activation）：Webhook 到內部系統、GA4／Google Ads 再行銷受眾包

> 版本 v0.4（2026-10-14）。算好的標籤與受眾包（分群快照）要能主動推送到：① 內部數據系統（Webhook、Kafka、GCS）；② GA4 受眾 → Google Ads 再行銷。推送一律經審批、套隱碼、寫審計。**Google Ads Customer Match 不在本期範圍**（見 §5）。

## 1. 物件模型

```
受眾包 Audience Package
  ├─ 來源：分群（規則／上傳／即時）或「標籤值組合」
  ├─ 版本：每次日批或即時更新產生 version；保留 7 版，可算 delta（加入／移除）
  ├─ 識別子對應：one_id → customer_code（代碼化）／ GA client_id・app_instance_id
  ├─ 同意過濾：僅含「同意行銷」且（對外目的地）「同意廣告再行銷」的客戶
  ├─ 目的地 Destination（多個）：webhook | kafka | gcs | ga4_audience
  ├─ 排程：日批完成後自動、固定時間、即時（分群成員進出即推）、手動
  ├─ 審批：首次綁定目的地與每次全量推送需營運主管核准；增量推送依既核准的排程自動執行
  └─ 交付紀錄 Delivery：批次、筆數、狀態、重試、下游回應；審計 sync.delivered
```

## 2. 目的地類型

| 類型 | 用途 | 識別子 | 傳輸 | 隱碼 |
|---|---|---|---|---|
| **Webhook（內部系統）** | CRM、行銷系統（MA）、數倉 | `customer_code`（HMAC 代碼化）+ `one_id`；可選附帶標籤值 | HTTPS POST、HMAC-SHA256 簽章、冪等、重試、DLQ | 依目的地合約：L2 數值區間化或排除 |
| Kafka | 內部即時消費 | 同上 | `dmp.audience_updates`、`dmp.tag_updates`（Avro） | 同上 |
| GCS Parquet | 數倉、大量 | 同上 | `gs://dmp-sync/audiences/<id>/dt=…/` + manifest | 同上 |
| **GA4 受眾（→ Google Ads 再行銷）** | 把受眾包回流到 GA4，讓 GA4 受眾同步到已連結的 Google Ads 做再行銷 | `client_id`（Web）／`app_instance_id`（App），來自 GA360 匯出的 `user_pseudo_id`；可附 `user_id`（HMAC 代碼） | **GA4 Measurement Protocol**：送事件 `dmp_audience` + user property `dmp_audience_<id>=1`（移除時設為 0），GA4 端以該 user property 建立受眾並勾選「Google Ads 連結」 | **不含任何個資**：只用 GA 自己的匿名 ID；省去 Customer Match 的個資上傳問題 |

### 2.1 為什麼 GA4 受眾回流是預設做法
- GA360 匯出已經有每個事件的 `user_pseudo_id`（即 `client_id`／`app_instance_id`），DMP 的 OneID 本來就把它與客戶打通，所以可以**以匿名 ID 回推**，不需要任何個資。
- GA4 受眾與 Google Ads 連結後自動同步，可直接用於再行銷、排除、相似受眾（Ads 端功能），也可在 GA4 做成效報表。
- 限制：Measurement Protocol 的 user property 名稱上限（自訂 25 個）→ 以 `dmp_audience_<id>` 命名時，同時活躍的受眾包建議 ≤ 20 個，或改用單一 user property `dmp_audiences` 存逗號清單並在 GA4 以「包含」條件建受眾；GA4 受眾成員資格最長 540 天；MP 事件不會回填歷史；需 GA4 `api_secret`（存 Secret Manager）。
- 若客戶沒有 Google Ads 與 GA4 連結，或需要觸及未裝 GA 的客戶，才需要 Customer Match（本期不做，見 §5）。

## 3. Webhook 規格（內部系統）

```
POST {destination.url}
Headers:
  Content-Type: application/json
  X-DMP-Event: audience.delta | audience.full | tag.update | audience.test
  X-DMP-Delivery-Id: dlv_01J…            （冪等鍵；重試沿用同一個）
  X-DMP-Timestamp: 1760400000            （Unix 秒；接收端拒絕 ±5 分鐘外）
  X-DMP-Signature: sha256=<hex HMAC-SHA256(secret, timestamp + "." + body)>
Body:
{
  "audience_id": "sg1", "audience_name": "高價值沉睡客", "version": 42, "computed_at": "2026-10-14T02:41:00Z",
  "mode": "delta", "batch": {"index": 3, "total": 12, "size": 5000},
  "identifier": "customer_code",
  "members": [
    {"customer_code": "cust_8f3a…", "one_id": "oid_7k3a9q", "action": "add",
     "tags": {"lifecycle": "沉睡", "fee_tier": "P1", "churn_score_bucket": "high"}},
    {"customer_code": "cust_21cc…", "one_id": "oid_2m8x1c", "action": "remove"}
  ]
}
```

| 規則 | 內容 |
|---|---|
| 批次 | 每批 ≤ 5,000 筆；`batch.total` 讓接收端知道完整性；最後一批後送 `audience.complete`（可選） |
| 成功 | 接收端回 2xx 即視為交付；非 2xx 或逾時 10 秒 → 重試 |
| 重試 | 指數退避 1 分、5 分、15 分、1 小時、6 小時（共 5 次）；仍失敗進 DLQ，告警，可在 UI「重放」 |
| 冪等 | 同一 `X-DMP-Delivery-Id` 可能重送，接收端應以此去重 |
| 安全 | 簽章驗證、時間戳防重放、目的地可設 IP 白名單與 mTLS；secret 由平台產生並可輪替（新舊並存 24 小時） |
| 隱碼 | 目的地合約指定可帶哪些標籤；L2 數值一律區間化；不含 L3 以上明文 |
| 測試 | UI「送測試事件」：`audience.test` 帶 3 筆假資料，顯示接收端回應 |
| 監控 | 成功率、延遲 P95、重試次數、DLQ 筆數；連續失敗 3 批 → 暫停目的地並通知 |

接收端驗章範例（Python）：
```python
import hmac, hashlib
def verify(secret: bytes, timestamp: str, body: bytes, signature: str) -> bool:
    mac = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={mac}", signature)
```

## 4. GA4 受眾回流流程

```
日批完成（或即時分群變動）
  → 受眾包 version N 與 N-1 比對 → delta（add / remove）
  → 以 OneID 取每位成員的 GA client_id / app_instance_id（可能多個裝置 → 逐一送）
  → 過濾：同意廣告再行銷、且 GA 的 analytics_storage 同意
  → GA4 Measurement Protocol：POST https://www.google-analytics.com/mp/collect?measurement_id=G-XXXX&api_secret=***
      { "client_id": "GA1.2.1844…", "user_id": "u_9a7b…",
        "user_properties": { "dmp_audience_sg1": { "value": "1" } },
        "events": [ { "name": "dmp_audience", "params": { "audience_id": "sg1", "action": "add", "version": 42 } } ] }
  → GA4 受眾定義：user property dmp_audience_sg1 = 1（成員資格 30–540 天）→ 已連結的 Google Ads 自動同步為再行銷名單
  → 交付紀錄：MP 回 2xx 不代表成功處理，另以 GA4 Data API 查受眾人數對帳（T+1）
```
速率：MP 建議每秒 ≤ 數百請求／每個 client；1.5 億變更的受眾包分 24 小時攤平；大型全量以「只送 add、靠 GA4 成員資格到期自然移除」降低量。

## 5. Google Ads Customer Match：本期不做
- 本期只用 GA4 受眾回流（匿名 ID，無個資）。Customer Match 需上傳雜湊 email／手機到 Google（境外傳輸），涉及客戶同意、金管會委外評估與 Google 帳戶資格，列為未來擴充項目。
- 若日後啟用，設計原則：獨立的 adapter 服務向 CRM 取明文、記憶體內正規化與 SHA-256 後呼叫 Google Ads API，DMP 本身不存不傳明文；需法遵開啟與稽核核可。

## 6. 權限、審批與審計
- 建立目的地、綁定受眾包、首次／全量推送：需 `segments.approve`（營運主管）；對外目的地（GA4／Ads）另需稽核核可一次。
- 每次交付寫審計 `sync.delivered`（受眾包、版本、目的地、筆數、成功／失敗、操作者或排程）；外部推送另記 `sync.external`。
- agent 可提議推送（`audience.activate` 工具，status=pending_approval），不能自行執行。

## 7. 對應檔案
- `backend/activation/destinations.yaml`：目的地類型定義與預設參數。
- `backend/activation/webhook.py`：簽章、批次切分、重試策略、驗章範例（可執行自測）。
- `backend/activation/ga4_mp.py`：Measurement Protocol payload 產生器。
- `backend/openapi.yaml`：`/v1/destinations`、`/v1/audiences/{id}/push`、`/v1/deliveries`。
- `backend/mcp/tools.yaml`：`audience.activate`（需審批）。
- 原型：標籤與分群 › 「推送目的地」分頁與「推送」側欄。
