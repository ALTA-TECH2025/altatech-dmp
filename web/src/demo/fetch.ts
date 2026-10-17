// 示範模式的 fetch：以「單機後端 + 合成黃金資料集」擷取的固定回應（fixtures.json，backend/tools/demo_capture.py）回答前端的請求。
// 規則：GET／已擷取的 POST → 固定資料（依路徑與查詢字串，動態 id 以 * 代）；未擷取的寫入 → 回合成成功（不保存）；/v1/iam/me 依選擇的角色。
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
  for (const k of candidates(method, path, url.search)) if (k in fx.routes) return json(fx.routes[k]);
  if (method === 'DELETE') return json({ ok: true, demo: true });
  if (method !== 'GET') { const b = parseBody(init?.body); return json({ ...b, id: typeof b.id === 'string' ? b.id : `demo_${Math.random().toString(36).slice(2, 8)}`, status: typeof b.status === 'string' ? b.status : 'draft', demo: true, message: '示範模式：已模擬成功，變更不會保存' }, method === 'POST' ? 201 : 200); }
  return json({ detail: `示範模式沒有這筆固定資料：${method} ${path}` }, 404);
};
