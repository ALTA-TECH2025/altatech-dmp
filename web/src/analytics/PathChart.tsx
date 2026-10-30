// 路徑桑基圖（ECharts sankey）：層 = 步、節點高度、連線寬 ∝ 會話數（同神策；舊格式結果為人數，unit 決定文字）；顏色跟節點名（固定順序類別色，離開／其他灰）；滑過高亮相鄰連線；點節點 → 設為新起點／看用戶。
import { escapeHtml } from '@/lib/html';
import { useMemo, useState } from 'react';
import { Badge, Button, Group, Text } from '@mantine/core';
import { Chart, resolveColor, type ChartEvent, type ChartOption } from '@/ds/Chart';
import { fmtNum } from './series';
import { GRAY_NODES, fmtUnit, nodeColors, skLinks, skNodes, type RawLink, type RawNode, type SkNode, type SkUnit } from './sankey';

const key = (level: number, node: string) => `${level}|${node}`;

export function PathChart({ nodes: rawNodes, links: rawLinks, unit, labelOf, onNode }: { nodes: RawNode[]; links: RawLink[]; unit: SkUnit; labelOf: (n: string) => string; onNode?: (n: SkNode, action: 'anchor' | 'users') => void }) {
  const [menu, setMenu] = useState<SkNode | null>(null);
  const nodes = useMemo(() => skNodes(rawNodes, unit), [rawNodes, unit]); const links = useMemo(() => skLinks(rawLinks, unit), [rawLinks, unit]);
  const levels = nodes.length ? Math.max(...nodes.map((n) => n.level)) + 1 : 0;
  const option = useMemo<ChartOption>(() => {
    const colors = nodeColors(nodes);
    const byKey = new Map(nodes.map((n) => [key(n.level, n.node), n]));
    return {
      tooltip: { trigger: 'item', formatter: (p) => {
        const q = p as { dataType?: string; data: { name?: string; source?: string; target?: string; value?: number } };
        if (q.dataType === 'edge') { const [, f] = (q.data.source ?? '').split('|'); const [, t] = (q.data.target ?? '').split('|'); return `${escapeHtml(labelOf(f))} → ${escapeHtml(labelOf(t))}：${fmtUnit(q.data.value ?? 0, unit, fmtNum)}`; }
        const n = byKey.get(q.data.name ?? ''); if (!n) return '';
        const out = links.filter((k) => k.level === n.level && k.from === n.node).sort((a, b) => b.value - a.value).slice(0, 6).map((k) => `→ ${escapeHtml(labelOf(k.to))}：${fmtNum(k.value)}（${Math.round((k.value / n.value) * 100)}%）`).join('<br/>');
        return `<b>第 ${n.level} 步 · ${escapeHtml(labelOf(n.node))}</b><br/>${fmtUnit(n.value, unit, fmtNum)}${out ? `<br/>${out}` : ''}`;
      } },
      series: [{ type: 'sankey', left: 8, right: 120, top: 8, bottom: 8, nodeWidth: 14, nodeGap: 8, nodeAlign: 'left', layoutIterations: 0, draggable: false,
        data: nodes.map((n) => ({ name: key(n.level, n.node), depth: n.level, value: n.value, itemStyle: { color: resolveColor(colors[n.node] ?? (GRAY_NODES.has(n.node) ? 'var(--faint)' : 'var(--s8)')), borderColor: resolveColor('var(--panel)') } })),
        links: links.map((k) => ({ source: key(k.level, k.from), target: key(k.level + 1, k.to), value: k.value })),
        lineStyle: { color: 'source', opacity: 0.3, curveness: 0.5 }, emphasis: { focus: 'adjacency', lineStyle: { opacity: 0.6 } },
        label: { fontSize: 11.5, color: resolveColor('var(--body)'), formatter: (p) => { const [, node] = String((p as { name: string }).name).split('|'); return `${labelOf(node)} ${fmtNum((p as { value?: number }).value)}`; } } }],
    };
  }, [nodes, links, labelOf, unit]);
  if (!nodes.length) return <Text size="sm" c="dimmed">沒有路徑（起點在期間內沒有出現，或都被抑制）</Text>;
  const onClick = (e: ChartEvent) => { if (!onNode || e.dataType !== 'node') return; const [lv, node] = String(e.name).split('|'); const n = nodes.find((x) => x.level === Number(lv) && x.node === node); if (n) setMenu(n); };
  return (
    <div>
      <Chart option={option} height={440} minWidth={640} label="路徑桑基圖" onClick={onClick} />
      <Group justify="space-between" px="xs"><Text size="xs" c="dimmed">第 0 步</Text>{levels > 1 && <Text size="xs" c="dimmed">第 {levels - 1} 步</Text>}</Group>
      {menu && onNode && <Group gap="xs" mt="xs"><Badge color="brand">第 {menu.level} 步 · {labelOf(menu.node)}</Badge>
        {!GRAY_NODES.has(menu.node) && <Button size="compact-sm" variant="subtle" onClick={() => { onNode(menu, 'anchor'); setMenu(null); }}>設為新起點</Button>}
        <Button size="compact-sm" variant="subtle" onClick={() => { onNode(menu, 'users'); setMenu(null); }}>用戶列表</Button><Button size="compact-sm" variant="subtle" onClick={() => setMenu(null)}>關閉</Button></Group>}
    </div>
  );
}
