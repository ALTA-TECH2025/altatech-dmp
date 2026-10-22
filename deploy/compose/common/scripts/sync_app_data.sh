#!/bin/bash
# VM1 → VM2 同步應用資料目錄（APP_DATA：排程狀態 sched.db、看板／匯出產出檔；docs/44 §5a）。由 VM1 主機 cron 每 15 分鐘跑（crontab.vm1）。
# 前置：VM1 root 對 VM2 有免密碼 SSH（只允許 rsync 到 APP_DATA）；主機要有 python3 與 rsync。
# 接管後（VM2 變主）停用這支，回切前反向同步一次。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
set -a; . "$HERE/.env"; set +a
SRC=${APP_DATA:?}
SNAP="$SRC/.sync"
mkdir -p "$SNAP"
# SQLite 不能直接複製正在寫的檔：先用 backup API 做一致快照
if [ -f "$SRC/sched.db" ]; then
  python3 -c 'import sqlite3,sys;s=sqlite3.connect(sys.argv[1]);d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close();s.close()' "$SRC/sched.db" "$SNAP/sched.db"
fi
rsync -a --delete --exclude '.sync/' --exclude 'sched.db*' "$SRC/" "root@${VM2_HOST}:$SRC/"
[ -f "$SNAP/sched.db" ] && rsync -a "$SNAP/sched.db" "root@${VM2_HOST}:$SRC/sched.db"
echo "$(date -Is) app-data synced to ${VM2_HOST}"
