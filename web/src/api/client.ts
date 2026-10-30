// HTTP 用戶端（BFF）：同源 cookie session；非 GET 一律帶 X-CSRF-Token（double-submit，值來自可讀 cookie dmp_csrf）；
// 錯誤依 Error 結構（docs/46、openapi components.schemas.Error）讀 code：UNAUTHENTICATED／TOKEN_INVALID → 回登入頁（帶 return_to）；
// 其他 401（驗證碼錯、帳密錯）留在原頁顯示訊息；403 → ForbiddenError。沒有 token 存在 localStorage。

/** 後端錯誤的欄位層級原因（參數驗證、規則檢查）。 */
export interface FieldError { field: string; message: string; code?: string }

export class ApiError extends Error {
  /** 錯誤碼（backend/api/errors.yaml）；舊後端或非 JSON 回應時為 null。 */
  code: string | null = null;
  /** 同樣的請求稍後重送是否可能成功。 */
  retryable = false;
  /** 回報代碼：給客服查日誌（X-Request-Id）。 */
  requestId: string | null = null;
  fieldErrors: FieldError[] = [];
  params: Record<string, unknown> = {};
  /** 後端的 detail 原文（message 在 5xx 時另外附回報代碼）。 */
  detail: string;
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
    this.detail = message;
    if (body && typeof body === 'object') {
      const b = body as Record<string, unknown>;
      if (typeof b.code === 'string') this.code = b.code;
      this.retryable = b.retryable === true;
      if (typeof b.request_id === 'string') this.requestId = b.request_id;
      if (Array.isArray(b.errors)) this.fieldErrors = b.errors.filter((x): x is FieldError => !!x && typeof x === 'object' && typeof (x as FieldError).message === 'string');
      if (b.params && typeof b.params === 'object') this.params = b.params as Record<string, unknown>;
    }
    if (status >= 500 && this.requestId) this.message = `${message}（回報代碼：${this.requestId}）`;   // 只顯示 message 的頁面也帶得到編號（docs/46 §4.4）
  }
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
    const text = await res.text();
    const data = text ? safeJson(text) : null;
    if (res.status === 401) {
      const e = new ApiError(401, detail(data) ?? '未登入或 session 已逾時', data);
      if (e.code === null || SESSION_GONE.has(e.code)) onUnauthorized(currentPath());   // 驗證碼錯（OTP_INVALID）等不是登出，留在原頁
      throw e;
    }
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

const SESSION_GONE = new Set(['UNAUTHENTICATED', 'TOKEN_INVALID']);

function safeJson(t: string): unknown { try { return JSON.parse(t); } catch { return t; } }
function detail(d: unknown): string | undefined {
  if (d && typeof d === 'object' && 'detail' in d) { const v = (d as { detail: unknown }).detail; return typeof v === 'string' ? v : JSON.stringify(v); }
  return undefined;
}

import { demoFetch } from '@/demo/fetch';
import { isDemo } from '@/demo/mode';

/** 正式：同源 fetch；示範建置（VITE_DEMO=1）：固定資料、不導向登入。 */
export const api = createClient(isDemo ? { fetchImpl: demoFetch, onUnauthorized: () => undefined } : {});
