// 用戶列表／分群規則（UsersQuery）的純函式：欄位目錄、條件值轉型、規則摘要、有效性。無 DOM，有單元測試。
import type { Catalog, Cond } from '@/analytics/types';
import type { UsersBehavior, UsersQuery, UsersSource } from './types';

export type FieldType = 'string' | 'number' | 'bool' | 'date';
export interface FieldOpt { v: string; cn: string; type: FieldType; values?: string[] }

const T: Record<string, FieldType> = { enum: 'string', string: 'string', number: 'number', bool: 'bool', date: 'date', datetime: 'date' };
export const COUNT_OPS: [UsersBehavior['count'] extends infer C ? (C extends { op: infer O } ? O : never) : never, string][] = [['gte', '≥'], ['lte', '≤'], ['eq', '='], ['between', '介於']];
export const OP_CN: Record<string, string> = { eq: '等於', neq: '不等於', in: '包含於', nin: '不包含於', like: '包含文字', nlike: '不包含文字', set: '有值', nset: '沒有值', gt: '>', gte: '≥', lt: '<', lte: '≤', between: '介於', true: '是', false: '否', rel: '最近 N 天內' };
export const NO_VALUE_OPS = new Set(['set', 'nset', 'true', 'false']);

/** 用戶條件可選欄位：寬表用戶屬性（user.<key>）+ 分群（seg.<id>）。 */
export function userFields(cat: Catalog | null, segments: { id: string; name: string }[] = []): FieldOpt[] {
  const props = (cat?.user_props ?? []).filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: u.cn, type: T[u.type] ?? 'string', values: u.values?.length ? u.values : undefined }));
  return [...props, ...segments.map((s) => ({ v: `seg.${s.id}`, cn: `分群：${s.name}`, type: 'string' as FieldType }))];
}

/** 事件屬性條件可選欄位（做過／未做過裡的「且屬性…」）。 */
export function eventFields(cat: Catalog | null): FieldOpt[] {
  const seen = new Set<string>(); const out: FieldOpt[] = [];
  for (const f of [...(cat?.event_columns ?? []).map((c) => ({ v: c.name, cn: c.cn, type: (c.type ?? 'string') as FieldType })), ...(cat?.dimensions ?? []).map((d) => ({ v: d.name, cn: d.cn, type: (d.type as FieldType) ?? 'string', values: d.values?.length ? d.values : undefined }))]) {
    if (!seen.has(f.v)) { seen.add(f.v); out.push(f); }
  }
  return out;
}

export function opsFor(cat: Catalog | null, f: FieldOpt | undefined): string[] {
  if (!f) return ['eq'];
  if (f.v.startsWith('seg.')) return ['in', 'nin'];
  return cat?.ops[f.type] ?? ['eq'];
}

/** 輸入字串 → 條件值：數字轉型；in／nin／between 逗號分隔；rel → {days}。 */
export function condValue(type: FieldType, op: string, raw: string): unknown {
  if (op === 'rel') return { days: Number(raw) || 0 };
  if (['in', 'nin', 'between'].includes(op)) return raw.split(',').map((s) => s.trim()).filter(Boolean).map((s) => (type === 'number' ? Number(s) : s));
  return type === 'number' ? Number(raw) : raw;
}
export function rawValue(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) return v.join(',');
  if (typeof v === 'object') return String((v as { days?: number }).days ?? '');
  return String(v);
}

export function emptyRule(project?: string): UsersQuery { return { project, filters: [], behaviors: [], behaviors_op: 'and' }; }
export function newBehavior(from: string, to: string): UsersBehavior { return { event: '', done: true, count: { op: 'gte', value: 1 }, time: { from, to } }; }

/** 規則有效：至少一個條件；行為要有事件與期間；條件要有值（除了有值／沒有值／是／否）。 */
export function ruleValid(q: UsersQuery): boolean {
  const conds = q.filters ?? []; const bs = q.behaviors ?? [];
  if (!conds.length && !bs.length && !q.source && !q.segment) return false;
  if (conds.some((c) => !c.field || (!NO_VALUE_OPS.has(c.op) && !c.field.startsWith('seg.') && (c.value === '' || c.value === undefined || (Array.isArray(c.value) && !c.value.length))))) return false;
  return bs.every((b) => b.event && b.time?.from && b.time?.to);
}

function condText(c: Cond, fields: FieldOpt[]): string {
  const f = fields.find((x) => x.v === c.field); const name = f?.cn ?? c.field;
  if (c.field.startsWith('seg.')) return `${c.op === 'nin' ? '不屬於' : '屬於'}${name}`;
  if (NO_VALUE_OPS.has(c.op)) return `${name} ${OP_CN[c.op] ?? c.op}`;
  if (c.op === 'rel') return `${name} 最近 ${(c.value as { days: number })?.days ?? '?'} 天內`;
  return `${name} ${OP_CN[c.op] ?? c.op} ${rawValue(c.value)}`;
}

export function sourceText(s: UsersSource): string {
  if (s.label) return s.label;
  if (s.type === 'funnel') return `漏斗第 ${s.step} 步${s.reached === false ? '流失' : '到達'}${s.bucket ? `（${s.bucket}）` : ''}${s.group != null ? ` · ${String(s.group)}` : ''}`;
  if (s.type === 'retention') return `留存 ${s.cohort} 同期群第 ${s.period} 期${s.retained === false ? '未留存' : s.retained === null ? '全部' : '留存'}${s.group != null ? ` · ${String(s.group)}` : ''}`;
  if (s.type === 'path') return `路徑第 ${s.level} 步 ${s.node}`;
  if (s.type === 'attribution') return `歸因觸點 ${s.touch}${s.group != null ? ` · ${String(s.group)}` : ''} 的轉化者`;
  return `事件分析${s.bucket ? ` ${s.bucket}` : ''}${Array.isArray(s.group) ? ` · ${s.group.join(' / ')}` : ''}`;
}

/** 規則 → 一行中文（列表卡片、分群詳情用）。 */
export function summarizeRule(q: UsersQuery, cat: Catalog | null, segments: { id: string; name: string }[] = []): string {
  const uf = userFields(cat, segments); const ef = eventFields(cat);
  const parts: string[] = [];
  if (q.segment) parts.push(`屬於${segments.find((s) => s.id === q.segment)?.name ?? q.segment}`);
  for (const c of q.filters ?? []) parts.push(condText(c, uf));
  const bs = (q.behaviors ?? []).map((b) => {
    const ev = cat?.events.find((e) => e.name === b.event)?.cn ?? b.event ?? '?';
    const when = b.time ? `${b.time.from}～${b.time.to}` : '';
    const cnt = b.count ? (b.count.op === 'between' && Array.isArray(b.count.value) ? `${b.count.value[0]}～${b.count.value[1]} 次` : `${COUNT_OPS.find(([o]) => o === b.count?.op)?.[1] ?? ''} ${rawValue(b.count.value)} 次`) : '';
    const fl = (b.filters ?? []).map((c) => condText(c, ef)).join('、');
    return `${when} ${b.done === false ? '未做過' : '做過'} ${ev}${b.done === false ? '' : ` ${cnt}`}${fl ? `（${fl}）` : ''}`.trim();
  });
  if (bs.length) parts.push(bs.join(q.behaviors_op === 'or' ? ' 或 ' : ' 且 '));
  if (q.source) parts.push(`來源：${sourceText(q.source)}`);
  return parts.join(' 且 ') || '（沒有條件）';
}
