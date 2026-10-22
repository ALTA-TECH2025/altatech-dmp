"""背景作業的主控租約（docs/44 §5a）：多份 worker 同時開著時，只有持有租約的那份跑例行作業。

為什麼：start_ticker 直接呼叫各服務 tick（同步、看板寄送、分群、推送、資料輸出、告警通知），這些 tick 自己沒有跨程序 claim；
兩份 worker 同時跑會重複執行（審查重現：同一張看板寄兩封）。逐項加 claim 改動面太大，先用「一個主控」把整段例行包起來。

- 租約存在 PostgreSQL 控制面一列（name 主鍵）；時間一律用資料庫時鐘（clock_timestamp），不受各主機時鐘偏差影響。
- 取得／續約是同一條 UPSERT：沒人持有、已過期、或本來就是自己 → 拿到；換人時 generation +1（給稽核與日後的 fencing）。
- 持有者有心跳執行緒每 ttl/3 秒續約；程序死掉或卡死到心跳也停，ttl 秒後別台接手。
- 已知限制：持有者整個程序暫停（VM 凍結、長 GC）超過 ttl，醒來到下一次心跳前可能和新主控重疊一小段；
  各 tick 原本的「已執行」紀錄（last_run 等）仍是第二道防線。

用法：DMP_PG_DSN=… python3 -m backend.ops.lease     # 自測：互斥、心跳、過期接手、generation、併發搶、包住例行只執行一次
"""
from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import Callable
from typing import Any

DDL = "CREATE TABLE IF NOT EXISTS {s}.ops_leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, until DOUBLE PRECISION NOT NULL, generation BIGINT NOT NULL)"
UPSERT = """
INSERT INTO {t} AS l (name, owner, until, generation)
VALUES ($1, $2, extract(epoch FROM clock_timestamp()) + $3, 1)
ON CONFLICT (name) DO UPDATE
   SET owner = EXCLUDED.owner, until = EXCLUDED.until,
       generation = CASE WHEN l.owner = EXCLUDED.owner THEN l.generation ELSE l.generation + 1 END
 WHERE l.owner = EXCLUDED.owner OR l.until < extract(epoch FROM clock_timestamp())
RETURNING generation
"""


def default_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class PgLease:
    def __init__(self, pg: Any, name: str, owner: str | None = None, ttl_s: float = 90.0,
                 on_change: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.pg, self.name, self.owner, self.ttl_s = pg, name, owner or default_owner(), ttl_s
        self.on_change = on_change or (lambda e, d: None)
        self.generation: int | None = None                     # None = 目前沒持有
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._hb: threading.Thread | None = None
        self._run(lambda c: c.execute(DDL.format(s=pg.schema)))

    def _run(self, fn: Callable[[Any], Any]) -> Any:
        async def go() -> Any:
            async with self.pg.pool.acquire() as c:
                return await fn(c)
        return self.pg._loop.run(go())

    def ensure(self) -> bool:
        """取得或續約；回傳現在是不是主控。持有狀態改變時呼叫 on_change（審計用）。"""
        with self._lock:
            sql = UPSERT.format(t=self.pg.t("ops_leases"))
            try:
                gen = self._run(lambda c: c.fetchval(sql, self.name, self.owner, float(self.ttl_s)))
            except Exception as e:                              # 資料庫連不上 → 保守視為沒持有（寧可少跑一輪，不要兩邊一起跑）
                gen = None
                self.on_change("ops.lease.error", {"lease": self.name, "owner": self.owner, "error": str(e)[:200]})
            before, self.generation = self.generation, gen
            if before is None and gen is not None:
                self.on_change("ops.lease.acquired", {"lease": self.name, "owner": self.owner, "generation": gen})
            elif before is not None and gen is None:
                self.on_change("ops.lease.lost", {"lease": self.name, "owner": self.owner, "generation": before})
            return gen is not None

    def start_heartbeat(self) -> None:
        """持有期間每 ttl/3 秒續約，例行作業跑得比 ttl 久也不會被別台接手。"""
        if self._hb is not None:
            return
        def beat() -> None:
            while not self._stop.wait(self.ttl_s / 3):
                if self.generation is not None:
                    self.ensure()
        self._hb = threading.Thread(target=beat, name=f"lease-{self.name}", daemon=True); self._hb.start()

    def stop(self, release: bool = True) -> None:
        self._stop.set()
        if release and self.generation is not None:
            sql = f"UPDATE {self.pg.t('ops_leases')} SET until = 0 WHERE name = $1 AND owner = $2"
            self._run(lambda c: c.execute(sql, self.name, self.owner))
            self.generation = None

    def holder(self) -> dict[str, Any] | None:
        sql = f"SELECT owner, until - extract(epoch FROM clock_timestamp()) AS remaining_s, generation FROM {self.pg.t('ops_leases')} WHERE name = $1"
        row = self._run(lambda c: c.fetchrow(sql, self.name))
        return dict(row) if row else None


def selftest() -> None:
    import time
    if not os.environ.get("DMP_PG_DSN"):
        print("lease self-test 略過（未設 DMP_PG_DSN）"); return
    from ..api.store_pg import PostgresStore
    schema = f"t_{uuid.uuid4().hex[:10]}"
    pgs = [PostgresStore(os.environ["DMP_PG_DSN"], schema=schema) for _ in range(2)]     # 兩個連線池＝兩份 worker
    try:
        events: list[tuple[str, dict[str, Any]]] = []
        a = PgLease(pgs[0], "dmp-tick", "worker-a", ttl_s=1.5, on_change=lambda e, d: events.append((e, d)))
        b = PgLease(pgs[1], "dmp-tick", "worker-b", ttl_s=1.5, on_change=lambda e, d: events.append((e, d)))
        assert a.ensure() and not b.ensure() and a.ensure() and a.generation == 1          # 互斥；自己續約不換代
        a.start_heartbeat(); time.sleep(2.5)
        assert not b.ensure(), "心跳中的租約不應被接手"
        a._stop.set(); time.sleep(2.0)                                                    # 模擬 A 卡死：心跳停
        assert b.ensure() and b.generation == 2 and b.holder()["owner"] == "worker-b"      # 過期 → B 接手、generation +1
        assert not a.ensure() and a.generation is None and ("ops.lease.lost", {"lease": "dmp-tick", "owner": "worker-a", "generation": 1}) in events
        b.stop()
        assert a.ensure()                                                                 # B 正常停止會釋放 → A 立刻可拿
        # 併發搶：每輪兩邊同時 ensure，永遠恰好一個主控
        a.stop()
        for i in range(10):
            la = PgLease(pgs[0], f"race-{i}", "a", ttl_s=30); lb = PgLease(pgs[1], f"race-{i}", "b", ttl_s=30)
            got: list[bool] = []; bar = threading.Barrier(2)
            def go(lease: PgLease, bar: threading.Barrier = bar, got: list[bool] = got) -> None:
                bar.wait(); got.append(lease.ensure())
            ts = [threading.Thread(target=go, args=(x,)) for x in (la, lb)]
            [t.start() for t in ts]; [t.join() for t in ts]
            assert sorted(got) == [False, True], got
        # 審查情境：兩份 worker 同時跑同一輪例行（例：看板定時寄送），包上主控租約後只執行一次
        sent: list[str] = []
        wa = PgLease(pgs[0], "dmp-tick-2", "wa", ttl_s=30); wb = PgLease(pgs[1], "dmp-tick-2", "wb", ttl_s=30)
        bar2 = threading.Barrier(2)
        def worker(lease: PgLease) -> None:
            bar2.wait()
            if lease.ensure():
                sent.append(lease.owner)
        ts = [threading.Thread(target=worker, args=(x,)) for x in (wa, wb)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert len(sent) == 1, sent
        ticks = _two_tickers(pgs)
        assert sorted(x > 0 for x in ticks) == [False, True], ticks                            # 真的 start_ticker：只有一份 worker 在跑
        print(f"lease self-test ok: mutex/renew/heartbeat/takeover(gen 1→2)/release/race×10/routine-once/start_ticker{ticks}; events={len(events)}")
    finally:
        pgs[0].drop_schema()
        for pg in pgs:
            pg.close()


def _two_tickers(pgs: list[Any]) -> list[int]:
    """兩份 worker 各自跑 backend.api.main.start_ticker（每輪 60 秒縮成 0.02 秒），回傳各自執行看板寄送 tick 的次數。"""
    import time
    from types import SimpleNamespace
    os.environ["DMP_BUILD_APP"] = "0"
    from ..api import main as api_main

    class Share:
        def __init__(self) -> None: self.n = 0
        def tick(self, *a: Any, **k: Any) -> list[Any]:
            self.n += 1; return []
    shares = [Share(), Share()]
    real_sleep = api_main.time.sleep
    api_main.time = SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")})   # 只換 main 看到的 time，不動全域
    api_main.time.sleep = lambda s: real_sleep(0.02)
    stops = [api_main.start_ticker(SimpleNamespace(state=SimpleNamespace(dashboard_share=sh)), pg, object()) for pg, sh in zip(pgs, shares, strict=True)]
    threading.Event().wait(1.5)
    for st in stops:
        st.set()
    threading.Event().wait(0.3)
    api_main.time = time
    return [sh.n for sh in shares]


if __name__ == "__main__":
    selftest()
