"""analysis.configure：自然語言 → 既有分析模型的配置卡（docs/30 §1；舊平台 Copilot「在分析模型上做選擇題」的做法）。

兩個解析器產同一份結構：
- RulesParser：關鍵字規則（分析類型、事件同義詞、指標、分組、時間），離線可用、確定性、示範站也用（web/src/demo/ask.ts 是同構的 TS 版）。
- LLMParser：企業 LLM Gateway 或 Claude API（anthropic SDK，可選依賴），結構化輸出同一份 JSON schema；提示只放語意層目錄，不放資料。
兩者的草稿都再以當事人身分 compile_kind 乾跑：沒權限 403、格式錯 400 的卡片丟掉並回原因；信心度 < 0.3 只給候選。

自測：python3 -m backend.insight_agent.configure（evals/ask_golden.yaml 20 題：model 正確率 ≥ 95%、關鍵參數 ≥ 85%）。
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml  # type: ignore

from ..semantic.compiler import Catalog

KINDS = ("events", "funnel", "retention", "path", "attribution")
KIND_WORDS: dict[str, list[str]] = {
    "funnel": ["漏斗", "轉換率", "轉化率", "轉換", "轉化", "→", "->", "到"],
    "retention": ["留存", "回訪", "回來"],
    "path": ["路徑", "之後做什麼", "接著", "下一步", "之前做什麼"],
    "attribution": ["歸因", "貢獻", "觸點", "功勞"],
}
# 事件同義詞（口語 → 事件名）；目錄的 cn／name 也會比對
SYNONYMS: dict[str, list[str]] = {
    "bond_deal": ["成交", "交易成功", "成交筆", "成交金額", "成交量"],
    "bond_order": ["委託", "下單", "掛單"],
    "app_login_success": ["登入", "登錄", "登陸", "login"],
    "hq_stockdetail_view": ["看盤", "個股", "行情", "看行情"],
    "$pageview": ["瀏覽頁面", "頁面瀏覽", "瀏覽", "pv", "訪問"],
    "kh_apply": ["開戶申請", "完成開戶", "開戶"],
    "kh_review_pass": ["審核通過", "開戶成功"],
    "sip_purchase": ["定期定額", "申購"],
    "search_submit": ["搜尋", "搜索"],
    "push_click": ["推播點擊", "推播", "push"],
    "info_report_view": ["研究報告", "研報"],
    "line_follow": ["line 加好友", "加好友"],
}
METRIC_WORDS: list[tuple[list[str], str, str | None]] = [   # (關鍵字, agg, field)
    (["金額", "總額", "成交額", "額度"], "sum", "amount"),
    (["人數", "用戶數", "客戶數", "多少人", "幾人", "使用者數", "用戶", "客戶"], "users", None),
    (["人均", "平均每人"], "avg_per_user", None),
    (["次數", "筆數", "筆", "量", "次"], "total", None),
]
DIM_WORDS: dict[str, list[str]] = {"platform": ["平台", "ios", "android", "web"], "channel": ["渠道", "通路"], "utm_source": ["來源", "流量來源"], "market": ["市場"], "branch_id": ["分公司"], "lifecycle": ["生命週期"], "age_band": ["年齡"], "app_version": ["版本"]}
TIME_UNITS = {"天": "day", "日": "day", "週": "week", "周": "week", "月": "month"}
CN_NUM = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十四": 14, "三十": 30, "九十": 90}


def _num(s: str) -> int | None:
    if s.isdigit(): return int(s)
    return CN_NUM.get(s)


def parse_time(text: str, today: date, default: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """「上週」「近 7 天」「本月」「10 月」「昨天」→ {from,to,grain}；回 (time, pinned)。沒提到就用 default（看板區間）。"""
    t = text
    if "上週" in t or "上周" in t:
        mon = today - timedelta(days=today.weekday() + 7); return {"from": mon.isoformat(), "to": (mon + timedelta(days=6)).isoformat(), "grain": "day"}, True
    if "本週" in t or "本周" in t or "這週" in t:
        mon = today - timedelta(days=today.weekday()); return {"from": mon.isoformat(), "to": today.isoformat(), "grain": "day"}, True
    if "上個月" in t or "上月" in t:
        first = today.replace(day=1) - timedelta(days=1); return {"from": first.replace(day=1).isoformat(), "to": first.isoformat(), "grain": "day"}, True
    if "本月" in t or "這個月" in t:
        return {"from": today.replace(day=1).isoformat(), "to": today.isoformat(), "grain": "day"}, True
    if "昨天" in t or "昨日" in t:
        y = today - timedelta(days=1); return {"from": y.isoformat(), "to": y.isoformat(), "grain": "day"}, True
    m = re.search(r"(?:近|最近|過去)\s*(\d+|[一二兩三四五六七八九十]+)\s*(天|日|週|周|月)", t)
    if m:
        n = _num(m.group(1)) or 7; unit = TIME_UNITS[m.group(2)]; days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return {"from": (today - timedelta(days=days - 1)).isoformat(), "to": today.isoformat(), "grain": "week" if unit == "week" and n >= 4 else "day"}, True
    m = re.search(r"(\d{1,2})\s*月(?!\s*\d)", t)
    if m and 1 <= int(m.group(1)) <= 12:
        mo = int(m.group(1)); y = today.year if mo <= today.month else today.year - 1
        end = (date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)); return {"from": date(y, mo, 1).isoformat(), "to": min(end, today).isoformat(), "grain": "day"}, True
    return dict(default), False


class RulesParser:
    name = "rules"

    def __init__(self, cat: Catalog):
        self.cat = cat
        self.lex: list[tuple[str, str]] = []                                        # (關鍵字, 事件名)，長的先比
        for ev, words in SYNONYMS.items():
            if ev in cat.events:
                self.lex += [(w.lower(), ev) for w in words]
        for name, e in cat.events.items():
            cn = str(e.get("cn") or "")
            if cn and cn != name: self.lex.append((cn.lower(), name))
            self.lex.append((name.lower(), name))
            for part in cn.split("_")[1:]:                                           # 「交易_委託送出」→「委託送出」
                if len(part) >= 3: self.lex.append((part.lower(), name))
        self.lex.sort(key=lambda kv: -len(kv[0]))

    def find_events(self, text: str) -> list[tuple[int, str, str]]:
        """回 (位置, 事件名, 命中的詞)，依出現順序、去重。"""
        t = text.lower(); found: dict[str, tuple[int, str]] = {}; used: list[tuple[int, int]] = []
        for w, ev in self.lex:
            i = t.find(w)
            while i >= 0:
                if not any(a <= i < b or a < i + len(w) <= b for a, b in used):
                    used.append((i, i + len(w)))
                    if ev not in found or found[ev][0] > i: found[ev] = (i, w)
                    break
                i = t.find(w, i + 1)
        return sorted([(pos, ev, w) for ev, (pos, w) in found.items()])

    def kind_of(self, text: str, n_events: int) -> tuple[str, bool]:
        for k, words in KIND_WORDS.items():
            if any(w in text for w in words) and (k != "funnel" or n_events >= 2 or "漏斗" in text or "轉" in text):
                return k, True
        return "events", False

    def parse(self, question: str, *, default_time: dict[str, Any], today: date, project: str | None = None) -> dict[str, Any]:
        clauses = [c.strip() for c in re.split(r"[，,；;。\n]|以及|、(?=[^，。]{6,})", question) if c.strip()][:6]
        cards, unmatched = [], []
        for cl in clauses:
            evs = self.find_events(cl)
            kind, explicit = self.kind_of(cl, len(evs))
            time, pinned = parse_time(cl, today, default_time)
            if pinned: time["pinned"] = True
            if not evs:
                unmatched.append(cl); continue
            names = [e for _, e, _ in evs]; cn = lambda n: str(self.cat.events[n].get("cn") or n)
            conf = 0.9 if explicit else 0.75
            base: dict[str, Any] = {"time": time, **({"project": project} if project else {})}
            if kind == "funnel":
                steps = names if len(names) >= 2 else [names[0], "bond_deal" if names[0] != "bond_deal" and "bond_deal" in self.cat.events else names[0]]
                if len(names) < 2: conf -= 0.3
                q = {**base, "steps": [{"event": n, "alias": cn(n).split("_")[-1]} for n in steps], "window": {"value": 7, "unit": "day"}, "order": "loose", "metric": "users"}
                title = " → ".join(cn(n).split("_")[-1] for n in steps) + " 漏斗"; expl = f"漏斗：{len(steps)} 步（7 天視窗），步驟依問題出現順序"
            elif kind == "retention":
                unit = "week" if ("週" in cl or "周" in cl) else ("month" if "月" in cl and "個月" in cl else "day")
                m = re.search(r"(\d+|[一二兩三四五六七八九十]+)\s*(週|周|天|日|個月)", cl); periods = (_num(m.group(1)) if m else None) or (4 if unit == "week" else 7)
                ret = names[1] if len(names) > 1 else names[0]
                q = {**base, "initial": {"event": names[0]}, "return": {"event": ret}, "model": "interval", "unit": unit, "periods": min(int(periods), 12), "include_day0": False}
                title = f"{cn(names[0])} 後 {periods} {'週' if unit == 'week' else '天' if unit == 'day' else '月'}留存"; expl = f"留存：初始 {cn(names[0])}、回訪 {cn(ret)}、{unit}"
            elif kind == "path":
                direction = "before" if "之前" in cl else "after"
                m = re.search(r"(\d+)\s*步", cl); steps_n = int(m.group(1)) if m else 3
                q = {**base, "anchor": {"event": names[0]}, "direction": direction, "steps": steps_n, "sample": 0.1}
                title = f"{cn(names[0])} {'之前' if direction == 'before' else '之後'} {steps_n} 步路徑"; expl = f"路徑：以 {cn(names[0])} 為起點，{steps_n} 步，抽樣 1/10"
            elif kind == "attribution":
                target = next((n for n in names if n in ("bond_deal", "kh_apply", "sip_purchase")), names[-1])
                touches = [n for n in names if n != target] or [n for n in ("$pageview", "hq_stockdetail_view", "app_login_success") if n in self.cat.events and n != target]
                q = {**base, "target": {"event": target, **({"value_field": "amount"} if target == "bond_deal" else {})}, "touches": [{"event": n, "alias": cn(n).split("_")[-1]} for n in touches], "models": ["first", "last", "linear"], "window_days": 7}
                title = f"{cn(target)} 歸因（首次／末次／線性）"; expl = f"歸因：轉化 {cn(target)}，觸點 {len(touches)} 個，7 天回溯"
            else:
                agg, field = "total", None
                for words, a, f in METRIC_WORDS:
                    if any(w in cl for w in words): agg, field = a, f; break
                metrics = [{"event": n, "agg": agg, **({"field": field} if field else {}), "alias": f"m{i + 1}"} for i, n in enumerate(names[:3])]
                group_by = [d for d, words in DIM_WORDS.items() if any(w in cl.lower() for w in words) and (d in self.cat.dimensions)]
                q = {**base, "metrics": metrics, **({"group_by": group_by[:1]} if group_by else {})}
                aggcn = {"total": "總次數", "users": "觸發用戶數", "sum": "金額", "avg_per_user": "人均次數"}[agg]
                title = "、".join(cn(n) for n in names[:3]) + f" {aggcn}" + (f"（依{self.cat.dimensions[group_by[0]].get('cn', group_by[0])}）" if group_by else "")
                expl = f"事件分析：{aggcn}" + ("，分組 " + "、".join(group_by[:1]) if group_by else "") + ("，時間依問題釘住" if pinned else "，時間跟看板走")
                if not explicit and not any(w in cl for ws, _, _ in METRIC_WORDS for w in ws): conf -= 0.1
            cards.append({"title": title[:80], "kind": kind, "query": q, "chart": "auto", "confidence": round(max(0.1, min(conf, 0.98)), 2), "explanation": expl + f"；事件：{', '.join(f'{cn(n)}（{n}）' for n in names)}"})
        return {"cards": cards, "unmatched": unmatched}


LLM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["cards", "unmatched"],
    "properties": {
        "unmatched": {"type": "array", "items": {"type": "string"}},
        "cards": {"type": "array", "maxItems": 6, "items": {"type": "object", "additionalProperties": False, "required": ["title", "kind", "query", "confidence", "explanation"],
                  "properties": {"title": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)}, "query": {"type": "object", "additionalProperties": True},
                                 "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "explanation": {"type": "string"}}}},
    },
}


def catalog_digest(cat: Catalog, limit: int = 160) -> str:
    evs = [f"{n}（{e.get('cn', '')}）" for n, e in cat.events.items() if e.get("status", "live") != "deprecated"][:limit]
    dims = [f"{n}（{d.get('cn', '')}）" for n, d in cat.dimensions.items()]
    props = [f"user.{n}（{p.get('cn', '')}）" for n, p in cat.user_props.items() if p.get("groupable")]
    return "事件：" + "、".join(evs) + "\n分組維度：" + "、".join(dims) + "\n用戶屬性（可分組）：" + "、".join(props)


SYSTEM_PROMPT = """你是證券 DMP 的分析配置助手。使用者用中文問數，你只能在五種既有分析模型上做選擇，並只能使用下面語意層目錄裡的事件、維度與用戶屬性；不要生成 SQL，不要發明不存在的事件。
每個問題拆成最多 6 張卡片，每張卡片回 title、kind、query、confidence（0–1）、explanation（為什麼這樣配，繁體中文）。對不上目錄的片段放到 unmatched。
query 格式（time 只在問題明確提到期間時才給 {from,to,grain,pinned:true}，否則省略讓看板決定）：
- events：{metrics:[{event,agg:total|users|sum|avg_per_user,field?,alias}], group_by?:[維度名或 user.屬性], filters?:[], time?}
- funnel：{steps:[{event,alias}], window:{value,unit:day}, order:loose, metric:users, time?}
- retention：{initial:{event}, return:{event}, model:interval, unit:day|week|month, periods:int, include_day0:false, time?}
- path：{anchor:{event}, direction:after|before, steps:int, sample:0.1, time?}
- attribution：{target:{event,value_field?}, touches:[{event,alias}], models:[first,last,linear], window_days:7, time?}
信心度：事件與模型都明確 ≥ 0.8；要猜事件 0.4–0.6；完全對不上就不要出卡片。"""


class LLMParser:
    """anthropic SDK（可選依賴）；base_url 可指向企業 LLM Gateway。結構化輸出 → 與 RulesParser 同一份卡片結構。"""
    name = "llm"

    def __init__(self, cat: Catalog, api_key: str, model: str = "claude-opus-5-5", base_url: str | None = None, client: Any | None = None):
        self.cat, self.model = cat, model
        if client is not None:
            self.client = client
        else:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=60.0) if base_url else anthropic.Anthropic(api_key=api_key, timeout=60.0)

    def parse(self, question: str, *, default_time: dict[str, Any], today: date, project: str | None = None) -> dict[str, Any]:
        system = [{"type": "text", "text": SYSTEM_PROMPT + "\n\n語意層目錄：\n" + catalog_digest(self.cat), "cache_control": {"type": "ephemeral"}}]
        user = f"今天是 {today.isoformat()}；看板預設期間 {default_time.get('from', '')}～{default_time.get('to', '')}。問題：{question}"
        resp = self.client.beta.messages.create(model=self.model, max_tokens=4000, system=system, messages=[{"role": "user", "content": user}],
                                                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": LLM_SCHEMA}})
        if getattr(resp, "stop_reason", None) == "refusal":
            return {"cards": [], "unmatched": [question], "refused": True}
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        for c in data.get("cards", []):
            c.setdefault("chart", "auto")
            if project and isinstance(c.get("query"), dict): c["query"].setdefault("project", project)
        return {"cards": data.get("cards", [])[:6], "unmatched": data.get("unmatched", [])}

    def narrate(self, dashboard: str, period: str, facts: dict[str, Any]) -> str:
        """看板解讀敘述版：只拿規則算出的事實（不含明細列）寫三段。"""
        user = f"看板「{dashboard}」{period}。事實：\n" + json.dumps(facts, ensure_ascii=False) + "\n請用繁體中文寫三段以內的經營解讀：先整體，再值得注意的卡片與可能原因（用「可能」），最後建議下一步要看什麼。不要編造事實裡沒有的數字。"
        resp = self.client.beta.messages.create(model=self.model, max_tokens=1500, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                system="你是證券公司數位經營分析師，語氣務實，只依據給定事實。", messages=[{"role": "user", "content": user}], output_config={"effort": "medium"})
        if getattr(resp, "stop_reason", None) == "refusal":
            return ""
        return "".join(b.text for b in resp.content if b.type == "text").strip()


def validate_cards(cards: list[dict[str, Any]], compile_fn: Callable[[str, dict[str, Any]], Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """每張草稿乾跑編譯：成功附 sql_preview／warnings，失敗進 rejected。compile_fn(kind, query) → Compiled 或丟例外（HTTPException／ValueError）。"""
    ok, rejected = [], []
    for c in cards:
        try:
            comp = compile_fn(c["kind"], c["query"])
            ok.append({**c, "sql_preview": (getattr(comp, "sql", "") or "")[:600], "warnings": list(getattr(comp, "warnings", []) or []), "selected": float(c.get("confidence", 0)) >= 0.6})
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            rejected.append({"title": c.get("title"), "kind": c.get("kind"), "reason": str(detail)[:200]})
    return ok, rejected


def configure(question: str, cat: Catalog, *, parser: Any, default_time: dict[str, Any], today: date | None = None, project: str | None = None, compile_fn: Callable[[str, dict[str, Any]], Any] | None = None, max_cards: int = 6) -> dict[str, Any]:
    today = today or date.today()
    raw = parser.parse(question, default_time=default_time, today=today, project=project)
    cards = [c for c in raw["cards"] if float(c.get("confidence", 0)) >= 0.3][:max_cards]
    low = [c for c in raw["cards"] if float(c.get("confidence", 0)) < 0.3]
    ok, rejected = validate_cards(cards, compile_fn) if compile_fn else ([{**c, "sql_preview": None, "warnings": [], "selected": float(c.get("confidence", 0)) >= 0.6} for c in cards], [])
    cand_events = []
    for c in low:
        for m in (c.get("query") or {}).get("metrics", []) or []:
            if m.get("event") in cat.events: cand_events.append({"name": m["event"], "cn": cat.events[m["event"]].get("cn", m["event"])})
    return {"run_id": "ask_" + uuid.uuid4().hex[:10], "parser": parser.name, "cards": ok, "rejected": rejected, "candidates": {"events": cand_events, "dimensions": list(cat.dimensions)[:10]},
            "unmatched": list(raw.get("unmatched", [])), "refused": bool(raw.get("refused"))}


# ---------------- 自測：golden 20 題 ----------------
def selftest() -> None:
    cat = Catalog(); p = RulesParser(cat)
    golden = yaml.safe_load((Path(__file__).resolve().parent / "evals" / "ask_golden.yaml").read_text(encoding="utf-8"))["cases"]
    today = date(2026, 10, 17); ok_model = ok_params = 0; total = len(golden); fails = []
    for g in golden:
        res = configure(g["question"], cat, parser=p, default_time={"from": "2026-09-17", "to": "2026-10-16", "grain": "day"}, today=today)
        exp = g["expect"]; cards = res["cards"]
        if exp.get("no_cards"):
            if not cards: ok_model += 1; ok_params += 1
            else: fails.append((g["id"], "expected no cards", [c["kind"] for c in cards]))
            continue
        if not cards: fails.append((g["id"], "no cards", res["unmatched"])); continue
        c = cards[0]; q = c["query"]
        if c["kind"] == exp["kind"]: ok_model += 1
        else: fails.append((g["id"], "kind", c["kind"])); continue
        checks = []
        if "events" in exp:
            got = [m["event"] for m in q.get("metrics", [])] if c["kind"] == "events" else [s["event"] for s in q.get("steps", [])] if c["kind"] == "funnel" else [q.get("initial", {}).get("event"), q.get("return", {}).get("event")] if c["kind"] == "retention" else [q.get("anchor", {}).get("event")] if c["kind"] == "path" else [q.get("target", {}).get("event")]
            checks.append(all(e in got for e in exp["events"]))
        if "agg" in exp: checks.append(q["metrics"][0]["agg"] == exp["agg"])
        if "group_by" in exp: checks.append(q.get("group_by") == exp["group_by"])
        if "time" in exp: checks.append(q.get("time", {}).get("from") == exp["time"]["from"] and q.get("time", {}).get("to") == exp["time"]["to"])
        if "unit" in exp: checks.append(q.get("unit") == exp["unit"])
        if "direction" in exp: checks.append(q.get("direction") == exp["direction"])
        if "cards" in exp: checks.append(len(cards) == exp["cards"])
        if "min_confidence" in exp: checks.append(c["confidence"] >= exp["min_confidence"])
        if all(checks): ok_params += 1
        else: fails.append((g["id"], "params", {"q": q, "checks": checks}))
    print(f"ask golden: model {ok_model}/{total} ({ok_model / total:.0%}), params {ok_params}/{total} ({ok_params / total:.0%})")
    for f in fails: print("  FAIL", f)
    assert ok_model / total >= 0.95 and ok_params / total >= 0.85, "問數 golden 未達標"
    # LLM 解析器用假 client 走一遍（結構化輸出 → 同一份結構）
    class _Resp:
        stop_reason = "end_turn"
        def __init__(self, text: str): self.content = [type("B", (), {"type": "text", "text": text})()]
    class _Fake:
        def __init__(self): self.beta = type("Beta", (), {})(); self.beta.messages = type("M", (), {})(); self.beta.messages.create = self._create; self.calls: list[dict[str, Any]] = []
        def _create(self, **kw: Any) -> _Resp:
            self.calls.append(kw)
            if "format" in kw.get("output_config", {}):
                return _Resp(json.dumps({"cards": [{"title": "成交金額（平台）", "kind": "events", "query": {"metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount", "alias": "m1"}], "group_by": ["platform"]}, "confidence": 0.9, "explanation": "x"}], "unmatched": []}))
            return _Resp("整體平穩。")
    fake = _Fake(); lp = LLMParser(cat, "k", client=fake)
    r = configure("各平台成交金額", cat, parser=lp, default_time={"from": "2026-10-01", "to": "2026-10-16"}, today=today, project="golden")
    assert r["parser"] == "llm" and r["cards"][0]["query"]["project"] == "golden" and r["cards"][0]["selected"] and fake.calls[0]["betas"] == ["server-side-fallback-2026-07-01"] and fake.calls[0]["fallbacks"] == "default"
    assert "語意層目錄" in fake.calls[0]["system"][0]["text"] and fake.calls[0]["system"][0]["cache_control"]["type"] == "ephemeral"
    assert lp.narrate("x", "最近 7 天", {"summary": ["a"]}) == "整體平穩。"
    print("llm parser (fake client) ok")


if __name__ == "__main__":
    selftest()
