// 權限中心 › 成員（對應神策「成員管理」）：登入審計看到的 SSO 使用者 ∪ 本機帳號 ∪ 本地指派；本機帳號可新增／停用／重設密碼／重設 TOTP／刪除（需 iam.admin）。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Code, Group, List, MultiSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Member, MemberDetail, MemberStatus, Role } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { KeyValue, Mono, SectionTitle, StatusBadge } from '@/ds';

const STATUS: Record<MemberStatus, [string, string]> = { active: ['啟用', 'blue'], disabled: ['停用', 'gray'], locked: ['鎖定', 'red'], expired: ['到期', 'amber'], never_logged_in: ['未登入', 'gray'], no_role: ['無角色', 'amber'] };
const fmt = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString('zh-TW', { hour12: false }) : '—');
const WARN_BG = 'var(--mantine-color-orange-light)';

export function MembersTab() {
  const { can } = useAuth();
  const editable = can('iam', 'admin');
  const [rows, setRows] = useState<Member[] | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [src, setSrc] = useState<'all' | 'sso' | 'local'>('all');
  const [open, setOpen] = useState<MemberDetail | null>(null);
  const [draft, setDraft] = useState<{ username: string; password: string; roles: string[]; name: string; email: string } | null>(null);
  const [otp, setOtp] = useState<string | null>(null);
  const reload = () => api.get<Member[]>('/v1/iam/users').then((r) => { setRows(r); setErr(null); }).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); void api.get<Role[]>('/v1/iam/roles').then(setRoles).catch(() => undefined); }, []);
  const list = useMemo(() => (rows ?? []).filter((m) => (src === 'all' || m.source === src) && (!q || [m.id, m.username, m.name, m.email, ...m.role_cn].some((x) => x.includes(q)))), [rows, q, src]);
  const run = async (f: () => Promise<unknown>, ok: string) => { try { await f(); setMsg(ok); await reload(); if (open) setOpen(await api.get<MemberDetail>(`/v1/iam/users/${open.id}`)); } catch (e) { setMsg(e instanceof ApiError ? e.message : '操作失敗'); } };
  const create = () => draft && run(async () => { const r = await api.post<Member & { otpauth: string }>('/v1/iam/users', draft); setOtp(r.otpauth); setDraft(null); }, '已建立本機帳號（審計 iam.user.created）；請把 TOTP 設定交給帳號持有人');
  const localRoles = roles.filter((r) => r.key !== 'admin');
  if (err) return <Alert color="red" variant="light">{err}</Alert>;
  if (!rows) return <Text size="sm" c="dimmed" ta="center" py="xl">載入中…</Text>;
  const counts = { all: rows.length, sso: rows.filter((m) => m.source === 'sso').length, local: rows.filter((m) => m.source === 'local').length };
  return (
    <Stack gap="md">
      {msg && <Text size="sm" c="dimmed">{msg}</Text>}
      {otp && <Paper><SectionTitle title="TOTP 設定（只顯示這一次）" right={<Button size="compact-sm" variant="subtle" onClick={() => setOtp(null)}>關閉</Button>} /><Code block style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{otp}</Code><Text size="sm" c="dimmed" mt="xs">用驗證器 App 掃描或手動輸入 secret；之後登入需密碼 + 6 位數。</Text></Paper>}
      <Group gap="xs">
        <TextInput aria-label="篩選成員" placeholder="篩選身分／姓名／信箱／角色" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 240px' }} />
        <SegmentedControl size="xs" value={src} onChange={(v) => setSrc(v as typeof src)} data={[{ value: 'all', label: `全部 ${counts.all}` }, { value: 'sso', label: `公司帳號（SSO） ${counts.sso}` }, { value: 'local', label: `本機帳號 ${counts.local}` }]} />
        {editable && <Button variant="filled" onClick={() => setDraft({ username: '', password: '', roles: [], name: '', email: '' })}>＋ 本機帳號</Button>}
      </Group>
      <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={1000}><Table>
        <Table.Thead><Table.Tr><Table.Th>成員</Table.Th><Table.Th>來源</Table.Th><Table.Th>角色</Table.Th><Table.Th>狀態</Table.Th><Table.Th>MFA</Table.Th><Table.Th>最近登入</Table.Th><Table.Th ta="right">失敗登入 7 天</Table.Th><Table.Th ta="right">session</Table.Th><Table.Th ta="right">指派</Table.Th><Table.Th /></Table.Tr></Table.Thead>
        <Table.Tbody>{list.map((m) => (
          <Table.Tr key={m.id} bg={m.status === 'locked' || m.login_failed_7d >= 3 ? WARN_BG : undefined}>
            <Table.Td><Text fw={600} size="sm">{m.name || m.username}</Text><Text size="xs" c="dimmed" ff="monospace">{m.username} · {m.id}{m.email ? ` · ${m.email}` : ''}</Text></Table.Td>
            <Table.Td><StatusBadge tone={m.source === 'local' ? 'custom' : 'blue'}>{m.source === 'local' ? '本機' : m.provider || 'SSO'}</StatusBadge></Table.Td>
            <Table.Td>{m.role_cn.length ? <Group gap={4}>{m.role_cn.map((r, i) => <StatusBadge key={m.roles[i]} tone={m.roles[i] === 'admin' ? 'red' : 'blue'}>{r}</StatusBadge>)}</Group> : <Text size="xs" c="dimmed">—</Text>}</Table.Td>
            <Table.Td><StatusBadge tone={STATUS[m.status][1]}>{STATUS[m.status][0]}</StatusBadge></Table.Td>
            <Table.Td>{m.mfa ? '✓' : '—'}</Table.Td>
            <Table.Td><Mono>{fmt(m.last_login)}</Mono></Table.Td>
            <Table.Td ta="right"><Mono>{m.login_failed_7d || '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{m.sessions || '—'}</Mono></Table.Td><Table.Td ta="right"><Mono>{m.assignments || '—'}</Mono></Table.Td>
            <Table.Td ta="right"><Button size="compact-sm" variant="subtle" onClick={() => void api.get<MemberDetail>(`/v1/iam/users/${m.id}`).then(setOpen)}>詳情</Button></Table.Td>
          </Table.Tr>))}</Table.Tbody>
      </Table></Table.ScrollContainer></Paper>
      <Text size="sm" c="dimmed">SSO 帳號由 IdP（AD／Entra）管理：停用帳號或移出群組即無法登入；這裡只能管理本機帳號（外包／廠商），而且只能持有白名單角色、強制 TOTP、{'≤'} 90 天到期。</Text>
      {draft && editable && (
        <Paper><SectionTitle title="新增本機帳號" />
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="帳號（英數、點、底線）" value={draft.username} onChange={(e) => setDraft({ ...draft, username: e.target.value })} />
            <TextInput label="初始密碼（≥ 14 字）" type="password" value={draft.password} onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
            <TextInput label="姓名" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <TextInput label="信箱" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
            <MultiSelect label="角色（白名單內；正式環境只允許自訂角色）" value={draft.roles} data={localRoles.map((r) => ({ value: r.key ?? '', label: r.name }))} onChange={(v) => setDraft({ ...draft, roles: v })} />
          </SimpleGrid>
          <Group gap="xs" mt="md"><Button variant="filled" disabled={!draft.username || draft.password.length < 14 || !draft.roles.length} onClick={() => void create()}>建立</Button><Button onClick={() => setDraft(null)}>取消</Button></Group>
        </Paper>)}
      {open && <MemberDrawer m={open} editable={editable} onClose={() => setOpen(null)} run={run} />}
    </Stack>
  );
}

function MemberDrawer({ m, editable, onClose, run }: { m: MemberDetail; editable: boolean; onClose: () => void; run: (f: () => Promise<unknown>, ok: string) => Promise<void> }) {
  const local = m.source === 'local';
  const [pw, setPw] = useState('');
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title={<>{m.name || m.username} <Text component="span" size="xs" c="dimmed" ff="monospace">{m.id}</Text></>} right={<Button size="compact-sm" variant="subtle" onClick={onClose}>關閉</Button>} />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <KeyValue rows={[['來源', local ? '本機帳號' : m.provider], ['狀態', STATUS[m.status][0]], ['MFA', m.mfa ? 'TOTP' : '—'], ['最近登入', fmt(m.last_login)], ...(local ? [['到期', fmt(m.expires_at)] as [string, string]] : [])]} />
          <div><Text size="sm" fw={600}>本地指派</Text>{m.assignment_rows.length ? <List size="sm" spacing="xs" c="dimmed">{m.assignment_rows.map((a) => <List.Item key={a.id}>{a.roles.join('、')}{a.temp ? '（臨時提權）' : ''}{a.expires_at ? ` · 到期 ${typeof a.expires_at === 'number' ? fmt(a.expires_at) : a.expires_at}` : ''} · {a.reason}</List.Item>)}</List> : <Text size="sm" c="dimmed">無（角色來自 IdP 群組對應）</Text>}</div>
          <div><Text size="sm" fw={600}>有效 session</Text>{m.session_rows.length ? <List size="sm" spacing="xs" c="dimmed">{m.session_rows.map((s) => <List.Item key={s.sid}><Mono>{s.sid}… · {s.provider} · {fmt(s.last_seen)} · {s.ip_masked}</Mono></List.Item>)}</List> : <Text size="sm" c="dimmed">無</Text>}</div>
        </SimpleGrid>
        {local && editable && (
          <Group gap="xs">
            <Button size="compact-sm" variant="subtle" onClick={() => void run(() => api.patch(`/v1/iam/users/${m.id}`, { disabled: m.status !== 'disabled' }), m.status !== 'disabled' ? '已停用（既有 session 已踢出）' : '已啟用')}>{m.status === 'disabled' ? '啟用' : '停用'}</Button>
            <TextInput aria-label="新密碼" type="password" placeholder="新密碼（≥ 14 字）" value={pw} onChange={(e) => setPw(e.target.value)} w={220} />
            <Button size="compact-sm" variant="subtle" disabled={pw.length < 14} onClick={() => void run(() => api.post(`/v1/iam/users/${m.id}/reset-password`, { password: pw }).then(() => setPw('')), '已重設密碼並延展到期')}>重設密碼</Button>
            <Button size="compact-sm" variant="subtle" onClick={() => void run(async () => { const r = await api.post<{ otpauth: string }>(`/v1/iam/users/${m.id}/reset-totp`); alert(`新的 TOTP 設定（只顯示這一次）：\n${r.otpauth}`); }, '已重設 TOTP')}>重設 TOTP</Button>
            <Button size="compact-sm" variant="subtle" color="red" onClick={() => { if (confirm('刪除此本機帳號？會同時撤銷 session 與本地指派。')) void run(() => api.del(`/v1/iam/users/${m.id}`).then(onClose), '已刪除帳號'); }}>刪除</Button>
          </Group>)}
        <div><Text size="sm" fw={600} mb="xs">最近審計</Text>
          <Table.ScrollContainer minWidth={560}><Table><Table.Thead><Table.Tr><Table.Th>時間</Table.Th><Table.Th>事件</Table.Th><Table.Th>內容</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{m.recent_audit.slice(0, 20).map((r, i) => <Table.Tr key={i}><Table.Td style={{ whiteSpace: 'nowrap' }}><Text size="xs" ff="monospace">{fmt(r.at)}</Text></Table.Td><Table.Td><Mono>{r.event}</Mono></Table.Td><Table.Td><Text size="xs" ff="monospace" style={{ overflowWrap: 'anywhere' }}>{JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k]) => !['event', 'at', 'user'].includes(k)))).slice(0, 200)}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer></div>
      </Stack>
    </Paper>
  );
}
