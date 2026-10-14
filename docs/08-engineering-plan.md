# 工程規劃：語言與框架、分工、版本與時程

> 版本 v0.2（2026-10-14）。

## 1. 語言與框架

| 元件 | 語言 | 框架／函式庫 | 理由 |
|---|---|---|---|
| Web 前端 | TypeScript 5 | React 18 + Vite、TanStack Query/Table、Zustand、**Apache ECharts**（桑基、漏斗、折線）、React Flow（可視化建模畫布，MIT） | 生態成熟、授權寬鬆；原型 `prototype/` 的頁面與互動直接移植 |
| Collector（高吞吐入口） | **Go 1.23** | net/http + franz-go（Kafka，BSD-3）、sonic/jsoniter | 每節點 5 萬 req/s、低記憶體、單一二進位 |
| API / 語意層 / 分群標籤服務 / 治理 | **Python 3.12** | FastAPI、Pydantic v2、SQLGlot（SQL 生成與方言）、clickhouse-connect、asyncpg | 與 agent 同語言、資料團隊熟悉、SQL 生成邏輯易測 |
| Agent Runtime / Tool Gateway | Python 3.12 | 官方 `mcp` SDK、LangGraph（選配）、httpx、pgvector | MCP 生態以 Python/TS 最完整 |
| 串流作業（標準化、事件化、OneID、Session、CEP、即時標籤） | **Flink SQL ＋ Flink CDC 3 YAML pipeline**（主）；Java 17 僅 OneID 合併一個 job | Flink 1.19、Flink CDC、Debezium、RoaringBitmap | SQL-first：LLM 寫 SQL 正確率高、弱點面小；見 docs/11 |
| 批次作業（標籤、ID 校正、觸點表、匯入） | **ClickHouse SQL ＋ dbt-core**（主）；PySpark 僅 BigQuery 匯入與 ML | dbt-core、Spark 3.5 | 減少 JVM 面；dbt 提供版本化、測試與血緣 |
| 基礎設施 | HCL / YAML | OpenTofu、Helm、Kustomize、Ansible（機房） | — |
| 測試 | — | Vitest/Playwright（前端）、pytest（Python）、JUnit（Java）、k6（負載）、Great Expectations（資料） | — |

Monorepo 結構：
```
altatech-dmp/
  apps/web  apps/api  apps/semantic  apps/segment  apps/agent  apps/gateway  apps/collector(go)
  jobs/flink(java)  jobs/spark(py)  jobs/dbt
  packages/schema(事件字典/協議, 產生 TS+Py+Java 型別)  packages/ui  packages/sdk-ts  packages/sdk-py
  deploy/helm  deploy/tofu  deploy/images
  docs/  prototype/
```
工具：pnpm workspaces + uv（Python）+ Gradle（Java）；Conventional Commits；trunk-based + release branches；CI（GitHub Actions 或 Cloud Build）跑 lint、單元、整合（Testcontainers：Kafka/ClickHouse/PG）、SBOM 與授權檢查。

## 2. 團隊分工（建議 9 人 + PM/PO）

| 工作流 | 人數 | 負責 |
|---|---|---|
| WS1 平台與部署 | 1.5 | GKE/機房 K8s、Helm/OpenTofu、Operators、觀測、安全基線、授權審查、OS 驗證 |
| WS2 接入與串流 | 2 | Collector（Go）、GA360 Importer、Debezium/Flink CDC、事件化狀態機、OneID、Session、即時規則 |
| WS3 資料與效能 | 1.5 | ClickHouse 表設計、MV、Bitmap 標籤引擎、dbt 模型、效能測試（百億級） |
| WS4 後端 API 與語意層 | 1.5 | 語意層、查詢編譯器、六個 P0 模組 API、分群標籤服務、治理 API、權限 |
| WS5 前端 | 1.5 | 六個 P0 頁面、可視化建模畫布、AI 洞察介面、看板 |
| WS6 AI agent | 1 | Tool Gateway（MCP）、Agent Runtime、skills、Knowledge Pack、evals、法遵守門 |
| QA／DataOps（橫向） | 0.5–1 | 測試資料產生器（百億級合成事件）、資料品質、驗收 |

介面契約先行：`packages/schema`（事件協議、語意查詢 JSON、工具 schema）在 Sprint 1 凍結 v1，讓各工作流平行開發。

## 3. 版本規劃

| 版本 | 目標 | 內容 |
|---|---|---|
| **v0.1**（已完成） | 原型與設計 | 文件、靜態原型、後端骨架 |
| **v0.3 Alpha** | 資料進得來、看得到 | Collector、GA360 日批匯入、MSSQL/PG CDC、事件字典、OneID v1（即時）、ClickHouse 基表 + MV、**事件分析**、**用戶列表**（基本）、SSO/RBAC、GCP 一鍵部署 |
| **v0.6 Beta** | P0 分析模組齊 | **漏斗**、**行為路徑**、**歸因**、**標籤能力**（規則／統計／SQL／生命週期 + Bitmap 引擎）、分群、可視化建模 v1（MSSQL/PG/BigQuery）、審計、資料品質監控、效能基準 10 億 |
| **v1.0 GA** | 可上線 | 神策歷史事件一次性匯入、即時規則引擎、AI agent v1（指標搜尋、選擇題式分析、可校驗回答、異常下鑽、MCP 對外、法遵守門）、OneID 日批校正與拆分、DSAR、效能基準 **100 億**、RHEL/Rocky 驗證、授權 SBOM、營運手冊 |
| **v1.5** | 智能與閉環 | 看板解讀與診斷報告、行業知識庫、策略輸出與復盤 agent、即時標籤／分群、留存／間隔／分布／LTV（P1）、可視化建模 v2（雙向映射、Schema drift） |
| **v2.0** | 規模與執行 | 審批後自動化執行、多專案多租戶、DR 站、熱圖／A/B 對接、百億級日增量優化（分層壓實、冷熱自動遷移） |

## 4. 時程（以 2 週一個 Sprint，約 10 個月到 GA）

```
月      1        2        3        4        5        6        7        8        9        10
Sprint  S1  S2   S3  S4   S5  S6   S7  S8   S9  S10  S11 S12  S13 S14  S15 S16  S17 S18  S19 S20
WS1     ████ 環境/CI/Helm ████ GCP一鍵 ──── 觀測/安全 ──── 機房驗證 ──── RHEL/Rocky ──── 授權SBOM ─ DR
WS2     ████ Collector/GA匯入 ████ CDC/事件化 ████ OneID即時 ──── Session/即時規則 ──── 神策歷史匯入 ──── 校正/拆分
WS3     ████ 基表/MV ████ 用戶列表索引 ████ Bitmap標籤引擎 ████ 觸點表/路徑抽樣 ──── 10億基準 ──── 100億基準 ─ 調優
WS4     ████ 語意層/編譯器 ████ 事件分析API ████ 漏斗/路徑 ████ 歸因/標籤API ──── 分群/權限 ──── 治理/DSAR ─ 強化
WS5     ████ 設計系統/外框 ████ 事件分析頁 ████ 漏斗/路徑頁 ████ 歸因/用戶列表/標籤頁 ──── 可視化建模 ──── AI介面 ─ 打磨
WS6     ──── 契約/Gateway ──── 工具v1 ──── 指標搜尋/選擇題 ──── 異常下鑽/evals ──── MCP對外/守門 ──── 知識庫
里程碑           ▲v0.3 Alpha(S6)                 ▲v0.6 Beta(S12)                    ▲v1.0 GA(S20)
```

每個 Sprint 的 Definition of Done：功能測試通過、口徑一致性測試（UI vs agent 工具）、效能回歸（固定測試集）、授權檢查通過、文件更新。

## 5. 風險與緩解
| 風險 | 緩解 |
|---|---|
| 交易主機不允許 CDC | Sprint 2 前確認；備案為回報檔分鐘級拉取（WS2 已設計） |
| 百億級效能未達標 | WS3 自 S9 起每 Sprint 跑基準；預留分片擴充與抽樣策略 |
| LLM 回答品質不穩 | golden evals 自 S10 起納入 CI；選擇題式設計降低自由度 |
| 人力不足 | 先保證 WS2/WS3/WS4 主線；WS6 可延後一個版本 |
