# segments — 分群引擎最小版

docs/21 §1、docs/23 §4.5、openapi `Segments`。規則就是用戶列表的 `UsersQuery`（屬性／分群／做過未做過／分析落點下鑽），所以「完成 A 未完成 B」= `behaviors: [{event: A}, {event: B, done: false}]`，「漏斗第 2 步流失存成分群」= `kind: result` + `rule.source`。

| 動作 | 做什麼 | 權限 |
|---|---|---|
| `POST /v1/segments` | 建立；行銷與 agent 提議 → `pending_approval`，數據分析／數位經營／admin → `active`；名稱唯一、每專案 ≤ 500 | segments.use（規則編譯另需 users.use） |
| `POST /v1/segments/estimate`、`/{id}/preview` | Bitmap 基數（精確）；畫像 = lifecycle／tier／aum_bucket／branch_id 分布 | segments.use／view |
| `POST /{id}/approve`、`/{id}/submit` | 數位經營核可或退回（附 note）；退回後改完再送審 | segments.approve／use |
| `POST /{id}/compute` | `INSERT INTO dmp.segment_members … WITH <bitmaps> SELECT … WHERE bitmapContains` → 新版本；手動留 1 版、例行留 keep 版（舊版本 `ALTER … DELETE`）；失敗留紀錄並審計 | segments.use |
| `POST /v1/segments/tick` | 例行排程：到了 tz 當地最早計算時間且該 basetime 未算 → 計算（日＝當日；週＝本週一；月＝本月 1 日，錯過補算）；順帶清回收站到期（15 天） | segments.admin |
| `PUT /{id}` | 結果保存分群不可改規則；手動不可改更新方式；例行不可改頻率；改規則後 `latest.stale` | segments.use |
| `GET /{id}/versions/{basetime}/members` | 成員列表 = 用戶列表（`seg.<id>@<version>`），欄位依角色遮罩 | segments.view + users.use |
| `/{id}/pause`、`/copy`、`/lineage`、`/recycle`（有下游 409，`force=true`）、`/restore`、`DELETE /{id}/purge`（admin，清成員） | | |

查詢端：編譯器 `seg.<id>` 取最新版本（`version = (SELECT max(version) …)`），`seg.<id>@<n>` 取指定版本；動態匹配（事件日期 = basetime）之後做。

```bash
python3 -m backend.segments                    # 自測（假 ClickHouse）
CLICKHOUSE_URL=http://127.0.0.1:8123 DMP_STORE_PATH=store.json python3 -m backend.segments tick   # 排程器每分鐘呼叫
```

端到端：`backend/integration/e2e_clickhouse.py` 最後跑 `segments/e2e_stage.py`（黃金資料集：規則分群成員 == oracle、改規則重算、版本保留、`seg.<id>` 最新版本、例行 tick、結果保存分群、徹底刪除）。

之後：排隊式計算（Airflow）與 running 狀態、導入創建（txt／csv + L4 掃描）、排序與截取（前 N／N%）、分群推送（Webhook／Kafka／GCS）、動態匹配版本、規則組「交／併／差」的 UI 轉 UsersQuery。
