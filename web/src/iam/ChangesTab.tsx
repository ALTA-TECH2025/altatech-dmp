// 權限中心 › 變更審核：自訂角色變更（前後快照）與臨時提權申請；審批動作走 /v1/approvals/{id}/decide（不可自審）。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Alert, Button, Group, Paper, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { ACTIONS, MODULES, type ChangeRow, type Role } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { EmptyState, KeyValue, Mono, SectionTitle, StatusBadge } from '@/ds';

const ST: Record<ChangeRow['status'], [string, string]> = { pending: ['待複核', 'amber'], approved: ['已生效', 'blue'], rejected: ['已退回', 'red'], expired: ['已逾期', 'gray'] };
const OP: Record<NonNullable<ChangeRow['op']>, string> = { create: '新增角色', update: '修改角色', delete: '刪除角色' };
const fmt = (t?: number) => (t ? new Date(t * 1000).toLocaleString('zh-TW', { hour12: false }) : '—');
const WARN_BG = 'var(--mantine-color-orange-light)';

export function ChangesTab() {
  const { me } = useAuth();
  const [rows, setRows] = useState<ChangeRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const reload = () => api.get<ChangeRow[]>('/v1/iam/changes').then((r) => { setRows(r); setErr(null); }).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, []);
  const canReview = (me?.roles ?? []).some((r) => r.active && (r.key === 'admin' || r.key === 'digital_ops'));
  const decide = async (c: ChangeRow, approve: boolean) => {
    if (!c.approval) return;
    try { await api.post(`/v1/approvals/${c.approval}/decide`, { approve, note: notes[c.id] ?? '' }); setMsg(approve ? '已核可，變更已生效（審計 role.changed／iam.assignment.created）' : '已退回'); await reload(); } catch (e) { setMsg(e instanceof ApiError ? e.message : '操作失敗'); }
  };
  if (err) return <Alert color="red" variant="light">{err}</Alert>;
  if (!rows) return <Text size="sm" c="dimmed" ta="center" py="xl">載入中…</Text>;
  return (
    <Stack gap="md">
      {msg && <Text size="sm" c="dimmed">{msg}</Text>}
      <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={860}><Table>
        <Table.Thead><Table.Tr><Table.Th>申請</Table.Th><Table.Th>內容</Table.Th><Table.Th>申請者</Table.Th><Table.Th>時間</Table.Th><Table.Th>狀態</Table.Th><Table.Th>複核</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{rows.length === 0 && <Table.Tr><Table.Td colSpan={6}><EmptyState>尚無變更申請。自訂角色的建立／修改／刪除與臨時提權都會先出現在這裡，由另一位系統管理員或數位經營複核。</EmptyState></Table.Td></Table.Tr>}
          {rows.map((c) => (
            <Table.Tr key={c.id} bg={c.status === 'pending' ? WARN_BG : undefined}>
              <Table.Td><StatusBadge tone={c.kind === 'break_glass' ? 'red' : 'custom'}>{c.kind === 'break_glass' ? '臨時提權' : OP[c.op ?? 'update']}</StatusBadge><Text size="xs" c="dimmed" ff="monospace">{c.id}</Text></Table.Td>
              <Table.Td>{c.kind === 'break_glass' ? <><Text size="sm"><b>{c.user_id}</b> → admin {c.hours} 小時</Text><Text size="xs" c="dimmed">{c.reason}</Text></> : <><Text fw={600} size="sm">{(c.after ?? c.before)?.name}</Text><Text size="xs" c="dimmed" ff="monospace">{c.role}{c.reassign_to ? ` · 改派 ${c.reassign_to}` : ''}</Text>{c.before || c.after ? <Button size="compact-sm" variant="subtle" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? '收合' : '前後快照'}</Button> : null}</>}</Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{c.requested_by}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{fmt(c.created_at)}</Text></Table.Td>
              <Table.Td><StatusBadge tone={ST[c.status][1]}>{ST[c.status][0]}</StatusBadge></Table.Td>
              <Table.Td>{c.status === 'pending' ? (canReview && c.requested_by !== me?.user.id ? (
                <Stack gap="xs"><TextInput aria-label={`意見 ${c.id}`} placeholder="意見（退回必填）" value={notes[c.id] ?? ''} onChange={(e) => setNotes({ ...notes, [c.id]: e.target.value })} />
                  <Group gap="xs"><Button variant="filled" onClick={() => void decide(c, true)}>核可</Button><Button color="red" disabled={!(notes[c.id] ?? '').trim()} onClick={() => void decide(c, false)}>退回</Button></Group></Stack>
              ) : <Text size="xs" c="dimmed">{c.requested_by === me?.user.id ? '不可自審：等另一位系統管理員或數位經營' : '需系統管理員或數位經營'}</Text>) : <Text size="xs" c="dimmed">{c.decided_by ?? ''} · {fmt(c.decided_at)}{c.note ? ` · ${c.note}` : ''}</Text>}</Table.Td>
            </Table.Tr>))}</Table.Tbody>
      </Table></Table.ScrollContainer></Paper>
      {open && rows.filter((c) => c.id === open).map((c) => <Diff key={c.id} before={c.before ?? null} after={c.after ?? null} />)}
      <Text size="sm" c="dimmed">雙人複核設定在 backend/security/rbac.yaml › custom_roles.second_review（審批角色 admin、digital_ops；逾期 7 天）；審批單也會出現在「審批中心」。</Text>
    </Stack>
  );
}

export function Diff({ before, after }: { before: Role | null; after: Role | null }) {
  const b = before?.grants ?? {}; const a = after?.grants ?? {};
  const changed = MODULES.filter((m) => JSON.stringify([...(b[m.key] ?? [])].sort()) !== JSON.stringify([...(a[m.key] ?? [])].sort()));
  const meta: [string, unknown, unknown][] = [['名稱', before?.name, after?.name], ['範圍', before?.scope, after?.scope], ['回溯天數', before?.lookback_days ?? 0, after?.lookback_days ?? 0], ['說明', before?.description, after?.description]].filter(([, x, y]) => x !== y) as [string, unknown, unknown][];
  return (
    <Paper>
      <Stack gap="sm">
        <SectionTitle title="變更前後"><Text size="xs" c="dimmed">{before ? '' : '新增；'}{after ? '' : '刪除；'}{changed.length} 個模組權限異動</Text></SectionTitle>
        {meta.length > 0 && <KeyValue rows={meta.map(([k, x, y]) => [k, <Text size="sm" key={k}><Text component="s" c="dimmed" span>{String(x ?? '—')}</Text> → <b>{String(y ?? '—')}</b></Text>])} />}
        {changed.length > 0 && <Table.ScrollContainer minWidth={480}><Table><Table.Thead><Table.Tr><Table.Th>模組</Table.Th><Table.Th>變更前</Table.Th><Table.Th>變更後</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{changed.map((m) => <Table.Tr key={m.key}><Table.Td>{m.cn}</Table.Td><Table.Td><Mono>{(b[m.key] ?? []).map((x) => ACTIONS.find((y) => y.key === x)?.cn ?? x).join('、') || '—'}</Mono></Table.Td><Table.Td><Mono><b>{(a[m.key] ?? []).map((x) => ACTIONS.find((y) => y.key === x)?.cn ?? x).join('、') || '—'}</b></Mono></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>}
      </Stack>
    </Paper>
  );
}
