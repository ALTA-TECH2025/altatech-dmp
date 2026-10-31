"""行為路徑樣板（docs/04 §3、openapi PathQuery）。

口徑：節點 = 事件字典裡的事件（可依屬性拆成 event:值；可只保留指定事件、其餘折成「其他」）；會話 = 依間隔切（gap）／session_id／跨會話整段；
  每會話最多 max_events 個事件；連續重複節點可合併；會話結束補「離開」節點（$AppEnd 事件或會話逾時都成為「離開」）。
  同神策：每會話只切一條。起點（after）取會話中第一次出現 → 「起點 + N 步」；終點（before）取最後一次出現 → 「N 步 + 終點」。
  桑基節點／連線的數值 = 會話數（各路徑 sessions 加總，每會話一條路徑所以不重複）；每條路徑另給去重用戶數（K 匿名與下鑽用）。
SQL 形狀：
  ev  每筆事件 → (one_id, time, node, is_anchor)
  p/q 依間隔切會話：lagInFrame + 累計 sum（window function）
  s   每會話排序後的序列（arraySort by (time, event_id)，截到 max_events）
  m   合併重複；x 補「離開」
  r   arrayFirstIndex／arrayLastIndex 找起點（終點）位置 → arraySlice → path 陣列 + 耗時（每會話至多一列）
  SELECT path, uniqExact(one_id) AS users, count() AS sessions, avg(dur) AS avg_ms, (SELECT uniqExact(one_id) FROM r) AS anchor_users, (SELECT count() FROM r) AS anchor_sessions … ORDER BY users DESC, sessions DESC, path LIMIT top_n
K 匿名：users < K 的路徑 users／sessions／avg_ms 為 NULL。抽樣：SAMPLE（按用戶分桶），預設由 API 層給 0.1。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .catalog import Catalog, FieldRef
from .events import Compiled, Ctx, group_sql, q
from .parser import Group, Query, TimeRange, parse_filters

EXIT = "離開"
OTHER = "其他"
SESSION_MODES = ("gap", "id", "cross")


@dataclass
class PathQuery:
    project: str
    anchor: str                                   # 節點名（事件或 event:值）
    anchor_filters: Group | None
    direction: str
    steps: int
    session_mode: str
    gap_minutes: int
    max_events: int
    node_events: list[str]
    collapse_other: bool
    splits: list[tuple[str, FieldRef]]
    merge_repeats: bool
    exit_node: bool
    filters: Group | None
    sample: float | None
    top_n: int
    time: TimeRange
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def anchor_event(self) -> str:
        return self.anchor.split(":", 1)[0]

    def as_query(self) -> Query:
        return Query(project=self.project, metrics=[], time=self.time, filters=self.filters)


def parse_path(raw: dict[str, Any], cat: Catalog | None = None) -> PathQuery:
    cat = cat or Catalog()
    a = raw.get("anchor") or {}
    anchor = str(a.get("event") or "")
    if not anchor:
        raise ValueError("anchor.event 必填（起點或終點）")
    if anchor != EXIT:
        ev, _, val = anchor.partition(":")
        name = cat.canonical_event(ev)
        if not name:
            raise ValueError(f"事件 {ev} 不在事件字典")
        anchor = f"{name}:{val}" if val else name
    direction = str(raw.get("direction") or "after")
    if direction not in ("after", "before"):
        raise ValueError("direction 只能是 after／before")
    steps = int(raw.get("steps") or 5)
    if not 1 <= steps <= 10:
        raise ValueError("steps 需在 1–10")
    sess = raw.get("session") or {}
    mode = str(sess.get("mode") or "gap")
    if mode not in SESSION_MODES:
        raise ValueError(f"session.mode 只能是 {SESSION_MODES}")
    gap = int(sess.get("gap_minutes") or 30)
    if not 1 <= gap <= 24 * 60:
        raise ValueError("session.gap_minutes 需在 1–1440")
    max_events = int(sess.get("max_events") or 200)
    if not 2 <= max_events <= 1000:
        raise ValueError("session.max_events 需在 2–1000")
    nodes = raw.get("nodes") or {}
    node_events: list[str] = []
    for ev in nodes.get("events") or []:
        n = cat.canonical_event(str(ev))
        if not n:
            raise ValueError(f"事件 {ev} 不在事件字典")
        node_events.append(n)
    splits: list[tuple[str, FieldRef]] = []
    for sp in nodes.get("split") or []:
        n = cat.canonical_event(str(sp.get("event") or ""))
        if not n:
            raise ValueError(f"split 事件 {sp.get('event')} 不在事件字典")
        f = cat.resolve(str(sp.get("field") or ""), sp.get("type"))
        if f.kind == "segment" or f.name == "one_id":
            raise ValueError("拆分欄位不能是分群或 one_id")
        splits.append((n, f))
    if len(splits) > 5:
        raise ValueError("最多拆分 5 個事件")
    top_n = int(raw.get("top_n") or 100)
    if not 1 <= top_n <= 1000:
        raise ValueError("top_n 需在 1–1000")
    tm = raw.get("time") or {}
    try:
        start, end = date.fromisoformat(str(tm["from"])[:10]), date.fromisoformat(str(tm["to"])[:10])
    except (KeyError, ValueError):
        raise ValueError("time.from／time.to 必填且為 YYYY-MM-DD") from None
    if end < start:
        raise ValueError("time.to 早於 time.from")
    if (end - start).days > 92:
        raise ValueError("路徑分析時間範圍最長 92 天")
    now = date.fromisoformat(str(tm["now"])[:10]) if tm.get("now") else None
    filters = parse_filters(cat, raw.get("filters"))
    if raw.get("segment"):
        from .parser import Cond
        c = Cond(cat.resolve(f"seg.{raw['segment']}"), "in", None)
        filters = Group("and", [filters, c]) if filters else Group("and", [c])
    sample = raw.get("sample")
    if sample is not None:
        sample = float(sample)
        if not 0 < sample <= 1:
            raise ValueError("sample 必須在 (0,1]")
        if sample == 1: sample = None
    return PathQuery(str(raw.get("project") or "altatech_dmp"), anchor, parse_filters(cat, a.get("filters")), direction, steps, mode, gap, max_events, node_events,
                     bool(nodes.get("collapse_other")), splits, bool(nodes.get("merge_repeats", True)), bool(nodes.get("exit_node", True)),
                     filters, sample, top_n, TimeRange(start, end, "day", now), raw)


@dataclass
class PathParts:
    with_sql: str              # "ev AS (…), …, r AS (…)"
    ctx: Ctx
    warnings: list[str]
    row_filters: list[str]
    k: int


def node_expr(pq: PathQuery, ctx: Ctx) -> str:
    branches = []
    for ev, f in pq.splits:
        branches.append(f"e.event = {q(ev)}, concat({q(ev + ':')}, toString(ifNull({ctx.expr(f)}, '')))")
    if pq.exit_node:
        branches.append(f"e.event = '$AppEnd', {q(EXIT)}")
    expr = f"multiIf({', '.join(branches)}, e.event)" if branches else "e.event"
    if pq.node_events and pq.collapse_other:
        keep = sorted(set(pq.node_events) | {pq.anchor_event} | ({"$AppEnd"} if pq.exit_node else set()))
        expr = f"if(e.event IN ({', '.join(q(x) for x in keep)}), {expr}, {q(OTHER)})"
    return expr


def path_parts(pq: PathQuery, cat: Catalog | None = None, decision: Any = None) -> PathParts:
    cat = cat or Catalog()
    ctx = Ctx(cat, pq.as_query(), "L0")
    node = node_expr(pq, ctx)
    where = [f"e.project = {q(pq.project)}", f"e.event_date BETWEEN toDate({q(pq.time.start.isoformat())}) AND toDate({q(pq.time.end.isoformat())})"]
    if pq.node_events and not pq.collapse_other:
        keep = sorted(set(pq.node_events) | {pq.anchor_event} | ({"$AppEnd"} if pq.exit_node else set()))
        where.append(f"e.event IN ({', '.join(q(x) for x in keep)})")
    if pq.filters:
        where.append(group_sql(pq.filters, ctx))
    warnings: list[str] = []; row_filters: list[str] = []
    if decision is not None:
        from .policy import inject
        row_filters, _, _, w = inject(decision, pq.as_query(), cat, ctx, [])
        where += row_filters; warnings += w
    k = int(getattr(decision, "k_anonymity", 0) or 0)
    is_anchor = f"node = {q(pq.anchor)}" + (f" AND {group_sql(pq.anchor_filters, ctx)}" if pq.anchor_filters else "")
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sample = f" SAMPLE {pq.sample}" if pq.sample else ""
    ev = (f"SELECT e.one_id AS one_id, e.time AS time, e.event_id AS event_id, e.session_id AS session_id, {node} AS node, ({is_anchor}) AS is_anchor "
          f"FROM dmp.events AS e{sample}{cold} WHERE {' AND '.join(where)}")
    ctes = [f"ev AS ({ev})"]
    if pq.session_mode == "gap":
        ctes.append(f"p AS (SELECT *, dateDiff('second', lagInFrame(time, 1, time) OVER (PARTITION BY one_id ORDER BY time, event_id), time) > {pq.gap_minutes * 60} AS brk FROM ev)")
        ctes.append("sq AS (SELECT one_id, time, event_id, node, is_anchor, sum(brk) OVER (PARTITION BY one_id ORDER BY time, event_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS sid FROM p)")
    elif pq.session_mode == "id":
        ctes.append("sq AS (SELECT one_id, time, event_id, node, is_anchor, session_id AS sid FROM ev)")
    else:
        ctes.append("sq AS (SELECT one_id, time, event_id, node, is_anchor, 0 AS sid FROM ev)")
    ctes.append(f"s AS (SELECT one_id, sid, arraySlice(arraySort(x -> (x.1, x.4), groupArray((time, node, is_anchor, event_id))), 1, {pq.max_events}) AS seq FROM sq GROUP BY one_id, sid)")
    seq = "arrayFilter((x, i) -> i = 1 OR x.2 != seq[i - 1].2, seq, arrayEnumerate(seq))" if pq.merge_repeats else "seq"
    ctes.append(f"m AS (SELECT one_id, sid, {seq} AS seq2 FROM s)")
    if pq.exit_node:
        exit_t = f"(seq2[-1].1, {q(EXIT)}, {1 if pq.anchor == EXIT else 0}, seq2[-1].4)"
        ctes.append(f"x AS (SELECT one_id, sid, if(seq2[-1].2 = {q(EXIT)}, seq2, arrayPushBack(seq2, {exit_t})) AS seq3 FROM m)")
    else:
        ctes.append("x AS (SELECT one_id, sid, seq2 AS seq3 FROM m)")
    sl = f"arraySlice(seq3, idx, {pq.steps + 1})" if pq.direction == "after" else f"arraySlice(seq3, greatest(1, idx - {pq.steps}), idx - greatest(1, idx - {pq.steps}) + 1)"
    pick = "arrayFirstIndex" if pq.direction == "after" else "arrayLastIndex"      # 同神策：起點取會話第一次、終點取最後一次
    ctes.append(f"r AS (SELECT one_id, sid, arrayMap(y -> y.2, sl) AS path, dateDiff('millisecond', sl[1].1, sl[-1].1) AS dur FROM (SELECT one_id, sid, {sl} AS sl FROM (SELECT one_id, sid, seq3, {pick}(y -> y.3 = 1, seq3) AS idx FROM x) WHERE idx > 0))")
    return PathParts(", ".join(ctes), ctx, warnings, row_filters, k)


def compile_path(pq: PathQuery, cat: Catalog | None = None, decision: Any = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    parts = path_parts(pq, cat, decision); k = parts.k
    inner = "SELECT path, uniqExact(one_id) AS _k, count() AS _n, avg(dur) AS _ms, (SELECT uniqExact(one_id) FROM r) AS anchor_users, (SELECT count() FROM r) AS anchor_sessions FROM r GROUP BY path"
    # 外層別名不可與內層欄位同名（ClickHouse 會把同名別名代入所有出現處，抑制條件就會用到已經變 NULL 的值）
    sel = ["path"] + ([f"if(_k < {k}, NULL, _k) AS users", f"if(_k < {k}, NULL, _n) AS sessions", f"if(_k < {k}, NULL, _ms) AS avg_ms", "anchor_users", "anchor_sessions", "_k", f"_k < {k} AS suppressed"] if k else ["_k AS users", "_n AS sessions", "_ms AS avg_ms", "anchor_users", "anchor_sessions", "_k"])
    sql = f"WITH {parts.with_sql} SELECT {', '.join(sel)} FROM ({inner}) ORDER BY _k DESC, _n DESC, path LIMIT {pq.top_n}"
    from .policy import budget
    cols = ["path", "users", "sessions", "avg_ms", "anchor_users", "anchor_sessions", "_k"] + (["suppressed"] if k else [])
    fields = _fields(pq)
    lineage = ["dmp.events", f"event:{pq.anchor_event}"] + [f"event:{e}" for e in pq.node_events] + (["dmp.users_dict"] if any(f.kind == "user_hot" for f in fields) else []) \
        + (["dmp.users"] if parts.ctx.cold else []) + (["dmp.segment_members"] if any(f.kind == "segment" for f in fields) else [])
    warnings = list(parts.warnings)
    if pq.sample: warnings.append(f"抽樣 {pq.sample}（按用戶分桶）；人數為抽樣後實數，推估請除以抽樣比例")
    if pq.session_mode == "id": warnings.append("以 session_id 切會話：沒有 session_id 的事件會落在同一條會話")
    return Compiled(sql=sql, layer="L0", columns=cols, settings=budget(profile), lineage=sorted(set(lineage)), warnings=warnings, k_anonymity=k, masked={}, row_filters=parts.row_filters)


def _fields(pq: PathQuery) -> list[FieldRef]:
    out: list[FieldRef] = [f for _, f in pq.splits]
    def rec(x: Any) -> None:
        if x is None: return
        for it in x.items:
            if isinstance(it, Group): rec(it)
            else: out.append(it.field)
    for g in (pq.filters, pq.anchor_filters): rec(g)
    return out


def sankey(rows: list[dict[str, Any]], pq: PathQuery) -> dict[str, Any]:
    """路徑列 → paths（含 share）＋桑基 nodes／links（只用未抑制的路徑；before 方向以終點為最後一層）。
    數值是會話數（同神策）：每會話只有一條路徑，各路徑 sessions 加總不重複；share = 路徑會話 ÷ 起點會話。"""
    anchor_users = int(rows[0]["anchor_users"]) if rows else 0
    anchor_sessions = int(rows[0]["anchor_sessions"]) if rows else 0
    paths = []
    nodes: dict[tuple[int, str], int] = {}; links: dict[tuple[int, str, str], int] = {}
    for r in rows:
        sup = bool(r.get("suppressed"))
        paths.append({"path": list(r["path"]), "users": None if sup else int(r["users"]), "sessions": None if sup else int(r["sessions"]),
                      "share": None if sup or not anchor_sessions else round(int(r["sessions"]) / anchor_sessions, 4), "avg_ms": None if sup or r["avg_ms"] is None else float(r["avg_ms"]), "suppressed": sup})
        if sup: continue
        p = list(r["path"]); n_s = int(r["sessions"])
        off = (pq.steps + 1 - len(p)) if pq.direction == "before" else 0        # before：對齊到右邊，終點在第 steps 層
        for i, n in enumerate(p):
            nodes[(i + off, n)] = nodes.get((i + off, n), 0) + n_s
            if i > 0:
                key = (i - 1 + off, p[i - 1], n); links[key] = links.get(key, 0) + n_s
    return {"paths": paths, "anchor_users": anchor_users, "anchor_sessions": anchor_sessions,
            "nodes": [{"level": lv, "node": n, "sessions": v} for (lv, n), v in sorted(nodes.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))],
            "links": [{"level": lv, "from": a, "to": b, "sessions": v} for (lv, a, b), v in sorted(links.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1], kv[0][2]))]}
