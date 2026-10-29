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

from .store import Conflict

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
            try:                                                             # 書籤同專案不可同名（store.bookmark_key 同規則）：兩個請求同時存同名，資料庫只讓一個成功
                await c.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS items_bookmark_name ON {self.t('items')} "
                                "((COALESCE(doc->'query'->>'project', '')), (doc->>'name')) WHERE kind = 'bookmark'")
            except asyncpg.UniqueViolationError:                             # 加索引前已有重名：照常啟動（程式層仍擋新的重名），請先改名再重啟
                print(f"[dmp] 警告：{self.schema}.items 已有同專案同名的書籤，唯一索引未建立；改名後重啟即會補上", flush=True)

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
        try:
            self._put("items", "id", obj["id"], obj, {"kind": kind})
        except asyncpg.UniqueViolationError as e:
            raise Conflict(f"{kind} 唯一鍵重複") from e

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
        # 1b. 書籤唯一索引：同專案同名由資料庫擋（兩個執行緒同時寫只成功一個）；大小寫不同、別的專案可以；改同一筆其他欄位不算重名
        bm = lambda i, n, p="golden": {"id": f"bm_{tag}{i}", "name": n, "kind": "events", "query": {"project": p}}
        st.put_item("bookmark", bm(0, "同名"))
        def try_put(i: int) -> str:
            try: st.put_item("bookmark", bm(10 + i, "併發同名")); return "ok"
            except Conflict: return "conflict"
        with ThreadPoolExecutor(8) as ex:
            outcomes = list(ex.map(try_put, range(8)))
        assert outcomes.count("ok") == 1 and outcomes.count("conflict") == 7, outcomes
        try: st.put_item("bookmark", bm(90, "同名")); raise AssertionError("同專案同名應被擋")
        except Conflict: pass
        st.put_item("bookmark", bm(91, "同名", "other")); st.put_item("bookmark", {**bm(0, "同名"), "description": "改說明"})
        st.put_item("bookmark", bm(92, "deal")); st.put_item("bookmark", bm(93, "DEAL"))
        assert sum(1 for x in st.items("bookmark") if x["name"] in ("同名", "併發同名")) == 3
        # 1c. 加索引前已有重名：照常啟動（只印警告），舊資料不動
        tag2 = uuid.uuid4().hex[:6]; s2 = f"selftest_{tag2}"
        async def seed_dups() -> None:
            async with st.pool.acquire() as c:
                await c.execute(f"CREATE SCHEMA {s2}; CREATE TABLE {s2}.items (kind TEXT NOT NULL, id TEXT NOT NULL, doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (kind, id))")
                for i in (1, 2): await c.execute(f"INSERT INTO {s2}.items (kind, id, doc) VALUES ('bookmark', $1, $2)", f"bm_d{i}", json.dumps({"id": f"bm_d{i}", "name": "舊重名", "query": {"project": "p"}}))
        st._loop.run(seed_dups())
        old = PostgresStore(dsn, schema=s2)
        try: assert len(old.items("bookmark")) == 2
        finally: old.drop_schema(); old.close()
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
