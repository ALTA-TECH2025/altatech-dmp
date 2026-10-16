"""語意查詢編譯器（docs/23 §4.3）：parser（dict → AST）→ planner／events 樣板（AST → SQL）→ policy 注入 → cost（EXPLAIN ESTIMATE）。

用法：
    from backend.semantic.compiler import compile_query
    c = compile_query({"metrics": [{"event": "bond_order", "agg": "total"}], "time": {"from": "2026-10-01", "to": "2026-10-16"}}, decision=authorize(...))
    c.sql, c.settings, c.lineage, c.warnings
"""
from __future__ import annotations

from typing import Any

from .catalog import Catalog
from .events import Compiled, compile_events
from .parser import Query, parse

__all__ = ["Catalog", "Compiled", "Query", "compile_events", "compile_query", "parse"]


def compile_query(raw: dict[str, Any], decision: Any = None, cat: Catalog | None = None, profile: str = "human") -> Compiled:
    cat = cat or Catalog()
    return compile_events(parse(raw, cat), cat, decision, profile)
