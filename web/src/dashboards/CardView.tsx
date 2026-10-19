// 一張卡片：POST /v1/dashboards/{id}/cards/{card}/data（以檢視者身分重算）→ 依 kind／chart 畫圖；錯誤與抑制照分析頁規則。設計系統元件組合。
// 每張卡可臨時改自己的期間與粒度（只在這次瀏覽生效、不寫回看板；後端 time 覆寫：請求 → 看板 preset → 卡片原查詢）。
import { useEffect, useState, type HTMLAttributes } from 'react';
import { Alert, Box, Button, Code, Group, NativeSelect, Paper, SimpleGrid, Table, Text } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { shareSeries } from '@/analytics/AttributionPage';
import { FunnelChart } from '@/analytics/FunnelChart';
import { LineChart } from '@/analytics/LineChart';
import { PathChart } from '@/analytics/PathChart';
import { RetentionMatrix } from '@/analytics/RetentionPage';
import { fmtNum, groupLabel, toSeries } from '@/analytics/series';
import type { AttributionResult, EventsResult, FunnelRow, RetentionRow } from '@/analytics/types';
import { Mono, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { DateRangeInput } from '@/ds/DateRange';
import { presetRange, type DateRange } from '@/ds/daterange';
import { firstGroup, metricLabels, tableColumns, timeText } from './dashboards';
import type { CardData, Dashboard, DashboardCard } from './types';

const GRAINS = [{ value: 'hour', label: '小時' }, { value: 'day', label: '日' }, { value: 'week', label: '週' }, { value: 'month', label: '月' }, { value: 'total', label: '合計' }];

/** 指標卡的值：數字加千分位；區間字串（≥5000000、100000–500000）裡的數字也加千分位。 */
function fmtStat(v: unknown): string {
  if (typeof v === 'number') return fmtNum(v);
  return String(v ?? '—').replace(/\d{5,}/g, (m) => Number(m).toLocaleString());
}

/** 看板數字卡：外層保留 .tile 供端到端測試定位，框線與底色交給 StatTile。 */
function Tile({ label, value }: { label: string; value: string }) {
  return <Box className="tile" style={{ padding: 0, border: 0, background: 'transparent', display: 'block' }}><StatTile label={label} value={value} /></Box>;
}

export function CardView({ dash, card, k, labelOf, reloadKey, onRemove, onRename, onWidth, rootProps }: { dash: Dashboard; card: DashboardCard; k: number; labelOf: (n: string) => string; reloadKey: number; onRemove?: () => void; onRename?: () => void; onWidth?: () => void; rootProps?: HTMLAttributes<HTMLDivElement> }) {
  const [data, setData] = useState<CardData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [showSql, setShowSql] = useState(false);
  const [own, setOwn] = useState<{ from: string; to: string; grain?: string } | null>(null);   // 這張卡自己的期間／粒度（臨時）
  useEffect(() => { setOwn(null); }, [dash.time.preset]);
  useEffect(() => {
    let alive = true; setBusy(true); setErr(null);
    api.post<CardData>(`/v1/dashboards/${dash.id}/cards/${card.id}/data`, own ? { time: own } : undefined).then((d) => { if (alive) setData(d); }).catch((e: Error) => { if (alive) setErr(e instanceof ApiError ? e.message : '查詢失敗'); }).finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [dash.id, card.id, dash.time.preset, reloadKey, own]);
  const range: DateRange = own ?? (data?.time?.from && data.time.to ? { from: data.time.from, to: data.time.to } : presetRange(dash.time.preset));
  const grain = own?.grain ?? String((card.query.time as { grain?: string } | undefined)?.grain ?? data?.time?.grain ?? 'day');
  const warnings = data ? data.warnings.filter((w) => !w.startsWith('plan ')) : [];
  return (
    <Paper className={`dash-card w${card.width}`} {...rootProps}>
      <SectionTitle title={card.title} right={
        <Group gap={4}>
          {data && <Button size="compact-sm" variant="subtle" onClick={() => setShowSql((v) => !v)} title="SQL 與血緣">SQL</Button>}
          {onRename && <Button size="compact-sm" variant="subtle" onClick={onRename}>改名</Button>}
          {onWidth && <Button size="compact-sm" variant="subtle" className="w-toggle" onClick={onWidth} title="切換寬度">{card.width === 2 ? '½ 寬' : '全寬'}</Button>}
          {onRemove && <Button size="compact-sm" variant="subtle" color="red" onClick={onRemove}>移除</Button>}
        </Group>}>
        {card.bookmark && <StatusBadge tone={card.bookmark_deleted ? 'gray' : 'custom'}><span title={card.bookmark_deleted ? '書籤已刪除，顯示刪除前的查詢' : `書籤 ${card.bookmark}`}>{card.bookmark_deleted ? '書籤已刪除' : '書籤'}</span></StatusBadge>}
        <Group gap={4} wrap="wrap" className="card-time">
          <DateRangeInput size="xs" fullWidth={false} ariaLabel={`${card.title} 期間`} value={range} onChange={(r) => setOwn({ ...own, ...r })} />
          {card.kind === 'events' && <NativeSelect size="xs" aria-label={`${card.title} 粒度`} value={grain} data={GRAINS} onChange={(e) => setOwn({ ...range, grain: e.target.value })} />}
          {own && <Button size="compact-xs" variant="subtle" onClick={() => setOwn(null)}>跟看板</Button>}
          <Text size="xs" c="dimmed">{data ? `${own ? '這張卡自訂' : timeText(data.time, dash.time.preset)} · ${data.cost.ms} ms` : busy ? '載入中…' : ''}</Text>
        </Group>
      </SectionTitle>
      {err && <Alert color="red" variant="light" mb="sm">{err}</Alert>}
      {warnings.length > 0 && <Text size="sm" c="dimmed" mb="sm">{warnings.join('；')}</Text>}
      {data && <CardBody card={card} data={data} k={k} labelOf={labelOf} />}
      {card.note && <Text size="sm" c="dimmed" mt="sm">{card.note}</Text>}
      {showSql && data && <Code block mt="sm">{data.lineage.join(' · ')}{'\n'}{data.sql}</Code>}
    </Paper>
  );
}

function CardBody({ card, data, k, labelOf }: { card: DashboardCard; data: CardData; k: number; labelOf: (n: string) => string }) {
  if (card.kind === 'events') {
    const { aliases, labels } = metricLabels(card.query);
    const res = data as unknown as EventsResult;
    const spec = String(data.chart_spec.type ?? 'line');
    const bucketed = data.rows.length > 0 && aliases.some((a) => data.rows.every((r) => r[a] === null || (typeof r[a] === 'string' && !Number.isFinite(Number(r[a])))));   // 遮罩分桶（例：金額 ≥5000000）→ 自動改表格
    const chart = bucketed ? 'table' : card.chart === 'auto' ? spec : card.chart;        // 分桶字串畫不出長條／折線，不管卡片設定一律表格
    if (chart === 'table') return <DataTable data={data} labels={labels} />;
    if (chart === 'stat') {
      const r = data.rows[0] as Record<string, unknown> | undefined;
      return <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">{aliases.map((a) => <Tile key={a} label={labels[a]} value={r?.suppressed ? `< ${k}` : fmtStat(r?.[a])} />)}</SimpleGrid>;
    }
    return <LineChart series={toSeries(res, aliases, labels)} kind={chart === 'bar' || spec === 'bar' ? 'bar' : 'line'} height={220} />;
  }
  if (card.kind === 'sql') {
    const cols = (data.columns ?? Object.keys(data.rows[0] ?? {})).filter((c) => c !== '_k' && c !== 'suppressed');
    const num = cols.slice(1).filter((c) => data.rows.some((r) => typeof r[c] === 'number'));
    const dated = /^\d{4}-\d{2}/.test(String(data.rows[0]?.[cols[0]] ?? ''));                      // 第一欄是日期才畫折線；維度欄畫長條
    const chart = card.chart === 'auto' ? (num.length && data.rows.length > 1 && cols.length <= 4 ? (dated ? 'line' : 'bar') : 'table') : card.chart;
    if (chart === 'line' || chart === 'bar') return <LineChart series={num.slice(0, 3).map((y) => ({ key: y, label: y, metric: y, group: '', points: data.rows.map((r) => ({ x: String(r[cols[0]] ?? ''), y: typeof r[y] === 'number' ? (r[y] as number) : null, suppressed: false })) }))} kind={chart} height={220} />;
    return <DataTable data={data} labels={{}} />;
  }
  if (card.kind === 'funnel') {
    const g = firstGroup(data.rows as unknown as FunnelRow[]);
    return <>{g.groups > 1 && <Text size="sm" c="dimmed" mb="xs">共 {g.groups} 組，只顯示 {g.key}；完整請到漏斗分析。</Text>}<FunnelChart rows={g.rows} k={k} /></>;
  }
  if (card.kind === 'retention') {
    const rows = data.rows as unknown as RetentionRow[];
    const unit = String(card.query.unit ?? 'day') as 'day' | 'week' | 'month';
    return <RetentionMatrix rows={rows.filter((r) => r.g1 == null || r.g1 === rows[0]?.g1)} k={k} display="rate" unit={unit} />;
  }
  if (card.kind === 'path') {
    return <>
      <Text size="xs" c="dimmed">起點用戶 {fmtNum(data.anchor_users ?? 0)}{data.sampled ? `（抽樣 ${data.sampled}）` : ''}</Text>
      <PathChart nodes={data.nodes ?? []} links={data.links ?? []} labelOf={labelOf} />
    </>;
  }
  const res = data as unknown as AttributionResult;
  return <>
    {data.summary && <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm" mb="sm">
      <Tile label="轉化次數" value={fmtNum(data.summary.conversions)} />
      <Tile label="有觸點的轉化" value={fmtNum(data.summary.touched)} />
      <Tile label="轉化價值" value={fmtNum(data.summary.value)} />
    </SimpleGrid>}
    <LineChart series={shareSeries(res)} kind="bar" height={220} />
  </>;
}

function DataTable({ data, labels }: { data: CardData; labels: Record<string, string> }) {
  const cols = tableColumns(data.columns, data.rows);
  return (
    <Table.ScrollContainer minWidth={320}>
      <Table>
        <Table.Thead><Table.Tr>{cols.map((c) => <Table.Th key={c} ta={data.rows.some((r) => typeof r[c] === 'number') ? 'right' : undefined}>{c === 'bucket' ? '時間' : labels[c] ?? c}</Table.Th>)}</Table.Tr></Table.Thead>
        <Table.Tbody>{data.rows.slice(0, 50).map((r, i) => <Table.Tr key={i} bg={r.suppressed ? 'var(--mantine-color-orange-light)' : undefined}>{cols.map((c) => <Table.Td key={c} ta={typeof r[c] === 'number' ? 'right' : undefined}><Mono>{c.startsWith('g') && c.length === 2 ? groupLabel({ [c]: r[c] as string }) : typeof r[c] === 'number' ? fmtNum(r[c] as number) : String(r[c] ?? '—')}</Mono></Table.Td>)}</Table.Tr>)}</Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
