"""舊平台遷移資料校驗（#53，docs/42）：新規則清洗後入庫，逐層核對每一行的去處、key、條數與非個資欄位。

本檔是校驗工具的第一段：
- 參考實作：依屬性字典與規則表，從原始檔獨立算出「應該是什麼樣子」。不 import 管線程式（legacy_format／modeling），
  只讀規則：字典 csv、user_properties.yaml、events.yaml、個資樣式表（transforms.PII_PATTERNS 是規則表，不是管線流程）。
  管線與參考實作同時寫錯的機率很低（docs/42 §1 第 2 點）。
- L1 檔案層：原始檔每一行恰好落入一個去處（空行／壞行／個資 ID 隔離／events／id_map／user_prop_log／明確略過），
  和管線的預處理報告逐項比對；沒有去處的類型（匿名 profile、item_set…）不自行決定丟或留，直接判失敗（docs/42 §8 第 5 點待客戶決定），
  客戶決定略過時以 --skip-type 明確列出，照樣計數。
- L5 的「丟棄報告」：被丟的屬性鍵與次數，參考實作 vs 管線。
- 報告：分片、原始檔大小與 SHA-256、規則版本（規則檔內容雜湊）、各層結果、差異樣本（只放行號），不含任何原值。
  還沒實作的層標 not_run，判定為 incomplete，不會被當成通過（L2、L5 其餘項目在後續 PR）。
- L3 事件層／L4 欄位層對 ClickHouse 的比對在 validate_ch.py；給 ch 與 batch_id 才跑（CLI：--clickhouse-url、--batch-id、--replay-batch-id）。

用法：
  DMP_K_IDM=… python3 -m backend.migration.validate run --file sa_20261016.jsonl --stage-report stage.json --shard sa_20261016 [--skip-type item_set] [--out report.json]
  python3 -m backend.migration.validate selftest
stage.json = LegacyLoader.run_file() 回傳的 "stage"（lines／ok／bad／pii_rows／pii_dropped／by_type）。
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from ..integration.transforms import PII_PATTERNS  # 規則表：身分證／手機／email 樣式

ROOT = Path(__file__).resolve().parents[1]
RULE_FILES = ("migration/legacy_properties.csv", "semantic/user_properties.yaml", "semantic/events.yaml")
DROP_DECISIONS = ("drop_l4", "drop_ops", "hash")                                  # docs/28：個資、維護欄、可識別個人的代碼都不入庫
IP_GEO = {"$ip", "$ip_isp", "$ip_country", "$latitude", "$longitude"}              # docs/22 §3.1：IP 與地理座標不入倉
PROFILE_OPS = {"profile_set": "set", "profile_set_once": "set_once", "profile_increment": "increment", "profile_unset": "unset", "profile_append": "append"}
MAX_DEPTH = 16
SAMPLES = 20
_PATTERNS = tuple(PII_PATTERNS.values())


@dataclass
class Rules:
    drop_names: set[str]            # 正規化（去 $、小寫）後的丟棄鍵名
    exact_drop: set[str]            # 原樣比對的丟棄鍵（IP／地理）
    aliases: dict[str, str]         # 舊事件名 → 正式名
    version: str                    # 規則檔內容雜湊：規則改版後報告看得出來


def load_rules(root: Path = ROOT) -> Rules:
    with (root / RULE_FILES[0]).open(encoding="utf-8-sig", newline="") as f:
        dict_drop = {r["property"] for r in csv.DictReader(f) if r["decision"] in DROP_DECISIONS}
    up = yaml.safe_load((root / RULE_FILES[1]).read_text(encoding="utf-8"))
    ev = yaml.safe_load((root / RULE_FILES[2]).read_text(encoding="utf-8"))["events"]
    h = hashlib.sha256()
    for p in RULE_FILES:
        h.update(p.encode() + b"\0" + (root / p).read_bytes())
    names = {_norm(x) for x in dict_drop | set(up.get("forbidden_l4") or [])}
    return Rules(names, set(IP_GEO), {a: e["name"] for e in ev for a in (e.get("aliases") or [])}, h.hexdigest()[:16])


def _norm(k: Any) -> str:
    return str(k).lstrip("$").lower()


def looks_pii(s: str) -> bool:
    t = unicodedata.normalize("NFKC", s)
    return any(p.search(t) for p in _PATTERNS)


def _name_hit(k: Any, names: set[str]) -> bool:
    return _norm(k) in names or any(_norm(seg) in names for seg in str(k).split(".") if seg)


def _bad_value(v: Any, names: set[str], depth: int = 0) -> bool:
    """巢狀任一層的鍵名在名單、或任一字串像個資；過深的結構不冒險。"""
    if depth > MAX_DEPTH:
        return True
    if isinstance(v, dict):
        return any(_name_hit(k, names) or _bad_value(x, names, depth + 1) for k, x in v.items())
    if isinstance(v, list):
        return any(_bad_value(x, names, depth + 1) for x in v)
    return isinstance(v, str) and looks_pii(v)


def scrub(props: dict[str, Any], rules: Rules) -> tuple[dict[str, Any], list[str]]:
    keep: dict[str, Any] = {}; dropped: list[str] = []
    for k, v in props.items():
        if k in rules.exact_drop or _name_hit(k, rules.drop_names) or _bad_value(v, rules.drop_names):
            dropped.append(str(k))
        else:
            keep[k] = v
    return keep, dropped


def truthy(v: Any) -> bool:
    """舊平台格式的 $is_login_id 是布林；字串 "false"／"0" 不算登入。"""
    return v is True or (not isinstance(v, bool) and isinstance(v, int) and v == 1) or (isinstance(v, str) and v.strip().lower() in ("true", "1"))


def token(v: str, key: bytes) -> str:
    """客戶代碼（docs/20）：v1_ + HMAC-SHA256 前 20 個 hex。"""
    return "v1_" + hmac.new(key, v.encode(), hashlib.sha256).hexdigest()[:20]


@dataclass
class Outcome:
    line: int
    dest: str                       # blank | bad | pii_id | events | id_map | user_prop_log | no_destination | skipped
    type: str = ""
    record: dict[str, Any] | None = None
    dropped: list[str] = field(default_factory=list)


def expect(path: Path, rules: Rules, key: bytes, skip_types: Iterable[str] = ()) -> Iterator[Outcome]:
    """逐行算出預期去處與預期內容（串流，不把檔案讀進記憶體）。"""
    skip = set(skip_types)
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                yield Outcome(n, "blank"); continue
            try:
                d = json.loads(line, parse_float=Decimal)      # 小數原樣（金額精確到分、浮點誤差不吞掉）
                typ, did = d["type"], d["distinct_id"]
                if not isinstance(typ, str) or not isinstance(did, (str, int)) or isinstance(did, bool):
                    raise ValueError
                if typ.startswith("track") and ("event" not in d or "time" not in d):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                yield Outcome(n, "bad"); continue
            props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
            did, orig = str(did), str(d.get("original_id", ""))
            login = typ == "track_signup" or truthy(props.get("$is_login_id"))
            if (not login and looks_pii(did)) or looks_pii(orig):
                yield Outcome(n, "pii_id", typ); continue
            user = token(did, key) if login else did
            keep, dropped = scrub(props, rules)
            if typ == "track":
                ev = str(d["event"])
                yield Outcome(n, "events", typ, {"event": rules.aliases.get(ev, ev), "time": int(d["time"]), "user": user, "props": keep}, dropped)
            elif typ == "track_signup":
                yield Outcome(n, "id_map", typ, {"anon": orig, "user": user}, dropped)
            elif typ in PROFILE_OPS and login:
                yield Outcome(n, "user_prop_log", typ, {"user": user, "op": PROFILE_OPS[typ], "props": {k: v for k, v in keep.items() if k != "$is_login_id"}}, dropped)
            else:
                yield Outcome(n, "skipped" if typ in skip else "no_destination", typ if typ in PROFILE_OPS or typ in skip else typ or "?", None, dropped)


@dataclass
class RefSummary:
    lines: int = 0
    dest: Counter = field(default_factory=Counter)          # 去處 → 行數
    accepted_by_type: Counter = field(default_factory=Counter)   # 通過預處理的行，依類型
    no_dest_by_type: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)       # 被丟的屬性鍵 → 次數
    samples: dict[str, list[int]] = field(default_factory=dict)


def summarize(outcomes: Iterable[Outcome], sink: Callable[[Outcome], None] | None = None) -> RefSummary:
    s = RefSummary()
    for o in outcomes:
        if sink:
            sink(o)
        s.lines += 1; s.dest[o.dest] += 1
        if o.dest not in ("blank", "bad", "pii_id"):
            s.accepted_by_type[o.type] += 1
            s.dropped.update(o.dropped)
        if o.dest == "no_destination":
            s.no_dest_by_type[o.type] += 1
        if o.dest in ("blank", "no_destination") and len(s.samples.setdefault(o.dest, [])) < SAMPLES:
            s.samples[o.dest].append(o.line)
    return s


def _check(cid: str, ok: bool, expected: Any, actual: Any, detail: str = "") -> dict[str, Any]:
    return {"id": cid, "ok": bool(ok), "expected": expected, "actual": actual, **({"detail": detail} if detail else {})}


def check_l1(ref: RefSummary, stage: dict[str, Any]) -> dict[str, Any]:
    lines, ok, bad = int(stage.get("lines", -1)), int(stage.get("ok", -1)), int(stage.get("bad", -1))
    d = ref.dest
    unaccounted = lines - ok - bad
    checks = [
        _check("L1.lines", lines == ref.lines, ref.lines, lines, "原始行數：參考實作 vs 管線"),
        _check("L1.unaccounted", unaccounted == 0, 0, unaccounted,
               f"管線有 {unaccounted} 行沒有歸類" + (f"（其中空行 {d['blank']} 行，管線未計數；docs/42 V-02）" if d["blank"] else "") if unaccounted else ""),
        _check("L1.bad", bad == d["bad"] + d["pii_id"], d["bad"] + d["pii_id"], bad, "壞行＋個資 ID 隔離"),
        _check("L1.pii_id", int(stage.get("pii_rows", -1)) == d["pii_id"], d["pii_id"], stage.get("pii_rows")),
        _check("L1.by_type", dict(stage.get("by_type") or {}) == dict(ref.accepted_by_type), dict(ref.accepted_by_type), dict(stage.get("by_type") or {})),
        _check("L1.no_destination", not ref.no_dest_by_type, {}, dict(ref.no_dest_by_type),
               "這些行通過預處理但沒有寫進任何表（匿名用戶屬性、item_set 等）：要入庫或明確略過待客戶決定（docs/42 §8 第 5 點），決定略過時以 --skip-type 列出" if ref.no_dest_by_type else ""),
        _check("L1.equation", ref.lines == sum(d.values()), ref.lines, sum(d.values()), "參考實作自身：各去處加總 = 原始行數"),
    ]
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks, "counts": dict(d),
            "samples": {k: v for k, v in ref.samples.items() if v}}


def check_l5_drop_report(ref: RefSummary, stage: dict[str, Any]) -> dict[str, Any]:
    got = {str(k): int(v) for k, v in (stage.get("pii_dropped") or {}).items()}
    want = dict(ref.dropped)
    diff = sorted(set(want) ^ set(got) | {k for k in want if k in got and want[k] != got[k]})
    c = _check("L5.drop_report", not diff, want, got, f"鍵或次數不同：{diff[:SAMPLES]}" if diff else "")
    return {"status": "partial" if c["ok"] else "fail", "checks": [c], "note": "其餘 L5 項目（樣式掃描、原值反查、誤判候選）尚未實作"}


def file_meta(path: Path) -> dict[str, Any]:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": h.hexdigest()}


def validate(path: Path, stage: dict[str, Any], shard: str, key: bytes, rules: Rules | None = None, skip_types: Iterable[str] = (),
             ch: Any = None, batch_id: str | None = None, db: str = "dmp", replay_batch: str | None = None) -> dict[str, Any]:
    """ch＋batch_id 有給才跑 L3／L4（對 ClickHouse）；沒給時兩層維持 not_run。"""
    rules = rules or load_rules()
    l3: dict[str, Any] = {"status": "not_run", "note": "需要 ClickHouse 與 batch_id"}; l4 = dict(l3)
    checker = None
    if ch is not None and batch_id:
        from .validate_ch import Checker
        try:
            checker = Checker(ch, batch_id, db, rules.aliases, replay_batch)
        except Exception as e:                          # 連不上也是失敗，不是「沒有差異」
            l3 = l4 = {"status": "fail", "error": f"{type(e).__name__}: {str(e)[:300]}"}
    sink = (lambda o: checker.add(o.line, o.record) if o.dest == "events" and o.record else None) if checker else None
    ref = summarize(expect(path, rules, key, skip_types), sink)
    if checker:
        l3, l4 = checker.run()
    layers: dict[str, Any] = {"L1": check_l1(ref, stage), "L2": {"status": "not_run"}, "L3": l3, "L4": l4,
                              "L5": check_l5_drop_report(ref, stage), "L6": {"status": "not_run", "note": "L1–L5 通過後另跑 docs/22 T4／T9"}}
    req = [layers[k]["status"] for k in ("L1", "L2", "L3", "L4", "L5")]
    verdict = "fail" if "fail" in req else "pass" if all(s == "pass" for s in req) else "incomplete"
    return {"shard": shard, "file": file_meta(path), "rules_version": rules.version, "skip_types": sorted(set(skip_types)), "layers": layers, "verdict": verdict}


def selftest() -> None:
    import tempfile

    from ..integration import legacy_format, modeling
    key = b"selftest-key"; modeling.set_key("k_idm", key)
    rules = load_rules()
    up = yaml.safe_load((ROOT / RULE_FILES[1]).read_text(encoding="utf-8"))
    forbid = set(up.get("forbidden_l4") or []) | legacy_format.ops_drop_props()
    t = 1_792_000_000_000
    rows = [
        {"type": "track", "event": "order_submit", "distinct_id": "C001", "time": t, "properties": {"$is_login_id": True, "symbol": "2330", "amount": 1000, "$ip": "10.0.0.1", "S_IDNO": "A123456789"}},
        {"type": "track", "event": "$pageview", "distinct_id": "ck-1", "time": t + 1, "properties": {"$is_login_id": False, "note": "手機0912345678請聯絡", "profile.S_ACNO": "98000", "n": 123091234567}},
        "{broken json",
        {"type": "track", "distinct_id": "C002", "properties": {}},                                              # 缺 event／time
        {"type": "track", "event": "$pageview", "distinct_id": "0912345678", "time": t + 2, "properties": {"$is_login_id": False}},
        {"type": "track_signup", "event": "$SignUp", "distinct_id": "C001", "original_id": "ck-1", "time": t + 3, "properties": {}},
        {"type": "profile_set", "distinct_id": "C001", "time": t + 4, "properties": {"$is_login_id": True, "lifecycle": "成長", "name": "王小明"}},
    ]
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows]
    with tempfile.TemporaryDirectory() as d:
        def run(extra: list[str], skip: tuple[str, ...] = ()) -> dict[str, Any]:
            src = Path(d) / "sa.jsonl"; src.write_text("\n".join(lines + extra) + "\n", encoding="utf-8")
            st = legacy_format.stage(src, Path(d) / "s.jsonl", Path(d) / "q.jsonl", "k_idm", forbid)
            return validate(src, st.__dict__, "selftest", key, rules, skip)
        r = run([])
        l1 = r["layers"]["L1"]
        assert l1["status"] == "pass", [c for c in l1["checks"] if not c["ok"]]
        assert l1["counts"] == {"events": 2, "bad": 2, "pii_id": 1, "id_map": 1, "user_prop_log": 1}, l1["counts"]
        assert r["layers"]["L5"]["status"] == "partial", r["layers"]["L5"]
        assert r["verdict"] == "incomplete"                                                  # 未實作的層不能當通過
        assert len(r["file"]["sha256"]) == 64 and r["rules_version"]
        blob = json.dumps(r, ensure_ascii=False)
        for leak in ("A123456789", "0912345678", "王小明", "C001", "98000"):
            assert leak not in blob, leak                                                    # 報告不含原值
        # V-02：空行管線沒有計數 → 未歸類 ≠ 0，判失敗
        r = run([""]); c = {x["id"]: x for x in r["layers"]["L1"]["checks"]}
        assert not c["L1.unaccounted"]["ok"] and "空行 1 行" in c["L1.unaccounted"]["detail"] and r["verdict"] == "fail", c["L1.unaccounted"]
        # V-06／V-07：匿名 profile、item_set 沒有去處 → 失敗；客戶決定略過後明確列出、照樣計數
        extra = [json.dumps({"type": "profile_set", "distinct_id": "ck-9", "time": t, "properties": {"$is_login_id": False, "x": 1}}),
                 json.dumps({"type": "item_set", "distinct_id": "item-1", "time": t, "properties": {"price": 5}})]
        r = run(extra); c = {x["id"]: x for x in r["layers"]["L1"]["checks"]}
        assert c["L1.no_destination"]["actual"] == {"profile_set": 1, "item_set": 1} and r["verdict"] == "fail", c["L1.no_destination"]
        assert r["layers"]["L1"]["samples"]["no_destination"] == [8, 9]
        r = run(extra, ("profile_set", "item_set"))
        assert r["layers"]["L1"]["status"] == "pass" and r["layers"]["L1"]["counts"]["skipped"] == 2, r["layers"]["L1"]
        # 管線報告被改動（少算一行壞行、丟棄次數不符）→ 對應檢查失敗
        src = Path(d) / "sa.jsonl"; src.write_text("\n".join(lines) + "\n", encoding="utf-8")
        st = legacy_format.stage(src, Path(d) / "s.jsonl", Path(d) / "q.jsonl", "k_idm", forbid).__dict__
        bad = validate(src, {**st, "bad": st["bad"] - 1, "ok": st["ok"] + 1}, "x", key, rules)
        assert not {x["id"]: x for x in bad["layers"]["L1"]["checks"]}["L1.bad"]["ok"]
        bad = validate(src, {**st, "pii_dropped": {**st["pii_dropped"], "note": 9}}, "x", key, rules)
        assert bad["layers"]["L5"]["status"] == "fail" and bad["verdict"] == "fail"
        # 字串 "false" 不算登入：參考實作不代碼化
        o = next(expect(_write(Path(d) / "f.jsonl", [{"type": "track", "event": "e", "distinct_id": "ck-2", "time": t, "properties": {"$is_login_id": "false"}}]), rules, key))
        assert o.record and o.record["user"] == "ck-2", o
        # 參考實作的代碼與管線一致（同一金鑰、同一公式）
        assert token("C001", key) == modeling.apply_transform("token_v1(k_idm)", "C001")
    print("validate selftest ok: L1 每行去處（空行未計數、無去處類型、明確略過）、L5 丟棄報告、報告不含原值、未實作層判 incomplete")


def _write(p: Path, rows: list[dict[str, Any]]) -> Path:
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="validate")
    ap.add_argument("cmd", choices=["run", "selftest"])
    ap.add_argument("--file", type=Path); ap.add_argument("--stage-report", type=Path); ap.add_argument("--shard", default="")
    ap.add_argument("--skip-type", action="append", default=[], help="客戶決定明確略過的類型（照樣計數）")
    ap.add_argument("--key-env", default="DMP_K_IDM", help="客戶代碼金鑰所在的環境變數（由 secret 注入）")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--clickhouse-url", help="給了才跑 L3／L4（帳密取 CLICKHOUSE_USER／CLICKHOUSE_PASSWORD）")
    ap.add_argument("--batch-id", help="這個檔載入時的 batch_id")
    ap.add_argument("--replay-batch-id", help="同一檔重送時用的 batch_id：應寫入 0 筆")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        selftest(); return 0
    if not (a.file and a.stage_report):
        ap.error("run 需要 --file 與 --stage-report")
    key = os.environ.get(a.key_env)
    if not key:
        ap.error(f"環境變數 {a.key_env} 未設定（客戶代碼金鑰）")
    ch = None
    if a.clickhouse_url:
        from ..integration.runner import ClickHouseHTTP
        ch = ClickHouseHTTP(a.clickhouse_url, user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=3600)
    rep = validate(a.file, json.loads(a.stage_report.read_text(encoding="utf-8")), a.shard or a.file.stem, key.encode(), skip_types=a.skip_type,
                   ch=ch, batch_id=a.batch_id, replay_batch=a.replay_batch_id)
    out = json.dumps(rep, ensure_ascii=False, indent=2)
    (a.out.write_text(out, encoding="utf-8") if a.out else print(out))
    return 0 if rep["verdict"] != "fail" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
