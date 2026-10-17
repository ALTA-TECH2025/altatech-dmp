// 與 backend/openapi.yaml（IAM、Auth）及 backend/auth/mapping.me_payload 對齊的型別。契約變更時先改 openapi，再改這裡。

export type Action = 'view' | 'use' | 'export' | 'approve' | 'admin';
export type Module =
  | 'overview' | 'events' | 'funnel' | 'path' | 'attribution' | 'users' | 'profile' | 'segments' | 'triggers'
  | 'integration' | 'ai_ask' | 'ai_admin' | 'governance' | 'audit' | 'iam';
export type Level = 'L0' | 'L1' | 'L2' | 'L3' | 'L4';
export type ScopeKind = 'global' | 'aggregate_only' | 'restricted';

export interface Me {
  user: { id: string; username: string; name: string; email: string; provider: string };
  roles: { key: string; cn: string; active: boolean }[];
  grants: Partial<Record<Module, Action[]>>;
  masking: Record<string, { level: Level; policy: string }>;
  scope: { kind: ScopeKind; k_anonymity: number; lookback_days: number | null; projects?: string[]; branches?: string[]; segment_limit?: string };
  session: { idp_mode: 'broker' | 'direct'; amr: string[]; auth_time: number; step_up_needed: boolean; csrf_header: string };
  levels: Level[];
}

export interface Provider { key: string; cn: string; kind: 'oidc' | 'ldap' }
export interface ProvidersResponse { mode: 'broker' | 'direct'; providers: Provider[] }

export interface Role {
  key?: string;
  name: string;
  description?: string;
  scope: 'global' | 'branch' | 'agent' | 'aggregate_only';
  lookback_days?: number;
  grants: Partial<Record<Module, Action[]>>;
  masking_profile?: 'default' | 'stricter';
  builtin?: boolean;
}

export interface Assignment {
  id?: string;
  user_id: string;
  roles: string[];
  scope?: { branches?: string[]; agent_customers?: boolean; projects?: string[]; segment_limit?: string; lookback_days?: number };
  expires_at?: string;
  reason?: string;
  sso_group?: string;
  last_login?: string;
  temp?: boolean;
  approved_by?: string;
}

export interface ScopeSimulation { where: string[]; masked_fields: string[]; k_anonymity: number }

export interface SessionRow { sid: string; user: string; username?: string; name?: string; provider: string; roles: string[]; amr?: string[]; created_at: number; last_seen: number; ip_masked: string; current?: boolean }

// ---- 系統管理（backend/api/sysadmin.py、iam.py /changes）----
export type MemberStatus = 'active' | 'disabled' | 'locked' | 'expired' | 'never_logged_in' | 'no_role';
export interface Member {
  id: string; username: string; name: string; email: string; provider: string; source: 'sso' | 'local'; roles: string[]; role_cn: string[]; status: MemberStatus; mfa: boolean;
  last_login: number | null; login_failed_7d: number; sessions: number; assignments: number; expires_at: number | null;
}
export interface MemberDetail extends Member { assignment_rows: Assignment[]; session_rows: SessionRow[]; recent_audit: { event: string; at: number; [k: string]: unknown }[] }
export interface ChangeRow {
  kind: 'role_change' | 'break_glass'; id: string; status: 'pending' | 'approved' | 'rejected' | 'expired'; requested_by: string; created_at: number; approval?: string; decided_by?: string; decided_at?: number; note?: string;
  op?: 'create' | 'update' | 'delete'; role?: string; before?: Role | null; after?: Role | null; reassign_to?: string | null;     // role_change
  user_id?: string; hours?: number; reason?: string; assignment?: string;                                                          // break_glass
}
export interface PendingReview { status: 'pending_review'; approval: string; change?: string; request?: string; message: string }
export interface AuditStats {
  days: number; scope: 'all' | 'mine'; from: string; to: string; source: string;
  tiles: Record<'events' | 'active_users' | 'logins' | 'login_failed' | 'denied' | 'profile_views' | 'reveals' | 'exports' | 'queries' | 'approvals_pending', number>;
  daily: { date: string; logins: number; login_failed: number; denied: number; profile_views: number; exports: number; queries: number; events: number }[];
  by_event: { event: string; n: number }[]; by_module: { module: string; n: number }[]; by_role: { role: string; profile_views: number; queries: number; exports: number }[];
  top_sensitive: { user: string; n: number; reason: string; off_hours: number }[];
  approvals: { pending: number; approved: number; rejected: number; expired: number; avg_hours: number | null; by_type: Record<string, Record<string, number>> };
  agent: { tool_calls: number; proposals: number; stage: 'phase2' | 'live' };
  anomalies: { rule: string; cn: string; severity: 'high' | 'medium' | 'low'; user: string; bucket: string; n: number; threshold: number }[];
  rules: { key: string; cn: string; event: string; window?: 'hour' | 'day'; per?: string; threshold: number; severity?: string; field?: string; distinct?: string; off_hours_only?: boolean }[];
  business_hours: { start: number; end: number; tz: string; weekdays_only: boolean }; notify: string[];
}
export interface SettingsView { sections: { key: string; cn: string; source: string; items: { key: string; cn: string; value: unknown; note: string }[] }[]; edit: { enabled: boolean; stage: string; note: string } }

export const MODULES: { key: Module; cn: string }[] = [
  { key: 'overview', cn: '總覽' }, { key: 'events', cn: '事件分析' }, { key: 'funnel', cn: '漏斗' }, { key: 'path', cn: '路徑' }, { key: 'attribution', cn: '歸因' },
  { key: 'users', cn: '用戶列表' }, { key: 'profile', cn: '用戶細查' }, { key: 'segments', cn: '標籤與分群' }, { key: 'triggers', cn: '即時觸發' },
  { key: 'integration', cn: '數據整合' }, { key: 'ai_ask', cn: 'AI 洞察' }, { key: 'ai_admin', cn: 'Agent 設定' }, { key: 'governance', cn: '資料治理' },
  { key: 'audit', cn: '日誌統計' }, { key: 'iam', cn: '權限中心' },
];
export const ACTIONS: { key: Action; cn: string; short: string }[] = [
  { key: 'view', cn: '可見', short: 'v' }, { key: 'use', cn: '可用', short: 'u' }, { key: 'export', cn: '匯出', short: 'e' }, { key: 'approve', cn: '審批', short: 'a' }, { key: 'admin', cn: '設定', short: 'd' },
];
