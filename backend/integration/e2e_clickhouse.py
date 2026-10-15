"""端到端測試：啟動單機 ClickHouse → 建表 → 合成來源檔 → 排程器跑兩個任務（維度表全量、委託增量三個窗口）
→ 驗證 ClickHouse 內筆數、隔離區、對帳表、水位、依賴 → 以 compile_sql 的 INSERT…SELECT 路徑重算一次比對 Python 路徑。

用法：CLICKHOUSE_BIN=/path/to/clickhouse python3 -m backend.integration.e2e_clickhouse
沒有 CLICKHOUSE_BIN 時只做乾跑（不連 ClickHouse）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    binary = os.environ.get("CLICKHOUSE_BIN")
    work = Path(tempfile.mkdtemp(prefix="dmp-e2e-"))
    proc = None
    try:
        files = synth(work)
        modeling.set_key("k_idm", b"e2e-key-idm"); modeling.set_key("k_acct", b"e2e-key-acct")
        ch = None
        if binary:
            proc = start_clickhouse(binary, work); ch = ClickHouseHTTP(f"http://127.0.0.1:{HTTP_PORT}")
            for st in local_ddl((ROOT / "clickhouse" / "schema.sql").read_text(encoding="utf-8")):
                ch.query(st)
            print("ClickHouse", ch.query("SELECT version()").strip(), "· tables:", ch.query("SELECT count() FROM system.tables WHERE database='dmp'").strip())
        runner = LocalRunner(ch)
        s = Scheduler(work / "sched.db", runner)
        t0 = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)   # 09:00 台北
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
        if ch:
            n_ev = ch.scalar("SELECT count() FROM dmp.events WHERE source='mdl_order_hist'")
            assert n_ev == loaded_py, (n_ev, loaded_py)
            by_event = ch.query("SELECT event, count() c FROM dmp.events GROUP BY event ORDER BY c DESC FORMAT TSV").strip().replace("\n", " | ")
            print("events by name:", by_event)
            assert ch.scalar("SELECT count() FROM dmp.events WHERE one_id NOT LIKE 'v1_%'") == 0        # 全部代碼化
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


if __name__ == "__main__":
    sys.exit(main())
