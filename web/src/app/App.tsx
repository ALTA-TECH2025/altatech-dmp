// 路由：/login、/no-access 公開；其餘在 RequireAuth + AppShell 內，每頁再用 RequireGrant 綁模組與動作。
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '@/auth/AuthContext';
import { LoginPage, NoAccessPage } from '@/auth/LoginPage';
import { RequireAuth, RequireGrant } from '@/auth/RequireGrant';
import { AppShell } from '@/shell/AppShell';
import { IamPage } from '@/iam/IamPage';
import { FunnelPage } from '@/analytics/FunnelPage';
import { RetentionPage } from '@/analytics/RetentionPage';
import { EventsPage } from '@/analytics/EventsPage';
import { PathPage } from '@/analytics/PathPage';
import { UsersPage } from '@/users/UsersPage';
import { SegmentsPage } from '@/segments/SegmentsPage';
import { ApprovalsPage } from '@/approvals/ApprovalsPage';
import type { Module } from '@/api/types';

function Placeholder({ title, module }: { title: string; module: Module }) {
  return (
    <RequireGrant module={module}>
      <div className="page"><div className="page-h"><h1>{title}</h1><p>此頁由對應工作流接上 API（docs/23 分工）；外框、權限與登入已就緒。</p></div></div>
    </RequireGrant>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/no-access" element={<NoAccessPage />} />
          <Route element={<RequireAuth><AppShell /></RequireAuth>}>
            <Route index element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Placeholder title="總覽" module="overview" />} />
            <Route path="/events" element={<EventsPage />} />
            <Route path="/funnel" element={<FunnelPage />} />
            <Route path="/retention" element={<RetentionPage />} />
            <Route path="/path" element={<PathPage />} />
            <Route path="/attribution" element={<Placeholder title="歸因分析" module="attribution" />} />
            <Route path="/sql" element={<Placeholder title="自定義 SQL" module="events" />} />
            <Route path="/users" element={<UsersPage />} />
            <Route path="/segments" element={<SegmentsPage />} />
            <Route path="/segments/:id" element={<SegmentsPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/triggers" element={<Placeholder title="即時觸發" module="triggers" />} />
            <Route path="/integration" element={<Placeholder title="數據整合" module="integration" />} />
            <Route path="/governance/*" element={<Placeholder title="資料治理" module="governance" />} />
            <Route path="/migration" element={<Placeholder title="神策遷移" module="governance" />} />
            <Route path="/audit" element={<Placeholder title="日誌統計" module="audit" />} />
            <Route path="/settings" element={<Placeholder title="平台設定" module="iam" />} />
            <Route path="/iam/*" element={<RequireGrant module="iam"><IamPage /></RequireGrant>} />
            <Route path="*" element={<div className="page"><div className="card"><h1>找不到頁面</h1></div></div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
