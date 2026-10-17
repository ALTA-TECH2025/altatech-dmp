"""正式啟動：uvicorn backend.api.main:app --host 0.0.0.0 --port 8000

環境變數：
  DMP_IDP_MODE          broker | direct（覆寫 idp.yaml）
  DMP_SECRETS_JSON      secret_ref → 明文 的 JSON 檔路徑（只在執行節點，由密鑰庫掛載）；沒有時所有 secret_ref 解析失敗
  CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD
  DMP_PG_DSN_REF        控制面 PostgreSQL 連線字串的 secret_ref（正式）；本機開發可用 DMP_PG_DSN 明文；兩者都沒有才用 DMP_STORE_PATH JSON 檔
  DMP_STORE_PATH        控制面 JSON 檔（單機開發用）
  DMP_EXPORT_DIR        匯出檔目錄（核可後產生的 CSV；正式環境換物件儲存 + 到期清理）
  DMP_API_KEY_PEM / DMP_API_PUB_PEM   API token 簽章金鑰檔（沒有就不開 /auth/token）
  DMP_SCHED_DB          同步任務排程狀態（SQLite 路徑）；沒有就用暫存檔（重啟後水位重來）
  DMP_LOCAL_ACCOUNTS    本機帳號檔（JSON；idp.yaml › local_accounts）；設了且 mode=direct 就多一個 provider=local 的帳密登入
  DMP_LOCAL_ALLOWED_ROLES  本機帳號可持有的角色（逗號分隔；* = 全部）；預設照 idp.yaml（只有 custom_*）。單機測試環境才放開
  DMP_WEB_DIST          前端建置目錄（web/dist）；設了就由 API 同源提供前端（SPA）
  DMP_TICK              1 = 背景執行緒：每分鐘跑同步任務排程、每 10 分鐘跑分群例行計算與清理（單機環境用；正式環境交給排程系統）
  DMP_K_IDM / DMP_K_ACCT  代碼化（tokenization）金鑰（單機測試用明文；正式環境從密鑰庫以 secret_ref 掛載）
  DMP_COOKIE_SECURE     0 = session cookie 不加 Secure（只給沒有 TLS 的單機測試；正式環境一律 TLS）
  DMP_SMTP_URL / DMP_SMTP_PASSWORD_REF / DMP_MAIL_FROM   看板定時寄送用的 SMTP relay（smtp://user@host:587 或 smtps://…；密碼 secret_ref）；沒設就只產檔不寄
  DMP_MAIL_DOMAINS      定時寄送可直接填的信箱白名單網域（逗號分隔）；沒設就只能用角色當收件人
  DMP_RENDER_KEY        HTML 看板分享連結的簽章金鑰（沒設就存在控制面；多實例請設同一把）
  DMP_BASE_URL          對外網址（分享連結前綴，例：https://dmp.internal）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..auth.api import AuthService, load_cfg
from ..auth.ldap import LdapProvider
from ..auth.local import LocalAccounts, LocalProvider
from ..auth.oidc import OIDCProvider
from ..auth.session import ApiTokens, SessionManager
from ..auth.session import MemoryStore as SessionMemory
from ..integration.runner import ClickHouseHTTP
from .app import create_app
from .store import MemoryStore


def _http(method: str, url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=10) as c:
        r = c.request(method, url, data=data, headers=headers)
        r.raise_for_status()
        return r.json()


def build() -> Any:
    cfg = load_cfg()
    if os.environ.get("DMP_IDP_MODE"):
        cfg["mode"] = os.environ["DMP_IDP_MODE"]
    if os.environ.get("DMP_COOKIE_SECURE") == "0":
        cfg["session"]["cookie"]["secure"] = False
    secrets: dict[str, str] = {}
    if os.environ.get("DMP_SECRETS_JSON"):
        secrets = json.loads(Path(os.environ["DMP_SECRETS_JSON"]).read_text(encoding="utf-8"))
    resolver = lambda ref: secrets[ref]
    if os.environ.get("DMP_PG_DSN_REF") or os.environ.get("DMP_PG_DSN"):
        from .store_pg import PostgresStore
        store: Any = PostgresStore(resolver(os.environ["DMP_PG_DSN_REF"]) if os.environ.get("DMP_PG_DSN_REF") else os.environ["DMP_PG_DSN"])
    else:
        store = MemoryStore(Path(os.environ["DMP_STORE_PATH"]) if os.environ.get("DMP_STORE_PATH") else None)
    sessions = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), now=time.time, admin_requires=cfg["role_mapping"].get("admin_requires"))
    providers: dict[str, Any] = {}
    for key, p in cfg["providers"].items():
        try:
            for k, v in p.items():                                   # 所有 *_ref 都要解析得到，否則整個提供者不啟用（避免登入頁按下去才 500）
                if k.endswith("_ref"):
                    resolver(v)
            if p["kind"] == "oidc":
                providers[key] = OIDCProvider(key, p, _http, resolver)
            elif p["kind"] == "ldap" and cfg["mode"] == "direct":
                from ..auth.ldap import PythonLdapClient
                providers[key] = LdapProvider(key, p, PythonLdapClient(p["url"], resolver(p["ca_bundle_ref"])), resolver)
        except (KeyError, ImportError) as e:                     # 密鑰沒掛載／驅動沒裝 → 這個提供者不啟用（單機環境常見），不要讓整個 API 起不來
            print(f"[auth] provider {key} 未啟用：{type(e).__name__} {e}")
    la_cfg = cfg.get("local_accounts") or {}
    if os.environ.get("DMP_LOCAL_ACCOUNTS") and la_cfg.get("enabled") and cfg["mode"] == "direct":
        allowed = [x.strip() for x in os.environ.get("DMP_LOCAL_ALLOWED_ROLES", "").split(",") if x.strip()] or list(la_cfg.get("allowed_roles") or ["custom_*"])
        providers["local"] = LocalProvider(LocalAccounts(Path(os.environ["DMP_LOCAL_ACCOUNTS"]), {**la_cfg.get("password_policy", {}), "max_days": la_cfg.get("max_days", 90)}, allowed), {"mfa": la_cfg.get("mfa", "totp")})
    tokens = None
    if os.environ.get("DMP_API_KEY_PEM"):
        tokens = ApiTokens(cfg["session"]["api_token"], Path(os.environ["DMP_API_KEY_PEM"]).read_text(), Path(os.environ["DMP_API_PUB_PEM"]).read_text())
    ch = ClickHouseHTTP(os.environ["CLICKHOUSE_URL"], user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=120) if os.environ.get("CLICKHOUSE_URL") else None
    auth = AuthService(cfg, providers, sessions, tokens, store.assignments)
    sched = None
    if os.environ.get("DMP_SCHED_DB"):                      # 排程狀態（SQLite）；執行器直接寫 ClickHouse
        from ..integration.runner import LocalRunner
        from ..integration.scheduler import Scheduler
        sched = Scheduler(os.environ["DMP_SCHED_DB"], LocalRunner(ch))
    for ref, env in (("k_idm", "DMP_K_IDM"), ("k_acct", "DMP_K_ACCT")):
        if os.environ.get(env):
            from ..integration import modeling
            modeling.set_key(ref, os.environ[env].encode())
    mailer = None
    if os.environ.get("DMP_SMTP_URL"):
        from .dashboard_share import smtp_mailer
        mailer = smtp_mailer(os.environ["DMP_SMTP_URL"], resolver(os.environ["DMP_SMTP_PASSWORD_REF"]) if os.environ.get("DMP_SMTP_PASSWORD_REF") else None, os.environ.get("DMP_MAIL_FROM", "dmp@localhost"))
    app = create_app(auth, store, ch, export_dir=os.environ.get("DMP_EXPORT_DIR"), scheduler=sched, mailer=mailer, mail_domains=[x.strip() for x in os.environ.get("DMP_MAIL_DOMAINS", "").split(",") if x.strip()],
                     render_key=os.environ.get("DMP_RENDER_KEY"), base_url=os.environ.get("DMP_BASE_URL", ""))
    if os.environ.get("DMP_WEB_DIST"):
        from .app import mount_web
        mount_web(app, Path(os.environ["DMP_WEB_DIST"]))
    if os.environ.get("DMP_TICK") == "1":
        start_ticker(app, store, ch)
    return app


def start_ticker(app: Any, store: Any, ch: Any) -> None:
    """單機環境的背景例行：每分鐘 scheduler.tick()（到點的同步任務）與看板定時寄送、每 10 分鐘分群例行計算＋到期清理。錯誤只記錄不中斷。"""
    import threading

    from ..security.policy import Subject, authorize
    from ..segments import SegmentService
    sched = getattr(app.state, "scheduler", None)
    seg = SegmentService(store, ch) if ch is not None else None
    system = Subject("system:scheduler", ["admin"])

    def loop() -> None:
        n = 0
        while True:
            time.sleep(60); n += 1
            try:
                if sched is not None:
                    sched.tick()
                share = getattr(app.state, "dashboard_share", None)
                if share is not None and ch is not None:
                    from ..security.policy import RBAC as _R
                    from .dashboards import apply_time as _at
                    share.tick(_at, int(_R["k_anonymity"]["min_group_size"]))
                if seg is not None and n % 10 == 0:
                    seg.tick(system, lambda s: authorize(Subject(s["owner"], ["data_analyst"]), "use", "users"))
                    seg.purge_expired(system)
            except Exception as e:
                store.audit("scheduler.tick.failed", {"error": str(e)[:300]})
    threading.Thread(target=loop, name="dmp-tick", daemon=True).start()


app = build() if os.environ.get("DMP_BUILD_APP", "1") == "1" and __name__ != "__main__" else None
