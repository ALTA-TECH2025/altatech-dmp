// 日誌統計（docs/10 §8）：/v1/audit/stats 看板（指標卡、每日趨勢、模組／角色、敏感存取 Top、審批、告警規則評估、agent 活動）+ /v1/audit 明細；audit.export 可匯出報表。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Checkbox, Group, List, NativeSelect, Paper, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { AuditStats } from '@/api/types';
import { LineChart } from '@/analytics/LineChart';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { Chart, hbar, type ChartOption } from '@/ds/Chart';
import { EmptyState, Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { DAILY_KEYS, dailySeries, hbars, ruleText, sortAnomalies } from './stats';

type Row = { event: string; at: number; user?: string; module?: string; [k: string]: unknown };
const TILES: { key: keyof AuditStats['tiles']; cn: string; hint?: string }[] = [
  { key: 'active_users', cn: '活躍使用者' }, { key: 'logins', cn: '登入' }, { key: 'login_failed', cn: '登入失敗' }, { key: 'denied', cn: '權限拒絕' },
  { key: 'profile_views', cn: '細查' }, { key: 'reveals', cn: '揭露申請' }, { key: 'exports', cn: '匯出' }, { key: 'queries', cn: '查詢' }, { key: 'approvals_pending', cn: '待審批' }, { key: 'events', cn: '審計事件' },
];
const SEV: Record<string, [string, string]> = { high: ['高', 'red'], medium: ['中', 'amber'], low: ['低', 'gray'] };
const WARN_BG = 'var(--mantine-color-orange-light)';
const Sub = ({ children }: { children: React.ReactNode }) => <Text size="xs" c="dimmed">{children}</Text>;
/** 次數型橫向長條：沿用 hbar 版面，提示框單位改成「次」。 */
const countBars = (rows: { label: string; n: number }[]): ChartOption => ({ ...hbar(rows.map((r) => ({ value: r.label, users: r.n }))), tooltip: { trigger: 'item', formatter: (p) => `${(p as { name: string }).name}：${Number((p as { value: number }).value).toLocaleString()} 次` } });

export function AuditPage() {
  return <RequireGrant module="audit"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth();
  const [days, setDays] = useState(30);
  const [st, setSt] = useState<AuditStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [series, setSeries] = useState<(keyof AuditStats['daily'][number])[]>(['logins', 'denied', 'profile_views']);
  useEffect(() => { setSt(null); api.get<AuditStats>(`/v1/audit/stats?days=${days}`).then(setSt).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [days]);
  const exportCsv = () => { window.open(`/v1/audit/export?days=${days}`, '_blank'); };
  return (
    <div className="page">
      <PageHeader title="日誌統計" lead={<>所有人與 AI 的讀寫都是審計事件；用看板看使用概況、敏感存取、審批與異常。{st?.scope === 'all' ? '你看到的是全平台。' : '你的角色只看到自己的紀錄。'}</>}
        actions={<>
          <NativeSelect aria-label="統計期間" value={String(days)} data={[7, 14, 30, 90].map((d) => ({ value: String(d), label: `近 ${d} 天` }))} onChange={(e) => setDays(Number(e.target.value))} />
          {can('audit', 'export') && <Button onClick={exportCsv}>匯出審計報表</Button>}
          <StatusBadge tone="gray"><span title="問 agent：上週誰細查最多？有沒有非上班時間的敏感存取？">問 agent · 第二階段</span></StatusBadge>
        </>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {!st && !err && <Text size="sm" c="dimmed" ta="center" py="xl">載入中…</Text>}
      {st && <Board st={st} series={series} setSeries={setSeries} />}
      <Detail />
    </div>
  );
}

function Board({ st, series, setSeries }: { st: AuditStats; series: (keyof AuditStats['daily'][number])[]; setSeries: (s: (keyof AuditStats['daily'][number])[]) => void }) {
  const anomalies = sortAnomalies(st.anomalies);
  const mods = useMemo(() => hbars(st.by_module.map((m) => ({ label: m.module, n: m.n }))), [st]);
  const roles = useMemo(() => hbars(st.by_role.map((r) => ({ label: r.role, n: r.profile_views }))), [st]);
  const modOpt = useMemo(() => countBars(mods), [mods]);
  const roleOpt = useMemo(() => countBars(roles), [roles]);
  const types: Record<string, string> = { export: '匯出', segment: '分群', push: '推送', reveal_l2: '揭露 L2', reveal_l3: '揭露 L3', agent_proposal: 'agent', role_change: '角色變更', break_glass: '臨時提權' };
  return (
    <Stack gap="md" mb="md">
      <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }} spacing="sm">{TILES.map((t) => <StatTile key={t.key} label={t.cn} value={st.tiles[t.key].toLocaleString()} />)}</SimpleGrid>
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)' }}>
        <Stack gap="md">
          <Paper><SectionTitle title="每日趨勢" right={<Group gap="sm">{DAILY_KEYS.map((k) => <Checkbox key={k.key} size="xs" label={k.cn} checked={series.includes(k.key)} onChange={(e) => setSeries(e.target.checked ? [...series, k.key] : series.filter((x) => x !== k.key))} />)}</Group>} />
            {st.daily.length ? <LineChart series={dailySeries(st, series)} height={220} /> : <Text size="sm" c="dimmed">期間內沒有審計事件。</Text>}</Paper>
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Paper><SectionTitle title="模組使用次數" right={<Sub>query.executed · users.listed · profile.viewed</Sub>} />{mods.length ? <Chart option={modOpt} height={Math.max(100, mods.length * 24 + 16)} label="模組使用次數" /> : <Text size="sm" c="dimmed">無</Text>}</Paper>
            <Paper><SectionTitle title="細查次數（依角色）" right={<Sub>profile.* · 以登入時角色歸類</Sub>} />{roles.length ? <Chart option={roleOpt} height={Math.max(100, roles.length * 24 + 16)} label="細查次數（依角色）" /> : <Text size="sm" c="dimmed">無</Text>}</Paper>
          </SimpleGrid>
          <Paper><SectionTitle title="審批" right={<Sub>待審 {st.approvals.pending} · 核可 {st.approvals.approved} · 退回 {st.approvals.rejected} · 逾期 {st.approvals.expired}{st.approvals.avg_hours != null ? ` · 平均處理 ${st.approvals.avg_hours} 小時` : ''}</Sub>} />
            {Object.keys(st.approvals.by_type).length ? <Table><Table.Thead><Table.Tr><Table.Th>類型</Table.Th><Table.Th ta="right">待審</Table.Th><Table.Th ta="right">核可</Table.Th><Table.Th ta="right">退回</Table.Th><Table.Th ta="right">逾期</Table.Th><Table.Th ta="right">核准率</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{Object.entries(st.approvals.by_type).map(([t, v]) => { const d = (v.approved ?? 0) + (v.rejected ?? 0); return <Table.Tr key={t}><Table.Td>{types[t] ?? t}</Table.Td><Table.Td ta="right"><Mono>{v.pending ?? 0}</Mono></Table.Td><Table.Td ta="right"><Mono>{v.approved ?? 0}</Mono></Table.Td><Table.Td ta="right"><Mono>{v.rejected ?? 0}</Mono></Table.Td><Table.Td ta="right"><Mono>{v.expired ?? 0}</Mono></Table.Td><Table.Td ta="right"><Mono>{d ? `${Math.round(((v.approved ?? 0) / d) * 100)}%` : '—'}</Mono></Table.Td></Table.Tr>; })}</Table.Tbody></Table> : <Text size="sm" c="dimmed">期間內沒有審批單。</Text>}</Paper>
        </Stack>
        <Stack gap="md">
          <Paper><SectionTitle title="敏感存取 Top" right={<Sub>細查次數 · 主要理由 · 非上班時間</Sub>} />
            {st.top_sensitive.length ? <Table><Table.Thead><Table.Tr><Table.Th>使用者</Table.Th><Table.Th ta="right">次數</Table.Th><Table.Th ta="right">非上班</Table.Th><Table.Th>主要理由</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{st.top_sensitive.map((r) => <Table.Tr key={r.user} bg={r.off_hours ? WARN_BG : undefined}><Table.Td><Text size="xs" ff="monospace">{r.user}</Text></Table.Td><Table.Td ta="right"><Mono>{r.n}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.off_hours || '—'}</Mono></Table.Td><Table.Td><Sub>{r.reason || '—'}</Sub></Table.Td></Table.Tr>)}</Table.Tbody></Table> : <Text size="sm" c="dimmed">期間內沒有細查。</Text>}</Paper>
          <Paper><SectionTitle title="異常與告警" right={<Sub>{anomalies.length} 筆 · 通知 {st.notify.join('、')}</Sub>} />
            {anomalies.length ? <Table><Table.Tbody>{anomalies.slice(0, 12).map((a, i) => <Table.Tr key={i}><Table.Td style={{ whiteSpace: 'nowrap' }}><Text size="xs" ff="monospace">{a.bucket}</Text></Table.Td><Table.Td><Group gap={4}><StatusBadge tone={SEV[a.severity]?.[1] ?? 'gray'}>{SEV[a.severity]?.[0] ?? a.severity}</StatusBadge><Text size="sm">{a.cn}</Text></Group><Text size="xs" c="dimmed" ff="monospace">{a.user} · {a.n} / 門檻 {a.threshold}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table> : <Text size="sm" c="dimmed">{st.scope === 'all' ? '期間內沒有觸發告警規則。' : '告警評估只對全平台檢視者顯示。'}</Text>}</Paper>
          <Paper><SectionTitle title="AI agent 活動">{st.agent.stage === 'phase2' && <StatusBadge tone="gray">第二階段</StatusBadge>}</SectionTitle>
            <SimpleGrid cols={2} spacing="sm"><StatTile label="工具呼叫" value={st.agent.tool_calls} /><StatTile label="提議" value={st.agent.proposals} /></SimpleGrid>
            <Text size="sm" c="dimmed" mt="sm">第一階段 AI 閘道關閉；接上後 agent.tool_called（含委託身份、理由、預算用量）與 evals 通過率會在這裡。</Text></Paper>
          <Paper><SectionTitle title="告警規則" right={<Sub>backend/security/audit_alerts.yaml</Sub>} /><List size="sm" spacing="xs" c="dimmed">{st.rules.map((r) => <List.Item key={r.key}>{ruleText(r)}</List.Item>)}</List><Text size="sm" c="dimmed" mt="sm">上班時間 {st.business_hours.start}:00–{st.business_hours.end}:00（{st.business_hours.tz}{st.business_hours.weekdays_only ? '，週一至週五' : ''}）；通知管道第二階段。</Text></Paper>
        </Stack>
      </SimpleGrid>
    </Stack>
  );
}

function Detail() {
  const [rows, setRows] = useState<Row[]>([]); const [err, setErr] = useState<string | null>(null); const [q, setQ] = useState(''); const [n, setN] = useState(200);
  useEffect(() => { api.get<Row[]>(`/v1/audit?n=${n}`).then((r) => setRows([...r].reverse())).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [n]);
  const list = useMemo(() => rows.filter((r) => !q || r.event.includes(q) || String(r.user ?? '').includes(q) || String(r.module ?? '').includes(q)), [rows, q]);
  return (
    <Paper>
      <SectionTitle title="明細" right={<Group gap="xs"><TextInput aria-label="篩選明細" placeholder="篩選事件／使用者／模組" value={q} onChange={(e) => setQ(e.target.value)} /><NativeSelect aria-label="明細筆數" value={String(n)} data={[100, 200, 500, 1000].map((x) => ({ value: String(x), label: `最近 ${x} 筆` }))} onChange={(e) => setN(Number(e.target.value))} /></Group>} />
      {err && <Alert color="red" variant="light" mb="sm">{err}</Alert>}
      <Table.ScrollContainer minWidth={760}><Table><Table.Thead><Table.Tr><Table.Th>時間</Table.Th><Table.Th>事件</Table.Th><Table.Th>使用者</Table.Th><Table.Th>模組</Table.Th><Table.Th>內容</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{list.map((r, i) => <Table.Tr key={i}><Table.Td style={{ whiteSpace: 'nowrap' }}><Text size="xs" ff="monospace">{new Date(r.at * 1000).toISOString().slice(0, 19).replace('T', ' ')}</Text></Table.Td><Table.Td><Mono>{r.event}</Mono></Table.Td><Table.Td><Text size="xs" ff="monospace">{String(r.user ?? '')}</Text></Table.Td><Table.Td>{String(r.module ?? '')}</Table.Td><Table.Td><Text size="xs" ff="monospace" style={{ maxWidth: 520, overflowWrap: 'anywhere' }}>{JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k]) => !['event', 'at', 'user', 'module'].includes(k)))).slice(0, 300)}</Text></Table.Td></Table.Tr>)}
          {!list.length && <Table.Tr><Table.Td colSpan={5}><EmptyState>沒有符合的審計事件</EmptyState></Table.Td></Table.Tr>}</Table.Tbody></Table></Table.ScrollContainer>
      <Text size="sm" c="dimmed" mt="sm">查詢效能與掃描量彙總（ClickHouse query_log）第二階段；正式環境看板改由 dmp.audit_log 物化視圖供數。</Text>
    </Paper>
  );
}
