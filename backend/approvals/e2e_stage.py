"""端到端（真 ClickHouse）：數據分析申請匯出黃金 U01 條件 → 數位經營核可 → 本人產檔 → CSV 列數 == oracle、one_id 遮罩、浮水印；非本人 403。由 e2e_clickhouse 呼叫。"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ..api.app import create_app
from ..api.store import MemoryStore
from ..auth.api import AuthService, load_cfg
from ..auth.session import MemoryStore as SessionMemory
from ..auth.session import SessionManager
from ..semantic.compiler import Catalog
from ..semantic.compiler.golden import PROJECT, USERS, Oracle, build, decision_of
from ..semantic.compiler.policy import mask_value
from ..semantic.compiler.users import parse_users


def exports_stage(ch: Any) -> None:
    cfg = load_cfg(); store = MemoryStore(); cat = Catalog()
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    app = create_app(AuthService(cfg, {}, sm, None), store, ch, export_dir=Path(tempfile.mkdtemp(prefix="dmp-exp-")))
    c = TestClient(app, base_url="https://dmp.internal")
    def login(roles: list[str], user: str) -> None:
        s = sm.create(user_id=f"entra:{user}", username=user, name=user, email="", provider="entra", roles=roles, groups=[], amr=["pwd", "mfa"], auth_time=int(time.time()))
        c.cookies.set("dmp_sid", s.sid); c.cookies.set("dmp_csrf", s.csrf); c.headers["X-CSRF-Token"] = s.csrf
    ds = build(); oracle = Oracle(ds.events, ds.users, ds.segments)
    name, raw, spec = next(x for x in USERS if x[0].startswith("U01"))
    expect = oracle.run_users(parse_users({**raw, "project": PROJECT}, cat), decision_of(spec))
    login(["data_analyst"], "ana")
    e = c.post("/v1/exports", json={"kind": "users", "rule": {**raw, "project": PROJECT}, "purpose": "e2e 匯出驗證", "columns": ["tier", "churn_score", "aum"]}).json()
    assert e["status"] == "pending_approval" and e["rows_estimate"] == len(expect), e
    login(["digital_ops"], "ops"); assert c.post(f"/v1/approvals/{e['approval_id']}/decide", json={"approve": True}).json()["status"] == "approved"
    login(["data_analyst"], "ana")
    g = c.post(f"/v1/exports/{e['id']}/run").json(); assert g["status"] == "generated" and g["rows"] == len(expect) and g["masked"] == {"one_id": "mask_tail(4)", "aum": "bucket"}, g
    text = c.get(f"/v1/exports/{e['id']}/download").text
    lines = text.lstrip("\ufeff").splitlines()
    assert lines[0].startswith("# AltaTech DMP 匯出") and lines[1] == "one_id,tier,churn_score,aum,_watermark" and len(lines) == len(expect) + 2, (lines[:2], len(lines))
    ids = {ln.split(",")[0] for ln in lines[2:]}; assert ids == {mask_value("mask_tail(4)", o) for o in expect} and all(ln.endswith(g["watermark_id"]) for ln in lines[2:])
    import re
    assert all(re.fullmatch(r"<\d+|\d+–\d+|≥\d+|", ln.split(",")[3]) for ln in lines[2:]), lines[2]        # 匯出：aum 區間化（masking.yaml export: bucket 用預設區間；數據分析在 UI 看明文）
    login(["digital_ops"], "ops"); assert c.get(f"/v1/exports/{e['id']}/download").status_code == 403
    # 揭露：數據分析申請 one_id（L3）限單一用戶 → admin 核可 → 該用戶畫像 one_id 明文、別人仍遮罩、匯出仍遮罩
    oid = sorted(expect)[0]; other = sorted(expect)[1]
    login(["data_analyst"], "ana"); rv = c.post("/v1/reveals", json={"field": "one_id", "one_id": oid, "reason": "e2e 揭露驗證"}).json()
    login(["admin"], "adm"); c.post(f"/v1/approvals/{rv['approval_id']}/decide", json={})
    login(["data_analyst"], "ana")
    assert c.get(f"/v1/profiles/{oid}", params={"reason": "e2e 揭露", "project": PROJECT, "now": "2026-10-16"}).json()["one_id"] == oid
    assert c.get(f"/v1/profiles/{other}", params={"reason": "e2e 揭露", "project": PROJECT, "now": "2026-10-16"}).json()["one_id"] == mask_value("mask_tail(4)", other)
    assert c.get("/v1/reveals").json()[0]["views"] >= 1
    print(f"exports stage OK: request→approve→generate→download rows={g['rows']} (== oracle U01) masked={g['masked']} watermark={g['watermark_id']}; reveal one_id scoped to one user ok")
