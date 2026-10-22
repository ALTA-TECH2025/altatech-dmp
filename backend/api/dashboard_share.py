"""儀表板進階（docs/30 B／C／D）：規則式看板解讀、HTML 看板產物（含簽章分享連結與到期）、定時寄送（審批後由背景 tick 執行）。

- 解讀與渲染都用 execute_kind 以「當下身分」重算卡片（遮罩、行級、K 匿名照該身分）；渲染不輸出明細、SQL、血緣。
- 含 L3 分組（one_id／cust_code／account_no 等代碼化識別欄位）的卡片不渲染，改顯示說明。
- 產物存 render_dir/dashboards/<artifact_id>.html；到期後檔案刪除、紀錄保留（410）。分享 token = HMAC(key, artifact_id:expires)。
- 定時寄送：建立 → 審批單 type=dashboard_schedule（數位經營）→ active；tick() 每分鐘檢查台北時間是否到點（同一天只跑一次）；
  有 mailer（SMTP relay）就寄，沒有就只產檔並記錄 rendered_no_transport。收件人：角色 → 成員目錄的信箱；直接信箱需在白名單網域。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from ..render import svg
from ..security.policy import MASK, RBAC, Subject, authorize
from ..semantic.compiler import Catalog
from .analytics import Executor, execute_kind
from .store import Store


def resolve_card(store: Store, c: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    """卡片 → (kind, query, 警告)。引用書籤的卡片取書籤當下的查詢；書籤已刪除就用卡片快照並警告。"""
    bid = c.get("bookmark")
    if bid:
        b = next((x for x in store.items("bookmark") if x["id"] == bid), None)
        if b:
            return b["kind"], b["query"], None
        return c["kind"], c["query"], "書籤已刪除，顯示的是刪除前的查詢快照"
    return c["kind"], c["query"], None

TZ = ZoneInfo("Asia/Taipei")
RENDER_KIND, SCHED_KIND = "dashboard_render", "dashboard_schedule"


class AuthorizationLost(RuntimeError):
    """排程建立者現在已沒有執行所需的授權。"""
L3_FIELDS = {f["name"] for f in MASK.get("fields", []) if f.get("level") in ("L3", "L4")} | {"one_id", "cust_code", "account_no", "customer_code"}
AGG_CN = {"total": "總次數", "users": "觸發用戶數", "avg_per_user": "人均次數", "uniq": "去重數", "sum": "數值求和", "avg": "數值均值", "max": "最大值", "min": "最小值", "p50": "中位數", "p90": "P90", "mau": "MAU"}


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def group_fields(kind: str, q: dict[str, Any]) -> list[str]:
    out = [str(x) for x in (q.get("group_by") or [])]
    if kind == "attribution" and q.get("split"):
        out.append(str(q["split"]))
    return out


def has_l3_group(kind: str, q: dict[str, Any]) -> bool:
    return any(g.split(".")[-1] in L3_FIELDS for g in group_fields(kind, q))


def metric_labels(q: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    ms = q.get("metrics") or []
    aliases = [m.get("alias") or f"m{i + 1}" for i, m in enumerate(ms)]
    labels = {a: f"{m.get('event') or '任一事件'} {AGG_CN.get(m.get('agg'), m.get('agg'))}{'(' + m['field'] + ')' if m.get('field') else ''}" for a, m in zip(aliases, ms, strict=False)}
    return aliases, labels


def _series(rows: list[dict[str, Any]], aliases: list[str], labels: dict[str, str], groups: list[str]) -> list[dict[str, Any]]:
    gk = [f"g{i + 1}" for i in range(len(groups))]
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        gl = " / ".join(str(r.get(k) or "") for k in gk)
        for a in aliases:
            key = f"{a}|{gl}"
            s = out.setdefault(key, {"label": (labels.get(a, a) + (f" · {gl}" if gl else "")), "points": []})
            s["points"].append({"x": str(r.get("bucket") or gl or a), "y": _f(r.get(a)), "suppressed": bool(r.get("suppressed"))})
    return list(out.values())


def card_html(card: dict[str, Any], data: dict[str, Any], k: int, label_of: Callable[[str], str]) -> str:
    kind, q, chart = card["kind"], card["query"], card.get("chart", "auto")
    rows = data.get("rows") or []
    if kind == "events":
        aliases, labels = metric_labels(q)
        spec = str((data.get("chart_spec") or {}).get("type") or "line")
        bucketed = bool(rows) and any(all(r.get(a) is None or (isinstance(r.get(a), str) and _f(r.get(a)) is None) for r in rows) for a in aliases)
        ch = "table" if (chart == "auto" and bucketed) else (spec if chart == "auto" else chart)
        if ch == "table":
            cols = [c for c in (data.get("columns") or list(rows[0].keys() if rows else [])) if c not in ("_k", "suppressed")]
            return svg.table(cols, rows, {**labels, "bucket": "時間"}, k)
        if ch == "stat":
            r0 = rows[0] if rows else {}
            return svg.stat_tiles([(labels[a], r0.get(a), bool(r0.get("suppressed"))) for a in aliases], k)
        return svg.line_chart(_series(rows, aliases, labels, [str(x) for x in (q.get("group_by") or [])]), "bar" if ch == "bar" or spec == "bar" else "line")
    if kind == "funnel":
        first_key = (rows[0].get("bucket"), rows[0].get("g1")) if rows else None
        return svg.funnel([r for r in rows if (r.get("bucket"), r.get("g1")) == first_key], k)
    if kind == "retention":
        g1 = rows[0].get("g1") if rows else None
        return svg.retention_matrix([r for r in rows if r.get("g1") in (None, g1)], k, str(q.get("unit") or "day"))
    if kind == "path":
        return svg.path_top(data.get("paths") or [], int(data.get("anchor_users") or 0), label_of, k)
    return svg.attribution_share(rows, list(data.get("models") or []), data.get("summary"), k)


# ---------------- 規則式解讀 ----------------
def explain_card(card: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    kind, q, rows = card["kind"], card["query"], data.get("rows") or []
    sup = sum(1 for r in rows if r.get("suppressed"))
    warnings = [w for w in (data.get("warnings") or []) if not str(w).startswith("plan ")] + ([f"{sup} 列因 K 匿名抑制"] if sup else [])
    line, signal = "沒有資料", "none"
    if kind == "events" and rows:
        aliases, labels = metric_labels(q); a = aliases[0]; lab = labels[a]
        if "bucket" in rows[0]:
            by: dict[str, float] = {}
            for r in rows:
                v = _f(r.get(a))
                if v is not None: by[str(r["bucket"])] = by.get(str(r["bucket"]), 0) + v
            xs = sorted(by)
            if len(xs) >= 2:
                half = max(1, len(xs) // 2); a1 = sum(by[x] for x in xs[:half]) / half; a2 = sum(by[x] for x in xs[-half:]) / half
                chg = (a2 - a1) / a1 * 100 if a1 else None
                peak = max(xs, key=lambda x: by[x])
                signal = "flat" if chg is None or abs(chg) < 5 else ("up" if chg > 0 else "down")
                line = f"{lab}：後半段日均 {svg.fmt(a2)}，較前半段{'持平' if signal == 'flat' else ('上升' if signal == 'up' else '下降') + f' {abs(chg):.0f}%'}；高點 {peak}（{svg.fmt(by[peak])}）"
                if q.get("group_by"):
                    gt: dict[str, float] = {}
                    for r in rows:
                        v = _f(r.get(a))
                        if v is not None: gt[str(r.get("g1") or "（未填）")] = gt.get(str(r.get("g1") or "（未填）"), 0) + v
                    if gt:
                        g_top = max(gt, key=lambda g: gt[g]); g_tot = sum(gt.values())
                        line += f"；分組以 {g_top} 最高（占 {gt[g_top] / g_tot * 100:.0f}%）" if g_tot else ""
            else:
                line = f"{lab}：{svg.fmt(by.get(xs[0]) if xs else None)}"
        elif q.get("group_by"):
            vals = [(str(r.get("g1") or "（未填）"), _f(r.get(a))) for r in rows if _f(r.get(a)) is not None]
            if vals:
                top = max(vals, key=lambda t: t[1]); tot = sum(v for _, v in vals)
                line = f"{lab}：{top[0]} 最高（{svg.fmt(top[1])}，占 {top[1] / tot * 100:.0f}%）" if tot else f"{lab}：{top[0]} 最高"
        else:
            line = "；".join(f"{labels[x]} {svg.fmt(rows[0].get(x))}" for x in aliases)
    elif kind == "funnel" and rows:
        fk = (rows[0].get("bucket"), rows[0].get("g1")); steps = [r for r in rows if (r.get("bucket"), r.get("g1")) == fk]
        last = steps[-1]; rf = _f(last.get("rate_first"))
        drops = [(r.get("alias") or r.get("event"), _f(r.get("rate_prev"))) for r in steps[1:] if _f(r.get("rate_prev")) is not None]
        worst = min(drops, key=lambda t: t[1]) if drops else None
        line = f"整體轉換 {rf * 100:.1f}%" if rf is not None else "整體轉換 —"
        if worst: line += f"；流失最多在「{worst[0]}」（上一步只有 {worst[1] * 100:.1f}% 進入）"; signal = "down" if worst[1] < 0.3 else "flat"
    elif kind == "retention" and rows:
        p1 = [r for r in rows if int(r.get("period") or 0) == 1 and _f(r.get("rate")) is not None and r.get("complete", True)]
        if p1:
            latest = max(p1, key=lambda r: str(r["cohort"])); avg = sum(_f(r["rate"]) for r in p1) / len(p1)   # type: ignore[misc]
            rate = _f(latest["rate"]) or 0
            signal = "flat" if abs(rate - avg) < 0.03 else ("up" if rate > avg else "down")
            line = f"最新同期群 {latest['cohort']} 第 1 期留存 {rate * 100:.1f}%，{len(p1)} 個同期群平均 {avg * 100:.1f}%"
        else:
            line = "尚無完整期的同期群"
    elif kind == "path":
        paths = [p for p in (data.get("paths") or []) if not p.get("suppressed")]
        if paths:
            def real(p: dict[str, Any]) -> bool:          # 有實際後續步驟（不是起點後直接離開）
                steps = [x for x in (p.get("path") or [])[1:] if x not in ("離開", "其他")]
                return len(steps) >= 1
            nxt = next((p for p in paths if real(p)), None); leave = next((p for p in paths if not real(p)), None)
            parts = []
            if nxt: parts.append(f"最常見後續路徑 {' → '.join(nxt.get('path', []))}（{svg.fmt(nxt.get('users'))} 人，占起點 {float(nxt.get('share') or 0) * 100:.1f}%）")
            if leave: parts.append(f"{float(leave.get('share') or 0) * 100:.0f}% 的起點用戶有過直接離開的會話")
            line = "；".join(parts) + f"；起點 {svg.fmt(data.get('anchor_users'))} 人"
    elif kind == "attribution" and rows:
        models = list(data.get("models") or []); m = models[0] if models else None
        if m:
            best = max(((r.get("touch"), _f((r.get("models") or {}).get(m, {}).get("share"))) for r in rows if not r.get("suppressed")), key=lambda t: t[1] or 0, default=None)
            if best and best[1] is not None: line = f"{m} 模型下「{best[0]}」貢獻最高（{best[1] * 100:.1f}%）；轉化 {svg.fmt((data.get('summary') or {}).get('conversions'))} 次"
    return {"card": card["id"], "title": card["title"], "kind": kind, "line": line, "signal": signal, "warnings": warnings}


KIND_CN = {"events": "事件", "funnel": "漏斗", "retention": "留存", "path": "路徑", "attribution": "歸因", "sql": "SQL"}


def explain_summary(period: str, cards: list[dict[str, Any]]) -> list[str]:
    """三句話：趨勢（只算有前後半段可比的卡）、重點（下降／上升卡優先，否則漏斗／留存／歸因那句）、注意事項。"""
    trend = [c for c in cards if c["signal"] in ("up", "down", "flat") and c["kind"] == "events"]
    ups = [c for c in trend if c["signal"] == "up"]; downs = [c for c in trend if c["signal"] == "down"]
    others = [c for c in cards if c not in trend]
    kinds = sorted({KIND_CN.get(c["kind"], c["kind"]) for c in others})
    if trend:
        s1 = f"{period}：{len(cards)} 張卡片；{len(trend)} 張趨勢卡中 {len(ups)} 張上升、{len(downs)} 張下降、{len(trend) - len(ups) - len(downs)} 張持平（前後半段日均差 < 5%）" + (f"；另 {len(others)} 張為{'／'.join(kinds)}。" if others else "。")
    else:
        s1 = f"{period}：{len(cards)} 張卡片（{'／'.join(kinds) or '—'}），沒有可比前後半段的趨勢卡。"
    picks = downs[:1] + ups[:1]
    if picks:
        s2 = "；".join((("值得注意：" if c["signal"] == "down" else "表現最好：") + f"{c['title']}（{c['line']}）") for c in picks) + "。"
    else:
        key = [c for c in cards if c["kind"] in ("funnel", "retention", "attribution", "path") and c["signal"] != "none" or (c["kind"] in ("funnel", "retention", "attribution", "path") and c["line"] not in ("沒有資料", ""))]
        s2 = ("重點：" + "；".join(f"{c['title']}：{c['line']}" for c in key[:2]) + "。") if key else "趨勢卡皆持平，沒有明顯變化。"
    nw = sum(len(c["warnings"]) for c in cards)
    s3 = f"{nw} 則注意事項（抑制列或資料不完整），解讀時請留意樣本量。" if nw else "沒有抑制列或資料不完整的提醒。"
    return [s1, s2, s3]


class DashboardShare:
    def __init__(self, store: Store, ch: Executor | None, cat: Catalog, render_dir: Path, approvals: Any | None = None, mailer: Callable[[list[str], str, str], None] | None = None,
                 recipients: Callable[[list[str]], list[str]] | None = None, mail_domains: list[str] | None = None, render_key: str | None = None, base_url: str = "", llm: Any | None = None,
                 roles_of: Callable[[str], list[str] | None] | None = None):
        self.store, self.ch, self.cat, self.approvals, self.mailer, self.recipients, self.llm = store, ch, cat, approvals, mailer, recipients or (lambda roles: []), llm
        self.roles_of = roles_of                 # 排程執行時查建立者「現在」的角色（含指派撤銷／到期／停用）；None＝沿用建立時快照（單元測試）
        self.dir = Path(render_dir) / "dashboards"; self.dir.mkdir(parents=True, exist_ok=True)
        self.mail_domains = [d.lower().lstrip("@") for d in (mail_domains or []) if d]
        self.base_url = base_url.rstrip("/")
        self.key = render_key or self._persisted_key()
        if approvals is not None:
            approvals.hooks[SCHED_KIND] = self._on_schedule_decided

    def _persisted_key(self) -> str:
        cur = next((x for x in self.store.items("config") if x["id"] == "render_key"), None)
        if cur: return str(cur["key"])
        k = secrets.token_hex(32); self.store.put_item("config", {"id": "render_key", "key": k}); return k

    # ---- 共用 ----
    def label_of(self, name: str) -> str:
        return str((self.cat.events.get(name) or {}).get("cn") or name)

    def run_cards(self, d: dict[str, Any], subject: Subject, override: dict[str, Any] | None, apply_time: Callable[..., dict[str, Any]], module: str) -> list[tuple[dict[str, Any], dict[str, Any] | None, str | None]]:
        """每張卡 → (card, data|None, error)。"""
        out = []
        for c0 in d.get("cards") or []:
            kind, query, _warn = resolve_card(self.store, c0); c = {**c0, "kind": kind, "query": query}
            ov = override or ({"preset": d["time"]["preset"]} if (d.get("time") or {}).get("preset") and not (query.get("time") or {}).get("pinned") else None)
            try:
                q = apply_time(query, ov)
                data = execute_kind(kind, self.store, self.ch, self.cat, subject, q, module=module, extra={"dashboard": d["id"], "card": c["id"]})
                data["time"] = q.get("time"); out.append((c, data, None))
            except HTTPException as e:
                out.append((c, None, str(e.detail)))
        return out

    def explain(self, d: dict[str, Any], subject: Subject, override: dict[str, Any] | None, apply_time: Callable[..., dict[str, Any]], mode: str = "rules") -> dict[str, Any]:
        cards = []
        for c, data, err in self.run_cards(d, subject, override, apply_time, "dashboard"):
            cards.append(explain_card(c, data) if data else {"card": c["id"], "title": c["title"], "kind": c["kind"], "line": f"無法計算：{err}", "signal": "none", "warnings": []})
        period = self._period_text(d, override)
        res: dict[str, Any] = {"method": "rules", "summary": explain_summary(period, cards), "cards": cards, "generated_at": _now_iso(), "period": period, "narrative": None}
        if mode == "llm":
            if self.llm is None:
                res["note"] = "此環境未設定 LLM 金鑰，回規則式解讀"
            else:
                try:
                    res["narrative"] = self.llm.narrate(d["name"], period, {"summary": res["summary"], "cards": [{k: v for k, v in c.items() if k != "card"} for c in cards]}); res["method"] = "llm"
                except Exception as e:
                    res["note"] = f"模型解讀失敗，回規則式：{str(e)[:120]}"
        self.store.audit("dashboard.explained", {"user": subject.user_id, "dashboard": d["id"], "cards": len(cards), "method": res["method"]})
        return res

    @staticmethod
    def _period_text(d: dict[str, Any], override: dict[str, Any] | None) -> str:
        if override and override.get("from") and override.get("to"): return f"{override['from']} ～ {override['to']}"
        preset = {"7d": "最近 7 天", "14d": "最近 14 天", "30d": "最近 30 天", "90d": "最近 90 天"}.get(str((override or d.get("time") or {}).get("preset") or ""), "")
        pins = [t for t in ((c.get("query") or {}).get("time") or {} for c in d.get("cards") or []) if t.get("pinned") and t.get("from") and t.get("to")]
        if pins and len(pins) == len(d.get("cards") or []):
            return f"{min(t['from'] for t in pins)} ～ {max(t['to'] for t in pins)}（卡片釘住區間）"
        if pins:
            return f"{preset}（{len(pins)} 張卡片釘住 {min(t['from'] for t in pins)} ～ {max(t['to'] for t in pins)}）"
        return preset

    # ---- HTML 看板 ----
    def token(self, aid: str, expires_at: str) -> str:
        return hmac.new(self.key.encode(), f"{aid}:{expires_at}".encode(), hashlib.sha256).hexdigest()[:32]

    def view(self, a: dict[str, Any]) -> dict[str, Any]:
        url = f"/v1/dashboards/{a['dashboard']}/renders/{a['id']}"
        return {**{k: v for k, v in a.items() if k != "path"}, "artifact_id": a["id"], "url": url, "share_url": f"{self.base_url}{url}?token={self.token(a['id'], a['expires_at'])}", "expired": self.expired(a)}

    @staticmethod
    def expired(a: dict[str, Any]) -> bool:
        return datetime.fromisoformat(a["expires_at"]) <= datetime.now(TZ)

    def render(self, d: dict[str, Any], subject: Subject, body: dict[str, Any], apply_time: Callable[..., dict[str, Any]], k: int) -> dict[str, Any]:
        days = int(body.get("expires_days") or 14)
        if not 1 <= days <= 30:
            raise HTTPException(400, "expires_days 1–30")
        override = body.get("time")
        aid = "art_" + secrets.token_hex(6); created = datetime.now(TZ); expires = created + timedelta(days=days)
        cards_html, skipped = [], []
        results = self.run_cards(d, subject, override, apply_time, "dashboard_render")
        for c, data, err in results:
            if has_l3_group(c["kind"], c["query"]):
                skipped.append({"card": c["id"], "title": c["title"], "why": "此卡片含代碼化識別欄位分組，不納入分享看板"}); continue
            if data is None:
                skipped.append({"card": c["id"], "title": c["title"], "why": f"無法計算：{err}"}); continue
            t = data.get("time") or {}
            cards_html.append({"title": c["title"], "width": c.get("width", 1), "body_html": card_html(c, data, k, self.label_of), "note": c.get("note", ""),
                               "time_text": f"{t.get('from', '')} ～ {t.get('to', '')}" + ("（釘住）" if t.get("pinned") else "")})
        explain = None
        if body.get("include_explain", True):
            ex_cards = [explain_card(c, data) for c, data, _ in results if data and not has_l3_group(c["kind"], c["query"])]
            explain = {"summary": explain_summary(self._period_text(d, override), ex_cards)}
        audience = str(body.get("audience") or "")[:80]
        wm = f"{subject.user_id} · {created.strftime('%Y-%m-%d %H:%M')} · 到期 {expires.strftime('%Y-%m-%d')} · 內部使用"
        doc = svg.html_document(d["name"], {"period": self._period_text(d, override), "created_at": created.strftime("%Y-%m-%d %H:%M"), "expires_at": expires.strftime("%Y-%m-%d"), "audience": audience}, cards_html, explain, skipped, wm)
        if len(doc.encode()) > 3 * 1024 * 1024:
            raise HTTPException(413, "看板 HTML 超過 3 MB，請減少卡片或縮短區間")
        path = self.dir / f"{aid}.html"; path.write_text(doc, encoding="utf-8")
        a = {"id": aid, "dashboard": d["id"], "name": d["name"], "created_by": subject.user_id, "created_at": created.isoformat(timespec="seconds"), "expires_at": expires.isoformat(timespec="seconds"), "audience": audience,
             "bytes": len(doc.encode()), "cards_included": len(cards_html), "cards_skipped": skipped, "downloads": 0, "path": str(path)}
        self.store.put_item(RENDER_KIND, a)
        self.store.audit("dashboard.rendered", {"user": subject.user_id, "dashboard": d["id"], "artifact": aid, "audience": audience, "expires_at": a["expires_at"], "cards": len(cards_html), "skipped": len(skipped)})
        return self.view(a)

    def renders(self, dashboard_id: str) -> list[dict[str, Any]]:
        return sorted([self.view(a) for a in self.store.items(RENDER_KIND) if a["dashboard"] == dashboard_id], key=lambda a: a["created_at"], reverse=True)

    def get_render(self, dashboard_id: str, aid: str) -> dict[str, Any]:
        a = next((x for x in self.store.items(RENDER_KIND) if x["id"] == aid and x["dashboard"] == dashboard_id), None)
        if not a:
            raise HTTPException(404, "產物不存在")
        return a

    def download(self, a: dict[str, Any], who: str, via: str) -> str:
        if self.expired(a):
            Path(a["path"]).unlink(missing_ok=True)
            raise HTTPException(410, "此看板已到期")
        try:
            doc = Path(a["path"]).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise HTTPException(410, "產物檔已清除") from None
        a["downloads"] = int(a.get("downloads") or 0) + 1; self.store.put_item(RENDER_KIND, a)
        self.store.audit("dashboard.render.downloaded", {"user": who, "dashboard": a["dashboard"], "artifact": a["id"], "via": via})
        return doc

    def purge_expired(self) -> int:
        n = 0
        for a in self.store.items(RENDER_KIND):
            if self.expired(a) and Path(a["path"]).exists():
                Path(a["path"]).unlink(missing_ok=True); n += 1
        return n

    # ---- 定時寄送 ----
    def validate_schedule(self, body: dict[str, Any]) -> dict[str, Any]:
        wd = body.get("weekday"); hour = int(body.get("hour") if body.get("hour") is not None else -1)
        if wd is not None and not 0 <= int(wd) <= 6:
            raise HTTPException(400, "weekday 0–6 或 null")
        if not 0 <= hour <= 23:
            raise HTTPException(400, "hour 0–23")
        aud = body.get("audience") or {}
        roles = [str(r) for r in (aud.get("roles") or []) if str(r) in RBAC["roles"]]
        emails = [str(e).strip().lower() for e in (aud.get("emails") or []) if str(e).strip()]
        bad = [e for e in emails if "@" not in e or e.split("@", 1)[1] not in self.mail_domains]
        if bad:
            raise HTTPException(400, f"信箱 {bad} 不在白名單網域 {self.mail_domains or '（未設定 DMP_MAIL_DOMAINS）'}；請改用角色")
        if not roles and not emails:
            raise HTTPException(400, "audience 需至少一個角色或信箱")
        days = int(body.get("expires_days") or 14)
        if not 1 <= days <= 30:
            raise HTTPException(400, "expires_days 1–30")
        return {"weekday": None if wd is None else int(wd), "hour": hour, "audience": {"roles": roles, "emails": emails}, "expires_days": days, "include_explain": bool(body.get("include_explain", True))}

    def create_schedule(self, d: dict[str, Any], subject: Subject, body: dict[str, Any]) -> dict[str, Any]:
        spec = self.validate_schedule(body)
        s = {"id": "ds_" + secrets.token_hex(5), "dashboard": d["id"], **spec, "status": "pending_approval", "approval": None, "created_by": subject.user_id, "creator_roles": list(subject.roles), "created_at": _now_iso(), "last_run": None}
        if self.approvals is None:
            s["status"] = "active"
        self.store.put_item(SCHED_KIND, s)
        if self.approvals is not None:
            when = ("每天" if s["weekday"] is None else "每週" + "一二三四五六日"[s["weekday"]]) + f" {s['hour']:02d}:00"
            a = self.approvals.create(SCHED_KIND, subject, f"定時寄送看板「{d['name']}」{when}", {"dashboard": d["id"], "when": when, "audience": s["audience"], "expires_days": s["expires_days"]}, {"kind": SCHED_KIND, "id": s["id"]})
            s["approval"] = a["id"]; self.store.put_item(SCHED_KIND, s)
        self.store.audit("dashboard.schedule.created", {"user": subject.user_id, "dashboard": d["id"], "schedule": s["id"], "approval": s["approval"], "audience": s["audience"]})
        return s

    def _on_schedule_decided(self, a: dict[str, Any], approve: bool, by: Subject) -> None:
        s = next((x for x in self.store.items(SCHED_KIND) if x["id"] == a["object"]["id"]), None)
        if not s or s["status"] != "pending_approval":
            return
        s["status"] = "active" if approve else "rejected"; s["decided_by"] = by.user_id
        self.store.put_item(SCHED_KIND, s)
        self.store.audit("dashboard.schedule.approved" if approve else "dashboard.schedule.rejected", {"user": by.user_id, "schedule": s["id"], "dashboard": s["dashboard"]})

    def schedules(self, dashboard_id: str) -> list[dict[str, Any]]:
        return sorted([s for s in self.store.items(SCHED_KIND) if s["dashboard"] == dashboard_id], key=lambda s: s["created_at"], reverse=True)

    def delete_schedule(self, dashboard_id: str, sid: str, who: str) -> None:
        if not any(s["id"] == sid and s["dashboard"] == dashboard_id for s in self.store.items(SCHED_KIND)):
            raise HTTPException(404, "定時寄送不存在")
        self.store.delete_item(SCHED_KIND, sid)
        self.store.audit("dashboard.schedule.deleted", {"user": who, "schedule": sid, "dashboard": dashboard_id})

    def run_schedule(self, s: dict[str, Any], apply_time: Callable[..., dict[str, Any]], k: int, trigger: str = "tick", now: datetime | None = None,
                     fence_key: str | None = None) -> dict[str, Any]:
        d = next((x for x in self.store.items("dashboard") if x["id"] == s["dashboard"]), None)
        run: dict[str, Any] = {"at": (now or datetime.now(TZ)).isoformat(timespec="seconds"), "trigger": trigger, "artifact_id": None, "status": "failed", "recipients": 0, "error": None}
        try:
            if not d:
                raise RuntimeError("儀表板已刪除")
            subject = self._schedule_subject(s)
            art = self.render(d, subject, {"audience": "定時寄送", "expires_days": s["expires_days"], "include_explain": s["include_explain"]}, apply_time, k)
            run["artifact_id"] = art["artifact_id"]
            to = sorted(set(self.recipients(s["audience"].get("roles") or [])) | set(s["audience"].get("emails") or []))
            run["recipients"] = len(to)
            fence = getattr(self, "fence", None) if fence_key else None
            if self.mailer is None or not to:
                run["status"] = "rendered_no_transport"
            elif fence is not None and not fence.begin(fence_key):          # 寄信前一刻：已不是主控 → 不寄，也不寫 last_run，留給接手的 worker
                run["status"] = "fenced"
                self.store.audit("dashboard.schedule.fenced", {"user": s["created_by"], "schedule": s["id"], "dashboard": s["dashboard"], "artifact_id": run["artifact_id"]})
                return run
            else:
                self.mailer(to, f"[DMP] {d['name']} {datetime.now(TZ).strftime('%Y-%m-%d')}", f'<p>看板連結（到期 {art["expires_at"][:10]}）：<a href="{art["share_url"]}">{art["share_url"]}</a></p>')
                run["status"] = "sent"
        except AuthorizationLost as e:                                  # 建立者授權已失效：停送並留原因（R09）
            run["error"] = str(e)[:300]; s["status"] = "paused"; s["paused_reason"] = str(e)[:300]
            self.store.audit("dashboard.schedule.paused", {"user": s["created_by"], "schedule": s["id"], "dashboard": s["dashboard"], "reason": str(e)[:300]})
        except Exception as e:
            run["error"] = str(e)[:300]
        if fence_key and getattr(self, "fence", None) is not None:
            self.fence.finish(fence_key)
        s["last_run"] = run; self.store.put_item(SCHED_KIND, s)
        self.store.audit("dashboard.schedule.ran", {"user": s["created_by"], "schedule": s["id"], "dashboard": s["dashboard"], **{k2: v for k2, v in run.items() if k2 != "at"}})
        return run

    def _schedule_subject(self, s: dict[str, Any]) -> Subject:
        """排程的執行身分 = 建立者現在的有效角色（不是建立時的 creator_roles 快照）；查不到、沒角色或已無 overview.use → AuthorizationLost。"""
        if self.roles_of is None:
            return Subject(s["created_by"], list(s.get("creator_roles") or []))
        roles = self.roles_of(s["created_by"])
        if roles is None:
            raise AuthorizationLost("建立者帳號已停用或不存在，定時寄送已暫停")
        d = authorize(Subject(s["created_by"], roles), "use", "overview")
        if not roles or not d.allowed:
            raise AuthorizationLost(f"建立者授權已失效（目前角色：{', '.join(roles) or '無'}），定時寄送已暫停")
        return Subject(s["created_by"], roles)

    def tick(self, apply_time: Callable[..., dict[str, Any]], k: int, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(TZ); ran = []
        for s in self.store.items(SCHED_KIND):
            if s["status"] != "active" or now.hour != int(s["hour"]) or (s["weekday"] is not None and now.weekday() != int(s["weekday"])):
                continue
            lr = s.get("last_run") or {}
            if lr.get("at", "")[:10] == now.strftime("%Y-%m-%d") and lr.get("trigger") == "tick":
                continue
            fence = getattr(self, "fence", None)                         # 多份 worker（ops/lease.py）：以「排程×日期」認領；真正寄信前 run_schedule 再 begin
            key = f"dashboard_share:{s['id']}:{now:%Y-%m-%d}"
            if fence is not None and not fence.claim(key):
                continue
            ran.append(self.run_schedule(s, apply_time, k, "tick", now, fence_key=key if fence is not None else None))
        if now.minute == 0:
            self.purge_expired()
        return ran


def smtp_mailer(url: str, password: str | None, sender: str) -> Callable[[list[str], str, str], None]:
    """DMP_SMTP_URL = smtp://user@host:587（STARTTLS）或 smtps://user@host:465；密碼由 secret_ref 解析。"""
    import smtplib
    from email.message import EmailMessage
    from urllib.parse import urlparse

    u = urlparse(url)

    def send(to: list[str], subject: str, body_html: str) -> None:
        msg = EmailMessage(); msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
        msg.set_content("此信需要 HTML 顯示"); msg.add_alternative(body_html, subtype="html")
        cls = smtplib.SMTP_SSL if u.scheme == "smtps" else smtplib.SMTP
        with cls(u.hostname or "localhost", u.port or (465 if u.scheme == "smtps" else 587), timeout=30) as s:
            if u.scheme != "smtps":
                s.starttls()
            if u.username:
                s.login(u.username, password or "")
            s.send_message(msg)
    return send


