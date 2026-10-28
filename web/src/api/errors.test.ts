import { describe, expect, it } from 'vitest';
import { ApiError } from './client';
import { errorView, fieldError } from './errors';

const err = (status: number, body: Record<string, unknown>) => new ApiError(status, String(body.detail ?? ''), body);

describe('錯誤畫面判斷（docs/46 §4.6）', () => {
  it('retryable → 重試；CSRF_INVALID → 重新整理；STEP_UP_REQUIRED → 提示再驗證；回報代碼一律帶', () => {
    expect(errorView(err(504, { detail: '查詢逾時', code: 'TIMEOUT', retryable: true, request_id: 'req-1234567890abcdef' }), 'x')).toMatchObject({ text: '查詢逾時', retry: true, reload: false, reportCode: 'req-1234567890abcdef' });
    expect(errorView(err(403, { detail: '頁面已過期，請重新整理後再操作', code: 'CSRF_INVALID', retryable: false, request_id: 'req-1' }), 'x')).toMatchObject({ reload: true, retry: false });
    expect(errorView(err(403, { detail: '這個操作需要再次驗證身分', code: 'STEP_UP_REQUIRED', retryable: false, request_id: 'req-2' }), 'x').stepUp).toBe(true);
    expect(errorView(err(418, { detail: '新代碼', code: 'SOMETHING_NEW', retryable: false, request_id: 'req-3' }), 'x')).toMatchObject({ text: '新代碼', reportCode: 'req-3' });   // 不認得的代碼：顯示 detail 與回報代碼
    expect(errorView(new Error('network'), '讀取失敗')).toMatchObject({ text: '讀取失敗', reportCode: null, retry: false });
  });
  it('欄位錯誤可依欄位或子路徑取出', () => {
    const e = err(422, { detail: '檢查未通過', code: 'RULE_VIOLATION', retryable: false, request_id: 'req-4', errors: [{ field: 'body.steps[2].event', message: '事件不在字典' }, { field: 'body.owner', message: '缺負責人' }] });
    expect(fieldError(e, 'owner')).toBe('缺負責人');
    expect(fieldError(e, 'body.steps')).toBe('事件不在字典');
    expect(fieldError(e, 'name')).toBeUndefined();
    expect(fieldError('x', 'owner')).toBeUndefined();
  });
});
