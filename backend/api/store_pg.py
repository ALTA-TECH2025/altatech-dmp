"""控制面儲存的 PostgreSQL 實作（與 store.MemoryStore 同一個 Store Protocol）：角色、指派、分群、審批單／匯出單（泛用 items）、審計。

設計：每種物件一張「id + jsonb doc」表（和 JSON 檔同形，換儲存不改呼叫端）；審計 append-only（bigserial + at 索引）。
asyncpg 已在 requirements（Apache-2.0）；Store 介面是同步的（FastAPI sync handler 在 threadpool），所以把事件迴圈放在一條背景執行緒，
用 run_coroutine_threadsafe 包成同步呼叫；連線池 1–8。DSN 只能來自密鑰（DMP_PG_DSN_REF → DMP_SECRETS_JSON），本機開發才用 DMP_PG_DSN 明文。物件都放在 schema `dmp`（可指定）。

用法：
  DMP_PG_DSN=postgresql://user@host/dmp python3 -m backend.api.store_pg     # 自測：建表 → CRUD → 審計尾端 → 併發寫入 → 以 PostgresStore 跑一遍 API 自測流程
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Coroutine
from typing import Any

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS {s}.iam_roles       (key TEXT PRIMARY KEY, doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS {s}.iam_assignments (id  TEXT PRIMARY KEY, doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS {s}.segments        (id  TEXT PRIMARY KEY, doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS {s}.items           (kind TEXT NOT NULL, id TEXT NOT NULL, doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS {s}.audit_log       (id BIGSERIAL PRIMARY KEY, event TEXT NOT NULL, at DOUBLE PRECISION NOT NULL, actor TEXT, data JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS audit_log_at    ON {s}.audit_log (at);
CREATE INDEX IF NOT EXISTS audit_log_actor ON {s}.audit_log (actor, id);
CREATE INDEX IF NOT EXISTS items_kind      ON {s}.items (kind, updated_at);
"""   # 表名一律帶 schema：asyncpg 連線池歸還時會 RESET 會話狀態，不能靠 search_path


class _Loop:
    """背景事件迴圈：同步介面 → asyncpg。"""
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.t = threading.Thread(target=self.loop.run_forever, name="pg-store", daemon=True); self.t.start()

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


class PostgresStore:
    def __init__(self, dsn: str, schema: str = "dmp", min_size: int = 1, max_size: int = 8):
        if not schema.replace("_", "").isalnum():
            raise ValueError("schema 只能是英數與底線")
        self.schema = schema
        self._loop = _Loop()
        async def mk() -> asyncpg.Pool:
            return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, command_timeout=30)
        self.pool: asyncpg.Pool = self._loop.run(mk())
        self._loop.run(self._init())

    async def _init(self) -> None:
        async with self.pool.acquire() as c:
            await c.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            await c.execute(DDL.format(s=self.schema))

    def t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def drop_schema(self) -> None:
        """只給測試：整個 schema 連資料一起刪。"""
        async def go() -> None:
            async with self.pool.acquire() as c:
                await c.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self._loop.run(go())

    def close(self) -> None:
        self._loop.run(self.pool.close()); self._loop.stop()

    # ---- 泛用 doc 表
    def _all(self, table: str, where: str = "", *args: Any) -> list[dict[str, Any]]:
        async def go() -> list[dict[str, Any]]:
            async with self.pool.acquire() as c:
                rows = await c.fetch(f"SELECT doc FROM {self.t(table)} {where} ORDER BY updated_at, doc->>'id'", *args)
                return [json.loads(r["doc"]) for r in rows]
        return self._loop.run(go())

    def _put(self, table: str, keycol: str, key: str, doc: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
        cols = [keycol] + list((extra or {}).keys()) + ["doc", "updated_at"]
        vals = [key] + list((extra or {}).values()) + [json.dumps(doc, ensure_ascii=False)]
        ph = ", ".join(f"${i + 1}" for i in range(len(vals))) + ", now()"
        conflict = ", ".join([keycol] + list((extra or {}).keys()))
        async def go() -> None:
            async with self.pool.acquire() as c:
                await c.execute(f"INSERT INTO {self.t(table)} ({', '.join(cols)}) VALUES ({ph}) ON CONFLICT ({conflict}) DO UPDATE SET doc = EXCLUDED.doc, updated_at = now()", *vals)
        self._loop.run(go())

    def _del(self, table: str, where: str, *args: Any) -> None:
        async def go() -> None:
            async with self.pool.acquire() as c:
                await c.execute(f"DELETE FROM {self.t(table)} WHERE {where}", *args)
        self._loop.run(go())

    # ---- Store Protocol
    def roles(self) -> list[dict[str, Any]]:
        return self._all("iam_roles")

    def put_role(self, role: dict[str, Any]) -> None:
        self._put("iam_roles", "key", role["key"], role)

    def delete_role(self, key: str) -> None:
        self._del("iam_roles", "key = $1", key)

    def assignments(self) -> list[dict[str, Any]]:
        return self._all("iam_assignments")

    def put_assignment(self, a: dict[str, Any]) -> None:
        a.setdefault("id", "asg_" + uuid.uuid4().hex[:10]); self._put("iam_assignments", "id", a["id"], a)

    def delete_assignment(self, id_: str) -> None:
        self._del("iam_assignments", "id = $1", id_)

    def segments(self) -> list[dict[str, Any]]:
        return self._all("segments")

    def put_segment(self, seg: dict[str, Any]) -> None:
        self._put("segments", "id", seg["id"], seg)

    def delete_segment(self, id_: str) -> None:
        self._del("segments", "id = $1", id_)

    def items(self, kind: str) -> list[dict[str, Any]]:
        return self._all("items", "WHERE kind = $1", kind)

    def put_item(self, kind: str, obj: dict[str, Any]) -> None:
        self._put("items", "id", obj["id"], obj, {"kind": kind})

    def delete_item(self, kind: str, id_: str) -> None:
        self._del("items", "kind = $1 AND id = $2", kind, id_)

    def audit(self, event: str, data: dict[str, Any]) -> None:
        async def go() -> None:
            async with self.pool.acquire() as c:
                await c.execute(f"INSERT INTO {self.t('audit_log')} (event, at, actor, data) VALUES ($1, $2, $3, $4)", event, time.time(), str(data.get("user") or ""), json.dumps(data, ensure_ascii=False, default=str))
        self._loop.run(go())

    def audit_tail(self, n: int = 100) -> list[dict[str, Any]]:
        async def go() -> list[dict[str, Any]]:
            async with self.pool.acquire() as c:
                rows = await c.fetch(f"SELECT event, at, data FROM {self.t('audit_log')} ORDER BY id DESC LIMIT $1", n)
                return [{"event": r["event"], "at": r["at"], **json.loads(r["data"])} for r in reversed(rows)]
        return self._loop.run(go())


def selftest(dsn: str) -> None:
    import os
    from concurrent.futures import ThreadPoolExecutor
    tag = uuid.uuid4().hex[:6]
    st = PostgresStore(dsn, schema=f"selftest_{tag}")            # 獨立 schema：可重複跑、跑完整個刪掉
    try:
        # 1. CRUD 與同形：角色、指派、分群、items、審計尾端順序
        st.put_role({"key": f"custom_{tag}", "name": "x", "grants": {"events": ["view"]}}); assert any(r["key"] == f"custom_{tag}" for r in st.roles())
        st.put_role({"key": f"custom_{tag}", "name": "y", "grants": {}}); assert next(r for r in st.roles() if r["key"] == f"custom_{tag}")["name"] == "y"      # upsert
        a = {"user_id": f"u-{tag}", "roles": [f"custom_{tag}"]}; st.put_assignment(a); assert a["id"].startswith("asg_") and any(x["id"] == a["id"] for x in st.assignments())
        st.put_segment({"id": f"sg_{tag}", "name": "s", "status": "active", "updated_at": "2026-10-16T00:00:00"}); assert any(s["id"] == f"sg_{tag}" for s in st.segments())
        st.put_item("approvals", {"id": f"ap_{tag}", "status": "pending"}); st.put_item("exports", {"id": f"ex_{tag}", "status": "pending_approval"})
        assert any(x["id"] == f"ap_{tag}" for x in st.items("approvals")) and not any(x["id"] == f"ap_{tag}" for x in st.items("exports"))
        for i in range(3): st.audit("t.event", {"user": f"u-{tag}", "i": i, "中文": "可以"})
        tail = st.audit_tail(3); assert [x["i"] for x in tail] == [0, 1, 2] and tail[-1]["中文"] == "可以" and tail[-1]["event"] == "t.event"
        # 2. 併發寫入（threadpool 模擬 FastAPI sync handler）
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda i: st.audit("t.concurrent", {"user": f"u-{tag}", "i": i}), range(40)))
        assert sum(1 for x in st.audit_tail(60) if x["event"] == "t.concurrent" and x.get("user") == f"u-{tag}") == 40
        st.delete_role(f"custom_{tag}"); st.delete_assignment(a["id"]); st.delete_segment(f"sg_{tag}"); st.delete_item("approvals", f"ap_{tag}"); st.delete_item("exports", f"ex_{tag}")
        assert not any(r["key"] == f"custom_{tag}" for r in st.roles()) and not any(s["id"] == f"sg_{tag}" for s in st.segments())
        # 3. 整套 API 自測流程改用 PostgresStore（角色 CRUD、分群、審批、匯出都落 PostgreSQL）
        if os.environ.get("DMP_PG_SKIP_API") != "1":
            from .selftest import main as api_selftest
            api_selftest(store=st)
        print(f"postgres store self-test ok: {dsn.split('@')[-1]} schema=selftest_{tag} roles/assignments/segments/items/audit upsert+delete, 40 concurrent audits, api self-test on PostgresStore")
    finally:
        st.drop_schema(); st.close()


if __name__ == "__main__":
    import os
    import sys
    dsn = os.environ.get("DMP_PG_DSN")
    if not dsn:
        print("DMP_PG_DSN 未設定：略過 PostgreSQL 儲存自測（本機可用 deploy/quickstart 起 PostgreSQL）"); sys.exit(0)
    selftest(dsn)
