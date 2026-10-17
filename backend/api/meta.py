"""元數據／資料治理路由：/v1/meta/*。

語意層 yaml（events／dimensions／user_properties／dimension_tables／virtual_events／oneid_rules）是契約來源；
執行期的登記、狀態轉移、變更單、字典上傳、規則修改存在控制面 store（kind=meta_*）當「覆蓋層」，
回應時合併；統計（日均量、隔離、覆蓋率、列數、OneID 數）從 ClickHouse 讀，沒連線就是 null。
上線到語意層的事件要進 events.yaml 才會出現在查詢目錄（契約 PR）：這裡記錄登記與審計，回應標示 catalog_synced。
router 只做：驗證 → Policy → 合併 → 審計；不在這裡拼 SQL 以外的業務邏輯。"""
from __future__ import annotations

import csv
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

import yaml  # type: ignore
from fastapi import APIRouter, Depends, HTTPException, Query

from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog
from .analytics import Executor
from .store import Store

SEMANTIC = Path(__file__).resolve().parents[1] / "semantic"
MIGRATION = Path(__file__).resolve().parents[1] / "migration"
NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{2,47}$")
PROP_TYPES = {"string", "number", "bool", "datetime", "list", "enum", "date"}
LEVELS = ["L0", "L1", "L2", "L3", "L4"]
STATUS_FLOW = {("draft", "live"), ("quarantine", "live"), ("live", "deprecated"), ("draft", "deprecated")}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((SEMANTIC / name).read_text(encoding="utf-8"))


def _rows(ch: Executor | None, sql: str) -> list[dict[str, Any]]:
    """ClickHouse 統計；沒連線或查詢失敗（表不存在等）→ 空，不讓治理頁打不開。"""
    if ch is None:
        return []
    try:
        txt = ch.query(sql + " FORMAT JSONEachRow", settings={"max_execution_time": 20, "output_format_json_quote_64bit_integers": 0})
        return [json.loads(line) for line in txt.splitlines() if line.strip()]
    except Exception:
        return []


def sensors_names() -> dict[str, dict[str, str]]:
    """屬性 → 客戶神策舊名與顯示名（backend/migration/sensors_properties.csv；docs/28）。"""
    out: dict[str, dict[str, str]] = {}
    p = MIGRATION / "sensors_properties.csv"
    if not p.exists():
        return out
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = r.get("ga4_param") or ""
            if k and k not in out:
                out[k] = {"sa": r.get("property", ""), "display": (r.get("display") or "").lstrip("#"), "sensitivity": r.get("sensitivity", ""), "decision": r.get("decision", "")}
    return out


def check_event(e: dict[str, Any], existing: set[str], forbidden: set[str], is_new: bool) -> list[str]:
    """上線檢查：命名、負責人、顯示名、屬性型別與敏感等級、無 L4（原型與契約同一套規則）。"""
    errs = []
    if not NAME_RE.fullmatch(str(e.get("name", ""))) and not str(e.get("name", "")).startswith("$"):
        errs.append("事件名稱須為小寫英數與 _ .（3–48 字元）")
    if is_new and e.get("name") in existing:
        errs.append("事件名稱已存在")
    if not str(e.get("cn", "")).strip():
        errs.append("請填顯示名稱")
    if not str(e.get("owner", "")).strip():
        errs.append("請填負責人")
    for p in e.get("properties") or []:
        if p.get("type") and p["type"] not in PROP_TYPES:
            errs.append(f"屬性 {p.get('key')} 型別 {p['type']} 不合法")
        if p.get("sensitivity") == "L4" or p.get("key") in forbidden:
            errs.append(f"屬性 {p.get('key')} 為 L4 個資：不得入倉，請移除或改為雜湊／分桶後的 L3／L2 屬性")
    if e.get("entity") not in ("user", "account", "item"):
        errs.append("主體只能是 user／account／item")
    if e.get("kind") not in ("behavior", "business", "trade", "marketing"):
        errs.append("類別只能是 behavior／business／trade／marketing")
    return errs


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()
    sa_names = sensors_names()

    def need(subject: Subject, action: str) -> None:
        d = authorize(subject, action, "governance")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    def overlay(kind: str) -> dict[str, dict[str, Any]]:
        return {x["id"]: x for x in store.items(kind)}

    # ---------- 事件字典 ----------
    def daily_counts() -> dict[str, float]:
        rows = _rows(ch, "SELECT event, count() / 7 AS daily FROM dmp.events WHERE event_date >= today() - 7 GROUP BY event")
        return {str(x["event"]): float(x["daily"]) for x in rows if x.get("event")}

    def merged_events() -> list[dict[str, Any]]:
        ov = overlay("meta_event")
        out = []
        for name, e in cat.events.items():
            o = ov.get(name, {})
            props = [{"key": k, "required": True} for k in e.get("required") or []] + [{"key": k, "required": False} for k in e.get("optional") or []]
            out.append({"name": name, "cn": e.get("cn", name), "group": e.get("group", ""), "entity": e.get("entity", "user"), "source": e.get("source", ""), "kind": e.get("kind", "behavior"),
                        "status": o.get("status") or e.get("status", "live"), "platforms": e.get("platforms", ""), "aliases": e.get("aliases") or [], "properties": props,
                        "owner": o.get("owner") or e.get("owner") or "", "note": o.get("note") or e.get("note") or "", "caveats": e.get("caveats") or [], "catalog_synced": True,
                        "version": o.get("version", 1), "updated_at": o.get("updated_at"), "pending_change": o.get("pending_change")})
        for name, o in ov.items():
            if name not in cat.events:
                out.append({**o, "catalog_synced": False})
        return out

    @r.get("/v1/meta/events")
    def list_events(status: str | None = None, q: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        items = merged_events(); daily = daily_counts()
        for e in items:
            e["daily"] = daily.get(e["name"])
        if status:
            items = [e for e in items if e["status"] == status]
        if q:
            ql = q.lower(); items = [e for e in items if ql in e["name"].lower() or ql in e["cn"].lower() or ql in (e.get("group") or "").lower()]
        counts: dict[str, int] = {}
        for e in merged_events():
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        return {"items": items, "counts": counts, "total": sum(counts.values()), "stats_source": "clickhouse" if daily else None}

    @r.get("/v1/meta/events/{name}")
    def get_event(name: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        e = next((x for x in merged_events() if x["name"] == name), None)
        if not e:
            raise HTTPException(404, "事件不存在")
        hist = [x for x in store.items("meta_event_history") if x.get("event") == name]
        used_by = [f"虛擬事件 {v['name']}" for v in merged_virtual_events() if any(m.get("event") == name for m in v.get("members") or [])]
        return {**e, "history": sorted(hist, key=lambda x: x.get("at", ""))[-20:], "used_by": used_by}

    def record_history(name: str, action: str, subject: Subject, detail: dict[str, Any]) -> None:
        store.put_item("meta_event_history", {"id": "h_" + secrets.token_hex(5), "event": name, "action": action, "by": subject.user_id, "at": _now(), **detail})

    @r.post("/v1/meta/events", status_code=201)
    def create_event(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        existing = {e["name"] for e in merged_events()}
        quarantined = next((e for e in merged_events() if e["name"] == body.get("name") and e["status"] == "quarantine"), None)
        errs = check_event(body, existing - ({quarantined["name"]} if quarantined else set()), cat.forbidden_l4, is_new=True)
        status = str(body.get("status") or "draft")
        if status == "live" and errs:
            raise HTTPException(422, {"message": "上線檢查未通過", "errors": errs})
        if status not in ("draft", "live"):
            raise HTTPException(400, "status 只能是 draft 或 live")
        props = [{"key": str(p.get("key")), "type": p.get("type", "string"), "sensitivity": p.get("sensitivity", "L1"), "required": bool(p.get("required", True))} for p in body.get("properties") or []]
        e = {"id": str(body["name"]), "name": str(body["name"]), "cn": str(body.get("cn", "")), "group": str(body.get("group") or "未分組"), "entity": body.get("entity", "user"), "source": str(body.get("source", "")),
             "kind": body.get("kind", "behavior"), "status": status, "platforms": str(body.get("platforms", "")), "aliases": [], "properties": props, "owner": str(body.get("owner", "")),
             "note": str(body.get("note", "")), "path_node_rule": body.get("path_node_rule"), "version": 1, "created_by": subject.user_id, "updated_at": _now(), "errors": errs if status == "draft" else []}
        store.put_item("meta_event", e)
        for p in props:                                   # 新屬性同步進屬性字典（覆蓋層）
            if p["key"] not in all_property_keys():
                store.put_item("meta_property", {"id": p["key"], "key": p["key"], "cn": p["key"], "type": p["type"], "scope": "event", "sensitivity": p["sensitivity"], "source": e["source"], "status": status, "registered_by": subject.user_id, "at": _now()})
        record_history(e["name"], "published" if status == "live" else "drafted", subject, {"status": status})
        store.audit("meta.event.published" if status == "live" else "meta.event.drafted", {"user": subject.user_id, "event_name": e["name"], "properties": len(props), "quarantine_released": bool(quarantined)})
        return {**e, "catalog_synced": False}

    @r.patch("/v1/meta/events/{name}")
    def patch_event(name: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> Any:
        need(subject, "use")
        cur = next((x for x in merged_events() if x["name"] == name), None)
        if not cur:
            raise HTTPException(404, "事件不存在")
        if "name" in body and body["name"] != name:
            raise HTTPException(400, "上線事件不可改名，只能停用後另建")
        ov = overlay("meta_event").get(name) or {"id": name, "name": name, "version": 1}
        fields = {k: body[k] for k in ("cn", "owner", "note", "source", "kind", "entity", "group", "platforms", "path_node_rule") if k in body}
        if cur["status"] == "live" and ("properties" in body or any(k in body for k in ("entity", "kind"))):
            cr = {"id": "cr_" + secrets.token_hex(4), "event": name, "requested_by": subject.user_id, "at": _now(), "status": "pending", "changes": {k: body[k] for k in ("properties", "entity", "kind") if k in body}, "note": str(body.get("reason") or "")}
            store.put_item("meta_change_request", cr)
            ov = {**ov, **fields, "pending_change": cr["id"], "updated_at": _now()}
            store.put_item("meta_event", ov)
            record_history(name, "change_requested", subject, {"change_request": cr["id"]})
            store.audit("meta.event.change_requested", {"user": subject.user_id, "event_name": name, "change_request": cr["id"], "fields": sorted(cr["changes"])})
            from fastapi.responses import JSONResponse
            return JSONResponse({**cur, **fields, "pending_change": cr["id"], "change_request": cr}, status_code=202)
        if "properties" in body:
            fields["properties"] = [{"key": str(p.get("key")), "type": p.get("type", "string"), "sensitivity": p.get("sensitivity", "L1"), "required": bool(p.get("required", True))} for p in body["properties"] or []]
            errs = check_event({**cur, **fields}, set(), cat.forbidden_l4, is_new=False)
            if any("L4" in x for x in errs):
                raise HTTPException(422, {"message": "含 L4 屬性", "errors": errs})
        ov = {**ov, **fields, "status": ov.get("status") or cur["status"], "version": int(ov.get("version", 1)) + 1, "updated_at": _now()}
        store.put_item("meta_event", ov)
        record_history(name, "updated", subject, {"fields": sorted(fields)})
        store.audit("meta.event.updated", {"user": subject.user_id, "event_name": name, "fields": sorted(fields)})
        return next(x for x in merged_events() if x["name"] == name)

    @r.post("/v1/meta/events/{name}/status")
    def set_status(name: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        cur = next((x for x in merged_events() if x["name"] == name), None)
        if not cur:
            raise HTTPException(404, "事件不存在")
        to = str(body.get("to") or "")
        if (cur["status"], to) not in STATUS_FLOW:
            raise HTTPException(400, f"不允許 {cur['status']} → {to}（允許：草稿／隔離 → 上線、上線 → 停用）")
        if to == "live":
            errs = check_event(cur, set(), cat.forbidden_l4, is_new=False)
            if errs:
                raise HTTPException(422, {"message": "上線檢查未通過", "errors": errs})
        ov = overlay("meta_event").get(name) or {"id": name, "name": name, **{k: cur[k] for k in ("cn", "group", "entity", "source", "kind", "platforms", "properties", "owner", "note")}, "version": 1}
        ov = {**ov, "status": to, "updated_at": _now(), "status_reason": str(body.get("reason") or "")}
        store.put_item("meta_event", ov)
        record_history(name, f"status:{to}", subject, {"from": cur["status"], "reason": ov["status_reason"]})
        store.audit("meta.event.published" if to == "live" else "meta.event.deprecated", {"user": subject.user_id, "event_name": name, "from": cur["status"], "reason": ov["status_reason"]})
        return next(x for x in merged_events() if x["name"] == name)

    @r.get("/v1/meta/change-requests")
    def change_requests(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return sorted(store.items("meta_change_request"), key=lambda x: x.get("at", ""), reverse=True)

    @r.post("/v1/meta/change-requests/{id_}/decide")
    def decide_change(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        """變更單審批（governance.admin）：核准 → 套用屬性變更並升版；駁回 → 清除 pending。"""
        need(subject, "admin")
        cr = next((x for x in store.items("meta_change_request") if x["id"] == id_), None)
        if not cr or cr.get("status") != "pending":
            raise HTTPException(404, "變更單不存在或已處理")
        approve = bool(body.get("approve"))
        if cr.get("requested_by") == subject.user_id and approve:
            raise HTTPException(409, "不可核准自己提出的變更單（雙人複核）")
        ov = overlay("meta_event").get(cr["event"]) or {"id": cr["event"], "name": cr["event"], "version": 1}
        if approve:
            ov = {**ov, **cr["changes"], "version": int(ov.get("version", 1)) + 1}
        ov = {**ov, "pending_change": None, "updated_at": _now()}
        store.put_item("meta_event", ov)
        cr = {**cr, "status": "approved" if approve else "rejected", "decided_by": subject.user_id, "decided_at": _now(), "decision_note": str(body.get("note") or "")}
        store.put_item("meta_change_request", cr)
        record_history(cr["event"], "change_" + cr["status"], subject, {"change_request": id_})
        store.audit("meta.change_request.decided", {"user": subject.user_id, "change_request": id_, "event_name": cr["event"], "approve": approve})
        return cr

    # ---------- 隔離區 ----------
    @r.get("/v1/meta/quarantine")
    def quarantine(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        known = {e["name"] for e in merged_events()} | set(cat.aliases)
        unreg = [x for x in _rows(ch, "SELECT event, count() AS n, min(time) AS first_seen, max(time) AS last_seen, uniqExact(one_id) AS users FROM dmp.events WHERE event_date >= today() - 30 GROUP BY event ORDER BY n DESC") if x.get("event") and x["event"] not in known]
        reasons = _rows(ch, "SELECT task_id, reason, count() AS n, max(at) AS last_at FROM dmp.quarantine WHERE at >= now() - INTERVAL 30 DAY GROUP BY task_id, reason ORDER BY n DESC LIMIT 100")
        ov_q = [e for e in merged_events() if e["status"] == "quarantine"]
        return {"unregistered_events": [{**x, "status": "quarantine", "retention_days": 30} for x in unreg] + [{"event": e["name"], "n": None, "first_seen": None, "last_seen": None, "users": None, "status": "quarantine", "retention_days": 30, "note": e.get("note")} for e in ov_q],
                "rows": [x for x in reasons if x.get("reason")], "policy": {"retention_days": 30, "note": "未登記事件由 Collector 攔進隔離區，日批統計後通知負責人登記；30 天未登記自動丟棄。同步任務的隔離列（枚舉／範圍／必填不符）在數據整合 › 執行紀錄可見。"},
                "stats_source": "clickhouse" if ch is not None else None}

    # ---------- 屬性字典 ----------
    def all_property_keys() -> set[str]:
        keys: set[str] = set()
        for e in merged_events():
            keys |= {p["key"] for p in e.get("properties") or []}
        return keys | set(cat.dimensions) | set(overlay("meta_property"))

    def merged_properties() -> list[dict[str, Any]]:
        usage: dict[str, int] = {}; sources: dict[str, set[str]] = {}
        for e in merged_events():
            for p in e.get("properties") or []:
                usage[p["key"]] = usage.get(p["key"], 0) + 1; sources.setdefault(p["key"], set()).add(e.get("source") or "")
        ov = overlay("meta_property"); dicts = {d["property"]: d for d in merged_dictionaries()}
        dim_items = {}
        for t in merged_dimension_tables():
            for j in t.get("join") or []:
                dim_items[j.get("column")] = t["id"]
        out = []
        for key in sorted(set(usage) | set(cat.dimensions) | set(ov)):
            d = cat.dimensions.get(key, {}); o = ov.get(key, {}); sa = sa_names.get(key, {})
            typ = o.get("type") or d.get("type") or ("number" if key in ("amount", "qty", "fee", "price", "duration_ms") else "string")
            sens = o.get("sensitivity") or d.get("sensitivity") or sa.get("sensitivity") or ("L2" if typ == "number" else "L1")
            out.append({"key": key, "cn": o.get("cn") or d.get("cn") or sa.get("display") or key, "sa": sa.get("sa") or "", "type": typ, "scope": o.get("scope", "event"), "sensitivity": sens,
                        "source": o.get("source") or "／".join(sorted(x for x in sources.get(key, set()) if x)) or d.get("source", ""), "enum": o.get("enum") or d.get("values") or [], "unit": o.get("unit", ""),
                        "used_by": usage.get(key, 0), "dimension_table": dim_items.get(key), "dictionary": key in dicts, "status": o.get("status", "live"), "row_level": bool(d.get("row_level")), "caveats": d.get("caveats") or [], "in_catalog": key in cat.dimensions})
        return out

    @r.get("/v1/meta/properties")
    def properties(q: str | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        items = merged_properties()
        if q:
            ql = q.lower(); items = [p for p in items if ql in p["key"].lower() or ql in p["cn"].lower() or ql in p["sa"].lower()]
        return {"items": items, "forbidden_l4": sorted(cat.forbidden_l4), "levels": {"L0": "聚合", "L1": "行為", "L2": "敏感業務", "L3": "代碼化識別", "L4": "可識別（永不入倉）"}}

    @r.post("/v1/meta/properties", status_code=201)
    def create_property(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        key = str(body.get("key") or "")
        if not re.fullmatch(r"^\$?[a-z][a-z0-9_.]{0,63}$", key):
            raise HTTPException(400, "屬性名稱須為小寫英數與 _ .")
        if body.get("sensitivity") == "L4" or key in cat.forbidden_l4:
            raise HTTPException(422, "L4 個資屬性一律拒絕入倉")
        if body.get("type") not in PROP_TYPES:
            raise HTTPException(400, f"type 只能是 {sorted(PROP_TYPES)}")
        cur = next((p for p in merged_properties() if p["key"] == key), None)
        if cur:
            if LEVELS.index(str(body.get("sensitivity", cur["sensitivity"]))) < LEVELS.index(cur["sensitivity"]):
                raise HTTPException(422, f"敏感等級只能升不能降（目前 {cur['sensitivity']}）")
            if cur["type"] == "number" and body.get("type") != "number":
                raise HTTPException(422, "型別只能放寬，不能把數值改成其他型別")
        p = {"id": key, "key": key, "cn": str(body.get("cn") or key), "type": body["type"], "scope": body.get("scope", "event"), "sensitivity": body.get("sensitivity", "L1"), "source": str(body.get("source", "")),
             "enum": list(body.get("enum") or []), "unit": str(body.get("unit", "")), "status": "live", "registered_by": subject.user_id, "at": _now()}
        store.put_item("meta_property", p)
        store.audit("meta.property.registered", {"user": subject.user_id, "property": key, "sensitivity": p["sensitivity"], "update": bool(cur)})
        return next(x for x in merged_properties() if x["key"] == key)

    # ---------- 虛擬事件 ----------
    def merged_virtual_events() -> list[dict[str, Any]]:
        base = {v["name"]: {**v, "id": v["name"], "origin": "catalog"} for v in _load("virtual_events.yaml").get("virtual_events") or []}
        for o in store.items("virtual_event"):
            base[o["name"]] = {**base.get(o["name"], {}), **o, "origin": "registered"}
        return [{**v, "definition": " OR ".join(m["event"] + ("（" + "，".join(f"{f['field']} {f['op']} {f.get('value', '')}" for f in m.get("filters") or []) + "）" if m.get("filters") else "") for m in v.get("members") or [])} for v in base.values()]

    @r.get("/v1/meta/virtual-events")
    def virtual_events(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return merged_virtual_events()

    @r.post("/v1/meta/virtual-events", status_code=201)
    def create_virtual_event(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        name = str(body.get("name") or "")
        if not re.fullmatch(r"^v\.[a-z][a-z0-9_]{1,40}$", name):
            raise HTTPException(400, "虛擬事件名稱須為 v. 開頭的小寫英數底線")
        if name in cat.events:
            raise HTTPException(409, "與真實事件重名")
        members = body.get("members") or []
        if not members or not all(isinstance(m, dict) and m.get("event") for m in members):
            raise HTTPException(400, "members 至少一個，每個要有 event")
        bad = [m["event"] for m in members if not cat.canonical_event(str(m["event"])) and m["event"] not in {e["name"] for e in merged_events()}]
        if bad:
            raise HTTPException(400, f"事件不存在：{bad}")
        v = {"id": name, "name": name, "cn": str(body.get("cn") or name), "owner": str(body.get("owner") or subject.user_id), "members": [{"event": str(m["event"]), **({"filters": m["filters"]} if m.get("filters") else {})} for m in members],
             "note": str(body.get("note") or ""), "created_by": subject.user_id, "at": _now()}
        store.put_item("virtual_event", v)
        store.audit("meta.virtual_event.created", {"user": subject.user_id, "name": name, "members": len(members)})
        return next(x for x in merged_virtual_events() if x["name"] == name)

    @r.delete("/v1/meta/virtual-events/{name}")
    def delete_virtual_event(name: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        if name not in {x["name"] for x in store.items("virtual_event")}:
            raise HTTPException(404, "只能刪除執行期登記的虛擬事件；語意檔裡的請走契約 PR")
        store.delete_item("virtual_event", name)
        store.audit("meta.virtual_event.deleted", {"user": subject.user_id, "name": name})
        return {"ok": True}

    # ---------- 用戶表屬性 ----------
    def coverage() -> dict[str, float]:
        keys = [k for k in cat.stored_props if k in cat.user_props]
        if not keys or ch is None:
            return {}
        exprs = ", ".join(f"countIf(toString({k}) != '' AND toString({k}) != '0') / count() AS `{k}`" for k in keys if cat.user_props[k].get("type") != "number") + \
                "".join(f", countIf({k} IS NOT NULL) / count() AS `{k}`" for k in keys if cat.user_props[k].get("type") == "number")
        rows = _rows(ch, f"SELECT {exprs.lstrip(', ')} FROM dmp.users FINAL")
        return {k: float(v) for k, v in (rows[0].items() if rows else []) if isinstance(v, int | float)}

    @r.get("/v1/meta/user-properties")
    def user_properties(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        cov = coverage(); ov = overlay("user_property")
        up_sa = sensors_names()
        total = _rows(ch, "SELECT count() AS n FROM dmp.users FINAL")
        items = []
        for k, p in cat.user_props.items():
            o = ov.get(k, {})
            items.append({"key": k, "cn": p.get("cn", k), "sa": up_sa.get(k, {}).get("sa", ""), "type": p.get("type", "string"), "values": p.get("values") or [], "source": p.get("source", ""), "update": str(p.get("update", "")),
                          "sensitivity": p.get("sensitivity", "L1"), "filterable": bool(p.get("filterable")), "groupable": bool(p.get("groupable")), "row_level_key": bool(p.get("row_level_key")), "buckets": p.get("buckets"),
                          "note": o.get("note") or p.get("note", ""), "stored": k in cat.stored_props, "hot": k in cat.hot_props, "coverage": cov.get(k), "origin": "catalog"})
        for k, o in ov.items():
            if k not in cat.user_props:
                items.append({**o, "coverage": None, "stored": False, "hot": False, "origin": "registered"})
        return {"items": items, "users_total": int(total[0]["n"]) if total and "n" in total[0] else None, "write_protocol": _load("user_properties.yaml").get("write_protocol") or [],
                "forbidden_l4_count": len(cat.forbidden_l4), "stats_source": "clickhouse" if cov else None}

    @r.post("/v1/meta/user-properties", status_code=201)
    def create_user_property(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        key = str(body.get("key") or "")
        if not re.fullmatch(r"^[a-z][a-z0-9_]{1,40}$", key):
            raise HTTPException(400, "屬性名稱須為小寫英數底線")
        if key in cat.forbidden_l4 or body.get("sensitivity") == "L4":
            raise HTTPException(422, "L4 個資屬性永不入倉")
        if key in cat.user_props:
            raise HTTPException(409, "屬性已存在於語意層（修改請走契約 PR）")
        p = {"id": key, "key": key, "cn": str(body.get("cn") or key), "type": body.get("type", "string"), "values": list(body.get("values") or []), "source": str(body.get("source", "")), "update": str(body.get("update", "daily")),
             "sensitivity": body.get("sensitivity", "L1"), "filterable": bool(body.get("filterable", True)), "groupable": bool(body.get("groupable", False)), "note": str(body.get("note", "")), "registered_by": subject.user_id, "at": _now(), "status": "pending_contract"}
        store.put_item("user_property", p)
        store.audit("meta.user_property.registered", {"user": subject.user_id, "property": key, "sensitivity": p["sensitivity"]})
        return {**p, "origin": "registered", "coverage": None, "stored": False, "hot": False}

    # ---------- OneID 規則 ----------
    @r.get("/v1/meta/oneid/rules")
    def oneid_rules(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        base = _load("oneid_rules.yaml"); ov = next(iter(store.items("oneid_rules")), None)
        ids = ov["ids"] if ov else base["ids"]
        short = lambda k: k.replace("$identity_", "")                                  # id_edges 的 id_type 用短名（login_id／device_id…）
        by_type = {short(str(x["id_type"])): x for x in _rows(ch, "SELECT id_type_a AS id_type, count() AS edges, uniqExact(one_id) AS users FROM dmp.id_edges FINAL WHERE status = 'active' GROUP BY id_type_a")}
        tot = _rows(ch, "SELECT uniqExact(one_id) AS users, uniqExactIf(one_id, id_type_a IN ('login_id','mobile_hash','email_hash','account_no','$identity_login_id','$identity_mobile_hash','$identity_email_hash','$identity_account_no')) AS identified FROM dmp.id_edges FINAL WHERE status = 'active'")
        merges = _rows(ch, "SELECT op, count() AS n FROM dmp.oneid_merge_log WHERE at >= now() - INTERVAL 30 DAY GROUP BY op")
        backfill = _rows(ch, "SELECT count() AS n FROM dmp.oneid_backfill WHERE applied_at >= now() - INTERVAL 1 DAY")
        return {"version": base.get("version"), "alias": base.get("alias") or {}, "ids": [{**i, "stats": by_type.get(short(i["key"]))} for i in ids], "strong_ids": base.get("strong_ids") or [],
                "anonymous_user": base.get("anonymous_user") or {}, "login_backfill": base.get("login_backfill") or {},
                "stats": {"users": tot[0].get("users") if tot else None, "identified": tot[0].get("identified") if tot else None, "merges_30d": {m["op"]: m["n"] for m in merges if m.get("op")}, "backfill_24h": backfill[0].get("n") if backfill else None},
                "override": {"by": ov.get("by"), "at": ov.get("at"), "status": ov.get("status")} if ov else None, "stats_source": "clickhouse" if tot else None}

    @r.put("/v1/meta/oneid/rules")
    def put_oneid_rules(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        """只允許改優先序、上限、超限策略、狀態；識別類型本身與 login_id 不可改。需 governance.admin；寫入後標 pending_review（稽核審批後生效，影響標籤重算）。"""
        need(subject, "admin")
        base = _load("oneid_rules.yaml")["ids"]; by_key = {i["key"]: i for i in base}
        ids = body.get("ids")
        if not isinstance(ids, list) or {i.get("key") for i in ids} != set(by_key):
            raise HTTPException(400, "ids 必須包含且只包含既有識別類型")
        out = []
        for i in ids:
            b = by_key[i["key"]]
            if b["key"] == "$identity_login_id" and (i.get("priority", 1) != 1 or i.get("status", "enabled") != "enabled"):
                raise HTTPException(422, "客戶代碼永遠優先級 1 且不可停用")
            out.append({**b, "priority": int(i.get("priority", b["priority"])), "max_count": i.get("max_count", b.get("max_count")), "over_limit": i.get("over_limit", b.get("over_limit")), "status": i.get("status", b.get("status", "enabled"))})
        if sorted(x["priority"] for x in out) != sorted(b["priority"] for b in base):
            raise HTTPException(422, "優先序只能重排，不能重複")
        rec = {"id": "current", "ids": sorted(out, key=lambda x: x["priority"]), "by": subject.user_id, "at": _now(), "status": "pending_review", "reason": str(body.get("reason") or "")}
        store.put_item("oneid_rules", rec)
        store.audit("meta.oneid.rules_changed", {"user": subject.user_id, "reason": rec["reason"], "status": "pending_review"})
        return oneid_rules(subject)

    # ---------- 維度表 ----------
    def merged_dimension_tables() -> list[dict[str, Any]]:
        base = {t["id"]: {**t, "origin": "catalog"} for t in _load("dimension_tables.yaml").get("dimension_tables") or []}
        for o in store.items("dimension_table"):
            base[o["id"]] = {**base.get(o["id"], {}), **o, "origin": o.get("origin", "registered")}
        return list(base.values())

    def dim_rows() -> dict[str, int]:
        return {str(x["name"]): int(x["rows"]) for x in _rows(ch, "SELECT name, total_rows AS rows FROM system.tables WHERE database = 'dmp' AND name LIKE 'dim_%'") if x.get("name")}

    @r.get("/v1/meta/dimension-tables")
    def dimension_tables(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        rows = dim_rows(); tasks = {t["id"]: t for t in store.items("sync_task")}
        rules = _load("dimension_tables.yaml").get("rules") or {}
        items = []
        for t in merged_dimension_tables():
            n = rows.get(t["id"])
            items.append({**t, "rows": n, "exists": t["id"] in rows, "engine_effective": "dictionary" if (n or 0) > int(rules.get("max_rows_for_join", 1_000_000)) else t.get("engine", "join"),
                          "task": {"id": t.get("source"), "status": tasks[t["source"]].get("status"), "last": tasks[t["source"]].get("last")} if t.get("source") in tasks else None,
                          "exposed": [c["key"] for c in t.get("columns") or [] if c.get("expose", c.get("exposed", True))]})
        return {"items": items, "rules": rules, "stats_source": "clickhouse" if rows else None}

    @r.get("/v1/meta/dimension-tables/{id_}")
    def dimension_table(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        t = next((x for x in dimension_tables(subject)["items"] if x["id"] == id_), None)
        if not t:
            raise HTTPException(404, "維度表不存在")
        used = [f"虛擬屬性 {v['name']}" for v in merged_virtual_properties() if id_ in (v.get("uses") or [])] + [f"維度字典 {d['property']}" for d in merged_dictionaries() if isinstance(d.get("source"), dict) and d["source"].get("dimension_table") == id_]
        return {**t, "used_by": used}

    @r.post("/v1/meta/dimension-tables", status_code=201)
    def create_dimension_table(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        id_ = str(body.get("id") or "")
        if not re.fullmatch(r"^dim_[a-z0-9_]+$", id_):
            raise HTTPException(400, "id 須為 dim_ 開頭的小寫英數底線")
        if id_ in {t["id"] for t in merged_dimension_tables()}:
            raise HTTPException(409, "維度表已存在")
        joins = body.get("join") or []
        if not joins or not body.get("primary_key"):
            raise HTTPException(400, "需要 primary_key 與至少一條 join")
        prop_keys = all_property_keys() | set(cat.user_props)
        for j in joins:
            if j.get("table") not in ("events", "users") or not j.get("column"):
                raise HTTPException(400, "join 需要 table（events／users）與 column")
            if j["column"] not in prop_keys and j["column"] not in ("symbol", "account_no", "branch_id", "agent_id", "campaign_id", "utm_campaign"):
                raise HTTPException(422, f"關聯屬性 {j['column']} 不存在於事件／用戶屬性")
        cols = [{"key": str(c.get("key")), "cn": str(c.get("cn") or c.get("key")), "type": c.get("type", "string"), "sensitivity": c.get("sensitivity", "L1"), "expose": bool(c.get("exposed", c.get("expose", True)))} for c in body.get("columns") or []]
        if any(c["sensitivity"] == "L4" for c in cols):
            raise HTTPException(422, "維度表欄位不得為 L4")
        if body.get("source") and body["source"] not in {t["id"] for t in store.items("sync_task")}:
            raise HTTPException(422, f"同步任務 {body['source']} 不存在（先在數據整合建立）")
        t = {"id": id_, "cn": str(body.get("cn") or id_), "primary_key": str(body["primary_key"]), "item_type": str(body.get("item_type") or id_[4:]), "join": [{"table": j["table"], "column": j["column"], **({"dim_column": j["dim_column"]} if j.get("dim_column") else {})} for j in joins],
             "source": str(body.get("source") or ""), "sync": str(body.get("sync") or ""), "engine": "join", "columns": cols, "origin": "registered", "created_by": subject.user_id, "at": _now()}
        store.put_item("dimension_table", t)
        store.audit("meta.dimension_table.created", {"user": subject.user_id, "id": id_, "columns": len(cols)})
        return next(x for x in dimension_tables(subject)["items"] if x["id"] == id_)

    @r.patch("/v1/meta/dimension-tables/{id_}")
    def patch_dimension_table(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        cur = next((x for x in merged_dimension_tables() if x["id"] == id_), None)
        if not cur:
            raise HTTPException(404, "維度表不存在")
        upd: dict[str, Any] = {k: body[k] for k in ("cn", "join", "sync") if k in body}
        if "columns" in body:
            old = {c["key"]: c for c in cur.get("columns") or []}
            cols = []
            for c in body["columns"] or []:
                o = old.get(c.get("key"), {})
                sens = c.get("sensitivity", o.get("sensitivity", "L1"))
                if o and LEVELS.index(sens) < LEVELS.index(o.get("sensitivity", "L1")):
                    raise HTTPException(422, f"欄位 {c.get('key')} 敏感等級只能升不能降")
                if sens == "L4":
                    raise HTTPException(422, "維度表欄位不得為 L4")
                cols.append({**o, "key": c.get("key"), "cn": c.get("cn", o.get("cn", c.get("key"))), "type": c.get("type", o.get("type", "string")), "sensitivity": sens, "expose": bool(c.get("exposed", c.get("expose", o.get("expose", o.get("exposed", True)))))})
            upd["columns"] = cols
        rec = {**{k: v for k, v in cur.items() if k != "origin"}, **upd, "id": id_, "origin": cur.get("origin", "catalog"), "updated_by": subject.user_id, "updated_at": _now()}
        store.put_item("dimension_table", rec)
        store.audit("meta.dimension_table.updated", {"user": subject.user_id, "id": id_, "fields": sorted(upd)})
        return next(x for x in dimension_tables(subject)["items"] if x["id"] == id_)

    @r.post("/v1/meta/dimension-tables/{id_}/sync", status_code=202)
    def sync_dimension_table(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        cur = next((x for x in merged_dimension_tables() if x["id"] == id_), None)
        if not cur:
            raise HTTPException(404, "維度表不存在")
        task = next((t for t in store.items("sync_task") if t["id"] == cur.get("source")), None)
        store.audit("dimension.sync", {"user": subject.user_id, "id": id_, "task": cur.get("source"), "task_registered": bool(task)})
        if not task:
            return {"accepted": False, "id": id_, "task": cur.get("source"), "message": f"來源同步任務 {cur.get('source') or '（未設定）'} 尚未在數據整合登記；請先建立任務再立即同步"}
        return {"accepted": True, "id": id_, "task": task["id"], "message": "已送交排程器；進度見數據整合 › 同步任務", "run_via": "/v1/integration/sync-tasks/{id}/run"}

    # ---------- 維度字典 ----------
    def merged_dictionaries() -> list[dict[str, Any]]:
        base = {d["property"]: {**d, "id": d["property"], "origin": "catalog", "updated_at": None} for d in _load("dimension_tables.yaml").get("dictionaries") or []}
        for o in store.items("dictionary"):
            base[o["id"]] = {**base.get(o["id"], {}), **o, "origin": "uploaded"}
        out = []
        for d in base.values():
            entries = d.get("entries") or {}
            out.append({**d, "entries": entries, "count": len(entries), "mode": d.get("mode", "1_to_1"), "sample": list(entries.items())[:6]})
        return out

    @r.get("/v1/meta/dictionaries")
    def dictionaries(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return [{k: v for k, v in d.items() if k != "entries"} for d in merged_dictionaries()]

    @r.get("/v1/meta/dictionaries/{prop}")
    def get_dictionary(prop: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        d = next((x for x in merged_dictionaries() if x["property"] == prop), None)
        if not d:
            raise HTTPException(404, "字典不存在")
        return d

    @r.put("/v1/meta/dictionaries/{prop}")
    def put_dictionary(prop: str, body: dict[str, Any], mode: str = Query("append"), subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        """上傳字典：body.entries {值: 顯示名} 或 body.csv（兩欄，值,顯示名）；mode=append 保留既有、overwrite 先清空。只限文字／數值屬性。"""
        need(subject, "use")
        p = next((x for x in merged_properties() if x["key"] == prop), None)
        if not p or p["type"] not in ("string", "number", "enum"):
            raise HTTPException(422, "只能給文字／數值屬性建字典")
        entries: dict[str, str] = {str(k): str(v) for k, v in (body.get("entries") or {}).items()}
        if body.get("csv"):
            for line in str(body["csv"]).splitlines():
                parts = [x.strip() for x in line.split(",", 1)]
                if len(parts) == 2 and parts[0] and not parts[0].startswith("#"):
                    entries[parts[0]] = parts[1]
        if not entries:
            raise HTTPException(400, "沒有可用的值→顯示名")
        if len(entries) > 50_000:
            raise HTTPException(422, "字典最多 5 萬筆")
        cur = next((x for x in merged_dictionaries() if x["property"] == prop), None)
        merged = ({**(cur.get("entries") or {})} if cur and mode == "append" else {}) | entries
        mode_kind = "n_to_1" if len(set(merged.values())) < len(merged) else "1_to_1"
        rec = {"id": prop, "property": prop, "mode": mode_kind, "source": "manual_upload", "entries": merged, "uploaded_by": subject.user_id, "updated_at": _now(), "upload_mode": mode}
        store.put_item("dictionary", rec)
        store.audit("dictionary.uploaded", {"user": subject.user_id, "property": prop, "mode": mode, "entries": len(entries), "total": len(merged)})
        return get_dictionary(prop, subject)

    # ---------- 虛擬屬性 ----------
    def merged_virtual_properties() -> list[dict[str, Any]]:
        base = {v["name"]: {**v, "id": v["name"], "origin": "catalog"} for v in _load("dimension_tables.yaml").get("virtual_properties") or []}
        for o in store.items("virtual_property"):
            base[o["name"]] = {**base.get(o["name"], {}), **o, "origin": "registered"}
        return list(base.values())

    @r.get("/v1/meta/virtual-properties")
    def virtual_properties(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return merged_virtual_properties()

    @r.post("/v1/meta/virtual-properties", status_code=201)
    def create_virtual_property(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        name = str(body.get("name") or ""); expr = str(body.get("expr") or "")
        if not re.fullmatch(r"^v\.[a-z][a-z0-9_]{1,40}$", name):
            raise HTTPException(400, "虛擬屬性名稱須為 v. 開頭的小寫英數底線")
        if name[2:] in all_property_keys() or name[2:] in cat.user_props or name in {v["name"] for v in merged_virtual_properties()}:
            raise HTTPException(409, "名稱不可與既有屬性重複")
        if not expr or len(expr) > 1024:
            raise HTTPException(400, "表達式必填且 ≤ 1024 字元")
        if re.search(r"\b(insert|alter|drop|delete|system|grant)\b", expr, re.I) or ";" in expr:
            raise HTTPException(422, "表達式只能是純運算式")
        uses = sorted({m for m in re.findall(r"dictGet\('([a-z_]+)'", expr)} | {m for m in re.findall(r"\b(dim_[a-z0-9_]+)\.", expr)})
        unknown = [u for u in uses if u not in {t["id"] for t in merged_dimension_tables()}]
        if unknown:
            raise HTTPException(422, f"引用的維度表不存在：{unknown}")
        typ = "number" if re.search(r"^\s*(toHour|toInt|toFloat|count|sum|length|dateDiff)\b", expr) and "multiIf" not in expr else "bool" if re.search(r"\bBETWEEN\b|[<>=]", expr) and "multiIf" not in expr and "'" not in expr else "string"
        v = {"id": name, "name": name, "cn": str(body.get("cn") or name), "scope": body.get("scope", "event"), "expr": expr, "uses": uses, "type": typ, "note": str(body.get("note") or ""), "created_by": subject.user_id, "at": _now()}
        store.put_item("virtual_property", v)
        store.audit("meta.virtual_property.created", {"user": subject.user_id, "name": name, "uses": uses})
        return {**v, "origin": "registered"}

    return r
