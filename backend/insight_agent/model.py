"""AI 工作台的模型介面（docs/41 D2：DMP 控制流程、模型以可替換的 API 呼叫）。

為什麼要有這層：模型供應商、端點、部署區域都還沒核定。控制器只認 ModelDriver 介面，換供應商只換 driver，
不改控制器、Gateway 或 skill。這裡**不內建任何供應商**：
- TestDoubleDriver：腳本化的測試替身（provider=test-double），自測與驗收用，只吃合成資料。
- ApiDriver：正式路徑。啟用條件缺任一項就回 MODEL_ENDPOINT_NOT_CONFIGURED，detail.missing 列出缺的設定：
  端點設定（DMP_AGENT_MODEL_PROVIDER／_ENDPOINT／_ID／_REGION／_KEY_REF，金鑰只能是 secret_ref）、
  模型投影參數（tools.yaml model_view.approved_params，D3 未核定前為 null）、該供應商的 transport（核定後才實作）。

模型只看得到 ModelRequest：系統提示（skill 本文）、對話（工具結果只放 model_view）與工具清單；看不到身分、data 或 SQL。
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml  # type: ignore

MV = yaml.safe_load((Path(__file__).resolve().parent.parent / "mcp" / "tools.yaml").read_text(encoding="utf-8"))["model_view"]

SECRET_REF_RE = re.compile(r"^(kv/data/dmp/[a-z]+/[a-z0-9_]+#[a-z_]+|projects/[^/]+/secrets/[^/]+/versions/(\d+|latest)|secret://[a-z0-9-]+/[a-z0-9-]+/[a-zA-Z0-9_.-]+)$")
ENV = {"provider": "DMP_AGENT_MODEL_PROVIDER", "endpoint": "DMP_AGENT_MODEL_ENDPOINT", "model": "DMP_AGENT_MODEL_ID", "region": "DMP_AGENT_MODEL_REGION", "key_ref": "DMP_AGENT_MODEL_KEY_REF"}


class ModelNotConfigured(Exception):
    """對應錯誤碼 MODEL_ENDPOINT_NOT_CONFIGURED（retryable=false）；控制器把 run 標 failed 並顯示 missing。"""
    code = "MODEL_ENDPOINT_NOT_CONFIGURED"

    def __init__(self, missing: list[str]):
        super().__init__("AI 工作台的模型尚未設定，請洽系統管理員（D2 端點、D3 投影參數待核定）"); self.missing = missing


@dataclass
class ModelRequest:
    system: str                                   # skill 本文（不含 frontmatter）
    messages: list[dict[str, Any]]                # {"role": "user"|"assistant"|"tool", "content": ...}；tool 內容只會是 model_view
    tools: list[dict[str, Any]]                   # {"name", "description", "input"}：只列 skill 宣告且 MVP 可用的工具
    max_tokens: int = 2000


@dataclass
class ModelTurn:
    kind: str                                     # tool_call | ask | final
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    text: str = ""                                # final 的結論（只能引用 model_view 裡有的數字；畫面數字不取自這裡）
    question: dict[str, Any] | None = None        # ask：{"text", "choices": [2–3 個], "default"}
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


class ModelDriver(Protocol):
    provider: str
    model: str
    def next(self, req: ModelRequest) -> ModelTurn: ...


class TestDoubleDriver:
    """依腳本回應；script 是 ModelTurn 序列，或 (req, 第幾輪) → ModelTurn 的函式。記下每次收到的請求，測試可檢查模型看到什麼。"""
    __test__ = False
    provider, model = "test-double", "scripted"

    def __init__(self, script: list[ModelTurn] | Callable[[ModelRequest, int], ModelTurn]):
        self.script, self.requests = script, []

    def next(self, req: ModelRequest) -> ModelTurn:
        self.requests.append(req); i = len(self.requests) - 1
        if callable(self.script):
            return self.script(req, i)
        if i >= len(self.script):
            return ModelTurn("final", text="（測試替身腳本已結束）")
        return self.script[i]


def missing_config(env: Mapping[str, str] | None = None, transports: Mapping[str, Any] | None = None) -> list[str]:
    """正式模型的啟用條件；回傳缺的設定名（空 = 可用）。不讀金鑰本身。"""
    env = os.environ if env is None else env
    miss = [ENV[k] for k in ENV if not str(env.get(ENV[k]) or "").strip()]
    ref = str(env.get(ENV["key_ref"]) or "")
    if ref and not SECRET_REF_RE.match(ref):
        miss.append(ENV["key_ref"] + "（只接受 secret_ref）")
    if MV.get("approved_params") is None:
        miss.append("model_view.approved_params")
    prov = str(env.get(ENV["provider"]) or "")
    if prov and prov not in (transports or {}):
        miss.append(f"transport:{prov}")
    return miss


class ApiDriver:
    """正式模型。transport(cfg, req) → ModelTurn 由核定的供應商實作後註冊；此前一律 MODEL_ENDPOINT_NOT_CONFIGURED。"""
    def __init__(self, env: Mapping[str, str] | None = None, transports: Mapping[str, Callable[[dict[str, str], ModelRequest], ModelTurn]] | None = None):
        self.env = dict(os.environ if env is None else env); self.transports = dict(transports or {})
        self.provider = self.env.get(ENV["provider"], ""); self.model = self.env.get(ENV["model"], "")

    def next(self, req: ModelRequest) -> ModelTurn:
        miss = missing_config(self.env, self.transports)
        if miss:
            raise ModelNotConfigured(miss)
        cfg = {k: self.env[v] for k, v in ENV.items()}
        return self.transports[self.provider](cfg, req)


def selftest() -> None:
    d = TestDoubleDriver([ModelTurn("tool_call", tool="semantic.search", args={"query": "開戶"}), ModelTurn("final", text="完成")])
    r = ModelRequest(system="s", messages=[{"role": "user", "content": "q"}], tools=[])
    assert d.next(r).tool == "semantic.search" and d.next(r).kind == "final" and d.next(r).kind == "final" and len(d.requests) == 3
    try:
        ApiDriver(env={}).next(r); raise AssertionError("應拒絕")
    except ModelNotConfigured as e:
        assert e.code == "MODEL_ENDPOINT_NOT_CONFIGURED" and set(ENV.values()) <= set(e.missing) and "model_view.approved_params" in e.missing
    full = {ENV["provider"]: "vendor_x", ENV["endpoint"]: "https://llm.example.internal", ENV["model"]: "m", ENV["region"]: "tw", ENV["key_ref"]: "secret://dmp/agent/model_key"}
    m = missing_config(full, {})
    assert "transport:vendor_x" in m and "model_view.approved_params" in m and not any(x in m for x in ENV.values())   # 端點齊了仍缺投影參數與 transport
    assert ENV["key_ref"] + "（只接受 secret_ref）" in missing_config({**full, ENV["key_ref"]: "sk-plaintext"}, {"vendor_x": None})
    print("model driver self-test ok: 測試替身腳本、未設定端點／投影參數／transport → MODEL_ENDPOINT_NOT_CONFIGURED（列出缺項）、金鑰只收 secret_ref")


if __name__ == "__main__":
    selftest()
