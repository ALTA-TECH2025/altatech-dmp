// 圖表：ECharts（Apache 軟體基金會治理，Apache-2.0）。只載入用到的圖型與元件、SVG 渲染；配色與深淺色跟 app.css 的 token 走。
import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { ComposeOption } from 'echarts/core';
import type { BarSeriesOption } from 'echarts/charts';
import type { GridComponentOption, LegendComponentOption, TooltipComponentOption } from 'echarts/components';

echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);
export type ChartOption = ComposeOption<BarSeriesOption | GridComponentOption | LegendComponentOption | TooltipComponentOption>;

function cssVar(name: string, fallback: string): string { const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fallback; }
/** 以 app.css 的 token 組 ECharts 基礎樣式（系列色 --s1…--s8、字色、格線）。 */
export function chartBase(): ChartOption {
  return {
    color: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => cssVar(`--s${i}`, '#2a78d6')),
    textStyle: { color: cssVar('--body', '#56646D'), fontFamily: "'Plus Jakarta Sans', 'Noto Sans TC', sans-serif", fontSize: 12 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: cssVar('--panel', '#fff'), borderColor: cssVar('--line', '#E2E8EC'), textStyle: { color: cssVar('--ink', '#1C2B33'), fontSize: 12 } },
    grid: { left: 8, right: 12, top: 8, bottom: 8, containLabel: true },
  };
}

export function Chart({ option, height = 220, label }: { option: ChartOption; height?: number; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const inst = echarts.init(el, undefined, { renderer: 'svg' });
    inst.setOption({ ...chartBase(), ...option });
    const ro = new ResizeObserver(() => inst.resize()); ro.observe(el);
    return () => { ro.disconnect(); inst.dispose(); };
  }, [option]);
  return <div ref={ref} role="img" aria-label={label} style={{ width: '100%', height }} />;
}

/** 橫向長條（值分布、畫像）：由大到小，值標在條尾。 */
export function hbar(rows: { value: string; users: number }[], total?: number): ChartOption {
  const r = [...rows].reverse();
  return {
    grid: { left: 8, right: 56, top: 4, bottom: 4, containLabel: true },
    xAxis: { type: 'value', axisLabel: { show: false }, splitLine: { lineStyle: { color: cssVar('--grid', '#EDF1F3') } } },
    yAxis: { type: 'category', data: r.map((x) => x.value || '（空）'), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { width: 96, overflow: 'truncate' } },
    series: [{ type: 'bar', data: r.map((x) => x.users), barMaxWidth: 14, itemStyle: { borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', formatter: (p) => `${Number(p.value).toLocaleString()}${total ? ` (${Math.round((Number(p.value) / total) * 100)}%)` : ''}`, fontSize: 11 } }],
    tooltip: { trigger: 'item', formatter: (p) => `${(p as { name: string }).name}：${Number((p as { value: number }).value).toLocaleString()} 人` },
  };
}
