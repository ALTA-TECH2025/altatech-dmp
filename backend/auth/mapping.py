"""IdP 群組 → 角色（idp.yaml role_mapping）＋ 本地指派疊加（/v1/iam/assignments）＋ /v1/iam/me 的前端啟動資料。

前端只依 /v1/iam/me 的 grants／masking 決定畫面（選單、按鈕、唯讀）；最終裁決仍是後端每個請求的 authorize()。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..security.policy import LEVEL_ORDER, MASK, RBAC, Subject, _grants
from .oidc import Identity

ACTIONS = ["view", "use", "export", "approve", "admin"]


def map_roles(ident: Identity, mapping: dict[str, Any], assignments: list[dict[str, Any]] | None = None, now: float | None = None) -> tuple[list[str], dict[str, Any], list[str]]:
    """回傳 (roles, scope, applied_rules)。群組比對：Entra 用 Object ID（entra_id）、其他用群組名（不分大小寫）。"""
    roles: list[str] = []; applied: list[str] = []; scope: dict[str, Any] = {}
    gset = {g.lower() for g in ident.groups}
    if ident.provider == "local":                                   # 本機帳號：角色寫在帳號裡（已過 allowed_roles 白名單），不走群組
        roles = list(dict.fromkeys(ident.raw.get("roles") or [])); applied.append("local:account")
    for rule in mapping.get("by_group", []) if ident.provider != "local" else []:
        hit = (rule.get("entra_id", "").lower() in gset) if ident.provider == "entra" else (str(rule.get("group", "")).lower() in gset)
        if hit:
            roles += [r for r in rule["roles"] if r not in roles]; applied.append(rule.get("group", rule.get("entra_id")))
    if not roles:
        roles = list(mapping.get("default_roles") or [])
    if mapping.get("assignment_overlay", True):
        roles, scope, more = overlay_assignments((ident.provider + ":" + ident.subject, ident.username), roles, assignments, now)
        applied += more
    roles = [r for r in roles if r in RBAC["roles"] or r.startswith(RBAC["custom_roles"]["key_prefix"])]
    return roles, scope, applied


def overlay_assignments(user_ids: tuple[str, ...], base_roles: list[str], assignments: list[dict[str, Any]] | None, now: float | None = None) -> tuple[list[str], dict[str, Any], list[str]]:
    """本機指派疊加（登入與背景排程共用同一套規則）：到期的略過、deny 移除、其餘加上（只收 RBAC 內或自訂前綴的角色）並合併 scope。回 (roles, scope, applied)。"""
    t = now if now is not None else time.time()
    roles = list(base_roles); scope: dict[str, Any] = {}; applied: list[str] = []
    for a in assignments or []:
        if a.get("user_id") not in user_ids:
            continue
        if a.get("expires_at") and float(a["expires_at"]) < t:
            continue
        if a.get("deny"):
            roles = [r for r in roles if r not in a["roles"]]; applied.append(f"deny:{a.get('id')}")
        else:
            roles += [r for r in a["roles"] if r not in roles and r in RBAC["roles"] or r.startswith(RBAC["custom_roles"]["key_prefix"])]
            applied.append(f"assign:{a.get('id')}")
            for k, v in (a.get("scope") or {}).items():
                scope[k] = v
    roles = [r for r in roles if r in RBAC["roles"] or r.startswith(RBAC["custom_roles"]["key_prefix"])]
    return roles, scope, applied


def grants_of(roles: list[str]) -> dict[str, list[str]]:
    """多角色功能聯集；每個模組回 [view, use, export, approve, admin] 的子集（順序固定，前端畫矩陣用）。"""
    out: dict[str, list[str]] = {}
    for m in RBAC["modules"]:
        acts = set().union(*(_grants(r, m) for r in roles if r in RBAC["roles"])) if roles else set()
        if acts:
            out[m] = [a for a in ACTIONS if a in acts]
    return out


def masking_of(roles: list[str]) -> dict[str, dict[str, str]]:
    """每個敏感欄位對此身分的策略（取最嚴格）：{field: {level, policy}}。"""
    out = {}
    for f in MASK["fields"]:
        plain = f.get("plain_roles", [])
        is_plain = "*" in plain or any(r in plain for r in roles)
        out[f["name"]] = {"level": f["level"], "policy": "plain" if is_plain else f.get("others", "hide")}
    return out


def me_payload(subject: Subject, session: Any, roles_full: list[str], idp_mode: str, step_up_needed: bool) -> dict[str, Any]:
    """GET /v1/iam/me：前端 AppShell 啟動所需的一切。roles_full = session 內的角色；subject.roles = 目前有效（admin 可能因未 step-up 暫時拿掉）。"""
    scopes = {RBAC["roles"][r]["scope"] for r in subject.roles if r in RBAC["roles"]}
    return {
        "user": {"id": subject.user_id, "username": session.username, "name": session.name, "email": session.email, "provider": session.provider},
        "roles": [{"key": r, "cn": RBAC["roles"].get(r, {}).get("cn", r), "active": r in subject.roles} for r in roles_full],
        "grants": grants_of(subject.roles),
        "masking": masking_of(subject.roles),
        "scope": {"kind": "global" if "global" in scopes else ("aggregate_only" if "aggregate_only" in scopes else "restricted"), **(session.scope or {}),
                  "k_anonymity": RBAC["k_anonymity"]["min_group_size"], "lookback_days": max((RBAC["roles"][r].get("lookback_days") or 0 for r in subject.roles if r in RBAC["roles"]), default=0) or None},
        "session": {"idp_mode": idp_mode, "amr": session.amr, "auth_time": session.auth_time, "step_up_needed": step_up_needed, "csrf_header": "X-CSRF-Token"},
        "levels": LEVEL_ORDER,
    }


def audit_sink(sink: list[dict[str, Any]]) -> Callable[[str, dict[str, Any]], None]:
    def write(event: str, data: dict[str, Any]) -> None:
        sink.append({"event": event, "at": time.time(), **data})
    return write
