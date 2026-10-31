"""看板草稿自測：Gateway dashboard.draft 經 journal.commit_operation（契約 C4、C6）。對應 docs/40 B01–B04；合成資料、假 ClickHouse、規則模式。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ..api import dashboards
from ..api.store import MemoryStore
from ..mcp.server import Gateway
from ..security.policy import RBAC, Subject
from .journal import Fenced, MemoryJournal, PostgresJournal, artifact_id
from .runs import RunService, _Ctx, digest
from .runs_selftest import FakeCH


class Crash(BaseException):
    pass


def selftest(st: Any = None, journal: Any = None, label: str = "memory") -> None:
    ch, clock = FakeCH(), [1_000_000.0]
    if st is None:
        st = MemoryStore(); journal = MemoryJournal(st)
    today = lambda: date(2026, 10, 28)                                         # 上週 10/19–10/25、前週 10/12–10/18
    roles: dict[str, list[str]] = {"u_mgr": ["data_analyst"], "u_peer": ["data_analyst"], "u_mkt": ["marketing"], "u_cus": ["custom_ai_draft"]}
    svc = RunService(st, ch, journal=journal, gateway=Gateway(st, ch, today=today), model_missing=lambda: ["DMP_AGENT_MODEL_PROVIDER"], today=today, clock=lambda: clock[0], roles_of=roles.get)
    svc.beat_s = 0
    S = {u: Subject(u, r) for u, r in roles.items()}
    q = {"entry": "dashboard", "question": "上週開戶漏斗跟前週比"}

    def ready(u: str) -> str:
        x = svc.create(S[u], q, None)["run_id"]; svc.tick()
        svc.answer(S[u], x, {"question_id": "term:開戶", "choice": "kh_review_pass"}); svc.tick()
        svc.answer(S[u], x, {"question_id": "confirm", "choice": "c0"}); return x
    def op_of(rid: str) -> str:
        return "op_" + digest([svc.j.get_run(rid).get("op_root") or rid, "s4_dashboard"])[:16]
    def drafts(owner: str | None = None) -> list[dict[str, Any]]:
        return [d for d in st.items("dashboard") if (d.get("origin") or {}).get("kind") == "ai_draft" and (owner is None or d["owner"] == owner)]
    def created(did: str) -> int:
        return sum(a["event"] == "dashboard.created" and a.get("dashboard") == did for a in st.audit_tail(10000))

    # B01：私人草稿、origin、3–4 張卡存 kind + query；期間固定在上週／前週；卡片查詢就是證據查詢（同一口徑）
    x = ready("u_mgr"); svc.tick(); r = svc._own(S["u_mgr"], x)
    assert r["state"] == "succeeded", r.get("error")
    did = r["result"]["dashboard_id"]; d = next(d for d in st.items("dashboard") if d["id"] == did)
    assert did == artifact_id("dashboard", op_of(x)) and {"kind": "dashboard", "id": did, "operation_id": op_of(x)} in r["artifacts"]
    assert d["origin"] == {"kind": "ai_draft", "run_id": x, "skill": {"name": r["skill"]["name"], "version": r["skill"]["version"]}}, d["origin"]
    assert d["owner"] == "u_mgr" and d["shared"] == {"roles": [], "users": []} and d["status"] == "draft" and 3 <= len(d["cards"]) <= 4
    ev = {e["label"]: e["query"] for e in r["evidence"].values()}
    strip = lambda qq: {**qq, "time": {k: v for k, v in qq["time"].items() if k != "pinned"}}
    assert [strip(c["query"]) for c in d["cards"][:2]] == [ev["上週"], ev["前週"]] and all(c["query"]["time"]["pinned"] for c in d["cards"])
    assert d["cards"][2]["query"]["group_by"] == [{"field": "channel"}] and all(set(c) >= {"kind", "query"} and c["created_by"] == "u_mgr" for c in d["cards"])
    assert all(dashboards.compile_kind(c["kind"], S["u_mgr"], c["query"], svc.gw.cat) for c in d["cards"])        # 擁有者照自己權限可重算
    # REST：擁有者看得到 origin、可照一般看板編輯；別人看不到（私人、不分享）
    who = [S["u_mgr"]]; app = FastAPI(); app.include_router(dashboards.build_router(st, lambda: who[0], None)); c = TestClient(app)
    g = c.get(f"/v1/dashboards/{did}").json(); assert g["origin"]["run_id"] == x and g["editable"] and g["mine"]
    assert c.put(f"/v1/dashboards/{did}", json={"name": "我的開戶週報"}).json()["origin"]["kind"] == "ai_draft"
    assert next(i for i in c.get("/v1/dashboards").json() if i["id"] == did)["origin"]["kind"] == "ai_draft"
    who[0] = S["u_peer"]; assert c.get(f"/v1/dashboards/{did}").status_code == 404 and all(i["id"] != did for i in c.get("/v1/dashboards").json())
    assert c.post("/v1/dashboards", json={"name": "人建"}).json()["origin"] is None
    assert created(did) == 1 and next(a for a in st.audit_tail(10000) if a.get("dashboard") == did)["via"] == "agent"

    # B02：內建行銷（看板只有 view）→ 草稿步驟 AUTHZ_DENIED、不建看板；授權失敗不覆寫證據
    m = ready("u_mkt"); svc.tick(); rm = svc._own(S["u_mkt"], m)
    assert rm["state"] == "failed" and rm["error"]["code"] == "AUTHZ_DENIED" and not drafts("u_mkt") and len(rm["evidence"]) == 2, (rm["state"], rm.get("error"))
    # 自訂角色勾了 overview.use → 可以建；角色撤掉後同一失敗 run 重試沿用 operation_id 的根（op_root）
    RBAC["roles"]["custom_ai_draft"] = {"cn": "合成：AI 週報", "scope": "global", "lookback_days": None, "grants": {"overview": ["view", "use"], "events": ["view", "use"], "funnel": ["view", "use"], "ai_ask": ["use"]}, "note": ""}
    try:
        u = ready("u_cus"); svc.tick(); ru = svc._own(S["u_cus"], u)
        assert ru["state"] == "succeeded" and [x_["id"] for x_ in drafts("u_cus")] == [ru["result"]["dashboard_id"]], ru.get("error")
        roles["u_mkt"] = ["custom_ai_draft"]; S["u_mkt"] = Subject("u_mkt", ["custom_ai_draft"])
        m2 = svc.retry(S["u_mkt"], m)["run_id"]; svc.tick(); rm2 = svc._own(S["u_mkt"], m2)
        assert rm2["state"] == "succeeded" and op_of(m2) == op_of(m) and rm2["result"]["dashboard_id"] == artifact_id("dashboard", op_of(m)) and len(drafts("u_mkt")) == 1
    finally:
        RBAC["roles"].pop("custom_ai_draft", None); roles["u_mkt"] = ["marketing"]; S["u_mkt"] = Subject("u_mkt", ["marketing"])

    real = svc.j.commit_operation
    # B03-1：提交後、步驟記成功前崩潰 → 租約過期由別的 worker 接手，再呼叫一次拿到同一張（existing），只有 1 張
    b = ready("u_mgr")
    def crash_after(*a: Any) -> Any:
        real(*a); clock[0] += 120; raise Crash()
    svc.j.commit_operation = crash_after
    try:
        svc.tick(worker="wA"); raise AssertionError("應崩潰")
    except Crash:
        pass
    finally:
        svc.j.commit_operation = real
    assert next(s_ for s_ in svc.j.get_run(b)["steps"] if s_["step_id"] == "s4_dashboard")["state"] == "running"
    svc.tick(worker="wB"); rb = svc._own(S["u_mgr"], b); bid = artifact_id("dashboard", op_of(b))
    s4 = next(s_ for s_ in rb["steps"] if s_["step_id"] == "s4_dashboard")
    assert rb["state"] == "succeeded" and rb["result"]["dashboard_id"] == bid and s4["attempts"] == 2 and s4["operation_id"] == op_of(b) and sum(d_["id"] == bid for d_ in st.items("dashboard")) == 1 and created(bid) <= 1
    calls = [a for a in st.audit_tail(10000) if a["event"] == "agent.tool_called" and a.get("operation_id") == op_of(b)]
    assert [a["ok"] for a in calls] == [True], calls                                                                    # 崩潰那次沒寫完審計；接手那次記到 operation_id（dashboard.created 至多一次，見 PR 說明）
    # B03-2：操作紀錄已寫、產物未寫時崩潰 → 整筆回滾；控制器照 INTERNAL 重試，最後只有 1 張
    e = ready("u_mgr"); fired = []
    def once(point: str) -> None:
        if point == "after_operation_insert" and not fired:
            fired.append(point); raise RuntimeError("注入崩潰")
    svc.j.fault = once; svc.tick(); svc.j.fault = None; re_ = svc._own(S["u_mgr"], e); eid = artifact_id("dashboard", op_of(e))
    assert fired and re_["state"] == "succeeded" and sum(d_["id"] == eid for d_ in st.items("dashboard")) == 1 and next(s_ for s_ in re_["steps"] if s_["step_id"] == "s4_dashboard")["attempts"] == 2
    # B03-3：租約過期的舊 worker → 寫入被擋（Fenced 穿過 Gateway、不被包成錯誤信封），沒有產物；新 worker 接手只建 1 張
    f = ready("u_mgr"); gens: list[int] = []
    def stale(rid: str, gen: int, *a: Any) -> Any:
        if not gens:
            gens.append(gen); clock[0] += 120
        return real(rid, gen, *a)
    svc.j.commit_operation = stale
    try:
        assert svc.tick(worker="wC") == 0 and not svc.j.operations(f)
        svc.tick(worker="wD")
    finally:
        svc.j.commit_operation = real
    rf = svc._own(S["u_mgr"], f); fid = artifact_id("dashboard", op_of(f))
    assert rf["state"] == "succeeded" and rf["generation"] == gens[0] + 1 and sum(d_["id"] == fid for d_ in st.items("dashboard")) == 1
    try:
        real(f, gens[0], op_of(f), "dashboard.draft", "dashboard", {}, {}); raise AssertionError("舊世代應被擋")
    except Fenced:
        pass
    # B03-4：草稿已提交、步驟還沒記成功就崩潰，接手前發起人被撤權 → run 失敗；恢復權限後重試的新 run 沿用 operation_id，拿到同一張、不另建
    h = ready("u_mgr"); svc.j.commit_operation = crash_after
    try:
        svc.tick(worker="wE"); raise AssertionError("應崩潰")
    except Crash:
        pass
    finally:
        svc.j.commit_operation = real
    roles["u_mgr"] = []; svc.tick(worker="wF"); roles["u_mgr"] = ["data_analyst"]; hid = artifact_id("dashboard", op_of(h))
    assert svc._own(S["u_mgr"], h)["state"] == "failed" and sum(d_["id"] == hid for d_ in st.items("dashboard")) == 1
    h2 = svc.retry(S["u_mgr"], h)["run_id"]; svc.tick(); rh = svc._own(S["u_mgr"], h2)
    assert rh["state"] == "succeeded" and rh["result"]["dashboard_id"] == hid and sum(d_["id"] == hid for d_ in st.items("dashboard")) == 1
    assert next(d_ for d_ in st.items("dashboard") if d_["id"] == hid)["origin"]["run_id"] == h                       # 產物不被覆寫：origin 仍是第一次提交的 run

    # B04：兩個 worker（兩個 run 共用同一 operation_id）同時寫 → 只提交一次，另一個拿到同一張；改了標題重送 → 409 CONFLICT、原看板不變
    k1, k2 = (svc.create(S["u_mgr"], {**q, "question": f"上週開戶漏斗跟前週比 {i}"}, None)["run_id"] for i in (1, 2))
    g1, g2 = svc.j.claim(k1, "w1", 60, now=clock[0]), svc.j.claim(k2, "w2", 60, now=clock[0])
    op = "op_b04_synthetic"; args = {"name": "合成週報", "cards": [{"title": "上週漏斗", "kind": d["cards"][0]["kind"], "query": d["cards"][0]["query"]}]}
    ctx = lambda rid, gen: _Ctx(S["u_mgr"], rid, "s4_dashboard", operation_id=op, commit=svc._committer(rid, gen, op))
    with ThreadPoolExecutor(2) as ex:
        envs = list(ex.map(lambda p: svc.gw.call(ctx(*p), "dashboard.draft", args), ((k1, g1), (k2, g2))))
    assert all(v["ok"] for v in envs) and [v["data"] for v in envs] == [envs[0]["data"]] * 2 and envs[0]["data"]["draft"] is True, envs
    oid = envs[0]["data"]["dashboard_id"]; assert [o["run_id"] for o in svc.j.operations() if o["operation_id"] == op] in ([k1], [k2])   # 一筆操作紀錄 = 一次提交
    before = next(d_ for d_ in st.items("dashboard") if d_["id"] == oid)
    bad = svc.gw.call(ctx(k1, g1), "dashboard.draft", {**args, "name": "改過的標題"})
    assert not bad["ok"] and bad["error"]["code"] == "CONFLICT" and not bad["error"]["retryable"] and bad["error"]["detail"]["operation_id"] == op
    assert next(d_ for d_ in st.items("dashboard") if d_["id"] == oid) == before and sum(d_["id"] == oid for d_ in st.items("dashboard")) == 1 and created(oid) == 1
    # 寫入工具一定要由控制器帶 operation_id；卡片格式錯 → INVALID_ARGUMENT、不寫
    assert svc.gw.call(_Ctx(S["u_mgr"], k1, "s"), "dashboard.draft", args)["error"]["code"] == "INTERNAL"
    assert svc.gw.call(ctx(k1, g1), "dashboard.draft", {"name": "x", "cards": [{"kind": "nope", "query": {}}]})["error"]["code"] == "INVALID_ARGUMENT"
    print(f"agent dashboard draft self-test ok ({label}): B01 私人草稿／origin／kind+query／期間固定／擁有者可編輯、他人 404，B02 行銷 403 不建、自訂角色可建、重試沿用 operation_id，"
          "B03 提交後崩潰接手／回滾後重試／過期 worker 被擋／失敗後重試都只有 1 張，B04 兩 worker 同 operation 一次提交、改標題 409 原看板不變")


def main() -> None:
    import os
    import uuid
    selftest()
    if not (dsn := os.environ.get("DMP_PG_DSN")):
        print("DMP_PG_DSN 未設定：略過 PostgreSQL 看板草稿自測"); return
    from ..api.store_pg import PostgresStore
    ps = PostgresStore(dsn, schema="selftest_d" + uuid.uuid4().hex[:6])
    try:
        selftest(ps, PostgresJournal(ps), "postgres")
    finally:
        ps.drop_schema(); ps.close()


if __name__ == "__main__":
    main()
