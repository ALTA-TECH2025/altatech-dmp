"""事件表排序鍵升級（#74，docs/19 §2.5、docs/23 §4.1）：舊庫的 dmp.events 以 one_id_uint 排序／抽樣，新版改成 distinct_id_uint。

ClickHouse 不能改既有表的排序鍵與 SAMPLE BY，CREATE TABLE IF NOT EXISTS 也不會動既有表，所以舊庫要「建新表 → 逐分區搬 → 切換」：
  1. 舊表先補 distinct_id_uint（DEFAULT，讀取時計算），新表 dmp.events_rekey 照 schema.sql 的 events 建（含 bloom 索引）。
  2. 逐分區搬：先清新表該分區、再整區 INSERT … SELECT，比對「筆數＋內容簽章」相同才記進進度表 dmp._mig_events_rekey。
     重跑時簽章沒變的分區跳過，變了（搬到一半有新寫入）就重搬；舊表已不存在的分區從新表刪掉。
  3. 切換：拆掉讀 events 的 MV（events_daily_agg_mv，套 schema 時會重建）→ EXCHANGE TABLES → 舊表改名 dmp.events_pre_rekey 留著回退。
任何一步中斷都可以直接重跑 migrate()：進度表在就從斷點接續；已切換但沒收尾的也會補完。

切換那一刻起到 schema 重建 MV 之前，寫入者要停（quickstart 的 schema 步驟在 API 啟動前跑；正式環境照 docs/23 §4.1 先停接入排程）。
正式叢集的 ON CLUSTER／各分片執行方式另見 runbook，本模組只處理單一節點（Replicated 引擎的 ZooKeeper 路徑另起 *_rekey）。

用法：
  python3 -m backend.clickhouse.migrate_events status|run [--url http://127.0.0.1:8123] [--replicated]
  CLICKHOUSE_BIN=/path/clickhouse python3 -m backend.clickhouse.migrate_events selftest      # 空庫與舊庫兩條回歸
"""
from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "clickhouse" / "schema.sql"
NEW_KEY_COL = "distinct_id_uint"


def _strip(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _with_db(sql: str, db: str) -> str:
    return sql if db == "dmp" else re.sub(r"\bdmp\.", f"{db}.", sql).replace("DATABASE IF NOT EXISTS dmp", f"DATABASE IF NOT EXISTS {db}")


def events_ddl(schema_sql: str, replicated: bool, db: str = "dmp") -> tuple[str, list[str]]:
    """從 schema.sql 取 events 的 CREATE 與 ADD INDEX，改名成 events_rekey（單機去掉 Replicated 與冷層 TTL）。"""
    s = _strip(schema_sql)
    m = re.search(r"CREATE TABLE IF NOT EXISTS dmp\.events\s*\(.*?;", s, re.S)
    if not m:
        raise RuntimeError("schema.sql 找不到 dmp.events 的 CREATE")
    ddl = m.group(0).rstrip(";").replace("dmp.events", "dmp.events_rekey", 1)
    if replicated:
        ddl = ddl.replace("'/ch/{shard}/dmp.events'", "'/ch/{shard}/dmp.events_rekey'")
    else:
        ddl = re.sub(r"Replicated(\w*MergeTree)\('[^']*',\s*'[^']*'(?:,\s*)?", r"\1(", ddl)
        ddl = re.sub(r"\s*TTL [^;]*TO VOLUME 'cold'", "", ddl)
    idx = [x.strip().rstrip(";").replace("dmp.events ", "dmp.events_rekey ") for x in re.findall(r"ALTER TABLE dmp\.events ADD INDEX[^;]*;", s)]
    return _with_db(ddl, db), [_with_db(x, db) for x in idx]


class Migrator:
    def __init__(self, ch: Any, db: str = "dmp", replicated: bool = False, schema_sql: str | None = None,
                 on_partition: Callable[[str], None] | None = None) -> None:
        self.ch, self.db, self.replicated = ch, db, replicated
        self.schema_sql = schema_sql if schema_sql is not None else SCHEMA.read_text(encoding="utf-8")
        self.on_partition = on_partition                    # 測試用：每搬完一個分區呼叫（模擬中斷）
        self.state = f"{db}._mig_events_rekey"

    # ---- 查詢小工具 ----
    def _q(self, sql: str) -> str:
        return self.ch.query(sql, settings={"allow_experimental_json_type": 1})

    def _rows(self, sql: str) -> list[list[str]]:
        return [ln.split("\t") for ln in self._q(sql + " FORMAT TSV").splitlines() if ln.strip()]

    def _exists(self, name: str) -> bool:
        return bool(self._rows(f"SELECT 1 FROM system.tables WHERE database = '{self.db}' AND name = '{name}'"))

    def sorting_key(self) -> str:
        r = self._rows(f"SELECT sorting_key FROM system.tables WHERE database = '{self.db}' AND name = 'events'")
        return r[0][0] if r else ""

    def status(self) -> str:
        """fresh＝還沒有事件表（直接套 schema）；current＝已是新鍵；pending＝搬到一半；legacy＝舊鍵、待升級。"""
        if not self._exists("events"):
            return "fresh"
        pending = self._exists("_mig_events_rekey")
        if NEW_KEY_COL in self.sorting_key():
            return "pending" if pending else "current"
        return "pending" if pending else "legacy"

    def _sig(self, table: str, pid: str) -> tuple[int, int]:
        r = self._rows(f"SELECT count(), sum(cityHash64(event_id, time, event, distinct_id, one_id, project)) FROM {self.db}.{table} WHERE _partition_id = '{pid}'")
        return int(r[0][0]), int(r[0][1] or 0)

    def _partitions(self, table: str) -> list[str]:
        return [r[0] for r in self._rows(f"SELECT DISTINCT partition_id FROM system.parts WHERE database = '{self.db}' AND table = '{table}' AND active ORDER BY partition_id")]

    def _copy_cols(self) -> list[str]:
        """新表的實體欄位（不含 MATERIALIZED／ALIAS；event_date 由 time 重算），兩邊都有的才搬。"""
        new = [r[0] for r in self._rows(f"SELECT name FROM system.columns WHERE database = '{self.db}' AND table = 'events_rekey' AND default_kind IN ('', 'DEFAULT') ORDER BY position")]
        old = {r[0] for r in self._rows(f"SELECT name FROM system.columns WHERE database = '{self.db}' AND table = 'events'")}
        return [c for c in new if c in old]

    # ---- 主流程 ----
    def run(self) -> str:
        st = self.status()
        if st in ("fresh", "current"):
            return st
        if NEW_KEY_COL in self.sorting_key():          # 已 EXCHANGE、還沒收尾（中斷在最後一步）
            self._finish()
            return "migrated"
        self._q(f"CREATE TABLE IF NOT EXISTS {self.state} (partition_id String, rows UInt64, sig UInt64, at DateTime DEFAULT now()) ENGINE = MergeTree ORDER BY partition_id")
        self._q(f"ALTER TABLE {self.db}.events ADD COLUMN IF NOT EXISTS distinct_id_uint UInt64 DEFAULT cityHash64(distinct_id) AFTER distinct_id")
        ddl, idx = events_ddl(self.schema_sql, self.replicated, self.db)
        self._q(ddl)
        for x in idx:
            self._q(x)
        cols = ", ".join(f"`{c}`" for c in self._copy_cols())
        for _ in range(3):                             # 搬完一輪再核一輪：有分區在搬的期間被寫入就重搬，連續三輪都有新寫入表示寫入者沒停
            old_parts = self._partitions("events")
            if not self._copy_pass(old_parts, cols):
                break
        else:
            raise RuntimeError("搬移期間舊表持續有新寫入：先停接入排程再重跑（進度已保留）")
        for pid in set(self._partitions("events_rekey")) - set(old_parts):     # 舊表已刪的分區
            self._q(f"ALTER TABLE {self.db}.events_rekey DROP PARTITION ID '{pid}'")
        # 切換：MV 綁的是表本身，EXCHANGE 後會留在舊表上 → 先拆，套 schema 時重建
        self._q(f"DROP VIEW IF EXISTS {self.db}.events_daily_agg_mv")
        if self._exists("events_pre_rekey"):
            raise RuntimeError(f"{self.db}.events_pre_rekey 已存在（上一次升級留下的回退表）：確認不需要後先 DROP 再重跑")
        self._q(f"EXCHANGE TABLES {self.db}.events AND {self.db}.events_rekey")
        self._finish()
        return "migrated"

    def _copy_pass(self, old_parts: list[str], cols: str) -> int:
        """逐分區搬一輪，回傳這輪實際搬了幾個分區（0 = 新舊兩表已一致）。"""
        done = {r[0]: (int(r[1]), int(r[2])) for r in self._rows(f"SELECT partition_id, argMax(rows, at), argMax(sig, at) FROM {self.state} GROUP BY partition_id")}
        copied = 0
        for pid in old_parts:
            sig = self._sig("events", pid)
            if done.get(pid) == sig and self._sig("events_rekey", pid) == sig:
                continue
            self._q(f"ALTER TABLE {self.db}.events_rekey DROP PARTITION ID '{pid}'")
            # optimize_on_insert=0：照原樣搬，不在寫入時先做 Replacing 合併（否則筆數對不上）；insert_deduplicate=0：重搬同一分區不被當成重複區塊丟掉
            self.ch.query(f"INSERT INTO {self.db}.events_rekey ({cols}) SELECT {cols} FROM {self.db}.events WHERE _partition_id = '{pid}'",
                          settings={"allow_experimental_json_type": 1, "insert_deduplicate": 0, "optimize_on_insert": 0})
            got = self._sig("events_rekey", pid)
            if got != sig:
                raise RuntimeError(f"分區 {pid} 搬移後不一致：舊表 {sig}、新表 {got}（可直接重跑）")
            self._q(f"INSERT INTO {self.state} (partition_id, rows, sig) VALUES ('{pid}', {sig[0]}, {sig[1]})")
            copied += 1
            if self.on_partition:
                self.on_partition(pid)
        return copied

    def _finish(self) -> None:
        if self._exists("events_rekey"):
            self._q(f"RENAME TABLE {self.db}.events_rekey TO {self.db}.events_pre_rekey")
        self._q(f"DROP VIEW IF EXISTS {self.db}.events_daily_agg_mv")
        self._q(f"DROP TABLE IF EXISTS {self.state}")


def migrate(ch: Any, db: str = "dmp", replicated: bool = False) -> str:
    """套 schema.sql 之前呼叫。回 fresh／current／migrated。"""
    return Migrator(ch, db, replicated).run()


def main(argv: list[str]) -> int:
    import argparse
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "run", "selftest"])
    ap.add_argument("--url", default=os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123"))
    ap.add_argument("--replicated", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        from .migrate_selftest import main as selftest
        return selftest()
    from ..integration.runner import ClickHouseHTTP
    ch = ClickHouseHTTP(a.url, user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=3600)
    m = Migrator(ch, replicated=a.replicated)
    print(m.status() if a.cmd == "status" else m.run())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
