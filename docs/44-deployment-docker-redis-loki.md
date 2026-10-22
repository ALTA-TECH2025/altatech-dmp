# 44 — 部署方式調整：Docker Compose、Redis、Loki（2026-10-22 研發／運維決議）

> 狀態：**決議已定（研發＋運維，2026-10-22）**；授權處理（§3）待法務確認。
> 架構圖：`docs/diagrams/slides/slide-6-docker-deployment.png`（提案簡報與規格書 §1.2 同一張，放在既有架構圖之後）。

## 1. 決議

| # | 決議 | 原規劃 | 理由 |
|---|---|---|---|
| D-1 | 部署用 **Docker（Engine ＋ Compose v2）**，每台主機一份 compose；**第一階段不用 Kubernetes** | RKE2／GKE ＋ Helm umbrella chart ＋ Operators | 運維團隊熟 Docker、不熟 K8s；七人團隊沒有餘力同時維運一套 K8s |
| D-2 | 快取、佇列、登入狀態用 **Redis**（不換 Valkey） | Valkey 8 | 團隊熟 Redis；授權處理見 §3 |
| D-3 | 日誌用 **Loki**（＋ Grafana 查詢），不用 OpenSearch／VictoriaLogs／Cloud Logging | VictoriaLogs（或 OpenSearch） | 團隊熟 Loki；與既有 Grafana 同一介面 |

不變：6 個自建映像（core／ingest／batch／agent／flink-jobs／web）、ClickHouse、Kafka（KRaft）、PostgreSQL 16、Keycloak、OpenBao、Prometheus ＋ Grafana、Harbor、物件儲存（機房 SeaweedFS／GCP GCS）、機器檔位（docs/13）。映像本來就是 Docker 映像，應用程式碼不用改。

## 2. 部署拓樸（3 台版，D 檔正式／C 檔測試）

| 主機 | 容器 |
|---|---|
| 1 | dmp-web（nginx，TLS）、dmp-core、ClickHouse 副本 1、Keeper 1、Kafka 1、PostgreSQL 主庫、Redis 主 ＋ Sentinel |
| 2 | dmp-web、dmp-core、ClickHouse 副本 2、Keeper 2、Kafka 2、PostgreSQL 從庫、Redis 從 ＋ Sentinel |
| 3 | dmp-ingest、Flink（JobManager ＋ TaskManager，跑 docs/43 的 J1–J4）、Kafka 3、Keeper 3、Redis Sentinel、Keycloak、OpenBao、Prometheus ＋ Alertmanager、Grafana ＋ Loki、SeaweedFS（機房） |
| 每台 | Alloy（容器日誌 → Loki）、node-exporter、cAdvisor；RHEL／Rocky 9，SELinux enforcing |

- A 檔 6 台：ClickHouse 兩副本移到兩台專用機（olap），其餘同上分散到 4 台。
- 單機測試環境（docs/17 §5a 建議 1 台 4 vCPU／16 GB）：同一份 compose、每種服務 1 份、不開 Sentinel。
- GCP：Compute Engine VM 跑同一份 compose；可選擇改用託管 Cloud SQL（PostgreSQL）、Memorystore for Redis、GCS，compose 裡對應服務不啟動、連線字串改指託管端點。
- 入口：客戶既有負載平衡（機房 F5 等，GCP 內部 LB）→ 主機 1、2 的 nginx；不新增 keepalived 等元件。

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
| 主機故障時把服務搬到別台 | 不搬；靠服務本身的多副本：ClickHouse 雙副本、Kafka／Keeper 三節點、Redis Sentinel 自動切換；PostgreSQL 從庫手動 promote（運維手冊，RTO 30 分鐘） | PostgreSQL 切換變手動；故障主機上的 dmp-core 由另一台 nginx 承接 |
| Flink Operator（HA、升級） | Flink standalone：單一 JobManager ＋ TaskManager；checkpoint／savepoint 存物件儲存；JobManager 重啟後由啟動腳本從最近一次 checkpoint 續跑 | 中斷期間（分鐘級）即時路徑延遲上升；stream_writer 寫入冪等（docs/43 §3），不重算不重複；日批照常是權威 |
| CloudNativePG（備份、切換） | PostgreSQL 官方映像 ＋ 串流複寫 ＋ pgBackRest（WAL 歸檔、日備份到物件儲存） | 切換手動 |
| Altinity operator | ClickHouse 官方映像 ＋ 設定檔；備份用內建 `BACKUP TO S3`（日增量／週全量） | 擴 shard 要手動改設定 |
| Strimzi | Kafka 官方映像（KRaft，3 節點，replication 3） | 滾動升級手動逐台 |
| 滾動更新、自動擴縮 | 逐台 `compose pull && up -d`，維護窗口內；容量按 docs/13 檔位預留 | 沒有自動擴縮（本案負載可預估，影響小） |
| NetworkPolicy、Pod 安全 | 每台 Docker 網路只開必要埠＋主機防火牆（firewalld）；容器 `user` 非 root、`read_only`、`cap_drop: [ALL]`、`no-new-privileges`、SELinux 標籤 | 規則分散在每台，需納入部署檢查 |
| Secret 掛載 | OpenBao Agent 把密鑰寫成檔案掛進容器；設定只存 secret_ref（不變） | 無 |
| cert-manager | 客戶內部 CA 簽發，檔案掛載；到期前 30 天告警 | 換憑證手動 |
| ServiceMonitor／PrometheusRule | Prometheus 靜態設定（file_sd）；告警規則沿用 `ops/alerts.yaml` 同一份 | 無 |

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
| infra | `deploy/compose/`：主機角色 compose、.env 範本、啟動腳本（含 Flink 從最近 checkpoint 續跑）、CI `docker compose config` 驗證；backend/docker-compose.yml 的 valkey 映像換 redis:8（附 CVE 掃描結果） |
| infra | 監控：Loki ＋ Alloy ＋ cAdvisor 設定、Grafana 資料源、Prometheus file_sd |
| docs | docs/07、12、13、32、37 與簡報第 4、5 頁改為 Docker 版；Helm 標示不使用 |
| 法務 | Redis 8（AGPLv3）與 Loki（AGPLv3）列入白名單例外；核可後更新 AGENTS.md「禁止」的例外條文，再換部署映像 |
