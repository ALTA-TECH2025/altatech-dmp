"""分析路由：POST /v1/semantic/compile（dry-run）與 POST /v1/analytics/events（執行）。router 只做：驗證 → Policy → 編譯器 → 執行 → 審計；不在這裡拼 SQL。"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException

from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog, Compiled, compile_query
from .store import Store


class Executor(Protocol):
    """ClickHouse 執行介面（runner.ClickHouseHTTP 相容）；測試用假物件。"""
    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str: ...


def run_rows(ch: Executor, c: Compiled) -> list[dict[str, Any]]:
    txt = ch.query(c.sql + " FORMAT JSONEachRow", settings={**c.settings, "output_format_json_quote_64bit_integers": 0, "output_format_json_quote_denormals": 0})
    return [json.loads(l) for l in txt.splitlines() if l.strip()]


def estimate_rows(ch: Executor, sql: str) -> int:
    total = 0
    for l in ch.query("EXPLAIN ESTIMATE " + sql + " FORMAT TSV").splitlines():
        p = l.split("\t")
        if len(p) >= 4:
            total += int(p[3])
    return total


def chart_spec(raw: dict[str, Any], c: Compiled) -> dict[str, Any]:
    """給前端的最小圖表規格：有時間桶 → 折線（多指標多線、分組 → 多線）；total 粒度 → 長條；其餘表格。"""
    gran = (raw.get("time") or {}).get("grain", "day")
    metrics = [m for m in c.columns if m not in ("bucket", "_k", "suppressed") and not m.startswith("g") and not m.endswith("_prev")]
    groups = [g for g in c.columns if g.startswith("g") and g[1:].isdigit()]
    if gran != "total":
        return {"type": "line", "x": "bucket", "series": metrics, "group": groups, "suppressed": "suppressed" in c.columns}
    if groups:
        return {"type": "bar", "x": groups[0], "series": metrics, "suppressed": "suppressed" in c.columns}
    return {"type": "stat", "series": metrics}


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()

    def compile_for(subject: Subject, raw: dict[str, Any]) -> Compiled:
        d = authorize(subject, "use", "events", {"export": bool(raw.get("export"))})
        if not d.allowed:
            raise HTTPException(403, d.reason)
        try:
            return compile_query(raw, decision=d, cat=cat, profile=str(raw.get("profile") or "human"))
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @r.post("/v1/semantic/compile")
    def compile_(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        c = compile_for(subject, body)
        est = estimate_rows(ch, c.sql) if ch is not None else None
        return {"sql": c.sql, "layer": c.layer, "columns": c.columns, "settings": c.settings, "lineage": c.lineage, "warnings": c.warnings,
                "estimated_scan_rows": est, "estimated_scan_bytes": (est * 150) if est is not None else None, "masked": c.masked, "k_anonymity": c.k_anonymity}

    @r.post("/v1/analytics/events")
    def events(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        c = compile_for(subject, body)
        if ch is None:
            raise HTTPException(503, "此環境未連接 ClickHouse（只能 /v1/semantic/compile）")
        t0 = time.perf_counter()
        try:
            rows = run_rows(ch, c)
        except RuntimeError as e:
            store.audit("query.failed", {"user": subject.user_id, "sql_hash": hashlib.sha256(c.sql.encode()).hexdigest()[:16], "error": str(e)[:200]})
            raise HTTPException(502, f"查詢失敗：{str(e)[:300]}") from None
        ms = round((time.perf_counter() - t0) * 1000)
        store.audit("query.executed", {"user": subject.user_id, "module": "events", "sql_hash": hashlib.sha256(c.sql.encode()).hexdigest()[:16], "layer": c.layer,
                                       "rows": len(rows), "ms": ms, "masked": list(c.masked), "row_policy": c.row_filters, "lineage": c.lineage})
        return {"rows": rows, "chart_spec": chart_spec(body, c), "sql": c.sql, "lineage": c.lineage, "cost": {"ms": ms, "rows": len(rows), "layer": c.layer},
                "warnings": c.warnings, "columns": c.columns, "masked": c.masked}

    return r
