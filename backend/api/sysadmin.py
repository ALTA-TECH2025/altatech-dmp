"""系統管理路由：成員目錄與本機帳號管理（/v1/iam/users*）、日誌統計看板與審計報表（/v1/audit/stats、/v1/audit/export）、平台設定唯讀（/v1/settings）。

- 成員目錄 = 登入審計看到的使用者（SSO）∪ 本機帳號 ∪ 本地指派；SSO 帳號的啟停在 IdP，這裡只能改本機帳號（白名單角色、強制 TOTP）。
- 日誌統計用審計尾端即時彙總（單機／示範足夠）；正式環境改由 dmp.audit_log 物化視圖供數（docs/10 §8，第二階段）。告警規則在 security/audit_alerts.yaml。
- 平台設定只讀設定檔，密鑰只顯示 secret_ref；線上修改（雙人複核）第二階段。
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml  # type: ignore
from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth.local import LocalAccounts, LocalError, totp_uri
from ..security.policy import MASK, RBAC, Subject, authorize, has_grant
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
ALERTS = yaml.safe_load((ROOT / "security" / "audit_alerts.yaml").read_text(encoding="utf-8"))
TAIL = 10_000
FULL_AUDIT_ROLES = ("admin", "digital_ops", "it_admin")
SENSITIVE_EVENTS = ("profile.viewed", "profile.timeline", "profile.identities")


def _need(subject: Subject, action: str, module: str) -> None:
    d = authorize(subject, action, module)
    if not d.allowed:
        raise HTTPException(403, d.reason)


# ---------------- 成員目錄 ----------------
def member_directory(store: Store, accounts: LocalAccounts | None, sessions: Any, now: float | None = None) -> list[dict[str, Any]]:
    now = now or time.time()
    rows = store.audit_tail(TAIL)
    m: dict[str, dict[str, Any]] = {}

    def ent(uid: str) -> dict[str, Any]:
        if uid not in m:
            prov = uid.split(":", 1)[0] if ":" in uid else ""
            m[uid] = {"id": uid, "username": uid.split(":", 1)[-1], "name": "", "email": "", "provider": prov, "source": "local" if prov == "local" else "sso", "roles": [], "status": "never_logged_in",
                      "mfa": False, "last_login": None, "login_failed_7d": 0, "sessions": 0, "assignments": 0, "expires_at": None, "last_roles": [], "base_roles": None}
        return m[uid]

    week = now - 7 * 86400
    for x in rows:
        ev = x.get("event", "")
        if ev in ("auth.login.success", "auth.login.no_role") and x.get("user"):
            e = ent(str(x["user"]))
            if (e["last_login"] or 0) <= float(x.get("at", 0)):
                e["last_login"] = float(x.get("at", 0)); e["last_roles"] = list(x.get("roles") or []); e["mfa"] = "mfa" in (x.get("amr") or [])
                e["base_roles"] = list(x["base_roles"]) if isinstance(x.get("base_roles"), list) else None     # 登入時的基底角色（不含指派）；舊審計沒有 → None
                e["status"] = "active" if x.get("roles") else "no_role"
        elif ev == "auth.login.failed" and x.get("user") and float(x.get("at", 0)) >= week:
            u = str(x["user"]); prov = str(x.get("provider") or "")
            uid = next((k for k, v in m.items() if v["username"] == u or k == u), None)
            if uid is None and accounts is not None and prov == "local":
                sub = next((a["subject"] for a in accounts.list() if a["username"] == u), None)
                uid = f"local:{sub}" if sub else None
            if uid: ent(uid)["login_failed_7d"] += 1
    for a in store.assignments():
        if a.get("expires_at") and isinstance(a["expires_at"], int | float) and float(a["expires_at"]) < now:
            continue
        e = ent(str(a["user_id"])); e["assignments"] += 1
        if not a.get("deny"):
            e["roles"] = [*e["roles"], *[r for r in a.get("roles", []) if r not in e["roles"]]]
    if accounts is not None:
        for a in accounts.list():
            e = ent(f"local:{a['subject']}")
            e.update({"username": a["username"], "name": a["name"], "email": a["email"], "source": "local", "provider": "local", "mfa": a["mfa"], "expires_at": a["expires_at"]})
            e["roles"] = [*a["roles"], *[r for r in e["roles"] if r not in a["roles"]]]
            if a["disabled"]: e["status"] = "disabled"
            elif a["locked"]: e["status"] = "locked"
            elif a["expires_at"] and a["expires_at"] < now: e["status"] = "expired"
            elif e["status"] == "never_logged_in" and e["last_login"]: e["status"] = "active"
    lister = getattr(sessions, "list_all", None) if sessions is not None else None
    for s in (lister() if lister else []):
        e = ent(s.user_id); e["sessions"] += 1
        if not e["name"]: e["name"], e["email"] = s.name, s.email
        if not e["username"] or e["username"] == e["id"].split(":", 1)[-1]: e["username"] = s.username
    for e in m.values():
        e["roles"] = [*e["last_roles"], *[r for r in e["roles"] if r not in e["last_roles"]]] if e["last_roles"] else e["roles"]
        e["login_roles"] = e.pop("last_roles", [])                              # 最近一次登入的角色快照（含當時指派）；只給診斷用，不拿來授權
        e["role_cn"] = [RBAC["roles"].get(r, {}).get("cn", r) for r in e["roles"]]
    return sorted(m.values(), key=lambda e: (-(e["last_login"] or 0), e["id"]))


# ---------------- 日誌統計 ----------------
def _tz() -> ZoneInfo:
    return ZoneInfo(str(ALERTS.get("business_hours", {}).get("tz") or "Asia/Taipei"))


def _off_hours(at: float) -> bool:
    bh = ALERTS.get("business_hours", {}); d = datetime.fromtimestamp(at, _tz())
    if bh.get("weekdays_only", True) and d.weekday() >= 5:
        return True
    return not (int(bh.get("start", 9)) <= d.hour < int(bh.get("end", 18)))


def evaluate_rules(rows: list[dict[str, Any]], rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """每條規則：event 在 window 內依 per 分組計數（或 distinct 欄位數）超過 threshold → 異常。field 規則：單筆數值欄位超過 threshold。"""
    out: list[dict[str, Any]] = []
    tz = _tz()
    for rule in rules if rules is not None else ALERTS.get("rules", []):
        ev = [x for x in rows if x.get("event") == rule["event"]]
        if rule.get("off_hours_only"):
            ev = [x for x in ev if _off_hours(float(x.get("at", 0)))]
        if rule.get("field"):
            for x in ev:
                v = x.get(rule["field"])
                if isinstance(v, int | float) and v > float(rule["threshold"]):
                    out.append({"rule": rule["key"], "cn": rule["cn"], "severity": rule.get("severity", "medium"), "user": x.get("user", ""), "bucket": datetime.fromtimestamp(float(x.get("at", 0)), tz).strftime("%Y-%m-%d %H:%M"), "n": v, "threshold": rule["threshold"]})
            continue
        fmt = "%Y-%m-%d %H:00" if rule.get("window") == "hour" else "%Y-%m-%d"
        distinct = bool(rule.get("distinct"))
        groups: dict[tuple[str, str], set[str] | int] = defaultdict(set if distinct else int)   # type: ignore[arg-type]
        for x in ev:
            k = (str(x.get(rule.get("per", "user"), "")), datetime.fromtimestamp(float(x.get("at", 0)), tz).strftime(fmt))
            if rule.get("distinct"):
                groups[k].add(str(x.get(rule["distinct"], "")))     # type: ignore[union-attr]
            else:
                groups[k] += 1                                    # type: ignore[operator]
        for (who, bucket), v in groups.items():
            n = len(v) if isinstance(v, set) else v
            if n >= int(rule["threshold"]) if rule.get("distinct") else n > int(rule["threshold"]):
                out.append({"rule": rule["key"], "cn": rule["cn"], "severity": rule.get("severity", "medium"), "user": who, "bucket": bucket, "n": n, "threshold": rule["threshold"]})
    sev = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda a: (sev.get(a["severity"], 9), a["bucket"]), reverse=False)[:50]


def audit_stats(store: Store, subject: Subject, days: int, approvals: Any | None = None, now: float | None = None) -> dict[str, Any]:
    now = now or time.time(); days = max(1, min(int(days), 90)); since = now - days * 86400
    full = any(r in subject.roles for r in FULL_AUDIT_ROLES) or authorize(subject, "use", "audit").allowed
    rows = [x for x in store.audit_tail(TAIL) if float(x.get("at", 0)) >= since and (full or x.get("user") == subject.user_id)]
    tz = _tz()
    day = lambda x: datetime.fromtimestamp(float(x.get("at", 0)), tz).strftime("%Y-%m-%d")
    counts = Counter(x.get("event", "") for x in rows)
    users = {x.get("user") for x in rows if x.get("user") and not str(x.get("user")).startswith("system:")}
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"logins": 0, "login_failed": 0, "denied": 0, "profile_views": 0, "exports": 0, "queries": 0, "events": 0})
    role_of: dict[str, list[str]] = {}
    for x in rows:
        d = daily[day(x)]; ev = x.get("event", ""); d["events"] += 1
        if ev == "auth.login.success": d["logins"] += 1; role_of[str(x.get("user"))] = list(x.get("roles") or [])
        elif ev == "auth.login.failed": d["login_failed"] += 1
        elif ev == "authz.denied": d["denied"] += 1
        elif ev in SENSITIVE_EVENTS: d["profile_views"] += 1
        elif ev == "export.downloaded": d["exports"] += 1
        elif ev == "query.executed": d["queries"] += 1
    by_module = Counter(str(x.get("module")) for x in rows if x.get("event") in ("query.executed", "users.listed", "profile.viewed") and x.get("module"))
    by_role: dict[str, dict[str, int]] = defaultdict(lambda: {"profile_views": 0, "queries": 0, "exports": 0})
    for x in rows:
        ev = x.get("event", "")
        if ev not in (*SENSITIVE_EVENTS, "query.executed", "export.downloaded"): continue
        for r in role_of.get(str(x.get("user")), ["(未知)"]) or ["(未知)"]:
            k = "profile_views" if ev in SENSITIVE_EVENTS else ("queries" if ev == "query.executed" else "exports")
            by_role[RBAC["roles"].get(r, {}).get("cn", r)][k] += 1
    sens: dict[str, Counter[str]] = defaultdict(Counter)
    for x in rows:
        if x.get("event") in SENSITIVE_EVENTS: sens[str(x.get("user"))][str(x.get("reason") or "")] += 1
    top = [{"user": u, "n": sum(c.values()), "reason": c.most_common(1)[0][0], "off_hours": sum(1 for x in rows if x.get("event") in SENSITIVE_EVENTS and x.get("user") == u and _off_hours(float(x.get("at", 0))))}
           for u, c in sens.items()]
    top.sort(key=lambda t: -t["n"])
    appr: dict[str, Any] = {"pending": 0, "approved": 0, "rejected": 0, "expired": 0, "avg_hours": None, "by_type": {}}
    if approvals is not None and full:
        hours: list[float] = []
        for a in store.items("approvals"):
            appr[a["status"]] = appr.get(a["status"], 0) + 1
            bt = appr["by_type"].setdefault(a["type"], {"pending": 0, "approved": 0, "rejected": 0, "expired": 0}); bt[a["status"]] = bt.get(a["status"], 0) + 1
            if a.get("decided_at") and a["status"] in ("approved", "rejected"):
                with contextlib.suppress(ValueError):
                    hours.append((datetime.fromisoformat(a["decided_at"]) - datetime.fromisoformat(a["created_at"])).total_seconds() / 3600)
        appr["avg_hours"] = round(sum(hours) / len(hours), 1) if hours else None
    agent_calls = counts.get("agent.tool_called", 0)
    return {
        "days": days, "scope": "all" if full else "mine", "from": datetime.fromtimestamp(since, tz).strftime("%Y-%m-%d"), "to": datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d"), "source": "audit_tail",
        "tiles": {"events": len(rows), "active_users": len(users), "logins": counts.get("auth.login.success", 0), "login_failed": counts.get("auth.login.failed", 0), "denied": counts.get("authz.denied", 0),
                  "profile_views": sum(counts.get(e, 0) for e in SENSITIVE_EVENTS), "reveals": counts.get("mask.reveal_requested", 0), "exports": counts.get("export.downloaded", 0),
                  "queries": counts.get("query.executed", 0), "approvals_pending": appr["pending"]},
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "by_event": [{"event": e, "n": n} for e, n in counts.most_common(12)],
        "by_module": [{"module": k, "n": n} for k, n in by_module.most_common()],
        "by_role": [{"role": r, **v} for r, v in sorted(by_role.items(), key=lambda kv: -kv[1]["profile_views"])],
        "top_sensitive": top[:10],
        "approvals": appr,
        "agent": {"tool_calls": agent_calls, "proposals": counts.get("agent.proposal", 0), "stage": "phase2" if agent_calls == 0 else "live"},
        "anomalies": evaluate_rules(rows) if full else [],
        "rules": [{k: v for k, v in r.items()} for r in ALERTS.get("rules", [])], "business_hours": ALERTS.get("business_hours", {}), "notify": ALERTS.get("notify", []),
    }


# ---------------- 平台設定（唯讀）----------------
def settings_view(auth_cfg: dict[str, Any], local_accounts: LocalAccounts | None) -> dict[str, Any]:
    tools = yaml.safe_load((ROOT / "mcp" / "tools.yaml").read_text(encoding="utf-8")) or {}
    agents = yaml.safe_load((ROOT / "insight_agent" / "agents.yaml").read_text(encoding="utf-8")) or {}
    sess = auth_cfg.get("session", {}); la = auth_cfg.get("local_accounts", {}); rm = auth_cfg.get("role_mapping", {})
    provs = [{"key": k, "cn": v.get("cn"), "kind": v.get("kind"), "endpoint": v.get("issuer") or v.get("url"), "mfa": v.get("mfa") or ("IdP" if v.get("kind") == "oidc" else None),
              "secret_refs": sorted(kk for kk in v if kk.endswith("_ref"))} for k, v in (auth_cfg.get("providers") or {}).items()]
    items = lambda **kw: [{"key": k, "cn": cn, "value": v, "note": n} for k, (cn, v, n) in kw.items()]
    sections = [
        {"key": "identity", "cn": "身分與登入", "source": "backend/auth/idp.yaml", "items": items(
            mode=("部署模式", auth_cfg.get("mode"), "broker = Keycloak 身分代理；direct = 直連 Entra ID／AD／本機帳號"),
            providers=("身分提供者", provs, "密鑰只存 secret_ref"),
            group_mapping=("群組 → 角色", [{"group": x.get("group"), "roles": x.get("roles")} for x in rm.get("by_group", [])], "IdP 群組對應內建角色；本地指派疊加（assignment_overlay）"),
            admin_requires=("admin 取得條件", rm.get("admin_requires"), "需 MFA 且認證在 max_auth_age_s 內，否則降級直到 step-up"),
            idle_timeout_s=("閒置逾時（秒）", sess.get("idle_timeout_s"), ""), absolute_timeout_s=("絕對逾時（秒）", sess.get("absolute_timeout_s"), ""),
            concurrent_sessions=("同帳號同時 session", sess.get("concurrent_sessions"), "超過踢最舊"), bind_ip=("綁定 IP", sess.get("bind_ip"), "金融業常要求 true"),
            api_token=("API token", {k: v for k, v in (sess.get("api_token") or {}).items()}, "MCP／排程／CLI 短效 token"),
            local_accounts=("本機帳號", {"enabled": la.get("enabled"), "allowed_roles": local_accounts.allowed_roles if local_accounts else la.get("allowed_roles"), "mfa": la.get("mfa"), "max_days": la.get("max_days"), **(la.get("password_policy") or {})},
                            "只給外包／廠商；強制 TOTP、到期、白名單角色"),
        )},
        {"key": "rbac", "cn": "權限", "source": "backend/security/rbac.yaml", "items": items(
            roles=("內建角色", [{"key": k, "cn": v.get("cn"), "scope": v.get("scope"), "modules": len([m for m in RBAC["modules"] if (v.get("grants", {}).get(m) or v.get("grants", {}).get("*"))])} for k, v in RBAC["roles"].items() if not k.startswith(RBAC["custom_roles"]["key_prefix"])], "模組 × 動作矩陣見權限中心"),
            custom_roles=("自訂角色", {"enabled": RBAC["custom_roles"].get("enabled"), "key_prefix": RBAC["custom_roles"].get("key_prefix")}, ""),
            second_review=("角色變更雙人複核", RBAC["custom_roles"].get("second_review"), "自訂角色建立／修改／刪除先成審批單"),
            break_glass=("臨時提權", RBAC.get("assignment", {}).get("break_glass"), "核可後才建立到期指派"),
            combination=("多角色合併", RBAC.get("combination"), ""), k_anonymity=("K 匿名", RBAC.get("k_anonymity"), "分組人數低於門檻 → 指標抑制"),
        )},
        {"key": "masking", "cn": "隱碼與揭露", "source": "backend/security/masking.yaml", "items": items(
            levels=("敏感等級", MASK.get("levels"), "L4 永不進分析倉"),
            fields=("欄位策略", [{"name": f["name"], "level": f["level"], "plain_roles": f.get("plain_roles"), "others": f.get("others", "plain"), "export": f.get("export")} for f in MASK.get("fields", [])], "依角色取最嚴格"),
            reveal=("揭露", MASK.get("reveal"), "需理由 + 審批；到期自動收回"), export=("匯出", MASK.get("export"), "比 UI 嚴一級、浮水印、列數上限"),
            agent=("agent 回傳", MASK.get("agent"), "回傳前套遮罩；敘述正則阻擋"),
        )},
        {"key": "agent", "cn": "Agent 預算", "source": "backend/mcp/tools.yaml、backend/insight_agent/agents.yaml", "items": items(
            gateway=("工具閘道預算", (tools.get("gateway") or {}).get("budgets"), "每次呼叫／每次 run／每 agent 每日"),
            policy=("閘道政策", (tools.get("gateway") or {}).get("policy"), ""),
            agents=("agent 預算", [{"key": a.get("key") or a.get("name"), "budget": a.get("budget")} for a in (agents.get("agents") or [])] if isinstance(agents.get("agents"), list) else agents.get("agents"), "第一階段 AI 閘道關閉（docs/22 §1）"),
            tools=("工具數", len(tools.get("tools") or []), "backend/mcp/tools.yaml"),
        )},
        {"key": "alerts", "cn": "審計告警", "source": "backend/security/audit_alerts.yaml", "items": items(
            business_hours=("上班時間", ALERTS.get("business_hours"), ""), notify=("通知角色", ALERTS.get("notify"), "通知管道第二階段"),
            rules=("規則", ALERTS.get("rules"), "日誌統計看板即時評估"),
        )},
    ]
    return {"sections": sections, "edit": {"enabled": False, "stage": "phase2", "note": "線上修改（含雙人複核與變更歷史）為第二階段；目前以設定檔與 PR 管理，密鑰只放 secret_ref。"}}


# ---------------- 路由 ----------------
def build_router(store: Store, get_subject: Any, local_accounts: LocalAccounts | None = None, session_store: Any | None = None, auth_cfg: dict[str, Any] | None = None, approvals: Any | None = None) -> APIRouter:
    r = APIRouter()

    def local_or_501() -> LocalAccounts:
        if local_accounts is None:
            raise HTTPException(501, "此環境未啟用本機帳號（DMP_LOCAL_ACCOUNTS）")
        return local_accounts

    def username_for(id_: str) -> str:
        """路徑參數可以是 user_id（local:<subject>）或帳號名。"""
        la = local_or_501()
        if id_.startswith("local:"):
            u = la.username_of(id_[6:])
            if not u:
                raise HTTPException(404, "本機帳號不存在")
            return u
        if ":" in id_:
            raise HTTPException(400, "只有本機帳號可在此管理；SSO 帳號請在 IdP 停用")
        return id_

    @r.get("/v1/iam/users")
    def users(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        _need(subject, "view", "iam")
        return member_directory(store, local_accounts, session_store)

    @r.post("/v1/iam/users", status_code=201)
    def create_user(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "admin", "iam")
        la = local_or_501()
        unknown = [x for x in (body.get("roles") or []) if x not in RBAC["roles"]]
        if unknown:
            raise HTTPException(400, f"未知角色 {unknown}")
        try:
            pub = la.add(str(body.get("username") or ""), str(body.get("password") or ""), list(body.get("roles") or []), str(body.get("name") or ""), str(body.get("email") or ""), totp=True)
        except LocalError as e:
            raise HTTPException(400, str(e)) from None
        store.audit("iam.user.created", {"user": subject.user_id, "target": f"local:{pub['subject']}", "username": pub["username"], "roles": pub["roles"]})
        secret = la._get(pub["username"]).get("totp_secret") or ""
        return {**pub, "id": f"local:{pub['subject']}", "otpauth": totp_uri(pub["username"], secret)}

    @r.get("/v1/iam/users/{id_}")
    def user_detail(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "view", "iam")
        e = next((x for x in member_directory(store, local_accounts, session_store) if x["id"] == id_ or x["username"] == id_), None)
        if not e:
            raise HTTPException(404, "成員不存在")
        lister = getattr(session_store, "list_all", None) if session_store is not None else None
        sess = [{"sid": s.sid[:8], "provider": s.provider, "roles": s.roles, "created_at": s.created_at, "last_seen": s.last_seen, "ip_masked": s.attrs.get("ip", "")[:7] + "…"} for s in (lister() if lister else []) if s.user_id == e["id"]]
        recent = [x for x in store.audit_tail(TAIL) if x.get("user") == e["id"] or x.get("target") == e["id"]][-50:]
        return {**e, "assignment_rows": [a for a in store.assignments() if a.get("user_id") == e["id"]], "session_rows": sess, "recent_audit": list(reversed(recent))}

    @r.patch("/v1/iam/users/{id_}")
    def patch_user(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "admin", "iam")
        la = local_or_501(); u = username_for(id_)
        unknown = [x for x in (body.get("roles") or []) if x not in RBAC["roles"]]
        if unknown:
            raise HTTPException(400, f"未知角色 {unknown}")
        try:
            if "disabled" in body:
                la.set_disabled(u, bool(body["disabled"]))
            pub = la.update(u, body.get("name"), body.get("email"), list(body["roles"]) if "roles" in body else None)
        except LocalError as e:
            raise HTTPException(400 if "不存在" not in str(e) else 404, str(e)) from None
        if body.get("disabled") and session_store is not None:
            for s in session_store.list_user(f"local:{pub['subject']}"):
                session_store.delete(s.sid)
        store.audit("iam.user.changed", {"user": subject.user_id, "target": f"local:{pub['subject']}", "username": u, "fields": sorted(k for k in body if k in ("disabled", "name", "email", "roles"))})
        return {**pub, "id": f"local:{pub['subject']}"}

    @r.delete("/v1/iam/users/{id_}", status_code=204)
    def delete_user(id_: str, subject: Subject = Depends(get_subject)) -> Response:
        _need(subject, "admin", "iam")
        la = local_or_501(); u = username_for(id_)
        try:
            pub = la.public(u); la.remove(u)
        except LocalError as e:
            raise HTTPException(404, str(e)) from None
        uid = f"local:{pub['subject']}"
        if session_store is not None:
            for s in session_store.list_user(uid):
                session_store.delete(s.sid)
        for a in store.assignments():
            if a.get("user_id") in (uid, u):
                store.delete_assignment(a["id"])
        store.audit("iam.user.deleted", {"user": subject.user_id, "target": uid, "username": u})
        return Response(status_code=204)

    @r.post("/v1/iam/users/{id_}/reset-password")
    def reset_password(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "admin", "iam")
        la = local_or_501(); u = username_for(id_)
        try:
            la.reset_password(u, str(body.get("password") or ""))
        except LocalError as e:
            raise HTTPException(400 if "不存在" not in str(e) else 404, str(e)) from None
        pub = la.public(u)
        store.audit("iam.user.password_reset", {"user": subject.user_id, "target": f"local:{pub['subject']}", "username": u})
        return {**pub, "id": f"local:{pub['subject']}"}

    @r.post("/v1/iam/users/{id_}/reset-totp")
    def reset_totp(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "admin", "iam")
        la = local_or_501(); u = username_for(id_)
        try:
            secret = la.reset_totp(u)
        except LocalError as e:
            raise HTTPException(404, str(e)) from None
        pub = la.public(u)
        store.audit("iam.user.totp_reset", {"user": subject.user_id, "target": f"local:{pub['subject']}", "username": u})
        return {**pub, "id": f"local:{pub['subject']}", "otpauth": totp_uri(u, secret)}

    @r.get("/v1/audit/stats")
    def stats(days: int = 30, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "view", "audit")
        return audit_stats(store, subject, days, approvals)

    @r.get("/v1/audit/export")
    def export(days: int = 30, subject: Subject = Depends(get_subject)) -> Response:
        """審計報表不經匯出審批單（稽核用途、不含客戶資料），但需 audit.export 授權並帶浮水印、寫審計。"""
        if not has_grant(subject, "export", "audit"):
            raise HTTPException(403, "需要日誌統計的匯出權限（audit.export）")
        since = time.time() - max(1, min(int(days), 365)) * 86400
        rows = [x for x in store.audit_tail(TAIL) if float(x.get("at", 0)) >= since]
        ts = datetime.now(_tz()).isoformat(timespec="seconds")
        wm = hashlib.sha256(f"{subject.user_id}|{ts}|{len(rows)}".encode()).hexdigest()[:16]
        buf = io.StringIO()
        buf.write(f"# AltaTech DMP 審計報表；匯出者 {subject.user_id}；時間 {ts}；{len(rows)} 列；浮水印 {wm}\n")
        w = csv.writer(buf); w.writerow(["at", "event", "user", "module", "data", "_watermark"])
        for x in rows:
            rest = {k: v for k, v in x.items() if k not in ("at", "event", "user", "module")}
            w.writerow([datetime.fromtimestamp(float(x.get("at", 0)), _tz()).isoformat(timespec="seconds"), x.get("event"), x.get("user", ""), x.get("module", ""), yaml.safe_dump(rest, allow_unicode=True, default_flow_style=True, width=10_000).strip(), wm])
        store.audit("audit.exported", {"user": subject.user_id, "rows": len(rows), "days": days, "watermark": wm})
        return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="audit-{ts[:10]}.csv"'})

    @r.get("/v1/settings")
    def settings(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        _need(subject, "view", "iam")
        return settings_view(auth_cfg or {}, local_accounts)

    return r
