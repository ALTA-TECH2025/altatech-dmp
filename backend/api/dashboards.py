"""儀表板路由：/v1/dashboards 與卡片。卡片 = 「一種分析 + 查詢 body」；卡片資料每次以「檢視者」身分重算（遮罩、行級、K 匿名都照檢視者），
不是存結果再給別人看。擁有者／admin 可編輯；分享給角色或使用者只給檢視。router 只做：驗證 → Policy → 執行器 → 審計。"""
from __future__ import annotations

import secrets
import time
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..security.policy import RBAC, Subject, authorize
from ..semantic.compiler import Catalog
from .analytics import RUNNERS, Executor, compile_kind, execute_kind
from .dashboard_share import DashboardShare, resolve_card
from .store import Store

KIND = "dashboard"
CHARTS = {"auto", "line", "area", "bar", "stacked", "pie", "percent", "stat", "table"}
FORMATS = {"auto", "int", "dec2", "pct0", "pct2", "twd0", "twd2"}   # 數值格式（與前端 fmtValue 同一組）


def _format(raw: dict[str, Any], default: str = "auto") -> str:
    f = str(raw.get("format") or default)
    if f not in FORMATS:
        raise HTTPException(400, f"format 只能是 {sorted(FORMATS)}")
    return f
PRESETS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def visible(d: dict[str, Any], s: Subject) -> bool:
    if d.get("owner") == s.user_id or "admin" in s.roles:
        return True
    sh = d.get("shared") or {}
    return bool(set(sh.get("roles") or []) & set(s.roles)) or s.user_id in (sh.get("users") or [])


def editable(d: dict[str, Any], s: Subject) -> bool:
    return d.get("owner") == s.user_id or "admin" in s.roles


def preset_range(preset: str, today: date | None = None) -> dict[str, str]:
    """預設區間 → {from, to}（台北日期，含兩端；到昨天為止，避免當天不完整）。"""
    days = PRESETS.get(preset)
    if not days:
        raise ValueError(f"time.preset 只能是 {sorted(PRESETS)}")
    t = (today or date.today()) - timedelta(days=1)
    return {"from": (t - timedelta(days=days - 1)).isoformat(), "to": t.isoformat()}


def apply_time(query: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """卡片查詢套用儀表板／請求的時間覆寫：preset 或 from/to；保留原本 grain／now。"""
    if not override:
        return query
    tm = dict(query.get("time") or {})
    if override.get("preset"):
        tm.update(preset_range(str(override["preset"]), date.fromisoformat(str(tm["now"])[:10]) if tm.get("now") else None))
    for k in ("from", "to", "grain"):
        if override.get(k):
            tm[k] = override[k]
    return {**query, "time": tm}


def _card(raw: dict[str, Any], subject: Subject, cat: Catalog, store: Store | None = None) -> dict[str, Any]:
    if raw.get("bookmark"):
        b = next((x for x in (store.items("bookmark") if store else []) if x["id"] == raw["bookmark"]), None)
        if not b or not visible(b, subject):
            raise HTTPException(404, "書籤不存在或你沒有檢視權")
        raw = {**raw, "kind": b["kind"], "query": b["query"], "title": raw.get("title") or b["name"], "chart": raw.get("chart") or b.get("chart", "auto"), "format": raw.get("format") or b.get("format", "auto")}
    kind = str(raw.get("kind") or "")
    if kind not in RUNNERS:
        raise HTTPException(400, f"kind 只能是 {sorted(RUNNERS)}")
    q = raw.get("query")
    if not isinstance(q, dict) or not q:
        raise HTTPException(400, "query 必須是分析頁的查詢物件")
    chart = str(raw.get("chart") or "auto")
    if chart not in CHARTS:
        raise HTTPException(400, f"chart 只能是 {sorted(CHARTS)}")
    width = int(raw.get("width") or 1)
    if width not in (1, 2):
        raise HTTPException(400, "width 只能是 1 或 2")
    compile_kind(kind, subject, q, cat)                       # 存之前先以建立者身分編譯一次：查詢格式錯就 400、沒權限就 403
    return {"id": raw.get("id") or "c_" + secrets.token_hex(4), "title": str(raw.get("title") or "")[:80] or "未命名卡片", "kind": kind, "query": q, "chart": chart, "format": _format(raw), "width": width,
            "note": str(raw.get("note") or "")[:400], "created_by": subject.user_id, "created_at": _now(), **({"bookmark": str(raw["bookmark"])} if raw.get("bookmark") else {})}


def _shared(raw: Any) -> dict[str, list[str]]:
    sh = raw if isinstance(raw, dict) else {}
    return {"roles": [str(x) for x in (sh.get("roles") or [])][:20], "users": [str(x) for x in (sh.get("users") or [])][:50]}


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None, share: DashboardShare | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()
    K = int(RBAC["k_anonymity"]["min_group_size"])

    def need(subject: Subject, action: str) -> None:
        d = authorize(subject, action, "overview")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    def load(id_: str, subject: Subject, edit: bool = False) -> dict[str, Any]:
        d = next((x for x in store.items(KIND) if x["id"] == id_), None)
        if not d or not visible(d, subject):
            raise HTTPException(404, "儀表板不存在或你沒有檢視權")
        if edit and not editable(d, subject):
            raise HTTPException(403, "只有擁有者或系統管理員可以編輯此儀表板")
        return d

    def out(d: dict[str, Any], subject: Subject) -> dict[str, Any]:
        return {**d, "origin": d.get("origin"), "mine": d.get("owner") == subject.user_id, "editable": editable(d, subject)}   # origin：AI 草稿（契約 #117 C6），人建為 null

    @r.get("/v1/dashboards")
    def list_(subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view")
        return [{**out(d, subject), "cards": len(d.get("cards") or [])} for d in store.items(KIND) if visible(d, subject)]

    @r.post("/v1/dashboards", status_code=201)
    def create(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        name = str(body.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(400, "name 必填")
        preset = str((body.get("time") or {}).get("preset") or "30d")
        if preset not in PRESETS:
            raise HTTPException(400, f"time.preset 只能是 {sorted(PRESETS)}")
        d = {"id": "d_" + secrets.token_hex(5), "name": name, "description": str(body.get("description") or "")[:400], "owner": subject.user_id, "shared": _shared(body.get("shared")),
             "time": {"preset": preset}, "cards": [_card(c, subject, cat, store) for c in (body.get("cards") or [])[:24]], "created_at": _now(), "updated_at": _now()}
        store.put_item(KIND, d)
        store.audit("dashboard.created", {"user": subject.user_id, "dashboard": d["id"], "name": name, "cards": len(d["cards"])})
        return out(d, subject)

    @r.get("/v1/dashboards/{id_}")
    def get(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return out(load(id_, subject), subject)

    @r.put("/v1/dashboards/{id_}")
    def update(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        d = load(id_, subject, edit=True)
        if "name" in body:
            d["name"] = str(body["name"]).strip()[:60] or d["name"]
        if "description" in body:
            d["description"] = str(body["description"] or "")[:400]
        if "shared" in body:
            d["shared"] = _shared(body["shared"])
        if body.get("time"):
            preset = str(body["time"].get("preset") or d["time"]["preset"])
            if preset not in PRESETS:
                raise HTTPException(400, f"time.preset 只能是 {sorted(PRESETS)}")
            d["time"] = {"preset": preset}
        if "cards" in body:                                     # 只允許重排／改標題寬度；查詢內容改動請刪卡重建（避免偷換查詢繞過建立時的授權檢查）
            by_id = {c["id"]: c for c in d.get("cards") or []}
            cards = []
            for c in body["cards"] or []:
                old = by_id.get(str(c.get("id")))
                if not old:
                    raise HTTPException(400, f"卡片 {c.get('id')} 不存在；新增卡片請用 POST /cards")
                chart = str(c.get("chart") or old["chart"])
                if chart not in CHARTS:
                    raise HTTPException(400, f"chart 只能是 {sorted(CHARTS)}")
                cards.append({**old, "title": str(c.get("title") or old["title"])[:80], "width": int(c.get("width") or old["width"]), "chart": chart, "format": _format(c, str(old.get("format") or "auto")), "note": str(c.get("note") if c.get("note") is not None else old.get("note", ""))[:400]})
            d["cards"] = cards
        d["updated_at"] = _now()
        store.put_item(KIND, d)
        store.audit("dashboard.updated", {"user": subject.user_id, "dashboard": id_, "fields": sorted(k for k in body if k in ("name", "description", "shared", "time", "cards"))})
        return out(d, subject)

    @r.delete("/v1/dashboards/{id_}")
    def delete(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        load(id_, subject, edit=True)
        store.delete_item(KIND, id_)
        store.audit("dashboard.deleted", {"user": subject.user_id, "dashboard": id_})
        return {"ok": True}

    @r.post("/v1/dashboards/{id_}/cards", status_code=201)
    def add_card(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        d = load(id_, subject, edit=True)
        if len(d.get("cards") or []) >= 24:
            raise HTTPException(400, "一個儀表板最多 24 張卡片")
        c = _card(body, subject, cat, store)
        d["cards"] = [*(d.get("cards") or []), c]; d["updated_at"] = _now()
        store.put_item(KIND, d)
        store.audit("dashboard.card.added", {"user": subject.user_id, "dashboard": id_, "card": c["id"], "kind": c["kind"], "bookmark": c.get("bookmark")})
        return c

    @r.delete("/v1/dashboards/{id_}/cards/{card_id}")
    def del_card(id_: str, card_id: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        d = load(id_, subject, edit=True)
        before = len(d.get("cards") or [])
        d["cards"] = [c for c in d.get("cards") or [] if c["id"] != card_id]
        if len(d["cards"]) == before:
            raise HTTPException(404, "卡片不存在")
        d["updated_at"] = _now(); store.put_item(KIND, d)
        store.audit("dashboard.card.removed", {"user": subject.user_id, "dashboard": id_, "card": card_id})
        return {"ok": True}

    @r.post("/v1/dashboards/{id_}/cards/{card_id}/data")
    def card_data(id_: str, card_id: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        """以檢視者身分執行卡片查詢；time 覆寫優先序：請求 body.time → 儀表板 time.preset → 卡片原查詢。"""
        need(subject, "view")
        d = load(id_, subject)
        c = next((x for x in d.get("cards") or [] if x["id"] == card_id), None)
        if not c:
            raise HTTPException(404, "卡片不存在")
        kind, query, warn = resolve_card(store, c)
        override = (body or {}).get("time") or ({"preset": d["time"]["preset"]} if (d.get("time") or {}).get("preset") and not (query.get("time") or {}).get("pinned") else None)
        try:
            q = apply_time(query, override)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        res = execute_kind(kind, store, ch, cat, subject, q, module="dashboard", extra={"dashboard": id_, "card": card_id, "bookmark": c.get("bookmark")})
        if warn: res["warnings"] = [*res.get("warnings", []), warn]
        return {**res, "card": {"id": c["id"], "kind": kind, "chart": c["chart"], "format": c.get("format", "auto"), "title": c["title"], "bookmark": c.get("bookmark")}, "time": q.get("time")}

    # ---- 進階（docs/30）：解讀、HTML 看板、定時寄送；沒有注入 share（舊測試）就 501 ----
    def svc() -> DashboardShare:
        if share is None:
            raise HTTPException(501, "此環境未啟用看板分享功能")
        return share

    @r.post("/v1/dashboards/{id_}/explain")
    def explain(id_: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "view")
        return svc().explain(load(id_, subject), subject, (body or {}).get("time"), apply_time, str((body or {}).get("mode") or "rules"))

    @r.post("/v1/dashboards/{id_}/render", status_code=201)
    def render(id_: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return svc().render(load(id_, subject), subject, body or {}, apply_time, K)

    @r.get("/v1/dashboards/{id_}/renders")
    def renders(id_: str, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view"); load(id_, subject)
        return svc().renders(id_)

    @r.get("/v1/dashboards/{id_}/renders/{aid}")
    def download(id_: str, aid: str, request: Request, token: str | None = None) -> Response:
        """登入且可檢視看板，或帶簽章 token（分享連結）；到期 410。"""
        s = svc(); a = s.get_render(id_, aid)
        if token:
            if token != s.token(aid, a["expires_at"]):
                raise HTTPException(403, "分享連結無效")
            who, via = "share:" + aid, "token"
        else:
            subject = get_subject(request); need(subject, "view"); load(id_, subject); who, via = subject.user_id, "login"
        return Response(content=s.download(a, who, via), media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'inline; filename="{aid}.html"', "X-Robots-Tag": "noindex"})

    @r.get("/v1/dashboards/{id_}/schedules")
    def schedules(id_: str, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        need(subject, "view"); load(id_, subject)
        return svc().schedules(id_)

    @r.post("/v1/dashboards/{id_}/schedules", status_code=202)
    def create_schedule(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use")
        return svc().create_schedule(load(id_, subject), subject, body)

    @r.delete("/v1/dashboards/{id_}/schedules/{sid}", status_code=204)
    def delete_schedule(id_: str, sid: str, subject: Subject = Depends(get_subject)) -> Response:
        need(subject, "use"); load(id_, subject, edit=True)
        svc().delete_schedule(id_, sid, subject.user_id)
        return Response(status_code=204)

    @r.post("/v1/dashboards/{id_}/schedules/{sid}/run")
    def run_schedule(id_: str, sid: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject, "use"); load(id_, subject, edit=True)
        sch = next((x for x in svc().schedules(id_) if x["id"] == sid), None)
        if not sch:
            raise HTTPException(404, "定時寄送不存在")
        if sch["status"] != "active":
            raise HTTPException(409, f"尚未核可（{sch['status']}）")
        return svc().run_schedule(sch, apply_time, K, trigger="manual")

    return r
