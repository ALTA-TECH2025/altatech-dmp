# 6 人分工、用 Claude 寫碼與合併、GitHub 協同方式

> 版本 v0.1（2026-10-14）。搭配 docs/15（里程碑與拆解）。本文只講「怎麼一起做」：誰擁有什麼、Claude 怎麼用、PR 怎麼流、什麼情況才能合併。對應檔案：`CLAUDE.md`、`AGENTS.md`、`.github/CODEOWNERS`、`.github/pull_request_template.md`、`.github/ISSUE_TEMPLATE/story.md`、`.github/workflows/{ci,security}.yml`。

## 1. 6 人分工：一人一條工作流、一個目錄、一個 Claude session

| 工作流（分支前綴） | 人 | 擁有目錄（CODEOWNERS） | 第一個月交付 | 人必須親手做的事 |
|---|---|---|---|---|
| `infra/` 平台與安全 | 平台 | `deploy/`、`.github/`、`LICENSES.md` | 測試環境、6 個映像、Helm、SSO、CI | 密鑰、網路、IAM 的實際設定；授權判斷 |
| `data/` 資料平台 | 資料 | `backend/stream/`、`backend/integration/`、`backend/clickhouse/` | Collector、GA360 匯入、CDC、Flink SQL、ClickHouse 基表／MV、OneID | schema、分區鍵、CDC 前置與 DBA 溝通 |
| `query/` 後端查詢 | 後端 | `backend/api/`、`backend/query/`、`backend/security/` | 語意層編譯器、6 個分析 API、用戶列表、Policy Engine | SQL 樣板審核、權限邏輯審核 |
| `web/` 前端 | 前端 | `prototype/` → `web/` | 原型工程化、逐頁接 API、Playwright 回歸 | 驗收 UX、截圖審核 |
| `agent/` AI 洞察 | TL 兼 | `backend/mcp/`、`backend/insight_agent/`、契約檔 | 契約、golden evals、M4 工具閘道 | 口徑裁決、契約合併、最終 review |
| `qa/` 測試與資料 | QA | `tests/`、`tools/synth/` | 合成資料產生器、golden 答案集、e2e、效能基準 | 定義「正確答案」、驗收 |

原則：**目錄即邊界**。要改別人的目錄就開 issue 給擁有者；要改契約（openapi、semantic、tools、rbac）一律獨立 `contract:` PR，由 TL 當天合併，其他人隔天拉最新再叫 Claude 重生 client／stub。

## 2. 用 Claude 寫碼：兩種用法、各有邊界

| 用法 | 什麼時候 | 怎麼用 | 邊界 |
|---|---|---|---|
| **本機 Claude Code（主要）** | 每個人每天的開發 | 在自己的工作流分支開 session；先讓它讀 `CLAUDE.md`、`AGENTS.md`、issue 的驗收條件；要求「先寫測試，再寫實作，最後跑收工前指令」 | 只在自己目錄；每個 PR 附「跑過的驗證」實際輸出 |
| **Claude Code on the web（雲端 session）** | 平行跑獨立小 story、夜間批次（補測試、修 lint、文件同步） | 每個 session 綁一個 issue 與一條分支，結束時自己推分支開 PR | 一個 session 一個 story，不做跨目錄重構 |

要讓 Claude 寫出可合併的碼，關鍵不是 prompt 技巧，是 repo 自己說清楚規則：
- `AGENTS.md`：目錄邊界、驗證指令、禁止事項（已存在）。
- `CLAUDE.md`：開工前、收工前、PR 規則（本次新增）。
- 契約檔與語意層 YAML：Claude 看 YAML 比看口頭描述準；口徑只在 YAML 定義一次。
- issue 的驗收條件寫成可測試的句子：Claude 會先把它轉成測試。
- 人審的紅線：SQL、權限、密鑰、授權相關的 diff 一定人看；其他可以靠 CI + 一位人類 approve。CI 不呼叫 Claude、不需要任何模型 API 金鑰；要 AI 第一輪審查就在本機 Claude Code 依 AGENTS.md 自審後再開 PR。

## 3. GitHub 協同方式

### 3.1 分支與合併
- **trunk-based**：只有 `main` 一條長期分支；功能分支 `<ws>/<issue>-<slug>`，壽命 ≤ 3 天。
- **合併方式 squash**：一個 PR 一個 commit 進 main，訊息用 PR 標題（中文、說明動機）。
- **main 永遠可部署**：CI 綠、測試環境每日從 main 自動部署（`make deploy ENV=gcp-3n`）。
- **發版**：每兩週從 main 打 tag `v0.x.y`，Helm chart 版本同步；hotfix 直接從 main 開分支、squash、再打 tag。
- 禁止：`git push --force` 到 main、長期分支互相 merge、把契約變更夾在功能 PR。

### 3.2 PR 規則（Branch protection on `main`）
| 規則 | 設定 |
|---|---|
| Required checks | `ci / contract`、`ci / backend`、`ci / web`、`ci / deploy`（依路徑自動跳過）、`security-and-license / scan` |
| Required reviews | 1 位人類 approve + CODEOWNERS 的擁有者 approve（改契約檔時 TL 必審） |
| Dismiss stale approvals | 開啟（push 新 commit 後重新審） |
| Require conversation resolution | 開啟 |
| Linear history | 開啟（squash only） |
| 大小 | diff > 400 行需在描述說明為何不能拆（契約 PR 除外） |

### 3.3 Issue 與看板
- 所有工作從 Story issue 開始（模板：目標、工作流、契約依賴、**驗收條件**、口徑來源、估算）。
- GitHub Projects 看板欄位：Backlog → Ready（驗收條件寫好、契約依賴已合併）→ In progress → In review → Done（合併且測試環境可見）。
- 標籤：`ws:infra|data|query|web|agent|qa`、`contract`、`security`、`perf`、`blocked`、`good-for-claude`（適合丟給雲端 session 的獨立小任務）。
- 每日 15 分鐘「契約同步」只看三件事：今天要合的 contract PR、被 blocked 的 issue、In review 超過 1 天的 PR。

### 3.4 CI 分層（`.github/workflows/ci.yml` 與 `security.yml`）
| Job | 觸發 | 內容 | 時間目標 |
|---|---|---|---|
| contract | 每個 PR | 全部 YAML 可解析、OpenAPI 合規、改契約檔必須帶 `contract` 標籤 | < 1 分 |
| backend | 動到 `backend/` | ruff、骨架自測（policy、transforms、auth、runtime）、pytest | < 5 分 |
| web | 動到 `prototype/` 或 `web/` | `node --check`、Playwright 煙霧（桌機 1440 + 手機 390，無 console error、無橫向溢出） | < 5 分 |
| deploy | 動到 `deploy/` | 每個 values 檔 helm lint、hadolint | < 3 分 |
| security-and-license | 每個 PR 與 push | gitleaks、Semgrep、SBOM、Grype、授權白名單、Trivy 映像 | < 8 分 |
| nightly（QA 工作流 S2 加入） | 每夜 | 口徑一致性全集、dbt tests、1 千萬事件 fixture、e2e 全模組 | < 40 分 |

### 3.5 Renovate 與依賴
- 已設定每週一自動 PR；minor／patch 自動合併，stateful 服務（ClickHouse、Kafka、Flink、PostgreSQL、Valkey）不自動合併，由 infra 工作流評估後合併。
- 新增套件的 PR 必須附授權與 CVE 檢查（`LICENSES.md` 白名單），`security-and-license / scan` 與人工 review 會檢查這一點。

## 4. 一個 Story 的完整流程（範例：漏斗 API 的「每步篩選」）

1. QA 在 issue 寫驗收條件：「步驟 2 加 `page_group = account_open` 後，fixture 中的轉化人數為 12,338；UI 與 agent 工具產生相同 SQL」。
2. TL 確認 `semantic/events.yaml` 已有 `page_group` 維度；若無，先開 `contract:` PR 合併。
3. 後端在 `query/123-funnel-step-filter` 開 Claude Code session：「讀 CLAUDE.md 與 issue #123，先寫 pytest 覆蓋驗收條件，再改 `backend/query/funnel.py`，最後跑收工前指令並貼輸出」。
4. 開 PR（模板填好、貼驗證輸出）。CI 跑 contract/backend；CODEOWNERS 指派後端擁有者；TL 看 SQL 樣板。
5. 修完評論、1 位 approve、conversation 全部 resolved → squash 合併。
6. 當晚 nightly 跑口徑一致性與 e2e；隔天站會 QA 在測試環境驗收，issue 移到 Done。

## 5. 啟用清單（第 1 週 infra 工作流做）
- [ ] GitHub repo：開 `main` branch protection（§3.2）、啟用 Projects 看板與標籤。
- [ ] Secrets：GCP Workload Identity Federation（CI 推映像與部署，不用 SA 金鑰）。
- [ ] 把 `CODEOWNERS` 的 `@*-handle` 換成實際帳號或 team。
- [ ] 每人本機安裝 Claude Code，第一個 session 先跑「讀 CLAUDE.md、AGENTS.md，列出你負責目錄的驗證指令」確認規則被讀到。
- [ ] QA 加入 `tests/e2e/smoke.mjs`（可直接沿用原型驗證腳本）讓 `ci / web` 真正跑 Playwright。
