# 數位軌跡中台（Digital Trace Platform, DTP）設計書

> 私有化部署、承接 GA360 埋點事件與證券交易事件的使用者行為資料中台
> 對標：火山引擎 VeDI（DataFinder 增長分析 + VeCDP 客戶數據平台）、神策數據（神策分析 + SDP 數據根基平台 + 用戶畫像 + 智能運營）
> 版本：v0.1（2026-10-14）　狀態：設計提案，待架構評審

> **v0.2 勘誤（授權審查後）**：本文件原選型中的 MinIO（AGPL）、Redis（RSAL/SSPL）、HashiCorp Vault（BSL）、Grafana/Loki（AGPL）已分別改為 GCS／SeaweedFS、Valkey、Cloud KMS／OpenBao、Perses／OpenSearch，詳見 `07-deployment-gcp-license-os.md` 與 `LICENSES.md`；分析模組優先序改依 `04-analysis-modules-p0.md`。**範圍修訂**：神策只做一次性歷史匯入（不接增量、不寫回，見 06 文件 §4）；第 14 節「與 AI Coach 的銜接」作廢，平台下游為 MA、客服與營業員 App。

> **v0.3 勘誤（角色）**：本文提到的「營業員僅能看所屬客戶」「分公司主管僅見分公司」「稽核角色」不再適用。本系統的使用者只有五種角色：系統管理員、行銷、數據分析、數位經營、資訊管理（`backend/security/rbac.yaml`、docs/10 v0.4）；營業員與分公司主管不進本系統，客戶的所屬營業員／分公司只是分析維度，不是行級權限。

> **v0.4 勘誤（交易資料來源）**：本文的「交易主機 MSSQL CDC（Debezium）」不再適用。交易與帳務資料來自數據中台／CDP 的事實表，以批次導入（每 15 分鐘增量）或數據中台提供的 Kafka 接入；交易事件可查延遲口徑改為 P95 < 20 分鐘（批次）／< 1 分鐘（Kafka）。見 docs/06 v0.6、docs/17 §資料來源。


---

## 目錄

0. [文件目的與範圍](#0-文件目的與範圍)
1. [標竿調研：火山引擎與神策](#1-標竿調研火山引擎與神策)
2. [需求、限制與設計原則](#2-需求限制與設計原則)
3. [整體架構](#3-整體架構)
4. [統一事件模型（Event–User–Item）](#4-統一事件模型eventuseritem)
5. [資料接入設計](#5-資料接入設計)
6. [ID-Mapping 與 OneID](#6-id-mapping-與-oneid)
7. [儲存與計算層](#7-儲存與計算層)
8. [分析能力（對標分析模型）](#8-分析能力對標分析模型)
9. [標籤、分群、畫像與即時輸出](#9-標籤分群畫像與即時輸出)
10. [埋點治理、資料品質與血緣](#10-埋點治理資料品質與血緣)
11. [資安、個資與法遵](#11-資安個資與法遵)
12. [私有化部署架構](#12-私有化部署架構)
13. [實施路線圖](#13-實施路線圖)
14. [與 AI 對練 / AI Coach 的銜接](#14-與-ai-對練--ai-coach-的銜接)
15. [風險與待決事項](#15-風險與待決事項)
- [附錄 A：證券事件字典（初版）](#附錄-a證券事件字典初版)
- [附錄 B：GA360 → DTP 欄位映射表](#附錄-bga360--dtp-欄位映射表)
- [附錄 C：參考來源](#附錄-c參考來源)

---

## 0. 文件目的與範圍

本文件回答三個問題：

1. **火山引擎 CDP／增長分析與神策分析到底提供了哪些能力？** 哪些是「使用者行為分析平台」的標配，哪些是差異化功能，哪些與證券業務有關。
2. **若要在證券公司內部（私有化、資料不出境）自建一套同等級的數位軌跡中台，應該長什麼樣？** 包括架構、資料模型、ID 打通、儲存、分析模型、標籤分群、即時輸出。
3. **如何同時承接兩類性質迥異的資料？** 一是 Web/App 的 GA360 埋點事件（行為資料，高量、半結構化、匿名為主），二是證券交易事件（業務資料，強一致、結構化、實名、受法遵約束）。

**範圍內**：行為事件與交易事件的採集、接入、標準化、ID 打通、儲存、分析查詢、標籤分群、對外輸出；資安與法遵設計；部署拓撲與路線圖。
**範圍外**：行銷自動化（MA）流程設計、推薦演算法、BI 報表美學、具體廠商採購評估。

---


> **勘誤（2026-10-15，以後續文件為準）**
> 1. **GA360 沒有雙寫**：行為事件的 GA 來源只有 GA360 BigQuery 匯出（日批 `events_` 為權威、串流 `events_intraday_` 提供分鐘級）；本文 §5 的「GTM Server-side 雙寫（路徑 B）」與 Collector `/ga4` 端點取消。即時（秒級）只適用於交易／帳務 CDC 事件。
> 2. **廣告平台不在範圍**：§受眾／再行銷相關的 GA4 受眾回流、Google Ads 不做；受眾推送只做「名單推送到內部系統」（docs/14）。
> 3. **OneID 歸戶邏輯改版**：§6 的「圖連通 + 合併 + 夜間改寫」改為對齊神策 IDM 3.0 的「接入時決定、一個 ID 只屬一人、不自動合併、首次關聯升級、改綁政策」，見 docs/19 與 `backend/oneid/attribution.py`。

## 1. 標竿調研：火山引擎與神策

### 1.1 火山引擎數智平台 VeDI

火山引擎（字節跳動旗下）把 ToB 資料產品包成「數智平台 VeDI」，其中與本題直接相關的是三個產品：

| 產品 | 定位 | 本設計對應模組 |
|---|---|---|
| **DataFinder 增長分析** | 使用者行為分析（事件、轉化、留存、路徑、歸因…） | 分析引擎、看板 |
| **VeCDP 客戶數據平台** | OneID、標籤、分群、洞察、營銷輸出 | OneID、標籤分群、即時規則引擎 |
| **DataTester A/B 測試** | 實驗平台 | 不在本期範圍 |
| **ByteHouse** | ClickHouse 路線的雲原生數倉，是 DataFinder 的數倉底座 | 儲存層選型依據 |

#### 1.1.1 DataFinder 增長分析

- **部署模式**：SaaS 與私有化兩種。私有化版本有獨立文件中心（增長分析（私有化）），OpenAPI 服務位址依部署環境而定。
- **分析模型**（官方稱 10+ 種）：
  - 事件分析：建構指標、分組篩選、多種圖表；內建「異常數據分析／異常檢測」。
  - 轉化分析（漏斗）：流程拆成步驟，以步驟轉化率衡量。
  - 留存分析：自訂起始事件與回訪事件；另有「基本留存」。
  - 分布分析：依計算結果切區間，看人數分布。
  - 路徑分析、間隔分析、Session 分析、用戶生命週期。
  - 歸因分析：線性、位置（首末各 40%、中間平分 20%）、時間衰減等模型。
  - 熱圖／圈選事件：屬於「全埋點」能力，需開啟全埋點。
  - 用戶細查：單一使用者的行為序列，支援屬性高亮、下載，結果可存成分群或匯出使用者 ID。
- **指標管理**：事件指標、事件組合指標、留存指標、Session 指標、轉化指標。
- **埋點**：程式碼埋點 + 全埋點（自動採集）+ 可視化圈選。
- **資料治理**：私有化版本提供「數據刪除」功能（可清理已匯入的歷史行為事件）；「單項目多應用」讓看板按專案粒度彙總。
- **數倉底座**：DataFinder 已整合進 ByteHouse（ClickHouse 技術路線、存算分離 MPP 架構）。

#### 1.1.2 VeCDP 客戶數據平台

- **資料整合**：宣稱支援 MySQL、廣告監測、串流等 20+ 種資料來源。
- **OneID（系統內名稱 Baseid）**：透過 ID-Mapping 產生，一個 ID 只對應一個 OneID。可配置 **ID 優先級**與 **ID 間參考關係**；演算法可選「增強分段」或「複用原有 ID」；支援**即時 OneID 生成**；私有化版本提供 ID-Mapping 運維（血緣管理、ID 映射查詢）。
- **多主體**：不只「人」，可建立人、物（商品／帳戶／門市等）多主體檔案並跨主體分析（單獨付費）。
- **標籤體系**：
  - 規則標籤：每個標籤值單獨定義規則。
  - SQL 標籤：以 SQL 計算結果作為標籤值，**僅私有化環境提供**。
  - 即時標籤：規則、生命週期、首末次、統計、排序、運算、SQL 等類型，需基於即時資料來源。
  - 多值標籤：匯入、人工、邏輯表達式、Hive SQL。
  - 模型標籤：K-means 聚類等，用於把既有人群再細分。
  - 標籤人數分布、行業標籤模板、標籤運維管理。
- **分群**：規則組合、上傳名單、分群分組、任務狀態監控；支援**即時分群**（人群隨上游即時資料更新）。
- **群體洞察**：自訂目標群體與分析維度，圖表對比群體差異。
- **即時規則引擎**：把標籤、行為、分群的變化以 **Kafka 訊息**推送給下游（單獨付費；目前不支援多值文本標籤）。
- **營銷應用**：人群包推送到廣告平台或火山引擎渠道。

### 1.2 神策數據

神策的產品矩陣：**神策分析（SA）** → **SDP 數據根基平台**（採集、治理、儲存、查詢、展示）→ **用戶畫像**（OneID + 標籤）→ **智能運營**（MA）→ **A/B 測試**、**智能推薦**。

#### 1.2.1 神策分析的分析模型與資料模型

- **資料模型**：Event–User–Item。所有分析（事件、留存、歸因、運營計畫）都建立在這個模型上。
- **資料協議**（JSON，一行一筆）：
  - `type`：`track`（事件）、`profile_set` / `profile_set_once` / `profile_increment` / `profile_append` / `profile_unset` / `profile_delete`（使用者屬性）、`item_set` / `item_delete`（物品）、`track_signup`（舊版關聯）、`track_id_bind` / `track_id_unbind`（IDM 3.0 綁定／解綁，解綁事件名 `$UnbindID`）。
  - 共用欄位：`distinct_id`、`login_id` / `anonymous_id`、`identities`、`event`、`time`（毫秒）、`project`、`properties`。
- **ID-Mapping 3.0（全域使用者關聯）**：以 `identities` 多鍵標識使用者。`$identity_login_id` 為系統內優先級最高、單值、不可修改、不可解綁；`$identity_mobile` 預設單值但可修改；另有 `$identity_cookie_id`、`$identity_android_id`、`$identity_idfv`、`$identity_anonymous_id` 等。SDK 提供 `bind(key, value)` / `unbind(key, value)`；登出時清除本機快取的 login_id 與業務 ID。
- **十大分析模型**：事件分析、漏斗分析、留存分析、分布分析、屬性分析、用戶路徑、間隔分析、歸因分析、LTV 分析、用戶行為序列（細查）；另有 Session 分析、自定義查詢（SQL）、看板、預警（指標告警）。
- **部署**：以私有化部署為主打（「本地部署的用戶行為分析平台」），並作為 PaaS 開放二次開發；**Kafka 訂閱即時資料僅私有化版本提供**。

#### 1.2.2 神策的技術架構（私有化）

```
SDK ──► Nginx ──► Extractor（解析/校驗/清洗，產生符合神策協議的資料）
                       │
                       ▼
                     Kafka ──► Data Loader ──► Kudu（即時寫入，最近資料）
                       │                          │ 定時轉存
                       │                          ▼
                       │                       Parquet（歷史，高速掃描）
                       │                          │
                       └──► 下游訂閱            Impala（MPP 查詢引擎，在 Kudu+Parquet 上建統一視圖）
```

設計取向：保存最細粒度原始事件、查詢時即時聚合，不預先定義指標維度（沒有採用 Kylin／Druid 那類預聚合方案）；以 user_id 雜湊抽樣與排序掃描實作漏斗。

#### 1.2.3 證券業經驗

- 神策公開案例：海通證券在合規前提下採私有化部署，整合多平台資料、打通業務資料與行為資料。
- 神策 2020 年文章提及服務過 7 家 TOP10 券商，主題為「客戶分級分類到全生命週期運營」。
- 金融業 OneID 的特殊性：一個客戶號對應多個資金帳號的**二級 ID 結構**，這一點與本設計的「多主體（客戶／帳戶）」直接相關。

### 1.3 功能對照表

| 能力 | 火山引擎（DataFinder + VeCDP） | 神策（SA + SDP + 畫像） | 本設計 DTP |
|---|---|---|---|
| 資料模型 | 事件 + 使用者 + 多主體 | Event–User–Item | Event–User–Item + 多主體（客戶/帳戶/營業員/商品） |
| 採集 | 程式碼埋點、全埋點、可視化圈選、SDK、OpenAPI、匯入 | 程式碼埋點、全埋點、可視化埋點、SDK、FormatImporter、Kafka | **GA360 匯入 + GTM Server-side 雙寫 + 神策相容 Collector API + CDC/Outbox 交易事件** |
| ID 打通 | OneID/Baseid、優先級+參考關係、即時生成、多主體 | IDM 3.0 identities、bind/unbind、login_id 最高優先 | 圖連通 OneID、優先級表、即時 + 批次校正、多主體 |
| 事件分析 | ✔ + 異常檢測 | ✔ | ✔ + 異常檢測 |
| 漏斗 / 留存 / 分布 / 路徑 / 間隔 / Session / 歸因 / LTV | ✔（歸因：線性/位置/時間衰減） | ✔（十大模型） | ✔（ClickHouse 原生 windowFunnel / retention / sequenceMatch） |
| 用戶細查（行為序列） | ✔ | ✔ | ✔（合規脫敏視圖） |
| 熱圖 | ✔（需全埋點） | ✔ | 二期（依 GA 全埋點） |
| 自定義 SQL | ✔（OpenAPI） | ✔ | ✔（Trino/ClickHouse SQL，列級權限） |
| 標籤 | 規則 / SQL（私有化）/ 即時 / 多值 / 模型（聚類） | 規則 / 統計 / SQL / 模型 | 規則 / 統計 / SQL / 即時 / 模型 |
| 分群 | 規則、上傳、即時分群、聚類細分 | 規則、上傳、行為序列 | 規則、上傳、即時、組合 |
| 群體洞察 / 畫像 | ✔ | ✔ | ✔ |
| 即時輸出 | 即時規則引擎 → Kafka | Kafka 訂閱（私有化）、智能運營觸發 | 即時規則引擎 → Kafka / Webhook / gRPC |
| 資料刪除（DSAR） | ✔（私有化） | ✔ | ✔（必備，個資法） |
| 數倉底座 | ByteHouse（ClickHouse 路線） | Kudu + Parquet + Impala | ClickHouse（熱）+ Iceberg on MinIO（冷） |
| 部署 | SaaS / 私有化 | 私有化為主 | **純私有化，K8s** |

### 1.4 對本設計的啟示

1. **資料模型要先定，分析模型是其結果。** 兩家都用 Event–User(–Item) 模型，而且都保存最細粒度事件、查詢時即時聚合。這是正確取向，本設計沿用。
2. **ID 打通是 CDP 的核心而不是附屬功能。** 火山引擎把 OneID 做成「優先級 + 參考關係 + 多主體」；神策把登入 ID 定為不可變的最高優先級錨點。證券業的「客戶 ↔ 多帳戶」二級結構必須在 ID 模型裡一開始就支援。
3. **私有化環境才允許的功能（SQL 標籤、Kafka 訂閱、資料刪除）正是證券業最需要的。** 本設計把它們當作一期必備。
4. **資料協議向神策對齊**。神策協議（track / profile_* / item_* / identities）是業界事實標準，GA4 事件結構也能無損映射。採用相容協議可降低未來更換商用產品或與 MA 整合的成本。
5. **「即時」要分層定義**：即時分群／即時規則引擎（秒級、給觸發用）與即時分析（分鐘級、給人看）是不同 SLA，用不同路徑實現。

---

## 2. 需求、限制與設計原則

### 2.1 資料來源

| 來源 | 性質 | 量級（假設） | 時效 |
|---|---|---|---|
| **GA360**（官網、交易 App、行動網站、理財 App） | 行為事件，匿名為主，`user_id` 僅登入後有 | 每日 5,000 萬至 3 億事件 | 日批（daily）/ 日鮮（fresh daily）/ 串流（streaming） |
| **交易事件**（委託、成交、改量、刪單、出入金、申購／贖回、開戶、信用／期權申請） | 業務事件，實名，強一致 | 每日 500 萬至 5,000 萬 | 秒級（CDC）或分鐘級（批次） |
| **客戶主檔 / 帳戶主檔**（CRM、核心帳務） | 維度資料 | 千萬級列 | 日批 + 變更即時 |
| **營業員 / 分公司 / 商品主檔** | 維度資料 | 萬級 | 日批 |
| **客服、推播、行銷活動回饋** | 事件 | 百萬級／日 | 分鐘級 |

### 2.2 法遵與資安限制（台灣證券業）

- **委外與雲端**：金融機構適用「金融機構作業委託他人處理內部作業制度及程序辦法」（2019 年底開放上雲），證券商有對應的「證券商作業委託他人處理內部作業制度及程序辦法」。使用雲端時客戶資料**儲存境內為原則**、傳輸與儲存須**加密或代碼化**並訂定**金鑰管理機制**，重大性或境外委外採核准制。本設計採純私有化，從源頭避開境外委外問題；但 **GA360 本身就是境外 SaaS**，故埋點層不得送出可識別個資（見 §5.1、§11）。
- **個人資料保護法**：蒐集目的特定、最小化、保存期限、當事人權利（查詢、刪除）、資安事故通報。
- **證券業內規**：交易紀錄保存年限、稽核軌跡、交易資料存取權限分級、營業員僅能看所屬客戶。

### 2.3 非功能需求

| 項目 | 目標 |
|---|---|
| 行為事件端到端延遲（Collector → 可查） | P95 < 60 秒 |
| 交易事件端到端延遲（CDC → 可查） | P95 < 10 秒 |
| 即時規則引擎觸發延遲 | P95 < 5 秒 |
| 查詢延遲（30 天、1 億事件、漏斗） | P95 < 10 秒 |
| 熱資料保留 | 13 個月（ClickHouse） |
| 冷資料保留 | 依法定年限（Iceberg on MinIO，加密） |
| 可用性 | 接入層 99.95%、查詢層 99.9% |
| 容量成長 | 三年 3 倍不改架構 |

### 2.4 設計原則

1. **一份事件、兩種來源、一個模型**：GA 行為與交易事件進同一張事件表，靠 `source` 與事件字典區分，而不是兩套倉。
2. **原始可回放**：所有入站事件原文落 Kafka + 物件儲存，Schema 變更可重算。
3. **ID 打通為一級公民**：OneID 服務獨立、有版本、可回溯、可拆分。
4. **協議相容**：對外 Collector API 與匯出格式相容神策協議，GA4 事件可雙向映射。
5. **個資預設脫敏**：原始識別欄位只存在 ID 服務的加密區，分析倉只有代碼化 ID。
6. **以開源組件為主、可被商用產品替換**：任何一層都能用 VeCDP／神策的對應產品替換而不影響其他層。

---

## 3. 整體架構

### 3.1 分層架構

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ 應用層    看板/自助分析 UI │ 標籤/分群工作台 │ 用戶細查 │ 營運觸發 │ AI Coach/對練 │ 外部 MA │
├──────────────────────────────────────────────────────────────────────────────────┤
│ 服務層    Analytics Query API │ Segment/Tag API │ Profile API │ OneID API │ Export/Subscribe │
│           (事件/漏斗/留存/路徑/歸因/LTV/Session 查詢編譯器 → SQL)                       │
├──────────────────────────────────────────────────────────────────────────────────┤
│ 計算層    Flink 串流（標準化、ID 解析、Session 化、即時規則引擎、即時標籤）              │
│           Spark/Trino 批次（日批重算、標籤計算、ID 合併校正、冷資料壓實）                │
│           排程：Airflow                                                               │
├──────────────────────────────────────────────────────────────────────────────────┤
│ 儲存層    Kafka（事件匯流排，7–30 天）                                                 │
│           ClickHouse（events / users / items / id_map / tag_bitmap，熱 13 個月）       │
│           Iceberg on MinIO（原始與歷史，冷，加密）                                     │
│           PostgreSQL（中繼資料、埋點 Schema、標籤定義、權限）  Redis（OneID 快取、計數器）│
├──────────────────────────────────────────────────────────────────────────────────┤
│ 接入層    GA360 Importer（BigQuery Export 拉取）│ GTM Server-side 雙寫 │ Collector API │
│           CDC（Debezium）/ Outbox（交易、帳務）│ 檔案/批次匯入（FormatImporter 相容）    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ 採集層    Web gtag/GTM │ App Firebase SDK │ GTM Server Container（境內）│ 交易主機/櫃台/API │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 資料流（即時路徑與批次路徑）

```
                     ┌────────────── 批次路徑（T+1，權威） ──────────────┐
GA360 ──► BigQuery ──► GA Importer ──► Kafka raw.ga ──┐                    │
         (events_/events_fresh_)                      │                    ▼
                                                      ├─► Flink 標準化 ─► Kafka dtp.events ─► ClickHouse events
GTM Server ──► Collector API ─────────► Kafka raw.ga ─┘        │                     │
   (境內, 即時)                                                 │                     ├─► Iceberg (raw + curated)
                                                               │                     │
交易主機/帳務 ──► Debezium CDC ──► Kafka raw.trade ──► Flink 事件化 ─┘                     │
   (Outbox 表)                                         │                               │
                                                       ▼                               ▼
                                            OneID Service (Flink + Redis + CH id_map)   標籤/分群 (Spark 日批 + Flink 即時)
                                                       │                               │
                                                       ▼                               ▼
                                            即時規則引擎 ─────────────────────► Kafka dtp.triggers ─► MA / 客服 / 營業員 App / AI Coach
```

### 3.3 技術選型與理由

| 層 | 選型 | 理由 | 可替換為 |
|---|---|---|---|
| 事件匯流排 | Apache Kafka 3.x（KRaft） | 業界標準；神策/火山都以 Kafka 作為訂閱介面 | Pulsar |
| 串流計算 | Apache Flink 1.19+ | Exactly-once、狀態後端、CEP（即時規則）、Debezium 連接器 | Spark Structured Streaming |
| 分析數倉 | ClickHouse 24.x | 內建 `windowFunnel`、`retention`、`sequenceMatch`、Bitmap；火山 ByteHouse 即同路線；單機即可跑 PoC | StarRocks / Apache Doris（若需多表 Join 強） |
| 冷層 / 湖 | Apache Iceberg on MinIO | 可回放、Schema 演進、時間旅行、支援刪除（DSAR） | HDFS + Hive |
| 批次 / Ad-hoc SQL | Spark 3.5 + Trino | 標籤日批、ID 合併校正、跨源查詢 | Impala |
| 中繼資料 / 控制面 | PostgreSQL 16 | 埋點 Schema、標籤定義、分群、權限、審計 | MySQL |
| 快取 | Redis 7（Cluster） | OneID 查找、即時計數、Dedup 短窗 | KeyDB |
| 排程 | Apache Airflow | 日批、依賴、回補 | DolphinScheduler |
| 編排 | Kubernetes（Operator：Strimzi、Flink K8s Operator、ClickHouse Operator） | 私有化標準化交付 | 裸機 Ansible |
| 觀測 | Prometheus + Grafana + Loki + OpenTelemetry | — | — |
| 密鑰 | HashiCorp Vault + HSM（金管會金鑰管理要求） | — | KMS 設備 |

---

## 4. 統一事件模型（Event–User–Item）

### 4.1 核心實體

| 實體 | 說明 | 主鍵 |
|---|---|---|
| **Event** | 誰（OneID）在何時（time）做了什麼（event）、帶什麼屬性（properties） | `event_id`（冪等鍵） |
| **User（客戶）** | OneID 對應的人；屬性來自 CRM 與 profile_* 指令 | `one_id` |
| **Item** | 非人主體：帳戶（account）、商品（instrument）、營業員（agent）、分公司（branch）、活動（campaign）、內容（content） | `(item_type, item_id)` |
| **Identity** | 各種原始識別子與 OneID 的對應 | `(id_type, id_value_hash)` |

### 4.2 標準事件 Schema（相容神策協議的超集）

```jsonc
{
  "type": "track",                       // track | profile_set | profile_set_once | profile_increment | profile_append | profile_unset | item_set | item_delete | track_id_bind | track_id_unbind
  "event": "order_submit",               // 事件字典登記名；GA 事件沿用 GA 名稱並加 ga. 前綴 (例 ga.page_view)
  "event_id": "sha256(source|src_event_id|…)",   // 冪等鍵
  "time": 1760400000123,                 // 事件發生時間 (ms, UTC)
  "_track_id": 123456789,
  "project": "sinopac_sec",
  "source": "ga360 | gtm_ss | trade_cdc | crm | mkt | collector",
  "distinct_id": "c_8f3a…",              // 送入時的主識別（代碼化）
  "identities": {                        // IDM 3.0 風格，多鍵
    "$identity_login_id": "cust_000123",         // 客戶 ID（代碼化後）
    "$identity_cookie_id": "GA1.2.1234.5678",    // GA client_id / user_pseudo_id
    "$identity_ga_user_id": "u_9a7b…",           // GA user_id（與 login_id 同源但保留原值）
    "$identity_device_id": "idfv/android_id",
    "$identity_mobile_hash": "hmac(mobile)",
    "$identity_account_no": ["9801234567", "9809876543"]   // 多值：一客多戶
  },
  "one_id": "oid_…",                     // 由 OneID Service 填入；入站時可為空
  "entity": { "type": "account", "id": "9801234567" },  // 多主體：事件掛在哪個主體下（預設 user）
  "properties": {
    "$lib": "gtm_ss", "$lib_version": "2.0",
    "$app_id": "tw.com.sinopac.trade", "$platform": "ios",
    "$session_id": "1760399000", "$session_number": 42,
    "$ip_masked": "203.0.113.0", "$geo_city": "Taipei",
    "$utm_source": "line", "$utm_medium": "push", "$utm_campaign": "q4_fund",
    "$screen_name": "OrderPage",
    // 事件專屬屬性 (由事件字典驗證)
    "symbol": "2330", "market": "TSE", "side": "buy", "order_type": "limit",
    "price": 1050.0, "qty": 1000, "amount": 1050000, "channel": "app",
    "order_no": "A2026101400012345"
  },
  "ingest_time": 1760400001456,
  "schema_version": 3
}
```

命名與型別規範：

- 事件名：`snake_case`，業務事件由事件字典核發；GA 事件保留原名加 `ga.` 前綴（`ga.page_view`、`ga.screen_view`、`ga.first_open`）。
- 屬性型別：`string | number | bool | datetime | list<string>`；`$` 前綴為系統預置，不可自訂。
- 金額一律 `number`（元，含小數），不以字串存；幣別獨立屬性 `currency`。
- 禁止欄位（入站校驗直接拒絕）：身分證號明文、手機明文、Email 明文、完整帳號明文、姓名、地址。

### 4.3 證券交易事件字典（摘要，完整見附錄 A）

| 事件 | 主體 | 關鍵屬性 | 來源 |
|---|---|---|---|
| `account_open_start` / `account_open_submit` / `account_open_approved` / `account_open_rejected` | user | `channel`, `account_types[]`, `kyc_level`, `reject_reason` | 開戶系統 CDC |
| `login` / `logout` | user | `channel`, `auth_method`, `$device_id` | App/網銀後台 |
| `quote_view` | user | `symbol`, `market`, `duration_ms` | GA/App |
| `watchlist_add` / `watchlist_remove` | user | `symbol` | App |
| `order_submit` | account | `symbol`, `side`, `order_type`, `price`, `qty`, `amount`, `channel`, `order_no` | 交易主機 CDC |
| `order_filled` / `order_partial_filled` | account | `order_no`, `filled_qty`, `filled_price`, `fee`, `tax` | 交易主機 CDC |
| `order_cancelled` / `order_rejected` | account | `order_no`, `reason_code` | 交易主機 CDC |
| `fund_deposit` / `fund_withdraw` | account | `amount`, `bank_code`, `method` | 帳務 CDC |
| `fund_subscribe` / `fund_redeem` | account | `fund_code`, `amount`, `plan_type`(單筆/定期定額) | 基金平台 CDC |
| `margin_apply` / `option_apply` | user | `product`, `status` | 業務系統 |
| `campaign_exposed` / `campaign_clicked` / `push_received` / `push_opened` | user | `campaign_id`, `creative_id` | MA / GA |
| `service_contact` | user | `channel`(客服/營業員/Line), `topic`, `agent_id` | CRM / 客服 |

### 4.4 ClickHouse 事件表

```sql
CREATE TABLE dtp.events ON CLUSTER dtp
(
    project          LowCardinality(String),
    event_date       Date            MATERIALIZED toDate(time, 'Asia/Taipei'),
    time             DateTime64(3, 'UTC'),
    event            LowCardinality(String),
    source           LowCardinality(String),
    one_id           String,                -- 查詢主鍵
    entity_type      LowCardinality(String),
    entity_id        String,
    distinct_id      String,
    session_id       String,
    event_id         String,                -- 冪等
    platform         LowCardinality(String),
    app_id           LowCardinality(String),
    -- 高頻查詢屬性物化為欄位
    symbol           LowCardinality(String),
    side             LowCardinality(String),
    channel          LowCardinality(String),
    amount           Nullable(Float64),
    utm_source       LowCardinality(String),
    utm_campaign     LowCardinality(String),
    -- 其餘屬性
    props            JSON,                  -- CH 24.x JSON 型別；舊版用 Map(String,String) + 型別分欄
    ingest_time      DateTime64(3, 'UTC'),
    schema_version   UInt16
)
ENGINE = ReplicatedReplacingMergeTree('/ch/{shard}/dtp.events', '{replica}', ingest_time)
PARTITION BY toYYYYMM(event_date)
ORDER BY (project, event_date, event, one_id, time)
TTL event_date + INTERVAL 13 MONTH TO VOLUME 'cold'
SETTINGS index_granularity = 8192;

-- 二級索引：依使用者查行為序列（用戶細查）
ALTER TABLE dtp.events ADD INDEX idx_one_id one_id TYPE bloom_filter(0.01) GRANULARITY 4;
ALTER TABLE dtp.events ADD INDEX idx_event_id event_id TYPE bloom_filter(0.001) GRANULARITY 1;
```

> `ReplacingMergeTree` 以 `event_id` 去重（最終一致）；查詢端以 `FINAL` 或 `argMax` 取最新。
> 使用者表 `dtp.users`、物品表 `dtp.items` 用 `ReplacingMergeTree` 以 `one_id` / `(item_type,item_id)` 排序，屬性為 JSON；標籤表 `dtp.user_tags` 為 `(tag_id, tag_value, version) → AggregatingMergeTree(groupBitmap(one_id_uint))`，分群即 Bitmap 交併差。

---

## 5. 資料接入設計

### 5.1 GA360 埋點事件

#### 5.1.1 三條可行路徑

| 路徑 | 機制 | 時效 | 完整性 | 個資風險 | 建議 |
|---|---|---|---|---|---|
| **A. BigQuery Export 拉取** | GA360 每日匯出 `events_YYYYMMDD`（360 每日上限 200 億事件）；360 專屬 **Fresh Daily**（`events_fresh_YYYYMMDD`，約清晨 5 點到齊，日內約每 60 分鐘增量）；**Streaming**（`events_intraday_YYYYMMDD`，分鐘級但無完整性 SLO，且缺新使用者／新 Session 的流量來源歸因） | T+1 / 小時 / 分鐘 | 日批權威；fresh 近似；streaming 可能缺漏 | 資料已在 Google 境外；拉回境內落地 | **必選**：日批為權威、Fresh 為日內 |
| **B. GTM Server-side Container 雙寫** | 自建 GTM 伺服器容器（境內 K8s），瀏覽器／App 的 gtag 先打到自家網域，再由伺服器容器一份轉送 GA4、一份以神策相容格式打到 DTP Collector | 秒級 | 與 GA 近乎 1:1（可少掉 GA 端的 bot 過濾與修改器） | 可在境內先脫敏再送 GA（**降低 GA 個資風險**） | **必選**：即時路徑 + 個資閘門 |
| **C. GA4 Measurement Protocol 反向補送** | 用於離線或伺服器端事件補送到 GA | — | — | — | 僅用於把 DTP 的交易事件回補到 GA 做統一看板（選配） |

採 **A + B 雙軌**：B 提供即時與個資控制，A 提供權威對帳。兩者事件以 `event_id` 去重（§5.1.3），日批到齊後以 A 覆蓋 B（`ReplacingMergeTree` 版本以 `source` 優先級決定：`ga360` > `gtm_ss`）。

#### 5.1.2 GA Importer（路徑 A）

```
Airflow DAG: ga360_daily
  1. 檢測 BigQuery dataset 是否出現 events_{ds}（daily）/ events_fresh_{ds}
  2. 以 BigQuery Storage Read API（Arrow）分片讀取 → 直接寫 Iceberg raw.ga_events（不經 GCS）
     - 連線僅允許由境內 Importer 出站至 bigquery.googleapis.com（防火牆白名單）
     - 服務帳戶最小權限（bigquery.readsessions.create + dataViewer）
  3. Spark 作業 UNNEST event_params / user_properties / items → 標準事件 → Kafka dtp.events (source=ga360)
  4. 對帳：以 event_name × 日 計數比對 GA 介面／BigQuery；差異 > 1% 告警
  5. 回補：fresh 版本與 daily 版本差異 → 以 daily 為準重寫當日分區
```

映射核心（完整見附錄 B）：

| GA360 欄位 | DTP 欄位 | 備註 |
|---|---|---|
| `event_name` | `event` = `ga.` + event_name | |
| `event_timestamp` (µs) | `time` (ms) | |
| `user_pseudo_id` | `identities.$identity_cookie_id` / `$identity_device_id` | Web → cookie_id；App → device_id（依 `platform`） |
| `user_id` | `identities.$identity_ga_user_id` → 解析為 `$identity_login_id` | GA 端 user_id 必須是**代碼化客戶 ID**（HMAC），不可為帳號/身分證 |
| `event_params[ga_session_id]` | `properties.$session_id` | |
| `event_params[*]` | `properties.*`（取 string/int/float/double 中非空者） | |
| `user_properties[*]` | `profile_set` 指令（另產一筆） | |
| `device.*`, `geo.*`, `app_info.*` | `$platform`, `$os`, `$device_model`, `$geo_*`, `$app_version` | IP 不匯出；GA geo 僅到城市 |
| `traffic_source.*`（首次）/ `session_traffic_source_last_click`（Session） | `$first_utm_*` / `$utm_*` | streaming 缺此欄 |
| `items[]` | `properties.items` (list) + `item_set`（商品主體） | |
| `event_bundle_sequence_id`, `stream_id`, `platform` | 去重鍵與來源 | |
| `privacy_info.*` | 同意狀態 → 不同意者**不打通 OneID** | 與 §11 同意管理對接 |

#### 5.1.3 去重與冪等

```
event_id = sha256(stream_id | user_pseudo_id | event_timestamp | event_name | event_bundle_sequence_id | engagement_time_msec?)
```

- 路徑 B 的 GTM 伺服器容器以同樣公式計算（gtag 端可取得 `client_id`、`event_timestamp`（注入）、事件名、批次序號），確保 A/B 同一事件得到同一 `event_id`。無法完全對齊時退而求其次：`(one_id, event, floor(time/1000))` 近似去重，並在對帳報表標示。
- Flink 端以 Redis 30 分鐘布隆過濾器做短窗去重；ClickHouse `ReplacingMergeTree` 做最終去重。

#### 5.1.4 遲到資料與時區

- Flink 以 `time` 為事件時間、允許 24 小時 watermark 延遲；超過者仍寫入但標 `late=true`，由日批重算當日聚合。
- 全平台以 UTC 儲存，`event_date` 以 `Asia/Taipei` 物化；GA 屬性時區設為 Asia/Taipei 以便對帳。

### 5.2 證券交易事件

#### 5.2.1 來源與接法

| 來源系統 | 接法 | 理由 |
|---|---|---|
| 交易主機／委託回報（通常 Oracle/DB2/Informix 或檔案） | **優先 Outbox 表 + Debezium CDC**；主機不可裝 CDC 時改為「回報檔 → 分鐘級 FormatImporter」 | 不改交易核心、不加交易延遲 |
| 帳務／出入金 | Debezium CDC（MySQL/Oracle/SQL Server 連接器） | |
| 開戶系統、信用／期權申請 | 業務系統在狀態變更時以 Collector API 直打（Server SDK）或 CDC | 狀態機事件化 |
| 基金／財管平台 | CDC 或 API | |
| CRM / 客服 | 日批 + Webhook | |

#### 5.2.2 事件化（CDC 列變更 → 業務事件）

交易資料是「狀態表」而非「事件」。Flink 作業以**狀態機**把 CDC 變更轉成事件：

```
委託表 (order) 列變更
  INSERT status=NEW            → order_submit
  UPDATE status NEW→PARTIAL    → order_partial_filled (filled_qty delta)
  UPDATE status →FILLED        → order_filled
  UPDATE status →CANCELLED     → order_cancelled
  UPDATE status →REJECTED      → order_rejected (reason_code)
  UPDATE qty/price (改量改價)   → order_amended
```

- 以 `(source_table, pk, lsn/scn)` 組成 `event_id`，保證重放冪等。
- `time` 取業務時間欄位（委託時間），非 CDC 擷取時間；`ingest_time` 另存。
- 事件掛主體 `entity = account`，同時帶 `identities.$identity_account_no` 與 `$identity_login_id`（客戶 ID），讓 OneID 服務建立「客戶 ↔ 帳戶」關係。

#### 5.2.3 敏感欄位處理（在 Flink 事件化階段完成，分析倉不落明文）

| 欄位 | 處理 |
|---|---|
| 身分證號 / 統編 | 不進事件；僅 ID 服務保存 `HMAC-SHA256(id_no, k_idm)` 用於打通 |
| 客戶 ID | `HMAC(cust_id, k_idm)` → `$identity_login_id`；反查僅在 ID 服務加密區 |
| 帳號 | 代碼化（格式保留加密 FPE 或 HMAC）；分析端只需區分帳戶 |
| 姓名、地址、電話、Email | 不進事件；畫像需要的（如年齡層、縣市）由 CRM 日批以**分桶後**值 `profile_set` |
| 金額、數量、商品 | 保留原值（分析必需）；但行級權限限制誰能看單筆 |
| IP | 去尾段後保存 `$ip_masked`，原 IP 不落地 |

### 5.3 Collector API（神策相容）

- `POST /sa?project=…`：相容神策 SDK 的 `data_list`（gzip + base64 JSON 陣列），讓既有神策／類神策 SDK 直接可用。
- `POST /v1/events`：原生 JSON 陣列，支援 `track`、`profile_*`、`item_*`、`track_id_bind/unbind`。
- `POST /ga4`：接受 GTM 伺服器容器轉送的 GA4 Measurement Protocol 格式，由 Collector 做映射。
- 校驗：事件是否已在事件字典登記（未登記進 `dtp.events_quarantine`，不丟棄）、屬性型別、禁止欄位、時間戳合理性（±7 天）、速率限制。
- 寫入 Kafka `raw.collector`（原文）與 `dtp.events`（標準化後），皆含 `trace_id`。

---

## 6. ID-Mapping 與 OneID

### 6.1 ID 類型與優先級

| 優先級 | ID 類型 | 單/多值 | 可變 | 來源 | 說明 |
|---|---|---|---|---|---|
| 1 | `$identity_login_id`（客戶 ID 代碼化） | 單值 | 不可 | CRM / 交易 | OneID 錨點；同神策 `$identity_login_id`、火山 Baseid 最高優先 |
| 2 | `$identity_idno_hash` | 單值 | 不可 | CRM | 只在 ID 服務加密區，不進事件 |
| 3 | `$identity_mobile_hash` | 單值 | 可 | CRM / 開戶 | 換號時重綁 |
| 4 | `$identity_account_no` | **多值** | 可 | 帳務 | 一客多戶（證券、信用、期貨、基金） |
| 5 | `$identity_ga_user_id` | 單值 | 不可 | GA | 與客戶 ID 同源（HMAC 相同金鑰），直接等價 |
| 6 | `$identity_device_id`（IDFV / Android ID） | 多值 | 可 | App | 共用裝置風險 → 合併受限（§6.3） |
| 7 | `$identity_cookie_id`（GA client_id） | 多值 | 可 | Web | 清 cookie 即新 ID |
| 8 | `$identity_anonymous_id` | 多值 | 可 | Collector | 兜底 |
| — | `$identity_line_uid`、`$identity_push_token` | 多值 | 可 | LINE / 推播 | 觸達渠道 ID，不參與人合併，只做輸出對應 |

### 6.2 演算法：圖連通 + 優先級裁決

```
節點 = (id_type, id_value_hash)；邊 = 同一事件/綁定指令中共現的 ID 對（帶 time、source、confidence）
OneID = 連通分量中優先級最高的節點決定（login_id → idno → mobile → …）
```

- **即時層（Flink）**：維持 `id → one_id` 狀態（RocksDB），新事件到來：
  1. 查所有 identities 是否已有 one_id；
  2. 全無 → 產新 `one_id`（ULID）；
  3. 僅一個 → 沿用；
  4. 多個不同 → 觸發**合併**（低優先級分量併入高優先級），寫 `id_merge_log`；
  5. 更新 Redis 快取，事件補上 `one_id` 後下發。
- **批次層（Spark，每日）**：全量重跑連通分量，與即時層結果比對，修正漂移；輸出 `id_map` 快照版本（可回溯任一天的對應）。
- **合併限制（防止錯併）**：
  - 僅 `device_id` / `cookie_id` 共現，且兩端都已各自綁定不同 `login_id` → **不合併**（共用裝置／公用電腦），改記為「關聯但不等價」。
  - 一個 `device_id` 綁定 > N（預設 5）個 `login_id` → 視為公共裝置，停止用它傳遞合併。
  - 合併只能由「強 ID 共現」（login/idno/mobile）或人工指令發動。
- **拆分**：提供 `unbind` 指令與人工拆分工具；拆分後以 `id_map` 版本重算受影響事件的 `one_id`（事件表 `one_id` 以 `ALTER … UPDATE` 批次修正，或查詢時 JOIN 最新 `id_map`；建議熱表物化、冷表查詢時 JOIN）。
- 對照：火山 VeCDP 的「ID 優先級 + 參考關係 + 增強分段／複用原有 ID」≈ 本設計的優先級表 + 合併限制 + ULID/沿用策略；神策 IDM 3.0 的「login_id 單值不可解綁」≈ 本設計優先級 1。

### 6.3 多主體（客戶 / 帳戶 / 營業員 / 分公司 / 商品）

```
customer (one_id) ──1:N── account (account_no) ──N:1── branch
     │                                              │
     └──N:1── agent (營業員) ──N:1─────────────────┘
instrument (symbol) ◄── order events
```

- 事件表 `entity_type/entity_id` 指出事件直接主體；`one_id` 永遠填客戶。
- `dtp.items` 保存各主體屬性（帳戶類型、開戶日、營業員、分公司、商品產業別…）。
- 跨主體分析（火山「多主體分析」）：例如「某營業員名下客戶的 App 活躍 vs 交易轉化」= 事件表以 agent_id 關聯 dim_agent 維度表後分組。

### 6.4 OneID API

| API | 用途 |
|---|---|
| `GET /oneid/resolve?type=&value=` | 查某 ID 的 one_id（Redis → CH） |
| `GET /oneid/{one_id}/identities` | 列出該人的所有 ID（脫敏，依權限） |
| `POST /oneid/bind` / `POST /oneid/unbind` | 綁定／解綁（審計） |
| `GET /oneid/{one_id}/history` | 合併／拆分歷史 |
| `POST /oneid/erase` | DSAR 刪除：解除所有 ID、標記事件待刪 |

---

## 7. 儲存與計算層

### 7.1 分層儲存

| 層 | 儲存 | 內容 | 保留 | 加密 |
|---|---|---|---|---|
| Bronze（原始） | Iceberg on MinIO | Kafka raw.* 原文（GA 原始列、CDC 原始列） | 法定年限 | MinIO SSE-KMS + 桶策略 |
| Silver（標準化） | ClickHouse（熱）+ Iceberg（全量） | `events`、`users`、`items`、`id_map` | CH 13 個月；Iceberg 法定年限 | CH 磁碟加密（LUKS）+ 欄位 HMAC |
| Gold（服務） | ClickHouse | 日聚合、Session 表、標籤 Bitmap、分群快照、LTV cohort | 依需求 | 同上 |
| 控制面 | PostgreSQL | 事件字典、標籤定義、分群定義、權限、審計 | 永久 | TDE |

### 7.2 ClickHouse 叢集規劃

- 2 分片 × 2 副本起步（4 節點），依 `cityHash64(one_id)` 分片 → 同一人事件落同一分片，漏斗／留存／路徑可在分片內完成、不需跨節點重分佈。
- 物化視圖：`events_daily_agg`（event × date × platform × utm 計數、去重人數 `uniqExact(one_id)` 以 `AggregatingMergeTree` 存 `uniqState`）、`sessions`（Session 化結果）、`user_first_last`（首末次事件）。
- 冷熱分層：13 個月後 `TTL … TO VOLUME 'cold'`（MinIO S3 Disk），查詢仍透明。

### 7.3 容量估算（假設每日 2 億 GA 事件 + 2,000 萬交易事件）

| 項目 | 估算 |
|---|---|
| 單事件標準化 JSON | ~1.2 KB；CH 壓縮後 ~120–180 B |
| 每日 CH 增量 | 2.2 億 × 150 B ≈ 33 GB（含副本 66 GB） |
| 13 個月熱資料 | ≈ 13 TB（含副本 26 TB） → 4 節點各 8 TB NVMe |
| Kafka（7 天，壓縮） | 2.2 億 × 7 × 0.5 KB ≈ 770 GB（3 副本 2.3 TB） |
| Iceberg 原始 + 標準化（5 年） | ≈ 60–80 TB（Parquet + ZSTD） |

### 7.4 計算作業清單

| 作業 | 引擎 | 頻率 | 說明 |
|---|---|---|---|
| `ga-normalize` | Flink | 串流 | GTM SS / Importer → 標準事件 |
| `trade-eventize` | Flink | 串流 | CDC → 業務事件（狀態機） |
| `oneid-resolve` | Flink | 串流 | 填 one_id、合併 |
| `sessionize` | Flink | 串流 | 30 分鐘閒置切 Session；GA 已有 session_id 則沿用 |
| `rt-rules` | Flink CEP | 串流 | 即時規則引擎 |
| `rt-tags` | Flink | 串流 | 即時標籤（首末次、計數、生命週期） |
| `ga-daily-import` | Airflow + Spark | 日 | 權威日批、對帳、覆蓋 |
| `oneid-recompute` | Spark | 日 | 全量連通分量、漂移修正、快照 |
| `tags-batch` | Spark/CH SQL | 日/時 | 規則、統計、SQL、模型標籤 |
| `segments-refresh` | CH SQL | 依分群設定 | Bitmap 重算 |
| `ltv-cohort` | CH SQL | 日 | LTV / cohort 表 |
| `dq-monitor` | Airflow | 時 | 事件量、空值率、延遲、對帳 |
| `dsar-erase` | Spark + CH mutation | 依需求 | 個資刪除 |

---

## 8. 分析能力（對標分析模型）

查詢服務把 UI 的「分析模型 + 指標 + 篩選 + 分組 + 時間」編譯為 ClickHouse SQL。以下列出每個模型的核心 SQL 模式與證券場景。

| 模型 | 對標 | ClickHouse 實作 | 證券場景範例 |
|---|---|---|---|
| 事件分析 | 火山 事件分析 / 神策 事件分析 | `count()`, `uniqExact(one_id)`, `sum(amount)` + `GROUP BY toStartOfInterval(time, …), dims` | 每日下單人數、成交金額、依渠道（App/Web/營業員）分組 |
| 轉化（漏斗） | 火山 轉化分析 / 神策 漏斗 | `windowFunnel(window)(time, cond1, cond2, …)`（可設 `strict_order`） | 開戶漏斗：`ga.page_view(開戶頁) → account_open_start → account_open_submit → account_open_approved → fund_deposit → order_filled`，視窗 30 天 |
| 留存 | 留存分析 / 基本留存 | `retention(cond_day0, cond_day1, …)` 或 cohort 自聯結 | 新開戶客戶 7/30/90 日交易留存；App 活躍留存 |
| 分布 | 分布分析 | `histogram` / 分桶 `GROUP BY intDiv(x, bucket)` | 月成交金額分布、下單次數分布 |
| 屬性分析 | 神策 屬性分析 | `users` 表 `GROUP BY` | 年齡層 × 持有商品類型 |
| 路徑 | 路徑分析 | `groupArray(event) ORDER BY time` + 視窗截斷；桑基圖 | 從 `quote_view` 到 `order_submit` 的前後 5 步 |
| 間隔 | 間隔分析 | `minIf(time, e=B) - minIf(time, e=A)` per user，再 quantiles | 開戶核准 → 首次入金 → 首筆成交 的時間間隔 |
| Session | Session 分析 | `sessions` 物化表 | 每 Session 看盤深度、跳出率 |
| 歸因 | 歸因分析（線性/位置/時間衰減/首末） | 以 `groupArray` 取轉化前觸點序列，於查詢層套權重 | 哪個行銷渠道帶來首筆入金 |
| LTV | 神策 LTV | cohort × 累積 `sum(fee + tax)` 或 `sum(amount)` | 不同開戶渠道客戶 180 日累積手續費 |
| 用戶細查 | 用戶細查 / 行為序列 | `WHERE one_id = ?` ORDER BY time；JOIN `id_map` 顯示 ID 脫敏 | 客訴調查、營業員複盤（限所屬客戶） |
| 異常檢測 | 火山 異常數據分析 | 日聚合表 + 季節性 STL / 3σ，寫告警 | 下單失敗率突升、某渠道事件量驟降（埋點掉線） |
| 自定義 SQL | 兩者皆有 | Trino/CH SQL 沙箱，列級遮罩、行級過濾、資源群組 | 分析師 Ad-hoc |

### 8.1 漏斗 SQL 範例（開戶 → 首筆成交）

```sql
SELECT level, count() AS users
FROM (
  SELECT one_id,
         windowFunnel(2592000)(time,                     -- 30 天視窗（秒）
           event = 'ga.page_view' AND props.page_group = 'account_open',
           event = 'account_open_submit',
           event = 'account_open_approved',
           event = 'fund_deposit',
           event = 'order_filled') AS level
  FROM dtp.events
  WHERE project = 'sinopac_sec' AND event_date BETWEEN '2026-09-01' AND '2026-10-14'
    AND event IN ('ga.page_view','account_open_submit','account_open_approved','fund_deposit','order_filled')
  GROUP BY one_id
)
GROUP BY level ORDER BY level;
```

### 8.2 跨來源才有價值的分析（本中台的核心賣點）

| 問題 | 需要的資料 |
|---|---|
| 看了個股頁但 30 分鐘內沒下單的人，是在哪一步離開？ | GA `quote_view` + 交易 `order_submit` |
| App 改版後，從看盤到成交的中位時間有沒有縮短？ | GA screen_view + `order_filled` 間隔 |
| 推播打開率高但不成交的客群長什麼樣？ | MA `push_opened` + 交易事件 + 畫像 |
| 哪些客戶 60 天無登入但仍有定期定額扣款？（沉睡但有價值） | GA/App `login` + `fund_subscribe` |
| 營業員名下客戶的數位自助率 | dim_agent 維度表 + `channel` 分組 |

---

## 9. 標籤、分群、畫像與即時輸出

### 9.1 標籤類型

| 類型 | 對標 | 計算方式 | 例 |
|---|---|---|---|
| 規則標籤 | 火山 規則標籤 / 神策 規則 | UI 規則 → CH SQL（日批或即時） | 「近 30 日成交 ≥ 5 筆」= 活躍交易者 |
| 統計標籤 | 火山 即時標籤（統計/首末次/排序） | 首次/末次/次數/總和/去重/排序 | 末次登入日、偏好市場（下單次數最高的市場） |
| SQL 標籤 | 火山 SQL 標籤（私有化限定）/ 神策 SQL | 分析師寫 SQL，輸出 `(one_id, value)` | 持股集中度、週轉率分級 |
| 生命週期標籤 | 火山 生命週期 | 狀態機（新客/成長/成熟/沉睡/流失） | 依最近交易與登入間隔 |
| 模型標籤 | 火山 模型標籤（K-means）/ 神策 模型 | Spark MLlib / 外部模型寫回 | 流失風險分、商品偏好聚類、風險屬性預測 |
| 即時標籤 | 火山 即時標籤 | Flink 狀態 | 「過去 10 分鐘看了 ≥ 3 檔個股」 |
| 匯入標籤 | 多值標籤（匯入） | CSV / API | 行銷名單、VIP 等級 |

標籤定義含：名稱、分類、值域、更新頻率、負責人、資料來源（血緣）、權限等級；每次計算產生版本，Bitmap 存 CH `user_tags`。

### 9.2 分群

- 規則分群：使用者屬性 + 標籤 + 行為（事件 × 時間窗 × 次數 × 屬性篩選）+ 行為序列（做過 A 後 N 天內做過 B）。
- 上傳分群、組合分群（交／併／差）、即時分群（Flink 維護、人群隨事件進出）。
- 分群快照（每日 Bitmap）供留存/對比；分群可一鍵送入分析模型作對比（火山「群體洞察」）。
- 輸出：Kafka、CSV（加密）、API 拉取、推送到 MA／客服／營業員 App。

### 9.3 畫像（群體洞察）

- 單人畫像：屬性 + 標籤 + 最近行為摘要 + 持有商品 + 帳戶（依權限脫敏）。
- 群體洞察：目標群 vs 對照群在各維度的佔比差異（TGI）。

### 9.4 即時規則引擎

對標火山 VeCDP「實時規則引擎」與神策「智能運營」觸發。Flink CEP 規則 DSL：

```yaml
rule: cart_abandon_order
when:
  - event: order_submit            # 下單
  - not:
      event: order_filled          # 未成交
      within: 30m
      match: order_no
audience: segment:active_app_users
throttle: 1 per user per 24h
action:
  topic: dtp.triggers
  payload: { one_id, order_no, symbol, channel }
```

輸出 Kafka `dtp.triggers`（Avro，含 `one_id`、觸達 ID 由下游以 OneID API 換取），由 MA、客服工單、營業員 App、AI Coach 訂閱。

---

## 10. 埋點治理、資料品質與血緣

- **事件字典（Schema Registry）**：PostgreSQL 保存事件 / 屬性定義、型別、版本、負責人、上線狀態；提供 UI 與 API。GA 事件由 Importer 掃描 `events_` 自動建議登記，未登記事件進 quarantine 表每日報表。
- **埋點驗證**：Collector 與 Importer 共用校驗器；測試環境提供「即時埋點除錯視圖」（對標神策 Debug 模式 / 火山埋點驗證）。
- **資料品質監控**：事件量日環比、空值率、型別錯誤率、延遲 P95、GA 對帳差異、CDC lag、OneID 合併率異常，Grafana + 告警。
- **血緣**：來源表 → Kafka topic → Flink job → CH 表 → 標籤 → 分群 → 輸出，以 OpenLineage 事件寫入 Marquez（或簡化為 PostgreSQL 表）。
- **變更管理**：事件字典變更走審核；破壞性變更要求 `schema_version` 升版並由批次回填。

---

## 11. 資安、個資與法遵

### 11.1 資料分級與落地原則

| 分級 | 範例 | 落地位置 |
|---|---|---|
| L4 可直接識別 | 身分證號、姓名、手機、帳號明文 | 只在 CRM / 帳務；DTP 僅 ID 服務保存 HMAC（金鑰在 HSM） |
| L3 代碼化識別 | HMAC 客戶 ID、FPE 帳號 | 事件表、ID 表 |
| L2 敏感業務 | 單筆委託金額、持股 | 事件表，行級權限 |
| L1 行為 | 頁面瀏覽、點擊 | 事件表 |
| L0 聚合 | 看板數字 | Gold 層 |

### 11.2 GA360 側的個資閘門（因 GA 為境外 SaaS）

1. 所有 gtag / Firebase 流量先經境內 GTM 伺服器容器；容器內**移除** URL 查詢字串中的個資、表單值、Email；IP 匿名化。
2. GA `user_id` 僅能放 **HMAC 代碼**，且金鑰與 DTP 的 `$identity_login_id` 金鑰相同，GA 端無法反推。
3. 不開啟 Google Signals 與廣告個人化；關閉 GA 的細緻位置。
4. 同意管理（Consent Mode）：未同意者在 DTP 僅保存匿名事件，不參與 OneID 合併（`privacy_info` 映射）。
5. 法遵文件：將 GA360 列為資訊作業委外／境外服務，依證券商委外辦法辦理（保留評估與備查紀錄）。

### 11.3 平台內安全

- **加密**：傳輸 mTLS；靜態 MinIO SSE-KMS、ClickHouse LUKS 磁碟加密、PostgreSQL TDE；欄位級 HMAC/FPE 金鑰由 Vault + HSM 管理，定期輪替並保留版本。
- **存取控制**：SSO（AD/OIDC）+ RBAC；列級遮罩（分析師看不到帳號尾碼）；行級過濾（營業員僅見所屬客戶；分公司主管僅見分公司）；資料匯出需審批並加浮水印。
- **審計**：所有查詢、匯出、細查、OneID 反查、標籤修改寫入不可竄改審計日誌（WORM 儲存），保存依法定年限。
- **DSAR / 刪除**：`POST /oneid/erase` → 解除 ID → Iceberg 以 row-level delete 刪原始 → ClickHouse `ALTER DELETE` → Kafka 以 tombstone + 短保留自然過期 → 產出刪除證明。
- **網路**：DTP 位於內網資料區；僅 GA Importer 與 GTM 伺服器容器所在 DMZ 可出站／入站，白名單到 FQDN。
- **保存期限**：行為事件 13 個月熱 + 冷層至法定年限；交易事件依證券法規保存；超期自動 TTL。

---

## 12. 私有化部署架構

### 12.1 拓撲（中型規模，每日 2–3 億事件）

```
DMZ (K8s node pool: edge)
  ├─ GTM Server Container ×3      ├─ Collector API ×3 (HPA)      ├─ GA Importer (Airflow worker, 出站白名單)
內網資料區 (K8s node pool: data)
  ├─ Kafka ×3 (KRaft, 各 2 TB NVMe)
  ├─ Flink JobManager ×2 (HA) + TaskManager ×6 (各 16 vCPU/64 GB)
  ├─ ClickHouse 2 shard × 2 replica (各 32 vCPU/256 GB/8 TB NVMe) + ClickHouse Keeper ×3
  ├─ MinIO ×4 (各 40 TB HDD, EC 2+2)
  ├─ Spark on K8s (彈性) / Trino ×3
  ├─ PostgreSQL ×2 (Patroni HA) / Redis Cluster ×6
  ├─ Airflow (scheduler ×2, webserver ×2)
  └─ Vault ×3 (+HSM) / Prometheus / Grafana / Loki / Marquez
應用區 (K8s node pool: app)
  ├─ Analytics API ×3 / Segment API ×2 / OneID API ×3 / Web UI ×2
```

| 規模 | 每日事件 | ClickHouse | Flink TM | Kafka | 總 vCPU / RAM（約） |
|---|---|---|---|---|---|
| PoC | < 2,000 萬 | 1 節點 | 2 | 1 | 48 / 256 GB |
| 小 | < 1 億 | 1×2 | 4 | 3 | 160 / 1 TB |
| 中 | < 5 億 | 2×2 | 6–8 | 3 | 320 / 2 TB |
| 大 | < 20 億 | 4×2 | 12+ | 5 | 640+ / 4 TB |

### 12.2 高可用與備援

- Kafka 3 副本、`min.insync.replicas=2`；Flink Checkpoint 至 MinIO、Exactly-once sink。
- ClickHouse 副本 + Keeper 3 節點；每日 `BACKUP … TO S3`；Iceberg 本身可重建 CH。
- PostgreSQL 同步複寫 + 每日邏輯備份；Vault Raft 3 節點 + 離線解封金鑰保管。
- 異地：MinIO 跨站複寫到 DR 機房（加密），CH 可由 Iceberg 重建（RPO 1 天、RTO 8 小時）；若需 RTO < 1 小時，DR 站常駐 CH 1×2 熱待命。

### 12.3 交付方式

- Helm Chart（umbrella）+ Operators；離線映像倉；Ansible 準備裸機與 K8s。
- 環境：dev / staging / prod 三套，staging 接 GA 測試屬性與交易模擬器（交易事件以回放 CDC 檔案產生）。

---

## 13. 實施路線圖

| 階段 | 期程 | 交付 | 驗收指標 |
|---|---|---|---|
| **Phase 0 基礎與 PoC** | 1–2 個月 | K8s + Kafka + CH 單節點；GA360 日批 Importer；事件字典 v1；10 個核心看板 | GA 對帳差異 < 1%；30 天漏斗查詢 < 10 秒 |
| **Phase 1 雙軌接入 + OneID** | 2–3 個月 | GTM 伺服器容器雙寫；交易 CDC（委託/成交/出入金）事件化；OneID 服務（即時 + 日批）；用戶細查；RBAC/審計 | 行為事件 P95 < 60 秒；交易事件 P95 < 10 秒；OneID 錯併率 < 0.1%（抽樣人工驗證） |
| **Phase 2 分析模型全集 + 標籤分群** | 2–3 個月 | 漏斗/留存/路徑/間隔/歸因/LTV/Session/分布/屬性；標籤（規則/統計/SQL/生命週期）；分群 + Bitmap；群體洞察；Ad-hoc SQL | 10 大模型覆蓋；標籤日批 < 2 小時；分群 1,000 萬人 < 30 秒 |
| **Phase 3 即時與輸出** | 2 個月 | 即時規則引擎、即時標籤/分群、Kafka/Webhook 輸出、MA/客服/營業員 App/AI Coach 對接；異常檢測；DSAR 流程 | 觸發 P95 < 5 秒；DSAR 24 小時內完成 |
| **Phase 4 進階** | 持續 | 模型標籤（流失/偏好）；熱圖；A/B 實驗分流對接；多主體分析 UI；DR 站 | — |

---

## 14. 與 AI 對練 / AI Coach 的銜接

本倉（永豐 AI 語音對練系統）與 `sinopac-ai-drill` 已有「客戶畫像」「洞察」「對練統計」等模組。DTP 可供應：

| AI Coach 需求 | DTP 提供 | 介面 |
|---|---|---|
| 對練場景中的「客戶畫像」要貼近真實客群 | 群體洞察 + 標籤分布（去識別化、聚合） | `GET /segments/{id}/insight` |
| 營業員在對練前查看所屬客戶的近期數位行為摘要 | 用戶細查（行級權限：僅所屬客戶）、生命週期標籤 | `GET /profile/{one_id}/summary` |
| 即時提示：客戶剛看了某檔個股／推播已開啟但未下單 | 即時規則引擎 → `dtp.triggers` | Kafka / Webhook |
| 對練成效回饋：對練後所屬客戶的轉化是否提升 | 把對練完成事件以 `track` 寫回 DTP（`coach_session_completed`，主體 agent），再用漏斗／留存比較 | Collector API |
| 情境腳本生成（LLM）需要真實但脫敏的行為序列 | 用戶細查匯出（脫敏、聚合、抽樣） | 審批後匯出 |

---

## 15. 風險與待決事項

| # | 風險 / 待決 | 影響 | 建議 |
|---|---|---|---|
| 1 | GA360 為境外 SaaS，法遵認定（委外 / 境外）需法遵部門確認 | 高 | Phase 0 即啟動評估；GTM 伺服器容器 + HMAC user_id 作為降險方案 |
| 2 | 交易主機是否允許 CDC／Outbox；否則退回分鐘級檔案 | 中 | 與交易系統廠商確認；設計已支援兩種 |
| 3 | 路徑 A/B 事件 `event_id` 對齊精度 | 中 | PoC 驗證；備用近似去重 + 對帳報表 |
| 4 | OneID 錯併（共用裝置、家庭帳戶） | 高 | 合併限制規則 + 人工拆分 + 抽樣稽核 |
| 5 | ClickHouse JSON 型別成熟度（24.x） | 低 | 高頻屬性物化為欄位；不足時退回 Map + 分型別欄位 |
| 6 | 自建 vs 採購（VeCDP / 神策）TCO | 決策 | 本設計各層可被商用替換；建議以 Phase 0–1 自建接入層與 OneID，分析 UI 可評估採購 |
| 7 | 營業員行級權限與客戶重分配歷史 | 中 | dim_agent 維度表版本化，查詢以「當前歸屬」或「事件當下歸屬」可選 |
| 8 | 資料保存年限與 DSAR 衝突（法定保存 vs 刪除權） | 中 | 法遵定義「法定保存例外」，DSAR 僅刪行為資料、交易資料去識別化保留 |

---

## 附錄 A：證券事件字典（初版）

| 事件 | 中文 | 主體 | 必填屬性 | 選填屬性 | 來源 |
|---|---|---|---|---|---|
| `ga.page_view` | 頁面瀏覽 | user | `$page_url`, `$page_title`, `page_group` | `$referrer`, `$utm_*` | GA |
| `ga.screen_view` | 畫面瀏覽 | user | `$screen_name`, `$app_version` | | GA |
| `ga.first_open` / `ga.session_start` / `ga.user_engagement` | GA 預置 | user | | | GA |
| `quote_view` | 看盤 | user | `symbol`, `market` | `duration_ms`, `from_screen` | GA 自訂事件 |
| `search_symbol` | 搜尋商品 | user | `keyword` | `result_count` | GA |
| `watchlist_add` / `watchlist_remove` | 自選股 | user | `symbol` | | GA |
| `login` / `logout` | 登入/登出 | user | `channel`, `auth_method` | `$device_id` | App 後台 |
| `account_open_start` | 開戶開始 | user | `channel` | `$utm_*` | 開戶系統 |
| `account_open_submit` | 開戶送件 | user | `channel`, `account_types` | `kyc_level` | 開戶系統 |
| `account_open_approved` | 開戶核准 | user | `account_no`(代碼化), `account_type`, `branch_id`, `agent_id` | | 開戶系統 |
| `account_open_rejected` | 開戶退件 | user | `reject_reason` | | 開戶系統 |
| `order_submit` | 委託 | account | `order_no`, `symbol`, `market`, `side`, `order_type`, `price`, `qty`, `amount`, `channel` | `trade_kind`(現股/融資/融券/當沖), `time_in_force` | 交易主機 |
| `order_amended` | 改量改價 | account | `order_no`, `new_price`, `new_qty` | | 交易主機 |
| `order_cancelled` | 刪單 | account | `order_no`, `cancel_qty` | `by`(user/system) | 交易主機 |
| `order_rejected` | 委託失敗 | account | `order_no`, `reason_code` | `reason_text`(去個資) | 交易主機 |
| `order_partial_filled` / `order_filled` | 成交 | account | `order_no`, `filled_qty`, `filled_price`, `filled_amount`, `fee`, `tax` | | 交易主機 |
| `fund_deposit` / `fund_withdraw` | 出入金 | account | `amount`, `method` | `bank_code` | 帳務 |
| `fund_subscribe` / `fund_redeem` | 基金申贖 | account | `fund_code`, `amount`, `plan_type` | | 基金平台 |
| `margin_apply` / `option_apply` / `sub_brokerage_apply` | 業務申請 | user | `product`, `status` | | 業務系統 |
| `campaign_exposed` / `campaign_clicked` | 活動曝光/點擊 | user | `campaign_id` | `creative_id`, `placement` | GA / MA |
| `push_received` / `push_opened` | 推播 | user | `campaign_id`, `push_id` | | MA |
| `service_contact` | 服務接觸 | user | `channel`, `topic` | `agent_id`, `duration_s` | CRM / 客服 |
| `coach_session_completed` | AI 對練完成 | agent | `scenario_id`, `score` | | AI Coach |

## 附錄 B：GA360 → DTP 欄位映射表

| GA360 BigQuery 欄位 | 型別 | DTP 目標 | 轉換 |
|---|---|---|---|
| `event_date` | STRING | `event_date` | 僅對帳用，實際由 `time` 物化 |
| `event_timestamp` | INT64 (µs) | `time` | `/1000` |
| `event_previous_timestamp` | INT64 | `props.$prev_event_time` | |
| `event_name` | STRING | `event` | `'ga.' + event_name`（自訂事件若已登記則去前綴） |
| `event_params[].key / value.{string,int,float,double}_value` | REPEATED | `props.<key>` | 取非 NULL 者；`ga_session_id`→`$session_id`，`ga_session_number`→`$session_number`，`page_location`→`$page_url`，`page_referrer`→`$referrer`，`engagement_time_msec`→`$engagement_ms` |
| `event_bundle_sequence_id` | INT64 | 去重鍵 | |
| `event_server_timestamp_offset` | INT64 | `props.$server_offset` | |
| `user_id` | STRING | `identities.$identity_ga_user_id` | 需為 HMAC 代碼；解析為 `$identity_login_id` |
| `user_pseudo_id` | STRING | `identities.$identity_cookie_id`（web）/ `$identity_device_id`（app） | 依 `platform` |
| `user_properties[]` | REPEATED | `profile_set` 指令 | 另產一筆 |
| `user_first_touch_timestamp` | INT64 | `profile_set_once.$first_visit_time` | |
| `user_ltv.{revenue,currency}` | RECORD | 忽略 | 以交易事件計算 |
| `device.{category,mobile_brand_name,mobile_model_name,operating_system,operating_system_version,language,web_info.browser}` | RECORD | `$device_type`, `$brand`, `$model`, `$os`, `$os_version`, `$language`, `$browser` | |
| `device.advertising_id` | STRING | 不落地 | 個資閘門 |
| `geo.{country,region,city}` | RECORD | `$geo_country`, `$geo_region`, `$geo_city` | |
| `app_info.{id,version,install_source}` | RECORD | `$app_id`, `$app_version`, `$install_source` | |
| `traffic_source.{name,medium,source}` | RECORD | `$first_utm_campaign`, `$first_utm_medium`, `$first_utm_source` | 使用者首次來源 |
| `session_traffic_source_last_click.*` | RECORD | `$utm_*`（Session 層） | streaming 缺此欄，日批覆蓋 |
| `collected_traffic_source.*` | RECORD | `props.$collected_utm_*` | 事件層原始 UTM |
| `stream_id`, `platform` | STRING | `props.$stream_id`, `platform` | |
| `ecommerce.*`, `items[]` | RECORD/REPEATED | `props.items`, `props.transaction_id`… | 證券較少用；財管商品頁可用 |
| `privacy_info.{analytics_storage,ads_storage,uses_transient_token}` | RECORD | 同意旗標 | 未同意 → 不合併 OneID |
| `is_active_user` | BOOL | `props.$is_active_user` | |
| `batch_event_index`, `batch_page_id`, `batch_ordering_id` | INT64 | 去重輔助 | |
| `users_YYYYMMDD` / `pseudonymous_users_YYYYMMDD` | 表 | `profile_set` 日批 | 使用者層最新狀態、受眾歸屬 |

## 附錄 C：參考來源

**火山引擎**
- [客户数据平台 VeCDP 產品頁](https://www.volcengine.com/product/veCDP)
- [客户数据平台（私有化）产品介绍](https://docs.volcengine.com/docs/7139/191640)
- [ID-Mapping 配置逻辑（私有化）](https://docs.volcengine.com/docs/CustomerDataPlatformprivatization/ID-Mappingconfigurationlogic?lang=zh)
- [ID-Mapping 运维与管理（私有化）](https://www.volcengine.com/docs/7139/1222703)
- [常用名词（Baseid/OneID）](https://www.volcengine.com/docs/6356/75798)
- [规则标签（私有化）](https://www.volcengine.com/docs/7139/191743)、[SQL 标签（私有化）](https://docs.volcengine.com/docs/7139/191754)、[实时标签构建（私有化）](https://www.volcengine.com/docs/7139/1162990)、[标签常见问题](https://www.volcengine.com/docs/7139/191845?lang=zh)
- [新用户学习路径（私有化）](https://www.volcengine.com/docs/7139/1131847)
- [VeCDP 发版日志 2023 年 9 月（实时规则引擎）](https://www.volcengine.com/docs/6356/1148753)
- [增长分析 DataFinder 產品頁](https://www.volcengine.com/product/datafinder)
- [增长分析（私有化）产品简介和优势](https://www.volcengine.com/docs/6285/1219233)、[OpenAPI 概览](https://docs.volcengine.com/docs/6285/100050)、[专有名词](https://docs.volcengine.com/docs/Growthanalysisprivatization/ExplanationofDataFinderproprietaryterms?lang=zh)
- [DataFinder 转化分析](https://docs.volcengine.com/docs/84129/1261541)、[留存分析](https://docs.volcengine.com/docs/84129/1261540)、[分布分析](https://volcengine.com/docs/84129/1261543)、[用户生命周期](https://www.volcengine.com/docs/84129/1261547)、[埋点、全埋点](https://volcengine.com/docs/84129/1261517)、[指标管理](https://docs.volcengine.com/docs/GrowthAnalysisDataFinder/Metricmanagement-3?lang=zh)、[SaaS 发版日志 2025](https://docs.volcengine.com/docs/GrowthAnalysisDataFinder/SaaS-ReleaseLog2025?lang=zh)
- [VeDI 增长营销季刊 VOL.05](https://developer.volcengine.com/articles/7221039238916079671)、[VOL.06](https://developer.volcengine.com/articles/7258155356163932214)、[VOL.07](https://developer.volcengine.com/articles/7293089661453828134)
- [ByteHouse 技术白皮书](https://developer.volcengine.com/articles/7221794260490813501)、[ByteHouse 关键技术及最佳实践](https://developer.volcengine.com/articles/7418056624952377363)

**神策數據**
- [神策分析產品頁（本地部署）](https://www.sensorsdata.cn/product/analysis-zh-t.html)
- [神策數據根基平台 SDP](https://www.sensorsdata.cn/features/bigdata.html)、[CDP 解決方案](https://www.sensorsdata.cn/solutions/cdp.html)、[用戶畫像](https://www.sensorsdata.cn/product/userTag.html)
- [數據格式（track / profile_*）](https://manual.sensorsdata.cn/sa/docs/tech_import_format)、[預置用戶標識清單（IDM 3.0）](https://manual.sensorsdata.cn/sa/docs/preset_identity_list/v0205)、[標識用戶——全域用戶關聯](https://manual.sensorsdata.cn/sa/3.0/zh_cn/tech_knowledge_user_idm3-150667578.html)、[全域用戶關聯（Web）](https://manual.sensorsdata.cn/sa/3.0/zh_cn/tech_sdk_client_web_idm3-109576372.html)
- [訂閱 Kafka 實時數據（私有化）](https://manual.sensorsdata.cn/sa/docs/tech_export_subscribe/v0204)
- [基於 Impala 構建實時用戶行為分析引擎](https://sensorsdata.cn/uploads/2bd6708aa9e7d3fe7db04ce608907856/%E7%A5%9E%E7%AD%96%E6%95%B0%E6%8D%AE__%E5%9F%BA%E4%BA%8EImpala%E6%9E%84%E5%BB%BA%E5%AE%9E%E6%97%B6%E7%94%A8%E6%88%B7%E8%A1%8C%E4%B8%BA%E5%88%86%E6%9E%90%E5%BC%95%E6%93%8E.pdf)、[Impala 在神策實時分析引擎中的落地與優化](https://blog.csdn.net/rlnLo2pNEfx9c/article/details/124310846)、[神策分析的技術選型與架構實現](https://blog.csdn.net/weixin_33755847/article/details/90387637)
- [證券客戶生命週期運營方案全解析（2020）](https://news.pedaily.cn/20200415/1253.shtml)
- [神策大數據：用戶識別（OneID 二級 ID 結構）](https://cloud.tencent.com/developer/article/1794950)

**GA360 / BigQuery**
- [[GA4] BigQuery Export（官方說明）](https://support.google.com/analytics/answer/9358801?hl=en)
- [A practical guide to the GA4 BigQuery export schema](https://piotrlitwa.com/articles/en/ga4-bigquery-export-schema.html)
- [GA4 BigQuery Tips: Event Parameters and Other Repeated Fields](https://adswerve.com/blog/ga4-bigquery-tips-event-parameters-and-other-repeated-fields-part-two/)
- [Merkle: GA4 × BigQuery Daily Fresh](https://www.merkle.com/en/merkle-now/articles-blogs/2024/merkle---ga4-x-bigquery---daily-fresh.html)

**台灣法遵**
- [iThome：金管會開放金融業上雲（委外辦法修正）](https://www.ithome.com.tw/news/133098)
- [iThome：金融業上雲規範放寬](https://www.ithome.com.tw/news/155839)
- [證交所：證券商作業委託他人處理內部作業制度及程序辦法（草案說明）](https://www.twse.com.tw/market_insights/zh/detail/ff8080818d397607018df2a0ab4801be)
