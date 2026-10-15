"""Agent Tool Gateway — MCP server 骨架。

把 tools.yaml 登記的工具暴露給任何 MCP client（Insight Agent Runtime、Claude Code…）。
每次呼叫：套身份與權限 → 檢查預算 → 執行 handler → 列級遮罩 → 寫審計事件。
正式版以 FastMCP 或官方 mcp SDK 實作；此檔為可讀的最小骨架。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml  # type: ignore

ROOT = Path(__file__).resolve().parent
SPEC = yaml.safe_load((ROOT / "tools.yaml").read_text(encoding="utf-8"))


@dataclass
class Caller:
    user_id: str                       # on_behalf_of
    roles: list[str] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    run_id: str | None = None


@dataclass
class Budget:
    scan_bytes_used: float = 0.0
    llm_tokens_used: int = 0

    def charge(self, scan_bytes: float) -> None:
        limit = float(SPEC["gateway"]["budgets"]["per_run_scan_bytes"])
        if self.scan_bytes_used + scan_bytes > limit:
            raise PermissionError(f"run scan budget exceeded: {self.scan_bytes_used + scan_bytes:.0f} > {limit:.0f}")
        self.scan_bytes_used += scan_bytes


HANDLERS: dict[str, Callable[[Caller, dict[str, Any]], dict[str, Any]]] = {}


def tool(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def _mask(payload: dict[str, Any], strict: bool) -> dict[str, Any]:
    """列級遮罩：strict 時移除 L3 以上欄位；永遠不會有 L4 欄位進到這裡。"""
    if not strict:
        return payload
    banned = {"account_no", "mobile_hash", "idno_hash", "$identity_mobile_hash"}
    def scrub(o):
        if isinstance(o, dict):
            return {k: scrub(v) for k, v in o.items() if k not in banned}
        if isinstance(o, list):
            return [scrub(x) for x in o]
        return o
    return scrub(payload)


def _audit(caller: Caller, name: str, args: dict[str, Any], result: dict[str, Any], ms: float) -> None:
    event = {
        "type": "track", "event": SPEC["gateway"]["policy"]["audit_event"],
        "time": int(time.time() * 1000), "project": "altatech_dmp",
        "distinct_id": caller.user_id,
        "properties": {
            "tool": name, "run_id": caller.run_id,
            "args_hash": hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16],
            "scan_bytes": result.get("cost", {}).get("scan_bytes", 0), "ms": ms,
            "warnings": result.get("warnings", []),
        },
    }
    # 正式版：寫 Kafka dmp.events；骨架：stdout
    print(json.dumps(event, ensure_ascii=False))


def call(caller: Caller, budget: Budget, name: str, args: dict[str, Any]) -> dict[str, Any]:
    spec = next((t for t in SPEC["tools"] if t["name"] == name), None)
    if spec is None:
        raise KeyError(f"unknown tool {name}")
    if spec.get("scope") == "sensitive" and not args.get("reason"):
        raise PermissionError(f"{name} requires a reason for audit")
    if spec.get("approval") == "required":
        args = {**args, "status": "pending_approval"}
    handler = HANDLERS.get(name)
    if handler is None:
        raise NotImplementedError(f"handler for {name} not registered")
    t0 = time.perf_counter()
    result = handler(caller, args)
    budget.charge(float(result.get("cost", {}).get("scan_bytes", 0)))
    result = _mask(result, strict=spec.get("masking") == "strict")
    _audit(caller, name, args, result, (time.perf_counter() - t0) * 1000)
    return result


# ---- 範例 handler：正式版改為呼叫 Analytics Query API / Semantic Compiler ----
@tool("semantic.search")
def semantic_search(caller: Caller, args: dict[str, Any]) -> dict[str, Any]:
    q = args["query"].lower()
    sem = ROOT.parent / "semantic"
    items = []
    for kind, fn, key in (("event", "events.yaml", "events"), ("metric", "metrics.yaml", "metrics"), ("dimension", "dimensions.yaml", "dimensions")):
        for it in yaml.safe_load((sem / fn).read_text(encoding="utf-8"))[key]:
            hay = " ".join(str(it.get(k, "")) for k in ("name", "cn", "description")).lower()
            if any(tok in hay for tok in q.split()):
                items.append({"kind": kind, "name": it["name"], "cn": it.get("cn"), "description": it.get("description", ""), "caveats": it.get("caveats", [])})
    return {"items": items[: args.get("limit", 10)], "cost": {"scan_bytes": 0}, "warnings": []}


@tool("metric.query")
def metric_query(caller: Caller, args: dict[str, Any]) -> dict[str, Any]:
    """骨架：回傳編譯後 SQL 與空結果。正式版呼叫 Semantic Compiler + ClickHouse。"""
    from_, to = args["time"]["from"], args["time"]["to"]
    gb = ", ".join(args.get("group_by", [])) or "event_date"
    sql = f"SELECT {gb}, uniqExact(one_id) AS value FROM dmp.events WHERE event_date BETWEEN '{from_}' AND '{to}' GROUP BY {gb} ORDER BY {gb}"
    return {"rows": [], "chart_spec": {"type": "line", "x": "event_date", "y": "value"}, "sql": sql,
            "lineage": ["dmp.events"], "cost": {"scan_bytes": 0, "ms": 0}, "warnings": ["dry-run skeleton: no data"]}


if __name__ == "__main__":
    c = Caller(user_id="demo", roles=["data_analyst"], run_id="run_demo")
    b = Budget()
    print(json.dumps(call(c, b, "semantic.search", {"query": "開戶 轉化"}), ensure_ascii=False, indent=1))
