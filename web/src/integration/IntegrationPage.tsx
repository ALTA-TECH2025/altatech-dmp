// 數據整合：數據源管理（連線：secret_ref、測試診斷、輪替、最小權限）→ 數據表管理（登記、探索欄位與敏感等級、重新探索）→ 同步任務（建立精靈、預覽、立即執行、執行紀錄）。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Group, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, KeyValue, Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { isSecretRef, looksLikeSecret, parseList, rotationText, runPill, runText, RUN_STATUS_CN, scheduleBody, SENS_PILL, STEP_CN, TASK_STATUS } from './integration';
import type { Connection, ConnectionCreate, ConnectionTest, Connector, ExtTable, Preview, Privileges, RunNow, SyncRun, SyncTask, SyncTaskCreate, TableCreate } from './types';

type Tab = 'connections' | 'tables' | 'tasks' | 'wizard';
const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const opt = (value: string, label: string) => ({ value, label });
const WARN_BG = 'var(--mantine-color-orange-light)';   // 需要注意的列（最近失敗）底色，跟著深淺色走

export function IntegrationPage() {
  return <RequireGrant module="integration"><IntegrationInner /></RequireGrant>;
}

function IntegrationInner() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>('connections');
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [conns, setConns] = useState<Connection[]>([]);
  const [tables, setTables] = useState<ExtTable[]>([]);
  const [tasks, setTasks] = useState<SyncTask[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canUse = can('integration', 'use'); const canAdmin = can('integration', 'admin');
  const reload = async () => {
    setErr(null);
    try {
      const [cs, cn, tb, tk] = await Promise.all([api.get<Connector[]>('/v1/integration/connectors'), api.get<Connection[]>('/v1/integration/connections'), api.get<ExtTable[]>('/v1/integration/tables'), api.get<SyncTask[]>('/v1/integration/sync-tasks')]);
      setConnectors(cs); setConns(cn); setTables(tb); setTasks(tk);
    } catch (e) { setErr(errText(e, '讀取失敗')); }
  };
  useEffect(() => { void reload(); }, []);
  const run = async (fn: () => Promise<void>, fallback: string) => { setBusy(true); setErr(null); try { await fn(); await reload(); } catch (e) { setErr(errText(e, fallback)); } finally { setBusy(false); } };
  const warnTasks = tasks.filter((t) => t.status !== 'active').length;
  return (
    <div className="page">
      <PageHeader title="數據整合" lead="數據源管理 → 數據表管理 → 同步任務（目標：事件表／用戶表／維度表；全量／增量微批）→ 清洗與隔離。連線只存密鑰庫參照，畫面永遠看不到明文；任務是建模產物（模型 yaml）＋來源表＋排程，立即執行會寫入 ClickHouse 並三方對帳。" />
      <SegmentedControl mb="md" value={tab} onChange={(v) => setTab(v as Tab)} data={[opt('connections', `數據源 ${conns.length}`), opt('tables', `數據表 ${tables.length}`), opt('tasks', `同步任務 ${tasks.length}${warnTasks ? ` ⚠${warnTasks}` : ''}`), opt('wizard', '建立任務')]} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {tab === 'connections' && <ConnectionsTab connectors={connectors} conns={conns} busy={busy} canUse={canUse} canAdmin={canAdmin} run={run} />}
      {tab === 'tables' && <TablesTab conns={conns} tables={tables} busy={busy} canUse={canUse} run={run} />}
      {tab === 'tasks' && <TasksTab tasks={tasks} busy={busy} canUse={canUse} run={run} onNew={() => setTab('wizard')} />}
      {tab === 'wizard' && <Wizard tables={tables} tasks={tasks} canUse={canUse} onDone={async () => { await reload(); setTab('tasks'); }} />}
    </div>
  );
}

/* ------------------------------------------------------------------ 數據源 */
function ConnectionsTab({ connectors, conns, busy, canUse, canAdmin, run }: { connectors: Connector[]; conns: Connection[]; busy: boolean; canUse: boolean; canAdmin: boolean; run: (fn: () => Promise<void>, fb: string) => Promise<void> }) {
  const [creating, setCreating] = useState(false);
  const [test, setTest] = useState<{ id: string; res: ConnectionTest } | null>(null);
  const [priv, setPriv] = useState<Privileges | null>(null);
  const [rotateId, setRotateId] = useState<string | null>(null);
  const [newRef, setNewRef] = useState('');
  const byType = Object.fromEntries(connectors.map((c) => [c.type, c]));
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">密鑰原則：平台只存 secret_ref（Secret Manager／OpenBao／K8s Secret），執行節點以 Workload Identity 取值；密碼型憑證 90 天輪替、到期前 14 天提醒。</Text>
        {canAdmin && <Button variant="filled" ml="auto" onClick={() => setCreating(true)}>＋ 新增連線</Button>}
      </Group>
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1000}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>連線</Table.Th><Table.Th>類型 / 鑑權</Table.Th><Table.Th>密鑰 / 輪替</Table.Th><Table.Th>最後測試</Table.Th><Table.Th ta="right">表 / 任務</Table.Th><Table.Th>狀態</Table.Th><Table.Th>動作</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{conns.map((c) => { const rot = rotationText(c.rotation); return (
              <Table.Tr key={c.id}>
                <Table.Td><Text fw={600} size="sm" ff="monospace">{c.id}</Text><Text size="xs" c="dimmed">{Object.entries(c.fields).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${String(v)}`).join(' · ')}{c.owner ? ` · 負責人 ${c.owner}` : ''}</Text></Table.Td>
                <Table.Td>{byType[c.type]?.cn ?? c.type}<Text size="xs" c="dimmed" ff="monospace">{c.auth_method}{c.tls_verify ? ' · TLS 驗證' : ''}</Text></Table.Td>
                <Table.Td><StatusBadge tone={rot.pill}>{rot.text}</StatusBadge>{Object.entries(c.secrets).map(([k, s]) => <Text key={k} size="xs" c="dimmed" ff="monospace">{k}: {s.value} → {s.ref}</Text>)}</Table.Td>
                <Table.Td>{c.last_test ? <><StatusBadge tone={c.last_test.ok ? 'blue' : 'red'}>{c.last_test.ok ? '通過' : `失敗於 ${STEP_CN[c.last_test.failed_step as keyof typeof STEP_CN] ?? c.last_test.failed_step}`}</StatusBadge><Text size="xs" c="dimmed">{c.last_test.at.slice(0, 16).replace('T', ' ')}</Text></> : <Text size="xs" c="dimmed">未測試</Text>}</Table.Td>
                <Table.Td ta="right"><Mono>{c.tables} / {c.tasks}</Mono></Table.Td>
                <Table.Td><StatusBadge tone={c.enabled ? 'blue' : 'gray'}>{c.enabled ? '啟用' : '已停用'}</StatusBadge></Table.Td>
                <Table.Td><Group gap={4} wrap="nowrap">
                  {canUse && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void run(async () => { setTest({ id: c.id, res: await api.post<ConnectionTest>(`/v1/integration/connections/${c.id}/test`) }); }, '測試失敗')}>測試</Button>}
                  <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void run(async () => { setPriv(await api.get<Privileges>(`/v1/integration/connections/${c.id}/privileges`)); }, '讀取失敗')}>權限</Button>
                  {canAdmin && c.rotation.kind === 'rotating' && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => { setRotateId(c.id); setNewRef(''); }}>輪替</Button>}
                  {canAdmin && c.enabled && <Button size="compact-sm" variant="subtle" color="red" disabled={busy} onClick={() => void run(async () => { await api.del(`/v1/integration/connections/${c.id}`); }, '停用失敗')}>停用</Button>}
                </Group></Table.Td>
              </Table.Tr>); })}
              {!conns.length && <Table.Tr><Table.Td colSpan={7}><EmptyState>還沒有連線。先新增數據源（數據中台、BigQuery、舊平台歷史…）。</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {test && <Paper><SectionTitle title={`連線測試 · ${test.id}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setTest(null)}>關閉</Button>}><StatusBadge tone={test.res.ok ? 'blue' : 'red'}>{test.res.ok ? '通過' : '失敗'}</StatusBadge></SectionTitle>
        <Table.ScrollContainer minWidth={480}><Table><Table.Tbody>{test.res.steps.map((s) => <Table.Tr key={s.step}><Table.Td w={120}><Text fw={600} size="sm">{STEP_CN[s.step]}</Text></Table.Td><Table.Td><StatusBadge tone={s.ok ? (s.detail === '不適用' ? 'gray' : 'blue') : 'red'}>{s.ok ? (s.detail === '不適用' ? '不適用' : '通過') : '失敗'}</StatusBadge></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{s.detail}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed">{s.fix ?? ''}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>
        <Text size="sm" c="dimmed" mt="sm">API 節點只做 DNS 與 TCP；鑑權、權限、CDC 前置、讀樣本由執行節點以密鑰探測，結果會回寫到這裡。</Text></Paper>}
      {priv && <Paper><SectionTitle title={`最小權限自檢 · ${priv.conn_id}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setPriv(null)}>關閉</Button>}><Text size="xs" c="dimmed">{priv.verified_at ? `驗證於 ${priv.verified_at.slice(0, 16)}` : priv.note}</Text></SectionTitle>
        <Table.ScrollContainer minWidth={480}><Table><Table.Tbody>{priv.items.map((it, i) => <Table.Tr key={i}><Table.Td w={80}><Mono>{it.scope}</Mono></Table.Td><Table.Td>{it.privilege}</Table.Td><Table.Td>{it.checked === null ? <StatusBadge tone="gray">未驗證</StatusBadge> : it.checked ? <StatusBadge tone="blue">✓</StatusBadge> : <StatusBadge tone="red">✗</StatusBadge>}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer></Paper>}
      {rotateId && <Paper><SectionTitle title={`輪替密鑰 · ${rotateId}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setRotateId(null)}>取消</Button>} />
        <Group gap="xs" wrap="wrap"><TextInput aria-label="新的 secret_ref" style={{ flex: '1 1 320px' }} placeholder="新的 secret_ref（projects/…/versions/N、kv/data/dmp/…#key、secret://ns/name/key）" value={newRef} onChange={(e) => setNewRef(e.target.value)} />
          <Button variant="filled" disabled={busy || !isSecretRef(newRef)} onClick={() => void run(async () => { await api.post(`/v1/integration/connections/${rotateId}/rotate`, { secret_ref: newRef }); setRotateId(null); }, '輪替失敗')}>輪替（新舊並存 24 小時）</Button></Group>
        {newRef && !isSecretRef(newRef) && <Text size="sm" c="dimmed" mt="sm">不是合法的密鑰參照；請先把密鑰放進密鑰庫，這裡只填參照。</Text>}</Paper>}
      {creating && <ConnectionForm connectors={connectors} busy={busy} onClose={() => setCreating(false)} onSave={(body) => run(async () => { await api.post('/v1/integration/connections', body); setCreating(false); }, '建立失敗')} />}
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        {connectors.map((c) => <Paper key={c.type}><SectionTitle title={c.cn}><StatusBadge tone="gray">{c.type}</StatusBadge></SectionTitle>
          <Text size="sm" c="dimmed">鑑權（建議順序）：{c.auth.join(' › ')}<br />驅動：{c.driver.name ?? '—'}（{c.driver.license ?? '—'}）<br />能力：{Object.entries(c.capabilities).map(([k, v]) => `${k}=${v}`).join('、')}</Text></Paper>)}
      </SimpleGrid>
    </Stack>
  );
}

function ConnectionForm({ connectors, busy, onClose, onSave }: { connectors: Connector[]; busy: boolean; onClose: () => void; onSave: (b: ConnectionCreate) => Promise<void> }) {
  const [type, setType] = useState(connectors[0]?.type ?? '');
  const spec = connectors.find((c) => c.type === type);
  const [method, setMethod] = useState('');
  const m = spec?.auth_methods[method || spec.auth[0] || ''];
  const methodKey = method || spec?.auth[0] || '';
  const [id, setId] = useState('');
  const [owner, setOwner] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [refs, setRefs] = useState<Record<string, string>>({});
  const plain = Object.entries(fields).filter(([k, v]) => v && looksLikeSecret(k, v)).map(([k]) => k);
  const refBad = (m?.secret_fields ?? []).filter((k) => !isSecretRef(refs[k] ?? ''));
  const ok = /^[a-z][a-z0-9_]{2,31}$/.test(id) && !!m && (m.fields.every((f) => (fields[f] ?? '').trim())) && !refBad.length && !plain.length;
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title="新增連線" right={<Button size="compact-sm" variant="subtle" onClick={onClose}>取消</Button>}><Text size="xs" c="dimmed">非機密欄位直接填；密鑰只填密鑰庫參照</Text></SectionTitle>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
          <TextInput label="連線 id" aria-label="連線 id" placeholder="dwh_trade" value={id} onChange={(e) => setId(e.target.value)} />
          <NativeSelect label="類型" aria-label="類型" value={type} onChange={(e) => { setType(e.target.value); setMethod(''); setFields({}); setRefs({}); }} data={connectors.map((c) => opt(c.type, c.cn))} />
          <NativeSelect label="鑑權" aria-label="鑑權" value={methodKey} onChange={(e) => { setMethod(e.target.value); setRefs({}); }} data={(spec?.auth ?? []).map((a) => opt(a, `${a}${spec?.auth_methods[a]?.rotation_days ? `（${spec.auth_methods[a]?.rotation_days} 天輪替）` : '（短效／免密鑰）'}`))} />
          <TextInput label="負責人" aria-label="負責人" value={owner} onChange={(e) => setOwner(e.target.value)} />
        </SimpleGrid>
        {m && <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>{m.fields.map((f) => <TextInput key={f} label={f} aria-label={f} value={fields[f] ?? ''} onChange={(e) => setFields({ ...fields, [f]: e.target.value })} />)}</SimpleGrid>}
        {m && m.secret_fields.length > 0 && <SimpleGrid cols={{ base: 1, sm: 2 }}>{m.secret_fields.map((f) => <TextInput key={f} label={`${f}（secret_ref）`} aria-label={`${f} secret_ref`} placeholder="kv/data/dmp/prod/<conn>#<key>" value={refs[f] ?? ''} onChange={(e) => setRefs({ ...refs, [f]: e.target.value })} />)}</SimpleGrid>}
        {m?.notes && <Text size="sm" c="dimmed">{m.notes}</Text>}
        {plain.length > 0 && <Alert color="red" variant="light">欄位 {plain.join('、')} 疑似明文密鑰：請改放密鑰庫，只填參照。</Alert>}
        <Group gap="xs"><Button variant="filled" disabled={busy || !ok} onClick={() => void onSave({ id, type, auth_method: methodKey, fields: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, /^\d+$/.test(v) ? Number(v) : v])), secret_refs: refs, tls_verify: true, owner })}>建立連線</Button>
          {!ok && <Text size="xs" c="dimmed">id 小寫英數底線 3–32 字；必填欄位與每個密鑰參照都要合法</Text>}</Group>
      </Stack>
    </Paper>
  );
}

/* ------------------------------------------------------------------ 數據表 */
function TablesTab({ conns, tables, busy, canUse, run }: { conns: Connection[]; tables: ExtTable[]; busy: boolean; canUse: boolean; run: (fn: () => Promise<void>, fb: string) => Promise<void> }) {
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [d, setD] = useState<{ connection: string; table: string; pk: string; inc: string; kind: 'csv' | 'jsonl'; path: string; fields: string }>({ connection: '', table: '', pk: '', inc: '', kind: 'csv', path: '', fields: '' });
  const body = (): TableCreate => ({ connection: d.connection, table: d.table.trim(), pk: parseList(d.pk), incremental_field: d.inc.trim() || null, ...(d.path.trim() ? { source: { kind: d.kind, path: d.path.trim() } } : { fields: parseList(d.fields).map((n) => ({ name: n })) }) });
  const ok = d.table.trim() && (d.path.trim() || parseList(d.fields).length);
  const openTable = open ? tables.find((x) => x.id === open) : undefined;
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">登記外部表：設主鍵與增量欄位、探索欄位型別，個資欄位（姓名、電話、信箱、身分證…）自動標 L4，建模時不可映射。來源 Schema 變了按任務的策略處理。</Text>
        {canUse && <Button variant="filled" ml="auto" onClick={() => setCreating(true)}>＋ 登記數據表</Button>}
      </Group>
      {creating && <Paper><Stack gap="md">
        <SectionTitle title="登記數據表" right={<Button size="compact-sm" variant="subtle" onClick={() => setCreating(false)}>取消</Button>} />
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
          <NativeSelect label="連線" aria-label="連線" value={d.connection} onChange={(e) => setD({ ...d, connection: e.target.value })} data={[opt('', '（檔案來源）'), ...conns.map((c) => opt(c.id, c.id))]} />
          <TextInput label="表" aria-label="表" placeholder="dbo.fact_order_hist" value={d.table} onChange={(e) => setD({ ...d, table: e.target.value })} />
          <TextInput label="主鍵" aria-label="主鍵" placeholder="ORD_NO, STATUS" value={d.pk} onChange={(e) => setD({ ...d, pk: e.target.value })} />
          <TextInput label="增量欄位" aria-label="增量欄位" placeholder="UPDATED_AT" value={d.inc} onChange={(e) => setD({ ...d, inc: e.target.value })} />
        </SimpleGrid>
        <Group gap="xs" wrap="wrap" align="flex-end">
          <NativeSelect label="探索樣本檔" aria-label="格式" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as 'csv' | 'jsonl' })} data={[opt('csv', 'csv'), opt('jsonl', 'jsonl')]} w={110} />
          <TextInput aria-label="樣本路徑" style={{ flex: '1 1 240px' }} placeholder="/stage/fact_order_hist.csv（執行節點可讀的路徑）" value={d.path} onChange={(e) => setD({ ...d, path: e.target.value })} />
          <Text size="xs" c="dimmed" pb={8}>或直接列欄位</Text>
          <TextInput aria-label="欄位清單" style={{ flex: '1 1 240px' }} placeholder="ORD_NO, STATUS, CUST_NAME…" value={d.fields} onChange={(e) => setD({ ...d, fields: e.target.value })} />
        </Group>
        <Group gap="xs"><Button variant="filled" disabled={busy || !ok} onClick={() => void run(async () => { await api.post('/v1/integration/tables', body()); setCreating(false); }, '登記失敗')}>登記並探索</Button></Group>
      </Stack></Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1000}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>數據源 / 表</Table.Th><Table.Th>主鍵</Table.Th><Table.Th>增量欄位</Table.Th><Table.Th ta="right">欄位</Table.Th><Table.Th ta="right">L4</Table.Th><Table.Th ta="right">Schema 版本</Table.Th><Table.Th>變更</Table.Th><Table.Th>使用它的任務</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{tables.map((t) => { const l4 = t.fields.filter((f) => f.sensitivity === 'L4').length; const last = t.changes[0]; return (
              <Table.Tr key={t.id}>
                <Table.Td><Text fw={600} size="sm" ff="monospace">{t.table}</Text><Text size="xs" c="dimmed" ff="monospace">{t.connection || '檔案'} · {t.id}</Text></Table.Td>
                <Table.Td><Mono>{t.pk.join(', ') || '—'}</Mono></Table.Td>
                <Table.Td><Mono>{t.incremental_field ?? '—'}</Mono></Table.Td>
                <Table.Td ta="right"><Mono>{t.fields.length}</Mono></Table.Td>
                <Table.Td ta="right">{l4 ? <StatusBadge tone="red">{l4}</StatusBadge> : '0'}</Table.Td>
                <Table.Td ta="right"><Mono>v{t.schema_version}</Mono></Table.Td>
                <Table.Td>{last ? <StatusBadge tone="amber">{last.items.length} 項 · {last.at.slice(0, 10)}</StatusBadge> : <StatusBadge tone="gray">無</StatusBadge>}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed" ff="monospace">{t.used_by.join(', ') || '—'}</Text></Table.Td>
                <Table.Td><Group gap={4} wrap="nowrap"><Button size="compact-sm" variant="subtle" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? '收合' : '欄位'}</Button>
                  {canUse && t.source?.path && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void run(async () => { await api.post(`/v1/integration/tables/${t.id}/rescan`); }, '重新探索失敗')}>重新探索</Button>}</Group></Table.Td>
              </Table.Tr>); })}
              {!tables.length && <Table.Tr><Table.Td colSpan={9}><EmptyState>還沒有登記的數據表</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {openTable && <Paper><SectionTitle title={`${openTable.table} 的欄位`}><Text size="xs" c="dimmed">L4 不可映射、不回顯樣本</Text></SectionTitle>
        <Table.ScrollContainer minWidth={560}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>欄位</Table.Th><Table.Th>型別</Table.Th><Table.Th>敏感等級</Table.Th><Table.Th ta="right">空值率</Table.Th><Table.Th>樣本</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{openTable.fields.map((f) => <Table.Tr key={f.name}><Table.Td><Mono>{f.name}</Mono></Table.Td><Table.Td><Mono>{f.type}</Mono></Table.Td><Table.Td><StatusBadge tone={SENS_PILL[f.sensitivity]}>{f.sensitivity}</StatusBadge></Table.Td><Table.Td ta="right"><Mono>{f.null_rate != null ? `${Math.round(f.null_rate * 100)}%` : '—'}</Mono></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{f.sensitivity === 'L4' ? '（不回顯）' : f.sample ?? ''}</Text></Table.Td></Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {openTable.changes.length > 0 && <Text size="sm" c="dimmed" mt="sm">最近變更：{openTable.changes[0]?.items.map((c) => `${c.kind} ${c.field}${c.from ? ` ${c.from}→${c.to}` : ''}`).join('、')}</Text>}</Paper>}
    </Stack>
  );
}

/* ------------------------------------------------------------------ 同步任務 */
function TasksTab({ tasks, busy, canUse, run, onNew }: { tasks: SyncTask[]; busy: boolean; canUse: boolean; run: (fn: () => Promise<void>, fb: string) => Promise<void>; onNew: () => void }) {
  const [runs, setRuns] = useState<{ id: string; rows: SyncRun[] } | null>(null);
  const [pv, setPv] = useState<{ id: string; res: Preview } | null>(null);
  const [last, setLast] = useState<RunNow | null>(null);
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">目標類型決定寫入：事件表只增量（event_id 去重）、用戶表 upsert、維度表 upsert 或全量；依賴的上游同窗口成功才跑；失敗按退避重試；Schema 變更依策略自動映射／忽略／暫停並通知。</Text>
        {canUse && <Button variant="filled" ml="auto" onClick={onNew}>＋ 建立任務</Button>}
      </Group>
      {last && <Paper><SectionTitle title={`立即執行 · ${last.cn}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setLast(null)}>關閉</Button>}><StatusBadge tone={runPill(last.status)}>{RUN_STATUS_CN[last.status] ?? last.status}</StatusBadge></SectionTitle>
        <SimpleGrid cols={{ base: 2, sm: 5 }}>{([['讀入', last.rows_in], ['通過', last.rows_ok], ['隔離', last.rows_quarantined], ['拒絕', last.rows_rejected], ['載入', last.rows_loaded]] as [string, number | null][]).map(([k, v]) => <StatTile key={k} label={k} value={(v ?? 0).toLocaleString()} />)}</SimpleGrid>
        {last.error && <Alert color="red" variant="light" mt="sm">{last.error}</Alert>}</Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1100}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>任務</Table.Th><Table.Th>來源</Table.Th><Table.Th>目標</Table.Th><Table.Th style={{ whiteSpace: 'nowrap' }}>同步方式</Table.Th><Table.Th>排程 / 依賴</Table.Th><Table.Th>最近執行</Table.Th><Table.Th>狀態</Table.Th><Table.Th>動作</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{tasks.map((t) => (
              <Table.Tr key={t.id} bg={t.status === 'error' ? WARN_BG : undefined}>
                <Table.Td><Text fw={600} size="sm">{t.cn}</Text><Text size="xs" c="dimmed" ff="monospace">{t.id} · {t.model_path.split('/').slice(-1)[0]}</Text></Table.Td>
                <Table.Td><Mono>{t.source.table ?? t.source.path?.split('/').slice(-1)[0] ?? '—'}</Mono><Text size="xs" c="dimmed">{t.source.connection ?? '檔案'}{t.source.incremental_field ? ` · 增量 ${t.source.incremental_field}` : ''}</Text></Table.Td>
                <Table.Td><Badge color="gray">{t.target.type}</Badge><Text size="xs" c="dimmed" ff="monospace">{t.target.table} · {t.target.write}{t.events.length ? ` · ${t.events.join('／')}` : ''}</Text></Table.Td>
                <Table.Td>{{ full: '全量', incremental: '增量', stream: '流' }[t.mode]}</Table.Td>
                <Table.Td><Mono>{t.schedule}</Mono><Text size="xs" c="dimmed">{t.depends_on.length ? `依賴 ${t.depends_on.join(', ')}` : '無依賴'} · {t.schema_change}</Text></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{runText(t.last)}</Text>{t.watermark ? <Text size="xs" c="dimmed" ff="monospace">水位 {t.watermark}</Text> : null}</Table.Td>
                <Table.Td><StatusBadge tone={TASK_STATUS[t.status].pill}>{TASK_STATUS[t.status].cn}</StatusBadge>{t.paused_reason && <Text size="xs" c="dimmed">{t.paused_reason}</Text>}</Table.Td>
                <Table.Td><Group gap={4} wrap="nowrap">
                  {canUse && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void run(async () => { setPv({ id: t.id, res: await api.post<Preview>(`/v1/integration/sync-tasks/${t.id}/preview`, { limit: 1000 }) }); }, '預覽失敗')}>預覽</Button>}
                  {canUse && <Button size="compact-sm" variant="filled" disabled={busy} onClick={() => void run(async () => { setLast(await api.post<RunNow>(`/v1/integration/sync-tasks/${t.id}/run`)); }, '執行失敗')}>立即執行</Button>}
                  <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void run(async () => { setRuns({ id: t.id, rows: await api.get<SyncRun[]>(`/v1/integration/sync-tasks/${t.id}/runs?limit=20`) }); }, '讀取失敗')}>紀錄</Button>
                </Group></Table.Td>
              </Table.Tr>))}
              {!tasks.length && <Table.Tr><Table.Td colSpan={8}><EmptyState>還沒有同步任務，從「建立任務」開始。</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {pv && <Paper><SectionTitle title={`預覽驗證 · ${pv.id}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setPv(null)}>關閉</Button>}><Text size="xs" c="dimmed">以樣本跑映射與清洗，不寫入</Text></SectionTitle>
        <SimpleGrid cols={{ base: 2, sm: 5 }}>{([['樣本', pv.res.summary.in], ['通過', pv.res.summary.ok], ['隔離', pv.res.summary.quarantined], ['拒絕', pv.res.summary.rejected], ['個資命中', pv.res.pii_hits]] as [string, number][]).map(([k, v]) => <StatTile key={k} label={k} value={v.toLocaleString()} />)}</SimpleGrid>
        {pv.res.new_fields.length > 0 && <Text size="sm" c="dimmed" mt="sm">來源新欄位（未映射）：{pv.res.new_fields.join('、')}</Text>}
        {pv.res.quarantined.length > 0 && <Table.ScrollContainer minWidth={480}><Table mt="sm"><Table.Thead><Table.Tr><Table.Th>隔離原因</Table.Th><Table.Th>列（摘要）</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{pv.res.quarantined.slice(0, 10).map((q, i) => <Table.Tr key={i}><Table.Td>{q.reason}</Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{Object.entries(q.row).slice(0, 6).map(([k, v]) => `${k}=${String(v)}`).join(' ')}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>}
        {pv.res.sample_ok.length > 0 && <Text size="sm" c="dimmed" mt="sm">通過樣本：{pv.res.sample_ok.slice(0, 2).map((r) => JSON.stringify(r)).join('  ')}</Text>}</Paper>}
      {runs && <Paper p={0} style={{ overflow: 'hidden' }}>
        <Group p="sm" pb={0} justify="space-between"><SectionTitle title={`執行紀錄 · ${runs.id}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setRuns(null)}>關閉</Button>} /></Group>
        <Table.ScrollContainer minWidth={1000}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>窗口</Table.Th><Table.Th>觸發</Table.Th><Table.Th>狀態</Table.Th><Table.Th ta="right">讀入</Table.Th><Table.Th ta="right">通過</Table.Th><Table.Th ta="right">隔離</Table.Th><Table.Th ta="right">拒絕</Table.Th><Table.Th ta="right">載入</Table.Th><Table.Th>水位</Table.Th><Table.Th>錯誤 / 日誌</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{runs.rows.map((r) => <Table.Tr key={r.id}><Table.Td><Mono>{r.scheduled_for.slice(0, 16).replace('T', ' ')}</Mono><Text size="xs" c="dimmed">第 {r.attempt} 次</Text></Table.Td><Table.Td>{r.trigger}</Table.Td><Table.Td><StatusBadge tone={runPill(r.status)}>{RUN_STATUS_CN[r.status] ?? r.status}</StatusBadge></Table.Td>
              <Table.Td ta="right"><Mono>{r.rows_in ?? '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows_ok ?? '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows_quarantined ?? '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows_rejected ?? '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{r.rows_loaded ?? '—'}</Mono></Table.Td>
              <Table.Td><Text size="xs" c="dimmed" ff="monospace">{r.watermark_from ?? '—'} → {r.watermark_to ?? '—'}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed">{r.error ?? r.log.slice(-2).join(' / ')}</Text></Table.Td></Table.Tr>)}
              {!runs.rows.length && <Table.Tr><Table.Td colSpan={10}><EmptyState>沒有執行紀錄</EmptyState></Table.Td></Table.Tr>}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>}
    </Stack>
  );
}

/* ------------------------------------------------------------------ 建立任務精靈 */
function Wizard({ tables, tasks, canUse, onDone }: { tables: ExtTable[]; tasks: SyncTask[]; canUse: boolean; onDone: () => Promise<void> }) {
  const [step, setStep] = useState(1);
  const [d, setD] = useState({ id: '', cn: '', table_id: tables[0]?.id ?? '', model_path: 'backend/integration/models.example.yaml', mode: 'incremental' as 'full' | 'incremental', schedKind: 'interval' as 'interval' | 'cron', sched: '15', depends: '', schema_change: 'pause_and_notify' as SyncTaskCreate['schema_change'], retries: 3 });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<SyncTask | null>(null);
  const [pv, setPv] = useState<Preview | null>(null);
  const table = tables.find((t) => t.id === d.table_id);
  const sched = scheduleBody(d.schedKind, d.sched);
  const idOk = /^[a-z][a-z0-9_]{2,47}$/.test(d.id) && !tasks.some((t) => t.id === d.id);
  const steps = ['來源數據表', '模型（建模產物）', '排程與策略', '建立與預覽'];
  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const body: SyncTaskCreate = { id: d.id, cn: d.cn || d.id, model_path: d.model_path.trim(), source: { table_id: d.table_id }, schedule: sched ?? { interval_min: 15 }, mode: d.mode, depends_on: parseList(d.depends), schema_change: d.schema_change, retries: d.retries };
      const t = await api.post<SyncTask>('/v1/integration/sync-tasks', body); setCreated(t);
      setPv(await api.post<Preview>(`/v1/integration/sync-tasks/${t.id}/preview`, { limit: 1000 }));
    } catch (e) { setErr(errText(e, '建立失敗')); } finally { setBusy(false); }
  };
  if (!canUse) return <Paper><Text size="sm" c="dimmed">你的角色只能查看數據整合，建立任務需要 integration.use（資訊管理）。</Text></Paper>;
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title="建立同步任務"><Text size="xs" c="dimmed">數據表 → 模型 → 排程 → 建立並預覽 → 立即執行</Text></SectionTitle>
        <SegmentedControl value={String(step)} readOnly={!!created} onChange={(v) => { if (!created) setStep(Number(v)); }} data={steps.map((s, i) => opt(String(i + 1), `${i + 1}. ${s}`))} />
        {err && <Alert color="red" variant="light">{err}</Alert>}
        {step === 1 && <Stack gap="sm">
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="任務 id" aria-label="任務 id" placeholder="sync_orders" value={d.id} onChange={(e) => setD({ ...d, id: e.target.value })} />
            <TextInput label="名稱" aria-label="任務名稱" placeholder="委託事實表 → 委託／成交事件" value={d.cn} onChange={(e) => setD({ ...d, cn: e.target.value })} />
            <NativeSelect label="來源數據表" aria-label="來源數據表" value={d.table_id} onChange={(e) => setD({ ...d, table_id: e.target.value })} data={tables.map((t) => opt(t.id, `${t.table}（${t.connection || '檔案'}）`))} />
          </SimpleGrid>
          {table ? <Text size="sm" c="dimmed">主鍵 {table.pk.join(', ') || '—'} · 增量欄位 {table.incremental_field ?? '—'} · {table.fields.length} 欄，其中 L4 {table.fields.filter((f) => f.sensitivity === 'L4').length} 欄不可映射</Text> : <Text size="sm" c="dimmed">先到「數據表」登記來源表。</Text>}
          {d.id && !idOk && <Text size="sm" c="dimmed">id 小寫英數底線 3–48 字且不可重複。</Text>}
        </Stack>}
        {step === 2 && <Stack gap="sm">
          <TextInput label="模型檔（建模精靈產物，yaml）" aria-label="模型檔" value={d.model_path} onChange={(e) => setD({ ...d, model_path: e.target.value })} />
          <Text size="sm" c="dimmed">模型定義事件化（狀態機 → 事件名）、時間欄位、identities 代碼化（token_v1／fpe）、欄位映射與轉換、drop_fields（個資）、清洗規則（必填、枚舉、範圍、時間窗、個資掃描）、去重鍵。範例：<Mono>backend/integration/models.example.yaml</Mono>（委託事實表 → bond_order／bond_deal／order_cancelled／order_rejected）、<Mono>models.instrument.yaml</Mono>（商品主檔 → dim_instrument）。建立時會對語意層驗證：事件名要在字典裡、L4 不可映射、身分必須代碼化。</Text>
        </Stack>}
        {step === 3 && <Stack gap="sm">
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
            <NativeSelect label="同步方式" aria-label="同步方式" value={d.mode} onChange={(e) => setD({ ...d, mode: e.target.value as 'full' | 'incremental' })} data={[opt('incremental', '增量（水位 = 增量欄位最大值）'), opt('full', '全量')]} />
            <NativeSelect label="排程" aria-label="排程類型" value={d.schedKind} onChange={(e) => setD({ ...d, schedKind: e.target.value as 'interval' | 'cron', sched: e.target.value === 'cron' ? '*/15 * * * *' : '15' })} data={[opt('interval', '每 N 分鐘'), opt('cron', 'cron')]} />
            <TextInput label="排程值" aria-label="排程值" value={d.sched} onChange={(e) => setD({ ...d, sched: e.target.value })} />
            <TextInput label="依賴任務" aria-label="依賴任務" placeholder="sync_instrument" value={d.depends} onChange={(e) => setD({ ...d, depends: e.target.value })} />
            <NativeSelect label="Schema 變更" aria-label="Schema 變更" value={d.schema_change} onChange={(e) => setD({ ...d, schema_change: e.target.value as SyncTaskCreate['schema_change'] })} data={[opt('pause_and_notify', '暫停並通知'), opt('auto_map', '自動映射（記錄後照跑）'), opt('ignore', '忽略')]} />
            <TextInput label="重試次數" aria-label="重試次數" type="number" min={0} max={10} value={d.retries} onChange={(e) => setD({ ...d, retries: Number(e.target.value) })} />
          </SimpleGrid>
          {!sched && <Text size="sm" c="dimmed">排程值不合法：每 N 分鐘要正整數；cron 要 5 欄。</Text>}
          <Text size="sm" c="dimmed">第一階段沒有流任務：即時需求用每 15 分鐘微批。上游（依賴）同一窗口成功才會跑；失敗按退避重試，用完次數告警。</Text>
        </Stack>}
        {step === 4 && <Stack gap="sm">
          <KeyValue rows={[
            ['任務', <Mono key="id">{d.id} · {d.cn || d.id}</Mono>],
            ['來源', <Mono key="src">{table?.table ?? '—'}{table?.incremental_field ? ` · 增量 ${table.incremental_field}` : ''}</Mono>],
            ['模型', <Mono key="model">{d.model_path}</Mono>],
            ['排程', <Mono key="sched">{d.mode} · {d.schedKind === 'cron' ? d.sched : `every ${d.sched}m`}{d.depends ? ` · 依賴 ${d.depends}` : ''} · {d.schema_change} · 重試 {d.retries}</Mono>],
          ]} />
          {!created ? <Group gap="xs"><Button variant="filled" disabled={busy || !idOk || !table || !sched || !d.model_path.trim()} onClick={() => void create()}>建立任務並預覽</Button></Group>
            : <>
              <Group gap="xs"><StatusBadge tone={TASK_STATUS[created.status].pill}>已建立 · {TASK_STATUS[created.status].cn}</StatusBadge><Text size="xs" c="dimmed">目標 {created.target.type} {created.target.table} · 事件 {created.events.join('／') || '—'}</Text></Group>
              {pv && <SimpleGrid cols={{ base: 2, sm: 5 }}>{([['樣本', pv.summary.in], ['通過', pv.summary.ok], ['隔離', pv.summary.quarantined], ['拒絕', pv.summary.rejected], ['個資命中', pv.pii_hits]] as [string, number][]).map(([k, v]) => <StatTile key={k} label={k} value={v.toLocaleString()} />)}</SimpleGrid>}
              {pv && pv.quarantined.length > 0 && <Text size="sm" c="dimmed">隔離原因（前 5）：{pv.quarantined.slice(0, 5).map((q) => q.reason).join('；')}</Text>}
              <Group gap="xs"><Button variant="filled" disabled={busy} onClick={() => void onDone()}>到同步任務列表（可立即執行）</Button></Group>
            </>}
        </Stack>}
        {!created && <Group gap="xs"><Button disabled={step === 1} onClick={() => setStep(step - 1)}>上一步</Button><Button variant="filled" disabled={step === 4 || (step === 1 && (!idOk || !table)) || (step === 3 && !sched)} onClick={() => setStep(step + 1)}>下一步</Button></Group>}
      </Stack>
    </Paper>
  );
}
