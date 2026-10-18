// 路由：/login、/no-access 公開；其餘在 RequireAuth + AppShell 內，每頁再用 RequireGrant 綁模組與動作。
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
import { Anchor, Badge, Paper, Text } from '@mantine/core';
import { PageHeader } from '@/ds';
import { DesktopOnly } from '@/ds/DesktopOnly';
import { AuthProvider } from '@/auth/AuthContext';
import { LoginPage, NoAccessPage } from '@/auth/LoginPage';
import { RequireAuth, RequireGrant } from '@/auth/RequireGrant';
import { AppShell } from '@/shell/AppShell';
import { IamPage } from '@/iam/IamPage';
import { FunnelPage } from '@/analytics/FunnelPage';
import { RetentionPage } from '@/analytics/RetentionPage';
import { EventsPage } from '@/analytics/EventsPage';
import { PathPage } from '@/analytics/PathPage';
import { AttributionPage } from '@/analytics/AttributionPage';
import { UsersPage } from '@/users/UsersPage';
import { SegmentsPage } from '@/segments/SegmentsPage';
import { TagsPage } from '@/tags/TagsPage';
import { ActivationPage } from '@/activation/ActivationPage';
import { ApprovalsPage } from '@/approvals/ApprovalsPage';
import { IntegrationPage } from '@/integration/IntegrationPage';
import { DashboardsPage } from '@/dashboards/DashboardsPage';
import { GovernancePage } from '@/governance/GovernancePage';
import { UserSchemaPage } from '@/governance/UserSchemaPage';
import { ItemsPage } from '@/governance/ItemsPage';
import { MigrationPage } from '@/migration/MigrationPage';
import { AuditPage } from '@/audit/AuditPage';
import { BookmarksPage } from '@/dashboards/BookmarksPage';
import { SqlPage } from '@/sql/SqlPage';
import { SettingsPage } from '@/settings/SettingsPage';
import { ProfileEntryPage } from '@/users/ProfileEntryPage';
import { OpsPage } from '@/ops/OpsPage';
import { MonitorPage } from '@/ops/MonitorPage';
import type { Module } from '@/api/types';

/** 尚未實作的頁面：標示「第二階段」（規劃內、排在後續里程碑）或「待開發」（本階段範圍、尚未接上），說明目前狀態與可替代入口。 */
function Placeholder({ title, module, stage, note, instead }: { title: string; module: Module; stage: 'phase2' | 'todo'; note: string; instead?: { to: string; cn: string } }) {
  return (
    <RequireGrant module={module}>
      <div className="page">
        <PageHeader title={<>{title} <Badge color={stage === 'phase2' ? 'gray' : 'orange'} ml="xs">{stage === 'phase2' ? '第二階段' : '待開發'}</Badge></>} lead={note} />
        <Paper maw={760}><Text size="sm" c="dimmed">{stage === 'phase2' ? '這個模組在第一階段只做契約與原型，正式實作排在第二階段；外框、權限與登入已就緒。' : '這個模組在本階段範圍內，後端契約已定，前端尚未接上。'}{instead && <> 目前可先用 <Anchor component={Link} to={instead.to}>{instead.cn}</Anchor>。</>}</Text></Paper>
      </div>
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
            <Route path="/overview" element={<DashboardsPage />} />
            <Route path="/overview/:id" element={<DashboardsPage />} />
            <Route path="/bookmarks" element={<BookmarksPage />} />
            <Route path="/events" element={<EventsPage />} />
            <Route path="/funnel" element={<FunnelPage />} />
            <Route path="/retention" element={<RetentionPage />} />
            <Route path="/path" element={<DesktopOnly title="行為路徑"><PathPage /></DesktopOnly>} />
            <Route path="/attribution" element={<AttributionPage />} />
            <Route path="/sql" element={<DesktopOnly title="自定義 SQL"><SqlPage /></DesktopOnly>} />
            <Route path="/users" element={<UsersPage />} />
            <Route path="/segments" element={<SegmentsPage />} />
            <Route path="/segments/:id" element={<SegmentsPage />} />
            <Route path="/tags" element={<TagsPage />} />
            <Route path="/tags/:id" element={<TagsPage />} />
            <Route path="/activation" element={<ActivationPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/triggers" element={<Placeholder title="即時觸發" module="triggers" stage="phase2" note="Flink 規則引擎 + MA Webhook：事件到達即觸發推送；第一階段以分群名單推送（審批後）替代。" instead={{ to: '/activation', cn: '名單推送' }} />} />
            <Route path="/integration" element={<DesktopOnly title="數據整合"><IntegrationPage /></DesktopOnly>} />
            <Route path="/profile" element={<ProfileEntryPage />} />
            <Route path="/ai/insights" element={<Placeholder title="洞察串流" module="ai_ask" stage="phase2" note="Agent（異常偵測、漏斗診斷、流失雷達）產出的洞察物件：證據、信心度、建議動作、訂閱與回饋；第一階段 AI 閘道關閉（docs/03、22 §1）。" instead={{ to: '/overview', cn: '總覽／儀表板' }} />} />
            <Route path="/ai/ask" element={<Placeholder title="問答" module="ai_ask" stage="phase2" note="對話式問答（多輪追問、解釋、存成洞察）排第二階段。「問數建卡片」已可用：到任一儀表板按「解讀 / HTML 看板 / 定時寄送」→「AI 生成卡片」，一句話在既有分析模型上做選擇，草稿乾跑編譯後加入看板或存成書籤；設定 LLM 金鑰後自動改用模型解析。" instead={{ to: '/overview', cn: '總覽／儀表板（AI 生成卡片）' }} />} />
            <Route path="/ai/agents" element={<Placeholder title="Agents" module="ai_admin" stage="phase2" note="Agent 定義（agents.yaml）、排程、執行紀錄與預算（掃描量、LLM token）；提議（分群、觸發）一律走審批。" />} />
            <Route path="/ai/tools" element={<Placeholder title="工具目錄" module="ai_admin" stage="phase2" note="MCP 工具閘道目錄（backend/mcp/tools.yaml）：每個工具的權限、預算、脫敏與審計；第一階段只有契約。" />} />
            <Route path="/governance" element={<DesktopOnly title="元事件管理"><GovernancePage /></DesktopOnly>} />
            <Route path="/governance/users" element={<DesktopOnly title="用戶表管理"><UserSchemaPage /></DesktopOnly>} />
            <Route path="/governance/items" element={<DesktopOnly title="維度表與字典"><ItemsPage /></DesktopOnly>} />
            <Route path="/governance/pipeline" element={<OpsPage />} />
            <Route path="/migration" element={<DesktopOnly title="歷史資料遷移"><MigrationPage /></DesktopOnly>} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<DesktopOnly title="平台設定"><SettingsPage /></DesktopOnly>} />
            <Route path="/system/monitor" element={<MonitorPage />} />
            <Route path="/iam/*" element={<RequireGrant module="iam"><DesktopOnly title="權限中心"><IamPage /></DesktopOnly></RequireGrant>} />
            <Route path="*" element={<div className="page"><div className="card"><h1>找不到頁面</h1></div></div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
