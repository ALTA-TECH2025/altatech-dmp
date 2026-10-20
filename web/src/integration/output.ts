// 數據輸出（資料輸出任務，docs/06 §8）：型別、中文對照、表單預設與前端先驗（與後端 _validate 同一套規則，先擋住明顯錯誤再送）、檔名樣板示例。
import type { Destination } from '@/tags/types';

export type OutputType = 'realtime' | 'once' | 'daily';
export type OutputKind = 'events' | 'users' | 'segment_members';
export type OutputStatus = 'pending_approval' | 'approved' | 'active' | 'paused' | 'rejected' | 'ended' | 'deleted';
export type RunStatus = 'running' | 'ok' | 'failed' | 'retrying' | 'skipped';

export interface OutputContent { kind: OutputKind; events?: string[]; columns?: string[]; filters?: unknown; segment_id?: string; time?: { from: string; to: string }; identifier?: 'one_id' | 'customer_code' }
export interface OutputFormat { type: 'csv' | 'jsonl' | 'parquet'; compression: 'none' | 'gzip' | 'zstd'; partition: 'none' | 'day'; file_pattern: string; max_rows_per_file: number; header: boolean; manifest: boolean }
export interface OutputTaskCreate { name: string; type: OutputType; content: OutputContent; destination_id: string; format: OutputFormat; schedule?: { at?: string; end_date?: string }; purpose: string; retention_days: number; shared_roles?: string[] }
export interface OutputFile { path: string; rows: number; bytes: number; checksum: string }
export interface OutputRun { id: string; task_id: string; trigger: 'manual' | 'schedule' | 'realtime' | 'replay'; partition_date: string | null; started_at: string; finished_at: string | null; status: RunStatus; rows: number | null; bytes: number | null; files: OutputFile[]; delivery_ids: string[]; error: string | null; attempts: number; watermark: string | null }
export interface OutputTask extends OutputTaskCreate { id: string; status: OutputStatus; approval_id: string | null; destination_approval_id?: string | null; pending_approvals?: string[]; masked: Record<string, string>; rows_estimate: number | null; owner: string; created_at: string; updated_at: string; last_run: OutputRun | null; runs?: OutputRun[] }
export interface PreviewColumn { name: string; cn?: string; level: string; policy: string }
export interface OutputPreview { columns: PreviewColumn[]; rejected: string[]; rows_estimate: number; scan_rows_estimate: number; sample: Record<string, unknown>[]; warnings: string[]; partition_date?: string | null }

export const TYPE_CN: Record<OutputType, string> = { realtime: '實時', once: '一次性', daily: '每日（T+1）' };
export const KIND_CN: Record<OutputKind, string> = { events: '事件明細', users: '用戶寬表', segment_members: '分群名單' };
export const STATUS: Record<OutputStatus, { cn: string; pill: string }> = {
  pending_approval: { cn: '待審批', pill: 'amber' }, approved: { cn: '已核可', pill: 'blue' }, active: { cn: '生效中', pill: 'blue' }, paused: { cn: '已暫停', pill: 'gray' },
  rejected: { cn: '已駁回', pill: 'red' }, ended: { cn: '已結束', pill: 'gray' }, deleted: { cn: '已刪除', pill: 'gray' },
};
export const RUN_CN: Record<RunStatus, { cn: string; pill: string }> = { running: { cn: '執行中', pill: 'blue' }, ok: { cn: '成功', pill: 'blue' }, failed: { cn: '失敗', pill: 'red' }, retrying: { cn: '重試中', pill: 'amber' }, skipped: { cn: '略過', pill: 'gray' } };
export const TRIGGER_CN: Record<OutputRun['trigger'], string> = { manual: '手動', schedule: '排程', realtime: '實時', replay: '重放' };
export const DEST_TYPE_CN: Record<Destination['type'], string> = { webhook: 'Webhook', kafka: 'Kafka', gcs: 'GCS', sftp: 'SFTP', s3: '物件儲存（S3）' };
export const POLICY_CN: Record<string, string> = { plain: '明文', bucket: '區間化', round: '取整', mask_tail: '尾碼遮罩', rejected: '不可輸出', hide: '不可輸出' };

/** 事件明細的固定欄位（與後端 EVENT_COLS 一致；props.<key> 另外加）。 */
export const EVENT_COLUMNS: { name: string; cn: string; level?: string }[] = [
  { name: 'event', cn: '事件' }, { name: 'time', cn: '時間' }, { name: 'event_date', cn: '日期' }, { name: 'source', cn: '來源' }, { name: 'platform', cn: '平台' }, { name: 'app_id', cn: 'App' },
  { name: 'symbol', cn: '商品代號' }, { name: 'side', cn: '買賣別' }, { name: 'channel', cn: '渠道' }, { name: 'amount', cn: '金額', level: 'L2' }, { name: 'utm_source', cn: '流量來源' }, { name: 'utm_campaign', cn: '活動' },
  { name: 'entity_type', cn: '業務實體' }, { name: 'entity_id', cn: '業務 ID' }, { name: 'session_id', cn: 'Session' }, { name: 'event_id', cn: '事件 ID' }, { name: 'props', cn: '屬性 JSON' },
];
export const DEFAULT_EVENT_COLUMNS = ['event', 'time', 'platform', 'channel', 'symbol', 'side', 'amount', 'session_id', 'event_id', 'props'];

export const DEFAULT_FORMAT: OutputFormat = { type: 'csv', compression: 'gzip', partition: 'day', file_pattern: '{task}/{date}/{task}_{date}_{seq}.{ext}', max_rows_per_file: 1_000_000, header: true, manifest: true };

/** 新任務的預設值：一次性、事件明細、昨天往前 7 天、csv.gz、保存 90 天。 */
export function defaultTask(today = new Date()): OutputTaskCreate {
  const d = (n: number) => { const x = new Date(today); x.setDate(x.getDate() - n); return x.toISOString().slice(0, 10); };
  return { name: '', type: 'once', content: { kind: 'events', events: [], columns: [...DEFAULT_EVENT_COLUMNS], filters: [], identifier: 'customer_code', time: { from: d(7), to: d(1) } }, destination_id: '', format: { ...DEFAULT_FORMAT }, schedule: { at: '02:00' }, purpose: '', retention_days: 90, shared_roles: [] };
}

/** 任務類型能接的目的地類型：realtime 只接 kafka／webhook；批量檔案接 sftp／s3／gcs。 */
export function destAllowed(type: OutputType, dest: Pick<Destination, 'type' | 'status'>): boolean {
  if (dest.status !== 'active') return false;
  return type === 'realtime' ? dest.type === 'kafka' || dest.type === 'webhook' : dest.type === 'sftp' || dest.type === 's3' || dest.type === 'gcs';
}

/** 前端先驗：回錯誤訊息清單（空＝可送）。規則同後端 _validate。 */
export function validateTask(t: OutputTaskCreate, dest?: Pick<Destination, 'type' | 'status'> | null): string[] {
  const errs: string[] = [];
  const name = t.name.trim(); if (name.length < 1 || name.length > 50) errs.push('名稱需 1–50 字');
  const purpose = t.purpose.trim(); if (purpose.length < 4 || purpose.length > 200) errs.push('用途需 4–200 字');
  if (!(t.retention_days >= 1 && t.retention_days <= 365)) errs.push('保存天數需在 1–365');
  if (!t.destination_id) errs.push('請選目的地');
  else if (dest && !destAllowed(t.type, dest)) errs.push(t.type === 'realtime' ? '實時任務只能送 Kafka／Webhook 目的地' : '批量任務只能送 SFTP／物件儲存目的地');
  const c = t.content;
  if (t.type === 'realtime' && c.kind !== 'events') errs.push('實時任務只接事件明細');
  if ((c.events?.length ?? 0) > 300) errs.push('事件清單最多 300 個');
  if (c.kind === 'segment_members' && !c.segment_id) errs.push('分群名單需選分群');
  if (c.kind === 'users' && !(c.columns?.length)) errs.push('用戶寬表至少選一個欄位');
  if (t.type === 'once') {
    const f = c.time?.from ?? '', to = c.time?.to ?? '';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f) || !/^\d{4}-\d{2}-\d{2}$/.test(to)) errs.push('一次性任務需要期間');
    else if (to < f) errs.push('期間結束日早於開始日');
  }
  if (t.type === 'daily') {
    if (!/^\d{2}:\d{2}$/.test(t.schedule?.at ?? '')) errs.push('每日任務需要執行時間（HH:MM）');
    if (t.schedule?.end_date && !/^\d{4}-\d{2}-\d{2}$/.test(t.schedule.end_date)) errs.push('結束日格式需為 YYYY-MM-DD');
  }
  if (!(t.format.max_rows_per_file >= 10_000 && t.format.max_rows_per_file <= 5_000_000)) errs.push('每檔列數需在 1 萬–500 萬');
  return errs;
}

/** 檔名樣板套上示例值，讓使用者看到會長怎樣。 */
export function fileExample(fmt: OutputFormat, taskId = 'ot_1a2b3c', date = '2026-10-16'): string {
  const base = fmt.file_pattern.replace(/\{task\}/g, taskId).replace(/\{date\}/g, fmt.partition === 'day' ? date : 'all').replace(/\{seq\}/g, '0001').replace(/\{ext\}/g, fmt.type);
  return base + (fmt.compression === 'gzip' ? '.gz' : fmt.compression === 'zstd' ? '.zst' : '');
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

/** 列表「上次執行」一句話。 */
export function runText(r: OutputRun | null | undefined): string {
  if (!r) return '尚未執行';
  const when = r.started_at.replace('T', ' ').slice(0, 16);
  const part = r.partition_date ? `${r.partition_date} 分區` : '整段期間';
  if (r.status === 'ok') return `${when} · ${part} · ${(r.rows ?? 0).toLocaleString()} 列 · ${r.files.filter((f) => !f.path.endsWith('manifest.json')).length} 檔`;
  if (r.status === 'failed') return `${when} · ${part} · 失敗：${r.error ?? ''}`.trim();
  return `${when} · ${part} · ${RUN_CN[r.status].cn}`;
}

/** 內容摘要（列表用）：事件明細 3 個事件 · 7 欄 · 2026-10-01～10-16。 */
export function contentText(c: OutputContent, segName?: (id: string) => string): string {
  const parts = [KIND_CN[c.kind]];
  if (c.kind === 'events') parts.push(c.events?.length ? `${c.events.length} 個事件` : '全部事件');
  if (c.kind === 'segment_members' && c.segment_id) parts.push(segName?.(c.segment_id) ?? c.segment_id);
  if (c.columns?.length) parts.push(`${c.columns.length} 欄`);
  if (c.time?.from && c.time?.to) parts.push(`${c.time.from}～${c.time.to.slice(5)}`);
  return parts.join(' · ');
}

/** 事件字典按 group 分組（批量添加用）。 */
export function groupEvents(events: { name: string; cn: string; group: string; status: string }[]): { group: string; items: { name: string; cn: string }[] }[] {
  const m = new Map<string, { name: string; cn: string }[]>();
  for (const e of events) { if (e.status !== 'live') continue; const g = e.group || '其他'; if (!m.has(g)) m.set(g, []); m.get(g)!.push({ name: e.name, cn: e.cn }); }
  return [...m.entries()].map(([group, items]) => ({ group, items }));
}

/** 改了輸出內容或目的地 → 後端視同新任務重走審批；表單上先提醒。 */
export function needsReapproval(before: OutputTaskCreate, after: OutputTaskCreate): boolean {
  return JSON.stringify(before.content) !== JSON.stringify(after.content) || before.destination_id !== after.destination_id || before.type !== after.type;
}
