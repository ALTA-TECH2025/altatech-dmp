// 自定義 SQL 純函式：動態參數偵測、結果欄位推斷（哪些欄可當 X／Y）、CSV 轉換（複製前 100 列）
export function paramNames(sql: string): string[] {
  const out: string[] = []; const re = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g; let m: RegExpExecArray | null;
  while ((m = re.exec(sql))) if (!out.includes(m[1])) out.push(m[1]);
  return out;
}
export function numericColumns(columns: string[], rows: Record<string, unknown>[]): string[] {
  return columns.filter((c) => rows.some((r) => typeof r[c] === 'number' || (typeof r[c] === 'string' && r[c] !== '' && Number.isFinite(Number(r[c])) && !/^\d{4}-\d{2}-\d{2}/.test(String(r[c])))));
}
export function toCsv(columns: string[], rows: Record<string, unknown>[], limit = 100): string {
  const esc = (v: unknown) => { const s = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [columns.map(esc).join(','), ...rows.slice(0, limit).map((r) => columns.map((c) => esc(r[c])).join(','))].join('\n');
}
export const fmtTs = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString('zh-TW', { hour12: false }) : '—');
