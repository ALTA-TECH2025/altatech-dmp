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
| `POST /v1/analytics/retention` | 留存：四模型 × 日週月 → 同期群 × 期的人數／留存／比例 + complete；審計 | events.use |
| `GET /v1/semantic/catalog` | 查詢建構器目錄（事件、維度、用戶屬性、聚合、運算子；L3 不給） | events.view |
| `POST /v1/analytics/events` | 編譯 → ClickHouse → rows + chart_spec + cost；審計 query.executed | events.use |
| `GET /v1/audit` | 審計尾端（行銷／分析只看自己） | audit.view |

```bash
python3 -m backend.api                      # 自測：假 ClickHouse、假 session；角色 CRUD、指派、模擬、編譯、執行、審計、CSRF
CLICKHOUSE_URL=http://127.0.0.1:8123 DMP_SECRETS_JSON=secrets.json uvicorn backend.api.main:app --port 8000   # 正式啟動（前端 vite 代理到 8000）
```

- 儲存：`store.py` 的 Protocol；骨架用記憶體／JSON 檔（`DMP_STORE_PATH`），正式換 PostgreSQL（iam_roles、iam_assignments、audit_log）。
- 密鑰：`DMP_SECRETS_JSON` 是 secret_ref → 明文 的對照（只在執行節點由密鑰庫掛載）；程式碼與設定檔只出現 secret_ref。
- 端到端：`backend/integration/e2e_clickhouse.py` 最後跑 `api/e2e_stage.py`，以 admin 與行銷兩種身分打 `/v1/analytics/events`，結果需與直接執行編譯器 SQL 相同。
- 之後：漏斗／留存／路徑／歸因端點（等編譯器樣板）、users／profile 端點、export 審批流程、schemathesis 契約測試、五角色 × 端點權限矩陣自動展開。
