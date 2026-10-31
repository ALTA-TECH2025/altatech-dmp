#!/bin/bash
# PostgreSQL 控制面：每日 pg_dump 全量到 NAS（WAL 歸檔由主庫 archive_command 持續寫 NAS 的 postgres/wal）；保留 30 天
set -euo pipefail
OUT="${NAS_BACKUP}/postgres/dump"
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M)
pg_dump -h "${PG_HOST}" -U "${PG_USER}" -d dmp -Fc -f "${OUT}/dmp-${STAMP}.dump"
pg_dump -h "${PG_HOST}" -U "${PG_USER}" -d keycloak -Fc -f "${OUT}/keycloak-${STAMP}.dump"
find "$OUT" -name '*.dump' -mtime +30 -delete
echo "pg ${STAMP} done" >> "${NAS_BACKUP}/postgres-backups.log"
