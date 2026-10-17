// 自繪 SVG 漏斗（dataviz 規範：單軸、細柱、資料端 4px 圓角、數值用文字色、抑制列用紋理與「< K」）。不引入圖表庫。
import type { FunnelRow } from './types';
import { useWidth } from './useWidth';

export function FunnelChart({ rows, k }: { rows: FunnelRow[]; k: number }) {
  const [box, cw] = useWidth(720);
  const narrow = cw < 520;
  const W = Math.min(720, Math.max(300, cw)), rowH = narrow ? 40 : 44, labelW = narrow ? 96 : 190, pad = 12;
  const H = rows.length * rowH + pad * 2;
  const max = Math.max(1, ...rows.map((r) => r.users ?? 0));
  const barW = W - labelW - (narrow ? 92 : 120);
  return (
    <div ref={box}><svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="漏斗圖" style={{ maxWidth: '100%', display: 'block' }}>
      <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="var(--muted)" strokeWidth="1.5" /></pattern></defs>
      {rows.map((r, i) => {
        const y = pad + i * rowH; const v = r.users ?? 0; const w = r.suppressed ? barW * 0.08 : Math.max(4, (v / max) * barW);
        return (
          <g key={r.step}>
            <text x={labelW - 10} y={y + rowH / 2 + 4} textAnchor="end" fontSize="13" fill="var(--ink)">{r.step}. {narrow && r.alias.length > 5 ? r.alias.slice(0, 5) + '…' : r.alias}</text>
            <rect x={labelW} y={y + 10} width={w} height={rowH - 20} rx={4} fill={r.suppressed ? 'url(#hatch)' : 'var(--brand)'} opacity={r.suppressed ? 0.6 : 0.9}>
              <title>{r.alias}：{r.suppressed ? `少於 ${k} 人，已抑制` : `${v.toLocaleString()} 人`}</title>
            </rect>
            <text x={labelW + w + 8} y={y + rowH / 2 + 4} fontSize="12.5" fill="var(--body)" fontFamily="IBM Plex Mono, monospace">
              {r.suppressed ? `< ${k}` : v.toLocaleString()}{i > 0 && r.rate_prev !== null && r.rate_prev !== undefined ? `  ↓ ${(r.rate_prev * 100).toFixed(1)}%` : ''}
            </text>
          </g>
        );
      })}
    </svg></div>
  );
}
