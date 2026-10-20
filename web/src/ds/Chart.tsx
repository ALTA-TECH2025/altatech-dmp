// 圖表：ECharts（Apache 軟體基金會治理，Apache-2.0）。只載入用到的圖型與元件（bar／line／pie／sankey、grid／tooltip／legend）、SVG 渲染；配色與深淺色跟 app.css 的 token 走。
import { escapeHtml } from '@/lib/html';
import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, SankeyChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { ComposeOption, ECharts } from 'echarts/core';
import type { BarSeriesOption, LineSeriesOption, PieSeriesOption, SankeySeriesOption } from 'echarts/charts';
import type { GridComponentOption, LegendComponentOption, TooltipComponentOption } from 'echarts/components';

echarts.use([BarChart, LineChart, PieChart, SankeyChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);
export type ChartOption = ComposeOption<BarSeriesOption | LineSeriesOption | PieSeriesOption | SankeySeriesOption | GridComponentOption | LegendComponentOption | TooltipComponentOption>;
export type ChartEvent = { componentType?: string; dataType?: string; name?: string; value?: unknown; data?: unknown; seriesName?: string; dataIndex?: number };

export function cssVar(name: string, fallback = ''): string { if (typeof document === 'undefined') return fallback; const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fallback; }
/** 'var(--s1)' 這類 token 字串 → 實際色碼（ECharts 的 SVG 屬性不吃 CSS 變數）。 */
export function resolveColor(c: string): string { const m = /^var\((--[\w-]+)\)$/.exec(c.trim()); return m ? cssVar(m[1], '#2a78d6') : c; }
export const seriesPalette = (): string[] => [1, 2, 3, 4, 5, 6, 7, 8].map((i) => cssVar(`--s${i}`, '#2a78d6'));
/** 抑制（人數少於 K）的資料點用斜線紋理，不只靠顏色。 */
export const HATCH: NonNullable<NonNullable<BarSeriesOption['itemStyle']>['decal']> = { symbol: 'rect', dashArrayX: [1, 0], dashArrayY: [2, 3], rotation: Math.PI / 4, color: 'rgba(120,130,140,.45)' };

/** 以 app.css 的 token 組 ECharts 基礎樣式（系列色 --s1…--s8、字色、格線）。 */
export function chartBase(): ChartOption {
  return {
    color: seriesPalette(),
    textStyle: { color: cssVar('--body', '#56646D'), fontFamily: "'Plus Jakarta Sans', 'Noto Sans TC', sans-serif", fontSize: 12 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: cssVar('--panel', '#fff'), borderColor: cssVar('--line', '#E2E8EC'), textStyle: { color: cssVar('--ink', '#1C2B33'), fontSize: 12 }, confine: true },
    grid: { left: 8, right: 12, top: 8, bottom: 8, containLabel: true },
  };
}

export function Chart({ option, height = 220, label, onClick, minWidth }: { option: ChartOption; height?: number; label: string; onClick?: (e: ChartEvent) => void; minWidth?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<ECharts | null>(null);
  const click = useRef(onClick); click.current = onClick;
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const c = echarts.init(el, undefined, { renderer: 'svg' }); inst.current = c;
    c.on('click', (p) => click.current?.(p as ChartEvent));
    const ro = new ResizeObserver(() => c.resize()); ro.observe(el);
    return () => { ro.disconnect(); c.dispose(); inst.current = null; };
  }, []);
  useEffect(() => { inst.current?.setOption({ ...chartBase(), ...option }, { notMerge: true }); }, [option]);
  return <div style={minWidth ? { overflowX: 'auto' } : undefined}><div ref={ref} role="img" aria-label={label} style={{ width: '100%', minWidth, height }} /></div>;
}

/** 橫向長條（值分布、畫像）：由大到小，值標在條尾。 */
export function hbar(rows: { value: string; users: number }[], total?: number): ChartOption {
  const r = [...rows].reverse();
  return {
    grid: { left: 8, right: 56, top: 4, bottom: 4, containLabel: true },
    xAxis: { type: 'value', axisLabel: { show: false }, splitLine: { lineStyle: { color: cssVar('--grid', '#EDF1F3') } } },
    yAxis: { type: 'category', data: r.map((x) => x.value || '（空）'), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { width: 96, overflow: 'truncate' } },
    series: [{ type: 'bar', data: r.map((x) => x.users), barMaxWidth: 14, itemStyle: { borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', formatter: (p) => `${Number(p.value).toLocaleString()}${total ? ` (${Math.round((Number(p.value) / total) * 100)}%)` : ''}`, fontSize: 11 } }],
    tooltip: { trigger: 'item', formatter: (p) => `${escapeHtml((p as { name: string }).name)}：${Number((p as { value: number }).value).toLocaleString()} 人` },
  };
}
