import { describe, expect, it } from 'vitest';
import { dedupe } from './FunnelPage';

describe('漏斗頁目錄合併', () => {
  it('事件欄位與維度同名時只留第一個', () => {
    expect(dedupe([{ v: 'platform', cn: '平台' }, { v: 'symbol', cn: '商品' }, { v: 'platform', cn: '平台（維度）' }]).map((x) => x.cn)).toEqual(['平台', '商品']);
  });
});
