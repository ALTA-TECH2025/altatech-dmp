// 資料治理純函式（有單元測試）：狀態／類別文字、上線檢查（與後端同規則，先在表單提示）、覆蓋率與日均量文字、字典 CSV 解析
import type { EventProp, EventStatus, Level, MetaEvent } from './types';

export const STATUS_CN: Record<EventStatus, { cn: string; pill: string }> = { live: { cn: '上線', pill: 'blue' }, draft: { cn: '草稿', pill: 'gray' }, quarantine: { cn: '隔離', pill: 'red' }, deprecated: { cn: '停用', pill: 'amber' } };
export const KIND_CN: Record<MetaEvent['kind'], string> = { behavior: '行為', business: '業務', trade: '交易', marketing: '行銷' };
export const ENTITY_CN: Record<MetaEvent['entity'], string> = { user: '用戶', account: '帳戶（Item）', item: '其他 Item' };
export const SENS_PILL: Record<Level, string> = { L0: 'gray', L1: 'gray', L2: 'blue', L3: 'amber', L4: 'red' };
export const SENS_CN: Record<Level, string> = { L0: '聚合', L1: '行為', L2: '敏感業務', L3: '代碼化識別', L4: '可識別（不入倉）' };
export const NAME_RE = /^[a-z][a-z0-9_.]{2,47}$/;

/** 上線檢查（後端同一套；這裡先給表單即時提示）。 */
export function checkEvent(e: { name: string; cn: string; owner: string; properties: EventProp[] }, existing: Set<string>, isNew: boolean, forbidden: Set<string> = new Set()): string[] {
  const errs: string[] = [];
  if (!NAME_RE.test(e.name) && !e.name.startsWith('$')) errs.push('事件名稱須為小寫英數與 _ .（3–48 字元）');
  if (isNew && existing.has(e.name)) errs.push('事件名稱已存在');
  if (!e.cn.trim()) errs.push('請填顯示名稱');
  if (!e.owner.trim()) errs.push('請填負責人');
  if (e.properties.some((p) => p.sensitivity === 'L4' || forbidden.has(p.key))) errs.push('含 L4 屬性：個資不得入倉，請移除或改為雜湊／分桶後的 L3／L2 屬性');
  return errs;
}

export function dailyText(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)} 萬`;
  return v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(v >= 10 ? 0 : 1);
}
export const pct = (v: number | null | undefined, d = 1): string => (v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(d)}%`);

/** 字典 CSV（值,顯示名）→ entries；略過空行與 # 註解。 */
export function parseDictCsv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const i = line.indexOf(','); if (i <= 0 || line.trim().startsWith('#')) continue;
    const k = line.slice(0, i).trim(); const v = line.slice(i + 1).trim();
    if (k && v) out[k] = v;
  }
  return out;
}

/** 虛擬屬性表達式裡引用的維度表（與後端同規則）。 */
export function dimRefs(expr: string): string[] {
  const s = new Set<string>();
  for (const m of expr.matchAll(/dictGet\('([a-z_]+)'/g)) s.add(m[1]);
  for (const m of expr.matchAll(/\b(dim_[a-z0-9_]+)\./g)) s.add(m[1]);
  return Array.from(s).sort();
}
