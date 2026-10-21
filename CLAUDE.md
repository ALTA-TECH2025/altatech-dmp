# CLAUDE.md — Claude Code 在這個 repo 的工作規則

先讀 `AGENTS.md`（目錄邊界、驗證指令、禁止事項），再讀你要改的工作流目錄下的 `README.md`（若有）。本檔只補 Claude 專屬的流程：

## 開工前
1. `git fetch origin && git switch -c <ws>/<issue-id>-<slug> origin/main`（ws = infra | data | query | web | agent | qa）。
2. 讀 issue（Story 模板）裡的「驗收條件」；把它轉成測試先寫下來。
3. 只改自己工作流擁有的目錄；要改契約（`backend/openapi.yaml`、`backend/semantic/*.yaml`、`backend/mcp/tools.yaml`、`backend/security/*.yaml`）先開「contract:」PR，不要夾在功能 PR 裡。

## 收工前（每個 PR 都要）
```bash
python3 tools/affected_tests.py --run          # 依相對 origin/main 的異動（含未提交）只跑受影響的測試
python3 tools/affected_tests.py --run --all    # 全跑：改了共用基礎或不確定影響範圍時
```
挑法：Python 自測沿 import 閉包挑（改 `backend/api` 只跑會載入它的自測），資料檔看哪個模組引用它；前端（`web/` typecheck／test／build 與全端 Playwright）只在動到 `web/` 或請求／回應 schema（`backend/openapi.yaml`）時才跑。不加 `--run` 只列出會跑什麼與原因。完整指令清單見 `AGENTS.md`「怎麼驗證」；CI 用同一份規則，PR 加 `full-ci` 標籤可強制全跑。
再加上該工作流自己的測試（pytest / vitest / go test / Playwright）。PR 描述用 `.github/pull_request_template.md`，「跑過的驗證」欄位要貼實際輸出摘要，不要只寫「已測」。

## 規則
- 一個 PR 一件事、< 400 行 diff（契約 PR 除外）；大功能拆成「契約 → 後端 → 前端」三個 PR。
- 不要產生與 `docs/` 口徑不一致的指標或權限邏輯；有衝突時以 `backend/semantic/*.yaml` 與 `backend/security/*.yaml` 為準並在 PR 說明。
- 不要引入新的第三方套件／映像，除非 PR 裡附授權（白名單見 `LICENSES.md`）與 CVE 檢查結果。
- 模擬資料不可含真實客戶資料；密鑰只能是 secret_ref。
- Commit 訊息中文、說明動機；不要在訊息或程式註解裡放模型名稱或工具名稱。
