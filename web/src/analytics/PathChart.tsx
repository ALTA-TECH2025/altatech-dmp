// 自繪 SVG 桑基圖：層 = 步、節點高度 ∝ 人數、連線寬 ∝ 人數；固定類別色（跟節點名，離開／其他灰）；滑過節點高亮相鄰連線並顯示提示；點節點 → 設為新起點／看用戶。
import { useMemo, useState } from 'react';
import { fmtNum } from './series';
import { layout, type SkLink, type SkNode } from './sankey';

export function PathChart({ nodes, links, labelOf, onNode }: { nodes: SkNode[]; links: SkLink[]; labelOf: (n: string) => string; onNode?: (n: SkNode, action: 'anchor' | 'users') => void }) {
  const W = 960, H = 440, NW = 14;
  const l = useMemo(() => layout(nodes, links, W, H, NW, 8), [nodes, links]);
  const [hover, setHover] = useState<string | null>(null);
  const [menu, setMenu] = useState<SkNode | null>(null);
  if (!l.nodes.length) return <p className="note">沒有路徑（起點在期間內沒有出現，或都被抑制）</p>;
  const key = (n: { level: number; node: string }) => `${n.level}|${n.node}`;
  const touches = (k: { level: number; from: string; to: string }) => hover !== null && (key({ level: k.level, node: k.from }) === hover || key({ level: k.level + 1, node: k.to }) === hover);
  const hn = l.nodes.find((n) => key(n) === hover);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H + 28}`} width="100%" role="img" aria-label="路徑桑基圖" style={{ display: 'block' }}>
        {Array.from({ length: l.levels }, (_, lv) => <text key={lv} x={(l.nodes.find((n) => n.level === lv)?.x ?? 0) + (lv === l.levels - 1 ? NW : 0)} y={H + 22} fontSize="11" fill="var(--muted)" textAnchor={lv === l.levels - 1 ? 'end' : 'start'}>第 {lv} 步</text>)}
        {l.links.map((k, i) => <path key={i} d={k.d} fill="none" stroke={k.color} strokeWidth={k.w} opacity={hover === null ? 0.35 : touches(k) ? 0.7 : 0.08} />)}
        {l.nodes.map((n) => (
          <g key={key(n)} onMouseEnter={() => setHover(key(n))} onMouseLeave={() => setHover(null)} onClick={() => onNode && setMenu(n)} style={{ cursor: onNode ? 'pointer' : 'default' }}>
            <rect x={n.x} y={n.y} width={NW} height={n.h} rx={3} fill={n.color} stroke="var(--panel)" strokeWidth={1} opacity={hover === null || hover === key(n) ? 1 : 0.6} />
            {n.h >= 12 && <text x={n.level === l.levels - 1 ? n.x - 6 : n.x + NW + 6} y={n.y + Math.min(n.h / 2 + 4, n.h - 2)} fontSize="11.5" fill="var(--body)" textAnchor={n.level === l.levels - 1 ? 'end' : 'start'} paintOrder="stroke" stroke="var(--panel)" strokeWidth={3} strokeLinejoin="round">{labelOf(n.node)} <tspan fill="var(--muted)">{fmtNum(n.users)}</tspan></text>}
          </g>
        ))}
      </svg>
      {hn && <div className="tooltip"><b>第 {hn.level} 步 · {labelOf(hn.node)}</b><div>{fmtNum(hn.users)} 人</div>
        {l.links.filter((k) => k.level === hn.level && k.from === hn.node).slice(0, 6).map((k) => <div key={k.to}><i style={{ background: k.color }} />→ {labelOf(k.to)}：{fmtNum(k.users)}（{Math.round((k.users / hn.users) * 100)}%）</div>)}</div>}
      {menu && onNode && <div className="row" style={{ marginTop: 8 }}><span className="pill blue">第 {menu.level} 步 · {labelOf(menu.node)}</span>
        {menu.node !== '離開' && menu.node !== '其他' && <button className="btn-ghost sm" onClick={() => { onNode(menu, 'anchor'); setMenu(null); }}>設為新起點</button>}
        <button className="btn-ghost sm" onClick={() => { onNode(menu, 'users'); setMenu(null); }}>用戶列表</button><button className="btn-ghost sm" onClick={() => setMenu(null)}>關閉</button></div>}
    </div>
  );
}
