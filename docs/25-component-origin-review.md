# 元件原產地與治理單位盤點（供客戶供應商評分表）

> 版本 v0.1（2026-10-16）。補充 `LICENSES.md`（授權）與 docs/11（風險評分）：每個元件再標「治理單位／公司、所在國、起源」，並對可能被評分表視為「大陸製」或「需留意」的元件提出決定與替代方案。判定依據是**軟體的治理單位與法律實體**（基金會／公司／專案委員會），不是個別貢獻者的國籍；有大陸籍主要作者但由非大陸實體治理者另標「留意」讓客戶自行裁量。

## 0. 結論先講

| 判定 | 元件 | 決定 |
|---|---|---|
| **大陸起源，建議移除** | Apache SeaTunnel（起源大陸社群／WhaleOps）、Flink CDC（起源 Ververica = 阿里巴巴子公司） | **移除**：批次整合只用 dlt + 自建 Python（`backend/integration/`）；CDC 只用 Debezium（Red Hat）。第一階段本來就不用 Flink（docs/23） |
| **大陸起源、已捐基金會，建議替換** | Apache ECharts（起源百度，2021 成 ASF 頂級專案，核心維護者仍多在百度） | **替換**：圖表改為自繪 SVG（原型已全部自繪）+ D3 scale／shape（ISC，美國 Observable）；需要互動大圖時用 Plotly.js（MIT，加拿大） |
| **非大陸但需留意（起源俄羅斯）** | ClickHouse（起源 Yandex；2021 起由美國公司 ClickHouse Inc. 治理，總部美國） | 保留；評分表若問「俄羅斯」一併揭露。替代方案成本極高（整個查詢層），不建議換 |
| **非大陸實體、主要作者大陸籍** | Vite／Vitest（VoidZero Inc.，美國公司，創辦人 Evan You 大陸出生、居新加坡）、SeaweedFS（SeaweedFS Inc.，美國，作者 Chris Lu 大陸出生、居美） | 保留但揭露；若評分表以作者籍貫計，Vite→Rollup+esbuild 直接用、Vitest→Jest（Meta）、SeaweedFS→客戶既有企業物件儲存或 Ceph RGW；GCP 用 GCS 本來就不用 SeaweedFS |
| **其餘全部** | 美國／歐洲／以色列／加拿大／日本實體或基金會（ASF、CNCF、Linux Foundation、Eclipse、OpenJS、PGDG、Red Hat、Google、Microsoft、Meta） | 無疑慮 |

被替換的系統：舊平台（舊平台 Data，大陸）本案即在汰換；V 平台 V-CDP 只是設計參考，不是元件。

## 1. 逐項盤點

### 1.1 前端

| 元件 | 用途 | 授權 | 治理單位 | 國家 | 起源／備註 | 判定 |
|---|---|---|---|---|---|---|
| React / ReactDOM | UI | MIT | Meta | 美國 | — | 無疑慮 |
| TypeScript | 語言 | Apache-2.0 | Microsoft | 美國 | — | 無疑慮 |
| React Router | 路由 | MIT | Remix（Shopify） | 美國／加拿大 | — | 無疑慮 |
| Vite | 建置 | MIT | VoidZero Inc. | 美國 | 創辦人 Evan You 大陸出生、居新加坡；依賴 esbuild（美）、Rollup（德）、cac（作者 EGOIST，大陸籍） | 留意 |
| Vitest | 測試 | MIT | Vitest team（VoidZero 贊助） | 美國／法國 | 主要維護者 Anthony Fu（大陸出生、居法國）、Vladimir Sheremet（俄） | 留意 |
| Babel（經 plugin-react） | 轉譯 | MIT | OpenJS Foundation | 美國 | — | 無疑慮 |
| **Apache ECharts（文件中規劃，尚未使用）** | 圖表 | Apache-2.0 | ASF | 美國（基金會）| **起源百度（大陸）**，核心維護者多在百度 | **替換**：自繪 SVG + D3（ISC，美） |
| React Flow（規劃） | 建模畫布 | MIT | xyflow GmbH | 德國 | — | 無疑慮 |
| TanStack Query／Table（規劃） | 資料快取／表格 | MIT | TanStack（Tanner Linsley） | 美國 | — | 無疑慮 |
| Zustand（規劃，目前未用） | 狀態 | MIT | Poimandres | 日本／德國 | — | 無疑慮 |
| Playwright | 煙霧測試 | Apache-2.0 | Microsoft | 美國 | — | 無疑慮 |
| 字型 Noto Sans TC / Plus Jakarta Sans / IBM Plex Mono | 字型 | OFL | Google / Tokotype / IBM | 美／印尼／美 | — | 無疑慮 |

`web/package-lock.json` 159 個套件全部是上述直接依賴的傳遞依賴（esbuild、rollup、babel、postcss、chai、tinypool…），CI 的 SBOM（Syft）每次列出；需要時可提供完整清單給客戶。

### 1.2 後端（Python）

| 元件 | 用途 | 授權 | 治理單位 | 國家 | 判定 |
|---|---|---|---|---|---|
| Python 3.12 | 語言 | PSF | Python Software Foundation | 美國 | 無疑慮 |
| FastAPI / Starlette / Uvicorn | API | MIT / BSD-3 / BSD-3 | Sebastián Ramírez（哥倫比亞籍，居德）/ Encode | 德國／英國 | 無疑慮 |
| Pydantic | 驗證 | MIT | Pydantic Services Inc. | 英國／美國 | 無疑慮 |
| SQLGlot | SQL 解析 | MIT | Toby Mao（TobikoData） | 美國 | 無疑慮 |
| clickhouse-connect | 驅動 | Apache-2.0 | ClickHouse Inc. | 美國 | 無疑慮（見 ClickHouse） |
| asyncpg | PostgreSQL 驅動 | Apache-2.0 | MagicStack | 加拿大 | 無疑慮 |
| httpx | HTTP | BSD-3 | Encode | 英國 | 無疑慮 |
| PyYAML | 設定 | MIT | PyYAML 社群 | 美國 | 無疑慮 |
| PyJWT / cryptography | 登入 token | MIT / Apache-2.0 或 BSD-3 | José Padilla / PyCA | 美國 | 無疑慮 |
| mcp（官方 SDK）/ LangGraph | Agent | MIT | Anthropic / LangChain | 美國 | 無疑慮 |
| pyroaring / RoaringBitmap（Java） | Bitmap | MIT / Apache-2.0 | Tom Cornebize / Daniel Lemire | 法國／加拿大 | 無疑慮 |
| Go 1.23 + franz-go | Collector | BSD-3 / BSD-3 | Google / Travis Bischel | 美國 | 無疑慮 |

### 1.3 資料平台

| 元件 | 用途 | 授權 | 治理單位 | 國家 | 起源／備註 | 判定 |
|---|---|---|---|---|---|---|
| ClickHouse + Keeper | OLAP | Apache-2.0 | ClickHouse Inc. | 美國（總部）/ 荷蘭 | **起源 Yandex（俄羅斯，2016 開源）**；2021 起獨立美國公司，創辦人仍含原作者 | 留意（俄羅斯起源），保留 |
| Altinity clickhouse-operator | Operator | Apache-2.0 | Altinity Inc. | 美國 | — | 無疑慮 |
| Apache Kafka（KRaft） | 匯流排 | Apache-2.0 | ASF | 美國 | 起源 LinkedIn | 無疑慮 |
| Strimzi | Kafka Operator | Apache-2.0 | CNCF（Red Hat 主導） | 美國 | — | 無疑慮 |
| Apicurio Registry | Schema | Apache-2.0 | Red Hat | 美國 | — | 無疑慮 |
| Debezium | CDC | Apache-2.0 | Red Hat | 美國 | — | 無疑慮 |
| **Flink CDC** | CDC（規劃） | Apache-2.0 | ASF（2024 捐入） | — | **起源 Ververica（阿里巴巴子公司）** | **移除**，只用 Debezium |
| Apache Flink + K8s Operator（第二階段才可能用） | 串流 | Apache-2.0 | ASF | 美國（基金會） | 起源柏林工業大學（德）；阿里巴巴是最大貢獻者之一 | 留意；第一階段不用 |
| Apache Spark / Iceberg / Trino | 批次／湖／Ad-hoc | Apache-2.0 | ASF / ASF / Trino Software Foundation | 美國 | 起源 UC Berkeley、Netflix、Facebook | 無疑慮 |
| Apache Airflow | 排程 | Apache-2.0 | ASF | 美國 | 起源 Airbnb | 無疑慮 |
| dbt-core | 轉換 | Apache-2.0 | dbt Labs | 美國 | — | 無疑慮 |
| **Apache SeaTunnel** | 批次整合（規劃備選） | Apache-2.0 | ASF | — | **起源大陸社群（Waterdrop），主要公司 WhaleOps（北京）** | **移除**，用 dlt + 自建 |
| dlt | 批次整合 | Apache-2.0 | dltHub | 德國 | — | 無疑慮 |
| Great Expectations / Soda Core | 資料品質 | Apache-2.0 | GX Labs / Soda | 美國／比利時 | — | 無疑慮 |
| OpenMetadata / OpenLineage | 中繼資料／血緣 | Apache-2.0 | Collate / LF AI & Data | 美國 | — | 無疑慮 |
| PostgreSQL / pgvector / CloudNativePG | 控制面 | PostgreSQL / PostgreSQL / Apache-2.0 | PGDG / Andrew Kane / EDB（CNCF） | 美國／美國／義大利 | — | 無疑慮 |
| Valkey | 快取 | BSD-3 | Linux Foundation | 美國 | AWS、Google、Oracle 主導 | 無疑慮 |
| SeaweedFS（機房物件儲存備選） | 物件儲存 | Apache-2.0 | SeaweedFS Inc. | 美國 | 作者 Chris Lu（大陸出生、居美） | 留意；優先用客戶既有企業 S3 儲存或 Ceph RGW（Red Hat／IBM，LGPL-2.1，需另列授權例外）；GCP 用 GCS |
| OpenBao / OpenTofu | 密鑰／IaC | MPL-2.0 | Linux Foundation（LF Edge） | 美國 | — | 無疑慮 |
| Keycloak | 身分代理 | Apache-2.0 | CNCF（Red Hat） | 美國 | — | 無疑慮 |
| Prometheus / OpenTelemetry / OpenSearch | 觀測 | Apache-2.0 | CNCF / CNCF / Linux Foundation（AWS） | 美國 | — | 無疑慮 |
| Grafana OSS | 看板 | AGPL-3.0（內部使用例外） | Grafana Labs | 美國 | — | 無疑慮 |
| Apache Superset（選配） | BI | Apache-2.0 | ASF | 美國 | 起源 Airbnb | 無疑慮 |

### 1.4 平台、映像與 CI

| 元件 | 用途 | 治理單位 | 國家 | 判定 |
|---|---|---|---|---|
| Kubernetes（RKE2 / kubeadm）、Helm | 平台 | CNCF / SUSE Rancher | 美國／德國 | 無疑慮 |
| Rocky Linux 9 / RHEL 9 | 節點 OS | RESF / Red Hat | 美國 | 無疑慮 |
| Red Hat UBI 9 minimal | 容器基底（core、agent） | Red Hat | 美國 | 無疑慮 |
| gcr.io distroless | 容器基底（ingest） | Google | 美國 | 無疑慮 |
| node:22-bookworm-slim / nginx-unprivileged | web 建置與服務 | OpenJS / F5 NGINX | 美國 | 無疑慮 |
| apache/airflow、flink 官方映像 | batch / flink-jobs | ASF | 美國 | 無疑慮 |
| Eclipse Temurin JDK | Java 執行環境 | Eclipse Foundation | 比利時／加拿大 | 無疑慮 |
| Syft / Trivy / Semgrep / gitleaks / pip-audit / hadolint | SBOM、弱掃、SAST、密鑰掃描 | Anchore / Aqua / Semgrep / 個人 / Trail of Bits / 個人 | 美／以色列／美／美／美／瑞士 | 無疑慮 |
| GitHub Actions（actions/*、dorny/paths-filter、azure/setup-helm） | CI | GitHub（Microsoft）/ 個人（捷克）/ Microsoft | 美國／捷克 | 無疑慮 |
| Google Analytics 360、BigQuery、GCS、Secret Manager | 來源與雲 | Google | 美國 | 無疑慮 |
| Microsoft Entra ID、Active Directory | 身分 | Microsoft | 美國 | 無疑慮 |

## 2. 本次變更

1. `LICENSES.md`：移除 SeaTunnel 與 Flink CDC；ECharts 改為 D3（ISC）+ Plotly.js（MIT）備選；表頭加「治理單位（國家）」欄。
2. docs/07、docs/08、docs/11、docs/24：同步上述替換；docs/02 的機房物件儲存註記「優先客戶既有 S3 相容儲存」。
3. CI 的 SBOM 規則不變（授權白名單）；另建議在評分表附上 `syft` 產出的 SBOM 檔作為完整清單。

## 3. 給客戶的回覆口徑

- 平台所有執行期元件的治理單位皆為美國、歐洲、以色列、加拿大或國際基金會；**沒有大陸公司治理的元件**。原本文件中列為備選的兩個大陸起源專案（SeaTunnel、Flink CDC）與一個百度起源專案（ECharts）已移除或替換。
- 需主動揭露的兩類：ClickHouse 起源於俄羅斯 Yandex（現為美國公司治理）；Vite／Vitest／SeaweedFS 由非大陸公司治理但主要作者為大陸出生。若評分表以此為扣分項，三者皆有現成替代（Rollup+esbuild、Jest、客戶既有物件儲存），替換成本 1–3 人日。
- 請客戶提供評分表原文，我們逐項填寫並附 SBOM。
