import { describe, expect, it } from 'vitest';
import { checkEvent, dailyText, dimRefs, parseDictCsv, pct } from './governance';

describe('governance helpers', () => {
  it('上線檢查', () => {
    expect(checkEvent({ name: 'Fund', cn: '', owner: '', properties: [] }, new Set(), true)).toHaveLength(3);
    expect(checkEvent({ name: 'fund_redeem', cn: '贖回', owner: '財富', properties: [{ key: 'mobile', sensitivity: 'L4' }] }, new Set(), true)).toEqual(['含 L4 屬性：個資不得入倉，請移除或改為雜湊／分桶後的 L3／L2 屬性']);
    expect(checkEvent({ name: 'bond_order', cn: 'x', owner: 'y', properties: [] }, new Set(['bond_order']), true)).toEqual(['事件名稱已存在']);
    expect(checkEvent({ name: '$pageview', cn: 'x', owner: 'y', properties: [{ key: 'cuadm' }] }, new Set(), false, new Set(['cuadm']))).toHaveLength(1);
  });
  it('數字文字', () => {
    expect(dailyText(null)).toBe('—'); expect(dailyText(123456)).toBe('12.3 萬'); expect(dailyText(234)).toBe('234'); expect(dailyText(3.4)).toBe('3.4');
    expect(pct(0.9464)).toBe('94.6%'); expect(pct(null)).toBe('—');
  });
  it('字典 CSV 與維度表引用', () => {
    expect(parseDictCsv('# 註解\nE001,餘額不足\nSYS02,閘道逾時, 含逗號\n\nbad')).toEqual({ E001: '餘額不足', SYS02: '閘道逾時, 含逗號' });
    expect(dimRefs("dictGet('dim_instrument', 'industry', symbol) + dim_branch.region")).toEqual(['dim_branch', 'dim_instrument']);
  });
});
