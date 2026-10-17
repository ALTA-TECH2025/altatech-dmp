// 元事件管理：事件字典（搜尋、狀態、登記／編輯抽屜、上線檢查、狀態轉移）、屬性字典（登記）、虛擬事件（建立）、隔離區、變更單（審批）。
// 權限：governance.view 看；governance.use 登記／修改；governance.admin 審批變更單。語意層 yaml 來的項目標示「語意層」，執行期登記的標示「待發版」。設計系統元件組合。
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Alert, Badge, Button, Checkbox, Drawer, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { checkEvent, dailyText, ENTITY_CN, KIND_CN, SENS_CN, SENS_PILL, STATUS_CN } from './governance';
import type { ChangeRequest, EventList, EventProp, EventStatus, Level, MetaEvent, MetaEventDetail, MetaProperty, PropertyList, Quarantine, VirtualEvent } from './types';

type Tab = 'dictionary' | 'props' | 'virtual' | 'quarantine' | 'changes';
const SOURCES = ['GA360（App／Web 埋點）', 'App 後台', '開戶系統', '數據中台（批次導入）', '交易 CDC', '帳務 CDC', '基金平台', 'MA 系統事件', 'CRM', '業務系統'];
const opt = (value: string, label: string) => ({ value, label });
const WARN_BG = 'var(--mantine-color-orange-light)';   // 需要注意的列（隔離、待審）底色，跟著深淺色走
const PROP_TYPES = ['string', 'number', 'bool', 'datetime', 'list'];

export function GovernancePage() {
  return <RequireGrant module="governance"><GovernanceInner /></RequireGrant>;
}

function GovernanceInner() {
  const { can } = useAuth();
  const canUse = can('governance', 'use'); const canAdmin = can('governance', 'admin');
  const [tab, setTab] = useState<Tab>('dictionary');
  const [ev, setEv] = useState<EventList | null>(null);
  const [props, setProps] = useState<PropertyList | null>(null);
  const [virt, setVirt] = useState<VirtualEvent[]>([]);
  const [qz, setQz] = useState<Quarantine | null>(null);
  const [crs, setCrs] = useState<ChangeRequest[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState(''); const [status, setStatus] = useState<EventStatus | 'all'>('all');
  const [drawer, setDrawer] = useState<{ event: MetaEvent | null } | null>(null);
  const reload = async () => {
    try { const [a, b, c, d, e] = await Promise.all([api.get<EventList>('/v1/meta/events'), api.get<PropertyList>('/v1/meta/properties'), api.get<VirtualEvent[]>('/v1/meta/virtual-events'), api.get<Quarantine>('/v1/meta/quarantine'), api.get<ChangeRequest[]>('/v1/meta/change-requests')]); setEv(a); setProps(b); setVirt(c); setQz(d); setCrs(e); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); }
  };
  useEffect(() => { void reload(); }, []);
  const list = useMemo(() => (ev?.items ?? []).filter((e) => (status === 'all' || e.status === status) && (!q || e.name.toLowerCase().includes(q.toLowerCase()) || e.cn.includes(q) || e.group.includes(q))), [ev, q, status]);
  const pending = crs.filter((c) => c.status === 'pending').length; const quarantined = ev?.counts.quarantine ?? 0;
  return (
    <div className="page">
      <PageHeader title="元事件管理" lead="事件字典是語意層的根：事件、屬性字典、虛擬事件、隔離區與變更單。未登記事件進隔離區；上線事件改定義走變更單審批；L4 個資屬性拒絕入倉。語意層（events.yaml）為契約來源，這裡登記的項目在發版後進查詢目錄。"
        actions={canUse ? <Button variant="filled" onClick={() => setDrawer({ event: null })}>＋ 登記事件</Button> : <StatusBadge tone="gray">唯讀</StatusBadge>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      <SegmentedControl mb="md" value={tab} onChange={(v) => setTab(v as Tab)} data={[opt('dictionary', `事件字典 ${ev?.total ?? ''}`), opt('props', `屬性字典 ${props?.items.length ?? ''}`), opt('virtual', `虛擬事件 ${virt.length}`), opt('quarantine', `隔離區${quarantined ? ` ${quarantined}` : ''}`), opt('changes', `變更單${pending ? ` ${pending}` : ''}`)]} />
      {tab === 'dictionary' && ev && (
        <Stack gap="md">
          <Group gap="xs" wrap="wrap">
            <TextInput aria-label="搜尋事件" placeholder="搜尋事件名稱／顯示名／業務線（例：開戶、sip_、$Plan）" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 320px' }} />
            <SegmentedControl size="xs" value={status} onChange={(v) => setStatus(v as EventStatus | 'all')} data={[opt('all', `全部 ${ev.total}`), opt('live', `上線 ${ev.counts.live ?? 0}`), opt('draft', `草稿 ${ev.counts.draft ?? 0}`), opt('quarantine', `隔離 ${ev.counts.quarantine ?? 0}`), opt('deprecated', `停用 ${ev.counts.deprecated ?? 0}`)]} />
            <Text size="sm" c="dimmed">{ev.stats_source ? '日均量 = 近 7 天 ClickHouse 實際' : '日均量：未連接 ClickHouse'}</Text>
          </Group>
          <Paper p={0} style={{ overflow: 'hidden' }}>
            <Table.ScrollContainer minWidth={1040}>
              <Table>
                <Table.Thead><Table.Tr><Table.Th>事件</Table.Th><Table.Th>業務線</Table.Th><Table.Th>平台</Table.Th><Table.Th>類別</Table.Th><Table.Th>來源</Table.Th><Table.Th>屬性</Table.Th><Table.Th ta="right">日均量</Table.Th><Table.Th>負責人</Table.Th><Table.Th>狀態</Table.Th><Table.Th /></Table.Tr></Table.Thead>
                <Table.Tbody>{list.map((e) => (
                  <Table.Tr key={e.name} bg={e.status === 'quarantine' ? WARN_BG : undefined}>
                    <Table.Td><Text fw={600} size="sm" ff="monospace">{e.name}</Text><Text size="xs" c="dimmed">{e.cn}{e.aliases.length > 0 && ` · 舊名 ${e.aliases.join('、')}`}{e.note && ` · ${e.note}`}</Text></Table.Td>
                    <Table.Td>{e.group}</Table.Td>
                    <Table.Td><Text size="xs" c="dimmed" ff="monospace">{e.platforms.replace(/JavaScript/g, 'JS').replace(/服務端/g, 'Server')}</Text></Table.Td>
                    <Table.Td><Badge color="gray">{KIND_CN[e.kind]}</Badge></Table.Td>
                    <Table.Td><Text size="xs">{e.source}</Text></Table.Td>
                    <Table.Td><Text size="xs" ff="monospace">{e.properties.slice(0, 4).map((p) => p.key).join(', ')}{e.properties.length > 4 && ` +${e.properties.length - 4}`}</Text></Table.Td>
                    <Table.Td ta="right"><Mono>{dailyText(e.daily)}</Mono></Table.Td>
                    <Table.Td>{e.owner || '—'}</Table.Td>
                    <Table.Td><Group gap={4}><StatusBadge tone={STATUS_CN[e.status].pill}>{STATUS_CN[e.status].cn}</StatusBadge>{!e.catalog_synced && <Badge color="orange" title="已登記，發版（契約 PR）後進查詢目錄">待發版</Badge>}{e.pending_change && <StatusBadge tone="amber">變更單</StatusBadge>}</Group></Table.Td>
                    <Table.Td ta="right"><Button size="compact-sm" variant="subtle" onClick={() => setDrawer({ event: e })}>{canUse ? (e.status === 'quarantine' ? '登記' : '編輯') : '查看'}</Button></Table.Td>
                  </Table.Tr>))}</Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
          <SimpleGrid cols={{ base: 1, md: 3 }}>
            <Paper><SectionTitle title="事件生命週期" /><Text size="sm" c="dimmed">草稿 → 上線（檢查：命名、負責人、屬性型別／敏感等級、無 L4）→ 變更需審批 → 停用（保留歷史）。未登記事件由 Collector 攔進隔離區，日批統計後通知負責人登記；30 天未登記自動丟棄。</Text></Paper>
            <Paper><SectionTitle title="與語意層的關係" /><Text size="sm" c="dimmed">事件字典是 semantic/events.yaml 的來源：上線事件出現在事件分析／漏斗的事件選單與 agent 的工具目錄；ga4_mapping 依「來源=GA360」的事件自動產生映射。這裡登記的事件在發版（契約 PR）後同步進語意層。</Text></Paper>
            <Paper><SectionTitle title="變更與審計" /><Text size="sm" c="dimmed">上線事件的屬性型別、敏感等級、主體、類別變更建立變更單，需資料治理管理員審批且不可自審；所有登記、狀態轉移、審批都寫審計（meta.*）。</Text></Paper>
          </SimpleGrid>
        </Stack>
      )}
      {tab === 'props' && props && <PropsTab props={props} canUse={canUse} onChange={reload} />}
      {tab === 'virtual' && <VirtualTab items={virt} events={ev?.items ?? []} canUse={canUse} onChange={reload} />}
      {tab === 'quarantine' && qz && <QuarantineTab qz={qz} canUse={canUse} onRegister={(name) => setDrawer({ event: { name, cn: '', group: '未登記', entity: 'user', source: 'GA360（App／Web 埋點）', kind: 'behavior', status: 'quarantine', platforms: '', aliases: [], properties: [], owner: '', note: '', catalog_synced: false, version: 0 } })} />}
      {tab === 'changes' && <ChangesTab items={crs} canAdmin={canAdmin} onChange={reload} />}
      {drawer && <EventDrawer event={drawer.event} all={ev?.items ?? []} props={props?.items ?? []} forbidden={new Set(props?.forbidden_l4 ?? [])} canUse={canUse} onClose={() => setDrawer(null)} onSaved={async () => { setDrawer(null); await reload(); }} />}
    </div>
  );
}

function EventDrawer({ event, all, props, forbidden, canUse, onClose, onSaved }: { event: MetaEvent | null; all: MetaEvent[]; props: MetaProperty[]; forbidden: Set<string>; canUse: boolean; onClose: () => void; onSaved: () => Promise<void> }) {
  const isNew = !event || event.status === 'quarantine';
  const [f, setF] = useState({ name: event?.name ?? '', cn: event?.cn ?? '', entity: event?.entity ?? 'user', kind: event?.kind ?? 'behavior', source: event?.source ?? SOURCES[0], owner: event?.owner ?? '', note: event?.note ?? '', group: event?.group ?? '', path_node_rule: event?.path_node_rule ?? '',
    properties: (event?.properties ?? []).map((p) => ({ key: p.key, type: p.type ?? props.find((x) => x.key === p.key)?.type ?? 'string', sensitivity: (p.sensitivity ?? props.find((x) => x.key === p.key)?.sensitivity ?? 'L1') as Level, required: p.required ?? true })) as EventProp[] });
  const [detail, setDetail] = useState<MetaEventDetail | null>(null);
  const [newProp, setNewProp] = useState('');
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [reason, setReason] = useState('');
  useEffect(() => { if (event && event.status !== 'quarantine') api.get<MetaEventDetail>(`/v1/meta/events/${encodeURIComponent(event.name)}`).then(setDetail).catch(() => setDetail(null)); }, [event]);
  const errs = checkEvent(f, new Set(all.filter((e) => e.status !== 'quarantine').map((e) => e.name)), isNew, forbidden);
  const live = event?.status === 'live';
  const setProp = (i: number, p: Partial<EventProp>) => setF({ ...f, properties: f.properties.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const submit = async (status: 'draft' | 'live') => {
    setBusy(true); setErr(null);
    try {
      const body = { ...f, status };
      if (isNew) await api.post('/v1/meta/events', body);
      else if (live) await api.patch(`/v1/meta/events/${encodeURIComponent(event.name)}`, { ...f, reason });
      else { await api.patch(`/v1/meta/events/${encodeURIComponent(event.name)}`, f); if (status === 'live') await api.post(`/v1/meta/events/${encodeURIComponent(event.name)}/status`, { to: 'live' }); }
      await onSaved();
    } catch (e) { setErr(e instanceof ApiError ? (typeof e.body === 'object' && e.body && 'detail' in e.body && typeof (e.body as { detail: unknown }).detail === 'object' ? JSON.stringify((e.body as { detail: { errors?: string[] } }).detail.errors ?? e.message) : e.message) : '儲存失敗'); }
    finally { setBusy(false); }
  };
  const deprecate = async () => { if (!event || !confirm(`停用 ${event.name}？不再接收但保留歷史。`)) return; setBusy(true); try { await api.post(`/v1/meta/events/${encodeURIComponent(event.name)}/status`, { to: 'deprecated', reason }); await onSaved(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } finally { setBusy(false); } };
  return (
    <Drawer opened onClose={onClose} position="right" size={720} withCloseButton={false} padding="md">
      <Stack gap="md">
        <SectionTitle title={isNew ? (event ? `登記隔離區事件：${event.name}` : '登記新事件') : `編輯事件：${event.name}`} right={<Button size="compact-sm" variant="subtle" onClick={onClose}>關閉</Button>}>{event && <StatusBadge tone={STATUS_CN[event.status].pill}>{STATUS_CN[event.status].cn}</StatusBadge>}</SectionTitle>
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label="事件名稱" ff="monospace" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} disabled={!isNew || !!event} placeholder="例：fund_redeem（上線後不可改名，只能停用）" />
          <TextInput label="顯示名稱" value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} disabled={!canUse} />
          <NativeSelect label="主體" value={f.entity} onChange={(e) => setF({ ...f, entity: e.target.value as MetaEvent['entity'] })} disabled={!canUse} data={(Object.keys(ENTITY_CN) as MetaEvent['entity'][]).map((k) => opt(k, ENTITY_CN[k]))} />
          <NativeSelect label="類別" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as MetaEvent['kind'] })} disabled={!canUse} data={(Object.keys(KIND_CN) as MetaEvent['kind'][]).map((k) => opt(k, KIND_CN[k]))} />
          <NativeSelect label="來源" value={SOURCES.includes(f.source) ? f.source : '__other'} onChange={(e) => setF({ ...f, source: e.target.value === '__other' ? f.source : e.target.value })} disabled={!canUse} data={[...SOURCES.map((s) => opt(s, s)), opt('__other', SOURCES.includes(f.source) ? '其他…' : f.source)]} />
          <TextInput label="業務線" value={f.group} onChange={(e) => setF({ ...f, group: e.target.value })} disabled={!canUse} />
          <TextInput label="負責人" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} placeholder="團隊或人" disabled={!canUse} />
          <NativeSelect label="路徑節點規則" value={f.path_node_rule ?? ''} onChange={(e) => setF({ ...f, path_node_rule: e.target.value })} disabled={!canUse} data={[opt('', '事件本身為節點'), ...f.properties.map((p) => opt(p.key, `按 ${p.key} 拆分`))]} />
        </SimpleGrid>
        <TextInput label="說明" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} disabled={!canUse} />
        <div>
          <SectionTitle title="事件屬性"><Text size="xs" c="dimmed">屬性須先存在於屬性字典；新屬性在此登記會同步進字典；型別與敏感等級一經上線不可降級</Text></SectionTitle>
          <Table.ScrollContainer minWidth={520}>
            <Table>
              <Table.Thead><Table.Tr><Table.Th>屬性</Table.Th><Table.Th>型別</Table.Th><Table.Th>敏感等級</Table.Th><Table.Th>必填</Table.Th><Table.Th /></Table.Tr></Table.Thead>
              <Table.Tbody>{f.properties.map((p, i) => <Table.Tr key={p.key}>
                <Table.Td><Group gap={4}><Mono>{p.key}</Mono>{!props.some((x) => x.key === p.key) && <StatusBadge tone="amber">新</StatusBadge>}</Group></Table.Td>
                <Table.Td><NativeSelect aria-label={`${p.key} 型別`} value={p.type} onChange={(e) => setProp(i, { type: e.target.value })} disabled={!canUse} data={PROP_TYPES.map((t) => opt(t, t))} /></Table.Td>
                <Table.Td><NativeSelect aria-label={`${p.key} 敏感等級`} value={p.sensitivity} onChange={(e) => setProp(i, { sensitivity: e.target.value as Level })} disabled={!canUse} data={(['L0', 'L1', 'L2', 'L3', 'L4'] as Level[]).map((s) => opt(s, `${s} ${SENS_CN[s]}`))} /></Table.Td>
                <Table.Td><Checkbox aria-label={`${p.key} 必填`} checked={p.required ?? true} onChange={(e) => setProp(i, { required: e.currentTarget.checked })} disabled={!canUse} /></Table.Td>
                <Table.Td ta="right">{canUse && <Button size="compact-sm" variant="subtle" color="red" onClick={() => setF({ ...f, properties: f.properties.filter((_, j) => j !== i) })}>✕</Button>}</Table.Td>
              </Table.Tr>)}</Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {canUse && <Group gap="xs" wrap="wrap" mt="xs">
            <NativeSelect aria-label="從屬性字典加入" value="" onChange={(e) => { const p = props.find((x) => x.key === e.target.value); if (p) setF({ ...f, properties: [...f.properties, { key: p.key, type: p.type, sensitivity: p.sensitivity, required: true }] }); }} data={[opt('', '＋ 從屬性字典加入'), ...props.filter((x) => !f.properties.some((p) => p.key === x.key)).map((x) => opt(x.key, `${x.cn}（${x.key}）`))]} />
            <TextInput aria-label="新屬性名稱" placeholder="或輸入新屬性名稱後按 Enter" value={newProp} onChange={(e) => setNewProp(e.target.value)} style={{ flex: '1 1 240px' }} onKeyDown={(e) => { if (e.key === 'Enter' && newProp.trim()) { e.preventDefault(); setF({ ...f, properties: [...f.properties, { key: newProp.trim(), type: 'string', sensitivity: 'L1', required: true }] }); setNewProp(''); } }} />
          </Group>}
        </div>
        <div>
          <SectionTitle title="上線檢查" />
          {errs.length ? <List size="sm" c="red">{errs.map((e) => <List.Item key={e}>{e}</List.Item>)}</List> : <StatusBadge tone="blue">可上線：命名、負責人、屬性型別與敏感等級皆完整</StatusBadge>}
        </div>
        {live && <TextInput label="變更理由（改屬性／主體／類別會建立變更單，需治理管理員審批）" value={reason} onChange={(e) => setReason(e.target.value)} />}
        {detail && detail.history.length > 0 && <div><SectionTitle title="歷程"><Text size="xs" c="dimmed">{detail.used_by.join('、')}</Text></SectionTitle><List size="sm">{detail.history.slice(-6).map((h, i) => <List.Item key={i}><Mono>{h.at.slice(0, 16).replace('T', ' ')}</Mono> {h.action} · {h.by}</List.Item>)}</List></div>}
        {canUse && <Group gap="xs" justify="space-between" wrap="wrap">
          <div>{live && <Button color="red" disabled={busy} onClick={() => void deprecate()}>停用</Button>}</div>
          <Group gap="xs">
            {!live && <Button disabled={busy} onClick={() => void submit('draft')}>存為草稿</Button>}
            <Button variant="filled" disabled={busy || (!live && errs.length > 0)} onClick={() => void submit('live')}>{live ? '送出變更' : '登記並上線'}</Button>
          </Group>
        </Group>}
        <Text size="sm" c="dimmed">上線後：事件進語意層（agent 可查）、Collector 放行、Flink 事件化套用；改屬性型別或敏感等級需走審批；停用 = 不再接收但保留歷史。</Text>
      </Stack>
    </Drawer>
  );
}

function PropsTab({ props, canUse, onChange }: { props: PropertyList; canUse: boolean; onChange: () => Promise<void> }) {
  const [q, setQ] = useState(''); const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ key: '', cn: '', type: 'string', scope: 'event', sensitivity: 'L1' as Level, source: '', enum: '', unit: '' });
  const list = props.items.filter((p) => !q || p.key.includes(q) || p.cn.includes(q) || p.sa.includes(q));
  const submit = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api.post('/v1/meta/properties', { ...f, enum: f.enum ? f.enum.split(/[,，\s]+/).filter(Boolean) : [] }); setOpen(false); setF({ ...f, key: '', cn: '', enum: '' }); await onChange(); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <TextInput aria-label="搜尋屬性" placeholder="搜尋屬性／神策舊名" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 280px' }} />
        <Text size="sm" c="dimmed">L4 屬性一律拒絕入倉（{props.forbidden_l4.length} 個已列黑名單）；L3 以 HMAC／FPE 代碼化；L2 匯出區間化</Text>
        {canUse && <Button ml="auto" onClick={() => setOpen((v) => !v)}>＋ 登記屬性</Button>}
      </Group>
      {open && <Paper component="form" onSubmit={submit}><Stack gap="md">
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="屬性名" ff="monospace" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} required />
          <TextInput label="顯示名" value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} />
          <NativeSelect label="型別" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} data={['string', 'number', 'bool', 'datetime', 'list', 'enum'].map((t) => opt(t, t))} />
          <NativeSelect label="範圍" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} data={[opt('event', '事件'), opt('user', '用戶'), opt('item', 'Item')]} />
          <NativeSelect label="敏感等級" value={f.sensitivity} onChange={(e) => setF({ ...f, sensitivity: e.target.value as Level })} data={(['L0', 'L1', 'L2', 'L3'] as Level[]).map((s) => opt(s, `${s} ${SENS_CN[s]}`))} />
          <TextInput label="來源" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} />
          <TextInput label="枚舉（逗號分隔）" value={f.enum} onChange={(e) => setF({ ...f, enum: e.target.value })} />
          <TextInput label="單位" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} />
        </SimpleGrid>
        <Group gap="xs"><Button type="submit" variant="filled">登記</Button><Text size="sm" c="dimmed">既有屬性：型別只能放寬、敏感等級只能升</Text></Group>
      </Stack></Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={960}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>屬性</Table.Th><Table.Th>神策舊名</Table.Th><Table.Th>型別</Table.Th><Table.Th>敏感等級</Table.Th><Table.Th>來源</Table.Th><Table.Th>枚舉／單位</Table.Th><Table.Th>維度表／字典</Table.Th><Table.Th ta="right">使用事件數</Table.Th><Table.Th>狀態</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{list.map((p) => <Table.Tr key={p.key}>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{p.key}</Text><Text size="xs" c="dimmed">{p.cn}{p.caveats.length > 0 && ` · ${p.caveats[0]}`}</Text></Table.Td>
              <Table.Td><Text size="xs" c="dimmed" ff="monospace">{p.sa || '—'}</Text></Table.Td>
              <Table.Td><Mono>{p.type}</Mono></Table.Td>
              <Table.Td><Group gap={4}><StatusBadge tone={SENS_PILL[p.sensitivity]}>{p.sensitivity}</StatusBadge>{p.row_level && <Badge color="gray">行級鍵</Badge>}</Group></Table.Td>
              <Table.Td><Text size="xs">{p.source}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{p.enum.length ? p.enum.join(' | ') : p.unit || '—'}</Text></Table.Td>
              <Table.Td><Group gap={4}>{p.dimension_table && <StatusBadge tone="blue">{p.dimension_table}</StatusBadge>}{p.dictionary && <Badge color="gray">字典</Badge>}</Group></Table.Td>
              <Table.Td ta="right"><Mono>{p.used_by}</Mono></Table.Td>
              <Table.Td>{p.in_catalog ? <StatusBadge tone="gray">語意層</StatusBadge> : <StatusBadge tone="amber">待發版</StatusBadge>}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
    </Stack>
  );
}

function VirtualTab({ items, events, canUse, onChange }: { items: VirtualEvent[]; events: MetaEvent[]; canUse: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ name: 'v.', cn: '', owner: '', note: '', members: [{ event: '', filters: '' }] });
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try {
      const members = f.members.filter((m) => m.event).map((m) => { const cond = m.filters.trim(); const mm = cond.match(/^([\w$.]+)\s*(=|in)\s*(.+)$/); return cond && mm ? { event: m.event, filters: [{ field: mm[1], op: mm[2] === '=' ? 'eq' : 'in', value: mm[2] === '=' ? mm[3].trim() : mm[3].split(/[,，\s]+/).filter(Boolean) }] } : { event: m.event }; });
      await api.post('/v1/meta/virtual-events', { ...f, members }); setOpen(false); setF({ name: 'v.', cn: '', owner: '', note: '', members: [{ event: '', filters: '' }] }); await onChange();
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); }
  };
  return (
    <Stack gap="md">
      {canUse && <Group gap="xs"><Button onClick={() => setOpen((v) => !v)}>＋ 建立虛擬事件</Button></Group>}
      {open && <Paper component="form" onSubmit={submit}><Stack gap="md">
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="名稱（v. 開頭）" ff="monospace" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
          <TextInput label="顯示名" value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} />
          <TextInput label="負責人" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} />
        </SimpleGrid>
        {f.members.map((m, i) => <Group gap="xs" key={i} wrap="wrap">
          <StatusBadge tone="blue">{i ? 'OR' : '事件'}</StatusBadge>
          <NativeSelect aria-label={`成員事件 ${i + 1}`} value={m.event} onChange={(e) => setF({ ...f, members: f.members.map((x, j) => (j === i ? { ...x, event: e.target.value } : x)) })} data={[opt('', '選事件…'), ...events.filter((e) => e.status === 'live').map((e) => opt(e.name, `${e.cn}（${e.name}）`))]} style={{ flex: '1 1 180px' }} />
          <TextInput aria-label={`成員事件 ${i + 1} 屬性條件`} placeholder="屬性條件（選填）例：platform in ios,android 或 page_group = account_open" value={m.filters} onChange={(e) => setF({ ...f, members: f.members.map((x, j) => (j === i ? { ...x, filters: e.target.value } : x)) })} style={{ flex: '1 1 300px' }} />
          {f.members.length > 1 && <Button type="button" size="compact-sm" variant="subtle" color="red" onClick={() => setF({ ...f, members: f.members.filter((_, j) => j !== i) })}>✕</Button>}
        </Group>)}
        <Group gap="xs"><Button type="button" size="compact-sm" variant="subtle" onClick={() => setF({ ...f, members: [...f.members, { event: '', filters: '' }] })}>＋ OR 事件</Button><Button type="submit" variant="filled">建立</Button></Group>
      </Stack></Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={760}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>虛擬事件</Table.Th><Table.Th>定義</Table.Th><Table.Th>負責人</Table.Th><Table.Th>說明</Table.Th><Table.Th>來源</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{items.map((v) => <Table.Tr key={v.name}>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{v.name}</Text><Text size="xs" c="dimmed">{v.cn}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{v.definition}</Text></Table.Td>
              <Table.Td>{v.owner}</Table.Td>
              <Table.Td><Text size="xs">{v.note}</Text></Table.Td>
              <Table.Td>{v.origin === 'catalog' ? <StatusBadge tone="gray">語意層</StatusBadge> : <StatusBadge tone="amber">待發版</StatusBadge>}</Table.Td>
              <Table.Td ta="right">{canUse && v.origin === 'registered' && <Button size="compact-sm" variant="subtle" color="red" onClick={() => { if (confirm(`刪除 ${v.name}？`)) api.del(`/v1/meta/virtual-events/${encodeURIComponent(v.name)}`).then(onChange).catch(() => undefined); }}>刪除</Button>}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Text size="sm" c="dimmed">虛擬事件 = 多個真實事件（可帶屬性條件）的 OR 組合，在語意層展開，不另存資料；可用於事件分析、漏斗、留存與標籤定義。</Text>
    </Stack>
  );
}

function QuarantineTab({ qz, canUse, onRegister }: { qz: Quarantine; canUse: boolean; onRegister: (name: string) => void }) {
  return (
    <Stack gap="md">
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Group p="sm" pb={0} gap="xs" wrap="wrap"><Title order={3}>未登記事件</Title><Text size="xs" c="dimmed">{qz.stats_source ? '近 30 天 ClickHouse 實際' : '未連接 ClickHouse：只列語意層標示為隔離的事件'}</Text></Group>
        <Table.ScrollContainer minWidth={760}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>事件</Table.Th><Table.Th ta="right">筆數</Table.Th><Table.Th ta="right">用戶</Table.Th><Table.Th>首見</Table.Th><Table.Th>最近</Table.Th><Table.Th>狀態</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{qz.unregistered_events.length === 0 ? <Table.Tr><Table.Td colSpan={7}><Text size="sm" c="dimmed">沒有未登記事件。</Text></Table.Td></Table.Tr> : qz.unregistered_events.map((e) => <Table.Tr key={e.event} bg={WARN_BG}>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{e.event}</Text>{e.note && <Text size="xs" c="dimmed">{e.note}</Text>}</Table.Td>
              <Table.Td ta="right"><Mono>{e.n ?? '—'}</Mono></Table.Td>
              <Table.Td ta="right"><Mono>{e.users ?? '—'}</Mono></Table.Td>
              <Table.Td><Mono>{e.first_seen?.slice(0, 16) ?? '—'}</Mono></Table.Td>
              <Table.Td><Mono>{e.last_seen?.slice(0, 16) ?? '—'}</Mono></Table.Td>
              <Table.Td><StatusBadge tone="red">隔離 · {e.retention_days} 天後丟棄</StatusBadge></Table.Td>
              <Table.Td ta="right">{canUse && <Button size="compact-sm" variant="subtle" onClick={() => onRegister(e.event)}>登記放行</Button>}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Group p="sm" pb={0}><Title order={3}>同步任務隔離列（近 30 天）</Title></Group>
        <Table.ScrollContainer minWidth={520}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>任務</Table.Th><Table.Th>原因</Table.Th><Table.Th ta="right">筆數</Table.Th><Table.Th>最近</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{qz.rows.length === 0 ? <Table.Tr><Table.Td colSpan={4}><Text size="sm" c="dimmed">沒有隔離列。</Text></Table.Td></Table.Tr> : qz.rows.map((r, i) => <Table.Tr key={i}><Table.Td><Mono>{r.task_id}</Mono></Table.Td><Table.Td>{r.reason}</Table.Td><Table.Td ta="right"><Mono>{r.n}</Mono></Table.Td><Table.Td><Mono>{String(r.last_at).slice(0, 16)}</Mono></Table.Td></Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Text size="sm" c="dimmed">{qz.policy.note}</Text>
    </Stack>
  );
}

function ChangesTab({ items, canAdmin, onChange }: { items: ChangeRequest[]; canAdmin: boolean; onChange: () => Promise<void> }) {
  const [err, setErr] = useState<string | null>(null);
  const decide = async (id: string, approve: boolean) => { const note = approve ? '' : prompt('駁回理由') ?? ''; try { await api.post(`/v1/meta/change-requests/${id}/decide`, { approve, note }); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={760}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>變更單</Table.Th><Table.Th>事件</Table.Th><Table.Th>變更</Table.Th><Table.Th>提出</Table.Th><Table.Th>狀態</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{items.length === 0 ? <Table.Tr><Table.Td colSpan={6}><Text size="sm" c="dimmed">沒有變更單。上線事件的屬性、主體、類別變更會在這裡等待審批。</Text></Table.Td></Table.Tr> : items.map((c) => <Table.Tr key={c.id} bg={c.status === 'pending' ? WARN_BG : undefined}>
              <Table.Td><Mono>{c.id}</Mono></Table.Td>
              <Table.Td><Mono>{c.event}</Mono></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{Object.keys(c.changes).join('、')}</Text>{c.note && <Text size="xs" c="dimmed">{c.note}</Text>}</Table.Td>
              <Table.Td>{c.requested_by}<Text size="xs" c="dimmed" ff="monospace">{c.at.slice(0, 16).replace('T', ' ')}</Text></Table.Td>
              <Table.Td><StatusBadge tone={c.status === 'pending' ? 'amber' : c.status === 'approved' ? 'blue' : 'gray'}>{c.status === 'pending' ? '待審' : c.status === 'approved' ? '已核准' : '已駁回'}</StatusBadge>{c.decided_by && <Text size="xs" c="dimmed">{c.decided_by}</Text>}</Table.Td>
              <Table.Td ta="right">{canAdmin && c.status === 'pending' && <Group gap={4} justify="flex-end" wrap="nowrap"><Button size="compact-sm" variant="subtle" onClick={() => void decide(c.id, true)}>核准</Button><Button size="compact-sm" variant="subtle" color="red" onClick={() => void decide(c.id, false)}>駁回</Button></Group>}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Text size="sm" c="dimmed">審批需 governance.admin（資訊管理／系統管理員），不可核准自己提出的變更單；核准後定義升版，審計 meta.change_request.decided。</Text>
    </Stack>
  );
}
