"""數據整合路由（openapi Integration）：驗證 → Policy（integration.view／use／admin）→ 連線（auth.py）／數據表／同步任務（scheduler + modeling）→ 審計。

- 連線只存 secret_ref；請求帶明文（secrets_once）要有密鑰庫寫入器（secret_writer），沒有就拒絕；回應永遠遮罩。
- 連線測試是逐步診斷：API 節點只做 dns／tcp 探測（不碰密鑰）；auth 以後的步驟由執行節點探測器注入（probes），沒有就標「不適用」。
- 同步任務 = 建模產物（model yaml）+ 來源 + 排程，交給 Scheduler；立即執行同步跑（乾跑或進 ClickHouse 由 runner 決定）。
- 數據表 = 已登記的外部表（連線、表、主鍵、增量欄位、欄位與敏感等級、Schema 版本）；檔案來源可探索樣本推型別。
"""
from __future__ import annotations

import csv
import json
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..integration import auth as conn_auth
from ..integration import modeling
from ..integration.runner import read_source
from ..integration.scheduler import Scheduler
from ..security.policy import Subject, authorize
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "integration"
PII_HINTS = ("name", "phone", "mobile", "email", "mail", "id_no", "idno", "birth", "addr", "tel")
SCHEMA_CHANGE = {"auto_map": "auto_map", "ignore": "ignore", "pause_and_notify": "pause", "pause": "pause"}


def default_probes(conn: conn_auth.Connection) -> dict[str, Callable[[conn_auth.Connection], tuple[bool, str]]]:
    """API 節點能做的探測：DNS 與 TCP（有 host 才做）。auth／privilege／sample_read 要密鑰，由執行節點注入。"""
    host = conn.fields.get("host") or conn.fields.get("bootstrap", "").split(":")[0]
    if not host:
        return {}
    port = int(conn.fields.get("port") or conn_auth.BY_TYPE[conn.type].get("network", {}).get("port_default", 443))

    def dns(c: conn_auth.Connection) -> tuple[bool, str]:
        try:
            return True, socket.getaddrinfo(host, None)[0][4][0]
        except OSError as e:
            return False, f"{host}: {e}"

    def tcp(c: conn_auth.Connection) -> tuple[bool, str]:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True, f"{port} open"
        except OSError as e:
            return False, f"{host}:{port}: {e}"
    return {"dns": dns, "tcp": tcp}


def infer_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """從樣本推型別與敏感等級提示：欄名像個資 → L4（不可映射）；數值 → number；其餘 string。"""
    out = []
    for k in (rows[0].keys() if rows else []):
        vals = [r.get(k) for r in rows[:200] if r.get(k) not in (None, "")]
        typ = "number" if vals and all(_is_num(v) for v in vals) else "string"
        sens = "L4" if any(h in k.lower() for h in PII_HINTS) else "L1"
        out.append({"name": k, "type": typ, "sensitivity": sens, "null_rate": round(1 - len(vals) / max(1, len(rows[:200])), 3), "sample": "" if sens == "L4" else str(vals[0])[:40] if vals else ""})
    return out


def _is_num(v: Any) -> bool:
    try:
        float(v); return True
    except (TypeError, ValueError):
        return False


def build_router(store: Store, get_subject: Any, sched: Scheduler, secret_writer: Callable[[str, str, str], str] | None = None,
                 probes: Callable[[conn_auth.Connection], dict[str, Any]] | None = None) -> APIRouter:
    r = APIRouter()
    probes = probes or default_probes
    lock = threading.RLock()      # Scheduler 的 SQLite 連線一次只給一個請求用

    def need(subject: Subject, action: str) -> None:
        d = authorize(subject, action, "integration")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    # ---------------------------------------------------------------- 連線
    def _conn(id_: str) -> conn_auth.Connection:
        rec = next((x for x in store.items("connection") if x["id"] == id_), None)
        if not rec:
            raise HTTPException(404, f"連線不存在：{id_}")
        return _to_conn(rec)

    def _to_conn(rec: dict[str, Any]) -> conn_auth.Connection:
        return conn_auth.Connection(id=rec["id"], type=rec["type"], auth_method=rec["auth_method"], fields=rec.get("fields") or {},
                                    secret_refs=rec.get("secret_refs") or {}, tls_verify=rec.get("tls_verify", True), created_by=rec.get("created_by", ""),
                                    rotated_at=datetime.fromisoformat(rec["rotated_at"]) if rec.get("rotated_at") else None, last_test=rec.get("last_test"))

    def _save(conn: conn_auth.Connection, extra: dict[str, Any]) -> dict[str, Any]:
        rec = {"id": conn.id, "type": conn.type, "auth_method": conn.auth_method, "fields": conn.fields, "secret_refs": conn.secret_refs,
               "tls_verify": conn.tls_verify, "created_by": conn.created_by, "rotated_at": conn.rotated_at.isoformat() if conn.rotated_at else None,
               "last_test": conn.last_test, **extra}
        store.put_item("connection", rec)
        return rec

    def _view(rec: dict[str, Any]) -> dict[str, Any]:
        conn = _to_conn(rec)
        return {**conn.redacted(), "owner": rec.get("owner", ""), "enabled": rec.get("enabled", True), "created_at": rec.get("created_at"),
                "tables": sum(1 for t in store.items("ext_table") if t.get("connection") == conn.id),
                "tasks": sum(1 for t in store.items("sync_task") if (t.get("source") or {}).get("connection") == conn.id)}

    @r.get("/v1/integration/connectors")
    def connectors(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return [{"type": c["type"], "cn": c.get("cn", c["type"]), "versions": c.get("versions", []), "auth": c.get("auth", []),
                 "auth_methods": {k: {"fields": v.get("fields", []), "secret_fields": v.get("secret_fields", []), "rotation_days": v.get("rotation_days"), "notes": v.get("notes", "")}
                                  for k, v in (c.get("auth_methods") or {}).items()},
                 "least_privilege": c.get("least_privilege", {}), "capabilities": c.get("capabilities", {}), "incremental": c.get("incremental", {}),
                 "tls": c.get("tls", {}), "network": c.get("network", {}), "driver": c.get("driver", {})} for c in conn_auth.CONNECTORS["connectors"]]

    @r.get("/v1/integration/connections")
    def connections(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return [_view(x) for x in store.items("connection")]

    @r.post("/v1/integration/connections", status_code=201)
    def create_connection(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        cid = str(body.get("id", ""))
        if not cid or any(x["id"] == cid for x in store.items("connection")):
            raise HTTPException(409 if cid else 400, f"連線 id 重複或缺少：{cid}")
        refs = dict(body.get("secret_refs") or {})
        once = body.get("secrets_once") or {}
        if once:
            if secret_writer is None:
                raise HTTPException(422, "此環境未接密鑰庫：請改以 secret_ref 提供密鑰（UI 不接受明文）")
            for k, v in once.items():
                refs[k] = secret_writer(cid, k, str(v))        # 寫入密鑰庫，只留參照；明文不入日誌
        conn = conn_auth.Connection(id=cid, type=str(body.get("type", "")), auth_method=str(body.get("auth_method", "")), fields=body.get("fields") or {},
                                    secret_refs=refs, tls_verify=bool(body.get("tls_verify", True)), created_by=subject.user_id)
        errs = conn.validate()
        if errs:
            raise HTTPException(422, "; ".join(errs))
        rec = _save(conn, {"owner": body.get("owner", ""), "enabled": True, "created_at": datetime.now(UTC).isoformat()})
        store.audit("integration.connection.created", {"user": subject.user_id, "conn_id": cid, "type": conn.type, "auth_method": conn.auth_method})
        return _view(rec)

    @r.get("/v1/integration/connections/{id}")
    def get_connection(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        _conn(id)
        return _view(next(x for x in store.items("connection") if x["id"] == id))

    @r.patch("/v1/integration/connections/{id}")
    def patch_connection(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        rec = next(x for x in store.items("connection") if x["id"] == id) if any(x["id"] == id for x in store.items("connection")) else None
        if not rec:
            raise HTTPException(404, f"連線不存在：{id}")
        conn = _to_conn(rec)
        if "fields" in body:
            conn.fields = {**conn.fields, **(body["fields"] or {})}
        if "tls_verify" in body:
            conn.tls_verify = bool(body["tls_verify"])
        rotated = None
        if body.get("secret_refs"):
            for k, v in body["secret_refs"].items():
                if conn.secret_refs.get(k) != v:
                    rotated = conn_auth.rotate(conn, v, lambda d: store.audit(d.pop("event"), {**d, "user": subject.user_id}))
        errs = conn.validate()
        if errs:
            raise HTTPException(422, "; ".join(errs))
        rec = _save(conn, {k: rec.get(k) for k in ("owner", "enabled", "created_at")} | {"owner": body.get("owner", rec.get("owner", ""))})
        store.audit("integration.connection.updated", {"user": subject.user_id, "conn_id": id, "rotated": bool(rotated)})
        return _view(rec)

    @r.delete("/v1/integration/connections/{id}", status_code=204)
    def delete_connection(id: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject, "admin")
        rec = next((x for x in store.items("connection") if x["id"] == id), None)
        if not rec:
            raise HTTPException(404, f"連線不存在：{id}")
        used = [t["id"] for t in store.items("sync_task") if (t.get("source") or {}).get("connection") == id]
        if used:
            raise HTTPException(409, f"有同步任務使用此連線：{', '.join(used)}")
        store.put_item("connection", {**rec, "enabled": False, "disabled_at": datetime.now(UTC).isoformat()})   # 停用保留審計，不刪
        store.audit("integration.connection.disabled", {"user": subject.user_id, "conn_id": id})
        return Response(status_code=204)

    @r.post("/v1/integration/connections/{id}/test")
    def test_connection(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        conn = _conn(id)
        res = conn_auth.test_connection(conn, probes(conn))
        rec = next(x for x in store.items("connection") if x["id"] == id)
        _save(conn, {k: rec.get(k) for k in ("owner", "enabled", "created_at")})
        store.audit("integration.connection.tested", {"user": subject.user_id, "conn_id": id, "ok": res["ok"], "failed_step": conn.last_test["failed_step"]})
        return res

    @r.post("/v1/integration/connections/{id}/rotate")
    def rotate_connection(id: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "admin")
        conn = _conn(id)
        new_ref = str(body.get("secret_ref", ""))
        if not any(rx.match(new_ref) for rx in conn_auth.SECRET_REF_RE.values()):
            raise HTTPException(422, f"secret_ref 格式不合法：{new_ref}")
        res = conn_auth.rotate(conn, new_ref, lambda d: store.audit(d.pop("event"), {**d, "user": subject.user_id}))
        if not res["ok"]:
            raise HTTPException(409, res["reason"])
        rec = next(x for x in store.items("connection") if x["id"] == id)
        _save(conn, {k: rec.get(k) for k in ("owner", "enabled", "created_at")})
        return {**res, "rotation": conn.rotation_status()}

    @r.get("/v1/integration/connections/{id}/privileges")
    def privileges(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        conn = _conn(id)
        spec = conn_auth.BY_TYPE[conn.type]
        verified = bool(conn.last_test and conn.last_test.get("ok"))
        items = [{"scope": k, "privilege": p, "checked": True if verified else None} for k, lst in (spec.get("least_privilege") or {}).items() for p in lst]
        return {"conn_id": id, "verified_at": conn.last_test["at"] if verified else None, "items": items,
                "note": "" if verified else "尚未通過連線測試；privilege 步驟由執行節點以最小權限清單逐項驗證"}

    # ---------------------------------------------------------------- 數據表
    def _discover(body: dict[str, Any]) -> list[dict[str, Any]]:
        if body.get("fields"):
            return [{"name": f["name"], "type": f.get("type", "string"), "sensitivity": f.get("sensitivity") or ("L4" if any(h in f["name"].lower() for h in PII_HINTS) else "L1")} for f in body["fields"]]
        src = body.get("source") or {}
        if src.get("path"):
            p = Path(src["path"])
            if not p.exists():
                raise HTTPException(400, f"找不到來源檔：{src['path']}")
            return infer_fields(read_source(src)[:1000])
        raise HTTPException(400, "請提供 fields（欄位清單）或檔案來源 source.path 供探索")

    @r.get("/v1/integration/tables")
    def tables(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        tasks = store.items("sync_task")
        return [{**t, "used_by": [k["id"] for k in tasks if (k.get("source") or {}).get("table_id") == t["id"]]} for t in store.items("ext_table")]

    @r.post("/v1/integration/tables", status_code=201)
    def register_table(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        tid = str(body.get("id") or f"{body.get('connection', 'file')}.{body.get('table', '')}".strip("."))
        if not body.get("table"):
            raise HTTPException(400, "缺少 table")
        if any(t["id"] == tid for t in store.items("ext_table")):
            raise HTTPException(409, f"數據表已登記：{tid}")
        if body.get("connection") and not any(x["id"] == body["connection"] for x in store.items("connection")):
            raise HTTPException(400, f"連線不存在：{body['connection']}")
        fields = _discover(body)
        rec = {"id": tid, "connection": body.get("connection", ""), "table": body["table"], "pk": body.get("pk") or [], "incremental_field": body.get("incremental_field"),
               "fields": fields, "schema_version": 1, "changes": [], "source": body.get("source") or {}, "note": body.get("note", ""),
               "registered_by": subject.user_id, "registered_at": datetime.now(UTC).isoformat()}
        missing_pk = [k for k in rec["pk"] if k not in {f["name"] for f in fields}]
        if missing_pk:
            raise HTTPException(400, f"主鍵欄位不存在：{missing_pk}")
        store.put_item("ext_table", rec)
        store.audit("integration.table.registered", {"user": subject.user_id, "table_id": tid, "fields": len(fields), "l4": sum(1 for f in fields if f["sensitivity"] == "L4")})
        return {**rec, "used_by": []}

    @r.post("/v1/integration/tables/{id}/rescan")
    def rescan_table(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        rec = next((t for t in store.items("ext_table") if t["id"] == id), None)
        if not rec:
            raise HTTPException(404, f"數據表不存在：{id}")
        new = _discover({"source": rec.get("source"), "fields": None})
        old = {f["name"]: f for f in rec["fields"]}
        changes = [{"kind": "added", "field": f["name"], "type": f["type"]} for f in new if f["name"] not in old]
        changes += [{"kind": "type_changed", "field": f["name"], "from": old[f["name"]]["type"], "to": f["type"]} for f in new if f["name"] in old and old[f["name"]]["type"] != f["type"]]
        changes += [{"kind": "removed", "field": n} for n in old if n not in {f["name"] for f in new}]
        if changes:
            rec = {**rec, "fields": new, "schema_version": rec["schema_version"] + 1, "changes": [{"at": datetime.now(UTC).isoformat(), "items": changes}] + rec.get("changes", [])}
            store.put_item("ext_table", rec)
            for t in store.items("sync_task"):      # 依任務的 schema_change 策略處理
                if (t.get("source") or {}).get("table_id") == id and t.get("schema_change") == "pause_and_notify":
                    with lock:
                        sched.set_enabled(t["id"], False, f"schema changed: {[c['field'] for c in changes]}")
        store.audit("integration.table.rescanned", {"user": subject.user_id, "table_id": id, "changes": len(changes)})
        return {"table_id": id, "schema_version": rec["schema_version"], "changes": changes}

    # ---------------------------------------------------------------- 同步任務
    def _meta(tid: str) -> dict[str, Any]:
        m = next((t for t in store.items("sync_task") if t["id"] == tid), None)
        if not m:
            raise HTTPException(404, f"任務不存在：{tid}")
        return m

    def _task_view(meta: dict[str, Any], st: dict[str, Any] | None) -> dict[str, Any]:
        return {**meta, "status": "paused" if st and not st["enabled"] else ("error" if st and st["last"] and st["last"]["status"] not in ("success", "running", "skipped") else "active"),
                "enabled": st["enabled"] if st else True, "paused_reason": st["paused_reason"] if st else None, "next_slot": st["next_slot"] if st else None,
                "last": st["last"] if st else None, "watermark": st["watermark"] if st else None}

    @r.get("/v1/integration/sync-tasks")
    def sync_tasks(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        with lock:
            status = {s["id"]: s for s in sched.status()}
        return [_task_view(m, status.get(m["id"])) for m in store.items("sync_task")]

    def _model_path(body: dict[str, Any]) -> str:
        if body.get("model"):                                   # 建模精靈的產物：存成任務自己的 yaml
            p = MODELS_DIR / "models.generated" / f"{body['id']}.yaml"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(yaml.safe_dump({"model": body["model"]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
            return str(p)
        mp = body.get("model_path")
        if not mp:
            raise HTTPException(400, "請提供 model（建模產物）或 model_path")
        p = Path(mp) if Path(mp).is_absolute() else ROOT.parent / mp
        if not p.exists():
            raise HTTPException(400, f"找不到模型：{mp}")
        return str(p)

    @r.post("/v1/integration/sync-tasks", status_code=201)
    def create_task(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        tid = str(body.get("id", ""))
        if not tid or any(t["id"] == tid for t in store.items("sync_task")):
            raise HTTPException(409 if tid else 400, f"任務 id 重複或缺少：{tid}")
        model_path = _model_path(body)
        model = modeling.load(model_path)
        errs = modeling.validate(model, modeling.Semantic.load())
        if errs:
            raise HTTPException(422, "模型未通過驗證：" + "; ".join(errs))
        source = dict(body.get("source") or {})
        if source.get("table_id"):
            t = next((x for x in store.items("ext_table") if x["id"] == source["table_id"]), None)
            if not t:
                raise HTTPException(400, f"數據表不存在：{source['table_id']}")
            source = {**(t.get("source") or {}), "connection": t.get("connection"), "table": t["table"], "incremental_field": source.get("incremental_column") or t.get("incremental_field"), **source}
        sched_body = body.get("schedule") or model.raw.get("schedule") or {}
        cron = sched_body if isinstance(sched_body, str) else sched_body.get("cron")
        interval = None if isinstance(sched_body, str) else sched_body.get("interval_min")
        mode = body.get("mode") or (sched_body.get("mode") if isinstance(sched_body, dict) else None) or "incremental"
        if mode == "stream":
            raise HTTPException(400, "第一階段不支援流任務：請用 interval_min=15 的微批")
        if not cron and not interval:
            interval = 15
        schema_change = body.get("schema_change") or "pause_and_notify"
        try:
            with lock:
                sched.add_task(tid, model_path, cron=cron, interval_min=interval, mode=mode, depends_on=body.get("depends_on") or [],
                               retries=int(body.get("retries", 3)), backoff_s=int(body.get("backoff_s", 60)), schema_change=SCHEMA_CHANGE.get(schema_change, "pause"), source=source)
        except (AssertionError, ValueError) as e:
            raise HTTPException(400, f"排程設定錯誤：{e}") from None
        meta = {"id": tid, "cn": body.get("cn") or model.raw.get("name") or tid, "model_path": model_path, "entity": model.entity,
                "target": body.get("target") or {"type": "events" if model.entity == "event" else "dimension_table" if model.entity == "dim" else "users", "table": model.target_table,
                                                  "write": "append" if model.entity == "event" else "upsert"},
                "source": source, "mode": mode, "schedule": cron or f"every {interval}m", "depends_on": body.get("depends_on") or [],
                "schema_change": schema_change, "events": model.event_names, "fields": [f.get("target") for f in model.fields], "owner": model.raw.get("owner", ""),
                "created_by": subject.user_id, "created_at": datetime.now(UTC).isoformat()}
        store.put_item("sync_task", meta)
        store.audit("integration.task.created", {"user": subject.user_id, "task_id": tid, "mode": mode, "schedule": meta["schedule"]})
        with lock:
            return _task_view(meta, next((s for s in sched.status() if s["id"] == tid), None))

    @r.post("/v1/integration/sync-tasks/{id}/run", status_code=202)
    def run_task(id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        meta = _meta(id)
        try:
            with lock:
                res = sched.run_now(id)
                last = sched.runs(id, 1)
                res = {**(last[0] if last else {}), **{k: v for k, v in res.items() if v is not None}}
        except KeyError:
            raise HTTPException(404, f"排程器沒有任務：{id}") from None
        store.audit("task.rerun", {"user": subject.user_id, "task_id": id, "status": res.get("status"), "rows_in": res.get("rows_in"), "rows_loaded": res.get("rows_loaded")})
        return {"task_id": id, "cn": meta["cn"], **{k: res.get(k) for k in ("status", "scheduled_for", "attempt", "rows_in", "rows_ok", "rows_quarantined", "rows_rejected", "rows_loaded", "error")}}

    @r.get("/v1/integration/sync-tasks/{id}/runs")
    def task_runs(id: str, limit: int = Query(20, ge=1, le=200), subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        _meta(id)
        out = []
        with lock:
            runs = sched.runs(id, limit)
        for x in runs:
            log = x.get("log") or ""
            out.append({**x, "log": log.splitlines() if isinstance(log, str) else log})
        return out

    @r.post("/v1/integration/sync-tasks/{id}/preview")
    def preview_task(id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        meta = _meta(id)
        model = modeling.load(meta["model_path"])
        src = meta.get("source") or {}
        rows: list[dict[str, Any]]
        if body and body.get("rows"):
            rows = list(body["rows"])
        elif src.get("path"):
            if not Path(src["path"]).exists():
                raise HTTPException(400, f"找不到來源檔：{src['path']}")
            rows = read_source(src)
        else:
            raise HTTPException(400, "此來源無法在 API 節點取樣（需執行節點）；可在 body.rows 帶樣本列")
        limit = int((body or {}).get("limit") or 1000)
        res = modeling.preview(model, rows, limit)
        pii = [c for c in res["rejected"] if "pii" in c["reason"].lower() or "個資" in c["reason"]]
        store.audit("integration.task.previewed", {"user": subject.user_id, "task_id": id, **res["summary"]})
        return {**res, "pii_hits": len(pii), "events": model.event_names}

    return r


def write_sample_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """測試用：把列寫成 CSV（給 selftest 與 e2e 建來源檔）。"""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
