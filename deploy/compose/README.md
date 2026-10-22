# deploy/compose — 第一階段機房正式部署（3 台 VM ＋ Docker Compose）

規格來源：docs/44 §2（部署拓樸）、§5（拿掉 K8s 的補位）。三台 VM 各跑一份 Compose，映像與 Helm 方案共用；Flink、Kafka 依 D-04（docs/45）第二階段才加。**請先在測試環境（3 台 16 核／128 GB）完整跑一輪再上正式。**

| VM | 應用 | 資料服務 | 維運 |
|---|---|---|---|
| VM-1（主） | dmp-web、dmp-core API、dmp-core worker（唯一一份背景排程）、dmp-batch（Airflow）；API 與 worker 共用 `APP_DATA` | ClickHouse 副本 r1、Keeper 1、PostgreSQL 主、Redis 主＋Sentinel、SeaweedFS master＋volume＋filer（S3 :8333） | node-exporter、cAdvisor、Alloy |
| VM-2（備援） | dmp-web／API 備援（平時不接流量）；worker、batch 只在接管時以 takeover profile 啟動 | ClickHouse 副本 r2、Keeper 2、PostgreSQL 備援、Redis 從＋Sentinel、SeaweedFS master＋volume | node-exporter、cAdvisor、Alloy |
| VM-3 | dmp-ingest（選配） | Keeper 3、Redis Sentinel 3、SeaweedFS master＋volume＋filer（S3） | Keycloak、Prometheus＋Grafana、Loki、備份作業、node-exporter、cAdvisor、Alloy |

SeaweedFS 三台都放（master ×3 Raft、每台一個 volume、複本 `001` 每檔 2 份落不同主機、filer＋S3 閘道在 VM1 與 VM3），任一台掉仍可讀寫；Redis 由 Sentinel 三票自動切換主從，應用連線字串仍指 VM1（Sentinel 切換後由運維改 .env 或改接 Sentinel，列為後續）。

網路：資料服務用 host 網路，三台以內網位址互連；應用容器走 bridge 網路對外發佈埠（80、8080、8082、8180）。F5／HAProxy 把 `https://dmp.…` 只送 VM1 的 80（VM2 設成 backup 成員，VM1 健康檢查失敗才送），`/auth` 指到 VM3 的 8180。第一階段是主備，不是雙活：雙活要等排程狀態搬進 PostgreSQL（見 §5）；TLS 在 LB 終結（內部 CA），容器間走內網明文——若資安要求容器間也要 TLS，改走 ClickHouse 9440／PostgreSQL ssl，列為待討論。

## 1. 主機前置（三台都做）

- OS：RHEL 9.x 或 Rocky 9.x；`dnf install docker-ce docker-compose-plugin`（Docker Engine 27、Compose v2）；Docker Desktop 不裝。
- 核心參數（Ansible 角色或手動）：`vm.max_map_count=262144`、`fs.file-max=2097152`、`net.core.somaxconn=4096`；關 swap 或 `vm.swappiness=1`。
- 目錄：資料碟掛 `/data`（SSD），`mkdir -p /data/dmp`；NAS 掛 `/mnt/nas/dmp-backup`（VM1 的 WAL 歸檔與 VM3 的備份都寫這裡）。VM1、VM2 都建 `/data/dmp/landing`（接收區：永豐 IT SFTP 平常落檔到 VM1，接管後改指 VM2；設唯讀給容器）與 `/data/dmp/app`（`APP_DATA`）。
- 密鑰：密鑰庫（OpenBao／HSM）在開機時把 secret_ref 對照檔掛到 `/run/secrets/dmp-secrets.json`（0400，root），程式只在執行節點看得到明文（docs/06 §4）。
- 防火牆（只開內網 10.10.0.0/24）：8123／9000／9009（ClickHouse）、9181／9234（Keeper）、5432、6379／26379（Redis／Sentinel）、8333／8380／8888／9333（SeaweedFS）、9100／9101／9363／9364（exporter、cAdvisor）、3100（Loki）、12345（Alloy）、9090／3000（Prometheus／Grafana，維運網段）、8080／80／8180／8082（LB 與維運）。
- `git clone` 到 `/opt/altatech-dmp`，`cd deploy/compose && cp .env.example .env`，填值；**每台的 `VM_HOST`、`KEEPER_ID`、`CH_REPLICA` 不同**（VM1=1/r1、VM2=2/r2、VM3=3/—）。`.env` 已 gitignore。

## 2. 首次啟動順序

```bash
# 三台先起 Keeper（要 3 台中至少 2 台在線才有法定人數）
./dmp.sh vm1 up keeper ; ./dmp.sh vm2 up keeper ; ./dmp.sh vm3 up keeper
# VM1：資料服務 → 應用
./dmp.sh vm1 up            # clickhouse（會跑 backend/clickhouse/schema.sql）、postgres（建 replicator、keycloak 庫）、redis、api、worker、batch、web
sudo crontab -u root common/scripts/crontab.vm1   # 每 15 分鐘把 APP_DATA 同步到 VM2（要先設好 VM1→VM2 的 SSH 金鑰）
# VM2：postgres 第一次啟動會從 VM1 pg_basebackup（空目錄才會做）；worker、batch 不會起（takeover profile）
./dmp.sh vm2 up
# VM3
./dmp.sh vm3 up            # keycloak、prometheus、loki、grafana；ingest 選配：COMPOSE_PROFILES=ingest ./dmp.sh vm3 up ingest
```

驗證：
```bash
./dmp.sh vm1 check                                   # 三台各跑一次：compose 設定與樣板渲染
curl -s http://10.10.0.11:8080/healthz               # api ok=true
docker compose -f vm1/docker-compose.yml exec clickhouse clickhouse-client -q "SELECT * FROM system.clusters WHERE cluster='dmp'"   # 兩個副本
docker compose -f vm2/docker-compose.yml exec postgres psql -U dmp -c "SELECT pg_is_in_recovery()"   # t
docker compose -f vm2/docker-compose.yml exec redis redis-cli -a "$REDIS_PASSWORD" info replication   # role:slave, master_link_status:up
docker compose -f vm3/docker-compose.yml exec sentinel redis-cli -p 26379 sentinel master dmp         # num-other-sentinels 2
curl -s http://10.10.0.11:9333/cluster/status | head                                            # SeaweedFS 三 master、有 leader
```
再用 `deploy/Makefile` 的 `make smoke` 口徑（寫 1 萬測試事件並查詢）跑一次；Grafana（VM3:3000）應看到三台 node、兩個 ClickHouse、三個 Keeper、兩個 dmp-api 都 up。

## 3. 日常維運

| 項目 | 做法 |
|---|---|
| 看狀態／日誌 | `./dmp.sh vmN ps`、`./dmp.sh vmN logs -f api`；跨台日誌在 Grafana → Loki（Alloy 收，標籤 host、container）；容器資源在 cAdvisor（:9101） |
| 升級 | 維護時段；`./common/scripts/upgrade.sh <tag>` 在 VM2 → VM1 → VM3 依序跑（先升備援；VM1 會先停 worker 讓排程收尾再一起換）；schema 變更走 migration 腳本，先在測試環境演練 |
| 備份 | VM3 主機 cron（`sudo crontab -u root common/scripts/crontab.vm3`）：ClickHouse 日增量 02:00、週全量週日 03:00 → SeaweedFS；PostgreSQL 日 pg_dump 01:30 → NAS；05:00 rclone 把 SeaweedFS 的備份桶同步到 NAS。WAL 歸檔由 VM1 主庫持續寫 NAS |
| 還原演練 | 每季一次：ClickHouse `RESTORE DATABASE dmp FROM S3(...)` 到測試環境；PostgreSQL `pg_restore` + WAL replay |
| 告警 | Prometheus 規則在 `common/prometheus/alerts.yml`；通知走 core API 的 `/v1/ops/notify`（Email／LINE，`backend/ops/notify.py`）；要接 Alertmanager 時在 `prometheus.yml.tmpl` 加 |

## 4. 故障處理（手冊，第一階段不做自動切換；隔離規則見 docs/44 §5.1）

**VM1 掛 → VM2 接管**（同一時間只能有一個可寫主庫、一個在跑的 worker）：
1. **先隔離 VM1**：確認 VM1 已關機，或已 `systemctl stop docker`，或已在防火牆切斷它對 5432、ClickHouse 的連線。無法確認就不接管（網路分區時 VM1 可能還活著）。
2. **PostgreSQL 升主**：`docker compose -f vm2/docker-compose.yml exec postgres pg_ctl promote`。
3. **改主庫名稱指向**：三台 `.env` 的 `PG_PRIMARY_HOST` 改成 VM2 位址，各台 `./dmp.sh vmN up` 重建應用容器（api、keycloak 等；連線字串都寫 `pg-primary`，不用改）。
4. **起背景作業**：確認 VM1 的 worker 已停後，`COMPOSE_PROFILES=takeover ./dmp.sh vm2 up worker batch`。排程狀態用最近一次同步的 `sched.db`（最多落後 15 分鐘，水位較舊的任務會重跑一次，寫入以批次 ID 去重）。
5. F5 健康檢查會自動把流量轉到 VM2；登入狀態在 PostgreSQL，使用者不必重登。永豐 IT 把 SFTP 落檔改指 VM2 的 `/data/dmp/landing`。
6. Redis：Sentinel 會自動把 VM2 升主，應用 `REDIS_URL` 改指 VM2（快取可丟，不急）。ClickHouse 由 r2 承接查詢與寫入。
7. **VM1 修好後不得自行回到主角色**：VM1 的 postgres 是 `restart: "no"`，復電不會自己起；api、worker 連不到庫也不會作業。先停掉 VM1 的 worker、batch（`./dmp.sh vm1 stop worker batch`），移除 VM1 的 `crontab.vm1`，再以 `pg_rewind` 對齊 VM2 改當從庫。計畫性回切排在維護窗口：反向同步 `APP_DATA`、VM1 promote、`PG_PRIMARY_HOST` 改回、VM2 停 takeover profile。

- **VM2 掛**：查詢不中斷，背景排程不受影響（在 VM1）；PostgreSQL 失去從庫、SeaweedFS 剩 2 master 仍可寫。VM2 回來後從庫自動追上；同步腳本這段期間會失敗，回來後自動補上。
- **VM3 掛**：Keeper、Sentinel、SeaweedFS master 都剩 2 票仍可用；監控、登入（Keycloak）中斷 → 優先救 Keycloak；S3 閘道改用 VM1 的 :8333。
- **任兩台同時掛**：Keeper 失去法定人數，ClickHouse 進唯讀；先救 Keeper。
- 部署驗收必做三個情境（docs/44 §5.1-5）：VM1 網路分區但仍存活、VM1 斷電後復電、計畫性回切；每個都要確認只有一個可寫主庫、一個在跑的 worker。

## 5. 已知限制與待討論

- 三台 VM 的規格（測試 16 核／128 GB、正式 64 核／256 GB）與 VM 配置待永豐資訊部門確認。
- 容器間未加密；Keycloak 對 AD 的 broker 設定、F5 健康檢查路徑（`/healthz`）、SFTP 接收區由永豐 IT 提供。
- `dmp-core` 的環境變數以 `backend/api/README.md` 為準，`DMP_OIDC_ISSUER`、`DMP_PUBLIC_URL`、`S3_*` 若程式尚未讀取，請研發對齊後再上正式。
- 模型設定目錄（`MODELS_DIR`，`backend/api/integration.py`）目前寫死在映像內、不能指到 `APP_DATA`，在畫面上新建的模型接管後要重建；排程狀態也還是 SQLite 加 15 分鐘同步。兩者改 PostgreSQL／可設定後才能做雙活（排在後續 PR）。
- Grafana OSS 法務已核可；Redis 8、Loki 為 AGPLv3 內部使用暫行例外，法務沒有說不行就照此規劃，若法務否決改 Valkey 8／VictoriaLogs（docs/44 §3、LICENSES.md）。
