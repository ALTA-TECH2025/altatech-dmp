"""審批單與匯出路由：驗證 → Policy → ApprovalService／ExportService → 審計。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..approvals import ApprovalService, ExportService, RevealService
from ..approvals.engine import Conflict
from ..security.policy import Subject
from .store import Store


def build_router(store: Store, get_subject: Any, approvals: ApprovalService, exports: ExportService, reveals: RevealService) -> APIRouter:
    r = APIRouter()

    def call(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"不存在：{e}") from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @r.get("/v1/approvals")
    def list_(scope: str = Query("to_me"), status: str | None = None, type: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        if scope not in ("to_me", "mine", "all"):
            raise HTTPException(400, "scope 只能是 to_me／mine／all")
        return approvals.list(subject, scope, status, type)

    @r.get("/v1/approvals/{id}")
    def get(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        a = call(approvals.get, id)
        if a["requester"] != subject.user_id and not approvals.can_approve(subject, a["type"]):
            raise HTTPException(403, "不是你的申請，也不是你審批的類型")
        return a

    @r.post("/v1/approvals/{id}/decide")
    def decide(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        body = body or {}
        return call(approvals.decide, id, subject, bool(body.get("approve", True)), str(body.get("note") or ""))

    @r.get("/v1/reveals")
    def reveals_list(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        return reveals.list(subject)

    @r.post("/v1/reveals", status_code=201)
    def reveals_request(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return call(reveals.request, body, subject)

    @r.get("/v1/exports")
    def exports_list(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        return exports.list(subject)

    @r.post("/v1/exports", status_code=201)
    def request(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return call(exports.request, body, subject)

    @r.get("/v1/exports/{id}")
    def export_get(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        e = call(exports.get, id)
        if e["requester"] != subject.user_id and not any(x in subject.roles for x in ("admin", "digital_ops")):
            raise HTTPException(403, "不是你的匯出申請")
        return e

    @r.post("/v1/exports/{id}/run")
    def run(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return call(exports.run, id, subject)

    @r.get("/v1/exports/{id}/download")
    def download(id: str, subject: Subject = Depends(get_subject)) -> FileResponse:
        path, name = call(exports.download, id, subject)
        return FileResponse(path, media_type="text/csv", filename=name)

    return r
