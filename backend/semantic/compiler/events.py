"""事件分析樣板：AST → ClickHouse SQL。

層級（planner）：
  L1  dmp.events_daily_agg（日聚合 MV）：指標只有 total／sum(amount)，欄位只用 event/platform/channel/utm_source，粒度 ≥ day，無抽樣、無用戶屬性。
      users 走 MV 是近似值（uniqMerge），黃金測試要求誤差 0，因此 users 一律走 L0（之後可用 exact:false 放行）。
  L0  dmp.events 明細（可加 SAMPLE）。

Policy 注入（policy.inject）：行級 WHERE（分公司／營業員／回看天數）、欄位遮罩（bucket／round／hide）、K 匿名（每列不足 K 人 → 指標置 NULL、suppressed=1）、
預算 settings（max_bytes_to_read／max_execution_time／max_result_rows）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .catalog import TZ, Catalog, FieldRef
from .parser import Cond, Formula, Group, Metric, Query

MV_AGGS = {"total", "sum"}


@dataclass
class Compiled:
    sql: str
    layer: str                                   # L0 | L1
    columns: list[str]
    settings: dict[str, Any] = field(default_factory=dict)
    lineage: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    k_anonymity: int = 0
    masked: dict[str, str] = field(default_factory=dict)   # 輸出欄 → 遮罩策略
    row_filters: list[str] = field(default_factory=list)


def q(s: Any) -> str:
    """SQL 字串常值。"""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _like(v: str) -> str:
    return q("%" + str(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")


def cond_sql(c: Cond, ctx: Ctx) -> str:
    f, op, v = c.field, c.op, c.value
    x = ctx.expr(f)
    if f.kind == "segment":
        ver = f"version = {int(f.sql)}" if f.sql else f"version = (SELECT max(version) FROM dmp.segment_members WHERE segment_id = {q(f.name)})"   # 預設最新版本
        sub = f"SELECT one_id_uint FROM dmp.segment_members WHERE segment_id = {q(f.name)} AND {ver}"
        return f"e.one_id_uint {'IN' if op == 'in' else 'NOT IN'} ({sub})"
    if f.type == "string":
        if op == "eq": return f"{x} = {q(v)}"
        if op == "neq": return f"{x} != {q(v)}"
        if op == "in": return f"{x} IN ({', '.join(q(i) for i in v)})"
        if op == "nin": return f"{x} NOT IN ({', '.join(q(i) for i in v)})"
        if op == "like": return f"{x} LIKE {_like(v)}"
        if op == "nlike": return f"NOT ({x} LIKE {_like(v)})"
        if op == "set": return f"({x} IS NOT NULL AND {x} != '')"
        if op == "nset": return f"({x} IS NULL OR {x} = '')"
    if f.type == "number":
        sym = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        if op in sym: return f"{x} {sym[op]} {v!r}"
        if op == "between": return f"{x} BETWEEN {v[0]!r} AND {v[1]!r}"
        if op == "set": return f"{x} IS NOT NULL"
        if op == "nset": return f"{x} IS NULL"
    if f.type == "bool":
        return f"{x} = {1 if op == 'true' else 0}"
    if f.type == "date":
        if op == "rel":
            now = ctx.now_sql()
            return f"{x} >= ({now} - INTERVAL {int(v['days'])} DAY)"
        if op == "between": return f"{x} BETWEEN toDate({q(v[0])}) AND toDate({q(v[1])})"
        if op == "set": return f"{x} IS NOT NULL"
        if op == "nset": return f"{x} IS NULL"
    raise ValueError(f"無法編譯條件 {f.key} {op}")


def group_sql(g: Group | None, ctx: Ctx) -> str:
    if g is None or not g.items:
        return "1"
    parts = [group_sql(it, ctx) if isinstance(it, Group) else cond_sql(it, ctx) for it in g.items]
    return "(" + f" {g.op.upper()} ".join(parts) + ")"


class Ctx:
    def __init__(self, cat: Catalog, query: Query, layer: str):
        self.cat, self.q, self.layer = cat, query, layer
        self.t = "a" if layer == "L1" else "e"          # 來源表別名
        self.cold: dict[str, FieldRef] = {}

    def mcond(self, m: Metric) -> str:
        """指標自己的條件：事件名 + 指標內篩選。"""
        parts = [f"{self.t}.event = {q(m.event)}" if m.event else "", group_sql(m.filters, self) if m.filters else ""]
        return " AND ".join(x for x in parts if x) or "1"

    def expr(self, f: FieldRef) -> str:
        if f.kind == "user_cold":
            self.cold[f.name] = f
        if self.layer == "L1":
            return f.sql.replace("e.", "a.", 1) if f.kind == "column" else f.sql
        return f.sql

    def now_sql(self) -> str:
        return f"toDate({q(self.q.time.now.isoformat())})" if self.q.time.now else f"toDate(now(), '{TZ}')"      # today() 不吃時區參數


def bucket_sql(grain: str, ctx: Ctx) -> str | None:
    t = "e.time" if ctx.layer == "L0" else None
    d = "e.event_date" if ctx.layer == "L0" else "a.event_date"
    return {
        "minute": f"toStartOfMinute({t}, '{TZ}')" if t else None,
        "hour": f"toStartOfHour({t}, '{TZ}')" if t else None,
        "day": d, "week": f"toMonday({d})", "month": f"toStartOfMonth({d})", "total": None,
    }[grain]


def metric_sql(m: Metric, ctx: Ctx) -> str:
    cond = ctx.mcond(m)
    if ctx.layer == "L1":
        if m.agg == "total": return f"countMergeIf(a.cnt, {cond})"
        if m.agg == "sum": return f"sumMergeIf(a.amount, {cond})"
        raise ValueError(f"L1 不支援 {m.agg}")
    fx = ctx.expr(m.field) if m.field else None
    users = f"uniqExactIf(e.one_id_uint, {cond})"      # 去重用 UInt64（cityHash64(one_id)）：結果同字串、記憶體約 1/3；1,500 萬人碰撞機率約 6e-6
    if m.agg == "total": return f"countIf({cond})"
    if m.agg == "users": return users
    if m.agg == "avg_per_user": return f"if({users} = 0, NULL, countIf({cond}) / {users})"
    if m.agg == "uniq": return f"uniqExactIf({fx}, {cond} AND {fx} IS NOT NULL)"
    if m.agg in ("sum", "avg", "max", "min"): return f"{m.agg}If({fx}, {cond} AND {fx} IS NOT NULL)"
    if m.agg == "avg_val": return f"if({users} = 0, NULL, sumIf({fx}, {cond} AND {fx} IS NOT NULL) / {users})"
    if m.agg in ("p25", "p50", "p75", "p90"):
        return f"quantileExactIf({int(m.agg[1:]) / 100})({fx}, {cond} AND {fx} IS NOT NULL)"
    if m.agg == "mau":
        return "0"   # 佔位；真正的值由 mau CTE 以月份 JOIN 補上（見 compile_events）
    raise ValueError(m.agg)


def formula_sql(f: Formula) -> str:
    """四則運算 → SQL；除法包成 if(分母=0, NULL, …)。"""
    toks = list(f.tokens); pos = 0

    def peek() -> str | None:
        return toks[pos] if pos < len(toks) else None

    def take() -> str:
        nonlocal pos; pos += 1; return toks[pos - 1]

    def atom() -> str:
        t = take()
        if t == "(":
            x = expr()
            if take() != ")": raise ValueError(f"公式括號不平衡：{f.expr}")
            return f"({x})"
        if t == "-": return f"(-{atom()})"
        return t

    def term() -> str:
        x = atom()
        while peek() in ("*", "/"):
            op = take(); y = atom()
            x = f"({x} * {y})" if op == "*" else f"if({y} = 0, NULL, {x} / {y})"
        return x

    def expr() -> str:
        x = term()
        while peek() in ("+", "-"):
            op = take(); y = term(); x = f"({x} {op} {y})"
        return x
    out = expr()
    if pos != len(toks): raise ValueError(f"公式語法錯誤：{f.expr}")
    return out


def plan(qr: Query) -> tuple[str, list[str]]:
    """選層：回傳 (layer, reasons)。"""
    why: list[str] = []
    if qr.exact: why.append("exact")
    if qr.sample: why.append("sample")
    if qr.time.grain in ("minute", "hour"): why.append("grain<day")
    if any(m.agg not in MV_AGGS for m in qr.metrics): why.append("agg not in MV")
    if any(m.agg == "sum" and m.field and m.field.name != "amount" for m in qr.metrics): why.append("sum field not amount")
    if any(not f.mv for f in qr.walk_fields(metric_fields=False)): why.append("field not in MV")
    return ("L0" if why else "L1"), why


def compile_events(qr: Query, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    """decision = security.policy.Decision（None = 不注入，僅測試用）；profile = budgets.yaml 的設定檔（human／batch／sql_user）。"""
    cat = cat or Catalog()
    layer, why = plan(qr)
    ctx = Ctx(cat, qr, layer)
    src = "dmp.events_daily_agg AS a" if layer == "L1" else "dmp.events AS e"
    tcol = "a" if layer == "L1" else "e"
    bucket = bucket_sql(qr.time.grain, ctx)
    sel: list[str] = []; cols: list[str] = []; grp: list[str] = []
    if bucket:
        sel.append(f"{bucket} AS bucket"); cols.append("bucket"); grp.append("bucket")
    for i, g in enumerate(qr.group_by):
        name = f"g{i + 1}"
        sel.append(f"{ctx.expr(g)} AS {name}"); cols.append(name); grp.append(name)
    msql = [metric_sql(m, ctx) for m in qr.metrics]
    kexpr = "uniqMerge(a.users)" if layer == "L1" else "uniqExact(e.one_id_uint)"
    where = [f"{tcol}.project = {q(qr.project)}", f"{tcol}.event_date BETWEEN toDate({q(qr.time.start.isoformat())}) AND toDate({q(qr.time.end.isoformat())})"]
    if qr.events:
        where.append(f"{tcol}.event IN ({', '.join(q(x) for x in qr.events)})")
    if qr.filters:
        where.append(group_sql(qr.filters, ctx))
    warnings: list[str] = []
    rows_filters: list[str] = []
    masked: dict[str, str] = {}
    if decision is not None:
        from .policy import inject, mask_sql
        rows_filters, msql, masked, warnings_p = inject(decision, qr, cat, ctx, msql)
        where += rows_filters; warnings += warnings_p
        for i, g in enumerate(qr.group_by):                      # 分組維度本身被遮罩（例：one_id 尾碼）→ 分組值先遮罩再聚合
            if f"g{i + 1}" in masked:
                sel[(1 if bucket else 0) + i] = f"{mask_sql(masked[f'g{i + 1}'], ctx.expr(g), g.type)} AS g{i + 1}"
    # mau：同月份去重用戶數，按天查詢時每天都顯示當月值
    ctes: list[str] = []
    joins = ""
    for m in qr.metrics:
        if m.agg == "mau":
            cond = ctx.mcond(m)
            gcols = ", ".join(f"{ctx.expr(g)} AS {c}" for g, c in zip(qr.group_by, cols[1:]))
            ctes.append(f"mau_{m.alias} AS (SELECT toStartOfMonth(e.event_date) AS mon{', ' + gcols if gcols else ''}, uniqExactIf(e.one_id_uint, {cond}) AS v FROM dmp.events AS e WHERE {' AND '.join(where)} GROUP BY ALL)")
            on = " AND ".join([f"mau_{m.alias}.mon = toStartOfMonth(base.bucket)"] + [f"mau_{m.alias}.{c} = base.{c}" for c in cols[1:]])
            joins += f" LEFT JOIN mau_{m.alias} ON {on}"
    sample = f" SAMPLE {qr.sample}" if qr.sample and layer == "L0" else ""
    cold_join = ""
    if ctx.cold:
        cold_cols = ", ".join(f"`{n}`" for n in ctx.cold)
        cold_join = f" LEFT JOIN (SELECT one_id, {cold_cols} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id"
    msel = [f"{s} AS {m.alias}" for m, s in zip(qr.metrics, msql)]
    base = (f"SELECT {', '.join(sel + msel + [f'{kexpr} AS _k'])} FROM {src}{sample}{cold_join} WHERE {' AND '.join(where)}"
            + (f" GROUP BY {', '.join(grp)}" if grp else ""))
    ctes.append(f"base AS ({base})")
    # 最終輸出：mau 取 CTE 值；K 匿名；公式；top_n；compare
    k = int(getattr(decision, "k_anonymity", 0) or 0)
    outm: list[str] = []
    for m in qr.metrics:
        v = f"mau_{m.alias}.v" if m.agg == "mau" else f"base.{m.alias}"
        if k: v = f"if(base._k < {k}, NULL, {v})"
        outm.append(f"{v} AS {m.alias}")
    fsel = [f"{formula_sql(f)} AS {f.alias}" for f in qr.formulas]
    out_cols = cols + [m.alias for m in qr.metrics] + [f.alias for f in qr.formulas] + ["_k"]
    if k:
        out_cols.append("suppressed")
    final_sel = [f"base.{c}" for c in cols] + outm
    inner = f"SELECT {', '.join(final_sel)}, base._k AS _k FROM base{joins}"
    if qr.top_n and cols[1 if bucket else 0:]:
        gcols_out = cols[1 if bucket else 0:]
        first = qr.metrics[0]
        tcond = ctx.mcond(first)
        rank = f"countIf({tcond})" if layer == "L0" else f"countMergeIf(a.cnt, {tcond})"
        if first.agg == "users": rank = f"uniqExactIf(e.one_id_uint, {tcond})"
        if first.agg == "sum" and first.field: rank = f"sumIf({ctx.expr(first.field)}, {tcond})" if layer == "L0" else f"sumMergeIf(a.amount, {tcond})"
        gsel = ", ".join(f"{ctx.expr(g)} AS {c}" for g, c in zip(qr.group_by, gcols_out))
        ctes.insert(0, f"top AS (SELECT {gsel}, {rank} AS r FROM {src}{sample}{cold_join} WHERE {' AND '.join(where)} GROUP BY {', '.join(gcols_out)} ORDER BY r DESC, {', '.join(gcols_out)} LIMIT {qr.top_n})")
        inner += f" WHERE ({', '.join('base.' + c for c in gcols_out)}) IN (SELECT {', '.join(gcols_out)} FROM top)"
    outer_cols = cols + [m.alias for m in qr.metrics]
    sql = f"SELECT {', '.join(outer_cols + fsel + ['_k'] + (['_k < ' + str(k) + ' AS suppressed'] if k else []))} FROM ({inner})"
    if qr.compare == "previous_period":
        days = (qr.time.end - qr.time.start).days + 1
        prev = Query(project=qr.project, metrics=qr.metrics, time=type(qr.time)(qr.time.start - timedelta(days=days), qr.time.end - timedelta(days=days), qr.time.grain, qr.time.now),
                     formulas=qr.formulas, filters=qr.filters, group_by=qr.group_by, top_n=None, compare=None, sample=qr.sample, exact=qr.exact)
        pc = compile_events(prev, cat, decision, profile)
        shift = f"bucket + INTERVAL {days} DAY" if qr.time.grain != "month" else f"bucket + INTERVAL {_months(qr.time.start, qr.time.end)} MONTH"
        vals = [m.alias for m in qr.metrics] + [f.alias for f in qr.formulas]
        join_on = " AND ".join([f"cur.{c} = prev.{c}" for c in cols[1 if bucket else 0:]] + (["cur.bucket = prev.bucket"] if bucket else []))
        prev_sel = ", ".join([f"{shift} AS bucket" if c == "bucket" else c for c in cols] + [f"{v} AS {v}_prev" for v in vals])
        psql = f"SELECT {prev_sel} FROM ({pc.sql})"
        sql = (f"SELECT {', '.join(['cur.' + c + ' AS ' + c for c in out_cols] + ['prev.' + v + '_prev AS ' + v + '_prev' for v in vals])} FROM ({sql}) AS cur"
               f" LEFT JOIN ({psql}) AS prev ON {join_on or '1 = 1'}")
        out_cols = out_cols + [v + "_prev" for v in vals]
        warnings += [w for w in pc.warnings if w not in warnings]
    sql = ("WITH " + ", ".join(ctes) + " " if ctes else "") + sql
    sql += " ORDER BY " + ", ".join(cols) if cols else ""
    lineage = [("dmp.events_daily_agg" if layer == "L1" else "dmp.events")] + (["dmp.users_dict"] if any(f.kind == "user_hot" for f in qr.walk_fields()) else []) \
        + (["dmp.users"] if ctx.cold else []) + (["dmp.segment_members"] if any(f.kind == "segment" for f in qr.walk_fields()) else []) \
        + [f"metric:{qr.metric_name}" if qr.metric_name else f"event:{e}" for e in (qr.events or ["any"])]
    from .policy import budget
    settings = budget(profile)
    return Compiled(sql=sql, layer=layer, columns=out_cols, settings=settings, lineage=sorted(set(lineage)), warnings=warnings + ([f"plan L0 because {', '.join(why)}"] if why else []),
                    k_anonymity=k, masked=masked, row_filters=rows_filters)


def _months(a: date, b: date) -> int:
    return max(1, (b.year - a.year) * 12 + b.month - a.month + 1)
