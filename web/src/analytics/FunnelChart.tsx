// 漏斗（ECharts 橫向長條）：單軸、細柱、資料端 4px 圓角；每步實心＝到達人數，後面接紋理段＝相對上一步流失的人數；
// 條尾標人數與逐步轉換率，最大流失那一步標紅；抑制列用紋理與「< K」。
import { escapeHtml } from '@/lib/html';
import { useMemo } from 'react';
import { Chart, HATCH, cssVar, type ChartOption } from '@/ds/Chart';
import type { StepSummary } from './funnel';

export function FunnelChart({ rows, k, maxDropStep }: { rows: StepSummary[]; k: number; maxDropStep?: number | null }) {
  const option = useMemo<ChartOption>(() => {
    const max = Math.max(1, ...rows.map((r) => r.users ?? 0));
    const r = [...rows].reverse();
    const danger = cssVar('--red', '#D14343');
    return {
      grid: { left: 8, right: 170, top: 6, bottom: 6, containLabel: true },
      xAxis: { type: 'value', max, axisLabel: { show: false }, splitLine: { show: false } },
      yAxis: { type: 'category', data: r.map((x) => `${x.step}. ${x.alias}`), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { width: 180, overflow: 'truncate', color: cssVar('--ink', '#1C2B33'), fontSize: 13 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p) => { const row = r[(Array.isArray(p) ? p[0] : p).dataIndex as number]; return `${escapeHtml(row.alias)}：${row.suppressed ? `少於 ${k} 人，已抑制` : `${(row.users ?? 0).toLocaleString()} 人`}${row.lost !== null && !row.suppressed ? `<br/>流失 ${row.lost.toLocaleString()} 人（${((row.lost_rate ?? 0) * 100).toFixed(1)}%）` : ''}`; } },
      series: [
        { type: 'bar', stack: 'f', barMaxWidth: 24, data: r.map((x) => (x.suppressed ? { value: max * 0.08, itemStyle: { decal: HATCH, color: cssVar('--line', '#E2E8EC') } } : { value: Math.max(x.users ?? 0, max * 0.006) })), itemStyle: { borderRadius: [0, 4, 4, 0], color: cssVar('--brand', '#2C73EA') },
          label: { show: true, position: 'right', fontFamily: 'IBM Plex Mono, monospace', fontSize: 12.5, color: cssVar('--body', '#56646D'),
            formatter: (p) => { const i = (p as { dataIndex: number }).dataIndex; const row = r[i]; const drop = row.lost_rate !== null && row.step > 1 ? `  ↓ ${((1 - row.lost_rate) * 100).toFixed(1)}%` : ''; return `${row.suppressed ? `< ${k}` : (row.users ?? 0).toLocaleString()}${drop}${maxDropStep === row.step ? `  {hot|最大流失 ${((row.lost_rate ?? 0) * 100).toFixed(1)}%}` : ''}`; },
            rich: { hot: { color: danger, fontWeight: 700, fontSize: 12 } } } },
        { type: 'bar', stack: 'f', barMaxWidth: 24, silent: true, data: r.map((x) => (x.suppressed || x.lost === null ? 0 : x.lost)), itemStyle: { color: cssVar('--line', '#E2E8EC'), decal: HATCH } },
      ],
    };
  }, [rows, k, maxDropStep]);
  return <Chart option={option} height={rows.length * 44 + 24} label="漏斗圖" />;
}
