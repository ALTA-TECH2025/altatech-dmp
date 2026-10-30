// 錯誤畫面的共用判斷（docs/46 §4.6）：依 ApiError.code 決定要不要給「重試」「重新整理」，回報代碼一律顯示。
import { ApiError, type FieldError } from './client';

export interface ErrorView {
  text: string;
  /** X-Request-Id；使用者截圖或複製給客服。 */
  reportCode: string | null;
  /** retryable=true 的錯誤（逾時、限流、下游暫時失敗）給「重試」。 */
  retry: boolean;
  /** CSRF_INVALID：頁面過期，給「重新整理」。 */
  reload: boolean;
  /** STEP_UP_REQUIRED：提示先在頂欄完成再次驗證。 */
  stepUp: boolean;
  fields: FieldError[];
}

export function errorView(e: unknown, fallback: string): ErrorView {
  if (!(e instanceof ApiError)) return { text: fallback, reportCode: null, retry: false, reload: false, stepUp: false, fields: [] };
  return { text: e.detail || fallback, reportCode: e.requestId, retry: e.retryable, reload: e.code === 'CSRF_INVALID', stepUp: e.code === 'STEP_UP_REQUIRED', fields: e.fieldErrors };
}

/** 欄位標紅用：取某欄位（或其子路徑，例 body.steps[2].event）的第一條錯誤。 */
export function fieldError(e: unknown, field: string): string | undefined {
  if (!(e instanceof ApiError)) return undefined;
  const hit = e.fieldErrors.find((x) => x.field === field || x.field.endsWith(`.${field}`) || x.field.startsWith(`${field}.`) || x.field.startsWith(`${field}[`));
  return hit?.message;
}
