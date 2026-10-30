// 大框架（與原型 v0.3 同構，橫向導覽）：頂欄 = 品牌 + 群組（首頁／分析／營運／資料／AI 洞察／數據治理／系統管理）+ 右側（專案、身分、登出）；
// 第二列子導覽 = 目前群組的項目（未實作標二期／待開發）+ 數據範圍；手機版收進抽屜。群組與項目依 /v1/iam/me 的 grants 過濾（後端另有 authorize）。
import { useState, type FormEvent } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Badge, Button, Group, NativeSelect, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { NAV, type NavItem } from './nav';
import { DEMO_ROLES, demoRole, isDemo, setDemoRole } from '@/demo/mode';

const stageTag = (it: NavItem) => (it.stage ? <small className="nav-stage">{it.stage === 'phase2' ? '二期' : '待開發'}</small> : null);

export function AppShell() {
  const { me, can, logout } = useAuth();
  const loc = useLocation(); const nav = useNavigate();
  const [open, setOpen] = useState(false);
  if (!me) return null;
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((it) => can(it.module, it.action ?? 'view')) })).filter((g) => g.items.length);
  const isActive = (it: NavItem) => (it.path === '/governance' ? loc.pathname === '/governance' : loc.pathname === it.path || loc.pathname.startsWith(it.path + '/'));
  const active = groups.find((g) => g.items.some(isActive)) ?? groups[0];
  const initial = (me.user.name || me.user.username || '?').slice(0, 1);
  const scopeCn = me.scope.kind === 'global' ? '全域' : me.scope.kind === 'aggregate_only' ? '僅聚合' : '受限範圍';
  return (
    <div className="app">
      <header className="topnav">
        <div className="tn-in">
          <button className="icon-btn hamb" aria-label="選單" onClick={() => setOpen(true)}>☰</button>
          <NavLink className="brand" to="/overview"><img className="logo" src="/favicon.svg" alt="" /><b>ALTA TECH</b><span className="prod">DMP</span></NavLink>
          <nav className="tn-nav" aria-label="主選單">
            {groups.map((g) => <button key={g.id} className={g.id === active?.id ? 'on' : ''} onClick={() => nav(g.items[0].path)}>{g.cn}{g.id === 'ai' && <span className="badge">二期</span>}</button>)}
          </nav>
          <div className="tn-right">
            {isDemo && <Group className="tn-demo" gap={6} wrap="nowrap"><Badge color="orange" title="固定資料來自單機後端 + 合成黃金資料集；寫入動作只模擬成功">示範模式 · 固定資料</Badge><NativeSelect size="xs" aria-label="示範角色" value={demoRole()} data={DEMO_ROLES.map(([k, cn]) => ({ value: k, label: cn }))} onChange={(e) => { setDemoRole(e.target.value); location.reload(); }} /></Group>}
            {me.session.step_up_needed && (me.user.provider === 'local' ? <StepUp /> : <a className="pill amber" href={`/auth/login?provider=${me.session.idp_mode === 'broker' ? 'keycloak' : 'entra'}&return_to=${encodeURIComponent(location.pathname)}`}>管理動作需重新驗證</a>)}
            <span className="org-pick" title="專案 / 環境">證券示範專案<span className="sep">/</span>{isDemo ? 'demo' : 'prod'}</span>
            <div className="me" title={me.user.id}><span className="av">{initial}</span><span><span className="nm">{me.user.name || me.user.username}</span><span className="rl">{me.roles.map((r) => r.cn + (r.active ? '' : '（待驗證）')).join('、') || '無角色'}</span></span></div>
            <Button className="tn-logout" size="compact-sm" onClick={() => void logout()}>登出</Button>
          </div>
        </div>
      </header>
      {active && (
        <div className="subnav"><div className="tn-in">
          {active.items.map((it) => <NavLink key={it.path} to={it.path} className={`sn-item${isActive(it) ? ' on' : ''}`} title={it.stage === 'phase2' ? '第二階段' : it.stage === 'todo' ? '待開發' : undefined}>{it.cn}{stageTag(it)}</NavLink>)}
          <div className="right"><Badge color={me.scope.kind === 'global' ? 'brand' : 'orange'}>{scopeCn}{me.scope.lookback_days ? ` · 回溯 ${me.scope.lookback_days} 天` : ''}</Badge></div>
        </div></div>
      )}
      {open && (
        <div className="mnav"><div className="drawer-bk" onClick={() => setOpen(false)} /><nav className="mnav-panel" aria-label="主選單">
          <div className="hd"><img className="logo" src="/favicon.svg" alt="" /><b>ALTA TECH</b><span className="prod">DMP</span><button className="icon-btn x" onClick={() => setOpen(false)}>✕</button></div>
          {groups.map((g) => <div key={g.id}><div className="grp">{g.cn}</div>{g.items.map((it) => <NavLink key={it.path} to={it.path} className={`it${isActive(it) ? ' on' : ''}`} onClick={() => setOpen(false)}>{it.cn}{stageTag(it)}</NavLink>)}</div>)}
          <div className="ft">{isDemo && <div className="mnav-demo"><Badge color="orange">示範模式 · 固定資料</Badge><NativeSelect size="xs" aria-label="示範角色（選單）" value={demoRole()} data={DEMO_ROLES.map(([k, cn]) => ({ value: k, label: cn }))} onChange={(e) => { setDemoRole(e.target.value); location.reload(); }} /></div>}<button className="it" onClick={() => void logout()}>登出</button></div>
        </nav></div>
      )}
      <main className="content"><div className="wrap"><Outlet /></div></main>
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
    <Group component="form" gap={6} wrap="nowrap" onSubmit={submit} aria-label="管理動作重新驗證">
      <Badge color="orange">管理動作需再驗證</Badge>
      <TextInput w={96} size="xs" inputMode="numeric" pattern="\d{6}" placeholder="驗證碼" value={code} onChange={(e) => setCode(e.target.value)} required />
      <Button size="compact-xs" type="submit">驗證</Button>
      {err && <Text size="xs" c="red">{err}</Text>}
    </Group>
  );
}
