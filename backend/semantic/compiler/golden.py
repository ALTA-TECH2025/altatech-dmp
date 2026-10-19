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
from dataclasses import dataclass, field
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
    edges: list[dict[str, Any]] = field(default_factory=list)     # id_edges（身分圖；用戶細查用）


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
    segments = {"vip": {o for o, u in users.items() if u["tier"] == "P1"}, "golden_all": set(users)}   # golden_all：用戶表是全公司共用（沒有 project），純屬性的黃金查詢用它把母體框在資料集內
    edges: list[dict[str, Any]] = []
    for i, oid in enumerate(ids[:60]):                           # 前 60 人有身分圖：login ↔ device，偶數再加 cookie
        base = {"source": "golden.login", "evidence_event_id": f"golden-ev-{i}", "first_seen": "2026-09-01 00:00:00.000", "last_seen": "2026-10-15 12:00:00.000", "seen_count": 3 + i % 5, "confidence": 1.0, "status": "active", "one_id": oid, "version": 1}
        edges.append({**base, "id_type_a": "login_id", "id_a": f"L{i:05d}", "id_type_b": "device_id", "id_b": f"D{i:05d}a"})
        if i % 2 == 0:
            edges.append({**base, "source": "golden.cooccur", "confidence": 0.8, "id_type_a": "login_id", "id_a": f"L{i:05d}", "id_type_b": "cookie_id", "id_b": f"C{i:05d}"})
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
        if ev in ("bond_order", "bond_deal", "order_rejected") and rng.random() < 0.95:
            props["order_no"] = f"O{rng.randrange(1, 1500)}"              # 業務 ID：委託與成交共用同一池，讓關聯屬性能串起流程
        events.append({
            "project": PROJECT, "one_id": oid, "event": ev, "time": t, "event_date": d, "platform": plat, "channel": rng.choice(["app", "web", "agent"]),
            "utm_source": rng.choices(["google", "facebook", "line", ""], [3, 2, 1, 4])[0], "utm_campaign": "", "symbol": rng.choice(["2330", "2317", "0050", "2454", "00878", ""]),
            "amount": amount, "props": props, "event_id": hashlib.sha256(f"golden|{i}".encode()).hexdigest()[:32],
        })
    events += scenario_events(rng, ids)
    return Dataset(users, events, segments, edges)


def scenario_events(rng: random.Random, ids: list[str]) -> list[dict[str, Any]]:
    """情境示範看板用的事件（docs/27 需求表 A4／A5／C2／C5／D4／E3）：開戶流程、推播／活動點擊追到交易、搜尋→看盤→委託、定期定額。
    只落在 2026-10-01 … 10-16、每種事件每天人數 ≥ K（20），日線圖才不會整張被 K 匿名抑制；oracle 與 SQL 一樣從這份資料算，黃金查詢不受影響。"""
    out: list[dict[str, Any]] = []
    n = [0]
    def put(oid: str, ev: str, t: datetime, plat: str, *, channel: str = "app", utm_source: str = "", utm_campaign: str = "", symbol: str = "", amount: float | None = None, props: dict[str, Any] | None = None) -> None:
        n[0] += 1
        if t.date() > NOW: return                                  # 隔天／兩天後的後續行為不能落到資料集範圍（DAY0 … NOW）之外，否則 ClickHouse 端的範圍斷言會失敗
        out.append({"project": PROJECT, "one_id": oid, "event": ev, "time": t, "event_date": t.date(), "platform": plat, "channel": channel, "utm_source": utm_source, "utm_campaign": utm_campaign,
                    "symbol": symbol, "amount": amount, "props": props or {}, "event_id": hashlib.sha256(f"golden-scenario|{n[0]}".encode()).hexdigest()[:32]})
    def day_time(day: int) -> datetime:
        d = date(2026, 10, 1) + timedelta(days=day)
        return datetime(d.year, d.month, d.day, rng.choices(range(24), HOUR_W)[0], rng.randrange(0, 60), rng.randrange(0, 60), rng.randrange(0, 1000) * 1000)
    plat_of = lambda: rng.choices(["iOS", "Android", "Web"], [45, 35, 20])[0]
    def n_of(base: float, day: int) -> int:
        """每天人數：週末七折、±15% 抖動；基數本身可帶趨勢（讓解讀規則有上升／下降可講）。"""
        wk = (date(2026, 10, 1) + timedelta(days=day)).weekday()
        return max(1, int(base * (0.7 if wk >= 5 else 1.0) * (0.85 + 0.3 * rng.random())))
    # A5／C2／C5 開戶：每天約 105 人進開戶首頁（三個來源平均，分組日線 ≥ K）→ 55% 當場完成申請（同一會話內）→ 70% 隔天登入 → 50% 兩天後委託
    for day in range(16):
        for oid in rng.sample(ids, n_of(105, day)):
            plat = plat_of(); src = rng.choice(["google", "facebook", "line"])
            t = day_time(day); put(oid, "kh_entry_khpg_pv", t, plat, utm_source=src, channel="web" if plat == "Web" else "app")
            if rng.random() < 0.55:
                put(oid, "kh_apply", t + timedelta(minutes=rng.randrange(3, 22)), plat, utm_source=src, channel="web" if plat == "Web" else "app")
                if rng.random() < 0.7:
                    put(oid, "app_login_success", t + timedelta(days=1, minutes=rng.randrange(0, 600)), plat)
                    if rng.random() < 0.5:
                        put(oid, "bond_order", t + timedelta(days=2, minutes=rng.randrange(0, 600)), plat, symbol=rng.choice(["2330", "0050", "2454"]), amount=float(rng.randrange(1, 20_001) * 100), props={"order_no": f"S{n[0]}"})
    # A4／D4／E3 推播：三個發送計畫，每天 85 → 125 人點擊（逐日上升；每計畫 ≥ K）→ 45% 委託送出（一半在同一會話、一半兩天內）→ 60% 成交（帶金額）
    camps = ["Q4_bond_promo", "sip_launch", "reactivation_oct"]
    for day in range(16):
        for oid in rng.sample(ids, n_of(85 + 40 * day / 15, day)):
            plat = rng.choice(["iOS", "Android"]); camp = rng.choices(camps, [4, 3, 3])[0]
            t = day_time(day); put(oid, "push_click", t, plat, channel="push", utm_source="push", utm_campaign=camp)
            if rng.random() < 0.45:
                t2 = t + timedelta(minutes=rng.randrange(4, 20) if rng.random() < 0.5 else rng.randrange(60, 2 * 24 * 60)); sym = rng.choice(["2330", "2317", "0050", "2454", "00878"])
                put(oid, "stock_order_submit", t2, plat, symbol=sym, utm_campaign=camp, props={"order_no": f"P{n[0]}"})
                if rng.random() < 0.6:
                    put(oid, "bond_deal", t2 + timedelta(minutes=rng.randrange(1, 9)), plat, symbol=sym, amount=float(rng.randrange(1, 30_001) * 100), utm_campaign=camp, props={"order_no": f"P{n[0] - 1}"})
    # 活動：每天 120 → 95 人點活動頁（逐日下降；渠道 app／web／line 各 ≥ K）
    for day in range(16):
        for oid in rng.sample(ids, n_of(120 - 25 * day / 15, day)):
            plat = plat_of(); put(oid, "act_click", day_time(day), plat, channel=rng.choices(["app", "web", "line"], [4, 3, 3])[0], utm_source=rng.choice(["line", "facebook", ""]))
    # C1／C4 搜尋 → 看盤 → 委託送出（同一會話，30 分鐘內）：每天約 45 人搜尋 → 65% 看盤 → 35% 委託送出 → 50% 成交；定期定額每天約 24 筆申購
    for day in range(16):
        for oid in rng.sample(ids, n_of(45, day)):
            plat = plat_of(); t = day_time(day); sym = rng.choice(["2330", "2317", "0050", "2454", "00878", "2603"])
            put(oid, "search_submit", t, plat, symbol=sym, props={"keyword": sym})
            if rng.random() < 0.65:
                t2 = t + timedelta(minutes=rng.randrange(1, 12)); put(oid, "hq_stockdetail_view", t2, plat, symbol=sym)
                if rng.random() < 0.35:
                    t3 = t2 + timedelta(minutes=rng.randrange(2, 12)); put(oid, "stock_order_submit", t3, plat, symbol=sym, props={"order_no": f"Q{n[0]}"})
                    if rng.random() < 0.5:
                        put(oid, "bond_deal", t3 + timedelta(minutes=rng.randrange(1, 8)), plat, symbol=sym, amount=float(rng.randrange(1, 30_001) * 100), props={"order_no": f"Q{n[0] - 1}"})
        for oid in rng.sample(ids, n_of(24, day)):
            put(oid, "sip_purchase", day_time(day), plat_of(), symbol=rng.choice(["0050", "00878", "006208"]), amount=float(rng.choice([3000, 5000, 10000, 20000])))
    # 登入會話鏈（路徑分析才有分支）：每天約 55 人 登入 → 看盤（2–9 分）→ 60% 委託（3–12 分）→ 70% 成交（1–6 分）
    for day in range(16):
        for oid in rng.sample(ids, n_of(55, day)):
            plat = plat_of(); t = day_time(day); sym = rng.choice(["2330", "2317", "0050", "2454", "00878"])
            put(oid, "app_login_success", t, plat)
            t2 = t + timedelta(minutes=rng.randrange(2, 10)); put(oid, "hq_stockdetail_view", t2, plat, symbol=sym)
            if rng.random() < 0.6:
                t3 = t2 + timedelta(minutes=rng.randrange(3, 13)); amt = float(rng.randrange(1, 50_001) * 100); on = f"L{n[0]}"
                put(oid, "bond_order", t3, plat, symbol=sym, amount=amt, props={"order_no": on})
                if rng.random() < 0.7:
                    put(oid, "bond_deal", t3 + timedelta(minutes=rng.randrange(1, 7)), plat, symbol=sym, amount=amt, props={"order_no": on})
    return out


def ch_rows(ds: Dataset) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """oracle 列 → ClickHouse JSONEachRow 列（時間轉 UTC 字串，見 modeling.ms_to_dt 的教訓）。"""
    ing = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.000")
    ev = []
    for r in ds.events:
        tu = r["time"] - TPE
        ev.append({"project": PROJECT, "time": tu.strftime("%Y-%m-%d %H:%M:%S.") + f"{tu.microsecond // 1000:03d}", "event": r["event"], "source": "golden",
                   "one_id": r["one_id"], "distinct_id": r["one_id"], "event_id": r["event_id"], "platform": r["platform"], "channel": r["channel"],
                   "utm_source": r["utm_source"], "utm_campaign": r.get("utm_campaign", ""), "symbol": r["symbol"], "amount": r["amount"], "props": r["props"], "ingest_time": ing, "schema_version": 1, "batch_id": "golden"})
    us = []
    for u in ds.users.values():
        row = dict(u); row["signup_date"] = u["signup_date"].isoformat(); row["last_active_at"] = "2026-10-15 12:00:00.000"
        row["first_seen"] = "2026-09-01 00:00:00.000"; row["updated_at"] = "2026-10-16 05:00:00.000"
        us.append(row)
    seg = [{"segment_id": s, "version": 1, "one_id": o, "computed_at": "2026-10-16 06:00:00"} for s, members in ds.segments.items() for o in sorted(members)]
    return ev, us, seg, list(ds.edges)


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
    ("Q30 行為篩選：期間內成交 ≥ 2 次且未被拒單 × 平台", {"metrics": [{"event": "bond_order", "agg": "total"}, {"event": "bond_order", "agg": "users"}], "group_by": ["platform"],
        "behaviors": [{"event": "bond_deal", "cmp": {"op": "gte", "value": 2}}, {"event": "order_rejected", "done": False}], "time": {**T, "grain": "total"}}, "admin"),
    ("Q31 對照組：iOS vs Android（含對比上期）", {"metrics": [{"event": "bond_deal", "agg": "total"}], "compare": "previous_period",
        "cohorts": [{"name": "iOS", "filters": [{"field": "platform", "op": "eq", "value": "iOS"}]}, {"name": "Android", "filters": [{"field": "platform", "op": "eq", "value": "Android"}]}, {"name": "VIP 有看盤", "segment": "vip", "behaviors": [{"event": "hq_stockdetail_view", "time": {"last_days": 30}}]}],
        "time": {"from": "2026-10-10", "to": "2026-10-16", "grain": "day", "now": NOW.isoformat()}}, "admin"),
    ("Q30 人均值 avg_val×渠道", {"metrics": [{"event": "bond_deal", "agg": "avg_val", "field": "amount"}, {"event": "bond_deal", "agg": "avg_per_user"}], "group_by": ["channel"], "time": {**T, "grain": "total"}}, "data_analyst"),
    ("Q31 快速估算：人數走 MV（uniqMerge；種子資料量小，與精確值相同）", {"metrics": [{"event": "bond_order", "agg": "users"}], "group_by": ["platform"], "estimate": True, "time": T}, "admin"),
]
W7 = {"value": 7, "unit": "day"}
FUNNELS: list[tuple[str, dict[str, Any], Decision | str | None]] = [
    ("F01 看盤→委託→成交 7 天", {"steps": [{"event": "hq_stockdetail_view"}, {"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "time": T_ALL}, "admin"),
    ("F02 30 分鐘窗口", {"steps": [{"event": "app_login_success"}, {"event": "bond_order"}], "window": {"value": 30, "unit": "minute"}, "time": T_ALL}, "admin"),
    ("F03 關聯屬性 order_no（委託→成交同一單）", {"steps": [{"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "relation": "props.order_no", "time": T_ALL}, "admin"),
    ("F04 關聯屬性 + 次數口徑", {"steps": [{"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "relation": "props.order_no", "metric": "times", "time": T_ALL}, "admin"),
    ("F05 分組 platform（取第一步事件）", {"steps": [{"event": "hq_stockdetail_view"}, {"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "group_by": ["platform"], "time": T}, "admin"),
    ("F06 趨勢 day", {"steps": [{"event": "app_login_success"}, {"event": "bond_order"}], "window": {"value": 1, "unit": "day"}, "trend": "day", "time": {"from": "2026-10-10", "to": "2026-10-16", "now": NOW.isoformat()}}, "admin"),
    ("F07 每步篩選 + 全局篩選 + 用戶屬性", {"steps": [{"event": "hq_stockdetail_view", "filters": [{"field": "symbol", "op": "set"}]}, {"event": "bond_order", "filters": [{"field": "amount", "op": "gte", "value": 100000}]}, {"event": "bond_deal"}],
        "window": W7, "filters": [{"field": "platform", "op": "in", "value": ["iOS", "Android"]}, {"field": "user.has_margin", "op": "true"}], "time": T_ALL}, "admin"),
    ("F08 分群 vip + 行銷（K 匿名）", {"steps": [{"event": "app_login_success"}, {"event": "bond_deal"}], "window": W7, "segment": "vip", "group_by": ["user.tier"], "time": T_ALL}, "marketing"),
    ("F09 90 天窗口、月趨勢、分群 market", {"steps": [{"event": "hq_stockdetail_view"}, {"event": "bond_deal"}], "window": {"value": 90, "unit": "day"}, "trend": "month", "group_by": ["market"], "time": T_ALL}, "admin"),
    ("F10 行為篩選（曾登入 ≥ 1 次）＋ 對照組 iOS／全體", {"steps": [{"event": "hq_stockdetail_view"}, {"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "behaviors": [{"event": "app_login_success"}],
        "cohorts": [{"name": "iOS", "filters": [{"field": "platform", "op": "eq", "value": "iOS"}]}, {"name": "全體"}], "time": T_ALL}, "admin"),
]
RETENTIONS: list[tuple[str, dict[str, Any], Decision | str | None]] = [
    ("R01 登入→任一事件 7 日留存（date）", {"initial": {"event": "app_login_success"}, "model": "date", "periods": 7, "time": T}, "admin"),
    ("R02 看盤→成交 7 日內留存（interval，含當期）", {"initial": {"event": "hq_stockdetail_view"}, "return": {"event": "bond_deal"}, "model": "interval", "periods": 7, "include_day0": True, "time": T}, "admin"),
    ("R03 連續留存 3 日", {"initial": {"event": "app_login_success"}, "return": {"event": "app_login_success"}, "model": "continuous", "periods": 3, "time": T}, "admin"),
    ("R04 流失（第 n 日起不再回訪）", {"initial": {"event": "bond_order"}, "return": {"event": "bond_order"}, "model": "churn", "periods": 5, "time": T}, "admin"),
    ("R05 週留存 × 平台分組", {"initial": {"event": "app_login_success"}, "model": "date", "unit": "week", "periods": 3, "group_by": ["platform"], "time": T_ALL}, "admin"),
    ("R06 月留存（跨月）", {"initial": {"event": "hq_stockdetail_view"}, "return": {"event": "bond_order"}, "model": "interval", "unit": "month", "periods": 1, "include_day0": True, "time": T_ALL}, "admin"),
    ("R07 篩選 + 用戶屬性 + 初始事件篩選", {"initial": {"event": "bond_order", "filters": [{"field": "amount", "op": "gte", "value": 100000}]}, "return": {"event": "bond_deal"}, "model": "date", "periods": 5,
        "filters": [{"field": "user.lifecycle", "op": "in", "value": ["新客", "成長"]}], "time": T}, "admin"),
    ("R08 分群 vip + 行銷（K 匿名）× tier", {"initial": {"event": "app_login_success"}, "model": "interval", "periods": 3, "segment": "vip", "group_by": ["user.tier"], "time": T}, "marketing"),
    ("R09 行為篩選（期間內未被拒單）＋ 對照組 有成交／全體", {"initial": {"event": "app_login_success"}, "model": "date", "periods": 3, "behaviors": [{"event": "order_rejected", "done": False}],
        "cohorts": [{"name": "有成交", "behaviors": [{"event": "bond_deal"}]}, {"name": "全體"}], "time": T}, "admin"),
]
RETENTION_REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("RR1 沒初始事件", {"model": "date", "time": T}, "初始行為"),
    ("RR2 期數 91", {"initial": {"event": "bond_order"}, "periods": 91, "time": T}, "1–90"),
    ("RR3 未知模型", {"initial": {"event": "bond_order"}, "model": "nope", "time": T}, "model"),
    ("RR4 未登記回訪事件", {"initial": {"event": "bond_order"}, "return": {"event": "nope"}, "time": T}, "事件字典"),
]
FUNNEL_REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("FR1 一步", {"steps": [{"event": "bond_order"}], "window": W7, "time": T}, "2–10 步"),
    ("FR2 窗口 91 天", {"steps": [{"event": "bond_order"}, {"event": "bond_deal"}], "window": {"value": 91, "unit": "day"}, "time": T}, "90 天"),
    ("FR3 次數口徑沒關聯屬性", {"steps": [{"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "metric": "times", "time": T}, "關聯屬性"),
    ("FR4 關聯屬性是用戶屬性", {"steps": [{"event": "bond_order"}, {"event": "bond_deal"}], "window": W7, "relation": "user.tier", "time": T}, "業務 ID"),
    ("FR5 未登記事件", {"steps": [{"event": "bond_order"}, {"event": "nope"}], "window": W7, "time": T}, "事件字典"),
]
PATHS: list[tuple[str, dict[str, Any], Decision | str | None]] = [
    ("P01 登入之後 3 步（間隔 30 分切會話、合併重複、離開）", {"anchor": {"event": "app_login_success"}, "steps": 3, "time": T}, "admin"),
    ("P02 成交之前 3 步", {"anchor": {"event": "bond_deal"}, "direction": "before", "steps": 3, "time": T}, "admin"),
    ("P03 瀏覽依平台拆分 + 只留 3 個事件其餘折「其他」", {"anchor": {"event": "app_login_success"}, "steps": 4, "nodes": {"events": ["app_login_success", "$pageview", "bond_order"], "collapse_other": True, "split": [{"event": "$pageview", "field": "platform"}]}, "time": T}, "admin"),
    ("P04 跨會話整段、不合併、不補離開、5 步", {"anchor": {"event": "bond_order"}, "steps": 5, "session": {"mode": "cross"}, "nodes": {"merge_repeats": False, "exit_node": False}, "time": {"from": "2026-10-10", "to": "2026-10-16", "now": NOW.isoformat()}}, "admin"),
    ("P05 以 session_id 切（黃金資料沒有 → 一條）+ 每會話最多 30 事件", {"anchor": {"event": "hq_stockdetail_view"}, "steps": 2, "session": {"mode": "id", "max_events": 30}, "time": T}, "admin"),
    ("P06 起點條件（委託金額 ≥ 10 萬）+ 用戶屬性 + 間隔 120 分", {"anchor": {"event": "bond_order", "filters": [{"field": "amount", "op": "gte", "value": 100000}]}, "steps": 3, "session": {"gap_minutes": 120}, "filters": [{"field": "user.has_margin", "op": "true"}], "time": T}, "admin"),
    ("P07 分群 vip + 行銷（K 匿名）", {"anchor": {"event": "app_login_success"}, "steps": 2, "segment": "vip", "time": T}, "marketing"),
    ("P08 終點「離開」之前 2 步", {"anchor": {"event": "離開"}, "direction": "before", "steps": 2, "top_n": 30, "time": T}, "admin"),
]
PATH_REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("PR1 未登記事件", {"anchor": {"event": "nope"}, "time": T}, "事件字典"),
    ("PR2 步數 11", {"anchor": {"event": "bond_order"}, "steps": 11, "time": T}, "1–10"),
    ("PR3 方向", {"anchor": {"event": "bond_order"}, "direction": "sideways", "time": T}, "direction"),
    ("PR4 超過 92 天", {"anchor": {"event": "bond_order"}, "time": {"from": "2026-01-01", "to": "2026-10-16"}}, "92 天"),
    ("PR5 拆分欄位 one_id", {"anchor": {"event": "bond_order"}, "nodes": {"split": [{"event": "$pageview", "field": "one_id"}]}, "time": T}, "one_id"),
]
TOUCHES = [{"event": "$pageview", "alias": "瀏覽"}, {"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "app_login_success", "alias": "登入"}]
ATTRIBUTIONS: list[tuple[str, dict[str, Any], Decision | str | None]] = [
    ("A01 成交 × 三觸點 × 首次／末次／線性，7 天", {"target": {"event": "bond_deal"}, "touches": TOUCHES, "models": ["first", "last", "linear"], "window_days": 7, "time": T}, "admin"),
    ("A02 金額價值 × 位置（0.3/0.5）× 時間衰減（半衰期 2 天）× 自訂權重，3 天", {"target": {"event": "bond_deal", "value_field": "amount"}, "touches": [{**TOUCHES[0], "weight": 3}, {**TOUCHES[1], "weight": 1}, {**TOUCHES[2], "weight": 0}], "models": ["position", "decay", "custom"], "position_weights": {"first": 0.3, "last": 0.5}, "half_life_days": 2, "window_days": 3, "time": T}, "admin"),
    ("A03 依平台拆分 × 線性，14 天", {"target": {"event": "bond_order"}, "touches": TOUCHES[:2], "split": "platform", "models": ["linear", "last"], "window_days": 14, "time": T}, "admin"),
    ("A04 轉化條件（金額 ≥ 10 萬）+ 觸點條件（iOS 看盤）+ 用戶屬性，1 天", {"target": {"event": "bond_order", "filters": [{"field": "amount", "op": "gte", "value": 100000}]}, "touches": [{"event": "hq_stockdetail_view", "alias": "iOS看盤", "filters": [{"field": "platform", "op": "eq", "value": "iOS"}]}, {"event": "hq_stockdetail_view", "alias": "其他看盤"}], "models": ["first", "linear"], "window_days": 1, "filters": [{"field": "user.has_margin", "op": "true"}], "time": T}, "admin"),
    ("A05 分群 vip + 行銷（K 匿名）× 市場拆分", {"target": {"event": "bond_deal"}, "touches": TOUCHES, "split": "market", "models": ["linear"], "segment": "vip", "window_days": 7, "time": T}, "marketing"),
    ("A06 全模型 × 90 天窗口（回溯到資料起點前）", {"target": {"event": "order_rejected"}, "touches": TOUCHES, "models": ["first", "last", "linear", "position", "decay", "custom"], "window_days": 90, "time": T}, "admin"),
]
ATTRIBUTION_REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("AR1 轉化事件也是觸點", {"target": {"event": "bond_deal"}, "touches": [{"event": "bond_deal"}], "time": T}, "不能同時"),
    ("AR2 未知模型", {"target": {"event": "bond_deal"}, "touches": TOUCHES, "models": ["magic"], "time": T}, "models"),
    ("AR3 位置權重超過 1", {"target": {"event": "bond_deal"}, "touches": TOUCHES, "position_weights": {"first": 0.7, "last": 0.5}, "time": T}, "≤ 1"),
    ("AR4 窗口 91 天", {"target": {"event": "bond_deal"}, "touches": TOUCHES, "window_days": 91, "time": T}, "1–90"),
    ("AR5 價值欄位非數值", {"target": {"event": "bond_deal", "value_field": "platform"}, "touches": TOUCHES, "time": T}, "數值"),
]
BRANCH = Decision(True, row_filters=["branch_id IN ('B01','B02')", "event_date >= today()-10"], field_policies={"one_id": "mask_tail(4)", "churn_score": "bucket([0,40,70,100])"}, k_anonymity=20)   # 分公司範圍 + 回溯 10 天的自訂角色
USERS: list[tuple[str, dict[str, Any], Decision | str | None]] = [
    ("U01 價值分級 P1/P2 且信用戶", {"segment": "golden_all", "filters": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}, {"field": "user.has_margin", "op": "true"}]}, "admin"),
    ("U02 分群 vip + 生命週期", {"segment": "vip", "filters": [{"field": "user.lifecycle", "op": "in", "value": ["新客", "成長"]}]}, "admin"),
    ("U03 資產 ≥ 100 萬 或 流失分 ≥ 70（排序流失分）", {"segment": "golden_all", "filters": {"op": "or", "items": [{"field": "user.aum", "op": "gte", "value": 1e6}, {"field": "user.churn_score", "op": "gte", "value": 70}]}}, "data_analyst"),
    ("U04 做過成交 ≥ 3 次", {"behaviors": [{"event": "bond_deal", "count": {"op": "gte", "value": 3}, "time": T}]}, "admin"),
    ("U05 近 7 天沒委託失敗 且 新客（頂層 time 預設）", {"segment": "golden_all", "filters": [{"field": "user.lifecycle", "op": "eq", "value": "新客"}], "behaviors": [{"event": "order_rejected", "done": False}], "time": {"from": "2026-10-10", "to": "2026-10-16", "now": NOW.isoformat()}}, "admin"),
    ("U06 委託 1–3 次（金額 ≥ 10 萬）或 委託失敗 ≥ 1（or）", {"behaviors_op": "or", "behaviors": [{"event": "bond_order", "count": {"op": "between", "value": [1, 3]}, "filters": [{"field": "amount", "op": "gte", "value": 100000}], "time": T}, {"event": "order_rejected", "time": T}]}, "admin"),
    ("U07 漏斗 F01 第 2 步到達", {"source": {"type": "funnel", "query": FUNNELS[0][1], "step": 2}}, "admin"),
    ("U08 漏斗 F06 第 2 步流失，桶 10-12", {"source": {"type": "funnel", "query": FUNNELS[5][1], "step": 2, "reached": False, "bucket": "2026-10-12"}}, "admin"),
    ("U09 留存 R02 同期群 10-03 第 1 期留存", {"source": {"type": "retention", "query": RETENTIONS[1][1], "cohort": "2026-10-03", "period": 1, "retained": True}}, "admin"),
    ("U10 流失 R04 同期群 10-03 第 2 期起流失", {"source": {"type": "retention", "query": RETENTIONS[3][1], "cohort": "2026-10-03", "period": 2, "retained": True}}, "admin"),
    ("U16 路徑 P01 第 1 步是看盤", {"source": {"type": "path", "query": PATHS[0][1], "node": "hq_stockdetail_view", "level": 1}}, "admin"),
    ("U17 路徑 P02（之前）終點前一步是委託", {"source": {"type": "path", "query": PATHS[1][1], "node": "bond_order", "level": 2}}, "admin"),
    ("U18 歸因 A03 被歸到「瀏覽」且平台 iOS 的轉化者", {"source": {"type": "attribution", "query": ATTRIBUTIONS[2][1], "touch": "瀏覽", "group": "iOS"}}, "admin"),
    ("U15 週留存 R05 同期群 09-28 第 1 期留存 × Android", {"source": {"type": "retention", "query": RETENTIONS[4][1], "cohort": "2026-09-28", "period": 1, "retained": True, "group": "Android"}}, "admin"),
    ("U11 事件 Q08 桶 10-05 分組 TSE", {"source": {"type": "events", "query": QUERIES[7][1], "bucket": "2026-10-05", "group": ["TSE"]}}, "admin"),
    ("U12 分公司範圍 + 回溯 10 天：做過委託（行銷遮罩）", {"behaviors": [{"event": "bond_order", "time": T_ALL}]}, BRANCH),
    ("U13 日期屬性：近 3 天活躍 且 2025 年開戶（排序開戶日）", {"segment": "golden_all", "filters": [{"field": "user.last_active_at", "op": "rel", "value": {"days": 3}}, {"field": "user.signup_date", "op": "between", "value": ["2025-01-01", "2025-12-31"]}], "time": {"now": NOW.isoformat()}}, "marketing"),
    ("U19 近 16 天成交金額合計 ≥ 50 萬 且 近 7 天活躍天數 ≥ 2（measure／last_days）", {"segment": "golden_all", "behaviors": [{"event": "bond_deal", "measure": {"agg": "sum", "field": "amount"}, "cmp": {"op": "gte", "value": 500000}, "time": {"last_days": 16}}, {"event": "app_login_success", "measure": {"agg": "days"}, "cmp": {"op": "gte", "value": 2}, "time": {"last_days": 7}}], "time": {"now": NOW.isoformat()}}, "admin"),
    ("U20 （P1 或 流失分 ≥ 70）或 近 16 天委託 > 2 次且單筆最大 < 20 萬（combine or、巢狀或）", {"segment": "golden_all", "combine": "or", "filters": {"op": "or", "items": [{"field": "user.tier", "op": "eq", "value": "P1"}, {"op": "and", "items": [{"field": "user.churn_score", "op": "gte", "value": 70}, {"field": "user.has_margin", "op": "false"}]}]}, "behaviors": [{"event": "bond_order", "cmp": {"op": "gt", "value": 2}, "time": {"last_days": 16}}, {"event": "bond_order", "measure": {"agg": "max", "field": "amount"}, "cmp": {"op": "lt", "value": 200000}, "time": {"last_days": 16}}], "time": {"now": NOW.isoformat()}}, "data_analyst"),
    ("U21 近 16 天依序 看盤 → 委託 → 成交，1 天內完成（行為序列）", {"segment": "golden_all", "sequences": [{"steps": [{"event": "hq_stockdetail_view"}, {"event": "bond_order"}, {"event": "bond_deal"}], "window": {"value": 1, "unit": "day"}, "time": {"last_days": 16}}], "time": {"now": NOW.isoformat()}}, "admin"),
    ("U22 委託（金額 ≥ 10 萬）後 2 小時內沒有成交 且 P1／P2（序列未完成 ＋ 屬性）", {"segment": "golden_all", "filters": [{"field": "user.tier", "op": "in", "value": ["P1", "P2"]}], "behaviors": [{"event": "bond_order", "filters": [{"field": "amount", "op": "gte", "value": 100000}], "time": {"last_days": 16}}], "sequences": [{"steps": [{"event": "bond_order", "filters": [{"field": "amount", "op": "gte", "value": 100000}]}, {"event": "bond_deal"}], "window": {"value": 2, "unit": "hour"}, "done": False, "time": {"last_days": 16}}], "time": {"now": NOW.isoformat()}}, "data_analyst"),
    ("U14 事件 Q10 合計、平台 iOS（巢狀篩選）+ 屬性", {"filters": [{"field": "user.pref_market", "op": "eq", "value": "US"}], "source": {"type": "events", "query": {**QUERIES[9][1], "group_by": ["platform"], "time": {**T, "grain": "total"}}, "group": ["iOS"]}}, "admin"),
]
USERS_SORT = {"U03": {"field": "churn_score", "dir": "desc"}, "U13": {"field": "signup_date", "dir": "asc"}}
USERS_REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("UR1 事件欄位當用戶條件", {"filters": [{"field": "platform", "op": "eq", "value": "iOS"}]}, "用戶屬性"),
    ("UR2 L3 屬性", {"filters": [{"field": "user.customer_code", "op": "set"}]}, "L3"),
    ("UR3 未登記事件", {"behaviors": [{"event": "nope", "time": T}]}, "事件字典"),
    ("UR4 第 1 步流失", {"source": {"type": "funnel", "query": FUNNELS[0][1], "step": 1, "reached": False}}, "step ≥ 2"),
    ("UR5 沒條件", {}, "至少"),
    ("UR6 行為沒時間", {"behaviors": [{"event": "bond_order"}]}, "time"),
    ("UR7 留存期數超出", {"source": {"type": "retention", "query": RETENTIONS[1][1], "cohort": "2026-10-03", "period": 9}}, "period"),
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
    ev, us, seg, edges = ch_rows(ds)
    ch.query("CREATE TABLE IF NOT EXISTS dmp.segment_members (segment_id LowCardinality(String), version UInt32, one_id String, one_id_uint UInt64 DEFAULT cityHash64(one_id), computed_at DateTime) ENGINE = MergeTree ORDER BY (segment_id, version, one_id_uint)")
    ch.query("CREATE TABLE IF NOT EXISTS dmp.id_edges (id_type_a LowCardinality(String), id_a String, id_type_b LowCardinality(String), id_b String, source LowCardinality(String), evidence_event_id String, first_seen DateTime64(3, 'UTC'), last_seen DateTime64(3, 'UTC'), seen_count UInt32, confidence Float32, status LowCardinality(String), one_id String, version UInt64) ENGINE = ReplacingMergeTree(version) ORDER BY (id_type_a, id_a, id_type_b, id_b)")
    ch.insert_json("dmp.id_edges", edges)
    ch.query(f"ALTER TABLE dmp.events DELETE WHERE project = '{PROJECT}'", settings={"mutations_sync": 2})
    ev.sort(key=lambda r: str(r["time"]))                           # 依時間寫入：每個 part 的日期範圍連續，EXPLAIN ESTIMATE 的邊界顆粒才不會隨情境事件增加而膨脹
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
        gcols = (1 if qr.time.grain != "total" else 0) + len(qr.group_by) + (1 if qr.cohorts else 0)
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
    # 漏斗：oracle（windowFunnel 演算法複刻）vs SQL 逐列；嚴格模式只檢查可執行且層級單調
    from .funnel import compile_funnel, parse_funnel
    for name, raw, spec in FUNNELS:
        d = decision_of(spec); fq = parse_funnel({**raw, "project": PROJECT}, cat); c = compile_funnel(fq, cat, d)
        got = run_sql(ch, c.sql, c.settings); expect = oracle.run_funnel(fq, d)
        compare(name, c.columns, expect, got, (1 if fq.trend else 0) + (1 if fq.group_by else 0) + 1 + (1 if fq.cohorts else 0))
        report.append(f"  {name:<44} L0 rows={len(got):<4} step1={next((r['users'] for r in got if r['step'] == 1), None)}")
    fs = parse_funnel({**FUNNELS[0][1], "project": PROJECT, "order": "strict"}, cat); cs = compile_funnel(fs, cat, _subject("admin"))
    strict = run_sql(ch, cs.sql, cs.settings); loose = run_sql(ch, compile_funnel(parse_funnel({**FUNNELS[0][1], "project": PROJECT}, cat), cat, _subject("admin")).sql, cs.settings)
    assert [r["users"] for r in strict] == sorted([r["users"] for r in strict], reverse=True) and all(a["users"] <= b["users"] for a, b in zip(strict, loose)), (strict, loose)
    from .retention import compile_retention, parse_retention
    for name, raw, spec in RETENTIONS:
        d = decision_of(spec); rq = parse_retention({**raw, "project": PROJECT}, cat); c = compile_retention(rq, cat, d)
        got = run_sql(ch, c.sql, c.settings); expect = oracle.run_retention(rq, d)
        compare(name, c.columns, expect, got, (2 if rq.group_by else 1) + 1 + (1 if rq.cohorts else 0))
        report.append(f"  {name:<44} L0 rows={len(got):<4} cohorts={len({r['cohort'] for r in got})}")
    for name, raw, kw in RETENTION_REJECTED:
        try:
            parse_retention({**raw, "project": PROJECT}, cat)
        except ValueError as e:
            assert kw in str(e), (name, e)
        else:
            raise AssertionError(name)
    from .path import compile_path, parse_path, sankey
    for name, raw, spec in PATHS:
        d = decision_of(spec); pq = parse_path({**raw, "project": PROJECT}, cat); c = compile_path(pq, cat, d)
        got = run_sql(ch, c.sql, c.settings); expect = oracle.run_path(pq, d)
        compare(name, c.columns, expect, got, 1)
        assert [tuple(r["path"]) for r in got] == [tuple(r["path"]) for r in expect], (name, "Top N 順序")
        sk = sankey(got, pq); assert sk["anchor_users"] == (got[0]["anchor_users"] if got else 0) and all(n["users"] > 0 for n in sk["nodes"])
        report.append(f"  {name:<44} L0 paths={len(got):<4} anchor_users={sk['anchor_users']} nodes={len(sk['nodes'])} links={len(sk['links'])} suppressed={sum(1 for r in got if r.get('suppressed'))}")
    for name, raw, kw in PATH_REJECTED:
        try:
            parse_path({**raw, "project": PROJECT}, cat)
        except ValueError as e:
            assert kw in str(e), (name, e)
        else:
            raise AssertionError(name)
    from .attribution import compile_attribution, parse_attribution, shape
    for name, raw, spec in ATTRIBUTIONS:
        d = decision_of(spec); aq = parse_attribution({**raw, "project": PROJECT}, cat); c = compile_attribution(aq, cat, d)
        got = run_sql(ch, c.sql, c.settings); expect = oracle.run_attribution(aq, d)
        compare(name, c.columns, expect, got, 2 if aq.split else 1)
        sh = shape(got, aq); assert sh["summary"]["conversions"] == sh["summary"]["touched"] + sh["summary"]["untouched"]
        for m in aq.models:                                   # 每個模型分出去的轉化數 == 有觸點的轉化數（未抑制時）
            tot = sum((r["models"][m]["conversions"] or 0) for r in sh["rows"])
            if not any(r["suppressed"] for r in sh["rows"]): assert abs(tot - sh["summary"]["touched"]) < 1e-6, (name, m, tot, sh["summary"])
        report.append(f"  {name:<44} L0 rows={len(got):<4} conversions={sh['summary']['conversions']} touched={sh['summary']['touched']} avg_touches={sh['summary']['avg_touches'] and round(sh['summary']['avg_touches'], 2)}")
    for name, raw, kw in ATTRIBUTION_REJECTED:
        try:
            parse_attribution({**raw, "project": PROJECT}, cat)
        except ValueError as e:
            assert kw in str(e), (name, e)
        else:
            raise AssertionError(name)
    users_stage(ch, ds, oracle, cat, report)
    for name, raw, kw in FUNNEL_REJECTED:
        try:
            parse_funnel({**raw, "project": PROJECT}, cat)
        except ValueError as e:
            assert kw in str(e), (name, e)
        else:
            raise AssertionError(name)
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
        print(f"compiler golden OK: {len(QUERIES)} queries (layers {layers}) + {len(FUNNELS)} funnels (+strict) + {len(RETENTIONS)} retentions + {len(PATHS)} paths + {len(ATTRIBUTIONS)} attributions + {len(USERS)} user lists (+profile/timeline/identities), {len(REJECTED)}+{len(FUNNEL_REJECTED)}+{len(RETENTION_REJECTED)}+{len(PATH_REJECTED)}+{len(ATTRIBUTION_REJECTED)}+{len(USERS_REJECTED)} rejected, sample ok, MV==L0")
    return {"queries": len(QUERIES), "layers": layers}


def users_stage(ch: Any, ds: Dataset, oracle: Oracle, cat: Catalog, report: list[str]) -> None:
    """用戶列表：估數 == oracle 集合大小；每頁 7 筆走完 == 同一集合（one_id 依角色遮罩）、排序單調；細查：畫像／摘要／序列（分頁）／身分圖 == 資料集。"""
    from .policy import mask_value
    from .users import (
        compile_estimate,
        compile_identities,
        compile_list,
        compile_profile,
        compile_segments_of,
        compile_summary,
        compile_timeline,
        decode_cursor,
        identity_graph,
        next_cursor,
        parse_list,
        parse_users,
        timeline_cursor,
    )
    first: str | None = None
    for name, raw, spec in USERS:
        d = decision_of(spec); uq = parse_users({**raw, "project": PROJECT}, cat)
        expect = oracle.run_users(uq, d)
        ce = compile_estimate(uq, cat, d); got = run_sql(ch, ce.sql, ce.settings)[0]["users"]
        assert got == len(expect), (name, got, len(expect))
        sort = USERS_SORT.get(name[:3]); spec_l = parse_list({"page_size": 7, "sort": sort}, cat); seen: list[dict[str, Any]] = []
        while True:
            cl = compile_list(uq, spec_l, cat, d); rows = run_sql(ch, cl.sql, cl.settings); seen += rows
            nc = next_cursor(rows, spec_l, got)
            if not nc: break
            spec_l = parse_list({"page_size": 7, "sort": sort, "cursor": nc}, cat)
            assert decode_cursor(nc)["t"] == got
        pol = (d.field_policies.get("one_id", "plain") if d is not None else "plain")
        assert len(seen) == len(expect) and {r["one_id"] for r in seen} == {mask_value(pol, o) for o in expect}, (name, len(seen), len(expect))
        keys = [(r["_s"], r["_u"]) for r in seen]
        assert keys == sorted(keys, reverse=(sort or {}).get("dir") == "desc") and len(set(keys)) == len(keys), (name, "分頁順序")
        if d is not None and "churn_score" in cl.masked:
            assert all(isinstance(r["churn_score"], str) or r["churn_score"] is None for r in seen), name
        if first is None and expect: first = sorted(expect)[0]
        report.append(f"  {name:<44} L0 users={got:<4} pages={-(-got // 7)} masked={list(cl.masked) or '-'}")
    for name, raw, kw in USERS_REJECTED:
        try:
            parse_users({**raw, "project": PROJECT}, cat)
        except ValueError as e:
            assert kw in str(e), (name, e)
        else:
            raise AssertionError(name)
    # 細查：admin 明文、數據分析 one_id 遮罩；摘要 == 近 30 天事件數；序列分頁走完 == 該用戶全部事件；身分圖 == 資料集的邊
    assert first is not None
    oid = next(o for o in sorted(ds.users) if any(e["one_id"] == o for e in ds.edges))
    adm = authorize(Subject("u-adm", ["admin"]), "use", "profile", {"reason": "黃金測試"}); ana = authorize(Subject("u-ana", ["data_analyst"]), "use", "profile", {"reason": "黃金測試"})
    p = run_sql(ch, (cp := compile_profile(oid, cat, adm)).sql, cp.settings)          # admin：one_id 明文；L2 金額類（aum／churn_score）admin 不在 plain_roles → 區間
    assert len(p) == 1 and p[0]["one_id"] == oid and p[0]["tier"] == ds.users[oid]["tier"] and p[0]["churn_score"] == mask_value("bucket([0,40,70,100])", ds.users[oid]["churn_score"]) and cp.masked.get("aum") == "bucket([0,1e6,5e6])", p
    p2 = run_sql(ch, (cp2 := compile_profile(oid, cat, ana)).sql, cp2.settings)      # 數據分析：one_id 遮罩；aum／churn_score 明文
    assert p2[0]["one_id"] == mask_value("mask_tail(4)", oid) and cp2.masked == {"one_id": "mask_tail(4)"} and p2[0]["aum"] == ds.users[oid]["aum"] and p2[0]["churn_score"] == ds.users[oid]["churn_score"], p2
    assert run_sql(ch, compile_profile("nope", cat, adm).sql, cp.settings) == []
    sm = run_sql(ch, (cs := compile_summary(oid, PROJECT, cat, adm, now=NOW)).sql, cs.settings)
    exp_n = sum(1 for e in ds.events if e["one_id"] == oid and NOW - timedelta(days=29) <= e["event_date"] <= NOW)
    assert sum(r["n"] for r in sm) == exp_n and sm == sorted(sm, key=lambda r: (-r["n"], r["event"])), (sm, exp_n)
    segs = run_sql(ch, (cg := compile_segments_of(oid)).sql, cg.settings); assert [r["segment_id"] for r in segs] == (["golden_all", "vip"] if oid in ds.segments["vip"] else ["golden_all"]), segs
    all_rows: list[dict[str, Any]] = []; cur = None
    while True:
        ct = compile_timeline(oid, PROJECT, DAY0, NOW, cat, ana, limit=50, cursor=cur, now=NOW); rows = run_sql(ch, ct.sql, ct.settings); all_rows += rows
        nc = timeline_cursor(rows, 50, cur)
        if not nc: break
        cur = decode_cursor(nc)
    mine = [e for e in ds.events if e["one_id"] == oid]
    assert len(all_rows) == len(mine) and {r["event_id"] for r in all_rows} == {e["event_id"] for e in mine}, (len(all_rows), len(mine))
    assert [(r["time"], r["event_id"]) for r in all_rows] == sorted([(r["time"], r["event_id"]) for r in all_rows], reverse=True) and "amount" not in ct.masked and ct.columns[-1] == "props"
    ctm = compile_timeline(oid, PROJECT, DAY0, NOW, cat, _subject("marketing"), events=["bond_deal"], limit=50, now=NOW); mrows = run_sql(ch, ctm.sql, ctm.settings)
    assert ctm.masked == {"amount": "bucket([0,1e5,5e5,1e6,5e6])"} and all(r["event"] == "bond_deal" and (r["amount"] is None or isinstance(r["amount"], str)) for r in mrows), mrows[:2]
    ci = compile_identities(oid); er = run_sql(ch, ci.sql, ci.settings); g = identity_graph(er, ana)
    mine_e = [e for e in ds.edges if e["one_id"] == oid]
    assert len(g["edges"]) == len(mine_e) and len(g["nodes"]) == len(mine_e) + 1 and all(n["value"].endswith("****") for n in g["nodes"]) and g["masked"] == {"id": "mask_tail(4)"}, g
    assert all(not n["value"].endswith("****") for n in identity_graph(er, adm)["nodes"])
    report.append(f"  {'細查 profile/summary/timeline/identities':<44} L0 events={len(all_rows)} edges={len(mine_e)} summary30d={exp_n}")


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
    # 選層：人數預設精確走 L0；快速估算才走 MV 並只警示一次（含 compare）；行級條件用到分公司／營業員時一律 L0（MV 沒有這兩個維度）
    uq = {"metrics": [{"event": "bond_order", "agg": "users"}], "time": T}
    assert compile_events(parse_g(uq, cat), cat, _subject("admin")).layer == "L0"
    ce = compile_events(parse_g({**uq, "estimate": True, "compare": "previous_period"}, cat), cat, _subject("admin"))
    assert ce.layer == "L1" and "uniqMerge(a.users)" in ce.sql and sum("快速估算" in w for w in ce.warnings) == 1, ce.warnings
    for raw in ({**uq, "estimate": True}, {"metrics": [{"event": "bond_order", "agg": "total"}], "time": T}):
        cb = compile_events(parse_g(raw, cat), cat, BRANCH)
        assert cb.layer == "L0" and "events_daily_agg" not in cb.sql and "row filter on user attribute" in " ".join(cb.warnings), cb.warnings
    assert compile_events(parse_g({**uq, "estimate": "false"}, cat), cat, _subject("admin")).layer == "L0"
    try:
        parse_g({**uq, "exact": "maybe"}, cat)
    except ValueError as e:
        assert "true／false" in str(e)
    else:
        raise AssertionError("exact 非布林應被拒絕")
    c1 = compile_events(parse_g(QUERIES[0][1], cat), cat, _subject("admin")); c4 = compile_events(parse_g(QUERIES[3][1], cat), cat, _subject("data_analyst"))
    assert c1.layer == "L1" and c4.layer == "L1" and "events_daily_agg" in c1.sql, (c1.layer, c4.layer)
    c19 = compile_events(parse_g(QUERIES[18][1], cat), cat, _subject("marketing"))
    assert c19.masked == {"m1": "bucket([0,1e5,5e5,1e6,5e6])", "m2": "bucket([0,1e5,5e5,1e6,5e6])"} and "multiIf" in c19.sql, c19.masked
    from .funnel import compile_funnel, parse_funnel, with_rates
    for name, raw, spec in FUNNELS:
        fq = parse_funnel({**raw, "project": PROJECT}, cat); c = compile_funnel(fq, cat, decision_of(spec))
        rows = oracle.run_funnel(fq, decision_of(spec)); assert rows and "windowFunnel" in c.sql and c.columns[-1] in ("suppressed", "_k"), name
        rated = with_rates(rows, fq.steps); assert rated[0]["rate_first"] in (1.0, None) and rated[0]["alias"], name
        h.update(c.sql.encode())
    for name, raw, kw in FUNNEL_REJECTED:
        try: parse_funnel({**raw, "project": PROJECT}, cat); raise AssertionError(name)
        except ValueError as e: assert kw in str(e), (name, e)
    from .retention import compile_retention, parse_retention, with_complete
    for name, raw, spec in RETENTIONS:
        rq = parse_retention({**raw, "project": PROJECT}, cat); c = compile_retention(rq, cat, decision_of(spec))
        rows = oracle.run_retention(rq, decision_of(spec)); assert rows and "ARRAY JOIN range(" in c.sql and c.columns[:1] in (["cohort"], ["cohort_name"]), name
        assert all(0 <= (r["rate"] or 0) <= 1 for r in rows), name
        wc = with_complete(rows, rq, NOW); assert any(x["complete"] for x in wc), name
        h.update(c.sql.encode())
    for name, raw, kw in RETENTION_REJECTED:
        try: parse_retention({**raw, "project": PROJECT}, cat); raise AssertionError(name)
        except ValueError as e: assert kw in str(e), (name, e)
    from .path import compile_path, parse_path, sankey
    for name, raw, spec in PATHS:
        pq = parse_path({**raw, "project": PROJECT}, cat); d = decision_of(spec); c = compile_path(pq, cat, d)
        rows = oracle.run_path(pq, d); assert rows and "ARRAY JOIN arrayEnumerate(seq3)" in c.sql and c.columns[0] == "path" and c.sql.count("(") == c.sql.count(")"), name
        assert all(r["path"][0 if pq.direction == "after" else -1] == pq.anchor for r in rows) and all(len(r["path"]) <= pq.steps + 1 for r in rows), name
        sk = sankey(rows, pq); assert sk["paths"][0]["share"] is None or 0 < sk["paths"][0]["share"] <= 1, name
        h.update(c.sql.encode())
    for name, raw, kw in PATH_REJECTED:
        try: parse_path({**raw, "project": PROJECT}, cat); raise AssertionError(name)
        except ValueError as e: assert kw in str(e), (name, e)
    from .attribution import SCAN_WINDOW_DAYS, compile_attribution, parse_attribution, shape
    for name, raw, spec in ATTRIBUTIONS:
        aq = parse_attribution({**raw, "project": PROJECT}, cat); d = decision_of(spec); c = compile_attribution(aq, cat, d)
        rows = oracle.run_attribution(aq, d); assert rows and "ARRAY JOIN if(n = 0, " in c.sql and "GROUPING SETS" in c.sql and "(SELECT count() FROM seq)" not in c.sql and (("ev AS (" in c.sql) == (aq.window_days >= SCAN_WINDOW_DAYS) != ("tpu AS (" in c.sql)) and c.columns[0] == "ti" and c.sql.count("(") == c.sql.count(")"), name
        sh = shape(rows, aq); assert sh["summary"]["conversions"] > 0 and all(set(r["models"]) == set(aq.models) for r in sh["rows"]), name
        h.update(c.sql.encode())
    for name, raw, kw in ATTRIBUTION_REJECTED:
        try: parse_attribution({**raw, "project": PROJECT}, cat); raise AssertionError(name)
        except ValueError as e: assert kw in str(e), (name, e)
    from .users import compile_estimate, compile_list, parse_list, parse_users
    for name, raw, spec in USERS:
        uq = parse_users({**raw, "project": PROJECT}, cat); d = decision_of(spec)
        c = compile_estimate(uq, cat, d); n = len(oracle.run_users(uq, d))
        assert "bitmapCardinality(bm)" in c.sql and c.sql.count("(") == c.sql.count(")") and "dmp.users" in c.lineage, name
        assert n > 0, name
        cl = compile_list(uq, parse_list({"sort": USERS_SORT.get(name[:3]), "page_size": 50}, cat), cat, d)
        assert cl.columns[:2] == ["one_id", "branch_id"] and cl.columns[-2:] == ["_s", "_u"] and "bitmapContains(bm, e.one_id_uint)" in cl.sql, name
        h.update(c.sql.encode())
    for name, raw, kw in USERS_REJECTED:
        try: parse_users({**raw, "project": PROJECT}, cat); raise AssertionError(name)
        except ValueError as e: assert kw in str(e), (name, e)
    print(f"compiler dry run OK: {len(QUERIES)} queries + {len(FUNNELS)} funnels + {len(RETENTIONS)} retentions + {len(PATHS)} paths + {len(ATTRIBUTIONS)} attributions + {len(USERS)} user lists compiled + oracle, {len(REJECTED)}+{len(FUNNEL_REJECTED)}+{len(RETENTION_REJECTED)}+{len(PATH_REJECTED)}+{len(ATTRIBUTION_REJECTED)}+{len(USERS_REJECTED)} rejected, sql digest {h.hexdigest()[:12]}")


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
