// HTTP 用戶端（BFF）：同源 cookie session；非 GET 一律帶 X-CSRF-Token（double-submit，值來自可讀 cookie dmp_csrf）；
// 401 → 回登入頁（帶 return_to）；403 → 丟 ForbiddenError 給頁面顯示「需要權限／需要 step-up」。沒有 token 存在 localStorage。

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) { super(message); }
}
export class ForbiddenError extends ApiError {}

export interface ClientOptions {
  fetchImpl?: typeof fetch;
  readCookie?: (name: string) => string | null;
  onUnauthorized?: (returnTo: string) => void;
  currentPath?: () => string;
}

export function cookieValue(raw: string, name: string): string | null {
  for (const part of raw.split(';')) {
    const [k, ...v] = part.trim().split('=');
    if (k === name) return decodeURIComponent(v.join('='));
  }
  return null;
}

export function createClient(opts: ClientOptions = {}) {
  const fetchImpl = opts.fetchImpl ?? ((input, init) => fetch(input, init));
  const readCookie = opts.readCookie ?? ((n: string) => (typeof document === 'undefined' ? null : cookieValue(document.cookie, n)));
  const currentPath = opts.currentPath ?? (() => (typeof location === 'undefined' ? '/' : location.pathname + location.search));
  // 已在登入頁／未授權頁時不要再導向（啟動時 /v1/iam/me 401 是正常狀態），否則 return_to 會無限套疊
  const onUnauthorized = opts.onUnauthorized ?? ((rt: string) => { if (typeof location === 'undefined' || location.pathname === '/login' || location.pathname === '/no-access') return; location.assign('/login?return_to=' + encodeURIComponent(rt)); });

  async function request<T>(method: string, url: string, body?: unknown, init: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json', ...(init.headers as Record<string, string> | undefined) };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && method !== 'HEAD') {
      const csrf = readCookie('dmp_csrf');
      if (csrf) headers['X-CSRF-Token'] = csrf;
    }
    const res = await fetchImpl(url, { ...init, method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) });
    if (res.status === 401) { onUnauthorized(currentPath()); throw new ApiError(401, '未登入或 session 已逾時'); }
    const text = await res.text();
    const data = text ? safeJson(text) : null;
    if (res.status === 403) throw new ForbiddenError(403, detail(data) ?? '沒有權限', data);
    if (!res.ok) throw new ApiError(res.status, detail(data) ?? `HTTP ${res.status}`, data);
    return data as T;
  }
  return {
    get: <T>(url: string) => request<T>('GET', url),
    post: <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {}),
    put: <T>(url: string, body: unknown) => request<T>('PUT', url, body),
    patch: <T>(url: string, body: unknown) => request<T>('PATCH', url, body),
    del: <T>(url: string) => request<T>('DELETE', url),
  };
}
export type Client = ReturnType<typeof createClient>;

function safeJson(t: string): unknown { try { return JSON.parse(t); } catch { return t; } }
function detail(d: unknown): string | undefined {
  if (d && typeof d === 'object' && 'detail' in d) { const v = (d as { detail: unknown }).detail; return typeof v === 'string' ? v : JSON.stringify(v); }
  return undefined;
}

import { demoFetch } from '@/demo/fetch';
import { isDemo } from '@/demo/mode';

/** 正式：同源 fetch；示範建置（VITE_DEMO=1）：固定資料、不導向登入。 */
export const api = createClient(isDemo ? { fetchImpl: demoFetch, onUnauthorized: () => undefined } : {});
