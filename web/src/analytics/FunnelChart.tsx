// 漏斗（ECharts 橫向長條）：單軸、細柱、資料端 4px 圓角、數值與逐步轉換率標在條尾；抑制列用紋理與「< K」。
import { useMemo } from 'react';
import { Chart, HATCH, cssVar, type ChartOption } from '@/ds/Chart';
import type { FunnelRow } from './types';

export function FunnelChart({ rows, k }: { rows: FunnelRow[]; k: number }) {
  const option = useMemo<ChartOption>(() => {
    const max = Math.max(1, ...rows.map((r) => r.users ?? 0));
    const r = [...rows].reverse();
    return {
      grid: { left: 8, right: 140, top: 6, bottom: 6, containLabel: true },
      xAxis: { type: 'value', max, axisLabel: { show: false }, splitLine: { show: false } },
      yAxis: { type: 'category', data: r.map((x) => `${x.step}. ${x.alias}`), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { width: 180, overflow: 'truncate', color: cssVar('--ink', '#1C2B33'), fontSize: 13 } },
      tooltip: { trigger: 'item', formatter: (p) => { const row = r[(p as { dataIndex: number }).dataIndex]; return `${row.alias}：${row.suppressed ? `少於 ${k} 人，已抑制` : `${(row.users ?? 0).toLocaleString()} 人`}`; } },
      series: [{ type: 'bar', barMaxWidth: 24, data: r.map((x) => (x.suppressed ? { value: max * 0.08, itemStyle: { decal: HATCH, color: cssVar('--line', '#E2E8EC') } } : { value: Math.max(x.users ?? 0, max * 0.006) })), itemStyle: { borderRadius: [0, 4, 4, 0], color: cssVar('--brand', '#2C73EA') },
        label: { show: true, position: 'right', fontFamily: 'IBM Plex Mono, monospace', fontSize: 12.5, color: cssVar('--body', '#56646D'), formatter: (p) => { const i = (p as { dataIndex: number }).dataIndex; const row = r[i]; const idx = rows.length - 1 - i; return `${row.suppressed ? `< ${k}` : (row.users ?? 0).toLocaleString()}${idx > 0 && row.rate_prev !== null && row.rate_prev !== undefined ? `  ↓ ${(row.rate_prev * 100).toFixed(1)}%` : ''}`; } } }],
    };
  }, [rows, k]);
  return <Chart option={option} height={rows.length * 44 + 24} label="漏斗圖" />;
}
