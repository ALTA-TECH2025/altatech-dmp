"""日彙總（dmp.events_daily_agg）的冪等保證（R12）。

問題：dmp.events 是 ReplacingMergeTree，同一 event_id 重插（GA4 重疊窗口重讀、日批覆蓋串流、重寫重載、任務重試）在合併後只留一版；
但餵 events_daily_agg 的 MV 是「每次 INSERT 都累加」countState／sumState／uniqState，底表之後合併掉的重複列，彙總不會跟著回滾。

兩道保證（都在 runner 載入路徑上）：
1. 入倉前去重（dedupe）：先查這批 event_id 在倉裡的最新版本；內容相同（彙總會看的欄位全同）的列不再寫入——重疊窗口重讀、重試
   這類「一模一樣再來一次」直接變成 no-op，MV 自然不會多算。內容不同的列照寫（ReplacingMergeTree 保留新版本），但記下它的分區。
2. 分區重建（rebuild）：對有「不同版本重插」的分區，從 dmp.events FINAL 重算該日彙總到暫存表，再 REPLACE PARTITION 原子換掉——
   彙總回到與去重後底表一致。全量重載（TRUNCATE）後也把彙總表現有的分區全部重建。

events_daily_agg 因此改成明確目標表（PARTITION BY event_date）+ MV TO；查詢編譯器的表名不變。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

AGG_TABLE, AGG_STAGE = "dmp.events_daily_agg", "dmp.events_daily_agg_stage"
AGG_KEYS = ("project", "event_date", "event", "platform", "channel", "utm_source")
AGG_SELECT = ("SELECT project, event_date, event, platform, channel, utm_source, countState() AS cnt, uniqState(one_id) AS users, sumState(coalesce(amount, 0)) AS amount "
              "FROM dmp.events FINAL WHERE event_date = toDate('{d}') GROUP BY project, event_date, event, platform, channel, utm_source")
# 去重比對的欄位：彙總鍵 + 彙總值會用到的欄位（one_id 進 uniq、amount 進 sum）+ source／time（日批覆蓋串流、時間更正要視為新版本）；
# 其他欄位（props、utm_campaign…）不影響彙總，不比
DEDUPE_COLS = ("event", "one_id", "platform", "channel", "utm_source", "amount", "source", "time")
TZ_OFFSET = timedelta(hours=8)                      # event_date = toDate(time, 'Asia/Taipei')（無日光節約）
CHUNK = 5_000                                        # 一次 IN 幾千個 event_id：查詢文字約 180 KB，加上 max_query_size 放寬，不會撞到預設 256 KB 上限


class CH(Protocol):
    def query(self, sql: str, data: bytes | None = None, settings: dict[str, Any] | None = None) -> str: ...
    def scalar(self, sql: str) -> Any: ...


def event_date_of(row: dict[str, Any]) -> str:
    """列的 event_date（分區鍵 = 台北日期）：time 是 'YYYY-MM-DD HH:MM:SS.mmm'（UTC）字串或 UTC datetime。"""
    t = row.get("time")
    if isinstance(t, datetime):
        dt = t if t.tzinfo is None else t.astimezone(UTC).replace(tzinfo=None)
    elif isinstance(t, date):
        return t.strftime("%Y-%m-%d")
    else:
        dt = datetime.strptime(str(t)[:19], "%Y-%m-%d %H:%M:%S")
    return (dt + TZ_OFFSET).strftime("%Y-%m-%d")


def _num(v: Any) -> float | None:
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    for c in DEDUPE_COLS:
        if c == "amount":
            x, y = _num(a.get(c)), _num(b.get(c))
            if (x is None) != (y is None) or (x is not None and abs(x - y) > 1e-9): return False
        elif c == "time":
            if str(a.get(c) or "")[:19] != str(b.get(c) or "")[:19]: return False
        elif str(a.get(c) or "") != str(b.get(c) or ""):
            return False
    return True


def existing_versions(ch: CH, event_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """倉裡這些 event_id 的最新版本（ingest_time 最大）→ {event_id: {event, one_id, …}}。event_id 有 bloom filter 索引，IN 一批幾萬個可接受。"""
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({str(e) for e in event_ids if e})
    for i in range(0, len(ids), CHUNK):
        lst = ", ".join("'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'" for x in ids[i:i + CHUNK])
        sql = (f"SELECT event_id, argMax(event, ingest_time) AS event, argMax(one_id, ingest_time) AS one_id, argMax(platform, ingest_time) AS platform, "
               f"argMax(channel, ingest_time) AS channel, argMax(utm_source, ingest_time) AS utm_source, argMax(amount, ingest_time) AS amount, "
               f"argMax(source, ingest_time) AS source, toString(argMax(time, ingest_time)) AS time, toString(argMax(event_date, ingest_time)) AS event_date "
               f"FROM dmp.events WHERE event_id IN ({lst}) GROUP BY event_id FORMAT JSONEachRow")
        for line in ch.query(sql, settings={"max_query_size": 4_000_000}).splitlines():
            if line.strip():
                r = json.loads(line); out[r["event_id"]] = r
    return out


def dedupe(ch: CH, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, set[str]]:
    """回 (要寫入的列, 略過的相同列數, 需要重建的 event_date 集合)。
    同 event_id 已在倉裡且內容相同 → 略過；內容不同 → 寫入（新版本）並把該分區列入重建；同一批內重複的 event_id 只留最後一筆。"""
    last: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        k = str(r.get("event_id") or "")
        if k not in last: order.append(k)
        last[k] = r
    existing = existing_versions(ch, order)
    keep: list[dict[str, Any]] = []; skipped = 0; rebuild: set[str] = set()
    for k in order:
        r = last[k]; old = existing.get(k)
        if old is None:
            keep.append(r); continue
        if _same(old, r):
            skipped += 1; continue
        keep.append(r); rebuild.add(event_date_of(r))
        if old.get("event_date"): rebuild.add(str(old["event_date"])[:10])     # 時間更正跨日：舊分區的彙總也要重算
    return keep, skipped, rebuild


def rebuild(ch: CH, dates: Iterable[str]) -> list[str]:
    """重算指定 event_date 的日彙總：先算進暫存表的同一分區，再 REPLACE PARTITION（原子換掉），最後清暫存。回處理過的日期。"""
    done = []
    for d in sorted({str(x)[:10] for x in dates}):
        pid = d.replace("-", "")
        ch.query(f"ALTER TABLE {AGG_STAGE} DROP PARTITION ID '{pid}'")
        ch.query(f"INSERT INTO {AGG_STAGE} " + AGG_SELECT.format(d=d))
        ch.query(f"ALTER TABLE {AGG_TABLE} REPLACE PARTITION ID '{pid}' FROM {AGG_STAGE}")
        ch.query(f"ALTER TABLE {AGG_STAGE} DROP PARTITION ID '{pid}'")
        done.append(d)
    return done


def agg_dates(ch: CH) -> list[str]:
    """彙總表現有的分區日期（全量重載後要全部重建用）。"""
    return [x for x in ch.query(f"SELECT DISTINCT toString(event_date) FROM {AGG_TABLE} ORDER BY 1 FORMAT TSV").split("\n") if x.strip()]


def rebuild_all(ch: CH) -> list[str]:
    """重建底表與彙總表出現過的所有日期（既有環境從舊 MV 換成目標表後的一次性回填；全量重載後）。"""
    base = [x for x in ch.query("SELECT DISTINCT toString(event_date) FROM dmp.events ORDER BY 1 FORMAT TSV").split("\n") if x.strip()]
    return rebuild(ch, set(base) | set(agg_dates(ch)))


def verify(ch: CH, d: str) -> dict[str, Any]:
    """一天的彙總 vs 去重後底表：{events_cnt, agg_cnt, events_users, agg_users, amount_diff}。測試與對帳頁用。"""
    base = ch.query(f"SELECT count(), uniq(one_id), sum(coalesce(amount, 0)) FROM dmp.events FINAL WHERE event_date = toDate('{d}') FORMAT TSV").strip().split("\t")
    agg = ch.query(f"SELECT countMerge(cnt), uniqMerge(users), sumMerge(amount) FROM {AGG_TABLE} WHERE event_date = toDate('{d}') FORMAT TSV").strip().split("\t")
    base = (base + ["0", "0", "0"])[:3]; agg = (agg + ["0", "0", "0"])[:3]
    return {"date": d, "events_cnt": int(base[0]), "agg_cnt": int(agg[0]), "events_users": int(base[1]), "agg_users": int(agg[1]), "amount_diff": abs(float(base[2]) - float(agg[2]))}


def selftest() -> None:
    """不連 ClickHouse：去重比對、分區日期、重建 SQL 的順序（假 CH 記錄語句）。"""
    class Fake:
        def __init__(self, existing: dict[str, dict[str, Any]]): self.existing, self.sql = existing, []
        def query(self, sql: str, data: Any = None, settings: Any = None) -> str:
            self.sql.append(sql)
            if sql.startswith("SELECT event_id, argMax"):
                return "\n".join(json.dumps({"event_id": k, **v}) for k, v in self.existing.items() if f"'{k}'" in sql)
            return ""
        def scalar(self, sql: str) -> Any: return 0
    base = {"event": "bond_deal", "one_id": "v1_a", "platform": "iOS", "channel": "app", "utm_source": "", "amount": 100.0, "source": "ga360_intraday"}
    old = {"e1": {**base, "time": "2026-10-16 01:00:00.000", "event_date": "2026-10-16"},
           "e2": {**base, "time": "2026-10-16 02:00:00.000", "event_date": "2026-10-16"},
           "e4": {**base, "time": "2026-10-16 15:30:00.000", "event_date": "2026-10-16"}}
    rows = [{"event_id": "e1", "time": "2026-10-16 01:00:00.000", **base, "amount": 100},                                       # 一模一樣（重疊窗口重讀）→ 略過
            {"event_id": "e2", "time": "2026-10-16 02:00:00.000", **base, "utm_source": "google"},                             # utm 補齊 → 新版本 + 重建當天
            {"event_id": "e3", "time": "2026-10-17 02:00:00.000", "event": "kh_apply", "one_id": "v1_b", "platform": "WEB", "channel": "", "utm_source": "", "amount": None, "source": "ga360_intraday"},   # 新的
            {"event_id": "e3", "time": "2026-10-17 02:00:00.000", "event": "kh_apply", "one_id": "v1_b", "platform": "WEB", "channel": "", "utm_source": "x", "amount": None, "source": "ga360_intraday"},    # 同批重複 → 留最後一筆
            {"event_id": "e4", "time": "2026-10-16 16:30:00.000", **base}]                                                     # 時間更正跨台北日（15:30Z=16 日 23:30 → 16:30Z=17 日）→ 兩天都重建
    ch = Fake(old)
    keep, skipped, rb = dedupe(ch, rows)
    assert [r["event_id"] for r in keep] == ["e2", "e3", "e4"] and skipped == 1 and rb == {"2026-10-16", "2026-10-17"} and keep[1]["utm_source"] == "x", (keep, skipped, rb)
    assert dedupe(Fake(old), [{"event_id": "e1", "time": "2026-10-16 01:00:00.000", **base, "source": "ga360_daily"}])[0], "日批覆蓋串流：source 不同就是新版本"
    assert _same({"amount": "100.0"}, {"amount": 100}) and not _same({"amount": None}, {"amount": 0}) and _same({"one_id": None}, {"one_id": ""}) and _same({"time": "2026-10-16 01:00:00"}, {"time": "2026-10-16 01:00:00.000"})
    assert event_date_of({"time": datetime(2026, 10, 16, 15, 59)}) == "2026-10-16" and event_date_of({"time": datetime(2026, 10, 16, 16, 0, tzinfo=UTC)}) == "2026-10-17"
    assert event_date_of({"time": "2026-10-16 01:00:00.000"}) == "2026-10-16" and event_date_of({"time": "2026-10-16 17:00:00.000"}) == "2026-10-17" and event_date_of({"time": date(2026, 10, 16)}) == "2026-10-16"
    ch.sql.clear(); assert rebuild(ch, ["2026-10-17", "2026-10-16", "2026-10-16"]) == ["2026-10-16", "2026-10-17"]
    assert [s.split(" ")[0:3] for s in ch.sql[:4]] == [["ALTER", "TABLE", AGG_STAGE], ["INSERT", "INTO", AGG_STAGE], ["ALTER", "TABLE", AGG_TABLE], ["ALTER", "TABLE", AGG_STAGE]], ch.sql[:4]
    assert "REPLACE PARTITION ID '20261016' FROM dmp.events_daily_agg_stage" in ch.sql[2] and "FROM dmp.events FINAL WHERE event_date = toDate('2026-10-16')" in ch.sql[1]
    assert dedupe(ch, []) == ([], 0, set())
    print("aggregates self-test ok: dedupe(same/changed/new/in-batch dup)/event_date/rebuild(stage → replace partition → drop stage)")


def main(argv: list[str]) -> int:
    """python3 -m backend.integration.aggregates rebuild --all | YYYY-MM-DD …（CLICKHOUSE_URL／CLICKHOUSE_USER／CLICKHOUSE_PASSWORD）；不帶參數 = 自測。"""
    if not argv:
        selftest(); return 0
    if argv[0] != "rebuild" or len(argv) < 2:
        print("用法：rebuild --all | rebuild YYYY-MM-DD [YYYY-MM-DD …]"); return 2
    import os

    from .runner import ClickHouseHTTP
    ch = ClickHouseHTTP(os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123"), user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""))
    done = rebuild_all(ch) if argv[1] == "--all" else rebuild(ch, argv[1:])
    for d in done:
        print(json.dumps(verify(ch, d), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
