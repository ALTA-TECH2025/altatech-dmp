// 用戶表管理：用戶屬性（來源、更新、敏感、可篩選／分組、覆蓋率；登記新屬性）、OneID 規則（優先序／上限／狀態可改，login_id 不可動；需 governance.admin）、個資防護與統計。設計系統元件組合。
import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Checkbox, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { fmtNum } from '@/analytics/series';
import { KeyValue, Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { pct, SENS_CN, SENS_PILL } from './governance';
import { TemplateTools } from './TemplateTools';
import type { Level, OneIdRule, OneIdRules, UserPropertyList } from './types';

type Tab = 'props' | 'oneid' | 'privacy';
const PRIVACY: [string, string, string][] = [['姓名', '不入倉；細查頁不顯示', '—'], ['身分證字號', '不入倉；OneID 不使用', '正規式 [A-Z][12]\\d{8} 掃描 → 拒絕＋告警'], ['手機', '只存 HMAC-SHA256（金鑰在 KMS）作識別', '09\\d{8} 掃描'], ['Email', '只存 HMAC 作識別', 'RFC 5322 掃描'], ['完整帳號', 'FPE 代碼化（可 JOIN 不可反推）', '—'], ['生日', '只存年齡層分桶', '—'], ['地址', '只存縣市', '—']];
const opt = (value: string, label: string) => ({ value, label });

export function UserSchemaPage() {
  return <RequireGrant module="governance"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const canUse = can('governance', 'use'); const canAdmin = can('governance', 'admin');
  const [tab, setTab] = useState<Tab>('props');
  const [up, setUp] = useState<UserPropertyList | null>(null);
  const [oi, setOi] = useState<OneIdRules | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reload = async () => { try { const [a, b] = await Promise.all([api.get<UserPropertyList>('/v1/meta/user-properties'), api.get<OneIdRules>('/v1/meta/oneid/rules')]); setUp(a); setOi(b); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => { void reload(); }, []);
  return (
    <div className="page">
      <PageHeader title="用戶表管理" lead="用戶屬性（來源、更新、敏感等級、可篩選／分組、覆蓋率）、OneID 識別與合併規則、個資防護。用戶表的每個可篩選屬性都會出現在分析的「用戶屬性」條件裡；L4 欄位永不入倉。" />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      <SegmentedControl mb="md" value={tab} onChange={(v) => setTab(v as Tab)} data={[opt('props', `用戶屬性 ${up?.items.length ?? ''}`), opt('oneid', 'OneID 規則'), opt('privacy', '個資防護與統計')]} />
      {tab === 'props' && up && <PropsTab up={up} canUse={canUse} onChange={reload} />}
      {tab === 'oneid' && oi && <OneIdTab oi={oi} canAdmin={canAdmin} onChange={reload} />}
      {tab === 'privacy' && up && <PrivacyTab up={up} />}
    </div>
  );
}

function PropsTab({ up, canUse, onChange }: { up: UserPropertyList; canUse: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ key: '', cn: '', type: 'string', values: '', source: '', update: 'daily', sensitivity: 'L1' as Level, filterable: true, groupable: false, note: '' });
  const submit = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api.post('/v1/meta/user-properties', { ...f, values: f.values ? f.values.split(/[,，\s]+/).filter(Boolean) : [] }); setOpen(false); await onChange(); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  const low = up.items.filter((p) => p.coverage !== null && p.coverage < 0.8);
  return (
    <Stack gap="md">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">{up.users_total !== null ? `users ${fmtNum(up.users_total)} 列 · 覆蓋率 = ClickHouse 實際` : '未連接 ClickHouse：沒有覆蓋率'} · L4 黑名單 {up.forbidden_l4_count} 欄</Text>
        <Group ml="auto" gap="xs"><TemplateTools kind="user-properties" canUse={canUse} onApplied={onChange} />{canUse && <Button onClick={() => setOpen((v) => !v)}>＋ 登記用戶屬性</Button>}</Group>
      </Group>
      {open && <Paper component="form" onSubmit={submit}><Stack gap="md">
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="屬性名" ff="monospace" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} required />
          <TextInput label="顯示名" value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} />
          <NativeSelect label="型別" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} data={['string', 'number', 'bool', 'enum', 'date', 'datetime'].map((t) => opt(t, t))} />
          <TextInput label="枚舉值（逗號分隔）" value={f.values} onChange={(e) => setF({ ...f, values: e.target.value })} />
          <TextInput label="來源（映射或 profile_* 協議）" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} placeholder="例：crm.cdc" />
          <TextInput label="更新" value={f.update} onChange={(e) => setF({ ...f, update: e.target.value })} />
          <NativeSelect label="敏感等級" value={f.sensitivity} onChange={(e) => setF({ ...f, sensitivity: e.target.value as Level })} data={(['L0', 'L1', 'L2', 'L3'] as Level[]).map((s) => opt(s, `${s} ${SENS_CN[s]}`))} />
          <Checkbox label="可篩選" checked={f.filterable} onChange={(e) => setF({ ...f, filterable: e.currentTarget.checked })} mt="xl" />
          <Checkbox label="可分組" checked={f.groupable} onChange={(e) => setF({ ...f, groupable: e.currentTarget.checked })} mt="xl" />
        </SimpleGrid>
        <Group gap="xs"><Button type="submit" variant="filled">登記</Button><Text size="sm" c="dimmed">登記後標示「待發版」：同步進 user_properties.yaml／dimensions.yaml 需契約 PR；型別與敏感等級由治理審核。</Text></Group>
      </Stack></Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={1000}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>屬性</Table.Th><Table.Th>神策舊名</Table.Th><Table.Th>型別</Table.Th><Table.Th>來源</Table.Th><Table.Th>更新</Table.Th><Table.Th>敏感</Table.Th><Table.Th>篩選</Table.Th><Table.Th>分組</Table.Th><Table.Th ta="right">覆蓋率</Table.Th><Table.Th>狀態</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{up.items.map((p) => <Table.Tr key={p.key}>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{p.key}</Text><Text size="xs" c="dimmed">{p.cn}{p.note && ` · ${p.note}`}{p.row_level_key && ' · 行級權限鍵'}</Text></Table.Td>
              <Table.Td><Text size="xs" c="dimmed" ff="monospace">{p.sa || '—'}</Text></Table.Td>
              <Table.Td><Mono>{p.type}</Mono>{p.values.length > 0 && <Text size="xs" c="dimmed" ff="monospace">{p.values.join(' | ')}</Text>}{p.buckets && <Text size="xs" c="dimmed" ff="monospace">分桶 {p.buckets.join('/')}</Text>}</Table.Td>
              <Table.Td><Text size="xs">{p.source}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{p.update}</Text></Table.Td>
              <Table.Td><StatusBadge tone={SENS_PILL[p.sensitivity]}>{p.sensitivity}</StatusBadge></Table.Td>
              <Table.Td>{p.filterable ? '✓' : '—'}</Table.Td>
              <Table.Td>{p.groupable ? '✓' : '—'}</Table.Td>
              <Table.Td ta="right"><Text span c={p.coverage !== null && p.coverage < 0.8 ? 'red' : undefined}><Mono>{pct(p.coverage, 1)}</Mono></Text></Table.Td>
              <Table.Td>{p.origin === 'catalog' ? <StatusBadge tone="gray">{p.stored ? (p.hot ? '熱欄位' : '語意層') : 'L3 不進寬表'}</StatusBadge> : <StatusBadge tone="amber">待發版</StatusBadge>}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Paper><SectionTitle title="寫入方式" /><Text size="sm" c="dimmed">{up.write_protocol.join('／')}（神策相容協議）；CDC 來源以映射規則寫入；標籤引擎每日覆寫標籤型屬性；事件推導屬性由 Flink 即時更新。</Text></Paper>
        <Paper><SectionTitle title="新增屬性" /><Text size="sm" c="dimmed">在數據整合的「欄位映射」把來源欄位對到用戶屬性即自動登記；型別與敏感等級由治理審核；可篩選／分組會同步到語意層 dimensions.yaml。</Text></Paper>
        <Paper><SectionTitle title="覆蓋率低的屬性" />{low.length ? <List size="sm">{low.map((p) => <List.Item key={p.key}><Mono>{p.key}</Mono> {pct(p.coverage)}</List.Item>)}</List> : <Text size="sm" c="dimmed">{up.stats_source ? '沒有覆蓋率低於 80% 的屬性。' : '未連接 ClickHouse。'}</Text>}</Paper>
      </SimpleGrid>
    </Stack>
  );
}

function OneIdTab({ oi, canAdmin, onChange }: { oi: OneIdRules; canAdmin: boolean; onChange: () => Promise<void> }) {
  const [edit, setEdit] = useState(false); const [ids, setIds] = useState<OneIdRule[]>(oi.ids); const [reason, setReason] = useState(''); const [err, setErr] = useState<string | null>(null);
  useEffect(() => setIds(oi.ids), [oi]);
  const strong = new Set(oi.strong_ids);
  const save = async () => { setErr(null); try { await api.put('/v1/meta/oneid/rules', { ids: ids.map((i) => ({ key: i.key, priority: i.priority, max_count: i.max_count, over_limit: i.over_limit, status: i.status ?? 'enabled' })), reason }); setEdit(false); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const st = oi.stats;
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatTile label="OneID 用戶數（有關聯邊）" value={st.users === null ? '—' : fmtNum(st.users)} hint={oi.stats_source ? 'ClickHouse id_edges' : '未連接 ClickHouse'} />
        <StatTile label="已識別（有強 ID）" value={st.users ? pct(st.identified !== null ? st.identified / st.users : null) : '—'} hint={`${st.identified ?? '—'} 人可對應客戶代碼`} />
        <StatTile label="近 30 天合併／拆分" value={Object.keys(st.merges_30d).length ? Object.entries(st.merges_30d).map(([k, v]) => `${k} ${v}`).join(' · ') : '0'} hint="人工合併需審批；oneid_merge_log" />
        <StatTile label="近 24h 登入改寫" value={st.backfill_24h ?? '—'} hint="近 7 天匿名事件改寫到登入客戶" />
      </SimpleGrid>
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Group p="sm" pb={0} gap="xs" wrap="wrap">
          <Title order={3}>識別類型（對齊神策 IDM 3.0）</Title>
          {oi.override && <StatusBadge tone={oi.override.status === 'pending_review' ? 'amber' : 'blue'}>修改待覆核 · {oi.override.by}</StatusBadge>}
          <Group gap="xs" ml="auto">
            {canAdmin && !edit && <Button size="compact-sm" variant="subtle" onClick={() => setEdit(true)}>修改規則</Button>}
            {edit && <><TextInput aria-label="變更理由" placeholder="變更理由" value={reason} onChange={(e) => setReason(e.target.value)} w={200} /><Button variant="filled" onClick={() => void save()}>送出覆核</Button><Button size="compact-sm" variant="subtle" onClick={() => { setEdit(false); setIds(oi.ids); }}>取消</Button></>}
          </Group>
        </Group>
        <Table.ScrollContainer minWidth={1100}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>優先序</Table.Th><Table.Th>識別類型</Table.Th><Table.Th>強度</Table.Th><Table.Th>數量</Table.Th><Table.Th>改綁／解綁</Table.Th><Table.Th>上限／超限</Table.Th><Table.Th>狀態</Table.Th><Table.Th>來源</Table.Th><Table.Th ta="right">關聯邊 / 用戶</Table.Th><Table.Th>說明</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{ids.map((r, i) => <Table.Tr key={r.key}>
              <Table.Td>{edit && r.key !== '$identity_login_id' ? <TextInput aria-label={`${r.key} 優先序`} type="number" min={2} max={20} value={r.priority} w={70} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, priority: Number(e.target.value) } : x)))} /> : <Mono>{r.priority}</Mono>}</Table.Td>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{r.key}</Text><Text size="xs" c="dimmed">{r.cn}</Text></Table.Td>
              <Table.Td><StatusBadge tone={strong.has(r.key) ? 'blue' : r.participates === false ? 'gray' : 'amber'}>{strong.has(r.key) ? '強' : r.participates === false ? '渠道' : '弱'}</StatusBadge></Table.Td>
              <Table.Td>{r.cardinality === 'single' ? '單值' : '多值'}</Table.Td>
              <Table.Td><Text size="xs">{r.rebind}</Text></Table.Td>
              <Table.Td>{edit && r.cardinality === 'multi' ? <Group gap={4} wrap="nowrap"><TextInput aria-label={`${r.key} 上限`} type="number" value={r.max_count ?? ''} w={70} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, max_count: e.target.value ? Number(e.target.value) : undefined } : x)))} /><NativeSelect aria-label={`${r.key} 超限策略`} value={r.over_limit ?? 'intercept'} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, over_limit: e.target.value } : x)))} data={[opt('intercept', '攔截'), opt('rolling_unbind', '滾動解綁')]} /></Group> : <Text size="xs" ff="monospace">{r.max_count ? `${r.max_count} · ${r.over_limit === 'rolling_unbind' ? '滾動解綁' : '攔截'}` : '—'}</Text>}</Table.Td>
              <Table.Td>{edit && r.key !== '$identity_login_id' ? <NativeSelect aria-label={`${r.key} 狀態`} value={r.status ?? 'enabled'} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, status: e.target.value } : x)))} data={[opt('enabled', '啟用'), opt('disabled', '停用')]} /> : <StatusBadge tone={(r.status ?? 'enabled') === 'enabled' ? 'blue' : 'gray'}>{(r.status ?? 'enabled') === 'enabled' ? '啟用' : '停用'}</StatusBadge>}</Table.Td>
              <Table.Td><Text size="xs">{r.source.join('、')}</Text></Table.Td>
              <Table.Td ta="right"><Mono>{r.stats ? `${r.stats.edges} / ${r.stats.users}` : '—'}</Mono></Table.Td>
              <Table.Td><Text size="xs" style={{ maxWidth: 320 }}>{r.note}</Text></Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper><SectionTitle title="歸戶規則（docs/19 · oneid_rules.yaml）" />
          <List size="sm" spacing="xs">
            <List.Item><b>接入時決定</b>：每筆事件進入 Flink 時依其 identities 決定 one_id；未登入事件以 distinct_id 歸到匿名用戶。</List.Item>
            <List.Item><b>登入歸戶 + 近 {String(oi.login_backfill.window_days ?? 7)} 天匿名行為改寫</b>：登入事件到達時做歸戶，並把同 distinct_id、登入前仍屬匿名用戶的事件改寫到該客戶；更早的事件不動。</List.Item>
            <List.Item><b>一個 ID 同一時刻只屬於一個用戶</b>；兩個已存在的用戶不自動合併（人工合併／拆分需審批與審計）。</List.Item>
            <List.Item><b>資料歸優先級最高的 ID</b>：登入事件永遠以客戶代碼為準；弱 ID 已屬已識別客戶時不自動改綁，記 conflict。</List.Item>
            <List.Item>別名：{Object.entries(oi.alias).map(([a, b]) => `${a} → ${b}`).join('；') || '—'}。</List.Item>
          </List></Paper>
        <Paper><SectionTitle title="修改規則的限制" />
          <List size="sm" spacing="xs">
            <List.Item>只能改優先序、多值上限與超限策略、啟用狀態；識別類型本身與客戶代碼（優先級 1、不可停用）不可動。</List.Item>
            <List.Item>送出後狀態為「待覆核」：稽核核准才生效，並觸發標籤重算；審計 meta.oneid.rules_changed。</List.Item>
            <List.Item>需 governance.admin（資訊管理／系統管理員）。</List.Item>
          </List></Paper>
      </SimpleGrid>
    </Stack>
  );
}

function PrivacyTab({ up }: { up: UserPropertyList }) {
  return (
    <SimpleGrid cols={{ base: 1, md: 2 }}>
      <Paper><SectionTitle title="個資防護（L4 永不入倉）" />
        <Table.ScrollContainer minWidth={420}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>欄位</Table.Th><Table.Th>處理</Table.Th><Table.Th>偵測</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{PRIVACY.map((r) => <Table.Tr key={r[0]}><Table.Td><Text fw={600} size="sm">{r[0]}</Text></Table.Td><Table.Td>{r[1]}</Table.Td><Table.Td><Text size="xs" ff="monospace">{r[2]}</Text></Table.Td></Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <Text size="sm" c="dimmed" mt="sm">黑名單（forbidden_l4）共 {up.forbidden_l4_count} 欄，含客戶神策事件屬性與用戶屬性裡的姓名、手機、信箱、生日、銀行帳號；接入與同步任務一律丟棄並記隔離。</Text></Paper>
      <Stack gap="md">
        <Paper><SectionTitle title="DSAR／刪除權"><StatusBadge tone="gray">第二階段</StatusBadge></SectionTitle><Text size="sm" c="dimmed">客戶要求刪除：以客戶代碼找 one_id → 刪 users／id_map／user_tags → events 以 one_id 改寫為 tombstone（保留匿名統計）→ 下游推送撤回 → 30 天內完成並產出證明。工單流程待第二階段接上審批中心。</Text></Paper>
        <Paper><SectionTitle title="用戶表統計" />
          <KeyValue rows={[
            ['users 列數', <Mono key="u">{up.users_total === null ? '—（未連接 ClickHouse）' : up.users_total.toLocaleString()}</Mono>],
            ['屬性數', <Mono key="n">{up.items.length}</Mono>],
            ['可篩選／可分組', <Mono key="f">{up.items.filter((p) => p.filterable).length} / {up.items.filter((p) => p.groupable).length}</Mono>],
            ['熱欄位（users_dict）', <Mono key="h">{up.items.filter((p) => p.hot).length}</Mono>],
          ]} /></Paper>
      </Stack>
    </SimpleGrid>
  );
}
