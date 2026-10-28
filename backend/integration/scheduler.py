"""同步任務排程器（第一階段微批版，docs/06 §7.1、docs/23 §2）。

能力：cron（5 欄）／間隔排程、任務依賴（上游同一窗口成功才跑）、失敗重試與退避、增量水位（watermark）、
Schema 變更策略（auto_map／ignore／pause）、執行紀錄與日誌、手動執行（排隊）、啟停。執行本體由 runner 提供（LocalRunner：檔案 → 模型 → ClickHouse）。
狀態：沒有控制面 PostgreSQL 時存 SQLite（本機、CI、quickstart）；有的話存 PostgreSQL（PgSchedDB，schema `<控制面>_sched`），
API 與 worker 拆開或切到主機 2 接手後同一份狀態（docs/44 §5a、docs/superpowers/specs/2026-10-28-scheduler-pg-design.md）。
- 只有持有主控租約的 worker 跑 tick；「立即執行」由 API 登記成 queued，下一次 tick 執行。
- 結案要「仍是 running、仍是我、租約仍有效」才寫入；新主控把別人留下的 running 標成 interrupted 並依重試設定重跑。
- 畫面建的模型存在 models 表（任務 model_path = db:<id>）；執行紀錄與告警保留 365 天。

時鐘可注入（tick(now)），所以排程邏輯可以用模擬時間完整測試。
"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

DDL = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, model_path TEXT NOT NULL, cron TEXT, interval_min INTEGER, mode TEXT NOT NULL,
  depends_on TEXT NOT NULL DEFAULT '[]', retries INTEGER NOT NULL DEFAULT 0, backoff_s INTEGER NOT NULL DEFAULT 60,
  schema_change TEXT NOT NULL DEFAULT 'pause', enabled INTEGER NOT NULL DEFAULT 1, paused_reason TEXT,
  source TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, scheduled_for TEXT NOT NULL, attempt INTEGER NOT NULL,
  status TEXT NOT NULL, started_at TEXT, finished_at TEXT, next_retry_at TEXT, trigger TEXT NOT NULL,
  rows_in INTEGER DEFAULT 0, rows_ok INTEGER DEFAULT 0, rows_quarantined INTEGER DEFAULT 0, rows_rejected INTEGER DEFAULT 0,
  rows_loaded INTEGER DEFAULT 0, watermark_from TEXT, watermark_to TEXT, error TEXT, log TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS runs_task ON runs(task_id, scheduled_for);
CREATE TABLE IF NOT EXISTS watermarks (task_id TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS known_fields (task_id TEXT PRIMARY KEY, fields TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task_id TEXT, level TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, yaml TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
DDL_AFTER = """
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_queued ON runs(task_id) WHERE status = 'queued';
"""
PG_DDL = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", DDL).replace(
    "log TEXT NOT NULL DEFAULT '');", "log TEXT NOT NULL DEFAULT '', owner TEXT, generation BIGINT);") + DDL_AFTER
KEEP_DAYS = 365                                             # 執行紀錄與告警保留天數（Windy 2026-10-28）


class _Rows:
    def __init__(self, rows: list[Any]): self.rows = rows
    def fetchone(self) -> Any: return self.rows[0] if self.rows else None
    def fetchall(self) -> list[Any]: return self.rows
    def __iter__(self) -> Any: return iter(self.rows)


class PgSchedDB:
    """Scheduler 的 PostgreSQL 連線，介面模仿 sqlite3（execute／commit／rollback），兩邊共用同一份 SQL（? 轉 $n）。
    - 用控制面連線池裡一條專用連線，search_path 指到 `<schema>_sched`；所有呼叫經控制面的事件迴圈執行緒。
    - 交易語意同 sqlite3：第一條 INSERT／UPDATE／DELETE 開交易，commit() 結束；交易期間其他執行緒等待（不會混進別人的交易）。"""
    def __init__(self, pg: Any) -> None:
        self.pg, self.schema = pg, f"{pg.schema}_sched"
        self.conn: Any = None
        self._tx = False
        self._lock = threading.RLock()
        import asyncpg
        x = asyncpg.exceptions
        self._conn_errors = (x.ConnectionDoesNotExistError, x.InterfaceError, x.InternalClientError, x.OperatorInterventionError,
                             x.PostgresConnectionError, ConnectionError, OSError)                 # 伺服器端踢掉連線後 asyncpg 下一次操作可能報 InternalClientError
        self._run(self._connect())
        self.executescript(PG_DDL)

    def _run(self, coro: Any) -> Any:
        return self.pg._loop.run(coro)

    async def _connect(self) -> None:
        self.conn = await self.pg.pool.acquire()
        await self.conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}; SET search_path TO {self.schema}")

    @staticmethod
    def _sql(sql: str) -> str:
        n = iter(range(1, 10_000))
        return re.sub(r"\?", lambda _: f"${next(n)}", sql)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        write = sql.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE")
        with self._lock:
            for attempt in (0, 1):
                fresh = not self._tx                          # 交易中途斷線：前面的語句已丟失，不能只重送這一條
                try:
                    if write and not self._tx:
                        self._lock.acquire(); self._tx = True   # 交易這一層鎖到 commit()／rollback() 才放
                        self._run(self.conn.execute("BEGIN"))
                    return _Rows(list(self._run(self.conn.fetch(self._sql(sql), *params))))
                except self._conn_errors:
                    self._abort(); self._reconnect()            # 主備切換後 pg-primary 換主機：重連
                    if attempt or not fresh: raise
                except BaseException:
                    self._abort(); raise
        raise AssertionError("unreachable")

    def _abort(self) -> None:
        if self._tx:
            self._tx = False
            try:
                self._run(self.conn.execute("ROLLBACK"))
            except Exception:
                pass
            finally:
                self._lock.release()

    def _reconnect(self) -> None:
        old, self.conn = self.conn, None
        async def drop() -> None:                               # 斷掉的連線直接關閉再交還：連線池會補一條新的，不會把壞連線再借出去
            old.terminate()
            await self.pg.pool.release(old)
        with contextlib.suppress(Exception):
            self._run(drop())
        self._run(self._connect())

    def executescript(self, script: str) -> None:
        with self._lock:
            self._run(self.conn.execute(script))

    def _end(self, verb: str) -> None:
        with self._lock:
            if self._tx:
                self._tx = False
                try:
                    self._run(self.conn.execute(verb))
                finally:
                    self._lock.release()

    def close(self) -> None:
        """把專用連線還給連線池（連線池 close 會等所有借出的連線歸還）。"""
        with self._lock:
            if self.conn is not None:
                self._run(self.pg.pool.release(self.conn)); self.conn = None

    def commit(self) -> None: self._end("COMMIT")
    def rollback(self) -> None: self._end("ROLLBACK")

iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds")
parse = lambda s: datetime.fromisoformat(s)


# ---------------------------------------------------------------- cron（分 時 日 月 週；支援 * , - */n）
def _field_ok(spec: str, value: int, lo: int, hi: int) -> bool:
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/"); step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def cron_match(expr: str, t: datetime) -> bool:
    m, h, dom, mon, dow = expr.split()
    return (_field_ok(m, t.minute, 0, 59) and _field_ok(h, t.hour, 0, 23) and _field_ok(dom, t.day, 1, 31)
            and _field_ok(mon, t.month, 1, 12) and _field_ok(dow, t.isoweekday() % 7, 0, 6))


def cron_prev(expr: str, now: datetime) -> datetime:
    """最近一次（含 now）符合 cron 的分鐘；最多回看 366 天。"""
    t = now.replace(second=0, microsecond=0)
    for _ in range(366 * 24 * 60):
        if cron_match(expr, t):
            return t
        t -= timedelta(minutes=1)
    raise ValueError(f"cron 一年內無匹配：{expr}")


# ---------------------------------------------------------------- runner 介面
@dataclass
class RunResult:
    rows_in: int = 0
    rows_ok: int = 0
    rows_quarantined: int = 0
    rows_rejected: int = 0
    rows_loaded: int = 0
    watermark_to: str | None = None
    new_fields: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


class Runner(Protocol):
    def run(self, task: dict[str, Any], *, watermark: str | None, batch_id: str, known_fields: set[str] | None,
            now: datetime, log: Callable[[str], None]) -> RunResult: ...


class SchemaChanged(Exception):
    def __init__(self, new_fields: list[str]):
        super().__init__(f"schema changed: {new_fields}"); self.new_fields = new_fields


# ---------------------------------------------------------------- 排程器
class Scheduler:
    def __init__(self, db: str | Path | PgSchedDB, runner: Runner):
        """db：SQLite 路徑（本機、CI）或 PgSchedDB（控制面 PostgreSQL）。"""
        if isinstance(db, PgSchedDB):
            self.db: Any = db
        else:
            self.db = sqlite3.connect(str(db), check_same_thread=False); self.db.row_factory = sqlite3.Row   # API 在執行緒池呼叫；呼叫端加鎖
            self.db.executescript(DDL)
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(runs)")}
            for c, typ in (("owner", "TEXT"), ("generation", "INTEGER")):    # 舊的本機排程檔補欄位
                if c not in cols: self.db.execute(f"ALTER TABLE runs ADD COLUMN {c} {typ}")
            self.db.executescript(DDL_AFTER); self.db.commit()
        self.runner = runner
        self.fence: Any = None                     # 主控租約（ops/lease.PgLease）；start_ticker 設定。沒有就是單程序（本機）
        self.local_owner = f"local:{uuid.uuid4().hex[:8]}"
        self._purged_on: str | None = None

    def _who(self) -> tuple[str, int]:
        f = self.fence
        return (f.owner, int(f.generation)) if f is not None and f.generation is not None else (self.local_owner, 0)

    def _held(self) -> tuple[str, tuple[Any, ...]]:
        """結案條件：租約仍有效（資料庫時鐘）。沒有租約（本機）就不加條件。"""
        f = self.fence
        if f is None or not isinstance(self.db, PgSchedDB):
            return "", ()
        return (f" AND EXISTS (SELECT 1 FROM {f.pg.t('ops_leases')} WHERE name=? AND owner=? AND generation=? AND until > extract(epoch FROM clock_timestamp()))",
                (f.name, f.owner, int(f.generation or -1)))

    # ---- 畫面建的模型（存資料庫，接管不遺失）
    def put_model(self, id: str, yaml_text: str, now: datetime | None = None) -> str:
        self.db.execute("INSERT INTO models (id, yaml, updated_at) VALUES (?,?,?) ON CONFLICT (id) DO UPDATE SET yaml=excluded.yaml, updated_at=excluded.updated_at",
                        (id, yaml_text, iso(now or datetime.now(UTC)))); self.db.commit()
        return f"db:{id}"

    def model_text(self, model_path: str) -> str:
        r = self.db.execute("SELECT yaml FROM models WHERE id=?", (model_path.removeprefix("db:"),)).fetchone()
        if not r: raise KeyError(model_path)
        return r["yaml"]

    # ---- 任務 CRUD
    def add_task(self, id: str, model_path: str, *, cron: str | None = None, interval_min: int | None = None, mode: str = "incremental",
                 depends_on: list[str] | None = None, retries: int = 0, backoff_s: int = 60, schema_change: str = "pause",
                 source: dict[str, Any] | None = None, now: datetime | None = None) -> None:
        assert (cron is None) != (interval_min is None), "cron 與 interval_min 擇一"
        assert schema_change in ("auto_map", "ignore", "pause")
        if cron: cron_match(cron, datetime(2026, 1, 1, tzinfo=UTC))   # 語法檢查
        self.db.execute("INSERT INTO tasks (id, model_path, cron, interval_min, mode, depends_on, retries, backoff_s, schema_change, enabled, paused_reason, source, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,1,NULL,?,?) ON CONFLICT (id) DO UPDATE SET model_path=excluded.model_path, cron=excluded.cron, interval_min=excluded.interval_min, "
                        "mode=excluded.mode, depends_on=excluded.depends_on, retries=excluded.retries, backoff_s=excluded.backoff_s, schema_change=excluded.schema_change, "
                        "enabled=1, paused_reason=NULL, source=excluded.source, created_at=excluded.created_at",
                        (id, model_path, cron, interval_min, mode, json.dumps(depends_on or []), retries, backoff_s, schema_change,
                         json.dumps(source or {}), iso(now or datetime.now(UTC))))
        self.db.commit()

    def set_enabled(self, task_id: str, enabled: bool, reason: str | None = None) -> None:
        self.db.execute("UPDATE tasks SET enabled=?, paused_reason=? WHERE id=?", (1 if enabled else 0, reason, task_id)); self.db.commit()

    def task(self, task_id: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r: raise KeyError(task_id)
        d = dict(r); d["depends_on"] = json.loads(d["depends_on"]); d["source"] = json.loads(d["source"])
        if d["model_path"].startswith("db:"):
            d["model_yaml"] = self.model_text(d["model_path"])
        return d

    def tasks(self) -> list[dict[str, Any]]:
        return [self.task(r["id"]) for r in self.db.execute("SELECT id FROM tasks ORDER BY id")]

    def runs(self, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM runs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))]

    def alerts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM alerts ORDER BY id")]

    def watermark(self, task_id: str) -> str | None:
        r = self.db.execute("SELECT value FROM watermarks WHERE task_id=?", (task_id,)).fetchone(); return r["value"] if r else None

    # ---- 排程判斷
    def _due_slot(self, t: dict[str, Any], now: datetime) -> datetime | None:
        if t["cron"]:
            return cron_prev(t["cron"], now)
        step = t["interval_min"]
        return now.replace(second=0, microsecond=0, minute=(now.minute // step) * step)

    def _has_run(self, task_id: str, slot: datetime, statuses: tuple[str, ...] = ("success", "running", "retry_wait", "retried", "failed", "paused")) -> bool:
        q = f"SELECT 1 FROM runs WHERE task_id=? AND scheduled_for=? AND status IN ({','.join('?' * len(statuses))}) LIMIT 1"
        return self.db.execute(q, (task_id, iso(slot), *statuses)).fetchone() is not None

    def _deps_ready(self, t: dict[str, Any], slot: datetime) -> tuple[bool, str]:
        for dep in t["depends_on"]:
            r = self.db.execute("SELECT scheduled_for, status FROM runs WHERE task_id=? AND status='success' ORDER BY id DESC LIMIT 1", (dep,)).fetchone()
            if not r:
                return False, f"等待上游 {dep}（尚無成功執行）"
            dep_task = self.task(dep)
            dep_slot = self._due_slot(dep_task, slot)      # 上游在本窗口應該跑的那一次
            if parse(r["scheduled_for"]) < dep_slot:
                return False, f"等待上游 {dep}（{dep_slot:%H:%M} 那次尚未成功）"
        return True, ""

    def status(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC); out = []
        for t in self.tasks():
            last = self.runs(t["id"], 1)
            out.append({"id": t["id"], "enabled": bool(t["enabled"]), "paused_reason": t["paused_reason"], "schedule": t["cron"] or f"every {t['interval_min']}m",
                        "mode": t["mode"], "depends_on": t["depends_on"], "next_slot": iso(self._due_slot(t, now + (timedelta(minutes=t["interval_min"]) if t["interval_min"] else timedelta(days=1)))),
                        "last": {k: last[0][k] for k in ("scheduled_for", "status", "rows_in", "rows_ok", "rows_quarantined", "rows_rejected", "rows_loaded", "attempt", "error")} if last else None,
                        "watermark": self.watermark(t["id"])})
        return out

    # ---- tick：一次掃描，啟動到期且上游就緒的任務；處理到期的重試
    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC); started = []
        self._recover(now); self._purge(now)
        # 到期的重試
        for r in self.db.execute("SELECT * FROM runs WHERE status='retry_wait' AND next_retry_at<=? ORDER BY id", (iso(now),)).fetchall():
            t = self.task(r["task_id"])
            if not t["enabled"]: continue
            self.db.execute("UPDATE runs SET status='retried' WHERE id=?", (r["id"],))   # 舊紀錄結案，避免重複觸發
            started.append(self._execute(t, parse(r["scheduled_for"]), r["attempt"] + 1, "retry", now))
        # 排隊中的手動執行（API 只登記，主控在這裡跑）
        for r in self.db.execute("SELECT * FROM runs WHERE status='queued' ORDER BY id").fetchall():
            res = self._execute(self.task(r["task_id"]), parse(r["scheduled_for"]), r["attempt"], "manual", now, run_id=r["id"])
            if res: started.append(res)
        for t in self.tasks():
            if not t["enabled"]: continue
            slot = self._due_slot(t, now)
            if slot < parse(t["created_at"]).replace(second=0, microsecond=0): continue   # 不補跑建立前的窗口
            if self._has_run(t["id"], slot): continue
            ok, why = self._deps_ready(t, slot)
            if not ok:
                self._log_skip(t["id"], slot, why, now); continue
            started.append(self._execute(t, slot, 1, "schedule", now))
        return started

    def run_now(self, task_id: str, now: datetime | None = None) -> dict[str, Any]:
        """同步執行（CLI、測試）。API 用 enqueue：交給主控 worker 跑。"""
        now = now or datetime.now(UTC)
        return self._execute(self.task(task_id), now.replace(second=0, microsecond=0), 1, "manual", now)

    def enqueue(self, task_id: str, now: datetime | None = None) -> dict[str, Any]:
        """「立即執行」：登記 queued，下一次 tick 由主控執行。已有 queued／running 就回那一筆（重按不會排兩次）。"""
        self.task(task_id)
        slot = iso((now or datetime.now(UTC)).replace(second=0, microsecond=0))
        active = "SELECT id, status, scheduled_for FROM runs WHERE task_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1"
        cur = self.db.execute(active, (task_id,)).fetchone()
        if cur is None:                                           # 部分唯一索引擋住兩個 API 同時排同一任務
            r = self.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, trigger) VALUES (?,?,1,'queued','manual') ON CONFLICT DO NOTHING RETURNING id",
                                (task_id, slot)).fetchone()
            self.db.commit()
            cur = self.db.execute(active, (task_id,)).fetchone() or ({"id": r["id"], "status": "queued", "scheduled_for": slot} if r else None)
        return {"run_id": cur["id"] if cur else None, "task_id": task_id, "status": cur["status"] if cur else "queued", "scheduled_for": cur["scheduled_for"] if cur else slot}

    def _recover(self, now: datetime) -> None:
        """別人（前一任主控、已結束的程序）留下的 running：標 interrupted，依任務重試設定排重試，用完就 failed＋告警。"""
        owner, gen = self._who()
        rows = self.db.execute("UPDATE runs SET status='interrupted', finished_at=?, error=? WHERE status='running' AND NOT (COALESCE(owner,'')=? AND COALESCE(generation,0)=?) "
                               "RETURNING id, task_id, scheduled_for, attempt", (iso(now), "執行中斷：前一任主控未完成（程序結束或失去主控）", owner, gen)).fetchall()
        for r in rows:
            try:
                t = self.task(r["task_id"])
            except KeyError:
                continue
            if r["attempt"] <= t["retries"]:
                self.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, next_retry_at, trigger, error) VALUES (?,?,?,'retry_wait',?,'retry',?)",
                                (r["task_id"], r["scheduled_for"], r["attempt"], iso(now), "前一次執行中斷，重跑"))
            else:
                self._alert(now, r["task_id"], "error", f"執行中斷且重試已用完（第 {r['attempt']} 次）")
        self.db.commit()

    def _purge(self, now: datetime) -> None:
        """每天一次：刪除超過 KEEP_DAYS 天的執行紀錄與告警（任務、水位、已知欄位、模型不刪）。"""
        day = now.strftime("%Y-%m-%d")
        if self._purged_on == day:
            return
        cut = iso(now - timedelta(days=KEEP_DAYS))
        self.db.execute("DELETE FROM runs WHERE scheduled_for < ? AND status NOT IN ('queued','running','retry_wait')", (cut,))
        self.db.execute("DELETE FROM alerts WHERE at < ?", (cut,)); self.db.commit()
        self._purged_on = day

    def _log_skip(self, task_id: str, slot: datetime, why: str, now: datetime) -> None:
        # 同一窗口只記一次 skipped，避免每次 tick 都寫
        if self.db.execute("SELECT 1 FROM runs WHERE task_id=? AND scheduled_for=? AND status='skipped'", (task_id, iso(slot))).fetchone(): return
        self.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, trigger, log) VALUES (?,?,0,'skipped','schedule',?)", (task_id, iso(slot), why)); self.db.commit()

    def _execute(self, t: dict[str, Any], slot: datetime, attempt: int, trigger: str, now: datetime, run_id: int | None = None) -> dict[str, Any] | None:
        logs: list[str] = []
        log = lambda m: logs.append(f"{iso(now)} {m}")
        owner, gen = self._who()
        self.db.execute("DELETE FROM runs WHERE task_id=? AND scheduled_for=? AND status='skipped'", (t["id"], iso(slot)))
        if run_id is not None:                                   # 排隊的手動執行：queued → running（別人先接走就不跑）
            if self.db.execute("UPDATE runs SET status='running', started_at=?, watermark_from=?, owner=?, generation=? WHERE id=? AND status='queued' RETURNING id",
                               (iso(now), self.watermark(t["id"]), owner, gen, run_id)).fetchone() is None:
                self.db.commit(); return None
        else:
            run_id = self.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, started_at, trigger, watermark_from, owner, generation) VALUES (?,?,?,'running',?,?,?,?,?) RETURNING id",
                                     (t["id"], iso(slot), attempt, iso(now), trigger, self.watermark(t["id"]), owner, gen)).fetchone()["id"]
        self.db.commit()
        batch_id = f"{t['id']}:{slot:%Y%m%dT%H%M}:{attempt}"
        kf_row = self.db.execute("SELECT fields FROM known_fields WHERE task_id=?", (t["id"],)).fetchone()
        known = set(json.loads(kf_row["fields"])) if kf_row else None
        held, held_args = self._held()
        mine = f" WHERE id=? AND status='running' AND owner=? AND generation=?{held} RETURNING id"   # 結案條件：仍是 running、仍是我、租約仍有效
        me = (run_id, owner, gen, *held_args)

        def finish(sql: str, args: tuple[Any, ...]) -> bool:
            if self.db.execute(sql + mine, args + me).fetchone() is None:
                self.db.rollback()
                return False
            return True
        try:
            res = self.runner.run(t, watermark=self.watermark(t["id"]) if t["mode"] == "incremental" else None, batch_id=batch_id,
                                  known_fields=known, now=now, log=log)
            if res.watermark_to is not None: res.watermark_to = str(res.watermark_to)
            status = "success"
            if res.new_fields:
                policy = t["schema_change"]
                if policy == "pause":
                    raise SchemaChanged(res.new_fields)
                log(f"schema change ({policy}): {res.new_fields}")
            if not finish("UPDATE runs SET status=?, finished_at=?, rows_in=?, rows_ok=?, rows_quarantined=?, rows_rejected=?, rows_loaded=?, watermark_to=?, log=?",
                          (status, iso(now), res.rows_in, res.rows_ok, res.rows_quarantined, res.rows_rejected, res.rows_loaded, res.watermark_to, "\n".join(logs + res.log))):
                status = "superseded"
            else:
                if res.new_fields:
                    self._alert(now, t["id"], "warn", f"來源新增欄位 {res.new_fields}，策略 {t['schema_change']}")
                if known is None or res.new_fields:
                    fields = sorted((known or set()) | set(res.new_fields) | set(self._runner_fields(res)))
                    self.db.execute("INSERT INTO known_fields (task_id, fields, version) VALUES (?,?,1) ON CONFLICT (task_id) DO UPDATE SET fields=excluded.fields, version=known_fields.version+1",
                                    (t["id"], json.dumps(fields)))
                if res.watermark_to is not None:
                    self.db.execute("INSERT INTO watermarks (task_id, value, updated_at) VALUES (?,?,?) ON CONFLICT (task_id) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                                    (t["id"], res.watermark_to, iso(now)))
                if res.rows_in and res.rows_quarantined / max(1, res.rows_in) > 0.01:
                    self._alert(now, t["id"], "warn", f"隔離率 {res.rows_quarantined / res.rows_in:.1%} > 1%")
                if res.rows_loaded != res.rows_ok:
                    self._alert(now, t["id"], "error", f"對帳差異：通過 {res.rows_ok} 筆、載入 {res.rows_loaded} 筆")
        except SchemaChanged as e:
            status = "paused"
            if finish("UPDATE runs SET status='paused', finished_at=?, error=?, log=?", (iso(now), str(e), "\n".join(logs))):
                self.db.execute("UPDATE tasks SET enabled=0, paused_reason=? WHERE id=?", (f"schema change: {e.new_fields}", t["id"]))
                self._alert(now, t["id"], "error", f"來源 Schema 變更 {e.new_fields}，任務已暫停待確認")
            else:
                status = "superseded"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"; logs.append(traceback.format_exc(limit=3))
            if attempt <= t["retries"]:
                delay = t["backoff_s"] * (2 ** (attempt - 1)); status = "retry_wait"
                ok = finish("UPDATE runs SET status='retry_wait', finished_at=?, next_retry_at=?, error=?, log=?",
                            (iso(now), iso(now + timedelta(seconds=delay)), err, "\n".join(logs)))
            else:
                status = "failed"
                ok = finish("UPDATE runs SET status='failed', finished_at=?, error=?, log=?", (iso(now), err, "\n".join(logs)))
                if ok: self._alert(now, t["id"], "error", f"重試 {t['retries']} 次後仍失敗：{err}")
            if not ok: status = "superseded"
        self.db.commit()                                         # superseded：已不是主控（或被新主控標成中斷），結果不寫入，交給下一任重跑
        return {"run_id": run_id, "task_id": t["id"], "scheduled_for": iso(slot), "attempt": attempt, "status": status}

    @staticmethod
    def _runner_fields(res: RunResult) -> list[str]:
        return [l.split("fields=", 1)[1].strip("[]").replace("'", "").replace(" ", "").split(",") for l in res.log if l.startswith("fields=")][-1] if any(l.startswith("fields=") for l in res.log) else []

    def _alert(self, now: datetime, task_id: str | None, level: str, message: str) -> None:
        self.db.execute("INSERT INTO alerts(at, task_id, level, message) VALUES (?,?,?,?)", (iso(now), task_id, level, message))


def common_selftest(s: Scheduler, fr: Any, t: datetime) -> None:
    """SQLite 與 PostgreSQL 共用的 #87 驗收：排隊、模型、中斷接手、保留。"""
    ypath = s.put_model("sync_gen", "model:\n  id: gen\n  entity: event\n", t)
    s.add_task("sync_gen", ypath, cron="0 0 1 1 *", mode="full", retries=1, backoff_s=60, now=t)
    assert ypath == "db:sync_gen" and "entity: event" in s.task("sync_gen")["model_yaml"]               # 模型存資料庫，任務以 db: 讀
    q1 = s.enqueue("sync_gen", t); q2 = s.enqueue("sync_gen", t + timedelta(seconds=5))
    assert q1["status"] == "queued" and q2["run_id"] == q1["run_id"], (q1, q2)                           # 重按回同一筆
    gen_calls = lambda: sum(1 for c in fr.calls if c[0] == "sync_gen")
    r = s.tick(t + timedelta(seconds=30))
    assert [x["task_id"] for x in r if x["task_id"] == "sync_gen"] == ["sync_gen"] and gen_calls() == 1 and s.runs("sync_gen", 1)[0]["trigger"] == "manual", r
    s.tick(t + timedelta(seconds=40)); assert gen_calls() == 1                                           # 排隊的只跑一次
    # 別人留下的 running（前一任主控）→ interrupted＋重試
    s.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, started_at, trigger, owner, generation) VALUES (?,?,1,'running',?,'schedule','old-owner',7)",
                 ("sync_gen", iso(t), iso(t))); s.db.commit()
    s.tick(t + timedelta(minutes=2))
    st = [x["status"] for x in s.runs("sync_gen", 5)]
    assert "interrupted" in st and "retried" in st and any(x["attempt"] == 2 and x["status"] == "success" for x in s.runs("sync_gen", 5)), s.runs("sync_gen", 5)
    # 保留：一年多前的紀錄刪掉，近期留著
    s.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, trigger) VALUES (?,?,1,'success','schedule')", ("sync_gen", iso(t - timedelta(days=400)))); s.db.commit()
    s._purged_on = None; s.tick(t + timedelta(minutes=3))
    left = s.db.execute("SELECT scheduled_for FROM runs WHERE task_id=?", ("sync_gen",)).fetchall()
    assert left and all(parse(x["scheduled_for"]) > t - timedelta(days=KEEP_DAYS) for x in left), left


def pg_selftest(fake_runner: Any) -> None:
    """設 DMP_PG_DSN 時：兩份 worker 共用 PostgreSQL；只有主控跑；跑到一半換主控 → 舊主控晚到的結案不寫入。"""
    import os
    import time
    if not os.environ.get("DMP_PG_DSN"):
        print("DMP_PG_DSN 未設定：略過 PostgreSQL 排程自測"); return
    from ..api.store_pg import PostgresStore
    from ..ops.lease import PgLease
    schema = "sched_selftest_" + uuid.uuid4().hex[:6]
    pg = PostgresStore(os.environ["DMP_PG_DSN"], schema=schema)
    try:
        fa, fb = fake_runner(), fake_runner()
        a, b = Scheduler(PgSchedDB(pg), fa), Scheduler(PgSchedDB(pg), fb)
        la, lb = PgLease(pg, "dmp-tick", owner="A", ttl_s=1.5), PgLease(pg, "dmp-tick", owner="B", ttl_s=1.5)
        a.fence, b.fence = la, lb
        t = datetime.now(UTC).replace(second=0, microsecond=0)
        a.add_task("sync_x", "m.yaml", interval_min=15, mode="incremental", retries=2, backoff_s=60, now=t - timedelta(hours=1))
        assert la.ensure() and not lb.ensure()                                       # A 是主控
        assert [x["status"] for x in a.tick(t)] == ["success"] and b.task("sync_x")["id"] == "sync_x"    # B 看得到 A 建的任務與結果
        assert b.watermark("sync_x") == a.watermark("sync_x") is not None
        pid = a.db.execute("SELECT pg_backend_pid() AS p").fetchone()["p"]
        pg._loop.run(pg.pool.execute("SELECT pg_terminate_backend($1)", pid))          # 模擬主備切換斷線：下一次存取自動重連
        assert a.task("sync_x")["id"] == "sync_x" and a.db.execute("SELECT pg_backend_pid() AS p").fetchone()["p"] != pid
        q = b.enqueue("sync_x", t)                                                    # API（B 那份）只登記
        assert q["status"] == "queued" and a.tick(t + timedelta(seconds=20))[0]["run_id"] == q["run_id"]
        common_selftest(a, fa, t + timedelta(minutes=1))
        # 跑到一半：A 失去主控、B 接手並把 A 的 running 標中斷重跑；A 晚到的結案不寫入、水位不被覆蓋
        def slow(task: dict[str, Any], **kw: Any) -> RunResult:
            time.sleep(1.7); assert lb.ensure() and not la.verify()
            b.tick(datetime.now(UTC))
            return RunResult(rows_in=1, rows_ok=1, rows_loaded=1, watermark_to="A-late")
        fa.run = slow  # type: ignore[method-assign]
        q = a.enqueue("sync_x", t + timedelta(minutes=5)); r = a.tick(t + timedelta(minutes=5, seconds=10))
        mine = [x for x in r if x["run_id"] == q["run_id"]][0]
        assert mine["status"] == "superseded" and a.watermark("sync_x") != "A-late", (mine, a.watermark("sync_x"))
        sts = {x["id"]: x["status"] for x in b.runs("sync_x", 10)}
        assert sts[q["run_id"]] == "interrupted" and "success" in sts.values(), sts
        print(f"scheduler pg ok: 兩份 worker 共用、只有主控執行、排隊、中斷接手（舊主控晚到結案未寫入）、模型入庫、保留 {KEEP_DAYS} 天")
    finally:
        for x in (a, b): x.db.close()
        la.stop(); lb.stop()
        pg._loop.run(pg.pool.execute(f"DROP SCHEMA IF EXISTS {schema}_sched CASCADE"))
        pg.drop_schema(); pg.close()


# ---------------------------------------------------------------- 自測（模擬時鐘、假 runner）
if __name__ == "__main__":
    import tempfile

    class FakeRunner:
        def __init__(self): self.calls: list[tuple[str, str, int]] = []; self.fail_times = {}; self.drift = {}
        def run(self, task, *, watermark, batch_id, known_fields, now, log):
            self.calls.append((task["id"], batch_id, len(self.calls)))
            n = self.fail_times.get(task["id"], 0)
            if n:
                self.fail_times[task["id"]] = n - 1; raise ConnectionError("來源逾時")
            res = RunResult(rows_in=100, rows_ok=99, rows_quarantined=1, rows_loaded=99, watermark_to=iso(now), log=["fields=[A,B]"])
            if self.drift.get(task["id"]) and known_fields is not None: res.new_fields = ["NEW_COL"]
            return res

    with tempfile.TemporaryDirectory() as d:
        fr = FakeRunner(); s = Scheduler(Path(d) / "sched.db", fr)
        t0 = datetime(2026, 10, 16, 5, 29, tzinfo=UTC)
        s.add_task("sync_instrument", "m1.yaml", cron="30 5 * * *", mode="full", retries=1, backoff_s=60, schema_change="pause", now=t0)
        s.add_task("sync_orders", "m2.yaml", cron="*/15 * * * *", depends_on=["sync_instrument"], retries=2, backoff_s=30, schema_change="auto_map", now=t0)
        # 05:29：兩個任務的最近窗口（昨日 05:30、今日 05:15）都早於建立時間 → 不補跑
        r = s.tick(t0); assert r == [] and s.runs("sync_orders") == [] and s.runs("sync_instrument") == []
        # 05:30：instrument 跑；orders 05:30 窗口：上游 05:30 那次已成功 → 跑
        r = s.tick(t0 + timedelta(minutes=1)); assert [x["task_id"] for x in r] == ["sync_instrument", "sync_orders"] and all(x["status"] == "success" for x in r), r
        # 同一分鐘再 tick 不重跑
        assert s.tick(t0 + timedelta(minutes=1)) == []
        # 05:45：orders 跑，instrument 不跑；水位帶入
        r = s.tick(t0 + timedelta(minutes=16)); assert [x["task_id"] for x in r] == ["sync_orders"] and s.runs("sync_orders")[0]["watermark_from"] is not None
        # 06:00：來源失敗 → retry_wait（30 s 後），06:00:30 重試再失敗 → retry_wait（60 s），06:01:30 成功
        fr.fail_times["sync_orders"] = 2
        r = s.tick(t0 + timedelta(minutes=31)); assert r[0]["status"] == "retry_wait" and r[0]["attempt"] == 1
        assert s.tick(t0 + timedelta(minutes=31, seconds=10)) == []
        r = s.tick(t0 + timedelta(minutes=31, seconds=30)); assert r[0]["status"] == "retry_wait" and r[0]["attempt"] == 2
        r = s.tick(t0 + timedelta(minutes=32, seconds=30)); assert r[0]["status"] == "success" and r[0]["attempt"] == 3, r
        # 06:15：重試耗盡 → failed + alert
        fr.fail_times["sync_orders"] = 5
        s.tick(t0 + timedelta(minutes=46)); s.tick(t0 + timedelta(minutes=47)); r = s.tick(t0 + timedelta(minutes=48))
        assert r[0]["status"] == "failed" and any("仍失敗" in a["message"] for a in s.alerts())
        fr.fail_times["sync_orders"] = 0
        # Schema drift：orders 是 auto_map → 照跑 + warn；instrument 是 pause → 暫停 + 停用
        fr.drift["sync_orders"] = True; r = s.tick(t0 + timedelta(minutes=61)); assert r[0]["status"] == "success" and "schema change (auto_map)" in s.runs("sync_orders")[0]["log"]
        fr.drift["sync_instrument"] = True; r = s.tick(t0 + timedelta(days=1, minutes=1))
        inst = [x for x in r if x["task_id"] == "sync_instrument"][0]; assert inst["status"] == "paused" and s.task("sync_instrument")["enabled"] == 0
        # 上游暫停 → 下游下一窗口等待
        r = s.tick(t0 + timedelta(days=1, minutes=16)); assert r == [] and "等待上游" in s.runs("sync_orders")[0]["log"]
        # 手動重跑 + 重新啟用
        s.set_enabled("sync_instrument", True); fr.drift["sync_instrument"] = False
        assert s.run_now("sync_instrument", t0 + timedelta(days=1, minutes=20))["status"] == "success"
        st = {x["id"]: x for x in s.status(t0 + timedelta(days=1, minutes=21))}
        assert st["sync_orders"]["last"]["status"] in ("success", "skipped") and st["sync_instrument"]["enabled"]
        assert cron_match("30 5 * * *", datetime(2026, 10, 16, 5, 30)) and not cron_match("30 5 * * 1-5", datetime(2026, 10, 17, 5, 30))  # 週六
        assert cron_prev("*/15 * * * *", datetime(2026, 10, 16, 5, 44)) == datetime(2026, 10, 16, 5, 30)
        # #87：手動執行排隊、模型入庫、中斷接手、保留 365 天（SQLite 版；PostgreSQL 版見下方 pg_selftest）
        common_selftest(s, fr, t0 + timedelta(days=2))
        print("scheduler ok; runs:", len(s.db.execute("SELECT * FROM runs").fetchall()), "alerts:", len(s.alerts()))
    pg_selftest(FakeRunner)
