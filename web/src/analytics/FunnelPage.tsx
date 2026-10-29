// 漏斗分析：步驟（事件 + 篩選）、窗口、順序、關聯屬性（業務 ID）、口徑、分組、趨勢、時間 → POST /v1/analytics/funnel。
// 結果：轉化步驟（總轉化率、每步到達／流失、最大流失標記、明細表）與轉化趨勢（總轉化率折線＋每桶明細）；每步可加多條件篩選；SQL 可展開；K 匿名抑制列以「< K」顯示。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ActionIcon, Alert, Badge, Button, Checkbox, Code, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle, StatTile } from '@/ds';
import { DateRangeInput } from '@/ds/DateRange';
import { GroupEditor } from '@/users/RuleBuilder';
import { emptyCond, eventFields, groupOk, pruneGroup, toGroup } from '@/users/rule';
import type { CondGroup, Segment } from '@/users/types';
import { filterFieldsOf } from './EventsPage';
import { funnelCsv, pct, summarize, trend, windowError } from './funnel';
import { FunnelChart } from './FunnelChart';
import { LineChart } from './LineChart';
import { ScopeEditor, cleanScope, scopeOk } from './ScopeEditor';
import type { Catalog, FunnelQuery, FunnelResult, FunnelStep } from './types';

const WINDOW_PRESETS: [number, 'minute' | 'hour' | 'day', string][] = [[30, 'minute', '30 分鐘'], [1, 'day', '1 天'], [3, 'day', '3 天'], [7, 'day', '7 天'], [14, 'day', '14 天'], [30, 'day', '30 天'], [90, 'day', '90 天'], [180, 'day', '180 天'], [365, 'day', '365 天']];

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
  const [segments, setSegments] = useState<{ id: string; name: string }[]>([]);
  const [view, setView] = useState<'steps' | 'trend'>('steps');
  const [customWin, setCustomWin] = useState(false);
  const evFields = useMemo(() => eventFields(cat), [cat]);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  useEffect(() => { if (can('segments', 'view')) api.get<Segment[]>('/v1/segments').then((s) => setSegments(s.filter((x) => x.status === 'active' || x.status === 'draft').map((x) => ({ id: x.id, name: x.name })))).catch(() => setSegments([])); }, [can]);
  const scopeFields = useMemo(() => filterFieldsOf(cat), [cat]);

  const setStep = (i: number, s: Partial<FunnelStep>) => setQ({ ...q, steps: q.steps.map((x, j) => (j === i ? { ...x, ...s } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try { const body = { ...q, steps: q.steps.map((s) => ({ ...s, filters: pruneGroup(toGroup(s.filters as CondGroup | undefined)) })), ...cleanScope(q) }; setRes(await api.post<FunnelResult>('/v1/analytics/funnel', body)); setSent(body); setView(q.trend ? 'trend' : 'steps'); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const maxSteps = cat?.funnel.max_steps ?? 10; const maxWinDays = cat?.funnel.max_window_days ?? 90;   // 上限以後端目錄為準（32 步、365 天）
  const winErr = windowError(q.window, maxWinDays);
  const ready = !winErr && q.steps.length >= 2 && q.steps.every((s) => s.event && groupOk(toGroup(s.filters as CondGroup | undefined))) && scopeOk(q);
  const summaries = useMemo(() => (res ? summarize(res.rows) : []), [res]);
  const trendRows = useMemo(() => (res && sent?.trend ? trend(res.rows) : []), [res, sent]);
  const trendSeries = useMemo(() => Array.from(new Set(trendRows.map((t) => t.key))).map((key) => ({ key: key || 'all', label: key || '總轉化率', metric: 'rate', group: key, points: trendRows.filter((t) => t.key === key).map((t) => ({ x: t.bucket, y: t.total_rate === null ? null : t.total_rate * 100, suppressed: t.total_rate === null })) })), [trendRows]);
  const copyCsv = () => {
    if (!res) return;
    const stepNames = (sent?.steps ?? q.steps).map((s, i) => s.alias || s.event || `步驟 ${i + 1}`);
    const text = view === 'trend'
      ? funnelCsv(['組', '時間', '總轉化率', ...stepNames.flatMap((n) => [n, `${n} 相對上一步`])], trendRows.map((t) => [t.key, t.bucket, t.total_rate, ...t.users.flatMap((u, i) => [u, t.rate_prev[i]])]))
      : funnelCsv(['組', '步驟', '事件', '人數', '相對上一步', '相對第一步', '流失', '流失率'], summaries.flatMap((s) => s.steps.map((x) => [s.key, `${x.step}. ${x.alias}`, x.event, x.suppressed ? `< ${k}` : x.users, x.rate_prev, x.rate_first, x.lost, x.lost_rate])));
    void navigator.clipboard?.writeText(text);
  };
  const k = me?.scope.k_anonymity ?? 20;
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  const eventOpts = [opt('', '選事件…'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))];
  return (
    <div className="page">
      <PageHeader title="漏斗分析" lead={`步驟可含線下事件（開戶、入金、成交）；關聯屬性可用業務 ID（訂單編號、帳戶）把多步串成同一流程；窗口 1 分鐘到 90 天；結果不抽樣，分組人數少於 ${k} 人的列會被抑制。`} />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" className="ds-split">
          <Paper>
            <Stack gap="md">
              <div>
                <SectionTitle title="步驟" right={q.steps.length < maxSteps && <Button size="compact-sm" variant="subtle" onClick={() => setQ({ ...q, steps: [...q.steps, { event: '' }] })}>＋ 步驟</Button>} />
                <Stack gap="xs">
                  {q.steps.map((s, i) => (
                    <Stack gap={4} key={i}>
                      <Group gap="xs" wrap="wrap">
                        <Badge color="brand" variant="filled" circle>{i + 1}</Badge>
                        <NativeSelect style={{ flex: 1, minWidth: 200 }} value={s.event} onChange={(e) => setStep(i, { event: e.target.value })} aria-label={`第 ${i + 1} 步事件`} data={eventOpts} />
                        <TextInput w={120} placeholder="別名" value={s.alias ?? ''} onChange={(e) => setStep(i, { alias: e.target.value })} />
                        {!toGroup(s.filters as CondGroup | undefined).items.length && <Button size="compact-sm" variant="subtle" onClick={() => setStep(i, { filters: { op: 'and', items: [emptyCond()] } })}>＋ 篩選</Button>}
                        {q.steps.length > 2 && <ActionIcon color="red" aria-label={`刪除第 ${i + 1} 步`} onClick={() => setQ({ ...q, steps: q.steps.filter((_, j) => j !== i) })}>✕</ActionIcon>}
                      </Group>
                      {toGroup(s.filters as CondGroup | undefined).items.length > 0 && <Paper p="xs" radius="md" shadow="none" bg="var(--soft)" ml={28} className="rb"><GroupEditor g={toGroup(s.filters as CondGroup | undefined)} fields={evFields} cat={cat} label={`第 ${i + 1} 步篩選 `} onChange={(ng) => setStep(i, { filters: ng })} /></Paper>}
                    </Stack>
                  ))}
                </Stack>
              </div>
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <Stack gap={4}>
                  <NativeSelect label="轉換窗口" value={customWin || !WINDOW_PRESETS.some(([v, u]) => v === q.window.value && u === q.window.unit) ? 'custom' : `${q.window.value}:${q.window.unit}`} data={[...WINDOW_PRESETS.map(([v, u, cn]) => opt(`${v}:${u}`, cn)), opt('custom', '自訂…')]} onChange={(e) => { if (e.target.value === 'custom') { setCustomWin(true); return; } setCustomWin(false); const [v, u] = e.target.value.split(':'); setQ({ ...q, window: { value: Number(v), unit: u as FunnelQuery['window']['unit'] } }); }} />
                  {(customWin || !WINDOW_PRESETS.some(([v, u]) => v === q.window.value && u === q.window.unit)) && (
                    <Group gap={6} wrap="nowrap">
                      <TextInput w={100} type="number" min={1} aria-label="窗口數值" value={q.window.value} error={winErr ?? undefined} onChange={(e) => setQ({ ...q, window: { ...q.window, value: Number(e.target.value) } })} />
                      <NativeSelect w={90} aria-label="窗口單位" value={q.window.unit} data={[opt('minute', '分鐘'), opt('hour', '小時'), opt('day', '天')]} onChange={(e) => setQ({ ...q, window: { ...q.window, unit: e.target.value as FunnelQuery['window']['unit'] } })} />
                    </Group>)}
                </Stack>
                <NativeSelect label="順序" value={q.order} data={[opt('loose', '寬鬆（中間可有其他事件）'), opt('strict', '嚴格（其他步驟事件介入即中斷）')]} onChange={(e) => setQ({ ...q, order: e.target.value as 'loose' | 'strict' })} />
                <NativeSelect label="關聯屬性（業務 ID）" value={q.relation ?? ''} data={[opt('', '不串接（同一用戶即可）'), opt('entity_id', '業務 ID（entity_id）'), opt('props.order_no', '訂單編號（props.order_no）'), opt('symbol', '商品代號')]} onChange={(e) => setQ({ ...q, relation: e.target.value || undefined, metric: e.target.value ? q.metric : 'users' })} />
                <NativeSelect label="口徑" value={q.metric} disabled={!q.relation} data={[opt('users', '用戶數'), opt('times', '流程數（需關聯屬性）')]} onChange={(e) => setQ({ ...q, metric: e.target.value as 'users' | 'times' })} />
                <NativeSelect label="分組" value={q.group_by?.[0] ?? ''} data={[opt('', '不分組'), ...dimOptions.map((o) => opt(o.v, o.cn))]} onChange={(e) => setQ({ ...q, group_by: e.target.value ? [e.target.value] : undefined })} />
                <NativeSelect label="趨勢" value={q.trend ?? ''} data={[opt('', '合計'), opt('day', '按日'), opt('week', '按週'), opt('month', '按月')]} onChange={(e) => setQ({ ...q, trend: (e.target.value || undefined) as FunnelQuery['trend'] })} />
                <DateRangeInput label="期間" value={q.time} onChange={(r) => setQ({ ...q, time: { ...q.time, ...r } })} />
                {segments.length > 0 ? <NativeSelect label="用戶屬於分群" aria-label="分群" value={q.segment ?? ''} data={[opt('', '全體用戶'), ...segments.map((s) => opt(s.id, s.name))]} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} /> : <TextInput label="分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />}
              </SimpleGrid>
              <Checkbox label="限制後續事件在時間區間內" description="不勾：期間只限第一步，後續步驟在窗口內即可（可落在期間之後）；勾選：每一步都要在期間內" checked={!!q.restrict_to_range} onChange={(e) => setQ({ ...q, restrict_to_range: e.currentTarget.checked || undefined })} />
              <ScopeEditor value={q} onChange={(s) => setQ({ ...q, ...s })} cat={cat} fields={scopeFields} segments={segments} note="只作用於進漏斗的用戶" />
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
              <List.Item>期間是第一步發生的範圍；後續步驟只要在窗口內，期間結束後才發生也算（同神策）。勾選「限制後續事件在時間區間內」則每一步都要在期間內。</List.Item>
              <List.Item>關聯屬性指定後，同一用戶不同業務 ID 的事件各算一條流程；「流程數」口徑數流程條數，「用戶數」仍去重。</List.Item>
              <List.Item>線下事件由數據中台批次導入後與 App／Web 事件同表，可直接作為任一步。</List.Item>
              <List.Item>結果附 SQL、血緣與成本；所有查詢寫審計。</List.Item>
            </List></Paper>
        </SimpleGrid>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            <Group gap="xs" wrap="wrap">
              <SegmentedControl size="xs" value={view} onChange={(v) => setView(v as 'steps' | 'trend')} data={[{ value: 'steps', label: '轉化步驟' }, { value: 'trend', label: '轉化趨勢', disabled: !sent?.trend }]} />
              <Button size="compact-xs" variant="subtle" onClick={copyCsv}>複製 CSV</Button>
              <Text size="xs" c="dimmed">{res.cost.ms} ms · {res.cost.layer}{sent?.trend ? '' : ' · 要看轉化趨勢請在「趨勢」選按日／週／月'}</Text>
            </Group>
            {view === 'steps' && summaries.map((s) => (
              <Paper key={s.key}>
                <SectionTitle title={s.key || '整體'} />
                <SimpleGrid cols={{ base: 2, sm: 4 }} mb="sm">
                  <StatTile label="總轉化率" value={s.total_rate === null ? '—' : `${(s.total_rate * 100).toFixed(2)}%`} hint={`第 1 步 → 第 ${s.steps.length} 步`} />
                  <StatTile label={q.metric === 'times' ? '進入流程數' : '進入人數'} value={s.steps[0]?.suppressed ? `< ${k}` : (s.steps[0]?.users ?? 0).toLocaleString()} />
                  <StatTile label="最終到達" value={s.steps[s.steps.length - 1]?.suppressed ? `< ${k}` : (s.steps[s.steps.length - 1]?.users ?? 0).toLocaleString()} />
                  <StatTile label="最大流失" value={s.max_drop_step === null ? '—' : pct(s.max_drop_rate)} hint={s.max_drop_step === null ? undefined : `第 ${s.max_drop_step - 1} → ${s.max_drop_step} 步`} />
                </SimpleGrid>
                <FunnelChart rows={s.steps} k={k} maxDropStep={s.max_drop_step} />
                <Table.ScrollContainer minWidth={640} mt="sm">
                  <Table>
                    <Table.Thead><Table.Tr><Table.Th>步驟</Table.Th><Table.Th>事件</Table.Th><Table.Th ta="right">{q.metric === 'times' ? '流程數' : '到達人數'}</Table.Th><Table.Th ta="right">相對上一步</Table.Th><Table.Th ta="right">相對第一步</Table.Th><Table.Th ta="right">流失</Table.Th><Table.Th ta="right">流失率</Table.Th>{can('users', 'use') && <Table.Th>用戶</Table.Th>}</Table.Tr></Table.Thead>
                    <Table.Tbody>{s.steps.map((r) => (
                      <Table.Tr key={r.step} style={s.max_drop_step === r.step ? { background: 'var(--mantine-color-red-light)' } : undefined}><Table.Td>{r.step}. {r.alias}</Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{r.event}</Text></Table.Td>
                        <Table.Td ta="right"><Mono>{r.suppressed ? `< ${k}` : (r.users ?? 0).toLocaleString()}</Mono></Table.Td>
                        <Table.Td ta="right"><Mono>{pct(r.rate_prev)}</Mono></Table.Td><Table.Td ta="right"><Mono>{pct(r.rate_first)}</Mono></Table.Td>
                        <Table.Td ta="right"><Mono>{r.lost === null ? '—' : r.lost.toLocaleString()}</Mono></Table.Td><Table.Td ta="right"><Mono>{pct(r.lost_rate)}</Mono></Table.Td>
                        {can('users', 'use') && <Table.Td><Group gap={4}>{(['reached', 'lost'] as const).filter((m) => m === 'reached' || r.step > 1).map((m) => <Button key={m} size="compact-sm" variant="subtle" onClick={() => nav('/users', { state: { source: { type: 'funnel', query: { ...q, trend: undefined, cohorts: undefined }, step: r.step, reached: m === 'reached', group: s.key.includes(' · ') ? s.key.split(' · ').pop() : (q.group_by?.length ? s.key : undefined), label: `漏斗「${q.steps.map((st) => st.alias || st.event).join(' → ')}」第 ${r.step} 步${m === 'reached' ? '到達' : '流失'}${s.key ? ` · ${s.key}` : ''}` } } })}>{m === 'reached' ? '到達' : '流失'}</Button>)}</Group></Table.Td>}</Table.Tr>))}</Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              </Paper>
            ))}
            {view === 'trend' && (
              <Paper>
                <SectionTitle title="總轉化率趨勢"><Text size="xs" c="dimmed">每桶 = 該期間做第一步的用戶，之後在窗口內走到最後一步的比例</Text></SectionTitle>
                <LineChart series={trendSeries} kind="line" format="pct2" height={260} />
                <Table.ScrollContainer minWidth={720} mt="sm">
                  <Table>
                    <Table.Thead><Table.Tr>{trendRows.some((t) => t.key) && <Table.Th>組</Table.Th>}<Table.Th>時間</Table.Th><Table.Th ta="right">總轉化率</Table.Th>{(sent?.steps ?? q.steps).map((st, i) => <Table.Th key={i} ta="right">{i + 1}. {st.alias || st.event}{i > 0 ? '（相對上一步）' : ''}</Table.Th>)}</Table.Tr></Table.Thead>
                    <Table.Tbody>{trendRows.map((t) => (
                      <Table.Tr key={`${t.key}|${t.bucket}`}>{trendRows.some((x) => x.key) && <Table.Td>{t.key}</Table.Td>}<Table.Td><Mono>{t.bucket}</Mono></Table.Td><Table.Td ta="right"><Mono>{pct(t.total_rate, 2)}</Mono></Table.Td>
                        {t.users.map((u, i) => <Table.Td key={i} ta="right"><Mono>{u === null ? `< ${k}` : u.toLocaleString()}</Mono>{i > 0 && <Text size="xs" c="dimmed" ta="right">{pct(t.rate_prev[i])}</Text>}</Table.Td>)}
                      </Table.Tr>))}</Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              </Paper>
            )}
            {showSql && <Paper><SectionTitle title="SQL"><Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text></SectionTitle><Code block>{res.sql}</Code></Paper>}
          </>
        )}
      </Stack>
    </div>
  );
}

/** 事件欄位與 dimensions.yaml 可能指到同一欄（platform、channel、symbol）：以第一個為準。 */
export function dedupe<T extends { v: string }>(xs: T[]): T[] { const seen = new Set<string>(); return xs.filter((x) => (seen.has(x.v) ? false : (seen.add(x.v), true))); }

