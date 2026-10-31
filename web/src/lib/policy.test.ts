import { describe, expect, it } from 'vitest';
import { can, copyAsCustom, display, parsePolicy, toggleAction, unionGrants, visibleModules } from './policy';
import type { Action, Me } from '@/api/types';

const me = (grants: Me['grants'], masking: Me['masking'] = {}): Me => ({
  user: { id: 'u', username: 'u', name: 'U', email: '', provider: 'entra' },
  roles: [{ key: 'marketing', cn: '行銷', active: true }], grants, masking,
  scope: { kind: 'global', k_anonymity: 20, lookback_days: null },
  session: { idp_mode: 'broker', amr: ['pwd', 'mfa'], auth_time: 0, step_up_needed: false, csrf_header: 'X-CSRF-Token' }, levels: ['L0', 'L1', 'L2', 'L3', 'L4'],
});

describe('can（與 rbac 的蘊含規則一致）', () => {
  it('use 蘊含 view；export 蘊含 use/view；沒有模組就全部 false', () => {
    const m = me({ events: ['use'], users: ['export'] });
    expect(can(m, 'events', 'view')).toBe(true);
    expect(can(m, 'events', 'export')).toBe(false);
    expect(can(m, 'users', 'use')).toBe(true);
    expect(can(m, 'iam', 'view')).toBe(false);
    expect(can(null, 'events', 'view')).toBe(false);
  });
  it('visibleModules 只列有 view/use 的模組', () => {
    expect(visibleModules(me({ events: ['view'], profile: ['use'], ai_admin: ['admin'] }))).toEqual(['events', 'profile', 'ai_admin']);
  });
});

describe('遮罩策略（與 backend/semantic/compiler/policy.py 同口徑）', () => {
  it('parsePolicy', () => {
    expect(parsePolicy('bucket([0,1e5,5e5,1e6,5e6])')).toEqual({ kind: 'bucket', arg: [0, 1e5, 5e5, 1e6, 5e6] });
    expect(parsePolicy('mask_tail(4)')).toEqual({ kind: 'mask_tail', arg: 4 });
    expect(parsePolicy('round(1000)')).toEqual({ kind: 'round', arg: 1000 });
    expect(parsePolicy('plain')).toEqual({ kind: 'plain' });
  });
  it('display', () => {
    const m = me({}, { amount: { level: 'L2', policy: 'bucket([0,1e5,5e5,1e6,5e6])' }, one_id: { level: 'L3', policy: 'mask_tail(4)' }, fee: { level: 'L2', policy: 'round(1000)' }, x: { level: 'L3', policy: 'hide' } });
    expect(display(m, 'amount', 250000)).toBe('100000–500000');
    expect(display(m, 'amount', 9e6)).toBe('≥5000000');
    expect(display(m, 'amount', -1)).toBe('<0');
    expect(display(m, 'one_id', 'v1_abcdef12')).toBe('v1_abcd****');
    expect(display(m, 'fee', 1499)).toBe('1000');
    expect(display(m, 'x', 'v')).toBe('（不可見）');
    expect(display(m, 'platform', 'iOS')).toBe('iOS');
    expect(display(m, 'amount', null)).toBe('—');
  });
});

describe('角色矩陣編輯', () => {
  it('toggleAction 蘊含與取消', () => {
    expect(toggleAction([], 'export', true)).toEqual(['view', 'use', 'export']);
    expect(toggleAction(['view', 'use', 'export'], 'use', false)).toEqual(['view']);
    expect(toggleAction(['view', 'use', 'approve'], 'view', false)).toEqual([]);
    expect(toggleAction(['view'], 'admin', true)).toEqual(['view', 'admin']);
  });
  it('unionGrants 多角色聯集且順序固定', () => {
    expect(unionGrants([{ events: ['view', 'use'] }, { events: ['export'], iam: ['view'] }])).toEqual({ events: ['view', 'use', 'export'], iam: ['view'] });
  });
  it('copyAsCustom：內建角色複製成可改的自訂角色（沒有 key、名稱加副本、權限照抄且不共用參照）', () => {
    const r = { key: 'data_analyst', name: '數據分析', scope: 'global' as const, lookback_days: 0, grants: { events: ['view', 'use'] as Action[] }, builtin: true };
    const c = copyAsCustom(r);
    expect(c).toEqual({ ...r, key: undefined, name: '數據分析（副本）', builtin: false });
    c.grants.events!.push('export');
    expect(r.grants.events).toEqual(['view', 'use']);
  });
});
