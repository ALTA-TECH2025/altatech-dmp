"""FastAPI 應用組裝：auth（登入／session／me）+ iam + analytics。每個 router 只做：驗證 → Policy → 服務 → 審計。

create_app(auth_service, store, ch) 讓測試能注入假 IdP／假 ClickHouse；main.py 由環境變數組裝正式物件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from ..approvals import ApprovalService, ExportService, RevealService
from ..auth.api import AuthService, subject_from_request
from ..auth.api import build_router as auth_router
from ..integration.runner import LocalRunner
from ..integration.scheduler import Scheduler
from ..security.policy import Subject
from ..segments import SegmentService
from . import analytics, dashboards, exports, iam, integration, segments, users
from .store import Store


def create_app(auth: AuthService, store: Store, ch: Any | None = None, segment_service: SegmentService | None = None, export_dir: Any | None = None, approval_service: ApprovalService | None = None,
               scheduler: Scheduler | None = None, secret_writer: Any | None = None) -> FastAPI:
    app = FastAPI(title="AltaTech DMP API", version="0.1.0", docs_url="/v1/docs", openapi_url="/v1/openapi.json")

    approvals = approval_service or ApprovalService(store)
    reveals = RevealService(store, approvals)

    def get_subject(request: Request) -> Subject:
        s = subject_from_request(auth, request)
        one_id = request.path_params.get("one_id") if request.path_params else None
        s.reveals = reveals.active_for(s.user_id, one_id)                       # 揭露中的欄位 → authorize 放行為明文
        if s.reveals and request.url.path.startswith(("/v1/users", "/v1/profiles", "/v1/analytics", "/v1/segments")) and request.method in ("GET", "POST"):
            reveals.note_view(s.user_id, list(s.reveals), request.url.path)      # 每次查看寫審計 mask.revealed
        return s

    # auth 的審計寫進同一個 store（SessionManager 建立時已綁 audit callable；這裡只補 store 的 audit 給 iam／analytics）
    auth.assignments = store.assignments
    app.include_router(auth_router(auth))
    app.include_router(iam.build_router(store, get_subject))
    app.include_router(analytics.build_router(store, get_subject, ch))
    app.include_router(dashboards.build_router(store, get_subject, ch))
    app.include_router(users.build_router(store, get_subject, ch))
    seg_svc = segment_service or SegmentService(store, ch)
    seg_svc.approvals = approvals
    import tempfile
    exp = ExportService(store, ch, approvals, Path(export_dir) if export_dir else Path(tempfile.gettempdir()) / "dmp-exports", members_rule=seg_svc.members_rule)
    app.include_router(segments.build_router(store, get_subject, seg_svc))
    app.include_router(exports.build_router(store, get_subject, approvals, exp, reveals))
    sched = scheduler or Scheduler(Path(tempfile.gettempdir()) / "dmp-sched.db", LocalRunner(ch if hasattr(ch, "insert_json") and not ch.__class__.__name__.startswith("Fake") else None))
    app.include_router(integration.build_router(store, get_subject, sched, secret_writer))
    app.state.scheduler = sched

    @app.exception_handler(PermissionError)
    def _perm(_: Request, e: PermissionError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(ValueError)
    def _val(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "clickhouse": ch is not None, "idp_mode": auth.mode}

    return app


def mount_web(app: FastAPI, dist: Path) -> None:
    """API 同源提供前端建置檔（web/dist）：單機／小型部署不另起 nginx。/v1、/auth、/healthz 以外的 GET → 檔案存在就給檔案，否則回 index.html（SPA 路由）。
    正式環境仍建議 nginx／Ingress 放前面（快取、壓縮、TLS）。"""
    dist = dist.resolve()
    index = dist / "index.html"
    if not index.exists():
        raise FileNotFoundError(f"找不到 {index}：先在 web/ 執行 npm run build")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Any:
        f = (dist / path).resolve() if path else index
        if path and f.is_relative_to(dist) and f.is_file():
            hdr = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {"Cache-Control": "no-cache"}
            return FileResponse(f, headers=hdr)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
