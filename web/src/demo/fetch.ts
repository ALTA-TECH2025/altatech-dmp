// 示範模式的 fetch：以「單機後端 + 合成黃金資料集」擷取的固定回應（fixtures.json，backend/tools/demo_capture.py）回答前端的請求。
// 規則：GET／已擷取的 POST → 固定資料（依路徑與查詢字串，動態 id 以 * 代）；未擷取的寫入 → 回合成成功（不保存）；/v1/iam/me 依選擇的角色。
// 例外：看板與書籤在瀏覽器記憶體裡真的建得出來（建看板 → 存書籤 → 加入看板 → 看板顯示卡片），重新整理即消失；卡片資料借用同類型範例卡的固定資料。
import { askRules } from './ask';
import { firstColumn, TEMPLATE_KEYS, toCsv, type TemplateKind } from '@/governance/templates';
import { demoRole, setDemoRole } from './mode';

interface Fixtures { captured_at: string; project?: string; roles: Record<string, unknown>; routes: Record<string, unknown> }
let demoProject = 'golden';                                  // 擷取時的 project（fixtures.project）；示範站新建的分群／名單沿用
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
  demoProject = fx.project ?? 'golden';
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  const path = url.pathname;
  if (path === '/v1/iam/me') return json(fx.roles[demoRole()] ?? fx.roles.admin);
  if (path === '/auth/ldap') { const b = parseBody(init?.body); const u = String(b.username ?? ''); if (fx.roles[u]) setDemoRole(u); return json({ ok: true, roles: [], return_to: '/' }); }
  if (path === '/auth/logout') return json({ ok: true, idp_logout: null });
  if (path === '/auth/step-up') return json({ ok: true, roles: [] });
  curQs = url.search;
  const local = overlay(method, path, init, fx);
  if (local) return json(local.body, local.status);
  for (const k of candidates(method, path, url.search)) if (k in fx.routes) return json(fx.routes[k]);
  if (method === 'DELETE') return json({ ok: true, demo: true });
  if (method !== 'GET') { const b = parseBody(init?.body); return json({ ...b, id: typeof b.id === 'string' ? b.id : `demo_${Math.random().toString(36).slice(2, 8)}`, status: typeof b.status === 'string' ? b.status : 'draft', demo: true, message: '示範模式：已模擬成功，變更不會保存' }, method === 'POST' ? 201 : 200); }
  return json({ detail: `示範模式沒有這筆固定資料：${method} ${path}` }, 404);
};

// ---- 記憶體覆蓋層：示範站建立的看板／書籤 ----
type Obj = Record<string, unknown>;
let curQs = '';                                            // 目前請求的 query string（覆蓋層需要 ?status=recycled 之類的篩選）
const mem = { dashboards: new Map<string, Obj>(), bookmarks: new Map<string, Obj>(), sqlSaved: new Map<string, Obj>(), sqlRuns: [] as Obj[], mids: new Map<string, Obj>(), midGone: new Set<string>(), tags: new Map<string, Obj>(), dests: new Map<string, Obj>(), pushes: new Map<string, Obj>(), dl: [] as Obj[], notify: null as Obj | null, ntf: [] as Obj[] };
const now = () => new Date().toISOString().slice(0, 19) + 'Z';
const me = (fx: Fixtures): string => String(((fx.roles[demoRole()] ?? fx.roles.admin) as { user: { id: string } }).user.id);
const nid = (p: string) => `${p}_demo${Math.random().toString(36).slice(2, 7)}`;
function sampleData(fx: Fixtures, kind: string, query?: Obj): Obj | null {
  if (kind === 'events' && query) return synthEvents(query);
  if (kind === 'sql' && query) { const r = synthSql(fx, String(query.sql ?? '')); return { rows: r.rows, columns: r.columns, chart_spec: { type: 'auto' }, masked: {}, sql: r.sql_effective, lineage: r.lineage, cost: { ms: r.ms, rows: r.row_count, layer: 'L0' }, warnings: r.warnings }; }
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
  if (seg[1] === 'meta' && seg[2] === 'templates' && seg[3] in TEMPLATE_KEYS) {                    // 示範：範本由固定資料的清單產生；上傳只預覽、套用回模擬成功
    const kind = seg[3] as TemplateKind;
    const src = { events: 'GET /v1/meta/events', properties: 'GET /v1/meta/properties', 'user-properties': 'GET /v1/meta/user-properties' }[kind];
    const items = ((fx.routes[src] as { items?: Obj[] } | undefined)?.items ?? []).map((it) => kind === 'events'
      ? { ...it, properties: ((it.properties as Obj[] | undefined) ?? []).map((p) => `${String(p.key)}:${String(p.type ?? 'string')}:${String(p.sensitivity ?? 'L1')}${p.required === false ? ':optional' : ''}`).join('; ') } : it);
    if (method === 'GET') return { body: toCsv(TEMPLATE_KEYS[kind], new URLSearchParams(curQs).get('filled') === 'true' ? items : []) };
    const have = new Set(items.map((it) => String(it.name ?? it.key)));
    const rows = firstColumn(String(b.csv ?? '')).map(({ line, key }) => ({ line, key, action: have.has(key) ? 'unchanged' : 'create', changes: [] as string[], errors: [] as string[] }));
    const applied = new URLSearchParams(curQs).get('dry_run') === 'false';
    const count = (a: string) => rows.filter((r) => r.action === a).length;
    return { body: { kind, applied, total: rows.length, summary: { create: count('create'), update: 0, change_request: 0, unchanged: count('unchanged'), error: 0 }, rows, demo: true, message: applied ? '示範模式：已模擬套用，變更不會保存' : undefined } };
  }
  if (seg[1] === 'ops' && seg[2] === 'batch-jobs' && seg[4] === 'rerun' && method === 'POST') return { body: { job: seg[3], status: 'success', demo: true }, status: 202 };   // 示範：重跑同步任務
  if (seg[1] === 'ops' && seg[2] === 'notify') {                                                    // 示範：通知設定／測試／紀錄（記憶體覆蓋層）
    const base = (mem.notify ?? (fx.routes['GET /v1/ops/notify'] as Obj | undefined)) as Obj | undefined;
    if (!seg[3] && method === 'GET' && base) return { body: base };
    if (!seg[3] && method === 'PUT' && base) { const merged = { ...base, email: { ...(base.email as Obj), ...((b.email as Obj) ?? {}) }, line: { ...(base.line as Obj), ...((b.line as Obj) ?? {}) }, policy: { ...(base.policy as Obj), ...((b.policy as Obj) ?? {}) }, updated_at: now(), updated_by: who, available: { email: true, line: Boolean(((b.line as Obj) ?? (base.line as Obj)).channel_access_token_ref) } }; mem.notify = merged; return { body: merged }; }
    if (seg[3] === 'test' && method === 'POST') { const ch = String(b.channel ?? 'email'); const rec = { id: nid('ntf'), at: now(), channel: ch, kind: 'test', alerts: ['test'], recipients: ch === 'line' ? ((base?.line as Obj)?.to as string[] | undefined)?.length ?? 1 : 2, status: 'ok', response: ch === 'line' ? '200 31ms {}' : null, text: `DMP 測試通知（${who} 送出）`, demo: true }; mem.ntf.unshift(rec); return { body: rec }; }
    if (seg[3] === 'history' && method === 'GET') return { body: [...mem.ntf, ...((fx.routes['GET /v1/ops/notify/history?limit=50'] as Obj[] | undefined) ?? [])] };
  }
  if (seg[1] === 'ops' && seg[2] === 'series' && method === 'GET') { const h = Number(new URLSearchParams(curQs).get('hours') || 6); const base = fx.routes[`GET /v1/ops/series?hours=${h}`] ?? fx.routes['GET /v1/ops/series?hours=6']; if (base) return { body: synthSeries(base as Obj, h) }; }
  if (seg[1] === 'analytics' && seg[2] === 'ask') {
    if (seg[3] === 'status') return { body: { allowed: true, parsers: ['rules'], default: 'rules', model: null, examples: ['上週各平台成交金額、看盤到委託到成交的漏斗、登入後 4 週留存', '近 7 天各渠道開戶申請人數', '成交之前做什麼', '推播點擊對成交的貢獻'], note: '示範模式：瀏覽器內規則解析（與後端 rules 解析器同構）；正式環境設定 LLM 金鑰後改用模型解析，草稿會再乾跑編譯。' } };
    return askOverlay(String(b.question ?? ''), null, fx);
  }
  if (seg[1] === 'dashboards' && seg[3] === 'ask' && method === 'POST') return askOverlay(String(b.question ?? ''), seg[2], fx);
  if (seg[1] === 'sql') return sqlOverlay(method, seg, b, fx, who);
  if (seg[1] === 'segments') { const r = segOverlay(method, seg, b, fx, who); if (r) return r; }
  if (seg[1] === 'migration' && seg[2] === 'sensors-groups') return groupsOverlay(method, seg, b, fx, who);
  if (seg[1] === 'tags' || seg[1] === 'destinations' || seg[1] === 'pushes' || seg[1] === 'audiences' || seg[1] === 'deliveries' || seg[1] === 'activation') return actOverlay(method, seg, b, fx, who);
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
// ---- 分群：示範站建立的分群（含導入名單）存在記憶體；固定資料的分群照舊 ----
const segMem = new Map<string, Obj>(); const imports = new Map<string, Obj>();
const ID_OK = { customer_code: /^[A-Za-z0-9_-]{1,40}$/, one_id: /^[A-Za-z0-9_\-:.]{1,64}$/ } as const;
function segOverlay(method: string, seg: string[], b: Obj, fx: Fixtures, who: string): { body: unknown; status?: number } | null {
  const id = seg[2]; const sub = seg[3];
  if (id === 'imports') {
    if (method === 'POST' && !sub) {
      const vals = (Array.isArray(b.values) ? b.values : []).map((v) => String(v).trim());
      const re = b.id_type === 'one_id' ? ID_OK.one_id : ID_OK.customer_code;
      if (!vals.length) return { body: { detail: 'values 不可為空' }, status: 400 };
      const good = vals.filter((v) => re.test(v)); const distinct = new Set(good).size;
      if (!distinct) return { body: { detail: '清單裡沒有格式正確的值' }, status: 400 };
      const matched = Math.round(distinct * 0.93);                                  // 示範：約 93% 對到用戶表
      const rec: Obj = { id: `im_${Math.random().toString(16).slice(2, 12).padEnd(10, '0')}`, project: demoProject, id_type: b.id_type ?? 'customer_code', label: b.label ?? '', total: vals.length, distinct, matched, unmatched: distinct - matched, invalid: vals.length - good.length, created_by: who, created_at: now() };
      imports.set(String(rec.id), rec); return { body: rec, status: 201 };
    }
    const rec = sub ? imports.get(sub) : undefined; return rec ? { body: rec } : { body: { detail: '不存在' }, status: 404 };
  }
  if (method === 'GET' && !id) { if (curQs.includes('status=recycled')) return null; return { body: listOf(fx, 'GET /v1/segments', segMem) }; }
  if (method === 'POST' && !id) return { body: createSeg(b, who), status: 201 };
  const s = id ? segMem.get(id) : undefined;
  if (!s) return null;
  if (method === 'GET' && !sub) return { body: s };
  if (method === 'GET' && sub === 'versions' && !seg[4]) return { body: [...((s.versions as Obj[]) ?? [])].reverse() };
  if (method === 'GET' && sub === 'lineage') return { body: s.lineage };
  if (method === 'PUT') { const x = { ...s, ...b, kind: s.kind, rule: s.kind === 'rule' ? b.rule ?? s.rule : s.rule, updated_at: now() }; segMem.set(id, x); return { body: x }; }
  if (method === 'POST' && sub === 'pause') { const x = { ...s, status: s.status === 'active' ? 'paused' : 'active', updated_at: now() }; segMem.set(id, x); return { body: x }; }
  if (method === 'POST' && sub === 'preview') { const u = Number(((s.latest as Obj | null)?.users as number) ?? 0); return { body: { users: u, profile: {}, sql: '-- 示範模式', warnings: ['示範模式：不產生畫像'] } }; }
  if (method === 'POST' && sub === 'compute') {
    if (s.status !== 'active') return { body: { detail: `分群未生效（${s.status}），不能計算` }, status: 409 };
    const src = ((s.rule as Obj).source ?? {}) as Obj; const imp = src.type === 'import' ? imports.get(String(src.import_id)) : undefined;
    const users = imp ? Number(imp.matched) : 80 + Math.round(Math.random() * 400);
    const v: Obj = { version: Number(((s.latest as Obj | null)?.version as number) ?? 0) + 1, basetime: now().slice(0, 10), users, status: 'success', computed_at: now(), ms: 41, trigger: 'manual', error: null };
    segMem.set(id, { ...s, latest: v, versions: [...((s.versions as Obj[]) ?? []), v].slice(-Number((s.update as Obj).keep ?? 7)), updated_at: now() }); return { body: v, status: 202 };
  }
  return null;
}
function createSeg(b: Obj, who: string, latestUsers?: number): Obj {
  const role = demoRole(); const kind = String(b.kind ?? 'rule'); const rule = (b.rule ?? {}) as Obj;
  const update = kind === 'import' ? { mode: 'manual', freq: 'daily', earliest: '02:00', keep: 1 } : { mode: 'routine', freq: 'daily', earliest: '02:00', keep: 7, ...((b.update as Obj) ?? {}) };
  const pending = role === 'marketing';
  const latest = latestUsers == null ? null : { version: 1, basetime: now().slice(0, 10), users: latestUsers, status: 'success', computed_at: now(), ms: 38, trigger: 'manual', error: null };
  const s: Obj = { id: nid('sg'), name: String(b.name ?? '未命名分群').slice(0, 60), description: b.description ?? '', kind, rule: { project: demoProject, ...rule }, project: demoProject, update, tz: 'Asia/Taipei', status: pending ? 'pending_approval' : 'active', owner: who, created_at: now(), updated_at: now(),
    approval: { status: pending ? 'pending' : 'none', by: null, at: null, note: '' }, latest, versions: latest ? [latest] : [], lineage: { up: kind === 'import' ? ['dmp.segment_imports'] : ['dmp.users', 'dmp.events'], down: [] }, recycled_at: null };
  segMem.set(String(s.id), s); return s;
}

// ---- 神策分群／標籤遷移：合成的神策清單（名稱與人數為示範值），處理狀態存在記憶體 ----
let groups: Obj[] | null = null; let scannedAt: string | null = null;
const SEED_GROUPS: [string, 'segment' | 'tag', number, number | null][] = [
  ['user_group_active_traders_30d', 'segment', 205, null], ['user_group_etf_regular_saving', 'segment', 96, null], ['user_group_dormant_90d', 'segment', 142, null],      // 與示範資料集（400 人）同量級
  ['user_group_2025_spring_campaign', 'segment', 31, null], ['user_group_ipo_subscribers', 'segment', 68, null], ['user_group_manual_vip_list', 'segment', 12, null],
  ['user_tag_risk_level', 'tag', 380, 5], ['user_tag_preferred_channel', 'tag', 352, 4], ['user_tag_aum_band', 'tag', 395, 6],
];
function groupsOverlay(method: string, seg: string[], b: Obj, fx: Fixtures, who: string): { body: unknown; status?: number } {
  const role = demoRole(); const canUse = role === 'admin' || role === 'it';
  groups ??= SEED_GROUPS.map(([name, kind, users, values]) => ({ name, kind, cn: name.split('_').slice(2).join('_'), users, values, status: 'pending', dmp: null, note: '', updated_at: '2026-10-17T09:00:00Z' }));
  const list = () => {
    const items = (scannedAt ? groups ?? [] : []).map((g) => withDmp(g, fx));
    const st = ['pending', 'snapshot', 'rebuilt', 'skipped'] as const;
    return { items, scanned_at: scannedAt, summary: { total: items.length, ...Object.fromEntries(st.map((k) => [k, items.filter((x) => x.status === k).length])) }, sensors_configured: true };
  };
  if (method === 'GET') return { body: list() };
  if (!canUse) return { body: { detail: '需 governance.use' }, status: 403 };
  if (seg[3] === 'scan') { scannedAt = now(); groups = SEED_GROUPS.map(([name, kind, users, values]) => (groups ?? []).find((g) => g.name === name) ?? ({ name, kind, cn: name.split('_').slice(2).join('_'), users, values, status: 'pending', dmp: null, note: '', updated_at: now() })); return { body: list() }; }
  const g = (groups ?? []).find((x) => x.name === decodeURIComponent(seg[3] ?? ''));
  if (!g) return { body: { detail: '不存在' }, status: 404 };
  const act = seg[4];
  if (act === 'snapshot') {
    if (role !== 'admin') return { body: { detail: '快照需 segments.use（資訊管理沒有）' }, status: 403 };
    if (g.status === 'snapshot' || g.status === 'rebuilt') return { body: { detail: `已處理（${g.status}）` }, status: 409 };
    const name = String(b.dmp_name || `神策：${g.cn}`).slice(0, 60); const users = Math.round(Number(g.users) * 0.96);
    if (g.kind === 'segment') { const s = createSeg({ name, kind: 'import', description: `神策分群 ${g.name} 的快照`, rule: { source: { type: 'import', import_id: 'im_demo' } } }, who, users); Object.assign(g, { status: 'snapshot', dmp: { type: 'segment', id: s.id, name }, updated_at: now() }); }
    else { const key = `sa_${String(g.cn).slice(0, 30)}`; mem.tags.set(key, { id: key, key, name, kind: 'import', directory: '其他', description: `神策標籤 ${g.name} 的快照`, dtype: 'string', values: [], status: 'active', owner: who, update: { mode: 'manual', freq: 'daily', earliest: '02:00', keep: 7 }, latest: { version: 1, basetime: now().slice(0, 10), users, status: 'success', computed_at: now(), ms: 52, trigger: 'manual', error: null, distribution: [] }, versions: [], lineage: { up: ['dmp.tag_imports'], down: [] }, approval: { status: 'none' }, created_at: now(), updated_at: now(), fields: {}, changes: [] });
      Object.assign(g, { status: 'snapshot', dmp: { type: 'tag', id: key, name }, updated_at: now() }); }
    return { body: withDmp(g, fx) };
  }
  if (act === 'link') { const t = String(b.type); const obj = t === 'segment' ? findIn(fx, 'GET /v1/segments', segMem, String(b.id)) : findIn(fx, 'GET /v1/tags', mem.tags, String(b.id)); if (!obj) return { body: { detail: '分群或標籤不存在' }, status: 404 }; Object.assign(g, { status: 'rebuilt', dmp: { type: t, id: obj.id, name: obj.name }, updated_at: now() }); return { body: withDmp(g, fx) }; }
  if (act === 'skip') { const note = String(b.note ?? '').trim(); if (!note) return { body: { detail: '略過需附原因（1–200 字）' }, status: 400 }; Object.assign(g, { status: 'skipped', note, updated_at: now() }); return { body: withDmp(g, fx) }; }
  return { body: { detail: '不支援' }, status: 404 };
}
function withDmp(g: Obj, fx: Fixtures): Obj {
  const d = g.dmp as Obj | null; if (!d) return { ...g };
  const obj = d.type === 'segment' ? findIn(fx, 'GET /v1/segments', segMem, String(d.id)) : findIn(fx, 'GET /v1/tags', mem.tags, String(d.id));
  const users = ((obj?.latest as Obj | null)?.users as number | undefined) ?? null; const sa = Number(g.users);
  return { ...g, dmp: { ...d, name: obj?.name ?? d.name, users, diff_pct: users != null && sa ? Math.round(((users - sa) / sa) * 10000) / 10000 : null } };
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
  const r = askRules(question, cat, { defaultTime: { from: '2026-09-17', to: '2026-10-16', grain: 'day' }, project: demoProject, dashboard });
  if (!r.cards.length) return { body: { detail: { message: '問題對不上任何分析模型或語意層事件', unmatched: r.unmatched } }, status: 422 };
  return { body: r };
}

// ---- 自定義 SQL：示範模式在瀏覽器內模擬（表目錄走固定資料；查詢結果為合成列；已存查詢／記錄／中間表存在記憶體） ----
const ts = () => Math.floor(Date.now() / 1000);
function hash(str: string): string { let h = 5381; for (const c of str) h = ((h << 5) + h + c.charCodeAt(0)) | 0; return (h >>> 0).toString(16).padStart(8, '0'); }
/** 從 SELECT 清單推欄名（AS 別名優先，否則取運算式最後一個識別字），FROM 推血緣；產生 14 列確定性資料。 */
function synthSql(fx: Fixtures, sql: string): { run_id: string; columns: string[]; rows: Obj[]; row_count: number; truncated: boolean; ms: number; scan_rows_estimate: number; sql_effective: string; lineage: string[]; warnings: string[] } {
  const fixed = (fx.routes['POST /v1/sql/query/examples'] as Obj[] | undefined) ?? [];
  const norm = (x: string) => x.replace(/\s+/g, ' ').trim().toLowerCase();
  const hit = fixed.find((f) => norm(String(f.sql ?? '')) === norm(sql));
  if (hit) { const r = hit.result as Obj; return { ...(r as ReturnType<typeof synthSql>), run_id: nid('run'), warnings: [...((r.warnings as string[]) ?? []), '示範模式：固定資料'] }; }
  const m = /select\s+(?:distinct\s+)?([\s\S]*?)\s+from\s+([\w.]+)/i.exec(sql);
  const items: string[] = []; let depth = 0, cur = '';
  for (const ch of m?.[1] ?? '*') { if (ch === '(') depth++; if (ch === ')') depth--; if (ch === ',' && depth === 0) { items.push(cur); cur = ''; } else cur += ch; }
  items.push(cur);
  const columns = items.map((it, i) => { const t = it.trim(); if (t === '*') return 'col'; const as = /\s+as\s+([\w]+)\s*$/i.exec(t); if (as) return as[1]; const id = t.match(/[A-Za-z_][\w]*/g); return id ? id[id.length - 1] : `c${i + 1}`; });
  const rows: Obj[] = []; let seed = 11; const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  const dimSets = [['iOS', 'Android', 'Web', 'Mobile Web'], ['P1', 'P2', 'P3', 'P4'], ['活躍', '成長', '成熟', '沉睡', '新客', '流失']];
  const isDate = (c: string) => /date|day|time|at$|bucket|week|month/i.test(c);
  const isNum = (c: string) => /count|cnt|users|deals|amount|sum|avg|rate|viewed|dealt|total|num|qty|aum|score|lvl|n$/i.test(c) || /\(/.test(items[columns.indexOf(c)] ?? '');
  const dimCols = columns.filter((c) => !isDate(c) && !isNum(c));
  const n = columns.some(isDate) ? 14 : dimCols.length ? Math.min(14, dimSets[0].length * (dimCols.length > 1 ? dimSets[1].length : 1)) : 1;   // 有日期欄：14 天；只有維度欄：各維度值組合各一列
  for (let i = 0; i < n; i++) {
    const r: Obj = {};
    for (const c of columns) {
      const di = dimCols.indexOf(c);
      if (isDate(c)) r[c] = new Date(Date.UTC(2026, 9, 3 + i)).toISOString().slice(0, 10);
      else if (isNum(c)) r[c] = Math.round((60 + 50 * Math.sin(i / 2 + columns.indexOf(c)) + rnd() * 40) * 10) / 10;
      else { const set = dimSets[Math.min(di, dimSets.length - 1)]; r[c] = columns.some(isDate) ? set[i % set.length] : set[di === 0 ? Math.floor(i / (dimCols.length > 1 ? dimSets[1].length : 1)) % set.length : i % set.length]; }
    }
    rows.push(r);
  }
  const table = m?.[2] ? (m[2].includes('.') ? m[2] : `dmp.${m[2]}`) : 'dmp.events';
  return { run_id: nid('run'), columns, rows, row_count: rows.length, truncated: false, ms: 18, scan_rows_estimate: 1_240_000, sql_effective: `SELECT * FROM (${sql.trim()}) AS _q LIMIT 1000 SETTINGS readonly = 1, max_execution_time = 60`, lineage: [table], warnings: ['示範模式：合成資料，未執行 SQL'] };
}
const L3_ONLY_ADMIN = new Set(['one_id', 'cust_code', 'account_no']);
const L2_ANALYST = new Set(['amount', 'filled_amount', 'fee', 'fee90', 'churn_score', 'aum']);
/** 表目錄依示範角色標記不可直接查的欄位（與 masking.yaml 同口徑：L3 只有 admin 明文；L2 數據分析／數位經營明文）。 */
function sqlTables(fx: Fixtures): Obj | null {
  const cat = fx.routes['GET /v1/sql/tables'] as { tables: { name: string; columns: { name: string; allowed: boolean; level: string }[] }[] } | undefined;
  if (!cat) return null;
  const role = demoRole(); const admin = role === 'admin' || role === 'it';
  const blocked = (c: string) => (L3_ONLY_ADMIN.has(c) && !admin) || (L2_ANALYST.has(c) && role === 'it');
  const mids = [...mem.mids.values()].filter((m) => !cat.tables.some((t) => t.name === m.name)).map((m) => ({ name: m.name, mid_table: true, note: m.note, columns: (m.columns as string[]).map((c) => ({ name: c, type: 'auto', level: 'L1', allowed: true })) }));
  return { ...cat, tables: [...cat.tables.filter((t) => !mem.midGone.has(t.name)).map((t) => ({ ...t, columns: t.columns.map((c) => ({ ...c, allowed: c.allowed && !blocked(c.name) })) })), ...mids] };
}
function sqlOverlay(method: string, seg: string[], b: Obj, fx: Fixtures, who: string): { body: unknown; status?: number } | null {
  const kind = seg[2]; const id = seg[3];
  if (demoRole() === 'marketing' || demoRole() === 'ops') return { body: { detail: '此身分沒有自定義 SQL 權限（數據分析、資訊管理、系統管理員）' }, status: 403 };
  if (kind === 'tables' && method === 'GET') { const t = sqlTables(fx); return t ? { body: t } : null; }
  if (kind === 'query' && method === 'POST' && !id) {
    const sql = String(b.sql ?? '');
    if (!sql.trim()) return { body: { detail: 'sql 必填' }, status: 400 };
    if (/^\s*(insert|alter|drop|create|truncate|delete|update|optimize|system|grant|attach|detach|rename|set|kill)\b/i.test(sql) || /;\s*\S/.test(sql)) return { body: { detail: '只允許單一 SELECT（唯讀）；不接受 DDL／DML／多語句' }, status: 400 };
    const cat = sqlTables(fx) as { tables?: { name: string; columns: { name: string; allowed: boolean }[] }[] } | null;
    const bad = (cat?.tables ?? []).flatMap((t) => t.columns.filter((c) => !c.allowed).map((c) => c.name)).find((c) => new RegExp(`\\b${c}\\b`, 'i').test(sql));
    if (bad) return { body: { detail: `SQL 涉及脫敏欄位，不可直接查詢：${bad}（可用 one_id_uint 做去重／關聯；明細請走用戶列表與揭露申請）` }, status: 400 };
    const r = synthSql(fx, sql);
    if (b.explain) return { body: { ...r, rows: [], row_count: 0, explained: true } };
    mem.sqlRuns.unshift({ id: r.run_id, user: who, sql, sql_hash: hash(sql), at: ts(), status: 'ok', rows: r.row_count, ms: r.ms, error: null, lineage: r.lineage, columns: r.columns });
    return { body: r };
  }
  if (kind === 'history' && method === 'GET') return { body: [...mem.sqlRuns, ...(((fx.routes['GET /v1/sql/history'] as Obj[] | undefined) ?? []))] };
  if (kind === 'saved') {
    const fixed = ((fx.routes['GET /v1/sql/saved'] as Obj[] | undefined) ?? []).filter((x) => !mem.sqlSaved.has(String(x.id)));
    if (method === 'GET' && !id) return { body: [...[...mem.sqlSaved.values()].filter((x) => !x._gone), ...fixed] };
    if (method === 'POST' && !id) { const x: Obj = { id: nid('sq'), name: b.name || '未命名查詢', group: b.group ?? '', description: b.description ?? '', sql: b.sql, params_default: b.params_default ?? {}, shared: b.shared ?? { roles: [], users: [] }, owner: who, created_at: ts(), updated_at: ts(), mine: true, editable: true }; mem.sqlSaved.set(String(x.id), x); return { body: x, status: 201 }; }
    const cur = id ? (mem.sqlSaved.get(id) ?? fixed.find((x) => x.id === id)) : undefined;
    if (!cur) return null;
    if (method === 'PUT') { const x = { ...cur, ...b, updated_at: ts() }; mem.sqlSaved.set(id, x); return { body: x }; }
    if (method === 'DELETE') { mem.sqlSaved.set(id, { ...cur, _gone: true }); return { body: null, status: 204 }; }
    return { body: cur };
  }
  if (kind === 'mid-tables') {
    const fixed = ((fx.routes['GET /v1/sql/mid-tables'] as Obj[] | undefined) ?? []).filter((x) => !mem.mids.has(String(x.name)) && !mem.midGone.has(String(x.name)));
    if (method === 'GET' && !id) return { body: [...mem.mids.values(), ...fixed] };
    if (method === 'POST' && !id) {
      const raw = String(b.name ?? '');
      if (!/^[a-z][a-z0-9_]{1,40}$/.test(raw)) return { body: { detail: '名稱需為小寫英數底線，2–41 字' }, status: 400 };
      const name = raw.startsWith('mid_') ? raw : `mid_${raw}`;
      if (mem.mids.has(name) || fixed.some((x) => x.name === name)) return { body: { detail: `中間表 ${name} 已存在` }, status: 409 };
      const r = synthSql(fx, String(b.sql ?? ''));
      const x: Obj = { id: nid('mt'), name, sql: b.sql, note: b.note ?? '', owner: who, created_at: ts(), rows: r.row_count, columns: r.columns, refreshed_at: ts() };
      mem.mids.set(name, x); return { body: x, status: 201 };
    }
    const cur = id ? (mem.mids.get(id) ?? fixed.find((x) => x.name === id)) : undefined;
    if (!cur) return null;
    if (seg[4] === 'refresh' && method === 'POST') { const x = { ...cur, refreshed_at: ts() }; mem.mids.set(id, x); return { body: x }; }
    if (method === 'DELETE') { mem.mids.delete(id); mem.midGone.add(id); return { body: null, status: 204 }; }
    return { body: cur };
  }
  return null;
}

// ---- 標籤／推送：固定資料 + 記憶體覆蓋（建立標籤／計算、目的地／測試、推送單／執行、交付／重放都在瀏覽器內模擬） ----
function listOf(fx: Fixtures, key: string, m: Map<string, Obj>, idKey = 'id'): Obj[] {
  const fixed = ((fx.routes[key] as Obj[] | undefined) ?? []).filter((x) => !m.has(String(x[idKey])));
  return [...[...m.values()].filter((x) => !x._gone), ...fixed];
}
function findIn(fx: Fixtures, key: string, m: Map<string, Obj>, id: string, idKey = 'id'): Obj | undefined {
  return m.get(id) ?? ((fx.routes[key] as Obj[] | undefined) ?? []).find((x) => x[idKey] === id);
}
/** 示範：快照只有擷取當下的幾筆，依每條序列的最後值合成整個視窗的走勢（確定性正弦＋抖動；stat 類保持常數）。 */
function synthSeries(base: Obj, hours: number): Obj {
  const n = Math.min(hours * 60, 288); const step = (hours * 3600) / n; const end = Date.now();
  let seed = 11; const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  const panels = ((base.panels as Obj[]) ?? []).map((p) => {
    const kind = p.kind as string; const series = ((p.series as Obj[]) ?? []).map((s, si) => {
      const pts = (s.points as Obj[]) ?? []; const last = [...pts].reverse().find((q) => q.v != null)?.v as number | undefined; const v0 = last ?? 0;
      const points = Array.from({ length: n }, (_, i) => { const t = new Date(end - (n - 1 - i) * step * 1000).toISOString().slice(0, 19) + 'Z'; if (kind === 'stat' || v0 === 0) return { t, v: v0 }; const w = 1 + 0.25 * Math.sin(i / 9 + si) + (rnd() - 0.5) * 0.12; return { t, v: Math.round(v0 * w * 1e4) / 1e4 }; });
      return { ...s, points };
    });
    return { ...p, series };
  });
  return { ...base, hours, points: n, from: new Date(end - hours * 3600 * 1000).toISOString().slice(0, 19) + 'Z', to: new Date(end).toISOString().slice(0, 19) + 'Z', panels, demo: true };
}
function synthDist(values: Obj[]): Obj[] { let seed = 3; const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; }; return values.map((v) => ({ value: v.value, users: 40 + Math.round(rnd() * 160) })); }
function actOverlay(method: string, seg: string[], b: Obj, fx: Fixtures, who: string): { body: unknown; status?: number } | null {
  const kind = seg[1]; const id = seg[2]; const sub = seg[3];
  const role = demoRole(); const approver = role === 'admin' || role === 'ops';
  if (kind === 'tags') {
    if (id === 'custom-fields' || id === 'tick') return null;
    if (method === 'GET' && !id) { const rec = curQs.includes('status=recycled'); return { body: listOf(fx, rec ? 'GET /v1/tags?status=recycled' : 'GET /v1/tags', mem.tags).filter((t) => (rec ? t.status === 'recycled' : t.status !== 'recycled')) }; }
    if (method === 'POST' && !id) {
      const key = String(b.key || `tag_${Math.random().toString(36).slice(2, 7)}`);
      if (findIn(fx, 'GET /v1/tags', mem.tags, key)) return { body: { detail: `標籤 ${key} 已存在` }, status: 409 };
      const pending = role === 'marketing';
      const t: Obj = { id: key, key, name: b.name, directory: b.directory ?? '其他', description: b.description ?? '', dtype: b.dtype ?? 'string', kind: b.kind ?? 'values', values: b.values ?? [], sql: b.sql ?? '', update: { mode: 'manual', freq: 'daily', earliest: '06:00', keep: 7, ...((b.update as Obj) ?? {}) }, fields: b.fields ?? {},
        status: pending ? 'pending_approval' : 'active', owner: who, created_at: now(), updated_at: now(), approval: { status: pending ? 'pending' : 'none', by: null, at: null, note: '' }, latest: null, versions: [], lineage: { up: ['dmp.users'], down: [] }, recycled_at: null };
      mem.tags.set(key, t); return { body: t, status: 201 };
    }
    const t = id ? findIn(fx, 'GET /v1/tags', mem.tags, id) : undefined;
    if (!t) return null;
    const put = (x: Obj) => { mem.tags.set(String(x.id), x); return { body: x }; };
    if (method === 'GET' && !sub) return { body: t };
    if (method === 'GET' && sub === 'versions') return { body: t.versions ?? [] };
    if (method === 'GET' && sub === 'distribution') { const l = t.latest as Obj | null; return l ? { body: { tag: id, version: l.version, basetime: l.basetime, users: l.users, distribution: l.distribution } } : { body: { detail: '尚無計算結果' }, status: 404 }; }
    if (method === 'PUT') return put({ ...t, ...b, updated_at: now() });
    if (sub === 'compute' && method === 'POST') {
      if (t.status !== 'active') return { body: { detail: `標籤未生效（${t.status}），不能計算` }, status: 409 };
      const dist = t.kind === 'values' ? synthDist(t.values as Obj[]) : synthDist([{ value: '1' }, { value: '2' }, { value: '3' }, { value: '5' }, { value: '8' }]);
      const v: Obj = { version: (((t.latest as Obj | null)?.version as number) ?? 0) + 1, basetime: now().slice(0, 10), users: dist.reduce((s, d) => s + (d.users as number), 0), status: 'success', computed_at: now(), ms: 37, trigger: 'manual', error: null, distribution: dist };
      put({ ...t, latest: v, versions: [...((t.versions as Obj[]) ?? []), v].slice(-Number((t.update as Obj).keep ?? 7)), updated_at: now() }); return { body: v, status: 202 };
    }
    if (sub === 'approve' && method === 'POST') { if (!approver) return { body: { detail: '需審批權限' }, status: 403 }; const ok = b.approve !== false; return put({ ...t, status: ok ? 'active' : 'draft', approval: { status: ok ? 'approved' : 'rejected', by: who, at: now(), note: b.note ?? '' }, updated_at: now() }); }
    if (sub === 'submit' && method === 'POST') return put({ ...t, status: 'pending_approval', approval: { status: 'pending', by: null, at: null, note: '' } });
    if (sub === 'pause' && method === 'POST') { const down = (t.lineage as Obj).down as string[]; return { body: { ...put({ ...t, status: t.status === 'active' ? 'paused' : 'active' }).body as Obj, warnings: down?.length && t.status === 'active' ? [`有 ${down.length} 個下游（推送單／目的地）引用此標籤`] : [] } }; }
    if (sub === 'recycle' && method === 'POST') { if (((t.lineage as Obj).down as string[])?.length) return { body: { detail: '有下游引用，先解除再移入回收站' }, status: 409 }; return put({ ...t, status: 'recycled', recycled_at: now() }); }
    if (sub === 'restore' && method === 'POST') return put({ ...t, status: 'active', recycled_at: null });
    return null;
  }
  if (kind === 'destinations') {
    if (method === 'GET' && !id) return { body: listOf(fx, 'GET /v1/destinations', mem.dests) };
    if (method === 'POST' && !id) {
      if (!approver && role !== 'it') return { body: { detail: '目的地需數位經營（segments.approve）或資訊管理（integration.admin）' }, status: 403 };
      if (b.secret || b.token || b.password) return { body: { detail: '欄位 secret 疑似明文密鑰，請改用 secret_ref' }, status: 400 };
      if (!/^kv\/data\/dmp\/[a-z]+\/[a-z0-9_]+#[a-z_]+$/.test(String(b.secret_ref ?? ''))) return { body: { detail: 'secret_ref 格式不合法（只接受密鑰庫參照，不接受明文）' }, status: 400 };
      if (!/^https:\/\//.test(String(b.url ?? ''))) return { body: { detail: 'url 須為 https://' }, status: 400 };
      const d: Obj = { id: nid('dst'), name: b.name, type: 'webhook', url: b.url, secret_ref: b.secret_ref, secret: '••••••••', identifier: b.identifier ?? 'customer_code', allowed_tags: b.allowed_tags ?? [], masking_level: 'L2', ip_allowlist: [], mtls: false, note: b.note ?? '', status: 'active', owner: who, created_at: now(), updated_at: now(), first_push_at: null, consecutive_failures: 0, rotated_at: null, stats: { ok_7d: null, deliveries_7d: 0, p95_ms: null, last_delivery_at: null } };
      mem.dests.set(String(d.id), d); return { body: d, status: 201 };
    }
    const d = id ? findIn(fx, 'GET /v1/destinations', mem.dests, id) : undefined;
    if (!d) return null;
    const put = (x: Obj) => { mem.dests.set(String(x.id), x); return { body: x }; };
    if (method === 'GET') return { body: d };
    if (method === 'PUT') return put({ ...d, ...b, updated_at: now() });
    if (sub === 'pause') return put({ ...d, status: d.status === 'active' ? 'paused' : 'active', consecutive_failures: 0 });
    if (sub === 'rotate-secret') return put({ ...d, secret_ref: b.secret_ref, rotated_at: now() });
    if (sub === 'test') { const ok = !String(d.url).includes('ma.internal'); const dlv: Obj = { id: nid('dlv'), push_id: null, segment_id: 'audience_test', destination_id: d.id, version: 0, mode: 'test', event: 'audience.test', batch: { index: 1, total: 1, size: 3 }, members: 3, status: ok ? 'ok' : 'retrying', attempts: 1, next_retry_at: null, response: ok ? { status: 200, ms: 180, body: '{"ok":true}' } : { status: null, ms: 10004, body: 'ConnectTimeout: ma.internal' }, created_at: now(), updated_at: now(), replay_of: null }; mem.dl.unshift(dlv); return { body: { delivery: dlv.id, status: dlv.status, response: dlv.response } }; }
    return null;
  }
  if (kind === 'audiences' && sub === 'push' && method === 'POST') {
    const segs = (fx.routes['GET /v1/segments'] as Obj[] | undefined) ?? []; const sg = segs.find((s) => s.id === id);
    if (!sg) return { body: { detail: `找不到：分群 ${id}` }, status: 404 };
    const ids = (b.destination_ids as string[] | undefined) ?? []; const dests = ids.map((x) => findIn(fx, 'GET /v1/destinations', mem.dests, x)).filter(Boolean) as Obj[];
    if (!dests.length) return { body: { detail: 'destination_ids 至少一個' }, status: 400 };
    if (b.mode === 'realtime') return { body: { detail: '即時推送（Flink 規則／即時標籤）排第二階段；請先用增量 + 日批後排程' }, status: 400 };
    const tags = (b.include_tags as string[] | undefined) ?? [];
    for (const d of dests) { const bad = tags.filter((t) => !(d.allowed_tags as string[]).includes(t)); if (bad.length) return { body: { detail: `目的地 ${d.name} 不允許附帶標籤：${bad.join('、')}` }, status: 400 }; }
    const first = dests.filter((d) => !d.first_push_at);
    const needs = b.mode === 'full' || (first.length > 0 && !approver);
    const p: Obj = { id: nid('push'), segment_id: id, segment_name: sg.name, destination_ids: ids, mode: b.mode ?? 'delta', schedule: b.schedule ?? 'after_batch', cron: b.cron ?? '', include_tags: tags, status: needs ? 'pending_approval' : 'active', approval_id: needs ? nid('ap') : null, owner: who, created_at: now(), updated_at: now(), last_delivered_version: null, last_run_at: null, last_result: null, runs: 0 };
    mem.pushes.set(String(p.id), p);
    if (p.status === 'active' && p.schedule === 'now') runPush(p, fx);
    return { body: p, status: 202 };
  }
  if (kind === 'pushes') {
    if (method === 'GET' && !id) return { body: listOf(fx, 'GET /v1/pushes', mem.pushes).filter((p) => p.status !== 'deleted') };
    const p = id ? findIn(fx, 'GET /v1/pushes', mem.pushes, id) : undefined;
    if (!p) return null;
    if (method === 'GET') { const fixed = fx.routes[`GET /v1/pushes/${id}`] as Obj | undefined; return { body: { ...(fixed ?? {}), ...p, deliveries: [...mem.dl.filter((d) => d.push_id === id), ...(((fixed?.deliveries as Obj[]) ?? []))] } }; }
    if (sub === 'run' && method === 'POST') { if (p.status !== 'active') return { body: { detail: `推送單未生效（${p.status}）` }, status: 409 }; return { body: runPush(p, fx), status: 202 }; }
    if (sub === 'pause' && method === 'POST') { const x = { ...p, status: p.status === 'active' ? 'paused' : 'active', updated_at: now() }; mem.pushes.set(id, x); return { body: x }; }
    if (method === 'DELETE') { mem.pushes.set(id, { ...p, status: 'deleted', _gone: true }); return { body: null, status: 204 }; }
    return null;
  }
  if (kind === 'deliveries') {
    if (method === 'GET' && !id) { const fixed = (fx.routes['GET /v1/deliveries'] as Obj[] | undefined) ?? []; const ids = new Set(mem.dl.map((d) => d.id)); return { body: [...mem.dl, ...fixed.filter((d) => !ids.has(d.id))] }; }
    if (sub === 'replay' && method === 'POST') {
      const cur = mem.dl.find((d) => d.id === id) ?? ((fx.routes['GET /v1/deliveries'] as Obj[] | undefined) ?? []).find((d) => d.id === id);
      if (!cur) return null;
      if (cur.status === 'ok') return { body: { detail: '交付已成功，不需重放' }, status: 409 };
      const x = { ...cur, status: 'ok', attempts: Number(cur.attempts) + 1, next_retry_at: null, response: { status: 200, ms: 210, body: '{"ok":true}' }, replay_of: cur.replay_of ?? id, updated_at: now() };
      const i = mem.dl.findIndex((d) => d.id === id); if (i >= 0) mem.dl[i] = x; else mem.dl.unshift(x);
      return { body: x, status: 202 };
    }
    return null;
  }
  if (kind === 'activation' && id === 'tick') return { body: { retried: [], ran: [] } };
  return null;
}
/** 示範模式執行推送單：分群人數當作名單，產生一批（或全量 + complete）交付；MA 目的地固定失敗以示範重試。 */
function runPush(p: Obj, fx: Fixtures): Obj {
  const segs = (fx.routes['GET /v1/segments'] as Obj[] | undefined) ?? []; const sg = segs.find((s) => s.id === p.segment_id);
  const users = Number(((sg?.latest as Obj | undefined)?.users as number) ?? 120); const ver = Number(((sg?.latest as Obj | undefined)?.version as number) ?? 1);
  const first = p.last_delivered_version == null; const delta = p.mode === 'delta' && !first;
  const added = delta ? Math.max(1, Math.round(users * 0.04)) : users; const removed = delta ? Math.max(0, Math.round(users * 0.02)) : 0;
  const result: Obj = { version: ver, added, removed, batches: 0, ok: 0, failed: 0, destinations: {} as Obj };
  for (const did of p.destination_ids as string[]) {
    const d = findIn(fx, 'GET /v1/destinations', mem.dests, did); if (!d) continue;
    const fail = String(d.url).includes('ma.internal'); const n = Math.max(1, Math.ceil((added + removed) / 5000)); const events = [...Array(n).keys()].map((i) => ({ ev: delta ? 'audience.delta' : 'audience.full', i: i + 1, t: n + (delta ? 0 : 1), size: Math.min(5000, added + removed - i * 5000) }));
    if (!delta) events.push({ ev: 'audience.complete', i: n + 1, t: n + 1, size: 0 });
    for (const e of events) { mem.dl.unshift({ id: nid('dlv'), push_id: p.id, segment_id: p.segment_id, destination_id: did, version: ver, mode: delta ? 'delta' : 'full', event: e.ev, batch: { index: e.i, total: e.t, size: e.size }, members: e.size, status: fail ? 'retrying' : 'ok', attempts: 1, next_retry_at: fail ? new Date(Date.now() + 60000).toISOString().slice(0, 19) : null, response: fail ? { status: 503, ms: 12, body: 'upstream busy' } : { status: 200, ms: 180 + e.i * 7, body: '{"ok":true}' }, created_at: now(), updated_at: now(), replay_of: null }); (result.batches as number)++; if (fail) (result.failed as number)++; else (result.ok as number)++; }
    (result.destinations as Obj)[did] = fail ? 'partial' : 'ok';
    mem.dests.set(did, { ...d, first_push_at: d.first_push_at ?? now(), consecutive_failures: fail ? Number(d.consecutive_failures ?? 0) + events.length : 0 });
  }
  mem.pushes.set(String(p.id), { ...p, last_delivered_version: ver, last_run_at: now(), last_result: result, runs: Number(p.runs ?? 0) + 1, updated_at: now() });
  return result;
}
