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
UQ = {"project": "golden", "filters": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}], "behaviors": [{"event": "bond_deal", "count": {"op": "gte", "value": 3}, "time": {"from": "2026-10-01", "to": "2026-10-16"}}]}


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
    if not [a for a in adm.call("GET", "/v1/iam/assignments") if a.get("user_id") == "local:vendor-a"]:
        roles = {r["key"] for r in adm.call("GET", "/v1/iam/roles")}
        if "custom_vendor_analyst" in roles:
            adm.call("POST", "/v1/iam/assignments", {"user_id": "local:vendor-a", "roles": ["custom_vendor_analyst"], "scope": {"projects": ["golden"], "lookback_days": 180}, "expires_at": "2026-12-31", "reason": "外包分析專案"})
    print("demo fill ok")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(str(e))
