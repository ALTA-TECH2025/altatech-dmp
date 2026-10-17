// 與 openapi Dashboards（/v1/dashboards*）對齊：儀表板、卡片（一種分析 + 查詢 body）、卡片資料（以檢視者身分重算）
export type CardKind = 'events' | 'funnel' | 'retention' | 'path' | 'attribution';
export type CardChart = 'auto' | 'line' | 'bar' | 'stat' | 'table';
export type Preset = '7d' | '14d' | '30d' | '90d';
export interface DashboardCard { id: string; title: string; kind: CardKind; query: Record<string, unknown>; chart: CardChart; width: 1 | 2; note: string; created_by: string; created_at: string }
export interface Dashboard {
  id: string; name: string; description: string; owner: string; shared: { roles: string[]; users: string[] }; time: { preset: Preset };
  cards: DashboardCard[]; created_at: string; updated_at: string; mine: boolean; editable: boolean; seed?: boolean;
}
export type DashboardSummary = Omit<Dashboard, 'cards'> & { cards: number };
export interface CardData {
  rows: Record<string, unknown>[]; columns?: string[]; chart_spec: Record<string, unknown>; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[];
  card: { id: string; kind: CardKind; chart: CardChart; title: string }; time: { from?: string; to?: string; grain?: string; pinned?: boolean };
  steps?: { event: string; alias: string }[]; masked?: Record<string, string>;
  nodes?: { level: number; node: string; users: number }[]; links?: { level: number; from: string; to: string; users: number }[]; paths?: unknown[]; anchor_users?: number; sampled?: number | null;
  summary?: { conversions: number; converters: number; touched: number; untouched: number; value: number; avg_touches: number | null }; models?: string[];
}
