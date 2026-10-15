# AltaTech DMP 產品規格書（v0.5，2026-10-15）

> 本文是總規格：每一節給結論與圖，細節連到對應文件。讀者：客戶 PO／架構／資安／採購、AltaTech 開發團隊。對應原型 v0.5.0（demo）、後端契約 `backend/`、部署套件 `deploy/`。

## 0. 一頁摘要

| 項目 | 內容 |
|---|---|
| 產品 | 可私有化部署的數位軌跡／客戶數據中台：承接 GA360 埋點事件與證券交易事件，OneID 打通，提供事件／漏斗／留存／路徑／歸因／用戶列表六個分析模組、標籤與分群、即時觸發與受眾推送、AI 洞察 agent；企業級角色／模組／數據權限、欄位隱碼、審批與審計 |
| 對標 | 神策分析／SDP／用戶畫像、火山引擎 DataFinder／VeCDP（docs/01、05） |
| 規模 | 3 年 200 億事件（首年含舊資料 50 億）、每日 500 個標籤、50 人同時上線、尖峰 7,000 事件/秒（docs/13） |
| 部署 | 6 個自建映像 + Helm + OpenTofu；正式 GCP A 檔 6 台（或 D 檔 3 台大機型），測試 C-經濟 3 台；機房 RKE2 同一套件（docs/07、13） |
| 授權 | 全部 OSI 寬鬆授權（Grafana OSS 僅內部使用），金融業無需額外商業授權（docs/07、11、LICENSES.md） |
| 時程 | 代碼級第一版第 8 週、P0 完整第 12 週、AI 洞察 v1 第 16 週、GA 約 6 個月（docs/15） |

## 1. 系統架構圖

![系統架構圖](diagrams/system-architecture.png)

分層：來源 → 接入（Collector／KafkaConnect）→ 串流（Kafka、Flink 事件化、OneID、session）→ 儲存（ClickHouse 熱層 + MV、Iceberg 冷層、PostgreSQL 控制面、Valkey）→ 語意層與 Policy Engine → 服務（六個分析模組、標籤／分群、觸發／推送、AI 工具閘道）→ 前端與 API；橫切：身分與權限、資料保護、審計與法遵、觀測、部署。詳見 docs/02、12。

## 2. 元件模組圖

![元件模組圖](diagrams/module-map.png)

功能模組與它們負責的資料、模組間關係：接入與治理（數據整合、元事件管理、用戶表／維度表）→ 核心資料（events + MV、users／items／id_map、Bitmap／segments／insights）；分析與營運八個模組與 AI 洞察都經「語意層 + 查詢編譯器」與「Policy Engine」存取資料。部署視圖（節點池、Pod、託管服務）見 `diagrams/component-deployment.png`（docs/12）。

## 3. 數據流圖

![數據流圖](diagrams/data-flow.png)

| 流 | 路徑 | 延遲目標 |
|---|---|---|
| 準即時行為 | GA360 BigQuery 串流匯出（events_intraday_）→ Importer → Kafka raw → Flink（normalize／eventize／OneID／session）→ ClickHouse | 分鐘級（P95 < 15 分鐘，受 GA 串流匯出延遲限制） |
| 日批行為 | GA360 BigQuery 日批匯出（events_，權威）→ Storage Read API → Kafka raw → 同上；event_id 去重，覆蓋串流 | 前日資料 07:00 前到齊 |
| 交易／帳務 | 數據中台／CDP 事實表批次導入（每 15 分鐘增量 + 日批校正；數據中台有 Kafka 則流任務）→ Kafka → Flink 狀態機事件化（委託／成交／失敗／刪單）；不直連交易主機 | < 20 分鐘（批次）／< 1 分鐘（Kafka） |
| 主檔 | CRM／HR／商品主檔 → 建模映射 → users／items | 每日 |
| 分析查詢 | UI／API／agent → 語意層編譯 → Policy（行級、遮罩）→ ClickHouse（MV 優先）→ 快取 → 審計 | 各模組 P95 SLA 見 §5.9 |
| 標籤與名單推送 | dbt 日批 → user_tags Bitmap → 分群 → 審批 → Webhook／Kafka／GCS（內部系統）→ deliveries | 日批 < 20 分鐘；增量 15 分鐘送完 |
| 治理 | 元事件／屬性／用戶表／Item／OneID 規則變更 → 語意層 YAML → 影響範圍 → 變更單 → 審計 | 即時生效（上線）或審批後 |

每個模組讀寫哪些資料見 docs/18 §2 與原型「資料流與效能」頁。

## 4. 資料模型與語意層

- Event–User–Item 模型，神策相容協議（track／profile_*／item_*／identities）與 GA4 匯出映射（docs/01、06）；OneID 歸戶對齊神策 IDM 3.0（docs/19）。
- ClickHouse：`events`（L0，按 project／event_date／event／one_id／time 排序，月分區，13 個月熱層）、MV L1–L3、`users`、維度表 `dim_*`（dictionary）、`id_map`、`user_tags`（Bitmap）、`audit_log`（`backend/clickhouse/*.sql`）。
- 語意層 YAML：`events.yaml`、`metrics.yaml`、`dimensions.yaml`、`dimension_tables.yaml`（維度表／字典／虛擬屬性）、`user_properties.yaml`、`oneid_rules.yaml`、`ga4_mapping.yaml`；UI、API、agent 工具走同一查詢編譯器。
- 敏感等級 L0–L4：L4 永不入倉；L3 代碼化；L2 匯出區間化；依角色遮罩（docs/10）。

## 5. 模組規格（每模組：功能、輸入／輸出、權限、效能、契約）

| 模組 | 功能要點（對標神策／DataFinder） | 契約 | 權限 | 效能目標（P95） | 細節 |
|---|---|---|---|---|---|
| 5.1 數據整合（數據源的集成與管理） | 數據源管理（鑑權方式排序、密鑰參照不入庫、逐步測試、最小權限自檢、輪替）→ 數據表管理（Schema 探索、主鍵／增量欄位、版本與變更）→ 同步任務（目標 events 只增量／users upsert／維度表／OneID 綁定／標籤；全量／增量／流任務；排程、依賴、日誌、重試、Schema 變更處理）→ 清洗與隔離；建立任務走七步精靈 | `/v1/integration/*` | integration v/u/d | 連線測試 < 15 s；發佈後首批資料 < 5 分鐘 | docs/06 §7 |
| 5.2 元事件管理 | 事件字典（狀態：草稿／上線／隔離／停用）、事件 CRUD 與上線檢查（命名、負責人、屬性型別與敏感等級、無 L4）、屬性字典（型別只放寬、敏感只升）、虛擬事件、隔離區登記（30 天丟棄）、變更單審批、路徑節點規則 | `/v1/meta/events*`、`/v1/meta/properties`、`/v1/meta/virtual-events`、`/v1/meta/quarantine` | governance v/d | 上線即生效（語意層、Collector、Flink） | docs/18 |
| 5.3 用戶表管理 | 用戶屬性（來源、更新、敏感、可篩選／分組、覆蓋率、分桶）、profile_* 協議、OneID 規則（識別優先序、強度、合併策略、合併／拆分統計）、個資防護（L4 清單、偵測規則）、DSAR | `/v1/meta/user-properties`、`/v1/meta/oneid/rules` | governance v/d | — | docs/18、`semantic/user_properties.yaml` |
| 5.4 維度表與字典 | 維度表（以「屬性值 = 主鍵」補充事件／用戶沒有埋的維度，欄位成為關聯屬性；主鍵唯一；< 100 萬列否則 dictGet）、維度字典（值 → 顯示名，1:1／N:1，新增／覆蓋）、虛擬屬性（表達式，查詢時計算） | `/v1/meta/dimension-tables*`、`/v1/meta/dictionaries*`、`/v1/meta/virtual-properties` | governance v/d | 同步依任務（即時～每日） | docs/06 §7.2、`semantic/dimension_tables.yaml` |
| 5.5 事件分析 | 多指標 × 指標類型（總次數／用戶數／人均／去重／數值求和、均值、最大、最小、中位、P90）、指標篩選與全局篩選（且／或、運算子）、按分組（≤3 維、Top N）、粒度（分鐘～合計）、對比上期、折線／柱狀／堆疊／累計／表格、合計列、下鑽用戶列表、儲存報表、SQL | `/v1/analytics/events` | events v/u/e | 3 s（MV 命中 < 1 s） | docs/04 §1 |
| 5.6 漏斗分析 | 步驟增刪與每步篩選、窗口（值＋單位）、關聯屬性、全局篩選、分組對比、對比上期、漏斗圖／趨勢／表格、步驟明細（轉化率、中位時長、流失）、下鑽流失／轉化用戶 | `/v1/analytics/funnel` | events | 5 s | docs/04 §2 |
| 5.7 留存分析 | 初始／後續行為各自篩選、任意事件、日／週／月、期數、當期／區間、留存／流失、比例／人數、關聯屬性、分組；留存表（平均列、格子下鑽）、曲線、分組表 | `/v1/analytics/retention` | events | 5 s | docs/04 §7（P1 提前） |
| 5.8 行為路徑 | 起始／結束事件、參與事件集（按屬性拆分）、步數、會話間隔、合併重複、用戶／會話口徑、抽樣、篩選、分群；桑基圖節點可點選、以節點為新起點、下鑽；Top 路徑過濾；節點表 | `/v1/analytics/path` | events | 8 s（SAMPLE 1/10） | docs/04 §3 |
| 5.9 歸因分析 | 目標事件與指標（次數／用戶／數值）、觸點（命名、事件、篩選）、前向關聯事件、五模型（位置權重、半衰期可調）與對比、回溯窗口、分組；觸點次數、直接／輔助轉化、歸因值、轉化路徑、模型差異 | `/v1/analytics/attribution` | events | 5 s | docs/04 §4 |
| 5.10 用戶列表／細查 | 條件 → Bitmap 估數 → 分頁；來源條件下鑽（事件／漏斗／留存／路徑／歸因）；存為分群／匯出（審批、浮水印、上限）；細查需理由、身分圖、時間軸 | `/v1/users`、`/v1/profiles/*` | users、profile | 估數 1 s；分頁 2 s；細查 1 s | docs/04 §5 |
| 5.11 標籤與分群 | 標籤類型（規則／統計／SQL／模型）、日批 Bitmap（版本 7）、分群交並差、審批、Tag Sync API（REST／Kafka／GCS） | `/v1/segments*`、`/v1/sync/*` | segments | 500 標籤日批 < 20 分鐘 | docs/04 §6、09 |
| 5.12 即時觸發／受眾名單推送 | Flink 規則引擎；目的地皆為內部系統（Webhook 簽章／批次／重試／DLQ、Kafka、GCS）；推送單審批、交付紀錄；廣告平台不在範圍 | `/v1/triggers*`、`/v1/destinations*`、`/v1/audiences/*/push` | triggers、segments a | 即時 < 5 s；批次 15 分鐘 | docs/14 |
| 5.13 AI 洞察（第二階段可延後） | 工具閘道（tools.yaml：權限／預算／脫敏／審計）、agent（異常偵測、漏斗診斷、流失雷達）、選擇題式問答、洞察物件、提議需審批、golden evals | `/v1/insights*`、`/v1/agents*`、MCP | ai_ask、ai_admin | 工具呼叫 5 s；預算 20 GB/呼叫 | docs/03、05 |
| 5.14 權限中心 | 角色（五種：系統管理員、行銷、數據分析、數位經營、資訊管理 + 自訂、勾選權限）、使用者指派（多角色、範圍、到期、SSO 群組、臨時提權）、模組矩陣（v/u/e/a/d）、數據權限（專案／分群限制／回溯／K 匿名；組織範圍保留給自訂角色）與行級模擬、欄位隱碼、審批 | `/v1/iam/*` | iam v/d | authorize() < 5 ms | docs/10 |
| 5.15 日誌統計／審計 | 全部人與 agent 的讀寫寫審計；看板：使用概況、敏感存取、審批、異常 | `/v1/governance/audit*` | audit | — | docs/10 §7–8 |
| 5.16 資料流與效能 | 端到端資料流（吞吐／延遲／狀態）、各模組讀寫、資料品質、查詢效能（P50/P95/P99 對 SLA、掃描、快取）、批次作業、儲存、告警 | `/v1/ops/*` | governance v | Prometheus 供數 | docs/09、13 |

## 6. 非功能規格

| 項目 | 規格 |
|---|---|
| 容量 | 3 年 200 億事件全熱（ClickHouse 2 副本 7.8 TB）、GCS 8 TB、500 標籤／日、50 人併發、尖峰 7,000 事件/秒（docs/13 §1–2） |
| 可用性 | A 檔：任一台故障不中斷；ClickHouse 2 副本、Kafka／Keeper quorum；RPO 15 分鐘（checkpoint）、RTO 30 分鐘；DR 站為 v2.0 |
| 安全 | 私有網路、TLS 強制、CMEK／KMS、密鑰只存參照、最小權限、Pod Security restricted、SBOM／Trivy／Grype／Semgrep／gitleaks、授權白名單（docs/07、10、11、06 §6） |
| 法遵 | L4 不入倉、代碼化、K 匿名、揭露審批、審計 WORM、DSAR 30 天（docs/01 §法遵、10） |
| 可維護性 | SQL-first（Flink SQL／dbt／MV）、6 個映像、Operators、Renovate、AGENTS.md／CLAUDE.md（docs/11、16） |
| 可觀測 | Prometheus + OTel + Grafana OSS；資料流、查詢、批次、儲存、審計看板；告警規則（原型「資料流與效能」頁） |
| 相容 | 神策協議超集、GA4 匯出 schema、OpenAPI 3.1、MCP 工具；OS：RHEL 9／Rocky 9／Ubuntu 24.04 節點（docs/07 §OS） |

## 7. 開發測試計畫

摘要（完整見 docs/15、16）：

| 里程碑 | 範圍 | 週 |
|---|---|---|
| M1 走通一條線 | GA360 BigQuery 日批 + 串流 → ClickHouse；事件分析查真資料；SSO | 3 |
| M2 代碼級第一版 | 六個分析模組接真 API、OneID v1、數據中台交易批次導入、RBAC／隱碼／審計、元事件／用戶表／維度表管理基本版、測試環境部署 | 8 |
| M3 P0 完整 | 標籤 v1、分群、推送、建模 v1、Tag Sync、神策匯入工具、資料流與效能頁接 Prometheus | 12 |
| M4 AI 洞察 v1 | 工具閘道、分析工具、異常 agent、問答、golden evals | 16 |
| M5 GA | 10 億合成事件基準、安全強化、機房驗證、DR 演練 | 22–24 |

測試層：單元（≥70%，編譯器 ≥90%）、契約（schemathesis）、口徑一致性（UI 與 agent 同 SQL、golden 答案集）、資料（dbt tests、1 千萬 fixture）、權限／隱碼（5 角色打每個 API）、e2e（Playwright 桌機＋390px）、效能（1 億 → 10 億）、安全（CI 掃描）、AI 品質（golden evals ≥95%）。協同：trunk-based、squash、branch protection、CODEOWNERS、Claude 自動審查與 @claude 指派（docs/16）。

每模組驗收條件寫在 Story issue（模板 `.github/ISSUE_TEMPLATE/story.md`），以 docs/04 §8 的驗收矩陣為基準。

## 8. 產品每個模組待確認細節

見 docs/18 §3（19 個模組／面向的待確認項與建議預設）。決定後回寫到對應 YAML（semantic、security、connectors）與本規格。

## 9. 文件索引

| 文件 | 內容 |
|---|---|
| docs/01 | 調研與總體設計 |
| docs/02 | 後端架構 |
| docs/03、05 | AI agent 洞察層、神策 AI 對標 |
| docs/04 | P0 分析模組規格與 ClickHouse 實作 |
| docs/06 | 數據整合與可視化建模；§6 連線鑑權 |
| docs/07 | GCP 部署、授權、OS |
| docs/08、15、16 | 工程規劃、vibe coding 計畫、GitHub 協同 |
| docs/09 | 百億級效能 |
| docs/10 | 企業級安全：角色／模組／數據權限、隱碼、審批、審計 |
| docs/11 | 開源風險評分與可維護性 |
| docs/12 | 架構圖集（4 張）與簡報版 |
| docs/13 | 容量規劃（GCP／機房／Dell、3 台 vs 6 台） |
| docs/14 | 受眾名單推送（內部系統） |
| docs/19 | OneID 歸戶邏輯（對齊神策 IDM 3.0） |
| docs/18 | Demo 全面檢查：問題、漏洞、優化、待確認 |
