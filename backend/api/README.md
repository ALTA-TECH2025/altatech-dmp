# api — REST API（FastAPI）

docs/23 §4.4：router 只做「驗證 → Policy → 呼叫服務 → 審計」，不寫 SQL。目前接上的端點：

| 端點 | 服務 | 權限 |
|---|---|---|
| `/auth/*`、`/v1/iam/me`、`/v1/iam/sessions` | `backend/auth`（docs/24） | session cookie |
| `GET/POST/PUT/DELETE /v1/iam/roles`、`/roles/{key}/copy` | 自訂角色（內建唯讀）；註冊進 Policy Engine | iam.view／iam.admin |
| `/v1/iam/assignments` | 本地指派（自訂角色、範圍、到期、拒絕；admin 只能 break-glass） | iam.admin |
| `POST /v1/iam/scope/simulate` | Policy 行級條件 + 編譯器對範例查詢注入後的 SQL | iam.view |
| `POST /v1/semantic/compile` | 查詢編譯器 dry-run（SQL、層級、血緣、EXPLAIN ESTIMATE） | events.use |
| `POST /v1/analytics/funnel` | 漏斗：編譯 → ClickHouse → 每步人數 + 轉換率；審計 | funnel.use |
| `POST /v1/analytics/path` | 行為路徑：起點／終點 N 步、會話切割、節點拆分與折疊、離開節點；預設抽樣 1/10；Top N 路徑 + 桑基節點／連線；審計 | path.use |
| `POST /v1/analytics/attribution` | 歸因：轉化目標 × 觸點 × 六種模型同時比較、回溯窗口、拆分；summary + 每觸點各模型轉化數／價值／占比；審計 | attribution.use |
| `POST /v1/analytics/retention` | 留存：四模型 × 日週月 → 同期群 × 期的人數／留存／比例 + complete；審計 | events.use |
| `GET /v1/semantic/catalog` | 查詢建構器目錄（事件、維度、用戶屬性、聚合、運算子；L3 不給） | events.view |
| `POST /v1/analytics/events` | 編譯 → ClickHouse → rows + chart_spec + cost；審計 query.executed | events.use |
| `GET/POST /v1/dashboards`、`GET/PUT/DELETE /v1/dashboards/{id}`、`POST /cards`、`DELETE /cards/{card}`、`POST /cards/{card}/data` | 儀表板：卡片 = 一種分析 + 查詢 body（存條件不存結果）；建立時以建立者身分編譯一次；卡片資料以「檢視者」身分重算（遮罩、行級、K 匿名照檢視者），審計 query.executed module=dashboard；時間覆寫：請求 → 儀表板 preset → 卡片釘住區間；擁有者／admin 可編輯，分享給角色或使用者只能看 | overview.view／overview.use |
| `POST /auth/ldap`（provider=ad｜local）、`POST /auth/step-up` | 帳密登入：AD（LDAPS）或本機帳號（`backend/auth/local.py`：scrypt、TOTP、鎖定、到期、allowed_roles 白名單）；admin 超過 15 分鐘後輸入 TOTP 延展 | — |
| `POST /v1/users/estimate`、`/v1/users/list` | 用戶列表：條件 → Bitmap 估數；keyset 分頁列表（欄位依角色遮罩、每頁 ≤ 500、最多 10 萬筆）；審計 users.estimated／users.listed | users.use |
| `GET /v1/profiles/{one_id}`、`/timeline`、`/identities` | 用戶細查：畫像 + 近 30 天摘要 + 分群、行為序列（倒序分頁、props 去 L4 鍵）、身分圖（id_edges）；需 reason 4–200 字；範圍外 404；審計 profile.viewed／timeline／identities 只記 one_id 雜湊 | profile.use |
| `/v1/segments*` | 分群引擎最小版（`backend/segments/`）：建立／審批／估數與畫像／計算（版本保留）／例行 tick／成員列表／複製／暫停／血緣／回收站／徹底刪除 | segments.view／use／approve／admin |
| `/v1/approvals*`、`/v1/exports*`、`/v1/reveals` | 審批單（類型決定審批角色、hook 回寫受控物件、逾期）、匯出申請（用途、估列數、核可後產檔：遮罩嚴一級 + 浮水印、下載）與欄位揭露申請（L2／L3、限單一用戶、時效、每次查看審計）；`backend/approvals/` | users.export／類型審批角色 |
| `GET /v1/audit` | 審計尾端（行銷／分析只看自己） | audit.view |
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
