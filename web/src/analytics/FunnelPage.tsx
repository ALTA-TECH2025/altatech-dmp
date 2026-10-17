// 漏斗分析：步驟（事件 + 篩選）、窗口、順序、關聯屬性（業務 ID）、口徑、分組、趨勢、時間 → POST /v1/analytics/funnel。
// 圖：自繪 SVG 漏斗（每步一條橫柱，寬度 = 相對第一步）；表：人數、相對上一步、相對第一步；SQL 可展開；K 匿名抑制列以「< K」顯示。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ActionIcon, Alert, Badge, Button, Code, Group, List, NativeSelect, Paper, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle } from '@/ds';
import { FunnelChart } from './FunnelChart';
import type { Catalog, FunnelQuery, FunnelResult, FunnelStep } from './types';

const WINDOW_PRESETS: [number, 'minute' | 'hour' | 'day', string][] = [[30, 'minute', '30 分鐘'], [1, 'day', '1 天'], [3, 'day', '3 天'], [7, 'day', '7 天'], [14, 'day', '14 天'], [30, 'day', '30 天'], [90, 'day', '90 天']];

export function FunnelPage() {
  return <RequireGrant module="funnel" action="use"><FunnelInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
const opt = (value: string, label: string) => ({ value, label });

function FunnelInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<FunnelQuery>({ steps: [{ event: '' }, { event: '' }], window: { value: 7, unit: 'day' }, order: 'loose', metric: 'users', time: { from: today(-30), to: today() } });
  const [res, setRes] = useState<FunnelResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<FunnelQuery | null>(null);
  const bm = useBookmarkOpen<typeof q>('funnel', setQ);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);

  const setStep = (i: number, s: Partial<FunnelStep>) => setQ({ ...q, steps: q.steps.map((x, j) => (j === i ? { ...x, ...s } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try { setRes(await api.post<FunnelResult>('/v1/analytics/funnel', q)); setSent(q); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const ready = q.steps.length >= 2 && q.steps.every((s) => s.event);
  const groups = useMemo(() => {
    if (!res) return [];
    const keys = new Map<string, FunnelResult['rows']>();
    for (const r of res.rows) { const k = `${r.bucket ?? ''}|${r.g1 ?? ''}`; keys.set(k, [...(keys.get(k) ?? []), r]); }
    return Array.from(keys.entries());
  }, [res]);
  const k = me?.scope.k_anonymity ?? 20;
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  const eventOpts = [opt('', '選事件…'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))];
  return (
    <div className="page">
      <PageHeader title="漏斗分析" lead={`步驟可含線下事件（開戶、入金、成交）；關聯屬性可用業務 ID（訂單編號、帳戶）把多步串成同一流程；窗口 1 分鐘到 90 天；結果不抽樣，分組人數少於 ${k} 人的列會被抑制。`} />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)' }}>
          <Paper>
            <Stack gap="md">
              <div>
                <SectionTitle title="步驟" right={q.steps.length < 10 && <Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, steps: [...q.steps, { event: '' }] })}>＋ 步驟</Button>} />
                <Stack gap="xs">
                  {q.steps.map((s, i) => (
                    <Group gap="xs" wrap="wrap" key={i}>
                      <Badge color="brand" variant="filled" circle>{i + 1}</Badge>
                      <NativeSelect style={{ flex: 1, minWidth: 200 }} value={s.event} onChange={(e) => setStep(i, { event: e.target.value })} aria-label={`第 ${i + 1} 步事件`} data={eventOpts} />
                      <TextInput w={120} placeholder="別名" value={s.alias ?? ''} onChange={(e) => setStep(i, { alias: e.target.value })} />
                      <StepFilter cat={cat} step={s} onChange={(f) => setStep(i, { filters: f })} />
                      {q.steps.length > 2 && <ActionIcon color="red" aria-label={`刪除第 ${i + 1} 步`} onClick={() => setQ({ ...q, steps: q.steps.filter((_, j) => j !== i) })}>✕</ActionIcon>}
                    </Group>
                  ))}
                </Stack>
              </div>
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <NativeSelect label="轉換窗口" value={`${q.window.value}:${q.window.unit}`} data={WINDOW_PRESETS.map(([v, u, cn]) => opt(`${v}:${u}`, cn))} onChange={(e) => { const [v, u] = e.target.value.split(':'); setQ({ ...q, window: { value: Number(v), unit: u as FunnelQuery['window']['unit'] } }); }} />
                <NativeSelect label="順序" value={q.order} data={[opt('loose', '寬鬆（中間可有其他事件）'), opt('strict', '嚴格（其他步驟事件介入即中斷）')]} onChange={(e) => setQ({ ...q, order: e.target.value as 'loose' | 'strict' })} />
                <NativeSelect label="關聯屬性（業務 ID）" value={q.relation ?? ''} data={[opt('', '不串接（同一用戶即可）'), opt('entity_id', '業務 ID（entity_id）'), opt('props.order_no', '訂單編號（props.order_no）'), opt('symbol', '商品代號')]} onChange={(e) => setQ({ ...q, relation: e.target.value || undefined, metric: e.target.value ? q.metric : 'users' })} />
                <NativeSelect label="口徑" value={q.metric} disabled={!q.relation} data={[opt('users', '用戶數'), opt('times', '流程數（需關聯屬性）')]} onChange={(e) => setQ({ ...q, metric: e.target.value as 'users' | 'times' })} />
                <NativeSelect label="分組" value={q.group_by?.[0] ?? ''} data={[opt('', '不分組'), ...dimOptions.map((o) => opt(o.v, o.cn))]} onChange={(e) => setQ({ ...q, group_by: e.target.value ? [e.target.value] : undefined })} />
                <NativeSelect label="趨勢" value={q.trend ?? ''} data={[opt('', '合計'), opt('day', '按日'), opt('week', '按週'), opt('month', '按月')]} onChange={(e) => setQ({ ...q, trend: (e.target.value || undefined) as FunnelQuery['trend'] })} />
                <TextInput label="開始" type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} />
                <TextInput label="結束" type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} />
                <TextInput label="分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />
              </SimpleGrid>
              <Group gap="xs">
                <Button variant="filled" disabled={!ready || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</Button>
                {res && <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>}
                {res && <SaveCardButton kind="funnel" query={sent as Record<string, unknown> | null} />}
                {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
              </Group>
            </Stack>
          </Paper>
          <Paper><SectionTitle title="說明" />
            <List size="sm" spacing="xs" c="dimmed">
              <List.Item>每步人數 = 在窗口內依序完成到該步的用戶數（ClickHouse windowFunnel）；分組值取自該用戶第一步事件。</List.Item>
              <List.Item>關聯屬性指定後，同一用戶不同業務 ID 的事件各算一條流程；「流程數」口徑數流程條數，「用戶數」仍去重。</List.Item>
              <List.Item>線下事件由數據中台批次導入後與 App／Web 事件同表，可直接作為任一步。</List.Item>
              <List.Item>結果附 SQL、血緣與成本；所有查詢寫審計。</List.Item>
            </List></Paper>
        </SimpleGrid>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            {groups.map(([key, rows]) => (
              <Paper key={key}>
                <SectionTitle title={key === '|' ? '整體' : key.replace('|', ' · ').replace(/^ · | · $/g, '')}><Text size="xs" c="dimmed">{res.cost.ms} ms · {res.cost.layer}</Text></SectionTitle>
                <FunnelChart rows={rows} k={k} />
                <Table.ScrollContainer minWidth={560} mt="sm">
                  <Table>
                    <Table.Thead><Table.Tr><Table.Th>步驟</Table.Th><Table.Th>事件</Table.Th><Table.Th ta="right">{q.metric === 'times' ? '流程數' : '用戶數'}</Table.Th><Table.Th ta="right">相對上一步</Table.Th><Table.Th ta="right">相對第一步</Table.Th>{can('users', 'use') && <Table.Th>用戶</Table.Th>}</Table.Tr></Table.Thead>
                    <Table.Tbody>{rows.map((r) => (
                      <Table.Tr key={r.step}><Table.Td>{r.step}. {r.alias}</Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{r.event}</Text></Table.Td>
                        <Table.Td ta="right"><Mono>{r.suppressed ? `< ${k}` : (r.users ?? 0).toLocaleString()}</Mono></Table.Td>
                        <Table.Td ta="right"><Mono>{pct(r.rate_prev)}</Mono></Table.Td><Table.Td ta="right"><Mono>{pct(r.rate_first)}</Mono></Table.Td>
                        {can('users', 'use') && <Table.Td><Group gap={4}>{(['reached', 'lost'] as const).filter((m) => m === 'reached' || r.step > 1).map((m) => <Button key={m} size="compact-sm" variant="subtle" onClick={() => nav('/users', { state: { source: { type: 'funnel', query: q, step: r.step, reached: m === 'reached', bucket: r.bucket, group: r.g1, label: `漏斗「${q.steps.map((st) => st.alias || st.event).join(' → ')}」第 ${r.step} 步${m === 'reached' ? '到達' : '流失'}${r.bucket ? `（${r.bucket}）` : ''}${r.g1 != null ? ` · ${r.g1}` : ''}` } } })}>{m === 'reached' ? '到達' : '流失'}</Button>)}</Group></Table.Td>}</Table.Tr>))}</Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              </Paper>
            ))}
            {showSql && <Paper><SectionTitle title="SQL"><Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text></SectionTitle><Code block>{res.sql}</Code></Paper>}
          </>
        )}
      </Stack>
    </div>
  );
}

/** 事件欄位與 dimensions.yaml 可能指到同一欄（platform、channel、symbol）：以第一個為準。 */
export function dedupe<T extends { v: string }>(xs: T[]): T[] { const seen = new Set<string>(); return xs.filter((x) => (seen.has(x.v) ? false : (seen.add(x.v), true))); }

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`);

function StepFilter({ cat, step, onChange }: { cat: Catalog | null; step: FunnelStep; onChange: (f: FunnelStep['filters']) => void }) {
  const f = step.filters?.[0];
  const fields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn, type: d.type }))] : []);
  const cur = fields.find((x) => x.v === f?.field);
  const ops = cat ? cat.ops[cur?.type === 'number' ? 'number' : 'string'] : [];
  return (
    <Group gap={4} wrap="wrap">
      <NativeSelect w={140} value={f?.field ?? ''} aria-label="篩選欄位" data={[opt('', '無篩選'), ...fields.map((x) => opt(x.v, x.cn))]} onChange={(e) => onChange(e.target.value ? [{ field: e.target.value, op: 'eq', value: '' }] : undefined)} />
      {f && <NativeSelect w={100} value={f.op} aria-label="運算子" data={ops.map((o) => opt(o, o))} onChange={(e) => onChange([{ ...f, op: e.target.value }])} />}
      {f && !['set', 'nset'].includes(f.op) && <TextInput w={110} aria-label="值" value={String(f.value ?? '')} onChange={(e) => onChange([{ ...f, value: cur?.type === 'number' ? Number(e.target.value) : e.target.value }])} />}
    </Group>
  );
}
