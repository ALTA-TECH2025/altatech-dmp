# 架構圖集：系統架構、元件組成、數據流

> 版本 v0.3（2026-10-14）。三張圖由 `docs/diagrams/build.py` 產生（純 Python，無相依）；改資料結構後執行 `python3 docs/diagrams/build.py` 重新產生 SVG，PNG 預覽以瀏覽器轉出。下方另附 Mermaid 版本供 GitHub 直接渲染與快速修改。

| 圖 | 檔案 | 用途 |
|---|---|---|
| 系統架構圖（分層視圖） | `diagrams/system-architecture.svg` / `.png` | 提案與評審：六層（來源→接入→儲存→計算→語意服務→Agent→體驗）＋ 安全、觀測、部署、供應鏈 |
| 元件組成圖（GKE 部署視圖） | `diagrams/component-deployment.svg` / `.png` | 基礎設施與資安評審：節點池、Pod、託管服務、Ingress、邊界 |
| 數據流圖 | `diagrams/data-flow.svg` / `.png` | 工程與資料治理：六段資料流、關鍵規則、延遲 SLA |

## 提案簡報版（16:9，`diagrams/slides/`，由 `build_slides.py` 產生）

一頁一個訊息、每頁不超過 12 個方塊、字級 20px 以上，可直接貼進 Google Slides 或 PowerPoint：

| 頁 | 檔案 | 訊息 |
|---|---|---|
| 1 系統架構 | `slides/slide-1-architecture.png` | 一個中台承接 GA360 行為軌跡與證券交易事件；四大能力 + 基礎 + 治理 |
| 2 數據流 | `slides/slide-2-data-flow.png` | 五個步驟、五個延遲承諾 |
| 3 AI Agent | `slides/slide-3-ai-agent.png` | 選擇題式 agent 流程、六個內建 agent、金融業守門 |
| 4 GCP 部署與規模 | `slides/slide-4-gcp-deployment.png` | 節點池、託管服務、安全、18 台／月費 |

![簡報 1](diagrams/slides/slide-1-architecture.png)
![簡報 2](diagrams/slides/slide-2-data-flow.png)
![簡報 3](diagrams/slides/slide-3-ai-agent.png)
![簡報 4](diagrams/slides/slide-4-gcp-deployment.png)

## 工程版（詳細）

![系統架構圖](diagrams/system-architecture.png)

![元件組成圖](diagrams/component-deployment.png)

![數據流圖](diagrams/data-flow.png)

## Mermaid 版本

### 系統架構（分層）
```mermaid
flowchart TB
  subgraph EXP[體驗層]
    WEB[DMP Web<br/>React + ECharts] --- AIUI[AI 洞察助理] --- EXT[外部系統<br/>MA / 客服 / 營業員 App / AI Coach] --- MCPC[外部 AI 客戶端<br/>Claude Code / Cursor]
  end
  subgraph AGENT[Agent 層]
    RT[Insight Agent Runtime<br/>Planner→Executor→Verifier→Narrator] --- GW[Agent Tool Gateway MCP<br/>權限·預算·脫敏·審計] --- IS[Insight Store + Knowledge Pack<br/>PostgreSQL + pgvector] --- LLM[LLM Gateway]
  end
  subgraph SVC[語意與服務層 FastAPI]
    SEM[Semantic Layer<br/>YAML → SQL 編譯器] --- AQ[Analytics Query API] --- SG[Segment / Tag / Profile / OneID API] --- PE[Policy Engine<br/>模組→行級→隱碼→審批]
  end
  subgraph CMP[計算層]
    FL[Flink SQL-first<br/>標準化·事件化·OneID·Session·CEP] --- DBT[dbt + ClickHouse SQL<br/>日批標籤·聚合] --- SP[Spark 輔助<br/>BigQuery 匯入·模型] --- AF[Airflow]
  end
  subgraph STO[儲存層 五個有狀態服務]
    KF[(Kafka)] --- CH[(ClickHouse)] --- PG[(PostgreSQL + pgvector)] --- VK[(Valkey)] --- OBJ[(GCS / 物件儲存<br/>Iceberg 冷層)]
  end
  subgraph ING[接入層]
    COL[Collector Go<br/>/v1/events /sa /ga4] --- GA[GA360 Importer<br/>BigQuery + GTM SS] --- CDC[Debezium / Flink CDC] --- BAT[dlt / SeaTunnel / 神策連接器]
  end
  SRC[資料來源：GA360 · 交易系統 · CRM · 神策 · MA · AI Coach] --> ING --> STO --> CMP --> SVC --> AGENT --> EXP
  SEC[安全：Keycloak · RBAC · 隱碼 · KMS · 審計 WORM]:::x -.-> SVC
  OBS[觀測：Prometheus · OTel · Grafana OSS]:::x -.-> CMP
  DEP[部署：Docker 套件 · Helm · GKE/RKE2 · OpenTofu]:::x -.-> STO
  classDef x fill:#F1F4F6,stroke:#56646D
```

### 元件組成（部署）
```mermaid
flowchart LR
  subgraph EXTS[外部]
    BQ[GA360 / BigQuery] ; GTM[GTM Server-side] ; MS[交易主機 MSSQL] ; CRM[CRM PostgreSQL] ; SA[神策] ; LLMG[LLM Gateway]
  end
  subgraph GKE[GKE asia-east1 / RKE2]
    subgraph P1[ingest pool]
      C1[Collector ×3 Go] ; C2[Debezium / Flink CDC]
    end
    subgraph P2[stream pool]
      F1[Flink JM ×2 + TM ×6–16]
    end
    subgraph P3[data pool]
      K1[Kafka ×3 KRaft] ; A1[Airflow] ; D1[dbt / Spark]
    end
    subgraph P4[olap pool]
      CH1[ClickHouse 2×2 + Keeper ×3]
    end
    subgraph P5[app pool]
      API[API / Semantic / Segment / OneID] ; MCP[Tool Gateway + Agent Runtime] ; WEB[Web]
    end
    subgraph P6[platform]
      KC[Keycloak] ; PR[Prometheus + OTel] ; GF[Grafana OSS]
    end
    ING[Ingress + Cloud Armor + IAP]
  end
  subgraph MG[GCP 託管]
    SQL[(Cloud SQL PG16)] ; MEM[(Memorystore Valkey)] ; GCS[(Cloud Storage CMEK)] ; KMS[Cloud KMS / Secret Manager] ; AR[Artifact Registry]
  end
  subgraph DOWN[下游]
    MA[MA] ; APP[營業員 App / AI Coach] ; DW[數倉 / 客服]
  end
  BQ --> C1 ; GTM --> C1 ; MS --> C2 ; CRM --> C2 ; SA --> K1
  C1 --> K1 --> F1 --> CH1 ; D1 --> CH1 ; CH1 --> API --> MCP --> LLMG
  API --> SQL ; API --> MEM ; CH1 --> GCS ; K1 --> MA ; MCP --> APP ; GCS --> DW
  ING --> C1 ; ING --> API ; ING --> WEB
```

### 數據流
```mermaid
flowchart LR
  S1[GA360 / GTM SS] --> I1[Collector / Importer] --> R[(Kafka raw.*)]
  S2[交易主機 MSSQL] --> I2[Debezium CDC] --> R
  S3[CRM / 神策 / MA] --> I3[dlt / 神策連接器] --> R
  R --> N[normalize] --> E[eventize 狀態機<br/>HMAC/FPE 代碼化] --> O[oneid-resolve] --> SS[sessionize] --> EV[(dmp.events)]
  SS --> RR[rt-rules CEP / rt-tags] --> TR[(dmp.triggers)] --> MA[MA / 營業員 App / AI Coach]
  EV --> L0[(ClickHouse L0 events)] --> L1[(L1/L2 MV)] --> L3[(L3 觸點 / L4 Bitmap 標籤)]
  EV --> ICE[(Iceberg on GCS 冷層)] -.雙向映射.-> DW[數倉 / 神策]
  L0 --> SEM[Semantic Compiler] --> PE[Policy Engine] --> API[Analytics / Segment / Profile API] --> UI[DMP Web]
  API --> GW[Tool Gateway MCP] --> AG[Insight Agents] --> IS[(Insight Store PG)] --> UI
  PE -.audit.-> AU[(audit_log + WORM)] --> GRA[Grafana 日誌統計]
```
