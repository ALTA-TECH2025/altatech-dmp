"""留存樣板。查詢形狀見 openapi RetentionQuery。

口徑（對齊神策留存分析）：
  同期群（cohort）= 在 time 範圍內做過初始行為的用戶，按初始行為發生的期（日／週／月）歸入；同一用戶可屬多個同期群。
  回訪 = 做過回訪行為（可為任一事件）的期；periods 陣列 = 回訪期 − 同期群期（0..P）。
  模型：date       第 n 期當期有回訪                         has(periods, n)
        interval   第 1..n 期內任一期有回訪（含當期則 0..n）    arrayExists(p -> lo <= p <= n)
        continuous 第 1..n 期每一期都有回訪                   arrayAll over range(lo, n+1)
        churn      第 n 期起到第 P 期都沒有回訪（流失）          NOT arrayExists(p -> n <= p <= P)
SQL 形狀：
  WITH c AS (SELECT one_id, <期>(initial time) AS cohort [, argMinIf(<分組>, time, init) AS g1] FROM events WHERE init GROUP BY one_id, cohort),
       r AS (SELECT one_id, <期>(return time) AS rb FROM events WHERE ret GROUP BY one_id, rb),
       u AS (SELECT c.one_id, c.cohort [, c.g1], groupArray(dateDiff(unit, c.cohort, r.rb)) AS periods FROM c LEFT JOIN r USING one_id WHERE rb BETWEEN cohort AND cohort + P GROUP BY …)
  SELECT cohort [, g1], period, uniqExact(one_id) AS users, uniqExactIf(one_id, <模型條件>) AS retained FROM u ARRAY JOIN range(lo, P+1) AS period GROUP BY …
LEFT JOIN 讓沒回訪的用戶 periods 為空陣列（仍算在同期群人數）。K 匿名：同期群人數 < K → retained／rate NULL。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .catalog import TZ, Catalog, FieldRef
from .events import Compiled, Ctx, group_sql, q
from .parser import Cond, Group, Query, TimeRange, parse_filters

MODELS = ("date", "interval", "continuous", "churn")
UNITS = ("day", "week", "month")


@dataclass
class Behavior:
    event: str | None
    filters: Group | None = None


@dataclass
class RetentionQuery:
    project: str
    initial: Behavior
    ret: Behavior
    model: str
    unit: str
    periods: int
    include_day0: bool
    group_by: FieldRef | None
    filters: Group | None
    time: TimeRange
    sample: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def lo(self) -> int:
        return 0 if self.include_day0 else 1

    def as_query(self) -> Query:
        return Query(project=self.project, metrics=[], time=self.time, filters=self.filters, group_by=[self.group_by] if self.group_by else [])

    def return_end(self) -> date:
        """回訪事件掃描的結束日：最後一個同期群 + P 期。"""
        e = self.time.end
        if self.unit == "day": return e + timedelta(days=self.periods)
        if self.unit == "week": return e + timedelta(days=7 * (self.periods + 1))
        y, m = divmod(e.month - 1 + self.periods + 1, 12)
        return date(e.year + y, m + 1, 1) - timedelta(days=1)


def _behavior(cat: Catalog, raw: dict[str, Any] | None, required: bool) -> Behavior:
    raw = raw or {}
    ev = raw.get("event")
    if ev in (None, "", "any", "$Anything"):
        if required:
            raise ValueError("初始行為需要事件")
        return Behavior(None, parse_filters(cat, raw.get("filters")))
    name = cat.canonical_event(str(ev))
    if not name:
        raise ValueError(f"事件 {ev} 不在事件字典")
    return Behavior(name, parse_filters(cat, raw.get("filters")))


def parse_retention(raw: dict[str, Any], cat: Catalog | None = None) -> RetentionQuery:
    cat = cat or Catalog()
    initial = _behavior(cat, raw.get("initial"), True)
    ret = _behavior(cat, raw.get("return"), False)
    model = str(raw.get("model") or "date")
    if model not in MODELS:
        raise ValueError(f"model 只能是 {MODELS}")
    unit = str(raw.get("unit") or "day")
    if unit not in UNITS:
        raise ValueError(f"unit 只能是 {UNITS}")
    periods = int(raw["periods"]) if raw.get("periods") is not None else 7
    if not 1 <= periods <= 90:
        raise ValueError("periods 需在 1–90")
    gb = raw.get("group_by") or []
    if len(gb) > 1:
        raise ValueError("留存分組最多 1 維")
    group = None
    if gb:
        g = gb[0] if isinstance(gb[0], dict) else {"field": gb[0]}
        group = cat.resolve(str(g["field"]), g.get("type"))
        if group.kind == "segment" or group.name == "one_id":
            raise ValueError("分組不能是分群或 one_id")
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
        c = Cond(cat.resolve(f"seg.{raw['segment']}"), "in", None)
        filters = Group("and", [filters, c]) if filters else Group("and", [c])
    sample = raw.get("sample")
    if sample is not None:
        sample = float(sample)
        if not 0 < sample < 1:
            raise ValueError("sample 必須在 (0,1)")
    return RetentionQuery(str(raw.get("project") or "altatech_dmp"), initial, ret, model, unit, periods, bool(raw.get("include_day0")), group, filters, TimeRange(start, end, "day", now), sample, raw)


def bucket_expr(unit: str, col: str = "e.time") -> str:
    d = f"toDate({col}, '{TZ}')"
    return {"day": d, "week": f"toMonday({d})", "month": f"toStartOfMonth({d})"}[unit]


def compile_retention(rq: RetentionQuery, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    ctx = Ctx(cat, rq.as_query(), "L0")
    def cond(b: Behavior) -> str:
        parts = ([f"e.event = {q(b.event)}"] if b.event else []) + ([group_sql(b.filters, ctx)] if b.filters else [])
        return "(" + " AND ".join(parts) + ")" if parts else "1"
    init_c, ret_c = cond(rq.initial), cond(rq.ret)
    common = [f"e.project = {q(rq.project)}"]
    if rq.filters:
        common.append(group_sql(rq.filters, ctx))
    warnings: list[str] = []; row_filters: list[str] = []
    if decision is not None:
        from .policy import inject
        row_filters, _, _, w = inject(decision, rq.as_query(), cat, ctx, [])
        common += row_filters; warnings += w
    k = int(getattr(decision, "k_anonymity", 0) or 0)
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sample = f" SAMPLE {rq.sample}" if rq.sample else ""
    src = f"dmp.events AS e{sample}{cold}"
    gsel = f", argMinIf({ctx.expr(rq.group_by)}, e.time, {init_c}) AS g1" if rq.group_by else ""
    init_where = common + [f"e.event_date BETWEEN toDate({q(rq.time.start.isoformat())}) AND toDate({q(rq.time.end.isoformat())})"] + ([f"e.event = {q(rq.initial.event)}"] if rq.initial.event else [])
    c_sql = f"SELECT e.one_id AS one_id, {bucket_expr(rq.unit)} AS cohort{gsel} FROM {src} WHERE {' AND '.join(init_where)} AND {init_c} GROUP BY one_id, cohort"
    ret_where = common + [f"e.event_date BETWEEN toDate({q(rq.time.start.isoformat())}) AND toDate({q(rq.return_end().isoformat())})"] + ([f"e.event = {q(rq.ret.event)}"] if rq.ret.event else [])
    r_sql = f"SELECT e.one_id AS one_id, {bucket_expr(rq.unit)} AS rb FROM {src} WHERE {' AND '.join(ret_where)} AND {ret_c} GROUP BY one_id, rb"
    gcols = ["cohort"] + (["g1"] if rq.group_by else [])
    diff = f"dateDiff('{rq.unit}', c.cohort, r.rb)"
    u_sql = (f"SELECT c.one_id AS one_id, c.cohort AS cohort{', c.g1 AS g1' if rq.group_by else ''}, "
             f"groupArrayIf({diff}, r.rb >= c.cohort AND {diff} <= {rq.periods}) AS periods FROM c LEFT JOIN r ON c.one_id = r.one_id GROUP BY {', '.join('c.' + x for x in ['one_id'] + gcols)}")
    P = rq.periods; lo = rq.lo
    model_cond = {
        "date": "has(periods, period)",
        "interval": f"arrayExists(p -> p >= {lo} AND p <= period, periods)",
        "continuous": f"arrayAll(n -> has(periods, n), range({lo}, period + 1))",
        "churn": f"NOT arrayExists(p -> p >= period AND p <= {P}, periods)",
    }[rq.model]
    inner = f"SELECT {', '.join(gcols)}, period, uniqExact(one_id) AS users, uniqExactIf(one_id, {model_cond}) AS retained FROM u ARRAY JOIN range({lo}, {P + 1}) AS period GROUP BY {', '.join(gcols)}, period"
    sel = gcols + ["period", "users"] + ([f"if(users < {k}, NULL, retained) AS retained", f"if(users < {k}, NULL, retained / users) AS rate", f"users < {k} AS suppressed"] if k else ["retained", "retained / users AS rate"])
    sql = f"WITH c AS ({c_sql}), r AS ({r_sql}), u AS ({u_sql}) SELECT {', '.join(sel)} FROM ({inner}) ORDER BY {', '.join(gcols)}, period"
    from .policy import budget
    cols = gcols + ["period", "users", "retained", "rate"] + (["suppressed"] if k else [])
    fields = _fields(rq)
    lineage = ["dmp.events"] + (["dmp.users_dict"] if any(f.kind == "user_hot" for f in fields) else []) + (["dmp.users"] if ctx.cold else []) \
        + (["dmp.segment_members"] if any(f.kind == "segment" for f in fields) else []) + [f"event:{b.event}" for b in (rq.initial, rq.ret) if b.event]
    return Compiled(sql=sql, layer="L0", columns=cols, settings=budget(profile), lineage=sorted(set(lineage)), warnings=warnings, k_anonymity=k, masked={}, row_filters=row_filters)


def _fields(rq: RetentionQuery) -> list[FieldRef]:
    out: list[FieldRef] = []
    def rec(x: Any) -> None:
        if x is None: return
        for it in x.items:
            if isinstance(it, Group): rec(it)
            else: out.append(it.field)
    for g in (rq.filters, rq.initial.filters, rq.ret.filters): rec(g)
    if rq.group_by: out.append(rq.group_by)
    return out


def with_complete(rows: list[dict[str, Any]], rq: RetentionQuery, today: date) -> list[dict[str, Any]]:
    """API 回應：補 complete（該期是否已過：同期群期 + period 的期末 < today）。"""
    out = []
    for r in rows:
        c = date.fromisoformat(str(r["cohort"])[:10]); n = int(r["period"])
        if rq.unit == "day": end = c + timedelta(days=n)
        elif rq.unit == "week": end = c + timedelta(days=7 * n + 6)
        else:
            y, m = divmod(c.month - 1 + n + 1, 12); end = date(c.year + y, m + 1, 1) - timedelta(days=1)
        out.append({**r, "complete": end < today})
    return out
