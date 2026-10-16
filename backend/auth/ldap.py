"""直連 Active Directory（mode=direct 的 ad 提供者）：服務帳號搜尋使用者 DN → 以使用者 DN + 密碼 bind 驗證 → 讀群組（含巢狀）。

安全要點：
  - 只走 LDAPS（636）並釘選內部 CA；不允許 StartTLS 以外的明文、不允許關閉憑證驗證。
  - 使用者名稱先過白名單正規式，再做 LDAP 跳脫（RFC 4515），防 LDAP injection。
  - 密碼只用於一次 bind，不落地、不進日誌；失敗訊息一律「帳號或密碼錯誤」（不洩漏帳號是否存在）。
  - 速率限制（每帳號／每 IP）；AD 端鎖定策略照舊。
  - 直連 AD 沒有 IdP 的 MFA：本系統要求 TOTP 第二因子（idp.yaml ad.mfa）。
LDAP 客戶端用 Protocol 注入：正式環境用 python-ldap（PSF 式授權，需 OpenLDAP 函式庫）；自測用 FakeLdap。
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any, Protocol

from .oidc import Identity

NESTED = "1.2.840.113556.1.4.1941"   # AD LDAP_MATCHING_RULE_IN_CHAIN


class LdapClient(Protocol):
    def search(self, base_dn: str, filter_: str, attributes: list[str]) -> list[tuple[str, dict[str, list[str]]]]: ...
    def bind(self, dn: str, password: str) -> bool: ...


class LdapError(Exception):
    pass


def escape_filter(v: str) -> str:
    """RFC 4515：過濾式裡的特殊字元跳脫。"""
    return "".join(f"\\{ord(c):02x}" if c in "\\*()\0" else c for c in v)


class RateLimiter:
    def __init__(self, per_min: int, now: Callable[[], float] = time.time):
        self.per_min, self.now = per_min, now
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        t = self.now(); q = self.hits[key]
        while q and t - q[0] > 60: q.popleft()
        if len(q) >= self.per_min: return False
        q.append(t); return True


class LdapProvider:
    def __init__(self, key: str, cfg: dict[str, Any], client: LdapClient, secrets_resolver: Callable[[str], str], now: Callable[[], float] = time.time):
        self.key, self.cfg, self.client, self.secret, self.now = key, cfg, client, secrets_resolver, now
        rl = cfg.get("rate_limit", {})
        self.rl_account = RateLimiter(int(rl.get("per_account_per_min", 5)), now)
        self.rl_ip = RateLimiter(int(rl.get("per_ip_per_min", 30)), now)
        self.username_re = re.compile(cfg.get("username_pattern", r"^[A-Za-z0-9._-]{2,64}$"))

    def authenticate(self, username: str, password: str, ip: str = "") -> Identity:
        if not self.username_re.fullmatch(username or "") or not password:
            raise LdapError("帳號或密碼錯誤")
        if not self.rl_ip.allow(ip or "-") or not self.rl_account.allow(username.lower()):
            raise LdapError("嘗試次數過多，請稍後再試")
        attrs = self.cfg["attributes"]
        if not self.client.bind(self.cfg["bind_dn"], self.secret(self.cfg["bind_password_ref"])):
            raise LdapError("目錄服務暫時無法使用")             # 服務帳號 bind 失敗 → 營運告警，不是使用者的錯
        flt = self.cfg["user_filter"].replace("{username}", escape_filter(username))
        rows = self.client.search(self.cfg["base_dn"], flt, [attrs["username"], attrs["email"], attrs["name"], attrs["groups"], attrs.get("upn", "userPrincipalName"), "objectGUID"])
        if len(rows) != 1:
            raise LdapError("帳號或密碼錯誤")
        dn, a = rows[0]
        if not self.client.bind(dn, password):
            raise LdapError("帳號或密碼錯誤")
        groups = [self._cn(g) for g in a.get(attrs["groups"], [])]
        if self.cfg.get("nested_groups"):
            gb = self.cfg.get("group_base_dn", self.cfg["base_dn"])
            nested = self.client.search(gb, f"(member:{NESTED}:={escape_filter(dn)})", ["cn"])
            groups = sorted(set(groups) | {self._cn(gdn) for gdn, _ in nested})
        one = lambda k: (a.get(k) or [""])[0]
        return Identity(self.key, one("objectGUID") or dn, one(attrs["username"]) or username, one(attrs["email"]), one(attrs["name"]), groups, amr=["pwd"], auth_time=int(self.now()),
                        raw={"dn": dn, "upn": one(attrs.get("upn", "userPrincipalName"))})

    @staticmethod
    def _cn(dn: str) -> str:
        m = re.match(r"\s*CN=([^,]+)", dn, re.I)
        return (m.group(1) if m else dn).replace("\\,", ",")


class FakeLdap:
    """自測用目錄：{dn: {attrs}}；密碼表 {dn: password}。"""
    def __init__(self, entries: dict[str, dict[str, list[str]]], passwords: dict[str, str]):
        self.entries, self.passwords = entries, passwords
        self.binds: list[str] = []

    def search(self, base_dn: str, filter_: str, attributes: list[str]) -> list[tuple[str, dict[str, list[str]]]]:
        m = re.search(r"\(sAMAccountName=([^)]*)\)", filter_)
        if m:
            u = m.group(1)
            return [(dn, {k: v for k, v in e.items() if k in attributes}) for dn, e in self.entries.items()
                    if dn.endswith(base_dn) and u in e.get("sAMAccountName", []) and "2" not in e.get("userAccountControl_disabled", [])]
        m = re.search(rf"\(member:{re.escape(NESTED)}:=([^)]*)\)", filter_)
        if m:
            member = m.group(1).replace("\\2c", ",")
            out = []; seen = set(); todo = [member]
            while todo:                                    # 巢狀：沿 member 關係往上找
                cur = todo.pop()
                for dn, e in self.entries.items():
                    if dn.endswith(base_dn) and cur in e.get("member", []) and dn not in seen:
                        seen.add(dn); out.append((dn, {"cn": e.get("cn", [])})); todo.append(dn)
            return out
        return []

    def bind(self, dn: str, password: str) -> bool:
        self.binds.append(dn)
        return self.passwords.get(dn) == password


class PythonLdapClient:
    """正式環境：python-ldap（需 libldap）。只在需要時 import，骨架與 CI 不依賴它。"""
    def __init__(self, url: str, ca_pem_path: str, timeout: int = 10):
        import ldap  # type: ignore
        self.ldap = ldap
        self.conn = ldap.initialize(url)
        self.conn.set_option(ldap.OPT_X_TLS_CACERTFILE, ca_pem_path)
        self.conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_DEMAND)
        self.conn.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
        self.conn.set_option(ldap.OPT_NETWORK_TIMEOUT, timeout)
        self.conn.set_option(ldap.OPT_REFERRALS, 0)

    def search(self, base_dn: str, filter_: str, attributes: list[str]) -> list[tuple[str, dict[str, list[str]]]]:
        res = self.conn.search_s(base_dn, self.ldap.SCOPE_SUBTREE, filter_, attributes)
        return [(dn, {k: [x.decode("utf-8", "replace") if isinstance(x, bytes) else x for x in v] for k, v in a.items()}) for dn, a in res if dn]

    def bind(self, dn: str, password: str) -> bool:
        try:
            self.conn.simple_bind_s(dn, password); return True
        except self.ldap.INVALID_CREDENTIALS:
            return False
