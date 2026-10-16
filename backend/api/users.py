"""用戶列表與用戶細查路由：POST /v1/users/estimate、/v1/users/list；GET /v1/profiles/{one_id}［/timeline｜/identities］。
router 只做：驗證 → Policy（users.use；細查 profile.use + reason）→ 編譯器（semantic/compiler/users）→ 執行 → 審計；不在這裡拼 SQL。
"""
from __future__ import annotations

import hashlib
import time
from datetime import date as _date
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog, Compiled
from ..semantic.compiler.users import (
    MAX_BROWSE,
    MAX_PAGE,
    compile_estimate,
    compile_identities,
    compile_list,
    compile_profile,
    compile_segments_of,
    compile_summary,
    compile_timeline,
    decode_cursor,
    identity_graph,
    next_cursor,
    parse_list,
    parse_users,
    strip_props,
    timeline_cursor,
)
from .analytics import Executor, run_rows
from .store import Store


def _oid_hash(one_id: str) -> str:
    return hashlib.sha256(one_id.encode()).hexdigest()[:16]


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()

    def need_ch() -> Executor:
        if ch is None:
            raise HTTPException(503, "此環境未連接 ClickHouse")
        return ch

    def run(c: Compiled, subject: Subject, module: str) -> tuple[list[dict[str, Any]], int]:
        t0 = time.perf_counter()
        try:
            rows = run_rows(need_ch(), c)
        except RuntimeError as e:
            store.audit("query.failed", {"user": subject.user_id, "module": module, "error": str(e)[:200]})
            raise HTTPException(502, f"查詢失敗：{str(e)[:300]}") from None
        return rows, round((time.perf_counter() - t0) * 1000)

    def compile_users(subject: Subject, body: dict[str, Any], fn: Any, *extra: Any) -> tuple[Any, Compiled]:
        d = authorize(subject, "use", "users")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        try:
            uq = parse_users(body, cat)
            return uq, fn(uq, *extra, cat, d, str(body.get("profile") or "human"))
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @r.post("/v1/users/estimate")
    def estimate(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _, c = compile_users(subject, body, compile_estimate)
        rows, ms = run(c, subject, "users")
        n = int(rows[0]["users"]) if rows else 0
        store.audit("users.estimated", {"user": subject.user_id, "module": "users", "sql_hash": hashlib.sha256(c.sql.encode()).hexdigest()[:16], "users": n, "ms": ms, "row_policy": c.row_filters, "lineage": c.lineage})
        return {"users": n, "sql": c.sql, "lineage": c.lineage, "cost": {"ms": ms, "rows": 1, "layer": c.layer}, "warnings": c.warnings}

    @r.post("/v1/users/list")
    def list_(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        try:
            spec = parse_list(body, cat)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        uq, c = compile_users(subject, body, lambda uq_, cat_, d_, p_: compile_list(uq_, spec, cat_, d_, p_))
        total: int | None = None
        if spec.cursor is None:
            ce = compile_estimate(uq, cat, authorize(subject, "use", "users"))
            er, _ = run(ce, subject, "users"); total = int(er[0]["users"]) if er else 0
        else:
            total = spec.cursor.get("t")
        rows, ms = run(c, subject, "users")
        nc = next_cursor(rows, spec, total)
        out = [{k: v for k, v in row.items() if k not in ("_s", "_u")} for row in rows]
        store.audit("users.listed", {"user": subject.user_id, "module": "users", "sql_hash": hashlib.sha256(c.sql.encode()).hexdigest()[:16], "rows": len(out), "ms": ms,
                                     "masked": list(c.masked), "row_policy": c.row_filters, "lineage": c.lineage, "offset": int((spec.cursor or {}).get("n", 0))})
        return {"rows": out, "columns": [x for x in c.columns if x not in ("_s", "_u")], "masked": c.masked, "next_cursor": nc, "total": total, "sql": c.sql,
                "lineage": c.lineage, "cost": {"ms": ms, "rows": len(out), "layer": c.layer}, "warnings": c.warnings + ([f"瀏覽上限 {MAX_BROWSE:,} 筆；更多請走匯出（審批）"] if total and total > MAX_BROWSE else [])}

    # ---- 細查（需 reason；範圍外 404）----
    def profile_decision(subject: Subject, reason: str) -> Any:
        reason = (reason or "").strip()
        if not 4 <= len(reason) <= 200:
            raise HTTPException(400, "reason 需 4–200 字（客訴單號、調查目的等）")
        d = authorize(subject, "use", "profile", {"reason": reason})
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return d

    def find_user(one_id: str, d: Any, subject: Subject) -> dict[str, Any]:
        c = compile_profile(one_id, cat, d)
        rows, _ = run(c, subject, "profile")
        if not rows:
            raise HTTPException(404, "用戶不存在或不在你的資料範圍")
        return {"row": rows[0], "masked": c.masked, "warnings": c.warnings}

    @r.get("/v1/profiles/{one_id}")
    def profile(one_id: str, reason: str = Query(...), project: str = Query("altatech_dmp"), days: int = Query(30, ge=1, le=365), now: str | None = Query(None), subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = profile_decision(subject, reason)
        found = find_user(one_id, d, subject)
        nd = _date.fromisoformat(now[:10]) if now else None
        sm, ms = run(compile_summary(one_id, project, cat, d, now=nd, days=days), subject, "profile")
        segs, _ = run(compile_segments_of(one_id), subject, "profile")
        store.audit("profile.viewed", {"user": subject.user_id, "module": "profile", "one_id_hash": _oid_hash(one_id), "reason": reason.strip(), "masked": list(found["masked"]), "ms": ms})
        row = found["row"]
        return {"one_id": row["one_id"], "props": {k: v for k, v in row.items() if k != "one_id"}, "masked": found["masked"], "segments": [s["segment_id"] for s in segs],
                "summary": {"days": days, "events": [{**x, "cn": cat.events.get(x["event"], {}).get("cn", x["event"])} for x in sm], "total": sum(int(x["n"]) for x in sm)}, "warnings": found["warnings"]}

    @r.get("/v1/profiles/{one_id}/timeline")
    def timeline(one_id: str, reason: str = Query(...), project: str = Query("altatech_dmp"), from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                 events: str | None = Query(None), limit: int = Query(100, ge=1, le=MAX_PAGE), cursor: str | None = Query(None), now: str | None = Query(None),
                 subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = profile_decision(subject, reason)
        find_user(one_id, d, subject)
        nd = _date.fromisoformat(now[:10]) if now else None
        end = _date.fromisoformat(to[:10]) if to else (nd or _date.today())
        start = _date.fromisoformat(from_[:10]) if from_ else end - timedelta(days=89)
        try:
            cur = decode_cursor(cursor) if cursor else None
            c = compile_timeline(one_id, project, start, end, cat, d, events=[x for x in (events or "").split(",") if x], limit=limit, cursor=cur, now=nd)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        rows, ms = run(c, subject, "profile")
        fp = d.field_policies
        for row in rows:
            row["props"] = strip_props(row.get("props"), cat, fp)
            row["cn"] = cat.events.get(str(row.get("event")), {}).get("cn", row.get("event"))
        store.audit("profile.timeline", {"user": subject.user_id, "module": "profile", "one_id_hash": _oid_hash(one_id), "reason": reason.strip(), "rows": len(rows), "masked": list(c.masked), "ms": ms, "from": start.isoformat(), "to": end.isoformat()})
        return {"rows": rows, "next_cursor": timeline_cursor(rows, limit, cur), "masked": c.masked, "sql": c.sql, "cost": {"ms": ms, "rows": len(rows), "layer": c.layer}}

    @r.get("/v1/profiles/{one_id}/identities")
    def identities(one_id: str, reason: str = Query(...), subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        d = profile_decision(subject, reason)
        find_user(one_id, d, subject)
        rows, ms = run(compile_identities(one_id), subject, "profile")
        g = identity_graph(rows, d)
        store.audit("profile.identities", {"user": subject.user_id, "module": "profile", "one_id_hash": _oid_hash(one_id), "reason": reason.strip(), "edges": len(g["edges"]), "ms": ms})
        return g

    return r
