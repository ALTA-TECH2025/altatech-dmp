# 第三方元件授權清單（原產地與治理單位盤點見 docs/25；給客戶的「來源、授權、是否商用付費」整理版見 docs/32）（白名單：Apache-2.0、MIT、BSD-2/3、ISC、PostgreSQL、MPL-2.0、UBI EULA、GPLv2+Classpath Exception for OpenJDK）

| 元件 | 版本 | 授權 | 用途 | 備註 | 治理單位（國家） |
|---|---|---|---|---|---|
| Apache Kafka | 3.8 | Apache-2.0 | 事件匯流排 | KRaft，不用 Confluent 元件 | ASF（美） |
| Apicurio Registry | 2.x | Apache-2.0 | Schema Registry | 取代 Confluent Schema Registry | Red Hat（美） |
| Debezium | 2.7 | Apache-2.0 | CDC | Flink CDC（阿里巴巴起源）已移除，見 docs/25 | Red Hat（美） |
| Apache Flink | 1.19（docs/43 改 2.3，確認後更新並附 CVE 檢查） | Apache-2.0 | 串流 | 第一階段 standalone（Docker）部署，不用 K8s Operator（docs/44） | ASF（美；起源德國 TU Berlin） |
| ClickHouse / Keeper | 25.8 LTS | Apache-2.0 | OLAP | — | ClickHouse Inc.（美；起源俄羅斯 Yandex） |
| Altinity clickhouse-operator | 0.24 | Apache-2.0 | Operator | 第一階段不使用（改 Docker，docs/44）；保留供日後 K8s 部署 | Altinity（美） |
| Apache Spark / Iceberg / Trino | 3.5 / 1.6 / 460 | Apache-2.0 | 批次／湖／Ad-hoc | — | ASF／ASF／Trino SF（美） |
| Apache Airflow | 2.10 | Apache-2.0 | 排程 | — | ASF（美） |
| dlt | 1.x | Apache-2.0 | 批次整合 | 取代 Airbyte（ELv2）；SeaTunnel（大陸起源）已移除，見 docs/25 | dltHub（德） |
| anthropic（Python SDK） | 1.x | MIT | 問數建卡片與看板解讀敘述版的模型呼叫（可指向企業 LLM Gateway） | 可選依賴：未設金鑰不載入；只送語意層目錄與規則算出的彙總事實，不送明細列 | Anthropic（美） |
| Great Expectations / Soda Core | 1.x | Apache-2.0 | 資料品質 | — | GX Labs（美）／Soda（比） |
| OpenMetadata / OpenLineage | 1.5 / 1.x | Apache-2.0 | 中繼資料／血緣 | — | Collate（美）／LF AI & Data |
| PostgreSQL / pgvector / CloudNativePG | 16 / 0.7 / 1.24 | PostgreSQL / PostgreSQL / Apache-2.0 | 控制面 | — | PGDG／個人（美）／EDB（義） |
| asyncpg | 0.29+ | Apache-2.0 | 控制面 PostgreSQL 驅動（`backend/api/store_pg.py`） | 純 Python + Cython，無 libpq；pip-audit 在 security.yml | MagicStack（加） |
| Redis（含 Sentinel） | 8.x | AGPL-3.0（Redis 8 起 RSALv2／SSPLv1／AGPLv3 三選一，本案選 AGPLv3；**暫行例外（已送法務，Windy 2026-10-23：未否決前照此規劃）**：僅內部使用、不散布、不修改、不對外提供 Redis 服務） | 快取、登入狀態、輕量佇列 | 2026-10-22 研發／運維決議沿用 Redis（docs/44 §3）；客戶端只用與 Valkey 共通的指令，法務不核可時換 Valkey 8（BSD-3）只改映像 | Redis Ltd.（美） |
| SeaweedFS | 3.x | Apache-2.0 | 機房物件儲存 | 取代 MinIO（AGPL）；GCP 用 GCS | SeaweedFS Inc.（美；作者大陸出生） |
| OpenBao / OpenTofu | 2.x / 1.8 | MPL-2.0 | 密鑰／IaC | 取代 Vault / Terraform（BSL） | Linux Foundation |
| Keycloak | 25 | Apache-2.0 | 身分 | — | CNCF／Red Hat（美） |
| Prometheus / Alertmanager / node-exporter / cAdvisor | — | Apache-2.0 | 指標與告警 | — | CNCF／CNCF／CNCF／Google（美） |
| Grafana Alloy | 1.x | Apache-2.0 | 容器日誌收集 → Loki | 不用 Promtail（AGPL、官方已停止維護） | Grafana Labs（美） |
| Grafana Loki | 3.x | AGPL-3.0（暫行例外，已送法務；未否決前照此規劃：僅內部使用、不散布、不修改） | 日誌 | 2026-10-22 決議：日誌只用 Loki，不用 OpenSearch／VictoriaLogs／Cloud Logging（docs/44 §4） | Grafana Labs（美） |
| pgBackRest | 2.x | MIT | PostgreSQL 備份與 WAL 歸檔 | 取代 CloudNativePG 內建備份（docs/44 §5） | Crunchy Data 等社群（美） |
| Docker Engine（Moby）/ Docker Compose v2 | 27.x / 2.x | Apache-2.0 | 部署 | 第一階段部署方式（docs/44）；不用 Docker Desktop（商業授權） | Docker Inc.（美） |
| Grafana OSS | 11.x | AGPL-3.0（例外：僅內部使用、不散布、不修改） | 看板與告警 | 法務核可之白名單例外；不得嵌入產品對外提供 | Grafana Labs（美） |
| Apache Superset | 4.x | Apache-2.0 | 選配 BI | 取代 Metabase（AGPL） | ASF（美） |
| FastAPI / Pydantic / SQLGlot / clickhouse-connect | — | MIT / MIT / MIT / Apache-2.0 | 後端 | — | 個人（德）／Pydantic（英）／TobikoData（美）／ClickHouse Inc.（美） |
| React / Vite / TypeScript / React Router / React Flow | — | MIT / MIT / Apache-2.0 / MIT / MIT | 前端 | — | Meta／VoidZero（美；創辦人大陸出生）／Microsoft／Shopify／xyflow（德） |
| Apache ECharts（含 zrender） | 6.1.0 | Apache-2.0 | 圖表（分群畫像、標籤分布；分析圖逐步換用） | Apache 軟體基金會頂級專案；只載入用到的圖型與元件、SVG 渲染；npm audit 0 漏洞（2026-10-17） | ASF（美） |
| Mantine（@mantine/core、@mantine/hooks；帶入 floating-ui、react-remove-scroll 等 MIT 相依） | 8.3.18 | MIT | 前端設計系統（表單、表格、標章、版面） | 主題 token 與 app.css 同一套；npm audit 0 漏洞（2026-10-17）；見 docs/35 | Mantine 開源社群（主要維護者在烏克蘭） |
| D3（d3-scale / d3-shape）/ Plotly.js（備選） | 7.x / 2.x | ISC / MIT | 圖表 | 自繪 SVG 的分析圖仍用；互動大圖才用 Plotly.js | Observable（美）／Plotly（加） |
| mcp（官方 SDK）/ LangGraph | — | MIT / MIT | Agent | — | Anthropic／LangChain（美） |
| PyJWT / cryptography | 2.x / 4x | MIT / Apache-2.0 或 BSD-3 | 登入（OIDC ID token 驗證、API token） | — | 個人（美）／PyCA（美） |
| python-ldap | 3.x | PSF 式（permissive） | 直連 AD（direct 模式才需要；broker 模式不裝） | 需 libldap | python-ldap 社群（德） |
| Vitest | 3.x | MIT | 前端測試 | — | Vitest team／VoidZero（美；維護者大陸出生） |
| RoaringBitmap（Java）/ pyroaring | — | Apache-2.0 / MIT | Bitmap | — | 個人（加）／個人（法） |
| franz-go | — | BSD-3 | Go Kafka 客戶端 | — | 個人（美） |
| Eclipse Temurin JDK | 17/21 | GPLv2 + Classpath Exception | Java 執行環境 | 可自由使用 | Eclipse Foundation |
| Red Hat UBI 9 | 9.4 | UBI EULA | 容器基底 | 免訂閱可再散布 | Red Hat（美） |
| Syft / Trivy | — | Apache-2.0 | SBOM／弱點掃描 | CI | Anchore（美）／Aqua（以） |
| Playwright（@playwright/test）+ Chromium | 1.48 | Apache-2.0 / BSD-3 | 瀏覽器端到端測試（CI 對真後端跑主線；不進產品） | devDependency | Microsoft（美）／Google（美） |
| PyFlink（apache-flink、apache-flink-libraries）及其 Python 相依（apache-beam、pemja、py4j、pyarrow、pandas、numpy、grpcio、protobuf 等 51 個，`backend/streaming/requirements-flink.txt`） | 2.3.0 | Apache-2.0；相依為 Apache-2.0／BSD／MIT／ISC／MPL-2.0／PSF-2.0（regex 另含 CNRI-Python，Python 系寬鬆授權） | Flink 作業的 Python UDF 與批流一致性測試（docs/43 F2） | 2026-10-22 pip-audit 0 弱點；pyarrow 23.0.1、httplib2 0.32.0 刻意高於上游上限以避開 CVE-2026-25087／CVE-2026-59939（檔頭說明）；不進 dmp-core 映像 | ASF（美） |
| superpowers（Claude Code 外掛，`.claude/settings.json` 啟用） | 6.4.2 | MIT | 開發流程 skill：規格先行（brainstorming → 規格 → 計畫）、TDD、審查、除錯 | 開發工具；只有文字型 skill 與一個 SessionStart hook（注入使用說明，不連網、不改檔），不進產品映像；來源為 Anthropic 內建外掛目錄 | Jesse Vincent（美） |

CI 規則：SBOM 中任何不在白名單的授權 → 失敗；新增元件需更新本表。
