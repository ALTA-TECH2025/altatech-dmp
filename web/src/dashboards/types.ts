// 與 openapi Dashboards（/v1/dashboards*）對齊：儀表板、卡片（一種分析 + 查詢 body）、卡片資料（以檢視者身分重算）
export type CardKind = 'events' | 'funnel' | 'retention' | 'path' | 'attribution' | 'sql';
export type CardChart = 'auto' | 'line' | 'area' | 'bar' | 'stacked' | 'pie' | 'percent' | 'stat' | 'table';
export type CardFormat = 'auto' | 'int' | 'dec2' | 'pct0' | 'pct2' | 'twd0' | 'twd2';
export type Preset = '7d' | '14d' | '30d' | '90d';
export interface DashboardCard { id: string; title: string; kind: CardKind; query: Record<string, unknown>; chart: CardChart; format?: CardFormat; width: 1 | 2; note: string; created_by: string; created_at: string; bookmark?: string; bookmark_deleted?: boolean }
export interface Dashboard {
  id: string; name: string; description: string; owner: string; shared: { roles: string[]; users: string[] }; time: { preset: Preset };
  cards: DashboardCard[]; created_at: string; updated_at: string; mine: boolean; editable: boolean; seed?: boolean;
}
export type DashboardSummary = Omit<Dashboard, 'cards'> & { cards: number };
export interface CardData {
  rows: Record<string, unknown>[]; columns?: string[]; chart_spec: Record<string, unknown>; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[];
  card: { id: string; kind: CardKind; chart: CardChart; format?: CardFormat; title: string }; time: { from?: string; to?: string; grain?: string; pinned?: boolean };
  steps?: { event: string; alias: string }[]; masked?: Record<string, string>;
  nodes?: { level: number; node: string; sessions?: number; users?: number }[]; links?: { level: number; from: string; to: string; sessions?: number; users?: number }[]; paths?: unknown[]; anchor_users?: number; anchor_sessions?: number; sampled?: number | null;
  summary?: { conversions: number; converters: number; touched: number; untouched: number; value: number; avg_touches: number | null }; models?: string[];
}

// ---- 進階（docs/30）：解讀、HTML 看板產物、定時寄送 ----
export interface ExplainCard { card: string; title: string; kind: CardKind; line: string; signal: 'up' | 'down' | 'flat' | 'none'; warnings: string[] }
export interface Explain { method: 'rules' | 'llm'; summary: string[]; cards: ExplainCard[]; generated_at: string; period: string; narrative?: string | null; note?: string }
export interface RenderArtifact {
  artifact_id: string; dashboard: string; name: string; url: string; share_url: string; audience: string; created_by: string; created_at: string; expires_at: string; bytes: number;
  cards_included: number; cards_skipped: { card: string; title: string; why: string }[]; downloads: number; expired: boolean;
}
export interface ScheduleRun { at: string; trigger: 'tick' | 'manual'; artifact_id: string | null; status: 'sent' | 'rendered_no_transport' | 'failed'; recipients: number; error: string | null }
export interface Schedule {
  id: string; dashboard: string; weekday: number | null; hour: number; audience: { roles: string[]; emails: string[] }; expires_days: number; include_explain: boolean;
  status: 'pending_approval' | 'active' | 'paused' | 'rejected'; approval: string | null; created_by: string; created_at: string; last_run: ScheduleRun | null;
}

// ---- 書籤（舊平台「書籤」對應）：分析查詢存成可重用、可分享的物件；看板卡片可引用 ----
export interface Bookmark {
  id: string; name: string; description: string; kind: CardKind; query: Record<string, unknown>; chart: CardChart; format?: CardFormat; shared: { roles: string[]; users: string[] };
  owner: string; created_at: string; updated_at: string; mine: boolean; editable: boolean; used_in: { dashboard: string; name: string }[]; added_to?: { dashboard: string; name: string }[];
}

// ---- 問數建卡片 ----
export interface AskCard { title: string; kind: CardKind; query: Record<string, unknown>; chart: string; confidence: number; explanation: string; sql_preview: string | null; warnings: string[]; selected: boolean }
export interface AskResult { run_id: string; parser: 'rules' | 'llm'; cards: AskCard[]; rejected: { title: string; kind: string; reason: string }[]; candidates: { events: { name: string; cn: string }[]; dimensions: string[] }; unmatched: string[]; dashboard: string | null; refused?: boolean }
export interface AskStatus { allowed: boolean; parsers: ('rules' | 'llm')[]; default: 'rules' | 'llm'; model: string | null; examples: string[]; note: string }
