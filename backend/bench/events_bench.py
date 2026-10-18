"""效能基準（docs/09 §7、docs/36）：在 ClickHouse 端用 numbers() 合成 N 筆事件與對應用戶，跑語意編譯器產生的固定查詢集，
量每條查詢的 P50／P95、讀取列數與位元組、記憶體、是否命中聚合層（MV），再跑一輪並發看 P95 劣化倍數。輸出 JSON 與 markdown。

用法：
  CLICKHOUSE_BIN=/path/clickhouse python3 -m backend.bench --rows 10000000          # 起一個本機 ClickHouse（暫存目錄）跑完即關
  CLICKHOUSE_URL=http://host:8123 python3 -m backend.bench --rows 1000000000 --keep  # 對現成伺服器（測試機）；--keep 不刪資料，可接著重跑查詢
  只重跑查詢（資料已在）：加 --skip-load

合成資料全在伺服器端產生（INSERT … SELECT FROM numbers），10 億筆不經過 Python；分布與黃金資料集同形（事件權重、時段、平台、金額、
委託單號池、props 欄位），天數預設 90 天、結束日 2026-10-16，查詢集沿用 golden 的事件／漏斗／留存／路徑／歸因／用戶列表，
project 換成 bench。資料純屬合成，沒有任何真實客戶資料。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT = "bench"
END = date(2026, 10, 16)
EVENT_W = [("bond_order", 20), ("bond_deal", 12), ("app_login_success", 25), ("hq_stockdetail_view", 30), ("$pageview", 25), ("order_rejected", 4)]


# ---------------------------------------------------------------- 合成資料（伺服器端）
def _pick(expr_hash: str, items: list[tuple[str, int]]) -> str:
    """依權重從 items 選值：multiIf(h % total < c1, v1, h % total < c2, v2, …)。"""
    total = sum(w for _, w in items); acc = 0; parts = []
    for v, w in items[:-1]:
        acc += w; parts.append(f"({expr_hash}) % {total} < {acc}, '{v}'")
    return f"multiIf({', '.join(parts)}, '{items[-1][0]}')"


def user_expr() -> str:
    """第 u 個合成用戶的 one_id（v1_ + 20 個十六進位字元，與正式代碼化格式同長）。"""
    return "concat('v1_', leftPad(lower(hex(cityHash64(toUInt64(%s), 'bench-user'))), 20, '0'))"


def load(ch: Any, rows: int, users: int, days: int, chunk: int = 50_000_000) -> dict[str, float]:
    t: dict[str, float] = {}
    t0 = time.perf_counter()
    ch.query(f"ALTER TABLE dmp.events DELETE WHERE project = '{PROJECT}'", settings={"mutations_sync": 2})
    start = END - timedelta(days=days - 1)
    uid = user_expr() % f"cityHash64(number, 1) % {users}"
    ev = _pick("cityHash64(number, 2)", EVENT_W)
    plat = _pick("cityHash64(number, 4)", [("iOS", 40), ("Android", 35), ("Web", 20), ("", 5)])
    # 時段：用平方分布讓白天多（UTC 00–10 = 台北 08–18）；秒與毫秒散開
    tm = (f"toDateTime64(toDateTime('{start} 00:00:00', 'Asia/Taipei') + toIntervalDay(cityHash64(number, 3) % {days})"
          f" + toIntervalSecond(toUInt32(86400 * pow((cityHash64(number, 5) % 10000) / 10000, 0.8))), 3, 'UTC') + toIntervalMillisecond(cityHash64(number, 6) % 1000)")
    trade = "event IN ('bond_order', 'bond_deal', 'order_rejected')"
    sel = f"""
SELECT '{PROJECT}' AS project, {tm} AS time, {ev} AS event, 'bench' AS source, {uid} AS one_id, one_id AS distinct_id,
       lower(hex(cityHash64(number, 'bench-ev'))) AS event_id, {plat} AS platform,
       ['app', 'web', 'agent'][1 + cityHash64(number, 8) % 3] AS channel,
       {_pick("cityHash64(number, 9)", [("google", 3), ("facebook", 2), ("line", 1), ("", 4)])} AS utm_source, '' AS utm_campaign,
       ['2330', '2317', '0050', '2454', '00878', ''][1 + cityHash64(number, 10) % 6] AS symbol,
       if({trade} AND cityHash64(number, 11) % 10 < 9, toFloat64((1 + cityHash64(number, 12) % 50000) * 100), NULL) AS amount,
       concat('{{',
              if(cityHash64(number, 13) % 10 < 8, concat('"market":"', ['TSE', 'OTC', 'US'][1 + cityHash64(number, 14) % 3], '",'), ''),
              if(platform IN ('iOS', 'Android') AND cityHash64(number, 15) % 10 < 9, concat('"$app_version":"', ['5.2.0', '5.1.3', '5.0.9'][1 + cityHash64(number, 16) % 3], '",'), ''),
              if({trade} AND cityHash64(number, 17) % 100 < 95, concat('"order_no":"O', toString(1 + cityHash64(number, 18) % {max(1500, rows // 16)}), '",'), ''),
              '"score":', toString(cityHash64(number, 19) % 101), '}}')::JSON AS props,
       now64(3) AS ingest_time, toUInt16(1) AS schema_version, 'bench' AS batch_id
FROM numbers({{off}}, {{n}})"""
    cols = "project, time, event, source, one_id, distinct_id, event_id, platform, channel, utm_source, utm_campaign, symbol, amount, props, ingest_time, schema_version, batch_id"
    done = 0
    while done < rows:
        n = min(chunk, rows - done)
        ch.query(f"INSERT INTO dmp.events ({cols}) " + sel.replace("{off}", str(done)).replace("{n}", str(n)),
                 settings={"max_execution_time": 0, "max_insert_threads": os.cpu_count() or 4, "max_memory_usage": 0, "allow_experimental_json_type": 1})
        done += n
        print(f"  events {done:,}/{rows:,}  {time.perf_counter() - t0:.0f}s", flush=True)
    t["load_events_s"] = round(time.perf_counter() - t0, 1)
    t1 = time.perf_counter()
    u = user_expr() % "number"
    ch.query(f"""INSERT INTO dmp.users (one_id, lifecycle, tier, age_band, gender, branch_id, first_utm_source, signup_date, account_age_days, aum_bucket, has_margin,
                 churn_score, pref_market, aum, is_first_trade, last_active_at, first_seen, updated_at)
SELECT {u}, ['新客', '成長', '成熟', '沉睡', '流失'][1 + cityHash64(number, 21) % 5], ['P1', 'P2', 'P3', 'P4'][1 + cityHash64(number, 22) % 4],
       ['<25', '25-34', '35-44', '45-54', '55-64', '65+'][1 + cityHash64(number, 23) % 6], ['M', 'F'][1 + cityHash64(number, 24) % 2],
       concat('B0', toString(1 + cityHash64(number, 25) % 5)), ['google', 'facebook', 'line', 'direct'][1 + cityHash64(number, 26) % 4],
       toDate('2024-01-01') + cityHash64(number, 27) % 1000, toFloat64(1000 - cityHash64(number, 27) % 1000),
       ['<100萬', '100–500萬', '≥500萬'][1 + cityHash64(number, 28) % 3], cityHash64(number, 29) % 2, round((cityHash64(number, 30) % 1000) / 10, 1),
       ['TSE', 'OTC', 'US'][1 + cityHash64(number, 31) % 3], round(exp(13 + (cityHash64(number, 32) % 1000) / 400), 2), cityHash64(number, 33) % 2,
       toDateTime64('2026-10-15 04:00:00', 3, 'UTC'), toDateTime64('2026-09-01 00:00:00', 3, 'UTC'), now64(3)
FROM numbers({users})""", settings={"max_execution_time": 0})
    ch.query("ALTER TABLE dmp.segment_members DELETE WHERE segment_id IN ('vip', 'golden_all')", settings={"mutations_sync": 2})
    ch.query("INSERT INTO dmp.segment_members (segment_id, version, one_id, computed_at) SELECT 'golden_all', 1, one_id, now() FROM dmp.users FINAL")
    ch.query("INSERT INTO dmp.segment_members (segment_id, version, one_id, computed_at) SELECT 'vip', 1, one_id, now() FROM dmp.users FINAL WHERE tier = 'P1'")
    try:
        ch.query("SYSTEM RELOAD DICTIONARY dmp.users_dict")
    except Exception as e:                                   # 字典來源在部分環境未設定，不影響事件類查詢
        print("  (users_dict reload skipped:", str(e)[:120], ")")
    t["load_users_s"] = round(time.perf_counter() - t1, 1)
    t2 = time.perf_counter()
    for _ in range(360):                                     # 不做 OPTIMIZE FINAL（正式環境靠背景合併）；等背景合併告一段落再量，最多 30 分鐘
        if int(ch.scalar("SELECT count() FROM system.merges WHERE database = 'dmp' AND table = 'events'") or 0) == 0:
            break
        time.sleep(5)
    t["merge_wait_s"] = round(time.perf_counter() - t2, 1)
    t["parts"] = int(ch.scalar("SELECT count() FROM system.parts WHERE database = 'dmp' AND table = 'events' AND active") or 0)
    return t


# ---------------------------------------------------------------- 查詢集
def queries(days: int) -> list[dict[str, Any]]:
    """golden 查詢集換成 project=bench，另加三條 90 天全期間查詢。回傳 [{name, module, sql, settings, layer}]。"""
    from ..semantic.compiler import Catalog, compile_events, parse
    from ..semantic.compiler import golden as g
    from ..semantic.compiler.attribution import compile_attribution, parse_attribution
    from ..semantic.compiler.funnel import compile_funnel, parse_funnel
    from ..semantic.compiler.path import compile_path, parse_path
    from ..semantic.compiler.retention import compile_retention, parse_retention
    from ..semantic.compiler.users import compile_estimate, parse_users
    cat = Catalog(); out: list[dict[str, Any]] = []
    full = {"from": (END - timedelta(days=days - 1)).isoformat(), "to": END.isoformat(), "grain": "day", "now": END.isoformat()}
    extra = [("X01 全期間每日委託次數", {"metrics": [{"event": "bond_order", "agg": "total"}], "time": full}, "admin"),
             ("X02 全期間每日活躍人數×平台（精確，預設）", {"metrics": [{"event": None, "agg": "users"}], "group_by": ["platform"], "time": full}, "admin"),
             ("X02e 全期間每日活躍人數×平台（快速估算，走 MV）", {"metrics": [{"event": None, "agg": "users"}], "group_by": ["platform"], "estimate": True, "time": full}, "admin"),
             ("X03 全期間成交金額×商品（明細）", {"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount"}], "group_by": ["symbol"], "exact": True, "time": {**full, "grain": "total"}}, "admin")]
    for name, raw, spec in [*g.QUERIES, *extra]:
        c = compile_events(parse({**raw, "project": PROJECT}, cat), cat, g.decision_of(spec))
        out.append({"name": name, "module": "events", "sql": c.sql, "settings": c.settings, "layer": c.layer})
    for name, raw, spec in g.FUNNELS:
        c = compile_funnel(parse_funnel({**raw, "project": PROJECT}, cat), cat, g.decision_of(spec))
        out.append({"name": name, "module": "funnel", "sql": c.sql, "settings": c.settings, "layer": "L0"})
    for name, raw, spec in g.RETENTIONS:
        c = compile_retention(parse_retention({**raw, "project": PROJECT}, cat), cat, g.decision_of(spec))
        out.append({"name": name, "module": "retention", "sql": c.sql, "settings": c.settings, "layer": "L0"})
    for name, raw, spec in g.PATHS:
        c = compile_path(parse_path({**raw, "project": PROJECT, "sample": raw.get("sample", 0.1)}, cat), cat, g.decision_of(spec))   # 與 API 一致：路徑預設抽樣 1/10
        out.append({"name": name, "module": "path", "sql": c.sql, "settings": c.settings, "layer": "L0"})
    for name, raw, spec in g.ATTRIBUTIONS:
        c = compile_attribution(parse_attribution({**raw, "project": PROJECT}, cat), cat, g.decision_of(spec))
        out.append({"name": name, "module": "attribution", "sql": c.sql, "settings": c.settings, "layer": "L0"})
    for name, raw, spec in g.USERS:
        try:
            c = compile_estimate(parse_users({**raw, "project": PROJECT}, cat), cat, g.decision_of(spec))
        except Exception:                                    # 依 golden 專屬分群或來源的查詢略過
            continue
        out.append({"name": name, "module": "users", "sql": c.sql, "settings": c.settings, "layer": "bitmap"})
    return out


# ---------------------------------------------------------------- 量測
def limits(concurrency: int = 1) -> dict[str, int]:
    """每條查詢的記憶體上限與溢寫門檻：超過就溢寫到磁碟或記為失敗，不讓單一查詢把伺服器拖垮（1 億筆在 4 核 15 GB 實測會 OOM）。
    並發時每條的上限 ＝ min(單條上限, 實體記憶體 50% ÷ 並發數)。"""
    mem = int(float(os.environ.get("BENCH_QUERY_MEM_GB", "4")) * 1e9)
    if concurrency > 1:
        mem = min(mem, int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") * 0.5 / concurrency))
    return {"max_memory_usage": mem, "max_bytes_before_external_group_by": mem // 2, "max_bytes_before_external_sort": mem // 2}


def _run(ch: Any, q: dict[str, Any], tag: str, concurrency: int = 1) -> tuple[str, str | None]:
    qid = f"bench-{tag}-{uuid.uuid4().hex[:12]}"
    try:
        ch.query(q["sql"] + " FORMAT Null", settings={**q["settings"], **limits(concurrency), "query_id": qid, "use_query_cache": 0, "max_execution_time": 600})
        return qid, None
    except Exception as e:                                   # 記憶體／溢寫空間／逾時：記錄原因，繼續下一條
        msg = str(e); code = msg.split("DB::Exception:")[0][-40:] if "DB::Exception" in msg else msg[:80]
        reason = "記憶體超過上限" if "MEMORY_LIMIT" in msg else "暫存磁碟不足" if "NOT_ENOUGH_SPACE" in msg else "逾時" if "TIMEOUT" in msg else code.strip()[:80]
        return qid, reason


def _log(ch: Any, qids: list[str]) -> dict[str, dict[str, Any]]:
    ch.query("SYSTEM FLUSH LOGS")
    ids = ", ".join(f"'{x}'" for x in qids)
    txt = ch.query(f"""SELECT query_id, query_duration_ms AS ms, read_rows, read_bytes, memory_usage AS mem,
                          arrayExists(t -> t LIKE '%events_daily_agg%' OR t LIKE '%.inner%', tables) AS mv
                       FROM system.query_log WHERE type = 'QueryFinish' AND query_id IN ({ids}) FORMAT JSONEachRow""",
                   settings={"output_format_json_quote_64bit_integers": 0})
    return {r["query_id"]: r for r in (json.loads(x) for x in txt.splitlines() if x.strip())}


def pct(xs: list[float], p: float) -> float:
    if not xs: return 0.0
    s = sorted(xs); k = (len(s) - 1) * p; f = int(k); c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


def measure(ch: Any, qs: list[dict[str, Any]], repeat: int, concurrency: int) -> dict[str, Any]:
    failed: dict[str, str] = {}
    for q in qs:                                             # 暖身一次（載入 mark cache；不計）；暖身就失敗的查詢不再重跑
        _, err = _run(ch, q, "warm")
        if err:
            failed[q["name"]] = err; print(f"  失敗：{q['name']}（{err}）", flush=True)
    per: dict[str, list[str]] = {q["name"]: [] for q in qs}
    for _ in range(repeat):
        for q in qs:
            if q["name"] in failed: continue
            qid, err = _run(ch, q, "seq")
            if err: failed[q["name"]] = err
            else: per[q["name"]].append(qid)
    logs = _log(ch, [x for v in per.values() for x in v])
    rows = []
    for q in qs:
        if q["name"] in failed:
            rows.append({"name": q["name"], "module": q["module"], "layer": q["layer"], "failed": failed[q["name"]]}); continue
        rs = [logs[x] for x in per[q["name"]] if x in logs]
        ms = [float(r["ms"]) for r in rs]
        rows.append({"name": q["name"], "module": q["module"], "layer": q["layer"], "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95),
                     "read_rows": int(statistics.median([r["read_rows"] for r in rs])) if rs else None,
                     "read_mb": round(statistics.median([r["read_bytes"] for r in rs]) / 1e6, 1) if rs else None,
                     "mem_mb": round(max(r["mem"] for r in rs) / 1e6, 1) if rs else None, "mv_hit": bool(rs and rs[0]["mv"])})
    conc: dict[str, Any] = {}
    if concurrency > 1:
        jobs = [q for q in qs if q["name"] not in failed for _ in range(2)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(concurrency) as pool:
            res_c = list(pool.map(lambda q: (q["name"], _run(ch, q, "conc", concurrency)), jobs))
        qids = [(n, r[0]) for n, r in res_c if not r[1]]
        conc_failed = sorted({f"{n}（{r[1]}）" for n, r in res_c if r[1]})
        wall = time.perf_counter() - t0
        logs = _log(ch, [x for _, x in qids])
        ms = [float(logs[x]["ms"]) for _, x in qids if x in logs]
        seq = [r["p95_ms"] for r in rows if "failed" not in r]
        conc = {"concurrency": concurrency, "queries": len(jobs), "failed": conc_failed, "mem_per_query_gb": round(limits(concurrency)["max_memory_usage"] / 1e9, 2), "wall_s": round(wall, 1), "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95),
                "seq_p95_of_p95_ms": pct(seq, 0.95), "degradation": round(pct(ms, 0.95) / max(pct(seq, 0.95), 1), 2)}
    by_mod: dict[str, dict[str, Any]] = {}
    for m in sorted({r["module"] for r in rows}):
        xs = [r for r in rows if r["module"] == m and "failed" not in r]
        by_mod[m] = {"queries": len(xs), "failed": sum(1 for r in rows if r["module"] == m and "failed" in r),
                     "p95_max_ms": max((r["p95_ms"] for r in xs), default=None), "p95_median_ms": pct([r["p95_ms"] for r in xs], 0.5) if xs else None}
    ev = [r for r in rows if r["module"] == "events" and "failed" not in r]
    return {"queries": rows, "modules": by_mod, "concurrency": conc,
            "mv_hit_rate": round(sum(1 for r in ev if r["mv_hit"]) / len(ev), 3) if ev else None,
            "mv_eligible": sum(1 for r in ev if r["layer"] == "L1")}


def report_md(res: dict[str, Any]) -> str:
    m = res["meta"]; out = [f"# 效能基準結果：{m['rows']:,} 筆事件 × {m['users']:,} 人 × {m['days']} 天", "",
                            f"- ClickHouse {m['clickhouse']}；{m['cpus']} vCPU；資料 {m['events_disk_gb']} GB（壓縮後，{m['bytes_per_event']} B／筆）",
                            f"- 載入：{m['load']}", f"- 每條查詢暖身 1 次後跑 {m['repeat']} 次；查詢快取關閉", ""]
    out += ["| 模組 | 成功條數 | 失敗 | P95 中位數 (ms) | P95 最大 (ms) |", "|---|---:|---:|---:|---:|"]
    out += [f"| {k} | {v['queries']} | {v['failed']} | {v['p95_median_ms']} | {v['p95_max_ms']} |" for k, v in res["modules"].items()]
    bad = [r for r in res["queries"] if "failed" in r]
    if bad:
        out += ["", f"單條查詢記憶體上限 {m.get('query_mem_gb')} GB 下失敗：" + "；".join(f"{r['name']}（{r['failed']}）" for r in bad)]
    out += ["", f"事件分析命中聚合層（MV）：{res['mv_hit_rate']:.0%}（編譯器判定可走 L1 的 {res['mv_eligible']} 條）" if res["mv_hit_rate"] is not None else ""]
    c = res["concurrency"]
    if c:
        out += ["", f"並發 {c['concurrency']}（{c['queries']} 條查詢，每條記憶體上限 {c.get('mem_per_query_gb')} GB）：P95 {c['p95_ms']} ms，單條依序的 P95 {c['seq_p95_of_p95_ms']} ms，劣化 {c['degradation']} 倍；總耗時 {c['wall_s']} s"
                + (f"；並發下失敗 {len(c['failed'])} 條：{'；'.join(c['failed'])}" if c.get("failed") else "")]
    out += ["", "| 查詢 | 層 | P50 | P95 | 讀取列數 | 讀取 MB | 記憶體 MB | MV |", "|---|---|---:|---:|---:|---:|---:|---|"]
    out += [f"| {r['name']} | {r['layer']} | {r['p50_ms']} | {r['p95_ms']} | {r['read_rows']:,} | {r['read_mb']} | {r['mem_mb']} | {'✓' if r['mv_hit'] else ''} |" if "failed" not in r
            else f"| {r['name']} | {r['layer']} | 失敗：{r['failed']} | | | | | |" for r in res["queries"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m backend.bench")
    ap.add_argument("--rows", type=int, default=10_000_000)
    ap.add_argument("--users", type=int, default=0, help="預設 rows / 100（與每人每月約 30 筆事件同量級）")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", default="bench-results")
    ap.add_argument("--skip-load", action="store_true")
    ap.add_argument("--only", default="", help="只跑指定模組（逗號分隔：events,funnel,retention,path,attribution,users）")
    ap.add_argument("--keep", action="store_true", help="跑完保留資料（現成伺服器不刪 bench 資料；本機不刪暫存資料夾）")
    a = ap.parse_args(argv)
    users = a.users or max(1000, a.rows // 100)
    from ..integration.e2e_clickhouse import ROOT, start_clickhouse
    from ..integration.runner import ClickHouseHTTP, local_ddl
    binary, url = os.environ.get("CLICKHOUSE_BIN"), os.environ.get("CLICKHOUSE_URL")
    if not (binary or url):
        print("需要 CLICKHOUSE_BIN（本機起一個）或 CLICKHOUSE_URL（現成伺服器）"); return 2
    proc = None; work = Path(os.environ.get("BENCH_WORK") or tempfile.mkdtemp(prefix="dmp-bench-"))
    try:
        if binary:
            proc = start_clickhouse(binary, work, extra="<query_log><database>system</database><table>query_log</table><flush_interval_milliseconds>1000</flush_interval_milliseconds></query_log>"
                                                 "<merge_tree><old_parts_lifetime>30</old_parts_lifetime></merge_tree>"
                                                 f"<max_server_memory_usage>{int(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') * 0.6)}</max_server_memory_usage>")      # 合併後的舊 part 30 秒就清，避免 1 億筆以上把磁碟塞滿
            url = "http://127.0.0.1:18123"
        ch = ClickHouseHTTP(url or "", user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=7200)
        for st in local_ddl((ROOT / "clickhouse" / "schema.sql").read_text(encoding="utf-8")):
            try:
                ch.query(st, settings={"allow_experimental_json_type": 1})
            except Exception as e:                           # 已存在／字典來源未設定：繼續
                if "already exists" not in str(e).lower(): print("  ddl skipped:", str(e)[:100])
        load_t: dict[str, float] = {}
        if not a.skip_load:
            print(f"--- load {a.rows:,} events × {users:,} users × {a.days} days"); load_t = load(ch, a.rows, users, a.days)
        n = int(ch.scalar(f"SELECT count() FROM dmp.events WHERE project = '{PROJECT}'"))
        disk = float(ch.scalar("SELECT sum(bytes_on_disk) FROM system.parts WHERE database = 'dmp' AND table = 'events' AND active"))
        qs = queries(a.days)
        if a.only:
            keep = {m.strip() for m in a.only.split(",")}; qs = [q for q in qs if q["module"] in keep]
        print(f"--- measure {len(qs)} queries × {a.repeat} + concurrency {a.concurrency}")
        res = measure(ch, qs, a.repeat, a.concurrency)
        res["meta"] = {"rows": n, "users": users, "days": a.days, "repeat": a.repeat, "clickhouse": ch.query("SELECT version()").strip(), "cpus": os.cpu_count(),
                       "events_disk_gb": round(disk / 1e9, 2), "query_mem_gb": limits()["max_memory_usage"] / 1e9, "bytes_per_event": round(disk / max(n, 1), 1), "load": load_t, "at": time.strftime("%Y-%m-%d %H:%M")}
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        stem = f"bench_{n // 1_000_000}M" + (f"_{a.only.replace(',', '-')}" if a.only else "")
        (out / f"{stem}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{stem}.md").write_text(report_md(res), encoding="utf-8")
        print(report_md(res).split("| 查詢 |")[0]); print(f"結果：{out / stem}.json／.md")
        if url and not binary and not a.keep:
            ch.query(f"ALTER TABLE dmp.events DELETE WHERE project = '{PROJECT}'", settings={"mutations_sync": 2})
        return 0
    finally:
        if proc:
            proc.terminate(); proc.wait(timeout=30)
            if not os.environ.get("BENCH_WORK") and not a.keep:       # 本機暫存的 ClickHouse 資料夾跑完就刪；--keep 或指定 BENCH_WORK 才保留
                shutil.rmtree(work, ignore_errors=True)
