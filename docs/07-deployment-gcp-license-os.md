# 部署：GCP 快速部署（容器化）、開源授權審查、作業系統支援

> 版本 v0.2（2026-10-14）。

## 1. GCP 參考架構（容器化，一套 Helm 發佈）

目標：**一條指令在 GCP 建立可用環境**，所有服務容器化、以 Helm umbrella chart（本文件稱「部署套件」）發佈；相同套件也能部署到機房 Kubernetes（RKE2／kubeadm）。

```
asia-east1（台灣）  VPC（私有子網、無公網 IP）
├─ GKE Standard（區域叢集，3 zone）
│   ├─ node pool: ingest   (c3-standard-8 ×3)      Collector、GTM SS、Debezium、Kafka Connect
│   ├─ node pool: stream   (c3-standard-16 ×4)     Flink（K8s Operator）
│   ├─ node pool: olap     (c3-highmem-44 ×4 + Hyperdisk Balanced/Extreme)  ClickHouse 2×2 + Keeper
│   ├─ node pool: data     (n2-standard-8 ×3)      Kafka（Strimzi）、Spark/Trino、Airflow
│   └─ node pool: app      (n2-standard-4 ×3)      API、語意層、Agent Runtime、Tool Gateway、Web
├─ Cloud SQL for PostgreSQL 16（HA）+ pgvector     控制面、Insight Store、中繼資料
├─ Memorystore for Valkey                           快取、即時標籤、OneID 熱表
├─ Cloud Storage（CMEK）                            Iceberg 冷層、原始事件、備份（取代 MinIO）
├─ Artifact Registry                                容器映像（Binary Authorization 簽章）
├─ Secret Manager + Cloud KMS（HSM 層級）            金鑰（取代 Vault）
├─ Cloud Logging / Monitoring + Managed Prometheus  觀測
├─ Private Google Access / Private Service Connect  BigQuery、GCS 不經公網
└─ VPC Service Controls、Cloud Armor、IAP            邊界與存取
```

- **BigQuery（GA360）**：同專案或跨專案以 Storage Read API 讀取；流量留在 Google 骨幹。
- **資料駐留**：所有服務與儲存指定 `asia-east1`；BigQuery 資料集亦建議設在 `asia-east1`。
- **可替換**：Cloud SQL ↔ CloudNativePG（機房）；Memorystore ↔ Valkey（機房）；GCS ↔ 機房物件儲存（見授權審查）；Cloud KMS ↔ OpenBao + HSM。

### 1.1 部署套件（容器 + Helm + IaC）
```
deploy/
  helm/altatech-dmp/            umbrella chart：依賴 strimzi、flink-kubernetes-operator、clickhouse-operator、cloudnative-pg（機房）
    values.yaml                 預設（機房）
    values-gcp.yaml             GCP 覆寫（使用 Cloud SQL / Memorystore / GCS / Workload Identity）
    values-small.yaml           PoC 單節點
  tofu/gcp/                     OpenTofu：VPC、GKE、Cloud SQL、Memorystore、GCS、KMS、IAM、Artifact Registry
  images/                       Dockerfile（UBI 9 minimal 基底）：collector、api、semantic、agent-runtime、tool-gateway、web、flink-jobs、spark-jobs
  Makefile                      make gcp-up / make deploy ENV=gcp / make smoke
```
- 一鍵流程：`tofu apply` → `helm dependency update` → `helm upgrade --install dmp ./helm/altatech-dmp -f values-gcp.yaml` → `make smoke`（寫入 1 萬測試事件並查詢）。
- 映像：多架構（amd64）、無 root、唯讀檔案系統、SBOM（Syft）與弱點掃描（Trivy，皆 Apache-2.0）。
- GKE Autopilot 可用於 app／ingest 池；ClickHouse、Kafka 需本地 NVMe／Hyperdisk，建議 Standard 池。

### 1.2 規模對應（GCP）
| 規模 | 每日事件 | ClickHouse | Flink TM | 月費粗估（僅運算+儲存，不含網路） |
|---|---|---|---|---|
| PoC | < 2 千萬 | 1 × c3-highmem-8 | 2 | 低 |
| 中 | 3 億 | 2×2 c3-highmem-44 + Hyperdisk 8 TB×4 | 8 | 中 |
| 大（百億級總量、日 5 億） | 5 億 | 4×2 c3-highmem-88 | 16 | 高 |
實際報價以 GCP Pricing Calculator 為準；建議以 CUD（承諾使用折扣）降低 OLAP 池成本。

## 2. 開源授權審查（避免金融業需額外購買商業授權）

原則：**只採用 OSI 核可的寬鬆授權（Apache-2.0、MIT、BSD、PostgreSQL、MPL-2.0）**；**排除** AGPL／SSPL／BSL／ELv2／RSAL 等「源碼可得但有使用或服務限制」或「需商業授權才能安心用於企業內部」的元件；Copyleft（GPL）僅限不連結進自家程式的獨立工具。

| 層 | 選用元件 | 授權 | 風險 | 刻意不用（授權） |
|---|---|---|---|---|
| 事件匯流排 | Apache Kafka 3.8（KRaft） | Apache-2.0 | 無 | Confluent Platform 元件：Schema Registry、ksqlDB（Confluent Community License）→ 改用 **Apicurio Registry**（Apache-2.0） |
| CDC | Debezium、Flink CDC | Apache-2.0 | 無 | Airbyte（ELv2）、Fivetran（商業） |
| 串流 | Apache Flink 1.19 + Kubernetes Operator | Apache-2.0 | 無 | — |
| OLAP | ClickHouse 24.8 LTS、ClickHouse Keeper、clickhouse-operator（Altinity） | Apache-2.0 | 無 | Druid／Pinot 亦 Apache-2.0 可替代；Snowflake／Databricks 商業 |
| 批次／湖 | Apache Spark 3.5、Apache Iceberg、Trino | Apache-2.0 | 無 | Databricks Delta（Apache-2.0 但生態綁定） |
| 物件儲存（機房） | **GCS（雲）**；機房：SeaweedFS（Apache-2.0）或 Ceph RGW（LGPL-2.1，獨立服務） | — | 低 | **MinIO（AGPL-3.0）**：企業內部使用雖不觸發散布義務，但金融業法務常列為紅燈；排除 |
| 快取 | **Valkey 8**（BSD-3） | BSD-3 | 無 | **Redis ≥ 7.4（RSALv2／SSPL；8.0 起另有 AGPL）**：排除 |
| 關聯式 DB | PostgreSQL 16 + pgvector、CloudNativePG | PostgreSQL／PostgreSQL／Apache-2.0 | 無 | MongoDB（SSPL）排除 |
| 排程 | Apache Airflow 2.10 | Apache-2.0 | 無 | Prefect（Apache-2.0 可）、Dagster（Apache-2.0 可）；Astronomer 商業 |
| 批次整合 | Apache SeaTunnel、dlt | Apache-2.0 | 無 | Airbyte（ELv2）、Talend（商業） |
| 資料品質 | Great Expectations、Soda Core | Apache-2.0 | 無 | — |
| 中繼資料／血緣 | OpenMetadata、OpenLineage | Apache-2.0 | 無 | Collibra／Alation 商業 |
| BI／看板（選配） | Apache Superset | Apache-2.0 | 無 | **Metabase（AGPL）**、**Grafana（AGPL-3.0 自 8.0）**、Tableau／Power BI 商業 |
| 觀測 | Prometheus、OpenTelemetry；儀表板 **Grafana OSS**（AGPL-3.0，僅內部使用、不散布、不修改，法務已接受）；日誌 **OpenSearch**（Apache-2.0）或 Cloud Logging | Apache-2.0 / AGPL-3.0（內部使用） | 低 | Loki／Tempo 改用 OpenSearch／Cloud Trace；Elastic（SSPL／ELv2／AGPL 三選一）排除 |
| 密鑰 | Cloud KMS／Secret Manager；機房：**OpenBao**（MPL-2.0） | MPL-2.0 | 無 | **HashiCorp Vault／Terraform（BSL 1.1）**：排除 → **OpenTofu**（MPL-2.0） |
| 身分 | Keycloak | Apache-2.0 | 無 | Okta／Auth0 商業 |
| API／閘道 | FastAPI、Uvicorn；Envoy／Kong OSS | MIT／BSD；Apache-2.0 | 無 | Kong Enterprise 商業 |
| 前端 | React、Vite、TypeScript、**Apache ECharts** | MIT／MIT／Apache-2.0／Apache-2.0 | 無 | Highcharts（商業）、AG Grid Enterprise（商業；Community 版 MIT 可） |
| Agent／LLM | 官方 `mcp` SDK（MIT）、LangGraph（MIT）選配；模型經企業閘道 | MIT | 模型授權另計（Claude API 商業條款；本地開源模型注意 Llama 社群授權非 OSI） | — |
| 容器基底 | Red Hat **UBI 9**（免費再散布）、Debian 12 slim、distroless | UBI EULA（免費）、多種 | 無 | — |
| K8s（機房） | RKE2 或 kubeadm；Operators：Strimzi、Flink、Altinity clickhouse-operator、CloudNativePG | Apache-2.0 | 無 | OpenShift（訂閱）、Rancher Prime（訂閱） |
| Java 執行環境 | Eclipse Temurin 17／21 | GPLv2+CE（標準 OpenJDK 例外，可自由使用） | 無 | Oracle JDK（商業條款） |

流程保證：
- CI 以 **Syft 產生 SBOM** + **ScanCode／license-checker** 比對白名單；非白名單授權直接讓 CI 失敗。
- `LICENSES.md`（repo 根目錄）維護元件清單與授權；每次升版更新。
- 法務一次性核可白名單（Apache-2.0、MIT、BSD-2/3、ISC、PostgreSQL、MPL-2.0、UBI EULA、GPLv2+CE for OpenJDK）。

## 3. 作業系統支援

| 層 | 建議 | 支援矩陣 |
|---|---|---|
| **GKE 節點** | Container-Optimized OS（Google 維護） | 預設；或 Ubuntu（GKE 提供） |
| **機房 K8s 節點／VM** | **RHEL 9.x** 或 **Rocky Linux 9.x**（1:1 相容，免費）；次選 AlmaLinux 9、Ubuntu 22.04／24.04 LTS | RHEL 9：完整支援至 2027、維護至 2032；RHEL 10（2025/5 GA）與 Rocky 10（2025/6）可作新環境；**RHEL／Rocky 8 完整支援已於 2024/5 結束**（維護至 2029/5），新部署不採用 |
| 容器基底 | **UBI 9 minimal**（與 RHEL 9 同源，免訂閱可散布）；Python／Node 映像用 Debian 12 slim 或 distroless | 所有自建映像統一基底，CVE 修補節奏一致 |
| 核心需求 | kernel ≥ 5.14（RHEL 9），cgroup v2，`vm.max_map_count`、`fs.file-max` 調整（ClickHouse／Kafka） | Ansible 角色統一設定 |
| 不支援 | CentOS Linux 7／8（EOL）、Windows 節點 | — |

驗證策略：CI 於 Rocky 9 與 Ubuntu 24.04 兩種節點映像各跑一次整合測試；RHEL 9 以客戶訂閱環境做發版前驗證。

## 4. 安全基線（GCP）
- Workload Identity（無 SA 金鑰檔）、Binary Authorization、Shielded GKE nodes、私有叢集、CMEK（GCS／Cloud SQL／Hyperdisk）、VPC-SC 圍籬包住 BigQuery／GCS／Cloud SQL、Cloud Armor WAF 於 Collector 入口、IAP 保護管理介面、Cloud Audit Logs 匯出至不可變儲存。
- 對應金管會雲端委外要求：資料境內（asia-east1）、加密與金鑰管理（Cloud KMS／HSM、CMEK）、稽核權（Cloud Audit Logs 匯出）、終止時資料刪除（CMEK 金鑰銷毀 + 桶刪除證明）。
