"""自測（不連 ClickHouse）：指標庫 → 以假排程器／假 store／假 ClickHouse 跑四個視圖與告警規則。"""
from __future__ import annotations

import json
from typing import Any

from . import metrics as M
from .service import ALERTS, SLA, OpsService


class FakeStore:
    def __init__(self, now: float):
        self.now = now
        self._audit: list[dict[str, Any]] = []
        self._items: dict[str, list[dict[str, Any]]] = {"sync_task": [{"id": "sync_orders", "cn": "交易委託"}, {"id": "sync_instrument", "cn": "商品主檔"}],
                                                          "delivery": [{"id": "dl1", "mode": "delta", "status": "ok", "created_at": iso(now - 3600)}, {"id": "dl2", "mode": "delta", "status": "dlq", "created_at": iso(now - 7200)},
                                                                       {"id": "dl3", "mode": "test", "status": "ok", "created_at": iso(now - 60)}, {"id": "dl4", "mode": "full", "status": "ok", "created_at": iso(now - 3 * 86400)}],
                                                          "destination": [{"id": "dst1", "name": "CRM", "status": "paused", "consecutive_failures": 3}, {"id": "dst2", "name": "MA", "status": "active", "consecutive_failures": 0}]}

    def audit(self, event: str, data: dict[str, Any]) -> None: self._audit.append({"event": event, "at": self.now, **data})
    def audit_tail(self, n: int = 100) -> list[dict[str, Any]]: return self._audit[-n:]
    def items(self, kind: str) -> list[dict[str, Any]]: return list(self._items.get(kind, []))


class FakeSched:
    def __init__(self, now: float): self.now = now; self.ran: list[str] = []
    def status(self, now: Any = None) -> list[dict[str, Any]]:
        return [{"id": "sync_orders", "enabled": True, "paused_reason": None, "schedule": "*/30 * * * *", "mode": "incremental", "next_slot": iso(self.now + 600), "watermark": iso(self.now - 4 * 3600),
                 "last": {"scheduled_for": iso(self.now - 1800), "status": "success", "rows_in": 1000, "rows_ok": 900, "rows_quarantined": 80, "rows_rejected": 20, "rows_loaded": 900, "attempt": 1, "error": None}},
                {"id": "sync_instrument", "enabled": False, "paused_reason": "schema change", "schedule": "0 6 * * *", "mode": "full", "next_slot": None, "watermark": iso(self.now - 600),
                 "last": {"scheduled_for": iso(self.now - 86400), "status": "paused", "rows_in": 0, "rows_ok": 0, "rows_quarantined": 0, "rows_rejected": 0, "rows_loaded": 0, "attempt": 1, "error": "schema change (pause)"}}]
    def task(self, id_: str) -> dict[str, Any]:
        if id_ not in ("sync_orders", "sync_instrument"): raise KeyError(id_)
        return {"id": id_}
    def run_now(self, id_: str) -> dict[str, Any]: self.ran.append(id_); return {"run_id": 1, "task_id": id_, "status": "success"}


class FakeCH:
    def __init__(self, now_iso: str): self.now_iso = now_iso; self.sqls: list[str] = []
    def query(self, sql: str, data: Any = None, settings: Any = None) -> str:
        self.sqls.append(sql); assert settings == {"max_execution_time": 60}
        if "system.parts" in sql: return '{"table":"events","rows":7000000000,"bytes":7200000000000,"parts":120}\n{"table":"users","rows":3000000,"bytes":900000000,"parts":4}\n'
        if "max(time)" in sql: return json.dumps({"t": self.now_iso}) + "\n"
        if "system.query_log" in sql: return '{"n":420,"p95_ms":812.5,"scan_rows_avg":1500000.0,"memory_peak_mb":512.25,"errors":2}\n'
        raise AssertionError(sql)


def iso(t: float) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def main() -> None:
    M.selftest()
    now = 1_800_000_000.0
    store = FakeStore(now); sched = FakeSched(now); ch = FakeCH(iso(now - 95 * 60))
    for i in range(20):                                                                   # 24h 內的查詢審計：events 20 次、funnel 3 次（含 1 次超 SLA）、1 次失敗、sql 2 次
        store._audit.append({"event": "query.executed", "at": now - 60 * i, "module": "events", "ms": 100 + i * 10, "rows": 30, "user": "u1", "sql_hash": f"h{i}", "suppressed": i % 5 == 0})
    store._audit += [{"event": "query.executed", "at": now - 10, "module": "funnel", "ms": 12000, "rows": 3, "user": "u2", "sql_hash": "hf"}, {"event": "query.executed", "at": now - 20, "module": "funnel", "ms": 900, "rows": 3, "user": "u2", "sql_hash": "hf2"},
                     {"event": "query.executed", "at": now - 30, "module": "funnel", "ms": 950, "rows": 3, "user": "u2", "sql_hash": "hf3"}, {"event": "query.failed", "at": now - 40, "module": "funnel", "user": "u2", "sql_hash": "hf4"},
                     {"event": "query.sql", "at": now - 50, "module": "sql", "ms": 300, "rows": 10, "user": "u3", "sql_hash": "hs"}, {"event": "query.sql", "at": now - 55, "module": "sql", "status": "error", "user": "u3", "sql_hash": "hs2"},
                     {"event": "query.executed", "at": now - 2 * 86400, "module": "events", "ms": 99999, "rows": 1, "user": "u1", "sql_hash": "old"}, {"event": "scheduler.tick.failed", "at": now - 100, "error": "x"},
                     {"event": "scheduler.tick.failed", "at": now - 200, "error": "x"}, {"event": "scheduler.tick.failed", "at": now - 300, "error": "x"}]
    reg = M.Registry()
    svc = OpsService(store, ch, sched, now=lambda: now, reg=reg)
    M.HTTP_REQUESTS.inc({"method": "GET", "route": "/v1/x", "status": "200"}, 97); M.HTTP_REQUESTS.inc({"method": "GET", "route": "/v1/x", "status": "502"}, 3)
    for v in (0.05, 0.1, 0.4): M.HTTP_SECONDS.observe(v, {"method": "GET", "route": "/v1/x"}, at=now - 10)
    M.observe_query("events", 250, True, 10, at=now - 5)
    with M.routine("segments", now=lambda: now): pass
    try:
        with M.routine("tags", now=lambda: now): raise RuntimeError("boom")
    except RuntimeError: pass

    p = svc.pipeline()
    st = {s["key"]: s for s in p["stages"]}
    assert [s["key"] for s in p["stages"]] == ["sources", "warehouse", "serving", "activation"], p["stages"]
    src = {s["task"]: s for s in st["sources"]["items"]}
    assert src["sync_orders"]["lag_min"] == 240 and src["sync_orders"]["quarantine_rate"] == 0.1 and src["sync_orders"]["status"] == "warn" and src["sync_orders"]["cn"] == "交易委託", src["sync_orders"]
    assert src["sync_instrument"]["failed"] == 1 and src["sync_instrument"]["status"] == "error" and st["sources"]["status"] == "error"
    assert st["warehouse"]["status"] == "error" and "事件落後 95 分鐘" in st["warehouse"]["summary"] and st["warehouse"]["items"][0]["name"] == "events" and st["warehouse"]["items"][0]["cn"] == "行為事件", st["warehouse"]
    sv = st["serving"]["items"][0]; assert sv["requests_5m"] == 3 and sv["http_p95_ms"] == 400 and abs(sv["http_error_rate"] - 0.03) < 1e-9 and sv["queries_5m"] == 1 and st["serving"]["status"] == "error", sv
    act = st["activation"]["items"][0]; assert act["deliveries_24h"] == {"ok": 1, "retrying": 0, "dlq": 1} and act["success_rate"] == 0.5 and [x["destination"] for x in act["paused_destinations"]] == ["dst1"] and st["activation"]["status"] == "error", act
    q = {x["key"]: x for x in p["quality"]}
    assert q["quarantine_rate"]["value"] == 0.1 and q["quarantine_rate"]["status"] == "error" and q["sync_failed"]["text"] == "商品主檔" and q["events_stale"]["status"] == "error" and q["k_suppressed"]["text"] == "4 / 23 次" and q["delivery_success"]["status"] == "error", q
    assert p["slo"]["query_p95_ms"]["funnel"] == 10000 and p["slo"] is SLA

    qp = svc.query_perf("24h")
    m = {x["module"]: x for x in qp["modules"]}
    assert [x["module"] for x in qp["modules"]] == ["events", "funnel", "sql"], [x["module"] for x in qp["modules"]]
    assert m["events"]["n"] == 20 and m["events"]["p50_ms"] == 200 and m["events"]["p95_ms"] == 280 and m["events"]["p99_ms"] == 290 and m["events"]["sla_ok"] is True and m["events"]["avg_rows"] == 30, m["events"]
    assert m["funnel"]["n"] == 4 and m["funnel"]["errors"] == 1 and m["funnel"]["p95_ms"] == 12000 and m["funnel"]["sla_ok"] is False and m["funnel"]["error_rate"] == 0.25, m["funnel"]
    assert m["sql"]["n"] == 2 and m["sql"]["errors"] == 1 and m["sql"]["sla_ms"] == 60000 and m["sql"]["cn"] == "自定義 SQL", m["sql"]
    assert qp["slow"][0]["ms"] == 12000 and len(qp["slow"]) == 10 and all(r["ms"] < 99999 for r in qp["slow"]), qp["slow"][:2]
    assert qp["clickhouse"] == {"n": 420, "p95_ms": 812.5, "scan_rows_avg": 1500000.0, "memory_peak_mb": 512.25, "errors": 2}
    assert svc.query_perf("1h")["modules"][0]["n"] == 20 and len(svc.query_perf("7d")["modules"]) == 3

    bj = svc.batch_jobs()
    names = [j["name"] for j in bj["jobs"]]
    assert names == ["sync_orders", "sync_instrument", "scheduler", "dashboard_share", "segments", "tags", "activation"], names
    j = {x["name"]: x for x in bj["jobs"]}
    assert j["sync_orders"]["rerunnable"] and j["sync_orders"]["last_detail"] == "讀入 1,000，載入 900，隔離／拒絕 100" and j["sync_instrument"]["last_detail"] == "schema change (pause)" and not j["sync_instrument"]["enabled"]
    assert j["segments"]["last_status"] == "success" and j["tags"]["last_status"] == "failed" and j["tags"]["errors_total"] == 1 and j["scheduler"]["last_status"] is None and not j["segments"]["rerunnable"], j["tags"]
    assert bj["storage"][0]["name"] == "clickhouse.events" and bj["storage"][0]["bytes"] == 7200000000000 and bj["storage"][-1]["name"] == "control_plane" and bj["storage"][-1]["rows"] >= 30

    class Subj: user_id = "entra:it"
    assert svc.rerun("sync_orders", Subj())["status"] == "success" and sched.ran == ["sync_orders"] and store._audit[-1]["event"] == "batch.rerun" and store._audit[-1]["job"] == "sync_orders"
    try:
        svc.rerun("segments", Subj()); raise AssertionError("routine 不可重跑")
    except KeyError: pass

    al = svc.alerts()
    firing = {(a["key"], a["subject"]): a for a in al["firing"]}
    assert ("source_lag", "交易委託") in firing and firing[("source_lag", "交易委託")]["value"] == 240 and ("source_lag", "商品主檔") not in firing       # 停用的任務不算延遲
    assert ("sync_failed", "商品主檔") in firing and ("quarantine_rate", "交易委託") in firing and ("query_p95", "漏斗分析") in firing and firing[("query_p95", "漏斗分析")]["value"] == 1.2
    assert ("query_errors", "漏斗分析") in firing and ("query_errors", "自定義 SQL") in firing and ("query_errors", "事件分析") not in firing
    assert ("http_errors", "API") in firing and ("delivery_dlq", "推送") in firing and ("destination_paused", "CRM") in firing and ("routine_failed", "背景例行") in firing and ("events_stale", "dmp.events") in firing
    assert ("storage_hot", "ClickHouse") in firing and firing[("storage_hot", "ClickHouse")]["value"] == 0.9232, firing.get(("storage_hot", "ClickHouse"))
    assert al["firing"][0]["severity"] == "critical" and al["firing"][0]["key"] == "events_stale" and len(al["rules"]) == len(ALERTS["rules"]) and al["notify"] == ["it_admin", "admin"]

    txt = reg.render()
    assert 'dmp_source_lag_seconds{task="sync_orders"} 14400' in txt and 'dmp_sync_last_ok{task="sync_instrument"} 0' in txt and 'dmp_sync_quarantine_ratio{task="sync_orders"} 0.1' in txt, txt
    assert 'dmp_delivery_24h{status="dlq"} 1' in txt and 'dmp_destination_paused{destination="dst1"} 1' in txt and "dmp_events_stale_seconds 5700" in txt and 'dmp_clickhouse_table_bytes{table="events"} 7200000000000' in txt and "dmp_clickhouse_hot_ratio 0.923192" in txt, txt
    assert "# HELP dmp_source_lag_seconds 同步任務水位落後秒數" in txt
    assert len([s for s in ch.sqls if "system.parts" in s]) == 1, "系統表查詢應快取"            # 多次視圖只打一次 ClickHouse

    svc2 = OpsService(store, None, None, now=lambda: now, reg=M.Registry())                  # 沒有 ClickHouse／排程器：頁面仍可用
    p2 = svc2.pipeline(); assert p2["stages"][0]["summary"].startswith("沒有同步任務") and p2["stages"][1]["status"] == "unknown" and svc2.query_perf()["clickhouse"] is None
    assert svc2.batch_jobs()["jobs"][0]["name"] == "scheduler" and svc2.alerts()["firing"] and all(a["key"] not in ("source_lag", "events_stale", "storage_hot") for a in svc2.alerts()["firing"])
    print(f"ops self-test ok: pipeline(4 stages, {len(p['quality'])} quality) query-perf({len(qp['modules'])} modules) batch-jobs({len(bj['jobs'])} jobs, {len(bj['storage'])} storage) alerts({len(al['firing'])} firing / {len(al['rules'])} rules) metrics({len(txt.splitlines())} lines)")


if __name__ == "__main__":
    main()
