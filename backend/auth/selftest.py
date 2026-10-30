"""登入整合自測（不連網）：假 Entra ID 與假 Keycloak（JWKS + token endpoint；AD 經 Keycloak User Federation）、session／CSRF／逾時、群組 → 角色、admin step-up、API token、FastAPI 路由。

用法：python3 -m backend.auth
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
import yaml  # type: ignore
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ..security.policy import authorize
from .mapping import grants_of, map_roles, masking_of
from .oidc import OIDCError, OIDCProvider
from .session import ApiTokens, MemoryStore, SessionManager

CFG = yaml.safe_load((Path(__file__).resolve().parent / "idp.yaml").read_text(encoding="utf-8"))
TENANT = "11111111-2222-3333-4444-555555555555"
SECRETS = {"kv/data/dmp/auth/entra#tenant_id": TENANT, "kv/data/dmp/auth/entra#client_id": "app-dmp", "kv/data/dmp/auth/entra#client_secret": "s3cret",
           "kv/data/dmp/auth/keycloak#client_secret": "kc-secret"}


class Clock:
    def __init__(self) -> None: self.t = float(int(time.time()))
    def __call__(self) -> float: return self.t


def rsa_pair() -> tuple[str, str, dict[str, Any]]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key())); jwk["kid"] = "k1"; jwk["use"] = "sig"; jwk["alg"] = "RS256"
    return priv, pub, jwk


class FakeEntra:
    """假 IdP：發現文件、JWKS、token endpoint（依 code 決定 claims）。keycloak=True 時發 Keycloak 形狀的 claims（sub、群組名，無 oid／tid）。"""
    def __init__(self, issuer: str, priv: str, jwk: dict[str, Any], clock: Clock, client_id: str = "app-dmp", client_secret: str = "s3cret", keycloak: bool = False):
        self.issuer, self.priv, self.jwk, self.clock = issuer, priv, jwk, clock
        self.client_id, self.client_secret, self.keycloak = client_id, client_secret, keycloak
        self.codes: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def http(self, method: str, url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(f"{method} {url.split('?')[0]}")
        if url.endswith("/.well-known/openid-configuration"):
            return {"authorization_endpoint": self.issuer + "/authorize", "token_endpoint": self.issuer + "/token", "jwks_uri": self.issuer + "/keys", "end_session_endpoint": self.issuer + "/logout"}
        if url.endswith("/keys"):
            return {"keys": [self.jwk]}
        if url.endswith("/token"):
            assert data and data["grant_type"] == "authorization_code" and data["code_verifier"] and data["client_secret"] == self.client_secret
            spec = self.codes[data["code"]]
            t = int(self.clock())
            claims = {"iss": self.issuer, "aud": self.client_id, "sub": "sub-" + spec["oid"], "oid": spec["oid"], "tid": TENANT, "preferred_username": spec["upn"], "name": spec["name"],
                      "email": spec["upn"], "groups": spec.get("groups", []), "amr": spec.get("amr", ["pwd", "mfa"]), "iat": t, "exp": t + 3600, "auth_time": t, "nonce": spec["nonce"]}
            if self.keycloak:
                claims.pop("oid"); claims.pop("tid")
            if spec.get("overage"):
                claims.pop("groups"); claims["_claim_names"] = {"groups": "src1"}
            return {"id_token": jwt.encode(claims, self.priv, algorithm="RS256", headers={"kid": "k1"}), "access_token": "at-" + spec["oid"], "token_type": "Bearer"}
        if "graph.microsoft.com" in url:
            assert headers and headers["Authorization"].startswith("Bearer at-")
            return {"value": [{"id": "1c7b1c9a-0000-4000-8000-000000000003", "displayName": "DMP-Analysts"}, {"id": "zzz", "displayName": "Everyone"}]}
        raise AssertionError(url)


def main() -> None:
    clock = Clock(); priv, pub, jwk = rsa_pair()
    issuer = f"https://login.microsoftonline.com/{TENANT}/v2.0"
    idp = FakeEntra(issuer, priv, jwk, clock)
    entra = OIDCProvider("entra", CFG["providers"]["entra"], idp.http, SECRETS.__getitem__, now=clock)
    audit: list[dict[str, Any]] = []
    sm = SessionManager(CFG["session"], MemoryStore(), lambda e, d: audit.append({"event": e, **d}), now=clock, admin_requires=CFG["role_mapping"]["admin_requires"])

    # 1. 授權碼 + PKCE：authorize 網址含 code_challenge、state、nonce；回呼驗 state／nonce／簽章／aud／iss／amr
    url, st = entra.begin("/events")
    assert "code_challenge=" in url and f"state={st.state}" in url and "response_mode=query" in url, url
    idp.codes["c1"] = {"oid": "oid-admin", "upn": "admin@corp.internal", "name": "張敏慧", "groups": ["1c7b1c9a-0000-4000-8000-000000000001"], "nonce": st.nonce}
    ident = entra.callback({"code": "c1", "state": st.state}, st)
    assert ident.subject == "oid-admin" and ident.groups == ["1c7b1c9a-0000-4000-8000-000000000001"] and "mfa" in ident.amr
    roles, scope, applied = map_roles(ident, CFG["role_mapping"])
    assert roles == ["admin"] and applied == ["DMP-Admins"], (roles, applied)
    s_admin = sm.create(user_id=f"entra:{ident.subject}", username=ident.username, name=ident.name, email=ident.email, provider="entra", roles=roles, groups=ident.groups, amr=ident.amr, auth_time=ident.auth_time, ip="10.1.2.3")
    assert audit[-1]["event"] == "auth.login.success" and audit[-1]["ip_masked"] == "10.1.2.x"
    # 錯的 state、重放的 nonce、沒 MFA 的 token、別的 aud 都要被擋
    for bad in ({"code": "c1", "state": "nope"},):
        try: entra.callback(bad, st); raise AssertionError("state 應被擋")
        except OIDCError: pass
    url2, st2 = entra.begin()
    idp.codes["c2"] = {"oid": "oid-x", "upn": "x@corp.internal", "name": "X", "groups": [], "amr": ["pwd"], "nonce": st2.nonce}
    try: entra.callback({"code": "c2", "state": st2.state}, st2); raise AssertionError("無 MFA 應被擋")
    except OIDCError as e: assert "多因子" in str(e)
    url3, st3 = entra.begin()
    idp.codes["c3"] = {"oid": "oid-y", "upn": "y@corp.internal", "name": "Y", "groups": [], "nonce": "wrong"}
    try: entra.callback({"code": "c3", "state": st3.state}, st3); raise AssertionError("nonce 應被擋")
    except OIDCError: pass
    # 2. 群組 overage → Graph memberOf；未知群組不給角色；無角色 → auth.login.no_role
    url4, st4 = entra.begin()
    idp.codes["c4"] = {"oid": "oid-ana", "upn": "ana@corp.internal", "name": "林雅婷", "overage": True, "nonce": st4.nonce}
    ident4 = entra.callback({"code": "c4", "state": st4.state}, st4)
    assert "graph.microsoft.com" in idp.calls[-1].split()[1] and map_roles(ident4, CFG["role_mapping"])[0] == ["data_analyst"]
    url5, st5 = entra.begin()
    idp.codes["c5"] = {"oid": "oid-none", "upn": "n@corp.internal", "name": "N", "groups": ["not-mapped"], "nonce": st5.nonce}
    ident5 = entra.callback({"code": "c5", "state": st5.state}, st5)
    assert map_roles(ident5, CFG["role_mapping"])[0] == []
    sm.create(user_id="entra:oid-none", username="n", name="N", email="", provider="entra", roles=[], groups=ident5.groups, amr=ident5.amr, auth_time=ident5.auth_time)
    assert audit[-1]["event"] == "auth.login.no_role"
    # 3. 本地指派疊加：到期的不算、deny 可拿掉、自訂角色帶範圍
    asg = [{"id": "a1", "user_id": "entra:oid-ana", "roles": ["custom_vendor_analyst"], "scope": {"projects": ["sinopac_demo"], "lookback_days": 180}, "expires_at": clock() + 86400},
           {"id": "a2", "user_id": "entra:oid-ana", "roles": ["admin"], "expires_at": clock() - 1},
           {"id": "a3", "user_id": "entra:oid-ana", "roles": ["data_analyst"], "deny": True}]
    roles4, scope4, applied4 = map_roles(ident4, CFG["role_mapping"], asg, now=clock())
    assert roles4 == ["custom_vendor_analyst"] and scope4["lookback_days"] == 180 and applied4 == ["DMP-Analysts", "assign:a1", "deny:a3"], (roles4, applied4)
    # 4. broker：AD 經 Keycloak User Federation 接入（API → Keycloak → AD）。API 只對 Keycloak 講 OIDC，AD 群組（含巢狀，由 Keycloak group-ldap-mapper 展開）以群組名進 groups claim
    kc_iss = CFG["providers"]["keycloak"]["issuer"]
    kc_idp = FakeEntra(kc_iss, priv, jwk, clock, client_id=CFG["providers"]["keycloak"]["client_id"], client_secret="kc-secret", keycloak=True)
    kc = OIDCProvider("keycloak", CFG["providers"]["keycloak"], kc_idp.http, SECRETS.__getitem__, now=clock)
    url_kc, st_kc = kc.begin("/integration")
    assert url_kc.startswith(kc_iss + "/authorize?") and "code_challenge=" in url_kc and "response_mode" not in url_kc, url_kc
    kc_idp.codes["k1"] = {"oid": "kc-wuch", "upn": "wuch", "name": "吳建宏", "groups": ["DMP-IT", "IT-Staff"], "amr": ["pwd"], "nonce": st_kc.nonce}
    idw = kc.callback({"code": "k1", "state": st_kc.state}, st_kc)
    assert idw.provider == "keycloak" and idw.subject == "sub-kc-wuch" and idw.groups == ["DMP-IT", "IT-Staff"] and map_roles(idw, CFG["role_mapping"])[0] == ["it_admin"], idw
    assert not any("graph.microsoft.com" in x for x in kc_idp.calls)                    # broker 模式不碰 Graph、也不碰 AD
    url_kc2, st_kc2 = kc.begin()
    kc_idp.codes["k2"] = {"oid": "kc-x", "upn": "x", "name": "X", "groups": ["DMP-IT"], "nonce": "wrong"}
    try: kc.callback({"code": "k2", "state": st_kc2.state}, st_kc2); raise AssertionError("Keycloak nonce 應被擋")
    except OIDCError: pass
    # 5. session：閒置逾時、絕對逾時、CSRF、同時 session 上限、admin step-up（認證太舊 → admin 暫時拿掉）
    s = sm.resolve(s_admin.sid, "10.1.2.3"); assert s and s.user_id == "entra:oid-admin"
    assert "admin" in sm.subject(s).roles and authorize(sm.subject(s), "admin", "iam").allowed
    clock.t += 1000                                        # 超過 15 分鐘 → admin 降級（需 step-up）
    s = sm.resolve(s_admin.sid); assert s and "admin" not in sm.subject(s).roles and not authorize(sm.subject(s), "admin", "iam").allowed
    s.step_up_at = int(clock()); assert "admin" in sm.subject(s).roles
    assert not sm.check_csrf(s, "bad") and sm.check_csrf(s, s.csrf)
    clock.t += CFG["session"]["idle_timeout_s"] + 1
    assert sm.resolve(s_admin.sid) is None and audit[-1]["event"] == "auth.session.expired"
    for _ in range(4):
        sm.create(user_id="entra:oid-ana", username="ana", name="林雅婷", email="", provider="entra", roles=["data_analyst"], groups=[], amr=["pwd", "mfa"], auth_time=int(clock()))
    assert len(sm.store.list_user("entra:oid-ana")) == CFG["session"]["concurrent_sessions"] and any(a["event"] == "auth.session.evicted" for a in audit)
    hdrs = sm.cookie_headers(sm.store.list_user("entra:oid-ana")[-1])
    # 3b. 既有 session 的有效授權每次請求重算（R07）：接上指派來源後，指派新增／到期／deny／scope 變更不用重新登入就反映；本機帳號停用 → 沒有角色
    live_asg: list[dict[str, Any]] = []; disabled = {"v": False}
    sm.assignments = lambda: live_asg
    sm.base_roles_of = lambda x: (None if disabled["v"] else ["data_analyst"]) if x.provider == "local" else x.attrs.get("base_roles")
    s_live = sm.create(user_id="entra:oid-live", username="live", name="L", email="", provider="entra", roles=["data_analyst"], groups=[], amr=["pwd", "mfa"], auth_time=int(clock()), base_roles=["data_analyst"])
    assert sm.subject(s_live).roles == ["data_analyst"] and sm.subject(s_live).branches == []
    live_asg.append({"id": "l1", "user_id": "entra:oid-live", "roles": ["marketing"], "scope": {"branches": ["B01"]}, "expires_at": clock() + 600})
    sub = sm.subject(s_live); assert sub.roles == ["data_analyst", "marketing"] and sub.branches == ["B01"], (sub.roles, sub.branches)   # 新指派：同一個 session 立刻有
    clock.t += 601; sub = sm.subject(s_live); assert sub.roles == ["data_analyst"] and sub.branches == [], (sub.roles, sub.branches)       # 到期：立刻沒有（session 未重新登入）
    live_asg.append({"id": "l2", "user_id": "entra:oid-live", "roles": ["data_analyst"], "deny": True}); assert sm.subject(s_live).roles == []   # deny：立刻拿掉
    live_asg.clear(); assert sm.subject(s_live).roles == ["data_analyst"]
    s_loc = sm.create(user_id="local:sub1", username="loc", name="", email="", provider="local", roles=["data_analyst"], groups=[], amr=["pwd"], auth_time=int(clock()), base_roles=["data_analyst"])
    assert sm.subject(s_loc).roles == ["data_analyst"]; disabled["v"] = True; assert sm.subject(s_loc).roles == []                                # 本機帳號停用：立刻沒有角色
    s_old = sm.create(user_id="entra:oid-old", username="old", name="", email="", provider="entra", roles=["data_analyst"], groups=[], amr=["pwd", "mfa"], auth_time=int(clock()))   # 升版前的 session：沒記 base_roles
    live_asg.append({"id": "l3", "user_id": "entra:oid-old", "roles": ["data_analyst"], "expires_at": clock() - 1})
    assert sm.subject(s_old).roles == [], "舊 session 的 roles 混了指派，不能當基底：沒有角色，要重新登入"
    sm.assignments = sm.base_roles_of = None
    assert sm.subject(s_old).roles == ["data_analyst"]                                                                                                               # 沒接來源（單元測試）才沿用快照
    assert any("dmp_sid=" in v and "HttpOnly" in v and "Secure" in v and "SameSite=Lax" in v for _, v in hdrs) and any("dmp_csrf=" in v and "HttpOnly" not in v for _, v in hdrs)
    # 6. API token（RS256，15 分鐘）：簽發 → 驗證回 Subject；竄改／過期被擋
    tokens = ApiTokens(CFG["session"]["api_token"], priv, pub, now=clock)
    sess = sm.store.list_user("entra:oid-ana")[-1]
    tk = tokens.issue(sess, ["data_analyst"])
    subj = tokens.verify(tk); assert subj.roles == ["data_analyst"] and authorize(subj, "use", "events").allowed
    try: tokens.verify(tk[:-3] + "abc"); raise AssertionError("竄改")
    except jwt.PyJWTError: pass
    clock.t += 1000
    try: tokens.verify(tk); raise AssertionError("過期")
    except jwt.PyJWTError: pass
    # 7. /v1/iam/me 的內容：grants 聯集、masking 最嚴格、scope
    g = grants_of(["marketing", "digital_ops"]); assert "approve" in g["segments"] and "export" in g["events"] and "profile" in g
    m = masking_of(["marketing"]); assert m["amount"]["policy"].startswith("bucket") and m["one_id"]["policy"] == "mask_tail(4)" and masking_of(["admin"])["one_id"]["policy"] == "plain"
    # 8. FastAPI 路由（若環境有 fastapi）：providers → login 302 → callback 302 + cookies → /v1/iam/me → logout；未登入 401；CSRF 403
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except ImportError:
        print("auth self-test ok (fastapi 未安裝，略過路由測試)"); return
    import copy

    from .api import AuthService, apply_env_overrides, build_router
    svc_b = AuthService(dict(CFG), {"keycloak": kc, "entra": entra}, sm, tokens, lambda: asg)     # broker：登入頁只有 Keycloak；帳密端點不開
    app_b = FastAPI(); app_b.include_router(build_router(svc_b))
    cb = TestClient(app_b, base_url="https://dmp.internal")
    assert cb.get("/auth/providers").json() == {"mode": "broker", "providers": [{"key": "keycloak", "cn": CFG["providers"]["keycloak"]["cn"], "kind": "oidc"}]}
    assert cb.get("/auth/login", params={"provider": "entra"}, follow_redirects=False).status_code == 400
    r = cb.post("/auth/ldap", json={"provider": "ad", "username": "wuch", "password": "Passw0rd!"}); assert r.status_code == 400 and "Keycloak" in r.json()["detail"], r.text
    # 部署覆寫：broker 模式列 Keycloak；issuer、回呼網址跟著環境變數；打錯模式（例：oidc）啟動就報錯，不會默默沒有登入入口
    ov = apply_env_overrides(copy.deepcopy(CFG), {"DMP_IDP_MODE": "broker", "DMP_OIDC_ISSUER": "https://dmp.example.test/auth/realms/dmp", "DMP_PUBLIC_URL": "https://dmp.example.test/"})
    kc_ov = ov["providers"]["keycloak"]
    assert ov["mode"] == "broker" and kc_ov["issuer"] == "https://dmp.example.test/auth/realms/dmp", kc_ov
    assert kc_ov["redirect_uri"] == "https://dmp.example.test/auth/callback" and kc_ov["post_logout_redirect_uri"] == "https://dmp.example.test/login", kc_ov
    assert CFG["providers"]["keycloak"]["redirect_uri"] != kc_ov["redirect_uri"]       # 不改到共用設定
    for bad in ({"DMP_IDP_MODE": "oidc"}, {"DMP_PUBLIC_URL": "dmp.example.test"}):
        try:
            apply_env_overrides(copy.deepcopy(CFG), bad); raise AssertionError(bad)
        except ValueError:
            pass
    stub = type("P", (), {"cfg": kc_ov})()
    assert [p["key"] for p in AuthService(ov, {"keycloak": stub}, sm).visible_providers()] == ["keycloak"]
    cfg = dict(CFG); cfg["mode"] = "direct"
    svc = AuthService(cfg, {"entra": entra}, sm, tokens, lambda: asg)
    from ..api import errors as api_errors
    app = FastAPI(); app.include_router(build_router(svc))
    api_errors.install(app, type("Sink", (), {"audit": lambda self, e, d: None})())   # 與正式 API 同一套錯誤輸出；302 不能被改成 Error
    c = TestClient(app, base_url="https://dmp.internal")
    assert [p["key"] for p in c.get("/auth/providers").json()["providers"]] == ["entra"]
    assert c.get("/v1/iam/me").json()["code"] == "UNAUTHENTICATED"
    r = c.get("/auth/login", params={"provider": "entra", "return_to": "/users"}, follow_redirects=False)
    assert r.status_code == 302 and "code_challenge" in r.headers["location"] and "dmp_login" in r.headers.get("set-cookie", "") and not r.content and r.headers.get("x-request-id")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    idp.codes["c9"] = {"oid": "oid-ops", "upn": "ops@corp.internal", "name": "陳柏宇", "groups": ["1c7b1c9a-0000-4000-8000-000000000004", "1c7b1c9a-0000-4000-8000-000000000002"], "nonce": svc.login_states[state].nonce}
    r = c.get("/auth/callback", params={"code": "c9", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/users" and "dmp_sid=" in r.headers.get("set-cookie", "") and not r.content, (r.status_code, r.text)
    me = c.get("/v1/iam/me").json()
    import os
    if os.environ.get("AUTH_DEBUG"): print(json.dumps(me, ensure_ascii=False)[:1200])
    assert [x["key"] for x in me["roles"]] == ["marketing", "digital_ops"] and "approve" in me["grants"]["segments"] and me["masking"]["amount"]["policy"] == "plain" and me["session"]["csrf_header"] == "X-CSRF-Token"
    assert c.post("/auth/logout").json()["code"] == "CSRF_INVALID"                    # 沒帶 CSRF → 403
    tk2 = c.post("/auth/token", headers={"X-CSRF-Token": c.cookies["dmp_csrf"]}).json()["access_token"]
    assert tokens.verify(tk2).roles == ["marketing", "digital_ops"]
    r = c.post("/auth/ldap", json={"provider": "ad", "username": "wuch", "password": "Passw0rd!"}); assert r.status_code == 400 and "Keycloak" in r.json()["detail"]   # direct 模式也不直連 AD
    assert c.post("/auth/ldap", json={"username": "wuch", "password": "Passw0rd!"}).status_code == 400                                                            # 預設 provider=local，未啟用 → 400
    r = c.post("/auth/logout", headers={"X-CSRF-Token": c.cookies["dmp_csrf"]}); assert r.status_code == 200 and "logout" in (r.json()["idp_logout"] or "")
    assert c.get("/v1/iam/me").status_code == 401
    # 9. 本機帳號（provider=local）：密碼 → next=totp → 錯碼 401 → 正確碼登入（amr pwd+mfa，角色來自帳號）→ 15 分鐘後 admin 降級 → step-up 回復 → 不允許的角色被濾掉
    import tempfile
    from pathlib import Path as _P

    from .local import LocalAccounts, LocalProvider, totp_code
    la = LocalAccounts(_P(tempfile.mkdtemp(prefix="dmp-la-")) / "accounts.json", {"min_length": 14, "lockout_after": 5, "lockout_minutes": 15, "max_days": 90}, ["admin", "data_analyst", "custom_*"], clock)
    la.add("ops.local", "local-admin-password-1", ["admin", "data_analyst"], name="本機管理員")
    la.data["accounts"]["ops.local"]["roles"].append("digital_ops")        # 模擬帳號檔被改／政策收緊：登入時仍過白名單
    svc.providers["local"] = LocalProvider(la, now=clock)
    assert [p["key"] for p in c.get("/auth/providers").json()["providers"]] == ["entra", "local"]
    r = c.post("/auth/ldap", json={"provider": "local", "username": "ops.local", "password": "local-admin-password-1"}); assert r.json()["next"] == "totp", r.text
    assert c.post("/auth/ldap", json={"provider": "local", "username": "ops.local", "password": "local-admin-password-1", "totp": "000000"}).status_code == 401
    assert c.post("/auth/ldap", json={"provider": "local", "username": "ops.local", "password": "nope-nope-nope-nope"}).status_code == 401
    assert c.post("/auth/ldap", json={"provider": "x", "username": "a", "password": "b"}).status_code == 400
    sec = la.data["accounts"]["ops.local"]["totp_secret"]
    r = c.post("/auth/ldap", json={"provider": "local", "username": "ops.local", "password": "local-admin-password-1", "totp": totp_code(sec, clock())})
    assert r.status_code == 200 and r.json()["roles"] == ["admin", "data_analyst"], r.text                  # digital_ops 不在 allowed_roles → 濾掉
    me = c.get("/v1/iam/me").json(); assert me["user"]["provider"] == "local" and [x["key"] for x in me["roles"] if x.get("active")] == ["admin", "data_analyst"] and me["session"]["amr"] == ["pwd", "mfa"], me["session"]
    clock.t += 16 * 60
    me = c.get("/v1/iam/me").json(); assert me["session"]["step_up_needed"] and "admin" not in [x["key"] for x in me["roles"] if x.get("active")]
    assert c.post("/auth/step-up", json={"totp": "000000"}, headers={"X-CSRF-Token": c.cookies["dmp_csrf"]}).status_code == 401
    r = c.post("/auth/step-up", json={"totp": totp_code(sec, clock())}, headers={"X-CSRF-Token": c.cookies["dmp_csrf"]}); assert r.status_code == 200 and "admin" in r.json()["roles"], r.text
    assert not c.get("/v1/iam/me").json()["session"]["step_up_needed"]
    la.add("lock.me", "lock-me-password-123", ["data_analyst"])               # 連錯 5 次：第 6 次撞速率限制 → 429；過了一分鐘 → 帳號鎖定 403（docs/46 代碼）
    codes = [c.post("/auth/ldap", json={"provider": "local", "username": "lock.me", "password": "wrong-wrong-wrong-1"}).json()["code"] for _ in range(6)]
    assert codes == ["UNAUTHENTICATED"] * 5 + ["RATE_LIMITED"], codes
    clock.t += 61; r = c.post("/auth/ldap", json={"provider": "local", "username": "lock.me", "password": "lock-me-password-123"})
    assert r.status_code == 403 and r.json()["code"] == "ACCOUNT_LOCKED" and "鎖定" in r.json()["detail"], r.text
    assert {x["event"] for x in audit} >= {"auth.login.failed", "auth.step_up.failed", "auth.step_up"}
    c.post("/auth/logout", headers={"X-CSRF-Token": c.cookies["dmp_csrf"]})
    shared = _shared_two_nodes(cfg, entra, idp, clock, asg)
    print(f"auth self-test ok: oidc(entra pkce/jwks/amr/overage) broker(keycloak→ad groups, ad direct rejected) local(scrypt/totp/step-up) session(idle/absolute/csrf/evict/step-up) api-token routes; audit events={len(audit)}; {shared}")


def _shared_two_nodes(cfg: dict[str, Any], entra: OIDCProvider, idp: FakeEntra, clock: Clock, asg: list[dict[str, Any]]) -> str:
    """10. 兩個 API 實例共用 PostgreSQL（docs/44 §5a）：A 發起 SSO、B 收回呼；同一 cookie 兩台都認得；B 登出 A 也失效；
    登入前狀態只能用一次；資料庫只存 sid 雜湊；超過同時 session 上限跨台也踢最舊的。沒設 DMP_PG_DSN 就略過。"""
    import os
    import uuid
    if not os.environ.get("DMP_PG_DSN"):
        return "shared-session(略過：未設 DMP_PG_DSN)"
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ..api.store_pg import PostgresStore
    from .api import AuthService, build_router
    from .session_pg import PgLoginStates, PgSessionStore
    schema = f"t_{uuid.uuid4().hex[:10]}"
    pgs = [PostgresStore(os.environ["DMP_PG_DSN"], schema=schema) for _ in range(2)]       # 兩個連線池＝兩個 API 程序
    try:
        audit: list[dict[str, Any]] = []
        nodes = []
        for pg in pgs:
            sm = SessionManager(CFG["session"], PgSessionStore(pg), lambda e, d: audit.append({"event": e, **d}), now=clock, admin_requires=CFG["role_mapping"]["admin_requires"])
            svc = AuthService(cfg, {"entra": entra}, sm, None, lambda: asg, PgLoginStates(pg, now=clock))
            app = FastAPI(); app.include_router(build_router(svc))
            nodes.append((svc, TestClient(app, base_url="https://dmp.internal")))
        (sa, ca), (sb, cb) = nodes
        ck = CFG["session"]["cookie"]["name"]
        r = ca.get("/auth/login", params={"provider": "entra", "return_to": "/funnel"}, follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        idp.codes["c10"] = {"oid": "oid-ops2", "upn": "ops2@corp.internal", "name": "王小明", "groups": ["1c7b1c9a-0000-4000-8000-000000000004"], "nonce": sb.login_states[state].nonce}
        cb.cookies.set("dmp_login", ca.cookies["dmp_login"], domain="dmp.internal", path="/auth")
        r = cb.get("/auth/callback", params={"code": "c10", "state": state}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/funnel", (r.status_code, r.text)     # A 發起、B 收回呼
        assert ca.cookies.get("dmp_login") == state
        assert ca.get("/auth/callback", params={"code": "c10", "state": state}, follow_redirects=False).status_code == 400   # 登入前狀態只能用一次
        sid, csrf = cb.cookies[ck], cb.cookies["dmp_csrf"]
        for c in (ca,):
            c.cookies.set(ck, sid, domain="dmp.internal"); c.cookies.set("dmp_csrf", csrf, domain="dmp.internal")
        assert ca.get("/v1/iam/me").status_code == 200 and cb.get("/v1/iam/me").status_code == 200          # 同一 cookie 兩台都認得
        mine = ca.get("/v1/iam/sessions").json(); assert len(mine) == 1 and mine[0]["current"], mine
        raw = pgs[0]._loop.run(_fetch(pgs[0], f"SELECT sid_hash, doc::text AS doc FROM {schema}.iam_sessions"))
        assert raw and all(sid not in (x["sid_hash"] + x["doc"]) for x in raw)                                     # 只存雜湊
        ca.cookies.set(ck, raw[0]["sid_hash"], domain="dmp.internal")
        assert ca.get("/v1/iam/me").status_code == 401                                                             # 資料庫裡的雜湊不能當 cookie
        ca.cookies.set(ck, sid, domain="dmp.internal")
        # 撤銷 vs 在途請求：A 已讀到 session（resolve 前半）→ B 登出 → A 才寫回活動時間（resolve 後半）；不得復活
        import threading
        a_read, b_done = threading.Event(), threading.Event()
        def node_a() -> None:
            stale = sa.sessions.store.get(sid); assert stale is not None
            a_read.set(); b_done.wait(10)
            stale.last_seen = clock() + 1; sa.sessions.store.put(stale)
        def node_b() -> None:
            a_read.wait(10)
            assert cb.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
            b_done.set()
        ts = [threading.Thread(target=node_a), threading.Thread(target=node_b)]
        [t.start() for t in ts]; [t.join(20) for t in ts]
        assert sa.sessions.store.get(sid) is None and sa.sessions.resolve(sid) is None
        assert ca.get("/v1/iam/me").status_code == 401 and cb.get("/v1/iam/me").status_code == 401             # B 登出後，舊請求也救不回來
        n = int(CFG["session"]["concurrent_sessions"])
        for i in range(n + 1):
            (sa if i % 2 else sb).sessions.create(user_id="entra:oid-x", username="x", name="X", email="", provider="entra", roles=["marketing"], groups=[], amr=["mfa"], auth_time=int(clock()))
        assert len(sa.sessions.store.list_user("entra:oid-x")) == n and any(a["event"] == "auth.session.evicted" for a in audit)   # 跨台也守同時 session 上限
        url, st = entra.begin("/")
        sa.login_states[st.state] = st; clock.t += 601
        assert sb.login_states.pop(st.state, None) is None                                                         # 逾時的登入前狀態視同不存在
        return "shared-session(2 nodes: callback/cookie/hash-not-cookie/logout-vs-inflight/one-time-state/hash/evict/ttl)"
    finally:
        pgs[0].drop_schema()
        for pg in pgs:
            pg.close()


async def _fetch(pg: Any, sql: str) -> list[dict[str, Any]]:
    async with pg.pool.acquire() as c:
        return [dict(r) for r in await c.fetch(sql)]


if __name__ == "__main__":
    main()
