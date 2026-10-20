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
    assert r["id_map"] == r["signup_anon_ids"], (r["id_map"], r["signup_anon_ids"])   # 同一匿名 ID 多次綁定只留最新（ReplacingMergeTree by (id_type, id_hash)）
    by_ev = ch.query("SELECT event, count() FROM dmp.events WHERE batch_id='sa_20261016' GROUP BY event ORDER BY 2 DESC FORMAT TSV").strip().replace("\n", " | ")
    print(f"legacy format: {st['lines']:,} lines / {size_mb:.0f} MB → ok {st['ok']:,} bad {st['bad']} tokenized {st['tokenized']:,} → raw {r['raw']:,} events {r['events']:,} prop_log {r['prop_log']:,} id_map {r['id_map']:,} in {dt:.1f}s "
          f"(≈ {3800 / max(size_mb, 1) * dt / 60:.0f} min per 3.8 GB)")
    print("  events by name:", by_ev)


def agg_stage(ch: ClickHouseHTTP) -> None:
    """R12 回歸：同一事件直接寫兩次（繞過載入路徑）→ 底表 FINAL 1 筆、MV 彙總 2 筆（Codex 的重現）；重建該日分區後彙總回到 1。
    再走載入路徑（LocalRunner GA4）重插同一事件：內容相同 → 不寫入，彙總不變；內容不同（utm 補齊）→ 寫入新版本 + 該日重建，彙總仍 = 1。"""
    from .aggregates import dedupe, purge_stale, rebuild, verify
    from .ga4_source import FakeBigQuery, ga4_row
    row = {"project": "altatech_dmp", "time": "2026-09-01 01:00:00.000", "event": "bond_deal", "source": "e2e_dup", "one_id": "v1_dup", "entity_type": "", "entity_id": "", "distinct_id": "",
           "session_id": "", "event_id": "agg-dup-1", "platform": "iOS", "app_id": "", "symbol": "2330", "side": "B", "channel": "app", "amount": 100.0, "utm_source": "", "utm_campaign": "",
           "props": {}, "ingest_time": "2026-09-01 01:00:00.000", "schema_version": 1, "late": 0, "batch_id": "dup1"}
    ch.insert_json("dmp.events", [row]); ch.insert_json("dmp.events", [{**row, "ingest_time": "2026-09-01 01:00:01.000", "batch_id": "dup2"}])
    ch.query("OPTIMIZE TABLE dmp.events PARTITION '202609' FINAL")
    v = verify(ch, "2026-09-01"); assert v["events_cnt"] == 1 and v["agg_cnt"] == 2, v                      # 修正前的狀態：MV 每次 INSERT 都累加
    assert rebuild(ch, ["2026-09-01"]) == ["2026-09-01"]
    v = verify(ch, "2026-09-01"); assert v["events_cnt"] == 1 and v["agg_cnt"] == 1 and v["agg_users"] == 1 and v["amount_diff"] < 1e-6, v
    d0 = dedupe(ch, [row]); assert d0.rows == [] and d0.skipped == 1 and d0.rebuild == set(), d0          # 載入路徑：一模一樣 → no-op
    d0 = dedupe(ch, [{**row, "utm_source": "google"}]); assert len(d0.rows) == 1 and d0.rebuild == {"2026-09-01"} and d0.stale == [], d0   # 內容變 → 新版本 + 重建
    bq = FakeBigQuery(); runner = LocalRunner(ch, bq_clients={"bq": bq})
    base_us = int(datetime(2026, 9, 2, 0, 0, tzinfo=UTC).timestamp() * 1_000_000)
    bq.tables["ds.events_intraday_20260902"] = [ga4_row(i, base_us + i * 60_000_000, "kh_apply", uid="v1_abc") for i in range(50)]
    task = {"id": "agg_ga4", "source": {"kind": "ga4", "connection": "bq", "dataset": "ds", "table_kind": "intraday"}}
    now = datetime(2026, 9, 2, 1, 0, tzinfo=UTC); log: list[str] = []
    r1 = runner.run(task, watermark=None, batch_id="g1", known_fields=None, now=now, log=log.append)
    r2 = runner.run(task, watermark=None, batch_id="g2", known_fields=None, now=now + timedelta(minutes=15), log=log.append)   # 整批重讀（重試）
    assert r1.rows_loaded == 50 and r2.rows_loaded == 50 and ch.scalar("SELECT count() FROM dmp.events WHERE batch_id = 'g2'") == 0, (r1, r2)   # 第二次一筆都沒寫
    v = verify(ch, "2026-09-02"); assert v["events_cnt"] == v["agg_cnt"] == 50 and v["agg_users"] == 1, v
    for ev in bq.tables["ds.events_intraday_20260902"]: ev["session_traffic_source_last_click"]["manual_campaign"]["source"] = "google"   # 內容變：utm_source 補齊
    runner.run(task, watermark=None, batch_id="g3", known_fields=None, now=now + timedelta(minutes=30), log=log.append)
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id = 'g3'") == 50 and "daily_agg rebuilt for ['2026-09-02']" in " ".join(log), log[-3:]
    ch.query("OPTIMIZE TABLE dmp.events PARTITION '202609' FINAL")
    v = verify(ch, "2026-09-02"); assert v["events_cnt"] == v["agg_cnt"] == 50 and v["agg_users"] == 1, v   # 新版本寫入、該日重建 → 彙總仍 = 去重後底表
    assert ch.scalar("SELECT countMerge(cnt) FROM dmp.events_daily_agg WHERE event_date = toDate('2026-09-02') AND utm_source = 'google'") == 50
    # 複審 P1：只改非彙總欄位也要入倉；排序鍵變了（跨日／同日時間更正、歸戶改寫）要清掉舊版本，舊日期歸零、新日期一筆
    fx = {**row, "event_id": "agg-fix-1", "props": {"market": "TSE"}, "batch_id": "f0"}
    ch.insert_json("dmp.events", [fx]); rebuild(ch, ["2026-09-01"])
    d1 = dedupe(ch, [{**fx, "props": {"market": "US"}, "utm_campaign": "new_campaign", "ingest_time": "2026-09-01 02:00:00.000", "batch_id": "f1"}])
    assert len(d1.rows) == 1 and d1.skipped == 0 and d1.rebuild == {"2026-09-01"} and d1.stale == [], d1                        # props／utm_campaign 變 → 寫入 + 重建，排序鍵沒變不清
    ch.insert_json("dmp.events", d1.rows); rebuild(ch, d1.rebuild); ch.query("OPTIMIZE TABLE dmp.events PARTITION '202609' FINAL")
    assert ch.query("SELECT props.market, utm_campaign FROM dmp.events FINAL WHERE event_id = 'agg-fix-1' FORMAT TSV").strip() == "US\tnew_campaign"
    v = verify(ch, "2026-09-01"); assert v["events_cnt"] == v["agg_cnt"] == 2, v                                                   # agg-dup-1 + agg-fix-1
    assert dedupe(ch, [{**fx, "props": {"market": "US"}, "utm_campaign": "new_campaign", "batch_id": "f1b"}]).skipped == 1       # 同版本重讀 → 略過
    fx2 = {**fx, "props": {"market": "US"}, "utm_campaign": "new_campaign", "time": "2026-09-02 10:00:00.100", "ingest_time": "2026-09-01 03:00:00.000", "batch_id": "f2"}   # 跨日時間更正
    d2 = dedupe(ch, [fx2]); assert len(d2.rows) == 1 and d2.rebuild == {"2026-09-01", "2026-09-02"} and d2.stale == [("agg-fix-1", "2026-09-01 01:00:00.000", "v1_dup")], d2
    assert purge_stale(ch, d2.stale) == 1; ch.insert_json("dmp.events", d2.rows); rebuild(ch, d2.rebuild)
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE event_id = 'agg-fix-1'") == 1 and ch.scalar("SELECT count() FROM dmp.events FINAL WHERE event_id = 'agg-fix-1' AND event_date = toDate('2026-09-02')") == 1
    v1, v2 = verify(ch, "2026-09-01"), verify(ch, "2026-09-02"); assert v1["events_cnt"] == v1["agg_cnt"] == 1 and v2["events_cnt"] == v2["agg_cnt"] == 51, (v1, v2)   # 舊日期少一筆、新日期多一筆（50 筆 GA4 + 1）
    fx3 = {**fx2, "time": "2026-09-02 11:00:00.100", "ingest_time": "2026-09-01 04:00:00.000", "batch_id": "f3"}                 # 同日時間更正
    d3 = dedupe(ch, [fx3]); assert d3.stale == [("agg-fix-1", "2026-09-02 10:00:00.100", "v1_dup")] and d3.rebuild == {"2026-09-02"}, d3
    purge_stale(ch, d3.stale); ch.insert_json("dmp.events", d3.rows); rebuild(ch, d3.rebuild)
    assert ch.query("SELECT toString(time) FROM dmp.events FINAL WHERE event_id = 'agg-fix-1' FORMAT TSV").strip() == "2026-09-02 11:00:00.100"
    fx4 = {**fx3, "one_id": "v1_rewritten", "ingest_time": "2026-09-01 05:00:00.000", "batch_id": "f4"}                            # 歸戶改寫
    d4 = dedupe(ch, [fx4]); assert d4.stale == [("agg-fix-1", "2026-09-02 11:00:00.100", "v1_dup")], d4
    purge_stale(ch, d4.stale); ch.insert_json("dmp.events", d4.rows); rebuild(ch, d4.rebuild)
    assert ch.query("SELECT one_id FROM dmp.events FINAL WHERE event_id = 'agg-fix-1' FORMAT TSV").strip() == "v1_rewritten"
    v2 = verify(ch, "2026-09-02"); assert v2["events_cnt"] == v2["agg_cnt"] == 51 and v2["events_users"] == v2["agg_users"] == 2, v2   # v1_abc + v1_rewritten
    assert ch.scalar("SELECT uniqMerge(users) FROM dmp.events_daily_agg WHERE event_date = toDate('2026-09-02') AND event = 'bond_deal'") == 1
    # 複審 P1：寫入與重建之間中斷 → 重試不能把錯的彙總當成功；再複審 P1：多個寫入者／重建交錯不能把別人的恢復工作吃掉。
    # 標記日誌（events_daily_agg_pending）：寫入者 begin（phase 0 + 租約）→ 寫入 → landed（phase 1）；重建只確認落地早於自己快照的標記，
    # 快照之後有人落地就再快照一次。測試把租約與餘量縮短（LEASE 4 秒、餘量 1 秒）好等得到過期。
    import time

    from . import aggregates as agg_mod
    from .aggregates import LeaseExpired, Rebuild, begin, event_date_of, gc_marks, pending_dates
    assert pending_dates(ch) == [], pending_dates(ch)                                                                         # 前面的正常路徑沒有殘留標記
    lease0, margin0 = agg_mod.LEASE_S, agg_mod.MARGIN_S; agg_mod.LEASE_S, agg_mod.MARGIN_S = 4, 1
    settle = lambda: time.sleep(agg_mod.MARGIN_S + 0.5)                                                                       # 等過餘量：落地時間要早於下一次快照減餘量才會被確認
    expire = lambda: time.sleep(agg_mod.LEASE_S + agg_mod.MARGIN_S + 0.5)                                                     # 等租約過期
    no_tmp = lambda: ch.scalar("SELECT count() FROM system.tables WHERE database = 'dmp' AND name LIKE 'events_daily_agg_stage_%'") == 0
    cr = {**row, "event_id": "agg-crash-1", "time": "2026-09-03 01:00:00.000", "amount": 1000.0, "props": {"market": "TSE"}, "batch_id": "c0", "ingest_time": "2026-09-03 01:00:00.000"}
    ch.insert_json("dmp.events", [cr]); rebuild(ch, ["2026-09-03"]); v = verify(ch, "2026-09-03"); assert v["events_cnt"] == v["agg_cnt"] == 1 and v["amount_diff"] < 1e-6, v

    def protocol(new_rows: list[dict[str, Any]], batch: str, crash_after: str | None = None) -> Any:
        """與 runner 相同的順序：dedupe → begin（change + touch 標記）→ 清舊版本 → 寫入 → landed → 重建（含被別人換過分區的日期）；crash_after 指定在哪一步之後中斷"""
        d = dedupe(ch, new_rows); m = begin(ch, d.rebuild, batch, touch=[event_date_of(r) for r in d.rows])
        if crash_after == "mark": return d
        if d.stale: m.check(); purge_stale(ch, d.stale)
        if crash_after == "purge": return d
        if d.rows: m.check(); ch.insert_json("dmp.events", d.rows)
        if crash_after == "insert": return d                                                                                  # 落地了但還沒記 phase 1（標記要等租約過期才會被確認）
        m.landed()
        if crash_after == "landed": return d
        if crash_after == "stage":                                                                                            # REPLACE 之前中斷：私有暫存表留著
            r = Rebuild(ch, sorted(d.rebuild | set(pending_dates(ch)))[0], batch); r.mark(); r.snapshot(); return d
        rebuild(ch, d.rebuild | m.settle(), batch); return d
    # (a) 落地後、重建前中斷（Codex 的重現）：props 改了 → 新版本進倉、MV 多算 → agg=2、金額差 1000；重試整批內容相同、一筆不寫，仍要補重建並確認標記
    c1 = {**cr, "props": {"market": "US"}, "ingest_time": "2026-09-03 02:00:00.000", "batch_id": "c1"}
    d = protocol([c1], "c1", crash_after="landed"); assert len(d.rows) == 1 and pending_dates(ch) == ["2026-09-03"], (d, pending_dates(ch))
    v = verify(ch, "2026-09-03"); assert v["events_cnt"] == 1 and v["agg_cnt"] == 2 and abs(v["amount_diff"] - 1000) < 1e-6, v         # 中斷留下的錯誤狀態
    settle(); d = protocol([{**c1, "batch_id": "c1r"}], "c1r"); assert d.skipped == 1 and d.rebuild == set(), d
    v = verify(ch, "2026-09-03"); assert v["events_cnt"] == v["agg_cnt"] == 1 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [], (v, pending_dates(ch))
    # (a2) 寫入後、記 phase 1 前中斷：標記只有 phase 0 → 每次重建都會重做那天但不確認它（寫入者可能還在寫）；租約過期後才確認
    c1b = {**c1, "props": {"market": "HK"}, "ingest_time": "2026-09-03 02:30:00.000", "batch_id": "c1b"}
    protocol([c1b], "c1b", crash_after="insert"); assert verify(ch, "2026-09-03")["agg_cnt"] == 2
    settle(); protocol([{**c1b, "batch_id": "c1br"}], "c1br"); v = verify(ch, "2026-09-03"); assert v["events_cnt"] == v["agg_cnt"] == 1 and v["amount_diff"] < 1e-6 and pending_dates(ch) == ["2026-09-03"], (v, pending_dates(ch))
    expire(); rebuild(ch, []); assert pending_dates(ch) == [] and verify(ch, "2026-09-03")["agg_cnt"] == 1
    # (b) 跨日更正：清舊版本後、寫入前中斷 → 底表已沒有這筆、舊日彙總還留著；重試時倉裡沒舊版本、推不出舊日期，舊日的修復靠標記：舊日 0、新日 1、金額正確
    c2 = {**c1b, "time": "2026-09-04 01:00:00.000", "ingest_time": "2026-09-03 03:00:00.000", "batch_id": "c2"}
    d = protocol([c2], "c2", crash_after="purge"); assert d.stale and d.rebuild == {"2026-09-03", "2026-09-04"} and pending_dates(ch) == ["2026-09-03", "2026-09-04"], (d, pending_dates(ch))
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE event_id = 'agg-crash-1'") == 0 and verify(ch, "2026-09-03")["agg_cnt"] == 1
    settle(); d = protocol([{**c2, "batch_id": "c2r"}], "c2r"); assert len(d.rows) == 1 and d.rebuild == set() and d.stale == [], d          # 倉裡已沒有舊版本 → 當新事件
    v3, v4 = verify(ch, "2026-09-03"), verify(ch, "2026-09-04")
    assert v3["events_cnt"] == v3["agg_cnt"] == 0 and v4["events_cnt"] == v4["agg_cnt"] == 1 and v3["amount_diff"] < 1e-6 and v4["amount_diff"] < 1e-6, (v3, v4)
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE event_id = 'agg-crash-1'") == 1
    expire(); rebuild(ch, []); assert pending_dates(ch) == [], pending_dates(ch)                                              # c2 的 phase 0 標記租約過期後被確認
    # (c) REPLACE 前中斷（私有暫存表留著）、(d) REPLACE 後、確認前中斷：重試只是再算一次，結果不變、暫存表清掉、標記確認
    c3 = {**c2, "props": {"market": "JP"}, "ingest_time": "2026-09-03 04:00:00.000", "batch_id": "c3"}
    protocol([c3], "c3", crash_after="stage"); assert pending_dates(ch) == ["2026-09-04"] and verify(ch, "2026-09-04")["agg_cnt"] == 2 and not no_tmp()
    settle(); protocol([{**c3, "batch_id": "c3r"}], "c3r"); v4 = verify(ch, "2026-09-04"); assert v4["events_cnt"] == v4["agg_cnt"] == 1 and v4["amount_diff"] < 1e-6, v4
    ch.query("DROP TABLE IF EXISTS " + ch.query("SELECT name FROM system.tables WHERE database = 'dmp' AND name LIKE 'events_daily_agg_stage_%' LIMIT 1 FORMAT TSV").strip().join(["dmp.", ""]))   # 中斷留下的暫存表（營運清理）
    expire(); rebuild(ch, []); assert pending_dates(ch) == [] and no_tmp(), pending_dates(ch)
    r = Rebuild(ch, "2026-09-04", "c4"); r.mark(); r.snapshot(); r.replace()                                                  # (d) REPLACE 後、確認前中斷
    assert pending_dates(ch) == ["2026-09-04"]; settle(); rebuild(ch, []); v4 = verify(ch, "2026-09-04"); assert v4["agg_cnt"] == 1 and v4["amount_diff"] < 1e-6
    expire(); rebuild(ch, []); assert pending_dates(ch) == [], pending_dates(ch)
    # (e) 真的 runner（GA4 路徑）：rebuild 丟例外模擬中斷 → 下一次同內容的 run 一筆不寫，但補重建並寫進 log
    for ev in bq.tables["ds.events_intraday_20260902"]: ev["session_traffic_source_last_click"]["manual_campaign"]["source"] = "bing"      # 內容再變
    def boom(*a: Any, **k: Any) -> Any: raise RuntimeError("simulated crash before rebuild")
    orig = agg_mod.rebuild; agg_mod.rebuild = boom
    try: runner.run(task, watermark=None, batch_id="g4", known_fields=None, now=now + timedelta(minutes=45), log=log.append)
    except RuntimeError: pass
    finally: agg_mod.rebuild = orig
    assert pending_dates(ch) == ["2026-09-02"] and ch.scalar("SELECT count() FROM dmp.events WHERE batch_id = 'g4'") == 50 and verify(ch, "2026-09-02")["agg_cnt"] == 101, verify(ch, "2026-09-02")
    settle(); runner.run(task, watermark=None, batch_id="g5", known_fields=None, now=now + timedelta(minutes=60), log=log.append)
    assert ch.scalar("SELECT count() FROM dmp.events WHERE batch_id = 'g5'") == 0 and any("incl. pending from an interrupted run" in x for x in log[-4:]), log[-4:]
    v = verify(ch, "2026-09-02"); assert v["events_cnt"] == v["agg_cnt"] == 51 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [], (v, pending_dates(ch))
    # (f) 再複審 P1 的交錯（barrier 式）：A 重建快照（amount=1000）後暫停 → B 把同事件改成 2000、標記、寫入、落地後中斷 → A 換分區。
    #     A 看得到「有人在我快照後落地」→ 再快照一次才確認；B 的標記不會被舊快照吃掉。B 原樣重試：一筆不寫，彙總已對、標記空。
    w = {**row, "event_id": "agg-race-1", "time": "2026-09-05 01:00:00.000", "amount": 1000.0, "batch_id": "r0", "ingest_time": "2026-09-05 01:00:00.000"}
    ch.insert_json("dmp.events", [w]); rebuild(ch, ["2026-09-05"]); assert verify(ch, "2026-09-05")["amount_diff"] < 1e-6
    A = Rebuild(ch, "2026-09-05", "A"); A.mark(); A.snapshot()                                                                # A：快照後暫停
    wb = {**w, "amount": 2000.0, "ingest_time": "2026-09-05 02:00:00.000", "batch_id": "B"}
    dB = dedupe(ch, [wb]); assert dB.rebuild == {"2026-09-05"}; mB = begin(ch, dB.rebuild, "B"); mB.check(); ch.insert_json("dmp.events", dB.rows); mB.landed()   # B：落地後中斷
    A.replace(); assert A.newer_landed(), "A 必須看到 B 在它快照後落地"                                                       # A：拿舊快照換分區（金額還是 1000）
    v = verify(ch, "2026-09-05"); assert abs(v["amount_diff"] - 1000) < 1e-6, v                                                # 此刻彙總是錯的（舊快照）
    settle(); A.snapshot(); A.replace(); assert not A.newer_landed(); acked = A.ack()                                           # A：再快照 → 對了 → 確認（含 B）
    v = verify(ch, "2026-09-05"); assert v["events_cnt"] == v["agg_cnt"] == 1 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [] and mB.ids["2026-09-05"] in acked, (v, acked)
    settle(); d = protocol([{**wb, "batch_id": "Br"}], "Br"); assert d.skipped == 1 and verify(ch, "2026-09-05")["amount_diff"] < 1e-6 and pending_dates(ch) == []   # B 原樣重試
    # (f2) 同一交錯但 A 不做「再快照」（舊協定的行為）：確認只會確認落地早於 A 快照的標記 → B 的標記留著 → 下一次重建修好
    A = Rebuild(ch, "2026-09-05", "A2"); A.mark(); A.snapshot()
    wc = {**wb, "amount": 3000.0, "ingest_time": "2026-09-05 03:00:00.000", "batch_id": "C"}
    dC = dedupe(ch, [wc]); mC = begin(ch, dC.rebuild, "C"); ch.insert_json("dmp.events", dC.rows); mC.landed()
    A.replace(); acked = A.ack(); assert mC.ids["2026-09-05"] not in acked and pending_dates(ch) == ["2026-09-05"], (acked, pending_dates(ch))   # C 沒被吃掉
    assert abs(verify(ch, "2026-09-05")["amount_diff"] - 1000) < 1e-6
    settle(); rebuild(ch, []); v = verify(ch, "2026-09-05"); assert v["agg_cnt"] == 1 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [], v
    # (g) 兩個重建同一天競爭 + 中間有寫入者落地：A 快照 → W 落地 → B 完整重建（含確認 W）→ A 用舊快照換分區 → A 看到更新的落地 → 再快照。私有暫存表互不干擾、沒有空來源誤刪分區
    A = Rebuild(ch, "2026-09-05", "A3"); A.mark(); A.snapshot()
    wd = {**wc, "amount": 4000.0, "ingest_time": "2026-09-05 04:00:00.000", "batch_id": "W"}
    dW = dedupe(ch, [wd]); mW = begin(ch, dW.rebuild, "W"); ch.insert_json("dmp.events", dW.rows); mW.landed()
    settle(); B = Rebuild(ch, "2026-09-05", "B3"); assert mW.ids["2026-09-05"] in B.run() and verify(ch, "2026-09-05")["amount_diff"] < 1e-6
    A.replace(); assert abs(verify(ch, "2026-09-05")["amount_diff"] - 1000) < 1e-6 and A.newer_landed()                      # A 的舊快照蓋掉了 B 的正確結果，但 A 自己會發現
    A.snapshot(); A.replace(); assert not A.newer_landed(); A.ack()
    v = verify(ch, "2026-09-05"); assert v["events_cnt"] == v["agg_cnt"] == 1 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [] and no_tmp(), v
    # (h) 租約圍籬：標記後拖太久的寫入者不准再寫；它的標記在租約過期後由任何重建確認
    slow = begin(ch, ["2026-09-05"], "slow"); slow.check(); time.sleep(agg_mod.LEASE_S)
    try: slow.check(); raise AssertionError("租約過期要擋")
    except LeaseExpired: pass
    assert pending_dates(ch) == ["2026-09-05"]; expire(); rebuild(ch, []); assert pending_dates(ch) == [] and no_tmp()
    # (i) 只寫「全新事件」的寫入者（沒有版本變更、不需要重建）夾在 A 的快照與換分區之間：MV 加上去的被 A 的舊快照蓋掉 →
    #     touch 標記讓 A 看得到「有人在我快照後落地」→ 再快照；寫入者落地後也看得到 A 換過分區 → 自己再重建一次。新事件不會被吃掉
    base6 = {**row, "event_id": "agg-new-0", "time": "2026-09-06 01:00:00.000", "amount": 100.0, "batch_id": "n0", "ingest_time": "2026-09-06 01:00:00.000"}
    ch.insert_json("dmp.events", [base6]); rebuild(ch, ["2026-09-06"]); assert verify(ch, "2026-09-06")["agg_cnt"] == 1
    A = Rebuild(ch, "2026-09-06", "A6"); A.mark(); A.snapshot()
    n1 = {**base6, "event_id": "agg-new-1", "time": "2026-09-06 02:00:00.000", "amount": 500.0, "one_id": "v1_new", "batch_id": "N1", "ingest_time": "2026-09-06 02:00:00.000"}
    dN = dedupe(ch, [n1]); assert len(dN.rows) == 1 and dN.rebuild == set(), dN                                                # 全新事件：不需要重建
    mN = begin(ch, dN.rebuild, "N1", touch=[event_date_of(r) for r in dN.rows]); assert list(mN.touch) == ["2026-09-06"]
    mN.check(); ch.insert_json("dmp.events", dN.rows); mN.landed()
    A.replace(); v = verify(ch, "2026-09-06"); assert v["events_cnt"] == 2 and v["agg_cnt"] == 1 and abs(v["amount_diff"] - 500) < 1e-6, v   # 舊快照把新事件蓋掉了
    assert A.newer_landed(); settle(); A.snapshot(); A.replace(); assert not A.newer_landed(); A.ack()
    v = verify(ch, "2026-09-06"); assert v["events_cnt"] == v["agg_cnt"] == 2 and v["events_users"] == v["agg_users"] == 2 and v["amount_diff"] < 1e-6, v
    assert mN.overlapped() == {"2026-09-06"} and pending_dates(ch) == [], pending_dates(ch)
    # (j) 寫入者跨過 A 的整段重建：A 快照 → 寫入者寫入（還沒落地）→ A 換分區、沒看到落地、確認收工 → 寫入者落地，發現 A 換過分區 → 自己重建
    mW2 = begin(ch, set(), "N2", touch=["2026-09-06"])
    A = Rebuild(ch, "2026-09-06", "A7"); A.mark(); A.snapshot()
    n2 = {**n1, "event_id": "agg-new-2", "time": "2026-09-06 03:00:00.000", "amount": 700.0, "one_id": "v1_new2", "batch_id": "N2", "ingest_time": "2026-09-06 03:00:00.000"}
    mW2.check(); ch.insert_json("dmp.events", [n2])
    A.replace(); assert not A.newer_landed(); A.ack()
    v = verify(ch, "2026-09-06"); assert v["events_cnt"] == 3 and v["agg_cnt"] == 2 and abs(v["amount_diff"] - 700) < 1e-6, v       # A 的快照沒包到 n2
    mW2.landed(); ov = mW2.settle(); assert ov == {"2026-09-06"}, ov; rebuild(ch, ov, "N2")
    v = verify(ch, "2026-09-06"); assert v["events_cnt"] == v["agg_cnt"] == 3 and v["events_users"] == v["agg_users"] == 3 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [], v
    # (k) 寫入者寫到一半掛了（touch 沒落地）：租約過期後，任何一次重建都會保守重做那天
    mW3 = begin(ch, set(), "N3", touch=["2026-09-06"]); A = Rebuild(ch, "2026-09-06", "A8"); A.mark(); A.snapshot()
    n3 = {**n1, "event_id": "agg-new-3", "time": "2026-09-06 04:00:00.000", "amount": 900.0, "one_id": "v1_new3", "batch_id": "N3", "ingest_time": "2026-09-06 04:00:00.000"}
    mW3.check(); ch.insert_json("dmp.events", [n3]); A.replace(); A.ack()                                                     # 寫入者在這裡掛了
    assert verify(ch, "2026-09-06")["agg_cnt"] == 3 and pending_dates(ch) == []                                               # 租約還沒過期：還不算待重建
    expire(); assert pending_dates(ch) == ["2026-09-06"]; rebuild(ch, [])
    v = verify(ch, "2026-09-06"); assert v["events_cnt"] == v["agg_cnt"] == 4 and v["amount_diff"] < 1e-6 and pending_dates(ch) == [] and no_tmp(), v
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
        settle(); d = protocol([{**lv, "batch_id": f"L-{variant}-r"}], f"L-{variant}-r")                                     # 相同 payload 重試
        assert d.skipped == 1 and d.rows == [], d
        v = verify(ch, "2026-09-07"); assert v["events_cnt"] == v["agg_cnt"] and v["events_users"] == v["agg_users"] and v["amount_diff"] < 1e-6 and pending_dates(ch) == [], (variant, v, pending_dates(ch))
    # (m) 正常的只寫新事件批次：落地 → settle 自己確認，不留待重建、不觸發重建
    n_ok = {**l1, "event_id": "agg-l-ok", "batch_id": "L-ok"}; d = protocol([n_ok], "L-ok"); assert len(d.rows) == 1 and pending_dates(ch) == [], pending_dates(ch)
    v = verify(ch, "2026-09-07"); assert v["events_cnt"] == v["agg_cnt"] == 5 and v["amount_diff"] < 1e-6, v
    agg_mod.LEASE_S, agg_mod.MARGIN_S = lease0, margin0
    print("daily_agg idempotency: direct double insert → agg 2 vs base 1 → rebuild → 1; runner re-read → skipped 50; changed content → rebuilt → 50; "
          "props-only change stored; cross-day/same-day/one_id corrections purge stale versions (old date 0, new date 1); "
          "crash after landed/insert/purge/stage/replace → marker journal → retry repairs (base == agg, amount_diff 0, markers acked or expired); runner crash before rebuild → next run repairs; "
          "writer/rebuild interleaving (A snapshot → B lands → A replace) re-snapshots and never acks B early; two rebuilds racing with a writer in between converge; lease fencing; "
          "new-only writers (touch marks) landing inside / spanning a rebuild are never lost (re-snapshot or writer-side rebuild); crashed touch writer → conservative rebuild; touch writer crashing after landed stays pending through lease expiry and gc, same-payload retry repairs; normal new-only batches self-ack")


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
    v = verify(ch, "2026-10-16"); assert v["agg_cnt"] == v["events_cnt"] and v["agg_users"] == v["events_users"] and v["amount_diff"] < 1e-6, v   # 日批覆蓋（source 變了）→ 該日分區重建，彙總 = 去重後底表（修正前這裡 agg 會是底表的兩倍多）
    assert "daily_agg rebuilt for ['2026-10-16']" in s.runs("ga360_daily", 1)[0]["log"], s.runs("ga360_daily", 1)[0]["log"][-400:]
    # 隔兩天 07:00：GA 重寫日表（+120 筆遲到）→ restate 偵測列數變化重載
    r = s.run_now("ga360_restate", t0 + timedelta(days=2)); assert r["status"] == "success" and s.runs("ga360_restate", 1)[0]["rows_in"] == total + 300 + 1   # rows_in 含被守門隔離的那一筆
    bq.tables[daily] += [mk(total + 300 + i, base_us + 70 * 60_000_000 + i * 1000) for i in range(120)]
    r = s.run_now("ga360_restate", t0 + timedelta(days=2, minutes=1)); assert s.runs("ga360_restate", 1)[0]["rows_in"] == total + 420 + 1
    r = s.run_now("ga360_restate", t0 + timedelta(days=2, minutes=2)); assert s.runs("ga360_restate", 1)[0]["rows_in"] == 0 and "未變" in s.runs("ga360_restate", 1)[0]["log"]
    ch.query("OPTIMIZE TABLE dmp.events FINAL")
    assert ch.scalar("SELECT count() FROM dmp.events FINAL WHERE source='ga360_daily'") == total + 420
    assert ch.scalar("SELECT count() FROM (SELECT event_id FROM dmp.events FINAL WHERE source LIKE 'ga360%' GROUP BY event_id HAVING count() > 1)") == 0
    v = verify(ch, "2026-10-16"); assert v["agg_cnt"] == v["events_cnt"] and v["agg_users"] == v["events_users"] and v["amount_diff"] < 1e-6, v   # 重寫重載（內容相同的列略過、新列照寫）後仍一致
    assert "dedupe: skipped=" in s.runs("ga360_restate", 2)[-1]["log"]
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
