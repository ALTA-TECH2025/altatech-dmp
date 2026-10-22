"""多個 API 實例共用的登入狀態（docs/44 §5a）：伺服器端 session 與 SSO 登入前狀態都放 PostgreSQL 控制面。

為什麼：每個 API 程序原本各有一份記憶體 session（SessionMemory）與 login_states，同一個 cookie 打到另一台會 401，
SSO 回呼若被分到另一台也會找不到登入前狀態。改放共用的 PostgreSQL 後，任何一台都認得同一個 cookie、登出一處全部失效。

- session id 只存 SHA-256 雜湊：資料庫外洩也拿不到可用的 cookie 值。cookie 查詢（get）一律先驗格式（43 字元 base64url）再雜湊，
  所以拿資料庫裡的雜湊當 cookie 也查不到。列出（list_user／list_all）時回傳的 Session.sid 是雜湊值，只給伺服器端的
  delete（撤銷、踢舊 session、停用帳號）用；delete 兩種都接受。
- 新建用 add（INSERT）；活動更新用 put，只 UPDATE 仍存在的列：別台已登出／撤銷的 session 不會被舊請求寫回來。
- 登入前狀態只能取用一次：pop 用 DELETE … RETURNING，兩台同時回呼只有一台拿得到；超過 TTL（預設 600 秒）視同不存在。
- 共用 PostgresStore 的連線池與背景事件迴圈；表名帶 schema（同 store_pg 的理由）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any

from .oidc import LoginState
from .session import Session

DDL = """
CREATE TABLE IF NOT EXISTS {s}.iam_sessions (sid_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, doc JSONB NOT NULL, last_seen DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS iam_sessions_user ON {s}.iam_sessions (user_id);
CREATE TABLE IF NOT EXISTS {s}.iam_login_states (state_hash TEXT PRIMARY KEY, doc JSONB NOT NULL, created_at DOUBLE PRECISION NOT NULL);
"""


_RAW_SID = re.compile(r"[A-Za-z0-9_-]{43}")          # secrets.token_urlsafe(32)


def cookie_key(sid: str) -> str | None:
    """cookie 送來的值 → 雜湊；格式不符（含 64 位雜湊本身）一律查不到。"""
    return hashlib.sha256(sid.encode()).hexdigest() if _RAW_SID.fullmatch(sid or "") else None


def sid_key(sid: str) -> str:
    """伺服器端用：原始 sid → 雜湊；已經是雜湊（列表回傳的）就原樣。不可用在 cookie 查詢。"""
    if len(sid) == 64 and all(c in "0123456789abcdef" for c in sid):
        return sid
    return hashlib.sha256(sid.encode()).hexdigest()


class _Pg:
    def __init__(self, pg: Any) -> None:
        self.pg = pg
        self._run(lambda c: c.execute(DDL.format(s=pg.schema)))

    def _run(self, fn: Callable[[Any], Any]) -> Any:
        async def go() -> Any:
            async with self.pg.pool.acquire() as c:
                return await fn(c)
        return self.pg._loop.run(go())


class PgSessionStore(_Pg):
    """SessionStore Protocol 的 PostgreSQL 實作（與 session.MemoryStore 同介面，另有 list_all）。"""

    def _load(self, key: str, doc: str | dict[str, Any]) -> Session:
        d = json.loads(doc) if isinstance(doc, str) else doc
        return Session(sid=key, **d)

    def get(self, sid: str) -> Session | None:
        key = cookie_key(sid)
        if key is None:
            return None
        row = self._run(lambda c: c.fetchrow(f"SELECT doc FROM {self.pg.t('iam_sessions')} WHERE sid_hash=$1", key))
        if not row:
            return None
        s = self._load(key, row["doc"])
        s.sid = sid                                  # 用原始 sid 查的就還原始 sid（cookie_headers、current 比對要用）
        return s

    def add(self, s: Session) -> None:
        d = dataclasses.asdict(s); d.pop("sid")
        sql = f"INSERT INTO {self.pg.t('iam_sessions')} (sid_hash, user_id, doc, last_seen) VALUES ($1,$2,$3::jsonb,$4)"
        self._run(lambda c: c.execute(sql, sid_key(s.sid), s.user_id, json.dumps(d, ensure_ascii=False), s.last_seen))

    def put(self, s: Session) -> None:
        d = dataclasses.asdict(s); d.pop("sid")
        sql = f"UPDATE {self.pg.t('iam_sessions')} SET doc=$2::jsonb, last_seen=$3 WHERE sid_hash=$1"     # 不存在就什麼都不做（不會復活）
        self._run(lambda c: c.execute(sql, sid_key(s.sid), json.dumps(d, ensure_ascii=False), s.last_seen))

    def delete(self, sid: str) -> None:
        self._run(lambda c: c.execute(f"DELETE FROM {self.pg.t('iam_sessions')} WHERE sid_hash=$1", sid_key(sid)))

    def list_user(self, user_id: str) -> list[Session]:
        rows = self._run(lambda c: c.fetch(f"SELECT sid_hash, doc FROM {self.pg.t('iam_sessions')} WHERE user_id=$1", user_id))
        return sorted((self._load(r["sid_hash"], r["doc"]) for r in rows), key=lambda s: s.created_at)

    def list_all(self) -> list[Session]:
        rows = self._run(lambda c: c.fetch(f"SELECT sid_hash, doc FROM {self.pg.t('iam_sessions')} ORDER BY last_seen DESC"))
        return [self._load(r["sid_hash"], r["doc"]) for r in rows]


class PgLoginStates(_Pg):
    """AuthService.login_states 的共用版：只支援 svc 用到的 `[k] = v`、`[k]`、`pop(k, default)`。"""

    def __init__(self, pg: Any, ttl_s: int = 600, now: Callable[[], float] = time.time) -> None:
        super().__init__(pg)
        self.ttl_s, self.now = ttl_s, now

    def __setitem__(self, state: str, st: LoginState) -> None:
        t = self.pg.t("iam_login_states")
        doc = json.dumps(dataclasses.asdict(st), ensure_ascii=False)

        async def go(c: Any) -> None:
            async with c.transaction():
                await c.execute(f"DELETE FROM {t} WHERE created_at < $1", self.now() - self.ttl_s)        # 順手清掉逾時的
                await c.execute(f"INSERT INTO {t} (state_hash, doc, created_at) VALUES ($1,$2::jsonb,$3)", _h(state), doc, st.created_at)
        self._run(go)

    def _fresh(self, row: Any) -> LoginState | None:
        if not row:
            return None
        st = LoginState(**(json.loads(row["doc"]) if isinstance(row["doc"], str) else row["doc"]))
        return st if self.now() - st.created_at <= self.ttl_s else None

    def __getitem__(self, state: str) -> LoginState:
        st = self._fresh(self._run(lambda c: c.fetchrow(f"SELECT doc FROM {self.pg.t('iam_login_states')} WHERE state_hash=$1", _h(state))))
        if st is None:
            raise KeyError(state)
        return st

    def pop(self, state: str, default: Any = None) -> Any:
        row = self._run(lambda c: c.fetchrow(f"DELETE FROM {self.pg.t('iam_login_states')} WHERE state_hash=$1 RETURNING doc", _h(state)))
        st = self._fresh(row)
        return default if st is None else st


def _h(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()
