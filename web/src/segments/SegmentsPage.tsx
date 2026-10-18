// 分群：列表（搜尋、狀態、回收站）／新建與編輯（規則 = 用戶列表同一套條件、更新方式）／詳情（狀態與審批、最新版本、歷史版本、成員、預覽畫像、血緣、動作）。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Alert, Anchor, Badge, Button, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { Chart, hbar } from '@/ds/Chart';
import { EmptyState, KeyValue, Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { ExportDialog } from '@/users/ExportDialog';
import { ProfileDrawer } from '@/users/ProfileDrawer';
import { RuleBuilder } from '@/users/RuleBuilder';
import { DesktopOnly } from '@/ds/DesktopOnly';
import { cleanRule, emptyRule, normalizeRule, ruleValid, summarizeRule } from '@/users/rule';
import type { Segment, SegmentCreate, SegmentPreview, SegmentStatus, SegmentVersion, UsersEstimate, UsersListResult } from '@/users/types';
import { UsersTable } from '@/users/UsersTable';
import { ImportForm } from './ImportForm';
import { ACTION_CN, KIND_CN, STATUS_CN, STATUS_PILL, TRIGGER_CN, actionsFor, updateText, type SegAction } from './segments';

export function SegmentsPage() {
  return <RequireGrant module="segments"><SegmentsInner /></RequireGrant>;
}

type Draft = SegmentCreate & { update: NonNullable<SegmentCreate['update']> };
const newDraft = (): Draft => ({ name: '', description: '', kind: 'rule', rule: emptyRule(), update: { mode: 'routine', freq: 'daily', earliest: '02:00', keep: 7 }, tz: 'Asia/Taipei' });   // 動態分群：預設每日凌晨 02:00 重算
const n = (x: number | null | undefined) => (x == null ? '—' : x.toLocaleString());

function SegmentsInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const { id } = useParams();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [list, setList] = useState<Segment[]>([]);
  const [tab, setTab] = useState<'all' | 'pending' | 'recycled'>('all');
  const [search, setSearch] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    const live = await api.get<Segment[]>('/v1/segments'); const rec = await api.get<Segment[]>('/v1/segments?status=recycled');
    setList([...live, ...rec]);
  };
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); reload().catch((e: Error) => setErr(e.message)); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const shown = list.filter((s) => (tab === 'recycled' ? s.status === 'recycled' : tab === 'pending' ? s.status === 'pending_approval' : s.status !== 'recycled') && (!search || s.name.includes(search) || (s.description ?? '').includes(search)));
  const segOpts = list.filter((s) => s.status !== 'recycled').map((s) => ({ id: s.id, name: s.name }));
  const current = id && id !== 'new' && id !== 'import' ? list.find((s) => s.id === id) ?? null : null;
  const pending = list.filter((s) => s.status === 'pending_approval').length;
  return (
    <div className="page">
      <PageHeader title="分群" lead="動態分群：規則 = 用戶屬性／所屬分群 × 行為（事件、事件屬性、指標比較、近 N 天）× 且／或分組，以 Bitmap 計算存成版本，預設每日凌晨 02:00 重算最新符合資格的受眾；行銷建立的分群需數位經營審批後生效；分析落點也可存成分群。"
        actions={can('segments', 'use') && <Group gap="xs"><Button component={Link} to="/segments/import" variant="light">導入名單</Button><Button component={Link} to="/segments/new" variant="filled">＋ 新建分群</Button></Group>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {id === 'import' ? <DesktopOnly title="導入名單" inline reason="上傳名單檔案與確認比對結果請用電腦操作。"><ImportForm onDone={async (s) => { await reload(); nav(`/segments/${s.id}`); }} onCancel={() => nav('/segments')} /></DesktopOnly>
        : id === 'new' ? <SegmentForm cat={cat} segOpts={segOpts} initial={newDraft()} onDone={async (s) => { await reload(); nav(`/segments/${s.id}`); }} onCancel={() => nav('/segments')} />
        : current ? <SegmentDetail seg={current} cat={cat} segOpts={segOpts} me={me?.user.id} can={can} onChanged={reload} busy={busy} setBusy={setBusy} setErr={setErr} />
        : (
          <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" className="ds-split">
            <Paper p={0} style={{ overflow: 'hidden' }}>
              <Group p="sm" gap="sm">
                <SegmentedControl size="xs" value={tab} onChange={(v) => setTab(v as typeof tab)} data={[{ value: 'all', label: '全部' }, { value: 'pending', label: pending ? `待審批 (${pending})` : '待審批' }, { value: 'recycled', label: '回收站' }]} />
                <TextInput aria-label="搜尋分群" placeholder="搜尋名稱／說明" value={search} onChange={(e) => setSearch(e.target.value)} style={{ flex: 1, maxWidth: 320 }} />
              </Group>
              <Table.ScrollContainer minWidth={760}>
                <Table>
                  <Table.Thead><Table.Tr><Table.Th>分群</Table.Th><Table.Th>狀態</Table.Th><Table.Th ta="right">人數</Table.Th><Table.Th>更新</Table.Th><Table.Th>最近計算</Table.Th><Table.Th>建立者</Table.Th></Table.Tr></Table.Thead>
                  <Table.Tbody>{shown.map((s) => (
                    <Table.Tr key={s.id} style={{ cursor: 'pointer' }} onClick={() => nav(`/segments/${s.id}`)}>
                      <Table.Td><Text fw={600} size="sm">{s.name}</Text><Text size="xs" c="dimmed" lineClamp={1}>{s.kind !== 'rule' ? `${KIND_CN[s.kind]} · ` : ''}{summarizeRule(s.rule, cat, segOpts)}</Text></Table.Td>
                      <Table.Td><Group gap={4}><StatusBadge tone={STATUS_PILL[s.status]}>{STATUS_CN[s.status]}</StatusBadge>{s.latest?.stale && <StatusBadge tone="amber">規則已改</StatusBadge>}</Group></Table.Td>
                      <Table.Td ta="right"><Mono>{n(s.latest?.users)}</Mono></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed">{updateText(s.update, s.tz)}</Text></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed" ff="monospace">{s.latest ? `${s.latest.basetime} v${s.latest.version}${s.latest.status === 'failed' ? ' 失敗' : ''}` : '尚未計算'}</Text></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed">{s.owner}</Text></Table.Td>
                    </Table.Tr>))}
                    {!shown.length && <Table.Tr><Table.Td colSpan={6}><EmptyState>沒有分群</EmptyState></Table.Td></Table.Tr>}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
            <Paper><SectionTitle title="說明" />
              <List size="sm" spacing="xs" c="dimmed">
                <List.Item>規則與用戶列表同一套條件；「完成 A 未完成 B」= 做過 A 且 未做過 B；「完成 A 後 N 天內做 B」用行為序列。</List.Item>
                <List.Item>導入名單：上傳客戶代碼或 one_id 清單，先看對到幾人再建分群；固定名單只算一次。</List.Item>
                <List.Item>人數預估是精確的 Bitmap 基數；計算會把名單寫成版本（手動 1 版、例行留 N 版），查詢端引用分群時取最新版本。</List.Item>
                <List.Item>結果保存分群（從漏斗流失、留存某期等落點建立）不可改規則；手動分群不可改更新方式；例行分群不可改頻率。</List.Item>
                <List.Item>回收站保留 15 天後徹底刪除；有下游引用時需確認。所有動作寫審計。</List.Item>
              </List></Paper>
          </SimpleGrid>
        )}
    </div>
  );
}

function SegmentForm(props: Parameters<typeof SegmentFormInner>[0]) {
  return <DesktopOnly title="新建／編輯分群" inline reason="規則建構器有多層條件與欄位，手機上不好設定。請用電腦建立或編輯；手機可以查看清單與詳情。"><SegmentFormInner {...props} /></DesktopOnly>;
}

function SegmentFormInner({ cat, segOpts, initial, existing, onDone, onCancel }: { cat: Catalog | null; segOpts: { id: string; name: string }[]; initial: Draft; existing?: Segment; onDone: (s: Segment) => Promise<void> | void; onCancel: () => void }) {
  const [d, setD] = useState<Draft>(initial);
  const [est, setEst] = useState<UsersEstimate | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const clean = (): SegmentCreate => ({ ...d, rule: cleanRule(d.rule), update: { ...d.update, keep: d.update.mode === 'manual' ? 1 : d.update.keep } });
  const estimate = async () => { setBusy(true); setErr(null); try { setEst(await api.post<UsersEstimate>('/v1/segments/estimate', { rule: clean().rule })); } catch (e) { setErr(e instanceof ApiError ? e.message : '估數失敗'); } finally { setBusy(false); } };
  const save = async () => {
    setBusy(true); setErr(null);
    try { const s = existing ? await api.put<Segment>(`/v1/segments/${existing.id}`, clean()) : await api.post<Segment>('/v1/segments', clean()); await onDone(s); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '儲存失敗'); } finally { setBusy(false); }
  };
  const ro = existing?.kind === 'result' || existing?.kind === 'import'; const manual = d.update.mode === 'manual';
  const upd = (p: Partial<Draft['update']>) => setD({ ...d, update: { ...d.update, ...p } });
  return (
    <Paper maw={980}>
      <Stack gap="md">
        <SectionTitle title={existing ? '編輯分群' : '新建分群'} />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label="名稱" aria-label="分群名稱" value={d.name} maxLength={60} required onChange={(e) => setD({ ...d, name: e.target.value })} />
          <TextInput label="說明" value={d.description ?? ''} onChange={(e) => setD({ ...d, description: e.target.value })} />
        </SimpleGrid>
        <Paper shadow="none" radius="md"><RuleBuilder q={d.rule} cat={cat} segments={segOpts.filter((s) => s.id !== existing?.id)} readOnly={ro} onChange={(r) => { setD({ ...d, rule: r }); setEst(null); }} /></Paper>
        <SimpleGrid cols={{ base: 2, sm: 5 }}>
          <NativeSelect label="更新方式" aria-label="更新方式" value={d.update.mode} disabled={!!existing} data={[{ value: 'manual', label: '手動（1 版）' }, { value: 'routine', label: '例行' }]} onChange={(e) => upd({ mode: e.target.value as 'manual' | 'routine' })} />
          <NativeSelect label="頻率" aria-label="頻率" value={d.update.freq} disabled={manual || !!existing} data={[{ value: 'daily', label: '每天' }, { value: 'weekly', label: '每週一' }, { value: 'monthly', label: '每月 1 日' }]} onChange={(e) => upd({ freq: e.target.value as 'daily' })} />
          <TextInput label="最早計算時間" aria-label="最早計算時間" type="time" value={d.update.earliest} disabled={manual} onChange={(e) => upd({ earliest: e.target.value })} />
          <TextInput label="保留版本數" type="number" min={1} max={100} value={manual ? 1 : d.update.keep} disabled={manual} onChange={(e) => upd({ keep: Number(e.target.value) || 1 })} />
          <TextInput label="時區" value={d.tz ?? 'Asia/Taipei'} onChange={(e) => setD({ ...d, tz: e.target.value })} />
        </SimpleGrid>
        <Group gap="xs">
          <Button disabled={!ruleValid(d.rule) || busy} onClick={() => void estimate()}>估人數{est ? `：${est.users.toLocaleString()}` : ''}</Button>
          <Button variant="filled" disabled={!d.name.trim() || !ruleValid(d.rule) || busy} loading={busy} onClick={() => void save()}>{existing ? '儲存' : '建立'}</Button>
          <Button variant="subtle" onClick={onCancel}>取消</Button>
          {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
        </Group>
        <Text size="sm" c="dimmed">{summarizeRule(d.rule, cat, segOpts)}</Text>
      </Stack>
    </Paper>
  );
}

function SegmentDetail({ seg, cat, segOpts, me, can, onChanged, busy, setBusy, setErr }: { seg: Segment; cat: Catalog | null; segOpts: { id: string; name: string }[]; me?: string; can: Parameters<typeof actionsFor>[1]; onChanged: () => Promise<void>; busy: boolean; setBusy: (b: boolean) => void; setErr: (e: string | null) => void }) {
  const nav = useNavigate();
  const [editing, setEditing] = useState(false);
  const [versions, setVersions] = useState<SegmentVersion[]>([]);
  const [preview, setPreview] = useState<SegmentPreview | null>(null);
  const [members, setMembers] = useState<UsersListResult | null>(null);
  const [cursors, setCursors] = useState<string[]>([]);
  const [lineage, setLineage] = useState<{ up: string[]; down: string[] } | null>(null);
  const [note, setNote] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  useEffect(() => { setEditing(false); setPreview(null); setMembers(null); setCursors([]); api.get<SegmentVersion[]>(`/v1/segments/${seg.id}/versions`).then(setVersions).catch(() => setVersions([])); api.get<{ up: string[]; down: string[] }>(`/v1/segments/${seg.id}/lineage`).then(setLineage).catch(() => undefined); }, [seg.id, seg.updated_at]);
  const act = async (a: SegAction, body?: unknown) => {
    setBusy(true); setErr(null);
    try {
      if (a === 'edit') { setEditing(true); return; }
      if (a === 'purge') { if (!window.confirm('徹底刪除後不可恢復，成員名單一併清除。確定？')) return; await api.del(`/v1/segments/${seg.id}/purge`); nav('/segments'); await onChanged(); return; }
      if (a === 'recycle') { try { await api.post(`/v1/segments/${seg.id}/recycle`); } catch (e) { if (e instanceof ApiError && e.status === 409 && window.confirm(`${e.message}\n仍要移入回收站？`)) await api.post(`/v1/segments/${seg.id}/recycle?force=true`); else throw e; } }
      else if (a === 'copy') { const s = await api.post<Segment>(`/v1/segments/${seg.id}/copy`, {}); await onChanged(); nav(`/segments/${s.id}`); return; }
      else await api.post(`/v1/segments/${seg.id}/${a === 'resume' ? 'pause' : a}`, body ?? {});
      await onChanged();
    } catch (e) { setErr(e instanceof ApiError ? e.message : '操作失敗'); } finally { setBusy(false); }
  };
  const loadMembers = async (cursor?: string) => { setBusy(true); try { setMembers(await api.get<UsersListResult>(`/v1/segments/${seg.id}/versions/latest/members?page_size=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } finally { setBusy(false); } };
  const loadPreview = async () => { setBusy(true); try { setPreview(await api.post<SegmentPreview>(`/v1/segments/${seg.id}/preview`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '預覽失敗'); } finally { setBusy(false); } };
  const actions = actionsFor(seg, can, me);
  const colCn = (c: string) => (c === 'one_id' ? 'one_id' : cat?.user_props.find((u) => u.key === c)?.cn ?? c);
  const propCn = useMemo(() => Object.fromEntries((cat?.user_props ?? []).map((u) => [u.key, u.cn])), [cat]);
  const charts = useMemo(() => (preview ? Object.entries(preview.profile).map(([prop, rows]) => [prop, hbar(rows.slice(0, 8), preview.users)] as const) : []), [preview]);
  if (editing) return <SegmentForm cat={cat} segOpts={segOpts} existing={seg} initial={{ name: seg.name, description: seg.description, kind: seg.kind, rule: normalizeRule(seg.rule), update: seg.update, tz: seg.tz }} onDone={async () => { setEditing(false); await onChanged(); }} onCancel={() => setEditing(false)} />;
  const approval = { none: '不需審批', pending: '待審批', approved: `已核可（${seg.approval.by}）`, rejected: `退回：${seg.approval.note}` }[seg.approval.status];
  return (
    <Stack gap="md">
      <Paper>
        <Stack gap="sm">
          <Group gap="xs"><Anchor component={Link} to="/segments" size="sm" c="dimmed">← 分群列表</Anchor><Title order={2}>{seg.name}</Title><StatusBadge tone={STATUS_PILL[seg.status]}>{STATUS_CN[seg.status]}</StatusBadge>{seg.kind !== 'rule' && <Badge color="violet">{KIND_CN[seg.kind]}</Badge>}{seg.latest?.stale && <StatusBadge tone="amber">規則已改，請重新計算</StatusBadge>}</Group>
          {seg.description && <Text size="sm">{seg.description}</Text>}
          <Text size="sm" c="dimmed">{summarizeRule(seg.rule, cat, segOpts)}</Text>
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <StatTile label="最新人數" value={n(seg.latest?.users)} hint={seg.latest ? `v${seg.latest.version} · ${seg.latest.basetime} · ${TRIGGER_CN[seg.latest.trigger]}${seg.latest.status === 'failed' ? ` · 失敗：${seg.latest.error ?? ''}` : ''}` : '尚未計算'} />
            <StatTile label="更新方式" value={<Text fw={600} size="sm">{updateText(seg.update, seg.tz)}</Text>} />
            <StatTile label="審批" value={<Text fw={600} size="sm">{approval}</Text>} hint={`建立者 ${seg.owner} · ${seg.created_at.slice(0, 16)}`} />
          </SimpleGrid>
          <Group gap="xs">
            {actions.filter((a) => a !== 'approve').map((a) => <Button key={a} color={a === 'purge' ? 'red' : undefined} disabled={busy} onClick={() => void act(a)}>{ACTION_CN[a]}</Button>)}
            <Button disabled={busy} onClick={() => void loadPreview()}>預覽人數與畫像</Button>
            {seg.latest?.status === 'success' && can('users', 'use') && <Button disabled={busy} onClick={() => { setCursors([]); void loadMembers(); }}>看成員</Button>}
            {seg.latest?.status === 'success' && can('users', 'export') && <Button disabled={busy} onClick={() => setExporting(true)}>匯出成員（審批）</Button>}
          </Group>
          {exporting && <ExportDialog base={{ kind: 'segment_members', segment_id: seg.id, basetime: 'latest' }} onClose={() => setExporting(false)} />}
          {actions.includes('approve') && <Paper shadow="none" radius="md" bg="var(--soft)" p="sm"><Group gap="xs"><TextInput aria-label="審批意見" placeholder="審批意見（退回時必填）" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} /><Button variant="filled" disabled={busy} onClick={() => void act('approve', { approve: true, note })}>核可</Button><Button color="red" disabled={busy || !note.trim()} onClick={() => void act('approve', { approve: false, note })}>退回</Button></Group></Paper>}
        </Stack>
      </Paper>
      {preview && <Paper><SectionTitle title="預覽" right={<Text size="sm" c="dimmed">{preview.users.toLocaleString()} 人（精確）</Text>} />
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>{charts.map(([prop, opt]) => <Stack key={prop} gap={4}><Text size="xs" fw={600} c="dimmed">{propCn[prop] ?? prop}</Text><Chart option={opt} height={Math.max(120, Math.min(8, preview.profile[prop].length) * 24 + 16)} label={`${propCn[prop] ?? prop} 分布`} /></Stack>)}</SimpleGrid></Paper>}
      {members && <UsersTable res={members} colCn={colCn} busy={busy} canPrev={cursors.length > 0} onPrev={() => { const s = cursors.slice(0, -1); setCursors(s); void loadMembers(s[s.length - 1]); }} onNext={() => { if (members.next_cursor) { setCursors([...cursors, members.next_cursor]); void loadMembers(members.next_cursor); } }} onOpen={can('profile', 'use') ? setOpen : undefined} />}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper p={0} style={{ overflow: 'hidden' }}><Group p="sm" pb={0}><Title order={3}>歷史版本</Title></Group>
          <Table.ScrollContainer minWidth={520}><Table>
            <Table.Thead><Table.Tr><Table.Th>版本</Table.Th><Table.Th>基準日</Table.Th><Table.Th ta="right">人數</Table.Th><Table.Th>狀態</Table.Th><Table.Th>觸發</Table.Th><Table.Th>計算於</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {versions.map((v) => <Table.Tr key={v.version}><Table.Td><Mono>v{v.version}</Mono></Table.Td><Table.Td><Mono>{v.basetime}</Mono></Table.Td><Table.Td ta="right"><Mono>{n(v.users)}</Mono></Table.Td><Table.Td><StatusBadge tone={v.status === 'success' ? 'blue' : v.status === 'failed' ? 'red' : 'gray'}>{v.status}</StatusBadge>{v.error && <Text size="xs" c="dimmed">{v.error}</Text>}</Table.Td><Table.Td>{TRIGGER_CN[v.trigger]}</Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{v.computed_at.slice(0, 16)} · {v.ms} ms</Text></Table.Td></Table.Tr>)}
              {!versions.length && <Table.Tr><Table.Td colSpan={6}><EmptyState>尚未計算</EmptyState></Table.Td></Table.Tr>}
            </Table.Tbody></Table></Table.ScrollContainer></Paper>
        <Paper><SectionTitle title="血緣" />
          <KeyValue rows={[
            ['上游', <Group gap={4} key="up">{(lineage?.up ?? seg.lineage.up).map((u) => <Badge key={u} color="gray">{u}</Badge>)}</Group>],
            ['下游（引用本分群）', <Group gap={4} key="down">{(lineage?.down ?? []).length ? lineage?.down.map((d) => { const sid = d.replace('segment:', ''); return <Badge key={d} component={Link} to={`/segments/${sid}`} color="brand" style={{ cursor: 'pointer' }}>{segOpts.find((s) => s.id === sid)?.name ?? d}</Badge>; }) : <Text size="sm" c="dimmed">無</Text>}</Group>],
          ]} /></Paper>
      </SimpleGrid>
      {open && <ProfileDrawer oneId={open} cat={cat} project={seg.project} onClose={() => setOpen(null)} />}
    </Stack>
  );
}

export type { SegmentStatus };
