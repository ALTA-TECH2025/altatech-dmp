#!/usr/bin/env python3
"""依異動檔案挑出要跑的測試：改 backend/api 只跑會載入它的自測，不必連 web 一起跑；只有動到 web/ 或請求／回應 schema（backend/openapi.yaml）才跑前端。

挑法：
- Python 自測：從每個自測的入口檔沿 import（含函式內的延遲 import）靜態算出閉包；異動的 .py 落在誰的閉包裡就跑誰。
- 資料檔（yaml／sql／json／md）：哪個模組的字串常數提到這個檔名，就當成那個模組異動；沒人提到就歸給同目錄（或最近上層）的模組。
- 保守退路：刪掉的 .py、backend/requirements.txt、ruff.toml → 跑全部 backend；本檔或 ci.yml 異動、--all → 全部都跑。
只用標準庫，CI 的 changes job 不必先裝套件。

本機：python3 tools/affected_tests.py          # 列出相對 origin/main（含未提交、未追蹤）的異動與要跑的測試
      python3 tools/affected_tests.py --run    # 實際執行；--all 跑全部；--files a b 指定檔案；--base 指定比較基準
CI：  python3 tools/affected_tests.py --base origin/main --github-output   # 寫出各 job 開關與 backend_suites
      python3 tools/affected_tests.py --run --suites "api segments"       # backend job 只跑挑中的自測
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import functools
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# (名稱, 指令, 入口檔)。與 .github/workflows/ci.yml 的 backend job 同一份清單；新增自測加在這裡就會自動被挑。
PY_SUITES: list[tuple[str, str, str]] = [
    ("policy", "python3 backend/security/policy.py", "backend/security/policy.py"),
    ("transforms", "python3 backend/integration/transforms.py", "backend/integration/transforms.py"),
    ("integration.auth", "python3 backend/integration/auth.py", "backend/integration/auth.py"),
    ("agent.runtime", "python3 backend/insight_agent/runtime.py", "backend/insight_agent/runtime.py"),
    ("oneid", "python3 backend/oneid/attribution.py", "backend/oneid/attribution.py"),
    ("auth", "python3 -m backend.auth", "backend/auth/__main__.py"),
    ("auth.local", "python3 -m backend.auth.local selftest", "backend/auth/local.py"),
    ("api", "python3 -m backend.api", "backend/api/__main__.py"),
    ("segments", "python3 -m backend.segments", "backend/segments/__main__.py"),
    ("api.store_pg", "python3 -m backend.api.store_pg", "backend/api/store_pg.py"),
    ("modeling", "python3 -m backend.integration.modeling", "backend/integration/modeling.py"),
    ("scheduler", "python3 -m backend.integration.scheduler", "backend/integration/scheduler.py"),
    ("legacy_format", "python3 -m backend.integration.legacy_format", "backend/integration/legacy_format.py"),
    ("ga4_source", "python3 -m backend.integration.ga4_source", "backend/integration/ga4_source.py"),
    ("legacy_api", "python3 -m backend.integration.legacy_api", "backend/integration/legacy_api.py"),
    ("golden", "python3 -m backend.semantic.compiler.golden", "backend/semantic/compiler/golden.py"),
    ("e2e_clickhouse", "python3 -m backend.integration.e2e_clickhouse", "backend/integration/e2e_clickhouse.py"),
    ("migration.bridge", "python3 backend/migration/bridge.py --check", "backend/migration/bridge.py"),
    ("migration.properties", "python3 backend/migration/properties.py --check", "backend/migration/properties.py"),
    ("migration.legacy_specific", "python3 backend/migration/legacy_specific.py --check", "backend/migration/legacy_specific.py"),
    ("migration.presets", "python3 backend/migration/presets.py", "backend/migration/presets.py"),
    ("entry", "python3 -m backend.entry --selftest && python3 -m backend.entry --check && DMP_ROLE=worker python3 -m backend.entry --check", "backend/entry.py"),
]
FULL_BACKEND = ["backend/requirements.txt", "ruff.toml"]
FULL_ALL = ["tools/affected_tests.py", ".github/workflows/ci.yml"]
SCHEMA = ["backend/openapi.yaml"]   # 請求／回應契約：web/src/**/types.ts 對齊它，改了才需要跑前端
# CI job → 觸發樣式（fnmatch，* 可跨目錄）。e2e_clickhouse 另外看 Python 閉包（見 plan()）。
GLOB_JOBS: dict[str, list[str]] = {
    "web": ["web/*", *SCHEMA],
    "prototype": ["prototype/*"],
    # 全端瀏覽器主線：前端、schema，或起堆疊會用到的檔案（quickstart、entry、示範資料、Webhook 接收端、DDL）
    "e2e_browser": ["web/*", *SCHEMA, "deploy/quickstart/*", "backend/entry.py", "backend/tools/demo_fill.py", "backend/tools/webhook_receiver.py", "backend/clickhouse/schema.sql", "backend/requirements.txt"],
    "deploy": ["deploy/*", ".github/*"],
    "contract": [*SCHEMA, "backend/semantic/*.yaml", "backend/mcp/tools.yaml", "backend/security/*.yaml"],
}
# 其他 backend job 內的檢查：名稱 → (指令, 觸發樣式)
EXTRA_BACKEND: dict[str, tuple[str, list[str]]] = {
    "ruff": ("ruff check backend tools tests", ["backend/*.py", "tools/*.py", "tests/*.py", "ruff.toml"]),
    "pytest": ("pytest -q tests", ["tests/*", "tools/*"]),
}
# 只在本機跑的指令（CI 由其他 job 負責）
LOCAL_CMDS: dict[str, str] = {
    "yaml": "python3 -c \"import yaml,glob;[yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('backend/**/*.yaml',recursive=True)+glob.glob('deploy/**/*.yaml',recursive=True) if '/templates/' not in f]\"",
    "prototype": "node --check prototype/js/app.js && node --check prototype/js/data.js",
    "web": "cd web && {npm_ci}npm run typecheck && npm test && npm run build",
}
YAML_GLOBS = ["backend/*.yaml", "deploy/*.yaml"]


def _match(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


class Graph:
    """backend 的模組 import 圖與字串常數（找資料檔引用）。"""

    def __init__(self) -> None:
        self.mods: dict[str, str] = {}        # backend.api.app → backend/api/app.py
        for p in BACKEND.rglob("*.py"):
            parts = list(p.relative_to(ROOT).with_suffix("").parts)
            if parts[-1] == "__init__": parts.pop()
            self.mods[".".join(parts)] = p.relative_to(ROOT).as_posix()
        self.files = {v: k for k, v in self.mods.items()}
        self.deps: dict[str, set[str]] = {}
        self.consts: dict[str, set[str]] = {}
        for name, rel in self.mods.items():
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            pkg = name if rel.endswith("__init__.py") else name.rpartition(".")[0]
            out: set[str] = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Import): out |= {a.name for a in n.names}
                elif isinstance(n, ast.ImportFrom):
                    base = n.module or ""
                    if n.level: base = ".".join([*pkg.split(".")[:len(pkg.split(".")) - n.level + 1], *([n.module] if n.module else [])])
                    out.add(base); out |= {f"{base}.{a.name}" for a in n.names}
            self.deps[rel] = {f for m in out for f in self._files_of(m)}
            self.consts[rel] = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 200}

    def _files_of(self, mod: str) -> list[str]:
        """一個 import 名稱實際會執行的檔：它本身＋各層上層套件的 __init__.py。"""
        parts = mod.split(".")
        return [self.mods[m] for m in (".".join(parts[:i]) for i in range(1, len(parts) + 1)) if m in self.mods]

    def closure(self, entry: str) -> set[str]:
        seen, todo = set(), [entry, *self._files_of(self.files[entry])]
        while todo:
            f = todo.pop()
            if f in seen: continue
            seen.add(f); todo += self.deps.get(f, ())
        return seen

    def owners(self, data: str) -> set[str]:
        """資料檔 → 視同異動的模組：字串常數提到檔名的；沒有就同目錄或最近上層目錄的模組。"""
        name = data.rsplit("/", 1)[-1]
        hit = {f for f, cs in self.consts.items() if any(c == name or c.endswith("/" + name) for c in cs)}
        d = data.rsplit("/", 1)[0]
        while not hit and d.startswith("backend/"):   # 不退到 backend/ 本身：那層的 __init__.py 在每個閉包裡，等於全跑（openapi.yaml 由 contract job 驗）
            hit = {f for f in self.deps if f.rsplit("/", 1)[0] == d}
            d = d.rsplit("/", 1)[0]
        return hit


@functools.cache
def graph() -> Graph:
    return Graph()


def changed_files(base: str) -> list[str]:
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    mb = git("merge-base", base, "HEAD")[0]
    files = git("diff", "--name-only", mb) + git("ls-files", "--others", "--exclude-standard")   # 已提交＋未提交＋未追蹤
    return sorted({f for f in files if f})


def plan(files: list[str], full: bool = False) -> dict:
    """回傳 {jobs: {job: bool}, suites: [(名稱, 指令, 原因)], notes: [...]}。"""
    g = graph()
    full = full or any(f in FULL_ALL for f in files)
    full_backend = full or any(f in FULL_BACKEND for f in files)
    notes: list[str] = []
    touched: dict[str, str] = {}   # 視同異動的模組 → 來源檔
    for f in files:
        if not f.startswith("backend/") or f.endswith(".md") and f.rsplit("/", 1)[-1] == "README.md": continue
        if f.endswith(".py"):
            if f in g.deps: touched.setdefault(f, f)
            else: full_backend = True; notes.append(f"{f} 已刪除或不在 backend 模組裡 → 跑全部 backend 自測")
        else:
            own = g.owners(f)
            for m in own: touched.setdefault(m, f)
            if not own: notes.append(f"{f} 找不到引用它的模組，未挑任何自測")
    suites: list[tuple[str, str, str]] = []
    covered: set[str] = set()
    ch_closure: set[str] = set()
    for name, cmd, entry in PY_SUITES:
        cl = g.closure(entry); covered |= cl
        if name == "e2e_clickhouse": ch_closure = cl
        why = "全部" if full_backend else next((touched[m] for m in sorted(cl) if m in touched), None)
        if why: suites.append((name, cmd, why))
    for name, (cmd, globs) in EXTRA_BACKEND.items():
        why = "全部" if full_backend else next((f for f in files if _match(f, globs)), None)
        if why: suites.append((name, cmd, why))
    for m in sorted(set(touched) - covered):
        notes.append(f"{m} 不在任何自測的 import 閉包裡，沒有自測涵蓋")
    jobs = {job: full or any(_match(f, globs) for f in files) for job, globs in GLOB_JOBS.items()}
    jobs["backend"] = bool(suites)
    jobs["e2e_clickhouse"] = full_backend or any(m in ch_closure for m in touched)
    jobs["yaml"] = full or any(_match(f, YAML_GLOBS) for f in files)
    return {"jobs": jobs, "suites": suites, "notes": notes}


def run(cmds: list[tuple[str, str]]) -> int:
    done: list[tuple[str, float, int]] = []
    for name, cmd in cmds:
        tool = cmd.split(" ", 1)[0]
        if tool in ("ruff", "pytest") and not shutil.which(tool):   # 沒在 PATH 上就改用 python -m；也沒裝就略過（CI 會跑）
            if not importlib.util.find_spec(tool): print(f"── {name}：略過（本機沒有 {tool}；CI 會跑）"); continue
            cmd = f"{sys.executable} -m {cmd}"
        print(f"── {name}：{cmd}", flush=True)
        t = time.monotonic(); rc = subprocess.run(cmd, shell=True, cwd=ROOT).returncode
        done.append((name, time.monotonic() - t, rc))
        if rc: break
    print("\n結果：")
    for name, sec, rc in done: print(f"  {'✗' if rc else '✓'} {name}（{sec:.1f}s）")
    return next((rc for _, _, rc in done if rc), 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main", help="比較基準（取與 HEAD 的 merge-base）")
    ap.add_argument("--files", nargs="*", help="直接指定異動檔案（不讀 git）")
    ap.add_argument("--all", action="store_true", help="全部都跑")
    ap.add_argument("--run", action="store_true", help="實際執行挑中的測試")
    ap.add_argument("--suites", help="只執行這些 backend 自測（空白分隔；CI backend job 用）")
    ap.add_argument("--github-output", action="store_true", help="把 job 開關與 backend_suites 寫進 $GITHUB_OUTPUT")
    a = ap.parse_args(argv)

    if a.suites is not None:   # CI：changes job 已經挑好，這裡只照名單跑
        table = {n: c for n, c, _ in plan([], full=True)["suites"]}
        want = a.suites.split()
        bad = [n for n in want if n not in table]
        if bad: print(f"未知的自測：{bad}"); return 2
        return run([(n, table[n]) for n in want])

    files = a.files if a.files is not None else changed_files(a.base)
    p = plan(files, full=a.all)
    print(f"異動檔案（{len(files)}）：" + ("".join(f"\n  {f}" for f in files) or " 無"))
    print("\n要跑的 backend 自測：" + ("".join(f"\n  {n:<26} ← {why}" for n, _, why in p["suites"]) or " 無"))
    print("\nCI job：" + "  ".join(f"{j}={'跑' if on else '略'}" for j, on in p["jobs"].items()))
    for n in p["notes"]: print(f"注意：{n}")

    if a.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            for j, on in p["jobs"].items(): out.write(f"{j}={'true' if on else 'false'}\n")
            out.write(f"backend_suites={' '.join(n for n, _, _ in p['suites'])}\n")
    if not a.run: return 0

    cmds = [(n, c) for n, c, _ in p["suites"]]
    if p["jobs"]["yaml"]: cmds.append(("yaml", LOCAL_CMDS["yaml"]))
    if p["jobs"]["prototype"]: cmds.append(("prototype", LOCAL_CMDS["prototype"]))
    if p["jobs"]["web"]:
        fresh = not (ROOT / "web/node_modules").is_dir() or "web/package-lock.json" in files
        cmds.append(("web", LOCAL_CMDS["web"].format(npm_ci="npm ci --no-audit --no-fund && " if fresh else "")))
    if p["jobs"]["e2e_browser"]: print("注意：e2e_browser（Playwright 全端主線）需要本機堆疊，只在 CI 跑；要本機跑見 deploy/quickstart/README.md")
    return run(cmds) if cmds else (print("沒有需要跑的測試。") or 0)


if __name__ == "__main__":
    sys.exit(main())
