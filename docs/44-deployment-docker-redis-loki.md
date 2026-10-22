# 44 — 部署方式調整：Docker Compose、Redis、Loki（2026-10-22 研發／運維決議）

> 狀態：**決議已定（研發＋運維，2026-10-22）**；授權處理（§3）待法務確認。
> 架構圖：`docs/diagrams/slides/slide-6-docker-deployment.png`（提案簡報與規格書 §1.2 同一張，放在既有架構圖之後）。

## 1. 決議

| # | 決議 | 原規劃 | 理由 |
|---|---|---|---|
| D-1 | 部署用 **Docker（Engine ＋ Compose v2）**，每台主機一份 compose；**第一階段不用 Kubernetes** | RKE2／GKE ＋ Helm umbrella chart ＋ Operators | 運維團隊熟 Docker、不熟 K8s；七人團隊沒有餘力同時維運一套 K8s |
| D-2 | 快取、佇列、登入狀態用 **Redis**（不換 Valkey） | Valkey 8 | 團隊熟 Redis；授權處理見 §3 |
| D-3 | 日誌用 **Loki**（＋ Grafana 查詢），不用 OpenSearch／VictoriaLogs／Cloud Logging | VictoriaLogs（或 OpenSearch） | 團隊熟 Loki；與既有 Grafana 同一介面 |

不變：6 個自建映像（core／ingest／batch／agent／flink-jobs／web）、ClickHouse、Kafka（KRaft）、PostgreSQL 16、Keycloak、OpenBao、Prometheus ＋ Grafana、Harbor、物件儲存（機房 SeaweedFS／GCP GCS）、機器檔位（docs/13）。映像本來就是 Docker 映像；**單一 API 實例**部署不用改程式。兩個 API 同時接流量（雙活）與背景作業多份執行都**需要先改程式**，見 §5a；完成前採主備。

## 2. 部署拓樸（3 台版，D 檔正式／C 檔測試）

| 主機 | 容器 |
|---|---|
| 1 | dmp-web（nginx，TLS）、dmp-core API（唯一接流量）＋ dmp-core worker（唯一開背景作業）、ClickHouse 副本 1、Keeper 1、Kafka 1、PostgreSQL 主庫、Redis 主 ＋ Sentinel |
| 2 | dmp-web、dmp-core API 備援（平時不接流量、不開背景作業）、ClickHouse 副本 2、Keeper 2、Kafka 2、PostgreSQL 從庫、Redis 從 ＋ Sentinel |
| 3 | dmp-ingest、Flink（JobManager ＋ TaskManager，跑 docs/43 的 J1–J4）、Kafka 3、Keeper 3、Redis Sentinel、Keycloak、OpenBao、Prometheus ＋ Alertmanager、Grafana ＋ Loki |
| 每台 | SeaweedFS master ＋ volume（機房物件儲存，見下）、Alloy（容器日誌 → Loki）、node-exporter、cAdvisor；RHEL／Rocky 9，SELinux enforcing |

- A 檔 6 台：ClickHouse 兩副本移到兩台專用機（olap），其餘同上分散到 4 台。
- 單機測試環境（docs/17 §5a 建議 1 台 4 vCPU／16 GB）：同一份 compose、每種服務 1 份、不開 Sentinel。
- GCP：Compute Engine VM 跑同一份 compose；可選擇改用託管 Cloud SQL（PostgreSQL）、Memorystore for Redis、GCS，compose 裡對應服務不啟動、連線字串改指託管端點。
- 入口：客戶既有負載平衡（機房 F5 等，GCP 內部 LB）→ 主機 1 的 nginx，主機 2 設為備援（backup）；不新增 keepalived 等元件。雙活要等 §5a 完成。
- **SeaweedFS（機房物件儲存）三台都放**，不是只在主機 3：master ×3（Raft，任兩台在就能寫）、每台一個 volume server 用本機 HDD、複本設定 `001`（每個檔案 2 份、落在不同主機）、filer ×2（主機 1、3；中繼資料存 PostgreSQL 控制面，filer 本身無狀態）。
  - 可用容量 ≈ 原始 HDD ÷ 2，再保留約 15% 空間：D 檔 3 台 × 2 × 8 TB = 48 TB 原始 → 約 20 TB 可用。docs/13 寫的「約 16–24 TB」是按 K8s 版估的，M1 依實機與實際複本設定重算，不直接沿用。
  - 任一台損毀：另一份複本仍在、master 剩 2 票可寫；損毀主機上的 Flink 由其他主機以同一映像啟動，從物件儲存上最近一次 checkpoint 續跑（docs/43 §5）。
  - **備份要有叢集外的第二份**：ClickHouse、PostgreSQL 備份先寫 SeaweedFS，再每日複製到獨立目的地（客戶既有備份系統／NAS／異地；GCP 為另一個區域的 GCS bucket）。同一個 SeaweedFS 叢集內的備份不算數，因為叢集整個損毀時會一起不見。

## 3. Redis 授權怎麼處理（需法務確認）

Redis 的授權歷史：7.2 以前 BSD-3；7.4 起 RSALv2／SSPLv1 雙授權（都不是 OSI 開源）；**Redis 8 起加回 AGPLv3，變成 RSALv2／SSPLv1／AGPLv3 三選一**。

| 選項 | 做法 | 優點 | 風險／代價 |
|---|---|---|---|
| **A（建議）** | 用 Redis 8 官方映像，**選 AGPLv3**；不修改、只在中台內部使用、不對外提供 Redis 服務 | 團隊熟、版本持續維護；與既有 Grafana OSS 例外同一套規則（LICENSES.md） | AGPL §13 只在「修改後對外提供網路服務」才要求公開原始碼；我們不改、終端使用者也不直接連 Redis，實務風險低，但白名單外，需法務核可例外 |
| B | 釘在 Redis 7.2（最後的 BSD-3 版本） | 授權最乾淨 | 7.2 已不是現行主線，安全修補期有限；三年合約內勢必要升級，屆時又回到 A |
| C | 改用 Valkey 8（BSD-3） | 授權乾淨；協定、指令、redis-cli 全相容 | 團隊決議不換；留作退路 |
| GCP | Memorystore for Redis | 授權由 Google 處理 | 只適用 GCP 部署 |

另：RSALv2 其實也允許「內部使用」，只禁止把 Redis 當託管服務賣給第三方；但它不是 OSI 授權，會破壞「全部 OSI 授權」的對外說法，所以選 AGPLv3 而不是 RSALv2。

**與 repo 規則的衝突**：`AGENTS.md`「禁止」寫明不得引入 AGPL／SSPL／BSL／ELv2 授權的執行期元件，目前唯一例外是 Grafana OSS（LICENSES.md）。Redis 8 與 Loki 都是執行期元件，所以**法務核可前，部署檔與 CI 不得改用 Redis 8 映像**（現有 `backend/docker-compose.yml` 的 Valkey 維持不動；Valkey 與 Redis 協定相容，運維操作方式相同）。核可後同一個 PR 更新：AGENTS.md 例外條文、LICENSES.md 備註、compose 映像。

落實：客戶端只用 redis-py（MIT）、只用 Redis 與 Valkey 共通的指令（不用 Redis 8 專有模組功能），連線設定 `REDIS_URL`；萬一法務不核可，換 Valkey 只改映像，不改程式。

## 4. Loki 授權

Loki 3.x 為 AGPLv3，與 Grafana OSS 同一家、同一種處理：官方映像、不修改、僅內部使用、不嵌入產品對外提供；列入 LICENSES.md 例外，與 Redis 一起送法務。日誌收集用 Grafana Alloy（Apache-2.0）；不用 Promtail（AGPL，且官方已停止維護）。

## 5. 拿掉 Kubernetes 之後的缺口與處置

| 原本由 K8s／Operator 做的事 | Docker 版做法 | 代價 |
|---|---|---|
| 程序掛掉自動重啟 | `restart: unless-stopped` ＋ `healthcheck` | 無 |
| 主機故障時把服務搬到別台 | 不搬；靠服務本身的多副本：ClickHouse 雙副本、Kafka／Keeper 三節點、Redis Sentinel 自動切換；PostgreSQL 從庫手動 promote（運維手冊，RTO 30 分鐘） | PostgreSQL 切換變手動；主機 1 故障時負載平衡改送主機 2 的備援 API（使用者需重新登入、進行中的 SSO 登入要重來），運維手動啟動主機 2 的 worker（§5a） |
| Flink Operator（HA、升級） | Flink standalone：單一 JobManager ＋ TaskManager；checkpoint／savepoint 存物件儲存；JobManager 重啟後由啟動腳本從最近一次 checkpoint 續跑 | 中斷期間（分鐘級）即時路徑延遲上升；stream_writer 寫入冪等（docs/43 §3），不重算不重複；日批照常是權威 |
| CloudNativePG（備份、切換） | PostgreSQL 官方映像 ＋ 串流複寫 ＋ pgBackRest（WAL 歸檔、日備份到物件儲存） | 切換手動 |
| Altinity operator | ClickHouse 官方映像 ＋ 設定檔；備份用內建 `BACKUP TO S3`（日增量／週全量） | 擴 shard 要手動改設定 |
| Strimzi | Kafka 官方映像（KRaft，3 節點，replication 3） | 滾動升級手動逐台 |
| 滾動更新、自動擴縮 | 逐台 `compose pull && up -d`，維護窗口內；容量按 docs/13 檔位預留 | 沒有自動擴縮（本案負載可預估，影響小） |
| NetworkPolicy、Pod 安全 | 每台 Docker 網路只開必要埠＋主機防火牆（firewalld）；容器 `user` 非 root、`read_only`、`cap_drop: [ALL]`、`no-new-privileges`、SELinux 標籤 | 規則分散在每台，需納入部署檢查 |
| Secret 掛載 | OpenBao Agent 把密鑰寫成檔案掛進容器；設定只存 secret_ref（不變） | 無 |
| cert-manager | 客戶內部 CA 簽發，檔案掛載；到期前 30 天告警 | 換憑證手動 |
| ServiceMonitor／PrometheusRule | Prometheus 靜態設定（file_sd）；告警規則沿用 `ops/alerts.yaml` 同一份 | 無 |

### 5.1 手動接管時的隔離規則（避免兩個 worker 或兩個可寫主庫）

1. **接管前先隔離舊主機**：確認主機 1 已關機，或已停掉它的 Docker 服務，或已在防火牆切斷它對 PostgreSQL（5432）、Kafka、ClickHouse 的連線。無法確認時不接管（網路分區時，主機 1 可能還活著）。
2. **應用連 PostgreSQL 一律用名稱 `pg-primary`**（各主機 hosts／內部 DNS），不寫主機 IP；接管時把 `pg-primary` 改指主機 2，從庫 promote。
3. **舊主庫恢復後不得自行回到主角色**：PostgreSQL 主庫容器用 `restart: "no"`，程序崩潰由告警通知運維依手冊重啟（RTO 30 分鐘內）。主機 1 復電後，主庫先以 `pg_rewind` 對齊新主庫、改當從庫，之後才可在維護窗口做計畫性回切。
4. **舊 worker 不得自行恢復**：主機 1 的 worker 在接管後要停用（`docker compose stop` 並取消開機啟動），直到回切。#73（主控租約）合併後，即使兩份 worker 同時起來也只有一份會跑，但仍維持此規則。
5. 驗收情境（部署驗收必做）：主機 1 網路分區但仍存活、主機 1 斷電後復電、計畫性回切；每個情境都要確認同一時間只有一個可寫主庫、一個在跑的 worker。

## 5a. 雙活前置工作（目前程式不支援，審查已重現）

| 缺口 | 現況 | 要做的 | 驗收 |
|---|---|---|---|
| 登入 session 跨 API 實例 | `backend/api/main.py` 每個程序建立自己的 `SessionMemory()`；OIDC 登入前狀態 `AuthService.login_states` 也在程序內（`backend/auth/api.py`）。同一個 cookie 打到另一個 API 會回 401 | 共用 session store 與 login-state adapter（Redis 或 PostgreSQL 控制面） | 跨節點的 SSO callback、cookie、登出、主機故障切換不需重新登入 |
| 背景作業單一執行 | `start_ticker` 直接呼叫各服務 tick（例：看板定時寄送先讀 last_run、寄完才更新），沒有跨程序 claim／租約；兩份 worker 同時跑會重複寄送 | 每類例行作業加分散式 claim／租約（PostgreSQL 列鎖或租約表） | 兩份 worker 併發同一排程只執行一次；持有者中斷後租約到期由另一份接手 |
| 同步排程狀態與產物（**單一 API ＋ worker 拆成兩個容器就需要**） | 同步任務排程存在 SQLite（`DMP_SCHED_DB`，未設時預設各容器的 `/tmp/dmp-sched.db`，`backend/api/app.py`）；API 建的任務寫進 API 那份，worker 只輪詢自己那份，排程會靜默不執行。建模產物寫在映像內的 `backend/integration/models.generated`（`MODELS_DIR` 固定），看板寄送與資料輸出產物寫本機 `DMP_EXPORT_DIR` | 排程器改用 PostgreSQL（與控制面同庫）；`MODELS_DIR`、`DMP_EXPORT_DIR` 改成可設定，並放到物件儲存或共享持久目錄 | API 建立任務 → worker 執行 → API 可下載產物；接管到主機 2 後，同步水位與已產生的檔案仍可用 |

完成前的暫行規則（單一主備也適用）：API 與 worker 放同一台、掛同一個持久目錄（例 `/data/dmp`），`DMP_SCHED_DB=/data/dmp/sched.db`、`DMP_EXPORT_DIR=/data/dmp/exports`，建模產物目錄也指到這裡；這個目錄每 15 分鐘同步到主機 2（接管時最多遺失 15 分鐘內新建的任務與產物，需在驗收時確認可接受）。

完成前的部署規則：負載平衡只送主機 1 的 API；只有主機 1 的 `DMP_ROLE=worker` 開背景作業，兩個 API 都不開 `DMP_TICK`；主機 1 故障時改送主機 2 的 API（使用者重新登入）、運維手動啟動主機 2 的 worker。

## 6. 其他架構問題（這次一併檢查）

1. **Flink 已改第一階段（docs/43，D-F1 = A）**：舊圖仍標第二階段；docs/43 的部署段落（Strimzi、Flink K8s Operator、Helm 啟用）要改成本文件的 standalone 做法，F8 工作量不變。
2. **Redis 用途要定清楚**：目前程式只有登入前狀態（TTL 10 分鐘）規劃用 Redis，其餘都在 PostgreSQL；第一階段 Redis 只當登入狀態、快取、輕量佇列，不放任何不能遺失的資料（AOF 每秒落盤）。
3. **單一 Keycloak、OpenBao**：3 台版放在主機 3 單一份；主機 3 故障時已登入的 session 不受影響，但新登入失敗。正式環境建議 Keycloak 主機 1、3 各一份（共用 PostgreSQL），OpenBao 用內建 Raft 三節點；列入待確認。
4. **監控也在主機 3**：主機 3 故障時告警跟著失效；Alertmanager 另在主機 1 放一份，或由客戶既有監控探測 nginx 健康檢查。
5. **Helm chart 與 values**：保留在 repo 但標示「第一階段不使用」；部署套件改為 `deploy/compose/`（每種主機角色一份 compose ＋ .env 範本），由後續 infra PR 提供，並在 CI 驗證 `docker compose config`。
6. **RHEL 9 與 Docker**：Red Hat 預設是 Podman；Docker CE 官方提供 RHEL 9 套件庫。若客戶資安只允許 RHEL 原生套件，compose 檔可用 Podman ＋ podman-compose 執行（需另做相容測試）。
7. **對外文件一致性**：docs/07、12、13、32、37 與提案簡報第 4、5 頁仍寫 K8s／Helm／Valkey／VictoriaLogs，後續逐份改；在改完前以本文件為準。

## 7. 後續 PR（依序）

| PR | 內容 |
|---|---|
| backend | 雙活前置（§5a）：共用 session／login-state（#72）、背景作業主控租約（#73）、排程器改 PostgreSQL 與產物目錄可設定（待開），含跨節點驗收 |
| infra | `deploy/compose/`：主機角色 compose、.env 範本、啟動腳本（含 Flink 從最近 checkpoint 續跑）、CI `docker compose config` 驗證；backend/docker-compose.yml 的 valkey 映像換 redis:8（附 CVE 掃描結果） |
| infra | 監控：Loki ＋ Alloy ＋ cAdvisor 設定、Grafana 資料源、Prometheus file_sd |
| docs | docs/07、12、13、32、37 與簡報第 4、5 頁改為 Docker 版；Helm 標示不使用 |
| 法務 | Redis 8（AGPLv3）與 Loki（AGPLv3）列入白名單例外；核可後更新 AGENTS.md「禁止」的例外條文，再換部署映像 |
