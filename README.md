# AltaTech DMP

可私有化部署的數位軌跡／客戶數據中台（Digital-trace & Customer Data Platform）：
承接 **GA360 埋點事件**與**證券交易事件**，打通 OneID，提供事件／漏斗／留存／路徑等分析、標籤分群、即時觸發，
並以**語意層 + 工具閘道（MCP）+ Insight Agent** 讓 AI agent 能安全、可擴展地洞察資料。

對標：火山引擎 VeDI（DataFinder、VeCDP）、神策（神策分析、SDP、用戶畫像、智能運營）。

## 目錄

```
docs/
  01-research-volcano-sensors-and-dtp-design.md   調研（火山引擎／神策／GA360／法遵）與中台總體設計
  02-backend-architecture.md                       後端架構（分層、元件、API、查詢路徑、部署）
  03-ai-agent-insight-layer.md                     AI agent 洞察擴展層（語意層、工具目錄、agent、洞察物件、治理）
prototype/                                          可直接開啟的前端原型（純靜態，無建置步驟）
  index.html  css/app.css  js/app.js  js/data.js  vercel.json
backend/                                            後端骨架（可讀、可跑的最小實作與設定）
  openapi.yaml                                     人與 agent 共用的 API
  semantic/{events,metrics,dimensions,ga4_mapping}.yaml   語意層定義（agent 的資料地圖）
  mcp/tools.yaml  mcp/server.py                    Agent Tool Gateway（工具目錄 + 權限/預算/脫敏/審計骨架）
  insight_agent/agents.yaml  skills/*.md  evals/golden.yaml  runtime.py   agent 註冊、skill、評測、執行迴圈
  clickhouse/schema.sql  clickhouse/insight_store.sql      熱層 DDL、Insight Store（PostgreSQL + pgvector）
  docker-compose.yml                               本機開發環境（Kafka、ClickHouse、PostgreSQL、Redis、MinIO）
```

## 原型

```bash
cd prototype && python3 -m http.server 8080   # 或直接部署到 Vercel（root = prototype）
```

頁面：總覽、事件分析、漏斗、留存、用戶細查／OneID、標籤與分群、即時觸發、**AI 洞察**（洞察串流／問答／Agents／工具目錄）、資料治理、平台設定。
四種示範身分（分析師、營運主管、營業員、管理員）展示權限差異：SQL 檢視、審批 agent 提議、僅能細查所屬客戶、細查需理由並審計。

## 後端骨架

```bash
pip install pyyaml
python3 backend/mcp/server.py              # 工具閘道：semantic.search 範例呼叫 + 審計事件
python3 backend/insight_agent/runtime.py   # agent 執行迴圈：plan → execute → verify → narrate
docker compose -f backend/docker-compose.yml up -d   # 本機基礎設施
```

## AI agent 擴展能力（設計重點）

1. **語意層**：指標／維度／事件以 YAML 定義（含口徑、caveats）；UI 與 agent 走同一個查詢編譯器，口徑與權限一致。
2. **工具即契約**：每個能力是有 schema、權限範圍、預算、脫敏策略、審計的 MCP 工具；新增能力 = 新增工具，不改 agent 程式。
3. **洞察是一級資料物件**：帶可重跑的證據、信心度、影響範圍、行動提議、回饋；可訂閱、再利用、餵回 agent。
4. **人在迴路**：agent 可提議分群／規則，但生效需審批；敏感工具需理由；所有工具呼叫寫審計事件。
5. **模型無關**：透過 LLM Gateway；golden evals 保證換模型或改 skill 不退化。

詳見 `docs/03-ai-agent-insight-layer.md`。
