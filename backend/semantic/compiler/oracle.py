"""黃金測試的參考實作：用純 Python 對同一份事件列算出查詢結果，和 ClickHouse 跑出的 SQL 結果逐列比對（誤差 0）。

語意要和 ClickHouse 一致：
  - 字串欄位未設值是 ''（LowCardinality(String)）；JSON／用戶冷屬性缺值是 None；dictGet 缺鍵回型別預設（'' / 0）
  - sumIf 無列 → 0；avg／max／min／quantile 無列 → None；avg_per_user／avg_val 分母 0 → None
  - quantileExact(level) = 排序後第 floor(level × n) 個元素（ClickHouse nth_element 定義）
  - K 匿名：該列（分組）去重用戶數 < K → 指標 None、suppressed=True
"""
from __future__ import annotations

import operator
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .attribution import AttributionQuery
from .catalog import FieldRef
from .funnel import FunnelQuery
from .parser import Cond, Formula, Group, Metric, Query
from .path import EXIT, OTHER, PathQuery
from .policy import bucket_label, parse_policy
from .retention import RetentionQuery
from .users import UsersQuery


class Oracle:
    def __init__(self, events: list[dict[str, Any]], users: dict[str, dict[str, Any]], segments: dict[str, set[str]] | None = None, hot_defaults: dict[str, Any] | None = None):
        self.events, self.users, self.segments = events, users, segments or {}
        self.hot_defaults = hot_defaults or {"string": "", "number": 0.0, "bool": 0, "date": date(1970, 1, 1)}
        self.imports: dict[str, set[str]] = {}                         # 導入名單 id → one_id（golden／e2e 用）

    # ---- 欄位取值 ----
    def value(self, f: FieldRef, r: dict[str, Any]) -> Any:
        if f.kind == "column":
            v = r.get(f.name)
            if f.type == "string": return "" if v is None else v
            return v
        if f.kind == "expr":
            return r["time"].hour if f.name == "event_hour" else r["event_date"]
        if f.kind == "json":
            v = r.get("props", {}).get(self._dim_path(f))
            if v is None: return None
            if f.type == "number": return float(v)
            if f.type == "string": return str(v)
            return v
        if f.kind in ("user_hot", "user_cold"):
            u = self.users.get(r["one_id"])
            if u is None or f.name not in u or u[f.name] is None:
                return self.hot_defaults[f.type] if f.kind == "user_hot" else None
            v = u[f.name]
            return (v + timedelta(hours=8)).date() if isinstance(v, datetime) else v     # 時間型屬性：台北日期（catalog 用 toDate(…, 'Asia/Taipei')）
        raise ValueError(f.kind)

    @staticmethod
    def _dim_path(f: FieldRef) -> str:
        # dimensions.yaml 的 props.<path>：FieldRef.name 已去掉開頭的 $（遮罩表用），props 原 key 可能含 $
        return f.sql.split("`")[1]

    def cond(self, c: Cond, r: dict[str, Any], now: date) -> bool:
        f, op, v = c.field, c.op, c.value
        if f.kind == "segment":
            inside = r["one_id"] in self.segments.get(f.name, set())
            return inside if op == "in" else not inside
        x = self.value(f, r)
        if f.type == "string":
            if op == "set": return x is not None and x != ""
            if op == "nset": return x is None or x == ""
            if x is None: return False
            if op == "eq": return x == v
            if op == "neq": return x != v
            if op == "in": return x in v
            if op == "nin": return x not in v
            if op == "like": return str(v) in x
            if op == "nlike": return str(v) not in x
        if f.type == "number":
            if op == "set": return x is not None
            if op == "nset": return x is None
            if x is None: return False
            if op == "between": return v[0] <= x <= v[1]
            return {"eq": x == v, "neq": x != v, "gt": x > v, "gte": x >= v, "lt": x < v, "lte": x <= v}[op]
        if f.type == "bool":
            return bool(x) == (op == "true") if x is not None else False
        if f.type == "date":
            if op == "set": return x is not None
            if op == "nset": return x is None
            if x is None: return False
            if op == "rel": return x >= now - timedelta(days=int(v["days"]))
            if op == "between": return date.fromisoformat(v[0]) <= x <= date.fromisoformat(v[1])
        raise ValueError(op)

    def group(self, g: Group | None, r: dict[str, Any], now: date) -> bool:
        if g is None or not g.items: return True
        res = [self.group(it, r, now) if isinstance(it, Group) else self.cond(it, r, now) for it in g.items]
        return all(res) if g.op == "and" else any(res)

    # ---- 聚合 ----
    @staticmethod
    def bucket(grain: str, r: dict[str, Any]) -> Any:
        t: datetime = r["time"]; d: date = r["event_date"]
        if grain == "minute": return t.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        if grain == "hour": return t.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        if grain == "day": return d.isoformat()
        if grain == "week": return (d - timedelta(days=d.weekday())).isoformat()
        if grain == "month": return d.replace(day=1).isoformat()
        return None

    @staticmethod
    def quantile_exact(vals: list[float], level: float) -> float | None:
        if not vals: return None
        s = sorted(vals); n = int(level * len(s)) if level < 1 else len(s) - 1
        return float(s[min(n, len(s) - 1)])

    def metric(self, m: Metric, rows: list[dict[str, Any]], now: date) -> Any:
        sel = [r for r in rows if (m.event is None or r["event"] == m.event) and self.group(m.filters, r, now)]
        users = len({r["one_id"] for r in sel})
        if m.agg == "total": return len(sel)
        if m.agg in ("users", "mau"): return users
        if m.agg == "avg_per_user": return None if users == 0 else len(sel) / users
        vals = [self.value(m.field, r) for r in sel]        # type: ignore[arg-type]
        vals = [v for v in vals if v is not None]
        if m.agg == "uniq": return len(set(vals))
        if m.agg == "sum": return float(sum(vals))
        if m.agg == "avg": return None if not vals else sum(vals) / len(vals)
        if m.agg == "avg_val": return None if users == 0 else sum(vals) / users
        if m.agg == "max": return None if not vals else max(vals)
        if m.agg == "min": return None if not vals else min(vals)
        if m.agg in ("p25", "p50", "p75", "p90"): return self.quantile_exact(vals, int(m.agg[1:]) / 100)
        raise ValueError(m.agg)

    @staticmethod
    def formula(f: Formula, env: dict[str, Any]) -> Any:
        toks = list(f.tokens); pos = 0

        def peek() -> str | None:
            return toks[pos] if pos < len(toks) else None

        def take() -> str:
            nonlocal pos; pos += 1; return toks[pos - 1]

        def atom() -> Any:
            t = take()
            if t == "(":
                x = expr(); take(); return x
            if t == "-":
                x = atom(); return None if x is None else -x
            return float(t) if t[0].isdigit() else env.get(t)

        def term() -> Any:
            x = atom()
            while peek() in ("*", "/"):
                op = take(); y = atom()
                if x is None or y is None: x = None
                elif op == "*": x = x * y
                else: x = None if y == 0 else x / y
            return x

        def expr() -> Any:
            x = term()
            while peek() in ("+", "-"):
                op = take(); y = term()
                x = None if x is None or y is None else (x + y if op == "+" else x - y)
            return x
        return expr()

    # ---- 整條查詢 ----
    def behavior_users(self, project: str, b: Any, decision: Any, now: date) -> set[str]:
        """一條行為「做過且指標符合」的用戶集合（未做過的條件由呼叫端取反）；與 SQL 的 behavior_select_sql 同口徑。"""
        rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self.events:
            if r["project"] == project and b.time.start <= r["event_date"] <= b.time.end and r["event"] == b.event and self.group(b.filters, r, now) and self._row_filters_ok(decision, r, b.time.now or now):
                rows[r["one_id"]].append(r)
        if not b.done:
            return set(rows)
        ms = {o: m for o, m in ((o, self._measure(b, rs)) for o, rs in rows.items()) if m is not None}
        if b.count_op == "between":
            return {o for o, n in ms.items() if b.count[0] <= n <= b.count[1]}      # type: ignore[index]
        cmp = {"gte": operator.ge, "lte": operator.le, "eq": operator.eq, "gt": operator.gt, "lt": operator.lt}[b.count_op]
        return {o for o, n in ms.items() if cmp(n, b.count)}

    def _behaviors_ok(self, q: Any, decision: Any, now: date) -> set[str] | None:
        """行為篩選通過的用戶（None = 沒有行為篩選）。"""
        if not getattr(q, "behaviors", None): return None
        from .scope import behavior_ok
        ids = {r["one_id"] for r in self.events if r["project"] == q.project}
        return {o for o in ids if behavior_ok(self, q.behaviors, q.project, o, decision, now)}

    def _with_cohorts(self, q: Any, runner: Any, decision: Any, col: str) -> list[dict[str, Any]]:
        from .scope import derive
        out: list[dict[str, Any]] = []
        for c in q.cohorts:
            out += [{col: c.name, **r} for r in runner(derive(q, c), decision)]
        return out

    def run(self, q: Query, decision: Any = None) -> list[dict[str, Any]]:
        if q.cohorts:
            return self._with_cohorts(q, self.run, decision, "cohort")
        now = q.time.now or date.today()
        k = int(getattr(decision, "k_anonymity", 0) or 0)
        fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
        allowed = self._behaviors_ok(q, decision, now)
        rows = [r for r in self.events if r["project"] == q.project and q.time.start <= r["event_date"] <= q.time.end
                and (not q.events or r["event"] in q.events) and self.group(q.filters, r, now) and self._row_filters_ok(decision, r, now)
                and (allowed is None or r["one_id"] in allowed)]
        if q.sample:
            raise ValueError("oracle 不做抽樣（SAMPLE 由 ClickHouse 的 cityHash64 分桶決定）")
        gcols = [f"g{i + 1}" for i in range(len(q.group_by))]
        groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            key = (self.bucket(q.time.grain, r),) + tuple(self._masked_group(fp, g, i, self.value(g, r)) for i, g in enumerate(q.group_by))
            groups[key].append(r)
        # top_n：按第一個指標在整段期間的值排序
        keep: set[tuple] | None = None
        if q.top_n:
            first = q.metrics[0]; rank: dict[tuple, Any] = defaultdict(float)
            by_g: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
            for key, rs in groups.items():
                by_g[key[1:]].extend(rs)
            for gk, rs in by_g.items():
                agg = first.agg if first.agg in ("users", "sum") else "total"
                rank[gk] = self.metric(Metric(first.alias, agg, first.event, first.field, first.filters), rs, now)
            ordered = sorted(rank.items(), key=lambda kv: (-(kv[1] or 0), tuple("" if x is None else str(x) for x in kv[0])))
            keep = {gk for gk, _ in ordered[: q.top_n]}
        mau: dict[tuple, int] = {}
        if any(m.agg == "mau" for m in q.metrics):
            for m in q.metrics:
                if m.agg != "mau": continue
                by_m: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
                for key, rs in groups.items():
                    by_m[(m.alias, key[0][:7]) + key[1:]].extend(rs)
                for mk, rs in by_m.items():
                    mau[mk] = self.metric(m, rs, now)
        out: list[dict[str, Any]] = []
        for key, rs in groups.items():
            if keep is not None and key[1:] not in keep: continue
            row: dict[str, Any] = {}
            if key[0] is not None: row["bucket"] = key[0]
            for c, v in zip(gcols, key[1:]): row[c] = v
            kk = len({r["one_id"] for r in rs}); suppressed = bool(k and kk < k)
            env: dict[str, Any] = {}
            for m in q.metrics:
                v = mau[(m.alias, key[0][:7]) + key[1:]] if m.agg == "mau" else self.metric(m, rs, now)
                if m.field and m.agg in ("sum", "avg", "avg_val", "max", "min", "p25", "p50", "p75", "p90") and fp.get(m.field.name, "plain") != "plain":
                    name, arg = parse_policy(fp[m.field.name])
                    if name == "bucket": v = bucket_label(v, arg)                           # type: ignore[arg-type]
                    elif name == "round": v = None if v is None else round(v / arg) * arg   # type: ignore[operator]
                v = None if suppressed else v
                row[m.alias] = v; env[m.alias] = v
            for f in q.formulas:
                row[f.alias] = self.formula(f, env); env[f.alias] = row[f.alias]
            row["_k"] = kk
            if k: row["suppressed"] = suppressed
            out.append(row)
        if q.compare == "previous_period":
            days = (q.time.end - q.time.start).days + 1
            prev_q = Query(project=q.project, metrics=q.metrics, time=type(q.time)(q.time.start - timedelta(days=days), q.time.end - timedelta(days=days), q.time.grain, q.time.now),
                           formulas=q.formulas, filters=q.filters, group_by=q.group_by, top_n=None, compare=None, sample=None, exact=q.exact, behaviors=q.behaviors)
            prev = {}
            for pr in self.run(prev_q, decision):
                b = pr.get("bucket")
                if b is not None:
                    b = self._shift(b, q.time.grain, days, q)
                key = (b,) + tuple(pr.get(c) for c in gcols)
                if any(x is None for x in key[1:]): continue      # NULL 分組值在 JOIN 中不相等
                prev[key] = pr
            vals = [m.alias for m in q.metrics] + [f.alias for f in q.formulas]
            for row in out:
                key = (row.get("bucket"),) + tuple(row.get(c) for c in gcols)
                p = prev.get(key) if not any(x is None for x in key[1:]) else None
                for v in vals:
                    row[v + "_prev"] = p[v] if p else None
        return out

    @staticmethod
    def _shift(b: str, grain: str, days: int, q: Query) -> str:
        if grain == "month":
            from .events import _months
            n = _months(q.time.start, q.time.end); d = date.fromisoformat(b)
            y, m = divmod(d.month - 1 + n, 12)
            return date(d.year + y, m + 1, 1).isoformat()
        if grain in ("minute", "hour"):
            return (datetime.strptime(b, "%Y-%m-%d %H:%M:%S") + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        return (date.fromisoformat(b) + timedelta(days=days)).isoformat()

    @staticmethod
    def _masked_group(fp: dict[str, str], g: FieldRef, i: int, v: Any) -> Any:
        pol = fp.get(g.name, "plain")
        if pol == "plain" or v is None: return v
        name, arg = parse_policy(pol)
        if name == "bucket": return bucket_label(float(v), arg)                      # type: ignore[arg-type]
        if name == "mask_tail":
            s = str(v); n = int(arg)                                                 # type: ignore[arg-type]
            return s[: max(len(s) - n, 0)] + "*" * min(len(s), n)
        if name == "mask_all": return "***"
        return v

    def _row_filters_ok(self, decision: Any, r: dict[str, Any], now: date) -> bool:
        """只模擬 policy.authorize 會產生的兩種行級條件：branch_id IN (...) 與 event_date >= today()-n。"""
        import re
        for f in (getattr(decision, "row_filters", []) or []) if decision is not None else []:
            m = re.search(r"branch_id IN \(([^)]*)\)", f)
            if m:
                allowed = {x.strip().strip("'") for x in m.group(1).split(",")}
                if (self.users.get(r["one_id"]) or {}).get("branch_id", "") not in allowed: return False
            m = re.search(r"event_date >= today\(\)-(\d+)", f)
            if m and r["event_date"] < now - timedelta(days=int(m.group(1))): return False
            if f.strip() == "1=0": return False
        return True


    # ---- 漏斗（windowFunnel 預設模式；時間取秒）----
    @staticmethod
    def window_funnel(events: list[tuple[int, int]], window_s: int, n: int) -> int:
        """ClickHouse AggregateFunctionWindowFunnel 的預設演算法：事件依 (時間, 條件序號) 排序；條件 0 每次出現都重設鏈的起點；
        條件 i 在「條件 i-1 的鏈起點 + 窗口」內則延伸鏈；回傳最深層級。"""
        ts: list[int | None] = [None] * n
        for t, idx in sorted(events):
            if idx == 0:
                ts[0] = t
            elif ts[idx - 1] is not None and t <= ts[idx - 1] + window_s:
                ts[idx] = ts[idx - 1]
                if idx + 1 == n:
                    return n
        for i in range(n - 1, -1, -1):
            if ts[i] is not None:
                return i + 1
        return 0

    def _funnel_groups(self, fq: FunnelQuery, decision: Any = None) -> dict[tuple, list[tuple[tuple, int]]]:
        """(趨勢桶, 分組值) → [(鏈鍵 (one_id, rel), 層級)]：漏斗結果與用戶列表下鑽共用。"""
        now = fq.time.now or date.today()
        step_events = {s.event for s in fq.steps}
        allowed = self._behaviors_ok(fq, decision, now)
        from .funnel import scan_end
        last = scan_end(fq)
        rows = [r for r in self.events if r["project"] == fq.project and fq.time.start <= r["event_date"] <= last and r["event"] in step_events
                and self.group(fq.filters, r, now) and self._row_filters_ok(decision, r, now) and (allowed is None or r["one_id"] in allowed)]
        if fq.sample:
            raise ValueError("oracle 不做抽樣")
        chains: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
        first_ev: dict[tuple, tuple[Any, dict[str, Any]]] = {}        # 鏈鍵 → (最早第一步時間, 列)：趨勢桶用（minIf）
        first_g: dict[tuple, tuple[Any, Any]] = {}                     # 鏈鍵 → (最早「分組值非 NULL」的第一步時間, 值)：argMinIf 會跳過 NULL
        for r in rows:
            key = (r["one_id"], self.value(fq.relation, r) if fq.relation else None)
            sec = int((r["time"] - timedelta(hours=8)).replace(tzinfo=UTC).timestamp())
            for i, s in enumerate(fq.steps):
                if i == 0 and r["event_date"] > fq.time.end:      # 第一步只看期間內（延伸區只給後續步驟）
                    continue
                if r["event"] == s.event and self.group(s.filters, r, now):
                    chains[key].append((sec, i))
                    if i == 0 and (key not in first_ev or r["time"] < first_ev[key][0]):
                        first_ev[key] = (r["time"], r)
                    if i == 0 and fq.group_by:
                        gv = self.value(fq.group_by, r)
                        if gv is not None and (key not in first_g or r["time"] < first_g[key][0]):
                            first_g[key] = (r["time"], gv)
        levels = {key: self.window_funnel(evs, fq.window_s, len(fq.steps)) for key, evs in chains.items()}
        # 分組值與趨勢桶取自「該用戶最早的第一步事件」（SQL：argMinIf／minIf 以 one_id[, rel] 分組）
        groups: dict[tuple, list[tuple]] = defaultdict(list)
        for key, lvl in levels.items():
            if key not in first_ev:
                groups[(None, None)].append((key, lvl)) if not (fq.trend or fq.group_by) else None
                continue
            t, r = first_ev[key]
            b = None
            if fq.trend:
                d = r["event_date"]
                b = {"day": d.isoformat(), "week": (d - timedelta(days=d.weekday())).isoformat(), "month": d.replace(day=1).isoformat()}[fq.trend]
            g = first_g.get(key, (None, None))[1] if fq.group_by else None
            groups[(b, g)].append((key, lvl))
        return groups

    def run_funnel(self, fq: FunnelQuery, decision: Any = None) -> list[dict[str, Any]]:
        if fq.cohorts:
            return self._with_cohorts(fq, self.run_funnel, decision, "cohort")
        k = int(getattr(decision, "k_anonymity", 0) or 0)
        groups = self._funnel_groups(fq, decision)
        out = []
        for (b, g), members in sorted(groups.items(), key=lambda kv: tuple("" if x is None else str(x) for x in kv[0])):
            for step in range(1, len(fq.steps) + 1):
                reached = [key for key, lvl in members if lvl >= step]
                users = len({key[0] for key in reached}) if fq.metric == "users" else len(reached)
                kk = len({key[0] for key in reached})
                row: dict[str, Any] = {}
                if fq.trend: row["bucket"] = b
                if fq.group_by: row["g1"] = g
                row["step"] = step; row["users"] = None if (k and kk < k) else users; row["_k"] = kk
                if k: row["suppressed"] = bool(kk < k)
                out.append(row)
        return out


    # ---- 留存 ----
    @staticmethod
    def _bucket_of(unit: str, d: date) -> date:
        if unit == "day": return d
        if unit == "week": return d - timedelta(days=d.weekday())
        return d.replace(day=1)

    @staticmethod
    def _diff(unit: str, c: date, r: date) -> int:
        if unit == "day": return (r - c).days
        if unit == "week": return (r - c).days // 7
        return (r.year - c.year) * 12 + r.month - c.month

    @staticmethod
    def _retained(model: str, ps: set[int], n: int, lo: int, P: int) -> bool:
        if model == "date": return n in ps
        if model == "interval": return any(lo <= p <= n for p in ps)
        if model == "continuous": return all(x in ps for x in range(lo, n + 1))
        return not any(n <= p <= P for p in ps)

    def _retention_members(self, rq: RetentionQuery, decision: Any = None) -> dict[tuple, list[tuple[str, set[int]]]]:
        """(同期群, 分組值) → [(one_id, 回訪期集合)]：留存結果與用戶列表下鑽共用。"""
        now = rq.time.now or date.today()
        if rq.sample:
            raise ValueError("oracle 不做抽樣")
        allowed = self._behaviors_ok(rq, decision, now)
        def ok(r: dict[str, Any]) -> bool:
            return r["project"] == rq.project and self.group(rq.filters, r, now) and self._row_filters_ok(decision, r, now) and (allowed is None or r["one_id"] in allowed)
        ret_end = rq.return_end()
        cohorts: dict[tuple[str, date], list[tuple[Any, Any]]] = defaultdict(list)       # (one_id, cohort) → [(time, group value)]
        returns: dict[str, set[date]] = defaultdict(set)
        for r in self.events:
            if not ok(r): continue
            if rq.time.start <= r["event_date"] <= rq.time.end and (rq.initial.event is None or r["event"] == rq.initial.event) and self.group(rq.initial.filters, r, now):
                cohorts[(r["one_id"], self._bucket_of(rq.unit, r["event_date"]))].append((r["time"], self.value(rq.group_by, r) if rq.group_by else None))
            if rq.time.start <= r["event_date"] <= ret_end and (rq.ret.event is None or r["event"] == rq.ret.event) and self.group(rq.ret.filters, r, now):
                returns[r["one_id"]].add(self._bucket_of(rq.unit, r["event_date"]))
        members: dict[tuple, list[tuple[str, set[int]]]] = defaultdict(list)             # (cohort, g1) → [(one_id, periods)]
        for (oid, c), evs in cohorts.items():
            g = None
            if rq.group_by:
                nn = [(t, v) for t, v in evs if v is not None]                              # argMinIf 跳過 NULL
                g = min(nn)[1] if nn else None
            periods = {self._diff(rq.unit, c, rb) for rb in returns.get(oid, set()) if rb >= c and self._diff(rq.unit, c, rb) <= rq.periods}
            members[(c, g)].append((oid, periods))
        return members

    def run_retention(self, rq: RetentionQuery, decision: Any = None) -> list[dict[str, Any]]:
        if rq.cohorts:
            return self._with_cohorts(rq, self.run_retention, decision, "cohort_name")
        k = int(getattr(decision, "k_anonymity", 0) or 0)
        members = self._retention_members(rq, decision)
        P, lo = rq.periods, rq.lo
        out = []
        for (c, g), ms in sorted(members.items(), key=lambda kv: (kv[0][0].isoformat(), "" if kv[0][1] is None else str(kv[0][1]))):
            users = len({oid for oid, _ in ms})
            for n in range(lo, P + 1):
                retained = len({oid for oid, ps in ms if self._retained(rq.model, ps, n, lo, P)})
                row: dict[str, Any] = {"cohort": c.isoformat()}
                if rq.group_by: row["g1"] = g
                sup = bool(k and users < k)
                row.update({"period": n, "users": users, "retained": None if sup else retained, "rate": None if sup else retained / users})
                if k: row["suppressed"] = sup
                out.append(row)
        return out


    # ---- 用戶列表 ----
    def _user_ok(self, decision: Any, u: dict[str, Any]) -> bool:
        import re
        for f in (getattr(decision, "row_filters", []) or []) if decision is not None else []:
            m = re.search(r"branch_id IN \(([^)]*)\)", f)
            if m and u.get("branch_id", "") not in {x.strip().strip("'") for x in m.group(1).split(",")}: return False
            if f.strip() == "1=0": return False
        return True

    def run_users(self, uq: UsersQuery, decision: Any = None) -> set[str]:
        """同口徑：母體 = 用戶寬表（屬性 NULL 照 SQL 語意：不墊預設值）；做過 = 期間內次數符合；未做過 = 0 次；下鑽 = 漏斗／留存／事件落點。"""
        now = uq.now or date.today()
        saved = self.hot_defaults
        self.hot_defaults = {k: None for k in saved}                  # 寬表語意
        try:
            base = {oid for oid, u in self.users.items() if self.group(uq.filters, {"one_id": oid}, now) and self._user_ok(decision, u)}
        finally:
            self.hot_defaults = saved
        if uq.behaviors or uq.sequences:
            sets: list[tuple[set[str], bool]] = []
            if uq.combine == "or" and uq.filters:                      # 「屬性 或 行為」：行為那邊對全體（只受行級範圍限制）算
                attr_base = base; base = {oid for oid, u in self.users.items() if self._user_ok(decision, u)}
            for b in uq.behaviors:
                rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for r in self.events:
                    if r["project"] == uq.project and b.time.start <= r["event_date"] <= b.time.end and r["event"] == b.event and self.group(b.filters, r, now) and self._row_filters_ok(decision, r, b.time.now or now):   # 回溯窗口以行為自己的 now 為準（與 SQL 的 now_sql 一致）
                        rows[r["one_id"]].append(r)
                if not b.done:
                    sets.append((set(rows), False)); continue
                ms = {o: m for o, m in ((o, self._measure(b, rs)) for o, rs in rows.items()) if m is not None}
                if b.count_op == "between": ok = {o for o, n in ms.items() if b.count[0] <= n <= b.count[1]}      # type: ignore[index]
                else:
                    cmp = {"gte": operator.ge, "lte": operator.le, "eq": operator.eq, "gt": operator.gt, "lt": operator.lt}[b.count_op]
                    ok = {o for o, n in ms.items() if cmp(n, b.count)}
                sets.append((ok, True))
            for sq in uq.sequences:                                     # 行為序列：與 SQL 同一套 windowFunnel 演算法
                chains: dict[str, list[tuple[int, int]]] = defaultdict(list)
                evs = {ev for ev, _ in sq.steps}
                for r in self.events:
                    if r["project"] == uq.project and sq.time.start <= r["event_date"] <= sq.time.end and r["event"] in evs and self._row_filters_ok(decision, r, sq.time.now or now):
                        sec = int((r["time"] - timedelta(hours=8)).replace(tzinfo=UTC).timestamp())
                        for k, (ev, fl) in enumerate(sq.steps):
                            if r["event"] == ev and self.group(fl, r, now):
                                chains[r["one_id"]].append((sec, k))
                done_set = {o for o, ch in chains.items() if self.window_funnel(ch, sq.window_s, len(sq.steps)) >= len(sq.steps)}
                sets.append((done_set, sq.done))
            if uq.behaviors_op == "and":
                for s_, done in sets:
                    base = (base & s_) if done else (base - s_)
            else:
                u: set[str] = set()
                for s_, done in sets:
                    u |= s_ if done else (base - s_)
                base &= u
            if uq.combine == "or" and uq.filters:
                base |= attr_base
        if uq.source:
            base &= self._source_users(uq.source, decision)
        return base

    def _measure(self, b: Any, rs: list[dict[str, Any]]) -> float | None:
        """行為指標：次數／天數／數值屬性的 sum、avg、max、min（NULL 不計；全 NULL → 不符合）。"""
        if b.agg == "count": return float(len(rs))
        if b.agg == "days": return float(len({x["event_date"] for x in rs}))
        vals = [float(v) for v in (self.value(b.field, x) for x in rs) if v is not None]
        if not vals: return None
        return {"sum": sum(vals), "avg": sum(vals) / len(vals), "max": max(vals), "min": min(vals)}[b.agg]

    def _source_users(self, s: Any, decision: Any) -> set[str]:
        if s.type == "import":
            return set(self.imports.get(s.import_id, set()))
        if s.type == "funnel":
            fq = s.query
            best: dict[str, int] = defaultdict(int)
            for (b, g), members in self._funnel_groups(fq, decision).items():
                if s.bucket is not None and b != s.bucket: continue
                if s.group is not None and g != s.group: continue
                for key, lvl in members:
                    best[key[0]] = max(best[key[0]], lvl)
            return {o for o, lvl in best.items() if (lvl >= s.step if s.reached else lvl == s.step - 1)}
        if s.type == "retention":
            rq = s.query
            out: set[str] = set()
            for (c, g), ms in self._retention_members(rq, decision).items():
                if c.isoformat() != s.cohort: continue
                if s.group is not None and g != s.group: continue
                for oid, ps in ms:
                    if s.retained is None or self._retained(rq.model, ps, s.period, rq.lo, rq.periods) == s.retained:
                        out.add(oid)
            return out
        if s.type == "attribution":
            aq = s.query; ti = next(i for i, t in enumerate(aq.touches) if t.alias == s.group)
            return {oid for oid, _, win, _ in self._attribution_conversions(aq, decision) if any(x[1] == ti and (s.bucket is None or x[2] == s.bucket) for x in win)}
        if s.type == "path":
            pq = s.query; out = set()
            for oid, path, _ in self._path_slices(pq, decision):
                i = int(s.period) if pq.direction == "after" else len(path) - 1 - (pq.steps - int(s.period))
                if 0 <= i < len(path) and path[i] == s.group: out.add(oid)
            return out
        qr = s.query; now = qr.time.now or date.today()
        fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
        m = next(x for x in qr.metrics if x.alias == s.metric)
        out = set()
        for r in self.events:
            if not (r["project"] == qr.project and qr.time.start <= r["event_date"] <= qr.time.end and self.group(qr.filters, r, now) and self._row_filters_ok(decision, r, now)): continue
            if m.event and r["event"] != m.event: continue
            if not self.group(m.filters, r, now): continue
            if m.field and self.value(m.field, r) is None: continue
            if s.bucket is not None and self.bucket(qr.time.grain, r) != s.bucket: continue
            if s.group is not None and any(self._masked_group(fp, g, i, self.value(g, r)) != v for i, (g, v) in enumerate(zip(qr.group_by, s.group))): continue
            out.add(r["one_id"])
        return out


    # ---- 路徑 ----
    def _path_slices(self, pq: PathQuery, decision: Any = None) -> list[tuple[str, tuple[str, ...], float]]:
        """每個起點（終點）出現 → (one_id, path, 耗時 ms)；與 SQL 的 ev→會話→合併→離開→切片同口徑。"""
        now = pq.time.now or date.today()
        if pq.sample:
            raise ValueError("oracle 不做抽樣")
        keep = set(pq.node_events) | {pq.anchor_event} | ({"$AppEnd"} if pq.exit_node else set())
        split = dict(pq.splits)
        def node(r: dict[str, Any]) -> str:
            ev = r["event"]
            if ev in split:
                v = self.value(split[ev], r); n = f"{ev}:{'' if v is None else v}"
            elif pq.exit_node and ev == "$AppEnd": n = EXIT
            else: n = ev
            if pq.node_events and pq.collapse_other and ev not in keep: return OTHER
            return n
        per_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self.events:
            if r["project"] != pq.project or not (pq.time.start <= r["event_date"] <= pq.time.end): continue
            if pq.node_events and not pq.collapse_other and r["event"] not in keep: continue
            if not self.group(pq.filters, r, now) or not self._row_filters_ok(decision, r, now): continue
            per_user[r["one_id"]].append(r)
        out: list[tuple[str, tuple[str, ...], float]] = []
        for oid, rs in per_user.items():
            rs.sort(key=lambda r: (r["time"], r["event_id"]))
            sessions: dict[Any, list[tuple[datetime, str, bool, str]]] = defaultdict(list)
            sid = 0; prev: datetime | None = None
            for r in rs:
                if pq.session_mode == "gap":
                    if prev is not None and (r["time"].replace(microsecond=0) - prev.replace(microsecond=0)).total_seconds() > pq.gap_minutes * 60: sid += 1
                    prev = r["time"]; key: Any = sid
                elif pq.session_mode == "id": key = r.get("session_id", "")
                else: key = 0
                n = node(r)
                sessions[key].append((r["time"], n, n == pq.anchor and self.group(pq.anchor_filters, r, now), r["event_id"]))
            for seq in sessions.values():
                seq = seq[: pq.max_events]
                if pq.merge_repeats:
                    seq = [x for i, x in enumerate(seq) if i == 0 or x[1] != seq[i - 1][1]]
                if pq.exit_node and seq[-1][1] != EXIT:
                    seq.append((seq[-1][0], EXIT, pq.anchor == EXIT, seq[-1][3]))
                for i, x in enumerate(seq):
                    if not x[2]: continue
                    sl = seq[i: i + pq.steps + 1] if pq.direction == "after" else seq[max(0, i - pq.steps): i + 1]
                    ms = (sl[-1][0].replace(microsecond=sl[-1][0].microsecond // 1000 * 1000) - sl[0][0].replace(microsecond=sl[0][0].microsecond // 1000 * 1000)).total_seconds() * 1000
                    out.append((oid, tuple(y[1] for y in sl), ms))
        return out

    def run_path(self, pq: PathQuery, decision: Any = None) -> list[dict[str, Any]]:
        k = int(getattr(decision, "k_anonymity", 0) or 0)
        slices = self._path_slices(pq, decision)
        anchor_users = len({o for o, _, _ in slices})
        agg: dict[tuple[str, ...], tuple[set[str], int, float]] = {}
        for oid, path, ms in slices:
            u, n, t = agg.get(path, (set(), 0, 0.0)); u.add(oid); agg[path] = (u, n + 1, t + ms)
        rows = []
        for path, (u, n, t) in agg.items():
            users = len(u); sup = bool(k and users < k)
            row: dict[str, Any] = {"path": list(path), "users": None if sup else users, "sessions": None if sup else n, "avg_ms": None if sup else t / n, "anchor_users": anchor_users, "_k": users}
            if k: row["suppressed"] = sup
            rows.append(row)
        rows.sort(key=lambda r: (-r["_k"], -(r["sessions"] if r["sessions"] is not None else agg[tuple(r["path"])][1]), r["path"]))
        return rows[: pq.top_n]


    # ---- 歸因 ----
    def _attribution_conversions(self, aq: AttributionQuery, decision: Any = None) -> list[tuple[str, float, list[tuple[datetime, int, str]], datetime]]:
        """每次轉化 → (one_id, 價值, 窗口內觸點 [(time, 觸點序號, 拆分值)] 依時間排序, 轉化時間)。觸點序號取第一個符合的觸點（同 SQL multiIf）。"""
        now = aq.time.now or date.today()
        if aq.sample:
            raise ValueError("oracle 不做抽樣")
        tev = {t.event for t in aq.touches}
        tstart = aq.touch_start()
        touches: dict[str, list[tuple[datetime, int, str]]] = defaultdict(list)
        convs: list[tuple[str, datetime, float]] = []
        for r in self.events:
            if r["project"] != aq.project or not self.group(aq.filters, r, now) or not self._row_filters_ok(decision, r, now): continue
            if r["event"] == aq.target and aq.time.start <= r["event_date"] <= aq.time.end and self.group(aq.target_filters, r, now):
                v = self.value(aq.value, r) if aq.value else 1
                convs.append((r["one_id"], r["time"], float(v) if v is not None else 0.0))
            if r["event"] in tev and tstart <= r["event_date"] <= aq.time.end:
                i = next((j for j, t in enumerate(aq.touches) if t.event == r["event"] and self.group(t.filters, r, now)), None)
                if i is None: continue
                sp = self.value(aq.split, r) if aq.split else ""
                touches[r["one_id"]].append((r["time"], i, "" if sp is None else str(sp)))
        out = []
        for oid, ct, v in convs:
            win = sorted([x for x in touches.get(oid, []) if x[0] < ct and x[0] >= ct - timedelta(days=aq.window_days)], key=lambda x: x[0])
            out.append((oid, v, win, ct))
        return out

    def _weights(self, aq: AttributionQuery, model: str, ct: datetime, win: list[tuple[datetime, int, str]]) -> list[float]:
        n = len(win)
        if model == "first": return [1.0 if i == 0 else 0.0 for i in range(n)]
        if model == "last": return [1.0 if i == n - 1 else 0.0 for i in range(n)]
        if model == "linear": return [1.0 / n] * n
        if model == "position":
            if n == 1: return [1.0]
            if n == 2: return [0.5, 0.5]
            mid = (1 - aq.w_first - aq.w_last) / (n - 2)
            return [aq.w_first if i == 0 else aq.w_last if i == n - 1 else mid for i in range(n)]
        if model == "decay":
            def ms(t: datetime) -> int: return int(t.timestamp() * 1000)
            raw = [2 ** (-((ms(ct) - ms(x[0])) / 1000) / (aq.half_life_days * 86400)) for x in win]
            s = sum(raw); return [r / s for r in raw]
        raw = [aq.touches[x[1]].weight for x in win]; s = sum(raw)
        return [1.0 / n] * n if s == 0 else [r / s for r in raw]

    def run_attribution(self, aq: AttributionQuery, decision: Any = None) -> list[dict[str, Any]]:
        k = int(getattr(decision, "k_anonymity", 0) or 0)
        convs = self._attribution_conversions(aq, decision)
        rows: dict[tuple, dict[str, Any]] = {}
        for oid, v, win, ct in convs:
            if not win: continue
            ws = {m: self._weights(aq, m, ct, win) for m in aq.models}
            for i, x in enumerate(win):
                key = (x[1], x[2]) if aq.split else (x[1],)
                r = rows.setdefault(key, {"users": set(), **{f"{m}_conv": 0.0 for m in aq.models}, **{f"{m}_val": 0.0 for m in aq.models}})
                r["users"].add(oid)
                for m in aq.models:
                    r[f"{m}_conv"] += ws[m][i]; r[f"{m}_val"] += ws[m][i] * v
        total = len(convs); converters = len({c[0] for c in convs}); touched = sum(1 for c in convs if c[2]); tv = sum(c[1] for c in convs)
        avg = (sum(len(c[2]) for c in convs if c[2]) / touched) if touched else None
        out = []
        for key in sorted(rows):
            r = rows[key]; users = len(r["users"]); sup = bool(k and users < k)
            row: dict[str, Any] = {"ti": key[0]}
            if aq.split: row["sp"] = key[1]
            for m in aq.models:
                row[f"{m}_conv"] = None if sup else r[f"{m}_conv"]; row[f"{m}_val"] = None if sup else r[f"{m}_val"]
            row.update({"_k": users, "conversions": total, "converters": converters, "touched": touched, "total_value": tv, "avg_touches": avg})
            if k: row["suppressed"] = sup
            out.append(row)
        return out
