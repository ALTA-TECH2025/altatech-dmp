"""示範資料填充：對「正在跑的單機 API」以五種角色帳號操作一輪，讓控制面有分群、審批、匯出、揭露、數據整合、治理登記等紀錄。
只打 HTTP（與使用者操作同一條路），不直接改 store。用法：
  DMP_API=http://127.0.0.1:8000 DMP_LOCAL_ACCOUNTS=~/dmp-local/accounts.json DMP_SEED_PASSWORD=... python3 -m backend.tools.demo_fill
前提：run_local.sh up + seed 已跑過（帳號 admin/marketing/analyst/ops/it 存在）。可重複執行（已存在的項目略過）。"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib import request as _rq

from ..auth.local import LocalAccounts, totp_code

API = os.environ.get("DMP_API", "http://127.0.0.1:8000")
# 神策式規則：用戶屬性樹（且）× 行為（近 N 天以計算日回推、指標比較、事件屬性）
UQ = {"project": "golden", "filters": {"op": "and", "items": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}]},
      "behaviors": [{"event": "bond_deal", "measure": {"agg": "count"}, "cmp": {"op": "gte", "value": 3}, "time": {"last_days": 30}}]}


class Session:
    def __init__(self, user: str):
        self.user, self.cookies, self.csrf = user, {}, ""

    def call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201, 202)) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = _rq.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json", "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()), **({"X-CSRF-Token": self.csrf} if self.csrf else {})})
        try:
            with _rq.urlopen(req, timeout=120) as r:
                for h, v in r.headers.items():
                    if h.lower() == "set-cookie":
                        k, _, rest = v.partition("="); self.cookies[k.strip()] = rest.split(";")[0]
                txt = r.read().decode(); code = r.status
        except Exception as e:  # HTTPError
            code = getattr(e, "code", 0); txt = e.read().decode() if hasattr(e, "read") else str(e)
        if code not in ok:
            raise RuntimeError(f"{method} {path} → {code} {txt[:200]}")
        self.csrf = self.cookies.get("dmp_csrf", self.csrf)
        if not txt:
            return None
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            raise RuntimeError(f"{method} {path} → 非 JSON 回應（{txt[:40]!r}）") from None


def login(user: str, la: LocalAccounts, password: str) -> Session:
    """同一個 TOTP 時間格不可重用、每帳號每分鐘 5 次：被擋就等下一格／下一分鐘再試（工具用，使用者不會這樣連登）。"""
    for _ in range(6):
        s = Session(user)
        try:
            s.call("POST", "/auth/ldap", {"provider": "local", "username": user, "password": password})
            code = totp_code(la.data["accounts"][user]["totp_secret"])
            s.call("POST", "/auth/ldap", {"provider": "local", "username": user, "password": password, "totp": code})
            return s
        except RuntimeError as e:
            msg = str(e)
            if "已使用" in msg or "驗證碼" in msg:
                time.sleep(30 - time.time() % 30 + 1)
            elif "頻繁" in msg:
                time.sleep(61)
            else:
                raise
    raise RuntimeError(f"login {user} failed after retries")


def main() -> None:
    la = LocalAccounts(Path(os.environ["DMP_LOCAL_ACCOUNTS"]), allowed_roles=["*"])
    pw = os.environ["DMP_SEED_PASSWORD"]
    adm, ana, ops, it, mkt = (login(u, la, pw) for u in ("admin", "analyst", "ops", "it", "marketing"))
    # 1. 分群：分析師建規則分群（自動核准？否：數據分析建立直接 active；行銷建立需審批）
    segs = {s["name"]: s for s in ana.call("GET", "/v1/segments")}
    if "高價值活躍交易客" not in segs:
        ana.call("POST", "/v1/segments", {"name": "高價值活躍交易客", "kind": "rule", "rule": UQ, "schedule": {"mode": "daily"}, "note": "P1／P2 且近期成交 ≥ 3 筆"})
    if "行銷：沉睡客喚醒名單" not in segs:
        mkt.call("POST", "/v1/segments", {"name": "行銷：沉睡客喚醒名單", "kind": "rule", "rule": {"project": "golden", "filters": [{"field": "user.lifecycle", "op": "in", "value": ["沉睡"]}]}, "note": "推播喚醒"})
    # 2. 匯出申請（分析師）→ 數位經營審批其中一件
    exps = ana.call("GET", "/v1/exports")
    if not exps:
        ana.call("POST", "/v1/exports", {"kind": "users", "rule": UQ, "purpose": "Q4 名單交付 MA（保存 30 天）", "retention_days": 30})
        ana.call("POST", "/v1/exports", {"kind": "users", "rule": {"project": "golden", "filters": [{"field": "user.aum_bucket", "op": "in", "value": ["≥1000萬"]}]}, "purpose": "財富管理 VIP 活動邀請名單", "retention_days": 14})
    pend = ops.call("GET", "/v1/approvals?scope=to_me")
    for a in [x for x in pend if x.get("status") == "pending"][:1]:
        with contextlib.suppress(RuntimeError):
            ops.call("POST", f"/v1/approvals/{a['id']}/decide", {"approve": True, "note": "用途明確，30 天到期自動刪除"})
    # 3. 揭露申請（分析師對一位用戶）
    lst = ana.call("POST", "/v1/users/list", {**UQ, "columns": ["tier", "aum", "churn_score"], "page_size": 5})
    oid = lst["rows"][0]["one_id"] if lst.get("rows") else None
    if oid and not ana.call("GET", "/v1/reveals"):
        for field in ("one_id", "customer_code", "churn_score"):            # 對分析師遮罩的欄位才能申請揭露
            try:
                ana.call("POST", "/v1/reveals", {"field": field, "one_id": oid, "reason": "客訴單 C-1001：確認客戶身分與資產區間是否符合 VIP 門檻", "hours": 4}); break
            except RuntimeError:
                continue
    # 4. 數據整合（資訊管理）：連線 → 數據表（合成 CSV）→ 同步任務 → 立即執行
    conns = {c["id"] for c in it.call("GET", "/v1/integration/connections")}
    if "dwh_trade" not in conns:
        it.call("POST", "/v1/integration/connections", {"id": "dwh_trade", "type": "mssql", "auth_method": "password", "fields": {"host": "sql.internal", "port": 1433, "database": "TRADE", "username": "dmp_ro"}, "secret_refs": {"password": "kv/data/dmp/prod/dwh_trade#password"}, "owner": "dba"})
        it.call("POST", "/v1/integration/connections/dwh_trade/test")
    if "ga_bq" not in conns:
        it.call("POST", "/v1/integration/connections", {"id": "ga_bq", "type": "bigquery", "auth_method": "workload_identity", "fields": {"project_id": "ga-export", "dataset": "analytics_1", "location": "asia-east1"}})
        it.call("POST", "/v1/integration/connections/ga_bq/test")
    tables = {t["id"] for t in it.call("GET", "/v1/integration/tables")}
    if "dwh_trade.dbo.fact_order_hist" not in tables:
        from ..integration.e2e_clickhouse import synth
        work = Path(os.environ.get("DMP_DEMO_WORK") or tempfile.mkdtemp(prefix="dmp-demo-")); work.mkdir(parents=True, exist_ok=True); files = synth(work, 300, 11)
        it.call("POST", "/v1/integration/tables", {"connection": "dwh_trade", "table": "dbo.fact_order_hist", "pk": ["ORD_NO", "STATUS"], "incremental_field": "UPDATED_AT", "source": {"kind": "csv", "path": str(files["orders"])}})
    tasks = {t["id"] for t in it.call("GET", "/v1/integration/sync-tasks")}
    if "sync_orders" not in tasks:
        it.call("POST", "/v1/integration/sync-tasks", {"id": "sync_orders", "cn": "委託事實表 → 委託／成交事件", "model_path": "backend/integration/models.example.yaml", "source": {"table_id": "dwh_trade.dbo.fact_order_hist"}, "schedule": {"interval_min": 15}, "mode": "incremental", "schema_change": "pause_and_notify"})
        it.call("POST", "/v1/integration/sync-tasks/sync_orders/run")
    # 5. 治理：登記草稿事件、變更單、虛擬事件、字典上傳（資訊管理）；admin 建一筆本地指派
    evs = {e["name"] for e in it.call("GET", "/v1/meta/events")["items"]}
    if "fund_redeem" not in evs:
        it.call("POST", "/v1/meta/events", {"name": "fund_redeem", "cn": "基金贖回", "group": "基金（數據中台）", "entity": "account", "source": "基金平台", "kind": "business", "owner": "財富管理", "status": "draft", "properties": [{"key": "fund_code", "type": "string", "sensitivity": "L1"}, {"key": "amount", "type": "number", "sensitivity": "L2"}]})
    if not it.call("GET", "/v1/meta/change-requests"):
        it.call("PATCH", "/v1/meta/events/bond_order", {"properties": [{"key": k, "type": "string", "sensitivity": "L1"} for k in ("order_no", "symbol", "market", "side", "order_type", "price", "qty", "amount", "channel", "trade_kind")], "reason": "補 trade_kind（現股／融資／融券）"}, ok=(200, 202))
    if not any(v["name"] == "v.fund_any" for v in it.call("GET", "/v1/meta/virtual-events")):
        it.call("POST", "/v1/meta/virtual-events", {"name": "v.fund_any", "cn": "任一基金行為", "owner": "財富管理", "members": [{"event": "sip_purchase"}, {"event": "bond_deal", "filters": [{"field": "market", "op": "eq", "value": "FUND"}]}]})
    it.call("PUT", "/v1/meta/dictionaries/reason_code?mode=append", {"entries": {"E099": "系統維護", "SYS04": "委託閘道逾時"}})
    if not it.call("GET", "/v1/migration/exports")["items"]:
        it.call("POST", "/v1/migration/exports", {"dataset": "events", "date_from": "2026-10-01", "date_to": "2026-10-03", "method": "api", "note": "抽樣校驗（查詢 API）"})
    # 6. 權限中心：自訂角色（送審 → 數位經營核可）、本機帳號 vendor-a、本地指派；留一筆角色修改與一筆臨時提權待複核，示範「變更審核」
    grants = {"overview": ["view"], "events": ["view", "use"], "funnel": ["view", "use"], "segments": ["view"], "ai_ask": ["use"], "audit": ["view"]}
    if "custom_vendor_analyst" not in {r["key"] for r in adm.call("GET", "/v1/iam/roles")}:
        r = adm.call("POST", "/v1/iam/roles", {"key": "custom_vendor_analyst", "name": "外包分析（專案限定）", "description": "外包廠商：只看 golden 專案、回溯 180 天、不可匯出", "scope": "project", "lookback_days": 180, "grants": grants})
        if isinstance(r, dict) and r.get("approval"):
            ops.call("POST", f"/v1/approvals/{r['approval']}/decide", {"approve": True, "note": "專案合約期內，範圍與回溯天數符合外包規範"})
    members = {m["username"]: m for m in adm.call("GET", "/v1/iam/users")}
    if "vendor-a" not in members:
        adm.call("POST", "/v1/iam/users", {"username": "vendor-a", "password": pw, "roles": ["custom_vendor_analyst"], "name": "外包分析（廠商 A）", "email": "a@vendor.example"})
        members = {m["username"]: m for m in adm.call("GET", "/v1/iam/users")}
    vid = members["vendor-a"]["id"]
    if not [a for a in adm.call("GET", "/v1/iam/assignments") if a.get("user_id") == vid]:
        adm.call("POST", "/v1/iam/assignments", {"user_id": vid, "roles": ["custom_vendor_analyst"], "scope": {"projects": ["golden"], "lookback_days": 180}, "expires_at": "2026-12-31", "reason": "外包分析專案（合約至 2026-12-31）"})
    changes = adm.call("GET", "/v1/iam/changes")
    if not any(c["kind"] == "break_glass" for c in changes) and "it" in members:
        adm.call("POST", "/v1/iam/assignments", {"user_id": members["it"]["id"], "roles": ["admin"], "break_glass": True, "hours": 2, "reason": "事故單 INC-2041：凌晨批次失敗，需調整排程與重跑"})
    if not any(c["kind"] == "role_change" and c.get("op") == "update" for c in changes):
        adm.call("PUT", "/v1/iam/roles/custom_vendor_analyst", {"grants": {**grants, "path": ["view", "use"]}})
    for sess, path in ((mkt, "/v1/iam/roles"), (mkt, "/v1/settings"), (ana, "/v1/iam/users"), (mkt, "/v1/audit/export")):   # 幾筆權限拒絕，讓看板有「權限拒絕」
        with contextlib.suppress(RuntimeError):
            sess.call("GET", path)
    # 7a. 書籤：分析師存三個書籤（分享給行銷），其中兩個放進「行銷週報」看板
    if not ana.call("GET", "/v1/bookmarks"):
        T2 = {"from": "2026-10-01", "to": "2026-10-16", "grain": "day"}
        b1 = ana.call("POST", "/v1/bookmarks", {"name": "成交筆數（日）", "description": "bond_deal 每日總次數，口徑同神策『成交』", "kind": "events", "chart": "line", "shared": {"roles": ["marketing", "digital_ops"]},
                                               "query": {"project": "golden", "metrics": [{"event": "bond_deal", "agg": "total", "alias": "deals"}], "time": T2}, "new_dashboard": "行銷週報", "width": 2})
        wid = b1["added_to"][0]["dashboard"]
        ana.call("POST", "/v1/bookmarks", {"name": "看盤 → 委託 → 成交（7 天窗）", "kind": "funnel", "shared": {"roles": ["marketing"]}, "add_to": [wid],
                                          "query": {"project": "golden", "steps": [{"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "bond_order", "alias": "委託"}, {"event": "bond_deal", "alias": "成交"}], "window": {"value": 7, "unit": "day"}, "order": "loose", "metric": "users", "time": {"from": "2026-10-01", "to": "2026-10-16"}}})
        ana.call("POST", "/v1/bookmarks", {"name": "登入後 4 週留存", "kind": "retention", "query": {"project": "golden", "initial": {"event": "app_login_success"}, "return": {"event": "app_login_success"}, "model": "interval", "unit": "week", "periods": 3, "time": {"from": "2026-09-20", "to": "2026-10-16"}}})
    # 7. 儀表板定時寄送：範例看板留一筆待審批
    seed = next((d for d in adm.call("GET", "/v1/dashboards") if d.get("seed")), None)
    if seed and not adm.call("GET", f"/v1/dashboards/{seed['id']}/schedules"):
        adm.call("POST", f"/v1/dashboards/{seed['id']}/schedules", {"weekday": 0, "hour": 8, "audience": {"roles": ["digital_ops", "marketing"]}, "expires_days": 14})
    # 8. 自定義 SQL：分析師跑範例、存兩個查詢（分享給數位經營）；資訊管理建一張中間表
    if not ana.call("GET", "/v1/sql/saved"):
        cat = ana.call("GET", "/v1/sql/tables")
        for e in cat["examples"]:
            with contextlib.suppress(RuntimeError):
                ana.call("POST", "/v1/sql/query", {"sql": e["sql"]})
        ana.call("POST", "/v1/sql/saved", {"name": "每日成交筆數（近 14 天）", "group": "交易", "description": "口徑同神策『成交』事件", "sql": cat["examples"][0]["sql"], "shared": {"roles": ["digital_ops"]}})
        ana.call("POST", "/v1/sql/saved", {"name": "各平台成交金額（參數：天數）", "group": "交易", "sql": "SELECT platform, count() AS deals, sum(amount) AS amount\nFROM dmp.events\nWHERE event = 'bond_deal' AND event_date >= today() - {{days}}\nGROUP BY platform ORDER BY amount DESC", "params_default": {"days": 7}, "shared": {"roles": ["digital_ops"]}})
    if not it.call("GET", "/v1/sql/mid-tables"):
        it.call("POST", "/v1/sql/mid-tables", {"name": "mid_deals_daily", "note": "每日成交彙總，供看板與 SQL 直接查", "sql": "SELECT event_date, platform, count() AS deals, uniq(one_id_uint) AS users\nFROM dmp.events WHERE event = 'bond_deal' GROUP BY event_date, platform"})
    # 9. 標籤與推送：分析師建「價值分級」標籤（分群對應值）並計算；數位經營建兩個目的地（本機接收端 CRM、MA）；分析師對分群建推送單 → 首次目的地需核可 → 執行
    if not any(t["id"] == "tier_grade" for t in ana.call("GET", "/v1/tags")):
        ana.call("POST", "/v1/tags", {"name": "價值分級", "key": "tier_grade", "directory": "客戶價值", "description": "依 user.tier 對應 P1／P2／P3；名單推送附帶", "kind": "values",
                                     "values": [{"value": v, "rule": {"project": "golden", "filters": [{"field": "user.tier", "op": "in", "value": [v]}]}} for v in ("P1", "P2", "P3")],
                                     "update": {"mode": "routine", "freq": "daily", "earliest": "06:00", "keep": 7}, "fields": {"department": "數位金融", "requester": "王小明", "category": "名單推送"}})
        ana.call("POST", "/v1/tags/tier_grade/compute", {})
        ana.call("POST", "/v1/tags", {"name": "近 30 天成交筆數（SQL）", "key": "deals_30d", "directory": "交易活躍", "kind": "sql", "dtype": "number",
                                     "sql": "SELECT one_id_uint, toString(count()) AS tag_value\nFROM dmp.events WHERE event = 'bond_deal' AND event_date >= today() - 30 GROUP BY one_id_uint"})
        ana.call("POST", "/v1/tags/deals_30d/compute", {})
        ana.call("POST", "/v1/tags", {"name": "近 90 天成交筆數分級", "key": "deals_90d_level", "directory": "交易活躍", "kind": "metric", "project": "golden", "description": "基礎指標值：成交次數分桶，推送時當交易活躍度",
                                     "spec": {"event": "bond_deal", "agg": "count", "window_days": 90, "buckets": [1, 3, 10]}, "update": {"mode": "routine", "freq": "daily", "earliest": "06:30", "keep": 7}, "fields": {"department": "數位金融", "category": "名單推送"}})
        ana.call("POST", "/v1/tags/deals_90d_level/compute", {})
        ana.call("POST", "/v1/tags", {"name": "首次成交商品", "key": "first_deal_symbol", "directory": "交易偏好", "kind": "firstlast", "project": "golden", "description": "首末次特徵：第一筆成交的商品代碼，看入門商品",
                                     "spec": {"event": "bond_deal", "which": "first", "feature": "field", "field": "symbol", "window_days": 0}})
        ana.call("POST", "/v1/tags/first_deal_symbol/compute", {})
        ana.call("POST", "/v1/tags", {"name": "RFM 客群（近 90 天成交）", "key": "rfm_90d", "directory": "客戶價值", "kind": "rfm", "project": "golden", "description": "R 最近成交距今、F 成交次數、M 成交金額各分 5 級，輸出八類客群",
                                     "spec": {"event": "bond_deal", "amount_field": "amount", "window_days": 90, "bins": 5, "output": "segment"}, "update": {"mode": "routine", "freq": "weekly", "earliest": "07:00", "keep": 8}, "fields": {"department": "財富管理", "category": "分析分群"}})
        ana.call("POST", "/v1/tags/rfm_90d/compute", {})
        mkt.call("POST", "/v1/tags", {"name": "Q4 活動名單（待審）", "key": "campaign_q4", "directory": "行銷", "kind": "values", "values": [{"value": "A 組", "rule": {"project": "golden", "combine": "or", "filters": {"op": "or", "items": [{"field": "user.lifecycle", "op": "in", "value": ["沉睡"]}, {"op": "and", "items": [{"field": "user.churn_score", "op": "gte", "value": 70}, {"field": "user.has_margin", "op": "false"}]}]},
                                                                           "behaviors": [{"event": "bond_deal", "measure": {"agg": "sum", "field": "amount"}, "cmp": {"op": "lt", "value": 100000}, "time": {"last_days": 30}}]}}]})
    dests = {d["name"]: d for d in ops.call("GET", "/v1/destinations")}
    if "CRM 客戶系統" not in dests:
        dests["CRM 客戶系統"] = ops.call("POST", "/v1/destinations", {"name": "CRM 客戶系統", "url": os.environ.get("DMP_DEMO_WEBHOOK", "http://127.0.0.1:9099/dmp/webhook"), "secret_ref": "kv/data/dmp/prod/crm#webhook_secret", "allowed_tags": ["tier_grade", "deals_30d"], "note": "HMAC 簽章、IP 白名單"})
        ops.call("POST", f"/v1/destinations/{dests['CRM 客戶系統']['id']}/test")
        dests["行銷自動化 MA"] = ops.call("POST", "/v1/destinations", {"name": "行銷自動化 MA", "url": "https://ma.internal/hooks/dmp", "secret_ref": "kv/data/dmp/prod/ma#webhook_secret", "identifier": "one_id", "allowed_tags": ["tier_grade"], "note": "mTLS"})
    segs = {s["name"]: s for s in ana.call("GET", "/v1/segments")}
    hv = segs.get("高價值活躍交易客")
    if hv and not ana.call("GET", "/v1/pushes"):
        if not (hv.get("latest") or {}).get("users"):
            ana.call("POST", f"/v1/segments/{hv['id']}/compute", {})
        p = ana.call("POST", f"/v1/audiences/{hv['id']}/push", {"destination_ids": [dests["CRM 客戶系統"]["id"]], "mode": "delta", "schedule": "after_batch", "include_tags": ["tier_grade"]})
        if p.get("approval_id"):
            ops.call("POST", f"/v1/approvals/{p['approval_id']}/decide", {"approve": True, "note": "CRM 首次推送，已與接收端對過簽章"})
        ana.call("POST", f"/v1/pushes/{p['id']}/run")
        p2 = ops.call("POST", f"/v1/audiences/{hv['id']}/push", {"destination_ids": [dests["行銷自動化 MA"]["id"]], "mode": "full", "schedule": "now"})
        if p2.get("approval_id"):
            adm.call("POST", f"/v1/approvals/{p2['approval_id']}/decide", {"approve": True, "note": "MA 初始化全量"})
    print("demo fill ok")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(str(e))
