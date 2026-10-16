// 路由守門：未登入 → /login（帶 return_to）；沒有模組權限 → 403 頁；admin 需 step-up → 提示重新驗證。
import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAuth } from './AuthContext';
import type { Action, Module } from '@/api/types';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="loading">載入中…</div>;
  if (!me) return <Navigate to={`/login?return_to=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  if (me.roles.length === 0) return <Navigate to="/no-access" replace />;
  return <>{children}</>;
}

export function RequireGrant({ module, action = 'view', children }: { module: Module; action?: Action; children: ReactNode }) {
  const { me, can } = useAuth();
  if (!can(module, action)) return <Forbidden module={module} action={action} stepUp={!!me?.session.step_up_needed && action === 'admin'} />;
  return <>{children}</>;
}

export function Forbidden({ module, action, stepUp }: { module: Module; action: Action; stepUp?: boolean }) {
  return (
    <div className="page">
      <div className="card">
        <h1>{stepUp ? '需要重新驗證' : '沒有權限'}</h1>
        {stepUp
          ? <p>系統管理員的設定動作需要 15 分鐘內完成過多因子驗證。<a className="btn-primary" href={`/auth/login?provider=keycloak&return_to=${encodeURIComponent(location.pathname)}`}>重新驗證</a></p>
          : <p>你的角色沒有「{module}」模組的「{action}」權限。需要時請向系統管理員申請（權限中心 › 使用者指派）。此次拒絕已寫入審計。</p>}
      </div>
    </div>
  );
}
