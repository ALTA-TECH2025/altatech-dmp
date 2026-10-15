# AGENTS.md — 給 AI 協作工具（Claude Code / Cursor）的工作規則

## 這個 repo 是什麼
AltaTech DMP：數位軌跡／客戶數據中台。`docs/` 是設計文件，`prototype/` 是純靜態原型，`backend/` 是可跑的骨架與 YAML 設定，`deploy/` 是 Helm / OpenTofu。

## 改東西前先看哪裡
| 想改… | 看這裡 | 不要碰 |
|---|---|---|
| 指標、維度、事件定義 | `backend/semantic/*.yaml` | 不要在程式裡硬編口徑 |
| 角色、模組權限、隱碼 | `backend/security/rbac.yaml`、`masking.yaml`，邏輯在 `policy.py` | UI 不可自行判斷權限，一律呼叫 authorize() |
| AI 工具與 agent | `backend/mcp/tools.yaml`、`backend/insight_agent/agents.yaml`、`skills/*.md` | 不要讓 agent 直接寫 SQL；新能力 = 新工具 |
| 資料來源與映射 | `backend/integration/*.yaml`、`transforms.py` | 個資欄位（L4）永不進平台 |
| 原型頁面 | `prototype/js/app.js`（頁面函式 `pageXxx`）、`js/data.js`（模擬資料）、`css/app.css` | 不要加建置工具；保持純 ES module |
| 部署 | `deploy/helm/altatech-dmp/values*.yaml`、`deploy/tofu/gcp` | 不要引入非白名單授權元件（見 `LICENSES.md`） |

## 怎麼驗證
```bash
python3 backend/security/policy.py            # 權限引擎自測
python3 backend/integration/transforms.py     # 轉換函式自測
python3 -m backend.integration.modeling       # 建模引擎自測（映射、清洗、SQL 編譯）
python3 -m backend.integration.scheduler      # 排程器自測（模擬時鐘）
python3 -m backend.integration.sensors_format # 神策格式檔預處理自測
python3 -m backend.integration.e2e_clickhouse # 接入端到端乾跑；設 CLICKHOUSE_BIN 走真 ClickHouse
python3 backend/insight_agent/runtime.py      # agent 迴圈煙霧測試
python3 -c "import yaml,glob;[yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('backend/**/*.yaml',recursive=True)+glob.glob('deploy/**/*.yaml',recursive=True)]"
node --check prototype/js/app.js && node --check prototype/js/data.js
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
