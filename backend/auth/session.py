"""Session 與 token：伺服器端 session（cookie 只放不透明 id）、CSRF double-submit、給 MCP／排程／CLI 的短效 RS256 API token。

為什麼不把 IdP 的 access token 交給瀏覽器：BFF 模式下瀏覽器只有 httpOnly cookie，XSS 拿不到 token；IdP token 只在後端用一次（換 Identity）就丟。
Session 存放：骨架用記憶體；正式環境用 PostgreSQL iam_sessions（多副本共用、可撤銷、可列出同帳號的所有 session）。
"""
from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import jwt

from ..security.policy import Subject


@dataclass
class Session:
    sid: str
    user_id: str                     # provider:subject
    username: str
    name: str
    email: str
    provider: str
    roles: list[str]
    groups: list[str]
    amr: list[str]
    auth_time: int
    created_at: float
    last_seen: float
    csrf: str
    scope: dict[str, Any] = field(default_factory=dict)   # 自訂角色的範圍（branches / projects / segment_limit / lookback_days）
    step_up_at: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)


class SessionStore(Protocol):
    def get(self, sid: str) -> Session | None: ...
    def put(self, s: Session) -> None: ...
    def delete(self, sid: str) -> None: ...
    def list_user(self, user_id: str) -> list[Session]: ...


class MemoryStore:
    def __init__(self) -> None:
        self.d: dict[str, Session] = {}

    def get(self, sid: str) -> Session | None:
        return self.d.get(sid)

    def put(self, s: Session) -> None:
        self.d[s.sid] = s

    def delete(self, sid: str) -> None:
        self.d.pop(sid, None)

    def list_user(self, user_id: str) -> list[Session]:
        return sorted([s for s in self.d.values() if s.user_id == user_id], key=lambda s: s.created_at)

    def list_all(self) -> list[Session]:
        """全平台 session（權限中心 › session 分頁，iam.admin）。"""
        return sorted(self.d.values(), key=lambda s: s.last_seen, reverse=True)


class SessionManager:
    def __init__(self, cfg: dict[str, Any], store: SessionStore, audit: Callable[[str, dict[str, Any]], None], now: Callable[[], float] = time.time,
                 admin_requires: dict[str, Any] | None = None, assignments: Callable[[], list[dict[str, Any]]] | None = None,
                 base_roles_of: Callable[[Session], list[str] | None] | None = None):
        """cfg = idp.yaml 的 session 段；admin_requires = role_mapping.admin_requires（admin 需 MFA 且認證夠新）。
        assignments／base_roles_of 給了就每次請求重算有效授權（見 effective）；不給則沿用登入快照（單元測試用）。"""
        self.cfg, self.store, self.audit, self.now = cfg, store, audit, now
        self.admin_requires = admin_requires or {}
        self.assignments, self.base_roles_of = assignments, base_roles_of

    def create(self, *, user_id: str, username: str, name: str, email: str, provider: str, roles: list[str], groups: list[str], amr: list[str], auth_time: int,
               scope: dict[str, Any] | None = None, ip: str = "", base_roles: list[str] | None = None) -> Session:
        t = self.now()
        s = Session(secrets.token_urlsafe(32), user_id, username, name, email, provider, roles, groups, amr, auth_time, t, t, secrets.token_urlsafe(24), scope or {},
                    attrs={"ip": ip, **({"base_roles": list(base_roles)} if base_roles is not None else {})})
        olds = self.store.list_user(user_id)
        for old in olds[: max(0, len(olds) + 1 - int(self.cfg.get("concurrent_sessions", 3)))]:     # 超過同時 session 上限 → 踢最舊的
            self.store.delete(old.sid); self.audit("auth.session.evicted", {"user": user_id, "sid": old.sid[:8]})
        self.store.put(s)
        self.audit("auth.login.success" if roles else "auth.login.no_role", {"user": user_id, "provider": provider, "roles": roles, "amr": amr, "ip_masked": _mask_ip(ip)})
        return s

    def resolve(self, sid: str | None, ip: str = "") -> Session | None:
        """每個請求：找 session、檢查閒置與絕對逾時、（可選）IP 綁定、更新 last_seen。"""
        if not sid: return None
        s = self.store.get(sid)
        if not s: return None
        t = self.now()
        if t - s.last_seen > int(self.cfg["idle_timeout_s"]) or t - s.created_at > int(self.cfg["absolute_timeout_s"]):
            self.store.delete(sid); self.audit("auth.session.expired", {"user": s.user_id}); return None
        if self.cfg.get("bind_ip") and s.attrs.get("ip") and ip and s.attrs["ip"] != ip:
            self.store.delete(sid); self.audit("auth.session.ip_changed", {"user": s.user_id}); return None
        s.last_seen = t; self.store.put(s)
        return s

    def logout(self, sid: str) -> None:
        s = self.store.get(sid)
        if s:
            self.store.delete(sid); self.audit("auth.logout", {"user": s.user_id})

    def check_csrf(self, s: Session, header_value: str | None) -> bool:
        return bool(header_value) and hmac.compare_digest(s.csrf, header_value or "")

    def cookie_headers(self, s: Session | None) -> list[tuple[str, str]]:
        """Set-Cookie：session（httpOnly）與 csrf（JS 可讀，double-submit）。s=None 時清除。"""
        c = self.cfg["cookie"]; cs = self.cfg["csrf"]
        base = f"; Path={c.get('path', '/')}; SameSite={c.get('same_site', 'Lax')}" + ("; Secure" if c.get("secure", True) else "")
        if s is None:
            kill = "; Max-Age=0"
            return [("Set-Cookie", f"{c['name']}=;{base}; HttpOnly{kill}"), ("Set-Cookie", f"{cs['cookie']}=;{base}{kill}")]
        return [("Set-Cookie", f"{c['name']}={s.sid}{base}; HttpOnly"), ("Set-Cookie", f"{cs['cookie']}={s.csrf}{base}")]

    def effective(self, s: Session) -> tuple[list[str], dict[str, Any]]:
        """目前有效的角色與範圍（每個請求都算，不用登入快照）：基底角色（登入時的群組映射；本機帳號則重讀帳號，停用／改角色即時生效）
        + 現在的本機指派疊加（新增、撤銷、到期、deny、scope 變更都立刻反映）。沒接 assignments 時（單元測試）才沿用 session 快照。"""
        from .mapping import overlay_assignments
        base = list(s.attrs.get("base_roles", s.roles))
        if self.base_roles_of is not None:
            b = self.base_roles_of(s)
            if b is None:                                             # 帳號已停用／鎖定／刪除：沒有任何角色
                return [], {}
            base = list(b)
        if self.assignments is None:
            return list(s.roles), dict(s.scope or {})
        roles, scope, _ = overlay_assignments((s.user_id, s.username), base, self.assignments(), self.now())
        return roles, scope

    def subject(self, s: Session) -> Subject:
        """Session → Policy Engine 的 Subject：有效角色（effective）再套 admin 條件（需 MFA 且認證夠新，否則 admin 角色暫時拿掉）。"""
        roles, sc = self.effective(s)
        req = self.admin_requires
        if "admin" in roles and req:
            fresh = self.now() - max(s.auth_time, s.step_up_at) <= int(req.get("max_auth_age_s", 900))
            mfa_ok = not req.get("amr") or set(req["amr"]) & set(s.amr)
            if not (fresh and mfa_ok):
                roles.remove("admin")
        return Subject(s.user_id, roles, branches=list(sc.get("branches", [])), agents=list(sc.get("agents", [])))


# ---- 給 MCP／排程／CLI 的短效 API token（由 session 換發；驗證不用查 DB）----
class ApiTokens:
    def __init__(self, cfg: dict[str, Any], private_pem: str, public_pem: str, now: Callable[[], float] = time.time):
        self.cfg, self.priv, self.pub, self.now = cfg, private_pem, public_pem, now

    def issue(self, s: Session, roles: list[str]) -> str:
        t = int(self.now())
        return jwt.encode({"iss": self.cfg["issuer"], "aud": self.cfg["audience"], "sub": s.user_id, "name": s.username, "roles": roles, "sid": s.sid[:8],
                           "iat": t, "exp": t + int(self.cfg["ttl_s"])}, self.priv, algorithm=self.cfg.get("alg", "RS256"))

    def verify(self, token: str) -> Subject:
        c = jwt.decode(token, self.pub, algorithms=[self.cfg.get("alg", "RS256")], audience=self.cfg["audience"], issuer=self.cfg["issuer"], options={"verify_exp": False, "verify_iat": False})
        if c["exp"] < self.now() - 30:
            raise jwt.ExpiredSignatureError("API token 已過期")
        return Subject(c["sub"], list(c.get("roles", [])))


def _mask_ip(ip: str) -> str:
    p = ip.split(".")
    return ".".join(p[:3] + ["x"]) if len(p) == 4 else (ip[:8] + "…" if ip else "")
