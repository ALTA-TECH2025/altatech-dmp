// 與 openapi FunnelQuery／FunnelResult 及 /v1/semantic/catalog 對齊
export interface Cond { field: string; op: string; value?: unknown; type?: string }
export interface FunnelStep { event: string; alias?: string; filters?: Cond[] }
export interface FunnelQuery {
  project?: string;
  steps: FunnelStep[];
  window: { value: number; unit: 'minute' | 'hour' | 'day' };
  order: 'loose' | 'strict';
  relation?: string;
  metric: 'users' | 'times';
  group_by?: string[];
  trend?: 'day' | 'week' | 'month';
  filters?: Cond[] | { op: 'and' | 'or'; items: unknown[] };
  segment?: string;
  sample?: number;
  time: { from: string; to: string };
}
export interface FunnelRow { bucket?: string; g1?: string | null; step: number; alias: string; event: string; users: number | null; _k: number; suppressed?: boolean; rate_prev: number | null; rate_first: number | null }
export interface FunnelResult { rows: FunnelRow[]; steps: { event: string; alias: string }[]; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; chart_spec: Record<string, unknown> }

export interface Catalog {
  events: { name: string; cn: string; group: string; status: string }[];
  dimensions: { name: string; cn: string; type: string; values: string[]; sensitivity: string }[];
  event_columns: { name: string; cn: string; type?: string }[];
  user_props: { key: string; cn: string; type: string; values: string[]; filterable: boolean; groupable: boolean }[];
  aggs: string[];
  ops: Record<string, string[]>;
  funnel: { window_units: string[]; max_window_days: number; max_steps: number; orders: string[]; metrics: string[] };
}

export interface RetentionQuery {
  project?: string;
  initial: { event: string; filters?: Cond[] };
  return?: { event: string; filters?: Cond[] };
  model: 'date' | 'interval' | 'continuous' | 'churn';
  unit: 'day' | 'week' | 'month';
  periods: number;
  include_day0?: boolean;
  group_by?: string[];
  filters?: Cond[];
  segment?: string;
  sample?: number;
  time: { from: string; to: string; now?: string };
}
export interface RetentionRow { cohort: string; g1?: string | null; period: number; users: number | null; retained: number | null; rate: number | null; complete: boolean; suppressed?: boolean }
export interface RetentionResult { rows: RetentionRow[]; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; chart_spec: Record<string, unknown> }

export interface EventsMetric { event?: string | null; agg: string; field?: string; alias?: string; filters?: Cond[] }
export interface EventsQuery {
  project?: string;
  metric?: string;
  metrics: EventsMetric[];
  formulas?: { alias: string; expr: string }[];
  filters?: Cond[];
  group_by?: string[];
  top_n?: number;
  time: { from: string; to: string; grain: 'minute' | 'hour' | 'day' | 'week' | 'month' | 'total'; now?: string };
  compare?: 'previous_period';
  sample?: number;
  segment?: string;
}
export type EventsRow = Record<string, string | number | boolean | null | undefined>;
export interface EventsResult { rows: EventsRow[]; columns: string[]; chart_spec: Record<string, unknown>; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; masked: Record<string, string> }
