import { describe, expect, it } from 'vitest';
import { numericColumns, paramNames, toCsv } from './sql';

describe('自定義 SQL 純函式', () => {
  it('動態參數去重', () => { expect(paramNames('SELECT 1 WHERE a = {{ev}} AND b = {{ d }} AND c = {{ev}}')).toEqual(['ev', 'd']); });
  it('數值欄推斷（日期字串不算）', () => { expect(numericColumns(['d', 'n', 's'], [{ d: '2026-10-01', n: 3, s: 'x' }, { d: '2026-10-02', n: '4', s: '' }])).toEqual(['n']); });
  it('CSV 逸出與列數上限', () => { const csv = toCsv(['a', 'b'], [{ a: 'x,y', b: 1 }, { a: 'q"r', b: null }, { a: 'z', b: 2 }], 2); expect(csv.split('\n')).toEqual(['a,b', '"x,y",1', '"q""r",']); });
});

import { formatSql, paramPairs, paramType, selectFor } from './sql';
describe('自定義 SQL：參數型別、格式化、生成 SELECT', () => {
  it('參數型別靠命名慣例；from／to 成對', () => {
    expect(paramType('event_date')).toBe('date'); expect(paramType('from')).toBe('date'); expect(paramType('days')).toBe('number'); expect(paramType('sym')).toBe('text');
    expect(paramPairs(['d_from', 'd_to', 'sym'])).toEqual([['d', 'd_from', 'd_to']]); expect(paramPairs(['from', 'to'])).toEqual([['期間', 'from', 'to']]); expect(paramPairs(['from'])).toEqual([]);
  });
  it('格式化：關鍵字大寫、子句換行、欄位逐列，字串與子查詢不動', () => {
    const f = formatSql("select a, count() as n from dmp.events where event = 'x,y' and d >= {{from}} group by a order by n desc limit 10");
    expect(f).toBe("SELECT\n  a,\n  count() AS n\nFROM dmp.events\nWHERE event = 'x,y'\n  AND d >= {{from}}\nGROUP BY a\nORDER BY n DESC\nLIMIT 10");
    expect(formatSql('select n from t where d between {{from}} and {{to}} and x = 1')).toBe('SELECT\n  n\nFROM t\nWHERE d BETWEEN {{from}} AND {{to}}\n  AND x = 1');
    expect(formatSql('select x from (select x, y from t where y > 1) as s')).toBe('SELECT\n  x\nFROM (SELECT x, y FROM t WHERE y > 1) AS s');
    expect(formatSql("select a -- pick a\nfrom t where x = '--not a comment' /* block\n comment */ and y = 1")).toBe("SELECT\n  a -- pick a\nFROM t\nWHERE x = '--not a comment' /* block\n comment */\n  AND y = 1");   // 行註解不吞掉後面的子句；字串裡的 -- 與區塊註解原樣保留
    expect(formatSql('select a from t -- trailing')).toBe('SELECT\n  a\nFROM t -- trailing');
  });
  it('勾選欄位生成 SELECT', () => {
    expect(selectFor('events', [{ name: 'event', type: 'String' }, { name: 'platform', type: 'String' }], true)).toBe('SELECT\n  event,\n  platform\nFROM dmp.events\nWHERE event_date BETWEEN {{from}} AND {{to}}\nLIMIT 500');
    expect(selectFor('dmp.users', [], false)).toBe('SELECT\n  *\nFROM dmp.users\nLIMIT 500');
  });
});
