"""神策資料格式（FormatImporter／批量導入工具的 JSON lines）→ DMP（docs/22 §3.3、docs/23 §4.1）。

客戶線下每日約 3.8 GB 的神策格式檔（track／track_signup／profile_*／item_set 各一行一筆 JSON）本來餵 FormatImporter，
現在直接餵本模組；神策歷史的 HDFS 導出也能先轉成同一格式。

流程（每個檔案一批）：
  1. Python 串流預處理 stage()：$is_login_id 為真的 distinct_id → token_v1 客戶代碼（金鑰不進 ClickHouse）、丟 $ip 與 L4 欄位、
     壞行進隔離檔；每行只做必要的 JSON 解析（3.8 GB／約 1,000 萬行，單核數分鐘）
  2. ClickHouse 讀檔 load_raw()：file(JSONEachRow) → dmp.raw_sensors（properties 以字串保存，ClickHouse 解析 JSON 遠快於 Python）
  3. normalize SQL：track → dmp.events（事件名別名轉正式名、event_id 去重鍵、常用屬性物化欄位、其餘進 props JSON）
                     profile_set／set_once／increment／unset → dmp.user_prop_log（微批合併進 users）
                     track_signup（IDM 2.0 匿名 → 登入綁定）→ dmp.id_map
  4. 對帳：行數 → 預處理通過 → raw → events／prop_log／id_map
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import modeling
from .runner import ClickHouseHTTP

ROOT = Path(__file__).resolve().parents[1]
DROP_PROPS = {"$ip", "$ip_isp", "$ip_country", "$latitude", "$longitude"}   # 不入倉
PROFILE_TYPES = {"profile_set": "set", "profile_set_once": "set_once", "profile_increment": "increment", "profile_unset": "unset", "profile_append": "append"}

RAW_DDL = """CREATE TABLE IF NOT EXISTS dmp.raw_sensors
(
    batch_id     LowCardinality(String),
    type         LowCardinality(String),
    event        String,
    distinct_id  String,
    is_login     UInt8,
    original_id  String,
    time         Int64,
    project      LowCardinality(String),
    properties   String,
    line_no      UInt64,
    ingested_at  DateTime DEFAULT now()
)
ENGINE = {engine}
PARTITION BY batch_id
ORDER BY (batch_id, type, line_no)
TTL ingested_at + INTERVAL 7 DAY          -- 以載入時間計，不能用事件時間（歷史檔一載入就會被 TTL 清掉，e2e 實測）"""


def sql_list(xs: Iterable[str]) -> str:
    return "[" + ", ".join("'" + x.replace("'", "\\'") + "'" for x in xs) + "]"


def alias_lists() -> tuple[list[str], list[str]]:
    ev = yaml.safe_load((ROOT / "semantic" / "events.yaml").read_text(encoding="utf-8"))["events"]
    pairs = [(a, e["name"]) for e in ev for a in (e.get("aliases") or [])]
    return [a for a, _ in pairs], [c for _, c in pairs]


@dataclass
class StageResult:
    lines: int = 0
    ok: int = 0
    bad: int = 0
    tokenized: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    bad_samples: list[str] = field(default_factory=list)


def stage(src: Path, dst: Path, quarantine: Path, key_ref: str = "k_idm", forbidden: Iterable[str] = ()) -> StageResult:
    """神策格式檔 → 已代碼化、去個資的 JSON lines（一行一筆，欄位攤平成 raw_sensors 的欄位）。"""
    r = StageResult(); forbidden = set(forbidden)
    with src.open(encoding="utf-8") as f, dst.open("w", encoding="utf-8") as out, quarantine.open("w", encoding="utf-8") as q:
        for n, line in enumerate(f, 1):
            r.lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                typ = d["type"]; did = str(d["distinct_id"]); props = d.get("properties") or {}
                if typ.startswith("track") and ("event" not in d or "time" not in d):
                    raise ValueError("track 缺 event/time")
            except (ValueError, KeyError, TypeError) as e:
                r.bad += 1
                if len(r.bad_samples) < 5: r.bad_samples.append(f"line {n}: {e}")
                q.write(json.dumps({"line": n, "reason": str(e), "raw": line[:500]}, ensure_ascii=False) + "\n"); continue
            is_login = bool(props.get("$is_login_id")) or typ == "track_signup"
            if is_login:
                did = modeling.apply_transform(f"token_v1({key_ref})", did); r.tokenized += 1
            for k in list(props):
                if k in DROP_PROPS or k in forbidden:
                    props.pop(k)
            rec = {"type": typ, "event": d.get("event", ""), "distinct_id": did, "is_login": int(is_login),
                   "original_id": str(d.get("original_id", "")), "time": int(d.get("time") or 0), "project": d.get("project", "default"),
                   "properties": json.dumps(props, ensure_ascii=False, separators=(",", ":")), "line_no": n}
            out.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            r.ok += 1; r.by_type[typ] = r.by_type.get(typ, 0) + 1
    return r


class SensorsLoader:
    def __init__(self, ch: ClickHouseHTTP, project: str = "altatech_dmp", local: bool = False):
        self.ch, self.project = ch, project
        self.ch.query(RAW_DDL.format(engine="MergeTree" if local else "ReplicatedMergeTree('/ch/{shard}/dmp.raw_sensors', '{replica}')"))

    SCHEMA = "type String, event String, distinct_id String, is_login UInt8, original_id String, time Int64, project String, properties String, line_no UInt64"

    def load_raw(self, staged: Path, batch_id: str, via_http: bool = False) -> int:
        """staged 檔需在 ClickHouse 的 user_files_path 下（正式環境是物件儲存：file() 換 s3()）；
        via_http：伺服器不在本機（CI 服務容器）時把檔案當 INSERT 本體送、以 input() 補 batch_id。"""
        cols = "batch_id, type, event, distinct_id, is_login, original_id, time, project, properties, line_no"
        sel = f"SELECT '{batch_id}', type, event, distinct_id, is_login, original_id, time, project, properties, line_no"
        st = {"max_insert_threads": 4, "input_format_json_read_objects_as_strings": 1}
        if via_http:
            self.ch.query(f"INSERT INTO dmp.raw_sensors ({cols}) {sel} FROM input('{self.SCHEMA}') FORMAT JSONEachRow", data=staged.read_bytes(), settings=st)
        else:
            self.ch.query(f"INSERT INTO dmp.raw_sensors ({cols}) {sel} FROM file('{staged.name}', 'JSONEachRow', '{self.SCHEMA}')", settings=st)
        return self.ch.scalar(f"SELECT count() FROM dmp.raw_sensors WHERE batch_id = '{batch_id}'")

    def normalize(self, batch_id: str) -> dict[str, int]:
        aliases, canon = alias_lists()
        ev = f"transform(event, {sql_list(aliases)}, {sql_list(canon)}, event)" if aliases else "event"
        self.ch.query(f"""INSERT INTO dmp.events (project, time, event, source, one_id, entity_type, entity_id, distinct_id, session_id, event_id, platform, app_id, symbol, side, channel, amount, utm_source, utm_campaign, props, ingest_time, schema_version, late, batch_id)
SELECT '{self.project}', fromUnixTimestamp64Milli(time), {ev}, 'sensors_format', distinct_id, '', '', distinct_id,
  JSONExtractString(properties, '$session_id'),
  substring(lower(hex(SHA256(concat(distinct_id, '|', toString(time), '|', event, '|', JSONExtractString(properties, '$track_id'))))), 1, 32),
  JSONExtractString(properties, '$lib'), JSONExtractString(properties, '$app_id'),
  JSONExtractString(properties, 'symbol'), JSONExtractString(properties, 'side'), JSONExtractString(properties, 'channel'),
  JSONExtract(properties, 'amount', 'Nullable(Float64)'), JSONExtractString(properties, '$utm_source'), JSONExtractString(properties, '$utm_campaign'),
  CAST(properties, 'JSON'), now64(3), 1, 0, '{batch_id}'
FROM dmp.raw_sensors WHERE batch_id = '{batch_id}' AND type = 'track'""", settings={"max_insert_threads": 4})
        self.ch.query(f"""INSERT INTO dmp.user_prop_log (one_id, prop, value, op, ts)
SELECT distinct_id, kv.1, kv.2, transform(type, {sql_list(PROFILE_TYPES)}, {sql_list(PROFILE_TYPES.values())}, 'set'), fromUnixTimestamp64Milli(time)
FROM dmp.raw_sensors ARRAY JOIN JSONExtractKeysAndValuesRaw(properties) AS kv
WHERE batch_id = '{batch_id}' AND type LIKE 'profile_%' AND is_login = 1""")
        self.ch.query(f"""INSERT INTO dmp.id_map (id_type, id_hash, one_id, confidence, version, updated_at)
SELECT '$identity_anonymous_id', original_id, distinct_id, 1.0, toUInt32(time / 1000), fromUnixTimestamp64Milli(time)
FROM dmp.raw_sensors WHERE batch_id = '{batch_id}' AND type = 'track_signup' AND original_id != ''""")
        q = lambda sql: self.ch.scalar(sql)
        return {"raw": q(f"SELECT count() FROM dmp.raw_sensors WHERE batch_id='{batch_id}'"),
                "events": q(f"SELECT count() FROM dmp.events WHERE batch_id='{batch_id}'"),
                "prop_log": q(f"SELECT count() FROM dmp.user_prop_log WHERE one_id IN (SELECT distinct_id FROM dmp.raw_sensors WHERE batch_id='{batch_id}' AND type LIKE 'profile_%')"),
                "id_map": q(f"SELECT count() FROM dmp.id_map FINAL WHERE id_type='$identity_anonymous_id' AND id_hash IN (SELECT original_id FROM dmp.raw_sensors WHERE batch_id='{batch_id}' AND type='track_signup')"),
                "signup_anon_ids": q(f"SELECT uniqExact(original_id) FROM dmp.raw_sensors WHERE batch_id='{batch_id}' AND type='track_signup'")}

    def run_file(self, src: Path, user_files_dir: Path, batch_id: str, key_ref: str = "k_idm", via_http: bool = False) -> dict[str, Any]:
        user_files_dir.mkdir(parents=True, exist_ok=True)
        staged = user_files_dir / f"{src.stem}.staged.jsonl"; quarantine = user_files_dir / f"{src.stem}.quarantine.jsonl"
        up = yaml.safe_load((ROOT / "semantic" / "user_properties.yaml").read_text(encoding="utf-8"))
        st = stage(src, staged, quarantine, key_ref, up.get("forbidden_l4", []))
        raw = self.load_raw(staged, batch_id, via_http=via_http)
        counts = self.normalize(batch_id)
        return {"stage": st.__dict__ | {"bad_samples": st.bad_samples}, "raw": raw, **counts,
                "recon_ok": raw == st.ok and counts["events"] == st.by_type.get("track", 0)}


if __name__ == "__main__":
    import tempfile
    modeling.set_key("k_idm", b"t")
    d = Path(tempfile.mkdtemp()); src = d / "sa.jsonl"
    src.write_text("\n".join([
        json.dumps({"type": "track", "event": "order_submit", "distinct_id": "C001", "time": 1760000000000, "properties": {"$is_login_id": True, "$ip": "1.2.3.4", "symbol": "2330", "amount": 1000, "name": "王小明"}}),
        json.dumps({"type": "track", "event": "$pageview", "distinct_id": "cookie-abc", "time": 1760000001000, "properties": {"$is_login_id": False, "$url": "https://x"}}),
        json.dumps({"type": "profile_set", "distinct_id": "C001", "time": 1760000002000, "properties": {"$is_login_id": True, "lifecycle": "成長", "aum": 123.5}}),
        json.dumps({"type": "track_signup", "event": "$SignUp", "distinct_id": "C001", "original_id": "cookie-abc", "time": 1760000003000, "properties": {}}),
        "{not json", json.dumps({"type": "track", "distinct_id": "x", "properties": {}})]), encoding="utf-8")
    r = stage(src, d / "out.jsonl", d / "q.jsonl", "k_idm", ["name"])
    assert (r.lines, r.ok, r.bad, r.tokenized) == (6, 4, 2, 3), r
    rows = [json.loads(l) for l in (d / "out.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["distinct_id"].startswith("v1_") and rows[1]["distinct_id"] == "cookie-abc" and rows[3]["original_id"] == "cookie-abc"
    p0 = json.loads(rows[0]["properties"]); assert "$ip" not in p0 and "name" not in p0 and p0["symbol"] == "2330"
    assert (d / "q.jsonl").read_text(encoding="utf-8").count("\n") == 2
    a, c = alias_lists(); assert "order_submit" in a and c[a.index("order_submit")] == "bond_order"
    print("sensors_format ok", r.by_type)
