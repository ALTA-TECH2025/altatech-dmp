"""Policy Engine 骨架：authorize() 回傳 允許/拒絕 + 行級過濾 + 欄位遮罩。UI、REST、MCP 工具閘道共用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore

ROOT = Path(__file__).resolve().parent
RBAC = yaml.safe_load((ROOT / "rbac.yaml").read_text(encoding="utf-8"))
MASK = yaml.safe_load((ROOT / "masking.yaml").read_text(encoding="utf-8"))
LEVEL_ORDER = ["L0", "L1", "L2", "L3", "L4"]


@dataclass
class Subject:
    user_id: str
    roles: list[str]
    branches: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    approvals: set[str] = field(default_factory=set)       # 已核准的 approval_id
    reveals: dict[str, str] = field(default_factory=dict)  # field → approval_id（揭露中）


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    row_filters: list[str] = field(default_factory=list)
    field_policies: dict[str, str] = field(default_factory=dict)   # field → plain | mask_tail(4) | bucket | hide…
    k_anonymity: int = RBAC["k_anonymity"]["min_group_size"]
    audit: dict[str, Any] = field(default_factory=dict)


def _grants(role: str, module: str) -> set[str]:
    r = RBAC["roles"][role]; g = r.get("grants", {})
    acts = set(g.get(module, [])) | set(g.get("*", []))
    acts -= set(r.get("deny", {}).get(module, [])) if module in r.get("deny", {}) and r["deny"][module] == [] else set()
    if module in r.get("deny", {}) and r["deny"][module]:
        acts -= set(r["deny"][module])
    return acts


def authorize(subject: Subject, action: str, module: str, context: dict[str, Any] | None = None) -> Decision:
    context = context or {}
    # 1. 模組權限：多角色取聯集
    allowed_acts = set().union(*(_grants(r, module) for r in subject.roles))
    if action not in allowed_acts:
        return Decision(False, f"{module}.{action} not granted to roles {subject.roles}", audit={"event": "authz.denied"})
    d = Decision(True)
    # 2. 數據權限：scope 取聯集（任一角色 global 即全域）
    scopes = {RBAC["roles"][r]["scope"] for r in subject.roles}
    if "global" not in scopes:
        conds = []
        if "branch" in scopes and subject.branches:
            conds.append("branch_id IN (" + ",".join(f"'{b}'" for b in subject.branches) + ")")
        if "agent" in scopes and subject.agents:
            conds.append("agent_id IN (" + ",".join(f"'{a}'" for a in subject.agents) + ")")
        if "aggregate_only" in scopes and module in ("profile", "users"):
            return Decision(False, "aggregate-only identity cannot access individual data")
        d.row_filters.append("(" + " OR ".join(conds) + ")" if conds else "1=0")
    for r in subject.roles:
        lb = RBAC["roles"][r].get("lookback_days")
        if lb:
            d.row_filters.append(f"event_date >= today()-{lb}")
    # 3. 欄位權限：取最嚴格；揭露中的欄位放行
    for f in MASK["fields"]:
        plain = f.get("plain_roles", [])
        is_plain = "*" in plain or any(r in plain for r in subject.roles)
        strictest = "plain" if is_plain else f.get("others", "hide")
        if f["name"] in subject.reveals:
            strictest = "plain"
        if context.get("export") and f.get("export"):
            strictest = f["export"]
        d.field_policies[f["name"]] = strictest
    # 4. 受控動作需審批
    if action in ("export", "approve") or module == "profile":
        if module == "profile" and not context.get("reason"):
            return Decision(False, "profile access requires a reason", audit={"event": "authz.denied", "why": "no_reason"})
        if action == "export" and context.get("approval_id") not in subject.approvals:
            return Decision(False, "export requires an approved request", audit={"event": "authz.denied", "why": "no_approval"})
    d.audit = {"event": "query.executed" if action in ("view", "use") else f"{module}.{action}", "row_policy": d.row_filters, "masked_fields": [k for k, v in d.field_policies.items() if v != "plain"]}
    return d


if __name__ == "__main__":
    agent = Subject("u-agt", ["sales_agent"], branches=["TPE01"], agents=["A001"])
    print(authorize(agent, "use", "users"))
    print(authorize(agent, "use", "profile", {"reason": "客戶來電"}).field_policies["amount"])
    print(authorize(agent, "use", "events").allowed, "<- 營業員不可用事件分析")
    analyst = Subject("u-ana", ["analyst"])
    print(authorize(analyst, "export", "events", {}).reason)
    print(authorize(Subject("u-adm", ["platform_admin"]), "use", "users").field_policies["one_id"])
