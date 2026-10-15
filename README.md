# AltaTech DMP

可私有化部署的數位軌跡／客戶數據中台（Digital-trace & Customer Data Platform）：
承接 **GA360 埋點事件**（BigQuery 匯出）與**證券交易事件**，打通 OneID，提供事件／漏斗／留存／路徑等分析、標籤分群、即時觸發、受眾名單推送，
並以**語意層 + 工具閘道（MCP）+ Insight Agent** 讓 AI agent 能安全、可擴展地洞察資料。

對標：火山引擎 VeDI（DataFinder、VeCDP）、神策（神策分析、神策数界）。

## 目錄

```
docs/
  01-research-volcano-sensors-and-dtp-design.md   調研（火山引擎／神策／GA360／法遵）與中台總體設計
  02-backend-architecture.md                       後端架構（分層、元件、API、查詢路徑、部署）
  03-ai-agent-insight-layer.md                     AI agent 洞察擴展層（語意層、工具目錄、agent、洞察物件、治理）
  04-analysis-modules-p0.md                        P0 模組規格：事件、漏斗、行為路徑、歸因、用戶列表、標籤（含 ClickHouse 實作與效能目標）
  05-agent-capabilities-sensors-benchmark.md       以Sensors AI 智能分析師／智能體為參考的 agent 能力設計
  06-data-integration-visual-modeling.md           可視化建模與資料整合：MSSQL／BigQuery／PostgreSQL／GA 360 Sensors DATA event user ，欄位映射與清洗
  07-deployment-gcp-license-os.md                  GCP 容器化一鍵部署、開源授權審查（無需額外商業授權）、OS 支援矩陣
  08-engineering-plan.md                           語言與框架、團隊分工、版本規劃、6 個月時程
  09-scale-10b-events-performance.md               百億事件量級：稠密 ID、多層聚合、Bitmap 標籤引擎、查詢效能策略
  10-enterprise-security-rbac-masking-audit.md     企業級安全：角色／模組／數據權限、欄位隱碼、審批、審計與日誌統計可視化
  11-oss-risk-scorecard-and-maintainability.md     開源元件評分（弱點掃描風險／可用性／主流性／vibe coding 維護性）與選型修訂
  12-architecture-diagrams.md  diagrams/           系統架構圖、元件組成（部署）圖、數據流圖、元件模組圖（SVG/PNG 由 build.py 產生 + Mermaid）
  14-audience-activation.md                        受眾推送：簽章 Webhook 到內部系統（亦可 Kafka／GCS）；廣告平台不在範圍
  19-oneid-attribution.md                          OneID 歸戶邏輯（對齊神策 IDM 3.0：接入時決定、一個 ID 只屬一人、不自動合併、首次關聯升級、改綁政策；未登入事件以 distinct_id 歸匿名用戶，登入時近 7 天匿名事件改寫到客戶；情境與自測）
  21-segments-tags-custom-sql.md                   分群（規則／導入／結果保存、例行與版本、回收站、血緣、推送）、標籤（十種創建方式、審批、自定義欄位）、自定義 SQL（表目錄、脫敏預校驗、可視化、中間表）對照神策
  20-ga360-trading-instrumentation.md              GA360 埋點與交易事件打通：代碼化客戶 ID（HMAC）同款用於 GA user_id 與數據中台交易資料、手機／帳號去識別化、登入／登出埋點呼叫、匯出欄位映射；行為與交易事件只靠客戶代碼歸戶、不做逐筆對應
  17-product-spec.md                               產品規格書（總規格：架構圖、元件模組圖、數據流圖、資料模型、16 個模組規格、非功能、開發測試計畫、待確認索引）
  18-demo-audit-findings.md                        Demo 全面檢查：數據流／效能／建模／數據權限／元事件／用戶表／維度表／角色權限的問題、漏洞、優化與每模組待確認細節
  16-github-collaboration-with-claude.md          6 人分工（目錄即邊界）、Claude 三種用法與邊界、GitHub 協同（trunk-based、squash、branch protection、CODEOWNERS、CI 分層、@claude 指派）
  15-vibe-coding-plan.md                           Vibe coding 開發計畫：6 人分工、測試協同、Epic/Story 拆解（154 人日）、代碼級第一版第 8 週、AI 洞察 v1 第 16 週
  13-capacity-plan-gcp.md                          GCP 容量規劃：3 年 200 億事件（首年含舊資料）、每日 500–1,000 標籤、API 同步、50 人上線；正式 A 檔 6 台或 D 檔 3 台大機型／測試 3 台
prototype/                                          可直接開啟的前端原型（純靜態，無建置步驟）
  index.html  css/app.css  js/app.js  js/data.js  vercel.json
backend/                                            後端骨架（可讀、可跑的最小實作與設定）
  openapi.yaml                                     人與 agent 共用的 API
  semantic/{events,metrics,dimensions,dimension_tables,user_properties,oneid_rules,ga4_mapping}.yaml   語意層定義（事件、指標、維度、維度表／字典／虛擬屬性、用戶表、OneID 規則；agent 的資料地圖）
  mcp/tools.yaml  mcp/server.py                    Agent Tool Gateway（工具目錄 + 權限/預算/脫敏/審計骨架）
  insight_agent/agents.yaml  skills/*.md  evals/golden.yaml  runtime.py   agent 註冊、skill、評測、執行迴圈
  integration/connectors.yaml  models.example.yaml  transforms.py  auth.py   連接器定義（鑑權方式、最小權限、輪替、測試步驟）、建模產物、轉換函式庫、鑑權邏輯（密鑰參照、連線診斷、輪替）
  security/rbac.yaml  masking.yaml  policy.py                           角色矩陣、欄位隱碼策略、Policy Engine（authorize）骨架
  activation/destinations.yaml  webhook.py                           受眾名單推送目的地、Webhook 簽章／批次／重試
  oneid/attribution.py  semantic/oneid_rules.yaml                     OneID 歸戶參考實作與規則表（含登入改寫，10 個情境自測）
  clickhouse/audit.sql                             審計表、日誌統計物化視圖、row policy 與 masking view
  clickhouse/schema.sql  clickhouse/insight_store.sql      熱層 DDL、Insight Store（PostgreSQL + pgvector）
  docker-compose.yml                               本機開發環境（Kafka、ClickHouse、PostgreSQL、Valkey、物件儲存）
deploy/                                             部署套件
  helm/altatech-dmp/   umbrella chart（values.yaml 機房、values-gcp.yaml GCP、values-gcp-6n（正式 A）/3n-xl（正式 D，3 台大機型）/5n-noai/5n-lite/4n/3n（測試 C）、values-onprem-6n/3n-xl/3n（機房）、values-gcp-30b 擴張、values-small.yaml PoC）
  tofu/gcp/            OpenTofu：VPC、私有 GKE、節點池、Cloud SQL、GCS、KMS、Artifact Registry
  images/              Dockerfile 6 個（ingest / core / agent / batch / flink-jobs / web；UBI 9 minimal / distroless 基底）
  Makefile             gcp-up / deploy / smoke / sbom
LICENSES.md                                         第三方元件授權清單（白名單制）
SECURITY.md  AGENTS.md  CLAUDE.md  renovate.json                      弱點管理、AI 協作規則（目錄邊界／驗證指令）、Claude Code 開工／收工流程、自動更新
.github/  CODEOWNERS  pull_request_template.md  ISSUE_TEMPLATE/story.md  workflows/{ci,claude,security}.yml   協同：擁有者審查、PR／Story 模板、分層 CI、Claude 自動審查與 @claude 指派、安全掃描
```

## 原型

```bash
cd prototype && python3 -m http.server 8080   # 或直接部署到 Vercel（root = prototype）
```

UI：AltaTech 品牌色（#2C73EA）與四角箭頭標記；橫向導覽（首頁／分析／營運／資料／AI 洞察／數據治理／系統管理）＋ 第二列子導覽，參考 AltaHub。
頁面（P0 優先）：總覽、事件分析、漏斗分析、行為路徑、歸因分析、用戶列表、標籤與分群、即時觸發、數據整合（可視化建模）、用戶細查／OneID、留存（P1）、**AI 洞察**（洞察串流／選擇題式問答／Agents／工具目錄）、資料治理、**日誌統計**、**權限中心**（角色、模組權限矩陣、數據權限、欄位隱碼、審批）、平台設定。
**事件分析**（v0.4，對標神策／DataFinder）：多指標（事件 × 指標類型：總次數／觸發用戶數／人均次數／去重數／數值屬性求和、均值、最大、最小、中位數、90 分位）、指標篩選與全局篩選（事件屬性／用戶屬性／標籤，且／或切換，等於／包含於／區間／有值…）、按分組查看（最多 3 維、Top N）、時間粒度（分鐘／小時／日／週／月／合計）、對比上期、折線／柱狀／堆疊／累計／數據表切換、合計列、點擊人數下鑽到用戶列表、儲存報表、檢視編譯後 SQL。
**漏斗分析**（v0.4）：步驟新增／移除／上移與每步事件屬性篩選、窗口期（數值＋分鐘／小時／天）、關聯屬性、全局篩選、分組對比（平台／渠道／價值分級／年齡層／人群分群）、對比上期、漏斗圖／轉化趨勢／數據表切換、點選步驟看轉化率／中位轉化時長／流失人數並下鑽「流失用戶／轉化用戶」到用戶列表、儲存漏斗。
**行為路徑**（v0.4.1，對標神策「用戶路徑」）：起始事件之後／結束事件之前、起始事件篩選、參與分析的事件集（畫面／頁面可按屬性拆分成多個節點）、步數 3–8、會話間隔、合併連續重複、用戶數／會話數口徑、抽樣／全量、全局篩選、分群對比；桑基圖節點可點選看來源／去向、以該節點為新起點、下鑽用戶列表；Top 路徑可按「包含某事件」過濾；節點表視圖；SQL 隨設定更新。
**歸因分析**（v0.4.1，對標神策「歸因分析」）：目標轉化事件（轉化次數／用戶數／數值屬性求和，可篩選）、待歸因觸點（可命名、可選事件、可加屬性篩選，最多 10 個）、前向關聯事件＋關聯屬性、五種模型（首次／末次／線性／位置 U 型權重可調／時間衰減半衰期可調）與模型對比、回溯窗口（天／小時）、全局篩選、分組對比；結果含觸點次數、直接／輔助轉化、歸因轉化與占比、歸因價值、觸點轉化率、Top 轉化路徑、模型差異最大的觸點，觸點可下鑽用戶列表。
**留存分析**（v0.4.1，對標神策「留存分析」）：初始行為與後續行為各自可篩選、後續可選「任意事件」、日／週／月粒度、顯示期數、第 N 期當期留存／N 期內區間留存、留存／流失視角、比例／人數、關聯屬性、全局篩選、分組對比；留存表（平均列、未滿期留白、格子可下鑽到用戶列表）、留存曲線（平均 vs 所選 cohort 或各組對比）、分組對比表。
**治理（v0.5）**：元事件管理（事件 CRUD 抽屜、上線檢查、L4 拒絕、變更單、停用；屬性字典；虛擬事件；隔離區登記）、用戶表管理（屬性來源／更新／敏感／覆蓋率、OneID 歸戶規則與情境、個資防護與 DSAR）、維度表與字典（維度表以屬性值＝主鍵補充維度、維度字典、虛擬屬性；對齊神策元數據管理）、數據整合改為數據源管理／數據表管理／同步任務（對齊神策通用數據接入）、資料流與效能（端到端資料流、各模組讀寫、資料品質、查詢 P50/P95/P99 對 SLA、批次、儲存、告警）、權限中心新增使用者指派與行級規則模擬；審批／推送／治理寫入皆檢查權限。
**H5 行動版適配**（v0.4）：≤ 900px 時頂欄改為抽屜式選單、子導覽可橫向滑動、查詢條件面板可收合、所有網格改單欄、表格橫向捲動、抽屜全寬；所有頁面在 390px 寬無橫向溢出（Playwright 檢查）。
五種角色（數據分析、行銷、數位經營、系統管理員、資訊管理；營業員不進本系統）展示企業級權限：模組可見／可用／匯出／審批、欄位依敏感等級隱碼（L3 尾碼遮罩、L2 區間化）、揭露申請、審計。

## 後端骨架

```bash
pip install pyyaml
python3 backend/mcp/server.py              # 工具閘道：semantic.search 範例呼叫 + 審計事件
python3 backend/insight_agent/runtime.py   # agent 執行迴圈：plan → execute → verify → narrate
python3 backend/integration/transforms.py  # 欄位轉換函式庫自測
python3 backend/integration/auth.py        # 連線鑑權自測：密鑰參照驗證、逐步連線診斷、輪替、審計
python3 backend/oneid/attribution.py       # OneID 歸戶自測：首次關聯升級、改綁、共用裝置、帳戶綁定、衝突、同意
docker compose -f backend/docker-compose.yml up -d   # 本機基礎設施
cd deploy && make gcp-up PROJECT=<gcp-project> && make deploy ENV=gcp   # GCP 一鍵部署（需 tofu/helm/kubectl）
```

## AI agent 擴展能力（設計重點）

1. **語意層**：指標／維度／事件以 YAML 定義（含口徑、caveats）；UI 與 agent 走同一個查詢編譯器，口徑與權限一致。
2. **工具即契約**：每個能力是有 schema、權限範圍、預算、脫敏策略、審計的 MCP 工具；新增能力 = 新增工具，不改 agent 程式。
3. **洞察是一級資料物件**：帶可重跑的證據、信心度、影響範圍、行動提議、回饋；可訂閱、再利用、餵回 agent。
4. **人在迴路**：agent 可提議分群／規則，但生效需審批；敏感工具需理由；所有工具呼叫寫審計事件。
5. **模型無關**：透過 LLM Gateway；golden evals 保證換模型或改 skill 不退化。

詳見 `docs/03-ai-agent-insight-layer.md`。
