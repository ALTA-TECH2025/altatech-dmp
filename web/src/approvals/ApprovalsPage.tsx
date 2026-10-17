// 審批與匯出：待我審批（依我的角色能審的類型）、我的申請、匯出申請（核可後產檔 → 下載 CSV）。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Badge, Button, Group, Paper, SegmentedControl, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, Mono, PageHeader, StatusBadge } from '@/ds';
import type { Approval, Export, Reveal } from '@/users/types';
import { APPROVAL_PILL, APPROVAL_STATUS_CN, APPROVAL_TYPE_CN, EXPORT_PILL, EXPORT_STATUS_CN, exportNext, expiresText } from './approvals';

export function ApprovalsPage() {
  return <RequireGrant module="users"><ApprovalsInner /></RequireGrant>;
}

const Sub = ({ children }: { children: React.ReactNode }) => <Text size="xs" c="dimmed">{children}</Text>;

function ApprovalsInner() {
  const { me } = useAuth();
  const [tab, setTab] = useState<'to_me' | 'mine' | 'exports' | 'reveals'>('to_me');
  const [rvs, setRvs] = useState<Reveal[]>([]);
  const [list, setList] = useState<Approval[]>([]);
  const [exps, setExps] = useState<Export[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    setErr(null);
    try {
      if (tab === 'exports') setExps(await api.get<Export[]>('/v1/exports'));
      else if (tab === 'reveals') setRvs(await api.get<Reveal[]>('/v1/reveals'));
      else setList(await api.get<Approval[]>(`/v1/approvals?scope=${tab}`));
    } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); }
  };
  useEffect(() => { void reload(); }, [tab]);   // eslint-disable-line react-hooks/exhaustive-deps
  const decide = async (a: Approval, approve: boolean) => {
    setBusy(true); setErr(null);
    try { await api.post(`/v1/approvals/${a.id}/decide`, { approve, note: notes[a.id] ?? '' }); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '操作失敗'); } finally { setBusy(false); }
  };
  const run = async (e: Export) => { setBusy(true); setErr(null); try { await api.post(`/v1/exports/${e.id}/run`); await reload(); } catch (x) { setErr(x instanceof ApiError ? x.message : '產檔失敗'); } finally { setBusy(false); } };
  // 審批對象連結：以標章呈現，點了到該物件頁（匯出單切到本頁的匯出分頁）
  const objectLink = (a: Approval) => (a.object.kind === 'segment' ? <Badge component={Link} to={`/segments/${a.object.id}`} color="brand" style={{ cursor: 'pointer' }}>分群 {a.object.id}</Badge> : a.object.kind === 'tag' ? <Badge component={Link} to={`/tags/${a.object.id}`} color="brand" style={{ cursor: 'pointer' }}>標籤 {a.object.id}</Badge> : a.object.kind === 'push' ? <Badge component={Link} to="/activation" color="brand" style={{ cursor: 'pointer' }}>推送單 {a.object.id}</Badge> : a.object.kind === 'export' ? <Badge component="button" type="button" color="brand" style={{ cursor: 'pointer' }} onClick={() => setTab('exports')}>匯出單 {a.object.id}</Badge> : a.object.kind === 'role_change' || a.object.kind === 'break_glass' ? <Badge component={Link} to="/iam/changes" color="violet" style={{ cursor: 'pointer' }}>權限變更 {a.object.id}</Badge> : <Badge color="gray">{a.object.kind} {a.object.id}</Badge>);
  return (
    <div className="page">
      <PageHeader title="審批與匯出" lead="匯出、分群生效、推送、欄位揭露、agent 提議都走審批單；類型決定審批角色（匯出與分群 = 數位經營、L3 揭露 = 系統管理員），不能審自己的申請，7 天未審自動逾期。匯出核可後 7 天內由申請者本人產檔下載，檔案帶浮水印、欄位比畫面再嚴一級。" />
      <SegmentedControl mb="md" size="xs" value={tab} onChange={(v) => setTab(v as typeof tab)} data={[{ value: 'to_me', label: '待我審批' }, { value: 'mine', label: '我的申請' }, { value: 'exports', label: '匯出申請' }, { value: 'reveals', label: '揭露申請' }]} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {tab === 'reveals' ? (
        <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={820}><Table>
          <Table.Thead><Table.Tr><Table.Th>揭露單</Table.Th><Table.Th>欄位</Table.Th><Table.Th>範圍</Table.Th><Table.Th>理由</Table.Th><Table.Th>狀態</Table.Th><Table.Th>申請者</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{rvs.map((r) => (
            <Table.Tr key={r.id}><Table.Td><Mono>{r.id}</Mono><Sub>{r.created_at.slice(0, 16)}</Sub></Table.Td><Table.Td><Text fw={600} size="sm">{r.field}</Text><Sub>{r.level} · {r.hours} 小時</Sub></Table.Td><Table.Td><Text size="xs" c="dimmed" ff="monospace">{r.one_id ?? '全部'}</Text></Table.Td><Table.Td><Sub>{r.reason}</Sub></Table.Td>
              <Table.Td><StatusBadge tone={r.status === 'active' ? 'blue' : r.status === 'pending_approval' ? 'amber' : r.status === 'rejected' ? 'red' : 'gray'}>{{ pending_approval: '待審批', active: '揭露中', rejected: '已退回', expired: '已到期' }[r.status]}</StatusBadge>{r.status === 'active' && <Sub>{expiresText(r.expires_at)} · 已查看 {r.views} 次</Sub>}</Table.Td><Table.Td><Sub>{r.requester}</Sub></Table.Td></Table.Tr>))}
            {!rvs.length && <Table.Tr><Table.Td colSpan={6}><EmptyState>沒有揭露申請</EmptyState></Table.Td></Table.Tr>}
          </Table.Tbody></Table></Table.ScrollContainer></Paper>
      ) : tab !== 'exports' ? (
        <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={900}><Table>
          <Table.Thead><Table.Tr><Table.Th>類型</Table.Th><Table.Th>內容</Table.Th><Table.Th>申請者</Table.Th><Table.Th>狀態</Table.Th><Table.Th>時間</Table.Th><Table.Th>{tab === 'to_me' ? '審批' : '結果'}</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{list.map((a) => (
            <Table.Tr key={a.id}>
              <Table.Td><Badge color="gray">{APPROVAL_TYPE_CN[a.type] ?? a.type}</Badge></Table.Td>
              <Table.Td><Text fw={600} size="sm">{a.summary}</Text><Group gap={4} mt={2}>{objectLink(a)}<Sub>{Object.entries(a.detail ?? {}).filter(([k]) => k !== 'rule').map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ')}</Sub></Group></Table.Td>
              <Table.Td><Sub>{a.requester}</Sub></Table.Td>
              <Table.Td><StatusBadge tone={APPROVAL_PILL[a.status]}>{APPROVAL_STATUS_CN[a.status]}</StatusBadge>{a.status === 'pending' && <Sub>{expiresText(a.expires_at)}</Sub>}</Table.Td>
              <Table.Td><Text size="xs" c="dimmed" ff="monospace">{a.created_at.slice(0, 16)}</Text></Table.Td>
              <Table.Td>{a.status === 'pending' && tab === 'to_me' && a.requester !== me?.user.id ? (
                <Group gap="xs" wrap="nowrap"><TextInput aria-label={`意見 ${a.id}`} placeholder="意見（退回必填）" w={160} value={notes[a.id] ?? ''} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} />
                  <Button variant="filled" disabled={busy} onClick={() => void decide(a, true)}>核可</Button><Button color="red" disabled={busy || !(notes[a.id] ?? '').trim()} onClick={() => void decide(a, false)}>退回</Button></Group>
              ) : <Sub>{a.decided_by ? `${a.decided_by} · ${a.decided_at?.slice(0, 16)}` : ''}{a.note ? ` · ${a.note}` : ''}</Sub>}</Table.Td>
            </Table.Tr>))}
            {!list.length && <Table.Tr><Table.Td colSpan={6}><EmptyState>沒有審批單</EmptyState></Table.Td></Table.Tr>}
          </Table.Tbody></Table></Table.ScrollContainer></Paper>
      ) : (
        <Paper p={0} style={{ overflow: 'hidden' }}><Table.ScrollContainer minWidth={860}><Table>
          <Table.Thead><Table.Tr><Table.Th>匯出單</Table.Th><Table.Th>內容</Table.Th><Table.Th ta="right">列數</Table.Th><Table.Th>狀態</Table.Th><Table.Th>申請者</Table.Th><Table.Th>動作</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{exps.map((e) => { const nx = exportNext(e); return (
            <Table.Tr key={e.id}>
              <Table.Td><Mono>{e.id}</Mono><Sub>{e.created_at.slice(0, 16)}</Sub></Table.Td>
              <Table.Td><Text fw={600} size="sm">{e.what ?? e.kind}</Text><Sub>{e.purpose} · 保存 {e.retention_days} 天{e.masked && Object.keys(e.masked).length ? ` · 遮罩 ${Object.keys(e.masked).join('、')}` : ''}{e.watermark_id ? ` · 浮水印 ${e.watermark_id}` : ''}</Sub></Table.Td>
              <Table.Td ta="right"><Mono>{(e.rows ?? e.rows_estimate).toLocaleString()}</Mono></Table.Td>
              <Table.Td><StatusBadge tone={EXPORT_PILL[e.status]}>{EXPORT_STATUS_CN[e.status]}</StatusBadge>{e.expires_at && (e.status === 'approved' || e.status === 'generated') && <Sub>{expiresText(e.expires_at)}</Sub>}{e.note && <Sub>{e.note}</Sub>}</Table.Td>
              <Table.Td><Sub>{e.requester}</Sub></Table.Td>
              <Table.Td><Group gap="xs">{e.requester === me?.user.id && nx === 'run' && <Button variant="filled" disabled={busy} onClick={() => void run(e)}>產檔</Button>}
                {e.requester === me?.user.id && nx === 'download' && <Button component="a" href={`/v1/exports/${e.id}/download`}>下載 CSV</Button>}
                {nx === 'wait' && <Sub>等待數位經營審批</Sub>}</Group></Table.Td>
            </Table.Tr>); })}
            {!exps.length && <Table.Tr><Table.Td colSpan={6}><EmptyState>沒有匯出申請</EmptyState></Table.Td></Table.Tr>}
          </Table.Tbody></Table></Table.ScrollContainer></Paper>
      )}
    </div>
  );
}
