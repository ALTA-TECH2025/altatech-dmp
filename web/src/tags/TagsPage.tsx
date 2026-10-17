// 標籤：目錄列表（狀態／型別／人數／更新／分布）→ 詳情（分布圖、版本、血緣、動作）→ 新建（規則標籤：每值一條規則；基礎指標值／首末次特徵／RFM；SQL 標籤）。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Alert, Anchor, Badge, Button, Code, Collapse, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Textarea, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { Chart, hbar } from '@/ds/Chart';
import { EmptyState, KeyValue, Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { RuleBuilder } from '@/users/RuleBuilder';
import { cleanRule, emptyRule, ruleValid, summarizeRule } from '@/users/rule';
import type { Segment } from '@/users/types';
import { AGG_CN, DTYPE_CN, FEATURE_CN, TAG_KIND_CN, TAG_STATUS_CN, TAG_STATUS_PILL, bucketLabels, fmtTs, parseBuckets, specText, suggestKey, topDistribution, updateText } from './tags';
import type { Tag, TagCreate, TagCustomFields, TagKind, TagSpec, TagValue, TagVersion } from './types';

export function TagsPage() {
  return <RequireGrant module="segments"><TagsInner /></RequireGrant>;
}
const n = (x: number | null | undefined) => (x == null ? '—' : x.toLocaleString());
const opt = (value: string, label: string) => ({ value, label });

function TagsInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const { id } = useParams();
  const [list, setList] = useState<Tag[]>([]);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [cf, setCf] = useState<TagCustomFields | null>(null);
  const [segOpts, setSegOpts] = useState<{ id: string; name: string }[]>([]);
  const [tab, setTab] = useState<'all' | 'pending' | 'recycled'>('all');
  const [search, setSearch] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => { const [a, r] = await Promise.all([api.get<Tag[]>('/v1/tags'), api.get<Tag[]>('/v1/tags?status=recycled')]); setList([...a, ...r]); };
  useEffect(() => {
    reload().catch((e: Error) => setErr(e.message));
    api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch(() => null);
    api.get<TagCustomFields>('/v1/tags/custom-fields').then(setCf).catch(() => null);
    api.get<Segment[]>('/v1/segments').then((s) => setSegOpts(s.map((x) => ({ id: x.id, name: x.name })))).catch(() => null);
  }, []);
  const shown = list.filter((t) => (tab === 'recycled' ? t.status === 'recycled' : tab === 'pending' ? t.status === 'pending_approval' : t.status !== 'recycled') && (!search || t.name.includes(search) || t.id.includes(search) || (t.description ?? '').includes(search)));
  const current = id && id !== 'new' ? list.find((t) => t.id === id) ?? null : null;
  const pending = list.filter((t) => t.status === 'pending_approval').length;
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(true); setErr(null); setMsg(null); try { await fn(); await reload(); setMsg(`${label}完成`); } catch (e) { setErr(e instanceof ApiError ? e.message : `${label}失敗`); } finally { setBusy(false); } };
  return (
    <div className="page">
      <PageHeader title="標籤" lead="標籤 = 每個用戶一個值，存成 Bitmap 版本（dmp.user_tags），可在推送名單時附帶、在分析時當維度。動態標籤：每個標籤值一條規則（用戶屬性、事件、事件屬性、指標比較，且／或分組），每日凌晨依規則重算最新符合資格的受眾；另有基礎指標值、首末次特徵、RFM 與 SQL 標籤；行銷建立需數位經營審批。"
        actions={can('segments', 'use') && <Button component={Link} to="/tags/new" variant="filled">＋ 新建標籤</Button>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}{msg && <Alert color="teal" variant="light" mb="md">{msg}</Alert>}
      {id === 'new' ? <TagForm cat={cat} cf={cf} segOpts={segOpts} onDone={async (t) => { await reload(); nav(`/tags/${t.id}`); }} onCancel={() => nav('/tags')} />
        : current ? <TagDetail tag={current} cat={cat} segOpts={segOpts} me={me?.user.id} can={can} busy={busy} act={act} />
        : (
          <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)' }}>
            <Paper p={0} style={{ overflow: 'hidden' }}>
              <Group p="sm" gap="sm">
                <SegmentedControl size="xs" value={tab} onChange={(v) => setTab(v as typeof tab)} data={[{ value: 'all', label: '全部' }, { value: 'pending', label: pending ? `待審批 (${pending})` : '待審批' }, { value: 'recycled', label: '回收站' }]} />
                <TextInput aria-label="搜尋標籤" placeholder="搜尋名稱／key／說明" value={search} onChange={(e) => setSearch(e.target.value)} style={{ flex: 1, maxWidth: 320 }} />
              </Group>
              <Table.ScrollContainer minWidth={900}>
                <Table>
                  <Table.Thead><Table.Tr><Table.Th>標籤</Table.Th><Table.Th>目錄</Table.Th><Table.Th>建立方式</Table.Th><Table.Th>狀態</Table.Th><Table.Th ta="right">覆蓋人數</Table.Th><Table.Th>更新</Table.Th><Table.Th>最近計算</Table.Th><Table.Th>建立者</Table.Th></Table.Tr></Table.Thead>
                  <Table.Tbody>{shown.map((t) => (
                    <Table.Tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => nav(`/tags/${t.id}`)}>
                      <Table.Td><Text fw={600} size="sm">{t.name}</Text><Text size="xs" c="dimmed" ff="monospace">{t.id} · {DTYPE_CN[t.dtype]}</Text></Table.Td>
                      <Table.Td><Text size="sm">{t.directory}</Text></Table.Td>
                      <Table.Td><Text size="sm">{TAG_KIND_CN[t.kind]}{t.kind === 'values' ? `（${t.values.length} 值）` : ''}</Text></Table.Td>
                      <Table.Td><StatusBadge tone={TAG_STATUS_PILL[t.status]}>{TAG_STATUS_CN[t.status]}</StatusBadge></Table.Td>
                      <Table.Td ta="right"><Mono>{n(t.latest?.users)}</Mono></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed">{updateText(t.update)}</Text></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed" ff="monospace">{t.latest ? `${t.latest.basetime} v${t.latest.version}${t.latest.status === 'failed' ? ' 失敗' : ''}` : '尚未計算'}</Text></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed">{t.owner}</Text></Table.Td>
                    </Table.Tr>))}
                    {!shown.length && <Table.Tr><Table.Td colSpan={8}><EmptyState>沒有標籤</EmptyState></Table.Td></Table.Tr>}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
            <Paper><SectionTitle title="說明" />
              <List size="sm" spacing="xs" c="dimmed">
                <List.Item>規則標籤（動態）：每個值一條規則（與分群／用戶列表同一套條件），最多 24 值；同一人可命中多值；每日凌晨重算。</List.Item>
                <List.Item>基礎指標值、首末次特徵、RFM：選事件與回溯期間，後端編譯成同一形狀的查詢，可分桶。</List.Item>
                <List.Item>SQL 標籤：唯讀沙盒驗過的 SELECT，輸出 one_id_uint 與 tag_value 兩欄；涉及脫敏欄位會被擋。</List.Item>
                <List.Item>計算：每值一個 Bitmap 寫入 dmp.user_tags，一次一版、保留 N 版；推送附帶標籤時以 Bitmap 交集取值，數值型一律區間化。</List.Item>
              </List></Paper>
          </SimpleGrid>)}
    </div>
  );
}

function TagDetail({ tag, cat, segOpts, me, can, busy, act }: { tag: Tag; cat: Catalog | null; segOpts: { id: string; name: string }[]; me?: string; can: (m: 'segments', a: 'use' | 'approve') => boolean; busy: boolean; act: (label: string, fn: () => Promise<unknown>) => Promise<void> }) {
  const [versions, setVersions] = useState<TagVersion[]>([]);
  useEffect(() => { api.get<TagVersion[]>(`/v1/tags/${tag.id}/versions`).then(setVersions).catch(() => setVersions(tag.versions ?? [])); }, [tag.id, tag.updated_at]);   // eslint-disable-line react-hooks/exhaustive-deps
  const mine = tag.owner === me; const approver = can('segments', 'approve');
  const dist = useMemo(() => topDistribution(tag.latest?.distribution ?? []), [tag.latest]);
  const distOpt = useMemo(() => hbar(dist.rows, tag.latest?.users ?? undefined), [dist, tag.latest]);
  const fieldCn: Record<string, string> = { department: '所屬部門', requester: '需求提出人', category: '需求類別' };
  const eventCn = (e: string) => cat?.events.find((x) => x.name === e)?.cn ?? e;
  return (
    <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)' }}>
      <Stack gap="md">
        <Paper>
          <Stack gap="sm">
            <Group gap="xs"><Anchor component={Link} to="/tags" size="sm" c="dimmed">← 標籤目錄</Anchor><Title order={2}>{tag.name}</Title><StatusBadge tone={TAG_STATUS_PILL[tag.status]}>{TAG_STATUS_CN[tag.status]}</StatusBadge></Group>
            <KeyValue rows={[
              ['key', <Mono key="k">{tag.id}</Mono>], ['目錄／型別', `${tag.directory} · ${DTYPE_CN[tag.dtype]}`], ['建立方式', TAG_KIND_CN[tag.kind]], ['更新', updateText(tag.update)], ['建立者', `${tag.owner} · ${fmtTs(tag.created_at)}`],
              ...(tag.description ? [['說明', tag.description] as [string, string]] : []),
              ...Object.entries(tag.fields ?? {}).filter(([, v]) => v).map(([k, v]) => [fieldCn[k] ?? k, v] as [string, string]),
              ...(tag.approval.status !== 'none' ? [['審批', tag.approval.status === 'pending' ? '待數位經營核可' : `${tag.approval.status === 'approved' ? '已核可' : '已退回'} · ${tag.approval.by ?? ''} ${fmtTs(tag.approval.at)}${tag.approval.note ? ` · ${tag.approval.note}` : ''}`] as [string, string]] : []),
            ]} />
            <Group gap="xs">
              {(mine || approver) && tag.status === 'active' && <Button variant="filled" disabled={busy} onClick={() => void act('計算', () => api.post(`/v1/tags/${tag.id}/compute`, {}))}>立即計算</Button>}
              {approver && tag.status === 'pending_approval' && !mine && <><Button variant="filled" disabled={busy} onClick={() => void act('核可', () => api.post(`/v1/tags/${tag.id}/approve`, { approve: true, note: '' }))}>核可</Button><Button color="red" disabled={busy} onClick={() => { const note = prompt('退回意見'); if (note) void act('退回', () => api.post(`/v1/tags/${tag.id}/approve`, { approve: false, note })); }}>退回</Button></>}
              {(mine || approver) && tag.status === 'draft' && <Button disabled={busy} onClick={() => void act('送審', () => api.post(`/v1/tags/${tag.id}/submit`, {}))}>送審</Button>}
              {(mine || approver) && (tag.status === 'active' || tag.status === 'paused') && <Button disabled={busy} onClick={() => void act(tag.status === 'active' ? '暫停' : '啟用', () => api.post(`/v1/tags/${tag.id}/pause`, {}))}>{tag.status === 'active' ? '暫停' : '啟用'}</Button>}
              {(mine || approver) && tag.status !== 'recycled' && <Button color="red" disabled={busy} onClick={() => { if (confirm('移入回收站？有下游引用時會被擋。')) void act('移入回收站', () => api.post(`/v1/tags/${tag.id}/recycle`, {})); }}>移入回收站</Button>}
              {(mine || approver) && tag.status === 'recycled' && <Button disabled={busy} onClick={() => void act('恢復', () => api.post(`/v1/tags/${tag.id}/restore`, {}))}>恢復</Button>}
            </Group>
            {tag.latest?.status === 'failed' && <Alert color="red" variant="light">最近計算失敗：{tag.latest.error}</Alert>}
          </Stack>
        </Paper>
        <Paper><SectionTitle title="規則" />
          {tag.spec && ['metric', 'firstlast', 'rfm'].includes(tag.kind) ? <Text size="sm"><b>{TAG_KIND_CN[tag.kind]}</b>：{specText(tag, eventCn)}{tag.project ? `（專案 ${tag.project}）` : ''}</Text>
            : tag.kind === 'values' ? <Table><Table.Thead><Table.Tr><Table.Th>標籤值</Table.Th><Table.Th>規則</Table.Th><Table.Th ta="right">人數（最新版）</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{tag.values.map((v: TagValue) => <Table.Tr key={v.value}><Table.Td><Text fw={600} size="sm">{v.value}</Text></Table.Td><Table.Td><Text size="sm" c="dimmed">{summarizeRule(v.rule, cat, segOpts)}</Text></Table.Td><Table.Td ta="right"><Mono>{n(tag.latest?.distribution.find((d) => d.value === v.value)?.users)}</Mono></Table.Td></Table.Tr>)}</Table.Tbody></Table>
            : <Code block>{tag.sql}</Code>}
        </Paper>
        <Paper p={0} style={{ overflow: 'hidden' }}><Group p="sm" pb={0} justify="space-between"><Title order={3}>歷史版本</Title><Text size="xs" c="dimmed">保留 {tag.update.keep} 版</Text></Group>
          {versions.length ? <Table.ScrollContainer minWidth={620}><Table><Table.Thead><Table.Tr><Table.Th>版本</Table.Th><Table.Th>basetime</Table.Th><Table.Th ta="right">人數</Table.Th><Table.Th>狀態</Table.Th><Table.Th>觸發</Table.Th><Table.Th>計算時間</Table.Th><Table.Th ta="right">耗時</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{[...versions].reverse().map((v) => <Table.Tr key={v.version}><Table.Td><Mono>v{v.version}</Mono></Table.Td><Table.Td><Mono>{v.basetime}</Mono></Table.Td><Table.Td ta="right"><Mono>{n(v.users)}</Mono></Table.Td><Table.Td><StatusBadge tone={v.status === 'success' ? 'blue' : v.status === 'failed' ? 'red' : 'amber'}>{v.status === 'success' ? '成功' : v.status === 'failed' ? '失敗' : '執行中'}</StatusBadge></Table.Td><Table.Td><Text size="sm">{v.trigger === 'routine' ? '例行' : '手動'}</Text></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{fmtTs(v.computed_at)}</Text></Table.Td><Table.Td ta="right"><Mono>{v.ms} ms</Mono></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer> : <EmptyState>尚未計算。</EmptyState>}
        </Paper>
      </Stack>
      <Stack gap="md">
        <Paper><SectionTitle title="值分布" right={<Text size="xs" c="dimmed">{tag.latest ? `v${tag.latest.version} · ${n(tag.latest.users)} 人` : '—'}</Text>} />
          {dist.rows.length ? <Chart option={distOpt} height={Math.max(140, dist.rows.length * 26 + 16)} label={`${tag.name} 值分布`} /> : <EmptyState>計算後顯示各值人數。</EmptyState>}
        </Paper>
        <Paper><SectionTitle title="血緣" />
          <KeyValue rows={[
            ['上游', <Group gap={4} key="up">{tag.lineage.up.length ? tag.lineage.up.map((u) => <Badge key={u} color="gray">{u.startsWith('segment:') ? `分群 ${segOpts.find((s) => s.id === u.slice(8))?.name ?? u.slice(8)}` : u}</Badge>) : '—'}</Group>],
            ['下游', <Group gap={4} key="down">{tag.lineage.down.length ? tag.lineage.down.map((d) => <Badge key={d} component={Link} to="/activation" color="brand" style={{ cursor: 'pointer' }}>{d.startsWith('push_') ? `推送單 ${d}` : `目的地 ${d}`}</Badge>) : '—'}</Group>],
          ]} />
          {tag.warnings?.length ? <Text size="sm" c="dimmed" mt="sm">{tag.warnings.join('；')}</Text> : null}
        </Paper>
      </Stack>
    </SimpleGrid>
  );
}

type Draft = { name: string; key: string; directory: string; description: string; dtype: 'string' | 'number' | 'bool'; kind: TagKind; values: TagValue[]; sql: string; spec: TagSpec; buckets: string; project: string; update: Tag['update']; fields: Record<string, string> };
const SPEC_KINDS: TagKind[] = ['metric', 'firstlast', 'rfm'];
const KIND_OPTS = [opt('values', '規則標籤（動態：每值一條規則）'), opt('metric', '基礎指標值（次數／總和／均值…，可分桶）'), opt('firstlast', '首末次特徵（首次／末次的日期、距今天數、屬性）'), opt('rfm', 'RFM（R／F／M 分級或八類客群）'), opt('sql', 'SQL 標籤')];
function TagForm({ cat, cf, segOpts, onDone, onCancel }: { cat: Catalog | null; cf: TagCustomFields | null; segOpts: { id: string; name: string }[]; onDone: (t: Tag) => Promise<void> | void; onCancel: () => void }) {
  const [d, setD] = useState<Draft>({ name: '', key: '', directory: cf?.directories[0] ?? '客戶價值', description: '', dtype: 'string', kind: 'values', values: [{ value: '', rule: emptyRule('golden') }], spec: { event: '', agg: 'count', window_days: 90, which: 'first', feature: 'date', amount_field: 'amount', bins: 5, output: 'score' }, buckets: '', project: 'golden', sql: "SELECT one_id_uint, toString(count()) AS tag_value\nFROM dmp.events WHERE event = 'bond_deal' AND event_date >= today() - 30\nGROUP BY one_id_uint", update: { mode: 'routine', freq: 'daily', earliest: '02:00', keep: 7 }, fields: {} });   // 動態標籤：預設每日凌晨 02:00 重算
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(0);
  const setV = (i: number, p: Partial<TagValue>) => setD({ ...d, values: d.values.map((v, j) => (j === i ? { ...v, ...p } : v)) });
  const bk = parseBuckets(d.buckets);
  const specOk = !!d.spec.event && bk !== null && (d.kind !== 'metric' || !['sum', 'avg', 'max', 'min'].includes(d.spec.agg ?? '') || !!d.spec.field) && (d.kind !== 'firstlast' || d.spec.feature !== 'field' || !!d.spec.field);
  const valid = d.name.trim() && (d.kind === 'sql' ? d.sql.trim().length > 10 : SPEC_KINDS.includes(d.kind) ? specOk : d.values.length > 0 && d.values.every((v) => v.value.trim() && ruleValid(v.rule)));
  const setS = (p: Partial<TagSpec>) => setD({ ...d, spec: { ...d.spec, ...p } });
  const upd = (p: Partial<Tag['update']>) => setD({ ...d, update: { ...d.update, ...p } });
  const numFields = (cat?.event_columns ?? []).filter((c) => c.type === 'number');
  const events = (cat?.events ?? []).filter((e) => e.status !== 'deprecated').map((e) => opt(e.name, `${e.cn}（${e.name}）`));
  const eventCn = (e: string) => cat?.events.find((x) => x.name === e)?.cn ?? e;
  const save = async () => {
    setBusy(true); setErr(null);
    const body: TagCreate = { name: d.name, key: d.key || suggestKey(d.name) || undefined, directory: d.directory, description: d.description, dtype: d.dtype, kind: d.kind, update: d.update, fields: d.fields,
      ...(d.kind === 'values' ? { values: d.values.map((v) => ({ value: v.value.trim(), rule: cleanRule(v.rule) })) } : d.kind === 'sql' ? { sql: d.sql } : { project: d.project, spec: d.kind === 'metric' ? { event: d.spec.event, agg: d.spec.agg, field: d.spec.field || undefined, window_days: d.spec.window_days, buckets: bk?.length ? bk : undefined } : d.kind === 'firstlast' ? { event: d.spec.event, which: d.spec.which, feature: d.spec.feature, field: d.spec.feature === 'field' ? d.spec.field : undefined, window_days: d.spec.window_days ?? 0, buckets: d.spec.feature === 'days_since' && bk?.length ? bk : undefined } : { event: d.spec.event, amount_field: d.spec.amount_field || 'amount', window_days: d.spec.window_days, bins: d.spec.bins, output: d.spec.output } }) };
    try { await onDone(await api.post<Tag>('/v1/tags', body)); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); }
  };
  const manual = d.update.mode === 'manual';
  return (
    <Paper maw={1040}>
      <Stack gap="md">
        <SectionTitle title="新建標籤" />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="顯示名" aria-label="標籤名稱" required value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} />
          <TextInput label="key（留空自動）" aria-label="標籤 key" ff="monospace" value={d.key} placeholder={suggestKey(d.name) || 'tag_xxx'} onChange={(e) => setD({ ...d, key: e.target.value })} />
          <NativeSelect label="目錄" value={d.directory} data={(cf?.directories ?? ['客戶價值', '其他']).map((x) => opt(x, x))} onChange={(e) => setD({ ...d, directory: e.target.value })} />
          <NativeSelect label="建立方式" aria-label="建立方式" value={d.kind} data={KIND_OPTS} onChange={(e) => setD({ ...d, kind: e.target.value as TagKind, dtype: e.target.value === 'metric' && !d.buckets ? 'number' : d.dtype })} />
          <NativeSelect label="型別" value={d.dtype} data={[opt('string', '字串'), opt('number', '數值（推送時區間化）'), opt('bool', '布林')]} onChange={(e) => setD({ ...d, dtype: e.target.value as 'string' })} />
          <TextInput label="說明" value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} />
        </SimpleGrid>
        {SPEC_KINDS.includes(d.kind) && (
          <Paper shadow="none" radius="md" bg="var(--soft)">
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <NativeSelect label="事件" aria-label="指標事件" value={d.spec.event} data={[opt('', '— 選事件 —'), ...events]} onChange={(e) => setS({ event: e.target.value })} />
              <TextInput label="專案" value={d.project} onChange={(e) => setD({ ...d, project: e.target.value })} />
              <TextInput label={d.kind === 'firstlast' ? '回溯天數（0＝不限）' : '回溯天數'} type="number" min={d.kind === 'firstlast' ? 0 : 1} max={730} value={d.spec.window_days ?? 90} onChange={(e) => setS({ window_days: Number(e.target.value) })} />
              {d.kind === 'metric' && <>
                <NativeSelect label="指標" aria-label="指標彙總" value={d.spec.agg} data={(Object.keys(AGG_CN) as (keyof typeof AGG_CN)[]).map((k) => opt(k, AGG_CN[k]))} onChange={(e) => setS({ agg: e.target.value as TagSpec['agg'] })} />
                {['sum', 'avg', 'max', 'min'].includes(d.spec.agg ?? '') && <NativeSelect label="數值欄位" aria-label="指標欄位" value={d.spec.field ?? ''} data={[opt('', '— 選欄位 —'), ...numFields.map((c) => opt(c.name, `${c.cn}（${c.name}）`)), ...(numFields.some((c) => c.name === 'amount') ? [] : [opt('amount', 'amount')])]} onChange={(e) => setS({ field: e.target.value })} />}
                <TextInput label="分桶界線（逗號分隔，留空輸出原值）" aria-label="分桶" ff="monospace" placeholder="1, 3, 10" value={d.buckets} onChange={(e) => setD({ ...d, buckets: e.target.value, dtype: parseBuckets(e.target.value)?.length ? 'string' : 'number' })} />
              </>}
              {d.kind === 'firstlast' && <>
                <NativeSelect label="首次／末次" aria-label="首末次" value={d.spec.which} data={[opt('first', '首次'), opt('last', '末次')]} onChange={(e) => setS({ which: e.target.value as 'first' | 'last' })} />
                <NativeSelect label="特徵" aria-label="特徵" value={d.spec.feature} data={(Object.keys(FEATURE_CN) as (keyof typeof FEATURE_CN)[]).map((k) => opt(k, FEATURE_CN[k]))} onChange={(e) => setS({ feature: e.target.value as TagSpec['feature'] })} />
                {d.spec.feature === 'field' && <NativeSelect label="屬性" aria-label="特徵欄位" value={d.spec.field ?? ''} data={[opt('', '— 選欄位 —'), ...(cat?.event_columns ?? []).map((c) => opt(c.name, `${c.cn}（${c.name}）`))]} onChange={(e) => setS({ field: e.target.value })} />}
                {d.spec.feature === 'days_since' && <TextInput label="天數分桶（逗號分隔）" aria-label="分桶" ff="monospace" placeholder="7, 30, 90" value={d.buckets} onChange={(e) => setD({ ...d, buckets: e.target.value })} />}
              </>}
              {d.kind === 'rfm' && <>
                <NativeSelect label="金額欄位" aria-label="金額欄位" value={d.spec.amount_field ?? 'amount'} data={[opt('amount', 'amount'), ...numFields.filter((c) => c.name !== 'amount').map((c) => opt(c.name, `${c.cn}（${c.name}）`))]} onChange={(e) => setS({ amount_field: e.target.value })} />
                <TextInput label="分級數" type="number" min={2} max={10} value={d.spec.bins ?? 5} onChange={(e) => setS({ bins: Number(e.target.value) })} />
                <NativeSelect label="輸出" aria-label="RFM 輸出" value={d.spec.output} data={[opt('score', 'R／F／M 分數（R5F4M3）'), opt('segment', '八類客群（重要價值…一般挽留）')]} onChange={(e) => setS({ output: e.target.value as 'score' | 'segment' })} />
              </>}
            </SimpleGrid>
            <Text size="sm" c="dimmed" mt="sm">{d.spec.event ? specText({ kind: d.kind, spec: { ...d.spec, buckets: bk ?? [] } }, eventCn) : '選一個事件。'}{bk === null ? ' 分桶需為遞增數字。' : bk.length ? `（標籤值：${bucketLabels(bk).join('／')}）` : ''}{d.kind === 'rfm' ? ' 各維度以 ntile 分級，高於中位為「高」；只計算窗口內有該事件的人。' : ''}</Text>
          </Paper>
        )}
        {d.kind === 'values' && (
          <Stack gap="xs">
            {d.values.map((v, i) => (
              <Paper key={i} shadow="none" radius="md" p="sm">
                <Group gap="xs" wrap="wrap">
                  <Badge color="brand" variant="filled" circle>{i + 1}</Badge>
                  <TextInput aria-label={`標籤值 ${i + 1}`} placeholder="標籤值，例如 P1" value={v.value} maxLength={40} w={200} onChange={(e) => setV(i, { value: e.target.value })} />
                  <Button size="compact-sm" variant="subtle" onClick={() => setOpen(open === i ? -1 : i)}>{open === i ? '收起規則' : `規則：${summarizeRule(v.rule, cat, segOpts).slice(0, 60) || '未設定'}`}</Button>
                  {d.values.length > 1 && <Button size="compact-sm" variant="subtle" color="red" onClick={() => setD({ ...d, values: d.values.filter((_, j) => j !== i) })}>移除</Button>}
                </Group>
                <Collapse in={open === i}><Paper shadow="none" radius="md" mt="sm"><RuleBuilder q={v.rule} cat={cat} segments={segOpts} onChange={(r) => setV(i, { rule: r })} /></Paper></Collapse>
              </Paper>))}
            {d.values.length < 24 && <Group><Button size="compact-sm" variant="light" onClick={() => { setD({ ...d, values: [...d.values, { value: '', rule: emptyRule('golden') }] }); setOpen(d.values.length); }}>＋ 新增值</Button></Group>}
          </Stack>
        )}
        {d.kind === 'sql' && (
          <Stack gap={4}><Textarea aria-label="標籤 SQL" value={d.sql} spellCheck={false} autosize minRows={5} ff="monospace" onChange={(e) => setD({ ...d, sql: e.target.value })} />
            <Text size="sm" c="dimmed">輸出 one_id_uint 與 tag_value 兩欄；與自定義 SQL 相同沙盒（唯讀、依權限表目錄、脫敏欄位會被擋）。</Text></Stack>
        )}
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <NativeSelect label="更新方式" aria-label="更新方式" value={d.update.mode} data={[opt('manual', '手動'), opt('routine', '例行')]} onChange={(e) => upd({ mode: e.target.value as 'manual' | 'routine' })} />
          <NativeSelect label="頻率" value={d.update.freq} disabled={manual} data={[opt('daily', '每天'), opt('weekly', '每週一'), opt('monthly', '每月 1 日')]} onChange={(e) => upd({ freq: e.target.value as 'daily' })} />
          <TextInput label="最早計算時間" type="time" value={d.update.earliest} disabled={manual} onChange={(e) => upd({ earliest: e.target.value })} />
          <TextInput label="保留版本數" type="number" min={1} max={100} value={d.update.keep} onChange={(e) => upd({ keep: Number(e.target.value) || 1 })} />
          {(cf?.fields ?? []).map((f) => f.type === 'select'
            ? <NativeSelect key={f.key} label={f.cn} value={d.fields[f.key] ?? ''} data={[opt('', '—'), ...(f.options ?? []).map((o) => opt(o, o))]} onChange={(e) => setD({ ...d, fields: { ...d.fields, [f.key]: e.target.value } })} />
            : <TextInput key={f.key} label={f.cn} value={d.fields[f.key] ?? ''} onChange={(e) => setD({ ...d, fields: { ...d.fields, [f.key]: e.target.value } })} />)}
        </SimpleGrid>
        <Group gap="xs"><Button variant="filled" disabled={!valid || busy} loading={busy} onClick={() => void save()}>建立</Button><Button variant="subtle" onClick={onCancel}>取消</Button>{err && <Alert color="red" variant="light" py={6}>{err}</Alert>}</Group>
      </Stack>
    </Paper>
  );
}
