// AltaTech DMP 原型 v0.1
// 側欄 + 頂欄 + 內容的外框沿用 AltaTech 原型系列；頁面：總覽、事件分析、漏斗、留存、用戶細查、標籤分群、即時觸發、AI 洞察、治理、設定。
// 所有數字來自 data.js 的模擬資料；圖表為內嵌 SVG（dataviz 規範：細線 2px、單軸、固定類別色序、hover 提示）。
'use strict';

import { ACCOUNTS, SOURCES, EVENTS, TS, PLATFORM_SPLIT, CHANNEL_SPLIT, UTM_SPLIT, FUNNELS, RETENTION, TAGS, SEGMENTS,
         TRIGGERS, TRIGGER_FEED, PROFILES, INSIGHTS, AGENTS, TOOLS, ASK_SCRIPTS, TODAY, dkey, dayOffset,
         PATHS, ATTRIBUTION, USERS, CONNECTIONS, MODEL_EXAMPLE, CONFIG_CARDS } from './data.js?v=20261014.2';

const VERSION = 'v0.2.0';
const BUILD = '20261014.2';

/* ------------------------------------------------------------------ state */
const S = {
  user: null, route: { name: 'overview' }, collapsed: false,
  range: 30,                                   // 7 / 30 / 90 天
  ev: { metric: 'traders', group: 'none', gran: '日' },
  funnel: { id: 'open', compare: true, group: 'none' },
  profile: { q: '', sel: null, reason: '', unlocked: {} },
  seg: { tab: 'segments' }, segDrawer: null,
  ai: { tab: 'insights', kind: 'all', status: 'open', open: {}, chat: [], busy: false, prefill: '' },
  gov: { tab: 'dictionary' },
  path: { steps: 4 }, attr: { model: 'position' }, users: { q: '', lc: 'all', tier: 'all', page: 0, sel: {} }, integ: { tab: 'connections', step: 4 },
  sqlDrawer: null, toast: null,
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
  code:  '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  send:  '<path d="M3 11l18-8-8 18-2-8z" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
  up:    '<path d="M12 19V5M6 11l6-6 6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  down:  '<path d="M12 5v14M6 13l6 6 6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
};
const svg = (p, size = 18, cls = '') => `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none">${p}</svg>`;
const LOGO = `<svg class="logo" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#1F4E8C"/><path d="M14 44 L26 18 L32 32 L38 18 L50 44" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/><circle cx="32" cy="44" r="4" fill="#5FD3C7"/></svg>`;
const SER = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)', 'var(--s7)', 'var(--s8)'];

/* ------------------------------------------------------------------ charts */
function ticks(max) { const raw = max / 4; const p = Math.pow(10, Math.floor(Math.log10(raw))); const n = [1, 2, 2.5, 5, 10].find(k => k * p >= raw) * p; const out = []; for (let v = 0; v <= max + n * .001; v += n) out.push(v); return out; }
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
function funnelView(f, { compare = true } = {}) {
  const top = f.steps[0].n;
  return `<div class="funnel">${f.steps.map((s, i) => {
    const conv = i ? s.n / f.steps[i - 1].n : 1; const convP = i ? s.prev / f.steps[i - 1].prev : 1;
    const drop = i ? `<div class="fdrop"><span></span><span>轉化 <b style="color:${conv - convP < -.03 ? 'var(--st-serious)' : 'var(--ink2)'}">${pct(conv)}</b>${compare ? ` <span style="color:var(--muted)">（前期 ${pct(convP)}，${pt(conv - convP)}）</span>` : ''} · 流失 ${n0(f.steps[i - 1].n - s.n)} 人</span></div>` : '';
    return `${drop}<div class="fstep"><div class="nm">${esc(s.cn)}<small class="mono">${esc(s.ev)}</small></div><div class="trk">${compare ? `<div class="fill cmp" style="width:${(s.prev / top * 100).toFixed(1)}%"></div>` : ''}<div class="fill" style="width:${(s.n / top * 100).toFixed(1)}%;${compare ? 'bottom:50%' : ''}"></div></div><div class="v">${n0(s.n)}<small>${pct(s.n / top)} 總體</small></div></div>`;
  }).join('')}</div>${compare ? `<div class="legend"><span class="k"><i class="sw rect" style="background:var(--s1)"></i>本期</span><span class="k"><i class="sw rect" style="background:var(--s2)"></i>前期</span></div>` : ''}`;
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

/* ------------------------------------------------------------------ routing */
function parseHash() { const h = (location.hash || '#/overview').replace(/^#\/?/, ''); const p = h.split('/').filter(Boolean); return { name: p[0] || 'overview', tab: p[1], arg: p[2] }; }

/* ------------------------------------------------------------------ shell */
function viewGate() {
  return `<div class="gate"><div class="gate-brand">${LOGO}<div><b>AltaTech DMP</b><span>數位軌跡／客戶數據中台 · 原型 ${VERSION}</span></div></div>
  <div class="gate-card"><h1>選擇示範身分</h1><p>原型以四種角色展示權限差異：分析師可看 SQL；營運主管可審批 agent 提議；營業員只能細查所屬客戶；管理員可改設定。資料皆為模擬。</p>
  ${ACCOUNTS.map(a => `<button class="gate-role" data-act="login" data-arg="${a.id}"><span class="av" style="background:${a.col}">${a.init}</span><span><span class="nm">${esc(a.name)}</span><span class="em">${esc(a.email)}</span></span><span class="rl">${esc(a.role)}</span></button>`).join('')}</div>
  <div class="gate-note">承接 GA360 埋點事件與證券交易事件；OneID 打通；事件／漏斗／留存／路徑分析；標籤分群與即時觸發；AI agent 透過語意層與工具閘道產生洞察。</div></div>`;
}
function navItem(hash, icon, label, on, badge = '') { return `<button class="nav-item${on ? ' on' : ''}" data-act="goto" data-arg="${hash}" title="${esc(label)}">${svg(icon, 18, 'ico')}<span class="lbl">${esc(label)}</span>${badge}</button>`; }
function viewSide() {
  const r = S.route; const open = INSIGHTS.filter(i => i.status === 'open').length; const pend = SEGMENTS.filter(s => s.status === 'pending_approval' && !S.approved[s.id]).length + TRIGGERS.filter(t => t.status === 'pending_approval' && !S.approved[t.id]).length;
  return `<aside class="side"><div class="side-brand">${LOGO}<b>AltaTech DMP</b></div><div class="side-scroll">
    <div class="nav-group">分析</div>
    ${navItem('#/overview', I.home, '總覽', r.name === 'overview')}
    ${navItem('#/events', I.chart, '事件分析', r.name === 'events')}
    ${navItem('#/funnel', I.funnel, '漏斗分析', r.name === 'funnel')}
    ${navItem('#/path', I.right, '行為路徑', r.name === 'path')}
    ${navItem('#/attribution', I.grid, '歸因分析', r.name === 'attribution')}
    ${navItem('#/users', I.user, '用戶列表', r.name === 'users')}
    <div class="nav-group">營運</div>
    ${navItem('#/segments', I.tag, '標籤與分群', r.name === 'segments', pend ? `<span class="badge">${pend} 待審</span>` : '')}
    ${navItem('#/triggers', I.bolt, '即時觸發', r.name === 'triggers')}
    <div class="nav-group">資料</div>
    ${navItem('#/integration', I.code, '數據整合', r.name === 'integration')}
    ${navItem('#/profile', I.search, '用戶細查 / OneID', r.name === 'profile')}
    ${navItem('#/retention', I.clock, '留存分析', r.name === 'retention', '<span class="badge">P1</span>')}
    <div class="nav-group">智能</div>
    ${navItem('#/ai', I.spark, 'AI 洞察', r.name === 'ai', `<span class="badge ai">${open}</span>`)}
    <div class="nav-group">治理</div>
    ${navItem('#/governance', I.shield, '資料治理', r.name === 'governance')}
    ${navItem('#/settings', I.cog, '平台設定', r.name === 'settings')}
  </div><div class="side-foot"><div class="side-ver">${VERSION} · build ${BUILD}</div><button class="side-collapse" data-act="collapse">${svg(S.collapsed ? I.right : I.left, 16)}<span class="lbl">收合側欄</span></button></div></aside>`;
}
function viewTop() {
  const u = S.user;
  return `<header class="topbar"><button class="org-pick" data-act="noop">AltaTech<span class="sep">/</span>證券示範專案<span class="sep">/</span>prod</button>
    <div class="search">${svg(I.spark, 15)}<input placeholder="問 AI：上週開戶到入金的轉化率？" data-act="askbar" value="" autocomplete="off"><kbd>⏎</kbd></div>
    <div class="top-right"><button class="icon-btn" data-act="goto" data-arg="#/ai/insights" title="洞察通知">${svg(I.bell, 18)}<span class="dot"></span></button>
    <button class="me" data-act="logout" title="切換身分"><span class="av" style="background:${u.col}">${u.init}</span><span><span class="nm">${esc(u.name)}</span><span class="rl">${esc(u.role)}</span></span></button></div></header>`;
}
const TITLES = { overview: '總覽', events: '事件分析', funnel: '漏斗分析', path: '行為路徑分析', attribution: '歸因分析', users: '用戶列表', integration: '數據整合', retention: '留存分析', profile: '用戶細查 / OneID', segments: '標籤與分群', triggers: '即時觸發', ai: 'AI 洞察', governance: '資料治理', settings: '平台設定' };
function viewCrumb() { const u = S.user; return `<div class="crumb"><span>證券示範專案</span><span>/</span><span class="cur">${TITLES[S.route.name] || ''}</span><span class="perm">${svg(I.shield, 12)}${u.scope === 'all' ? '全域' : '台北分公司 TPE01'}</span><div class="right"><div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-act="range" data-arg="${n}">近 ${n} 天</button>`).join('')}</div></div></div>`; }

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

function pageEvents() {
  const m = METRICS.find(x => x[0] === S.ev.metric); const series = S.ev.group === 'platform' ? splitByPlatform(m[0]) : [{ name: m[1], color: SER[0], data: seriesRange(m[0]) }];
  const rows = series[0].data.map((p, i) => ({ d: p.d, vals: series.map(s => s.data[i].v) })).reverse().slice(0, 12);
  return `<div class="page-h"><div><h1>事件分析</h1><p>指標來自語意層（metrics.yaml）；同一個查詢編譯器同時服務這個頁面與 AI agent 的 metric.query 工具。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="metric">${svg(I.code, 14)} 檢視 SQL 與血緣</button><button class="btn-primary teal" data-act="askpre" data-arg="${esc(m[1])}最近的變化有什麼值得注意？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="filters"><span class="note">指標</span><select class="sel" data-act="evmetric">${METRICS.map(x => `<option value="${x[0]}" ${x[0] === m[0] ? 'selected' : ''}>${x[1]}</option>`).join('')}</select>
    <span class="note">分組</span><select class="sel" data-act="evgroup"><option value="none" ${S.ev.group === 'none' ? 'selected' : ''}>不分組</option><option value="platform" ${S.ev.group === 'platform' ? 'selected' : ''}>平台</option></select>
    <span class="note">篩選</span><span class="pill blue">project = 證券示範</span><span class="pill">+ 條件</span><span style="margin-left:auto" class="note">掃描估算 <b class="mono">${(S.range * 0.21).toFixed(1)} GB</b> · 行級規則：${S.user.scope === 'all' ? '無' : 'branch_id IN (TPE01)'}</span></div>
  <div class="grid c2"><div class="card"><div class="card-h"><h3>${esc(m[1])}</h3><span class="sub">近 ${S.range} 天 · 每日</span></div>${lineChart('ev-main', series, { h: 300, fmt: m[2] })}</div>
  <div class="card flush"><table class="tbl"><tr><th>日期</th>${series.map(s => `<th class="num">${esc(s.name)}</th>`).join('')}</tr>${rows.map(r => `<tr><td class="mono">${r.d}</td>${r.vals.map(v => `<td class="num mono">${m[2](v)}</td>`).join('')}</tr>`).join('')}</table></div></div>
  <div class="grid c3" style="margin-top:16px"><div class="card"><div class="card-h"><h3>平台</h3><span class="sub">成交客戶</span></div>${hbar(PLATFORM_SPLIT.map(p => ({ k: p.k, v: p.users })))}</div><div class="card"><div class="card-h"><h3>委託失敗率（平台）</h3></div>${hbar(PLATFORM_SPLIT.map(p => ({ k: p.k, v: Math.round(p.reject * 10000) / 100 })), { fmt: v => v + '%', color: SER[1], total: 1e9 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '')}</div><div class="card"><div class="card-h"><h3>新戶首次來源</h3><span class="sub">first_utm_source</span></div>${hbar(UTM_SPLIT.map(u => ({ k: u.k, v: u.v })), { fmt: n0, color: SER[2] })}</div></div>`;
}

function pageFunnel() {
  const f = FUNNELS.find(x => x.id === S.funnel.id);
  const byPlat = ['iOS', 'Android', 'Web'].map((k, i) => { const w = [.46, .36, .18][i]; const adj = (f.id === 'open' && k === 'iOS') ? [1, 1, .87, .87, .8, .8] : f.steps.map(() => 1); return { k, steps: f.steps.map((s, j) => Math.round(s.n * w * adj[j])) }; });
  return `<div class="page-h"><div><h1>漏斗分析</h1><p>以 ClickHouse windowFunnel 計算；步驟可混用 GA 行為事件與交易事件。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="funnel">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="診斷「${esc(f.name)}」漏斗：哪一步流失最大、哪個分群最差、可能原因？">${svg(I.spark, 14)} 請 agent 診斷</button></div></div>
  <div class="filters"><div class="seg">${FUNNELS.map(x => `<button class="${x.id === f.id ? 'on' : ''}" data-act="funnelid" data-arg="${x.id}">${esc(x.name)}</button>`).join('')}</div><span class="pill blue">視窗 ${f.window}</span><button class="btn-ghost sm" data-act="funnelcmp">${S.funnel.compare ? '✓ ' : ''}對比前期</button><span class="pill">+ 篩選</span></div>
  <div class="grid c2"><div class="card"><div class="card-h"><h3>${esc(f.name)}</h3><span class="sub">近 ${S.range} 天 vs 前 ${S.range} 天</span></div>${funnelView(f, { compare: S.funnel.compare })}</div>
  <div class="stack"><div class="card"><div class="card-h"><h3>各平台轉化</h3><span class="sub">首步→末步</span></div><table class="tbl"><tr><th>平台</th>${f.steps.map(s => `<th class="num">${esc(s.cn)}</th>`).join('')}<th class="num">總轉化</th></tr>${byPlat.map(p => { const t = p.steps[p.steps.length - 1] / p.steps[0]; return `<tr><td style="font-weight:600">${p.k}</td>${p.steps.map(v => `<td class="num mono">${n0(v)}</td>`).join('')}<td class="num mono" style="color:${t < .03 && f.id === 'open' ? 'var(--st-serious)' : 'inherit'}">${pct(t)}</td></tr>`; }).join('')}</table></div>
  <div class="card"><div class="card-h"><h3>相關洞察</h3></div><div class="stack" style="gap:10px">${INSIGHTS.filter(i => i.kind === 'funnel' || i.kind === 'opportunity').slice(0, 2).map(i => insightCard(i, true)).join('')}</div></div></div></div>`;
}

function pageRetention() {
  return `<div class="page-h"><div><h1>留存分析</h1><p>Cohort：開戶核准週 → 之後各週是否有 order_filled。最近一週第 1 週留存偏低，與 iOS 23.4 開戶中斷洞察相關。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="retention">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="新開戶客戶的交易留存最近有什麼變化？哪個 cohort 最差？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="filters"><span class="note">初始事件</span><select class="sel"><option>account_open_approved 開戶核准</option><option>fund_deposit 入金</option><option>login 登入</option></select><span class="note">回訪事件</span><select class="sel"><option>order_filled 成交</option><option>login 登入</option><option>quote_view 看盤</option></select><div class="seg"><button class="on">週</button><button>日</button><button>月</button></div></div>
  <div class="card">${heatTable(RETENTION)}<div class="legend" style="margin-top:12px"><span class="k">色階 = 留存率（單一色相由淺到深）</span></div></div>
  <div class="grid c3" style="margin-top:16px">${[['第 1 週平均留存', '44.8%', '前 8 週 cohort'], ['第 4 週平均留存', '30.6%', ''], ['最近 cohort 第 1 週', '39.9%', '−4.9 pt vs 平均']].map(t => `<div class="tile"><div class="lbl">${t[0]}</div><div class="val">${t[1]}</div><div class="delta"><span>${t[2]}</span></div></div>`).join('')}</div>`;
}

function pageProfile() {
  const list = PROFILES.filter(canSee).filter(p => !S.profile.q || p.one_id.includes(S.profile.q) || p.cust.includes(S.profile.q));
  const p = PROFILES.find(x => x.one_id === S.profile.sel);
  const unlocked = p && S.profile.unlocked[p.one_id];
  return `<div class="page-h"><div><h1>用戶細查 / OneID</h1><p>單人行為序列與身分圖。分析倉不含姓名、手機、身分證字號；細查需填寫理由並寫入審計（agent 的 profile.* 工具走同一道門）。</p></div></div>
  <div class="grid c2" style="grid-template-columns:360px 1fr"><div class="stack"><div class="card"><div class="filters" style="margin-bottom:10px"><input class="inp" style="flex:1" placeholder="one_id 或客戶代碼" data-act="pq" value="${esc(S.profile.q)}"></div>
    ${list.length ? `<table class="tbl">${list.map(x => `<tr class="click" data-act="psel" data-arg="${x.one_id}"><td><b class="mono">${x.one_id}</b><span class="sub">${esc(x.cust)} · ${esc(x.branch)} · ${esc(x.agent)}</span></td><td class="num"><span class="pill ${x.lifecycle === '沉睡' ? 'amber' : 'teal'}">${x.lifecycle}</span></td></tr>`).join('')}</table>` : '<div class="empty">沒有符合（或不在你的客戶範圍內）</div>'}
    <div class="note" style="margin-top:10px">${S.user.scope === 'all' ? '你的範圍：全域' : '你的範圍：台北分公司 TPE01 所屬客戶'}</div></div></div>
  <div class="stack">${p ? `
    <div class="card"><div class="card-h"><h3 class="mono">${p.one_id}</h3><span class="pill ${p.lifecycle === '沉睡' ? 'amber' : 'teal'}">${p.lifecycle}</span><span class="pill">${p.fee_tier}</span><span class="pill ${p.churn > 60 ? 'red' : 'gray'}">流失風險 ${p.churn}</span><div class="right"><button class="btn-ghost sm" data-act="askpre" data-arg="客戶 ${p.one_id} 的行為摘要與建議聯繫切入點（理由：營業員跟進）">${svg(I.spark, 13)} 請 agent 摘要</button></div></div>
      <dl class="kv"><dt>客戶代碼</dt><dd class="mono">${esc(p.cust)}</dd><dt>帳戶</dt><dd>${p.accounts.map(a => `<span class="pill">${esc(a)}</span>`).join(' ')}</dd><dt>分公司 / 營業員</dt><dd>${esc(p.branch)} / ${esc(p.agent)}</dd><dt>偏好市場 / 年齡層</dt><dd>${esc(p.pref)} / ${esc(p.age)}</dd><dt>開戶日</dt><dd class="mono">${esc(p.since)}</dd></dl></div>
    <div class="card"><div class="card-h"><h3>身分圖（OneID）</h3><span class="sub">${p.identities.length} 個識別子 · 優先級 login_id › mobile › account_no › device › cookie</span></div>${idGraph(p)}</div>
    <div class="card"><div class="card-h"><h3>行為序列</h3><span class="sub">GA 行為與交易事件合併時間軸</span></div>
      ${unlocked ? `<div class="tl">${p.timeline.map(t => `<div class="tl-item ${t[2] === 'trade' ? 'trade' : t[2] === 'mkt' ? 'mkt' : t[2] === 'sys' ? 'sys' : ''}"><div class="t">${t[0]}</div><div class="e"><b>${esc(t[3])}</b> <span class="pill gray mono" style="font-size:10px">${esc(t[1])}</span><span class="p">${esc(t[4])}</span></div></div>`).join('')}</div><div class="legend"><span class="k"><i class="sw" style="background:var(--s1)"></i>GA 行為</span><span class="k"><i class="sw" style="background:var(--s2)"></i>交易</span><span class="k"><i class="sw" style="background:var(--s5)"></i>行銷</span><span class="k"><i class="sw" style="background:var(--muted)"></i>系統</span></div>`
        : `<div class="note">查看個人行為序列需填寫理由；本次查詢會以你的身份寫入審計（profile.timeline）。</div><div class="filters" style="margin-top:10px"><input class="inp wide" placeholder="查詢理由（例：客戶來電詢問 10/8 委託失敗原因）" data-act="preason" value="${esc(S.profile.reason)}"><button class="btn-primary" data-act="punlock" data-arg="${p.one_id}">解鎖並記錄審計</button></div>`}</div>`
    : '<div class="card"><div class="empty">從左側選擇一位客戶</div></div>'}</div></div>`;
}

function pageSegments() {
  const t = S.seg.tab;
  const stPill = s => { const st = S.approved[s.id] ? 'active' : s.status; return st === 'active' ? '<span class="pill green">生效</span>' : '<span class="pill amber">待審批</span>'; };
  return `<div class="page-h"><div><h1>標籤與分群</h1><p>標籤：規則／統計／SQL／模型／即時／匯入。分群以 Bitmap 交併差計算；agent 提議的分群需審批才生效。</p></div><div class="right"><button class="btn-ghost" data-act="toast" data-arg="原型：新增標籤精靈">＋ 新增標籤</button><button class="btn-primary" data-act="toast" data-arg="原型：分群規則編輯器">＋ 新增分群</button></div></div>
  <div class="filters"><div class="seg"><button class="${t === 'segments' ? 'on' : ''}" data-act="segtab" data-arg="segments">分群 ${SEGMENTS.length}</button><button class="${t === 'tags' ? 'on' : ''}" data-act="segtab" data-arg="tags">標籤 ${TAGS.length}</button></div></div>
  ${t === 'segments' ? `<div class="card flush"><table class="tbl"><tr><th>分群</th><th>規則</th><th class="num">人數</th><th>建立者</th><th>更新</th><th>狀態</th><th></th></tr>${SEGMENTS.map(s => `<tr class="click" data-act="segopen" data-arg="${s.id}"><td><b>${esc(s.name)}</b>${s.rt ? ' <span class="pill teal">即時</span>' : ''}</td><td class="mono" style="font-size:11.5px;color:var(--body)">${esc(s.rule)}</td><td class="num mono">${n0(s.n)}</td><td>${s.by.includes('-') ? `<span class="pill purple">${svg(I.spark, 11)} ${esc(s.by)}</span>` : esc(s.by)}</td><td style="color:var(--muted)">${esc(s.upd)}</td><td>${stPill(s)}</td><td class="num">${s.status === 'pending_approval' && !S.approved[s.id] ? (S.user.can.approve ? `<button class="btn-ghost sm" data-act="approve" data-arg="${s.id}">核准</button>` : '<span class="note">需主管核准</span>') : `<button class="btn-ghost sm" data-act="toast" data-arg="已推送到 MA / 營業員 App（Kafka dmp.segments）">推送</button>`}</td></tr>`).join('')}</table></div>`
  : `<div class="card flush"><table class="tbl"><tr><th>標籤</th><th>類型</th><th>值域</th><th class="num">覆蓋人數</th><th>更新</th><th>負責人</th></tr>${TAGS.map(x => `<tr><td><b>${esc(x.cn)}</b><span class="sub mono">${esc(x.id)}</span></td><td><span class="pill ${x.rt ? 'teal' : x.type === '模型' ? 'purple' : x.type === 'SQL' ? 'blue' : 'gray'}">${esc(x.type)}</span></td><td style="color:var(--body)">${x.vals.map(v => `<span class="pill" style="margin:1px">${esc(v)}</span>`).join('')}</td><td class="num mono">${n0(x.cover)}</td><td style="color:var(--muted)">${esc(x.upd)}</td><td>${esc(x.owner)}</td></tr>`).join('')}</table></div>`}`;
}
function segDrawer() {
  const s = SEGMENTS.find(x => x.id === S.segDrawer); if (!s) return '';
  const sql = `SELECT bitmapCardinality(\n  bitmapAnd(\n    (SELECT groupBitmapMergeState(users) FROM dmp.user_tags WHERE tag_id='fee_tier' AND tag_value IN ('P1','P2')),\n    (SELECT groupBitmapState(one_id_uint) FROM dmp.events\n      WHERE event='login' AND event_date >= today()-30 GROUP BY 1 HAVING count()=0)\n  ))`;
  return `<div class="drawer-bg" data-act="segclose"></div><div class="drawer"><div class="hd"><b>${esc(s.name)}</b><span class="pill">${n0(s.n)} 人</span><button class="icon-btn x" data-act="segclose">✕</button></div><div class="bd stack">
    <dl class="kv"><dt>規則</dt><dd class="mono">${esc(s.rule)}</dd><dt>建立者</dt><dd>${esc(s.by)}</dd><dt>更新</dt><dd>${esc(s.upd)}</dd><dt>狀態</dt><dd>${S.approved[s.id] ? '生效' : s.status === 'active' ? '生效' : '待審批'}</dd></dl>
    <div><div class="note" style="margin-bottom:6px"><b>群體洞察（vs 全體）</b></div>${hbar([{ k: '45–54 歲', v: 41 }, { k: '偏好 US', v: 38 }, { k: '偏好 TSE', v: 35 }, { k: '定期定額', v: 64 }, { k: 'iOS', v: 52 }], { fmt: v => v + '%', color: SER[2], total: 1e9 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '')}</div>
    <div><div class="note" style="margin-bottom:6px"><b>編譯後 SQL（Bitmap）</b></div><div class="code">${esc(sql)}</div></div>
    ${s.by.includes('-') ? `<div class="note">${svg(I.spark, 12)} 由 agent <b>${esc(s.by)}</b> 透過 segment.propose 提議；證據見 AI 洞察頁。</div>` : ''}
    <div class="acts" style="display:flex;gap:8px">${s.status === 'pending_approval' && !S.approved[s.id] && S.user.can.approve ? `<button class="btn-primary" data-act="approve" data-arg="${s.id}">核准並生效</button>` : ''}<button class="btn-ghost" data-act="askpre" data-arg="分群「${esc(s.name)}」這群人有什麼共同行為特徵？建議怎麼接觸？">${svg(I.spark, 13)} 問 agent</button></div></div></div>`;
}

function pageTriggers() {
  return `<div class="page-h"><div><h1>即時觸發</h1><p>Flink CEP 規則 → Kafka dmp.triggers；下游：MA、客服工單、營業員 App、AI Coach、Insight Agent。</p></div><div class="right"><button class="btn-primary" data-act="toast" data-arg="原型：規則 DSL 編輯器">＋ 新增規則</button></div></div>
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
    <div class="stack"><div class="card"><div class="card-h"><h3>工具閘道（MCP）</h3></div><div class="note">每個工具 = schema + 權限範圍 + 預算 + 脫敏策略 + 審計。UI 與 agent 走同一條語意層 API，口徑與權限一致。外部系統（AI Coach、Claude Code、MA）可直接以 MCP client 連線。</div><div class="code" style="margin-top:10px">gateway:\n  budgets: { per_call_scan_bytes: 20e9, per_run_scan_bytes: 100e9 }\n  policy:  { identity: on_behalf_of, masking: column_level,\n             row_level: from_dimensions, audit_event: agent.tool_called }</div></div>
    <div class="card"><div class="card-h"><h3>每個工具的回傳</h3></div><div class="code">{ data, chart_spec?, sql?, lineage[],\n  cost: { scan_bytes, ms }, warnings[] }</div><div class="note" style="margin-top:8px">agent 必須把 warnings（樣本小、streaming 缺歸因、口徑 caveats）帶進結論。</div></div></div></div>`;
  }
  return `<div class="page-h"><div><h1>AI 洞察</h1><p>agent 透過語意層與工具閘道讀資料、提洞察、提議行動；洞察是一級資料物件，可訂閱、回饋、再利用。</p></div></div>
  <div class="filters"><div class="seg">${tabs.map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="aitab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

function pageGovernance() {
  const t = S.gov.tab;
  let body = '';
  if (t === 'dictionary') body = `<div class="card flush"><table class="tbl"><tr><th>事件</th><th>主體</th><th>來源</th><th>屬性</th><th class="num">日均量</th><th>負責人</th><th>狀態</th></tr>${EVENTS.map(e => `<tr><td><b class="mono">${esc(e.name)}</b><span class="sub">${esc(e.cn)}${e.note ? ' · ' + esc(e.note) : ''}</span></td><td>${esc(e.entity)}</td><td>${esc(e.source)}</td><td style="max-width:280px">${e.props.map(p => `<span class="pill mono" style="font-size:10px;margin:1px">${esc(p)}</span>`).join('')}</td><td class="num mono">${compact(e.daily)}</td><td>${esc(e.owner)}</td><td>${({ live: '<span class="pill green">上線</span>', quarantine: '<span class="pill red">隔離</span>', draft: '<span class="pill gray">草稿</span>' })[e.status]}</td></tr>`).join('')}</table></div>`;
  else if (t === 'sources') body = `<div class="card flush"><table class="tbl"><tr><th>資料源</th><th>型態</th><th>延遲</th><th class="num">24h 事件</th><th>備註</th><th>狀態</th></tr>${SOURCES.map(s => `<tr><td><b>${esc(s.name)}</b></td><td><span class="pill">${({ batch: '批次', stream: '串流', api: 'API' })[s.kind]}</span></td><td class="mono">${esc(s.lag)}</td><td class="num mono">${n0(s.events24h)}</td><td style="color:var(--body)">${esc(s.note)}</td><td><span class="status ${s.status}"><span class="dot"></span>${s.status === 'ok' ? '正常' : '注意'}</span></td></tr>`).join('')}</table></div>
    <div class="grid c3" style="margin-top:16px"><div class="tile"><div class="lbl">GA360 對帳差異（昨日）</div><div class="val">0.3%</div><div class="delta"><span>daily vs GTM 雙寫，門檻 1%</span></div></div><div class="tile"><div class="lbl">OneID 打通率</div><div class="val">87.4%</div><div class="delta"><span>GA 事件可對應到客戶 ID 的比例</span></div></div><div class="tile"><div class="lbl">隔離事件</div><div class="val">1</div><div class="delta"><span>未登記事件種類，待登記</span></div></div></div>`;
  else body = `<div class="card flush"><table class="tbl"><tr><th>時間</th><th>主體</th><th>動作</th><th>對象</th><th>理由 / 參數</th><th class="num">掃描</th></tr>${[
    ['14:31:08', 'agent anomaly-watch（svc-insight-readonly）', 'agent.tool_called metric.query', 'order_reject_rate by platform,reason_code', 'run_8f3a…', '1.8 GB'],
    ['14:20:41', '林雅婷', 'query.events', 'traders by platform', 'UI', '6.3 GB'],
    ['14:05:12', '王信宏', 'profile.timeline', 'oid_7k3a9q', '客戶來電詢問 10/8 委託失敗原因', '12 MB'],
    ['13:58:02', 'agent ask（代表 陳柏宇）', 'segment.propose', '高價值沉睡客', 'pending_approval', '—'],
    ['13:40:19', '張敏慧', 'tag.update', 'fee_tier（SQL 標籤）', '調整分級門檻', '—'],
    ['07:04:55', 'agent churn-radar', 'insight.create', 'ins_03', 'conf 0.72', '—'],
  ].map(r => `<tr><td class="mono" style="color:var(--muted)">${r[0]}</td><td>${esc(r[1])}</td><td class="mono" style="font-size:11.5px">${esc(r[2])}</td><td>${esc(r[3])}</td><td style="color:var(--body)">${esc(r[4])}</td><td class="num mono">${esc(r[5])}</td></tr>`).join('')}</table></div>`;
  return `<div class="page-h"><div><h1>資料治理</h1><p>事件字典是語意層的根；未登記事件進隔離區；人與 agent 的每一次查詢都寫審計。</p></div></div>
  <div class="filters"><div class="seg">${[['dictionary', '事件字典'], ['sources', '資料源'], ['audit', '審計']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="govtab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

function pageSettings() {
  return `<div class="page-h"><div><h1>平台設定</h1><p>${S.user.can.admin ? '管理員可修改' : '唯讀（需管理員）'}</p></div></div>
  <div class="grid c2"><div class="stack"><div class="card"><div class="card-h"><h3>角色與權限</h3></div><table class="tbl"><tr><th>角色</th><th>範圍</th><th>細查</th><th>SQL</th><th>審批</th></tr>${ACCOUNTS.map(a => `<tr><td><b>${esc(a.role)}</b></td><td>${a.scope === 'all' ? '全域' : '所屬分公司'}</td><td>${a.can.detail ? '✓（需理由）' : '—'}</td><td>${a.can.sql ? '✓' : '—'}</td><td>${a.can.approve ? '✓' : '—'}</td></tr>`).join('')}</table></div>
  <div class="card"><div class="card-h"><h3>資料保存與個資</h3></div><dl class="kv"><dt>熱資料</dt><dd>ClickHouse 13 個月</dd><dt>冷資料</dt><dd>Iceberg on MinIO，法定年限，SSE-KMS</dd><dt>可識別欄位</dt><dd>不進分析倉；ID 服務保存 HMAC（HSM 金鑰）</dd><dt>DSAR</dt><dd>oneid.erase → 24 小時內完成並出具證明</dd></dl></div></div>
  <div class="stack"><div class="card"><div class="card-h"><h3>Agent 預算</h3></div><dl class="kv"><dt>每次工具呼叫</dt><dd>20 GB 掃描</dd><dt>每次 run</dt><dd>100 GB · 50k tokens</dd><dt>每 agent 每日</dt><dd>2 TB</dd><dt>敏感工具</dt><dd>理由必填、審計、僅所屬客戶</dd></dl></div>
  <div class="card"><div class="card-h"><h3>接入</h3></div><dl class="kv"><dt>GA360</dt><dd>日批（權威）+ Fresh + GTM Server-side 雙寫</dd><dt>交易</dt><dd>Outbox + Debezium CDC → Flink 狀態機</dd><dt>Collector</dt><dd>神策相容 /sa、/v1/events、/ga4</dd></dl></div></div></div>`;
}

/* ================================================================== v0.2：P0 模組 — 行為路徑、歸因、用戶列表、數據整合 */
function sankey(P) {
  const W = 760, H = 380, nodeW = 14, gap = 10, padT = 26;
  const cols = P.nodes.length; const colX = c => 20 + c * (W - 40 - nodeW - 120) / (cols - 1);
  const maxTotal = Math.max(...P.nodes.map(col => col.reduce((a, n) => a + n.n, 0)));
  const scale = (H - padT - 10 - gap * 5) / maxTotal;
  const pos = P.nodes.map((col, c) => { let y = padT; return col.map(n => { const h = Math.max(2, n.n * scale); const o = { x: colX(c), y, h, n, outOff: 0, inOff: 0 }; y += h + gap; return o; }); });
  const kind = k => k === 'exit' ? 'exit' : k.startsWith('order_') ? 'trade' : '';
  const links = P.links.map(([c1, i1, c2, i2, v]) => { const s = pos[c1][i1], t = pos[c2][i2]; const h = v * scale; const sy = s.y + s.outOff + h / 2, ty = t.y + t.inOff + h / 2; s.outOff += h; t.inOff += h; const x1 = s.x + nodeW, x2 = t.x; const mx = (x1 + x2) / 2;
    return `<path class="link ${kind(t.n.k)}" stroke-width="${h.toFixed(1)}" d="M${x1},${sy.toFixed(1)} C${mx},${sy.toFixed(1)} ${mx},${ty.toFixed(1)} ${x2},${ty.toFixed(1)}"><title>${esc(s.n.cn)} → ${esc(t.n.cn)}：${n0(v)} 人</title></path>`; }).join('');
  const nodes = pos.flatMap((col, c) => { let lastLy = -100; return col.map(o => { const ly = Math.max(o.y + Math.min(o.h, 14) / 2 + 4, lastLy + 27); lastLy = ly;
    return `<rect class="node ${kind(o.n.k)}" x="${o.x}" y="${o.y.toFixed(1)}" width="${nodeW}" height="${o.h.toFixed(1)}" rx="2"><title>${esc(o.n.cn)}：${n0(o.n.n)} 人</title></rect><text x="${o.x + nodeW + 6}" y="${ly.toFixed(1)}">${esc(o.n.cn)}</text><text class="n" x="${o.x + nodeW + 6}" y="${(ly + 12).toFixed(1)}">${n0(o.n.n)} · ${pct(o.n.n / P.users, 0)}</text>`; }); }).join('');
  const heads = P.nodes.map((_, c) => `<text class="col" x="${colX(c)}" y="14">${c === 0 ? '起點' : `第 ${c} 步`}</text>`).join('');
  return `<svg class="sankey" viewBox="0 0 ${W} ${H}">${heads}${links}${nodes}</svg>`;
}
function pagePath() {
  const P = PATHS;
  return `<div class="page-h"><div><h1>行為路徑分析</h1><p>起點事件之後 N 步的實際走向（Session 內）。預設抽樣 1/10；節點由事件字典的「路徑節點規則」合併（例：頁面依 page_group）。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="path">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="看盤之後最常見的流失路徑是什麼？委託失敗後的人去了哪裡？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="filters"><span class="note">起點</span><select class="sel"><option>quote_view 看盤</option><option>login 登入</option><option>push_opened 推播開啟</option><option>account_open_start 開戶開始</option></select><div class="seg"><button class="on">之後</button><button>之前</button></div><span class="note">步數</span><div class="seg">${[3, 4, 5, 6].map(n => `<button class="${S.path.steps === n ? 'on' : ''}" data-act="pathsteps" data-arg="${n}">${n}</button>`).join('')}</div><span class="pill blue">Session 內</span><span class="pill amber">抽樣 ${P.sample} · 推估 ${n0(P.users * 10)} 人</span><span class="pill">+ 分群</span></div>
  <div class="grid c2"><div class="card"><div class="card-h"><h3>看盤之後 ${S.path.steps} 步</h3><span class="sub">近 ${S.range} 天 · 寬度 = 人數</span></div>${sankey(P)}<div class="legend"><span class="k"><i class="sw rect" style="background:var(--s1)"></i>行為</span><span class="k"><i class="sw rect" style="background:var(--s2)"></i>交易</span><span class="k"><i class="sw rect" style="background:var(--axis)"></i>離開 Session</span></div></div>
  <div class="stack"><div class="card flush"><table class="tbl pathlist"><tr><th>Top 路徑</th><th class="num">人數</th><th class="num">平均耗時</th><th></th></tr>${P.top.map(r => `<tr><td><div class="p">${r.p.map((s, i) => `${i ? '<i>›</i>' : ''}<span class="pill ${s === '離開' ? 'gray' : s.includes('成交') || s.includes('委託') ? 'amber' : 'blue'}">${esc(s)}</span>`).join('')}</div></td><td class="num mono">${n0(r.n)}</td><td class="num mono">${esc(r.t)}</td><td class="num"><button class="btn-ghost sm" data-act="goto" data-arg="#/users">用戶列表</button></td></tr>`).join('')}</table></div>
  <div class="card"><div class="card-h"><h3>相關洞察</h3></div><div class="note">委託失敗（SYS02）後 68% 的人在 1 分鐘內重新委託、32% 直接離開；離開者中 P1/P2 占 19%。建議對 SYS 類失敗即時推播「已為您保留委託」訊息（tr3 已觸發客服工單）。</div><div class="acts" style="margin-top:10px;display:flex;gap:6px"><button class="btn-ghost sm" data-act="goto" data-arg="#/ai/insights">查看 ins_01</button><button class="btn-ghost sm" data-act="toast" data-arg="已提議即時規則（需審批）">提議規則</button></div></div></div></div>`;
}
function pageAttribution() {
  const A = ATTRIBUTION; const m = S.attr.model; const MODELS = [['first', '首次'], ['last', '末次'], ['linear', '線性'], ['position', '位置（U 型）'], ['decay', '時間衰減'], ['all', '模型對比']];
  const tot = A.touches.reduce((a, t) => a + t[m === 'all' ? 'position' : m], 0);
  return `<div class="page-h"><div><h1>歸因分析</h1><p>以預計算的觸點表（事件表 5–10% 體積）計算；同一次掃描內可比較多個模型。</p></div><div class="right"><button class="btn-ghost" data-act="sql" data-arg="attribution">${svg(I.code, 14)} SQL</button><button class="btn-primary teal" data-act="askpre" data-arg="首筆成交的歸因：哪個觸點在位置模型與末次模型下差異最大？為什麼？">${svg(I.spark, 14)} 請 agent 解讀</button></div></div>
  <div class="filters"><span class="note">轉化目標</span><select class="sel"><option>order_filled（首筆成交）</option><option>fund_deposit（首次入金）</option><option>account_open_approved</option></select><span class="note">回溯</span><select class="sel"><option>14 天</option><option>7 天</option><option>30 天</option></select><span class="note">模型</span><div class="seg">${MODELS.map(x => `<button class="${m === x[0] ? 'on' : ''}" data-act="attrmodel" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div><span class="pill blue">觸點 6 類</span></div>
  <div class="grid c4" style="margin-bottom:16px"><div class="tile"><div class="lbl">轉化數</div><div class="val">${n0(A.conversions)}</div></div><div class="tile"><div class="lbl">歸因價值（手續費）</div><div class="val">${compact(A.value)}<small>TWD</small></div></div><div class="tile"><div class="lbl">平均觸點數</div><div class="val">1.9</div></div><div class="tile"><div class="lbl">有觸點的轉化占比</div><div class="val">73%</div><div class="delta"><span>其餘 27% 無可歸因觸點（直接）</span></div></div></div>
  <div class="grid c2">${m === 'all' ? `<div class="card flush"><table class="tbl"><tr><th>觸點</th><th class="num">首次</th><th class="num">末次</th><th class="num">線性</th><th class="num">位置</th><th class="num">時間衰減</th><th class="num">平均觸點</th></tr>${A.touches.map(t => `<tr><td style="font-weight:600">${esc(t.k)}</td>${['first', 'last', 'linear', 'position', 'decay'].map(k => `<td class="num mono">${n0(t[k])}</td>`).join('')}<td class="num mono">${t.avgTouches}</td></tr>`).join('')}</table></div>`
    : `<div class="card"><div class="card-h"><h3>${MODELS.find(x => x[0] === m)[1]}歸因 · 轉化數</h3><span class="sub">近 ${S.range} 天</span></div>${hbar(A.touches.map(t => ({ k: t.k, v: t[m] })), { fmt: n0, color: SER[2], total: tot })}</div>`}
  <div class="stack"><div class="card"><div class="card-h"><h3>模型差異最大的觸點</h3></div>${hbar([{ k: '營業員接觸', v: 1020 }, { k: 'App 推薦卡片', v: 740 }, { k: 'LINE 推播開啟', v: 420 }], { fmt: v => '±' + n0(v), color: SER[6], total: 1e9 }).replace(/<td class="num" style="width:60px[^<]*<\/td>/g, '')}<div class="note" style="margin-top:8px">營業員接觸在「末次」模型下貢獻最高（臨門一腳），在「首次」模型下最低（很少是第一個觸點）；LINE 推播相反。建議以位置模型作為預算分配依據。</div></div>
  <div class="card"><div class="card-h"><h3>歸因 → 人群</h3></div><div class="note">點選任一觸點可進入「經該觸點轉化的客戶」用戶列表，再存為分群或比較後續留存。</div><div class="acts" style="margin-top:10px;display:flex;gap:6px"><button class="btn-ghost sm" data-act="goto" data-arg="#/users">用戶列表</button><button class="btn-ghost sm" data-act="toast" data-arg="已存為分群草稿（經 LINE 推播轉化者）">存為分群</button></div></div></div></div>`;
}
function pageUsers() {
  const u = S.users; const all = USERS.filter(x => !S.user || S.user.scope === 'all' || S.user.scope === `branch:${x.branch}`);
  const list = all.filter(x => (u.lc === 'all' || x.lifecycle === u.lc) && (u.tier === 'all' || x.tier === u.tier) && (!u.q || x.one_id.includes(u.q) || x.cust.includes(u.q)));
  const est = Math.round(list.length / Math.max(1, all.length) * (S.user.scope === 'all' ? 1412000 : 38400));
  const PS = 10; const pages = Math.max(1, Math.ceil(list.length / PS)); const page = Math.min(u.page, pages - 1); const rows = list.slice(page * PS, page * PS + PS); const nsel = Object.keys(u.sel).filter(k => u.sel[k]).length;
  return `<div class="page-h"><div><h1>用戶列表</h1><p>條件 → Bitmap 人數預估（&lt; 1 秒）→ 分頁列表（脫敏、行級權限）。任何分析落點都能進到這裡，再存為分群或推送。</p></div><div class="right"><button class="btn-ghost" data-act="toast" data-arg="已存為分群草稿">存為分群</button><button class="btn-ghost" data-act="toast" data-arg="匯出需審批：已送出申請（含浮水印）">匯出（審批）</button><button class="btn-primary" data-act="toast" data-arg="推送需審批：已送出申請">推送到渠道</button></div></div>
  <div class="filters"><input class="inp" placeholder="one_id / 客戶代碼" data-act="uq" value="${esc(u.q)}"><select class="sel" data-act="ulc"><option value="all">生命週期：全部</option>${['新客', '成長', '成熟', '沉睡', '流失'].map(v => `<option value="${v}" ${u.lc === v ? 'selected' : ''}>${v}</option>`).join('')}</select><select class="sel" data-act="utier"><option value="all">價值分級：全部</option>${['P1', 'P2', 'P3', 'P4', 'P5'].map(v => `<option value="${v}" ${u.tier === v ? 'selected' : ''}>${v}</option>`).join('')}</select><span class="pill">+ 行為條件</span><span class="pill">+ 標籤</span><span class="pill">+ 分群</span><span style="margin-left:auto" class="pill teal">預估 ${n0(est)} 人 · Bitmap 0.3 s</span>${nsel ? `<span class="pill blue">已選 ${nsel}</span><button class="btn-ghost sm" data-act="toast" data-arg="已批次打標籤（需審批）">批次打標籤</button>` : ''}</div>
  <div class="card flush"><table class="tbl"><tr><th></th><th>one_id</th><th>客戶</th><th>分公司 / 營業員</th><th>生命週期</th><th>價值</th><th>偏好</th><th>年齡層</th><th class="num">流失分</th><th class="num">90 天手續費</th><th class="num">30 天委託</th><th>最近活躍</th><th></th></tr>
  ${rows.map(x => `<tr><td><button class="chk ${u.sel[x.one_id] ? 'on' : ''}" data-act="usel" data-arg="${x.one_id}" aria-label="選取"></button></td><td class="mono">${x.one_id}</td><td class="mono">${esc(x.cust)}</td><td>${esc(x.branch)} / ${esc(x.agent)}</td><td><span class="pill ${x.lifecycle === '沉睡' || x.lifecycle === '流失' ? 'amber' : 'teal'}">${x.lifecycle}</span></td><td><span class="pill">${x.tier}</span></td><td>${x.market}</td><td>${x.age}</td><td class="num mono" style="color:${x.churn > 60 ? 'var(--st-serious)' : 'inherit'}">${x.churn}</td><td class="num mono">${n0(x.fee90)}</td><td class="num mono">${x.orders30}</td><td class="mono">${x.last}</td><td class="num"><button class="btn-ghost sm" data-act="uprofile" data-arg="${x.one_id}">細查</button></td></tr>`).join('') || '<tr><td colspan="13"><div class="empty">沒有符合條件的客戶</div></td></tr>'}</table>
  <div class="pager"><span>第 ${page + 1} / ${pages} 頁 · 本頁 ${rows.length} 筆 · 列表上限 10 萬筆</span><span class="right"><button class="btn-ghost sm" data-act="upage" data-arg="-1" ${page === 0 ? 'disabled' : ''}>上一頁</button><button class="btn-ghost sm" data-act="upage" data-arg="1" ${page >= pages - 1 ? 'disabled' : ''}>下一頁</button></span></div></div>
  <div class="note" style="margin-top:10px">欄位皆為代碼化或分桶後的值；姓名、手機、帳號明文不在分析倉。「細查」需填寫理由並寫入審計。${S.user.scope === 'all' ? '' : '你的範圍：台北分公司 TPE01。'}</div>`;
}
function pageIntegration() {
  const t = S.integ.tab; const M = MODEL_EXAMPLE; const step = S.integ.step;
  const TYPECOL = { MSSQL: '#A4262C', BigQuery: '#4285F4', PostgreSQL: '#336791', SensorsData: '#0F8F85' };
  let body = '';
  if (t === 'connections') body = `<div class="card flush"><table class="tbl"><tr><th>來源</th><th>接入方式</th><th class="num">表 / 模型</th><th>延遲</th><th class="num">24h 筆數</th><th>狀態</th><th></th></tr>${CONNECTIONS.map(c => `<tr><td style="display:flex;gap:10px;align-items:center"><span class="conn-type" style="background:${TYPECOL[c.type]}">${c.type === 'SensorsData' ? 'SA' : c.type === 'PostgreSQL' ? 'PG' : c.type === 'BigQuery' ? 'BQ' : 'MS'}</span><span><b>${esc(c.cn)}</b><span class="sub mono">${esc(c.id)} · ${esc(c.type)}</span></span></td><td>${esc(c.mode)}</td><td class="num mono">${c.tables} / ${c.models}</td><td class="mono">${esc(c.lag)}</td><td class="num mono">${n0(c.rows24h)}</td><td><span class="status ${c.status}"><span class="dot"></span>${c.status === 'ok' ? '正常' : '注意'}</span>${c.note ? `<span class="sub">${esc(c.note)}</span>` : ''}</td><td class="num"><button class="btn-ghost sm" data-act="integtab" data-arg="modeling">建模</button></td></tr>`).join('')}</table></div>
    <div class="grid c4" style="margin-top:16px">${[['MSSQL', 'JDBC 增量 / Debezium CDC', 'mssql-jdbc（MIT）、Debezium（Apache-2.0）'], ['BigQuery', 'Storage Read API（Arrow）', 'spark-bigquery-connector（Apache-2.0）'], ['PostgreSQL', 'JDBC / pgoutput CDC', 'pgjdbc（BSD-2）、Debezium'], ['SensorsData', 'Kafka 訂閱 / 查詢 API', '神策協議 1:1 映射；可反向輸出']].map(x => `<div class="tile"><div class="lbl">${x[0]}</div><div style="font-size:12.5px;margin-top:6px;font-weight:600">${x[1]}</div><div class="delta"><span>${x[2]}</span></div></div>`).join('')}</div>`;
  else if (t === 'modeling') {
    const steps = ['連線', '選表', '實體對映', '欄位映射', '清洗規則', '預覽驗證', '排程發佈'];
    const stepper = `<div class="stepper">${steps.map((s, i) => `<button class="${i + 1 === step ? 'on' : i + 1 < step ? 'done' : ''}" data-act="integstep" data-arg="${i + 1}"><b>${i + 1 < step ? '✓' : `0${i + 1}`}</b>${s}</button>`).join('')}</div>`;
    let pane = '';
    if (step <= 2) pane = `<div class="card"><dl class="kv"><dt>來源</dt><dd>${esc(M.source)}</dd><dt>接入</dt><dd>CDC（Debezium SQL Server）· 已啟用 sys.sp_cdc_enable_table</dd><dt>偵測到欄位</dt><dd>${M.src.length} 個 · 主鍵 ORD_NO · 變更欄位 STATUS</dd></dl><div class="note" style="margin-top:10px">同一張表可建立多個模型（事件、帳戶物品屬性）；與既有數倉表可「雙向映射」只存一份。</div></div>`;
    else if (step === 3) pane = `<div class="card"><div class="card-h"><h3>實體對映：狀態表 → 事件（狀態機）</h3></div><table class="tbl"><tr><th>狀態欄位 STATUS</th><th>轉移</th><th>事件</th></tr>${[['insert', '→ N', 'order_submit'], ['N', '→ P', 'order_partial_filled'], ['*', '→ F', 'order_filled'], ['*', '→ C', 'order_cancelled'], ['*', '→ R', 'order_rejected']].map(r => `<tr><td class="mono">${r[0]}</td><td class="mono">${r[1]}</td><td><span class="pill blue mono">${r[2]}</span></td></tr>`).join('')}</table><div class="note" style="margin-top:10px">主體：account（ACCT_NO 代碼化）· 人：CUST_ID → $identity_login_id（HMAC）</div></div>`;
    else if (step === 4) pane = `<div class="grid c2" style="grid-template-columns:1fr 1.4fr"><div class="card flush"><table class="tbl"><tr><th>來源欄位</th><th>型別</th><th class="num">空值率</th><th>樣本</th></tr>${M.src.map(r => `<tr><td class="mono" style="font-weight:600">${r[0]}</td><td class="mono" style="color:var(--muted)">${r[1]}</td><td class="num mono">${r[2]}</td><td class="mono" style="color:var(--body)">${esc(r[3])}</td></tr>`).join('')}</table></div><div class="card flush"><table class="tbl"><tr><th>來源</th><th></th><th>目標屬性（事件字典）</th><th>轉換</th></tr>${M.map.map(r => `<tr><td class="mono">${r[0]}</td><td class="map-arrow">→</td><td>${r[1].startsWith('（') ? `<span class="pill red">${r[1]}</span>` : `<span class="pill ${r[1].startsWith('$') ? 'purple' : 'blue'} mono">${r[1]}</span>`}</td><td class="mono" style="font-size:11px;color:var(--body)">${esc(r[2])}</td></tr>`).join('')}</table></div></div><div class="note" style="margin-top:10px">自動建議映射（名稱相似度 + 型別 + 歷史）命中 11/14；目標屬性不存在時可在此新增到事件字典（走審核）。</div>`;
    else if (step === 5) pane = `<div class="card flush"><table class="tbl"><tr><th>規則</th><th>內容</th><th>違反處理</th></tr>${M.rules.map(r => `<tr><td style="font-weight:600">${r[0]}</td><td class="mono" style="font-size:11.5px">${esc(r[1])}</td><td><span class="pill ${r[2].includes('拒絕') ? 'red' : r[2] === '隔離' ? 'amber' : 'gray'}">${r[2]}</span></td></tr>`).join('')}</table></div>`;
    else if (step === 6) pane = `<div class="grid c4"><div class="tile"><div class="lbl">樣本</div><div class="val">${n0(M.preview.sample)}</div></div><div class="tile"><div class="lbl">通過</div><div class="val" style="color:var(--st-good)">${n0(M.preview.ok)}</div></div><div class="tile"><div class="lbl">隔離</div><div class="val" style="color:var(--st-warn)">${M.preview.quarantined}</div><div class="delta"><span>${M.preview.reasons.join('；')}</span></div></div><div class="tile"><div class="lbl">個資命中</div><div class="val" style="color:var(--st-good)">${M.preview.pii}</div><div class="delta"><span>估算每日 ${M.preview.estDaily} 筆</span></div></div></div><div class="card" style="margin-top:16px"><div class="card-h"><h3>資料契約（自動產生）</h3></div><div class="code">source: trade_core.dbo.ORDER_HIST (cdc)\nentity: event  events: [order_submit, order_partial_filled, order_filled, order_cancelled, order_rejected]\nidentities: [$identity_login_id(hmac), $identity_account_no(fpe, multi)]\nowner: 交易系統   sla: latency_p95 ≤ 10s   pii: none   lineage: raw.trade_cdc → dmp.events</div></div>`;
    else pane = `<div class="card"><dl class="kv"><dt>模式</dt><dd>即時（Debezium → Kafka raw.trade_cdc → Flink 事件化）</dd><dt>監控</dt><dd>延遲、筆數、隔離率、Schema drift 告警</dd><dt>血緣</dt><dd>已登記（OpenLineage）</dd></dl><div class="acts" style="margin-top:12px;display:flex;gap:8px"><button class="btn-primary" data-act="toast" data-arg="已發佈：Flink CDC 作業建立中（K8s Operator）">發佈</button><button class="btn-ghost" data-act="toast" data-arg="已輸出 models/mdl_order_hist.yaml">匯出 YAML</button></div></div>`;
    body = `<div class="card-h" style="margin-bottom:10px"><h3>${esc(M.name)}</h3><span class="pill">${esc(M.entity)}</span><div class="right"><button class="btn-ghost sm" data-act="integstep" data-arg="${Math.max(1, step - 1)}">上一步</button><button class="btn-primary" data-act="integstep" data-arg="${Math.min(7, step + 1)}">下一步</button></div></div>${stepper}${pane}`;
  } else body = `<div class="grid c2"><div class="card flush"><table class="tbl"><tr><th>時間</th><th>來源</th><th>原因</th><th class="num">筆數</th><th></th></tr>${[['14:20', 'trade_core', 'market 值 "X" 不在枚舉', 4], ['14:05', 'fund_pg', '必填 plan_type 空值（來源新增 PLAN_FREQ）', 38], ['13:50', 'ga_bq', 'time 超出 7 天（遲到資料）', 1210], ['12:30', 'crm_pg', '個資掃描：memo 含手機號', 2]].map(r => `<tr><td class="mono" style="color:var(--muted)">${r[0]}</td><td class="mono">${r[1]}</td><td>${esc(r[2])}</td><td class="num mono">${n0(r[3])}</td><td class="num"><button class="btn-ghost sm" data-act="toast" data-arg="已重放（修正映射後）">重放</button></td></tr>`).join('')}</table></div><div class="card"><div class="card-h"><h3>隔離原則</h3></div><div class="note">不符規則的資料進隔離表而非丟棄；修正映射或規則後可重放。個資命中一律拒絕並告警，不進隔離表（不落地）。隔離率 &gt; 1% 時通知模型負責人。</div></div></div>`;
  return `<div class="page-h"><div><h1>數據整合（可視化建模）</h1><p>連線 → 選表 → 實體對映 → 欄位映射 → 清洗規則 → 預覽驗證 → 排程發佈。支援 MSSQL、BigQuery、PostgreSQL、神策；與事件模型解耦，倉表可雙向映射。</p></div><div class="right"><button class="btn-primary" data-act="toast" data-arg="原型：新增連線精靈（MSSQL / BigQuery / PostgreSQL / SensorsData / Kafka / GCS）">＋ 新增連線</button></div></div>
  <div class="filters"><div class="seg">${[['connections', '連線'], ['modeling', '建模'], ['quarantine', '清洗與隔離']].map(x => `<button class="${t === x[0] ? 'on' : ''}" data-act="integtab" data-arg="${x[0]}">${x[1]}</button>`).join('')}</div></div>${body}`;
}

function sqlDrawer() {
  const k = S.sqlDrawer; if (!k) return '';
  const SQLS = {
    events_volume: `-- metric.query: 事件量 by source（語意層編譯）\nSELECT event_date, source, countMerge(cnt) AS events\nFROM dmp.events_daily_agg\nWHERE project='sinopac_demo' AND event_date >= today()-${S.range}\nGROUP BY event_date, source ORDER BY event_date;`,
    metric: `-- metric.query: ${S.ev.metric}${S.ev.group === 'platform' ? ' group_by=platform' : ''}\nSELECT event_date${S.ev.group === 'platform' ? ', platform' : ''}, uniqExact(one_id) AS value\nFROM dmp.events\nWHERE project='sinopac_demo' AND event='order_filled'\n  AND event_date BETWEEN today()-${S.range} AND today()${S.user.scope === 'all' ? '' : "\n  AND entity_id IN (SELECT item_id FROM dmp.items WHERE item_type='account' AND props.branch_id='TPE01')  -- 行級規則"}\nGROUP BY event_date${S.ev.group === 'platform' ? ', platform' : ''} ORDER BY event_date;`,
    funnel: `-- funnel.run: ${S.funnel.id}（windowFunnel）\nSELECT level, count() AS users FROM (\n  SELECT one_id, windowFunnel(2592000)(time,\n    event='ga.page_view' AND props.page_group='account_open',\n    event='account_open_start', event='account_open_submit',\n    event='account_open_approved', event='fund_deposit', event='order_filled') AS level\n  FROM dmp.events WHERE event_date >= today()-${S.range + 30}\n  GROUP BY one_id)\nGROUP BY level ORDER BY level;`,
    path: `-- path.run: anchor=quote_view after 4 steps（Session 內，SAMPLE 1/10）\nWITH arr AS (\n  SELECT one_id, session_id, arraySort(x -> x.1, groupArray((time, event_node))) AS seq\n  FROM dmp.events SAMPLE 1/10\n  PREWHERE event_date >= today()-${S.range} AND event_node != ''\n  GROUP BY one_id, session_id)\nSELECT path, count() AS users FROM (\n  SELECT one_id, arraySlice(arrayMap(x -> x.2, seq), idx, 5) AS path\n  FROM arr ARRAY JOIN arrayEnumerate(seq) AS idx WHERE seq[idx].2 = 'quote_view')\nGROUP BY path ORDER BY users DESC LIMIT 100;`,
    attribution: `-- attribution.run: conversion=order_filled(first) window=14d models=[first,last,linear,position,decay]\nWITH conv AS (SELECT one_id_uint, min(time) t FROM dmp.events WHERE event='order_filled' AND event_date >= today()-${S.range} GROUP BY 1),\n tp AS (SELECT c.one_id_uint, arraySort(x -> x.1, groupArray((t.time, t.touch))) AS touches\n       FROM conv c JOIN dmp.touchpoints t ON t.one_id_uint = c.one_id_uint AND t.time BETWEEN c.t - 14*86400 AND c.t GROUP BY 1)\nSELECT touch,\n  sum(w_first) first, sum(w_last) last, sum(w_linear) linear, sum(w_pos) position, sum(w_decay) decay\nFROM tp ARRAY JOIN touches AS x, arrayEnumerate(touches) AS i   -- 權重於 SQL 內計算\nGROUP BY touch ORDER BY position DESC;`,
    retention: `-- retention.run: cohort=account_open_approved return=order_filled（週）\nSELECT toStartOfWeek(c.t) AS cohort, count(DISTINCT c.one_id) AS n,\n  retention(r.t >= c.t, r.t >= c.t + 7*86400, r.t >= c.t + 14*86400, ...)\nFROM (SELECT one_id, min(time) t FROM dmp.events WHERE event='account_open_approved' GROUP BY one_id) c\nLEFT JOIN (SELECT one_id, time t FROM dmp.events WHERE event='order_filled') r USING one_id\nGROUP BY cohort;`,
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
function render() {
  const root = document.getElementById('root');
  if (!S.user) { root.innerHTML = viewGate(); return; }
  const pages = { overview: pageOverview, events: pageEvents, funnel: pageFunnel, path: pagePath, attribution: pageAttribution, users: pageUsers, integration: pageIntegration, retention: pageRetention, profile: pageProfile, segments: pageSegments, triggers: pageTriggers, ai: pageAI, governance: pageGovernance, settings: pageSettings };
  const page = (pages[S.route.name] || pageOverview)();
  const active = document.activeElement; const keep = active && active.dataset && active.dataset.act; const selStart = active && active.selectionStart;
  root.innerHTML = `<div class="shell ${S.collapsed ? 'collapsed' : ''}">${viewSide()}<div class="main">${viewTop()}${viewCrumb()}<div class="content"><div class="wrap">${page}</div></div></div></div>${sqlDrawer()}${segDrawer()}${S.toast ? `<div class="toast">${esc(S.toast)}</div>` : ''}`;
  if (keep) { const el = root.querySelector(`[data-act="${keep}"]`); if (el && el.focus) { el.focus(); try { if (selStart != null) el.setSelectionRange(selStart, selStart); } catch (e) { /* non-text */ } } }
}

/* ------------------------------------------------------------------ events */
document.addEventListener('click', e => {
  const b = e.target.closest('[data-act]'); if (!b) return; const act = b.dataset.act, arg = b.dataset.arg;
  const A = {
    login: () => { S.user = ACCOUNTS.find(a => a.id === arg); S.route = parseHash(); render(); },
    logout: () => { S.user = null; S.ai.chat = []; render(); },
    goto: () => go(arg), collapse: () => { S.collapsed = !S.collapsed; render(); }, noop: () => {},
    range: () => { S.range = Number(arg); render(); },
    evmetric: () => {}, evgroup: () => {},
    funnelid: () => { S.funnel.id = arg; render(); }, funnelcmp: () => { S.funnel.compare = !S.funnel.compare; render(); },
    psel: () => { S.profile.sel = arg; render(); }, punlock: () => { if (!S.profile.reason.trim()) { toast('請先填寫查詢理由'); return; } S.profile.unlocked[arg] = S.profile.reason; S.profile.reason = ''; toast('已記錄審計：profile.timeline（' + arg + '）'); },
    segtab: () => { S.seg.tab = arg; render(); }, segopen: () => { S.segDrawer = arg; render(); }, segclose: () => { S.segDrawer = null; render(); },
    approve: () => { S.approved[arg] = true; S.segDrawer = null; toast('已核准並生效，審計已記錄'); },
    sql: () => { S.sqlDrawer = arg; render(); }, sqlclose: () => { S.sqlDrawer = null; render(); },
    aitab: () => { S.ai.tab = arg; go('#/ai/' + arg); }, aikind: () => { S.ai.kind = arg; render(); }, aistatus: () => { S.ai.status = arg; render(); },
    insopen: () => { S.ai.open[arg] = !S.ai.open[arg]; render(); },
    fb: () => { const [id, v] = arg.split(':'); S.feedback[id] = v; toast(v === 'useful' ? '已回饋：有用（進入 agent 的排序與 few-shot）' : '已回饋：有誤（reviewer 會重跑證據）'); },
    askpre: () => { S.ai.prefill = arg; S.ai.tab = 'ask'; S.segDrawer = null; S.sqlDrawer = null; go('#/ai/ask'); setTimeout(() => document.getElementById('askin')?.focus(), 60); },
    asksug: () => askSubmit(arg), asksend: () => askSubmit(document.getElementById('askin')?.value),
    chatact: () => { if (arg.includes('漏斗')) go('#/funnel'); else if (arg.includes('分群')) go('#/segments'); else if (arg.includes('細查')) go('#/profile'); else if (arg.includes('ins_01')) { S.ai.tab = 'insights'; S.ai.status = 'all'; go('#/ai/insights'); } else if (arg.includes('指標')) go('#/events'); else if (arg.startsWith('改問')) askSubmit(arg.replace('改問：', '')); else toast(arg.includes('洞察') ? '已存為洞察（insight.create, kind=answer）' : '已提議（status=pending_approval）'); },
    govtab: () => { S.gov.tab = arg; render(); },
    pathsteps: () => { S.path.steps = Number(arg); render(); },
    attrmodel: () => { S.attr.model = arg; render(); },
    usel: () => { S.users.sel[arg] = !S.users.sel[arg]; render(); },
    upage: () => { S.users.page = Math.max(0, S.users.page + Number(arg)); render(); },
    uprofile: () => { S.profile.sel = PROFILES.some(p => p.one_id === arg) ? arg : PROFILES[0].one_id; go('#/profile'); },
    integtab: () => { S.integ.tab = arg; render(); },
    integstep: () => { S.integ.tab = 'modeling'; S.integ.step = Number(arg); render(); },
    toast: () => toast(arg),
  };
  if (A[act]) A[act]();
});
document.addEventListener('change', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  if (el.dataset.act === 'evmetric') { S.ev.metric = el.value; render(); }
  if (el.dataset.act === 'evgroup') { S.ev.group = el.value; render(); }
  if (el.dataset.act === 'ulc') { S.users.lc = el.value; S.users.page = 0; render(); }
  if (el.dataset.act === 'utier') { S.users.tier = el.value; S.users.page = 0; render(); }
});
document.addEventListener('input', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  if (el.dataset.act === 'pq') { S.profile.q = el.value; render(); }
  if (el.dataset.act === 'uq') { S.users.q = el.value; S.users.page = 0; render(); }
  if (el.dataset.act === 'preason') S.profile.reason = el.value;
  if (el.dataset.act === 'askinput') S.ai.prefill = el.value;
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return; const el = e.target;
  if (el.dataset && el.dataset.act === 'askbar') { askSubmit(el.value); el.value = ''; }
  if (el.dataset && el.dataset.act === 'askinput') askSubmit(el.value);
});
document.addEventListener('pointermove', onChartMove);
document.addEventListener('pointerout', onChartLeave);
window.addEventListener('hashchange', () => { S.route = parseHash(); if (S.route.name === 'ai' && S.route.tab) S.ai.tab = S.route.tab; if (S.route.name === 'governance' && S.route.tab) S.gov.tab = S.route.tab; render(); });

S.route = parseHash(); if (S.route.name === 'ai' && S.route.tab) S.ai.tab = S.route.tab;
render();
