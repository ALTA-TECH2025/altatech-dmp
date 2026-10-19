// 舊平台分群／標籤遷移：掃描 users 表 user_group_*／user_tag_* → 每項快照（固定名單）、連到重建的 DMP 分群／標籤（比對人數）或附原因略過。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Anchor, Button, Group, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { EmptyState, Mono, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { diffOk, diffText, GROUP_STATUS } from './migration';
import type { LegacyGroup, LegacyGroupList, LegacyGroupStatus } from './types';

type Pick = { id: string; name: string };
const opt = (value: string, label: string) => ({ value, label });

export function GroupsTab({ canUse, canSnapshot }: { canUse: boolean; canSnapshot: boolean }) {
  const [data, setData] = useState<LegacyGroupList | null>(null);
  const [filter, setFilter] = useState<'all' | LegacyGroupStatus>('all');
  const [kind, setKind] = useState<'all' | 'segment' | 'tag'>('all');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState<{ name: string; mode: 'link' | 'skip' | 'snapshot'; type: 'segment' | 'tag'; id: string; note: string } | null>(null);
  const [segs, setSegs] = useState<Pick[]>([]); const [tags, setTags] = useState<Pick[]>([]);
  const load = async () => { try { setData(await api.get<LegacyGroupList>('/v1/migration/legacy-groups')); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => {
    void load();
    api.get<Pick[]>('/v1/segments').then((x) => setSegs(x.map((s) => ({ id: s.id, name: s.name })))).catch(() => undefined);
    api.get<Pick[]>('/v1/tags').then((x) => setTags(x.map((t) => ({ id: t.id, name: t.name })))).catch(() => undefined);
  }, []);
  const run = async (key: string, fn: () => Promise<unknown>) => { setBusy(key); setErr(null); try { await fn(); setForm(null); await load(); } catch (e) { setErr(e instanceof ApiError ? e.message : '操作失敗'); } finally { setBusy(null); } };
  const scan = () => run('scan', () => api.post('/v1/migration/legacy-groups/scan', {}));
  const submit = (g: LegacyGroup) => {
    if (!form) return;
    const u = `/v1/migration/legacy-groups/${encodeURIComponent(g.name)}`;
    if (form.mode === 'snapshot') void run(g.name, () => api.post(`${u}/snapshot`, { dmp_name: form.note.trim() || undefined }));
    else if (form.mode === 'link') void run(g.name, () => api.post(`${u}/link`, { type: form.type, id: form.id }));
    else void run(g.name, () => api.post(`${u}/skip`, { note: form.note }));
  };
  const rows = (data?.items ?? []).filter((g) => (filter === 'all' || g.status === filter) && (kind === 'all' || g.kind === kind));
  const s = data?.summary;
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <SimpleGrid cols={{ base: 2, md: 5 }}>
        <StatTile label="舊平台分群＋標籤" value={s ? s.total.toLocaleString() : '—'} hint={data?.scanned_at ? `掃描於 ${data.scanned_at.slice(0, 16).replace('T', ' ')}` : '尚未掃描'} />
        <StatTile label="待處理" value={s ? s.pending.toLocaleString() : '—'} />
        <StatTile label="快照" value={s ? s.snapshot.toLocaleString() : '—'} hint="固定名單，不再更新" />
        <StatTile label="已重建" value={s ? s.rebuilt.toLocaleString() : '—'} hint="以規則重建、例行計算" />
        <StatTile label="略過" value={s ? s.skipped.toLocaleString() : '—'} />
      </SimpleGrid>
      <Group gap="xs" wrap="wrap">
        <SegmentedControl size="xs" value={filter} onChange={(v) => setFilter(v as typeof filter)} data={[opt('all', '全部'), ...(['pending', 'snapshot', 'rebuilt', 'skipped'] as const).map((k) => opt(k, GROUP_STATUS[k].cn))]} />
        <SegmentedControl size="xs" value={kind} onChange={(v) => setKind(v as typeof kind)} data={[opt('all', '分群＋標籤'), opt('segment', '分群'), opt('tag', '標籤')]} />
        {canUse && <Button ml="auto" loading={busy === 'scan'} disabled={!!busy || data?.legacy_configured === false} title={data?.legacy_configured === false ? '執行節點需設定 LEGACY_BASE_URL／LEGACY_PROJECT／LEGACY_API_SECRET' : ''} onClick={() => void scan()}>以舊平台 API 掃描</Button>}
      </Group>
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1040}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>舊平台分群／標籤</Table.Th><Table.Th>類型</Table.Th><Table.Th ta="right">舊平台人數</Table.Th><Table.Th>狀態</Table.Th><Table.Th>DMP 對應</Table.Th><Table.Th ta="right">DMP 人數</Table.Th><Table.Th ta="right">差異</Table.Th><Table.Th>處理</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{rows.map((g) => { const ok = diffOk(g.dmp?.diff_pct); const editing = form?.name === g.name; return (
              <Table.Tr key={g.name}>
                <Table.Td><Text fw={600} size="sm">{g.cn}</Text><Text size="xs" c="dimmed" ff="monospace">{g.name}</Text>{g.note && <Text size="xs" c="dimmed">{g.note}</Text>}</Table.Td>
                <Table.Td><Text size="sm">{g.kind === 'segment' ? '分群' : `標籤${g.values != null ? `（${g.values} 值）` : ''}`}</Text></Table.Td>
                <Table.Td ta="right"><Mono>{g.users.toLocaleString()}</Mono></Table.Td>
                <Table.Td><StatusBadge tone={GROUP_STATUS[g.status].pill}>{GROUP_STATUS[g.status].cn}</StatusBadge></Table.Td>
                <Table.Td>{g.dmp ? (g.dmp.missing ? <Text size="xs" c="red">已刪除：{g.dmp.id}</Text> : <Anchor component={Link} to={`/${g.dmp.type === 'segment' ? 'segments' : 'tags'}/${g.dmp.id}`} size="sm">{g.dmp.name}</Anchor>) : <Text size="xs" c="dimmed">—</Text>}</Table.Td>
                <Table.Td ta="right"><Mono>{g.dmp?.users == null ? '—' : g.dmp.users.toLocaleString()}</Mono></Table.Td>
                <Table.Td ta="right">{ok === null ? <Text size="xs" c="dimmed">—</Text> : <StatusBadge tone={ok ? 'blue' : 'amber'}>{diffText(g.dmp?.diff_pct)}</StatusBadge>}</Table.Td>
                <Table.Td>
                  {canUse && !editing && <Group gap={4} wrap="nowrap">
                    {(g.status === 'pending' || g.status === 'skipped') && canSnapshot && <Button size="compact-xs" variant="light" disabled={!!busy} onClick={() => setForm({ name: g.name, mode: 'snapshot', type: g.kind, id: '', note: `舊平台：${g.cn}` })}>快照</Button>}
                    <Button size="compact-xs" variant="light" disabled={!!busy} onClick={() => setForm({ name: g.name, mode: 'link', type: g.kind, id: '', note: '' })}>{g.status === 'rebuilt' ? '改連' : '連到重建'}</Button>
                    {g.status === 'pending' && <Button size="compact-xs" variant="subtle" color="gray" disabled={!!busy} onClick={() => setForm({ name: g.name, mode: 'skip', type: g.kind, id: '', note: '' })}>略過</Button>}
                  </Group>}
                  {editing && form && <Stack gap={4} miw={280}>
                    {form.mode === 'snapshot' && <TextInput size="xs" aria-label="DMP 名稱" label="建立的 DMP 名稱" value={form.note} maxLength={60} onChange={(e) => setForm({ ...form, note: e.target.value })} />}
                    {form.mode === 'link' && <Group gap={4} wrap="nowrap">
                      <NativeSelect size="xs" aria-label="對應類型" value={form.type} data={[opt('segment', '分群'), opt('tag', '標籤')]} onChange={(e) => setForm({ ...form, type: e.target.value as 'segment' | 'tag', id: '' })} />
                      <NativeSelect size="xs" aria-label="對應項目" style={{ flex: 1 }} value={form.id} data={[opt('', '選擇…'), ...(form.type === 'segment' ? segs : tags).map((x) => opt(x.id, x.name))]} onChange={(e) => setForm({ ...form, id: e.target.value })} />
                    </Group>}
                    {form.mode === 'skip' && <TextInput size="xs" aria-label="略過原因" placeholder="原因（例：活動已結束）" value={form.note} maxLength={200} onChange={(e) => setForm({ ...form, note: e.target.value })} />}
                    <Group gap={4}>
                      <Button size="compact-xs" variant="filled" loading={busy === g.name} disabled={(form.mode === 'link' && !form.id) || (form.mode === 'skip' && !form.note.trim())} onClick={() => submit(g)}>{form.mode === 'snapshot' ? '建立快照' : form.mode === 'link' ? '連結' : '略過'}</Button>
                      <Button size="compact-xs" variant="subtle" onClick={() => setForm(null)}>取消</Button>
                    </Group>
                  </Stack>}
                </Table.Td>
              </Table.Tr>); })}
              {!rows.length && <Table.Tr><Table.Td colSpan={8}><EmptyState>{data?.scanned_at ? '沒有符合的項目' : '尚未掃描舊平台分群與標籤'}</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Paper><SectionTitle title="怎麼選" />
        <SimpleGrid cols={{ base: 1, md: 3 }}>
          <Text size="sm"><b>連到重建</b>：還在用、規則清楚的，用規則建構器在 DMP 重建，之後例行計算；連結後比對人數，差異超過 5% 要查口徑。</Text>
          <Text size="sm"><b>快照</b>：規則重建不了（例如人工上傳的名單、已失效的事件）或只是留紀錄的，取當下成員做成固定名單；登入 ID 用同一把金鑰轉 one_id，沒對到用戶表的人會少算。</Text>
          <Text size="sm"><b>略過</b>：已停用、沒人用的，附原因留紀錄，切換後不再追。</Text>
        </SimpleGrid></Paper>
    </Stack>
  );
}
