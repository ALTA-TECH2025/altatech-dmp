"""同步任務排程器（第一階段微批版，docs/06 §7.1、docs/23 §2）。

能力：cron（5 欄）／間隔排程、任務依賴（上游同一窗口成功才跑）、失敗重試與退避、增量水位（watermark）、
Schema 變更策略（auto_map／ignore／pause）、執行紀錄與日誌、手動執行、啟停。狀態存 SQLite（標準函式庫），
正式環境換 PostgreSQL 控制面同一張表結構；執行本體由 runner 提供（LocalRunner：檔案 → 模型 → ClickHouse）。

時鐘可注入（tick(now)），所以排程邏輯可以用模擬時間完整測試。
"""
from __future__ import annotations

import json
import sqlite3
import traceback
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
"""

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
    def __init__(self, db_path: str | Path, runner: Runner):
        self.db = sqlite3.connect(str(db_path)); self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL); self.runner = runner

    # ---- 任務 CRUD
    def add_task(self, id: str, model_path: str, *, cron: str | None = None, interval_min: int | None = None, mode: str = "incremental",
                 depends_on: list[str] | None = None, retries: int = 0, backoff_s: int = 60, schema_change: str = "pause",
                 source: dict[str, Any] | None = None, now: datetime | None = None) -> None:
        assert (cron is None) != (interval_min is None), "cron 與 interval_min 擇一"
        assert schema_change in ("auto_map", "ignore", "pause")
        if cron: cron_match(cron, datetime(2026, 1, 1, tzinfo=UTC))   # 語法檢查
        self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?,1,NULL,?,?)",
                        (id, model_path, cron, interval_min, mode, json.dumps(depends_on or []), retries, backoff_s, schema_change,
                         json.dumps(source or {}), iso(now or datetime.now(UTC))))
        self.db.commit()

    def set_enabled(self, task_id: str, enabled: bool, reason: str | None = None) -> None:
        self.db.execute("UPDATE tasks SET enabled=?, paused_reason=? WHERE id=?", (1 if enabled else 0, reason, task_id)); self.db.commit()

    def task(self, task_id: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r: raise KeyError(task_id)
        d = dict(r); d["depends_on"] = json.loads(d["depends_on"]); d["source"] = json.loads(d["source"]); return d

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
        # 到期的重試
        for r in self.db.execute("SELECT * FROM runs WHERE status='retry_wait' AND next_retry_at<=? ORDER BY id", (iso(now),)).fetchall():
            t = self.task(r["task_id"])
            if not t["enabled"]: continue
            self.db.execute("UPDATE runs SET status='retried' WHERE id=?", (r["id"],))   # 舊紀錄結案，避免重複觸發
            started.append(self._execute(t, parse(r["scheduled_for"]), r["attempt"] + 1, "retry", now))
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
        now = now or datetime.now(UTC)
        return self._execute(self.task(task_id), now.replace(second=0, microsecond=0), 1, "manual", now)

    def _log_skip(self, task_id: str, slot: datetime, why: str, now: datetime) -> None:
        # 同一窗口只記一次 skipped，避免每次 tick 都寫
        if self.db.execute("SELECT 1 FROM runs WHERE task_id=? AND scheduled_for=? AND status='skipped'", (task_id, iso(slot))).fetchone(): return
        self.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, trigger, log) VALUES (?,?,0,'skipped','schedule',?)", (task_id, iso(slot), why)); self.db.commit()

    def _execute(self, t: dict[str, Any], slot: datetime, attempt: int, trigger: str, now: datetime) -> dict[str, Any]:
        logs: list[str] = []
        log = lambda m: logs.append(f"{iso(now)} {m}")
        self.db.execute("DELETE FROM runs WHERE task_id=? AND scheduled_for=? AND status='skipped'", (t["id"], iso(slot)))
        cur = self.db.execute("INSERT INTO runs(task_id, scheduled_for, attempt, status, started_at, trigger, watermark_from) VALUES (?,?,?,'running',?,?,?)",
                              (t["id"], iso(slot), attempt, iso(now), trigger, self.watermark(t["id"])))
        run_id = cur.lastrowid; self.db.commit()
        batch_id = f"{t['id']}:{slot:%Y%m%dT%H%M}:{attempt}"
        kf_row = self.db.execute("SELECT fields FROM known_fields WHERE task_id=?", (t["id"],)).fetchone()
        known = set(json.loads(kf_row["fields"])) if kf_row else None
        try:
            res = self.runner.run(t, watermark=self.watermark(t["id"]) if t["mode"] == "incremental" else None, batch_id=batch_id,
                                  known_fields=known, now=now, log=log)
            status = "success"
            if res.new_fields:
                policy = t["schema_change"]
                if policy == "pause":
                    raise SchemaChanged(res.new_fields)
                log(f"schema change ({policy}): {res.new_fields}")
                self._alert(now, t["id"], "warn", f"來源新增欄位 {res.new_fields}，策略 {policy}")
            if known is None or res.new_fields:
                fields = sorted((known or set()) | set(res.new_fields) | set(self._runner_fields(res)))
                self.db.execute("INSERT OR REPLACE INTO known_fields VALUES (?,?,COALESCE((SELECT version FROM known_fields WHERE task_id=?),0)+1)", (t["id"], json.dumps(fields), t["id"]))
            if res.watermark_to is not None:
                self.db.execute("INSERT OR REPLACE INTO watermarks VALUES (?,?,?)", (t["id"], res.watermark_to, iso(now)))
            self.db.execute("UPDATE runs SET status=?, finished_at=?, rows_in=?, rows_ok=?, rows_quarantined=?, rows_rejected=?, rows_loaded=?, watermark_to=?, log=? WHERE id=?",
                            (status, iso(now), res.rows_in, res.rows_ok, res.rows_quarantined, res.rows_rejected, res.rows_loaded, res.watermark_to, "\n".join(logs + res.log), run_id))
            if res.rows_in and res.rows_quarantined / max(1, res.rows_in) > 0.01:
                self._alert(now, t["id"], "warn", f"隔離率 {res.rows_quarantined / res.rows_in:.1%} > 1%")
            if res.rows_loaded != res.rows_ok:
                self._alert(now, t["id"], "error", f"對帳差異：通過 {res.rows_ok} 筆、載入 {res.rows_loaded} 筆")
        except SchemaChanged as e:
            self.db.execute("UPDATE runs SET status='paused', finished_at=?, error=?, log=? WHERE id=?", (iso(now), str(e), "\n".join(logs), run_id))
            self.set_enabled(t["id"], False, f"schema change: {e.new_fields}")
            self._alert(now, t["id"], "error", f"來源 Schema 變更 {e.new_fields}，任務已暫停待確認")
            status = "paused"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"; logs.append(traceback.format_exc(limit=3))
            if attempt <= t["retries"]:
                delay = t["backoff_s"] * (2 ** (attempt - 1)); status = "retry_wait"
                self.db.execute("UPDATE runs SET status='retry_wait', finished_at=?, next_retry_at=?, error=?, log=? WHERE id=?",
                                (iso(now), iso(now + timedelta(seconds=delay)), err, "\n".join(logs), run_id))
            else:
                status = "failed"
                self.db.execute("UPDATE runs SET status='failed', finished_at=?, error=?, log=? WHERE id=?", (iso(now), err, "\n".join(logs), run_id))
                self._alert(now, t["id"], "error", f"重試 {t['retries']} 次後仍失敗：{err}")
        self.db.commit()
        return {"run_id": run_id, "task_id": t["id"], "scheduled_for": iso(slot), "attempt": attempt, "status": status}

    @staticmethod
    def _runner_fields(res: RunResult) -> list[str]:
        return [l.split("fields=", 1)[1].strip("[]").replace("'", "").replace(" ", "").split(",") for l in res.log if l.startswith("fields=")][-1] if any(l.startswith("fields=") for l in res.log) else []

    def _alert(self, now: datetime, task_id: str | None, level: str, message: str) -> None:
        self.db.execute("INSERT INTO alerts(at, task_id, level, message) VALUES (?,?,?,?)", (iso(now), task_id, level, message))


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
        print("scheduler ok; runs:", len(s.db.execute("SELECT * FROM runs").fetchall()), "alerts:", len(s.alerts()))
