"""IAM 路由：角色（內建唯讀 + 自訂 CRUD）、使用者指派、行級規則模擬。寫入需 iam.admin（authorize），一律寫審計。

自訂角色會註冊進 security.policy 的 RBAC['roles']（與內建角色同一份查表），所以 authorize() 不用改就認得自訂角色。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..security.policy import RBAC, Decision, Subject, authorize
from ..semantic.compiler import Catalog, compile_query
from .store import Store

ACTIONS = ["view", "use", "export", "approve", "admin"]
IMPLIES = {"use": ["view"], "export": ["use", "view"], "approve": ["view"], "admin": ["view"]}
PREFIX = RBAC["custom_roles"]["key_prefix"]


def role_view(key: str, r: dict[str, Any], builtin: bool) -> dict[str, Any]:
    g = r.get("grants", {})
    grants = {m: [a for a in ACTIONS if a in set(g.get(m, [])) | set(g.get("*", []))] for m in RBAC["modules"] if (g.get(m) or g.get("*"))}
    return {"key": key, "name": r.get("cn") or r.get("name") or key, "description": r.get("note") or r.get("description") or "", "scope": r["scope"],
            "lookback_days": r.get("lookback_days") or 0, "grants": grants, "masking_profile": r.get("masking_profile", "default"), "builtin": builtin}


def normalize_grants(grants: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m, acts in (grants or {}).items():
        if m not in RBAC["modules"]:
            raise HTTPException(400, f"未知模組 {m}")
        s = set(acts or [])
        if not s <= set(ACTIONS):
            raise HTTPException(400, f"未知動作 {sorted(s - set(ACTIONS))}")
        for a in list(s):
            s.update(IMPLIES.get(a, []))              # 可用蘊含可見、匯出蘊含可用
        if s:
            out[m] = [a for a in ACTIONS if a in s]
    return out


def register_custom_roles(store: Store) -> None:
    for r in store.roles():
        RBAC["roles"][r["key"]] = {"cn": r["name"], "scope": r["scope"], "lookback_days": r.get("lookback_days") or None, "grants": r["grants"], "note": r.get("description", "")}


def build_router(store: Store, get_subject: Any) -> APIRouter:
    r = APIRouter()
    register_custom_roles(store)

    def need(subject: Subject, action: str) -> Decision:
        d = authorize(subject, action, "iam")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return d

    @r.get("/v1/iam/roles")
    def roles(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        builtin = [role_view(k, v, True) for k, v in RBAC["roles"].items() if not k.startswith(PREFIX)]
        return builtin + [role_view(x["key"], {**x, "cn": x["name"], "note": x.get("description", "")}, False) for x in store.roles()]

    @r.post("/v1/iam/roles", status_code=201)
    def create_role(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name 必填")
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]
        key = body.get("key") or PREFIX + (slug or "r" + uuid.uuid4().hex[:8])      # 中文名稱沒有 ASCII slug → 隨機尾碼
        if not key.startswith(PREFIX):
            raise HTTPException(400, f"自訂角色 key 必須以 {PREFIX} 開頭")
        if key in RBAC["roles"]:
            raise HTTPException(409, f"角色 {key} 已存在")
        role = {"key": key, "name": name, "description": str(body.get("description") or ""), "scope": body.get("scope", "global"), "lookback_days": int(body.get("lookback_days") or 0),
                "grants": normalize_grants(body.get("grants", {})), "masking_profile": body.get("masking_profile", "default")}
        if role["scope"] not in ("global", "project", "segment", "aggregate_only", "branch", "agent"):
            raise HTTPException(400, "scope 不合法")
        if "iam" in role["grants"] and "admin" in role["grants"]["iam"]:
            raise HTTPException(400, "自訂角色不能有權限中心的設定權")
        store.put_role(role); register_custom_roles(store)
        store.audit("role.changed", {"user": subject.user_id, "role": key, "op": "create", "needs_second_review": True})
        return role_view(key, {**role, "cn": name, "note": role["description"]}, False)

    @r.put("/v1/iam/roles/{key}")
    def update_role(key: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        cur = next((x for x in store.roles() if x["key"] == key), None)
        if not cur:
            raise HTTPException(404 if key not in RBAC["roles"] else 403, "內建角色由 rbac.yaml 定義，不可在 UI 修改" if key in RBAC["roles"] else "角色不存在")
        for f in ("name", "description", "scope", "masking_profile"):
            if f in body: cur[f] = body[f]
        if "lookback_days" in body: cur["lookback_days"] = int(body["lookback_days"] or 0)
        if "grants" in body: cur["grants"] = normalize_grants(body["grants"])
        store.put_role(cur); register_custom_roles(store)
        store.audit("role.changed", {"user": subject.user_id, "role": key, "op": "update", "needs_second_review": True})
        return role_view(key, {**cur, "cn": cur["name"], "note": cur.get("description", "")}, False)

    @r.delete("/v1/iam/roles/{key}", status_code=204)
    def delete_role(key: str, reassign_to: str | None = None, subject: Subject = Depends(get_subject)) -> None:
        need(subject, "admin")
        if key == "admin" or (key in RBAC["roles"] and not key.startswith(PREFIX)):
            raise HTTPException(403, "內建角色不可刪除")
        if not any(x["key"] == key for x in store.roles()):
            raise HTTPException(404, "角色不存在")
        holders = [a for a in store.assignments() if key in a.get("roles", [])]
        if holders and not reassign_to:
            raise HTTPException(409, f"仍有 {len(holders)} 筆指派使用此角色，請帶 reassign_to 改派")
        for a in holders:
            a["roles"] = [reassign_to if x == key else x for x in a["roles"]]; store.put_assignment(a)
        store.delete_role(key); RBAC["roles"].pop(key, None)
        store.audit("role.deleted", {"user": subject.user_id, "role": key, "reassigned": len(holders), "to": reassign_to, "needs_second_review": True})

    @r.post("/v1/iam/roles/{key}/copy", status_code=201)
    def copy_role(key: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        src = RBAC["roles"].get(key)
        if not src:
            raise HTTPException(404, "角色不存在")
        v = role_view(key, src, not key.startswith(PREFIX))
        return create_role({"name": v["name"] + "（副本）", "description": v["description"], "scope": "global" if v["scope"] not in ("project", "segment", "aggregate_only") else v["scope"],
                            "lookback_days": v["lookback_days"], "grants": {m: [a for a in acts if not (m == "iam" and a == "admin")] for m, acts in v["grants"].items()}}, subject)

    @r.get("/v1/iam/assignments")
    def assignments(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return sorted(store.assignments(), key=lambda a: a.get("user_id", ""))

    @r.post("/v1/iam/assignments", status_code=201)
    def create_assignment(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        roles = list(body.get("roles") or [])
        if not body.get("user_id") or not roles:
            raise HTTPException(400, "user_id 與 roles 必填")
        unknown = [x for x in roles if x not in RBAC["roles"]]
        if unknown:
            raise HTTPException(400, f"未知角色 {unknown}")
        if "admin" in roles and not body.get("break_glass"):
            raise HTTPException(400, "admin 不能用本地指派給；臨時提權請走 break-glass（需雙人核准）")
        if not body.get("reason"):
            raise HTTPException(400, "reason 必填")
        a = {"user_id": body["user_id"], "roles": roles, "scope": body.get("scope") or {}, "expires_at": body.get("expires_at"), "reason": body["reason"],
             "deny": bool(body.get("deny")), "created_by": subject.user_id, "temp": bool(body.get("break_glass"))}
        store.put_assignment(a)
        store.audit("iam.assignment.created", {"user": subject.user_id, "target": a["user_id"], "roles": roles, "id": a["id"]})
        return a

    @r.patch("/v1/iam/assignments/{id_}")
    def patch_assignment(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        a = next((x for x in store.assignments() if x["id"] == id_), None)
        if not a:
            raise HTTPException(404, "指派不存在")
        for f in ("scope", "expires_at", "reason"):
            if f in body: a[f] = body[f]
        store.put_assignment(a); store.audit("iam.assignment.changed", {"user": subject.user_id, "id": id_})
        return a

    @r.delete("/v1/iam/assignments/{id_}", status_code=204)
    def delete_assignment(id_: str, subject: Subject = Depends(get_subject)) -> None:
        need(subject, "admin")
        if not any(x["id"] == id_ for x in store.assignments()):
            raise HTTPException(404, "指派不存在")
        store.delete_assignment(id_); store.audit("iam.assignment.revoked", {"user": subject.user_id, "id": id_})

    @r.post("/v1/iam/scope/simulate")
    def simulate(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        """給角色 + 範圍，回 Policy Engine 的行級條件、遮罩欄位、K，以及查詢編譯器對一條範例查詢實際注入後的 SQL。"""
        need(subject, "view")
        role = body.get("role")
        if role not in RBAC["roles"]:
            raise HTTPException(400, "未知角色")
        sc = body.get("scope") or {}
        sim = Subject("sim", [role], branches=list(sc.get("branches", [])), agents=list(sc.get("agents", [])))
        d = authorize(sim, "use", "events")
        if not d.allowed:
            return {"where": [], "masked_fields": [], "k_anonymity": d.k_anonymity, "allowed": False, "reason": d.reason, "sql": None}
        if sc.get("lookback_days"):
            d.row_filters.append(f"event_date >= today()-{int(sc['lookback_days'])}")
        c = compile_query({"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount"}, {"event": "bond_deal", "agg": "users"}], "group_by": ["platform"],
                           "time": {"from": "2026-10-01", "to": "2026-10-16"}}, decision=d, cat=Catalog())
        return {"where": d.row_filters, "masked_fields": sorted(k for k, v in d.field_policies.items() if v != "plain"), "k_anonymity": d.k_anonymity, "allowed": True,
                "sql": c.sql, "layer": c.layer, "masked_metrics": c.masked}

    @r.get("/v1/audit")
    def audit(n: int = 100, request: Request = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        d = authorize(subject, "view", "audit")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        rows = store.audit_tail(min(n, 1000))
        if "admin" not in subject.roles and "digital_ops" not in subject.roles and "it_admin" not in subject.roles:
            rows = [x for x in rows if x.get("user") == subject.user_id]     # 行銷／分析只看自己
        return rows

    return r
