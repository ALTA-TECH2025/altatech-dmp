// 用戶條件規則（UsersQuery）的純函式：欄位目錄、條件樹（且／或）、行為指標與比較、規則清理、摘要、有效性。無 DOM，有單元測試。
import type { Catalog, Cond } from '@/analytics/types';
import type { CmpOp, CondGroup, MeasureAgg, RuleFilters, UsersBehavior, UsersQuery, UsersSource } from './types';

export type FieldType = 'string' | 'number' | 'bool' | 'date';
export interface FieldOpt { v: string; cn: string; type: FieldType; values?: string[] }

const T: Record<string, FieldType> = { enum: 'string', string: 'string', number: 'number', bool: 'bool', date: 'date', datetime: 'date' };
export const CMP_OPS: [CmpOp, string][] = [['gte', '≥'], ['lte', '≤'], ['eq', '='], ['gt', '>'], ['lt', '<'], ['between', '介於']];
export const MEASURES: [MeasureAgg, string][] = [['count', '總次數'], ['days', '活躍天數'], ['sum', '合計'], ['avg', '平均'], ['max', '最大值'], ['min', '最小值']];
export const NUMERIC_MEASURES: MeasureAgg[] = ['sum', 'avg', 'max', 'min'];
export const OP_CN: Record<string, string> = { eq: '等於', neq: '不等於', in: '包含於', nin: '不包含於', like: '包含文字', nlike: '不包含文字', set: '有值', nset: '沒有值', gt: '>', gte: '≥', lt: '<', lte: '≤', between: '介於', true: '是', false: '否', rel: '最近 N 天內' };
export const NO_VALUE_OPS = new Set(['set', 'nset', 'true', 'false']);
export const MAX_DEPTH = 3;

// ---- 欄位目錄 ----
/** 用戶條件可選欄位：寬表用戶屬性（user.<key>）+ 分群（seg.<id>）。 */
export function userFields(cat: Catalog | null, segments: { id: string; name: string }[] = []): FieldOpt[] {
  const props = (cat?.user_props ?? []).filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: u.cn, type: T[u.type] ?? 'string', values: u.values?.length ? u.values : undefined }));
  return [...props, ...segments.map((s) => ({ v: `seg.${s.id}`, cn: `分群：${s.name}`, type: 'string' as FieldType }))];
}
/** 事件屬性條件可選欄位（行為裡的「且屬性…」）。 */
export function eventFields(cat: Catalog | null): FieldOpt[] {
  const seen = new Set<string>(); const out: FieldOpt[] = [];
  for (const f of [...(cat?.event_columns ?? []).map((c) => ({ v: c.name, cn: c.cn, type: (c.type ?? 'string') as FieldType })), ...(cat?.dimensions ?? []).map((d) => ({ v: d.name, cn: d.cn, type: (d.type as FieldType) ?? 'string', values: d.values?.length ? d.values : undefined }))]) {
    if (!seen.has(f.v)) { seen.add(f.v); out.push(f); }
  }
  return out;
}
export function numericEventFields(cat: Catalog | null): FieldOpt[] { return eventFields(cat).filter((f) => f.type === 'number'); }
export function opsFor(cat: Catalog | null, f: FieldOpt | undefined): string[] {
  if (!f) return ['eq'];
  if (f.v.startsWith('seg.')) return ['in', 'nin'];
  return cat?.ops[f.type] ?? ['eq'];
}

// ---- 條件值 ----
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

// ---- 條件樹 ----
export const isGroup = (x: Cond | CondGroup | undefined): x is CondGroup => !!x && typeof x === 'object' && 'items' in x;
export const emptyGroup = (op: 'and' | 'or' = 'and'): CondGroup => ({ op, items: [] });
export const emptyCond = (): Cond => ({ field: '', op: 'eq', value: '' });
/** 任何形狀（undefined／平鋪陣列／樹）→ 樹；平鋪陣列視為「且」。 */
export function toGroup(f: RuleFilters | undefined): CondGroup { return f === undefined ? emptyGroup() : Array.isArray(f) ? { op: 'and', items: f } : f; }
/** 去掉空群組；沒有條件 → undefined（送後端時不帶 filters）。 */
export function pruneGroup(g: CondGroup): CondGroup | undefined {
  const items = g.items.map((it) => (isGroup(it) ? pruneGroup(it) : it)).filter((x): x is Cond | CondGroup => !!x);
  return items.length ? { op: g.op, items } : undefined;
}
export function condOk(c: Cond): boolean {
  if (!c.field) return false;
  if (NO_VALUE_OPS.has(c.op) || c.field.startsWith('seg.')) return true;
  return !(c.value === '' || c.value === undefined || (Array.isArray(c.value) && !c.value.length));
}
export function groupOk(g: CondGroup | undefined): boolean { return !g || g.items.every((it) => (isGroup(it) ? groupOk(it) : condOk(it))); }
export function countConds(g: CondGroup | undefined): number { return g ? g.items.reduce((n, it) => n + (isGroup(it) ? countConds(it) : 1), 0) : 0; }

// ---- 行為 ----
export function emptyRule(project?: string): UsersQuery { return { project, filters: emptyGroup(), behaviors: [], behaviors_op: 'and', combine: 'and' }; }
export function newBehavior(lastDays = 30): UsersBehavior { return { event: '', done: true, measure: { agg: 'count' }, cmp: { op: 'gte', value: 1 }, time: { last_days: lastDays } }; }
/** 舊規則（count、絕對期間、平鋪 filters）讀進編輯器時正規化。 */
export function normalizeBehavior(b: UsersBehavior): UsersBehavior {
  const { count, ...rest } = b;
  return { ...rest, measure: b.measure ?? { agg: 'count' }, cmp: b.cmp ?? count ?? { op: 'gte', value: 1 }, filters: toGroup(b.filters), time: b.time ?? { last_days: 30 } };
}
export function normalizeRule(q: UsersQuery): UsersQuery { return { ...q, filters: toGroup(q.filters), behaviors: (q.behaviors ?? []).map(normalizeBehavior), behaviors_op: q.behaviors_op ?? 'and', combine: q.combine ?? 'and' }; }
export function behaviorOk(b: UsersBehavior): boolean {
  if (!b.event) return false;
  const t = b.time ?? {}; if (!((t.last_days && t.last_days >= 1) || (t.from && t.to))) return false;
  if (!groupOk(toGroup(b.filters))) return false;
  if (b.done === false) return true;
  const agg = b.measure?.agg ?? 'count'; if (NUMERIC_MEASURES.includes(agg) && !b.measure?.field) return false;
  const v = (b.cmp ?? b.count)?.value; return v !== undefined && !(Array.isArray(v) ? v.length !== 2 || v.some((x) => Number.isNaN(Number(x))) : Number.isNaN(Number(v)));
}
/** 送後端前清理：空群組拿掉、未做過不帶指標、deprecated count 不帶。 */
export function cleanRule(q: UsersQuery): UsersQuery {
  const filters = pruneGroup(toGroup(q.filters));
  const behaviors = (q.behaviors ?? []).map((b) => {
    const { count, ...rest } = b; const f = pruneGroup(toGroup(b.filters));
    const base: UsersBehavior = { ...rest, filters: f, cmp: b.cmp ?? count };
    if (b.done === false) { delete base.measure; delete base.cmp; }
    return base;
  });
  return { ...q, filters, behaviors: behaviors.length ? behaviors : undefined, combine: filters && behaviors.length ? q.combine ?? 'and' : undefined };
}
/** 規則有效：至少一個條件；條件要有欄位與值；行為要有事件、期間、指標欄位與比較值。 */
export function ruleValid(q: UsersQuery): boolean {
  const g = pruneGroup(toGroup(q.filters)); const bs = q.behaviors ?? [];
  if (!g && !bs.length && !q.source && !q.segment) return false;
  return groupOk(g) && bs.every(behaviorOk);
}

// ---- 摘要 ----
function condText(c: Cond, fields: FieldOpt[]): string {
  const f = fields.find((x) => x.v === c.field); const name = f?.cn ?? c.field;
  if (c.field.startsWith('seg.')) return `${c.op === 'nin' ? '不屬於' : '屬於'}${name}`;
  if (NO_VALUE_OPS.has(c.op)) return `${name} ${OP_CN[c.op] ?? c.op}`;
  if (c.op === 'rel') return `${name} 最近 ${(c.value as { days: number })?.days ?? '?'} 天內`;
  return `${name} ${OP_CN[c.op] ?? c.op} ${rawValue(c.value)}`;
}
export function groupText(g: CondGroup | undefined, fields: FieldOpt[], top = true): string {
  if (!g || !g.items.length) return '';
  const s = g.items.map((it) => (isGroup(it) ? groupText(it, fields, false) : condText(it, fields))).filter(Boolean).join(g.op === 'or' ? ' 或 ' : ' 且 ');
  return top || g.items.length < 2 ? s : `（${s}）`;
}
export function behaviorText(b: UsersBehavior, cat: Catalog | null): string {
  const ev = cat?.events.find((e) => e.name === b.event)?.cn ?? b.event ?? '?';
  const t = b.time ?? {}; const when = t.last_days ? `近 ${t.last_days} 天` : t.from && t.to ? `${t.from}～${t.to}` : '';
  const fl = groupText(pruneGroup(toGroup(b.filters)), eventFields(cat));
  if (b.done === false) return `${when} 未做過 ${ev}${fl ? `（${fl}）` : ''}`.trim();
  const agg = b.measure?.agg ?? 'count'; const cmp = b.cmp ?? b.count ?? { op: 'gte' as CmpOp, value: 1 };
  const fieldCn = b.measure?.field ? (eventFields(cat).find((f) => f.v === b.measure?.field)?.cn ?? b.measure.field) : '';
  const m = agg === 'count' ? '' : agg === 'days' ? '活躍天數' : `${fieldCn}${MEASURES.find(([a]) => a === agg)?.[1] ?? agg}`;
  const unit = agg === 'count' ? ' 次' : agg === 'days' ? ' 天' : '';
  const v = cmp.op === 'between' && Array.isArray(cmp.value) ? `${cmp.value[0]}～${cmp.value[1]}` : `${CMP_OPS.find(([o]) => o === cmp.op)?.[1] ?? ''} ${rawValue(cmp.value)}`;
  return `${when} 做過 ${ev}${fl ? `（${fl}）` : ''} ${m}${m ? ' ' : ''}${v}${unit}`.replace(/\s+/g, ' ').trim();
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
  const parts: string[] = [];
  if (q.segment) parts.push(`屬於${segments.find((s) => s.id === q.segment)?.name ?? q.segment}`);
  const ft = groupText(pruneGroup(toGroup(q.filters)), userFields(cat, segments)); if (ft) parts.push(ft);
  const bs = (q.behaviors ?? []).map((b) => behaviorText(b, cat));
  const bt = bs.join(q.behaviors_op === 'or' ? ' 或 ' : ' 且 ');
  let s = parts.join(' 且 ');
  if (bt) s = s ? `${s}${q.combine === 'or' ? ' 或 ' : ' 且 '}${bs.length > 1 && q.combine === 'or' ? `（${bt}）` : bt}` : bt;
  if (q.source) s = s ? `${s} 且 來源：${sourceText(q.source)}` : `來源：${sourceText(q.source)}`;
  return s || '（沒有條件）';
}
