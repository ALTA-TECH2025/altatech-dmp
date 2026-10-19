import { describe, expect, it } from 'vitest';
import { contentText, defaultTask, destAllowed, fileExample, fmtBytes, groupEvents, needsReapproval, runText, validateTask, type OutputRun } from './output';

const sftp = { type: 'sftp' as const, status: 'active' as const };

describe('validateTask', () => {
  it('預設值只缺名稱、用途、目的地', () => {
    const t = defaultTask(new Date('2026-10-17T00:00:00Z'));
    expect(t.content.time).toEqual({ from: '2026-10-10', to: '2026-10-16' });
    expect(validateTask(t)).toEqual(['名稱需 1–50 字', '用途需 4–200 字', '請選目的地']);
    expect(validateTask({ ...t, name: '成交明細', purpose: '交付數據中台對帳', destination_id: 'd1' }, sftp)).toEqual([]);
  });
  it('實時只接事件明細與 kafka／webhook；批量只接 sftp／s3', () => {
    const t = { ...defaultTask(), name: 'x', purpose: '用途四個字', destination_id: 'd1' };
    expect(validateTask({ ...t, type: 'realtime', content: { kind: 'users', columns: ['tier'] } }, sftp)).toEqual(['實時任務只能送 Kafka／Webhook 目的地', '實時任務只接事件明細']);
    expect(validateTask({ ...t, type: 'realtime' }, { type: 'kafka', status: 'active' })).toEqual([]);
    expect(destAllowed('once', { type: 'webhook', status: 'active' })).toBe(false);
    expect(destAllowed('once', { type: 'sftp', status: 'paused' })).toBe(false);
  });
  it('一次性要期間、每日要時間；分群名單要分群；用戶寬表要欄位', () => {
    const t = { ...defaultTask(), name: 'x', purpose: '用途四個字', destination_id: 'd1' };
    expect(validateTask({ ...t, content: { ...t.content, time: { from: '2026-10-16', to: '2026-10-01' } } }, sftp)).toEqual(['期間結束日早於開始日']);
    expect(validateTask({ ...t, type: 'daily', schedule: { at: '2am' } }, sftp)).toEqual(['每日任務需要執行時間（HH:MM）']);
    expect(validateTask({ ...t, content: { kind: 'segment_members', time: t.content.time } }, sftp)).toEqual(['分群名單需選分群']);
    expect(validateTask({ ...t, content: { kind: 'users', columns: [], time: t.content.time } }, sftp)).toEqual(['用戶寬表至少選一個欄位']);
    expect(validateTask({ ...t, format: { ...t.format, max_rows_per_file: 5 } }, sftp)).toEqual(['每檔列數需在 1 萬–500 萬']);
  });
});

describe('顯示', () => {
  it('檔名樣板示例含壓縮副檔名與分區日', () => {
    const f = defaultTask().format;
    expect(fileExample(f)).toBe('ot_1a2b3c/2026-10-16/ot_1a2b3c_2026-10-16_0001.csv.gz');
    expect(fileExample({ ...f, type: 'jsonl', compression: 'none', partition: 'none' })).toBe('ot_1a2b3c/all/ot_1a2b3c_all_0001.jsonl');
  });
  it('位元組與執行紀錄一句話', () => {
    expect(fmtBytes(880)).toBe('880 B'); expect(fmtBytes(2_300_000)).toBe('2.2 MB'); expect(fmtBytes(null)).toBe('—');
    const r: OutputRun = { id: 'or1', task_id: 'ot1', trigger: 'schedule', partition_date: '2026-10-16', started_at: '2026-10-17T02:00:05+08:00', finished_at: null, status: 'ok', rows: 81254, bytes: 1, files: [{ path: 'a.csv.gz', rows: 81254, bytes: 1, checksum: 'x' }, { path: 'ot1/manifest.json', rows: 0, bytes: 1, checksum: 'y' }], delivery_ids: [], error: null, attempts: 1, watermark: 'w' };
    expect(runText(r)).toBe('2026-10-17 02:00 · 2026-10-16 分區 · 81,254 列 · 1 檔');
    expect(runText({ ...r, status: 'failed', error: '連不上 SFTP', partition_date: null })).toBe('2026-10-17 02:00 · 整段期間 · 失敗：連不上 SFTP');
    expect(runText(null)).toBe('尚未執行');
  });
  it('內容摘要與事件分組', () => {
    expect(contentText({ kind: 'events', events: ['a', 'b'], columns: ['event', 'time'], time: { from: '2026-10-01', to: '2026-10-16' } })).toBe('事件明細 · 2 個事件 · 2 欄 · 2026-10-01～10-16');
    expect(contentText({ kind: 'segment_members', segment_id: 'sg1' }, (id) => `高價值（${id}）`)).toBe('分群名單 · 高價值（sg1）');
    expect(groupEvents([{ name: 'a', cn: 'A', group: '交易', status: 'live' }, { name: 'b', cn: 'B', group: '交易', status: 'live' }, { name: 'c', cn: 'C', group: '', status: 'draft' }])).toEqual([{ group: '交易', items: [{ name: 'a', cn: 'A' }, { name: 'b', cn: 'B' }] }]);
  });
  it('改內容或目的地才需重走審批', () => {
    const a = defaultTask(); const b = { ...a, name: '改名', purpose: '改用途' };
    expect(needsReapproval(a, b)).toBe(false);
    expect(needsReapproval(a, { ...b, content: { ...a.content, events: ['bond_deal'] } })).toBe(true);
    expect(needsReapproval(a, { ...b, destination_id: 'd2' })).toBe(true);
  });
});
