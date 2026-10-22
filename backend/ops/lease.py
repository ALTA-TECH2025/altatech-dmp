"""背景作業的主控租約（docs/44 §5a）：多份 worker 同時開著時，只有持有租約的那份跑例行作業。

為什麼：start_ticker 直接呼叫各服務 tick（同步、看板寄送、分群、推送、資料輸出、告警通知），這些 tick 自己沒有跨程序 claim；
兩份 worker 同時跑會重複執行（審查重現：同一張看板寄兩封）。逐項加 claim 改動面太大，先用「一個主控」把整段例行包起來。

- 租約存在 PostgreSQL 控制面一列（name 主鍵）；時間一律用資料庫時鐘（clock_timestamp），不受各主機時鐘偏差影響。
- 取得／續約是同一條 UPSERT：沒人持有、已過期、或本來就是自己 → 拿到；換人時 generation +1（給稽核與日後的 fencing）。
- 持有者有心跳執行緒每 ttl/3 秒續約；程序死掉或卡死到心跳也停，ttl 秒後別台接手。
- 三道防線：
  1. 本機期限：每次成功續約後，只在「續約前的時間點 + ttl×2/3」之前視為有效（比資料庫上的到期早 ttl/3），
     心跳連不上資料庫就立刻視為失去。start_ticker 每段例行開始前都檢查，失效就中止本輪。
  2. 資料庫 fencing：對外副作用（看板寄送、推送、資料輸出、告警通知）送出前呼叫 verify()／claim()，
     以資料庫時鐘確認「這個 owner、這一代、尚未到期」才放行；別台接手後舊主控的 verify 一律失敗。
  3. 唯一 claim：看板定時寄送以「排程 × 日期」claim（ops_claims 主鍵），兩邊都通過 fencing 也只會寄一次。
- 已知限制：單一個已開始的外部呼叫（例如正在送出的一封信）無法中途取消；它開始前已通過 fencing，
  而在它結束前別台最快要 ttl/3 之後才可能接手（本機期限比資料庫早 ttl/3 失效）。

用法：DMP_PG_DSN=… python3 -m backend.ops.lease     # 自測：互斥、心跳、過期接手、generation、併發搶、包住例行只執行一次
"""
from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS {s}.ops_leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, until DOUBLE PRECISION NOT NULL, generation BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS {s}.ops_claims (key TEXT PRIMARY KEY, lease TEXT NOT NULL, owner TEXT NOT NULL, generation BIGINT NOT NULL, at DOUBLE PRECISION NOT NULL);
"""
HELD = "EXISTS (SELECT 1 FROM {t} WHERE name = $1 AND owner = $2 AND generation = $3 AND until > extract(epoch FROM clock_timestamp()))"
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
        self._deadline = 0.0                                     # 本機單調時鐘：超過就視為失去（比資料庫到期早 ttl/3）
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
            t0 = time.monotonic()
            try:
                gen = self._run(lambda c: c.fetchval(sql, self.name, self.owner, float(self.ttl_s)))
            except Exception as e:                              # 資料庫連不上 → 保守視為沒持有（寧可少跑一輪，不要兩邊一起跑）
                gen = None
                self.on_change("ops.lease.error", {"lease": self.name, "owner": self.owner, "error": str(e)[:200]})
            before, self.generation = self.generation, gen
            self._deadline = t0 + self.ttl_s * 2 / 3 if gen is not None else 0.0
            if before is None and gen is not None:
                self.on_change("ops.lease.acquired", {"lease": self.name, "owner": self.owner, "generation": gen})
            elif before is not None and gen is None:
                self.on_change("ops.lease.lost", {"lease": self.name, "owner": self.owner, "generation": before})
            return gen is not None

    def valid(self) -> bool:
        """本機判斷（不連資料庫）：仍持有且未超過本機期限。"""
        return self.generation is not None and time.monotonic() < self._deadline

    def verify(self) -> bool:
        """資料庫 fencing：以資料庫時鐘確認仍是這一代的持有者。對外副作用前呼叫；連不上就當作不是。"""
        gen = self.generation
        if gen is None or not self.valid():
            return False
        sql = f"SELECT {HELD.format(t=self.pg.t('ops_leases'))}"
        try:
            return bool(self._run(lambda c: c.fetchval(sql, self.name, self.owner, gen)))
        except Exception:
            return False

    def claim(self, key: str) -> bool:
        """fencing ＋ 唯一：仍是持有者且這個 key 沒人 claim 過才回 True（同一件事只做一次）。"""
        gen = self.generation
        if gen is None or not self.valid():
            return False
        t = self.pg.t("ops_claims")
        sql = (f"INSERT INTO {t} (key, lease, owner, generation, at) SELECT $4, $1, $2, $3, extract(epoch FROM clock_timestamp()) "
               f"WHERE {HELD.format(t=self.pg.t('ops_leases'))} ON CONFLICT (key) DO NOTHING RETURNING key")
        purge = f"DELETE FROM {t} WHERE at < extract(epoch FROM clock_timestamp()) - 40 * 86400"      # 保留 40 天

        async def go(c: Any) -> Any:
            await c.execute(purge)
            return await c.fetchval(sql, self.name, self.owner, gen, key)
        try:
            return self._run(go) is not None
        except Exception:
            return False

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
            with contextlib.suppress(Exception):                # 連不上就讓它自然過期
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
        # fencing：verify／claim 只對「這一代、未到期」的持有者成立
        fa = PgLease(pgs[0], "fence", "fa", ttl_s=1.5); fb = PgLease(pgs[1], "fence", "fb", ttl_s=1.5)
        assert fa.ensure() and fa.verify() and fa.claim("k1") and not fa.claim("k1")                    # 同一個 key 只成功一次
        assert not fb.ensure() and not fb.verify() and not fb.claim("k2")                                # 非持有者
        time.sleep(1.1)
        assert not fa.valid() and not fa.verify() and fa.holder()["remaining_s"] > 0                    # 本機期限（ttl×2/3）先到，資料庫還沒過期
        time.sleep(0.6)
        assert fb.ensure() and fb.claim("k2") and not fa.claim("k3") and not fa.verify()                 # B 接手後舊主控一律被擋
        ticks = _two_tickers(pgs)
        lost = _failover_tickers(pgs)
        assert lost["a_share_after_loss"] == 0 and lost["b_share"] > 0 and lost["a_fence_after_loss"] == (False, False), lost
        assert sorted(x > 0 for x in ticks) == [False, True], ticks                            # 真的 start_ticker：只有一份 worker 在跑
        print(f"lease self-test ok: mutex/renew/heartbeat/takeover(gen 1→2)/release/race×10/routine-once/fence(verify,claim,local-deadline)/start_ticker{ticks}/failover{lost}; events={len(events)}")
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


def _failover_tickers(pgs: list[Any]) -> dict[str, Any]:
    """長作業期間 A 連不上資料庫（心跳失敗）→ B 在 ttl 後接手：A 本輪剩下的作業不得再跑、A 的 fencing 一律失敗。"""
    import time
    from types import SimpleNamespace
    os.environ["DMP_BUILD_APP"] = "0"; os.environ["DMP_TICK_LEASE_S"] = "1.5"
    from ..api import main as api_main

    class Share:
        def __init__(self) -> None: self.n = 0; self.fence: Any = None
        def tick(self, *a: Any, **k: Any) -> list[Any]:
            self.n += 1; return []
    sa, sb = Share(), Share()
    a_running, release = threading.Event(), threading.Event()
    app_a = SimpleNamespace(state=SimpleNamespace(dashboard_share=sa))
    app_b = SimpleNamespace(state=SimpleNamespace(dashboard_share=sb))

    class LongSync:
        """A 的同步作業：執行中 A 與資料庫斷線、心跳續約失敗；等 B 接手後才結束。"""
        def tick(self) -> None:
            lease = app_a.state.tick_lease
            def down(fn: Any) -> Any:
                raise ConnectionError("synthetic: control-plane unreachable")
            lease._run = down
            lease.ensure()                                       # 心跳這一下失敗
            a_running.set(); release.wait(15)
    app_a.state.scheduler = LongSync()
    real_sleep = api_main.time.sleep
    api_main.time = SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")})
    api_main.time.sleep = lambda s: real_sleep(0.02)
    try:
        stop_a = api_main.start_ticker(app_a, pgs[0], object())
        assert a_running.wait(10), "A 沒有開始長作業"
        stop_b = api_main.start_ticker(app_b, pgs[1], object())
        t_end = time.monotonic() + 10
        while sb.n == 0 and time.monotonic() < t_end:
            threading.Event().wait(0.05)
        release.set(); threading.Event().wait(0.5)                # A 的長作業結束，回到本輪剩下的步驟
        lease_a = app_a.state.tick_lease
        out = {"a_share_after_loss": sa.n, "b_share": sb.n, "a_fence_after_loss": (lease_a.verify(), lease_a.claim("x"))}
        stop_a.set(); stop_b.set(); threading.Event().wait(0.3)
        return out
    finally:
        api_main.time = time; os.environ.pop("DMP_TICK_LEASE_S", None)


if __name__ == "__main__":
    selftest()
