// 歸因分析：轉化目標（事件、條件、價值欄位）× 待歸因觸點（事件、別名、條件、自訂權重）× 拆分維度 × 多模型（首次／末次／線性／位置／時間衰減／自訂）× 回溯窗口
// → POST /v1/analytics/attribution → 模型對比表（轉化數、占比、價值）+ 各模型占比長條 + 觸點 → 轉化者用戶列表。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ActionIcon, Alert, Badge, Button, Checkbox, Code, Group, List, NativeSelect, Paper, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle, StatTile } from '@/ds';
import { dedupe } from './FunnelPage';
import { LineChart } from './LineChart';
import { fmtNum, type Series } from './series';
import type { AttributionModel, AttributionQuery, AttributionResult, AttributionTouch, Catalog, Cond } from './types';

export function AttributionPage() {
  return <RequireGrant module="attribution" action="use"><AttributionInner /></RequireGrant>;
}

export const MODEL_CN: Record<AttributionModel, string> = { first: '首次', last: '末次', linear: '線性', position: '位置（U 型）', decay: '時間衰減', custom: '自訂權重' };
function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
const opt = (value: string, label: string) => ({ value, label });
const WARN_ROW_BG = 'var(--mantine-color-orange-light)';   // 抑制列底色，跟著色彩模式走

/** 結果 → 各模型占比長條序列（x = 觸點[·拆分]，一個模型一條序列）。純函式，有單元測試。 */
export function shareSeries(res: AttributionResult): Series[] {
  return res.models.map((m) => ({ key: m, label: MODEL_CN[m], metric: m, group: '', points: res.rows.map((r) => ({ x: r.split ? `${r.touch} · ${r.split}` : r.touch, y: r.suppressed ? null : (r.models[m]?.share ?? null) === null ? null : Math.round((r.models[m].share as number) * 1000) / 10, suppressed: r.suppressed })) }));
}

function AttributionInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<AttributionQuery>({ target: { event: '' }, touches: [{ event: '' }], models: ['first', 'last', 'linear'], position_weights: { first: 0.4, last: 0.4 }, half_life_days: 7, window_days: 7, filters: [], time: { from: today(-30), to: today() } });
  const [res, setRes] = useState<AttributionResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const bm = useBookmarkOpen<typeof q>('attribution', setQ);
  const [metric, setMetric] = useState<'conversions' | 'value'>('conversions');
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  const setTouch = (i: number, t: Partial<AttributionTouch>) => setQ({ ...q, touches: q.touches.map((x, j) => (j === i ? { ...x, ...t } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try { const body = { ...q, filters: q.filters?.length ? q.filters : undefined, split: q.split || undefined, touches: q.touches.map((t) => ({ ...t, alias: t.alias || undefined })) }; setRes(await api.post<AttributionResult>('/v1/analytics/attribution', body)); setSent(body); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); } finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const ready = !!q.target.event && q.touches.length > 0 && q.touches.every((t) => t.event) && q.models.length > 0;
  const splitOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.type !== 'number' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn }))] : []);
  const series = useMemo(() => (res ? shareSeries(res) : []), [res]);
  const evCn = (n: string) => cat?.events.find((e) => e.name === n)?.cn ?? n;
  const toggleModel = (m: AttributionModel) => setQ({ ...q, models: q.models.includes(m) ? q.models.filter((x) => x !== m) : [...q.models, m] });
  const eventOpts = [opt('', '選事件…'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))];
  return (
    <div className="page">
      <PageHeader title="歸因分析" lead={`把每次轉化分給轉化前窗口內的觸點：首次、末次、線性、位置（U 型）、時間衰減、自訂權重，可同時比較；跨 Web／App 靠 OneID 歸戶。被歸到的轉化用戶少於 ${k} 人的觸點會被抑制。`} />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)' }}>
          <Paper>
            <Stack gap="md">
              <div>
                <SectionTitle title="轉化目標" />
                <Group gap="xs" wrap="wrap">
                  <NativeSelect style={{ flex: 1, minWidth: 200 }} value={q.target.event} aria-label="轉化事件" data={eventOpts} onChange={(e) => setQ({ ...q, target: { ...q.target, event: e.target.value } })} />
                  <NativeSelect w={180} value={q.target.value_field ?? ''} aria-label="價值欄位" data={[opt('', '每次轉化計 1'), ...(cat?.event_columns ?? []).filter((c) => c.type === 'number' && c.name !== '$event_hour').map((c) => opt(c.name, `價值 = ${c.cn}`))]} onChange={(e) => setQ({ ...q, target: { ...q.target, value_field: e.target.value || undefined } })} />
                </Group>
              </div>
              <div>
                <SectionTitle title="待歸因觸點" right={q.touches.length < 10 && <Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, touches: [...q.touches, { event: '' }] })}>＋ 觸點</Button>} />
                <Stack gap="xs">{q.touches.map((t, i) => (
                  <Group gap="xs" wrap="wrap" key={i}>
                    <Badge color="brand" variant="filled" circle>{i + 1}</Badge>
                    <NativeSelect style={{ flex: 1, minWidth: 200 }} value={t.event} aria-label={`觸點 ${i + 1} 事件`} data={eventOpts} onChange={(e) => setTouch(i, { event: e.target.value })} />
                    <TextInput w={110} placeholder="別名" aria-label={`觸點 ${i + 1} 別名`} value={t.alias ?? ''} onChange={(e) => setTouch(i, { alias: e.target.value })} />
                    {q.models.includes('custom') && <TextInput type="number" min={0} step={0.1} w={80} aria-label={`觸點 ${i + 1} 權重`} value={t.weight ?? 1} onChange={(e) => setTouch(i, { weight: Number(e.target.value) })} />}
                    <TouchFilter cat={cat} touch={t} onChange={(f) => setTouch(i, { filters: f })} />
                    {q.touches.length > 1 && <ActionIcon color="red" aria-label={`刪除觸點 ${i + 1}`} onClick={() => setQ({ ...q, touches: q.touches.filter((_, j) => j !== i) })}>✕</ActionIcon>}
                  </Group>))}</Stack>
              </div>
              <div>
                <SectionTitle title="模型" />
                <Group gap="md" wrap="wrap">{(Object.keys(MODEL_CN) as AttributionModel[]).map((m) => <Checkbox key={m} label={MODEL_CN[m]} checked={q.models.includes(m)} onChange={() => toggleModel(m)} />)}</Group>
              </div>
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <TextInput label="回溯窗口（天）" type="number" min={1} max={90} aria-label="回溯窗口" value={q.window_days} onChange={(e) => setQ({ ...q, window_days: Number(e.target.value) || 7 })} />
                <TextInput label="位置：首次占" type="number" min={0} max={1} step={0.05} disabled={!q.models.includes('position')} value={q.position_weights?.first ?? 0.4} onChange={(e) => setQ({ ...q, position_weights: { first: Number(e.target.value), last: q.position_weights?.last ?? 0.4 } })} />
                <TextInput label="位置：末次占" type="number" min={0} max={1} step={0.05} disabled={!q.models.includes('position')} value={q.position_weights?.last ?? 0.4} onChange={(e) => setQ({ ...q, position_weights: { first: q.position_weights?.first ?? 0.4, last: Number(e.target.value) } })} />
                <TextInput label="半衰期（天）" type="number" min={0.1} step={0.5} disabled={!q.models.includes('decay')} value={q.half_life_days ?? 7} onChange={(e) => setQ({ ...q, half_life_days: Number(e.target.value) || 7 })} />
                <NativeSelect label="觸點拆分" value={q.split ?? ''} aria-label="拆分" data={[opt('', '不拆分'), ...splitOptions.map((o) => opt(o.v, o.cn))]} onChange={(e) => setQ({ ...q, split: e.target.value || undefined })} />
                <TextInput label="分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />
                <TextInput label="轉化開始" type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} />
                <TextInput label="轉化結束" type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} />
              </SimpleGrid>
              <Group gap="xs">
                <Button variant="filled" disabled={!ready || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</Button>
                {res && <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>}
                {res && <SaveCardButton kind="attribution" query={sent} />}
                {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
              </Group>
            </Stack>
          </Paper>
          <Paper><SectionTitle title="說明" />
            <List size="sm" spacing="xs" c="dimmed">
              <List.Item>每次轉化往前看窗口內的觸點：首次給第一個、末次給最後一個、線性平分、位置首末各占設定比例、時間衰減依半衰期、自訂依觸點權重正規化。</List.Item>
              <List.Item>沒有任何觸點的轉化計入「未觸及」，不分給任何觸點。</List.Item>
              <List.Item>價值欄位給了就同時算歸因價值（例：成交金額）。</List.Item>
              <List.Item>點表格的觸點可下鑽該觸點的轉化者用戶列表。</List.Item>
            </List></Paper>
        </SimpleGrid>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            <SimpleGrid cols={{ base: 2, sm: 4 }}>
              <StatTile label="轉化次數" value={fmtNum(res.summary.conversions)} hint={`${fmtNum(res.summary.converters)} 人`} />
              <StatTile label="有觸點的轉化" value={fmtNum(res.summary.touched)} hint={`未觸及 ${fmtNum(res.summary.untouched)}`} />
              <StatTile label="平均觸點數" value={res.summary.avg_touches === null ? '—' : res.summary.avg_touches.toFixed(2)} />
              {q.target.value_field && <StatTile label="轉化價值" value={fmtNum(res.summary.value)} />}
            </SimpleGrid>
            <Paper>
              <SectionTitle title="各模型貢獻占比（%）"><Text size="xs" c="dimmed">{res.cost.ms} ms · {res.rows.length} 個觸點{q.split ? ' × 拆分值' : ''}</Text></SectionTitle>
              <LineChart series={series} kind="bar" />
            </Paper>
            <Paper p={0} style={{ overflow: 'hidden' }}>
              <Group gap="xs" p="sm" pb={0}><Text size="xs" c="dimmed">顯示</Text><NativeSelect w={160} value={metric} aria-label="顯示指標" data={[opt('conversions', '歸因轉化數'), { value: 'value', label: '歸因價值', disabled: !q.target.value_field }]} onChange={(e) => setMetric(e.target.value as 'conversions' | 'value')} /></Group>
              <Table.ScrollContainer minWidth={640}>
                <Table>
                  <Table.Thead><Table.Tr><Table.Th>觸點</Table.Th>{q.split && <Table.Th>{splitOptions.find((o) => o.v === q.split)?.cn ?? q.split}</Table.Th>}<Table.Th ta="right">轉化用戶</Table.Th>{res.models.map((m) => <Table.Th key={m} ta="right">{MODEL_CN[m]}</Table.Th>)}{can('users', 'use') && <Table.Th />}</Table.Tr></Table.Thead>
                  <Table.Tbody>{res.rows.map((r, i) => (
                    <Table.Tr key={i} bg={r.suppressed ? WARN_ROW_BG : undefined}>
                      <Table.Td><Text fw={600} size="sm">{r.touch}</Text><Text size="xs" c="dimmed" ff="monospace">{r.event}</Text></Table.Td>{q.split && <Table.Td>{r.split || '（空）'}</Table.Td>}
                      <Table.Td ta="right"><Mono>{r.suppressed ? `< ${k}` : fmtNum(r.users)}</Mono></Table.Td>
                      {res.models.map((m) => { const v = r.models[m]; return <Table.Td key={m} ta="right"><Mono>{r.suppressed || !v ? '—' : metric === 'value' ? fmtNum(v.value) : `${fmtNum(v.conversions, 1)}${v.share !== null ? ` (${(v.share * 100).toFixed(1)}%)` : ''}`}</Mono></Table.Td>; })}
                      {can('users', 'use') && <Table.Td ta="right">{!r.suppressed && <Button size="compact-sm" variant="subtle" onClick={() => nav('/users', { state: { source: { type: 'attribution', query: q, touch: r.touch, group: r.split ?? undefined, label: `歸因「${evCn(q.target.event)}」觸點 ${r.touch}${r.split ? ` · ${r.split}` : ''} 的轉化者` } } })}>用戶</Button>}</Table.Td>}
                    </Table.Tr>))}</Table.Tbody>
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

function TouchFilter({ cat, touch, onChange }: { cat: Catalog | null; touch: AttributionTouch; onChange: (f: Cond[] | undefined) => void }) {
  const fields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn, type: d.type }))] : []);
  const f = touch.filters?.[0];
  if (!f) return <Button size="compact-sm" variant="subtle" onClick={() => onChange([{ field: 'platform', op: 'eq', value: '' }])}>＋ 條件</Button>;
  const cur = fields.find((x) => x.v === f.field); const ops = cat?.ops[cur?.type === 'number' ? 'number' : cur?.type === 'bool' ? 'bool' : 'string'] ?? [];
  return <>
    <NativeSelect w={140} value={f.field} aria-label="觸點條件欄位" data={fields.map((x) => opt(x.v, x.cn))} onChange={(e) => onChange([{ field: e.target.value, op: 'eq', value: '' }])} />
    <NativeSelect w={100} value={f.op} aria-label="觸點條件運算子" data={ops.map((o) => opt(o, o))} onChange={(e) => onChange([{ ...f, op: e.target.value }])} />
    {!['set', 'nset', 'true', 'false'].includes(f.op) && <TextInput w={120} aria-label="觸點條件值" value={String(f.value ?? '')} onChange={(e) => onChange([{ ...f, value: cur?.type === 'number' ? Number(e.target.value) : e.target.value }])} />}
    <ActionIcon color="red" aria-label="移除觸點條件" onClick={() => onChange(undefined)}>✕</ActionIcon>
  </>;
}
