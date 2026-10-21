"""端到端測試：啟動單機 ClickHouse → 建表 → 合成來源檔 → 排程器跑兩個任務（維度表全量、委託增量三個窗口）
→ 驗證 ClickHouse 內筆數、隔離區、對帳表、水位、依賴 → 以 compile_sql 的 INSERT…SELECT 路徑重算一次比對 Python 路徑。

用法：CLICKHOUSE_BIN=/path/to/clickhouse python3 -m backend.integration.e2e_clickhouse      # 自己啟動單機 ClickHouse
     CLICKHOUSE_URL=http://127.0.0.1:8123 python3 -m backend.integration.e2e_clickhouse   # 連現成的（CI 的 service container）
沒有兩者時只做乾跑（不連 ClickHouse）。最後一段是語意查詢編譯器的黃金測試（semantic/compiler/golden.py）。
"""
from __future__ import annotations

import csv
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from . import modeling
from .runner import ClickHouseHTTP, LocalRunner, local_ddl
from .scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]
HTTP_PORT, TCP_PORT = 18123, 19000


def start_clickhouse(binary: str, workdir: Path, extra: str = "") -> subprocess.Popen:
    cfg = workdir / "config.xml"
    cfg.write_text(f"""<clickhouse>
  <logger><level>warning</level><console>0</console><log>{workdir}/ch.log</log><errorlog>{workdir}/ch.err</errorlog></logger>
  <http_port>{HTTP_PORT}</http_port><tcp_port>{TCP_PORT}</tcp_port><listen_host>127.0.0.1</listen_host>
  <path>{workdir}/data/</path><tmp_path>{workdir}/tmp/</tmp_path><user_files_path>{workdir}/user_files/</user_files_path>
  <mark_cache_size>268435456</mark_cache_size><max_server_memory_usage_to_ram_ratio>0.5</max_server_memory_usage_to_ram_ratio>
  <users><default><password></password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
  <profiles><default><allow_experimental_json_type>1</allow_experimental_json_type></default></profiles><quotas><default/></quotas>
  {extra}
  <named_collections><dmp_dict><host>127.0.0.1</host><port>{TCP_PORT}</port><user>default</user><password></password></dmp_dict></named_collections>
</clickhouse>""")
    p = subprocess.Popen([binary, "server", f"--config-file={cfg}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", HTTP_PORT), timeout=1):
                return p
        except OSError:
            time.sleep(0.5)
    p.kill(); raise RuntimeError("ClickHouse 未在 30 秒內啟動")


def synth(workdir: Path, n_orders: int = 20_000, seed: int = 7) -> dict[str, Path]:
    rnd = random.Random(seed)
    inst = workdir / "instrument_master.csv"
    symbols = [f"{2300 + i}" for i in range(300)]
    with inst.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["STK_CODE", "STK_NAME", "MKT", "IND_NAME", "STK_TYPE", "LOT"])
        for s in symbols:
            w.writerow([s, f"商品{s}", rnd.choice("TTTTO"), rnd.choice(["半導體", "金融", "電子零組件", "航運"]), rnd.choice(["STOCK", "ETF"]), 1000])
        w.writerow(["BAD1", "", "T", "x", "STOCK", 1000])         # required 失敗 → 隔離
        w.writerow(["BAD2", "壞市場", "Z", "x", "STOCK", 1000])   # enum 失敗 → 隔離
    orders = workdir / "fact_order_hist.csv"
    base = datetime(2026, 10, 16, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    rows = []
    for i in range(n_orders):
        t = base + timedelta(seconds=rnd.randint(0, 3 * 3600))
        status = rnd.choices(["NEW", "FILLED", "PARTIAL", "CANCELLED", "REJECTED", "WEIRD"], [40, 30, 10, 10, 8, 2])[0]
        qty = rnd.choice([1000, 2000, 5000, ""]) if status == "NEW" and rnd.random() < 0.02 else rnd.choice([1000, 2000, 5000])
        rows.append({"ORD_NO": f"O{i:07d}", "STATUS": status, "ORD_TIME": t.strftime("%Y-%m-%d %H:%M:%S"), "UPDATED_AT": t.strftime("%Y-%m-%d %H:%M:%S"),
                     "CUST_ID": f"C{rnd.randint(1, 3000):05d}", "ACCT_NO": f"98{rnd.randint(0, 99999999):08d}", "STK_CODE": rnd.choice(symbols),
                     "MKT": rnd.choice("T" * 30 + "O" * 8 + "X"), "BS_FLAG": rnd.choice("BS"), "ORD_TYPE": rnd.choice(["LMT", "MKT"]), "PRICE": rnd.randint(10, 1000),
                     "QTY": qty, "FILL_QTY": qty if status in ("FILLED", "PARTIAL") else "", "CHANNEL": rnd.choice("AAAWS"), "FEE": rnd.randint(0, 500),
                     "TAX": 0, "RJ_CODE": "E12" if status == "REJECTED" else "", "CUST_NAME": "王小明", "ID_NO": "A123456789", "PHONE": "0912345678"})
    rows.sort(key=lambda r: r["UPDATED_AT"])
    with orders.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    return {"instrument": inst, "orders": orders, "orders_rows": rows}


def main() -> int:
    binary, url = os.environ.get("CLICKHOUSE_BIN"), os.environ.get("CLICKHOUSE_URL")
    work = Path(tempfile.mkdtemp(prefix="dmp-e2e-"))
    proc = None
    try:
        files = synth(work)
        modeling.set_key("k_idm", b"e2e-key-idm"); modeling.set_key("k_acct", b"e2e-key-acct")
        ch = None
        if binary or url:
            if binary:
                proc = start_clickhouse(binary, work); url = f"http://127.0.0.1:{HTTP_PORT}"
            ch = ClickHouseHTTP(url, user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=3600)
            ch.query("DROP DATABASE IF EXISTS dmp")       # 現成伺服器（CI）可能殘留上一輪的表
            if not binary:                                 # 現成伺服器：字典來源的 named collection 用 SQL 建（需 named_collection_control）
                ch.query(f"CREATE NAMED COLLECTION IF NOT EXISTS dmp_dict AS host = '127.0.0.1', port = {int(os.environ.get('CLICKHOUSE_TCP_PORT', '9000'))}, user = '{ch.user}', password = '{ch.password}'")
            for st in local_ddl((ROOT / "clickhouse" / "schema.sql").read_text(encoding="utf-8")):
                ch.query(st, settings={"allow_experimental_json_type": 1})
            print("ClickHouse", ch.query("SELECT version()").strip(), "· tables:", ch.query("SELECT count() FROM system.tables WHERE database='dmp'").strip())
        runner = LocalRunner(ch)
        s = Scheduler(work / "sched.db", runner)
        t0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)   # 09:00 台北
        s.add_task("sync_instrument", str(ROOT / "integration" / "models.instrument.yaml"), cron="30 5 * * *", mode="full", retries=2, backoff_s=300,
                   schema_change="pause", source={"kind": "csv", "path": str(files["instrument"])}, now=t0)
        s.add_task("sync_orders", str(ROOT / "integration" / "models.example.yaml"), cron="*/15 * * * *", mode="incremental", depends_on=["sync_instrument"],
                   retries=3, backoff_s=60, schema_change="auto_map", source={"kind": "csv", "path": str(files["orders"]), "incremental_field": "UPDATED_AT"}, now=t0)
        # 09:00 台北 = 01:00 UTC：orders 到期但上游未跑 → 等待；手動跑維度表後，orders 三個窗口各跑一次（來源檔以 UPDATED_AT 水位切增量）
        r = s.tick(t0); assert r == [], r
        r = s.run_now("sync_instrument", t0); assert r["status"] == "success", r
        results = []
        # 模擬來源逐步到資料：先截斷來源檔到每個窗口的時間，再 tick
        all_rows = files["orders_rows"]
        for k, cut in enumerate(["2026-10-16 10:00:00", "2026-10-16 11:00:00", "2026-10-16 12:30:00"]):
            with files["orders"].open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys())); w.writeheader(); w.writerows([x for x in all_rows if x["UPDATED_AT"] <= cut])
            r = s.tick(t0 + timedelta(minutes=15 * (k + 1)))
            assert len(r) == 1 and r[0]["status"] == "success", r
            results.append(s.runs("sync_orders", 1)[0])
        loaded_py = sum(x["rows_loaded"] for x in results); ok_py = sum(x["rows_ok"] for x in results)
        in_total = sum(x["rows_in"] for x in results)
        assert in_total == len(all_rows), (in_total, len(all_rows))                 # 三個增量窗口無重複、無遺漏
        assert all(x["rows_loaded"] == x["rows_ok"] for x in results), [(x["rows_ok"], x["rows_loaded"], x["log"][-300:]) for x in results]   # 三方對帳
        assert results[1]["watermark_from"] == results[0]["watermark_to"]            # 水位接續
        q_total = sum(x["rows_quarantined"] for x in results)
        print(f"orders: in={in_total} ok={ok_py} quarantined={q_total} rejected={sum(x['rows_rejected'] for x in results)} loaded={loaded_py} (3 windows)")
        print("instrument:", {k: s.runs("sync_instrument", 1)[0][k] for k in ("rows_in", "rows_ok", "rows_quarantined", "rows_loaded")})
        if ch is None:
            dbapi_stage(None, work, all_rows)
        if ch:
            n_ev = ch.scalar("SELECT count() FROM dmp.events WHERE source='mdl_order_hist'")
            assert n_ev == loaded_py, (n_ev, loaded_py)
            by_event = ch.query("SELECT event, count() c FROM dmp.events GROUP BY event ORDER BY c DESC FORMAT TSV").strip().replace("\n", " | ")
            print("events by name:", by_event)
            assert ch.scalar("SELECT count() FROM dmp.events WHERE one_id NOT LIKE 'v1_%'") == 0        # 全部代碼化
            assert ch.scalar("SELECT count() FROM dmp.events WHERE event_date NOT BETWEEN toDate('2026-10-15') AND toDate('2026-10-17')") == 0   # 時間欄位沒有飽和到 9999
            assert ch.scalar("SELECT count() FROM dmp.events WHERE props.`$identities`.`$identity_login_id` IS NULL") == 0
            assert ch.scalar("SELECT count() FROM dmp.dim_instrument") == 300
            print("quarantine by reason:", ch.query("SELECT splitByChar(':', reason)[1] r, count() FROM dmp.quarantine GROUP BY r ORDER BY r FORMAT TSV").strip().replace("\n", " | "))
            assert ch.scalar("SELECT count() FROM dmp.quarantine WHERE reason LIKE 'pii:%' AND row != ''") == 0   # 拒絕列不留內容
            assert ch.scalar("SELECT count() FROM dmp.ingest_recon") == 4
            # 關聯屬性：events.symbol = dim_instrument.symbol（docs/06 §7.2）
            joined = ch.scalar("SELECT count() FROM dmp.events e INNER JOIN dmp.dim_instrument d ON e.symbol = d.symbol")
            assert joined == n_ev, (joined, n_ev)
            # 漏斗樣板跑在真資料上（docs/23 §4.3 的 windowFunnel）
            funnel = ch.query("SELECT level, count() FROM (SELECT one_id, windowFunnel(86400)(toDateTime(time), event='bond_order', event='bond_deal') AS level FROM dmp.events GROUP BY one_id) GROUP BY level ORDER BY level FORMAT TSV").strip().replace("\n", " | ")
            print("funnel bond_order→bond_deal (levels):", funnel)
            # compile_sql 路徑：raw 表（已在暫存階段完成代碼化）→ INSERT SELECT，筆數應與 Python 路徑一致
            ch.query("CREATE TABLE dmp.raw_orders (ORD_NO String, STATUS String, ORD_TIME String, UPDATED_AT String, CUST_ID String, ACCT_NO String, STK_CODE String, MKT String, BS_FLAG String, ORD_TYPE String, PRICE String, QTY String, FILL_QTY String, CHANNEL String, FEE String, TAX String, RJ_CODE String) ENGINE = Memory")
            model = modeling.load(ROOT / "integration" / "models.example.yaml")
            staged = []
            for row in all_rows:
                if modeling.T.pii_scan({k: v for k, v in row.items() if k not in ("CUST_NAME", "ID_NO", "PHONE")}): continue
                rr = {k: str(v) for k, v in row.items() if k not in ("CUST_NAME", "ID_NO", "PHONE")}
                rr["CUST_ID"] = modeling.apply_transform("token_v1(k_idm)", row["CUST_ID"]); rr["ACCT_NO"] = modeling.apply_transform("fpe(k_acct)", row["ACCT_NO"])
                staged.append(rr)
            ch.insert_json("dmp.raw_orders", staged)
            ch.query("CREATE TABLE dmp.events_sql AS dmp.events")
            sql = modeling.compile_sql(model, "dmp.raw_orders", batch_id="sqlpath").replace("INSERT INTO dmp.events", "INSERT INTO dmp.events_sql")
            ch.query(sql)
            n_sql = ch.scalar("SELECT count() FROM dmp.events_sql")
            # SQL 路徑不含 enum／range／time_window 清洗（那些在暫存階段）；只比對事件名轉換與必填：預期 = 有合法狀態且必填齊的列
            expect = ch.scalar("SELECT count() FROM dmp.raw_orders WHERE STATUS IN ('NEW','FILLED','PARTIAL','CANCELLED','REJECTED') AND ORD_NO != '' AND STK_CODE != '' AND BS_FLAG != '' AND QTY != ''")
            assert n_sql == expect, (n_sql, expect)
            same_ids = ch.scalar("SELECT count() FROM dmp.events e INNER JOIN dmp.events_sql s ON e.event_id = s.event_id AND e.event = s.event")
            assert same_ids == n_ev, (same_ids, n_ev)   # Python 路徑的每一筆都能在 SQL 路徑找到同 event_id 同事件名
            print(f"compile_sql path: {n_sql} rows (superset before enum/range cleansing); event_id/event match with python path: {same_ids}/{n_ev}")
            legacy_stage(ch, work, int(os.environ.get("E2E_SENSORS_LINES", "200000")), via_http=proc is None)   # 現成伺服器讀不到本機 user_files
            ga4_stage(ch, work, int(os.environ.get("E2E_GA4_PER_TICK", "20000")))
            agg_stage(ch)
            dbapi_stage(ch, work, all_rows)
            if os.environ.get("E2E_USERS", "1") != "0":
                main_users_bench(ch)
            from ..semantic.compiler.golden import compiler_stage  # 語意查詢編譯器：30 條黃金查詢 oracle vs SQL 誤差 0
            print("--- compiler golden")
            compiler_stage(ch)
            from ..api.e2e_stage import api_stage  # REST：/v1/analytics/events 結果 == 直接執行編譯器 SQL
            api_stage(ch)
            from ..segments.e2e_stage import segments_stage  # 分群：建立 → 計算 → 成員 == oracle；版本保留；seg.<id> 最新版本；例行 tick
            segments_stage(ch)
            from ..approvals.e2e_stage import exports_stage  # 匯出：申請 → 審批 → 產檔（遮罩、浮水印）→ 下載
            exports_stage(ch)
        print("status:", json.dumps([{k: x[k] for k in ('id', 'enabled', 'last', 'watermark')} for x in s.status(t0 + timedelta(hours=1))], ensure_ascii=False, default=str)[:600])
        print("alerts:", [a["message"] for a in s.alerts()])
        print("E2E OK")
        return 0
    finally:
        if proc:
            proc.terminate()
            try: proc.wait(10)
            except subprocess.TimeoutExpired: proc.kill()
        shutil.rmtree(work, ignore_errors=True)



# ---------------------------------------------------------------- 舊平台格式檔（FormatImporter 的輸入）→ DMP；MSSQL（DB-API）排程抽取 → DMP
def legacy_stage(ch: ClickHouseHTTP, work: Path, n_lines: int, rnd_seed: int = 3, via_http: bool = False) -> None:
    import time as _t

    from .legacy_format import LegacyLoader
    rnd = random.Random(rnd_seed); src = work / "sa_20261016.jsonl"
    base = 1_792_000_000_000   # 2026-10-15 前後
    n_dup = 0
    with src.open("w", encoding="utf-8") as f:
        for i in range(n_lines):
            cust = f"C{rnd.randint(1, 50000):06d}"; anon = f"ck-{rnd.randint(1, 80000)}"
            kind = rnd.choices(["track_login", "track_anon", "profile_set", "track_signup", "bad", "pii_login", "pii_anon_id", "pii_dotted"], [60, 25, 10, 4, 1, 2, 1, 1])[0]
            if kind == "bad":
                f.write("{broken json\n"); continue
            if kind == "pii_login":     # 舊平台實際會出現的個資：字典 hash 類欄位、自由文字電話、巢狀 email → 都不能入庫
                f.write(json.dumps({"type": "track", "event": "order_submit", "distinct_id": cust, "time": base + i * 10, "project": "sbp_family",
                                    "properties": {"$is_login_id": True, "$lib": "iOS", "$track_id": i, "S_IDNO": "A123456789", "MT_ACNO": "9800012345", "note": "請回電 0912345678",
                                                   "extra": {"contact": {"email": "pii@example.com"}}, "symbol": "2330", "amount": 1000}}, ensure_ascii=False) + "\n"); continue
            if kind == "pii_dotted":    # Codex 複審：點分鍵（頂層／巢狀）與中文緊貼的手機 → ClickHouse JSON 會展成巢狀，不能靠鍵名比對繞過
                f.write(json.dumps({"type": "track", "event": "$pageview", "distinct_id": anon, "time": base + i * 10, "project": "sbp_family",
                                    "properties": {"$is_login_id": False, "$track_id": i, "profile.S_ACNO": "9800012345", "note": "手機0912345678請聯絡",
                                                   "nested": {"profile.MT_ACNO": "9800012345"}, "msg": "身分證A123456789號", "$lib": "js"}}, ensure_ascii=False) + "\n"); continue
            if kind == "pii_anon_id":   # 未登入卻用手機當 distinct_id → 整列隔離
                f.write(json.dumps({"type": "track", "event": "$pageview", "distinct_id": "0912345678", "time": base + i * 10, "project": "sbp_family",
                                    "properties": {"$is_login_id": False, "$lib": "js", "$track_id": i}}) + "\n"); continue
            if kind == "track_login":
                ev = rnd.choice(["order_submit", "order_filled", "bond_order", "hq_stockdetail_view", "quote_view", "sip_purchase"])
                line = json.dumps({"type": "track", "event": ev, "distinct_id": cust, "time": base + i * 10, "project": "sbp_family",
                                   "properties": {"$is_login_id": True, "$lib": "iOS", "$ip": "10.0.0.1", "$track_id": i, "symbol": str(2300 + rnd.randint(0, 299)), "side": rnd.choice(["buy", "sell"]), "amount": rnd.randint(1000, 900000), "channel": "app"}}, ensure_ascii=False) + "\n"
                f.write(line)
                if i % 97 == 0: f.write(line); n_dup += 1        # 同一行重送：時間與屬性全同 → 只算一筆
            elif kind == "track_anon":
                f.write(json.dumps({"type": "track", "event": rnd.choice(["$pageview", "$WebClick", "search_symbol"]), "distinct_id": anon, "time": base + i * 10, "project": "sbp_family",
                                    "properties": {"$is_login_id": False, "$lib": "js", "$url": "https://sbp/x", "$utm_source": rnd.choice(["google", "line", ""]), "$track_id": i}}) + "\n")
            elif kind == "profile_set":
                f.write(json.dumps({"type": "profile_set", "distinct_id": cust, "time": base + i * 10, "properties": {"$is_login_id": True, "lifecycle": rnd.choice(["新客", "成長", "沉睡"]), "aum": rnd.randint(0, 5_000_000), "name": "王小明", "mobile": "0912345678"}}, ensure_ascii=False) + "\n")
            else:
                f.write(json.dumps({"type": "track_signup", "event": "$SignUp", "distinct_id": cust, "original_id": anon, "time": base + i * 10, "properties": {}}) + "\n")
    size_mb = src.stat().st_size / 1048576
    # 改版前入倉的舊列（舊公式 event_id 由 ClickHouse 算：sha256(distinct_id|time|event|JSONExtractString($track_id))，同一台伺服器算才一致）：重放同一行不能再寫一次
    from .legacy_format import LegacyLoader as _LL
    first = next(json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.startswith('{"type": "track"') and '"$is_login_id": true' in x and '"channel": "app"' in x)
    from .legacy_format import alias_lists, ops_drop_props, scrub
    _al, _ca = alias_lists(); _forbid = set(yaml.safe_load((ROOT / "semantic" / "user_properties.yaml").read_text(encoding="utf-8")).get("forbidden_l4", [])) | ops_drop_props()
    first_props, _ = scrub(dict(first["properties"]), _forbid)                       # 改版前入倉的舊列：正式事件名、清理後的屬性（與舊 normalize 寫入的內容相同）
    first_event = _ca[_al.index(first["event"])] if first["event"] in _al else first["event"]
    tok = modeling.apply_transform("token_v1(k_idm)", first["distinct_id"])
    pj = json.dumps(first["properties"], ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'")
    v1 = ch.query(f"SELECT {_LL.V1_ID} FROM (SELECT '{tok}' AS distinct_id, toInt64({first['time']}) AS time, '{first['event']}' AS event, '{pj}' AS properties) FORMAT TSV").strip()
    assert len(v1) == 32 and not v1.startswith("e2_"), v1
    ch.insert_json("dmp.events", [{"project": "altatech_dmp", "time": modeling.ms_to_dt(first["time"]), "event": first_event, "source": "legacy_format", "one_id": tok,
                                   "entity_type": "", "entity_id": "", "distinct_id": tok, "session_id": "", "event_id": v1, "platform": "iOS", "app_id": "", "symbol": "", "side": "", "channel": "",
                                   "amount": None, "utm_source": "", "utm_campaign": "", "props": first_props, "ingest_time": "2026-10-15 00:00:00.000", "schema_version": 1, "late": 0, "batch_id": "pre_v1"}])
    loader = LegacyLoader(ch, local=True)
    t0 = _t.perf_counter(); r = loader.run_file(src, work / "user_files", "sa_20261016", "k_idm", via_http=via_http); dt = _t.perf_counter() - t0
    st = r["stage"]
    assert r["recon_ok"], r
    assert st["bad"] == st["lines"] - st["ok"] and st["bad"] > 0
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND event IN ('order_submit','order_filled','quote_view','search_symbol')") == 0    # 別名已轉正式名
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND one_id LIKE 'C0%'") == 0                                                     # 登入 ID 全部代碼化
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND one_id LIKE 'ck-%'") == ch.scalar("SELECT count() FROM dmp.raw_legacy WHERE batch_id='sa_20261016' AND type='track' AND is_login=0")  # 匿名保留
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND JSONHas(toString(props), '$ip')") == 0                                       # $ip 不入倉
    assert ch.scalar("SELECT count() FROM dmp.user_prop_log WHERE prop IN ('name','mobile')") == 0                                                                  # L4 不入倉
    pii_re = "'\\\\b[A-Z][12][0-9]{8}\\\\b|\\\\b09[0-9]{8}\\\\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+[.]'"                                                     # 身分證／手機／email（與 transforms.PII_PATTERNS 同樣以字界判斷，v1_ 代碼的 hex 不算）
    assert st["pii_rows"] > 0 and {"S_IDNO", "MT_ACNO", "note", "extra", "profile.S_ACNO", "nested", "msg"} <= set(st["pii_dropped"]), (st["pii_rows"], st["pii_dropped"])
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND event = '$pageview' AND JSONHas(toString(props), '$track_id')") > 0           # 非敏感欄照常保留
    for sql in (f"SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND (match(toString(props), {pii_re}) OR match(distinct_id, {pii_re}) OR match(one_id, {pii_re}))",
                f"SELECT count() FROM dmp.raw_legacy WHERE batch_id='sa_20261016' AND (match(properties, {pii_re}) OR match(distinct_id, {pii_re}) OR match(original_id, {pii_re}))",
                f"SELECT count() FROM dmp.user_prop_log WHERE match(value, {pii_re}) OR match(one_id, {pii_re})",
                "SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND (JSONHas(toString(props), 'S_IDNO') OR JSONHas(toString(props), 'MT_ACNO'))",
                "SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND (position(toString(props), '9800012345') > 0 OR JSONHas(toString(props), 'profile') OR JSONHas(toString(props), 'nested'))",
                "SELECT count() FROM dmp.raw_legacy WHERE batch_id='sa_20261016' AND position(properties, '9800012345') > 0"):
        assert ch.scalar(sql) == 0, sql                                                                                                                             # 個資不入庫（含隔離檔以外的所有落地處）
    qf = work / "user_files" / "sa_20261016.quarantine.jsonl"
    assert "0912345678" not in qf.read_text(encoding="utf-8") and "broken json" not in qf.read_text(encoding="utf-8")                                         # 隔離檔只記行號與原因
    assert r["id_map"] == r["signup_anon_ids"], (r["id_map"], r["signup_anon_ids"])   # 同一匿名 ID 多次綁定只留最新（ReplacingMergeTree by (id_type, id_hash)）
    # 事件只追加、內容全同才去重：檔內重複行只寫一次、改版前的舊列不重寫、整檔重放一筆都不寫
    assert r["events_skipped"] == n_dup + 1, (r["events_skipped"], n_dup)
    assert ch.scalar("SELECT count() - uniqExact(event_id) FROM dmp.events WHERE batch_id='sa_20261016'") == 0
    assert ch.scalar(f"SELECT count() FROM dmp.events WHERE distinct_id = '{tok}' AND time = toDateTime64('{modeling.ms_to_dt(first['time'])}', 3, 'UTC')") == 1
    assert ch.scalar("SELECT countIf(NOT startsWith(event_id, 'e2_')) FROM dmp.events WHERE batch_id='sa_20261016'") == 0
    rr = loader.run_file(src, work / "user_files", "sa_20261016_r", "k_idm", via_http=via_http)
    assert rr["recon_ok"] and rr["events"] == 0 and rr["events_skipped"] == st["by_type"]["track"], rr
    by_ev = ch.query("SELECT event, count() FROM dmp.events WHERE batch_id='sa_20261016' GROUP BY event ORDER BY 2 DESC FORMAT TSV").strip().replace("\n", " | ")
    print(f"legacy format: {st['lines']:,} lines / {size_mb:.0f} MB → ok {st['ok']:,} bad {st['bad']} (pii id rows {st['pii_rows']}) pii props dropped {sum(st['pii_dropped'].values()):,} tokenized {st['tokenized']:,} → raw {r['raw']:,} events {r['events']:,} "
          f"(skipped {r['events_skipped']}: {n_dup} duplicate lines + 1 pre-v1 row; full replay wrote 0) prop_log {r['prop_log']:,} id_map {r['id_map']:,} in {dt:.1f}s "
          f"(≈ {3800 / max(size_mb, 1) * dt / 60:.0f} min per 3.8 GB)")
    print("  events by name:", by_ev)
    legacy_v1_collision(ch, loader, work, via_http)
    legacy_bigint(ch, loader, work, via_http)


def legacy_v1_collision(ch: ClickHouseHTTP, loader: Any, work: Path, via_http: bool) -> None:
    """Codex #51 複審 P1：改版前公式的舊 id 只含 distinct_id／時間／事件名／$track_id、不含屬性。舊 id 命中只是候選，
    清理後內容也相同才算同一筆；同 did／時間／事件但金額不同 → 另一筆，兩筆都保留。日彙總與對帳也要對得上。"""
    from .legacy_format import LegacyLoader as _LL
    t_ms, did = 1_792_454_400_000, "synthetic_cookie"                               # 2026-10-19 08:00 台北；未登入（不代碼化）
    v1 = ch.query(f"SELECT {_LL.V1_ID} FROM (SELECT '{did}' AS distinct_id, toInt64({t_ms}) AS time, 'order_submit' AS event, '{{\"amount\":100}}' AS properties) FORMAT TSV").strip()
    ch.insert_json("dmp.events", [{"project": "altatech_dmp", "time": modeling.ms_to_dt(t_ms), "event": "bond_order", "source": "legacy_format", "one_id": did,
                                   "entity_type": "", "entity_id": "", "distinct_id": did, "session_id": "", "event_id": v1, "platform": "", "app_id": "", "symbol": "", "side": "", "channel": "",
                                   "amount": 100.0, "utm_source": "", "utm_campaign": "", "props": {"amount": 100}, "ingest_time": "2026-10-19 00:00:00.000", "schema_version": 1, "late": 0, "batch_id": "pre_v1_collide"}])
    line = lambda amt: json.dumps({"type": "track", "event": "order_submit", "distinct_id": did, "time": t_ms, "project": "sbp_family", "properties": {"amount": amt}}) + "\n"
    day = f"toDate(fromUnixTimestamp64Milli(toInt64({t_ms})), 'Asia/Taipei')"
    def state() -> tuple[int, float, int, float]:
        ev = ch.query(f"SELECT count(), sum(amount) FROM dmp.events FINAL WHERE distinct_id = '{did}' AND event = 'bond_order' FORMAT TSV").strip().split("\t")
        ag = ch.query(f"SELECT countMerge(cnt), sumMerge(amount) FROM dmp.events_daily_agg WHERE project = 'altatech_dmp' AND event_date = {day} AND event = 'bond_order' AND platform = '' AND channel = '' FORMAT TSV").strip().split("\t")
        return int(ev[0]), float(ev[1]), int(ag[0]), float(ag[1])
    assert state() == (1, 100.0, 1, 100.0), state()
    # (1) 同 did／時間／事件、金額 100 → 200：舊 id 相同但內容不同 → 新 id 寫入，兩筆都在
    src = work / "v1_collide.jsonl"; src.write_text(line(200), encoding="utf-8")
    r = loader.run_file(src, work / "user_files", "v1_collide", "k_idm", via_http=via_http)
    assert r["recon_ok"] and r["events"] == 1 and r["events_skipped"] == 0, r
    assert state() == (2, 300.0, 2, 300.0), state()
    # (2) 與舊列內容完全相同的重送 → 只一筆（skipped），彙總不變；再送一次金額 200 的那行 → 已有同新 id，也不寫
    src.write_text(line(100) + line(200), encoding="utf-8")
    r2 = loader.run_file(src, work / "user_files", "v1_replay", "k_idm", via_http=via_http)
    assert r2["recon_ok"] and r2["events"] == 0 and r2["events_skipped"] == 2, r2
    assert state() == (2, 300.0, 2, 300.0), state()
    print(f"legacy v1 collision: old id + amount 100→200 → kept both (events 2, agg cnt 2, amount 300); identical replay skipped {r2['events_skipped']}")



def legacy_bigint(ch: ClickHouseHTTP, loader: Any, work: Path, via_http: bool) -> None:
    """Codex #51 複審 P1：相鄰大整數（> 2^53）只差 1 的兩筆事件，內容 id 不能因為經過浮點而相同。同批、跨批都要兩筆入庫，值原樣保留。"""
    t_ms, n0, n1 = 1_792_454_500_000, 9007199254740992, 9007199254740993
    line = lambda did, n: json.dumps({"type": "track", "event": "order_submit", "distinct_id": did, "time": t_ms, "project": "sbp_family", "properties": {"n": n}}) + "\n"
    vals = lambda did: ch.query(f"SELECT arraySort(groupArray(toString(props.n))) FROM dmp.events FINAL WHERE distinct_id = '{did}' FORMAT TSV").strip()
    src = work / "bigint.jsonl"
    # 同批：兩行只差 1 → 兩筆
    src.write_text(line("bigint_cookie", n0) + line("bigint_cookie", n1), encoding="utf-8")
    r = loader.run_file(src, work / "user_files", "bigint_same", "k_idm", via_http=via_http)
    assert r["recon_ok"] and r["events"] == 2 and r["events_skipped"] == 0, r
    assert vals("bigint_cookie") == f"['{n0}','{n1}']", vals("bigint_cookie")
    # 跨批：先 n0、再 n1 → 各寫一筆；再送 n1 → 跳過
    for i, (n, written) in enumerate([(n0, 1), (n1, 1), (n1, 0)]):
        src.write_text(line("bigint_cookie2", n), encoding="utf-8")
        r = loader.run_file(src, work / "user_files", f"bigint_cross{i}", "k_idm", via_http=via_http)
        assert r["recon_ok"] and r["events"] == written and r["events_skipped"] == 1 - written, (i, r)
    assert vals("bigint_cookie2") == f"['{n0}','{n1}']", vals("bigint_cookie2")
    print(f"legacy bigint: {n0} vs {n1} → same batch 2 events, cross batch 2 events, replay skipped; values exact")


def agg_stage(ch: ClickHouseHTTP) -> None:
    """R12 回歸（事件只追加、不覆蓋）：同一事件直接寫兩次（繞過載入路徑）→ 底表 FINAL 1 筆、MV 彙總 2 筆；重建該日分區後彙總回到 1。
    載入路徑：倉裡已有的 event_id 一律不寫（內容相同 skipped、不同 kept_first），保留第一筆，彙總不變；並行寫入者／重建交錯、各點中斷都可由標記日誌恢復。"""
    from .aggregates import dedupe, rebuild, verify
    from .ga4_source import FakeBigQuery, ga4_row
    row = {"project": "altatech_dmp", "time": "2026-09-01 01:00:00.000", "event": "bond_deal", "source": "e2e_dup", "one_id": "v1_dup", "entity_type": "", "entity_id": "", "distinct_id": "",
           "session_id": "", "event_id": "agg-dup-1", "platform": "iOS", "app_id": "", "symbol": "2330", "side": "B", "channel": "app", "amount": 100.0, "utm_source": "", "utm_campaign": "",
           "props": {}, "ingest_time": "2026-09-01 01:00:00.000", "schema_version": 1, "late": 0, "batch_id": "dup1"}
    ch.insert_json("dmp.events", [row]); ch.insert_json("dmp.events", [{**row, "ingest_time": "2026-09-01 01:00:01.000", "batch_id": "dup2"}])
    ch.query("OPTIMIZE TABLE dmp.events PARTITION '202609' FINAL")
    v = verify(ch, "2026-09-01"); assert v["events_cnt"] == 1 and v["agg_cnt"] == 2, v                      # 修正前的狀態：MV 每次 INSERT 都累加
    assert rebuild(ch, ["2026-09-01"]) == ["2026-09-01"]
    v = verify(ch, "2026-09-01"); assert v["events_cnt"] == 1 and v["agg_cnt"] == 1 and v["agg_users"] == 1 and v["amount_diff"] < 1e-6, v
    # 載入路徑的去重（只追加）：一模一樣 → skipped；同 event_id 但內容不同（utm 補齊、props、時間跨日、one_id）→ kept_first，倉裡仍是第一筆
    d0 = dedupe(ch, [row]); assert d0.rows == [] and d0.skipped == 1 and d0.kept_first == 0, d0
    for change in ({"utm_source": "google"}, {"props": {"market": "US"}, "utm_campaign": "new"}, {"time": "2026-09-02 10:00:00.000"}, {"one_id": "v1_rewritten"}):
        d0 = dedupe(ch, [{**row, **change, "ingest_time": "2026-09-01 02:00:00.000", "batch_id": "chg"}])
        assert d0.rows == [] and d0.skipped == 0 and d0.kept_first == 1 and d0.kept_first_ids == ["agg-dup-1"], (change, d0)
    other = {**row, "event_id": "agg-dup-2"}; d0 = dedupe(ch, [other]); assert d0.rows == [other], d0         # 不同 event_id（同時間、同內容）＝另一筆事件 → 追加
    bq = FakeBigQuery(); runner = LocalRunner(ch, bq_clients={"bq": bq})
    base_us = int(datetime(2026, 9, 2, 0, 0, tzinfo=UTC).timestamp() * 1_000_000)
    bq.tables["ds.events_intraday_20260902"] = [ga4_row(i, base_us + i * 60_000_000, "kh_apply", uid="v1_abc") for i in range(50)]
    task = {"id": "agg_ga4", "source": {"kind": "ga4", "connection": "bq", "dataset": "ds", "table_kind": "intraday"}}
    now = datetime(2026, 9, 2, 1, 0, tzinfo=UTC); log: list[str] = []
    r1 = runner.run(task, watermark=None, batch_id="g1", known_fields=None, now=now, log=log.append)
    r2 = runner.run(task, watermark=None, batch_id="g2", known_fields=None, now=now + timedelta(minutes=15), log=log.append)   # 整批重讀（重試）
    assert r1.rows_loaded == 50 and r2.rows_loaded == 50 and ch.scalar("SELECT count() FROM dmp.events WHERE batch_id = 'g2'") == 0, (r1, r2)   # 第二次一筆都沒寫
    v = verify(ch, "2026-09-02"); assert v["events_cnt"] == v["agg_cnt"] == 50 and v["agg_users"] == 1, v
    for ev in bq.tables["ds.events_intraday_20260902"]: ev["session_traffic_source_last_click"]["manual_campaign"]["source"] = "google"   # 日批補欄位：同 event_id、內容不同
    r3 = runner.run(task, watermark=None, batch_id="g3", known_fields=None, now=now + timedelta(minutes=30), log=log.append)
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id = 'g3'") == 0 and r3.rows_loaded == 50, r3                 # 不覆蓋：一筆都不寫
    assert any("kept_first=50" in x for x in log[-4:]) and not any("daily_agg rebuilt" in x for x in log[-4:]), log[-4:]
    v = verify(ch, "2026-09-02"); assert v["events_cnt"] == v["agg_cnt"] == 50 and v["agg_users"] == 1, v
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE event_date = toDate('2026-09-02') AND utm_source = 'google'") == 0   # 保留第一筆（串流版本）
    # event_id 內容雜湊（#32 第五輪複審 P1）：真正的 modeling 路徑
    # (1) 同委託單、同狀態、不同時間的兩次部分成交 → 兩筆都入倉（舊公式兩筆同 id，會被當重複丟掉）；整批重送 → 0 筆
    mdl = modeling.load(ROOT / "integration" / "models.example.yaml"); t_ing = int(datetime(2026, 9, 20, 2, tzinfo=UTC).timestamp() * 1000)
    so = {"ORD_NO": "SYNTH_ORDER_1", "STATUS": "PARTIAL", "ORD_TIME": "2026-09-20 09:00:00", "UPDATED_AT": "2026-09-20 09:00:00", "CUST_ID": "C1", "ACCT_NO": "9800000001",
          "STK_CODE": "2330", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "1000", "QTY": "3000", "FILL_QTY": "1000", "CHANNEL": "A", "FEE": "", "TAX": "", "RJ_CODE": ""}
    trade = lambda rows: [modeling.to_ch_row(mdl, x, "altatech_dmp", t_ing) for x in modeling.apply(mdl, rows, now=datetime(2026, 9, 20, 2, tzinfo=UTC)).ok]
    tr = trade([so, {**so, "ORD_TIME": "2026-09-20 09:01:00", "UPDATED_AT": "2026-09-20 09:01:00", "FILL_QTY": "2000"}])
    assert len({x["event_id"] for x in tr}) == 2 and len({x["_event_id_v1"] for x in tr}) == 1, tr
    dt1 = dedupe(ch, tr); assert len(dt1.rows) == 2 and dt1.kept_first == 0, dt1
    ch.insert_json("dmp.events", dt1.rows); rebuild(ch, ["2026-09-20"])
    dt2 = dedupe(ch, trade([so, {**so, "ORD_TIME": "2026-09-20 09:01:00", "UPDATED_AT": "2026-09-20 09:01:00", "FILL_QTY": "2000"}])); assert dt2.rows == [] and dt2.skipped == 2, dt2
    # (2) 改版前（舊公式 id）入倉的列：同內容重放 → skipped_v1；同舊 id 但不同時間（舊公式碰撞，其實是另一筆）→ 追加
    so2 = {**so, "ORD_NO": "SYNTH_ORDER_2"}; old = {k: v for k, v in trade([so2])[0].items() if k != "_event_id_v1"}
    old["event_id"] = trade([so2])[0]["_event_id_v1"]; ch.insert_json("dmp.events", [old])
    dt3 = dedupe(ch, trade([so2, {**so2, "ORD_TIME": "2026-09-20 09:05:00", "UPDATED_AT": "2026-09-20 09:05:00", "FILL_QTY": "3000"}]))
    assert len(dt3.rows) == 1 and dt3.skipped_v1 == 1 and dt3.rows[0]["time"].endswith("01:05:00.000"), dt3
    ch.insert_json("dmp.events", dt3.rows); rebuild(ch, ["2026-09-20"])
    v = verify(ch, "2026-09-20"); assert v["events_cnt"] == v["agg_cnt"] == 4 and v["amount_diff"] < 1e-6, v
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE event_date = toDate('2026-09-20')") == 4                                   # 兩次部分成交 ×2 單，無重複
    # (3) GA4：同時間、核心 event_params 不同 → 兩筆；改版前入倉的串流列（舊 id）遇到日批同一事件（只多了日批補的欄位）→ 認得、不寫
    from .ga4_source import flatten
    g_us = int(datetime(2026, 9, 21, 1, 0, tzinfo=UTC).timestamp() * 1_000_000)
    ga = ga4_row(900, g_us, "kh_apply", uid="v1_ga"); gb = ga4_row(900, g_us, "kh_apply", uid="v1_ga"); gb["event_params"][2] = {"key": "symbol", "value": {"string_value": "2454"}}
    fa, fb = flatten(ga, {}, "altatech_dmp", "ga360_intraday", "gx", t_ing), flatten(gb, {}, "altatech_dmp", "ga360_intraday", "gx", t_ing)
    dg = dedupe(ch, [fa, fb]); assert len(dg.rows) == 2, dg; ch.insert_json("dmp.events", dg.rows)
    gc = ga4_row(901, g_us + 60_000_000, "kh_apply", uid="v1_ga"); fc = flatten(gc, {}, "altatech_dmp", "ga360_intraday", "gy", t_ing)
    oldg = {k: v for k, v in fc.items() if k != "_event_id_v1"}; ch.insert_json("dmp.events", [oldg | {"event_id": fc["_event_id_v1"]}])
    gd = ga4_row(901, g_us + 60_000_000, "kh_apply", uid="v1_ga"); gd["traffic_source"] = {"source": "bing"}; gd["geo"] = {"country": "Japan"}   # 日批：stream／bundle 序號相同，只多了補的欄位
    gd["session_traffic_source_last_click"] = {"manual_campaign": {"source": "edm", "campaign_name": "late"}}
    fd = flatten(gd, {}, "altatech_dmp", "ga360_daily", "gz", t_ing); assert fd["event_id"] == fc["event_id"] and fd["_event_id_v1"] == fc["_event_id_v1"]
    dd = dedupe(ch, [fd]); assert dd.rows == [] and dd.skipped_v1 == 1, dd
    rebuild(ch, ["2026-09-21"]); v = verify(ch, "2026-09-21"); assert v["events_cnt"] == v["agg_cnt"] == 3, v
    # 標記日誌（events_daily_agg_pending）：寫入者 begin（touch／change，phase 0 + 租約，在去重之前）→ 寫入 → landed（phase 1）→
    # 窗口重疊的別的寫入者？（可能同一筆寫兩次）→ 登記待重建再重建；被別人重建換過分區？→ 重建；否則自己確認。重建只確認落地早於自己快照的標記。
    # 測試把租約與餘量縮短（LEASE 4 秒、餘量 1 秒）好等得到過期。
    import time

    from . import aggregates as agg_mod
    from .aggregates import LeaseExpired, Rebuild, begin, event_date_of, gc_marks, mark_pending, pending_dates
    assert pending_dates(ch) == [], pending_dates(ch)                                                                         # 前面的正常路徑沒有殘留標記
    lease0, margin0 = agg_mod.LEASE_S, agg_mod.MARGIN_S; agg_mod.LEASE_S, agg_mod.MARGIN_S = 4, 1
    settle = lambda: time.sleep(agg_mod.MARGIN_S + 0.5)                                                                       # 等過餘量：落地時間要早於下一次快照減餘量才會被確認
    expire = lambda: time.sleep(agg_mod.LEASE_S + agg_mod.MARGIN_S + 0.5)                                                     # 等租約過期
    no_tmp = lambda: ch.scalar("SELECT count() FROM system.tables WHERE database = 'dmp' AND name LIKE 'events_daily_agg_stage_%'") == 0
    ok = lambda d, cnt: (lambda v: v["events_cnt"] == v["agg_cnt"] == cnt and v["events_users"] == v["agg_users"] and v["amount_diff"] < 1e-6)(verify(ch, d))

    def protocol(new_rows: list[dict[str, Any]], batch: str, crash_after: str | None = None) -> Any:
        """與 runner 相同的順序：begin（touch，去重之前）→ dedupe → 寫入 → landed → concurrent（重疊 → 登記待重建）→ settle → 重建；crash_after 指定在哪一步之後中斷"""
        m = begin(ch, set(), batch, touch=[event_date_of(r) for r in new_rows]); d = dedupe(ch, new_rows)
        if crash_after == "dedupe": return d, m
        if d.rows: m.check(); ch.insert_json("dmp.events", d.rows)
        if crash_after == "insert": return d, m
        m.landed()
        if crash_after == "landed": return d, m
        race = m.concurrent()
        if race: mark_pending(ch, race, batch)
        rebuild(ch, race | m.settle(), batch); return d, m

    def rewrite_one_id(r: dict[str, Any], new_oid: str, owner: str, crash_after: str | None = None) -> tuple[dict[str, Any], Any]:
        """OneID 歸戶改寫（唯一允許的就地改寫，尚未接線）的呼叫順序：begin(change 日期) → 刪舊列 → 寫新 one_id → landed → rebuild。one_id 在排序鍵，只能刪了再寫。"""
        d = event_date_of(r); m = begin(ch, [d], owner); m.check(); nr = {**r, "one_id": new_oid, "batch_id": owner}
        ch.query(f"DELETE FROM dmp.events WHERE event_id = '{r['event_id']}'"); ch.insert_json("dmp.events", [nr])
        m.landed()
        if crash_after != "landed": rebuild(ch, [d], owner)
        return nr, m
    # (a0) 兩個寫入者同時讀到同一筆新事件：都通過去重、各寫一次（MV 多算一次，底表合併後只剩一筆）→ 窗口重疊被偵測 → 重建，彙總 = 1
    cr = {**row, "event_id": "agg-crash-1", "time": "2026-09-03 01:00:00.000", "amount": 1000.0, "one_id": "v1_c1", "props": {"market": "TSE"}, "batch_id": "c0", "ingest_time": "2026-09-03 01:00:00.000"}
    W1 = begin(ch, set(), "W1", touch=["2026-09-03"]); W2 = begin(ch, set(), "W2", touch=["2026-09-03"])
    d1, d2 = dedupe(ch, [cr]), dedupe(ch, [{**cr, "batch_id": "W2"}]); assert len(d1.rows) == len(d2.rows) == 1             # 兩邊都以為是新的
    W1.check(); ch.insert_json("dmp.events", d1.rows); W2.check(); ch.insert_json("dmp.events", d2.rows)
    v = verify(ch, "2026-09-03"); assert v["events_cnt"] == 1 and v["agg_cnt"] == 2 and abs(v["amount_diff"] - 1000) < 1e-6, v   # 並行重複寫入的錯誤狀態
    W1.landed(); W2.landed()
    for W in (W1, W2):
        race = W.concurrent(); assert race == {"2026-09-03"}, race; mark_pending(ch, race, W.owner); rebuild(ch, race | W.settle(), W.owner)
    settle(); rebuild(ch, []); assert ok("2026-09-03", 1) and pending_dates(ch) == [], (verify(ch, "2026-09-03"), pending_dates(ch))
    # (a) 同樣的並行重複，W1 先做完（偵測到 W2 的窗口 → 重建，此時 W2 還沒寫）→ W2 寫入、落地後中斷 → W2 已落地未確認的 touch 列入待重建 → 相同 payload 重試修好
    ca = {**cr, "event_id": "agg-crash-2", "time": "2026-09-03 02:00:00.000", "amount": 300.0, "one_id": "v1_c2"}
    W1 = begin(ch, set(), "W1a", touch=["2026-09-03"]); W2 = begin(ch, set(), "W2a", touch=["2026-09-03"])
    d1, d2 = dedupe(ch, [ca]), dedupe(ch, [ca]); W1.check(); ch.insert_json("dmp.events", d1.rows); W1.landed()
    race = W1.concurrent(); assert race == {"2026-09-03"}, race; mark_pending(ch, race, "W1a"); rebuild(ch, race | W1.settle(), "W1a")
    W2.check(); ch.insert_json("dmp.events", d2.rows); W2.landed()                                                            # W2：落地後中斷
    v = verify(ch, "2026-09-03"); assert v["events_cnt"] == 2 and v["agg_cnt"] == 3 and abs(v["amount_diff"] - 300) < 1e-6, v
    assert pending_dates(ch) == ["2026-09-03"], pending_dates(ch)
    settle(); d, _ = protocol([{**ca, "batch_id": "W2a-r"}], "W2a-r"); assert d.skipped == 1 and d.rows == [], d
    assert ok("2026-09-03", 2) and pending_dates(ch) == [], (verify(ch, "2026-09-03"), pending_dates(ch))
    # (a2) W2 寫入後、記落地前中斷（touch 只有 phase 0）：相同 payload 重試時 W2 的租約還在 → 視為窗口重疊 → 登記待重建並重建；租約過期後 W2 的標記被確認
    cb = {**cr, "event_id": "agg-crash-3", "time": "2026-09-03 03:00:00.000", "amount": 50.0, "one_id": "v1_c3"}
    W1 = begin(ch, set(), "W1b", touch=["2026-09-03"]); W2 = begin(ch, set(), "W2b", touch=["2026-09-03"])
    d1, d2 = dedupe(ch, [cb]), dedupe(ch, [cb]); W1.check(); ch.insert_json("dmp.events", d1.rows); W1.landed()
    race = W1.concurrent(); mark_pending(ch, race, "W1b"); rebuild(ch, race | W1.settle(), "W1b")
    W2.check(); ch.insert_json("dmp.events", d2.rows)                                                                         # W2：寫入後中斷（沒記落地）
    assert verify(ch, "2026-09-03")["agg_cnt"] == 4 and pending_dates(ch) == []                                               # 租約還在：還不算待重建
    d, _ = protocol([{**cb, "batch_id": "W2b-r"}], "W2b-r"); assert d.skipped == 1 and ok("2026-09-03", 3), verify(ch, "2026-09-03")
    assert pending_dates(ch) == [], pending_dates(ch)
    expire(); assert pending_dates(ch) == ["2026-09-03"]; rebuild(ch, []); assert ok("2026-09-03", 3) and pending_dates(ch) == [], pending_dates(ch)
    # (c) REPLACE 前中斷（私有暫存表留著）、(d) REPLACE 後、確認前中斷：重試只是再算一次，結果不變、暫存表清掉、標記確認
    r = Rebuild(ch, "2026-09-03", "c3"); r.mark(); r.snapshot(); assert pending_dates(ch) == ["2026-09-03"] and not no_tmp()
    settle(); rebuild(ch, []); assert ok("2026-09-03", 3)
    ch.query("DROP TABLE IF EXISTS " + ch.query("SELECT name FROM system.tables WHERE database = 'dmp' AND name LIKE 'events_daily_agg_stage_%' LIMIT 1 FORMAT TSV").strip().join(["dmp.", ""]))   # 中斷留下的暫存表（營運清理）
    expire(); rebuild(ch, []); assert pending_dates(ch) == [] and no_tmp(), pending_dates(ch)
    r = Rebuild(ch, "2026-09-03", "c4"); r.mark(); r.snapshot(); r.replace()                                                  # (d) REPLACE 後、確認前中斷
    assert pending_dates(ch) == ["2026-09-03"]; settle(); rebuild(ch, []); assert ok("2026-09-03", 3)
    expire(); rebuild(ch, []); assert pending_dates(ch) == [], pending_dates(ch)
    # (e) 真的 runner（GA4 路徑）：另一個寫入者還在寫（租約內、它的去重沒看到倉裡那筆）把同一筆 GA4 事件又寫了一次 → runner 偵測到窗口重疊 →
    #     登記待重建後 rebuild 丟例外（模擬中斷）→ 那個寫入者也掛了；租約過期後下一次同內容的 run 一筆不寫，但補重建並寫進 log
    dup = {**json.loads(ch.query("SELECT * EXCEPT (one_id_uint) FROM dmp.events WHERE event_date = toDate('2026-09-02') ORDER BY event_id LIMIT 1 FORMAT JSONEachRow",
                                 settings={"output_format_json_quote_64bit_integers": 0}).splitlines()[0]), "batch_id": "Wdup"}
    Wd = begin(ch, set(), "Wdup", touch=["2026-09-02"]); Wd.check(); ch.insert_json("dmp.events", [dup])                                 # Wd 寫入中（還沒落地）
    def boom(*a: Any, **k: Any) -> Any: raise RuntimeError("simulated crash before rebuild")
    orig = agg_mod.rebuild; agg_mod.rebuild = boom
    try: runner.run(task, watermark=None, batch_id="g4", known_fields=None, now=now + timedelta(minutes=45), log=log.append)
    except RuntimeError: pass
    finally: agg_mod.rebuild = orig
    assert any("concurrent writer on ['2026-09-02']" in x for x in log[-4:]) and verify(ch, "2026-09-02")["agg_cnt"] == 51 and pending_dates(ch) == ["2026-09-02"], (log[-4:], verify(ch, "2026-09-02"))
    expire(); runner.run(task, watermark=None, batch_id="g5", known_fields=None, now=now + timedelta(minutes=60), log=log.append)   # Wd 的租約已過期
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id IN ('g4', 'g5')") == 0 and any("incl. pending from an interrupted run" in x for x in log[-4:]), log[-4:]
    assert ok("2026-09-02", 50) and pending_dates(ch) == [], (verify(ch, "2026-09-02"), pending_dates(ch))
    # (f) 重建與歸戶改寫交錯（barrier 式）：A 重建快照後暫停 → B 把 w2 的 one_id 改成 w1 的（用戶 3 → 2）、落地後中斷 → A 用舊快照換分區。
    #     A 看得到「有人在我快照後落地」→ 再快照一次才確認；B 的標記不會被舊快照吃掉。
    day5 = "2026-09-05"
    w1, w2, w3 = ({**row, "event_id": f"agg-race-{i}", "time": f"2026-09-05 0{i}:00:00.000", "amount": 1000.0 * i, "one_id": f"v1_r{i}", "batch_id": "r0", "ingest_time": "2026-09-05 01:00:00.000"} for i in (1, 2, 3))
    ch.insert_json("dmp.events", [w1, w2, w3]); rebuild(ch, [day5]); assert ok(day5, 3) and verify(ch, day5)["agg_users"] == 3
    A = Rebuild(ch, day5, "A"); A.mark(); A.snapshot()                                                                        # A：快照後暫停
    w2b, mB = rewrite_one_id(w2, "v1_r1", "B", crash_after="landed")                                                          # B：改寫落地後中斷
    A.replace(); assert A.newer_landed(), "A 必須看到 B 在它快照後落地"
    v = verify(ch, day5); assert v["events_users"] == 2 and v["agg_users"] == 3, v                                           # 此刻彙總是錯的（舊快照）
    settle(); A.snapshot(); A.replace(); assert not A.newer_landed(); acked = A.ack()                                         # A：再快照 → 對了 → 確認（含 B）
    assert ok(day5, 3) and verify(ch, day5)["agg_users"] == 2 and mB.ids[day5] in acked and pending_dates(ch) == [], (verify(ch, day5), acked)
    # (f2) 同一交錯但 A 不做「再快照」：確認只會確認落地早於 A 快照的標記 → C 的標記留著 → 下一次重建修好
    A = Rebuild(ch, day5, "A2"); A.mark(); A.snapshot()
    w3b, mC = rewrite_one_id(w3, "v1_r1", "C", crash_after="landed")
    A.replace(); acked = A.ack(); assert mC.ids[day5] not in acked and pending_dates(ch) == [day5], (acked, pending_dates(ch))   # C 沒被吃掉
    assert verify(ch, day5)["agg_users"] == 2
    settle(); rebuild(ch, []); assert ok(day5, 3) and verify(ch, day5)["agg_users"] == 1 and pending_dates(ch) == [], verify(ch, day5)
    # (g) 兩個重建同一天競爭 + 中間有只寫新事件的寫入者落地：A 快照 → W 落地 → B 完整重建（含確認 W）→ A 用舊快照換分區 → A 看到更新的落地 → 再快照。私有暫存表互不干擾
    A = Rebuild(ch, day5, "A3"); A.mark(); A.snapshot()
    w4 = {**w1, "event_id": "agg-race-4", "time": "2026-09-05 04:00:00.000", "amount": 4000.0, "one_id": "v1_r4", "batch_id": "W"}
    _, mW = protocol([w4], "W", crash_after="landed")
    settle(); B = Rebuild(ch, day5, "B3"); assert mW.touch[day5] in B.run() and ok(day5, 4)
    A.replace(); assert abs(verify(ch, day5)["amount_diff"] - 4000) < 1e-6 and A.newer_landed()                             # A 的舊快照蓋掉了 B 的正確結果，但 A 自己會發現
    A.snapshot(); A.replace(); assert not A.newer_landed(); A.ack()
    assert ok(day5, 4) and pending_dates(ch) == [] and no_tmp(), verify(ch, day5)
    # (h) 租約圍籬：標記後拖太久的寫入者不准再寫；它的標記在租約過期後由任何重建確認
    slow = begin(ch, [day5], "slow"); slow.check(); time.sleep(agg_mod.LEASE_S)
    try: slow.check(); raise AssertionError("租約過期要擋")
    except LeaseExpired: pass
    assert pending_dates(ch) == [day5]; expire(); rebuild(ch, []); assert pending_dates(ch) == [] and no_tmp()
    # (i) 只寫「全新事件」的寫入者夾在 A 的快照與換分區之間：MV 加上去的被 A 的舊快照蓋掉 →
    #     touch 標記讓 A 看得到「有人在我快照後落地」→ 再快照；寫入者落地後也看得到 A 換過分區 → 自己再重建一次。新事件不會被吃掉
    base6 = {**row, "event_id": "agg-new-0", "time": "2026-09-06 01:00:00.000", "amount": 100.0, "batch_id": "n0", "ingest_time": "2026-09-06 01:00:00.000"}
    ch.insert_json("dmp.events", [base6]); rebuild(ch, ["2026-09-06"]); assert verify(ch, "2026-09-06")["agg_cnt"] == 1
    A = Rebuild(ch, "2026-09-06", "A6"); A.mark(); A.snapshot()
    n1 = {**base6, "event_id": "agg-new-1", "time": "2026-09-06 02:00:00.000", "amount": 500.0, "one_id": "v1_new", "batch_id": "N1", "ingest_time": "2026-09-06 02:00:00.000"}
    mN = begin(ch, set(), "N1", touch=[event_date_of(n1)]); dN = dedupe(ch, [n1]); assert len(dN.rows) == 1 and dN.kept_first == 0 and list(mN.touch) == ["2026-09-06"], dN
    mN.check(); ch.insert_json("dmp.events", dN.rows); mN.landed()
    A.replace(); v = verify(ch, "2026-09-06"); assert v["events_cnt"] == 2 and v["agg_cnt"] == 1 and abs(v["amount_diff"] - 500) < 1e-6, v   # 舊快照把新事件蓋掉了
    assert A.newer_landed(); settle(); A.snapshot(); A.replace(); assert not A.newer_landed(); A.ack()
    assert ok("2026-09-06", 2) and verify(ch, "2026-09-06")["agg_users"] == 2
    assert mN.overlapped() == {"2026-09-06"} and pending_dates(ch) == [], pending_dates(ch)
    # (j) 寫入者跨過 A 的整段重建：A 快照 → 寫入者寫入（還沒落地）→ A 換分區、沒看到落地、確認收工 → 寫入者落地，發現 A 換過分區 → 自己重建
    mW2 = begin(ch, set(), "N2", touch=["2026-09-06"])
    A = Rebuild(ch, "2026-09-06", "A7"); A.mark(); A.snapshot()
    n2 = {**n1, "event_id": "agg-new-2", "time": "2026-09-06 03:00:00.000", "amount": 700.0, "one_id": "v1_new2", "batch_id": "N2", "ingest_time": "2026-09-06 03:00:00.000"}
    mW2.check(); ch.insert_json("dmp.events", [n2])
    A.replace(); assert not A.newer_landed(); A.ack()
    v = verify(ch, "2026-09-06"); assert v["events_cnt"] == 3 and v["agg_cnt"] == 2 and abs(v["amount_diff"] - 700) < 1e-6, v       # A 的快照沒包到 n2
    mW2.landed(); ov = mW2.settle(); assert ov == {"2026-09-06"}, ov; rebuild(ch, ov, "N2")
    assert ok("2026-09-06", 3) and verify(ch, "2026-09-06")["agg_users"] == 3 and pending_dates(ch) == [], verify(ch, "2026-09-06")
    # (k) 寫入者寫到一半掛了（touch 沒落地）：租約過期後，任何一次重建都會保守重做那天
    mW3 = begin(ch, set(), "N3", touch=["2026-09-06"]); A = Rebuild(ch, "2026-09-06", "A8"); A.mark(); A.snapshot()
    n3 = {**n1, "event_id": "agg-new-3", "time": "2026-09-06 04:00:00.000", "amount": 900.0, "one_id": "v1_new3", "batch_id": "N3", "ingest_time": "2026-09-06 04:00:00.000"}
    mW3.check(); ch.insert_json("dmp.events", [n3]); A.replace(); A.ack()                                                     # 寫入者在這裡掛了
    assert verify(ch, "2026-09-06")["agg_cnt"] == 3 and pending_dates(ch) == []                                               # 租約還沒過期：還不算待重建
    expire(); assert pending_dates(ch) == ["2026-09-06"]; rebuild(ch, [])
    assert ok("2026-09-06", 4) and pending_dates(ch) == [] and no_tmp(), verify(ch, "2026-09-06")
    # (l) 第四輪複審 P1：touch 寫入者在 A 快照後寫入、A 換分區時它還沒落地（A 不會再快照、照常確認）→ 寫入者落地後、settle／重建前中斷。
    #     已落地未確認的 touch 是恢復意圖：列入待重建、租約過期與 gc 都不能讓它消失；相同 payload 重試一筆不寫也會補重建
    base7 = {**row, "event_id": "agg-l-0", "time": "2026-09-07 01:00:00.000", "amount": 100.0, "one_id": "v1_l0", "batch_id": "l0", "ingest_time": "2026-09-07 01:00:00.000"}
    ch.insert_json("dmp.events", [base7]); rebuild(ch, ["2026-09-07"]); assert verify(ch, "2026-09-07")["agg_cnt"] == 1
    l1 = {**base7, "event_id": "agg-l-1", "time": "2026-09-07 02:00:00.000", "amount": 200.0, "one_id": "v1_l1", "batch_id": "L", "ingest_time": "2026-09-07 02:00:00.000"}
    for variant in ("retry", "expire", "gc"):
        lv = {**l1, "event_id": f"agg-l-{variant}", "batch_id": f"L-{variant}"}
        B = begin(ch, [], "B-" + variant, touch=["2026-09-07"]); A = Rebuild(ch, "2026-09-07", "A-" + variant); A.mark(); A.snapshot()
        B.check(); ch.insert_json("dmp.events", [lv])
        A.replace(); assert not A.newer_landed(); A.ack()                                                                     # B 還沒登記 phase 1 → A 不再快照
        B.landed()                                                                                                            # 落地後中斷：不跑 settle／重建
        v = verify(ch, "2026-09-07"); assert v["events_cnt"] == v["agg_cnt"] + 1 and abs(v["amount_diff"] - 200) < 1e-6, v      # A 的舊快照把 B 的新事件蓋掉了（Codex 實測的狀態）
        assert pending_dates(ch) == ["2026-09-07"], pending_dates(ch)                                                         # 恢復意圖還在
        if variant == "expire": expire(); assert pending_dates(ch) == ["2026-09-07"], "租約過期不能讓已落地的 touch 消失"
        if variant == "gc":
            keep0 = agg_mod.KEEP_S; agg_mod.KEEP_S = 0; time.sleep(0.2)
            try: gc_marks(ch)
            finally: agg_mod.KEEP_S = keep0
            assert pending_dates(ch) == ["2026-09-07"], "gc 不能清掉已落地未確認的 touch"
        settle(); d, _ = protocol([{**lv, "batch_id": f"L-{variant}-r"}], f"L-{variant}-r")                                  # 相同 payload 重試
        assert d.skipped == 1 and d.rows == [], d
        v = verify(ch, "2026-09-07"); assert v["events_cnt"] == v["agg_cnt"] and v["events_users"] == v["agg_users"] and v["amount_diff"] < 1e-6 and pending_dates(ch) == [], (variant, v, pending_dates(ch))
    # (m) 正常的只寫新事件批次：落地 → settle 自己確認，不留待重建、不觸發重建
    n_ok = {**l1, "event_id": "agg-l-ok", "batch_id": "L-ok"}; d, _ = protocol([n_ok], "L-ok"); assert len(d.rows) == 1 and pending_dates(ch) == [], pending_dates(ch)
    assert ok("2026-09-07", 5)
    agg_mod.LEASE_S, agg_mod.MARGIN_S = lease0, margin0
    print("daily_agg idempotency (append-only): direct double insert → agg 2 vs base 1 → rebuild → 1; existing event_id → skipped (identical) / kept_first (different content: utm/props/time/one_id), never overwritten; "
          "runner re-read → 0 written; GA4 enrichment re-read → kept_first=50, 0 written, agg unchanged; concurrent writers inserting the same new event → window overlap detected → rebuild (agg 2 → 1); "
          "crash after landed/insert/stage/replace → marker journal → retry repairs; runner crash before rebuild → next run repairs; one_id rewrite × rebuild interleaving re-snapshots and never acks early; "
          "two rebuilds racing with a writer converge; lease fencing; new-only writers inside / spanning a rebuild never lost; touch writer crashing after landed stays pending through lease expiry and gc; normal batches self-ack")


def dbapi_stage(ch: ClickHouseHTTP | None, work: Path, rows: list[dict[str, Any]]) -> None:
    """MSSQL 的替身：sqlite（同樣是 DB-API 2.0）；正式環境 db_connections={'dwh_trade': lambda: pyodbc.connect(...)}。"""
    import sqlite3
    db = work / "mssql_stand_in.db"; conn = sqlite3.connect(db)
    cols = list(rows[0].keys())
    conn.execute(f"CREATE TABLE fact_order_hist ({', '.join(c + ' TEXT' for c in cols)})")
    conn.executemany(f"INSERT INTO fact_order_hist VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows[:12000]]); conn.commit(); conn.close()
    runner = LocalRunner(ch, db_connections={"dwh_trade": lambda: sqlite3.connect(db)})
    s = Scheduler(work / "sched_db.db", runner)
    t0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
    s.add_task("sync_orders_mssql", str(ROOT / "integration" / "models.example.yaml"), cron="*/15 * * * *", mode="incremental", retries=2, backoff_s=60, schema_change="auto_map",
               source={"kind": "dbapi", "connection": "dwh_trade", "table": "fact_order_hist", "incremental_field": "UPDATED_AT", "paramstyle": "?"}, now=t0)
    r1 = s.tick(t0 + timedelta(minutes=15)); assert r1[0]["status"] == "success", r1
    run1 = s.runs("sync_orders_mssql", 1)[0]
    conn = sqlite3.connect(db); conn.executemany(f"INSERT INTO fact_order_hist VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows[12000:20000]]); conn.commit(); conn.close()
    r2 = s.tick(t0 + timedelta(minutes=30)); assert r2[0]["status"] == "success", r2
    run2 = s.runs("sync_orders_mssql", 1)[0]
    assert run1["rows_in"] == 12000 and run2["rows_in"] == 8000 and run2["watermark_from"] == run1["watermark_to"], (run1["rows_in"], run2["rows_in"])
    assert all(x["rows_loaded"] == x["rows_ok"] for x in (run1, run2))
    print(f"dbapi (sqlite stand-in for MSSQL): window1 in={run1['rows_in']} ok={run1['rows_ok']} loaded={run1['rows_loaded']}; window2 in={run2['rows_in']} ok={run2['rows_ok']} loaded={run2['rows_loaded']}; watermark={run2['watermark_to']}")


# ---------------------------------------------------------------- GA360 BigQuery 匯出：串流 → 日批覆蓋 → 72 小時重寫（假 BigQuery）
def ga4_stage(ch: ClickHouseHTTP, work: Path, n_per_tick: int = 20000) -> None:
    from .ga4_source import FakeBigQuery, ga4_row
    bq = FakeBigQuery(); rnd = random.Random(5)
    runner = LocalRunner(ch, bq_clients={"ga_bq": bq}); s = Scheduler(work / "sched_ga4.db", runner)
    t0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)   # 台北 09:00
    base_us = int(t0.timestamp() * 1_000_000)
    src = {"kind": "ga4", "connection": "ga_bq", "dataset": "analytics_1", "tz_offset_hours": 8}
    s.add_task("ga360_intraday", "", cron="*/15 * * * *", mode="incremental", retries=2, backoff_s=60, schema_change="ignore", source={**src, "table_kind": "intraday"}, now=t0)
    s.add_task("ga360_daily", "", cron="30 6 * * *", mode="full", retries=5, backoff_s=600, schema_change="ignore", source={**src, "table_kind": "daily", "day_offset": 1}, now=t0)
    s.add_task("ga360_restate", "", cron="0 7 * * *", mode="incremental", retries=2, backoff_s=600, schema_change="ignore", source={**src, "table_kind": "restate", "day_offset": 2}, now=t0)
    names = ["page_view", "kh_apply", "hq_stockdetail_view", "stock_order_submit", "screen_view", "sip_purchase"]
    mk = lambda i, ts: ga4_row(i, ts, rnd.choice(names), uid=f"v1_{rnd.randint(1, 30000):020d}" if rnd.random() < 0.7 else None, pseudo=f"p{rnd.randint(1, 50000)}",
                               platform=rnd.choice(["WEB", "IOS", "ANDROID"]), seq=i, stream=rnd.choice(["s1", "s2"]))
    tbl = "analytics_1.events_intraday_20261016"; bq.tables[tbl] = []; total = 0
    for k in range(1, 4):                                          # 09:15、09:30、09:45：串流表持續追加，含 5% 亂序（時間戳落在上一窗口）
        for i in range(n_per_tick):
            ts = base_us + (k * 15 - rnd.randint(0, 25 if rnd.random() < 0.05 else 14)) * 60_000_000 - rnd.randint(0, 59_000_000)
            bq.tables[tbl].append(mk(total + i, ts))
        if k == 1:                                                   # 混一筆 event_params 帶 email 的列：守門隔離，不得進 dmp.events
            bad = mk(999_999, base_us + 14 * 60_000_000); bad["event_params"] = bad["event_params"] + [{"key": "email", "value": {"string_value": "fixture@example.test"}}]
            bq.tables[tbl].append(bad)
        total += n_per_tick
        r = s.tick(t0 + timedelta(minutes=15 * k)); assert [x["status"] for x in r] == ["success"], r
    runs = s.runs("ga360_intraday", 3)
    assert sum(x["rows_in"] for x in runs) > total                   # 重疊窗口有重讀
    assert runs[-1]["rows_quarantined"] >= 1 and ch.scalar("SELECT count() FROM dmp.quarantine WHERE task_id='ga360_intraday' AND reason='l4:props.email' AND row=''") >= 1
    from .aggregates import verify
    v = verify(ch, "2026-10-16"); assert v["agg_cnt"] == v["events_cnt"] and v["agg_users"] == v["events_users"] and v["amount_diff"] < 1e-6, v   # 重讀的列入倉前去重，日彙總沒有多算（R12）；同日還有 orders 階段的事件
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    n_intraday = ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_intraday'")
    assert n_intraday == total, (n_intraday, total)                  # 去重後 = 串流表筆數（隔離列不算）
    assert ch.scalar("SELECT count() FROM dmp.events WHERE props.email IS NOT NULL OR toString(props) LIKE '%example.test%'") == 0
    assert ch.scalar("SELECT count() FROM dmp.events WHERE source='ga360_intraday' AND event LIKE 'ga.%'") == 0
    # 隔天 06:30 台北：日批未到 → 0 筆成功（記錄 skipped 原因）；日批到齊（多 300 筆遲到）→ 事件只追加：串流已有的 event_id 保留第一筆（kept_first，不覆蓋），只寫 300 筆遲到
    r = s.tick(t0 + timedelta(days=1, minutes=-30 + 5*60 + 30 - 60))  # 06:30 台北 = 前一日 22:30 UTC
    daily = "analytics_1.events_20261016"
    assert any(x["task_id"] == "ga360_daily" for x in r) and "尚未到齊" in s.runs("ga360_daily", 1)[0]["log"]
    bq.tables[daily] = list(bq.tables[tbl]) + [mk(total + i, base_us + 60 * 60_000_000 + i * 1000) for i in range(300)]
    del bq.tables[tbl]                                               # GA 在日批到齊後刪除串流表
    r = s.run_now("ga360_daily", t0 + timedelta(days=1)); assert r["status"] == "success"
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    n_day = ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source IN ('ga360_intraday','ga360_daily') AND event_date = toDate('2026-10-16')")
    n_daily_src = ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_daily'")
    assert n_day == total + 300 and n_daily_src == 300, (n_day, n_daily_src)             # 串流版本保留，只追加遲到的 300 筆
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_intraday'") == total
    v = verify(ch, "2026-10-16"); assert v["agg_cnt"] == v["events_cnt"] and v["agg_users"] == v["events_users"] and v["amount_diff"] < 1e-6, v   # 不覆蓋、不重寫 → 彙總 = 底表（修正前這裡 agg 會是底表的兩倍多）
    dlog = s.runs("ga360_daily", 1)[0]["log"]; assert f"kept_first={total}" in dlog and "daily_agg rebuilt" not in dlog, dlog[-400:]
    # 隔兩天 07:00：GA 重寫日表（+120 筆遲到）→ restate 偵測列數變化重載
    r = s.run_now("ga360_restate", t0 + timedelta(days=2)); assert r["status"] == "success" and s.runs("ga360_restate", 1)[0]["rows_in"] == total + 300 + 1   # rows_in 含被守門隔離的那一筆
    bq.tables[daily] += [mk(total + 300 + i, base_us + 70 * 60_000_000 + i * 1000) for i in range(120)]
    r = s.run_now("ga360_restate", t0 + timedelta(days=2, minutes=1)); assert s.runs("ga360_restate", 1)[0]["rows_in"] == total + 420 + 1
    r = s.run_now("ga360_restate", t0 + timedelta(days=2, minutes=2)); assert s.runs("ga360_restate", 1)[0]["rows_in"] == 0 and "未變" in s.runs("ga360_restate", 1)[0]["log"]
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_daily'") == 420
    assert ch.scalar("SELECT count() FROM (SELECT event_id FROM dmp.events FINAL WHERE source LIKE 'ga360%' GROUP BY event_id HAVING count() > 1)") == 0
    v = verify(ch, "2026-10-16"); assert v["agg_cnt"] == v["events_cnt"] and v["agg_users"] == v["events_users"] and v["amount_diff"] < 1e-6, v   # 重寫重載（內容相同的列略過、新列照寫）後仍一致
    assert "dedupe: skipped=" in s.runs("ga360_restate", 2)[-1]["log"]
    print(f"ga360 bigquery: intraday 3 ticks in={sum(x['rows_in'] for x in runs):,} (incl. overlap re-reads) → unique {n_intraday:,}; daily kept intraday copies (kept_first={total:,}) + {n_daily_src:,} late; restate +120 → {total + 420:,}; dup event_id after FINAL: 0")
    print("  by source/event:", ch.query("SELECT event, count() FROM dmp.events FINAL WHERE source='ga360_daily' GROUP BY event ORDER BY 2 DESC FORMAT TSV").strip().replace("\n", " | "))


# ---------------------------------------------------------------- 用戶表規模測試：N 萬用戶 × 300 屬性（寬表），日批快照覆蓋、即時微批、dictionary、事件關聯、SAMPLE
KIND_OF = {"string": "enum", "enum": "enum", "number": "num", "bool": "flag", "date": "date", "datetime": "datetime"}
CH_TYPE = {"enum": "LowCardinality(String)", "num": "Nullable(Float64)", "flag": "UInt8", "date": "Nullable(Date)", "datetime": "Nullable(DateTime64(3, 'UTC'))"}
CAST_OF = {"enum": "{v}", "num": "toFloat64OrNull({v})", "flag": "toUInt8OrZero({v})", "date": "toDateOrNull({v})", "datetime": "parseDateTime64BestEffortOrNull({v}, 3)"}


def prop_catalog(n_props: int) -> list[tuple[str, str, bool]]:
    """(name, kind, registered)：前面是 user_properties.yaml 已登記（L0–L2）的屬性，其餘合成到 n_props（模擬還沒進 YAML、以 ALTER 加上的欄位）。"""
    from backend.clickhouse.gen_users_ddl import columns, load_props
    out = [(k, KIND_OF[y], True) for k, _, y in columns(load_props())]
    kinds = ["num", "enum", "flag", "date"]; i = 0
    while len(out) < n_props:
        out.append((f"p{len(out):03d}_{kinds[i % 4]}", kinds[i % 4], False)); i += 1
    return out


def gen_expr(name: str, kind: str, idx: int, seed: int, changed: str | None = None) -> str:
    """以 number 與 seed 決定的確定性值；changed 給了就只對該條件的用戶換值（模擬日批覆蓋）。"""
    enums = {"lifecycle": ["新客", "成長", "成熟", "沉睡", "流失"], "tier": ["P1", "P2", "P3", "P4", "P5"], "age_band": ["<25", "25-34", "35-44", "45-54", "55-64", "65+"],
             "gender": ["M", "F"], "aum_bucket": ["<10萬", "10–50萬", "50–100萬", "100–500萬", "≥500萬"], "pref_market": ["TSE", "OTC", "US", "HK"],
             "branch_id": [f"B{i:03d}" for i in range(60)], "first_utm_source": ["google", "line", "fb", "direct", "edm"]}
    vals = enums.get(name, ["A", "B", "C", "D", "E"]); arr = "[" + ", ".join(f"'{v}'" for v in vals) + "]"
    def val(h: str) -> str:
        return {"enum": f"{arr}[{h} % {len(vals)} + 1]", "num": f"round(({h} % 100000000) / 100, 2)", "flag": f"toUInt8({h} % 2)",
                "date": f"toDate('2024-01-01') + ({h} % 900)", "datetime": f"toDateTime64('2026-01-01 00:00:00', 3) + ({h} % 1000000)"}[kind]
    base = val(f"cityHash64(number, {seed}, {idx})")
    if changed:
        return f"if({changed}, {val(f'cityHash64(number, {seed + 1}, {idx})')}, {base}) AS `{name}`"
    return f"{base} AS `{name}`"


def users_bench(ch: ClickHouseHTTP, n_users: int, n_props: int, n_events: int) -> dict[str, float]:
    import time as _t
    t: dict[str, float] = {}
    cat = prop_catalog(n_props)
    one_id = "concat('v1_', substring(lower(hex(sipHash64(number))), 1, 20)) AS one_id"
    cols = ", ".join(f"`{n}`" for n, _, _ in cat)

    def timed(key: str, sql: str, settings: dict | None = None) -> str:
        t0 = _t.perf_counter(); r = ch.query(sql, settings=settings); t[key] = round(_t.perf_counter() - t0, 2); return r

    # 0) 未登記的合成屬性以 ALTER ADD COLUMN 加上（= 改 YAML 後重產 DDL 的效果；metadata-only）
    adds = ", ".join(f"ADD COLUMN IF NOT EXISTS `{n}` {CH_TYPE[k]}" for n, k, reg in cat if not reg)
    if adds:
        timed("alter_add_columns", f"ALTER TABLE dmp.users_snapshot {adds}"); ch.query(f"ALTER TABLE dmp.users {adds}")
    # 1) 日批：數據中台給寬表 Parquet（300 欄）→ users_snapshot（INSERT SELECT，不 UPDATE）；第 2 天 30% 用戶數值／枚舉／旗標換值
    for day, seed, changed in (("2026-10-15", 11, None), ("2026-10-16", 11, "number % 10 < 3")):
        exprs = ", ".join(gen_expr(n, k, i, seed, changed if k in ("num", "flag", "enum") else None) for i, (n, k, _) in enumerate(cat))
        timed(f"gen_parquet_{day}", f"INSERT INTO FUNCTION file('users_{day}.parquet', 'Parquet') SELECT toDate('{day}') AS snapshot_date, {one_id}, {exprs} FROM numbers({n_users})",
              {"engine_file_truncate_on_insert": 1, "max_insert_threads": 4})
        timed(f"load_snapshot_{day}", f"INSERT INTO dmp.users_snapshot (snapshot_date, one_id, {cols}) SELECT snapshot_date, one_id, {cols} FROM file('users_{day}.parquet', 'Parquet')",
              {"max_insert_threads": 4, "max_threads": 4})
        timed(f"refresh_users_{day}", f"INSERT INTO dmp.users (one_id, {cols}, first_seen, updated_at) SELECT one_id, {cols}, toDateTime64('{day} 05:00:00', 3, 'UTC'), toDateTime64('{day} 05:00:00', 3, 'UTC') FROM dmp.users_snapshot WHERE snapshot_date = toDate('{day}')",
              {"max_insert_threads": 4})
    assert ch.scalar("SELECT count() FROM dmp.users_snapshot") == 2 * n_users
    assert ch.scalar("SELECT count() FROM dmp.users FINAL") == n_users
    diff = ch.scalar("SELECT count() FROM (SELECT one_id, anyIf(aum, snapshot_date=toDate('2026-10-15')) a1, anyIf(aum, snapshot_date=toDate('2026-10-16')) a2 FROM dmp.users_snapshot GROUP BY one_id HAVING a1 != a2)")
    assert 0.25 * n_users < diff < 0.35 * n_users, diff
    assert ch.scalar("SELECT count() FROM dmp.users FINAL WHERE toDate(updated_at) = toDate('2026-10-16')") == n_users
    timed("q_users_final_filter", "SELECT count() FROM dmp.users FINAL WHERE aum_bucket = '≥500萬' AND is_first_trade = 1 FORMAT TSV")
    timed("q_users_final_cold_prop", "SELECT p120_enum, count() FROM dmp.users FINAL GROUP BY 1 ORDER BY 1 FORMAT TSV")
    timed("q_users_history_asof", "SELECT snapshot_date, aum_bucket, count() FROM dmp.users_snapshot GROUP BY 1, 2 ORDER BY 1, 2 FORMAT TSV")
    # 2) 即時零星更新：5% 用戶 profile_set lifecycle → 微批合併進 users（每欄 if(mapContains) 由欄位清單產生）
    k = max(1, n_users // 20)
    ch.query(f"INSERT INTO dmp.user_prop_log (one_id, prop, value, op, ts) SELECT one_id, 'lifecycle', '流失', 'set', toDateTime64('2026-10-16 09:10:00', 3, 'UTC') FROM (SELECT {one_id} FROM numbers({k}))")
    merged = ", ".join(f"if(mapContains(l.m, '{n}'), {CAST_OF[kd].format(v=f'l.m[\'{n}\']')}, u.`{n}`) AS `{n}`" for n, kd, _ in cat)
    timed("microbatch_merge_prop_log", f"""INSERT INTO dmp.users (one_id, {cols}, first_seen, updated_at)
        SELECT u.one_id, {merged}, u.first_seen, l.last_ts
        FROM (SELECT one_id, mapFromArrays(groupArray(prop), groupArray(last_value)) AS m, max(prop_ts) AS last_ts
              FROM (SELECT one_id, prop, argMax(value, ts) AS last_value, max(ts) AS prop_ts FROM dmp.user_prop_log WHERE ts > toDateTime64('2026-10-16 09:00:00', 3, 'UTC') GROUP BY one_id, prop) GROUP BY one_id) AS l
        INNER JOIN dmp.users AS u FINAL ON u.one_id = l.one_id""")
    assert ch.scalar("SELECT count() FROM dmp.users FINAL WHERE lifecycle = '流失' AND toDateTime(updated_at) = toDateTime('2026-10-16 09:10:00', 'UTC')") == k
    assert ch.scalar("SELECT count() FROM dmp.users FINAL") == n_users           # 合併不會多出列
    assert ch.scalar("SELECT count() FROM dmp.users FINAL WHERE toDateTime(updated_at) = toDateTime('2026-10-16 09:10:00', 'UTC') AND aum IS NULL") == 0   # 其他屬性保留
    # 3) dictionary：熱欄位載入時間與記憶體
    timed("dict_reload", "SYSTEM RELOAD DICTIONARY dmp.users_dict")
    row = ch.query("SELECT element_count, round(bytes_allocated/1048576), status FROM system.dictionaries WHERE name='users_dict' FORMAT TSV").strip().split("\t")
    assert int(row[0]) == n_users and row[2] == "LOADED", row
    t["dict_mb"] = float(row[1])
    # 4) 事件 × 用戶屬性：生成 n_events 筆事件（分佈在全部用戶）→ dictGet 分組、JOIN 冷屬性、SAMPLE 一致性、漏斗
    timed("gen_events", f"""INSERT INTO dmp.events (project, time, event, source, one_id, distinct_id, event_id, platform, symbol, props, ingest_time, schema_version, batch_id)
        SELECT 'altatech_dmp', toDateTime64('2026-10-01 01:00:00', 3) + (number % 1296000) * 2, ['hq_stockdetail_view','bond_order','bond_deal','app_login_success','$pageview'][number % 5 + 1], 'bench',
               concat('v1_', substring(lower(hex(sipHash64(number % {n_users}))), 1, 20)) AS oid, oid, lower(hex(cityHash64(number, 7))), ['iOS','Android','Web'][number % 3 + 1],
               toString(2300 + number % 300), CAST('{{}}', 'JSON'), now64(3), 1, 'bench'
        FROM numbers({n_events})""", {"max_insert_threads": 4})
    timed("optimize_events", "OPTIMIZE TABLE dmp.events FINAL")
    timed("q_dictget_group", "SELECT dictGet('dmp.users_dict', 'aum_bucket', one_id_uint) AS g, uniqExact(one_id) FROM dmp.events WHERE event = 'bond_deal' AND source='bench' GROUP BY g ORDER BY g FORMAT TSV")
    timed("q_dictget_filter_funnel", "SELECT level, count() FROM (SELECT one_id, windowFunnel(604800)(toDateTime(time), event='hq_stockdetail_view', event='bond_order', event='bond_deal') AS level FROM dmp.events WHERE source='bench' AND dictGet('dmp.users_dict', 'lifecycle', one_id_uint) = '成長' GROUP BY one_id) GROUP BY level ORDER BY level FORMAT TSV")
    timed("q_join_cold_prop", "SELECT u.g, count() FROM dmp.events e INNER JOIN (SELECT one_id, p120_enum AS g FROM dmp.users FINAL) u ON e.one_id = u.one_id WHERE e.source='bench' AND e.event='bond_order' GROUP BY u.g ORDER BY u.g FORMAT TSV")
    full = ch.scalar("SELECT count() FROM dmp.events WHERE source='bench' AND one_id IN (SELECT DISTINCT one_id FROM dmp.events SAMPLE 1/10 WHERE source='bench')")
    sampled = ch.scalar("SELECT count() FROM dmp.events SAMPLE 1/10 WHERE source='bench'")
    assert full == sampled, (full, sampled)                                       # 抽到的用戶，事件全在樣本裡（按用戶分桶）
    t["sample_ratio"] = round(sampled / n_events, 3)
    timed("q_path_sample", "SELECT count() FROM (SELECT one_id, groupArray(event) FROM dmp.events SAMPLE 1/10 WHERE source='bench' GROUP BY one_id) FORMAT TSV")
    t["events_disk_mb"] = float(ch.query("SELECT round(sum(bytes_on_disk)/1048576) FROM system.parts WHERE database='dmp' AND table='events' AND active").strip())
    t["users_disk_mb"] = float(ch.query("SELECT round(sum(bytes_on_disk)/1048576) FROM system.parts WHERE database='dmp' AND table IN ('users','users_snapshot') AND active").strip())
    t["server_rss_mb"] = float(ch.query("SELECT round(value/1048576) FROM system.asynchronous_metrics WHERE metric='MemoryResident'").strip() or 0)
    return t


def main_users_bench(ch: ClickHouseHTTP) -> None:
    n_users = int(os.environ.get("E2E_USERS", "200000")); n_props = int(os.environ.get("E2E_PROPS", "300")); n_events = int(os.environ.get("E2E_EVENTS", "1000000"))
    print(f"--- users bench: {n_users:,} users × {n_props} props (wide table), {n_events:,} events")
    t = users_bench(ch, n_users, n_props, n_events)
    for k, v in t.items():
        print(f"  {k:<32} {v:>10}")

if __name__ == "__main__":
    sys.exit(main())
