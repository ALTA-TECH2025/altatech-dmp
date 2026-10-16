# CLAUDE.md — Claude Code 在這個 repo 的工作規則

先讀 `AGENTS.md`（目錄邊界、驗證指令、禁止事項），再讀你要改的工作流目錄下的 `README.md`（若有）。本檔只補 Claude 專屬的流程：

## 開工前
1. `git fetch origin && git switch -c <ws>/<issue-id>-<slug> origin/main`（ws = infra | data | query | web | agent | qa）。
2. 讀 issue（Story 模板）裡的「驗收條件」；把它轉成測試先寫下來。
3. 只改自己工作流擁有的目錄；要改契約（`backend/openapi.yaml`、`backend/semantic/*.yaml`、`backend/mcp/tools.yaml`、`backend/security/*.yaml`）先開「contract:」PR，不要夾在功能 PR 裡。

## 收工前（每個 PR 都要）
```bash
python3 backend/security/policy.py && python3 backend/integration/transforms.py && python3 backend/integration/auth.py && python3 backend/oneid/attribution.py && python3 -m backend.auth && python3 -m backend.api && python3 -m backend.segments
python3 -m backend.integration.modeling && python3 -m backend.integration.scheduler && python3 -m backend.integration.sensors_api && python3 -m backend.semantic.compiler.golden && python3 -m backend.integration.e2e_clickhouse
python3 -c "import yaml,glob;[yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('backend/**/*.yaml',recursive=True)+glob.glob('deploy/**/*.yaml',recursive=True)]"
node --check prototype/js/app.js && node --check prototype/js/data.js
(cd web && npm ci && npm run typecheck && npm test && npm run build)
```
再加上該工作流自己的測試（pytest / vitest / go test / Playwright）。PR 描述用 `.github/pull_request_template.md`，「跑過的驗證」欄位要貼實際輸出摘要，不要只寫「已測」。

## 規則
- 一個 PR 一件事、< 400 行 diff（契約 PR 除外）；大功能拆成「契約 → 後端 → 前端」三個 PR。
- 不要產生與 `docs/` 口徑不一致的指標或權限邏輯；有衝突時以 `backend/semantic/*.yaml` 與 `backend/security/*.yaml` 為準並在 PR 說明。
- 不要引入新的第三方套件／映像，除非 PR 裡附授權（白名單見 `LICENSES.md`）與 CVE 檢查結果。
- 模擬資料不可含真實客戶資料；密鑰只能是 secret_ref。
- Commit 訊息中文、說明動機；不要在訊息或程式註解裡放模型名稱或工具名稱。
