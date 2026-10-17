"""分群引擎自測（假 ClickHouse、假 session）：建立（審批與否）→ 估數／預覽 → 計算（版本、保留數、失敗紀錄）→ 例行 tick（時鐘注入、日／週／月）→ 編輯限制 → 複製／暫停 → 血緣 → 回收站（409／force／恢復／徹底刪除／到期自動刪）→ 成員列表 → 權限。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ..api.app import create_app
from ..api.selftest import login
from ..api.store import MemoryStore
from ..auth.api import AuthService, load_cfg
from ..auth.session import MemoryStore as SessionMemory
from ..auth.session import SessionManager
from .engine import SegmentService

TPE = ZoneInfo("Asia/Taipei")


class FakeClickHouse:
    """記錄 SQL；INSERT 後 count 回「版本號 × 10」好驗證；bitmapCardinality 回 123；畫像回兩列；用戶列表回 1 列；可注入一次失敗。"""
    def __init__(self) -> None:
        self.sqls: list[str] = []; self.fail_next = False

    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str:
        self.sqls.append(sql)
        if self.fail_next and sql.startswith("INSERT"):
            self.fail_next = False; raise RuntimeError("ClickHouse 500: boom")
        if sql.startswith("INSERT") or sql.startswith("ALTER"): return ""
        if "bitmapCardinality(" in sql: return '{"users":123}\n'
        if sql.startswith("SELECT count() FROM dmp.segment_members"):
            return str(int(sql.rsplit("version = ", 1)[1]) * 10) + "\n"
        if "GROUP BY value" in sql: return '{"value":"P1","users":80}\n{"value":"P2","users":43}\n'
        if "bitmapContains(" in sql: return '{"one_id":"v1_abc****","branch_id":"B01","lifecycle":"新客","tier":"P2","aum_bucket":"<100萬","churn_score":"40–70","last_active_at":"2026-10-15 12:00:00.000","_s":0,"_u":1}\n'
        return ""


def main() -> None:
    cfg = load_cfg(); store = MemoryStore(); ch = FakeClickHouse()
    clock = {"now": datetime(2026, 10, 16, 9, 0, tzinfo=TPE)}
    svc = SegmentService(store, ch, now=lambda: clock["now"])
    sm = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), admin_requires=cfg["role_mapping"]["admin_requires"])
    app = create_app(AuthService(cfg, {}, sm, None), store, ch, svc)
    c = TestClient(app, base_url="https://dmp.internal")
    T = {"from": "2026-10-01", "to": "2026-10-16", "now": "2026-10-16"}
    rule = {"project": "golden", "filters": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}], "behaviors": [{"event": "bond_deal", "count": {"op": "gte", "value": 3}, "time": T}, {"event": "order_rejected", "done": False, "time": T}]}
    # 1. 數據分析建立 → 直接 active；估數／預覽；名稱重複 409；規則不合法 400
    login(c, sm, ["data_analyst"], "ana")
    r = c.post("/v1/segments", json={"name": "成交 3 次未退單", "rule": rule, "update": {"mode": "routine", "freq": "daily", "earliest": "06:00", "keep": 2}}); assert r.status_code == 201, r.text
    sg = r.json(); sid = sg["id"]
    assert sg["status"] == "active" and sg["approval"]["status"] == "none" and sg["update"]["keep"] == 2 and "event:bond_deal" in sg["lineage"]["up"] and sg["latest"] is None, sg
    assert c.post("/v1/segments", json={"name": "成交 3 次未退單", "rule": rule}).status_code == 409
    assert c.post("/v1/segments", json={"name": "x", "rule": {"filters": [{"field": "platform", "op": "eq", "value": "iOS"}]}}).status_code == 400
    assert c.post("/v1/segments/estimate", json={"rule": rule}).json()["users"] == 123
    pv = c.post(f"/v1/segments/{sid}/preview").json(); assert pv["users"] == 123 and pv["profile"]["tier"][0] == {"value": "P1", "users": 80} and set(pv["profile"]) == {"lifecycle", "tier", "aum_bucket", "branch_id"}, pv
    # 2. 手動計算 → 版本 1；再算 → 版本 2 且刪掉 ≤ 0 的（無）；第三次 → 刪 version ≤ 1；失敗一次留紀錄
    v1 = c.post(f"/v1/segments/{sid}/compute").json(); assert v1["version"] == 1 and v1["users"] == 10 and v1["status"] == "success" and v1["trigger"] == "manual" and v1["basetime"] == "2026-10-16", v1
    assert ch.sqls[-2].startswith("INSERT INTO dmp.segment_members (segment_id, version, one_id, computed_at) WITH") and f"SELECT '{sid}', 1, e.one_id, now() FROM dmp.users AS e FINAL WHERE bitmapContains(bm, e.one_id_uint)" in ch.sqls[-2]
    c.post(f"/v1/segments/{sid}/compute"); c.post(f"/v1/segments/{sid}/compute", json={"basetime": "2026-10-10"})
    assert any(f"DELETE WHERE segment_id = '{sid}' AND version <= 1" in s for s in ch.sqls), ch.sqls[-3:]
    vs = c.get(f"/v1/segments/{sid}/versions").json(); assert [v["version"] for v in vs] == [3, 2] and vs[0]["trigger"] == "backfill" and vs[0]["basetime"] == "2026-10-10", vs
    ch.fail_next = True
    vf = c.post(f"/v1/segments/{sid}/compute").json(); assert vf["status"] == "failed" and "boom" in vf["error"] and c.get(f"/v1/segments/{sid}").json()["latest"]["status"] == "failed"
    assert [x["event"] for x in store.audit_tail(3)][-1] == "segment.compute_failed"
    # 3. 例行 tick：05:59 不到時間；06:00 算當日；同日再 tick 不重算；週／月頻率算本週一／本月 1 日
    login(c, sm, ["admin"], "adm")
    assert c.post("/v1/segments/tick", params={"now": "2026-10-17T05:59:00+08:00"}).json()["computed"] == []
    t = c.post("/v1/segments/tick", params={"now": "2026-10-17T06:00:00+08:00"}).json()["computed"]; assert len(t) == 1 and t[0]["basetime"] == "2026-10-17" and t[0]["trigger"] == "routine", t
    assert c.post("/v1/segments/tick", params={"now": "2026-10-17T12:00:00+08:00"}).json()["computed"] == []
    login(c, sm, ["data_analyst"], "ana")
    wk = c.post("/v1/segments", json={"name": "週分群", "rule": rule, "update": {"mode": "routine", "freq": "weekly", "earliest": "07:00", "keep": 3}}).json()
    mo = c.post("/v1/segments", json={"name": "月分群", "rule": rule, "update": {"mode": "routine", "freq": "monthly", "earliest": "07:00", "keep": 3}}).json()
    login(c, sm, ["admin"], "adm")
    t = c.post("/v1/segments/tick", params={"now": "2026-10-21T08:00:00+08:00"}).json()["computed"]           # 週三：週分群補算本週一 10-19、月分群算 10-01、日分群算 10-21
    assert {(x["segment"], x["basetime"]) for x in t} == {(sid, "2026-10-21"), (wk["id"], "2026-10-19"), (mo["id"], "2026-10-01")}, t
    # 4. 行銷建立 → pending_approval；計算 409；自己不能審；數位經營核可 → active；退回 → draft → 送審
    login(c, sm, ["marketing"], "mkt")
    m = c.post("/v1/segments", json={"name": "行銷圈人", "rule": {**rule, "segment": sid}, "update": {"mode": "manual"}}).json(); assert m["status"] == "pending_approval" and f"segment:{sid}" in m["lineage"]["up"], m
    assert c.post(f"/v1/segments/{m['id']}/compute").status_code == 409 and c.post(f"/v1/segments/{m['id']}/approve").status_code == 403
    login(c, sm, ["digital_ops"], "ops")
    assert c.post(f"/v1/segments/{m['id']}/approve", json={"approve": False, "note": "條件太寬"}).json()["status"] == "draft"
    login(c, sm, ["marketing"], "mkt"); assert c.post(f"/v1/segments/{m['id']}/submit").json()["status"] == "pending_approval"
    login(c, sm, ["digital_ops"], "ops"); a = c.post(f"/v1/segments/{m['id']}/approve", json={"note": "ok"}).json(); assert a["status"] == "active" and a["approval"]["by"] == "entra:ops"
    # 5. 編輯限制：手動分群不可改更新方式；例行不可改頻率；結果保存分群不可改規則；改規則 → latest.stale
    login(c, sm, ["data_analyst"], "ana")
    assert c.put(f"/v1/segments/{m['id']}", json={"update": {"mode": "routine"}}).status_code == 409
    assert c.put(f"/v1/segments/{sid}", json={"update": {"freq": "weekly"}}).status_code == 409
    assert c.put(f"/v1/segments/{sid}", json={"update": {"earliest": "07:30"}}).json()["update"]["earliest"] == "07:30"
    e = c.put(f"/v1/segments/{sid}", json={"rule": {**rule, "behaviors": rule["behaviors"][:1]}}).json(); assert e["latest"]["stale"] is True and e["lineage"]["up"]
    fq = {"steps": [{"event": "app_login_success"}, {"event": "bond_order"}], "window": {"value": 7, "unit": "day"}, "time": T}
    res = c.post("/v1/segments", json={"name": "漏斗第 2 步流失", "kind": "result", "rule": {"project": "golden", "source": {"type": "funnel", "query": fq, "step": 2, "reached": False}}}).json()
    assert res["kind"] == "result" and c.put(f"/v1/segments/{res['id']}", json={"rule": rule}).status_code == 409 and c.put(f"/v1/segments/{res['id']}", json={"name": "改名"}).status_code == 200
    assert c.post("/v1/segments", json={"name": "沒落點", "kind": "result", "rule": rule}).status_code == 400
    # 6. 複製（規則型）、暫停／啟動、血緣（下游 = 引用它的分群）、成員列表（指定版本 → seg.<id>@<n>）
    cp = c.post(f"/v1/segments/{sid}/copy", json={"name": "複製品"}).json(); assert cp["kind"] == "rule" and cp["rule"] == e["rule"] and cp["status"] == "active"
    assert c.post(f"/v1/segments/{sid}/pause").json()["status"] == "paused" and c.post(f"/v1/segments/{sid}/compute").status_code == 409 and c.post(f"/v1/segments/{sid}/pause").json()["status"] == "active"
    assert c.get(f"/v1/segments/{sid}/lineage").json()["down"] == [f"segment:{m['id']}"]
    mem = c.get(f"/v1/segments/{sid}/versions/latest/members?page_size=20").json()
    assert mem["rows"][0]["one_id"] == "v1_abc****" and f"segment_id = '{sid}' AND version = {mem['version']['version']}" in mem["sql"] and mem["masked"]["one_id"] == "mask_tail(4)" and mem["total"] == mem["version"]["users"], mem
    assert c.get(f"/v1/segments/{sid}/versions/2020-01-01/members").status_code == 404
    # 7. 回收站：有下游 409 → force → 列表不含；恢復；徹底刪除需 admin 並清成員；到期自動刪
    assert c.post(f"/v1/segments/{sid}/recycle").status_code == 409
    assert c.post(f"/v1/segments/{sid}/recycle?force=true").json()["status"] == "recycled" and sid not in {s["id"] for s in c.get("/v1/segments").json()}
    assert sid in {s["id"] for s in c.get("/v1/segments?status=recycled").json()} and c.post(f"/v1/segments/{sid}/restore").json()["status"] == "active"
    assert c.delete(f"/v1/segments/{cp['id']}/purge").status_code == 403
    c.post(f"/v1/segments/{cp['id']}/recycle")
    login(c, sm, ["admin"], "adm")
    assert c.delete(f"/v1/segments/{cp['id']}/purge").status_code == 204 and ch.sqls[-1] == f"ALTER TABLE dmp.segment_members DELETE WHERE segment_id = '{cp['id']}'" and c.get(f"/v1/segments/{cp['id']}").status_code == 404
    login(c, sm, ["data_analyst"], "ana"); c.post(f"/v1/segments/{res['id']}/recycle")
    login(c, sm, ["admin"], "adm")
    clock["now"] = datetime(2026, 11, 1, 9, 0, tzinfo=TPE)
    assert c.post("/v1/segments/tick").json()["purged"] == [res["id"]]
    # 8. 權限：資訊管理只能看列表、不能建；AI 服務身份只能看；行銷不能 tick；審計
    login(c, sm, ["it_admin"], "it"); assert c.get("/v1/segments").status_code == 200 and c.post("/v1/segments", json={"name": "x", "rule": rule}).status_code == 403
    login(c, sm, ["svc_insight"], "svc"); assert c.get("/v1/segments").status_code == 200 and c.post(f"/v1/segments/{sid}/compute").status_code == 403
    login(c, sm, ["marketing"], "mkt"); assert c.post("/v1/segments/tick").status_code == 403
    ev = [x["event"] for x in store.audit_tail(500)]
    assert {"segment.created", "segment.computed", "segment.compute_failed", "segment.approved", "segment.rejected", "segment.recycled", "segment.restored", "segment.purged", "segment.members_listed", "segment.updated"} <= set(ev)
    print(f"segments self-test ok: create/approve/compute(versions keep={sg['update']['keep']})/tick(daily,weekly,monthly)/edit rules/copy/pause/lineage/members/recycle/purge; audit={len(ev)} sql={len(ch.sqls)}")


if __name__ == "__main__":
    main()
