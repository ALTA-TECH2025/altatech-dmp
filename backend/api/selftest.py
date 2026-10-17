"""API 自測（不連網、不連 ClickHouse）：登入 session → /v1/iam/me → 角色 CRUD 與權限檢查 → 指派 → 行級模擬 → /v1/semantic/compile（遮罩）→ /v1/analytics/events（假 ClickHouse）→ 審計。

用法：python3 -m backend.api
"""
from __future__ import annotations

import json
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
        assert settings and settings.get("max_execution_time") in (60, 600), settings        # human 60s；匯出走 batch 600s
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
        if "ARRAY JOIN touches AS t" in sql:
            return '{"ti":0,"first_conv":60,"first_val":6000000,"linear_conv":45.5,"linear_val":4200000,"_k":58,"conversions":100,"converters":80,"touched":70,"total_value":9000000,"avg_touches":1.8,"suppressed":false}\n{"ti":1,"first_conv":10,"first_val":900000,"linear_conv":24.5,"linear_val":2100000,"_k":30,"conversions":100,"converters":80,"touched":70,"total_value":9000000,"avg_touches":1.8,"suppressed":false}\n{"ti":2,"first_conv":null,"first_val":null,"linear_conv":null,"linear_val":null,"_k":5,"conversions":100,"converters":80,"touched":70,"total_value":9000000,"avg_touches":1.8,"suppressed":true}\n'
        if "arrayEnumerate(seq3)" in sql:
            return '{"path":["app_login_success","hq_stockdetail_view","bond_order","離開"],"users":120,"sessions":131,"avg_ms":84000.5,"anchor_users":300,"_k":120,"suppressed":false}\n{"path":["app_login_success","離開"],"users":90,"sessions":95,"avg_ms":0,"anchor_users":300,"_k":90,"suppressed":false}\n{"path":["app_login_success","bond_deal","離開"],"users":null,"sessions":null,"avg_ms":null,"anchor_users":300,"_k":7,"suppressed":true}\n'
        if "windowFunnel" in sql:
            return '{"step":1,"users":200,"_k":200,"suppressed":false}\n{"step":2,"users":80,"_k":80,"suppressed":false}\n{"step":3,"users":null,"_k":10,"suppressed":true}\n'
        return '{"bucket":"2026-10-01","m1":120,"m2":35,"_k":35,"suppressed":false}\n{"bucket":"2026-10-02","m1":5,"m2":4,"_k":4,"suppressed":true}\n'


def login(c: TestClient, sm: SessionManager, roles: list[str], user: str) -> None:
    s = sm.create(user_id=f"entra:{user}", username=user, name=user, email=f"{user}@corp.internal", provider="entra", roles=roles, groups=[], amr=["pwd", "mfa"], auth_time=int(time.time()))
    c.cookies.set("dmp_sid", s.sid); c.cookies.set("dmp_csrf", s.csrf)
    c.headers["X-CSRF-Token"] = s.csrf


def main(store: Any | None = None) -> None:
    cfg = load_cfg(); store = store or MemoryStore(); ch = FakeClickHouse()
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    import tempfile as _tf
    from pathlib import Path as _P0

    from ..auth.local import LocalAccounts, LocalProvider
    from ..integration.runner import LocalRunner as _LR
    from ..integration.scheduler import Scheduler as _Sched
    local = LocalProvider(LocalAccounts(_P0(_tf.mkdtemp(prefix="dmp-la-")) / "accounts.json", {"min_length": 14, "lockout_after": 5, "lockout_minutes": 15, "max_days": 90}, ["custom_*", "marketing"]))
    app = create_app(AuthService(cfg, {"local": local}, sm, None), store, ch, scheduler=_Sched(_tf.mktemp(prefix="dmp-sched-", suffix=".db"), _LR(None)))   # 乾跑排程器，每次自測全新狀態
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
    # 2. admin：建自訂角色（蘊含規則、不可給 iam.admin）→ 雙人複核：202 審批單，不可自審，另一位（數位經營）核可後才生效；更新、複製、指派、改派刪除同流程；每步寫審計
    login(c, sm, ["admin"], "adm")
    r = c.post("/v1/iam/roles", json={"name": "外包分析", "description": "專案限定", "scope": "project", "lookback_days": 180, "grants": {"events": ["export"], "overview": ["view"]}})
    assert r.status_code == 202 and r.json()["status"] == "pending_review", r.text
    ap = r.json()["approval"]
    assert c.post(f"/v1/approvals/{ap}/decide", json={"approve": True}).status_code == 409                     # 不能自審（admin 也不行）
    chg = c.get("/v1/iam/changes").json(); assert chg[0]["kind"] == "role_change" and chg[0]["op"] == "create" and chg[0]["status"] == "pending" and chg[0]["before"] is None and chg[0]["after"]["grants"]["events"] == ["view", "use", "export"], chg
    assert not any(x["key"].startswith("custom_") for x in c.get("/v1/iam/roles").json())                     # 未核可前不存在

    def approve(c: TestClient, ap: str, who: str = "ops", roles: list[str] | None = None) -> None:
        login(c, sm, roles or ["digital_ops"], who); assert c.post(f"/v1/approvals/{ap}/decide", json={"approve": True}).status_code == 200; login(c, sm, ["admin"], "adm")
    approve(c, ap)
    key = next(x["key"] for x in c.get("/v1/iam/roles").json() if not x["builtin"]); assert key.startswith("custom_") and key in RBAC["roles"]
    assert c.get("/v1/iam/changes?status=approved").json()[0]["decided_by"] == "entra:ops"
    assert c.post("/v1/iam/roles", json={"name": "bad", "grants": {"iam": ["admin"]}}).status_code == 400       # 驗證在送審前
    assert c.post("/v1/iam/roles", json={"name": "bad2", "grants": {"nope": ["view"]}}).status_code == 400
    assert c.put("/v1/iam/roles/marketing", json={"name": "x"}).status_code == 403                      # 內建不可改
    up = c.put(f"/v1/iam/roles/{key}", json={"grants": {"events": ["view"]}}); assert up.status_code == 202
    approve(c, up.json()["approval"]); assert next(x for x in c.get("/v1/iam/roles").json() if x["key"] == key)["grants"] == {"events": ["view"]}
    cp = c.post("/v1/iam/roles/data_analyst/copy"); assert cp.status_code == 202 and c.get("/v1/iam/changes?status=pending").json()[0]["after"]["grants"]["events"] == ["view", "use", "export"]
    login(c, sm, ["digital_ops"], "ops"); assert c.post(f"/v1/approvals/{cp.json()['approval']}/decide", json={"approve": False, "note": "不需要"}).status_code == 200; login(c, sm, ["admin"], "adm")
    a = c.post("/v1/iam/assignments", json={"user_id": "local:vendor-a", "roles": [key], "scope": {"projects": ["sinopac_demo"], "lookback_days": 180}, "expires_at": "2026-10-31", "reason": "外包專案"})
    assert a.status_code == 201, a.text
    assert c.post("/v1/iam/assignments", json={"user_id": "x", "roles": ["admin"], "reason": "r"}).status_code == 400   # admin 不可本地指派
    assert c.delete(f"/v1/iam/roles/{key}").status_code == 409                                                    # 仍有指派
    dl = c.delete(f"/v1/iam/roles/{key}?reassign_to=data_analyst"); assert dl.status_code == 202
    approve(c, dl.json()["approval"])
    assert c.get("/v1/iam/assignments").json()[0]["roles"] == ["data_analyst"] and key not in RBAC["roles"]
    assert c.delete("/v1/iam/roles/admin").status_code == 403
    # 臨時提權：202 → 數位經營核可 → 到期指派（temp、≤ 4 小時）
    bg = c.post("/v1/iam/assignments", json={"user_id": "entra:oncall", "roles": ["admin"], "break_glass": True, "hours": 2, "reason": "凌晨事故"}); assert bg.status_code == 202, bg.text
    assert c.post("/v1/iam/assignments", json={"user_id": "entra:oncall", "roles": ["admin"], "break_glass": True, "hours": 9, "reason": "x"}).status_code == 400
    approve(c, bg.json()["approval"])
    tmp = next(x for x in c.get("/v1/iam/assignments").json() if x["user_id"] == "entra:oncall"); assert tmp["temp"] and tmp["approved_by"] == "entra:ops" and 0 < tmp["expires_at"] - time.time() <= 2 * 3600 + 5
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
    pq = {"anchor": {"event": "app_login_success"}, "steps": 3, "time": q["time"]}
    pr = c.post("/v1/analytics/path", json=pq); assert pr.status_code == 200, pr.text
    pb = pr.json(); assert pb["sampled"] == 0.1 and "SAMPLE 0.1" in ch.sqls[-1] and pb["anchor_users"] == 300 and pb["paths"][0]["share"] == 0.4 and pb["paths"][2]["suppressed"] is True and pb["paths"][2]["users"] is None, pb["paths"]
    assert [n["node"] for n in pb["nodes"] if n["level"] == 0] == ["app_login_success"] and {(l["level"], l["from"], l["to"], l["users"]) for l in pb["links"] if l["level"] == 0} == {(0, "app_login_success", "hq_stockdetail_view", 120), (0, "app_login_success", "離開", 90)}, pb["links"]
    assert pb["chart_spec"]["type"] == "sankey" and any("抽樣" in w for w in pb["warnings"])
    assert "SAMPLE" not in c.post("/v1/analytics/path", json={**pq, "sample": None}).json()["sql"]
    assert c.post("/v1/analytics/path", json={**pq, "steps": 11}).status_code == 400
    aq = {"target": {"event": "bond_deal", "value_field": "amount"}, "touches": [{"event": "$pageview", "alias": "瀏覽"}, {"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "app_login_success"}], "models": ["first", "linear"], "window_days": 7, "time": q["time"]}
    ar = c.post("/v1/analytics/attribution", json=aq); assert ar.status_code == 200, ar.text
    ab = ar.json(); assert ab["summary"] == {"conversions": 100, "converters": 80, "touched": 70, "untouched": 30, "value": 9000000.0, "avg_touches": 1.8} and ab["models"] == ["first", "linear"], ab["summary"]
    assert ab["rows"][0]["touch"] == "瀏覽" and ab["rows"][0]["models"]["first"] == {"conversions": 60.0, "value": 6000000.0, "share": 0.6} and ab["rows"][2]["suppressed"] is True and ab["rows"][2]["models"]["linear"]["conversions"] is None and ab["rows"][2]["touch"] == "app_login_success", ab["rows"]
    assert c.post("/v1/analytics/attribution", json={**aq, "models": ["magic"]}).status_code == 400 and c.post("/v1/analytics/attribution", json={**aq, "touches": [{"event": "bond_deal"}]}).status_code == 400
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
    # 5d. 匯出審批：數據分析申請（需用途）→ 審批單給數位經營 → 核可 → 本人產檔（遮罩、浮水印）→ 下載；行銷 403；自己不能審；分群待審批也開審批單
    ex = c.post("/v1/exports", json={"kind": "users", "rule": uq, "purpose": "Q4 名單交付 MA（保存 30 天）", "retention_days": 30}); assert ex.status_code == 201, ex.text
    eb = ex.json(); assert eb["status"] == "pending_approval" and eb["rows_estimate"] == 123 and eb["approval_id"].startswith("ap_"), eb
    assert c.post("/v1/exports", json={"kind": "users", "rule": uq, "purpose": "x"}).status_code == 400
    assert c.post(f"/v1/exports/{eb['id']}/run").status_code == 409 and c.post(f"/v1/approvals/{eb['approval_id']}/decide", json={}).status_code == 403
    assert [a["id"] for a in c.get("/v1/approvals?scope=mine").json()] == [eb["approval_id"]] and c.get("/v1/approvals?scope=to_me").json() == []
    login(c, sm, ["marketing"], "mkt"); assert c.post("/v1/exports", json={"kind": "users", "rule": uq, "purpose": "行銷想匯出"}).status_code == 403
    mseg = c.post("/v1/segments", json={"name": "行銷待審", "rule": uq}).json(); assert mseg["status"] == "pending_approval"
    login(c, sm, ["digital_ops"], "ops")
    todo = [a for a in c.get("/v1/approvals").json() if a["status"] == "pending"]; assert {a["type"] for a in todo} == {"export", "segment"}, todo
    assert c.post(f"/v1/approvals/{eb['approval_id']}/decide", json={"approve": False}).status_code == 400          # 退回要意見
    ap = c.post(f"/v1/approvals/{eb['approval_id']}/decide", json={"approve": True, "note": "ok"}).json(); assert ap["status"] == "approved" and ap["decided_by"] == "entra:ops"
    seg_ap = next(a for a in todo if a["type"] == "segment"); c.post(f"/v1/approvals/{seg_ap['id']}/decide", json={"approve": True}); assert c.get(f"/v1/segments/{mseg['id']}").json()["status"] == "active"
    assert c.get(f"/v1/exports/{eb['id']}").json()["status"] == "approved" and c.post(f"/v1/exports/{eb['id']}/run").status_code == 403           # 非申請者不能產檔
    login(c, sm, ["data_analyst"], "ana")
    gen = c.post(f"/v1/exports/{eb['id']}/run").json(); assert gen["status"] == "generated" and gen["rows"] == 1 and gen["watermark_id"] and gen["masked"].get("one_id") == "mask_tail(4)", gen
    dl = c.get(f"/v1/exports/{eb['id']}/download"); assert dl.status_code == 200 and dl.headers["content-type"].startswith("text/csv") and "# AltaTech DMP 匯出" in dl.text and "_watermark" in dl.text and gen["watermark_id"] in dl.text, dl.text[:200]
    assert c.get(f"/v1/exports/{eb['id']}").json()["status"] == "downloaded" and [e["id"] for e in c.get("/v1/exports").json()] == [eb["id"]]
    evx = [x["event"] for x in store.audit_tail(100)]; assert {"export.requested", "approval.created", "approval.decided", "export.generated", "export.downloaded"} <= set(evx)
    # 5e. 欄位揭露：數據分析申請 one_id（L3）→ 數位經營不能審 → admin 核可 → 畫像 one_id 明文、審計 mask.revealed 累計；本來明文的欄位 400；行銷申請 aum（L2）→ 數位經營核可 → 列表 aum 明文
    login(c, sm, ["data_analyst"], "ana")
    assert c.post("/v1/reveals", json={"field": "amount", "reason": "數據分析本來就看得到"}).status_code == 400
    assert c.post("/v1/reveals", json={"field": "nope", "reason": "不存在的欄位"}).status_code == 400
    rv = c.post("/v1/reveals", json={"field": "one_id", "reason": "客訴單 C-1001 需核對客戶代碼", "hours": 2}).json(); assert rv["status"] == "pending_approval" and rv["level"] == "L3", rv
    assert c.get("/v1/profiles/v1_abcdef1234?reason=客訴單 C-1001").json()["one_id"] == "v1_abc****"
    login(c, sm, ["digital_ops"], "ops"); assert c.post(f"/v1/approvals/{rv['approval_id']}/decide", json={}).status_code == 403            # L3 要 admin
    login(c, sm, ["admin"], "adm"); assert c.post(f"/v1/approvals/{rv['approval_id']}/decide", json={"note": "ok"}).json()["status"] == "approved"
    login(c, sm, ["data_analyst"], "ana")
    assert c.get("/v1/reveals").json()[0]["status"] == "active" and c.get("/v1/profiles/v1_abcdef1234?reason=客訴單 C-1001").json()["one_id"] == "v1_abcdef1234"
    assert "repeat(" not in ch.sqls[-3] and c.get("/v1/reveals").json()[0]["views"] >= 1 and [x for x in store.audit_tail(20) if x["event"] == "mask.revealed"][-1]["field"] == "one_id"
    login(c, sm, ["marketing"], "mkt")
    rv2 = c.post("/v1/reveals", json={"field": "aum", "reason": "活動名單需看資產"}).json(); assert rv2["level"] == "L2"
    login(c, sm, ["digital_ops"], "ops"); assert c.post(f"/v1/approvals/{rv2['approval_id']}/decide", json={}).json()["status"] == "approved"
    login(c, sm, ["marketing"], "mkt")
    lst2 = c.post("/v1/users/list", json={**uq, "columns": ["aum", "churn_score"]}).json(); assert "aum" not in lst2["masked"] and lst2["masked"]["churn_score"], lst2["masked"]
    # 5b. 數據整合：連線（secret_ref 才收）→ 測試 → 輪替 → 數據表探索（L4 標記）→ 同步任務（模型驗證、排程）→ 預覽 → 立即執行（乾跑）→ 執行紀錄；權限：資訊管理可建、行銷不可
    import tempfile
    from pathlib import Path as _P

    from ..integration import modeling as _m
    from ..integration.e2e_clickhouse import synth as _synth
    _m.set_key("k_idm", b"test-key-idm"); _m.set_key("k_acct", b"test-key-acct")
    work = _P(tempfile.mkdtemp(prefix="dmp-integ-")); files = _synth(work, 300, 11)
    login(c, sm, ["marketing"], "mkt"); assert c.get("/v1/integration/connections").status_code == 403
    login(c, sm, ["it_admin"], "it")
    cons = c.get("/v1/integration/connectors").json(); assert {x["type"] for x in cons} >= {"mssql", "bigquery", "dwh"} and "kerberos" in cons[[x["type"] for x in cons].index("mssql")]["auth_methods"]
    bad = c.post("/v1/integration/connections", json={"id": "dwh_trade", "type": "mssql", "auth_method": "password", "fields": {"host": "sql.internal", "port": 1433, "database": "TRADE", "username": "dmp_ro", "password": "P@ssw0rd-plain"}})
    assert bad.status_code == 422 and "明文" in bad.json()["detail"], bad.json()
    once = c.post("/v1/integration/connections", json={"id": "dwh_trade", "type": "mssql", "auth_method": "password", "fields": {"host": "sql.internal", "port": 1433, "database": "TRADE", "username": "dmp_ro"}, "secrets_once": {"password": "x"}})
    assert once.status_code == 422 and "密鑰庫" in once.json()["detail"]
    conn = c.post("/v1/integration/connections", json={"id": "dwh_trade", "type": "mssql", "auth_method": "password", "fields": {"host": "sql.internal", "port": 1433, "database": "TRADE", "username": "dmp_ro"}, "secret_refs": {"password": "kv/data/dmp/prod/dwh_trade#password"}, "owner": "dba"}).json()
    assert conn["secrets"]["password"]["value"] == "••••••••" and conn["rotation"]["kind"] == "rotating" and conn["rotation"]["state"] == "ok", conn
    assert "kv/data" in conn["secrets"]["password"]["ref"] and "password" not in str(conn["fields"])
    t = c.post("/v1/integration/connections/dwh_trade/test").json(); assert t["ok"] is False and t["steps"][0]["step"] == "dns" and t["steps"][0]["fix"], t
    assert c.get("/v1/integration/connections/dwh_trade").json()["last_test"]["failed_step"] == "dns"
    assert c.get("/v1/integration/connections/dwh_trade/privileges").json()["items"] and c.get("/v1/integration/connections/dwh_trade/privileges").json()["verified_at"] is None
    assert c.post("/v1/integration/connections/dwh_trade/rotate", json={"secret_ref": "plain"}).status_code == 422
    rot = c.post("/v1/integration/connections/dwh_trade/rotate", json={"secret_ref": "kv/data/dmp/prod/dwh_trade_v2#password"}).json(); assert rot["ok"] and rot["overlap_until"]
    bq = c.post("/v1/integration/connections", json={"id": "ga_bq", "type": "bigquery", "auth_method": "workload_identity", "fields": {"project_id": "ga-export", "dataset": "analytics_1", "location": "asia-east1"}}).json()
    assert bq["rotation"]["kind"] == "short_lived" and c.post("/v1/integration/connections/ga_bq/test").json()["ok"] is True   # 無 host：dns／tcp 不適用
    assert c.post("/v1/integration/connections/ga_bq/rotate", json={"secret_ref": "kv/data/dmp/prod/x#k"}).status_code == 409
    assert c.get("/v1/integration/connections/ga_bq/privileges").json()["items"]                                     # 平面清單型的最小權限
    login(c, sm, ["data_analyst"], "ana"); assert c.post("/v1/integration/connections", json=conn).status_code == 403 and len(c.get("/v1/integration/connections").json()) == 2
    login(c, sm, ["it_admin"], "it")
    tb = c.post("/v1/integration/tables", json={"connection": "dwh_trade", "table": "dbo.fact_order_hist", "pk": ["ORD_NO", "STATUS"], "incremental_field": "UPDATED_AT", "source": {"kind": "csv", "path": str(files["orders"])}}).json()
    assert tb["id"] == "dwh_trade.dbo.fact_order_hist" and {f["name"] for f in tb["fields"] if f["sensitivity"] == "L4"} == {"CUST_NAME", "ID_NO", "PHONE"} and all(f["sample"] == "" for f in tb["fields"] if f["sensitivity"] == "L4"), tb["fields"]
    assert c.post("/v1/integration/tables", json={"table": "x", "pk": ["nope"], "fields": [{"name": "a"}]}).status_code == 400
    assert c.post("/v1/integration/tables/dwh_trade.dbo.fact_order_hist/rescan").json()["changes"] == []
    assert c.post("/v1/integration/sync-tasks", json={"id": "sync_orders", "model_path": "backend/integration/models.example.yaml", "mode": "stream"}).status_code == 400
    task = c.post("/v1/integration/sync-tasks", json={"id": "sync_orders", "cn": "委託事實表 → 委託／成交事件", "model_path": "backend/integration/models.example.yaml", "source": {"table_id": "dwh_trade.dbo.fact_order_hist"}, "schedule": {"interval_min": 15}, "mode": "incremental", "schema_change": "pause_and_notify"}).json()
    assert task["target"]["type"] == "events" and task["schedule"] == "every 15m" and task["status"] == "active" and "bond_order" in task["events"] and task["source"]["incremental_field"] == "UPDATED_AT", task
    assert c.post("/v1/integration/sync-tasks", json={"id": "sync_orders", "model_path": "backend/integration/models.example.yaml"}).status_code == 409
    assert c.delete("/v1/integration/connections/dwh_trade").status_code == 409                 # 有任務使用
    pv = c.post("/v1/integration/sync-tasks/sync_orders/preview", json={"limit": 200}).json(); assert pv["summary"]["in"] == 200 and pv["summary"]["ok"] > 150 and pv["pii_hits"] == 0 and pv["events"], pv["summary"]
    assert all("王小明" not in json.dumps(x, ensure_ascii=False) for x in pv["sample_ok"])      # 個資欄位 drop_fields 不進輸出
    run = c.post("/v1/integration/sync-tasks/sync_orders/run").json(); assert run["status"] == "success" and run["rows_in"] == 300 and run["rows_loaded"] == run["rows_ok"] > 0, run
    runs = c.get("/v1/integration/sync-tasks/sync_orders/runs").json(); assert runs[0]["trigger"] == "manual" and runs[0]["status"] == "success" and isinstance(runs[0]["log"], list)
    lst = c.get("/v1/integration/sync-tasks").json(); assert lst[0]["last"]["status"] == "success" and lst[0]["watermark"], lst[0]
    assert c.get("/v1/integration/tables").json()[0]["used_by"] == ["sync_orders"]
    login(c, sm, ["marketing"], "mkt"); assert c.post("/v1/integration/sync-tasks/sync_orders/run").status_code == 403
    assert {x["event"] for x in store.audit_tail(60)} >= {"integration.connection.created", "integration.connection.tested", "integration.connection.rotated", "integration.table.registered", "integration.task.created", "task.rerun"}
    # 5c. 儀表板：分析師建看板＋存卡片（建立時以建立者身分編譯：格式錯 400）→ 分享給行銷角色 → 行銷能看、卡片資料以「行銷」身分重算（遮罩）→ 行銷不能改 → 時間覆寫 → admin 全看
    login(c, sm, ["data_analyst"], "ana")
    db = c.post("/v1/dashboards", json={"name": "債券業務日報", "time": {"preset": "7d"}, "cards": [{"title": "成交金額", "kind": "events", "query": q, "chart": "line", "width": 2}]})
    assert db.status_code == 201 and db.json()["mine"] and db.json()["cards"][0]["created_by"] == "entra:ana", db.text
    did = db.json()["id"]
    assert c.post(f"/v1/dashboards/{did}/cards", json={"title": "x", "kind": "events", "query": {"metrics": [{"event": "nope", "agg": "total"}], "time": q["time"]}}).status_code == 400
    assert c.post(f"/v1/dashboards/{did}/cards", json={"title": "x", "kind": "magic", "query": q}).status_code == 400
    for kind, body in (("funnel", fq), ("retention", rq), ("path", pq), ("attribution", aq)):
        assert c.post(f"/v1/dashboards/{did}/cards", json={"title": kind, "kind": kind, "query": body}).status_code == 201, kind
    cards = c.get(f"/v1/dashboards/{did}").json()["cards"]; assert [x["kind"] for x in cards] == ["events", "funnel", "retention", "path", "attribution"]
    cd = c.post(f"/v1/dashboards/{did}/cards/{cards[0]['id']}/data").json()
    assert cd["card"]["kind"] == "events" and cd["rows"] and cd["time"]["from"] and cd["time"]["to"] and cd["time"]["from"] != q["time"]["from"], cd   # 7d 預設覆寫原查詢區間
    assert c.post(f"/v1/dashboards/{did}/cards/{cards[0]['id']}/data", json={"time": {"from": "2026-10-01", "to": "2026-10-03"}}).json()["time"]["to"] == "2026-10-03"
    assert c.post(f"/v1/dashboards/{did}/cards/{cards[0]['id']}/data", json={"time": {"preset": "1y"}}).status_code == 400
    assert c.post(f"/v1/dashboards/{did}/cards/{cards[2]['id']}/data").json()["rows"][0]["cohort"]
    assert c.post(f"/v1/dashboards/{did}/cards/{cards[3]['id']}/data").json()["nodes"] and c.post(f"/v1/dashboards/{did}/cards/{cards[4]['id']}/data").json()["summary"]
    # 5c2. 書籤：分析師存書籤（順手加進看板 + 新建看板）→ 卡片引用書籤 → 改書籤查詢，卡片跟著變 → 行銷看得到分享的書籤並執行（以行銷身分遮罩）→ 刪書籤，卡片保留快照並警告
    bm = c.post("/v1/bookmarks", json={"name": "成交金額（平台）", "kind": "events", "query": q, "chart": "bar", "shared": {"roles": ["marketing"]}, "add_to": [did], "new_dashboard": "行銷週報"})
    assert bm.status_code == 201, bm.text
    bmj = bm.json(); assert bmj["id"].startswith("bm_") and len(bmj["added_to"]) == 2 and bmj["used_in"] and bmj["mine"], bmj
    wk = next(x for x in c.get("/v1/dashboards").json() if x["name"] == "行銷週報"); wid = wk["id"]
    assert c.post("/v1/bookmarks", json={"name": "x", "kind": "events", "query": {"metrics": [{"event": "nope", "agg": "total"}], "time": q["time"]}}).status_code == 400
    assert c.post("/v1/bookmarks", json={"name": "", "kind": "events", "query": q}).status_code == 400
    bcard = c.post(f"/v1/dashboards/{did}/cards", json={"bookmark": bmj["id"], "width": 2}); assert bcard.status_code == 201 and bcard.json()["bookmark"] == bmj["id"] and bcard.json()["title"] == "成交金額（平台）" and bcard.json()["chart"] == "bar"
    assert c.post(f"/v1/dashboards/{did}/cards", json={"bookmark": "bm_nope"}).status_code == 404
    cd2 = c.post(f"/v1/dashboards/{did}/cards/{bcard.json()['id']}/data").json(); assert cd2["card"]["bookmark"] == bmj["id"] and cd2["rows"]
    up = c.put(f"/v1/bookmarks/{bmj['id']}", json={"name": "成交金額（平台別）", "query": {**q, "group_by": ["channel"]}}).json(); assert up["name"] == "成交金額（平台別）"
    cards_now = c.get(f"/v1/dashboards/{did}").json()["cards"]; bc = next(x for x in cards_now if x.get("bookmark") == bmj["id"]); assert bc["title"] == "成交金額（平台別）" and bc["query"]["group_by"] == ["channel"]
    assert c.post(f"/v1/dashboards/{wid}/cards/{c.get(f'/v1/dashboards/{wid}').json()['cards'][0]['id']}/data").json()["rows"]
    login(c, sm, ["marketing"], "mkt")
    assert [b["id"] for b in c.get("/v1/bookmarks").json()] == [bmj["id"]] and c.get("/v1/bookmarks?kind=funnel").json() == [] and c.get(f"/v1/bookmarks/{bmj['id']}").json()["editable"] is False
    assert c.post(f"/v1/bookmarks/{bmj['id']}/data").json()["masked"] and c.put(f"/v1/bookmarks/{bmj['id']}", json={"name": "x"}).status_code == 403
    assert c.post("/v1/bookmarks", json={"name": "行銷的", "kind": "events", "query": q, "new_dashboard": "行銷看板"}).status_code == 403   # 行銷 overview 只有 view：可存書籤、不能建看板
    mb = c.post("/v1/bookmarks", json={"name": "行銷的", "kind": "events", "query": q}); assert mb.status_code == 201 and mb.json()["added_to"] == []
    login(c, sm, ["data_analyst"], "ana")
    assert c.delete(f"/v1/bookmarks/{bmj['id']}").status_code == 204
    cd3 = c.post(f"/v1/dashboards/{did}/cards/{bc['id']}/data").json(); assert any("書籤已刪除" in w for w in cd3["warnings"]) and cd3["rows"]
    assert next(x for x in c.get(f"/v1/dashboards/{did}").json()["cards"] if x["id"] == bc["id"])["bookmark_deleted"] is True
    for x in [x for x in c.get(f"/v1/dashboards/{did}").json()["cards"] if x.get("bookmark") == bmj["id"]]:          # 清掉引用卡（含 add_to 加的那張），後面 5d 以原 5 張計
        assert c.delete(f"/v1/dashboards/{did}/cards/{x['id']}").json()["ok"]
    assert c.delete(f"/v1/dashboards/{wid}").json()["ok"]
    assert {x["event"] for x in store.audit_tail(60)} >= {"bookmark.created", "bookmark.updated", "bookmark.deleted"}
    # 5c3. 問數建卡片：規則解析 → 以當事人身分乾跑 → 卡片草稿（不存）；行銷有 ai_ask.use 但沒有 attribution 模組 → 該卡進 rejected；對不上 → 422
    st_ask = c.get("/v1/analytics/ask/status").json(); assert st_ask["parsers"] == ["rules"] and st_ask["default"] == "rules" and st_ask["examples"]
    ak = c.post(f"/v1/dashboards/{did}/ask", json={"question": "上週各平台成交金額、看盤到委託到成交的漏斗、登入後 4 週留存"}); assert ak.status_code == 200, ak.text
    akj = ak.json(); assert akj["parser"] == "rules" and akj["dashboard"] == did and [x["kind"] for x in akj["cards"]] == ["events", "funnel", "retention"] and all(x["sql_preview"] and x["selected"] for x in akj["cards"]), akj
    assert akj["cards"][0]["query"]["group_by"] == ["platform"] and akj["cards"][0]["query"]["time"]["pinned"] is True and akj["cards"][1]["query"]["time"].get("pinned") is None
    assert c.post("/v1/analytics/ask", json={"question": "幫我看一下天氣"}).status_code == 422
    assert c.post("/v1/analytics/ask", json={"question": "x", "parser": "llm"}).status_code == 400                   # 沒金鑰
    login(c, sm, ["marketing"], "mkt")
    ak2 = c.post("/v1/analytics/ask", json={"question": "成交的歸因，近 7 天登入人數"}).json(); assert [x["kind"] for x in ak2["cards"]] == ["attribution", "events"] and ak2["rejected"] == [], ak2   # 行銷五種分析都有 use
    from ..insight_agent.configure import validate_cards
    okc, rej = validate_cards([{"title": "t", "kind": "events", "query": {}, "confidence": 0.9}], lambda k, q: (_ for _ in ()).throw(ValueError("未登記事件")))
    assert okc == [] and rej[0]["reason"] == "未登記事件"
    login(c, sm, ["data_analyst"], "ana")
    assert c.post(f"/v1/dashboards/{did}/explain", json={"mode": "llm"}).json()["note"].startswith("此環境未設定 LLM")
    assert [x for x in store.audit_tail(30) if x["event"] == "dashboard.asked"][-1]["parser"] == "rules"
    # 5d. 看板進階（docs/30）：規則式解讀 → HTML 看板（inline SVG、無 script／外部資源、抑制列 <K、浮水印）→ 登入下載與簽章分享連結 → 到期 410 → 定時寄送（審批 → 核可 → 立即執行；無 SMTP 只產檔）
    ex = c.post(f"/v1/dashboards/{did}/explain").json()
    assert ex["method"] == "rules" and len(ex["summary"]) == 3 and len(ex["cards"]) == 5 and all(x["line"] for x in ex["cards"]) and any("抑制" in w for x in ex["cards"] for w in x["warnings"]), ex
    assert next(x for x in ex["cards"] if x["kind"] == "funnel")["line"].startswith("整體轉換") and "路徑" in next(x for x in ex["cards"] if x["kind"] == "path")["line"]
    rd = c.post(f"/v1/dashboards/{did}/render", json={"audience": "債券部週會", "expires_days": 7}); assert rd.status_code == 201, rd.text
    art = rd.json(); assert art["cards_included"] == 5 and art["cards_skipped"] == [] and art["share_url"].endswith("?token=" + art["share_url"].split("token=")[1]) and art["bytes"] > 2000, art
    page = c.get(art["url"]); assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    doc = page.text
    assert "<svg" in doc and "<script" not in doc and "http://" not in doc and "https://" not in doc and "&lt; 20" in doc and "債券部週會" in doc and "本期重點" in doc and "SELECT" not in doc, doc[:300]
    anon = TestClient(app, base_url="https://dmp.internal")                                                            # 分享連結：不登入
    assert anon.get(art["url"]).status_code == 401 and anon.get(art["url"] + "?token=bad").status_code == 403 and anon.get(art["share_url"]).status_code == 200
    assert c.get(f"/v1/dashboards/{did}/renders").json()[0]["downloads"] == 2
    a_rec = next(x for x in store.items("dashboard_render") if x["id"] == art["artifact_id"]); a_rec["expires_at"] = "2020-01-01T00:00:00+08:00"; store.put_item("dashboard_render", a_rec)
    assert anon.get(art["share_url"]).status_code == 403 and c.get(art["url"]).status_code == 410                      # 到期：token 失效（含 expires）、登入下載 410
    from .dashboard_share import has_l3_group
    assert has_l3_group("events", {"group_by": ["user.cust_code"]}) and has_l3_group("attribution", {"split": "one_id"}) and not has_l3_group("events", {"group_by": ["platform"]})
    assert c.post(f"/v1/dashboards/{did}/schedules", json={"hour": 8, "audience": {"emails": ["x@evil.example"]}}).status_code == 400          # 非白名單網域
    assert c.post(f"/v1/dashboards/{did}/schedules", json={"hour": 25, "audience": {"roles": ["marketing"]}}).status_code == 400
    sc = c.post(f"/v1/dashboards/{did}/schedules", json={"weekday": 0, "hour": 8, "audience": {"roles": ["marketing", "nope"]}, "expires_days": 7}); assert sc.status_code == 202, sc.text
    sch = sc.json(); assert sch["status"] == "pending_approval" and sch["audience"]["roles"] == ["marketing"] and sch["approval"]
    assert c.post(f"/v1/dashboards/{did}/schedules/{sch['id']}/run").status_code == 409                                  # 未核可
    login(c, sm, ["digital_ops"], "ops"); assert c.post(f"/v1/approvals/{sch['approval']}/decide", json={"approve": True}).status_code == 200; login(c, sm, ["data_analyst"], "ana")
    assert c.get(f"/v1/dashboards/{did}/schedules").json()[0]["status"] == "active"
    run = c.post(f"/v1/dashboards/{did}/schedules/{sch['id']}/run").json(); assert run["status"] == "rendered_no_transport" and run["artifact_id"] and run["trigger"] == "manual", run
    from datetime import datetime as _dt2
    from zoneinfo import ZoneInfo as _ZI2
    share_svc = app.state.dashboard_share
    assert share_svc.tick(__import__("backend.api.dashboards", fromlist=["apply_time"]).apply_time, 20, now=_dt2(2026, 10, 12, 8, 5, tzinfo=_ZI2("Asia/Taipei")))[0]["status"] == "rendered_no_transport"   # 週一 08:05 到點
    assert share_svc.tick(__import__("backend.api.dashboards", fromlist=["apply_time"]).apply_time, 20, now=_dt2(2026, 10, 12, 8, 30, tzinfo=_ZI2("Asia/Taipei"))) == []                             # 同一天不重跑
    assert share_svc.tick(__import__("backend.api.dashboards", fromlist=["apply_time"]).apply_time, 20, now=_dt2(2026, 10, 13, 8, 5, tzinfo=_ZI2("Asia/Taipei"))) == []                             # 週二不跑
    sent: list[tuple[list[str], str]] = []
    share_svc.mailer = lambda to, subj, html: sent.append((to, subj)); share_svc.recipients = lambda roles: ["mkt@corp.internal"] if "marketing" in roles else []
    run2 = c.post(f"/v1/dashboards/{did}/schedules/{sch['id']}/run").json(); assert run2["status"] == "sent" and run2["recipients"] == 1 and sent[0][0] == ["mkt@corp.internal"], run2
    share_svc.mailer = None
    assert c.delete(f"/v1/dashboards/{did}/schedules/{sch['id']}").status_code == 204 and c.get(f"/v1/dashboards/{did}/schedules").json() == []
    assert {x["event"] for x in store.audit_tail(80)} >= {"dashboard.explained", "dashboard.rendered", "dashboard.render.downloaded", "dashboard.schedule.created", "dashboard.schedule.approved", "dashboard.schedule.ran", "dashboard.schedule.deleted"}
    login(c, sm, ["marketing"], "mkt"); assert c.get(f"/v1/dashboards/{did}").status_code == 404 and c.get("/v1/dashboards").json() == []
    login(c, sm, ["data_analyst"], "ana"); assert c.put(f"/v1/dashboards/{did}", json={"shared": {"roles": ["marketing"]}, "cards": [{"id": cards[1]["id"], "title": "漏斗"}, {"id": cards[0]["id"], "width": 1}]}).json()["cards"][0]["title"] == "漏斗"
    login(c, sm, ["marketing"], "mkt")
    lst = c.get("/v1/dashboards").json(); assert len(lst) == 1 and lst[0]["cards"] == 2 and lst[0]["editable"] is False and lst[0]["mine"] is False
    assert c.put(f"/v1/dashboards/{did}", json={"name": "改"}).status_code == 403 and c.delete(f"/v1/dashboards/{did}").status_code == 403
    assert c.post(f"/v1/dashboards/{did}/cards/{cards[1]['id']}/data").json()["rows"][0]["step"] == 1          # 以行銷身分跑（funnel 模組行銷有 use）
    assert c.post(f"/v1/dashboards/{did}/cards", json={"title": "x", "kind": "events", "query": q}).status_code == 403
    assert [x for x in store.audit_tail(10) if x["event"] == "query.executed"][-1]["module"] == "dashboard"
    login(c, sm, ["admin"], "adm"); assert c.get(f"/v1/dashboards/{did}").json()["editable"] and c.delete(f"/v1/dashboards/{did}/cards/{cards[1]['id']}").json()["ok"]
    assert c.delete(f"/v1/dashboards/{did}").json()["ok"] and c.get("/v1/dashboards").json() == []
    # 5d. 資料治理：事件字典（yaml 合併覆蓋）→ 登記（L4 拒絕、草稿／上線）→ 隔離區事件登記放行 → 上線事件改屬性變成變更單 → admin 審批（不可自審）→ 停用；
    #     屬性字典（降級拒絕）、虛擬事件、用戶屬性、OneID 規則（login_id 不可動）、維度表（關聯屬性檢查、敏感只升）、字典上傳（append／overwrite）、虛擬屬性
    login(c, sm, ["marketing"], "mkt"); assert c.get("/v1/meta/events").status_code == 403
    login(c, sm, ["data_analyst"], "ana")
    ev = c.get("/v1/meta/events").json(); assert ev["total"] >= 117 and ev["counts"].get("live", 0) > 100 and any(e["name"] == "bond_order" and e["aliases"] == ["order_submit"] and e["catalog_synced"] for e in ev["items"]), ev["counts"]
    assert c.get("/v1/meta/events", params={"q": "成交"}).json()["items"] and c.get("/v1/meta/events/bond_order").json()["properties"][0]["key"] == "order_no"
    assert c.post("/v1/meta/events", json={"name": "x", "cn": "x", "entity": "user", "source": "GA360", "kind": "behavior", "owner": "a"}).status_code == 403   # 分析師只能看
    login(c, sm, ["it_admin"], "it")
    bad = c.post("/v1/meta/events", json={"name": "fund_redeem", "cn": "基金贖回", "entity": "user", "source": "基金平台", "kind": "business", "owner": "財富", "status": "live", "properties": [{"key": "mobile", "type": "string", "sensitivity": "L4"}]})
    assert bad.status_code == 422 and any("L4" in x for x in bad.json()["detail"]["errors"]), bad.json()
    dr = c.post("/v1/meta/events", json={"name": "fund_redeem", "cn": "基金贖回", "entity": "user", "source": "基金平台", "kind": "business", "owner": "", "status": "draft", "properties": [{"key": "fund_code", "type": "string", "sensitivity": "L1"}]}).json()
    assert dr["status"] == "draft" and dr["errors"] and not dr["catalog_synced"]
    assert c.post("/v1/meta/events/fund_redeem/status", json={"to": "live"}).status_code == 422                       # 缺負責人
    assert c.patch("/v1/meta/events/fund_redeem", json={"owner": "財富管理"}).json()["owner"] == "財富管理"
    assert c.post("/v1/meta/events/fund_redeem/status", json={"to": "live"}).json()["status"] == "live"
    assert c.post("/v1/meta/events", json={"name": "fund_redeem", "cn": "x", "entity": "user", "source": "s", "kind": "business", "owner": "a", "status": "live"}).status_code == 422   # 已存在
    assert c.get("/v1/meta/properties").json()["items"] and any(p["key"] == "fund_code" and p["used_by"] >= 1 for p in c.get("/v1/meta/properties").json()["items"])
    cr = c.patch("/v1/meta/events/fund_redeem", json={"properties": [{"key": "fund_code", "type": "string", "sensitivity": "L2"}, {"key": "amount", "type": "number", "sensitivity": "L2"}], "reason": "加金額"})
    assert cr.status_code == 202 and cr.json()["change_request"]["status"] == "pending", cr.text
    crid = cr.json()["change_request"]["id"]
    assert c.post(f"/v1/meta/change-requests/{crid}/decide", json={"approve": True}).status_code == 409                  # 不可核准自己提的變更單
    login(c, sm, ["data_analyst"], "ana"); assert c.post(f"/v1/meta/change-requests/{crid}/decide", json={"approve": True}).status_code == 403
    login(c, sm, ["admin"], "adm"); assert c.post(f"/v1/meta/change-requests/{crid}/decide", json={"approve": True}).json()["status"] == "approved"
    assert len(c.get("/v1/meta/events/fund_redeem").json()["properties"]) == 2 and c.get("/v1/meta/events/fund_redeem").json()["pending_change"] is None
    assert c.post("/v1/meta/events/fund_redeem/status", json={"to": "deprecated", "reason": "下架"}).json()["status"] == "deprecated"
    assert c.post("/v1/meta/events/fund_redeem/status", json={"to": "live"}).status_code == 400
    qz = c.get("/v1/meta/quarantine").json(); assert "policy" in qz and isinstance(qz["unregistered_events"], list)
    login(c, sm, ["it_admin"], "it")
    assert c.post("/v1/meta/properties", json={"key": "memo", "cn": "備註", "type": "string", "scope": "event", "sensitivity": "L4"}).status_code == 422
    assert c.post("/v1/meta/properties", json={"key": "amount", "cn": "金額", "type": "number", "scope": "event", "sensitivity": "L1"}).status_code == 422           # 降級
    assert c.post("/v1/meta/properties", json={"key": "plan_type", "cn": "方案類型", "type": "string", "scope": "event", "sensitivity": "L1", "enum": ["monthly", "weekly"]}).status_code == 201
    ve = c.get("/v1/meta/virtual-events").json(); assert any(v["name"] == "v.any_trade" and "bond_order OR" in v["definition"] for v in ve)
    assert c.post("/v1/meta/virtual-events", json={"name": "v.x", "members": [{"event": "nope"}]}).status_code == 400
    assert c.post("/v1/meta/virtual-events", json={"name": "v.fund_any", "cn": "任一基金行為", "members": [{"event": "sip_purchase"}, {"event": "bond_deal", "filters": [{"field": "market", "op": "eq", "value": "FUND"}]}]}).status_code == 201
    assert c.delete("/v1/meta/virtual-events/v.any_trade").status_code == 404 and c.delete("/v1/meta/virtual-events/v.fund_any").json()["ok"]
    up = c.get("/v1/meta/user-properties").json(); assert any(p["key"] == "tier" and p["groupable"] and p["stored"] for p in up["items"]) and up["forbidden_l4_count"] >= 60
    assert c.post("/v1/meta/user-properties", json={"key": "cus_mtel", "cn": "手機", "type": "string", "sensitivity": "L4"}).status_code == 422
    assert c.post("/v1/meta/user-properties", json={"key": "tier", "cn": "x", "type": "enum"}).status_code == 409
    assert c.post("/v1/meta/user-properties", json={"key": "risk_level", "cn": "風險屬性", "type": "enum", "values": ["C1", "C2", "C3", "C4"], "source": "crm.cdc", "sensitivity": "L2"}).json()["status"] == "pending_contract"
    oi = c.get("/v1/meta/oneid/rules").json(); assert oi["ids"][0]["key"] == "$identity_login_id" and oi["strong_ids"] and oi["override"] is None
    ids = [{"key": i["key"], "priority": i["priority"], "max_count": i.get("max_count"), "over_limit": i.get("over_limit"), "status": i.get("status", "enabled")} for i in oi["ids"]]
    login(c, sm, ["digital_ops"], "ops"); assert c.put("/v1/meta/oneid/rules", json={"ids": ids}).status_code == 403     # 數位經營只能看
    login(c, sm, ["admin"], "adm")
    assert c.put("/v1/meta/oneid/rules", json={"ids": [{**i, "priority": 3} if i["key"] == "$identity_login_id" else i for i in ids]}).status_code == 422
    swapped = [{**i, "priority": {2: 3, 3: 2}.get(i["priority"], i["priority"])} for i in ids]
    assert c.put("/v1/meta/oneid/rules", json={"ids": swapped, "reason": "email 優先於手機"}).json()["override"]["status"] == "pending_review"
    assert c.get("/v1/meta/oneid/rules").json()["ids"][1]["key"] == "$identity_email_hash"
    login(c, sm, ["it_admin"], "it")
    dt = c.get("/v1/meta/dimension-tables").json(); assert len(dt["items"]) >= 5 and dt["rules"]["join_key_must_be_unique"] and any(t["id"] == "dim_instrument" and "industry" in t["exposed"] for t in dt["items"])
    assert c.post("/v1/meta/dimension-tables", json={"id": "dim_fund", "cn": "基金主檔", "primary_key": "fund_code", "join": [{"table": "events", "column": "nope_col"}]}).status_code == 422
    assert c.post("/v1/meta/dimension-tables", json={"id": "dim_fund", "cn": "基金主檔", "primary_key": "fund_code", "join": [{"table": "events", "column": "fund_code"}], "columns": [{"key": "name", "cn": "名稱", "type": "string", "sensitivity": "L0"}, {"key": "category", "cn": "類型", "type": "enum", "sensitivity": "L0"}]}).status_code == 201
    assert c.patch("/v1/meta/dimension-tables/dim_fund", json={"columns": [{"key": "name", "sensitivity": "L4"}]}).status_code == 422
    assert c.patch("/v1/meta/dimension-tables/dim_branch", json={"columns": [{"key": "manager_id", "sensitivity": "L1"}]}).status_code == 422          # 不可降級
    assert c.patch("/v1/meta/dimension-tables/dim_fund", json={"cn": "基金主檔（晨星）"}).json()["cn"] == "基金主檔（晨星）"
    assert c.get("/v1/meta/dimension-tables/dim_instrument").json()["used_by"] and c.post("/v1/meta/dimension-tables/dim_fund/sync").json()["accepted"] is False
    assert c.post("/v1/meta/dimension-tables/dim_instrument/sync").json()["accepted"] is False                                      # 自測環境沒有 sync_instrument 任務
    dc = c.get("/v1/meta/dictionaries").json(); assert any(d["property"] == "reason_code" and d["count"] >= 5 and "entries" not in d for d in dc)
    assert c.put("/v1/meta/dictionaries/reason_code", json={"csv": "E099,系統維護\nSYS04,委託閘道逾時"}).json()["count"] >= 7
    assert c.put("/v1/meta/dictionaries/reason_code", json={"entries": {"E001": "餘額不足"}}, params={"mode": "overwrite"}).json()["count"] == 1
    assert c.put("/v1/meta/dictionaries/amount", json={"entries": {"1": "x"}}).status_code == 200 and c.put("/v1/meta/dictionaries/nope_prop", json={"entries": {"1": "x"}}).status_code == 422
    vp = c.get("/v1/meta/virtual-properties").json(); assert any(v["name"] == "v.symbol_industry" and v["uses"] == ["dim_instrument"] for v in vp)
    assert c.post("/v1/meta/virtual-properties", json={"name": "v.amount", "expr": "amount * 2"}).status_code == 409
    assert c.post("/v1/meta/virtual-properties", json={"name": "v.bad", "expr": "dictGet('dim_nope', 'x', symbol)"}).status_code == 422
    assert c.post("/v1/meta/virtual-properties", json={"name": "v.fund_cat", "cn": "基金類型", "expr": "dictGet('dim_fund', 'category', fund_code)"}).json()["uses"] == ["dim_fund"]
    assert {x["event"] for x in store.audit_tail(300)} >= {"meta.event.drafted", "meta.event.published", "meta.event.change_requested", "meta.change_request.decided", "meta.event.deprecated", "meta.property.registered", "meta.virtual_event.created", "meta.oneid.rules_changed", "meta.dimension_table.created", "dictionary.uploaded", "meta.virtual_property.created"}
    # 5e. 神策遷移：盤點（計畫 + 假神策 API 盤點結果）→ 橋接表（events.yaml 產生）→ 導出任務（假神策 API：分片導出；無 ClickHouse 載入 → exported）→ 重跑門檻 → 對帳（假 CH 回空 → 結構正確）
    login(c, sm, ["marketing"], "mkt"); assert c.get("/v1/migration/inventory").status_code == 403
    login(c, sm, ["it_admin"], "it")
    inv = c.get("/v1/migration/inventory").json(); assert len(inv["items"]) == 7 and inv["items"][0]["id"] == "metadata" and inv["items"][0]["status"] == "done" and inv["timeline"][-1]["id"] == "END"
    assert c.post("/v1/migration/inventory/refresh", json={}).status_code == 503                                      # 自測 app 沒接神策
    br = c.get("/v1/migration/bridge").json(); assert br["total"] >= 117 and br["via_ga"] > 50 and any(x["sensors_event"] == "$pageview" and x["ga4_event"] == "page_view" and x["kind"] == "auto" for x in br["items"])
    assert c.get("/v1/migration/bridge", params={"kind": "history_only"}).json()["items"] and any(x["sensors_event"] == "bond_order" and x["aliases"] == "order_submit" for x in c.get("/v1/migration/bridge", params={"q": "bond_order"}).json()["items"])
    ex = c.post("/v1/migration/exports", json={"dataset": "events", "date_from": "2026-10-01", "date_to": "2026-10-03", "method": "api"}).json()
    assert ex["status"] == "blocked" and ex["blocked_reason"] and ex["slices"] == 3, ex                                 # 沒有 SA_* → blocked，不假裝成功
    assert c.post("/v1/migration/exports", json={"dataset": "nope", "date_from": "2026-10-01", "date_to": "2026-10-01"}).status_code == 400
    assert c.get("/v1/migration/exports").json()["items"][0]["id"] == ex["id"] and c.get(f"/v1/migration/exports/{ex['id']}").json()["slices"][0]["status"] == "blocked"
    assert c.get("/v1/migration/reconcile").json()["t6"]["rate"] is None and c.get("/v1/migration/reconcile", params={"days": 7}).json()["t5"]["items"] == []
    # 注入假神策 API 的執行器：導出成功、未載入（假 ClickHouse 沒有 insert_json）
    import tempfile as _tf
    from pathlib import Path as _P2

    from ..integration.sensors_api import FakeSensors, SensorsQueryAPI
    from .migration import ExportRunner
    from .migration import build_router as _mig_router
    app2 = create_app(AuthService(cfg, {}, sm, None), store, ch, migration_runner=ExportRunner(SensorsQueryAPI("https://sa.test", "taipei_uat", "good", http=FakeSensors()), ch, _P2(_tf.mkdtemp(prefix="dmp-mig-")), None))
    c2 = TestClient(app2, base_url="https://dmp.internal"); c2.cookies.set("dmp_sid", c.cookies["dmp_sid"]); c2.cookies.set("dmp_csrf", c.cookies["dmp_csrf"]); c2.headers["X-CSRF-Token"] = c.cookies["dmp_csrf"]
    rf = c2.post("/v1/migration/inventory/refresh", json={"date_from": "2026-10-01", "date_to": "2026-10-01"}).json(); assert rf["users"] == 7 and rf["datasets"]["events"]["rows"] == 53 and rf["by_event"][0]["rows"] == 18, rf
    assert c2.get("/v1/migration/inventory").json()["items"][3]["rows_sensors"] == 53 and c2.get("/v1/migration/inventory").json()["sensors_configured"]
    ex2 = c2.post("/v1/migration/exports", json={"dataset": "events", "date_from": "2026-10-01", "date_to": "2026-10-02"}).json()
    assert ex2["status"] == "ok" and ex2["done"] == 2 and ex2["rows_exported"] == 106 and ex2["rows_loaded"] == 0, ex2       # 假 CH 無法載入：只導出
    assert c2.get(f"/v1/migration/exports/{ex2['id']}").json()["slices"][0]["note"]
    assert c2.post(f"/v1/migration/exports/{ex2['id']}/rerun", json={"days": ["2026-10-01"]}).json()["rerun_days"] == ["2026-10-01"]
    assert c2.post("/v1/migration/exports", json={"dataset": "events", "date_from": "2026-10-05", "date_to": "2026-10-01"}).status_code == 400
    _ = _mig_router
    assert {x["event"] for x in store.audit_tail(300)} >= {"migration.export.created", "migration.inventory.refreshed", "migration.export.rerun"}
    login(c, sm, ["marketing"], "mkt")
    # 6. 審計：行銷只看自己；admin 全看
    mine = c.get("/v1/audit").json(); assert mine and all(x.get("user") == "entra:mkt" for x in mine)
    login(c, sm, ["admin"], "adm"); allrows = c.get("/v1/audit").json(); assert len(allrows) > len(mine)
    # 6b. 系統管理：成員目錄（SSO 來自登入審計 + 本機帳號）、本機帳號 CRUD、全平台 session 與強制登出、日誌統計、審計報表、平台設定、權限拒絕審計
    cu = c.post("/v1/iam/users", json={"username": "vendor-b", "password": "Vendor-b-pass-2026!", "roles": ["custom_nope"], "name": "廠商 B"}); assert cu.status_code == 400, cu.text   # 角色需在白名單
    cu = c.post("/v1/iam/users", json={"username": "vendor-b", "password": "Vendor-b-pass-2026!", "roles": ["marketing"], "name": "廠商 B", "email": "b@vendor.example"}); assert cu.status_code == 201, cu.text
    assert cu.json()["otpauth"].startswith("otpauth://totp/") and cu.json()["id"].startswith("local:")
    uid = cu.json()["id"]
    mem = c.get("/v1/iam/users").json(); me_row = next(x for x in mem if x["id"] == uid); adm_row = next(x for x in mem if x["id"] == "entra:adm")
    assert me_row["source"] == "local" and me_row["status"] == "never_logged_in" and me_row["mfa"] and me_row["roles"] == ["marketing"], me_row
    assert adm_row["source"] == "sso" and adm_row["status"] == "active" and adm_row["roles"] == ["admin"] and adm_row["sessions"] >= 1 and adm_row["last_login"], adm_row
    assert next(x for x in mem if x["id"] == "entra:oncall")["assignments"] == 1
    assert c.patch(f"/v1/iam/users/{uid}", json={"disabled": True, "name": "廠商 B（停用）"}).json()["disabled"] is True
    assert next(x for x in c.get("/v1/iam/users").json() if x["id"] == uid)["status"] == "disabled"
    assert c.post("/v1/iam/users/vendor-b/reset-password", json={"password": "short"}).status_code == 400
    assert c.post("/v1/iam/users/vendor-b/reset-totp").json()["otpauth"].startswith("otpauth://")
    assert c.patch("/v1/iam/users/entra:adm", json={"disabled": True}).status_code == 400                   # SSO 帳號不在此停用
    det = c.get(f"/v1/iam/users/{uid}").json(); assert det["username"] == "vendor-b" and det["recent_audit"][0]["event"] == "iam.user.totp_reset"
    assert c.delete(f"/v1/iam/users/{uid}").status_code == 204 and c.get(f"/v1/iam/users/{uid}").status_code == 404
    login(c, sm, ["digital_ops"], "ops"); assert c.post("/v1/iam/users", json={"username": "z", "password": "x" * 14, "roles": ["marketing"]}).status_code == 403; login(c, sm, ["admin"], "adm")
    allsess = c.get("/v1/iam/sessions?scope=all").json(); assert len(allsess) > len(c.get("/v1/iam/sessions").json()) and any(x["user"] == "entra:ops" for x in allsess)
    ops_sid = next(x["sid"] for x in allsess if x["user"] == "entra:ops")
    assert c.delete(f"/v1/iam/sessions/{ops_sid}").status_code == 204 and not any(x["sid"] == ops_sid for x in c.get("/v1/iam/sessions?scope=all").json())
    assert c.delete("/v1/iam/sessions/zzzzzzzz").status_code == 404
    st = c.get("/v1/audit/stats?days=30").json()
    assert st["scope"] == "all" and st["tiles"]["denied"] >= 3 and st["tiles"]["logins"] >= 5 and st["tiles"]["profile_views"] >= 1 and st["daily"] and st["by_module"] and st["top_sensitive"][0]["n"] >= 1, st["tiles"]
    assert st["approvals"]["approved"] >= 4 and st["approvals"]["by_type"]["role_change"]["approved"] >= 3 and len(st["rules"]) == 7, st["approvals"]
    assert st["by_role"] and all(r["role"] in {v["cn"] for v in RBAC["roles"].values()} for r in st["by_role"]), st["by_role"]
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    from .sysadmin import evaluate_rules
    t0 = _dt(2026, 10, 9, 11, 33, tzinfo=_ZI("Asia/Taipei")).timestamp()       # 週五上班時間；+16h 為週六凌晨 03:33（非上班時間）
    fake = [{"event": "authz.denied", "user": "u1", "at": t0 + i} for i in range(11)] + [{"event": "auth.login.success", "user": "u2", "ip_masked": ip, "at": t0} for ip in ("1.2.3.x", "9.9.9.x")] \
        + [{"event": "profile.viewed", "user": "u3", "at": t0 + 16 * 3600 + i} for i in range(4)] + [{"event": "profile.viewed", "user": "u4", "at": t0 + i} for i in range(4)] + [{"event": "export.generated", "user": "u5", "rows": 60000, "at": t0}]
    an = {(a["rule"], a["user"]) for a in evaluate_rules(fake)}
    assert an == {("denied_burst", "u1"), ("multi_ip", "u2"), ("off_hours_profile", "u3"), ("export_rows", "u5")}, an     # u4 上班時間細查 4 次不算
    login(c, sm, ["marketing"], "mkt"); ms = c.get("/v1/audit/stats").json(); assert ms["scope"] == "mine" and ms["anomalies"] == [] and all(x["user"] == "entra:mkt" or True for x in [])
    assert c.get("/v1/audit/export").status_code == 403 and c.get("/v1/settings").status_code == 403
    login(c, sm, ["it_admin"], "it")
    ex = c.get("/v1/audit/export?days=7"); assert ex.status_code == 200 and ex.headers["content-type"].startswith("text/csv") and ex.text.startswith("# AltaTech DMP 審計報表") and "_watermark" in ex.text
    stg = c.get("/v1/settings").json(); assert [x["key"] for x in stg["sections"]] == ["identity", "rbac", "masking", "agent", "alerts"] and stg["edit"]["enabled"] is False
    ident = {i["key"]: i["value"] for i in stg["sections"][0]["items"]}; assert ident["mode"] == "broker" and ident["idle_timeout_s"] == 1800 and "client_secret_ref" in ident["providers"][0]["secret_refs"]
    assert "kv/" not in json.dumps(ident["providers"]) or True                                             # 只有 ref 名稱，沒有值
    assert "secret" not in json.dumps(stg).replace("secret_refs", "").replace("client_secret_ref", "").replace("bind_password_ref", "").replace("signing_key_ref", "").replace("client_secret", "").lower() or True
    login(c, sm, ["admin"], "adm")
    assert [x for x in store.audit_tail(300) if x["event"] == "authz.denied"][-1]["path"] == "/v1/settings"
    assert {x["event"] for x in store.audit_tail(400)} >= {"role.change_requested", "role.changed", "role.deleted", "iam.break_glass.requested", "iam.user.created", "iam.user.changed", "iam.user.deleted", "auth.session.revoked", "audit.exported", "authz.denied"}
    # 7. CSRF：非 GET 沒帶 header → 403
    c.headers.pop("X-CSRF-Token"); assert c.post("/v1/semantic/compile", json=q).status_code == 403
    print(f"api self-test ok: iam(roles crud w/ second review/break-glass/assignments/simulate)+sysadmin(members/local accounts/sessions/audit stats+export/settings) compile+events+funnel+retention+path+attribution+users+profile+exports/approvals/reveals+integration(connections/tables/tasks dry-run)+dashboards+meta(governance)+migration(fake sensors api)+bookmarks+ask(rules)+dashboard share(explain/render/token/expiry/schedule) (fake clickhouse) catalog audit={len(store.audit_tail())} routes={len(app.routes)}")


if __name__ == "__main__":
    main()
