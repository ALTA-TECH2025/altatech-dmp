#!/usr/bin/env bash
# 單機測試環境（筆電或一台 GCP VM，Ubuntu 22.04+／Rocky 9，4 vCPU 16 GB 夠用）：
#   ClickHouse（固定版本單一執行檔）+ 建表 + FastAPI（BFF／API）+ 前端（vite preview 代理到 API）
# 用途：資料匯入驗證（神策查詢 API → stage → ClickHouse → 對帳 → 前端查）。不是正式部署（正式走 deploy/helm）。
#
# 用法：
#   deploy/quickstart/run_local.sh up        # 下載 ClickHouse、起服務、建表、起 API（:8000）與前端（:5173）
#   deploy/quickstart/run_local.sh e2e       # 跑端到端（合成資料 + 黃金測試 + API）
#   deploy/quickstart/run_local.sh import --date 2026-10-01   # 神策查詢 API 導出該日 → 代碼化 → 載入 → 對帳（需 SA_BASE_URL、SA_PROJECT、SA_API_SECRET）
#   deploy/quickstart/run_local.sh down
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${DMP_WORK:-$HOME/dmp-local}"
CH_VERSION="${CH_VERSION:-25.8.33.6}"
mkdir -p "$WORK"

ch_bin() { echo "$WORK/clickhouse"; }

download_ch() {
  if [ ! -x "$(ch_bin)" ]; then
    echo ">> 下載 ClickHouse $CH_VERSION（官方 tgz）"
    curl -sSL "https://packages.clickhouse.com/tgz/lts/clickhouse-common-static-${CH_VERSION}-amd64.tgz" | tar xz -C "$WORK" --strip-components=3 "clickhouse-common-static-${CH_VERSION}/usr/bin/clickhouse"
  fi
  "$(ch_bin)" --version
}

write_config() {
  mkdir -p "$WORK/ch"
  cat > "$WORK/ch/config.xml" <<EOF
<clickhouse>
  <logger><level>warning</level><console>0</console><log>$WORK/ch/ch.log</log><errorlog>$WORK/ch/ch.err</errorlog></logger>
  <http_port>8123</http_port><tcp_port>9000</tcp_port><listen_host>127.0.0.1</listen_host>
  <path>$WORK/ch/data/</path><tmp_path>$WORK/ch/tmp/</tmp_path><user_files_path>$WORK/ch/user_files/</user_files_path>
  <max_server_memory_usage_to_ram_ratio>0.6</max_server_memory_usage_to_ram_ratio>
  <users><default><password_sha256_hex>$(printf '%s' "${CH_PASSWORD:-dmp-local}" | sha256sum | cut -d' ' -f1)</password_sha256_hex><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
  <profiles><default><allow_experimental_json_type>1</allow_experimental_json_type></default></profiles><quotas><default/></quotas>
  <named_collections><dmp_dict><host>127.0.0.1</host><port>9000</port><user>default</user><password>${CH_PASSWORD:-dmp-local}</password></dmp_dict></named_collections>
</clickhouse>
EOF
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
from backend.integration.runner import ClickHouseHTTP, local_ddl
ch = ClickHouseHTTP("http://127.0.0.1:8123", password=os.environ.get("CH_PASSWORD", "dmp-local"))
for st in local_ddl(Path("backend/clickhouse/schema.sql").read_text(encoding="utf-8")):
    ch.query(st, settings={"allow_experimental_json_type": 1})
print(">> schema ok · tables:", ch.query("SELECT count() FROM system.tables WHERE database='dmp'").strip())
PY
}

api_up() {
  cd "$ROOT"
  if [ ! -f "$WORK/secrets.json" ]; then echo '{}' > "$WORK/secrets.json"; fi
  CLICKHOUSE_URL=http://127.0.0.1:8123 CLICKHOUSE_PASSWORD="${CH_PASSWORD:-dmp-local}" DMP_SECRETS_JSON="$WORK/secrets.json" DMP_STORE_PATH="$WORK/store.json" \
    nohup python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 > "$WORK/api.out" 2>&1 &
  for _ in $(seq 1 20); do curl -s http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 1; done
  echo ">> API: $(curl -s http://127.0.0.1:8000/healthz)"
}

web_up() {
  cd "$ROOT/web" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null
  nohup npx vite preview --port 5173 --strictPort > "$WORK/web.out" 2>&1 &
  echo ">> web: http://127.0.0.1:5173（vite preview 不代理 /auth、/v1；請用 nginx 或 ssh 轉發把 /auth、/v1 導到 :8000，或用 npm run dev）"
}

case "${1:-}" in
  up)
    download_ch; write_config; ch_up; schema; api_up; web_up ;;
  e2e)
    download_ch; cd "$ROOT" && CLICKHOUSE_BIN="$(ch_bin)" python3 -m backend.integration.e2e_clickhouse ;;
  import)
    shift; DATE=""; while [ $# -gt 0 ]; do case "$1" in --date) DATE="$2"; shift 2;; *) shift;; esac; done
    [ -n "$DATE" ] || { echo "需要 --date YYYY-MM-DD"; exit 2; }
    : "${SA_BASE_URL:?設定 SA_BASE_URL}"; : "${SA_PROJECT:?設定 SA_PROJECT}"; : "${SA_API_SECRET:?設定 SA_API_SECRET（不要寫在命令列）}"
    cd "$ROOT"
    python3 -m backend.integration.sensors_api test --base-url "$SA_BASE_URL" --project "$SA_PROJECT" --auth "${SA_AUTH:-query_token}" --sample-date "$DATE"
    python3 -m backend.integration.sensors_api export --base-url "$SA_BASE_URL" --project "$SA_PROJECT" --auth "${SA_AUTH:-query_token}" --date "$DATE" --out "$WORK/sa_$DATE.jsonl"
    python3 - "$DATE" <<'PY'
import os, sys
from pathlib import Path
from backend.integration import modeling
from backend.integration.runner import ClickHouseHTTP
from backend.integration.sensors_format import SensorsLoader
day = sys.argv[1]; work = Path(os.environ.get("DMP_WORK", Path.home() / "dmp-local"))
modeling.set_key("k_idm", os.environ.get("DMP_K_IDM", "local-test-key").encode())      # 正式環境從密鑰庫；本機測試用固定值
ch = ClickHouseHTTP("http://127.0.0.1:8123", password=os.environ.get("CH_PASSWORD", "dmp-local"), timeout=1800)
r = SensorsLoader(ch, local=True).run_file(work / f"sa_{day}.jsonl", work / "ch" / "user_files", f"sa_{day.replace('-', '')}", "k_idm")
print(">> import:", {k: r[k] for k in ("raw", "events", "prop_log", "id_map", "recon_ok")}, "stage:", {k: r["stage"][k] for k in ("lines", "ok", "bad", "tokenized")})
PY
    ;;
  down)
    pkill -f "clickhouse server --config-file=$WORK/ch/config.xml" || true
    pkill -f "uvicorn backend.api.main:app" || true
    pkill -f "vite preview --port 5173" || true
    echo ">> stopped" ;;
  *)
    echo "用法: $0 up | e2e | import --date YYYY-MM-DD | down"; exit 2 ;;
esac
