import { describe, expect, it } from 'vitest';
import { exportNext, expiresText } from './approvals';

describe('approvals helpers', () => {
  it('exportNext', () => {
    expect(exportNext({ status: 'approved' })).toBe('run'); expect(exportNext({ status: 'generated' })).toBe('download');
    expect(exportNext({ status: 'pending_approval' })).toBe('wait'); expect(exportNext({ status: 'rejected' })).toBe('none');
  });
  it('expiresText', () => {
    const now = new Date('2026-10-16T09:00:00+08:00');
    expect(expiresText(null, now)).toBeNull(); expect(expiresText('2026-10-16T08:00:00+08:00', now)).toBe('已到期');
    expect(expiresText('2026-10-16T12:30:00+08:00', now)).toBe('3 小時後到期'); expect(expiresText('2026-10-23T09:00:00+08:00', now)).toBe('7 天後到期');
  });
});
