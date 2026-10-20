"""登入整合自測（不連網）：假 Entra ID（JWKS + token endpoint）、假 AD 目錄、session／CSRF／逾時、群組 → 角色、admin step-up、API token、FastAPI 路由。

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
from .ldap import FakeLdap, LdapError, LdapProvider
from .mapping import grants_of, map_roles, masking_of
from .oidc import OIDCError, OIDCProvider
from .session import ApiTokens, MemoryStore, SessionManager

CFG = yaml.safe_load((Path(__file__).resolve().parent / "idp.yaml").read_text(encoding="utf-8"))
TENANT = "11111111-2222-3333-4444-555555555555"
SECRETS = {"kv/data/dmp/auth/entra#tenant_id": TENANT, "kv/data/dmp/auth/entra#client_id": "app-dmp", "kv/data/dmp/auth/entra#client_secret": "s3cret",
           "kv/data/dmp/auth/keycloak#client_secret": "kc-secret", "kv/data/dmp/auth/ad#bind_password": "svc-pw"}


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
    """假 IdP：發現文件、JWKS、token endpoint（依 code 決定 claims）。"""
    def __init__(self, issuer: str, priv: str, jwk: dict[str, Any], clock: Clock):
        self.issuer, self.priv, self.jwk, self.clock = issuer, priv, jwk, clock
        self.codes: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def http(self, method: str, url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(f"{method} {url.split('?')[0]}")
        if url.endswith("/.well-known/openid-configuration"):
            return {"authorization_endpoint": self.issuer + "/authorize", "token_endpoint": self.issuer + "/token", "jwks_uri": self.issuer + "/keys", "end_session_endpoint": self.issuer + "/logout"}
        if url.endswith("/keys"):
            return {"keys": [self.jwk]}
        if url.endswith("/token"):
            assert data and data["grant_type"] == "authorization_code" and data["code_verifier"] and data["client_secret"] == "s3cret"
            spec = self.codes[data["code"]]
            t = int(self.clock())
            claims = {"iss": self.issuer, "aud": "app-dmp", "sub": "sub-" + spec["oid"], "oid": spec["oid"], "tid": TENANT, "preferred_username": spec["upn"], "name": spec["name"],
                      "email": spec["upn"], "groups": spec.get("groups", []), "amr": spec.get("amr", ["pwd", "mfa"]), "iat": t, "exp": t + 3600, "auth_time": t, "nonce": spec["nonce"]}
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
    # 4. 直連 AD：搜尋 → 使用者 bind → 巢狀群組；錯密碼／停用帳號／注入／速率限制
    base = "OU=Users,DC=corp,DC=internal"; gbase = "OU=Groups,DC=corp,DC=internal"
    ldap = FakeLdap({
        f"CN=吳建宏,{base}": {"sAMAccountName": ["wuch"], "mail": ["it@corp.internal"], "displayName": ["吳建宏"], "memberOf": [f"CN=IT-Staff,{gbase}"], "objectGUID": ["guid-wu"], "userPrincipalName": ["wuch@corp.internal"]},
        f"CN=離職者,{base}": {"sAMAccountName": ["gone"], "userAccountControl_disabled": ["2"], "objectGUID": ["guid-gone"]},
        f"CN=IT-Staff,{gbase}": {"cn": ["IT-Staff"], "member": [f"CN=吳建宏,{base}"]},
        f"CN=DMP-IT,{gbase}": {"cn": ["DMP-IT"], "member": [f"CN=IT-Staff,{gbase}"]},            # 巢狀：IT-Staff ∈ DMP-IT
    }, {CFG["providers"]["ad"]["bind_dn"]: "svc-pw", f"CN=吳建宏,{base}": "Passw0rd!"})
    ad = LdapProvider("ad", CFG["providers"]["ad"], ldap, SECRETS.__getitem__, now=clock)
    idw = ad.authenticate("wuch", "Passw0rd!", "10.0.0.9")
    assert idw.subject == "guid-wu" and idw.groups == ["DMP-IT", "IT-Staff"] and map_roles(idw, CFG["role_mapping"])[0] == ["it_admin"], idw
    assert ldap.binds[-1] == f"CN=吳建宏,{base}"
    for u, pw, why in (("wuch", "wrong", "錯密碼"), ("gone", "x", "停用"), ("wuch)(|(sAMAccountName=*", "x", "注入"), ("nobody", "x", "不存在")):
        try: ad.authenticate(u, pw, "10.0.0.9"); raise AssertionError(why)
        except LdapError as e: assert str(e) in ("帳號或密碼錯誤", "嘗試次數過多，請稍後再試"), (why, e)
    got = ""
    for _ in range(8):                                     # 連續錯密碼 → 同帳號每分鐘 5 次後鎖住（AD 端鎖定策略另計）
        try: ad.authenticate("wuch", "wrong", "10.0.0.7")
        except LdapError as e: got = str(e)
        if "過多" in got: break
    assert "過多" in got, got
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
    from .api import AuthService, build_router
    cfg = dict(CFG); cfg["mode"] = "direct"
    svc = AuthService(cfg, {"entra": entra, "ad": ad}, sm, tokens, lambda: asg)
    app = FastAPI(); app.include_router(build_router(svc))
    c = TestClient(app, base_url="https://dmp.internal")
    assert [p["key"] for p in c.get("/auth/providers").json()["providers"]] == ["entra", "ad"]
    assert c.get("/v1/iam/me").status_code == 401
    r = c.get("/auth/login", params={"provider": "entra", "return_to": "/users"}, follow_redirects=False)
    assert r.status_code == 302 and "code_challenge" in r.headers["location"] and "dmp_login" in r.headers.get("set-cookie", "")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    idp.codes["c9"] = {"oid": "oid-ops", "upn": "ops@corp.internal", "name": "陳柏宇", "groups": ["1c7b1c9a-0000-4000-8000-000000000004", "1c7b1c9a-0000-4000-8000-000000000002"], "nonce": svc.login_states[state].nonce}
    r = c.get("/auth/callback", params={"code": "c9", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/users", (r.status_code, r.text)
    me = c.get("/v1/iam/me").json()
    import os
    if os.environ.get("AUTH_DEBUG"): print(json.dumps(me, ensure_ascii=False)[:1200])
    assert [x["key"] for x in me["roles"]] == ["marketing", "digital_ops"] and "approve" in me["grants"]["segments"] and me["masking"]["amount"]["policy"] == "plain" and me["session"]["csrf_header"] == "X-CSRF-Token"
    assert c.post("/auth/logout").status_code == 403                                  # 沒帶 CSRF
    tk2 = c.post("/auth/token", headers={"X-CSRF-Token": c.cookies["dmp_csrf"]}).json()["access_token"]
    assert tokens.verify(tk2).roles == ["marketing", "digital_ops"]
    r = c.post("/auth/ldap", json={"username": "wuch", "password": "Passw0rd!"}); assert r.json()["next"] == "totp"
    r = c.post("/auth/ldap", json={"username": "wuch", "password": "Passw0rd!", "totp": "123456"}); assert r.status_code == 401 and "驗證器" in r.json()["detail"]   # AD 直連尚未接 TOTP 驗證器 → 不放行
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
    assert [p["key"] for p in c.get("/auth/providers").json()["providers"]] == ["entra", "ad", "local"]
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
    assert {x["event"] for x in audit} >= {"auth.login.failed", "auth.step_up.failed", "auth.step_up"}
    c.post("/auth/logout", headers={"X-CSRF-Token": c.cookies["dmp_csrf"]})
    print(f"auth self-test ok: oidc(entra pkce/jwks/amr/overage) ldap(bind/nested/limits) local(scrypt/totp/step-up) session(idle/absolute/csrf/evict/step-up) api-token routes; audit events={len(audit)}")


if __name__ == "__main__":
    main()
