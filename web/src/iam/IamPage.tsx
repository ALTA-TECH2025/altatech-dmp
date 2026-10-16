// 權限中心：角色（勾選矩陣）、使用者指派、數據權限（行級模擬）、欄位隱碼、我的 session。資料全部來自 /v1/iam/*；寫入需 iam.admin（後端再驗一次）。
import { useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { ACTIONS, MODULES, type Action, type Assignment, type Module, type Role, type ScopeSimulation, type SessionRow } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { toggleAction, unionGrants } from '@/lib/policy';

const TABS = [['roles', '角色'], ['assign', '使用者指派'], ['data', '數據權限'], ['masking', '欄位隱碼'], ['sessions', '我的 session']] as const;

export function IamPage() {
  const { can } = useAuth();
  return (
    <div className="page">
      <div className="page-h">
        <div><h1>權限中心</h1><p>角色（RBAC + 範圍）、模組權限矩陣、數據權限（行級）、欄位隱碼。UI、API 與 AI 工具閘道共用同一套 Policy Engine。</p></div>
        <div className="right">{can('iam', 'admin') ? null : <span className="pill gray">唯讀</span>}</div>
      </div>
      <div className="seg">{TABS.map(([k, cn]) => <NavLink key={k} to={k === 'roles' ? '/iam' : `/iam/${k}`} end className={({ isActive }) => (isActive ? 'on' : '')}>{cn}</NavLink>)}</div>
      <Routes>
        <Route index element={<RolesTab />} />
        <Route path="assign" element={<AssignmentsTab />} />
        <Route path="data" element={<ScopeTab />} />
        <Route path="masking" element={<MaskingTab />} />
        <Route path="sessions" element={<SessionsTab />} />
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
  const save = async () => {
    if (!edit) return;
    try {
      if (edit.key && edit.builtin === false || edit.key) await api.put(`/v1/iam/roles/${edit.key}`, edit); else await api.post('/v1/iam/roles', edit);
      setMsg('已儲存（審計 role.changed；正式環境需雙人複核）'); setEdit(null); void reload();
    } catch (e) { setMsg(e instanceof ApiError ? e.message : '儲存失敗'); }
  };
  if (err) return <div className="alert">{err}</div>;
  if (!roles) return <div className="loading">載入中…</div>;
  return (
    <>
      {msg && <div className="note">{msg}</div>}
      <div className="card flush">
        <table className="tbl">
          <thead><tr><th>角色</th><th>範圍</th><th>說明</th><th>權限摘要</th><th /></tr></thead>
          <tbody>{roles.map((r) => (
            <tr key={r.key}>
              <td><b>{r.name}</b>{r.builtin === false && <span className="pill custom">自訂</span>}<span className="sub mono">{r.key}</span></td>
              <td><span className={`pill ${r.scope === 'global' ? 'blue' : 'amber'}`}>{r.scope}</span>{r.lookback_days ? ` · 回溯 ${r.lookback_days} 天` : ''}</td>
              <td>{r.description}</td>
              <td className="sub">{Object.keys(r.grants).length} 個模組 · 匯出 {Object.values(r.grants).filter((a) => a?.includes('export')).length} · 審批 {Object.values(r.grants).filter((a) => a?.includes('approve')).length}</td>
              <td className="num"><button className="btn-ghost sm" onClick={() => setEdit(structuredClone(r))}>{editable ? '編輯' : '檢視'}</button>
                {editable && <button className="btn-ghost sm" onClick={() => setEdit({ ...structuredClone(r), key: undefined, name: r.name + '（副本）', builtin: false })}>複製</button>}</td>
            </tr>))}</tbody>
        </table>
      </div>
      {editable && <div className="row" style={{ marginTop: 12 }}><button className="btn-primary" onClick={() => setEdit({ name: '', scope: 'global', grants: {}, builtin: false })}>＋ 新增角色</button></div>}
      {edit && <RoleEditor role={edit} readOnly={!editable || edit.builtin === true} onChange={setEdit} onSave={save} onCancel={() => setEdit(null)} />}
    </>
  );
}

export function RoleEditor({ role, readOnly, onChange, onSave, onCancel }: { role: Role; readOnly: boolean; onChange: (r: Role) => void; onSave: () => void; onCancel: () => void }) {
  const set = (m: Module, a: Action, on: boolean) => onChange({ ...role, grants: { ...role.grants, [m]: toggleAction(role.grants[m] ?? [], a, on) } });
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-h"><h3>{role.key ? `角色：${role.name}` : '新增角色'}</h3>{role.builtin && <span className="pill gray">內建角色：權限由 rbac.yaml 定義，不可在 UI 修改</span>}</div>
      <div className="grid c3">
        <label>名稱<input value={role.name} disabled={readOnly} onChange={(e) => onChange({ ...role, name: e.target.value })} /></label>
        <label>範圍<select value={role.scope} disabled={readOnly} onChange={(e) => onChange({ ...role, scope: e.target.value as Role['scope'] })}>
          <option value="global">全域</option><option value="aggregate_only">僅聚合</option><option value="branch">分公司（保留）</option><option value="agent">營業員（保留）</option></select></label>
        <label>回溯天數（0 = 不限）<input type="number" min={0} value={role.lookback_days ?? 0} disabled={readOnly} onChange={(e) => onChange({ ...role, lookback_days: Number(e.target.value) })} /></label>
      </div>
      <table className="tbl matrix">
        <thead><tr><th>模組</th>{ACTIONS.map((a) => <th key={a.key}>{a.cn}</th>)}</tr></thead>
        <tbody>{MODULES.map((m) => (
          <tr key={m.key}><td>{m.cn}<span className="sub mono">{m.key}</span></td>
            {ACTIONS.map((a) => <td key={a.key} className="num"><input type="checkbox" aria-label={`${m.cn} ${a.cn}`} disabled={readOnly} checked={(role.grants[m.key] ?? []).includes(a.key)} onChange={(e) => set(m.key, a.key, e.target.checked)} /></td>)}
          </tr>))}</tbody>
      </table>
      <p className="note">勾選規則：可用蘊含可見、匯出蘊含可用；匯出一律走審批並加浮水印。欄位隱碼不在角色上設定（見「欄位隱碼」）。</p>
      <div className="row">{!readOnly && <button className="btn-primary" onClick={onSave}>儲存</button>}<button className="btn-ghost" onClick={onCancel}>關閉</button></div>
    </div>
  );
}

// ---- 使用者指派：群組對應（唯讀，來自 IdP）＋ 本地指派（自訂角色、到期、臨時提權）----
export function AssignmentsTab() {
  const { can } = useAuth();
  const { data, err, reload } = useLoad<Assignment[]>('/v1/iam/assignments');
  const { data: roles } = useLoad<Role[]>('/v1/iam/roles');
  const [draft, setDraft] = useState<Assignment | null>(null);
  const editable = can('iam', 'admin');
  const revoke = async (id: string) => { if (confirm('撤銷此指派？（審計 iam.assignment.revoked）')) { await api.del(`/v1/iam/assignments/${id}`); void reload(); } };
  const save = async () => { if (draft) { await api.post('/v1/iam/assignments', draft); setDraft(null); void reload(); } };
  if (err) return <div className="alert">{err}</div>;
  if (!data) return <div className="loading">載入中…</div>;
  const preview = draft && roles ? unionGrants(roles.filter((r) => draft.roles.includes(r.key ?? '')).map((r) => r.grants)) : null;
  return (
    <>
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>使用者</th><th>角色</th><th>數據範圍</th><th>到期</th><th>身分來源</th><th>最近登入</th><th /></tr></thead>
        <tbody>{data.map((u) => (
          <tr key={u.id ?? u.user_id} className={u.temp ? 'warnrow' : ''}>
            <td><b>{u.user_id}</b></td>
            <td>{u.roles.map((r) => <span key={r} className={`pill ${r === 'admin' ? 'red' : 'blue'}`}>{roles?.find((x) => x.key === r)?.name ?? r}</span>)}</td>
            <td>{scopeText(u.scope)}</td>
            <td className="mono">{u.expires_at ?? '—'}</td>
            <td className="mono sub">{u.sso_group ?? '本地指派'}</td>
            <td className="mono">{u.last_login ?? '—'}</td>
            <td className="num">{editable && u.id && <button className="btn-ghost sm danger" onClick={() => void revoke(u.id!)}>撤銷</button>}</td>
          </tr>))}</tbody>
      </table></div></div>
      <div className="grid c3" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>指派規則</h3></div><ul className="priv">
          <li>角色由 IdP 群組自動對應（AD／Entra 群組 → 角色，設定在 backend/auth/idp.yaml），登入時套用，不可在此修改。</li>
          <li>本地指派只能「加自訂角色、限範圍、設到期」或「拒絕某角色」；內建角色改群組即可。</li>
          <li>多角色：功能聯集、隱碼最嚴、範圍聯集。臨時提權最長 4 小時，需系統管理員＋數位經營雙人核准，到期自動撤銷。</li></ul></div>
        <div className="card"><div className="card-h"><h3>離職／調動</h3></div><p className="note">停用 AD／Entra 帳號即無法登入；既有 session 最長 30 分鐘閒置或 8 小時絕對逾時失效；可在「我的 session」或由管理員強制登出。</p></div>
        <div className="card"><div className="card-h"><h3>新增本地指派</h3></div>
          {editable ? (draft ? (
            <div className="stack">
              <label>使用者（provider:subject 或帳號）<input value={draft.user_id} onChange={(e) => setDraft({ ...draft, user_id: e.target.value })} /></label>
              <label>角色<select multiple value={draft.roles} onChange={(e) => setDraft({ ...draft, roles: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
                {roles?.filter((r) => r.key !== 'admin').map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</select></label>
              <label>到期<input type="date" value={draft.expires_at ?? ''} onChange={(e) => setDraft({ ...draft, expires_at: e.target.value })} /></label>
              <label>回溯天數<input type="number" min={0} value={draft.scope?.lookback_days ?? 0} onChange={(e) => setDraft({ ...draft, scope: { ...draft.scope, lookback_days: Number(e.target.value) } })} /></label>
              <label>理由<input value={draft.reason ?? ''} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} required /></label>
              {preview && <p className="sub">合併後可用模組：{Object.keys(preview).join('、') || '無'}</p>}
              <div className="row"><button className="btn-primary" onClick={() => void save()} disabled={!draft.user_id || !draft.roles.length || !draft.reason}>建立</button><button className="btn-ghost" onClick={() => setDraft(null)}>取消</button></div>
            </div>
          ) : <button className="btn-primary" onClick={() => setDraft({ user_id: '', roles: [], reason: '' })}>＋ 指派</button>) : <span className="pill gray">需 iam.admin</span>}
        </div>
      </div>
    </>
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
    <div className="grid c2">
      <div className="card"><div className="card-h"><h3>你目前的行級條件</h3></div>
        <pre className="code">{me?.scope.kind === 'global' ? '-- 全域：無行級條件' : `-- 受限範圍：${JSON.stringify(me?.scope)}`}{'\n'}-- K 匿名：分組人數 &lt; {me?.scope.k_anonymity} → 指標抑制{me?.scope.lookback_days ? `\n-- event_date >= today()-${me.scope.lookback_days}` : ''}</pre></div>
      <div className="card"><div className="card-h"><h3>行級規則模擬</h3><div className="right"><select value={role} onChange={(e) => void run(e.target.value)}><option value="">選角色…</option>{roles?.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</select></div></div>
        {sim ? <pre className="code">{sim.where.length ? sim.where.map((w) => `AND ${w}`).join('\n') : '-- 無行級條件'}{'\n'}-- 遮罩欄位：{sim.masked_fields.join(', ') || '無'}{'\n'}-- K = {sim.k_anonymity}</pre> : <p className="note">選角色，看查詢編譯器（backend/semantic/compiler/policy.py）會注入的條件；UI、REST 與 AI 工具共用同一套。</p>}
      </div>
    </div>
  );
}

// ---- 欄位隱碼：此身分的每個敏感欄位策略（來自 masking.yaml，取最嚴格）----
export function MaskingTab() {
  const { me } = useAuth();
  if (!me) return null;
  const rows = Object.entries(me.masking);
  return (
    <div className="card flush"><table className="tbl">
      <thead><tr><th>欄位</th><th>敏感等級</th><th>你看到的</th></tr></thead>
      <tbody>{rows.map(([f, m]) => <tr key={f}><td className="mono">{f}</td><td><span className={`pill ${m.level === 'L3' ? 'red' : m.level === 'L2' ? 'amber' : 'blue'}`}>{m.level}</span></td><td className="mono">{m.policy}</td></tr>)}</tbody>
    </table><p className="note">L4（姓名、身分證、手機、Email、完整帳號）不進分析倉，因此不在此表。揭露申請：需理由 + 審批（L3 由系統管理員、L2 由數位經營），24 小時後自動收回。</p></div>
  );
}

// ---- 我的 session ----
export function SessionsTab() {
  const { data, err } = useLoad<SessionRow[]>('/v1/iam/sessions');
  if (err) return <div className="alert">{err}</div>;
  return (
    <div className="card flush"><table className="tbl">
      <thead><tr><th>session</th><th>身分來源</th><th>角色</th><th>建立</th><th>最近活動</th><th>IP</th></tr></thead>
      <tbody>{(data ?? []).map((s) => <tr key={s.sid}><td className="mono">{s.sid}…</td><td>{s.provider}</td><td>{s.roles.join('、')}</td><td className="mono">{fmtTs(s.created_at)}</td><td className="mono">{fmtTs(s.last_seen)}</td><td className="mono">{s.ip_masked}</td></tr>)}</tbody>
    </table><p className="note">同帳號最多 3 個同時 session；閒置 30 分鐘或 8 小時後自動登出。</p></div>
  );
}
const fmtTs = (t: number) => new Date(t * 1000).toLocaleString('zh-TW', { hour12: false });
