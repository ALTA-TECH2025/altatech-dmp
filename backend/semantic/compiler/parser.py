"""語意查詢（dict，= openapi SemanticQuery 的超集）→ AST。只做結構與目錄檢查，不產 SQL。

查詢形狀（事件分析）：
  metric:   "dau"                                   # 語意層指標名（metrics.yaml）…或
  metrics:  [{event, agg, field?, filters?, alias?}] # 原型「事件分析」的多指標；agg 見 AGGS
  formulas: [{alias, expr}]                          # 指標別名間的四則運算，例 "m1 / m2 * 100"
  filters:  {op: and|or, items: [...]} 或 條件陣列（= and）；條件 = {field, op, value, type?}
  group_by: ["platform", {field: "props.market", type: string}]；top_n: N（依第一個指標取前 N 個分組值）
  time:     {from, to, grain: minute|hour|day|week|month|total, now?}   # 台北日期，含兩端
  compare:  previous_period；sample: 0.1（按用戶抽樣）；exact: true（不走 MV）；estimate: true（快速估算：人數可走 MV 近似）
  behaviors: [UsersBehavior]（用戶做過／未做過，與用戶列表同一套）＋ behaviors_op；cohorts: [{name, filters?, segment?, behaviors?}]（對照組 ≤10，結果多一欄 cohort）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .catalog import Catalog, FieldRef

AGGS = {"total", "users", "avg_per_user", "uniq", "sum", "avg", "avg_val", "max", "min", "p25", "p50", "p75", "p90", "mau"}
NUM_AGGS = {"sum", "avg", "avg_val", "max", "min", "p25", "p50", "p75", "p90"}
OPS = {
    "string": {"eq", "neq", "in", "nin", "like", "nlike", "set", "nset"},
    "number": {"eq", "neq", "gt", "gte", "lt", "lte", "between", "set", "nset"},
    "bool": {"true", "false"},
    "date": {"rel", "between", "set", "nset"},
    "segment": {"in", "nin"},
}
GRAINS = ("minute", "hour", "day", "week", "month", "total")


@dataclass
class Cond:
    field: FieldRef
    op: str
    value: Any = None


@dataclass
class Group:
    op: str                      # and | or
    items: list[Cond | Group] = field(default_factory=list)


@dataclass
class Metric:
    alias: str
    agg: str
    event: str | None            # None = 任一事件
    field: FieldRef | None = None
    filters: Group | None = None
    label: str = ""


@dataclass
class Formula:
    alias: str
    expr: str                    # 已驗證只含 別名 數字 + - * / ( )
    tokens: list[str] = field(default_factory=list)


@dataclass
class TimeRange:
    start: date
    end: date
    grain: str = "day"
    now: date | None = None


@dataclass
class Query:
    project: str
    metrics: list[Metric]
    time: TimeRange
    formulas: list[Formula] = field(default_factory=list)
    filters: Group | None = None
    group_by: list[FieldRef] = field(default_factory=list)
    top_n: int | None = None
    compare: str | None = None
    sample: float | None = None
    exact: bool = False
    estimate: bool = False                  # 快速估算：人數可走 MV（uniqMerge 近似）
    metric_name: str | None = None          # 來自 metrics.yaml 的指標名（血緣用）
    behaviors: list[Any] = field(default_factory=list)   # [([Behavior], and|or)]：行為篩選組（全局一組；對照組再加一組），組間 AND
    cohorts: list[Any] = field(default_factory=list)     # [scope.Cohort]

    @property
    def events(self) -> list[str]:
        return sorted({m.event for m in self.metrics if m.event})

    def walk_fields(self, metric_fields: bool = True) -> list[FieldRef]:
        out: list[FieldRef] = list(self.group_by)

        def rec(g: Group | None) -> None:
            if not g: return
            for it in g.items:
                if isinstance(it, Group): rec(it)
                else: out.append(it.field)
        rec(self.filters)
        for m in self.metrics:
            rec(m.filters)
            if m.field and metric_fields: out.append(m.field)
        return out


def _date(s: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise ValueError(f"time.{what} 必須是 YYYY-MM-DD：{s}") from None


def parse_filters(cat: Catalog, raw: Any) -> Group | None:
    if raw in (None, [], {}):
        return None
    if isinstance(raw, list):
        raw = {"op": "and", "items": raw}
    if "items" in raw:
        op = str(raw.get("op", "and")).lower()
        if op not in ("and", "or"):
            raise ValueError(f"filters.op 只能是 and／or：{op}")
        items = [parse_filters(cat, it) for it in raw["items"]]
        return Group(op, [it for it in items if it is not None])
    key = raw.get("field") or raw.get("dimension")
    if not key:
        raise ValueError(f"條件缺 field：{raw}")
    f = cat.resolve(key, raw.get("type"))
    op = str(raw.get("op", "eq"))
    if op not in OPS[f.type]:
        raise ValueError(f"欄位 {key}（{f.type}）不支援運算子 {op}；可用 {sorted(OPS[f.type])}")
    v = raw.get("value")
    if op in ("in", "nin") and not isinstance(v, list | tuple):
        v = [v]
    if op == "between" and not (isinstance(v, list | tuple) and len(v) == 2):
        raise ValueError(f"between 需要兩個值：{key}")
    if op in ("eq", "neq", "gt", "gte", "lt", "lte", "like", "nlike") and v is None:
        raise ValueError(f"條件 {key} {op} 缺 value")
    if f.type == "number" and op in ("eq", "neq", "gt", "gte", "lt", "lte"):
        v = float(v)
    if f.type == "number" and op == "between":
        v = [float(v[0]), float(v[1])]
    if op == "rel" and not (isinstance(v, dict) and isinstance(v.get("days"), int)):
        raise ValueError(f"rel 需要 {{days: n}}：{key}")
    return Cond(f, op, v)


def _metric(cat: Catalog, raw: dict[str, Any], i: int) -> Metric:
    agg = str(raw.get("agg", "total"))
    if agg not in AGGS:
        raise ValueError(f"未知聚合 {agg}；可用 {sorted(AGGS)}")
    ev = raw.get("event")
    event: str | None = None
    if ev not in (None, "", "any", "$Anything"):
        event = cat.canonical_event(str(ev))
        if not event:
            raise ValueError(f"事件 {ev} 不在事件字典（含別名）；請先在元事件管理登記")
    fref = None
    if agg in NUM_AGGS or agg == "uniq":
        if not raw.get("field"):
            raise ValueError(f"聚合 {agg} 需要 field")
        fref = cat.resolve(str(raw["field"]), raw.get("type"))
        if agg in NUM_AGGS and fref.type != "number":
            raise ValueError(f"聚合 {agg} 的欄位必須是數值：{raw['field']} 是 {fref.type}")
        if fref.kind == "segment":
            raise ValueError("分群不能當聚合欄位")
    alias = str(raw.get("alias") or f"m{i + 1}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError(f"指標別名只能是英數底線：{alias}")
    return Metric(alias, agg, event, fref, parse_filters(cat, raw.get("filters")), str(raw.get("label") or ""))


def _from_semantic_metric(cat: Catalog, name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """metrics.yaml 的指標 → 事件分析的 metrics／formulas。漏斗／留存／LTV 型指標屬其他樣板。"""
    m = cat.metrics.get(name)
    if not m:
        raise ValueError(f"指標 {name} 不在 metrics.yaml")
    t = m["type"]
    def one(d: dict[str, Any], alias: str) -> dict[str, Any]:
        agg = {"uniq_users": "users", "count": "total", "sum": "sum"}.get(d["type"])
        if not agg:
            raise ValueError(f"指標 {name} 的型別 {d['type']} 不屬事件分析樣板（漏斗／留存／LTV 另有樣板）")
        out: dict[str, Any] = {"event": d.get("event", "any"), "agg": agg, "alias": alias}
        if agg == "sum": out["field"] = d["field"]
        if d.get("filter"): out["filters"] = [_sql_filter_to_cond(d["filter"])]
        return out
    if t == "ratio":
        return [one(m["numerator"], "num"), one(m["denominator"], "den")], [{"alias": name, "expr": "num / den"}]
    return [one(m, name)], []


def _sql_filter_to_cond(s: str) -> dict[str, Any]:
    """metrics.yaml 裡的簡單 SQL 片段（col IN ('a','b') / col = 'a'）→ 條件；只支援這兩種，其他請改寫成結構化 filter。"""
    mm = re.fullmatch(r"\s*(\w+)\s+IN\s*\(([^)]*)\)\s*", s, re.I)
    if mm:
        return {"field": mm.group(1), "op": "in", "value": [x.strip().strip("'") for x in mm.group(2).split(",")]}
    mm = re.fullmatch(r"\s*(\w+)\s*=\s*'([^']*)'\s*", s)
    if mm:
        return {"field": mm.group(1), "op": "eq", "value": mm.group(2)}
    raise ValueError(f"metrics.yaml 的 filter 無法解析：{s}")


def parse_formula(raw: dict[str, Any], aliases: set[str]) -> Formula:
    alias = str(raw.get("alias") or "")
    expr = str(raw.get("expr") or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias) or alias in aliases:
        raise ValueError(f"公式別名不合法或與指標重複：{alias}")
    toks = re.findall(r"\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*|[-+*/()]", expr)
    if "".join(toks) != re.sub(r"\s+", "", expr):
        raise ValueError(f"公式只能含指標別名、數字與 + - * / ( )：{expr}")
    for t in toks:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t) and t not in aliases:
            raise ValueError(f"公式引用了不存在的指標別名 {t}")
    return Formula(alias, expr, toks)


def flag(raw: dict[str, Any], key: str) -> bool:
    """布林開關：只收 true／false（表單或查詢字串來的 "true"／"false"／"1"／"0" 也收），其他值拒絕，避免 "false" 被當成真。"""
    v = raw.get(key)
    if v is None or isinstance(v, bool): return bool(v)
    if isinstance(v, int) and v in (0, 1): return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "1", "false", "0", ""): return v.strip().lower() in ("true", "1")
    raise ValueError(f"{key} 只能是 true／false")


def parse(raw: dict[str, Any], cat: Catalog | None = None) -> Query:
    cat = cat or Catalog()
    if not isinstance(raw, dict):
        raise ValueError("查詢必須是物件")
    tm = raw.get("time") or {}
    if not tm.get("from") or not tm.get("to"):
        raise ValueError("time.from／time.to 必填")
    start, end = _date(tm["from"], "from"), _date(tm["to"], "to")
    if end < start:
        raise ValueError("time.to 早於 time.from")
    grain = str(tm.get("grain", "day"))
    if grain not in GRAINS:
        raise ValueError(f"grain 只能是 {GRAINS}")
    now = _date(tm["now"], "now") if tm.get("now") else None
    mets_raw = list(raw.get("metrics") or [])
    forms_raw = list(raw.get("formulas") or [])
    metric_name = None
    if raw.get("metric"):
        metric_name = str(raw["metric"])
        m2, f2 = _from_semantic_metric(cat, metric_name)
        mets_raw = m2 + mets_raw; forms_raw = f2 + forms_raw
    if not mets_raw:
        raise ValueError("至少一個指標（metric 或 metrics）")
    if len(mets_raw) > 10:
        raise ValueError("指標最多 10 個")
    metrics = [_metric(cat, m, i) for i, m in enumerate(mets_raw)]
    aliases = [m.alias for m in metrics]
    if len(set(aliases)) != len(aliases):
        raise ValueError(f"指標別名重複：{aliases}")
    if any(m.agg == "mau" for m in metrics) and grain != "day":
        raise ValueError("mau（當月觸發用戶數）只能按天查詢")
    formulas: list[Formula] = []
    for f in forms_raw:
        formulas.append(parse_formula(f, set(aliases) | {x.alias for x in formulas}))
    gb: list[FieldRef] = []
    for g in raw.get("group_by") or []:
        g = g if isinstance(g, dict) else {"field": g}
        ref = cat.resolve(str(g["field"]), g.get("type"))
        if ref.kind == "segment":
            raise ValueError("分群不能作分組")
        if ref.name == "one_id":
            raise ValueError("不能按 one_id 分組（個人層級請走用戶列表／細查）")
        gb.append(ref)
    if len(gb) > 3:
        raise ValueError("分組最多 3 維")
    top_n = raw.get("top_n")
    if top_n is not None:
        top_n = int(top_n)
        if top_n < 1 or not gb:
            raise ValueError("top_n 需要 ≥ 1 且有分組")
    compare = raw.get("compare")
    if compare not in (None, "", "previous_period"):
        raise ValueError("compare 目前只支援 previous_period")
    sample = raw.get("sample")
    if sample is not None:
        sample = float(sample)
        if not 0 < sample < 1:
            raise ValueError("sample 必須在 (0,1)")
    q = Query(project=str(raw.get("project") or "altatech_dmp"), metrics=metrics, time=TimeRange(start, end, grain, now),
              formulas=formulas, filters=parse_filters(cat, raw.get("filters")), group_by=gb, top_n=top_n,
              compare=compare or None, sample=sample, exact=flag(raw, "exact"), estimate=flag(raw, "estimate"), metric_name=metric_name)
    if raw.get("segment"):
        seg = Cond(cat.resolve(f"seg.{raw['segment']}"), "in", None)
        q.filters = Group("and", [q.filters, seg]) if q.filters else Group("and", [seg])
    if raw.get("behaviors") or raw.get("cohorts"):
        from .scope import parse_behavior_group, parse_cohorts  # 延遲載入：scope → users → parser
        bg = parse_behavior_group(cat, raw, now, q.time)
        q.behaviors = [bg] if bg else []
        q.cohorts = parse_cohorts(cat, raw.get("cohorts"), now, q.time)
    return q
