#!/bin/bash
# 三台 VM 共用的操作入口：./dmp.sh <vm1|vm2|vm3> <up|down|ps|logs|pull|run|config|check> [args...]
# up 之前會把 .env 的值套進 *.tmpl（Prometheus、SeaweedFS 的設定檔不吃環境變數），輸出到 <vm>/rendered/（已 gitignore）。
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
VM=${1:?vm1|vm2|vm3}; shift
CMD=${1:-ps}; shift || true
[ -f "$HERE/.env" ] || { echo "缺 $HERE/.env，先 cp .env.example .env 填值" >&2; exit 1; }
set -a; . "$HERE/.env"; set +a
subst() {   # $1 樣板 → $2 輸出；優先用 envsubst（gettext），沒有就用 python3
  if command -v envsubst >/dev/null 2>&1; then envsubst < "$1" > "$2"
  else python3 -c 'import os,sys;sys.stdout.write(os.path.expandvars(open(sys.argv[1]).read()))' "$1" > "$2"; fi
}
render() {
  mkdir -p "$HERE/$VM/rendered"
  [ "$VM" = vm3 ] && subst "$HERE/common/prometheus/prometheus.yml.tmpl" "$HERE/$VM/rendered/prometheus.yml"
  [ "$VM" != vm2 ] && subst "$HERE/common/seaweedfs/s3.json.tmpl" "$HERE/$VM/rendered/s3.json"
  return 0
}
DC="docker compose --env-file $HERE/.env -f $HERE/$VM/docker-compose.yml"
case "$CMD" in
  up)      render; $DC up -d "$@" ;;
  run)     render; $DC --profile backup run --rm "$@" ;;
  check)   render; $DC config --quiet && echo "$VM: compose config ok" ;;
  config)  render; $DC config "$@" ;;
  *)       $DC "$CMD" "$@" ;;
esac
