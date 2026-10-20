"""歸因樣板（docs/04 §4、openapi AttributionQuery）。

口徑：每次轉化（目標事件，可帶條件與價值欄位）往前回溯 window_days 天，取該用戶的觸點序列（時間 < 轉化時間），依模型把 1 次轉化（與其價值）分給各觸點：
  first 第一個觸點 1；last 最後一個 1；linear 1/n；position U 型（n=1 → 1、n=2 → 各半、否則首末各 wf／wl，中間平分其餘）；
  decay 2^(-(t_conv - t_i)/半衰期) 正規化；custom 觸點自訂權重正規化。沒有觸點的轉化不分（計入 untouched）。
SQL 形狀：
  短窗口：conv（轉化）、tp（觸點）→ tpu 每人觸點陣列 → seq LEFT JOIN；長窗口（≥ SCAN_WINDOW_DAYS）：ev 一次掃描聚成轉化陣列與觸點陣列 → seq ARRAY JOIN。
  兩者 seq 後都 arrayFilter 回溯窗口 → w 各模型權重陣列
  → ARRAY JOIN 觸點與序號（無觸點的轉化補哨兵列）→ GROUP BY GROUPING SETS ((ti [, sp]), ())：各觸點的模型 sum(w)、sum(w × v)、_k 與全體 summary 一次算完。
K 匿名：被歸到該觸點的轉化用戶 < K → 該列各模型數值 NULL。觸點表（dmp.touchpoints）是之後的加速層；現在直接掃事件表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .catalog import Catalog, FieldRef
from .events import Compiled, Ctx, group_sql, q
from .parser import Cond, Group, Query, TimeRange, parse_filters

MODELS = ("first", "last", "linear", "position", "decay", "custom")


@dataclass
class Touch:
    event: str
    alias: str
    filters: Group | None
    weight: float


@dataclass
class AttributionQuery:
    project: str
    target: str
    target_filters: Group | None
    value: FieldRef | None
    touches: list[Touch]
    split: FieldRef | None
    models: list[str]
    w_first: float
    w_last: float
    half_life_days: float
    window_days: int
    filters: Group | None
    sample: float | None
    time: TimeRange
    raw: dict[str, Any] = field(default_factory=dict)

    def as_query(self) -> Query:
        return Query(project=self.project, metrics=[], time=self.time, filters=self.filters)

    def touch_start(self) -> date:
        return self.time.start - timedelta(days=self.window_days)


def parse_attribution(raw: dict[str, Any], cat: Catalog | None = None) -> AttributionQuery:
    cat = cat or Catalog()
    t = raw.get("target") or {}
    target = cat.canonical_event(str(t.get("event") or ""))
    if not target:
        raise ValueError(f"轉化事件 {t.get('event')} 不在事件字典")
    value = None
    if t.get("value_field"):
        value = cat.resolve(str(t["value_field"]), "number")
        if value.type != "number":
            raise ValueError("value_field 必須是數值欄位")
    touches: list[Touch] = []; seen: set[str] = set()
    for tr in raw.get("touches") or []:
        ev = cat.canonical_event(str(tr.get("event") or ""))
        if not ev:
            raise ValueError(f"觸點事件 {tr.get('event')} 不在事件字典")
        alias = str(tr.get("alias") or ev)
        if alias in seen:
            raise ValueError(f"觸點別名重複：{alias}")
        seen.add(alias)
        w = float(tr.get("weight", 1))
        if w < 0:
            raise ValueError("觸點權重不能為負")
        touches.append(Touch(ev, alias, parse_filters(cat, tr.get("filters")), w))
    if not 1 <= len(touches) <= 10:
        raise ValueError("觸點需 1–10 個")
    if target in {x.event for x in touches}:
        raise ValueError("轉化事件不能同時是觸點")
    split = None
    if raw.get("split"):
        split = cat.resolve(str(raw["split"]))
        if split.kind == "segment" or split.name == "one_id":
            raise ValueError("拆分欄位不能是分群或 one_id")
    models = [str(m) for m in (raw.get("models") or ["first", "last", "linear"])]
    bad = [m for m in models if m not in MODELS]
    if bad or not models or len(set(models)) != len(models):
        raise ValueError(f"models 只能是 {MODELS} 且不重複")
    pw = raw.get("position_weights") or {}
    wf, wl = float(pw.get("first", 0.4)), float(pw.get("last", 0.4))
    if wf < 0 or wl < 0 or wf + wl > 1:
        raise ValueError("position_weights 首末之和需 ≤ 1")
    hl = float(raw.get("half_life_days") or 7)
    if not 0.01 <= hl <= 365:
        raise ValueError("half_life_days 需在 0.01–365")
    window = int(raw.get("window_days") or 7)
    if not 1 <= window <= 90:
        raise ValueError("window_days 需在 1–90")
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
    return AttributionQuery(str(raw.get("project") or "altatech_dmp"), target, parse_filters(cat, t.get("filters")), value, touches, split, models, wf, wl, hl, window, filters, sample, TimeRange(start, end, "day", now), raw)


SCAN_WINDOW_DAYS = 30           # 回溯窗口 ≥ 這個天數改用一次掃描（docs/36 §3a 的量測）


@dataclass
class AttributionParts:
    with_sql: str              # ev（長窗口）或 conv, tp, tpu（短窗口）, seq, w
    ctx: Ctx
    warnings: list[str]
    row_filters: list[str]
    k: int


def attribution_parts(aq: AttributionQuery, cat: Catalog | None = None, decision: Any = None) -> AttributionParts:
    cat = cat or Catalog()
    ctx = Ctx(cat, aq.as_query(), "L0")
    common = [f"e.project = {q(aq.project)}"]
    if aq.filters:
        common.append(group_sql(aq.filters, ctx))
    warnings: list[str] = []; row_filters: list[str] = []
    if decision is not None:
        from .policy import inject
        row_filters, _, _, w = inject(decision, aq.as_query(), cat, ctx, [])
        common += row_filters; warnings += w
    k = int(getattr(decision, "k_anonymity", 0) or 0)
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sample = f" SAMPLE {aq.sample}" if aq.sample else ""
    src = f"dmp.events AS e{sample}{cold}"
    tcond = [f"e.event = {q(aq.target)}", f"e.event_date BETWEEN toDate({q(aq.time.start.isoformat())}) AND toDate({q(aq.time.end.isoformat())})"] + ([group_sql(aq.target_filters, ctx)] if aq.target_filters else [])
    vexpr = f"ifNull(toFloat64({ctx.expr(aq.value)}), 0)" if aq.value else "1"
    branches = ", ".join(f"e.event = {q(t.event)}{' AND ' + group_sql(t.filters, ctx) if t.filters else ''}, {i}" for i, t in enumerate(aq.touches))
    ti = f"multiIf({branches}, -1)"
    sp = f"toString(ifNull({ctx.expr(aq.split)}, ''))" if aq.split else "''"
    tstart = aq.touch_start().isoformat()
    tdates = f"e.event_date BETWEEN toDate({q(tstart)}) AND toDate({q(aq.time.end.isoformat())})"
    def window(ct: str, ts: str) -> str:
        return f"arraySort(x -> (x.1, x.2, x.3), arrayFilter(x -> x.1 < {ct} AND x.1 >= {ct} - toIntervalDay({aq.window_days}), {ts}))"
    if aq.window_days >= SCAN_WINDOW_DAYS:
        # 長窗口：一次掃描、不做 JOIN。目標與觸點事件一起讀，按用戶聚成轉化陣列 cs 與觸點陣列 ts（先用轉化事件篩出有轉化的人），再 ARRAY JOIN cs 逐次回溯。
        # JOIN 版的每人觸點陣列隨窗口放大，90 天在 1 億筆時要約 4 GB；但短窗口時 JOIN 版較快（一次掃描要連觸點列的 one_id、event_id 一起讀）。
        is_conv = " AND ".join(tcond)
        evs = sorted({aq.target} | {t.event for t in aq.touches})
        ev = (f"SELECT e.one_id_uint AS uid, any(e.one_id) AS one_id, "
              f"groupArrayIf((e.time, {ti}, {sp}), {ti} >= 0) AS ts, groupArrayIf((e.time, e.event_id, {vexpr}), {is_conv}) AS cs "
              f"FROM {src} WHERE {' AND '.join(common)} AND e.event IN ({', '.join(q(x) for x in evs)}) AND {tdates} "
              f"AND e.one_id_uint IN (SELECT e.one_id_uint FROM {src} WHERE {' AND '.join(common + tcond)}) "
              f"GROUP BY uid HAVING notEmpty(cs)")
        seq = f"SELECT one_id, c.1 AS ct, c.2 AS cid, c.3 AS v, {window('c.1', 'ts')} AS touches FROM ev ARRAY JOIN cs AS c"
        head = f"ev AS ({ev})"
    else:
        conv = f"SELECT e.one_id AS one_id, e.time AS ct, e.event_id AS cid, {vexpr} AS v FROM {src} WHERE {' AND '.join(common + tcond)}"
        tp = (f"SELECT e.one_id AS one_id, e.time AS tt, {ti} AS ti, {sp} AS sp FROM {src} WHERE {' AND '.join(common)} AND {tdates}"
              f" AND e.event IN ({', '.join(q(x) for x in sorted({t.event for t in aq.touches}))}) AND ti >= 0")
        tpu = "SELECT one_id, groupArray((tt, ti, sp)) AS ts FROM tp GROUP BY one_id"
        seq = f"SELECT c.one_id AS one_id, c.ct AS ct, c.cid AS cid, c.v AS v, {window('c.ct', 't.ts')} AS touches FROM conv AS c LEFT JOIN tpu AS t ON c.one_id = t.one_id"
        head = f"conv AS ({conv}), tp AS ({tp}), tpu AS ({tpu})"
    cw = "[" + ", ".join(repr(float(t.weight)) for t in aq.touches) + "]"
    w = (f"SELECT one_id, ct, cid, v, touches, length(touches) AS n, "
         f"arrayMap(x -> exp2(-(toUnixTimestamp64Milli(ct) - toUnixTimestamp64Milli(x.1)) / 1000 / {aq.half_life_days * 86400!r}), touches) AS raw_decay, "
         f"arrayMap(r -> r / arraySum(raw_decay), raw_decay) AS w_decay, "
         f"arrayMap(i -> if(n = 1, 1, if(n = 2, 0.5, if(i = 1, {aq.w_first!r}, if(i = n, {aq.w_last!r}, {1 - aq.w_first - aq.w_last!r} / (n - 2))))), arrayEnumerate(touches)) AS w_pos, "
         f"arrayMap(x -> {cw}[x.2 + 1], touches) AS raw_custom, arrayMap(r -> if(arraySum(raw_custom) = 0, 1 / n, r / arraySum(raw_custom)), raw_custom) AS w_custom FROM seq")
    return AttributionParts(f"{head}, seq AS ({seq}), w AS ({w})", ctx, warnings, row_filters, k)


WEIGHT = {"first": "if(i = 1, 1, 0)", "last": "if(i = n, 1, 0)", "linear": "1 / n", "position": "w_pos[i]", "decay": "w_decay[i]", "custom": "w_custom[i]"}


def compile_attribution(aq: AttributionQuery, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    parts = attribution_parts(aq, cat, decision); k = parts.k
    # 一次掃描：沒有觸點的轉化補一列哨兵（ti = -1），每次轉化恰有一列 i = 1；GROUPING SETS 同時算各觸點（ti[, sp]）與全體（()）。
    # 以前 summary 用 (SELECT … FROM seq) 純量子查詢，ClickHouse 會把 CTE 鏈（含兩次掃事件表）每個引用各算一遍，1 億筆時讀 4.5 倍資料。
    msel = []
    for m in aq.models:
        msel += [f"sumIf({WEIGHT[m]}, ti >= 0) AS {m}_conv", f"sumIf({WEIGHT[m]} * v, ti >= 0) AS {m}_val"]
    gcols = ["ti"] + (["sp"] if aq.split else [])
    arr = "if(n = 0, [(ct, -1, '')], touches)"
    inner = (f"SELECT grouping({', '.join(gcols)}) AS g, t.2 AS ti{', t.3 AS sp' if aq.split else ''}, {', '.join(msel)}, uniqExactIf(one_id, ti >= 0) AS _k, "
             f"countIf(i = 1) AS c_t, uniqExactIf(one_id, i = 1) AS cu_t, countIf(i = 1 AND n > 0) AS t_t, sumIf(v, i = 1) AS v_t, avgIf(n, i = 1 AND n > 0) AS a_t "
             f"FROM w ARRAY JOIN {arr} AS t, arrayEnumerate({arr}) AS i GROUP BY GROUPING SETS (({', '.join(gcols)}), ())")
    mid = (f"SELECT *, maxIf(c_t, g != 0) OVER () AS conversions, maxIf(cu_t, g != 0) OVER () AS converters, maxIf(t_t, g != 0) OVER () AS touched, "
           f"maxIf(v_t, g != 0) OVER () AS total_value, maxIf(a_t, g != 0) OVER () AS avg_touches FROM ({inner})")
    vals = [f"{m}_{x}" for m in aq.models for x in ("conv", "val")]
    from .policy import budget, mask_sql
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    vpol = fp.get(aq.value.name, "plain") if aq.value else "plain"                 # 價值欄位（金額）的遮罩策略：模型金額與 summary 的 total_value 同套（行銷只看區間）
    masked: dict[str, str] = {}
    def outv(c: str) -> str:
        e = f"if(_k < {k}, NULL, {c})" if k else c
        if vpol != "plain" and (c.endswith("_val") or c == "total_value"):
            masked[c] = vpol; e = mask_sql(vpol, e, "number")
        return f"{e} AS {c}"
    sel = gcols + [outv(c) for c in vals] + ["_k", "conversions", "converters", "touched", outv("total_value"), "avg_touches"] + ([f"_k < {k} AS suppressed"] if k else [])
    sql = f"WITH {parts.with_sql} SELECT {', '.join(sel)} FROM ({mid}) WHERE g = 0 AND ti >= 0 ORDER BY {', '.join(gcols)}"
    cols = gcols + vals + ["_k", "conversions", "converters", "touched", "total_value", "avg_touches"] + (["suppressed"] if k else [])
    fields = _fields(aq)
    lineage = ["dmp.events", f"event:{aq.target}"] + [f"event:{t.event}" for t in aq.touches] + (["dmp.users_dict"] if any(f.kind == "user_hot" for f in fields) else []) \
        + (["dmp.users"] if parts.ctx.cold else []) + (["dmp.segment_members"] if any(f.kind == "segment" for f in fields) else [])
    warnings = list(parts.warnings)
    if aq.sample: warnings.append(f"抽樣 {aq.sample}（按用戶分桶）")
    if masked: warnings.append(f"價值欄位 {aq.value.key if aq.value else ''} 對此身分只給區間：模型金額與總價值為區間標籤")
    return Compiled(sql=sql, layer="L0", columns=cols, settings=budget(profile), lineage=sorted(set(lineage)), warnings=warnings, k_anonymity=k, masked=masked, row_filters=parts.row_filters)


def _fields(aq: AttributionQuery) -> list[FieldRef]:
    out: list[FieldRef] = ([aq.value] if aq.value else []) + ([aq.split] if aq.split else [])
    def rec(x: Any) -> None:
        if x is None: return
        for it in x.items:
            if isinstance(it, Group): rec(it)
            else: out.append(it.field)
    for g in [aq.filters, aq.target_filters] + [t.filters for t in aq.touches]: rec(g)
    return out


def shape(rows: list[dict[str, Any]], aq: AttributionQuery) -> dict[str, Any]:
    """寬列 → API 回應：每觸點（× 拆分）一列，models 下各模型 conversions／value／share；summary 來自純量欄。"""
    first = rows[0] if rows else {}
    conv_total = int(first.get("conversions") or 0)
    out = []
    for r in rows:
        sup = bool(r.get("suppressed"))
        t = aq.touches[int(r["ti"])]
        models = {}
        for m in aq.models:
            c = r.get(f"{m}_conv"); v = r.get(f"{m}_val")
            models[m] = {"conversions": None if sup or c is None else float(c), "value": None if sup or v is None else (v if isinstance(v, str) else float(v)), "share": None if sup or c is None or not conv_total else round(float(c) / conv_total, 4)}   # 區間化時 value 是標籤字串
        out.append({"touch": t.alias, "event": t.event, "split": r.get("sp") if aq.split else None, "models": models, "users": None if sup else int(r["_k"]), "suppressed": sup})
    summary = {"conversions": conv_total, "converters": int(first.get("converters") or 0), "touched": int(first.get("touched") or 0), "untouched": conv_total - int(first.get("touched") or 0),
               "value": first["total_value"] if isinstance(first.get("total_value"), str) else float(first.get("total_value") or 0), "avg_touches": None if first.get("avg_touches") is None else float(first["avg_touches"])}
    return {"rows": out, "summary": summary, "models": list(aq.models)}
