// 自繪 SVG 折線／長條（dataviz 規範：單一 y 軸、2px 線、≥8px 標記、固定類別色、≥2 序列有圖例並直接標注、十字線 + 提示框、抑制點以空心標記）。
import { useMemo, useState } from 'react';
import { fmtNum, niceScale, seriesColor, type Series } from './series';

export function LineChart({ series, kind = 'line', height = 280 }: { series: Series[]; kind?: 'line' | 'bar'; height?: number }) {
  const W = 900, H = height, L = 56, R = 16, T = 16, B = 40;
  const xs = useMemo(() => Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.x)))).sort(), [series]);
  const max = Math.max(0, ...series.flatMap((s) => s.points.map((p) => p.y ?? 0)));
  const scale = niceScale(max);
  const [hover, setHover] = useState<number | null>(null);
  const shown = series.slice(0, 8);
  if (!xs.length) return <p className="note">沒有資料</p>;
  const xPos = (i: number) => L + ((W - L - R) * (xs.length === 1 ? 0.5 : i / (xs.length - 1)));
  const yPos = (v: number) => T + (H - T - B) * (1 - v / scale.max);
  const bandW = (W - L - R) / Math.max(1, xs.length);
  const idx = (x: string) => xs.indexOf(x);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="趨勢圖" style={{ display: 'block' }}
        onMouseMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); const px = ((e.clientX - r.left) / r.width) * W; const i = kind === 'bar' ? Math.floor((px - L) / bandW) : Math.round(((px - L) / (W - L - R)) * (xs.length - 1)); setHover(i >= 0 && i < xs.length ? i : null); }}
        onMouseLeave={() => setHover(null)}>
        {scale.ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={yPos(t)} y2={yPos(t)} stroke="var(--grid)" /><text x={L - 8} y={yPos(t) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{fmtNum(t)}</text></g>)}
        {xs.map((x, i) => (i % Math.ceil(xs.length / 8) === 0 || i === xs.length - 1) && <text key={x} x={kind === 'bar' ? L + bandW * (i + 0.5) : xPos(i)} y={H - B + 18} textAnchor="middle" fontSize="11" fill="var(--muted)">{x.length > 10 ? x.slice(5, 16) : x}</text>)}
        {kind === 'bar' ? shown.map((s, si) => s.points.map((p) => {
          const i = idx(p.x); const bw = (bandW * 0.7) / shown.length; const x = L + bandW * i + bandW * 0.15 + bw * si; const y = yPos(p.y ?? 0);
          return <rect key={p.x} x={x + 1} y={y} width={Math.max(2, bw - 2)} height={H - B - y} rx={4} fill={p.suppressed ? 'url(#hatchc)' : seriesColor(si)} opacity={hover === null || hover === i ? 0.9 : 0.45} />;
        })) : shown.map((s, si) => {
          const pts = s.points.filter((p) => p.y !== null).map((p) => `${xPos(idx(p.x))},${yPos(p.y as number)}`).join(' ');
          return (
            <g key={s.key}>
              <polyline points={pts} fill="none" stroke={seriesColor(si)} strokeWidth={2} strokeLinejoin="round" opacity={0.95} />
              {s.points.map((p) => p.y !== null && <circle key={p.x} cx={xPos(idx(p.x))} cy={yPos(p.y)} r={hover === idx(p.x) ? 5 : 3.5} fill={p.suppressed ? 'var(--panel)' : seriesColor(si)} stroke={seriesColor(si)} strokeWidth={p.suppressed ? 2 : 1.5} />)}
              {s.points.length > 0 && shown.length > 1 && shown.length <= 4 && <text x={xPos(idx(s.points[s.points.length - 1].x)) + 6} y={yPos(s.points[s.points.length - 1].y ?? 0) + 4} fontSize="11" fill="var(--body)">{s.label}</text>}
            </g>
          );
        })}
        {hover !== null && kind === 'line' && <line x1={xPos(hover)} x2={xPos(hover)} y1={T} y2={H - B} stroke="var(--axis)" strokeDasharray="3 3" />}
        <defs><pattern id="hatchc" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="var(--muted)" strokeWidth="1.5" /></pattern></defs>
      </svg>
      {shown.length > 1 && <div className="legend">{shown.map((s, i) => <span key={s.key}><i style={{ background: seriesColor(i) }} />{s.label}</span>)}{series.length > 8 && <span className="sub">其餘 {series.length - 8} 條未畫（請縮小分組或用 Top N）</span>}</div>}
      {hover !== null && (
        <div className="tooltip"><b>{xs[hover]}</b>{shown.map((s, i) => { const p = s.points.find((q) => q.x === xs[hover]); return <div key={s.key}><i style={{ background: seriesColor(i) }} />{s.label}：{p?.suppressed ? '已抑制' : fmtNum(p?.y)}</div>; })}</div>
      )}
    </div>
  );
}
