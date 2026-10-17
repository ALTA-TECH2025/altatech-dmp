// 登入頁：GET /auth/providers 決定顯示哪些入口。
//   broker 模式：一顆「公司帳號登入」→ 整頁跳 /auth/login?provider=keycloak（Keycloak 再分流到 Entra ID 或 AD）
//   direct 模式：「Microsoft Entra ID」按鈕（OIDC 跳轉）＋「網域帳號」表單（POST /auth/ldap，第二步 TOTP）
//   置中版面沿用 .gate／.gate-card；表單與按鈕用設計系統元件。
import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Box, Button, Divider, NativeSelect, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { ProvidersResponse } from '@/api/types';
import { DEMO_ROLES, isDemo, setDemoRole } from '@/demo/mode';

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
      <Paper className="gate-card">
        <Title order={1}>登入</Title>
        {err && <Alert color="red" variant="light">{err}</Alert>}
        {isDemo && <Stack gap="sm"><Text size="sm" c="dimmed">示範模式：固定資料（合成黃金資料集，無真實客戶），選一個角色直接進入；寫入動作只模擬成功。正式環境走 SSO／本機帳號 + TOTP。</Text>{DEMO_ROLES.map(([k, cn]) => <Button key={k} variant="filled" fullWidth onClick={() => { setDemoRole(k); location.assign(returnTo); }}>以「{cn}」進入</Button>)}</Stack>}
        {!isDemo && oidc.map((p) => (
          <Button key={p.key} variant="filled" fullWidth onClick={() => sso(p.key)} disabled={busy}>{p.cn}登入</Button>
        ))}
        {!isDemo && hasLdap && (
          <Box component="form" onSubmit={ldap} aria-label="帳密登入">
            <Stack gap="sm">
              <Divider label={`${oidc.length ? '或' : ''}使用${pwdCn}`} labelPosition="center" />
              {pwd.length > 1 && <NativeSelect label="登入方式" value={pwdKey} data={pwd.map((p) => ({ value: p.key, label: p.cn }))} onChange={(e) => setPwdKey(e.target.value)} disabled={needTotp} />}
              <TextInput label="帳號" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required pattern="[A-Za-z0-9._\-]{2,64}" disabled={needTotp} />
              <PasswordInput label="密碼" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required disabled={needTotp} />
              {needTotp && <TextInput label="驗證器 6 位數" inputMode="numeric" pattern="\d{6}" value={totp} onChange={(e) => setTotp(e.target.value)} autoComplete="one-time-code" autoFocus required />}
              <Button type="submit" fullWidth disabled={busy}>{needTotp ? '驗證並登入' : '登入'}</Button>
            </Stack>
          </Box>
        )}
        {!isDemo && prov && prov.providers.length === 0 && <Text size="sm" c="dimmed">此環境尚未設定身分提供者（backend/auth/idp.yaml）。</Text>}
        <Text size="sm" c="dimmed">登入即表示同意存取受到審計；所有查詢與匯出都會記錄到日誌統計。</Text>
      </Paper>
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
      <Paper className="gate-card">
        <Title order={1}>尚未授權</Title>
        <Text size="sm">你已成功登入，但帳號不屬於任何 DMP 群組（DMP-Admins／DMP-Marketing／DMP-Analysts／DMP-DigitalOps／DMP-IT）。請向系統管理員申請加入群組；申請後需重新登入。</Text>
        <Button component="a" href="/login" fullWidth>回登入頁</Button>
      </Paper>
    </div>
  );
}
