// 折線／面積／長條／堆疊／百分比堆疊／圓餅（ECharts）：單一 y 軸、固定順序類別色、≥2 序列有圖例（含全選／反選）、2–4 條折線在線尾標名、十字線提示框、
// 抑制點用空心標記與紋理；超過 8 條序列只畫前 8 條並提示；數值依頁面選的格式顯示。
import { useMemo } from 'react';
import { Text } from '@mantine/core';
import { Chart, HATCH, cssVar, type ChartOption } from '@/ds/Chart';
import { escapeHtml } from '@/lib/html';
import { fmtValue, pieSlices, pieTop, type ChartKind, type NumFormat } from './events';
import type { Series } from './series';

export type LineChartKind = Exclude<ChartKind, 'table'>;
const xLabel = (x: string) => (/^\d{4}-\d{2}-\d{2}/.test(x) ? (x.length > 10 ? x.slice(5, 16) : x) : x.length > 12 ? `${x.slice(0, 11)}…` : x);

export function LineChart({ series, kind = 'line', height = 280, format = 'auto' }: { series: Series[]; kind?: LineChartKind; height?: number; format?: NumFormat }) {
  const shown = series.slice(0, 8);
  const categorical = kind === 'bar' || kind === 'stacked' || kind === 'percent';
  const xs = useMemo(() => { const u = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.x)))); return categorical ? u : u.sort(); }, [series, categorical]);   // 長條 = 類別軸，照結果順序；折線 = 時間軸，排序
  const option = useMemo<ChartOption>(() => {
    const panel = cssVar('--panel', '#fff');
    const fmt = (v: unknown) => (kind === 'percent' ? `${Number(v).toFixed(1)}%` : fmtValue(v, format));
    const legend = shown.length > 1 ? { top: 0, left: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10, type: 'scroll' as const, selector: [{ type: 'all' as const, title: '全選' }, { type: 'inverse' as const, title: '反選' }], selectorLabel: { fontSize: 11 } } : undefined;
    if (kind === 'pie') {
      const data = pieTop(pieSlices(series), 12);                                 // 超過 12 類併成「其他」，百分比分母仍是全部
      return { legend, tooltip: { trigger: 'item', formatter: (p) => { const q = p as { name: string; value: number; percent: number }; return `${escapeHtml(q.name)}：${fmtValue(q.value, format)}（${q.percent}%）`; } },
        series: [{ type: 'pie', radius: ['38%', '68%'], center: ['50%', '55%'], data, itemStyle: { borderColor: panel, borderWidth: 2 }, label: { formatter: '{b}', fontSize: 11 }, emphasis: { scale: false } }] };
    }
    const endLabels = kind === 'line' && shown.length > 1 && shown.length <= 4;
    const stacked = kind === 'stacked' || kind === 'percent';
    const stackOf = (s: Series) => (s.group ? s.metric : 'all');                 // 有分組：同一指標的各分組疊成一條；沒分組：各指標疊成一條
    const totals = new Map<string, number>();                                       // 百分比堆疊：每條堆疊在每個 x 的合計
    if (kind === 'percent') for (const s of shown) for (const p of s.points) totals.set(`${stackOf(s)}|${p.x}`, (totals.get(`${stackOf(s)}|${p.x}`) ?? 0) + (p.y ?? 0));
    return {
      grid: { left: 8, right: endLabels ? 96 : 16, top: shown.length > 1 ? 36 : 12, bottom: 8, containLabel: true },
      legend,
      tooltip: { trigger: 'axis', axisPointer: categorical ? { type: 'shadow' } : { type: 'line', lineStyle: { type: 'dashed' } }, valueFormatter: fmt },
      xAxis: { type: 'category', data: xs, boundaryGap: categorical, axisTick: { show: false }, axisLine: { lineStyle: { color: cssVar('--axis', '#C9D2D8') } }, axisLabel: { formatter: xLabel, hideOverlap: true } },
      yAxis: { type: 'value', max: kind === 'percent' ? 100 : undefined, axisLabel: { formatter: (v: number) => (kind === 'percent' ? `${v}%` : fmtValue(v, format === 'auto' ? 'auto' : format)) }, splitLine: { lineStyle: { color: cssVar('--grid', '#EDF1F3') } } },
      series: shown.map((s) => {
        const byX = new Map(s.points.map((p) => [p.x, p]));
        const pts = xs.map((x) => { const p = byX.get(x); const t = totals.get(`${stackOf(s)}|${x}`); return !p || p.y === null ? null : kind === 'percent' ? { ...p, y: t ? ((p.y as number) / t) * 100 : 0 } : p; });
        if (categorical) return { type: 'bar' as const, name: s.label, stack: stacked ? stackOf(s) : undefined, barMaxWidth: 28, itemStyle: { borderRadius: stacked ? 0 : [4, 4, 0, 0], borderColor: stacked ? panel : undefined, borderWidth: stacked ? 1 : 0 }, data: pts.map((p) => (p === null ? null : p.suppressed ? { value: p.y as number, itemStyle: { decal: HATCH } } : (p.y as number))) };
        const data = pts.map((p) => (p === null ? null : p.suppressed ? { value: p.y as number, itemStyle: { color: panel }, symbol: 'emptyCircle', symbolSize: 9 } : (p.y as number)));
        return { type: 'line' as const, name: s.label, data, symbol: 'circle', symbolSize: 7, lineStyle: { width: 2 }, areaStyle: kind === 'area' ? { opacity: 0.18 } : undefined, connectNulls: false, emphasis: { focus: 'series' as const }, endLabel: endLabels ? { show: true, formatter: ({ seriesName }: { seriesName?: string }) => (seriesName && seriesName.length > 14 ? `${seriesName.slice(0, 13)}…` : seriesName ?? ''), fontSize: 11, color: cssVar('--body', '#56646D') } : undefined, labelLayout: { moveOverlap: 'shiftY' } };
      }),
    };
  }, [shown, xs, kind, categorical, format, series]);
  if (!xs.length) return <Text size="sm" c="dimmed">沒有資料</Text>;
  return (
    <div>
      <Chart option={option} height={height} label="趨勢圖" />
      {series.length > 8 && kind !== 'pie' && <Text size="xs" c="dimmed">其餘 {series.length - 8} 條未畫（請縮小分組或用 Top N）</Text>}
    </div>
  );
}
