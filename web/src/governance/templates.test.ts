import { describe, expect, it } from 'vitest';
import { firstColumn, toCsv } from './templates';

describe('範本 CSV', () => {
  it('BOM、逗號與引號跳脫、陣列以 | 連接', () => {
    expect(toCsv(['key', 'cn', 'enum'], [{ key: 'a', cn: '甲,乙 "丙"', enum: ['x', 'y'] }])).toBe('﻿key,cn,enum\r\na,"甲,乙 ""丙""",x|y\r\n');
  });
  it('第一欄：略過表頭、空行、# 開頭；行號從 1 起算', () => {
    expect(firstColumn('﻿name,cn\r\n#說明,x\r\n\r\n"ev,1",y\r\nev2,z')).toEqual([{ line: 4, key: 'ev,1' }, { line: 5, key: 'ev2' }]);
  });
});
