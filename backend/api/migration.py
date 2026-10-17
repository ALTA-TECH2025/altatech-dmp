"""神策 → GA360 遷移路由：/v1/migration/*（docs/22）。

- inventory：migration/inventory.yaml 的計畫欄位 + 神策查詢 API 盤點結果（store kind=migration_inventory）+ 導出任務進度。
- bridge：backend/migration/bridge.py 由 events.yaml 產生的埋點銜接表（即時計算，與 CSV 同一來源）。
- exports：導出／載入任務（store kind=migration_export）：按日分片；執行器用 sensors_api 導出 → sensors_format 代碼化 → ClickHouse 載入 → 對帳；
  執行節點沒有 SA_BASE_URL／SA_PROJECT／SA_API_SECRET 時任務建立但 blocked（UI 顯示待連線），不會假裝成功。
- reconcile：T2 筆數（ingest_recon）、T5 並行期（events.source = sensors vs ga360 每日量）、T6 登入用戶銜接率，全部從 ClickHouse 算。
"""
from __future__ import annotations

import os
import secrets
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml  # type: ignore
from fastapi import APIRouter, Depends, HTTPException, Query

from ..migration import bridge as bridge_mod
from ..security.policy import Subject, authorize
from .analytics import Executor
from .meta import _rows
from .store import Store

MIGRATION = Path(__file__).resolve().parents[1] / "migration"
KIND_CN = {"auto": "GA 自動", "recommended": "GA 建議事件", "custom": "自訂事件", "not_ga": "不經 GA", "history_only": "僅歷史"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def plan() -> dict[str, Any]:
    return yaml.safe_load((MIGRATION / "inventory.yaml").read_text(encoding="utf-8"))


def sensors_client() -> Any | None:
    """執行節點有神策連線設定才建立客戶端（金鑰只從環境變數讀，不寫入任何回應）。"""
    base, proj, tok = os.environ.get("SA_BASE_URL"), os.environ.get("SA_PROJECT"), os.environ.get("SA_API_SECRET")
    if not (base and proj and tok):
        return None
    from ..integration.sensors_api import SensorsQueryAPI
    return SensorsQueryAPI(base, proj, tok, auth=os.environ.get("SA_AUTH", "query_token"))


def day_slices(date_from: str, date_to: str) -> list[str]:
    a, b = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if b < a or (b - a).days > 1200:
        raise HTTPException(400, "日期範圍不合法（最多 1200 天）")
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


class ExportRunner:
    """一個分片的執行：導出 → 代碼化／去個資 → 載入 → 對帳。client／loader 可注入假物件（自測）。"""

    def __init__(self, client: Any | None, ch: Executor | None, work: Path, user_files: Path | None, key_ref: str = "k_idm"):
        self.client, self.ch, self.work, self.user_files, self.key_ref = client, ch, work, user_files, key_ref

    def run_slice(self, job: dict[str, Any], day: str) -> dict[str, Any]:
        from ..integration.sensors_api import export_to_file
        if self.client is None:
            return {"status": "blocked", "error": "未設定神策連線（SA_BASE_URL／SA_PROJECT／SA_API_SECRET）"}
        out = self.work / f"sa_{job['dataset']}_{day}.jsonl"
        try:
            exp = export_to_file(self.client, day, out, events=job.get("events") or None)
        except Exception as e:
            return {"status": "failed", "error": f"導出失敗：{str(e)[:200]}"}
        res: dict[str, Any] = {"rows_src": exp["rows"], "rows_exported": exp["rows"], "file": str(out), "status": "exported"}
        if self.ch is None or self.user_files is None or not hasattr(self.ch, "insert_json"):
            res["note"] = "未連接 ClickHouse：只導出未載入"
            return res
        try:
            from ..integration.sensors_format import SensorsLoader
            r = SensorsLoader(self.ch, local=True).run_file(out, self.user_files, f"mig_{job['id']}_{day.replace('-', '')}", self.key_ref)
            res.update({"rows_loaded": r["events"], "rows_quarantined": r["stage"].get("bad", 0), "recon_ok": bool(r.get("recon_ok")), "status": "loaded" if r.get("recon_ok") else "mismatch"})
        except Exception as e:
            res.update({"status": "failed", "error": f"載入失敗：{str(e)[:200]}"})
        return res


def build_router(store: Store, get_subject: Any, ch: Executor | None, runner: ExportRunner | None = None) -> APIRouter:
    r = APIRouter()
    work = Path(os.environ.get("DMP_MIGRATION_WORK", "/tmp/dmp-migration")); work.mkdir(parents=True, exist_ok=True)
    uf = Path(os.environ["DMP_CH_USER_FILES"]) if os.environ.get("DMP_CH_USER_FILES") else None
    runner = runner or ExportRunner(sensors_client(), ch, work, uf)
    thresholds = plan().get("recon_thresholds") or {}

    def need(subject: Subject, action: str) -> None:
        d = authorize(subject, action, "governance")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    def jobs() -> list[dict[str, Any]]:
        return sorted(store.items("migration_export"), key=lambda j: j.get("created_at", ""), reverse=True)

    def job_summary(j: dict[str, Any]) -> dict[str, Any]:
        sl = j.get("slices") or []
        done = [s for s in sl if s.get("status") in ("loaded", "exported")]
        src = sum(int(s.get("rows_src") or 0) for s in sl); exp = sum(int(s.get("rows_exported") or 0) for s in sl); load = sum(int(s.get("rows_loaded") or 0) for s in sl)
        status = "pending" if not sl else "blocked" if any(s.get("status") == "blocked" for s in sl) else "failed" if any(s.get("status") == "failed" for s in sl) else "warn" if any(s.get("status") == "mismatch" for s in sl) else "ok" if len(done) == len(sl) else "running"
        return {**{k: v for k, v in j.items() if k != "slices"}, "slices": len(sl), "done": len(done), "rows_src": src, "rows_exported": exp, "rows_loaded": load,
                "diff": ((src - load) / src) if src else None, "status": status, "last": max((s.get("at") or "" for s in sl), default=None), "blocked_reason": next((s.get("error") for s in sl if s.get("status") == "blocked"), None)}

    # ---------- 盤點 ----------
    @r.get("/v1/migration/inventory")
    def inventory(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        p = plan(); inv = next(iter(store.items("migration_inventory")), None)
        by_ds: dict[str, dict[str, Any]] = {}
        for j in jobs():
            s = job_summary(j); d = by_ds.setdefault(j["dataset"], {"rows_loaded": 0, "rows_src": 0, "jobs": 0, "status": "ok"})
            d["rows_loaded"] += s["rows_loaded"]; d["rows_src"] += s["rows_src"]; d["jobs"] += 1
            if s["status"] in ("blocked", "failed", "warn"):
                d["status"] = "warn"
        items = []
        for ds in sorted(p["datasets"], key=lambda x: x.get("order", 9)):
            sa = (inv or {}).get("datasets", {}).get(ds["id"], {})
            prog = by_ds.get(ds["id"])
            items.append({**ds, "rows_sensors": sa.get("rows"), "range": sa.get("range"), "events": sa.get("events"), "inventoried_at": (inv or {}).get("at"),
                          "rows_loaded": prog["rows_loaded"] if prog else (None if not ds.get("done") else None), "jobs": prog["jobs"] if prog else 0,
                          "progress": 100 if ds.get("done") else (round(100 * prog["rows_loaded"] / sa["rows"]) if prog and sa.get("rows") else (0 if not prog else None)),
                          "status": "done" if ds.get("done") else (prog["status"] if prog else ("planned" if not sa else "inventoried"))})
        return {"items": items, "inventoried_at": (inv or {}).get("at"), "users_total": (inv or {}).get("users"), "sensors_configured": runner.client is not None,
                "export_rules": p.get("export_rules"), "timeline": p.get("timeline"), "thresholds": thresholds}

    @r.post("/v1/migration/inventory/refresh")
    def inventory_refresh(body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        if runner.client is None:
            raise HTTPException(503, "此節點未設定神策連線（SA_BASE_URL／SA_PROJECT／SA_API_SECRET）")
        b = body or {}
        d_to = str(b.get("date_to") or (date.today() - timedelta(days=1)).isoformat()); d_from = str(b.get("date_from") or (date.fromisoformat(d_to) - timedelta(days=30)).isoformat())
        try:
            rows = runner.client.inventory(d_from, d_to); users = runner.client.users_count()
        except Exception as e:
            raise HTTPException(502, f"神策查詢失敗：{str(e)[:200]}") from None
        by_event: dict[str, int] = {}; days: set[str] = set()
        for x in rows:
            by_event[str(x.get("event"))] = by_event.get(str(x.get("event")), 0) + int(x.get("cnt") or 0); days.add(str(x.get("date")))
        total = sum(by_event.values())
        rec = {"id": "current", "at": _now(), "by": subject.user_id, "date_from": d_from, "date_to": d_to, "users": users,
               "datasets": {"events": {"rows": total, "range": f"{min(days)} ～ {max(days)}" if days else None, "events": len(by_event)}, "users": {"rows": users, "range": "全量快照 " + d_to}, "id_map": {"rows": users, "range": "全量快照 " + d_to}},
               "by_event": sorted(({"event": k, "rows": v} for k, v in by_event.items()), key=lambda x: -x["rows"])}
        store.put_item("migration_inventory", rec)
        store.audit("migration.inventory.refreshed", {"user": subject.user_id, "date_from": d_from, "date_to": d_to, "events": len(by_event), "rows": total, "users": users})
        return rec

    # ---------- 埋點銜接表 ----------
    @r.get("/v1/migration/bridge")
    def bridge(kind: str | None = None, q: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        rows, up_rows, issues = bridge_mod.build()
        counts: dict[str, int] = {}
        for x in rows:
            counts[x["kind"]] = counts.get(x["kind"], 0) + 1
        items = rows
        if kind:
            items = [x for x in items if x["kind"] == kind]
        if q:
            ql = q.lower(); items = [x for x in items if ql in x["sensors_event"].lower() or ql in x["ga4_event"].lower() or ql in x["cn"].lower()]
        return {"items": [{**x, "kind_cn": KIND_CN.get(x["kind"], x["kind"]), "issues": [i[1] for i in issues if i[0] == x["sensors_event"]]} for x in items], "counts": counts, "total": len(rows),
                "via_ga": sum(v for k, v in counts.items() if k not in ("not_ga", "history_only")), "user_properties": up_rows, "issues": [{"event": a, "issue": b} for a, b in issues],
                "limits": bridge_mod.LIMITS, "csv_path": "backend/migration/ga360_tracking_spec.csv"}

    # ---------- 導出與載入 ----------
    @r.get("/v1/migration/exports")
    def exports(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return {"items": [job_summary(j) for j in jobs()], "sensors_configured": runner.client is not None, "clickhouse": ch is not None, "rules": plan().get("export_rules")}

    @r.get("/v1/migration/exports/{id_}")
    def export_detail(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        j = next((x for x in jobs() if x["id"] == id_), None)
        if not j:
            raise HTTPException(404, "任務不存在")
        return {**job_summary(j), "slices": j.get("slices") or []}

    def run_job(j: dict[str, Any], only_days: list[str] | None = None, max_slices: int = 400) -> dict[str, Any]:
        n = 0
        for s in j["slices"]:
            if only_days and s["day"] not in only_days:
                continue
            if not only_days and s.get("status") in ("loaded", "exported"):
                continue
            if n >= max_slices:
                break
            res = runner.run_slice(j, s["day"]); n += 1
            s.update({**res, "at": _now(), "attempt": int(s.get("attempt", 0)) + 1})
            if res.get("status") == "blocked":
                break                                           # 沒連線：一片就知道，不用每片都試
        store.put_item("migration_export", j)
        return j

    @r.post("/v1/migration/exports", status_code=202)
    def create_export(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        ds = str(body.get("dataset") or "events")
        if ds not in {d["id"] for d in plan()["datasets"]}:
            raise HTTPException(400, "dataset 不在盤點清單")
        method = str(body.get("method") or "api")
        if method not in ("api", "impala"):
            raise HTTPException(400, "method 只能是 api 或 impala")
        days = day_slices(str(body.get("date_from") or ""), str(body.get("date_to") or "")) if ds in ("events", "ma") else [str(body.get("date_to") or date.today().isoformat())]
        j = {"id": "mx_" + secrets.token_hex(4), "dataset": ds, "method": method, "date_from": days[0], "date_to": days[-1], "events": list(body.get("events") or []), "created_by": subject.user_id, "created_at": _now(),
             "slices": [{"day": d, "status": "pending", "attempt": 0} for d in days], "note": str(body.get("note") or "")}
        if method == "impala":
            for s in j["slices"]:
                s.update({"status": "blocked", "error": "Impala／HDFS 直讀由 sensors_export 工具在客戶內網執行，結果以對帳表回寫；本 API 只追蹤進度"})
        store.put_item("migration_export", j)
        store.audit("migration.export.created", {"user": subject.user_id, "job": j["id"], "dataset": ds, "method": method, "slices": len(days)})
        if method == "api" and body.get("run", True):
            run_job(j)
        return {**job_summary(j), "slice_preview": j["slices"][:5]}

    @r.post("/v1/migration/exports/{id_}/rerun", status_code=202)
    def rerun(id_: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        j = next((x for x in jobs() if x["id"] == id_), None)
        if not j:
            raise HTTPException(404, "任務不存在")
        days = [str(d) for d in ((body or {}).get("days") or [])]
        thr = float((plan().get("export_rules") or {}).get("rerun_threshold", 0.001)); max_auto = int((plan().get("export_rules") or {}).get("max_auto_rerun", 3))
        if not days:                                          # 沒指定 → 差異 > 門檻或失敗／阻擋的分片
            days = [s["day"] for s in j["slices"] if s.get("status") in ("failed", "blocked", "mismatch", "pending") or ((s.get("rows_src") or 0) and abs((s.get("rows_src") or 0) - (s.get("rows_loaded") or 0)) / (s.get("rows_src") or 1) > thr)]
        over = [s["day"] for s in j["slices"] if s["day"] in days and int(s.get("attempt", 0)) >= max_auto]
        if over and not (body or {}).get("force"):
            raise HTTPException(409, f"分片 {over[:5]} 已重跑 {max_auto} 次仍有差異，標記待人工；確認後帶 force=true")
        store.audit("migration.export.rerun", {"user": subject.user_id, "job": id_, "days": len(days)})
        run_job(j, only_days=days)
        return {**job_summary(j), "rerun_days": days}

    # ---------- 對帳 ----------
    @r.get("/v1/migration/reconcile")
    def reconcile(days: int = Query(28, ge=1, le=366), project: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        if ch is None:
            raise HTTPException(503, "此環境未連接 ClickHouse")
        pf = f"AND project = '{project}'" if project and project.replace("_", "").isalnum() else ""
        t2 = _rows(ch, f"SELECT task_id, toDate(at) AS day, sum(rows_in) AS rows_in, sum(rows_loaded) AS rows_loaded, sum(rows_quarantined) AS quarantined FROM dmp.ingest_recon WHERE task_id LIKE 'mig_%' AND at >= now() - INTERVAL {days} DAY GROUP BY task_id, day ORDER BY day DESC, task_id LIMIT 500")
        par = _rows(ch, f"SELECT event, event_date AS day, countIf(source = 'sensors') AS sa, countIf(source = 'ga360') AS ga FROM dmp.events WHERE event_date >= today() - {days} {pf} AND source IN ('sensors', 'ga360') GROUP BY event, day HAVING sa > 0 OR ga > 0 ORDER BY day, event")
        by_event: dict[str, dict[str, Any]] = {}
        for x in par:
            e = by_event.setdefault(str(x["event"]), {"event": str(x["event"]), "sa": 0, "ga": 0, "days": 0, "series": []})
            e["sa"] += int(x["sa"]); e["ga"] += int(x["ga"]); e["days"] += 1; e["series"].append({"day": str(x["day"]), "sa": int(x["sa"]), "ga": int(x["ga"])})
        rows_, up_, _ = bridge_mod.build(); ga_name = {x["sensors_event"]: x["ga4_event"] for x in rows_}
        t5_thr = float(thresholds.get("t5_parallel", 0.05))
        parallel = []
        for e in by_event.values():
            diff = ((e["ga"] - e["sa"]) / e["sa"]) if e["sa"] else None
            parallel.append({**e, "ga4_event": ga_name.get(e["event"], e["event"]), "sa_per_day": round(e["sa"] / e["days"]) if e["days"] else 0, "ga_per_day": round(e["ga"] / e["days"]) if e["days"] else 0, "diff": diff, "ok": diff is not None and abs(diff) <= t5_thr})
        parallel.sort(key=lambda x: -(x["sa"] + x["ga"]))
        ub = _rows(ch, f"SELECT uniqExactIf(one_id, has_sa AND has_ga) AS both, uniqExactIf(one_id, has_sa AND NOT has_ga) AS only_sa, uniqExactIf(one_id, has_ga AND NOT has_sa) AS only_ga FROM (SELECT one_id, max(source = 'sensors') AS has_sa, max(source = 'ga360') AS has_ga FROM dmp.events WHERE event_date >= today() - {days} {pf} AND source IN ('sensors', 'ga360') AND one_id LIKE 'v1_%' GROUP BY one_id)")
        u = ub[0] if ub and "both" in ub[0] else {"both": 0, "only_sa": 0, "only_ga": 0}
        login_users = int(u["both"]) + int(u["only_sa"])
        rate = (int(u["both"]) / login_users) if login_users else None
        t2_items = []
        for x in t2:
            ri, rl = int(x.get("rows_in") or 0), int(x.get("rows_loaded") or 0)
            t2_items.append({**x, "diff": ((ri - rl) / ri) if ri else None, "ok": ri == 0 or (ri - rl) / ri <= float(thresholds.get("t2_count", 0.001))})
        return {"days": days, "project": project, "thresholds": thresholds,
                "t2": {"items": t2_items, "ok": all(x["ok"] for x in t2_items) if t2_items else None},
                "t5": {"items": parallel, "events_over": sum(1 for p in parallel if not p["ok"]), "median_abs_diff": (sorted(abs(p["diff"]) for p in parallel if p["diff"] is not None)[len([p for p in parallel if p["diff"] is not None]) // 2] if any(p["diff"] is not None for p in parallel) else None)},
                "t6": {"both": int(u["both"]), "only_sa": int(u["only_sa"]), "only_ga": int(u["only_ga"]), "login_users": login_users, "rate": rate, "ok": rate is not None and rate >= float(thresholds.get("t6_user_bridge", 0.95))},
                "caveats": [{"item": "會話", "note": "DMP 以自己的 session 規則重算，兩邊一致"}, {"item": "$WebClick vs web_click", "note": "GA 只埋主要按鈕，量差屬口徑而非漏埋"}, {"item": "停留／時長", "note": "分析口徑改 session"}, {"item": "同意", "note": "拒絕 analytics_storage 者不做 OneID 合併"}, {"item": "延遲", "note": "串流匯出分鐘級、日批次日到齊；對帳以日批為準"}]}

    return r
