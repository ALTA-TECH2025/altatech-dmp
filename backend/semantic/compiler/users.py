"""用戶列表與用戶細查樣板（docs/04 §5；openapi UsersQuery／UsersListRequest／Profile／TimelineResult／IdentityGraph）。

人數預估：每個條件各自產生一個 Bitmap（groupBitmapState(one_id_uint)），交／併／差後 bitmapCardinality。
  WITH (SELECT groupBitmapState(one_id_uint) FROM dmp.users AS e FINAL WHERE <用戶屬性／分群／行級>) AS bm_u,
       (SELECT groupBitmapState(one_id_uint) FROM (SELECT e.one_id_uint FROM dmp.events AS e WHERE <事件／期間／屬性／行級> GROUP BY e.one_id_uint HAVING count() >= n)) AS bm_b1,
       (SELECT groupBitmapState(cityHash64(one_id)) FROM (<漏斗 base／留存 u／事件分析落點>)) AS bm_s,
       bitmapAnd(bitmapAndnot(bm_u, bm_b1), bm_s) AS bm
  SELECT bitmapCardinality(bm) AS users
列表：同一個 bm，FROM dmp.users AS e FINAL WHERE bitmapContains(bm, e.one_id_uint) AND (排序鍵, one_id_uint) > 游標 ORDER BY … LIMIT 每頁；
  游標 = base64(JSON{v, u, n, t})，n = 已瀏覽筆數（上限 100,000，之後走匯出審批）。
口徑：母體是用戶寬表（dmp.users）；只有事件沒有用戶列的人不在列表裡。做過 = 期間內次數符合 count；未做過 = 期間內 0 次。
  下鑽：漏斗某步到達 = max(level) ≥ step；流失 = max(level) = step−1；留存 = u CTE 的模型條件（period 代入期數）；事件分析落點 = 該桶該分組值的事件 → 用戶。
欄位遮罩：one_id 與用戶屬性依 Decision.field_policies（mask_tail／bucket／round；hide 略過並 warning）。
細查（profile／timeline／identities）另需 reason，由 API 層用 authorize(module="profile") 檢查；這裡只編譯。
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .catalog import TZ, Catalog, FieldRef
from .events import Compiled, Ctx, bucket_sql, group_sql, q
from .parser import Cond, Group, Query, TimeRange, parse, parse_filters
from .policy import budget, inject, mask_sql

MAX_PAGE = 500
MAX_BROWSE = 100_000
DEFAULT_COLUMNS = ["branch_id", "lifecycle", "tier", "aum_bucket", "churn_score", "last_active_at"]
MEASURE_AGGS = ("count", "sum", "avg", "max", "min", "days")
WINDOW_UNITS = {"minute": 60, "hour": 3600, "day": 86400}
IMPORT_ID = re.compile(r"^im_[0-9a-f]{10}$")
COUNT_OPS = ("gte", "lte", "eq", "gt", "lt", "between")
PROFILE_SUMMARY_DAYS = 30
TIMELINE_COLS = ["time", "event", "platform", "channel", "symbol", "side", "amount", "entity_type", "entity_id", "session_id", "event_id", "props"]
IDENTITY_COLS = ["id_type_a", "id_a", "id_type_b", "id_b", "source", "evidence_event_id", "first_seen", "last_seen", "seen_count", "confidence", "status"]
HIDDEN_ID_TYPES = re.compile(r"mobile|email|tw_id|id_no", re.I)      # 身分圖裡若出現這類 ID 類型（不該進倉）一律整個隱藏


@dataclass
class Behavior:
    event: str
    filters: Group | None
    done: bool
    count_op: str                 # 比較運算：gte | lte | eq | gt | lt | between
    count: float | list[float]    # 比較值
    time: TimeRange
    agg: str = "count"            # 指標：count | sum | avg | max | min | days
    field: Any = None             # sum／avg／max／min 的數值欄位（FieldRef）
    last_days: int | None = None  # 原始「近 N 天」（摘要與重算用；TimeRange 已依 now 展開）


@dataclass
class Sequence:
    steps: list[tuple[str, Group | None]]   # [(事件, 該步事件屬性條件)]，2–6 步
    window_s: int                           # 從第一步起算的完成窗口（秒）
    done: bool
    time: TimeRange
    last_days: int | None = None


@dataclass
class Source:
    type: str                      # funnel | retention | events | path | attribution | import
    query: Any                     # FunnelQuery | RetentionQuery | Query
    step: int | None = None
    reached: bool = True
    bucket: str | None = None
    group: Any = None              # funnel／retention：g1 值；events：[g1, g2, …]
    cohort: str | None = None
    period: int | None = None
    retained: bool | None = None
    metric: str | None = None
    import_id: str | None = None   # import：導入名單 id


@dataclass
class UsersQuery:
    project: str
    filters: Group | None
    behaviors: list[Behavior]
    behaviors_op: str
    source: Source | None
    now: date | None
    raw: dict[str, Any] = field(default_factory=dict)
    combine: str = "and"          # 用戶屬性塊與行為塊之間：and | or
    sequences: list[Sequence] = field(default_factory=list)   # 行為序列，與 behaviors 同受 behaviors_op 組合
    window: tuple[date, date] | None = None   # 只取 updated_at 落在這段（台北日，含兩端）的用戶：資料輸出的 once 期間／daily 分區

    def as_query(self) -> Query:
        d = self.now or date.today()
        return Query(project=self.project, metrics=[], time=TimeRange(d, d, "day", self.now), filters=self.filters)


@dataclass
class ListSpec:
    columns: list[str]
    sort_field: str | None
    sort_dir: str
    page_size: int
    cursor: dict[str, Any] | None


# ---------------------------------------------------------------- 解析
def _walk(g: Group | None) -> list[Cond]:
    out: list[Cond] = []
    if g is None: return out
    for it in g.items:
        out += _walk(it) if isinstance(it, Group) else [it]
    return out


def _time(raw: dict[str, Any] | None, now: date | None, what: str) -> TimeRange | None:
    raw = raw or {}
    if raw.get("now"):
        now = date.fromisoformat(str(raw["now"])[:10])
    if raw.get("last_days") is not None:                                   # 近 N 天：以基準日（now；例行計算時為 basetime）往前，含基準日
        n = int(raw["last_days"])
        if not 1 <= n <= 730:
            raise ValueError(f"{what}.time.last_days 需在 1–730")
        base = now or date.today()
        return TimeRange(base - timedelta(days=n - 1), base, "day", base)
    if not raw.get("from") or not raw.get("to"):
        return None
    try:
        s, e = date.fromisoformat(str(raw["from"])[:10]), date.fromisoformat(str(raw["to"])[:10])
    except ValueError:
        raise ValueError(f"{what}.time 必須是 YYYY-MM-DD") from None
    if e < s:
        raise ValueError(f"{what}.time.to 早於 from")
    return TimeRange(s, e, "day", now)


def parse_behavior(cat: Catalog, b: dict[str, Any], i: int, now: date | None, default_time: TimeRange | None, what: str = "behaviors") -> Behavior:
    """一條「做過／未做過」：事件 × 指標 × 比較 × 期間 × 事件屬性條件。用戶列表、分群、事件分析／漏斗／留存的行為篩選同一套。"""
    name = cat.canonical_event(str(b.get("event") or ""))
    if not name:
        raise ValueError(f"事件 {b.get('event')} 不在事件字典")
    meas = b.get("measure") or {}; cmp_ = b.get("cmp") or b.get("count") or {}
    agg = str(meas.get("agg") or "count")
    if agg not in MEASURE_AGGS:
        raise ValueError(f"measure.agg 只能是 {MEASURE_AGGS}")
    fref = None
    if agg in ("sum", "avg", "max", "min"):
        if not meas.get("field"):
            raise ValueError(f"{what}[{i}] measure.{agg} 需要 field（數值事件屬性）")
        fref = cat.resolve(str(meas["field"]), "number")
        if fref.type != "number":
            raise ValueError(f"{what}[{i}] measure.field {meas['field']} 不是數值欄位")
    op = str(cmp_.get("op") or "gte")
    if op not in COUNT_OPS:
        raise ValueError(f"cmp.op 只能是 {COUNT_OPS}")
    v = cmp_.get("value", 1)
    if op == "between":
        if not (isinstance(v, list | tuple) and len(v) == 2):
            raise ValueError("cmp.between 需要 [a, b]")
        val: float | list[float] = [float(v[0]), float(v[1])]
    else:
        val = float(v)
        if agg in ("count", "days") and val < 1 and op not in ("lte", "lt"):
            raise ValueError("做過的次數至少 1；未做過請用 done: false")
    t = _time(b.get("time"), now, f"{what}[{i}]") or default_time
    if t is None:
        raise ValueError(f"{what}[{i}] 需要 time（近 N 天或自訂期間；或在頂層 time 給預設）")
    return Behavior(name, parse_filters(cat, b.get("filters")), bool(b.get("done", True)), op, val, t, agg, fref, (b.get("time") or {}).get("last_days"))


def parse_behaviors(cat: Catalog, raw: Any, now: date | None, default_time: TimeRange | None, what: str = "behaviors") -> list[Behavior]:
    out: list[Behavior] = []
    for i, b in enumerate(raw or []):
        if i >= 10:
            raise ValueError(f"{what} 最多 10 條")
        out.append(parse_behavior(cat, b, i, now, default_time, what))
    return out


def parse_users(raw: dict[str, Any], cat: Catalog | None = None) -> UsersQuery:
    cat = cat or Catalog()
    project = str(raw.get("project") or "altatech_dmp")
    tm = raw.get("time") or {}
    now = date.fromisoformat(str(tm["now"])[:10]) if tm.get("now") else None
    default_time = _time(tm, now, "time")
    filters = parse_filters(cat, raw.get("filters"))
    if raw.get("segment"):
        c = Cond(cat.resolve(f"seg.{raw['segment']}"), "in", None)
        filters = Group("and", [filters, c]) if filters else Group("and", [c])
    for c in _walk(filters):
        if c.field.kind not in ("user_hot", "user_cold", "segment"):
            raise ValueError(f"用戶列表的條件只能是用戶屬性（user.<key>）或分群（seg.<id>）：{c.field.key}；行為條件請用 behaviors")
    behaviors = parse_behaviors(cat, raw.get("behaviors"), now, default_time, "behaviors")
    sequences: list[Sequence] = []
    for i, sq in enumerate(raw.get("sequences") or []):
        if i >= 5:
            raise ValueError("sequences 最多 5 條")
        steps_raw = sq.get("steps") or []
        if not 2 <= len(steps_raw) <= 6:
            raise ValueError(f"sequences[{i}] 需要 2–6 步")
        steps: list[tuple[str, Group | None]] = []
        for j, st in enumerate(steps_raw):
            name = cat.canonical_event(str(st.get("event") or ""))
            if not name:
                raise ValueError(f"sequences[{i}].steps[{j}] 事件 {st.get('event')} 不在事件字典")
            steps.append((name, parse_filters(cat, st.get("filters"))))
        w = sq.get("window") or {}
        unit = str(w.get("unit") or "day")
        if unit not in WINDOW_UNITS:
            raise ValueError(f"sequences[{i}].window.unit 只能是 {tuple(WINDOW_UNITS)}")
        try:
            wv = int(w.get("value") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"sequences[{i}].window.value 需為整數") from None
        window_s = wv * WINDOW_UNITS[unit]
        if not 60 <= window_s <= 90 * 86400:
            raise ValueError(f"sequences[{i}] 窗口需在 1 分鐘～90 天")
        t = _time(sq.get("time"), now, f"sequences[{i}]") or default_time
        if t is None:
            raise ValueError(f"sequences[{i}] 需要 time（近 N 天或自訂期間；或在頂層 time 給預設）")
        sequences.append(Sequence(steps, window_s, bool(sq.get("done", True)), t, (sq.get("time") or {}).get("last_days")))
    bop = str(raw.get("behaviors_op") or "and")
    if bop not in ("and", "or"):
        raise ValueError("behaviors_op 只能是 and／or")
    combine = str(raw.get("combine") or "and")
    if combine not in ("and", "or"):
        raise ValueError("combine 只能是 and／or")
    source = _parse_source(raw.get("source"), project, cat) if raw.get("source") else None
    if not (filters or behaviors or sequences or source):
        raise ValueError("至少要有一個條件（filters／segment／behaviors／sequences／source）")
    return UsersQuery(project, filters, behaviors, bop, source, now, raw, combine, sequences)


def _parse_source(raw: dict[str, Any], project: str, cat: Catalog) -> Source:
    t = str(raw.get("type") or "")
    if t == "import":
        iid = str(raw.get("import_id") or "")
        if not IMPORT_ID.match(iid):
            raise ValueError("source.import_id 格式不符（im_ 加 10 碼）")
        return Source("import", None, import_id=iid)
    sq = dict(raw.get("query") or {}); sq["project"] = project
    if t == "funnel":
        from .funnel import parse_funnel
        fq = parse_funnel(sq, cat)
        step = int(raw.get("step") or 0)
        if not 1 <= step <= len(fq.steps):
            raise ValueError(f"step 需在 1–{len(fq.steps)}")
        reached = bool(raw.get("reached", True))
        if not reached and step < 2:
            raise ValueError("流失（reached=false）需 step ≥ 2")
        if raw.get("bucket") is not None and not fq.trend:
            raise ValueError("漏斗沒有 trend，不能指定 bucket")
        if raw.get("group") is not None and not fq.group_by:
            raise ValueError("漏斗沒有 group_by，不能指定 group")
        return Source("funnel", fq, step=step, reached=reached, bucket=raw.get("bucket"), group=raw.get("group"))
    if t == "retention":
        from .retention import parse_retention
        rq = parse_retention(sq, cat)
        cohort = raw.get("cohort")
        if not cohort:
            raise ValueError("retention 下鑽需要 cohort")
        date.fromisoformat(str(cohort)[:10])
        period = raw.get("period")
        retained = raw.get("retained", True)
        if retained is not None:
            if period is None or not rq.lo <= int(period) <= rq.periods:
                raise ValueError(f"period 需在 {rq.lo}–{rq.periods}")
        if raw.get("group") is not None and not rq.group_by:
            raise ValueError("留存沒有 group_by，不能指定 group")
        return Source("retention", rq, cohort=str(cohort)[:10], period=None if period is None else int(period), retained=None if retained is None else bool(retained), group=raw.get("group"))
    if t == "attribution":
        from .attribution import parse_attribution
        aq = parse_attribution(sq, cat)
        touch = raw.get("touch")
        if not touch or touch not in {x.alias for x in aq.touches}:
            raise ValueError("attribution 下鑽需要 touch（觸點別名）")
        if raw.get("group") is not None and not aq.split:
            raise ValueError("歸因沒有 split，不能指定 group")
        return Source("attribution", aq, group=str(touch), bucket=None if raw.get("group") is None else str(raw["group"]))
    if t == "path":
        from .path import parse_path
        pq = parse_path(sq, cat)
        node = raw.get("node"); level = raw.get("level")
        if not node or level is None or not 0 <= int(level) <= pq.steps:
            raise ValueError(f"path 下鑽需要 node 與 level（0–{pq.steps}）")
        return Source("path", pq, group=str(node), period=int(level))
    if t == "events":
        qr = parse(sq, cat)
        alias = raw.get("metric") or qr.metrics[0].alias
        m = next((x for x in qr.metrics if x.alias == alias), None)
        if m is None:
            raise ValueError(f"指標 {alias} 不在查詢裡")
        if m.agg == "mau":
            raise ValueError("mau 指標不支援下鑽（請用 users 指標按月查）")
        if raw.get("bucket") is not None and qr.time.grain == "total":
            raise ValueError("合計粒度沒有 bucket")
        g = raw.get("group")
        if g is not None and (not isinstance(g, list) or len(g) != len(qr.group_by)):
            raise ValueError(f"group 需是長度 {len(qr.group_by)} 的陣列（對應 group_by）")
        return Source("events", qr, bucket=raw.get("bucket"), group=g, metric=m.alias)
    raise ValueError("source.type 只能是 funnel／retention／events／path／attribution")


def parse_list(raw: dict[str, Any], cat: Catalog | None = None) -> ListSpec:
    cat = cat or Catalog()
    cols: list[str] = []
    for c in raw.get("columns") or DEFAULT_COLUMNS:
        c = str(c)
        if c == "one_id" or c in cols: continue
        _stored(cat, c)
        cols.append(c)
    sort = raw.get("sort") or {}
    sf = sort.get("field")
    if sf is not None:
        sf = str(sf)
        if sf != "one_id": _stored(cat, sf)
        else: sf = None
    sd = str(sort.get("dir") or "asc")
    if sd not in ("asc", "desc"):
        raise ValueError("sort.dir 只能是 asc／desc")
    ps = int(raw.get("page_size") or 50)
    if not 1 <= ps <= MAX_PAGE:
        raise ValueError(f"page_size 需在 1–{MAX_PAGE}")
    cur = decode_cursor(raw.get("cursor")) if raw.get("cursor") else None
    if cur is not None and int(cur.get("n", 0)) >= MAX_BROWSE:
        raise ValueError(f"已達瀏覽上限 {MAX_BROWSE:,} 筆；更多請走匯出（審批）")
    return ListSpec(cols, sf, sd, ps, cur)


def _stored(cat: Catalog, prop: str) -> dict[str, Any]:
    if prop in cat.forbidden_l4:
        raise ValueError(f"用戶屬性 {prop} 為 L4 個資，不存在於分析倉")
    p = cat.user_props.get(prop)
    if not p:
        raise ValueError(f"用戶屬性 {prop} 未在 user_properties.yaml 登記")
    if prop not in cat.stored_props:
        raise ValueError(f"用戶屬性 {prop} 為 L3，不在用戶寬表，不能顯示")
    return p


def encode_cursor(d: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(s: str) -> dict[str, Any]:
    try:
        d = json.loads(base64.urlsafe_b64decode(str(s) + "=" * (-len(s) % 4)).decode())
        assert isinstance(d, dict) and "u" in d
        return d
    except Exception:
        raise ValueError("cursor 無效") from None


# ---------------------------------------------------------------- SQL
class UserCtx:
    """用戶寬表（FROM dmp.users AS e）下的欄位表達式：屬性即實體欄位（時間型取台北日期，與 catalog 一致）；分群走 segment_members（cond_sql 內建）。"""
    def __init__(self, cat: Catalog, now: date | None):
        self.cat, self.now = cat, now

    def expr(self, f: FieldRef) -> str:
        if f.kind in ("user_hot", "user_cold"):
            return f"toDate(e.`{f.name}`, '{TZ}')" if _ptype(self.cat, f.name) == "datetime" else f"e.`{f.name}`"
        if f.kind == "segment":
            return "e.one_id_uint"            # cond_sql 對分群只用 e.one_id_uint IN (…)
        raise ValueError(f"用戶列表條件不能用 {f.key}")

    def now_sql(self) -> str:
        return f"toDate({q(self.now.isoformat())})" if self.now else f"toDate(now(), '{TZ}')"


def _ptype(cat: Catalog, prop: str) -> str:
    return cat.user_props[prop]["type"]


def user_row_filters(decision: Any) -> list[str]:
    """policy 的行級條件套到用戶寬表：branch_id → e.branch_id；event_date（回溯）只對事件有意義，略過；agent 範圍寬表沒有欄位 → 拒絕。"""
    out: list[str] = []
    for f in (getattr(decision, "row_filters", []) or []) if decision is not None else []:
        if "event_date" in f: continue
        if "agent_id" in f:
            raise PermissionError("營業員範圍的身分不能用用戶列表（用戶寬表沒有 agent_id）")
        out.append(re.sub(r"\bbranch_id\b", "e.branch_id", f))
    return out


def _lit(v: Any, t: str) -> str:
    if t == "number": return repr(float(v))
    if t == "bool": return "1" if v in (True, 1, "1", "true") else "0"
    if t == "date": return f"toDate({q(str(v)[:10])})"
    return q(v)


def _eq(expr: str, v: Any, t: str) -> str:
    return f"{expr} IS NULL" if v is None else f"{expr} = {_lit(v, t)}"


def _int_lit(x: Any) -> str: return str(int(x))
def _float_lit(x: Any) -> str: return repr(float(x))


def behavior_select_sql(project: str, b: Behavior, cat: Catalog, decision: Any) -> tuple[str, list[str], list[str], list[str]]:
    """一條行為 → 「符合的用戶」子查詢（SELECT one_id_uint … GROUP BY … HAVING）；未做過回「有做過的人」（呼叫端用 NOT IN／bitmapAndnot）。
    回 (sql, lineage, warnings, row_filters)。用戶列表的 Bitmap 與事件分析／漏斗／留存的行為篩選共用。"""
    qr = Query(project=project, metrics=[], time=b.time, filters=b.filters)
    ctx = Ctx(cat, qr, "L0")
    where = [f"e.project = {q(project)}", f"e.event_date BETWEEN toDate({q(b.time.start.isoformat())}) AND toDate({q(b.time.end.isoformat())})", f"e.event = {q(b.event)}"]
    if b.filters:
        where.append(group_sql(b.filters, ctx))
    warnings: list[str] = []; row_filters: list[str] = []
    if decision is not None:
        row_filters, _, _, warnings = inject(decision, qr, cat, ctx, [])
        where += row_filters
    mexpr = {"count": "count()", "days": "uniqExact(e.event_date)"}.get(b.agg) or f"{b.agg}({ctx.expr(b.field)})"
    lit = _int_lit if b.agg in ("count", "days") else _float_lit
    if not b.done:
        having = "count() >= 1"
    elif b.count_op == "between":
        having = f"{mexpr} BETWEEN {lit(b.count[0])} AND {lit(b.count[1])}"       # type: ignore[index]
    else:
        having = f"{mexpr} {dict(gte='>=', lte='<=', eq='=', gt='>', lt='<')[b.count_op]} {lit(b.count)}"   # type: ignore[arg-type]
    if b.field is not None and b.field.kind == "user_cold":
        ctx.cold[b.field.name] = b.field
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sql = f"SELECT e.one_id_uint AS one_id_uint FROM dmp.events AS e{cold} WHERE {' AND '.join(where)} GROUP BY e.one_id_uint HAVING {having}"
    lineage = ["dmp.events", f"event:{b.event}"] + (["dmp.users_dict"] if any(c.field.kind == "user_hot" for c in _walk(b.filters)) else []) + (["dmp.users"] if ctx.cold else [])
    return sql, lineage, warnings, row_filters


def _bitmaps(uq: UsersQuery, cat: Catalog, decision: Any) -> tuple[list[str], str, list[str], list[str], list[str]]:
    """回 (WITH 子句清單, 最終 bitmap 表達式名, lineage, warnings, row_filters)。"""
    if decision is not None and not getattr(decision, "allowed", True):
        raise PermissionError(getattr(decision, "reason", "denied"))
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    for c in _walk(uq.filters):
        if fp.get(c.field.name, "plain") == "hide":
            raise PermissionError(f"欄位 {c.field.key} 對此角色不可見")
    uctx = UserCtx(cat, uq.now)
    uwhere = ["1"] + ([group_sql(uq.filters, uctx)] if uq.filters else []) + user_row_filters(decision)
    if uq.window:
        w0, w1 = uq.window
        uwhere.append(f"e.updated_at >= toDateTime64({q(w0.isoformat() + ' 00:00:00')}, 3, 'Asia/Taipei') AND e.updated_at < toDateTime64({q((w1 + timedelta(days=1)).isoformat() + ' 00:00:00')}, 3, 'Asia/Taipei')")
    ctes = [f"(SELECT groupBitmapState(one_id_uint) FROM dmp.users AS e FINAL WHERE {' AND '.join(uwhere)}) AS bm_u"]
    has_beh = bool(uq.behaviors or uq.sequences)
    if uq.combine == "or" and uq.filters and has_beh:                       # 「屬性 或 行為」：行為那邊要對全體（含行級範圍）算，不能先被屬性框住
        ctes.append(f"(SELECT groupBitmapState(one_id_uint) FROM dmp.users AS e FINAL WHERE {' AND '.join(['1'] + user_row_filters(decision))}) AS bm_all")
    lineage = ["dmp.users"] + (["dmp.segment_members"] if any(c.field.kind == "segment" for c in _walk(uq.filters)) else [])
    warnings: list[str] = []; row_filters: list[str] = []
    parts: list[tuple[str, bool]] = []
    for i, b in enumerate(uq.behaviors, 1):
        sub, lin, w, rf = behavior_select_sql(uq.project, b, cat, decision)
        warnings += w; row_filters = rf or row_filters
        ctes.append(f"(SELECT groupBitmapState(one_id_uint) FROM ({sub})) AS bm_b{i}")
        parts.append((f"bm_b{i}", b.done))
        lineage += lin
    for i, sq in enumerate(uq.sequences, 1):                                # 行為序列：依序完成各步（windowFunnel 預設模式，窗口從第一步起算）
        qr = Query(project=uq.project, metrics=[], time=sq.time, filters=None)
        ctx = Ctx(cat, qr, "L0")
        evs = sorted({ev for ev, _ in sq.steps})
        where = [f"e.project = {q(uq.project)}", f"e.event_date BETWEEN toDate({q(sq.time.start.isoformat())}) AND toDate({q(sq.time.end.isoformat())})",
                 f"e.event IN ({', '.join(q(ev) for ev in evs)})"]
        if decision is not None:
            rf, _, _, w = inject(decision, qr, cat, ctx, [])
            where += rf; warnings += w; row_filters = rf
        conds = [f"(e.event = {q(ev)} AND {group_sql(fl, ctx)})" if fl else f"e.event = {q(ev)}" for ev, fl in sq.steps]
        cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
        ctes.append(f"(SELECT groupBitmapState(one_id_uint) FROM (SELECT e.one_id_uint AS one_id_uint FROM dmp.events AS e{cold} WHERE {' AND '.join(where)} "
                    f"GROUP BY e.one_id_uint HAVING windowFunnel({sq.window_s})(toDateTime(e.time), {', '.join(conds)}) >= {len(sq.steps)})) AS bm_q{i}")
        parts.append((f"bm_q{i}", sq.done))
        lineage += ["dmp.events"] + [f"event:{ev}" for ev in evs] + (["dmp.users_dict"] if any(c.field.kind == "user_hot" for _, fl in sq.steps for c in _walk(fl)) else [])
    base = "bm_all" if (uq.combine == "or" and uq.filters and has_beh) else "bm_u"
    acc = base
    if parts:
        if uq.behaviors_op == "and":
            for name, done in parts:
                acc = f"{'bitmapAnd' if done else 'bitmapAndnot'}({acc}, {name})"
        else:
            terms = [name if done else f"bitmapAndnot({base}, {name})" for name, done in parts]
            u = terms[0]
            for t in terms[1:]:
                u = f"bitmapOr({u}, {t})"
            acc = f"bitmapAnd({base}, {u})"
        if base == "bm_all":
            acc = f"bitmapOr(bm_u, {acc})"                                   # 屬性 或 行為
    if uq.source:
        sub, lin, w, rf = _source_sql(uq.source, cat, decision)
        ctes.append(f"(SELECT groupBitmapState(cityHash64(one_id)) FROM ({sub})) AS bm_s")
        acc = f"bitmapAnd({acc}, bm_s)"; lineage += lin; warnings += w; row_filters = row_filters or rf
    ctes.append(f"{acc} AS bm")
    return ctes, "bm", sorted(set(lineage)), warnings, row_filters


def _source_sql(s: Source, cat: Catalog, decision: Any) -> tuple[str, list[str], list[str], list[str]]:
    """下鑽來源 → 回 one_id 的子查詢。"""
    if s.type == "import":                                                  # 導入名單：只存 one_id；行級範圍由 bm_u（用戶寬表）那邊套用
        return f"SELECT one_id FROM dmp.segment_imports WHERE import_id = {q(s.import_id)}", ["dmp.segment_imports"], [], []
    if s.type == "funnel":
        from .funnel import funnel_base
        fq = s.query; fb = funnel_base(fq, cat, decision)
        conds = ["1"]
        if s.bucket is not None: conds.append(f"bucket = toDate({q(s.bucket)})")
        if s.group is not None: conds.append(_eq("g1", s.group, fq.group_by.type))
        having = f"max(level) >= {s.step}" if s.reached else f"max(level) = {s.step - 1}"       # type: ignore[operator]
        sub = f"SELECT one_id FROM ({fb.sql}) WHERE {' AND '.join(conds)} GROUP BY one_id HAVING {having}"
        return sub, ["dmp.events"] + [f"event:{st.event}" for st in fq.steps], fb.warnings, fb.row_filters
    if s.type == "retention":
        from .retention import retention_parts
        rq = s.query; parts = retention_parts(rq, cat, decision)
        conds = [f"cohort = toDate({q(s.cohort)})"]
        if s.group is not None: conds.append(_eq("g1", s.group, rq.group_by.type))
        if s.retained is not None:
            mc = parts.model_cond(rq, str(s.period))
            conds.append(f"({mc})" if s.retained else f"NOT ({mc})")
        sub = f"WITH {parts.with_sql} SELECT one_id FROM u WHERE {' AND '.join(conds)}"
        return sub, ["dmp.events"] + [f"event:{b.event}" for b in (rq.initial, rq.ret) if b.event], parts.warnings, parts.row_filters
    if s.type == "attribution":
        from .attribution import attribution_parts
        aq = s.query; parts = attribution_parts(aq, cat, decision); ti = next(i for i, t in enumerate(aq.touches) if t.alias == s.group)
        cond = f"t.2 = {ti}" + (f" AND t.3 = {q(s.bucket)}" if s.bucket is not None else "")
        sub = f"WITH {parts.with_sql} SELECT DISTINCT one_id FROM seq ARRAY JOIN touches AS t WHERE {cond}"
        return sub, ["dmp.events", f"event:{aq.target}"], parts.warnings, parts.row_filters
    if s.type == "path":
        from .path import path_parts
        pq = s.query; parts = path_parts(pq, cat, decision)
        pos = f"{int(s.period) + 1}" if pq.direction == "after" else f"length(path) - {pq.steps - int(s.period)}"     # before：從右對齊（終點 = 第 steps 層）
        sub = f"WITH {parts.with_sql} SELECT DISTINCT one_id FROM r WHERE path[{pos}] = {q(s.group)}"
        return sub, ["dmp.events", f"event:{pq.anchor_event}"], parts.warnings, parts.row_filters
    qr: Query = s.query
    ctx = Ctx(cat, qr, "L0")
    m = next(x for x in qr.metrics if x.alias == s.metric)
    where = [f"e.project = {q(qr.project)}", f"e.event_date BETWEEN toDate({q(qr.time.start.isoformat())}) AND toDate({q(qr.time.end.isoformat())})"]
    if qr.filters: where.append(group_sql(qr.filters, ctx))
    where.append(f"({ctx.mcond(m)})")
    if m.field: where.append(f"{ctx.expr(m.field)} IS NOT NULL")
    masked: dict[str, str] = {}; warnings: list[str] = []; rf: list[str] = []
    if decision is not None:
        rf, _, masked, warnings = inject(decision, qr, cat, ctx, [])
        where += rf
    if s.bucket is not None:
        b = bucket_sql(qr.time.grain, ctx)
        lit = f"toDateTime({q(s.bucket)}, '{TZ}')" if qr.time.grain in ("minute", "hour") else f"toDate({q(s.bucket)})"
        where.append(f"{b} = {lit}")
    if s.group is not None:
        for i, (g, v) in enumerate(zip(qr.group_by, s.group)):
            expr = ctx.expr(g); name = f"g{i + 1}"
            if name in masked: expr = mask_sql(masked[name], expr, g.type); where.append(_eq(expr, v, "string"))
            else: where.append(_eq(expr, v, g.type))
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sub = f"SELECT DISTINCT e.one_id AS one_id FROM dmp.events AS e{cold} WHERE {' AND '.join(where)}"
    return sub, ["dmp.events"] + [f"event:{e}" for e in (qr.events or ["any"])], warnings, rf


def compile_estimate(uq: UsersQuery, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    ctes, bm, lineage, warnings, rf = _bitmaps(uq, cat, decision)
    sql = f"WITH {', '.join(ctes)} SELECT bitmapCardinality({bm}) AS users"
    return Compiled(sql=sql, layer="L0", columns=["users"], settings=budget(profile), lineage=lineage, warnings=warnings, k_anonymity=int(getattr(decision, "k_anonymity", 0) or 0), masked={}, row_filters=rf)


def _sort_expr(cat: Catalog, prop: str | None) -> tuple[str, str]:
    """排序鍵表達式與其字面值型別（NULL 以哨兵值墊底，讓 keyset 元組可比較）。"""
    if prop is None:
        return "toUInt8(0)", "number"
    t = _ptype(cat, prop)
    if t == "number": return f"ifNull(toFloat64(e.`{prop}`), -1e308)", "number"
    if t == "date": return f"ifNull(e.`{prop}`, toDate('1970-01-01'))", "date"
    if t == "datetime": return f"ifNull(e.`{prop}`, toDateTime64('1970-01-01 00:00:00', 3, 'UTC'))", "datetime"
    if t == "bool": return f"toUInt8(e.`{prop}`)", "number"
    return f"e.`{prop}`", "string"


def _sort_lit(v: Any, t: str) -> str:
    if t == "number": return repr(float(v))
    if t == "date": return f"toDate({q(str(v)[:10])})"
    if t == "datetime": return f"toDateTime64({q(v)}, 3, 'UTC')"
    return q(v)


def _col_sql(cat: Catalog, fp: dict[str, str], prop: str) -> tuple[str, str]:
    """用戶屬性輸出表達式（依策略遮罩）→ (sql, policy)。"""
    pol = fp.get(prop, "plain")
    t = {"enum": "string", "string": "string", "number": "number", "bool": "bool", "date": "date", "datetime": "date"}[_ptype(cat, prop)]
    return (f"e.`{prop}`" if pol == "plain" else mask_sql(pol, f"e.`{prop}`", t)), pol


def compile_list(uq: UsersQuery, spec: ListSpec, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    ctes, bm, lineage, warnings, rf = _bitmaps(uq, cat, decision)
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    masked: dict[str, str] = {}
    sel = [f"{mask_sql(fp.get('one_id', 'plain'), 'e.one_id', 'string')} AS one_id"]; cols = ["one_id"]
    if fp.get("one_id", "plain") != "plain": masked["one_id"] = fp["one_id"]
    for c in spec.columns:
        expr, pol = _col_sql(cat, fp, c)
        if pol == "hide":
            warnings.append(f"欄位 {c} 對此角色不可見，已略過"); continue
        if pol != "plain": masked[c] = pol
        sel.append(f"{expr} AS `{c}`"); cols.append(c)
    if spec.sort_field and fp.get(spec.sort_field, "plain") == "hide":
        raise PermissionError(f"欄位 {spec.sort_field} 對此角色不可見，不能排序")
    sexpr, stype = _sort_expr(cat, spec.sort_field)
    sel += [f"{sexpr} AS _s", "e.one_id_uint AS _u"]; cols += ["_s", "_u"]
    where = [f"bitmapContains({bm}, e.one_id_uint)"]
    if spec.cursor:
        cmp = ">" if spec.sort_dir == "asc" else "<"
        where.append(f"(_s, _u) {cmp} ({_sort_lit(spec.cursor.get('v', 0), stype)}, {int(spec.cursor['u'])})")
    d = spec.sort_dir.upper()
    sql = f"WITH {', '.join(ctes)} SELECT {', '.join(sel)} FROM dmp.users AS e FINAL WHERE {' AND '.join(where)} ORDER BY _s {d}, _u {d} LIMIT {spec.page_size}"
    return Compiled(sql=sql, layer="L0", columns=cols, settings=budget(profile), lineage=lineage, warnings=warnings, k_anonymity=0, masked=masked, row_filters=rf)


def next_cursor(rows: list[dict[str, Any]], spec: ListSpec, total: int | None) -> str | None:
    """最後一列的 (_s, _u) → 下一頁游標；不足一頁即沒有下一頁。"""
    if len(rows) < spec.page_size:
        return None
    last = rows[-1]
    n = int((spec.cursor or {}).get("n", 0)) + len(rows)
    return encode_cursor({"v": last["_s"], "u": last["_u"], "n": n, "t": total if total is not None else (spec.cursor or {}).get("t")})


# ---------------------------------------------------------------- 細查
def compile_profile(one_id: str, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    masked: dict[str, str] = {}; warnings: list[str] = []
    sel = [f"{mask_sql(fp.get('one_id', 'plain'), 'e.one_id', 'string')} AS one_id"]; cols = ["one_id"]
    if fp.get("one_id", "plain") != "plain": masked["one_id"] = fp["one_id"]
    for c in sorted(cat.stored_props):
        expr, pol = _col_sql(cat, fp, c)
        if pol == "hide":
            warnings.append(f"欄位 {c} 對此角色不可見，已略過"); continue
        if pol != "plain": masked[c] = pol
        sel.append(f"{expr} AS `{c}`"); cols.append(c)
    where = [f"e.one_id = {q(one_id)}"] + user_row_filters(decision)
    sql = f"SELECT {', '.join(sel)} FROM dmp.users AS e FINAL WHERE {' AND '.join(where)} LIMIT 1"
    return Compiled(sql=sql, layer="L0", columns=cols, settings=budget(profile), lineage=["dmp.users"], warnings=warnings, masked=masked, row_filters=user_row_filters(decision))


def _event_scope(one_id: str, project: str, cat: Catalog, decision: Any, t: TimeRange) -> tuple[Ctx, list[str], list[str]]:
    qr = Query(project=project, metrics=[], time=t)
    ctx = Ctx(cat, qr, "L0")
    where = [f"e.project = {q(project)}", f"e.one_id = {q(one_id)}", f"e.event_date BETWEEN toDate({q(t.start.isoformat())}) AND toDate({q(t.end.isoformat())})"]
    rf: list[str] = []
    if decision is not None:
        rf, _, _, _ = inject(decision, qr, cat, ctx, [])
        where += rf
    return ctx, where, rf


def compile_summary(one_id: str, project: str, cat: Catalog | None = None, decision: Any = None, now: date | None = None, days: int = PROFILE_SUMMARY_DAYS, profile: str = "human") -> Compiled:
    cat = cat or Catalog(); end = now or date.today()
    _, where, rf = _event_scope(one_id, project, cat, decision, TimeRange(end - timedelta(days=days - 1), end, "day", now))
    sql = f"SELECT e.event AS event, count() AS n, min(e.time) AS first, max(e.time) AS last FROM dmp.events AS e WHERE {' AND '.join(where)} GROUP BY event ORDER BY n DESC, event"
    return Compiled(sql=sql, layer="L0", columns=["event", "n", "first", "last"], settings=budget(profile), lineage=["dmp.events"], row_filters=rf)


def compile_segments_of(one_id: str, profile: str = "human") -> Compiled:
    sql = f"SELECT DISTINCT segment_id FROM dmp.segment_members WHERE one_id = {q(one_id)} ORDER BY segment_id"
    return Compiled(sql=sql, layer="L0", columns=["segment_id"], settings=budget(profile), lineage=["dmp.segment_members"])


def compile_timeline(one_id: str, project: str, start: date, end: date, cat: Catalog | None = None, decision: Any = None, events: list[str] | None = None,
                     limit: int = 100, cursor: dict[str, Any] | None = None, now: date | None = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"limit 需在 1–{MAX_PAGE}")
    if end < start:
        raise ValueError("to 早於 from")
    _, where, rf = _event_scope(one_id, project, cat, decision, TimeRange(start, end, "day", now))
    names: list[str] = []
    for ev in events or []:
        n = cat.canonical_event(ev)
        if not n: raise ValueError(f"事件 {ev} 不在事件字典")
        names.append(n)
    if names:
        where.append(f"e.event IN ({', '.join(q(x) for x in sorted(set(names)))})")
    if cursor:
        where.append(f"(e.time, e.event_id) < (toDateTime64({q(cursor['t'])}, 3, 'UTC'), {q(cursor['e'])})")
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    masked: dict[str, str] = {}
    sel = []
    for c in TIMELINE_COLS:
        pol = fp.get(c, "plain")
        if c == "amount" and pol != "plain":
            sel.append(f"{mask_sql(pol, 'e.amount', 'number')} AS amount"); masked["amount"] = pol
        else:
            sel.append(f"e.{c} AS {c}")
    sql = f"SELECT {', '.join(sel)} FROM dmp.events AS e WHERE {' AND '.join(where)} ORDER BY e.time DESC, e.event_id DESC LIMIT {limit}"
    return Compiled(sql=sql, layer="L0", columns=list(TIMELINE_COLS), settings=budget(profile), lineage=["dmp.events"], masked=masked, row_filters=rf)


def timeline_cursor(rows: list[dict[str, Any]], limit: int, cursor: dict[str, Any] | None) -> str | None:
    if len(rows) < limit: return None
    last = rows[-1]
    return encode_cursor({"t": str(last["time"]), "e": str(last["event_id"]), "u": 0, "n": int((cursor or {}).get("n", 0)) + len(rows)})


def strip_props(props: Any, cat: Catalog, fp: dict[str, str]) -> dict[str, Any]:
    """事件 props：去掉 L4 名稱的鍵（永不該進倉；多一道保險）、$identity_* 與 hide 策略的欄位。"""
    if isinstance(props, str):
        try: props = json.loads(props)
        except ValueError: return {}
    if not isinstance(props, dict): return {}
    out = {}
    for k, v in props.items():
        base = str(k).lstrip("$")
        if base in cat.forbidden_l4 or str(k).startswith("$identity_") or fp.get(k, "plain") == "hide" or fp.get(base, "plain") == "hide":
            continue
        out[k] = v
    return out


def compile_identities(one_id: str, profile: str = "human") -> Compiled:
    sql = f"SELECT {', '.join(IDENTITY_COLS)} FROM dmp.id_edges FINAL WHERE one_id = {q(one_id)} ORDER BY first_seen, id_type_a, id_a, id_type_b, id_b"
    return Compiled(sql=sql, layer="L0", columns=list(IDENTITY_COLS), settings=budget(profile), lineage=["dmp.id_edges"])


def identity_graph(rows: list[dict[str, Any]], decision: Any = None) -> dict[str, Any]:
    """id_edges 列 → 節點／邊；ID 值依 one_id 的策略遮罩（admin 明文），不該出現的 ID 類型整個隱藏。"""
    from .policy import mask_value
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    pol = fp.get("one_id", "mask_tail(4)")
    nodes: dict[str, dict[str, Any]] = {}; edges = []
    def node(t: str, v: str) -> str:
        shown = "***" if HIDDEN_ID_TYPES.search(t) else mask_value(pol, v)
        key = f"{t}:{shown}"
        nodes.setdefault(key, {"id": key, "type": t, "value": shown})
        return key
    for r in rows:
        a = node(str(r["id_type_a"]), str(r["id_a"])); b = node(str(r["id_type_b"]), str(r["id_b"]))
        edges.append({"a": a, "b": b, **{k: r.get(k) for k in ("source", "evidence_event_id", "first_seen", "last_seen", "seen_count", "confidence", "status")}})
    return {"nodes": list(nodes.values()), "edges": edges, "masked": {"id": pol} if pol != "plain" else {}}


def compile_export(uq: UsersQuery, columns: list[str], cat: Catalog | None = None, decision: Any = None, max_rows: int = MAX_BROWSE, profile: str = "batch") -> Compiled:
    """匯出：同一套列表 SQL，一次取到上限（不分頁）；欄位策略由 authorize(action="export", context={"export": True}) 給（比 UI 嚴一級）。"""
    cat = cat or Catalog()
    cols: list[str] = []
    for c in columns or DEFAULT_COLUMNS:
        if c != "one_id" and c not in cols:
            _stored(cat, c); cols.append(c)
    return compile_list(uq, ListSpec(cols, None, "asc", int(max_rows), None), cat, decision, profile)
