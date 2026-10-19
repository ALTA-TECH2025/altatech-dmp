"""行為篩選（用戶做過／未做過）與對照組：事件分析、漏斗、留存共用（契約 openapi SemanticQuery／FunnelQuery／RetentionQuery 的 behaviors／behaviors_op／cohorts）。

- 行為條件：與用戶列表／分群同一套 Behavior（users.parse_behavior／behavior_select_sql），編成
  e.one_id_uint IN (SELECT one_id_uint FROM dmp.events … GROUP BY one_id_uint HAVING …)（未做過 = NOT IN）；
  同一組內依 behaviors_op 以 AND／OR 串，組與組之間一律 AND（全局一組、對照組自己一組）。只算符合用戶的事件，一律走明細層。
- 對照組：每組把自己的篩選（與全局篩選取交集）與行為併進查詢各編一次，UNION ALL 後第一欄是組名（事件／漏斗叫 cohort；留存叫 cohort_name，避免與同期群欄撞名）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from .catalog import Catalog
from .parser import Group, TimeRange, parse_filters

MAX_COHORTS = 10
BehaviorGroup = tuple[list[Any], str]          # ([Behavior], and|or)


@dataclass
class Cohort:
    name: str
    filters: Group | None = None
    behaviors: list[Any] = field(default_factory=list)
    behaviors_op: str = "and"


def parse_behavior_group(cat: Catalog, raw: dict[str, Any], now: date | None, default_time: TimeRange | None, what: str = "behaviors") -> BehaviorGroup | None:
    """raw 的 behaviors／behaviors_op → 一組行為條件；沒有行為 → None。"""
    from .users import parse_behaviors
    bs = parse_behaviors(cat, raw.get("behaviors"), now, default_time, what)
    if not bs:
        return None
    op = str(raw.get("behaviors_op") or "and")
    if op not in ("and", "or"):
        raise ValueError(f"{what}_op 只能是 and／or")
    return (bs, op)


def parse_cohorts(cat: Catalog, raw: Any, now: date | None, default_time: TimeRange | None) -> list[Cohort]:
    out: list[Cohort] = []
    for i, c in enumerate(raw or []):
        if i >= MAX_COHORTS:
            raise ValueError(f"cohorts 最多 {MAX_COHORTS} 組")
        if not isinstance(c, dict):
            raise ValueError(f"cohorts[{i}] 必須是物件")
        name = str(c.get("name") or "").strip()
        if not 1 <= len(name) <= 40:
            raise ValueError(f"cohorts[{i}].name 必填（1–40 字）")
        if any(x.name == name for x in out):
            raise ValueError(f"對照組名重複：{name}")
        filters = parse_filters(cat, c.get("filters"))
        if c.get("segment"):
            from .parser import Cond
            seg = Cond(cat.resolve(f"seg.{c['segment']}"), "in", None)
            filters = Group("and", [filters, seg]) if filters else Group("and", [seg])
        bg = parse_behavior_group(cat, c, now, default_time, f"cohorts[{i}].behaviors")
        out.append(Cohort(name, filters, bg[0] if bg else [], bg[1] if bg else "and"))
    return out


def merge_filters(a: Group | None, b: Group | None) -> Group | None:
    if a and b:
        return Group("and", [a, b])
    return a or b


def derive(q: Any, c: Cohort) -> Any:
    """對照組的查詢 = 原查詢 ＋ 該組的篩選（且）與行為（另一組，且）；清掉 cohorts 避免遞迴。事件／漏斗／留存三種查詢物件都有 filters、behaviors、cohorts 欄位。"""
    groups = list(q.behaviors) + ([(list(c.behaviors), c.behaviors_op)] if c.behaviors else [])
    return replace(q, filters=merge_filters(q.filters, c.filters), behaviors=groups, cohorts=[])


def behavior_where(groups: list[BehaviorGroup], project: str, cat: Catalog, decision: Any, alias: str = "e") -> tuple[list[str], list[str], list[str]]:
    """行為組 → WHERE 條件（每組一條）、lineage、warnings。"""
    from .users import behavior_select_sql
    conds: list[str] = []; lineage: list[str] = []; warnings: list[str] = []
    for bs, op in groups:
        parts = []
        for b in bs:
            sub, lin, w, _ = behavior_select_sql(project, b, cat, decision)
            parts.append(f"{alias}.one_id_uint {'IN' if b.done else 'NOT IN'} ({sub})")
            lineage += lin; warnings += [x for x in w if x not in warnings]
        conds.append("(" + f" {op.upper()} ".join(parts) + ")" if len(parts) > 1 else parts[0])
    return conds, lineage, warnings


def with_cohorts(cohorts: list[Cohort], compile_one: Callable[[Cohort], Any], col: str, order_cols: list[str]) -> Any:
    """每組各編一次 → UNION ALL，第一欄 col = 組名；依組的順序、再依各分析自己的鍵排序。"""
    from .events import Compiled, q
    parts: list[str] = []; comps = []
    for i, c in enumerate(cohorts):
        comp = compile_one(c); comps.append(comp)
        parts.append(f"SELECT {i} AS _c, {q(c.name)} AS {col}, {', '.join(comp.columns)} FROM ({comp.sql})")
    first = comps[0]
    cols = [col] + list(first.columns)
    sql = f"SELECT {', '.join(cols)} FROM ({' UNION ALL '.join(parts)}) ORDER BY {', '.join(['_c'] + order_cols)}"
    warnings: list[str] = []
    for comp in comps:
        warnings += [w for w in comp.warnings if w not in warnings]
    lineage = sorted({x for comp in comps for x in comp.lineage})
    return Compiled(sql=sql, layer=first.layer, columns=cols, settings=first.settings, lineage=lineage, warnings=warnings, k_anonymity=first.k_anonymity,
                    masked=first.masked, row_filters=first.row_filters)


def behavior_ok(oracle: Any, groups: list[BehaviorGroup], project: str, one_id: str, decision: Any, now: date) -> bool:
    """oracle 用：這個用戶是否通過所有行為組（組內 and／or，組間 and）。"""
    for bs, op in groups:
        res = []
        for b in bs:
            s = oracle.behavior_users(project, b, decision, now)
            res.append((one_id in s) if b.done else (one_id not in s))
        if not (all(res) if op == "and" else any(res)):
            return False
    return True
