"""FastAPI 應用組裝：auth（登入／session／me）+ iam + analytics。每個 router 只做：驗證 → Policy → 服務 → 審計。

create_app(auth_service, store, ch) 讓測試能注入假 IdP／假 ClickHouse；main.py 由環境變數組裝正式物件。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..auth.api import AuthService, subject_from_request
from ..auth.api import build_router as auth_router
from ..security.policy import Subject
from . import analytics, iam
from .store import Store


def create_app(auth: AuthService, store: Store, ch: Any | None = None) -> FastAPI:
    app = FastAPI(title="AltaTech DMP API", version="0.1.0", docs_url="/v1/docs", openapi_url="/v1/openapi.json")

    def get_subject(request: Request) -> Subject:
        return subject_from_request(auth, request)

    # auth 的審計寫進同一個 store（SessionManager 建立時已綁 audit callable；這裡只補 store 的 audit 給 iam／analytics）
    auth.assignments = store.assignments
    app.include_router(auth_router(auth))
    app.include_router(iam.build_router(store, get_subject))
    app.include_router(analytics.build_router(store, get_subject, ch))

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
