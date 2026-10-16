"""端到端最後一段：用真 ClickHouse 組 FastAPI 應用，以 admin／行銷兩種身分呼叫 /v1/analytics/events，結果要與直接執行編譯器 SQL 一致；審計有寫。由 e2e_clickhouse 呼叫。"""
from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from ..auth.api import AuthService, load_cfg
from ..auth.session import MemoryStore as SessionMemory
from ..auth.session import SessionManager
from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog, compile_query
from ..semantic.compiler.funnel import compile_funnel, parse_funnel, with_rates
from ..semantic.compiler.golden import FUNNELS, PATHS, PROJECT, QUERIES, RETENTIONS, USERS, run_sql
from ..semantic.compiler.path import compile_path, parse_path, sankey
from ..semantic.compiler.retention import compile_retention, parse_retention
from ..semantic.compiler.users import compile_estimate, compile_identities, compile_list, compile_profile, compile_timeline, identity_graph, parse_list, parse_users
from .app import create_app
from .store import MemoryStore


def api_stage(ch: Any) -> None:
    cfg = load_cfg(); store = MemoryStore()
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    app = create_app(AuthService(cfg, {}, sm, None), store, ch)
    c = TestClient(app, base_url="https://dmp.internal")
    cat = Catalog()
    checked = 0; checked_users = 0
    for role, user in (("admin", "adm"), ("marketing", "mkt")):
        s = sm.create(user_id=f"entra:{user}", username=user, name=user, email="", provider="entra", roles=[role], groups=[], amr=["pwd", "mfa"], auth_time=int(time.time()))
        c.cookies.set("dmp_sid", s.sid); c.cookies.set("dmp_csrf", s.csrf); c.headers["X-CSRF-Token"] = s.csrf
        for name, raw, _ in QUERIES[:8]:                      # 前 8 條黃金查詢（走 MV 與明細都有）
            q = {**raw, "project": PROJECT}
            r = c.post("/v1/analytics/events", json=q)
            assert r.status_code == 200, (name, role, r.text[:300])
            body = r.json()
            expect = run_sql(ch, (comp := compile_query(q, decision=authorize(Subject(f"entra:{user}", [role]), "use", "events"), cat=cat)).sql, comp.settings)
            assert body["rows"] == expect, (name, role, body["rows"][:2], expect[:2])
            assert body["cost"]["layer"] == comp.layer and body["columns"] == comp.columns
            checked += 1
        for name, raw, _ in FUNNELS[:4]:
            fq = parse_funnel(q := {**raw, "project": PROJECT}, cat); cf = compile_funnel(fq, cat, authorize(Subject(f"entra:{user}", [role]), "use", "funnel"))
            r = c.post("/v1/analytics/funnel", json=q); assert r.status_code == 200, (name, r.text[:200])
            assert r.json()["rows"] == with_rates(run_sql(ch, cf.sql, cf.settings), fq.steps), name
            checked += 1
        for name, raw, _ in PATHS[:3]:                        # 路徑：API 預設抽樣 0.1，這裡明確 sample=null 與直接 SQL 比
            pq = parse_path(q := {**raw, "project": PROJECT, "sample": None}, cat); cp = compile_path(pq, cat, authorize(Subject(f"entra:{user}", [role]), "use", "path"))
            r = c.post("/v1/analytics/path", json=q); assert r.status_code == 200, (name, r.text[:200])
            assert r.json()["paths"] == sankey(run_sql(ch, cp.sql, cp.settings), pq)["paths"] and r.json()["sampled"] is None, name
            checked += 1
        for name, raw, _ in RETENTIONS[:3]:
            rq = parse_retention(q := {**raw, "project": PROJECT}, cat); cr = compile_retention(rq, cat, authorize(Subject(f"entra:{user}", [role]), "use", "events"))
            r = c.post("/v1/analytics/retention", json=q); assert r.status_code == 200, (name, r.text[:200])
            assert [{k: v for k, v in x.items() if k != "complete"} for x in r.json()["rows"]] == run_sql(ch, cr.sql, cr.settings), name
            checked += 1
        est = c.post("/v1/semantic/compile", json={**QUERIES[0][1], "project": PROJECT}).json()
        assert est["estimated_scan_rows"] is not None and est["sql"]
        for name, raw, _ in USERS[:8]:                        # 用戶列表：估數與第一頁 == 直接 SQL（one_id 依角色遮罩）
            d = authorize(Subject(f"entra:{user}", [role]), "use", "users"); uq = parse_users(q := {**raw, "project": PROJECT}, cat)
            ce = compile_estimate(uq, cat, d); cl = compile_list(uq, parse_list({"page_size": 20}, cat), cat, d)
            r = c.post("/v1/users/list", json={**q, "page_size": 20}); assert r.status_code == 200, (name, r.text[:200])
            body = r.json(); direct = run_sql(ch, cl.sql, cl.settings)
            assert body["total"] == run_sql(ch, ce.sql, ce.settings)[0]["users"] and body["rows"] == [{k: v for k, v in x.items() if k not in ("_s", "_u")} for x in direct], name
            assert (body["next_cursor"] is not None) == (len(direct) == 20) and (("one_id" not in body["masked"]) if role == "admin" else ("one_id" in body["masked"])), name   # admin：one_id 明文，但 L2 分數仍區間
            checked_users += 1
    # 細查：數據分析（one_id 遮罩）看畫像／序列／身分圖 == 直接 SQL；範圍外 404；審計有 reason 無明文 one_id
    s = sm.create(user_id="entra:ana", username="ana", name="ana", email="", provider="entra", roles=["data_analyst"], groups=[], amr=["pwd", "mfa"], auth_time=int(time.time()))
    c.cookies.set("dmp_sid", s.sid); c.cookies.set("dmp_csrf", s.csrf); c.headers["X-CSRF-Token"] = s.csrf
    oid = run_sql(ch, "SELECT one_id FROM dmp.id_edges FINAL WHERE source LIKE 'golden.%' ORDER BY one_id LIMIT 1", {})[0]["one_id"]
    d = authorize(Subject("entra:ana", ["data_analyst"]), "use", "profile", {"reason": "e2e"})
    pr = c.get(f"/v1/profiles/{oid}", params={"reason": "e2e 客訴調查", "project": PROJECT, "now": "2026-10-16"}); assert pr.status_code == 200, pr.text[:200]
    cp = compile_profile(oid, cat, d); direct = run_sql(ch, cp.sql, cp.settings)[0]
    assert pr.json()["one_id"] == direct["one_id"] and pr.json()["one_id"].endswith("****") and pr.json()["props"]["tier"] == direct["tier"] and pr.json()["summary"]["total"] > 0, pr.json()["summary"]
    tl = c.get(f"/v1/profiles/{oid}/timeline", params={"reason": "e2e 客訴調查", "project": PROJECT, "from": "2026-09-20", "to": "2026-10-16", "limit": 30, "now": "2026-10-16"}).json()
    from datetime import date as _d
    ct = compile_timeline(oid, PROJECT, _d(2026, 9, 20), _d(2026, 10, 16), cat, d, limit=30, now=_d(2026, 10, 16)); dt = run_sql(ch, ct.sql, ct.settings)
    assert [x["event_id"] for x in tl["rows"]] == [x["event_id"] for x in dt] and all("cn" in x and isinstance(x["props"], dict) for x in tl["rows"]), len(tl["rows"])
    ig = c.get(f"/v1/profiles/{oid}/identities", params={"reason": "e2e 客訴調查"}).json()
    ci = compile_identities(oid); assert ig == identity_graph(run_sql(ch, ci.sql, ci.settings), d) and ig["edges"], ig
    assert c.get("/v1/profiles/no-such-user", params={"reason": "e2e 客訴調查"}).status_code == 404
    pv = [x for x in store.audit_tail(300) if x["event"] == "profile.viewed"]; assert pv and pv[-1]["reason"] == "e2e 客訴調查" and oid not in str(pv[-1])
    ev = [x for x in store.audit_tail(400) if x["event"] == "query.executed"]
    assert len(ev) == checked and any(x["masked"] for x in ev if x["user"] == "entra:mkt")   # 行銷的金額查詢有遮罩紀錄
    ul = [x for x in store.audit_tail(400) if x["event"] == "users.listed"]
    assert len(ul) == checked_users and any(x["masked"] for x in ul if x["user"] == "entra:mkt")
    print(f"api stage OK: {checked} queries via /v1/analytics/events+funnel+retention+path + {checked_users} via /v1/users/list == direct SQL (admin + marketing), profile/timeline/identities (data_analyst), audit query.executed={len(ev)} users.listed={len(ul)}")
