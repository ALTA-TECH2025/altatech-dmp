import { describe, expect, it } from 'vitest';
import { dailySeries, hbars, ruleText, sortAnomalies } from './stats';

describe('audit stats helpers', () => {
  it('daily → series（缺值補 0，依日期順序）', () => {
    const s = dailySeries({ daily: [{ date: '2026-10-01', logins: 3, login_failed: 0, denied: 1, profile_views: 2, exports: 0, queries: 9, events: 15 }, { date: '2026-10-02', logins: 1, login_failed: 2, denied: 0, profile_views: 0, exports: 1, queries: 4, events: 8 }] }, ['logins', 'denied']);
    expect(s.map((x) => x.label)).toEqual(['登入', '權限拒絕']);
    expect(s[0].points.map((p) => p.y)).toEqual([3, 1]);
    expect(s[1].points[0]).toEqual({ x: '2026-10-01', y: 1, suppressed: false });
  });
  it('hbars 以最大值為 100%，空列表不除以零', () => {
    expect(hbars([{ label: 'events', n: 40 }, { label: 'profile', n: 10 }]).map((r) => r.pct)).toEqual([100, 25]);
    expect(hbars([])).toEqual([]);
  });
  it('異常依嚴重度再依時間新到舊', () => {
    const out = sortAnomalies([{ severity: 'low', bucket: '2026-10-02' }, { severity: 'high', bucket: '2026-10-01' }, { severity: 'high', bucket: '2026-10-03' }]);
    expect(out.map((a) => `${a.severity}:${a.bucket}`)).toEqual(['high:2026-10-03', 'high:2026-10-01', 'low:2026-10-02']);
  });
  it('規則文字', () => {
    expect(ruleText({ key: 'a', cn: '非上班時間細查', event: 'profile.viewed', window: 'day', per: 'user', threshold: 3, off_hours_only: true })).toBe('非上班時間細查：非上班時間 1 日內 > 3／人');
    expect(ruleText({ key: 'b', cn: '單次匯出列數', event: 'export.generated', field: 'rows', threshold: 50000 })).toBe('單次匯出列數：單筆 rows > 50,000');
    expect(ruleText({ key: 'c', cn: '同帳號多 IP', event: 'auth.login.success', window: 'day', per: 'user', distinct: 'ip_masked', threshold: 2 })).toBe('同帳號多 IP：1 日內 ip_masked ≥ 2');
  });
});
