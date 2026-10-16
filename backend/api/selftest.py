"""API 自測（不連網、不連 ClickHouse）：登入 session → /v1/iam/me → 角色 CRUD 與權限檢查 → 指派 → 行級模擬 → /v1/semantic/compile（遮罩）→ /v1/analytics/events（假 ClickHouse）→ 審計。

用法：python3 -m backend.api
"""
from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from ..auth.api import AuthService, load_cfg
from ..auth.session import MemoryStore as SessionMemory
from ..auth.session import SessionManager
from ..security.policy import RBAC
from .app import create_app
from .store import MemoryStore


class FakeClickHouse:
    """回固定列；記錄 SQL 供斷言。EXPLAIN ESTIMATE 回兩列。"""
    def __init__(self) -> None:
        self.sqls: list[str] = []

    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str:
        self.sqls.append(sql)
        if sql.startswith("EXPLAIN ESTIMATE"):
            return "dmp\tevents\t2\t16384\t2\n"
        assert settings and settings.get("max_execution_time") == 60, settings
        if "bitmapCardinality(" in sql:
            return '{"users":123}\n'
        if "bitmapContains(" in sql:
            oid = "v1_abc****" if "repeat(" in sql else "v1_abcdef1234"
            return '{"one_id":"' + oid + '","branch_id":"B01","lifecycle":"新客","tier":"P2","aum_bucket":"<100萬","churn_score":"40–70","last_active_at":"2026-10-15 12:00:00.000","_s":0,"_u":1}\n'
        if "FROM dmp.users AS e FINAL WHERE e.one_id = " in sql:
            if "'nope'" in sql: return ""
            return '{"one_id":"' + ("v1_abc****" if "repeat(" in sql else "v1_abcdef1234") + '","tier":"P2","aum":1500000,"churn_score":55.5,"branch_id":"B01"}\n'
        if "GROUP BY event ORDER BY n DESC" in sql:
            return '{"event":"bond_deal","n":7,"first":"2026-10-01 01:00:00.000","last":"2026-10-15 09:00:00.000"}\n{"event":"app_login_success","n":3,"first":"2026-10-02 01:00:00.000","last":"2026-10-14 09:00:00.000"}\n'
        if "FROM dmp.segment_members WHERE one_id" in sql:
            return '{"segment_id":"vip"}\n'
        if "ORDER BY e.time DESC" in sql:
            return '{"time":"2026-10-15 09:00:00.000","event":"bond_deal","platform":"iOS","channel":"app","symbol":"2330","side":"B","amount":"100000–500000","entity_type":"account","entity_id":"A1","session_id":"s1","event_id":"e1","props":{"market":"TSE","mobile":"0912","$identity_mobile_hash":"x"}}\n'
        if "FROM dmp.id_edges" in sql:
            return '{"id_type_a":"login_id","id_a":"L00001","id_type_b":"device_id","id_b":"D00001a","source":"ga360.login","evidence_event_id":"ev1","first_seen":"2026-09-01 00:00:00.000","last_seen":"2026-10-15 12:00:00.000","seen_count":3,"confidence":1.0,"status":"active"}\n'
        if "ARRAY JOIN range(" in sql and "cohort" in sql:
            return '{"cohort":"2026-10-01","period":1,"users":300,"retained":120,"rate":0.4,"suppressed":false}\n{"cohort":"2026-10-01","period":2,"users":300,"retained":90,"rate":0.3,"suppressed":false}\n{"cohort":"2026-10-15","period":1,"users":5,"retained":null,"rate":null,"suppressed":true}\n'
        if "windowFunnel" in sql:
            return '{"step":1,"users":200,"_k":200,"suppressed":false}\n{"step":2,"users":80,"_k":80,"suppressed":false}\n{"step":3,"users":null,"_k":10,"suppressed":true}\n'
        return '{"bucket":"2026-10-01","m1":120,"m2":35,"_k":35,"suppressed":false}\n{"bucket":"2026-10-02","m1":5,"m2":4,"_k":4,"suppressed":true}\n'


def login(c: TestClient, sm: SessionManager, roles: list[str], user: str) -> None:
    s = sm.create(user_id=f"entra:{user}", username=user, name=user, email=f"{user}@corp.internal", provider="entra", roles=roles, groups=[], amr=["pwd", "mfa"], auth_time=int(time.time()))
    c.cookies.set("dmp_sid", s.sid); c.cookies.set("dmp_csrf", s.csrf)
    c.headers["X-CSRF-Token"] = s.csrf


def main() -> None:
    cfg = load_cfg(); store = MemoryStore(); ch = FakeClickHouse()
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    app = create_app(AuthService(cfg, {}, sm, None), store, ch)
    c = TestClient(app, base_url="https://dmp.internal")
    assert c.get("/healthz").json()["clickhouse"] is True
    assert c.get("/v1/iam/roles").status_code == 401
    # 1. 行銷：看得到角色清單（iam.view 無 → 403）；數位經營可看不可改
    login(c, sm, ["marketing"], "mkt")
    assert c.get("/v1/iam/roles").status_code == 403
    login(c, sm, ["digital_ops"], "ops")
    roles = c.get("/v1/iam/roles").json()
    assert {r["key"] for r in roles} >= {"admin", "marketing", "data_analyst", "digital_ops", "it_admin", "svc_insight"} and all(r["builtin"] for r in roles)
    assert c.post("/v1/iam/roles", json={"name": "x", "grants": {}}).status_code == 403
    # 2. admin：建自訂角色（蘊含規則、不可給 iam.admin）、更新、複製、指派、改派刪除；每步寫審計
    login(c, sm, ["admin"], "adm")
    r = c.post("/v1/iam/roles", json={"name": "外包分析", "description": "專案限定", "scope": "project", "lookback_days": 180, "grants": {"events": ["export"], "overview": ["view"]}})
    assert r.status_code == 201, r.text
    key = r.json()["key"]; assert key.startswith("custom_") and r.json()["grants"]["events"] == ["view", "use", "export"], r.json()
    assert c.post("/v1/iam/roles", json={"name": "bad", "grants": {"iam": ["admin"]}}).status_code == 400
    assert c.post("/v1/iam/roles", json={"name": "bad2", "grants": {"nope": ["view"]}}).status_code == 400
    assert c.put("/v1/iam/roles/marketing", json={"name": "x"}).status_code == 403                      # 內建不可改
    assert c.put(f"/v1/iam/roles/{key}", json={"grants": {"events": ["view"]}}).json()["grants"] == {"events": ["view"]}
    cp = c.post("/v1/iam/roles/data_analyst/copy"); assert cp.status_code == 201 and cp.json()["grants"]["events"] == ["view", "use", "export"]
    assert key in RBAC["roles"]                                                                           # 已註冊進 Policy Engine
    a = c.post("/v1/iam/assignments", json={"user_id": "local:vendor-a", "roles": [key], "scope": {"projects": ["sinopac_demo"], "lookback_days": 180}, "expires_at": "2026-10-31", "reason": "外包專案"})
    assert a.status_code == 201, a.text
    assert c.post("/v1/iam/assignments", json={"user_id": "x", "roles": ["admin"], "reason": "r"}).status_code == 400   # admin 不可本地指派
    assert c.delete(f"/v1/iam/roles/{key}").status_code == 409                                                    # 仍有指派
    assert c.delete(f"/v1/iam/roles/{key}?reassign_to=data_analyst").status_code == 204
    assert c.get("/v1/iam/assignments").json()[0]["roles"] == ["data_analyst"] and key not in RBAC["roles"]
    assert c.delete("/v1/iam/roles/admin").status_code == 403
    # 3. 行級模擬：自訂角色（回溯 180 天）→ WHERE 含 event_date；行銷 → amount 遮罩
    sim = c.post("/v1/iam/scope/simulate", json={"role": "marketing", "scope": {"lookback_days": 180}}).json()
    assert "amount" in sim["masked_fields"] and any("event_date >= today()-180" in w for w in sim["where"]) and "multiIf" in sim["sql"] and sim["k_anonymity"] == 20, sim
    assert c.post("/v1/iam/scope/simulate", json={"role": "svc_insight"}).json()["allowed"] is True
    # 4. 編譯 dry-run：行銷看金額只有區間；未登記事件 400；svc_insight 不能細查
    login(c, sm, ["marketing"], "mkt")
    q = {"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount"}, {"event": "bond_deal", "agg": "users"}], "group_by": ["platform"], "time": {"from": "2026-10-01", "to": "2026-10-16"}}
    comp = c.post("/v1/semantic/compile", json=q).json()
    assert comp["layer"] == "L0" and comp["masked"] == {"m1": "bucket([0,1e5,5e5,1e6,5e6])"} and comp["estimated_scan_rows"] == 16384 and "dmp.events" in comp["lineage"], comp
    assert c.post("/v1/semantic/compile", json={"metrics": [{"event": "nope", "agg": "total"}], "time": q["time"]}).status_code == 400
    assert c.post("/v1/semantic/compile", json={"metrics": [{"event": None, "agg": "total"}], "group_by": ["user.agent_id"], "time": q["time"]}).status_code == 400
    # 5. 執行：假 ClickHouse 回兩列（一列被 K 匿名抑制）；chart_spec；審計 query.executed 帶 sql_hash 與遮罩欄
    res = c.post("/v1/analytics/events", json=q)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["rows"]) == 2 and body["rows"][1]["suppressed"] is True and body["chart_spec"]["type"] == "line" and body["cost"]["layer"] == "L0"
    assert ch.sqls[-1].endswith("FORMAT JSONEachRow") and "SAMPLE" not in ch.sqls[-1]
    ev = [x for x in store.audit_tail() if x["event"] == "query.executed"]
    assert ev and ev[-1]["user"] == "entra:mkt" and ev[-1]["masked"] == ["m1"] and len(ev[-1]["sql_hash"]) == 16
    # 5b. 漏斗：轉換率、抑制列、warnings；目錄依身分過濾；行銷沒有 funnel.use？（有：view/use）
    fq = {"steps": [{"event": "hq_stockdetail_view"}, {"event": "bond_order"}, {"event": "bond_deal", "alias": "成交"}], "window": {"value": 7, "unit": "day"}, "time": q["time"]}
    fr = c.post("/v1/analytics/funnel", json=fq); assert fr.status_code == 200, fr.text
    rows = fr.json()["rows"]
    assert [r["alias"] for r in rows] == ["hq_stockdetail_view", "bond_order", "成交"] and rows[1]["rate_prev"] == 0.4 and rows[1]["rate_first"] == 0.4 and rows[2]["users"] is None and rows[2]["rate_prev"] is None and rows[2]["suppressed"] is True, rows
    assert "windowFunnel(604800)" in ch.sqls[-1] and fr.json()["chart_spec"]["type"] == "funnel"
    assert c.post("/v1/analytics/funnel", json={**fq, "window": {"value": 91, "unit": "day"}}).status_code == 400
    assert c.post("/v1/analytics/funnel", json={**fq, "metric": "times"}).status_code == 400
    rq = {"initial": {"event": "app_login_success"}, "return": {"event": "bond_deal"}, "model": "interval", "periods": 2, "time": {**q["time"], "now": "2026-10-16"}}
    rr = c.post("/v1/analytics/retention", json=rq); assert rr.status_code == 200, rr.text
    rrows = rr.json()["rows"]; assert rrows[0]["complete"] is True and rrows[2]["complete"] is False and rrows[2]["suppressed"] is True and rr.json()["chart_spec"]["type"] == "retention_matrix", rrows
    assert c.post("/v1/analytics/retention", json={**rq, "periods": 0}).status_code == 400
    catl = c.get("/v1/semantic/catalog").json()
    assert any(e["name"] == "bond_deal" for e in catl["events"]) and not any(u["key"] == "agent_id" for u in catl["user_props"]) and catl["funnel"]["max_window_days"] == 90 and "between" in catl["ops"]["number"]
    # 5c. 用戶列表：行銷估數／列表（one_id、流失分遮罩）；資訊管理 403；分頁參數檢查；細查需理由（行銷 403、數據分析遮罩、不存在 404、props 去 L4 鍵、身分圖遮罩）
    uq = {"filters": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}], "behaviors": [{"event": "bond_deal", "count": {"op": "gte", "value": 3}, "time": q["time"]}]}
    est = c.post("/v1/users/estimate", json=uq); assert est.status_code == 200 and est.json()["users"] == 123 and "bitmapCardinality(bm)" in est.json()["sql"], est.text
    lst = c.post("/v1/users/list", json={**uq, "page_size": 50}); assert lst.status_code == 200, lst.text
    lb = lst.json(); assert lb["rows"][0]["one_id"] == "v1_abc****" and "_s" not in lb["rows"][0] and lb["masked"] == {"one_id": "mask_tail(4)", "churn_score": "bucket([0,40,70,100])"} and lb["next_cursor"] is None and lb["total"] == 123 and lb["columns"][:2] == ["one_id", "branch_id"], lb
    assert c.post("/v1/users/list", json={**uq, "page_size": 1000}).status_code == 400
    assert c.post("/v1/users/list", json={**uq, "columns": ["customer_code"]}).status_code == 400
    assert c.post("/v1/users/estimate", json={"filters": [{"field": "platform", "op": "eq", "value": "iOS"}]}).status_code == 400
    from ..semantic.compiler.users import encode_cursor
    assert c.post("/v1/users/list", json={**uq, "cursor": encode_cursor({"v": 0, "u": 1, "n": 100000, "t": 123})}).status_code == 400
    assert c.get("/v1/profiles/v1_abcdef1234?reason=客訴調查").status_code == 403          # 行銷不可細查
    login(c, sm, ["it_admin"], "it"); assert c.post("/v1/users/estimate", json=uq).status_code == 403
    login(c, sm, ["data_analyst"], "ana")
    assert c.get("/v1/profiles/v1_abcdef1234").status_code == 422 and c.get("/v1/profiles/v1_abcdef1234?reason=x").status_code == 400
    pr = c.get("/v1/profiles/v1_abcdef1234?reason=客訴單 C-1001&now=2026-10-16"); assert pr.status_code == 200, pr.text
    pb = pr.json(); assert pb["one_id"] == "v1_abc****" and pb["props"]["aum"] == 1500000 and pb["segments"] == ["vip"] and pb["summary"]["total"] == 10 and pb["summary"]["events"][0]["cn"] and pb["masked"] == {"one_id": "mask_tail(4)"}, pb
    assert c.get("/v1/profiles/nope?reason=客訴單 C-1001").status_code == 404
    tl = c.get("/v1/profiles/v1_abcdef1234/timeline?reason=客訴單 C-1001&from=2026-10-01&to=2026-10-16&events=bond_deal&limit=50"); assert tl.status_code == 200, tl.text
    tb = tl.json(); assert tb["rows"][0]["props"] == {"market": "TSE"} and tb["rows"][0]["cn"] and tb["next_cursor"] is None and "e.event IN ('bond_deal')" in tb["sql"], tb
    ig = c.get("/v1/profiles/v1_abcdef1234/identities?reason=客訴單 C-1001").json()
    assert [n["value"] for n in ig["nodes"]] == ["L0****", "D00****"] and ig["edges"][0]["source"] == "ga360.login" and ig["masked"] == {"id": "mask_tail(4)"}, ig
    pv = [x for x in store.audit_tail(50) if x["event"] == "profile.viewed"]; assert pv and pv[-1]["reason"] == "客訴單 C-1001" and len(pv[-1]["one_id_hash"]) == 16 and "one_id" not in pv[-1]
    login(c, sm, ["marketing"], "mkt")
    # 6. 審計：行銷只看自己；admin 全看
    mine = c.get("/v1/audit").json(); assert mine and all(x.get("user") == "entra:mkt" for x in mine)
    login(c, sm, ["admin"], "adm"); allrows = c.get("/v1/audit").json(); assert len(allrows) > len(mine)
    # 7. CSRF：非 GET 沒帶 header → 403
    c.headers.pop("X-CSRF-Token"); assert c.post("/v1/semantic/compile", json=q).status_code == 403
    print(f"api self-test ok: iam(roles crud/assignments/simulate) compile+events+funnel+retention+users+profile(fake clickhouse) catalog audit={len(store.audit_tail())} routes={len(app.routes)}")


if __name__ == "__main__":
    main()
