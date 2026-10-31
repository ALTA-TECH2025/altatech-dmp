"""統一錯誤輸出自測（docs/46 §6 PR2 驗收）：由 backend/api/selftest.py 呼叫，掛在真的 create_app() 上跑。

- 每個 REST 代碼：HTTP 狀態取目錄、回應符合 openapi components.schemas.Error、標頭與內文的 request_id 相同
- 502／500／ValueError 不回原始例外文字；原文與 request_id 只進日誌
- X-Request-Id：合格就沿用，不合格就產生；403 的 authz.denied 審計帶 request_id 與 code
- 參數驗證 → 400 INVALID_ARGUMENT＋errors[]；舊寫法 dict detail 拆成 detail＋errors[]＋params；不存在的 API 路徑 → NOT_FOUND
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from .errors import CATALOG, ApiError

SCHEMA = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))["components"]["schemas"]["Error"]
SECRET = "db01.internal:9000 password=hunter2"                              # 假的內部錯誤原文，不可出現在回應


def check_error(r: Any, status: int, code: str) -> dict[str, Any]:
    """回應要符合 Error schema（required、additionalProperties: false、code enum、request_id pattern、params／errors 型別）。"""
    assert r.status_code == status, (status, r.status_code, r.text)
    b = r.json(); props = SCHEMA["properties"]
    assert set(SCHEMA["required"]) <= set(b) and set(b) <= set(props), b
    assert b["code"] == code and b["code"] in props["code"]["enum"] and isinstance(b["detail"], str) and b["detail"], b
    assert b["retryable"] is bool(CATALOG[code]["retryable"]) and re.fullmatch(props["request_id"]["pattern"].strip("^$"), b["request_id"]), b
    assert r.headers.get("x-request-id") == b["request_id"], (r.headers, b)
    assert all(isinstance(v, (str, int, float, bool, list)) for v in b.get("params", {}).values()), b
    for e in b.get("errors", []):
        assert {"field", "message"} <= set(e) <= {"field", "message", "code"} and all(isinstance(v, str) for v in e.values()), e
    return b


def run(app: FastAPI, store: Any) -> int:
    api_codes = [c for c, v in CATALOG.items() if "api" in v["scope"]]

    @app.get("/v1/_selftest/code/{code}", include_in_schema=False)
    def _code(code: str) -> None:
        raise ApiError(code, params={"limit": 5, "bad": {"nested": 1}})        # dict 型的 params 不合 schema，要被濾掉

    @app.get("/v1/_selftest/upstream", include_in_schema=False)
    def _up() -> None:
        raise HTTPException(502, f"查詢失敗：{SECRET}")

    @app.get("/v1/_selftest/boom", include_in_schema=False)
    def _boom() -> None:
        raise RuntimeError(SECRET)

    @app.get("/v1/_selftest/value", include_in_schema=False)
    def _value() -> None:
        int(SECRET)

    @app.get("/v1/_selftest/perm", include_in_schema=False)
    def _perm() -> None:
        raise PermissionError("沒有 segments.export 權限")

    @app.get("/v1/_selftest/legacy", include_in_schema=False)
    def _legacy() -> None:
        raise HTTPException(422, {"message": "檢查未通過", "errors": ["缺負責人", "屬性 memo 為 L4"], "candidates": ["events"], "skip": {"x": 1}})

    @app.post("/v1/_selftest/body", include_in_schema=False)
    def _body(n: int) -> None: ...

    logs: list[str] = []
    h = logging.Handler(); h.emit = lambda rec: logs.append(rec.getMessage())    # type: ignore[method-assign]
    lg = logging.getLogger("dmp.api"); lg.addHandler(h); lg.setLevel(logging.INFO)
    try:
        c = TestClient(app, base_url="https://dmp.internal", raise_server_exceptions=False)
        for code in api_codes:                                                  # 每個代碼：狀態、結構、retryable、request_id
            b = check_error(c.get(f"/v1/_selftest/code/{code}"), int(CATALOG[code]["http"]), code)
            assert code in {"UPSTREAM_FAILED", "DEPENDENCY_UNAVAILABLE", "INTERNAL"} or b["params"] == {"limit": 5}, b
        for path, status, code in (("upstream", 502, "UPSTREAM_FAILED"), ("boom", 500, "INTERNAL"), ("value", 400, "INVALID_ARGUMENT")):
            r = c.get(f"/v1/_selftest/{path}", headers={"X-Request-Id": f"f5-{path}-0001"})
            b = check_error(r, status, code)
            assert SECRET not in r.text and "hunter2" not in r.text and b["detail"] == CATALOG[code]["message"] and b["request_id"] == f"f5-{path}-0001", r.text
            assert any(f"rid=f5-{path}-0001" in m and "hunter2" in m for m in logs), (path, logs[-3:])   # 原文只在日誌，用 request_id 查得到
        r = c.get("/v1/_selftest/perm", headers={"X-Request-Id": "bad id!"})       # 不合格的編號不沿用
        b = check_error(r, 403, "AUTHZ_DENIED"); assert b["request_id"].startswith("req-") and b["detail"] == "沒有 segments.export 權限", b
        ev = store.audit_tail(5)[-1]
        assert ev["event"] == "authz.denied" and ev["request_id"] == b["request_id"] and ev["code"] == "AUTHZ_DENIED", ev
        b = check_error(c.get("/v1/_selftest/legacy"), 422, "RULE_VIOLATION")
        assert b["detail"].startswith("檢查未通過：缺負責人") and [e["message"] for e in b["errors"]] == ["缺負責人", "屬性 memo 為 L4"] and b["params"] == {"candidates": ["events"]}, b
        b = check_error(c.post("/v1/_selftest/body"), 400, "INVALID_ARGUMENT"); assert b["errors"][0]["field"] == "query.n", b
        check_error(c.get("/v1/_selftest_nope"), 404, "NOT_FOUND")
        assert c.get("/healthz").headers.get("x-request-id", "").startswith("req-")   # 成功回應也帶編號
        try:
            ApiError("AMBIGUOUS"); raise AssertionError("tool 專用代碼不可用在 REST")
        except ValueError:
            pass
    finally:
        lg.removeHandler(h)
    return len(api_codes)
