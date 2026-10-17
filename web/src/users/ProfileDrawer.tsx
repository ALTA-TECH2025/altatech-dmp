// 用戶細查抽屜：先填理由（4–200 字，寫入審計）→ 畫像（屬性脫敏、近 30 天摘要、分群）→ 行為序列（倒序分頁、事件篩選）→ 身分圖（節點／邊）。設計系統元件組合（Drawer、Table、Timeline）。
import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Drawer, Group, Loader, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Timeline, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { EmptyState, Mono, StatTile, StatusBadge } from '@/ds';
import type { IdentityGraph, Profile, Reveal, TimelineResult } from './types';

type Tab = 'profile' | 'timeline' | 'identities';
const TAB_CN: Record<Tab, string> = { profile: '畫像', timeline: '行為序列', identities: '身分圖' };

export function ProfileDrawer({ oneId, cat, project, onClose }: { oneId: string; cat: Catalog | null; project?: string; onClose: () => void }) {
  const [reason, setReason] = useState('');
  const [granted, setGranted] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('profile');
  const [prof, setProf] = useState<Profile | null>(null);
  const [tl, setTl] = useState<TimelineResult | null>(null);
  const [tlCursors, setTlCursors] = useState<string[]>([]);
  const [tlEvent, setTlEvent] = useState('');
  const [ig, setIg] = useState<IdentityGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState<{ field: string; reason: string; hours: number } | null>(null);
  const [revealMsg, setRevealMsg] = useState<string | null>(null);
  const requestReveal = async () => {
    if (!reveal) return;
    setBusy(true); setErr(null);
    try { const r = await api.post<Reveal>('/v1/reveals', { field: reveal.field, one_id: oneId, reason: reveal.reason.trim(), hours: reveal.hours }); setRevealMsg(`已送出揭露申請 ${r.id}（${r.level}，${r.level === 'L3' ? '系統管理員' : '數位經營'}審批）；核可後 ${r.hours} 小時內本畫像該欄位顯示明文。`); setReveal(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '申請失敗'); } finally { setBusy(false); }
  };
  const qs = (extra: Record<string, string | number | undefined> = {}) => { const p = new URLSearchParams({ reason: granted ?? '', ...(project ? { project } : {}) }); for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== '') p.set(k, String(v)); return p.toString(); };
  const loadTl = async (cursor?: string, ev = tlEvent) => {
    setBusy(true);
    try { setTl(await api.get<TimelineResult>(`/v1/profiles/${encodeURIComponent(oneId)}/timeline?${qs({ limit: 50, cursor, events: ev })}`)); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } finally { setBusy(false); }
  };
  useEffect(() => {
    if (!granted) return;
    setBusy(true); setErr(null);
    api.get<Profile>(`/v1/profiles/${encodeURIComponent(oneId)}?${qs()}`).then(setProf).catch((e: Error) => setErr(e.message)).finally(() => setBusy(false));
  }, [granted, oneId]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!granted) return;
    if (tab === 'timeline' && !tl) void loadTl();
    if (tab === 'identities' && !ig) api.get<IdentityGraph>(`/v1/profiles/${encodeURIComponent(oneId)}/identities?${qs()}`).then(setIg).catch((e: Error) => setErr(e.message));
  }, [tab, granted]);   // eslint-disable-line react-hooks/exhaustive-deps
  const propCn = (k: string) => cat?.user_props.find((u) => u.key === k)?.cn ?? k;
  const val = (v: unknown) => (v === null || v === undefined ? '—' : typeof v === 'boolean' ? (v ? '是' : '否') : String(v));
  const tlRows = tl?.rows ?? [];
  return (
    <Drawer.Root opened onClose={onClose} position="right" size={720}>
      <Drawer.Overlay />
      <Drawer.Content aria-label="用戶細查">
        <Drawer.Header>
          <Group gap="xs"><Title order={3}>用戶細查</Title><Text size="xs" c="dimmed" ff="monospace">{prof?.one_id ?? oneId}</Text></Group>
          <Button size="compact-sm" variant="subtle" onClick={onClose}>關閉</Button>
        </Drawer.Header>
        <Drawer.Body>
          {!granted ? (
            <Stack gap="sm">
              <Text size="sm" c="dimmed">細查單一客戶需填寫理由（客訴單號、調查目的等）；理由與你的帳號會寫入審計，one_id 只記雜湊。</Text>
              <TextInput label="理由" aria-label="細查理由" value={reason} placeholder="例：客訴單 C-1001 查詢交易序列" onChange={(e) => setReason(e.target.value)} />
              <Group gap="xs"><Button variant="filled" disabled={reason.trim().length < 4 || reason.length > 200} onClick={() => setGranted(reason.trim())}>進入細查</Button></Group>
            </Stack>
          ) : (
            <Stack gap="sm">
              <SegmentedControl size="xs" value={tab} onChange={(v) => setTab(v as Tab)} data={(['profile', 'timeline', 'identities'] as Tab[]).map((t) => ({ value: t, label: TAB_CN[t] }))} style={{ alignSelf: 'flex-start' }} />
              {err && <Alert color="red" variant="light">{err}</Alert>}
              {busy && !prof && <Group justify="center" py="xl" gap="xs"><Loader size="sm" /><Text size="sm" c="dimmed">讀取中…</Text></Group>}
              {tab === 'profile' && prof && (
                <Stack gap="sm">
                  <SimpleGrid cols={2}>
                    <StatTile label={`近 ${prof.summary.days} 天事件`} value={prof.summary.total.toLocaleString()} />
                    <StatTile label="分群" value={<Text fw={700} size="sm">{prof.segments.length ? prof.segments.join('、') : '—'}</Text>} />
                  </SimpleGrid>
                  {Object.keys(prof.masked).length > 0 && (reveal ? (
                    <Paper shadow="none" radius="md" bg="var(--soft)" p="sm">
                      <Group gap="xs">
                        <NativeSelect aria-label="揭露欄位" value={reveal.field} data={Object.keys(prof.masked).map((f) => ({ value: f, label: `${f === 'one_id' ? 'one_id' : propCn(f)}（${prof.masked[f]}）` }))} onChange={(e) => setReveal({ ...reveal, field: e.target.value })} />
                        <TextInput aria-label="揭露理由" placeholder="理由（必填，4–200 字）" value={reveal.reason} onChange={(e) => setReveal({ ...reveal, reason: e.target.value })} style={{ flex: 1, minWidth: 160 }} />
                        <NativeSelect aria-label="揭露時效" value={String(reveal.hours)} data={[1, 4, 24].map((h) => ({ value: String(h), label: `${h} 小時` }))} onChange={(e) => setReveal({ ...reveal, hours: Number(e.target.value) })} />
                        <Button variant="filled" disabled={busy || reveal.reason.trim().length < 4} onClick={() => void requestReveal()}>送審</Button>
                        <Button variant="subtle" onClick={() => setReveal(null)}>取消</Button>
                      </Group>
                    </Paper>
                  ) : <Group gap="xs"><Text size="xs" c="dimmed">遮罩欄位：{Object.keys(prof.masked).join('、')}（只對這位用戶、限時）</Text><Button size="compact-sm" variant="subtle" onClick={() => setReveal({ field: Object.keys(prof.masked)[0], reason: '', hours: 24 })}>申請揭露</Button></Group>)}
                  {revealMsg && <Text size="sm" c="dimmed">{revealMsg}</Text>}
                  <Table.ScrollContainer minWidth={320}><Table>
                    <Table.Thead><Table.Tr><Table.Th>屬性</Table.Th><Table.Th>值</Table.Th></Table.Tr></Table.Thead>
                    <Table.Tbody>{Object.entries(prof.props).map(([k, v]) => <Table.Tr key={k}><Table.Td>{propCn(k)}{prof.masked[k] && <Text size="xs" c="dimmed">遮罩</Text>}</Table.Td><Table.Td><Mono>{val(v)}</Mono></Table.Td></Table.Tr>)}</Table.Tbody>
                  </Table></Table.ScrollContainer>
                  <Table.ScrollContainer minWidth={480}><Table>
                    <Table.Thead><Table.Tr><Table.Th>事件</Table.Th><Table.Th ta="right">次數</Table.Th><Table.Th>首次</Table.Th><Table.Th>最近</Table.Th></Table.Tr></Table.Thead>
                    <Table.Tbody>
                      {prof.summary.events.map((e) => <Table.Tr key={e.event}><Table.Td>{e.cn}<Text size="xs" c="dimmed" ff="monospace">{e.event}</Text></Table.Td><Table.Td ta="right"><Mono>{e.n}</Mono></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{e.first}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{e.last}</Text></Table.Td></Table.Tr>)}
                      {!prof.summary.events.length && <Table.Tr><Table.Td colSpan={4}><EmptyState>期間內沒有事件</EmptyState></Table.Td></Table.Tr>}
                    </Table.Tbody>
                  </Table></Table.ScrollContainer>
                  {prof.warnings.length > 0 && <Text size="sm" c="dimmed">{prof.warnings.join('；')}</Text>}
                </Stack>
              )}
              {tab === 'timeline' && (
                <Stack gap="sm">
                  <Group gap="xs" justify="space-between" wrap="wrap">
                    <Group gap="xs">
                      <NativeSelect aria-label="序列事件篩選" value={tlEvent} data={[{ value: '', label: '全部事件' }, ...(cat?.events ?? []).map((ev) => ({ value: ev.name, label: ev.cn }))]} onChange={(e) => { setTlEvent(e.target.value); setTlCursors([]); void loadTl(undefined, e.target.value); }} />
                      <Text size="xs" c="dimmed">近 90 天 · 時間為 UTC{tl?.masked.amount ? ' · 金額已區間化' : ''}</Text>
                    </Group>
                    <Group gap="xs"><Button size="compact-sm" variant="subtle" disabled={!tlCursors.length || busy} onClick={() => { const s = tlCursors.slice(0, -1); setTlCursors(s); void loadTl(s[s.length - 1]); }}>上一頁</Button><Button size="compact-sm" variant="subtle" disabled={!tl?.next_cursor || busy} onClick={() => { if (tl?.next_cursor) { setTlCursors([...tlCursors, tl.next_cursor]); void loadTl(tl.next_cursor); } }}>下一頁</Button></Group>
                  </Group>
                  {tlRows.length > 0 && (
                    <Timeline active={tlRows.length} bulletSize={12} lineWidth={2}>
                      {tlRows.map((r) => (
                        <Timeline.Item key={r.event_id} title={<Group gap="xs"><Text size="xs" c="dimmed" ff="monospace">{r.time}</Text><Text size="sm" fw={700}>{r.cn}</Text><Text size="xs" c="dimmed" ff="monospace">{r.event}</Text></Group>}>
                          <Text size="xs" c="dimmed">{[r.platform, r.channel, r.symbol, r.side, r.amount !== null && r.amount !== undefined ? `金額 ${r.amount}` : '', r.entity_id ? `ID ${r.entity_id}` : ''].filter(Boolean).join(' · ')}{Object.keys(r.props).length ? ` · ${Object.entries(r.props).map(([k, v]) => `${k}=${String(v)}`).join(' ')}` : ''}</Text>
                        </Timeline.Item>))}
                    </Timeline>)}
                  {tl && !tl.rows.length && <EmptyState>沒有事件</EmptyState>}
                </Stack>
              )}
              {tab === 'identities' && ig && (
                <Stack gap="sm">
                  <Group gap="xs">{ig.nodes.map((n) => <Badge key={n.id} color="gray"><Text component="span" size="xs" c="dimmed">{n.type}</Text> {n.value}</Badge>)}{!ig.nodes.length && <Text size="xs" c="dimmed">沒有身分邊</Text>}</Group>
                  <Table.ScrollContainer minWidth={720}><Table>
                    <Table.Thead><Table.Tr><Table.Th>A</Table.Th><Table.Th>B</Table.Th><Table.Th>來源</Table.Th><Table.Th>證據</Table.Th><Table.Th ta="right">次數</Table.Th><Table.Th ta="right">信心</Table.Th><Table.Th>狀態</Table.Th><Table.Th>首次／末次</Table.Th></Table.Tr></Table.Thead>
                    <Table.Tbody>{ig.edges.map((e, i) => <Table.Tr key={i}><Table.Td><Mono>{e.a}</Mono></Table.Td><Table.Td><Mono>{e.b}</Mono></Table.Td><Table.Td>{e.source}</Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{e.evidence_event_id}</Text></Table.Td><Table.Td ta="right"><Mono>{e.seen_count}</Mono></Table.Td><Table.Td ta="right"><Mono>{e.confidence}</Mono></Table.Td><Table.Td><StatusBadge tone={e.status === 'active' ? 'blue' : e.status === 'conflict' ? 'red' : 'gray'}>{e.status}</StatusBadge></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{e.first_seen}<br />{e.last_seen}</Text></Table.Td></Table.Tr>)}</Table.Tbody>
                  </Table></Table.ScrollContainer>
                  {ig.masked.id && <Text size="sm" c="dimmed">ID 值依你的角色遮罩（{ig.masked.id}）；揭露需走審批。</Text>}
                </Stack>
              )}
            </Stack>
          )}
        </Drawer.Body>
      </Drawer.Content>
    </Drawer.Root>
  );
}
