"""證據與資料就緒自測：Gateway evidence.get、data.readiness（docs/39 §2.5、§3.3）與 A20 敘述數字可追溯。合成資料、假 ClickHouse。"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..api.store import MemoryStore
from ..mcp.server import GW, Gateway, ToolContext, _has_internal
from ..security.policy import RBAC, Subject
from .runs import RunService
from .runs_selftest import FakeCH, code
from .trace import untraced

NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)                          # 台北 10/28 02:00


class CH(FakeCH):
    """漏斗結果帶非零人數（給 A20 比對）；readiness 的來源新鮮度與待重建日期另外回。"""
    def __init__(self) -> None:
        super().__init__(); self.src: list[dict[str, Any]] = []; self.pending: str | None = "2026-10-20\n2026-09-01\n"
    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str:
        if sql.startswith("EXPLAIN ESTIMATE"):
            return super().query(sql, data, settings)
        if "events_daily_agg_pending" in sql:
            if self.pending is None:
                raise RuntimeError("Code: 60. Table dmp.events_daily_agg_pending does not exist")
            return self.pending
        if "max(e.time)" in sql:
            self.sql.append(sql); self.settings.append(dict(settings or {})); return "\n".join(json.dumps(r) for r in self.src)
        self.sql.append(sql); self.settings.append(dict(settings or {}))
        return "\n".join(json.dumps(r) for r in ({"step": 1, "users": 120, "_k": 120, "suppressed": 0, "g1": ""}, {"step": 2, "users": 45, "_k": 45, "suppressed": 0, "g1": ""}))


def selftest() -> None:
    import jsonschema
    import yaml
    st, ch, clock = MemoryStore(), CH(), [NOW.timestamp()]
    today = lambda: date(2026, 10, 28)
    roles: dict[str, list[str]] = {"u_ana": ["data_analyst"], "u_peer": ["data_analyst"]}
    gw = Gateway(st, ch, today=today, clock=lambda: clock[0])
    svc = RunService(st, ch, gateway=gw, model_missing=lambda: ["DMP_AGENT_MODEL_PROVIDER"], today=today, clock=lambda: clock[0], roles_of=roles.get)
    ana = Subject("u_ana", ["data_analyst"])
    x = svc.create(ana, {"entry": "analyze", "question": "上週開戶漏斗跟前週比"}, None)["run_id"]; svc.tick()
    svc.answer(ana, x, {"question_id": "term:開戶", "choice": "kh_review_pass"}); svc.tick(); svc.answer(ana, x, {"question_id": "confirm", "choice": "c0"}); svc.tick()
    r = svc._own(ana, x); assert r["state"] == "succeeded", r.get("error")
    eid = r["result"]["evidence_ids"][0]; ev = r["evidence"][eid]
    get = lambda s, e=eid, **kw: gw.call(ToolContext(s, x, "ev", **kw), "evidence.get", {"evidence_id": e})

    # evidence.get：每次以讀取者現在的權限重算（不快取）；不回 SQL；模型看 D3 投影
    n = len(ch.sql); e1 = get(ana); e2 = get(ana)
    assert e1["ok"] and e2["ok"] and len(ch.sql) == n + 2, e1.get("error")                                     # 兩次都重查
    d = e1["data"]
    assert d["evidence_id"] == eid and d["run_id"] == x and d["kind"] == ev["kind"] and d["query"] == ev["query"] and d["result_digest"] == ev["result_digest"]
    assert d["data"]["rows"][0]["users"] == 120 and d["sql_hash"] and "sql" not in d and "SELECT " not in json.dumps(d, ensure_ascii=False)
    assert not _has_internal(e1["model_view"]) and e1["model_view"]["data"]["rows"] and "lineage" not in e1["model_view"]["data"]
    # 權限指紋：角色或行級條件不同 → 指紋不同、照新權限重算（SQL 帶新的行級條件）
    RBAC["roles"]["custom_ev_7d"] = {"cn": "合成：近 7 天", "scope": "global", "lookback_days": 7, "grants": {"funnel": ["view", "use"], "events": ["view", "use"], "ai_ask": ["use"]}, "note": ""}
    try:
        narrow = Subject("u_ana", ["custom_ev_7d"]); e3 = get(narrow)
        assert e3["ok"] and e3["data"]["subject_fingerprint"] != d["subject_fingerprint"] and "'Asia/Taipei')-7" in ch.sql[-1], e3.get("error")
        assert get(ana)["data"]["subject_fingerprint"] == d["subject_fingerprint"]                                  # 同權限同指紋（仍然重查）
    finally:
        RBAC["roles"].pop("custom_ev_7d", None)
    # 404 先於 403：別人的 run、不存在的證據 → NOT_FOUND；自己的但沒有漏斗權限 → AUTHZ_DENIED；預算不足 → 不送查詢
    n = len(ch.sql)
    assert get(Subject("u_peer", ["data_analyst"]))["error"]["code"] == "NOT_FOUND" and get(ana, "ev_nope")["error"]["code"] == "NOT_FOUND"
    deny = get(Subject("u_ana", []))
    assert deny["error"]["code"] == "AUTHZ_DENIED" and deny["error"]["detail"]["module"] == "funnel", deny
    assert get(ana, scan_bytes_used=float(GW["budgets"]["per_run_scan_bytes"]))["error"]["code"] == "BUDGET_EXCEEDED" and len(ch.sql) == n
    # REST 入口走同一份實作：他人 404、空白分組顯示「（未設定）」（Q21）
    rest = svc.evidence(ana, x, eid)
    assert rest["data"]["rows"][0]["g1"] == "（未設定）" and rest["subject_fingerprint"] == d["subject_fingerprint"] and code(svc.evidence, Subject("u_peer", ["data_analyst"]), x, eid)[0] == 404

    # A20：敘述裡的每個數字都要在某個證據結果找得到（容許四捨五入、百分比對比率）；日期不算
    datas = [get(ana, e)["data"]["data"] for e in r["result"]["evidence_ids"]]
    rate = datas[0]["rows"][1]["rate_first"]
    assert untraced(f"上週（10/19～10/25）審核通過 120 人，其中 45 人成交，轉換率 {rate * 100:.1f}%", datas) == []
    assert untraced("上週 2026-10-19 起審核通過 121 人，轉換率 40%", datas) == ["121", "40%"]
    big = [{"users": 1_200_000, "amount": 350_000_000}]                       # 量級字：萬／億要換算後比對，不能只看前面的數字
    assert untraced("共有120萬人，交易額3.5億", big) == [] and untraced("共有120人", big) == ["120"]
    assert untraced("共有120億人", big) == ["120億"] and untraced("共有12萬人", big) == ["12萬"]
    assert untraced("共有1.2百萬人", big) == [] and untraced("共有1萬人", big) == ["1萬"]
    assert untraced("共有120萬人", [{"users": 120}]) == ["120萬"] and untraced("共有120億人", [{"users": 120}]) == ["120億"]
    assert untraced("共有1.2M人、3k筆", big) == ["1.2M", "3k"]                # 英文量級不解析，一律不放行
    for sp in (" ", "\u3000", "\u00a0"):                                     # 數字與量級間有半形／全形／不換行空白
        assert untraced(f"共有120{sp}萬人", [{"users": 120}]) == [f"120{sp}萬"] and untraced(f"共有120{sp}億人", [{"users": 120}]) == [f"120{sp}億"]
        assert untraced(f"共有120{sp}萬人，交易額3.5{sp}億", big) == [] and untraced(f"共有1.2{sp}M人", big) == [f"1.2{sp}M"]
    assert untraced("共有 120 人，轉換率 37.5 %", [{"users": 120, "rate": 0.375}]) == [] and untraced("共有 120 人", big) == ["120"]

    # data.readiness：依數據源管理的新鮮度設定判斷（#122）；未設定 → unknown；backfill（#74）、identity（#33）回 null + READINESS_PARTIAL
    from ..integration import freshness
    put_cfg = lambda s, b: st.put_item(freshness.KIND, {"id": freshness.item_id(s), "source": s, **freshness.validate(b)})
    rd_call = lambda **kw: gw.call(ToolContext(ana, x, "rd", **kw), "data.readiness", {"events": ["kh_apply", "kh_review_pass"], "period": {"from": "2026-10-19", "to": "2026-10-25"}})
    ch.src = [{"source": "sdk", "t": "2026-10-27 17:55:00.000"},                  # 即時 15 分：5 分鐘前 → ok
              {"source": "trade", "t": "2026-10-24 04:00:00.000"},                # 日批 07:00／T-1：現在台北 10/28 02:00 應涵蓋到 10/26，只到 10/24 → late
              {"source": "legacy", "t": "2026-10-01 00:00:00.000"}]               # 期間開始前就沒資料 → missing
    R0 = rd_call()["data"]                                                                                       # 上線初始：一個都沒設定 → 全部 unknown、日期到齊回 null
    assert [(s["source"], s["status"], s["mode"], s["expected_lag_minutes"]) for s in R0["sources"]] == [(s, "unknown", None, None) for s in ("sdk", "trade", "legacy")]
    assert all(d_["complete"] is None and d_["unknown_sources"] == ["sdk", "trade", "legacy"] and d_["missing_sources"] == [] for d_ in R0["dates"])
    assert [w["code"] for w in R0["warnings"]] == ["READINESS_PARTIAL"] * 2 and "sdk, trade, legacy" in R0["warnings"][1]["message"]
    put_cfg("sdk", {"mode": "realtime", "max_lag_minutes": 15}); put_cfg("trade", {"mode": "daily_batch", "ready_at": "07:00", "covers_days_back": 1})
    put_cfg("legacy", {"mode": "realtime", "max_lag_minutes": 60})
    rd = rd_call(query_id=f"{x}:rd:1")
    assert rd["ok"], rd["error"]; R = rd["data"]
    assert [(s["source"], s["status"]) for s in R["sources"]] == [("sdk", "ok"), ("trade", "late"), ("legacy", "missing")] and R["sources"][0]["lag_minutes"] == 5.0
    assert R["sources"][0]["expected_lag_minutes"] == 15 and R["sources"][0]["expected_through"] is None                       # 門檻取自設定，不再讀 ops/sla.yaml
    assert R["sources"][1]["mode"] == "daily_batch" and R["sources"][1]["expected_lag_minutes"] is None and R["sources"][1]["expected_through"] == "2026-10-26"
    by = {x_["date"]: x_ for x_ in R["dates"]}
    assert sorted(by) == [f"2026-10-{i}" for i in range(19, 26)] and by["2026-10-23"]["missing_sources"] == ["legacy"] and by["2026-10-25"]["missing_sources"] == ["trade", "legacy"]
    assert not any(x_["complete"] for x_ in R["dates"]) and all(x_["unknown_sources"] == [] for x_ in R["dates"])
    assert R["backfill"] is None and R["identity"] is None and R["aggregates"] == {"pending_rebuild_dates": ["2026-10-20"]}
    assert [w["code"] for w in R["warnings"]] == ["READINESS_PARTIAL"] and "READINESS_PARTIAL" in [w["code"] for w in rd["warnings"]] and set(R["contract"]) >= {"semantic", "tools"}
    assert rd["model_view"]["backfill"] is None and not _has_internal(rd["model_view"])
    sql = ch.sql[-1]
    assert "e.event IN ('kh_apply', 'kh_review_pass')" in sql and "e.project = 'altatech_dmp'" in sql and "e.event_date >= toDate('2026-09-21')" in sql and "JOIN" not in sql
    assert ch.settings[-1].get("query_id") == f"{x}:rd:1"                                                         # C7：讀取帶 query_id
    S = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))["components"]["schemas"]
    for out in (R0, R):
        jsonschema.Draft202012Validator(S["Readiness"]).validate(out)                                          # 完整回應（含 backfill=null、unknown、complete=null）都符合 #122 契約
    clock[0] = datetime(2026, 10, 27, 22, 59, tzinfo=UTC).timestamp()            # 台北 10/28 06:59：日批仍只要求到 10/26
    ch.src[1] = {"source": "trade", "t": "2026-10-26 15:00:00.000"}              # 最後事件台北 10/26 23:00
    assert rd_call()["data"]["sources"][1]["status"] == "ok"
    clock[0] = datetime(2026, 10, 27, 23, 0, tzinfo=UTC).timestamp()             # 台北 10/28 07:00：改要求到 10/27 → late
    Rb = rd_call()["data"]["sources"][1]; assert (Rb["status"], Rb["expected_through"]) == ("late", "2026-10-27"), Rb
    clock[0] = NOW.timestamp(); ch.src[1] = {"source": "trade", "t": "2026-10-24 04:00:00.000"}
    # 兩個來源都到齊的日期才算 complete；行級條件照讀取者；未登錄事件、無 events.view、待重建表讀不到、預算不足
    ch.src = ch.src[:1]; R2 = gw.call(ToolContext(ana, x, "rd"), "data.readiness", {"events": ["kh_apply"], "period": {"from": "2026-10-19", "to": "2026-10-28"}})["data"]
    assert [x_["complete"] for x_ in R2["dates"]] == [True] * 9 + [False]                                         # 現在台北 10/28 02:00：10/27 已過完且來源已超過、10/28 還沒過完
    RBAC["roles"]["custom_ev_7d"] = {"cn": "合成：近 7 天", "scope": "global", "lookback_days": 7, "grants": {"events": ["view"]}, "note": ""}
    try:
        assert gw.call(ToolContext(Subject("u_ana", ["custom_ev_7d"]), x, "rd"), "data.readiness", {"events": ["kh_apply"], "period": {"from": "2026-10-19", "to": "2026-10-25"}})["ok"]
        assert "e.event_date >= toDate(now(), 'Asia/Taipei')-7" in ch.sql[-1]
    finally:
        RBAC["roles"].pop("custom_ev_7d", None)
    call = lambda s, a: gw.call(ToolContext(s, x, "rd"), "data.readiness", a)
    p = {"from": "2026-10-19", "to": "2026-10-25"}
    assert call(ana, {"events": ["no_such_event"], "period": p})["error"]["code"] == "INVALID_ARGUMENT"
    assert call(ana, {"events": ["kh_apply"], "period": {"from": "2026-10-25", "to": "2026-10-19"}})["error"]["code"] == "INVALID_ARGUMENT"
    assert call(Subject("u_x", []), {"events": ["kh_apply"], "period": p})["error"]["code"] == "AUTHZ_DENIED"
    ch.pending = None; R3 = call(ana, {"events": ["kh_apply"], "period": p})["data"]; ch.pending = ""
    assert R3["aggregates"] is None and [w["code"] for w in R3["warnings"]] == ["READINESS_PARTIAL", "READINESS_PARTIAL"]
    n = len(ch.sql); ch.est_rows = 10 ** 12
    assert call(ana, {"events": ["kh_apply"], "period": p})["error"]["code"] == "BUDGET_EXCEEDED" and len(ch.sql) == n
    ch.est_rows = 1000
    print("agent evidence/readiness self-test ok: evidence.get 每次重算不快取、不回 SQL、D3 投影、權限指紋隨角色／行級條件變、404 先於 403、預算前置、REST 同實作；"
          "A20 數字可追溯（四捨五入、百分比、日期不算、捏造數字被抓）；data.readiness 依新鮮度設定（未設定 unknown／即時 ok／日批 06:59／07:00 邊界／late／missing）、依日期到齊（未設定 → null）、backfill／identity null + READINESS_PARTIAL、待重建日期、行級條件、參數與權限檢查")


if __name__ == "__main__":
    selftest()
