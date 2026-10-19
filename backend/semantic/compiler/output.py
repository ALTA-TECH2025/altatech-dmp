"""資料輸出任務的查詢編譯（docs/06 §8）：事件明細／用戶寬表／分群名單 → 套遮罩（匯出口徑，比 UI 嚴一級）的列。

- 欄位先驗：L3／L4 一律拒絕（識別子另外處理：one_id 只有輸出策略為 plain 的身分可用，否則用 customer_code 代碼化）；hide 策略拒絕；L2 數值依 masking.yaml 的 export 策略區間化。
- 事件明細：固定欄 + props.<key>（走 Catalog.resolve，L4 名稱不存在於倉）；時間範圍、事件清單、條件樹、行級範圍（policy.inject）。
- 用戶寬表／分群名單：沿用用戶列表的 compile_export（同一套欄位策略與上限）。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ...security.policy import MASK
from .catalog import Catalog
from .events import Compiled, Ctx, group_sql, q
from .parser import Query, TimeRange, parse_filters
from .policy import budget, inject, mask_sql
from .users import props_key_blocked

EVENT_COLS: dict[str, str] = {"event": "string", "time": "date", "event_date": "date", "source": "string", "platform": "string", "app_id": "string", "symbol": "string", "side": "string",
                              "channel": "string", "amount": "number", "utm_source": "string", "utm_campaign": "string", "entity_type": "string", "entity_id": "string", "session_id": "string",
                              "event_id": "string", "props": "json"}
DEFAULT_EVENT_COLS = ["event", "time", "platform", "channel", "symbol", "side", "amount", "session_id", "event_id", "props"]
_LEVEL = {f["name"].lstrip("$"): f.get("level", "L1") for f in MASK["fields"]}


def level_of(name: str) -> str:
    """欄位敏感等級：masking.yaml 有登記的照登記；props.<key>／user.<key> 取鍵名；其餘 L1。"""
    base = name.split(".", 1)[1] if name.startswith(("props.", "user.")) else name
    return _LEVEL.get(base.lstrip("$"), "L1")


def check_columns(columns: list[str], cat: Catalog, decision: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """欄位清單 → [{name, level, policy}]、被拒的欄位（L3／L4、hide）。給預覽與建立時驗證。"""
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {}) if decision is not None else {}
    out: list[dict[str, Any]] = []; rejected: list[str] = []
    for c in columns:
        base = c.split(".", 1)[1] if c.startswith(("props.", "user.")) else c
        lv = level_of(c); pol = fp.get(base.lstrip("$"), fp.get(c, "plain"))
        if base.lstrip("$") in cat.forbidden_l4 or lv in ("L3", "L4") or pol == "hide" or base == "one_id" or (c.startswith("props.") and props_key_blocked(base, cat, fp)):
            rejected.append(c); out.append({"name": c, "level": "L4" if base.lstrip("$") in cat.forbidden_l4 else lv, "policy": "rejected"}); continue
        out.append({"name": c, "level": lv, "policy": pol})
    return out, rejected


def compile_event_rows(project: str, start: date, end: date, events: list[str], columns: list[str], filters_raw: Any, cat: Catalog | None = None, decision: Any = None,
                       limit: int = 1_000_000, now: date | None = None, profile: str = "batch") -> Compiled:
    """事件明細輸出：永遠多帶 _one_id（內部用，呼叫端換成識別子後才輸出）。"""
    cat = cat or Catalog()
    if end < start:
        raise ValueError("time.to 早於 time.from")
    names: list[str] = []
    for ev in events or []:
        n = cat.canonical_event(str(ev))
        if not n: raise ValueError(f"事件 {ev} 不在事件字典")
        names.append(n)
    cols = list(columns or DEFAULT_EVENT_COLS)
    plan, rejected = check_columns(cols, cat, decision)
    if rejected:
        raise ValueError(f"欄位不可輸出（L3／L4 或不可見）：{rejected}")
    qr = Query(project=project, metrics=[], time=TimeRange(start, end, "day", now), filters=parse_filters(cat, filters_raw))
    ctx = Ctx(cat, qr, "L0")
    where = [f"e.project = {q(project)}", f"e.event_date BETWEEN toDate({q(start.isoformat())}) AND toDate({q(end.isoformat())})"]
    if names:
        where.append(f"e.event IN ({', '.join(q(x) for x in sorted(set(names)))})")
    if qr.filters:
        where.append(group_sql(qr.filters, ctx))
    rf: list[str] = []
    if decision is not None:
        rf, _, _, _ = inject(decision, qr, cat, ctx, [])
        where += rf
    sel = ["e.one_id AS _one_id"]; masked: dict[str, str] = {}
    for c, p in zip(cols, plan):
        if c in EVENT_COLS:
            expr = "toString(e.props)" if c == "props" else "toTimeZone(e.time, 'Asia/Taipei')" if c == "time" else f"e.{c}"   # 輸出檔給客戶看：時間用台北時區
        else:
            expr = ctx.expr(cat.resolve(c))
        if p["policy"] != "plain":
            expr = mask_sql(p["policy"], expr, "number" if EVENT_COLS.get(c) == "number" or c.startswith("props.") else "string"); masked[c] = p["policy"]
        sel.append(f"{expr} AS `{c}`")
    cold = f" LEFT JOIN (SELECT one_id, {', '.join('`' + n + '`' for n in ctx.cold)} FROM dmp.users FINAL) AS u ON u.one_id = e.one_id" if ctx.cold else ""
    sql = f"SELECT {', '.join(sel)} FROM dmp.events AS e{cold} WHERE {' AND '.join(where)} ORDER BY e.time, e.event_id LIMIT {int(limit)} /* output_task */"
    lineage = ["dmp.events"] + [f"event:{x}" for x in sorted(set(names))] + (["dmp.users"] if ctx.cold else [])
    return Compiled(sql=sql, layer="L0", columns=["_one_id"] + cols, settings=budget(profile), lineage=lineage, masked=masked, row_filters=rf)


def count_event_rows(c: Compiled) -> str:
    """同一條件的列數估算（去掉 SELECT 清單與 LIMIT）。"""
    body = c.sql.split(" FROM dmp.events AS e", 1)[1].rsplit(" ORDER BY e.time", 1)[0]
    return f"SELECT count() AS n FROM dmp.events AS e{body} /* output_task_count */"
