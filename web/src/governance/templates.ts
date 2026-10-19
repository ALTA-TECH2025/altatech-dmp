// 範本下載／上傳（openapi /v1/meta/templates/{kind}）：型別、欄位鍵（示範模式用來產生範本）、檔案下載。
export type TemplateKind = 'events' | 'properties' | 'user-properties';
export type ImportAction = 'create' | 'update' | 'change_request' | 'unchanged' | 'error';
export interface ImportRow { line: number; key: string; action: ImportAction; changes: string[]; errors: string[] }
export interface ImportResult { kind: TemplateKind; applied: boolean; total: number; summary: Record<ImportAction, number>; rows: ImportRow[] }

export const TEMPLATE_KEYS: Record<TemplateKind, string[]> = {
  events: ['name', 'cn', 'group', 'entity', 'source', 'kind', 'platforms', 'owner', 'properties', 'status', 'note'],
  properties: ['key', 'cn', 'type', 'scope', 'sensitivity', 'source', 'enum', 'unit'],
  'user-properties': ['key', 'cn', 'type', 'values', 'source', 'update', 'sensitivity', 'filterable', 'groupable', 'note'],
};
export const ACTION_CN: Record<ImportAction, string> = { create: '新增', update: '更新', change_request: '變更單', unchanged: '不變', error: '錯誤' };
export const ACTION_TONE: Record<ImportAction, string> = { create: 'blue', update: 'custom', change_request: 'amber', unchanged: 'gray', error: 'red' };   // ds/StatusBadge 的 tone

const cell = (v: unknown): string => { const s = Array.isArray(v) ? v.join('|') : v === undefined || v === null ? '' : String(v); return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
/** 物件列 → CSV（UTF-8 BOM，Excel 直接開）。 */
export function toCsv(keys: string[], rows: Record<string, unknown>[]): string {
  return '﻿' + [keys.join(','), ...rows.map((r) => keys.map((k) => cell(r[k])).join(','))].join('\r\n') + '\r\n';
}

/** 讀第一欄（名稱）：示範模式的預覽只需要知道每列是誰、在第幾行。 */
export function firstColumn(text: string): { line: number; key: string }[] {
  const out: { line: number; key: string }[] = [];
  text.replace(/^﻿/, '').split(/\r?\n/).forEach((l, i) => {
    if (i === 0 || !l.trim()) return;
    const k = (l.startsWith('"') ? l.slice(1, l.indexOf('"', 1)) : l.split(',')[0]).trim();
    if (k && !k.startsWith('#')) out.push({ line: i + 1, key: k });
  });
  return out;
}

export function saveFile(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
