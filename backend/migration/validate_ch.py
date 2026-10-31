"""遷移資料校驗 L3 事件層／L4 欄位層（#53，docs/42 §3）：參考實作的預期事件 vs ClickHouse 實際入庫。

做法：預期事件與實際事件都在 Python 端正規化、算指紋，分批寫進兩張臨時表（{db}._val_exp_*／_val_act_*），
比對全部用 SQL（雙向差集、分組加總），不把千萬筆讀進記憶體；實際事件以 HTTP 串流逐行讀。跑完一定刪表。

- 指紋 = SHA-256(正式事件名|毫秒時間|代碼化用戶|保留屬性的正規 JSON)。正規化只統一數字寫法（1000.0 = 1000、1e20 = 100000000000000000000，
  大整數原樣比），其餘照原樣：ClickHouse JSON 丟掉 null（#48）、把點分鍵展成巢狀，都會被當成差異報出來，不吞掉。
- 管線寫入前會查倉（同內容已入倉就不寫）：預期有、這批沒有的，到事件表其他批次用同一指紋找，找得到計為 already_loaded，找不到才是漏寫。
- 差異樣本最多 20 筆，只放行號、event_id 與不同的屬性鍵名，不放值。
- 用戶屬性（profile op 語意，#43）不在本層。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
BATCH = 50_000
SAMPLES = 20
MATERIALIZED = {"platform": "$lib", "app_id": "$app_id", "symbol": "symbol", "side": "side", "channel": "channel",
                "utm_source": "$utm_source", "utm_campaign": "$utm_campaign"}


def _num(v: Any) -> str:
    d = Decimal(v) if not isinstance(v, Decimal) else v
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def canon(v: Any) -> str:
    """正規 JSON：鍵排序、數字統一寫法、字串原樣（不轉義非 ASCII）。"""
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(str(k), ensure_ascii=False) + ":" + canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))) + "}"
    if isinstance(v, list):
        return "[" + ",".join(canon(x) for x in v) + "]"
    if v is None or isinstance(v, bool):
        return json.dumps(v)
    if isinstance(v, (int, Decimal, float)):
        return _num(Decimal(repr(v)) if isinstance(v, float) else v)
    return json.dumps(v, ensure_ascii=False)


def _h(s: str, n: int = 32) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:n]


def cents(v: Any) -> int | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        return None
    d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def row(event: str, ms: int, user: str, props: dict[str, Any]) -> dict[str, Any]:
    head = f"{event}|{ms}|{user}"
    return {"fp": _h(head + "|" + canon(props)), "k": _h(head, 16), "event": event, "ms": ms, "user": user,
            "kh": {str(k): _h(canon(v), 12) for k, v in props.items()}}


def tpe_date(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, TPE).date().isoformat()


def _stream(ch: Any, sql: str) -> Iterator[dict[str, Any]]:
    """逐行讀 JSONEachRow（不把整個結果讀進記憶體）；查詢失敗直接拋錯。"""
    if not hasattr(ch, "url"):
        yield from (json.loads(x, parse_float=Decimal) for x in ch.query(sql + " FORMAT JSONEachRow").splitlines() if x.strip()); return
    q = urllib.parse.urlencode({"user": ch.user, "password": ch.password, "database": ch.database, "output_format_json_quote_64bit_integers": 0})
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{ch.url}/?{q}", data=(sql + " FORMAT JSONEachRow").encode(), method="POST"), timeout=ch.timeout) as r:
            for line in r:
                if line.strip():
                    yield json.loads(line, parse_float=Decimal)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ClickHouse {e.code}: {e.read().decode()[:300]}") from None


class Checker:
    """一個分片的 L3／L4：add() 收預期事件（與參考實作同一輪串流），run() 讀實際並比對。"""

    def __init__(self, ch: Any, batch_id: str, db: str = "dmp", aliases: dict[str, str] | None = None, replay_batch: str | None = None) -> None:
        self.ch, self.batch, self.db, self.aliases, self.replay = ch, batch_id, db, aliases or {}, replay_batch
        sfx = secrets.token_hex(4)
        self.exp, self.act, self.old = (f"{db}._val_{k}_{sfx}" for k in ("exp", "act", "old"))
        self.buf: list[dict[str, Any]] = []
        self.ch.query(f"CREATE TABLE {self.exp} (line UInt64, fp String, k String, event String, ms Int64, user String, d Date, cents Nullable(Int64), kh Map(String, String)) ENGINE = MergeTree ORDER BY fp")

    def add(self, line: int, rec: dict[str, Any]) -> None:
        r = row(rec["event"], rec["time"], rec["user"], rec["props"])
        self.buf.append({**r, "line": line, "d": tpe_date(rec["time"]), "cents": cents(rec["props"].get("amount"))})
        if len(self.buf) >= BATCH:
            self._flush(self.exp)

    def _flush(self, table: str) -> None:
        self.ch.insert_json(table, self.buf); self.buf = []

    def _q(self, sql: str) -> list[dict[str, Any]]:
        return list(_stream(self.ch, sql))

    def _n(self, sql: str) -> int:
        return int(self._q(sql)[0]["n"])

    def _load_rows(self, table: str, where: str) -> None:
        """事件表的列 → 指紋、物化欄位是否等於 props 原值、金額（分）；這批實際列與倉中舊列都用同一套。"""
        self.ch.query(f"CREATE TABLE {table} (event_id String, fp String, k String, event String, ms Int64, user String, d Date, cents Nullable(Int64), mat_bad Array(String), kh Map(String, String)) ENGINE = MergeTree ORDER BY fp")
        cols = ", ".join(MATERIALIZED)
        for a in _stream(self.ch, f"SELECT event_id, event, toUnixTimestamp64Milli(time) AS ms, distinct_id, toString(event_date) AS d, amount, {cols}, toJSONString(props) AS p FROM {self.db}.events WHERE {where}"):
            props = json.loads(a["p"], parse_float=Decimal)
            bad = [c for c, k in MATERIALIZED.items() if a[c] != _mat(props.get(k))]
            if cents(a["amount"]) != cents(props.get("amount")):
                bad.append("amount")
            self.buf.append({**row(a["event"], int(a["ms"]), a["distinct_id"], props), "event_id": a["event_id"], "d": a["d"], "cents": cents(a["amount"]), "mat_bad": bad})
            if len(self.buf) >= BATCH:
                self._flush(table)
        self._flush(table)

    def _load_actual(self) -> None:
        self._load_rows(self.act, f"batch_id = '{self.batch}'")

    def _load_already(self) -> None:
        """預期有、這批沒有的：到其他批次找同內容（管線寫入前查倉，已有就不寫）。找到的舊列一樣核物化欄位與金額，不能只比 props。"""
        miss = f"SELECT event, ms, user FROM {self.exp} WHERE fp NOT IN (SELECT fp FROM {self.act})"
        self._load_rows(self.old, f"batch_id != '{self.batch}' AND (event, toUnixTimestamp64Milli(time), distinct_id) IN ({miss})")

    def _matched_old(self) -> str:
        """倉中舊列裡真正對上預期指紋的（同指紋只取一筆）。"""
        return f"(SELECT * FROM {self.old} WHERE fp IN (SELECT fp FROM {self.exp}) AND fp NOT IN (SELECT fp FROM {self.act}) ORDER BY event_id LIMIT 1 BY fp)"

    def _loaded(self) -> str:
        """這批新寫入 ∪ 已在倉中：物化欄位、金額、日期都對這個集合核。"""
        return f"(SELECT event_id, fp, event, d, cents, mat_bad FROM {self.act} UNION ALL SELECT event_id, fp, event, d, cents, mat_bad FROM {self._matched_old()})"

    def _diff_keys(self, side: str, other: str, where: str) -> list[dict[str, Any]]:
        """差異樣本：同一事件／時間／用戶在另一邊的屬性雜湊比一比，只回不同的鍵名。"""
        idcol = "line" if side == self.exp else "event_id"
        out = []
        for r in self._q(f"SELECT s.{idcol} AS id, s.kh AS a, o.kh AS b, o.k != '' AS paired FROM {side} AS s LEFT JOIN {other} AS o ON s.k = o.k WHERE {where} ORDER BY s.{idcol} LIMIT 1 BY s.{idcol} LIMIT {SAMPLES}"):
            a, b = r["a"], r["b"] if r["paired"] else {}
            out.append({idcol: r["id"], "keys": sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)) if r["paired"] else "unpaired"})
        return out

    def run(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            self._flush(self.exp); self._load_actual(); self._load_already()
            return self._l3(), self._l4()
        except Exception as e:                       # 失敗要大聲：查詢出錯就是失敗，不回空
            err = {"status": "fail", "error": f"{type(e).__name__}: {str(e)[:300]}"}
            return err, err
        finally:
            for t in (self.exp, self.act, self.old):
                self.ch.query(f"DROP TABLE IF EXISTS {t}")

    def _l3(self) -> dict[str, Any]:
        from .validate import _check
        e, a, o = self.exp, self.act, self.old
        rows, uniq = (int(x) for x in self._q(f"SELECT count() AS r, uniqExact(fp) AS u FROM {e}")[0].values())
        act_n, act_u = (int(x) for x in self._q(f"SELECT count() AS r, uniqExact(fp) AS u FROM {a}")[0].values())
        already = self._n(f"SELECT uniqExact(fp) AS n FROM {e} WHERE fp NOT IN (SELECT fp FROM {a}) AND fp IN (SELECT fp FROM {o})")
        grp = self._q(f"""SELECT ifNull(x.event, y.event) AS event, toString(ifNull(x.d, y.d)) AS d, ifNull(x.n, 0) AS expected, ifNull(y.n, 0) AS actual FROM
  (SELECT event, d, uniqExact(fp) AS n FROM {e} WHERE fp IN (SELECT fp FROM {a}) OR fp NOT IN (SELECT fp FROM {o}) GROUP BY event, d) AS x
  FULL OUTER JOIN (SELECT event, d, count() AS n FROM {a} GROUP BY event, d) AS y ON x.event = y.event AND x.d = y.d
  WHERE expected != actual ORDER BY event, d LIMIT {SAMPLES}""")
        old_names = list(self.aliases)
        lit = "[" + ", ".join("'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'" for x in old_names) + "]"
        alias_left = self._n(f"SELECT count() AS n FROM {a} WHERE has({lit}, event)") if old_names else 0
        bad_date = self._n(f"SELECT count() AS n FROM {self._loaded()} AS y INNER JOIN (SELECT fp, any(d) AS d FROM {e} GROUP BY fp) AS x ON x.fp = y.fp WHERE x.d != y.d")
        checks = [
            _check("L3.per_event_day", not grp, "每組相等", grp, "（正式事件名, 台北日期）預期去重後筆數（扣除已在倉中）vs 實際" if grp else ""),
            _check("L3.dedupe", rows - uniq == rows - act_n - already, rows - uniq, rows - act_n - already, "去重筆數 = 參考實作算的完全重複數（預期列數 − 實際 − 已在倉中）"),
            _check("L3.no_dup_written", act_n == act_u, act_u, act_n, "這批沒有寫入內容完全相同的兩筆"),
            _check("L3.alias_old_names", alias_left == 0, 0, alias_left, "舊事件名在實際入庫為 0"),
            _check("L3.event_date_tpe", bad_date == 0, 0, bad_date, "event_date 依 Asia/Taipei 計（日界 23:59:59.999／00:00:00.000）"),
        ]
        if self.replay:
            n = self._n(f"SELECT count() AS n FROM {self.db}.events WHERE batch_id = '{self.replay}'")
            checks.append(_check("L3.replay_idempotent", n == 0, 0, n, f"同一檔重送（批次 {self.replay}）寫入 0 筆"))
        return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks,
                "counts": {"expected_rows": rows, "expected_unique": uniq, "duplicates": rows - uniq, "actual": act_n, "already_loaded": already}}

    def _l4(self) -> dict[str, Any]:
        from .validate import _check
        e, a, o = self.exp, self.act, self.old
        exp_only = f"s.fp NOT IN (SELECT fp FROM {a}) AND s.fp NOT IN (SELECT fp FROM {o})"
        act_only = f"s.fp NOT IN (SELECT fp FROM {e})"
        n_exp, n_act = self._n(f"SELECT uniqExact(fp) AS n FROM {e} AS s WHERE {exp_only}"), self._n(f"SELECT count() AS n FROM {a} AS s WHERE {act_only}")
        loaded = self._loaded()
        mat = self._q(f"SELECT event_id, mat_bad FROM {loaded} WHERE notEmpty(mat_bad) ORDER BY event_id LIMIT {SAMPLES}")
        n_mat = self._n(f"SELECT count() AS n FROM {loaded} WHERE notEmpty(mat_bad)")
        sums = self._q(f"""SELECT ifNull(x.event, y.event) AS event, toString(ifNull(x.d, y.d)) AS d, toString(ifNull(x.c, 0)) AS expected_cents, toString(ifNull(y.c, 0)) AS actual_cents FROM
  (SELECT event, d, sum(c) AS c FROM (SELECT fp, any(event) AS event, any(d) AS d, any(cents) AS c FROM {e} GROUP BY fp) GROUP BY event, d) AS x
  FULL OUTER JOIN (SELECT event, d, sum(cents) AS c FROM {loaded} GROUP BY event, d) AS y ON x.event = y.event AND x.d = y.d
  WHERE expected_cents != actual_cents ORDER BY event, d LIMIT {SAMPLES}""")
        checks = [
            _check("L4.expected_not_loaded", n_exp == 0, 0, n_exp, "預期有、實際沒有（也不在倉中其他批次）" if n_exp else ""),
            _check("L4.actual_not_expected", n_act == 0, 0, n_act, "實際有、預期沒有：屬性值被改動（null 被丟為 #48、點分鍵被展成巢狀等）或多寫" if n_act else ""),
            _check("L4.materialized", n_mat == 0, 0, n_mat, "物化欄位 ≠ props 原值（含已在倉中的舊列）" if n_mat else ""),
            _check("L4.amount_sum_cents", not sums, "每組相等", sums, "每事件每台北日 Σamount（Decimal，精確到分；實際 = 這批新寫入＋已在倉中）" if sums else ""),
        ]
        samples = {"expected_not_loaded": self._diff_keys(e, a, exp_only) if n_exp else [], "actual_not_expected": self._diff_keys(a, e, act_only) if n_act else [],
                   "materialized": mat}
        return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks, "samples": {k: v for k, v in samples.items() if v},
                "note": "用戶屬性（profile op 語意，#43）不在本層"}


def _mat(v: Any) -> str:
    """物化欄位的預期值：字串原樣、數字照寫法、其餘（缺、null、物件）為空字串。"""
    if isinstance(v, str):
        return v
    if v is None or isinstance(v, (bool, dict, list)):
        return ""
    return _num(v)


def selftest_ch(ch: Any, work: Any) -> None:
    """需要 ClickHouse（e2e 呼叫）：完全重複、同時間不同屬性、大整數差 1、台北日界、別名、重送冪等；竄改後 L3／L4 要失敗且樣本不含原值。"""
    from pathlib import Path

    from ..integration import modeling
    from ..integration.legacy_format import LegacyLoader
    from .validate import validate
    key = modeling.KEYS["k_idm"]; work = Path(work); loader = LegacyLoader(ch, local=True)
    t, eod = 1_792_000_000_000, 1_792_080_000_000 - 1                           # eod = 台北 2026-10-15 23:59:59.999
    base = {"$is_login_id": True, "$lib": "iOS", "symbol": "2330", "channel": "app"}
    rows = [{"type": "track", "event": "bond_order", "distinct_id": "V001", "time": t, "properties": {**base, "amount": 1000.5}},
            {"type": "track", "event": "bond_order", "distinct_id": "V001", "time": t, "properties": {**base, "amount": 1000.5}},   # 完全重複 → 1 筆
            {"type": "track", "event": "bond_order", "distinct_id": "V001", "time": t, "properties": {**base, "amount": 2000}},     # 同時間不同屬性 → 另一筆
            {"type": "track", "event": "bond_order", "distinct_id": "V002", "time": t + 1, "properties": {**base, "n": 9007199254740992}},
            {"type": "track", "event": "bond_order", "distinct_id": "V002", "time": t + 1, "properties": {**base, "n": 9007199254740993}},
            {"type": "track", "event": "$pageview", "distinct_id": "ck-v", "time": eod, "properties": {"$is_login_id": False, "$lib": "js"}},
            {"type": "track", "event": "$pageview", "distinct_id": "ck-v", "time": eod + 1, "properties": {"$is_login_id": False, "$lib": "js"}},
            {"type": "track", "event": "order_submit", "distinct_id": "V003", "time": t + 2, "properties": {**base, "amount": 0.1}}]

    def load(name: str, rs: list[dict[str, Any]], batch: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        src = work / f"{name}.jsonl"; src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rs) + "\n", encoding="utf-8")
        st = loader.run_file(src, work / "uf_val", batch, "k_idm", via_http=True)["stage"]
        return src, st, validate(src, st, batch, key, ch=ch, batch_id=batch)

    src, st, r = load("val_ch", rows, "val_ch_1")
    L3, L4 = r["layers"]["L3"], r["layers"]["L4"]
    assert L3["status"] == "pass" and L4["status"] == "pass", (L3, L4)
    assert L3["counts"] == {"expected_rows": 8, "expected_unique": 7, "duplicates": 1, "actual": 7, "already_loaded": 0}, L3["counts"]
    days = ch.query("SELECT toString(event_date) FROM dmp.events WHERE batch_id = 'val_ch_1' AND event = '$pageview' ORDER BY time FORMAT TSV").split()
    assert days == ["2026-10-15", "2026-10-16"], days                                                                      # 台北日界分兩天
    loader.run_file(src, work / "uf_val", "val_ch_1r", "k_idm", via_http=True)                                             # 整檔重送
    r = validate(src, st, "val_ch_1", key, ch=ch, batch_id="val_ch_1", replay_batch="val_ch_1r")
    assert r["layers"]["L3"]["status"] == "pass" and any(c["id"] == "L3.replay_idempotent" and c["ok"] for c in r["layers"]["L3"]["checks"])
    # 倉內金額被改掉（props 不變）：原批次與重送批次都要失敗——重送批次的列全是「已在倉中」，舊列也要核物化欄位與金額（Codex #116）
    eid = ch.query("SELECT event_id FROM dmp.events WHERE batch_id = 'val_ch_1' AND amount = 1000.5 FORMAT TSV").strip()
    ch.query(f"ALTER TABLE dmp.events UPDATE amount = 999 WHERE event_id = '{eid}' SETTINGS mutations_sync = 2")
    for b in ("val_ch_1", "val_ch_1r"):
        r = validate(src, st, b, key, ch=ch, batch_id=b)
        c4 = {c["id"]: c for c in r["layers"]["L4"]["checks"]}
        assert not c4["L4.materialized"]["ok"] and not c4["L4.amount_sum_cents"]["ok"], (b, c4)
        assert r["layers"]["L4"]["samples"]["materialized"] == [{"event_id": eid, "mat_bad": ["amount"]}], (b, r["layers"]["L4"]["samples"])
        assert "99900" not in json.dumps(r) and "100050" not in json.dumps(r), "樣本不可帶單筆金額"
    assert r["layers"]["L3"]["counts"]["actual"] == 0 and r["layers"]["L3"]["counts"]["already_loaded"] == 7, r["layers"]["L3"]["counts"]
    ch.query(f"ALTER TABLE dmp.events UPDATE amount = 1000.5 WHERE event_id = '{eid}' SETTINGS mutations_sync = 2")
    assert validate(src, st, "val_ch_1r", key, ch=ch, batch_id="val_ch_1r")["layers"]["L4"]["status"] == "pass"
    # 竄改：刪掉大整數那筆、再寫回一筆 n 被改掉的 → L3 該組少一筆、L4 兩向都有差異，樣本只有行號／event_id 與鍵名
    big = json.loads(ch.query("SELECT * EXCEPT (event_date, props), toJSONString(props) AS p FROM dmp.events WHERE batch_id = 'val_ch_1' AND toJSONString(props) LIKE '%9007199254740993%' FORMAT JSONEachRow",
                              settings={"output_format_json_quote_64bit_integers": 0}))
    ch.query(f"ALTER TABLE dmp.events DELETE WHERE event_id = '{big['event_id']}' SETTINGS mutations_sync = 2")
    r = validate(src, st, "val_ch_1", key, ch=ch, batch_id="val_ch_1")
    c3 = {c["id"]: c for c in r["layers"]["L3"]["checks"]}; c4 = {c["id"]: c for c in r["layers"]["L4"]["checks"]}
    assert not c3["L3.per_event_day"]["ok"] and c4["L4.expected_not_loaded"]["actual"] == 1, (c3, c4)
    assert r["layers"]["L4"]["samples"]["expected_not_loaded"] == [{"line": 5, "keys": ["n"]}], r["layers"]["L4"]["samples"]
    props = json.loads(big.pop("p")); props["n"] = 1
    ch.insert_json("dmp.events", [{**big, "props": props}])
    r = validate(src, st, "val_ch_1", key, ch=ch, batch_id="val_ch_1")
    s4 = r["layers"]["L4"]["samples"]
    assert r["layers"]["L4"]["status"] == "fail" and s4["actual_not_expected"] == [{"event_id": big["event_id"], "keys": ["n"]}], s4
    blob = json.dumps(r, ensure_ascii=False)
    assert "9007199254740993" not in blob and "V001" not in blob and "2330" not in blob, "報告不可含原值"
    # #48：null 入 JSON 欄後被丟掉 → 要被報出來，不能吞掉
    _, _, r = load("val_null", [{"type": "track", "event": "bond_order", "distinct_id": "V009", "time": t + 9, "properties": {**base, "x": None}}], "val_ch_null")
    assert r["layers"]["L4"]["status"] == "fail" and r["layers"]["L4"]["samples"]["actual_not_expected"][0]["keys"] == ["x"], r["layers"]["L4"]
    print("validate L3/L4 (ClickHouse) ok: 完全重複 1 筆、同時間不同屬性 2 筆、大整數差 1 各一筆、台北日界分兩天、別名、重送 0 筆；"
          "刪一筆 → L3 分組與 L4 預期未入庫（樣本行號 5、鍵 n）；改值 → L4 實際非預期（鍵 n）；null 被丟（#48）被報出；倉內金額被改 → 原批次與重送批次都失敗（樣本只有 event_id）")
