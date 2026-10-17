// 神策遷移（docs/22）：盤點（計畫 + 神策查詢 API 盤點 + 任務進度）、埋點銜接（bridge.py）、導出與載入（任務、分片、重跑）、新舊對帳（T2／T5／T6）、切換時序。設計系統元件組合。
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Alert, Button, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { LineChart } from '@/analytics/LineChart';
import { fmtNum } from '@/analytics/series';
import type { Series } from '@/analytics/series';
import { Chart, hbar } from '@/ds/Chart';
import { Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { pct } from '@/governance/governance';
import { daysBetween, diffText, INV_STATUS, JOB_STATUS, KIND_PILL, timelineStatus } from './migration';
import type { Bridge, ExportJob, Exports, ExportSlice, Inventory, Reconcile } from './types';

type Tab = 'inventory' | 'bridge' | 'exports' | 'recon' | 'timeline';
const opt = (value: string, label: string) => ({ value, label });
const WARN_BG = 'var(--mantine-color-orange-light)';   // 需要注意的列（有議題、差異超標、進行中）底色，跟著深淺色走
const SLICE_CN: Record<string, string> = { loaded: '已載入', exported: '已導出', pending: '待執行', blocked: '待連線', failed: '失敗', mismatch: '筆數不符' };

export function MigrationPage() {
  return <RequireGrant module="governance"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const canUse = can('governance', 'use');
  const [tab, setTab] = useState<Tab>('inventory');
  const [inv, setInv] = useState<Inventory | null>(null); const [br, setBr] = useState<Bridge | null>(null); const [ex, setEx] = useState<Exports | null>(null); const [rc, setRc] = useState<Reconcile | null>(null);
  const [err, setErr] = useState<string | null>(null); const [rcErr, setRcErr] = useState<string | null>(null);
  const [project, setProject] = useState('migration_demo'); const [days, setDays] = useState(28);
  const reload = async () => { try { const [a, b, c] = await Promise.all([api.get<Inventory>('/v1/migration/inventory'), api.get<Bridge>('/v1/migration/bridge'), api.get<Exports>('/v1/migration/exports')]); setInv(a); setBr(b); setEx(c); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  const loadRecon = async () => { try { setRc(await api.get<Reconcile>(`/v1/migration/reconcile?days=${days}${project ? `&project=${encodeURIComponent(project)}` : ''}`)); setRcErr(null); } catch (e) { setRc(null); setRcErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => { void reload(); void loadRecon(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const tl = inv ? timelineStatus(inv.timeline, new Date().toISOString().slice(0, 10)) : [];
  const loaded = inv ? inv.items.filter((i) => i.id === 'events').map((i) => i.rows_loaded ?? 0)[0] ?? 0 : 0;
  const tiles = [
    ['歷史事件已載入', inv ? fmtNum(loaded) : '—', inv?.items.find((i) => i.id === 'events')?.rows_sensors ? `神策 ${fmtNum(inv.items.find((i) => i.id === 'events')!.rows_sensors!)}` : inv?.sensors_configured ? '尚未盤點' : '未設定神策連線'],
    ['事件銜接', br ? `${br.via_ga} / ${br.total}` : '—', br ? `經 GA 埋點；${br.counts.not_ga ?? 0} 個直接接入、${br.counts.history_only ?? 0} 個僅歷史` : ''],
    ['並行期量差（中位）', rc?.t5.median_abs_diff != null ? pct(rc.t5.median_abs_diff) : '—', rc ? `${rc.t5.events_over} 個事件 > ${pct(rc.thresholds.t5_parallel ?? 0.05, 0)} 待處理` : rcErr ?? ''],
    ['登入用戶銜接率', rc?.t6.rate != null ? pct(rc.t6.rate) : '—', rc ? `${fmtNum(rc.t6.both)} 人兩邊同一客戶代碼（目標 ≥ ${pct(rc.thresholds.t6_user_bridge ?? 0.95, 0)}）` : ''],
  ];
  return (
    <div className="page">
      <PageHeader title="神策遷移" lead="神策 → GA360：依事件字典產生 GA 埋點規格、神策歷史資料盤點／導出／載入、新舊資料對帳與切換時序。盤點筆數與對帳數字來自神策查詢 API 與 ClickHouse；沒有連線的部分會標示待連線，不用模擬數字。"
        actions={<><StatusBadge tone={inv?.sensors_configured ? 'blue' : 'amber'}>{inv?.sensors_configured ? '神策連線已設定' : '神策連線未設定（SA_*）'}</StatusBadge><Button component="a" href="https://github.com/ALTA-TECH2025/altatech-dmp/blob/main/docs/22-sensors-to-ga360-migration.md" target="_blank" rel="noreferrer">文件 docs/22</Button></>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      <SimpleGrid cols={{ base: 2, md: 4 }} mb="md">{tiles.map(([a, b, c]) => <StatTile key={a} label={a} value={b} hint={c} />)}</SimpleGrid>
      <SegmentedControl mb="md" value={tab} onChange={(v) => setTab(v as Tab)} data={[opt('inventory', '盤點'), opt('bridge', `埋點銜接 ${br?.total ?? ''}`), opt('exports', `導出與載入 ${ex?.items.length ?? ''}`), opt('recon', '新舊對帳'), opt('timeline', '切換時序')]} />
      {tab === 'inventory' && inv && <InventoryTab inv={inv} canUse={canUse} onChange={reload} />}
      {tab === 'bridge' && br && <BridgeTab br={br} />}
      {tab === 'exports' && ex && inv && <ExportsTab ex={ex} inv={inv} canUse={canUse} onChange={reload} />}
      {tab === 'recon' && <ReconTab rc={rc} err={rcErr} project={project} setProject={setProject} days={days} setDays={setDays} onLoad={loadRecon} />}
      {tab === 'timeline' && inv && (
        <Stack gap="md">
          <Paper><SectionTitle title="切換時序"><Text size="xs" c="dimmed">T0 = 神策停止寫入；UAT 2027-06-30、PD 2027-09-30 為客戶定的硬日期</Text></SectionTitle>
            <Table.ScrollContainer minWidth={520}>
              <Table>
                <Table.Thead><Table.Tr><Table.Th>里程碑</Table.Th><Table.Th>日期</Table.Th><Table.Th>內容</Table.Th><Table.Th>狀態</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{inv.timeline.map((m, i) => <Table.Tr key={m.id} bg={tl[i] === 'now' ? WARN_BG : undefined}><Table.Td><Text fw={600} size="sm">{m.id}</Text></Table.Td><Table.Td><Mono>{m.date}</Mono></Table.Td><Table.Td>{m.cn}</Table.Td><Table.Td><StatusBadge tone={tl[i] === 'done' ? 'blue' : tl[i] === 'now' ? 'amber' : 'gray'}>{tl[i] === 'done' ? '完成' : tl[i] === 'now' ? '進行中' : '待辦'}</StatusBadge></Table.Td></Table.Tr>)}</Table.Tbody>
              </Table>
            </Table.ScrollContainer></Paper>
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Paper><SectionTitle title="分工" /><Table><Table.Tbody>{[['AltaTech', '橋接表、導出與載入工具、對帳、整合測試、系統'], ['GA360 導入商', '依橋接表埋點、GTM／Firebase 設定、BigQuery 匯出開通、DebugView 核對'], ['客戶', '神策存取權、數據中台交易測試資料、新 MA 工具決策、驗收']].map((r) => <Table.Tr key={r[0]}><Table.Td><Text fw={600} size="sm">{r[0]}</Text></Table.Td><Table.Td>{r[1]}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Paper>
            <Paper><SectionTitle title="風險" />
              <List size="sm" spacing="xs">
                <List.Item>客戶無法開放神策底層（Impala／HDFS）→ 退而用查詢 API 按日導出，70 億約需更久；先確認權限。</List.Item>
                <List.Item>GA360 埋點品質：並行期至少 3 個月，差異逐事件收斂；T5 門檻 5%。</List.Item>
                <List.Item>口徑差異（會話、停留、匿名 ID）已文件化，分析按時期分開看。</List.Item>
                <List.Item>T0 後 4 週內最終增量與對帳；神策 2027-10-31 到期，之後無退路。</List.Item>
              </List></Paper>
          </SimpleGrid>
        </Stack>
      )}
    </div>
  );
}

function InventoryTab({ inv, canUse, onChange }: { inv: Inventory; canUse: boolean; onChange: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const refresh = async () => { setBusy(true); setErr(null); try { await api.post('/v1/migration/inventory/refresh', {}); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } finally { setBusy(false); } };
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">{inv.inventoried_at ? `最近盤點 ${inv.inventoried_at.slice(0, 16).replace('T', ' ')} · 神策用戶 ${inv.users_total?.toLocaleString() ?? '—'}` : '尚未以神策查詢 API 盤點'}</Text>
        {canUse && <Button ml="auto" disabled={busy || !inv.sensors_configured} title={inv.sensors_configured ? '' : '執行節點需設定 SA_BASE_URL／SA_PROJECT／SA_API_SECRET'} onClick={() => void refresh()}>{busy ? '盤點中…' : '以神策 API 重新盤點'}</Button>}
      </Group>
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={960}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>資料集</Table.Th><Table.Th>神策位置</Table.Th><Table.Th>範圍</Table.Th><Table.Th ta="right">神策筆數</Table.Th><Table.Th>導出方式</Table.Th><Table.Th>目標</Table.Th><Table.Th>進度</Table.Th><Table.Th>狀態</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{inv.items.map((r) => <Table.Tr key={r.id}>
              <Table.Td><Text fw={600} size="sm" style={{ whiteSpace: 'nowrap' }}>{r.cn}</Text><Text size="xs" c="dimmed">{r.note ?? r.rule}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{r.location}</Text></Table.Td>
              <Table.Td><Text size="xs">{r.range ?? '—'}</Text></Table.Td>
              <Table.Td ta="right"><Mono>{r.rows_sensors === null ? '—' : r.rows_sensors.toLocaleString()}</Mono></Table.Td>
              <Table.Td><Text size="xs">{r.method}</Text></Table.Td>
              <Table.Td><Text size="xs">{r.target}</Text></Table.Td>
              <Table.Td>{r.progress === null ? '—' : <Mono>{r.progress}%{r.rows_loaded ? ` · 載入 ${fmtNum(r.rows_loaded)}` : ''}</Mono>}</Table.Td>
              <Table.Td><StatusBadge tone={INV_STATUS[r.status].pill}>{INV_STATUS[r.status].cn}</StatusBadge></Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Paper><SectionTitle title="載入順序" />
          <List size="sm" spacing="xs">
            <List.Item>先 ID 映射與用戶表，再事件：事件載入時客戶代碼已存在。</List.Item>
            <List.Item>事件名保留神策原名；時間轉 UTC、日分區按 Asia/Taipei；$ip 丟棄。</List.Item>
            <List.Item>$identity_login_id 以同一把金鑰 HMAC 成 v1_ 客戶代碼，與 GA user_id 同款。</List.Item>
            <List.Item>IDM 2.0：second_id → 客戶代碼；first_id 保留為匿名用戶，不與 GA client_id 銜接。</List.Item>
          </List></Paper>
        <Paper><SectionTitle title="導出方式" />
          <List size="sm" spacing="xs">
            <List.Item>Impala JDBC／HDFS Parquet 直讀（採用；客戶內網執行，進度回寫）。</List.Item>
            <List.Item>查詢 API /api/sql/query：按日切片，抽樣校驗與小量資料集；本頁「導出與載入」用這條。</List.Item>
            <List.Item>神策資料格式檔（FormatImporter JSONL）：sensors_format 代碼化後載入。</List.Item>
            <List.Item>分片：{inv.export_rules.slice}；校驗：{inv.export_rules.checks.join('、')}；差異 &gt; {pct(inv.export_rules.rerun_threshold, 1)} 自動重跑，{inv.export_rules.max_auto_rerun} 次仍差待人工。</List.Item>
          </List></Paper>
        <Paper><SectionTitle title="盤點來源" /><Text size="sm" c="dimmed">計畫欄位來自 migration/inventory.yaml（docs/22 §3.1）；筆數、範圍由「以神策 API 重新盤點」寫入（每事件每日筆數、用戶數）；進度由導出任務回寫。元數據盤點已完成：事件 117、屬性 681、用戶屬性 3,606（docs/28、29）。</Text></Paper>
      </SimpleGrid>
    </Stack>
  );
}

function BridgeTab({ br }: { br: Bridge }) {
  const [kind, setKind] = useState('all'); const [q, setQ] = useState('');
  const rows = br.items.filter((r) => (kind === 'all' || r.kind === kind) && (!q || r.sensors_event.toLowerCase().includes(q.toLowerCase()) || r.ga4_event.includes(q.toLowerCase()) || r.cn.includes(q)));
  const download = () => { const head = ['sensors_event', 'cn', 'group', 'source', 'platforms', 'ga4_event', 'kind', 'params', 'note', 'aliases']; const esc = (v: string) => `"${String(v ?? '').replace(/"/g, '""')}"`; const csv = [head.join(','), ...br.items.map((r) => head.map((h) => esc(String((r as unknown as Record<string, unknown>)[h] ?? ''))).join(','))].join('\n'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' })); a.download = 'ga360_tracking_spec.csv'; a.click(); };
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <SegmentedControl size="xs" value={kind} onChange={setKind} data={[opt('all', `全部 ${br.total}`), opt('auto', `GA 自動 ${br.counts.auto ?? 0}`), opt('recommended', `建議事件 ${br.counts.recommended ?? 0}`), opt('custom', `自訂 ${br.counts.custom ?? 0}`), opt('not_ga', `不經 GA ${br.counts.not_ga ?? 0}`), opt('history_only', `僅歷史 ${br.counts.history_only ?? 0}`)]} />
        <TextInput aria-label="搜尋事件" placeholder="搜尋神策事件／GA4 事件名" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 200px', maxWidth: 260 }} />
        <Button ml="auto" onClick={download}>下載 ga360_tracking_spec.csv</Button>
      </Group>
      {br.issues.length > 0 && <Text size="sm" c="dimmed">未解議題 {br.issues.length}：{br.issues.slice(0, 3).map((i) => `${i.event}：${i.issue}`).join('；')}{br.issues.length > 3 && ' …'}</Text>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1100}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>神策事件（正式事件名）</Table.Th><Table.Th>中文</Table.Th><Table.Th>分組</Table.Th><Table.Th>GA4 事件名</Table.Th><Table.Th>類型</Table.Th><Table.Th>參數</Table.Th><Table.Th>平台</Table.Th><Table.Th>備註</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{rows.map((r) => <Table.Tr key={r.sensors_event} bg={r.issues.length ? WARN_BG : undefined}>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{r.sensors_event}</Text>{r.aliases && <Text size="xs" c="dimmed">別名 {r.aliases}</Text>}</Table.Td>
              <Table.Td>{r.cn}</Table.Td>
              <Table.Td><Text size="xs">{r.group}</Text></Table.Td>
              <Table.Td><Text size="sm" ff="monospace" c={r.kind === 'not_ga' || r.kind === 'history_only' ? 'dimmed' : undefined}>{r.kind === 'not_ga' || r.kind === 'history_only' ? '—' : r.ga4_event}</Text></Table.Td>
              <Table.Td><StatusBadge tone={KIND_PILL[r.kind] ?? ''}>{r.kind_cn}</StatusBadge></Table.Td>
              <Table.Td><Text size="xs" ff="monospace" style={{ maxWidth: 300, overflowWrap: 'anywhere' }}>{r.params}</Text></Table.Td>
              <Table.Td><Text size="xs" c="dimmed" ff="monospace">{r.platforms.replace(/JavaScript/g, 'JS').replace(/服務端/g, 'Server')}</Text></Table.Td>
              <Table.Td><Text size="xs" c={r.issues.length ? 'red' : undefined}>{r.issues.length ? r.issues.join('；') : r.note}</Text></Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper><SectionTitle title="轉換規則" />
          <List size="sm" spacing="xs">
            <List.Item>正式事件名 = 神策事件名，歷史資料不改名；GA 上報轉換後的名字，接入時以橋接表轉回。</List.Item>
            <List.Item>去 $、CamelCase → snake_case、小寫、只留 a–z 0–9 _、不以數字開頭；避開 firebase_／google_／ga_ 前綴與 GA4 保留事件名（撞到加 sa_）。</List.Item>
            <List.Item>神策預置屬性對 GA 內建欄位（$page_url→page_location、$screen_name→firebase_screen、$utm_*→流量來源）；$ip 不送。</List.Item>
            <List.Item>限制：事件名 {br.limits.event_name_len}、參數名 {br.limits.param_name_len}、每事件參數 {br.limits.params_per_event}、user property {br.limits.user_properties_per_project} 個。</List.Item>
          </List></Paper>
        <Paper><SectionTitle title="用戶屬性 → GA user property" />
          <Table.ScrollContainer minWidth={420}>
            <Table>
              <Table.Thead><Table.Tr><Table.Th>屬性</Table.Th><Table.Th>敏感</Table.Th><Table.Th>送 GA</Table.Th><Table.Th>備註</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{br.user_properties.map((u) => <Table.Tr key={u.key}><Table.Td><Mono>{u.key}</Mono><Text size="xs" c="dimmed">{u.cn}</Text></Table.Td><Table.Td>{u.sensitivity}</Table.Td><Table.Td>{u.to_ga === 'user_property' ? '✓' : '—'}</Table.Td><Table.Td><Text size="xs">{u.note}</Text></Table.Td></Table.Tr>)}</Table.Tbody>
            </Table>
          </Table.ScrollContainer></Paper>
      </SimpleGrid>
    </Stack>
  );
}

function ExportsTab({ ex, inv, canUse, onChange }: { ex: Exports; inv: Inventory; canUse: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [sel, setSel] = useState<(ExportJob & { slices: ExportSlice[] }) | null>(null);
  const [f, setF] = useState({ dataset: 'events', date_from: '2026-10-01', date_to: '2026-10-03', method: 'api' as 'api' | 'impala', events: '', note: '' });
  const n = f.dataset === 'events' || f.dataset === 'ma' ? Math.max(0, daysBetween(f.date_from, f.date_to)) : 1;
  const create = async (e: FormEvent) => { e.preventDefault(); setBusy(true); setErr(null); try { await api.post('/v1/migration/exports', { ...f, events: f.events ? f.events.split(/[,，\s]+/).filter(Boolean) : [] }); setOpen(false); await onChange(); } catch (ex2) { setErr(ex2 instanceof ApiError ? ex2.message : '失敗'); } finally { setBusy(false); } };
  const rerun = async (id: string, force = false) => { setBusy(true); setErr(null); try { await api.post(`/v1/migration/exports/${id}/rerun`, { force }); await onChange(); if (sel?.id === id) await view(id); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } finally { setBusy(false); } };
  const view = async (id: string) => { try { setSel(await api.get<ExportJob & { slices: ExportSlice[] }>(`/v1/migration/exports/${id}`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">{ex.sensors_configured ? '神策連線已設定：任務會真的導出 → 代碼化／去個資 → 載入 → 對帳' : '未設定神策連線：任務會建立但停在「待連線」，不會假裝成功'}{!ex.clickhouse && ' · 未連接 ClickHouse：只導出不載入'}</Text>
        {canUse && <Button ml="auto" onClick={() => setOpen((v) => !v)}>＋ 建立導出任務</Button>}
      </Group>
      {open && <Paper component="form" onSubmit={create}><Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NativeSelect label="資料集" value={f.dataset} onChange={(e) => setF({ ...f, dataset: e.target.value })} data={inv.items.filter((i) => !i.done).map((i) => opt(i.id, i.cn))} />
          <NativeSelect label="方式" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value as 'api' | 'impala' })} data={[opt('api', '查詢 API（按日切片）'), opt('impala', 'Impala／HDFS 直讀（客戶內網執行，只追蹤）')]} />
          <TextInput label="事件（選填，逗號分隔；空 = 全部）" ff="monospace" value={f.events} onChange={(e) => setF({ ...f, events: e.target.value })} />
          <TextInput label="開始" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
          <TextInput label="結束" type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
          <TextInput label="備註" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </SimpleGrid>
        <Group gap="xs"><Button type="submit" variant="filled" disabled={busy || n <= 0}>{busy ? '建立並執行中…' : `建立（${n} 個分片）`}</Button><Text size="sm" c="dimmed">每片記錄來源、導出、載入筆數與校驗；差異 &gt; {pct(ex.rules.rerun_threshold)} 自動重跑；審計 migration.export.created。</Text></Group>
      </Stack></Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1200}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>任務</Table.Th><Table.Th>資料集</Table.Th><Table.Th>方式</Table.Th><Table.Th>範圍</Table.Th><Table.Th ta="right">分片</Table.Th><Table.Th ta="right">來源筆數</Table.Th><Table.Th ta="right">導出</Table.Th><Table.Th ta="right">載入</Table.Th><Table.Th ta="right">差異</Table.Th><Table.Th>狀態</Table.Th><Table.Th>最近</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{ex.items.length === 0 ? <Table.Tr><Table.Td colSpan={12}><Text size="sm" c="dimmed">還沒有導出任務。</Text></Table.Td></Table.Tr> : ex.items.map((j) => <Table.Tr key={j.id} bg={j.status === 'blocked' || j.status === 'warn' || j.status === 'failed' ? WARN_BG : undefined}>
              <Table.Td><Mono>{j.id}</Mono></Table.Td>
              <Table.Td>{inv.items.find((i) => i.id === j.dataset)?.cn ?? j.dataset}</Table.Td>
              <Table.Td><Text size="xs">{j.method === 'api' ? '查詢 API' : 'Impala／HDFS'}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{j.date_from} ～ {j.date_to}</Text></Table.Td>
              <Table.Td ta="right"><Mono>{j.done}/{j.slices}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{j.rows_src.toLocaleString()}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{j.rows_exported.toLocaleString()}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{j.rows_loaded.toLocaleString()}</Mono></Table.Td>
              <Table.Td ta="right"><Text span c={j.diff !== null && j.diff > ex.rules.rerun_threshold ? 'red' : undefined}><Mono>{j.diff === null ? '—' : pct(j.diff, 2)}</Mono></Text></Table.Td>
              <Table.Td><StatusBadge tone={JOB_STATUS[j.status].pill}>{JOB_STATUS[j.status].cn}</StatusBadge>{j.blocked_reason && <Text size="xs" c="dimmed">{j.blocked_reason}</Text>}</Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{j.last?.slice(0, 16).replace('T', ' ') ?? '—'}</Text></Table.Td>
              <Table.Td ta="right"><Group gap={4} justify="flex-end" wrap="nowrap"><Button size="compact-sm" variant="subtle" onClick={() => void view(j.id)}>分片</Button>{canUse && j.method === 'api' && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void rerun(j.id)}>重跑</Button>}</Group></Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {sel && <Paper><SectionTitle title={`${sel.id} 分片`} right={<Button size="compact-sm" variant="subtle" onClick={() => setSel(null)}>關閉</Button>}><Text size="xs" c="dimmed">{sel.slices.length} 片</Text></SectionTitle>
        <Table.ScrollContainer minWidth={900} maxHeight={360}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>日期</Table.Th><Table.Th>狀態</Table.Th><Table.Th ta="right">來源</Table.Th><Table.Th ta="right">導出</Table.Th><Table.Th ta="right">載入</Table.Th><Table.Th ta="right">隔離</Table.Th><Table.Th ta="right">重試</Table.Th><Table.Th>訊息</Table.Th><Table.Th>時間</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{sel.slices.map((s) => <Table.Tr key={s.day} bg={s.status === 'blocked' || s.status === 'failed' || s.status === 'mismatch' ? WARN_BG : undefined}>
              <Table.Td><Mono>{s.day}</Mono></Table.Td>
              <Table.Td><StatusBadge tone={s.status === 'loaded' ? 'blue' : s.status === 'exported' ? '' : s.status === 'pending' ? 'gray' : 'amber'}>{SLICE_CN[s.status] ?? s.status}</StatusBadge></Table.Td>
              <Table.Td ta="right"><Mono>{s.rows_src ?? '—'}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{s.rows_exported ?? '—'}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{s.rows_loaded ?? '—'}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{s.rows_quarantined ?? '—'}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{s.attempt}</Mono></Table.Td>
              <Table.Td><Text size="xs">{s.error ?? s.note ?? ''}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{s.at?.slice(0, 16).replace('T', ' ') ?? ''}</Text></Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {canUse && sel.slices.some((s) => s.attempt >= ex.rules.max_auto_rerun) && <Group gap="xs" mt="sm"><Button size="compact-sm" variant="subtle" color="red" disabled={busy} onClick={() => void rerun(sel.id, true)}>已達自動重跑上限：人工確認後強制重跑</Button></Group>}
      </Paper>}
      <Paper><SectionTitle title="載入管線" /><Text size="sm" c="dimmed">神策（Impala／HDFS Parquet 或查詢 API 按日切片）→ 導出工具（日 × 資料集分片，md5 + 列數校驗）→ Kafka raw.sensors（與 GA 新資料分 topic）→ Flink normalize（事件名保留、時間轉 UTC、login_id → HMAC 客戶代碼、$ip 丟棄）→ OneID（綁定先載、登入事件歸客戶、匿名事件歸匿名用戶）→ ClickHouse 歷史分區（OPTIMIZE、MV 補算、對帳表）。單機環境走 sensors_format 直接代碼化後載入 ClickHouse。</Text></Paper>
    </Stack>
  );
}

function ReconTab({ rc, err, project, setProject, days, setDays, onLoad }: { rc: Reconcile | null; err: string | null; project: string; setProject: (v: string) => void; days: number; setDays: (v: number) => void; onLoad: () => Promise<void> }) {
  const [sel, setSel] = useState(0);
  const ev = rc?.t5.items[sel] ?? rc?.t5.items[0];
  const series = useMemo<Series[]>(() => ev ? [{ key: 'sa', label: '神策', metric: 'sa', group: '', points: ev.series.map((p) => ({ x: p.day, y: p.sa, suppressed: false })) }, { key: 'ga', label: 'GA360', metric: 'ga', group: '', points: ev.series.map((p) => ({ x: p.day, y: p.ga, suppressed: false })) }] : [], [ev]);
  const bars = rc ? [['兩邊都出現', rc.t6.both], ['只在神策', rc.t6.only_sa], ['只在 GA360', rc.t6.only_ga]] as [string, number][] : [];
  const barOpt = useMemo(() => hbar(bars.map(([k, v]) => ({ value: k, users: v }))), [rc]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap" align="flex-end">
        <TextInput label="專案" aria-label="專案" value={project} onChange={(e) => setProject(e.target.value)} placeholder="空 = 全部" w={160} />
        <TextInput label="近 N 天" aria-label="近 N 天" type="number" min={1} max={366} value={days} onChange={(e) => setDays(Number(e.target.value))} w={90} />
        <Button variant="filled" onClick={() => void onLoad()}>重算</Button>
        {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
        <Text size="sm" c="dimmed">數字來自 ClickHouse：ingest_recon（T2）、events.source 神策 vs GA360（T5）、登入用戶兩邊同代碼（T6）。單機 seed 的樣本在 project=migration_demo。</Text>
      </Group>
      {rc && (
        <>
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Paper><SectionTitle title="T5 並行期：每日量 神策 vs GA360"><Text size="xs" c="dimmed">{ev ? `${ev.event} → ${ev.ga4_event} · 近 ${rc.days} 天` : '沒有並行期資料'}</Text></SectionTitle>{ev ? <LineChart series={series} height={220} /> : <Text size="sm" c="dimmed">沒有 source = sensors／ga360 的事件。</Text>}</Paper>
            <Paper><SectionTitle title="T6 登入用戶銜接"><Text size="xs" c="dimmed">期間內出現的客戶代碼（v1_）</Text></SectionTitle>
              <Chart option={barOpt} height={Math.max(100, bars.length * 26 + 16)} label="T6 登入用戶銜接" />
              <Text size="sm" c="dimmed" mt="sm">銜接率 <Text span fw={700}><Mono>{pct(rc.t6.rate)}</Mono></Text>（目標 ≥ {pct(rc.thresholds.t6_user_bridge ?? 0.95, 0)}）{rc.t6.ok ? '，通過' : '，未達標'}。匿名用戶不銜接，分析按時期分開看。</Text></Paper>
          </SimpleGrid>
          <Paper p={0} style={{ overflow: 'hidden' }}>
            <Table.ScrollContainer minWidth={760}>
              <Table>
                <Table.Thead><Table.Tr><Table.Th>神策事件</Table.Th><Table.Th>GA4 事件</Table.Th><Table.Th ta="right">神策／日</Table.Th><Table.Th ta="right">GA360／日</Table.Th><Table.Th ta="right">差異</Table.Th><Table.Th>判定</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{rc.t5.items.map((r, i) => <Table.Tr key={r.event} style={{ cursor: 'pointer' }} bg={!r.ok ? WARN_BG : undefined} onClick={() => setSel(i)}>
                  <Table.Td><Text fw={600} size="sm" ff="monospace">{r.event}</Text></Table.Td>
                  <Table.Td><Mono>{r.ga4_event}</Mono></Table.Td>
                  <Table.Td ta="right"><Mono>{r.sa_per_day.toLocaleString()}</Mono></Table.Td>
                  <Table.Td ta="right"><Mono>{r.ga_per_day.toLocaleString()}</Mono></Table.Td>
                  <Table.Td ta="right"><Text span c={r.ok ? undefined : 'red'}><Mono>{diffText(r.diff)}</Mono></Text></Table.Td>
                  <Table.Td><StatusBadge tone={r.ok ? 'blue' : 'red'}>{r.ok ? '通過' : `> ${pct(rc.thresholds.t5_parallel ?? 0.05, 0)} 查埋點`}</StatusBadge></Table.Td>
                </Table.Tr>)}</Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
          <Paper p={0} style={{ overflow: 'hidden' }}>
            <Group p="sm" pb={0} gap="xs" wrap="wrap"><Title order={3}>T2 筆數對帳（神策 → 載入）</Title><Text size="xs" c="dimmed">{rc.t2.ok === null ? '沒有載入紀錄' : rc.t2.ok ? '全部通過' : '有分片差異 > 門檻'}</Text></Group>
            <Table.ScrollContainer minWidth={760} maxHeight={300}>
              <Table>
                <Table.Thead><Table.Tr><Table.Th>任務</Table.Th><Table.Th>日期</Table.Th><Table.Th ta="right">來源</Table.Th><Table.Th ta="right">載入</Table.Th><Table.Th ta="right">隔離</Table.Th><Table.Th ta="right">差異</Table.Th><Table.Th>判定</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{rc.t2.items.map((r, i) => <Table.Tr key={i} bg={r.ok ? undefined : WARN_BG}><Table.Td><Mono>{r.task_id}</Mono></Table.Td><Table.Td><Mono>{r.day}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows_in.toLocaleString()}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows_loaded.toLocaleString()}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.quarantined}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.diff === null ? '—' : pct(r.diff, 2)}</Mono></Table.Td><Table.Td><StatusBadge tone={r.ok ? 'blue' : 'amber'}>{r.ok ? '通過' : '重跑'}</StatusBadge></Table.Td></Table.Tr>)}</Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Paper><SectionTitle title="對帳口徑" />
              <List size="sm" spacing="xs">
                <List.Item>T2 筆數：每事件每日 神策 → 導出 → 載入，去重後差異 &lt; {pct(rc.thresholds.t2_count ?? 0.001, 1)}。</List.Item>
                <List.Item>T5 並行：同一事件每日量 神策 vs GA360，差異 ≤ {pct(rc.thresholds.t5_parallel ?? 0.05, 0)}（同意拒絕、攔截、串流延遲）。</List.Item>
                <List.Item>T6 OneID：登入用戶 ≥ {pct(rc.thresholds.t6_user_bridge ?? 0.95, 0)} 兩邊同一客戶代碼；跨切換日漏斗無斷崖。</List.Item>
              </List></Paper>
            <Paper><SectionTitle title="口徑差異（已文件化）" /><List size="sm" spacing="xs">{rc.caveats.map((c) => <List.Item key={c.item}><b>{c.item}</b>：{c.note}</List.Item>)}</List></Paper>
          </SimpleGrid>
        </>
      )}
    </Stack>
  );
}
