"""受眾推送路由（openapi Activation）：目的地（segments.approve 或 integration.admin）、推送單（segments.use 建立；本人或審批者執行／暫停／刪除）、交付紀錄（segments.view；重放需 approve）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from ..activation import ActivationService, Conflict
from ..security.policy import Subject, authorize
from .store import Store


def build_router(store: Store, get_subject: Any, svc: ActivationService) -> APIRouter:
    r = APIRouter()

    def need(subject: Subject, action: str) -> Any:
        d = authorize(subject, action, "segments")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return d

    def need_dest_admin(subject: Subject) -> None:
        if not (authorize(subject, "approve", "segments").allowed or authorize(subject, "admin", "integration").allowed):
            raise HTTPException(403, "目的地需數位經營（segments.approve）或資訊管理（integration.admin）")

    def can_approve(subject: Subject) -> bool:
        return authorize(subject, "approve", "segments").allowed

    def call(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"找不到：{e}") from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    # ---- 目的地
    @r.get("/v1/destinations")
    def destinations(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return svc.destinations()

    @r.post("/v1/destinations", status_code=201)
    def create_destination(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view"); need_dest_admin(subject)
        return call(svc.create_destination, body, subject)

    @r.get("/v1/destinations/{id}")
    def destination(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(lambda: svc._with_stats(svc.destination(id)))

    @r.put("/v1/destinations/{id}")
    def update_destination(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view"); need_dest_admin(subject)
        return call(svc.update_destination, id, body, subject)

    @r.post("/v1/destinations/{id}/pause")
    def pause_destination(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view"); need_dest_admin(subject)
        return call(svc.pause_destination, id, subject)

    @r.post("/v1/destinations/{id}/test")
    def test_destination(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view"); need_dest_admin(subject)
        return call(svc.test_destination, id, subject)

    @r.post("/v1/destinations/{id}/rotate-secret")
    def rotate(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view"); need_dest_admin(subject)
        return call(svc.rotate_secret, id, body, subject)

    # ---- 推送單
    @r.get("/v1/pushes")
    def pushes(segment_id: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return svc.pushes(segment_id)

    @r.post("/v1/audiences/{id}/push", status_code=202)
    def create_push(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        if body.get("mode") == "full" and not can_approve(subject):
            p = call(svc.create_push, id, body, subject, False)       # 全量：一律開審批單
            return p
        return call(svc.create_push, id, body, subject, can_approve(subject))

    @r.get("/v1/pushes/{id}")
    def push(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return call(svc.push_detail, id)

    def mine_or_approver(subject: Subject, id_: str) -> dict[str, Any]:
        p = call(svc.push, id_)
        if p["owner"] != subject.user_id and not can_approve(subject):
            raise HTTPException(403, "只有建立者或有審批權限的身分能操作推送單")
        return p

    @r.post("/v1/pushes/{id}/run", status_code=202)
    def run(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); mine_or_approver(subject, id)
        return call(svc.run, id, subject)

    @r.post("/v1/pushes/{id}/pause")
    def pause(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); mine_or_approver(subject, id)
        return call(svc.pause_push, id, subject)

    @r.delete("/v1/pushes/{id}", status_code=204)
    def delete(id: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject, "use"); mine_or_approver(subject, id)
        call(svc.delete_push, id, subject)
        return Response(status_code=204)

    @r.post("/v1/activation/tick")
    def tick(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "approve")
        return svc.tick(subject)

    # ---- 交付
    @r.get("/v1/deliveries")
    def deliveries(audience_id: str | None = None, status: str | None = None, destination_id: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return svc.deliveries(audience_id, status, destination_id)

    @r.post("/v1/deliveries/{id}/replay", status_code=202)
    def replay(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "approve")
        return call(svc.replay, id, subject)

    return r
