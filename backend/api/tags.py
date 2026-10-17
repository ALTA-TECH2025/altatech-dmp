"""標籤路由（openapi Segments：/v1/tags*）：驗證 → Policy（segments.view／use／approve）→ TagService → 審計。規則編譯用 users.use 的 Decision；SQL 標籤另需自定義 SQL 權限。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..security.policy import RBAC, Subject, authorize
from ..sql import sandbox
from ..tags import Conflict, TagService
from ..tags.service import CUSTOM_FIELDS, DIRECTORIES
from .store import Store


def build_router(store: Store, get_subject: Any, svc: TagService) -> APIRouter:
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
            raise HTTPException(404, f"標籤不存在：{e}") from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except (ValueError, sandbox.SandboxError) as e:
            raise HTTPException(400, str(e)) from None

    def rule_decision(subject: Subject, body: dict[str, Any] | None = None, existing: dict[str, Any] | None = None) -> Any:
        d = authorize(subject, "use", "users")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        kind = (body or {}).get("kind") or (existing or {}).get("kind")
        if kind == "sql" and not sandbox.sandbox_allowed(subject.roles, RBAC["roles"]):
            raise HTTPException(403, "SQL 標籤需自定義 SQL 權限（數據分析、資訊管理、系統管理員）")
        return d

    def mine_or_approver(subject: Subject, t: dict[str, Any]) -> None:
        if t["owner"] != subject.user_id and not authorize(subject, "approve", "segments").allowed:
            raise HTTPException(403, "只有建立者或有審批權限的身分能操作")

    @r.get("/v1/tags")
    def list_(q: str | None = None, status: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return svc.list(q, status)

    @r.get("/v1/tags/custom-fields")
    def custom_fields(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return {"fields": CUSTOM_FIELDS, "directories": DIRECTORIES}

    @r.post("/v1/tags/tick")
    def tick(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "approve")
        return call(svc.tick, subject, lambda t: authorize(Subject(t["owner"], ["data_analyst"]), "use", "users"))

    @r.post("/v1/tags", status_code=201)
    def create(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return call(svc.create, body, subject, rule_decision(subject, body))

    @r.get("/v1/tags/{id}")
    def get(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(lambda: svc._public(svc.get(id)))

    @r.put("/v1/tags/{id}")
    def update(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); t = call(svc.get, id); mine_or_approver(subject, t)
        return call(svc.update, id, body, subject, rule_decision(subject, body, t))

    @r.get("/v1/tags/{id}/distribution")
    def distribution(id: str, basetime: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.distribution, id, basetime)

    @r.get("/v1/tags/{id}/versions")
    def versions(id: str, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return call(svc.versions, id)

    @r.get("/v1/tags/{id}/lineage")
    def lineage(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(lambda: svc._public(svc.get(id))["lineage"])

    @r.post("/v1/tags/{id}/compute", status_code=202)
    def compute(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); t = call(svc.get, id); mine_or_approver(subject, t)
        return call(svc.compute, id, subject, rule_decision(subject, None, t), (body or {}).get("basetime"))

    @r.post("/v1/tags/{id}/approve")
    def approve(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "approve"); body = body or {}
        return call(svc.approve, id, bool(body.get("approve", True)), str(body.get("note") or ""), subject)

    @r.post("/v1/tags/{id}/submit")
    def submit(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); t = call(svc.get, id); mine_or_approver(subject, t)
        return call(svc.submit, id, subject)

    @r.post("/v1/tags/{id}/pause")
    def pause(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); t = call(svc.get, id); mine_or_approver(subject, t)
        return call(svc.pause, id, subject)

    @r.post("/v1/tags/{id}/recycle")
    def recycle(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); t = call(svc.get, id); mine_or_approver(subject, t)
        return call(svc.recycle, id, subject)

    @r.post("/v1/tags/{id}/restore")
    def restore(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); t = call(svc.get, id); mine_or_approver(subject, t)
        return call(svc.restore, id, subject)

    return r
