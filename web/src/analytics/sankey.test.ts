import { describe, expect, it } from 'vitest';
import { fmtMs, fmtUnit, layout, nodeColors, pathUnit, skLinks, skNodes } from './sankey';

const nodes = [{ level: 0, node: 'login', value: 100 }, { level: 1, node: 'view', value: 60 }, { level: 1, node: '離開', value: 40 }, { level: 2, node: 'order', value: 30 }, { level: 2, node: '離開', value: 30 }];
const links = [{ level: 0, from: 'login', to: 'view', value: 60 }, { level: 0, from: 'login', to: '離開', value: 40 }, { level: 1, from: 'view', to: 'order', value: 30 }, { level: 1, from: 'view', to: '離開', value: 30 }];

describe('sankey layout', () => {
  it('assigns fixed categorical colors by total value and gray for exit/other', () => {
    const c = nodeColors(nodes);
    expect(c.login).toBe('var(--s1)'); expect(c.view).toBe('var(--s2)'); expect(c.order).toBe('var(--s3)'); expect(c['離開']).toBe('var(--axis)');
  });
  it('node heights are proportional and columns sum to the tallest level', () => {
    const l = layout(nodes, links, 900, 420, 14, 8);
    expect(l.levels).toBe(3);
    const lv0 = l.nodes.find((n) => n.level === 0)!; const view = l.nodes.find((n) => n.node === 'view')!;
    expect(lv0.h).toBeCloseTo(view.h / 0.6, 5);
    expect(lv0.h).toBeCloseTo(420 - 8, 5);                    // 最高的一層占滿（扣掉 1 個間距）
    expect(l.nodes.filter((n) => n.level === 1).map((n) => n.node)).toEqual(['view', '離開']);
    expect(lv0.x).toBe(8); expect(l.nodes.find((n) => n.level === 2)!.x).toBe(900 - 8 - 14);
  });
  it('links stack without overlap on both ends and carry source color', () => {
    const l = layout(nodes, links, 900, 420, 14, 8);
    const out = l.links.filter((k) => k.from === 'login');
    expect(out.map((k) => k.to)).toEqual(['view', '離開']);
    expect(out[0].w + out[1].w).toBeCloseTo(l.nodes[0].h, 5);
    expect(out[0].color).toBe('var(--s1)'); expect(out[0].d.startsWith('M22,')).toBe(true);
  });
  it('empty input and fmtMs', () => {
    expect(layout([], []).levels).toBe(0);
    expect(fmtMs(null)).toBe('—'); expect(fmtMs(850)).toBe('850 ms'); expect(fmtMs(84000.5)).toBe('1.4 分'); expect(fmtMs(7200000)).toBe('2.0 時');
  });
});

describe('會話／人數單位（新舊格式不混用）', () => {
  const legacy = { anchor_users: 3, nodes: [{ level: 0, node: 'A', users: 3 }, { level: 1, node: 'B', users: 3 }], links: [{ level: 0, from: 'A', to: 'B', users: 3 }] };
  const fresh = { anchor_users: 3, anchor_sessions: 7, nodes: [{ level: 0, node: 'A', sessions: 7, users: 3 }], links: [{ level: 0, from: 'A', to: 'B', sessions: 7 }] };
  it('pathUnit：有 anchor_sessions 或任一節點有 sessions 才是會話，否則是人數', () => {
    expect(pathUnit(fresh)).toBe('sessions'); expect(pathUnit(legacy)).toBe('users');
    expect(pathUnit({ nodes: [{ level: 0, node: 'A', sessions: 1 }] })).toBe('sessions'); expect(pathUnit({})).toBe('users');
  });
  it('舊格式取 users、標「人」；新格式取 sessions、標「個會話」；不把 users 當會話', () => {
    expect(skLinks(legacy.links, pathUnit(legacy))).toEqual([{ level: 0, from: 'A', to: 'B', value: 3 }]);
    expect(fmtUnit(skLinks(legacy.links, pathUnit(legacy))[0].value, pathUnit(legacy))).toBe('3 人');
    expect(skNodes(fresh.nodes, pathUnit(fresh))).toEqual([{ level: 0, node: 'A', value: 7 }]);
    expect(fmtUnit(skLinks(fresh.links, pathUnit(fresh))[0].value, pathUnit(fresh))).toBe('7 個會話');
    expect(skNodes([{ level: 0, node: 'A', users: 3 }], 'sessions')).toEqual([{ level: 0, node: 'A', value: 0 }]);   // 會話口徑下沒有 sessions 不拿 users 充數
  });
});
