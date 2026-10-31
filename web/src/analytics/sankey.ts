// 桑基圖版面（純函式，有單元測試）：層 = 步數，節點高度與連線寬度 ∝ 數值（新格式為會話數；舊格式為人數，見 pathUnit）；顏色跟著節點名（固定順序類別色，離開／其他用灰）。
export interface SkNode { level: number; node: string; value: number }
export interface SkLink { level: number; from: string; to: string; value: number }
export interface LaidNode extends SkNode { x: number; y: number; h: number; color: string }
export interface LaidLink extends SkLink { d: string; w: number; color: string }
export interface Layout { nodes: LaidNode[]; links: LaidLink[]; levels: number; scale: number }

export const GRAY_NODES = new Set(['離開', '其他']);

/** 後端節點／連線。新格式帶 sessions（會話數，同神策）；舊格式（示範固定資料、舊看板快取）只有 users（各路徑人數相加，可能重複）。
 *  兩種單位不混用：整份結果判定一個單位，畫面文字跟著單位走，不把 users 當成會話。 */
export interface RawNode { level: number; node: string; sessions?: number; users?: number }
export interface RawLink { level: number; from: string; to: string; sessions?: number; users?: number }
export type SkUnit = 'sessions' | 'users';
export function pathUnit(r: { anchor_sessions?: number | null; nodes?: RawNode[] | null }): SkUnit {
  return r.anchor_sessions != null || (r.nodes ?? []).some((n) => n.sessions != null) ? 'sessions' : 'users';
}
export const unitCn = (u: SkUnit) => (u === 'sessions' ? '個會話' : '人');
export const fmtUnit = (v: number, u: SkUnit, fmt: (n: number) => string = String) => `${fmt(v)} ${unitCn(u)}`;
export const skNodes = (ns: RawNode[], u: SkUnit): SkNode[] => ns.map(({ level, node, sessions, users }) => ({ level, node, value: (u === 'sessions' ? sessions : users) ?? 0 }));
export const skLinks = (ls: RawLink[], u: SkUnit): SkLink[] => ls.map(({ level, from, to, sessions, users }) => ({ level, from, to, value: (u === 'sessions' ? sessions : users) ?? 0 }));

/** 節點名 → 顏色 token：依「跨層總會話數」排序取前 8 個類別色，其餘與離開／其他用灰。 */
export function nodeColors(nodes: SkNode[]): Record<string, string> {
  const tot = new Map<string, number>();
  for (const n of nodes) tot.set(n.node, (tot.get(n.node) ?? 0) + n.value);
  const order = Array.from(tot.entries()).filter(([k]) => !GRAY_NODES.has(k)).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([k]) => k);
  const out: Record<string, string> = {};
  order.forEach((k, i) => { out[k] = i < 8 ? `var(--s${i + 1})` : 'var(--axis)'; });
  for (const g of GRAY_NODES) out[g] = 'var(--axis)';
  return out;
}

export function layout(nodes: SkNode[], links: SkLink[], W = 900, H = 420, nodeW = 14, gap = 8, padX = 8): Layout {
  const levels = nodes.length ? Math.max(...nodes.map((n) => n.level)) + 1 : 0;
  if (!levels) return { nodes: [], links: [], levels: 0, scale: 0 };
  const byLevel: SkNode[][] = Array.from({ length: levels }, () => []);
  for (const n of nodes) byLevel[n.level].push(n);
  for (const col of byLevel) col.sort((a, b) => b.value - a.value || (a.node < b.node ? -1 : 1));
  const maxTotal = Math.max(...byLevel.map((col) => col.reduce((s, n) => s + n.value, 0)));
  const maxCount = Math.max(...byLevel.map((col) => col.length));
  const scale = (H - gap * Math.max(0, maxCount - 1)) / Math.max(1, maxTotal);
  const colors = nodeColors(nodes);
  const colX = (lv: number) => padX + (levels === 1 ? 0 : (lv * (W - 2 * padX - nodeW)) / (levels - 1));
  const laid: LaidNode[] = []; const pos = new Map<string, LaidNode>();
  byLevel.forEach((col, lv) => {
    let y = 0;
    for (const n of col) { const h = Math.max(2, n.value * scale); const ln = { ...n, x: colX(lv), y, h, color: colors[n.node] }; laid.push(ln); pos.set(`${lv}|${n.node}`, ln); y += h + gap; }
  });
  // 連線：每個節點的出線依目標會話數排、入線依來源會話數排，各自累加偏移
  const outOff = new Map<string, number>(); const inOff = new Map<string, number>();
  const sorted = [...links].sort((a, b) => a.level - b.level || b.value - a.value || (a.from < b.from ? -1 : 1) || (a.to < b.to ? -1 : 1));
  const laidLinks: LaidLink[] = [];
  for (const l of sorted) {
    const a = pos.get(`${l.level}|${l.from}`); const b = pos.get(`${l.level + 1}|${l.to}`);
    if (!a || !b) continue;
    const w = Math.max(1, l.value * scale);
    const ko = `${l.level}|${l.from}`; const ki = `${l.level + 1}|${l.to}`;
    const y1 = a.y + (outOff.get(ko) ?? 0) + w / 2; const y2 = b.y + (inOff.get(ki) ?? 0) + w / 2;
    outOff.set(ko, (outOff.get(ko) ?? 0) + w); inOff.set(ki, (inOff.get(ki) ?? 0) + w);
    const x1 = a.x + nodeW; const x2 = b.x; const cx = (x1 + x2) / 2;
    laidLinks.push({ ...l, w, color: a.color, d: `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}` });
  }
  return { nodes: laid, links: laidLinks, levels, scale };
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} 秒`;
  if (s < 3600) return `${(s / 60).toFixed(1)} 分`;
  return `${(s / 3600).toFixed(1)} 時`;
}
