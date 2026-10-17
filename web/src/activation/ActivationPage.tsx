// 營運 › 推送（docs/14）：推送單（分群 × 目的地 × 模式 × 排程）／目的地（Webhook、secret_ref、測試事件、輪替、停用）／交付紀錄（批次、回應、重試、重放）。設計系統元件組合。
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Anchor, Badge, Button, Checkbox, Group, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, KeyValue, Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { DELIVERY_PILL, DELIVERY_STATUS_CN, DEST_STATUS_CN, EVENT_CN, MODE_CN, PUSH_STATUS_CN, PUSH_STATUS_PILL, SCHEDULE_CN, deliveryText, fmtTs, pct, pushSummary } from '@/tags/tags';
import type { Delivery, Destination, Push, Tag } from '@/tags/types';
import type { Segment } from '@/users/types';

export function ActivationPage() {
  return <RequireGrant module="segments"><ActivationInner /></RequireGrant>;
}

const opt = (value: string, label: string) => ({ value, label });
/** 表格內的等寬小字（ID、端點、時間）。 */
const Small = ({ children }: { children: ReactNode }) => <Text size="xs" c="dimmed" ff="monospace">{children}</Text>;

type Tab = 'pushes' | 'destinations' | 'deliveries';
function ActivationInner() {
  const { me, can } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get('tab') as Tab) || 'pushes';
  const setTab = (t: Tab) => setSp(t === 'pushes' ? {} : { tab: t });
  const [pushes, setPushes] = useState<Push[]>([]);
  const [dests, setDests] = useState<Destination[]>([]);
  const [dl, setDl] = useState<Delivery[]>([]);
  const [segs, setSegs] = useState<Segment[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newPush, setNewPush] = useState(false);
  const [newDest, setNewDest] = useState(false);
  const [openPush, setOpenPush] = useState<Push | null>(null);
  const reload = async () => { const [p, d, l] = await Promise.all([api.get<Push[]>('/v1/pushes'), api.get<Destination[]>('/v1/destinations'), api.get<Delivery[]>('/v1/deliveries')]); setPushes(p); setDests(d); setDl(l); };
  useEffect(() => {
    reload().catch((e: Error) => setErr(e.message));
    api.get<Segment[]>('/v1/segments').then(setSegs).catch(() => null);
    api.get<Tag[]>('/v1/tags').then(setTags).catch(() => null);
  }, []);
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(true); setErr(null); setMsg(null); try { const r = await fn(); await reload(); setMsg(typeof r === 'string' ? r : `${label}完成`); } catch (e) { setErr(e instanceof ApiError ? e.message : `${label}失敗`); } finally { setBusy(false); } };
  const destName = (id: string) => dests.find((d) => d.id === id)?.name ?? id;
  const approver = can('segments', 'approve'); const destAdmin = approver || can('integration', 'admin');
  const mine = (p: Push) => p.owner === me?.user.id || approver;
  const pendingDl = dl.some((d) => d.status === 'retrying' || d.status === 'dlq') ? ` (${dl.filter((d) => d.status !== 'ok').length})` : '';
  const headerAction = tab === 'pushes' && can('segments', 'use') ? <Button variant="filled" onClick={() => setNewPush(true)}>＋ 新建推送單</Button>
    : tab === 'destinations' && destAdmin ? <Button variant="filled" onClick={() => setNewDest(true)}>＋ 新增目的地</Button> : undefined;
  return (
    <div className="page">
      <PageHeader title="名單推送" lead="把分群成員（代碼化識別子 customer_code／one_id，可附帶允許的標籤）以簽章 Webhook 推到內部系統（MA、CRM）。增量＝與上次交付版本的差集；全量與目的地首次推送需審批；每批 ≤ 5,000 筆、失敗重試 1／5／15 分、1／6 小時後進 DLQ 可重放；連續 3 批失敗自動停用目的地。" actions={headerAction} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}{msg && <Text size="sm" c="dimmed" mb="sm">{msg}</Text>}
      <Stack gap="md" align="stretch">
        <SegmentedControl size="xs" value={tab} onChange={(v) => setTab(v as Tab)} data={[opt('pushes', '推送單'), opt('destinations', '目的地'), opt('deliveries', `交付紀錄${pendingDl}`)]} style={{ alignSelf: 'flex-start' }} />
        {newPush && <PushForm segs={segs.filter((s) => s.status === 'active')} dests={dests.filter((d) => d.status === 'active')} tags={tags.filter((t) => t.status === 'active')} onDone={async (p) => { setNewPush(false); await reload(); setMsg(p.status === 'pending_approval' ? '推送單已送審（全量或目的地首次推送需核可）' : '推送單已建立'); }} onCancel={() => setNewPush(false)} />}
        {newDest && <DestForm tags={tags} onDone={async () => { setNewDest(false); await reload(); setMsg('目的地已建立，先送測試事件確認接收端驗章'); }} onCancel={() => setNewDest(false)} />}
        {tab === 'pushes' && (
          <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={960}><Table>
            <Table.Thead><Table.Tr><Table.Th>分群</Table.Th><Table.Th>目的地</Table.Th><Table.Th>模式 / 排程</Table.Th><Table.Th>附帶標籤</Table.Th><Table.Th>狀態</Table.Th><Table.Th>最近</Table.Th><Table.Th>建立者</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{pushes.map((p) => (
              <Table.Tr key={p.id}>
                <Table.Td><Anchor component={Link} to={`/segments/${p.segment_id}`} size="sm" fw={700}>{p.segment_name}</Anchor><Small>{p.id}</Small></Table.Td>
                <Table.Td><Group gap={4}>{p.destination_ids.map((d) => <Badge key={d} color="gray">{destName(d)}</Badge>)}</Group></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{MODE_CN[p.mode].split('（')[0]} · {SCHEDULE_CN[p.schedule]}{p.schedule === 'cron' ? ` ${p.cron}` : ''}</Text></Table.Td>
                <Table.Td>{p.include_tags.length ? <Group gap={4}>{p.include_tags.map((t) => <Badge key={t} component={Link} to={`/tags/${t}`} color="gray" ff="monospace" style={{ cursor: 'pointer' }}>{t}</Badge>)}</Group> : <Text size="sm" c="dimmed">無</Text>}</Table.Td>
                <Table.Td><StatusBadge tone={PUSH_STATUS_PILL[p.status]}>{PUSH_STATUS_CN[p.status]}</StatusBadge></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{pushSummary(p)}</Text>{p.last_run_at ? <Small>{fmtTs(p.last_run_at)}</Small> : null}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{p.owner}</Text></Table.Td>
                <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>
                  <Group gap={4} justify="flex-end" wrap="nowrap">
                    <Button size="compact-sm" variant="subtle" onClick={() => void api.get<Push>(`/v1/pushes/${p.id}`).then(setOpenPush).catch((e: Error) => setErr(e.message))}>詳情</Button>
                    {mine(p) && p.status === 'active' && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void act('執行', async () => { const r = await api.post<Push['last_result']>(`/v1/pushes/${p.id}/run`); return r ? `已執行 v${r.version}：+${r.added} −${r.removed}，${r.batches} 批，失敗 ${r.failed}` : '已執行'; })}>立即執行</Button>}
                    {mine(p) && (p.status === 'active' || p.status === 'paused') && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void act(p.status === 'active' ? '暫停' : '恢復', () => api.post(`/v1/pushes/${p.id}/pause`))}>{p.status === 'active' ? '暫停' : '恢復'}</Button>}
                    {mine(p) && <Button size="compact-sm" variant="subtle" color="red" disabled={busy} onClick={() => { if (confirm('刪除推送單？已有交付的只改狀態、保留紀錄。')) void act('刪除', () => api.del(`/v1/pushes/${p.id}`)); }}>刪除</Button>}
                  </Group>
                </Table.Td>
              </Table.Tr>))}
              {!pushes.length && <Table.Tr><Table.Td colSpan={8}><EmptyState>還沒有推送單。到「分群」選一個生效中的分群，或按「新建推送單」。</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody></Table></Table.ScrollContainer></Paper>)}
        {tab === 'destinations' && (
          <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={1040}><Table>
            <Table.Thead><Table.Tr><Table.Th>目的地</Table.Th><Table.Th>端點</Table.Th><Table.Th>識別子</Table.Th><Table.Th>可附標籤</Table.Th><Table.Th ta="right">7 天成功率</Table.Th><Table.Th ta="right">P95</Table.Th><Table.Th>最近交付</Table.Th><Table.Th>狀態</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{dests.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td><Text size="sm" fw={700}>{d.name}</Text><Text size="xs" c="dimmed">{d.note}{d.first_push_at ? '' : ' · 尚未首次推送（需核可）'}</Text></Table.Td>
                <Table.Td><Text size="xs" ff="monospace">{d.url}</Text><Small>secret：{d.secret_ref}{d.rotated_at ? `（${fmtTs(d.rotated_at)} 輪替）` : ''}</Small></Table.Td>
                <Table.Td><Text size="xs" ff="monospace">{d.identifier}</Text></Table.Td>
                <Table.Td>{d.allowed_tags.length ? <Group gap={2}>{d.allowed_tags.map((t) => <Badge key={t} color="gray" size="xs" ff="monospace">{t}</Badge>)}</Group> : <Text size="sm" c="dimmed">無（匿名 ID）</Text>}</Table.Td>
                <Table.Td ta="right"><Mono>{pct(d.stats.ok_7d)}</Mono></Table.Td>
                <Table.Td ta="right"><Mono>{d.stats.p95_ms != null ? `${d.stats.p95_ms} ms` : '—'}</Mono></Table.Td>
                <Table.Td><Small>{fmtTs(d.stats.last_delivery_at)}</Small></Table.Td>
                <Table.Td><Group gap={4}><StatusBadge tone={d.status === 'active' ? 'blue' : 'gray'}>{DEST_STATUS_CN[d.status]}</StatusBadge>{d.consecutive_failures > 0 && <StatusBadge tone="amber">連續失敗 {d.consecutive_failures}</StatusBadge>}</Group></Table.Td>
                <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>{destAdmin && <Group gap={4} justify="flex-end" wrap="nowrap">
                  <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void act('測試', async () => { const r = await api.post<{ status: string; response: { status: number | null; ms: number; body: string } }>(`/v1/destinations/${d.id}/test`); return `audience.test → ${r.response.status ?? '連線失敗'} · ${r.response.ms} ms（${r.status === 'ok' ? '接收端驗章成功' : r.response.body.slice(0, 60)}）`; })}>送測試事件</Button>
                  <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => { const ref = prompt('新的 secret_ref（密鑰庫參照，例如 kv/data/dmp/prod/crm#webhook_secret_v2）'); if (ref) void act('輪替', () => api.post(`/v1/destinations/${d.id}/rotate-secret`, { secret_ref: ref })); }}>輪替 secret</Button>
                  <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void act(d.status === 'active' ? '停用' : '啟用', () => api.post(`/v1/destinations/${d.id}/pause`))}>{d.status === 'active' ? '停用' : '啟用'}</Button></Group>}</Table.Td>
              </Table.Tr>))}
              {!dests.length && <Table.Tr><Table.Td colSpan={9}><EmptyState>還沒有目的地。數位經營或資訊管理可新增（只存密鑰庫參照，不存明文）。</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody></Table></Table.ScrollContainer></Paper>)}
        {tab === 'deliveries' && (
          <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={900}><Table>
            <Table.Thead><Table.Tr><Table.Th>交付 ID</Table.Th><Table.Th>分群 / 版本</Table.Th><Table.Th>目的地</Table.Th><Table.Th>內容</Table.Th><Table.Th>狀態</Table.Th><Table.Th ta="right">嘗試</Table.Th><Table.Th>時間</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{dl.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td><Text size="xs" ff="monospace">{d.id}</Text>{d.replay_of && <Text size="xs" c="dimmed">重放</Text>}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{segs.find((s) => s.id === d.segment_id)?.name ?? d.segment_id}{d.mode !== 'test' ? ` v${d.version}` : ''}</Text></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{destName(d.destination_id)}</Text></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{deliveryText(d)}</Text></Table.Td>
                <Table.Td><StatusBadge tone={DELIVERY_PILL[d.status]}>{DELIVERY_STATUS_CN[d.status]}</StatusBadge></Table.Td>
                <Table.Td ta="right"><Mono>{d.attempts}</Mono></Table.Td>
                <Table.Td><Small>{fmtTs(d.updated_at)}</Small></Table.Td>
                <Table.Td ta="right">{approver && d.status !== 'ok' && <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => void act('重放', () => api.post(`/v1/deliveries/${d.id}/replay`))}>重放</Button>}</Table.Td>
              </Table.Tr>))}
              {!dl.length && <Table.Tr><Table.Td colSpan={8}><EmptyState>還沒有交付。</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody></Table></Table.ScrollContainer></Paper>)}
        {openPush && (
          <Paper>
            <SectionTitle title={`推送單 ${openPush.id}`} right={<Button size="compact-sm" variant="subtle" onClick={() => setOpenPush(null)}>關閉</Button>} />
            <KeyValue rows={[['分群', openPush.segment_name], ['目的地', openPush.destination_ids.map(destName).join('、')], ['模式／排程', `${MODE_CN[openPush.mode]} · ${SCHEDULE_CN[openPush.schedule]}${openPush.schedule === 'cron' && openPush.cron ? ` ${openPush.cron}` : ''}`], ['附帶標籤', openPush.include_tags.join('、') || '無'], ['最近交付版本', openPush.last_delivered_version ?? '—'], ['執行次數', openPush.runs]]} />
            <Table.ScrollContainer minWidth={640} mt="sm"><Table>
              <Table.Thead><Table.Tr><Table.Th>交付</Table.Th><Table.Th>事件</Table.Th><Table.Th>內容</Table.Th><Table.Th>狀態</Table.Th><Table.Th>時間</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{(openPush.deliveries ?? []).map((d) => <Table.Tr key={d.id}><Table.Td><Text size="xs" ff="monospace">{d.id}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed">{EVENT_CN[d.event] ?? d.event}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed">{deliveryText(d)}</Text></Table.Td><Table.Td><StatusBadge tone={DELIVERY_PILL[d.status]}>{DELIVERY_STATUS_CN[d.status]}</StatusBadge></Table.Td><Table.Td><Small>{fmtTs(d.updated_at)}</Small></Table.Td></Table.Tr>)}</Table.Tbody>
            </Table></Table.ScrollContainer>
          </Paper>)}
        <Text size="sm" c="dimmed">接收端要做的事：驗簽（HMAC-SHA256，X-DMP-Timestamp ±5 分鐘）→ 以 X-DMP-Delivery-Id 冪等 → 依 action 加入／移除名單 → 回 2xx。名單不含姓名、手機、Email 等任何 L3／L4 明文。Kafka／GCS 目的地與即時推送排第二階段。</Text>
      </Stack>
    </div>
  );
}

function PushForm({ segs, dests, tags, onDone, onCancel }: { segs: Segment[]; dests: Destination[]; tags: Tag[]; onDone: (p: Push) => Promise<void>; onCancel: () => void }) {
  const [seg, setSeg] = useState(segs[0]?.id ?? '');
  const [ids, setIds] = useState<string[]>([]);
  const [mode, setMode] = useState<'delta' | 'full'>('delta');
  const [schedule, setSchedule] = useState<'now' | 'after_batch' | 'cron'>('after_batch');
  const [cron, setCron] = useState('03:30');
  const [inc, setInc] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const allowed = tags.filter((t) => ids.length > 0 && ids.every((d) => dests.find((x) => x.id === d)?.allowed_tags.includes(t.id)));
  const chosen = segs.find((s) => s.id === seg);
  const save = async () => { setBusy(true); setErr(null); try { await onDone(await api.post<Push>(`/v1/audiences/${seg}/push`, { destination_ids: ids, mode, schedule, cron: schedule === 'cron' ? cron : '', include_tags: inc.filter((t) => allowed.some((a) => a.id === t)) })); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); } };
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title="新建推送單" />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NativeSelect label="分群（生效中）" aria-label="推送分群" value={seg} data={segs.map((s) => opt(s.id, `${s.name}${s.latest?.users != null ? `（${s.latest.users.toLocaleString()} 人）` : '（尚未計算）'}`))} onChange={(e) => setSeg(e.target.value)} />
          <NativeSelect label="模式" aria-label="推送模式" value={mode} data={[opt('delta', '增量（加入／移除）'), opt('full', '全量（需審批）')]} onChange={(e) => setMode(e.target.value as 'delta')} />
          <Group gap="xs" align="flex-end" wrap="nowrap">
            <NativeSelect label="排程" aria-label="推送排程" value={schedule} data={[opt('after_batch', '日批完成後（分群有新版本即推）'), opt('now', '立即'), opt('cron', '每日定時')]} onChange={(e) => setSchedule(e.target.value as 'now')} style={{ flex: 1 }} />
            {schedule === 'cron' && <TextInput type="time" aria-label="每日定時時間" value={cron} onChange={(e) => setCron(e.target.value)} w={120} />}
          </Group>
        </SimpleGrid>
        <Stack gap={4}>
          <Text size="xs" c="dimmed" fw={700}>目的地</Text>
          <Group gap="md">
            {dests.map((d) => <Checkbox key={d.id} aria-label={`目的地 ${d.name}`} checked={ids.includes(d.id)} onChange={(e) => setIds(e.target.checked ? [...ids, d.id] : ids.filter((x) => x !== d.id))} label={<>{d.name}<Text component="span" display="block" size="xs" c="dimmed">{d.identifier}{d.first_push_at ? '' : ' · 首次推送需核可'}</Text></>} />)}
            {!dests.length && <Text size="sm" c="dimmed">沒有啟用中的目的地</Text>}
          </Group>
        </Stack>
        <Stack gap={4}>
          <Text size="xs" c="dimmed" fw={700}>附帶標籤（須在每個目的地的可附標籤內；數值型區間化）</Text>
          <Group gap="md">
            {allowed.map((t) => <Checkbox key={t.id} checked={inc.includes(t.id)} onChange={(e) => setInc(e.target.checked ? [...inc, t.id] : inc.filter((x) => x !== t.id))} label={<>{t.name}<Text component="span" display="block" size="xs" c="dimmed" ff="monospace">{t.id}</Text></>} />)}
            {!allowed.length && <Text size="sm" c="dimmed">選目的地後列出可附帶的標籤</Text>}
          </Group>
        </Stack>
        {chosen && !chosen.latest?.users && <Text size="sm" c="dimmed">此分群尚未計算，推送前請先到分群頁「立即計算」。</Text>}
        <Group gap="xs"><Button variant="filled" disabled={!seg || !ids.length || busy} onClick={() => void save()}>{busy ? '建立中…' : mode === 'full' || ids.some((d) => !dests.find((x) => x.id === d)?.first_push_at) ? '送審並建立' : '建立'}</Button><Button variant="subtle" onClick={onCancel}>取消</Button>{err && <Alert color="red" variant="light" py={6}>{err}</Alert>}</Group>
      </Stack>
    </Paper>
  );
}

function DestForm({ tags, onDone, onCancel }: { tags: Tag[]; onDone: () => Promise<void>; onCancel: () => void }) {
  const [d, setD] = useState({ name: '', url: 'https://', secret_ref: 'kv/data/dmp/prod/', identifier: 'customer_code' as 'customer_code' | 'one_id', allowed_tags: [] as string[], note: '' });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => { setBusy(true); setErr(null); try { await api.post('/v1/destinations', { ...d, type: 'webhook' }); await onDone(); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); } };
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title="新增目的地（Webhook）" />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="名稱" aria-label="目的地名稱" value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} />
          <TextInput label="端點 URL（https）" aria-label="目的地 URL" ff="monospace" value={d.url} onChange={(e) => setD({ ...d, url: e.target.value })} />
          <TextInput label="簽章密鑰 secret_ref" aria-label="secret_ref" ff="monospace" value={d.secret_ref} placeholder="kv/data/dmp/prod/crm#webhook_secret" onChange={(e) => setD({ ...d, secret_ref: e.target.value })} />
          <NativeSelect label="識別子" value={d.identifier} data={[opt('customer_code', 'customer_code（代碼化）'), opt('one_id', 'one_id')]} onChange={(e) => setD({ ...d, identifier: e.target.value as 'customer_code' })} />
          <TextInput label="說明" value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} />
        </SimpleGrid>
        <Stack gap={4}>
          <Text size="xs" c="dimmed" fw={700}>可附帶標籤</Text>
          <Group gap="md">{tags.filter((t) => t.status === 'active').map((t) => <Checkbox key={t.id} label={t.name} checked={d.allowed_tags.includes(t.id)} onChange={(e) => setD({ ...d, allowed_tags: e.target.checked ? [...d.allowed_tags, t.id] : d.allowed_tags.filter((x) => x !== t.id) })} />)}</Group>
        </Stack>
        <Text size="sm" c="dimmed">密鑰只存密鑰庫參照（OpenBao／GCP Secret Manager／K8s Secret），不接受明文；新目的地或換了端點／密鑰後，第一次推送需核可。</Text>
        <Group gap="xs"><Button variant="filled" disabled={!d.name.trim() || busy} onClick={() => void save()}>{busy ? '建立中…' : '建立'}</Button><Button variant="subtle" onClick={onCancel}>取消</Button>{err && <Alert color="red" variant="light" py={6}>{err}</Alert>}</Group>
      </Stack>
    </Paper>
  );
}
