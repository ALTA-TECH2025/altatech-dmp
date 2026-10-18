import { describe, expect, it } from 'vitest';
import type { Action, Module } from '@/api/types';
import { actionsFor, parseIdList, updateText } from './segments';

const grants = (g: Partial<Record<Module, Action[]>>) => (m: Module, a: Action) => (g[m] ?? []).includes(a);
describe('segment actions', () => {
  it('data analyst on active segment: edit/compute/pause/copy/recycle; no approve', () => {
    expect(actionsFor({ status: 'active', owner: 'u1' }, grants({ segments: ['view', 'use'] }), 'u1')).toEqual(['edit', 'compute', 'pause', 'copy', 'recycle']);
  });
  it('approver cannot approve own segment unless admin', () => {
    const ops = grants({ segments: ['view', 'use', 'approve'] });
    expect(actionsFor({ status: 'pending_approval', owner: 'me' }, ops, 'me')).not.toContain('approve');
    expect(actionsFor({ status: 'pending_approval', owner: 'other' }, ops, 'me')).toContain('approve');
    expect(actionsFor({ status: 'pending_approval', owner: 'me' }, grants({ segments: ['view', 'use', 'approve', 'admin'] }), 'me')).toContain('approve');
  });
  it('recycled: restore for use, purge only admin; viewer gets nothing', () => {
    expect(actionsFor({ status: 'recycled', owner: 'x' }, grants({ segments: ['view', 'use'] }))).toEqual(['restore']);
    expect(actionsFor({ status: 'recycled', owner: 'x' }, grants({ segments: ['view', 'use', 'admin'] }))).toEqual(['restore', 'purge']);
    expect(actionsFor({ status: 'active', owner: 'x' }, grants({ segments: ['view'] }))).toEqual([]);
  });
  it('updateText', () => {
    expect(updateText({ mode: 'manual', freq: 'daily', earliest: '06:00', keep: 1 }, 'Asia/Taipei')).toBe('手動（1 版）');
    expect(updateText({ mode: 'routine', freq: 'weekly', earliest: '07:00', keep: 4 }, 'Asia/Taipei')).toBe('例行 每週一 07:00（Asia/Taipei）· 留 4 版');
  });
});

describe('parseIdList', () => {
  it('takes first column, drops header, blanks and quotes', () => {
    expect(parseIdList('客戶代碼,姓名\n"A123",x\n\n B456 \nC789\t2\n')).toEqual(['A123', 'B456', 'C789']);
    expect(parseIdList('one_id\r\nv1_abc\r\n')).toEqual(['v1_abc']);
    expect(parseIdList('A1\nA1')).toEqual(['A1', 'A1']);              // 去重交給後端（回去重後數）
  });
});
