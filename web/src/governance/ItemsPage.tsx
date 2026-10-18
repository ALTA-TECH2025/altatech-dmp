// 維度表與字典（對齊神策元數據管理）：維度表（清單、定義、新增、編輯欄位顯示名／敏感等級、立即同步）、維度字典（上傳 CSV append／overwrite）、虛擬屬性（建立）。設計系統元件組合。
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Alert, Badge, Button, Checkbox, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Textarea, UnstyledButton } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { fmtNum } from '@/analytics/series';
import { KeyValue, Mono, PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
import { dimRefs, parseDictCsv, SENS_PILL } from './governance';
import type { Dictionary, DimensionTable, DimensionTableList, Level, VirtualProperty } from './types';

type Tab = 'tables' | 'dicts' | 'virtual';
const opt = (value: string, label: string) => ({ value, label });

export function ItemsPage() {
  return <RequireGrant module="governance"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const canUse = can('governance', 'use');
  const [tab, setTab] = useState<Tab>('tables');
  const [dt, setDt] = useState<DimensionTableList | null>(null); const [dicts, setDicts] = useState<Dictionary[]>([]); const [vps, setVps] = useState<VirtualProperty[]>([]);
  const [cur, setCur] = useState<string>('dim_instrument'); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const reload = async () => { try { const [a, b, c] = await Promise.all([api.get<DimensionTableList>('/v1/meta/dimension-tables'), api.get<Dictionary[]>('/v1/meta/dictionaries'), api.get<VirtualProperty[]>('/v1/meta/virtual-properties')]); setDt(a); setDicts(b); setVps(c); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => { void reload(); }, []);
  const t = dt?.items.find((x) => x.id === cur) ?? dt?.items[0];
  return (
    <div className="page">
      <PageHeader title="維度表與字典" lead={<><b>維度表</b>以「屬性值 = 主鍵」補充事件／用戶沒有埋的維度，欄位成為關聯屬性；<b>維度字典</b>給屬性值顯示名；<b>虛擬屬性</b>以表達式組合既有屬性與維度表欄位。資料由「數據整合 › 同步任務」寫入。</>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}{msg && <Text size="sm" c="dimmed" mb="md">{msg}</Text>}
      <SegmentedControl mb="md" value={tab} onChange={(v) => setTab(v as Tab)} data={[opt('tables', `維度表 ${dt?.items.length ?? ''}`), opt('dicts', `維度字典 ${dicts.length}`), opt('virtual', `虛擬屬性 ${vps.length}`)]} />
      {tab === 'tables' && dt && t && <TablesTab dt={dt} t={t} setCur={setCur} canUse={canUse} onChange={reload} onMsg={setMsg} />}
      {tab === 'dicts' && <DictsTab dicts={dicts} canUse={canUse} onChange={reload} />}
      {tab === 'virtual' && <VirtualTab vps={vps} tables={dt?.items ?? []} canUse={canUse} onChange={reload} setCur={(id) => { setCur(id); setTab('tables'); }} />}
    </div>
  );
}

/** 可點選的統計磚（維度表清單）：選中者加外框。 */
function TileButton({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return <UnstyledButton onClick={onClick} aria-pressed={selected} style={{ display: 'block', width: '100%', textAlign: 'left', borderRadius: 8, outline: selected ? '2px solid var(--brand)' : undefined }}>{children}</UnstyledButton>;
}

function TablesTab({ dt, t, setCur, canUse, onChange, onMsg }: { dt: DimensionTableList; t: DimensionTable; setCur: (id: string) => void; canUse: boolean; onChange: () => Promise<void>; onMsg: (m: string | null) => void }) {
  const [edit, setEdit] = useState(false); const [cols, setCols] = useState(t.columns); const [cn, setCn] = useState(t.cn); const [err, setErr] = useState<string | null>(null); const [open, setOpen] = useState(false);
  const [nf, setNf] = useState({ id: 'dim_', cn: '', primary_key: '', item_type: '', join_table: 'events', join_column: '', source: '', columns: '' });
  useEffect(() => { setCols(t.columns); setCn(t.cn); setEdit(false); }, [t]);
  const save = async () => { setErr(null); try { await api.patch(`/v1/meta/dimension-tables/${t.id}`, { cn, columns: cols }); setEdit(false); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const sync = async () => { try { const r = await api.post<{ accepted: boolean; message: string }>(`/v1/meta/dimension-tables/${t.id}/sync`); onMsg(r.message); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const create = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try { await api.post('/v1/meta/dimension-tables', { id: nf.id, cn: nf.cn, primary_key: nf.primary_key, item_type: nf.item_type || undefined, join: [{ table: nf.join_table, column: nf.join_column }], source: nf.source || undefined, columns: nf.columns.split(/\r?\n/).map((l) => l.split(',').map((x) => x.trim())).filter((p) => p[0]).map((p) => ({ key: p[0], cn: p[1] || p[0], type: p[2] || 'string', sensitivity: (p[3] || 'L1') as Level })) }); setOpen(false); setCur(nf.id); await onChange(); }
    catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); }
  };
  const kv: [ReactNode, ReactNode][] = [
    ['關聯條件', <Text key="j" size="xs" ff="monospace">{t.join.map((j) => `${j.table}.${j.column} = ${t.id}.${j.dim_column ?? t.primary_key}`).join('；')}</Text>],
    ['item_type', <Mono key="i">{t.item_type}</Mono>],
    ['來源', <Group key="s" gap={4}>{t.source || '—'}{t.task && <StatusBadge tone="blue">任務 {t.task.status ?? ''}</StatusBadge>}{t.source && !t.task && <StatusBadge tone="amber">任務未登記</StatusBadge>}</Group>],
    ...(t.row_level ? [['行級', <Mono key="r">{t.row_level}</Mono>] as [ReactNode, ReactNode]] : []),
  ];
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <Group gap="xs" wrap="wrap">
        <Text size="sm" c="dimmed">{dt.stats_source ? '列數 = ClickHouse 實際（表尚未由同步任務建立者顯示「未建」）' : '未連接 ClickHouse：沒有列數'} · 關聯鍵必須唯一；&gt; {fmtNum(Number(dt.rules.max_rows_for_join ?? 1000000))} 列改 dictGet</Text>
        {canUse && <Button ml="auto" onClick={() => setOpen((v) => !v)}>＋ 新增維度表</Button>}
      </Group>
      {open && <Paper component="form" onSubmit={create}><Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="id（dim_ 開頭）" ff="monospace" value={nf.id} onChange={(e) => setNf({ ...nf, id: e.target.value })} required />
          <TextInput label="顯示名" value={nf.cn} onChange={(e) => setNf({ ...nf, cn: e.target.value })} required />
          <TextInput label="主鍵欄位" ff="monospace" value={nf.primary_key} onChange={(e) => setNf({ ...nf, primary_key: e.target.value })} required />
          <TextInput label="item_type" ff="monospace" value={nf.item_type} onChange={(e) => setNf({ ...nf, item_type: e.target.value })} />
          <NativeSelect label="關聯表" value={nf.join_table} onChange={(e) => setNf({ ...nf, join_table: e.target.value })} data={[opt('events', 'events'), opt('users', 'users')]} />
          <TextInput label="關聯屬性（事件／用戶屬性）" ff="monospace" value={nf.join_column} onChange={(e) => setNf({ ...nf, join_column: e.target.value })} required />
          <TextInput label="來源同步任務 id（選填，須先在數據整合建立）" ff="monospace" value={nf.source} onChange={(e) => setNf({ ...nf, source: e.target.value })} />
        </SimpleGrid>
        <Textarea label="欄位（每行：key,顯示名,型別,敏感等級）" autosize minRows={4} ff="monospace" value={nf.columns} onChange={(e) => setNf({ ...nf, columns: e.target.value })} placeholder={'name,名稱,string,L0\ncategory,類型,enum,L0'} />
        <Group gap="xs"><Button type="submit" variant="filled">建立</Button><Text size="sm" c="dimmed">主鍵須唯一；關聯屬性須存在；欄位不得 L4。</Text></Group>
      </Stack></Paper>}
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }}>
        {dt.items.map((d) => <TileButton key={d.id} selected={d.id === t.id} onClick={() => setCur(d.id)}>
          <StatTile label={d.cn} value={<>{d.rows === null ? (d.exists ? '0' : '未建') : fmtNum(d.rows)}<Text span size="xs" fw={400}> 列</Text></>} hint={<Text span size="xs" ff="monospace">{d.id}{d.origin === 'registered' && ' · 待發版'}</Text>} />
        </TileButton>)}
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper>
          <SectionTitle title={edit ? <TextInput aria-label="維度表顯示名" value={cn} onChange={(e) => setCn(e.target.value)} /> : t.cn}><Text size="sm" c="dimmed" ff="monospace">{t.id}</Text></SectionTitle>
          <Text size="xs" c="dimmed" mb="sm">主鍵 {t.primary_key} · {t.rows === null ? '—' : t.rows.toLocaleString()} 列 · 同步 {t.sync || '—'} · {t.engine_effective === 'dictionary' ? 'dictGet' : 'JOIN'}</Text>
          <KeyValue rows={kv} />
          <Table.ScrollContainer minWidth={480} mt="sm">
            <Table>
              <Table.Thead><Table.Tr><Table.Th>欄位</Table.Th><Table.Th>顯示名</Table.Th><Table.Th>型別</Table.Th><Table.Th>敏感</Table.Th><Table.Th>關聯屬性</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{cols.map((c, i) => <Table.Tr key={c.key}>
                <Table.Td><Mono>{c.key}</Mono></Table.Td>
                <Table.Td>{edit ? <TextInput aria-label={`${c.key} 顯示名`} value={c.cn} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, cn: e.target.value } : x)))} /> : c.cn}</Table.Td>
                <Table.Td><Mono>{c.type}</Mono></Table.Td>
                <Table.Td>{edit ? <NativeSelect aria-label={`${c.key} 敏感等級`} value={c.sensitivity} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, sensitivity: e.target.value as Level } : x)))} data={(['L0', 'L1', 'L2', 'L3'] as Level[]).map((s) => opt(s, s))} /> : <StatusBadge tone={SENS_PILL[c.sensitivity]}>{c.sensitivity}</StatusBadge>}</Table.Td>
                <Table.Td>{edit ? <Checkbox aria-label={`${c.key} 曝露為關聯屬性`} checked={c.expose ?? c.exposed ?? true} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, expose: e.currentTarget.checked } : x)))} /> : ((c.expose ?? c.exposed ?? true) ? '✓' : '—')}</Table.Td>
              </Table.Tr>)}</Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {t.note && <Text size="sm" c="dimmed" mt="sm">{t.note}</Text>}
          {canUse && <Group gap="xs" mt="sm">{edit ? <><Button variant="filled" onClick={() => void save()}>儲存</Button><Button onClick={() => { setEdit(false); setCols(t.columns); setCn(t.cn); }}>取消</Button></> : <><Button size="compact-sm" variant="subtle" onClick={() => setEdit(true)}>編輯顯示名／敏感等級</Button><Button size="compact-sm" variant="subtle" onClick={() => void sync()}>立即同步</Button></>}</Group>}
        </Paper>
        <Stack gap="md">
          <Paper><SectionTitle title="用在哪裡" /><List size="sm" spacing="xs">{(t.used_by ?? []).length ? t.used_by!.map((u) => <List.Item key={u}>{u}</List.Item>) : <List.Item>曝露欄位：{t.exposed.map((e) => `${t.id}.${e}`).join('、') || '—'}（事件分析／漏斗可篩選、分組）</List.Item>}</List></Paper>
          <Paper><SectionTitle title="維度表的規則" />
            <List size="sm" spacing="xs">
              <List.Item>維度表用來<b>補充事件／用戶屬性沒有埋的維度</b>：以「屬性值 = 主鍵」關聯，欄位成為可篩選、可分組的關聯屬性；事件仍然屬於用戶。</List.Item>
              <List.Item>關聯鍵必須是主鍵（唯一），否則查詢會放大列數。</List.Item>
              <List.Item>建議行數 &lt; 100 萬；超過時改以 dictGet，不走 JOIN。</List.Item>
              <List.Item>來源由「數據整合 › 同步任務」寫入（upsert by 主鍵）或 item_set 協議上報；欄位有敏感等級，依角色隱碼；敏感等級只能升。</List.Item>
            </List></Paper>
        </Stack>
      </SimpleGrid>
    </Stack>
  );
}

function DictsTab({ dicts, canUse, onChange }: { dicts: Dictionary[]; canUse: boolean; onChange: () => Promise<void> }) {
  const [sel, setSel] = useState<Dictionary | null>(null); const [prop, setProp] = useState(''); const [csv, setCsv] = useState(''); const [mode, setMode] = useState<'append' | 'overwrite'>('append'); const [err, setErr] = useState<string | null>(null);
  const view = async (p: string) => { try { setSel(await api.get<Dictionary>(`/v1/meta/dictionaries/${encodeURIComponent(p)}`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const upload = async (e: FormEvent) => { e.preventDefault(); setErr(null); const entries = parseDictCsv(csv); if (!Object.keys(entries).length) { setErr('沒有可用的「值,顯示名」'); return; } try { await api.put(`/v1/meta/dictionaries/${encodeURIComponent(prop)}?mode=${mode}`, { entries }); setCsv(''); await onChange(); await view(prop); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  return (
    <Stack gap="md">
      {err && <Alert color="red" variant="light">{err}</Alert>}
      <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" className="ds-split">
        <Paper p={0} style={{ overflow: 'hidden' }}>
          <Table.ScrollContainer minWidth={760}>
            <Table>
              <Table.Thead><Table.Tr><Table.Th>屬性</Table.Th><Table.Th>映射</Table.Th><Table.Th ta="right">值數</Table.Th><Table.Th>範例</Table.Th><Table.Th>來源</Table.Th><Table.Th>更新</Table.Th><Table.Th /></Table.Tr></Table.Thead>
              <Table.Tbody>{dicts.map((d) => <Table.Tr key={d.property}>
                <Table.Td><Text fw={600} size="sm" ff="monospace">{d.property}</Text></Table.Td>
                <Table.Td><Badge color="gray">{d.mode === 'n_to_1' ? 'N:1' : '1:1'}</Badge></Table.Td>
                <Table.Td ta="right"><Mono>{d.count}</Mono></Table.Td>
                <Table.Td><Group gap={4}>{d.sample.slice(0, 4).map(([k, v]) => <Badge key={k} color="gray" ff="monospace" fz={10}>{k} → {v}</Badge>)}{d.count > 4 && <Text size="sm" c="dimmed">…</Text>}</Group></Table.Td>
                <Table.Td><Text size="xs">{typeof d.source === 'string' ? (d.source === 'manual_upload' ? '手動上傳' : d.source) : `維度表 ${d.source.dimension_table}.${d.source.column}`}</Text></Table.Td>
                <Table.Td><Text size="xs" ff="monospace">{d.updated_at?.slice(0, 10) ?? '語意層'}</Text></Table.Td>
                <Table.Td ta="right"><Group gap={4} justify="flex-end" wrap="nowrap"><Button size="compact-sm" variant="subtle" onClick={() => void view(d.property)}>查看</Button>{canUse && typeof d.source === 'string' && <Button size="compact-sm" variant="subtle" onClick={() => { setProp(d.property); setMode('append'); }}>上傳</Button>}</Group></Table.Td>
              </Table.Tr>)}</Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
        <Stack gap="md">
          {canUse && <Paper component="form" onSubmit={upload}><Stack gap="md">
            <SectionTitle title="上傳字典" />
            <TextInput label="屬性" ff="monospace" value={prop} onChange={(e) => setProp(e.target.value)} placeholder="例：reason_code（只限文字／數值屬性）" required />
            <Textarea label="CSV（值,顯示名；# 開頭略過）" autosize minRows={6} ff="monospace" value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'E001,餘額不足\nSYS02,委託閘道逾時\nSYS03,委託閘道逾時'} />
            <Group gap="xs"><NativeSelect aria-label="上傳模式" value={mode} onChange={(e) => setMode(e.target.value as 'append' | 'overwrite')} data={[opt('append', '新增（保留既有）'), opt('overwrite', '覆蓋（先清空）')]} /><Button type="submit" variant="filled">上傳</Button></Group>
            <Text size="sm" c="dimmed">N:1 允許（多個代碼同一顯示名）；上傳寫審計 dictionary.uploaded。</Text>
          </Stack></Paper>}
          {sel && <Paper><SectionTitle title={sel.property} right={<Button size="compact-sm" variant="subtle" onClick={() => setSel(null)}>關閉</Button>}><Text size="xs" c="dimmed">{sel.count} 筆 · {sel.mode === 'n_to_1' ? 'N:1' : '1:1'}</Text></SectionTitle>
            <Table.ScrollContainer minWidth={240} maxHeight={320}><Table><Table.Tbody>{Object.entries(sel.entries ?? {}).slice(0, 500).map(([k, v]) => <Table.Tr key={k}><Table.Td><Mono>{k}</Mono></Table.Td><Table.Td>{v}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer></Paper>}
          <Paper><SectionTitle title="字典與維度表的差別" /><Text size="sm" c="dimmed">字典只是顯示名，不新增欄位：篩選與分組時以顯示名呈現並可選，原值仍保留。要補多個維度（產業、類型、區域）用維度表。</Text></Paper>
        </Stack>
      </SimpleGrid>
    </Stack>
  );
}

function VirtualTab({ vps, tables, canUse, onChange, setCur }: { vps: VirtualProperty[]; tables: DimensionTable[]; canUse: boolean; onChange: () => Promise<void>; setCur: (id: string) => void }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ name: 'v.', cn: '', scope: 'event', expr: '', note: '' });
  const refs = dimRefs(f.expr); const unknown = refs.filter((r) => !tables.some((t) => t.id === r));
  const submit = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api.post('/v1/meta/virtual-properties', f); setOpen(false); setF({ name: 'v.', cn: '', scope: 'event', expr: '', note: '' }); await onChange(); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  return (
    <Stack gap="md">
      {canUse && <Group gap="xs"><Button onClick={() => setOpen((v) => !v)}>＋ 建立虛擬屬性</Button></Group>}
      {open && <Paper component="form" onSubmit={submit}><Stack gap="md">
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="名稱（v. 開頭）" ff="monospace" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
          <TextInput label="顯示名" value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} />
          <NativeSelect label="範圍" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} data={[opt('event', '事件'), opt('user', '用戶')]} />
        </SimpleGrid>
        <Textarea label="表達式（ClickHouse 運算式，≤ 1024 字元；維度表用 dictGet('dim_x', '欄位', 關聯屬性)）" autosize minRows={3} ff="monospace" value={f.expr} onChange={(e) => setF({ ...f, expr: e.target.value })} required maxLength={1024} />
        <Group gap="xs"><Text size="sm" c="dimmed">引用維度表：{refs.join('、') || '無'}{unknown.length > 0 && <Text span c="red">（不存在：{unknown.join('、')}）</Text>}</Text><Button type="submit" variant="filled" disabled={unknown.length > 0}>建立</Button></Group>
      </Stack></Paper>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={900}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>虛擬屬性</Table.Th><Table.Th>範圍</Table.Th><Table.Th>表達式</Table.Th><Table.Th>引用維度表</Table.Th><Table.Th>說明</Table.Th><Table.Th>來源</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{vps.map((v) => <Table.Tr key={v.name}>
              <Table.Td><Text fw={600} size="sm" ff="monospace">{v.name}</Text><Text size="xs" c="dimmed">{v.cn}</Text></Table.Td>
              <Table.Td><Badge color="gray">{v.scope === 'event' ? '事件' : '用戶'}</Badge></Table.Td>
              <Table.Td><Text size="xs" ff="monospace" style={{ maxWidth: 380, overflowWrap: 'anywhere' }}>{v.expr}</Text></Table.Td>
              <Table.Td><Group gap={4}>{v.uses.map((u) => <Badge key={u} component="a" href="#" color="brand" style={{ cursor: 'pointer' }} onClick={(e) => { e.preventDefault(); setCur(u); }}>{u}</Badge>)}{v.uses.length === 0 && '—'}</Group></Table.Td>
              <Table.Td><Text size="xs">{v.note}</Text></Table.Td>
              <Table.Td>{v.origin === 'catalog' ? <StatusBadge tone="gray">語意層</StatusBadge> : <StatusBadge tone="amber">待發版</StatusBadge>}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Text size="sm" c="dimmed">虛擬屬性以表達式組合事件／用戶屬性與維度表欄位，查詢時計算、不落地；名稱不可與既有屬性重複；可用於篩選、分組與標籤定義。</Text>
    </Stack>
  );
}
