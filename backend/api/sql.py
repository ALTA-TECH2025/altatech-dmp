"""自定義 SQL 路由：表目錄、執行（沙盒）、預覽下載、已存查詢、查詢記錄、中間表。router 只做：驗證 → 沙盒編譯 → 執行 → 審計 query.sql。"""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import re
import secrets
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response

from ..ops.metrics import observe_query
from ..security.policy import RBAC, Subject, authorize, has_grant
from ..semantic.compiler import Catalog
from ..sql import sandbox
from .analytics import Executor, estimate_rows, run_rows
from .dashboards import _shared, editable, visible
from .store import Store

TZ = ZoneInfo("Asia/Taipei")
MAX_CONCURRENT = 5
SCAN_BUDGET_ROWS = 500_000_000


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()
    running: dict[str, int] = collections.defaultdict(int); lock = threading.Lock()
    results: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()           # run_id → {rows, columns}（最近 50 次，供下載）

    def refresh_mid() -> list[dict[str, Any]]:
        mids = store.items("mid_table"); sandbox.MID_TABLES[:] = [m["name"] for m in mids]
        sandbox.MID_COLUMNS.clear(); sandbox.MID_COLUMNS.update({m["name"]: list(m.get("columns") or []) for m in mids}); return mids
    refresh_mid()

    def need(subject: Subject) -> Any:
        d = authorize(subject, "use", "events")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        if not sandbox.sandbox_allowed(subject.roles, RBAC["roles"]):
            raise HTTPException(403, "此身分沒有自定義 SQL 權限（數據分析、資訊管理、系統管理員）")
        return d

    def compile_(subject: Subject, d: Any, sql: str, params: dict[str, Any] | None, limit: int) -> Any:
        refresh_mid()                                                                      # 中間表名與欄位要是最新的（展開 * 時用）
        try:
            return sandbox.compile_sql(sql, params, d, cat, subject.roles, limit, sandbox.MID_TABLES)
        except sandbox.SandboxError as e:
            raise HTTPException(400, str(e)) from None

    def remember(run: dict[str, Any]) -> None:
        store.put_item("sql_run", run)
        mine = sorted([x for x in store.items("sql_run") if x["user"] == run["user"]], key=lambda x: x["at"], reverse=True)
        cutoff = time.time() - 30 * 86400
        for old in mine[100:] + [x for x in mine if x["at"] < cutoff]:
            store.delete_item("sql_run", old["id"])

    @r.get("/v1/sql/tables")
    def tables(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = need(subject)
        return sandbox.table_catalog(subject.roles, d, cat, refresh_mid())

    @r.post("/v1/sql/query")
    def query(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = need(subject)
        sql = str(body.get("sql") or ""); limit = int(body.get("limit") or sandbox.MAX_LIMIT)
        c = compile_(subject, d, sql, body.get("params") or None, limit)
        run = {"id": "sq_" + secrets.token_hex(5), "user": subject.user_id, "sql": sql[:20000], "sql_hash": hashlib.sha256(c.sql.encode()).hexdigest()[:16], "at": time.time(), "status": "ok", "rows": 0, "ms": 0, "error": None, "lineage": c.lineage}
        if body.get("explain"):
            est = estimate_rows(ch, c.sql) if ch is not None else None
            return {"run_id": run["id"], "sql_effective": c.sql, "scan_rows_estimate": est, "lineage": c.lineage, "warnings": c.warnings, "rows": [], "columns": [], "row_count": 0, "truncated": False, "ms": 0, "explained": True}
        if ch is None:
            raise HTTPException(503, "此環境未連接 ClickHouse")
        with lock:
            if running[subject.user_id] >= MAX_CONCURRENT:
                raise HTTPException(429, f"每人最多 {MAX_CONCURRENT} 條查詢同時執行")
            running[subject.user_id] += 1
        t0 = time.perf_counter()
        try:
            est = None
            try:
                est = estimate_rows(ch, c.sql)
            except Exception:
                est = None
            if est is not None and est > SCAN_BUDGET_ROWS:
                raise HTTPException(400, f"掃描估算 {est:,} 列超過預算 {SCAN_BUDGET_ROWS:,}；請縮小時間範圍或加條件")
            try:
                rows = run_rows(ch, c)
            except RuntimeError as e:
                run.update({"status": "error", "error": str(e)[:300], "ms": round((time.perf_counter() - t0) * 1000)}); remember(run); observe_query("sql", run["ms"], ok=False)
                store.audit("query.sql", {"user": subject.user_id, "module": "sql", "run_id": run["id"], "sql_hash": run["sql_hash"], "status": "error", "error": str(e)[:200]})
                raise HTTPException(502, f"查詢失敗：{str(e)[:300]}") from None
        finally:
            with lock:
                running[subject.user_id] -= 1
        ms = round((time.perf_counter() - t0) * 1000); cols = list(rows[0].keys()) if rows else []
        run.update({"rows": len(rows), "ms": ms, "columns": cols}); remember(run); observe_query("sql", ms, ok=True, rows=len(rows))
        results[run["id"]] = {"rows": rows, "columns": cols, "user": subject.user_id}
        while len(results) > 50: results.popitem(last=False)
        store.audit("query.sql", {"user": subject.user_id, "module": "sql", "run_id": run["id"], "sql_hash": run["sql_hash"], "rows": len(rows), "ms": ms, "lineage": c.lineage, "scan_rows_estimate": est})
        return {"run_id": run["id"], "columns": cols, "rows": rows, "row_count": len(rows), "truncated": len(rows) >= min(limit, sandbox.MAX_LIMIT), "ms": ms, "scan_rows_estimate": est, "sql_effective": c.sql, "lineage": c.lineage, "warnings": c.warnings}

    @r.get("/v1/sql/query/{run_id}/download")
    def download(run_id: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject)
        if not has_grant(subject, "export", "events"):
            raise HTTPException(403, "下載需要事件分析的匯出權限（events.export）")
        res = results.get(run_id)
        if not res or res["user"] != subject.user_id:
            raise HTTPException(404, "結果已不在快取（請重新執行）或不是你的查詢")
        ts = datetime.now(TZ).isoformat(timespec="seconds"); wm = hashlib.sha256(f"{subject.user_id}|{run_id}|{ts}".encode()).hexdigest()[:16]
        buf = io.StringIO(); buf.write(f"# AltaTech DMP 自定義 SQL 預覽；{subject.user_id}；{ts}；{len(res['rows'])} 列；浮水印 {wm}\n")
        w = csv.writer(buf); w.writerow([*res["columns"], "_watermark"])
        for row in res["rows"]:
            w.writerow([*(row.get(c) for c in res["columns"]), wm])
        store.audit("sql.downloaded", {"user": subject.user_id, "run_id": run_id, "rows": len(res["rows"]), "watermark": wm})
        return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="sql-{run_id}.csv"'})

    @r.get("/v1/sql/history")
    def history(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject)
        cutoff = time.time() - 30 * 86400
        return sorted([x for x in store.items("sql_run") if x["user"] == subject.user_id and x["at"] >= cutoff], key=lambda x: x["at"], reverse=True)[:100]

    # ---- 已存查詢 ----
    @r.get("/v1/sql/saved")
    def saved(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject)
        return sorted([{**x, "mine": x["owner"] == subject.user_id, "editable": editable(x, subject)} for x in store.items("sql_saved") if visible(x, subject)], key=lambda x: (x.get("group") or "", x["name"]))

    def validate_saved(body: dict[str, Any], subject: Subject, d: Any, cur: dict[str, Any] | None = None) -> dict[str, Any]:
        x = dict(cur or {})
        if "sql" in body or cur is None:
            x["sql"] = str(body.get("sql") or "").strip()
            compile_(subject, d, x["sql"], body.get("params_default") or x.get("params_default") or None, 10)   # 存之前驗一次（給參數預設值）
        if "name" in body or cur is None:
            x["name"] = str(body.get("name") or "").strip()[:80]
            if not x["name"]: raise HTTPException(400, "name 必填")
        if "group" in body or cur is None: x["group"] = str(body.get("group") or "")[:40]
        if "description" in body: x["description"] = str(body.get("description") or "")[:400]
        if "params_default" in body: x["params_default"] = dict(body.get("params_default") or {})
        if "shared" in body or cur is None: x["shared"] = _shared(body.get("shared"))
        x.setdefault("description", ""); x.setdefault("params_default", {})
        return x

    @r.post("/v1/sql/saved", status_code=201)
    def save(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = need(subject)
        x = validate_saved(body, subject, d); x.update({"id": "sv_" + secrets.token_hex(5), "owner": subject.user_id, "created_at": time.time(), "updated_at": time.time()})
        store.put_item("sql_saved", x); store.audit("sql.saved", {"user": subject.user_id, "saved": x["id"], "name": x["name"]})
        return {**x, "mine": True, "editable": True}

    @r.put("/v1/sql/saved/{id_}")
    def update_saved(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = need(subject)
        cur = next((x for x in store.items("sql_saved") if x["id"] == id_), None)
        if not cur or not visible(cur, subject): raise HTTPException(404, "已存查詢不存在")
        if not editable(cur, subject): raise HTTPException(403, "只有擁有者或系統管理員可修改")
        x = validate_saved(body, subject, d, cur); x["updated_at"] = time.time(); store.put_item("sql_saved", x)
        return {**x, "mine": x["owner"] == subject.user_id, "editable": True}

    @r.delete("/v1/sql/saved/{id_}", status_code=204)
    def delete_saved(id_: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject)
        cur = next((x for x in store.items("sql_saved") if x["id"] == id_), None)
        if not cur or not visible(cur, subject): raise HTTPException(404, "已存查詢不存在")
        if not editable(cur, subject): raise HTTPException(403, "只有擁有者或系統管理員可刪除")
        store.delete_item("sql_saved", id_); store.audit("sql.saved.deleted", {"user": subject.user_id, "saved": id_})
        return Response(status_code=204)

    # ---- 中間表 ----
    def need_mid(subject: Subject) -> None:
        if not (authorize(subject, "admin", "governance").allowed or authorize(subject, "admin", "integration").allowed):
            raise HTTPException(403, "中間表需要資料治理或數據整合的設定權（資訊管理、系統管理員）")

    @r.get("/v1/sql/mid-tables")
    def mid_tables(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject)
        return sorted(refresh_mid(), key=lambda m: m["name"])

    def build_mid(m: dict[str, Any], subject: Subject, d: Any) -> int:
        c = compile_(subject, d, m["sql"], m.get("params_default") or None, sandbox.MAX_LIMIT)
        inner = c.sql[len("SELECT * FROM ("):c.sql.rindex(") AS _q LIMIT")]
        if ch is None:
            raise HTTPException(503, "此環境未連接 ClickHouse")
        ch.query(f"CREATE OR REPLACE TABLE dmp.{m['name']} ENGINE = MergeTree ORDER BY tuple() AS {inner}", settings={"max_execution_time": 600})
        try:
            n = int((ch.query(f"SELECT count() FROM dmp.{m['name']} FORMAT TSV", settings={"max_execution_time": 60}) or "0").strip().splitlines()[0] or 0)
        except (ValueError, IndexError):
            n = 0
        cols = [line.split("\t")[0] for line in (ch.query(f"SELECT name FROM system.columns WHERE database = 'dmp' AND table = '{m['name']}' FORMAT TSV", settings={"max_execution_time": 60}) or "").splitlines() if line.strip() and not line.startswith("{")]
        m.update({"rows": n, "columns": cols, "refreshed_at": time.time()})
        return n

    @r.post("/v1/sql/mid-tables", status_code=201)
    def create_mid(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = need(subject); need_mid(subject)
        raw = str(body.get("name") or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", raw):
            raise HTTPException(400, "name 只能是小寫英數與底線（2–41 字）")
        name = raw if raw.startswith("mid_") else f"mid_{raw}"
        if any(m["name"] == name for m in store.items("mid_table")):
            raise HTTPException(409, f"中間表 {name} 已存在")
        m = {"id": name, "name": name, "sql": str(body.get("sql") or "").strip(), "note": str(body.get("note") or "")[:200], "params_default": dict(body.get("params_default") or {}), "owner": subject.user_id, "created_at": time.time(), "rows": None, "columns": [], "refreshed_at": None}
        try:
            build_mid(m, subject, d)
        except RuntimeError as e:
            raise HTTPException(502, f"建表失敗：{str(e)[:300]}") from None
        store.put_item("mid_table", m); refresh_mid()
        store.audit("sql.mid_table.created", {"user": subject.user_id, "table": name, "rows": m["rows"]})
        return m

    @r.post("/v1/sql/mid-tables/{name}/refresh")
    def refresh(name: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = need(subject); need_mid(subject)
        m = next((x for x in store.items("mid_table") if x["name"] == name), None)
        if not m: raise HTTPException(404, "中間表不存在")
        try:
            build_mid(m, subject, d)
        except RuntimeError as e:
            raise HTTPException(502, f"重算失敗：{str(e)[:300]}") from None
        store.put_item("mid_table", m); store.audit("sql.mid_table.refreshed", {"user": subject.user_id, "table": name, "rows": m["rows"]})
        return m

    @r.delete("/v1/sql/mid-tables/{name}", status_code=204)
    def delete_mid(name: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject); need_mid(subject)
        m = next((x for x in store.items("mid_table") if x["name"] == name), None)
        if not m: raise HTTPException(404, "中間表不存在")
        users = [b["name"] for b in store.items("bookmark") if b.get("kind") == "sql" and name in str((b.get("query") or {}).get("sql", ""))]
        if users: raise HTTPException(409, f"仍有書籤引用：{', '.join(users[:5])}")
        if ch is not None:
            ch.query(f"DROP TABLE IF EXISTS dmp.{name}", settings={"max_execution_time": 60})
        store.delete_item("mid_table", name); refresh_mid(); store.audit("sql.mid_table.deleted", {"user": subject.user_id, "table": name})
        return Response(status_code=204)

    return r
