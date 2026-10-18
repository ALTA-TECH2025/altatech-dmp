"""分群路由（openapi Segments）：驗證 → Policy（segments.view／use／approve／admin；members 另需 users.use）→ SegmentService → 審計。不在這裡拼 SQL。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..security.policy import Subject, authorize
from ..segments import Conflict, SegmentService, Unavailable
from ..semantic.compiler.users import compile_estimate, compile_list, next_cursor, parse_list, parse_users
from .analytics import run_rows
from .store import Store


def build_router(store: Store, get_subject: Any, svc: SegmentService) -> APIRouter:
    r = APIRouter()

    def need(subject: Subject, action: str) -> Any:
        d = authorize(subject, action, "segments")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return d

    def call(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"分群不存在：{e}") from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except Unavailable as e:
            raise HTTPException(503, str(e)) from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    def rule_decision(subject: Subject) -> Any:
        """規則編譯用的 Decision：users.use（行級／遮罩與用戶列表一致）；沒有 users.use 的身分（資訊管理）不能建規則。"""
        d = authorize(subject, "use", "users")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return d

    @r.get("/v1/segments")
    def list_(q: str | None = None, status: str | None = None, kind: str | None = None, project: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return svc.list(q, status, kind, project)

    @r.post("/v1/segments", status_code=201)
    def create(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.create, body, subject, rule_decision(subject))

    @r.post("/v1/segments/estimate")
    def estimate(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        res = call(svc.estimate, body.get("rule") or body, rule_decision(subject))
        store.audit("segment.estimated", {"user": subject.user_id, "users": res["users"]})          # 預估只記次數，不記規則明細
        return res

    @r.post("/v1/segments/imports", status_code=201)
    def create_import(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.imports.create, body, subject)

    @r.get("/v1/segments/imports/{id}")
    def get_import(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.imports.get, id)

    @r.post("/v1/segments/tick")
    def tick(now: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        t = datetime.fromisoformat(now) if now else None
        out = call(svc.tick, subject, lambda seg: authorize(Subject(seg["owner"], ["data_analyst"]), "use", "users"), t)     # 例行計算以「無行級限制」的身分編譯：分群結果是全量名單，查詢端再套行級
        return {"computed": out, "purged": svc.purge_expired(subject)}

    @r.get("/v1/segments/{id}")
    def get(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.get, id)

    @r.put("/v1/segments/{id}")
    def update(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.update, id, body, subject, rule_decision(subject))

    @r.post("/v1/segments/{id}/preview")
    def preview(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        res = call(svc.preview, id, rule_decision(subject))
        store.audit("segment.previewed", {"user": subject.user_id, "segment": id, "users": res["users"]})
        return res

    @r.post("/v1/segments/{id}/submit")
    def submit(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.submit, id, subject)

    @r.post("/v1/segments/{id}/approve")
    def approve(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "approve"); body = body or {}
        return call(svc.approve, id, bool(body.get("approve", True)), str(body.get("note") or ""), subject)

    @r.post("/v1/segments/{id}/compute", status_code=202)
    def compute(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); body = body or {}
        seg = call(svc.get, id)
        d = authorize(Subject(seg["owner"], ["data_analyst"]), "use", "users")       # 同 tick：全量名單
        return call(svc.compute, id, subject, d, body.get("basetime"), "backfill" if body.get("basetime") else "manual")

    @r.get("/v1/segments/{id}/versions")
    def versions(id: str, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return call(svc.versions, id)

    @r.get("/v1/segments/{id}/versions/{basetime}/members")
    def members(id: str, basetime: str, page_size: int = Query(50), cursor: str | None = None, columns: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        d = authorize(subject, "use", "users")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        rule, ver = call(svc.members_rule, id, basetime)
        try:
            spec = parse_list({"page_size": page_size, "cursor": cursor, "columns": [c for c in (columns or "").split(",") if c] or None}, svc.cat)
            uq = parse_users(rule, svc.cat); c = compile_list(uq, spec, svc.cat, d)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if svc.ch is None:
            raise HTTPException(503, "此環境未連接 ClickHouse")
        rows = run_rows(svc.ch, c)
        total = ver["users"] if spec.cursor is None else spec.cursor.get("t")
        if spec.cursor is None and total is None:
            ce = compile_estimate(uq, svc.cat, d); total = int(run_rows(svc.ch, ce)[0]["users"])
        store.audit("segment.members_listed", {"user": subject.user_id, "segment": id, "version": ver["version"], "rows": len(rows), "masked": list(c.masked)})
        return {"rows": [{k: v for k, v in x.items() if k not in ("_s", "_u")} for x in rows], "columns": [x for x in c.columns if x not in ("_s", "_u")], "masked": c.masked,
                "next_cursor": next_cursor(rows, spec, total), "total": total, "sql": c.sql, "lineage": c.lineage, "cost": {"rows": len(rows), "layer": c.layer}, "warnings": c.warnings, "version": ver}

    @r.post("/v1/segments/{id}/pause")
    def pause(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.pause, id, subject)

    @r.post("/v1/segments/{id}/copy", status_code=201)
    def copy(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.copy, id, subject, rule_decision(subject), (body or {}).get("name"))

    @r.get("/v1/segments/{id}/lineage")
    def lineage(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.lineage, id)

    @r.post("/v1/segments/{id}/recycle")
    def recycle(id: str, force: bool = Query(False), subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.recycle, id, subject, force)

    @r.post("/v1/segments/{id}/restore")
    def restore(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.restore, id, subject)

    @r.delete("/v1/segments/{id}/purge", status_code=204)
    def purge(id: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject, "admin")
        call(svc.purge, id, subject)
        return Response(status_code=204)

    return r
