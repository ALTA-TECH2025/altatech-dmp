"""OIDC 授權碼 + PKCE（Keycloak 或 Microsoft Entra ID v2.0）。後端當機密用戶端（BFF）：token 不進瀏覽器，只發 session cookie。

流程：
  1. begin()      → 產 state／nonce／code_verifier，回 IdP 授權網址；三者綁在「登入前 session」（cookie dmp_login，10 分鐘）
  2. callback()   → 比對 state → 以 code + code_verifier 換 token → 驗 ID token（簽章 JWKS、iss、aud、exp、nonce、amr、tid）→ Identity
  3. 群組：ID token 的 groups claim；Entra 超過 200 群組時 token 不帶 groups（overage），改呼叫 Graph /me/memberOf
網路呼叫全部透過注入的 http（callable），自測用假 IdP。
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import jwt  # PyJWT（MIT）
from jwt import PyJWK


@dataclass
class Identity:
    """兩種登入路徑（OIDC／LDAP）的共同輸出。subject 穩定不變（Entra oid／Keycloak sub／AD objectGUID），username 可變。"""
    provider: str
    subject: str
    username: str
    email: str
    name: str
    groups: list[str]
    amr: list[str] = field(default_factory=list)     # 認證方法（pwd、mfa、otp…）
    auth_time: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoginState:
    provider: str
    state: str
    nonce: str
    code_verifier: str
    created_at: float
    return_to: str = "/"


class OIDCError(Exception):
    pass


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class OIDCProvider:
    def __init__(self, key: str, cfg: dict[str, Any], http: Callable[..., dict[str, Any]], secrets_resolver: Callable[[str], str], now: Callable[[], float] = time.time):
        """http(method, url, data=None, headers=None) → dict（JSON）；secrets_resolver(secret_ref) → 明文（只在執行節點）。"""
        self.key, self.cfg, self.http, self.secret, self.now = key, cfg, http, secrets_resolver, now
        self._meta: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_at = 0.0

    # ---- 發現文件與金鑰 ----
    @property
    def issuer(self) -> str:
        iss = self.cfg["issuer"]
        if "{tenant_id}" in iss:
            iss = iss.replace("{tenant_id}", self.secret(self.cfg["tenant_id_ref"]))
        return iss

    @property
    def client_id(self) -> str:
        return self.cfg["client_id"] if "client_id" in self.cfg else self.secret(self.cfg["client_id_ref"])

    def metadata(self) -> dict[str, Any]:
        if self._meta is None:
            self._meta = self.http("GET", self.issuer.rstrip("/") + "/.well-known/openid-configuration")
        return self._meta

    def jwks(self, force: bool = False) -> dict[str, Any]:
        if self._jwks is None or force or self.now() - self._jwks_at > 3600:
            self._jwks = self.http("GET", self.metadata()["jwks_uri"]); self._jwks_at = self.now()
        return self._jwks

    # ---- 1. 開始登入 ----
    def begin(self, return_to: str = "/") -> tuple[str, LoginState]:
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        st = LoginState(self.key, secrets.token_urlsafe(24), secrets.token_urlsafe(24), verifier, self.now(), return_to if return_to.startswith("/") else "/")
        params = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.cfg["redirect_uri"], "scope": " ".join(self.cfg.get("scopes", ["openid"])),
                  "state": st.state, "nonce": st.nonce, "code_challenge": challenge, "code_challenge_method": self.cfg.get("pkce", "S256")}
        if self.key == "entra":
            params["response_mode"] = "query"; params["prompt"] = "select_account"
        return self.metadata()["authorization_endpoint"] + "?" + urlencode(params), st

    # ---- 2. 回呼 ----
    def callback(self, params: dict[str, str], st: LoginState, max_age_s: int = 600) -> Identity:
        if "error" in params:
            raise OIDCError(f"IdP 回傳錯誤：{params.get('error')} {params.get('error_description', '')[:200]}")
        if params.get("state") != st.state or self.now() - st.created_at > max_age_s:
            raise OIDCError("state 不符或登入逾時（請重新登入）")
        tok = self.http("POST", self.metadata()["token_endpoint"],
                        data={"grant_type": "authorization_code", "code": params["code"], "redirect_uri": self.cfg["redirect_uri"], "client_id": self.client_id,
                              "client_secret": self.secret(self.cfg["client_secret_ref"]), "code_verifier": st.code_verifier})
        claims = self.verify_id_token(tok["id_token"], st.nonce)
        groups = list(claims.get(self.cfg.get("groups_claim", "groups")) or [])
        if not groups and self.cfg.get("groups_overage") == "graph_memberof" and (claims.get("_claim_names") or {}).get("groups"):
            groups = self._graph_groups(tok.get("access_token", ""))
        return Identity(self.key, str(claims.get("oid") or claims["sub"]), str(claims.get("preferred_username") or claims.get("email") or claims["sub"]),
                        str(claims.get("email") or claims.get("preferred_username") or ""), str(claims.get("name") or ""), groups,
                        list(claims.get("amr") or []), int(claims.get("auth_time") or claims.get("iat") or 0), {k: v for k, v in claims.items() if k not in ("groups",)})

    def verify_id_token(self, token: str, nonce: str) -> dict[str, Any]:
        try:
            hdr = jwt.get_unverified_header(token)
            key = self._key_for(hdr.get("kid"))
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.client_id, issuer=self.issuer,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_exp": False, "verify_iat": False})
        except jwt.PyJWTError as e:
            raise OIDCError(f"ID token 驗證失敗：{e}") from None
        t = self.now()                                    # 時間用注入的時鐘（可測）；容許 60 秒時鐘偏移
        if claims["exp"] < t - 60 or claims["iat"] > t + 60:
            raise OIDCError("ID token 已過期或時間不合理")
        if claims.get("nonce") != nonce:
            raise OIDCError("nonce 不符")
        if self.cfg.get("tid_check") and claims.get("tid") and claims["tid"] not in self.issuer:
            raise OIDCError("租戶不符")
        need = set(self.cfg.get("require_amr") or [])
        if need and not need & set(claims.get("amr") or []):
            raise OIDCError(f"此應用需要 {sorted(need)}（請在 IdP 完成多因子驗證）")
        return claims

    def _key_for(self, kid: str | None) -> Any:
        for attempt in (False, True):                 # 金鑰輪替：找不到 kid 就重抓一次 JWKS
            for k in self.jwks(force=attempt).get("keys", []):
                if k.get("kid") == kid:
                    return PyJWK.from_dict(k).key
        raise OIDCError(f"JWKS 無此 kid：{kid}")

    def _graph_groups(self, access_token: str) -> list[str]:
        out: list[str] = []
        url: str | None = "https://graph.microsoft.com/v1.0/me/memberOf?$select=id,displayName&$top=999"
        while url:
            page = self.http("GET", url, headers={"Authorization": f"Bearer {access_token}"})
            out += [g["id"] for g in page.get("value", []) if g.get("id")]
            url = page.get("@odata.nextLink")
        return out

    # ---- 3. 登出（RP-initiated）----
    def logout_url(self, id_token_hint: str | None = None) -> str | None:
        ep = self.metadata().get("end_session_endpoint")
        if not ep:
            return None
        p = {"post_logout_redirect_uri": self.cfg.get("post_logout_redirect_uri", "/"), "client_id": self.client_id}
        if id_token_hint: p["id_token_hint"] = id_token_hint
        return ep + "?" + urlencode(p)
