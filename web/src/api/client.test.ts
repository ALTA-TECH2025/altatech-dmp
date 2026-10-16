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
});
