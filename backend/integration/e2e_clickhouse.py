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

from . import modeling
from .runner import ClickHouseHTTP, LocalRunner, local_ddl
from .scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]
HTTP_PORT, TCP_PORT = 18123, 19000


def start_clickhouse(binary: str, workdir: Path) -> subprocess.Popen:
    cfg = workdir / "config.xml"
    cfg.write_text(f"""<clickhouse>
  <logger><level>warning</level><console>0</console><log>{workdir}/ch.log</log><errorlog>{workdir}/ch.err</errorlog></logger>
  <http_port>{HTTP_PORT}</http_port><tcp_port>{TCP_PORT}</tcp_port><listen_host>127.0.0.1</listen_host>
  <path>{workdir}/data/</path><tmp_path>{workdir}/tmp/</tmp_path><user_files_path>{workdir}/user_files/</user_files_path>
  <mark_cache_size>268435456</mark_cache_size><max_server_memory_usage_to_ram_ratio>0.5</max_server_memory_usage_to_ram_ratio>
  <users><default><password></password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
  <profiles><default><allow_experimental_json_type>1</allow_experimental_json_type></default></profiles><quotas><default/></quotas>
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
            ch.query("CREATE TABLE dmp.raw_orders (ORD_NO String, STATUS String, ORD_TIME String, CUST_ID String, ACCT_NO String, STK_CODE String, MKT String, BS_FLAG String, ORD_TYPE String, PRICE String, QTY String, FILL_QTY String, CHANNEL String, FEE String, TAX String, RJ_CODE String) ENGINE = Memory")
            model = modeling.load(ROOT / "integration" / "models.example.yaml")
            staged = []
            for row in all_rows:
                if modeling.T.pii_scan({k: v for k, v in row.items() if k not in ("CUST_NAME", "ID_NO", "PHONE")}): continue
                rr = {k: str(v) for k, v in row.items() if k not in ("CUST_NAME", "ID_NO", "PHONE", "UPDATED_AT")}
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
            sensors_stage(ch, work, int(os.environ.get("E2E_SENSORS_LINES", "200000")), via_http=proc is None)   # 現成伺服器讀不到本機 user_files
            ga4_stage(ch, work, int(os.environ.get("E2E_GA4_PER_TICK", "20000")))
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



# ---------------------------------------------------------------- 神策格式檔（FormatImporter 的輸入）→ DMP；MSSQL（DB-API）排程抽取 → DMP
def sensors_stage(ch: ClickHouseHTTP, work: Path, n_lines: int, rnd_seed: int = 3, via_http: bool = False) -> None:
    import time as _t

    from .sensors_format import SensorsLoader
    rnd = random.Random(rnd_seed); src = work / "sa_20261016.jsonl"
    base = 1_792_000_000_000   # 2026-10-15 前後
    with src.open("w", encoding="utf-8") as f:
        for i in range(n_lines):
            cust = f"C{rnd.randint(1, 50000):06d}"; anon = f"ck-{rnd.randint(1, 80000)}"
            kind = rnd.choices(["track_login", "track_anon", "profile_set", "track_signup", "bad"], [60, 25, 10, 4, 1])[0]
            if kind == "bad":
                f.write("{broken json\n"); continue
            if kind == "track_login":
                ev = rnd.choice(["order_submit", "order_filled", "bond_order", "hq_stockdetail_view", "quote_view", "sip_purchase"])
                f.write(json.dumps({"type": "track", "event": ev, "distinct_id": cust, "time": base + i * 10, "project": "sbp_family",
                                    "properties": {"$is_login_id": True, "$lib": "iOS", "$ip": "10.0.0.1", "$track_id": i, "symbol": str(2300 + rnd.randint(0, 299)), "side": rnd.choice(["buy", "sell"]), "amount": rnd.randint(1000, 900000), "channel": "app"}}, ensure_ascii=False) + "\n")
            elif kind == "track_anon":
                f.write(json.dumps({"type": "track", "event": rnd.choice(["$pageview", "$WebClick", "search_symbol"]), "distinct_id": anon, "time": base + i * 10, "project": "sbp_family",
                                    "properties": {"$is_login_id": False, "$lib": "js", "$url": "https://sbp/x", "$utm_source": rnd.choice(["google", "line", ""]), "$track_id": i}}) + "\n")
            elif kind == "profile_set":
                f.write(json.dumps({"type": "profile_set", "distinct_id": cust, "time": base + i * 10, "properties": {"$is_login_id": True, "lifecycle": rnd.choice(["新客", "成長", "沉睡"]), "aum": rnd.randint(0, 5_000_000), "name": "王小明", "mobile": "0912345678"}}, ensure_ascii=False) + "\n")
            else:
                f.write(json.dumps({"type": "track_signup", "event": "$SignUp", "distinct_id": cust, "original_id": anon, "time": base + i * 10, "properties": {}}) + "\n")
    size_mb = src.stat().st_size / 1048576
    loader = SensorsLoader(ch, local=True)
    t0 = _t.perf_counter(); r = loader.run_file(src, work / "user_files", "sa_20261016", "k_idm", via_http=via_http); dt = _t.perf_counter() - t0
    st = r["stage"]
    assert r["recon_ok"], r
    assert st["bad"] == st["lines"] - st["ok"] and st["bad"] > 0
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND event IN ('order_submit','order_filled','quote_view','search_symbol')") == 0    # 別名已轉正式名
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND one_id LIKE 'C0%'") == 0                                                     # 登入 ID 全部代碼化
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND one_id LIKE 'ck-%'") == ch.scalar("SELECT count() FROM dmp.raw_sensors WHERE batch_id='sa_20261016' AND type='track' AND is_login=0")  # 匿名保留
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id='sa_20261016' AND JSONHas(toString(props), '$ip')") == 0                                       # $ip 不入倉
    assert ch.scalar("SELECT count() FROM dmp.user_prop_log WHERE prop IN ('name','mobile')") == 0                                                                  # L4 不入倉
    assert r["id_map"] == r["signup_anon_ids"], (r["id_map"], r["signup_anon_ids"])   # 同一匿名 ID 多次綁定只留最新（ReplacingMergeTree by (id_type, id_hash)）
    by_ev = ch.query("SELECT event, count() FROM dmp.events WHERE batch_id='sa_20261016' GROUP BY event ORDER BY 2 DESC FORMAT TSV").strip().replace("\n", " | ")
    print(f"sensors format: {st['lines']:,} lines / {size_mb:.0f} MB → ok {st['ok']:,} bad {st['bad']} tokenized {st['tokenized']:,} → raw {r['raw']:,} events {r['events']:,} prop_log {r['prop_log']:,} id_map {r['id_map']:,} in {dt:.1f}s "
          f"(≈ {3800 / max(size_mb, 1) * dt / 60:.0f} min per 3.8 GB)")
    print("  events by name:", by_ev)


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
        total += n_per_tick
        r = s.tick(t0 + timedelta(minutes=15 * k)); assert [x["status"] for x in r] == ["success"], r
    runs = s.runs("ga360_intraday", 3)
    assert sum(x["rows_in"] for x in runs) > total                   # 重疊窗口有重讀
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    n_intraday = ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_intraday'")
    assert n_intraday == total, (n_intraday, total)                  # 去重後 = 串流表筆數
    assert ch.scalar("SELECT count() FROM dmp.events WHERE source='ga360_intraday' AND event LIKE 'ga.%'") == 0
    # 隔天 06:30 台北：日批未到 → 0 筆成功（記錄 skipped 原因）；日批到齊（多 300 筆遲到 + 欄位更完整）→ 整天重載覆蓋串流版本
    r = s.tick(t0 + timedelta(days=1, minutes=-30 + 5*60 + 30 - 60))  # 06:30 台北 = 前一日 22:30 UTC
    daily = "analytics_1.events_20261016"
    assert any(x["task_id"] == "ga360_daily" for x in r) and "尚未到齊" in s.runs("ga360_daily", 1)[0]["log"]
    bq.tables[daily] = list(bq.tables[tbl]) + [mk(total + i, base_us + 60 * 60_000_000 + i * 1000) for i in range(300)]
    del bq.tables[tbl]                                               # GA 在日批到齊後刪除串流表
    r = s.run_now("ga360_daily", t0 + timedelta(days=1)); assert r["status"] == "success"
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    n_day = ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source IN ('ga360_intraday','ga360_daily') AND event_date = toDate('2026-10-16')")
    n_daily_src = ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_daily'")
    assert n_day == total + 300 and n_daily_src == total + 300, (n_day, n_daily_src)     # 日批覆蓋了全部串流列（同 event_id，ingest_time 較新）
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_intraday'") == 0
    # 隔兩天 07:00：GA 重寫日表（+120 筆遲到）→ restate 偵測列數變化重載
    r = s.run_now("ga360_restate", t0 + timedelta(days=2)); assert r["status"] == "success" and s.runs("ga360_restate", 1)[0]["rows_in"] == total + 300
    bq.tables[daily] += [mk(total + 300 + i, base_us + 70 * 60_000_000 + i * 1000) for i in range(120)]
    r = s.run_now("ga360_restate", t0 + timedelta(days=2, minutes=1)); assert s.runs("ga360_restate", 1)[0]["rows_in"] == total + 420
    r = s.run_now("ga360_restate", t0 + timedelta(days=2, minutes=2)); assert s.runs("ga360_restate", 1)[0]["rows_in"] == 0 and "未變" in s.runs("ga360_restate", 1)[0]["log"]
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_daily'") == total + 420
    assert ch.scalar("SELECT count() FROM (SELECT event_id FROM dmp.events FINAL WHERE source LIKE 'ga360%' GROUP BY event_id HAVING count() > 1)") == 0
    print(f"ga360 bigquery: intraday 3 ticks in={sum(x['rows_in'] for x in runs):,} (incl. overlap re-reads) → unique {n_intraday:,}; daily replaced all ({n_daily_src:,}); restate +120 → {total + 420:,}; dup event_id after FINAL: 0")
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
        timed(f"refresh_users_{day}", f"INSERT INTO dmp.users (one_id, {cols}, first_seen, updated_at) SELECT one_id, {cols}, toDateTime64('{day} 05:00:00', 3), toDateTime64('{day} 05:00:00', 3) FROM dmp.users_snapshot WHERE snapshot_date = toDate('{day}')",
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
    ch.query(f"INSERT INTO dmp.user_prop_log (one_id, prop, value, op, ts) SELECT one_id, 'lifecycle', '流失', 'set', toDateTime64('2026-10-16 09:10:00', 3) FROM (SELECT {one_id} FROM numbers({k}))")
    merged = ", ".join(f"if(mapContains(l.m, '{n}'), {CAST_OF[kd].format(v=f'l.m[\'{n}\']')}, u.`{n}`) AS `{n}`" for n, kd, _ in cat)
    timed("microbatch_merge_prop_log", f"""INSERT INTO dmp.users (one_id, {cols}, first_seen, updated_at)
        SELECT u.one_id, {merged}, u.first_seen, l.last_ts
        FROM (SELECT one_id, mapFromArrays(groupArray(prop), groupArray(last_value)) AS m, max(prop_ts) AS last_ts
              FROM (SELECT one_id, prop, argMax(value, ts) AS last_value, max(ts) AS prop_ts FROM dmp.user_prop_log WHERE ts > toDateTime64('2026-10-16 09:00:00', 3) GROUP BY one_id, prop) GROUP BY one_id) AS l
        INNER JOIN dmp.users AS u FINAL ON u.one_id = l.one_id""")
    assert ch.scalar("SELECT count() FROM dmp.users FINAL WHERE lifecycle = '流失' AND toDateTime(updated_at) = toDateTime('2026-10-16 09:10:00')") == k
    assert ch.scalar("SELECT count() FROM dmp.users FINAL") == n_users           # 合併不會多出列
    assert ch.scalar("SELECT count() FROM dmp.users FINAL WHERE toDateTime(updated_at) = toDateTime('2026-10-16 09:10:00') AND aum IS NULL") == 0   # 其他屬性保留
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
