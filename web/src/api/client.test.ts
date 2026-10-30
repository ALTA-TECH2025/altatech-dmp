import { describe, expect, it, vi } from 'vitest';
import { ApiError, cookieValue, createClient, ForbiddenError } from './client';

const res = (status: number, body: unknown = null) => ({ status, ok: status >= 200 && status < 300, text: async () => (body === null ? '' : JSON.stringify(body)) }) as unknown as Response;

describe('api client（BFF：cookie session + CSRF double-submit）', () => {
  it('非 GET 帶 X-CSRF-Token（值來自 dmp_csrf cookie），GET 不帶；credentials=same-origin', async () => {
    const fetchImpl = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => res(200, { ok: true, init }));
    const c = createClient({ fetchImpl, readCookie: (n) => (n === 'dmp_csrf' ? 'tok123' : null), onUnauthorized: () => {} });
    await c.post('/auth/logout');
    const init = fetchImpl.mock.calls[0][1]!;
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('tok123');
    expect(init.credentials).toBe('same-origin');
    await c.get('/v1/iam/me');
    expect((fetchImpl.mock.calls[1][1]!.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
  });
  it('401 → onUnauthorized(return_to) 並丟 ApiError', async () => {
    const onUnauthorized = vi.fn();
    const c = createClient({ fetchImpl: async () => res(401), readCookie: () => null, onUnauthorized, currentPath: () => '/users?x=1' });
    await expect(c.get('/v1/iam/me')).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledWith('/users?x=1');
  });
  it('403 → ForbiddenError 帶後端 detail', async () => {
    const c = createClient({ fetchImpl: async () => res(403, { detail: 'CSRF token 不符' }), readCookie: () => null, onUnauthorized: () => {} });
    await expect(c.post('/x')).rejects.toMatchObject({ status: 403, message: 'CSRF token 不符' });
    await expect(c.post('/x')).rejects.toBeInstanceOf(ForbiddenError);
  });
  it('cookieValue 解析', () => {
    expect(cookieValue('a=1; dmp_csrf=abc%3D; b=2', 'dmp_csrf')).toBe('abc=');
    expect(cookieValue('a=1', 'dmp_sid')).toBeNull();
  });
  it('401 依錯誤碼：session 失效才導回登入；驗證碼錯留在原頁並顯示後端訊息（docs/46 §4.6）', async () => {
    for (const code of ['UNAUTHENTICATED', 'TOKEN_INVALID']) {
      const onUnauthorized = vi.fn();
      const c = createClient({ fetchImpl: async () => res(401, { detail: '未登入或 session 已逾時', code, retryable: false, request_id: 'req-0000000000000001' }), readCookie: () => null, onUnauthorized, currentPath: () => '/funnel' });
      await expect(c.get('/v1/iam/me')).rejects.toMatchObject({ status: 401, code });
      expect(onUnauthorized).toHaveBeenCalledWith('/funnel');
    }
    const onUnauthorized = vi.fn();
    const c = createClient({ fetchImpl: async () => res(401, { detail: '驗證碼錯誤或已使用', code: 'OTP_INVALID', retryable: false, request_id: 'req-0000000000000002' }), readCookie: () => null, onUnauthorized });
    await expect(c.post('/auth/step-up', { totp: '000000' })).rejects.toMatchObject({ status: 401, code: 'OTP_INVALID', message: '驗證碼錯誤或已使用' });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
  it('讀 Error 結構：code、retryable、request_id、errors[]；5xx 訊息附回報代碼', async () => {
    const c = createClient({ fetchImpl: async () => res(502, { detail: '後端服務執行失敗', code: 'UPSTREAM_FAILED', retryable: true, request_id: 'f5-abc-0001' }), readCookie: () => null, onUnauthorized: () => {} });
    const e = await c.get('/v1/sql/run').catch((x: unknown) => x) as ApiError;
    expect(e).toMatchObject({ status: 502, code: 'UPSTREAM_FAILED', retryable: true, requestId: 'f5-abc-0001', detail: '後端服務執行失敗' });
    expect(e.message).toBe('後端服務執行失敗（回報代碼：f5-abc-0001）');
    const c2 = createClient({ fetchImpl: async () => res(400, { detail: '請求內容不正確：query.n Field required', code: 'INVALID_ARGUMENT', retryable: false, request_id: 'req-0000000000000003', errors: [{ field: 'query.n', message: 'Field required', code: 'INVALID_ARGUMENT' }] }), readCookie: () => null, onUnauthorized: () => {} });
    const e2 = await c2.post('/x').catch((x: unknown) => x) as ApiError;
    expect(e2.message).toBe('請求內容不正確：query.n Field required');                       // 4xx 不附編號，訊息照後端
    expect(e2.fieldErrors).toEqual([{ field: 'query.n', message: 'Field required', code: 'INVALID_ARGUMENT' }]);
    const c3 = createClient({ fetchImpl: async () => res(500, { detail: 'boom' }), readCookie: () => null, onUnauthorized: () => {} });   // 舊格式（沒有 code）仍可用
    await expect(c3.get('/x')).rejects.toMatchObject({ code: null, retryable: false, requestId: null, message: 'boom' });
  });
});
