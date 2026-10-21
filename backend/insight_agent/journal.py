"""AI 工作台 run 紀錄：狀態機、租約世代圍籬、寫入操作冪等（docs/39 §3.1、§5；驗收 docs/40 B03／B04）。

為什麼要有這層：run 會跨 worker、跨重試，寫入類工具（dashboard.draft、segment.draft）不能因為崩潰或重試多建一份產物。
- `operation_id` 由控制器產生（模型不碰）；`args_digest = sha256(canon_json(args))`。同 op 不同 digest → Conflict，不寫。
- 產物 id 由 operation_id 推出（d_／sg_ + sha256 前 16 碼），崩潰後重算得同一個 id。
- 操作紀錄與產物寫在同一個交易（PostgreSQL）或同一把鎖（記憶體）；先檢查 run 世代，世代不符 → Fenced，整筆不寫。
- worker 以 claim() 取得新世代；過期 worker 的任何寫入（step、狀態、產物）都會被拒。

兩個實作同一組方法：MemoryJournal（掛在 MemoryStore 上，開發／測試）與 PostgresJournal（共用 PostgresStore 的連線池）。
用法：python3 -m backend.insight_agent.journal          # 記憶體自測；設 DMP_PG_DSN 時另跑 PostgreSQL 自測（含雙 worker 併發）
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from typing import Any

RUN_NEXT: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"waiting_input", "waiting_lease", "succeeded", "failed", "cancelled"},
    "waiting_input": {"queued", "cancelled"},          # 使用者回答口徑後回到佇列
    "waiting_lease": {"running", "failed", "cancelled"},
}
TERMINAL = {"succeeded", "failed", "cancelled"}
STEP_NEXT: dict[str, set[str]] = {
    "pending": {"running", "skipped"},
    "running": {"succeeded", "failed", "retry_wait"},
    "retry_wait": {"running", "failed"},
}
CLAIMABLE = {"queued", "running", "waiting_lease"}    # running 只有在租約過期（或同一 worker）時可接手
ARTIFACT = {"dashboard": ("d_", "dashboard"), "segment": ("sg_", "segments")}   # kind → (id 前綴, 儲存位置)


class Fenced(Exception):
    """世代不符：這個 worker 的租約已被接手，寫入整筆不生效。"""


class Conflict(Exception):
    """同 operation_id 不同 args_digest（docs/39 §2.3 CONFLICT，retryable=false）。"""


class BadTransition(ValueError):
    pass


def args_digest(args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def artifact_id(kind: str, operation_id: str) -> str:
    return ARTIFACT[kind][0] + hashlib.sha256(operation_id.encode()).hexdigest()[:16]


def _check(table: dict[str, set[str]], src: str, dst: str) -> None:
    if dst not in table.get(src, set()):
        raise BadTransition(f"{src} → {dst} 不是允許的轉換")


def _artifact_doc(kind: str, run_id: str, op: str, doc: dict[str, Any]) -> dict[str, Any]:
    return {**doc, "id": artifact_id(kind, op), "status": "draft", "operation_id": op, "created_by_run": run_id}


class MemoryJournal:
    """run／step／operation 放在 MemoryStore.d，和產物共用 store.lock，所以「操作紀錄 + 產物」是原子的。"""
    fault: Any = None   # 測試用：在「操作紀錄已寫、產物未寫」之間注入崩潰
    clock: Any = staticmethod(time.time)   # 租約時鐘；測試換成可控時間

    def __init__(self, store: Any):
        self.s = store
        for k in ("agent_runs", "agent_steps", "agent_operations", "agent_idem"):
            self.s.d.setdefault(k, {})

    def create_run(self, doc: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        with self.s.lock:
            idem = f"{doc.get('initiator', {}).get('user_id', '')}:{idempotency_key}" if idempotency_key else None
            if idem and idem in self.s.d["agent_idem"]:
                return self._get(self.s.d["agent_idem"][idem])
            rid = "run_" + uuid.uuid4().hex[:12]
            self.s.d["agent_runs"][rid] = {**doc, "run_id": rid, "state": "queued", "generation": 0, "lease_owner": None, "lease_until": 0.0, "cancel_requested": False}
            self.s.d["agent_steps"][rid] = []
            if idem:
                self.s.d["agent_idem"][idem] = rid
            self.s._save()
            return self._get(rid)

    def _get(self, rid: str) -> dict[str, Any]:
        r = self.s.d["agent_runs"].get(rid)
        return {**copy.deepcopy(r), "steps": copy.deepcopy(self.s.d["agent_steps"][rid])} if r else {}

    def get_run(self, rid: str) -> dict[str, Any]:
        with self.s.lock:
            return self._get(rid)

    def claim(self, rid: str, worker: str, lease_s: float = 60, now: float | None = None) -> int | None:
        now = self.clock() if now is None else now
        with self.s.lock:
            r = self.s.d["agent_runs"][rid]
            if r["state"] not in CLAIMABLE or r["cancel_requested"] or not (r["lease_until"] < now or r["lease_owner"] == worker):
                return None
            r.update(generation=r["generation"] + 1, lease_owner=worker, lease_until=now + lease_s, state="running"); self.s._save()
            return int(r["generation"])

    def _fenced(self, rid: str, gen: int) -> dict[str, Any]:
        """寫入條件：世代相同、run 仍在 running、租約未過期。過期但尚未被接手的 worker 也不能寫（含續租）。"""
        r = self.s.d["agent_runs"][rid]
        if r["generation"] != gen or r["state"] != "running" or r["lease_until"] < self.clock():
            raise Fenced(f"{rid} generation={r['generation']} state={r['state']}，寫入者帶 {gen}（租約過期或已被接手）")
        return r

    def renew(self, rid: str, gen: int, lease_s: float = 60, now: float | None = None) -> None:
        with self.s.lock:
            self._fenced(rid, gen)["lease_until"] = (self.clock() if now is None else now) + lease_s; self.s._save()

    def set_state(self, rid: str, gen: int, to: str, **fields: Any) -> None:
        with self.s.lock:
            r = self._fenced(rid, gen); _check(RUN_NEXT, r["state"], to)
            r.update(fields, state=to, lease_until=0.0 if to != "running" else r["lease_until"]); self.s._save()

    def put_step(self, rid: str, gen: int, step: dict[str, Any]) -> None:
        with self.s.lock:
            self._fenced(rid, gen)
            steps = self.s.d["agent_steps"][rid]
            old = next((x for x in steps if x["step_id"] == step["step_id"]), None)
            if old is None:
                _check({"": {"pending", "running"}}, "", step["state"]); steps.append(copy.deepcopy(step))
            else:
                if old["state"] != step["state"]:
                    _check(STEP_NEXT, old["state"], step["state"])
                old.clear(); old.update(copy.deepcopy(step))
            self.s._save()

    def commit_operation(self, rid: str, gen: int, op: str, tool: str, kind: str, args: dict[str, Any], doc: dict[str, Any]) -> tuple[str, str]:
        """回 (committed|existing, artifact_id)；Conflict／Fenced 時什麼都不寫。"""
        dig = args_digest(args)
        with self.s.lock:
            self._fenced(rid, gen)
            old = self.s.d["agent_operations"].get(op)
            if old:
                if old["args_digest"] != dig:
                    raise Conflict(f"{op} 已以不同參數提交")
                return "existing", old["artifact_id"]
            a = _artifact_doc(kind, rid, op, doc)
            ops = dict(self.s.d["agent_operations"])                                # 先寫副本，產物也寫成功才換上（等同交易）
            ops[op] = {"operation_id": op, "run_id": rid, "tool": tool, "args_digest": dig, "artifact_kind": kind, "artifact_id": a["id"], "generation": gen, "state": "committed"}
            if self.fault:
                self.fault("after_operation_insert")
            self.s.d["agent_operations"] = ops
            self.s.d.setdefault(ARTIFACT[kind][1], {}).setdefault(a["id"], a)   # 不覆蓋使用者事後的編輯
            self.s._save()
            return "committed", a["id"]

    def resume(self, rid: str, **fields: Any) -> bool:
        """使用者回答口徑：waiting_input → queued（不需世代，由 API 呼叫）。"""
        with self.s.lock:
            r = self.s.d["agent_runs"][rid]
            if r["state"] != "waiting_input":
                return False
            r.update(fields, state="queued"); self.s._save()
            return True

    def request_cancel(self, rid: str) -> None:
        with self.s.lock:
            r = self.s.d["agent_runs"][rid]
            if r["state"] in TERMINAL:
                return
            r["cancel_requested"] = True
            if r["state"] in {"queued", "waiting_input", "waiting_lease"}:
                r["state"] = "cancelled"
            self.s._save()

    def operations(self, rid: str | None = None) -> list[dict[str, Any]]:
        with self.s.lock:
            return [dict(o) for o in self.s.d["agent_operations"].values() if rid is None or o["run_id"] == rid]


DDL = """
CREATE TABLE IF NOT EXISTS {s}.agent_runs (run_id TEXT PRIMARY KEY, state TEXT NOT NULL, generation BIGINT NOT NULL DEFAULT 0, lease_owner TEXT,
  lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, cancel_requested BOOLEAN NOT NULL DEFAULT false, idem TEXT UNIQUE, doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS {s}.agent_steps (seq BIGSERIAL, run_id TEXT NOT NULL REFERENCES {s}.agent_runs(run_id), step_id TEXT NOT NULL, state TEXT NOT NULL, doc JSONB NOT NULL, PRIMARY KEY (run_id, step_id));
CREATE TABLE IF NOT EXISTS {s}.agent_operations (operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool TEXT NOT NULL, args_digest TEXT NOT NULL,
  artifact_kind TEXT NOT NULL, artifact_id TEXT NOT NULL, generation BIGINT NOT NULL, state TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


class PostgresJournal:
    """表放在 PostgresStore 同一個 schema；看板（items）與分群（segments）同庫，所以產物與操作紀錄同一交易。"""
    fault: Any = None
    clock: Any = staticmethod(time.time)

    def __init__(self, store: Any):
        self.s = store
        async def init() -> None:
            async with store.pool.acquire() as c:
                await c.execute(DDL.format(s=store.schema))
        store._loop.run(init())

    def _run(self, fn: Any) -> Any:
        async def go() -> Any:
            async with self.s.pool.acquire() as c, c.transaction():
                return await fn(c)
        return self.s._loop.run(go())

    async def _fence(self, c: Any, rid: str, gen: int) -> Any:
        """同 MemoryJournal._fenced；UPDATE 取列鎖，同一 run 的寫入在此排隊。"""
        row = await c.fetchrow(f"UPDATE {self.s.t('agent_runs')} SET updated_at = now() WHERE run_id = $1 AND generation = $2 AND state = 'running' AND lease_until >= $3 "
                               "RETURNING state, lease_until", rid, gen, self.clock())
        if row is None:
            raise Fenced(f"{rid} 世代已不是 {gen}、不在 running 或租約已過期")       # 例外 → 交易回滾
        return row

    def create_run(self, doc: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        idem = f"{doc.get('initiator', {}).get('user_id', '')}:{idempotency_key}" if idempotency_key else None
        rid = "run_" + uuid.uuid4().hex[:12]
        async def fn(c: Any) -> str:
            got = await c.fetchval(f"INSERT INTO {self.s.t('agent_runs')} (run_id, state, idem, doc) VALUES ($1, 'queued', $2, $3) ON CONFLICT (idem) DO NOTHING RETURNING run_id", rid, idem, json.dumps(doc, ensure_ascii=False))
            return got or await c.fetchval(f"SELECT run_id FROM {self.s.t('agent_runs')} WHERE idem = $1", idem)
        return self.get_run(self._run(fn))

    def get_run(self, rid: str) -> dict[str, Any]:
        async def fn(c: Any) -> dict[str, Any]:
            r = await c.fetchrow(f"SELECT * FROM {self.s.t('agent_runs')} WHERE run_id = $1", rid)
            if r is None:
                return {}
            steps = await c.fetch(f"SELECT doc FROM {self.s.t('agent_steps')} WHERE run_id = $1 ORDER BY seq", rid)
            return {**json.loads(r["doc"]), "run_id": rid, "state": r["state"], "generation": r["generation"], "lease_owner": r["lease_owner"], "lease_until": r["lease_until"],
                    "cancel_requested": r["cancel_requested"], "steps": [json.loads(x["doc"]) for x in steps]}
        return self._run(fn)

    def claim(self, rid: str, worker: str, lease_s: float = 60, now: float | None = None) -> int | None:
        now = self.clock() if now is None else now
        return self._run(lambda c: c.fetchval(
            f"UPDATE {self.s.t('agent_runs')} SET generation = generation + 1, lease_owner = $2, lease_until = $3 + $4, state = 'running', updated_at = now() "
            "WHERE run_id = $1 AND state = ANY($5::text[]) AND NOT cancel_requested AND (lease_until < $3 OR lease_owner = $2) RETURNING generation",
            rid, worker, now, lease_s, sorted(CLAIMABLE)))

    def renew(self, rid: str, gen: int, lease_s: float = 60, now: float | None = None) -> None:
        until = (self.clock() if now is None else now) + lease_s
        async def fn(c: Any) -> None:
            await self._fence(c, rid, gen)
            await c.execute(f"UPDATE {self.s.t('agent_runs')} SET lease_until = $2 WHERE run_id = $1", rid, until)
        self._run(fn)

    def set_state(self, rid: str, gen: int, to: str, **fields: Any) -> None:
        async def fn(c: Any) -> None:
            row = await self._fence(c, rid, gen); _check(RUN_NEXT, row["state"], to)
            await c.execute(f"UPDATE {self.s.t('agent_runs')} SET state = $2, lease_until = $3, doc = doc || $4::jsonb WHERE run_id = $1",
                            rid, to, row["lease_until"] if to == "running" else 0.0, json.dumps(fields, ensure_ascii=False, default=str))
        self._run(fn)

    def put_step(self, rid: str, gen: int, step: dict[str, Any]) -> None:
        async def fn(c: Any) -> None:
            await self._fence(c, rid, gen)
            old = await c.fetchval(f"SELECT state FROM {self.s.t('agent_steps')} WHERE run_id = $1 AND step_id = $2 FOR UPDATE", rid, step["step_id"])
            if old is None:
                _check({"": {"pending", "running"}}, "", step["state"])
            elif old != step["state"]:
                _check(STEP_NEXT, old, step["state"])
            await c.execute(f"INSERT INTO {self.s.t('agent_steps')} (run_id, step_id, state, doc) VALUES ($1, $2, $3, $4) "
                            "ON CONFLICT (run_id, step_id) DO UPDATE SET state = EXCLUDED.state, doc = EXCLUDED.doc", rid, step["step_id"], step["state"], json.dumps(step, ensure_ascii=False, default=str))
        self._run(fn)

    def commit_operation(self, rid: str, gen: int, op: str, tool: str, kind: str, args: dict[str, Any], doc: dict[str, Any]) -> tuple[str, str]:
        dig = args_digest(args); a = _artifact_doc(kind, rid, op, doc)
        async def fn(c: Any) -> tuple[str, str]:
            await self._fence(c, rid, gen)
            ins = await c.fetchval(f"INSERT INTO {self.s.t('agent_operations')} (operation_id, run_id, tool, args_digest, artifact_kind, artifact_id, generation, state) "
                                   "VALUES ($1, $2, $3, $4, $5, $6, $7, 'committed') ON CONFLICT (operation_id) DO NOTHING RETURNING artifact_id", op, rid, tool, dig, kind, a["id"], gen)
            if ins is None:                                                        # 同 op 已提交（可能是另一個 run 的重試或併發 worker）
                old = await c.fetchrow(f"SELECT args_digest, artifact_id FROM {self.s.t('agent_operations')} WHERE operation_id = $1", op)
                if old["args_digest"] != dig:
                    raise Conflict(f"{op} 已以不同參數提交")
                return "existing", old["artifact_id"]
            if self.fault:
                self.fault("after_operation_insert")
            body = json.dumps(a, ensure_ascii=False, default=str)
            if kind == "segment":
                await c.execute(f"INSERT INTO {self.s.t('segments')} (id, doc) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING", a["id"], body)
            else:
                await c.execute(f"INSERT INTO {self.s.t('items')} (kind, id, doc) VALUES ($1, $2, $3) ON CONFLICT (kind, id) DO NOTHING", ARTIFACT[kind][1], a["id"], body)
            return "committed", a["id"]
        return self._run(fn)

    def resume(self, rid: str, **fields: Any) -> bool:
        return bool(self._run(lambda c: c.fetchval(
            f"UPDATE {self.s.t('agent_runs')} SET state = 'queued', doc = doc || $2::jsonb, updated_at = now() WHERE run_id = $1 AND state = 'waiting_input' RETURNING true",
            rid, json.dumps(fields, ensure_ascii=False, default=str))))

    def request_cancel(self, rid: str) -> None:
        self._run(lambda c: c.execute(
            f"UPDATE {self.s.t('agent_runs')} SET cancel_requested = true, state = CASE WHEN state IN ('queued', 'waiting_input', 'waiting_lease') THEN 'cancelled' ELSE state END "
            "WHERE run_id = $1 AND state <> ALL($2::text[])", rid, sorted(TERMINAL)))

    def operations(self, rid: str | None = None) -> list[dict[str, Any]]:
        async def fn(c: Any) -> list[dict[str, Any]]:
            rows = await c.fetch(f"SELECT * FROM {self.s.t('agent_operations')} WHERE $1::text IS NULL OR run_id = $1", rid)
            return [{k: v for k, v in dict(r).items() if k != "created_at"} for r in rows]
        return self._run(fn)


def selftest(j: Any, store: Any, label: str) -> None:
    """docs/40 B03／B04 的確定性版本：崩潰點、接手、過期 worker、改參數、雙 run 併發同一 operation_id。"""
    from concurrent.futures import ThreadPoolExecutor
    dash = lambda: [d for d in store.items("dashboard") if d.get("operation_id", "").startswith(tag)]
    tag = "op_" + uuid.uuid4().hex[:6]; t0 = 1_000_000.0; T = [t0]; j.clock = lambda: T[0]
    who = {"initiator": {"user_id": "u_synth", "auth_source": "test"}, "entry": "dashboard", "question": "合成案例：週報"}
    r = j.create_run(who, idempotency_key=tag); assert j.create_run(who, idempotency_key=tag)["run_id"] == r["run_id"]      # 同使用者同鍵 = 同一 run
    assert j.create_run({**who, "initiator": {"user_id": "u_other"}}, idempotency_key=tag)["run_id"] != r["run_id"]
    rid = r["run_id"]; args = {"name": "合成週報", "cards": [{"kind": "metric", "query": {"metric": "m_dau"}}]}
    g1 = j.claim(rid, "w1", 60); T[0] = t0 + 1; assert g1 == 1 and j.claim(rid, "w2", 60) is None                             # 租約有效：別人拿不到
    j.put_step(rid, g1, {"step_id": "s1", "tool": "dashboard.draft", "state": "running", "operation_id": f"{tag}_1"})
    # (c) 操作紀錄已寫、產物未寫時崩潰 → 整筆回滾，0 筆
    def boom(_: str) -> None:
        raise RuntimeError("注入崩潰")
    j.fault = boom
    try:
        j.commit_operation(rid, g1, f"{tag}_1", "dashboard.draft", "dashboard", args, args); raise AssertionError("應崩潰")
    except RuntimeError:
        pass
    j.fault = None; assert not j.operations(rid) and not dash()
    # (a) 成功後同一 worker 重試 → existing、同一 id
    st, aid = j.commit_operation(rid, g1, f"{tag}_1", "dashboard.draft", "dashboard", args, args); assert st == "committed" and aid == artifact_id("dashboard", f"{tag}_1")
    assert j.commit_operation(rid, g1, f"{tag}_1", "dashboard.draft", "dashboard", args, args) == ("existing", aid) and len(dash()) == 1
    # 租約已過期、還沒人接手：世代雖然還是 g1，step、產物、續租都不能寫（Codex #62 P1）
    T[0] = t0 + 61; expired = 0
    for fn in (lambda: j.put_step(rid, g1, {"step_id": "s1", "state": "succeeded"}), lambda: j.renew(rid, g1, 60),
               lambda: j.commit_operation(rid, g1, f"{tag}_x", "dashboard.draft", "dashboard", args, args)):
        try:
            fn()
        except Fenced:
            expired += 1
    assert expired == 3 and len(j.operations(rid)) == 1 and j.get_run(rid)["lease_until"] == t0 + 60
    # (b) 提交後、step 記成功前崩潰；租約過期由 w2 接手 → 查回同一產物
    g2 = j.claim(rid, "w2", 60); assert g2 == g1 + 1 and j.get_run(rid)["steps"][0]["state"] == "running"
    assert j.commit_operation(rid, g2, f"{tag}_1", "dashboard.draft", "dashboard", args, args) == ("existing", aid) and len(dash()) == 1
    # (d) 舊 worker 醒來：step、狀態、產物、續租都被圍籬擋下
    fenced = 0
    for fn in (lambda: j.put_step(rid, g1, {"step_id": "s1", "state": "succeeded"}), lambda: j.set_state(rid, g1, "succeeded"),
               lambda: j.commit_operation(rid, g1, f"{tag}_2", "dashboard.draft", "dashboard", args, args), lambda: j.renew(rid, g1, 60)):
        try:
            fn()
        except Fenced:
            fenced += 1
    assert fenced == 4 and len(j.operations(rid)) == 1 and len(dash()) == 1 and j.get_run(rid)["steps"][0]["state"] == "running"
    # B04(c) 同 op 不同參數 → Conflict，原產物不變
    try:
        j.commit_operation(rid, g2, f"{tag}_1", "dashboard.draft", "dashboard", {**args, "name": "模型改了標題"}, args); raise AssertionError("應 Conflict")
    except Conflict:
        pass
    assert [d["name"] for d in dash()] == ["合成週報"]
    j.put_step(rid, g2, {"step_id": "s1", "tool": "dashboard.draft", "state": "succeeded", "operation_id": f"{tag}_1"})
    # 狀態機：waiting_input 不可接手、使用者回答後回佇列；終態不可再轉；step 不可倒退
    j.set_state(rid, g2, "waiting_input", pending_question={"question_id": "q1", "text": "合成問題"}); T[0] = t0 + 999; assert j.claim(rid, "w3", 60) is None
    assert j.resume(rid, answer={"q1": "a"}) and not j.resume(rid); T[0] = t0 + 1000; g3 = j.claim(rid, "w3", 60); assert g3 == g2 + 1
    for bad in (lambda: j.put_step(rid, g3, {"step_id": "s1", "state": "running"}), lambda: j.set_state(rid, g3, "queued")):
        try:
            bad(); raise AssertionError("應拒絕")
        except BadTransition:
            pass
    j.set_state(rid, g3, "succeeded"); assert j.get_run(rid)["state"] == "succeeded" and j.claim(rid, "w4", 60) is None
    # B04(b) 原 run 與 retry_of run 併發提交同一 operation_id → 一個 committed、一個 existing、同一產物
    T[0] = t0 + 6000; runs = [j.create_run({**who, "retry_of": rid}) for _ in range(2)]; gens = [j.claim(x["run_id"], f"wc{i}", 60) for i, x in enumerate(runs)]
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda i: j.commit_operation(runs[i]["run_id"], gens[i], f"{tag}_9", "dashboard.draft", "dashboard", args, args), range(2)))
    assert sorted(x[0] for x in res) == ["committed", "existing"] and res[0][1] == res[1][1] and len(dash()) == 2
    # 分群草稿也走同一條路；取消：佇列中直接取消、執行中設旗標且不可再接手
    s, sid = j.commit_operation(runs[0]["run_id"], gens[0], f"{tag}_s", "segment.draft", "segment", {"rule": {"all": []}}, {"name": "合成受眾"})
    assert s == "committed" and sid.startswith("sg_") and next(x for x in store.segments() if x["id"] == sid)["status"] == "draft"
    q = j.create_run(who); j.request_cancel(q["run_id"]); assert j.get_run(q["run_id"])["state"] == "cancelled"
    j.request_cancel(runs[1]["run_id"]); assert j.get_run(runs[1]["run_id"])["cancel_requested"] and j.claim(runs[1]["run_id"], "wc1", 60) is None
    j.set_state(runs[1]["run_id"], gens[1], "cancelled")
    print(f"run journal self-test ok ({label}): 崩潰回滾 0 筆、重試/接手 existing 同 id、租約過期未接手被擋 {expired} 次、被接手後舊 worker 被擋 {fenced} 次、改參數 CONFLICT、雙 run 併發 1 committed + 1 existing、狀態機與取消")


if __name__ == "__main__":
    import os

    from backend.api.store import MemoryStore
    ms = MemoryStore(); selftest(MemoryJournal(ms), ms, "memory")
    dsn = os.environ.get("DMP_PG_DSN")
    if not dsn:
        print("DMP_PG_DSN 未設定：略過 PostgreSQL run journal 自測")
    else:
        from backend.api.store_pg import PostgresStore
        ps = PostgresStore(dsn, schema="selftest_j" + uuid.uuid4().hex[:6])
        try:
            selftest(PostgresJournal(ps), ps, "postgres")
        finally:
            ps.drop_schema(); ps.close()
