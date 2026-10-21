"""tools/affected_tests.py：依異動檔挑測試的規則。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("affected_tests", ROOT / "tools" / "affected_tests.py")
at = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(at)
ALL_PY = {n for n, _, _ in at.PY_SUITES}


def names(p): return {n for n, _, _ in p["suites"]}


def test_suite_entries_exist():
    for _, _, entry in at.PY_SUITES: assert (ROOT / entry).is_file(), entry


def test_api_change_skips_web():
    p = at.plan(["backend/api/iam.py"])
    assert {"api", "ruff"} <= names(p) and "policy" not in names(p) and "oneid" not in names(p)
    assert p["jobs"]["backend"] and not p["jobs"]["web"] and not p["jobs"]["e2e_browser"] and not p["jobs"]["contract"]


def test_schema_change_runs_web_not_backend():
    p = at.plan(["backend/openapi.yaml"])
    assert p["jobs"]["web"] and p["jobs"]["e2e_browser"] and p["jobs"]["contract"] and not p["suites"]


def test_policy_change_runs_dependents():
    assert {"policy", "auth", "api", "golden"} <= names(at.plan(["backend/security/policy.py"]))


def test_data_file_maps_to_loader():
    p = at.plan(["backend/semantic/metrics.yaml"])
    assert "golden" in names(p) and "ruff" not in names(p) and p["jobs"]["contract"]
    assert "oneid" in names(at.plan(["backend/semantic/oneid_rules.yaml"]))


def test_web_and_docs_only():
    p = at.plan(["web/src/App.tsx"]); assert not p["suites"] and p["jobs"]["web"] and not p["jobs"]["e2e_clickhouse"]
    p = at.plan(["docs/02-backend-architecture.md", "backend/api/README.md"]); assert not p["suites"] and not any(p["jobs"].values())


def test_conservative_fallbacks():
    assert names(at.plan(["backend/requirements.txt"])) >= ALL_PY
    assert names(at.plan(["backend/api/removed_module.py"])) >= ALL_PY
    p = at.plan(["tools/affected_tests.py"]); assert names(p) >= ALL_PY and all(p["jobs"].values())
