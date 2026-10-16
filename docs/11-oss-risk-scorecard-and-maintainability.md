# 開源元件評分：弱點掃描風險、可用性、主流性、Vibe Coding 維護性

> 版本 v0.3（2026-10-14）。補充 07（授權）與 08（工程規劃）：除了授權之外，每個元件再以四個面向評分，並據此修訂選型與開發慣例。

## 1. 評分準則

| 面向 | 問的問題 | 評分依據 |
|---|---|---|
| **弱點掃描風險**（Vuln） | 金融業每季弱掃／年度滲透能不能過？修補多快？ | 近兩年 CVE 數與嚴重度、修補節奏（LTS、安全公告）、是否 EOL、映像基底、相依樹大小（Java 大相依樹 = 高 CVE 噪音） |
| **可用性**（HA） | 能否做到無單點、滾動升級、備份還原、GCP 有無託管版？ | 原生複寫／Operator 成熟度／GCP 託管替代 |
| **主流性**（Main） | 五年後還有人維護嗎？招得到人嗎？ | GitHub stars、CNCF／Apache／Linux Foundation 背書、大廠採用、台灣金融業採用 |
| **Vibe coding 維護性**（Vibe） | AI 協作開發（Claude Code／Cursor）能不能可靠地改它？ | LLM 訓練語料熟悉度、文件完整、慣例清楚、型別與測試友善、設定即程式（YAML/SQL）而非自訂程式 |

分數 1–5（5 最佳）。總評 = 四項平均，< 3.5 需有替代方案。

## 2. 元件評分卡

| 層 | 元件 | Vuln | HA | Main | Vibe | 總評 | 備註 / 決定 |
|---|---|---|---|---|---|---|---|
| 前端 | React 18 + Vite + TypeScript | 4 | — | 5 | **5** | 4.7 | LLM 最熟；相依樹用 `pnpm audit` + Renovate 控制 |
| 前端 | 自繪 SVG + D3（scale／shape） | 5 | — | 5 | 5 | 5.0 | 原型已全部自繪；ECharts（百度起源）依客戶供應商評分改不採用（docs/25） |
| 後端 | Python 3.12 + FastAPI + Pydantic v2 | 4 | 5 | 5 | **5** | 4.8 | 採用；所有業務服務統一此棧 |
| 後端 | SQLGlot（SQL 生成） | 4 | — | 4 | 4 | 4.0 | 語意層編譯器；純 Python，無原生相依 |
| 入口 | Go 1.23 Collector | **5** | 5 | 5 | 4 | 4.8 | 靜態二進位、distroless、CVE 極少；只放熱路徑，程式量小 |
| 匯流排 | Apache Kafka 3.8（KRaft）+ Strimzi | 4 | 5 | 5 | 3 | 4.3 | 採用；KRaft 去掉 ZooKeeper 減少一個元件；Java 相依樹需定期更新 |
| CDC | Debezium 2.7 | 3 | 4 | 5 | 4 | 4.0 | 採用；Flink CDC（阿里巴巴起源）不用（docs/25） |
| 串流 | Apache Flink 1.19 + K8s Operator | 3 | 4 | 5 | **2（DataStream Java）/ 4（Flink SQL）** | 3.5 / 4.0 | **採用但 SQL-first**：規則用 `MATCH_RECOGNIZE`、轉換用 Flink SQL；自訂 Java 只保留 OneID 合併一個 job |
| OLAP | ClickHouse 24.8 LTS + Altinity Operator | 4 | 4 | 5 | **5** | 4.5 | 採用；LLM 對 ClickHouse SQL 很熟；LTS 每季安全修補 |
| 批次 | ClickHouse SQL + **dbt-core**（主）；Spark 3.5（僅 BigQuery 匯入、ML） | 4 / 3 | 4 | 5 | 5 / 3 | 4.5 / 3.8 | **修訂**：標籤與聚合盡量用 dbt 管理的 SQL，Spark 降為輔助，減少 Java/JVM 面 |
| 湖 | Apache Iceberg + Trino | 3 | 4 | 5 | 3 | 3.8 | 採用；Trino 只給 Ad-hoc 與冷層，非核心路徑 |
| 控制面 | PostgreSQL 16 + pgvector（Cloud SQL / CloudNativePG） | 5 | 5 | 5 | 5 | **5.0** | 採用 |
| 快取 | Valkey 8 | 5 | 4 | 4 | 5 | 4.5 | 採用（Redis 協定相容，LLM 等同熟悉；Memorystore 有託管版） |
| 排程 | Apache Airflow 2.10 | 3 | 4 | 5 | 4 | 4.0 | 採用；GCP 可用 Cloud Composer；DAG 由建模產物生成，不手寫 |
| 整合 | **dlt** + 自建 Python（backend/integration） | 4 | 3 | 4 | 5 | 4.0 | **修訂**：SeaTunnel（大陸起源）不採用（docs/25）；批次整合一律 dlt／自建 |
| 品質 | Great Expectations / Soda Core | 4 | — | 4 | 4 | 4.0 | 二選一即可；建議 Soda Core（YAML 檢查，簡單） |
| 中繼資料 | OpenMetadata | 3 | 3 | 4 | 3 | 3.3 | **降為選配**：一期先以 PostgreSQL 自建字典／血緣（OpenLineage 事件），二期再評估 |
| 物件儲存 | GCS（雲）／ SeaweedFS（機房） | 5 / 3 | 5 / 3 | 5 / 3 | 5 / 3 | 5.0 / 3.0 | GCP 一律 GCS；機房備選 **Ceph（Rook）**：更主流但更重，需專人 |
| 密鑰 | Cloud KMS + Secret Manager（雲）／ OpenBao（機房） | 5 / 4 | 5 / 4 | 5 / 3 | 5 / 4 | 5.0 / 3.8 | OpenBao 為 Linux Foundation 專案，API 與 Vault 相容，LLM 可沿用 Vault 知識 |
| 身分 | Keycloak 25 | 3 | 4 | 5 | 4 | 4.0 | 採用；CVE 較頻繁，需跟緊版本；GCP 可改 Cloud Identity + IAP |
| 觀測 | Prometheus + OpenTelemetry | 4 | 4 | 5 | 4 | 4.3 | 採用 |
| 觀測看板 | **Grafana OSS**（雲與機房皆用；AGPL 內部使用已獲接受） | 4 | 4 | 5 | 5 | 4.5 | 決定採用；Managed Prometheus／Prometheus 為資料源；不修改原始碼、不對外散布 |
| 日誌 | Cloud Logging（雲）／ OpenSearch（機房） | 5 / 3 | 5 / 4 | 5 / 4 | 5 / 3 | 5.0 / 3.5 | 機房日誌量大時 OpenSearch 需專人維運 |
| 容器 | UBI 9 minimal / distroless | 5 | — | 5 | 4 | 4.7 | Red Hat 安全公告與 CVE feed 完整；金融業弱掃友善 |
| K8s | GKE（雲）／ RKE2（機房） | 5 / 4 | 5 / 4 | 5 / 4 | 5 / 4 | 5.0 / 4.0 | 採用 |
| IaC | OpenTofu + Helm | 4 | — | 4 / 5 | 5 | 4.5 | 採用 |
| Agent | 官方 `mcp` SDK + 自建 runtime（Python） | 4 | 4 | 4 | 5 | 4.3 | 採用；避免重型 agent 框架（版本變動快、LLM 知識過期快） |
| 安全掃描 | Trivy、Grype、Syft、Semgrep OSS、Dependabot/Renovate | 5 | — | 5 | 5 | 5.0 | 採用（全部 Apache-2.0 / LGPL 工具，不進產品） |

### 2.1 與原提案簡報元件清單的對照（弱點角度）

提案簡報（2025/07）中的舊棧：Kubernetes 1.19、Hadoop 2.10、Hive 2.3／1.2、Spark 3.2、Flink 1.11／1.15、Kafka 2.1／2.8、MySQL 5.7、Redis 6.2、ZooKeeper、HBase、RocketMQ、Tendis、InfluxDB、Consul、MinIO。
這些版本**多數已 EOL**（K8s 1.19 於 2021 年、MySQL 5.7 於 2023 年、Hadoop 2.x、Hive 2.x、Spark 3.2、Flink 1.11/1.15 皆已停止安全修補），在金融業季度弱掃會出現大量無法修補的 Critical／High；且元件數量多（> 15 個有狀態服務），可用性與維運成本高。
本平台棧的設計原則正好相反：**有狀態服務只有 5 個**（Kafka、ClickHouse、PostgreSQL、Valkey、物件儲存），全部在 LTS 版本並有 Operator 或 GCP 託管版。

## 3. 弱點管理流程（SECURITY.md 摘要）

| 階段 | 作法 | 工具（授權） |
|---|---|---|
| 相依鎖定 | 所有語言鎖版本（pnpm-lock、uv.lock、Gradle lock、go.sum）；映像以 digest 固定 | — |
| 自動更新 | Renovate 每週開 PR（安全更新即時）；minor 自動合併需測試全綠 | Renovate（AGPL 工具，僅 CI 使用）或 Dependabot |
| 原始碼掃描 | 每次 PR：SAST、秘密掃描、相依弱點 | Semgrep OSS（LGPL）、gitleaks（MIT）、`pnpm audit`／`pip-audit`／`govulncheck` |
| SBOM 與映像掃描 | 每次建置：產 SBOM，掃映像，Critical／High 阻擋發佈 | Syft、Grype、Trivy（Apache-2.0） |
| 授權檢查 | SBOM 比對白名單，非白名單即失敗 | 自建 `license_check.py` |
| 簽章與准入 | 映像以 cosign 簽章；GKE Binary Authorization 只允許簽章映像 | Sigstore（Apache-2.0） |
| 執行期 | 非 root、唯讀檔案系統、NetworkPolicy、Pod Security `restricted`、GCP Artifact Analysis 持續掃描 | — |
| 修補 SLA | Critical 7 天、High 30 天、Medium 90 天；LTS 元件每季升版；K8s 每半年 | — |
| 外部弱掃 | 每季 Nessus／OpenVAS、年度滲透測試；提供 SBOM 給客戶資安 | — |
| 應變 | 安全公告訂閱（ClickHouse、Kafka、Flink、PostgreSQL、Keycloak、UBI errata）；緊急版 48 小時內 | — |

## 4. Vibe coding 維護性：開發慣例

目標：讓 Claude Code／Cursor 等 AI 協作工具在**不讀完整個 repo** 的情況下也能安全地改對地方，並讓 CI 擋住錯誤。

1. **設定即程式**：指標、維度、事件、角色、隱碼、連接器、agent、規則全部是 YAML（`backend/semantic`、`backend/security`、`backend/integration`、`backend/mcp`、`agents.yaml`）。多數需求變更不碰程式。
2. **SQL-first**：資料轉換用 dbt 管理的 ClickHouse SQL 與 Flink SQL；自訂 Java 限縮到一個 job。LLM 寫 SQL 的正確率遠高於寫 Flink DataStream。
3. **語言收斂**：Python（服務、agent、批次）、TypeScript（前端）、Go（僅 Collector）、SQL。不引入 Scala、Rust、Kotlin。
4. **契約先行**：OpenAPI → 產生 TS／Python client；語意查詢 JSON schema → 前端、API、agent 工具共用；改契約必須同步改三處，由 CI 驗證。
5. **可跑的本機環境**：`docker compose up` 十分鐘內可用；合成資料產生器可生成 1 萬到 1 億事件；每個模組有「黃金查詢」測試（固定輸入 → 固定數字）。
6. **Repo 導覽檔**：根目錄 `AGENTS.md`（給 AI 工具的工作規則：目錄職責、怎麼跑測試、禁止事項、常見改法）；每個 app 有 `README.md` 與 `Makefile`（`make test`、`make lint`、`make dev`）。
7. **小而清楚的服務**：每個服務 < 5k 行；函式有型別；禁止隱式魔法（metaclass、動態 import）；錯誤訊息可讀。
8. **測試金字塔**：單元（pytest、Vitest）→ 契約（schemathesis 對 OpenAPI）→ 整合（Testcontainers：ClickHouse、PostgreSQL、Kafka）→ 效能回歸（k6）。AI 改完程式必須跑 `make check` 全綠才能 PR。
9. **升級友善**：避免 fork 開源元件（提案簡報中「開源改寫」的 ClickHouse／RocketMQ 是維護黑洞）；需要客製走 plugin／UDF／設定。
10. **文件即測試**：docs 內的 SQL 範例與 YAML 範例在 CI 以 dry-run 驗證可解析，避免文件腐化。

## 5. 修訂後的最終棧（一句話版）

**GKE（或 RKE2）上的五個有狀態服務（Kafka、ClickHouse、PostgreSQL、Valkey、GCS/物件儲存）＋ SQL-first 的 Flink/dbt 轉換 ＋ Python/TypeScript/Go 三語言 ＋ YAML 驅動的語意層、權限與 agent。** 所有元件 OSI 寬鬆授權、LTS 版本、有 Operator 或託管版，並由 CI 自動做 SBOM、弱點、授權與契約檢查。
