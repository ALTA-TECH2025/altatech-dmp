// 權限中心 › 變更審核：自訂角色變更（前後快照）與臨時提權申請；審批動作走 /v1/approvals/{id}/decide（不可自審）。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { ACTIONS, MODULES, type ChangeRow, type Role } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';

const ST: Record<ChangeRow['status'], [string, string]> = { pending: ['待複核', 'amber'], approved: ['已生效', 'blue'], rejected: ['已退回', 'red'], expired: ['已逾期', 'gray'] };
const OP: Record<NonNullable<ChangeRow['op']>, string> = { create: '新增角色', update: '修改角色', delete: '刪除角色' };
const fmt = (t?: number) => (t ? new Date(t * 1000).toLocaleString('zh-TW', { hour12: false }) : '—');

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
  if (err) return <div className="alert">{err}</div>;
  if (!rows) return <div className="loading">載入中…</div>;
  return (
    <>
      {msg && <div className="note">{msg}</div>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>申請</th><th>內容</th><th>申請者</th><th>時間</th><th>狀態</th><th>複核</th></tr></thead>
        <tbody>{rows.length === 0 && <tr><td colSpan={6} className="sub" style={{ padding: 20 }}>尚無變更申請。自訂角色的建立／修改／刪除與臨時提權都會先出現在這裡，由另一位系統管理員或數位經營複核。</td></tr>}
          {rows.map((c) => (
            <tr key={c.id} className={c.status === 'pending' ? 'warnrow' : ''}>
              <td><span className={`pill ${c.kind === 'break_glass' ? 'red' : 'custom'}`}>{c.kind === 'break_glass' ? '臨時提權' : OP[c.op ?? 'update']}</span><span className="sub mono">{c.id}</span></td>
              <td>{c.kind === 'break_glass' ? <><b>{c.user_id}</b> → admin {c.hours} 小時<span className="sub">{c.reason}</span></> : <><b>{(c.after ?? c.before)?.name}</b><span className="sub mono">{c.role}{c.reassign_to ? ` · 改派 ${c.reassign_to}` : ''}</span>{c.before || c.after ? <button className="btn-ghost sm" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? '收合' : '前後快照'}</button> : null}</>}</td>
              <td className="mono" style={{ fontSize: 12 }}>{c.requested_by}</td>
              <td className="mono" style={{ fontSize: 12 }}>{fmt(c.created_at)}</td>
              <td><span className={`pill ${ST[c.status][1]}`}>{ST[c.status][0]}</span></td>
              <td>{c.status === 'pending' ? (canReview && c.requested_by !== me?.user.id ? (
                <div className="stack"><input placeholder="意見（退回必填）" value={notes[c.id] ?? ''} onChange={(e) => setNotes({ ...notes, [c.id]: e.target.value })} />
                  <div className="row"><button className="btn-primary" onClick={() => void decide(c, true)}>核可</button><button className="btn-ghost danger" disabled={!(notes[c.id] ?? '').trim()} onClick={() => void decide(c, false)}>退回</button></div></div>
              ) : <span className="sub">{c.requested_by === me?.user.id ? '不可自審：等另一位系統管理員或數位經營' : '需系統管理員或數位經營'}</span>) : <span className="sub">{c.decided_by ?? ''} · {fmt(c.decided_at)}{c.note ? ` · ${c.note}` : ''}</span>}</td>
            </tr>))}</tbody>
      </table></div></div>
      {open && rows.filter((c) => c.id === open).map((c) => <Diff key={c.id} before={c.before ?? null} after={c.after ?? null} />)}
      <p className="note">雙人複核設定在 backend/security/rbac.yaml › custom_roles.second_review（審批角色 admin、digital_ops；逾期 7 天）；審批單也會出現在「審批中心」。</p>
    </>
  );
}

export function Diff({ before, after }: { before: Role | null; after: Role | null }) {
  const b = before?.grants ?? {}; const a = after?.grants ?? {};
  const changed = MODULES.filter((m) => JSON.stringify([...(b[m.key] ?? [])].sort()) !== JSON.stringify([...(a[m.key] ?? [])].sort()));
  const meta: [string, unknown, unknown][] = [['名稱', before?.name, after?.name], ['範圍', before?.scope, after?.scope], ['回溯天數', before?.lookback_days ?? 0, after?.lookback_days ?? 0], ['說明', before?.description, after?.description]].filter(([, x, y]) => x !== y) as [string, unknown, unknown][];
  return (
    <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>變更前後</h3><span className="sub">{before ? '' : '新增；'}{after ? '' : '刪除；'}{changed.length} 個模組權限異動</span></div>
      {meta.length > 0 && <dl className="kv">{meta.map(([k, x, y]) => <><dt key={k + 'k'}>{k}</dt><dd key={k + 'v'}><s className="sub" style={{ display: 'inline' }}>{String(x ?? '—')}</s> → <b>{String(y ?? '—')}</b></dd></>)}</dl>}
      {changed.length > 0 && <table className="tbl matrix"><thead><tr><th>模組</th><th>變更前</th><th>變更後</th></tr></thead>
        <tbody>{changed.map((m) => <tr key={m.key}><td>{m.cn}</td><td className="mono">{(b[m.key] ?? []).map((x) => ACTIONS.find((y) => y.key === x)?.cn ?? x).join('、') || '—'}</td><td className="mono"><b>{(a[m.key] ?? []).map((x) => ACTIONS.find((y) => y.key === x)?.cn ?? x).join('、') || '—'}</b></td></tr>)}</tbody></table>}
    </div>
  );
}
