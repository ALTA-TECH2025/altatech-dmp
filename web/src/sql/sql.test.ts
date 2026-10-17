import { describe, expect, it } from 'vitest';
import { numericColumns, paramNames, toCsv } from './sql';

describe('自定義 SQL 純函式', () => {
  it('動態參數去重', () => { expect(paramNames('SELECT 1 WHERE a = {{ev}} AND b = {{ d }} AND c = {{ev}}')).toEqual(['ev', 'd']); });
  it('數值欄推斷（日期字串不算）', () => { expect(numericColumns(['d', 'n', 's'], [{ d: '2026-10-01', n: 3, s: 'x' }, { d: '2026-10-02', n: '4', s: '' }])).toEqual(['n']); });
  it('CSV 逸出與列數上限', () => { const csv = toCsv(['a', 'b'], [{ a: 'x,y', b: 1 }, { a: 'q"r', b: null }, { a: 'z', b: 2 }], 2); expect(csv.split('\n')).toEqual(['a,b', '"x,y",1', '"q""r",']); });
});
