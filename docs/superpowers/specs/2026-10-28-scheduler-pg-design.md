# 主備前置：同步排程與建模產物改存 PostgreSQL（規格）

> 狀態：規格待審（2026-10-28），issue #87。來源：docs/44 §5a 第三列「同步排程狀態與產物」；Windy 2026-10-28 拍板四項（§2）。
> 不改契約：`POST /v1/integration/sync-tasks/{id}/run` 本來就回 202 accepted，回應內容 openapi 沒有規定。

## 1. 為什麼

目前（main @ f093ee7）：
- 同步排程（任務、執行紀錄、水位、已知欄位、告警）存在 SQLite（`DMP_SCHED_DB`，沒設就是各容器自己的 `/tmp/dmp-sched.db`）。
- 畫面建的模型寫在映像內 `backend/integration/models.generated/`（`MODELS_DIR` 寫死）。
- 「立即執行」在 API 程序裡直接跑。

所以 API 和 worker 拆成兩個容器、或切到主機 2 接手時，會有三個問題：
- 排程會靜默不跑：API 寫自己那份 SQLite，worker 讀另一份。
- 畫面建的模型會不見。
- API（手動）和 worker（排程）可能同時跑同一個任務，水位互相覆蓋。

docs/44 的驗收：API 建立任務 → worker 執行 → API 可下載產物；接管到主機 2 後，同步水位與已產生的檔案仍可用。

## 2. 已拍板（Windy 2026-10-28）

| 項目 | 決定 |
|---|---|
| 畫面建的模型（YAML） | 存 PostgreSQL，與排程同庫；接管不遺失、不靠目錄同步 |
| 看板寄送、資料輸出產物 | 一期放共用目錄（`DMP_EXPORT_DIR` → `APP_DATA`，每 15 分鐘同步到主機 2）；接管最多少 15 分鐘內產生的檔，可重產 |
| 執行紀錄保留 | 365 天，過期自動清除（告警紀錄同） |
| 「立即執行」 | 交給 worker 排隊：API 只登記，持有主控租約的 worker 下一次 tick（約 1 分鐘內）執行；畫面顯示「排隊中」 |

## 3. 設計

### 3.1 儲存
- `Scheduler` 的儲存抽成兩種實作，介面不變：
  - **SQLite**：沒有控制面 PostgreSQL 時使用（本機開發、CI 自測、quickstart 不設 DSN 時）。
  - **PostgreSQL**：設了 `DMP_PG_DSN_REF`／`DMP_PG_DSN` 時使用，跟控制面同一個連線池（asyncpg，已在 requirements，不加新套件）。
- 資料表（schema `dmp`，與 store_pg 同庫）：
  - `sched_tasks`、`sched_runs`、`sched_watermarks`、`sched_known_fields`、`sched_alerts`：欄位對應現有 SQLite 表。
  - `sched_runs` 另加 `owner`、`generation`：執行時的主控租約持有者與世代。
  - `sched_models (id PK, yaml TEXT, updated_at)`：畫面建的模型。
- 時間一律 `timestamptz`（UTC）。
- 設了 PostgreSQL 時，`DMP_SCHED_DB` 不再使用（啟動時若兩者都設，記警告）。

### 3.2 模型
- 建模精靈產物寫進 `sched_models`。任務的 `model_path` 記成 `db:<任務 id>`；runner 依前綴從資料庫讀 YAML。
- repo 內建的範例模型（`backend/integration/models.*.yaml`）照舊用檔案路徑。
- 沒有 PostgreSQL 時（SQLite 模式），畫面建的模型一樣存進 SQLite 的 `sched_models`，不再寫進映像目錄。

### 3.3 執行只由主控 worker 做
- `tick()` 只在持有主控租約（`ops/lease.py` 的 `dmp-tick`）的 worker 跑（現況已是如此）。
- 「立即執行」改為插入一筆 `status='queued', trigger='manual'` 的執行紀錄，API 回 202：`{task_id, run_id, status: "queued"}`。
- 同一任務已有 `queued` 或 `running` 時，不再新增，回同一筆（重按不會排兩次）。
- worker 每次 tick 先處理到期重試，再處理 `queued`，最後才是到點的排程。
- 停用中的任務仍可手動執行（與現況相同）。

### 3.4 跑到一半當機
- 開始執行時把 `owner`、`generation` 寫進該筆紀錄。
- 結案（寫結果、水位、已知欄位）在同一個交易內，條件是「這筆仍是 running、仍是我、租約仍有效」（沿用 lease 的 HELD 條件，以資料庫時鐘判斷）。條件不成立就不寫，整筆交給下一任。
- 新主控每次 tick 把 `running` 但 owner／generation 不是自己的紀錄標成 `interrupted`，再照任務的重試設定排重試（次數用完就 `failed`，並發告警）。
- 重跑同一窗口不會重複入倉：事件路徑有內容雜湊去重，用戶路徑內容相同不寫（#85），維度表是 upsert／全量。

### 3.5 匯出檔
- 程式已讀 `DMP_EXPORT_DIR`，不需要改。
- 新增啟動檢查：設了控制面 PostgreSQL（正式部署）卻沒設 `DMP_EXPORT_DIR` 時，啟動失敗並說明原因，避免檔案寫進容器暫存區、重啟就不見。

### 3.6 保留
- 每天一次（主控 worker）刪除 `scheduled_for` 早於 365 天前的執行紀錄與告警。
- 任務、水位、已知欄位、模型不刪。

### 3.7 舊資料
- 後端測試／正式環境尚未部署，沒有要搬的 SQLite 資料。
- quickstart 若已有本機排程，提供一次性匯入指令：`python3 -m backend.integration.cli sched import-sqlite --from <sched.db>`。

## 4. 驗收條件（轉成自測）
1. 設了 PostgreSQL：API 建任務（含建模精靈產物）→ 另一份程序（worker）tick 會執行；API 讀得到執行紀錄與水位。
2. 兩份 worker 同時 tick：同一窗口只執行一次（只有主控跑）。
3. 「立即執行」回 202、`status=queued`；重按回同一筆；下一次 tick 由主控執行。
4. 主控執行到一半停止（模擬：不結案）→ 租約過期、另一份接手 → 該筆標 `interrupted` 並重試一次；舊主控晚到的結案不寫入（水位不被覆蓋）。
5. 模型存在 `sched_models`；任務改由 `db:` 讀；容器內 `models.generated` 不再產生檔案。
6. 超過 365 天的執行紀錄被清除，近期紀錄保留。
7. 設了 PostgreSQL 但沒設 `DMP_EXPORT_DIR`：啟動失敗並說明。
8. 沒設 PostgreSQL：行為與現在相同（SQLite），現有 scheduler／api／e2e 自測全過。
9. `import-sqlite` 把 SQLite 的任務、水位、已知欄位、模型搬進 PostgreSQL，重跑不重複。

## 5. 不在範圍
- 匯出檔改物件儲存（SeaweedFS）：二期或另案。
- 兩個 API 同時接流量（雙活）：docs/44 §5a 其他列。
- PostgreSQL 本身的主備複寫與切換（部署套件 #76、運維）。

## 6. PR 拆法
1. 本規格（docs）。
2. 後端：儲存抽換、模型入庫、排隊、中斷接手、保留、啟動檢查、匯入指令（預估 350 行內，超過就把匯入指令拆出）。
3. 前端：「立即執行」顯示「已排入佇列」，提示到執行紀錄看結果。
4. 文件收尾：docs/44 §5a 第三列標完成、部署套件 README 移除「MODELS_DIR 不能共用」限制。

## 7. 待確認
- 無。（四項已拍板；PostgreSQL 主備複寫的資料遺失上限屬部署套件與運維範圍。）
