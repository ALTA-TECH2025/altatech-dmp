"""FastAPI 路由：/auth/*（登入、回呼、LDAP、登出、API token）與 /v1/iam/me。router 只做：驗證 → Policy → 呼叫服務 → 審計；不在這裡寫 SQL。

依賴注入（AuthService）由部署組裝：providers（OIDC／LDAP）、SessionManager、ApiTokens、assignments 讀取。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import yaml  # type: ignore
from fastapi import APIRouter, HTTPException, Request, Response

from ..security.policy import Subject, authorize
from .ldap import LdapError, LdapProvider
from .local import LocalError, LocalProvider
from .mapping import base_roles, map_roles, me_payload
from .oidc import LoginState, OIDCError, OIDCProvider
from .session import ApiTokens, Session, SessionManager

LOGIN_COOKIE = "dmp_login"


@dataclass
class AuthService:
    cfg: dict[str, Any]
    providers: dict[str, OIDCProvider | LdapProvider | LocalProvider]
    sessions: SessionManager
    tokens: ApiTokens | None = None
    assignments: Callable[[], list[dict[str, Any]]] = lambda: []
    login_states: dict[str, LoginState] = field(default_factory=dict)     # 登入前狀態（正式環境：Valkey，TTL 10 分鐘）

    @property
    def mode(self) -> str:
        return self.cfg.get("mode", "broker")

    def visible_providers(self) -> list[dict[str, str]]:
        keys = ["keycloak"] if self.mode == "broker" else ["entra", "ad", "local"]
        return [{"key": k, "cn": self.providers[k].cfg["cn"], "kind": self.providers[k].cfg["kind"]} for k in keys if k in self.providers]

    def finish_login(self, ident: Any, ip: str) -> Session:
        roles, scope, applied = map_roles(ident, self.cfg["role_mapping"], self.assignments(), now=self.sessions.now())
        self.sessions.audit("iam.role_mapping.applied", {"user": f"{ident.provider}:{ident.subject}", "rules": applied, "roles": roles})
        return self.sessions.create(user_id=f"{ident.provider}:{ident.subject}", username=ident.username, name=ident.name, email=ident.email, provider=ident.provider,
                                    roles=roles, groups=ident.groups, amr=ident.amr, auth_time=ident.auth_time, scope=scope, ip=ip, base_roles=base_roles(ident, self.cfg["role_mapping"]))


def _set(resp: Response, headers: list[tuple[str, str]]) -> None:
    for k, v in headers:
        resp.headers.append(k, v)


def current_session(svc: AuthService, req: Request) -> Session:
    sid = req.cookies.get(svc.cfg["session"]["cookie"]["name"])
    s = svc.sessions.resolve(sid, req.client.host if req.client else "")
    if not s:
        raise HTTPException(401, "未登入或 session 已逾時")
    if req.method not in ("GET", "HEAD", "OPTIONS") and not svc.sessions.check_csrf(s, req.headers.get(svc.cfg["session"]["csrf"]["header"])):
        raise HTTPException(403, "CSRF token 不符")
    return s


def build_router(svc: AuthService) -> APIRouter:
    r = APIRouter()

    @r.get("/auth/providers")
    def providers() -> dict[str, Any]:
        return {"mode": svc.mode, "providers": svc.visible_providers()}

    @r.get("/auth/login")
    def login(provider: str, return_to: str = "/") -> Response:
        p = svc.providers.get(provider)
        if not isinstance(p, OIDCProvider) or provider not in {x["key"] for x in svc.visible_providers()}:
            raise HTTPException(400, "未知或未啟用的身分提供者")
        url, st = p.begin(return_to)
        svc.login_states[st.state] = st
        resp = Response(status_code=302, headers={"Location": url})
        resp.headers.append("Set-Cookie", f"{LOGIN_COOKIE}={st.state}; Path=/auth; HttpOnly; SameSite=Lax; Secure; Max-Age=600")
        return resp

    @r.get("/auth/callback")
    def callback(req: Request) -> Response:
        st = svc.login_states.pop(req.cookies.get(LOGIN_COOKIE, ""), None)
        if not st:
            raise HTTPException(400, "登入狀態不存在或已逾時，請重新登入")
        p = svc.providers[st.provider]
        assert isinstance(p, OIDCProvider)
        try:
            ident = p.callback(dict(req.query_params), st)
        except OIDCError as e:
            svc.sessions.audit("auth.login.failed", {"provider": st.provider, "why": str(e)[:120]})
            raise HTTPException(401, str(e)) from None
        s = svc.finish_login(ident, req.client.host if req.client else "")
        resp = Response(status_code=302, headers={"Location": st.return_to if s.roles else "/no-access"})
        _set(resp, svc.sessions.cookie_headers(s))
        resp.headers.append("Set-Cookie", f"{LOGIN_COOKIE}=; Path=/auth; HttpOnly; SameSite=Lax; Secure; Max-Age=0")
        return resp

    @r.post("/auth/ldap")
    def ldap_login(body: dict[str, Any], req: Request, resp: Response) -> dict[str, Any]:
        """帳密登入（direct 模式）：provider=ad → LDAPS bind；provider=local → 本機帳號。mfa=totp 時第一步回 next=totp，第二步帶 totp 真正驗證。"""
        key = str(body.get("provider") or "ad")
        p = svc.providers.get(key)
        if key not in ("ad", "local") or not isinstance(p, LdapProvider | LocalProvider) or svc.mode != "direct":
            raise HTTPException(400, "此環境未啟用帳密登入" if key in ("ad", "local") else "未知的身分提供者")
        ip = req.client.host if req.client else ""
        username = str(body.get("username", ""))
        try:
            ident = p.authenticate(username, str(body.get("password", "")), ip)
        except (LdapError, LocalError) as e:
            svc.sessions.audit("auth.login.failed", {"provider": key, "user": username[:64], "why": str(e)})
            raise HTTPException(401, str(e)) from None
        if p.cfg.get("mfa") == "totp":
            code = str(body.get("totp") or "")
            if not code:
                return {"next": "totp", "message": "請輸入驗證器 6 位數"}       # 第二因子：前端再送一次含 totp 的請求
            check = getattr(p, "check_totp", None)                               # AD 直連的 TOTP 需另接驗證器服務；沒有實作時這裡不放行 mfa
            if check is None or not check(username, code):
                svc.sessions.audit("auth.login.failed", {"provider": key, "user": username[:64], "why": "totp"})
                raise HTTPException(401, "驗證碼錯誤或已使用" if check else "此環境尚未接上 TOTP 驗證器")
            ident.amr = ["pwd", "mfa"]
        s = svc.finish_login(ident, ip)
        _set(resp, svc.sessions.cookie_headers(s))
        return {"ok": True, "roles": s.roles, "return_to": "/" if s.roles else "/no-access"}

    @r.post("/auth/step-up")
    def step_up(body: dict[str, Any], req: Request) -> dict[str, Any]:
        """admin 角色需 MFA 且認證在 15 分鐘內；超過後再輸入一次 TOTP 延展（只對有 TOTP 驗證能力的提供者）。"""
        s = current_session(svc, req)
        p = svc.providers.get(s.provider)
        check = getattr(p, "check_totp", None)
        if check is None:
            raise HTTPException(400, "此登入方式的 step-up 由身分提供者處理，請重新登入")
        if not check(s.username, str(body.get("totp") or "")):
            svc.sessions.audit("auth.step_up.failed", {"user": s.user_id})
            raise HTTPException(401, "驗證碼錯誤或已使用")
        s.step_up_at = int(svc.sessions.now())
        if "mfa" not in s.amr:
            s.amr = [*s.amr, "mfa"]
        svc.sessions.store.put(s)
        svc.sessions.audit("auth.step_up", {"user": s.user_id})
        return {"ok": True, "roles": svc.sessions.subject(s).roles}

    @r.post("/auth/logout")
    def logout(req: Request) -> Response:
        s = current_session(svc, req)
        svc.sessions.logout(s.sid)
        p = svc.providers.get(s.provider)
        url = p.logout_url() if isinstance(p, OIDCProvider) else None
        resp = Response(status_code=200, content='{"ok": true, "idp_logout": ' + ('"' + url + '"' if url else "null") + "}", media_type="application/json")
        _set(resp, svc.sessions.cookie_headers(None))
        return resp

    @r.get("/v1/iam/me")
    def me(req: Request) -> dict[str, Any]:
        s = current_session(svc, req)
        subj = svc.sessions.subject(s)
        roles_full, _ = svc.sessions.effective(s)                     # 目前有效角色（指派撤銷／到期即時反映），不是登入快照
        return me_payload(subj, s, roles_full, svc.mode, step_up_needed="admin" in roles_full and "admin" not in subj.roles)

    @r.post("/auth/token")
    def token(req: Request) -> dict[str, Any]:
        """短效 API token（MCP／CLI）：以現有 session 換發；角色 = 當下有效角色。"""
        s = current_session(svc, req)
        if not svc.tokens:
            raise HTTPException(501, "此環境未設定 API token 簽章金鑰")
        subj = svc.sessions.subject(s)
        svc.sessions.audit("auth.token.issued", {"user": s.user_id, "roles": subj.roles})
        return {"access_token": svc.tokens.issue(s, subj.roles), "token_type": "Bearer", "expires_in": svc.cfg["session"]["api_token"]["ttl_s"]}

    def _row(x: Session, me: Session) -> dict[str, Any]:
        return {"sid": x.sid[:8], "user": x.user_id, "username": x.username, "name": x.name, "provider": x.provider, "roles": x.roles, "amr": x.amr, "created_at": x.created_at, "last_seen": x.last_seen,
                "ip_masked": x.attrs.get("ip", "")[:7] + "…", "current": x.sid == me.sid}

    @r.get("/v1/iam/sessions")
    def sessions(req: Request, scope: str = "me") -> list[dict[str, Any]]:
        """scope=me：本人；scope=all：全平台（iam.admin）。"""
        s = current_session(svc, req)
        subj = svc.sessions.subject(s)
        d = authorize(subj, "admin" if scope == "all" else "view", "iam")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        if scope == "all":
            lister = getattr(svc.sessions.store, "list_all", None)
            if lister is None:
                raise HTTPException(501, "此 session 儲存不支援列全平台")
            return [_row(x, s) for x in lister()]
        return [_row(x, s) for x in svc.sessions.store.list_user(s.user_id)]

    @r.delete("/v1/iam/sessions/{sid}", status_code=204)
    def revoke_session(sid: str, req: Request) -> Response:
        """強制登出：本人的任一個 session，或 iam.admin 對任何人（sid 前 8 碼）。"""
        s = current_session(svc, req)
        subj = svc.sessions.subject(s)
        mine = [x for x in svc.sessions.store.list_user(s.user_id) if x.sid.startswith(sid)]
        target = mine[0] if mine else None
        if target is None:
            if not authorize(subj, "admin", "iam").allowed:
                raise HTTPException(404, "session 不存在")
            lister = getattr(svc.sessions.store, "list_all", None)
            target = next((x for x in (lister() if lister else []) if x.sid.startswith(sid)), None)
            if target is None:
                raise HTTPException(404, "session 不存在")
        svc.sessions.logout(target.sid)
        svc.sessions.audit("auth.session.revoked", {"user": s.user_id, "target": target.user_id, "sid": target.sid[:8], "self": target.user_id == s.user_id})
        return Response(status_code=204)

    return r


def subject_from_request(svc: AuthService, req: Request) -> Subject:
    """其他 router 共用：cookie session 或 Bearer API token → Subject。"""
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and svc.tokens:
        try:
            return svc.tokens.verify(auth[7:])
        except Exception:
            raise HTTPException(401, "API token 無效或過期") from None
    return svc.sessions.subject(current_session(svc, req))


def load_cfg() -> dict[str, Any]:
    from pathlib import Path
    return yaml.safe_load((Path(__file__).resolve().parent / "idp.yaml").read_text(encoding="utf-8"))


def login_redirect(return_to: str) -> str:
    return "/login?" + urlencode({"return_to": return_to})
