#!/bin/bash
# 備援（VM2）第一次啟動：資料目錄是空的就從主庫 pg_basebackup；之後每次啟動直接以 standby 模式起來。
# 故障切換（手冊 README §6）：docker compose exec postgres pg_ctl promote
set -euo pipefail
PGDATA=${PGDATA:-/var/lib/postgresql/data}
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "空資料目錄，從主庫 ${PRIMARY_HOST} 複製..."
  PGPASSWORD="$PG_REPL_PASSWORD" pg_basebackup -h "$PRIMARY_HOST" -p 5432 -U replicator -D "$PGDATA" -Fp -Xs -R -P
  chmod 700 "$PGDATA"
fi
exec docker-entrypoint.sh postgres -c hot_standby=on
