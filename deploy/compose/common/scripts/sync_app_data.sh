#!/bin/bash
# VM1 → VM2 同步應用資料目錄（APP_DATA：排程狀態 sched.db、看板／匯出產出檔；docs/44 §5a）。由 VM1 主機 cron 每 15 分鐘跑（crontab.vm1）。
# 前置：VM1 root 對 VM2 有免密碼 SSH（只允許對 APP_DATA 的 rsync、mv、rm）；主機要有 python3 與 rsync。
# 檔案一律維持應用 UID（APP_UID，core 映像 10001）可寫：快照先 chown，rsync 用 --numeric-ids 保留，目的端先寫暫存檔再 mv 原子替換。
# 接管後（VM2 變主）停用這支，回切前反向同步一次。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/../.." && pwd)
set -a; . "$HERE/.env"; set +a
SRC=${APP_DATA:?}
SNAP="$SRC/.sync"
mkdir -p "$SNAP"
# SQLite 不能直接複製正在寫的檔：先用 backup API 做一致快照
APP_UID=${APP_UID:-10001}
if [ -f "$SRC/sched.db" ]; then
  rm -f "$SNAP/sched.db"
  python3 -c 'import sqlite3,sys;s=sqlite3.connect(sys.argv[1]);d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close();s.close()' "$SRC/sched.db" "$SNAP/sched.db"
  chown "$APP_UID:$APP_UID" "$SNAP/sched.db"; chmod 0600 "$SNAP/sched.db"
fi
rsync -a --numeric-ids --delete --exclude '.sync/' --exclude 'sched.db*' --exclude '.sched.db.tmp' "$SRC/" "root@${VM2_HOST}:$SRC/"
if [ -f "$SNAP/sched.db" ]; then
  rsync -a --numeric-ids "$SNAP/sched.db" "root@${VM2_HOST}:$SRC/.sched.db.tmp"
  # 原子替換；排程用 SQLite 預設 rollback journal（非 WAL），備援端平時沒有寫入交易，殘留的 journal 屬於舊檔，一併刪除以免套到新檔
  ssh "root@${VM2_HOST}" "mv -f '$SRC/.sched.db.tmp' '$SRC/sched.db' && rm -f '$SRC/sched.db-journal'"
fi
echo "$(date -Is) app-data synced to ${VM2_HOST}"
