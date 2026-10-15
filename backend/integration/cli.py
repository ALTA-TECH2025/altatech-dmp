"""數據整合命令列：建模驗證／預覽／編譯 SQL，與排程器的任務管理。API（/v1/integration/*）之前給團隊與 CI 用。

  python3 -m backend.integration.cli validate backend/integration/models.example.yaml
  python3 -m backend.integration.cli preview  backend/integration/models.example.yaml sample.csv [--key k_idm=…]
  python3 -m backend.integration.cli compile  backend/integration/models.example.yaml dmp.raw_orders
  python3 -m backend.integration.cli sched --db sched.db add sync_orders backend/integration/models.example.yaml \
        --cron "*/15 * * * *" --mode incremental --source path=orders.csv,incremental_field=UPDATED_AT --depends sync_instrument
  python3 -m backend.integration.cli sched --db sched.db tick [--ch http://127.0.0.1:8123]
  python3 -m backend.integration.cli sched --db sched.db status | runs <task> | run <task> | enable <task> | disable <task>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import modeling
from .runner import ClickHouseHTTP, LocalRunner, read_source
from .scheduler import Scheduler


def _keys(args: argparse.Namespace) -> None:
    for kv in args.key or []:
        k, _, v = kv.partition("="); modeling.set_key(k, v.encode())
    for k, v in os.environ.items():                 # 正式環境：密鑰由 KMS／Secret 注入環境變數 DMP_KEY_<ref>
        if k.startswith("DMP_KEY_"):
            modeling.set_key(k[8:].lower(), v.encode())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dmp-integration")
    ap.add_argument("--key", action="append", help="測試用密鑰 ref=value")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate"); p.add_argument("model")
    p = sub.add_parser("preview"); p.add_argument("model"); p.add_argument("sample"); p.add_argument("--limit", type=int, default=1000)
    p = sub.add_parser("compile"); p.add_argument("model"); p.add_argument("raw_table")
    p = sub.add_parser("sched"); p.add_argument("--db", required=True); p.add_argument("--ch"); p.add_argument("--now")
    ss = p.add_subparsers(dest="scmd", required=True)
    a = ss.add_parser("add"); a.add_argument("id"); a.add_argument("model"); a.add_argument("--cron"); a.add_argument("--every", type=int)
    a.add_argument("--mode", default="incremental"); a.add_argument("--source", required=True); a.add_argument("--depends", nargs="*", default=[])
    a.add_argument("--retries", type=int, default=3); a.add_argument("--backoff", type=int, default=60); a.add_argument("--schema-change", default="pause")
    for name in ("tick", "status"):
        ss.add_parser(name)
    for name in ("runs", "run", "enable", "disable"):
        ss.add_parser(name).add_argument("task")
    args = ap.parse_args(argv); _keys(args)

    if args.cmd == "validate":
        errs = modeling.validate(modeling.load(args.model))
        print("\n".join(errs) if errs else "OK"); return 1 if errs else 0
    if args.cmd == "preview":
        m = modeling.load(args.model); rows = read_source({"kind": "jsonl" if args.sample.endswith(".jsonl") else "csv", "path": args.sample})
        print(json.dumps(modeling.preview(m, rows, args.limit), ensure_ascii=False, indent=1, default=str)); return 0
    if args.cmd == "compile":
        print(modeling.compile_sql(modeling.load(args.model), args.raw_table)); return 0
    ch = ClickHouseHTTP(args.ch) if args.ch else None
    s = Scheduler(args.db, LocalRunner(ch)); now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if args.scmd == "add":
        src = dict(kv.split("=", 1) for kv in args.source.split(","))
        s.add_task(args.id, args.model, cron=args.cron, interval_min=args.every, mode=args.mode, depends_on=args.depends, retries=args.retries,
                   backoff_s=args.backoff, schema_change=args.schema_change, source=src, now=now); print("added", args.id)
    elif args.scmd == "tick":
        print(json.dumps(s.tick(now), ensure_ascii=False))
    elif args.scmd == "status":
        print(json.dumps(s.status(now), ensure_ascii=False, indent=1, default=str))
    elif args.scmd == "runs":
        print(json.dumps(s.runs(args.task), ensure_ascii=False, indent=1, default=str))
    elif args.scmd == "run":
        print(json.dumps(s.run_now(args.task, now), ensure_ascii=False))
    elif args.scmd in ("enable", "disable"):
        s.set_enabled(args.task, args.scmd == "enable", None if args.scmd == "enable" else "manual"); print(args.scmd, args.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
