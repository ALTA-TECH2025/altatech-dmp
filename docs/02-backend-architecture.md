# AltaTech DMP 後端架構

> 版本 v0.1（2026-10-14）。本文件描述 AltaTech DMP（數位軌跡／客戶數據中台）的後端架構。
> 調研背景與 GA360／交易事件接入細節見 `01-research-volcano-sensors-and-dtp-design.md`；AI agent 洞察層見 `03-ai-agent-insight-layer.md`。

## 1. 設計目標

| 目標 | 說明 |
|---|---|
| 私有化 | 全部元件可在客戶機房 K8s 上離線部署，資料不出境 |
| 雙來源 | 同一事件模型承接 GA360 埋點（行為）與證券交易事件（業務） |
| OneID | 客戶／帳戶／裝置／Cookie 打通，多主體 |
| 分析對標 | 事件、漏斗、留存、路徑、間隔、分布、歸因、LTV、Session、用戶細查 |
| **AI agent 原生** | 所有分析能力同時以「語意層 + 工具（MCP）」形式暴露給 AI agent；agent 產生的洞察是一級資料物件，可被訂閱、回饋、再利用 |
| 可替換 | 每一層都可換成火山引擎 VeCDP／DataFinder 或神策對應產品 |

## 2. 架構分層

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ 體驗層   DMP Web（原型 /prototype）│ AI 洞察助理（Chat）│ 營運觸發 │ 外部 MA / 客服 / 營業員 App │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Agent 層 Insight Agent Runtime（排程 / 問答 / 事件驅動 agent）                             │
│          Agent Tool Gateway（MCP Server：query_metric / run_funnel / … 權限、預算、脫敏）   │
│          Insight Store（洞察、證據、回饋、向量索引）  Knowledge Pack（字典、詞彙、歷史洞察）  │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 語意層   Semantic Layer（metrics.yaml / events.yaml / dimensions.yaml → SQL 編譯器）        │
│          Analytics Query API │ Segment & Tag API │ Profile / OneID API │ Subscribe API        │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 計算層   Flink（標準化、事件化、OneID、Session、即時規則、即時標籤）                         │
│          Spark / Trino（日批、標籤、ID 校正、嵌入特徵）  Airflow（排程）                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 儲存層   Kafka │ ClickHouse（熱）│ Iceberg on GCS（機房：SeaweedFS）（冷）│ PostgreSQL + pgvector │ Valkey      │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 接入層   GA360 Importer │ GTM Server-side 雙寫 │ Collector API（神策相容）│ Debezium CDC │ 匯入 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

與傳統 CDP 的差別在「語意層」與「Agent 層」：UI 與 agent 走**同一條**語意層 API，因此 agent 看到的指標定義、權限、脫敏規則與人看到的完全一致。

## 3. 元件清單

| 元件 | 技術 | 職責 | 擴展點 |
|---|---|---|---|
| Collector API | Go / FastAPI | 神策相容 `/sa`、原生 `/v1/events`、GA4 MP `/ga4`；校驗、脫敏、寫 Kafka | 事件字典、禁止欄位規則 |
| GA360 Importer | Airflow + Spark（BigQuery Storage Read API） | 日批／Fresh 匯入、對帳、覆蓋 | 映射表 `backend/semantic/ga4_mapping.yaml` |
| Trade Eventizer | Flink | CDC 列變更 → 業務事件（狀態機） | 狀態機 YAML |
| OneID Service | Flink + Redis + ClickHouse `id_map` + gRPC/REST | 即時解析與合併、日批校正、拆分、DSAR | ID 優先級表、合併限制規則 |
| Semantic Layer | Python（`backend/semantic`） | 指標／維度／事件語意定義；編譯為 ClickHouse SQL；成本估算 | 新增 metric / dimension 只改 YAML |
| Analytics Query API | FastAPI | 事件、漏斗、留存、路徑、間隔、分布、歸因、LTV、Session、細查 | 分析模型以 plugin 註冊 |
| Segment & Tag Service | FastAPI + Spark + ClickHouse Bitmap | 規則／SQL／即時／模型標籤；分群交併差；快照 | 標籤計算器 plugin |
| Realtime Rule Engine | Flink CEP | 規則 DSL → 觸發事件 → Kafka `dmp.triggers` | 規則 DSL |
| **Agent Tool Gateway** | Python（MCP Server, `backend/mcp`） | 把語意層／分析／分群／畫像包成工具；RBAC、列級脫敏、查詢預算、審計 | `tools.yaml` 註冊新工具 |
| **Insight Agent Runtime** | Python（`backend/insight_agent`） | 計畫 → 工具 → 驗證 → 敘事；排程／問答／事件驅動 | Agent skill（Markdown）、LLM provider |
| **Insight Store** | PostgreSQL + pgvector | 洞察物件、證據查詢、圖表規格、回饋、訂閱 | 洞察類型 schema |
| Knowledge Pack | PostgreSQL + pgvector | 事件字典、業務詞彙、指標說明、歷史洞察、SOP | RAG 來源 |
| Governance | PostgreSQL | 事件字典、血緣（OpenLineage）、審計、權限 | — |

## 4. 資料模型摘要

- `events`（ClickHouse）：`(project, event_date, event, one_id, time)` 排序；高頻屬性物化欄位；其餘 JSON。
- `users` / `items`：ReplacingMergeTree；`id_map`：`(id_type, id_hash) → one_id, version`。
- `user_tags`：`(tag_id, tag_value, version) → groupBitmap(one_id_uint)`。
- `insights`（PostgreSQL）：`id, kind, title, summary, hypothesis, evidence[] (semantic query + result digest), chart_spec, confidence, severity, scope (segment/dimension), created_by (agent|user), status, feedback[], embedding`。
- `agent_runs`：`id, agent, trigger (schedule|question|event), plan, tool_calls[], cost, outcome, insight_ids[]`。

完整 DDL：`backend/clickhouse/schema.sql`。

## 5. API 一覽（OpenAPI：`backend/openapi.yaml`）

| 群組 | 端點 | 說明 |
|---|---|---|
| Ingest | `POST /v1/events`、`POST /sa`、`POST /ga4` | 事件寫入 |
| Semantic | `GET /v1/semantic/metrics`、`GET /v1/semantic/dimensions`、`GET /v1/semantic/events`、`POST /v1/semantic/compile` | 語意層目錄與 SQL 編譯（dry-run、成本） |
| Analytics | `POST /v1/analytics/{events,funnel,retention,path,interval,distribution,attribution,ltv,session}` | 分析模型 |
| Profile | `GET /v1/profiles/{one_id}`、`GET /v1/profiles/{one_id}/timeline` | 畫像、用戶細查 |
| OneID | `GET /v1/oneid/resolve`、`POST /v1/oneid/bind`、`POST /v1/oneid/erase` | ID 服務 |
| Segments / Tags | `GET/POST /v1/segments`、`POST /v1/segments/{id}/preview`、`GET/POST /v1/tags` | 分群與標籤 |
| Triggers | `GET/POST /v1/triggers`、`GET /v1/triggers/{id}/events` | 即時規則 |
| **Insights** | `GET/POST /v1/insights`、`POST /v1/insights/{id}/feedback`、`POST /v1/insights/subscriptions` | 洞察物件 |
| **Agents** | `GET /v1/agents`、`POST /v1/agents/{id}/run`、`POST /v1/agents/ask`、`GET /v1/agents/runs/{run_id}` | agent 執行 |
| Governance | `GET /v1/dictionary/events`、`GET /v1/lineage/{entity}`、`GET /v1/audit` | 治理 |

## 6. 查詢路徑（人與 agent 共用）

```
UI 分析頁 ───┐
             ├─► Analytics Query API ─► Semantic Compiler ─► Policy Engine（RBAC／行級／列級／預算）─► ClickHouse
Agent 工具 ──┘        │                                                               │
                      └── dry-run: 估算掃描量、回傳 SQL 與欄位血緣 ◄──────────────────┘
```

- 同一個 `compile()`：輸入語意查詢（metric、filters、group_by、time），輸出 SQL、估算 bytes、血緣。
- Policy Engine 以呼叫者身份（人或 agent 的委託身份）套規則；agent 永遠以「代表某位使用者」執行，沒有超級權限。
- 預算：每次查詢的掃描上限、每個 agent run 的總掃描與 LLM token 上限，超出即中止並記錄。

## 7. 即時路徑

```
Kafka dmp.events ─► Flink CEP（規則）─► dmp.triggers ─► 訂閱者（MA / 客服 / 營業員 App / Insight Agent）
                 └► Flink 即時標籤 ─► Redis + ClickHouse user_tags(rt)
                 └► 異常偵測 agent（分鐘級聚合 → 偏離門檻 → 建立洞察 → 通知）
```

## 8. 非功能

| 項目 | 目標 |
|---|---|
| 行為事件可查延遲 | P95 < 60 秒 |
| 交易事件可查延遲 | P95 < 10 秒 |
| 即時觸發 | P95 < 5 秒 |
| 30 天漏斗查詢 | P95 < 10 秒 |
| Agent 問答（含 3–5 次工具呼叫） | P95 < 40 秒 |
| 熱資料 | 13 個月；冷資料依法定年限 |

## 9. 部署

- Helm umbrella chart；Operators：Strimzi（Kafka）、Flink K8s Operator、ClickHouse Operator、CloudNativePG。
- LLM：預設走企業內 LLM Gateway（可接 Claude／本地模型）；agent 不直接持有外部金鑰。
- 本機開發：`backend/docker-compose.yml`（Kafka、ClickHouse、PostgreSQL+pgvector、Valkey、SeaweedFS；授權見 07 文件）。

## 10. 與商用產品的替換關係

| 本平台模組 | 火山引擎 | 神策 |
|---|---|---|
| Collector / Importer | DataFinder SDK + 數據導入 | 神策 SDK + FormatImporter |
| OneID Service | VeCDP ID-Mapping / Baseid | IDM 3.0 |
| Analytics Query API | DataFinder OpenAPI | 神策分析 查詢 API |
| Segment & Tag | VeCDP 標籤／分群 | 用戶畫像 |
| Realtime Rule Engine | VeCDP 實時規則引擎 | 智能運營 |
| Semantic Layer + Agent Tool Gateway + Insight Agent | （無對應；本平台差異化） | （無對應；本平台差異化） |
