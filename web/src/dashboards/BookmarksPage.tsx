// 書籤：我的 + 分享給我的；依分析類型篩選；在分析頁開啟（帶查詢重算）、加入看板、改名／說明／分享、刪除；顯示被哪些看板引用。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Alert, Anchor, Badge, Button, Group, NativeSelect, Paper, SegmentedControl, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { KIND_CN } from './dashboards';
import type { Bookmark, CardKind, DashboardSummary } from './types';

const PATH: Record<CardKind, string> = { events: '/events', funnel: '/funnel', retention: '/retention', path: '/path', attribution: '/attribution', sql: '/sql' };
const ROLES: Record<string, string> = { admin: '系統管理員', marketing: '行銷', data_analyst: '數據分析', digital_ops: '數位經營', it_admin: '資訊管理' };

export function BookmarksPage() {
  return <RequireGrant module="overview"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [list, setList] = useState<Bookmark[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [kind, setKind] = useState<'' | CardKind>('');
  const [q, setQ] = useState('');
  const [pick, setPick] = useState<Bookmark | null>(null);
  const [dash, setDash] = useState<DashboardSummary[] | null>(null);
  const [target, setTarget] = useState('');
  const reload = () => api.get<Bookmark[]>('/v1/bookmarks').then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, []);
  const rows = useMemo(() => (list ?? []).filter((b) => (!kind || b.kind === kind) && (!q || b.name.includes(q) || b.description.includes(q))), [list, kind, q]);
  const openPicker = async (b: Bookmark) => { setPick(b); const ds = (await api.get<DashboardSummary[]>('/v1/dashboards')).filter((d) => d.editable); setDash(ds); setTarget(ds[0]?.id ?? ''); };
  const addTo = async () => {
    if (!pick || !target) return;
    try { await api.post(`/v1/dashboards/${target}/cards`, { bookmark: pick.id }); setMsg(`已把「${pick.name}」加入看板`); setPick(null); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '加入失敗'); }
  };
  const rename = async (b: Bookmark) => { const n = prompt('書籤名稱', b.name); if (!n?.trim()) return; try { await api.put(`/v1/bookmarks/${b.id}`, { name: n.trim() }); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '更新失敗'); } };
  const del = async (b: Bookmark) => { if (!confirm(`刪除書籤「${b.name}」？引用它的看板卡片會保留刪除前的查詢。`)) return; try { await api.del(`/v1/bookmarks/${b.id}`); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '刪除失敗'); } };
  const mine = rows.filter((b) => b.mine); const shared = rows.filter((b) => !b.mine);
  // SegmentedControl 的值不能是空字串：「全部」用 all 代表
  const kindOpts = [{ value: 'all', label: `全部 ${list?.length ?? 0}` }, ...(Object.keys(KIND_CN) as CardKind[]).map((k) => ({ value: k, label: `${KIND_CN[k]} ${(list ?? []).filter((b) => b.kind === k).length}` }))];
  return (
    <div className="page">
      <PageHeader title="書籤" lead="分析頁的查詢「存為書籤」後在這裡管理：重新開啟、分享、加入一或多個看板；書籤改了，引用它的看板卡片一起更新。每位檢視者打開時以自己的權限重算。"
        actions={<Button component={Link} to="/overview">看板</Button>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}{msg && <Alert color="teal" variant="light" mb="md">{msg}</Alert>}
      <Group gap="xs" mb="sm">
        <TextInput aria-label="搜尋書籤" placeholder="搜尋名稱／說明" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 220px' }} />
        <SegmentedControl size="xs" value={kind || 'all'} onChange={(v) => setKind(v === 'all' ? '' : (v as CardKind))} data={kindOpts} />
      </Group>
      {pick && <Paper mb="sm">
        <SectionTitle title={`把「${pick.name}」加入看板`} right={<Button size="compact-sm" variant="subtle" onClick={() => setPick(null)}>關閉</Button>} />
        <Group gap="xs"><NativeSelect aria-label="看板" value={target} data={(dash ?? []).map((d) => ({ value: d.id, label: `${d.name}（${d.cards} 張）` }))} onChange={(e) => setTarget(e.target.value)} /><Button variant="filled" disabled={!target} onClick={() => void addTo()}>加入</Button>{dash && dash.length === 0 && <Text size="xs" c="dimmed">沒有可編輯的看板，先到<Anchor component={Link} to="/overview" size="xs">總覽</Anchor>建一個。</Text>}</Group>
      </Paper>}
      {[['我的書籤', mine], ['分享給我的', shared]].map(([h, bs]) => (
        <Paper mb="md" key={h as string}>
          <SectionTitle title={h as string}><Text size="xs" c="dimmed">{(bs as Bookmark[]).length} 個</Text></SectionTitle>
          {list === null ? <Text size="sm" c="dimmed">載入中…</Text> : (bs as Bookmark[]).length === 0 ? <EmptyState>{h === '我的書籤' ? '還沒有書籤。到事件／漏斗／留存／路徑／歸因分析查詢後按「存為書籤」。' : '沒有人分享書籤給你。'}</EmptyState> : (
            <Table.ScrollContainer minWidth={820}>
              <Table>
                <Table.Thead><Table.Tr><Table.Th>書籤</Table.Th><Table.Th>類型</Table.Th><Table.Th>分享</Table.Th><Table.Th>用於看板</Table.Th><Table.Th>更新</Table.Th><Table.Th /></Table.Tr></Table.Thead>
                <Table.Tbody>{(bs as Bookmark[]).map((b) => (
                  <Table.Tr key={b.id}>
                    <Table.Td><Text fw={600} size="sm">{b.name}</Text>{b.description && <Text size="xs" c="dimmed">{b.description}</Text>}<Text size="xs" c="dimmed" ff="monospace">{b.id} · {b.owner}</Text></Table.Td>
                    <Table.Td><StatusBadge tone="blue">{KIND_CN[b.kind]}</StatusBadge></Table.Td>
                    <Table.Td><Group gap={4}>{b.shared.roles.map((r) => <Badge key={r} color="gray">{ROLES[r] ?? r}</Badge>)}{b.shared.users.length > 0 && <Badge color="gray">{b.shared.users.length} 位</Badge>}{!b.shared.roles.length && !b.shared.users.length && <Text size="xs" c="dimmed">私有</Text>}</Group></Table.Td>
                    <Table.Td>{b.used_in.length ? <Group gap={4}>{b.used_in.map((u) => <Badge key={u.dashboard} component={Link} to={`/overview/${u.dashboard}`} color="violet" style={{ cursor: 'pointer' }}>{u.name}</Badge>)}</Group> : <Text size="xs" c="dimmed">—</Text>}</Table.Td>
                    <Table.Td><Mono>{b.updated_at.slice(0, 16).replace('T', ' ')}</Mono></Table.Td>
                    <Table.Td ta="right"><Group gap={4} justify="flex-end" wrap="nowrap"><Button size="compact-sm" variant="subtle" onClick={() => nav(PATH[b.kind], { state: { bookmark: b } })}>開啟</Button>{can('overview', 'use') && <Button size="compact-sm" variant="subtle" onClick={() => void openPicker(b)}>加入看板</Button>}{b.editable && <><Button size="compact-sm" variant="subtle" onClick={() => void rename(b)}>改名</Button><Button size="compact-sm" variant="subtle" color="red" onClick={() => void del(b)}>刪除</Button></>}</Group></Table.Td>
                  </Table.Tr>))}</Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Paper>
      ))}
    </div>
  );
}
