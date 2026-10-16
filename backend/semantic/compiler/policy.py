"""Policy 注入：security.policy.Decision → 行級 WHERE、欄位遮罩、K 匿名、預算 settings。UI／REST／MCP 都經由這裡，編譯器之外不得拼 SQL。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore

from .catalog import Catalog
from .parser import Query

BUDGETS = yaml.safe_load((Path(__file__).resolve().parent / "budgets.yaml").read_text(encoding="utf-8"))
DEFAULT_BUCKETS = [0, 1e5, 5e5, 1e6, 5e6]


def budget(profile: str) -> dict[str, Any]:
    if profile not in BUDGETS["profiles"]:
        raise ValueError(f"未知預算設定檔 {profile}；可用 {sorted(BUDGETS['profiles'])}")
    return dict(BUDGETS["profiles"][profile])


def parse_policy(p: str) -> tuple[str, list[float] | int | None]:
    """'bucket([0,1e5])' → ('bucket', [0,100000]); 'round(1000)' → ('round', 1000); 'mask_tail(4)'；'plain'／'hide'／'bucket'。"""
    m = re.fullmatch(r"(\w+)(?:\((.*)\))?", p.strip())
    if not m:
        raise ValueError(f"無法解析遮罩策略 {p}")
    name, arg = m.group(1), m.group(2)
    if name == "bucket":
        edges = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", arg or "")] or DEFAULT_BUCKETS
        return name, edges
    if name in ("round", "mask_tail"):
        return name, int(float(arg)) if arg else (1000 if name == "round" else 4)
    return name, None


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def bucket_label_sql(v: str, edges: list[float]) -> str:
    """數值 → 區間標籤（字串）：'<0'、'0–100000'、…、'≥5000000'；NULL 保持 NULL。"""
    branches = [f"{v} < {edges[0]!r}, '<{_fmt(edges[0])}'"]
    for a, b in zip(edges, edges[1:]):
        branches.append(f"{v} < {b!r}, '{_fmt(a)}–{_fmt(b)}'")
    return f"multiIf({v} IS NULL, NULL, {', '.join(branches)}, '≥{_fmt(edges[-1])}')"


def bucket_label(v: float | None, edges: list[float]) -> str | None:
    if v is None: return None
    if v < edges[0]: return f"<{_fmt(edges[0])}"
    for a, b in zip(edges, edges[1:]):
        if v < b: return f"{_fmt(a)}–{_fmt(b)}"
    return f"≥{_fmt(edges[-1])}"


def mask_sql(policy: str, v: str, vtype: str) -> str:
    name, arg = parse_policy(policy)
    if name == "plain": return v
    if name == "bucket": return bucket_label_sql(v, arg)            # type: ignore[arg-type]
    if name == "round": return f"round({v} / {arg}) * {arg}"
    if name == "mask_tail": return f"if({v} IS NULL, NULL, concat(substring(toString({v}), 1, greatest(length(toString({v})) - {arg}, 0)), repeat('*', least(length(toString({v})), {arg}))))"
    if name == "mask_all": return "'***'"
    if name == "hash": return f"lower(hex(sipHash64({v})))"
    raise ValueError(f"遮罩策略 {name} 不能用在查詢輸出")


def rewrite_row_filter(expr: str, cat: Catalog, ctx: Any) -> str:
    """policy.authorize() 的行級條件寫在邏輯欄位上（branch_id／agent_id／event_date）；改寫成事件表可用的表達式。"""
    def sub(m: re.Match[str]) -> str:
        col = m.group(0)
        if col in ("branch_id", "agent_id"):
            return ctx.expr(cat.resolve(f"user.{col}"))
        if col == "event_date":
            return f"{ctx.t}.event_date"
        return col
    out = re.sub(r"\b(branch_id|agent_id|event_date)\b", sub, expr)
    return out.replace("today()", ctx.now_sql())


def inject(decision: Any, qr: Query, cat: Catalog, ctx: Any, msql: list[str]) -> tuple[list[str], list[str], dict[str, str], list[str]]:
    """回傳 (row_filters, 遮罩後的指標 SQL, 輸出欄→策略, warnings)。欄位被 hide 的指標／分組／篩選直接拒絕。"""
    if not getattr(decision, "allowed", True):
        raise PermissionError(getattr(decision, "reason", "denied"))
    fp: dict[str, str] = dict(getattr(decision, "field_policies", {}) or {})
    rows = [rewrite_row_filter(r, cat, ctx) for r in (getattr(decision, "row_filters", []) or [])]
    masked: dict[str, str] = {}
    warnings: list[str] = []
    for f in qr.walk_fields():
        if fp.get(f.name, "plain") == "hide":
            raise PermissionError(f"欄位 {f.key} 對此角色不可見")
    out = []
    for m, s in zip(qr.metrics, msql):
        pol = fp.get(m.field.name, "plain") if m.field else "plain"
        if m.field and m.agg in ("sum", "avg", "avg_val", "max", "min", "p25", "p50", "p75", "p90") and pol != "plain":
            out.append(mask_sql(pol, s, "number")); masked[m.alias] = pol
            if any(m.alias in f.tokens for f in qr.formulas):
                raise PermissionError(f"指標 {m.alias} 對此角色只給區間，不能再進公式")
        else:
            out.append(s)
    for i, g in enumerate(qr.group_by):
        pol = fp.get(g.name, "plain")
        if pol != "plain":
            masked[f"g{i + 1}"] = pol
    return rows, out, masked, warnings
