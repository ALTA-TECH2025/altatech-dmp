# Vibe Coding 開發計畫：分工、測試協同、拆解與「代碼級第一版」時程

> 版本 v0.1（2026-10-14）。前提：原型（prototype/）、後端骨架與契約（backend/openapi.yaml、semantic/*.yaml、mcp/tools.yaml、security/*.yaml）、部署套件（deploy/）已完成；範圍依 docs/13 v0.6（不寫回神策、無 AI Coach、無客服工單／營業員 App、Customer Match 不做、自建映像 6 個）。

## 0. 結論先講

| 里程碑 | 內容 | 時間（自 kick-off 起） |
|---|---|---|
| **M1 走通一條線** | GA360 BigQuery 日批 + 串流 → Kafka → Flink → ClickHouse；事件分析頁查到真資料；SSO 登入 | 第 3 週末 |
| **M2 代碼級第一版（可演示）** | 6 個分析模組（事件、漏斗、留存、路徑、歸因、用戶列表）全部接真 API；OneID v1；交易 CDC；RBAC／隱碼／審計基本版；Helm 部署在測試環境（C-經濟 3 台） | **第 8 週末** |
| **M3 P0 完整** | 標籤 v1（規則／SQL／統計，Bitmap 日批）、分群、受眾推送（Webhook／GA4）、可視化建模 v1、Tag Sync API、神策歷史一次性匯入工具 | 第 12 週末 |
| **M4 AI 洞察 v1** | 工具閘道、metric.query／funnel.run／analysis.configure、異常偵測 agent、選擇題式問答、golden evals | 第 16 週末 |
| **M5 GA** | 10 億合成事件效能基準、安全強化、機房 RKE2 驗證、DR 演練、文件 | 第 22–24 週 |

**代碼級第一版 = 第 8 週**（6 人、每人配一個 AI 編程 session），比 docs/08 的 10 個月 GA 縮到約 6 個月，靠的是：範圍已砍、契約先行、原型已定 UI、6 個映像、SQL-first（Flink SQL／dbt／ClickHouse MV）讓 AI 產碼可讀可驗。

前提條件（第 2 週前要到位，否則整體順延）：GA360 BigQuery 匯出權限（含串流 intraday）、交易主機 CDC（或回報檔）存取、測試環境 GCP 專案與 3 台配額、SSO（Keycloak 或既有 IdP）測試帳號。

## 1. 團隊與分工（6 人，每人一條工作流 + 一個 AI session）

| 角色 | 人 | 擁有的目錄 | 主要交付 | AI 的用法 |
|---|---|---|---|---|
| **TL／架構** | 1 | `backend/openapi.yaml`、`semantic/`、`AGENTS.md`、CI | 契約、口徑、Code review 最終把關、每日契約同步 | 用 AI 做 PR 審查第一輪（repo 內 `AGENTS.md` 規則）、生成契約測試 |
| **資料平台** | 1 | `backend/stream/`、`backend/integration/`、`clickhouse/` | Collector（Go）、GA360 匯入、CDC、Flink SQL、ClickHouse 基表／MV、OneID、Bitmap 日批 | Flink SQL／dbt／DDL 讓 AI 寫，人定 schema 與分區鍵；每段 SQL 配 fixture 測試 |
| **後端查詢** | 1 | `backend/api/`、`backend/query/`、`security/` | 語意層編譯器（YAML → ClickHouse SQL）、6 個分析 API、用戶列表、Policy Engine、審計 | AI 產編譯器與測試；人定 SQL 樣板（windowFunnel、retention、sequenceMatch、觸點表） |
| **前端** | 1 | `prototype/` → `web/` | 把原型升級為正式前端（TypeScript + Vite，保留頁面結構與 data-act 模式）、接 API、權限／隱碼由 API 回傳 | 原型即規格；AI 逐頁把 `pageXxx` 改成呼叫 API，保留 Playwright 腳本做回歸 |
| **平台／安全** | 1（可 0.5） | `deploy/`、`.github/`、`LICENSES.md` | 6 個映像、Helm、測試環境、SSO、Secret、SBOM／Trivy／Semgrep、監控看板 | AI 產 Helm template 與 Grafana 看板 JSON；人管密鑰與網路 |
| **QA／數據測試** | 1 | `tests/`、`tools/synth/` | 合成資料產生器（1 千萬→1 億→10 億事件）、golden 查詢答案集、e2e Playwright、效能基準、口徑一致性測試 | AI 產測試案例與資料產生器；人定「正確答案」與驗收口徑 |

AI 洞察層（M4）由後端查詢 + TL 接手，不另加人。PM／PO 由客戶側或 AltaTech 兼任。

## 2. Vibe coding 的工作規則（讓 6 個 AI session 不互相踩）

1. **契約先行**：`openapi.yaml`、`semantic/*.yaml`、`mcp/tools.yaml`、`security/rbac.yaml` 是唯一真相；改契約要先開 PR，TL 當天合併，其他人隔天 `git pull` 後再叫 AI 重生 client／stub。
2. **目錄即邊界**：每人只在自己的目錄開 AI session；跨目錄的需求走 issue，不直接改別人的檔。`dmp-core` 內以 Python package 切模組，CI 用 import-linter 禁止跨模組 import。
3. **AGENTS.md 是 AI 的 onboarding**：規則、驗證指令、禁止事項都在裡面；每個 PR 的 AI 摘要要附「跑過哪些驗證指令」。
4. **小 PR、每天合併**：一個 PR 一件事、< 400 行；AI 產碼必附測試；CI 綠才能合。
5. **人負責三件事**：定規格與口徑、審 SQL 與權限相關程式碼、決定「正確答案」。其餘讓 AI 寫。
6. **每日 15 分鐘契約同步**、每週五 demo（跑在測試環境，不是本機）。

## 3. 測試協同（誰寫、誰驗、在哪跑）

| 層 | 工具 | 誰寫 | 觸發 | 門檻 |
|---|---|---|---|---|
| 單元 | pytest、vitest、go test | 各 workstream（AI 產） | 每個 PR | 覆蓋率 ≥ 70%（查詢編譯器 ≥ 90%） |
| 契約 | schemathesis 打 `openapi.yaml`；YAML schema 驗證 | TL | 每個 PR | 0 違反 |
| **口徑一致性** | 同一語意層定義：UI 查詢 API 產生的 SQL == agent 工具產生的 SQL；結果對 golden 答案集誤差 0 | 後端查詢 + QA | 每個 PR（小集）、每夜（全集） | 全部一致 |
| 資料 | dbt tests（唯一、非空、關聯）、ClickHouse fixture（1 千萬合成事件） | 資料平台 + QA | 每夜 | 0 失敗 |
| 權限／隱碼 | 以 5 個示範角色打每個 API，比對 rbac.yaml／masking.yaml 期望 | 後端查詢（AI 產） | 每個 PR | 0 越權 |
| e2e | Playwright：原型的腳本改打正式前端（登入、6 模組查詢、下鑽、匯出審批） | 前端 + QA | 每夜、發版前 | 全綠 |
| 效能 | 1 億合成事件（M2）→ 10 億（M5）：30 天漏斗 P95、標籤日批時間、50 併發 | QA + 資料平台 | 每 Sprint | docs/09、13 的目標 |
| 安全 | Semgrep、gitleaks、Syft／Grype、Trivy、授權白名單 | 平台 | 每個 PR | Critical 0 |
| AI 品質（M4 起） | golden evals（問題 → 期望工具呼叫與答案） | TL + 後端 | 每個 PR 改 skill／工具時 | 通過率 ≥ 95% |

合成資料產生器是整個測試的地基：第 1 週就要有（1 千萬事件、30 萬 OneID、含已知答案的漏斗／留存／路徑），之後所有「對不對」都對它驗。

## 4. 拆解（Epic → Story，估人日，含 AI 協作）

估算口徑：有 AI 協作、原型與契約已在，1 人日 ≈ 人工 2–3 人日的產出。

### E1 平台與環境（平台，14 人日）
| Story | 人日 | 驗收 |
|---|---|---|
| 測試環境 GCP：tofu `3n`（C-經濟）、GKE、Cloud SQL、GCS、Artifact Registry | 3 | `make gcp-up` 一次成功 |
| 6 個映像 Dockerfile 可建、CI 建置＋簽章＋SBOM | 3 | `make images` 綠 |
| Helm umbrella 可部署 Kafka／ClickHouse／Keeper／Flink Operator／Keycloak／Prometheus／Grafana | 4 | `make deploy ENV=gcp-3n` 全部 Ready |
| SSO（Keycloak OIDC）＋ Ingress ＋ Secret | 2 | 5 個示範角色可登入 |
| CI：lint／type／unit／契約／授權／安全掃描 | 2 | PR 必跑 |

### E2 資料接入與事件化（資料平台，26 人日）
| Story | 人日 | 驗收 |
|---|---|---|
| Collector（Go）：/v1/events 神策協議、/sa 批次；寫 Kafka raw | 3 | 1 萬事件／秒壓測 |
| GA360 BigQuery 匯入：日批 events_（權威）+ 串流 events_intraday_（分鐘級）→ Kafka raw；event_id 去重、日批覆蓋 | 4 | 前日 05:30 前到齊；串流延遲 < 15 分鐘 |
| 交易 CDC（Debezium MSSQL，KafkaConnect）或回報檔分鐘級拉取 | 4 | 委託／成交 2 秒內入 Kafka |
| Flink SQL：normalize → eventize（ga4_mapping.yaml）→ session → 寫 ClickHouse | 5 | 口徑測試通過 |
| OneID v1：接入時歸戶（docs/19 規則：一個 ID 只屬一人、不自動合併、首次關聯升級、改綁政策）、id_map、one_id_uint、Valkey 快取 | 4 | attribution.py 的 9 個情境 + 合成資料 100% |
| ClickHouse 基表（events、users、items、id_map）、MV L1–L3、TTL、row policy | 4 | 1 千萬 fixture 載入 |
| 合成資料產生器（QA 協作）接 Collector 回放 | 2 | 可重複回放 |

### E3 語意層與分析 API（後端查詢，30 人日）
| Story | 人日 | 驗收 |
|---|---|---|
| 查詢編譯器：metrics／dimensions／events YAML → ClickHouse SQL；篩選（且／或、運算子）、分組、粒度、對比 | 5 | 編譯器單元 ≥ 90% |
| 事件分析 API（多指標、指標類型、Top N、合計） | 3 | golden 一致 |
| 漏斗 API（windowFunnel、每步篩選、窗口、關聯屬性、分組、趨勢、流失名單） | 4 | golden 一致 |
| 留存 API（retention()、當期／區間、任意事件、分組） | 3 | golden 一致 |
| 行為路徑 API（session 序列、事件集、拆分、步數、合併重複、SAMPLE） | 4 | golden 一致 |
| 歸因 API（觸點表日批 + 五模型同掃） | 4 | golden 一致 |
| 用戶列表 API（條件 → Bitmap 估數 → 分頁；下鑽來源條件） | 3 | < 1 秒估數 |
| Policy Engine：authorize()、行級、欄位隱碼 L0–L4、審計事件 | 4 | 越權測試 0 |

### E4 前端（前端，24 人日）
| Story | 人日 | 驗收 |
|---|---|---|
| 原型 → TypeScript + Vite 工程化、API client（由 openapi 生成）、登入／路由／權限 | 4 | e2e 登入 |
| 事件分析、漏斗接 API（篩選建構器共用元件） | 4 | e2e |
| 留存、行為路徑、歸因接 API | 5 | e2e |
| 用戶列表、用戶細查、來源條件下鑽 | 3 | e2e |
| 標籤／分群／推送頁接 API（M3） | 4 | e2e |
| 權限中心、日誌統計接 API；H5 回歸 | 4 | e2e（390px） |

### E5 標籤、分群、推送（資料平台 + 後端，M3，22 人日）
| Story | 人日 |
|---|---|
| 標籤定義（規則／SQL／統計）、dbt 日批、Bitmap 表、版本 7 份 | 6 |
| 分群（條件 → Bitmap 交並差）、審批流 | 4 |
| Tag Sync（Kafka 增量、GCS Parquet、REST 點查） | 4 |
| 受眾名單推送：Webhook 簽章／批次／重試／DLQ、Kafka、GCS | 3 |
| 可視化建模 v1（MSSQL／PG／BigQuery 連接、欄位映射、清洗產 DAG） | 4 |

### E6 AI 洞察 v1（TL + 後端，M4，18 人日）
| Story | 人日 |
|---|---|
| 工具閘道（tools.yaml → MCP；權限／預算／脫敏／審計） | 4 |
| metric.query、funnel.run、retention.run、path.run、users.list、analysis.configure | 4 |
| 異常偵測 agent（排程）、洞察物件與 Insight Store | 4 |
| 選擇題式問答 UI 接線、可校驗回答 | 3 |
| golden evals 進 CI | 3 |

### E7 測試與基準（QA，貫穿，20 人日）
合成資料（3）、golden 答案集（4）、e2e 腳本（5）、效能基準 1 億／10 億（5）、口徑一致性框架（3）。

合計約 **154 人日**；6 人並行、扣除整合與等待，約 **16 週到 M4**，M2（代碼級第一版）落在第 8 週。

## 5. 時程（2 週一個 Sprint）

```
週      1-2            3-4            5-6            7-8            9-10           11-12          13-14          15-16
平台    環境/映像/CI   Helm/SSO       監控/安全掃描   測試環境固化    機房RKE2驗證    DR/備份        強化           文件
資料    合成資料/Collector GA匯入/Flink  CDC/OneID      MV/路徑抽樣    標籤Bitmap日批  TagSync/推送    神策匯入工具    10億基準
後端    編譯器         事件/用戶列表API 漏斗/留存API   路徑/歸因/權限  分群/審批      建模v1 API     工具閘道/agent  evals
前端    工程化/登入     事件分析頁      漏斗/留存頁     路徑/歸因/列表  標籤/分群頁    推送/建模頁     AI問答頁        H5/打磨
QA      產生器/golden  契約測試       口徑一致性      e2e全模組      1億效能        權限/隱碼回歸   evals          驗收
里程碑         ▲M1(W3)                       ▲M2 代碼級第一版(W8)        ▲M3(W12)                     ▲M4(W16)
```

## 6. 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| 資料存取（BigQuery／CDC／SSO）第 2 週未到位 | M1、M2 順延同等週數 | kick-off 前列為前提；先用合成資料走通全線，真資料接上只是換來源 |
| AI 產碼品質參差、口徑漂移 | 數字錯 | 口徑一致性測試 + golden 答案集每 PR 跑；SQL 與權限程式碼人審 |
| 6 個 session 互相覆蓋 | 合併衝突、重工 | 目錄即邊界、契約先行、小 PR 每日合併 |
| 效能到 10 億才發現問題 | GA 延後 | 第 9–10 週先跑 1 億，分區鍵／MV 設計在 M2 前定稿 |
| 人力只有 4 人 | M2 延到第 10–11 週 | 平台與 QA 合併一人、AI 層延後；主線（資料、後端、前端）不動 |
