"""書籤路由（舊平台「書籤」對應）：分析頁查詢存成可重用、可分享的物件；看板卡片可引用書籤（卡片跟著書籤更新）。
router 只做：驗證 → Policy（建立／更新時以當事人身分編譯一次）→ store → 審計。"""
from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog
from .analytics import RUNNERS, Executor, compile_kind, execute_kind
from .dashboards import CHARTS, _format, _shared, apply_time, editable, visible
from .errors import ApiError
from .store import Conflict, Store

KIND = "bookmark"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_router(store: Store, get_subject: Any, ch: Executor | None, cat: Catalog | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()

    def used_in(bid: str) -> list[dict[str, str]]:
        return [{"dashboard": d["id"], "name": d["name"]} for d in store.items("dashboard") if any(c.get("bookmark") == bid for c in d.get("cards") or [])]

    def out(b: dict[str, Any], s: Subject) -> dict[str, Any]:
        return {**b, "mine": b["owner"] == s.user_id, "editable": editable(b, s), "used_in": used_in(b["id"])}

    def load(id_: str, s: Subject, edit: bool = False) -> dict[str, Any]:
        b = next((x for x in store.items(KIND) if x["id"] == id_), None)
        if not b or not visible(b, s):
            raise HTTPException(404, "書籤不存在或你沒有檢視權")
        if edit and not editable(b, s):
            raise HTTPException(403, "只有擁有者或系統管理員可以修改此書籤")
        return b

    def project_of(b: dict[str, Any]) -> str:
        q = b.get("query")
        return str(q.get("project") or "") if isinstance(q, dict) else ""

    def unique_name(b: dict[str, Any], cur: dict[str, Any] | None) -> None:
        """同一專案內書籤（含漏斗）不可重名，不分擁有者與類型（Windy 2026-10-28）；引用一律用系統編號 id，名稱只給人看。
        看不到的書籤也算，回應不透露是誰的、編號多少。"""
        me = cur["id"] if cur else None
        if any(x["id"] != me and x.get("name") == b["name"] and project_of(x) == project_of(b) for x in store.items(KIND)):
            raise ApiError("CONFLICT", f"書籤名稱「{b['name']}」在這個專案已被使用，請換一個名稱", params={"name": b["name"]})

    def save(b: dict[str, Any]) -> None:
        """寫入時由儲存層的唯一限制再擋一次：兩個請求同時存同名，先查都沒撞到，也只會成功一個。"""
        try:
            store.put_item(KIND, b)
        except Conflict:
            raise ApiError("CONFLICT", f"書籤名稱「{b['name']}」在這個專案已被使用，請換一個名稱", params={"name": b["name"]}) from None

    def validate(body: dict[str, Any], s: Subject, cur: dict[str, Any] | None = None) -> dict[str, Any]:
        b = dict(cur or {})
        if cur is None or "kind" in body or "query" in body:
            kind = str(body.get("kind") or b.get("kind") or "")
            q = body.get("query") if "query" in body else b.get("query")
            if kind not in RUNNERS:
                raise HTTPException(400, f"kind 只能是 {sorted(RUNNERS)}")
            if not isinstance(q, dict) or not q:
                raise HTTPException(400, "query 必須是分析頁的查詢物件")
            compile_kind(kind, s, q, cat)                                   # 沒權限 403、格式錯 400
            b["kind"], b["query"] = kind, q
        if "name" in body or cur is None:
            b["name"] = str(body.get("name") or "").strip()[:80]
            if not b["name"]:
                raise HTTPException(400, "name 必填")
        if "name" in body or cur is None or "query" in body:
            unique_name(b, cur)
        if "description" in body: b["description"] = str(body.get("description") or "")[:400]
        if "chart" in body or cur is None:
            b["chart"] = str(body.get("chart") or "auto")
            if b["chart"] not in CHARTS:
                raise HTTPException(400, f"chart 只能是 {sorted(CHARTS)}")
        if "format" in body or cur is None:
            b["format"] = _format(body)
        if "shared" in body or cur is None: b["shared"] = _shared(body.get("shared"))
        b.setdefault("description", "")
        return b

    @r.get("/v1/bookmarks")
    def list_(kind: str | None = None, subject: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        items = [out(b, subject) for b in store.items(KIND) if visible(b, subject) and (not kind or b["kind"] == kind)]
        return sorted(items, key=lambda b: b["updated_at"], reverse=True)

    @r.post("/v1/bookmarks", status_code=201)
    def create(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        b = validate(body, subject)
        targets = list(dict.fromkeys(str(x) for x in (body.get("add_to") or [])))     # 同一看板寫兩次只加一張（否則可越過 24 張上限）
        try:
            width = int(body.get("width") or 1)
        except (TypeError, ValueError):
            raise HTTPException(400, "width 只能是 1 或 2") from None
        width = width if width in (1, 2) else 1
        if body.get("new_dashboard") and not authorize(subject, "use", "overview").allowed:   # 先檢查完再存：失敗時不留下孤兒書籤（否則重試同名會被當成重名）
            raise HTTPException(403, "需要總覽的可用權限才能建看板")
        for did in targets:
            d = next((x for x in store.items("dashboard") if x["id"] == did), None)
            if not d or not visible(d, subject) or not editable(d, subject):
                raise HTTPException(403, f"看板 {did} 不存在或不可編輯")
            if len(d.get("cards") or []) >= 24:
                raise HTTPException(400, f"看板 {d['name']} 已達 24 張卡片")
        b.update({"id": "bm_" + secrets.token_hex(5), "owner": subject.user_id, "created_at": _now(), "updated_at": _now()})
        save(b)
        store.audit("bookmark.created", {"user": subject.user_id, "bookmark": b["id"], "kind": b["kind"], "name": b["name"]})
        added: list[dict[str, str]] = []
        if body.get("new_dashboard"):
            nd = {"id": "d_" + secrets.token_hex(5), "name": str(body["new_dashboard"]).strip()[:60] or "我的儀表板", "description": "", "owner": subject.user_id, "shared": {"roles": [], "users": []}, "time": {"preset": "30d"}, "cards": [], "created_at": _now(), "updated_at": _now()}
            store.put_item("dashboard", nd); store.audit("dashboard.created", {"user": subject.user_id, "dashboard": nd["id"], "name": nd["name"], "cards": 0}); targets.append(nd["id"])
        for did in targets:
            d = next(x for x in store.items("dashboard") if x["id"] == did)
            c = {"id": "c_" + secrets.token_hex(4), "bookmark": b["id"], "title": b["name"], "kind": b["kind"], "query": b["query"], "chart": b["chart"], "format": b.get("format", "auto"), "width": width, "note": "", "created_by": subject.user_id, "created_at": _now()}
            d["cards"] = [*(d.get("cards") or []), c]; d["updated_at"] = _now(); store.put_item("dashboard", d)
            store.audit("dashboard.card.added", {"user": subject.user_id, "dashboard": did, "card": c["id"], "kind": c["kind"], "bookmark": b["id"]}); added.append({"dashboard": did, "name": d["name"]})
        return {**out(b, subject), "added_to": added}

    @r.get("/v1/bookmarks/{id_}")
    def get(id_: str, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return out(load(id_, subject), subject)

    @r.put("/v1/bookmarks/{id_}")
    def update(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        cur = load(id_, subject, edit=True)
        b = validate(body, subject, cur); b["updated_at"] = _now(); save(b)
        for d in store.items("dashboard"):                                     # 引用它的卡片同步（標題跟著名稱，除非卡片改過名）
            changed = False
            for c in d.get("cards") or []:
                if c.get("bookmark") == id_:
                    if c["title"] == cur["name"]: c["title"] = b["name"]
                    c["kind"], c["query"], c["chart"] = b["kind"], b["query"], b["chart"] if c.get("chart") == cur.get("chart") else c["chart"]; changed = True
            if changed: d["updated_at"] = _now(); store.put_item("dashboard", d)
        store.audit("bookmark.updated", {"user": subject.user_id, "bookmark": id_, "fields": sorted(k for k in body if k in ("name", "description", "query", "kind", "chart", "shared"))})
        return out(b, subject)

    @r.delete("/v1/bookmarks/{id_}", status_code=204)
    def delete(id_: str, subject: Subject = Depends(get_subject)) -> Response:
        load(id_, subject, edit=True)
        store.delete_item(KIND, id_)
        for d in store.items("dashboard"):                                     # 卡片保留快照、標示書籤已刪除
            if any(c.get("bookmark") == id_ for c in d.get("cards") or []):
                for c in d["cards"]:
                    if c.get("bookmark") == id_: c["bookmark_deleted"] = True
                store.put_item("dashboard", d)
        store.audit("bookmark.deleted", {"user": subject.user_id, "bookmark": id_})
        return Response(status_code=204)

    @r.post("/v1/bookmarks/{id_}/data")
    def data(id_: str, body: dict[str, Any] | None = None, subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        b = load(id_, subject)
        try:
            q = apply_time(b["query"], (body or {}).get("time"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        res = execute_kind(b["kind"], store, ch, cat, subject, q, module="bookmark", extra={"bookmark": id_})
        return {**res, "bookmark": {"id": b["id"], "kind": b["kind"], "chart": b["chart"], "format": b.get("format", "auto"), "name": b["name"]}, "time": q.get("time")}

    return r
