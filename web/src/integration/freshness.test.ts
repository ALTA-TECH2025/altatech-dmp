import { describe, expect, it } from 'vitest';
import { formToBody, freshnessText, sortFreshness, SOURCE_RE, toForm, type SourceFreshness } from './freshness';

const row = (source: string, over: Partial<SourceFreshness> = {}): SourceFreshness => ({
  source, configured: false, mode: null, max_lag_minutes: null, ready_at: null, covers_days_back: null, timezone: 'Asia/Taipei', note: null, last_event_time: null, ...over,
});

describe('來源新鮮度表單', () => {
  it('未設定的來源給日批常見預設；既有設定照帶', () => {
    expect(toForm(null)).toEqual({ mode: 'daily_batch', max_lag_minutes: '15', ready_at: '07:00', covers_days_back: '1', note: '' });
    expect(toForm(row('sdk', { configured: true, mode: 'realtime', max_lag_minutes: 30, note: 'x' })).max_lag_minutes).toBe('30');
  });
  it('只送目前模式的欄位（整筆取代），備註空白不送', () => {
    expect(formToBody({ mode: 'realtime', max_lag_minutes: '15', ready_at: '07:00', covers_days_back: '1', note: '  ' })).toEqual({ body: { mode: 'realtime', max_lag_minutes: 15 } });
    expect(formToBody({ mode: 'daily_batch', max_lag_minutes: '15', ready_at: ' 07:00 ', covers_days_back: '2', note: 'GA360' }))
      .toEqual({ body: { mode: 'daily_batch', ready_at: '07:00', covers_days_back: 2, note: 'GA360' } });
  });
  it('範圍與格式錯誤回欄位訊息，不送出', () => {
    expect(formToBody({ mode: 'realtime', max_lag_minutes: '0', ready_at: '', covers_days_back: '', note: '' }).errors).toHaveProperty('max_lag_minutes');
    expect(formToBody({ mode: 'realtime', max_lag_minutes: '1441', ready_at: '', covers_days_back: '', note: '' }).errors).toHaveProperty('max_lag_minutes');
    expect(formToBody({ mode: 'realtime', max_lag_minutes: '1.5', ready_at: '', covers_days_back: '', note: '' }).errors).toHaveProperty('max_lag_minutes');
    const e = formToBody({ mode: 'daily_batch', max_lag_minutes: '', ready_at: '24:00', covers_days_back: '8', note: 'x'.repeat(201) }).errors!;
    expect(Object.keys(e).sort()).toEqual(['covers_days_back', 'note', 'ready_at']);
    expect(formToBody({ mode: 'daily_batch', max_lag_minutes: '', ready_at: '7:00', covers_days_back: '1', note: '' }).errors).toHaveProperty('ready_at');
  });
  it('說明文字與排序：未設定排前面', () => {
    expect(freshnessText(row('x'))).toContain('未設定');
    expect(freshnessText(row('x', { configured: true, mode: 'realtime', max_lag_minutes: 15 }))).toContain('15 分鐘');
    expect(freshnessText(row('x', { configured: true, mode: 'daily_batch', ready_at: '07:00', covers_days_back: 1 }))).toBe('日批：每天 07:00（台北）前到齊 T-1；之前只要求 T-2');
    expect(sortFreshness([row('b', { configured: true, mode: 'realtime' }), row('z'), row('a')]).map((r) => r.source)).toEqual(['a', 'z', 'b']);
  });
  it('來源代號格式與後端相同', () => {
    expect(SOURCE_RE.test('mdl_open_acct')).toBe(true); expect(SOURCE_RE.test('GA360')).toBe(false); expect(SOURCE_RE.test('a'.repeat(65))).toBe(false);
  });
});
