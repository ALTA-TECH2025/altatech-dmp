#!/usr/bin/env bash
# 單機測試環境（筆電或一台 GCP VM，Ubuntu 22.04+／Rocky 9，4 vCPU 16 GB 夠用）：
#   ClickHouse（固定版本單一執行檔）+ 建表 + FastAPI（API 同源提供前端建置檔）+ 本機帳號登入（密碼 + TOTP）+ 背景排程
# 用途：真的能登入操作的完整系統（分析、分群、審批、數據整合、儀表板）與資料匯入驗證。不是正式部署（正式走 deploy/helm）。
#
# 用法：
#   deploy/quickstart/run_local.sh up                 # 下載 ClickHouse、起服務、建表、建前端、起 API（http://127.0.0.1:8000 即是前端）
#   deploy/quickstart/run_local.sh seed               # 載入合成資料（黃金資料集）、建五種角色帳號（印 TOTP 註冊網址）、建範例儀表板
#   deploy/quickstart/run_local.sh account list | add <帳號> --roles admin,... | reset-password <帳號> | reset-totp <帳號> | remove <帳號>
#   deploy/quickstart/run_local.sh code <帳號>        # 印目前 TOTP 驗證碼（沒有手機驗證器時用；只在測試環境）
#   deploy/quickstart/run_local.sh e2e                # 跑端到端（合成資料 + 黃金測試 + API）
#   deploy/quickstart/run_local.sh import --date 2026-10-01   # 舊平台查詢 API 導出該日 → 代碼化 → 載入 → 對帳（需 LEGACY_BASE_URL、LEGACY_PROJECT、LEGACY_API_SECRET）
#   deploy/quickstart/run_local.sh status | down
#
# 環境變數（都有預設）：DMP_WORK 工作目錄（~/dmp-local）、DMP_BIND 監聽位址（127.0.0.1；VM 給團隊用改 0.0.0.0 並在前面放 TLS）、DMP_PORT（8000）、
#   CH_PASSWORD（dmp-local）、DMP_SEED_PASSWORD（seed 時五個帳號的初始密碼，≥14 字；沒設會互動輸入）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${DMP_WORK:-$HOME/dmp-local}"
CH_VERSION="${CH_VERSION:-25.8.33.6}"
BIND="${DMP_BIND:-127.0.0.1}"; PORT="${DMP_PORT:-8000}"
mkdir -p "$WORK"

ch_bin() { echo "$WORK/clickhouse"; }

# API 與 seed／account 共用的環境（單機：本機帳號、所有角色、cookie 不加 Secure、排程與分群背景執行）
dmp_env() {
  export CLICKHOUSE_URL=http://127.0.0.1:8123 CLICKHOUSE_PASSWORD="${CH_PASSWORD:-dmp-local}" \
    DMP_SECRETS_JSON="$WORK/secrets.json" DMP_STORE_PATH="$WORK/store.json" DMP_EXPORT_DIR="$WORK/exports" DMP_SCHED_DB="$WORK/sched.db" \
    DMP_IDP_MODE=direct DMP_LOCAL_ACCOUNTS="$WORK/accounts.json" DMP_LOCAL_ALLOWED_ROLES='*' DMP_COOKIE_SECURE="${DMP_COOKIE_SECURE:-0}" \
    DMP_WEB_DIST="$ROOT/web/dist" DMP_TICK=1 DMP_K_IDM="${DMP_K_IDM:-local-test-key}" DMP_K_ACCT="${DMP_K_ACCT:-local-test-key-acct}" \
    DMP_CH_USER_FILES="$WORK/ch/user_files" DMP_MIGRATION_WORK="$WORK/migration"
  [ -f "$WORK/secrets.json" ] || echo '{}' > "$WORK/secrets.json"
}

download_ch() {
  if [ ! -x "$(ch_bin)" ]; then
    echo ">> 下載 ClickHouse $CH_VERSION（官方 tgz）"
    curl -sSL "https://packages.clickhouse.com/tgz/lts/clickhouse-common-static-${CH_VERSION}-amd64.tgz" | tar xz -C "$WORK" --strip-components=3 "clickhouse-common-static-${CH_VERSION}/usr/bin/clickhouse"
  fi
  "$(ch_bin)" --version
}

write_config() {
  mkdir -p "$WORK/ch"
  cat > "$WORK/ch/config.xml" <<EOC
<clickhouse>
  <logger><level>warning</level><console>0</console><log>$WORK/ch/ch.log</log><errorlog>$WORK/ch/ch.err</errorlog></logger>
  <http_port>8123</http_port><tcp_port>9000</tcp_port><listen_host>127.0.0.1</listen_host>
  <path>$WORK/ch/data/</path><tmp_path>$WORK/ch/tmp/</tmp_path><user_files_path>$WORK/ch/user_files/</user_files_path>
  <max_server_memory_usage_to_ram_ratio>0.6</max_server_memory_usage_to_ram_ratio>
  <users><default><password_sha256_hex>$(printf '%s' "${CH_PASSWORD:-dmp-local}" | sha256sum | cut -d' ' -f1)</password_sha256_hex><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
  <profiles><default><allow_experimental_json_type>1</allow_experimental_json_type></default></profiles><quotas><default/></quotas>
  <named_collections><dmp_dict><host>127.0.0.1</host><port>9000</port><user>default</user><password>${CH_PASSWORD:-dmp-local}</password></dmp_dict></named_collections>
</clickhouse>
EOC
}

ch_up() {
  if curl -s http://127.0.0.1:8123/ping >/dev/null 2>&1; then echo ">> ClickHouse 已在跑"; return; fi
  nohup "$(ch_bin)" server --config-file="$WORK/ch/config.xml" > "$WORK/ch/server.out" 2>&1 &
  for _ in $(seq 1 30); do curl -s http://127.0.0.1:8123/ping >/dev/null 2>&1 && break; sleep 1; done
  echo ">> ClickHouse up"
}

schema() {
  cd "$ROOT" && python3 - <<'PY'
import os
from pathlib import Path
from backend.integration import aggregates
from backend.integration.runner import ClickHouseHTTP, local_ddl
ch = ClickHouseHTTP("http://127.0.0.1:8123", password=os.environ.get("CH_PASSWORD", "dmp-local"))
if aggregates.migrate_legacy_mv(ch):               # 舊版日彙總是 MV：先拆掉
    print(">> 已拆除舊版日彙總 MV")
for st in local_ddl(Path("backend/clickhouse/schema.sql").read_text(encoding="utf-8")):
    ch.query(st, settings={"allow_experimental_json_type": 1})
filled = aggregates.backfill_missing(ch)            # 底表有、彙總沒有的日期一律補（升級中斷後重跑也會補）
if filled:
    print(">> 日彙總回填日期：", len(filled))
print(">> schema ok · tables:", ch.query("SELECT count() FROM system.tables WHERE database='dmp'").strip())
PY
}

web_build() {
  if [ -f "$ROOT/web/dist/index.html" ] && [ "${1:-}" != "--rebuild" ]; then echo ">> 前端已建置（要重建：up --rebuild）"; return; fi
  echo ">> 建置前端（npm ci + build）"
  (cd "$ROOT/web" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
}

api_up() {
  cd "$ROOT"; dmp_env
  if curl -s "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then echo ">> API 已在跑（要重啟：down 再 up）"; return; fi
  nohup python3 -m uvicorn backend.api.main:app --host "$BIND" --port "$PORT" > "$WORK/api.out" 2>&1 &
  for _ in $(seq 1 30); do curl -s "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 1; done
  echo ">> API: $(curl -s "http://127.0.0.1:$PORT/healthz")"
  echo ">> 前端：http://${BIND}:$PORT  （帳號：先跑 seed 或 account add；登入用「本機帳號」）"
}

case "${1:-}" in
  up)
    download_ch; write_config; ch_up; schema; web_build "${2:-}"; api_up ;;
  seed)
    cd "$ROOT"; dmp_env
    if [ -z "${DMP_SEED_PASSWORD:-}" ]; then read -r -s -p "五個帳號的初始密碼（至少 14 字，不會顯示）：" DMP_SEED_PASSWORD; echo; export DMP_SEED_PASSWORD; fi
    # 控制面是單機 JSON 檔（一個寫入者）：seed 期間先停 API，寫完再起，避免 API 的審計寫入蓋掉 seed 的儀表板
    WAS_UP=0; curl -s "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && WAS_UP=1
    [ "$WAS_UP" = 1 ] && { pkill -f "[u]vicorn backend.api.main:app" || true; sleep 1; }
    shift; python3 -m backend.api.seed "$@"
    [ "$WAS_UP" = 1 ] && api_up || true ;;
  account)
    cd "$ROOT"; dmp_env; shift; python3 -m backend.auth.local "$@" ;;
  code)
    cd "$ROOT"; dmp_env; python3 -m backend.auth.local code "${2:?帳號}" ;;
  status)
    echo "ClickHouse: $(curl -s http://127.0.0.1:8123/ping 2>/dev/null || echo down)"; echo "API: $(curl -s "http://127.0.0.1:$PORT/healthz" 2>/dev/null || echo down)" ;;
  e2e)
    download_ch; cd "$ROOT" && CLICKHOUSE_BIN="$(ch_bin)" python3 -m backend.integration.e2e_clickhouse ;;
  import)
    shift; DATE=""; while [ $# -gt 0 ]; do case "$1" in --date) DATE="$2"; shift 2;; *) shift;; esac; done
    [ -n "$DATE" ] || { echo "需要 --date YYYY-MM-DD"; exit 2; }
    : "${LEGACY_BASE_URL:?設定 LEGACY_BASE_URL}"; : "${LEGACY_PROJECT:?設定 LEGACY_PROJECT}"; : "${LEGACY_API_SECRET:?設定 LEGACY_API_SECRET（不要寫在命令列）}"
    cd "$ROOT"
    python3 -m backend.integration.legacy_api test --base-url "$LEGACY_BASE_URL" --project "$LEGACY_PROJECT" --auth "${LEGACY_AUTH:-query_token}" --sample-date "$DATE"
    python3 -m backend.integration.legacy_api export --base-url "$LEGACY_BASE_URL" --project "$LEGACY_PROJECT" --auth "${LEGACY_AUTH:-query_token}" --date "$DATE" --out "$WORK/legacy_$DATE.jsonl"
    python3 - "$DATE" <<'PY'
import os, sys
from pathlib import Path
from backend.integration import modeling
from backend.integration.runner import ClickHouseHTTP
from backend.integration.legacy_format import LegacyLoader
day = sys.argv[1]; work = Path(os.environ.get("DMP_WORK", Path.home() / "dmp-local"))
modeling.set_key("k_idm", os.environ.get("DMP_K_IDM", "local-test-key").encode())      # 正式環境從密鑰庫；本機測試用固定值
ch = ClickHouseHTTP("http://127.0.0.1:8123", password=os.environ.get("CH_PASSWORD", "dmp-local"), timeout=1800)
r = LegacyLoader(ch, local=True).run_file(work / f"legacy_{day}.jsonl", work / "ch" / "user_files", f"legacy_{day.replace('-', '')}", "k_idm")
print(">> import:", {k: r[k] for k in ("raw", "events", "prop_log", "id_map", "recon_ok")}, "stage:", {k: r["stage"][k] for k in ("lines", "ok", "bad", "tokenized")})
PY
    ;;
  down)
    pkill -f "[c]lickhouse server --config-file=$WORK/ch/config.xml" || true
    pkill -f "[u]vicorn backend.api.main:app" || true
    pkill -f "[v]ite preview --port 5173" || true
    echo ">> stopped" ;;
  *)
    echo "用法: $0 up [--rebuild] | seed [--no-data|--no-accounts|--no-dashboard] | account <子命令> | code <帳號> | status | e2e | import --date YYYY-MM-DD | down"; exit 2 ;;
esac
