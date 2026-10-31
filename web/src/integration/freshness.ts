// 來源新鮮度設定（/v1/integration/source-freshness；規格 docs/superpowers/specs/2026-10-31-source-freshness.md）的型別與純函式（有單元測試）。
// 規則與後端 backend/integration/freshness.py 相同：即時只帶 max_lag_minutes；日批帶 ready_at＋covers_days_back；時區固定 Asia/Taipei、不可指定。

export type FreshnessMode = 'realtime' | 'daily_batch';
export interface SourceFreshness {
  source: string; configured: boolean; mode: FreshnessMode | null; max_lag_minutes: number | null; ready_at: string | null;
  covers_days_back: number | null; timezone: 'Asia/Taipei'; note: string | null; last_event_time: string | null;
}
export type SourceFreshnessUpdate =
  | { mode: 'realtime'; max_lag_minutes: number; note?: string }
  | { mode: 'daily_batch'; ready_at: string; covers_days_back: number; note?: string };

/** 表單狀態：輸入框都是字串，送出前再轉。 */
export interface FreshnessForm { mode: FreshnessMode; max_lag_minutes: string; ready_at: string; covers_days_back: string; note: string }

export const SOURCE_RE = /^[a-z0-9_]{1,64}$/;
const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const intIn = (v: string, lo: number, hi: number): number | null => (/^\d+$/.test(v.trim()) && +v >= lo && +v <= hi ? +v : null);

/** 既有設定 → 表單；未設定時給日批的常見預設（07:00、T-1），使用者仍要按儲存才生效。 */
export function toForm(r: SourceFreshness | null): FreshnessForm {
  return { mode: r?.mode ?? 'daily_batch', max_lag_minutes: String(r?.max_lag_minutes ?? 15), ready_at: r?.ready_at ?? '07:00', covers_days_back: String(r?.covers_days_back ?? 1), note: r?.note ?? '' };
}

/** 檢查表單；回 { body } 或 { errors }（欄位名 → 訊息）。只送目前模式的欄位（PUT 整筆取代）。 */
export function formToBody(f: FreshnessForm): { body: SourceFreshnessUpdate; errors?: undefined } | { body?: undefined; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const note = f.note.trim();
  if (note.length > 200) errors.note = '備註最多 200 字';
  const extra = note ? { note } : {};
  if (f.mode === 'realtime') {
    const lag = intIn(f.max_lag_minutes, 1, 1440);
    if (lag === null) errors.max_lag_minutes = '請填 1–1440 的整數分鐘';
    return Object.keys(errors).length ? { errors } : { body: { mode: 'realtime', max_lag_minutes: lag as number, ...extra } };
  }
  const days = intIn(f.covers_days_back, 1, 7);
  if (!HHMM.test(f.ready_at.trim())) errors.ready_at = '請填 HH:MM（00:00–23:59）';
  if (days === null) errors.covers_days_back = '請填 1–7 的整數';
  return Object.keys(errors).length ? { errors } : { body: { mode: 'daily_batch', ready_at: f.ready_at.trim(), covers_days_back: days as number, ...extra } };
}

/** 設定的一句話說明（列表用）。 */
export function freshnessText(r: Pick<SourceFreshness, 'configured' | 'mode' | 'max_lag_minutes' | 'ready_at' | 'covers_days_back'>): string {
  if (!r.configured || !r.mode) return '未設定（資料就緒顯示「未知」）';
  if (r.mode === 'realtime') return `即時：最後一筆超過 ${r.max_lag_minutes} 分鐘算延遲`;
  const n = r.covers_days_back ?? 1;
  return `日批：每天 ${r.ready_at}（台北）前到齊 T-${n}；之前只要求 T-${n + 1}`;
}

/** 未設定的排前面（要人處理），其餘依來源代號。 */
export const sortFreshness = (rows: SourceFreshness[]): SourceFreshness[] =>
  [...rows].sort((a, b) => Number(a.configured) - Number(b.configured) || a.source.localeCompare(b.source));
