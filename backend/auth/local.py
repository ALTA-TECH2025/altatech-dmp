"""本機帳號（idp.yaml › local_accounts）：沒有 AD／Keycloak 的單機、外包或測試環境用。

- 密碼 scrypt（標準庫，不加套件；n=2^14 r=8 p=1），密碼政策（長度、鎖定）照 idp.yaml。
- 第二因子 TOTP（RFC 6238：HMAC-SHA1、30 秒、6 位、容忍前後一格；同一格不可重放）。
- 帳號有到期日（max_days），到期後拒絕登入；roles 寫在帳號裡但登入時仍過 allowed_roles 白名單（正式環境只給 custom_*；單機測試用 * 放開）。
- 帳號檔是 JSON（DMP_LOCAL_ACCOUNTS）；只存鹽與雜湊、TOTP 密鑰（base32）。檔案權限請設 600。

CLI：python3 -m backend.auth.local [--file 路徑] add <帳號> --roles admin,marketing [--name 名稱] [--email 信箱]（密碼從 DMP_PASSWORD 環境變數或互動輸入）
                                   list | remove <帳號> | reset-password <帳號> | reset-totp <帳號> | code <帳號>（印目前 TOTP，測試環境沒有手機時用）
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .ldap import RateLimiter
from .oidc import Identity

ISSUER = "AltaTech DMP"
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class LocalError(Exception):
    pass


# ---- TOTP ----
def totp_code(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    counter = int((at if at is not None else time.time()) // step)
    return _hotp(secret_b32, counter, digits)


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


def totp_uri(username: str, secret_b32: str) -> str:
    return f"otpauth://totp/{quote(ISSUER)}:{quote(username)}?secret={secret_b32}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period=30"


# ---- 密碼 ----
def hash_password(pw: str, salt: bytes | None = None) -> dict[str, str]:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, **SCRYPT)
    return {"alg": "scrypt", "salt": salt.hex(), "hash": h.hex()}


def check_password(rec: dict[str, str], pw: str) -> bool:
    h = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(rec["salt"]), **SCRYPT)
    return hmac.compare_digest(h.hex(), rec["hash"])


class LocalAccounts:
    """帳號檔的讀寫與驗證。所有變更立刻寫回檔案（單機環境；多實例請改用控制面 DB）。"""

    def __init__(self, path: Path, policy: dict[str, Any] | None = None, allowed_roles: list[str] | None = None, now: Callable[[], float] = time.time):
        self.path, self.now = Path(path), now
        pol = policy or {}
        self.min_length = int(pol.get("min_length", 14)); self.lockout_after = int(pol.get("lockout_after", 5)); self.lockout_s = int(pol.get("lockout_minutes", 15)) * 60
        self.allowed_roles = allowed_roles or ["custom_*"]
        self.max_days = int(pol.get("max_days", 90))
        self.data: dict[str, Any] = {"version": 1, "accounts": {}}
        self._mtime = -1.0
        self._sync()

    def _sync(self) -> None:
        """檔案被 CLI／seed 改過（API 啟動後才建帳號是常態）→ 重新讀；沒變就不讀。"""
        try:
            m = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if m != self._mtime:
            self.data = json.loads(self.path.read_text(encoding="utf-8")); self._mtime = m

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime

    def role_allowed(self, role: str) -> bool:
        return any(a == "*" or a == role or (a.endswith("*") and role.startswith(a[:-1])) for a in self.allowed_roles)

    def add(self, username: str, password: str, roles: list[str], name: str = "", email: str = "", totp: bool = True) -> dict[str, Any]:
        self._sync()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", username):
            raise LocalError("帳號只能是英數、點、底線、連字號（2–64 字）")
        if username in self.data["accounts"]:
            raise LocalError("帳號已存在")
        bad = [r for r in roles if not self.role_allowed(r)]
        if bad:
            raise LocalError(f"本機帳號不可持有角色 {bad}（allowed_roles={self.allowed_roles}）")
        self._check_policy(password)
        t = self.now()
        acc = {"subject": secrets.token_hex(8), "name": name or username, "email": email, "roles": roles, "pw": hash_password(password), "totp_secret": base64.b32encode(secrets.token_bytes(20)).decode() if totp else None,
               "created_at": int(t), "expires_at": int(t + self.max_days * 86400), "failed": 0, "locked_until": 0, "last_totp_counter": -1, "disabled": False}
        self.data["accounts"][username] = acc; self._save()
        return self.public(username)

    def _check_policy(self, password: str) -> None:
        if len(password) < self.min_length:
            raise LocalError(f"密碼至少 {self.min_length} 字")
        if password.lower() in {"password", "passw0rd"} or password.isdigit():
            raise LocalError("密碼太簡單")

    def remove(self, username: str) -> None:
        self._sync()
        if self.data["accounts"].pop(username, None) is None:
            raise LocalError("帳號不存在")
        self._save()

    def reset_password(self, username: str, password: str) -> None:
        acc = self._get(username); self._check_policy(password)
        acc["pw"] = hash_password(password); acc["failed"] = 0; acc["locked_until"] = 0; acc["expires_at"] = int(self.now() + self.max_days * 86400)
        self._save()

    def reset_totp(self, username: str) -> str:
        acc = self._get(username)
        acc["totp_secret"] = base64.b32encode(secrets.token_bytes(20)).decode(); acc["last_totp_counter"] = -1
        self._save()
        return acc["totp_secret"]

    def set_disabled(self, username: str, disabled: bool) -> None:
        acc = self._get(username); acc["disabled"] = bool(disabled)
        if not disabled:
            acc["failed"] = 0; acc["locked_until"] = 0
        self._save()

    def update(self, username: str, name: str | None = None, email: str | None = None, roles: list[str] | None = None) -> dict[str, Any]:
        acc = self._get(username)
        if roles is not None:
            bad = [r for r in roles if not self.role_allowed(r)]
            if bad:
                raise LocalError(f"本機帳號不可持有角色 {bad}（allowed_roles={self.allowed_roles}）")
            acc["roles"] = list(roles)
        if name is not None: acc["name"] = name or username
        if email is not None: acc["email"] = email
        self._save()
        return self.public(username)

    def username_of(self, subject: str) -> str | None:
        """user_id 的 subject 部分 → 帳號名（成員目錄用）。"""
        self._sync()
        return next((u for u, a in self.data["accounts"].items() if a.get("subject") == subject), None)

    def _get(self, username: str) -> dict[str, Any]:
        self._sync()
        acc = self.data["accounts"].get(username)
        if not acc:
            raise LocalError("帳號不存在")
        return acc

    def public(self, username: str) -> dict[str, Any]:
        a = self._get(username)
        return {"username": username, "subject": a["subject"], "name": a["name"], "email": a["email"], "roles": a["roles"], "mfa": bool(a.get("totp_secret")), "expires_at": a["expires_at"],
                "disabled": a.get("disabled", False), "locked": a.get("locked_until", 0) > self.now(), "created_at": a.get("created_at")}

    def list(self) -> list[dict[str, Any]]:
        self._sync()
        return [self.public(u) for u in sorted(self.data["accounts"])]

    def verify_password(self, username: str, password: str) -> dict[str, Any]:
        """密碼正確 → 回帳號；錯誤一律「帳號或密碼錯誤」（不洩漏帳號是否存在），連錯 lockout_after 次鎖 lockout_minutes。"""
        self._sync()
        acc = self.data["accounts"].get(username)
        if not acc or acc.get("disabled"):
            hashlib.scrypt(b"x", salt=b"0" * 16, **SCRYPT)                 # 等時：不存在的帳號也算一次雜湊
            raise LocalError("帳號或密碼錯誤")
        t = self.now()
        if acc.get("locked_until", 0) > t:
            raise LocalError(f"帳號已鎖定，請 {int((acc['locked_until'] - t) // 60) + 1} 分鐘後再試")
        if not check_password(acc["pw"], password):
            acc["failed"] = int(acc.get("failed", 0)) + 1
            if acc["failed"] >= self.lockout_after:
                acc["locked_until"] = t + self.lockout_s; acc["failed"] = 0
            self._save()
            raise LocalError("帳號或密碼錯誤")
        if t > acc["expires_at"]:
            raise LocalError("帳號已到期，請管理員重設密碼以延展")
        if acc.get("failed"):
            acc["failed"] = 0; self._save()
        return acc

    def verify_totp(self, username: str, code: str) -> bool:
        self._sync()
        acc = self.data["accounts"].get(username)
        if not acc or not acc.get("totp_secret") or not re.fullmatch(r"\d{6}", code or ""):
            return False
        now_counter = int(self.now() // 30)
        for c in (now_counter, now_counter - 1, now_counter + 1):
            if c > int(acc.get("last_totp_counter", -1)) and hmac.compare_digest(_hotp(acc["totp_secret"], c), code):
                acc["last_totp_counter"] = c; self._save()
                return True
        return False


class LocalProvider:
    """與 LdapProvider 同介面（cfg、authenticate）→ Identity(provider='local')；roles 放 raw['roles'] 由 mapping.map_roles 採用。"""

    def __init__(self, accounts: LocalAccounts, cfg: dict[str, Any] | None = None, now: Callable[[], float] = time.time):
        self.key = "local"
        self.accounts, self.now = accounts, now
        self.cfg = {"kind": "ldap", "cn": "本機帳號", "mfa": "totp", **(cfg or {})}
        self.rl_account = RateLimiter(5, now); self.rl_ip = RateLimiter(30, now)

    def authenticate(self, username: str, password: str, ip: str = "") -> Identity:
        if not self.rl_account.allow(username) or (ip and not self.rl_ip.allow(ip)):
            raise LocalError("嘗試太頻繁，請稍後再試")
        acc = self.accounts.verify_password(username, password)
        roles = [r for r in acc["roles"] if self.accounts.role_allowed(r)]
        return Identity("local", acc["subject"], username, acc.get("email", ""), acc.get("name", username), [], amr=["pwd"], auth_time=int(self.now()), raw={"roles": roles})

    def check_totp(self, username: str, code: str) -> bool:
        return self.accounts.verify_totp(username, code)


def selftest() -> None:
    import tempfile
    t = [1_800_000_000.0]
    now = lambda: t[0]
    path = Path(tempfile.mkdtemp(prefix="dmp-local-")) / "accounts.json"
    la = LocalAccounts(path, {"min_length": 14, "lockout_after": 3, "lockout_minutes": 15, "max_days": 90}, ["custom_*"], now)
    try: la.add("ana", "correct-horse-battery", ["admin"]); raise AssertionError("admin 不該允許")
    except LocalError as e: assert "allowed_roles" in str(e)
    try: la.add("ana", "short", ["custom_vendor_analyst"]); raise AssertionError
    except LocalError as e: assert "至少" in str(e)
    la2 = LocalAccounts(path, {"min_length": 14, "lockout_after": 3, "lockout_minutes": 15, "max_days": 90}, ["*"], now)
    pub = la2.add("ana", "correct-horse-battery", ["admin", "data_analyst"], name="分析師", email="ana@x.test")
    assert pub["mfa"] and pub["roles"] == ["admin", "data_analyst"] and json.loads(path.read_text())["accounts"]["ana"]["pw"]["alg"] == "scrypt"
    assert "correct-horse" not in path.read_text()
    p = LocalProvider(la2, now=now)
    ident = p.authenticate("ana", "correct-horse-battery", "10.0.0.1"); assert ident.provider == "local" and ident.raw["roles"] == ["admin", "data_analyst"] and ident.amr == ["pwd"]
    for _ in range(2):
        try: p.authenticate("ana", "wrong-password-xx", "10.0.0.1"); raise AssertionError
        except LocalError as e: assert str(e) == "帳號或密碼錯誤"
    try: p.authenticate("ana", "wrong-password-xx", "10.0.0.1"); raise AssertionError
    except LocalError: pass
    try: p.authenticate("ana", "correct-horse-battery", "10.0.0.1"); raise AssertionError("應鎖定")
    except LocalError as e: assert "鎖定" in str(e), e
    try: p.authenticate("ana", "correct-horse-battery", "10.0.0.1"); raise AssertionError("速率限制：1 分鐘 5 次已用完")
    except LocalError as e: assert "頻繁" in str(e)
    t[0] += 16 * 60
    assert p.authenticate("ana", "correct-horse-battery").username == "ana"
    try: p.authenticate("nobody", "correct-horse-battery"); raise AssertionError
    except LocalError as e: assert str(e) == "帳號或密碼錯誤"
    sec = json.loads(path.read_text())["accounts"]["ana"]["totp_secret"]
    code = totp_code(sec, now()); assert len(code) == 6 and p.check_totp("ana", code) and not p.check_totp("ana", code)      # 同一格不可重放
    assert p.check_totp("ana", totp_code(sec, now() + 30)) and not p.check_totp("ana", "000000") and not p.check_totp("ana", "12345")
    assert totp_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"      # RFC 6238 附錄 B 向量（SHA1、秘密 12345678901234567890）
    assert totp_uri("ana", sec).startswith("otpauth://totp/AltaTech%20DMP:ana?secret=")
    t[0] += 91 * 86400
    try: p.authenticate("ana", "correct-horse-battery"); raise AssertionError
    except LocalError as e: assert "到期" in str(e)
    la2.reset_password("ana", "another-long-password"); assert LocalProvider(la2, now=now).authenticate("ana", "another-long-password")
    la2.remove("ana"); assert la2.list() == []
    print("local accounts self-test ok: scrypt/lockout/rate-limit/expiry/totp(rfc6238 vector, replay)")


def main(argv: list[str] | None = None) -> None:
    import argparse
    import getpass

    import yaml  # type: ignore
    ap = argparse.ArgumentParser(description="本機帳號管理")
    ap.add_argument("--file", default=os.environ.get("DMP_LOCAL_ACCOUNTS", ""), help="帳號檔（預設 DMP_LOCAL_ACCOUNTS）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("username"); a.add_argument("--roles", required=True); a.add_argument("--name", default=""); a.add_argument("--email", default=""); a.add_argument("--no-totp", action="store_true")
    sub.add_parser("list"); sub.add_parser("selftest")
    for c in ("remove", "reset-password", "reset-totp", "code"):
        sub.add_parser(c).add_argument("username")
    ns = ap.parse_args(argv)
    if ns.cmd == "selftest":
        selftest(); return
    if not ns.file:
        sys.exit("需要 --file 或 DMP_LOCAL_ACCOUNTS")
    cfg = yaml.safe_load((Path(__file__).resolve().parent / "idp.yaml").read_text(encoding="utf-8"))["local_accounts"]
    allowed = [x.strip() for x in os.environ.get("DMP_LOCAL_ALLOWED_ROLES", "").split(",") if x.strip()] or list(cfg.get("allowed_roles") or ["custom_*"])
    la = LocalAccounts(Path(ns.file), {**cfg.get("password_policy", {}), "max_days": cfg.get("max_days", 90)}, allowed)

    def ask_password() -> str:
        pw = os.environ.get("DMP_PASSWORD") or getpass.getpass("密碼（不會顯示）：")
        return pw
    try:
        if ns.cmd == "add":
            pub = la.add(ns.username, ask_password(), [r.strip() for r in ns.roles.split(",") if r.strip()], ns.name, ns.email, totp=not ns.no_totp)
            print(json.dumps(pub, ensure_ascii=False))
            if pub["mfa"]:
                print("TOTP 註冊（加到 Google Authenticator／Microsoft Authenticator）：", totp_uri(ns.username, la.data["accounts"][ns.username]["totp_secret"]))
        elif ns.cmd == "list":
            for x in la.list():
                print(json.dumps(x, ensure_ascii=False))
        elif ns.cmd == "remove":
            la.remove(ns.username); print("removed")
        elif ns.cmd == "reset-password":
            la.reset_password(ns.username, ask_password()); print("password reset；到期日已延展")
        elif ns.cmd == "reset-totp":
            print(totp_uri(ns.username, la.reset_totp(ns.username)))
        elif ns.cmd == "code":
            sec = la.data["accounts"][ns.username].get("totp_secret") if ns.username in la.data["accounts"] else None
            if not sec:
                sys.exit("帳號不存在或未啟用 TOTP")
            print(totp_code(sec))
    except LocalError as e:
        sys.exit(f"錯誤：{e}")


if __name__ == "__main__":
    main()
