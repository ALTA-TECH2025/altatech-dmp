#!/bin/bash
# 主庫第一次啟動：建複寫帳號、控制面 schema。密碼由環境變數帶入（密鑰庫注入）。
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${PG_REPL_PASSWORD}';
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS dmp;
CREATE DATABASE keycloak OWNER ${POSTGRES_USER};
SQL
