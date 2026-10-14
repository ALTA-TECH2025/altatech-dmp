# 第三方元件授權清單（白名單：Apache-2.0、MIT、BSD-2/3、ISC、PostgreSQL、MPL-2.0、UBI EULA、GPLv2+Classpath Exception for OpenJDK）

| 元件 | 版本 | 授權 | 用途 | 備註 |
|---|---|---|---|---|
| Apache Kafka | 3.8 | Apache-2.0 | 事件匯流排 | KRaft，不用 Confluent 元件 |
| Apicurio Registry | 2.x | Apache-2.0 | Schema Registry | 取代 Confluent Schema Registry |
| Debezium / Flink CDC | 2.7 / 3.2 | Apache-2.0 | CDC | — |
| Apache Flink + K8s Operator | 1.19 / 1.10 | Apache-2.0 | 串流 | — |
| ClickHouse / Keeper | 24.8 LTS | Apache-2.0 | OLAP | — |
| Altinity clickhouse-operator | 0.24 | Apache-2.0 | Operator | — |
| Apache Spark / Iceberg / Trino | 3.5 / 1.6 / 460 | Apache-2.0 | 批次／湖／Ad-hoc | — |
| Apache Airflow | 2.10 | Apache-2.0 | 排程 | — |
| Apache SeaTunnel / dlt | 2.3 / 1.x | Apache-2.0 | 批次整合 | 取代 Airbyte（ELv2） |
| Great Expectations / Soda Core | 1.x | Apache-2.0 | 資料品質 | — |
| OpenMetadata / OpenLineage | 1.5 / 1.x | Apache-2.0 | 中繼資料／血緣 | — |
| PostgreSQL / pgvector / CloudNativePG | 16 / 0.7 / 1.24 | PostgreSQL / PostgreSQL / Apache-2.0 | 控制面 | — |
| Valkey | 8 | BSD-3 | 快取 | 取代 Redis（RSAL/SSPL/AGPL） |
| SeaweedFS | 3.x | Apache-2.0 | 機房物件儲存 | 取代 MinIO（AGPL）；GCP 用 GCS |
| OpenBao / OpenTofu | 2.x / 1.8 | MPL-2.0 | 密鑰／IaC | 取代 Vault / Terraform（BSL） |
| Keycloak | 25 | Apache-2.0 | 身分 | — |
| Prometheus / OpenTelemetry / OpenSearch | — | Apache-2.0 | 觀測 | — |
| Grafana OSS | 11.x | AGPL-3.0（例外：僅內部使用、不散布、不修改） | 看板與告警 | 法務核可之白名單例外；不得嵌入產品對外提供 |
| Apache Superset | 4.x | Apache-2.0 | 選配 BI | 取代 Metabase（AGPL） |
| FastAPI / Pydantic / SQLGlot / clickhouse-connect | — | MIT / MIT / MIT / Apache-2.0 | 後端 | — |
| React / Vite / TypeScript / Apache ECharts / React Flow | — | MIT / MIT / Apache-2.0 / Apache-2.0 / MIT | 前端 | — |
| mcp（官方 SDK）/ LangGraph | — | MIT / MIT | Agent | — |
| RoaringBitmap（Java）/ pyroaring | — | Apache-2.0 / MIT | Bitmap | — |
| franz-go | — | BSD-3 | Go Kafka 客戶端 | — |
| Eclipse Temurin JDK | 17/21 | GPLv2 + Classpath Exception | Java 執行環境 | 可自由使用 |
| Red Hat UBI 9 | 9.4 | UBI EULA | 容器基底 | 免訂閱可再散布 |
| Syft / Trivy | — | Apache-2.0 | SBOM／弱點掃描 | CI |

CI 規則：SBOM 中任何不在白名單的授權 → 失敗；新增元件需更新本表。
