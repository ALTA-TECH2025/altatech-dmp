// 數據治理 › 資料流與效能（docs/17 §5.16）：資料流（四段＋資料品質）／查詢效能（各模組 P50/P95/P99 對 SLA、最慢查詢、ClickHouse query_log）／批次與儲存（同步任務可重跑、例行作業、表用量）／告警（觸發中＋全部規則）。
// 權限：governance.view 看；重跑需 governance.admin（審計 batch.rerun）。數字來源：排程器、審計、程序內指標、ClickHouse 系統表；叢集內同一組指標由 Prometheus／Grafana 供數。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Anchor, Box, Button, Checkbox, Code, Group, List, NativeSelect, Paper, SimpleGrid, Stack, Switch, Table, Tabs, Text, TextInput, Textarea, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, KeyValue, Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { alertText, checkNotify, countBySeverity, fmtBytes, fmtMs, fmtPct, fmtRows, fmtTs, JOB_STATUS_CN, lagText, NTF_KIND_CN, NTF_STATUS_PILL, overall, ruleThreshold, SEV_CN, SEV_PILL, slaStatus, splitList, STATUS_CN, STATUS_PILL } from './ops';
import type { ActivationItem, Alerts, BatchJobs, Notification, NotifyConfig, OpsStatus, Pipeline, QueryPerf, Severity, ServingItem, SourceItem, TableItem } from './types';

export function OpsPage() {
  return <RequireGrant module="governance"><OpsInner /></RequireGrant>;
}

type Tab = 'flow' | 'query' | 'batch' | 'alerts';
const WARN_BG = 'var(--mantine-color-orange-light)';
const STATUS_COLOR: Record<OpsStatus, string> = { ok: 'var(--st-good)', warn: 'var(--st-warn)', error: 'var(--st-serious)', unknown: 'var(--muted)' };
const Dot = ({ s }: { s: OpsStatus }) => <Box component="span" aria-label={STATUS_CN[s]} title={STATUS_CN[s]} style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: STATUS_COLOR[s], flex: '0 0 auto' }} />;
const Pill = ({ s }: { s: OpsStatus }) => <StatusBadge tone={STATUS_PILL[s]}>{STATUS_CN[s]}</StatusBadge>;
const Sub = ({ children, mono }: { children: React.ReactNode; mono?: boolean }) => <Text size="xs" c="dimmed" ff={mono ? 'monospace' : undefined}>{children}</Text>;
const Loading = () => <Text size="sm" c="dimmed">讀取中…</Text>;

function OpsInner() {
  const { can } = useAuth();
  const canAdmin = can('governance', 'admin');
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get('tab') as Tab) || 'flow';
  const setTab = (t: Tab) => setSp(t === 'flow' ? {} : { tab: t });
  const [pl, setPl] = useState<Pipeline | null>(null);
  const [qp, setQp] = useState<QueryPerf | null>(null);
  const [bj, setBj] = useState<BatchJobs | null>(null);
  const [al, setAl] = useState<Alerts | null>(null);
  const [win, setWin] = useState<QueryPerf['window']>('24h');
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    try { const [a, b, c] = await Promise.all([api.get<Pipeline>('/v1/ops/pipeline'), api.get<BatchJobs>('/v1/ops/batch-jobs'), api.get<Alerts>('/v1/ops/alerts')]); setPl(a); setBj(b); setAl(c); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); }
  };
  useEffect(() => { void reload(); }, []);
  useEffect(() => { api.get<QueryPerf>(`/v1/ops/query-perf?window=${win}`).then(setQp).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [win]);
  const rerun = async (name: string) => {
    setBusy(true); setMsg(null);
    try { const r = await api.post<{ job: string; status?: string }>(`/v1/ops/batch-jobs/${encodeURIComponent(name)}/rerun`); setMsg(`已重跑 ${r.job}：${JOB_STATUS_CN[r.status ?? ''] ?? r.status ?? '已排入'}（審計 batch.rerun）`); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '重跑失敗'); }
    finally { setBusy(false); }
  };
  const status = pl ? overall(pl.stages.map((s) => s.status)) : 'unknown';
  const sev = al ? countBySeverity(al.firing) : {};
  return (
    <div className="page">
      <PageHeader title={<Group gap="xs" component="span">資料流與效能 <Pill s={status} /></Group>} lead={<>端到端資料流、各模組查詢效能對 SLA、批次作業與儲存、告警。數字來自排程器、審計、API 程序內指標與 ClickHouse 系統表；叢集內同一組指標由 Prometheus 抓 <Code>/metrics</Code>、Grafana 看板與 Alertmanager 用同一份規則。</>}
        actions={<Button onClick={() => void reload()} disabled={busy}>重新整理</Button>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {msg && <Alert color="teal" variant="light" mb="md">{msg}</Alert>}
      <Tabs value={tab} onChange={(v) => setTab((v as Tab) || 'flow')} mb="md">
        <Tabs.List>{([['flow', '資料流'], ['query', '查詢效能'], ['batch', '批次與儲存'], ['alerts', '告警']] as const).map(([k, cn]) => <Tabs.Tab key={k} value={k}>{cn}{k === 'alerts' && al?.firing.length ? ` (${al.firing.length})` : ''}</Tabs.Tab>)}</Tabs.List>
      </Tabs>
      {tab === 'flow' && (pl ? <FlowTab pl={pl} /> : <Loading />)}
      {tab === 'query' && <QueryTab qp={qp} win={win} setWin={setWin} />}
      {tab === 'batch' && (bj ? <BatchTab bj={bj} canAdmin={canAdmin} busy={busy} rerun={rerun} /> : <Loading />)}
      {tab === 'alerts' && (al ? <Stack gap="md"><AlertsTab al={al} sev={sev} /><NotifyCard canAdmin={canAdmin} /></Stack> : <Loading />)}
    </div>
  );
}

function FlowTab({ pl }: { pl: Pipeline }) {
  const src = (pl.stages.find((s) => s.key === 'sources')?.items ?? []) as SourceItem[];
  const tables = (pl.stages.find((s) => s.key === 'warehouse')?.items ?? []) as TableItem[];
  const sv = (pl.stages.find((s) => s.key === 'serving')?.items ?? [])[0] as ServingItem | undefined;
  const act = (pl.stages.find((s) => s.key === 'activation')?.items ?? [])[0] as ActivationItem | undefined;
  const stageBg = (s: OpsStatus) => (s === 'error' ? 'var(--mantine-color-red-light)' : s === 'warn' ? WARN_BG : 'var(--soft)');
  return (
    <Stack gap="md">
      <Paper><SectionTitle title="端到端資料流" right={<Sub>來源同步 → ClickHouse 進倉 → API 與查詢 → 推送交付 · {fmtTs(pl.at)}</Sub>} />
        <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="sm">{pl.stages.map((s) => <Paper key={s.key} p="sm" radius="md" shadow="none" bg={stageBg(s.status)}><Group justify="space-between" gap="xs" mb={4}><Text fw={600} size="sm">{s.cn}</Text><Dot s={s.status} /></Group><Sub>{s.summary}</Sub></Paper>)}</SimpleGrid>
        <Text size="sm" c="dimmed" mt="sm">SLO：行為事件 {pl.slo.freshness.behavior_events_min} 分鐘可查、日批 {pl.slo.freshness.daily_batch_deadline} 前到齊、例行作業 {pl.slo.freshness.routine_max_min} 分鐘內完成、API 5xx &lt; {fmtPct(pl.slo.serving.http_error_rate_max, 0)}、交付成功率 ≥ {fmtPct(pl.slo.activation.delivery_success_min, 0)}。</Text></Paper>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper p={0} style={{ overflow: 'hidden' }}><Group p="sm" pb={0}><Title order={3}>資料品質</Title></Group>
          <Table.ScrollContainer minWidth={360}><Table><Table.Tbody>{pl.quality.map((q) => <Table.Tr key={q.key} bg={q.status === 'error' ? WARN_BG : undefined}><Table.Td>{q.cn}</Table.Td><Table.Td><Mono>{q.text}</Mono></Table.Td><Table.Td><Pill s={q.status} /></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer></Paper>
        <Paper p={0} style={{ overflow: 'hidden' }}><Group p="sm" pb={0} justify="space-between"><Title order={3}>來源同步</Title><Sub>{src.length} 個任務 · <Anchor component={Link} to="/integration?tab=tasks" size="xs">數據整合</Anchor></Sub></Group>
          {src.length ? <Table.ScrollContainer minWidth={720}><Table><Table.Thead><Table.Tr><Table.Th>任務</Table.Th><Table.Th>排程</Table.Th><Table.Th>最近一次</Table.Th><Table.Th ta="right">讀入／隔離</Table.Th><Table.Th>水位落後</Table.Th><Table.Th>狀態</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{src.map((s) => <Table.Tr key={s.task} bg={s.status === 'error' ? WARN_BG : undefined}><Table.Td><Text fw={600} size="sm">{s.cn}</Text><Sub mono>{s.task}{s.enabled ? '' : ' · 已停用'}</Sub></Table.Td><Table.Td><Text size="xs" ff="monospace">{s.schedule}</Text></Table.Td><Table.Td><Text size="xs" ff="monospace">{fmtTs(s.last_at)}</Text><Sub>{JOB_STATUS_CN[s.last_status ?? ''] ?? s.last_status ?? '—'}{s.error ? ` · ${s.error.slice(0, 60)}` : ''}</Sub></Table.Td><Table.Td ta="right"><Mono>{fmtRows(s.rows_in)}</Mono>{s.rows_bad ? <Sub>{fmtRows(s.rows_bad)}（{fmtPct(s.quarantine_rate)}）</Sub> : null}</Table.Td><Table.Td><Mono>{lagText(s.lag_min)}</Mono></Table.Td><Table.Td><Pill s={s.status} /></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer> : <Text size="sm" c="dimmed" p="sm">排程器未啟用或沒有同步任務（單機以 DMP_SCHED_DB 開啟）。</Text>}</Paper>
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Paper><SectionTitle title="ClickHouse 進倉" />
          {tables.length ? <Table.ScrollContainer minWidth={300}><Table><Table.Thead><Table.Tr><Table.Th>表</Table.Th><Table.Th ta="right">列數</Table.Th><Table.Th ta="right">用量</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{tables.slice(0, 8).map((t) => <Table.Tr key={t.name}><Table.Td>{t.cn}<Sub mono>dmp.{t.name}</Sub></Table.Td><Table.Td ta="right"><Mono>{fmtRows(t.rows)}</Mono></Table.Td><Table.Td ta="right"><Mono>{fmtBytes(t.bytes)}</Mono></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer> : <Text size="sm" c="dimmed">未連接 ClickHouse。</Text>}</Paper>
        <Paper><SectionTitle title="API 與查詢（近 5 分鐘）" />
          {sv ? <KeyValue rows={[['請求', <Mono key="r">{sv.requests_5m} 次 · P50 {fmtMs(sv.http_p50_ms)} · P95 {fmtMs(sv.http_p95_ms)}</Mono>], ['5xx', <Mono key="e">{fmtPct(sv.http_error_rate, 2)}（累計 {sv.errors_5xx_total} / {sv.requests_total}）</Mono>], ['分析查詢', <Mono key="q">{sv.queries_5m} 次 · P95 {fmtMs(sv.query_p95_ms)}</Mono>]]} /> : <Text size="sm" c="dimmed">—</Text>}</Paper>
        <Paper><SectionTitle title="推送交付（近 24 小時）" right={<Anchor component={Link} to="/activation?tab=deliveries" size="xs">交付紀錄</Anchor>} />
          {act ? <KeyValue rows={[['交付', <Mono key="d">{Object.entries(act.deliveries_24h).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(' · ') || '無'}</Mono>], ['成功率', <Mono key="s">{fmtPct(act.success_rate)}</Mono>], ['自動暫停', <Group gap={4} key="p">{act.paused_destinations.length ? act.paused_destinations.map((p) => <StatusBadge key={p.destination} tone="amber">{p.cn} ×{p.consecutive_failures}</StatusBadge>) : '無'}</Group>]]} /> : <Text size="sm" c="dimmed">—</Text>}</Paper>
      </SimpleGrid>
    </Stack>
  );
}

function QueryTab({ qp, win, setWin }: { qp: QueryPerf | null; win: QueryPerf['window']; setWin: (w: QueryPerf['window']) => void }) {
  return (
    <Stack gap="md">
      <Group gap="xs"><NativeSelect aria-label="查詢效能期間" value={win} data={[{ value: '1h', label: '近 1 小時' }, { value: '24h', label: '近 24 小時' }, { value: '7d', label: '近 7 天' }]} onChange={(e) => setWin(e.target.value as QueryPerf['window'])} />
        {qp?.clickhouse && <Sub>ClickHouse query_log：{qp.clickhouse.n} 次 · P95 {fmtMs(qp.clickhouse.p95_ms)} · 平均掃描 {fmtRows(qp.clickhouse.scan_rows_avg)} 列 · 記憶體峰值 {qp.clickhouse.memory_peak_mb ?? '—'} MB · 例外 {qp.clickhouse.errors}</Sub>}</Group>
      {!qp ? <Loading /> : (
        <>
          <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={900}><Table><Table.Thead><Table.Tr><Table.Th>模組</Table.Th><Table.Th ta="right">次數</Table.Th><Table.Th ta="right">錯誤</Table.Th><Table.Th ta="right">P50</Table.Th><Table.Th ta="right">P95</Table.Th><Table.Th ta="right">P99</Table.Th><Table.Th ta="right">SLA（P95）</Table.Th><Table.Th ta="right">平均列數</Table.Th><Table.Th>狀態</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{qp.modules.length ? qp.modules.map((m) => { const s = slaStatus(m); return <Table.Tr key={m.module} bg={s === 'error' ? WARN_BG : undefined}><Table.Td><Text fw={600} size="sm">{m.cn}</Text><Sub mono>{m.module}</Sub></Table.Td><Table.Td ta="right"><Mono>{m.n}</Mono></Table.Td><Table.Td ta="right"><Mono>{m.errors ? <Text component="span" c="red" inherit>{m.errors}（{fmtPct(m.error_rate)}）</Text> : '0'}</Mono></Table.Td><Table.Td ta="right"><Mono>{fmtMs(m.p50_ms)}</Mono></Table.Td><Table.Td ta="right"><Mono><b>{fmtMs(m.p95_ms)}</b></Mono></Table.Td><Table.Td ta="right"><Mono>{fmtMs(m.p99_ms)}</Mono></Table.Td><Table.Td ta="right"><Mono>{fmtMs(m.sla_ms)}</Mono></Table.Td><Table.Td ta="right"><Mono>{m.avg_rows == null ? '—' : Math.round(m.avg_rows).toLocaleString()}</Mono></Table.Td><Table.Td>{m.sla_ms == null ? <StatusBadge tone="gray">無 SLA</StatusBadge> : <Pill s={s} />}</Table.Td></Table.Tr>; }) : <Table.Tr><Table.Td colSpan={9}><EmptyState>期間內沒有查詢。</EmptyState></Table.Td></Table.Tr>}</Table.Tbody></Table></Table.ScrollContainer></Paper>
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Paper p={0} style={{ overflow: 'hidden' }}><Group p="sm" pb={0} justify="space-between"><Title order={3}>最慢的查詢</Title><Sub>sql_hash 可對到審計 query.executed</Sub></Group>
              {qp.slow.length ? <Table.ScrollContainer minWidth={600}><Table><Table.Thead><Table.Tr><Table.Th>時間</Table.Th><Table.Th>模組</Table.Th><Table.Th ta="right">耗時</Table.Th><Table.Th ta="right">列數</Table.Th><Table.Th>使用者</Table.Th><Table.Th>sql_hash</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{qp.slow.map((r, i) => <Table.Tr key={i}><Table.Td><Text size="xs" ff="monospace">{fmtTs(r.at)}</Text></Table.Td><Table.Td>{r.module}</Table.Td><Table.Td ta="right"><Mono>{fmtMs(r.ms)}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows}</Mono></Table.Td><Table.Td><Text size="xs" ff="monospace">{r.user ?? '—'}</Text></Table.Td><Table.Td><Text size="xs" ff="monospace">{r.sql_hash ?? '—'}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer> : <Text size="sm" c="dimmed" p="sm">—</Text>}</Paper>
            <Paper><SectionTitle title="效能策略（docs/17 §5、docs/23 §6）" /><List size="sm" spacing="xs" c="dimmed"><List.Item>90% 查詢走物化視圖（L1 日彙總、L2 常用分組、L3 cohort），不碰明細；路徑預設抽樣 1/10。</List.Item><List.Item>one_id_uint 稠密 ID + Bitmap：人數估算目標 &lt; 1 秒；標籤／分群成員以 Bitmap 交集。</List.Item><List.Item>工作負載隔離：human 60 秒、batch 600 秒的 max_execution_time；自定義 SQL 有掃描預算與每人併發上限。</List.Item><List.Item>P95 連續超標先看 query_log 的 read_rows 與 memory_usage，再決定補 MV 或改口徑（M2 效能基準回填 SLA）。</List.Item></List></Paper>
          </SimpleGrid>
        </>
      )}
    </Stack>
  );
}

function BatchTab({ bj, canAdmin, busy, rerun }: { bj: BatchJobs; canAdmin: boolean; busy: boolean; rerun: (n: string) => Promise<void> }) {
  const jobStatus = (j: BatchJobs['jobs'][number]): OpsStatus => (!j.enabled ? 'warn' : j.last_status == null ? 'unknown' : ['success', 'running', 'skipped', 'retried'].includes(j.last_status) ? 'ok' : j.last_status === 'retry_wait' ? 'warn' : 'error');
  const hot = bj.storage.filter((s) => s.name.startsWith('clickhouse.')).reduce((a, s) => a + (s.bytes ?? 0), 0);
  return (
    <Stack gap="md">
      <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={900}><Table><Table.Thead><Table.Tr><Table.Th>作業</Table.Th><Table.Th>類型</Table.Th><Table.Th>排程</Table.Th><Table.Th>最近一次</Table.Th><Table.Th ta="right">耗時</Table.Th><Table.Th>結果</Table.Th><Table.Th>狀態</Table.Th><Table.Th /></Table.Tr></Table.Thead>
        <Table.Tbody>{bj.jobs.map((j) => { const s = jobStatus(j); return <Table.Tr key={j.name} bg={s === 'error' ? WARN_BG : undefined}><Table.Td><Text fw={600} size="sm">{j.cn}</Text><Sub mono>{j.name}</Sub></Table.Td><Table.Td>{j.kind === 'sync' ? '同步任務' : '例行'}</Table.Td><Table.Td><Text size="xs" ff="monospace">{j.schedule}</Text>{j.next_at ? <Sub>下次 {fmtTs(j.next_at)}</Sub> : null}</Table.Td><Table.Td><Text size="xs" ff="monospace">{fmtTs(j.last_at)}</Text></Table.Td><Table.Td ta="right"><Mono>{fmtMs(j.last_ms)}</Mono></Table.Td><Table.Td><Text size="xs">{j.last_status ? <>{JOB_STATUS_CN[j.last_status] ?? j.last_status}{j.last_detail ? <Sub>{j.last_detail}</Sub> : null}{j.errors_total ? <Sub>累計失敗 {j.errors_total} / {j.runs_total}</Sub> : null}</> : <Sub>尚未執行（DMP_TICK=1 才有背景例行）</Sub>}</Text></Table.Td><Table.Td><Pill s={s} /></Table.Td><Table.Td ta="right">{j.rerunnable && canAdmin ? <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void rerun(j.name)}>重跑</Button> : null}</Table.Td></Table.Tr>; })}</Table.Tbody></Table></Table.ScrollContainer></Paper>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper><SectionTitle title="儲存用量" right={<Sub>ClickHouse 熱層合計 {fmtBytes(hot)}</Sub>} />
          <Table.ScrollContainer minWidth={420}><Table><Table.Thead><Table.Tr><Table.Th>項目</Table.Th><Table.Th ta="right">列數</Table.Th><Table.Th ta="right">用量</Table.Th><Table.Th ta="right">parts</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{bj.storage.map((s) => <Table.Tr key={s.name}><Table.Td>{s.cn}<Text size="xs" c="dimmed" ff="monospace" style={{ overflowWrap: 'anywhere' }}>{s.name}{s.note ? ` · ${s.note}` : ''}</Text></Table.Td><Table.Td ta="right"><Mono>{fmtRows(s.rows)}</Mono></Table.Td><Table.Td ta="right"><Mono>{fmtBytes(s.bytes)}</Mono></Table.Td><Table.Td ta="right"><Mono>{s.parts ?? '—'}</Mono></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer></Paper>
        <Paper><SectionTitle title="作業說明" /><List size="sm" spacing="xs" c="dimmed"><List.Item>同步任務由排程器（cron／間隔、依賴、重試與退避、schema 變更策略）執行；「重跑」= 立即執行一次，需 governance.admin，審計 batch.rerun。</List.Item><List.Item>例行作業由 API 背景執行緒跑：同步排程每分鐘、看板定時寄送每分鐘、分群／標籤每 10 分鐘、推送重試與增量每分鐘；失敗寫審計 scheduler.tick.failed，24 小時達 3 次觸發告警。</List.Item><List.Item>Airflow／dbt 的日批作業（70 億歷史載入、觸點表、冷層搬移）第二階段接入同一張表。</List.Item></List></Paper>
      </SimpleGrid>
    </Stack>
  );
}

function AlertsTab({ al, sev }: { al: Alerts; sev: ReturnType<typeof countBySeverity> }) {
  return (
    <>
      <Paper><SectionTitle title="觸發中" right={<Sub>{al.firing.length ? (Object.keys(sev) as (keyof typeof sev)[]).map((k) => `${SEV_CN[k]} ${sev[k]}`).join(' · ') : '沒有觸發中的告警'} · 通知：依下方「告警通知設定」送 Email／LINE（規則的通知角色：{al.notify.join('、')}）</Sub>} />
        {al.firing.length ? <Table><Table.Tbody>{al.firing.map((a, i) => <Table.Tr key={i} bg={a.severity === 'critical' || a.severity === 'high' ? WARN_BG : undefined}><Table.Td w={70}><StatusBadge tone={SEV_PILL[a.severity]}>{SEV_CN[a.severity]}</StatusBadge></Table.Td><Table.Td>{alertText(a)}<Sub>{a.text}</Sub></Table.Td></Table.Tr>)}</Table.Tbody></Table> : <Text size="sm" c="dimmed">全部規則都在門檻內。</Text>}</Paper>
      <Paper p={0} style={{ overflow: 'hidden' }}><Group p="sm" pb={0} justify="space-between"><Title order={3}>告警規則</Title><Sub>backend/ops/alerts.yaml；叢集內由 PrometheusRule 以同名規則送 Alertmanager</Sub></Group>
        <Table.ScrollContainer minWidth={820}><Table><Table.Thead><Table.Tr><Table.Th>規則</Table.Th><Table.Th>指標</Table.Th><Table.Th>門檻</Table.Th><Table.Th>對象</Table.Th><Table.Th>窗口</Table.Th><Table.Th>嚴重度</Table.Th><Table.Th>說明</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{al.rules.map((r) => <Table.Tr key={r.key}><Table.Td><Text fw={600} size="sm">{r.cn}</Text><Sub mono>{r.key}</Sub></Table.Td><Table.Td><Text size="xs" ff="monospace">{r.metric}</Text></Table.Td><Table.Td><Mono>{ruleThreshold(r)}</Mono></Table.Td><Table.Td>{r.per ?? '全域'}</Table.Td><Table.Td>{r.window}</Table.Td><Table.Td><StatusBadge tone={SEV_PILL[r.severity]}>{SEV_CN[r.severity]}</StatusBadge></Table.Td><Table.Td><Text size="xs">{r.text}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer></Paper>
    </>
  );
}

const ROLE_OPTIONS = [['admin', '系統管理員'], ['it_admin', '資訊管理'], ['digital_ops', '數位經營'], ['data_analyst', '數據分析'], ['marketing', '行銷']] as const;
function NotifyCard({ canAdmin }: { canAdmin: boolean }) {
  const [cfg, setCfg] = useState<NotifyConfig | null>(null);
  const [hist, setHist] = useState<Notification[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [extraTo, setExtraTo] = useState(''); const [lineTo, setLineTo] = useState('');
  const load = async () => {
    try { const [c, h] = await Promise.all([api.get<NotifyConfig>('/v1/ops/notify'), api.get<Notification[]>('/v1/ops/notify/history?limit=50')]); setCfg(c); setHist(h); setExtraTo(c.email.extra_to.join('\n')); setLineTo(c.line.to.join('\n')); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? (e.status === 403 ? null : e.message) : '讀取失敗'); }
  };
  useEffect(() => { void load(); }, []);
  if (!canAdmin) return <Paper><SectionTitle title="告警通知設定" /><Text size="sm" c="dimmed">Email／LINE 官方帳號通知由系統管理員或資訊管理設定；通知紀錄見審計 ops.notify.sent。</Text></Paper>;
  if (!cfg) return <Paper><SectionTitle title="告警通知設定" />{err ? <Alert color="red" variant="light">{err}</Alert> : <Loading />}</Paper>;
  const draft = { ...cfg, email: { ...cfg.email, extra_to: splitList(extraTo) }, line: { ...cfg.line, to: splitList(lineTo) } };
  const errs = checkNotify(draft);
  const set = (patch: Partial<NotifyConfig>) => setCfg({ ...cfg, ...patch });
  const save = async () => {
    setBusy(true); setMsg(null);
    try { const c = await api.put<NotifyConfig>('/v1/ops/notify', { email: draft.email, line: draft.line, policy: draft.policy }); setCfg(c); setExtraTo(c.email.extra_to.join('\n')); setLineTo(c.line.to.join('\n')); setMsg('通知設定已儲存（審計 ops.notify.changed）'); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '儲存失敗'); }
    finally { setBusy(false); }
  };
  const test = async (channel: 'email' | 'line') => {
    setBusy(true); setMsg(null);
    try { const r = await api.post<Notification>('/v1/ops/notify/test', { channel }); setMsg(`${channel === 'email' ? 'Email' : 'LINE'} 測試：${r.status === 'ok' ? `已送 ${r.recipients} 個收件對象` : `${r.status}：${r.response ?? ''}`}`); await load(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '測試失敗'); }
    finally { setBusy(false); }
  };
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title="告警通知設定" right={<Sub>{cfg.updated_at ? `上次修改 ${fmtTs(cfg.updated_at)} · ${cfg.updated_by}` : '使用預設值（backend/ops/notify.yaml）'}</Sub>} />
        {err && <Alert color="red" variant="light">{err}</Alert>}
        {msg && <Alert color="teal" variant="light">{msg}</Alert>}
        <SimpleGrid cols={{ base: 1, md: 3 }}>
          <Stack gap="sm">
            <Group gap="xs"><Title order={4}>Email（SMTP）</Title>{cfg.available.email ? <StatusBadge tone="blue">可用</StatusBadge> : <StatusBadge tone="gray"><span title="API 需設定 DMP_SMTP_URL">未設定 SMTP</span></StatusBadge>}</Group>
            <Switch label="啟用" aria-label="Email 啟用" checked={cfg.email.enabled} onChange={(e) => set({ email: { ...cfg.email, enabled: e.currentTarget.checked } })} />
            <Checkbox.Group label="收件角色（從成員目錄取 email）" value={cfg.email.to_roles} onChange={(v) => set({ email: { ...cfg.email, to_roles: v } })}>
              <Stack gap={6} mt={6}>{ROLE_OPTIONS.map(([k, cn]) => <Checkbox key={k} value={k} label={cn} aria-label={`收件角色 ${cn}`} />)}</Stack>
            </Checkbox.Group>
            <Textarea label="額外收件人（值班／群組信箱，一行一個）" aria-label="額外收件人" rows={2} value={extraTo} onChange={(e) => setExtraTo(e.target.value)} placeholder="oncall@example.com" />
            <TextInput label="主旨前綴" aria-label="主旨前綴" value={cfg.email.subject_prefix} onChange={(e) => set({ email: { ...cfg.email, subject_prefix: e.target.value } })} />
            <Group gap="xs"><Button size="compact-sm" variant="subtle" disabled={busy || !cfg.email.enabled || !cfg.available.email} onClick={() => void test('email')}>送測試 Email</Button></Group>
          </Stack>
          <Stack gap="sm">
            <Group gap="xs"><Title order={4}>LINE 官方帳號（Messaging API）</Title>{cfg.available.line ? <StatusBadge tone="blue">token 可解析</StatusBadge> : <StatusBadge tone="gray">未設定</StatusBadge>}</Group>
            <Switch label="啟用" aria-label="LINE 啟用" checked={cfg.line.enabled} onChange={(e) => set({ line: { ...cfg.line, enabled: e.currentTarget.checked } })} />
            <TextInput label="Channel access token 的密鑰庫參照（不貼明文）" aria-label="Channel access token 密鑰庫參照" ff="monospace" value={cfg.line.channel_access_token_ref} onChange={(e) => set({ line: { ...cfg.line, channel_access_token_ref: e.target.value.trim() } })} placeholder="kv/data/dmp/prod/line#channel_access_token" />
            <Textarea label="收件 userId／groupId（一行一個；把官方帳號加進值班群組後由 webhook 事件取得 groupId）" aria-label="LINE 收件對象" rows={2} ff="monospace" value={lineTo} onChange={(e) => setLineTo(e.target.value)} placeholder="Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" />
            <Text size="sm" c="dimmed">免費方案每月 200 則推送；LINE Notify 已停止服務，不用。多個收件對象用 multicast 一次送。</Text>
            <Group gap="xs"><Button size="compact-sm" variant="subtle" disabled={busy || !cfg.line.enabled || !cfg.available.line} onClick={() => void test('line')}>送測試 LINE</Button></Group>
          </Stack>
          <Stack gap="sm">
            <Title order={4}>政策</Title>
            <NativeSelect label="最低嚴重度（以下只在頁面顯示）" aria-label="最低嚴重度" value={cfg.policy.min_severity} data={(['low', 'medium', 'high', 'critical'] as const).map((s) => ({ value: s, label: SEV_CN[s] }))} onChange={(e) => set({ policy: { ...cfg.policy, min_severity: e.target.value as Severity } })} />
            <TextInput label="同一告警冷卻（分鐘）" aria-label="同一告警冷卻" type="number" min={0} max={1440} value={cfg.policy.cooldown_min} onChange={(e) => set({ policy: { ...cfg.policy, cooldown_min: Number(e.target.value) } })} />
            <Checkbox label="告警消失時送「已恢復」" checked={cfg.policy.notify_resolved} onChange={(e) => set({ policy: { ...cfg.policy, notify_resolved: e.currentTarget.checked } })} />
            <Checkbox label="靜音時段只送「緊急」" checked={!!cfg.policy.quiet_hours} onChange={(e) => set({ policy: { ...cfg.policy, quiet_hours: e.currentTarget.checked ? { start: 0, end: 7, tz: 'Asia/Taipei' } : null } })} />
            {cfg.policy.quiet_hours && <Group gap="xs"><TextInput aria-label="靜音開始（時）" type="number" min={0} max={23} w={70} value={cfg.policy.quiet_hours.start} onChange={(e) => set({ policy: { ...cfg.policy, quiet_hours: { ...cfg.policy.quiet_hours!, start: Number(e.target.value) } } })} /><Text size="sm">時 ～</Text><TextInput aria-label="靜音結束（時）" type="number" min={0} max={23} w={70} value={cfg.policy.quiet_hours.end} onChange={(e) => set({ policy: { ...cfg.policy, quiet_hours: { ...cfg.policy.quiet_hours!, end: Number(e.target.value) } } })} /><Text size="sm">時（Asia/Taipei）</Text></Group>}
            <Text size="sm" c="dimmed">叢集內 Prometheus／Alertmanager 的告警經 webhook 走同一組管道與政策（Helm AlertmanagerConfig）。</Text>
          </Stack>
        </SimpleGrid>
        {errs.length > 0 && <Alert color="red" variant="light">{errs.join('；')}</Alert>}
        <Group gap="xs"><Button variant="filled" disabled={busy || errs.length > 0} onClick={() => void save()}>儲存設定</Button><Button disabled={busy} onClick={() => void load()}>還原</Button></Group>
        <SectionTitle title="通知紀錄" right={<Sub>最近 50 筆</Sub>} />
        {hist.length ? <Table.ScrollContainer minWidth={820}><Table><Table.Thead><Table.Tr><Table.Th>時間</Table.Th><Table.Th>管道</Table.Th><Table.Th>類型</Table.Th><Table.Th>告警</Table.Th><Table.Th ta="right">收件</Table.Th><Table.Th>狀態</Table.Th><Table.Th>回應</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{hist.map((n) => <Table.Tr key={n.id} bg={n.status === 'failed' ? WARN_BG : undefined}><Table.Td style={{ whiteSpace: 'nowrap' }}><Text size="xs" ff="monospace">{fmtTs(n.at)}</Text></Table.Td><Table.Td>{n.channel === 'email' ? 'Email' : 'LINE'}</Table.Td><Table.Td>{NTF_KIND_CN[n.kind] ?? n.kind}</Table.Td><Table.Td><Text size="xs" title={n.text}>{n.alerts.slice(0, 3).join('、')}{n.alerts.length > 3 ? ` 等 ${n.alerts.length} 項` : ''}</Text></Table.Td><Table.Td ta="right"><Mono>{n.recipients}</Mono></Table.Td><Table.Td><StatusBadge tone={NTF_STATUS_PILL[n.status] ?? 'gray'}>{n.status}</StatusBadge></Table.Td><Table.Td><Text size="xs" c="dimmed" style={{ maxWidth: 260, overflowWrap: 'anywhere' }}>{n.response ?? ''}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer> : <Text size="sm" c="dimmed">還沒有通知。</Text>}
      </Stack>
    </Paper>
  );
}
