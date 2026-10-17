// 示範模式的 fetch：以「單機後端 + 合成黃金資料集」擷取的固定回應（fixtures.json，backend/tools/demo_capture.py）回答前端的請求。
// 規則：GET／已擷取的 POST → 固定資料（依路徑與查詢字串，動態 id 以 * 代）；未擷取的寫入 → 回合成成功（不保存）；/v1/iam/me 依選擇的角色。
// 例外：看板與書籤在瀏覽器記憶體裡真的建得出來（建看板 → 存書籤 → 加入看板 → 看板顯示卡片），重新整理即消失；卡片資料借用同類型範例卡的固定資料。
import { askRules } from './ask';
import { demoRole, setDemoRole } from './mode';

interface Fixtures { captured_at: string; roles: Record<string, unknown>; routes: Record<string, unknown> }
let loading: Promise<Fixtures> | null = null;
const load = (): Promise<Fixtures> => (loading ??= import('./fixtures.json').then((m) => m.default as unknown as Fixtures));
const LITERAL = new Set(['cards', 'data', 'versions', 'latest', 'members', 'lineage', 'preview', 'privileges', 'test', 'runs', 'rotate', 'rescan', 'run', 'status', 'decide', 'sync', 'rerun', 'timeline', 'identities', 'estimate', 'list', 'simulate', 'refresh', 'copy', 'recycle', 'pause', 'resume', 'purge', 'submit', 'compile', 'catalog', 'scope', 'rules', 'oneid', 'change-requests']);

/** 候選鍵：精確（含查詢）→ 精確 → 動態段落逐一換 * → 全換 *。 */
export function candidates(method: string, path: string, qs: string): string[] {
  const segs = path.split('/');
  const out = [`${method} ${path}${qs}`, `${method} ${path}`];
  const dyn = segs.map((_, i) => i).filter((i) => i >= 3 && segs[i] !== '' && !LITERAL.has(segs[i]));
  for (const i of dyn) { const s = [...segs]; s[i] = '*'; out.push(`${method} ${s.join('/')}${qs}`, `${method} ${s.join('/')}`); }
  if (dyn.length > 1) { const all = segs.map((s, i) => (dyn.includes(i) ? '*' : s)).join('/'); out.push(`${method} ${all}${qs}`, `${method} ${all}`); }
  return out;
}

function parseBody(b: BodyInit | null | undefined): Record<string, unknown> {
  if (typeof b !== 'string') return {};
  try { const v: unknown = JSON.parse(b); return v && typeof v === 'object' ? (v as Record<string, unknown>) : {}; } catch { return {}; }
}

export const demoFetch: typeof fetch = async (input, init) => {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  const url = new URL(raw, 'http://demo.local');
  const method = (init?.method ?? 'GET').toUpperCase();
  const fx = await load();
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  const path = url.pathname;
  if (path === '/v1/iam/me') return json(fx.roles[demoRole()] ?? fx.roles.admin);
  if (path === '/auth/ldap') { const b = parseBody(init?.body); const u = String(b.username ?? ''); if (fx.roles[u]) setDemoRole(u); return json({ ok: true, roles: [], return_to: '/' }); }
  if (path === '/auth/logout') return json({ ok: true, idp_logout: null });
  if (path === '/auth/step-up') return json({ ok: true, roles: [] });
  const local = overlay(method, path, init, fx);
  if (local) return json(local.body, local.status);
  for (const k of candidates(method, path, url.search)) if (k in fx.routes) return json(fx.routes[k]);
  if (method === 'DELETE') return json({ ok: true, demo: true });
  if (method !== 'GET') { const b = parseBody(init?.body); return json({ ...b, id: typeof b.id === 'string' ? b.id : `demo_${Math.random().toString(36).slice(2, 8)}`, status: typeof b.status === 'string' ? b.status : 'draft', demo: true, message: '示範模式：已模擬成功，變更不會保存' }, method === 'POST' ? 201 : 200); }
  return json({ detail: `示範模式沒有這筆固定資料：${method} ${path}` }, 404);
};

// ---- 記憶體覆蓋層：示範站建立的看板／書籤 ----
type Obj = Record<string, unknown>;
const mem = { dashboards: new Map<string, Obj>(), bookmarks: new Map<string, Obj>() };
const now = () => new Date().toISOString().slice(0, 19) + 'Z';
const me = (fx: Fixtures): string => String(((fx.roles[demoRole()] ?? fx.roles.admin) as { user: { id: string } }).user.id);
const nid = (p: string) => `${p}_demo${Math.random().toString(36).slice(2, 7)}`;
function sampleData(fx: Fixtures, kind: string, query?: Obj): Obj | null {
  if (kind === 'events' && query) return synthEvents(query);
  for (const [k, v] of Object.entries(fx.routes)) if (/^POST \/v1\/dashboards\/d_[^/]+\/cards\/[^/]+\/data$/.test(k) && (v as { card?: { kind?: string } }).card?.kind === kind) return v as Obj;
  return null;
}
/** 事件分析卡片在示範模式的合成資料：依查詢的指標別名與分組產生每日數列（固定種子，重新整理也一樣）。 */
function synthEvents(query: Obj): Obj {
  const metrics = (Array.isArray(query.metrics) ? query.metrics : []) as Obj[];
  const aliases = metrics.map((m, i) => String(m.alias || `m${i + 1}`));
  const time = (query.time ?? {}) as { from?: string; to?: string; grain?: string };
  const from = new Date(time.from ?? '2026-10-01'); const to = new Date(time.to ?? '2026-10-16');
  const groups = Array.isArray(query.group_by) && query.group_by.length ? ['iOS', 'Android', 'Web'] : [null];
  const rows: Obj[] = []; let seed = 7;
  const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  for (let d = new Date(from); d <= to; d.setDate(d.getDate() + 1)) {
    for (const g of groups) {
      const r: Obj = { bucket: d.toISOString().slice(0, 10), suppressed: false, _k: 120 };
      if (g) r.g1 = g;
      for (const [i, a] of aliases.entries()) r[a] = Math.round((80 + 40 * Math.sin(d.getDate() / 3 + i) + rnd() * 30) * (g === 'Web' ? 0.4 : 1));
      rows.push(r);
    }
  }
  return { rows, columns: ['bucket', ...(groups[0] ? ['g1'] : []), ...aliases, '_k', 'suppressed'], chart_spec: { type: 'line', x: 'bucket', series: aliases, group: groups[0] ? ['g1'] : [], suppressed: true }, masked: {}, sql: '-- 示範模式：合成資料，未執行 SQL', lineage: ['dmp.events'], cost: { ms: 12, rows: rows.length, layer: 'L0' }, warnings: ['示範模式：合成資料'] };
}
function overlay(method: string, path: string, init: RequestInit | undefined, fx: Fixtures): { body: unknown; status?: number } | null {
  const b = parseBody(init?.body);
  const seg = path.split('/').filter(Boolean);            // ['v1','dashboards',id,...]
  const who = me(fx);
  if (seg[0] !== 'v1') return null;
  if (seg[1] === 'analytics' && seg[2] === 'ask') {
    if (seg[3] === 'status') return { body: { allowed: true, parsers: ['rules'], default: 'rules', model: null, examples: ['上週各平台成交金額、看盤到委託到成交的漏斗、登入後 4 週留存', '近 7 天各渠道開戶申請人數', '成交之前做什麼', '推播點擊對成交的貢獻'], note: '示範模式：瀏覽器內規則解析（與後端 rules 解析器同構）；正式環境設定 LLM 金鑰後改用模型解析，草稿會再乾跑編譯。' } };
    return askOverlay(String(b.question ?? ''), null, fx);
  }
  if (seg[1] === 'dashboards' && seg[3] === 'ask' && method === 'POST') return askOverlay(String(b.question ?? ''), seg[2], fx);
  if (seg[1] === 'bookmarks') {
    const id = seg[2]; const bm = id ? mem.bookmarks.get(id) : undefined;
    const usedIn = (bid: string) => [...mem.dashboards.values()].filter((d) => (d.cards as Obj[]).some((c) => c.bookmark === bid)).map((d) => ({ dashboard: d.id, name: d.name }));
    if (method === 'GET' && !id) { const fixed = (fx.routes['GET /v1/bookmarks'] as Obj[] | undefined) ?? []; return { body: [...[...mem.bookmarks.values()].map((x) => ({ ...x, used_in: usedIn(String(x.id)) })), ...fixed] }; }
    if (method === 'POST' && !id) {
      const nb: Obj = { id: nid('bm'), name: b.name || '未命名書籤', description: b.description ?? '', kind: b.kind, query: b.query, chart: b.chart ?? 'auto', shared: b.shared ?? { roles: [], users: [] }, owner: who, created_at: now(), updated_at: now(), mine: true, editable: true, used_in: [], added_to: [] as Obj[] };
      mem.bookmarks.set(String(nb.id), nb);
      const targets = [...((b.add_to as string[] | undefined) ?? [])];
      if (typeof b.new_dashboard === 'string') { const d = newDash(String(b.new_dashboard), who); targets.push(String(d.id)); }
      for (const t of targets) { const d = mem.dashboards.get(t) ?? seedCopy(fx, t); if (d) { mem.dashboards.set(String(d.id), d); (d.cards as Obj[]).push(cardOf(nb, who, Number(b.width) === 2 ? 2 : 1)); (nb.added_to as Obj[]).push({ dashboard: d.id, name: d.name }); } }
      nb.used_in = usedIn(String(nb.id)); return { body: nb, status: 201 };
    }
    if (bm && seg[3] === 'data' && method === 'POST') { const d = sampleData(fx, String(bm.kind), bm.query as Obj); return { body: { ...(d ?? { rows: [], chart_spec: {}, sql: '', lineage: [], cost: { ms: 0, rows: 0, layer: 'L0' }, warnings: [] }), bookmark: { id: bm.id, kind: bm.kind, chart: bm.chart, name: bm.name } } }; }
    if (bm && method === 'GET') return { body: { ...bm, used_in: usedIn(id) } };
    if (bm && method === 'PUT') { Object.assign(bm, { ...b, updated_at: now() }); for (const d of mem.dashboards.values()) for (const c of d.cards as Obj[]) if (c.bookmark === id) { c.title = bm.name; c.query = bm.query; c.kind = bm.kind; } return { body: { ...bm, used_in: usedIn(id) } }; }
    if (bm && method === 'DELETE') { mem.bookmarks.delete(id); for (const d of mem.dashboards.values()) for (const c of d.cards as Obj[]) if (c.bookmark === id) c.bookmark_deleted = true; return { body: null, status: 204 }; }
    return null;
  }
  if (seg[1] === 'dashboards') {
    const id = seg[2]; const d = id ? mem.dashboards.get(id) : undefined;
    if (method === 'GET' && !id) { const fixed = (fx.routes['GET /v1/dashboards'] as Obj[] | undefined) ?? []; return { body: [...[...mem.dashboards.values()].map((x) => ({ ...x, cards: (x.cards as Obj[]).length })), ...fixed.filter((f) => !mem.dashboards.has(String(f.id)))] }; }
    if (method === 'POST' && !id) { const nd = newDash(String(b.name || '我的儀表板'), who, String((b.time as Obj | undefined)?.preset ?? '30d')); return { body: nd, status: 201 }; }
    if (!d) {
      if (id && seg[3] === 'cards' && method === 'POST' && (fx.routes[`GET /v1/dashboards/${id}`] || fx.routes['GET /v1/dashboards/*'])) { const copy = seedCopy(fx, id); if (copy) { mem.dashboards.set(id, copy); return overlay(method, path, init, fx); } }
      return null;
    }
    if (seg.length === 3 && method === 'GET') return { body: d };
    if (seg.length === 3 && method === 'PUT') {
      if (b.name) d.name = b.name; if (b.description !== undefined) d.description = b.description; if (b.shared) d.shared = b.shared; if (b.time) d.time = b.time;
      if (Array.isArray(b.cards)) { const by = new Map((d.cards as Obj[]).map((c) => [c.id, c])); d.cards = (b.cards as Obj[]).map((c) => ({ ...(by.get(c.id) ?? {}), ...Object.fromEntries(Object.entries(c).filter(([, v]) => v !== undefined)) })); }
      d.updated_at = now(); return { body: d };
    }
    if (seg.length === 3 && method === 'DELETE') { mem.dashboards.delete(id); return { body: { ok: true } }; }
    if (seg[3] === 'cards' && seg.length === 4 && method === 'POST') {
      const fixedBm = ((fx.routes['GET /v1/bookmarks'] as Obj[] | undefined) ?? []).find((x) => x.id === b.bookmark);
      const bm = b.bookmark ? (mem.bookmarks.get(String(b.bookmark)) ?? fixedBm) : undefined;
      if (b.bookmark && !bm) return { body: { detail: '書籤不存在' }, status: 404 };
      const c = bm ? cardOf(bm, who, Number(b.width) === 2 ? 2 : 1) : { id: nid('c'), title: b.title ?? '未命名卡片', kind: b.kind, query: b.query, chart: b.chart ?? 'auto', width: Number(b.width) === 2 ? 2 : 1, note: '', created_by: who, created_at: now() };
      (d.cards as Obj[]).push(c); d.updated_at = now(); return { body: c, status: 201 };
    }
    if (seg[3] === 'cards' && seg.length === 5 && method === 'DELETE') { d.cards = (d.cards as Obj[]).filter((c) => c.id !== seg[4]); return { body: { ok: true } }; }
    if (seg[3] === 'cards' && seg[5] === 'data' && method === 'POST') { const c = (d.cards as Obj[]).find((x) => x.id === seg[4]); const data = c ? sampleData(fx, String(c.kind), c.query as Obj) : null; return data && c ? { body: { ...data, card: { id: c.id, kind: c.kind, chart: c.chart, title: c.title, bookmark: c.bookmark }, warnings: c.bookmark_deleted ? ['書籤已刪除，顯示的是刪除前的查詢快照'] : (data.warnings as string[] | undefined) ?? [] } } : null; }
    return null;                                            // explain／render／schedules 等走固定資料（* 比對）
  }
  return null;
}
function newDash(name: string, who: string, preset = '30d'): Obj {
  const d: Obj = { id: nid('d'), name: name.slice(0, 60), description: '', owner: who, shared: { roles: [], users: [] }, time: { preset }, cards: [] as Obj[], created_at: now(), updated_at: now(), mine: true, editable: true };
  mem.dashboards.set(String(d.id), d); return d;
}
function seedCopy(fx: Fixtures, id: string): Obj | null {
  const src = (fx.routes[`GET /v1/dashboards/${id}`] ?? fx.routes['GET /v1/dashboards/*']) as Obj | undefined;
  return src && src.id === id ? { ...structuredClone(src), editable: true } : null;
}
function cardOf(bm: Obj, who: string, width: 1 | 2): Obj {
  return { id: nid('c'), bookmark: bm.id, title: bm.name, kind: bm.kind, query: bm.query, chart: bm.chart ?? 'auto', width, note: '', created_by: who, created_at: now() };
}

function askOverlay(question: string, dashboard: string | null, fx: Fixtures): { body: unknown; status?: number } {
  const cat = (fx.routes['GET /v1/semantic/catalog'] ?? { events: [], dimensions: [] }) as { events: { name: string; cn: string; status?: string }[]; dimensions: { name: string; cn: string }[] };
  if (!question.trim()) return { body: { detail: 'question 必填' }, status: 400 };
  const r = askRules(question, cat, { defaultTime: { from: '2026-09-17', to: '2026-10-16', grain: 'day' }, project: 'golden', dashboard });
  if (!r.cards.length) return { body: { detail: { message: '問題對不上任何分析模型或語意層事件', unmatched: r.unmatched } }, status: 422 };
  return { body: r };
}
