"""問數路由：/v1/analytics/ask（通用）、/v1/analytics/ask/status、/v1/dashboards/{id}/ask（用看板期間）。
router 只做：驗證（ai_ask.use）→ configure（rules 或 llm 解析器）→ 每張草稿以當事人身分乾跑編譯 → 審計 dashboard.asked。不存卡片：確認後走 POST /cards 或 /v1/bookmarks。"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..insight_agent.configure import LLMParser, RulesParser, configure
from ..security.policy import Subject, authorize
from ..semantic.compiler import Catalog
from .analytics import compile_kind
from .dashboards import PRESETS, preset_range, visible
from .store import Store

EXAMPLES = ["上週各平台成交金額、看盤到委託到成交的漏斗、登入後 4 週留存", "近 7 天各渠道開戶申請人數", "成交之前做什麼", "推播點擊對成交的貢獻"]


def build_router(store: Store, get_subject: Any, cat: Catalog | None = None, llm: LLMParser | None = None) -> APIRouter:
    r = APIRouter()
    cat = cat or Catalog()
    rules = RulesParser(cat)

    def need(subject: Subject) -> None:
        d = authorize(subject, "use", "ai_ask")
        if not d.allowed:
            raise HTTPException(403, d.reason)

    def pick(name: str) -> Any:
        if name == "llm" and llm is None:
            raise HTTPException(400, "此環境未設定 LLM 金鑰（DMP_LLM_API_KEY_REF），只有 rules 解析器")
        return llm if (name == "llm" or (name == "auto" and llm is not None)) else rules

    def run(body: dict[str, Any], subject: Subject, default_time: dict[str, Any], dashboard: str | None) -> dict[str, Any]:
        q = str(body.get("question") or "").strip()
        if not q or len(q) > 500:
            raise HTTPException(400, "question 必填且 ≤ 500 字")
        parser = pick(str(body.get("parser") or "auto"))
        res = configure(q, cat, parser=parser, default_time=default_time, today=date.today(), project=body.get("project"), compile_fn=lambda k, qq: compile_kind(k, subject, qq, cat)[0], max_cards=min(int(body.get("max_cards") or 6), 6))
        store.audit("dashboard.asked", {"user": subject.user_id, "dashboard": dashboard, "question": q[:200], "parser": res["parser"], "cards": len(res["cards"]), "rejected": len(res["rejected"]), "run_id": res["run_id"]})
        if not res["cards"] and not res["rejected"]:
            raise HTTPException(422, {"message": "問題對不上任何分析模型或語意層事件", **{k: res[k] for k in ("candidates", "unmatched", "run_id", "parser")}})
        return {**res, "dashboard": dashboard}

    def default_time(body: dict[str, Any], preset: str = "30d") -> dict[str, Any]:
        t = body.get("time") or {}
        if t.get("from") and t.get("to"):
            return {"from": str(t["from"]), "to": str(t["to"]), "grain": str(t.get("grain") or "day")}
        p = str(t.get("preset") or preset)
        if p not in PRESETS:
            raise HTTPException(400, f"time.preset 只能是 {sorted(PRESETS)}")
        return {**preset_range(p), "grain": "day"}

    @r.get("/v1/analytics/ask/status")
    def status(subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        return {"allowed": authorize(subject, "use", "ai_ask").allowed, "parsers": ["rules", *(["llm"] if llm else [])], "default": "llm" if llm else "rules", "model": getattr(llm, "model", None), "examples": EXAMPLES,
                "note": "" if llm else "未設定 LLM 金鑰：用內建規則解析（事件同義詞、分析類型關鍵字、分組與時間）；設定 DMP_LLM_API_KEY_REF 後自動改用模型解析，草稿同樣會再乾跑編譯。"}

    @r.post("/v1/analytics/ask")
    def ask(body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject)
        return run(body, subject, default_time(body), None)

    @r.post("/v1/dashboards/{id_}/ask")
    def ask_dashboard(id_: str, body: dict[str, Any], subject: Subject = Depends(get_subject)) -> dict[str, Any]:
        need(subject)
        d = next((x for x in store.items("dashboard") if x["id"] == id_), None)
        if not d or not visible(d, subject):
            raise HTTPException(404, "儀表板不存在或你沒有檢視權")
        return run(body, subject, default_time(body, str((d.get("time") or {}).get("preset") or "30d")), id_)

    return r
