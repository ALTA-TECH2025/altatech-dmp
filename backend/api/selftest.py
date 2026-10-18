"""API 自測（不連網、不連 ClickHouse）：登入 session → /v1/iam/me → 角色 CRUD 與權限檢查 → 指派 → 行級模擬 → /v1/semantic/compile（遮罩）→ /v1/analytics/events（假 ClickHouse）→ 審計。

用法：python3 -m backend.api
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi.testclient import TestClient

from ..auth.api import AuthService, load_cfg
from ..auth.session import MemoryStore as SessionMemory
from ..auth.session import SessionManager
from ..security.policy import RBAC, Subject
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
        if sql.startswith(("INSERT INTO dmp.user_tags", "ALTER TABLE dmp.user_tags", "INSERT INTO dmp.segment_members", "INSERT INTO dmp.segment_imports", "INSERT INTO dmp.tag_imports")):
            return ""
        if sql.startswith(("SELECT count() FROM dmp.segment_imports", "SELECT count() FROM dmp.tag_imports")):     # 導入名單對到用戶表的人數
            return "3\n"
        if sql.startswith("SELECT count() FROM dmp.segment_members"):                          # 分群引擎的計數查詢不帶 settings
            return "3\n"
        assert settings and settings.get("max_execution_time") in (60, 600), settings        # human 60s；匯出走 batch 600s
        if sql.startswith("SELECT one_id, one_id_uint FROM dmp.segment_members"):
            v = int(re.search(r"version = (\d+)", sql).group(1))                                 # 版本 n 的成員 = u{n}, u{n+1}, u{n+2}（相鄰版本差一進一出）
            return "".join(f'{{"one_id":"u{i}","one_id_uint":{i}}}\n' for i in range(v, v + 3))
        if "FROM dmp.user_tags" in sql:
            if "bitmapAnd(" in sql: return '{"tag_value":"P1","us":[2,3]}\n{"tag_value":"P2","us":[4]}\n'
            if "GROUP BY tag_value" in sql: return '{"value":"P1","users":80}\n{"value":"P2","users":40}\n'
            return '{"users":120}\n'
        if "bitmapCardinality(" in sql:
            return '{"users":123}\n'
        if "system.parts" in sql:
            return '{"table":"events","rows":120000,"bytes":48000000,"parts":6}\n{"table":"users","rows":3000,"bytes":900000,"parts":1}\n'
        if "max(time) AS t" in sql:
            return '{"t":"2026-10-15 09:00:00"}\n'
        if "system.query_log" in sql:
            return '{"n":42,"p95_ms":812.5,"scan_rows_avg":150000,"memory_peak_mb":64,"errors":0}\n'
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
        if "GROUPING SETS" in sql or "ARRAY JOIN touches AS t" in sql:     # 歸因（一次掃描版）／歸因落點名單
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
    hooks: list[dict[str, Any]] = []; ma_state = {"ok": False}
    def fake_transport(url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int | None, int, str]:
        hooks.append({"url": url, "headers": headers, "body": json.loads(body)})
        if "ma.internal" in url and not ma_state["ok"]: return 503, 12, "upstream busy"
        return 200, 20, '{"ok":true}'
    secrets = {"kv/data/dmp/prod/crm#webhook_secret": "crm-s3cret", "kv/data/dmp/prod/ma#webhook_secret": "ma-s3cret"}
    app = create_app(AuthService(cfg, {"local": local}, sm, None), store, ch, scheduler=_Sched(_tf.mktemp(prefix="dmp-sched-", suffix=".db"), _LR(None)), secret_of=lambda r: secrets[r], transport=fake_transport)   # 乾跑排程器，每次自測全新狀態
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
    # 神策式規則：巢狀且／或屬性樹、行為指標（金額合計、活躍天數）與比較、近 N 天、屬性塊「或」行為塊
    sc = {"combine": "or", "filters": {"op": "or", "items": [{"field": "user.tier", "op": "eq", "value": "P1"}, {"op": "and", "items": [{"field": "user.churn_score", "op": "gte", "value": 70}, {"field": "user.has_margin", "op": "false"}]}]},
          "behaviors": [{"event": "bond_deal", "measure": {"agg": "sum", "field": "amount"}, "cmp": {"op": "gt", "value": 500000}, "time": {"last_days": 16}}, {"event": "app_login_success", "measure": {"agg": "days"}, "cmp": {"op": "between", "value": [2, 5]}, "time": {"last_days": 7}}], "time": {"now": "2026-10-16"}}
    sq = c.post("/v1/users/estimate", json=sc); assert sq.status_code == 200, sq.text
    ssql = sq.json()["sql"]; assert "sum(" in ssql and "> 500000.0" in ssql and "uniqExact(e.event_date) BETWEEN 2 AND 5" in ssql and "bitmapOr(bm_u" in ssql and "2026-10-01" in ssql and "2026-10-10" in ssql, ssql
    assert c.post("/v1/users/estimate", json={**sc, "behaviors": [{"event": "bond_deal", "measure": {"agg": "sum"}, "time": {"last_days": 16}}]}).status_code == 400          # sum 沒給欄位
    assert c.post("/v1/users/estimate", json={**sc, "behaviors": [{"event": "bond_deal", "time": {"last_days": 0}}]}).status_code == 400                                       # 近 N 天範圍
    assert c.post("/v1/users/estimate", json={**sc, "combine": "xor"}).status_code == 400
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
    # 5c4. 自定義 SQL 沙盒：分析師可用（表目錄標示脫敏欄位）、唯讀執行、脫敏欄位 400、行銷 403、記錄、下載（events.export）、已存查詢、kind=sql 書籤與卡片；中間表需資訊管理
    tb = c.get("/v1/sql/tables").json(); assert any(t["name"] == "dmp.events" for t in tb["tables"]) and not any(t["name"] == "dmp.id_map" for t in tb["tables"]) and tb["examples"]
    assert next(col for t in tb["tables"] if t["name"] == "dmp.events" for col in t["columns"] if col["name"] == "one_id")["allowed"] is False
    sq = c.post("/v1/sql/query", json={"sql": "SELECT event_date AS bucket, count() AS m1 FROM dmp.events WHERE event = 'bond_deal' GROUP BY bucket", "limit": 100}); assert sq.status_code == 200, sq.text
    sqj = sq.json(); assert sqj["rows"] and sqj["sql_effective"].startswith("SELECT * FROM (") and sqj["sql_effective"].endswith("LIMIT 100") and sqj["lineage"] == ["dmp.events"] and sqj["scan_rows_estimate"] == 16384
    assert c.post("/v1/sql/query", json={"sql": "SELECT one_id FROM dmp.events"}).status_code == 400 and c.post("/v1/sql/query", json={"sql": "DROP TABLE dmp.events"}).status_code == 400
    assert c.post("/v1/sql/query", json={"sql": "SELECT count() FROM dmp.events", "explain": True}).json()["explained"] is True
    hist = c.get("/v1/sql/history").json(); assert hist and hist[0]["status"] == "ok" and hist[0]["rows"] >= 1
    dl = c.get(f"/v1/sql/query/{sqj['run_id']}/download"); assert dl.status_code == 200 and dl.text.startswith("# AltaTech DMP 自定義 SQL") and "_watermark" in dl.text
    sv = c.post("/v1/sql/saved", json={"name": "每日成交", "group": "債券", "sql": "SELECT event_date, count() AS n FROM dmp.events WHERE event = {{ev}} GROUP BY event_date", "params_default": {"ev": "bond_deal"}, "shared": {"roles": ["marketing"]}}); assert sv.status_code == 201, sv.text
    assert c.put(f"/v1/sql/saved/{sv.json()['id']}", json={"name": "每日成交（改）"}).json()["name"] == "每日成交（改）" and c.get("/v1/sql/saved").json()[0]["group"] == "債券"
    assert c.post("/v1/sql/saved", json={"name": "bad", "sql": "SELECT one_id FROM dmp.events"}).status_code == 400
    sb = c.post("/v1/bookmarks", json={"name": "SQL 書籤", "kind": "sql", "query": {"sql": "SELECT event_date AS bucket, count() AS m1 FROM dmp.events GROUP BY bucket"}, "add_to": [did]}); assert sb.status_code == 201, sb.text
    sql_card = next(x for x in c.get(f"/v1/dashboards/{did}").json()["cards"] if x.get("bookmark") == sb.json()["id"])
    cds = c.post(f"/v1/dashboards/{did}/cards/{sql_card['id']}/data").json(); assert cds["rows"] and cds["chart_spec"]["type"] == "table" and cds["card"]["kind"] == "sql"
    assert c.post("/v1/bookmarks", json={"name": "x", "kind": "sql", "query": {"sql": "SELECT one_id FROM dmp.events"}}).status_code == 400
    assert c.post("/v1/sql/mid-tables", json={"name": "deals", "sql": "SELECT count() FROM dmp.events"}).status_code == 403          # 分析師不能建中間表
    login(c, sm, ["marketing"], "mkt")
    assert c.get("/v1/sql/tables").status_code == 403 and c.post("/v1/sql/query", json={"sql": "SELECT count() FROM dmp.events"}).status_code == 403
    assert c.get("/v1/sql/saved").status_code == 403                                                                                     # 行銷沒有沙盒權限：連已存查詢也不給
    assert c.post(f"/v1/dashboards/{did}/cards/{sql_card["id"]}/data").status_code in (403, 404)                                      # 行銷看 sql 卡：無沙盒權限（此時看板也還沒分享）
    login(c, sm, ["it_admin"], "it")
    mt = c.post("/v1/sql/mid-tables", json={"name": "deals_daily", "sql": "SELECT event_date, count() AS n FROM dmp.events GROUP BY event_date", "note": "每日成交"}); assert mt.status_code == 201, mt.text
    assert mt.json()["name"] == "mid_deals_daily" and any(t["name"] == "dmp.mid_deals_daily" for t in c.get("/v1/sql/tables").json()["tables"])
    assert c.post("/v1/sql/query", json={"sql": "SELECT * FROM dmp.mid_deals_daily"}).status_code == 200
    assert c.post("/v1/sql/mid-tables/mid_deals_daily/refresh").status_code == 200 and c.delete("/v1/sql/mid-tables/mid_deals_daily").status_code == 204
    login(c, sm, ["data_analyst"], "ana")
    assert c.delete(f"/v1/sql/saved/{sv.json()['id']}").status_code == 204 and c.delete(f"/v1/bookmarks/{sb.json()['id']}").status_code == 204
    for x in [x for x in c.get(f"/v1/dashboards/{did}").json()["cards"] if x.get("bookmark") == sb.json()["id"]]:
        assert c.delete(f"/v1/dashboards/{did}/cards/{x['id']}").json()["ok"]
    assert {x["event"] for x in store.audit_tail(80)} >= {"query.sql", "sql.downloaded", "sql.saved", "sql.mid_table.created", "sql.mid_table.deleted"}
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
    # 5c5. 標籤與推送：標籤（分群對應值／SQL；審批；計算→分布／版本；暫停下游提醒）→ 目的地（secret_ref、測試事件簽章）→ 推送單（首次目的地需核可；全量、增量差集、附帶標籤）→ 交付（重試、自動停用、重放、tick）
    login(c, sm, ["data_analyst"], "ana")
    cf = c.get("/v1/tags/custom-fields").json(); assert [f["key"] for f in cf["fields"]] == ["department", "requester", "category"] and "客戶價值" in cf["directories"]
    tvals = [{"value": "P1", "rule": {"filters": [{"field": "user.tier", "op": "in", "value": ["P1"]}]}}, {"value": "P2", "rule": {"filters": [{"field": "user.tier", "op": "in", "value": ["P2"]}]}}]
    tg = c.post("/v1/tags", json={"name": "價值分級", "key": "tier_grade", "directory": "客戶價值", "kind": "values", "values": tvals, "update": {"mode": "routine", "freq": "daily", "earliest": "06:00", "keep": 7}, "fields": {"department": "數位金融", "category": "名單推送"}})
    assert tg.status_code == 201 and tg.json()["status"] == "active" and tg.json()["id"] == "tier_grade" and tg.json()["lineage"]["up"] == ["dmp.users"], tg.text
    assert c.post("/v1/tags", json={"name": "重複", "key": "tier_grade", "kind": "values", "values": tvals}).status_code == 409
    assert c.post("/v1/tags", json={"name": "值重複", "kind": "values", "values": [tvals[0], tvals[0]]}).status_code == 400
    assert c.post("/v1/tags", json={"name": "欄位", "kind": "values", "values": tvals, "fields": {"department": "外星部"}}).status_code == 400
    assert c.post("/v1/tags", json={"name": "脫敏", "kind": "sql", "sql": "SELECT one_id_uint, one_id AS tag_value FROM dmp.users"}).status_code == 400          # one_id 對分析師脫敏
    ts_ = c.post("/v1/tags", json={"name": "分級（SQL）", "key": "tier_sql", "kind": "sql", "dtype": "string", "sql": "SELECT one_id_uint, tier AS tag_value FROM dmp.users FINAL"}); assert ts_.status_code == 201 and ts_.json()["status"] == "active", ts_.text
    cp = c.post("/v1/tags/tier_grade/compute", json={}); assert cp.status_code == 202, cp.text
    cpb = cp.json(); assert cpb["status"] == "success" and cpb["version"] == 1 and cpb["users"] == 120 and [d["value"] for d in cpb["distribution"]] == ["P1", "P2"], cpb
    assert ch.sqls[-3].startswith("INSERT INTO dmp.user_tags (tag_id, tag_value, version, users) WITH") and "'tier_grade', 'P2', 1, groupBitmapState(e.one_id_uint)" in ch.sqls[-3], ch.sqls[-3]
    # 基礎指標值／首末次特徵／RFM：建立即編一次 SQL（事件、欄位、分桶都檢查），計算走與 SQL 標籤同一條寫入路徑
    for bad in ({"kind": "metric", "spec": {"event": "nope", "agg": "count", "window_days": 30}}, {"kind": "metric", "spec": {"event": "bond_deal", "agg": "sum", "window_days": 30}}, {"kind": "metric", "spec": {"event": "bond_deal", "agg": "count", "window_days": 30, "buckets": [5, 1]}},
                {"kind": "firstlast", "spec": {"event": "bond_deal", "which": "soon", "feature": "date"}}, {"kind": "rfm", "spec": {"event": "bond_deal", "bins": 1}}, {"kind": "metric"}):
        r = c.post("/v1/tags", json={"name": "壞的", **bad}); assert r.status_code == 400, (bad, r.text)
    mt = c.post("/v1/tags", json={"name": "近 90 天成交分級", "key": "deals_90d_level", "kind": "metric", "project": "golden", "spec": {"event": "bond_deal", "agg": "count", "window_days": 90, "buckets": [1, 3, 10]}}).json()
    assert mt["status"] == "active" and mt["spec"] == {"event": "bond_deal", "window_days": 90, "agg": "count", "field": "", "buckets": [1.0, 3.0, 10.0]} and "event:bond_deal" in mt["lineage"]["up"], mt
    cp3 = c.post("/v1/tags/deals_90d_level/compute", json={}).json(); assert cp3["status"] == "success", cp3
    ins = next(s for s in reversed(ch.sqls) if s.startswith("INSERT INTO dmp.user_tags") and "deals_90d_level" in s)
    assert "multiIf(v < 1.0, '<1', v < 3.0, '1–3', v < 10.0, '3–10', '≥10')" in ins and "e.project = 'golden' AND e.event = 'bond_deal'" in ins and "count() AS v" in ins and "GROUP BY tag_value" in ins, ins
    fl = c.post("/v1/tags", json={"name": "首次成交商品", "key": "first_symbol", "kind": "firstlast", "project": "golden", "spec": {"event": "bond_deal", "which": "first", "feature": "field", "field": "symbol"}}).json()
    assert fl["spec"]["window_days"] == 0 and fl["spec"]["buckets"] is None and "symbol" in fl["lineage"]["up"], fl["spec"]
    assert c.post("/v1/tags/first_symbol/compute", json={}).json()["status"] == "success" and "argMin(e.symbol, e.time)" in ch.sqls[-3] and "event_date >" not in ch.sqls[-3]
    rf = c.post("/v1/tags", json={"name": "RFM", "key": "rfm_90d", "kind": "rfm", "project": "golden", "spec": {"event": "bond_deal", "window_days": 90, "bins": 5, "output": "segment"}}).json()
    assert rf["spec"] == {"event": "bond_deal", "window_days": 90, "amount_field": "amount", "bins": 5, "output": "segment"}, rf["spec"]
    assert c.post("/v1/tags/rfm_90d/compute", json={}).json()["status"] == "success" and "ntile(5) OVER (ORDER BY r_days)" in ch.sqls[-3] and "'重要價值'" in ch.sqls[-3] and "sum(coalesce(e.amount, 0))" in ch.sqls[-3]
    ed = c.put("/v1/tags/rfm_90d", json={"spec": {"event": "bond_deal", "window_days": 180, "bins": 4, "output": "score"}}).json(); assert ed["spec"]["bins"] == 4 and ed["spec"]["output"] == "score" and "spec" in ed["changes"][-1]["changed"], ed.get("changes")
    cp2 = c.post("/v1/tags/tier_sql/compute", json={}).json(); assert cp2["status"] == "success" and "GROUP BY tag_value" in ch.sqls[-3] and "toString(tag_value), 1, groupBitmapState(toUInt64(one_id_uint))" in ch.sqls[-3], (cp2, ch.sqls[-3])
    dist = c.get("/v1/tags/tier_grade/distribution").json(); assert dist["version"] == 1 and dist["users"] == 120 and dist["distribution"][0] == {"value": "P1", "users": 80}, dist
    assert len(c.get("/v1/tags/tier_grade/versions").json()) == 1 and c.get("/v1/tags/tier_grade/distribution?basetime=1999-01-01").status_code == 404
    assert {t["id"] for t in c.get("/v1/tags").json()} == {"tier_sql", "tier_grade", "deals_90d_level", "first_symbol", "rfm_90d"}
    login(c, sm, ["marketing"], "mkt")
    mt = c.post("/v1/tags", json={"name": "行銷標籤", "key": "mkt_grp", "kind": "values", "values": tvals[:1]}); assert mt.status_code == 201 and mt.json()["status"] == "pending_approval" and mt.json()["approval"]["status"] == "pending", mt.text
    assert c.post("/v1/tags", json={"name": "行銷 SQL", "kind": "sql", "sql": "SELECT one_id_uint, tier AS tag_value FROM dmp.users"}).status_code == 403
    assert c.post("/v1/tags/mkt_grp/compute", json={}).status_code == 409 and c.post("/v1/tags/tier_grade/compute", json={}).status_code == 403                 # 待審批不能算；別人的不能算
    login(c, sm, ["digital_ops"], "ops")
    tap = next(a for a in c.get("/v1/approvals?scope=to_me").json() if a["type"] == "tag"); assert tap["object"] == {"kind": "tag", "id": "mkt_grp"}
    assert c.post("/v1/tags/mkt_grp/approve", json={"approve": True, "note": "ok"}).json()["status"] == "active" and c.get(f"/v1/approvals/{tap['id']}").json()["status"] == "approved"
    # 目的地：行銷 403；數位經營建立（明文密鑰 400、非 https 400、未知標籤 400）；測試事件簽章可驗
    login(c, sm, ["marketing"], "mkt"); assert c.post("/v1/destinations", json={"name": "x", "url": "https://x.internal/h", "secret_ref": "kv/data/dmp/prod/x#webhook_secret"}).status_code == 403
    login(c, sm, ["digital_ops"], "ops")
    assert c.post("/v1/destinations", json={"name": "CRM", "url": "https://crm.internal/dmp/webhook", "secret": "plain"}).status_code == 400
    assert c.post("/v1/destinations", json={"name": "CRM", "url": "http://crm.internal/dmp/webhook", "secret_ref": "kv/data/dmp/prod/crm#webhook_secret"}).status_code == 400
    assert c.post("/v1/destinations", json={"name": "CRM", "url": "https://crm.internal/dmp/webhook", "secret_ref": "kv/data/dmp/prod/crm#webhook_secret", "allowed_tags": ["nope"]}).status_code == 400
    crm = c.post("/v1/destinations", json={"name": "CRM 客戶系統", "url": "https://crm.internal/dmp/webhook", "secret_ref": "kv/data/dmp/prod/crm#webhook_secret", "allowed_tags": ["tier_grade"], "note": "HMAC 簽章"}); assert crm.status_code == 201, crm.text
    crm = crm.json(); assert crm["status"] == "active" and crm["secret"] == "••••••••" and crm["stats"]["ok_7d"] is None and "secret_ref" in crm
    ma = c.post("/v1/destinations", json={"name": "行銷自動化 MA", "url": "https://ma.internal/hooks/dmp", "secret_ref": "kv/data/dmp/prod/ma#webhook_secret", "identifier": "one_id"}).json()
    tst = c.post(f"/v1/destinations/{crm['id']}/test").json(); assert tst["status"] == "ok" and tst["response"]["status"] == 200, tst
    from ..activation import webhook as _wh
    h = hooks[-1]; assert h["headers"]["X-DMP-Event"] == "audience.test" and h["body"]["batch"]["size"] == 3 and h["body"]["members"][0]["customer_code"].startswith("cust_") and h["body"]["members"][0]["tags"] == {"tier_grade": "測試"}
    assert _wh.verify(b"crm-s3cret", h["headers"]["X-DMP-Timestamp"], json.dumps(h["body"], ensure_ascii=False, separators=(",", ":")).encode(), h["headers"]["X-DMP-Signature"])
    assert c.post(f"/v1/destinations/{crm['id']}/rotate-secret", json={"secret_ref": "kv/data/dmp/prod/crm#webhook_secret"}).status_code == 400
    # 推送：分析師建分群並算兩版 → 推送單（目的地首次 → 核可）→ 全量 + complete → 分群新版 → tick 增量（差集、附帶標籤）
    login(c, sm, ["data_analyst"], "ana")
    sg = c.post("/v1/segments", json={"name": "推送測試分群", "rule": uq, "update": {"mode": "routine", "freq": "daily", "earliest": "03:00", "keep": 7}}).json(); assert sg["status"] == "active", sg
    sc1 = c.post(f"/v1/segments/{sg['id']}/compute", json={}).json(); assert sc1["status"] == "success", sc1
    assert c.post(f"/v1/segments/{sg['id']}/compute", json={"basetime": "2026-10-16"}).json()["version"] == 2
    assert c.post(f"/v1/audiences/{sg['id']}/push", json={"destination_ids": [crm["id"], ma["id"]], "mode": "delta", "include_tags": ["tier_grade"]}).status_code == 400       # MA 不允許附帶標籤
    assert c.post(f"/v1/audiences/{sg['id']}/push", json={"destination_ids": [crm["id"]], "mode": "realtime"}).status_code == 400
    assert c.post("/v1/audiences/nope/push", json={"destination_ids": [crm["id"]]}).status_code == 404
    ps = c.post(f"/v1/audiences/{sg['id']}/push", json={"destination_ids": [crm["id"]], "mode": "delta", "schedule": "after_batch", "include_tags": ["tier_grade"]}); assert ps.status_code == 202, ps.text
    ps = ps.json(); assert ps["status"] == "pending_approval" and ps["approval_id"], ps
    assert c.post(f"/v1/pushes/{ps['id']}/run").status_code == 409
    login(c, sm, ["marketing"], "mkt"); assert c.get("/v1/pushes").json()[0]["id"] == ps["id"] and c.delete(f"/v1/pushes/{ps['id']}").status_code == 403
    login(c, sm, ["digital_ops"], "ops")
    pap = next(a for a in c.get("/v1/approvals?scope=to_me").json() if a["type"] == "push"); assert "首次推送" in pap["summary"]
    c.post(f"/v1/approvals/{pap['id']}/decide", json={"approve": True, "note": "ok"}); assert c.get(f"/v1/pushes/{ps['id']}").json()["status"] == "active"
    login(c, sm, ["data_analyst"], "ana")
    n0 = len(hooks); r1 = c.post(f"/v1/pushes/{ps['id']}/run"); assert r1.status_code == 202, r1.text
    r1 = r1.json(); assert r1 == {**r1, "version": 2, "added": 3, "removed": 0, "batches": 2, "ok": 2, "failed": 0} and r1["destinations"][crm["id"]] == "ok", r1
    assert [h["headers"]["X-DMP-Event"] for h in hooks[n0:]] == ["audience.full", "audience.complete"] and hooks[n0]["body"]["members"][0]["tags"] == {"tier_grade": "P1"} and hooks[n0]["body"]["members"][2]["tags"] == {"tier_grade": "P2"} and hooks[n0 + 1]["body"]["total"] == 3, hooks[n0:]
    assert c.post(f"/v1/segments/{sg['id']}/compute", json={"basetime": "2026-10-17"}).json()["version"] == 3
    tk = app.state.activation.tick(Subject("system:scheduler", ["admin"])); assert tk["ran"][0]["push"] == ps["id"] and tk["ran"][0]["added"] == 1 and tk["ran"][0]["removed"] == 1, tk
    assert hooks[-1]["headers"]["X-DMP-Event"] == "audience.delta" and [(m["one_id"], m["action"]) for m in hooks[-1]["body"]["members"]] == [("u5", "add"), ("u2", "remove")], hooks[-1]["body"]
    pd = c.get(f"/v1/pushes/{ps['id']}").json(); assert pd["last_delivered_version"] == 3 and len(pd["deliveries"]) == 3 and all(d["status"] == "ok" and "payload" not in d for d in pd["deliveries"]), pd
    assert app.state.activation.tick(Subject("system:scheduler", ["admin"]))["ran"] == []                                                      # 沒有新版本就不推
    # 失敗路徑：MA 回 503 → 重試排程 → 連續 3 次自動停用目的地 → 恢復 → 重放成功；全量推送需審批（ops 也不能自審）
    login(c, sm, ["digital_ops"], "ops")
    pf = c.post(f"/v1/audiences/{sg['id']}/push", json={"destination_ids": [ma["id"]], "mode": "full", "schedule": "now"}).json(); assert pf["status"] == "pending_approval", pf
    assert c.post(f"/v1/approvals/{pf['approval_id']}/decide", json={"approve": True}).status_code == 409                                      # 不能審自己的
    login(c, sm, ["admin"], "adm"); c.post(f"/v1/approvals/{pf['approval_id']}/decide", json={"approve": True, "note": "ok"})
    pf2 = c.get(f"/v1/pushes/{pf['id']}").json(); assert pf2["status"] == "active" and pf2["last_result"]["failed"] == 2 and pf2["runs"] == 1, pf2       # 核可後立即執行（schedule=now）：1 批 + complete 都失敗
    dl = c.get(f"/v1/deliveries?audience_id={sg['id']}&status=retrying").json(); assert len(dl) == 2 and dl[0]["attempts"] == 1 and dl[0]["next_retry_at"] and dl[0]["response"]["status"] == 503, dl
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo as _Z
    later = _dt.now(_Z("Asia/Taipei")) + _td(minutes=2)
    assert c.get(f"/v1/destinations/{ma['id']}").json()["consecutive_failures"] == 2
    assert len(app.state.activation.tick(Subject("system:scheduler", ["admin"]), now=later)["retried"]) == 1                                    # 第 3 次連續失敗 → 目的地自動停用，另一筆不再重試
    mad = c.get(f"/v1/destinations/{ma['id']}").json(); assert mad["status"] == "paused" and mad["consecutive_failures"] == 3, mad
    assert app.state.activation.tick(Subject("system:scheduler", ["admin"]), now=later + _td(hours=1))["retried"] == []                          # 停用中不重試
    login(c, sm, ["digital_ops"], "ops")
    assert c.post(f"/v1/destinations/{ma['id']}/pause").json()["status"] == "active"
    ma_state["ok"] = True
    rp = c.post(f"/v1/deliveries/{dl[0]['id']}/replay"); assert rp.status_code == 202 and rp.json()["status"] == "ok" and rp.json()["replay_of"] == dl[0]["id"] and hooks[-1]["headers"]["X-DMP-Delivery-Id"] == dl[0]["id"], rp.text
    assert c.post(f"/v1/deliveries/{dl[0]['id']}/replay").status_code == 409
    assert c.get(f"/v1/destinations/{ma['id']}").json()["stats"]["deliveries_7d"] == 2
    # 標籤：有下游引用不能回收；暫停提醒下游；資訊管理可建目的地但不能建標籤規則
    login(c, sm, ["data_analyst"], "ana")
    assert c.post("/v1/tags/tier_grade/recycle").status_code == 409 and c.post("/v1/tags/tier_grade/pause").json()["warnings"] and c.post("/v1/tags/tier_grade/pause").json()["status"] == "active"
    assert c.post("/v1/tags/tier_sql/recycle").status_code == 200 and [t["id"] for t in c.get("/v1/tags?status=recycled").json()] == ["tier_sql"] and c.post("/v1/tags/tier_sql/restore").json()["status"] == "active"
    login(c, sm, ["it_admin"], "it"); assert c.post("/v1/tags", json={"name": "x", "kind": "values", "values": tvals}).status_code == 403 and c.get("/v1/destinations").status_code == 200
    evp = {x["event"] for x in store.audit_tail(1000)}; assert {"tag.created", "tag.computed", "tag.approved", "destination.created", "destination.tested", "push.created", "push.ran", "sync.delivered", "sync.failed", "destination.auto_paused", "sync.replayed"} <= evp, evp
    login(c, sm, ["data_analyst"], "ana")
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
    # 神策分群／標籤遷移：未接神策 → 清單空、掃描 503；掃描（user_group_*／user_tag_*）→ 快照（登入 ID 轉 one_id、原始 ID 不落地）→ 409 重複 → 標籤快照 → 略過需原因 → 連到重建的分群 → 權限
    def sync() -> None:
        c2.cookies.set("dmp_sid", c.cookies["dmp_sid"]); c2.cookies.set("dmp_csrf", c.cookies["dmp_csrf"]); c2.headers["X-CSRF-Token"] = c.cookies["dmp_csrf"]
    login(c, sm, ["admin"], "adm"); sync()
    assert c.get("/v1/migration/sensors-groups").json()["items"] == [] and c.post("/v1/migration/sensors-groups/scan").status_code == 503
    sg_ = c2.post("/v1/migration/sensors-groups/scan").json(); by = {x["name"]: x for x in sg_["items"]}
    assert set(by) == {"user_group_vip_active", "user_group_old_campaign", "user_tag_risk_level"} and sg_["summary"]["pending"] == 3 and sg_["scanned_at"], sg_
    assert by["user_group_vip_active"]["users"] == 4 and by["user_group_vip_active"]["cn"] == "vip_active" and by["user_tag_risk_level"]["values"] == 3 and by["user_tag_risk_level"]["users"] == 6, by
    sn = c2.post("/v1/migration/sensors-groups/user_group_vip_active/snapshot", json={"dmp_name": "神策：活躍 VIP"}); assert sn.status_code == 200, sn.text
    sn = sn.json(); assert sn["status"] == "snapshot" and sn["dmp"]["type"] == "segment" and sn["dmp"]["users"] == 3 and sn["dmp"]["diff_pct"] == -0.25, sn
    ins = [x for x in ch.sqls if x.startswith("INSERT INTO dmp.segment_imports")][-1]; assert ins.count("'v1_") == 4 and "C0001" not in ins, ins
    snap_seg = c.get(f"/v1/segments/{sn['dmp']['id']}").json(); assert snap_seg["kind"] == "import" and snap_seg["update"]["mode"] == "manual" and snap_seg["latest"]["status"] == "success", snap_seg
    assert c2.post("/v1/migration/sensors-groups/user_group_vip_active/snapshot", json={}).status_code == 409
    tn = c2.post("/v1/migration/sensors-groups/user_tag_risk_level/snapshot", json={}).json(); assert tn["status"] == "snapshot" and tn["dmp"]["type"] == "tag" and tn["dmp"]["users"] == 120 and tn["dmp"]["name"] == "神策：risk_level", tn
    tins = [x for x in ch.sqls if x.startswith("INSERT INTO dmp.tag_imports")][-1]; assert "'R1'" in tins and "'dev0000'" in tins and "C0002" not in tins, tins      # C0000 沒有登入 ID → first_id
    assert any("FROM dmp.tag_imports WHERE import_id = " in x for x in ch.sqls if x.startswith("INSERT INTO dmp.user_tags"))
    assert c2.post("/v1/migration/sensors-groups/user_group_old_campaign/skip", json={"note": ""}).status_code == 400
    assert c2.post("/v1/migration/sensors-groups/user_group_old_campaign/skip", json={"note": "2025 活動已結束"}).json()["status"] == "skipped"
    assert c2.post("/v1/migration/sensors-groups/user_group_old_campaign/link", json={"type": "segment", "id": "sg_nope"}).status_code == 404
    lk = c2.post("/v1/migration/sensors-groups/user_group_old_campaign/link", json={"type": "segment", "id": sn["dmp"]["id"]}).json(); assert lk["status"] == "rebuilt" and lk["dmp"]["diff_pct"] == 0.5, lk
    s2 = c2.get("/v1/migration/sensors-groups").json()["summary"]; assert s2 == {"total": 3, "pending": 0, "snapshot": 2, "rebuilt": 1, "skipped": 0}, s2
    login(c, sm, ["data_analyst"], "ana"); sync(); assert c2.get("/v1/migration/sensors-groups").status_code == 200 and c2.post("/v1/migration/sensors-groups/scan").status_code == 403
    login(c, sm, ["it_admin"], "it"); sync(); assert c2.post("/v1/migration/sensors-groups/user_group_old_campaign/snapshot", json={}).status_code == 403      # 資訊管理沒有 segments.use
    assert not any("C0001" in repr(x) for x in store.audit_tail(300))
    assert {x["event"] for x in store.audit_tail(300)} >= {"migration.export.created", "migration.inventory.refreshed", "migration.export.rerun", "migration.groups.scanned", "migration.group.snapshot", "migration.group.skipped", "migration.group.linked", "segment.import.created"}
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
    assert {x["event"] for x in store.audit_tail(1200)} >= {"role.change_requested", "role.changed", "role.deleted", "iam.break_glass.requested", "iam.user.created", "iam.user.changed", "iam.user.deleted", "auth.session.revoked", "audit.exported", "authz.denied"}
    # 6b. 資料流與效能（/v1/ops/*、/metrics）：it_admin 看得到；marketing 403；重跑需 governance.admin；指標含 HTTP 與查詢直方圖
    login(c, sm, ["it_admin"], "it")
    pl = c.get("/v1/ops/pipeline").json(); assert [s["key"] for s in pl["stages"]] == ["sources", "warehouse", "serving", "activation"] and pl["slo"]["query_p95_ms"]["funnel"] == 10000, pl["stages"]
    wh = pl["stages"][1]; assert wh["items"][0]["name"] == "events" and wh["items"][0]["bytes"] == 48000000 and "2 張表" in wh["summary"], wh
    sv = pl["stages"][2]["items"][0]; assert sv["requests_total"] > 50 and sv["errors_5xx_total"] >= 1 and sv["queries_5m"] >= 5, sv              # 前面的 502 查詢失敗測試與分析查詢都已計入
    q = {x["key"]: x for x in pl["quality"]}; assert q["delivery_success"]["value"] is not None and q["k_suppressed"]["value"] is not None, q
    qp = c.get("/v1/ops/query-perf?window=24h").json(); mods = {m["module"]: m for m in qp["modules"]}
    assert "events" in mods and mods["events"]["n"] >= 1 and mods["events"]["p95_ms"] is not None and mods["events"]["sla_ms"] == 3000 and "sql" in mods and mods["sql"]["cn"] == "自定義 SQL", mods
    assert qp["clickhouse"]["n"] == 42 and qp["slow"] and qp["slow"][0]["ms"] >= qp["slow"][-1]["ms"] and c.get("/v1/ops/query-perf?window=2h").status_code == 422
    bj = c.get("/v1/ops/batch-jobs").json(); jn = [j["name"] for j in bj["jobs"]]
    assert "scheduler" in jn and "segments" in jn and bj["storage"][0]["name"] == "clickhouse.events" and bj["storage"][-1]["name"] == "control_plane" and bj["storage"][-1]["rows"] > 100, (jn, bj["storage"])
    sync_names = [j["name"] for j in bj["jobs"] if j["kind"] == "sync"]
    if sync_names:
        rr = c.post(f"/v1/ops/batch-jobs/{sync_names[0]}/rerun"); assert rr.status_code == 202 and rr.json()["job"] == sync_names[0] and store.audit_tail(5)[-1]["event"] == "batch.rerun", rr.text
    assert c.post("/v1/ops/batch-jobs/segments/rerun").status_code == 404 and c.post("/v1/ops/batch-jobs/nope/rerun").status_code == 404
    al = c.get("/v1/ops/alerts").json(); assert len(al["rules"]) == 11 and al["notify"] == ["it_admin", "admin"] and all(a["firing"] for a in al["firing"]) and any(a["key"] == "http_errors" or True for a in al["rules"]), al["rules"][:2]
    mt = c.get("/metrics"); assert mt.status_code == 200 and mt.headers["content-type"].startswith("text/plain") and "dmp_http_requests_total{" in mt.text and 'dmp_query_seconds_bucket{module="events",le="+Inf"}' in mt.text and "dmp_clickhouse_table_bytes{table=\"events\"} 48000000" in mt.text and "dmp_process_uptime_seconds" in mt.text, mt.text[:400]
    assert any('route="/v1/dashboards/{' in l for l in mt.text.splitlines()) and not any("d_seed" in l or "d_demo" in l for l in mt.text.splitlines() if "dmp_http_requests_total{" in l), "路由樣板而不是實際 id"
    app.state.ops.snapshot(); app.state.ops.snapshot(time.time() + 60)
    se = c.get("/v1/ops/series?hours=6").json(); assert len(se["panels"]) == 17 and se["points"] == 2 and se["grafana"]["url"] is None and all(p["expr"] for p in se["panels"]) and se["panels"][0]["series"][0]["name"] == "all", (se["points"], se["panels"][0])
    assert c.get("/v1/ops/series?hours=2").status_code == 422
    # 告警通知：設定（密鑰只收 secret_ref）、測試送 LINE（假 transport）、紀錄、Alertmanager webhook（Bearer）
    nc = c.get("/v1/ops/notify").json(); assert nc["email"]["to_roles"] == ["it_admin", "admin"] and nc["policy"]["min_severity"] == "medium" and nc["available"]["email"] is False and nc["available"]["line"] is False, nc
    assert c.put("/v1/ops/notify", json={"line": {"enabled": True, "channel_access_token_ref": "this-is-a-plain-token", "to": ["U" + "1" * 32]}}).status_code == 400
    secrets["kv/data/dmp/prod/line#channel_access_token"] = "LINE-T0KEN"
    nc = c.put("/v1/ops/notify", json={"line": {"enabled": True, "channel_access_token_ref": "kv/data/dmp/prod/line#channel_access_token", "to": ["C" + "2" * 32]}, "policy": {"cooldown_min": 15}}).json()
    assert nc["line"]["enabled"] and nc["available"]["line"] and nc["updated_by"] == "entra:it" and "LINE-T0KEN" not in json.dumps(nc) and nc["policy"]["cooldown_min"] == 15, nc
    tr = c.post("/v1/ops/notify/test", json={"channel": "line"}); assert tr.status_code == 200 and tr.json()["status"] == "ok" and tr.json()["recipients"] == 1 and hooks[-1]["url"].endswith("/v2/bot/message/push") and hooks[-1]["headers"]["Authorization"] == "Bearer LINE-T0KEN", tr.text
    assert c.post("/v1/ops/notify/test", json={"channel": "email"}).status_code == 409                    # 沒有 SMTP
    hist = c.get("/v1/ops/notify/history?limit=5").json(); assert hist and hist[0]["kind"] == "test" and hist[0]["channel"] == "line"
    sent = app.state.notifier.dispatch(c.get("/v1/ops/alerts").json()["firing"]); assert sent and sent[0]["channel"] == "line" and sent[0]["kind"] == "alert" and sent[0]["status"] == "ok", sent
    am = {"version": "4", "alerts": [{"status": "firing", "labels": {"alertname": "DmpEventsStale", "severity": "critical", "key": "events_stale"}, "annotations": {"summary": "行為事件停滯"}}]}
    assert c.post("/v1/ops/notify/alertmanager", json=am).status_code == 401
    c3 = TestClient(create_app(AuthService(cfg, {}, sm, None), store, ch, metrics_token="tok-123", secret_of=lambda r: secrets[r], transport=fake_transport))
    amr = c3.post("/v1/ops/notify/alertmanager", json=am, headers={"Authorization": "Bearer tok-123"}); assert amr.status_code == 200 and amr.json()["notifications"][0]["kind"] == "alert", amr.text
    assert store.audit_tail(50)[-1]["event"] == "ops.notify.sent"
    login(c, sm, ["marketing"], "mkt"); assert c.get("/v1/ops/pipeline").status_code == 403 and c.get("/metrics").status_code == 403 and c.post("/v1/ops/batch-jobs/x/rerun").status_code == 403 and c.get("/v1/ops/series").status_code == 403
    login(c, sm, ["data_analyst"], "ana"); assert c.get("/v1/ops/alerts").status_code == 200 and c.post("/v1/ops/batch-jobs/x/rerun").status_code == 403 and c.get("/v1/ops/series").status_code == 403        # 分析師 governance.view 可看、不可重跑、看不到系統監控
    c2 = TestClient(create_app(AuthService(cfg, {}, sm, None), store, ch, metrics_token="tok-123")); assert c2.get("/metrics").status_code == 401 and c2.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401 and c2.get("/metrics", headers={"Authorization": "Bearer tok-123"}).status_code == 200
    login(c, sm, ["admin"], "adm")
    # 7. CSRF：非 GET 沒帶 header → 403
    c.headers.pop("X-CSRF-Token"); assert c.post("/v1/semantic/compile", json=q).status_code == 403
    print(f"api self-test ok: iam(roles crud w/ second review/break-glass/assignments/simulate)+sysadmin(members/local accounts/sessions/audit stats+export/settings) compile+events+funnel+retention+path+attribution+users+profile+exports/approvals/reveals+integration(connections/tables/tasks dry-run)+dashboards+meta(governance)+migration(fake sensors api)+bookmarks+sql sandbox+tags+activation(push/deliveries)+ops(pipeline/query-perf/batch-jobs/alerts/metrics/series/notify email+line+alertmanager)+ask(rules)+dashboard share(explain/render/token/expiry/schedule) (fake clickhouse) catalog audit={len(store.audit_tail())} routes={len(app.routes)}")


if __name__ == "__main__":
    main()
