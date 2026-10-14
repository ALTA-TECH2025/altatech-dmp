# 可視化建模與資料整合：MSSQL、BigQuery、PostgreSQL、神策（SensorsData）

> 版本 v0.2（2026-10-14）。定義「資料連結 → 可視化建模 → 欄位整合與清洗 → 載入事件／用戶／物品模型」的產品與技術設計。全部元件採 Apache-2.0／MIT／BSD／PostgreSQL 等寬鬆授權（見 07 文件授權審查）。

## 1. 目標
- 業務／資料工程師在 UI 上以**拖拉與表單**完成來源連結、實體對映、欄位映射與清洗規則，不寫程式。
- 與神策一樣「與資料建模域解耦」：來源怎麼接與事件模型長什麼樣分開；同一來源表可映射到多個實體。
- 倉表**雙向映射**：既有數倉的事實表可直接註冊為事件來源（不複製），本平台的事件表亦可被數倉以 Iceberg 讀取（只存一份）。

## 2. 支援的來源與接法

| 來源 | 批次（全量／增量） | 即時（CDC） | 連接器（授權） | 備註 |
|---|---|---|---|---|
| **Microsoft SQL Server** 2016+ | JDBC 增量（時間戳／自增鍵）、分頁拉取 | **Debezium SQL Server 連接器**（需開啟 CDC，`sys.sp_cdc_enable_table`）／Flink CDC SQL Server | Debezium（Apache-2.0）、Flink CDC（Apache-2.0）、mssql-jdbc（MIT） | 交易主機常見；若主機不允許 CDC，退回「回報檔 + 分鐘級拉取」 |
| **PostgreSQL** 12+ | JDBC 增量 | Debezium PostgreSQL（`pgoutput` 邏輯複寫）／Flink CDC | Debezium、pgjdbc（BSD-2） | 本平台控制面亦為 PostgreSQL |
| **BigQuery**（GA360 匯出、既有數倉） | **BigQuery Storage Read API**（Arrow，分片平行）；表分片 `events_YYYYMMDD` 自動偵測；增量以分區／日期 | 無 CDC；以 Fresh Daily／Streaming 表近即時 | Spark BigQuery Connector（Apache-2.0，Google）、google-cloud-bigquery（Apache-2.0） | 唯一出站連線；服務帳戶最小權限；GCP 部署時走 Private Google Access，不經公網 |
| **神策 SensorsData**（歷史事件，**一次性匯入**） | **查詢 API**（`/api/sql/query`）分日匯出，或由神策工程匯出 Parquet；匯入 Iceberg → ClickHouse | 不接增量：匯入完成後行為事件全部來自 GA360 | 神策協議為 Apache-2.0；映射器為本平台程式 | 事件名、屬性名、`identities` 直接映射；`$` 預置屬性對齊到 GA 事件命名 |
| 其他（MySQL、Oracle、檔案 GCS/S3、Kafka、HTTP API） | 依連接器 | Debezium 支援 MySQL／Oracle（Oracle 需 LogMiner，注意 Oracle 授權） | — | P1 |

技術底座：
- **CDC**：Debezium Server 或 Flink CDC（皆 Apache-2.0）→ Kafka `raw.<source>` → Flink 事件化。
- **批次**：Apache Spark（JDBC、BigQuery 連接器）或 Apache SeaTunnel（Apache-2.0，含 JDBC／Kafka／ClickHouse 連接器）；小表用 Python `dlt`（Apache-2.0）。**不採用 Airbyte**（平台 ELv2，部分連接器 ELv2）。
- **排程**：Apache Airflow（Apache-2.0）。
- **資料品質**：Great Expectations 或 Soda Core（皆 Apache-2.0）。
- **中繼資料／血緣**：OpenMetadata（Apache-2.0）或自建於 PostgreSQL + OpenLineage。

## 3. 可視化建模（UI 流程）

```
① 連線      ② 選表/查詢     ③ 實體對映        ④ 欄位映射          ⑤ 清洗規則        ⑥ 預覽驗證      ⑦ 排程/發佈
 MSSQL       dbo.ORDER_HIST  → 事件 order_*    ORD_NO→order_no     trim/upper          100 筆樣本       每 5 分鐘增量
 BigQuery    events_*        → 事件 ga.*       event_params→props  µs→ms, 時區         型別檢查         Fresh/Daily
 PostgreSQL  crm.customer    → 用戶 profile    CUST_ID→login_id    HMAC 代碼化         禁止欄位掃描     每日 02:00
 Sensors     歷史匯出 Parquet → 事件（同名）     identities 直通      $ 屬性對齊          字典比對         一次性
```

### 3.1 實體對映（Entity Mapping）
- 來源表／主題 → `event`（含狀態機事件化）、`user`（profile_set／set_once）、`item`（帳戶、商品、營業員…）、`identity`（ID 綁定）。
- 事件化規則（狀態表 → 事件）：以 UI 設定「狀態欄位 + 轉移 → 事件名」，例：`STATUS: NEW→FILLED ⇒ order_filled`。
- 一張表可產生多個實體：`ORDER_HIST` 同時產 `order_submit` 事件與 `item(account)` 的最近交易日屬性。

### 3.2 欄位映射（Field Mapping）
- 左側來源欄位（型別、樣本值、空值率），右側目標屬性（來自事件字典，含必填標記）；拖拉連線或自動建議（名稱相似度 + 型別 + 歷史映射）。
- 轉換函式庫（純函式，可組合）：型別轉換、時間（時區、µs/ms/s）、字串（trim/upper/regex）、數值（單位、四捨五入）、對照表（代碼 → 名稱）、條件（CASE）、合併／拆分欄位、JSON 展開（`event_params` UNNEST）、常數、來源標記。
- 目標屬性不存在 → 直接在此新增到事件字典（走審核）。

### 3.3 清洗規則（Cleansing）
| 類別 | 規則 | 違反處理 |
|---|---|---|
| 完整性 | 必填屬性非空、時間戳在 ±7 天內 | 進隔離表 `events_quarantine`，不丟 |
| 一致性 | 型別、枚舉值域（market ∈ TSE/OTC/US/HK/FUND）、金額 ≥ 0 | 修正（可配置預設值）或隔離 |
| 去重 | `event_id` 規則（來源主鍵 + 版本） | ReplacingMergeTree 最終去重 |
| 個資 | 禁止欄位掃描（身分證／手機／Email 正規式）、代碼化（HMAC／FPE）、分桶（年齡→年齡層） | **入站即拒絕**，告警 |
| 標準化 | 事件／屬性命名 snake_case、`$` 預置屬性對齊 | 自動轉換 |
| 參考完整性 | `account_no` 必須能解析到 one_id（或建立待解析） | 標記 `unresolved`，OneID 日批補 |

### 3.4 預覽與驗證
- 以 100–1,000 筆樣本跑完整管線，顯示：映射結果、被隔離筆數與原因、新事件／屬性、個資掃描結果、估算每日量。
- 發佈前自動產生「資料契約」（來源、實體、欄位、規則、負責人、SLA），存入中繼資料並進血緣。

### 3.5 排程與監控
- 批次：Airflow DAG 由模型定義自動生成（不手寫 DAG）；增量游標（時間戳／LSN）由平台保存。
- 即時：Debezium／Flink CDC 任務由平台以 K8s Operator 建立。
- 監控：延遲、筆數、隔離率、Schema drift（來源新增欄位 → 告警與建議映射）。

## 4. 神策歷史資料的一次性匯入

- 範圍：神策分析既有的歷史行為事件與使用者屬性，**只匯入一次**；匯入完成後行為事件全部改由 GA360（日批 + GTM 即時雙寫）供應，不再接神策增量，也不寫回神策。
- 流程：神策查詢 API 分日匯出（或請神策工程直接匯出 Parquet）→ GCS 暫存桶 → Spot 節點以 dlt／Spark 批次寫入 Iceberg raw → 映射器轉成本平台事件（事件名、屬性名、`identities` 幾乎 1:1；`$` 預置屬性對齊到 GA 事件命名）→ ClickHouse。
- 對齊：神策事件名與 GA360 事件名需在事件字典建立對照（例：神策 `$pageview` ↔ `ga.page_view`、`$AppViewScreen` ↔ `ga.screen_view`），讓歷史與新資料能在同一漏斗中銜接；`distinct_id`／`login_id` 轉為 `$identity_login_id`，`track_signup`／`track_id_bind` 轉為 OneID 綁定指令。
- 驗證：匯入後以每日事件量、去重人數與神策既有報表對帳（差異 < 1%），通過後刪除暫存桶。
- 時程與資源：約 50 億事件，Spot 池 4 台 1–2 天完成；期間暫停標籤日批。

## 5. 後端骨架對應
- `backend/integration/connectors.yaml`：來源連接器定義（類型、認證方式、能力：batch/cdc、預設轉換）。
- `backend/integration/models.example.yaml`：一個可視化建模產物的 YAML（UI 存檔格式；Flink／Spark 作業由此生成）。
- `backend/integration/transforms.py`：轉換函式庫（純函式，單元測試）。

## 6. 資料來源連線的鑑權設計（v0.4.2 檢視與優化）

### 6.1 檢視結果（優化前的缺口）

| 項目 | 優化前 | 問題 |
|---|---|---|
| 鑑權方式 | connectors.yaml 只列方法名稱（password／kerberos／azure_ad…） | 沒有欄位定義、沒有優先順序，UI 無從生成表單 |
| 密鑰存放 | 未定義 | 若實作時直接存在平台 PostgreSQL，等於多一份明文密鑰；稽核不會過 |
| 最小權限 | 未定義 | 現場常拿 db_owner／superuser 帳號接 CDC；一旦外洩影響範圍是整個交易庫 |
| 連線測試 | 無 | 連不上只回「失敗」，DBA 與平台互推；CDC 前置（cdc 啟用、wal_level、publication）無法事前驗證 |
| 輪替 | 無 | 密碼型憑證無到期管理；神策 token 匯入後沒有撤銷動作 |
| API | 無 integration 端點 | UI「新增連線」只是提示 |
| 審計 | 只有 sync／profile 類事件 | 密鑰讀取與連線測試不留痕 |

### 6.2 優化後的設計

1. **密鑰不入庫**：Connection 只存 `secret_ref`（`projects/…/secrets/…/versions/N`、OpenBao `kv/data/dmp/{env}/{conn}#key`、PoC 用 `secret://ns/name/key`）。UI 可「輸入一次」，API 直接寫入密鑰庫後只保留參照，回應與日誌不回顯；也可直接填既有參照。API 會掃描非機密欄位，疑似明文密鑰（長 token、PEM）一律 422。
2. **無密鑰優先，方法有排序**：
   - BigQuery：Workload Identity ＞ 服務帳號模擬（跨專案）＞ 金鑰檔（不建議，組織政策通常禁止）。
   - PostgreSQL：Cloud SQL IAM 驗證（1 小時 token）＞ mTLS 用戶端憑證 ＞ 帳密（機房可用 OpenBao database engine 發動態帳密，TTL 24 h）。
   - MSSQL：Kerberos（AD，keytab 以 CSI 掛到執行 Pod）＞ Entra ID 服務主體 ＞ SQL 帳密。
   - Kafka：mTLS ＞ SASL/SCRAM ＞ OAUTHBEARER；GCS／S3 相容：Workload Identity ＞ HMAC。
   - 神策：API token，一次性匯入完成後撤銷。
3. **最小權限清單**寫在 connectors.yaml（批次與 CDC 分開），連線測試逐項自檢；不接受 db_owner／superuser／專案層級 Viewer。
4. **連線測試是逐步診斷**：dns → tcp → tls → auth → privilege → cdc_prereq → sample_read → clock_skew；在哪一步失敗就回該步的原因與修法（例：TLS 失敗 → 提供 CA bundle，而不是關閉驗證）。測試在執行節點以 Workload Identity 取密鑰，API 節點與瀏覽器不接觸明文。儲存前必須通過測試。
5. **傳輸與網路**：TLS 1.2+ 且驗證伺服器憑證為強制（UI 不提供關閉）；私有 IP／Private Google Access；來源端放行 dmp 固定出口 IP（Cloud NAT／機房出口）。
6. **輪替**：密碼 90 天、服務帳號金鑰 90 天、API token 180 天、用戶端憑證依憑證效期；新舊版本並存 24 小時（執行中的作業用舊版跑完）；到期前 14 天通知憑證負責人；UI 顯示到期倒數與一鍵輪替。
7. **審計**：`integration.connection.created/updated/tested/rotated` 與 `secret.accessed`（含 provider、ref、on_behalf_of、指紋，不含明文）進 audit_log，日誌統計可看「誰在何時讀了哪個密鑰」。
8. **權限**：建立／修改／輪替連線需 `integration` 模組的設定權限（d）；分析師只可見連線清單（遮罩）。

對應檔案：`backend/integration/connectors.yaml`（secret_providers、auth_methods、least_privilege、rotation_policy、connection_test）、`backend/integration/auth.py`（Connection.validate／redacted、SecretResolver、test_connection、rotate，含自測）、`backend/openapi.yaml`（/v1/integration/*）、原型「數據整合 → 連線 → 連線設定／新增連線」抽屜。
