// 數據整合頁的純函式與顯示常數（有單元測試）：密鑰參照格式、疑似明文、輪替文字、任務與執行狀態、排程文字
import type { Rotation, RunSummary, SyncRun, SyncTask, TestStep } from './types';

/** secret_ref 三種格式（與 backend/integration/auth.py 相同）。 */
export const SECRET_REF_RES = [
  /^projects\/[^/]+\/secrets\/[^/]+\/versions\/(\d+|latest)$/,
  /^kv\/data\/dmp\/[a-z]+\/[a-z0-9_]+#[a-z_]+$/,
  /^secret:\/\/[a-z0-9-]+\/[a-z0-9-]+\/[a-zA-Z0-9_.-]+$/,
];
export const isSecretRef = (v: string): boolean => SECRET_REF_RES.some((r) => r.test(v.trim()));

/** 非機密欄位裡疑似明文密鑰：欄名含 password／secret／token／key，或值像金鑰。 */
export function looksLikeSecret(key: string, value: string): boolean {
  const k = key.toLowerCase();
  if (['password', 'secret', 'token', 'keytab'].some((w) => k.includes(w)) || k === 'key') return true;
  return value.startsWith('-----BEGIN') || (value.length > 40 && /^[A-Za-z0-9+/=_-]+$/.test(value));
}

export const STEP_CN: Record<TestStep, string> = { validate: '設定檢查', dns: 'DNS', tcp: 'TCP', tls: 'TLS', auth: '鑑權', privilege: '權限', cdc_prereq: 'CDC 前置', sample_read: '讀樣本', clock_skew: '時鐘' };

export function rotationText(r: Rotation): { text: string; pill: 'gray' | 'blue' | 'amber' | 'red' } {
  if (r.kind === 'short_lived') return { text: '短效憑證，免輪替', pill: 'gray' };
  const left = r.days_left ?? 0;
  if (r.state === 'expired') return { text: `已過期 ${-left} 天`, pill: 'red' };
  if (r.state === 'warn') return { text: `${left} 天後到期（${r.due}）`, pill: 'amber' };
  return { text: `每 ${r.every_days} 天 · ${r.due} 到期`, pill: 'blue' };
}

export const TASK_STATUS: Record<SyncTask['status'], { cn: string; pill: string }> = { active: { cn: '啟用', pill: 'blue' }, paused: { cn: '已暫停', pill: 'amber' }, error: { cn: '最近失敗', pill: 'red' } };
export const RUN_STATUS_CN: Record<string, string> = { queued: '已排入佇列', success: '成功', running: '執行中', failed: '失敗', retry_wait: '等待重試', retried: '已重試', interrupted: '中斷（主控切換，將重試）', skipped: '略過（上游未就緒）' };
/** 還沒結束的狀態：排隊、執行中、中斷後等重試。 */
export const RUN_PENDING = new Set(['queued', 'running', 'retry_wait', 'interrupted']);
export const QUEUE_POLL_MS = 3000;
export const QUEUE_WAIT_MS = 5 * 60_000;

/** 立即執行後的輪詢：在執行紀錄裡找排隊那一筆；結束了回該筆，還沒結束（或還沒出現）回 null。 */
export function queuedResult(rows: SyncRun[], runId: number): SyncRun | null {
  const r = rows.find((x) => x.id === runId);
  return r && !RUN_PENDING.has(r.status) ? r : null;
}
export const runPill = (s: string): string => (s === 'success' ? 'blue' : s === 'failed' ? 'red' : s === 'skipped' ? 'gray' : 'amber');

/** 最近執行一行摘要。 */
export function runText(r: RunSummary | null): string {
  if (!r) return '尚未執行';
  const base = `${RUN_STATUS_CN[r.status] ?? r.status} · ${r.scheduled_for.slice(0, 16).replace('T', ' ')}`;
  if (r.status === 'success') return `${base} · 讀 ${r.rows_in ?? 0} 載入 ${r.rows_loaded ?? 0}${r.rows_quarantined ? ` 隔離 ${r.rows_quarantined}` : ''}${r.rows_rejected ? ` 拒絕 ${r.rows_rejected}` : ''}`;
  return r.error ? `${base} · ${r.error}` : base;
}

/** 排程設定 → 排程器需要的 schedule。cron 與 interval 擇一。 */
export function scheduleBody(kind: 'interval' | 'cron', value: string): { interval_min?: number; cron?: string } | null {
  if (kind === 'interval') { const n = Number(value); return Number.isInteger(n) && n > 0 ? { interval_min: n } : null; }
  const parts = value.trim().split(/\s+/);
  return parts.length === 5 ? { cron: parts.join(' ') } : null;
}

export const SENS_PILL: Record<string, string> = { L1: 'gray', L2: 'blue', L3: 'amber', L4: 'red' };
export const parseList = (s: string): string[] => s.split(/[,\s，、]+/).map((x) => x.trim()).filter(Boolean);
