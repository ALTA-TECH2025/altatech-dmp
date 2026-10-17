"""分析路由：POST /v1/semantic/compile（dry-run）與 POST /v1/analytics/events（執行）。router 只做：驗證 → Policy → 編譯器 → 執行 → 審計；不在這裡拼 SQL。"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date as _date
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException

from ..security.policy import RBAC, Subject, authorize
from ..semantic.compiler import Catalog, Compiled, compile_query
from ..semantic.compiler.attribution import compile_attribution, parse_attribution, shape
from ..semantic.compiler.funnel import compile_funnel, parse_funnel, with_rates
from ..semantic.compiler.parser import AGGS, OPS
from ..semantic.compiler.path import compile_path, parse_path, sankey
from ..semantic.compiler.retention import compile_retention, parse_retention, with_complete
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


class _Run:
    """一種分析（events／funnel／retention／path／attribution）的執行器：授權 → 解析／編譯 → 執行 → 審計 → 回應。
    路由與儀表板卡片共用；module 參數讓儀表板的審計寫 dashboard 而非各分析模組。"""
    kind: str = ""
    module: str = ""          # rbac 模組（授權用）

    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]: ...
    def shape(self, rows: list[dict[str, Any]], q: Any, c: Compiled) -> dict[str, Any]: ...
    def extra_audit(self, q: Any) -> dict[str, Any]: return {}


class _Events(_Run):
    kind, module = "events", "events"
    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]:
        return compile_query(body, decision=d, cat=cat, profile=str(body.get("profile") or "human")), body
    def shape(self, rows: list[dict[str, Any]], q: Any, c: Compiled) -> dict[str, Any]:
        return {"rows": rows, "chart_spec": chart_spec(q, c), "columns": c.columns, "masked": c.masked}


class _Funnel(_Run):
    kind, module = "funnel", "funnel"
    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]:
        fq = parse_funnel(body, cat); return compile_funnel(fq, cat, d, str(body.get("profile") or "human")), fq
    def shape(self, rows: list[dict[str, Any]], fq: Any, c: Compiled) -> dict[str, Any]:
        return {"rows": with_rates(rows, fq.steps), "steps": [{"event": s.event, "alias": s.alias} for s in fq.steps], "columns": c.columns + ["alias", "event", "rate_prev", "rate_first"],
                "chart_spec": {"type": "funnel", "x": "step", "series": ["users"], "group": ["g1"] if fq.group_by else [], "trend": fq.trend}}


class _Retention(_Run):
    kind, module = "retention", "events"          # 留存屬 events 模組（rbac：事件／漏斗／路徑／歸因／留存）
    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]:
        rq = parse_retention(body, cat); return compile_retention(rq, cat, d, str(body.get("profile") or "human")), rq
    def shape(self, rows: list[dict[str, Any]], rq: Any, c: Compiled) -> dict[str, Any]:
        today = rq.time.now or _date.today()
        return {"rows": with_complete(rows, rq, today), "columns": c.columns + ["complete"],
                "chart_spec": {"type": "retention_matrix", "x": "period", "y": "cohort", "value": "rate", "group": ["g1"] if rq.group_by else [], "model": rq.model, "unit": rq.unit, "periods": rq.periods, "lo": rq.lo}}


class _Path(_Run):
    kind, module = "path", "path"
    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]:
        raw = {**body, "sample": body["sample"] if "sample" in body else 0.1}          # 路徑預設抽樣 1/10（與神策同）；明確給 null 才不抽樣
        pq = parse_path(raw, cat); return compile_path(pq, cat, d, str(body.get("profile") or "human")), pq
    def shape(self, rows: list[dict[str, Any]], pq: Any, c: Compiled) -> dict[str, Any]:
        return {**sankey(rows, pq), "sampled": pq.sample, "chart_spec": {"type": "sankey", "direction": pq.direction, "steps": pq.steps, "anchor": pq.anchor}}
    def extra_audit(self, pq: Any) -> dict[str, Any]: return {"sample": pq.sample}


class _Attribution(_Run):
    kind, module = "attribution", "attribution"
    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]:
        aq = parse_attribution(body, cat); return compile_attribution(aq, cat, d, str(body.get("profile") or "human")), aq
    def shape(self, rows: list[dict[str, Any]], aq: Any, c: Compiled) -> dict[str, Any]:
        return {**shape(rows, aq), "chart_spec": {"type": "attribution", "x": "touch", "series": aq.models, "split": bool(aq.split)}}


class _Sql(_Run):
    """自定義 SQL 當作一種分析（書籤／看板卡片 kind=sql）：沙盒規則驗證後包成唯讀查詢；表格呈現。"""
    kind, module = "sql", "events"
    def compile(self, body: dict[str, Any], d: Any, cat: Catalog) -> tuple[Compiled, Any]:
        from ..sql import sandbox
        if not sandbox.sandbox_allowed(list(body.get("_roles") or []), RBAC["roles"]):
            raise PermissionError("此身分沒有自定義 SQL 權限（數據分析、資訊管理、系統管理員）")
        try:
            c = sandbox.compile_sql(str(body.get("sql") or ""), body.get("params") or None, d, cat, list(body.get("_roles") or []), int(body.get("limit") or sandbox.MAX_LIMIT), sandbox.MID_TABLES)
        except sandbox.SandboxError as e:
            raise ValueError(str(e)) from None
        return c, body
    def shape(self, rows: list[dict[str, Any]], q: Any, c: Compiled) -> dict[str, Any]:
        cols = list(rows[0].keys()) if rows else []
        return {"rows": rows, "columns": cols, "chart_spec": {"type": "table", "x": cols[0] if cols else None, "series": [k for k in cols[1:] if rows and isinstance(rows[0].get(k), int | float)]}, "masked": {}}
    def extra_audit(self, q: Any) -> dict[str, Any]: return {"sql_sandbox": True}


RUNNERS: dict[str, _Run] = {r.kind: r for r in (_Events(), _Funnel(), _Retention(), _Path(), _Attribution(), _Sql())}


def compile_kind(kind: str, subject: Subject, body: dict[str, Any], cat: Catalog) -> tuple[Compiled, Any, Any]:
    """授權 + 解析／編譯（不執行）；錯誤轉 HTTP 400／403。回 (compiled, parsed_query, decision)。"""
    run = RUNNERS[kind]
    d = authorize(subject, "use", run.module, {"export": bool(body.get("export"))} if kind == "events" else None)
    if not d.allowed:
        raise HTTPException(403, d.reason)
    if kind == "sql":
        body = {**body, "_roles": list(subject.roles)}                      # 沙盒需要角色（可用表、sql_sandbox 旗標）
    try:
        c, q = run.compile(body, d, cat)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return c, q, d


def execute_kind(kind: str, store: Store, ch: Executor | None, cat: Catalog, subject: Subject, body: dict[str, Any], module: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """完整一次分析：編譯 → ClickHouse → 審計 query.executed（module 預設為該分析模組；儀表板傳 dashboard）→ 回應。"""
    run = RUNNERS[kind]
    c, q, _ = compile_kind(kind, subject, body, cat)
    if ch is None:
        raise HTTPException(503, "此環境未連接 ClickHouse（只能 /v1/semantic/compile）")
    mod = module or run.kind
    sql_hash = hashlib.sha256(c.sql.encode()).hexdigest()[:16]
    t0 = time.perf_counter()
    try:
        rows = run_rows(ch, c)
    except RuntimeError as e:
        store.audit("query.failed", {"user": subject.user_id, "module": mod, "sql_hash": sql_hash, "error": str(e)[:200], **(extra or {})})
        raise HTTPException(502, f"查詢失敗：{str(e)[:300]}") from None
    ms = round((time.perf_counter() - t0) * 1000)
    audit = {"user": subject.user_id, "module": mod, "sql_hash": sql_hash, "rows": len(rows), "ms": ms, "row_policy": c.row_filters, "lineage": c.lineage, **run.extra_audit(q), **(extra or {})}
    if kind == "events":
        audit.update({"layer": c.layer, "masked": list(c.masked)})
    store.audit("query.executed", audit)
    return {**run.shape(rows, q, c), "sql": c.sql, "lineage": c.lineage, "cost": {"ms": ms, "rows": len(rows), "layer": c.layer}, "warnings": c.warnings}


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()

    @r.post("/v1/semantic/compile")
    def compile_(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        c, _, _ = compile_kind("events", subject, body, cat)
        est = estimate_rows(ch, c.sql) if ch is not None else None
        return {"sql": c.sql, "layer": c.layer, "columns": c.columns, "settings": c.settings, "lineage": c.lineage, "warnings": c.warnings,
                "estimated_scan_rows": est, "estimated_scan_bytes": (est * 150) if est is not None else None, "masked": c.masked, "k_anonymity": c.k_anonymity}

    @r.post("/v1/analytics/events")
    def events(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return execute_kind("events", store, ch, cat, subject, body)

    @r.post("/v1/analytics/funnel")
    def funnel(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return execute_kind("funnel", store, ch, cat, subject, body)

    @r.post("/v1/analytics/retention")
    def retention(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return execute_kind("retention", store, ch, cat, subject, body)

    @r.post("/v1/analytics/path")
    def path(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return execute_kind("path", store, ch, cat, subject, body)

    @r.post("/v1/analytics/attribution")
    def attribution(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return execute_kind("attribution", store, ch, cat, subject, body)

    @r.get("/v1/semantic/catalog")
    def catalog(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        """查詢建構器用的目錄：依身分過濾（L3 以上的用戶屬性不給）。"""
        d = authorize(subject, "view", "events")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return {
            "events": [{"name": n, "cn": e.get("cn", n), "group": e.get("group", ""), "status": e.get("status", "live")} for n, e in cat.events.items()],
            "dimensions": [{"name": n, "cn": x.get("cn", n), "type": x.get("type", "string"), "values": x.get("values", []), "sensitivity": x.get("sensitivity", "L1")} for n, x in cat.dimensions.items() if n not in ("agent_id",)],
            "event_columns": [{"name": "platform", "cn": "平台"}, {"name": "channel", "cn": "渠道"}, {"name": "utm_source", "cn": "流量來源"}, {"name": "symbol", "cn": "商品代號"}, {"name": "side", "cn": "買賣別"}, {"name": "amount", "cn": "金額", "type": "number"}, {"name": "entity_id", "cn": "業務 ID（entity_id）"}, {"name": "$event_hour", "cn": "時段", "type": "number"}],
            "user_props": [{"key": k, "cn": x.get("cn", k), "type": x.get("type", "string"), "values": x.get("values", []), "filterable": bool(x.get("filterable")), "groupable": bool(x.get("groupable"))} for k, x in cat.user_props.items() if k in cat.stored_props],
            "aggs": sorted(AGGS), "ops": {k: sorted(v) for k, v in OPS.items()},
            "funnel": {"window_units": ["minute", "hour", "day"], "max_window_days": 90, "max_steps": 10, "orders": ["loose", "strict"], "metrics": ["users", "times"]},
            "path": {"max_steps": 10, "session_modes": ["gap", "id", "cross"], "default_sample": 0.1, "max_days": 92, "exit_node": "離開"},
            "attribution": {"models": ["first", "last", "linear", "position", "decay", "custom"], "max_touches": 10, "max_window_days": 90, "value_fields": ["amount"]},
        }

    return r
