"""python3 -m backend.segments            自測（假 ClickHouse）
python3 -m backend.segments tick       例行排程一輪（環境變數同 backend.api.main：CLICKHOUSE_URL、DMP_STORE_PATH）；排程器每分鐘呼叫一次
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "tick":
        from ..api.store import MemoryStore
        from ..integration.runner import ClickHouseHTTP
        from ..security.policy import Subject, authorize
        from .engine import SegmentService
        store = MemoryStore(Path(os.environ["DMP_STORE_PATH"]) if os.environ.get("DMP_STORE_PATH") else None)
        ch = ClickHouseHTTP(os.environ["CLICKHOUSE_URL"], user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=600)
        svc = SegmentService(store, ch)
        sched = Subject("system:scheduler", ["admin"])
        out = svc.tick(sched, lambda seg: authorize(Subject(seg["owner"], ["data_analyst"]), "use", "users"))
        purged = svc.purge_expired(sched)
        print(f"segments tick: computed={[(x['segment'], x['basetime'], x['users'], x['status']) for x in out]} purged={purged}")
        return
    from .selftest import main as selftest
    selftest()


if __name__ == "__main__":
    main()
