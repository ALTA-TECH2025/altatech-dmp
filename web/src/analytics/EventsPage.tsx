// 事件分析：多指標（事件 × 聚合 × 欄位 × 指標內篩選）、自定義指標（公式）、細分篩選（且／或條件樹）、分群、分組（≤3）、粒度、時間、對比上期、Top N → POST /v1/analytics/events。
// 結果：圖型可切（折線／面積／長條／堆疊／圓餅／百分比堆疊／表格）、數值格式、明細表（轉置、增長％、佔比％、複製 CSV）；SQL／血緣可展開；K 匿名抑制以空心點與「< K」呈現。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ActionIcon, Alert, Button, Checkbox, Code, Group, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { DateRangeInput } from '@/ds/DateRange';
import { GroupEditor } from '@/users/RuleBuilder';
import { countConds, emptyCond, eventFields, groupOk, pruneGroup, toGroup, type FieldOpt } from '@/users/rule';
import type { CondGroup } from '@/users/types';
import type { Segment } from '@/users/types';
import { CHART_CN, FORMAT_CN, aliasOk, chartAvailable, fmtValue, formulaError, growthOf, shareOf, tableCsv, transpose, type ChartKind, type NumFormat } from './events';
import { dedupe } from './FunnelPage';
import { LineChart } from './LineChart';
import { groupLabel, toSeries } from './series';
import type { Catalog, Cond, EventsMetric, EventsQuery, EventsResult } from './types';

const AGG_CN: Record<string, string> = { total: '總次數', users: '觸發用戶數', avg_per_user: '人均次數', uniq: '去重數', sum: '數值求和', avg: '數值均值', avg_val: '數值人均值', max: '最大值', min: '最小值', p25: '下四分位', p50: '中位數', p75: '上四分位', p90: 'P90', mau: 'MAU（按天）' };
const NUM_AGGS = new Set(['sum', 'avg', 'avg_val', 'max', 'min', 'p25', 'p50', 'p75', 'p90']);
const GRAINS: [EventsQuery['time']['grain'], string][] = [['minute', '分鐘'], ['hour', '小時'], ['day', '日'], ['week', '週'], ['month', '月'], ['total', '合計']];
type View = 'auto' | ChartKind;

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
/** 全局篩選一列：欄位 × 運算子 × 值（行為路徑沿用；事件分析改用條件樹）。 */
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

/** 細分篩選可選欄位：事件欄位、維度（含枚舉值）、可篩選的用戶屬性。 */
function filterFieldsOf(cat: Catalog | null): FieldOpt[] {
  const t = (x: string | undefined): FieldOpt['type'] => (x === 'number' || x === 'bool' || x === 'date' ? x : 'string');
  return [...eventFields(cat), ...(cat?.user_props ?? []).filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}`, type: t(u.type === 'enum' ? 'string' : u.type), values: u.values?.length ? u.values : undefined }))];
}

function EventsInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [segments, setSegments] = useState<{ id: string; name: string }[] | null>(null);
  const [q, setQ] = useState<EventsQuery>({ metrics: [{ event: '', agg: 'total' }], formulas: [], filters: { op: 'and', items: [] }, group_by: [], time: { from: today(-14), to: today(), grain: 'day' } });
  const [res, setRes] = useState<EventsResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<EventsQuery | null>(null);
  const [view, setView] = useState<View>('auto');
  const [fmt, setFmt] = useState<NumFormat>('auto');
  const [tbl, setTbl] = useState({ transpose: false, growth: false, share: false });
  const bm = useBookmarkOpen<typeof q>('events', setQ);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  useEffect(() => { if (can('segments', 'view')) api.get<Segment[]>('/v1/segments').then((s) => setSegments(s.filter((x) => x.status === 'active' || x.status === 'draft').map((x) => ({ id: x.id, name: x.name })))).catch(() => setSegments([])); else setSegments([]); }, [can]);

  const fg = toGroup(q.filters as CondGroup | Cond[] | undefined);
  const setMetric = (i: number, m: Partial<EventsMetric>) => setQ({ ...q, metrics: q.metrics.map((x, j) => (j === i ? { ...x, ...m } : x)) });
  const aliases = q.metrics.map((m, i) => m.alias || `m${i + 1}`);
  const formulas = q.formulas ?? [];
  const formulaErrs = formulas.map((f) => (!aliasOk(f.alias) ? '別名只能是英數底線' : aliases.includes(f.alias) || formulas.filter((x) => x.alias === f.alias).length > 1 ? '別名與指標或其他公式重複' : formulaError(f.expr, aliases)));
  const metricsOk = q.metrics.every((m) => ((!NUM_AGGS.has(m.agg) && m.agg !== 'uniq') || m.field) && groupOk(toGroup(m.filters as CondGroup | Cond[] | undefined)));
  const ready = metricsOk && formulaErrs.every((e) => !e) && groupOk(fg);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const body: EventsQuery = { ...q, metrics: q.metrics.map((m) => ({ ...m, event: m.event || null, field: NUM_AGGS.has(m.agg) || m.agg === 'uniq' ? m.field : undefined, filters: pruneGroup(toGroup(m.filters as CondGroup | Cond[] | undefined)) })),
        formulas: formulas.length ? formulas : undefined, filters: pruneGroup(fg), group_by: q.group_by?.length ? q.group_by : undefined, segment: q.segment || undefined };
      setRes(await api.post<EventsResult>('/v1/analytics/events', body)); setSent(body);
    } catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const META_COLS = new Set(['bucket', 'g1', 'g2', 'g3', '_k', 'suppressed']);
  const wanted = [...aliases, ...formulas.map((f) => f.alias)];
  const allAliases = res && !wanted.some((a) => res.columns.includes(a)) ? res.columns.filter((c) => !META_COLS.has(c) && !c.endsWith('_prev')) : wanted;   // 結果欄位名與目前表單對不上（書籤用語意指標名、示範模式固定結果）→ 以結果為準
  const labels: Record<string, string> = { ...Object.fromEntries(q.metrics.map((m, i) => [m.alias || `m${i + 1}`, `${m.event || '任一事件'} ${AGG_CN[m.agg]}${m.field ? `(${m.field})` : ''}`])), ...Object.fromEntries(formulas.map((f) => [f.alias, `${f.alias} = ${f.expr}`])) };
  const series = useMemo(() => (res ? toSeries(res, allAliases, labels) : []), [res]);   // eslint-disable-line react-hooks/exhaustive-deps
  const numFields = cat ? [...cat.event_columns.filter((c) => c.type === 'number'), { name: 'props.score', cn: '屬性 score', type: 'number' }] : [];
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  const filterFields = useMemo(() => filterFieldsOf(cat), [cat]);
  const evFields = useMemo(() => eventFields(cat), [cat]);
  const isTotal = q.time.grain === 'total';
  const hasBucket = !!res?.columns.includes('bucket');
  const grouped = !!q.group_by?.length;
  const effective: ChartKind | 'stat' = view !== 'auto' ? view : isTotal ? (grouped ? 'bar' : 'stat') : 'line';
  const shownCols = res ? res.columns.filter((c) => c !== '_k' && c !== 'suppressed') : [];
  const colCn = (c: string) => (c === 'bucket' ? '時間' : c.startsWith('g') && c.length === 2 ? (q.group_by?.[Number(c[1]) - 1] ?? c) : labels[c] ?? (c.endsWith('_prev') ? `${labels[c.slice(0, -5)] ?? c}（上期）` : c));
  const rowLabel = (r: EventsResult['rows'][number]) => [r.bucket != null ? String(r.bucket) : '', grouped ? groupLabel(r) : ''].filter(Boolean).join(' · ') || '合計';
  const growth = useMemo(() => (res && tbl.growth ? growthOf(res.rows, allAliases, hasBucket) : null), [res, tbl.growth]);   // eslint-disable-line react-hooks/exhaustive-deps
  const share = useMemo(() => (res && tbl.share ? shareOf(res.rows, allAliases, grouped) : null), [res, tbl.share]);         // eslint-disable-line react-hooks/exhaustive-deps
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
  const copyTable = () => {
    if (!res) return;
    const t = tbl.transpose ? transpose(res.rows, allAliases, labels, rowLabel) : { head: shownCols.map(colCn), body: res.rows.map((r) => shownCols.map((c) => (c.startsWith('g') && c.length === 2 ? groupLabel({ [c]: r[c] }) : r.suppressed && allAliases.includes(c) ? `< ${k}` : String(r[c] ?? '')))) };
    void navigator.clipboard?.writeText(tableCsv(t.head, t.body));
  };
  return (
    <div className="page">
      <PageHeader title="事件分析" lead={`依事件、屬性、用戶分群交叉分析次數、人數、金額等指標。預設不抽樣、精確去重；分組人數少於 ${k} 人的列會被抑制。`} />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
          <Paper>
            <Stack gap="md">
              <div>
                <SectionTitle title="配置指標" right={q.metrics.length < 10 && <Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, metrics: [...q.metrics, { event: '', agg: 'total' }] })}>＋ 指標</Button>}><Text size="xs" c="dimmed">事件 × 聚合 × 欄位；每個指標可加自己的篩選</Text></SectionTitle>
                <Stack gap="xs">
                  {q.metrics.map((m, i) => { const mf = toGroup(m.filters as CondGroup | Cond[] | undefined); return (
                    <Stack gap={4} key={i}>
                      <Group gap="xs" wrap="wrap">
                        <StatusBadge tone="blue">{m.alias || `m${i + 1}`}</StatusBadge>
                        <NativeSelect style={{ flex: 1, minWidth: 180 }} value={m.event ?? ''} aria-label={`指標 ${i + 1} 事件`} data={[opt('', '任一事件'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))]} onChange={(e) => setMetric(i, { event: e.target.value })} />
                        <NativeSelect w={140} value={m.agg} aria-label={`指標 ${i + 1} 聚合`} data={Object.entries(AGG_CN).map(([v, cn]) => opt(v, cn))} onChange={(e) => setMetric(i, { agg: e.target.value })} />
                        {(NUM_AGGS.has(m.agg) || m.agg === 'uniq') && <NativeSelect w={160} value={m.field ?? ''} aria-label={`指標 ${i + 1} 欄位`} data={[opt('', '選欄位…'), ...(m.agg === 'uniq' ? filterFields.map((f) => ({ name: f.v, cn: f.cn })) : numFields).map((f) => opt(f.name, f.cn))]} onChange={(e) => setMetric(i, { field: e.target.value })} />}
                        <TextInput w={80} placeholder="別名" aria-label={`指標 ${i + 1} 別名`} value={m.alias ?? ''} onChange={(e) => setMetric(i, { alias: e.target.value })} />
                        {!mf.items.length && <Button size="compact-sm" variant="subtle" onClick={() => setMetric(i, { filters: { op: 'and', items: [emptyCond()] } })}>＋ 篩選</Button>}
                        {q.metrics.length > 1 && <ActionIcon color="red" aria-label={`刪除指標 ${i + 1}`} onClick={() => setQ({ ...q, metrics: q.metrics.filter((_, j) => j !== i) })}>✕</ActionIcon>}
                      </Group>
                      {mf.items.length > 0 && <Paper p="xs" radius="md" shadow="none" bg="var(--soft)" ml={28}><GroupEditor g={mf} fields={evFields} cat={cat} label={`指標 ${i + 1} 篩選 `} onChange={(ng) => setMetric(i, { filters: ng })} /></Paper>}
                    </Stack>); })}
                </Stack>
              </div>
              <div>
                <SectionTitle title="自定義指標" right={formulas.length < 5 && <Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, formulas: [...formulas, { alias: `f${formulas.length + 1}`, expr: aliases.length > 1 ? `${aliases[1]} / ${aliases[0]} * 100` : `${aliases[0]} / 100` }] })}>＋ 自定義指標</Button>}><Text size="xs" c="dimmed">指標別名之間的四則運算，例 m2 / m1 * 100</Text></SectionTitle>
                <Stack gap="xs">
                  {formulas.map((f, i) => (
                    <Group gap="xs" wrap="wrap" key={i} align="flex-start">
                      <StatusBadge tone="custom">{f.alias || '?'}</StatusBadge>
                      <TextInput w={90} aria-label={`公式 ${i + 1} 別名`} value={f.alias} onChange={(e) => setQ({ ...q, formulas: formulas.map((x, j) => (j === i ? { ...x, alias: e.target.value } : x)) })} />
                      <Text size="sm" c="dimmed">=</Text>
                      <TextInput style={{ flex: 1, minWidth: 180 }} aria-label={`公式 ${i + 1}`} value={f.expr} placeholder="m2 / m1 * 100" error={formulaErrs[i] ?? undefined} onChange={(e) => setQ({ ...q, formulas: formulas.map((x, j) => (j === i ? { ...x, expr: e.target.value } : x)) })} />
                      <ActionIcon color="red" aria-label={`刪除公式 ${i + 1}`} onClick={() => setQ({ ...q, formulas: formulas.filter((_, j) => j !== i) })}>✕</ActionIcon>
                    </Group>
                  ))}
                </Stack>
              </div>
            </Stack>
          </Paper>
          <Paper>
            <Stack gap="md">
              <div>
                <SectionTitle title="細分篩選"><Text size="xs" c="dimmed">事件屬性、維度、用戶屬性；條件可分組，群組內與群組間都可選「且／或」{countConds(fg) ? `（${countConds(fg)} 條）` : ''}</Text></SectionTitle>
                <div className="rb"><GroupEditor g={fg} fields={filterFields} cat={cat} label="條件 " onChange={(ng) => setQ({ ...q, filters: ng })} /></div>
              </div>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                {segments && segments.length > 0
                  ? <NativeSelect label="用戶屬於分群" aria-label="分群" value={q.segment ?? ''} data={[opt('', '全體用戶'), ...segments.map((s) => opt(s.id, s.name))]} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />
                  : <TextInput label="用戶屬於分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />}
                <NativeSelect label="分組（最多 3）" multiple styles={MULTI_STYLES} value={q.group_by ?? []} data={dimOptions.map((o) => opt(o.v, o.cn))} onChange={(e) => setQ({ ...q, group_by: Array.from(e.target.selectedOptions).map((o) => o.value).slice(0, 3) })} />
                <TextInput label="Top N 分組值" type="number" min={1} value={q.top_n ?? ''} placeholder="全部" onChange={(e) => setQ({ ...q, top_n: e.target.value ? Number(e.target.value) : undefined })} />
                <NativeSelect label="抽樣" value={q.sample ?? ''} data={[opt('', '不抽樣'), opt('0.5', '1/2'), opt('0.1', '1/10'), opt('0.01', '1/100')]} onChange={(e) => setQ({ ...q, sample: e.target.value ? Number(e.target.value) : undefined })} />
                <NativeSelect label="人數計算" aria-label="人數計算" value={q.estimate ? 'estimate' : 'exact'} data={[opt('exact', '精確（預設）'), opt('estimate', '快速估算（近似值）')]} onChange={(e) => setQ({ ...q, estimate: e.target.value === 'estimate' ? true : undefined })} />
              </SimpleGrid>
            </Stack>
          </Paper>
        </SimpleGrid>
        <Paper>
          <Group gap="sm" wrap="wrap" align="flex-end">
            <NativeSelect label="粒度" w={110} value={q.time.grain} data={GRAINS.map(([v, cn]) => opt(v, cn))} onChange={(e) => setQ({ ...q, time: { ...q.time, grain: e.target.value as EventsQuery['time']['grain'] } })} />
            <div style={{ minWidth: 300 }}><DateRangeInput label="期間" value={q.time} onChange={(r) => setQ({ ...q, time: { ...q.time, ...r } })} /></div>
            <NativeSelect label="添加對比" w={140} value={q.compare ?? ''} data={[opt('', '無'), opt('previous_period', '上一期')]} onChange={(e) => setQ({ ...q, compare: (e.target.value || undefined) as EventsQuery['compare'] })} />
            <Button variant="filled" disabled={busy || !ready} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</Button>
            {res && <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>}
            {res && <SaveCardButton kind="events" query={sent as Record<string, unknown> | null} />}
            {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
          </Group>
        </Paper>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            <Paper>
              <SectionTitle title={isTotal ? (grouped ? '分組對比' : '合計') : '趨勢'} right={
                <Group gap="xs" wrap="wrap">
                  <SegmentedControl size="xs" value={view} aria-label="圖型" onChange={(v) => setView(v as View)} data={[{ value: 'auto', label: '自動' }, ...CHART_CN.map(([v, cn]) => ({ value: v, label: cn, disabled: !chartAvailable(v, hasBucket, series) }))]} />
                  <NativeSelect size="xs" aria-label="數值格式" value={fmt} data={FORMAT_CN.map(([v, cn]) => opt(v, cn))} onChange={(e) => setFmt(e.target.value as NumFormat)} />
                </Group>}>
                <Text size="xs" c="dimmed">{res.cost.ms} ms · {res.cost.layer === 'L1' ? '日聚合（物化視圖）' : '明細'}{q.sample ? ` · 抽樣 ${q.sample}` : ' · 不抽樣'} · {res.rows.length} 列</Text>
              </SectionTitle>
              {effective === 'stat' ? (
                <SimpleGrid cols={{ base: 2, sm: 4 }}>{allAliases.map((a) => { const r = res.rows[0]; return <StatTile key={a} label={labels[a]} value={r?.suppressed ? `< ${k}` : fmtValue(r?.[a], fmt)} hint={q.compare ? `上期 ${fmtValue(r?.[`${a}_prev`], fmt)}` : undefined} />; })}</SimpleGrid>
              ) : effective === 'table' ? <Text size="sm" c="dimmed">明細見下表。</Text> : <LineChart series={series} kind={effective} format={fmt} />}
            </Paper>
            <Paper p={0} style={{ overflow: 'hidden' }}>
              <Group gap="md" px="sm" pt="sm" wrap="wrap">
                <Checkbox size="xs" label="行列轉置" checked={tbl.transpose} onChange={(e) => setTbl({ ...tbl, transpose: e.target.checked })} />
                <Checkbox size="xs" label="增長百分比" checked={tbl.growth} disabled={tbl.transpose} onChange={(e) => setTbl({ ...tbl, growth: e.target.checked })} />
                <Checkbox size="xs" label="佔總百分比" checked={tbl.share} disabled={tbl.transpose} onChange={(e) => setTbl({ ...tbl, share: e.target.checked })} />
                <Button size="compact-xs" variant="subtle" onClick={copyTable}>複製 CSV（前 500 列）</Button>
                <Text size="xs" c="dimmed">完整下載請走匯出審批</Text>
              </Group>
              <Table.ScrollContainer minWidth={560}>
                {tbl.transpose ? (() => { const t = transpose(res.rows, allAliases, labels, rowLabel); return (
                  <Table>
                    <Table.Thead><Table.Tr>{t.head.map((h, i) => <Table.Th key={i} ta={i ? 'right' : undefined}>{h}</Table.Th>)}</Table.Tr></Table.Thead>
                    <Table.Tbody>{t.body.map((row, i) => <Table.Tr key={i}>{row.map((c, j) => <Table.Td key={j} ta={j ? 'right' : undefined}><Mono>{j === 0 ? c : c === 'K' ? `< ${k}` : fmtValue(c === '' ? null : Number.isFinite(Number(c)) ? Number(c) : c, fmt)}</Mono></Table.Td>)}</Table.Tr>)}</Table.Tbody>
                  </Table>); })() : (
                  <Table>
                    <Table.Thead><Table.Tr>{shownCols.map((c) => <Table.Th key={c} ta={allAliases.includes(c) || c.endsWith('_prev') ? 'right' : undefined}>{colCn(c)}</Table.Th>)}</Table.Tr></Table.Thead>
                    <Table.Tbody>{res.rows.slice(0, 500).map((r, i) => {
                      const clickable = can('users', 'use') && !r.suppressed;
                      return (
                        <Table.Tr key={i} bg={r.suppressed ? WARN_ROW_BG : undefined} style={clickable ? { cursor: 'pointer' } : undefined} title={can('users', 'use') ? '點：這個落點的用戶列表' : undefined}
                          onClick={clickable ? () => nav('/users', { state: { source: { type: 'events', query: q, bucket: r.bucket != null ? String(r.bucket) : undefined, group: q.group_by?.length ? q.group_by.map((_, gi) => r[`g${gi + 1}`] ?? null) : undefined, metric: aliases[0], label: `事件分析 ${labels[aliases[0]]}${r.bucket != null ? ` ${String(r.bucket)}` : ''}${q.group_by?.length ? ` · ${q.group_by.map((_, gi) => groupLabel({ [`g${gi + 1}`]: r[`g${gi + 1}`] })).join(' / ')}` : ''}` } } }) : undefined}>
                          {shownCols.map((c) => { const ai = allAliases.indexOf(c); return (
                            <Table.Td key={c} ta={typeof r[c] === 'number' ? 'right' : undefined}>
                              <Mono>{c === 'bucket' ? String(r[c]) : c.startsWith('g') && c.length === 2 ? groupLabel({ [c]: r[c] }) : r.suppressed ? `< ${k}` : fmtValue(r[c], fmt)}</Mono>
                              {ai >= 0 && !r.suppressed && (growth || share) && <Text size="xs" c="dimmed" ta="right">{[growth ? `↕ ${pct(growth[i]?.[ai])}` : '', share ? `佔 ${share[i]?.[ai] == null ? '—' : `${(share[i][ai] as number).toFixed(1)}%`}` : ''].filter(Boolean).join(' · ')}</Text>}
                            </Table.Td>); })}
                        </Table.Tr>);
                    })}</Table.Tbody>
                  </Table>)}
              </Table.ScrollContainer>
              {res.rows.length > 500 && !tbl.transpose && <Text size="sm" c="dimmed" p="sm">只顯示前 500 列；完整結果請匯出（走審批）。</Text>}
              {tbl.transpose && res.rows.length > 60 && <Text size="sm" c="dimmed" p="sm">轉置只顯示前 60 欄（共 {res.rows.length} 列）。</Text>}
            </Paper>
            {showSql && <Paper><SectionTitle title="SQL"><Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text></SectionTitle><Code block>{res.sql}</Code></Paper>}
          </>
        )}
      </Stack>
    </div>
  );
}
