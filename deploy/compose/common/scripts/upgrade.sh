#!/bin/bash
# 升級（docs/07 §1.3）：拉新版映像後逐台重啟應用容器，資料服務不動。排在維護時段；先在測試環境演練。
# 用法：./common/scripts/upgrade.sh <new_tag>   （在三台各跑一次，順序 VM2 → VM1 → VM3；先升備援 VM2，VM1 先停 worker 讓排程收尾）
set -euo pipefail
NEW=${1:?new tag}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
VM=$(grep -E '^VM_HOST=' "$HERE/.env" | cut -d= -f2 | awk '{print $1}')   # .env 行尾可能有註解
sed -i "s/^DMP_TAG=.*/DMP_TAG=${NEW}/" "$HERE/.env"
for v in vm1 vm2 vm3; do
  if grep -Eq "^${v^^}_HOST=${VM}([[:space:]]|$)" "$HERE/.env"; then
    echo "本機是 $v，升級到 ${NEW}"
    cd "$HERE"
    ./dmp.sh "$v" pull
    case "$v" in
      vm1) ./dmp.sh vm1 stop worker batch          # 讓進行中的排程收尾，避免新舊版同時跑
           ./dmp.sh vm1 up --no-deps worker batch api web ;;
      vm2) ./dmp.sh vm2 up --no-deps api web ;;  # worker、batch 只在接管時起（takeover profile），這裡不動
      vm3) ./dmp.sh vm3 up --no-deps keycloak ;;
    esac
    exit 0
  fi
done
echo "找不到本機位址 ${VM} 對應的 VM" >&2; exit 1
