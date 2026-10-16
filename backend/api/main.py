"""正式啟動：uvicorn backend.api.main:app --host 0.0.0.0 --port 8000

環境變數：
  DMP_IDP_MODE          broker | direct（覆寫 idp.yaml）
  DMP_SECRETS_JSON      secret_ref → 明文 的 JSON 檔路徑（只在執行節點，由密鑰庫掛載）；沒有時所有 secret_ref 解析失敗
  CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD
  DMP_STORE_PATH        控制面 JSON 檔（單機開發用；正式環境換 PostgreSQL）
  DMP_API_KEY_PEM / DMP_API_PUB_PEM   API token 簽章金鑰檔（沒有就不開 /auth/token）
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
    secrets: dict[str, str] = {}
    if os.environ.get("DMP_SECRETS_JSON"):
        secrets = json.loads(Path(os.environ["DMP_SECRETS_JSON"]).read_text(encoding="utf-8"))
    resolver = lambda ref: secrets[ref]
    store = MemoryStore(Path(os.environ["DMP_STORE_PATH"]) if os.environ.get("DMP_STORE_PATH") else None)
    sessions = SessionManager(cfg["session"], SessionMemory(), lambda e, d: store.audit(e, d), now=time.time, admin_requires=cfg["role_mapping"].get("admin_requires"))
    providers: dict[str, Any] = {}
    for key, p in cfg["providers"].items():
        if p["kind"] == "oidc":
            providers[key] = OIDCProvider(key, p, _http, resolver)
        elif p["kind"] == "ldap" and cfg["mode"] == "direct":
            from ..auth.ldap import PythonLdapClient
            providers[key] = LdapProvider(key, p, PythonLdapClient(p["url"], resolver(p["ca_bundle_ref"])), resolver)
    tokens = None
    if os.environ.get("DMP_API_KEY_PEM"):
        tokens = ApiTokens(cfg["session"]["api_token"], Path(os.environ["DMP_API_KEY_PEM"]).read_text(), Path(os.environ["DMP_API_PUB_PEM"]).read_text())
    ch = ClickHouseHTTP(os.environ["CLICKHOUSE_URL"], user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=120) if os.environ.get("CLICKHOUSE_URL") else None
    auth = AuthService(cfg, providers, sessions, tokens, store.assignments)
    return create_app(auth, store, ch)


app = build() if os.environ.get("DMP_BUILD_APP", "1") == "1" and __name__ != "__main__" else None
