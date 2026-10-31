"""run API 與控制器自測（合成資料、假 ClickHouse、模型替身未設定 → 規則模式）。對應 docs/40 A03、A17、A19、A25、A25b、A26、A27 與契約 C2、C3、C5、C6。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..api.errors import ApiError, body
from ..api.store import MemoryStore
from ..mcp.server import Gateway
from ..security.policy import Subject
from . import skill_loader
from .runs import RunService, build_router


class FakeCH:
    def __init__(self) -> None:
        self.sql: list[str] = []; self.settings: list[dict[str, Any]] = []; self.fail: str | None = None; self.hook: Any = None; self.est_rows = 1000
    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str:
        if sql.startswith("EXPLAIN ESTIMATE"):
            return f"dmp\tevents\t1\t{self.est_rows}\n"
        if self.hook:
            self.hook(dict(settings or {}))
        if self.fail:
            raise RuntimeError(self.fail)
        self.sql.append(sql); self.settings.append(dict(settings or {}))
        return "\n".join(json.dumps(r) for r in ({"step": 1, "users": 0, "_k": 50, "suppressed": 0, "g1": ""}, {"step": 2, "users": 0, "_k": 50, "suppressed": 0, "g1": ""}))


def code(fn: Any, *a: Any) -> tuple[int, str, dict[str, Any]]:
    try:
        fn(*a); return 0, "", {}
    except ApiError as e:
        return e.status_code, e.code, e.params


def selftest() -> None:
    import jsonschema
    import yaml
    st, ch, clock = MemoryStore(), FakeCH(), [1_000_000.0]
    day = [date(2026, 10, 28)]
    today = lambda: day[0]                                                     # 週三 → 上週 10/19–10/25、前週 10/12–10/18
    missing = ["DMP_AGENT_MODEL_PROVIDER", "model_view.approved_params"]
    roles: dict[str, list[str] | None] = {"u_ana": ["data_analyst"], "u_other": ["data_analyst"], "u_rev": ["data_analyst"], "u_par": ["data_analyst"]}   # 權威來源（帳號＋指派現況）
    svc = RunService(st, ch, gateway=Gateway(st, ch, today=today), model_missing=lambda: missing, today=today, clock=lambda: clock[0], roles_of=roles.get)
    build_router(svc, lambda: None)
    ana, other = Subject("u_ana", ["data_analyst"]), Subject("u_other", ["data_analyst"])
    q = {"entry": "analyze", "question": "上週開戶漏斗跟前週比"}

    # A03 → A27：先問「開戶」口徑，再給候選配置，確認前不送查詢
    r = svc.create(ana, q, None); rid = r["run_id"]
    assert r["state"] == "queued" and r["mode"] == "rules" and r["skill"]["content_digest"] and "preset_properties" in r["contracts"] and "roles" not in r["initiator"]
    svc.tick(); r = svc.view(svc._own(ana, rid))
    assert r["state"] == "waiting_input" and [c["id"] for c in r["pending_question"]["choices"]] == ["kh_apply", "kh_review_pass"], r
    assert code(svc.answer, ana, rid, {"question_id": "term:開戶", "choice": "bond_deal"})[1] == "INVALID_ARGUMENT"
    svc.answer(ana, rid, {"question_id": "term:開戶", "choice": "kh_review_pass"}); svc.tick(); r = svc._own(ana, rid)
    assert r["state"] == "waiting_input" and r["pending_question"]["question_id"] == "confirm" and r["candidates"] and not ch.sql, (r.get("error"), ch.sql)
    assert "kh_review_pass" in json.dumps(r["candidates"][0]["query"]) and "kh_apply" not in json.dumps(r["candidates"][0]["query"])
    svc.answer(ana, rid, {"question_id": "confirm", "choice": "c0"}); svc.tick(); r = svc._own(ana, rid)
    assert r["state"] == "succeeded" and len(r["evidence"]) == 2 and r["result"]["label"] == "規則模式（非 AI）", (r.get("error"), r["state"])
    periods = sorted((e["period"]["from"], e["period"]["to"]) for e in r["evidence"].values())
    assert periods == [("2026-10-12", "2026-10-18"), ("2026-10-19", "2026-10-25")], periods                  # 1029-7：完整週一到週日
    assert any(s.get("query_id") == f"{rid}:s2_current:1" for s in ch.settings) and "可能原因" not in json.dumps(r, ensure_ascii=False)   # C7、規則模式不產生診斷
    eid = next(iter(r["evidence"])); ev = svc.evidence(ana, rid, eid)
    assert ev["data"]["rows"][0]["g1"] == "（未設定）" and ev["data"]["rows"][1]["rate_prev"] is None                # Q21
    # C6：他人看不到
    assert code(svc._own, other, rid)[0] == 404 and svc.list(other, None, 50) == [] and code(svc.evidence, other, rid, eid)[0] == 404 and code(svc.cancel, other, rid)[0] == 404
    # C3、C2、權限、未開放入口
    assert code(svc.create, ana, {**q, "project": "other"}, None)[:2] == (400, "INVALID_ARGUMENT")
    assert code(svc.create, ana, {**q, "user_id": "admin", "roles": ["admin"]}, None)[2]["rejected"] == ["roles", "user_id"]
    assert code(svc.create, ana, {**q, "params": {"window": 30}}, None)[2]["rejected"] == ["window"]
    assert code(svc.create, Subject("u_none", []), q, None)[:2] == (403, "AUTHZ_DENIED")
    assert code(svc.create, ana, {**q, "entry": "dashboard"}, None)[1] == "NOT_CONFIGURED"
    # C5：同鍵同內容回同一筆、不同內容 409；24 小時後可重用
    a = svc.create(other, q, "k1"); assert svc.create(other, q, "k1")["run_id"] == a["run_id"]
    assert code(svc.create, other, {**q, "question": "上週登入人數"}, "k1")[:2] == (409, "CONFLICT")
    svc.cancel(other, a["run_id"]); clock[0] += 86401
    assert svc.create(other, q, "k1")["run_id"] != a["run_id"]
    # A26：queued／running 滿 2 個就 429；waiting_input 不佔名額
    svc.create(other, {**q, "question": "上週登入人數"}, None)
    assert code(svc.create, other, {**q, "question": "x 人數"}, None)[:3] == (429, "RATE_LIMITED", {"limit": 2})
    svc.tick(); assert sum(x["state"] == "waiting_input" for x in svc.list(other, None, 50)) >= 1
    assert svc.create(other, {**q, "question": "上週登入人數"}, None)["state"] == "queued"
    # A17：技能契約不符 → 409，不建 run
    n = len(svc.list(ana, None, 200))
    def stale(name: str) -> Any:
        raise skill_loader.SkillError("CONTRACT_MISMATCH", "契約不相容", required={"semantic": ">=5"}, current={})
    assert code(RunService(st, ch, journal=svc.j, gateway=svc.gw, model_missing=lambda: missing, skills=stale).create, ana, q, None)[:2] == (409, "CONTRACT_MISMATCH")
    # A25／A25b：技能不允許規則模式時同步 503，缺項放 params.missing，通過 openapi Error schema
    denied = skill_loader.load("explore-data"); denied.meta = {**denied.meta, "rule_mode": "denied"}
    st_, c_, p_ = code(RunService(st, ch, journal=svc.j, gateway=svc.gw, model_missing=lambda: missing, skills=lambda n: denied).create, ana, q, None)
    assert (st_, c_, p_["missing"]) == (503, "MODEL_ENDPOINT_NOT_CONFIGURED", missing) and len(svc.list(ana, None, 200)) == n
    S = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    err = body(503, None, "MODEL_ENDPOINT_NOT_CONFIGURED", {"missing": missing}); jsonschema.Draft202012Validator(S["Error"]).validate(err)
    assert isinstance(err["detail"], str) and err["params"]["missing"] == missing
    # A19：逾時重試 1 次、其他執行錯誤重試 2 次，用完 failed；失敗後可重試成新 run（沿用已回答的口徑）
    for msg, attempts, want in (("Code: 159. TIMEOUT exceeded", 2, "TIMEOUT"), ("Code: 241. memory limit", 3, "INTERNAL")):
        b = svc.create(ana, q, None)["run_id"]
        svc.tick(); svc.answer(ana, b, {"question_id": "term:開戶", "choice": "kh_apply"}); svc.tick(); svc.answer(ana, b, {"question_id": "confirm", "choice": "c0"})
        ch.fail = msg; svc.tick(); ch.fail = None; r = svc._own(ana, b)
        step = next(x for x in r["steps"] if x["step_id"] == "s2_current")
        assert r["state"] == "failed" and r["error"]["code"] == want and step["attempts"] == attempts and step["query_ids"] == [f"{b}:s2_current:{attempts}"], (r["state"], r.get("error"), step)
    nr = svc.retry(ana, b); assert nr["retry_of"] == b and code(svc.retry, ana, rid)[1] == "CONFLICT"
    svc.tick(); assert svc._own(ana, nr["run_id"])["state"] == "succeeded"
    # A13：排隊後、等待回答後、重試後撤權 → 不送查詢、AUTHZ_DENIED（worker 每次工具呼叫前重讀；回答與重試照現在權限）
    rev = Subject("u_rev", ["data_analyst"]); n_sql = len(ch.sql)
    x = svc.create(rev, q, None)["run_id"]; roles["u_rev"] = []; svc.tick()
    assert svc._own(rev, x)["state"] == "failed" and svc._own(rev, x)["error"]["code"] == "AUTHZ_DENIED" and len(ch.sql) == n_sql
    roles["u_rev"] = ["data_analyst"]; y = svc.create(rev, q, None)["run_id"]; svc.tick(); roles["u_rev"] = None              # 帳號停用
    assert code(svc.answer, Subject("u_rev", []), y, {"question_id": "term:開戶", "choice": "kh_apply"})[:2] == (403, "AUTHZ_DENIED")
    svc.j.resume(y, answers={"term:開戶": "kh_apply"}, pending_question=None); svc.tick()                                         # 就算已回到佇列，worker 也擋下
    assert svc._own(rev, y)["error"]["code"] == "AUTHZ_DENIED" and len(ch.sql) == n_sql
    assert code(svc.retry, Subject("u_rev", []), x)[:2] == (403, "AUTHZ_DENIED")
    roles["u_rev"] = ["data_analyst"]; z = svc.retry(rev, x)["run_id"]; roles["u_rev"] = []; svc.tick()
    assert svc._own(rev, z)["error"]["code"] == "AUTHZ_DENIED" and len(ch.sql) == n_sql and "roles" not in json.dumps(svc.j.get_run(z)["initiator"])
    # P2：重試、回答後回佇列與建立共用名額；同一 run 連續重試、併發建立都不能超過 2
    roles["u_rev"] = ["data_analyst"]
    svc.retry(rev, x); svc.retry(rev, x)
    assert code(svc.retry, rev, x)[:2] == (429, "RATE_LIMITED") and code(svc.retry, rev, y)[:2] == (429, "RATE_LIMITED")
    wu = Subject("u_w", ["data_analyst"]); roles["u_w"] = ["data_analyst"]
    wr = svc.create(wu, q, None)["run_id"]; svc.tick(); assert svc._own(wu, wr)["state"] == "waiting_input"
    svc.create(wu, {**q, "question": "上週登入人數"}, None); svc.create(wu, {**q, "question": "上週委託人數"}, None)        # waiting_input 不佔名額，所以還能建 2 個
    assert code(svc.answer, wu, wr, {"question_id": "term:開戶", "choice": "kh_apply"})[:2] == (429, "RATE_LIMITED")      # 回到佇列要佔名額 → 已滿
    from concurrent.futures import ThreadPoolExecutor
    par = Subject("u_par", ["data_analyst"])
    with ThreadPoolExecutor(6) as ex:
        got = list(ex.map(lambda i: code(svc.create, par, {**q, "question": f"上週登入人數 {i}"}, None)[1], range(6)))
    assert got.count("") == 2 and got.count("RATE_LIMITED") == 4, got
    # 租約：worker 崩潰後由別的 worker 接手、已完成步驟不重做、舊 worker 寫入被擋；單步超過租約時背景續租
    import threading

    from .journal import Fenced

    class Crash(BaseException):
        pass

    lu = Subject("u_lease", ["data_analyst"]); roles["u_lease"] = ["data_analyst"]
    def ready() -> str:
        x = svc.create(lu, q, None)["run_id"]; svc.tick(); svc.answer(lu, x, {"question_id": "term:開戶", "choice": "kh_apply"}); svc.tick()
        svc.answer(lu, x, {"question_id": "confirm", "choice": "c0"}); return x
    def sent(rid_: str, sid: str) -> int:
        return sum(str(x.get("query_id", "")).startswith(f"{rid_}:{sid}:") for x in ch.settings)
    lr = ready(); svc.beat_s = 0
    def crash(st_: dict[str, Any]) -> None:
        if ":s3_previous:" in str(st_.get("query_id", "")):
            clock[0] += 120; raise Crash()
    ch.hook = crash
    try:
        svc.tick(worker="wA"); raise AssertionError("應崩潰")
    except Crash:
        pass
    ch.hook = None; ra = svc.j.get_run(lr); gen_a = ra["generation"]
    assert ra["state"] == "running" and {x["step_id"]: x["state"] for x in ra["steps"]}.items() >= {"s2_current": "succeeded", "s3_previous": "running"}.items(), ra["steps"]
    gen_b = svc.j.claim(lr, "wB", svc.lease_s, now=clock[0]); assert gen_b == gen_a + 1                                    # 租約過期 → 別的 worker 接得到
    for bad in (lambda: svc.j.put_step(lr, gen_a, {"step_id": "s3_previous", "state": "failed"}), lambda: svc.j.set_state(lr, gen_a, "failed")):
        try:
            bad(); raise AssertionError("舊 worker 應被擋")
        except Fenced:
            pass
    n_s2 = sent(lr, "s2_current"); svc.tick(worker="wB"); rb = svc._own(lu, lr)
    assert rb["state"] == "succeeded" and len(rb["evidence"]) == 2 and sent(lr, "s2_current") == n_s2, rb.get("error")              # s2 沿用、不重查
    assert next(x for x in rb["steps"] if x["step_id"] == "s3_previous")["attempts"] == 2
    # 單步 90 秒（合成時鐘）：有續租 → 完成；沒續租 → 寫入被擋、留給下一個 worker 接手
    lr2 = ready(); svc.beat_s = 0.001; beats = threading.Event(); svc.on_beat = beats.set
    def slow(st_: dict[str, Any]) -> None:
        if ":s2_current:" in str(st_.get("query_id", "")) and str(st_["query_id"]).endswith(":1"):
            for _ in range(9):
                clock[0] += 10; beats.clear(); assert beats.wait(5), "背景續租沒有發生"
    ch.hook = slow; svc.tick(worker="wC"); ch.hook = None
    assert svc._own(lu, lr2)["state"] == "succeeded"
    lr3 = ready(); svc.beat_s = 0; svc.on_beat = None
    def jump(st_: dict[str, Any]) -> None:
        if str(st_.get("query_id", "")) == f"{lr3}:s2_current:1":
            clock[0] += 90
    ch.hook = jump; svc.tick(worker="wD"); ch.hook = None
    assert svc.j.get_run(lr3)["state"] == "running"                                                                         # 沒續租：寫不進去
    clock[0] += 61; svc.tick(worker="wE"); assert svc._own(lu, lr3)["state"] == "succeeded"
    svc.beat_s = svc.lease_s / 3
    # 預算：中斷的 attempt 已預扣的量要算進去；接近 per_run 上限時崩潰接手 → 下一步 BUDGET_EXCEEDED、不送查詢
    from ..mcp.server import BYTES_PER_ROW, GW
    lr4 = ready(); budgets = dict(GW["budgets"]); GW["budgets"]["per_run_scan_bytes"] = 40e9; ch.est_rows = int(15e9 / BYTES_PER_ROW); svc.beat_s = 0
    def crash_s2(st_: dict[str, Any]) -> None:
        if str(st_.get("query_id", "")) == f"{lr4}:s2_current:1":
            clock[0] += 120; raise Crash()
    ch.hook = crash_s2
    try:
        svc.tick(worker="wF"); raise AssertionError("應崩潰")
    except Crash:
        pass
    ch.hook = None; clock[0] += 1
    from .runs import used_of
    assert used_of(svc.j.get_run(lr4)) == 15e9                                                                               # 崩潰前的預扣已寫進步驟
    svc.tick(worker="wG"); r4 = svc._own(lu, lr4)
    assert r4["state"] == "failed" and r4["error"]["code"] == "BUDGET_EXCEEDED" and sent(lr4, "s3_previous") == 0, (r4["state"], r4.get("error"))
    assert r4["budget"]["scan_bytes_used"] == 30e9                                                                           # 15（中斷）＋15（重跑）；s3 沒有預扣
    GW["budgets"].update(budgets); ch.est_rows = 1000; svc.beat_s = svc.lease_s / 3
    # 跨週接手：10/28 確認、崩潰，11/02 才接手 → 期間仍是確認當下的 10/19–10/25 與 10/12–10/18，s2 沿用的摘要與證據一致
    lr5 = ready(); svc.beat_s = 0
    def crash_s3(st_: dict[str, Any]) -> None:
        if str(st_.get("query_id", "")) == f"{lr5}:s3_previous:1":
            clock[0] += 120; raise Crash()
    ch.hook = crash_s3
    try:
        svc.tick(worker="wH"); raise AssertionError("應崩潰")
    except Crash:
        pass
    ch.hook = None; day[0] = date(2026, 11, 2); n5 = sent(lr5, "s2_current"); svc.tick(worker="wI"); r5 = svc._own(lu, lr5)
    ps = sorted((e["period"]["from"], e["period"]["to"]) for e in r5["evidence"].values())
    s2 = next(x for x in r5["steps"] if x["step_id"] == "s2_current"); e2 = next(e for e in r5["evidence"].values() if e["label"] == "上週")
    assert r5["state"] == "succeeded" and ps == [("2026-10-12", "2026-10-18"), ("2026-10-19", "2026-10-25")] and sent(lr5, "s2_current") == n5, (ps, r5.get("error"))
    assert e2["result_digest"] == s2["result_digest"] and e2["query"]["time"]["from"] == "2026-10-19" and not any("~" in x["step_id"] for x in r5["steps"])
    day[0] = date(2026, 10, 28); svc.beat_s = svc.lease_s / 3
    # 崩潰後才按取消：沒人會再接手（claim 不收已要求取消的 run），租約過期後 tick 收成 cancelled、不再送查詢、舊 worker 被擋
    lr6 = ready(); svc.beat_s = 0
    def crash_s2c(st_: dict[str, Any]) -> None:
        if str(st_.get("query_id", "")) == f"{lr6}:s2_current:1":
            clock[0] += 1; raise Crash()
    ch.hook = crash_s2c
    try:
        svc.tick(worker="wJ"); raise AssertionError("應崩潰")
    except Crash:
        pass
    ch.hook = None; r6 = svc.j.get_run(lr6); gen6 = r6["generation"]; q6 = len(ch.settings)
    assert r6["state"] == "running"
    svc.cancel(lu, lr6); svc.tick(worker="wK")
    assert svc.j.get_run(lr6)["state"] == "running"                                                                          # 租約未過期：可能只是慢，不搶收
    clock[0] += svc.lease_s + 1; svc.tick(worker="wK"); r6 = svc._own(lu, lr6)
    assert r6["state"] == "cancelled" and r6.get("finished_at") and len(ch.settings) == q6, (r6["state"], len(ch.settings) - q6)
    for bad in (lambda: svc.j.put_step(lr6, gen6, {"step_id": "s2_current", "state": "succeeded"}), lambda: svc.j.set_state(lr6, gen6, "succeeded")):
        try:
            bad(); raise AssertionError("舊 worker 醒來應被擋")
        except Fenced:
            pass
    assert code(svc.cancel, lu, lr6)[1] == "CONFLICT" and svc.tick(worker="wK") == 0                                       # 已結束再取消 409；不會重複收尾
    svc.beat_s = svc.lease_s / 3
    # 取消：排隊中直接取消；已結束再取消 409
    c = svc.create(ana, {**q, "question": "上週登入人數"}, None)["run_id"]; svc.cancel(ana, c)
    assert svc._own(ana, c)["state"] == "cancelled" and code(svc.cancel, ana, c)[1] == "CONFLICT"
    assert any(a["event"] == "agent.run.created" for a in st.audit_tail(1000))
    print("agent runs self-test ok: A03 補問口徑、A27 規則模式確認前不查、上週＝完整週一到週日、C7 query_id、Q21、C6 他人 404、C3/C2 欄位與參數拒絕、C5 冪等 24h、A26 名額、A17、A25/A25b、A19 重試、重試與取消、A13 撤權（排隊／等待回答／重試後）、名額共用入口（重試／回答／併發建立）、租約接手（崩潰接手不重做、舊 worker 被擋、長步驟續租、接手時預算從步驟重算、跨週接手沿用確認當下的期間、崩潰後取消由 tick 收成 cancelled）")


if __name__ == "__main__":
    selftest()
