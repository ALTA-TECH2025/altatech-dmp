import { describe, expect, it } from 'vitest';
import type { Catalog } from '@/analytics/types';
import { cleanRule, condValue, eventFields, normalizeRule, pruneGroup, ruleValid, summarizeRule, toGroup, userFields } from './rule';

const cat: Catalog = {
  events: [{ name: 'bond_deal', cn: '成交', group: '', status: 'live' }, { name: 'app_login_success', cn: '登入', group: '', status: 'live' }],
  dimensions: [{ name: 'platform', cn: '平台', type: 'string', values: ['iOS'], sensitivity: 'L1' }],
  event_columns: [{ name: 'platform', cn: '平台' }, { name: 'amount', cn: '金額', type: 'number' }],
  user_props: [{ key: 'tier', cn: '價值分級', type: 'enum', values: ['P1', 'P2'], filterable: true, groupable: true }, { key: 'churn_score', cn: '流失分', type: 'number', values: [], filterable: true, groupable: true }, { key: 'last_active_at', cn: '最近活躍', type: 'datetime', values: [], filterable: true, groupable: false }],
  aggs: [], ops: { string: ['eq', 'in', 'set'], number: ['gte', 'between'], bool: ['true', 'false'], date: ['rel', 'between'], segment: ['in', 'nin'] },
  funnel: { window_units: [], max_window_days: 90, max_steps: 10, orders: [], metrics: [] },
};

describe('rule helpers', () => {
  it('userFields maps types and segments; eventFields dedupes platform', () => {
    const uf = userFields(cat, [{ id: 'sg1', name: 'VIP' }]);
    expect(uf.map((f) => f.v)).toEqual(['user.tier', 'user.churn_score', 'user.last_active_at', 'seg.sg1']);
    expect(uf[0].values).toEqual(['P1', 'P2']); expect(uf[2].type).toBe('date');
    expect(eventFields(cat).map((f) => f.v)).toEqual(['platform', 'amount']);
  });
  it('condValue converts numbers, lists and rel', () => {
    expect(condValue('number', 'gte', '70')).toBe(70);
    expect(condValue('string', 'in', 'P1, P2')).toEqual(['P1', 'P2']);
    expect(condValue('number', 'between', '1,3')).toEqual([1, 3]);
    expect(condValue('date', 'rel', '7')).toEqual({ days: 7 });
  });
  it('toGroup／pruneGroup：平鋪陣列視為且；空群組拿掉', () => {
    expect(toGroup([{ field: 'user.tier', op: 'eq', value: 'P1' }])).toEqual({ op: 'and', items: [{ field: 'user.tier', op: 'eq', value: 'P1' }] });
    expect(pruneGroup({ op: 'or', items: [{ op: 'and', items: [] }, { field: 'user.tier', op: 'eq', value: 'P1' }] })).toEqual({ op: 'or', items: [{ field: 'user.tier', op: 'eq', value: 'P1' }] });
    expect(pruneGroup({ op: 'and', items: [{ op: 'or', items: [] }] })).toBeUndefined();
  });
  it('ruleValid：要有條件；條件要有值；行為要有事件、期間、數值指標欄位', () => {
    expect(ruleValid({ filters: { op: 'and', items: [] }, behaviors: [] })).toBe(false);
    expect(ruleValid({ filters: [{ field: 'user.tier', op: 'in', value: [] }] })).toBe(false);
    expect(ruleValid({ filters: { op: 'or', items: [{ field: 'user.tier', op: 'in', value: ['P1'] }, { op: 'and', items: [{ field: 'user.churn_score', op: 'gte', value: 70 }] }] } })).toBe(true);
    expect(ruleValid({ behaviors: [{ event: '', time: { last_days: 7 } }] })).toBe(false);
    expect(ruleValid({ behaviors: [{ event: 'bond_deal', time: { last_days: 0 } }] })).toBe(false);
    expect(ruleValid({ behaviors: [{ event: 'bond_deal', measure: { agg: 'sum' }, cmp: { op: 'gte', value: 1 }, time: { last_days: 7 } }] })).toBe(false);
    expect(ruleValid({ behaviors: [{ event: 'bond_deal', measure: { agg: 'sum', field: 'amount' }, cmp: { op: 'gte', value: 500000 }, time: { last_days: 7 } }] })).toBe(true);
    expect(ruleValid({ behaviors: [{ event: 'bond_deal', time: { from: '2026-10-01', to: '2026-10-16' }, done: false }] })).toBe(true);
    expect(ruleValid({ source: { type: 'funnel', query: {}, step: 2 } })).toBe(true);
  });
  it('cleanRule：空群組不送、未做過不帶指標、舊 count 轉 cmp、只有一邊時不帶 combine', () => {
    const c = cleanRule({ filters: { op: 'and', items: [] }, combine: 'or', behaviors: [{ event: 'bond_deal', done: false, measure: { agg: 'count' }, cmp: { op: 'gte', value: 1 }, filters: { op: 'or', items: [] }, time: { last_days: 7 } }, { event: 'bond_deal', count: { op: 'gte', value: 3 }, time: { last_days: 30 } }] });
    expect(c.filters).toBeUndefined(); expect(c.combine).toBeUndefined();
    expect(c.behaviors?.[0]).toEqual({ event: 'bond_deal', done: false, filters: undefined, time: { last_days: 7 } });
    expect(c.behaviors?.[1]).toEqual({ event: 'bond_deal', filters: undefined, cmp: { op: 'gte', value: 3 }, time: { last_days: 30 } });
    expect(cleanRule({ filters: [{ field: 'user.tier', op: 'eq', value: 'P1' }], behaviors: [] }).behaviors).toBeUndefined();
  });
  it('normalizeRule：舊規則讀進編輯器', () => {
    const n = normalizeRule({ filters: [{ field: 'user.tier', op: 'eq', value: 'P1' }], behaviors: [{ event: 'bond_deal', count: { op: 'gte', value: 3 }, time: { from: '2026-10-01', to: '2026-10-16' } }] });
    expect(n.filters).toEqual({ op: 'and', items: [{ field: 'user.tier', op: 'eq', value: 'P1' }] });
    expect(n.behaviors?.[0]).toEqual({ event: 'bond_deal', measure: { agg: 'count' }, cmp: { op: 'gte', value: 3 }, filters: { op: 'and', items: [] }, time: { from: '2026-10-01', to: '2026-10-16' } });
    expect(n.combine).toBe('and');
  });
  it('summarizeRule reads like Chinese', () => {
    const s = summarizeRule({ filters: [{ field: 'user.tier', op: 'in', value: ['P1', 'P2'] }, { field: 'user.last_active_at', op: 'rel', value: { days: 7 } }], behaviors_op: 'or',
      behaviors: [{ event: 'bond_deal', count: { op: 'gte', value: 3 }, time: { from: '2026-10-01', to: '2026-10-16' }, filters: [{ field: 'amount', op: 'gte', value: 100000 }] }, { event: 'app_login_success', done: false, time: { from: '2026-10-10', to: '2026-10-16' } }],
      source: { type: 'funnel', query: {}, step: 2, reached: false, bucket: '2026-10-12' } }, cat);
    expect(s).toBe('價值分級 包含於 P1,P2 且 最近活躍 最近 7 天內 且 2026-10-01～2026-10-16 做過 成交（金額 ≥ 100000） ≥ 3 次 或 2026-10-10～2026-10-16 未做過 登入 且 來源：漏斗第 2 步流失（2026-10-12）');
    const t = summarizeRule({ combine: 'or', filters: { op: 'or', items: [{ field: 'user.tier', op: 'eq', value: 'P1' }, { op: 'and', items: [{ field: 'user.churn_score', op: 'gte', value: 70 }, { field: 'user.tier', op: 'eq', value: 'P2' }] }] },
      behaviors: [{ event: 'bond_deal', measure: { agg: 'sum', field: 'amount' }, cmp: { op: 'gte', value: 500000 }, time: { last_days: 16 } }, { event: 'app_login_success', measure: { agg: 'days' }, cmp: { op: 'between', value: [2, 5] }, time: { last_days: 7 } }] }, cat);
    expect(t).toBe('價值分級 等於 P1 或 （流失分 ≥ 70 且 價值分級 等於 P2） 或 （近 16 天 做過 成交 金額合計 ≥ 500000 且 近 7 天 做過 登入 活躍天數 2～5 天）');
    expect(summarizeRule({ segment: 'sg1' }, cat, [{ id: 'sg1', name: 'VIP' }])).toBe('屬於VIP');
    expect(summarizeRule({}, cat)).toBe('（沒有條件）');
  });
  it('sequences: valid / clean / summary', () => {
    const q = { sequences: [{ steps: [{ event: 'app_login_success' }, { event: 'bond_deal', filters: { op: 'and' as const, items: [{ field: 'amount', op: 'gte', value: 100000 }] } }], window: { value: 2, unit: 'hour' as const }, time: { last_days: 7 } }] };
    expect(ruleValid(q)).toBe(true);
    expect(ruleValid({ sequences: [{ ...q.sequences[0], steps: [{ event: 'app_login_success' }] }] })).toBe(false);          // 少於 2 步
    expect(ruleValid({ sequences: [{ ...q.sequences[0], window: { value: 91, unit: 'day' as const } }] })).toBe(false);       // 超過 90 天
    expect(cleanRule({ ...q, filters: { op: 'and', items: [] } }).sequences?.[0].steps[0]).toEqual({ event: 'app_login_success', filters: undefined });
    expect(summarizeRule(q, cat)).toBe('近 7 天 依序完成 登入 → 成交（金額 ≥ 100000）（2 小時內）');
    expect(summarizeRule({ sequences: [{ ...q.sequences[0], done: false }] }, cat)).toContain('未依序完成');
    expect(summarizeRule({ source: { type: 'import', import_id: 'im_0123456789' } }, cat)).toBe('來源：導入名單 im_0123456789');
  });
});
