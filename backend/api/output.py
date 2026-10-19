"""資料輸出任務路由（openapi /v1/exports/tasks*）：全部需 integration.view；建立／預覽／編輯／執行／暫停另依輸出內容需 events.export／users.export（服務層檢查；數據分析、數位經營有匯出權，資訊管理只能看）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..approvals.engine import Conflict
from ..output import OutputService
from ..security.policy import Subject, authorize
from .store import Store


def build_router(store: Store, get_subject: Any, svc: OutputService) -> APIRouter:
    r = APIRouter()

    def need(subject: Subject, action: str) -> None:
        d = authorize(subject, action, "integration")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    def call(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"找不到：{e}") from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @r.get("/v1/exports/tasks")
    def list_(status: str | None = None, type: str | None = Query(None), subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return svc.list(subject, status, type)

    @r.post("/v1/exports/tasks", status_code=201)
    def create(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.create, body, subject)

    @r.post("/v1/exports/tasks/preview")
    def preview(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.preview, body, subject)

    @r.get("/v1/exports/tasks/{id}")
    def get(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.detail, id, subject)

    @r.put("/v1/exports/tasks/{id}")
    def update(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.update, id, body, subject)

    @r.delete("/v1/exports/tasks/{id}", status_code=204)
    def delete(id: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject, "view")
        call(svc.delete, id, subject)
        return Response(status_code=204)

    @r.post("/v1/exports/tasks/{id}/run", status_code=202)
    def run(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.run, id, subject, (body or {}).get("partition_date"))

    @r.post("/v1/exports/tasks/{id}/pause")
    def pause(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.pause, id, subject)

    @r.get("/v1/exports/tasks/{id}/runs")
    def runs(id: str, limit: int = 20, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        call(svc.detail, id, subject)
        return svc.runs(id, limit)

    return r
