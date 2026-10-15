// AltaTech DMP 原型 v0.4 — v0.4：事件分析／漏斗分析查詢條件建構器（對標神策／DataFinder）、H5 行動版適配
// 側欄 + 頂欄 + 內容的外框沿用 AltaTech 原型系列；頁面：總覽、事件分析、漏斗、留存、用戶細查、標籤分群、即時觸發、AI 洞察、治理、設定。
// 所有數字來自 data.js 的模擬資料；圖表為內嵌 SVG（dataviz 規範：細線 2px、單軸、固定類別色序、hover 提示）。
'use strict';

import { ACCOUNTS, SOURCES, EVENTS, TS, PLATFORM_SPLIT, CHANNEL_SPLIT, UTM_SPLIT, FUNNELS, RETENTION, TAGS, SEGMENTS,
         TRIGGERS, TRIGGER_FEED, PROFILES, INSIGHTS, AGENTS, TOOLS, ASK_SCRIPTS, TODAY, dkey, dayOffset,
         PATHS, ATTRIBUTION, USERS, CONNECTIONS, MODEL_EXAMPLE, CONFIG_CARDS,
         ROLES, MODULES, MATRIX, DATA_SCOPES, MASK_RULES, APPROVALS, AUDIT, DESTINATIONS, AUDIENCE_PUSHES, DELIVERIES,
         PROPS, PROP_SCOPES, OPS, AGGS, GRANS, CHART_TYPES, SAVED_REPORTS, CONNECTOR_AUTH, FIELD_CN, TEST_STEPS, TEST_FIX, ONEID_CASES,
         META_PROPS, USER_PROPS, ONEID_RULES, DIM_TABLES, DIM_DICTS, VIRTUAL_PROPS, EXT_TABLES, SYNC_TASKS, PIPELINE, QUERY_PERF, BATCH_JOBS, STORAGE, USER_ASSIGNMENTS, VIRTUAL_EVENTS } from './data.js?v=20261015.7';

const VERSION = 'v0.5.3';
const BUILD = '20261015.7';

/* ------------------------------------------------------------------ state */
const S = {
  user: null, route: { name: 'overview' }, collapsed: false,
  range: 30,                                   // 7 / 30 / 90 天
  ev: null, funnel: null, qcfgCollapsed: false, navOpen: false,   // v0.4：由 resetEvents()/resetFunnel() 初始化
  profile: { q: '', sel: null, reason: '', unlocked: {} },
  seg: { tab: 'segments' }, segDrawer: null,
  ai: { tab: 'insights', kind: 'all', status: 'open', open: {}, chat: [], busy: false, prefill: '' },
  gov: { tab: 'dictionary', q: '', status: 'all' }, meta: null, us: { tab: 'props' }, items: { tab: 'tables', type: 'dim_instrument' }, pipe: { tab: 'flow' },
  iam: { tab: 'roles' }, roleEdit: null, customRoles: {}, customMatrix: {}, push: null,
  path: null, attr: null, ret: null, conn: null, users: { q: '', lc: 'all', tier: 'all', page: 0, sel: {}, from: null }, integ: { tab: 'connections', step: 4 },
  sqlDrawer: null, toast: null, _evSql: '', _fnSql: '',
  approved: {},                                // 原型：審批狀態
  feedback: {},
};
const CHARTS = {};                             // chart id → { xs, series, fmt }

/* ------------------------------------------------------------------ helpers */
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const n0 = v => Math.round(v).toLocaleString('en-US');
const compact = v => { const a = Math.abs(v); if (a >= 1e12) return (v / 1e12).toFixed(2) + ' 兆'; if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + ' 億'; if (a >= 1e4) return (v / 1e4).toFixed(a >= 1e6 ? 0 : 1) + ' 萬'; return n0(v); };
const pct = (v, d = 1) => (v * 100).toFixed(d) + '%';
const pt = v => (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + ' pt';
const sum = a => a.reduce((x, y) => x + y, 0);
const last = (arr, n) => arr.slice(-n);
const go = h => { location.hash = h; };
const toast = msg => { S.toast = msg; render(); setTimeout(() => { S.toast = null; render(); }, 2400); };
const canSee = p => !S.user || S.user.scope === 'all' || S.user.scope === `branch:${p.branch}`;

const I = {
  menu:  '<path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
  home:  '<path d="M4 11l8-7 8 7v9a1 1 0 01-1 1h-5v-6h-4v6H5a1 1 0 01-1-1z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
  chart: '<path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6M20 16v-2" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round"/>',
  funnel:'<path d="M4 5h16l-6 7v6l-4 2v-8z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
  grid:  '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linejoin="round"/>',
  user:  '<circle cx="12" cy="8" r="3.6" stroke="currentColor" stroke-width="1.7" fill="none"/><path d="M5 20a7 7 0 0114 0" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round"/>',
  tag:   '<path d="M3 12V4h8l9 9-8 8z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/><circle cx="7.5" cy="8.5" r="1.3" fill="currentColor"/>',
  bolt:  '<path d="M13 2L4 14h7l-1 8 9-12h-7z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9z" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linejoin="round"/>',
  shield:'<path d="M12 3l7 2.5v5.6c0 4.4-3 7.6-7 9.4-4-1.8-7-5-7-9.4V5.5L12 3z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
  cog:   '<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.7" fill="none"/><path d="M19.4 13a7.9 7.9 0 000-2l2-1.5-2-3.4-2.4 1a7.6 7.6 0 00-1.7-1L15 3H9l-.3 2.6a7.6 7.6 0 00-1.7 1l-2.4-1-2 3.4L4.6 11a7.9 7.9 0 000 2l-2 1.5 2 3.4 2.4-1a7.6 7.6 0 001.7 1L9 21h6l.3-2.6a7.6 7.6 0 001.7-1l2.4 1 2-3.4z" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linejoin="round"/>',
  left:  '<path d="M11 6l-6 6 6 6M19 6l-6 6 6 6" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  right: '<path d="M13 6l6 6-6 6M5 6l6 6-6 6" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  back:  '<path d="M19 12H5M11 6l-6 6 6 6" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  bell:  '<path d="M6 9a6 6 0 1112 0c0 4 1.5 5.5 1.5 5.5h-15S6 13 6 9z" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linejoin="round"/><path d="M10 18a2 2 0 004 0" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/>',
  check: '<path d="M5 12l5 5 9-10" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  warn:  '<path d="M12 8v5M12 16.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6" fill="none"/>',
  search:'<circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="1.7" fill="none"/><path d="M16 16l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
  clock: '<path d="M12 7v5l3 2M3.5 12a8.5 8.5 0 1017 0 8.5 8.5 0 00-17 0z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  globe: '<circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M3.5 12h17M12 3.5c4 4.5 4 12.5 0 17-4-4.5-4-12.5 0-17z" stroke="currentColor" stroke-width="1.6" fill="none"/>',
  code:  '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  send:  '<path d="M3 11l18-8-8 18-2-8z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
  up:    '<path d="M12 19V5M6 11l6-6 6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  down:  '<path d="M12 5v14M6 13l6 6 6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
};
const svg = (p, size = 18, cls = '') => `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none">${p}</svg>`;
const LOGO = `<svg class="logo" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#1F4E8C"/><path d="M14 44 L26 18 L32 32 L38 18 L50 44" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/><circle cx="32" cy="44" r="4" fill="#5FD3C7"/></svg>`;
const SER = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)', 'var(--s7)', 'var(--s8)'];

/* ------------------------------------------------------------------ charts */
function ticks(max) { const raw = max / 4; const p = Math.pow(10, Math.floor(Math.log10(raw))); const n = [1, 2, 2.5, 5, 10].find(k => k * p >= raw) * p; const out = []; for (let v = 0; v < max + n; v += n) out.push(v); return out; }
function lineChart(id, series, { h = 220, fmt = compact, area = true } = {}) {
  const W = 760, H = h, padL = 54, padR = 16, padT = 14, padB = 28;
  const xs = series[0].data.map(p => p.d);
  const max = Math.max(...series.flatMap(s => s.data.map(p => p.v)));
  const tks = ticks(max); const top = tks[tks.length - 1];
  const x = i => padL + i * (W - padL - padR) / Math.max(1, xs.length - 1);
  const y = v => padT + (H - padT - padB) * (1 - v / top);
  CHARTS[id] = { xs, series, fmt, x: xs.map((_, i) => x(i)), W, padL, padR };
  const grid = tks.map(t => `<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"/><text class="tick" x="${padL - 8}" y="${(y(t) + 3.5).toFixed(1)}" text-anchor="end">${fmt(t)}</text>`).join('');
  const nX = xs.length; const step = Math.max(1, Math.round(nX / 6));
  const xt = xs.map((d, i) => (i % step === 0 || i === nX - 1) ? `<text class="tick" x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${d.slice(5)}</text>` : '').join('');
  const paths = series.map((s, si) => {
    const pts = s.data.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`);
    const d = 'M' + pts.join('L');
    const ar = area && si === 0 ? `<path class="area" fill="${s.color}" d="${d}L${x(nX - 1).toFixed(1)},${y(0).toFixed(1)}L${x(0).toFixed(1)},${y(0).toFixed(1)}Z"/>` : '';
    const lastP = s.data[nX - 1];
    return `${ar}<path class="line" stroke="${s.color}" d="${d}"/><circle class="dot" cx="${x(nX - 1).toFixed(1)}" cy="${y(lastP.v).toFixed(1)}" r="4" fill="${s.color}"/>`;
  }).join('');
  const endLbl = series.length <= 2 ? series.map(s => `<text class="lbl" x="${(W - padR + 4)}" y="${(y(s.data[nX - 1].v) + 4).toFixed(1)}">${fmt(s.data[nX - 1].v)}</text>`).join('') : '';
  const legend = series.length > 1 ? `<div class="legend">${series.map(s => `<span class="k"><i class="sw" style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  return `<div class="chart-wrap" data-chart="${id}"><svg class="chart" viewBox="0 0 ${W + 40} ${H}">${grid}<line class="axis" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>${xt}${paths}${endLbl}<line class="xline hidden" id="xl-${id}" y1="${padT}" y2="${y(0)}"/><rect class="hit" x="${padL}" y="0" width="${W - padL - padR}" height="${H}"/></svg>${legend}</div>`;
}
function spark(data, color = 'var(--s1)') {
  const W = 120, H = 30; const vs = data.map(p => p.v); const mn = Math.min(...vs), mx = Math.max(...vs);
  const x = i => i * W / (vs.length - 1), y = v => 2 + (H - 4) * (1 - (v - mn) / Math.max(1e-9, mx - mn));
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><path class="line" stroke="${color}" d="M${vs.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('L')}"/><circle class="dot" cx="${W}" cy="${y(vs[vs.length - 1]).toFixed(1)}" r="3.5" fill="${color}"/></svg>`;
}
function hbar(rows, { fmt = compact, color = 'var(--s1)', total = null } = {}) {
  const max = Math.max(...rows.map(r => r.v)); const tot = total ?? sum(rows.map(r => r.v));
  return `<table class="tbl">${rows.map(r => `<tr><td style="width:120px;font-weight:600">${esc(r.k)}</td><td><span class="bar-in" style="width:${(r.v / max * 100).toFixed(1)}%;background:${color}"></span></td><td class="num mono" style="width:90px">${fmt(r.v)}</td><td class="num" style="width:60px;color:var(--muted)">${pct(r.v / tot, 0)}</td></tr>`).join('')}</table>`;
}
function funnelView(f, { compare = true, sel = -1, selectable = false } = {}) {
  const top = f.steps[0].n;
  return `<div class="funnel">${f.steps.map((s, i) => {
    const conv = i ? s.n / f.steps[i - 1].n : 1; const convP = i ? s.prev / f.steps[i - 1].prev : 1;
    const drop = i ? `<div class="fdrop"><span></span><span>轉化 <b style="color:${conv - convP < -.03 ? 'var(--st-serious)' : 'var(--ink2)'}">${pct(conv)}</b>${compare ? ` <span style="color:var(--muted)">（上期 ${pct(convP)}，${pt(conv - convP)}）</span>` : ''} · 流失 ${n0(f.steps[i - 1].n - s.n)} 人</span></div>` : '';
    const selCls = selectable ? ` click ${sel === i || sel === i - 1 ? 'sel' : ''}` : '';
    return `${drop}<div class="fstep${selCls}" ${selectable ? `data-act="fnstep" data-arg="${Math.min(i, f.steps.length - 2)}"` : ''}><div class="nm">${selectable ? `${i + 1}. ` : ''}${esc(s.cn)}<small class="mono">${esc(s.ev)}</small></div><div class="trk">${compare ? `<div class="fill cmp" style="width:${(s.prev / top * 100).toFixed(1)}%"></div>` : ''}<div class="fill" style="width:${(s.n / top * 100).toFixed(1)}%;${compare ? 'bottom:50%' : ''}"></div></div><div class="v">${n0(s.n)}<small>${pct(s.n / top)} 總體</small></div></div>`;
  }).join('')}</div>${compare ? `<div class="legend"><span class="k"><i class="sw rect" style="background:var(--s1)"></i>本期</span><span class="k"><i class="sw rect" style="background:var(--s2)"></i>上期</span></div>` : ''}`;
}
function heatTable(rows) {
  const cls = v => v == null ? 'n' : v >= 45 ? 'c5' : v >= 38 ? 'c4' : v >= 32 ? 'c3' : v >= 27 ? 'c2' : v >= 20 ? 'c1' : 'c0';
  return `<table class="heat"><tr><th>Cohort（開戶核准週）</th><th>人數</th>${[0, 1, 2, 3, 4, 5, 6, 7].map(i => `<th>第 ${i} 週</th>`).join('')}</tr>${rows.map(r => `<tr><td class="nm">${esc(r.cohort)}</td><td class="n mono">${n0(r.n)}</td>${r.cells.map((v, i) => `<td class="${i === 0 ? 'c5' : cls(v)}" title="${esc(r.cohort)} 第 ${i} 週：${v == null ? '—' : v + '%'}">${v == null ? '' : i === 0 ? '100%' : v.toFixed(1) + '%'}</td>`).join('')}</tr>`).join('')}</table>`;
}
function idGraph(p) {
  const core = { x: 300, y: 90, k: 'one_id', v: p.one_id };
  const nodes = p.identities.map((it, i) => { const n = p.identities.length; const ang = (i / n) * Math.PI * 2 - Math.PI / 2; const r = 110 + (i % 2) * 40; return { x: 300 + Math.cos(ang) * r * 2.1, y: 90 + Math.sin(ang) * r * .62, k: it[0], v: it[1], s: it[2] }; });
  const edges = nodes.map(n => `<line class="edge ${n.s === 'weak' || n.s === 'channel' ? 'weak' : ''}" x1="${core.x}" y1="${core.y}" x2="${n.x.toFixed(0)}" y2="${n.y.toFixed(0)}"/>`).join('');
  const nd = n => `<g class="node ${n === core ? 'core' : ''}" transform="translate(${(n.x - 72).toFixed(0)},${(n.y - 17).toFixed(0)})"><rect width="144" height="34"/><text class="k" x="8" y="13">${esc(n.k)}</text><text x="8" y="27">${esc(n.v)}</text></g>`;
  return `<svg class="idg" viewBox="0 0 600 180">${edges}${nodes.map(nd).join('')}${nd(core)}</svg><div class="legend"><span class="k"><i class="sw" style="background:var(--axis)"></i>強 ID 共現（合併）</span><span class="k"><i class="sw" style="background:var(--axis);height:0;border-top:2px dashed var(--axis)"></i>弱 ID／觸達渠道（關聯不合併）</span></div>`;
}
function onChartMove(e) {
  const wrap = e.target.closest('.chart-wrap'); if (!wrap) return;
  const c = CHARTS[wrap.dataset.chart]; if (!c) return;
  const svgEl = wrap.querySelector('svg'); const r = svgEl.getBoundingClientRect();
  const vx = (e.clientX - r.left) / r.width * (c.W + 40);
  let i = 0; for (let k = 1; k < c.x.length; k++) if (Math.abs(c.x[k] - vx) < Math.abs(c.x[i] - vx)) i = k;
  const xl = svgEl.querySelector('.xline'); xl.classList.remove('hidden'); xl.setAttribute('x1', c.x[i]); xl.setAttribute('x2', c.x[i]);
  let tip = wrap.querySelector('.tip'); if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; wrap.appendChild(tip); }
  tip.textContent = ''; const t = document.createElement('div'); t.className = 't'; t.textContent = c.xs[i]; tip.appendChild(t);
  c.series.forEach(s => { const row = document.createElement('div'); row.className = 'r'; const k = document.createElement('i'); k.className = 'k'; k.style.background = s.color; const b = document.createElement('b'); b.textContent = c.fmt(s.data[i].v); const sp = document.createElement('span'); sp.textContent = s.name; row.append(k, b, sp); tip.appendChild(row); });
  const px = c.x[i] / (c.W + 40) * r.width; tip.style.left = `${px}px`; tip.style.top = `${Math.max(0, e.clientY - r.top - 14)}px`;
}
function onChartLeave(e) { const wrap = e.target.closest('.chart-wrap'); if (!wrap) return; wrap.querySelector('.xline')?.classList.add('hidden'); wrap.querySelector('.tip')?.remove(); }

/* ------------------------------------------------------------------ derived data */
const seriesRange = key => last(TS[key], S.range);
function splitByPlatform(key) { const base = seriesRange(key); const w = [.46, .36, .18]; return ['iOS', 'Android', 'Web'].map((k, i) => ({ name: k, color: SER[i], data: base.map(p => ({ d: p.d, v: Math.round(p.v * w[i] * (1 + Math.sin(p.d.length + i) * .02)) })) })); }
const METRICS = [
  ['dau', '日活躍客戶', compact], ['login_users', '登入人數', compact], ['quote_viewers', '看盤人數', compact], ['orders', '委託筆數', compact], ['traders', '成交客戶數', compact],
  ['turnover', '成交金額', compact], ['deposits', '入金金額', compact], ['new_accounts', '新開戶數', n0], ['reject_rate', '委託失敗率', v => pct(v, 2)],
];
const sumLast = (key, n) => sum(last(TS[key], n).map(p => p.v));
const delta = (key, n = 7) => { const a = sumLast(key, n), b = sum(TS[key].slice(-2 * n, -n).map(p => p.v)); return (a - b) / b; };

/* ------------------------------------------------------------------ v0.4 查詢狀態 */
function resetEvents() { S.ev = { metrics: [{ ev: 'bond_deal', agg: 'users', prop: 'amount', filters: { logic: 'and', items: [] }, open: false }], filters: { logic: 'and', items: [] }, groups: [], topN: 10, gran: 'day', chart: 'line', compare: false, tableAll: false, saved: S.ev ? S.ev.saved : [] }; }
function resetFunnel(id = 'open') { const t = FUNNELS.find(f => f.id === id); const w = t.window === '當日' ? { n: 1, unit: '天' } : t.window === '7 天' ? { n: 7, unit: '天' } : { n: 30, unit: '天' }; S.funnel = { id, name: t.name, steps: t.steps.map(s => ({ ev: s.ev, cn: s.cn, filters: { logic: 'and', items: [] }, open: false })), window: w, assoc: '', filters: { logic: 'and', items: [] }, group: 'none', compare: true, view: 'funnel', sel: 0, saved: S.funnel ? S.funnel.saved : [] }; }
resetEvents(); resetFunnel();

/* ------------------------------------------------------------------ routing */
function parseHash() { const h = (location.hash || '#/overview').replace(/^#\/?/, ''); const p = h.split('/').filter(Boolean); return { name: p[0] || 'overview', tab: p[1], arg: p[2] }; }

/* ------------------------------------------------------------------ shell */
const TITLES = { overview: '總覽', events: '事件分析', funnel: '漏斗分析', path: '行為路徑分析', attribution: '歸因分析', users: '用戶列表', integration: '數據整合', retention: '留存分析', audit: '日誌統計', iam: '權限中心', profile: '用戶細查 / OneID', segments: '標籤與分群', triggers: '即時觸發', ai: 'AI 洞察', governance: '元事件管理', userschema: '用戶表管理', items: '維度表與字典', pipeline: '資料流與效能', settings: '平台設定' };
/* ================================================================== v0.3：AltaTech 品牌、橫向導覽、模組權限、欄位隱碼 */
const LOGO_MARK = `<svg class="logo" viewBox="0 0 64 64" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M10 26V10h16M10 10l14 14" stroke="#21DCCE" stroke-width="6"/><path d="M38 10h16v16" stroke="#B57BEA" stroke-width="6"/><path d="M10 38v16h16" stroke="#FECF00" stroke-width="6"/><path d="M54 38v16H38M54 54L40 40" stroke="#2C73EA" stroke-width="6"/></svg>`;
const NAV_GROUPS = [
  { id: 'home', cn: '首頁', items: [['overview', '總覽']] },
  { id: 'analysis', cn: '分析', items: [['events', '事件分析'], ['funnel', '漏斗分析'], ['retention', '留存分析'], ['path', '行為路徑'], ['attribution', '歸因分析'], ['users', '用戶列表']] },
  { id: 'ops', cn: '營運', items: [['segments', '標籤與分群'], ['triggers', '即時觸發']] },
  { id: 'data', cn: '資料', items: [['integration', '數據整合'], ['profile', '用戶細查 / OneID']] },
  { id: 'ai', cn: 'AI 洞察', items: [['ai/insights', '洞察串流'], ['ai/ask', '問答'], ['ai/agents', 'Agents'], ['ai/tools', '工具目錄']] },
  { id: 'gov', cn: '治理', items: [['governance', '元事件管理'], ['userschema', '用戶表'], ['items', '維度表與字典'], ['pipeline', '資料流與效能'], ['audit', '日誌統計'], ['iam', '權限中心'], ['settings', '平台設定']] },
];
const ROUTE_MODULE = { overview: 'overview', events: 'events', funnel: 'events', path: 'events', attribution: 'events', retention: 'events', users: 'users', profile: 'profile', segments: 'segments', triggers: 'triggers', integration: 'integration', ai: 'ai_ask', governance: 'governance', userschema: 'governance', items: 'governance', pipeline: 'governance', audit: 'audit', iam: 'iam', settings: 'iam' };
const RK = () => (S.user && S.user.rk) || 'data_analyst';
const perm = (mod) => (MATRIX[RK()] || {})[mod] || '';
const has = (mod, act) => perm(mod).includes(act);
const canRoute = (name) => { const m = ROUTE_MODULE[name.split('/')[0]] || 'overview'; return perm(m).length > 0; };
const LOCK = '<svg class="lock" viewBox="0 0 24 24" fill="none"><rect x="5" y="11" width="14" height="10" rx="2" stroke="currentColor" stroke-width="2"/><path d="M8 11V7a4 4 0 018 0v4" stroke="currentColor" stroke-width="2"/></svg>';
const PLAIN_ID = ['admin'];
const idMasked = () => !PLAIN_ID.includes(RK());
function mId(v) { v = String(v); return idMasked() ? `<span class="masked" title="L3 代碼化識別：mask_tail(4)">${esc(v.slice(0, 4))}•••${esc(v.slice(-4))}${LOCK}</span>` : `<span class="mono">${esc(v)}</span>`; }
function mMoney(v) { if (['admin', 'data_analyst', 'digital_ops'].includes(RK())) return `<span class="mono">${n0(v)}</span>`; const b = v < 1e5 ? '< 10 萬' : v < 5e5 ? '10–50 萬' : v < 1e6 ? '50–100 萬' : '≥ 100 萬'; return `<span class="masked" title="L2 敏感業務：bucket">${b}${LOCK}</span>`; }
function mScore(v) { if (['admin', 'data_analyst', 'digital_ops'].includes(RK())) return `<span class="mono" style="color:${v > 60 ? 'var(--st-serious)' : 'inherit'}">${v}</span>`; return `<span class="masked">${v >= 70 ? '高' : v >= 40 ? '中' : '低'}${LOCK}</span>`; }

function activeGroup() { const n = S.route.name; return NAV_GROUPS.find(g => g.items.some(it => it[0].split('/')[0] === n)) || NAV_GROUPS[0]; }
function viewGate() {
  return `<div class="gate"><div class="gate-brand">${LOGO_MARK}<div><b>ALTA TECH<sup>LTD</sup></b><span>AltaTech DMP · 數位軌跡／客戶數據中台 · 原型 ${VERSION}</span></div></div>
  <div class="gate-card"><h1>選擇示範身分</h1><p>原型以五種角色展示企業級權限：模組權限（哪些功能可見／可用／可匯出／可審批）、數據權限（行級範圍）、欄位隱碼（依敏感等級）、審批與審計。資料皆為模擬。</p>
  ${ACCOUNTS.map(a => `<button class="gate-role" data-act="login" data-arg="${a.id}"><span class="av" style="background:${a.col}">${a.init}</span><span><span class="nm">${esc(a.name)}</span><span class="em">${esc(a.email)}</span></span><span class="rl">${esc(a.role)}</span></button>`).join('')}</div>
  <div class="gate-note">承接 GA360 埋點事件與證券交易事件；OneID 打通；事件／漏斗／路徑／歸因／用戶列表／標籤；數據整合可視化建模；AI agent 透過語意層與工具閘道產生洞察。</div></div>`;
}
function viewTopNav() {
  const u = S.user; const g = activeGroup(); const open = INSIGHTS.filter(i => i.status === 'open').length;
  const groups = NAV_GROUPS.filter(gr => gr.items.some(it => canRoute(it[0]))).map(gr => `<button class="${gr.id === g.id ? 'on' : ''}" data-act="goto" data-arg="#/${gr.items.find(it => canRoute(it[0]))[0]}">${gr.cn}${gr.id === 'ai' ? `<span class="badge">${open}</span>` : ''}</button>`).join('');
  return `<header class="topnav"><div class="tn-in"><button class="icon-btn hamb" data-act="navtoggle" aria-label="選單">${svg(I.menu, 20)}</button><a class="brand" href="#/overview">${LOGO_MARK}<b>ALTA TECH<sup>LTD</sup></b><span class="prod">DMP</span></a><nav class="tn-nav">${groups}</nav>
    <div class="tn-right"><div class="search">${svg(I.spark, 15)}<input placeholder="問 AI：上週開戶到入金的轉化率？" data-act="askbar" value="" autocomplete="off"><kbd>⏎</kbd></div>
      <button class="org-pick" data-act="noop">證券示範專案<span class="sep">/</span>prod</button>
      <button class="icon-btn" data-act="goto" data-arg="#/ai/insights" title="洞察通知">${svg(I.bell, 18)}<span class="dot"></span></button>
      <button class="icon-btn" data-act="noop" title="語言">${svg(I.globe, 18)}</button>
      <button class="me" data-act="logout" title="切換身分"><span class="av" style="background:${u.col}">${u.init}</span><span><span class="nm">${esc(u.name)}</span><span class="rl">${esc(u.role)}</span></span></button></div></div></header>`;
}
function viewMobileNav() {
  if (!S.navOpen) return ''; const cur = S.route.name === 'ai' ? `ai/${S.ai.tab}` : S.route.name;
  return `<div class="mnav"><div class="drawer-bg" data-act="navclose"></div><nav class="mnav-panel"><div class="hd">${LOGO_MARK}<b>ALTA TECH<sup>LTD</sup></b><span class="prod">DMP</span><button class="icon-btn x" data-act="navclose">✕</button></div>
    ${NAV_GROUPS.filter(gr => gr.items.some(it => canRoute(it[0]))).map(gr => `<div class="grp">${gr.cn}</div>${gr.items.filter(it => canRoute(it[0])).map(it => `<button class="it ${it[0] === cur ? 'on' : ''}" data-act="goto" data-arg="#/${it[0]}">${it[1]}</button>`).join('')}`).join('')}
    <div class="ft"><button class="it" data-act="logout">切換身分</button></div></nav></div>`;
}
function viewSubNav() {
  const g = activeGroup(); const cur = S.route.name === 'ai' ? `ai/${S.ai.tab}` : S.route.name; const u = S.user;
  const items = g.items.filter(it => canRoute(it[0])).map(it => `<button class="sn-item ${it[0] === cur ? 'on' : ''}" data-act="goto" data-arg="#/${it[0]}">${it[1]}</button>`).join('');
  return `<div class="subnav"><div class="tn-in">${items}<div class="right"><span class="pill ${u.scope === 'all' ? 'blue' : 'amber'}">${svg(I.shield, 11)} ${u.scope === 'all' ? '全域' : '專案分公司 TPE01 · 回溯 90 天'}</span><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div></div></div></div>`;
}
function pageForbidden() { return `<div class="card" style="max-width:640px;margin:60px auto"><div class="card-h"><h3>沒有這個模組的權限</h3></div><div class="note">你的角色「${esc(S.user.role)}」對此模組沒有可見或可用權限（模組權限矩陣由平台管理員在權限中心維護）。如需使用，請向主管申請角色或臨時提權（需審批並自動到期）。</div><div class="acts" style="margin-top:12px;display:flex;gap:8px"><button class="btn-ghost" data-act="goto" data-arg="#/overview">回總覽</button><button class="btn-ghost" data-act="toast" data-arg="已送出臨時提權申請（4 小時，需管理員＋稽核審批）">申請臨時提權</button></div></div>`; }

/* ================================================================== v0.3.1：自訂角色（名稱、說明、範圍、勾選權限） */
const ACTS = [['v', '可見'], ['u', '可用'], ['e', '匯出'], ['a', '審批'], ['d', '設定']];
const SCOPES = [['global', '全域'], ['project', '專案限定'], ['segment', '分群限定'], ['aggregate_only', '僅聚合（AI 服務身份）']];
const allRoles = () => [...ROLES, ...Object.values(S.customRoles)];
const matrixOf = key => S.customMatrix[key] || MATRIX[key] || {};
const slug = s => { const a = String(s).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 24); return 'custom_' + (a || Date.now().toString(36)); };
function openRoleEditor(key, mode) {
  const base = key ? allRoles().find(r => r.key === key) : null;
  const grants = {}; MODULES.forEach(m => { grants[m[0]] = base ? (matrixOf(base.key)[m[0]] || '') : ''; });
  S.roleEdit = { mode, origKey: mode === 'edit' ? key : null, custom: !!(base && S.customRoles[base.key]) || mode !== 'edit',
    cn: base ? (mode === 'copy' ? base.cn + '（副本）' : base.cn) : '', desc: base ? base.desc : '', scope: base ? (base.scopeKey || (base.scope.startsWith('全域') ? 'global' : base.scope.includes('分公司') ? 'branch' : base.scope.includes('客戶') ? 'agent' : 'aggregate_only')) : 'global',
    lookback: base && base.lookback ? base.lookback : 0, grants, err: '' };
  render();
}
function roleToggle(mod, act) {
  const e = S.roleEdit; let g = e.grants[mod] || '';
  const has = g.includes(act);
  if (has) { g = g.replace(act, ''); if (act === 'v') g = g.replace('u', '').replace('e', ''); if (act === 'u') g = g.replace('e', ''); }
  else { g += act; if (act === 'u' && !g.includes('v')) g += 'v'; if (act === 'e') { if (!g.includes('v')) g += 'v'; if (!g.includes('u')) g += 'u'; } }
  e.grants[mod] = ACTS.map(a => a[0]).filter(a => g.includes(a)).join(''); render();
}
function roleRowAll(mod) { const e = S.roleEdit; const full = 'vuead'; e.grants[mod] = e.grants[mod] === full ? '' : full; render(); }
function roleColAll(act) { const e = S.roleEdit; const allOn = MODULES.every(m => (e.grants[m[0]] || '').includes(act)); MODULES.forEach(m => { const has = (e.grants[m[0]] || '').includes(act); if (allOn && has) roleToggleSilent(m[0], act); if (!allOn && !has) roleToggleSilent(m[0], act); }); render(); }
function roleToggleSilent(mod, act) { const r = render; render = () => {}; try { roleToggle(mod, act); } finally { render = r; } }
function roleSave() {
  const e = S.roleEdit; if (!e.cn.trim()) { e.err = '請輸入角色名稱'; render(); return; }
  if (!MODULES.some(m => e.grants[m[0]])) { e.err = '至少勾選一個模組權限'; render(); return; }
  const key = e.mode === 'edit' ? e.origKey : slug(e.cn);
  const scopeCn = SCOPES.find(s => s[0] === e.scope)[1] + (e.lookback ? ` · 回溯 ${e.lookback} 天` : '');
  const existing = allRoles().find(r => r.key === key);
  S.customRoles[key] = { key, cn: e.cn.trim(), desc: e.desc.trim(), scope: scopeCn, scopeKey: e.scope, lookback: e.lookback, users: existing ? existing.users : 0, custom: true };
  S.customMatrix[key] = { ...e.grants };
  S.roleEdit = null; S.iam.tab = 'roles';
  toast(`角色「${e.cn.trim()}」已儲存（role.changed 已審計；正式版需雙人複核）`);
}
function roleDelete() { const e = S.roleEdit; const k = e.origKey; if (!k || !S.customRoles[k]) return; delete S.customRoles[k]; delete S.customMatrix[k]; S.roleEdit = null; toast('角色已刪除（已審計）'); }
function roleDrawer() {
  const e = S.roleEdit; if (!e) return ''; const ro = !has('iam', 'd');
  const title = e.mode === 'new' ? '新增角色' : e.mode === 'copy' ? '複製角色' : ro ? '檢視角色' : '編輯角色';
  const builtin = e.mode === 'edit' && !e.custom;
  return `<div class="drawer-bg" data-act="roleclose"></div><div class="drawer wide"><div class="hd"><b>${title}</b>${builtin ? '<span class="pill gray">內建角色：可調整權限</span>' : '<span class="pill custom">自訂角色</span>'}<button class="icon-btn x" data-act="roleclose">✕</button></div>
  <div class="bd stack">
    <div class="form">
      <label>角色名稱 *</label><input class="inp" data-act="rolecn" value="${esc(e.cn)}" placeholder="例：外包分析（專案限定）、活動專案小組" ${ro ? 'disabled' : ''}>
      <label>說明</label><textarea data-act="roledesc" ${ro ? 'disabled' : ''}>${esc(e.desc)}</textarea>
      <label>數據範圍</label><select class="sel" data-act="rolescope" ${ro ? 'disabled' : ''}>${SCOPES.map(s => `<option value="${s[0]}" ${e.scope === s[0] ? 'selected' : ''}>${s[1]}</option>`).join('')}</select>
      <label>回溯天數</label><div style="display:flex;gap:8px;align-items:center"><input class="inp" style="width:120px" type="number" min="0" data-act="rolelookback" value="${e.lookback || ''}" placeholder="0 = 不限" ${ro ? 'disabled' : ''}><span class="note">限制可查詢的事件日期範圍；K 匿名門檻（20 人）一律套用</span></div>
      ${e.err ? `<label></label><div class="err">${esc(e.err)}</div>` : ''}
    </div>
    <div><div class="note" style="margin-bottom:8px"><b>模組權限</b>（勾「可用」會自動勾「可見」；「匯出」需先有「可用」；匯出一律走審批＋浮水印）</div>
    <table class="rm"><tr><th>模組</th>${ACTS.map(a => `<th>${a[1]}<br><span class="all" data-act="rolecol" data-arg="${a[0]}">全選</span></th>`).join('')}<th><span class="all">整列</span></th></tr>
    ${MODULES.map(m => { const g = e.grants[m[0]] || ''; return `<tr><td>${esc(m[1])}<span class="sub mono">${m[0]}</span></td>${ACTS.map(a => `<td><button class="chk ${g.includes(a[0]) ? 'on' : ''} ${ro ? 'dis' : ''}" data-act="rolechk" data-arg="${m[0]}:${a[0]}" aria-label="${a[1]}" ${ro ? 'disabled' : ''}></button></td>`).join('')}<td><button class="chk ${g === 'vuead' ? 'on' : ''} ${ro ? 'dis' : ''}" data-act="rolerow" data-arg="${m[0]}" ${ro ? 'disabled' : ''}></button></td></tr>`; }).join('')}</table></div>
    <div class="grid c21"><div class="card" style="padding:12px 14px"><div class="note"><b>欄位隱碼</b>：依 masking.yaml 的敏感等級套用；自訂角色預設「其他角色」顯示（L3 尾碼遮罩、L2 區間化）。需要明文請在欄位隱碼頁將角色加入 plain_roles（需稽核核可）。</div></div><div class="card" style="padding:12px 14px"><div class="note"><b>生效規則</b>：儲存後寫入審計 role.changed，正式版需雙人複核；指派給使用者時可設到期日。</div></div></div>
  </div>
  <div class="ft">${e.mode === 'edit' && e.custom && !ro ? `<button class="btn-ghost danger" data-act="roledelete">刪除角色</button>` : ''}<div class="right"><button class="btn-ghost" data-act="roleclose">${ro ? '關閉' : '取消'}</button>${ro ? '' : `<button class="btn-primary" data-act="rolesave">儲存</button>`}</div></div></div>`;
}

/* ================================================================== v0.3.2：受眾推送（Webhook 名單推送到內部系統） */
const destById = id => DESTINATIONS.find(d => d.id === id);
function openPush(segId) {
  const seg = SEGMENTS.find(s => s.id === segId); const ex = AUDIENCE_PUSHES.find(p => p.seg === segId);
  S.push = { seg: segId, dest: Object.fromEntries((ex ? ex.dest : []).map(d => [d, true])), mode: ex && ex.mode === 'realtime' ? 'realtime' : 'delta', schedule: ex ? (ex.schedule.includes('即時') ? 'realtime' : 'after_batch') : 'after_batch', tags: ['lifecycle', 'fee_tier'] };
  S.segDrawer = null; render();
}
function pushDrawer() {
  const p = S.push; if (!p) return ''; const seg = SEGMENTS.find(s => s.id === p.seg); const sel = DESTINATIONS.filter(d => p.dest[d.id]);
  const sample = { audience_id: seg.id, audience_name: seg.name, version: 43, mode: p.mode, batch: { index: 1, total: Math.max(1, Math.ceil(seg.n / 5000)), size: Math.min(5000, seg.n) }, identifier: 'customer_code', members: [{ customer_code: 'cust_8f3a…', one_id: 'oid_7k3a9q', action: 'add', tags: Object.fromEntries(p.tags.map(t => [t, t === 'lifecycle' ? '沉睡' : t === 'fee_tier' ? 'P1' : 'high'])) }, { customer_code: 'cust_21cc…', one_id: 'oid_2m8x1c', action: 'remove' }] };
  return `<div class="drawer-bg" data-act="pushclose"></div><div class="drawer wide"><div class="hd"><b>推送受眾包：${esc(seg.name)}</b><span class="pill">${n0(seg.n)} 人</span><button class="icon-btn x" data-act="pushclose">✕</button></div><div class="bd stack">
    <div><div class="note" style="margin-bottom:8px"><b>目的地</b>（可多選；皆為內部系統：Webhook／Kafka／GCS）</div>
    ${DESTINATIONS.map(d => `<div class="dest-pick ${p.dest[d.id] ? 'on' : ''} ${d.status === 'paused' ? 'dis' : ''}" data-act="pushdest" data-arg="${d.id}"><span class="chk ${p.dest[d.id] ? 'on' : ''}"></span><span class="dest-type ${d.type}">${esc(d.cn)}</span><span><b>${esc(d.name)}</b><span class="d"> · ${esc(d.target)} · 識別子 ${esc(d.ident)}</span></span></div>`).join('')}</div>
    <div class="form"><label>模式</label><div class="seg"><button class="${p.mode === 'delta' ? 'on' : ''}" data-act="pushmode" data-arg="delta">增量（加入／移除）</button><button class="${p.mode === 'full' ? 'on' : ''}" data-act="pushmode" data-arg="full">全量（需審批）</button><button class="${p.mode === 'realtime' ? 'on' : ''}" data-act="pushmode" data-arg="realtime">即時（成員進出即推）</button></div>
      <label>排程</label><div class="seg"><button class="${p.schedule === 'now' ? 'on' : ''}" data-act="pushsched" data-arg="now">立即</button><button class="${p.schedule === 'after_batch' ? 'on' : ''}" data-act="pushsched" data-arg="after_batch">日批完成後</button><button class="${p.schedule === 'realtime' ? 'on' : ''}" data-act="pushsched" data-arg="realtime">即時</button></div>
      <label>附帶標籤</label><div>${['lifecycle', 'fee_tier', 'churn_score_bucket', 'pref_market'].map(t => `<button class="pill ${p.tags.includes(t) ? 'blue' : ''}" data-act="pushtag" data-arg="${t}" style="margin:2px">${t}</button>`).join('')}<div class="note" style="margin-top:4px">L2 數值標籤一律區間化；名單只含代碼化識別子（customer_code／one_id），不含個資</div></div></div>
    <div class="grid c21"><div><div class="note" style="margin-bottom:6px"><b>Webhook payload 預覽</b>（X-DMP-Signature: HMAC-SHA256）</div><div class="code" style="font-size:10.5px">${esc(JSON.stringify(sample, null, 1))}</div></div>
    <div class="card"><div class="card-h"><h3>接收端要做的事</h3></div><div class="note">驗簽（HMAC-SHA256 + 時間戳 ±5 分鐘）→ 以 X-DMP-Delivery-Id 冪等 → 依 action 加入／移除名單 → 回 2xx。名單成員資格變動由 DMP 增量推送；全量推送用於初始化或對帳。</div></div></div>
    <div class="note">審批：${p.mode === 'full' ? '全量推送需數位經營核准' : '增量／即時依既核准排程自動執行'}。每次交付寫審計 sync.delivered。</div></div>
    <div class="ft"><button class="btn-ghost" data-act="toast" data-arg="已送出 audience.test（3 筆假資料）：CRM 200 · 180 ms；MA 200 · 95 ms">送測試事件</button><div class="right"><button class="btn-ghost" data-act="pushclose">取消</button><button class="btn-primary" data-act="pushsave">${p.mode === 'full' ? '送審並排程' : '儲存排程'}</button></div></div></div>`;
}
function pageDestinations() {
  return `<div class="card flush"><table class="tbl"><tr><th>目的地</th><th>類型</th><th>端點 / Topic</th><th>識別子</th><th>可附標籤</th><th class="num">7 天成功率</th><th>P95</th><th>最近交付</th><th>狀態</th><th></th></tr>${DESTINATIONS.map(d => `<tr><td><b>${esc(d.name)}</b><span class="sub">${esc(d.note)}</span></td><td><span class="dest-type ${d.type}">${esc(d.cn)}</span></td><td class="mono" style="font-size:11px">${esc(d.target)}</td><td class="mono" style="font-size:11px">${esc(d.ident)}</td><td>${d.tags.length ? d.tags.map(t => `<span class="pill mono" style="font-size:10px;margin:1px">${t}</span>`).join('') : '<span class="note">無（匿名 ID）</span>'}</td><td class="num mono">${d.ok7d == null ? '—' : pct(d.ok7d)}</td><td class="mono">${esc(d.p95)}</td><td style="color:var(--muted)">${esc(d.last)}</td><td>${d.status === 'active' ? '<span class="pill green">啟用</span>' : '<span class="pill gray">停用</span>'}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="toast" data-arg="已送出 audience.test，接收端回 200">測試</button> <button class="btn-ghost sm" data-act="toast" data-arg="secret 已輪替，新舊並存 24 小時">輪替 secret</button></td></tr>`).join('')}</table></div>
  <div class="grid c2" style="margin-top:16px"><div class="card flush"><div class="card-h" style="padding:14px 16px 0"><h3>交付紀錄</h3><span class="sub">sync.delivered · 近 2 天</span></div><table class="tbl"><tr><th>Delivery</th><th>受眾包</th><th>目的地</th><th class="num">版本</th><th>模式</th><th class="num">筆數</th><th>批次</th><th>狀態</th><th>回應</th><th></th></tr>${DELIVERIES.map(x => `<tr><td class="mono" style="font-size:11px">${esc(x.id)}</td><td>${esc(x.seg)}</td><td>${esc(x.dest)}</td><td class="num mono">${esc(String(x.ver))}</td><td>${esc(x.mode)}</td><td class="num mono">${n0(x.n)}</td><td class="mono">${esc(x.batches)}</td><td>${({ ok: '<span class="pill green">成功</span>', running: '<span class="pill blue">進行中</span>', retrying: '<span class="pill amber">重試中</span>', failed: '<span class="pill red">失敗</span>' })[x.status]}</td><td style="color:var(--body);font-size:11.5px">${esc(x.resp)}</td><td class="num">${x.status === 'retrying' || x.status === 'failed' ? `<button class="btn-ghost sm" data-act="toast" data-arg="已重放（沿用同一 Delivery-Id）">重放</button>` : ''}</td></tr>`).join('')}</table></div>
  <div class="stack"><div class="card"><div class="card-h"><h3>Webhook 規格</h3></div><div class="code" style="font-size:11px">POST {url}\nX-DMP-Event: audience.delta | audience.full | tag.update\nX-DMP-Delivery-Id: dlv_…   （冪等鍵）\nX-DMP-Timestamp: 1760400000\nX-DMP-Signature: sha256=HMAC(secret, ts + "." + body)\n\n每批 ≤ 5,000 筆；2xx 即成功；重試 1m/5m/15m/1h/6h → DLQ 可重放；\n連續失敗 3 批暫停目的地並通知。</div></div>
  <div class="card"><div class="card-h"><h3>範圍說明</h3></div><div class="note">本系統的受眾推送只做「名單推送」：把分群成員（代碼化識別子＋可選標籤）以簽章 Webhook／Kafka／GCS 交給內部系統（MA、CRM、數倉），由這些系統負責後續觸達。廣告平台（GA／Google Ads 再行銷、Customer Match）不在本系統範圍。</div></div></div></div>`;
}

/* ---- 權限中心 ---- */
function pageIAM() {
  const t = S.iam.tab; const tabs = [['roles', '角色'], ['assign', '使用者指派'], ['modules', '模組權限'], ['data', '數據權限'], ['masking', '欄位隱碼'], ['approvals', '審批']];
  const act = { v: ['v', '可見'], u: ['u', '可用'], e: ['e', '匯出'], a: ['a', '審批'], d: ['d', '設定'] };
  let body = '';
  if (t === 'roles') body = `<div class="card flush"><table class="tbl"><tr><th>角色</th><th>範圍</th><th>說明</th><th class="num">使用者</th><th>權限摘要</th><th></th></tr>${allRoles().map(r => { const g = matrixOf(r.key); const nMod = MODULES.filter(m => (g[m[0]] || '').includes('v') || (g[m[0]] || '').includes('u')).length; const nE = MODULES.filter(m => (g[m[0]] || '').includes('e')).length; const nA = MODULES.filter(m => (g[m[0]] || '').includes('a')).length; return `<tr class="click" data-act="roleopen" data-arg="${r.key}"><td><b>${esc(r.cn)}</b>${r.custom ? ' <span class="pill custom">自訂</span>' : ''}<span class="sub mono">${esc(r.key)}</span></td><td><span class="pill ${r.scope.startsWith('全域') || r.scope.startsWith('僅聚合') ? 'blue' : 'amber'}">${esc(r.scope)}</span></td><td style="color:var(--body)">${esc(r.desc)}</td><td class="num mono">${n0(r.users)}</td><td style="font-size:11.5px;color:var(--body)">${nMod} 個模組 · 匯出 ${nE} · 審批 ${nA}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="roleopen" data-arg="${r.key}">${has('iam', 'd') ? '編輯' : '檢視'}</button> ${has('iam', 'd') ? `<button class="btn-ghost sm" data-act="rolecopy" data-arg="${r.key}">複製</button>` : ''}</td></tr>`; }).join('')}</table></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>多角色組合規則</h3></div><div class="note">功能權限取<b>聯集</b>；欄位隱碼取<b>最嚴格</b>（避免以多角色繞過）；數據範圍取聯集。</div></div><div class="card"><div class="card-h"><h3>指派時效</h3></div><div class="note">角色指派可設到期日；臨時提權（break-glass）需管理員＋稽核審批，最長 4 小時自動到期。</div></div><div class="card"><div class="card-h"><h3>身分來源</h3></div><div class="note">SSO（OIDC／AD）；組織樹同步每日；客戶重分配以 items(agent) 版本化。</div></div></div>`;
  else if (t === 'assign') body = iamAssignments();
  else if (t === 'modules') body = `<div class="card flush" style="overflow-x:auto"><table class="matrix"><tr><th>模組 ＼ 角色</th>${allRoles().map(r => `<th>${esc(r.cn)}${r.custom ? '<br><span class="pill custom" style="font-size:9px">自訂</span>' : ''}</th>`).join('')}</tr>${MODULES.map(m => `<tr><td>${esc(m[1])}</td>${allRoles().map(r => { const p = matrixOf(r.key)[m[0]] || ''; return `<td><span class="a">${Object.keys(act).map(k => `<i class="${p.includes(k) ? (k === 'a' ? 'ap' : k === 'd' ? 'ad' : '') : 'x'}" title="${act[k][1]}">${k}</i>`).join('')}</span></td>`; }).join('')}</tr>`).join('')}</table></div>
    <div class="note" style="margin-top:10px">v 可見 · u 可用 · e 匯出（一律審批＋浮水印）· a 審批 · d 設定。UI、REST API 與 AI 工具閘道共用同一個 authorize()；AI 以委託身份執行，權限與人相同。</div>`;
  else if (t === 'data') body = `<div class="card flush"><table class="tbl"><tr><th>維度</th><th>規則</th><th>實作</th><th>適用</th></tr>${DATA_SCOPES.map(d => `<tr><td><b>${esc(d.k)}</b></td><td class="mono" style="font-size:11.5px">${esc(d.rule)}</td><td style="color:var(--body)">${esc(d.how)}</td><td>${esc(d.who)}</td></tr>`).join('')}</table></div>
    <div class="grid c2" style="margin-top:16px"><div class="card"><div class="card-h"><h3>你目前的行級條件</h3></div><div class="code">${S.user.scope === 'all' ? '-- 全域：無行級條件' : "project IN ('sinopac_demo') AND event_date >= today()-180"}\n-- K 匿名：分組人數 < 20 → 顯示「< 20」</div></div><div class="card"><div class="card-h"><h3>範圍來源</h3></div><div class="note">範圍綁在「使用者 × 角色」：同一角色可指派不同組織範圍。查詢可選「當前歸屬」或「事件當下歸屬」。</div></div></div>` + iamScopeSim();
  else if (t === 'masking') body = `<div class="card flush"><table class="tbl"><tr><th>欄位</th><th>等級</th><th>明文可見</th><th>其他角色顯示</th><th>說明</th></tr>${MASK_RULES.map(m => `<tr><td style="font-weight:600">${esc(m.field)}</td><td><span class="level ${m.level}">${m.level}</span></td><td>${esc(m.plain)}</td><td class="mono" style="font-size:11.5px">${esc(m.others)}</td><td style="color:var(--body)">${esc(m.how)}</td></tr>`).join('')}</table></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>揭露申請</h3></div><div class="note">理由必填、時效 24 小時、L3 需系統管理員、L2 需數位經營；揭露期間每次查看都寫審計（mask.revealed）。</div></div><div class="card"><div class="card-h"><h3>匯出</h3></div><div class="note">比 UI 更嚴一級（L2 → 區間化）、浮水印（使用者／時間／單號）、上限 10 萬列。</div></div><div class="card"><div class="card-h"><h3>AI agent</h3></div><div class="note">工具回傳前先套遮罩；敘事層正規式攔截身分證／手機／Email；profile.* 必填理由。</div></div></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>你的視角示範</h3><span class="sub">${esc(S.user.role)}</span></div><table class="tbl"><tr><th>one_id</th><th>客戶代碼</th><th class="num">90 天手續費</th><th class="num">流失分</th></tr><tr><td>${mId('oid_7k3a9q')}</td><td>${mId('cust_00007281')}</td><td class="num">${mMoney(48000)}</td><td class="num">${mScore(23)}</td></tr><tr><td>${mId('oid_2m8x1c')}</td><td>${mId('cust_00000412')}</td><td class="num">${mMoney(312000)}</td><td class="num">${mScore(81)}</td></tr></table>${idMasked() ? `<div class="acts" style="margin-top:10px"><button class="btn-ghost sm" data-act="reveal" data-arg="one_id">申請揭露 one_id（24h）</button></div>` : ''}</div>`;
  else body = `<div class="card flush"><table class="tbl"><tr><th>單號</th><th>類型</th><th>申請者</th><th>內容</th><th>附註</th><th>時間</th><th>審批者</th><th>狀態</th><th></th></tr>${APPROVALS.map(a => { const st = S.approved[a.id] ? 'approved' : a.status; return `<tr><td class="mono">${a.id}</td><td><span class="pill ${({ '分群推送': 'blue', 'agent 提議': 'purple', '欄位揭露': 'amber', '匯出': 'teal', '臨時提權': 'red' })[a.type] || 'gray'}">${esc(a.type)}</span></td><td>${a.who.includes('-') ? `${svg(I.spark, 11)} ${esc(a.who)}` : esc(a.who)}</td><td>${esc(a.what)}</td><td style="color:var(--body)">${esc(a.n)}</td><td style="color:var(--muted)">${esc(a.at)}</td><td>${esc(a.approver)}</td><td>${({ pending: '<span class="pill amber">待審</span>', approved: '<span class="pill green">核准</span>', rejected: '<span class="pill red">駁回</span>', expired: '<span class="pill gray">已到期</span>' })[st]}</td><td class="num">${st === 'pending' && S.user.can.approve ? `<button class="btn-ghost sm" data-act="approve" data-arg="${a.id}">核准</button> <button class="btn-ghost sm danger" data-act="toast" data-arg="已駁回並通知申請者">駁回</button>` : ''}</td></tr>`; }).join('')}</table></div>`;
  return `<div class="page-h"><div><h1>權限中心</h1><p>角色權限（RBAC + 範圍）、模組權限矩陣、數據權限（行級）、欄位隱碼（列級）、審批流程。UI、API 與 AI 工具閘道共用同一套 Policy Engine。</p></div><div class="right">${has('iam', 'd') ? '<button class="btn-primary" data-act="rolenew">＋ 新增角色</button>' : '<span class="pill gray">唯讀</span>'}</div></div>
  <div class="filters"><div class="seg">${tabs.map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="iamtab" data-arg="${x[0]}">${x[1]}${x[0] === 'approvals' ? ` ${APPROVALS.filter(a => a.status === 'pending' && !S.approved[a.id]).length}` : ''}</button>`).join('')}</div></div>${body}`;
}

/* ---- 日誌統計 ---- */
function pageAudit() {
  const A = AUDIT; const full = ['admin', 'it_admin', 'digital_ops'].includes(RK());
  return `<div class="page-h"><div><h1>日誌統計</h1><p>所有人與 AI 的讀寫都是審計事件；這裡用看板看使用概況、敏感存取、審批與異常。${full ? '你看到的是全平台。' : '你的角色只看到自己（或部門）的紀錄。'}</p></div><div class="right"><button class="btn-ghost" data-act="toast" data-arg="已匯出審計報表（需審批、浮水印）">匯出審計報表</button><button class="btn-primary teal" data-act="askpre" data-arg="上週誰細查最多？有沒有非上班時間的敏感存取？">${svg(I.spark, 14)} 問 agent</button></div></div>
  <div class="grid c3" style="grid-template-columns:repeat(6,1fr);margin-bottom:16px">${A.tiles.map(t => `<div class="tile"><div class="lbl">${t[0]}</div><div class="val" style="font-size:22px">${t[1]}</div><div class="delta"><span>${t[2]}</span></div></div>`).join('')}</div>
  <div class="grid c2"><div class="stack"><div class="card"><div class="card-h"><h3>細查次數（依角色）</h3><span class="sub">profile.viewed · 近 30 天</span></div>${lineChart('au-prof', A.profileByRole.map((s, i) => ({ name: s.name, color: SER[i], data: s.data })), { h: 220, fmt: n0, area: false })}</div>
    <div class="grid c21"><div class="card"><div class="card-h"><h3>權限拒絕</h3><span class="sub">authz.denied · 日</span></div>${lineChart('au-den', [{ name: '拒絕次數', color: SER[7], data: A.denied }], { h: 150, fmt: n0 })}</div><div class="card"><div class="card-h"><h3>匯出件數</h3><span class="sub">export.downloaded · 日</span></div>${lineChart('au-exp', [{ name: '匯出件數', color: SER[2], data: A.exports }], { h: 150, fmt: n0 })}</div></div>
    <div class="card"><div class="card-h"><h3>模組使用次數</h3><span class="sub">query.executed · 近 7 天</span></div>${hbar(A.modules, { fmt: n0 })}</div></div>
  <div class="stack"><div class="card"><div class="card-h"><h3>敏感存取 Top</h3><span class="sub">近 7 天細查</span></div><table class="tbl"><tr><th>使用者</th><th class="num">次數</th><th>主要理由</th></tr>${A.topProfile.map(r => `<tr><td><b>${esc(r[0])}</b><span class="sub">${esc(r[1])}</span></td><td class="num mono">${r[2]}</td><td style="color:var(--body)">${esc(r[3])}</td></tr>`).join('')}</table></div>
    <div class="card"><div class="card-h"><h3>異常與告警</h3></div><table class="tbl">${A.anomalies.map(r => `<tr><td class="mono" style="color:var(--muted);white-space:nowrap">${r[0]}</td><td><span class="status ${r[3]}"><span class="dot"></span>${esc(r[1])}</span><span class="sub">${esc(r[2])}</span></td></tr>`).join('')}</table></div>
    <div class="card"><div class="card-h"><h3>AI agent 活動</h3></div><div class="grid c21" style="gap:10px">${A.agent.map(a => `<div class="tile" style="padding:12px 14px"><div class="lbl">${a.k}</div><div class="val" style="font-size:20px">${a.k === '採納率' ? a.v + '%' : n0(a.v)}</div></div>`).join('')}</div><div class="note" style="margin-top:8px">evals 通過率 94%；敏感工具（profile.*）呼叫皆附理由與委託身份。</div></div>
    <div class="card"><div class="card-h"><h3>告警規則</h3></div><div class="note">非上班時間細查 > 3／人／日 · 單人 1 小時細查 > 10 · 匯出 > 5 萬列 · 權限拒絕 1 小時 > 10 · 同帳號多 IP → 通知稽核。</div></div></div></div>`;
}

/* ------------------------------------------------------------------ pages */
function tile(lbl, key, fmt, unit = '') {
  const n = S.range; const cur = sumLast(key, n); const d = delta(key, n); const avg = key === 'reject_rate' || key === 'dau' || key === 'traders';
  const val = avg ? sumLast(key, n) / n : cur; const up = d >= 0; const good = key === 'reject_rate' ? !up : up;
  return `<div class="tile"><div class="lbl">${lbl}${avg ? '（日均）' : `（${n} 天）`}</div><div class="val">${fmt(val)}${unit ? `<small>${unit}</small>` : ''}</div><div class="delta ${good ? 'up' : 'down'}">${svg(up ? I.up : I.down, 12)}<b>${pct(Math.abs(d))}</b><span>vs 前 ${n} 天</span></div>${spark(last(TS[key], n), good ? 'var(--s1)' : 'var(--s2)')}</div>`;
}
function insightCard(i, compactMode = false) {
  const open = S.ai.open[i.id]; const fb = S.feedback[i.id];
  const kindCn = { anomaly: '異常', funnel: '漏斗', risk: '風險', opportunity: '機會', campaign: '活動', answer: '問答' }[i.kind];
  const stCn = { open: '待處理', acknowledged: '已確認', resolved: '已解決', dismissed: '已忽略' }[i.status];
  return `<div class="insight ${i.sev} ${open ? 'open' : ''} ${compactMode ? 'compact' : ''}" id="${i.id}" ${compactMode ? `data-act="goto" data-arg="#/ai/insights"` : ''}><div class="sev"></div><div>
    <div class="hd"><span class="pill ${({ critical: 'red', high: 'red', medium: 'amber', low: 'green' })[i.sev]}"><span class="dot"></span>${({ critical: '緊急', high: '高', medium: '中', low: '低' })[i.sev]}</span><span class="pill gray">${kindCn}</span><b>${esc(i.title)}</b><span class="ag">${svg(I.spark, 12)}${esc(i.agent)} · ${esc(i.at)}</span></div>
    <div class="sm">${esc(i.summary)}</div>
    ${compactMode ? '' : `<div class="ev">${i.evidence.map(e => `<span class="pill">${esc(e)}</span>`).join('')}</div>`}
    <div class="ft"><span class="conf">信心度 <i style="--w:${Math.round(i.conf * 100)}%"></i> ${Math.round(i.conf * 100)}%</span><span class="pill gray">${stCn}</span>${i.warn && !compactMode ? `<span style="color:#9A5E0E">${svg(I.warn, 12)} ${esc(i.warn)}</span>` : ''}
      ${compactMode ? '' : `<span class="right"><button class="btn-ghost sm ${fb === 'useful' ? 'on' : ''}" data-act="fb" data-arg="${i.id}:useful">${fb === 'useful' ? '✓ ' : ''}有用</button><button class="btn-ghost sm" data-act="fb" data-arg="${i.id}:wrong">${fb === 'wrong' ? '✓ ' : ''}有誤</button><button class="btn-ghost sm" data-act="insopen" data-arg="${i.id}">${open ? '收合' : '證據與行動'}</button></span>`}</div>
    <div class="body">${i.chart ? insightChart(i) : ''}
      <div class="note" style="margin-top:8px"><b>證據（可重跑）</b></div>${i.evidence.map((e, k) => `<div class="code" style="margin-top:6px">${k + 1}. ${esc(e)}</div>`).join('')}
      ${i.actions.map(a => `<div class="act">${a.st === 'done' ? svg(I.check, 14) : svg(I.warn, 14)}<span>${esc(a.t)}</span>${a.st === 'pending' ? `<span class="pill amber" style="margin-left:auto">待審批</span>` : `<span class="pill green" style="margin-left:auto">已完成</span>`}</div>`).join('')}
      <div class="acts" style="margin-top:10px"><button class="btn-ghost sm" data-act="askpre" data-arg="為什麼 ${esc(i.title.slice(0, 18))}…？請深入說明並建議下一步">${svg(I.spark, 13)} 追問 agent</button><button class="btn-ghost sm" data-act="goto" data-arg="#/funnel">開啟漏斗頁</button><button class="btn-ghost sm" data-act="toast" data-arg="已加入週報草稿（report.compose）">加入週報</button></div></div>
  </div></div>`;
}
function insightChart(i) {
  if (i.chart === 'reject_rate') return lineChart('ins-rr', [{ name: '委託失敗率', color: SER[1], data: last(TS.reject_rate, 14) }], { h: 150, fmt: v => pct(v, 2) });
  if (i.chart === 'funnel_open') return funnelView(FUNNELS[0]);
  if (i.chart === 'utm') return hbar(UTM_SPLIT.map(u => ({ k: u.k, v: Math.round(u.fund * 1000) / 10 })), { fmt: v => v + '%', color: SER[2], total: 1e9 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '');
  return '';
}

function pageOverview() {
  const open3 = INSIGHTS.filter(i => i.status === 'open').slice(0, 3);
  return `<div class="page-h"><div><h1>總覽</h1><p>GA360 行為事件與證券交易事件在同一個事件模型下；右側為 AI agent 近期產出的洞察。</p></div></div>
  <div class="grid c5" style="margin-bottom:16px">${tile('日活躍客戶', 'dau', compact)}${tile('成交客戶數', 'traders', compact)}${tile('成交金額', 'turnover', compact, 'TWD')}${tile('新開戶', 'new_accounts', n0)}${tile('委託失敗率', 'reject_rate', v => pct(v, 2))}</div>
  <div class="grid c2"><div class="stack">
    <div class="card"><div class="card-h"><h3>事件量：行為 vs 交易</h3><span class="sub">每日事件數 · 兩條來源同一事件表</span><div class="right"><button class="btn-ghost sm" data-act="sql" data-arg="events_volume">${svg(I.code, 13)} SQL</button></div></div>${lineChart('ov-vol', [{ name: 'GA360 行為事件', color: SER[0], data: seriesRange('ga_events') }, { name: '交易事件', color: SER[1], data: seriesRange('trade_events') }], { h: 230 })}</div>
    <div class="grid c21"><div class="card"><div class="card-h"><h3>委託渠道</h3><span class="sub">近 ${S.range} 天</span></div>${hbar(CHANNEL_SPLIT)}</div>
    <div class="card"><div class="card-h"><h3>開戶→首筆成交</h3><span class="sub">30 天視窗</span><div class="right"><button class="btn-ghost sm" data-act="goto" data-arg="#/funnel">展開</button></div></div>${funnelView(FUNNELS[0], { compare: false })}</div></div>
  </div><div class="stack">
    <div class="card"><div class="card-h"><h3>AI 洞察</h3><span class="sub">待處理 ${INSIGHTS.filter(i => i.status === 'open').length} 則</span><div class="right"><button class="btn-ghost sm" data-act="goto" data-arg="#/ai/insights">全部</button></div></div><div class="stack" style="gap:10px">${open3.map(i => insightCard(i, true)).join('')}</div></div>
    <div class="card"><div class="card-h"><h3>資料源健康</h3><div class="right"><button class="btn-ghost sm" data-act="goto" data-arg="#/governance/sources">詳細</button></div></div><table class="tbl">${SOURCES.slice(0, 6).map(s => `<tr><td><span class="status ${s.status}"><span class="dot"></span>${esc(s.name)}</span>${s.note && s.status !== 'ok' ? `<span class="sub">${esc(s.note)}</span>` : ''}</td><td class="num mono" style="color:var(--muted)">${esc(s.lag)}</td></tr>`).join('')}</table></div>
  </div></div>`;
}

/* ================================================================== v0.4：查詢條件建構器 + 模擬查詢引擎（事件分析／漏斗分析） */
// 設計對標：神策分析「事件分析」（多指標＋指標類型、篩選條件 且/或、按分組查看、時間粒度、圖表切換、數據表）
// 與「漏斗分析」（步驟＋每步篩選、窗口期、關聯屬性、分組對比、轉化趨勢、流失用戶）；火山 DataFinder「轉化分析」（窗口期、人群對比、UV/PV）。
const hash = s => { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; } return h; };
const hf = (s, lo, hi) => lo + (hash(s) % 10007) / 10007 * (hi - lo);          // 以設定字串決定的穩定係數
const propOf = k => PROPS.find(p => p.k === k);
const evOf = name => EVENTS.find(e => e.name === name) || EVENTS[0];
const LIVE_EVENTS = EVENTS.filter(e => e.status === 'live');
const NUM_PROPS = PROPS.filter(p => p.type === 'number');
const newFilter = () => ({ logic: 'and', items: [] });
const newCond = (k = 'platform') => { const p = propOf(k); return { k, op: OPS[p.type][0][0], v: p.vals ? p.vals[0] : (p.type === 'number' ? '1000' : ''), v2: '' }; };
const condText = c => { const p = propOf(c.k); const op = (OPS[p.type].find(o => o[0] === c.op) || [])[1] || c.op; const needV = !['set', 'nset', 'true', 'false'].includes(c.op); return `${p.cn} ${op}${needV ? ' ' + (c.op === 'between' ? `${c.v}～${c.v2}` : c.v) : ''}`; };
const filterText = f => f.items.length ? f.items.map(condText).join(f.logic === 'and' ? ' 且 ' : ' 或 ') : '';
const filterKey = f => f.items.map(c => `${c.k}${c.op}${c.v}${c.v2}`).join(f.logic);
function filterFactor(f) {                                                     // 篩選後保留比例（AND 連乘、OR 取聯集）
  if (!f.items.length) return 1;
  const fs = f.items.map(c => ['set', 'nlike', 'neq'].includes(c.op) ? hf(c.k + c.op + c.v, .72, .96) : ['nset'].includes(c.op) ? hf(c.k, .02, .12) : hf(c.k + c.op + c.v + c.v2, .12, .6));
  return f.logic === 'and' ? fs.reduce((a, b) => a * b, 1) : 1 - fs.reduce((a, b) => a * (1 - b), 1);
}
// 篩選條件建構器：ns 為條件物件的位址（ev / ev.m0 / fn / fn.s1）
function fbRef(ns) {
  if (ns === 'ev') return S.ev.filters; if (ns === 'fn') return S.funnel.filters;
  if (ns.startsWith('ev.m')) return S.ev.metrics[Number(ns.slice(4))].filters;
  if (ns.startsWith('fn.s')) return S.funnel.steps[Number(ns.slice(4))].filters;
  if (ns === 'pt') return S.path.filters; if (ns === 'pt.anchor') return S.path.anchorFilters;
  if (ns === 'at') return S.attr.filters; if (ns === 'at.goal') return S.attr.goalFilters; if (ns.startsWith('at.t')) return S.attr.touches[Number(ns.slice(4))].filters;
  if (ns === 'rt') return S.ret.filters; if (ns === 'rt.init') return S.ret.initFilters; if (ns === 'rt.ret') return S.ret.retFilters;
  return newFilter();
}
function filterBuilder(ns, { title = '篩選條件', compact = false } = {}) {
  const f = fbRef(ns);
  const propSel = (c, i) => `<select class="sel" data-act="fbprop" data-arg="${ns}:${i}">${PROP_SCOPES.map(sc => `<optgroup label="${sc[1]}">${PROPS.filter(p => p.scope === sc[0]).map(p => `<option value="${p.k}" ${p.k === c.k ? 'selected' : ''}>${esc(p.cn)}<span> </span>${p.scope === 'event' ? ' ' + p.k : ''}</option>`).join('')}</optgroup>`).join('')}</select>`;
  const rows = f.items.map((c, i) => {
    const p = propOf(c.k); const ops = OPS[p.type]; const needV = !['set', 'nset', 'true', 'false'].includes(c.op);
    let val = '';
    if (needV) {
      if (p.vals && ['eq', 'neq', 'in'].includes(c.op)) val = `<select class="sel" data-act="fbval" data-arg="${ns}:${i}">${p.vals.map(v => `<option ${v === c.v ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>${c.op === 'in' ? `<button class="btn-ghost sm" data-act="toast" data-arg="原型：多選值請以逗號分隔；正式版為可搜尋多選">+ 多選</button>` : ''}`;
      else if (c.op === 'between') val = `<input class="inp num" type="${p.type === 'date' ? 'date' : 'number'}" data-act="fbval" data-arg="${ns}:${i}" value="${esc(c.v)}"><span class="note">～</span><input class="inp num" type="${p.type === 'date' ? 'date' : 'number'}" data-act="fbval2" data-arg="${ns}:${i}" value="${esc(c.v2)}">`;
      else if (c.op === 'rel') val = `<span class="note">過去</span><input class="inp num" type="number" data-act="fbval" data-arg="${ns}:${i}" value="${esc(c.v || 30)}"><span class="note">天內</span>`;
      else val = `<input class="inp ${p.type === 'number' ? 'num' : ''}" type="${p.type === 'number' ? 'number' : 'text'}" data-act="fbval" data-arg="${ns}:${i}" value="${esc(c.v)}" placeholder="${p.type === 'number' ? '數值' : '文字'}">`;
    }
    return `<div class="fb-row">${i ? `<span class="fb-logic">${f.logic === 'and' ? '且' : '或'}</span>` : '<span class="fb-logic first">其中</span>'}${propSel(c, i)}<select class="sel" data-act="fbop" data-arg="${ns}:${i}">${ops.map(o => `<option value="${o[0]}" ${o[0] === c.op ? 'selected' : ''}>${o[1]}</option>`).join('')}</select>${val}<button class="icon-btn x sm" data-act="fbdel" data-arg="${ns}:${i}" title="移除條件">✕</button></div>`;
  }).join('');
  return `<div class="fb ${compact ? 'compact' : ''}"><div class="fb-hd"><span>${esc(title)}</span>${f.items.length > 1 ? `<span class="seg xs"><button class="${f.logic === 'and' ? 'on' : ''}" data-act="fblogic" data-arg="${ns}:and">且</button><button class="${f.logic === 'or' ? 'on' : ''}" data-act="fblogic" data-arg="${ns}:or">或</button></span>` : ''}<span class="right note">${f.items.length ? `${f.items.length} 條` : '未設定'}</span></div>${rows}<div class="fb-ft"><button class="btn-ghost sm" data-act="fbadd" data-arg="${ns}">＋ 添加篩選條件</button>${f.items.length ? `<button class="btn-ghost sm" data-act="fbclear" data-arg="${ns}">清空</button>` : ''}</div></div>`;
}
// 時間粒度：以 90 天日序列為母體，聚合或展開
const DAU_SHAPE = TS.dau.map(p => p.v / (sum(TS.dau.map(q => q.v)) / TS.dau.length));
const shapeAt = i => DAU_SHAPE[((i % 90) + 90) % 90];
const HOUR_PROFILE = [.05, .03, .02, .02, .02, .04, .12, .35, .9, 1.9, 1.8, 1.6, 1.5, 1.3, .7, .5, .45, .5, .6, .7, .8, .7, .4, .15];
function bucketize(points, gran, isUsers) {
  if (gran === 'day') return points;
  if (gran === 'total') return [{ d: '合計', v: isUsers ? Math.round(sum(points.map(p => p.v)) * .58) : sum(points.map(p => p.v)) }];
  if (gran === 'hour' || gran === 'minute') { const lastV = points[points.length - 1].v; const n = gran === 'hour' ? 24 : 60; const tot = sum(HOUR_PROFILE); return Array.from({ length: n }, (_, i) => { const h = gran === 'hour' ? i : 13; const w = gran === 'hour' ? HOUR_PROFILE[h] / tot : HOUR_PROFILE[13] / tot / 60 * (1 + Math.sin(i * 1.7) * .25); return { d: gran === 'hour' ? `${String(h).padStart(2, '0')}:00` : `13:${String(i).padStart(2, '0')}`, v: Math.round(lastV * w) }; }); }
  const keyOf = d => { const dt = new Date(d); if (gran === 'week') { const w = new Date(dt); w.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)); return dkey(w).slice(5) + ' 週'; } return d.slice(0, 7); };
  const out = []; const map = {};
  points.forEach(p => { const k = keyOf(p.d); if (!map[k]) { map[k] = { d: k, v: 0, n: 0 }; out.push(map[k]); } map[k].v += p.v; map[k].n++; });
  return out.map(b => ({ d: b.d, v: isUsers ? Math.round(b.v * (gran === 'week' ? .62 : .41)) : b.v }));
}
// 事件分析：指標 → 時序
function metricLabel(m) { const e = evOf(m.ev); const a = AGGS.find(x => x[0] === m.agg); return `${e.cn}·${a[1]}${a[2] ? `(${propOf(m.prop)?.cn || ''})` : ''}`; }
function metricFmt(m) { return ['sum', 'avg', 'max', 'min', 'p50', 'p90'].includes(m.agg) && /amount|fee/.test(m.prop || '') ? v => compact(v) : m.agg === 'avg_per_user' ? v => v.toFixed(2) : compact; }
function metricDaily(m, offset = 0) {                                            // offset: 0 本期、S.range 上期
  const e = evOf(m.ev); const usersBase = Math.min(e.daily * .3, 412000 * hf(e.name + ':u', .25, .95));
  const ff = filterFactor(S.ev.filters) * filterFactor(m.filters);
  const p = m.prop ? propOf(m.prop) : null; const unit = p ? hf(p.k + e.name, /amount|fee/.test(p.k) ? 900 : 3, /amount|fee/.test(p.k) ? 48000 : 400) : 1;
  const base = { total: e.daily, users: usersBase, avg_per_user: e.daily / usersBase, uniq: usersBase * hf(m.prop || '', .08, .6), sum: e.daily * unit, avg: unit, max: unit * hf(e.name + 'max', 18, 70), min: unit * hf(e.name + 'min', .01, .08), p50: unit * .72, p90: unit * 2.4 }[m.agg];
  const scaled = ['avg_per_user', 'avg', 'max', 'min', 'p50', 'p90'].includes(m.agg) ? base * (ff < 1 ? hf(filterKey(S.ev.filters) + filterKey(m.filters), .85, 1.25) : 1) : base * ff;
  const drift = hf(e.name + m.agg, -.08, .14);
  return Array.from({ length: S.range }, (_, j) => { const i = 90 - S.range + j - offset; const t = (i / 90); const v = scaled * (['avg_per_user', 'avg', 'p50', 'p90'].includes(m.agg) ? 1 + (shapeAt(i) - 1) * .3 : shapeAt(i)) * (1 + drift * t) * (1 + Math.sin(i * .9 + hash(e.name) % 7) * .03); return { d: dkey(dayOffset(j - S.range + 1 - offset)), v: v < 100 ? Math.round(v * 100) / 100 : Math.round(v) }; });
}
function groupSplit(points, dims, topN) {                                        // 按分組查看：多維度 → 組合
  if (!dims.length) return [{ name: null, data: points }];
  const combos = dims.reduce((acc, d) => { const p = propOf(d); const vals = p.vals || (p.type === 'number' ? ['0–100', '100–1k', '1k–10k', '> 10k'] : ['true', 'false']); return acc.flatMap(a => vals.map(v => [...a, v])); }, [[]]);
  const ws = combos.map(c => hf(c.join('|'), .3, 1.6)); const tot = sum(ws);
  return combos.map((c, i) => ({ name: c.join(' / '), w: ws[i] / tot, data: points.map((p, j) => ({ d: p.d, v: Math.round(p.v * ws[i] / tot * (1 + Math.sin(j * .5 + i) * .05) * 100) / 100 })) })).sort((a, b) => b.w - a.w).slice(0, topN);
}
function evSeries() {
  const ev = S.ev; const out = []; const isUsers = m => ['users', 'uniq'].includes(m.agg);
  ev.metrics.forEach((m, mi) => {
    const daily = metricDaily(m); const prev = ev.compare ? metricDaily(m, S.range) : null;
    groupSplit(daily, ev.groups, ev.topN).forEach((g, gi) => {
      const nm = (ev.metrics.length > 1 || !g.name ? metricLabel(m) : '') + (g.name ? (ev.metrics.length > 1 ? ' · ' : '') + g.name : '');
      out.push({ name: nm, m, color: SER[out.length % SER.length], data: bucketize(g.data, ev.gran, isUsers(m)) });
      if (prev && !g.name) out.push({ name: nm + '（上期）', m, prev: true, color: SER[out.length % SER.length], dash: true, data: bucketize(prev, ev.gran, isUsers(m)).map((p, i) => ({ d: out[out.length - 1].data[i]?.d || p.d, v: p.v })) });
    });
  });
  return out.slice(0, 8);
}
// 柱狀 / 堆疊 / 累計
function barChart(id, series, { h = 260, fmt = compact, stack = false } = {}) {
  const W = 760, H = h, padL = 54, padR = 16, padT = 14, padB = 28; const xs = series[0].data.map(p => p.d); const n = xs.length;
  const tops = xs.map((_, i) => stack ? sum(series.map(s => s.data[i].v)) : Math.max(...series.map(s => s.data[i].v)));
  const tks = ticks(Math.max(...tops)); const top = tks[tks.length - 1];
  const slot = (W - padL - padR) / n; const x = i => padL + slot * (i + .5); const y = v => padT + (H - padT - padB) * (1 - v / top);
  CHARTS[id] = { xs, series, fmt, x: xs.map((_, i) => x(i)), W, padL, padR };
  const grid = tks.map(t => `<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"/><text class="tick" x="${padL - 8}" y="${(y(t) + 3.5).toFixed(1)}" text-anchor="end">${fmt(t)}</text>`).join('');
  const step = Math.max(1, Math.round(n / 6)); const xt = xs.map((d, i) => (i % step === 0 || i === n - 1) ? `<text class="tick" x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${d.length > 7 ? d.slice(5) : d}</text>` : '').join('');
  const bw = stack ? Math.max(3, slot * .62) : Math.max(2, (slot * .72 - 2 * (series.length - 1)) / series.length);
  const bars = xs.map((_, i) => { let acc = 0; return series.map((s, si) => { const v = s.data[i].v; const bx = stack ? x(i) - bw / 2 : x(i) - (bw * series.length + 2 * (series.length - 1)) / 2 + si * (bw + 2); const y1 = y(acc + v), y0 = y(acc); acc += stack ? v : 0; return `<rect class="bar" x="${bx.toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0, y0 - y1 - (stack && si ? 2 : 0)).toFixed(1)}" fill="${s.color}" rx="${stack && si < series.length - 1 ? 0 : 3}"/>`; }).join(''); }).join('');
  const legend = series.length > 1 ? `<div class="legend">${series.map(s => `<span class="k"><i class="sw rect" style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  return `<div class="chart-wrap" data-chart="${id}"><svg class="chart" viewBox="0 0 ${W + 40} ${H}">${grid}<line class="axis" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>${xt}${bars}<line class="xline hidden" y1="${padT}" y2="${y(0)}"/><rect class="hit" x="${padL}" y="0" width="${W - padL - padR}" height="${H}"/></svg>${legend}</div>`;
}
const cumulate = series => series.map(s => { let a = 0; return { ...s, data: s.data.map(p => ({ d: p.d, v: (a += p.v) })) }; });
function evChart(series, fmt) {
  const t = S.ev.chart; if (!series.length) return '<div class="empty">請至少加入一個指標</div>';
  if (S.ev.gran === 'total' || t === 'bar') return barChart('ev-main', series, { h: 300, fmt });
  if (t === 'stack') return barChart('ev-main', series.filter(s => !s.prev), { h: 300, fmt, stack: true });
  if (t === 'cum') return lineChart('ev-main', cumulate(series), { h: 300, fmt, area: series.length === 1 });
  return lineChart('ev-main', series, { h: 300, fmt, area: series.length === 1 });
}
const dateRangeText = () => `${dkey(dayOffset(-S.range + 1))} ～ ${dkey(TODAY)}`;
function evSummary(series) {
  const cur = series.filter(s => !s.prev);
  return `<div class="grid c4 kpi">${cur.slice(0, 4).map(s => { const vals = s.data.map(p => p.v); const tot = ['avg_per_user', 'avg', 'max', 'min', 'p50', 'p90'].includes(s.m.agg) ? sum(vals) / vals.length : sum(vals); const prev = series.find(p => p.prev && p.m === s.m); const pv = prev ? (['avg_per_user', 'avg', 'max', 'min', 'p50', 'p90'].includes(s.m.agg) ? sum(prev.data.map(p => p.v)) / prev.data.length : sum(prev.data.map(p => p.v))) : null; const d = pv ? (tot - pv) / pv : null; const f = metricFmt(s.m); return `<div class="tile"><div class="lbl"><i class="sw" style="background:${s.color}"></i>${esc(s.name)}</div><div class="val">${f(tot)}</div><div class="delta ${d == null ? '' : d >= 0 ? 'up' : 'down'}">${d == null ? `<span>${S.ev.gran === 'total' ? '合計' : '區間合計'} · ${s.data.length} 個時間點</span>` : `<b>${d >= 0 ? '▲' : '▼'} ${pct(Math.abs(d))}</b><span>vs 上期 ${f(pv)}</span>`}</div></div>`; }).join('')}</div>`;
}
function evTable(series, fmt) {
  const xs = series[0].data.map(p => p.d); const usersCol = s => ['users', 'uniq'].includes(s.m.agg);
  const tot = s => ['avg_per_user', 'avg', 'max', 'min', 'p50', 'p90'].includes(s.m.agg) ? sum(s.data.map(p => p.v)) / s.data.length : sum(s.data.map(p => p.v));
  const rows = xs.map((d, i) => `<tr><td class="mono">${esc(d)}</td>${series.map(s => `<td class="num mono">${usersCol(s) && !s.prev ? `<button class="lnk" data-act="evusers" data-arg="${esc(s.name)}｜${esc(d)}" title="查看這格的用戶列表">${metricFmt(s.m)(s.data[i].v)}</button>` : metricFmt(s.m)(s.data[i].v)}</td>`).join('')}</tr>`);
  return `<div class="tbl-wrap"><table class="tbl"><tr><th>時間</th>${series.map(s => `<th class="num"><i class="sw" style="background:${s.color}"></i> ${esc(s.name)}</th>`).join('')}</tr><tr class="total"><td>合計 / 均值</td>${series.map(s => `<td class="num mono">${metricFmt(s.m)(tot(s))}</td>`).join('')}</tr>${(S.ev.tableAll ? rows : rows.slice().reverse().slice(0, 12)).join('')}</table></div>${rows.length > 12 ? `<div class="pager"><span>${S.ev.tableAll ? `全部 ${rows.length} 列` : `最近 12 列 / 共 ${rows.length}`}</span><span class="right"><button class="btn-ghost sm" data-act="evtableall">${S.ev.tableAll ? '只看最近 12 列' : '展開全部'}</button><button class="btn-ghost sm" data-act="toast" data-arg="${has('events', 'e') ? '已匯出 CSV（審計：export.events）' : '你的角色沒有匯出權限'}">匯出 CSV</button></span></div>` : ''}`;
}
function pageEvents() {
  const ev = S.ev; const series = evSeries(); const fmt = series.length ? metricFmt(series[0].m) : compact;
  const gText = ev.groups.map(g => propOf(g).cn).join('、');
  const cfgOpen = !S.qcfgCollapsed;
  const metricRow = (m, i) => { const agg = AGGS.find(a => a[0] === m.agg); const needProp = !!agg[2]; return `<div class="qm"><div class="qm-hd"><span class="qm-idx" style="background:${SER[i % 8]}">${String.fromCharCode(65 + i)}</span>
      <select class="sel grow" data-act="evmev" data-arg="${i}">${LIVE_EVENTS.map(e => `<option value="${e.name}" ${e.name === m.ev ? 'selected' : ''}>${esc(e.cn)}（${e.name}）</option>`).join('')}</select>
      <select class="sel" data-act="evmagg" data-arg="${i}">${AGGS.map(a => `<option value="${a[0]}" ${a[0] === m.agg ? 'selected' : ''}>${a[1]}</option>`).join('')}</select>
      ${needProp ? `<select class="sel" data-act="evmprop" data-arg="${i}">${(agg[2] === 'num' ? NUM_PROPS : PROPS).map(p => `<option value="${p.k}" ${p.k === m.prop ? 'selected' : ''}>${esc(p.cn)}</option>`).join('')}</select>` : ''}
      <button class="btn-ghost sm ${m.open ? 'on' : ''}" data-act="evmfilter" data-arg="${i}" title="指標篩選">篩選${m.filters.items.length ? ` ${m.filters.items.length}` : ''}</button>
      ${ev.metrics.length > 1 ? `<button class="icon-btn x sm" data-act="evmdel" data-arg="${i}" title="移除指標">✕</button>` : ''}</div>
      ${m.open ? filterBuilder(`ev.m${i}`, { title: `指標 ${String.fromCharCode(65 + i)} 的篩選`, compact: true }) : (m.filters.items.length ? `<div class="qm-ft note">${esc(filterText(m.filters))}</div>` : '')}</div>`; };
  const cfg = `<aside class="qpanel card ${cfgOpen ? '' : 'collapsed'}"><div class="card-h"><h3>查詢條件</h3><span class="sub">事件分析</span><div class="right"><button class="btn-ghost sm mob-only" data-act="qcfg">${cfgOpen ? '收合' : '展開'}</button></div></div><div class="qbody stack" style="gap:14px">
    <div class="qsec"><div class="qsec-h"><b>指標</b><span class="note">事件 × 指標類型（總次數／觸發用戶數／人均次數／去重數／數值屬性求和、均值、分位）</span></div>${ev.metrics.map(metricRow).join('')}<div class="acts"><button class="btn-ghost sm" data-act="evmadd" ${ev.metrics.length >= 5 ? 'disabled' : ''}>＋ 添加指標</button><button class="btn-ghost sm" data-act="toast" data-arg="原型：公式指標（A / B、A − B）將於正式版提供">＋ 公式指標</button></div></div>
    <div class="qsec">${filterBuilder('ev', { title: '全局篩選（作用於所有指標）' })}</div>
    <div class="qsec"><div class="qsec-h"><b>按分組查看</b><span class="note">最多 3 個維度；數值屬性自動分桶</span></div><div class="chips">${ev.groups.map(g => `<span class="chip">${esc(propOf(g).cn)}<button data-act="evgdel" data-arg="${g}">✕</button></span>`).join('')}${ev.groups.length < 3 ? `<select class="sel" data-act="evgadd"><option value="">＋ 添加分組維度</option>${PROP_SCOPES.map(sc => `<optgroup label="${sc[1]}">${PROPS.filter(p => p.scope === sc[0] && !ev.groups.includes(p.k)).map(p => `<option value="${p.k}">${esc(p.cn)}</option>`).join('')}</optgroup>`).join('')}</select>` : ''}</div>${ev.groups.length ? `<div class="row note" style="margin-top:8px">顯示前 <select class="sel xs" data-act="evtopn">${[5, 10, 20].map(n => `<option ${ev.topN === n ? 'selected' : ''}>${n}</option>`).join('')}</select> 組，其餘併入「其他」</div>` : ''}</div>
    <div class="qsec"><div class="qsec-h"><b>時間</b></div><div class="row"><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div><button class="btn-ghost sm" data-act="toast" data-arg="原型：自訂日期區間／相對時間（本週、上月）將於正式版提供">自訂</button></div><div class="note mono" style="margin-top:6px">${dateRangeText()}</div>
      <div class="row" style="margin-top:10px"><span class="note">粒度</span><div class="seg">${GRANS.map(g => `<button class="${ev.gran === g[0] ? 'on' : ''}" data-act="evgran" data-arg="${g[0]}">${g[1]}</button>`).join('')}</div></div>
      <label class="row chk-row" style="margin-top:10px"><button class="chk ${ev.compare ? 'on' : ''}" data-act="evcompare" aria-label="對比上期"></button><span>對比上一期（前 ${S.range} 天）</span></label></div>
    <div class="acts qacts"><button class="btn-primary" data-act="evrun">查詢</button><button class="btn-ghost" data-act="evsave">儲存報表</button><button class="btn-ghost" data-act="evreset">重置</button></div>
    </div></aside>`;
  const sqlHint = `SELECT ${ev.gran === 'total' ? '' : `toStartOf${ev.gran === 'day' ? 'Day' : ev.gran === 'week' ? 'Week' : ev.gran === 'month' ? 'Month' : 'Hour'}(time) AS t, `}${ev.groups.map(g => g.replace('u.', 'profile.').replace('tag.', 'tags.')).concat(ev.metrics.map(m => ({ total: 'count()', users: 'uniqExact(one_id)', avg_per_user: 'count()/uniqExact(one_id)', uniq: `uniqExact(props.${m.prop})` }[m.agg] || `${m.agg}(props.${m.prop})`) + ` AS m_${m.ev}`)).join(', ')}\nFROM dmp.events WHERE project='sinopac_demo' AND event IN (${[...new Set(ev.metrics.map(m => `'${m.ev}'`))].join(', ')})\n  AND event_date BETWEEN today()-${S.range} AND today()${ev.filters.items.length ? `\n  AND (${ev.filters.items.map(c => `${c.k.replace('u.', 'profile.').replace('tag.', 'tags.')} ${c.op === 'eq' ? '=' : c.op === 'neq' ? '!=' : c.op === 'gt' ? '>' : c.op === 'lt' ? '<' : c.op.toUpperCase()} '${c.v}'`).join(ev.filters.logic === 'and' ? ' AND ' : ' OR ')})` : ''}${S.user.scope === 'all' ? '' : "\n  AND branch_id IN ('TPE01')  -- 行級規則"}\nGROUP BY ${ev.gran === 'total' ? '' : 't'}${ev.groups.length ? (ev.gran === 'total' ? '' : ', ') + ev.groups.join(', ') : ''}${ev.gran === 'total' && !ev.groups.length ? '()' : ''} ORDER BY t`;
  S._evSql = sqlHint;
  const res = `<section class="qresult stack">
    ${evSummary(series)}
    <div class="card"><div class="card-h"><h3>${esc(ev.metrics.map(metricLabel).join('、'))}</h3><span class="sub">${dateRangeText()} · 按${GRANS.find(g => g[0] === ev.gran)[1]}${gText ? ` · 按 ${gText} 分組` : ''}${ev.filters.items.length ? ` · 篩選：${esc(filterText(ev.filters))}` : ''}</span>
      <div class="right"><div class="seg">${CHART_TYPES.map(c => `<button class="${ev.chart === c[0] ? 'on' : ''}" data-act="evchart" data-arg="${c[0]}" ${c[0] === 'stack' && series.length < 2 ? 'disabled' : ''}>${c[1]}</button>`).join('')}</div></div></div>
      ${ev.chart === 'table' ? evTable(series, fmt) : evChart(series, fmt)}
      ${ev.chart !== 'table' ? `<div class="note" style="margin-top:8px">滑過圖表查看每個時間點的數值；${ev.compare ? '虛線／淡色為上一期。' : ''}掃描估算 <b class="mono">${(S.range * 0.21 * Math.max(1, ev.metrics.length * .6)).toFixed(1)} GB</b> · 行級規則：${S.user.scope === 'all' ? '無' : 'branch_id IN (TPE01)'} · 查詢耗時 <b class="mono">${(0.3 + ev.metrics.length * .18 + ev.groups.length * .25).toFixed(2)} s</b></div>` : ''}</div>
    ${ev.chart !== 'table' ? `<div class="card"><div class="card-h"><h3>數據表</h3><span class="sub">點擊「觸發用戶數」的數字可下鑽到用戶列表</span></div>${evTable(series, fmt)}</div>` : ''}
    <div class="grid c21"><div class="card"><div class="card-h"><h3>已儲存報表</h3><span class="sub">書籤 · 可排程寄送／訂閱異常告警</span></div><div class="tbl-wrap"><table class="tbl">${SAVED_REPORTS.filter(r => r.kind === 'events').map(r => `<tr class="click"><td><b>${esc(r.name)}</b><span class="sub">${esc(r.owner)} · 更新 ${r.updated}</span></td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="已載入報表「${esc(r.name)}」（原型）">開啟</button></td></tr>`).join('')}${S.ev.saved.map(r => `<tr class="click"><td><b>${esc(r)}</b><span class="sub">${esc(S.user.name)} · 剛剛</span></td><td class="num"><span class="pill teal">新</span></td></tr>`).join('')}</table></div></div>
    <div class="card"><div class="card-h"><h3>相關洞察</h3><span class="sub">agent 以同一組指標口徑自動偵測</span></div><div class="stack" style="gap:10px">${INSIGHTS.filter(i => i.kind === 'anomaly' || i.kind === 'trend').slice(0, 2).map(i => insightCard(i, true)).join('') || '<div class="empty">暫無</div>'}</div></div></div>
  </section>`;
  return `<div class="page-h"><div><h1>事件分析</h1><p>多指標 × 指標類型 × 篩選（且／或）× 分組 × 時間粒度；指標口徑來自語意層，與 AI agent 的 metric.query 走同一個查詢編譯器。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="metric">${svg(I.code, 14)} 檢視 SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="解讀「${esc(series[0] ? series[0].name : '')}」近 ${S.range} 天的變化${gText ? `（按 ${gText} 分組）` : ''}，有什麼值得注意？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="qlayout">${cfg}${res}</div>`;
}

/* ------------------------------------------------------------------ 漏斗分析 */
const FUNNEL_GROUPS = [['none', '不分組'], ['platform', '平台'], ['channel', '渠道'], ['u.tier', '價值分級'], ['u.age_band', '年齡層'], ['segment', '人群分群']];
const WINDOW_UNITS = ['分鐘', '小時', '天'];
function funnelTemplate(id) { const t = FUNNELS.find(f => f.id === id); const w = t.window === '當日' ? { n: 1, unit: '天' } : t.window === '7 天' ? { n: 7, unit: '天' } : { n: 30, unit: '天' }; return { id, name: t.name, steps: t.steps.map(s => ({ ev: s.ev, cn: s.cn, filters: newFilter(), open: false })), window: w }; }
function funnelCounts(groupVal = null) {                                          // 每步人數（本期／上期）
  const f = S.funnel; const t = FUNNELS.find(x => x.id === f.id); const sameAsTemplate = t && t.steps.length === f.steps.length && t.steps.every((s, i) => s.ev === f.steps[i].ev);
  const rangeF = S.range / 30; const gF = filterFactor(f.filters); const win = f.window.unit === '天' ? f.window.n : f.window.unit === '小時' ? f.window.n / 24 : f.window.n / 1440;
  const winF = i => i === 0 ? 1 : Math.min(1, .55 + .45 * Math.log10(1 + win * 3) / Math.log10(91));
  const gw = groupVal == null ? 1 : hf(f.group + groupVal, .12, .5);
  const n = [], prev = [], nb = [], pb = []; let cumF = 1, cumD = 1;
  f.steps.forEach((s, i) => {
    let base, basePrev;
    if (sameAsTemplate) { base = t.steps[i].n * rangeF; basePrev = t.steps[i].prev * rangeF; }
    else if (i === 0) { const e = evOf(s.ev); base = Math.min(e.daily * .3, 412000 * hf(e.name + ':u', .25, .95)) * S.range * .55; basePrev = base * hf(s.ev + 'prev', .88, 1.08); }
    else { const conv = hf(f.steps[i - 1].ev + s.ev, .22, .78); base = nb[i - 1] * conv; basePrev = pb[i - 1] * conv * hf(s.ev + 'p' + i, .92, 1.06); }
    nb.push(base); pb.push(basePrev);
    cumF *= filterFactor(s.filters); if (groupVal != null && i) cumD *= hf(groupVal + i, .86, 1.06);
    n.push(base * gF * cumF * winF(i) * gw * cumD); prev.push(basePrev * gF * cumF * winF(i) * gw * cumD);
    if (i) { n[i] = Math.min(n[i], n[i - 1] * .985); prev[i] = Math.min(prev[i], prev[i - 1] * .985); }
  });
  return { n: n.map(Math.round), prev: prev.map(Math.round) };
}
function funnelGroupValues() { const f = S.funnel; if (f.group === 'none') return []; if (f.group === 'segment') return SEGMENTS.slice(0, 4).map(s => s.name); return propOf(f.group).vals.slice(0, 5); }
const medianTime = (i, g = '') => { const f = S.funnel; const h = hf(f.steps[i].ev + g + i, .2, 1) * (f.window.unit === '天' ? f.window.n * 24 * .18 : f.window.unit === '小時' ? f.window.n * .4 : f.window.n / 60 * .4); return h < 1 ? `${Math.max(1, Math.round(h * 60))} 分` : h < 48 ? `${h.toFixed(1)} 小時` : `${(h / 24).toFixed(1)} 天`; };
function funnelGroupView(groups) {
  const f = S.funnel; const top = Math.max(...groups.map(g => g.n[0]));
  return `<div class="funnel grp">${f.steps.map((s, i) => `<div class="fstep ${f.sel === i ? 'sel' : ''}" data-act="fnstep" data-arg="${i}"><div class="nm">${i + 1}. ${esc(s.cn)}<small class="mono">${esc(s.ev)}</small></div><div class="trk multi">${groups.map((g, gi) => `<div class="fill" style="width:${(g.n[i] / top * 100).toFixed(1)}%;background:${g.color}" title="${esc(g.name)}：${n0(g.n[i])}"></div>`).join('')}</div><div class="v"><small>${groups.map(g => `<span><i class="sw rect" style="background:${g.color}"></i>${i ? pct(g.n[i] / g.n[i - 1]) : n0(g.n[0])}</span>`).join('')}</small></div></div>`).join('')}</div><div class="legend">${groups.map(g => `<span class="k"><i class="sw rect" style="background:${g.color}"></i>${esc(g.name)}</span>`).join('')}</div>`;
}
function pageFunnel() {
  const f = S.funnel; const cur = funnelCounts(); const gvals = funnelGroupValues();
  const groups = gvals.map((v, i) => ({ name: v, color: SER[i], ...funnelCounts(v) }));
  const first = cur.n[0], lastN = cur.n[cur.n.length - 1]; const convAll = lastN / first; const prevConv = cur.prev[cur.prev.length - 1] / cur.prev[0];
  const sel = Math.min(f.sel, f.steps.length - 2); const cfgOpen = !S.qcfgCollapsed;
  const stepRow = (s, i) => `<div class="qm step"><div class="qm-hd"><span class="qm-idx">${i + 1}</span><select class="sel grow" data-act="fnev" data-arg="${i}">${LIVE_EVENTS.map(e => `<option value="${e.name}" ${e.name === s.ev ? 'selected' : ''}>${esc(e.cn)}（${e.name}）</option>`).join('')}</select>
      <button class="btn-ghost sm ${s.open ? 'on' : ''}" data-act="fnsfilter" data-arg="${i}">篩選${s.filters.items.length ? ` ${s.filters.items.length}` : ''}</button>
      <button class="icon-btn sm" data-act="fnup" data-arg="${i}" title="上移" ${i === 0 ? 'disabled' : ''}>↑</button>${f.steps.length > 2 ? `<button class="icon-btn x sm" data-act="fnsdel" data-arg="${i}" title="移除步驟">✕</button>` : ''}</div>
      ${s.open ? filterBuilder(`fn.s${i}`, { title: `步驟 ${i + 1} 的事件篩選`, compact: true }) : (s.filters.items.length ? `<div class="qm-ft note">${esc(filterText(s.filters))}</div>` : '')}</div>`;
  const cfg = `<aside class="qpanel card ${cfgOpen ? '' : 'collapsed'}"><div class="card-h"><h3>漏斗設定</h3><span class="sub">${esc(f.name)}</span><div class="right"><button class="btn-ghost sm mob-only" data-act="qcfg">${cfgOpen ? '收合' : '展開'}</button></div></div><div class="qbody stack" style="gap:14px">
    <div class="qsec"><div class="qsec-h"><b>範本</b></div><div class="seg wrap">${FUNNELS.map(x => `<button class="${x.id === f.id ? 'on' : ''}" data-act="funnelid" data-arg="${x.id}">${esc(x.name)}</button>`).join('')}</div></div>
    <div class="qsec"><div class="qsec-h"><b>漏斗步驟</b><span class="note">依序完成；每步可加事件屬性篩選（例：頁面群組 = account_open）</span></div>${f.steps.map(stepRow).join('')}<div class="acts"><button class="btn-ghost sm" data-act="fnsadd" ${f.steps.length >= 8 ? 'disabled' : ''}>＋ 添加步驟</button></div></div>
    <div class="qsec"><div class="qsec-h"><b>窗口期</b><span class="note">完成全部步驟的最長時間（1 分鐘～3650 天）</span></div><div class="row"><input class="inp num" type="number" min="1" max="3650" data-act="fnwin" value="${f.window.n}"><select class="sel" data-act="fnwinu">${WINDOW_UNITS.map(u => `<option ${u === f.window.unit ? 'selected' : ''}>${u}</option>`).join('')}</select><span class="note">內依序完成</span></div></div>
    <div class="qsec"><div class="qsec-h"><b>關聯屬性</b><span class="note">各步驟須為同一個屬性值（例：同一商品代碼）</span></div><select class="sel" data-act="fnassoc"><option value="">不設定</option>${PROPS.filter(p => p.scope === 'event' && p.vals).map(p => `<option value="${p.k}" ${f.assoc === p.k ? 'selected' : ''}>${esc(p.cn)}（${p.k}）</option>`).join('')}</select></div>
    <div class="qsec">${filterBuilder('fn', { title: '全局篩選（用戶屬性／標籤／事件屬性）' })}</div>
    <div class="qsec"><div class="qsec-h"><b>對比</b></div><div class="row"><span class="note">分組</span><select class="sel" data-act="fngroup">${FUNNEL_GROUPS.map(g => `<option value="${g[0]}" ${f.group === g[0] ? 'selected' : ''}>${g[1]}</option>`).join('')}</select></div>
      <label class="row chk-row" style="margin-top:8px"><button class="chk ${f.compare ? 'on' : ''}" data-act="funnelcmp" aria-label="對比上期"></button><span>對比上一期（前 ${S.range} 天）</span></label>
      <div class="row" style="margin-top:10px"><span class="note">時間</span><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div></div></div>
    <div class="acts qacts"><button class="btn-primary" data-act="fnrun">查詢</button><button class="btn-ghost" data-act="fnsave">儲存漏斗</button><button class="btn-ghost" data-act="fnreset">重置</button></div></div></aside>`;
  const tiles = `<div class="grid c4 kpi"><div class="tile"><div class="lbl">總轉化率</div><div class="val">${pct(convAll)}</div><div class="delta ${convAll - prevConv >= 0 ? 'up' : 'down'}">${f.compare ? `<b>${pt(convAll - prevConv)}</b><span>vs 上期 ${pct(prevConv)}</span>` : `<span>${f.steps[0].cn} → ${f.steps[f.steps.length - 1].cn}</span>`}</div></div>
    <div class="tile"><div class="lbl">轉化用戶</div><div class="val">${compact(lastN)}</div><div class="delta"><span>完成全部 ${f.steps.length} 步</span></div></div>
    <div class="tile"><div class="lbl">流失用戶</div><div class="val">${compact(first - lastN)}</div><div class="delta"><span>最大流失：第 ${(() => { let w = 1, m = 0; for (let i = 1; i < cur.n.length; i++) { const r = 1 - cur.n[i] / cur.n[i - 1]; if (r > m) { m = r; w = i; } } return w; })()} 步</span></div></div>
    <div class="tile"><div class="lbl">中位轉化時長</div><div class="val" style="font-size:22px">${medianTime(f.steps.length - 1, 'all')}</div><div class="delta"><span>窗口期 ${f.window.n} ${f.window.unit}${f.assoc ? ` · 關聯 ${propOf(f.assoc).cn}` : ''}</span></div></div></div>`;
  const stepDetail = (() => { const i = sel; const a = cur.n[i], b = cur.n[i + 1]; const conv = b / a; const pconv = cur.prev[i + 1] / cur.prev[i]; return `<div class="card"><div class="card-h"><h3>步驟 ${i + 1} → ${i + 2}：${esc(f.steps[i].cn)} → ${esc(f.steps[i + 1].cn)}</h3><span class="sub">點選左側漏斗的步驟切換</span></div>
    <dl class="kv"><dt>轉化率</dt><dd><b style="font-size:16px">${pct(conv)}</b>${f.compare ? ` <span class="note">上期 ${pct(pconv)}（${pt(conv - pconv)}）</span>` : ''}</dd><dt>轉化用戶</dt><dd class="mono">${n0(b)}</dd><dt>流失用戶</dt><dd class="mono" style="color:var(--st-serious)">${n0(a - b)}</dd><dt>中位轉化時長</dt><dd>${medianTime(i + 1, 'step')}</dd>${gvals.length ? `<dt>各組轉化率</dt><dd>${groups.map(g => `<span class="pill" style="margin:0 6px 4px 0"><i class="sw rect" style="background:${g.color}"></i>${esc(g.name)} ${pct(g.n[i + 1] / g.n[i])}</span>`).join('')}</dd>` : ''}</dl>
    <div class="acts" style="margin-top:12px"><button class="btn-ghost sm" data-act="fnusers" data-arg="lost:${i}">查看流失用戶（${compact(a - b)}）</button><button class="btn-ghost sm" data-act="fnusers" data-arg="conv:${i}">查看轉化用戶</button><button class="btn-ghost sm" data-act="goto" data-arg="#/path">流失用戶的後續路徑</button><button class="btn-ghost sm" data-act="toast" data-arg="已建立分群草稿：漏斗「${esc(f.name)}」步驟 ${i + 1} 流失用戶（需審批）">存為分群</button></div></div>`; })();
  const trendSeries = (gvals.length ? groups : [{ name: '總轉化率', color: SER[0], n: cur.n }]).map(g => { const base = g.n[g.n.length - 1] / g.n[0]; return { name: g.name, color: g.color, data: Array.from({ length: S.range }, (_, j) => { const i = 90 - S.range + j; return { d: dkey(dayOffset(j - S.range + 1)), v: Math.round(base * (1 + (shapeAt(i) - 1) * .35) * (1 + Math.sin(i * .7 + hash(g.name) % 5) * .06) * 10000) / 100 }; }) }; });
  const table = `<div class="tbl-wrap"><table class="tbl"><tr><th>${gvals.length ? FUNNEL_GROUPS.find(g => g[0] === f.group)[1] : '期間'}</th>${f.steps.map((s, i) => `<th class="num">${i + 1}. ${esc(s.cn)}</th>`).join('')}<th class="num">總轉化</th><th class="num">中位時長</th></tr>${(gvals.length ? groups.map(g => ({ name: g.name, color: g.color, n: g.n })) : [{ name: '本期', n: cur.n }, ...(f.compare ? [{ name: '上期', n: cur.prev }] : [])]).map(r => `<tr><td style="font-weight:600">${r.color ? `<i class="sw rect" style="background:${r.color}"></i> ` : ''}${esc(r.name)}</td>${r.n.map((v, i) => `<td class="num mono">${n0(v)}${i ? `<span class="sub">${pct(v / r.n[i - 1])}</span>` : ''}</td>`).join('')}<td class="num mono" style="color:${r.n[r.n.length - 1] / r.n[0] < convAll * .8 ? 'var(--st-serious)' : 'inherit'}">${pct(r.n[r.n.length - 1] / r.n[0])}</td><td class="num">${medianTime(f.steps.length - 1, r.name)}</td></tr>`).join('')}</table></div>`;
  const res = `<section class="qresult stack">${tiles}
    <div class="card"><div class="card-h"><h3>${esc(f.name)}</h3><span class="sub">${dateRangeText()} · 窗口 ${f.window.n} ${f.window.unit}${f.filters.items.length ? ` · 篩選：${esc(filterText(f.filters))}` : ''}${gvals.length ? ` · 按${FUNNEL_GROUPS.find(g => g[0] === f.group)[1]}對比` : ''}</span><div class="right"><div class="seg">${[['funnel', '漏斗圖'], ['trend', '轉化趨勢'], ['table', '數據表']].map(v => `<button class="${f.view === v[0] ? 'on' : ''}" data-act="fnview" data-arg="${v[0]}">${v[1]}</button>`).join('')}</div></div></div>
      ${f.view === 'trend' ? lineChart('fn-trend', trendSeries, { h: 280, fmt: v => v.toFixed(1) + '%', area: trendSeries.length === 1 }) + '<div class="note" style="margin-top:8px">每日完成首步的用戶，在窗口期內走完全部步驟的比例（依首步日期歸屬）。</div>'
        : f.view === 'table' ? table
        : gvals.length ? funnelGroupView(groups) : funnelView({ steps: f.steps.map((s, i) => ({ ev: s.ev, cn: s.cn, n: cur.n[i], prev: cur.prev[i] })) }, { compare: f.compare, sel, selectable: true })}
      ${f.view === 'funnel' ? `<div class="note" style="margin-top:8px">以 ClickHouse windowFunnel(${f.window.unit === '天' ? f.window.n * 86400 : f.window.unit === '小時' ? f.window.n * 3600 : f.window.n * 60})(strict_order) 計算；${f.assoc ? `關聯屬性 ${f.assoc} 以 arrayJoin + 相等條件實作；` : ''}步驟可混用 GA 行為事件與交易事件。</div>` : ''}</div>
    <div class="grid c21">${stepDetail}<div class="card"><div class="card-h"><h3>相關洞察</h3></div><div class="stack" style="gap:10px">${INSIGHTS.filter(i => i.kind === 'funnel' || i.kind === 'opportunity').slice(0, 2).map(i => insightCard(i, true)).join('')}</div></div></div>
    ${f.view !== 'table' ? `<div class="card"><div class="card-h"><h3>數據表</h3><span class="sub">各步驟人數與逐步轉化率</span></div>${table}</div>` : ''}
    <div class="card"><div class="card-h"><h3>已儲存漏斗</h3></div><div class="tbl-wrap"><table class="tbl">${SAVED_REPORTS.filter(r => r.kind === 'funnel').map(r => `<tr class="click"><td><b>${esc(r.name)}</b><span class="sub">${esc(r.owner)} · 更新 ${r.updated}</span></td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="已載入漏斗「${esc(r.name)}」（原型）">開啟</button></td></tr>`).join('')}${f.saved.map(r => `<tr class="click"><td><b>${esc(r)}</b><span class="sub">${esc(S.user.name)} · 剛剛</span></td><td class="num"><span class="pill teal">新</span></td></tr>`).join('')}</table></div></div>
  </section>`;
  S._fnSql = `-- funnel.run: ${f.id}（windowFunnel）\nSELECT level, count() AS users FROM (\n  SELECT one_id, windowFunnel(${f.window.unit === '天' ? f.window.n * 86400 : f.window.unit === '小時' ? f.window.n * 3600 : f.window.n * 60}, 'strict_order')(time,\n${f.steps.map((s, i) => `    event='${s.ev}'${s.filters.items.map(c => ` AND ${c.k} ${c.op === 'eq' ? '=' : c.op === 'neq' ? '!=' : c.op === 'gt' ? '>' : c.op === 'lt' ? '<' : c.op.toUpperCase()} '${c.v}'`).join('')}${i < f.steps.length - 1 ? ',' : ') AS level'}`).join('\n')}\n  FROM dmp.events WHERE event_date >= today()-${S.range + Math.ceil(f.window.unit === '天' ? f.window.n : 1)}${f.filters.items.length ? `\n    AND (${f.filters.items.map(c => `${c.k.replace('u.', 'profile.').replace('tag.', 'tags.')} ${c.op === 'eq' ? '=' : c.op.toUpperCase()} '${c.v}'`).join(f.filters.logic === 'and' ? ' AND ' : ' OR ')})` : ''}\n  GROUP BY one_id${f.group !== 'none' ? `, ${f.group}` : ''})\nGROUP BY level${f.group !== 'none' ? `, ${f.group}` : ''} ORDER BY level;`;
  return `<div class="page-h"><div><h1>漏斗分析</h1><p>步驟＋每步篩選、窗口期、關聯屬性、全局篩選、分組對比與轉化趨勢；點選步驟可看轉化／流失明細並下鑽到用戶列表。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="funnel">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="診斷「${esc(f.name)}」漏斗：哪一步流失最大、哪個分群最差、可能原因？">${svg(I.spark, 14)} 請 agent 診斷</button></div></div>
  <div class="qlayout">${cfg}${res}</div>`;
}

/* ================================================================== v0.4.1：行為路徑／歸因／留存（對標神策「用戶路徑」「歸因分析」「留存分析」、DataFinder「路徑分析」「歸因分析」「留存分析」） */
const PATH_EVENT_POOL = ['hq_stockdetail_view', 'bond_order', 'bond_deal', 'order_rejected', 'order_cancelled', 'deal_opt_mani', 'search_submit', '$AppViewScreen', '$pageview', 'app_login_success', 'fund_deposit', 'push_click', 'act_click', 'sip_purchase', 'kh_entry_khpg_pv'];
const PATH_SPLIT = { '$AppViewScreen': ['$screen_name', ['新聞', '庫存', '帳務', '設定']], '$pageview': ['page_group', ['quote', 'wealth', 'campaign', 'account_open']] };
// 事件間的「親和度」：讓桑基圖的走向合理（看盤→委託、委託→成交／失敗…），其餘由雜湊補足
const AFFINITY = { 'hq_stockdetail_view>bond_order': 4, 'hq_stockdetail_view>deal_opt_mani': 1.6, 'hq_stockdetail_view>hq_stockdetail_view': 2.2, 'hq_stockdetail_view>$AppViewScreen': 1.2, 'bond_order>bond_deal': 6, 'bond_order>order_rejected': .9, 'bond_order>order_cancelled': 1.1, 'bond_order>hq_stockdetail_view': 2, 'bond_deal>hq_stockdetail_view': 3, 'bond_deal>$AppViewScreen': 2.2, 'order_rejected>bond_order': 3.5, 'order_rejected>hq_stockdetail_view': 1.2, 'login>hq_stockdetail_view': 5, 'login>$AppViewScreen': 1.5, 'push_click>hq_stockdetail_view': 3, 'push_click>act_click': 2, 'act_click>kh_entry_khpg_pv': 1.5, 'search_submit>hq_stockdetail_view': 5, 'deal_opt_mani>hq_stockdetail_view': 3, 'fund_deposit>bond_order': 2.5, 'kh_entry_khpg_pv>$pageview': 2 };
const EXIT_RATE = { hq_stockdetail_view: .12, bond_deal: .34, order_rejected: .3, order_cancelled: .28, '$AppViewScreen': .3, app_login_success: .08, push_click: .25, deal_opt_mani: .2, search_submit: .1, fund_deposit: .3, act_click: .45, '$pageview': .3, bond_order: .05, sip_purchase: .4, kh_entry_khpg_pv: .35 };
function resetPath(anchor = 'hq_stockdetail_view') { S.path = { dir: 'after', anchor, anchorFilters: newFilter(), events: ['hq_stockdetail_view', 'bond_order', 'bond_deal', 'order_rejected', 'deal_opt_mani', '$AppViewScreen', 'search_submit', 'app_login_success'], split: { '$AppViewScreen': true }, steps: 4, session: 30, merge: true, unit: 'users', sample: '1/10', filters: newFilter(), group: 'none', must: '', view: 'sankey', sel: null, saved: S.path ? S.path.saved : [] }; }
function resetAttr() { S.attr = { goal: 'bond_deal', goalMetric: 'users', goalProp: 'fee', goalFilters: newFilter(), touches: [
    { name: 'LINE 推播開啟', ev: 'push_click', filters: newFilter(), open: false }, { name: 'Google 搜尋', ev: '$pageview', filters: { logic: 'and', items: [{ k: '$utm_source', op: 'eq', v: 'google', v2: '' }] }, open: false },
    { name: '營業員接觸', ev: 'app_login_success', filters: { logic: 'and', items: [{ k: 'channel', op: 'eq', v: '營業員', v2: '' }] }, open: false }, { name: '官網 ETF 專區', ev: '$pageview', filters: { logic: 'and', items: [{ k: 'page_group', op: 'eq', v: 'wealth', v2: '' }] }, open: false },
    { name: 'Facebook 廣告', ev: 'act_click', filters: { logic: 'and', items: [{ k: '$utm_source', op: 'eq', v: 'facebook', v2: '' }] }, open: false }, { name: 'App 推薦卡片', ev: '$AppViewScreen', filters: newFilter(), open: false }],
  assocEv: '', assocProp: 'symbol', model: 'position', posWeights: '40/20/40', halfLife: 7, window: { n: 14, unit: '天' }, filters: newFilter(), group: 'none', view: 'chart', saved: S.attr ? S.attr.saved : [] }; }
function resetRet() { S.ret = { init: 'kh_review_pass', initFilters: newFilter(), ret: 'bond_deal', retAny: false, retFilters: newFilter(), gran: 'week', periods: 8, mode: 'retention', show: 'pct', calc: 'period', assoc: '', filters: newFilter(), group: 'none', view: 'table', sel: null, saved: S.ret ? S.ret.saved : [] }; }
resetPath(); resetAttr(); resetRet();

/* ------------------------------------------------------------------ 行為路徑 */
const evCn = n => { const [base, sub] = n.split(':'); const e = evOf(base); return sub ? `${e.cn}·${sub}` : e.cn; };
function pathExpand(ev) { const sp = PATH_SPLIT[ev]; return S.path.split[ev] && sp ? sp[1].map(v => `${ev}:${v}`) : [ev]; }
function pathGraph() {
  const P = S.path; const e0 = evOf(P.anchor); const rangeF = S.range / 30;
  const base = Math.min(e0.daily * .3, 412000 * hf(e0.name + ':u', .25, .95)) * S.range * (P.sample === '1/10' ? .1 : 1) * filterFactor(P.filters) * filterFactor(P.anchorFilters) * (P.group === 'none' ? 1 : hf(P.group, .2, .5)) * (P.unit === 'sessions' ? 1.9 : 1);
  const users = Math.round(base); const cands = P.events.flatMap(pathExpand);
  const cols = [[{ k: P.anchor, cn: e0.cn, n: users }]]; const links = [];
  for (let s = 1; s <= P.steps; s++) {
    const prev = cols[s - 1]; const agg = {}; const lk = [];
    prev.forEach((src, si) => {
      if (src.k === 'exit' || src.k === 'other') return;
      const sBase = src.k.split(':')[0]; const exitR = Math.min(.6, (EXIT_RATE[sBase] ?? .25) * (1 + .12 * (s - 1)) * (P.session < 30 ? 1.15 : P.session > 30 ? .9 : 1));
      const ws = cands.map(c => { const cBase = c.split(':')[0]; if (P.merge && c === src.k) return 0; const a = AFFINITY[`${sBase}>${cBase}`] || .5; return a * hf(src.k + '>' + c + s, .4, 1.3); });
      const tot = sum(ws); const flow = src.n * (1 - exitR);
      cands.forEach((c, ci) => { const v = Math.round(flow * ws[ci] / tot); if (v < 1) return; agg[c] = (agg[c] || 0) + v; lk.push([si, c, v]); });
      const ex = Math.round(src.n * exitR); agg.exit = (agg.exit || 0) + ex; lk.push([si, 'exit', ex]);
    });
    const ranked = Object.entries(agg).filter(([k]) => k !== 'exit').sort((a, b) => b[1] - a[1]); const keep = ranked.slice(0, 5).map(([k]) => k); const otherN = sum(ranked.slice(5).map(([, v]) => v));
    const col = keep.map(k => ({ k, cn: evCn(k), n: agg[k] })); if (otherN) col.push({ k: 'other', cn: '其他事件', n: otherN }); col.push({ k: 'exit', cn: P.unit === 'sessions' ? '會話結束' : '離開', n: agg.exit || 0 });
    const idx = Object.fromEntries(col.map((c, i) => [c.k, i])); const merged = {};
    lk.forEach(([si, k, v]) => { const di = idx[k] ?? idx.other; const key = `${si}:${di}`; merged[key] = (merged[key] || 0) + v; });
    Object.entries(merged).forEach(([key, v]) => { const [si, di] = key.split(':').map(Number); links.push([s - 1, si, s, di, v]); });
    cols.push(col);
  }
  return { users, nodes: cols, links };
}
function pathTop(G) {                                                              // 以連結比例展開 Top 路徑
  const out = []; const walk = (c, i, p, v, depth) => { const n = G.nodes[c][i]; const np = [...p, n.cn]; if (n.k === 'exit' || c === G.nodes.length - 1 || depth >= S.path.steps) { out.push({ p: np, n: Math.round(v) }); return; }
    const outs = G.links.filter(l => l[0] === c && l[1] === i); const tot = sum(outs.map(l => l[4])) || 1; outs.forEach(l => { const share = v * l[4] / tot; if (share >= G.users * .004) walk(l[2], l[3], np, share, depth + 1); }); };
  walk(0, 0, [], G.users, 0); return out.filter(r => !S.path.must || r.p.includes(evCn(S.path.must))).sort((a, b) => b.n - a.n).slice(0, 10).map(r => ({ ...r, t: (() => { const sec = Math.round(hf(r.p.join('>'), 25, 320) * r.p.length / 3); return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${String(sec % 60).padStart(2, '0')} 秒`; })(), conv: r.p.includes('成交') }));
}
function sankey(G) {
  const P = S.path; const W = 760, H = 380, nodeW = 14, gap = 10, padT = 26; const rev = P.dir === 'before';
  const cols = G.nodes.length; const colX = c => 20 + (rev ? cols - 1 - c : c) * (W - 40 - nodeW - 120) / (cols - 1);
  const maxTotal = Math.max(...G.nodes.map(col => col.reduce((a, n) => a + n.n, 0))); const scale = (H - padT - 10 - gap * 6) / maxTotal;
  const pos = G.nodes.map((col, c) => { let y = padT; return col.map(n => { const h = Math.max(2, n.n * scale); const o = { x: colX(c), y, h, n, outOff: 0, inOff: 0 }; y += h + gap; return o; }); });
  const kind = k => k === 'exit' ? 'exit' : k === 'other' ? 'other' : k.startsWith('order_') || k === 'fund_deposit' ? 'trade' : '';
  const selKey = P.sel; const isSel = (c, i) => selKey === `${c}:${i}`;
  const links = G.links.map(([c1, i1, c2, i2, v]) => { const s = pos[c1][i1], t = pos[c2][i2]; const h = v * scale; const sy = s.y + s.outOff + h / 2, ty = t.y + t.inOff + h / 2; s.outOff += h; t.inOff += h; const a = rev ? t : s, b = rev ? s : t; const ay = rev ? ty : sy, by = rev ? sy : ty; const x1 = a.x + nodeW, x2 = b.x; const mx = (x1 + x2) / 2; const hl = isSel(c1, i1) || isSel(c2, i2);
    return `<path class="link ${kind(t.n.k)} ${hl ? 'hl' : ''}" stroke-width="${h.toFixed(1)}" d="M${x1},${ay.toFixed(1)} C${mx},${ay.toFixed(1)} ${mx},${by.toFixed(1)} ${x2},${by.toFixed(1)}"><title>${esc(s.n.cn)} → ${esc(t.n.cn)}：${n0(v)}</title></path>`; }).join('');
  const nodes = pos.flatMap((col, c) => { let lastLy = -100; return col.map((o, i) => { const ly = Math.max(o.y + Math.min(o.h, 14) / 2 + 4, lastLy + 27); lastLy = ly; const sel = isSel(c, i);
    return `<g class="nd ${sel ? 'sel' : ''}" data-act="pathnode" data-arg="${c}:${i}"><rect class="node ${kind(o.n.k)}" x="${o.x}" y="${o.y.toFixed(1)}" width="${nodeW}" height="${o.h.toFixed(1)}" rx="2"><title>${esc(o.n.cn)}：${n0(o.n.n)}</title></rect><text x="${o.x + nodeW + 6}" y="${ly.toFixed(1)}">${esc(o.n.cn)}</text><text class="n" x="${o.x + nodeW + 6}" y="${(ly + 12).toFixed(1)}">${n0(o.n.n)} · ${pct(o.n.n / G.users, 0)}</text></g>`; }); }).join('');
  const heads = G.nodes.map((_, c) => `<text class="col" x="${colX(c)}" y="14">${c === 0 ? (rev ? '終點' : '起點') : rev ? `前 ${c} 步` : `第 ${c} 步`}</text>`).join('');
  return `<svg class="sankey" viewBox="0 0 ${W} ${H}">${heads}${links}${nodes}</svg>`;
}
function pagePath() {
  const P = S.path; const G = pathGraph(); const top = pathTop(G); const cfgOpen = !S.qcfgCollapsed; const anchorE = evOf(P.anchor);
  const convN = sum(G.nodes.slice(1).flatMap(col => col.filter(n => n.k === 'bond_deal').map(n => n.n))); const exitFirst = (G.nodes[1].find(n => n.k === 'exit') || { n: 0 }).n;
  const avgLen = (sum(G.nodes.map((col, c) => c * sum(col.filter(n => n.k === 'exit').map(n => n.n)))) + P.steps * sum(G.nodes[P.steps].filter(n => n.k !== 'exit').map(n => n.n))) / G.users;
  const selNode = P.sel ? (() => { const [c, i] = P.sel.split(':').map(Number); const n = G.nodes[c]?.[i]; if (!n) return null; const ins = G.links.filter(l => l[2] === c && l[3] === i).map(l => ({ cn: G.nodes[l[0]][l[1]].cn, v: l[4] })).sort((a, b) => b.v - a.v).slice(0, 3); const outs = G.links.filter(l => l[0] === c && l[1] === i).map(l => ({ cn: G.nodes[l[2]][l[3]].cn, v: l[4] })).sort((a, b) => b.v - a.v).slice(0, 3); return { c, i, n, ins, outs }; })() : null;
  const evRow = ev => { const e = evOf(ev); const on = P.events.includes(ev); const sp = PATH_SPLIT[ev]; return `<label class="ev-pick ${on ? 'on' : ''}"><button class="chk ${on ? 'on' : ''}" data-act="pathev" data-arg="${ev}" aria-label="${esc(e.cn)}"></button><span>${esc(e.cn)}<small class="mono">${ev}</small></span>${sp && on ? `<button class="btn-ghost sm ${P.split[ev] ? 'on' : ''}" data-act="pathsplit" data-arg="${ev}" title="按屬性拆分為多個節點">按 ${sp[0]} 拆分</button>` : ''}</label>`; };
  const cfg = `<aside class="qpanel card ${cfgOpen ? '' : 'collapsed'}"><div class="card-h"><h3>路徑設定</h3><span class="sub">用戶路徑</span><div class="right"><button class="btn-ghost sm mob-only" data-act="qcfg">${cfgOpen ? '收合' : '展開'}</button></div></div><div class="qbody stack" style="gap:14px">
    <div class="qsec"><div class="qsec-h"><b>路徑方向</b><span class="note">看某事件「之後」去了哪裡，或「之前」從哪裡來</span></div><div class="seg"><button class="${P.dir === 'after' ? 'on' : ''}" data-act="pathdir" data-arg="after">起始事件之後</button><button class="${P.dir === 'before' ? 'on' : ''}" data-act="pathdir" data-arg="before">結束事件之前</button></div>
      <div class="row" style="margin-top:8px"><select class="sel grow" data-act="pathanchor">${LIVE_EVENTS.map(e => `<option value="${e.name}" ${e.name === P.anchor ? 'selected' : ''}>${esc(e.cn)}（${e.name}）</option>`).join('')}</select><button class="btn-ghost sm ${P.anchorOpen ? 'on' : ''}" data-act="pathanchorf">篩選${P.anchorFilters.items.length ? ` ${P.anchorFilters.items.length}` : ''}</button></div>${P.anchorOpen ? filterBuilder('pt.anchor', { title: `${esc(anchorE.cn)} 的事件篩選`, compact: true }) : ''}</div>
    <div class="qsec"><div class="qsec-h"><b>參與分析的事件</b><span class="note">只有勾選的事件會成為路徑節點，其餘視為不存在；畫面／頁面類可按屬性拆分</span></div><div class="ev-list">${PATH_EVENT_POOL.map(evRow).join('')}</div></div>
    <div class="qsec"><div class="qsec-h"><b>步數與會話</b></div><div class="row"><span class="note">步數</span><div class="seg">${[3, 4, 5, 6, 8].map(n => `<button class="${P.steps === n ? 'on' : ''}" data-act="pathsteps" data-arg="${n}">${n}</button>`).join('')}</div></div>
      <div class="row" style="margin-top:8px"><span class="note">會話間隔</span><select class="sel" data-act="pathsession">${[5, 15, 30, 60, 120].map(m => `<option value="${m}" ${P.session === m ? 'selected' : ''}>${m} 分鐘無事件即切斷</option>`).join('')}</select></div>
      <label class="row chk-row" style="margin-top:8px"><button class="chk ${P.merge ? 'on' : ''}" data-act="pathmerge" aria-label="合併連續重複"></button><span>合併連續重複的事件（看盤→看盤 視為一步）</span></label>
      <div class="row" style="margin-top:8px"><span class="note">統計單位</span><div class="seg"><button class="${P.unit === 'users' ? 'on' : ''}" data-act="pathunit" data-arg="users">用戶數</button><button class="${P.unit === 'sessions' ? 'on' : ''}" data-act="pathunit" data-arg="sessions">會話數</button></div><span class="note">抽樣</span><div class="seg"><button class="${P.sample === '1/10' ? 'on' : ''}" data-act="pathsample" data-arg="1/10">1/10</button><button class="${P.sample === 'full' ? 'on' : ''}" data-act="pathsample" data-arg="full">全量</button></div></div></div>
    <div class="qsec">${filterBuilder('pt', { title: '全局篩選（用戶屬性／標籤／事件屬性）' })}</div>
    <div class="qsec"><div class="qsec-h"><b>對比與時間</b></div><div class="row"><span class="note">分群</span><select class="sel" data-act="pathgroup"><option value="none">全部用戶</option>${SEGMENTS.slice(0, 5).map(s => `<option value="${esc(s.name)}" ${P.group === s.name ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}${['platform', 'u.tier'].map(k => propOf(k).vals.map(v => `<option value="${k}=${v}" ${P.group === `${k}=${v}` ? 'selected' : ''}>${propOf(k).cn} = ${v}</option>`).join('')).join('')}</select></div>
      <div class="row" style="margin-top:8px"><span class="note">時間</span><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div></div></div>
    <div class="acts qacts"><button class="btn-primary" data-act="pathrun">查詢</button><button class="btn-ghost" data-act="pathsave">儲存路徑</button><button class="btn-ghost" data-act="pathreset">重置</button></div></div></aside>`;
  const tiles = `<div class="grid c4 kpi"><div class="tile"><div class="lbl">${P.dir === 'after' ? '起點' : '終點'}${P.unit === 'sessions' ? '會話' : '用戶'}</div><div class="val">${compact(G.users)}</div><div class="delta"><span>${P.sample === '1/10' ? `抽樣 1/10，推估 ${compact(G.users * 10)}` : '全量'}</span></div></div>
    <div class="tile"><div class="lbl">${P.steps} 步內到達成交</div><div class="val">${pct(convN / G.users)}</div><div class="delta"><span>${compact(convN)} 人經過 bond_deal</span></div></div>
    <div class="tile"><div class="lbl">第 1 步即離開</div><div class="val">${pct(exitFirst / G.users)}</div><div class="delta ${exitFirst / G.users > .2 ? 'down' : ''}"><b>${compact(exitFirst)}</b><span>會話在 ${P.session} 分鐘內無後續</span></div></div>
    <div class="tile"><div class="lbl">平均路徑長度</div><div class="val">${avgLen.toFixed(1)}<small>步</small></div><div class="delta"><span>節點 ${sum(G.nodes.map(c => c.length))} · 連結 ${G.links.length}</span></div></div></div>`;
  const nodeCard = selNode ? `<div class="card"><div class="card-h"><h3>${selNode.c === 0 ? (P.dir === 'after' ? '起點' : '終點') : `${P.dir === 'after' ? '第' : '前'} ${selNode.c} 步`}：${esc(selNode.n.cn)}</h3><span class="sub">${n0(selNode.n.n)} · 占 ${pct(selNode.n.n / G.users)}</span></div>
      <div class="grid c21" style="gap:10px"><div><div class="note" style="font-weight:700;margin-bottom:4px">來自</div>${selNode.ins.length ? hbar(selNode.ins.map(x => ({ k: x.cn, v: x.v })), { fmt: n0, total: selNode.n.n }) : '<div class="note">—</div>'}</div><div><div class="note" style="font-weight:700;margin-bottom:4px">去向</div>${selNode.outs.length ? hbar(selNode.outs.map(x => ({ k: x.cn, v: x.v })), { fmt: n0, color: SER[1], total: selNode.n.n }) : '<div class="note">路徑在此結束</div>'}</div></div>
      <div class="acts" style="margin-top:10px">${selNode.n.k !== 'exit' && selNode.n.k !== 'other' ? `<button class="btn-ghost sm" data-act="pathsetanchor" data-arg="${selNode.n.k.split(':')[0]}">以此為起點重新分析</button>` : ''}<button class="btn-ghost sm" data-act="pathusers" data-arg="${esc(selNode.n.cn)}｜${selNode.c}">查看用戶列表（${compact(selNode.n.n)}）</button><button class="btn-ghost sm" data-act="toast" data-arg="已建立分群草稿：經過「${esc(selNode.n.cn)}」的用戶（需審批）">存為分群</button></div></div>`
    : `<div class="card"><div class="card-h"><h3>節點明細</h3></div><div class="note">點選桑基圖的任一節點：看它的來源／去向、以它為新起點、或下鑽到用戶列表。</div></div>`;
  const stepTable = `<div class="tbl-wrap"><table class="tbl"><tr><th>步驟</th><th>節點</th><th class="num">${P.unit === 'sessions' ? '會話數' : '用戶數'}</th><th class="num">占${P.dir === 'after' ? '起點' : '終點'}</th><th class="num">占本步</th></tr>${G.nodes.flatMap((col, c) => { const tot = sum(col.map(n => n.n)); return col.map((n, i) => `<tr class="click ${P.sel === `${c}:${i}` ? 'sel' : ''}" data-act="pathnode" data-arg="${c}:${i}"><td class="mono">${c === 0 ? '0' : (P.dir === 'after' ? '+' : '−') + c}</td><td style="font-weight:600">${esc(n.cn)}</td><td class="num mono">${n0(n.n)}</td><td class="num mono">${pct(n.n / G.users)}</td><td class="num mono">${pct(n.n / tot)}</td></tr>`); }).join('')}</table></div>`;
  const topTable = `<div class="tbl-wrap"><table class="tbl pathlist"><tr><th>Top 路徑${P.must ? `（含「${esc(evCn(P.must))}」）` : ''}</th><th class="num">${P.unit === 'sessions' ? '會話數' : '用戶數'}</th><th class="num">占比</th><th class="num">平均耗時</th><th></th></tr>${top.map(r => `<tr><td><div class="p">${(P.dir === 'before' ? r.p.slice().reverse() : r.p).map((s, i) => `${i ? '<i>›</i>' : ''}<span class="pill ${s === '離開' || s === '會話結束' ? 'gray' : /成交|委託|入金/.test(s) ? 'amber' : 'blue'}">${esc(s)}</span>`).join('')}</div></td><td class="num mono">${n0(r.n)}</td><td class="num mono">${pct(r.n / G.users)}</td><td class="num mono">${esc(r.t)}</td><td class="num"><button class="btn-ghost sm" data-act="pathusers" data-arg="${esc(r.p.join(' › '))}｜path">用戶列表</button></td></tr>`).join('') || '<tr><td colspan="5"><div class="empty">沒有符合的路徑</div></td></tr>'}</table></div>`;
  const res = `<section class="qresult stack">${tiles}
    <div class="card"><div class="card-h"><h3>${esc(anchorE.cn)}${P.dir === 'after' ? '之後' : '之前'} ${P.steps} 步</h3><span class="sub">${dateRangeText()} · 會話 ${P.session} 分鐘 · ${P.unit === 'sessions' ? '會話數' : '用戶數'}${P.group !== 'none' ? ` · 分群：${esc(P.group)}` : ''}${P.filters.items.length ? ` · 篩選：${esc(filterText(P.filters))}` : ''}</span><div class="right"><div class="seg">${[['sankey', '桑基圖'], ['list', '路徑列表'], ['table', '節點表']].map(v => `<button class="${P.view === v[0] ? 'on' : ''}" data-act="pathview" data-arg="${v[0]}">${v[1]}</button>`).join('')}</div></div></div>
      ${P.view === 'sankey' ? sankey(G) + `<div class="legend"><span class="k"><i class="sw rect" style="background:var(--s1)"></i>行為</span><span class="k"><i class="sw rect" style="background:var(--s2)"></i>交易</span><span class="k"><i class="sw rect" style="background:#B9C3CA"></i>其他事件</span><span class="k"><i class="sw rect" style="background:var(--axis)"></i>${P.unit === 'sessions' ? '會話結束' : '離開'}</span><span class="k note" style="margin-left:auto">點節點看明細；寬度 = 人數</span></div>` : P.view === 'list' ? `<div class="row" style="margin-bottom:8px"><span class="note">只看包含</span><select class="sel" data-act="pathmust"><option value="">任何事件</option>${P.events.flatMap(pathExpand).map(k => `<option value="${k}" ${P.must === k ? 'selected' : ''}>${esc(evCn(k))}</option>`).join('')}</select></div>` + topTable : stepTable}
      <div class="note" style="margin-top:8px">以 ClickHouse 在 Session 內以 ${P.anchor} 為${P.dir === 'after' ? '起點' : '終點'}取 ${P.steps} 步序列（arrayJoin + sequenceMatch），節點規則來自事件字典；${P.sample === '1/10' ? '抽樣 1/10（SAMPLE），人數已推估；' : '全量；'}未勾選的事件在序列中略過。</div></div>
    <div class="grid c21">${nodeCard}<div class="card"><div class="card-h"><h3>相關洞察</h3></div><div class="note">委託失敗（SYS02）後 68% 的人在 1 分鐘內重新委託、32% 直接離開；離開者中 P1/P2 占 19%。建議對 SYS 類失敗即時推播「已為您保留委託」訊息。</div><div class="acts" style="margin-top:10px"><button class="btn-ghost sm" data-act="goto" data-arg="#/ai/insights">查看 ins_01</button><button class="btn-ghost sm" data-act="toast" data-arg="已提議即時規則（需審批）">提議規則</button></div></div></div>
    ${P.view !== 'list' ? `<div class="card"><div class="card-h"><h3>Top 路徑</h3><span class="sub">依人數排序；可下鑽到用戶列表</span><div class="right"><span class="note">只看包含</span><select class="sel xs" data-act="pathmust"><option value="">任何事件</option>${P.events.flatMap(pathExpand).map(k => `<option value="${k}" ${P.must === k ? 'selected' : ''}>${esc(evCn(k))}</option>`).join('')}</select></div></div>${topTable}</div>` : ''}
  </section>`;
  S._ptSql = `-- path.run: anchor=${P.anchor} dir=${P.dir} steps=${P.steps} session=${P.session}m unit=${P.unit}${P.sample === '1/10' ? ' SAMPLE 1/10' : ''}\nWITH seq AS (\n  SELECT one_id, session_id, arraySort(x -> x.1, groupArray((time, event_node))) AS s\n  FROM dmp.events${P.sample === '1/10' ? ' SAMPLE 1/10' : ''}\n  PREWHERE event_date >= today()-${S.range} AND event IN (${P.events.map(e => `'${e}'`).join(', ')})${P.filters.items.length ? `\n    AND (${P.filters.items.map(c => `${c.k.replace('u.', 'profile.').replace('tag.', 'tags.')} ${c.op === 'eq' ? '=' : c.op.toUpperCase()} '${c.v}'`).join(P.filters.logic === 'and' ? ' AND ' : ' OR ')})` : ''}\n  GROUP BY one_id, session_id)   -- session_id 以 ${P.session} 分鐘無事件切斷（Flink session window）\nSELECT path, ${P.unit === 'sessions' ? 'count()' : 'uniqExact(one_id)'} AS n FROM (\n  SELECT one_id, arraySlice(${P.merge ? 'arrayCompact(' : ''}arrayMap(x -> x.2, s)${P.merge ? ')' : ''}, ${P.dir === 'after' ? 'idx' : `greatest(1, idx-${P.steps})`}, ${P.steps + 1}) AS path\n  FROM seq ARRAY JOIN arrayEnumerate(s) AS idx WHERE s[idx].2 = '${P.anchor}')\nGROUP BY path ORDER BY n DESC LIMIT 100;`;
  return `<div class="page-h"><div><h1>行為路徑分析</h1><p>起始／結束事件、參與事件集（可按屬性拆分）、步數、會話間隔、合併重複、用戶／會話口徑、全局篩選與分群對比；桑基圖節點可點選、下鑽與再分析。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="path">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="${esc(anchorE.cn)}${P.dir === 'after' ? '之後' : '之前'}最常見的流失路徑是什麼？哪一步離開最多、可能原因？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="qlayout">${cfg}${res}</div>`;
}

/* ------------------------------------------------------------------ 歸因分析 */
const ATTR_MODELS = [['first', '首次觸點'], ['last', '末次觸點'], ['linear', '線性'], ['position', '位置（U 型）'], ['decay', '時間衰減'], ['all', '模型對比']];
function attrCompute() {
  const A = S.attr; const rangeF = S.range / 30; const gF = filterFactor(A.filters) * filterFactor(A.goalFilters);
  const win = A.window.unit === '天' ? A.window.n : A.window.n / 24; const winF = Math.min(1.15, .6 + .4 * Math.log10(1 + win) / Math.log10(31));
  const goalBase = { bond_deal: 6120, fund_deposit: 9060, kh_review_pass: 22100, sip_purchase: 3900 }[A.goal] || Math.round(evOf(A.goal).daily * .002);
  const metricF = A.goalMetric === 'total' ? 1.6 : A.goalMetric === 'users' ? 1 : hf(A.goalProp, 300, 1800);   // 指標口徑：次數／用戶／數值
  const convU = Math.round(goalBase * rangeF * gF);                                                              // 轉化用戶（人）
  const touchedU = Math.round(convU * Math.min(.95, .62 * winF + hf(A.goal, 0, .1)));
  const [p1, p2, p3] = A.posWeights.split('/').map(Number); const hl = Math.max(.5, A.halfLife);
  const rows = A.touches.map((t, i) => {
    const ref = ATTRIBUTION.touches.find(x => x.k === t.name); const tf = filterFactor(t.filters) * (ref ? 1 : hf(t.name + t.ev, .3, .9)) * (A.assocEv ? hf(A.assocEv + t.ev, .5, .9) : 1);
    const base = ref || { first: 900 * hf(t.ev + 'f', .4, 1.4), last: 900 * hf(t.ev + 'l', .4, 1.4), linear: 900, avgTouches: 1.8 };
    const sc = v => v * (touchedU / 6120) * tf; const first = sc(base.first), last = sc(base.last), linear = sc(base.linear);   // 人數口徑
    const position = (first * p1 + linear * p2 + last * p3) / 100; const decay = last * (1 - Math.exp(-7 / hl)) + linear * Math.exp(-7 / hl);
    const exposures = Math.round(linear * hf(t.name + 'exp', 9, 40)); const direct = Math.round(last * hf(t.name + 'd', .45, .8)); const assist = Math.max(0, Math.round(linear * base.avgTouches - direct * .6));
    const m = v => Math.round(v * metricF);
    return { ...t, i, first: m(first), last: m(last), linear: m(linear), position: m(position), decay: m(decay), exposures, direct, assist, avgTouches: base.avgTouches, value: Math.round(linear * hf(t.name + 'v', 1200, 1900)) };
  });
  return { conversions: Math.round(convU * metricF), touched: Math.round(touchedU * metricF), touchedU, rows, avgTouches: Math.round(sum(rows.map(r => r.avgTouches)) / Math.max(1, rows.length) * 10) / 10 };
}
function pageAttribution() {
  const A = S.attr; const R = attrCompute(); const m = A.model; const key = m === 'all' ? 'position' : m; const cfgOpen = !S.qcfgCollapsed; const goalE = evOf(A.goal);
  const fmtV = A.goalMetric === 'sum' ? compact : n0; const tot = sum(R.rows.map(r => r[key]));
  const touchRow = (t, i) => `<div class="qm"><div class="qm-hd"><span class="qm-idx" style="background:${SER[i % 8]}">${i + 1}</span><input class="inp grow" data-act="attrtname" data-arg="${i}" value="${esc(t.name)}" placeholder="觸點名稱"><select class="sel" data-act="attrtev" data-arg="${i}">${LIVE_EVENTS.map(e => `<option value="${e.name}" ${e.name === t.ev ? 'selected' : ''}>${esc(e.cn)}</option>`).join('')}</select><button class="btn-ghost sm ${t.open ? 'on' : ''}" data-act="attrtfilter" data-arg="${i}">篩選${t.filters.items.length ? ` ${t.filters.items.length}` : ''}</button>${A.touches.length > 1 ? `<button class="icon-btn x sm" data-act="attrtdel" data-arg="${i}">✕</button>` : ''}</div>${t.open ? filterBuilder(`at.t${i}`, { title: `觸點 ${i + 1} 的事件篩選`, compact: true }) : (t.filters.items.length ? `<div class="qm-ft note">${esc(filterText(t.filters))}</div>` : '')}</div>`;
  const cfg = `<aside class="qpanel card ${cfgOpen ? '' : 'collapsed'}"><div class="card-h"><h3>歸因設定</h3><span class="sub">歸因分析</span><div class="right"><button class="btn-ghost sm mob-only" data-act="qcfg">${cfgOpen ? '收合' : '展開'}</button></div></div><div class="qbody stack" style="gap:14px">
    <div class="qsec"><div class="qsec-h"><b>目標轉化事件</b><span class="note">要被歸因的結果；指標可為次數、用戶數或數值屬性求和</span></div><div class="row"><select class="sel grow" data-act="attrgoal">${LIVE_EVENTS.filter(e => ['trade', 'business'].includes(e.kind)).map(e => `<option value="${e.name}" ${e.name === A.goal ? 'selected' : ''}>${esc(e.cn)}（${e.name}）</option>`).join('')}</select><button class="btn-ghost sm ${A.goalOpen ? 'on' : ''}" data-act="attrgoalf">篩選${A.goalFilters.items.length ? ` ${A.goalFilters.items.length}` : ''}</button></div>
      <div class="row" style="margin-top:8px"><span class="note">指標</span><select class="sel" data-act="attrgm"><option value="users" ${A.goalMetric === 'users' ? 'selected' : ''}>轉化用戶數</option><option value="total" ${A.goalMetric === 'total' ? 'selected' : ''}>轉化次數</option><option value="sum" ${A.goalMetric === 'sum' ? 'selected' : ''}>數值屬性求和</option></select>${A.goalMetric === 'sum' ? `<select class="sel" data-act="attrgp">${NUM_PROPS.map(p => `<option value="${p.k}" ${p.k === A.goalProp ? 'selected' : ''}>${esc(p.cn)}</option>`).join('')}</select>` : ''}</div>${A.goalOpen ? filterBuilder('at.goal', { title: '目標事件篩選', compact: true }) : ''}</div>
    <div class="qsec"><div class="qsec-h"><b>待歸因事件（觸點）</b><span class="note">轉化前的行銷／行為接觸；可命名、可加屬性篩選（例：$pageview 且 $utm_source = google）</span></div>${A.touches.map(touchRow).join('')}<div class="acts"><button class="btn-ghost sm" data-act="attrtadd" ${A.touches.length >= 10 ? 'disabled' : ''}>＋ 添加觸點</button></div></div>
    <div class="qsec"><div class="qsec-h"><b>前向關聯事件（選填）</b><span class="note">觸點與轉化之間須經過的事件，並以同一屬性值關聯（例：同一商品代碼的看盤）</span></div><div class="row"><select class="sel grow" data-act="attrassoc"><option value="">不設定</option>${['hq_stockdetail_view', 'deal_opt_mani', 'search_submit', '$pageview'].map(e => `<option value="${e}" ${A.assocEv === e ? 'selected' : ''}>${esc(evOf(e).cn)}</option>`).join('')}</select>${A.assocEv ? `<select class="sel" data-act="attrassocp">${PROPS.filter(p => p.scope === 'event' && p.vals).map(p => `<option value="${p.k}" ${p.k === A.assocProp ? 'selected' : ''}>${esc(p.cn)}</option>`).join('')}</select>` : ''}</div></div>
    <div class="qsec"><div class="qsec-h"><b>歸因模型與窗口</b></div><div class="seg wrap">${ATTR_MODELS.map(x => `<button class="${m === x[0] ? 'on' : ''}" data-act="attrmodel" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div>
      ${m === 'position' || m === 'all' ? `<div class="row" style="margin-top:8px"><span class="note">位置權重（首／中／末）</span><select class="sel" data-act="attrpos">${['40/20/40', '30/40/30', '50/0/50'].map(w => `<option ${A.posWeights === w ? 'selected' : ''}>${w}</option>`).join('')}</select></div>` : ''}
      ${m === 'decay' || m === 'all' ? `<div class="row" style="margin-top:8px"><span class="note">時間衰減半衰期</span><input class="inp num" type="number" min="1" max="90" data-act="attrhl" value="${A.halfLife}"><span class="note">天</span></div>` : ''}
      <div class="row" style="margin-top:8px"><span class="note">回溯窗口</span><input class="inp num" type="number" min="1" max="365" data-act="attrwin" value="${A.window.n}"><select class="sel" data-act="attrwinu">${['天', '小時'].map(u => `<option ${A.window.unit === u ? 'selected' : ''}>${u}</option>`).join('')}</select><span class="note">內的觸點才計入</span></div></div>
    <div class="qsec">${filterBuilder('at', { title: '全局篩選（用戶屬性／標籤）' })}</div>
    <div class="qsec"><div class="qsec-h"><b>分組與時間</b></div><div class="row"><span class="note">分組</span><select class="sel" data-act="attrgroup">${[['none', '不分組'], ['platform', '平台'], ['u.tier', '價值分級'], ['u.age_band', '年齡層'], ['u.first_utm_source', '首次來源']].map(g => `<option value="${g[0]}" ${A.group === g[0] ? 'selected' : ''}>${g[1]}</option>`).join('')}</select></div>
      <div class="row" style="margin-top:8px"><span class="note">轉化時間</span><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div></div></div>
    <div class="acts qacts"><button class="btn-primary" data-act="attrrun">查詢</button><button class="btn-ghost" data-act="attrsave">儲存報表</button><button class="btn-ghost" data-act="attrreset">重置</button></div></div></aside>`;
  const tiles = `<div class="grid c4 kpi"><div class="tile"><div class="lbl">${A.goalMetric === 'sum' ? `轉化價值（${esc(propOf(A.goalProp).cn)}）` : A.goalMetric === 'total' ? '轉化次數' : '轉化用戶'}</div><div class="val">${fmtV(R.conversions)}</div><div class="delta"><span>${esc(goalE.cn)} · ${dateRangeText()}</span></div></div>
    <div class="tile"><div class="lbl">有觸點可歸因</div><div class="val">${pct(R.touched / R.conversions)}</div><div class="delta"><span>其餘 ${pct(1 - R.touched / R.conversions)} 在 ${A.window.n} ${A.window.unit}內無任何觸點（直接轉化）</span></div></div>
    <div class="tile"><div class="lbl">平均觸點數</div><div class="val">${R.avgTouches}</div><div class="delta"><span>每次轉化前的接觸次數</span></div></div>
    <div class="tile"><div class="lbl">貢獻最高觸點</div><div class="val" style="font-size:18px">${esc([...R.rows].sort((a, b) => b[key] - a[key])[0]?.name || '—')}</div><div class="delta"><span>${ATTR_MODELS.find(x => x[0] === key)[1]}模型下 ${pct(([...R.rows].sort((a, b) => b[key] - a[key])[0]?.[key] || 0) / tot)}</span></div></div></div>`;
  const chart = m === 'all'
    ? barChart('attr-cmp', ['first', 'last', 'linear', 'position', 'decay'].map((k, i) => ({ name: ATTR_MODELS.find(x => x[0] === k)[1], color: SER[i], data: R.rows.map(r => ({ d: r.name, v: r[k] })) })), { h: 280, fmt: fmtV })
    : hbar(R.rows.slice().sort((a, b) => b[key] - a[key]).map(r => ({ k: r.name, v: r[key] })), { fmt: fmtV, color: SER[2], total: tot });
  const table = `<div class="tbl-wrap"><table class="tbl"><tr><th>待歸因事件</th><th class="num">觸點次數</th><th class="num">直接轉化</th><th class="num">輔助轉化</th>${m === 'all' ? ['first', 'last', 'linear', 'position', 'decay'].map(k => `<th class="num">${ATTR_MODELS.find(x => x[0] === k)[1]}</th>`).join('') : `<th class="num">歸因${A.goalMetric === 'sum' ? '價值' : '轉化'}</th><th class="num">占比</th>${A.goalMetric === 'sum' ? '' : '<th class="num">歸因價值（手續費）</th>'}`}<th class="num">觸點→轉化率</th><th></th></tr>${R.rows.slice().sort((a, b) => b[key] - a[key]).map(r => `<tr><td><i class="sw" style="background:${SER[r.i % 8]}"></i> <b>${esc(r.name)}</b><span class="sub mono">${r.ev}${r.filters.items.length ? ' · ' + esc(filterText(r.filters)) : ''}</span></td><td class="num mono">${n0(r.exposures)}</td><td class="num mono">${n0(r.direct)}</td><td class="num mono">${n0(r.assist)}</td>${m === 'all' ? ['first', 'last', 'linear', 'position', 'decay'].map(k => `<td class="num mono">${fmtV(r[k])}</td>`).join('') : `<td class="num mono"><b>${fmtV(r[key])}</b></td><td class="num mono">${pct(r[key] / tot)}</td>${A.goalMetric === 'sum' ? '' : `<td class="num mono">${compact(r.value)}</td>`}`}<td class="num mono">${pct(r[key] / r.exposures, 2)}</td><td class="num"><button class="btn-ghost sm" data-act="attrusers" data-arg="${esc(r.name)}">用戶列表</button></td></tr>`).join('')}<tr class="total"><td>合計</td><td class="num mono">${n0(sum(R.rows.map(r => r.exposures)))}</td><td class="num mono">${n0(sum(R.rows.map(r => r.direct)))}</td><td class="num mono">${n0(sum(R.rows.map(r => r.assist)))}</td>${m === 'all' ? ['first', 'last', 'linear', 'position', 'decay'].map(k => `<td class="num mono">${fmtV(sum(R.rows.map(r => r[k])))}</td>`).join('') : `<td class="num mono">${fmtV(tot)}</td><td class="num mono">100%</td>${A.goalMetric === 'sum' ? '' : `<td class="num mono">${compact(sum(R.rows.map(r => r.value)))}</td>`}`}<td></td><td></td></tr></table></div>`;
  const paths = [...R.rows].sort((a, b) => b.linear - a.linear).slice(0, 4); const convPaths = [[paths[0], paths[2]], [paths[1]], [paths[0], paths[1], paths[2]], [paths[2]], [paths[3], paths[0]]].filter(p => p.every(Boolean)).map((p, i) => ({ p: p.map(x => x.name), n: Math.round(R.touchedU * hf(p.map(x => x.name).join('>'), .04, .16) / (i + 1)), t: `${(hf(p.join(), .4, 6)).toFixed(1)} 天` }));
  const groupTable = A.group !== 'none' ? `<div class="card"><div class="card-h"><h3>分組對比：${esc(propOf(A.group).cn)}</h3><span class="sub">${ATTR_MODELS.find(x => x[0] === key)[1]}模型下各組歸因${A.goalMetric === 'sum' ? '價值' : '轉化'}</span></div><div class="tbl-wrap"><table class="tbl"><tr><th>${esc(propOf(A.group).cn)}</th>${R.rows.map(r => `<th class="num">${esc(r.name)}</th>`).join('')}<th class="num">合計</th></tr>${propOf(A.group).vals.slice(0, 5).map(v => { const w = hf(A.group + v, .08, .4); const cells = R.rows.map(r => Math.round(r[key] * w * hf(v + r.name, .7, 1.3))); return `<tr><td style="font-weight:600">${esc(v)}</td>${cells.map(c => `<td class="num mono">${fmtV(c)}</td>`).join('')}<td class="num mono"><b>${fmtV(sum(cells))}</b></td></tr>`; }).join('')}</table></div></div>` : '';
  const res = `<section class="qresult stack">${tiles}
    <div class="card"><div class="card-h"><h3>${esc(goalE.cn)}的歸因 · ${ATTR_MODELS.find(x => x[0] === m)[1]}</h3><span class="sub">回溯 ${A.window.n} ${A.window.unit}${A.assocEv ? ` · 前向關聯 ${esc(evOf(A.assocEv).cn)}（${A.assocProp}）` : ''}${A.filters.items.length ? ` · 篩選：${esc(filterText(A.filters))}` : ''}</span><div class="right"><div class="seg">${[['chart', '圖表'], ['table', '數據表'], ['paths', '轉化路徑']].map(v => `<button class="${A.view === v[0] ? 'on' : ''}" data-act="attrview" data-arg="${v[0]}">${v[1]}</button>`).join('')}</div></div></div>
      ${A.view === 'chart' ? chart : A.view === 'table' ? table : `<div class="tbl-wrap"><table class="tbl pathlist"><tr><th>Top 轉化路徑（觸點序列 → ${esc(goalE.cn)}）</th><th class="num">轉化數</th><th class="num">占可歸因</th><th class="num">平均轉化時長</th></tr>${convPaths.map(r => `<tr><td><div class="p">${r.p.map((s, i) => `${i ? '<i>›</i>' : ''}<span class="pill blue">${esc(s)}</span>`).join('')}<i>›</i><span class="pill amber">${esc(goalE.cn)}</span></div></td><td class="num mono">${n0(r.n)}</td><td class="num mono">${pct(r.n / R.touchedU)}</td><td class="num mono">${r.t}</td></tr>`).join('')}</table></div>`}
      <div class="note" style="margin-top:8px">${m === 'first' ? '首次觸點：全部功勞給窗口內第一個觸點，適合評估「帶來新客」的渠道。' : m === 'last' ? '末次觸點：全部功勞給轉化前最後一個觸點，適合評估「臨門一腳」。' : m === 'linear' ? '線性：窗口內每個觸點平分。' : m === 'position' ? `位置（U 型）：首／末各 ${A.posWeights.split('/')[0]}%／${A.posWeights.split('/')[2]}%，中間觸點平分 ${A.posWeights.split('/')[1]}%。` : m === 'decay' ? `時間衰減：越接近轉化的觸點權重越高，半衰期 ${A.halfLife} 天。` : '模型對比：同一組轉化在五種模型下的分配；差異大的觸點代表其在路徑中的位置偏首或偏末。'}以預計算觸點表（事件表 5–10% 體積）計算，五種模型同一次掃描。</div></div>
    ${A.view !== 'table' ? `<div class="card"><div class="card-h"><h3>數據表</h3><span class="sub">觸點次數、直接／輔助轉化、歸因結果、歸因價值；點「用戶列表」看經該觸點轉化的客戶</span></div>${table}</div>` : ''}
    ${groupTable}
    <div class="grid c21"><div class="card"><div class="card-h"><h3>模型差異最大的觸點</h3></div>${hbar(R.rows.map(r => ({ k: r.name, v: Math.abs(r.first - r.last) })).sort((a, b) => b.v - a.v).slice(0, 4), { fmt: v => '±' + n0(v), color: SER[6], total: 1e12 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '')}<div class="note" style="margin-top:8px">首次與末次差異大的觸點：差為正且末次高 = 臨門一腳型（例：營業員接觸）；首次高 = 引流型（例：LINE 推播）。建議以位置模型作預算分配依據，並用「分組」看各平台是否一致。</div></div>
    <div class="card"><div class="card-h"><h3>相關洞察</h3></div><div class="stack" style="gap:10px">${INSIGHTS.filter(i => i.kind === 'opportunity' || i.kind === 'attribution').slice(0, 2).map(i => insightCard(i, true)).join('') || '<div class="empty">暫無</div>'}</div></div></div>
  </section>`;
  S._atSql = `-- attribution.run: goal=${A.goal}(${A.goalMetric}${A.goalMetric === 'sum' ? ':' + A.goalProp : ''}) window=${A.window.n}${A.window.unit === '天' ? 'd' : 'h'} models=[first,last,linear,position(${A.posWeights}),decay(hl=${A.halfLife}d)]${A.assocEv ? ` assoc=${A.assocEv}.${A.assocProp}` : ''}\nWITH conv AS (SELECT one_id_uint, ${A.goalMetric === 'total' ? 'time' : 'min(time)'} t${A.goalMetric === 'sum' ? `, sum(props.${A.goalProp}) val` : ''} FROM dmp.events WHERE event='${A.goal}' AND event_date >= today()-${S.range}${A.goalFilters.items.length ? ' AND ' + A.goalFilters.items.map(c => `${c.k} = '${c.v}'`).join(' AND ') : ''} GROUP BY one_id_uint${A.goalMetric === 'total' ? ', time' : ''}),\n tp AS (SELECT c.one_id_uint, arraySort(x -> x.1, groupArray((t.time, t.touch))) AS touches\n       FROM conv c JOIN dmp.touchpoints t ON t.one_id_uint = c.one_id_uint AND t.time BETWEEN c.t - ${A.window.n}*${A.window.unit === '天' ? 86400 : 3600} AND c.t\n       WHERE t.touch IN (${A.touches.map(t => `'${t.name}'`).join(', ')})   -- 觸點定義：${A.touches.map(t => `${t.name}=${t.ev}${t.filters.items.length ? '[' + t.filters.items.map(c => `${c.k}=${c.v}`).join(',') + ']' : ''}`).join('; ')}\n       GROUP BY 1)\nSELECT touch, sum(w_first) first, sum(w_last) last, sum(w_linear) linear, sum(w_pos) position, sum(w_decay) decay\nFROM tp ARRAY JOIN touches AS x, arrayEnumerate(touches) AS i   -- 權重於 SQL 內計算\nGROUP BY touch${A.group !== 'none' ? `, ${A.group}` : ''} ORDER BY position DESC;`;
  return `<div class="page-h"><div><h1>歸因分析</h1><p>目標轉化事件（次數／用戶／數值）、待歸因觸點（可命名、可篩選）、前向關聯事件、五種模型（位置權重／半衰期可調）、回溯窗口、分組；結果含觸點次數、直接／輔助轉化、歸因價值與轉化路徑。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="attribution">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="${esc(goalE.cn)}的歸因：哪個觸點在位置模型與末次模型下差異最大？為什麼？預算該怎麼調？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="qlayout">${cfg}${res}</div>`;
}

/* ------------------------------------------------------------------ 留存分析 */
const RET_GRANS = [['day', '日'], ['week', '週'], ['month', '月']];
function retCurve(seedExtra = '') {                                                // 第 0..N 期留存率（基準曲線）
  const R = S.ret; const pair = `${R.init}>${R.retAny ? 'any' : R.ret}`; const a0 = R.retAny ? .72 : { 'kh_review_pass>bond_deal': .46, 'kh_review_pass>login': .68, 'login>login': .62, 'fund_deposit>bond_deal': .71, 'login>bond_deal': .41, 'push_click>login': .35 }[pair] ?? hf(pair, .25, .6);
  const g = R.gran === 'day' ? 1 : R.gran === 'week' ? .6 : .45; const slope = (hf(pair + 's', .18, .32)) * g;
  const ff = filterFactor(R.filters) * filterFactor(R.initFilters) * filterFactor(R.retFilters); const a = Math.min(.95, a0 * (ff < 1 ? hf(filterKey(R.filters) + filterKey(R.initFilters) + seedExtra, .85, 1.2) : 1) * (seedExtra ? hf(seedExtra, .8, 1.2) : 1));
  return Array.from({ length: R.periods + 1 }, (_, i) => i === 0 ? 1 : Math.min(.98, a * Math.pow(i, -slope) * (R.calc === 'range' ? 1 + .18 * Math.log(i + 1) : 1)));
}
function retCohorts(seedExtra = '') {
  const R = S.ret; const e = evOf(R.init); const perDays = R.gran === 'day' ? 1 : R.gran === 'week' ? 7 : 30; const nC = Math.max(2, Math.min(12, Math.floor(S.range / perDays)));
  const base = Math.min(e.daily * .3, 412000 * hf(e.name + ':u', .25, .95)) * perDays * filterFactor(R.filters) * filterFactor(R.initFilters) * (seedExtra ? hf(seedExtra, .1, .45) : 1);
  const curve = retCurve(seedExtra); const out = [];
  for (let c = nC - 1; c >= 0; c--) {
    const start = dayOffset(-perDays * c - (perDays - 1)); const label = R.gran === 'day' ? dkey(start) : R.gran === 'week' ? `${dkey(start).slice(5)} 週` : dkey(start).slice(0, 7);
    const n = Math.round(base * (1 + Math.sin(c * 1.3 + hash(R.init) % 5) * .12)); const avail = Math.floor((perDays * c + perDays - 1) / perDays);
    const cells = curve.map((r, i) => i > avail ? null : i === 0 ? 1 : Math.min(.98, r * (1 + (hf(label + i + seedExtra, -.07, .07))) * (c === 0 && i === 1 && R.init === 'kh_review_pass' ? .88 : 1)));
    out.push({ label, n, cells });
  }
  return out;
}
function retTable(rows) {
  const R = S.ret;
  const cls = v => { if (v == null) return 'n'; const x = R.mode === 'churn' ? 1 - v : v; return x >= .6 ? 'c5' : x >= .45 ? 'c4' : x >= .35 ? 'c3' : x >= .25 ? 'c2' : x >= .15 ? 'c1' : 'c0'; };
  const avg = i => { const vs = rows.map(r => r.cells[i]).filter(v => v != null); return vs.length ? sum(vs) / vs.length : null; };
  const unit = RET_GRANS.find(g => g[0] === R.gran)[1];
  return `<div class="tbl-wrap"><table class="heat"><tr><th>初始行為${unit}</th><th>初始人數</th>${Array.from({ length: R.periods + 1 }, (_, i) => `<th>${i === 0 ? '當' + unit : `第 ${i} ${unit}`}</th>`).join('')}</tr>
    <tr class="avg"><td class="nm">平均</td><td class="n mono">${n0(sum(rows.map(r => r.n)))}</td>${Array.from({ length: R.periods + 1 }, (_, i) => { const v = avg(i); return `<td class="${cls(v)}">${v == null ? '' : R.show === 'pct' ? pct(R.mode === 'churn' ? 1 - v : v, 1) : ''}</td>`; }).join('')}</tr>
    ${rows.map(r => `<tr><td class="nm">${esc(r.label)}</td><td class="n mono">${n0(r.n)}</td>${r.cells.map((v, i) => `<td class="${cls(v)} ${v != null && i ? 'click' : ''}" ${v != null && i ? `data-act="retcell" data-arg="${esc(r.label)}:${i}"` : ''} title="${esc(r.label)} 第 ${i} ${unit}：${v == null ? '—' : R.show === 'pct' ? pct(v, 1) : n0(v * r.n)}">${v == null ? '' : R.show === 'pct' ? pct(R.mode === 'churn' ? 1 - v : v, i ? 1 : 0) : n0((R.mode === 'churn' ? 1 - v : v) * r.n)}</td>`).join('')}</tr>`).join('')}</table></div>`;
}
function pageRetention() {
  const R = S.ret; const rows = retCohorts(); const curve = retCurve(); const cfgOpen = !S.qcfgCollapsed; const unit = RET_GRANS.find(g => g[0] === R.gran)[1]; const initE = evOf(R.init), retE = evOf(R.ret);
  const gvals = R.group === 'none' ? [] : R.group === 'segment' ? SEGMENTS.slice(0, 4).map(s => s.name) : propOf(R.group).vals.slice(0, 5);
  const gcurves = gvals.map((v, i) => ({ name: v, color: SER[i], data: retCurve(R.group + v).map((r, j) => ({ d: j === 0 ? '當' + unit : `第 ${j} ${unit}`, v: Math.round((R.mode === 'churn' ? 1 - r : r) * 1000) / 10 })) }));
  const avgAt = i => { const vs = rows.map(r => r.cells[i]).filter(v => v != null); return vs.length ? sum(vs) / vs.length : 0; }; const maxAvail = Math.max(...rows.map(r => r.cells.filter(v => v != null).length - 1)); const nMid = Math.max(1, Math.min(R.periods, 4, maxAvail));
  const latest = rows[rows.length - 1]; const best = rows.filter(r => r.cells[1] != null).sort((a, b) => b.cells[1] - a.cells[1])[0]; const worst = rows.filter(r => r.cells[1] != null).sort((a, b) => a.cells[1] - b.cells[1])[0];
  const cfg = `<aside class="qpanel card ${cfgOpen ? '' : 'collapsed'}"><div class="card-h"><h3>留存設定</h3><span class="sub">留存分析</span><div class="right"><button class="btn-ghost sm mob-only" data-act="qcfg">${cfgOpen ? '收合' : '展開'}</button></div></div><div class="qbody stack" style="gap:14px">
    <div class="qsec"><div class="qsec-h"><b>初始行為</b><span class="note">用戶在某期間第一次做這件事，成為該期 cohort</span></div><div class="row"><select class="sel grow" data-act="retinit">${LIVE_EVENTS.map(e => `<option value="${e.name}" ${e.name === R.init ? 'selected' : ''}>${esc(e.cn)}（${e.name}）</option>`).join('')}</select><button class="btn-ghost sm ${R.initOpen ? 'on' : ''}" data-act="retinitf">篩選${R.initFilters.items.length ? ` ${R.initFilters.items.length}` : ''}</button></div>${R.initOpen ? filterBuilder('rt.init', { title: '初始行為的事件篩選', compact: true }) : ''}</div>
    <div class="qsec"><div class="qsec-h"><b>後續行為（回訪）</b><span class="note">之後各期是否再做這件事；可選「任意事件」看活躍留存</span></div><div class="row"><select class="sel grow" data-act="retret" ${R.retAny ? 'disabled' : ''}>${LIVE_EVENTS.map(e => `<option value="${e.name}" ${e.name === R.ret ? 'selected' : ''}>${esc(e.cn)}（${e.name}）</option>`).join('')}</select><button class="btn-ghost sm ${R.retOpen ? 'on' : ''}" data-act="retretf" ${R.retAny ? 'disabled' : ''}>篩選${R.retFilters.items.length ? ` ${R.retFilters.items.length}` : ''}</button></div><label class="row chk-row" style="margin-top:8px"><button class="chk ${R.retAny ? 'on' : ''}" data-act="retany" aria-label="任意事件"></button><span>任意事件（活躍即算留存）</span></label>${R.retOpen && !R.retAny ? filterBuilder('rt.ret', { title: '後續行為的事件篩選', compact: true }) : ''}</div>
    <div class="qsec"><div class="qsec-h"><b>留存口徑</b></div><div class="row"><span class="note">粒度</span><div class="seg">${RET_GRANS.map(g => `<button class="${R.gran === g[0] ? 'on' : ''}" data-act="retgran" data-arg="${g[0]}">${g[1]}</button>`).join('')}</div><span class="note">顯示</span><input class="inp num" type="number" min="1" max="30" data-act="retperiods" value="${R.periods}"><span class="note">${unit}</span></div>
      <div class="row" style="margin-top:8px"><span class="note">計算</span><div class="seg"><button class="${R.calc === 'period' ? 'on' : ''}" data-act="retcalc" data-arg="period">第 N ${unit}當期留存</button><button class="${R.calc === 'range' ? 'on' : ''}" data-act="retcalc" data-arg="range">N ${unit}內區間留存</button></div></div>
      <div class="row" style="margin-top:8px"><span class="note">視角</span><div class="seg"><button class="${R.mode === 'retention' ? 'on' : ''}" data-act="retmode" data-arg="retention">留存</button><button class="${R.mode === 'churn' ? 'on' : ''}" data-act="retmode" data-arg="churn">流失</button></div><span class="note">數值</span><div class="seg"><button class="${R.show === 'pct' ? 'on' : ''}" data-act="retshow" data-arg="pct">比例</button><button class="${R.show === 'n' ? 'on' : ''}" data-act="retshow" data-arg="n">人數</button></div></div>
      <div class="row" style="margin-top:8px"><span class="note">關聯屬性</span><select class="sel" data-act="retassoc"><option value="">不設定</option>${PROPS.filter(p => p.scope === 'event' && p.vals).map(p => `<option value="${p.k}" ${R.assoc === p.k ? 'selected' : ''}>初始與回訪須同一 ${esc(p.cn)}</option>`).join('')}</select></div></div>
    <div class="qsec">${filterBuilder('rt', { title: '全局篩選（用戶屬性／標籤）' })}</div>
    <div class="qsec"><div class="qsec-h"><b>對比與時間</b></div><div class="row"><span class="note">分組</span><select class="sel" data-act="retgroup">${[['none', '不分組'], ['platform', '平台'], ['channel', '渠道'], ['u.tier', '價值分級'], ['u.age_band', '年齡層'], ['u.first_utm_source', '首次來源'], ['segment', '人群分群']].map(g => `<option value="${g[0]}" ${R.group === g[0] ? 'selected' : ''}>${g[1]}</option>`).join('')}</select></div>
      <div class="row" style="margin-top:8px"><span class="note">初始行為時間</span><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div></div></div>
    <div class="acts qacts"><button class="btn-primary" data-act="retrun">查詢</button><button class="btn-ghost" data-act="retsave">儲存報表</button><button class="btn-ghost" data-act="retreset">重置</button></div></div></aside>`;
  const v = x => R.mode === 'churn' ? 1 - x : x; const lbl = R.mode === 'churn' ? '流失' : '留存';
  const tiles = `<div class="grid c4 kpi"><div class="tile"><div class="lbl">第 1 ${unit}平均${lbl}</div><div class="val">${pct(v(avgAt(1)))}</div><div class="delta"><span>${rows.length} 個 cohort · 初始 ${compact(sum(rows.map(r => r.n)))} 人</span></div></div>
    <div class="tile"><div class="lbl">第 ${nMid} ${unit}平均${lbl}</div><div class="val">${pct(v(avgAt(nMid)))}</div><div class="delta"><span>${R.calc === 'range' ? '區間' : '當期'}口徑 · 已滿期最長 ${maxAvail} ${unit}${maxAvail < R.periods ? '（拉長初始行為時間可看更多期）' : ''}</span></div></div>
    <div class="tile"><div class="lbl">最近 cohort 第 1 ${unit}</div><div class="val">${latest.cells[1] != null ? pct(v(latest.cells[1])) : '—'}</div><div class="delta ${latest.cells[1] != null && latest.cells[1] < avgAt(1) - .02 ? 'down' : 'up'}">${latest.cells[1] != null ? `<b>${pt((latest.cells[1] - avgAt(1)) * (R.mode === 'churn' ? -1 : 1))}</b><span>vs 平均（${esc(latest.label)}）</span>` : '<span>尚未滿期</span>'}</div></div>
    <div class="tile"><div class="lbl">最佳／最差 cohort（第 1 ${unit}）</div><div class="val" style="font-size:18px">${best ? esc(best.label) : '—'}<small>${best ? pct(v(best.cells[1])) : ''}</small></div><div class="delta"><span>最差 ${worst ? `${esc(worst.label)} ${pct(v(worst.cells[1]))}` : '—'}</span></div></div></div>`;
  const trendSeries = gvals.length ? gcurves : [{ name: `平均${lbl}率`, color: SER[0], data: curve.slice(0, maxAvail + 1).map((r, j) => ({ d: j === 0 ? '當' + unit : `第 ${j} ${unit}`, v: Math.round(v(avgAt(j)) * 1000) / 10 })) }, ...(R.sel ? [{ name: R.sel, color: SER[1], data: (rows.find(r => r.label === R.sel) || rows[0]).cells.slice(0, maxAvail + 1).map((c, j) => ({ d: j === 0 ? '當' + unit : `第 ${j} ${unit}`, v: c == null ? 0 : Math.round(v(c) * 1000) / 10 })) }] : [])];
  const groupTable = gvals.length ? `<div class="tbl-wrap"><table class="tbl"><tr><th>${R.group === 'segment' ? '分群' : esc(propOf(R.group).cn)}</th><th class="num">初始人數</th>${Array.from({ length: R.periods + 1 }, (_, i) => `<th class="num">${i === 0 ? '當' + unit : `第 ${i} ${unit}`}</th>`).join('')}</tr>${gcurves.map((g, gi) => `<tr><td style="font-weight:600"><i class="sw rect" style="background:${g.color}"></i> ${esc(g.name)}</td><td class="num mono">${n0(sum(retCohorts(R.group + g.name).map(r => r.n)))}</td>${g.data.map(p => `<td class="num mono">${p.v.toFixed(1)}%</td>`).join('')}</tr>`).join('')}</table></div>` : '';
  const res = `<section class="qresult stack">${tiles}
    <div class="card"><div class="card-h"><h3>${esc(initE.cn)} → ${R.retAny ? '任意事件' : esc(retE.cn)}</h3><span class="sub">按${unit} · ${R.calc === 'range' ? `N ${unit}內區間` : '當期'}${lbl} · ${dateRangeText()}${R.assoc ? ` · 關聯 ${esc(propOf(R.assoc).cn)}` : ''}${R.filters.items.length ? ` · 篩選：${esc(filterText(R.filters))}` : ''}</span><div class="right"><div class="seg">${[['table', `${lbl}表`], ['trend', `${lbl}曲線`], ['group', '分組對比']].map(x => `<button class="${R.view === x[0] ? 'on' : ''}" data-act="retview" data-arg="${x[0]}" ${x[0] === 'group' && !gvals.length ? 'disabled' : ''}>${x[1]}</button>`).join('')}</div></div></div>
      ${R.view === 'table' ? retTable(rows) + `<div class="legend" style="margin-top:10px"><span class="k">色階 = ${lbl}率（單一色相由淺到深）</span><span class="k note">點任一格下鑽到該 cohort 當期${lbl}用戶</span></div>` : R.view === 'trend' ? lineChart('ret-trend', trendSeries, { h: 280, fmt: x => x.toFixed(1) + '%', area: trendSeries.length === 1 }) + `<div class="note" style="margin-top:8px">${gvals.length ? '各組的平均曲線。' : '平均曲線；在留存表點選某列可疊加該 cohort。'}</div>` : groupTable}
      <div class="note" style="margin-top:8px">以 ClickHouse retention() 計算：每個 cohort 的用戶是否在第 N ${unit}${R.calc === 'range' ? '內任一天' : '當期'}發生後續行為${R.assoc ? `（且 ${R.assoc} 相同）` : ''}；未滿期的格子留白。</div></div>
    ${R.view !== 'table' ? `<div class="card"><div class="card-h"><h3>${lbl}表</h3></div>${retTable(rows)}</div>` : ''}
    ${R.view !== 'trend' ? `<div class="card"><div class="card-h"><h3>${lbl}曲線</h3><span class="sub">${gvals.length ? '各組對比' : '平均 vs 所選 cohort'}</span></div>${lineChart('ret-trend2', trendSeries, { h: 240, fmt: x => x.toFixed(1) + '%', area: trendSeries.length === 1 })}</div>` : ''}
    <div class="grid c21"><div class="card"><div class="card-h"><h3>相關洞察</h3></div><div class="stack" style="gap:10px">${INSIGHTS.filter(i => i.kind === 'funnel' || i.kind === 'retention').slice(0, 1).map(i => insightCard(i, true)).join('')}<div class="note">最近一個開戶核准 cohort 的第 1 ${unit}交易留存低於平均，與 iOS 23.4 開戶中斷洞察相關；建議對該 cohort 推送「首筆交易手續費折抵」並觀察第 2 ${unit}是否回升。</div></div></div>
    <div class="card"><div class="card-h"><h3>已儲存報表</h3></div><div class="tbl-wrap"><table class="tbl">${[['開戶核准 → 成交（週）', '林主管', '10-10'], ['入金 → 成交（日，14 天）', '王小明', '10-12']].map(r => `<tr class="click"><td><b>${r[0]}</b><span class="sub">${r[1]} · 更新 ${r[2]}</span></td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="已載入報表「${r[0]}」（原型）">開啟</button></td></tr>`).join('')}${R.saved.map(r => `<tr class="click"><td><b>${esc(r)}</b><span class="sub">${esc(S.user.name)} · 剛剛</span></td><td class="num"><span class="pill teal">新</span></td></tr>`).join('')}</table></div></div></div>
  </section>`;
  S._rtSql = `-- retention.run: init=${R.init} return=${R.retAny ? 'any' : R.ret} gran=${R.gran} periods=${R.periods} calc=${R.calc}${R.assoc ? ` assoc=${R.assoc}` : ''}\nSELECT toStartOf${R.gran === 'day' ? 'Day' : R.gran === 'week' ? 'Week' : 'Month'}(c.t) AS cohort, count(DISTINCT c.one_id) AS n,\n  retention(${Array.from({ length: R.periods + 1 }, (_, i) => i === 0 ? 'r.t >= c.t' : R.calc === 'range' ? `r.t < c.t + ${i + 1}*P` : `toStartOf${R.gran === 'day' ? 'Day' : R.gran === 'week' ? 'Week' : 'Month'}(r.t) = toStartOf${R.gran === 'day' ? 'Day' : R.gran === 'week' ? 'Week' : 'Month'}(c.t + ${i}*P)`).slice(0, 4).join(', ')}, ...)   -- P = 1 ${unit}\nFROM (SELECT one_id, min(time) t${R.assoc ? `, any(props.${R.assoc}) a` : ''} FROM dmp.events WHERE event='${R.init}' AND event_date >= today()-${S.range}${R.initFilters.items.length ? ' AND ' + R.initFilters.items.map(c => `${c.k} = '${c.v}'`).join(' AND ') : ''} GROUP BY one_id) c\nLEFT JOIN (SELECT one_id, time t${R.assoc ? `, props.${R.assoc} a` : ''} FROM dmp.events WHERE ${R.retAny ? "event != ''" : `event='${R.ret}'`}${R.retFilters.items.length && !R.retAny ? ' AND ' + R.retFilters.items.map(c => `${c.k} = '${c.v}'`).join(' AND ') : ''}) r USING one_id${R.assoc ? ' WHERE r.a = c.a' : ''}${R.filters.items.length ? `\n${R.assoc ? '  AND' : 'WHERE'} (${R.filters.items.map(c => `${c.k.replace('u.', 'profile.').replace('tag.', 'tags.')} = '${c.v}'`).join(R.filters.logic === 'and' ? ' AND ' : ' OR ')})` : ''}\nGROUP BY cohort${R.group !== 'none' ? `, ${R.group}` : ''} ORDER BY cohort;`;
  return `<div class="page-h"><div><h1>留存分析</h1><p>初始行為 × 後續行為（各自可篩選、可選任意事件）、日／週／月粒度、第 N 期當期或區間留存、留存／流失視角、比例／人數、關聯屬性、全局篩選與分組對比；留存表格子可下鑽。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="retention">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="${esc(initE.cn)}後的${R.retAny ? '活躍' : esc(retE.cn)}留存最近有什麼變化？哪個 cohort 最差、哪個分群最好？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="qlayout">${cfg}${res}</div>`;
}

function pageProfile() {
  const list = PROFILES.filter(canSee).filter(p => !S.profile.q || p.one_id.includes(S.profile.q) || p.cust.includes(S.profile.q));
  const p = PROFILES.find(x => x.one_id === S.profile.sel);
  const unlocked = p && S.profile.unlocked[p.one_id];
  return `<div class="page-h"><div><h1>用戶細查 / OneID</h1><p>單人行為序列與身分圖。分析倉不含姓名、手機、身分證字號；細查需填寫理由並寫入審計（agent 的 profile.* 工具走同一道門）。</p></div></div>
  <div class="grid c2" style="grid-template-columns:360px 1fr"><div class="stack"><div class="card"><div class="filters" style="margin-bottom:10px"><input class="inp" style="flex:1" placeholder="one_id 或客戶代碼" data-act="pq" value="${esc(S.profile.q)}"></div>
    ${list.length ? `<table class="tbl">${list.map(x => `<tr class="click" data-act="psel" data-arg="${x.one_id}"><td><b>${mId(x.one_id)}</b><span class="sub">${esc(x.cust)} · ${esc(x.branch)} · ${esc(x.agent)}</span></td><td class="num"><span class="pill ${x.lifecycle === '沉睡' ? 'amber' : 'teal'}">${x.lifecycle}</span></td></tr>`).join('')}</table>` : '<div class="empty">沒有符合（或不在你的客戶範圍內）</div>'}
    <div class="note" style="margin-top:10px">${S.user.scope === 'all' ? '你的範圍：全域' : '你的範圍：專案限定'}</div></div></div>
  <div class="stack">${p ? `
    <div class="card"><div class="card-h"><h3>${mId(p.one_id)}</h3><span class="pill ${p.lifecycle === '沉睡' ? 'amber' : 'teal'}">${p.lifecycle}</span><span class="pill">${p.fee_tier}</span><span class="pill ${p.churn > 60 ? 'red' : 'gray'}">流失風險 ${p.churn}</span><div class="right">${idMasked() ? `<button class="btn-ghost sm" data-act="reveal" data-arg="one_id / 帳號尾碼">${LOCK} 申請揭露</button>` : ''}<button class="btn-ghost sm" data-act="askpre" data-arg="客戶 ${p.one_id} 的行為摘要與建議聯繫切入點（理由：營業員跟進）">${svg(I.spark, 13)} 請 agent 摘要</button></div></div>
      <dl class="kv"><dt>客戶代碼</dt><dd class="mono">${esc(p.cust)}</dd><dt>帳戶</dt><dd>${p.accounts.map(a => `<span class="pill">${esc(a)}</span>`).join(' ')}</dd><dt>分公司 / 營業員</dt><dd>${esc(p.branch)} / ${esc(p.agent)}</dd><dt>偏好市場 / 年齡層</dt><dd>${esc(p.pref)} / ${esc(p.age)}</dd><dt>開戶日</dt><dd class="mono">${esc(p.since)}</dd></dl></div>
    <div class="card"><div class="card-h"><h3>身分圖（OneID）</h3><span class="sub">${p.identities.length} 個識別子 · 優先級 login_id › mobile › account_no › device › cookie；一個 ID 只屬一個用戶</span></div>${idGraph(p)}</div>
    <div class="card"><div class="card-h"><h3>行為序列</h3><span class="sub">GA 行為與交易事件合併時間軸</span></div>
      ${unlocked ? `<div class="tl">${p.timeline.map(t => `<div class="tl-item ${t[2] === 'trade' ? 'trade' : t[2] === 'mkt' ? 'mkt' : t[2] === 'sys' ? 'sys' : ''}"><div class="t">${t[0]}</div><div class="e"><b>${esc(t[3])}</b> <span class="pill gray mono" style="font-size:10px">${esc(t[1])}</span><span class="p">${esc(t[4])}</span></div></div>`).join('')}</div><div class="legend"><span class="k"><i class="sw" style="background:var(--s1)"></i>GA 行為</span><span class="k"><i class="sw" style="background:var(--s2)"></i>交易</span><span class="k"><i class="sw" style="background:var(--s5)"></i>行銷</span><span class="k"><i class="sw" style="background:var(--muted)"></i>系統</span></div>`
        : `<div class="note">查看個人行為序列需填寫理由；本次查詢會以你的身份寫入審計（profile.timeline）。</div><div class="filters" style="margin-top:10px"><input class="inp wide" placeholder="查詢理由（例：客戶來電詢問 10/8 委託失敗原因）" data-act="preason" value="${esc(S.profile.reason)}"><button class="btn-primary" data-act="punlock" data-arg="${p.one_id}">解鎖並記錄審計</button></div>`}</div>`
    : '<div class="card"><div class="empty">從左側選擇一位客戶</div></div>'}</div></div>`;
}

function pageSegments() {
  const t = S.seg.tab;
  const stPill = s => { const st = S.approved[s.id] ? 'active' : s.status; return st === 'active' ? '<span class="pill green">生效</span>' : '<span class="pill amber">待審批</span>'; };
  return `<div class="page-h"><div><h1>標籤與分群</h1><p>標籤：規則／統計／SQL／模型／即時／匯入。分群以 Bitmap 交併差計算；agent 提議的分群需審批才生效。</p></div><div class="right"><button class="btn-ghost" data-act="toast" data-arg="原型：新增標籤精靈">＋ 新增標籤</button><button class="btn-primary" data-act="toast" data-arg="原型：分群規則編輯器">＋ 新增分群</button></div></div>
  <div class="filters"><div class="seg"><button class="${t === 'segments' ? 'on' : ''}" data-act="segtab" data-arg="segments">分群 ${SEGMENTS.length}</button><button class="${t === 'tags' ? 'on' : ''}" data-act="segtab" data-arg="tags">標籤 ${TAGS.length}</button><button class="${t === 'destinations' ? 'on' : ''}" data-act="segtab" data-arg="destinations">推送目的地 ${DESTINATIONS.filter(d => d.status === 'active').length}</button></div></div>
  ${t === 'destinations' ? pageDestinations() : t === 'segments' ? `<div class="card flush"><table class="tbl"><tr><th>分群</th><th>規則</th><th class="num">人數</th><th>建立者</th><th>更新</th><th>狀態</th><th>推送</th></tr>${SEGMENTS.map(s => `<tr class="click" data-act="segopen" data-arg="${s.id}"><td><b>${esc(s.name)}</b>${s.rt ? ' <span class="pill teal">即時</span>' : ''}</td><td class="mono" style="font-size:11.5px;color:var(--body)">${esc(s.rule)}</td><td class="num mono">${n0(s.n)}</td><td>${s.by.includes('-') ? `<span class="pill purple">${svg(I.spark, 11)} ${esc(s.by)}</span>` : esc(s.by)}</td><td style="color:var(--muted)">${esc(s.upd)}</td><td>${stPill(s)}</td><td class="num">${s.status === 'pending_approval' && !S.approved[s.id] ? (S.user.can.approve ? `<button class="btn-ghost sm" data-act="approve" data-arg="${s.id}">核准</button>` : '<span class="note">需主管核准</span>') : `<button class="btn-ghost sm" data-act="pushopen" data-arg="${s.id}">${AUDIENCE_PUSHES.some(p => p.seg === s.id) ? `推送 · ${AUDIENCE_PUSHES.find(p => p.seg === s.id).dest.length} 個目的地` : '推送'}</button>`}</td></tr>`).join('')}</table></div>`
  : `<div class="card flush"><table class="tbl"><tr><th>標籤</th><th>類型</th><th>值域</th><th class="num">覆蓋人數</th><th>更新</th><th>負責人</th></tr>${TAGS.map(x => `<tr><td><b>${esc(x.cn)}</b><span class="sub mono">${esc(x.id)}</span></td><td><span class="pill ${x.rt ? 'teal' : x.type === '模型' ? 'purple' : x.type === 'SQL' ? 'blue' : 'gray'}">${esc(x.type)}</span></td><td style="color:var(--body)">${x.vals.map(v => `<span class="pill" style="margin:1px">${esc(v)}</span>`).join('')}</td><td class="num mono">${n0(x.cover)}</td><td style="color:var(--muted)">${esc(x.upd)}</td><td>${esc(x.owner)}</td></tr>`).join('')}</table></div>`}`;
}
function segDrawer() {
  const s = SEGMENTS.find(x => x.id === S.segDrawer); if (!s) return '';
  const sql = `SELECT bitmapCardinality(\n  bitmapAnd(\n    (SELECT groupBitmapMergeState(users) FROM dmp.user_tags WHERE tag_id='fee_tier' AND tag_value IN ('P1','P2')),\n    (SELECT groupBitmapState(one_id_uint) FROM dmp.events\n      WHERE event='app_login_success' AND event_date >= today()-30 GROUP BY 1 HAVING count()=0)\n  ))`;
  return `<div class="drawer-bg" data-act="segclose"></div><div class="drawer"><div class="hd"><b>${esc(s.name)}</b><span class="pill">${n0(s.n)} 人</span><button class="icon-btn x" data-act="segclose">✕</button></div><div class="bd stack">
    <dl class="kv"><dt>規則</dt><dd class="mono">${esc(s.rule)}</dd><dt>建立者</dt><dd>${esc(s.by)}</dd><dt>更新</dt><dd>${esc(s.upd)}</dd><dt>狀態</dt><dd>${S.approved[s.id] ? '生效' : s.status === 'active' ? '生效' : '待審批'}</dd></dl>
    <div><div class="note" style="margin-bottom:6px"><b>群體洞察（vs 全體）</b></div>${hbar([{ k: '45–54 歲', v: 41 }, { k: '偏好 US', v: 38 }, { k: '偏好 TSE', v: 35 }, { k: '定期定額', v: 64 }, { k: 'iOS', v: 52 }], { fmt: v => v + '%', color: SER[2], total: 1e9 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '')}</div>
    <div><div class="note" style="margin-bottom:6px"><b>編譯後 SQL（Bitmap）</b></div><div class="code">${esc(sql)}</div></div>
    ${s.by.includes('-') ? `<div class="note">${svg(I.spark, 12)} 由 agent <b>${esc(s.by)}</b> 透過 segment.propose 提議；證據見 AI 洞察頁。</div>` : ''}
    <div class="acts" style="display:flex;gap:8px">${s.status === 'pending_approval' && !S.approved[s.id] && S.user.can.approve ? `<button class="btn-primary" data-act="approve" data-arg="${s.id}">核准並生效</button>` : ''}<button class="btn-ghost" data-act="askpre" data-arg="分群「${esc(s.name)}」這群人有什麼共同行為特徵？建議怎麼接觸？">${svg(I.spark, 13)} 問 agent</button><button class="btn-ghost" data-act="pushopen" data-arg="${s.id}">推送到目的地</button></div></div></div>`;
}

function pageTriggers() {
  return `<div class="page-h"><div><h1>即時觸發</h1><p>Flink CEP 規則 → Kafka dmp.triggers；下游：MA、內部系統 Webhook、Insight Agent；受眾包名單推送（Webhook／Kafka／GCS）見「標籤與分群 › 推送目的地」。</p></div><div class="right"><button class="btn-primary" data-act="toast" data-arg="原型：規則 DSL 編輯器">＋ 新增規則</button></div></div>
  <div class="grid c2"><div class="card flush"><table class="tbl"><tr><th>規則</th><th>DSL</th><th>受眾</th><th>輸出</th><th class="num">24h 觸發</th><th>狀態</th></tr>${TRIGGERS.map(t => `<tr><td><b>${esc(t.name)}</b><span class="sub">節流 ${esc(t.throttle)}</span></td><td class="mono" style="font-size:11px;color:var(--body)">${esc(t.dsl)}</td><td>${esc(t.audience)}</td><td style="font-size:11.5px">${esc(t.out)}</td><td class="num mono">${n0(t.fires24h)}</td><td>${t.status === 'on' || S.approved[t.id] ? '<span class="pill green">啟用</span>' : (S.user.can.approve ? `<button class="btn-ghost sm" data-act="approve" data-arg="${t.id}">核准</button>` : '<span class="pill amber">待審批</span>')}</td></tr>`).join('')}</table></div>
  <div class="card"><div class="card-h"><h3>觸發串流</h3><span class="sub">dmp.triggers · 即時</span><span class="status ok" style="margin-left:auto"><span class="dot"></span>P95 延遲 2.8 秒</span></div><table class="tbl">${TRIGGER_FEED.map(f => `<tr><td class="mono" style="color:var(--muted);width:80px">${f.t}</td><td><b>${esc(f.rule)}</b><span class="sub mono">${esc(f.one)} · ${esc(f.payload)}</span></td></tr>`).join('')}</table></div></div>`;
}

function pageAI() {
  const t = S.ai.tab;
  const tabs = [['insights', '洞察串流'], ['ask', '問答'], ['agents', 'Agents'], ['tools', '工具目錄']];
  let body = '';
  if (t === 'insights') {
    const list = INSIGHTS.filter(i => (S.ai.kind === 'all' || i.kind === S.ai.kind) && (S.ai.status === 'all' || i.status === S.ai.status));
    body = `<div class="filters"><div class="seg">${[['open', '待處理'], ['all', '全部']].map(x => `<button class="${S.ai.status === x[0] ? 'on' : ''}" data-act="aistatus" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div><div class="seg">${[['all', '全部類型'], ['anomaly', '異常'], ['funnel', '漏斗'], ['risk', '風險'], ['opportunity', '機會'], ['campaign', '活動']].map(x => `<button class="${S.ai.kind === x[0] ? 'on' : ''}" data-act="aikind" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div><button class="btn-ghost sm" data-act="toast" data-arg="已訂閱：高以上嚴重度 → LINE + 站內">${svg(I.bell, 13)} 訂閱</button></div>
    <div class="ai-layout"><div class="stack" style="gap:12px">${list.map(i => insightCard(i)).join('') || '<div class="card"><div class="empty">沒有符合的洞察</div></div>'}</div>
    <div class="stack"><div class="card"><div class="card-h"><h3>洞察如何產生</h3></div><div class="note">排程／提問／事件 → <b>Planner</b>（讀 skill + semantic.search）→ <b>Executor</b>（工具閘道：權限、預算、脫敏）→ <b>Verifier</b>（交叉重跑、樣本量、口徑 caveats）→ <b>Narrator</b> → Insight Store → 訂閱與回饋。</div><div class="code" style="margin-top:10px">{ kind, title, summary, hypothesis,\n  evidence[]: { tool, args, digest, sql_hash },\n  chart_spec, scope, confidence, severity,\n  actions[]: { type, status },\n  created_by: { agent, run_id, on_behalf_of } }</div></div>
    <div class="card"><div class="card-h"><h3>近 7 天</h3></div>${hbar(AGENTS.map(a => ({ k: a.cn, v: a.insights7d })), { fmt: n0, color: SER[6] })}<div class="note" style="margin-top:8px">回饋：有用 71% · 有誤 6% · 採納為行動 23%</div></div></div></div>`;
  } else if (t === 'ask') {
    body = `<div class="ai-layout"><div class="card chat"><div class="log" id="chatlog">${S.ai.chat.length ? S.ai.chat.map(m => m.who === 'user' ? `<div class="msg user"><div class="av">${S.user.init}</div><div class="bub">${esc(m.text)}</div></div>` : `<div class="msg"><div class="av">AI</div><div class="bub"><div class="plan">計畫：${esc(m.plan)}</div>${m.card ? `<div class="cfg"><div class="hd"><span class="pill blue">${esc(m.card.model)}</span><b>分析配置卡（選擇題式）</b><span class="pill gray">信心 ${Math.round(m.card.conf * 100)}%</span></div><dl>${m.card.params.map(p => `<dt>${esc(p[0])}</dt><dd>${esc(p[1])}</dd>`).join('')}</dl><div class="alt">其他候選：${m.card.alt.map(esc).join('；')}</div>${m.final ? `<div class="acts"><button class="btn-ghost sm" data-act="toast" data-arg="已在分析頁開啟此配置">在分析頁開啟</button><button class="btn-ghost sm" data-act="toast" data-arg="修改後重跑；已記錄 feedback.record（question → final_config）">修改配置並重跑</button></div>` : ''}</div>` : ''}${m.calls.slice(0, m.done).map((c, k) => `<div class="call"><span class="st ${k === m.done - 1 && m.done < m.calls.length + 1 && !m.final ? 'run' : ''}"></span><b>${esc(c[0])}</b><span style="color:var(--muted)">${esc(c[1])}</span><span class="cost">${esc(c[2])}</span></div>`).join('')}${m.final ? `<div class="ans">${m.ans}</div>${m.warn ? `<div class="warn">${svg(I.warn, 12)} ${esc(m.warn)}</div>` : ''}<div class="acts">${m.acts.map(a => `<button class="btn-ghost sm" data-act="chatact" data-arg="${esc(a)}">${esc(a)}</button>`).join('')}</div>` : ''}</div></div>`).join('') : `<div class="empty">以自然語言提問。agent 會先在語意層找到對應指標，再呼叫工具取數，最後附上證據與注意事項。<br>數字只能來自工具回傳；個資與細查受同一套權限守門。</div>`}</div>
      <div class="sug">${ASK_SCRIPTS.map(s => `<button data-act="asksug" data-arg="${esc(s.q)}">${esc(s.q)}</button>`).join('')}</div>
      <div class="in"><input id="askin" placeholder="例：上週開戶到入金的轉化率？" value="${esc(S.ai.prefill)}" data-act="askinput" ${S.ai.busy ? 'disabled' : ''}><button class="btn-primary teal" data-act="asksend" ${S.ai.busy ? 'disabled' : ''}>${svg(I.send, 14)} 送出</button></div></div>
    <div class="stack"><div class="card"><div class="card-h"><h3>這次問答的身份與預算</h3></div><dl class="kv"><dt>代表</dt><dd>${esc(S.user.name)}（${esc(S.user.role)}）</dd><dt>行級規則</dt><dd>${S.user.scope === 'all' ? '無' : 'branch_id IN (TPE01)'}</dd><dt>掃描預算</dt><dd>100 GB / run</dd><dt>LLM tokens</dt><dd>50k / run</dd><dt>敏感工具</dt><dd>profile.* 需理由並審計</dd><dt>可提議</dt><dd>segment / trigger（需審批）</dd></dl></div>
    <div class="card"><div class="card-h"><h3>Golden evals</h3><span class="sub">換模型或改 skill 前必須通過</span></div>${hbar([{ k: '口徑正確', v: 96 }, { k: '工具選擇', v: 93 }, { k: '個資守門', v: 100 }, { k: 'caveat 提及', v: 88 }], { fmt: v => v + '%', color: SER[2], total: 1e9 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '')}</div></div></div>`;
  } else if (t === 'agents') {
    body = `<div class="grid c2"><div class="card">${AGENTS.map(a => `<div class="agent-row"><div><b>${esc(a.cn)}</b> <span class="pill gray mono" style="font-size:10px">${esc(a.id)}</span><div class="d">觸發：${esc(a.trigger)} · 工具 ${a.tools} 個 · 身份 ${esc(a.identity)} · 24h 執行 ${a.runs24h} 次 · 7 天洞察 ${a.insights7d} 則</div><div class="budget" title="今日預算用量"><i style="width:${Math.round(a.budget * 100)}%"></i></div></div><div style="display:flex;gap:6px"><button class="btn-ghost sm" data-act="toast" data-arg="已手動執行 ${esc(a.id)}（POST /v1/agents/${esc(a.id)}/run）">執行</button><button class="btn-ghost sm" data-act="toast" data-arg="原型：skill Markdown 編輯器">Skill</button></div></div>`).join('')}</div>
    <div class="stack"><div class="card"><div class="card-h"><h3>新增一個 agent</h3></div><div class="note">1. 寫 skill（Markdown：目標、步驟、驗證規則、輸出格式、禁止事項）<br>2. 在 agents.yaml 登記：觸發（排程／提問／事件）、允許工具、身份、預算<br>3. 跑 golden evals；通過後啟用。<br><br>不需要改 runtime 程式；新能力以「工具」加入工具閘道。</div><div class="code" style="margin-top:10px">- id: churn-radar\n  skill: skills/churn-radar.md\n  trigger: { type: schedule, cron: "0 7 * * *" }\n  identity: svc-insight-readonly\n  tools: [metric.query, retention.run, segment.preview, segment.propose, insight.create]\n  budget: { scan_bytes: 100e9, llm_tokens: 40000 }</div></div>
    <div class="card"><div class="card-h"><h3>LLM Gateway</h3></div><dl class="kv"><dt>路由</dt><dd>enterprise-gateway → default-insight</dd><dt>備援</dt><dd>local-small（離線）</dd><dt>金鑰</dt><dd>Vault，agent 不持有</dd><dt>輸出守門</dt><dd>個資正規式攔截、數字對照工具回傳</dd></dl></div></div></div>`;
  } else {
    body = `<div class="grid c2"><div class="card">${TOOLS.map(t => `<div class="tool"><code>${esc(t[0])}</code><span class="d">${esc(t[1])}</span><span class="pill ${({ read: 'gray', propose: 'amber', sensitive: 'red', write: 'blue', execute: 'purple' })[t[2]]}">${({ read: '讀', propose: '提議（審批）', sensitive: '敏感（理由＋審計）', write: '寫', execute: '執行（審批後）' })[t[2]]}</span></div>`).join('')}</div>
    <div class="stack"><div class="card"><div class="card-h"><h3>工具閘道（MCP）</h3></div><div class="note">每個工具 = schema + 權限範圍 + 預算 + 脫敏策略 + 審計。UI 與 agent 走同一條語意層 API，口徑與權限一致。外部系統（Claude Code、MA）可直接以 MCP client 連線。</div><div class="code" style="margin-top:10px">gateway:\n  budgets: { per_call_scan_bytes: 20e9, per_run_scan_bytes: 100e9 }\n  policy:  { identity: on_behalf_of, masking: column_level,\n             row_level: from_dimensions, audit_event: agent.tool_called }</div></div>
    <div class="card"><div class="card-h"><h3>每個工具的回傳</h3></div><div class="code">{ data, chart_spec?, sql?, lineage[],\n  cost: { scan_bytes, ms }, warnings[] }</div><div class="note" style="margin-top:8px">agent 必須把 warnings（樣本小、streaming 缺歸因、口徑 caveats）帶進結論。</div></div></div></div>`;
  }
  return `<div class="page-h"><div><h1>AI 洞察</h1><p>agent 透過語意層與工具閘道讀資料、提洞察、提議行動；洞察是一級資料物件，可訂閱、回饋、再利用。</p></div></div>
  <div class="filters"><div class="seg">${tabs.map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="aitab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

/* ================================================================== v0.5：元事件管理／屬性字典／用戶表／Item 表／資料流與效能／使用者指派 */
const SENS_CN = { L0: '聚合', L1: '行為', L2: '敏感業務', L3: '代碼化識別', L4: '可識別（禁入倉）' };
const STATUS_PILL = { live: '<span class="pill green">上線</span>', draft: '<span class="pill gray">草稿</span>', quarantine: '<span class="pill red">隔離</span>', deprecated: '<span class="pill amber">停用</span>', blocked: '<span class="pill red">拒絕入倉</span>' };
const sensPill = s => `<span class="level ${s}" title="${esc(SENS_CN[s] || '')}">${s}</span>`;
const govWrite = () => has('governance', 'd');

/* ---- 元事件編輯抽屜 */
function openMeta(name) {
  const e = name ? EVENTS.find(x => x.name === name) : null;
  S.meta = e ? { isNew: false, orig: e.name, name: e.name, cn: e.cn, entity: e.entity, source: e.source, kind: e.kind, owner: e.owner === '—' ? '' : e.owner, status: e.status, props: e.props.map(p => ({ k: p, type: (META_PROPS.find(m => m.k === p) || {}).type || 'string', sens: (META_PROPS.find(m => m.k === p) || {}).sens || 'L1' })), node: e.name === '$pageview' ? 'page_group' : e.name === '$AppViewScreen' ? '$screen_name' : '', note: e.note || '' }
    : { isNew: true, name: '', cn: '', entity: 'user', source: 'GA360', kind: 'behavior', owner: '', status: 'draft', props: [], node: '', note: '' };
  render();
}
function metaDrawer() {
  const m = S.meta; if (!m) return ''; const errs = [];
  if (!/^[a-z][a-z0-9_.]{2,47}$/.test(m.name)) errs.push('事件名稱須為小寫英數與 _ .（3–48 字元）');
  if (m.isNew && EVENTS.some(e => e.name === m.name)) errs.push('事件名稱已存在');
  if (!m.cn.trim()) errs.push('請填顯示名稱'); if (!m.owner.trim()) errs.push('請填負責人');
  if (m.props.some(p => p.sens === 'L4')) errs.push('含 L4 屬性：個資不得入倉，請移除或改為雜湊／分桶後的 L3／L2 屬性');
  const canPublish = !errs.length;
  return `<div class="drawer-bg" data-act="metaclose"></div><div class="drawer wide"><div class="hd"><b>${m.isNew ? '登記新事件' : `編輯事件：${esc(m.orig)}`}</b>${STATUS_PILL[m.status]}<button class="icon-btn x" data-act="metaclose">✕</button></div><div class="bd stack">
    <div class="form"><label>事件名稱</label><input class="inp mono" data-act="metaf" data-arg="name" value="${esc(m.name)}" placeholder="例：fund_redeem（上線後不可改名，只能停用）" ${m.isNew ? '' : 'disabled'}>
      <label>顯示名稱</label><input class="inp" data-act="metaf" data-arg="cn" value="${esc(m.cn)}">
      <label>主體</label><div class="seg">${[['user', '用戶'], ['account', '帳戶（Item）'], ['item', '其他 Item']].map(x => `<button class="${m.entity === x[0] ? 'on' : ''}" data-act="metaseg" data-arg="entity:${x[0]}">${x[1]}</button>`).join('')}</div>
      <label>類別</label><div class="seg">${[['behavior', '行為'], ['business', '業務'], ['trade', '交易'], ['marketing', '行銷']].map(x => `<button class="${m.kind === x[0] ? 'on' : ''}" data-act="metaseg" data-arg="kind:${x[0]}">${x[1]}</button>`).join('')}</div>
      <label>來源</label><select class="sel" data-act="metaf" data-arg="source">${['GA360', 'App 後台', '開戶系統', '交易 CDC', '帳務 CDC', '基金平台', 'MA', 'CRM', '業務系統'].map(s => `<option ${m.source === s ? 'selected' : ''}>${s}</option>`).join('')}</select>
      <label>負責人</label><input class="inp" data-act="metaf" data-arg="owner" value="${esc(m.owner)}" placeholder="團隊或人">
      <label>路徑節點規則</label><select class="sel" data-act="metaf" data-arg="node"><option value="">事件本身為節點</option>${m.props.map(p => `<option value="${p.k}" ${m.node === p.k ? 'selected' : ''}>按 ${p.k} 拆分</option>`).join('')}</select>
      <label>說明</label><input class="inp" data-act="metaf" data-arg="note" value="${esc(m.note)}"></div>
    <div class="qsec"><div class="qsec-h"><b>事件屬性</b><span class="note">屬性須先存在於屬性字典；新屬性在此登記會同步進字典，型別與敏感等級一經上線不可降級</span></div>
      <div class="tbl-wrap"><table class="tbl"><tr><th>屬性</th><th>型別</th><th>敏感等級</th><th></th></tr>${m.props.map((p, i) => `<tr><td class="mono">${esc(p.k)}${META_PROPS.some(x => x.k === p.k) ? '' : ' <span class="pill amber">新</span>'}</td><td><select class="sel xs" data-act="metaprop" data-arg="${i}:type">${['string', 'number', 'bool', 'datetime', 'list'].map(t => `<option ${p.type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></td><td><select class="sel xs" data-act="metaprop" data-arg="${i}:sens">${['L0', 'L1', 'L2', 'L3', 'L4'].map(s => `<option value="${s}" ${p.sens === s ? 'selected' : ''}>${s} ${SENS_CN[s]}</option>`).join('')}</select></td><td class="num"><button class="icon-btn x sm" data-act="metapropdel" data-arg="${i}">✕</button></td></tr>`).join('')}</table></div>
      <div class="row" style="margin-top:8px"><select class="sel" data-act="metapropadd"><option value="">＋ 從屬性字典加入</option>${META_PROPS.filter(x => x.status === 'live' && !m.props.some(p => p.k === x.k)).map(x => `<option value="${x.k}">${esc(x.cn)}（${x.k}）</option>`).join('')}</select><input class="inp" data-act="metanewprop" placeholder="或輸入新屬性名稱後按 Enter"></div></div>
    <div class="qsec"><div class="qsec-h"><b>上線檢查</b></div>${errs.length ? `<ul class="priv" style="color:var(--st-serious)">${errs.map(e => `<li>${esc(e)}</li>`).join('')}</ul>` : '<div class="pill green">可上線：命名、負責人、屬性型別與敏感等級皆完整；語意層將自動產生事件定義與 ga4_mapping 條目</div>'}
      <div class="note" style="margin-top:8px">上線後：事件進語意層（agent 可查）、Collector 放行、Flink 事件化套用；改屬性型別或敏感等級需走審批（變更單）；停用 = 不再接收但保留歷史。</div></div>
    </div><div class="ft">${!m.isNew && m.status === 'live' ? `<button class="btn-ghost danger" data-act="metastatus" data-arg="deprecated">停用</button>` : ''}${m.status === 'quarantine' ? `<span class="pill red">隔離區事件：登記後即放行</span>` : ''}<div class="right"><button class="btn-ghost" data-act="metaclose">取消</button>${m.status !== 'live' ? `<button class="btn-ghost" data-act="metasave" data-arg="draft">存為草稿</button>` : ''}<button class="btn-primary" data-act="metasave" data-arg="live" ${canPublish ? '' : 'disabled'}>${m.status === 'live' ? '送出變更單' : '登記並上線'}</button></div></div></div>`;
}
function metaSave(status) {
  const m = S.meta; const props = m.props.map(p => p.k);
  m.props.forEach(p => { if (!META_PROPS.some(x => x.k === p.k)) META_PROPS.push({ k: p.k, cn: p.k, type: p.type, scope: 'event', sens: p.sens, src: m.source, usedBy: 1, status: status === 'live' ? 'live' : 'draft' }); });
  if (m.isNew) EVENTS.push({ name: m.name, cn: m.cn, entity: m.entity, source: m.source, kind: m.kind, status, daily: 0, props, owner: m.owner, note: m.note });
  else { const e = EVENTS.find(x => x.name === m.orig); const wasLive = e.status === 'live'; Object.assign(e, { cn: m.cn, entity: m.entity, source: m.source, kind: m.kind, owner: m.owner, props, note: m.note }); if (!wasLive) e.status = status; if (wasLive) { S.meta = null; toast('已送出變更單（事件定義變更需資料治理審批）；審計 meta.event.change_requested'); return; } }
  S.meta = null; toast(status === 'live' ? `事件 ${m.name} 已上線：語意層、Collector 白名單、Flink 事件化已更新；審計 meta.event.published` : `事件 ${m.name} 已存為草稿`);
}

/* ---- 資料治理（元事件管理） */
function pageGovernance() {
  const t = S.gov.tab; const q = (S.gov.q || '').toLowerCase(); const st = S.gov.status || 'all';
  let body = '';
  if (t === 'dictionary') {
    const list = EVENTS.filter(e => (st === 'all' || e.status === st) && (!q || e.name.toLowerCase().includes(q.toLowerCase()) || e.cn.includes(q) || (e.group || '').includes(q)));
    const counts = Object.fromEntries(['live', 'draft', 'quarantine', 'deprecated'].map(s => [s, EVENTS.filter(e => e.status === s).length]));
    body = `<div class="filters"><input class="inp" placeholder="搜尋事件名稱／顯示名／業務線（例：開戶、sip_、$Plan）" data-act="govq" value="${esc(S.gov.q || '')}"><div class="seg">${[['all', `全部 ${EVENTS.length}`], ['live', `上線 ${counts.live}`], ['draft', `草稿 ${counts.draft}`], ['quarantine', `隔離 ${counts.quarantine}`], ['deprecated', `停用 ${counts.deprecated}`]].map(x => `<button class="${st === x[0] ? 'on' : ''}" data-act="govstatus" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div><span style="margin-left:auto" class="note">${EVENTS.length} 個事件（客戶神策清單 112 + CDC 補充）· 日均 ${compact(sum(EVENTS.map(e => e.daily)))} 事件 · ${META_PROPS.filter(p => p.status === 'live').length} 個屬性</span></div>
    <div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>事件</th><th>業務線</th><th>平台</th><th>類別</th><th>來源</th><th>屬性</th><th class="num">日均量</th><th>負責人</th><th>狀態</th><th></th></tr>${list.map(e => `<tr><td><b class="mono">${esc(e.name)}</b><span class="sub">${esc(e.cn)}${e.aliases && e.aliases.length ? ' · 舊名 ' + esc(e.aliases.join('、')) : ''}${e.note ? ' · ' + esc(e.note) : ''}</span></td><td>${esc(e.group || '')}</td><td class="mono" style="font-size:10.5px;color:var(--muted)">${esc((e.platforms || '').replace(/JavaScript/g, 'JS').replace(/服務端/g, 'Server'))}</td><td><span class="pill">${({ behavior: '行為', business: '業務', trade: '交易', marketing: '行銷' })[e.kind] || e.kind}</span></td><td>${esc(e.source)}</td><td style="max-width:260px">${e.props.slice(0, 6).map(p => { const mp = META_PROPS.find(x => x.k === p); return `<span class="pill mono ${mp && mp.sens === 'L3' ? 'amber' : mp && mp.sens === 'L2' ? 'purple' : ''}" style="font-size:10px;margin:1px" title="${mp ? esc(mp.cn + ' · ' + mp.sens) : ''}">${esc(p)}</span>`; }).join('')}${e.props.length > 6 ? `<span class="note">+${e.props.length - 6}</span>` : ''}</td><td class="num mono">${e.daily ? compact(e.daily) : '—'}</td><td>${esc(e.owner)}</td><td>${STATUS_PILL[e.status] || e.status}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="metaopen" data-arg="${e.name}">${govWrite() ? (e.status === 'quarantine' ? '登記' : '編輯') : '檢視'}</button></td></tr>`).join('') || '<tr><td colspan="9"><div class="empty">沒有符合的事件</div></td></tr>'}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>事件生命週期</h3></div><div class="note">草稿 → 上線（檢查：命名、負責人、屬性型別／敏感等級、無 L4）→ 變更需審批 → 停用（保留歷史）。未登記事件由 Collector 攔進隔離區，日批統計後通知負責人登記；30 天未登記自動丟棄。</div></div><div class="card"><div class="card-h"><h3>與語意層的關係</h3></div><div class="note">事件字典是 semantic/events.yaml 的來源：上線事件自動出現在事件分析／漏斗的事件選單、agent 的工具目錄；ga4_mapping 依「來源=GA360」的事件自動產生映射樣板。</div></div><div class="card"><div class="card-h"><h3>變更管控</h3></div><div class="note">屬性型別只能放寬（int → number）不能收窄；敏感等級只能升不能降；改名視同新事件。所有變更寫審計 meta.event.*。</div></div></div>`;
  } else if (t === 'props') {
    const list = META_PROPS.filter(p => !q || p.k.includes(q) || p.cn.includes(q));
    body = `<div class="filters"><input class="inp" placeholder="搜尋屬性" data-act="govq" value="${esc(S.gov.q || '')}"><span style="margin-left:auto" class="note">L4 屬性一律拒絕入倉；L3 以 HMAC／FPE 代碼化；L2 匯出區間化</span></div>
    <div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>屬性</th><th>型別</th><th>敏感等級</th><th>來源</th><th>枚舉／單位</th><th>維度表／字典</th><th class="num">使用事件數</th><th>狀態</th></tr>${list.map(p => `<tr><td><b class="mono">${esc(p.k)}</b><span class="sub">${esc(p.cn)}${p.note ? ' · ' + esc(p.note) : ''}</span></td><td class="mono">${p.type}</td><td>${sensPill(p.sens)}</td><td>${esc(p.src)}</td><td class="mono" style="font-size:11px">${p.enum ? esc(p.enum.join(' | ')) : p.unit ? esc(p.unit) : '—'}</td><td>${p.item ? `<button class="lnk" data-act="goto" data-arg="#/items/${p.item.startsWith('dict:') ? 'dicts' : p.item}">${esc(p.item.replace('dict:', '字典：'))}</button>` : '—'}</td><td class="num mono">${p.usedBy}</td><td>${STATUS_PILL[p.status] || p.status}</td></tr>`).join('')}</table></div></div>`;
  } else if (t === 'virtual') {
    body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>虛擬事件</th><th>定義</th><th>負責人</th><th>被使用</th><th></th></tr>${VIRTUAL_EVENTS.map(v => `<tr><td><b class="mono">${esc(v.name)}</b><span class="sub">${esc(v.cn)}</span></td><td class="mono" style="font-size:11.5px">${esc(v.def)}</td><td>${esc(v.owner)}</td><td>${v.usedBy.map(u => `<span class="pill">${esc(u)}</span>`).join(' ')}</td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="原型：虛擬事件編輯器（事件 OR 組合＋屬性條件）將於正式版提供">編輯</button></td></tr>`).join('')}</table></div></div>
    <div class="note" style="margin-top:10px">虛擬事件 = 多個真實事件（可帶屬性條件）的 OR 組合，在語意層展開，不另存資料；可用於事件分析、漏斗、留存與標籤定義。</div>`;
  } else if (t === 'sources') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>資料源</th><th>型態</th><th>延遲</th><th class="num">24h 事件</th><th>備註</th><th>狀態</th></tr>${SOURCES.map(s => `<tr><td><b>${esc(s.name)}</b></td><td><span class="pill">${({ batch: '批次', stream: '串流', api: 'API' })[s.kind]}</span></td><td class="mono">${esc(s.lag)}</td><td class="num mono">${n0(s.events24h)}</td><td style="color:var(--body)">${esc(s.note)}</td><td><span class="status ${s.status}"><span class="dot"></span>${s.status === 'ok' ? '正常' : '注意'}</span></td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="tile"><div class="lbl">GA360 對帳差異（昨日）</div><div class="val">0.3%</div><div class="delta"><span>日批 vs 串流匯出，門檻 1%</span></div></div><div class="tile"><div class="lbl">OneID 打通率</div><div class="val">87.4%</div><div class="delta"><span>GA 事件可對應到客戶 ID 的比例</span></div></div><div class="tile"><div class="lbl">隔離事件</div><div class="val">${EVENTS.filter(e => e.status === 'quarantine').length}</div><div class="delta"><span>未登記事件種類，待登記</span></div></div></div>`;
  else body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>時間</th><th>主體</th><th>動作</th><th>對象</th><th>理由 / 參數</th><th class="num">掃描</th></tr>${[
    ['14:31:08', 'agent anomaly-watch（svc-insight-readonly）', 'agent.tool_called metric.query', 'order_reject_rate by platform,reason_code', 'run_8f3a…', '1.8 GB'],
    ['14:20:41', '林雅婷', 'query.events', 'traders by platform', 'UI', '6.3 GB'],
    ['14:05:12', '王信宏', 'profile.timeline', 'oid_7k3a9q', '客戶來電詢問 10/8 委託失敗原因', '12 MB'],
    ['13:58:02', 'agent ask（代表 陳柏宇）', 'segment.propose', '高價值沉睡客', 'pending_approval', '—'],
    ['13:40:19', '張敏慧', 'tag.update', 'fee_tier（SQL 標籤）', '調整分級門檻', '—'],
    ['10:02:11', '張敏慧', 'meta.event.published', 'sip_purchase v2（+plan_type）', '變更單 cr_0219 核准', '—'],
    ['07:04:55', 'agent churn-radar', 'insight.create', 'ins_03', 'conf 0.72', '—'],
  ].map(r => `<tr><td class="mono" style="color:var(--muted)">${r[0]}</td><td>${esc(r[1])}</td><td class="mono" style="font-size:11.5px">${esc(r[2])}</td><td>${esc(r[3])}</td><td style="color:var(--body)">${esc(r[4])}</td><td class="num mono">${esc(r[5])}</td></tr>`).join('')}</table></div></div>`;
  return `<div class="page-h"><div><h1>元事件管理</h1><p>事件字典是語意層的根：事件、屬性字典、虛擬事件、資料源與審計。未登記事件進隔離區；變更走審批；L4 個資屬性拒絕入倉。</p></div><div class="right">${govWrite() ? '<button class="btn-primary" data-act="metanew">＋ 登記事件</button>' : '<span class="pill gray">唯讀</span>'}</div></div>
  <div class="filters"><div class="seg">${[['dictionary', '事件字典'], ['props', '屬性字典'], ['virtual', '虛擬事件'], ['sources', '資料源'], ['audit', '審計']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="govtab" data-arg="${x[0]}">${x[1]}${x[0] === 'dictionary' && EVENTS.some(e => e.status === 'quarantine') ? ` <span class="badge" style="background:var(--st-serious);color:#fff;border-radius:999px;padding:0 6px;font-size:10px">${EVENTS.filter(e => e.status === 'quarantine').length}</span>` : ''}</button>`).join('')}</div></div>${body}`;
}

/* ---- 用戶表管理 */
function pageUserSchema() {
  const t = S.us.tab; let body = '';
  const total = 1_412_000; const linked = Math.round(total * .874);
  if (t === 'props') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>屬性</th><th>型別</th><th>來源</th><th>更新</th><th>敏感</th><th>篩選</th><th>分組</th><th class="num">覆蓋率</th><th></th></tr>${USER_PROPS.map(p => `<tr><td><b class="mono">${esc(p.k)}</b><span class="sub">${esc(p.cn)}${p.note ? ' · ' + esc(p.note) : ''}</span></td><td class="mono">${p.type}${p.enum ? `<span class="sub mono">${esc(p.enum.join(' | '))}</span>` : ''}</td><td>${esc(p.src)}</td><td class="mono" style="font-size:11.5px">${esc(p.upd)}</td><td>${sensPill(p.sens)}</td><td>${p.filter ? '✓' : '—'}</td><td>${p.group ? '✓' : '—'}</td><td class="num"><span class="bar-in" style="width:${p.cover}px;max-width:60px;background:${p.cover < 70 ? 'var(--st-warn)' : 'var(--s1)'}"></span><span class="mono">${p.cover}%</span></td><td class="num">${p.sens === 'L4' ? '<span class="pill red">禁止</span>' : `<button class="btn-ghost sm" data-act="toast" data-arg="原型：編輯用戶屬性（顯示名、分桶規則、是否可篩選／分組；型別與敏感等級變更需審批）">編輯</button>`}</td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>寫入方式</h3></div><div class="note">profile_set／set_once／increment／append／unset（神策相容協議）；CDC 來源以映射規則寫入；標籤引擎每日覆寫標籤型屬性；事件推導屬性由 Flink 即時更新。</div></div><div class="card"><div class="card-h"><h3>新增屬性</h3></div><div class="note">在可視化建模的「欄位映射」把來源欄位對到用戶屬性即自動登記；型別與敏感等級由治理審核；可篩選／分組會同步到語意層 dimensions.yaml。</div></div><div class="card"><div class="card-h"><h3>覆蓋率低的屬性</h3></div><div class="note">${USER_PROPS.filter(p => p.cover > 0 && p.cover < 75).map(p => `<b>${esc(p.cn)}</b> ${p.cover}%`).join('、')}：來自 GA 或標籤，未登入或未交易的用戶沒有值；分析時請注意分母。</div></div></div>`;
  else if (t === 'oneid') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>優先序</th><th>識別類型</th><th>強度</th><th>數量</th><th>改綁／解綁</th><th>來源</th><th>說明</th></tr>${ONEID_RULES.map(r => `<tr><td class="mono">${r.priority}</td><td><b class="mono">${esc(r.id)}</b><span class="sub">${esc(r.cn)}</span></td><td><span class="pill ${r.strength === 'strong' ? 'green' : r.strength === 'weak' ? 'amber' : 'gray'}">${({ strong: '強', weak: '弱', channel: '渠道' })[r.strength]}</span></td><td>${esc(r.card)}</td><td>${esc(r.rebind)}</td><td>${esc(r.src)}</td><td style="color:var(--body)">${esc(r.note)}</td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="tile"><div class="lbl">OneID 總數</div><div class="val">${compact(total)}</div><div class="delta"><span>含未登入的 GA 匿名 ID</span></div></div><div class="tile"><div class="lbl">已打通（有強 ID）</div><div class="val">${pct(linked / total)}</div><div class="delta"><span>${compact(linked)} 人可對應客戶代碼</span></div></div><div class="tile"><div class="lbl">昨日升級／改綁／登入改寫／衝突</div><div class="val" style="font-size:22px">4,120<small>/ 1,870 / 38.2 萬次·1,140 萬筆 / 3</small></div><div class="delta"><span>登入改寫 P95 落地 11 分鐘；衝突 3 件為同手機多客戶，待人工</span></div></div></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>歸戶規則（對齊神策 IDM 3.0）</h3><span class="sub">docs/19 · backend/semantic/oneid_rules.yaml · backend/oneid/attribution.py 可執行自測</span></div><ul class="priv"><li><b>接入時決定</b>：每筆事件在進入 Flink 時就依其 identities 決定 one_id 並寫入；未登入事件以 <b>distinct_id</b>（Web 為 GA client_id、App 為 app_instance_id、SDK 為匿名 ID）歸到匿名用戶。</li><li><b>登入歸戶 + 近 7 天匿名行為改寫</b>：登入事件（帶客戶代碼 + distinct_id）到達時做歸戶，並把同 distinct_id、登入前 7 天內仍屬匿名用戶的事件改寫到該客戶（distinct_id 保留原值）；已歸任何客戶的事件與 7 天外的事件不動。每 10 分鐘微批落地、只動近 8 個日分區，落地前查詢以覆蓋表即時生效；審計 oneid.backfilled。</li><li><b>一個 ID 同一時刻只屬一個用戶；不自動合併兩個已存在的用戶。</b>同一條資料多個 ID 分屬不同用戶時，資料歸「優先級最高的 ID」所屬用戶；可改綁的 ID（裝置、cookie）從舊用戶解綁、綁到該用戶。</li><li><b>首次關聯升級</b>：純匿名用戶第一次與「尚無用戶」的客戶代碼共現，整個匿名用戶直接升級為該客戶，登入前事件自動歸戶，不需要改寫事件。</li><li><b>既有客戶在新裝置</b>：事件歸客戶，cookie／裝置改綁過去，近 7 天的匿名事件改寫到客戶，更早的留在匿名用戶（與神策「不回填」的刻意差異，為了讓登入前瀏覽 → 登入 → 下單的漏斗接得起來）。</li><li><b>孤立 ID 永不打通</b>：只有同一條資料（事件或 CRM 的 bind 指令）裡共現的 ID 才會建立關聯；帳戶 ↔ 客戶的關聯由 CRM 主檔每日發 bind。</li><li><b>客戶代碼</b>單值、不可改、不可解綁；手機單值、衝突不改綁（人工）；裝置／cookie 多值、最新者取得。</li><li><b>未同意</b>（analytics_storage 拒絕）：弱 ID 不與強 ID 建立關聯、不改寫，事件以匿名用戶記錄。</li></ul></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>GA360 埋點與交易事件怎麼打通（去識別化同款）</h3><span class="sub">docs/20 · 一把金鑰、一個代碼、三個來源同值</span></div><div class="tbl-wrap"><table class="tbl"><tr><th>原始欄位</th><th>代碼化</th><th>產出 ID</th><th>GA 端</th><th>交易端</th></tr>${[['客戶 ID', 'HMAC-SHA256（k_idm，HSM）→ v1_ + 20 碼', '$identity_login_id', '登入成功後由 App 後端向 ID 服務取 token，前端 gtag(\'set\',{user_id}) / Firebase setUserId；登出設 null、App resetAnalyticsData()', 'CDC 事件化時由 Flink 呼叫同一個 ID 服務產生，同值'], ['手機', 'HMAC-SHA256（k_idm_mobile）', '$identity_mobile_hash', '不上報 GA', 'CRM／開戶寫入 id_map'], ['帳號', 'FPE（FF1，k_acct）', '$identity_account_no', '不上報 GA（只帶 account_type 區間）', '交易事件 identities、dim_account 主鍵、CRM bind'], ['GA client_id / app_instance_id', '原值（GA 產生的隨機 ID）', 'distinct_id → $identity_cookie_id / $identity_device_id', '未登入事件唯一識別', '—']].map(r => `<tr><td style="font-weight:600">${r[0]}</td><td>${esc(r[1])}</td><td class="mono" style="font-size:11px">${esc(r[2])}</td><td>${esc(r[3])}</td><td>${esc(r[4])}</td></tr>`).join('')}</table></div><div class="note" style="margin-top:10px">GA 的 user_id 不回溯套用到登入前的事件，所以登入前行為的歸戶由本系統的「登入改寫」完成；身分證、姓名、Email 永不進 GA 也永不進事件表。行為事件與交易事件不做逐筆對應（多數行為事件沒有對應的交易事件），只靠同一個客戶代碼歸到同一個 one_id，再由漏斗／路徑串起來。</div></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h3>情境對照</h3><span class="sub">與 attribution.py 自測一一對應</span></div><div class="tbl-wrap"><table class="tbl"><tr><th>情境</th><th>輸入</th><th>結果</th></tr>${ONEID_CASES.map(c => `<tr><td style="font-weight:600;white-space:nowrap">${esc(c[0])}</td><td class="mono" style="font-size:11.5px">${esc(c[1])}</td><td style="color:var(--body)">${esc(c[2])}</td></tr>`).join('')}</table></div></div>`;
  else body = `<div class="grid c2"><div class="card"><div class="card-h"><h3>個資防護（L4 永不入倉）</h3></div><div class="tbl-wrap"><table class="tbl"><tr><th>欄位</th><th>處理</th><th>偵測</th></tr>${[['姓名', '不入倉；細查頁不顯示', '—'], ['身分證字號', '不入倉；OneID 不使用', '正規式 [A-Z][12]\\d{8} 掃描 → 拒絕＋告警'], ['手機', '只存 HMAC-SHA256（金鑰在 KMS）作識別', '09\\d{8} 掃描'], ['Email', '只存 HMAC 作識別', 'RFC 5322 掃描'], ['完整帳號', 'FPE 代碼化（可 JOIN 不可反推）', '—'], ['生日', '只存年齡層分桶', '—'], ['地址', '只存縣市', '—']].map(r => `<tr><td style="font-weight:600">${r[0]}</td><td>${r[1]}</td><td class="mono" style="font-size:11px">${esc(r[2])}</td></tr>`).join('')}</table></div></div>
    <div class="stack"><div class="card"><div class="card-h"><h3>DSAR／刪除權</h3></div><div class="note">客戶要求刪除：以客戶代碼找 one_id → 刪 users／id_map／user_tags → events 以 one_id 改寫為 tombstone（保留匿名統計）→ 下游推送撤回 → 30 天內完成並產出證明。原型：稽核頁可發起。</div><div class="acts" style="margin-top:10px"><button class="btn-ghost sm" data-act="toast" data-arg="已建立 DSAR 工單（需系統管理員核准）">發起 DSAR</button></div></div>
    <div class="card"><div class="card-h"><h3>用戶表統計</h3></div><dl class="kv"><dt>users 列數</dt><dd class="mono">${n0(total)}</dd><dt>屬性數</dt><dd class="mono">${USER_PROPS.filter(p => p.sens !== 'L4').length}</dd><dt>可篩選／可分組</dt><dd class="mono">${USER_PROPS.filter(p => p.filter).length} / ${USER_PROPS.filter(p => p.group).length}</dd><dt>昨日更新</dt><dd class="mono">1,230,119（CDC）+ 1,412,000（標籤）</dd><dt>儲存</dt><dd class="mono">2.1 GB（ClickHouse users）</dd></dl></div></div></div>`;
  return `<div class="page-h"><div><h1>用戶表管理</h1><p>用戶屬性（來源、更新、敏感等級、可篩選／分組）、OneID 識別與合併規則、個資防護與 DSAR。用戶表的每個可篩選屬性都會出現在分析的「用戶屬性」條件裡。</p></div><div class="right">${govWrite() ? '<button class="btn-primary" data-act="toast" data-arg="原型：新增用戶屬性（名稱、型別、來源映射、敏感等級、分桶規則）">＋ 新增屬性</button>' : '<span class="pill gray">唯讀</span>'}</div></div>
  <div class="filters"><div class="seg">${[['props', '用戶屬性'], ['oneid', 'OneID 規則'], ['privacy', '個資防護與統計']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="ustab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

/* ---- 維度表與字典（對齊神策元數據管理：維度表／維度字典／虛擬屬性） */
function pageItems() {
  const t = S.items.tab; const cur = DIM_TABLES.find(d => d.id === S.items.type) || DIM_TABLES[0]; let body = '';
  if (t === 'tables') body = `<div class="grid c3" style="grid-template-columns:repeat(5,1fr);margin-bottom:16px">${DIM_TABLES.map(d => `<button class="tile ${d.id === cur.id ? 'sel' : ''}" data-act="itemtype" data-arg="${d.id}" style="text-align:left;cursor:pointer"><div class="lbl">${esc(d.cn)}</div><div class="val" style="font-size:20px">${compact(d.rows)}<small>列</small></div><div class="delta"><span class="mono">${esc(d.id)}</span>${d.status === 'warn' ? ' <span class="pill amber">注意</span>' : ''}</div></button>`).join('')}</div>
    <div class="grid c2"><div class="card"><div class="card-h"><h3>${esc(cur.cn)} <span class="mono note">${esc(cur.id)}</span></h3><span class="sub">主鍵 ${esc(cur.pk)} · ${n0(cur.rows)} 列 · 同步 ${esc(cur.sync)}</span></div>
      <dl class="kv" style="margin-bottom:10px"><dt>關聯條件</dt><dd class="mono" style="font-size:11.5px">${esc(cur.join)}</dd><dt>item_type</dt><dd class="mono">${esc(cur.itemType)}</dd><dt>來源</dt><dd>${esc(cur.src)}</dd></dl>
      <div class="tbl-wrap"><table class="tbl"><tr><th>欄位</th><th>顯示名</th><th>型別</th><th>敏感</th><th>可篩選／分組</th></tr>${cur.cols.map(c => `<tr><td class="mono">${esc(c[0])}</td><td>${esc(c[1])}</td><td class="mono">${c[2]}</td><td>${sensPill(c[3])}</td><td>${c[4] ? '✓ 關聯屬性' : '—'}</td></tr>`).join('')}</table></div>${cur.note ? `<div class="note" style="margin-top:10px">${esc(cur.note)}</div>` : ''}</div>
    <div class="stack"><div class="card"><div class="card-h"><h3>用在哪裡</h3></div><ul class="priv">${cur.usedBy.map(u => `<li>${esc(u)}</li>`).join('')}</ul><div class="acts" style="margin-top:10px"><button class="btn-ghost sm" data-act="toast" data-arg="原型：編輯關聯條件與欄位顯示名／敏感等級">編輯</button><button class="btn-ghost sm" data-act="goto" data-arg="#/integration/tasks">同步任務</button><button class="btn-ghost sm" data-act="toast" data-arg="已觸發同步（審計 dimension.sync）">立即同步</button></div></div>
    <div class="card"><div class="card-h"><h3>維度表的規則</h3></div><ul class="priv"><li>維度表是用來<b>補充事件／用戶屬性沒有埋的維度</b>：以「屬性值 = 維度表主鍵」關聯，欄位成為可篩選、可分組的關聯屬性；事件仍然屬於用戶，維度表不是另一個主體。</li><li>關聯鍵必須是主鍵（唯一），否則查詢會放大列數；同一主鍵可用 item_type 區分不同類型。</li><li>建議行數 &lt; 100 萬；超過時改以 dictGet 字典查詢，不走 JOIN。</li><li>來源由「數據整合 › 同步任務」寫入（upsert by 主鍵）或以 item_set 協議上報；欄位有敏感等級，依角色隱碼。</li><li>事件屬性已能滿足的分析不要用維度表（JOIN 有成本）。</li></ul></div></div></div>`;
  else if (t === 'dicts') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>屬性</th><th>映射</th><th class="num">值數</th><th>範例</th><th>來源</th><th>更新</th><th></th></tr>${DIM_DICTS.map(d => `<tr><td><b class="mono">${esc(d.prop)}</b><span class="sub">${esc(d.cn)}</span></td><td><span class="pill">${d.mode}</span></td><td class="num mono">${d.n}</td><td>${d.sample.slice(0, 4).map(x => `<span class="pill mono" style="font-size:10px;margin:1px">${esc(x[0])} → ${esc(x[1])}</span>`).join('')}${d.sample.length > 4 ? `<span class="note">…</span>` : ''}</td><td>${esc(d.src)}</td><td class="mono">${esc(d.upd)}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="toast" data-arg="原型：上傳字典（CSV：值,顯示名；新增／覆蓋）">上傳</button><button class="btn-ghost sm" data-act="toast" data-arg="已下載 ${esc(d.prop)} 字典">下載</button></td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>維度字典的規則</h3></div><ul class="priv"><li>針對已有資料上報的文字／數值屬性，上傳「值 → 顯示名」映射（1:1 或 N:1，例：SYS02、SYS03 都顯示為「閘道逾時」）。</li><li>篩選與分組時以顯示名呈現並可選；原值仍保留在資料裡。</li><li>上傳可選「新增」（保留既有）或「覆蓋」（先清空再上傳）。</li></ul></div><div class="card"><div class="card-h"><h3>與維度表的差別</h3></div><div class="note">字典只是顯示名，不新增欄位；要補多個維度（產業、類型、區域）用維度表。分公司代碼的字典由維度表 dim_branch.name 自動產生，兩者不衝突。</div></div><div class="card"><div class="card-h"><h3>治理</h3></div><div class="note">上傳需 governance.d；變更寫審計 dictionary.uploaded；字典變更即時生效，不影響歷史資料。</div></div></div>`;
  else body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>虛擬屬性</th><th>範圍</th><th>表達式</th><th>引用維度表</th><th>說明</th><th></th></tr>${VIRTUAL_PROPS.map(v => `<tr><td><b class="mono">${esc(v.name)}</b><span class="sub">${esc(v.cn)}</span></td><td><span class="pill">${v.scope === 'event' ? '事件' : '用戶'}</span></td><td class="mono" style="font-size:11px;max-width:380px;overflow-wrap:anywhere">${esc(v.expr)}</td><td>${v.uses.map(u => `<button class="lnk" data-act="itemtype" data-arg="${u}">${esc(u)}</button>`).join(' ') || '—'}</td><td style="color:var(--body)">${esc(v.note)}</td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="原型：虛擬屬性編輯器（表達式、型別推斷、預覽）">編輯</button></td></tr>`).join('')}</table></div></div>
    <div class="note" style="margin-top:10px">虛擬屬性以表達式組合事件／用戶屬性與維度表欄位，查詢時計算、不落地；名稱不可與既有屬性重複；可用於篩選、分組與標籤定義；表達式上限 1,024 字元。</div>`;
  return `<div class="page-h"><div><h1>維度表與字典</h1><p>對齊神策元數據管理：<b>維度表</b>以「屬性值 = 主鍵」補充事件／用戶沒有埋的維度，欄位成為關聯屬性；<b>維度字典</b>給屬性值顯示名；<b>虛擬屬性</b>以表達式組合既有屬性與維度表欄位。資料由「數據整合 › 同步任務」寫入。</p></div><div class="right">${govWrite() ? '<button class="btn-primary" data-act="toast" data-arg="原型：新增維度表（選同步任務的目標表 → 設主鍵與關聯屬性 → 選欄位與敏感等級）">＋ 新增維度表</button>' : '<span class="pill gray">唯讀</span>'}</div></div>
  <div class="filters"><div class="seg">${[['tables', '維度表'], ['dicts', '維度字典'], ['virtual', '虛擬屬性']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="itemtab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

/* ---- 資料流與效能 */
function pagePipeline() {
  const P = PIPELINE; const t = S.pipe.tab;
  const flow = `<div class="pipe">${P.stages.map((s, i) => `${i ? '<div class="pipe-arrow">→</div>' : ''}<div class="pipe-stage ${s.status}"><div class="hd"><b>${esc(s.cn)}</b><span class="status ${s.status}"><span class="dot"></span></span></div><ul>${s.items.map(x => `<li>${esc(x)}</li>`).join('')}</ul><div class="ft"><span>${esc(s.tput)}</span><span>${esc(s.lag)}</span></div>${s.note ? `<div class="note" style="color:#9A5E0E">${esc(s.note)}</div>` : ''}</div>`).join('')}</div>`;
  let body = '';
  if (t === 'flow') body = `<div class="card"><div class="card-h"><h3>端到端資料流（即時）</h3><span class="sub">來源 → 接入 → Kafka → Flink → ClickHouse → 服務 → 下游；每段顯示吞吐與延遲</span></div>${flow}<div class="note" style="margin-top:10px">端到端延遲（GTM 即時事件 → 可查）P95 <b class="mono">4.6 s</b>；日批（GA360 前日）<b class="mono">05:52</b> 到齊。SLO：即時 < 10 s、日批 < 07:00。</div></div>
    <div class="grid c2" style="margin-top:16px"><div class="card"><div class="card-h"><h3>資料品質</h3></div><div class="tbl-wrap"><table class="tbl">${P.quality.map(q => `<tr><td>${esc(q[0])}</td><td class="num mono">${esc(q[1])}</td><td><span class="status ${q[2]}"><span class="dot"></span>${q[2] === 'ok' ? '正常' : '注意'}</span></td></tr>`).join('')}</table></div></div>
    <div class="card"><div class="card-h"><h3>各模組的資料來源</h3></div><div class="tbl-wrap"><table class="tbl"><tr><th>模組</th><th>讀取</th><th>寫入／輸出</th></tr>${[['事件／漏斗／留存／路徑', 'events（L0）、MV L1–L3、users、items（dictGet）', '查詢結果快取（Valkey 5 分鐘）、audit_log'], ['歸因', 'touchpoints（日批）、events', 'audit_log'], ['用戶列表／細查', 'user_tags Bitmap、users、events（細查）', 'audit_log（含理由）'], ['標籤與分群', 'events、users、items', 'user_tags（版本）、segments、dmp.tag_updates'], ['即時觸發', 'Kafka 事件流（Flink 規則）', 'MA Webhook、audit_log'], ['受眾推送', 'segments Bitmap', 'Webhook／Kafka／GA4 MP、deliveries'], ['AI 洞察', '語意層 → 同一查詢編譯器', 'insights（PostgreSQL+pgvector）、audit_log'], ['數據整合', '來源系統（CDC／批次）', 'Kafka raw.*、quarantine、lineage']].map(r => `<tr><td style="font-weight:600">${r[0]}</td><td class="mono" style="font-size:11.5px">${esc(r[1])}</td><td class="mono" style="font-size:11.5px">${esc(r[2])}</td></tr>`).join('')}</table></div></div></div>`;
  else if (t === 'query') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>模組</th><th class="num">P50</th><th class="num">P95</th><th class="num">P99</th><th class="num">SLA（P95）</th><th class="num">平均掃描</th><th class="num">快取命中</th><th>查詢路徑</th><th class="num">24h 次數</th></tr>${QUERY_PERF.map(r => `<tr><td style="font-weight:600">${esc(r.mod)}</td><td class="num mono">${r.p50.toFixed(1)} s</td><td class="num mono" style="color:${r.p95 > r.sla ? 'var(--st-serious)' : 'inherit'}"><b>${r.p95.toFixed(1)} s</b></td><td class="num mono">${r.p99.toFixed(1)} s</td><td class="num mono">${r.sla} s</td><td class="num mono">${esc(r.scan)}</td><td class="num"><span class="bar-in" style="width:${r.cache * .6}px;background:${r.cache < 30 ? 'var(--st-warn)' : 'var(--s3)'}"></span><span class="mono">${r.cache}%</span></td><td style="color:var(--body);font-size:12px">${esc(r.path)}</td><td class="num mono">${n0(r.n24h)}</td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>效能策略</h3></div><ul class="priv"><li>90% 查詢走 MV（L1 日彙總、L2 常用分組、L3 cohort），不碰明細。</li><li>one_id_uint 稠密 ID + Bitmap：人數估算 < 1 秒。</li><li>路徑預設 SAMPLE 1/10；全量需確認（耗時 ×8）。</li><li>查詢結果快取 5 分鐘；相同設定命中率見表。</li><li>工作負載隔離：human／agent／batch 各自記憶體與執行緒上限。</li></ul></div><div class="card"><div class="card-h"><h3>需要注意</h3></div><ul class="priv"><li>行為路徑 P99 9.5 秒接近上限：全量查詢改走預聚合的 session 序列表（M3）。</li><li>漏斗快取命中 38%：窗口期與步驟組合多，建議對常用漏斗建 L3 預聚合。</li><li>50 人併發下 ClickHouse CPU 峰值 62%，日批時段 85%：批次排在 02:00–06:00。</li></ul></div><div class="card"><div class="card-h"><h3>基準測試</h3></div><div class="note">合成資料 1 億事件（M2）→ 10 億（M5）；固定查詢集 40 條；每 Sprint 回歸，P95 超過 SLA 即阻擋發版。目前數據為設計目標值與原型模擬，非實測。</div></div></div>`;
  else if (t === 'batch') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>作業</th><th>排程</th><th>最近一次</th><th class="num">耗時</th><th>狀態</th><th>備註</th><th></th></tr>${BATCH_JOBS.map(j => `<tr><td style="font-weight:600">${esc(j.name)}</td><td class="mono">${esc(j.sched)}</td><td class="mono">${esc(j.last)}</td><td class="num mono">${esc(j.dur)}</td><td><span class="status ${j.status}"><span class="dot"></span>${j.status === 'ok' ? '成功' : '注意'}</span></td><td style="color:var(--body)">${esc(j.note)}</td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="已觸發重跑（審計 batch.rerun）">重跑</button></td></tr>`).join('')}</table></div></div>
    <div class="grid c2" style="margin-top:16px"><div class="card"><div class="card-h"><h3>儲存用量</h3></div>${STORAGE.map(s => `<div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line2)"><span><b>${esc(s[0])}</b></span><span><span class="bar-in" style="width:${Math.round(s[1] / s[2] * 120)}px;background:${s[1] / s[2] > .9 ? 'var(--st-serious)' : 'var(--s1)'}"></span><span class="mono">${s[1]} / ${s[2]} ${s[3]}</span></span></div>`).join('')}<div class="note" style="margin-top:8px">ClickHouse 已用 99%：3 年全熱規劃值即為 7.8 TB，第 4 年起啟用冷層（GCS S3 disk）或擴到 8 TB。</div></div><div class="card"><div class="card-h"><h3>告警規則</h3></div><ul class="priv"><li>Kafka 消費延遲 > 60 s、Flink checkpoint 失敗 ×3、ClickHouse 副本延遲 > 5 分鐘。</li><li>日批超過 07:00 未完成；標籤日批 > 40 分鐘。</li><li>隔離率 > 1%、對帳差異 > 1%、Schema drift 新增。</li><li>查詢 P95 超過 SLA 連續 15 分鐘。</li><li>下游推送 DLQ > 0、Webhook 連續失敗 ×5。</li></ul><div class="note">告警到 Grafana OSS → 值班群組；嚴重等級寫審計 ops.alert。</div></div></div>`;
  return `<div class="page-h"><div><h1>資料流與效能</h1><p>端到端資料流、各模組讀寫、查詢效能（P50／P95／P99 對 SLA）、批次作業、儲存與告警。數字為設計目標與原型模擬，正式環境由 Prometheus／Grafana 供數。</p></div><div class="right"><button class="btn-ghost" data-act="toast" data-arg="原型：開啟 Grafana 看板（平台／資料品質／審計）">Grafana</button></div></div>
  <div class="filters"><div class="seg">${[['flow', '資料流'], ['query', '查詢效能'], ['batch', '批次與儲存']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="pipetab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

/* ---- IAM 補強：使用者指派、範圍指派與行級模擬 */
function iamAssignments() {
  return `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>使用者</th><th>角色</th><th>數據範圍</th><th>到期</th><th>身分來源</th><th>最近登入</th><th></th></tr>${USER_ASSIGNMENTS.map(u => `<tr class="${u.temp ? 'warnrow' : ''}"><td><b>${esc(u.user)}</b><span class="sub mono">${esc(u.email)}</span></td><td>${u.roles.map(r => { const ro = allRoles().find(x => x.key === r); return `<span class="pill ${r === 'admin' ? 'red' : 'blue'}">${esc(ro ? ro.cn : r)}</span>`; }).join(' ')}</td><td>${esc(u.scope)}</td><td class="mono" style="color:${u.temp ? 'var(--st-warn)' : 'inherit'}">${esc(u.expiry)}</td><td class="mono" style="font-size:11.5px">${esc(u.sso)}</td><td class="mono">${esc(u.last)}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="toast" data-arg="原型：編輯指派（角色多選、組織樹範圍、回溯天數、到期日）${has('iam', 'd') ? '' : '（需權限中心設定權限）'}">編輯</button>${u.temp ? `<button class="btn-ghost sm danger" data-act="toast" data-arg="已撤銷臨時指派（審計 iam.assignment.revoked）">撤銷</button>` : ''}</td></tr>`).join('')}</table></div></div>
  <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>指派規則</h3></div><ul class="priv"><li>角色由 SSO 群組自動對應（AD group → 角色），本地帳號僅限外包且強制 MFA 與到期日。</li><li>多角色：功能聯集、隱碼最嚴、範圍聯集。</li><li>臨時提權最長 4 小時，需系統管理員＋數位經營雙人核准，到期自動撤銷。</li></ul></div><div class="card"><div class="card-h"><h3>離職／調動</h3></div><div class="note">HR 同步（每日）停用帳號、調動自動更新 AD 群組對應的角色；90 天未登入自動停用；所有變更寫審計 iam.assignment.*。</div></div><div class="card"><div class="card-h"><h3>到期提醒</h3></div><div class="note">${USER_ASSIGNMENTS.filter(u => u.temp).map(u => `<b>${esc(u.user)}</b> ${esc(u.expiry)}`).join('；')}。到期前 7 天通知申請人與核准人。</div></div></div>`;
}
function iamScopeSim() {
  const r = S.iam.simRole || RK(); const role = allRoles().find(x => x.key === r) || allRoles()[0];
  const sql = { admin: '-- 全域：無行級條件', marketing: '-- 全域：無行級條件；欄位 L3 尾碼遮罩、L2 區間化；profile 模組拒絕\n-- K 匿名：分組人數 < 20 → 顯示「< 20」', data_analyst: '-- 全域：無行級條件；欄位 L3 尾碼遮罩；細查需理由、匯出需審批', digital_ops: '-- 全域：無行級條件；欄位 L3 尾碼遮罩', it_admin: '-- 全域（不含 profile／users 模組）：只能看聚合與治理資料', custom_vendor_analyst: "project IN ('sinopac_demo') AND event_date >= today()-180  -- 自訂角色：專案範圍 + 回溯" }[r] || "project IN ('sinopac_demo')  -- 自訂角色預設：專案範圍";
  return `<div class="card" style="margin-top:16px"><div class="card-h"><h3>行級規則模擬</h3><span class="sub">選角色，看查詢編譯器會加上的 WHERE 條件</span><div class="right"><select class="sel" data-act="iamsim">${allRoles().map(x => `<option value="${x.key}" ${x.key === r ? 'selected' : ''}>${esc(x.cn)}</option>`).join('')}</select></div></div><div class="code">${esc(sql)}</div><div class="note" style="margin-top:8px">同一條件同時套用在 UI 查詢、REST API 與 AI 工具（以委託身份）；分群先與範圍 Bitmap 交集再計算。範圍綁在「使用者 × 角色」，在使用者指派頁設定。</div></div>`;
}

function pageSettings() {
  return `<div class="page-h"><div><h1>平台設定</h1><p>${S.user.can.admin ? '管理員可修改' : '唯讀（需管理員）'}</p></div></div>
  <div class="grid c2"><div class="stack"><div class="card"><div class="card-h"><h3>角色與權限</h3></div><table class="tbl"><tr><th>角色</th><th>範圍</th><th>細查</th><th>SQL</th><th>審批</th></tr>${ACCOUNTS.map(a => `<tr><td><b>${esc(a.role)}</b></td><td>${a.scope === 'all' ? '全域' : '所屬分公司'}</td><td>${a.can.detail ? '✓（需理由）' : '—'}</td><td>${a.can.sql ? '✓' : '—'}</td><td>${a.can.approve ? '✓' : '—'}</td></tr>`).join('')}</table></div>
  <div class="card"><div class="card-h"><h3>資料保存與個資</h3></div><dl class="kv"><dt>熱資料</dt><dd>ClickHouse 13 個月</dd><dt>冷資料</dt><dd>Iceberg on MinIO，法定年限，SSE-KMS</dd><dt>可識別欄位</dt><dd>不進分析倉；ID 服務保存 HMAC（HSM 金鑰）</dd><dt>DSAR</dt><dd>oneid.erase → 24 小時內完成並出具證明</dd></dl></div></div>
  <div class="stack"><div class="card"><div class="card-h"><h3>Agent 預算</h3></div><dl class="kv"><dt>每次工具呼叫</dt><dd>20 GB 掃描</dd><dt>每次 run</dt><dd>100 GB · 50k tokens</dd><dt>每 agent 每日</dt><dd>2 TB</dd><dt>敏感工具</dt><dd>理由必填、審計、僅所屬客戶</dd></dl></div>
  <div class="card"><div class="card-h"><h3>接入</h3></div><dl class="kv"><dt>GA360</dt><dd>BigQuery 匯出：日批（權威）+ 串流 intraday（分鐘級）</dd><dt>交易</dt><dd>Outbox + Debezium CDC → Flink 狀態機</dd><dt>Collector</dt><dd>神策相容 /sa、/v1/events（App／內部系統直送）</dd></dl></div></div></div>`;
}

/* ================================================================== v0.2：P0 模組 — 行為路徑、歸因、用戶列表、數據整合 */
function pageUsers() {
  const u = S.users; const all = USERS.filter(x => !S.user || S.user.scope === 'all' || S.user.scope === `branch:${x.branch}`);
  const list = all.filter(x => (u.lc === 'all' || x.lifecycle === u.lc) && (u.tier === 'all' || x.tier === u.tier) && (!u.q || x.one_id.includes(u.q) || x.cust.includes(u.q)));
  const est = Math.round(list.length / Math.max(1, all.length) * (S.user.scope === 'all' ? 1412000 : 38400));
  const PS = 10; const pages = Math.max(1, Math.ceil(list.length / PS)); const page = Math.min(u.page, pages - 1); const rows = list.slice(page * PS, page * PS + PS); const nsel = Object.keys(u.sel).filter(k => u.sel[k]).length;
  return `<div class="page-h"><div><h1>用戶列表</h1><p>條件 → Bitmap 人數預估（&lt; 1 秒）→ 分頁列表（脫敏、行級權限）。任何分析落點都能進到這裡，再存為分群或推送。</p></div><div class="right"><button class="btn-ghost" data-act="toast" data-arg="已存為分群草稿">存為分群</button><button class="btn-ghost" data-act="toast" data-arg="${has('users', 'e') ? '匯出需審批：已送出申請（L2 區間化、浮水印、上限 10 萬列）' : '你的角色沒有用戶列表匯出權限'}" ${has('users', 'e') ? '' : 'style="opacity:.5"'}>匯出（審批）</button><button class="btn-primary" data-act="toast" data-arg="推送需審批：已送出申請">推送到渠道</button></div></div>
  ${u.from ? `<div class="from-bar"><span class="pill blue">來源條件</span><b>${esc(u.from.label)}</b>${u.from.n ? `<span class="note">預估 ${n0(u.from.n)} 人</span>` : ''}<button class="btn-ghost sm" data-act="ufromclear">清除</button></div>` : ''}
  <div class="filters"><input class="inp" placeholder="one_id / 客戶代碼" data-act="uq" value="${esc(u.q)}"><select class="sel" data-act="ulc"><option value="all">生命週期：全部</option>${['新客', '成長', '成熟', '沉睡', '流失'].map(v => `<option value="${v}" ${u.lc === v ? 'selected' : ''}>${v}</option>`).join('')}</select><select class="sel" data-act="utier"><option value="all">價值分級：全部</option>${['P1', 'P2', 'P3', 'P4', 'P5'].map(v => `<option value="${v}" ${u.tier === v ? 'selected' : ''}>${v}</option>`).join('')}</select><span class="pill">+ 行為條件</span><span class="pill">+ 標籤</span><span class="pill">+ 分群</span><span style="margin-left:auto" class="pill teal">預估 ${n0(est)} 人 · Bitmap 0.3 s</span>${nsel ? `<span class="pill blue">已選 ${nsel}</span><button class="btn-ghost sm" data-act="toast" data-arg="已批次打標籤（需審批）">批次打標籤</button>` : ''}</div>
  <div class="card flush"><table class="tbl"><tr><th></th><th>one_id</th><th>客戶</th><th>分公司 / 營業員</th><th>生命週期</th><th>價值</th><th>偏好</th><th>年齡層</th><th class="num">流失分</th><th class="num">90 天手續費</th><th class="num">30 天委託</th><th>最近活躍</th><th></th></tr>
  ${rows.map(x => `<tr><td><button class="chk ${u.sel[x.one_id] ? 'on' : ''}" data-act="usel" data-arg="${x.one_id}" aria-label="選取"></button></td><td>${mId(x.one_id)}</td><td class="mono">${esc(x.cust)}</td><td>${esc(x.branch)} / ${esc(x.agent)}</td><td><span class="pill ${x.lifecycle === '沉睡' || x.lifecycle === '流失' ? 'amber' : 'teal'}">${x.lifecycle}</span></td><td><span class="pill">${x.tier}</span></td><td>${x.market}</td><td>${x.age}</td><td class="num">${mScore(x.churn)}</td><td class="num">${mMoney(x.fee90)}</td><td class="num mono">${x.orders30}</td><td class="mono">${x.last}</td><td class="num"><button class="btn-ghost sm" data-act="uprofile" data-arg="${x.one_id}">細查</button></td></tr>`).join('') || '<tr><td colspan="13"><div class="empty">沒有符合條件的客戶</div></td></tr>'}</table>
  <div class="pager"><span>第 ${page + 1} / ${pages} 頁 · 本頁 ${rows.length} 筆 · 列表上限 10 萬筆</span><span class="right"><button class="btn-ghost sm" data-act="upage" data-arg="-1" ${page === 0 ? 'disabled' : ''}>上一頁</button><button class="btn-ghost sm" data-act="upage" data-arg="1" ${page >= pages - 1 ? 'disabled' : ''}>下一頁</button></span></div></div>
  <div class="note" style="margin-top:10px">欄位皆為代碼化或分桶後的值；姓名、手機、帳號明文不在分析倉。「細查」需填寫理由並寫入審計。${S.user.scope === 'all' ? '' : '你的範圍：台北分公司 TPE01。'}</div>`;
}
/* ================================================================== v0.4.2：資料來源連線與鑑權（新增／編輯連線、連線測試、輪替） */
const CONN_TYPES = ['MSSQL', 'PostgreSQL', 'BigQuery', 'Kafka', 'GCS', 'SensorsData'];
function openConn(id) {
  const c = id ? CONNECTIONS.find(x => x.id === id) : null; const type = c ? c.type : 'MSSQL'; const cat = CONNECTOR_AUTH[type]; const m = c ? cat.methods.find(x => x.id === c.auth.method) || cat.methods[0] : cat.methods[0];
  S.conn = { id: c ? c.id : '', isNew: !c, cn: c ? c.cn : '', type, method: m.id, fields: c ? { ...c.auth.fields } : Object.fromEntries(m.fields.map(f => [f, f === 'port' ? cat.port || '' : ''])), mode: c ? 'cdc' : 'batch', secretMode: c && c.auth.secret ? 'ref' : 'once', secretRef: c && c.auth.secret ? c.auth.secret.ref : '', secretOnce: '', owner: c ? c.auth.owner : '', test: null, testing: false, dirty: false };
  render();
}
function connSetType(t) { const cat = CONNECTOR_AUTH[t]; const m = cat.methods[0]; S.conn.type = t; S.conn.method = m.id; S.conn.fields = Object.fromEntries(m.fields.map(f => [f, f === 'port' ? cat.port || '' : ''])); S.conn.test = null; render(); }
function connSetMethod(id) { const cat = CONNECTOR_AUTH[S.conn.type]; const m = cat.methods.find(x => x.id === id); const old = S.conn.fields; S.conn.method = id; S.conn.fields = Object.fromEntries(m.fields.map(f => [f, old[f] ?? (f === 'port' ? cat.port || '' : '')])); S.conn.secretRef = ''; S.conn.secretOnce = ''; S.conn.test = null; render(); }
function connTest() {
  const c = S.conn; const cat = CONNECTOR_AUTH[c.type]; const m = cat.methods.find(x => x.id === c.method); const errs = [];
  m.fields.forEach(f => { if (!String(c.fields[f] || '').trim()) errs.push(`缺少 ${FIELD_CN[f] || f}`); });
  if (m.secret) { if (c.secretMode === 'ref' && !/^(projects\/[^/]+\/secrets\/[^/]+\/versions\/(\d+|latest)|kv\/data\/dmp\/[a-z]+\/[a-z0-9_]+#[a-z_]+|secret:\/\/[a-z0-9-]+\/[a-z0-9-]+\/[A-Za-z0-9_.-]+)$/.test(c.secretRef)) errs.push('密鑰參照格式不合法（projects/…/secrets/…/versions/N、kv/data/dmp/…#key 或 secret://ns/name/key）'); if (c.secretMode === 'once' && !c.secretOnce) errs.push(`請輸入 ${m.secret}（一次性，寫入密鑰庫後不回顯）`); }
  Object.entries(c.fields).forEach(([k, v]) => { if (/^(-----BEGIN|[A-Za-z0-9+/=_-]{40,})/.test(String(v))) errs.push(`欄位 ${FIELD_CN[k] || k} 疑似明文密鑰，請改用密鑰欄位`); });
  if (errs.length) { c.test = { ok: false, steps: [{ step: 'validate', cn: '表單驗證', ok: false, detail: errs.join('；') }] }; render(); return; }
  c.testing = true; render();
  setTimeout(() => {
    const host = String(c.fields.host || c.fields.instance || c.fields.project_id || c.fields.bootstrap || c.fields.base_url || c.fields.bucket || '');
    const failAt = /bad|錯/.test(host) ? 'auth' : /old|self/.test(host) ? 'tls' : /nocdc/.test(host) ? 'cdc_prereq' : /10\.9\./.test(host) ? 'tcp' : null;
    const isCdc = c.mode === 'cdc' && ['MSSQL', 'PostgreSQL'].includes(c.type);
    const steps = []; let ok = true;
    for (const [id, cn] of TEST_STEPS) {
      if (id === 'cdc_prereq' && !isCdc) { steps.push({ step: id, cn, ok: true, detail: '不適用（批次）' }); continue; }
      if (id === 'tls' && c.type === 'BigQuery') { steps.push({ step: id, cn, ok: true, detail: 'Google API（TLS 1.3）' }); continue; }
      if (failAt === id) { steps.push({ step: id, cn, ok: false, detail: { auth: '28000: authentication failed for user', tls: 'x509: certificate signed by unknown authority', cdc_prereq: 'publication "dmp_pub" does not exist', tcp: 'connect timeout after 5 s' }[id], fix: TEST_FIX[id] }); ok = false; break; }
      steps.push({ step: id, cn, ok: true, detail: { dns: '10.20.3.5（私有 DNS）', tcp: `${c.fields.port || 443} open · 2 ms`, tls: 'TLS 1.2 · 內部 CA · 到期 2027-05', auth: `${m.cn} · 以 Workload Identity 取密鑰 v${c.secretMode === 'ref' && /versions\/(\d+)/.test(c.secretRef) ? c.secretRef.match(/versions\/(\d+)/)[1] : '1'}`, privilege: `${(cat.priv[isCdc ? 'cdc' : 'batch'] || cat.priv.batch).length} 項權限全部具備`, cdc_prereq: 'cdc 已啟用 · 6 表 · agent running', sample_read: '讀取 1,000 列 · 0.4 s', clock_skew: '偏移 0.3 s' }[id] });
    }
    c.test = { ok, steps, at: '剛剛' }; c.testing = false; render();
  }, 700);
}
function connSave() {
  const c = S.conn; if (!c.test || !c.test.ok) { toast('請先通過連線測試再儲存'); return; }
  if (!c.cn.trim()) { toast('請填寫連線名稱'); return; }
  const m = CONNECTOR_AUTH[c.type].methods.find(x => x.id === c.method); const id = c.isNew ? (c.cn.replace(/[^a-z0-9]+/gi, '_').toLowerCase().slice(0, 16) || 'conn') + '_' + Date.now().toString(36).slice(-3) : c.id;
  const ref = m.secret ? (c.secretMode === 'ref' ? c.secretRef : `projects/dmp-prod/secrets/${id}/versions/1`) : null;
  const auth = { method: c.method, cn: m.cn, fields: { ...c.fields }, secret: m.secret ? { field: m.secret, ref, provider: ref.startsWith('kv/') ? 'OpenBao' : ref.startsWith('secret://') ? 'K8s Secret' : 'Secret Manager' } : null, rotation: m.rotate ? { kind: 'rotating', every: m.rotate, due: dkey(dayOffset(m.rotate)), left: m.rotate, state: 'ok' } : { kind: 'short_lived' }, lastTest: { at: '剛剛', ok: true }, owner: c.owner || S.user.name, tls: true, privileges: (CONNECTOR_AUTH[c.type].priv[c.mode === 'cdc' ? 'cdc' : 'batch'] || CONNECTOR_AUTH[c.type].priv.batch).map(p => [p, true]) };
  if (c.isNew) CONNECTIONS.push({ id, type: c.type, cn: c.cn, mode: c.mode === 'cdc' ? 'CDC' : '批次', tables: 0, models: 0, lag: '—', status: 'ok', rows24h: 0, auth });
  else { const x = CONNECTIONS.find(y => y.id === c.id); x.cn = c.cn; x.auth = { ...x.auth, ...auth, lastTest: auth.lastTest }; }
  S.conn = null; toast(c.isNew ? `連線已建立：密鑰已寫入密鑰庫（${auth.secret ? auth.secret.provider : '無密鑰'}），平台只保留參照；審計 integration.connection.created` : '連線已更新；審計 integration.connection.updated');
}
function connRotate(id) { const x = CONNECTIONS.find(y => y.id === id); if (!x.auth.secret || x.auth.rotation.kind !== 'rotating') { toast('短效憑證／無密鑰，不需輪替'); return; } const ref = x.auth.secret.ref.replace(/versions\/(\d+)$/, (_, n) => `versions/${Number(n) + 1}`).replace(/#(\w+)$/, '#$1'); x.auth.secret.ref = ref; x.auth.rotation = { ...x.auth.rotation, due: dkey(dayOffset(x.auth.rotation.every)), left: x.auth.rotation.every, state: 'ok' }; toast(`已輪替：新舊版本並存 24 小時，執行中的作業跑完才切換；審計 integration.connection.rotated`); }
function connDrawer() {
  const c = S.conn; if (!c) return ''; const cat = CONNECTOR_AUTH[c.type]; const m = cat.methods.find(x => x.id === c.method); const isCdc = c.mode === 'cdc' && ['MSSQL', 'PostgreSQL'].includes(c.type);
  const privs = cat.priv[isCdc ? 'cdc' : 'batch'] || cat.priv.batch;
  const testView = c.testing ? '<div class="note">測試中：在執行節點以 Workload Identity 取密鑰並逐步探測…</div>' : c.test ? `<div class="tsteps">${c.test.steps.map(s => `<div class="tstep ${s.ok ? 'ok' : 'fail'}"><span class="ic">${s.ok ? '✓' : '✗'}</span><span class="nm">${esc(s.cn || s.step)}</span><span class="d">${esc(s.detail)}</span>${s.fix ? `<div class="fix">修法：${esc(s.fix)}</div>` : ''}</div>`).join('')}${c.test.ok ? '<div class="pill green" style="margin-top:8px">全部通過 · 已寫審計 integration.connection.tested</div>' : '<div class="pill red" style="margin-top:8px">未通過 · 修正後再測</div>'}</div>` : '<div class="note">尚未測試。儲存前必須通過連線測試；測試不會把密鑰帶到瀏覽器或 API 節點。</div>';
  return `<div class="drawer-bg" data-act="connclose"></div><div class="drawer wide"><div class="hd"><b>${c.isNew ? '新增連線' : `編輯連線：${esc(c.cn)}`}</b><span class="pill blue">密鑰不入庫</span><button class="icon-btn x" data-act="connclose">✕</button></div><div class="bd stack">
    <div class="form"><label>連線名稱</label><input class="inp" data-act="conncn" value="${esc(c.cn)}" placeholder="例：交易主機（SQL Server 2019）">
      <label>來源類型</label><div class="seg wrap">${CONN_TYPES.map(t => `<button class="${c.type === t ? 'on' : ''}" data-act="conntype" data-arg="${t}" ${!c.isNew ? 'disabled' : ''}>${t}</button>`).join('')}</div>
      <label>接入方式</label><div class="seg"><button class="${c.mode === 'batch' ? 'on' : ''}" data-act="connmode" data-arg="batch">批次／增量</button><button class="${c.mode === 'cdc' ? 'on' : ''}" data-act="connmode" data-arg="cdc" ${['MSSQL', 'PostgreSQL'].includes(c.type) ? '' : 'disabled'}>CDC（即時）</button></div>
      <label>憑證負責人</label><input class="inp" data-act="connowner" value="${esc(c.owner)}" placeholder="輪替到期通知對象"></div>
    <div class="qsec"><div class="qsec-h"><b>鑑權方式</b><span class="note">依安全性排序；優先用無密鑰或短效憑證</span></div>${cat.methods.map(x => `<label class="auth-opt ${c.method === x.id ? 'on' : ''}"><button class="radio ${c.method === x.id ? 'on' : ''}" data-act="connmethod" data-arg="${x.id}" aria-label="${esc(x.cn)}"></button><span><b>${esc(x.cn)}</b>${x.rec ? '<span class="pill green">建議</span>' : ''}${x.secret ? `<span class="pill gray">密鑰：${x.secret}${x.rotate ? ` · ${x.rotate} 天輪替` : ''}</span>` : '<span class="pill teal">無長期密鑰</span>'}<small>${esc(x.note)}</small></span></label>`).join('')}</div>
    <div class="qsec"><div class="qsec-h"><b>連線參數</b><span class="note">非機密；API 會掃描疑似明文密鑰並拒絕</span></div><div class="form">${m.fields.map(f => `<label>${esc(FIELD_CN[f] || f)}</label><input class="inp" data-act="connfield" data-arg="${f}" value="${esc(c.fields[f] ?? '')}" placeholder="${f === 'host' ? '私有 IP 或內部 DNS' : f === 'port' ? String(cat.port || '') : ''}">`).join('')}
      <label>傳輸加密</label><div class="row"><button class="chk on dis" aria-label="TLS"></button><span class="note">TLS 1.2+ 並驗證伺服器憑證（強制；自簽憑證請提供 CA bundle 參照）</span></div></div></div>
    ${m.secret ? `<div class="qsec"><div class="qsec-h"><b>密鑰（${esc(m.secret)}）</b><span class="note">平台資料庫只存參照；明文只在執行節點（Flink／Kafka Connect／Spark）用 Workload Identity 取得</span></div>
      <div class="seg" style="margin-bottom:8px"><button class="${c.secretMode === 'once' ? 'on' : ''}" data-act="connsecmode" data-arg="once">輸入一次（寫入密鑰庫）</button><button class="${c.secretMode === 'ref' ? 'on' : ''}" data-act="connsecmode" data-arg="ref">使用既有密鑰參照</button></div>
      ${c.secretMode === 'once' ? `<input class="inp" type="password" autocomplete="new-password" data-act="connseconce" value="${esc(c.secretOnce)}" placeholder="${m.secret === 'keytab' || m.secret.includes('cert') || m.secret.includes('json') ? '貼上 base64 內容' : '輸入後只傳一次，不回顯'}" style="width:100%"><div class="note" style="margin-top:6px">送出後 API 直接寫入 ${S.user.scope === 'all' ? 'Secret Manager（GCP）／OpenBao（機房）' : '密鑰庫'}，回傳參照；表單與日誌都不保留。</div>` : `<input class="inp mono" data-act="connsecref" value="${esc(c.secretRef)}" placeholder="projects/dmp-prod/secrets/xxx/versions/3 或 kv/data/dmp/prod/xxx#password" style="width:100%">`}</div>` : `<div class="qsec"><div class="qsec-h"><b>密鑰</b></div><div class="note">此方式不需要長期密鑰：執行節點以 Workload Identity 取得短效權杖（約 1 小時），無輪替負擔。</div></div>`}
    <div class="qsec"><div class="qsec-h"><b>來源端最小權限（測試時逐項自檢）</b></div><ul class="priv">${privs.map(p => `<li>${esc(p)}</li>`).join('')}</ul><div class="note">只給唯讀與必要的 CDC 權限；不要 db_owner／superuser／專案層級 Viewer。網路：私有 IP／Private Google Access，來源端放行 dmp 固定出口 IP。</div></div>
    <div class="qsec"><div class="qsec-h"><b>連線測試</b><span class="note">逐步診斷：DNS → TCP → TLS → 鑑權 → 權限 → CDC 前置 → 抽樣讀取 → 時鐘</span></div>${testView}</div>
    </div><div class="ft"><button class="btn-ghost" data-act="conntest" ${c.testing ? 'disabled' : ''}>${c.testing ? '測試中…' : '測試連線'}</button><div class="right">${!c.isNew && CONNECTIONS.find(y => y.id === c.id)?.auth.secret ? `<button class="btn-ghost" data-act="connrotate" data-arg="${c.id}">輪替密鑰</button>` : ''}<button class="btn-ghost" data-act="connclose">取消</button><button class="btn-primary" data-act="connsave" ${c.test && c.test.ok ? '' : 'disabled'}>${c.isNew ? '建立連線' : '儲存'}</button></div></div></div>`;
}

function pageIntegration() {
  const t = S.integ.tab; const M = MODEL_EXAMPLE; const step = S.integ.step;
  const TYPECOL = { MSSQL: '#A4262C', BigQuery: '#4285F4', PostgreSQL: '#336791', SensorsData: '#0F8F85' };
  let body = '';
  if (t === 'connections') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>來源</th><th>接入方式</th><th>鑑權</th><th>密鑰／輪替</th><th>最後測試</th><th class="num">表 / 模型</th><th>延遲</th><th class="num">24h 筆數</th><th>狀態</th><th></th></tr>${CONNECTIONS.map(c => `<tr><td style="display:flex;gap:10px;align-items:center"><span class="conn-type" style="background:${TYPECOL[c.type]}">${c.type === 'SensorsData' ? 'SA' : c.type === 'PostgreSQL' ? 'PG' : c.type === 'BigQuery' ? 'BQ' : 'MS'}</span><span><b>${esc(c.cn)}</b><span class="sub mono">${esc(c.id)} · ${esc(c.type)}</span></span></td><td>${esc(c.mode)}</td><td><b style="font-size:12px">${esc(c.auth.cn)}</b><span class="sub mono">${esc(c.auth.fields.username || c.auth.fields.principal || c.auth.fields.iam_user || 'WI: dmp-ingest')}</span></td><td>${c.auth.secret ? `<span class="pill ${c.auth.secret.revoked ? 'gray' : c.auth.rotation.state === 'warn' ? 'amber' : c.auth.rotation.state === 'expired' ? 'red' : 'blue'}">${esc(c.auth.secret.provider)}${c.auth.secret.revoked ? ' · 已撤銷' : c.auth.rotation.kind === 'rotating' ? ` · ${c.auth.rotation.left} 天後到期` : ''}</span>` : '<span class="pill teal">無長期密鑰</span>'}${c.auth.note ? `<span class="sub">${esc(c.auth.note)}</span>` : ''}</td><td><span class="status ${c.auth.lastTest.ok ? 'ok' : 'warn'}"><span class="dot"></span>${c.auth.lastTest.ok ? '通過' : `失敗：${c.auth.lastTest.failedStep}`}</span><span class="sub mono">${esc(c.auth.lastTest.at)}</span></td><td class="num mono">${c.tables} / ${c.models}</td><td class="mono">${esc(c.lag)}</td><td class="num mono">${n0(c.rows24h)}</td><td><span class="status ${c.status}"><span class="dot"></span>${c.status === 'ok' ? '正常' : '注意'}</span>${c.note ? `<span class="sub">${esc(c.note)}</span>` : ''}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="connopen" data-arg="${c.id}">連線設定</button> <button class="btn-ghost sm" data-act="integtab" data-arg="modeling">建模</button></td></tr>`).join('')}</table></div></div>
    <div class="grid c21" style="margin-top:16px"><div class="card"><div class="card-h"><h3>鑑權原則</h3><span class="sub">connectors.yaml · auth.py</span></div><ul class="priv">${['平台資料庫永不存明文密鑰：只存密鑰庫參照（Secret Manager／OpenBao），UI 回顯一律遮罩', '優先無密鑰或短效憑證：BigQuery Workload Identity、Cloud SQL IAM token、MSSQL Kerberos；密碼只在來源不支援時使用', '最小權限：每來源一個專用唯讀帳號，連線測試逐項自檢；不給 db_owner／superuser', 'TLS 1.2+ 並驗證伺服器憑證（強制）；私有 IP／Private Google Access；來源端放行固定出口 IP', '輪替：密碼 90 天、服務帳號金鑰 90 天、API token 180 天；新舊並存 24 小時；到期前 14 天通知負責人', '密鑰只在執行節點取得；每次取用、測試、輪替都寫審計（secret.accessed、integration.connection.*）'].map(x => `<li>${x}</li>`).join('')}</ul></div>
    <div class="card"><div class="card-h"><h3>到期與告警</h3></div>${CONNECTIONS.filter(c => c.auth.secret && !c.auth.secret.revoked && c.auth.rotation.kind === 'rotating').map(c => `<div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line2)"><span style="min-width:0"><b>${esc(c.cn)}</b><span class="sub mono" style="display:block;font-size:11px;color:var(--muted);overflow-wrap:anywhere">${esc(c.auth.secret.ref)}</span></span><span class="pill ${c.auth.rotation.state === 'warn' ? 'amber' : 'blue'}">${c.auth.rotation.left} 天 · ${c.auth.rotation.due}</span><button class="btn-ghost sm" data-act="connrotate" data-arg="${c.id}">輪替</button></div>`).join('')}<div class="note" style="margin-top:8px">無密鑰連線（Workload Identity／IAM）不在此列；建議把 CRM 的帳密改為 Cloud SQL IAM 驗證即可免輪替。</div></div></div>
    <div class="grid c4" style="margin-top:16px">${[['MSSQL', 'JDBC 增量 / Debezium CDC', 'mssql-jdbc（MIT）、Debezium（Apache-2.0）'], ['BigQuery', 'Storage Read API（Arrow）', 'spark-bigquery-connector（Apache-2.0）'], ['PostgreSQL', 'JDBC / pgoutput CDC', 'pgjdbc（BSD-2）、Debezium'], ['SensorsData', '歷史事件一次性匯入', '查詢 API / Parquet → Iceberg；協議 1:1 映射']].map(x => `<div class="tile"><div class="lbl">${x[0]}</div><div style="font-size:12.5px;margin-top:6px;font-weight:600">${x[1]}</div><div class="delta"><span>${x[2]}</span></div></div>`).join('')}</div>`;
  else if (t === 'tables') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>數據源</th><th>表</th><th>主鍵</th><th>增量欄位</th><th class="num">欄位數</th><th class="num">Schema 版本</th><th>變更</th><th>使用它的任務</th><th></th></tr>${EXT_TABLES.map(x => `<tr><td class="mono">${esc(x.conn)}</td><td><b class="mono">${esc(x.table)}</b>${x.note ? `<span class="sub">${esc(x.note)}</span>` : ''}</td><td class="mono">${esc(x.pk)}</td><td class="mono" style="font-size:11.5px">${esc(x.incr)}</td><td class="num mono">${x.cols}</td><td class="num mono">v${x.schemaVer}</td><td>${x.drift ? `<span class="pill amber">${x.drift} 個新欄位待映射</span>` : '<span class="pill green">一致</span>'}</td><td>${x.tasks.map(k => `<button class="lnk" data-act="integtab" data-arg="tasks">${esc(k)}</button>`).join('<br>')}</td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="toast" data-arg="已重新探索 ${esc(x.table)} 的 Schema（審計 table.rescanned）">重新探索</button>${x.drift ? `<button class="btn-ghost sm" data-act="integstep" data-arg="4">處理變更</button>` : ''}</td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>數據表管理做什麼</h3></div><div class="note">從數據源探索表結構，設定主鍵與增量欄位（CDC 的 LSN／更新時間／分片日期），記錄 Schema 版本；來源新增或改型別時標記「變更」，由任務的 Schema 設定決定：自動映射、忽略、或暫停任務並通知。</div></div><div class="card"><div class="card-h"><h3>與同步任務的關係</h3></div><div class="note">一張表可被多個任務使用（例：帳戶表同時寫維度表與 OneID 綁定）；任務只能引用已登記的表。</div></div><div class="card"><div class="card-h"><h3>個資</h3></div><div class="note">探索時掃描欄位名與樣本，命中姓名／身分證／手機／Email 的欄位標 L4，任務無法選入。</div></div></div>`;
  else if (t === 'tasks') body = `<div class="card flush"><div class="tbl-wrap"><table class="tbl"><tr><th>任務</th><th>來源</th><th>目標</th><th>同步方式</th><th>排程</th><th>最近執行</th><th class="num">筆數</th><th>狀態</th><th></th></tr>${SYNC_TASKS.map(k => `<tr><td><b>${esc(k.cn)}</b><span class="sub mono">${esc(k.id)}${k.note ? ' · ' + esc(k.note) : ''}</span></td><td class="mono" style="font-size:11.5px">${esc(k.src)}</td><td><span class="pill ${k.target.startsWith('events') ? 'blue' : k.target.startsWith('users') ? 'purple' : k.target.startsWith('dim_') ? 'teal' : 'amber'}">${esc(k.target)}</span></td><td>${esc(k.mode)}</td><td class="mono">${esc(k.sched)}</td><td class="mono">${esc(k.last)}</td><td class="num mono">${n0(k.rows)}</td><td><span class="status ${k.status === 'done' ? 'ok' : k.status}"><span class="dot"></span>${({ ok: '正常', warn: '注意', done: '已完成' })[k.status]}</span></td><td class="num" style="white-space:nowrap"><button class="btn-ghost sm" data-act="toast" data-arg="原型：任務日誌（啟停、每次執行筆數、失敗原因、重試）">日誌</button>${k.status !== 'done' ? `<button class="btn-ghost sm" data-act="toast" data-arg="已${k.mode.includes('流') ? '重啟' : '重跑'} ${esc(k.id)}（審計 task.rerun）">重跑</button>` : ''}<button class="btn-ghost sm" data-act="toast" data-arg="已複製任務為 ${esc(k.id)}_copy（草稿）">複製</button></td></tr>`).join('')}</table></div></div>
    <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>目標類型決定寫入方式</h3></div><ul class="priv"><li><b>事件表 events</b>：只增量（append），需事件化映射（事件名、時間、identities、屬性）；event_id 去重。</li><li><b>用戶表 users</b>：upsert（profile_set 語意），映射到用戶屬性。</li><li><b>維度表 dim_*</b>：upsert by 主鍵或全量覆蓋。</li><li><b>OneID 綁定</b>：每列視為一條共現資料，走歸戶規則。</li><li><b>標籤</b>：外部算好的標籤值寫入 user_tags。</li></ul></div><div class="card"><div class="card-h"><h3>同步方式</h3></div><ul class="priv"><li>全量：覆蓋或 upsert；可排程。</li><li>增量：依增量欄位（更新時間／LSN／分片日期）拉取。</li><li>流任務：CDC（Debezium）或 Kafka 來源，持續執行。</li><li>任務可設依賴（前一任務完成後）、可複製、可暫停。</li></ul></div><div class="card"><div class="card-h"><h3>監控</h3></div><div class="note">每次執行記錄筆數、耗時、失敗原因；失敗自動重試 3 次後告警；Schema 變更依設定處理；所有啟停與重跑寫審計 task.*。</div></div></div>`;
  else if (t === 'modeling') {
    const steps = ['數據源', '選表與主鍵', '目標類型', '字段映射與轉換', '清洗規則', '預覽驗證', '排程發佈'];
    const stepper = `<div class="stepper">${steps.map((s, i) => `<button class="${i + 1 === step ? 'on' : i + 1 < step ? 'done' : ''}" data-act="integstep" data-arg="${i + 1}"><b>${i + 1 < step ? '✓' : `0${i + 1}`}</b>${s}</button>`).join('')}</div>`;
    let pane = '';
    if (step <= 2) pane = `<div class="card"><dl class="kv"><dt>數據源</dt><dd>${esc(M.source)}（已通過連線測試）</dd><dt>接入</dt><dd>CDC（Debezium SQL Server）· 已啟用 sys.sp_cdc_enable_table</dd><dt>表與主鍵</dt><dd>${M.src.length} 個欄位 · 主鍵 ORD_NO · 增量欄位 LSN · 狀態欄位 STATUS</dd></dl><div class="note" style="margin-top:10px">同一張表可建立多個任務（事件表、維度表、OneID 綁定）；數據表登記後 Schema 變更會被追蹤。</div></div>`;
    else if (step === 3) pane = `<div class="card"><div class="card-h"><h3>目標類型：事件表（狀態機事件化）</h3><span class="sub">其他目標：用戶表（upsert）、維度表（upsert by 主鍵）、OneID 綁定、標籤</span></div><table class="tbl"><tr><th>狀態欄位 STATUS</th><th>轉移</th><th>事件</th></tr>${[['insert', '→ N', 'bond_order'], ['N', '→ P', 'order_partial_filled'], ['*', '→ F', 'bond_deal'], ['*', '→ C', 'order_cancelled'], ['*', '→ R', 'order_rejected']].map(r => `<tr><td class="mono">${r[0]}</td><td class="mono">${r[1]}</td><td><span class="pill blue mono">${r[2]}</span></td></tr>`).join('')}</table><div class="note" style="margin-top:10px">主體：account（ACCT_NO 代碼化）· 人：CUST_ID → $identity_login_id（HMAC）</div></div>`;
    else if (step === 4) pane = `<div class="grid c2" style="grid-template-columns:1fr 1.4fr"><div class="card flush"><table class="tbl"><tr><th>來源欄位</th><th>型別</th><th class="num">空值率</th><th>樣本</th></tr>${M.src.map(r => `<tr><td class="mono" style="font-weight:600">${r[0]}</td><td class="mono" style="color:var(--muted)">${r[1]}</td><td class="num mono">${r[2]}</td><td class="mono" style="color:var(--body)">${esc(r[3])}</td></tr>`).join('')}</table></div><div class="card flush"><table class="tbl"><tr><th>來源</th><th></th><th>目標屬性（事件字典）</th><th>轉換</th></tr>${M.map.map(r => `<tr><td class="mono">${r[0]}</td><td class="map-arrow">→</td><td>${r[1].startsWith('（') ? `<span class="pill red">${r[1]}</span>` : `<span class="pill ${r[1].startsWith('$') ? 'purple' : 'blue'} mono">${r[1]}</span>`}</td><td class="mono" style="font-size:11px;color:var(--body)">${esc(r[2])}</td></tr>`).join('')}</table></div></div><div class="note" style="margin-top:10px">自動建議映射（名稱相似度 + 型別 + 歷史）命中 11/14；目標屬性不存在時可在此新增到事件字典（走審核）。</div>`;
    else if (step === 5) pane = `<div class="card flush"><table class="tbl"><tr><th>規則</th><th>內容</th><th>違反處理</th></tr>${M.rules.map(r => `<tr><td style="font-weight:600">${r[0]}</td><td class="mono" style="font-size:11.5px">${esc(r[1])}</td><td><span class="pill ${r[2].includes('拒絕') ? 'red' : r[2] === '隔離' ? 'amber' : 'gray'}">${r[2]}</span></td></tr>`).join('')}</table></div>`;
    else if (step === 6) pane = `<div class="grid c4"><div class="tile"><div class="lbl">樣本</div><div class="val">${n0(M.preview.sample)}</div></div><div class="tile"><div class="lbl">通過</div><div class="val" style="color:var(--st-good)">${n0(M.preview.ok)}</div></div><div class="tile"><div class="lbl">隔離</div><div class="val" style="color:var(--st-warn)">${M.preview.quarantined}</div><div class="delta"><span>${M.preview.reasons.join('；')}</span></div></div><div class="tile"><div class="lbl">個資命中</div><div class="val" style="color:var(--st-good)">${M.preview.pii}</div><div class="delta"><span>估算每日 ${M.preview.estDaily} 筆</span></div></div></div><div class="card" style="margin-top:16px"><div class="card-h"><h3>資料契約（自動產生）</h3></div><div class="code">source: trade_core.dbo.ORDER_HIST (cdc)\nentity: event  events: [bond_order, order_partial_filled, bond_deal, order_cancelled, order_rejected]\nidentities: [$identity_login_id(hmac), $identity_account_no(fpe, multi)]\nowner: 交易系統   sla: latency_p95 ≤ 10s   pii: none   lineage: raw.trade_cdc → dmp.events</div></div>`;
    else pane = `<div class="card"><dl class="kv"><dt>模式</dt><dd>即時（Debezium → Kafka raw.trade_cdc → Flink 事件化）</dd><dt>監控</dt><dd>延遲、筆數、隔離率、Schema drift 告警</dd><dt>血緣</dt><dd>已登記（OpenLineage）</dd></dl><div class="acts" style="margin-top:12px;display:flex;gap:8px"><button class="btn-primary" data-act="toast" data-arg="已發佈：Flink CDC 作業建立中（K8s Operator）">發佈</button><button class="btn-ghost" data-act="toast" data-arg="已輸出 models/mdl_order_hist.yaml">匯出 YAML</button></div></div>`;
    body = `<div class="card-h" style="margin-bottom:10px"><h3>${esc(M.name)}</h3><span class="pill">${esc(M.entity)}</span><div class="right"><button class="btn-ghost sm" data-act="integstep" data-arg="${Math.max(1, step - 1)}">上一步</button><button class="btn-primary" data-act="integstep" data-arg="${Math.min(7, step + 1)}">下一步</button></div></div>${stepper}${pane}`;
  } else body = `<div class="grid c2"><div class="card flush"><table class="tbl"><tr><th>時間</th><th>來源</th><th>原因</th><th class="num">筆數</th><th></th></tr>${[['14:20', 'trade_core', 'market 值 "X" 不在枚舉', 4], ['14:05', 'fund_pg', '必填 plan_type 空值（來源新增 PLAN_FREQ）', 38], ['13:50', 'ga_bq', 'time 超出 7 天（遲到資料）', 1210], ['12:30', 'crm_pg', '個資掃描：memo 含手機號', 2]].map(r => `<tr><td class="mono" style="color:var(--muted)">${r[0]}</td><td class="mono">${r[1]}</td><td>${esc(r[2])}</td><td class="num mono">${n0(r[3])}</td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="已重放（修正映射後）">重放</button></td></tr>`).join('')}</table></div><div class="card"><div class="card-h"><h3>隔離原則</h3></div><div class="note">不符規則的資料進隔離表而非丟棄；修正映射或規則後可重放。個資命中一律拒絕並告警，不進隔離表（不落地）。隔離率 &gt; 1% 時通知模型負責人。</div></div></div>`;
  return `<div class="page-h"><div><h1>數據整合（數據源的集成與管理）</h1><p>對齊神策「通用數據接入」：數據源管理 → 數據表管理 → 同步任務（目標：事件表／用戶表／維度表／OneID 綁定／標籤；全量／增量／流任務）→ 清洗與隔離。建立任務走可視化建模精靈。</p></div><div class="right"><button class="btn-primary" data-act="connnew" ${has('integration', 'd') ? '' : 'disabled title="需要數據整合的設定權限"'}>＋ 新增連線</button></div></div>
  <div class="filters"><div class="seg">${[['connections', '數據源管理'], ['tables', '數據表管理'], ['tasks', '同步任務'], ['modeling', '建立任務（建模）'], ['quarantine', '清洗與隔離']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="integtab" data-arg="${x[0]}">${x[1]}${x[0] === 'tasks' ? ` ${SYNC_TASKS.filter(k => k.status === 'warn').length ? '<span class="badge" style="background:var(--st-warn);color:#fff;border-radius:999px;padding:0 6px;font-size:10px">' + SYNC_TASKS.filter(k => k.status === 'warn').length + '</span>' : ''}` : ''}</button>`).join('')}</div></div>${body}`;
}

function sqlDrawer() {
  const k = S.sqlDrawer; if (!k) return '';
  const SQLS = {
    events_volume: `-- metric.query: 事件量 by source（語意層編譯）\nSELECT event_date, source, countMerge(cnt) AS events\nFROM dmp.events_daily_agg\nWHERE project='sinopac_demo' AND event_date >= today()-${S.range}\nGROUP BY event_date, source ORDER BY event_date;`,
    metric: `-- metric.query（語意層編譯，與事件分析頁設定一致）
${S._evSql}`,
    funnel: S._fnSql,
    path: S._ptSql,
    attribution: S._atSql,
    retention: S._rtSql,
  };
  return `<div class="drawer-bg" data-act="sqlclose"></div><div class="drawer"><div class="hd"><b>編譯後 SQL 與血緣</b><span class="pill blue">dry-run</span><button class="icon-btn x" data-act="sqlclose">✕</button></div><div class="bd stack">
    <div class="code">${esc(SQLS[k] || '')}</div>
    <dl class="kv"><dt>血緣</dt><dd class="mono">raw.ga_events, raw.trade_cdc → dmp.events → ${k === 'events_volume' ? 'dmp.events_daily_agg' : 'query'}</dd><dt>估算掃描</dt><dd class="mono">${(S.range * 0.21).toFixed(1)} GB</dd><dt>套用策略</dt><dd>${S.user.scope === 'all' ? 'RBAC：全域' : 'RBAC + 行級：branch_id IN (TPE01)'}；列級遮罩：account_no</dd><dt>同一編譯器</dt><dd>AI agent 的 metric.query / funnel.run 產生完全相同的 SQL</dd></dl>
    ${S.user.can.sql ? `<button class="btn-ghost" data-act="toast" data-arg="已開啟 SQL 沙箱（Trino，列級遮罩）">在 SQL 沙箱中編輯</button>` : '<div class="note">你的角色沒有 SQL 沙箱權限。</div>'}</div></div>`;
}

/* ------------------------------------------------------------------ chat */
function askSubmit(q) {
  q = (q || '').trim(); if (!q || S.ai.busy) return;
  S.route = { name: 'ai', tab: 'ask' }; S.ai.tab = 'ask'; S.ai.prefill = '';
  if (location.hash !== '#/ai/ask') location.hash = '#/ai/ask';
  const sc = ASK_SCRIPTS.find(s => s.match.some(k => q.includes(k))) || {
    plan: `以 semantic.search 對應「${q.slice(0, 24)}」到語意層；若無對應指標，回報可用的相近指標而不自創口徑。`,
    calls: [['semantic.search', `"${q.slice(0, 20)}"`, '0 B'], ['knowledge.search', '事件字典 / 指標說明', '0 B']],
    ans: `<b>原型中沒有這題的示範資料。</b><p>正式版會把問題對應到語意層的指標與維度後取數；若找不到對應指標，agent 會列出最接近的指標（例如：成交客戶數、委託失敗率、開戶→入金轉化率）請你確認，而不是自行拼 SQL。</p>`,
    warn: null, acts: ['看可用指標', '改問：上週開戶到入金的轉化率？'],
  };
  S.ai.chat.push({ who: 'user', text: q });
  const m = { who: 'ai', plan: sc.plan, calls: sc.calls, done: 1, final: false, ans: sc.ans, warn: sc.warn, acts: sc.acts, card: CONFIG_CARDS[q] || null };
  S.ai.chat.push(m); S.ai.busy = true; render(); scrollChat();
  const step = () => { if (m.done < m.calls.length) { m.done++; render(); scrollChat(); setTimeout(step, 550); } else { setTimeout(() => { m.final = true; S.ai.busy = false; render(); scrollChat(); setTimeout(() => document.getElementById('askin')?.focus(), 50); }, 600); } };
  setTimeout(step, 600);
}
function scrollChat() { const el = document.getElementById('chatlog'); if (el) el.scrollTop = el.scrollHeight; }

/* ------------------------------------------------------------------ render */
let render = function () {
  const root = document.getElementById('root');
  if (!S.user) { root.innerHTML = viewGate(); return; }
  const pages = { overview: pageOverview, events: pageEvents, funnel: pageFunnel, path: pagePath, attribution: pageAttribution, users: pageUsers, integration: pageIntegration, retention: pageRetention, iam: pageIAM, audit: pageAudit, profile: pageProfile, segments: pageSegments, triggers: pageTriggers, ai: pageAI, governance: pageGovernance, userschema: pageUserSchema, items: pageItems, pipeline: pagePipeline, settings: pageSettings };
  const page = canRoute(S.route.name) ? (pages[S.route.name] || pageOverview)() : pageForbidden();
  const active = document.activeElement; const keep = active && active.dataset && active.dataset.act; const keepArg = active && active.dataset && active.dataset.arg; const selStart = active && active.selectionStart;
  root.innerHTML = `<div class="app">${viewTopNav()}${viewSubNav()}<main class="content"><div class="wrap">${page}</div></main></div>${viewMobileNav()}${sqlDrawer()}${connDrawer()}${metaDrawer()}${segDrawer()}${roleDrawer()}${pushDrawer()}${S.toast ? `<div class="toast">${esc(S.toast)}</div>` : ''}`;
  if (keep) { const el = root.querySelector(keepArg != null ? `[data-act="${keep}"][data-arg="${keepArg}"]` : `[data-act="${keep}"]`); if (el && el.focus) { el.focus(); try { if (selStart != null) el.setSelectionRange(selStart, selStart); } catch (e) { /* non-text */ } } }
}

/* ------------------------------------------------------------------ events */
document.addEventListener('click', e => {
  const b = e.target.closest('[data-act]'); if (!b) return; const act = b.dataset.act, arg = b.dataset.arg;
  const A = {
    login: () => { S.user = ACCOUNTS.find(a => a.id === arg); S.route = parseHash(); render(); },
    logout: () => { S.user = null; S.ai.chat = []; render(); },
    goto: () => { S.navOpen = false; go(arg); if (location.hash === arg) render(); }, navtoggle: () => { S.navOpen = !S.navOpen; render(); }, navclose: () => { S.navOpen = false; render(); }, qcfg: () => { S.qcfgCollapsed = !S.qcfgCollapsed; render(); }, collapse: () => { S.collapsed = !S.collapsed; render(); }, noop: () => {},
    range: () => { S.range = Number(arg); render(); },
    // v0.4 事件分析
    evmadd: () => { if (S.ev.metrics.length >= 5) return; const nxt = ['app_login_success', 'hq_stockdetail_view', 'bond_order', 'fund_deposit', 'kh_review_pass'].find(e => !S.ev.metrics.some(m => m.ev === e)) || 'app_login_success'; S.ev.metrics.push({ ev: nxt, agg: 'users', prop: 'amount', filters: { logic: 'and', items: [] }, open: false }); render(); },
    evmdel: () => { S.ev.metrics.splice(Number(arg), 1); render(); }, evmfilter: () => { const m = S.ev.metrics[Number(arg)]; m.open = !m.open; if (m.open && !m.filters.items.length) m.filters.items.push(newCond('platform')); render(); },
    evgdel: () => { S.ev.groups = S.ev.groups.filter(g => g !== arg); render(); }, evgran: () => { S.ev.gran = arg; if (arg === 'total' && S.ev.chart === 'line') S.ev.chart = 'bar'; render(); },
    evchart: () => { S.ev.chart = arg; render(); }, evcompare: () => { S.ev.compare = !S.ev.compare; render(); }, evtableall: () => { S.ev.tableAll = !S.ev.tableAll; render(); },
    evrun: () => toast(`查詢完成：${S.ev.metrics.length} 個指標 · ${S.ev.groups.length} 個分組 · ${S.ev.filters.items.length} 條篩選（${(0.3 + S.ev.metrics.length * .18 + S.ev.groups.length * .25).toFixed(2)} s，審計 query.events）`),
    evsave: () => { const nm = `${metricLabel(S.ev.metrics[0])}${S.ev.groups.length ? ' × ' + S.ev.groups.map(g => propOf(g).cn).join('/') : ''}（近 ${S.range} 天）`; S.ev.saved.push(nm); toast('已儲存報表：' + nm); },
    evreset: () => { resetEvents(); toast('已重置查詢條件'); },
    evusers: () => { const [nm, d] = arg.split('｜'); S.users.from = { label: `事件分析 · ${nm} · ${d}`, n: null }; S.users.page = 0; go('#/users'); },
    // v0.4 篩選條件建構器（ns:index）
    fbadd: () => { const f = fbRef(arg); f.items.push(newCond(f.items.length ? 'u.lifecycle' : 'platform')); render(); },
    fbdel: () => { const [ns, i] = arg.split(':'); fbRef(ns).items.splice(Number(i), 1); render(); }, fbclear: () => { fbRef(arg).items = []; render(); },
    fblogic: () => { const [ns, l] = arg.split(':'); fbRef(ns).logic = l; render(); },
    // v0.4 漏斗
    funnelid: () => { resetFunnel(arg); render(); }, funnelcmp: () => { S.funnel.compare = !S.funnel.compare; render(); },
    fnstep: () => { S.funnel.sel = Number(arg); render(); }, fnview: () => { S.funnel.view = arg; render(); },
    fnsadd: () => { if (S.funnel.steps.length >= 8) return; const e = evOf('app_login_success'); S.funnel.steps.push({ ev: e.name, cn: e.cn, filters: { logic: 'and', items: [] }, open: false }); render(); },
    fnsdel: () => { S.funnel.steps.splice(Number(arg), 1); S.funnel.sel = Math.min(S.funnel.sel, S.funnel.steps.length - 2); render(); },
    fnup: () => { const i = Number(arg); if (!i) return; const st = S.funnel.steps; [st[i - 1], st[i]] = [st[i], st[i - 1]]; render(); },
    fnsfilter: () => { const st = S.funnel.steps[Number(arg)]; st.open = !st.open; if (st.open && !st.filters.items.length) st.filters.items.push(newCond(st.ev === '$pageview' ? 'page_group' : 'platform')); render(); },
    fnrun: () => toast(`漏斗已重算：${S.funnel.steps.length} 步 · 窗口 ${S.funnel.window.n} ${S.funnel.window.unit}${S.funnel.group !== 'none' ? ' · 分組對比' : ''}（windowFunnel，1.1 s，審計 query.funnel）`),
    fnsave: () => { const nm = `${S.funnel.steps[0].cn} → ${S.funnel.steps[S.funnel.steps.length - 1].cn}（${S.funnel.window.n} ${S.funnel.window.unit}）`; S.funnel.saved.push(nm); toast('已儲存漏斗：' + nm); },
    fnreset: () => { resetFunnel(S.funnel.id); toast('已重置漏斗設定'); },
    fnusers: () => { const [kind, i] = arg.split(':'); const f = S.funnel; const c = funnelCounts(); const k = Number(i); S.users.from = { label: `漏斗「${f.name}」步驟 ${k + 1} → ${k + 2} ${kind === 'lost' ? '流失' : '轉化'}用戶`, n: kind === 'lost' ? c.n[k] - c.n[k + 1] : c.n[k + 1] }; S.users.page = 0; go('#/users'); },
    ufromclear: () => { S.users.from = null; render(); },
    // v0.5 治理
    govstatus: () => { S.gov.status = arg; render(); }, metanew: () => { if (!govWrite()) { toast('需要治理設定權限'); return; } openMeta(null); }, metaopen: () => openMeta(arg), metaclose: () => { S.meta = null; render(); },
    metaseg: () => { const [k, v] = arg.split(':'); S.meta[k] = v; render(); }, metapropdel: () => { S.meta.props.splice(Number(arg), 1); render(); },
    metasave: () => { if (!govWrite()) { toast('需要治理設定權限'); return; } metaSave(arg); }, metastatus: () => { const e = EVENTS.find(x => x.name === S.meta.orig); e.status = arg; S.meta = null; toast(`事件 ${e.name} 已停用：不再接收，歷史保留；審計 meta.event.deprecated`); },
    ustab: () => { S.us.tab = arg; render(); }, itemtype: () => { S.items.type = arg; S.items.tab = 'tables'; render(); }, itemtab: () => { S.items.tab = arg; render(); }, pipetab: () => { S.pipe.tab = arg; render(); },
    // v0.4.2 連線與鑑權
    connnew: () => openConn(null), connopen: () => openConn(arg), connclose: () => { S.conn = null; render(); },
    conntype: () => connSetType(arg), connmethod: () => connSetMethod(arg), connmode: () => { S.conn.mode = arg; S.conn.test = null; render(); }, connsecmode: () => { S.conn.secretMode = arg; S.conn.test = null; render(); },
    conntest: () => connTest(), connsave: () => connSave(), connrotate: () => { if (!has('integration', 'd')) { toast('需要數據整合的設定權限'); return; } connRotate(arg); },
    // v0.4.1 行為路徑
    pathdir: () => { S.path.dir = arg; S.path.sel = null; render(); }, pathanchorf: () => { S.path.anchorOpen = !S.path.anchorOpen; if (S.path.anchorOpen && !S.path.anchorFilters.items.length) S.path.anchorFilters.items.push(newCond('platform')); render(); },
    pathev: () => { const e = S.path.events; const i = e.indexOf(arg); i >= 0 ? e.splice(i, 1) : e.push(arg); S.path.sel = null; render(); }, pathsplit: () => { S.path.split[arg] = !S.path.split[arg]; S.path.sel = null; render(); },
    pathsteps: () => { S.path.steps = Number(arg); S.path.sel = null; render(); }, pathmerge: () => { S.path.merge = !S.path.merge; render(); }, pathunit: () => { S.path.unit = arg; render(); }, pathsample: () => { S.path.sample = arg; render(); },
    pathview: () => { S.path.view = arg; render(); }, pathnode: () => { S.path.sel = S.path.sel === arg ? null : arg; if (S.path.view === 'list') S.path.view = 'sankey'; render(); },
    pathsetanchor: () => { S.path.anchor = arg; S.path.sel = null; if (!S.path.events.includes(arg)) S.path.events.push(arg); render(); },
    pathusers: () => { const [nm, c] = arg.split('｜'); S.users.from = { label: c === 'path' ? `行為路徑 · ${nm}` : `行為路徑 · 第 ${c} 步「${nm}」`, n: null }; S.users.page = 0; go('#/users'); },
    pathrun: () => toast(`路徑已重算：${S.path.steps} 步 · ${S.path.events.length} 個事件 · 會話 ${S.path.session} 分鐘（${S.path.sample === '1/10' ? 'SAMPLE 1/10，0.9 s' : '全量，4.2 s'}，審計 query.path）`),
    pathsave: () => { const nm = `${evOf(S.path.anchor).cn}${S.path.dir === 'after' ? '之後' : '之前'} ${S.path.steps} 步`; S.path.saved.push(nm); toast('已儲存路徑：' + nm); }, pathreset: () => { resetPath(); toast('已重置路徑設定'); },
    // v0.4.1 歸因
    attrmodel: () => { S.attr.model = arg; render(); }, attrview: () => { S.attr.view = arg; render(); }, attrgoalf: () => { S.attr.goalOpen = !S.attr.goalOpen; if (S.attr.goalOpen && !S.attr.goalFilters.items.length) S.attr.goalFilters.items.push(newCond('market')); render(); },
    attrtadd: () => { if (S.attr.touches.length >= 10) return; S.attr.touches.push({ name: `觸點 ${S.attr.touches.length + 1}`, ev: 'act_click', filters: newFilter(), open: false }); render(); },
    attrtdel: () => { S.attr.touches.splice(Number(arg), 1); render(); }, attrtfilter: () => { const t = S.attr.touches[Number(arg)]; t.open = !t.open; if (t.open && !t.filters.items.length) t.filters.items.push(newCond('$utm_source')); render(); },
    attrusers: () => { S.users.from = { label: `歸因分析 · 經「${arg}」轉化的 ${evOf(S.attr.goal).cn} 用戶（${ATTR_MODELS.find(x => x[0] === (S.attr.model === 'all' ? 'position' : S.attr.model))[1]}）`, n: null }; S.users.page = 0; go('#/users'); },
    attrrun: () => toast(`歸因已重算：${S.attr.touches.length} 個觸點 · 回溯 ${S.attr.window.n} ${S.attr.window.unit} · 五模型同一次掃描（1.4 s，審計 query.attribution）`),
    attrsave: () => { const nm = `${evOf(S.attr.goal).cn}歸因（${ATTR_MODELS.find(x => x[0] === S.attr.model)[1]}，${S.attr.window.n} ${S.attr.window.unit}）`; S.attr.saved.push(nm); toast('已儲存報表：' + nm); }, attrreset: () => { resetAttr(); toast('已重置歸因設定'); },
    // v0.4.1 留存
    retinitf: () => { S.ret.initOpen = !S.ret.initOpen; if (S.ret.initOpen && !S.ret.initFilters.items.length) S.ret.initFilters.items.push(newCond('channel')); render(); }, retretf: () => { S.ret.retOpen = !S.ret.retOpen; if (S.ret.retOpen && !S.ret.retFilters.items.length) S.ret.retFilters.items.push(newCond('market')); render(); },
    retany: () => { S.ret.retAny = !S.ret.retAny; render(); }, retgran: () => { S.ret.gran = arg; S.ret.periods = arg === 'day' ? 14 : arg === 'week' ? 8 : 6; S.ret.sel = null; render(); },
    retcalc: () => { S.ret.calc = arg; render(); }, retmode: () => { S.ret.mode = arg; render(); }, retshow: () => { S.ret.show = arg; render(); }, retview: () => { S.ret.view = arg; render(); },
    retcell: () => { const [label, i] = arg.split(':'); S.ret.sel = label; S.users.from = { label: `留存分析 · cohort ${label} 第 ${i} ${RET_GRANS.find(g => g[0] === S.ret.gran)[1]}${S.ret.mode === 'churn' ? '流失' : '留存'}用戶（${evOf(S.ret.init).cn} → ${S.ret.retAny ? '任意事件' : evOf(S.ret.ret).cn}）`, n: null }; S.users.page = 0; go('#/users'); },
    retrun: () => toast(`留存已重算：${S.ret.periods} ${RET_GRANS.find(g => g[0] === S.ret.gran)[1]} · ${S.ret.calc === 'range' ? '區間' : '當期'}口徑（retention()，0.7 s，審計 query.retention）`),
    retsave: () => { const nm = `${evOf(S.ret.init).cn} → ${S.ret.retAny ? '任意事件' : evOf(S.ret.ret).cn}（${RET_GRANS.find(g => g[0] === S.ret.gran)[1]}，${S.ret.periods} 期）`; S.ret.saved.push(nm); toast('已儲存報表：' + nm); }, retreset: () => { resetRet(); toast('已重置留存設定'); },
    psel: () => { S.profile.sel = arg; render(); }, punlock: () => { if (!S.profile.reason.trim()) { toast('請先填寫查詢理由'); return; } S.profile.unlocked[arg] = S.profile.reason; S.profile.reason = ''; toast('已記錄審計：profile.timeline（' + arg + '）'); },
    segtab: () => { S.seg.tab = arg; render(); }, segopen: () => { S.segDrawer = arg; render(); }, segclose: () => { S.segDrawer = null; render(); },
    approve: () => { if (!has('segments', 'a')) { toast('你的角色沒有分群審批權限'); return; } S.approved[arg] = true; S.segDrawer = null; toast('已核准並生效，審計已記錄'); },
    sql: () => { S.sqlDrawer = arg; render(); }, sqlclose: () => { S.sqlDrawer = null; render(); },
    aitab: () => { S.ai.tab = arg; go('#/ai/' + arg); }, aikind: () => { S.ai.kind = arg; render(); }, aistatus: () => { S.ai.status = arg; render(); },
    insopen: () => { S.ai.open[arg] = !S.ai.open[arg]; render(); },
    fb: () => { const [id, v] = arg.split(':'); S.feedback[id] = v; toast(v === 'useful' ? '已回饋：有用（進入 agent 的排序與 few-shot）' : '已回饋：有誤（reviewer 會重跑證據）'); },
    askpre: () => { S.ai.prefill = arg; S.ai.tab = 'ask'; S.segDrawer = null; S.sqlDrawer = null; go('#/ai/ask'); setTimeout(() => document.getElementById('askin')?.focus(), 60); },
    asksug: () => askSubmit(arg), asksend: () => askSubmit(document.getElementById('askin')?.value),
    chatact: () => { if (arg.includes('漏斗')) go('#/funnel'); else if (arg.includes('分群')) go('#/segments'); else if (arg.includes('細查')) go('#/profile'); else if (arg.includes('ins_01')) { S.ai.tab = 'insights'; S.ai.status = 'all'; go('#/ai/insights'); } else if (arg.includes('指標')) go('#/events'); else if (arg.startsWith('改問')) askSubmit(arg.replace('改問：', '')); else toast(arg.includes('洞察') ? '已存為洞察（insight.create, kind=answer）' : '已提議（status=pending_approval）'); },
    govtab: () => { S.gov.tab = arg; render(); },
    usel: () => { S.users.sel[arg] = !S.users.sel[arg]; render(); },
    upage: () => { S.users.page = Math.max(0, S.users.page + Number(arg)); render(); },
    uprofile: () => { S.profile.sel = PROFILES.some(p => p.one_id === arg) ? arg : PROFILES[0].one_id; go('#/profile'); },
    integtab: () => { S.integ.tab = arg; render(); },
    iamtab: () => { S.iam.tab = arg; go('#/iam/' + arg); },
    pushopen: () => { if (!has('segments', 'u')) { toast('你的角色沒有分群／推送的使用權限'); return; } openPush(arg); }, pushclose: () => { S.push = null; render(); },
    pushdest: () => { const d = destById(arg); if (d.status === 'paused') { toast('此目的地預設關閉，需系統管理員核可後啟用'); return; } S.push.dest[arg] = !S.push.dest[arg]; render(); },
    pushmode: () => { S.push.mode = arg; if (arg === 'realtime') S.push.schedule = 'realtime'; render(); }, pushsched: () => { S.push.schedule = arg; render(); },
    pushtag: () => { const t = S.push.tags; const i = t.indexOf(arg); i >= 0 ? t.splice(i, 1) : t.push(arg); render(); },
    pushsave: () => { const p = S.push; const n = Object.values(p.dest).filter(Boolean).length; if (!n) { toast('請至少選一個目的地'); return; } S.push = null; toast(p.mode === 'full' ? `推送單已送審（${n} 個目的地，${p.mode}）；核准後依排程執行` : `已儲存排程（${n} 個目的地，增量，日批後）；審計 sync.delivered`); },
    rolenew: () => openRoleEditor(null, 'new'), roleopen: () => openRoleEditor(arg, 'edit'), rolecopy: () => openRoleEditor(arg, 'copy'),
    roleclose: () => { S.roleEdit = null; render(); }, rolesave: () => roleSave(), roledelete: () => roleDelete(),
    rolechk: () => { if (!has('iam', 'd')) return; const [m, a] = arg.split(':'); roleToggle(m, a); },
    rolerow: () => { if (has('iam', 'd')) roleRowAll(arg); }, rolecol: () => { if (has('iam', 'd')) roleColAll(arg); },
    reveal: () => toast('揭露申請已送出：' + arg + '，時效 24 小時，L3 需系統管理員、L2 需數位經營審批；揭露期間每次查看都會寫入審計'),
    integstep: () => { S.integ.tab = 'modeling'; S.integ.step = Number(arg); render(); },
    toast: () => toast(arg),
  };
  if (A[act]) A[act]();
});
document.addEventListener('change', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  const act = el.dataset.act, arg = el.dataset.arg;
  if (act === 'evmev') { const m = S.ev.metrics[Number(arg)]; m.ev = el.value; render(); }
  if (act === 'evmagg') { const m = S.ev.metrics[Number(arg)]; m.agg = el.value; const a = AGGS.find(x => x[0] === m.agg); if (a[2] === 'num' && !NUM_PROPS.some(p => p.k === m.prop)) m.prop = 'amount'; if (a[2] === 'prop' && !m.prop) m.prop = 'symbol'; render(); }
  if (act === 'evmprop') { S.ev.metrics[Number(arg)].prop = el.value; render(); }
  if (act === 'evgadd' && el.value) { S.ev.groups.push(el.value); render(); }
  if (act === 'evtopn') { S.ev.topN = Number(el.value); render(); }
  if (act === 'fbprop') { const [ns, i] = arg.split(':'); const f = fbRef(ns); f.items[Number(i)] = newCond(el.value); render(); }
  if (act === 'fbop') { const [ns, i] = arg.split(':'); const c = fbRef(ns).items[Number(i)]; c.op = el.value; const p = propOf(c.k); if (['eq', 'neq', 'in'].includes(c.op) && p.vals && !p.vals.includes(c.v)) c.v = p.vals[0]; render(); }
  if (act === 'fbval' || act === 'fbval2') { const [ns, i] = arg.split(':'); const c = fbRef(ns).items[Number(i)]; c[act === 'fbval' ? 'v' : 'v2'] = el.value; render(); }
  if (act === 'fnev') { const st = S.funnel.steps[Number(arg)]; st.ev = el.value; st.cn = evOf(el.value).cn; render(); }
  if (act === 'fnwin') { S.funnel.window.n = Math.max(1, Math.min(3650, Number(el.value) || 1)); render(); }
  if (act === 'fnwinu') { S.funnel.window.unit = el.value; render(); }
  if (act === 'fnassoc') { S.funnel.assoc = el.value; render(); }
  if (act === 'fngroup') { S.funnel.group = el.value; render(); }
  if (act === 'pathanchor') { S.path.anchor = el.value; S.path.sel = null; if (!S.path.events.includes(el.value)) S.path.events.push(el.value); render(); }
  if (act === 'pathsession') { S.path.session = Number(el.value); render(); } if (act === 'pathgroup') { S.path.group = el.value; render(); } if (act === 'pathmust') { S.path.must = el.value; render(); }
  if (act === 'attrgoal') { S.attr.goal = el.value; render(); } if (act === 'attrgm') { S.attr.goalMetric = el.value; render(); } if (act === 'attrgp') { S.attr.goalProp = el.value; render(); }
  if (act === 'attrtname') { S.attr.touches[Number(arg)].name = el.value; render(); } if (act === 'attrtev') { S.attr.touches[Number(arg)].ev = el.value; render(); }
  if (act === 'attrassoc') { S.attr.assocEv = el.value; render(); } if (act === 'attrassocp') { S.attr.assocProp = el.value; render(); } if (act === 'attrpos') { S.attr.posWeights = el.value; render(); }
  if (act === 'attrhl') { S.attr.halfLife = Math.max(1, Math.min(90, Number(el.value) || 7)); render(); } if (act === 'attrwin') { S.attr.window.n = Math.max(1, Math.min(365, Number(el.value) || 14)); render(); } if (act === 'attrwinu') { S.attr.window.unit = el.value; render(); }
  if (act === 'attrgroup') { S.attr.group = el.value; render(); }
  if (act === 'retinit') { S.ret.init = el.value; S.ret.sel = null; render(); } if (act === 'retret') { S.ret.ret = el.value; render(); } if (act === 'retperiods') { S.ret.periods = Math.max(1, Math.min(30, Number(el.value) || 8)); render(); }
  if (act === 'metaf') { S.meta[arg] = el.value; render(); } if (act === 'metaprop') { const [i, k] = arg.split(':'); S.meta.props[Number(i)][k] = el.value; render(); }
  if (act === 'metapropadd' && el.value) { const mp = META_PROPS.find(x => x.k === el.value); S.meta.props.push({ k: mp.k, type: mp.type, sens: mp.sens }); render(); }
  if (act === 'iamsim') { S.iam.simRole = el.value; render(); }
  if (act === 'retassoc') { S.ret.assoc = el.value; render(); } if (act === 'retgroup') { S.ret.group = el.value; if (el.value === 'none' && S.ret.view === 'group') S.ret.view = 'table'; render(); }
  if (el.dataset.act === 'rolescope' && S.roleEdit) { S.roleEdit.scope = el.value; render(); }
  if (el.dataset.act === 'ulc') { S.users.lc = el.value; S.users.page = 0; render(); }
  if (el.dataset.act === 'utier') { S.users.tier = el.value; S.users.page = 0; render(); }
});
document.addEventListener('input', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  if (el.dataset.act === 'pq') { S.profile.q = el.value; render(); }
  if (el.dataset.act === 'fbval' || el.dataset.act === 'fbval2') { const [ns, i] = el.dataset.arg.split(':'); const c = fbRef(ns).items[Number(i)]; if (c) c[el.dataset.act === 'fbval' ? 'v' : 'v2'] = el.value; }
  if (el.dataset.act === 'fnwin') S.funnel.window.n = Math.max(1, Math.min(3650, Number(el.value) || 1));
  if (el.dataset.act === 'attrtname') S.attr.touches[Number(el.dataset.arg)].name = el.value;
  if (S.meta && el.dataset.act === 'metaf') S.meta[el.dataset.arg] = el.value;
  if (el.dataset.act === 'govq') { S.gov.q = el.value; render(); }
  if (S.conn) { const a = el.dataset.act; if (a === 'conncn') S.conn.cn = el.value; if (a === 'connowner') S.conn.owner = el.value; if (a === 'connfield') { S.conn.fields[el.dataset.arg] = el.value; S.conn.test = null; } if (a === 'connseconce') S.conn.secretOnce = el.value; if (a === 'connsecref') S.conn.secretRef = el.value; }
  if (el.dataset.act === 'attrhl') S.attr.halfLife = Number(el.value) || S.attr.halfLife; if (el.dataset.act === 'attrwin') S.attr.window.n = Number(el.value) || S.attr.window.n; if (el.dataset.act === 'retperiods') S.ret.periods = Math.max(1, Math.min(30, Number(el.value) || S.ret.periods));
  if (S.roleEdit) { if (el.dataset.act === 'rolecn') S.roleEdit.cn = el.value; if (el.dataset.act === 'roledesc') S.roleEdit.desc = el.value; if (el.dataset.act === 'rolelookback') S.roleEdit.lookback = Number(el.value) || 0; }
  if (el.dataset.act === 'uq') { S.users.q = el.value; S.users.page = 0; render(); }
  if (el.dataset.act === 'preason') S.profile.reason = el.value;
  if (el.dataset.act === 'askinput') S.ai.prefill = el.value;
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return; const el = e.target;
  if (el.dataset && el.dataset.act === 'askbar') { askSubmit(el.value); el.value = ''; }
  if (el.dataset && el.dataset.act === 'askinput') askSubmit(el.value);
  if (el.dataset && el.dataset.act === 'metanewprop' && S.meta) { const k = el.value.trim(); if (/^[a-z$][a-z0-9_]{1,40}$/.test(k) && !S.meta.props.some(p => p.k === k)) { S.meta.props.push({ k, type: 'string', sens: 'L1' }); render(); } else toast('屬性名稱須為小寫英數與 _（可 $ 開頭），且不重複'); }
});
document.addEventListener('pointermove', onChartMove);
document.addEventListener('pointerout', onChartLeave);
window.addEventListener('hashchange', () => { S.route = parseHash(); S.navOpen = false; if (S.route.name === 'ai' && S.route.tab) S.ai.tab = S.route.tab; if (S.route.name === 'governance' && S.route.tab) S.gov.tab = S.route.tab; if (S.route.name === 'iam' && S.route.tab) S.iam.tab = S.route.tab; if (S.route.name === 'items' && S.route.tab) { if (S.route.tab.startsWith('dim_')) { S.items.type = S.route.tab; S.items.tab = 'tables'; } else S.items.tab = S.route.tab; } if (S.route.name === 'integration' && S.route.tab) S.integ.tab = S.route.tab; render(); });

S.route = parseHash(); if (S.route.name === 'ai' && S.route.tab) S.ai.tab = S.route.tab;
render();
