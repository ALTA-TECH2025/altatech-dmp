"""IAM 路由：角色（內建唯讀 + 自訂 CRUD）、使用者指派、行級規則模擬、權限變更審核。寫入需 iam.admin（authorize），一律寫審計。

自訂角色會註冊進 security.policy 的 RBAC['roles']（與內建角色同一份查表），所以 authorize() 不用改就認得自訂角色。
雙人複核（rbac.yaml custom_roles.second_review）：自訂角色的建立／修改／刪除不直接生效，先存 role_change（含前後快照）並開審批單 type=role_change；
另一位系統管理員或數位經營核可（不可自審）後由 hook 寫入並註冊。臨時提權（break_glass）同理：審批單核可後才建立 ≤ max_hours 的到期指派。
沒有注入 ApprovalService（舊測試）或 second_review 關閉時維持直接生效。
"""
from __future__ import annotations

import contextlib
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ..security.policy import RBAC, Decision, Subject, authorize
from ..semantic.compiler import Catalog, compile_query
from .store import Store

ACTIONS = ["view", "use", "export", "approve", "admin"]
IMPLIES = {"use": ["view"], "export": ["use", "view"], "approve": ["view"], "admin": ["view"]}
PREFIX = RBAC["custom_roles"]["key_prefix"]
SECOND_REVIEW = RBAC["custom_roles"].get("second_review") or {}
BREAK_GLASS = RBAC.get("assignment", {}).get("break_glass") or {"max_hours": 4}
SCOPES = ("global", "project", "segment", "aggregate_only", "branch", "agent")


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


def validate_role(body: dict[str, Any], cur: dict[str, Any] | None = None) -> dict[str, Any]:
    """建立（cur=None）或更新：回完整角色 dict；錯誤 → 400。"""
    role = dict(cur or {})
    if cur is None:
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
    else:
        for f in ("name", "description", "scope", "masking_profile"):
            if f in body: role[f] = body[f]
        if "lookback_days" in body: role["lookback_days"] = int(body["lookback_days"] or 0)
        if "grants" in body: role["grants"] = normalize_grants(body["grants"])
    if role["scope"] not in SCOPES:
        raise HTTPException(400, "scope 不合法")
    if "iam" in role["grants"] and "admin" in role["grants"]["iam"]:
        raise HTTPException(400, "自訂角色不能有權限中心的設定權")
    return role


def build_router(store: Store, get_subject: Any, approvals: Any | None = None) -> APIRouter:
    r = APIRouter()
    register_custom_roles(store)
    review_on = bool(approvals is not None and SECOND_REVIEW.get("enabled"))

    def need(subject: Subject, action: str) -> Decision:
        d = authorize(subject, action, "iam")
        if not d.allowed:
            raise HTTPException(403, d.reason)
        return d

    # ---- 生效：直接套用（無雙人複核）或審批 hook 呼叫 ----
    def apply_role_change(op: str, key: str, role: dict[str, Any] | None, by: str, reviewer: str | None = None, reassign_to: str | None = None) -> None:
        if op == "delete":
            holders = [a for a in store.assignments() if key in a.get("roles", [])]
            for a in holders:
                a["roles"] = [reassign_to if x == key else x for x in a["roles"]]; store.put_assignment(a)
            store.delete_role(key); RBAC["roles"].pop(key, None)
            store.audit("role.deleted", {"user": by, "role": key, "reassigned": len(holders), "to": reassign_to, "reviewed_by": reviewer})
            return
        assert role is not None
        store.put_role(role); register_custom_roles(store)
        store.audit("role.changed", {"user": by, "role": key, "op": op, "reviewed_by": reviewer, "needs_second_review": reviewer is None and review_on})

    def propose(op: str, key: str, role: dict[str, Any] | None, before: dict[str, Any] | None, subject: Subject, reassign_to: str | None = None) -> JSONResponse:
        chg = {"id": "rc_" + uuid.uuid4().hex[:10], "op": op, "role": key, "before": before, "after": role, "reassign_to": reassign_to, "requested_by": subject.user_id, "status": "pending", "created_at": time.time()}
        store.put_item("role_change", chg)
        cn = (role or before or {}).get("name", key)
        a = approvals.create("role_change", subject, f"角色{ {'create': '新增', 'update': '修改', 'delete': '刪除'}[op] }：{cn}（{key}）",
                             {"op": op, "role": key, "before": before, "after": role, "reassign_to": reassign_to}, {"kind": "role_change", "id": chg["id"]})
        chg["approval"] = a["id"]; store.put_item("role_change", chg)
        store.audit("role.change_requested", {"user": subject.user_id, "role": key, "op": op, "approval": a["id"], "change": chg["id"]})
        return JSONResponse(status_code=202, content={"status": "pending_review", "approval": a["id"], "change": chg["id"], "message": "已送審：需另一位系統管理員或數位經營核可後生效"})

    def on_role_decided(a: dict[str, Any], approve: bool, by: Subject) -> None:
        chg = next((x for x in store.items("role_change") if x["id"] == a["object"]["id"]), None)
        if not chg or chg["status"] != "pending":
            return
        chg["status"] = "approved" if approve else ("rejected" if by.user_id != "system:expiry" else "expired"); chg["decided_by"] = by.user_id; chg["decided_at"] = time.time()
        if approve:
            if chg["op"] == "delete":
                apply_role_change("delete", chg["role"], None, chg["requested_by"], by.user_id, chg.get("reassign_to"))
            else:
                if chg["op"] == "update" and not any(x["key"] == chg["role"] for x in store.roles()):
                    chg["status"] = "rejected"; chg["note"] = "角色已不存在"
                else:
                    apply_role_change(chg["op"], chg["role"], chg["after"], chg["requested_by"], by.user_id)
        store.put_item("role_change", chg)

    def on_break_glass_decided(a: dict[str, Any], approve: bool, by: Subject) -> None:
        req = next((x for x in store.items("break_glass") if x["id"] == a["object"]["id"]), None)
        if not req or req["status"] != "pending":
            return
        req["status"] = "approved" if approve else ("rejected" if by.user_id != "system:expiry" else "expired"); req["decided_by"] = by.user_id; req["decided_at"] = time.time()
        if approve:
            exp = time.time() + req["hours"] * 3600
            asg = {"user_id": req["user_id"], "roles": ["admin"], "scope": {}, "expires_at": exp, "reason": req["reason"], "deny": False, "created_by": req["requested_by"], "temp": True, "approved_by": by.user_id}
            store.put_assignment(asg); req["assignment"] = asg["id"]
            store.audit("iam.assignment.created", {"user": req["requested_by"], "target": req["user_id"], "roles": ["admin"], "id": asg["id"], "temp": True, "approved_by": by.user_id, "expires_at": exp})
        store.put_item("break_glass", req)

    if approvals is not None:
        approvals.hooks["role_change"] = on_role_decided
        approvals.hooks["break_glass"] = on_break_glass_decided

    @r.get("/v1/iam/roles")
    def roles(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        builtin = [role_view(k, v, True) for k, v in RBAC["roles"].items() if not k.startswith(PREFIX)]
        return builtin + [role_view(x["key"], {**x, "cn": x["name"], "note": x.get("description", "")}, False) for x in store.roles()]

    @r.post("/v1/iam/roles", status_code=201)
    def create_role(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> Any:
        need(subject, "admin")
        role = validate_role(body)
        if review_on:
            return propose("create", role["key"], role, None, subject)
        apply_role_change("create", role["key"], role, subject.user_id)
        return role_view(role["key"], {**role, "cn": role["name"], "note": role["description"]}, False)

    @r.put("/v1/iam/roles/{key}")
    def update_role(key: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> Any:
        need(subject, "admin")
        cur = next((x for x in store.roles() if x["key"] == key), None)
        if not cur:
            raise HTTPException(404 if key not in RBAC["roles"] else 403, "內建角色由 rbac.yaml 定義，不可在 UI 修改" if key in RBAC["roles"] else "角色不存在")
        role = validate_role(body, cur)
        if review_on:
            return propose("update", key, role, cur, subject)
        apply_role_change("update", key, role, subject.user_id)
        return role_view(key, {**role, "cn": role["name"], "note": role.get("description", "")}, False)

    @r.delete("/v1/iam/roles/{key}", status_code=204)
    def delete_role(key: str, reassign_to: str | None = None, subject: Subject = Depends(get_subject)) -> Response:
        need(subject, "admin")
        if key == "admin" or (key in RBAC["roles"] and not key.startswith(PREFIX)):
            raise HTTPException(403, "內建角色不可刪除")
        cur = next((x for x in store.roles() if x["key"] == key), None)
        if not cur:
            raise HTTPException(404, "角色不存在")
        holders = [a for a in store.assignments() if key in a.get("roles", [])]
        if holders and not reassign_to:
            raise HTTPException(409, f"仍有 {len(holders)} 筆指派使用此角色，請帶 reassign_to 改派")
        if reassign_to and reassign_to not in RBAC["roles"]:
            raise HTTPException(400, "reassign_to 未知角色")
        if review_on:
            return propose("delete", key, None, cur, subject, reassign_to)
        apply_role_change("delete", key, None, subject.user_id, None, reassign_to)
        return Response(status_code=204)

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
    def create_assignment(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> Any:
        need(subject, "admin")
        roles = list(body.get("roles") or [])
        if not body.get("user_id") or not roles:
            raise HTTPException(400, "user_id 與 roles 必填")
        unknown = [x for x in roles if x not in RBAC["roles"]]
        if unknown:
            raise HTTPException(400, f"未知角色 {unknown}")
        if not body.get("reason"):
            raise HTTPException(400, "reason 必填")
        if body.get("break_glass"):
            # 臨時提權：只給 admin、≤ max_hours；需審批（申請者之外的系統管理員或數位經營）；沒有審批服務時不開放
            if roles != ["admin"]:
                raise HTTPException(400, "break-glass 只用於臨時取得 admin")
            hours = float(body.get("hours") or BREAK_GLASS.get("max_hours", 4))
            if not 0 < hours <= float(BREAK_GLASS.get("max_hours", 4)):
                raise HTTPException(400, f"臨時提權最長 {BREAK_GLASS.get('max_hours', 4)} 小時")
            if approvals is None:
                raise HTTPException(501, "此環境沒有審批服務，無法臨時提權")
            req = {"id": "bg_" + uuid.uuid4().hex[:10], "user_id": body["user_id"], "hours": hours, "reason": body["reason"], "requested_by": subject.user_id, "status": "pending", "created_at": time.time()}
            store.put_item("break_glass", req)
            a = approvals.create("break_glass", subject, f"臨時提權：{req['user_id']} → admin {hours:g} 小時", {"user_id": req["user_id"], "hours": hours, "reason": req["reason"]}, {"kind": "break_glass", "id": req["id"]})
            req["approval"] = a["id"]; store.put_item("break_glass", req)
            store.audit("iam.break_glass.requested", {"user": subject.user_id, "target": req["user_id"], "hours": hours, "approval": a["id"]})
            return JSONResponse(status_code=202, content={"status": "pending_review", "approval": a["id"], "request": req["id"], "message": "已送審：需另一位系統管理員或數位經營核可後生效"})
        if "admin" in roles:
            raise HTTPException(400, "admin 不能用本地指派給；臨時提權請走 break-glass（需雙人核准）")
        a = {"user_id": body["user_id"], "roles": roles, "scope": body.get("scope") or {}, "expires_at": body.get("expires_at"), "reason": body["reason"],
             "deny": bool(body.get("deny")), "created_by": subject.user_id, "temp": False}
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

    @r.get("/v1/iam/changes")
    def changes(status: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        """權限變更審核：角色變更（前後快照）與臨時提權申請，附審批單狀態。"""
        need(subject, "view")
        out = [{"kind": "role_change", **x} for x in store.items("role_change")] + [{"kind": "break_glass", **x} for x in store.items("break_glass")]
        if approvals is not None:
            for x in out:                                                   # 逾期由審批單判定（approvals.get 會把 pending 轉 expired 並呼叫 hook）
                if x.get("approval") and x["status"] == "pending":
                    with contextlib.suppress(KeyError):
                        approvals.get(x["approval"])
            out = [{"kind": "role_change", **x} for x in store.items("role_change")] + [{"kind": "break_glass", **x} for x in store.items("break_glass")]
        if status:
            out = [x for x in out if x["status"] == status]
        return sorted(out, key=lambda x: x.get("created_at", 0), reverse=True)

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
