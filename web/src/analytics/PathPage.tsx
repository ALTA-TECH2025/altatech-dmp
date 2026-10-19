// 行為路徑：起點／終點事件（可加條件）、方向、步數、會話切割、節點設定（只留哪些事件／折成其他／依屬性拆分／合併重複／離開節點）、篩選、分群、抽樣、時間
// → POST /v1/analytics/path → 桑基圖（節點可設為新起點、下鑽用戶列表）+ Top N 路徑表（人數、占比、路徑數、平均耗時；K 匿名抑制）。設計系統元件組合。
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ActionIcon, Alert, Badge, Button, Checkbox, Code, Group, List, NativeSelect, Paper, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle } from '@/ds';
import { DateRangeInput } from '@/ds/DateRange';
import { FilterRow, MULTI_STYLES, WARN_ROW_BG, opt } from './EventsPage';
import { dedupe } from './FunnelPage';
import { PathChart } from './PathChart';
import { fmtMs } from './sankey';
import { fmtNum } from './series';
import type { Catalog, PathQuery, PathResult } from './types';

export function PathPage() {
  return <RequireGrant module="path" action="use"><PathInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
const SAMPLES: [string, number | null][] = [['1/10（預設）', 0.1], ['1/2', 0.5], ['1/100', 0.01], ['不抽樣（精確）', null]];

function PathInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<PathQuery>({ anchor: { event: '' }, direction: 'after', steps: 5, session: { mode: 'gap', gap_minutes: 30, max_events: 200 }, nodes: { events: [], collapse_other: false, split: [], merge_repeats: true, exit_node: true }, filters: [], sample: 0.1, top_n: 100, time: { from: today(-30), to: today() } });
  const [res, setRes] = useState<PathResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const bm = useBookmarkOpen<typeof q>('path', setQ);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  const run = async (over?: Partial<PathQuery>) => {
    const body = { ...q, ...over }; setQ(body);
    setBusy(true); setErr(null);
    try { const sendBody = { ...body, filters: body.filters?.length ? body.filters : undefined, nodes: { ...body.nodes, events: body.nodes?.events?.length ? body.nodes.events : undefined, split: body.nodes?.split?.length ? body.nodes.split : undefined } }; setRes(await api.post<PathResult>('/v1/analytics/path', sendBody)); setSent(sendBody); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); } finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const evCn = (n: string) => { const [ev, val] = n.split(':'); const cn = cat?.events.find((e) => e.name === ev)?.cn ?? ev; return val !== undefined ? `${cn}:${val}` : cn; };
  const splitFields = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn }))] : []);
  const filterFields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.user_props.filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}`, type: u.type === 'enum' ? 'string' : u.type }))] : []);
  const setNodes = (n: Partial<NonNullable<PathQuery['nodes']>>) => setQ({ ...q, nodes: { ...q.nodes, ...n } });
  const ready = !!q.anchor.event;
  const anchorOpts = [opt('', '選事件…'), ...(q.direction === 'before' ? [opt('離開', '離開（App 退到背景／會話逾時）')] : []), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))];
  return (
    <div className="page">
      <PageHeader title="行為路徑" lead={`從起點事件往後（或終點事件往前）看用戶 1–10 步走到哪裡；節點就是事件，可依屬性拆分；會話依間隔切，結束補「離開」。預設抽樣 1/10（按用戶分桶），人數少於 ${k} 的路徑會被抑制。`} />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" className="ds-split">
          <Paper>
            <Stack gap="md">
              <div>
                <SectionTitle title={q.direction === 'after' ? '起點' : '終點'} />
                <Group gap="xs" wrap="wrap">
                  <NativeSelect w={160} value={q.direction} aria-label="方向" data={[opt('after', '之後（起點 →）'), opt('before', '之前（→ 終點）')]} onChange={(e) => setQ({ ...q, direction: e.target.value as 'after' | 'before' })} />
                  <NativeSelect style={{ flex: 1, minWidth: 200 }} value={q.anchor.event} aria-label="起點事件" data={anchorOpts} onChange={(e) => setQ({ ...q, anchor: { ...q.anchor, event: e.target.value } })} />
                  <NativeSelect w={100} value={String(q.steps)} aria-label="步數" data={Array.from({ length: 10 }, (_, i) => opt(String(i + 1), `${i + 1} 步`))} onChange={(e) => setQ({ ...q, steps: Number(e.target.value) })} />
                </Group>
              </div>
              <div>
                <SectionTitle title="節點" />
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <NativeSelect label="只把這些事件當節點（空 = 全部）" multiple styles={MULTI_STYLES} aria-label="節點事件" value={q.nodes?.events ?? []} data={(cat?.events ?? []).map((ev) => opt(ev.name, ev.cn))} onChange={(e) => setNodes({ events: Array.from(e.target.selectedOptions).map((o) => o.value) })} />
                  <Stack gap="xs">
                    <Checkbox label="其餘事件折成「其他」（不勾 = 略過）" checked={!!q.nodes?.collapse_other} disabled={!q.nodes?.events?.length} onChange={(e) => setNodes({ collapse_other: e.target.checked })} />
                    <Checkbox label="連續重複節點合併" checked={q.nodes?.merge_repeats !== false} onChange={(e) => setNodes({ merge_repeats: e.target.checked })} />
                    <Checkbox label="會話結束補「離開」節點" checked={q.nodes?.exit_node !== false} onChange={(e) => setNodes({ exit_node: e.target.checked })} />
                    <Group gap="xs" wrap="wrap">
                      <Text size="xs" c="dimmed">依屬性拆分</Text>
                      {(q.nodes?.split ?? []).map((sp, i) => <Group key={i} gap={2}><Badge color="gray">{evCn(sp.event)} × {splitFields.find((f) => f.v === sp.field)?.cn ?? sp.field}</Badge><ActionIcon size="xs" color="red" aria-label={`移除拆分 ${i + 1}`} onClick={() => setNodes({ split: (q.nodes?.split ?? []).filter((_, j) => j !== i) })}>✕</ActionIcon></Group>)}
                      {(q.nodes?.split?.length ?? 0) < 5 && <SplitAdder cat={cat} fields={splitFields} onAdd={(sp) => setNodes({ split: [...(q.nodes?.split ?? []), sp] })} />}
                    </Group>
                  </Stack>
                </SimpleGrid>
              </div>
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <NativeSelect label="會話切割" aria-label="會話切割" value={q.session?.mode ?? 'gap'} data={[opt('gap', '依間隔切會話'), opt('id', '用 session_id'), opt('cross', '跨會話（整段期間）')]} onChange={(e) => setQ({ ...q, session: { ...q.session, mode: e.target.value as 'gap' } })} />
                <TextInput label="會話間隔（分鐘）" type="number" min={1} max={1440} disabled={q.session?.mode !== 'gap'} value={q.session?.gap_minutes ?? 30} onChange={(e) => setQ({ ...q, session: { ...q.session, gap_minutes: Number(e.target.value) || 30 } })} />
                <NativeSelect label="抽樣" aria-label="抽樣" value={String(q.sample)} data={SAMPLES.map(([cn, v]) => opt(String(v), cn))} onChange={(e) => setQ({ ...q, sample: e.target.value === 'null' ? null : Number(e.target.value) })} />
                <DateRangeInput label="期間" value={q.time} onChange={(r) => setQ({ ...q, time: { ...q.time, ...r } })} />
                <TextInput label="分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />
              </SimpleGrid>
              <div>
                <SectionTitle title="篩選" right={<Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, filters: [...(q.filters ?? []), { field: 'platform', op: 'eq', value: '' }] })}>＋ 條件</Button>} />
                <Stack gap="xs">
                  {(q.filters ?? []).map((f, i) => <FilterRow key={i} f={f} fields={filterFields} cat={cat} onChange={(nf) => setQ({ ...q, filters: (q.filters ?? []).map((x, j) => (j === i ? nf : x)) })} onRemove={() => setQ({ ...q, filters: (q.filters ?? []).filter((_, j) => j !== i) })} />)}
                </Stack>
              </div>
              <Group gap="xs">
                <Button variant="filled" disabled={!ready || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</Button>
                {res && <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>}
                {res && <SaveCardButton kind="path" query={sent} />}
                {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
              </Group>
            </Stack>
          </Paper>
          <Paper><SectionTitle title="說明" />
            <List size="sm" spacing="xs" c="dimmed">
              <List.Item>每次出現起點就切一條「起點 + N 步」路徑；同一用戶同會話多次出現各算一條，人數去重。</List.Item>
              <List.Item>「離開」= `$AppEnd` 事件或會話結束（間隔逾時）；終點可直接選「離開」看哪些路徑最常以離開收尾。</List.Item>
              <List.Item>桑基圖節點點一下可設為新起點或下鑽用戶列表；抑制的路徑不畫進圖。</List.Item>
              <List.Item>抽樣按用戶分桶：同一用戶的事件全在或全不在；人數為抽樣後實數。</List.Item>
            </List></Paper>
        </SimpleGrid>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            <Paper>
              <SectionTitle title="路徑圖"><Text size="xs" c="dimmed">{q.direction === 'after' ? '起點' : '終點'}用戶 {fmtNum(res.anchor_users)}{res.sampled ? `（抽樣 ${res.sampled}，推估 ≈ ${fmtNum(Math.round(res.anchor_users / res.sampled))}）` : ''} · {res.cost.ms} ms · {res.paths.length} 條路徑{res.paths.some((p) => p.suppressed) ? `，${res.paths.filter((p) => p.suppressed).length} 條少於 ${k} 人已抑制` : ''}</Text></SectionTitle>
              <PathChart nodes={res.nodes} links={res.links} labelOf={evCn} onNode={(n, action) => {
                if (action === 'anchor') void run({ anchor: { event: n.node }, direction: 'after' });
                else if (can('users', 'use')) nav('/users', { state: { source: { type: 'path', query: q, node: n.node, level: n.level, label: `路徑「${evCn(q.anchor.event)}」第 ${n.level} 步 ${evCn(n.node)}` } } });
              }} />
            </Paper>
            <Paper p={0} style={{ overflow: 'hidden' }}>
              <Table.ScrollContainer minWidth={640}>
                <Table>
                  <Table.Thead><Table.Tr><Table.Th>#</Table.Th><Table.Th>路徑</Table.Th><Table.Th ta="right">人數</Table.Th><Table.Th ta="right">占比</Table.Th><Table.Th ta="right">路徑數</Table.Th><Table.Th ta="right">平均耗時</Table.Th></Table.Tr></Table.Thead>
                  <Table.Tbody>{res.paths.map((p, i) => <Table.Tr key={i} bg={p.suppressed ? WARN_ROW_BG : undefined}><Table.Td><Text size="xs" c="dimmed">{i + 1}</Text></Table.Td><Table.Td>{p.path.map((n, j) => <span key={j}>{j > 0 && <Text component="span" size="xs" c="dimmed" mx={4}>→</Text>}{evCn(n)}</span>)}</Table.Td>
                    <Table.Td ta="right"><Mono>{p.suppressed ? `< ${k}` : fmtNum(p.users)}</Mono></Table.Td><Table.Td ta="right"><Mono>{p.share === null ? '—' : `${(p.share * 100).toFixed(1)}%`}</Mono></Table.Td><Table.Td ta="right"><Mono>{p.suppressed ? '—' : fmtNum(p.sessions)}</Mono></Table.Td><Table.Td ta="right"><Mono>{fmtMs(p.avg_ms)}</Mono></Table.Td></Table.Tr>)}</Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
            {showSql && <Paper><SectionTitle title="SQL"><Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text></SectionTitle><Code block>{res.sql}</Code></Paper>}
          </>
        )}
      </Stack>
    </div>
  );
}

function SplitAdder({ cat, fields, onAdd }: { cat: Catalog | null; fields: { v: string; cn: string }[]; onAdd: (sp: { event: string; field: string }) => void }) {
  const [ev, setEv] = useState(''); const [f, setF] = useState('');
  return <>
    <NativeSelect w={140} value={ev} aria-label="拆分事件" data={[opt('', '事件…'), ...(cat?.events ?? []).map((x) => opt(x.name, x.cn))]} onChange={(e) => setEv(e.target.value)} />
    <NativeSelect w={140} value={f} aria-label="拆分屬性" data={[opt('', '屬性…'), ...fields.map((x) => opt(x.v, x.cn))]} onChange={(e) => setF(e.target.value)} />
    <Button size="compact-sm" variant="subtle" disabled={!ev || !f} onClick={() => { onAdd({ event: ev, field: f }); setEv(''); setF(''); }}>＋ 拆分</Button>
  </>;
}
