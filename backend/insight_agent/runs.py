"""AI 工作台 run API 與控制器（第一階段；docs/superpowers/specs/2026-10-31-ai-workbench-phase1.md，契約 #117）。

- API（/v1/agents/runs*）：建立時同步檢查權限、欄位、技能契約、同時名額、冪等鍵，全部照錯誤碼目錄回；run 只有發起人看得到（C6）。
- 控制器：core-worker 的背景例行呼叫 tick()（沿用 #73 主控租約）；每個 run 以 journal 的 claim 取得世代，過期 worker 的寫入一律被擋。
- 模型未設定時走規則模式（1029-1）：只給候選配置，使用者確認（answer）後才查，不產生診斷文字。
  S1「上週開戶漏斗 vs 前週」：上週＝上一個完整週一到週日（1029-7）；「開戶」口徑未核定（Q1）→ 先補問 kh_apply／kh_review_pass。
- 讀取查詢帶 query_id = run_id:step_id:attempt（C7）；等待回答不限時、不計入 max_wall_time（1029-5）。

用法：python3 -m backend.insight_agent.runs      # 自測（假 ClickHouse、合成資料）
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Header

from ..api.errors import ApiError
from ..mcp.server import GW, Gateway, ToolContext, ToolError, args_digest
from ..security.policy import Subject, authorize
from . import skill_loader
from .journal import TERMINAL, Conflict, Fenced, LimitReached, MemoryJournal
from .model import missing_config

FIELDS = {"entry", "question", "skill", "agent", "params", "period", "context"}
ENTRY_SKILL = {"explore": "explore-data", "analyze": "explore-data", "dashboard": "explore-data"}   # 受眾入口在後續 PR（B05–B12）
AMBIGUOUS = {"開戶": (("kh_apply", "開戶申請"), ("kh_review_pass", "審核通過"))}   # 選定後改寫成不歧義的詞再交給規則解析
EXPLICIT = ("開戶申請", "完成開戶", "審核通過", "開戶成功")
IDEM_TTL, RETRY, UNSET = 86400, {"INTERNAL": 2, "TIMEOUT": 1}, "（未設定）"
HIDDEN = {"generation", "lease_owner", "lease_until", "request_digest", "idem_key", "created_ts", "exec_s", "candidates_raw"}


digest = args_digest                                                           # 與 Gateway 同一個摘要函式：result_digest 與證據的 recomputed_digest 才能直接比對


def last_weeks(today: date) -> tuple[dict[str, str], dict[str, str]]:
    mon = today - timedelta(days=today.weekday() + 7)
    return ({"from": str(mon), "to": str(mon + timedelta(days=6)), "grain": "total"},
            {"from": str(mon - timedelta(days=7)), "to": str(mon - timedelta(days=1)), "grain": "total"})


def tidy(data: Any) -> Any:
    """Q21：分組值空白顯示「（未設定）」；0 人時轉換率已由編譯器回 null（畫面顯示「—」）。"""
    if isinstance(data, dict):
        rows = data.get("rows")
        if isinstance(rows, list):
            data = {**data, "rows": [{k: (UNSET if k[:1] == "g" and k[1:].isdigit() and v in (None, "") else v) for k, v in r.items()} if isinstance(r, dict) else r for r in rows]}
    return data


class _Cancel(Exception):
    pass


def used_of(r: dict[str, Any]) -> float:
    """run 已用掃描量＝各步驟各 attempt 的預扣加總；以 attempt 為鍵，重寫同一個 attempt 不會重複計。"""
    return float(sum(sum(float(v) for v in (x.get("scan_by_attempt") or {}).values()) for x in r.get("steps") or []))


class _Ctx(ToolContext):
    """Gateway 在送查詢前預扣 scan_bytes_used；這裡一預扣就寫進步驟紀錄，worker 中途崩潰也不會漏算。"""
    def __setattr__(self, k: str, v: Any) -> None:
        hook = self.__dict__.get("_hook")
        if k == "scan_bytes_used" and hook and "scan_bytes_used" in self.__dict__:
            hook(float(v) - float(self.__dict__["scan_bytes_used"]))
        object.__setattr__(self, k, v)


class RunService:
    def __init__(self, store: Any, ch: Any, journal: Any = None, gateway: Any = None, model_missing: Any = None, skills: Any = None, today: Any = date.today, clock: Any = time.time, roles_of: Any = None):
        if journal is None:
            from .journal import PostgresJournal
            journal = PostgresJournal(store) if hasattr(store, "pool") else MemoryJournal(store)
        self.store, self.j, self.today, self.clock = store, journal, today, clock
        if clock is not time.time:
            self.j.clock = clock                           # 租約判斷與控制器同一個時鐘（測試用合成時鐘時兩邊一致）
        self.gw = gateway or Gateway(store, ch, today=today)
        self.gw.evidence_of = self._evidence_of            # evidence.get 從 run 紀錄找證據
        self.model_missing = model_missing or missing_config
        self.skills = skills or skill_loader.load
        self.project = os.environ.get("DMP_PROJECT", "")
        self.roles_of = roles_of or (lambda uid: None)     # 權威來源：帳號與角色指派「現在」的狀態（停用／撤權 → None 或少了角色）；不用 run 上的快照
        self.limit = int(GW["limits"]["max_concurrent_runs_per_user"])
        self.lease_s = float(GW["limits"]["lease_s"])
        self.beat_s: float = self.lease_s / 3             # 長步驟執行中每 1/3 租約續租一次
        self.on_beat: Any = None                          # 測試用：每次續租後呼叫

    # ---------- API ----------
    def _own(self, s: Subject, rid: str) -> dict[str, Any]:
        r = self.j.get_run(rid) if rid.startswith("run_") else {}
        if not r or (r.get("initiator") or {}).get("user_id") != s.user_id:      # C6：他人一律 404
            raise ApiError("NOT_FOUND")
        return r

    def view(self, r: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in r.items() if k not in HIDDEN}
        out["initiator"] = {k: v for k, v in (r.get("initiator") or {}).items() if k != "roles"}
        return out

    def create(self, s: Subject, body: Any, key: str | None) -> dict[str, Any]:
        if not authorize(s, "use", "ai_ask").allowed:
            raise ApiError("AUTHZ_DENIED", params={"permission": "ai_ask.use"})
        if not isinstance(body, dict):
            raise ApiError("INVALID_ARGUMENT", "body 必須是物件")
        extra = sorted(set(body) - FIELDS)
        if extra:                                                              # C3：project／user_id／roles 不能由請求帶
            raise ApiError("INVALID_ARGUMENT", f"不接受的欄位：{extra}", params={"rejected": extra})
        q, entry, params = body.get("question"), body.get("entry"), body.get("params") or {}
        if not isinstance(q, str) or not q.strip() or len(q) > 2000 or entry not in ("explore", "analyze", "dashboard", "audience") or not isinstance(params, dict):
            raise ApiError("INVALID_ARGUMENT", "entry、question（1–2000 字）必填；params 必須是物件")
        if entry not in ENTRY_SKILL:
            raise ApiError("NOT_CONFIGURED", "受眾入口在後續版本開放")
        if params or body.get("agent"):                                        # C2：Agent 設定尚無已發布版，沒有任何可覆寫參數
            raise ApiError("INVALID_ARGUMENT", "參數覆寫只接受已發布 Agent 設定開放的參數", params={"rejected": sorted(params) or ["agent"]})
        name = str(body.get("skill") or ENTRY_SKILL[entry])
        try:
            sk = self.skills(name)
        except skill_loader.SkillError as e:
            if e.code == "CONTRACT_MISMATCH":
                raise ApiError("CONTRACT_MISMATCH", e.message, params={"required": json.dumps(e.detail.get("required"), ensure_ascii=False)}) from None
            raise ApiError("INVALID_ARGUMENT", f"技能 {name} 不存在或未發布", params={"skill": name}) from None
        missing = list(self.model_missing())
        if missing and str(sk.meta.get("rule_mode", "allowed")) == "denied":
            raise ApiError("MODEL_ENDPOINT_NOT_CONFIGURED", params={"missing": missing})
        now = self.clock(); self.j.expire_idem(now - IDEM_TTL)
        req = digest({k: body.get(k) for k in sorted(FIELDS)})
        mine = self.j.list_runs(s.user_id, None, 200)
        old = next((r for r in mine if key and r.get("idem_key") == key and float(r.get("created_ts") or 0) >= now - IDEM_TTL), None)
        if old:                                                               # C5：同鍵同內容回原 run，不同內容 409
            if old.get("request_digest") != req:
                raise ApiError("CONFLICT", "同一個冪等鍵已用於不同內容", params={"idempotency_key": key})
            return self.view(old)
        doc = {"entry": entry, "question": q, "initiator": {"user_id": s.user_id, "auth_source": "session"},
               "skill": {"name": sk.name, "version": sk.version, "content_digest": digest({"meta": sk.meta, "body": sk.body})}, "agent": None, "params": {},
               "project": self.project, "bookmarks": [], "contracts": skill_loader.contract_versions(),
               "mode": "rules" if missing else "model", "model": {"provider": "rules" if missing else "pending", "id": ""},
               "budget": {"scan_bytes_used": 0, "tokens_used": 0}, "answers": {}, "evidence": {}, "artifacts": [], "period": body.get("period"), "context": body.get("context"),
               "request_digest": req, "idem_key": key, "created_ts": now, "created_at": datetime.fromtimestamp(now, UTC).isoformat()}
        r = self._admit(lambda: self.j.create_run(doc, key, self.limit))
        if r.get("request_digest") != req:                                   # 併發同鍵：資料庫唯一鍵擋下，回到既有那筆
            raise ApiError("CONFLICT", "同一個冪等鍵已用於不同內容", params={"idempotency_key": key or ""})
        self.store.audit("agent.run.created", {"user": s.user_id, "run_id": r["run_id"], "skill": sk.name, "mode": doc["mode"]})
        return self.view(r)

    def _admit(self, fn: Any) -> Any:
        """建立、重試、回答後回到佇列共用同一個名額入口；檢查與寫入在 journal 的同一把鎖／交易內（P2）。"""
        try:
            return fn()
        except LimitReached:
            raise ApiError("RATE_LIMITED", params={"limit": self.limit}) from None

    def _may(self, s: Subject) -> None:
        if not authorize(s, "use", "ai_ask").allowed:                           # 回答、重試也照「現在」的權限（A13）
            raise ApiError("AUTHZ_DENIED", params={"permission": "ai_ask.use"})

    def list(self, s: Subject, state: str | None, limit: int) -> list[dict[str, Any]]:
        return [self.view(r) for r in self.j.list_runs(s.user_id, [state] if state else None, max(1, min(limit, 200)))]

    def cancel(self, s: Subject, rid: str) -> dict[str, Any]:
        if self._own(s, rid)["state"] in TERMINAL:
            raise ApiError("CONFLICT", "run 已結束")
        self.j.request_cancel(rid)
        return self.view(self.j.get_run(rid))

    def answer(self, s: Subject, rid: str, body: Any) -> dict[str, Any]:
        r = self._own(s, rid); pq = r.get("pending_question") or {}; self._may(s)
        if r["state"] != "waiting_input":
            raise ApiError("CONFLICT", "run 不在等待回答")
        b = body if isinstance(body, dict) else {}
        if b.get("question_id") != pq.get("question_id") or b.get("choice") not in [c["id"] for c in pq.get("choices", [])]:
            raise ApiError("INVALID_ARGUMENT", "問題編號或選項不正確")
        extra = {"plan": dict(zip(("current", "previous"), last_weeks(self.today())))} if pq["question_id"] == "confirm" else {}   # 確認當下就固定絕對期間，接手一律沿用
        if not self._admit(lambda: self.j.resume(rid, self.limit, answers={**(r.get("answers") or {}), pq["question_id"]: b["choice"]}, pending_question=None, **extra)):
            raise ApiError("CONFLICT", "run 不在等待回答")
        return self.view(self.j.get_run(rid))

    def retry(self, s: Subject, rid: str) -> dict[str, Any]:
        r = self._own(s, rid); self._may(s)
        if r["state"] not in ("failed", "cancelled"):
            raise ApiError("CONFLICT", "只有失敗或取消的 run 可以重試")
        keep = ("entry", "question", "skill", "agent", "params", "project", "bookmarks", "contracts", "mode", "model", "answers", "period", "context", "candidates")
        now = self.clock()
        doc = {**{k: r.get(k) for k in keep}, "budget": {"scan_bytes_used": 0, "tokens_used": 0}, "evidence": {}, "artifacts": [], "retry_of": rid, "op_root": r.get("op_root") or rid, "initiator": {"user_id": s.user_id, "auth_source": "session"},   # op_root：寫入步驟沿用原 operation_id
               "request_digest": r.get("request_digest"), "created_ts": now, "created_at": datetime.fromtimestamp(now, UTC).isoformat()}
        return self.view(self._admit(lambda: self.j.create_run(doc, None, self.limit)))

    def _evidence_of(self, rid: str, eid: str) -> tuple[str, dict[str, Any]] | None:
        try:
            r = self.j.get_run(rid) if rid.startswith("run_") else {}
        except KeyError:
            return None
        ev = (r.get("evidence") or {}).get(eid)
        return (str((r.get("initiator") or {}).get("user_id") or ""), ev) if ev else None

    def evidence(self, s: Subject, rid: str, eid: str) -> dict[str, Any]:
        """REST 入口與 Gateway evidence.get 同一份實作：依讀取者現在的權限重算，不給快取結果。"""
        self._own(s, rid)
        env = self.gw.call(ToolContext(s, rid, "evidence"), "evidence.get", {"evidence_id": eid})
        if not env["ok"]:
            code = env["error"]["code"]
            raise ApiError(code if code in ("NOT_FOUND", "AUTHZ_DENIED", "TIMEOUT", "BUDGET_EXCEEDED", "DATA_NOT_READY") else "INTERNAL")
        return {**env["data"], "data": tidy(env["data"]["data"])}

    # ---------- 控制器 ----------
    def tick(self, worker: str = "local", limit: int = 5) -> int:
        n = 0
        for r in reversed(self.j.list_runs(None, ["queued", "running", "waiting_lease"], 200)):   # 先建先跑；running 只有租約過期才接得到（claim 判斷）
            if n >= limit:
                break
            if r["state"] == "running" and r.get("cancel_requested"):        # worker 崩潰後才按取消：沒人會再接手，租約過期就直接收成 cancelled（草稿保留）
                n += int(self.j.reap_cancelled(r["run_id"], datetime.fromtimestamp(self.clock(), UTC).isoformat(), now=self.clock()))
                continue
            n += self._exec(r["run_id"], worker)
        return n

    def _exec(self, rid: str, worker: str) -> int:
        gen = self.j.claim(rid, worker, self.lease_s, now=self.clock())
        if gen is None:
            return 0
        r = self.j.get_run(rid); ini = r.get("initiator") or {}; t0 = self.clock()
        ctx = {"used": used_of(r), "t0": t0, "exec_s": float(r.get("exec_s") or 0)}          # 已用掃描量從步驟紀錄重算（含失敗與中斷步驟的預扣）
        try:
            fields, to = self._rules(rid, gen, r, str(ini.get("user_id") or ""), ctx)
        except _Cancel:
            fields, to = {}, "cancelled"
        except Fenced:
            return 0
        fields.update(budget={**(r.get("budget") or {}), "scan_bytes_used": ctx["used"]}, exec_s=ctx["exec_s"] + self.clock() - t0)
        if to in TERMINAL:
            fields["finished_at"] = datetime.fromtimestamp(self.clock(), UTC).isoformat()
        try:
            self.j.set_state(rid, gen, to, **fields)
        except Fenced:
            return 0
        return 1

    def subject_now(self, uid: str) -> Subject:
        roles = self.roles_of(uid)
        return Subject(uid, list(roles or []))

    @contextmanager
    def _beat(self, rid: str, gen: int) -> Any:
        """工具呼叫期間背景續租：單一查詢超過租約也不會被別的 worker 接手；被接手（Fenced）就停。"""
        stop = threading.Event()
        def run() -> None:
            while not stop.wait(self.beat_s):
                try:
                    self.j.renew(rid, gen, self.lease_s, now=self.clock())
                except Fenced:
                    return
                if self.on_beat:
                    self.on_beat()
        t = threading.Thread(target=run, daemon=True) if self.beat_s > 0 else None
        if t:
            t.start()
        try:
            yield
        finally:
            stop.set()
            if t:
                t.join()

    def _call(self, rid: str, gen: int, uid: str, sid: str, tool: str, args: dict[str, Any], ctx: dict[str, Any], op: str = "") -> dict[str, Any]:
        dig = digest(args)[:16]
        old = next((x for x in self.j.get_run(rid).get("steps") or [] if x["step_id"] == sid), None)
        if old and old.get("args_digest") != dig:                             # 參數不同（例如期間變了）就不是同一步：換步驟編號重查，不沿用舊結果
            sid = f"{sid}~{dig[:8]}"
            old = next((x for x in self.j.get_run(rid).get("steps") or [] if x["step_id"] == sid), None)
        if old and old["state"] == "succeeded":                                # 接手後已完成的步驟不重做（結果存在步驟上）
            return {"ok": True, "data": old.get("out") or {}, "result_digest": old.get("result_digest"), "warnings": old.get("warnings", []), "cached": True}
        attempt = int(old["attempts"]) if old and old["state"] in ("running", "retry_wait") else 0
        byatt: dict[str, float] = dict((old or {}).get("scan_by_attempt") or {})
        while True:
            attempt += 1
            self.j.renew(rid, gen, self.lease_s, now=self.clock())             # 每一步開始先續租；已被接手 → Fenced，整個 run 交給新 worker
            if self.j.get_run(rid).get("cancel_requested"):                    # 步驟之間檢查取消
                raise _Cancel()
            wall = float(GW["limits"]["max_wall_time_s"]["interactive"])
            if ctx["exec_s"] + self.clock() - ctx["t0"] > wall:                 # 只算執行時間，不含等待回答
                return {"ok": False, "error": {"code": "TIMEOUT", "message": "超過單次分析的執行時間上限", "retryable": False, "detail": {"limit_s": wall}}}
            qid = f"{rid}:{sid}:{attempt}"
            step = {"step_id": sid, "tool": tool, "args_digest": dig, "attempts": attempt, "query_ids": [qid], "scan_by_attempt": byatt, **({"operation_id": op} if op else {})}
            self.j.put_step(rid, gen, {**step, "state": "running"})
            s = self.subject_now(uid)                                           # 每次工具呼叫前重讀身分（A13：撤權、停用即時生效）
            if not authorize(s, "use", "ai_ask").allowed:
                err = {"code": "AUTHZ_DENIED", "message": "發起人目前沒有 AI 分析權限（已撤權或停用）", "retryable": False, "detail": {"permission": "ai_ask.use"}}
                self.j.put_step(rid, gen, {**step, "state": "failed", "error": err})
                return {"ok": False, "error": err}
            tc = _Ctx(s, rid, sid, scan_bytes_used=ctx["used"], query_id=qid, operation_id=op, commit=self._committer(rid, gen, op) if op else None)
            def reserve(delta: float, step: dict[str, Any] = step, key: str = str(attempt)) -> None:
                byatt[key] = byatt.get(key, 0.0) + delta
                self.j.put_step(rid, gen, {**step, "state": "running", "scan_by_attempt": dict(byatt)})
            object.__setattr__(tc, "_hook", reserve)
            with self._beat(rid, gen):
                env = self.gw.call(tc, tool, args)
            ctx["used"] = tc.scan_bytes_used
            if env["ok"]:
                env["result_digest"] = digest(env["data"])[:16]
                out = ({"cards": [{k: c.get(k) for k in ("title", "kind", "query")} for c in env["data"].get("cards") or []], "unmatched": env["data"].get("unmatched", [])} if tool == "analysis.configure"
                       else {"dashboard_id": env["data"]["dashboard_id"], "draft": True} if tool == "dashboard.draft" else {})
                self.j.put_step(rid, gen, {**step, "state": "succeeded", "warnings": env["warnings"], "usage": env["usage"], "result_digest": env["result_digest"], "out": out})
                return env
            err = env["error"]
            if err.get("retryable") and attempt <= RETRY.get(err["code"], 0):
                self.j.put_step(rid, gen, {**step, "state": "retry_wait", "error": err})
                continue
            self.j.put_step(rid, gen, {**step, "state": "failed", "error": err})
            return env

    def _committer(self, rid: str, gen: int, op: str) -> Any:
        """寫入工具用的提交函式：唯一冪等來源是 agent_operations（C4）；origin 由控制器填，模型與參數碰不到（C6）。"""
        def commit(tool: str, kind: str, args: dict[str, Any], doc: dict[str, Any]) -> tuple[str, str]:
            sk = self.j.get_run(rid).get("skill") or {}
            doc = {**doc, "origin": {"kind": "ai_draft", "run_id": rid, "skill": {"name": sk.get("name"), "version": sk.get("version")}}}
            try:
                return self.j.commit_operation(rid, gen, op, tool, kind, args, doc)
            except Conflict:
                raise ToolError("CONFLICT", "同一個寫入操作已以不同內容提交（不覆寫原產物）", operation_id=op) from None
        return commit

    def _rules(self, rid: str, gen: int, r: dict[str, Any], s: str, ctx: dict[str, Any]) -> tuple[dict[str, Any], str]:
        q, ans = r["question"], r.get("answers") or {}
        if not authorize(self.subject_now(s), "use", "ai_ask").allowed:              # 接手執行前也先確認發起人現在仍有權限
            return {"error": {"code": "AUTHZ_DENIED", "message": "發起人目前沒有 AI 分析權限（已撤權或停用）", "retryable": False, "detail": {"permission": "ai_ask.use"}}}, "failed"
        for term, opts in AMBIGUOUS.items():                                   # A03：口徑未核定就問，不替使用者選
            if term in q and not any(x in q for x in EXPLICIT):
                qid = "term:" + term
                if qid not in ans:
                    choices = [{"id": ev, "label": f"{word}（{ev}）"} for ev, word in opts]
                    return {"pending_question": {"question_id": qid, "text": f"「{term}」要算哪一個事件？", "choices": choices}}, "waiting_input"
                q = q.replace(term, dict(opts)[ans[qid]])
        plan = r.get("plan") or {}
        cur, prev = (plan["current"], plan["previous"]) if plan else last_weeks(self.today())
        if "confirm" not in ans:                                               # A27：規則模式先給候選，確認前不送查詢
            env = self._call(rid, gen, s, "s1_configure", "analysis.configure", {"question": q, "context": {"time": cur}}, ctx)
            if not env["ok"]:
                return {"error": env["error"]}, "failed"
            cards = [{"id": f"c{i}", "title": c["title"], "kind": c["kind"], "query": c["query"]} for i, c in enumerate(env["data"].get("cards") or [])]
            if not cards:
                return {"result": {"answerable": False, "label": "規則模式（非 AI）", "unmatched": env["data"].get("unmatched", [])}}, "succeeded"
            return {"candidates": cards, "pending_question": {"question_id": "confirm", "text": "規則模式（非 AI）：請確認要執行的分析",
                                                              "choices": [{"id": c["id"], "label": c["title"]} for c in cards]}}, "waiting_input"
        card = next(c for c in r.get("candidates") or [] if c["id"] == ans["confirm"])
        evid: dict[str, Any] = {}
        for sid, period, label in (("s2_current", cur, "上週"), ("s3_previous", prev, "前週")):
            query = {**card["query"], "time": dict(period)}
            env = self._call(rid, gen, s, sid, "analysis.run", {"config": {"model": card["kind"], "params": query}}, ctx)
            if not env["ok"]:
                return {"error": env["error"], "evidence": evid}, "failed"
            eid = "ev_" + digest([rid, sid])[:12]
            evid[eid] = {"evidence_id": eid, "run_id": rid, "label": label, "kind": card["kind"], "query": query, "period": {"from": period["from"], "to": period["to"], "tz": "Asia/Taipei"},
                         "contract": {**r.get("contracts", {}), "skill": f"{r['skill']['name']}@{r['skill']['version']}"}, "result_digest": env["result_digest"],
                         "created_at": datetime.fromtimestamp(self.clock(), UTC).isoformat()}
        arts: list[dict[str, Any]] = [{"kind": "evidence", "id": e} for e in evid]
        result: dict[str, Any] = {"answerable": True, "label": "規則模式（非 AI）", "evidence_ids": list(evid), "card": card["title"]}
        if r["entry"] == "dashboard":                                          # 開戶主管週報：私人看板草稿，卡片存 kind + query，期間固定（pinned）不被看板預設期間覆蓋
            pin = lambda p: {**card["query"], "time": {**p, "pinned": True}}
            cards = [{"title": f"{card['title']}・上週（{cur['from']}～{cur['to']}）", "kind": card["kind"], "query": pin(cur)},
                     {"title": f"{card['title']}・前週（{prev['from']}～{prev['to']}）", "kind": card["kind"], "query": pin(prev)}]
            if card["kind"] == "funnel" and not card["query"].get("group_by"):
                cards.append({"title": f"{card['title']}・上週依渠道", "kind": "funnel", "query": {**pin(cur), "group_by": [{"field": "channel"}]}})
            op = "op_" + digest([r.get("op_root") or rid, "s4_dashboard"])[:16]
            env = self._call(rid, gen, s, "s4_dashboard", "dashboard.draft", {"name": f"週報：{card['title']}（{cur['from']}～{cur['to']}）", "cards": cards}, ctx, op=op)
            if not env["ok"]:
                return {"error": env["error"], "evidence": evid, "artifacts": arts}, "failed"
            arts.append({"kind": "dashboard", "id": env["data"]["dashboard_id"], "operation_id": op}); result["dashboard_id"] = env["data"]["dashboard_id"]
        return {"evidence": evid, "artifacts": arts, "result": result}, "succeeded"


def build_router(svc: RunService, get_subject: Any) -> APIRouter:
    r = APIRouter()

    @r.post("/v1/agents/runs", status_code=202)
    def create(body: Any = Body(...), key: str | None = Header(None, alias="Idempotency-Key", max_length=128), s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return svc.create(s, body, key)

    @r.get("/v1/agents/runs")
    def list_runs(state: str | None = None, limit: int = 50, s: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        return svc.list(s, state, limit)

    @r.get("/v1/agents/runs/{run_id}")
    def get(run_id: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return svc.view(svc._own(s, run_id))

    @r.post("/v1/agents/runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return svc.cancel(s, run_id)

    @r.post("/v1/agents/runs/{run_id}/retry", status_code=202)
    def retry(run_id: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return svc.retry(s, run_id)

    @r.post("/v1/agents/runs/{run_id}/answer", status_code=202)
    def answer(run_id: str, body: Any = Body(...), s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return svc.answer(s, run_id, body)

    @r.get("/v1/agents/runs/{run_id}/evidence/{evidence_id}")
    def evidence(run_id: str, evidence_id: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return svc.evidence(s, run_id, evidence_id)
    return r


if __name__ == "__main__":
    from . import draft_selftest, evidence_selftest, runs_selftest
    runs_selftest.selftest(); draft_selftest.main(); evidence_selftest.selftest()
