"""任務執行器（LocalRunner）：來源檔案（CSV／JSONL）→ 模型（modeling.apply）→ ClickHouse（JSONEachRow）→ 隔離區 → 對帳。

正式環境的來源是暫存 Parquet（distcp／Storage Read API／Trino 增量查詢落地），邏輯相同：runner 只管「一批列進、三方筆數出」。
ClickHouse 走 HTTP 介面（標準函式庫 urllib），不需驅動程式。
"""
from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import modeling
from .scheduler import RunResult


class ClickHouseHTTP:
    def __init__(self, url: str = "http://127.0.0.1:8123", user: str = "default", password: str = "", database: str = "default", timeout: int = 600):
        self.url, self.user, self.password, self.database, self.timeout = url, user, password, database, timeout

    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str:
        params = {"user": self.user, "password": self.password, "database": self.database, **(settings or {})}
        if data is not None:
            params["query"] = sql
        req = urllib.request.Request(self.url + "/?" + urllib.parse.urlencode(params), data=data if data is not None else sql.encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ClickHouse {e.code}: {e.read().decode()[:600]}\nSQL: {sql[:300]}") from None

    def scalar(self, sql: str) -> Any:
        v = self.query(sql).strip()
        try:
            return int(v)
        except ValueError:
            return v

    def insert_json(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows).encode()
        self.query(f"INSERT INTO {table} FORMAT JSONEachRow", data=body, settings={"input_format_import_nested_json": 1, "date_time_input_format": "best_effort"})
        return len(rows)


def local_ddl(schema_sql: str) -> list[str]:
    """把 schema.sql 的 Replicated 引擎與冷層 TTL 轉成單機版（測試環境）。"""
    s = re.sub(r"--[^\n]*", "", schema_sql)                       # 先去註解，再動引擎與 TTL
    s = re.sub(r"Replicated(\w*MergeTree)\('[^']*',\s*'[^']*'(?:,\s*)?", r"\1(", s)
    s = re.sub(r"\nTTL [^;]*TO VOLUME 'cold'", "", s)
    return [st.strip() for st in s.split(";") if st.strip()]


def read_dbapi(src: dict[str, Any], connect: Callable[[], Any], watermark: str | None, batch_size: int = 50_000) -> list[dict[str, Any]]:
    """DB-API 2.0 來源（MSSQL 走 pyodbc／pymssql，PostgreSQL 走 psycopg；connect 由部署注入，密鑰從 secret_ref 解析）。
    增量：WHERE <incremental_field> > ? ORDER BY <incremental_field>；以 fetchmany 串流，不把整表讀進記憶體。"""
    inc = src.get("incremental_field"); table = src["table"]
    cols = src.get("columns", "*")
    sql = f"SELECT {cols} FROM {table}"
    params: tuple = ()
    if inc and watermark is not None:
        sql += f" WHERE {inc} > {src.get('paramstyle', '?')}"; params = (watermark,)
    if inc:
        sql += f" ORDER BY {inc}"
    conn = connect()
    try:
        cur = conn.cursor(); cur.execute(sql, params)
        names = [d[0] for d in cur.description]; out = []
        while True:
            chunk = cur.fetchmany(batch_size)
            if not chunk: break
            out.extend(dict(zip(names, r)) for r in chunk)
        return out
    finally:
        conn.close()


def read_source(src: dict[str, Any]) -> list[dict[str, Any]]:
    p = Path(src["path"])
    if src.get("kind", "csv") == "jsonl":
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class LocalRunner:
    def __init__(self, ch: ClickHouseHTTP | None, project: str = "altatech_dmp", sem: modeling.Semantic | None = None,
                 db_connections: dict[str, Callable[[], Any]] | None = None):
        self.ch, self.project, self.sem = ch, project, sem
        self.db_connections = db_connections or {}     # connection id（connectors.yaml 的連線）→ DB-API connect 工廠

    def run(self, task: dict[str, Any], *, watermark: str | None, batch_id: str, known_fields: set[str] | None,
            now: datetime, log: Callable[[str], None]) -> RunResult:
        model = modeling.load(task["model_path"])
        errs = modeling.validate(model, self.sem)
        if errs:
            raise ValueError("模型未通過驗證：" + "; ".join(errs))
        src = task["source"]
        inc = src.get("incremental_field")
        if src.get("kind") == "dbapi":
            rows = read_dbapi(src, self.db_connections[src["connection"]], watermark if task["mode"] == "incremental" else None)
            rows = [{k: ("" if v is None else v) for k, v in r.items()} for r in rows]
        else:
            rows = read_source(src)
        if task["mode"] == "incremental" and inc:
            if watermark is not None and src.get("kind") != "dbapi":
                rows = [r for r in rows if str(r.get(inc, "")) > watermark]
            wm_to = max((str(r.get(inc, "")) for r in rows), default=watermark)
        else:
            wm_to = None
        log(f"source={src.get('path')} rows={len(rows)} watermark={watermark}")
        out = modeling.apply(model, rows, known_fields=known_fields, now=now, batch_id=batch_id)
        if rows:
            log("fields=[" + ",".join(sorted(rows[0].keys())) + "]")
        res = RunResult(rows_in=out.summary["in"], rows_ok=out.summary["ok"], rows_quarantined=out.summary["quarantined"],
                        rows_rejected=out.summary["rejected"], watermark_to=wm_to, new_fields=sorted(out.new_fields))
        res.log.append("fields=[" + ",".join(sorted(rows[0].keys())) + "]") if rows else None
        if self.ch is None:   # 乾跑
            res.rows_loaded = res.rows_ok; return res
        ingest_ms = int(now.timestamp() * 1000)
        if model.entity == "dim" and model.raw.get("target", {}).get("mode") == "full":
            self.ch.query(f"TRUNCATE TABLE {model.target_table}")
        target_rows = [modeling.to_ch_row(model, r, self.project, ingest_ms) for r in out.ok]
        for i in range(0, len(target_rows), 50_000):
            self.ch.insert_json(model.target_table, target_rows[i:i + 50_000])
        at = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        q_rows = [{"task_id": task["id"], "batch_id": batch_id, "at": at, "reason": reason, "row": json.dumps(row, ensure_ascii=False, default=str)} for row, reason in out.quarantined]
        q_rows += [{"task_id": task["id"], "batch_id": batch_id, "at": at, "reason": reason, "row": ""} for _, reason in out.rejected]
        self.ch.insert_json("dmp.quarantine", q_rows)
        if model.entity == "event":
            res.rows_loaded = self.ch.scalar(f"SELECT count() FROM dmp.events WHERE batch_id = '{batch_id}'")
        else:
            res.rows_loaded = len(target_rows)
        self.ch.insert_json("dmp.ingest_recon", [{"task_id": task["id"], "batch_id": batch_id, "at": at, "rows_in": res.rows_in, "rows_ok": res.rows_ok,
                                                  "rows_quarantined": res.rows_quarantined, "rows_rejected": res.rows_rejected, "rows_loaded": res.rows_loaded}])
        log(f"loaded={res.rows_loaded} quarantined={res.rows_quarantined} rejected={res.rows_rejected}")
        return res
