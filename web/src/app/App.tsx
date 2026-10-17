// 路由：/login、/no-access 公開；其餘在 RequireAuth + AppShell 內，每頁再用 RequireGrant 綁模組與動作。
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
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
import { ApprovalsPage } from '@/approvals/ApprovalsPage';
import { IntegrationPage } from '@/integration/IntegrationPage';
import { DashboardsPage } from '@/dashboards/DashboardsPage';
import { GovernancePage } from '@/governance/GovernancePage';
import { UserSchemaPage } from '@/governance/UserSchemaPage';
import { ItemsPage } from '@/governance/ItemsPage';
import { MigrationPage } from '@/migration/MigrationPage';
import { AuditPage } from '@/audit/AuditPage';
import { SettingsPage } from '@/settings/SettingsPage';
import { ProfileEntryPage } from '@/users/ProfileEntryPage';
import type { Module } from '@/api/types';

/** 尚未實作的頁面：標示「第二階段」（規劃內、排在後續里程碑）或「待開發」（本階段範圍、尚未接上），說明目前狀態與可替代入口。 */
function Placeholder({ title, module, stage, note, instead }: { title: string; module: Module; stage: 'phase2' | 'todo'; note: string; instead?: { to: string; cn: string } }) {
  return (
    <RequireGrant module={module}>
      <div className="page"><div className="page-h"><div><h1>{title} <span className={`pill ${stage === 'phase2' ? 'gray' : 'amber'}`}>{stage === 'phase2' ? '第二階段' : '待開發'}</span></h1><p>{note}</p></div></div>
        <div className="card"><p className="note">{stage === 'phase2' ? '這個模組在第一階段只做契約與原型，正式實作排在第二階段；外框、權限與登入已就緒。' : '這個模組在本階段範圍內，後端契約已定，前端尚未接上。'}{instead && <> 目前可先用 <Link to={instead.to}>{instead.cn}</Link>。</>}</p></div>
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
            <Route path="/events" element={<EventsPage />} />
            <Route path="/funnel" element={<FunnelPage />} />
            <Route path="/retention" element={<RetentionPage />} />
            <Route path="/path" element={<PathPage />} />
            <Route path="/attribution" element={<AttributionPage />} />
            <Route path="/sql" element={<Placeholder title="自定義 SQL" module="events" stage="todo" note="SQL 沙盒：語意層遮罩、行級條件注入、掃描量門檻與歷史；排在 M4。" instead={{ to: '/events', cn: '事件分析（含 SQL 檢視）' }} />} />
            <Route path="/users" element={<UsersPage />} />
            <Route path="/segments" element={<SegmentsPage />} />
            <Route path="/segments/:id" element={<SegmentsPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/triggers" element={<Placeholder title="即時觸發" module="triggers" stage="phase2" note="Flink 規則引擎 + MA Webhook：事件到達即觸發推送；第一階段以分群推送（審批後）替代。" instead={{ to: '/segments', cn: '分群與標籤' }} />} />
            <Route path="/integration" element={<IntegrationPage />} />
            <Route path="/profile" element={<ProfileEntryPage />} />
            <Route path="/ai/insights" element={<Placeholder title="洞察串流" module="ai_ask" stage="phase2" note="Agent（異常偵測、漏斗診斷、流失雷達）產出的洞察物件：證據、信心度、建議動作、訂閱與回饋；第一階段 AI 閘道關閉（docs/03、22 §1）。" instead={{ to: '/overview', cn: '總覽／儀表板' }} />} />
            <Route path="/ai/ask" element={<Placeholder title="問答" module="ai_ask" stage="phase2" note="選擇題式問數：自然語言 → 在既有分析模型與語意層指標上做選擇（不生成任意 SQL）→ 執行與解釋；高敏查詢需理由並審計。儀表板的「AI 生成卡片」同屬此階段（docs/30）。" instead={{ to: '/events', cn: '事件分析' }} />} />
            <Route path="/ai/agents" element={<Placeholder title="Agents" module="ai_admin" stage="phase2" note="Agent 定義（agents.yaml）、排程、執行紀錄與預算（掃描量、LLM token）；提議（分群、觸發）一律走審批。" />} />
            <Route path="/ai/tools" element={<Placeholder title="工具目錄" module="ai_admin" stage="phase2" note="MCP 工具閘道目錄（backend/mcp/tools.yaml）：每個工具的權限、預算、脫敏與審計；第一階段只有契約。" />} />
            <Route path="/governance" element={<GovernancePage />} />
            <Route path="/governance/users" element={<UserSchemaPage />} />
            <Route path="/governance/items" element={<ItemsPage />} />
            <Route path="/governance/pipeline" element={<Placeholder title="資料流與效能" module="governance" stage="phase2" note="端到端資料流、查詢 P50／P95／P99、批次作業與儲存：由 Prometheus／Grafana 與 ClickHouse query_log 供數，第二階段接上。" instead={{ to: '/integration', cn: '數據整合（同步任務執行紀錄）' }} />} />
            <Route path="/migration" element={<MigrationPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/iam/*" element={<RequireGrant module="iam"><IamPage /></RequireGrant>} />
            <Route path="*" element={<div className="page"><div className="card"><h1>找不到頁面</h1></div></div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
