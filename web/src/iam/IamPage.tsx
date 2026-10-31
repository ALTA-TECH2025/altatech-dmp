// 權限中心：角色（勾選矩陣；變更走雙人複核）、成員、使用者指派（含臨時提權）、數據權限（行級模擬）、欄位隱碼、session（本人／全平台）、變更審核。資料全部來自 /v1/iam/*；寫入需 iam.admin（後端再驗一次）。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Alert, Button, Checkbox, Code, Group, List, MultiSelect, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Tabs, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { ACTIONS, MODULES, type Action, type Assignment, type Module, type PendingReview, type Role, type ScopeSimulation, type SessionRow } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { copyAsCustom, toggleAction, unionGrants } from '@/lib/policy';
import { ChangesTab } from './ChangesTab';
import { MembersTab } from './MembersTab';

const TABS = [['roles', '角色'], ['members', '成員'], ['assign', '使用者指派'], ['data', '數據權限'], ['masking', '欄位隱碼'], ['sessions', 'session'], ['changes', '變更審核']] as const;
const WARN_BG = 'var(--mantine-color-orange-light)';   // 需要注意的列（臨時提權、目前 session）
const Loading = () => <Text size="sm" c="dimmed" ta="center" py="xl">載入中…</Text>;

export function IamPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const current = loc.pathname.replace(/^\/iam\/?/, '').split('/')[0] || 'roles';   // 分頁 = 路由（/iam、/iam/members…）
  return (
    <div className="page">
      <PageHeader title="權限中心" lead="成員、角色（RBAC + 範圍）、模組權限矩陣、數據權限（行級）、欄位隱碼、session。UI、API 與 AI 工具閘道共用同一套 Policy Engine；自訂角色變更與臨時提權需另一人複核。"
        actions={can('iam', 'admin') ? null : <StatusBadge tone="gray">唯讀</StatusBadge>} />
      <Tabs value={current} onChange={(v) => nav(v === 'roles' || !v ? '/iam' : `/iam/${v}`)} mb="md">
        <Tabs.List>{TABS.map(([k, cn]) => <Tabs.Tab key={k} value={k}>{cn}</Tabs.Tab>)}</Tabs.List>
      </Tabs>
      <Routes>
        <Route index element={<RolesTab />} />
        <Route path="members" element={<MembersTab />} />
        <Route path="assign" element={<AssignmentsTab />} />
        <Route path="data" element={<ScopeTab />} />
        <Route path="masking" element={<MaskingTab />} />
        <Route path="sessions" element={<SessionsTab />} />
        <Route path="changes" element={<ChangesTab />} />
      </Routes>
    </div>
  );
}

function useLoad<T>(url: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reload = () => api.get<T>(url).then((d) => { setData(d); setErr(null); }).catch((e: Error) => setErr(e.message));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void reload(); }, deps);
  return { data, err, reload };
}

// ---- 角色：列表 + 勾選矩陣編輯（蘊含規則在 lib/policy.toggleAction）----
export function RolesTab() {
  const { can } = useAuth();
  const { data: roles, err, reload } = useLoad<Role[]>('/v1/iam/roles');
  const [edit, setEdit] = useState<Role | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const editable = can('iam', 'admin');
  const done = (r: unknown, ok: string) => { const p = r as Partial<PendingReview> | null; setMsg(p && p.status === 'pending_review' ? `已送審（審批單 ${p.approval ?? '—'}）：需另一位系統管理員或數位經營在「變更審核」核可後生效` : ok); setEdit(null); void reload(); };
  const save = async () => {
    if (!edit) return;
    try {
      done(edit.key ? await api.put(`/v1/iam/roles/${edit.key}`, edit) : await api.post('/v1/iam/roles', edit), '已儲存（審計 role.changed）');
    } catch (e) { setMsg(e instanceof ApiError ? e.message : '儲存失敗'); }
  };
  const remove = async (r: Role) => {
    const holders = (await api.get<Assignment[]>('/v1/iam/assignments')).filter((a) => a.roles.includes(r.key ?? '')).length;
    let q = '';
    if (holders) { q = prompt(`仍有 ${holders} 筆指派使用此角色，請輸入改派角色 key（例如 data_analyst）`) ?? ''; if (!q) return; } else if (!confirm(`刪除角色「${r.name}」？（需另一人複核）`)) return;
    try { done(await api.del(`/v1/iam/roles/${r.key}${q ? `?reassign_to=${encodeURIComponent(q)}` : ''}`), '已刪除（審計 role.deleted）'); } catch (e) { setMsg(e instanceof ApiError ? e.message : '刪除失敗'); }
  };
  if (err) return <Alert color="red" variant="light">{err}</Alert>;
  if (!roles) return <Loading />;
  return (
    <Stack gap="md">
      {msg && <Text size="sm" c="dimmed">{msg}</Text>}
      <Paper p={0} style={{ overflow: 'hidden' }}>
        <Table.ScrollContainer minWidth={760}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>角色</Table.Th><Table.Th>範圍</Table.Th><Table.Th>說明</Table.Th><Table.Th>權限摘要</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{roles.map((r) => (
              <Table.Tr key={r.key}>
                <Table.Td><Group gap={4}><Text fw={600} size="sm">{r.name}</Text>{r.builtin === false && <StatusBadge tone="custom">自訂</StatusBadge>}</Group><Text size="xs" c="dimmed" ff="monospace">{r.key}</Text></Table.Td>
                <Table.Td><StatusBadge tone={r.scope === 'global' ? 'blue' : 'amber'}>{r.scope}</StatusBadge>{r.lookback_days ? ` · 回溯 ${r.lookback_days} 天` : ''}</Table.Td>
                <Table.Td>{r.description}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{Object.keys(r.grants).length} 個模組 · 匯出 {Object.values(r.grants).filter((a) => a?.includes('export')).length} · 審批 {Object.values(r.grants).filter((a) => a?.includes('approve')).length}</Text></Table.Td>
                <Table.Td ta="right"><Group gap={4} justify="flex-end" wrap="nowrap"><Button size="compact-sm" variant="subtle" onClick={() => setEdit(structuredClone(r))}>{editable && r.builtin !== true ? '編輯' : '檢視'}</Button>
                  {editable && <Button size="compact-sm" variant="subtle" onClick={() => setEdit(copyAsCustom(r))}>複製</Button>}
                  {editable && r.builtin === false && <Button size="compact-sm" variant="subtle" color="red" onClick={() => void remove(r)}>刪除</Button>}</Group></Table.Td>
              </Table.Tr>))}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {editable && <Group gap="xs"><Button variant="filled" onClick={() => setEdit({ name: '', scope: 'global', grants: {}, builtin: false })}>＋ 新增角色</Button></Group>}
      {edit && <RoleEditor role={edit} readOnly={!editable || edit.builtin === true} onChange={setEdit} onSave={save} onCancel={() => setEdit(null)}
        onCopy={editable && edit.builtin === true ? () => setEdit(copyAsCustom(edit)) : undefined} />}
    </Stack>
  );
}

export function RoleEditor({ role, readOnly, onChange, onSave, onCancel, onCopy }: { role: Role; readOnly: boolean; onChange: (r: Role) => void; onSave: () => void; onCancel: () => void; onCopy?: () => void }) {
  const set = (m: Module, a: Action, on: boolean) => onChange({ ...role, grants: { ...role.grants, [m]: toggleAction(role.grants[m] ?? [], a, on) } });
  return (
    <Paper>
      <Stack gap="md">
        <SectionTitle title={role.key ? `角色：${role.name}` : '新增角色'}>{role.builtin && <Group gap="xs"><StatusBadge tone="gray">內建角色：權限由 rbac.yaml 定義，不可直接修改</StatusBadge>
          {onCopy && <Button size="compact-sm" variant="light" onClick={onCopy}>複製後可修改</Button>}</Group>}</SectionTitle>
        {role.builtin && onCopy && <Text size="sm" c="dimmed">要調整這個角色的權限：按「複製後可修改」建立一份自訂角色，勾選後儲存（走雙人複核），再到「使用者指派」把人改指派到新角色。</Text>}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="名稱" value={role.name} disabled={readOnly} onChange={(e) => onChange({ ...role, name: e.target.value })} />
          <NativeSelect label="範圍" value={role.scope} disabled={readOnly} data={[{ value: 'global', label: '全域' }, { value: 'aggregate_only', label: '僅聚合' }, { value: 'branch', label: '分公司（保留）' }, { value: 'agent', label: '營業員（保留）' }]} onChange={(e) => onChange({ ...role, scope: e.target.value as Role['scope'] })} />
          <TextInput label="回溯天數（0 = 不限）" type="number" min={0} value={role.lookback_days ?? 0} disabled={readOnly} onChange={(e) => onChange({ ...role, lookback_days: Number(e.target.value) })} />
        </SimpleGrid>
        <Table.ScrollContainer minWidth={520}>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>模組</Table.Th>{ACTIONS.map((a) => <Table.Th key={a.key} ta="center">{a.cn}</Table.Th>)}</Table.Tr></Table.Thead>
            <Table.Tbody>{MODULES.map((m) => (
              <Table.Tr key={m.key}><Table.Td>{m.cn}<Text size="xs" c="dimmed" ff="monospace">{m.key}</Text></Table.Td>
                {ACTIONS.map((a) => <Table.Td key={a.key} ta="center"><Group justify="center"><Checkbox aria-label={`${m.cn} ${a.cn}`} disabled={readOnly} checked={(role.grants[m.key] ?? []).includes(a.key)} onChange={(e) => set(m.key, a.key, e.target.checked)} /></Group></Table.Td>)}
              </Table.Tr>))}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <Text size="sm" c="dimmed">勾選規則：可用蘊含可見、匯出蘊含可用；匯出一律走審批並加浮水印。欄位隱碼不在角色上設定（見「欄位隱碼」）。儲存後成為變更申請，由另一位系統管理員或數位經營複核後生效。<StatusBadge tone="gray">逐事件／逐屬性可見白名單（舊平台式）：第二階段</StatusBadge></Text>
        <Group gap="xs">{!readOnly && <Button variant="filled" onClick={onSave}>儲存</Button>}<Button onClick={onCancel}>關閉</Button></Group>
      </Stack>
    </Paper>
  );
}

// ---- 使用者指派：群組對應（唯讀，來自 IdP）＋ 本地指派（自訂角色、到期、臨時提權）----
export function AssignmentsTab() {
  const { can } = useAuth();
  const { data, err, reload } = useLoad<Assignment[]>('/v1/iam/assignments');
  const { data: roles } = useLoad<Role[]>('/v1/iam/roles');
  const [draft, setDraft] = useState<Assignment | null>(null);
  const [bg, setBg] = useState<{ user_id: string; hours: number; reason: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const editable = can('iam', 'admin');
  const revoke = async (id: string) => { if (confirm('撤銷此指派？（審計 iam.assignment.revoked）')) { await api.del(`/v1/iam/assignments/${id}`); void reload(); } };
  const save = async () => { if (draft) { try { await api.post('/v1/iam/assignments', draft); setDraft(null); setMsg('已指派（審計 iam.assignment.created）'); void reload(); } catch (e) { setMsg(e instanceof ApiError ? e.message : '指派失敗'); } } };
  const requestBg = async () => { if (bg) { try { const r = await api.post<PendingReview>('/v1/iam/assignments', { ...bg, roles: ['admin'], break_glass: true }); setBg(null); setMsg(`已送審（審批單 ${r.approval ?? '—'}）：另一位系統管理員或數位經營核可後才生效，到期自動撤銷`); } catch (e) { setMsg(e instanceof ApiError ? e.message : '申請失敗'); } } };
  if (err) return <Alert color="red" variant="light">{err}</Alert>;
  if (!data) return <Loading />;
  const preview = draft && roles ? unionGrants(roles.filter((r) => draft.roles.includes(r.key ?? '')).map((r) => r.grants)) : null;
  const expText = (x?: string | number) => (x == null ? '—' : typeof x === 'number' ? new Date(x * 1000).toLocaleString('zh-TW', { hour12: false }) : x);
  return (
    <Stack gap="md">
      {msg && <Text size="sm" c="dimmed">{msg}</Text>}
      <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={860}><Table>
        <Table.Thead><Table.Tr><Table.Th>使用者</Table.Th><Table.Th>角色</Table.Th><Table.Th>數據範圍</Table.Th><Table.Th>到期</Table.Th><Table.Th>身分來源</Table.Th><Table.Th>理由</Table.Th><Table.Th /></Table.Tr></Table.Thead>
        <Table.Tbody>{data.map((u) => (
          <Table.Tr key={u.id ?? u.user_id} bg={u.temp ? WARN_BG : undefined}>
            <Table.Td><Text fw={600} size="sm">{u.user_id}</Text></Table.Td>
            <Table.Td><Group gap={4}>{u.roles.map((r) => <StatusBadge key={r} tone={r === 'admin' ? 'red' : 'blue'}>{roles?.find((x) => x.key === r)?.name ?? r}</StatusBadge>)}</Group></Table.Td>
            <Table.Td>{scopeText(u.scope)}</Table.Td>
            <Table.Td><Mono>{expText(u.expires_at as string | number | undefined)}</Mono></Table.Td>
            <Table.Td><Text size="xs" c="dimmed" ff="monospace">{u.temp ? `臨時提權 · ${u.approved_by ?? ''}` : u.sso_group ?? '本地指派'}</Text></Table.Td>
            <Table.Td><Text size="xs" c="dimmed">{u.reason ?? ''}</Text></Table.Td>
            <Table.Td ta="right">{editable && u.id && <Button size="compact-sm" variant="subtle" color="red" onClick={() => void revoke(u.id!)}>撤銷</Button>}</Table.Td>
          </Table.Tr>))}</Table.Tbody>
      </Table></Table.ScrollContainer></Paper>
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Paper><SectionTitle title="指派規則" /><List size="sm" spacing="xs" c="dimmed">
          <List.Item>角色由 IdP 群組自動對應（AD／Entra 群組 → 角色，設定在 backend/auth/idp.yaml），登入時套用，不可在此修改。</List.Item>
          <List.Item>本地指派只能「加自訂角色、限範圍、設到期」或「拒絕某角色」；內建角色改群組即可。</List.Item>
          <List.Item>多角色：功能聯集、隱碼最嚴、範圍聯集。臨時提權最長 4 小時，需系統管理員＋數位經營雙人核准，到期自動撤銷。</List.Item></List></Paper>
        <Paper><SectionTitle title="臨時提權（break-glass）" />
          {editable ? (bg ? (
            <Stack gap="sm">
              <TextInput label="使用者" value={bg.user_id} onChange={(e) => setBg({ ...bg, user_id: e.target.value })} />
              <TextInput label="小時（≤ 4）" type="number" min={1} max={4} value={bg.hours} onChange={(e) => setBg({ ...bg, hours: Number(e.target.value) })} />
              <TextInput label="理由（事故單號等）" value={bg.reason} onChange={(e) => setBg({ ...bg, reason: e.target.value })} />
              <Group gap="xs"><Button variant="filled" disabled={!bg.user_id || !bg.reason || !(bg.hours > 0 && bg.hours <= 4)} onClick={() => void requestBg()}>送審</Button><Button onClick={() => setBg(null)}>取消</Button></Group>
            </Stack>
          ) : <Stack gap="sm"><Text size="sm" c="dimmed">臨時取得 admin 最長 4 小時；申請成審批單，由另一位系統管理員或數位經營核可後才生效，到期自動撤銷。離職／調動：停用 AD／Entra 帳號即無法登入；既有 session 可在「session」分頁強制登出。</Text><Group gap="xs"><Button onClick={() => setBg({ user_id: '', hours: 2, reason: '' })}>申請臨時提權</Button></Group></Stack>) : <StatusBadge tone="gray">需 iam.admin</StatusBadge>}
        </Paper>
        <Paper><SectionTitle title="新增本地指派" />
          {editable ? (draft ? (
            <Stack gap="sm">
              <TextInput label="使用者（provider:subject 或帳號）" value={draft.user_id} onChange={(e) => setDraft({ ...draft, user_id: e.target.value })} />
              <MultiSelect label="角色" aria-label="角色" value={draft.roles} data={(roles ?? []).filter((r) => r.key !== 'admin').map((r) => ({ value: r.key ?? '', label: r.name }))} onChange={(v) => setDraft({ ...draft, roles: v })} />
              <TextInput label="到期" type="date" value={draft.expires_at ?? ''} onChange={(e) => setDraft({ ...draft, expires_at: e.target.value })} />
              <TextInput label="回溯天數" type="number" min={0} value={draft.scope?.lookback_days ?? 0} onChange={(e) => setDraft({ ...draft, scope: { ...draft.scope, lookback_days: Number(e.target.value) } })} />
              <TextInput label="理由" value={draft.reason ?? ''} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} required />
              {preview && <Text size="xs" c="dimmed">合併後可用模組：{Object.keys(preview).join('、') || '無'}</Text>}
              <Group gap="xs"><Button variant="filled" onClick={() => void save()} disabled={!draft.user_id || !draft.roles.length || !draft.reason}>建立</Button><Button onClick={() => setDraft(null)}>取消</Button></Group>
            </Stack>
          ) : <Group gap="xs"><Button variant="filled" onClick={() => setDraft({ user_id: '', roles: [], reason: '' })}>＋ 指派</Button></Group>) : <StatusBadge tone="gray">需 iam.admin</StatusBadge>}
        </Paper>
      </SimpleGrid>
    </Stack>
  );
}

function scopeText(s?: Assignment['scope']): string {
  if (!s) return '全域';
  const parts = [s.projects?.length ? `專案 ${s.projects.join('、')}` : '', s.branches?.length ? `分公司 ${s.branches.join('、')}` : '', s.segment_limit ? `分群 ${s.segment_limit}` : '', s.lookback_days ? `回溯 ${s.lookback_days} 天` : ''].filter(Boolean);
  return parts.join(' · ') || '全域';
}

// ---- 數據權限：行級規則模擬（查詢編譯器會加的 WHERE 與遮罩欄位）----
export function ScopeTab() {
  const { me } = useAuth();
  const { data: roles } = useLoad<Role[]>('/v1/iam/roles');
  const [role, setRole] = useState('');
  const [sim, setSim] = useState<ScopeSimulation | null>(null);
  const run = async (r: string) => { setRole(r); setSim(await api.post<ScopeSimulation>('/v1/iam/scope/simulate', { role: r, scope: {} })); };
  return (
    <SimpleGrid cols={{ base: 1, md: 2 }}>
      <Paper><SectionTitle title="你目前的行級條件" />
        <Code block>{me?.scope.kind === 'global' ? '-- 全域：無行級條件' : `-- 受限範圍：${JSON.stringify(me?.scope)}`}{'\n'}-- K 匿名：分組人數 &lt; {me?.scope.k_anonymity} → 指標抑制{me?.scope.lookback_days ? `\n-- event_date >= today()-${me.scope.lookback_days}` : ''}</Code></Paper>
      <Paper><SectionTitle title="行級規則模擬" right={<NativeSelect aria-label="模擬角色" value={role} data={[{ value: '', label: '選角色…' }, ...(roles ?? []).map((r) => ({ value: r.key ?? '', label: r.name }))]} onChange={(e) => void run(e.target.value)} />} />
        {sim ? <Code block>{sim.where.length ? sim.where.map((w) => `AND ${w}`).join('\n') : '-- 無行級條件'}{'\n'}-- 遮罩欄位：{sim.masked_fields.join(', ') || '無'}{'\n'}-- K = {sim.k_anonymity}</Code> : <Text size="sm" c="dimmed">選角色，看查詢編譯器（backend/semantic/compiler/policy.py）會注入的條件；UI、REST 與 AI 工具共用同一套。</Text>}
      </Paper>
    </SimpleGrid>
  );
}

// ---- 欄位隱碼：此身分的每個敏感欄位策略（來自 masking.yaml，取最嚴格）----
export function MaskingTab() {
  const { me } = useAuth();
  if (!me) return null;
  const rows = Object.entries(me.masking);
  return (
    <Paper p={0} style={{ overflow: 'hidden' }}>
      <Table.ScrollContainer minWidth={480}><Table>
        <Table.Thead><Table.Tr><Table.Th>欄位</Table.Th><Table.Th>敏感等級</Table.Th><Table.Th>你看到的</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{rows.map(([f, m]) => <Table.Tr key={f}><Table.Td><Mono>{f}</Mono></Table.Td><Table.Td><StatusBadge tone={m.level === 'L3' ? 'red' : m.level === 'L2' ? 'amber' : 'blue'}>{m.level}</StatusBadge></Table.Td><Table.Td><Mono>{m.policy}</Mono></Table.Td></Table.Tr>)}</Table.Tbody>
      </Table></Table.ScrollContainer>
      <Text size="sm" c="dimmed" p="sm">L4（姓名、身分證、手機、Email、完整帳號）不進分析倉，因此不在此表。揭露申請：需理由 + 審批（L3 由系統管理員、L2 由數位經營），24 小時後自動收回。</Text>
    </Paper>
  );
}

// ---- session：本人；iam.admin 可切到全平台並強制登出 ----
export function SessionsTab() {
  const { can, me } = useAuth();
  const admin = can('iam', 'admin');
  const [scope, setScope] = useState<'me' | 'all'>('me');
  const [msg, setMsg] = useState<string | null>(null);
  const { data, err, reload } = useLoad<SessionRow[]>(`/v1/iam/sessions${scope === 'all' ? '?scope=all' : ''}`, [scope]);
  const revoke = async (s: SessionRow) => {
    if (!confirm(`強制登出 ${s.user} 的 session ${s.sid}…？（審計 auth.session.revoked）`)) return;
    try { await api.del(`/v1/iam/sessions/${s.sid}`); setMsg('已強制登出'); void reload(); } catch (e) { setMsg(e instanceof ApiError ? e.message : '操作失敗'); }
  };
  if (err) return <Alert color="red" variant="light">{err}</Alert>;
  return (
    <Stack gap="md">
      {msg && <Text size="sm" c="dimmed">{msg}</Text>}
      {admin && <Group gap="xs"><SegmentedControl size="xs" value={scope} onChange={(v) => setScope(v as 'me' | 'all')} data={[{ value: 'me', label: '我的 session' }, { value: 'all', label: '全平台' }]} /><Text size="xs" c="dimmed">{(data ?? []).length} 個</Text></Group>}
      <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={900}><Table>
        <Table.Thead><Table.Tr><Table.Th>session</Table.Th>{scope === 'all' && <Table.Th>使用者</Table.Th>}<Table.Th>身分來源</Table.Th><Table.Th>角色</Table.Th><Table.Th>MFA</Table.Th><Table.Th>建立</Table.Th><Table.Th>最近活動</Table.Th><Table.Th>IP</Table.Th><Table.Th /></Table.Tr></Table.Thead>
        <Table.Tbody>{(data ?? []).map((s) => <Table.Tr key={s.sid} bg={s.current ? WARN_BG : undefined}><Table.Td><Group gap={4}><Mono>{s.sid}…</Mono>{s.current && <StatusBadge tone="blue">目前</StatusBadge>}</Group></Table.Td>{scope === 'all' && <Table.Td><Text fw={600} size="sm">{s.name || s.username || s.user}</Text><Text size="xs" c="dimmed" ff="monospace">{s.user}</Text></Table.Td>}<Table.Td>{s.provider}</Table.Td><Table.Td>{s.roles.join('、')}</Table.Td><Table.Td>{s.amr?.includes('mfa') ? '✓' : '—'}</Table.Td><Table.Td><Mono>{fmtTs(s.created_at)}</Mono></Table.Td><Table.Td><Mono>{fmtTs(s.last_seen)}</Mono></Table.Td><Table.Td><Mono>{s.ip_masked}</Mono></Table.Td>
          <Table.Td ta="right">{(scope === 'all' || s.user === me?.user.id) && !s.current && <Button size="compact-sm" variant="subtle" color="red" onClick={() => void revoke(s)}>強制登出</Button>}</Table.Td></Table.Tr>)}</Table.Tbody>
      </Table></Table.ScrollContainer><Text size="sm" c="dimmed" p="sm">同帳號最多 3 個同時 session；閒置 30 分鐘或 8 小時後自動登出。強制登出會立即失效對方的 cookie session。</Text></Paper>
    </Stack>
  );
}
const fmtTs = (t: number) => new Date(t * 1000).toLocaleString('zh-TW', { hour12: false });
