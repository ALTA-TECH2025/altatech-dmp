# 43. Flink 改列第一階段：範圍、架構、拆 PR 與時程

> 版本 v0.1 草案（2026-10-22）。起因：Windy 2026-10-22 指示「Flink 要改第一階段」。本文取代 docs/23 §1「第一階段不上 Flink、不以 Kafka 為主幹」的決定（決定確認後再回寫 docs/23、02、17、27、13、31、32）。
>
> **狀態**：D-F1 已確認採 A（Windy 2026-10-22「A ok」）；D-F2 延遲目標、D-F3 實作方式待確認，確認前契約只把延遲標為 proposed。

## 0. 要你決定的 3 件事

### D-F1　哪些路徑改走 Flink　✅ 已確認採 A（2026-10-22）

| | 範圍 | 差異 |
|---|---|---|
| **建議 A：即時路徑上 Flink** | Kafka 成為即時資料的主幹；四個 Flink 作業：J1 行為事件標準化、J2 交易事件化（狀態機）、J3 OneID 即時歸戶、J4 即時觸發（CEP）。**舊平台 70 億歷史一次性載入、GA360 日批（權威覆蓋）、對帳維持現有批次** | 需求表 C11（資料即時性）、E2（行為觸發）由「落差」改為可符合；歷史載入與日批不用重寫，已驗證的去重、個資守門、日彙總協定照用 |
| 替代 B：全部改走 Flink | 歷史載入、日批也進 Kafka／Flink | 一套路徑；但 70 億歷史用串流載入比批次慢、checkpoint 狀態大，已驗證的批次路徑要重驗一次 |
| 替代 C：只上即時觸發 | 只做 J4（CEP），其餘維持微批 | 工作量最小；行為事件仍 5–15 分鐘可查，C11 仍是落差 |

### D-F2　延遲目標

建議（從資料進到 Kafka 起算）：

- 即時觸發（J4）：P95 < 10 秒產生觸發事件；送到 MA／Webhook 再加對方處理時間。
- 行為事件可查詢：P95 < 1 分鐘。GA360 串流匯出本身是分鐘級（Google 端），這段不在我們控制內；`/v1/events` 直送（MA、LINE、短鏈）可以做到秒級。
- 交易事件：數據中台若提供 Kafka topic，P95 < 1 分鐘；若只能給批次表，仍是每 15 分鐘（瓶頸在來源）。

### D-F3　Flink 作業用什麼寫

| | 內容 | 差異 |
|---|---|---|
| **建議：Flink SQL 為主，Python UDF 重用現有規則** | 欄位轉換、事件名橋接、個資守門、內容雜湊 event_id 直接呼叫 `backend/integration/modeling.py`、`transforms.py` 的同一份程式（PyFlink）；視窗、join、CEP 用 Flink SQL | 個資守門與去重規則只有一份實作，批次與串流結果可以逐列比對；不需要 Java 人力 |
| 替代：Java DataStream | 規則用 Java 重寫 | 單機吞吐較高；但個資守門、event_id 等這兩週反覆修過的規則要再寫一份，兩邊容易不一致 |

本機驗證結果見 §4.1（PyFlink 能否原樣重用現有 Python 規則、額外開銷多少）。

## 1. 為什麼原本放第二階段、改了會影響什麼

docs/23 §1 當時的理由：所有來源都是批次或分鐘級、沒有秒級需求，第一階段團隊沒有 Flink 狀態管理專家，所以用「暫存 → ClickHouse SQL → 微批」取代。改列第一階段的影響：

| 項目 | 影響 |
|---|---|
| 需求表 | C11 資料即時性、E2 行為觸發、S6 即時情境：由「落差（微批）」改為可符合（以 D-F2 的延遲為準） |
| 架構 | Kafka 由選配改為即時主幹；新增 Flink 作業與 checkpoint 儲存；ClickHouse 寫入多一條串流入口 |
| 已驗證的批次路徑 | 歷史載入、GA360 日批、對帳不動（建議 A）；串流寫入必須沿用同一套去重與日彙總協定（§3.5） |
| 人力 | 研發 R1 增加約 30–40 人日；建議加 Flink 顧問 10–15 天（checkpoint、反壓、狀態大小調校） |
| 環境 | 測試環境要多跑 Kafka 3 個 broker、Flink 1 JM + 1–2 TM；單機測試建議由 4 vCPU／16 GB 提高到 8 vCPU／32 GB |
| 時程 | 見 §6；不動 UAT（2027-06-30）與 PD（2027-09-30），M3／M4 內容加重 |

## 2. 架構（建議 A）

```
來源                                   Kafka（Strimzi，KRaft，3 broker）        Flink（K8s Operator）                     ClickHouse
GA360 BigQuery 串流匯出 ─ Importer ─► dmp.raw.ga4 ──────────┐
MA／LINE／短鏈 ─ POST /v1/events ──► dmp.raw.api ──────────┼─► J1 行為標準化 ─► dmp.events ─┬─► 串流寫入器（小批，沿用日彙總協定）─► dmp.events
數據中台（有 Kafka 時）────────────► dmp.raw.dwh ─► J2 交易事件化 ─────────────► dmp.events ─┤                                         ├─ MV／日彙總
                                                    J1／J2 隔離列 ──────────────► dmp.quarantine（已去個資）                         ├─ dmp.id_map、oneid_backfill
                                                    J3 OneID 即時歸戶（讀 dmp.events 登入／綁定）─► dmp.identity ─► id_map／覆蓋表 ┘
                                                    J4 即時觸發 CEP（讀 dmp.events）─► dmp.triggers ─► 推送服務 ─► MA／Webhook

維持批次（不經 Flink）：舊平台歷史一次性載入 → 暫存 Parquet → ClickHouse SQL；GA360 日批 events_（權威，覆蓋串流）；數據中台批次表；三方對帳；分群／標籤例行計算
```

| Topic | 內容 | 保存 | 分區鍵 |
|---|---|---|---|
| `dmp.raw.ga4`、`dmp.raw.api`、`dmp.raw.dwh` | 原始列（接入時已拒收 L4 欄位，見 §3.1） | 7 天 | distinct_id |
| `dmp.events` | 標準化後的事件（與 ClickHouse `dmp.events` 欄位相同） | 7 天 | one_id（無則 distinct_id） |
| `dmp.quarantine` | 隔離列（只含去個資後的欄位與原因碼） | 30 天 | — |
| `dmp.identity` | 綁定與歸戶變更 | 7 天 | distinct_id |
| `dmp.triggers` | 觸發事件（規則 ID、one_id、觸發時間、證據事件 ID） | 7 天 | one_id |

容量沿用 docs/13（Kafka 7 天約 0.25 TB、尖峰月 0.8 TB；`partitions.events: 48`）。

## 3. 四個作業的設計

### 3.1 J1 行為事件標準化

- 輸入 `dmp.raw.ga4`／`dmp.raw.api`，輸出 `dmp.events`、`dmp.quarantine`。
- 每列呼叫現有規則：事件名橋接（別名 → 正式事件名）、時間轉 UTC、欄位轉換、**個資守門**（字典標為 hash／drop 的欄位與巢狀個資整個丟；手機／身分證樣式命中整列隔離，隔離區只存去個資後的列與原因碼）、**內容雜湊 event_id**（時間與屬性全同才算同一筆）。
- 無狀態；平行度依分區數。去重不在 Flink 做（見 §3.5），所以重送、重啟都不會多算。

### 3.2 J2 交易事件化

- 數據中台增量列 → 業務事件（委託、成交、部分成交、失敗、刪單）。以委託單號為鍵的狀態機（ValueState），狀態保留 7 天後清除。
- 數據中台若沒有 Kafka：J2 不啟用，交易照批次進來（現狀）。

### 3.3 J3 OneID 即時歸戶

- 讀 `dmp.events` 的登入／綁定事件，以 distinct_id 為鍵；首次關聯即時寫 `dmp.identity`，下游更新 `id_map` 與 `oneid_backfill` 覆蓋表（查詢即時生效）。
- 規則沿用 `semantic/oneid_rules.yaml` 與 `backend/oneid/attribution.py`（20 個歸戶情境自測）；登入後 7 天匿名事件改寫仍由覆蓋表 + 每 10 分鐘回寫完成，J3 只是把「發現綁定」從 10 分鐘縮到秒級。
- 人工合併／拆分、DSAR 不進 Flink（審批流程不變）。

### 3.4 J4 即時觸發（CEP）

- Flink SQL `MATCH_RECOGNIZE`；規則由現有觸發契約（`/v1/triggers*`）編譯成 SQL，審批通過才部署。
- 輸出 `dmp.triggers`，推送服務沿用現有 Webhook 全流程（簽章、重試、DLQ、交付紀錄、每人限發）。
- 觸發只帶 one_id 與規則 ID，不帶個資；MA 端用 one_id／客戶代碼對應。

### 3.5 寫入 ClickHouse：只有一條寫入規則

Flink 不直接寫 ClickHouse。`dmp.events` 由一個**串流寫入器**（Python，每 1–5 秒或每 N 筆一小批）寫入，沿用批次已驗證的：

- 入倉只追加、倉裡已有的 event_id 保留第一筆（`aggregates.dedupe`）；
- 日彙總標記協定（`aggregates.begin`／touch → 寫入 → landed → settle），中斷、重送、併發、舊版升級都會補完；
- 個資守門在 J1 已做，寫入器再做一次輕量檢查（防止有人直接寫 topic）。

這樣批次與串流共用同一個寫入路徑，「同一筆資料走兩條路結果不同」的風險只剩 J1 的規則；而 J1 呼叫的是同一份 Python 規則（D-F3）。寫入器以 Kafka consumer group 提交 offset：寫入成功才提交，重啟後重送由 event_id 去重吸收。

### 3.6 批次與串流的一致性守門（CI 必過）

- **批流一致性測試**：同一組合成輸入同時走批次（`modeling.apply`）與 J1（PyFlink MiniCluster），逐列比對 event、event_id、隔離與原因碼，必須完全相同。
- 端到端：合成事件 → Kafka（測試用單 broker）→ J1 → 寫入器 → ClickHouse，驗筆數、去重、日彙總 = 明細。
- GA360 日批覆蓋串流：同一天先串流再日批，結果與只跑日批相同。

## 4. 驗證與技術選型

### 4.1 本機驗證（PyFlink 重用現有 Python 規則）

2026-10-22 在本機（4 vCPU、OpenJDK 21、Python 3.12）以合成資料驗證，腳本未進 repo：

| 項目 | 結果 |
|---|---|
| 版本 | apache-flink **2.3.0**（PyPI 最新）、Python 3.12、Java 21 直接可用，沒有 Java 模組相容警告；套件安裝約 820 MB（含 Beam、pyarrow、pandas），要算進 flink-jobs 映像大小 |
| 重用現有規則 | 21 列合成輸入（一般列、個資列、完全重複、只差時間 1 秒）經 Table API 純量 UDF（process／thread 兩種模式）與 DataStream ProcessFunction，呼叫 repo 的 `modeling.apply`、`quarantine_row`、`to_ch_row`、`canon_json`，**三種寫法與批次 `apply()` 逐列完全相同**；重複列同 event_id、只差時間 1 秒則不同；個資列依模型設定被拒收或隔離，輸出內容沒有任何個資字串 |
| 有狀態運算 | KeyedProcessFunction 以 distinct_id 為鍵、ValueState 保存首次登入對應，hashmap 與 RocksDB 兩種狀態後端的 checkpoint 都完成，取消後從 checkpoint 還原狀態正確 |
| 吞吐（5 萬列） | 瓶頸是規則本身的 Python 運算（約 0.26 ms／列，單核約 3,800 列／秒），不是 Flink 傳輸；process 模式平行度 4 時約 6,000 列／秒（扣除作業啟動約 10,900）；thread 模式不會隨平行度增加。尖峰 7,000 事件／秒（docs/13）需要 J1 平行度約 4–8，正式檔位的 TM slot 足夠 |

上線前要固定的設定（寫進 F2）：每個運算子給固定 `uid()`（否則從 checkpoint 還原會失敗）；`python.executable` 指到映像內的 Python；Python 工作程序用 process 模式；`python.fn-execution.bundle.size` 調小（預設值讓一次 checkpoint 等了 24 秒，調成 20 後約 0.2 秒）。

結論：D-F3 建議的作法可行——批次與串流共用同一份個資守門與 event_id 規則，結果可逐列比對。

### 4.2 元件與授權

| 元件 | 版本（建議） | 授權 | 備註 |
|---|---|---|---|
| Apache Flink（含 PyFlink） | 2.3（§4.1 已驗證；Operator 版本需相容 2.x） | Apache-2.0 | LICENSES.md 已列（原寫 1.19，確認後更新版本並附 CVE 檢查） |
| Flink Kubernetes Operator | 1.10 以上 | Apache-2.0 | Chart.yaml 已列 |
| Apache Kafka（Strimzi） | 3.8（KRaft） | Apache-2.0 | values.yaml 已有 `kafka.operator` |
| checkpoint 儲存 | GCS／機房 S3 相容儲存 | — | values.yaml `flink.checkpointDir` 已有 |

不引入 Schema Registry：訊息用 JSON，欄位與 ClickHouse `dmp.events` 相同，schema 寫成契約檔並在寫入器驗證。

## 5. 部署與營運

- Helm：啟用 `kafka.operator`、`flink.operator`（values 已有，第一階段原本關閉）；一個 FlinkDeployment（session 模式）跑 J1–J4。
- 監控：Flink 指標（checkpoint 時長與失敗、反壓、延遲）與 Kafka consumer lag 接 Prometheus；告警規則加進 `backend/ops/alerts.yaml`；資料流與效能頁加「Kafka 延遲／Flink 作業狀態」。
- 故障時的降級：Flink 停擺時，GA360 日批與數據中台批次照常（權威資料不受影響），只有即時性下降；觸發規則暫停並告警。

## 6. 拆 PR 與時程

每個 PR < 400 行、一件事；契約先行。

| PR | 內容 | 依賴 | 估計 |
|---|---|---|---|
| F0 | 本文（規劃） | — | — |
| F1 contract | Kafka topic 與訊息 schema 契約檔；觸發事件格式；`/v1/ops` 加 Kafka／Flink 狀態欄位 | D-F1 | 2 人日 |
| F2 | Flink 本機與 CI 測試環境：PyFlink 版本釘選、MiniCluster 測試骨架、LICENSES 與 CVE | D-F3 | 3 人日 |
| F3 | J1 行為標準化 + 批流一致性測試 | F1、F2 | 6 人日 |
| F4 | 串流寫入器（Kafka → ClickHouse，沿用去重與日彙總協定）+ 端到端 | F1 | 6 人日 |
| F5 | J3 OneID 即時歸戶 | F3、F4 | 6 人日 |
| F6 | J4 即時觸發（CEP 規則編譯、審批後部署、接推送服務） | F3、F4 | 8 人日 |
| F7 | J2 交易事件化（數據中台有 Kafka 才啟用） | F3、F4 | 5 人日 |
| F8 | 部署與監控：Helm 啟用、checkpoint、告警、降級演練 | F3–F6 | 5 人日 |
| F9 | 前端：觸發規則頁（目前占位）、資料流頁加 Kafka／Flink 狀態；示範站 | F6、F8 | 5 人日 |

合計約 46 人日（不含 F0），主要落在研發 R1；建議另加 Flink 顧問 10–15 天審 checkpoint、狀態與反壓設定。對應里程碑（docs/31 時程 v2.1）：F1–F4 進 M2（第 7 週前），F5–F6 進 M3（GA360 接入與並行期一起驗），F7–F9 進 M4。UAT、PD 日期不變。

## 7. 風險

| 風險 | 對策 |
|---|---|
| 團隊沒有 Flink 狀態管理經驗（docs/23 §6） | 作業以無狀態 J1 起步；有狀態的 J2／J3 狀態都設 TTL；顧問審查；checkpoint 與重啟演練寫進 F8 驗收 |
| 批次與串流結果不一致 | 一份規則（D-F3）＋批流一致性測試在 CI 必過（§3.6）；ClickHouse 只有一條寫入路徑（§3.5） |
| PyFlink 效能不足 | §4.1 量額外開銷；不足時只把熱點規則改為 Flink SQL 內建函式，個資守門仍呼叫同一份程式 |
| 測試環境資源 | Kafka／Flink 在單機測試環境以最小配置跑（1 broker、1 TM）；正式檔位沿用 docs/13 |
| 個資經由 Kafka 外流 | 接入端就拒收 L4；raw topic 只保存 7 天、加密、限存取；隔離 topic 只存去個資後的列 |

## 8. 決定後要回寫的文件

docs/23 §1–2、§6（第一階段架構改為「批次 + 即時路徑 Flink」）、docs/02 第一階段註記、docs/17 規格書（架構、5.1、5.12、§5a）、docs/27 需求表 C11／E2／S6、docs/13 容量（測試環境檔位）、docs/31 時程與人力、docs/32 開源元件清單（Flink、Kafka 由「規劃中」改「第一階段使用」）、系統圖 `docs/diagrams/build.py` 的 STATUS 表。
