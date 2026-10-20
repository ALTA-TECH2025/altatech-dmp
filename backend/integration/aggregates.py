"""日彙總（dmp.events_daily_agg）的冪等保證（R12）。

問題：dmp.events 是 ReplacingMergeTree，同一 event_id 重插（GA4 重疊窗口重讀、日批覆蓋串流、重寫重載、任務重試）在合併後只留一版；
但餵 events_daily_agg 的 MV 是「每次 INSERT 都累加」countState／sumState／uniqState，底表之後合併掉的重複列，彙總不會跟著回滾。

兩道保證（都在 runner 載入路徑上）：
1. 入倉前去重（dedupe）：先查這批 event_id 在倉裡的最新版本；內容相同（彙總會看的欄位全同）的列不再寫入——重疊窗口重讀、重試
   這類「一模一樣再來一次」直接變成 no-op，MV 自然不會多算。內容不同的列照寫（ReplacingMergeTree 保留新版本），但記下它的分區。
2. 分區重建（rebuild）：對有「不同版本重插」的分區，從 dmp.events FINAL 重算該日彙總到暫存表，再 REPLACE PARTITION 原子換掉——
   彙總回到與去重後底表一致。全量重載（TRUNCATE）後也把彙總表現有的分區全部重建。
3. 重建意圖落地（events_daily_agg_pending）：要重建哪幾天先寫進這張表，再清舊版本／寫入新版本；重建成功才刪掉該日的標記。
   寫入與重建之間任何一點中斷（寫入後、清舊版本後、REPLACE PARTITION 前後），下一次載入（哪怕整批內容相同、一筆都不寫）
   都會先把標記裡的日期重建掉——重試不會把錯的彙總當成功。

events_daily_agg 因此改成明確目標表（PARTITION BY event_date）+ MV TO；查詢編譯器的表名不變。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

AGG_TABLE, AGG_STAGE, AGG_PENDING = "dmp.events_daily_agg", "dmp.events_daily_agg_stage", "dmp.events_daily_agg_pending"
AGG_KEYS = ("project", "event_date", "event", "platform", "channel", "utm_source")
AGG_SELECT = ("SELECT project, event_date, event, platform, channel, utm_source, countState() AS cnt, uniqState(one_id) AS users, sumState(coalesce(amount, 0)) AS amount "
              "FROM dmp.events FINAL WHERE event_date = toDate('{d}') GROUP BY project, event_date, event, platform, channel, utm_source")
# 「完整版本」比對的欄位：事件內容全部（彙總鍵／值 + props、utm_campaign、symbol…），載入元資料（ingest_time、batch_id、late）不算。
# 相同 → 不寫入；任何欄位不同 → 寫入新版本並重建該日彙總（MV 是累加的，哪怕只改 props 也會多算一筆）。
CONTENT_COLS = ("time", "event", "source", "one_id", "entity_type", "entity_id", "distinct_id", "session_id", "platform", "app_id", "symbol", "side", "channel",
                "amount", "utm_source", "utm_campaign", "props", "schema_version")
# ReplacingMergeTree 的排序鍵裡會變的欄位：這些一變，舊列就是另一個排序鍵，FINAL 不會把它換掉 → 要主動清掉舊版本
SORT_KEY_COLS = ("event_date", "event", "one_id", "time")
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


def _expand_dotted(v: Any) -> Any:
    """ClickHouse 的 JSON 型別把帶點的鍵（consent.analytics）存成巢狀路徑（consent → analytics）：比對前把新列也展成同樣的結構。"""
    if isinstance(v, list): return [_expand_dotted(x) for x in v]
    if not isinstance(v, dict): return v
    out: dict[str, Any] = {}
    for k, val in v.items():
        parts = str(k).split(".") if "." in str(k) else [str(k)]
        cur = out
        for seg in parts[:-1]:
            nxt = cur.get(seg)
            if not isinstance(nxt, dict): nxt = {}; cur[seg] = nxt
            cur = nxt
        leaf = _expand_dotted(val)
        if isinstance(leaf, dict) and isinstance(cur.get(parts[-1]), dict): cur[parts[-1]].update(leaf)
        else: cur[parts[-1]] = leaf
    return out


def _props_norm(v: Any) -> str:
    """props 正規化成可比對的字串：倉裡回來的是 JSON 字串、新列是 dict；帶點的鍵展成巢狀，再轉成鍵排序的 JSON。"""
    if isinstance(v, str):
        try: v = json.loads(v) if v.strip() else {}
        except ValueError: return v
    return json.dumps(_expand_dotted(v if v is not None else {}), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _same(a: dict[str, Any], b: dict[str, Any], cols: tuple[str, ...] = CONTENT_COLS) -> bool:
    for c in cols:
        if c == "amount":
            x, y = _num(a.get(c)), _num(b.get(c))
            if (x is None) != (y is None) or (x is not None and abs(x - y) > 1e-9): return False
        elif c == "time":
            if str(a.get(c) or "")[:23] != str(b.get(c) or "")[:23]: return False
        elif c == "props":
            if _props_norm(a.get(c)) != _props_norm(b.get(c)): return False
        elif c == "event_date":
            if str(a.get(c) or "")[:10] != event_date_of(b): return False
        elif str(a.get(c) if a.get(c) is not None else "") != str(b.get(c) if b.get(c) is not None else ""):
            return False
    return True


def existing_versions(ch: CH, event_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """倉裡這些 event_id 的最新版本（ingest_time 最大）→ {event_id: {event, one_id, …}}。event_id 有 bloom filter 索引，IN 一批幾萬個可接受。"""
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({str(e) for e in event_ids if e})
    for i in range(0, len(ids), CHUNK):
        lst = ", ".join("'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'" for x in ids[i:i + CHUNK])
        cols = ", ".join(f"argMax({c}, ingest_time) AS {c}" for c in CONTENT_COLS if c not in ("time", "props"))
        sql = (f"SELECT event_id, {cols}, toString(argMax(time, ingest_time)) AS time, argMax(toString(props), ingest_time) AS props, "
               f"toString(argMax(event_date, ingest_time)) AS event_date "
               f"FROM dmp.events WHERE event_id IN ({lst}) GROUP BY event_id FORMAT JSONEachRow")
        for line in ch.query(sql, settings={"max_query_size": 4_000_000}).splitlines():
            if line.strip():
                r = json.loads(line); out[r["event_id"]] = r
    return out


@dataclass
class DedupeResult:
    rows: list[dict[str, Any]]                      # 要寫入的列（新事件 + 內容有變的新版本）
    skipped: int = 0                                # 完整版本相同、不寫入的列數
    rebuild: set[str] = field(default_factory=set)  # 要重算日彙總的 event_date（新版本的日期；排序鍵變了連舊日期一起）
    stale: list[tuple[str, str, str]] = field(default_factory=list)   # 要清掉的舊版本 (event_id, time, one_id)：排序鍵變了，FINAL 不會替換


def dedupe(ch: CH, rows: list[dict[str, Any]]) -> DedupeResult:
    """同 event_id 已在倉裡且「完整版本」相同（CONTENT_COLS 全同）→ 略過；任何內容不同 → 寫入新版本並重建該日彙總；
    排序鍵（event_date／event／one_id／time）有變 → 另外記下舊版本要清掉（purge_stale），否則 ReplacingMergeTree 會留兩版。同一批內重複的 event_id 只留最後一筆。"""
    last: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        k = str(r.get("event_id") or "")
        if k not in last: order.append(k)
        last[k] = r
    existing = existing_versions(ch, order)
    out = DedupeResult(rows=[])
    for k in order:
        r = last[k]; old = existing.get(k)
        if old is None:
            out.rows.append(r); continue
        if _same(old, r):
            out.skipped += 1; continue
        out.rows.append(r); out.rebuild.add(event_date_of(r))
        if old.get("event_date"): out.rebuild.add(str(old["event_date"])[:10])
        if not _same(old, r, SORT_KEY_COLS):
            out.stale.append((k, str(old.get("time") or "")[:23], str(old.get("one_id") or "")))
    return out


def purge_stale(ch: CH, stale: Iterable[tuple[str, str, str]]) -> int:
    """清掉排序鍵已變的舊版本（lightweight DELETE，立即對查詢生效）：同 event_id 只留新版本，舊日期的 FINAL 與彙總才會歸零。"""
    keys = [k for k in stale if k[0]]
    for i in range(0, len(keys), CHUNK):
        tuples = ", ".join(f"('{e.replace(chr(39), chr(92) + chr(39))}', toDateTime64('{t}', 3, 'UTC'), '{o.replace(chr(39), chr(92) + chr(39))}')" for e, t, o in keys[i:i + CHUNK])
        ch.query(f"DELETE FROM dmp.events WHERE (event_id, time, one_id) IN ({tuples})", settings={"max_query_size": 4_000_000})
    return len(keys)


def mark_pending(ch: CH, dates: Iterable[str], batch_id: str = "") -> list[str]:
    """把「要重建的日期」先落地到 events_daily_agg_pending（在清舊版本／寫入新版本之前呼叫）；重建成功後 rebuild 才會刪掉。重複標記無妨。"""
    ds = sorted({str(x)[:10] for x in dates if str(x).strip()})
    if ds:
        vals = ", ".join(f"(toDate('{d}'), '{batch_id.replace(chr(39), chr(92) + chr(39))}')" for d in ds)
        ch.query(f"INSERT INTO {AGG_PENDING} (event_date, batch_id) VALUES {vals}")
    return ds


def pending_dates(ch: CH) -> list[str]:
    """上次載入中斷後還沒重建的日期（標記表裡還在的）。"""
    return [x for x in ch.query(f"SELECT DISTINCT toString(event_date) FROM {AGG_PENDING} ORDER BY 1 FORMAT TSV").split("\n") if x.strip()]


def rebuild(ch: CH, dates: Iterable[str], batch_id: str = "", with_pending: bool = True) -> list[str]:
    """重算日彙總：這批要重建的日期 ∪ 標記表裡待重建的日期（上次中斷留下的）。每一天：先標記（重建途中斷也能補做）→ 算進暫存表同一分區
    → REPLACE PARTITION（原子換掉）→ 清暫存 → 刪掉該日標記。每一步都可重跑：暫存先 DROP 再算、REPLACE 是整個分區換掉、標記刪完才算結束。回處理過的日期。"""
    todo = {str(x)[:10] for x in dates if str(x).strip()}
    if with_pending:
        todo |= set(pending_dates(ch))
    done = []
    for d in sorted(todo):
        pid = d.replace("-", "")
        mark_pending(ch, [d], batch_id or "rebuild")
        ch.query(f"ALTER TABLE {AGG_STAGE} DROP PARTITION ID '{pid}'")
        ch.query(f"INSERT INTO {AGG_STAGE} " + AGG_SELECT.format(d=d))
        ch.query(f"ALTER TABLE {AGG_TABLE} REPLACE PARTITION ID '{pid}' FROM {AGG_STAGE}")
        ch.query(f"ALTER TABLE {AGG_STAGE} DROP PARTITION ID '{pid}'")
        ch.query(f"DELETE FROM {AGG_PENDING} WHERE event_date = toDate('{d}')")
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
            if sql.startswith(f"SELECT DISTINCT toString(event_date) FROM {AGG_PENDING}"):
                return "\n".join(getattr(self, "pending", []))
            return ""
        def scalar(self, sql: str) -> Any: return 0
    base = {"event": "bond_deal", "one_id": "v1_a", "platform": "iOS", "channel": "app", "utm_source": "", "amount": 100.0, "source": "ga360_intraday", "entity_type": "", "entity_id": "",
            "distinct_id": "", "session_id": "", "app_id": "", "symbol": "2330", "side": "B", "utm_campaign": "", "schema_version": 1}
    old = {"e1": {**base, "time": "2026-10-16 01:00:00.000", "event_date": "2026-10-16", "props": '{"market":"TSE","n":1}'},
           "e2": {**base, "time": "2026-10-16 02:00:00.000", "event_date": "2026-10-16", "props": "{}"},
           "e4": {**base, "time": "2026-10-16 15:30:00.000", "event_date": "2026-10-16", "props": "{}"},
           "e5": {**base, "time": "2026-10-16 03:00:00.000", "event_date": "2026-10-16", "props": '{"market":"TSE"}'},
           "e6": {**base, "time": "2026-10-16 04:00:00.000", "event_date": "2026-10-16", "props": "{}"}}
    rows = [{"event_id": "e1", "time": "2026-10-16 01:00:00.000", **base, "amount": 100, "props": {"n": 1, "market": "TSE"}},     # 一模一樣（重疊窗口重讀；props 鍵序不同也算同）→ 略過
            {"event_id": "e2", "time": "2026-10-16 02:00:00.000", **base, "utm_source": "google", "props": {}},                 # utm 補齊 → 新版本 + 重建當天
            {"event_id": "e3", "time": "2026-10-17 02:00:00.000", **base, "event": "kh_apply", "one_id": "v1_b", "props": {}},  # 新的
            {"event_id": "e3", "time": "2026-10-17 02:00:00.000", **base, "event": "kh_apply", "one_id": "v1_b", "utm_source": "x", "props": {}},   # 同批重複 → 留最後一筆
            {"event_id": "e4", "time": "2026-10-16 16:30:00.000", **base, "props": {}},                                         # 時間更正跨台北日（15:30Z=16 日 23:30 → 16:30Z=17 日）→ 兩天重建 + 清舊版本
            {"event_id": "e5", "time": "2026-10-16 03:00:00.000", **base, "props": {"market": "US"}, "utm_campaign": "new"},    # 只改 props／utm_campaign（不影響彙總）→ 仍要入倉 + 重建（MV 累加）；排序鍵沒變 → 不清
            {"event_id": "e6", "time": "2026-10-16 04:00:00.000", **base, "one_id": "v1_rewritten", "props": {}}]              # 歸戶改寫 → 新版本 + 清舊版本
    ch = Fake(old)
    d = dedupe(ch, rows)
    assert [r["event_id"] for r in d.rows] == ["e2", "e3", "e4", "e5", "e6"] and d.skipped == 1 and d.rebuild == {"2026-10-16", "2026-10-17"} and d.rows[1]["utm_source"] == "x", d
    assert d.stale == [("e4", "2026-10-16 15:30:00.000", "v1_a"), ("e6", "2026-10-16 04:00:00.000", "v1_a")], d.stale
    assert dedupe(Fake(old), [{"event_id": "e1", "time": "2026-10-16 01:00:00.000", **base, "props": {"market": "TSE", "n": 1}, "source": "ga360_daily"}]).rows, "日批覆蓋串流：source 不同就是新版本"
    assert _same({"amount": "100.0"}, {"amount": 100}, ("amount",)) and not _same({"amount": None}, {"amount": 0}, ("amount",)) and _same({"one_id": None}, {"one_id": ""}, ("one_id",))
    assert _same({"time": "2026-10-16 01:00:00.000"}, {"time": "2026-10-16 01:00:00.000"}, ("time",)) and not _same({"time": "2026-10-16 01:00:00.100"}, {"time": "2026-10-16 01:00:00.000"}, ("time",))   # 毫秒也算
    assert _same({"props": '{"b":1,"a":"x"}'}, {"props": {"a": "x", "b": 1}}, ("props",)) and not _same({"props": "{}"}, {"props": {"a": 1}}, ("props",))
    assert _same({"props": '{"consent":{"analytics":true},"a":{"b":{"c":1}}}'}, {"props": {"consent.analytics": True, "a.b.c": 1}}, ("props",))   # 帶點的鍵＝ClickHouse 的巢狀路徑
    ch.sql.clear(); assert purge_stale(ch, d.stale) == 2 and ch.sql[0].startswith("DELETE FROM dmp.events WHERE (event_id, time, one_id) IN (('e4', toDateTime64('2026-10-16 15:30:00.000', 3, 'UTC'), 'v1_a')"), ch.sql[0]
    assert event_date_of({"time": datetime(2026, 10, 16, 15, 59)}) == "2026-10-16" and event_date_of({"time": datetime(2026, 10, 16, 16, 0, tzinfo=UTC)}) == "2026-10-17"
    assert event_date_of({"time": "2026-10-16 01:00:00.000"}) == "2026-10-16" and event_date_of({"time": "2026-10-16 17:00:00.000"}) == "2026-10-17" and event_date_of({"time": date(2026, 10, 16)}) == "2026-10-16"
    ch.sql.clear(); assert rebuild(ch, ["2026-10-17", "2026-10-16", "2026-10-16"]) == ["2026-10-16", "2026-10-17"]
    assert ch.sql[0].startswith(f"SELECT DISTINCT toString(event_date) FROM {AGG_PENDING}"), ch.sql[0]                       # 先把上次中斷留下的待重建日期撈進來
    step = ch.sql[1:7]
    assert [x.split(" ")[0:3] for x in step] == [["INSERT", "INTO", AGG_PENDING], ["ALTER", "TABLE", AGG_STAGE], ["INSERT", "INTO", AGG_STAGE], ["ALTER", "TABLE", AGG_TABLE],
                                                 ["ALTER", "TABLE", AGG_STAGE], ["DELETE", "FROM", AGG_PENDING]], step
    assert "REPLACE PARTITION ID '20261016' FROM dmp.events_daily_agg_stage" in step[3] and "FROM dmp.events FINAL WHERE event_date = toDate('2026-10-16')" in step[2]
    assert step[5] == f"DELETE FROM {AGG_PENDING} WHERE event_date = toDate('2026-10-16')" and len(ch.sql) == 1 + 6 * 2    # 標記在 REPLACE 成功之後才刪
    ch.sql.clear(); assert mark_pending(ch, ["2026-10-16", "2026-10-16", "2026-10-17"], "b'1") == ["2026-10-16", "2026-10-17"]
    assert ch.sql == [f"INSERT INTO {AGG_PENDING} (event_date, batch_id) VALUES (toDate('2026-10-16'), 'b\\'1'), (toDate('2026-10-17'), 'b\\'1')"], ch.sql
    ch.pending = ["2026-10-01"]; ch.sql.clear()
    assert rebuild(ch, []) == ["2026-10-01"] and rebuild(ch, ["2026-10-02"], with_pending=False) == ["2026-10-02"]            # 這批沒有要重建的也會補做上次中斷的
    assert dedupe(ch, []) == DedupeResult(rows=[])
    print("aggregates self-test ok: dedupe(full-version same/changed/new/in-batch dup/sort-key change → stale)/purge_stale/event_date/"
          "rebuild(pending ∪ dates → mark → stage → replace partition → drop stage → unmark)")


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
