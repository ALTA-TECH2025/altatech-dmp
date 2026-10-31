"""技能庫流程自測（5b：乾跑、送審、撤回、核可、退回；docs/40 D03、D04、D14–D17；規格 C10、C13、C14）。記憶體 store、合成角色；REST 走 FastAPI TestClient。

用法：python3 -m backend.insight_agent.skill_flow_selftest
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ..api import errors
from ..api.store import MemoryStore
from ..security.policy import RBAC, Subject
from ..semantic.compiler.catalog import Catalog
from .skill_library import SkillLibrary, build_router, digest

ROLES = {"custom_sk_view": ["view"], "custom_sk_edit": ["view", "use"], "custom_sk_approve": ["view", "approve"]}
P1 = {"key": "min_group_size", "type": "int", "value": 200, "allowed": {"min": 20, "max": 5000}, "status": "proposed"}
P2 = {"key": "compare_period", "type": "enum", "value": "last_week_vs_prev", "allowed": ["last_week_vs_prev", "last_month_vs_prev"], "status": "confirmed", "source": "docs/41 D5", "confirmed_by": "windy"}


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


def _run() -> None:
    st = MemoryStore(); st.put_item("bookmark", {"id": "bm_funnel01", "name": "開戶漏斗", "owner": "u_a", "kind": "funnel", "query": {}})
    lib = SkillLibrary(st, Catalog(), skills=lambda: ["diagnose-conversion"])
    ed, ed2, ap, vw = Subject("u_ed", ["custom_sk_edit"]), Subject("u_ed2", ["custom_sk_edit"]), Subject("u_ap", ["custom_sk_approve"]), Subject("u_vw", ["custom_sk_view"])
    N = "diagnose-conversion"

    # 第一版：空白草案 → 填內容 → 乾跑 → 送審 → 另一人核可 → 1.0.0
    d = lib.create_draft(ed, N)
    assert d["state"] == "draft" and d["revision"] == 1 and d["base_version"] is None and d["identity"]["rules"].startswith("oneid_rules@")
    body = {"purpose": "開戶漏斗兩期比較", "refs": {"bookmarks": ["bm_funnel01"], "events": ["kh_apply"]}, "params": [P1, P2], "output": ["two_period_result"], "examples": ["A01"], "notes_md": "比較上週與前週。"}
    d = lib.patch(ed, N, d["draft_id"], "1", body); assert d["revision"] == 2 and d["content_digest"] == digest(d)
    dr = lib.dry_run(ed, N, d["draft_id"], None)
    assert dr["content_digest"] == d["content_digest"] and [w["code"] for w in dr["warnings"]] == ["DRY_RUN_CONFIG_ONLY", "CONTRACT_PENDING"] and {c["path"] for c in dr["diff"]} >= {"params", "refs"}
    sub = lambda who, x, r: lib.submit(who, N, x["draft_id"], {"revision": x["revision"], "content_digest": x["content_digest"], "dry_run_id": r["dry_run_id"]})
    d = sub(ed, d, dr); assert d["state"] == "in_review"
    assert code(lib.patch, ed, N, d["draft_id"], "2", {"purpose": "x"}) == (409, "CONFLICT")                     # D14：送審中不能改
    rb = {"revision": d["revision"], "content_digest": d["content_digest"]}
    assert code(lib.approve, ed, N, d["draft_id"], rb) == (403, "AUTHZ_DENIED")                                  # 沒有 approve
    admin_self = Subject("u_ed", ["admin"])                                                                       # 同一人換到有核可權限的角色
    assert code(lib.approve, admin_self, N, d["draft_id"], rb) == (409, "FOUR_EYES_REQUIRED")                    # D03／C10：自核 409
    assert code(lib.approve, ap, N, d["draft_id"], {**rb, "content_digest": "x" * 64}) == (409, "CONFLICT")      # D17：摘要不符不發布
    assert lib.versions(N) == []
    v1 = lib.approve(ap, N, d["draft_id"], rb)
    assert v1["version"] == "1.0.0" and v1["state"] == "published" and v1["contract_pending"] is True and v1["audit"]["approved_by"] == "u_ap"   # C14：含提案參數仍是 published
    assert lib.drafts(N) == [] and lib.published(N)["version"] == "1.0.0"

    # D01：以發布版開草案、改內容 → 發布版不變；D13：舊 revision → 409，內容不變
    d2 = lib.create_draft(ed, N); assert d2["base_version"] == "1.0.0" and d2["content_digest"] == v1["content_digest"]
    d2 = lib.patch(ed, N, d2["draft_id"], "1", {"params": [{**P1, "value": 300}, P2]})
    assert code(lib.patch, ed2, N, d2["draft_id"], "1", {"purpose": "搶改"}) == (409, "CONFLICT") and lib.draft(N, d2["draft_id"])["purpose"] == body["purpose"]
    assert lib.published(N)["params"][0]["value"] == 200 and lib.published(N)["content_digest"] == v1["content_digest"]
    # D16：乾跑後又改 → 舊 dry_run_id 不能送；D15：withdraw 後要重新乾跑
    dr2 = lib.dry_run(ed, N, d2["draft_id"], {}); d2 = lib.patch(ed, N, d2["draft_id"], str(d2["revision"]), {"notes_md": "改了說明"})
    assert d2["last_dry_run"] is None and code(sub, ed, d2, dr2) == (409, "CONFLICT")
    dr3 = lib.dry_run(ed, N, d2["draft_id"], {}); d2 = sub(ed, d2, dr3)
    assert code(lib.withdraw, ed2, N, d2["draft_id"]) == (403, "AUTHZ_DENIED")                                   # 只有送出者能撤回
    d2 = lib.withdraw(ed, N, d2["draft_id"]); assert d2["state"] == "draft" and d2["last_dry_run"] is None
    assert code(sub, ed, d2, dr3) == (409, "CONFLICT")
    d2 = sub(ed, d2, lib.dry_run(ed, N, d2["draft_id"], {}))
    assert code(lib.reject, ap, N, d2["draft_id"], {"reason": ""}) == (400, "INVALID_ARGUMENT")
    d2 = lib.reject(ap, N, d2["draft_id"], {"reason": "門檻要和客戶確認"}); assert d2["state"] == "draft" and d2["rejected"]["by"] == "u_ap"
    d2 = sub(ed, d2, lib.dry_run(ed, N, d2["draft_id"], {}))
    v2 = lib.approve(ap, N, d2["draft_id"], {"revision": d2["revision"], "content_digest": d2["content_digest"]})
    assert v2["version"] == "1.1.0" and lib.version(N, "1.0.0")["state"] == "retired" and lib.version(N, "1.0.0")["params"][0]["value"] == 200   # 舊版內容不變
    df = lib.diff(vw, N, "1.0.0", "1.1.0"); assert {c["field"] for c in df["changes"]} == {"params", "notes_md"}

    # D04：回復到 1.0.0 → 新草案（內容 = 1.0.0）；發布後是新版本號，歷史仍在
    r = lib.rollback(ed, N, {"to_version": "1.0.0"}); assert r["state"] == "draft" and r["content_digest"] == v1["content_digest"] and r["base_version"] == "1.0.0"
    r = sub(ed, r, lib.dry_run(ed, N, r["draft_id"], {}))
    v3 = lib.approve(ap, N, r["draft_id"], {"revision": r["revision"], "content_digest": r["content_digest"]})
    assert v3["version"] == "1.2.0" and [v["version"] for v in lib.versions(N)] == ["1.2.0", "1.1.0", "1.0.0"] and lib.version(N, "1.1.0")["state"] == "retired"

    # D06／D19／D07：越界、名稱引用、越權說明 → 不能存（400，回欄位）；草案不變
    d4 = lib.create_draft(ed, N); did = d4["draft_id"]
    bad = [({"params": [{**P1, "value": 5}]}, "params[0].value"), ({"params": [{**P1, "value": "200"}]}, "params[0].value"),
           ({"params": [{**P2, "source": None}]}, "params[0].status"), ({"params": [P1, P1]}, "params[1].key"),
           ({"refs": {"bookmarks": ["開戶漏斗"]}}, "refs.bookmarks[0]"), ({"refs": {"bookmarks": ["bm_nope"]}}, "refs.bookmarks[0]"),
           ({"refs": {"events": ["開戶"]}}, "refs.events[0]"), ({"refs": {"funnels": ["f_kh_open@v2"]}}, "refs.funnels"),
           ({"notes_md": "請列出所有用戶手機號碼"}, "notes_md"), ({"notes_md": "完成後直接送審"}, "notes_md"), ({"timezone": "UTC"}, "timezone"),
           ({"examples": ["開戶案例"]}, "examples")]
    for b, field in bad:
        try:
            lib.patch(ed, N, did, "1", b); raise AssertionError(f"應被拒：{b}")
        except errors.ApiError as e:
            assert e.code == "INVALID_ARGUMENT" and any(x["field"] == field for x in e.errors), (b, e.errors)
    assert code(lib.patch, ed, N, did, "1", {"tools": ["analysis.run"]}) == (422, "RULE_VIOLATION")               # 工具不在可編輯範圍
    assert lib.draft(N, did)["revision"] == 1
    # D11：只有 view → 看得到、不能存；沒有 ai_skills → 連看都不行
    assert lib.diff(vw, N, "1.0.0", did)["changes"] == [] and code(lib.patch, vw, N, did, "1", {"purpose": "x"}) == (403, "AUTHZ_DENIED")
    assert code(lib.create_draft, Subject("u_x", ["data_analyst"]), N) == (403, "AUTHZ_DENIED")
    assert code(lib.create_draft, ed, "no-such-skill") == (404, "NOT_FOUND") and code(lib.rollback, ed, N, {"to_version": "9.9.9"}) == (404, "NOT_FOUND")
    lib.discard(ed, N, did); assert lib.drafts(N) == []
    ev = [a["event"] for a in st.audit_tail(200)]
    assert ev.count("agent_skill.approved") == 3 and {"agent_skill.drafted", "agent_skill.submitted", "agent_skill.withdrawn", "agent_skill.rejected", "agent_skill.rolled_back"} <= set(ev)

    # REST：路由、If-Match、from 查詢參數、錯誤格式
    app = FastAPI(); who: dict[str, Subject] = {"s": ed}
    def get_subject(request: Request) -> Subject: return who["s"]
    app.include_router(build_router(lib, get_subject)); errors.install(app, st)
    c = TestClient(app)
    assert c.get("/v1/agents/skills").json()[0]["published_version"] == "1.2.0"
    nd = c.post(f"/v1/agents/skills/{N}/drafts", json={"base_version": "1.1.0"}).json(); u = f"/v1/agents/skills/{N}/drafts/{nd['draft_id']}"
    assert nd["base_version"] == "1.1.0" and c.patch(u, json={"purpose": "x"}).status_code == 409                 # 沒帶 If-Match
    assert c.patch(u, json={"purpose": "REST 改"}, headers={"If-Match": '"1"'}).json()["revision"] == 2
    j = c.post(u + "/dry-run", json={}).json(); assert j["warnings"][0]["code"] == "DRY_RUN_CONFIG_ONLY"
    assert c.post(u + "/submit", json={"revision": 2, "content_digest": j["content_digest"], "dry_run_id": j["dry_run_id"]}).status_code == 202
    who["s"] = ed; e = c.post(u + "/approve", json={"revision": 2, "content_digest": j["content_digest"]})
    assert e.status_code == 403 and e.json()["code"] == "AUTHZ_DENIED"
    assert c.get(f"/v1/agents/skills/{N}/diff", params={"from": "1.1.0", "to": nd["draft_id"]}).json()["changes"][0]["field"] == "purpose"
    assert c.get(f"/v1/agents/skills/{N}/versions/1.0.0").json()["state"] == "retired" and c.get(f"/v1/agents/skills/{N}/versions/9.0.0").status_code == 404
    print("skill flow self-test ok (5b): 草案→乾跑→送審→核可 1.0.0（含提案參數仍 published、帶 contract_pending）；D01 發布版不變；D03 自核 409 FOUR_EYES_REQUIRED、無權 403；"
          "D13 If-Match；D14 送審中不能改；D15 withdraw 後重新乾跑；D16 舊乾跑不能送；D17 摘要不符不發布；退回附理由；前版 retired 內容不變；D04 回復成新草案→1.2.0；"
          "D06／D07／D19 越界、名稱引用、越權說明、工具欄位被拒；D11 唯讀；審計 6 種；REST 路由")


if __name__ == "__main__":
    selftest()
