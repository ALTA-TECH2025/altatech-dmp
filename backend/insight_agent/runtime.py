"""Insight Agent Runtime — Planner / Executor / Verifier / Narrator 最小骨架。

正式版：LLM 透過企業 LLM Gateway 呼叫；工具透過 MCP client 連 Agent Tool Gateway（backend/mcp）。
此骨架示範執行迴圈、預算與洞察物件組裝，不含實際 LLM 呼叫。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore

ROOT = Path(__file__).resolve().parent
REGISTRY = yaml.safe_load((ROOT / "agents.yaml").read_text(encoding="utf-8"))


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: dict[str, Any] | None = None


@dataclass
class Run:
    agent: str
    trigger: dict[str, Any]
    on_behalf_of: str
    id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    plan: list[dict[str, Any]] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    cost: dict[str, float] = field(default_factory=lambda: {"scan_bytes": 0, "llm_tokens": 0})
    insights: list[dict[str, Any]] = field(default_factory=list)


class Gateway:
    """骨架：直接呼叫本地 Tool Gateway（backend/mcp/server.py）。示範身分固定為數據分析角色；正式版由 run 的 initiator 解析（PR5 控制器）。"""
    def __init__(self, user_id: str, run_id: str):
        from backend.api.store import MemoryStore
        from backend.mcp import server
        from backend.security.policy import Subject
        self._gw = server.Gateway(MemoryStore(), ch=None)
        self._ctx = server.ToolContext(Subject(user_id, ["data_analyst"]), run_id=run_id)

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        env = self._gw.call(self._ctx, name, args)
        return {**(env["data"] or {}), "cost": {"scan_bytes": env["usage"].get("scan_bytes_est", 0)}, "warnings": [w["code"] for w in env["warnings"]] + ([env["error"]["code"]] if env["error"] else [])}


class InsightAgent:
    def __init__(self, agent_id: str):
        self.spec = next(a for a in REGISTRY["agents"] if a["id"] == agent_id)
        self.skill = (ROOT / self.spec["skill"]).read_text(encoding="utf-8")

    # ---- Planner：正式版由 LLM 依 skill + semantic.search 產生 ----
    def plan(self, run: Run, question: str | None) -> list[dict[str, Any]]:
        allowed = set(self.spec["tools"])
        steps = [{"tool": "semantic.search", "args": {"query": question or self.spec["cn"]}}]
        return [s for s in steps if s["tool"] in allowed]

    # ---- Executor ----
    def execute(self, run: Run, gw: Gateway) -> None:
        for step in run.plan:
            call = ToolCall(step["tool"], step["args"])
            call.result = gw.call(call.name, call.args)
            run.cost["scan_bytes"] += float(call.result.get("cost", {}).get("scan_bytes", 0))
            run.calls.append(call)

    # ---- Verifier：正式版重跑交叉查詢；骨架檢查 warnings 與數字來源 ----
    def verify(self, run: Run) -> list[str]:
        notes: list[str] = []
        for c in run.calls:
            for w in (c.result or {}).get("warnings", []):
                notes.append(f"{c.name}: {w}")
        return notes

    # ---- Narrator：正式版由 LLM 依輸出格式寫；骨架組裝洞察物件 ----
    def narrate(self, run: Run, notes: list[str]) -> dict[str, Any]:
        return {
            "id": f"ins_{uuid.uuid4().hex[:12]}",
            "kind": "answer" if run.trigger.get("type") == "question" else "anomaly",
            "title": f"[{self.spec['cn']}] 執行結果",
            "summary": "（骨架）此處由 LLM 依 skill 的輸出格式撰寫：結論 → 證據 → 注意事項 → 建議。",
            "evidence": [{"tool": c.name, "args": c.args, "digest": {k: v for k, v in (c.result or {}).items() if k in ("rows", "items")}} for c in run.calls],
            "confidence": 0.5 if notes else 0.7,
            "severity": "low",
            "actions": [],
            "created_by": {"agent": self.spec["id"], "run_id": run.id, "on_behalf_of": run.on_behalf_of},
            "status": "open",
            "notes": notes,
        }

    def run(self, trigger: dict[str, Any], on_behalf_of: str) -> Run:
        run = Run(agent=self.spec["id"], trigger=trigger, on_behalf_of=on_behalf_of)
        gw = Gateway(on_behalf_of, run.id)
        run.plan = self.plan(run, trigger.get("question"))
        self.execute(run, gw)
        notes = self.verify(run)
        run.insights.append(self.narrate(run, notes))
        return run


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT.parent.parent))
    r = InsightAgent("ask").run({"type": "question", "question": "開戶 轉化"}, on_behalf_of="demo")
    print(json.dumps({"run": r.id, "calls": [c.name for c in r.calls], "insight": r.insights[0]}, ensure_ascii=False, indent=1))
