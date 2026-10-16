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
from ..semantic.compiler.golden import PROJECT, QUERIES, run_sql
from .app import create_app
from .store import MemoryStore


def api_stage(ch: Any) -> None:
    cfg = load_cfg(); store = MemoryStore()
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    app = create_app(AuthService(cfg, {}, sm, None), store, ch)
    c = TestClient(app, base_url="https://dmp.internal")
    cat = Catalog()
    checked = 0
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
        est = c.post("/v1/semantic/compile", json={**QUERIES[0][1], "project": PROJECT}).json()
        assert est["estimated_scan_rows"] is not None and est["sql"]
    ev = [x for x in store.audit_tail(200) if x["event"] == "query.executed"]
    assert len(ev) == checked and any(x["masked"] for x in ev if x["user"] == "entra:mkt")   # 行銷的金額查詢有遮罩紀錄
    print(f"api stage OK: {checked} queries via /v1/analytics/events == direct SQL (admin + marketing), audit query.executed={len(ev)}")
