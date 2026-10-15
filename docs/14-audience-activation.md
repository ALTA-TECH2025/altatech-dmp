# 受眾名單推送（Audience Activation）：把分群成員名單推送到內部系統

> 版本 v0.5（2026-10-15）。範圍：**只做名單推送**。DMP 把分群（受眾包）的成員（代碼化識別子，可附帶標籤）以簽章 Webhook、Kafka 或 GCS Parquet 交給內部系統（MA、CRM、數倉），由這些系統負責後續觸達。廣告平台（GA4 受眾、Google Ads 再行銷、Customer Match）不在本系統範圍。

## 1. 物件模型

| 物件 | 說明 |
|---|---|
| Audience（受眾包） | 一個分群的快照版本：`audience_id`、`version`、成員 Bitmap、產生時間、口徑（分群定義的 hash） |
| Destination（目的地） | 內部系統端點：類型（webhook／kafka／gcs）、識別子（customer_code／one_id）、可附標籤清單、簽章密鑰參照、狀態 |
| Push（推送單） | 受眾包 × 目的地 × 模式（增量／全量／即時）× 排程；全量需審批 |
| Delivery（交付） | 一次實際送出：批次、筆數、回應、重試次數、狀態；冪等鍵 `delivery_id` |

## 2. 目的地類型

| 類型 | 用途 | 識別子 | 附帶標籤 | 傳輸 |
|---|---|---|---|---|
| Webhook | MA、CRM 等應用系統 | customer_code（代碼化）、one_id | 可選；L2 數值一律區間化 | HTTPS + HMAC-SHA256 簽章；mTLS／IP 白名單可選 |
| Kafka | 內部即時消費（數據平台、規則引擎） | customer_code + one_id | 可 | `dmp.audience_updates`、`dmp.tag_updates`（Avro） |
| GCS Parquet | 數倉、大量全量 | customer_code | 可 | `gs://dmp-sync/audiences/…` + manifest + checksum |

不傳：姓名、手機、Email、完整帳號等任何 L4／L3 明文。

## 3. Webhook 規格

```
POST {url}
X-DMP-Event: audience.delta | audience.full | audience.complete | tag.update | audience.test
X-DMP-Delivery-Id: dlv_…          # 冪等鍵；接收端重複收到同 id 應忽略
X-DMP-Timestamp: 1760400000       # 秒；偏差 > 300 s 拒絕
X-DMP-Signature: sha256=HMAC(secret, timestamp + "." + body)
Content-Type: application/json

{ "audience_id": "sg2", "audience_name": "新戶未入金（7 天）", "version": 118, "mode": "delta",
  "batch": { "index": 1, "total": 3, "size": 5000 }, "identifier": "customer_code",
  "members": [ { "customer_code": "cust_8f3a…", "one_id": "oid_…", "action": "add", "tags": { "lifecycle": "沉睡", "fee_tier": "P1" } },
               { "customer_code": "cust_21cc…", "one_id": "oid_…", "action": "remove" } ] }
```

- 每批 ≤ 5,000 筆；2xx 即成功；非 2xx 或逾時 10 秒重試：1 分、5 分、15 分、1 小時、6 小時 → DLQ（可重放）。
- 連續 3 批失敗暫停該目的地並通知負責人；審計 `sync.failed`。
- 全量推送分批後送 `audience.complete` 收尾（含總筆數與 checksum）。
- 密鑰：只存密鑰庫參照；輪替時新舊 24 小時並存（`/v1/destinations/{id}/rotate-secret`）。
- 接收端要做的事：驗簽 → 以 `delivery_id` 冪等 → 依 `action` 加入／移除名單 → 回 2xx。

## 4. 模式與排程

| 模式 | 內容 | 審批 |
|---|---|---|
| 增量（delta） | 與上一版本比對，只送加入／移除 | 已核准的排程自動執行 |
| 全量（full） | 整份名單；用於初始化或對帳 | 數位經營 |
| 即時（realtime） | 成員進出即推（Flink 規則／即時標籤） | 建立時審批一次 |

排程：日批完成後（預設 03:00）、立即、即時。推送前先與使用者數據範圍 Bitmap 交集（自訂角色若有專案／分群限制，只能推範圍內的名單）。

## 5. 權限、審批與審計

- 建立目的地：數位經營或資訊管理（`segments.approve` 或 `integration.admin`）；新目的地首次推送需系統管理員核可。
- 推送單：建立需 `segments.use`；全量需 `segments.approve`。
- 審計：`sync.delivered`、`sync.failed`、`sync.replayed`、`destination.created`、`destination.secret_rotated`。
- 名單內容依目的地合約套隱碼（標籤最多 L2，數值區間化）。

## 6. 對應檔案

- `backend/activation/destinations.yaml`：目的地類型、簽章、批次、重試、審批。
- `backend/activation/webhook.py`：簽章、批次切分、重試排程、DLQ 骨架。
- `backend/openapi.yaml`：`/v1/destinations*`、`/v1/audiences/{id}/push`、`/v1/sync/*`。
- 原型：標籤與分群 › 推送目的地；推送受眾包抽屜。
