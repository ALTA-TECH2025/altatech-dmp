"""資料流與效能路由（/v1/ops/*、/metrics）。檢視需 governance.view；重跑需 governance.admin（審計 batch.rerun）。
/metrics 給叢集內 Prometheus（ServiceMonitor）抓：Bearer DMP_METRICS_TOKEN；沒有 token 時退回 session（governance.view）。"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..ops import metrics as M
from ..ops.notify import Notifier, NotifyError
from ..ops.service import OpsService
from ..security.policy import Subject, authorize


def build_router(store: Any, get_subject: Any, svc: OpsService, metrics_token: str | None = None, notifier: Notifier | None = None) -> APIRouter:
    r = APIRouter()

    def need(subject: Subject, action: str) -> None:
        d = authorize(subject, action, "governance")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    @r.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        if metrics_token and auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), metrics_token):
            pass
        else:
            try:
                need(get_subject(request), "view")
            except HTTPException as e:
                raise HTTPException(401 if e.status_code == 401 else 403, "metrics 需要 Bearer DMP_METRICS_TOKEN 或 governance.view 的 session") from None
        return Response(M.REG.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @r.get("/v1/ops/pipeline")
    def pipeline(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return svc.pipeline()

    @r.get("/v1/ops/query-perf")
    def query_perf(window: str = Query("24h", pattern="^(1h|24h|7d)$"), subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return svc.query_perf(window)

    @r.get("/v1/ops/batch-jobs")
    def batch_jobs(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return svc.batch_jobs()

    @r.post("/v1/ops/batch-jobs/{name}/rerun", status_code=202)
    def rerun(name: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        try:
            return svc.rerun(name, subject)
        except KeyError:
            raise HTTPException(404, f"沒有可重跑的作業：{name}（只有同步任務可重跑；例行作業由排程自動執行）") from None

    @r.get("/v1/ops/series")
    def series(hours: int = Query(6, ge=1, le=24), subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        if hours not in (1, 6, 24):
            raise HTTPException(422, "hours 只能是 1、6、24")
        return svc.series(hours)

    @r.get("/v1/ops/alerts")
    def alerts(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return svc.alerts()

    def nf() -> Notifier:
        if notifier is None:
            raise HTTPException(503, "通知服務未啟用")
        return notifier

    @r.get("/v1/ops/notify")
    def notify_get(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        return nf().config()

    @r.put("/v1/ops/notify")
    def notify_put(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        try:
            return nf().update(body, subject)
        except NotifyError as e:
            raise HTTPException(400, str(e)) from None

    @r.post("/v1/ops/notify/test")
    def notify_test(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        try:
            return nf().test(str(body.get("channel") or ""), subject)
        except NotifyError as e:
            raise HTTPException(409, str(e)) from None

    @r.get("/v1/ops/notify/history")
    def notify_history(limit: int = Query(50, ge=1, le=200), subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return nf().history(limit)

    @r.post("/v1/ops/notify/alertmanager")
    def alertmanager(body: dict[str, Any], request: Request) -> dict[str, Any]:
        auth = request.headers.get("authorization", "")
        if not (metrics_token and auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), metrics_token)):
            raise HTTPException(401, "Alertmanager webhook 需要 Bearer DMP_METRICS_TOKEN")
        out = nf().from_alertmanager(body)
        return {"ok": True, "notifications": [{"id": x["id"], "channel": x["channel"], "kind": x["kind"], "status": x["status"]} for x in out]}

    return r
