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

    from ..integration.runner import LocalRunner as _LR
    from ..integration.scheduler import Scheduler as _Sched
    app = create_app(AuthService(cfg, {}, sm, None), store, ch, scheduler=_Sched(_tf.mktemp(prefix="dmp-sched-", suffix=".db"), _LR(None)))   # 乾跑排程器，每次自測全新狀態
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
    todo = c.get("/v1/approvals").json(); assert {a["type"] for a in todo} == {"export", "segment"} and all(a["status"] == "pending" for a in todo), todo
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
    login(c, sm, ["marketing"], "mkt")
    # 6. 審計：行銷只看自己；admin 全看
    mine = c.get("/v1/audit").json(); assert mine and all(x.get("user") == "entra:mkt" for x in mine)
    login(c, sm, ["admin"], "adm"); allrows = c.get("/v1/audit").json(); assert len(allrows) > len(mine)
    # 7. CSRF：非 GET 沒帶 header → 403
    c.headers.pop("X-CSRF-Token"); assert c.post("/v1/semantic/compile", json=q).status_code == 403
    print(f"api self-test ok: iam(roles crud/assignments/simulate) compile+events+funnel+retention+path+attribution+users+profile+exports/approvals/reveals+integration(connections/tables/tasks dry-run)+dashboards(fake clickhouse) catalog audit={len(store.audit_tail())} routes={len(app.routes)}")


if __name__ == "__main__":
    main()
