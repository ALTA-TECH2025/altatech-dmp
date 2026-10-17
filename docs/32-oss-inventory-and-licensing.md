# 第三方開源元件清單：來源、授權與是否需商用付費（給客戶的版本）

> 版本 v1.0（2026-10-17）。回答客戶兩個問題：**每個元件從哪裡來（治理單位、所在國、起源）**、**商用要不要付費**。授權白名單與 CI 規則在 `LICENSES.md`，原產地逐項評估在 docs/25，風險評分在 docs/11；本文把三者合成一張客戶看得懂的表，並區分「第一階段實際裝進系統的」與「規劃中、尚未引入的」。
> 口徑：「商用付費」指**在客戶自己的環境部署、內部使用**是否需要向廠商付授權費。**全部核心元件都不需要**；表中「可選付費」是廠商另外賣的託管／企業版或支援合約，不買也能用。

## 0. 結論

| 問題 | 回答 |
|---|---|
| 有沒有必須付費的軟體授權？ | **沒有。** 第一階段所有元件都是 OSI 認可的開源授權（Apache-2.0、MIT、BSD、PostgreSQL、MPL-2.0、ISC）或免費再散布的基底映像（Red Hat UBI）。唯一的 copyleft 元件是 Grafana OSS（AGPL-3.0），只做內部看板、不修改、不對外提供，不觸發散布義務（法務已核可，見 docs/07 §3）。 |
| 哪些是「可選付費」？ | 雲端託管或企業版：GCP（GKE、Cloud SQL、GCS、Managed Prometheus，依用量計費，機房部署不需要）、ClickHouse Cloud、Grafana Cloud／Enterprise、Confluent、Astronomer、Rancher Prime、Red Hat 訂閱（RHEL 節點 OS；用 Rocky Linux 則免費）。第一階段的設計都不依賴這些。 |
| 有沒有中國大陸原產的元件？ | **沒有。** 曾評估的 Apache SeaTunnel、Flink CDC（阿里巴巴起源）、Apache ECharts（百度起源）、DolphinScheduler 都已排除或未採用（docs/25）。需要揭露的兩項：ClickHouse 起源於俄羅斯 Yandex（2021 起由美國公司 ClickHouse Inc. 治理，Apache-2.0）；Vite／Vitest（VoidZero Inc.，美國公司）與 SeaweedFS（SeaweedFS Inc.，美國公司）的主要作者為大陸出生。 |
| 客戶要付的只有什麼？ | 基礎設施（機房硬體或 GCP 用量）、GA360（Google 商業產品，客戶既有）、LLM API（第一階段 AI 閘道關閉，不需要）、人力。另有兩個外部服務依用量：告警通知若用 LINE 官方帳號，Messaging API 免費方案每月 200 則推送、超過要買訊息方案（Email 走客戶既有 SMTP，免費）；GA4 BigQuery 匯出的 BigQuery 儲存與查詢費。 |

## 1. 第一階段實際使用（已在程式碼或部署套件中）

### 1.1 資料平台與基礎設施

| 元件 | 版本 | 用途 | 授權 | 商用付費 | 治理單位（國） | 起源／備註 |
|---|---|---|---|---|---|---|
| ClickHouse / ClickHouse Keeper | 25.8 LTS | 行為事件倉、分群／標籤 Bitmap | Apache-2.0 | 不需要（ClickHouse Cloud 可選） | ClickHouse Inc.（美） | 起源俄羅斯 Yandex，2021 分拆為美國公司；原始碼於 GitHub，自行建置映像 |
| Altinity clickhouse-operator | 0.24 | K8s 上管理 ClickHouse 叢集 | Apache-2.0 | 不需要（Altinity 支援合約可選） | Altinity（美） | — |
| PostgreSQL + pgvector | 16 / 0.7 | 控制面（看板、分群定義、審批、審計索引） | PostgreSQL License / PostgreSQL License | 不需要（Cloud SQL 可選） | PGDG（國際）／個人（美） | — |
| CloudNativePG | 1.24 | K8s 上管理 PostgreSQL | Apache-2.0 | 不需要 | EDB（義／美） | CNCF sandbox |
| Valkey | 8 | 快取、工作佇列 | BSD-3 | 不需要 | Linux Foundation | Redis 7.2 的開源分支（Redis ≥ 7.4 改為 RSAL／SSPL，已排除） |
| Apache Kafka（KRaft）+ Strimzi | 3.8 / 0.43 | 事件匯流排（第一階段只用於推送更新與原始事件落地） | Apache-2.0 | 不需要（Confluent 可選） | ASF（美）／Red Hat（美） | 不用 Confluent 社群授權元件 |
| Apache Flink + Kubernetes Operator | 1.20 / 1.10 | 串流（第二階段即時觸發；第一階段部署套件含、未啟用） | Apache-2.0 | 不需要 | ASF（美） | 起源德國 TU Berlin |
| Apache Airflow | 2.10 | 日批排程（第一階段由 API 內建排程器執行，Airflow 映像已備） | Apache-2.0 | 不需要（Astronomer 可選） | ASF（美） | 起源 Airbnb |
| SeaweedFS | 3.x | 機房物件儲存（GCP 改用 GCS） | Apache-2.0 | 不需要 | SeaweedFS Inc.（美） | 作者 Chris Lu 大陸出生、居美 |
| OpenBao | 2.x | 機房密鑰庫（GCP 改用 Secret Manager） | MPL-2.0 | 不需要 | Linux Foundation | HashiCorp Vault 的開源分支（Vault 改 BSL，已排除） |
| OpenTofu | 1.8 | 基礎設施即程式碼（GCP） | MPL-2.0 | 不需要 | Linux Foundation | Terraform 的開源分支（Terraform 改 BSL，已排除） |
| Keycloak | 25 | SSO／OIDC（broker 模式；direct 模式直連客戶 AD） | Apache-2.0 | 不需要 | CNCF／Red Hat（美） | — |
| Prometheus / OpenTelemetry Collector | 2.x / 0.1x | 指標抓取與告警 | Apache-2.0 | 不需要（GCP Managed Prometheus 可選） | CNCF | — |
| Grafana OSS | 11.x | 監控看板（平台內「系統監控」頁可直接看同一組面板） | **AGPL-3.0** | 不需要（Grafana Enterprise／Cloud 可選） | Grafana Labs（美） | 只內部使用、不修改、不對外散布 → 不觸發 AGPL 義務；法務核可例外 |
| OpenSearch | 2.x | 日誌（機房；GCP 用 Cloud Logging） | Apache-2.0 | 不需要 | Linux Foundation（AWS 捐贈） | Elasticsearch 改 SSPL／ELv2 後的開源分支 |
| Helm / Kubernetes（RKE2 或 kubeadm） | 3 / 1.30 | 部署 | Apache-2.0 | 不需要（Rancher Prime、OpenShift 訂閱可選） | CNCF／SUSE（德） | — |
| Red Hat UBI 9 minimal | 9.4 | 自建映像基底（core、batch） | UBI EULA（免費、可再散布） | 不需要（RHEL 節點 OS 若用 RHEL 需訂閱；Rocky Linux 9 免費） | Red Hat（美） | — |
| Debian 12 slim / distroless | 12 | Node 建置、ingest（Go）映像 | 多種（DFSG 相容）／Apache-2.0 | 不需要 | Debian／Google（美） | — |
| nginx-unprivileged | 1.27 | 前端靜態檔（Alpine 基底） | BSD-2 | 不需要 | NGINX（F5，美） | — |
| Eclipse Temurin JDK | 17 | Flink 執行環境 | GPLv2 + Classpath Exception | 不需要 | Eclipse Foundation（比） | OpenJDK 標準例外，可自由商用 |

### 1.2 後端（Python 3.12，`backend/requirements.txt`）

| 套件 | 用途 | 授權 | 商用付費 | 維護者（國） |
|---|---|---|---|---|
| FastAPI / Starlette / Uvicorn | API 框架與伺服器 | MIT / BSD-3 / BSD-3 | 不需要 | Sebastián Ramírez（德）／Encode（英） |
| Pydantic | 資料驗證 | MIT | 不需要 | Pydantic Services（英） |
| PyYAML | 設定檔 | MIT | 不需要 | 社群 |
| httpx | HTTP 用戶端（Webhook 推送、IdP） | BSD-3 | 不需要 | Encode（英） |
| asyncpg | PostgreSQL 驅動（控制面） | Apache-2.0 | 不需要 | MagicStack（加） |
| SQLGlot | 自定義 SQL 沙盒的解析與改寫 | MIT | 不需要 | TobikoData（美） |
| clickhouse-connect | ClickHouse 驅動（批次工具；API 走 HTTP 介面） | Apache-2.0 | 不需要 | ClickHouse Inc.（美） |
| PyJWT + cryptography | OIDC ID token 驗證、API token | MIT / Apache-2.0 或 BSD-3 | 不需要 | 個人（美）／PyCA（美） |
| mcp（官方 SDK） | Agent 工具閘道（第二階段；第一階段只有契約） | MIT | 不需要 | Anthropic（美） |
| anthropic（SDK，可選） | 問數建卡片、看板解讀的模型呼叫；未設金鑰不載入 | MIT | SDK 免費；**模型 API 依用量計費**（第一階段 AI 閘道關閉，不需要） | Anthropic（美） |
| python-ldap（可選） | direct 模式直連 AD | PSF 式 | 不需要 | 社群（德） |
| Python 標準庫 | sqlite3（排程器狀態）、hmac、ssl 等 | PSF | 不需要 | PSF（美） |

### 1.3 前端（`web/package.json`）

| 套件 | 用途 | 授權 | 商用付費 | 維護者（國） |
|---|---|---|---|---|
| React / React DOM | UI | MIT | 不需要 | Meta（美） |
| React Router | 路由 | MIT | 不需要 | Shopify／Remix（美／加） |
| TypeScript | 型別 | Apache-2.0 | 不需要 | Microsoft（美） |
| Vite / Vitest | 建置／測試 | MIT | 不需要 | VoidZero Inc.（美；創辦人大陸出生、居新加坡） |
| 圖表 | 自繪 SVG（折線、漏斗、路徑、留存矩陣），**沒有引入圖表套件** | — | — | 本專案；ECharts（百度起源）刻意不用 |
| 字型 | 不載入外部字型：CSS 以系統字型為主，等寬以 IBM Plex Mono（OFL-1.1）為優先、系統等寬為後備，未安裝即用系統字型 | — | — | — |

### 1.4 開發與 CI（不進產品）

| 工具 | 用途 | 授權 | 備註 |
|---|---|---|---|
| ruff | Python lint | MIT | Astral（美） |
| openapi-spec-validator | 契約驗證 | Apache-2.0 | — |
| Trivy / Syft | 映像弱點掃描、SBOM | Apache-2.0 | Aqua（以）／Anchore（美） |
| gitleaks | 密鑰外洩掃描 | MIT | — |
| hadolint | Dockerfile lint | GPL-3.0 | 只在 CI 執行、不散布，不影響產品授權 |
| cosign | 映像簽章 | Apache-2.0 | Sigstore（Linux Foundation） |
| Playwright + Chromium | 瀏覽器測試 | Apache-2.0 / BSD-3 | Microsoft／Google |
| Node.js 22 | 前端建置 | MIT | OpenJS Foundation |

## 2. 規劃中、第二階段才引入（尚未在程式碼裡）

| 元件 | 用途 | 授權 | 商用付費 | 治理單位（國） |
|---|---|---|---|---|
| Debezium | 交易／帳務 CDC | Apache-2.0 | 不需要 | Red Hat（美） |
| Apicurio Registry | Schema Registry | Apache-2.0 | 不需要 | Red Hat（美） |
| dlt | 批次整合（取代 Airbyte ELv2） | Apache-2.0 | 不需要（dltHub Cloud 可選） | dltHub（德） |
| dbt-core + dbt-clickhouse | 日批轉換 | Apache-2.0 | 不需要（dbt Cloud 可選） | dbt Labs（美）／ClickHouse Inc. |
| Apache Spark / Iceberg / Trino | 70 億歷史重算、冷層、Ad-hoc | Apache-2.0 | 不需要 | ASF／ASF／Trino SF（美） |
| Great Expectations / Soda Core | 資料品質 | Apache-2.0 | 不需要 | GX Labs（美）／Soda（比） |
| OpenMetadata / OpenLineage | 中繼資料、血緣 | Apache-2.0 | 不需要 | Collate（美）／LF AI & Data |
| Apache Superset（選配） | BI | Apache-2.0 | 不需要 | ASF（美） |
| LangGraph（選配） | Agent 編排 | MIT | 不需要 | LangChain（美） |
| pyroaring / RoaringBitmap | Bitmap（目前用 ClickHouse 內建 bitmap 函數） | MIT / Apache-2.0 | 不需要 | 個人（法）／個人（加） |
| D3（d3-scale、d3-shape）、Plotly.js | 互動大圖（目前自繪 SVG 已足夠） | ISC / MIT | 不需要 | Observable（美）／Plotly（加） |

## 3. 刻意不用（授權或來源）

| 元件 | 原因 | 替代 |
|---|---|---|
| Redis ≥ 7.4、MongoDB、Elasticsearch、MinIO、Metabase | RSAL／SSPL／AGPL（伺服器端 copyleft 或非 OSI 授權） | Valkey、PostgreSQL、OpenSearch、SeaweedFS／GCS、Superset |
| HashiCorp Vault／Terraform、Airbyte | BSL 1.1／ELv2（非開源） | OpenBao、OpenTofu、dlt |
| Confluent Platform（Schema Registry、ksqlDB） | Confluent Community License | Apicurio、Flink SQL |
| Apache SeaTunnel、Flink CDC、Apache ECharts、DolphinScheduler | 中國大陸起源（供應商評分表要求） | dlt、Debezium、自繪 SVG、Airflow |
| Oracle JDK、Highcharts、AG Grid Enterprise、Tableau／Power BI | 商業授權 | Temurin、自繪、Superset |

## 4. 怎麼維持這張表是對的

- CI 以 Syft 產 SBOM，任何不在 `LICENSES.md` 白名單的授權直接讓 PR 失敗；新增套件要同時改 `LICENSES.md` 與本文。
- 每個里程碑結束（docs/31 §2）由運維重跑 SBOM，比對本文 §1；第二階段元件引入時從 §2 搬到 §1。
- 交付客戶時附 SBOM（SPDX JSON）與本文 PDF；供應商評分表的「軟體來源聲明」引用 docs/25 §3 的口徑。
