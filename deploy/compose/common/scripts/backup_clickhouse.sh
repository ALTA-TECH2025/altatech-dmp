#!/bin/bash
# ClickHouse 備份（docs/17 §3 備份策略）：daily = 增量（以最近一次備份為基底）、weekly = 全量；目的地 SeaweedFS S3（VM2），再由 rclone 同步到 NAS。
# 用法：由 VM3 主機 cron 呼叫 → docker compose --profile backup run --rm backup /scripts/backup_clickhouse.sh daily|weekly
set -euo pipefail
MODE=${1:-daily}
STAMP=$(date +%Y%m%d-%H%M)
CLI="clickhouse-client --host ${CH_HOST} --user ${CH_USER} --password ${CH_PASSWORD}"
DEST="S3('${S3_ENDPOINT}/dmp-backup/clickhouse/${MODE}/${STAMP}/', '${S3_ACCESS_KEY}', '${S3_SECRET_KEY}')"
if [ "$MODE" = "daily" ]; then
  BASE=$($CLI -q "SELECT name FROM system.backups WHERE status = 'BACKUP_CREATED' ORDER BY end_time DESC LIMIT 1" || true)
  if [ -n "$BASE" ]; then
    $CLI -q "BACKUP DATABASE dmp TO ${DEST} SETTINGS base_backup = ${BASE}, async = 0"
  else
    $CLI -q "BACKUP DATABASE dmp TO ${DEST} SETTINGS async = 0"
  fi
else
  $CLI -q "BACKUP DATABASE dmp TO ${DEST} SETTINGS async = 0"
fi
$CLI -q "SELECT name, status, end_time FROM system.backups ORDER BY end_time DESC LIMIT 1"
echo "${MODE} ${STAMP} done" >> "${NAS_BACKUP}/clickhouse-backups.log"
