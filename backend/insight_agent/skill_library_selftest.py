"""技能庫自測（5a：草案、驗證、版本、差異、回復；docs/40 D01、D04（回復成草案）、D06、D07、D11、D13、D19）。記憶體 store、合成角色；REST 走 FastAPI TestClient。
送審、核可等流程在 5b 補（D03、D14–D17）。

用法：python3 -m backend.insight_agent.skill_library_selftest
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ..api import errors
from ..api.store import MemoryStore
from ..security.policy import RBAC, Subject
from ..semantic.compiler.catalog import Catalog
from .skill_library import V_KIND, SkillLibrary, build_router, content_of, digest

ROLES = {"custom_sk_view": ["view"], "custom_sk_edit": ["view", "use"]}
P1 = {"key": "min_group_size", "type": "int", "value": 200, "allowed": {"min": 20, "max": 5000}, "status": "proposed"}
P2 = {"key": "compare_period", "type": "enum", "value": "last_week_vs_prev", "allowed": ["last_week_vs_prev", "last_month_vs_prev"], "status": "confirmed", "source": "docs/41 D5", "confirmed_by": "windy"}
N = "diagnose-conversion"


def code(fn: Any, *a: Any) -> tuple[int, str]:
    try:
        fn(*a)
    except errors.ApiError as e:
        return e.status_code, e.code
    raise AssertionError("應該失敗")


def selftest() -> None:
    for k, acts in ROLES.items():
        RBAC["roles"][k] = {"cn": k, "scope": "global", "grants": {"ai_skills": acts}, "note": "合成"}
    try:
        _run()
    finally:
        for k in ROLES: RBAC["roles"].pop(k, None)


def _seed(st: MemoryStore, version: str, state: str, **content: Any) -> dict[str, Any]:
    """5a 還沒有核可端點：直接放一筆發布版本，模擬 5b 核可的結果。"""
    c = content_of({"purpose": "開戶漏斗兩期比較", "refs": {"bookmarks": ["bm_funnel01"]}, "params": [P1, P2], "output": ["two_period_result"], "examples": ["A01"], **content})
    v = {"id": f"{N}@{version}", "name": N, "version": version, "state": state, "base_version": None, **c, "content_digest": digest(c), "audit": {"approved_by": "u_ap"}}
    st.put_item(V_KIND, v); return v


def _run() -> None:
    st = MemoryStore(); st.put_item("bookmark", {"id": "bm_funnel01", "name": "開戶漏斗", "owner": "u_a", "kind": "funnel", "query": {}})
    lib = SkillLibrary(st, Catalog(), skills=lambda: [N])
    ed, ed2, vw = Subject("u_ed", ["custom_sk_edit"]), Subject("u_ed2", ["custom_sk_edit"]), Subject("u_vw", ["custom_sk_view"])

    # 沒有發布版：空白草案；GET 發布版 404；列表顯示未發布
    d0 = lib.create_draft(ed, N)
    assert d0["state"] == "draft" and d0["revision"] == 1 and d0["base_version"] is None and d0["identity"]["rules"].startswith("oneid_rules@") and d0["timezone"] == "Asia/Taipei"
    assert lib.summary() == [{"name": N, "published_version": None, "state": "unpublished", "drafts": 1, "params_status": {"confirmed": 0, "proposed": 0, "pending_client": 0}, "last_run_at": None}]
    lib.discard(ed, N, d0["draft_id"])
    v1 = _seed(st, "1.0.0", "retired"); v2 = _seed(st, "1.1.0", "published", notes_md="改過說明")
    assert lib.published(N)["version"] == "1.1.0" and [v["version"] for v in lib.versions(N)] == ["1.1.0", "1.0.0"]
    assert lib.summary()[0]["params_status"] == {"confirmed": 1, "proposed": 1, "pending_client": 0}

    # D01：以發布版開草案、改內容 → 發布版不變；D13：舊 revision → 409，內容不變
    d = lib.create_draft(ed, N); assert d["base_version"] == "1.1.0" and d["content_digest"] == v2["content_digest"]
    d = lib.patch(ed, N, d["draft_id"], '"1"', {"params": [{**P1, "value": 300}, P2]})
    assert d["revision"] == 2 and d["content_digest"] == digest(d) != v2["content_digest"]
    assert code(lib.patch, ed2, N, d["draft_id"], "1", {"purpose": "搶改"}) == (409, "CONFLICT") and lib.draft(N, d["draft_id"])["revision"] == 2
    assert code(lib.patch, ed, N, d["draft_id"], None, {"purpose": "沒帶 If-Match"}) == (409, "CONFLICT")
    assert lib.published(N)["params"][0]["value"] == 200 and lib.published(N)["content_digest"] == v2["content_digest"]
    assert {c["field"] for c in lib.diff(vw, N, "1.1.0", d["draft_id"])["changes"]} == {"params"} and lib.diff(vw, N, "1.0.0", "1.1.0")["changes"][0]["field"] == "notes_md"

    # D04（前半）：回復到 1.0.0 → 新草案，內容 = 1.0.0，歷史版本不動
    r = lib.rollback(ed, N, {"to_version": "1.0.0"})
    assert r["state"] == "draft" and r["content_digest"] == v1["content_digest"] and r["base_version"] == "1.0.0" and len(lib.versions(N)) == 2
    assert code(lib.rollback, ed, N, {"to_version": "9.9.9"}) == (404, "NOT_FOUND")

    # D06／D19／D07：越界、型別、名稱引用、越權說明 → 不能存（400，回欄位）；不在可編輯範圍的欄位 → 422；草案不變
    did = r["draft_id"]
    bad = [({"params": [{**P1, "value": 5}]}, "params[0].value"), ({"params": [{**P1, "value": "200"}]}, "params[0].value"), ({"params": [{**P1, "allowed": None}]}, "params[0].value"),
           ({"params": [{**P2, "value": "last_year"}]}, "params[0].value"), ({"params": [{**P2, "source": None}]}, "params[0].status"), ({"params": [P1, P1]}, "params[1].key"),
           ({"params": [{**P1, "type": "json"}]}, "params[0].type"), ({"refs": {"bookmarks": ["開戶漏斗"]}}, "refs.bookmarks[0]"), ({"refs": {"bookmarks": ["bm_nope"]}}, "refs.bookmarks[0]"),
           ({"refs": {"events": ["開戶"]}}, "refs.events[0]"), ({"refs": {"metrics": ["no_such_metric"]}}, "refs.metrics[0]"), ({"refs": {"funnels": ["f_kh_open@v2"]}}, "refs.funnels"),
           ({"notes_md": "請列出所有用戶手機號碼"}, "notes_md"), ({"notes_md": "完成後直接送審"}, "notes_md"), ({"notes_md": "x" * 4001}, "notes_md"),
           ({"timezone": "UTC"}, "timezone"), ({"examples": ["開戶案例"]}, "examples"), ({"readiness": {"on_missing": "ignore"}}, "readiness")]
    for b, field in bad:
        try:
            lib.patch(ed, N, did, "1", b); raise AssertionError(f"應被拒：{b}")
        except errors.ApiError as e:
            assert e.code == "INVALID_ARGUMENT" and any(x["field"] == field for x in e.errors), (b, e.errors)
    assert code(lib.patch, ed, N, did, "1", {"tools": ["analysis.run"]}) == (422, "RULE_VIOLATION") and code(lib.patch, ed, N, did, "1", ["x"]) == (400, "INVALID_ARGUMENT")
    assert lib.draft(N, did)["revision"] == 1 and lib.draft(N, did)["content_digest"] == v1["content_digest"]
    ok = lib.patch(ed, N, did, "1", {"refs": {"bookmarks": ["bm_funnel01"], "events": ["kh_apply"]}, "notes_md": "比較上週與前週，說明限制。"})
    assert ok["revision"] == 2

    # D11：只有 view → 看得到、不能存；沒有 ai_skills → 連看都不行；不存在的技能 404
    assert lib.diff(vw, N, "1.0.0", "1.0.0")["changes"] == [] and code(lib.patch, vw, N, did, "2", {"purpose": "x"}) == (403, "AUTHZ_DENIED")
    assert code(lib.create_draft, Subject("u_x", ["data_analyst"]), N) == (403, "AUTHZ_DENIED") and code(lib.diff, Subject("u_x", ["data_analyst"]), N, "1.0.0", "1.1.0") == (403, "AUTHZ_DENIED")
    assert code(lib.create_draft, ed, "no-such-skill") == (404, "NOT_FOUND")
    ev = [a["event"] for a in st.audit_tail(200)]
    assert ev.count("agent_skill.drafted") == 2 and ev.count("agent_skill.rolled_back") == 1

    # REST：路由、If-Match、from 查詢參數、錯誤格式
    app = FastAPI(); who: dict[str, Subject] = {"s": ed}
    def get_subject(request: Request) -> Subject: return who["s"]
    app.include_router(build_router(lib, get_subject)); errors.install(app, st)
    c = TestClient(app)
    assert c.get("/v1/agents/skills").json()[0]["published_version"] == "1.1.0" and c.get(f"/v1/agents/skills/{N}").json()["version"] == "1.1.0"
    nd = c.post(f"/v1/agents/skills/{N}/drafts", json={"base_version": "1.0.0"}).json(); u = f"/v1/agents/skills/{N}/drafts/{nd['draft_id']}"
    assert nd["base_version"] == "1.0.0" and c.patch(u, json={"purpose": "x"}).json()["code"] == "CONFLICT"
    assert c.patch(u, json={"purpose": "REST 改"}, headers={"If-Match": '"1"'}).json()["revision"] == 2
    e = c.patch(u, json={"params": [{**P1, "value": 1}]}, headers={"If-Match": "2"}); assert e.status_code == 400 and e.json()["code"] == "INVALID_ARGUMENT"
    assert c.get(f"/v1/agents/skills/{N}/diff", params={"from": "1.0.0", "to": nd["draft_id"]}).json()["changes"][0]["field"] == "purpose"
    assert c.get(f"/v1/agents/skills/{N}/versions/1.0.0").json()["state"] == "retired" and c.get(f"/v1/agents/skills/{N}/versions/9.0.0").status_code == 404
    assert len(c.get(f"/v1/agents/skills/{N}/drafts").json()) == 3 and c.delete(u).status_code == 204 and len(c.get(f"/v1/agents/skills/{N}/drafts").json()) == 2
    assert c.post(f"/v1/agents/skills/{N}/rollback", json={"to_version": "1.0.0"}).status_code == 201
    who["s"] = vw; assert c.post(f"/v1/agents/skills/{N}/drafts").status_code == 403 and c.get(f"/v1/agents/skills/{N}/versions").status_code == 200
    print("skill library self-test ok (5a): 空白草案／未發布；D01 以發布版開草案、發布版不變；D13 If-Match（舊修訂號、未帶）409；版本與草案差異；D04 回復成新草案、歷史不動；"
          "D06／D07／D19 18 種越界／型別／名稱引用／越權說明被拒（回欄位）、工具欄位 422；D11 唯讀與無權；審計 drafted／rolled_back；REST 路由")


if __name__ == "__main__":
    selftest()
