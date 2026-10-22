# deploy/compose — 第一階段機房正式部署（3 台 VM ＋ Docker Compose）

規格來源：docs/07 §1.3。三台 VM 各跑一份 Compose，映像與 Helm 方案共用。**請先在測試環境（3 台 16 核／128 GB）完整跑一輪再上正式。**

| VM | 應用 | 資料服務 | 維運 |
|---|---|---|---|
| VM-1 | dmp-web、dmp-core API | ClickHouse 副本 r1、Keeper 1、PostgreSQL 主、Redis 主 | node-exporter、Promtail |
| VM-2 | dmp-core worker（唯一一份背景排程）、dmp-batch（Airflow）、dmp-web／API 第二份 | ClickHouse 副本 r2、Keeper 2、PostgreSQL 備援、Redis 從、SeaweedFS（S3） | node-exporter、Promtail |
| VM-3 | dmp-ingest（選配） | Keeper 3 | Keycloak、Prometheus＋Grafana、Loki、備份作業、node-exporter、Promtail |

網路：資料服務用 host 網路，三台以內網位址互連；應用容器走 bridge 網路對外發佈埠（80、8080、8082、8180）。F5／HAProxy 把 `https://dmp.…` 指到 VM1、VM2 的 80，`/auth` 指到 VM3 的 8180；TLS 在 LB 終結（內部 CA），容器間走內網明文——若資安要求容器間也要 TLS，改走 ClickHouse 9440／PostgreSQL ssl，列為待討論。

## 1. 主機前置（三台都做）

- OS：RHEL 9.x 或 Rocky 9.x；`dnf install docker-ce docker-compose-plugin`（Docker Engine 27、Compose v2）；Docker Desktop 不裝。
- 核心參數（Ansible 角色或手動）：`vm.max_map_count=262144`、`fs.file-max=2097152`、`net.core.somaxconn=4096`；關 swap 或 `vm.swappiness=1`。
- 目錄：資料碟掛 `/data`（SSD），`mkdir -p /data/dmp`；NAS 掛 `/mnt/nas/dmp-backup`（VM1 的 WAL 歸檔與 VM3 的備份都寫這裡）。VM2 另建 `/data/dmp/landing`（接收區：永豐 IT SFTP 落檔目錄，設唯讀給容器）。
- 密鑰：密鑰庫（OpenBao／HSM）在開機時把 secret_ref 對照檔掛到 `/run/secrets/dmp-secrets.json`（0400，root），程式只在執行節點看得到明文（docs/06 §4）。
- 防火牆（只開內網 10.10.0.0/24）：8123／9000／9009（ClickHouse）、9181／9234（Keeper）、5432、6379、8333／9333／8888（SeaweedFS）、9100／9363／9364（exporter）、3100（Loki）、9090／3000（Prometheus／Grafana，維運網段）、8080／80／8180／8082（LB 與維運）。
- `git clone` 到 `/opt/altatech-dmp`，`cd deploy/compose && cp .env.example .env`，填值；**每台的 `VM_HOST`、`KEEPER_ID`、`CH_REPLICA` 不同**（VM1=1/r1、VM2=2/r2、VM3=3/—）。`.env` 已 gitignore。

## 2. 首次啟動順序

```bash
# 三台先起 Keeper（要 3 台中至少 2 台在線才有法定人數）
./dmp.sh vm1 up keeper ; ./dmp.sh vm2 up keeper ; ./dmp.sh vm3 up keeper
# VM1：資料服務 → 應用
./dmp.sh vm1 up            # clickhouse（會跑 backend/clickhouse/schema.sql）、postgres（建 replicator、keycloak 庫）、redis、api、web
# VM2：postgres 第一次啟動會從 VM1 pg_basebackup（空目錄才會做）
./dmp.sh vm2 up
# VM3
./dmp.sh vm3 up            # keycloak、prometheus、loki、grafana；ingest 選配：./dmp.sh vm3 up --profile ingest
```

驗證：
```bash
./dmp.sh vm1 check                                   # 三台各跑一次：compose 設定與樣板渲染
curl -s http://10.10.0.11:8080/healthz               # api ok=true
docker compose -f vm1/docker-compose.yml exec clickhouse clickhouse-client -q "SELECT * FROM system.clusters WHERE cluster='dmp'"   # 兩個副本
docker compose -f vm2/docker-compose.yml exec postgres psql -U dmp -c "SELECT pg_is_in_recovery()"   # t
docker compose -f vm2/docker-compose.yml exec redis redis-cli -a "$REDIS_PASSWORD" info replication   # role:slave, master_link_status:up
```
再用 `deploy/Makefile` 的 `make smoke` 口徑（寫 1 萬測試事件並查詢）跑一次；Grafana（VM3:3000）應看到三台 node、兩個 ClickHouse、三個 Keeper、兩個 dmp-api 都 up。

## 3. 日常維運

| 項目 | 做法 |
|---|---|
| 看狀態／日誌 | `./dmp.sh vmN ps`、`./dmp.sh vmN logs -f api`；跨台日誌在 Grafana → Loki（標籤 host、container） |
| 升級 | 維護時段；`./common/scripts/upgrade.sh <tag>` 在 VM2 → VM1 → VM3 依序跑（VM2 先停 worker 讓排程收尾）；schema 變更走 migration 腳本，先在測試環境演練 |
| 備份 | VM3 主機 cron（`sudo crontab -u root common/scripts/crontab.vm3`）：ClickHouse 日增量 02:00、週全量週日 03:00 → SeaweedFS；PostgreSQL 日 pg_dump 01:30 → NAS；05:00 rclone 把 SeaweedFS 的備份桶同步到 NAS。WAL 歸檔由 VM1 主庫持續寫 NAS |
| 還原演練 | 每季一次：ClickHouse `RESTORE DATABASE dmp FROM S3(...)` 到測試環境；PostgreSQL `pg_restore` + WAL replay |
| 告警 | Prometheus 規則在 `common/prometheus/alerts.yml`；通知走 core API 的 `/v1/ops/notify`（Email／LINE，`backend/ops/notify.py`）；要接 Alertmanager 時在 `prometheus.yml.tmpl` 加 |

## 4. 故障處理（手冊，第一階段不做自動切換）

- **VM1 掛（ClickHouse r1、PG 主、Redis 主）**：查詢與寫入由 r2 承接（F5 健康檢查把流量導到 VM2 的 80／8080）。PostgreSQL：在 VM2 `docker compose -f vm2/docker-compose.yml exec postgres pg_ctl promote`，然後把 VM1／VM2／VM3 `.env` 的 `DMP_PG_DSN`、`KC_DB_URL` 指到 VM2 並 `up -d` 應用；VM1 修好後清空 `/data/dmp/postgres` 用 `init-replica.sh` 的方式反向重建為備援。Redis：在 VM2 `redis-cli replicaof no one`，應用 `REDIS_URL` 改指 VM2（快取可丟，不急）。
- **VM2 掛**：查詢不中斷；背景排程（worker、batch）停擺 → 臨時在 VM1 以 `DMP_TICK=1` 起一份 worker（`docker compose -f vm1/docker-compose.yml run -d -e DMP_TICK=1 -e DMP_ROLE=worker api`），VM2 回來後停掉。SeaweedFS 只影響備份與冷層，當日備份補跑。
- **VM3 掛**：Keeper 剩 2 台仍有法定人數；監控、登入（Keycloak）中斷 → 優先救 Keycloak。
- **任兩台同時掛**：Keeper 失去法定人數，ClickHouse 進唯讀；先救 Keeper。

## 5. 已知限制與待討論

- 三台 VM 的規格（測試 16 核／128 GB、正式 64 核／256 GB）與 VM 配置待永豐資訊部門確認。
- 容器間未加密；Keycloak 對 AD 的 broker 設定、F5 健康檢查路徑（`/healthz`）、SFTP 接收區由永豐 IT 提供。
- `dmp-core` 的環境變數以 `backend/api/README.md` 為準，`DMP_OIDC_ISSUER`、`DMP_PUBLIC_URL`、`S3_*` 若程式尚未讀取，請研發對齊後再上正式。
- Redis 8、Loki、Grafana 為 AGPLv3 內部使用例外，待法務核可（docs/07 §1.3）。
