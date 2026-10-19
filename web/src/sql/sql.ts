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

// ---- 動態參數型別（命名慣例，後端語法不變仍是 {{name}}）----
export type ParamType = 'date' | 'number' | 'text';
/** 名稱像日期（date／day／from／to／at 結尾）→ 日期；像數量（days／limit／n／count／top）→ 數字；其他文字。 */
export function paramType(name: string): ParamType {
  const n = name.toLowerCase();
  if (/(^|_)(date|day|from|to|at|start|end)$/.test(n) || /date/.test(n)) return 'date';
  if (/(^|_)(days|limit|n|count|top|k|min|max|num)$/.test(n)) return 'number';
  return 'text';
}
/** 成對的日期參數（x_from + x_to，或 from + to）→ 一個日期區間選擇器；回 [前綴, from 名, to 名]。 */
export function paramPairs(names: string[]): [string, string, string][] {
  const out: [string, string, string][] = [];
  for (const a of names) {
    const m = /^(.*?)(_?)from$/i.exec(a); if (!m) continue;
    const b = names.find((x) => x.toLowerCase() === `${m[1]}${m[2]}to`.toLowerCase());
    if (b) out.push([m[1].replace(/_$/, '') || '期間', a, b]);
  }
  return out;
}

// ---- 格式化：關鍵字大寫、主要子句換行、SELECT 欄位逐列縮排（只處理最外層，子查詢保留原樣縮排）----
const CLAUSES = ['WITH', 'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'UNION ALL', 'UNION', 'LEFT JOIN', 'INNER JOIN', 'RIGHT JOIN', 'FULL JOIN', 'CROSS JOIN', 'JOIN', 'ON', 'AND', 'OR', 'SETTINGS'];
const KEYWORDS = [...CLAUSES, 'AS', 'IN', 'NOT', 'IS', 'NULL', 'BETWEEN', 'LIKE', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'DISTINCT', 'DESC', 'ASC', 'ARRAY JOIN', 'PREWHERE', 'SAMPLE'];
export function formatSql(sql: string): string {
  const strs: string[] = [];
  let s = sql.replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`[^`]*`|\{\{[^}]*\}\}/g, (m) => { strs.push(m); return `\u0000${strs.length - 1}\u0000`; });   // 字串／識別字／動態參數先收起來，不動內容
  s = s.replace(/\s+/g, ' ').trim();
  for (const k of KEYWORDS) s = s.replace(new RegExp(`\\b${k.replace(' ', '\\s+')}\\b`, 'gi'), k);
  let depth = 0; let out = ''; let i = 0; let between = false;                  // BETWEEN a AND b 的 AND 不換行
  const atTop = () => depth === 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === '(') { depth++; out += ch; i++; continue; }
    if (ch === ')') { depth--; out += ch; i++; continue; }
    const rest = s.slice(i);
    if (atTop() && /^BETWEEN\b/.test(rest)) between = true;
    const clause = atTop() ? CLAUSES.find((k) => new RegExp(`^${k.replace(' ', '\\s+')}\\b`).test(rest)) : undefined;
    if (clause === 'AND' && between) { between = false; out += 'AND '; i += 3; while (s[i] === ' ') i++; continue; }
    if (clause && !(clause === 'AND' || clause === 'OR' || clause === 'ON' ? !/\n(WHERE|HAVING|ON|AND|OR|[A-Z ]*JOIN)\b[^\n]*$/.test(out) : false)) {
      const indent = clause === 'AND' || clause === 'OR' || clause === 'ON' ? '  ' : '';
      out = out.replace(/ +$/, '') + (out ? '\n' : '') + indent + clause + ' ';
      i += clause.length; while (s[i] === ' ') i++;
      continue;
    }
    if (ch === ',' && atTop() && /\nSELECT [^\n]*$|^SELECT [^\n]*$/.test(out)) { out += ',\n  '; i++; while (s[i] === ' ') i++; continue; }
    out += ch; i++;
  }
  out = out.replace(/^SELECT (?=\S)/m, 'SELECT\n  ').replace(/\nSELECT (?=\S)/g, '\nSELECT\n  ');
  return out.replace(/\u0000(\d+)\u0000/g, (_, n) => strs[Number(n)]).replace(/ +\n/g, '\n').trim();
}

// ---- 勾選欄位 → SELECT ----
/** 勾的欄位生成查詢：有 event_date 的表預設帶期間參數與 LIMIT 500；一個維度欄 + 數值欄則順手 GROUP BY。 */
export function selectFor(table: string, columns: { name: string; type: string }[], hasDate: boolean): string {
  const cols = columns.map((c) => c.name);
  const t = table.includes('.') ? table : `dmp.${table}`;
  const where = hasDate ? '\nWHERE event_date BETWEEN {{from}} AND {{to}}' : '';
  return `SELECT\n  ${(cols.length ? cols : ['*']).join(',\n  ')}\nFROM ${t}${where}\nLIMIT 500`;
}
