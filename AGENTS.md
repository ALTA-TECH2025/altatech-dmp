# AGENTS.md — 給 AI 協作工具（Claude Code / Cursor）的工作規則

## 這個 repo 是什麼
AltaTech DMP：數位軌跡／客戶數據中台。`docs/` 是設計文件，`prototype/` 是純靜態原型，`backend/` 是可跑的骨架與 YAML 設定，`deploy/` 是 Helm / OpenTofu。

## 改東西前先看哪裡
| 想改… | 看這裡 | 不要碰 |
|---|---|---|
| 指標、維度、事件定義 | `backend/semantic/*.yaml` | 不要在程式裡硬編口徑 |
| 查詢怎麼變 SQL | `backend/semantic/compiler/`（parser → events 樣板 → policy 注入），口徑見其 README | UI／API／agent 不得自己拼 SQL；新查詢能力 = 新樣板 + 黃金查詢 |
| 角色、模組權限、隱碼 | `backend/security/rbac.yaml`、`masking.yaml`，邏輯在 `policy.py` | UI 不可自行判斷權限，一律呼叫 authorize() |
| AI 工具與 agent | `backend/mcp/tools.yaml`、`backend/insight_agent/agents.yaml`、`skills/*.md` | 不要讓 agent 直接寫 SQL；新能力 = 新工具 |
| 資料來源與映射 | `backend/integration/*.yaml`、`transforms.py` | 個資欄位（L4）永不進平台 |
| 原型頁面 | `prototype/js/app.js`（頁面函式 `pageXxx`）、`js/data.js`（模擬資料）、`css/app.css` | 不要加建置工具；保持純 ES module |
| 正式前端 | `web/src/`（app 路由、auth、shell、iam、lib/policy），設計見 docs/24 | 資料存取只走 `api` 客戶端；權限只讀 `/v1/iam/me`，不得自行判斷 |
| REST 端點 | `backend/api/`（app、iam、analytics、store） | router 不寫 SQL：驗證 → authorize() → 服務（compiler／auth）→ 審計 |
| 登入／session | `backend/auth/`（idp.yaml、oidc.py、ldap.py、session.py、mapping.py、api.py） | 密鑰只能 secret_ref；不把 IdP token 交給瀏覽器 |
| 部署 | `deploy/helm/altatech-dmp/values*.yaml`、`deploy/tofu/gcp` | 不要引入非白名單授權元件（見 `LICENSES.md`） |

## 怎麼驗證
```bash
python3 backend/security/policy.py            # 權限引擎自測
python3 backend/oneid/attribution.py          # OneID 歸戶 20 個情境（含 v1.2 血緣、傳遞映射、合併拆分）
python3 -m backend.api                        # REST API 自測：角色 CRUD、指派、行級模擬、/v1/semantic/compile、/v1/analytics/events（假 ClickHouse）、審計、CSRF
python3 -m backend.api.store_pg               # 控制面 PostgreSQL 儲存自測（需 DMP_PG_DSN；沒有就略過）：CRUD、併發審計、整套 API 自測改用 PostgresStore
python3 -m backend.segments                   # 分群引擎自測：建立／審批／計算（版本保留）／例行 tick／編輯限制／回收站／成員列表／權限（假 ClickHouse）
python3 -m backend.auth                       # 登入整合自測：假 Entra ID（PKCE／JWKS／amr／overage）、假 AD（bind／巢狀群組／限流）、session／CSRF／step-up、API token、FastAPI 路由
python3 backend/integration/transforms.py     # 轉換函式自測
python3 -m backend.integration.modeling       # 建模引擎自測（映射、清洗、SQL 編譯）
python3 -m backend.integration.scheduler      # 排程器自測（模擬時鐘）
python3 -m backend.integration.sensors_format # 神策格式檔預處理自測
python3 -m backend.integration.ga4_source     # GA360 BigQuery 三種表／三個任務自測（假 BigQuery）
python3 -m backend.integration.sensors_api    # 神策查詢 API 連接器自測（假伺服器：鑑權兩模式、盤點、分頁導出、接 stage）
python3 -m backend.semantic.compiler.golden   # 查詢編譯器黃金測試乾跑（30 條編譯 + oracle、10 條必拒）；設 CLICKHOUSE_BIN 走真 ClickHouse 逐列比對
python3 -m backend.integration.e2e_clickhouse # 接入端到端乾跑；設 CLICKHOUSE_BIN（自啟）或 CLICKHOUSE_URL（現成伺服器）走真 ClickHouse，最後一段跑編譯器黃金測試
python3 backend/insight_agent/runtime.py      # agent 迴圈煙霧測試
python3 -c "import yaml,glob;[yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('backend/**/*.yaml',recursive=True)+glob.glob('deploy/**/*.yaml',recursive=True)]"
node --check prototype/js/app.js && node --check prototype/js/data.js
cd web && npm ci && npm run typecheck && npm test && npm run build   # 正式前端（React + Vite + TS）：型別、單元測試、建置
cd prototype && python3 -m http.server 8080   # 手動或 Playwright 檢視
```

## 慣例
- 語言：Python 3.12（型別註記）、TypeScript、Go（僅 Collector）、SQL。不新增其他語言。
- 設定即程式：能用 YAML / SQL 表達的不要寫程式。
- 每個數字都要能追溯到語意層指標；每個敏感存取都要有理由與審計。
- 新增第三方元件：先查授權（白名單）、CVE 歷史、是否 LTS、有無 Operator／託管版，寫進 `LICENSES.md` 與 `docs/11`。
- Commit 訊息：中文、說明動機；一個 PR 一件事。

## 禁止
- 把真實客戶資料放進 repo 或原型模擬資料。
- 在前端或 agent 繞過 Policy Engine。
- fork 修改開源元件；需要客製走 plugin / UDF / 設定。
- 引入 AGPL / SSPL / BSL / ELv2 授權的執行期元件。
