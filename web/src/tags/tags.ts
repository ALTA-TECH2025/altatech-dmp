// 標籤／推送的標籤文字與純函式（有單元測試）
import type { TagKind, TagSpec, Delivery, DeliveryStatus, DestinationStatus, Push, PushStatus, Tag, TagStatus } from './types';

export const TAG_STATUS_CN: Record<TagStatus, string> = { draft: '草稿', pending_approval: '待審批', active: '生效中', paused: '已暫停', recycled: '回收站' };
export const TAG_STATUS_PILL: Record<TagStatus, string> = { draft: 'gray', pending_approval: 'amber', active: 'blue', paused: 'gray', recycled: 'red' };
export const TAG_KIND_CN: Record<TagKind, string> = { values: '自定義標籤值', sql: 'SQL 標籤', metric: '基礎指標值', firstlast: '首末次特徵', rfm: 'RFM' };
export const AGG_CN = { count: '次數', sum: '總和', avg: '平均', max: '最大值', min: '最小值', days: '有行為天數' } as const;
export const FEATURE_CN = { date: '發生日期', days_since: '距基準日天數', field: '當時的屬性值' } as const;
/** 分桶界線 → 標籤值清單（與後端 _bucket_expr 同規則） */
export function bucketLabels(b: number[]): string[] { const n = (x: number) => (Number.isInteger(x) ? String(x) : String(x)); return [`<${n(b[0])}`, ...b.slice(0, -1).map((x, i) => `${n(x)}–${n(b[i + 1])}`), `≥${n(b[b.length - 1])}`]; }
/** metric／firstlast／rfm 的一行說明 */
export function specText(t: { kind: TagKind; spec?: TagSpec }, eventCn: (e: string) => string = (e) => e): string {
  const s = t.spec; if (!s) return '';
  const win = s.window_days ? `近 ${s.window_days} 天` : '不限期間';
  const bk = s.buckets?.length ? `，分桶 ${bucketLabels(s.buckets).join('／')}` : '';
  if (t.kind === 'metric') return `${eventCn(s.event)} ${AGG_CN[s.agg ?? 'count']}${s.field ? `（${s.field}）` : ''}，${win}${bk}`;
  if (t.kind === 'firstlast') return `${s.which === 'last' ? '末次' : '首次'}${eventCn(s.event)}的${FEATURE_CN[s.feature ?? 'date']}${s.feature === 'field' ? `（${s.field}）` : ''}，${win}${bk}`;
  if (t.kind === 'rfm') return `${eventCn(s.event)}，金額 ${s.amount_field ?? 'amount'}，${win}，各分 ${s.bins ?? 5} 級，輸出${s.output === 'segment' ? '八類客群' : 'R／F／M 分數'}`;
  return '';
}
/** 逗號分隔的分桶字串 → 數字陣列；非遞增或非數字回 null */
export function parseBuckets(s: string): number[] | null {
  const t = s.trim(); if (!t) return [];
  const xs = t.split(/[\s,，、]+/).filter(Boolean).map(Number); if (xs.some((x) => Number.isNaN(x)) || xs.length > 10) return null;
  for (let i = 1; i < xs.length; i++) if (xs[i] <= xs[i - 1]) return null;
  return xs;
}
export const DTYPE_CN = { string: '字串', number: '數值', bool: '布林' } as const;
export const PUSH_STATUS_CN: Record<PushStatus, string> = { pending_approval: '待審批', active: '生效中', paused: '已暫停', rejected: '已退回', deleted: '已刪除' };
export const PUSH_STATUS_PILL: Record<PushStatus, string> = { pending_approval: 'amber', active: 'blue', paused: 'gray', rejected: 'red', deleted: 'gray' };
export const MODE_CN = { delta: '增量（加入／移除）', full: '全量（需審批）' } as const;
export const SCHEDULE_CN = { now: '立即', after_batch: '日批完成後', cron: '每日定時' } as const;
export const DELIVERY_STATUS_CN: Record<DeliveryStatus, string> = { ok: '成功', retrying: '重試中', failed: '失敗', dlq: 'DLQ' };
export const DELIVERY_PILL: Record<DeliveryStatus, string> = { ok: 'blue', retrying: 'amber', failed: 'red', dlq: 'red' };
export const DEST_STATUS_CN: Record<DestinationStatus, string> = { active: '啟用', paused: '停用' };
export const EVENT_CN: Record<string, string> = { 'audience.delta': '增量名單', 'audience.full': '全量名單', 'audience.complete': '全量收尾', 'audience.test': '測試事件', 'tag.update': '標籤更新' };

export function updateText(u: Tag['update']): string {
  if (u.mode === 'manual') return '手動';
  return `${u.freq === 'daily' ? '每天' : u.freq === 'weekly' ? '每週一' : '每月 1 日'} ${u.earliest} 起 · 留 ${u.keep} 版`;
}
export function pct(x: number | null | undefined): string { return x == null ? '—' : `${(x * 100).toFixed(1)}%`; }
export function fmtTs(s: string | null | undefined): string { return s ? s.replace('T', ' ').slice(0, 16) : '—'; }
/** 推送單的下一步說明：待審批／排程文字／最近結果。 */
export function pushSummary(p: Push): string {
  if (p.status === 'pending_approval') return '等待數位經營核可';
  if (p.status === 'rejected') return '已退回';
  const r = p.last_result;
  const last = r ? `最近 v${r.version}：+${r.added} −${r.removed}，${r.batches} 批（${r.failed ? `${r.failed} 批失敗` : '全部成功'}）` : '尚未交付';
  const sched = p.schedule === 'now' ? '' : p.schedule === 'cron' ? `；每日 ${p.cron} 自動增量` : '；分群有新版本即自動增量';
  return last + sched;
}
/** 交付摘要列：事件、批次、回應。 */
export function deliveryText(d: Delivery): string {
  const resp = d.response ? (d.response.status == null ? `連線失敗 · ${d.response.body.slice(0, 40)}` : `${d.response.status} · ${d.response.ms} ms`) : '—';
  return `${EVENT_CN[d.event] ?? d.event} ${d.batch.index}/${d.batch.total}（${d.members} 筆）· ${resp}${d.status === 'retrying' && d.next_retry_at ? ` · 下次重試 ${fmtTs(d.next_retry_at)}` : ''}`;
}
/** 分布列正規化：取前 n 名，其餘合併為「其他」，並回傳最大值供長條比例。 */
export function topDistribution(rows: { value: string; users: number }[], n = 8): { rows: { value: string; users: number }[]; max: number } {
  const sorted = [...rows].sort((a, b) => b.users - a.users);
  const head = sorted.slice(0, n); const rest = sorted.slice(n).reduce((s, r) => s + r.users, 0);
  const out = rest > 0 ? [...head, { value: '其他', users: rest }] : head;
  return { rows: out, max: Math.max(1, ...out.map((r) => r.users)) };
}
/** 名稱 → 標籤 key（與後端同規則：小寫英數底線，否則留空讓後端生成）。 */
export function suggestKey(name: string): string {
  const k = name.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  return /^[a-z][a-z0-9_]{1,40}$/.test(k) ? k : '';
}
