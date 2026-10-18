// 事件分析：多指標（事件 × 聚合 × 欄位）、全局篩選、分組（≤3）、粒度、時間、對比上期、Top N → POST /v1/analytics/events。
// 結果：時間粒度 → 折線；合計 + 分組 → 長條；合計不分組 → 數字卡；表格；SQL／血緣可展開；K 匿名抑制以空心點與「< K」呈現。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ActionIcon, Alert, Button, Code, Group, NativeSelect, Paper, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { dedupe } from './FunnelPage';
import { LineChart } from './LineChart';
import { fmtNum, groupLabel, toSeries } from './series';
import type { Catalog, Cond, EventsMetric, EventsQuery, EventsResult } from './types';

const AGG_CN: Record<string, string> = { total: '總次數', users: '觸發用戶數', avg_per_user: '人均次數', uniq: '去重數', sum: '數值求和', avg: '數值均值', avg_val: '數值人均值', max: '最大值', min: '最小值', p25: '下四分位', p50: '中位數', p75: '上四分位', p90: 'P90', mau: 'MAU（按天）' };
const NUM_AGGS = new Set(['sum', 'avg', 'avg_val', 'max', 'min', 'p25', 'p50', 'p75', 'p90']);
const GRAINS: [EventsQuery['time']['grain'], string][] = [['minute', '分鐘'], ['hour', '小時'], ['day', '日'], ['week', '週'], ['month', '月'], ['total', '合計']];

export function EventsPage() {
  return <RequireGrant module="events" action="use"><EventsInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
export const opt = (value: string, label: string) => ({ value, label });
/** 抑制列（人數少於 K）的底色：跟著色彩模式走。 */
export const WARN_ROW_BG = 'var(--mantine-color-orange-light)';
/** 多選的原生 select 需要比單行高。 */
export const MULTI_STYLES = { input: { height: 'auto', minHeight: 88, paddingBlock: 4 } } as const;

type FilterField = { v: string; cn: string; type: string };
/** 全局篩選一列：欄位 × 運算子 × 值（事件分析、行為路徑共用）。 */
export function FilterRow({ f, fields, cat, onChange, onRemove }: { f: Cond; fields: FilterField[]; cat: Catalog | null; onChange: (c: Cond) => void; onRemove: () => void }) {
  const cur = fields.find((x) => x.v === f.field); const ops = cat?.ops[cur?.type === 'number' ? 'number' : cur?.type === 'bool' ? 'bool' : 'string'] ?? [];
  return (
    <Group gap="xs" wrap="wrap">
      <NativeSelect w={200} value={f.field} aria-label="篩選欄位" data={fields.map((x) => opt(x.v, x.cn))} onChange={(e) => onChange({ ...f, field: e.target.value, op: 'eq', value: '' })} />
      <NativeSelect w={110} value={f.op} aria-label="運算子" data={ops.map((o) => opt(o, o))} onChange={(e) => onChange({ ...f, op: e.target.value })} />
      {!['set', 'nset', 'true', 'false'].includes(f.op) && <TextInput w={160} aria-label="值" value={String(f.value ?? '')} placeholder={['in', 'nin'].includes(f.op) ? '逗號分隔' : ''} onChange={(e) => onChange({ ...f, value: cur?.type === 'number' && !['in', 'nin', 'between'].includes(f.op) ? Number(e.target.value) : ['in', 'nin', 'between'].includes(f.op) ? e.target.value.split(',').map((s) => (cur?.type === 'number' ? Number(s) : s.trim())) : e.target.value })} />}
      <ActionIcon color="red" aria-label="刪除條件" onClick={onRemove}>✕</ActionIcon>
    </Group>
  );
}

function EventsInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<EventsQuery>({ metrics: [{ event: '', agg: 'total' }], filters: [], group_by: [], time: { from: today(-14), to: today(), grain: 'day' } });
  const [res, setRes] = useState<EventsResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<EventsQuery | null>(null);
  const bm = useBookmarkOpen<typeof q>('events', setQ);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);

  const setMetric = (i: number, m: Partial<EventsMetric>) => setQ({ ...q, metrics: q.metrics.map((x, j) => (j === i ? { ...x, ...m } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const body: EventsQuery = { ...q, metrics: q.metrics.map((m) => ({ ...m, event: m.event || null, field: NUM_AGGS.has(m.agg) || m.agg === 'uniq' ? m.field : undefined })), filters: q.filters?.length ? q.filters : undefined, group_by: q.group_by?.length ? q.group_by : undefined };
      setRes(await api.post<EventsResult>('/v1/analytics/events', body)); setSent(body);
    } catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const aliases = q.metrics.map((m, i) => m.alias || `m${i + 1}`);
  const labels = Object.fromEntries(q.metrics.map((m, i) => [m.alias || `m${i + 1}`, `${m.event || '任一事件'} ${AGG_CN[m.agg]}${m.field ? `(${m.field})` : ''}`]));
  const series = useMemo(() => (res ? toSeries(res, aliases, labels) : []), [res]);   // eslint-disable-line react-hooks/exhaustive-deps
  const numFields = cat ? [...cat.event_columns.filter((c) => c.type === 'number'), { name: 'props.score', cn: '屬性 score', type: 'number' }] : [];
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  const filterFields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn, type: d.type })), ...cat.user_props.filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}`, type: u.type === 'enum' ? 'string' : u.type }))] : []);
  const isTotal = q.time.grain === 'total';
  const shownCols = res ? res.columns.filter((c) => c !== '_k' && c !== 'suppressed') : [];
  const colCn = (c: string) => (c === 'bucket' ? '時間' : c.startsWith('g') && c.length === 2 ? (q.group_by?.[Number(c[1]) - 1] ?? c) : labels[c] ?? (c.endsWith('_prev') ? `${labels[c.slice(0, -5)] ?? c}（上期）` : c));
  return (
    <div className="page">
      <PageHeader title="事件分析" lead={`依事件、屬性、用戶分群交叉分析次數、人數、金額等指標。預設不抽樣、精確去重；分組人數少於 ${k} 人的列會被抑制。`} />
      <Stack gap="md">
        <Paper>
          <Stack gap="md">
            <div>
              <SectionTitle title="指標" right={q.metrics.length < 10 && <Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, metrics: [...q.metrics, { event: '', agg: 'total' }] })}>＋ 指標</Button>} />
              <Stack gap="xs">
                {q.metrics.map((m, i) => (
                  <Group gap="xs" wrap="wrap" key={i}>
                    <StatusBadge tone="blue">{m.alias || `m${i + 1}`}</StatusBadge>
                    <NativeSelect style={{ flex: 1, minWidth: 200 }} value={m.event ?? ''} aria-label={`指標 ${i + 1} 事件`} data={[opt('', '任一事件'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))]} onChange={(e) => setMetric(i, { event: e.target.value })} />
                    <NativeSelect w={150} value={m.agg} aria-label={`指標 ${i + 1} 聚合`} data={Object.entries(AGG_CN).map(([v, cn]) => opt(v, cn))} onChange={(e) => setMetric(i, { agg: e.target.value })} />
                    {(NUM_AGGS.has(m.agg) || m.agg === 'uniq') && <NativeSelect w={170} value={m.field ?? ''} aria-label={`指標 ${i + 1} 欄位`} data={[opt('', '選欄位…'), ...(m.agg === 'uniq' ? filterFields.map((f) => ({ name: f.v, cn: f.cn })) : numFields).map((f) => opt(f.name, f.cn))]} onChange={(e) => setMetric(i, { field: e.target.value })} />}
                    <TextInput w={90} placeholder="別名" value={m.alias ?? ''} onChange={(e) => setMetric(i, { alias: e.target.value })} />
                    {q.metrics.length > 1 && <ActionIcon color="red" aria-label={`刪除指標 ${i + 1}`} onClick={() => setQ({ ...q, metrics: q.metrics.filter((_, j) => j !== i) })}>✕</ActionIcon>}
                  </Group>
                ))}
              </Stack>
            </div>
            <div>
              <SectionTitle title="篩選" right={<Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, filters: [...(q.filters ?? []), { field: 'platform', op: 'eq', value: '' }] })}>＋ 條件</Button>} />
              <Stack gap="xs">
                {(q.filters ?? []).map((f, i) => <FilterRow key={i} f={f} fields={filterFields} cat={cat} onChange={(nf) => setQ({ ...q, filters: (q.filters ?? []).map((x, j) => (j === i ? nf : x)) })} onRemove={() => setQ({ ...q, filters: (q.filters ?? []).filter((_, j) => j !== i) })} />)}
              </Stack>
            </div>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <NativeSelect label="分組（最多 3）" multiple styles={MULTI_STYLES} value={q.group_by ?? []} data={dimOptions.map((o) => opt(o.v, o.cn))} onChange={(e) => setQ({ ...q, group_by: Array.from(e.target.selectedOptions).map((o) => o.value).slice(0, 3) })} />
              <NativeSelect label="粒度" value={q.time.grain} data={GRAINS.map(([v, cn]) => opt(v, cn))} onChange={(e) => setQ({ ...q, time: { ...q.time, grain: e.target.value as EventsQuery['time']['grain'] } })} />
              <TextInput label="Top N 分組值" type="number" min={1} value={q.top_n ?? ''} placeholder="全部" onChange={(e) => setQ({ ...q, top_n: e.target.value ? Number(e.target.value) : undefined })} />
              <TextInput label="開始" type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} />
              <TextInput label="結束" type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} />
              <NativeSelect label="對比" value={q.compare ?? ''} data={[opt('', '無'), opt('previous_period', '上一期')]} onChange={(e) => setQ({ ...q, compare: (e.target.value || undefined) as EventsQuery['compare'] })} />
              <NativeSelect label="抽樣" value={q.sample ?? ''} data={[opt('', '不抽樣'), opt('0.5', '1/2'), opt('0.1', '1/10'), opt('0.01', '1/100')]} onChange={(e) => setQ({ ...q, sample: e.target.value ? Number(e.target.value) : undefined })} />
              <TextInput label="分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />
              <NativeSelect label="人數計算" aria-label="人數計算" value={q.estimate ? 'estimate' : 'exact'} data={[opt('exact', '精確（預設）'), opt('estimate', '快速估算（近似值）')]} onChange={(e) => setQ({ ...q, estimate: e.target.value === 'estimate' ? true : undefined })} />
            </SimpleGrid>
            <Group gap="xs">
              <Button variant="filled" disabled={busy || !q.metrics.every((m) => (!NUM_AGGS.has(m.agg) && m.agg !== 'uniq') || m.field)} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</Button>
              {res && <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>}
              {res && <SaveCardButton kind="events" query={sent as Record<string, unknown> | null} />}
              {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
            </Group>
          </Stack>
        </Paper>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            <Paper>
              <SectionTitle title={isTotal ? (q.group_by?.length ? '分組對比' : '合計') : '趨勢'}><Text size="xs" c="dimmed">{res.cost.ms} ms · {res.cost.layer === 'L1' ? '日聚合（物化視圖）' : '明細'}{q.sample ? ` · 抽樣 ${q.sample}` : ' · 不抽樣'} · {res.rows.length} 列</Text></SectionTitle>
              {isTotal && !q.group_by?.length ? (
                <SimpleGrid cols={{ base: 2, sm: 4 }}>{aliases.map((a) => { const r = res.rows[0]; return <StatTile key={a} label={labels[a]} value={r?.suppressed ? `< ${k}` : fmtNum(r?.[a] as number)} hint={q.compare ? `上期 ${fmtNum(r?.[`${a}_prev`] as number)}` : undefined} />; })}</SimpleGrid>
              ) : <LineChart series={series} kind={isTotal ? 'bar' : 'line'} />}
            </Paper>
            <Paper p={0} style={{ overflow: 'hidden' }}>
              <Table.ScrollContainer minWidth={560}>
                <Table>
                  <Table.Thead><Table.Tr>{shownCols.map((c) => <Table.Th key={c} ta={aliases.includes(c) || c.endsWith('_prev') ? 'right' : undefined}>{colCn(c)}</Table.Th>)}</Table.Tr></Table.Thead>
                  <Table.Tbody>{res.rows.slice(0, 500).map((r, i) => {
                    const clickable = can('users', 'use') && !r.suppressed;
                    return (
                      <Table.Tr key={i} bg={r.suppressed ? WARN_ROW_BG : undefined} style={clickable ? { cursor: 'pointer' } : undefined} title={can('users', 'use') ? '點：這個落點的用戶列表' : undefined}
                        onClick={clickable ? () => nav('/users', { state: { source: { type: 'events', query: q, bucket: r.bucket != null ? String(r.bucket) : undefined, group: q.group_by?.length ? q.group_by.map((_, gi) => r[`g${gi + 1}`] ?? null) : undefined, metric: aliases[0], label: `事件分析 ${labels[aliases[0]]}${r.bucket != null ? ` ${String(r.bucket)}` : ''}${q.group_by?.length ? ` · ${q.group_by.map((_, gi) => groupLabel({ [`g${gi + 1}`]: r[`g${gi + 1}`] })).join(' / ')}` : ''}` } } }) : undefined}>
                        {shownCols.map((c) => <Table.Td key={c} ta={typeof r[c] === 'number' ? 'right' : undefined}><Mono>{c === 'bucket' ? String(r[c]) : c.startsWith('g') && c.length === 2 ? groupLabel({ [c]: r[c] }) : r.suppressed ? `< ${k}` : typeof r[c] === 'number' ? fmtNum(r[c] as number) : String(r[c] ?? '—')}</Mono></Table.Td>)}
                      </Table.Tr>);
                  })}</Table.Tbody>
                </Table>
              </Table.ScrollContainer>
              {res.rows.length > 500 && <Text size="sm" c="dimmed" p="sm">只顯示前 500 列；完整結果請匯出（走審批）。</Text>}
            </Paper>
            {showSql && <Paper><SectionTitle title="SQL"><Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text></SectionTitle><Code block>{res.sql}</Code></Paper>}
          </>
        )}
      </Stack>
    </div>
  );
}
