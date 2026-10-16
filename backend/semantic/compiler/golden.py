"""黃金測試：固定種子的合成事件 + 用戶表 → 同一組查詢分別用 Python oracle 與 ClickHouse（編譯出的 SQL）算，逐列比對誤差 0；
每條 SQL 另做 EXPLAIN ESTIMATE，估算掃描列數不得超過期間事件數的 1.5 倍（守住分區／主鍵裁剪）。

用法：python3 -m backend.semantic.compiler.golden                       # 乾跑：全部查詢編譯 + oracle 計算 + SQL 形狀檢查
     CLICKHOUSE_BIN=/path/clickhouse python3 -m backend.semantic.compiler.golden   # 真 ClickHouse 比對
     也由 backend.integration.e2e_clickhouse 在端到端最後呼叫 compiler_stage(ch)。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ...security.policy import Decision, Subject, authorize
from . import Catalog, compile_events, parse
from .oracle import Oracle
from .policy import BUDGETS

PROJECT = "golden"
SEED = 20261016
DAY0, DAYS = date(2026, 9, 20), 27                       # 2026-09-20 … 2026-10-16（跨月、跨週）
NOW = date(2026, 10, 16)
TPE = timedelta(hours=8)
EVENTS = ["bond_order", "bond_deal", "app_login_success", "hq_stockdetail_view", "$pageview", "order_rejected"]
EV_W = [20, 12, 25, 30, 25, 4]
HOUR_W = [1, 1, 1, 1, 1, 1, 2, 4, 8, 12, 12, 10, 8, 10, 9, 6, 4, 3, 3, 3, 2, 2, 1, 1]
LIFECYCLE = ["新客", "成長", "成熟", "沉睡", "流失"]
AGE = ["<25", "25-34", "35-44", "45-54", "55-64", "65+"]


@dataclass
class Dataset:
    users: dict[str, dict[str, Any]]
    events: list[dict[str, Any]]          # oracle 用：time 為台北時間（naive）
    segments: dict[str, set[str]]


def build(n_users: int = 400, n_events: int = 24_000, seed: int = SEED) -> Dataset:
    rng = random.Random(seed)
    users: dict[str, dict[str, Any]] = {}
    ids: list[str] = []
    for i in range(n_users):
        oid = "v1_" + "".join(rng.choice("0123456789abcdef") for _ in range(20))
        tier = "P5" if i < 5 else rng.choice(["P1", "P2", "P3", "P4"])         # P5 只有 5 人 → 觸發 K 匿名抑制
        sd = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 1000))
        aum = round(rng.lognormvariate(13, 1.2), 2)
        users[oid] = {
            "one_id": oid, "lifecycle": rng.choice(LIFECYCLE), "tier": tier, "age_band": rng.choice(AGE), "gender": rng.choice(["M", "F"]),
            "branch_id": f"B0{rng.randrange(1, 6)}", "first_utm_source": rng.choice(["google", "facebook", "line", "direct"]), "signup_date": sd,
            "account_age_days": float((NOW - sd).days), "aum_bucket": "≥500萬" if aum >= 5e6 else ("100–500萬" if aum >= 1e6 else "<100萬"),
            "has_margin": rng.randrange(0, 2), "churn_score": round(rng.random() * 100, 1), "pref_market": rng.choice(["TSE", "OTC", "US"]),
            "aum": aum, "is_first_trade": rng.randrange(0, 2), "last_active_at": datetime(2026, 10, 15, 12, 0, 0),
        }
        ids.append(oid)
    segments = {"vip": {o for o, u in users.items() if u["tier"] == "P1"}}
    events: list[dict[str, Any]] = []
    for i in range(n_events):
        oid = rng.choice(ids)
        ev = rng.choices(EVENTS, EV_W)[0]
        d = DAY0 + timedelta(days=rng.randrange(0, DAYS))
        h = rng.choices(range(24), HOUR_W)[0]
        t = datetime(d.year, d.month, d.day, h, rng.randrange(0, 60), rng.randrange(0, 60), rng.randrange(0, 1000) * 1000)
        plat = rng.choices(["iOS", "Android", "Web", ""], [40, 35, 20, 5])[0]
        props: dict[str, Any] = {}
        if rng.random() < 0.8: props["market"] = rng.choice(["TSE", "OTC", "US"])
        if plat in ("iOS", "Android") and rng.random() < 0.9: props["$app_version"] = rng.choice(["5.2.0", "5.1.3", "5.0.9"])
        if rng.random() < 0.5: props["score"] = rng.randrange(0, 101) if rng.random() < 0.5 else round(rng.random() * 100, 2)   # 整數與小數混放：驗 CAST
        amount = None
        if ev in ("bond_order", "bond_deal") and rng.random() < 0.9:
            amount = float(rng.randrange(1, 50_001) * 100)
        events.append({
            "project": PROJECT, "one_id": oid, "event": ev, "time": t, "event_date": d, "platform": plat, "channel": rng.choice(["app", "web", "agent"]),
            "utm_source": rng.choices(["google", "facebook", "line", ""], [3, 2, 1, 4])[0], "symbol": rng.choice(["2330", "2317", "0050", "2454", "00878", ""]),
            "amount": amount, "props": props, "event_id": hashlib.sha256(f"golden|{i}".encode()).hexdigest()[:32],
        })
    return Dataset(users, events, segments)


def ch_rows(ds: Dataset) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """oracle 列 → ClickHouse JSONEachRow 列（時間轉 UTC 字串，見 modeling.ms_to_dt 的教訓）。"""
    ing = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.000")
    ev = []
    for r in ds.events:
        tu = r["time"] - TPE
        ev.append({"project": PROJECT, "time": tu.strftime("%Y-%m-%d %H:%M:%S.") + f"{tu.microsecond // 1000:03d}", "event": r["event"], "source": "golden",
                   "one_id": r["one_id"], "distinct_id": r["one_id"], "event_id": r["event_id"], "platform": r["platform"], "channel": r["channel"],
                   "utm_source": r["utm_source"], "symbol": r["symbol"], "amount": r["amount"], "props": r["props"], "ingest_time": ing, "schema_version": 1, "batch_id": "golden"})
    us = []
    for u in ds.users.values():
        row = dict(u); row["signup_date"] = u["signup_date"].isoformat(); row["last_active_at"] = "2026-10-15 12:00:00.000"
        row["first_seen"] = "2026-09-01 00:00:00.000"; row["updated_at"] = "2026-10-16 05:00:00.000"
        us.append(row)
    seg = [{"segment_id": s, "version": 1, "one_id": o, "computed_at": "2026-10-16 06:00:00"} for s, members in ds.segments.items() for o in sorted(members)]
    return ev, us, seg


# ---------------------------------------------------------------- 查詢集（名稱, 查詢, 角色或 Decision）
def _subject(role: str) -> Decision:
    return authorize(Subject(f"u-{role}", [role]), "use", "events")


T = {"from": "2026-10-01", "to": "2026-10-16", "grain": "day", "now": NOW.isoformat()}
T_ALL = {"from": DAY0.isoformat(), "to": "2026-10-16", "grain": "day", "now": NOW.isoformat()}
QUERIES: list[tuple[str, dict[str, Any], Decision | str | None]] = [
    ("Q01 委託次數×日（走 MV）", {"metrics": [{"event": "bond_order", "agg": "total"}], "time": T}, "admin"),
    ("Q02 委託用戶數×日", {"metrics": [{"event": "bond_order", "agg": "users"}], "time": T}, "admin"),
    ("Q03 人均次數×合計", {"metrics": [{"event": "hq_stockdetail_view", "agg": "avg_per_user"}], "time": {**T, "grain": "total"}}, "admin"),
    ("Q04 成交金額×平台×日（走 MV，含未設值平台）", {"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount"}, {"event": "bond_deal", "agg": "total"}], "group_by": ["platform"], "time": T}, "data_analyst"),
    ("Q05 平均／最大／最小金額×週", {"metrics": [{"event": "bond_order", "agg": "avg", "field": "amount"}, {"event": "bond_order", "agg": "max", "field": "amount"}, {"event": "bond_order", "agg": "min", "field": "amount"}], "time": {**T_ALL, "grain": "week"}}, "data_analyst"),
    ("Q06 分位數×合計", {"metrics": [{"event": "bond_deal", "agg": a, "field": "amount"} for a in ("p25", "p50", "p75", "p90")], "time": {**T_ALL, "grain": "total"}}, "data_analyst"),
    ("Q07 去重商品數×渠道×月", {"metrics": [{"event": "hq_stockdetail_view", "agg": "uniq", "field": "symbol"}], "group_by": ["channel"], "time": {**T_ALL, "grain": "month"}}, "admin"),
    ("Q08 JSON 屬性分組（market）×日", {"metrics": [{"event": "bond_order", "agg": "total"}, {"event": "bond_order", "agg": "users"}], "group_by": ["market"], "time": T}, "admin"),
    ("Q09 時段篩選 9–13 點×小時", {"metrics": [{"event": "app_login_success", "agg": "total"}], "filters": [{"field": "$event_hour", "op": "between", "value": [9, 13]}], "time": {"from": "2026-10-14", "to": "2026-10-16", "grain": "hour", "now": NOW.isoformat()}}, "admin"),
    ("Q10 巢狀且／或 + in/like/set/nset", {"metrics": [{"event": None, "agg": "total"}, {"event": None, "agg": "users"}],
        "filters": {"op": "and", "items": [{"field": "platform", "op": "in", "value": ["iOS", "Android"]}, {"op": "or", "items": [{"field": "utm_source", "op": "like", "value": "oo"}, {"field": "utm_source", "op": "nset"}, {"field": "symbol", "op": "set"}]}, {"field": "props.$app_version", "op": "neq", "value": "5.0.9"}]},
        "time": T}, "admin"),
    ("Q11 指標內篩選 + 用戶屬性（dictGet）分組", {"metrics": [{"event": "bond_deal", "agg": "total", "filters": [{"field": "side", "op": "nset"}]}, {"event": "bond_deal", "agg": "users", "filters": [{"field": "amount", "op": "gte", "value": 1000000}]}],
        "group_by": ["user.age_band"], "time": {**T, "grain": "total"}}, "admin"),
    ("Q12 用戶屬性篩選（enum／bool／number／date rel）", {"metrics": [{"event": "app_login_success", "agg": "users"}],
        "filters": [{"field": "user.lifecycle", "op": "in", "value": ["新客", "成長"]}, {"field": "user.has_margin", "op": "true"}, {"field": "user.churn_score", "op": "lt", "value": 70}, {"field": "user.signup_date", "op": "rel", "value": {"days": 600}}], "time": T}, "admin"),
    ("Q13 分群篩選（segment in）", {"metrics": [{"event": "bond_order", "agg": "total"}], "segment": "vip", "time": {**T, "grain": "total"}}, "admin"),
    ("Q14 JSON 數值屬性（整數／小數混放）", {"metrics": [{"event": None, "agg": "avg", "field": "props.score", "type": "number"}, {"event": None, "agg": "p50", "field": "props.score", "type": "number"}, {"event": None, "agg": "uniq", "field": "props.score", "type": "number"}],
        "filters": [{"field": "props.score", "op": "gt", "value": 10, "type": "number"}], "time": {**T, "grain": "total"}}, "admin"),
    ("Q15 公式（成交率 %、除零）", {"metrics": [{"event": "bond_deal", "agg": "total", "alias": "deal"}, {"event": "bond_order", "agg": "total", "alias": "ord"}, {"event": "order_rejected", "agg": "total", "alias": "rej"}],
        "formulas": [{"alias": "deal_rate", "expr": "deal / ord * 100"}, {"alias": "rej_per_deal", "expr": "(rej + 1) / (deal - deal)"}], "group_by": ["platform"], "time": {**T, "grain": "week"}}, "admin"),
    ("Q16 top_n 商品（依第一指標）×日", {"metrics": [{"event": "hq_stockdetail_view", "agg": "total"}, {"event": "hq_stockdetail_view", "agg": "users"}], "group_by": ["symbol"], "top_n": 3, "time": T}, "admin"),
    ("Q17 mau（當月觸發用戶數，按天）", {"metrics": [{"event": "app_login_success", "agg": "mau"}, {"event": "app_login_success", "agg": "users"}], "time": T_ALL}, "admin"),
    ("Q18 K 匿名抑制（tier P5 只有 5 人）", {"metrics": [{"event": None, "agg": "total"}, {"event": "bond_deal", "agg": "sum", "field": "amount"}], "group_by": ["user.tier"], "time": {**T, "grain": "total"}}, "data_analyst"),
    ("Q19 行銷看金額只有區間（遮罩 bucket）", {"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount"}, {"event": "bond_deal", "agg": "avg", "field": "amount"}, {"event": "bond_deal", "agg": "users"}], "group_by": ["platform"], "time": {**T, "grain": "month"}}, "marketing"),
    ("Q20 回看天數行級條件（自訂角色 10 天）", {"metrics": [{"event": None, "agg": "total"}], "time": T_ALL},
        Decision(True, row_filters=["event_date >= today()-10"], field_policies={}, k_anonymity=20)),
    ("Q21 分公司行級條件（branch_id IN）", {"metrics": [{"event": "bond_order", "agg": "total"}, {"event": "bond_order", "agg": "users"}], "group_by": ["user.branch_id"], "time": {**T, "grain": "total"}},
        Decision(True, row_filters=["(branch_id IN ('B01','B02'))"], field_policies={}, k_anonymity=20)),
    ("Q22 compare 上一期×日", {"metrics": [{"event": "bond_order", "agg": "total"}, {"event": "bond_order", "agg": "users"}], "formulas": [{"alias": "per_user", "expr": "m1 / m2"}], "compare": "previous_period", "time": {"from": "2026-10-04", "to": "2026-10-10", "grain": "day", "now": NOW.isoformat()}}, "admin"),
    ("Q23 compare 上一期×月×平台", {"metrics": [{"event": None, "agg": "total"}], "group_by": ["platform"], "compare": "previous_period", "time": {"from": "2026-10-01", "to": "2026-10-16", "grain": "month", "now": NOW.isoformat()}}, "admin"),
    ("Q24 語意層指標 dau", {"metric": "dau", "time": T}, "admin"),
    ("Q25 語意層指標 order_reject_rate（ratio）", {"metric": "order_reject_rate", "time": {**T, "grain": "week"}}, "admin"),
    ("Q26 語意層指標 digital_self_service_rate（filter 片段）×平台", {"metric": "digital_self_service_rate", "group_by": ["platform"], "time": {**T, "grain": "total"}}, "admin"),
    ("Q27 三維分組（平台×渠道×market）×日", {"metrics": [{"event": None, "agg": "total"}], "group_by": ["platform", "channel", "market"], "time": {"from": "2026-10-10", "to": "2026-10-12", "grain": "day", "now": NOW.isoformat()}}, "admin"),
    ("Q28 exact 強制 L0 與 MV 結果一致", {"metrics": [{"event": "bond_order", "agg": "total"}, {"event": "bond_order", "agg": "sum", "field": "amount"}], "group_by": ["channel", "utm_source"], "filters": [{"field": "platform", "op": "neq", "value": "Web"}], "exact": True, "time": T}, "data_analyst"),
    ("Q29 分鐘粒度（單日）", {"metrics": [{"event": None, "agg": "total"}], "time": {"from": "2026-10-16", "to": "2026-10-16", "grain": "minute", "now": NOW.isoformat()}, "filters": [{"field": "$event_hour", "op": "eq", "value": 10}]}, "admin"),
    ("Q30 人均值 avg_val×渠道", {"metrics": [{"event": "bond_deal", "agg": "avg_val", "field": "amount"}, {"event": "bond_deal", "agg": "avg_per_user"}], "group_by": ["channel"], "time": {**T, "grain": "total"}}, "data_analyst"),
]
REJECTED: list[tuple[str, dict[str, Any], str, str]] = [   # 必須被拒絕的查詢（名稱, 查詢, 角色, 錯誤關鍵字）
    ("R01 未登記事件", {"metrics": [{"event": "no_such_event", "agg": "total"}], "time": T}, "admin", "事件字典"),
    ("R02 L4 個資屬性", {"metrics": [{"event": None, "agg": "total"}], "filters": [{"field": "user.mobile", "op": "set"}], "time": T}, "admin", "L4"),
    ("R03 L3 屬性不在寬表", {"metrics": [{"event": None, "agg": "total"}], "group_by": ["user.agent_id"], "time": T}, "admin", "L3"),
    ("R04 按 one_id 分組", {"metrics": [{"event": None, "agg": "total"}], "group_by": ["one_id"], "time": T}, "admin", "one_id"),
    ("R05 mau 非按天", {"metrics": [{"event": None, "agg": "mau"}], "time": {**T, "grain": "week"}}, "admin", "按天"),
    ("R06 遮罩指標進公式", {"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount", "alias": "amt"}, {"event": "bond_deal", "agg": "total", "alias": "n"}], "formulas": [{"alias": "x", "expr": "amt / n"}], "time": T}, "marketing", "區間"),
    ("R07 隱藏欄位", {"metrics": [{"event": None, "agg": "total"}], "filters": [{"field": "user.customer_code", "op": "set"}], "time": T}, "marketing", "L3"),
    ("R08 公式語法", {"metrics": [{"event": None, "agg": "total"}], "formulas": [{"alias": "x", "expr": "m1 ** 2"}], "time": T}, "admin", "公式"),
    ("R09 漏斗型指標走錯樣板", {"metric": "open_to_fund_rate", "time": T}, "admin", "樣板"),
    ("R10 AI 服務身份不可細查（authorize 拒絕）", {"metrics": [{"event": None, "agg": "total"}], "time": T}, "svc_insight_profile", "not granted"),
]


def decision_of(spec: Decision | str | None) -> Decision | None:
    if spec is None: return None
    if isinstance(spec, Decision): return spec
    if spec == "svc_insight_profile": return authorize(Subject("u-svc", ["svc_insight"]), "use", "profile", {"reason": "x"})
    return _subject(spec)


def parse_g(raw: dict[str, Any], cat: Catalog) -> Any:
    return parse({**raw, "project": PROJECT}, cat)


def compile_all(cat: Catalog) -> list[tuple[str, Any, Any, Decision | None]]:
    out = []
    for name, raw, spec in QUERIES:
        d = decision_of(spec)
        qr = parse_g(raw, cat)
        out.append((name, qr, compile_events(qr, cat, d), d))
    return out


def norm(v: Any) -> Any:
    if isinstance(v, bool): return v
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
    if isinstance(v, str) and len(v) == 19 and v[4] == "-" and v[10] == " ": return v
    return v


def same(a: Any, b: Any) -> bool:
    a, b = norm(a), norm(b)
    if a is None or b is None: return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool): return bool(a) == bool(b)
    if isinstance(a, int | float) and isinstance(b, int | float): return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return str(a) == str(b)


def compare(name: str, cols: list[str], expect: list[dict[str, Any]], got: list[dict[str, Any]], gcols: int) -> None:
    keyc = cols[: gcols]
    def key(r: dict[str, Any]) -> tuple:
        return tuple(str(norm(r.get(c))) if r.get(c) is not None else None for c in keyc)
    e = {key(r): r for r in expect}; g = {key(r): r for r in got}
    assert e.keys() == g.keys(), f"{name}: 分組鍵不一致 expect−got={sorted(set(e) - set(g), key=str)[:5]} got−expect={sorted(set(g) - set(e), key=str)[:5]}"
    for k, er in e.items():
        gr = g[k]
        for c in cols:
            assert same(er.get(c), gr.get(c)), f"{name}: 列 {k} 欄 {c} expect={er.get(c)!r} got={gr.get(c)!r}"


def run_sql(ch: Any, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    txt = ch.query(sql + " FORMAT JSONEachRow", settings={**settings, "output_format_json_quote_64bit_integers": 0, "output_format_json_quote_denormals": 0})
    return [json.loads(l) for l in txt.splitlines() if l.strip()]


def estimate(ch: Any, sql: str) -> dict[str, int]:
    """EXPLAIN ESTIMATE → 每張表的估算列數。"""
    out: dict[str, int] = {}
    for l in ch.query("EXPLAIN ESTIMATE " + sql + " FORMAT TSV").splitlines():
        p = l.split("\t")
        if len(p) >= 4:
            out[f"{p[0]}.{p[1]}"] = out.get(f"{p[0]}.{p[1]}", 0) + int(p[3])
    return out


def compiler_stage(ch: Any, ds: Dataset | None = None, verbose: bool = True) -> dict[str, Any]:
    """載入黃金資料集到 ClickHouse → 30 條查詢 oracle vs SQL 逐列比對 → EXPLAIN ESTIMATE 門檻 → 10 條必須拒絕。由 e2e 或 __main__ 呼叫。"""
    cat = Catalog()
    ds = ds or build()
    ev, us, seg = ch_rows(ds)
    ch.query("CREATE TABLE IF NOT EXISTS dmp.segment_members (segment_id LowCardinality(String), version UInt32, one_id String, one_id_uint UInt64 DEFAULT cityHash64(one_id), computed_at DateTime) ENGINE = MergeTree ORDER BY (segment_id, version, one_id_uint)")
    ch.query(f"ALTER TABLE dmp.events DELETE WHERE project = '{PROJECT}'", settings={"mutations_sync": 2})
    for i in range(0, len(ev), 20_000):
        ch.insert_json("dmp.events", ev[i:i + 20_000])
    ch.insert_json("dmp.users", us)
    ch.insert_json("dmp.segment_members", seg)
    ch.query("SYSTEM RELOAD DICTIONARY dmp.users_dict")
    n = ch.scalar(f"SELECT count() FROM dmp.events WHERE project = '{PROJECT}'")
    assert n == len(ev), (n, len(ev))
    assert ch.scalar(f"SELECT count() FROM dmp.events WHERE project = '{PROJECT}' AND event_date NOT BETWEEN toDate('{DAY0}') AND toDate('2026-10-16')") == 0
    oracle = Oracle(ds.events, ds.users, ds.segments)
    ratio, slack = BUDGETS["estimate"]["max_rows_ratio"], BUDGETS["estimate"]["granule_slack"]
    layers: dict[str, int] = {}
    report: list[str] = []
    for name, qr, c, d in compile_all(cat):
        expect = oracle.run(qr, d)
        got = run_sql(ch, c.sql, c.settings)
        gcols = (1 if qr.time.grain != "total" else 0) + len(qr.group_by)
        compare(name, c.columns, expect, got, gcols)
        in_range = sum(1 for r in ds.events if qr.time.start <= r["event_date"] <= qr.time.end)
        scans = c.sql.count("FROM dmp.events")                      # compare／mau／top_n 會多掃一次（各自裁剪）
        est = estimate(ch, c.sql)
        scanned = sum(est.values())                                 # MV 的內部表名是 .inner_id.*，一律加總
        assert scanned <= scans * (ratio * in_range + slack), f"{name}: EXPLAIN ESTIMATE rows={scanned} > {scans}×({ratio}×{in_range}+{slack})（分區／主鍵沒裁剪？）"
        layers[c.layer] = layers.get(c.layer, 0) + 1
        report.append(f"  {name:<44} {c.layer} rows={len(got):<4} est={scanned:<7} k={c.k_anonymity} masked={list(c.masked) or '-'}")
    # MV 與明細一致：Q28（exact）vs 同查詢走 MV
    q28 = next(x for x in QUERIES if x[0].startswith("Q28"))
    raw_mv = {k: v for k, v in q28[1].items() if k != "exact"}
    qm = parse_g(raw_mv, cat); cm = compile_events(qm, cat, decision_of(q28[2]))
    assert cm.layer == "L1", cm.warnings
    compare("Q28 MV vs L0", cm.columns, run_sql(ch, cm.sql, cm.settings), run_sql(ch, next(c for n, _, c, _ in compile_all(cat) if n.startswith("Q28")).sql, cm.settings), 3)
    # 抽樣：按用戶分桶，抽樣後的用戶數 ≤ 全量，且事件數比例落在合理區間
    qs = parse_g({"metrics": [{"event": None, "agg": "total"}, {"event": None, "agg": "users"}], "sample": 0.5, "time": {**T_ALL, "grain": "total"}}, cat)
    cs = compile_events(qs, cat, _subject("admin")); rs = run_sql(ch, cs.sql, cs.settings)[0]
    assert "SAMPLE 0.5" in cs.sql and 0.3 * len(ds.events) < rs["m1"] < 0.7 * len(ds.events) and rs["m2"] <= len(ds.users), rs
    # 必須拒絕
    for name, raw, role, kw in REJECTED:
        try:
            compile_events(parse_g(raw, cat), cat, decision_of(role))
        except (ValueError, PermissionError) as e:
            assert kw in str(e), f"{name}: 錯誤訊息應含「{kw}」：{e}"
        else:
            raise AssertionError(f"{name}: 應被拒絕")
    if verbose:
        print("\n".join(report))
        print(f"compiler golden OK: {len(QUERIES)} queries (layers {layers}), {len(REJECTED)} rejected, sample ok, MV==L0")
    return {"queries": len(QUERIES), "layers": layers}


def dry_run() -> None:
    cat = Catalog(); ds = build(); oracle = Oracle(ds.events, ds.users, ds.segments)
    h = hashlib.sha256()
    for name, qr, c, d in compile_all(cat):
        rows = oracle.run(qr, d)
        assert rows, name
        assert c.sql.count("(") == c.sql.count(")"), name
        assert f"project = '{PROJECT}'" in c.sql and "event_date BETWEEN" in c.sql, name
        assert c.settings["max_execution_time"] == 60 and c.settings["max_result_rows"] == 10000, name
        if d is not None:
            assert c.k_anonymity == 20 and "suppressed" in c.columns, name
        h.update(c.sql.encode())
    for name, raw, role, kw in REJECTED:
        try:
            compile_events(parse_g(raw, cat), cat, decision_of(role))
        except (ValueError, PermissionError) as e:
            assert kw in str(e), f"{name}: {e}"
        else:
            raise AssertionError(f"{name}: 應被拒絕")
    c1 = compile_events(parse_g(QUERIES[0][1], cat), cat, _subject("admin")); c4 = compile_events(parse_g(QUERIES[3][1], cat), cat, _subject("data_analyst"))
    assert c1.layer == "L1" and c4.layer == "L1" and "events_daily_agg" in c1.sql, (c1.layer, c4.layer)
    c19 = compile_events(parse_g(QUERIES[18][1], cat), cat, _subject("marketing"))
    assert c19.masked == {"m1": "bucket([0,1e5,5e5,1e6,5e6])", "m2": "bucket([0,1e5,5e5,1e6,5e6])"} and "multiIf" in c19.sql, c19.masked
    print(f"compiler dry run OK: {len(QUERIES)} queries compiled + oracle, {len(REJECTED)} rejected, sql digest {h.hexdigest()[:12]}")


if __name__ == "__main__":
    binary = os.environ.get("CLICKHOUSE_BIN")
    dry_run()
    if binary:
        import shutil
        import subprocess
        import tempfile

        from ...integration.e2e_clickhouse import HTTP_PORT, start_clickhouse
        from ...integration.runner import ClickHouseHTTP, local_ddl
        work = Path(tempfile.mkdtemp(prefix="dmp-golden-"))
        proc = start_clickhouse(binary, work)
        try:
            ch = ClickHouseHTTP(f"http://127.0.0.1:{HTTP_PORT}", timeout=600)
            for st in local_ddl((Path(__file__).resolve().parents[2] / "clickhouse" / "schema.sql").read_text(encoding="utf-8")):
                ch.query(st)
            print("ClickHouse", ch.query("SELECT version()").strip())
            compiler_stage(ch)
        finally:
            proc.terminate()
            try: proc.wait(10)
            except subprocess.TimeoutExpired: proc.kill()
            shutil.rmtree(work, ignore_errors=True)
    sys.exit(0)
