"""漏斗樣板：windowFunnel。查詢形狀見 openapi FunnelQuery。

SQL 形狀：
  WITH base AS (
    SELECT e.one_id [, <關聯屬性> AS rel] [, argMinIf(<分組>, e.time, cond1) AS g1] [, toDate(minIf(e.time, cond1), 'Asia/Taipei') AS bucket],
           windowFunnel(<秒>[, 'strict_order'])(toDateTime(e.time), cond1, cond2, …) AS level
    FROM dmp.events AS e [SAMPLE r] WHERE project／日期／事件 IN／全局篩選／Policy 行級
    GROUP BY e.one_id [, rel])
  SELECT [bucket,] [g1,] step, uniqExactIf(one_id, level >= step) AS users   -- metric=times 時 countIf(level >= step)
  FROM base ARRAY JOIN range(1, N+1) AS step GROUP BY … ORDER BY …
關聯屬性（業務 ID）：同一個 (one_id, 關聯值) 才算同一條流程，所以 GROUP BY 多一個鍵；users 仍以 one_id 去重，times 數流程條數。
K 匿名：每列（分組 × 步）人數 < K → NULL、suppressed=1。windowFunnel 的時間用秒（toDateTime），oracle 同口徑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .catalog import TZ, Catalog, FieldRef
from .events import Compiled, Ctx, group_sql, q
from .parser import Group, Query, TimeRange, parse_filters

UNIT_S = {"minute": 60, "hour": 3600, "day": 86400}
MAX_WINDOW_S = 90 * 86400


@dataclass
class Step:
    event: str
    alias: str
    filters: Group | None = None


@dataclass
class FunnelQuery:
    project: str
    steps: list[Step]
    window_s: int
    order: str                       # loose | strict
    relation: FieldRef | None
    metric: str                      # users | times
    group_by: FieldRef | None
    trend: str | None                # day | week | month
    filters: Group | None
    time: TimeRange
    sample: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_query(self) -> Query:
        """給 Ctx／policy 用的最小 Query（只用到 time、filters、group_by、metrics 為空）。"""
        return Query(project=self.project, metrics=[], time=self.time, filters=self.filters, group_by=[self.group_by] if self.group_by else [])


def parse_funnel(raw: dict[str, Any], cat: Catalog | None = None) -> FunnelQuery:
    cat = cat or Catalog()
    steps_raw = list(raw.get("steps") or [])
    if not 2 <= len(steps_raw) <= 10:
        raise ValueError("漏斗需要 2–10 步")
    steps: list[Step] = []
    for i, s in enumerate(steps_raw):
        ev = cat.canonical_event(str(s.get("event") or ""))
        if not ev:
            raise ValueError(f"第 {i + 1} 步事件 {s.get('event')} 不在事件字典")
        steps.append(Step(ev, str(s.get("alias") or ev), parse_filters(cat, s.get("filters"))))
    w = raw.get("window") or {}
    if w.get("unit") not in UNIT_S or int(w.get("value", 0)) < 1:
        raise ValueError("window 需要 value ≥ 1 與 unit ∈ minute/hour/day")
    window_s = int(w["value"]) * UNIT_S[w["unit"]]
    if window_s > MAX_WINDOW_S:
        raise ValueError("窗口最長 90 天")
    order = str(raw.get("order") or "loose")
    if order not in ("loose", "strict"):
        raise ValueError("order 只能是 loose／strict")
    relation = cat.resolve(str(raw["relation"]), raw.get("relation_type")) if raw.get("relation") else None
    if relation and relation.kind in ("segment", "user_hot", "user_cold"):
        raise ValueError("關聯屬性必須是事件欄位或事件屬性（業務 ID）")
    metric = str(raw.get("metric") or "users")
    if metric not in ("users", "times"):
        raise ValueError("metric 只能是 users／times")
    if metric == "times" and not relation:
        raise ValueError("次數口徑需要指定關聯屬性（以業務 ID 計流程數）")
    gb = raw.get("group_by") or []
    if len(gb) > 1:
        raise ValueError("漏斗分組最多 1 維")
    group = None
    if gb:
        g = gb[0] if isinstance(gb[0], dict) else {"field": gb[0]}
        group = cat.resolve(str(g["field"]), g.get("type"))
        if group.kind == "segment" or group.name == "one_id":
            raise ValueError("分組不能是分群或 one_id")
    trend = raw.get("trend")
    if trend not in (None, "", "day", "week", "month"):
        raise ValueError("trend 只能是 day／week／month")
    tm = raw.get("time") or {}
    try:
        start, end = date.fromisoformat(str(tm["from"])[:10]), date.fromisoformat(str(tm["to"])[:10])
    except (KeyError, ValueError):
        raise ValueError("time.from／time.to 必填且為 YYYY-MM-DD") from None
    if end < start:
        raise ValueError("time.to 早於 time.from")
    now = date.fromisoformat(str(tm["now"])[:10]) if tm.get("now") else None
    filters = parse_filters(cat, raw.get("filters"))
    if raw.get("segment"):
        seg = cat.resolve(f"seg.{raw['segment']}")
        from .parser import Cond
        c = Cond(seg, "in", None)
        filters = Group("and", [filters, c]) if filters else Group("and", [c])
    sample = raw.get("sample")
    if sample is not None:
        sample = float(sample)
        if not 0 < sample < 1:
            raise ValueError("sample 必須在 (0,1)")
    return FunnelQuery(str(raw.get("project") or "altatech_dmp"), steps, window_s, order, relation, metric, group, trend or None, filters, TimeRange(start, end, "day", now), sample, raw)


@dataclass
class FunnelBase:
    """每用戶[, 關聯屬性] 一列的 level（＋趨勢桶、分組值）：漏斗結果與用戶列表下鑽共用。"""
    sql: str
    outer_cols: list[str]
    ctx: Ctx
    warnings: list[str]
    row_filters: list[str]
    k: int


def funnel_base(fq: FunnelQuery, cat: Catalog | None = None, decision: Any = None) -> FunnelBase:
    cat = cat or Catalog()
    ctx = Ctx(cat, fq.as_query(), "L0")
    conds = []
    for s in fq.steps:
        parts = [f"e.event = {q(s.event)}"] + ([group_sql(s.filters, ctx)] if s.filters else [])
        conds.append("(" + " AND ".join(parts) + ")")
    where = [f"e.project = {q(fq.project)}", f"e.event_date BETWEEN toDate({q(fq.time.start.isoformat())}) AND toDate({q(fq.time.end.isoformat())})",
             f"e.event IN ({', '.join(q(x) for x in sorted({s.event for s in fq.steps}))})"]
    if fq.filters:
        where.append(group_sql(fq.filters, ctx))
    warnings: list[str] = []; row_filters: list[str] = []
    if decision is not None:
        from .policy import inject
        row_filters, _, _, w = inject(decision, fq.as_query(), cat, ctx, [])
        where += row_filters; warnings += w
    k = int(getattr(decision, "k_anonymity", 0) or 0)
    sel = ["e.one_id"]; keys = ["e.one_id"]; outer_cols: list[str] = []
    if fq.relation:
        sel.append(f"{ctx.expr(fq.relation)} AS rel"); keys.append("rel")
    if fq.trend:
        d = f"toDate(minIf(e.time, {conds[0]}), '{TZ}')"
        sel.append({"day": d, "week": f"toMonday({d})", "month": f"toStartOfMonth({d})"}[fq.trend] + " AS bucket"); outer_cols.append("bucket")
    if fq.group_by:
        sel.append(f"argMinIf({ctx.expr(fq.group_by)}, e.time, {conds[0]}) AS g1"); outer_cols.append("g1")
    mode = ", 'strict_order'" if fq.order == "strict" else ""
    sel.append(f"windowFunnel({fq.window_s}{mode})(toDateTime(e.time), {', '.join(conds)}) AS level")
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sample = f" SAMPLE {fq.sample}" if fq.sample else ""
    base = f"SELECT {', '.join(sel)} FROM dmp.events AS e{sample}{cold} WHERE {' AND '.join(where)} GROUP BY {', '.join(keys)} HAVING level > 0"   # 沒做第一步的用戶不產生分組／趨勢桶
    return FunnelBase(base, outer_cols, ctx, warnings, row_filters, k)


def compile_funnel(fq: FunnelQuery, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    fb = funnel_base(fq, cat, decision)
    base, outer_cols, ctx, warnings, row_filters, k = fb.sql, fb.outer_cols, fb.ctx, fb.warnings, fb.row_filters, fb.k
    agg = "uniqExactIf(one_id, level >= step)" if fq.metric == "users" else "countIf(level >= step)"
    kexpr = "uniqExactIf(one_id, level >= step)"
    grp = outer_cols + ["step"]
    inner = f"SELECT {', '.join(outer_cols + ['step'])}, {agg} AS users, {kexpr} AS _k FROM base ARRAY JOIN range(1, {len(fq.steps) + 1}) AS step GROUP BY {', '.join(grp)}"
    outer_sel = outer_cols + ["step", (f"if(_k < {k}, NULL, users) AS users" if k else "users"), "_k"] + ([f"_k < {k} AS suppressed"] if k else [])
    sql = f"WITH base AS ({base}) SELECT {', '.join(outer_sel)} FROM ({inner}) ORDER BY {', '.join(grp)}"
    from .policy import budget
    cols = outer_cols + ["step", "users", "_k"] + (["suppressed"] if k else [])
    lineage = ["dmp.events"] + (["dmp.users_dict"] if any(f.kind == "user_hot" for f in _fields(fq)) else []) + (["dmp.users"] if ctx.cold else []) \
        + (["dmp.segment_members"] if any(f.kind == "segment" for f in _fields(fq)) else []) + [f"event:{s.event}" for s in fq.steps]
    if fq.order == "strict":
        warnings.append("strict_order：步驟之間若出現其他步驟的事件則中斷（黃金測試只鎖定寬鬆模式）")
    return Compiled(sql=sql, layer="L0", columns=cols, settings=budget(profile), lineage=sorted(set(lineage)), warnings=warnings, k_anonymity=k, masked={}, row_filters=row_filters)


def _fields(fq: FunnelQuery) -> list[FieldRef]:
    out: list[FieldRef] = []
    for g in [fq.filters] + [s.filters for s in fq.steps]:
        def rec(x: Any) -> None:
            if x is None: return
            for it in x.items:
                if isinstance(it, Group): rec(it)
                else: out.append(it.field)
        rec(g)
    if fq.group_by: out.append(fq.group_by)
    if fq.relation: out.append(fq.relation)
    return out


def with_rates(rows: list[dict[str, Any]], steps: list[Step]) -> list[dict[str, Any]]:
    """API 回應：補 alias／event、相對上一步與第一步的轉換率（抑制列為 None）。rows 需依 (bucket, g1, step) 排序。"""
    out: list[dict[str, Any]] = []
    first: dict[tuple, Any] = {}; prev: dict[tuple, Any] = {}
    for r in rows:
        key = (r.get("bucket"), r.get("g1")); st = int(r["step"]); u = r.get("users")
        if st == 1:
            first[key] = u; prev[key] = u
        f, p = first.get(key), prev.get(key)
        r2 = dict(r); r2["alias"] = steps[st - 1].alias; r2["event"] = steps[st - 1].event
        r2["rate_first"] = (u / f) if (u is not None and f) else None
        r2["rate_prev"] = (u / p) if (u is not None and p) else None
        prev[key] = u
        out.append(r2)
    return out
