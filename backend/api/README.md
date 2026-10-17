# api — REST API（FastAPI）

docs/23 §4.4：router 只做「驗證 → Policy → 呼叫服務 → 審計」，不寫 SQL。目前接上的端點：

| 端點 | 服務 | 權限 |
|---|---|---|
| `/auth/*`、`/v1/iam/me`、`/v1/iam/sessions?scope=me|all`、`DELETE /v1/iam/sessions/{sid}` | `backend/auth`（docs/24）；全平台 session 與強制登出需 iam.admin | session cookie |
| `GET/POST/PUT/DELETE /v1/iam/roles`、`/roles/{key}/copy`、`GET /v1/iam/changes` | 自訂角色（內建唯讀）；`rbac.yaml custom_roles.second_review` 開啟時建立／修改／刪除回 202 審批單 `role_change`（前後快照、不可自審），核可後才註冊進 Policy Engine | iam.view／iam.admin |
| `/v1/iam/assignments` | 本地指派（自訂角色、範圍、到期、拒絕）；`break_glass=true` 回 202 審批單 `break_glass`，核可後建立 ≤ 4 小時的 admin 到期指派 | iam.admin |
| `GET/POST /v1/iam/users`、`GET/PATCH/DELETE /v1/iam/users/{id}`、`/reset-password`、`/reset-totp` | `sysadmin.py` 成員目錄（登入審計 ∪ 本機帳號 ∪ 本地指派：狀態、最近登入、失敗登入、session、指派）；本機帳號管理（白名單角色、TOTP、停用即踢 session） | iam.view／iam.admin |
| `GET /v1/audit/stats`、`GET /v1/audit/export`、`GET /v1/settings` | 日誌統計看板（指標卡、每日趨勢、模組／角色、敏感存取 Top、審批、告警規則評估 `security/audit_alerts.yaml`）；審計報表 CSV（浮水印）；平台設定唯讀（密鑰只顯示 secret_ref） | audit.view／use、audit.export、iam.view |
| `POST /v1/iam/scope/simulate` | Policy 行級條件 + 編譯器對範例查詢注入後的 SQL | iam.view |
| `POST /v1/semantic/compile` | 查詢編譯器 dry-run（SQL、層級、血緣、EXPLAIN ESTIMATE） | events.use |
| `POST /v1/analytics/funnel` | 漏斗：編譯 → ClickHouse → 每步人數 + 轉換率；審計 | funnel.use |
| `POST /v1/analytics/path` | 行為路徑：起點／終點 N 步、會話切割、節點拆分與折疊、離開節點；預設抽樣 1/10；Top N 路徑 + 桑基節點／連線；審計 | path.use |
| `POST /v1/analytics/attribution` | 歸因：轉化目標 × 觸點 × 六種模型同時比較、回溯窗口、拆分；summary + 每觸點各模型轉化數／價值／占比；審計 | attribution.use |
| `POST /v1/analytics/retention` | 留存：四模型 × 日週月 → 同期群 × 期的人數／留存／比例 + complete；審計 | events.use |
| `GET /v1/semantic/catalog` | 查詢建構器目錄（事件、維度、用戶屬性、聚合、運算子；L3 不給） | events.view |
| `POST /v1/analytics/events` | 編譯 → ClickHouse → rows + chart_spec + cost；審計 query.executed | events.use |
| `/v1/meta/events`（GET/POST）、`/{name}`（GET/PATCH）、`/{name}/status`、`/v1/meta/change-requests[/{id}/decide]`、`/v1/meta/quarantine`、`/v1/meta/properties`、`/v1/meta/virtual-events`、`/v1/meta/user-properties`、`/v1/meta/oneid/rules`（GET/PUT）、`/v1/meta/dimension-tables[/{id}[/sync]]`、`/v1/meta/dictionaries[/{property}]`、`/v1/meta/virtual-properties` | 資料治理：語意層 yaml 為契約來源，執行期的登記／狀態／變更單／字典上傳／規則修改存 store 覆蓋層並合併回應；統計（日均量、隔離、覆蓋率、列數、OneID）從 ClickHouse；上線檢查（命名、負責人、型別、無 L4）；上線事件改屬性 → 變更單（governance.admin 審批、不可自審）；敏感等級只升不降；L4 一律拒絕；OneID login_id 不可動、改規則 pending_review | governance.view／use／admin |
| `/v1/migration/inventory[/refresh]`、`/v1/migration/bridge`、`/v1/migration/exports`（GET/POST）、`/{id}`、`/{id}/rerun`、`/v1/migration/reconcile` | 神策遷移：盤點（inventory.yaml 計畫 + 神策查詢 API 盤點 + 任務進度）、橋接表（bridge.py 即時產生）、導出任務（按日分片；執行節點有 SA_* 才真的導出／代碼化／載入，否則 blocked）、重跑（> 0.1% 差異；3 次後待人工 force）、對帳 T2／T5／T6（ingest_recon、events.source、登入用戶銜接） | governance.view／use |
| `GET/POST /v1/dashboards`、`GET/PUT/DELETE /v1/dashboards/{id}`、`POST /cards`、`DELETE /cards/{card}`、`POST /cards/{card}/data` | 儀表板：卡片 = 一種分析 + 查詢 body（存條件不存結果）；建立時以建立者身分編譯一次；卡片資料以「檢視者」身分重算（遮罩、行級、K 匿名照檢視者），審計 query.executed module=dashboard；時間覆寫：請求 → 儀表板 preset → 卡片釘住區間；擁有者／admin 可編輯，分享給角色或使用者只能看 | overview.view／overview.use |
| `POST /auth/ldap`（provider=ad｜local）、`POST /auth/step-up` | 帳密登入：AD（LDAPS）或本機帳號（`backend/auth/local.py`：scrypt、TOTP、鎖定、到期、allowed_roles 白名單）；admin 超過 15 分鐘後輸入 TOTP 延展 | — |
| `POST /v1/users/estimate`、`/v1/users/list` | 用戶列表：條件 → Bitmap 估數；keyset 分頁列表（欄位依角色遮罩、每頁 ≤ 500、最多 10 萬筆）；審計 users.estimated／users.listed | users.use |
| `GET /v1/profiles/{one_id}`、`/timeline`、`/identities` | 用戶細查：畫像 + 近 30 天摘要 + 分群、行為序列（倒序分頁、props 去 L4 鍵）、身分圖（id_edges）；需 reason 4–200 字；範圍外 404；審計 profile.viewed／timeline／identities 只記 one_id 雜湊 | profile.use |
| `/v1/segments*` | 分群引擎最小版（`backend/segments/`）：建立／審批／估數與畫像／計算（版本保留）／例行 tick／成員列表／複製／暫停／血緣／回收站／徹底刪除 | segments.view／use／approve／admin |
| `/v1/approvals*`、`/v1/exports*`、`/v1/reveals` | 審批單（類型決定審批角色、hook 回寫受控物件、逾期）、匯出申請（用途、估列數、核可後產檔：遮罩嚴一級 + 浮水印、下載）與欄位揭露申請（L2／L3、限單一用戶、時效、每次查看審計）；`backend/approvals/` | users.export／類型審批角色 |
| `GET /v1/audit` | 審計尾端（行銷／分析只看自己）；403 一律寫 `authz.denied` | audit.view |
| `integration.py` | `/v1/integration/connectors`、`/connections`（建立／詳情／PATCH／停用／test／rotate／privileges）、`/tables`（登記／rescan）、`/sync-tasks`（建立／run／runs／preview） | integration.view／use／admin | 連線只存 secret_ref（明文 422）、回應遮罩；測試 = dns／tcp 探測（auth 以後由執行節點注入 probes）；任務 = 模型 yaml + 來源 + 排程交 Scheduler（SQLite）；run 同步執行（無 ClickHouse 時乾跑）；preview 以樣本跑映射與清洗 |

```bash
python3 -m backend.api                      # 自測：假 ClickHouse、假 session；角色 CRUD、指派、模擬、編譯、執行、審計、CSRF
CLICKHOUSE_URL=http://127.0.0.1:8123 DMP_SECRETS_JSON=secrets.json uvicorn backend.api.main:app --port 8000   # 正式啟動（前端 vite 代理到 8000）
```

- 儲存：`store.py` 的 Protocol；`store_pg.py` 是 PostgreSQL 實作（schema `dmp`：iam_roles／iam_assignments／segments／items（審批單、匯出單）各一張 id + jsonb 表、audit_log append-only；asyncpg 放在背景事件迴圈，表名一律帶 schema 因為連線池歸還會 RESET 會話），`DMP_PG_DSN_REF`（secret_ref）或 `DMP_PG_DSN` 設了就用；都沒設才用記憶體／JSON 檔（`DMP_STORE_PATH`）。自測 `python3 -m backend.api.store_pg` 用獨立 schema 跑 CRUD、併發審計與整套 API 自測後刪除。
- 密鑰：`DMP_SECRETS_JSON` 是 secret_ref → 明文 的對照（只在執行節點由密鑰庫掛載）；程式碼與設定檔只出現 secret_ref。
- 端到端：`backend/integration/e2e_clickhouse.py` 最後跑 `api/e2e_stage.py`，以 admin 與行銷兩種身分打 `/v1/analytics/events`／funnel／retention／`/v1/users/list`，結果需與直接執行編譯器 SQL 相同；數據分析身分打細查三端點。
- 之後：路徑／歸因端點（等編譯器樣板）、export 審批流程、schemathesis 契約測試、五角色 × 端點權限矩陣自動展開。

## 單機啟動（main.py 的環境變數）

`DMP_WEB_DIST`（API 同源提供前端 SPA）、`DMP_LOCAL_ACCOUNTS`／`DMP_LOCAL_ALLOWED_ROLES`（本機帳號）、`DMP_TICK=1`（背景執行同步任務排程與分群例行）、`DMP_COOKIE_SECURE=0`（無 TLS 測試）、`DMP_K_IDM`／`DMP_K_ACCT`（代碼化金鑰）；`python3 -m backend.api.seed` 載合成資料、建五種角色帳號、建範例儀表板。一鍵流程見 `deploy/quickstart/README.md`。
