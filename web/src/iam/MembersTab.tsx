// 權限中心 › 成員（對應神策「成員管理」）：登入審計看到的 SSO 使用者 ∪ 本機帳號 ∪ 本地指派；本機帳號可新增／停用／重設密碼／重設 TOTP／刪除（需 iam.admin）。
import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/api/client';
import type { Member, MemberDetail, MemberStatus, Role } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';

const STATUS: Record<MemberStatus, [string, string]> = { active: ['啟用', 'blue'], disabled: ['停用', 'gray'], locked: ['鎖定', 'red'], expired: ['到期', 'amber'], never_logged_in: ['未登入', 'gray'], no_role: ['無角色', 'amber'] };
const fmt = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString('zh-TW', { hour12: false }) : '—');

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
  if (err) return <div className="alert">{err}</div>;
  if (!rows) return <div className="loading">載入中…</div>;
  const counts = { all: rows.length, sso: rows.filter((m) => m.source === 'sso').length, local: rows.filter((m) => m.source === 'local').length };
  return (
    <>
      {msg && <div className="note">{msg}</div>}
      {otp && <div className="card" style={{ marginBottom: 12 }}><div className="card-h"><h3>TOTP 設定（只顯示這一次）</h3><div className="right"><button className="btn-ghost sm" onClick={() => setOtp(null)}>關閉</button></div></div><pre className="code" style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{otp}</pre><p className="note">用驗證器 App 掃描或手動輸入 secret；之後登入需密碼 + 6 位數。</p></div>}
      <div className="row" style={{ marginBottom: 12 }}>
        <input placeholder="篩選身分／姓名／信箱／角色" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 240px' }} />
        <div className="seg">{(['all', 'sso', 'local'] as const).map((k) => <a key={k} href="#" className={src === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setSrc(k); }}>{k === 'all' ? '全部' : k === 'sso' ? '公司帳號（SSO）' : '本機帳號'} {counts[k]}</a>)}</div>
        {editable && <button className="btn-primary" onClick={() => setDraft({ username: '', password: '', roles: [], name: '', email: '' })}>＋ 本機帳號</button>}
      </div>
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>成員</th><th>來源</th><th>角色</th><th>狀態</th><th>MFA</th><th>最近登入</th><th className="num">失敗登入 7 天</th><th className="num">session</th><th className="num">指派</th><th /></tr></thead>
        <tbody>{list.map((m) => (
          <tr key={m.id} className={m.status === 'locked' || m.login_failed_7d >= 3 ? 'warnrow' : ''}>
            <td><b>{m.name || m.username}</b><span className="sub mono">{m.username} · {m.id}{m.email ? ` · ${m.email}` : ''}</span></td>
            <td><span className={`pill ${m.source === 'local' ? 'custom' : 'blue'}`}>{m.source === 'local' ? '本機' : m.provider || 'SSO'}</span></td>
            <td>{m.role_cn.length ? m.role_cn.map((r, i) => <span key={m.roles[i]} className={`pill ${m.roles[i] === 'admin' ? 'red' : 'blue'}`}>{r}</span>) : <span className="sub">—</span>}</td>
            <td><span className={`pill ${STATUS[m.status][1]}`}>{STATUS[m.status][0]}</span></td>
            <td>{m.mfa ? '✓' : '—'}</td>
            <td className="mono">{fmt(m.last_login)}</td>
            <td className="num">{m.login_failed_7d || '—'}</td><td className="num">{m.sessions || '—'}</td><td className="num">{m.assignments || '—'}</td>
            <td className="num"><button className="btn-ghost sm" onClick={() => void api.get<MemberDetail>(`/v1/iam/users/${m.id}`).then(setOpen)}>詳情</button></td>
          </tr>))}</tbody>
      </table></div></div>
      <p className="note">SSO 帳號由 IdP（AD／Entra）管理：停用帳號或移出群組即無法登入；這裡只能管理本機帳號（外包／廠商），而且只能持有白名單角色、強制 TOTP、{'≤'} 90 天到期。</p>
      {draft && editable && (
        <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>新增本機帳號</h3></div>
          <div className="grid c3">
            <label>帳號（英數、點、底線）<input value={draft.username} onChange={(e) => setDraft({ ...draft, username: e.target.value })} /></label>
            <label>初始密碼（≥ 14 字）<input type="password" value={draft.password} onChange={(e) => setDraft({ ...draft, password: e.target.value })} /></label>
            <label>姓名<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
            <label>信箱<input value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} /></label>
            <label>角色（白名單內；正式環境只允許自訂角色）<select multiple value={draft.roles} onChange={(e) => setDraft({ ...draft, roles: Array.from(e.target.selectedOptions).map((o) => o.value) })}>{localRoles.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</select></label>
          </div>
          <div className="row"><button className="btn-primary" disabled={!draft.username || draft.password.length < 14 || !draft.roles.length} onClick={() => void create()}>建立</button><button className="btn-ghost" onClick={() => setDraft(null)}>取消</button></div>
        </div>)}
      {open && <MemberDrawer m={open} editable={editable} onClose={() => setOpen(null)} run={run} />}
    </>
  );
}

function MemberDrawer({ m, editable, onClose, run }: { m: MemberDetail; editable: boolean; onClose: () => void; run: (f: () => Promise<unknown>, ok: string) => Promise<void> }) {
  const local = m.source === 'local';
  const [pw, setPw] = useState('');
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-h"><h3>{m.name || m.username} <span className="sub mono">{m.id}</span></h3><div className="right"><button className="btn-ghost sm" onClick={onClose}>關閉</button></div></div>
      <div className="grid c3">
        <div><dl className="kv"><dt>來源</dt><dd>{local ? '本機帳號' : m.provider}</dd><dt>狀態</dt><dd>{STATUS[m.status][0]}</dd><dt>MFA</dt><dd>{m.mfa ? 'TOTP' : '—'}</dd><dt>最近登入</dt><dd>{fmt(m.last_login)}</dd>{local && <><dt>到期</dt><dd>{fmt(m.expires_at)}</dd></>}</dl></div>
        <div><b style={{ fontSize: 13 }}>本地指派</b>{m.assignment_rows.length ? <ul className="priv">{m.assignment_rows.map((a) => <li key={a.id}>{a.roles.join('、')}{a.temp ? '（臨時提權）' : ''}{a.expires_at ? ` · 到期 ${typeof a.expires_at === 'number' ? fmt(a.expires_at) : a.expires_at}` : ''} · {a.reason}</li>)}</ul> : <p className="note">無（角色來自 IdP 群組對應）</p>}</div>
        <div><b style={{ fontSize: 13 }}>有效 session</b>{m.session_rows.length ? <ul className="priv">{m.session_rows.map((s) => <li key={s.sid} className="mono">{s.sid}… · {s.provider} · {fmt(s.last_seen)} · {s.ip_masked}</li>)}</ul> : <p className="note">無</p>}</div>
      </div>
      {local && editable && (
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn-ghost sm" onClick={() => void run(() => api.patch(`/v1/iam/users/${m.id}`, { disabled: m.status !== 'disabled' }), m.status !== 'disabled' ? '已停用（既有 session 已踢出）' : '已啟用')}>{m.status === 'disabled' ? '啟用' : '停用'}</button>
          <input type="password" placeholder="新密碼（≥ 14 字）" value={pw} onChange={(e) => setPw(e.target.value)} />
          <button className="btn-ghost sm" disabled={pw.length < 14} onClick={() => void run(() => api.post(`/v1/iam/users/${m.id}/reset-password`, { password: pw }).then(() => setPw('')), '已重設密碼並延展到期')}>重設密碼</button>
          <button className="btn-ghost sm" onClick={() => void run(async () => { const r = await api.post<{ otpauth: string }>(`/v1/iam/users/${m.id}/reset-totp`); alert(`新的 TOTP 設定（只顯示這一次）：\n${r.otpauth}`); }, '已重設 TOTP')}>重設 TOTP</button>
          <button className="btn-ghost sm danger" onClick={() => { if (confirm('刪除此本機帳號？會同時撤銷 session 與本地指派。')) void run(() => api.del(`/v1/iam/users/${m.id}`).then(onClose), '已刪除帳號'); }}>刪除</button>
        </div>)}
      <div style={{ marginTop: 12 }}><b style={{ fontSize: 13 }}>最近審計</b>
        <div className="tbl-wrap"><table className="tbl"><thead><tr><th>時間</th><th>事件</th><th>內容</th></tr></thead>
          <tbody>{m.recent_audit.slice(0, 20).map((r, i) => <tr key={i}><td className="mono" style={{ whiteSpace: 'nowrap', fontSize: 11.5 }}>{fmt(r.at)}</td><td className="mono">{r.event}</td><td className="mono" style={{ fontSize: 11, overflowWrap: 'anywhere' }}>{JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k]) => !['event', 'at', 'user'].includes(k)))).slice(0, 200)}</td></tr>)}</tbody></table></div></div>
    </div>
  );
}
