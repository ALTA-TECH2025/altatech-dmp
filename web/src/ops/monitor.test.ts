import { describe, expect, it } from 'vitest';
import { displayUnit, fmtStat, grafanaPanelUrl, latestValues, thresholdStatus, toChartSeries, xLabel, type Panel } from './monitor';

const panel = (p: Partial<Panel>): Panel => ({ id: 1, key: 'k', cn: 'k', section: 'platform', kind: 'timeseries', unit: 'short', expr: 'x', thresholds: [], series: [], ...p });

describe('monitor helpers', () => {
  it('displayUnit / xLabel', () => {
    expect(displayUnit('percentunit')).toEqual({ label: '%', scale: 100 }); expect(displayUnit('bytes').scale).toBe(1 / 1e6); expect(displayUnit('short')).toEqual({ label: '', scale: 1 });
    expect(xLabel('2026-10-17T15:51:00+00:00')).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/); expect(xLabel('bad')).toBe('bad');
  });
  it('toChartSeries scales and caps at 8', () => {
    const p = panel({ unit: 'percentunit', series: Array.from({ length: 10 }, (_, i) => ({ name: `s${i}`, points: [{ t: '2026-10-17T00:00:00Z', v: 0.123 }, { t: '2026-10-17T00:01:00Z', v: null }] })) });
    const r = toChartSeries(p);
    expect(r.series.length).toBe(8); expect(r.dropped).toBe(2); expect(r.series[0].points[0].y).toBe(12.3); expect(r.series[0].points[1].y).toBeNull(); expect(r.series[0].metric).toBe('k');
  });
  it('latestValues / thresholdStatus / fmtStat', () => {
    const p = panel({ series: [{ name: 'a', points: [{ t: '1', v: 3 }, { t: '2', v: null }] }, { name: 'b', points: [{ t: '1', v: null }] }] });
    expect(latestValues(p)).toEqual([{ name: 'a', v: 3 }, { name: 'b', v: null }]);
    expect(thresholdStatus(null, [1])).toBe('unknown'); expect(thresholdStatus(0.5, [])).toBe('ok'); expect(thresholdStatus(1, [1])).toBe('error'); expect(thresholdStatus(0.85, [0.8, 0.9])).toBe('warn'); expect(thresholdStatus(0.95, [0.8, 0.9])).toBe('error');
    expect(thresholdStatus(1, [0, 1])).toBe('ok'); expect(thresholdStatus(0, [0, 1])).toBe('error');
    expect(fmtStat(0.0234, 'percentunit')).toBe('2.34%'); expect(fmtStat(0.5, 'percentunit')).toBe('50.0%'); expect(fmtStat(5400, 's')).toBe('1.5 小時'); expect(fmtStat(90, 's')).toBe('2 分鐘'); expect(fmtStat(1665.9, 'm')).toBe('1.2 天');
    expect(fmtStat(7.2e12, 'bytes')).toBe('7.20 TB'); expect(fmtStat(2948635, 'bytes')).toBe('2.9 MB'); expect(fmtStat(2.1, 'reqps')).toBe('2.1 /s'); expect(fmtStat(1234, 'short')).toBe('1,234'); expect(fmtStat(null, 's')).toBe('—');
  });
  it('grafanaPanelUrl', () => {
    expect(grafanaPanelUrl(null, 'dmp-overview', 3, 6)).toBeNull();
    expect(grafanaPanelUrl('https://g.internal', 'dmp-overview', 3, 6)).toBe('https://g.internal/d/dmp-overview?viewPanel=3&from=now-6h&to=now');
  });
});
