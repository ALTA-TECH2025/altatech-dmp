"""事件表排序鍵升級的回歸（#74）：空庫與舊庫兩條路都要走到「新排序鍵＋one_id_eff 可用＋日彙總 MV 還在」。

舊庫路線涵蓋：搬到一半中斷 → 期間舊表有新寫入 → 重跑接續（被寫入的分區重搬）→ 切換收尾中斷 → 重跑補完 → 再跑無事、schema 重套冪等。
每條路線用自己的資料庫（dmp_mig_fresh／dmp_mig_old），不碰 dmp。

用法：
  CLICKHOUSE_BIN=/path/clickhouse python3 -m backend.clickhouse.migrate_events selftest    # 自己起單機 ClickHouse
  CLICKHOUSE_URL=http://127.0.0.1:8123 python3 -m backend.clickhouse.migrate_events selftest
e2e_clickhouse 也會以同一台伺服器呼叫 run(ch)。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .migrate_events import NEW_KEY_COL, SCHEMA, Migrator, _with_db

LEGACY = Path(__file__).resolve().parent / "legacy" / "events_v1.sql"
SET = {"allow_experimental_json_type": 1}
COLS = "project, time, event, source, one_id, entity_type, entity_id, distinct_id, session_id, event_id, platform, app_id, symbol, side, channel, amount, utm_source, utm_campaign, props, ingest_time, schema_version"


def _ddl(sql: str) -> list[str]:
    from ..integration.runner import local_ddl
    return local_ddl(sql)


def apply_schema(ch: Any, db: str) -> None:
    for st in _ddl(_with_db(SCHEMA.read_text(encoding="utf-8"), db)):
        ch.query(st, settings=SET)


def build_legacy(ch: Any, db: str) -> None:
    """舊庫 = 舊版 events／oneid_backfill ＋ 現行 schema 的其他表（含讀 events 的日彙總 MV）。"""
    ch.query(f"CREATE DATABASE IF NOT EXISTS {db}")
    for st in _ddl(_with_db(LEGACY.read_text(encoding="utf-8"), db)):
        ch.query(st, settings=SET)
    skip = re.compile(rf"CREATE TABLE IF NOT EXISTS {db}\.events\s*\(|ALTER TABLE {db}\.events\s|{db}\.oneid_backfill\b")
    for st in _ddl(_with_db(SCHEMA.read_text(encoding="utf-8"), db)):
        if not skip.search(st) or "CREATE MATERIALIZED VIEW" in st:
            ch.query(st, settings=SET)


def _ev(i: int, ts: str, one_id: str, distinct_id: str, event: str = "page_view", amount: float = 1.0) -> str:
    return (f"('p1', '{ts}', '{event}', 'web', '{one_id}', 'user', '', '{distinct_id}', 's{i}', 'e{i}', 'web', '', '', '', 'app', "
            f"{amount}, '', '', '{{}}', '{ts}', 1)")


def insert_events(ch: Any, db: str, rows: list[str]) -> None:
    ch.query(f"INSERT INTO {db}.events ({COLS}) VALUES " + ", ".join(rows), settings=SET)


def scalar(ch: Any, sql: str) -> str:
    return ch.query(sql + " FORMAT TSV", settings=SET).strip()


def keys(ch: Any, db: str, table: str = "events") -> tuple[str, str]:
    r = scalar(ch, f"SELECT sorting_key, sampling_key FROM system.tables WHERE database = '{db}' AND name = '{table}'").split("\t")
    return r[0], r[1]


def table_sig(ch: Any, db: str, table: str) -> str:
    return scalar(ch, f"SELECT count(), sum(cityHash64(event_id, time, event, distinct_id, one_id, project)) FROM {db}.{table}")


def eff(ch: Any, db: str, event_id: str) -> str:
    return scalar(ch, f"SELECT one_id_eff = cityHash64('cust_1') FROM {db}.events WHERE event_id = '{event_id}'")


def agg_count(ch: Any, db: str, day: str) -> int:
    return int(scalar(ch, f"SELECT countMerge(cnt) FROM {db}.events_daily_agg WHERE event_date = '{day}'") or 0)


def fresh(ch: Any) -> None:
    db = "dmp_mig_fresh"
    ch.query(f"DROP DATABASE IF EXISTS {db} SYNC")
    m = Migrator(ch, db)
    assert m.run() == "fresh", m.status()
    apply_schema(ch, db)
    assert m.status() == "current" and m.run() == "current"
    sk, samp = keys(ch, db)
    assert NEW_KEY_COL in sk and "one_id_uint" not in sk and samp == NEW_KEY_COL, (sk, samp)
    insert_events(ch, db, [_ev(1, "2026-10-10 01:00:00", "anon_1", "dev_1"), _ev(2, "2026-10-12 01:00:00", "anon_1", "dev_1")])
    ch.query(f"INSERT INTO {db}.oneid_backfill (distinct_id, from_time, to_time, anon_one_id, target_one_id, created_at) "
             f"VALUES ('dev_1', '2026-10-09 00:00:00', '2026-10-11 00:00:00', 'anon_1', 'cust_1', now64(3))")
    ch.query(f"SYSTEM RELOAD DICTIONARY {db}.oneid_backfill_dict")
    assert eff(ch, db, "e1") == "1" and eff(ch, db, "e2") == "0", "空庫：回補視窗內換算、視窗外不動"
    ch.query(f"DROP DATABASE {db} SYNC")
    print("  fresh: 新排序鍵／抽樣鍵 ✓  one_id_eff 換算 ✓")


def legacy(ch: Any) -> None:
    db = "dmp_mig_old"
    ch.query(f"DROP DATABASE IF EXISTS {db} SYNC")
    build_legacy(ch, db)
    m = Migrator(ch, db)
    assert m.status() == "legacy" and "one_id_uint" in keys(ch, db)[0]
    # 兩個月分區；dev_9 有兩筆「一客多裝置」對照組
    insert_events(ch, db, [_ev(i, f"2026-09-{10 + i:02d} 02:00:00", f"u{i}", f"dev_{i}") for i in range(1, 6)]
                  + [_ev(10, "2026-10-10 01:00:00", "anon_1", "dev_1"), _ev(11, "2026-10-12 01:00:00", "anon_1", "dev_1"),
                     _ev(12, "2026-10-15 03:00:00", "cust_1", "dev_9", "trade", 50.0)])
    ch.query(f"INSERT INTO {db}.oneid_backfill (distinct_id, from_time, to_time, anon_one_id, target_one_id, applied, created_at) "
             f"VALUES ('dev_1', '2026-10-09 00:00:00', '2026-10-11 00:00:00', 'anon_1', 'cust_1', 1, now64(3))")
    agg_before = agg_count(ch, db, "2026-10-15")
    assert agg_before == 1, agg_before                         # 舊 MV 在跑

    # 1) 搬完第一個分區就中斷
    class Stop(Exception):
        pass

    def stop(pid: str) -> None:
        raise Stop(pid)
    try:
        Migrator(ch, db, on_partition=stop).run()
        raise AssertionError("應在第一個分區後中斷")
    except Stop as e:
        first = str(e)
    assert m.status() == "pending" and "one_id_uint" in keys(ch, db)[0]
    # 2) 中斷期間舊表照常寫入：已搬好的分區被寫入、也寫到還沒搬的分區
    insert_events(ch, db, [_ev(20, "2026-09-20 05:00:00", "u20", "dev_20"), _ev(21, "2026-10-20 05:00:00", "u21", "dev_21")])
    copied: list[str] = []
    # 3) 重跑接續，但在收尾（改名回退表／清進度）前中斷
    class StopFinish(Migrator):
        def _finish(self) -> None:
            raise Stop("finish")
    try:
        StopFinish(ch, db, on_partition=copied.append).run()
        raise AssertionError("應在收尾前中斷")
    except Stop:
        pass
    assert first in copied, (first, copied)                    # 被寫入的已搬分區有重搬
    assert m.status() == "pending" and NEW_KEY_COL in keys(ch, db)[0]
    # 4) 重跑補完收尾
    assert m.run() == "migrated" and m.status() == "current"
    sk, samp = keys(ch, db)
    assert NEW_KEY_COL in sk and "one_id_uint" not in sk and samp == NEW_KEY_COL, (sk, samp)
    assert table_sig(ch, db, "events") == table_sig(ch, db, "events_pre_rekey"), "新表與回退表內容一致"
    assert scalar(ch, f"SELECT count() FROM {db}.events") == "10"
    assert scalar(ch, f"SELECT count() FROM system.tables WHERE database = '{db}' AND name IN ('events_rekey', '_mig_events_rekey', 'events_daily_agg_mv')") == "0"
    idx = scalar(ch, f"SELECT groupArray(name) FROM (SELECT name FROM system.data_skipping_indices WHERE database = '{db}' AND table = 'events' ORDER BY name)")
    assert idx == "['idx_event_id','idx_one_id']", idx
    # 5) 套 schema：MV 重建、回補表補新欄、one_id_eff 可用
    apply_schema(ch, db)
    cols = scalar(ch, f"SELECT groupArray(name) FROM system.columns WHERE database = '{db}' AND table = 'oneid_backfill'")
    assert "distinct_id_uint" in cols and "target_one_id_uint" in cols and "source" in cols and "applied" not in cols, cols
    assert scalar(ch, f"SELECT distinct_id_uint = cityHash64('dev_1') AND source = 'login' FROM {db}.oneid_backfill") == "1"
    ch.query(f"SYSTEM RELOAD DICTIONARY {db}.oneid_backfill_dict")
    assert eff(ch, db, "e10") == "1" and eff(ch, db, "e11") == "0", "舊庫回補對照升級後照樣換算"
    insert_events(ch, db, [_ev(30, "2026-10-15 06:00:00", "u30", "dev_30")])
    assert agg_count(ch, db, "2026-10-15") == agg_before + 1, "日彙總 MV 已重建、新寫入有進彙總"
    assert scalar(ch, f"SELECT count() FROM {db}.events SAMPLE 1/2") != "", "SAMPLE BY 可用"
    # 6) 再跑無事、schema 重套冪等
    assert m.run() == "current"
    apply_schema(ch, db)
    assert m.status() == "current" and scalar(ch, f"SELECT count() FROM {db}.events") == "11"
    ch.query(f"DROP DATABASE {db} SYNC")
    print(f"  legacy: 中斷於分區 {first} → 期間寫入 → 接續重搬 {sorted(copied)} → 收尾中斷 → 補完 ✓  內容簽章一致 ✓  索引 ✓  MV 重建 ✓  回補表新欄 ✓  one_id_eff ✓  冪等 ✓")


def ddl_unit() -> None:
    """不需伺服器：Replicated 版新表另起 ZooKeeper 路徑；單機版去掉 Replicated 與冷層 TTL；索引一併改指新表。"""
    from .migrate_events import events_ddl
    sql = SCHEMA.read_text(encoding="utf-8")
    rep, idx = events_ddl(sql, True)
    assert "'/ch/{shard}/dmp.events_rekey'" in rep and "dmp.events_rekey" in rep and "TO VOLUME 'cold'" in rep, rep[-300:]
    one, idx1 = events_ddl(sql, False, "x")
    assert "Replicated" not in one and "TTL" not in one and one.startswith("CREATE TABLE IF NOT EXISTS x.events_rekey"), one[:200]
    assert len(idx) == 2 and all(i.startswith("ALTER TABLE dmp.events_rekey ADD INDEX") for i in idx), idx
    assert all(i.startswith("ALTER TABLE x.events_rekey ") for i in idx1), idx1


def run(ch: Any) -> None:
    print("事件表排序鍵升級回歸（#74）")
    ddl_unit()
    fresh(ch)
    legacy(ch)


def main() -> int:
    from ..integration.e2e_clickhouse import HTTP_PORT, start_clickhouse
    from ..integration.runner import ClickHouseHTTP
    binary, url = os.environ.get("CLICKHOUSE_BIN"), os.environ.get("CLICKHOUSE_URL")
    if not binary and not url:
        print("略過：需要 CLICKHOUSE_BIN 或 CLICKHOUSE_URL")
        return 0
    proc = None
    work = Path(tempfile.mkdtemp(prefix="dmp-mig-"))
    try:
        if binary:
            proc = start_clickhouse(binary, work); url = f"http://127.0.0.1:{HTTP_PORT}"
        ch = ClickHouseHTTP(url, user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=600)
        if not binary:
            ch.query(f"CREATE NAMED COLLECTION IF NOT EXISTS dmp_dict AS host = '127.0.0.1', port = {int(os.environ.get('CLICKHOUSE_TCP_PORT', '9000'))}, user = '{ch.user}', password = '{ch.password}'")
        run(ch)
        print("OK")
        return 0
    finally:
        if proc:
            proc.terminate(); proc.wait(timeout=30)
