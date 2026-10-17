// 登入頁：GET /auth/providers 決定顯示哪些入口。
//   broker 模式：一顆「公司帳號登入」→ 整頁跳 /auth/login?provider=keycloak（Keycloak 再分流到 Entra ID 或 AD）
//   direct 模式：「Microsoft Entra ID」按鈕（OIDC 跳轉）＋「網域帳號」表單（POST /auth/ldap，第二步 TOTP）
import { useEffect, useState, type FormEvent } from 'react';
import { api, ApiError } from '@/api/client';
import type { ProvidersResponse } from '@/api/types';

export function LoginPage() {
  const params = new URLSearchParams(location.search);
  const returnTo = safeReturnTo(params.get('return_to'));
  const [prov, setProv] = useState<ProvidersResponse | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [needTotp, setNeedTotp] = useState(false);
  const [pwdKey, setPwdKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(params.get('error'));

  useEffect(() => { api.get<ProvidersResponse>('/auth/providers').then((p) => { setProv(p); setPwdKey(p.providers.find((x) => x.kind === 'ldap')?.key ?? ''); }).catch((e: Error) => setErr(e.message)); }, []);

  const sso = (key: string) => { location.assign(`/auth/login?provider=${encodeURIComponent(key)}&return_to=${encodeURIComponent(returnTo)}`); };

  const ldap = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const r = await api.post<{ ok?: boolean; next?: 'totp'; message?: string; return_to?: string }>('/auth/ldap', { provider: pwdKey || undefined, username, password, totp: totp || undefined });
      if (r.next === 'totp') { setNeedTotp(true); setErr(r.message ?? null); return; }
      location.assign(r.return_to && r.return_to !== '/' ? r.return_to : returnTo);
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '登入失敗'); setPassword(''); setTotp(''); }
    finally { setBusy(false); }
  };

  const oidc = prov?.providers.filter((p) => p.kind === 'oidc') ?? [];
  const pwd = prov?.providers.filter((p) => p.kind === 'ldap') ?? [];
  const hasLdap = pwd.length > 0;
  const pwdCn = pwd.find((p) => p.key === pwdKey)?.cn ?? pwd[0]?.cn ?? '帳密';
  return (
    <div className="gate">
      <div className="gate-brand"><img className="logo" src="/favicon.svg" alt="" /><b>AltaTech DMP</b></div>
      <div className="card gate-card">
        <h1>登入</h1>
        {err && <div className="alert">{err}</div>}
        {oidc.map((p) => (
          <button key={p.key} className="btn-primary wide" onClick={() => sso(p.key)} disabled={busy}>{p.cn}登入</button>
        ))}
        {hasLdap && (
          <form onSubmit={ldap} className="stack" aria-label="帳密登入">
            <div className="divider"><span>{oidc.length ? '或' : ''}使用{pwdCn}</span></div>
            {pwd.length > 1 && <label>登入方式<select value={pwdKey} onChange={(e) => setPwdKey(e.target.value)} disabled={needTotp}>{pwd.map((p) => <option key={p.key} value={p.key}>{p.cn}</option>)}</select></label>}
            <label>帳號<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required pattern="[A-Za-z0-9._\-]{2,64}" disabled={needTotp} /></label>
            <label>密碼<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required disabled={needTotp} /></label>
            {needTotp && <label>驗證器 6 位數<input inputMode="numeric" pattern="\d{6}" value={totp} onChange={(e) => setTotp(e.target.value)} autoComplete="one-time-code" autoFocus required /></label>}
            <button className="btn-ghost wide" type="submit" disabled={busy}>{needTotp ? '驗證並登入' : '登入'}</button>
          </form>
        )}
        {prov && prov.providers.length === 0 && <p className="note">此環境尚未設定身分提供者（backend/auth/idp.yaml）。</p>}
        <p className="note">登入即表示同意存取受到審計；所有查詢與匯出都會記錄到日誌統計。</p>
      </div>
    </div>
  );
}

export function safeReturnTo(v: string | null): string {
  if (!v || !v.startsWith('/') || v.startsWith('//')) return '/';
  return v;
}

export function NoAccessPage() {
  return (
    <div className="gate">
      <div className="card gate-card">
        <h1>尚未授權</h1>
        <p>你已成功登入，但帳號不屬於任何 DMP 群組（DMP-Admins／DMP-Marketing／DMP-Analysts／DMP-DigitalOps／DMP-IT）。請向系統管理員申請加入群組；申請後需重新登入。</p>
        <a className="btn-ghost wide" href="/login">回登入頁</a>
      </div>
    </div>
  );
}
