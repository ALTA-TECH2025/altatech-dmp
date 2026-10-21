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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import aggregates, modeling
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
                 db_connections: dict[str, Callable[[], Any]] | None = None, bq_clients: dict[str, Any] | None = None):
        self.ch, self.project, self.sem = ch, project, sem
        self.db_connections = db_connections or {}     # connection id（connectors.yaml 的連線）→ DB-API connect 工廠
        self.bq_clients = bq_clients or {}             # connection id → BigQuery client（ga4_source.BigQueryLike）

    def _settle_marks(self, marks: Any, batch_id: str, log: Callable[[str], None]) -> set[str]:
        """落地之後：與別的寫入者窗口重疊的日期（可能同一筆被寫兩次）先登記待重建再重建；寫入期間被別人重建換過分區的日期由我重建；其餘 touch 自己確認。"""
        race = marks.concurrent()
        if race:
            aggregates.mark_pending(self.ch, race, batch_id)
            log(f"daily_agg: concurrent writer on {sorted(race)} → rebuild")
        return race | marks.settle()

    @staticmethod
    def _log_dedupe(d: Any, log: Callable[[str], None]) -> None:
        log(f"dedupe: skipped={d.skipped} identical rows already in dmp.events" + (f" (incl. {d.skipped_v1} stored under the pre-content event_id formula)" if getattr(d, "skipped_v1", 0) else ""))
        if d.kept_first:
            log(f"dedupe: kept_first={d.kept_first} event_ids already in dmp.events with different content (first copy kept, not overwritten): {d.kept_first_ids}")

    def _run_ga4(self, task: dict[str, Any], *, watermark: str | None, batch_id: str, now: datetime, log: Callable[[str], None]) -> RunResult:
        from . import ga4_source
        src = task["source"]
        b = ga4_source.read(self.bq_clients[src["connection"]], src, watermark=watermark, now=now, batch_id=batch_id, project=self.project)
        log(f"table={b.table} rows={len(b.rows)} quarantined={len(b.quarantined)} watermark={watermark}" + (f" skipped: {b.skipped_reason}" if b.skipped_reason else ""))
        res = RunResult(rows_in=len(b.rows) + len(b.quarantined), rows_ok=len(b.rows), rows_quarantined=len(b.quarantined), watermark_to=b.watermark_to)
        if b.unknown_events:
            log(f"unknown GA event names (kept with ga. prefix; register in 元事件管理): {b.unknown_events}")
        if b.dropped_params:
            log(f"event_params not in allow-list (dropped; register in 元事件管理 or task extra_params): {b.dropped_params}")
        if b.quarantined:
            by = {}
            for _, why in b.quarantined: by[why.split(":")[0]] = by.get(why.split(":")[0], 0) + 1
            log(f"quarantined by L4/PII gate (not loaded into dmp.events): {by}")
        if self.ch is None or not (b.rows or b.quarantined):
            if self.ch is not None:                                                # 空批次（表不存在、重述沒變）也要補做上次中斷留下的待重建日期
                done = aggregates.rebuild(self.ch, set(), batch_id)
                if done:
                    log(f"daily_agg rebuilt for {done} (pending from an interrupted run; this batch was empty)")
            res.rows_loaded = res.rows_ok; return res
        marks = aggregates.begin(self.ch, set(), batch_id, touch=[aggregates.event_date_of(r) for r in b.rows])   # 這批會寫到的日期先登記（帶租約；去重之前，並行寫入者才看得到彼此）
        d = aggregates.dedupe(self.ch, b.rows)                                    # 事件只追加：倉裡已有的 event_id 不再寫入（保留第一筆）→ 日彙總不會多算
        for i in range(0, len(d.rows), 50_000):
            marks.check()                                                         # 租約過期就不准再寫（這批失敗、排程重試）
            self.ch.insert_json("dmp.events", d.rows[i:i + 50_000])
        marks.landed()                                                            # 資料全部落地 → 重建才可以確認這個標記
        res.rows_loaded = self.ch.scalar(f"SELECT count() FROM dmp.events WHERE batch_id = '{batch_id}'") + d.skipped + d.kept_first   # 略過的列早已在倉裡，對帳上算已載入
        mine = self._settle_marks(marks, batch_id, log)
        done = aggregates.rebuild(self.ch, mine, batch_id)                        # ∪ 上次中斷留下的待重建日期
        if done:
            log(f"daily_agg rebuilt for {done}" + (" (incl. pending from an interrupted run)" if set(done) - mine else ""))
        self._log_dedupe(d, log)
        at = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        # 隔離列與 modeling 路徑同格式（原因 + 原列），但個資列不留內容（與 pii 拒絕列同規則）
        self.ch.insert_json("dmp.quarantine", [{"task_id": task["id"], "batch_id": batch_id, "at": at, "reason": why, "row": ""} for _, why in b.quarantined])
        self.ch.insert_json("dmp.ingest_recon", [{"task_id": task["id"], "batch_id": batch_id, "at": at, "rows_in": res.rows_in, "rows_ok": res.rows_ok,
                                                  "rows_quarantined": res.rows_quarantined, "rows_rejected": 0, "rows_loaded": res.rows_loaded}])
        return res

    def run(self, task: dict[str, Any], *, watermark: str | None, batch_id: str, known_fields: set[str] | None,
            now: datetime, log: Callable[[str], None]) -> RunResult:
        if task["source"].get("kind") == "ga4":
            return self._run_ga4(task, watermark=watermark, batch_id=batch_id, now=now, log=log)
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
        skipped, rebuild_dates = 0, set()
        d = None
        if model.entity == "event":
            marks = aggregates.begin(self.ch, set(), batch_id, touch=[aggregates.event_date_of(r) for r in target_rows])   # 會寫到的日期先登記（去重之前）
            d = aggregates.dedupe(self.ch, target_rows)                        # 事件只追加：重試／重跑窗口的同一事件不再寫入（保留第一筆）
            target_rows, skipped = d.rows, d.skipped + d.kept_first
        else:
            marks = None
        for i in range(0, len(target_rows), 50_000):
            if marks is not None: marks.check()
            self.ch.insert_json(model.target_table, target_rows[i:i + 50_000])
        if marks is not None: marks.landed()
        at = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        forbidden = modeling.Semantic.load().forbidden_l4
        q_rows = [{"task_id": task["id"], "batch_id": batch_id, "at": at, "reason": reason,
                   "row": json.dumps(modeling.quarantine_row(model, row, forbidden, reason), ensure_ascii=False, default=str)} for row, reason in out.quarantined]   # 個資不入庫：隔離列也先去個資
        q_rows += [{"task_id": task["id"], "batch_id": batch_id, "at": at, "reason": reason, "row": ""} for _, reason in out.rejected]
        self.ch.insert_json("dmp.quarantine", q_rows)
        if model.entity == "event":
            res.rows_loaded = self.ch.scalar(f"SELECT count() FROM dmp.events WHERE batch_id = '{batch_id}'") + skipped
            if marks is not None: rebuild_dates = self._settle_marks(marks, batch_id, log)
            done = aggregates.rebuild(self.ch, rebuild_dates, batch_id)       # ∪ 上次中斷留下的待重建日期
            if done:
                log(f"daily_agg rebuilt for {done}" + (" (incl. pending from an interrupted run)" if set(done) - rebuild_dates else ""))
            if d is not None and (d.skipped or d.kept_first):
                self._log_dedupe(d, log)
        else:
            res.rows_loaded = len(target_rows)
        self.ch.insert_json("dmp.ingest_recon", [{"task_id": task["id"], "batch_id": batch_id, "at": at, "rows_in": res.rows_in, "rows_ok": res.rows_ok,
                                                  "rows_quarantined": res.rows_quarantined, "rows_rejected": res.rows_rejected, "rows_loaded": res.rows_loaded}])
        log(f"loaded={res.rows_loaded} quarantined={res.rows_quarantined} rejected={res.rows_rejected}")
        return res
