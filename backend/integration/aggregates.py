"""日彙總（dmp.events_daily_agg）的冪等保證（R12）。

問題：dmp.events 是 ReplacingMergeTree，同一 event_id 重插（GA4 重疊窗口重讀、日批覆蓋串流、重寫重載、任務重試）在合併後只留一版；
但餵 events_daily_agg 的 MV 是「每次 INSERT 都累加」countState／sumState／uniqState，底表之後合併掉的重複列，彙總不會跟著回滾。

兩道保證（都在 runner 載入路徑上）：
1. 入倉前去重（dedupe）：先查這批 event_id 在倉裡的最新版本；內容相同（彙總會看的欄位全同）的列不再寫入——重疊窗口重讀、重試
   這類「一模一樣再來一次」直接變成 no-op，MV 自然不會多算。內容不同的列照寫（ReplacingMergeTree 保留新版本），但記下它的分區。
2. 分區重建（rebuild）：對有「不同版本重插」的分區，從 dmp.events FINAL 重算該日彙總到暫存表，再 REPLACE PARTITION 原子換掉——
   彙總回到與去重後底表一致。全量重載（TRUNCATE）後也把彙總表現有的分區全部重建。
3. 重建意圖落地 + 世代協定（events_daily_agg_pending，append-only 日誌）：寫入者在清舊版本／寫入之前先為每個要重建的日期登記一筆
   標記（phase 0，帶租約；每個 chunk 寫入前檢查租約，過期就不准再寫），資料全部落地後再記 phase 1；任何一次重建都把
   「這批的日期 ∪ 還沒被確認（phase 2）的日期」重做：用自己的私有暫存表快照 → REPLACE PARTITION → 若有寫入者在我的快照
   之後才落地就再快照一次 → 只確認「落地時間早於我最後一次快照」或「租約已過期而沒落地」的標記（phase 2）。
   結果：API／worker／CLI／多程序交錯寫入與重建都不會把別人的恢復工作吃掉——沒被確認的標記會讓下一次重建再做一次；
   任何一點中斷（寫入後、清舊版本後、REPLACE 前後、確認前），重試（哪怕整批內容相同、一筆不寫）都會先修好彙總。
   前提：寫入與重建連同一個 ClickHouse 節點（或叢集開 select_sequential_consistency），INSERT 回應後對後續 SELECT 可見。
"""
from __future__ import annotations

import json
import time
import uuid
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
LEASE_S = 600                                        # 寫入者的租約：標記後要在這段時間內寫完（每個 chunk 前檢查、剩一半時續租）；過期不准再寫，重試帶新標記
MARGIN_S = 2                                         # 「落地時間 vs 快照時間」比對的安全餘量（時鐘／可見性）
KEEP_S = 86_400                                      # 已確認的標記保留一天給後到的重建比對，之後清掉
MAX_ROUNDS = 6                                       # 一次重建最多重新快照幾次（每次都有人在我快照後落地時）
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


class LeaseExpired(RuntimeError):
    """寫入者的租約已過期：不准再寫（這批會失敗、由排程重試並帶新標記）；過期的標記之後由任何一次重建確認掉。"""


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("'", "\\'")


def _now(ch: CH) -> str:
    return ch.query("SELECT toString(now64(3, 'UTC')) FORMAT TSV").strip()


def _mark_rows(ch: CH, rows: list[tuple[str, str, int, str, str]], kind: str = "change") -> None:
    """(event_date, id, phase, owner, lease_until) → 標記表；lease_until 只有 phase 0 用，其他填 1970。
    kind：change＝有版本變更、這天一定要重建；touch＝只寫了新事件（MV 自己會加），只在被別人的重建蓋掉時才要補；replace＝某次重建換過這天的分區。"""
    if not rows: return
    vals = ", ".join(f"(toDate('{d}'), '{_esc(i)}', {ph}, '{_esc(o)}', toDateTime64('{lu or '1970-01-01 00:00:00.000'}', 3, 'UTC'), '{kind}')" for d, i, ph, o, lu in rows)
    ch.query(f"INSERT INTO {AGG_PENDING} (event_date, id, phase, owner, lease_until, kind) VALUES {vals}")


@dataclass
class Marks:
    """一個寫入者在標記表裡的身分：要重建的日期（change）與只寫了新事件的日期（touch）各一個 id。
    流程：begin（phase 0 + 租約）→ 每個 chunk 前 check（過期就丟 LeaseExpired、剩一半續租）→ 資料落地後 landed（phase 1）→ overlapped：
    寫入期間有別的重建換過 touch 日期的分區（它的快照可能沒包到我寫的新事件，MV 加上去的又被它蓋掉）→ 這些日期要由我重建。"""
    ch: CH
    owner: str
    ids: dict[str, str]
    touch: dict[str, str] = field(default_factory=dict)
    lease_until: str = ""
    begin_at: str = ""

    def renew(self) -> None:
        if not self.ids and not self.touch: return
        self.lease_until = self.ch.query(f"SELECT toString(now64(3, 'UTC') + INTERVAL {LEASE_S} SECOND) FORMAT TSV").strip()
        _mark_rows(self.ch, [(d, i, 0, self.owner, self.lease_until) for d, i in self.ids.items()])
        _mark_rows(self.ch, [(d, i, 0, self.owner, self.lease_until) for d, i in self.touch.items()], "touch")

    def check(self) -> None:
        if not self.ids and not self.touch: return
        left = float(self.ch.scalar(f"SELECT toDateTime64('{self.lease_until}', 3, 'UTC') - now64(3, 'UTC')") or 0)
        if left < MARGIN_S:
            raise LeaseExpired(f"日彙總標記租約已過期（owner={self.owner}），這批不再寫入；由排程重試")
        if left < LEASE_S / 2:
            self.renew()

    def landed(self) -> None:
        _mark_rows(self.ch, [(d, i, 1, self.owner, "") for d, i in self.ids.items()])
        _mark_rows(self.ch, [(d, i, 1, self.owner, "") for d, i in self.touch.items()], "touch")

    def overlapped(self) -> set[str]:
        """landed 之後呼叫：touch 日期裡，有哪幾天在我 begin 之後（含餘量）被某次重建換過分區 → 由我重建。"""
        if not self.touch: return set()
        ds = ", ".join(f"toDate('{d}')" for d in sorted(self.touch))
        sql = (f"SELECT DISTINCT toString(event_date) FROM {AGG_PENDING} WHERE kind = 'replace' AND event_date IN ({ds}) "
               f"AND at >= toDateTime64('{self.begin_at}', 3, 'UTC') - INTERVAL {MARGIN_S} SECOND FORMAT TSV")
        return {x for x in self.ch.query(sql).split("\n") if x.strip()}

    def settle(self) -> set[str]:
        """landed 之後呼叫（取代單獨的 overlapped）：沒被別人換過分區的 touch 日期，MV 加上去的還在 → 自己確認（phase 2）；
        被換過的日期回給呼叫端重建（那次重建的 ack 會確認它）。在 landed 與 settle 之間中斷的 touch 留著「已落地、未確認」→ 列入待重建，不會遺失。"""
        ov = self.overlapped()
        _mark_rows(self.ch, [(d, i, 2, self.owner, "") for d, i in self.touch.items() if d not in ov], "touch")
        return ov


def begin(ch: CH, dates: Iterable[str], owner: str = "", touch: Iterable[str] = ()) -> Marks:
    """寫入者在清舊版本／寫入之前呼叫：要重建的日期（dates）與這批會寫到的其他日期（touch）各登記 phase 0 標記（帶租約）。
    沒有日期就是空標記（check／landed／overlapped 都是 no-op）。"""
    ds = sorted({str(x)[:10] for x in dates if str(x).strip()})
    ts_ = sorted({str(x)[:10] for x in touch if str(x).strip()} - set(ds))
    m = Marks(ch, owner or "writer", {d: uuid.uuid4().hex for d in ds}, {d: uuid.uuid4().hex for d in ts_})
    if ds or ts_: m.begin_at = _now(ch)
    m.renew()
    return m


def mark_pending(ch: CH, dates: Iterable[str], batch_id: str = "") -> list[str]:
    """舊介面：標記並視為已落地（給直接寫入底表的呼叫端／測試用）。"""
    m = begin(ch, dates, batch_id); m.landed()
    return sorted(m.ids)


def pending_dates(ch: CH) -> list[str]:
    """要重建的日期：change 標記還沒被確認（phase 2）——上次中斷、或別的寫入者還在進行／已落地還沒被重建確認；
    加上 touch 標記：已落地但還沒確認的（寫入者在 landed 之後、settle／重建之前中斷——期間可能被別的重建用舊快照蓋掉），
    或租約過期卻沒落地的（寫到一半掛了）。正常寫入者落地後立刻 settle 自己確認，所以只寫新事件的批次不會每次都觸發重建。"""
    sql = (f"SELECT DISTINCT toString(event_date) FROM (SELECT event_date, id FROM {AGG_PENDING} GROUP BY event_date, id HAVING countIf(phase = 2) = 0 AND "
           "(any(kind) = 'change' OR (any(kind) = 'touch' AND (countIf(phase = 1) > 0 OR maxIf(lease_until, phase = 0) < now64(3, 'UTC'))))) "
           "ORDER BY 1 FORMAT TSV")
    return [x for x in ch.query(sql).split("\n") if x.strip()]


class Rebuild:
    """一天的重建，拆成可單獨呼叫的步驟（測試用來製造交錯）：mark → [snapshot → replace → newer_landed?]* → ack。"""

    def __init__(self, ch: CH, d: str, owner: str = "rebuild") -> None:
        self.ch, self.d, self.owner = ch, str(d)[:10], owner or "rebuild"
        self.pid = self.d.replace("-", "")
        self.id = uuid.uuid4().hex
        self.tmp = ""; self.ts = ""; self.rows = 0

    def mark(self) -> None:
        """自己的標記（phase 0 + 租約）：重建途中斷了，租約過期後由任何一次重建確認；沒斷就自己確認。"""
        lease = self.ch.query(f"SELECT toString(now64(3, 'UTC') + INTERVAL {LEASE_S} SECOND) FORMAT TSV").strip()
        _mark_rows(self.ch, [(self.d, self.id, 0, self.owner, lease)])

    def snapshot(self) -> None:
        """記下快照時間，從 dmp.events FINAL 算進自己的私有暫存表（與 stage 同結構、同分區／排序鍵；Replicated 路徑也跟著換名）。"""
        self.ts = _now(self.ch)
        self.tmp = f"{AGG_STAGE}_{uuid.uuid4().hex[:12]}"
        ddl = self.ch.query(f"SHOW CREATE TABLE {AGG_STAGE} FORMAT TSVRaw").strip().replace(AGG_STAGE, self.tmp)
        self.ch.query(ddl)
        self.ch.query(f"INSERT INTO {self.tmp} " + AGG_SELECT.format(d=self.d))
        self.rows = int(self.ch.scalar(f"SELECT count() FROM {self.tmp} WHERE event_date = toDate('{self.d}')") or 0)

    def replace(self) -> None:
        """整個分區原子換掉；那天已沒有事件（跨日更正把唯一一筆搬走）就 DROP 目標分區（空來源不能 REPLACE）。丟掉私有暫存表，
        再記一筆 replace：寫入期間被我換過分區的寫入者，落地後看得到、會自己補重建（我的快照可能沒包到它寫的新事件）。"""
        if self.rows == 0:
            self.ch.query(f"ALTER TABLE {AGG_TABLE} DROP PARTITION ID '{self.pid}'")
        else:
            self.ch.query(f"ALTER TABLE {AGG_TABLE} REPLACE PARTITION ID '{self.pid}' FROM {self.tmp}")
        self.ch.query(f"DROP TABLE IF EXISTS {self.tmp}")
        _mark_rows(self.ch, [(self.d, uuid.uuid4().hex, 1, self.owner, "")], "replace")

    def newer_landed(self) -> bool:
        """有別的寫入者在我的快照之後（含餘量）才落地 → 我的快照可能沒包到它的資料，要再快照一次。"""
        n = self.ch.scalar(f"SELECT count() FROM {AGG_PENDING} WHERE event_date = toDate('{self.d}') AND phase = 1 AND kind IN ('change', 'touch') "
                           f"AND at >= toDateTime64('{self.ts}', 3, 'UTC') - INTERVAL {MARGIN_S} SECOND")
        return int(n or 0) > 0

    def wait_newer(self) -> float:
        """等伺服器時間越過「在我快照之後落地的最新一筆 + 餘量」再重新快照：否則寫入者剛落地就重建時，每輪快照都還在餘量內，
        永遠確認不了那筆標記（機器越快越容易）。回等了幾秒。"""
        ms = self.ch.scalar(f"SELECT greatest(0, toInt64(toUnixTimestamp64Milli(max(at))) + {int(MARGIN_S * 1000)} - toInt64(toUnixTimestamp64Milli(now64(3, 'UTC')))) "
                            f"FROM {AGG_PENDING} WHERE event_date = toDate('{self.d}') AND phase = 1 AND kind IN ('change', 'touch') "
                            f"AND at >= toDateTime64('{self.ts}', 3, 'UTC') - INTERVAL {MARGIN_S} SECOND")
        wait = min(max(float(ms or 0) / 1000, 0.0) + 0.05, MARGIN_S + 1.0)
        time.sleep(wait)
        return wait

    def ack(self) -> list[str]:
        """只確認：落地時間早於我最後一次快照（減餘量）的標記、租約已過期而沒落地的標記、我自己的標記。其他（進行中、或在我快照後才落地）留著給下一次重建。"""
        t = f"toDateTime64('{self.ts}', 3, 'UTC') - INTERVAL {MARGIN_S} SECOND"
        sql = (f"SELECT id FROM {AGG_PENDING} WHERE event_date = toDate('{self.d}') AND kind IN ('change', 'touch') GROUP BY id HAVING countIf(phase = 2) = 0 AND ("
               f"(countIf(phase = 1) > 0 AND maxIf(at, phase = 1) < {t}) OR (countIf(phase = 1) = 0 AND maxIf(lease_until, phase = 0) < {t}) OR id = '{self.id}') "
               "FORMAT TSV")
        ids = [x for x in self.ch.query(sql).split("\n") if x.strip()]
        _mark_rows(self.ch, [(self.d, i, 2, self.owner, "") for i in ids])
        return ids

    def run(self) -> list[str]:
        self.mark()
        for _ in range(MAX_ROUNDS):
            self.snapshot(); self.replace()
            if not self.newer_landed(): break
            self.wait_newer()                                                     # 下一次快照一定晚於那筆落地 + 餘量 → 這次就能確認它
        return self.ack()


def gc_marks(ch: CH) -> None:
    """超過一天、已經沒用的標記清掉（lightweight DELETE）：已確認的、replace 紀錄。保留一天是為了讓後到的、拿舊快照的重建
    與還在寫的寫入者還看得到「有人在我之後落地／換過分區」。還沒確認的標記（change 或 touch，不管落地沒有）一律不清：那是恢復意圖。"""
    ch.query(f"DELETE FROM {AGG_PENDING} WHERE id IN (SELECT id FROM {AGG_PENDING} GROUP BY id HAVING max(at) < now64(3, 'UTC') - INTERVAL {KEEP_S} SECOND AND "
             "(countIf(phase = 2) > 0 OR any(kind) = 'replace'))")


def rebuild(ch: CH, dates: Iterable[str], batch_id: str = "", with_pending: bool = True) -> list[str]:
    """重算日彙總：這批要重建的日期 ∪ 還沒被確認的日期。每一天跑 Rebuild.run（見類別說明）。回處理過的日期。"""
    todo = {str(x)[:10] for x in dates if str(x).strip()}
    if with_pending:
        todo |= set(pending_dates(ch))
    done = []
    for d in sorted(todo):
        Rebuild(ch, d, batch_id or "rebuild").run(); done.append(d)
    if done:
        gc_marks(ch)
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
        def __init__(self, existing: dict[str, dict[str, Any]]): self.existing, self.sql, self.pending, self.stage_rows, self.newer, self.left = existing, [], [], 1, 0, 600.0
        def query(self, sql: str, data: Any = None, settings: Any = None) -> str:
            self.sql.append(sql)
            if sql.startswith("SELECT event_id, argMax"):
                return "\n".join(json.dumps({"event_id": k, **v}) for k, v in self.existing.items() if f"'{k}'" in sql)
            if sql.startswith("SELECT DISTINCT toString(event_date) FROM (SELECT event_date, id FROM"):
                return "\n".join(self.pending)
            if sql.startswith("SELECT toString(now64"): return "2026-10-16 00:00:00.000"
            if sql.startswith("SHOW CREATE TABLE"): return f"CREATE TABLE {AGG_STAGE}\n(x UInt8)\nENGINE = MergeTree\nORDER BY x"
            if sql.startswith("SELECT id FROM"): return "own"
            return ""
        def scalar(self, sql: str) -> Any:
            if sql.startswith("SELECT count() FROM dmp.events_daily_agg_stage_"): return self.stage_rows
            if "phase = 1" in sql: return self.newer
            if sql.startswith("SELECT toDateTime64"): return self.left
            return 0
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
    assert ch.sql[0].startswith("SELECT DISTINCT toString(event_date) FROM (SELECT event_date, id FROM"), ch.sql[0]        # 先把還沒確認的日期撈進來
    tmp = [x for x in ch.sql if x.startswith("CREATE TABLE dmp.events_daily_agg_stage_")]; assert len(tmp) == 2 and AGG_STAGE + "\n" not in tmp[0], tmp   # 私有暫存表（換名）
    day = ch.sql[1:]; i = next(k for k, x in enumerate(day) if "REPLACE PARTITION ID '20261016'" in x)
    assert day[i - 5].startswith(f"INSERT INTO {AGG_PENDING}") and "', 0, 'rebuild'" in day[i - 5], day[i - 5]                      # 自己的標記（phase 0 + 租約）在快照之前
    assert day[i - 3].startswith("SHOW CREATE TABLE") and day[i - 2].startswith("CREATE TABLE dmp.events_daily_agg_stage_"), day[i - 3:i]
    assert day[i - 1].startswith("INSERT INTO dmp.events_daily_agg_stage_") and "FROM dmp.events FINAL WHERE event_date = toDate('2026-10-16')" in day[i - 1]
    assert day[i + 1].startswith("DROP TABLE IF EXISTS dmp.events_daily_agg_stage_") and day[i + 2].startswith(f"INSERT INTO {AGG_PENDING}") and day[i + 2].endswith("'replace')")
    assert day[i + 3].startswith("SELECT id FROM") and "countIf(phase = 2) = 0" in day[i + 3] and "kind IN ('change', 'touch')" in day[i + 3]
    assert day[i + 4].startswith(f"INSERT INTO {AGG_PENDING}") and "'own', 2, 'rebuild'" in day[i + 4], day[i + 4]                   # 確認＝補 phase 2 列，不刪別人的
    assert ch.sql[-1].startswith(f"DELETE FROM {AGG_PENDING} WHERE id IN (SELECT id FROM {AGG_PENDING} GROUP BY id HAVING max(at) <") and "any(kind) = 'replace'" in ch.sql[-1], ch.sql[-1]
    ch.pending = ["2026-10-01"]; ch.sql.clear()
    assert rebuild(ch, []) == ["2026-10-01"] and rebuild(ch, ["2026-10-02"], with_pending=False) == ["2026-10-02"]                 # 這批沒有要重建的也會補做沒確認的
    ch.stage_rows = 0; ch.sql.clear(); rebuild(ch, ["2026-10-03"], with_pending=False)
    assert any(x == f"ALTER TABLE {AGG_TABLE} DROP PARTITION ID '20261003'" for x in ch.sql) and not any("REPLACE" in x for x in ch.sql), ch.sql   # 那天沒事件 → 丟掉目標分區
    ch.stage_rows = 1; ch.newer = 1; ch.sql.clear(); rebuild(ch, ["2026-10-04"], with_pending=False)
    assert sum(1 for x in ch.sql if "REPLACE PARTITION ID '20261004'" in x) == MAX_ROUNDS, "有人在我快照後落地 → 再快照，直到沒有（上限 MAX_ROUNDS）"
    ch.newer = 0
    m = begin(ch, ["2026-10-16", "2026-10-16", "2026-10-17"], "b'1"); assert sorted(m.ids) == ["2026-10-16", "2026-10-17"] and len(set(m.ids.values())) == 2
    assert "', 0, 'b\\'1', toDateTime64('2026-10-16 00:00:00.000'" in ch.sql[-1], ch.sql[-1]                                          # phase 0 + 租約
    m.check(); ch.sql.clear(); m.landed(); assert ch.sql[-1].count(", 1, 'b\\'1', toDateTime64('1970-01-01") == 2, ch.sql[-1]          # phase 1
    ch.left = 1.0
    try: m.check(); raise AssertionError("租約過期要擋")
    except LeaseExpired: pass
    ch.left = 100.0; ch.sql.clear(); m.check(); assert ch.sql and "', 0, 'b\\'1'" in ch.sql[-1], "剩不到一半 → 續租"
    assert begin(ch, [], "x").ids == {} and mark_pending(ch, ["2026-10-20"], "t") == ["2026-10-20"]
    ch.sql.clear(); mt = begin(ch, ["2026-10-16"], "w", touch=["2026-10-16", "2026-10-18"]); assert sorted(mt.touch) == ["2026-10-18"] and mt.begin_at   # change 已含的日期不重複 touch
    assert ch.sql[-1].endswith("'touch')") and ch.sql[-2].endswith("'change')"), ch.sql[-2:]
    mt.landed(); ch.sql.clear(); assert mt.overlapped() == set() and "kind = 'replace' AND event_date IN (toDate('2026-10-18'))" in ch.sql[0], ch.sql
    ch.sql.clear(); assert mt.settle() == set() and ch.sql[-1].startswith(f"INSERT INTO {AGG_PENDING}") and f"'{mt.touch['2026-10-18']}', 2, 'w'" in ch.sql[-1] and ch.sql[-1].endswith("'touch')"), ch.sql   # 沒被換過分區 → 自己確認 touch
    ch.sql.clear(); pending_dates(ch); assert "any(kind) = 'touch' AND (countIf(phase = 1) > 0 OR maxIf(lease_until, phase = 0) <" in ch.sql[0], ch.sql[0]   # 已落地未確認的 touch 算待重建
    ch.sql.clear(); gc_marks(ch); assert "countIf(phase = 1) > 0" not in ch.sql[0], "已落地未確認的 touch 不能被清掉"
    assert begin(ch, [], "x").overlapped() == set()
    assert dedupe(ch, []) == DedupeResult(rows=[])
    print("aggregates self-test ok: dedupe(full-version same/changed/new/in-batch dup/sort-key change → stale)/purge_stale/event_date/"
          "rebuild(pending ∪ dates → own mark → private stage → replace partition → re-snapshot while newer landed → ack landed-before-snapshot/expired)/marks(change+touch, lease check, renew, landed, overlapped replace, settle self-ack, landed-unacked touch stays pending/not gc'd)")


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
