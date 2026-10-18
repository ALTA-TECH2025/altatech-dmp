"""端到端（真 ClickHouse）：黃金資料集上建分群 → 計算 → 成員數 == 用戶列表估數 == oracle；版本保留；seg.<id> 在事件分析取最新版本；例行 tick；結果保存分群；徹底刪除清成員。由 e2e_clickhouse 在 api_stage 之後呼叫。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ..api.app import create_app
from ..api.store import MemoryStore
from ..auth.api import AuthService, load_cfg
from ..auth.session import MemoryStore as SessionMemory
from ..auth.session import SessionManager
from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog
from ..semantic.compiler.golden import FUNNELS, PROJECT, USERS, Oracle, T, build, decision_of, run_sql
from ..semantic.compiler.users import parse_users
from .engine import SegmentService


def segments_stage(ch: Any) -> None:
    cfg = load_cfg(); store = MemoryStore(); cat = Catalog()
    clock = {"now": datetime(2026, 10, 16, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))}
    svc = SegmentService(store, ch, cat, now=lambda: clock["now"])
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    app = create_app(AuthService(cfg, {}, sm, None), store, ch, svc)
    c = TestClient(app, base_url="https://dmp.internal")
    def login(roles: list[str], user: str) -> None:
        s = sm.create(user_id=f"entra:{user}", username=user, name=user, email="", provider="entra", roles=roles, groups=[], amr=["pwd", "mfa"], auth_time=int(time.time()))
        c.cookies.set("dmp_sid", s.sid); c.cookies.set("dmp_csrf", s.csrf); c.headers["X-CSRF-Token"] = s.csrf
    ds = build(); oracle = Oracle(ds.events, ds.users, ds.segments)
    expect = {name[:3]: oracle.run_users(parse_users({**raw, "project": PROJECT}, cat), decision_of(spec)) for name, raw, spec in USERS}
    # 1. 規則分群（U04：做過成交 ≥ 3 次）：預覽 == oracle；計算 → 成員數 == oracle；成員列表走完 == 同一集合
    login(["data_analyst"], "ana")
    u04 = next(raw for name, raw, _ in USERS if name.startswith("U04"))
    sg = c.post("/v1/segments", json={"name": "e2e 成交 ≥ 3", "rule": {**u04, "project": PROJECT}, "update": {"mode": "routine", "freq": "daily", "earliest": "06:00", "keep": 2}}).json()
    assert sg["status"] == "active", sg
    pv = c.post(f"/v1/segments/{sg['id']}/preview").json(); assert pv["users"] == len(expect["U04"]) and sum(x["users"] for x in pv["profile"]["tier"]) == len(expect["U04"]), pv
    v1 = c.post(f"/v1/segments/{sg['id']}/compute").json(); assert v1["status"] == "success" and v1["users"] == len(expect["U04"]), v1
    assert run_sql(ch, f"SELECT count() AS n FROM dmp.segment_members WHERE segment_id = '{sg['id']}' AND version = 1", {})[0]["n"] == len(expect["U04"])
    seen: list[str] = []; cur = None
    while True:
        m = c.get(f"/v1/segments/{sg['id']}/versions/latest/members", params={"page_size": 60, **({"cursor": cur} if cur else {})}).json()
        seen += [r["one_id"] for r in m["rows"]]; cur = m["next_cursor"]
        if not cur: break
    from ..semantic.compiler.policy import mask_value
    assert set(seen) == {mask_value("mask_tail(4)", o) for o in expect["U04"]} and len(seen) == len(expect["U04"]) and m["total"] == len(expect["U04"]), (len(seen), len(expect["U04"]))
    # 2. 事件分析用 seg.<id>：取最新版本；改規則（U01）重算 → 版本 2，seg 過濾人數跟著變；keep=2 → 第三版後只剩 2、3
    def seg_users(sid: str) -> int:
        r = c.post("/v1/analytics/events", json={"project": PROJECT, "metrics": [{"event": None, "agg": "users"}], "segment": sid, "time": {**T, "grain": "total"}}); assert r.status_code == 200, r.text[:200]
        return int(r.json()["rows"][0]["m1"])
    assert seg_users(sg["id"]) <= len(expect["U04"]) and seg_users(sg["id"]) > 0
    u01 = next(raw for name, raw, _ in USERS if name.startswith("U01"))
    c.put(f"/v1/segments/{sg['id']}", json={"rule": {**u01, "project": PROJECT}})
    v2 = c.post(f"/v1/segments/{sg['id']}/compute").json(); assert v2["version"] == 2 and v2["users"] == len(expect["U01"]), v2
    active = {r["one_id"] for r in ds.events if r["project"] == PROJECT and "2026-10-01" <= r["event_date"].isoformat() <= "2026-10-16"}
    assert seg_users(sg["id"]) == len(expect["U01"] & active), (seg_users(sg["id"]), len(expect["U01"] & active))
    v3 = c.post(f"/v1/segments/{sg['id']}/compute", json={"basetime": "2026-10-15"}).json(); assert v3["version"] == 3
    vers = run_sql(ch, f"SELECT version, count() AS n FROM dmp.segment_members WHERE segment_id = '{sg['id']}' GROUP BY version ORDER BY version", {})
    assert [v["version"] for v in vers] == [2, 3] and vers[1]["n"] == len(expect["U01"]), vers
    assert [v["version"] for v in c.get(f"/v1/segments/{sg['id']}/versions").json()] == [3, 2]
    # 3. 例行 tick（10-17 06:00）→ 版本 4，basetime 10-17；同日不重算
    login(["admin"], "adm")
    t = c.post("/v1/segments/tick", params={"now": "2026-10-17T06:00:00+08:00"}).json()["computed"]; assert len(t) == 1 and t[0]["version"] == 4 and t[0]["basetime"] == "2026-10-17", t
    assert c.post("/v1/segments/tick", params={"now": "2026-10-17T07:00:00+08:00"}).json()["computed"] == []
    # 4. 結果保存分群（U08：漏斗第 2 步流失、桶 10-12）→ 成員 == oracle；行銷建立需審批
    login(["marketing"], "mkt")
    u08 = next(raw for name, raw, _ in USERS if name.startswith("U08"))
    rs = c.post("/v1/segments", json={"name": "e2e 漏斗流失", "kind": "result", "rule": {**u08, "project": PROJECT}}).json(); assert rs["status"] == "pending_approval", rs
    login(["digital_ops"], "ops"); assert c.post(f"/v1/segments/{rs['id']}/approve", json={"note": "ok"}).json()["status"] == "active"
    login(["data_analyst"], "ana")
    vr = c.post(f"/v1/segments/{rs['id']}/compute").json(); assert vr["users"] == len(expect["U08"]) and vr["users"] > 0, (vr, len(expect["U08"]))
    assert "event:app_login_success" in rs["lineage"]["up"] and FUNNELS[5][1]["steps"][0]["event"] == "app_login_success"
    # 4b. 導入名單：30 個黃金用戶 + 5 個不存在 + 2 個格式錯 → matched 30；kind=import 分群計算 == 30 且 == oracle；帶值導入 → import 標籤分布 == 導入值
    known = sorted(ds.users)[:30]
    im = c.post("/v1/segments/imports", json={"project": PROJECT, "id_type": "one_id", "values": known + [f"v1_nobody{i:03d}" for i in range(5)] + ["bad id!", ""]}).json()
    assert (im["total"], im["distinct"], im["matched"], im["unmatched"], im["invalid"]) == (37, 35, 30, 5, 2), im
    isg = c.post("/v1/segments", json={"name": "e2e 導入名單", "kind": "import", "rule": {"project": PROJECT, "source": {"type": "import", "import_id": im["id"]}}}).json(); assert isg["status"] == "active", isg
    vi = c.post(f"/v1/segments/{isg['id']}/compute").json(); assert vi["users"] == 30, vi
    oracle.imports[im["id"]] = set(known) | {f"v1_nobody{i:03d}" for i in range(5)}
    assert len(oracle.run_users(parse_users({"project": PROJECT, "source": {"type": "import", "import_id": im["id"]}}, cat), decision_of("data_analyst"))) == 30
    ti = svc.imports.create_pairs(PROJECT, [(o, "A" if i % 3 else "B") for i, o in enumerate(known)], "e2e 標籤快照", Subject("entra:ana", ["data_analyst"]), 30, 0)
    tg = c.post("/v1/tags", json={"name": "e2e 導入標籤", "key": "e2e_import_tag", "kind": "import", "import_id": ti["id"]}); assert tg.status_code == 201, tg.text
    tv = c.post("/v1/tags/e2e_import_tag/compute", json={}).json(); assert tv["status"] == "success" and {d["value"]: d["users"] for d in tv["distribution"]} == {"A": 20, "B": 10}, tv
    # 5. 回收站 → 徹底刪除清成員；審計不含 one_id
    c.post(f"/v1/segments/{rs['id']}/recycle"); login(["admin"], "adm"); assert c.delete(f"/v1/segments/{rs['id']}/purge").status_code == 204
    assert run_sql(ch, f"SELECT count() AS n FROM dmp.segment_members WHERE segment_id = '{rs['id']}'", {})[0]["n"] == 0
    assert all("v1_" not in str(x) for x in store.audit_tail(300) if x["event"].startswith("segment."))
    d = authorize(Subject("entra:ana", ["data_analyst"]), "use", "users"); assert d.allowed
    print(f"segments stage OK: rule segment users={v1['users']} (== oracle U04), rule change → v2 users={v2['users']} (== oracle U01), keep=2 pruned, seg.<id> latest version in events, routine tick v4, result segment users={vr['users']} (== oracle U08), import segment users={vi['users']}/tag A20 B10, purge cleared members")
