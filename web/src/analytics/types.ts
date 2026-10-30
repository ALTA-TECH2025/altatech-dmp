// 與 openapi FunnelQuery／FunnelResult 及 /v1/semantic/catalog 對齊
import type { UsersBehavior } from '@/users/types';

export interface Cond { field: string; op: string; value?: unknown; type?: string }
/** 條件：平鋪陣列（＝且）或 {op, items} 樹（可巢狀）。 */
export type FilterTree = Cond[] | { op: 'and' | 'or'; items: unknown[] };
/** 對照組：組名 + 自己的篩選（屬性樹、分群、行為）；結果多一欄 cohort（留存 cohort_name）。 */
export interface Cohort { name: string; filters?: FilterTree; segment?: string; behaviors?: UsersBehavior[]; behaviors_op?: 'and' | 'or' }
/** 行為篩選與對照組：事件分析、漏斗、留存共用。 */
export interface Scope { behaviors?: UsersBehavior[]; behaviors_op?: 'and' | 'or'; cohorts?: Cohort[] }
export interface FunnelStep { event: string; alias?: string; filters?: FilterTree }
export interface FunnelQuery extends Scope {
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
export interface FunnelRow { cohort?: string; bucket?: string; g1?: string | null; step: number; alias: string; event: string; users: number | null; _k: number; suppressed?: boolean; rate_prev: number | null; rate_first: number | null }
export interface FunnelResult { rows: FunnelRow[]; steps: { event: string; alias: string }[]; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; chart_spec: Record<string, unknown> }

export interface Catalog {
  events: { name: string; cn: string; group: string; status: string }[];
  dimensions: { name: string; cn: string; type: string; values: string[]; sensitivity: string }[];
  event_columns: { name: string; cn: string; type?: string }[];
  user_props: { key: string; cn: string; type: string; values: string[]; filterable: boolean; groupable: boolean }[];
  aggs: string[];
  ops: Record<string, string[]>;
  funnel: { window_units: string[]; max_window_days: number; max_steps: number; orders: string[]; metrics: string[] };
  path?: { max_steps: number; session_modes: string[]; default_sample: number; max_days: number; exit_node: string };
}

export interface RetentionQuery extends Scope {
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
export interface RetentionRow { cohort: string; cohort_name?: string; g1?: string | null; period: number; users: number | null; retained: number | null; rate: number | null; complete: boolean; suppressed?: boolean }
export interface RetentionResult { rows: RetentionRow[]; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; chart_spec: Record<string, unknown> }

export interface EventsMetric { event?: string | null; agg: string; field?: string; alias?: string; filters?: FilterTree }
export interface EventsQuery extends Scope {
  project?: string;
  metric?: string;
  metrics: EventsMetric[];
  formulas?: { alias: string; expr: string }[];
  filters?: FilterTree;
  group_by?: string[];
  top_n?: number;
  time: { from: string; to: string; grain: 'minute' | 'hour' | 'day' | 'week' | 'month' | 'total'; now?: string };
  compare?: 'previous_period' | 'last_year';
  sample?: number;
  segment?: string;
  exact?: boolean;                                                   // true＝一律走明細（對帳用）
  estimate?: boolean;                                                // 快速估算：人數走日聚合層（近似值）；預設精確
}
export type EventsRow = Record<string, string | number | boolean | null | undefined>;
export interface EventsResult { rows: EventsRow[]; columns: string[]; chart_spec: Record<string, unknown>; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; masked: Record<string, string> }

export interface PathQuery {
  project?: string;
  anchor: { event: string; filters?: Cond[] };
  direction: 'after' | 'before';
  steps: number;
  session?: { mode?: 'gap' | 'id' | 'cross'; gap_minutes?: number; max_events?: number };
  nodes?: { events?: string[]; collapse_other?: boolean; split?: { event: string; field: string }[]; merge_repeats?: boolean; exit_node?: boolean };
  filters?: Cond[];
  segment?: string;
  sample?: number | null;
  top_n?: number;
  time: { from: string; to: string; now?: string };
}
export interface PathRow { path: string[]; users: number | null; sessions: number | null; share: number | null; avg_ms: number | null; suppressed: boolean }
export interface PathResult { paths: PathRow[]; anchor_users: number; nodes: { level: number; node: string; users: number }[]; links: { level: number; from: string; to: string; users: number }[]; sampled: number | null; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; chart_spec: Record<string, unknown> }

export type AttributionModel = 'first' | 'last' | 'linear' | 'position' | 'decay' | 'custom';
export interface AttributionTouch { event: string; alias?: string; filters?: Cond[]; weight?: number }
export interface AttributionQuery {
  project?: string;
  target: { event: string; filters?: Cond[]; value_field?: string };
  touches: AttributionTouch[];
  split?: string;
  models: AttributionModel[];
  position_weights?: { first: number; last: number };
  half_life_days?: number;
  window_days: number;
  filters?: Cond[];
  segment?: string;
  sample?: number;
  time: { from: string; to: string; now?: string };
}
export interface AttributionRow { touch: string; event: string; split: string | null; models: Record<string, { conversions: number | null; value: number | null; share: number | null }>; users: number | null; suppressed: boolean }
export interface AttributionResult { rows: AttributionRow[]; summary: { conversions: number; converters: number; touched: number; untouched: number; value: number; avg_touches: number | null }; models: AttributionModel[]; sql: string; lineage: string[]; cost: { ms: number; rows: number; layer: string }; warnings: string[]; chart_spec: Record<string, unknown> }
