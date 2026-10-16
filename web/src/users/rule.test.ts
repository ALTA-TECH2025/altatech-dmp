import { describe, expect, it } from 'vitest';
import type { Catalog } from '@/analytics/types';
import { condValue, eventFields, ruleValid, summarizeRule, userFields } from './rule';

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
  it('ruleValid requires a condition, event and time', () => {
    expect(ruleValid({ filters: [], behaviors: [] })).toBe(false);
    expect(ruleValid({ filters: [{ field: 'user.tier', op: 'in', value: [] }] })).toBe(false);
    expect(ruleValid({ filters: [{ field: 'user.tier', op: 'in', value: ['P1'] }] })).toBe(true);
    expect(ruleValid({ behaviors: [{ event: '', time: { from: 'a', to: 'b' } }] })).toBe(false);
    expect(ruleValid({ behaviors: [{ event: 'bond_deal', time: { from: '2026-10-01', to: '2026-10-16' }, done: false }] })).toBe(true);
    expect(ruleValid({ source: { type: 'funnel', query: {}, step: 2 } })).toBe(true);
  });
  it('summarizeRule reads like Chinese', () => {
    const s = summarizeRule({ filters: [{ field: 'user.tier', op: 'in', value: ['P1', 'P2'] }, { field: 'user.last_active_at', op: 'rel', value: { days: 7 } }], behaviors_op: 'or',
      behaviors: [{ event: 'bond_deal', count: { op: 'gte', value: 3 }, time: { from: '2026-10-01', to: '2026-10-16' }, filters: [{ field: 'amount', op: 'gte', value: 100000 }] }, { event: 'app_login_success', done: false, time: { from: '2026-10-10', to: '2026-10-16' } }],
      source: { type: 'funnel', query: {}, step: 2, reached: false, bucket: '2026-10-12' } }, cat);
    expect(s).toBe('價值分級 包含於 P1,P2 且 最近活躍 最近 7 天內 且 2026-10-01～2026-10-16 做過 成交 ≥ 3 次（金額 ≥ 100000） 或 2026-10-10～2026-10-16 未做過 登入 且 來源：漏斗第 2 步流失（2026-10-12）');
    expect(summarizeRule({ segment: 'sg1' }, cat, [{ id: 'sg1', name: 'VIP' }])).toBe('屬於VIP');
    expect(summarizeRule({}, cat)).toBe('（沒有條件）');
  });
});
