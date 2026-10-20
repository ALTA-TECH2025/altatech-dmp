// 數據整合 › 數據輸出（docs/06 §8）：任務列表（名稱、類型、輸出內容、目的地、狀態、上次執行）＋ 右側抽屜表單三段（基本資訊／連接配置／數據管理）＋ 預覽（欄位遮罩、估算列數、樣本）＋ 執行紀錄。
// 目的地沿用「名單推送 › 目的地」（sftp／s3 在那裡新增）；建立即送審（數位經營），核可後本人才能執行；改內容或目的地重走審批。
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Anchor, Badge, Button, Checkbox, Drawer, Group, NativeSelect, NumberInput, Paper, SimpleGrid, Stack, Table, Text, TextInput, Textarea, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { filterFieldsOf } from '@/analytics/EventsPage';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { DateRangeInput } from '@/ds/DateRange';
import { EmptyState, Mono, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { fmtTs } from '@/tags/tags';
import type { Destination } from '@/tags/types';
import { GroupEditor } from '@/users/RuleBuilder';
import type { CondGroup, Segment } from '@/users/types';
import { contentText, DEST_TYPE_CN, defaultTask, destAllowed, EVENT_COLUMNS, fileExample, fmtBytes, groupEvents, KIND_CN, needsReapproval, POLICY_CN, RUN_CN, runText, STATUS, TRIGGER_CN, TYPE_CN, validateTask, type OutputKind, type OutputPreview, type OutputRun, type OutputTask, type OutputTaskCreate, type OutputType } from './output';

const opt = (value: string, label: string) => ({ value, label });
const errText = (e: unknown, fb: string) => (e instanceof ApiError ? e.message : fb);
const ROLES = [opt('data_analyst', '數據分析'), opt('digital_ops', '數位經營'), opt('marketing', '行銷'), opt('it', '資訊管理')];
const asGroup = (f: unknown): CondGroup => (Array.isArray(f) ? { op: 'and', items: f as CondGroup['items'] } : f && typeof f === 'object' && 'items' in (f as object) ? (f as CondGroup) : { op: 'and', items: [] });

export function OutputTab() {
  const { me, can } = useAuth();
  const [tasks, setTasks] = useState<OutputTask[]>([]);
  const [dests, setDests] = useState<Destination[]>([]);
  const [segs, setSegs] = useState<Segment[]>([]);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ cur: OutputTask | null } | null>(null);
  const [runs, setRuns] = useState<{ t: OutputTask; rows: OutputRun[] } | null>(null);
  const canCreate = can('events', 'export') || can('users', 'export');
  const reload = async () => { setTasks(await api.get<OutputTask[]>('/v1/exports/tasks')); };
  useEffect(() => {
    reload().catch((e: Error) => setErr(e.message));
    api.get<Destination[]>('/v1/destinations').then(setDests).catch(() => null);
    api.get<Segment[]>('/v1/segments').then(setSegs).catch(() => null);
    api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch(() => null);
  }, []);
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(true); setErr(null); setMsg(null); try { const r = await fn(); await reload(); setMsg(typeof r === 'string' ? r : `${label}完成`); } catch (e) { setErr(errText(e, `${label}失敗`)); } finally { setBusy(false); } };
  const dest = (id: string) => dests.find((d) => d.id === id);
  const segName = (id: string) => segs.find((s) => s.id === id)?.name ?? id;
  const mine = (t: OutputTask) => t.owner === me?.user.id || can('iam', 'admin');
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">把事件明細、用戶寬表或分群名單一次性／每日 T+1 送到 SFTP 或物件儲存（實時走 Kafka／Webhook）。欄位比畫面嚴一級：L3／L4 不可輸出、L2 數值區間化；每列帶浮水印、每次附 manifest；建立即送數位經營審批。</Text>
        {canCreate && <Button variant="filled" ml="auto" onClick={() => setForm({ cur: null })}>＋ 新建任務</Button>}
      </Group>
      {err && <Alert color="red" variant="light">{err}</Alert>}{msg && <Text size="sm" c="dimmed">{msg}</Text>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1100}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>任務</Table.Th><Table.Th>類型</Table.Th><Table.Th>輸出內容</Table.Th><Table.Th>目的地 / 格式</Table.Th><Table.Th>狀態</Table.Th><Table.Th>上次執行</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{tasks.map((t) => {
              const d = dest(t.destination_id);
              return (
                <Table.Tr key={t.id} bg={t.last_run?.status === 'failed' ? 'var(--mantine-color-orange-light)' : undefined}>
                  <Table.Td><Text fw={600} size="sm">{t.name}</Text><Text size="xs" c="dimmed" ff="monospace">{t.id} · {t.owner}</Text></Table.Td>
                  <Table.Td><Badge color="gray">{TYPE_CN[t.type]}</Badge>{t.type === 'daily' && <Text size="xs" c="dimmed">每天 {t.schedule?.at ?? '02:00'}{t.schedule?.end_date ? ` 至 ${t.schedule.end_date}` : ''}</Text>}</Table.Td>
                  <Table.Td><Text size="sm">{contentText(t.content, segName)}</Text>{Object.keys(t.masked ?? {}).length > 0 && <Text size="xs" c="dimmed">遮罩：{Object.entries(t.masked).map(([k, v]) => `${k} ${POLICY_CN[v] ?? v}`).join('、')}</Text>}</Table.Td>
                  <Table.Td><Text size="sm">{d?.name ?? t.destination_id}</Text><Text size="xs" c="dimmed" ff="monospace">{d ? DEST_TYPE_CN[d.type] : ''} · {t.format.type}{t.format.compression !== 'none' ? `.${t.format.compression === 'gzip' ? 'gz' : 'zst'}` : ''}{t.format.partition === 'day' ? ' · 按日分檔' : ''}</Text></Table.Td>
                  <Table.Td><StatusBadge tone={STATUS[t.status].pill}>{STATUS[t.status].cn}</StatusBadge>{t.status === 'pending_approval' && t.approval_id && <Text size="xs" c="dimmed"><Anchor component={Link} to="/approvals" size="xs">審批單</Anchor>{t.destination_approval_id && (t.pending_approvals ?? []).includes(t.destination_approval_id) ? '（待系統管理員核可目的地）' : ''}</Text>}</Table.Td>
                  <Table.Td><Text size="xs" c="dimmed">{runText(t.last_run)}</Text></Table.Td>
                  <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}><Group gap={4} justify="flex-end" wrap="nowrap">
                    <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void api.get<OutputRun[]>(`/v1/exports/tasks/${t.id}/runs?limit=20`).then((rows) => setRuns({ t, rows })).catch((e: Error) => setErr(e.message))}>紀錄</Button>
                    {mine(t) && (t.status === 'approved' || t.status === 'active') && t.type !== 'realtime' && <Button size="compact-sm" variant="filled" disabled={busy} onClick={() => void act('執行', async () => { const r = await api.post<OutputRun>(`/v1/exports/tasks/${t.id}/run`, {}); return r.status === 'ok' ? `已執行：${(r.rows ?? 0).toLocaleString()} 列、${r.files.length} 個檔案（含 manifest）` : `執行${RUN_CN[r.status].cn}${r.error ? `：${r.error}` : ''}`; })}>立即執行</Button>}
                    {mine(t) && (t.status === 'active' || t.status === 'paused') && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void act(t.status === 'active' ? '暫停' : '恢復', () => api.post(`/v1/exports/tasks/${t.id}/pause`, {}))}>{t.status === 'active' ? '暫停' : '恢復'}</Button>}
                    {mine(t) && t.status !== 'deleted' && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => setForm({ cur: t })}>編輯</Button>}
                    {mine(t) && <Button size="compact-sm" variant="subtle" color="red" disabled={busy} onClick={() => { if (confirm('刪除任務？已有執行紀錄的只改狀態、保留紀錄。')) void act('刪除', () => api.del(`/v1/exports/tasks/${t.id}`)); }}>刪除</Button>}
                  </Group></Table.Td>
                </Table.Tr>);
            })}
              {!tasks.length && <Table.Tr><Table.Td colSpan={7}><EmptyState>還沒有輸出任務。按「新建任務」：選輸出內容與目的地，預覽欄位遮罩後送審。</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {runs && <RunsPanel t={runs.t} rows={runs.rows} onClose={() => setRuns(null)} />}
      {form && <TaskDrawer cur={form.cur} dests={dests} segs={segs} cat={cat} onClose={() => setForm(null)} onDone={async (t) => { setForm(null); await reload(); setMsg(t.status === 'pending_approval' ? '任務已送審（數位經營核可後才能執行）' : '任務已更新'); }} />}
    </Stack>
  );
}

function RunsPanel({ t, rows, onClose }: { t: OutputTask; rows: OutputRun[]; onClose: () => void }) {
  return (
    <Paper p={0} style={{ overflow: 'hidden' }}>
      <Group p="sm" pb={0} justify="space-between"><SectionTitle title={`執行紀錄 · ${t.name}`} right={<Button size="compact-sm" variant="subtle" onClick={onClose}>關閉</Button>} /></Group>
      <Table.ScrollContainer minWidth={1000}>
        <Table>
          <Table.Thead><Table.Tr><Table.Th>開始</Table.Th><Table.Th>分區</Table.Th><Table.Th>觸發</Table.Th><Table.Th>狀態</Table.Th><Table.Th ta="right">列數</Table.Th><Table.Th ta="right">大小</Table.Th><Table.Th>檔案（sha256）</Table.Th><Table.Th>浮水印</Table.Th><Table.Th>錯誤</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{rows.map((r) => <Table.Tr key={r.id}>
            <Table.Td><Mono>{fmtTs(r.started_at)}</Mono><Text size="xs" c="dimmed">第 {r.attempts} 次</Text></Table.Td><Table.Td><Mono>{r.partition_date ?? '整段'}</Mono></Table.Td><Table.Td>{TRIGGER_CN[r.trigger]}</Table.Td>
            <Table.Td><StatusBadge tone={RUN_CN[r.status].pill}>{RUN_CN[r.status].cn}</StatusBadge></Table.Td>
            <Table.Td ta="right"><Mono>{r.rows?.toLocaleString() ?? '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{fmtBytes(r.bytes)}</Mono></Table.Td>
            <Table.Td>{r.files.map((f) => <Text key={f.path} size="xs" ff="monospace">{f.path.split('/').slice(-1)[0]} <Text component="span" c="dimmed">{f.checksum.slice(0, 12)}…</Text></Text>)}{!r.files.length && <Text size="xs" c="dimmed">—</Text>}</Table.Td>
            <Table.Td><Text size="xs" ff="monospace" c="dimmed">{r.watermark ?? '—'}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed">{r.error ?? ''}</Text></Table.Td>
          </Table.Tr>)}
            {!rows.length && <Table.Tr><Table.Td colSpan={9}><EmptyState>沒有執行紀錄</EmptyState></Table.Td></Table.Tr>}</Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

/* ------------------------------------------------------------------ 抽屜表單：基本資訊 → 連接配置 → 數據管理 → 預覽 → 送審 */
function TaskDrawer({ cur, dests, segs, cat, onClose, onDone }: { cur: OutputTask | null; dests: Destination[]; segs: Segment[]; cat: Catalog | null; onClose: () => void; onDone: (t: OutputTask) => Promise<void> }) {
  const base = useMemo<OutputTaskCreate>(() => (cur ? { name: cur.name, type: cur.type, content: cur.content, destination_id: cur.destination_id, format: cur.format, schedule: cur.schedule ?? { at: '02:00' }, purpose: cur.purpose, retention_days: cur.retention_days, shared_roles: cur.shared_roles ?? [] } : defaultTask()), [cur]);
  const [t, setT] = useState<OutputTaskCreate>(base);
  const [pv, setPv] = useState<OutputPreview | null>(null);
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const [propKey, setPropKey] = useState('');
  const dest = dests.find((d) => d.id === t.destination_id);
  const errs = validateTask(t, dest);
  const okDests = dests.filter((d) => destAllowed(t.type, d));
  const c = t.content; const setC = (p: Partial<OutputTaskCreate['content']>) => { setT({ ...t, content: { ...c, ...p } }); setPv(null); };
  const setF = (p: Partial<OutputTaskCreate['format']>) => setT({ ...t, format: { ...t.format, ...p } });
  const setKind = (kind: OutputKind) => setC({ kind, columns: kind === 'events' ? defaultTask().content.columns : [], events: [], segment_id: undefined, filters: [] });
  const setType = (type: OutputType) => { setT({ ...t, type, destination_id: dest && destAllowed(type, dest) ? t.destination_id : '', format: type === 'realtime' ? { ...t.format, type: 'jsonl', compression: 'none', partition: 'none' } : t.format, content: type === 'realtime' ? { ...c, kind: 'events' } : c }); setPv(null); };
  const groups = groupEvents(cat?.events ?? []);
  const evSet = new Set(c.events ?? []);
  const toggleEvents = (names: string[], on: boolean) => setC({ events: on ? [...new Set([...(c.events ?? []), ...names])] : (c.events ?? []).filter((e) => !names.includes(e)) });
  const cols = c.columns ?? [];
  const toggleCol = (name: string, on: boolean) => setC({ columns: on ? [...cols, name] : cols.filter((x) => x !== name) });
  const userCols = (cat?.user_props ?? []).map((u) => ({ name: u.key, cn: u.cn }));
  const preview = async () => { setBusy(true); setErr(null); try { setPv(await api.post<OutputPreview>('/v1/exports/tasks/preview', t)); } catch (e) { setErr(errText(e, '預覽失敗')); } finally { setBusy(false); } };
  const save = async () => {
    setBusy(true); setErr(null);
    try { const r = cur ? await api.put<OutputTask>(`/v1/exports/tasks/${cur.id}`, t) : await api.post<OutputTask>('/v1/exports/tasks', t); await onDone(r); }
    catch (e) { setErr(errText(e, '建立失敗')); } finally { setBusy(false); }
  };
  const reapprove = cur ? needsReapproval(base, t) : false;
  return (
    <Drawer.Root opened onClose={onClose} position="right" size={820}>
      <Drawer.Overlay />
      <Drawer.Content>
        <Drawer.Header><Drawer.Title><Title order={4}>{cur ? `編輯任務 · ${cur.name}` : '新建輸出任務'}</Title></Drawer.Title><Drawer.CloseButton /></Drawer.Header>
        <Drawer.Body>
          <Stack gap="lg">
            <Stack gap="sm">
              <SectionTitle title="1. 基本資訊" />
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput label="任務名稱" aria-label="任務名稱" maxLength={50} value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} />
                <NativeSelect label="任務類型" aria-label="任務類型" value={t.type} data={[opt('once', '一次性（整段期間）'), opt('daily', '每日 T+1（每天跑前一天分區）'), opt('realtime', '實時（單條 jsonl → Kafka／Webhook）')]} onChange={(e) => setType(e.target.value as OutputType)} />
                <TextInput label="用途（進審批單與浮水印）" aria-label="用途" placeholder="例：交付數據中台對帳（保存 90 天）" maxLength={200} value={t.purpose} onChange={(e) => setT({ ...t, purpose: e.target.value })} />
                <NumberInput label="接收方保存天數" aria-label="保存天數" min={1} max={365} value={t.retention_days} onChange={(v) => setT({ ...t, retention_days: Number(v) || 0 })} />
              </SimpleGrid>
              <Group gap="md"><Text size="xs" c="dimmed" fw={700}>分享給角色</Text>{ROLES.map((r) => <Checkbox key={r.value} size="xs" label={r.label} checked={(t.shared_roles ?? []).includes(r.value)} onChange={(e) => setT({ ...t, shared_roles: e.target.checked ? [...(t.shared_roles ?? []), r.value] : (t.shared_roles ?? []).filter((x) => x !== r.value) })} />)}</Group>
            </Stack>

            <Stack gap="sm">
              <SectionTitle title="2. 連接配置" right={<Anchor component={Link} to="/activation?tab=destinations" size="xs">管理目的地（SFTP／S3 在名單推送 › 目的地新增）</Anchor>} />
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <NativeSelect label="目的地" aria-label="目的地" value={t.destination_id} data={[opt('', okDests.length ? '選目的地…' : (t.type === 'realtime' ? '沒有啟用中的 Kafka／Webhook 目的地' : '沒有啟用中的 SFTP／物件儲存目的地')), ...okDests.map((d) => opt(d.id, `${d.name}（${DEST_TYPE_CN[d.type]}${d.first_push_at ? '' : '，首次使用需系統管理員核可'}）`))]} onChange={(e) => { setT({ ...t, destination_id: e.target.value }); setPv(null); }} />
                {dest && <Text size="xs" c="dimmed" mt={26}>{dest.type === 'sftp' ? `${String(dest.config?.host ?? '')}:${String(dest.config?.port ?? 22)} ${String(dest.config?.path ?? '')}` : dest.type === 's3' ? `${String(dest.config?.endpoint ?? '')} / ${String(dest.config?.bucket ?? '')}/${String(dest.config?.prefix ?? '')}` : dest.url} · 密鑰 {dest.secret_ref}</Text>}
              </SimpleGrid>
              {t.type === 'once' && <DateRangeInput label="期間（事件依 event_date；用戶依 updated_at）" ariaLabel="輸出期間" value={{ from: c.time?.from ?? '', to: c.time?.to ?? '' }} onChange={(r) => setC({ time: { from: r.from, to: r.to } })} fullWidth={false} />}
              {t.type === 'daily' && <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <TextInput type="time" label="每天幾點（台北）" aria-label="每天幾點" value={t.schedule?.at ?? '02:00'} onChange={(e) => setT({ ...t, schedule: { ...t.schedule, at: e.target.value } })} />
                <TextInput type="date" label="結束日（含；可留空）" aria-label="結束日" value={t.schedule?.end_date ?? ''} onChange={(e) => setT({ ...t, schedule: { ...t.schedule, end_date: e.target.value || undefined } })} />
              </SimpleGrid>}
              {t.type !== 'realtime' && <>
                <SimpleGrid cols={{ base: 2, sm: 4 }}>
                  <NativeSelect label="檔案格式" aria-label="檔案格式" value={t.format.type} data={[opt('csv', 'CSV'), opt('jsonl', 'JSON Lines'), opt('parquet', 'Parquet（執行節點）')]} onChange={(e) => setF({ type: e.target.value as 'csv' })} />
                  <NativeSelect label="壓縮" aria-label="壓縮" value={t.format.compression} data={[opt('gzip', 'gzip'), opt('none', '不壓縮'), opt('zstd', 'zstd（執行節點）')]} onChange={(e) => setF({ compression: e.target.value as 'gzip' })} />
                  <NativeSelect label="分檔" aria-label="分檔" value={t.format.partition} data={[opt('day', '按日'), opt('none', '不分')]} onChange={(e) => setF({ partition: e.target.value as 'day' })} />
                  <NumberInput label="每檔最多列數" aria-label="每檔最多列數" min={10_000} max={5_000_000} step={100_000} thousandSeparator value={t.format.max_rows_per_file} onChange={(v) => setF({ max_rows_per_file: Number(v) || 0 })} />
                </SimpleGrid>
                <TextInput label="遠端檔名樣板（變數 {task}／{date}／{seq}／{ext}）" aria-label="檔名樣板" ff="monospace" value={t.format.file_pattern} onChange={(e) => setF({ file_pattern: e.target.value })} description={`示例：${fileExample(t.format)}`} />
                <Group gap="md">
                  {t.format.type === 'csv' && <Checkbox size="xs" label="CSV 表頭" checked={t.format.header} onChange={(e) => setF({ header: e.target.checked })} />}
                  <Checkbox size="xs" label="附 manifest.json（檔案、列數、sha256、浮水印、申請單）" checked={t.format.manifest} onChange={(e) => setF({ manifest: e.target.checked })} />
                </Group>
              </>}
            </Stack>

            <Stack gap="sm">
              <SectionTitle title="3. 數據管理" />
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <NativeSelect label="輸出內容" aria-label="輸出內容" value={c.kind} data={(['events', 'users', 'segment_members'] as OutputKind[]).map((k) => opt(k, KIND_CN[k]))} disabled={t.type === 'realtime'} onChange={(e) => setKind(e.target.value as OutputKind)} />
                <NativeSelect label="用戶識別子" aria-label="用戶識別子" value={c.identifier ?? 'one_id'} data={[opt('customer_code', 'customer_code（代碼化，建議）'), opt('one_id', 'one_id（需輸出策略為明文的身分）')]} onChange={(e) => setC({ identifier: e.target.value as 'one_id' })} />
              </SimpleGrid>
              {c.kind === 'events' && <>
                <Stack gap={4}>
                  <Group gap="xs"><Text size="xs" c="dimmed" fw={700}>事件清單（空＝全部 live 事件）· 已選 {evSet.size}</Text>{evSet.size > 0 && <Button size="compact-xs" variant="subtle" onClick={() => setC({ events: [] })}>清空</Button>}</Group>
                  <Stack gap={2} style={{ maxHeight: 220, overflow: 'auto' }}>
                    {groups.map((g) => { const all = g.items.every((e) => evSet.has(e.name)); return (
                      <Group key={g.group} gap={6} wrap="wrap" align="flex-start">
                        <Checkbox size="xs" fw={700} aria-label={`批量 ${g.group}`} label={<Text size="xs" fw={700}>{g.group}（{g.items.length}）</Text>} checked={all} indeterminate={!all && g.items.some((e) => evSet.has(e.name))} onChange={(e) => toggleEvents(g.items.map((x) => x.name), e.target.checked)} />
                        {g.items.map((e) => <Checkbox key={e.name} size="xs" aria-label={`事件 ${e.name}`} label={<Text size="xs">{e.cn} <Text component="span" c="dimmed" ff="monospace">{e.name}</Text></Text>} checked={evSet.has(e.name)} onChange={(ev) => toggleEvents([e.name], ev.target.checked)} />)}
                      </Group>); })}
                    {!groups.length && <Text size="xs" c="dimmed">事件字典讀取中…</Text>}
                  </Stack>
                </Stack>
                <Stack gap={4}>
                  <Text size="xs" c="dimmed" fw={700}>欄位 · 已選 {cols.length}（L3／L4 不可輸出；金額等 L2 數值區間化）</Text>
                  <Group gap="xs" wrap="wrap">{EVENT_COLUMNS.map((col) => <Checkbox key={col.name} size="xs" aria-label={`欄位 ${col.name}`} label={<Text size="xs">{col.cn}{col.level ? <Badge size="xs" color="orange" ml={4}>{col.level}</Badge> : null}</Text>} checked={cols.includes(col.name)} onChange={(e) => toggleCol(col.name, e.target.checked)} />)}</Group>
                  <Group gap="xs" wrap="wrap">{cols.filter((x) => x.startsWith('props.')).map((x) => <Badge key={x} color="gray" ff="monospace" rightSection={<span role="button" tabIndex={0} aria-label={`移除 ${x}`} style={{ cursor: 'pointer' }} onClick={() => toggleCol(x, false)}>×</span>}>{x}</Badge>)}
                    <TextInput size="xs" w={220} aria-label="加入事件屬性欄位" placeholder="props.<key>，Enter 加入" value={propKey} onChange={(e) => setPropKey(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && propKey.trim()) { const k = propKey.trim().startsWith('props.') ? propKey.trim() : `props.${propKey.trim()}`; if (!cols.includes(k)) toggleCol(k, true); setPropKey(''); } }} /></Group>
                </Stack>
                <GroupEditor g={asGroup(c.filters)} fields={filterFieldsOf(cat)} cat={cat} label="篩選" onChange={(g) => setC({ filters: g.items.length ? g : [] })} />
              </>}
              {c.kind === 'users' && <>
                <Stack gap={4}>
                  <Text size="xs" c="dimmed" fw={700}>用戶欄位 · 已選 {cols.length}（one_id／帳號等 L3 不在清單；資產等 L2 區間化）</Text>
                  <Group gap="xs" wrap="wrap">{userCols.map((col) => <Checkbox key={col.name} size="xs" aria-label={`欄位 ${col.name}`} label={<Text size="xs">{col.cn} <Text component="span" c="dimmed" ff="monospace">{col.name}</Text></Text>} checked={cols.includes(col.name)} onChange={(e) => toggleCol(col.name, e.target.checked)} />)}</Group>
                </Stack>
                <GroupEditor g={asGroup(c.filters)} fields={filterFieldsOf(cat).filter((f) => f.v.startsWith('user.'))} cat={cat} label="用戶條件" onChange={(g) => setC({ filters: g.items.length ? g : [] })} />
              </>}
              {c.kind === 'segment_members' && <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <NativeSelect label="分群（生效中；輸出最新版本成員）" aria-label="輸出分群" value={c.segment_id ?? ''} data={[opt('', '選分群…'), ...segs.filter((s) => s.status === 'active').map((s) => opt(s.id, `${s.name}${s.latest?.users != null ? `（${s.latest.users.toLocaleString()} 人）` : ''}`))]} onChange={(e) => setC({ segment_id: e.target.value || undefined })} />
                <Text size="xs" c="dimmed" mt={26}>名單只含識別子與選的欄位；附帶標籤請走「名單推送」。</Text>
              </SimpleGrid>}
            </Stack>

            {pv && <Paper withBorder p="sm">
              <SectionTitle title="預覽（依你的權限遮罩；不落地、不送目的地）" right={<Button size="compact-sm" variant="subtle" onClick={() => setPv(null)}>關閉</Button>} />
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                <StatTile label={pv.partition_date ? `估算列數（${pv.partition_date} 分區）` : '估算列數'} value={pv.rows_estimate.toLocaleString()} />
                <StatTile label="輸出欄位" value={pv.columns.filter((x) => x.policy !== 'rejected').length} hint={pv.rejected.length ? `拒絕 ${pv.rejected.length}` : undefined} />
                <StatTile label="區間化／遮罩" value={pv.columns.filter((x) => x.policy !== 'plain' && x.policy !== 'rejected').length} />
                <StatTile label="樣本" value={pv.sample.length} />
              </SimpleGrid>
              <Group gap={4} mt="sm" wrap="wrap">{pv.columns.map((col) => <Badge key={col.name} color={col.policy === 'rejected' ? 'red' : col.policy === 'plain' ? 'gray' : 'orange'} variant="light" ff="monospace">{col.name}{col.cn && col.cn !== col.name ? ` ${col.cn}` : ''} · {POLICY_CN[col.policy] ?? col.policy}</Badge>)}</Group>
              {pv.warnings.map((w) => <Alert key={w} color="orange" variant="light" mt="sm" py={6}>{w}</Alert>)}
              {pv.sample.length > 0 && <Table.ScrollContainer minWidth={600}><Table mt="sm" fz="xs"><Table.Thead><Table.Tr>{Object.keys(pv.sample[0]).map((k) => <Table.Th key={k}><Mono>{k}</Mono></Table.Th>)}</Table.Tr></Table.Thead>
                <Table.Tbody>{pv.sample.slice(0, 8).map((r, i) => <Table.Tr key={i}>{Object.keys(pv.sample[0]).map((k) => <Table.Td key={k}><Text size="xs" ff="monospace">{r[k] == null ? '' : String(r[k]).slice(0, 40)}</Text></Table.Td>)}</Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>}
            </Paper>}
            {err && <Alert color="red" variant="light">{err}</Alert>}
            {errs.length > 0 && <Text size="xs" c="dimmed">尚缺：{errs.join('；')}</Text>}
            {reapprove && <Alert color="orange" variant="light" py={6}>改了輸出內容、目的地或類型：儲存後視同新任務，重走審批。</Alert>}
            <Group gap="xs">
              <Button variant="default" disabled={busy || errs.length > 0} onClick={() => void preview()}>{busy ? '處理中…' : '預覽'}</Button>
              <Button variant="filled" disabled={busy || errs.length > 0} onClick={() => void save()}>{cur ? (reapprove ? '儲存並送審' : '儲存') : '送審並建立'}</Button>
              <Button variant="subtle" onClick={onClose}>取消</Button>
            </Group>
            <Textarea readOnly autosize minRows={2} maxRows={6} ff="monospace" fz="xs" label="送出的任務（JSON）" value={JSON.stringify(t, null, 1)} />
          </Stack>
        </Drawer.Body>
      </Drawer.Content>
    </Drawer.Root>
  );
}
