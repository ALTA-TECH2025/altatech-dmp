"""統一錯誤輸出與請求編號（docs/46；契約 backend/api/errors.yaml、openapi components.schemas.Error）。

- 每個請求一個 request_id：F5／前端帶來的 X-Request-Id 合格就沿用，否則產生；回應標頭、Error.request_id、審計與錯誤日誌都帶同一個。
- 只處理 4xx／5xx：正常的 3xx（SSO 302，帶 Location／Set-Cookie）與 2xx 原樣通過。
- HTTPException 沒指定代碼時依 HTTP 狀態給通用碼；要更精確就丟 ApiError(code=…)。
- 下游失敗（UPSTREAM_FAILED、DEPENDENCY_UNAVAILABLE、INTERNAL）只回目錄訊息與 request_id，原始內容只寫日誌。
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CATALOG: dict[str, dict[str, Any]] = {c["code"]: c for c in yaml.safe_load((Path(__file__).with_name("errors.yaml")).read_text(encoding="utf-8"))["codes"]}
BY_STATUS = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "AUTHZ_DENIED", 404: "NOT_FOUND", 409: "CONFLICT", 410: "GONE", 413: "PAYLOAD_TOO_LARGE",
             422: "RULE_VIOLATION", 429: "RATE_LIMITED", 500: "INTERNAL", 501: "NOT_CONFIGURED", 502: "UPSTREAM_FAILED", 503: "DEPENDENCY_UNAVAILABLE", 504: "TIMEOUT"}
SANITIZE = {"UPSTREAM_FAILED", "DEPENDENCY_UNAVAILABLE", "INTERNAL"}       # 只回目錄訊息（docs/46 §4.3）
RID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")
request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
log = logging.getLogger("dmp.api")


class ApiError(HTTPException):
    """帶錯誤碼的 HTTPException：raise ApiError("CSRF_INVALID")；HTTP 狀態取目錄，detail 省略就用目錄訊息。"""
    def __init__(self, code: str, detail: str | None = None, *, params: dict[str, Any] | None = None,
                 errors: list[dict[str, Any]] | None = None, headers: dict[str, str] | None = None) -> None:
        if code not in CATALOG or "api" not in CATALOG[code]["scope"]:
            raise ValueError(f"未登記的 REST 錯誤碼：{code}")
        super().__init__(int(CATALOG[code]["http"]), detail or CATALOG[code]["message"], headers=headers)
        self.code, self.params, self.errors = code, params or {}, errors or []


def code_for(status: int) -> str:
    return BY_STATUS.get(status) or ("INTERNAL" if status >= 500 else "INVALID_ARGUMENT")


def body(status: int, detail: Any, code: str | None = None, params: dict[str, Any] | None = None, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """組 Error 結構。detail 若是舊寫法的 dict（{message, errors, …}）就拆成 detail＋errors[]＋params，訊息仍含前幾條原因（舊前端只讀 detail）。"""
    code = code or code_for(status)
    params, errors = dict(params or {}), list(errors or [])
    if isinstance(detail, dict):
        params.update({k: v for k, v in detail.items() if k not in ("message", "errors", "detail")})
        errs = detail.get("errors") or []
        errors += [{"field": "body", "message": str(x)} for x in errs] if isinstance(errs, list) else []
        detail = str(detail.get("message") or detail.get("detail") or CATALOG[code]["message"]) + ("：" + "；".join(str(x) for x in errs[:5]) if isinstance(errs, list) and errs else "")
    elif not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False) if detail is not None else CATALOG[code]["message"]
    out: dict[str, Any] = {"detail": detail, "code": code, "retryable": bool(CATALOG[code]["retryable"]), "request_id": request_id.get() or new_id()}
    params = {k: v for k, v in params.items() if isinstance(v, (str, int, float, bool, list))}   # schema 只收純量與陣列
    if params: out["params"] = params
    if errors: out["errors"] = errors
    return out


def new_id() -> str:
    return "req-" + secrets.token_hex(8)


def install(app: FastAPI, store: Any) -> None:
    """請求編號中介層＋例外處理；403 照舊寫 authz.denied 審計（多記 code），審計事件都帶 request_id。"""
    raw_audit = store.audit

    def audit(event: str, data: dict[str, Any]) -> Any:
        rid = request_id.get()
        return raw_audit(event, {**data, "request_id": rid} if rid and "request_id" not in data else data)
    store.audit = audit

    def respond(request: Request, status: int, detail: Any, code: str | None, params: dict[str, Any] | None = None,
                errors: list[dict[str, Any]] | None = None, headers: dict[str, str] | None = None, cause: str = "") -> JSONResponse:
        code = code or code_for(status)
        rid = request_id.get()
        if code in SANITIZE:                                                # 原始內容只進日誌，用 request_id 查
            log.error("rid=%s %s %s → %s %s：%s", rid, request.method, request.url.path, status, code, cause or detail)
            detail, params, errors = CATALOG[code]["message"], None, None
        elif CATALOG[code]["log"] != "info":
            log.warning("rid=%s %s %s → %s %s", rid, request.method, request.url.path, status, code)
        if status == 403 and code != "ACCOUNT_LOCKED":                      # 權限拒絕也是審計事件（帳號鎖定已記 auth.login.failed，不重算）
            store.audit("authz.denied", {"user": getattr(request.state, "user_id", ""), "path": request.url.path, "method": request.method, "reason": str(detail)[:200], "code": code})
        return JSONResponse(body(status, detail, code, params, errors), status_code=status, headers=headers)

    @app.middleware("http")
    async def with_request_id(request: Request, call_next: Any) -> Any:
        given = request.headers.get("x-request-id", "")
        token = request_id.set(given if RID_RE.fullmatch(given) else new_id())
        try:
            try:
                resp = await call_next(request)
            except Exception as e:                                          # 未處理例外在這裡轉 500，回應才帶得到同一個 request_id
                resp = respond(request, 500, None, "INTERNAL", cause=f"{type(e).__name__}: {e}")
            resp.headers["X-Request-Id"] = request_id.get()
            return resp
        finally:
            request_id.reset(token)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException) -> Any:
        return respond(request, e.status_code, e.detail, getattr(e, "code", None), getattr(e, "params", None), getattr(e, "errors", None), getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, e: RequestValidationError) -> Any:
        errs = [{"field": ".".join(str(p) for p in x.get("loc", ())), "message": str(x.get("msg", "")), "code": "INVALID_ARGUMENT"} for x in e.errors()[:20]]
        return respond(request, 400, "請求內容不正確：" + "；".join(f"{x['field']} {x['message']}" for x in errs[:3]), "INVALID_ARGUMENT", errors=errs)

    @app.exception_handler(PermissionError)
    async def _perm(request: Request, e: PermissionError) -> Any:
        return respond(request, 403, str(e) or CATALOG["AUTHZ_DENIED"]["message"], "AUTHZ_DENIED")

    @app.exception_handler(ValueError)
    async def _val(request: Request, e: ValueError) -> Any:
        log.info("rid=%s %s %s → 400 ValueError：%s", request_id.get(), request.method, request.url.path, e)   # 可能是函式庫內部訊息，只進日誌（docs/46 §4.7）
        return respond(request, 400, None, "INVALID_ARGUMENT")

    @app.exception_handler(TimeoutError)
    async def _timeout(request: Request, e: TimeoutError) -> Any:
        return respond(request, 504, None, "TIMEOUT")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, e: Exception) -> Any:
        return respond(request, 500, None, "INTERNAL", cause=f"{type(e).__name__}: {e}")
