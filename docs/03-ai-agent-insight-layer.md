# AI Agent 洞察擴展層設計

> **第二階段**：本案第一階段不做 AI 功能（docs/22 §1）；本文與 docs/05、`backend/mcp/`、`backend/insight_agent/` 保留為第二階段設計，語意層與契約已預留接口。
>
> 讓 AI agent 能安全、可擴展地「讀懂資料、提出洞察、採取受控行動」，而不是只在 UI 上加一個聊天框。

## 1. 原則

1. **語意優先**：agent 不寫原始 SQL 對表，而是對「語意層」提問（metric、dimension、event、segment 皆有定義與說明）。這確保口徑一致、權限可控、成本可估。
2. **工具即契約**：每個能力都是有 schema、權限、預算、脫敏策略的工具（MCP）。新能力 = 新工具，不改 agent 程式。
3. **洞察是資料**：agent 的產出寫入 Insight Store，帶證據（可重跑的語意查詢）、信心度、影響範圍、回饋。人可訂閱、評分、採納；agent 下次執行會參考。
4. **人在迴路**：agent 可建議分群／觸發規則／標籤，但「建立並生效」需審批；所有 agent 行為記錄為事件並可審計。
5. **模型無關**：透過 LLM Gateway 呼叫模型；評測集（golden questions）保證換模型不退化。

## 2. 組成

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Insight Agent Runtime                                                      │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│   │ Scheduled     │  │ On-demand    │  │ Event-driven │  │ Reviewer     │  │
│   │ 異常偵測/日報 │  │ 問答/探索    │  │ 觸發後診斷   │  │ 證據覆核     │  │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│          └─────────────────┴───── Planner / Executor / Verifier / Narrator │
│                                   （skills：Markdown 指令 + 範例）          │
├────────────────────────────────────────────────────────────────────────────┤
│ Agent Tool Gateway (MCP)                                                   │
│   tools.yaml → handler；Policy（RBAC/行級/列級/脫敏）；Budget；Audit；Cache │
├────────────────────────────────────────────────────────────────────────────┤
│ Semantic Layer │ Analytics API │ Segment API │ Profile API │ Insight Store │
├────────────────────────────────────────────────────────────────────────────┤
│ Knowledge Pack（事件字典、指標說明、業務詞彙、SOP、歷史洞察；pgvector）     │
└────────────────────────────────────────────────────────────────────────────┘
```

## 3. 語意層（agent 的「資料地圖」）

`backend/semantic/*.yaml` 定義：

- **events**：名稱、中文、說明、主體、必填屬性、來源、範例問題。
- **dimensions**：欄位、型別、可用值（或取值查詢）、敏感等級、說明。
- **metrics**：`uniq_users(event=order_filled)`、`sum(amount where event=order_filled)`、比率型（分子／分母）、留存型、漏斗型；每個指標有 `description`、`owner`、`grain`、`caveats`（agent 會把 caveats 放進敘事）。
- **segments**：命名分群的語意定義。

agent 工具 `semantic.search("想看定期定額的新客")` 以向量 + 關鍵字檢索回傳最相關的 events/metrics/dimensions，附說明，減少幻覺。

## 4. 工具目錄（MCP）

完整定義：`backend/mcp/tools.yaml`。摘要：

| 工具 | 功能 | 權限 / 限制 |
|---|---|---|
| `semantic.search` | 以自然語言找事件／指標／維度 | 任何已登入身份 |
| `semantic.describe` | 取得某 metric/event 的定義、口徑、caveats | 同上 |
| `metric.query` | 語意查詢（metric × filters × group_by × time）→ 表格 + 圖表規格 | 掃描預算；行級過濾 |
| `funnel.run` / `retention.run` / `path.run` / `interval.run` / `distribution.run` / `attribution.run` / `ltv.run` | 分析模型 | 同上 |
| `anomaly.scan` | 對指標序列做季節性/門檻偵測 | 同上 |
| `segment.preview` / `segment.propose` | 預估人數與畫像；提議分群（草稿） | propose 需審批才生效 |
| `profile.get` / `profile.timeline` | 單人畫像與行為序列 | 脫敏；僅所屬客戶；高敏需理由並審計 |
| `insight.search` / `insight.create` / `insight.link` | 讀寫洞察 | create 標記 `created_by=agent` |
| `trigger.propose` | 提議即時規則 | 需審批 |
| `knowledge.search` | 查事件字典／SOP／詞彙 | — |
| `report.compose` | 把多個洞察組成日報／週報 | — |

每個工具回傳 `{ data, chart_spec?, sql?, lineage, cost, warnings[] }`，agent 必須把 `warnings`（如 streaming 缺歸因、樣本小）帶進結論。

## 5. Agent 類型與 skill

| Agent | 觸發 | Skill 摘要 | 產出 |
|---|---|---|---|
| `anomaly-watch` | 每 15 分鐘 | 掃描核心指標（事件量、下單失敗率、成交金額、登入數）；偏離 → 下鑽維度（平台、渠道、分公司）找主因 | `insight(kind=anomaly)` + 通知 |
| `funnel-doctor` | 每日 / 問答 | 針對開戶、入金、首筆交易漏斗；找流失最大步驟、對比分群、提出假設與驗證查詢 | `insight(kind=funnel)` |
| `churn-radar` | 每日 | 以生命週期標籤與行為衰減找高風險客群；提議分群與觸發 | `insight(kind=risk)` + `segment.propose` |
| `campaign-review` | 活動結束 | 曝光→點擊→轉化 歸因與增量 | `insight(kind=campaign)` |
| `ask` | 使用者提問 | 計畫 → 工具 → 驗證（二次查詢交叉）→ 回答，附證據與圖 | 對話 + 可存為洞察 |
| `reviewer` | 任何洞察建立後 | 重跑證據查詢、檢查口徑與樣本量、給信心度 | 更新 `confidence` |

Skill 以 Markdown 撰寫（`backend/insight_agent/skills/*.md`），含：目標、可用工具、步驟、驗證規則、輸出格式、禁止事項（例：不得輸出可識別個資；樣本 < 200 人須標示）。

## 6. 執行迴圈

```
question / schedule / event
   │
   ▼
Planner  —— 讀 skill + knowledge.search + semantic.search → 計畫（工具序列、預算）
   │
   ▼
Executor —— 逐一呼叫工具（Gateway 套權限/預算/脫敏）；失敗則重規劃（最多 N 次）
   │
   ▼
Verifier —— 交叉驗證（換維度或換時間窗重跑）、樣本量、口徑 caveats、數字一致性
   │
   ▼
Narrator —— 產生洞察：標題、摘要、證據（可重跑）、圖表規格、建議行動、信心度
   │
   ▼
Insight Store ─► 訂閱通知 / UI / 回饋 ─► 下次 Planner 的 few-shot 與排序依據
```

## 7. 洞察物件

```json
{
  "id": "ins_01J…",
  "kind": "funnel | anomaly | risk | campaign | opportunity | answer",
  "title": "開戶→首次入金 轉化在 iOS 23.4 版本下降 6.1 pt",
  "summary": "…",
  "hypothesis": "新版 KYC 頁面在 iOS 上的相機權限流程造成中斷",
  "evidence": [
    { "tool": "funnel.run", "args": { "...": "..." }, "digest": { "step3_rate": 0.41, "baseline": 0.47 }, "sql_hash": "…" }
  ],
  "chart_spec": { "type": "funnel", "series": [ "..." ] },
  "scope": { "segment": "new_customers_30d", "dimension": { "platform": "ios", "app_version": "23.4" } },
  "confidence": 0.78,
  "severity": "high",
  "actions": [ { "type": "segment.propose", "status": "pending_approval" } ],
  "created_by": { "agent": "funnel-doctor", "run_id": "run_…", "on_behalf_of": "user_…" },
  "feedback": [ { "user": "…", "vote": "useful", "note": "已交給 App 團隊" } ],
  "status": "open | acknowledged | resolved | dismissed"
}
```

## 8. 安全與治理

- **身份**：agent 以委託身份執行；排程 agent 使用具最小權限的服務身份（只能讀聚合、不能細查）。
- **脫敏**：Gateway 在工具回傳前套列級遮罩；`profile.*` 回傳不含 L4 欄位；細查需理由並審計。
- **預算**：每工具呼叫掃描上限（預設 20 GB）、每 run 上限（100 GB、50k tokens）、每 agent 每日上限。
- **審計**：每次工具呼叫寫 `agent.tool_called` 事件（含 SQL hash、bytes、延遲）；洞察與行動提議可追溯。
- **輸出守門**：敘事層禁止輸出個資樣式（身分證、手機、Email 正規式攔截）；數字必須來自工具回傳（Verifier 比對）。
- **評測**：`backend/insight_agent/evals/golden.yaml` 收錄標準問題與期望數字／結論；CI 跑通過率；換模型或改 skill 需過閾值。

## 9. 擴展方式

| 想要… | 做法 |
|---|---|
| 新增一個指標讓 agent 能回答 | 在 `semantic/metrics.yaml` 加一條（含 description、caveats）；不需改程式 |
| 新增一種分析（例：熱圖） | 實作 Analytics API plugin + 在 `mcp/tools.yaml` 登記工具 |
| 新增一個 agent | 寫一個 skill Markdown + 在 `agents.yaml` 設排程／觸發與允許的工具集 |
| 接新資料源 | 在事件字典登記事件；Collector 自動接受；語意層自動可見 |
| 換 LLM | 改 LLM Gateway 路由；跑 golden evals |
| 讓外部系統（MA、內部數據系統）用 agent | 呼叫 `POST /v1/agents/ask` 或直接以 MCP client 連 Gateway（同一權限模型） |

## 10. 原型對應

`/prototype` 的「AI 洞察」頁示範：洞察串流（agent 產出）、問答（顯示計畫與工具呼叫）、agent 註冊表、工具目錄與預算，對應本文件 §4–§7。
