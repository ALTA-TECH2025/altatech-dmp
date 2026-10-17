// 總覽／儀表板：/overview 列表（我的、分享給我的）與新增；/overview/:id 卡片網格（每張以檢視者身分重算）、區間切換、分享、改名、移除卡片、刪除、拖曳排序與寬度；看板進階（解讀／HTML 看板／定時寄送）。設計系統元件組合。
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Alert, Anchor, Badge, Button, Group, NativeSelect, Paper, SimpleGrid, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { CardView } from './CardView';
import { SharePanel } from './SharePanel';
import { KIND_CN, PRESET_CN } from './dashboards';
import type { Bookmark, Dashboard, DashboardSummary, Preset } from './types';

const ROLES: [string, string][] = [['admin', '系統管理員'], ['marketing', '行銷'], ['data_analyst', '數據分析'], ['digital_ops', '數位經營'], ['it_admin', '資訊管理']];
const PRESET_OPTS = (Object.keys(PRESET_CN) as Preset[]).map((p) => ({ value: p, label: PRESET_CN[p] }));
/** 多選的原生 select：拿掉單行高度與下拉箭頭。 */
const MULTI = { input: { height: 'auto', minHeight: 74, paddingBlock: 4 } };

export function DashboardsPage() {
  const { id } = useParams();
  return <RequireGrant module="overview">{id ? <DashboardDetail id={id} /> : <DashboardList />}</RequireGrant>;
}

function DashboardList() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [list, setList] = useState<DashboardSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [preset, setPreset] = useState<Preset>('30d');
  const load = () => api.get<DashboardSummary[]>('/v1/dashboards').then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void load(); }, []);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    try { const d = await api.post<Dashboard>('/v1/dashboards', { name: name.trim(), time: { preset } }); nav(`/overview/${d.id}`); }
    catch (ex) { setErr(ex instanceof ApiError ? ex.message : '建立失敗'); }
  };
  const mine = list?.filter((d) => d.mine) ?? []; const shared = list?.filter((d) => !d.mine) ?? [];
  return (
    <div className="page">
      <PageHeader title="總覽" lead="把事件、漏斗、留存、路徑、歸因的查詢存成卡片組成儀表板。卡片存的是條件：每位檢視者打開時以自己的權限重算，分享不會繞過遮罩與行級範圍。" />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {can('overview', 'use') && (
        <Paper component="form" onSubmit={create} aria-label="新增儀表板" mb="md">
          <SectionTitle title="新增儀表板" />
          <Group gap="xs">
            <TextInput aria-label="名稱" placeholder="名稱，例：債券業務日報" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} style={{ flex: 1 }} />
            <NativeSelect aria-label="區間" value={preset} data={PRESET_OPTS} onChange={(e) => setPreset(e.target.value as Preset)} />
            <Button type="submit" variant="filled" disabled={!name.trim()}>建立</Button>
          </Group>
          <Text size="sm" c="dimmed" mt="sm">看板由書籤組成：到各分析頁查詢後按「存為書籤」並加入看板，或在看板裡「＋ 加入書籤」。<Anchor component={Link} to="/bookmarks" size="sm">管理書籤</Anchor></Text>
        </Paper>
      )}
      {[['我的儀表板', mine], ['分享給我的', shared]].map(([h, ds]) => (
        <Paper mb="md" key={h as string}>
          <SectionTitle title={h as string}><Text size="xs" c="dimmed">{(ds as DashboardSummary[]).length} 個</Text></SectionTitle>
          {list === null ? <Text size="sm" c="dimmed">載入中…</Text> : (ds as DashboardSummary[]).length === 0 ? <EmptyState>{h === '我的儀表板' ? '還沒有儀表板。' : '沒有人分享儀表板給你。'}</EmptyState> : (
            <Table.ScrollContainer minWidth={720}>
              <Table>
                <Table.Thead><Table.Tr><Table.Th>名稱</Table.Th><Table.Th ta="right">卡片</Table.Th><Table.Th>區間</Table.Th><Table.Th>擁有者</Table.Th><Table.Th>分享</Table.Th><Table.Th>更新</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{(ds as DashboardSummary[]).map((d) => (
                  <Table.Tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => nav(`/overview/${d.id}`)}>
                    <Table.Td><Group gap={6}><Anchor component={Link} to={`/overview/${d.id}`} size="sm" fw={600} onClick={(e) => e.stopPropagation()}>{d.name}</Anchor>{d.seed && <Badge color="gray">範例</Badge>}</Group>{d.description && <Text size="xs" c="dimmed">{d.description}</Text>}</Table.Td>
                    <Table.Td ta="right"><Mono>{d.cards}</Mono></Table.Td><Table.Td>{PRESET_CN[d.time.preset]}</Table.Td><Table.Td><Mono>{d.owner}</Mono></Table.Td>
                    <Table.Td><Group gap={4}>{d.shared.roles.map((r) => <StatusBadge tone="blue" key={r}>{ROLES.find(([k]) => k === r)?.[1] ?? r}</StatusBadge>)}{d.shared.users.map((u) => <Badge color="gray" key={u}>{u}</Badge>)}</Group></Table.Td>
                    <Table.Td><Mono>{d.updated_at.slice(0, 16).replace('T', ' ')}</Mono></Table.Td>
                  </Table.Tr>))}</Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Paper>
      ))}
    </div>
  );
}

function DashboardDetail({ id }: { id: string }) {
  const { me } = useAuth();
  const nav = useNavigate();
  const [d, setD] = useState<Dashboard | null>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [share, setShare] = useState(false);
  const [adv, setAdv] = useState(false);
  const [roles, setRoles] = useState<string[]>([]); const [users, setUsers] = useState('');
  const [drag, setDrag] = useState<string | null>(null); const [over, setOver] = useState<string | null>(null);
  const [bms, setBms] = useState<Bookmark[] | null>(null); const [bmPick, setBmPick] = useState('');
  const openBm = async () => { const l = await api.get<Bookmark[]>('/v1/bookmarks'); setBms(l); setBmPick(l[0]?.id ?? ''); };
  const addBm = async () => { if (!bmPick) return; try { await api.post(`/v1/dashboards/${id}/cards`, { bookmark: bmPick }); setD(await api.get<Dashboard>(`/v1/dashboards/${id}`)); setBms(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '加入失敗'); } };
  useEffect(() => { api.get<Dashboard>(`/v1/dashboards/${id}`).then((x) => { setD(x); setRoles(x.shared.roles); setUsers(x.shared.users.join(', ')); }).catch((e: Error) => setErr(e.message)); }, [id]);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch(() => setCat(null)); }, []);
  const labelOf = useMemo(() => { const m = new Map((cat?.events ?? []).map((e) => [e.name, e.cn])); return (n: string) => m.get(n) ?? n; }, [cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const put = async (body: Record<string, unknown>) => { try { setD(await api.put<Dashboard>(`/v1/dashboards/${id}`, body)); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '更新失敗'); } };
  if (err && !d) return <div className="page"><Alert color="red" variant="light">{err}</Alert><Text size="sm" c="dimmed" mt="sm"><Anchor component={Link} to="/overview" size="sm">回總覽</Anchor></Text></div>;
  if (!d) return <div className="page"><Text size="sm" c="dimmed">載入中…</Text></div>;
  return (
    <div className="page">
      <PageHeader title={d.name}
        lead={<>{d.description || `擁有者 ${d.owner}`}{d.shared.roles.length > 0 && ` · 分享給 ${d.shared.roles.map((r) => ROLES.find(([x]) => x === r)?.[1] ?? r).join('、')}`}{d.shared.users.length > 0 && ` · ${d.shared.users.length} 位使用者`}</>}
        actions={<>
          <NativeSelect value={d.time.preset} aria-label="區間" data={PRESET_OPTS} onChange={(e) => (d.editable ? void put({ time: { preset: e.target.value } }) : setD({ ...d, time: { preset: e.target.value as Preset } }))} />
          <Button onClick={() => setReload((n) => n + 1)}>重新整理</Button>
          {d.editable && <>
            <Button onClick={() => { const n = prompt('儀表板名稱', d.name); if (n && n.trim()) void put({ name: n.trim() }); }}>改名</Button>
            <Button onClick={() => setShare((v) => !v)}>分享</Button>
            <Button onClick={() => void openBm()}>＋ 加入書籤</Button>
            <Button color="red" onClick={() => { if (confirm(`刪除「${d.name}」？卡片會一起刪除。`)) api.del(`/v1/dashboards/${id}`).then(() => nav('/overview')).catch((e: Error) => setErr(e.message)); }}>刪除</Button>
          </>}
          <Button onClick={() => setAdv((v) => !v)}>解讀 / HTML 看板 / 定時寄送</Button>
          <Button component={Link} to="/overview">所有儀表板</Button>
        </>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {bms && d.editable && (
        <Paper mb="md">
          <SectionTitle title="加入書籤" right={<Button size="compact-sm" variant="subtle" onClick={() => setBms(null)}>關閉</Button>}><Text size="xs" c="dimmed">書籤 = 分析頁存下的查詢；卡片會跟著書籤更新</Text></SectionTitle>
          <Group gap="xs">
            <NativeSelect aria-label="書籤" value={bmPick} data={bms.map((b) => ({ value: b.id, label: `${b.name}（${KIND_CN[b.kind]}）` }))} onChange={(e) => setBmPick(e.target.value)} style={{ flex: 1 }} />
            <Button variant="filled" disabled={!bmPick} onClick={() => void addBm()}>加入</Button>
            {bms.length === 0 && <Text size="xs" c="dimmed">還沒有書籤：到分析頁查詢後按「存為書籤」。</Text>}
            <Button component={Link} to="/bookmarks">管理書籤</Button>
          </Group>
        </Paper>)}
      {adv && <SharePanel d={d} onClose={() => setAdv(false)} onChanged={() => void api.get<Dashboard>(`/v1/dashboards/${id}`).then(setD)} />}
      {share && d.editable && (
        <Paper mb="md">
          <SectionTitle title="分享"><Text size="xs" c="dimmed">被分享者只能檢視；卡片資料以他們自己的權限重算</Text></SectionTitle>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <NativeSelect multiple label="角色" value={roles} styles={MULTI} rightSection={<span />} onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))}>{ROLES.map(([k2, cn]) => <option key={k2} value={k2}>{cn}</option>)}</NativeSelect>
            <TextInput label="使用者 ID（逗號分隔，例：local:ab12…、entra:oid）" value={users} onChange={(e) => setUsers(e.target.value)} />
          </SimpleGrid>
          <Group gap="xs" mt="sm"><Button variant="filled" onClick={() => void put({ shared: { roles, users: users.split(/[,\s，、]+/).map((x) => x.trim()).filter(Boolean) } }).then(() => setShare(false))}>儲存分享設定</Button></Group>
        </Paper>
      )}
      {d.cards.length === 0 ? <Paper><Text size="sm" c="dimmed">還沒有卡片。按「＋ 加入書籤」挑既有書籤，或到<Anchor component={Link} to="/events" size="sm">事件分析</Anchor>、<Anchor component={Link} to="/funnel" size="sm">漏斗</Anchor>、<Anchor component={Link} to="/retention" size="sm">留存</Anchor>、<Anchor component={Link} to="/path" size="sm">路徑</Anchor>或<Anchor component={Link} to="/attribution" size="sm">歸因</Anchor>查詢後按「存為書籤」並加入這個看板。</Text></Paper> : (
        <div className="dash-grid">
          {d.cards.map((c) => (
            <CardView key={c.id} dash={d} card={c} k={k} labelOf={labelOf} reloadKey={reload}
              onRename={d.editable ? () => { const t = prompt('卡片標題', c.title); if (t && t.trim()) void put({ cards: d.cards.map((x) => (x.id === c.id ? { id: x.id, title: t.trim() } : { id: x.id })) }); } : undefined}
              onWidth={d.editable ? () => void put({ cards: d.cards.map((x) => ({ id: x.id, ...(x.id === c.id ? { width: x.width === 2 ? 1 : 2 } : {}) })) }) : undefined}
              onRemove={d.editable ? () => { if (confirm(`移除卡片「${c.title}」？`)) api.del(`/v1/dashboards/${id}/cards/${c.id}`).then(() => setD({ ...d, cards: d.cards.filter((x) => x.id !== c.id) })).catch((e: Error) => setErr(e.message)); } : undefined}
              rootProps={d.editable ? {
                draggable: true, title: '拖曳可排序',
                onDragStart: (e) => { setDrag(c.id); e.dataTransfer.effectAllowed = 'move'; },
                onDragOver: (e) => { e.preventDefault(); if (over !== c.id) setOver(c.id); },
                onDragLeave: () => setOver((o) => (o === c.id ? null : o)),
                onDrop: (e) => { e.preventDefault(); const from = drag; setDrag(null); setOver(null); if (!from || from === c.id) return; const ids = d.cards.map((x) => x.id); const next = ids.filter((x) => x !== from); next.splice(next.indexOf(c.id), 0, from); void put({ cards: next.map((x) => ({ id: x })) }); },
                onDragEnd: () => { setDrag(null); setOver(null); },
                className: `dash-card w${c.width}${drag === c.id ? ' dragging' : ''}${over === c.id && drag !== c.id ? ' dropzone' : ''}`,
              } : undefined} />
          ))}
        </div>
      )}
      <Text size="sm" c="dimmed" mt="sm">卡片種類：{d.cards.map((c) => KIND_CN[c.kind]).filter((v, i, a) => a.indexOf(v) === i).join('、') || '—'}；每張卡片的 SQL 與血緣可按「SQL」查看，查詢都寫入審計。{d.editable && '拖曳卡片可排序，「全寬／½ 寬」切換寬度。'}</Text>
    </div>
  );
}
