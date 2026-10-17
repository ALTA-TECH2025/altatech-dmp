"""資料流與效能（docs/17 §5.16、docs/27 G2）：把排程器、審計、程序內指標、ClickHouse 系統表彙總成四個視圖，並依 ops/alerts.yaml 評估告警。

- pipeline：來源（同步任務）→ 進倉（ClickHouse 表、事件時效）→ 服務（HTTP、查詢）→ 下游（推送交付）；資料品質。
- query_perf：審計 query.executed／query.failed／query.sql 依模組算 P50/P95/P99、錯誤、SLA 達成；ClickHouse 可用時加 system.query_log。
- batch_jobs：同步任務＋背景例行（dmp_routine_* 指標）＋儲存用量。
- alerts：規則 × 指標 → 觸發清單；同一組規則在 Helm 的 PrometheusRule 裡用 PromQL 表達。
所有 ClickHouse 查詢都只讀系統表或日分區內的 max(time)，快取 30 秒；失敗回 None，不讓頁面 500。
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore

from . import metrics as M

ROOT = Path(__file__).resolve().parent
SLA: dict[str, Any] = yaml.safe_load((ROOT / "sla.yaml").read_text(encoding="utf-8"))
ALERTS: dict[str, Any] = yaml.safe_load((ROOT / "alerts.yaml").read_text(encoding="utf-8"))
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
MODULE_CN = {"events": "事件分析", "funnel": "漏斗分析", "retention": "留存分析", "path": "行為路徑", "attribution": "歸因分析", "users": "用戶列表", "estimate": "人數估算", "dashboard": "儀表板", "dashboard_render": "儀表板輸出", "bookmark": "書籤", "sql": "自定義 SQL", "segments": "分群", "tags": "標籤", "profile": "用戶細查"}
ROUTINES = [("scheduler", "同步任務排程", "每分鐘"), ("dashboard_share", "看板定時寄送", "每分鐘"), ("segments", "分群例行計算與到期清理", "每 10 分鐘"), ("tags", "標籤例行計算", "每 10 分鐘"), ("activation", "推送重試與日批後增量", "每分鐘")]
CH_TABLES_CN = {"events": "行為事件", "users": "用戶表", "user_tags": "標籤 Bitmap", "segment_members": "分群成員", "quarantine": "隔離區", "id_edges": "OneID 邊", "touchpoints": "歸因觸點", "audit_log": "審計", "raw": "原始事件", "prop_log": "屬性變更", "id_map": "ID 映射"}


def _table_cn(name: str) -> str:
    if name.startswith(".inner_id."): return "物化視圖內部表"
    if name.startswith(".inner."): return f"物化視圖 {name[7:]}"
    return CH_TABLES_CN.get(name, name)


def _bytes(b: float) -> str:
    return f"{b / 1e12:.2f} TB" if b >= 1e12 else f"{b / 1e9:.2f} GB" if b >= 1e9 else f"{b / 1e6:.1f} MB"


def _parse_ts(s: Any) -> float | None:
    if not s: return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None: t = t.replace(tzinfo=UTC)
        return t.timestamp()
    except ValueError:
        return None


def _status(v: float | None, warn: float, bad: float, higher_is_worse: bool = True) -> str:
    if v is None: return "unknown"
    if higher_is_worse: return "error" if v >= bad else "warn" if v >= warn else "ok"
    return "error" if v <= bad else "warn" if v <= warn else "ok"


class OpsService:
    def __init__(self, store: Any, ch: Any | None, scheduler: Any | None, now: Any = time.time, reg: M.Registry = M.REG):
        self.store, self.ch, self.sched, self.now, self.reg = store, ch, scheduler, now, reg
        self._cache: dict[str, tuple[float, Any]] = {}
        reg.collector(self._collect, {"dmp_source_lag_seconds": "同步任務水位落後秒數", "dmp_sync_last_ok": "同步任務最近一次是否成功", "dmp_sync_quarantine_ratio": "最近一次隔離＋拒絕比例",
                                      "dmp_delivery_24h": "24 小時推送交付數（依狀態）", "dmp_destination_paused": "自動暫停的目的地", "dmp_events_stale_seconds": "最新行為事件落後秒數",
                                      "dmp_clickhouse_table_bytes": "ClickHouse 表磁碟用量", "dmp_clickhouse_table_rows": "ClickHouse 表列數", "dmp_clickhouse_hot_ratio": "熱層用量佔規劃值比例"})

    # ---------------- ClickHouse（只讀系統表；快取 30 秒；失敗回 None） ----------------
    def _ch_rows(self, key: str, sql: str, ttl: float = 30) -> list[dict[str, Any]] | None:
        if self.ch is None: return None
        hit = self._cache.get(key)
        if hit and self.now() - hit[0] < ttl: return hit[1]
        try:
            txt = self.ch.query(sql + " FORMAT JSONEachRow", settings={"max_execution_time": 60})
            rows = [json.loads(l) for l in txt.splitlines() if l.strip()]
        except Exception:
            rows = None
        self._cache[key] = (self.now(), rows)
        return rows

    def ch_tables(self) -> list[dict[str, Any]] | None:
        rows = self._ch_rows("parts", "SELECT table, sum(rows) AS rows, sum(bytes_on_disk) AS bytes, count() AS parts FROM system.parts WHERE database = 'dmp' AND active GROUP BY table ORDER BY bytes DESC")
        return [{"name": r["table"], "cn": _table_cn(r["table"]), "rows": int(r["rows"]), "bytes": int(r["bytes"]), "parts": int(r["parts"])} for r in rows] if rows is not None else None

    def events_stale_min(self) -> float | None:
        rows = self._ch_rows("freshness", "SELECT max(time) AS t FROM dmp.events WHERE event_date >= today() - 1")
        if not rows or not rows[0].get("t"): return None
        t = _parse_ts(rows[0]["t"])
        return None if t is None else max(0.0, (self.now() - t) / 60)

    def ch_query_log(self, window: str) -> dict[str, Any] | None:
        secs = WINDOWS.get(window, 86400)
        rows = self._ch_rows(f"qlog:{window}", f"SELECT count() AS n, quantile(0.95)(query_duration_ms) AS p95_ms, avg(read_rows) AS scan_rows_avg, max(memory_usage) / 1048576 AS memory_peak_mb, countIf(exception_code != 0) AS errors "
                             f"FROM system.query_log WHERE type IN ('QueryFinish', 'ExceptionWhileProcessing') AND event_time > now() - INTERVAL {secs} SECOND AND query NOT ILIKE '%system.%' AND query_kind = 'Select'")
        if not rows: return None
        r = rows[0]
        return {"n": int(r.get("n") or 0), "p95_ms": _f(r.get("p95_ms")), "scan_rows_avg": _f(r.get("scan_rows_avg")), "memory_peak_mb": _f(r.get("memory_peak_mb")), "errors": int(r.get("errors") or 0)}

    # ---------------- 來源：同步任務 ----------------
    def sources(self) -> list[dict[str, Any]]:
        if self.sched is None: return []
        cn = {t["id"]: t.get("cn") or t["id"] for t in self.store.items("sync_task")}
        out = []
        for s in self.sched.status(datetime.fromtimestamp(self.now(), UTC)):
            last = s.get("last") or {}
            wm = _parse_ts(s.get("watermark")); lag = None if wm is None else max(0.0, (self.now() - wm) / 60)
            rin = int(last.get("rows_in") or 0); bad = int(last.get("rows_quarantined") or 0) + int(last.get("rows_rejected") or 0)
            st = last.get("status")
            failed = 1 if st in ("failed", "paused") or (not s["enabled"] and s.get("paused_reason")) else 0
            out.append({"task": s["id"], "cn": cn.get(s["id"], s["id"]), "enabled": bool(s["enabled"]), "schedule": s["schedule"], "mode": s["mode"], "last_at": last.get("scheduled_for"), "last_status": st,
                        "rows_in": rin, "rows_loaded": int(last.get("rows_loaded") or 0), "rows_bad": bad, "quarantine_rate": (bad / rin) if rin else None, "watermark": s.get("watermark"), "lag_min": lag, "failed": failed,
                        "next_at": s.get("next_slot"), "error": last.get("error"), "status": "error" if failed else _status(lag, 120, 720) if lag is not None else ("ok" if st == "success" else "unknown")})
        return out

    # ---------------- 服務層：HTTP 與查詢 ----------------
    def serving(self, secs: int = 300) -> dict[str, Any]:
        since = self.now() - secs
        http = M.HTTP_SECONDS.samples(since=since)
        total = sum(v for k, v in M.HTTP_REQUESTS.values.items())
        err5 = sum(v for k, v in M.HTTP_REQUESTS.values.items() if dict(k).get("status", "").startswith("5"))
        q = M.QUERY_SECONDS.samples(since=since)
        p50, p95, _ = M.quantiles(http); qp95 = M.quantiles(q)[1]
        return {"requests_5m": len(http), "http_p50_ms": None if p50 is None else round(p50 * 1000), "http_p95_ms": None if p95 is None else round(p95 * 1000), "http_error_rate": (err5 / total) if total else 0.0,
                "requests_total": int(total), "errors_5xx_total": int(err5), "queries_5m": len(q), "query_p95_ms": None if qp95 is None else round(qp95 * 1000)}

    # ---------------- 下游：推送交付 ----------------
    def activation(self) -> dict[str, Any]:
        cutoff = datetime.fromtimestamp(self.now() - 86400, UTC)
        by = {"ok": 0, "retrying": 0, "dlq": 0}
        for d in self.store.items("delivery"):
            if d.get("mode") == "test": continue
            t = _parse_ts(d.get("created_at"))
            if t is None or t < cutoff.timestamp(): continue
            by[d.get("status", "retrying")] = by.get(d.get("status", "retrying"), 0) + 1
        n = sum(by.values()); ok = by.get("ok", 0)
        paused = [{"destination": d["id"], "cn": d.get("name") or d["id"], "consecutive_failures": d.get("consecutive_failures", 0)} for d in self.store.items("destination") if d.get("status") == "paused" and (d.get("consecutive_failures") or 0) > 0]
        return {"deliveries_24h": by, "success_rate": (ok / n) if n else None, "paused_destinations": paused, "status": "error" if by.get("dlq") else "warn" if paused or (n and ok / n < SLA["activation"]["delivery_success_min"]) else "ok"}

    # ---------------- 例行作業與審計 ----------------
    def routines(self) -> list[dict[str, Any]]:
        out = []
        for job, cn, sched in ROUTINES:
            last = M.ROUTINE_LAST.get({"job": job}) or None
            ok = M.ROUTINE_LAST_STATUS.get({"job": job}) if last else None
            ms = M.ROUTINE_SECONDS.samples({"job": job})
            out.append({"name": job, "cn": cn, "kind": "routine", "schedule": sched, "enabled": True, "last_at": None if not last else datetime.fromtimestamp(last, UTC).isoformat(timespec="seconds"),
                        "last_status": None if last is None else ("success" if ok else "failed"), "last_ms": None if not ms else round(ms[-1] * 1000), "last_detail": None, "next_at": None, "rerunnable": False,
                        "errors_total": int(M.ROUTINE_TOTAL.get({"job": job, "status": "error"})), "runs_total": int(M.ROUTINE_TOTAL.get({"job": job, "status": "ok"}) + M.ROUTINE_TOTAL.get({"job": job, "status": "error"}))})
        return out

    def routine_failed_24h(self) -> int:
        since = self.now() - 86400
        return sum(1 for x in self.store.audit_tail(10_000) if x.get("event") == "scheduler.tick.failed" and float(x.get("at", 0)) >= since)

    def suppressed_24h(self) -> tuple[int, int]:
        since = self.now() - 86400; n = sup = 0
        for x in self.store.audit_tail(10_000):
            if x.get("event") == "query.executed" and float(x.get("at", 0)) >= since:
                n += 1; sup += 1 if x.get("suppressed") else 0
        return n, sup

    # ---------------- 四個視圖 ----------------
    def pipeline(self) -> dict[str, Any]:
        src = self.sources(); tables = self.ch_tables(); stale = self.events_stale_min(); sv = self.serving(); act = self.activation()
        src_status = "error" if any(s["status"] == "error" for s in src) else "warn" if any(s["status"] == "warn" for s in src) else ("ok" if src else "unknown")
        hot_tb = SLA["storage"]["clickhouse_hot_tb"]; used = sum(t["bytes"] for t in tables) if tables else None
        wh_items = [{**t, "note": ""} for t in (tables or [])]
        wh_status = "unknown" if tables is None else "error" if (stale is not None and stale > 60) else "warn" if (used or 0) / (hot_tb * 1e12) > 0.9 else "ok"
        sv_status = "error" if sv["http_error_rate"] > SLA["serving"]["http_error_rate_max"] else "warn" if (sv["http_p95_ms"] or 0) > SLA["serving"]["http_p95_ms"] else "ok"
        rin = sum(s["rows_in"] for s in src); bad = sum(s["rows_bad"] for s in src); qn, qs = self.suppressed_24h()
        quality = [
            {"key": "quarantine_rate", "cn": "隔離＋拒絕率（最近一次同步）", "value": (bad / rin) if rin else None, "text": f"{bad:,} / {rin:,} 列" if rin else "沒有同步紀錄", "status": _status((bad / rin) if rin else None, 0.01, 0.05)},
            {"key": "sync_failed", "cn": "失敗／暫停的同步任務", "value": sum(s["failed"] for s in src), "text": "、".join(s["cn"] for s in src if s["failed"]) or "無", "status": "error" if any(s["failed"] for s in src) else "ok"},
            {"key": "events_stale", "cn": "行為事件最新時間落後", "value": stale, "text": "未連接 ClickHouse" if stale is None else f"{stale:.0f} 分鐘（SLA {SLA['freshness']['behavior_events_min']} 分鐘可查）", "status": _status(stale, SLA["freshness"]["behavior_events_min"], 60)},
            {"key": "k_suppressed", "cn": "K 匿名抑制的查詢（24h）", "value": (qs / qn) if qn else None, "text": f"{qs} / {qn} 次" if qn else "24 小時內沒有查詢", "status": "ok" if not qn else _status(qs / qn, 0.2, 0.5)},
            {"key": "delivery_success", "cn": "推送交付成功率（24h）", "value": act["success_rate"], "text": "、".join(f"{k} {v}" for k, v in act["deliveries_24h"].items() if v) or "24 小時內沒有交付", "status": "ok" if act["success_rate"] is None else _status(act["success_rate"], SLA["activation"]["delivery_success_min"], 0.9, higher_is_worse=False)},
        ]
        stages = [
            {"key": "sources", "cn": "來源同步", "status": src_status, "summary": f"{len(src)} 個任務；最近一次讀入 {rin:,} 列" if src else "沒有同步任務（排程器未啟用）", "items": src},
            {"key": "warehouse", "cn": "ClickHouse 進倉", "status": wh_status, "summary": ("未連接 ClickHouse" if tables is None else f"{len(tables)} 張表，{_bytes(used or 0)}；事件落後 {stale:.0f} 分鐘" if stale is not None else f"{len(tables)} 張表，{_bytes(used or 0)}"), "items": wh_items},
            {"key": "serving", "cn": "API 與查詢", "status": sv_status, "summary": f"5 分鐘 {sv['requests_5m']} 次請求，P95 {sv['http_p95_ms'] or 0} ms，5xx {sv['http_error_rate'] * 100:.2f}%；查詢 P95 {sv['query_p95_ms'] or 0} ms", "items": [sv]},
            {"key": "activation", "cn": "推送交付", "status": act["status"], "summary": (f"24 小時 {sum(act['deliveries_24h'].values())} 筆交付，成功率 {act['success_rate'] * 100:.1f}%" if act["success_rate"] is not None else "24 小時內沒有交付") + (f"；{len(act['paused_destinations'])} 個目的地自動暫停" if act["paused_destinations"] else ""), "items": [act]},
        ]
        return {"at": self._iso(), "stages": stages, "quality": quality, "slo": SLA}

    def query_perf(self, window: str = "24h") -> dict[str, Any]:
        secs = WINDOWS.get(window, 86400); since = self.now() - secs
        by: dict[str, dict[str, Any]] = {}
        slow: list[dict[str, Any]] = []
        for x in self.store.audit_tail(10_000):
            at = float(x.get("at", 0))
            if at < since: continue
            ev = x.get("event")
            if ev == "query.executed": mod, ms, ok, rows = str(x.get("module") or "events"), float(x.get("ms") or 0), True, int(x.get("rows") or 0)
            elif ev == "query.failed": mod, ms, ok, rows = str(x.get("module") or "events"), 0.0, False, 0
            elif ev == "query.sql": mod, ms, ok, rows = "sql", float(x.get("ms") or 0), x.get("status") != "error", int(x.get("rows") or 0)
            else: continue
            d = by.setdefault(mod, {"ms": [], "errors": 0, "rows": 0})
            if ok: d["ms"].append(ms); d["rows"] += rows; slow.append({"at": datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds"), "module": mod, "ms": ms, "user": x.get("user"), "sql_hash": x.get("sql_hash"), "rows": rows})
            else: d["errors"] += 1
        mods = []
        for mod, d in sorted(by.items(), key=lambda kv: -len(kv[1]["ms"])):
            p50, p95, p99 = M.quantiles(d["ms"]); sla = SLA["query_p95_ms"].get(mod); n = len(d["ms"]) + d["errors"]
            mods.append({"module": mod, "cn": MODULE_CN.get(mod, mod), "n": n, "errors": d["errors"], "p50_ms": p50, "p95_ms": p95, "p99_ms": p99, "avg_rows": (d["rows"] / len(d["ms"])) if d["ms"] else None,
                         "sla_ms": sla, "sla_ok": None if (sla is None or p95 is None) else p95 <= sla, "error_rate": (d["errors"] / n) if n else 0.0, "scan_rows_avg": None, "memory_peak_mb": None})
        slow.sort(key=lambda r: -r["ms"])
        return {"window": window, "at": self._iso(), "modules": mods, "slow": slow[:10], "clickhouse": self.ch_query_log(window)}

    def batch_jobs(self) -> dict[str, Any]:
        jobs = [{"name": s["task"], "cn": s["cn"], "kind": "sync", "schedule": s["schedule"], "enabled": s["enabled"], "last_at": s["last_at"], "last_status": s["last_status"], "last_ms": None,
                 "last_detail": (f"讀入 {s['rows_in']:,}，載入 {s['rows_loaded']:,}，隔離／拒絕 {s['rows_bad']:,}" if s["rows_in"] else None) if not s["error"] else str(s["error"])[:200], "next_at": s["next_at"], "rerunnable": True} for s in self.sources()]
        jobs += self.routines()
        storage = [{"name": f"clickhouse.{t['name']}", "cn": t["cn"], "rows": t["rows"], "bytes": t["bytes"], "parts": t["parts"], "note": ""} for t in (self.ch_tables() or [])]
        audit_n = len(self.store.audit_tail(10_000))
        storage.append({"name": "control_plane", "cn": "控制面（看板／分群／標籤／推送／審計尾端）", "rows": audit_n + sum(len(self.store.items(k)) for k in ("dashboard", "tag", "push", "delivery", "destination", "bookmark")), "bytes": None, "parts": None,
                        "note": "單機為 JSON 檔；正式環境 PostgreSQL（store_pg）"})
        return {"at": self._iso(), "jobs": jobs, "storage": storage}

    def rerun(self, name: str, subject: Any) -> dict[str, Any]:
        if self.sched is None: raise KeyError(name)
        self.sched.task(name)                                                 # KeyError → 404
        res = self.sched.run_now(name)
        self.store.audit("batch.rerun", {"user": subject.user_id, "job": name, "status": res.get("status")})
        return {"job": name, **res}

    def alerts(self) -> dict[str, Any]:
        src = self.sources(); sv = self.serving(); act = self.activation(); qp = self.query_perf("24h"); tables = self.ch_tables(); stale = self.events_stale_min()
        used = sum(t["bytes"] for t in tables) if tables else None
        values: dict[str, list[tuple[str, float | None]]] = {
            "source_lag_min": [(s["cn"], s["lag_min"]) for s in src if s["enabled"]],
            "sync_failed": [(s["cn"], float(s["failed"])) for s in src],
            "quarantine_rate": [(s["cn"], s["quarantine_rate"]) for s in src],
            "query_p95_ms": [(m["cn"], (m["p95_ms"] / m["sla_ms"]) if (m["sla_ms"] and m["p95_ms"] is not None) else None) for m in qp["modules"]],
            "query_error_rate": [(m["cn"], m["error_rate"]) for m in qp["modules"]],
            "http_error_rate": [("API", sv["http_error_rate"])],
            "delivery_dlq_24h": [("推送", float(act["deliveries_24h"].get("dlq", 0)))],
            "destination_paused": [(p["cn"], 1.0) for p in act["paused_destinations"]],
            "routine_failed_24h": [("背景例行", float(self.routine_failed_24h()))],
            "events_stale_min": [("dmp.events", stale)],
            "clickhouse_hot_ratio": [("ClickHouse", None if used is None else used / (SLA["storage"]["clickhouse_hot_tb"] * 1e12))],
        }
        firing: list[dict[str, Any]] = []; rules: list[dict[str, Any]] = []
        for r in ALERTS["rules"]:
            base = {k: r[k] for k in ("key", "cn", "severity", "threshold", "op", "unit", "text")}
            rules.append({**base, "metric": r["metric"], "window": r.get("window", ""), "per": r.get("per")})
            for subj, v in values.get(r["metric"], []):
                if v is None: continue
                if _cmp(v, r["op"], float(r["threshold"])):
                    firing.append({**base, "value": round(v, 4), "subject": subj, "firing": True})
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        firing.sort(key=lambda a: (order.get(a["severity"], 9), a["key"]))
        return {"at": self._iso(), "firing": firing, "rules": rules, "notify": ALERTS.get("notify", [])}

    # ---------------- Prometheus collector ----------------
    def _collect(self) -> list[tuple[str, dict[str, str], float]]:
        out: list[tuple[str, dict[str, str], float]] = []
        for s in self.sources():
            if s["lag_min"] is not None: out.append(("dmp_source_lag_seconds", {"task": s["task"]}, round(s["lag_min"] * 60)))
            out.append(("dmp_sync_last_ok", {"task": s["task"]}, 0.0 if s["failed"] else 1.0))
            if s["quarantine_rate"] is not None: out.append(("dmp_sync_quarantine_ratio", {"task": s["task"]}, round(s["quarantine_rate"], 4)))
        act = self.activation()
        out += [("dmp_delivery_24h", {"status": k}, float(v)) for k, v in act["deliveries_24h"].items()]
        out += [("dmp_destination_paused", {"destination": p["destination"]}, 1.0) for p in act["paused_destinations"]]
        stale = self.events_stale_min()
        if stale is not None: out.append(("dmp_events_stale_seconds", {}, round(stale * 60)))
        tables = self.ch_tables()
        if tables is not None:
            out += [("dmp_clickhouse_table_bytes", {"table": t["name"]}, float(t["bytes"])) for t in tables]
            out += [("dmp_clickhouse_table_rows", {"table": t["name"]}, float(t["rows"])) for t in tables]
            out.append(("dmp_clickhouse_hot_ratio", {}, round(sum(t["bytes"] for t in tables) / (SLA["storage"]["clickhouse_hot_tb"] * 1e12), 6)))
        return out

    def _iso(self) -> str:
        return datetime.fromtimestamp(self.now(), UTC).isoformat(timespec="seconds")


def _f(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _cmp(v: float, op: str, t: float) -> bool:
    return {">": v > t, ">=": v >= t, "<": v < t, "<=": v <= t}[op]
