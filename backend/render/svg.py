"""卡片資料 → SVG／HTML 片段（與 web/src/analytics 的圖表同構：折線、長條、數字卡、漏斗、留存矩陣、路徑 Top、歸因份額）。
純函式、不連資料庫；抑制列顯示「< K」；輸出不含明細列、SQL、血緣。golden 資料集快照測試在 backend/render/selftest.py。"""
from __future__ import annotations

import html
import math
from typing import Any

PALETTE = ["#2C73EA", "#E0702B", "#2E9E6B", "#8B5CF6", "#C9A227", "#D64545", "#1BA3B5", "#7A7A7A"]
W, H, L, R, T, B = 760, 220, 56, 16, 16, 36


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def fmt(v: Any) -> str:
    if v is None: return "—"
    if isinstance(v, bool): return str(v)
    if isinstance(v, int | float):
        if math.isnan(v) or math.isinf(v): return "—"
        if abs(v) >= 1e8: return f"{v / 1e8:.2f} 億"
        if abs(v) >= 1e4: return f"{v / 1e4:.1f} 萬"
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return str(v)


def _nice(mx: float) -> float:
    if mx <= 0: return 1
    p = 10 ** math.floor(math.log10(mx)); f = mx / p
    return (1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10) * p


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def line_chart(series: list[dict[str, Any]], kind: str = "line") -> str:
    """series = [{label, points: [{x, y|None, suppressed}]}]；x 類別軸（折線依 x 排序）。"""
    series = [s for s in series if any(_num(p["y"]) is not None for p in s["points"])][:8]      # 全部抑制／空的序列不畫（例：分組為空的抑制列）
    xs = sorted({p["x"] for s in series for p in s["points"]}) if kind == "line" else list(dict.fromkeys(p["x"] for s in series for p in s["points"]))
    if not xs or not series:
        return '<p class="note">沒有資料（或全部因 K 匿名抑制）</p>'
    marked: set[str] = set()                                                                       # 每個 x 只標一次 <K
    mx = max((_num(p["y"]) or 0 for s in series for p in s["points"]), default=0)
    top = _nice(mx); pw = W - L - R; ph = H - T - B
    def X(i: int) -> float: return L + (pw * (i + 0.5) / len(xs) if kind == "bar" else (pw * i / max(1, len(xs) - 1)))
    def Y(v: float) -> float: return T + ph - ph * v / top
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="chart">']
    for g in range(5):
        y = T + ph * g / 4; out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" stroke="#E6E9EF"/><text x="{L - 6}" y="{y + 4:.1f}" font-size="10" fill="#6B7280" text-anchor="end">{esc(fmt(top * (4 - g) / 4))}</text>')
    step = max(1, math.ceil(len(xs) / 8))
    for i, x in enumerate(xs):
        if i % step == 0 or (i == len(xs) - 1 and i % step >= step / 2):
            out.append(f'<text x="{X(i):.1f}" y="{H - 14}" font-size="10" fill="#6B7280" text-anchor="middle">{esc(str(x)[-10:])}</text>')
    for si, s in enumerate(series):
        col = PALETTE[si % len(PALETTE)]; by = {p["x"]: p for p in s["points"]}
        if kind == "bar":
            bw = pw / len(xs) / (len(series) + 1)
            for i, x in enumerate(xs):
                p = by.get(x); v = _num(p["y"]) if p else None
                if v is None:
                    if p and p.get("suppressed") and x not in marked:
                        marked.add(x); out.append(f'<text x="{X(i):.1f}" y="{T + ph - 4}" font-size="9" fill="#9CA3AF" text-anchor="middle">&lt;K</text>')
                    continue
                x0 = X(i) + (si - len(series) / 2) * bw
                out.append(f'<rect x="{x0:.1f}" y="{Y(v):.1f}" width="{max(1, bw - 2):.1f}" height="{max(0, T + ph - Y(v)):.1f}" fill="{col}" rx="2"/>')
        else:
            pts = [(X(i), Y(_num(by[x]["y"]))) for i, x in enumerate(xs) if x in by and _num(by[x]["y"]) is not None]   # type: ignore[arg-type]
            if pts:
                out.append(f'<polyline fill="none" stroke="{col}" stroke-width="2" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>')
                for a, b in pts: out.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.5" fill="{col}"/>')
            for i, x in enumerate(xs):
                p = by.get(x)
                if p and p.get("suppressed") and _num(p["y"]) is None and x not in marked:
                    marked.add(x); out.append(f'<text x="{X(i):.1f}" y="{T + ph - 4}" font-size="9" fill="#9CA3AF" text-anchor="middle">&lt;K</text>')
    out.append("</svg>")
    leg = "".join(f'<span class="lg"><i style="background:{PALETTE[i % len(PALETTE)]}"></i>{esc(s["label"])}</span>' for i, s in enumerate(series))
    return "".join(out) + f'<div class="legend">{leg}</div>'


def stat_tiles(items: list[tuple[str, Any, bool]], k: int) -> str:
    return '<div class="tiles">' + "".join(f'<div class="tile"><span>{esc(lbl)}</span><b>{"&lt; " + str(k) if sup else esc(fmt(v))}</b></div>' for lbl, v, sup in items) + "</div>"


def table(columns: list[str], rows: list[dict[str, Any]], labels: dict[str, str] | None = None, k: int = 20, limit: int = 30) -> str:
    labels = labels or {}
    head = "".join(f"<th>{esc(labels.get(c, c))}</th>" for c in columns)
    body = []
    for r in rows[:limit]:
        sup = bool(r.get("suppressed"))
        body.append("<tr>" + "".join(f'<td class="{"num" if isinstance(r.get(c), int | float) else ""}">{"&lt; " + str(k) if sup and c not in ("bucket",) and not str(c).startswith("g") else esc(fmt(r.get(c)))}</td>' for c in columns) + "</tr>")
    more = f'<p class="note">只顯示前 {limit} 列</p>' if len(rows) > limit else ""
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>{more}'


def funnel(rows: list[dict[str, Any]], k: int) -> str:
    """rows：第一組（bucket, g1）的各步 users／rate_first／rate_prev；水平長條，寬度 = 相對第一步。"""
    if not rows: return '<p class="note">沒有資料</p>'
    first = _num(rows[0].get("users")) or 0
    out = ['<div class="funnel">']
    for r in rows:
        u = _num(r.get("users")); sup = bool(r.get("suppressed"))
        pct = (u / first * 100) if (u is not None and first) else 0
        rf = r.get("rate_first"); rp = r.get("rate_prev")
        out.append(f'<div class="fr"><span class="fl">{esc(r.get("alias") or r.get("event"))}</span><div class="fb"><i style="width:{max(2, pct):.1f}%"></i></div>'
                   f'<span class="fv">{"&lt; " + str(k) if sup else esc(fmt(u))}</span><span class="fp">{"" if rf is None else f"{float(rf) * 100:.1f}%"}{"" if rp is None or r.get("step") in (1, None) else f"（上一步 {float(rp) * 100:.1f}%）"}</span></div>')
    out.append("</div>")
    return "".join(out)


def retention_matrix(rows: list[dict[str, Any]], k: int, unit: str = "day") -> str:
    """rows：cohort × period（users／retained／rate／suppressed／complete）。"""
    if not rows: return '<p class="note">沒有資料</p>'
    periods = sorted({int(r["period"]) for r in rows})
    cohorts: dict[str, dict[int, dict[str, Any]]] = {}
    for r in rows:
        cohorts.setdefault(str(r["cohort"]), {})[int(r["period"])] = r
    u_cn = {"day": "日", "week": "週", "month": "月"}.get(unit, "")
    head = "<th>同期群</th><th>人數</th>" + "".join(f"<th>第 {p} {u_cn}</th>" for p in periods)
    body = []
    for c, by in sorted(cohorts.items()):
        base = next(iter(by.values()), {})
        cells = []
        for p in periods:
            r = by.get(p)
            if r is None: cells.append("<td></td>"); continue
            rate = _num(r.get("rate"))
            if r.get("suppressed") or rate is None:
                cells.append(f'<td class="num muted">{"&lt; " + str(k) if r.get("suppressed") else "—"}</td>')
            else:
                a = min(0.85, 0.15 + rate)
                cells.append(f'<td class="num" style="background:rgba(44,115,234,{a:.2f});color:{"#fff" if a > 0.5 else "#111"}">{rate * 100:.1f}%{"" if r.get("complete", True) else "*"}</td>')
        body.append(f'<tr><td>{esc(c)}</td><td class="num">{"&lt; " + str(k) if base.get("suppressed") else esc(fmt(base.get("users")))}</td>{"".join(cells)}</tr>')
    return f'<table class="ret"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table><p class="note">* 期間未完整</p>'


def path_top(paths: list[dict[str, Any]], anchor_users: int, label_of: Any, k: int, limit: int = 10) -> str:
    if not paths: return '<p class="note">沒有資料</p>'
    rows = []
    for p in paths[:limit]:
        steps = " → ".join(esc(label_of(x)) for x in p.get("path", []))
        sup = bool(p.get("suppressed")); u = p.get("users"); sh = p.get("share")
        rows.append(f'<tr><td>{steps}</td><td class="num">{"&lt; " + str(k) if sup else esc(fmt(u))}</td><td class="num">{"" if sup or sh is None else f"{float(sh) * 100:.1f}%"}</td></tr>')
    return f'<p class="note">起點用戶 {esc(fmt(anchor_users))}；Top {min(limit, len(paths))} 路徑</p><table><thead><tr><th>路徑</th><th>用戶</th><th>占比</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'


def attribution_share(rows: list[dict[str, Any]], models: list[str], summary: dict[str, Any] | None, k: int) -> str:
    tiles = stat_tiles([("轉化次數", (summary or {}).get("conversions"), False), ("有觸點的轉化", (summary or {}).get("touched"), False), ("轉化價值", (summary or {}).get("value"), False)], k) if summary else ""
    series = [{"label": m, "points": [{"x": r["touch"], "y": (r.get("models") or {}).get(m, {}).get("share"), "suppressed": bool(r.get("suppressed"))} for r in rows]} for m in models]
    for s in series:
        for p in s["points"]:
            if p["y"] is not None: p["y"] = round(float(p["y"]) * 100, 2)
    return tiles + line_chart(series, "bar") + '<p class="note">縱軸：各模型轉化份額（%）</p>'


CSS = """body{margin:0;background:#F4F6FA;font-family:-apple-system,'Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif;color:#111827;font-size:13px}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px}h1{font-size:20px;margin:0 0 4px}.sub{color:#6B7280;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:16px}.w2{grid-column:span 2}
.card{background:#fff;border:1px solid #E6E9EF;border-radius:10px;padding:14px 16px;min-width:0}.card h3{font-size:14px;margin:0 0 8px}
.note{color:#6B7280;font-size:12px;margin:6px 0 0}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:#374151;margin-top:4px}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;font-size:11px;color:#6B7280;border-bottom:1px solid #E6E9EF;padding:6px 8px}td{padding:6px 8px;border-bottom:1px solid #F0F2F5}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}td.muted{color:#9CA3AF}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:8px}.tile{background:#F7F8FB;border:1px solid #E6E9EF;border-radius:8px;padding:10px 12px}.tile span{display:block;font-size:11px;color:#6B7280}.tile b{font-size:22px;font-variant-numeric:tabular-nums}
.funnel .fr{display:grid;grid-template-columns:110px minmax(0,1fr) 70px 150px;gap:8px;align-items:center;margin:4px 0}.fb{background:#EEF1F6;border-radius:4px;height:14px}.fb i{display:block;height:14px;background:#2C73EA;border-radius:4px}.fv{text-align:right;font-variant-numeric:tabular-nums}.fp{color:#6B7280;font-size:11px}
.explain{background:#fff;border:1px solid #E6E9EF;border-left:4px solid #2C73EA;border-radius:10px;padding:12px 16px;margin-top:16px}.explain ul{margin:6px 0 0 18px;padding:0}
.wm{position:fixed;right:12px;bottom:10px;font-size:10px;color:#9CA3AF;background:rgba(255,255,255,.7);padding:3px 8px;border-radius:6px;pointer-events:none}
@media(max-width:800px){.grid{grid-template-columns:minmax(0,1fr)}.w2{grid-column:auto}}@media print{.wm{position:static}}"""


def html_document(title: str, meta: dict[str, Any], cards: list[dict[str, Any]], explain: dict[str, Any] | None, skipped: list[dict[str, Any]], watermark: str) -> str:
    """cards = [{title, width, body_html, time_text, note}]。無外部資源、無 script。"""
    parts = [f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">']
    parts.append(f'<h1>{esc(title)}</h1><div class="sub">{esc(meta.get("period", ""))} · 產生 {esc(meta.get("created_at", ""))} · 受眾 {esc(meta.get("audience") or "—")} · 到期 {esc(meta.get("expires_at", ""))} · 內部使用</div>')
    if explain:
        parts.append('<div class="explain"><b>本期重點</b><ul>' + "".join(f"<li>{esc(x)}</li>" for x in explain.get("summary", [])) + "</ul></div>")
    parts.append('<div class="grid">')
    for c in cards:
        parts.append(f'<div class="card{" w2" if c.get("width") == 2 else ""}"><h3>{esc(c["title"])}</h3><div class="sub">{esc(c.get("time_text", ""))}</div>{c["body_html"]}{f"<p class=note>{esc(c['note'])}</p>" if c.get("note") else ""}</div>')
    for s in skipped:
        parts.append(f'<div class="card"><h3>{esc(s["title"])}</h3><p class="note">{esc(s["why"])}</p></div>')
    parts.append("</div>")
    parts.append(f'<div class="wm">{esc(watermark)}</div></div></body></html>')
    return "".join(parts)
