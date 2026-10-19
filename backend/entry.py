"""容器入口（deploy/images/Dockerfile.core 的 ENTRYPOINT）：同一映像以 DMP_ROLE 分工。

  DMP_ROLE=api     → uvicorn 跑 backend.api.main:app；DMP_HOST／DMP_PORT（預設 0.0.0.0:8080，與 Dockerfile 的 EXPOSE 一致）
  DMP_ROLE=worker  → 不開 API：組同一個 app 並強制 DMP_TICK=1（背景例行：同步任務排程、分群／標籤、推送重試、看板定時寄送、資料輸出），
                     只開一個回 /healthz 的小 HTTP 給探針（回 tick 執行緒是否活著）
  --check          → 不啟動服務：驗角色、埠號、uvicorn 可用、app 模組可載入（DMP_BUILD_APP=0 不連外部服務）；CI 與映像煙霧測試用
  healthcheck      → 打本機 /healthz，200 且 ok=true 回 0，否則 1（Dockerfile HEALTHCHECK 用；映像裡沒有 curl）
  --selftest       → 本模組自測（不連網）

單機／quickstart 仍可直接 `uvicorn backend.api.main:app`；這裡只是讓映像的預設啟動真的能跑。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from typing import Any

ROLES = ("api", "worker")
DEFAULT_PORT = 8080


class EntryError(ValueError):
    pass


def parse(argv: list[str] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """命令列與環境變數 → {mode, role, host, port}。mode = serve | check | healthcheck | selftest。"""
    argv = list(sys.argv[1:] if argv is None else argv); env = os.environ if env is None else env
    mode = "serve"
    if "--selftest" in argv: mode = "selftest"
    elif "--check" in argv: mode = "check"
    elif "healthcheck" in argv: mode = "healthcheck"
    elif argv and argv[0] not in ("serve",):
        raise EntryError(f"不認得的參數 {argv}：可用 --check、healthcheck、--selftest 或不帶參數（依 DMP_ROLE 啟動）")
    role = (env.get("DMP_ROLE") or "api").strip().lower()
    if role not in ROLES:
        raise EntryError(f"DMP_ROLE={role!r} 不支援：只有 {', '.join(ROLES)}")
    try:
        port = int(env.get("DMP_PORT") or DEFAULT_PORT)
    except ValueError:
        raise EntryError(f"DMP_PORT={env.get('DMP_PORT')!r} 不是整數") from None
    if not 1 <= port <= 65535:
        raise EntryError(f"DMP_PORT={port} 超出 1–65535")
    return {"mode": mode, "role": role, "host": env.get("DMP_HOST") or "0.0.0.0", "port": port, "log_level": (env.get("DMP_LOG_LEVEL") or "info").lower()}


def check(opts: dict[str, Any]) -> dict[str, Any]:
    """不啟動服務的啟動前檢查：uvicorn 可匯入、app 模組可載入（不組 app、不連 ClickHouse／IdP）。回摘要給 CI 印。"""
    importlib.import_module("uvicorn")
    prev = os.environ.get("DMP_BUILD_APP"); os.environ["DMP_BUILD_APP"] = "0"
    try:
        m = importlib.import_module("backend.api.main")
    finally:
        if prev is None: os.environ.pop("DMP_BUILD_APP", None)
        else: os.environ["DMP_BUILD_APP"] = prev
    if not callable(getattr(m, "build", None)):
        raise EntryError("backend.api.main 沒有 build()")
    return {"ok": True, "role": opts["role"], "listen": f"{opts['host']}:{opts['port']}", "app": "backend.api.main:app" if opts["role"] == "api" else "backend.api.main:build() + ticker + /healthz"}


def healthcheck(port: int, host: str = "127.0.0.1", timeout: float = 3.0) -> int:
    """探針：GET /healthz 200 且 ok=true → 0；連不上、非 200、ok=false → 1。"""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8") or "{}")
            return 0 if r.status == 200 and body.get("ok") is True else 1
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return 1


def worker_app() -> Any:
    """worker 角色：組正式 app（拿到 scheduler／segments／activation／output／ops 的 tick）並強制背景例行，對外只給 /healthz。"""
    from fastapi import FastAPI
    os.environ["DMP_TICK"] = "1"
    from backend.api.main import build
    build()                                                             # 背景執行緒 dmp-tick 在 build() 內起
    health = FastAPI(title="AltaTech DMP worker", docs_url=None, openapi_url=None)

    @health.get("/healthz")
    def healthz() -> Any:
        alive = any(t.name == "dmp-tick" and t.is_alive() for t in threading.enumerate())
        return ({"ok": True, "role": "worker", "tick_alive": True} if alive
                else __import__("fastapi.responses", fromlist=["JSONResponse"]).JSONResponse({"ok": False, "role": "worker", "tick_alive": False}, status_code=503))

    return health


def serve(opts: dict[str, Any]) -> None:
    import uvicorn
    kw = {"host": opts["host"], "port": opts["port"], "log_level": opts["log_level"], "proxy_headers": True, "forwarded_allow_ips": "*"}
    if opts["role"] == "api":
        uvicorn.run("backend.api.main:app", **kw)
    else:
        uvicorn.run(worker_app(), **kw)


def main(argv: list[str] | None = None) -> int:
    try:
        opts = parse(argv)
    except EntryError as e:
        print(f"[entry] {e}", file=sys.stderr); return 2
    if opts["mode"] == "selftest":
        selftest(); return 0
    if opts["mode"] == "check":
        try:
            print("[entry] check " + json.dumps(check(opts), ensure_ascii=False)); return 0
        except Exception as e:                                          # 匯入失敗也要以非零結束，CI 才會紅
            print(f"[entry] check failed: {type(e).__name__}: {e}", file=sys.stderr); return 1
    if opts["mode"] == "healthcheck":
        return healthcheck(opts["port"])
    serve(opts); return 0


def selftest() -> None:
    """parse／check／healthcheck（對本機暫起的小 HTTP）。不啟動 uvicorn。"""
    import http.server
    import socketserver
    env = {"DMP_ROLE": "api"}
    o = parse([], env); assert o == {"mode": "serve", "role": "api", "host": "0.0.0.0", "port": DEFAULT_PORT, "log_level": "info"}, o
    assert parse(["--check"], {"DMP_ROLE": "worker", "DMP_PORT": "9000", "DMP_HOST": "127.0.0.1"})["mode"] == "check"
    assert parse(["healthcheck"], env)["mode"] == "healthcheck" and parse([], {})["role"] == "api"
    for bad_env, why in (({"DMP_ROLE": "cron"}, "不支援"), ({"DMP_PORT": "x"}, "不是整數"), ({"DMP_PORT": "70000"}, "超出")):
        try: parse([], bad_env); raise AssertionError(bad_env)
        except EntryError as e: assert why in str(e), (bad_env, str(e))
    try: parse(["--nope"], env); raise AssertionError
    except EntryError: pass
    assert main(["--nope"]) == 2
    summary = check(parse(["--check"], env)); assert summary["ok"] and summary["app"] == "backend.api.main:app", summary   # app 模組載得起來（R11 的回歸：以前 backend.entry 根本不存在）
    assert check(parse(["--check"], {"DMP_ROLE": "worker"}))["app"].startswith("backend.api.main:build()")

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = self.path == "/healthz" and self.server.ok   # type: ignore[attr-defined]
            body = json.dumps({"ok": ok}).encode(); self.send_response(200 if ok else 503); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a: Any) -> None: pass
    with socketserver.TCPServer(("127.0.0.1", 0), H) as srv:
        srv.ok = True   # type: ignore[attr-defined]
        port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
        assert healthcheck(port) == 0
        srv.ok = False   # type: ignore[attr-defined]
        assert healthcheck(port) == 1
        srv.shutdown()
    assert healthcheck(port) == 1                                     # 關掉後連不上 → 1
    print("entry self-test ok: parse(api/worker, port, bad args)/check(app module loads)/healthcheck(200 ok, 503, closed)")


if __name__ == "__main__":
    sys.exit(main())
