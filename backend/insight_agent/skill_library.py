"""技能庫（docs/39 §7.5；契約 /v1/agents/skills*、#61、#130；規格 docs/superpowers/specs/2026-10-31-ai-workbench-phase1.md C10–C14）。

技能的步驟與工具清單在 SKILL.md（工程審查）；這裡只管「參數與說明」的版本：草案 → 乾跑 → 送審 → 另一人核可 → 新版本。
- 可編輯欄位只有 EDITABLE；工具、模型可見範圍、自動化界線不在資料模型裡，改不到（安全靠 Gateway，不靠這裡）。
- 每次 PATCH 帶 If-Match（revision），改過 revision +1、content_digest 重算、舊乾跑失效；in_review 不可改。
- 送審綁 revision＋content_digest＋同內容的 dry_run_id；核可綁送審時鎖定的值；核可者 ≠ 送出者（409 FOUR_EYES_REQUIRED）。
- 核可只產生 published，前一個 published 改 retired（內容不變，既有 run 照舊可讀）；回復 = 以舊版內容開新草案。
- 乾跑第 5 步只比對設定（C13），兩份真資料結果第 6 步接上 S1 後補。
- 本檔分兩個 PR：5a 是草案、驗證、版本、差異、回復；5b 加乾跑、送審、撤回、核可、退回。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, Response

from ..api.errors import ApiError
from ..security.policy import Subject, authorize
from . import skill_loader

V_KIND, D_KIND = "skill_version", "skill_draft"          # store items：已發布的版本（含 retired）、草案（draft／in_review）
EDITABLE = ("purpose", "refs", "params", "timezone", "readiness", "output", "examples", "notes_md")
PARAM_TYPES = {"int", "number", "string", "enum", "bool", "date_window"}
STATUSES = {"confirmed", "proposed", "pending_client"}
BM_RE, KEY_RE, EX_RE = re.compile(r"^bm_[A-Za-z0-9_]+$"), re.compile(r"^[a-z][a-z0-9_]{0,47}$"), re.compile(r"^[A-D]\d{2}[a-z]?$")
# 說明文字裡明顯的越權指示（D07）：命中就退回。安全不靠這個掃描，靠工具白名單與 Gateway。
OVERREACH = re.compile(r"(列出|匯出|輸出|顯示).{0,8}(手機|電話|身分證|身份證|email|信箱|地址|個資|明細)|直接(送審|核可|發布|推送)|忽略.{0,6}(權限|規則|限制)|ignore (all|previous)", re.I)
DEFAULTS: dict[str, Any] = {"purpose": "", "refs": {}, "params": [], "timezone": "Asia/Taipei", "readiness": {}, "output": [], "examples": [], "notes_md": ""}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def content_of(x: dict[str, Any]) -> dict[str, Any]:
    return {k: x.get(k, DEFAULTS[k]) for k in EDITABLE}


def digest(x: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content_of(x), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pending_params(x: dict[str, Any]) -> list[str]:
    return [p["key"] for p in x.get("params") or [] if p.get("status") != "confirmed"]


def _value_ok(p: dict[str, Any]) -> str | None:
    t, v, a = p.get("type"), p.get("value"), p.get("allowed")
    num = isinstance(v, int | float) and not isinstance(v, bool)
    if t == "int" and not (isinstance(v, int) and not isinstance(v, bool)): return "值必須是整數"
    if t == "number" and not num: return "值必須是數字"
    if t in ("string", "enum") and not isinstance(v, str): return "值必須是字串"
    if t == "bool" and not isinstance(v, bool): return "值必須是 true／false"
    if t == "date_window" and not (isinstance(v, str) and re.fullmatch(r"(last|this)_(day|week|month|quarter)(_vs_prev)?|\d{1,3}d", v)): return "期間格式不正確（例：last_week、last_week_vs_prev、28d）"
    if t == "enum":
        if not (isinstance(a, list) and a and all(isinstance(x, str) for x in a)): return "enum 要有 allowed 選項清單"
        if v not in a: return f"值不在允許的選項內：{a}"
    elif isinstance(a, dict) and t in ("int", "number"):
        lo, hi = a.get("min"), a.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi): return f"值超出允許範圍 {lo}–{hi}"
    elif t in ("int", "number"):
        return "數值參數要有 allowed 範圍 {min, max}"
    return None


class SkillLibrary:
    def __init__(self, store: Any, cat: Any, skills: Any = None):
        self.store, self.cat = store, cat
        self.skill_names = skills or skill_loader.all_skills

    # ---- 權限與讀取
    def need(self, s: Subject, action: str) -> None:
        d = authorize(s, action, "ai_skills")
        if not d.allowed:
            raise ApiError("AUTHZ_DENIED", d.reason or f"需要 ai_skills.{action}", params={"permission": f"ai_skills.{action}"})

    def _known(self, name: str) -> None:
        if name not in self.skill_names():
            raise ApiError("NOT_FOUND", f"技能 {name} 不存在", params={"skill": name})

    def versions(self, name: str) -> list[dict[str, Any]]:
        vs = [x for x in self.store.items(V_KIND) if x["name"] == name]
        return sorted(vs, key=lambda x: tuple(int(n) for n in x["version"].split(".")), reverse=True)

    def published(self, name: str) -> dict[str, Any] | None:
        return next((x for x in self.versions(name) if x["state"] == "published"), None)

    def version(self, name: str, v: str) -> dict[str, Any]:
        x = next((x for x in self.versions(name) if x["version"] == v), None)
        if not x: raise ApiError("NOT_FOUND", f"技能 {name} 沒有版本 {v}", params={"skill": name, "version": v})
        return x

    def drafts(self, name: str) -> list[dict[str, Any]]:
        return sorted((x for x in self.store.items(D_KIND) if x["name"] == name), key=lambda x: x["audit"]["created_at"])

    def draft(self, name: str, did: str) -> dict[str, Any]:
        x = next((x for x in self.drafts(name) if x["draft_id"] == did), None)
        if not x: raise ApiError("NOT_FOUND", f"草案 {did} 不存在", params={"draft_id": did})
        return x

    def summary(self) -> list[dict[str, Any]]:
        out = []
        for n in self.skill_names():
            p = self.published(n); ps = (p or {}).get("params") or []
            out.append({"name": n, "published_version": (p or {}).get("version"), "state": "published" if p else "unpublished", "drafts": len(self.drafts(n)),
                        "params_status": {k: sum(1 for x in ps if x.get("status") == k) for k in ("confirmed", "proposed", "pending_client")}, "last_run_at": None})
        return out

    # ---- 驗證器（存草案與送審都跑）
    def validate(self, c: dict[str, Any]) -> list[dict[str, str]]:
        errs: list[dict[str, str]] = []
        bad = lambda f, m: errs.append({"field": f, "message": m})
        if not isinstance(c.get("purpose"), str) or len(c["purpose"]) > 500: bad("purpose", "用途必須是 500 字以內的字串")
        refs = c.get("refs")
        if not isinstance(refs, dict) or set(refs) - {"bookmarks", "funnels", "metrics", "events"}: bad("refs", "refs 只能有 bookmarks、metrics、events")
        else:
            if refs.get("funnels"): bad("refs.funnels", "funnels 是舊欄位，請改用 bookmarks（書籤系統編號 bm_…）")
            bms = {b["id"] for b in self.store.items("bookmark")}
            for i, b in enumerate(refs.get("bookmarks") or []):
                if not isinstance(b, str) or not BM_RE.match(b): bad(f"refs.bookmarks[{i}]", "要填書籤系統編號 bm_…，不接受名稱或自編 ID")
                elif b not in bms: bad(f"refs.bookmarks[{i}]", f"找不到書籤 {b}")
            for k, have in (("metrics", self.cat.metrics), ("events", self.cat.events)):
                for i, m in enumerate(refs.get(k) or []):
                    if m not in have: bad(f"refs.{k}[{i}]", f"語意層沒有 {m}（要填實際的 key，不接受名稱或別名）")
        params, keys = c.get("params"), set()
        if not isinstance(params, list): bad("params", "params 必須是陣列")
        else:
            for i, p in enumerate(params):
                f = f"params[{i}]"
                if not isinstance(p, dict): bad(f, "每個參數必須是物件"); continue
                if set(p) - {"key", "type", "value", "allowed", "status", "source", "confirmed_by"}: bad(f, "參數只能有 key、type、value、allowed、status、source、confirmed_by")
                k = p.get("key")
                if not isinstance(k, str) or not KEY_RE.match(k): bad(f"{f}.key", "參數名稱只能是小寫英數與底線"); continue
                if k in keys: bad(f"{f}.key", f"參數 {k} 重複")
                keys.add(k)
                if p.get("type") not in PARAM_TYPES: bad(f"{f}.type", f"型別必須是 {sorted(PARAM_TYPES)}"); continue
                if p.get("status") not in STATUSES: bad(f"{f}.status", "狀態必須是 confirmed、proposed、pending_client")
                elif p["status"] == "confirmed" and not (p.get("source") and p.get("confirmed_by")): bad(f"{f}.status", "已核定的參數要附口徑來源（source）與核定人（confirmed_by）")
                if (m := _value_ok(p)): bad(f"{f}.value", m)
        if c.get("timezone") != "Asia/Taipei": bad("timezone", "時區固定 Asia/Taipei")
        rd = c.get("readiness")
        if not isinstance(rd, dict) or set(rd) - {"require_sources", "on_missing"} or rd.get("on_missing", "truncate_and_label") not in ("truncate_and_label", "delay", "stop") \
                or not all(isinstance(x, str) for x in rd.get("require_sources") or []): bad("readiness", "readiness 只能有 require_sources（字串陣列）與 on_missing（truncate_and_label／delay／stop）")
        for k in ("output", "examples"):
            v = c.get(k)
            if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v): bad(k, f"{k} 必須是字串陣列")
        if isinstance(c.get("examples"), list) and any(isinstance(x, str) and not EX_RE.match(x) for x in c["examples"]): bad("examples", "驗收範例要填 docs/40 的案例 ID（例 A01）")
        n = c.get("notes_md")
        if not isinstance(n, str) or len(n) > 4000: bad("notes_md", "說明必須是 4000 字以內的字串")
        elif OVERREACH.search(n): bad("notes_md", "說明不能要求列出個資、跳過權限或直接送審／發布；可見範圍與自動化界線由平台強制，不由說明決定")
        return errs

    def _check(self, c: dict[str, Any]) -> None:
        if (errs := self.validate(c)):
            raise ApiError("INVALID_ARGUMENT", f"草案驗證未過：{errs[0]['field']} {errs[0]['message']}", errors=errs)

    # ---- 草案
    def _save(self, d: dict[str, Any]) -> dict[str, Any]:
        d["content_digest"] = digest(d); self.store.put_item(D_KIND, d); return d

    def create_draft(self, s: Subject, name: str, base: str | None = None, content: dict[str, Any] | None = None, event: str = "agent_skill.drafted") -> dict[str, Any]:
        self.need(s, "use"); self._known(name)
        src = self.version(name, base) if base else self.published(name)
        if content is None: content = content_of(src) if src else dict(DEFAULTS)
        did = "sd_" + secrets.token_hex(6)
        d = {"id": did, "draft_id": did, "name": name, "version": None, "state": "draft", "revision": 1, "base_version": (src or {}).get("version"),
             **content_of(content), "identity": {"rules": f"oneid_rules@{skill_loader.contract_versions()['oneid_rules']}"},
             "audit": {"created_by": s.user_id, "created_at": _now()}, "last_dry_run": None, "submitted": None}
        self._save(d)
        self.store.audit(event, {"user": s.user_id, "skill": name, "draft_id": did, "base_version": d["base_version"]})
        return d

    def patch(self, s: Subject, name: str, did: str, if_match: str | None, body: Any) -> dict[str, Any]:
        self.need(s, "use"); d = self.draft(name, did)
        if not isinstance(body, dict): raise ApiError("INVALID_ARGUMENT", "內容必須是物件")
        extra = sorted(set(body) - set(EDITABLE))
        if extra: raise ApiError("RULE_VIOLATION", f"只能改 {', '.join(EDITABLE)}；工具、可見範圍與自動化界線不在技能庫（不接受：{', '.join(extra)}）", params={"fields": extra})
        if d["state"] != "draft": raise ApiError("CONFLICT", "送審中的草案不能修改，請先撤回", params={"state": d["state"]})
        if str(if_match or "").strip('"') != str(d["revision"]): raise ApiError("CONFLICT", "草案已被改過，請重新載入", params={"revision": d["revision"]})
        new = {**d, **body}; self._check(content_of(new))
        new.update(revision=d["revision"] + 1, last_dry_run=None); new["audit"] = {**d["audit"], "updated_by": s.user_id, "updated_at": _now()}
        return self._save(new)

    def discard(self, s: Subject, name: str, did: str) -> None:
        self.need(s, "use"); d = self.draft(name, did)
        if d["state"] != "draft": raise ApiError("CONFLICT", "送審中的草案不能捨棄，請先撤回")
        self.store.delete_item(D_KIND, did)

    def rollback(self, s: Subject, name: str, body: Any) -> dict[str, Any]:
        to = str((body or {}).get("to_version") or "") if isinstance(body, dict) else ""
        self.need(s, "use"); self._known(name)
        src = self.version(name, to)
        return self.create_draft(s, name, base=to, content=content_of(src), event="agent_skill.rolled_back")

    # ---- 差異
    def _changes(self, a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
        ca, cb = content_of(a), content_of(b)
        return [{"field": k, "before": ca[k], "after": cb[k]} for k in EDITABLE if ca[k] != cb[k]]

    def resolve(self, name: str, ref: str) -> dict[str, Any]:
        return self.draft(name, ref) if ref.startswith("sd_") else self.version(name, ref)

    def diff(self, s: Subject, name: str, frm: str, to: str) -> dict[str, Any]:
        self.need(s, "view"); self._known(name)
        return {"from": frm, "to": to, "changes": self._changes(self.resolve(name, frm), self.resolve(name, to))}


def build_router(lib: SkillLibrary, get_subject: Any) -> APIRouter:
    r = APIRouter(); P = "/v1/agents/skills"

    def view(s: Subject, name: str | None = None) -> None:
        lib.need(s, "view")
        if name: lib._known(name)

    @r.get(P)
    def list_skills(s: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        view(s); return lib.summary()

    @r.get(P + "/{name}")
    def get_skill(name: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        view(s, name); p = lib.published(name)
        if not p: raise ApiError("NOT_FOUND", f"技能 {name} 還沒有發布版本", params={"skill": name})
        return p

    @r.get(P + "/{name}/versions")
    def versions(name: str, s: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        view(s, name); return lib.versions(name)

    @r.get(P + "/{name}/versions/{version}")
    def version(name: str, version: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        view(s, name); return lib.version(name, version)

    @r.get(P + "/{name}/diff")
    def diff(name: str, frm: str = Query(..., alias="from"), to: str = Query(...), s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return lib.diff(s, name, frm, to)

    @r.get(P + "/{name}/drafts")
    def drafts(name: str, s: Subject = Depends(get_subject)) -> list[dict[str, Any]]:
        view(s, name); return lib.drafts(name)

    @r.post(P + "/{name}/drafts", status_code=201)
    def create(name: str, body: Any = Body(None), s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return lib.create_draft(s, name, base=(body or {}).get("base_version") if isinstance(body, dict) else None)

    @r.get(P + "/{name}/drafts/{draft_id}")
    def get_draft(name: str, draft_id: str, s: Subject = Depends(get_subject)) -> dict[str, Any]:
        view(s, name); return lib.draft(name, draft_id)

    @r.patch(P + "/{name}/drafts/{draft_id}")
    def patch(name: str, draft_id: str, body: Any = Body(...), if_match: str | None = Header(None, alias="If-Match"), s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return lib.patch(s, name, draft_id, if_match, body)

    @r.delete(P + "/{name}/drafts/{draft_id}", status_code=204)
    def delete(name: str, draft_id: str, s: Subject = Depends(get_subject)) -> Response:
        lib.discard(s, name, draft_id); return Response(status_code=204)

    @r.post(P + "/{name}/rollback", status_code=201)
    def rollback(name: str, body: Any = Body(...), s: Subject = Depends(get_subject)) -> dict[str, Any]:
        return lib.rollback(s, name, body)

    return r
