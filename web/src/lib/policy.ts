// 前端的權限輔助：只拿 /v1/iam/me 的 grants／masking 決定「畫不畫、能不能按」；最終裁決永遠是後端 authorize()。
// 規則與 backend/security/policy.py 一致：多角色功能取聯集、欄位遮罩取最嚴格、use 蘊含 view、export 蘊含 use。
import type { Action, Me, Module } from '@/api/types';

const IMPLIES: Record<Action, Action[]> = { view: [], use: ['view'], export: ['use', 'view'], approve: ['view'], admin: ['view'] };

export function can(me: Me | null | undefined, module: Module, action: Action): boolean {
  if (!me) return false;
  const acts = me.grants[module];
  if (!acts) return false;
  if (acts.includes(action)) return true;
  return acts.some((a) => IMPLIES[a].includes(action));
}

export function visibleModules(me: Me | null | undefined): Module[] {
  if (!me) return [];
  return (Object.keys(me.grants) as Module[]).filter((m) => can(me, m, 'view') || can(me, m, 'use'));
}

export type MaskPolicy = { kind: 'plain' | 'bucket' | 'round' | 'mask_tail' | 'mask_all' | 'hash' | 'hide'; arg?: number[] | number };
export function parsePolicy(p: string): MaskPolicy {
  const m = /^(\w+)(?:\((.*)\))?$/.exec(p.trim());
  if (!m) return { kind: 'hide' };
  const name = m[1] as MaskPolicy['kind'];
  if (name === 'bucket') return { kind: 'bucket', arg: (m[2] ?? '').match(/[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?/g)?.map(Number) ?? [0, 1e5, 5e5, 1e6, 5e6] };
  if (name === 'round' || name === 'mask_tail') return { kind: name, arg: m[2] ? Number(m[2]) : name === 'round' ? 1000 : 4 };
  return { kind: name };
}

/** 依 me.masking 把一個欄位值渲染成顯示字串（後端已遮罩的值直接顯示；這裡只處理後端回明文但 UI 需要再提示的情況，例如匯出預覽）。 */
export function display(me: Me, field: string, value: unknown): string {
  const pol = me.masking[field]?.policy ?? 'plain';
  const p = parsePolicy(pol);
  if (value === null || value === undefined) return '—';
  const s = String(value);
  switch (p.kind) {
    case 'plain': return s;
    case 'mask_tail': { const n = p.arg as number; return s.slice(0, Math.max(s.length - n, 0)) + '*'.repeat(Math.min(s.length, n)); }
    case 'mask_all': return '***';
    case 'hide': return '（不可見）';
    case 'round': { const n = p.arg as number; return String(Math.round(Number(value) / n) * n); }
    case 'bucket': {
      const v = Number(value); const e = p.arg as number[];
      if (v < e[0]) return `<${fmt(e[0])}`;
      for (let i = 0; i < e.length - 1; i++) if (v < e[i + 1]) return `${fmt(e[i])}–${fmt(e[i + 1])}`;
      return `≥${fmt(e[e.length - 1])}`;
    }
    default: return s;
  }
}
const fmt = (x: number) => (Number.isInteger(x) ? String(x) : String(x));

/** 兩個角色矩陣的聯集（權限中心「使用者指派」預覽多角色結果用）。 */
export function unionGrants(list: Partial<Record<Module, Action[]>>[]): Partial<Record<Module, Action[]>> {
  const out: Partial<Record<Module, Action[]>> = {};
  for (const g of list) for (const [m, acts] of Object.entries(g) as [Module, Action[]][]) {
    const cur = new Set(out[m] ?? []); acts.forEach((a) => cur.add(a));
    out[m] = (['view', 'use', 'export', 'approve', 'admin'] as Action[]).filter((a) => cur.has(a));
  }
  return out;
}

/** 勾選矩陣的蘊含規則：勾 use 自動勾 view、勾 export 自動勾 use/view；取消 view 則整列取消。 */
export function toggleAction(acts: Action[], a: Action, on: boolean): Action[] {
  const s = new Set(acts);
  if (on) { s.add(a); IMPLIES[a].forEach((x) => s.add(x)); }
  else { s.delete(a); if (a === 'view') s.clear(); if (a === 'use') s.delete('export'); }
  return (['view', 'use', 'export', 'approve', 'admin'] as Action[]).filter((x) => s.has(x));
}
