"""黃金測試的參考實作：用純 Python 對同一份事件列算出查詢結果，和 ClickHouse 跑出的 SQL 結果逐列比對（誤差 0）。

語意要和 ClickHouse 一致：
  - 字串欄位未設值是 ''（LowCardinality(String)）；JSON／用戶冷屬性缺值是 None；dictGet 缺鍵回型別預設（'' / 0）
  - sumIf 無列 → 0；avg／max／min／quantile 無列 → None；avg_per_user／avg_val 分母 0 → None
  - quantileExact(level) = 排序後第 floor(level × n) 個元素（ClickHouse nth_element 定義）
  - K 匿名：該列（分組）去重用戶數 < K → 指標 None、suppressed=True
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .catalog import FieldRef
from .parser import Cond, Formula, Group, Metric, Query
from .policy import bucket_label, parse_policy


class Oracle:
    def __init__(self, events: list[dict[str, Any]], users: dict[str, dict[str, Any]], segments: dict[str, set[str]] | None = None, hot_defaults: dict[str, Any] | None = None):
        self.events, self.users, self.segments = events, users, segments or {}
        self.hot_defaults = hot_defaults or {"string": "", "number": 0.0, "bool": 0, "date": date(1970, 1, 1)}

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
            return u[f.name]
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
    def run(self, q: Query, decision: Any = None) -> list[dict[str, Any]]:
        now = q.time.now or date.today()
        k = int(getattr(decision, "k_anonymity", 0) or 0)
        fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
        rows = [r for r in self.events if r["project"] == q.project and q.time.start <= r["event_date"] <= q.time.end
                and (not q.events or r["event"] in q.events) and self.group(q.filters, r, now) and self._row_filters_ok(decision, r, now)]
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
                           formulas=q.formulas, filters=q.filters, group_by=q.group_by, top_n=None, compare=None, sample=None, exact=q.exact)
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
