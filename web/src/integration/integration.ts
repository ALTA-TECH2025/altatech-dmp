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
export const RUN_STATUS_CN: Record<string, string> = { queued: '已排入佇列', success: '成功', running: '執行中', failed: '失敗', retry_wait: '等待重試', retried: '已重試', interrupted: '中斷（主控切換）', skipped: '略過（上游未就緒）' };
/** 重試鏈上「還沒結束」的狀態。retried 是被下一筆接手的舊紀錄；interrupted 若還有重試次數，後端會在同一交易補一筆 retry_wait，所以鏈尾是 interrupted 代表重試已用完。 */
export const RUN_PENDING = new Set(['queued', 'running', 'retry_wait', 'retried']);
export const QUEUE_POLL_MS = 3000;
export const QUEUE_SLOW_POLL_MS = 15_000;
export const QUEUE_WAIT_MS = 5 * 60_000;

/** 一次「立即執行」的重試鏈：原始那筆，加上之後同任務、同排程時間、由重試產生的紀錄（暫時失敗重試、主控中斷接手都會開新紀錄）。
 *  排程窗口的紀錄 trigger 是 schedule，不會混進來；同一分鐘的排程也不會跑（手動那筆先佔住窗口）。 */
export function runChain(rows: SyncRun[], runId: number): SyncRun[] {
  const root = rows.find((x) => x.id === runId);
  if (!root) return [];
  return rows.filter((x) => x.id === runId || (x.id > runId && x.trigger === 'retry' && x.scheduled_for === root.scheduled_for)).sort((a, b) => a.id - b.id);
}

/** 鏈尾那筆與是否已結束。還沒出現在紀錄裡（latest=null）視為未結束。 */
export function chainState(rows: SyncRun[], runId: number): { latest: SyncRun | null; done: boolean } {
  const c = runChain(rows, runId); const latest = c[c.length - 1] ?? null;
  return { latest, done: !!latest && !RUN_PENDING.has(latest.status) };
}

/** 輪詢一次手動執行直到整條重試鏈結束；不依賴 React，方便用假時鐘測。
 *  超過 5 分鐘：鏈尾仍是 queued（或還沒出現）才標 stale 提醒確認 worker；執行中的長任務不警告。兩種都繼續追，只是放慢到 15 秒。 */
export function watchQueuedRun(o: {
  runId: number; fetchRuns: () => Promise<SyncRun[]>;
  onUpdate: (s: { latest: SyncRun | null; stale: boolean }) => void; onDone: (r: SyncRun) => void;
  now?: () => number; schedule?: (fn: () => void, ms: number) => unknown; cancel?: (h: unknown) => void;
}): () => void {
  const now = o.now ?? Date.now; const schedule = o.schedule ?? ((fn, ms) => setTimeout(fn, ms)); const cancel = o.cancel ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  const t0 = now(); let alive = true; let handle: unknown;
  const tick = async () => {
    try {
      const st = chainState(await o.fetchRuns(), o.runId);
      if (!alive) return;
      o.onUpdate({ latest: st.latest, stale: now() - t0 > QUEUE_WAIT_MS && (!st.latest || st.latest.status === 'queued') });
      if (st.done && st.latest) { o.onDone(st.latest); return; }
    } catch { /* 暫時讀不到就下一輪再試 */ }
    if (alive) handle = schedule(() => void tick(), now() - t0 > QUEUE_WAIT_MS ? QUEUE_SLOW_POLL_MS : QUEUE_POLL_MS);
  };
  handle = schedule(() => void tick(), QUEUE_POLL_MS);
  return () => { alive = false; cancel(handle); };
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
