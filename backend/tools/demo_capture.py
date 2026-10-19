"""示範模式固定資料擷取：對正在跑的單機 API 以各角色登入，把前端會呼叫的端點回應存成 web/src/demo/fixtures.json。
用法同 demo_fill（先 fill 再 capture）。固定資料只含合成黃金資料集，沒有真實客戶資料；密鑰在回應裡本來就是遮罩。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..auth.local import LocalAccounts
from .demo_fill import login

OUT = Path(__file__).resolve().parents[2] / "web" / "src" / "demo" / "fixtures.json"
DEMO_PROJECT = os.environ.get("DMP_DEMO_PROJECT", "golden")   # 示範資料查哪個 project（sinopac_demo＝100 萬人模擬資料）
ROLES = ["admin", "marketing", "analyst", "ops", "it"]
T = {"from": "2026-10-01", "to": "2026-10-16", "grain": "day", "pinned": True}
UQ = {"project": DEMO_PROJECT, "filters": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}], "behaviors": [{"event": "bond_deal", "count": {"op": "gte", "value": 3}, "time": {"from": "2026-10-01", "to": "2026-10-16"}}]}
GETS = ["/auth/providers", "/v1/dashboards", "/v1/bookmarks", "/v1/exports", "/v1/reveals", "/v1/segments", "/v1/segments?status=recycled", "/v1/semantic/catalog", "/v1/audit?n=200", "/v1/audit?n=100", "/v1/audit?n=500", "/v1/audit?n=1000",
        "/v1/approvals?scope=to_me", "/v1/approvals?scope=mine", "/v1/approvals?scope=all",
        "/v1/iam/roles", "/v1/iam/assignments", "/v1/iam/sessions", "/v1/iam/sessions?scope=all", "/v1/iam/users", "/v1/iam/changes", "/v1/iam/masking", "/v1/iam/modules",
        "/v1/settings", "/v1/sql/tables", "/v1/sql/saved", "/v1/sql/history", "/v1/sql/mid-tables",
        "/v1/tags", "/v1/tags?status=recycled", "/v1/tags/custom-fields", "/v1/destinations", "/v1/pushes", "/v1/deliveries", "/v1/exports/tasks",
        "/v1/integration/connectors", "/v1/integration/connections", "/v1/integration/tables", "/v1/integration/sync-tasks",
        "/v1/meta/events", "/v1/meta/properties", "/v1/meta/virtual-events", "/v1/meta/quarantine", "/v1/meta/change-requests", "/v1/meta/user-properties", "/v1/meta/oneid/rules", "/v1/meta/dimension-tables", "/v1/meta/dictionaries", "/v1/meta/virtual-properties",
        "/v1/migration/inventory", "/v1/migration/bridge", "/v1/migration/exports", "/v1/migration/reconcile?days=28&project=migration_demo",
        "/v1/ops/pipeline", "/v1/ops/query-perf?window=1h", "/v1/ops/query-perf?window=24h", "/v1/ops/query-perf?window=7d", "/v1/ops/batch-jobs", "/v1/ops/alerts", "/v1/ops/series?hours=1", "/v1/ops/series?hours=6", "/v1/ops/series?hours=24", "/v1/ops/notify", "/v1/ops/notify/history?limit=50"]
POSTS: list[tuple[str, dict[str, Any]]] = [
    ("/v1/analytics/events", {"project": DEMO_PROJECT, "metrics": [{"event": "bond_deal", "agg": "total", "alias": "deals"}, {"event": "bond_deal", "agg": "users", "alias": "deal_users"}], "group_by": ["platform"], "time": T}),
    ("/v1/analytics/funnel", {"project": DEMO_PROJECT, "steps": [{"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "bond_order", "alias": "委託"}, {"event": "bond_deal", "alias": "成交"}], "window": {"value": 7, "unit": "day"}, "time": T}),
    ("/v1/analytics/retention", {"project": DEMO_PROJECT, "initial": {"event": "app_login_success"}, "return": {"event": "app_login_success"}, "model": "interval", "unit": "week", "periods": 3, "time": {"from": "2026-09-20", "to": "2026-10-16", "pinned": True}}),
    ("/v1/analytics/path", {"project": DEMO_PROJECT, "anchor": {"event": "app_login_success"}, "steps": 3, "sample": None, "time": T}),
    ("/v1/analytics/attribution", {"project": DEMO_PROJECT, "target": {"event": "bond_deal", "value_field": "amount"}, "touches": [{"event": "$pageview", "alias": "瀏覽"}, {"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "app_login_success", "alias": "登入"}], "models": ["first", "linear"], "window_days": 7, "time": T}),
    ("/v1/semantic/compile", {"project": DEMO_PROJECT, "metrics": [{"event": "bond_deal", "agg": "total"}], "time": T}),
    ("/v1/users/estimate", UQ), ("/v1/users/list", {**UQ, "columns": ["tier", "lifecycle", "aum", "churn_score", "branch_id", "last_active_at"], "page_size": 50}),
    ("/v1/segments/estimate", {"rule": UQ}), ("/v1/iam/scope/simulate", {"role": "marketing", "scope": {}}),
]


def main() -> None:
    la = LocalAccounts(Path(os.environ["DMP_LOCAL_ACCOUNTS"]), allowed_roles=["*"]); pw = os.environ["DMP_SEED_PASSWORD"]
    sessions = {u: login(u, la, pw) for u in ROLES}
    adm = sessions["admin"]
    fx: dict[str, Any] = {"captured_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()), "project": DEMO_PROJECT, "roles": {}, "routes": {}}
    for u, s in sessions.items():
        fx["roles"][u] = s.call("GET", "/v1/iam/me")
    def put(method: str, path: str, body: Any) -> None:
        fx["routes"][f"{method} {path}"] = body
    for p in GETS:
        try:
            put("GET", p, adm.call("GET", p))
        except RuntimeError as e:
            print("skip", p, str(e)[:80])
    for p, b in POSTS:
        try:
            put("POST", p, adm.call("POST", p, b))
        except RuntimeError as e:
            print("skip", p, str(e)[:80])
    # 動態路徑：儀表板卡片、分群、匯出、連線、任務、事件、字典、用戶細查
    for d in fx["routes"].get("GET /v1/dashboards", []):
        det = adm.call("GET", f"/v1/dashboards/{d['id']}"); put("GET", f"/v1/dashboards/{d['id']}", det); put("GET", "/v1/dashboards/*", det)
        for c in det["cards"]:
            r = adm.call("POST", f"/v1/dashboards/{d['id']}/cards/{c['id']}/data", {}); put("POST", f"/v1/dashboards/{d['id']}/cards/{c['id']}/data", r); put("POST", "/v1/dashboards/*/cards/*/data", r)
        for m, sub, body in (("POST", "explain", {}), ("POST", "render", {"audience": "示範：債券部週會", "expires_days": 30}), ("GET", "renders", None), ("GET", "schedules", None)):
            r = adm.call(m, f"/v1/dashboards/{d['id']}/{sub}", body); put(m, f"/v1/dashboards/{d['id']}/{sub}", r); put(m, f"/v1/dashboards/*/{sub}", r)
    for sgm in fx["routes"].get("GET /v1/segments", []):
        for sub in ("versions", "lineage", "versions/latest/members?page_size=50"):
            try:
                r = adm.call("GET", f"/v1/segments/{sgm['id']}/{sub}"); put("GET", f"/v1/segments/{sgm['id']}/{sub}", r); put("GET", f"/v1/segments/*/{sub}", r)
            except RuntimeError:
                pass
        try:
            r = adm.call("POST", f"/v1/segments/{sgm['id']}/preview", {}); put("POST", f"/v1/segments/{sgm['id']}/preview", r); put("POST", "/v1/segments/*/preview", r)
        except RuntimeError:
            pass
    for c in fx["routes"].get("GET /v1/integration/connections", []):
        for sub, m in (("privileges", "GET"), ("test", "POST")):
            r = adm.call(m, f"/v1/integration/connections/{c['id']}/{sub}", {} if m == "POST" else None); put(m, f"/v1/integration/connections/{c['id']}/{sub}", r); put(m, f"/v1/integration/connections/*/{sub}", r)
    for t in fx["routes"].get("GET /v1/integration/sync-tasks", []):
        r = adm.call("GET", f"/v1/integration/sync-tasks/{t['id']}/runs?limit=20"); put("GET", f"/v1/integration/sync-tasks/{t['id']}/runs?limit=20", r); put("GET", "/v1/integration/sync-tasks/*/runs?limit=20", r)
        r = adm.call("POST", f"/v1/integration/sync-tasks/{t['id']}/preview", {"limit": 200}); put("POST", f"/v1/integration/sync-tasks/{t['id']}/preview", r); put("POST", "/v1/integration/sync-tasks/*/preview", r)
    for e in fx["routes"].get("GET /v1/meta/events", {}).get("items", [])[:40]:
        put("GET", f"/v1/meta/events/{e['name']}", adm.call("GET", f"/v1/meta/events/{e['name']}"))
    put("GET", "/v1/meta/events/*", adm.call("GET", "/v1/meta/events/bond_order"))
    for d in fx["routes"].get("GET /v1/meta/dictionaries", []):
        put("GET", f"/v1/meta/dictionaries/{d['property']}", adm.call("GET", f"/v1/meta/dictionaries/{d['property']}"))
    for j in fx["routes"].get("GET /v1/migration/exports", {}).get("items", []):
        r = adm.call("GET", f"/v1/migration/exports/{j['id']}"); put("GET", f"/v1/migration/exports/{j['id']}", r); put("GET", "/v1/migration/exports/*", r)
    put("GET", "/v1/migration/reconcile", fx["routes"]["GET /v1/migration/reconcile?days=28&project=migration_demo"])
    for bmk in fx["routes"].get("GET /v1/bookmarks", []):
        put("GET", f"/v1/bookmarks/{bmk['id']}", adm.call("GET", f"/v1/bookmarks/{bmk['id']}")); r = adm.call("POST", f"/v1/bookmarks/{bmk['id']}/data", {}); put("POST", f"/v1/bookmarks/{bmk['id']}/data", r)
    for m in fx["routes"].get("GET /v1/iam/users", [])[:12]:
        r = adm.call("GET", f"/v1/iam/users/{m['id']}"); put("GET", f"/v1/iam/users/{m['id']}", r); put("GET", "/v1/iam/users/*", r)
    rows = fx["routes"].get("POST /v1/users/list", {}).get("rows", [])
    if rows:
        oid = rows[0]["one_id"]; q = "reason=" + quote("示範：客訴單 C-1001")
        put("GET", "/v1/profiles/*", adm.call("GET", f"/v1/profiles/{oid}?{q}&project=golden"))
        put("GET", "/v1/profiles/*/timeline", adm.call("GET", f"/v1/profiles/{oid}/timeline?{q}&project=golden&limit=50"))
        put("GET", "/v1/profiles/*/identities", adm.call("GET", f"/v1/profiles/{oid}/identities?{q}&project=golden"))
    for p in ("/v1/audit/stats?days=7", "/v1/audit/stats?days=14", "/v1/audit/stats?days=30", "/v1/audit/stats?days=90", "/v1/audit?n=200", "/v1/audit?n=100", "/v1/audit?n=500", "/v1/audit?n=1000", "/v1/iam/users", "/v1/sql/history"):
        put("GET", p, adm.call("GET", p))                                       # 統計與明細最後擷取，才含上面查詢／細查產生的審計
    for t in fx["routes"].get("GET /v1/tags", []):
        for sub in ("distribution", "versions"):
            try:
                r = adm.call("GET", f"/v1/tags/{t['id']}/{sub}"); put("GET", f"/v1/tags/{t['id']}/{sub}", r); put("GET", f"/v1/tags/*/{sub}", r)
            except RuntimeError as e:
                print("skip tag", t["id"], sub, str(e)[:60])
        put("GET", f"/v1/tags/{t['id']}", adm.call("GET", f"/v1/tags/{t['id']}"))
    for pu in fx["routes"].get("GET /v1/pushes", []):
        r = adm.call("GET", f"/v1/pushes/{pu['id']}"); put("GET", f"/v1/pushes/{pu['id']}", r); put("GET", "/v1/pushes/*", r)
    for d in fx["routes"].get("GET /v1/destinations", []):
        r = adm.call("GET", f"/v1/destinations/{d['id']}"); put("GET", f"/v1/destinations/{d['id']}", r); put("GET", "/v1/destinations/*", r)
    for ot in fx["routes"].get("GET /v1/exports/tasks", []):                                 # 數據輸出：任務詳情、執行紀錄；預覽以分析師身分擷取（遮罩口徑）
        r = adm.call("GET", f"/v1/exports/tasks/{ot['id']}"); put("GET", f"/v1/exports/tasks/{ot['id']}", r); put("GET", "/v1/exports/tasks/*", r)
        r = adm.call("GET", f"/v1/exports/tasks/{ot['id']}/runs?limit=20"); put("GET", f"/v1/exports/tasks/{ot['id']}/runs?limit=20", r); put("GET", "/v1/exports/tasks/*/runs?limit=20", r)
    ot1 = next((t for t in fx["routes"].get("GET /v1/exports/tasks", []) if t["type"] == "once"), None)
    if ot1:
        try:
            body = {k: ot1[k] for k in ("name", "type", "content", "destination_id", "format", "purpose", "retention_days")}; body["project"] = DEMO_PROJECT
            put("POST", "/v1/exports/tasks/preview", sessions["analyst"].call("POST", "/v1/exports/tasks/preview", body))
        except RuntimeError as e:
            print("skip output preview", str(e)[:80])
    ex = []
    for e in fx["routes"].get("GET /v1/sql/tables", {}).get("examples", []):             # 自定義 SQL 範例：示範站以固定結果回放
        try:
            ex.append({"sql": e["sql"], "result": adm.call("POST", "/v1/sql/query", {"sql": e["sql"]})})
        except RuntimeError as err:
            print("skip sql example", str(err)[:80])
    put("POST", "/v1/sql/query/examples", ex)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"fixtures: {len(fx['routes'])} routes, {OUT.stat().st_size // 1024} KB → {OUT}")


if __name__ == "__main__":
    main()
