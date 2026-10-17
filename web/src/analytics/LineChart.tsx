// 折線／長條（ECharts）：單一 y 軸、固定順序類別色、≥2 序列有圖例、2–4 條折線在線尾標名、十字線提示框、抑制點用空心標記與紋理；超過 8 條序列只畫前 8 條並提示。
import { useMemo } from 'react';
import { Text } from '@mantine/core';
import { Chart, HATCH, cssVar, type ChartOption } from '@/ds/Chart';
import { fmtNum, type Series } from './series';

const xLabel = (x: string) => (/^\d{4}-\d{2}-\d{2}/.test(x) ? (x.length > 10 ? x.slice(5, 16) : x) : x.length > 12 ? `${x.slice(0, 11)}…` : x);

export function LineChart({ series, kind = 'line', height = 280 }: { series: Series[]; kind?: 'line' | 'bar'; height?: number }) {
  const shown = series.slice(0, 8);
  const xs = useMemo(() => { const u = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.x)))); return kind === 'bar' ? u : u.sort(); }, [series, kind]);   // 長條 = 類別軸，照結果順序；折線 = 時間軸，排序
  const option = useMemo<ChartOption>(() => {
    const endLabels = kind === 'line' && shown.length > 1 && shown.length <= 4;
    const panel = cssVar('--panel', '#fff');
    return {
      grid: { left: 8, right: endLabels ? 96 : 16, top: shown.length > 1 ? 36 : 12, bottom: 8, containLabel: true },
      legend: shown.length > 1 ? { top: 0, left: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10 } : undefined,
      tooltip: { trigger: 'axis', axisPointer: kind === 'bar' ? { type: 'shadow' } : { type: 'line', lineStyle: { type: 'dashed' } }, valueFormatter: (v) => fmtNum(v as number) },
      xAxis: { type: 'category', data: xs, boundaryGap: kind === 'bar', axisTick: { show: false }, axisLine: { lineStyle: { color: cssVar('--axis', '#C9D2D8') } }, axisLabel: { formatter: xLabel, hideOverlap: true } },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => fmtNum(v) }, splitLine: { lineStyle: { color: cssVar('--grid', '#EDF1F3') } } },
      series: shown.map((s) => {
        const byX = new Map(s.points.map((p) => [p.x, p]));
        const pts = xs.map((x) => { const p = byX.get(x); return !p || p.y === null ? null : p; });
        if (kind === 'bar') return { type: 'bar' as const, name: s.label, barMaxWidth: 28, itemStyle: { borderRadius: [4, 4, 0, 0] }, data: pts.map((p) => (p === null ? null : p.suppressed ? { value: p.y as number, itemStyle: { decal: HATCH } } : (p.y as number))) };
        const data = pts.map((p) => (p === null ? null : p.suppressed ? { value: p.y as number, itemStyle: { color: panel }, symbol: 'emptyCircle', symbolSize: 9 } : (p.y as number)));
        return { type: 'line' as const, name: s.label, data, symbol: 'circle', symbolSize: 7, lineStyle: { width: 2 }, connectNulls: false, emphasis: { focus: 'series' as const }, endLabel: endLabels ? { show: true, formatter: ({ seriesName }: { seriesName?: string }) => (seriesName && seriesName.length > 14 ? `${seriesName.slice(0, 13)}…` : seriesName ?? ''), fontSize: 11, color: cssVar('--body', '#56646D') } : undefined, labelLayout: { moveOverlap: 'shiftY' } };
      }),
    };
  }, [shown, xs, kind]);
  if (!xs.length) return <Text size="sm" c="dimmed">沒有資料</Text>;
  return (
    <div>
      <Chart option={option} height={height} label="趨勢圖" />
      {series.length > 8 && <Text size="xs" c="dimmed">其餘 {series.length - 8} 條未畫（請縮小分組或用 Top N）</Text>}
    </div>
  );
}
