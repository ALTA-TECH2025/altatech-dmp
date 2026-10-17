// 大框架：側欄（依權限過濾）＋ 頂欄（身分、角色、step-up 提示、登出）＋ 內容區（<Outlet/>）。版面沿用原型 css 的 .sidebar/.topbar/.page 結構。
import { useState, type FormEvent } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { NAV } from './nav';

export function AppShell() {
  const { me, can, logout } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me) return null;
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((it) => can(it.module, 'view')) })).filter((g) => g.items.length);
  return (
    <div className={`app ${open ? 'nav-open' : ''}`}>
      <aside className="sidebar" aria-label="主選單">
        <div className="brand"><img src="/favicon.svg" alt="" /><b>AltaTech DMP</b></div>
        {groups.map((g) => (
          <div className="nav-group" key={g.id}>
            <div className="nav-h">{g.cn}</div>
            {g.items.map((it) => (
              <NavLink key={it.path} to={it.path} className={({ isActive }) => `nav-item${isActive ? ' on' : ''}`} onClick={() => setOpen(false)}>{it.cn}</NavLink>
            ))}
          </div>
        ))}
        <div className="nav-foot">
          <span className="pill gray">{me.scope.kind === 'global' ? '全域' : me.scope.kind === 'aggregate_only' ? '僅聚合' : '受限範圍'}</span>
          {me.scope.lookback_days ? <span className="pill amber">回溯 {me.scope.lookback_days} 天</span> : null}
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="burger" aria-label="選單" onClick={() => setOpen((v) => !v)}>☰</button>
          <div className="grow" />
          {me.session.step_up_needed && (me.user.provider === 'local' ? <StepUp /> : <a className="pill amber" href={`/auth/login?provider=${me.session.idp_mode === 'broker' ? 'keycloak' : 'entra'}&return_to=${encodeURIComponent(location.pathname)}`}>管理動作需重新驗證</a>)}
          <div className="who">
            <b>{me.user.name || me.user.username}</b>
            <span className="sub">{me.roles.map((r) => r.cn + (r.active ? '' : '（待驗證）')).join('、') || '無角色'} · {me.user.provider}</span>
          </div>
          <button className="btn-ghost sm" onClick={() => void logout()}>登出</button>
        </header>
        <main className="content"><Outlet /></main>
      </div>
    </div>
  );
}

/** 本機帳號的 step-up：輸入 TOTP → POST /auth/step-up → 重新載入身分（admin 角色回復）。 */
function StepUp() {
  const [code, setCode] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try { await api.post('/auth/step-up', { totp: code }); location.reload(); }
    catch (ex) { setErr(ex instanceof ApiError ? ex.message : '驗證失敗'); setCode(''); }
  };
  return (
    <form onSubmit={submit} className="row" style={{ marginTop: 0, gap: 6 }} aria-label="管理動作重新驗證">
      <span className="pill amber">管理動作需再驗證</span>
      <input style={{ width: 90 }} inputMode="numeric" pattern="\d{6}" placeholder="驗證碼" value={code} onChange={(e) => setCode(e.target.value)} required />
      <button className="btn-ghost sm">驗證</button>
      {err && <span className="sub" style={{ color: 'var(--red)' }}>{err}</span>}
    </form>
  );
}
