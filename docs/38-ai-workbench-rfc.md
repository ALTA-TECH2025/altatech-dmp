# 38. RFC：AI 分析工作台（執行引擎 × Skills × DMP Tools）架構與規格草案

> 版本 v0.3 草案（2026-10-28 修訂），對應 Issue #34。**2026-10-28 修訂：** 依審查報告（必改 M1–M17）與 Windy 2026-10-28 的四項決定（受眾送審、錯誤碼照目錄、書籤以系統編號引用、部署用 Docker，見 docs/41「2026-10-28 決定」）更新；程式引用基準改為 `main@c8a87fa`（2026-10-28）；未決項目統一列在 docs/41「待確認」表。以下是 2026-10-21 的狀態說明，仍有效。
>
> **狀態：2026-10-21 規格複審已通過；Windy 已確認 MVP 範圍（docs/41 D1）、執行架構（D2：DMP 後端控制流程、模型用 API 呼叫）、模型可見範圍（D3：目錄 + 彙總結果）與自動化程度（D4：中等）；D5 的管理方式方向（前端技能庫）已提出、細節與業務參數待確認；模型供應商／端點／部署區域待確認。** **已確認範圍（D1–D4、D5 的技能庫基礎管理）已獲 Windy 授權拆小 PR 開發**（2026-10-21「先這樣能確認的先開發」，見 §8 與 docs/41 開頭），依契約 → 後端 → 前端 → 驗收提交、經 Codex 審查後合併。其餘「推薦」仍是提案；未決細節（見 §8「仍待設定」）實作時不得當正式預設，缺設定時明確提示。整份 RFC 仍是草案，合併與否不影響已授權範圍的開發。
>
> 同組文件：
> - docs/38（本文）：現況差距、目標架構、引擎比較、資料流與模型可見範圍、產品流程與文字 wireframe、分期與 MVP、工作拆分、架構決策表
> - docs/39：工具／技能契約草案（工具清單、統一 schema、錯誤碼、上限與預算、run 紀錄、狀態機、權限矩陣、六個技能、SKILL.md 遷移）
> - docs/40：驗收矩陣（客戶場景 × 橫切情境、獨立 oracle、量測方式與門檻）
> - docs/41：使用者確認頁（5 個關鍵決策；哪些已由契約定案、哪些要 Windy 決定）

## 0. 結論先講

1. **現有程式能撐起「問數 → 配置卡 → 乾跑編譯」這一段，其餘都是骨架或還沒有。** `backend/api/ask.py` 與 `backend/insight_agent/configure.py` 是唯一真的接上權限與編譯器的 AI 路徑；`backend/mcp/server.py` 與 `backend/insight_agent/runtime.py` 是示範骨架：沒有認證、不呼叫 `authorize()`、遮罩是寫死的欄位名、審批只是在參數上貼 `pending_approval`、`metric.query` 用 f-string 拼 SQL、預算在查詢跑完才扣、run 不落地（§2）。不能把 `tools.yaml` 的 25 條登記當成 25 個可用工具。
2. **第一階段引擎：「自有輕量 orchestration（DMP 後端內的任務控制器 + 模型 API）」——Windy 2026-10-21 已確認（docs/41 D2 執行架構）；第二引擎用同一驗收集比較。** 理由不是「自己做比較好」，而是本案的硬限制：身分要從 DMP 認證上下文來、每次工具呼叫都重新授權、run 與寫入的冪等由 DMP 持久化、可能部署在永豐機房。Claude Agent SDK 以「每個 session 一個 CLI 子行程 + 本機 JSONL」運作、預設帶 shell／檔案工具；OpenAI 的託管 Agents API 目前僅美國資料駐留且不支援 ZDR（§4，2026-10-20 讀官方文件）。兩者都可以當第二引擎接同一個 DMP Tool Gateway 比較，但第一個落地的不宜是它們。**模型供應商與推論位置是另一個決策**（docs/41 D2、D3），不綁在引擎選擇上。
3. **Issue 建議的 MVP「開戶漏斗診斷 → 原生看板 → 流失受眾草稿」方向對，但要改兩處。**（a）S1 的開戶步驟 00→07 在客戶端「待核定」（簡報 p12、p18B、p18D 第 3 項），語意層也沒有這 8 步的對應（`events.yaml` 有 27 個 `kh_*` 事件，沒有步驟編號；Sheet「事件設計盤點與清理」的 `om_open_acct_flow`「(C)開_07 開戶審核」不在目錄裡），所以「診斷」的數字目前沒有可驗收的口徑。（b）開戶流程中斷者多半還是匿名（Sheet「業務情境驗證」S1：「申辦第一步通常為匿名狀態」），做成受眾也推不進 CDP；客戶自己寫的名單場景是 **S6 新戶滿 7 天、有登入、未交易**（簡報 p12、p15）。切片改成（Windy 2026-10-21 已確認，docs/41 D1）：**S1 開戶漏斗診斷（只讀）→ 開戶主管週報原生看板草稿 → S6「開戶滿 7 天、有登入、未交易」受眾草稿（AI 不送審、不推送；使用者可在畫面上照原分群流程送審，Windy 2026-10-28）**（§7；精確定義見 docs/41 D5）。
4. **口徑有兩個現成衝突，必須先解決，不能在 skill 裡繞過：**「開戶」在規則解析器指 `kh_apply`（完成開戶申請，`configure.py:38`），在語意層指標 `new_accounts` 指 `kh_review_pass`（審核成功，`metrics.yaml:50-53`）；S5「文章瀏覽 14 天內開戶、匿名綁定覆蓋至少 14 天」（簡報 p12、p18D 第 4 項）與 OneID 契約 `login_backfill.window_days: 7`（`oneid_rules.yaml:34`）不一致。實測規則解析器把「文章瀏覽後 14 天內開戶的歸因」配成 7 天回溯、觸點是全部頁面瀏覽（`configure.py:153-154`）。
5. **相依（2026-10-28 以 GitHub REST 查證）：**
   - Issue #33（合成資料按來源規格產生、經真實歸戶服務寫入）是 data-readiness 與驗收 fixture 的前置，**仍 open**；其預置屬性契約 PR #35 已合併 main。
   - PR #51、#52、#37 已合併；#55（遷移數據校驗方法論）已合併。
   - PR #32（日彙總冪等）已於 2026-10-21 合併，後續 #66 也已合併；D-14 後日彙總重建要改用 `one_id_eff`（docs/45 D-16，功能 PR 待開）。
   - **#74（D-14 契約：事件只增刪不改寫、OneID 回補改為查詢時換算）仍 open**：data-readiness 的回補欄位與 A04b、A07 相依它（docs/39 §3.3）。
   - **#63（Tool Gateway 正式化）仍 open**：A12、A13、A15、A16、A23 的自測在它裡面。
   - 資料邏輯總表 #38：data-readiness 的歸戶品質依賴 #41（接入即歸戶）與 #39（事件不重複），兩者仍 open；回填一致性 #42；其餘見 #43–#50。
   - #29（props JSON 安全）已於 2026-10-20 合併（`9512adf`）。
6. **權限落差：** 現行 `rbac.yaml` 下行銷沒有 `overview.use`，**不能建立看板**（`dashboards.py:101-104`；本機以 `authorize()` 實測）；`svc_insight` 連 `events.view` 都沒有。Windy 2026-10-29 已定：依角色權限，管理員可指派含 `overview.use` 的自訂角色（docs/41 1029-8）。
7. **既有 skills 不能直接搬：** 9 個 `skills/*.md` 把事件名、門檻（樣本 < 200）、甚至由模型調整 confidence 的「證據覆核」寫在 prompt 裡（§6、docs/39 §8）。建議遷成標準 `SKILL.md` 套件、口徑一律從版本化契約取，**覆核改成確定性重跑 + oracle，不用另一個模型判斷數字對不對**。

## 1. 依據與讀取紀錄

| 來源 | 版本／讀取時間 | 讀了什麼 | 沒讀到／限制 |
|---|---|---|---|
| 程式與文件（2026-10-28 修訂） | `main` @ `c8a87fa`（2026-10-28，#90 合併後） | §2.1 重新核對的程式；`backend/api/errors.yaml`、docs/44、45、46；`backend/insight_agent/journal.py`、`model.py`、`skill_loader.py`、`skills/*/SKILL.md`；openapi `/v1/agents/*`、`/v1/segments*`、`/v1/bookmarks*` | 行號會隨 main 移動，§2.1 改以檔名與函式名引用 |
| 程式與文件（初版） | `main` @ `9512adf`（2026-10-20，#29 合併後） | AGENTS.md、CLAUDE.md、docs/03、05、17、19、22、27、30、31、37；§2 列的程式 | — |
| 客戶場景簡報 | Google Slides「永豐金證券｜數位軌跡採集與分析平台（提案協作）Ver 0.1」，id `1efbw1YK…PMUl0`，Drive modifiedTime 2026-10-20T07:59:55Z | 以 Drive 文字匯出讀全部投影片的文字；本文以投影片上印的頁碼引用（p7–p15 需求、p12 十個場景、p15 新開戶 7 天、p18A–18D 事件盤點、p19–p21 範圍與架構、p30 選配「Agent 問數：待討論」、p36–37 Agent 擴展、p39 方法論） | 圖片內容無法讀：p36–37 模擬 UI、p40–44 產品畫面截圖、最後 5 張「需求釐清」頁只有標題文字（內容疑為圖片）。Merkle 待補頁（p4、p18、p29–30 表格）為空白。 |
| 規格試算表 | Google Sheets「數軌需求確認表_廠商回覆版_20261015_pao」，id `1oSAOIqE…YJU`，revisionId `APfK_pDOJPF7…`（讀取時），Drive modifiedTime 2026-10-19T16:14:14Z | 「填寫說明」A1:F21；「需求確認表」A1:T44（含 M 欄 Data-DI 補充）；「業務情境驗證」A1:K11（S1–S10）；「資料量盤點」A1:J30；「事件設計盤點與清理」、「SDK預置屬性」、「用戶表」只讀表頭與前幾列 | 「SDK 全埋點事件表」未讀；「事件設計盤點與清理」1,532 列未逐列讀。**「需求確認表」M 欄表頭寫明「內部對比 還要再改，不會提供給客戶」**：本文引用 M 欄時只當廠商內部判斷，不當客戶確認。 |
| 官方文件（引擎比較） | 2026-10-20 讀取 | Claude Agent SDK：overview、skills、sessions、hosting；Claude Code skills（frontmatter 與 Agent Skills 標準）；OpenAI：Agents 總覽、Agents API overview／sessions／self-hosted sandbox、Agents SDK running agents／guardrails & approvals／sandbox agents／models、Skills（tools-skills）、Plugins skills concept | 只讀上述頁面；價格、區域可用性、合約條款未逐項查證，列為未決（§4.3）。 |
| GitHub | 2026-10-20（初版）；2026-10-28 以 REST 複查 | 初版：Issue #33、#34；PR #29（merged）、PR #32（當時 open）、PR #35（#33 的預置屬性契約，當時 open，2026-10-20 已合併）。複查：#60、#61、#62、#64、#65、#32、#73 已合併；#63、#74、#76 仍 open；Issue #33 仍 open | — |

## 2. 現況核對：已實作／骨架／待新增

> 「已實作」= 有程式且有自測；「骨架」= 有程式但不能當正式能力；「契約有、未實作」= 只在 yaml／openapi；「待新增」= 都沒有。

### 2.1 對照表

> 2026-10-28 依 `main@c8a87fa` 重新核對；「PR」欄是把該項帶進 main 的 PR，或仍在處理中的 PR（標 open）。只把已合併的 PR 算成已有。

| 能力 | 狀態（main@c8a87fa） | PR | 證據（檔案、函式） | 說明 |
|---|---|---|---|---|
| 問數 → 配置卡 | 已實作 | — | `backend/api/ask.py`、`backend/insight_agent/configure.py` | router：`ai_ask.use` 授權 → 解析 → 每張卡以當事人身分 `compile_kind` 乾跑 → 審計 `dashboard.asked`。不存卡片。 |
| 解析器：規則 | 已實作 | — | `configure.py` `SYNONYMS` 等；評測 `evals/ask_golden.yaml` | 2026-10-28 本機跑 `python3 -m backend.insight_agent.configure`：model 20/20、params 20/20。**事件同義詞、指標字、維度字仍寫死在程式**，與語意層是兩套來源；`SYNONYMS` 仍把「開戶」對到 `kh_apply`（W5 未做，A03 在修正前會失敗）。 |
| 解析器：模型 | 已實作（結構化輸出） | — | `configure.py` `LLMParser` | 提示只放語意層目錄，不放資料；拒答回空卡。供應商專屬參數寫在呼叫裡；預設模型 ID 寫死（`configure.py`、`backend/api/main.py`）。這是既有問數路徑，與工作台的 `ModelDriver` 是兩條路。 |
| 可用分析種類 | 已實作 5 種；契約已對齊 | #60 | `configure.py`、`backend/api/analytics.py`；`tools.yaml` `analysis.configure` | 問數只能出 events／funnel／retention／path／attribution；`tools.yaml` 的列舉已在 #60 改成同一組。 |
| 信心度門檻 | 已實作，但用模型自評 | — | `configure.py` | `confidence ≥ 0.6` 預設勾選、`< 0.3` 只給候選；LLM 路徑的 confidence 是模型自報。乾跑只驗格式與權限，不驗口徑是否答對問題。 |
| 看板卡片存查詢（原生看板） | 已實作；草稿與冪等只在契約 | #60（契約） | `backend/api/dashboards.py`；openapi `DashboardCreate.draft`、`POST /v1/dashboards` 的 `Idempotency-Key` | 卡片存 `kind + query`，存前以建立者身分編譯；資料以檢視者身分重算。`draft`、`Idempotency-Key` 已在 openapi，**`dashboards.py` 尚未實作**；看板沒有版本欄位（docs/39 §3.1）。 |
| 書籤（存起來的分析，含漏斗） | 已實作 | — | `backend/api/bookmarks.py`；openapi `/v1/bookmarks*` | 編號 `bm_` + 10 碼 hex，由後端建立時產生；看板卡片以 `bookmark` 欄位引用書籤編號。Windy 2026-10-28：書籤與漏斗一律以這個編號引用（docs/39 §1.2）。同專案書籤不可重名（回 409 `CONFLICT`）由另一個後端 PR 實作中。 |
| 看板解讀 | 已實作（規則為主、模型為輔） | — | `backend/api/dashboard_share.py` | 規則算事實，模型只拿事實寫敘述；模型失敗回規則式。可作為「數字由確定性程式算、模型只敘述」的範本。 |
| HTML 看板、定時寄送 | 已實作 | — | docs/30 §3–4；`dashboard_share.py`；`backend/api/app.py` | 排程以擁有者「現在」的角色重算——**每次排程重新授權的現成做法**。背景例行已有主控租約，多份 worker 只有一份在跑（#73，docs/45 D-22）。 |
| 分群預估、建立、送審 | 已實作；`draft` 建立入口只在契約 | #60（契約） | `backend/api/segments.py`；`backend/segments/engine.py` `SegmentService.create`／`submit`／`approve`／`list` | `create` 對數據分析／數位經營／admin 直接 `active`，其餘與 `agent:` 開頭的 user_id 為 `pending_approval`；openapi `SegmentCreate.status: draft`（私人草稿）已在 #60，**服務層未接**。現行 `draft` 只出現在「被退回」，可再送審；`list()` 不分擁有者。`POST /v1/segments/{id}/submit` 已實作但 openapi 未登記。私人草稿與被退回草稿要分開（docs/39 §3.2）。 |
| 審批 | 已實作 | — | `backend/approvals/engine.py` `APPROVER_ROLES` | 已有 `agent_proposal`、`segment`、`push`、`dashboard_schedule`、`export` 等類型與審批角色。 |
| 錯誤碼目錄 | 已實作 | #82、#90 | `backend/api/errors.yaml`、`backend/api/errors.py`；docs/46 | REST 與 AI 工具共用；工具只能用 `scope` 含 `tool` 的 12 個代碼（docs/39 §2.3）。 |
| Policy Engine | 已實作 | — | `backend/security/policy.py` `authorize`；`backend/semantic/compiler/policy.py` | 模組權限、行級、欄位遮罩、K 匿名（K=20，`rbac.yaml` `k_anonymity`）。行級條件以字串組裝（值未跳脫；來源是 IdP 映射，不是模型）——工具閘道不得讓模型影響這段輸入。 |
| 專案（project）範圍權限 | 未實作（fail-closed） | — | `policy.py` `authorize`；`rbac.yaml` | `scope: project` 的自訂角色落到 `1=0`；查詢的 `project` 只是 WHERE 條件（編譯器預設 `altatech_dmp`）。程式裡沒有 tenant 概念（單一部署 = 單一租戶）。`tools.yaml` 的丟棄清單沒有 `project`（docs/39 §2.1）。 |
| 查詢預算 | 已實作（編譯器層） | — | `backend/semantic/compiler/budgets.yaml`；`analytics.py`（EXPLAIN ESTIMATE） | ClickHouse settings（時限、讀取量、列數）隨 SQL 送出，伺服器端兜底。`/v1/semantic/compile` 只對 events 回估算。 |
| 資料新鮮度 | 部分 | #60（readiness 契約） | `backend/ops/service.py`；`backend/api/ops.py`；`backend/ops/sla.yaml` | 只有全域 `max(time)`，且要 `governance.view`。openapi 已有 `POST /v1/data/readiness`，**未實作**。 |
| 歸戶品質（已識別／匿名／衝突／未匹配） | 待新增 | — | `backend/api/meta.py`（只回規則）；`oneid_rules.yaml` | 有規則與審計事件，沒有可查詢的品質統計端點；也沒有可驗證的 fixture（#33 仍 open）。回補依 D-14 改成查詢時換算，契約 #74 仍 open。 |
| 黃金測試與 oracle | 已實作 | — | `backend/semantic/compiler/golden.py`、`oracle.py` | 純 Python 參考實作逐列比對（誤差 0）。**可直接當 AI 數字正確性的獨立 oracle**，但資料集是固定種子、不經真實歸戶（#33 要補）。 |
| run 紀錄、狀態機、寫入冪等 | 已實作（儲存層） | #62 | `backend/insight_agent/journal.py`（`MemoryJournal`、`PostgresJournal`） | 狀態機、租約世代圍籬、`agent_operations`、與產物同交易寫入；表與 `PostgresStore` 同一個 schema。**還沒接到 API 與 Gateway**；寫入直接插 `segments`／`items` 表、跳過服務層驗證，與「轉接既有服務」並存（docs/39 §1，待確認 Q27）。 |
| 模型介面 | 已實作（未接正式供應商） | #64 | `backend/insight_agent/model.py`（`ModelDriver`、`TestDoubleDriver`、`ApiDriver`） | 缺端點設定、`approved_params` 或 transport → `ModelNotConfigured`（`MODEL_ENDPOINT_NOT_CONFIGURED`）。`ModelRequest` 會送 `system`（skill 本文）與 `messages`（含使用者問題），見 §5.2。`ModelTurn.kind` 沒有「拒答」。 |
| SKILL.md 載入器與技能 | 已實作 2 個技能 | #64、#65 | `backend/insight_agent/skill_loader.py`；`skills/data-readiness/SKILL.md`、`skills/explore-data/SKILL.md` | 載入即驗 `requires_contracts`（版本逐段比對）、擋事件名直寫。`contract_versions()` 只回 semantic／tools／oneid_rules。9 個舊 `.md` 仍在，`agents.yaml`／`runtime.py` 仍讀舊檔。 |
| 技能庫 API 與 `ai_skills` 權限 | 契約有、未實作 | #61 | openapi `/v1/agents/skills*`、`/v1/agents/configs*`；`rbac.yaml` `ai_skills` | 角色分配仍是提案（核定前只有 admin）。 |
| Agent Runtime（舊） | 骨架 | — | `backend/insight_agent/runtime.py` | 計畫固定為一次 `semantic.search`；無模型呼叫；`Run` 只在記憶體；confidence 固定；skill 以整段文字讀入。 |
| MCP Tool Gateway | 骨架 | #63（open） | `backend/mcp/server.py`（見 §2.2） | 只有 `semantic.search`、`metric.query` 兩個示範 handler（`metric.query` 仍是 f-string SQL）；沒有 MCP 傳輸層。正式化在 #63，未合併。 |
| Agent 註冊表 | 契約有、與實作衝突 | — | `backend/insight_agent/agents.yaml` | 排程 agent 身分寫 `svc-insight-readonly`，RBAC 只有 `svc_insight`；`svc_insight` 是 `aggregate_only`，`authorize()` 對事件回 `row_filters=['1=0']`，**排程 agent 會靜默拿到 0 列**。`funnel-doctor`、`churn-radar` 以服務身分 `segment.propose`，與「以發起人身分重新授權」相違。 |
| `/v1/agents/runs*`（建立、狀態、取消、重試、回答、證據） | 契約有、未實作 | #60 | openapi `/v1/agents/runs*` | `backend/api/` 沒有對應路由。舊的 `/v1/agents`、`/v1/agents/{id}/run`、`/v1/agents/ask` 與新路徑並存（待確認 Q23）。 |
| 遮罩給 agent 的敘述過濾 | 契約有、未實作 | — | `backend/security/masking.yaml` `agent:` | `apply_before_return`、`narrative_regex_block` 在程式中沒有引用；而且只管輸出，不管送往模型的使用者輸入（§5.2）。 |
| 預算：每 run token、每 agent 每日掃描 | 契約有、未實作 | — | `backend/mcp/tools.yaml` `gateway.budgets` | 程式中沒有引用 `per_run_llm_tokens`、`per_agent_daily_scan_bytes`。 |
| 證據查詢 | 契約有、未實作 | #60 | openapi `GET /v1/agents/runs/{run_id}/evidence/{evidence_id}`、`Evidence` | 現在的「證據」只有 `ask` 回傳的 `sql_preview` 與審計裡的 `sql_hash`；沒有可回查的證據物件。 |

### 2.2 MCP 骨架缺口（逐項）

> 2026-10-28：正式化在 #63（仍 open），下表仍是 main 現況。

`backend/mcp/server.py` 註解寫「套身份與權限 → 檢查預算 → 執行 handler → 列級遮罩 → 寫審計事件」，實際程式：

| 項目 | 程式現況 | 缺口 | 正式設計（docs/39） |
|---|---|---|---|
| 身分 | `Caller(user_id, roles, …)` 由呼叫端自己填（`server.py:24-29`）；runtime 只給 `user_id`、roles 空（`runtime.py:44`） | 沒有認證；誰都能宣稱是誰；roles 不從 IdP／指派來 | Gateway 只接受 DMP 簽發、綁 run 的短效憑證；Subject 一律由 `get_subject` 同一條路徑重建（`app.py:41-48`），排程用 `owner_roles`（`app.py:74-89`）；**模型輸入裡不出現 user_id／roles 欄位** |
| Policy Engine 接線 | `call()` 完全沒有呼叫 `authorize()`（`server.py:84-100`） | 繞過權限 | 每次工具呼叫：`authorize(subject, action, module)` → 編譯器注入（`compiler/policy.py:81`）；`decision=None` 的測試路徑（`compiler/events.py:199`）在 Gateway 禁用 |
| 遮罩 | 寫死 4 個欄位名（`server.py:58`），只在 `masking: strict` 時套 | 與 `masking.yaml` 脫鉤；L2 金額、`one_id` 尾碼規則都沒套 | 一律用 Decision.field_policies 在 SQL 層遮罩（編譯器已做），回傳前再套 `narrative_regex_block` |
| 審批 | `approval: required` 只是把 `status: pending_approval` 塞進參數（`server.py:90-91`） | 沒建審批單；handler 可忽略 | 寫入類工具只產生私人草稿；送審（建立審批單）與核可都由人在 UI 操作（D4；受眾送審見 Windy 2026-10-28 決定），生效永遠走人工核可 |
| Handler | 2 個示範（`server.py:104-124`）；`tools.yaml` 只有 2 條寫 `handler:`（`tools.yaml:29`、`34`） | 23 條工具沒有實作 | 每條工具對應既有 REST 服務函式（docs/39 §1），Gateway 只做轉接 |
| SQL | `metric.query` 用 f-string 拼 SQL（`server.py:122`），繞過編譯器、未跳脫 | 注入風險；口徑不一致；違反 AGENTS.md「agent 不得直接寫 SQL」 | **刪除此示範**；一律 `compile_kind`／`execute_kind`（`analytics.py:131-170`） |
| 查詢前預算 | `budget.charge()` 在 handler 執行後才扣（`server.py:96-97`）；預算物件在記憶體、每個 Gateway 實例一份（`runtime.py:45`） | 超支後才知道；重啟歸零；token、每日上限沒做 | 執行前 EXPLAIN ESTIMATE 估掃描量 → 超過 per-call／per-run 剩餘額度直接拒絕（`BUDGET_EXCEEDED`）；用量寫 run 紀錄（PostgreSQL） |
| 審計 | `print()` 到 stdout（`server.py:81`），`project` 寫死（`server.py:71`） | 審計不落地、不可查 | 寫同一個 `store.audit`，帶 run_id、step_id、operation_id、args_digest、契約版本 |
| 持久化 | 無 | 重啟即遺失 | run／step journal（docs/39 §5–6） |
| 傳輸層 | 無（註解寫正式版用 FastMCP 或官方 SDK） | 外部 MCP client 無法連 | 新增依賴需先過 `LICENSES.md` 白名單與 CVE 檢查（CLAUDE.md 規則） |

### 2.3 程式現況與 Issue 敘述不一致之處

- Issue 說 `ask.py`「按使用者身分乾跑 compile_kind」——正確；但 run_id（`configure.py:260`）只寫進審計，**不能接續操作**，「沿用同一分析配置與證據接續」目前做不到。
- Issue 列的候選 skills（analyst-copilot、funnel-doctor、churn-radar、campaign-review、dashboard-reporter）存在，但另有 `anomaly-watch`、`ask`、`reviewer`、`strategy-loop`；其中 `reviewer` 以模型調 confidence（`skills/reviewer.md:7-10`），與 Issue「數字正確性不能只靠另一模型」衝突。
- Issue 稱 `agents.yaml` 已登記「身份」：登記了，但服務身分名稱與 RBAC 對不上、且該身分在 Policy 下看不到資料（§2.1）。
- 開戶相關口徑：Issue 以「開戶轉換」為 MVP，現有兩套「開戶」定義互相矛盾（§0 第 4 點）。

## 3. 客戶場景與工作台入口

依簡報 p12 與 Sheet「業務情境驗證」S1–S10（只列客戶文件寫的，不另造場景）：

| 編號 | 客戶寫的流程（簡報 p12／Sheet） | 主要入口 | 現有能力能否表達 | AI 工作台的前置缺口 |
|---|---|---|---|---|
| S1 | 開戶 00→07（步驟待核定）＋後端審核完成；依來源渠道比較；線下完成事件併入 | 分析 → 看板 | 漏斗可（窗口 1 分–90 天、分組）；Sheet M 欄（廠商內部）寫 9 步、窗口 30 天 | 步驟對應未核定、未入語意層；匿名首步需裝置 ID 比對（Sheet S1 G/I 欄）；線下審核 T+1 |
| S2 | 學堂文章→策略選股→加入自選→3 天內成交 | 分析 | 漏斗可 | 跨站匿名→登入只回溯 7 天（`oneid_rules.yaml:34`） |
| S3 | 推播送達→開啟→加入自選→3 天內成交 | 分析 | 漏斗關聯屬性可 | 送達事件能否進 GA 待 Merkle 確認（Sheet S3） |
| S4 | 早報 Deeplink→App 頁面→加入自選→下單 | 分析 | 路徑可 | 延遲深層連結斷軌屬 SDK |
| S5 | 文章瀏覽→14 天內開戶；匿名綁定覆蓋至少 14 天 | 分析 | 歸因可（回溯窗口自訂） | **OneID 回填 7 天 vs 需求 14 天**；規則解析器把 14 天配成 7 天（`configure.py:154`） |
| S6 | 開戶後登入／首交；T+1 圈選未交易者發簡訊；每人限發一次 | 分析 → 受眾 → 定期 | 留存／漏斗按開戶日 cohort、分群可 | 發送屬 MA；DMP 只到受眾草稿與送審（推送是 P4） |
| S7 | 禮品卡瀏覽→購物車→結帳→後端訂單（購物車代碼串接） | 分析 | 漏斗關聯屬性可 | 訂單／付款不送 GA，需後端事件 |
| S8 | App 前景→功能頁→下單意圖／背景或逾時 | 分析 | 路徑可（大期間抽樣） | 背景與逾時分開判定（p18B） |
| S9 | 推播、簡訊、EDM、LINE 點擊→App→下單 | 分析 | 歸因可 | 發送紀錄回流（簡報 p14 步驟 9 分工待討論） |
| S10 | 活動頁登錄→任務完成→得獎名單，回寫 CDP | 受眾 | 分群、匯出可 | 「行為操作 ≠ 交易達標或得獎」（p18B）；回寫介面需客戶端開發（Sheet S10） |

另外，簡報 p36–37 的 Agent 擴展頁標「模擬 UI 畫面，非實際產品功能」，p37 列「發送 LINE／Email、更新 CRM 標籤、呼叫外部 API」等自動執行；**本 RFC 不把這些列入 P1–P3**，觸達與外部寫入一律在 P4 且走既有審批（§5.3）。

## 4. 執行引擎比較與推薦

### 4.1 比較（2026-10-20 依官方文件；未查證處另列）

| 面向 | A. 自有 orchestration + 模型 API／工具 | B. Claude Agent SDK | C. OpenAI Agents SDK（應用內）／Agents API（託管 Codex harness） |
|---|---|---|---|
| 執行形態 | DMP 後端內的任務控制器 + 工具呼叫迴圈；模型經企業 LLM Gateway 或供應商 API | 函式庫會「spawn 並監管一個 CLI 子行程」，子行程持有 shell、工作目錄與本機 session 檔；一個 session 一個子行程（hosting 頁） | SDK：runner 在你的應用內跑迴圈、handoff、approval；Agents API：OpenAI 執行 harness、管理 session，可接自管 sandbox（agents 總覽、Agents API overview） |
| 部署位置 | 與 dmp-core 同一套 Docker Compose 部署：第一階段機房 3 台 VM＋Docker Compose、不上 Kubernetes（docs/45 D-01，docs/44 §2；套件 #76 仍 open）；GCP 方案與第二階段才用 Helm。第一階段只啟用 web、core、batch、ingest 四個映像，**agent 映像不啟用**（D-05、docs/44）。run 控制器與 worker 放哪個映像、哪個容器**待確認 Q25**（研發＋運維） | 同上；另外每個 session 起始建議 1 GiB RAM／1 CPU／5 GiB 磁碟、需容器隔離，要放到 3 台 VM 的規格（docs/45 P-02，待永豐資訊部門回覆）底下重算 | SDK：同上；Agents API：harness 在 OpenAI，**「目前僅支援美國資料駐留、不支援 ZDR；自管 sandbox 也不會變成 ZDR」**（Agents API overview） |
| 資料流向 | DMP → 模型端點只送：語意目錄、經政策處理的彙總（§5）；工具在 DMP 程序內執行 | 子行程 → 模型端點（Anthropic API、Amazon Bedrock 或 Google Cloud 區域端點）；工具經 MCP 連 DMP Gateway；**內建 shell／檔案工具必須關閉** | SDK → OpenAI 模型端點；Agents API 另把 session 狀態存在 OpenAI |
| Skills 載入 | 自寫 SKILL.md 載入器（frontmatter + 本文 + 參考檔），只用 Agent Skills 標準欄位 | 只從檔案系統載入（`settingSources`／plugins 路徑），**沒有程式化註冊 API**；`skills` 選項可限縮清單 | Responses 託管 shell：上傳版本化 skill bundle；本機 shell：檔案路徑；Agents API：sandbox 內 capability directories；SDK sandbox：Skills capability。**三種載入方式不同** |
| 身分傳遞 | Subject 在程序內傳給 Gateway，模型看不到 | 由 DMP 為每個 session 簽發綁 run 的 MCP 憑證；需防 shell 讀到環境變數（故關閉內建工具） | SDK：function tool 在你的程序內，可帶 context；Agents API：工具呼叫回到你的應用（required_actions） |
| 持久化／重試 | run／step journal 存 PostgreSQL（既有 `store_pg`），完全自控 | session 是本機 JSONL；跨主機要 `SessionStore` 鏡像，**鏡像寫入 best-effort，失敗會丟批次並發 `mirror_error`**（hosting 頁） | SDK：session 存你的儲存、approval 中斷可序列化 state 後續跑；Agents API：伺服器保存 session，送訊息支援 Idempotency-Key |
| 長任務 | 背景 worker＋狀態機。第一階段只有主機 1 的 dmp-core worker 開背景作業（`DMP_ROLE=worker`，docs/44 §2、§5a），例行作業已有主控租約（#73，D-22）；run 層的租約（`journal.py`，`lease_s: 60`）與背景作業主控租約的關係、控制器跑在哪個容器，待確認 Q25 | 無頂層 session 逾時，只能用 `maxTurns` 限制；長 session 記憶體成長 | Agents API 為長任務設計（非同步 turn、webhook）；SDK 由你的 worker 承擔 |
| 成本驅動 | 模型 token（目錄摘要可快取）、工具呼叫次數、ClickHouse 掃描 | 同左 + 每 session 子行程常駐記憶體；官方：token 成本通常遠大於容器成本 | 同左；Agents API 另計託管 sandbox／工具費率 |
| 維運 | 少一個執行期；但迴圈、重試、上下文壓縮、多供應商轉接要自己維護 | CLI 二進位隨 SDK 版本釘選；需管子行程、隔離、egress | SDK：函式庫升級；Agents API：外部託管依賴 |
| 與現有程式契合 | 高：`configure.py` 結構化輸出、`dashboard_share.py` 規則事實＋模型敘述已是這個形狀 | 中：要另包一層 MCP Gateway 與 session 管理 | 中：同 B；模型供應商不同時要另寫 Parser |
| 條款 | 依模型供應商合約 | 受 Anthropic Commercial Terms 規範；第三方產品不得提供 claude.ai 登入；品牌規範（overview 頁） | 依 OpenAI 條款（未逐項查） |

### 4.2 推薦

- **第一階段：A（自有輕量 orchestration）。** 任務控制器、run 狀態機、工具閘道本來就是 DMP 必須自己負責的部分（Issue 要求的「任務控制器／持久化 run → 可替換執行介面 → DMP Tool Gateway」），A 只是在控制器裡放一個很薄的「模型驅動器」介面（輸入：skill、目錄摘要、上一步工具結果；輸出：下一個工具呼叫或結構化草稿）。本案的任務是**有界的**（最多十幾次工具呼叫、產物是結構化配置），不需要通用 coding agent 的 shell 與檔案能力。
- **限制（必須寫清楚）：** 自己維護迴圈與上下文管理；不同供應商的 tool-use 格式各寫一個轉接；沒有現成 subagent／compaction。若之後要做開放式探索（例如多步、多分支的自由分析），A 的成本會上升，那時才是 B／C 的強項。
- **第二引擎比較計畫：** P1 驗收集（docs/40 §2 的 A 集）通過後，把同一組 skills（SKILL.md）與同一個 DMP Tool Gateway（MCP）接到 **B（Claude Agent SDK，`settingSources: []`、只開 MCP 工具與 Skill、關閉內建工具、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`、每 run 獨立 `cwd` 與 `CLAUDE_CONFIG_DIR`）**；若 D2 決定模型供應商是 OpenAI，則改接 **C 的 Agents SDK（不是託管 Agents API）**。比較指標：A 集通過率、工具呼叫數、token、延遲 P50／P95、失敗恢復行為、維運工作量。引擎不直接寫任何東西：所有寫入經 Gateway 帶操作鍵，**換引擎不會重複寫入**（docs/39 §6）。
- **不採用：** 不把託管型 harness（OpenAI Agents API、Anthropic Managed Agents）放第一階段——資料駐留與「線下資料不出永豐」（簡報 p9、Sheet「填寫說明」第 2 段第 1 點）尚無法對齊。不預設「某模型分析、另一個模型審查」的產品分工；審查數字靠 oracle（docs/40）。

### 4.3 未決（需查證或決策）

- 模型推論端點能放在哪裡：永豐證 GCP 專案內的託管端點、企業 LLM Gateway、或境外 API——這是資安／法遵問題（簡報 p11「去識別化交易資料可否上雲」同一類），**不是工程能定的**，放 docs/41 D2／D3。
- 各供應商在台灣或亞洲區域的模型可用性、資料保留條款、價格：本輪未查證。

## 5. 資料流、模型可見範圍、身分與注入

### 5.1 資料流

```
使用者（DMP 前端，SSO session）
  → DMP API：建立 run（身分＝session 的 Subject；排程＝擁有者「現在」的角色）
  → 任務控制器（PostgreSQL run／step journal）
      → 模型驅動器 ⇄ 模型端點        ← 只送：skill 指令（SKILL.md 本文＋技能庫 notes_md）、使用者問題文字與畫面上下文、語意目錄摘要、工具結果的「模型可見投影」（§5.2）
      → DMP Tool Gateway：每步 authorize() → 編譯器（行級／遮罩／K 匿名／預算）→ ClickHouse
      → 寫入類：只產生私人草稿（帶操作鍵）；送審與核可由人在 UI 操作
  → 結果：配置卡、證據引用、草稿 ID；畫面上的數字一律由 Gateway 直接回前端，不經模型轉述
```

交易資料、帳務、客戶資料一律留在 DMP（ClickHouse），不送 GA（簡報 p8–p9）；啟用 AI 不改變任何資料出境許可。

### 5.2 模型可見範圍（Windy 2026-10-21 確認 M0 + M1，docs/41 D3）

| 層級 | 內容 | 預設 |
|---|---|---|
| M0 目錄 | 事件／指標／維度名稱、中文名、說明、caveats、契約版本（現行 `catalog_digest`，`configure.py:182-186`） | 允許 |
| M1 結構化結果摘要 | 每個工具結果的「模型可見投影」：≤ 200 列彙總、已套檢視者遮罩與 K 匿名（被抑制列只給 `suppressed`，不給人數，同 `analytics.py:172-181`）、L2 數值一律區間化（即使檢視者可看明文）、無 one_id／帳號／裝置 ID；下鑽結果同樣只給投影 | **已確認允許** |
| M2 明細 | 個人時間軸、用戶列表列、身分圖 | **不允許**；`profile.*`、`users.list` 不進 P1–P3 工具清單 |
| 使用者輸入與技能說明 | `model.py` `ModelRequest` 的 `system`（SKILL.md 本文，以及技能庫的 `notes_md`）與 `messages`（使用者在工作台輸入的**問題文字**、`AgentRunCreate.context` 的畫面上下文） | **這一條路徑舊稿漏列（2026-10-28 補）**。使用者可能在問題裡打客戶姓名、身分證或帳號，這些文字會原樣送往模型端點。`masking.yaml` 的 `agent.narrative_regex_block: [tw_id, mobile, email]` 只管模型輸出，而且 main 上沒有程式引用它。送模型前要不要過同一套 regex、命中時遮掉或拒收、`context` 能帶哪些欄位，**都還沒定**；問題文字能不能出境併入待確認 Q12（永豐資安、法遵＋Windy），定案前不得預設為允許 |
| 畫面 | 使用者在前端看到的完整結果（依其權限） | 由 Gateway 直接回前端，不經模型 |

模型產生的敘述在回傳前過 `narrative_regex_block`（`masking.yaml` `agent:`，待實作），並檢查「敘述裡出現的數字必須能在證據中找到」。

**兩條路要分開**：給模型的是 M1 投影（Gateway 從查詢結果另外產生）；使用者畫面上的數字是 DMP 查詢結果直接回前端，依使用者本人權限，不經模型產生或轉述。模型敘述只能引用投影裡有的數字，畫面上的表格與圖不由模型文字產生。K 值與金額區間：現行安全下限照常適用；模型投影專屬參數未核定，正式啟用條件見 docs/39 §2.4。

### 5.3 自動化界線（Windy 2026-10-21 確認「中等」，docs/41 D4）

人工操作也遵循既有 RBAC 與審批流程，不因 AI 建立了草稿就取得額外權限。

| 動作 | 誰能做 | 方式 |
|---|---|---|
| 字典、資料品質、彙總探索（預算內） | 有對應模組 view／use 的使用者 | AI 自動做，不逐步詢問 |
| 建立私人草稿（看板、受眾規則、排程提案） | 發起人 | AI 直接完成並回可編輯草稿；**草稿不生效**（受眾不計算推送、排程不啟用） |
| 口徑有實質歧義 | — | 停下來問（例如「開戶」= 申請或審核通過；docs/39 §8 歧義處理）；客戶已確認的口徑直接沿用 |
| 共享、發布、排程啟用、受眾送審、匯出、推送／觸達 | 依 `rbac.yaml` 與審批角色（`approvals/engine.py:26-28`） | 使用者在 UI 操作；AI 只能預填表單，**不能送審（不建立審批單）、永遠不能核可**（受眾送審：Windy 2026-10-28） |
| 改事件契約、歸戶規則、權限 | 治理流程（元事件變更單、雙人複核） | AI 不提供工具 |

結果分四類標示：**資料觀察**（查詢直接得到）、**相關性**（分群差異、同時發生）、**假設**（可能原因，需標「可能」並附驗證查詢）、**因果證據**（只有對照組／實驗設計時才可用；P1–P3 不產生）。模型自評 confidence 不作任何執行門檻；門檻用可驗證條件（乾跑成功、口徑版本一致、樣本量、資料新鮮度）。

### 5.4 身分與提示注入

- 執行身分只從認證上下文取得（互動：session 的 Subject；排程：`owner_roles` 的現在角色；服務身分不代替使用者做分析）。工具 schema 不含 `user_id`／`roles`；模型輸出若出現這類欄位直接丟棄並記錄。
- 每次工具呼叫與每次排程執行都重新 `authorize()`；中途撤權 → 該步 `AUTHZ_DENIED`、run 進 `failed`（不重試），已產生的私人草稿保留但標示「建立者已無權限」。
- 讀到的資料（事件屬性值、頁面標題、文件、看板註記、技能參考檔）一律當資料，不當指令：工具結果以結構化欄位回傳、字串欄位標記為不可信；工具權限只由 run 的 skill 白名單與使用者權限決定，**任何讀到的內容都不能擴張工具清單或參數範圍**。
- 結果、快取、記憶、證據都以（部署, project, 使用者有效權限指紋）隔離；不做跨使用者共用的模型記憶。

## 6. 產品流程與文字 wireframe（MVP 切片）

### 6.1 工作輸入

```
┌ AI 分析工作台 ───────────────────────────────────────────────┐
│ 你想了解什麼？                                               │
│ [ 上週線上開戶轉換為什麼下降？                         ] [送出] │
│ 入口：(•)分析 ( )探索 ( )看板 ( )受眾      期間：上週（自動）  │
│ 資料狀態：行為事件 至 10/19 23:50 ✓  線下審核 至 10/18 ⚠ T+1  │
│ 口徑：開戶漏斗 v?（待核定，見 docs/41 D5）                    │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 執行進度

```
run_8f2c… 執行中（可取消）
 ✓ 1 資料就緒檢查    行為 ✓／線下審核缺 10/19 → 比較期改到 10/18 為止（已標示）
 ✓ 2 確認口徑        開戶漏斗 contract=metrics@v?、窗口 30 天
 ● 3 比較期查詢      上週 vs 前一週（掃描估計 1.2 GB／上限 20 GB）
 ○ 4 分群下鑽        渠道、平台、App 版本
 ○ 5 證據驗證        重跑最大差異步驟（換期間）
```

### 6.3 配置卡與證據

```
┌ 配置卡（可修改後重跑）────────────────────────────┐
│ 模型：漏斗  步驟：<依核定口徑>  窗口：30 天  分組：渠道 │
│ 期間：10/12–10/18 vs 10/05–10/11   身分：你（行銷）  │
│ 契約：events@4、metrics@?、tools@?                   │
└────────────────────────────────────────────────────┘
結論（資料觀察）：第 3→4 步轉換率 −4.1 pt（證據 E1）
可能原因（假設）：Android 7.3.0 佔流失增幅 62%（證據 E2；相關，非因果）
限制：線下審核 10/19 未到；樣本小的分組已標示；K 匿名抑制 2 列
[看證據 E1] [看證據 E2] [存成看板草稿] [以此建立受眾草稿]
```
（數字為版面示意，非真實資料。）

### 6.4 可編輯草稿

```
看板草稿「開戶主管週報」（私人，未分享）
 卡片 1 開戶漏斗（依渠道）  卡片 2 各步轉換率趨勢  卡片 3 新戶滿 7 天未交易人數
 [編輯卡片] [調整期間] [分享…（你決定）] [設定每週寄送…（需數位經營審批）]
受眾草稿「開戶滿 7 天、有登入、未交易」（私人草稿，只有你看得到；不計算、不推送）
 規則（提案參數，待 D5 核定）：as-of D = 昨天（台北）
   開戶審核成功（kh_review_pass）日期 = D−7
   AND 開戶日～D 至少 1 次 登入
   AND 開戶日～D 0 次 成交
 資料完整性：D 的登入／交易來源 ✓（未齊則延後並標示）   預估：約 N 人（預估時間 10/21 09:10）
 排除：已在其他觸達名單 [編輯]       [送審…]（你按；需另一位數位經營核可）
```
（AI 不會替你送審；「送審」是使用者照原本分群流程操作，Windy 2026-10-28。私人草稿與被退回草稿怎麼區分，見 docs/39 §3.2。）

### 6.5 審批與失敗恢復

```
送審（你按的）：受眾「開戶滿 7 天、有登入、未交易」→ 審批單 ap_…（數位經營，不能是你自己）  狀態：待審
失敗：步驟 3 查詢逾時（TIMEOUT，可重試）  [重試此步] [縮短期間後重跑] [取消]
      已完成的步驟 1–2 不重跑；查詢步驟以步驟查詢 ID 重試（定義待確認 Q28）；
      寫入步驟沿用同一 operation_id，不會重複建立草稿；參數改了要你確認
撤權：你已沒有 funnel.use → 本 run 停止（AUTHZ_DENIED，不可重試）
```

### 6.6 技能庫與 Agent 設定（D5 管理方向，Windy 2026-10-21 提出；以下細節為提案，待 Windy 確認）

Windy 原話：「應該有個前端技能庫，去編輯每個 skills 或 agent 的口徑。」目的：業務口徑在前端看得到、改得到，不藏在 prompt 或程式裡。

**三層分工**（避免同一指標在每個 Agent 各複製一份而不一致）：

| 層 | 放什麼 | 誰改 | 怎麼改 |
|---|---|---|---|
| 語意層（中央） | 事件、指標、維度的定義（`events.yaml`、`metrics.yaml` 等；main 上語意層沒有漏斗定義） | 治理 | 既有 contract PR／元事件變更單。技能庫只顯示與連結，**不能在技能庫直接改** |
| 書籤（存起來的分析，含漏斗 `kind=funnel`） | 分析配置；以系統編號 `bm_…` 引用（後端建立時產生、不可改），不用名稱或自編 ID（Windy 2026-10-28） | 書籤擁有者 | 既有 `/v1/bookmarks`；同專案不可重名。書籤內容被改後 Skill 版本怎麼固定，待確認 Q34 |
| Skill | 場景參數：引用哪些書籤編號與語意層 key、日期與時區、篩選條件、歸戶方式、資料完整性條件、輸出、驗收範例、給模型的補充說明 | 技能維護者 | 技能庫：草案 → 乾跑比對 → 送發布 → 他人確認 |
| Agent | 引用哪些 Skill 的哪個**固定版本**，以及使用者發問時可以調整哪些參數、範圍多大 | 技能維護者 | 同上 |

可以編輯的是**參數與說明**；Skill 的步驟與可用工具屬工程範圍（SKILL.md，走程式審查，docs/39 §8）。

**畫面（文字 wireframe）：**

```
技能庫                                         [搜尋…] [狀態▼] [場景▼]
 名稱                    發布版   草案   口徑狀態              最近執行
 diagnose-conversion    1.2.0    1 份   ⚠ 2 項待確認          今天 09:12
 build-audience（S6）    —        1 份   ⚠ 4 項待確認（試行）   —
 data-readiness         1.0.0    —      ✓ 全部已核定           今天 09:10
Agent 設定
 開戶主管助理            1.0.0    引用 diagnose-conversion@1.2.0、build-dashboard@1.0.0
```

```
diagnose-conversion ▸ 草案（基於 1.2.0）                      [乾跑比對] [送發布] [捨棄]
 用途        比較兩期開戶漏斗，找出差異最大的步驟並下鑽
 引用        漏斗 書籤 bm_…（S1 漏斗書籤的系統編號，唯讀，[查看書籤]）  指標 new_accounts（metrics.yaml；版本待定）
 漏斗步驟    00→07（取自引用的書籤；⚠ 待客戶核定）
 日期與時區  比較期：上週 vs 前週；Asia/Taipei
 篩選條件    渠道 ∈ {App, Web}               ⓘ 提案
 歸戶方式    OneID 預設規則 oneid_rules@v1（唯讀；新客強升級／既有客 7 天回填／共用裝置）
 資料完整性  登入、開戶來源到齊才計算；未齊 → 截到最後完整日並標示
 樣本門檻    每組 ≥ 【200】                    ⚠ 待確認（契約參數）
 輸出        兩期結果、差異最大步驟、分群差異、假設（附驗證查詢）、限制
 驗收範例    A01、A02（docs/40）               [執行範例]
 補充說明    （給模型的文字；不能改上面欄位、不能放寬可見範圍或自動化界線）
```

```
乾跑比對（同一使用者權限、同一期間）
            發布版 1.2.0      草案
 步驟 03     1,204 人          1,187 人      差 −17（篩選條件改了渠道）
 證據        ev_…              ev_…
 ⚠ 草案含 2 項待確認參數：發布後結果會標「口徑待核定」，不能設為預設版本、不能用於排程與分享
```

```
發布：送出 → 有發布權的另一個人確認（不能自己核可自己）→ 生效為 1.3.0
版本歷史：1.3.0（10/22 王○○ 核可）← 1.2.0 ← 1.1.0   [看差異] [以此版建立新草案（回復）]
既有執行紀錄固定引用當時版本，不會被新版本改寫
```

## 7. 分期與 MVP

### 7.1 分期提案（已確認範圍 2026-10-21 起依此開發；各期出口條件不變）

> 用詞：本節的 P0–P4 是 AI 工作台自己的分期，不是專案的「第一階段」。docs/45 D-23：AI 工作台**不列第一階段驗收**（正式模型未啟用）。

| 期 | 內容 | 出口條件 |
|---|---|---|
| P0 前置（非 AI） | #33 fixture 與歸戶驗證；#32 合併或明確接受殘餘；口徑衝突（開戶定義、S5 14 天 vs 7 天）走 contract PR；語意層版本（`metrics.yaml` 目前沒有版本；粒度待確認 Q35）；D-14 契約 #74 | docs/40 §1 的 fixture 可重跑；契約 PR 合併 |
| P1 可信問數 | data-readiness、explore、diagnose（只讀）；run 紀錄、證據、狀態機；Gateway 正式化（身分、授權、預算、審計）；技能庫**唯讀檢視**（口徑與參數狀態看得到，提案） | docs/40 A 集通過 |
| P2 草稿 | 原生看板草稿／保存、受眾私人草稿（AI 建立；使用者可在 UI 送審，AI 不送審）；技能庫**編輯、乾跑比對、發布確認、版本回復**（提案） | B 集、D 集通過；冪等測試通過 |
| P3 定期 | 已授權的定期重跑、差異、通知、失敗恢復 | C 集通過 |
| P4 策略閉環 | 受眾推送、觸達、對照組、成效回收 | 另案，依既有推送審批 |

### 7.2 「單一端到端 MVP」vs「先完成通用平台」

| | 單一端到端 MVP（推薦） | 先完成通用平台 |
|---|---|---|
| 範圍 | 一條客戶場景鏈：S1 診斷 → 看板草稿 → S6 受眾草稿；只做這條需要的 6–8 個工具 | 25 條工具全部正式化、六個 skill 全上 |
| 好處 | 每個工具都有真場景與 oracle 驗收；早暴露口徑與歸戶問題；可對客戶示範 | 後續場景接得快 |
| 代價 | 架構要刻意保持通用（工具契約、狀態機一次做對），不能為 S1 寫死 | 長時間沒有可驗收結果；多數工具沒有真場景驅動，容易做成假完成 |
| 風險 | S1 口徑未核定會卡住數字驗收（以 fixture 口徑先驗，標示待客戶核定） | 與 Issue「不把登記當可用」的要求相反 |

### 7.3 最小可驗收垂直切片（Windy 2026-10-21 確認，docs/41 D1）

**In：** data-readiness（新鮮度、回填狀態、歸戶品質摘要）；diagnose-conversion（S1 漏斗：比較期、分群下鑽、證據重跑）；build-dashboard（開戶主管週報：3–4 張原生卡片草稿，私人）；build-audience（S6 開戶滿 7 天、有登入、未交易：規則草稿 + 預估，私人草稿；使用者可照原分群流程送審；定義見 docs/41 D5）；run 紀錄／證據／取消／重試；權限矩陣與撤權；A、B 集驗收。

**Out：** 推送、觸達、匯出、排程啟用（P3／P4）；`profile.*`、`users.list`；S2–S5、S7–S10 的診斷（P1 只做到「探索能回答可不可以分析、缺什麼」）；外部 MCP client 存取（Gateway 做好後可開，但不在 MVP 驗收）；第二引擎（P1 出口後比較）。

**為何把受眾換成 S6：** 客戶明寫的名單場景（簡報 p15：滿 7 天、未交易、有登入；Sheet S6：T+1 圈選、每人限發一次）。「每人限發一次」屬發送階段（MA，P4），DMP 受眾草稿只產生名單、不負責發送去重、對象是已開戶的已識別客戶、與 S1 同一條開戶旅程；開戶流程中斷者多為匿名（Sheet S1），做成受眾推不進 CDP。「開戶中斷者」受眾（docs/41 D1 替代方案 1）未採用。本階段 AI 對 S6 只產生受眾草稿、不送審、不推送；使用者可以在畫面上照原分群流程送審，由有核可權的另一人核可後才生效（Windy 2026-10-28）。S6 細部定義仍待 docs/41 D5 確認。

## 8. 工作拆分（2026-10-21 起，已確認範圍開始開發）

**仍待設定／待核定（實作時不得填成正式預設，缺設定時明確提示；測試只用標示清楚的合成案例）：** 模型供應商／端點／部署區域；模型投影的 K 值與金額區間邊界（未核定；現行安全下限照常適用，正式啟用條件見 docs/39 §2.4）；開戶定義；S1 詳細步驟；S5 回溯天數；S6 日期 cohort、登入來源、成交商品範圍；診斷樣本門檻；含待確認參數的正式版本是否沿用排程／分享／送審限制（Q42）；用量積分算法與環境總上限（Q41）。資料品質與歸戶依賴（#33、#41、#39、#74 仍 open；#32、#51、#52 已合併）未完成前，不宣稱真實資料端到端驗收完成。

**第一批（不受 #32／#51／#52 阻擋，可並行）：** W0（工具與 run 契約）、W11（技能庫契約與 RBAC 能力）→ W3（run 紀錄、狀態機、冪等）、W2（Gateway 正式化含 D3 投影）、W5（可替換的模型介面 + 測試替身 + SKILL.md 載入器）、W12（技能庫後端）→ W13（技能庫前端）。**第二批（需資料正確性的部分）：** W4、W6、W7、W9 的真實資料整合驗收，待 #33、#51、#32、#52 通過後執行；在那之前以標示清楚的合成 fixture 驗流程。

估算只給相對大小與假設，不給工期與費用（簡報 p30「Agent 問數」報價仍為「待討論」）。S ≈ 一個 < 400 行 PR；M ≈ 2–3 個 PR；L ≈ 需先拆 Story。

| # | 工作 | 依賴 | 大小 | 估算假設 |
|---|---|---|---|---|
| W0 | contract PR：工具契約 v1（docs/39 §1–4）、`analysis.configure` 種類修正、`metrics.yaml` 版本、`segments` draft 建立參數、Idempotency-Key | — | M | 只改 yaml／openapi；契約 PR 例外於 400 行 |
| W1 | contract PR：開戶口徑與 S5 回溯窗口決議落地 | 客戶核定（docs/41 D5） | S | 依客戶回覆；未核定則以「fixture 口徑」標示 |
| W2 | Tool Gateway 正式化：身分、`authorize()`、查詢前預算、審計、刪除 f-string 示範 | W0 | M | 沿用 `compile_kind`／`execute_kind`，不新寫查詢 |
| W3 | run／step journal、狀態機、`agent_operations`（operation_id 唯一）、租約世代圍籬、與產物同交易的寫入方法（PostgreSQL） | W0 | M | 用既有 `store_pg`；新增 `insert_item_if_absent`／交易版本方法，既有 `put_item` 是覆寫式 |
| W4 | data-readiness 後端端點（新鮮度依來源、回填、歸戶品質） | #33 | M | 需真實歸戶寫入的資料才能驗 |
| W5 | 模型驅動器 + SKILL.md 載入器 + 規則解析器改讀語意層同義詞 | W0、W2 | M | 規則解析器保留為離線／降級路徑 |
| W6 | skills：data-readiness、explore-data、diagnose-conversion（SKILL.md + 測試） | W4、W5 | M | 口徑只引用契約 ID |
| W7 | 看板／受眾草稿工具（冪等）+ skills build-dashboard、build-audience | W3、W0 | M | 用既有 dashboards／segments 服務 |
| W8 | 前端工作台（輸入、進度、配置卡、證據、草稿、失敗恢復） | W2、W3 | L | 需另開前端 Story；權限只讀 `/v1/iam/me` |
| W9 | 驗收：A／B 集 fixture 與 oracle、注入與撤權案例 | #33、W6、W7 | M | 數字比對用 `compiler/oracle.py` 擴充 |
| W10 | 第二引擎比較（MCP 接同一 Gateway） | P1 出口 | M | 只跑驗收，不進正式路徑 |
| W11 | contract PR：技能庫 API、Skill／Agent 版本資料模型、參數狀態、RBAC 能力（docs/39 §7.5） | W0；Windy 確認 §6.6 | S | 只改 openapi／rbac.yaml |
| W12 | 技能庫後端：版本儲存、驗證器、乾跑比對、發布確認、回復、審計；run 固定引用版本 | W11、W3、W5 | M | 用既有 `store_pg`；步驟與工具仍由 SKILL.md 決定 |
| W13 | 技能庫前端：列表、編輯、乾跑比對、版本差異、發布 | W12 | M | 前端另開 Story；D 集驗收 |

**完成度（2026-10-28 以 main@c8a87fa 與 GitHub REST 查證）：**

| # | 狀態 | 說明 |
|---|---|---|
| W0 | 大部分完成（#60 已合併） | 工具與 run 契約、`analysis.configure` 種類、`segments` draft 建立參數、`Idempotency-Key` 已進 openapi／tools.yaml；**`metrics.yaml` 版本沒有做**（版本粒度待確認 Q35） |
| W1 | 未開始 | 等客戶核定（Q1、Q2、Q20） |
| W2 | 進行中（#63 open） | main 上 `backend/mcp/server.py` 仍是骨架、`metric.query` 仍是 f-string SQL |
| W3 | 儲存層完成（#62 已合併） | `journal.py`；還沒接 API 與 Gateway；寫入路徑與服務層的關係待確認 Q27 |
| W4 | 未開始 | 依賴 #33（open）；回補欄位依賴 #74（open） |
| W5 | 部分完成（#64、#65 已合併） | 模型介面與 SKILL.md 載入器已合併；**「規則解析器改讀語意層同義詞」沒有做**（`configure.py` `SYNONYMS` 仍把「開戶」對到 `kh_apply`） |
| W6 | 部分 | `data-readiness`、`explore-data` 兩個 SKILL.md 已在 main（#64、#65）；`diagnose-conversion` 未開始 |
| W7 | 未開始 | W7 之前要先合併的契約：私人受眾草稿（Q26）、看板版本（Q30）、寫入路徑（Q27） |
| W8 | 未開始 | — |
| W9 | 未開始 | 依賴 #33 |
| W10 | 未開始 | P1 出口後 |
| W11 | 完成（#61 已合併） | 技能庫 API 與 `ai_skills` 模組；角色分配改在角色設定頁配置（1029-3），前端角色編輯器要補 `ai_skills`；`SkillVersion.refs` 要改成可放書籤系統編號（Windy 2026-10-28，需另開 contract PR） |
| W12 | 未開始 | — |
| W13 | 未開始 | — |

**另需開的 contract PR**（審查報告 2026-10-28 指出，清單見 docs/39 §9）：錯誤碼（自核 `FOUR_EYES_REQUIRED`、拒答、trial 限制）、私人受眾草稿、kind → 模組對照、身分參數丟棄清單加 `project`、run 版本與 Agent 輸入、看板版本、技能庫書籤引用、語意層版本、#74 後的 readiness 回補欄位。依 CLAUDE.md，契約變更各自開 contract PR，不夾在功能 PR。

## 9. 架構決策表

| ID | 題目 | 選項 | 推薦 | 理由 | 限制 | 未決項 | 決策人 |
|---|---|---|---|---|---|---|---|
| ADR-1 | 第一階段執行引擎 | A 自有／B Claude Agent SDK／C OpenAI Agents SDK 或 Agents API | **已定：A**（Windy 2026-10-21 確認）；B 或 C-SDK 作第二引擎比較 | §4.2 | 迴圈與轉接自維護 | 第二引擎是 B 或 C 取決於 ADR-2 | Windy（docs/41 D2，執行架構已確認） |
| ADR-2 | 模型供應商與推論位置 | 境內／專案內託管端點、企業 LLM Gateway、境外 API | 先確認永豐資安可接受的端點，再選供應商 | 這是資料出境問題 | 本輪未查區域與條款 | **待定**：供應商、型號、端點位置、部署區域、資料保留政策；永豐資安意見 | Windy＋永豐 |
| ADR-3 | 模型可見範圍 | M0／M0+M1／含 M2 | **已定：M0+M1**（Windy 2026-10-21 確認） | 最小必要；畫面數字不經模型 | M1 需實作投影 | 永豐是否允許 M1 出境（隨 D2 端點）；模型投影的 K 值與金額區間邊界（未核定；現行安全下限照常適用，正式啟用條件見 docs/39 §2.4） | Windy（D3，已確認） |
| ADR-4 | 自動化授權 | 嚴／中／寬 | **已定：中**（Windy 2026-10-21 確認，§5.3） | 私人草稿直接完成、生效靠人 | 人工操作仍走既有 RBAC 與審批 | 行銷是否給看板建立權（權限治理，Q7）。受眾送審已定：AI 不送審、使用者可在 UI 照原分群流程送審（Windy 2026-10-28）；私人草稿與被退回草稿的區分方式待定（Q26） | Windy（D4，已確認） |
| ADR-5 | MVP 切片 | 原案／修正案／平台先行 | **已定：修正案（§7.3）**，Windy 2026-10-21 確認 | 場景驅動、可 oracle 驗收 | S1 口徑待核定 | 客戶核定時間；S6 細部定義（D5） | Windy（D1，已確認） |
| ADR-6 | 第一批口徑 | 等客戶核定／先以提案口徑 | 提案口徑 + 標示待核定（docs/41 D5） | 不卡工程，但不宣稱客戶確認 | 數字可能需重算 | 客戶回覆 | Windy＋永豐數金 |
| ADR-7 | 既有 skills 遷移 | 保留 .md／遷 SKILL.md／雙軌 | 遷 SKILL.md，單一來源（docs/39 §9） | 兩引擎都能載入同一格式 | 只用標準 frontmatter 欄位 | — | 工程（契約 PR 審查） |
| ADR-8 | 數字覆核 | 模型覆核／確定性重跑＋oracle | 確定性 | Issue 要求；可重現 | 需 fixture | — | 工程 |
| ADR-9 | 排程身分 | 服務身分／擁有者現在角色 | 擁有者現在角色（沿用 `app.py:74-89`） | 已有實作與測試 | `svc_insight` 只留給無發起人的系統檢查 | `agents.yaml` 身分欄位要改 | 工程（契約 PR） |
| ADR-10 | 口徑管理 | 寫在 prompt／程式；前端技能庫 | **方向已定：前端技能庫**（Windy 2026-10-21 提出）；三層分工與發布流程為提案（§6.6、docs/39 §7.5） | 業務口徑可檢視、可編輯、可追溯；共用定義集中在語意層 | 可編輯口徑不等於可改授權或資料保護 | 編輯／發布權在角色設定頁配置、只有已發布版本可選用（Windy 2026-10-29，docs/41 1029-2、1029-3）；含待確認參數可以正式發布（1029-9）；限制是否沿用（Q42）；畫面細節 | Windy（D5） |
| ADR-11 | 第一階段部署 | 3 台 VM＋Docker Compose／Kubernetes | **已定：Docker Compose**（docs/45 D-01，2026-10-22；Windy 2026-10-28 確認本 RFC 照此改） | 運維熟 Docker；第一階段只啟用 web、core、batch、ingest（D-05） | agent 映像第一階段不啟用 | run 控制器與 worker 放哪個映像／容器、模型端點的 egress 與金鑰 secret_ref 路徑（Q25） | 研發＋運維 |
| ADR-12 | 錯誤碼 | 規格自訂／照 `errors.yaml` 目錄 | **已定：照目錄**（Windy 2026-10-28） | 前端與 API 只認一套代碼 | 目錄沒有的代碼要另開 contract PR 新增 | 模型拒答（Q22、Q31）、trial 限制代碼（Q32） | Windy（原則）；研發（契約 PR） |
| ADR-13 | 書籤與漏斗引用 | 名稱／自編 ID／系統編號 | **已定：系統編號 `bm_…`**（Windy 2026-10-28） | 編號由後端產生、不可改，不會因改名或重名而指錯 | 書籤內容可被修改 | 書籤內容被改後 Skill／run 怎麼固定（Q34） | Windy |
