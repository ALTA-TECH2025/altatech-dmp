"""數據整合命令列：建模驗證／預覽／編譯 SQL，與排程器的任務管理。API（/v1/integration/*）之前給團隊與 CI 用。

  python3 -m backend.integration.cli validate backend/integration/models.example.yaml
  python3 -m backend.integration.cli preview  backend/integration/models.example.yaml sample.csv [--key k_idm=…]
  python3 -m backend.integration.cli compile  backend/integration/models.example.yaml dmp.raw_orders
  python3 -m backend.integration.cli sched --db sched.db add sync_orders backend/integration/models.example.yaml \
        --cron "*/15 * * * *" --mode incremental --source path=orders.csv,incremental_field=UPDATED_AT --depends sync_instrument
  python3 -m backend.integration.cli sched --db sched.db tick [--ch http://127.0.0.1:8123]
  python3 -m backend.integration.cli sched --db sched.db status | runs <task> | run <task> | enable <task> | disable <task>
  DMP_PG_DSN=… python3 -m backend.integration.cli sched import-sqlite --from sched.db    # 一次性：quickstart 的 SQLite 排程搬進控制面 PostgreSQL（#87）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import modeling
from .runner import ClickHouseHTTP, LocalRunner, read_source
from .scheduler import PgSchedDB, Scheduler, import_sqlite


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
    p = sub.add_parser("sched"); p.add_argument("--db"); p.add_argument("--ch"); p.add_argument("--now")
    ss = p.add_subparsers(dest="scmd", required=True)
    a = ss.add_parser("add"); a.add_argument("id"); a.add_argument("model"); a.add_argument("--cron"); a.add_argument("--every", type=int)
    a.add_argument("--mode", default="incremental"); a.add_argument("--source", required=True); a.add_argument("--depends", nargs="*", default=[])
    a.add_argument("--retries", type=int, default=3); a.add_argument("--backoff", type=int, default=60); a.add_argument("--schema-change", default="pause")
    for name in ("tick", "status"):
        ss.add_parser(name)
    for name in ("runs", "run", "enable", "disable"):
        ss.add_parser(name).add_argument("task")
    ss.add_parser("import-sqlite").add_argument("--from", dest="src", required=True)
    args = ap.parse_args(argv); _keys(args)

    if args.cmd == "validate":
        errs = modeling.validate(modeling.load(args.model))
        print("\n".join(errs) if errs else "OK"); return 1 if errs else 0
    if args.cmd == "preview":
        m = modeling.load(args.model); rows = read_source({"kind": "jsonl" if args.sample.endswith(".jsonl") else "csv", "path": args.sample})
        print(json.dumps(modeling.preview(m, rows, args.limit), ensure_ascii=False, indent=1, default=str)); return 0
    if args.cmd == "compile":
        print(modeling.compile_sql(modeling.load(args.model), args.raw_table)); return 0
    if args.scmd == "import-sqlite":
        return _import_sqlite(args.src)
    if not args.db:
        ap.error("sched 需要 --db <sched.db>（import-sqlite 除外）")
    ch = ClickHouseHTTP(args.ch) if args.ch else None
    s = Scheduler(args.db, LocalRunner(ch)); now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
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


def _import_sqlite(src: str) -> int:
    """目的端是控制面 PostgreSQL（與 API 同一組 DMP_PG_DSN_REF／DMP_PG_DSN）；已有的同 id 不覆蓋，重跑不重複。"""
    from ..api.store_pg import PostgresStore
    if os.environ.get("DMP_PG_DSN_REF"):
        secrets = json.loads(Path(os.environ["DMP_SECRETS_JSON"]).read_text(encoding="utf-8")) if os.environ.get("DMP_SECRETS_JSON") else {}
        dsn = secrets[os.environ["DMP_PG_DSN_REF"]]
    elif os.environ.get("DMP_PG_DSN"):
        dsn = os.environ["DMP_PG_DSN"]
    else:
        print("需要控制面 PostgreSQL：設 DMP_PG_DSN_REF（＋DMP_SECRETS_JSON）或本機 DMP_PG_DSN", file=sys.stderr); return 2
    pg = PostgresStore(dsn)
    try:
        s = Scheduler(PgSchedDB(pg), LocalRunner(None))
        try:
            n = import_sqlite(src, s)
        finally:
            s.db.close()
    finally:
        pg.close()
    print(json.dumps(n, ensure_ascii=False))
    if n["missing_models"]:
        print(f"注意：{n['missing_models']} 個任務的模型檔讀不到，照原路徑搬過來；請在畫面重新儲存模型或修正 model_path", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
