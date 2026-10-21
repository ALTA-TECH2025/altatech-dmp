"""SKILL.md 載入器（docs/39 §7.1、§7.4、§8）。

為什麼：skill 是給模型的工作說明，但安全不能靠它。載入時就把不合格的擋掉，執行時控制器只信這裡回的結構：
- frontmatter 必填 name／description／version／requires_contracts／tools；name 要和目錄名相同。
- tools 只能是 tools.yaml 裡 mvp: true 的工具，且不能是 model_access: denied。控制器依這份清單強制白名單。
- requires_contracts 與現行契約版本比對，不相容 → SkillError(CONTRACT_MISMATCH)，run 不執行任何工具（A17）。
- 本文必須有固定段落；「口徑」只引用契約 ID：本文出現語意層事件名就拒絕載入。
- 給模型的只有本文（不含 frontmatter）。

用法：python3 -m backend.insight_agent.skill_loader     # 載入 skills/*/SKILL.md 並自測
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore

ROOT = Path(__file__).resolve().parent
SKILLS = ROOT / "skills"
BACKEND = ROOT.parent
REQUIRED = ("name", "description", "version", "requires_contracts", "tools")
SECTIONS = ("目標", "觸發", "前置條件", "輸入", "輸出", "步驟", "口徑", "歧義處理", "錯誤與停止條件", "測試")


class SkillError(Exception):
    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message); self.code, self.message, self.detail = code, message, detail


@dataclass
class Skill:
    name: str
    description: str
    version: str
    requires_contracts: dict[str, str]
    tools: list[str]
    body: str                                  # 只有這段給模型
    meta: dict[str, Any] = field(default_factory=dict)


def contract_versions() -> dict[str, str]:
    """現行契約版本；run 紀錄的 contracts 欄位也存這份。"""
    y = lambda p: yaml.safe_load((BACKEND / p).read_text(encoding="utf-8"))
    return {"semantic": str(y("semantic/events.yaml")["version"]), "tools": str(y("mcp/tools.yaml")["contract_version"]), "oneid_rules": str(y("semantic/oneid_rules.yaml")["version"])}


def _ver(v: str) -> tuple[int, ...] | None:
    """'v1'、'4'、'1.10.2' → 各段整數；不是這種格式就 None（不相容）。不轉浮點：1.2 與 1.10 要能分開。"""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(v).strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _ok(req: str, cur: str) -> bool:
    req = str(req).strip()
    op = next((o for o in (">=", "<=", "==", ">", "<") if req.startswith(o)), "==")
    a, b = _ver(cur), _ver(req[len(op):] if req.startswith(op) else req)
    if a is None or b is None:
        return False
    n = max(len(a), len(b)); a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return {">=": a >= b, "<=": a <= b, "==": a == b, ">": a > b, "<": a < b}[op]


def _split(text: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise SkillError("INVALID_ARGUMENT", "SKILL.md 缺少 frontmatter")
    return yaml.safe_load(m.group(1)) or {}, m.group(2).strip()


def _event_names() -> set[str]:
    ev = yaml.safe_load((BACKEND / "semantic" / "events.yaml").read_text(encoding="utf-8"))["events"]
    names = {str(e["name"]) for e in ev} | {str(x) for e in ev for x in (e.get("aliases") or [])}   # 別名也是語意層事件識別字
    return {n for n in names if len(n) >= 4}


def _mvp_tools() -> dict[str, dict[str, Any]]:
    spec = yaml.safe_load((BACKEND / "mcp" / "tools.yaml").read_text(encoding="utf-8"))
    return {t["name"]: t for t in spec["tools"]}


def parse(text: str, dirname: str | None = None, versions: dict[str, str] | None = None) -> Skill:
    meta, body = _split(text)
    miss = [k for k in REQUIRED if not meta.get(k)]
    if miss:
        raise SkillError("INVALID_ARGUMENT", f"frontmatter 缺 {miss}")
    if dirname is not None and meta["name"] != dirname:
        raise SkillError("INVALID_ARGUMENT", f"name {meta['name']} 與目錄 {dirname} 不同")
    tools = _mvp_tools(); bad = [t for t in meta["tools"] if t not in tools or not tools[t].get("mvp") or tools[t].get("model_access") == "denied"]
    if bad:
        raise SkillError("INVALID_ARGUMENT", f"tools 含非 MVP 或不開放給模型的工具 {bad}")
    heads = set(re.findall(r"^## (.+?)\s*$", body, re.M)); lack = [s for s in SECTIONS if s not in heads]
    if lack:
        raise SkillError("INVALID_ARGUMENT", f"本文缺段落 {lack}")
    leaked = sorted(n for n in _event_names() if re.search(r"(?<![A-Za-z0-9_$])" + re.escape(n) + r"(?![A-Za-z0-9_])", body))
    if leaked:
        raise SkillError("INVALID_ARGUMENT", f"本文寫了事件名 {leaked}：口徑只引用契約 ID")
    cur = versions or contract_versions()
    bad_c = {k: v for k, v in dict(meta["requires_contracts"]).items() if k not in cur or not _ok(v, cur[k])}
    if bad_c:
        raise SkillError("CONTRACT_MISMATCH", f"skill {meta['name']} 需要的契約版本不相容：{bad_c}（現行 {cur}）", required=bad_c, current=cur)
    return Skill(str(meta["name"]), str(meta["description"]), str(meta["version"]), dict(meta["requires_contracts"]), list(meta["tools"]), body, meta)


def load(name: str, versions: dict[str, str] | None = None) -> Skill:
    p = SKILLS / name / "SKILL.md"
    if not re.fullmatch(r"[a-z0-9-]+", name) or not p.is_file():
        raise SkillError("NOT_FOUND", f"skill {name} 不存在")
    return parse(p.read_text(encoding="utf-8"), name, versions)


def all_skills() -> list[str]:
    return sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))


def selftest() -> None:
    names = all_skills(); loaded = [load(n) for n in names]
    assert {"data-readiness", "explore-data"} <= set(names) and all(not s.body.startswith("---") for s in loaded)
    good = (SKILLS / "data-readiness" / "SKILL.md").read_text(encoding="utf-8")
    def code(text: str, **kw: Any) -> str:
        try:
            parse(text, **kw); return "ok"
        except SkillError as e:
            return e.code
    assert code(good.replace('semantic: ">=4"', 'semantic: ">=5"')) == "CONTRACT_MISMATCH"                     # A17
    assert code(good.replace('tools: "v1"', 'tools: "v2"')) == "CONTRACT_MISMATCH"
    assert _ok(">=1.10", "1.10.0") and not _ok(">=1.10", "1.2") and _ok("==v1", "v1") and _ok("v1", "v1") and not _ok(">=4", "x4")   # 版本逐段比，不轉浮點
    assert code(good.replace("## 口徑\n", "## 口徑\n用 fund_subscribe 計算。\n")) == "INVALID_ARGUMENT"                          # 事件別名也算事件名
    assert code(good.replace("tools: [semantic.describe, data.readiness]", "tools: [semantic.describe, profile.get]")) == "INVALID_ARGUMENT"   # 不給模型的工具
    assert code(good.replace("tools: [semantic.describe, data.readiness]", "tools: [metric.query]")) == "INVALID_ARGUMENT"                    # 非 MVP
    assert code(good.replace("## 歧義處理", "## 其他")) == "INVALID_ARGUMENT"
    ev = sorted(_event_names())[0]; assert code(good.replace("## 口徑\n", f"## 口徑\n用 {ev} 計算。\n")) == "INVALID_ARGUMENT"     # 口徑寫事件名
    assert code(good, dirname="other") == "INVALID_ARGUMENT" and code("沒有 frontmatter") == "INVALID_ARGUMENT"
    try:
        load("../etc"); raise AssertionError("應拒絕")
    except SkillError as e:
        assert e.code == "NOT_FOUND"
    print(f"skill loader self-test ok: {names} 載入；契約版本 {contract_versions()}；過期契約 → CONTRACT_MISMATCH、非 MVP／不給模型的工具、缺段落、口徑寫事件名、路徑跳脫皆拒絕")


if __name__ == "__main__":
    selftest()
