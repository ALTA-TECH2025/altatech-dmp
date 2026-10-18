// 與 openapi Migration（/v1/migration/*）對齊
export interface InventoryItem { id: string; cn: string; location: string; method: string; target: string; order: number; rule: string; done?: boolean; note?: string; rows_sensors: number | null; range: string | null; events: number | null; inventoried_at: string | null; rows_loaded: number | null; jobs: number; progress: number | null; status: 'done' | 'planned' | 'inventoried' | 'ok' | 'warn' | 'running' | 'blocked' | 'failed' }
export interface Inventory { items: InventoryItem[]; inventoried_at: string | null; users_total: number | null; sensors_configured: boolean; export_rules: { slice: string; checks: string[]; rerun_threshold: number; max_auto_rerun: number }; timeline: { id: string; date: string; cn: string }[]; thresholds: Record<string, number> }
export interface BridgeRow { sensors_event: string; cn: string; group: string; source: string; platforms: string; ga4_event: string; kind: string; kind_cn: string; params: string; note: string; aliases: string; issues: string[] }
export interface Bridge { items: BridgeRow[]; counts: Record<string, number>; total: number; via_ga: number; user_properties: { key: string; cn: string; sensitivity: string; to_ga: string; note: string }[]; issues: { event: string; issue: string }[]; limits: Record<string, number>; csv_path: string }
export interface ExportJob { id: string; dataset: string; method: 'api' | 'impala'; date_from: string; date_to: string; events: string[]; created_by: string; created_at: string; note: string; slices: number; done: number; rows_src: number; rows_exported: number; rows_loaded: number; diff: number | null; status: 'pending' | 'blocked' | 'failed' | 'warn' | 'ok' | 'running'; last: string | null; blocked_reason: string | null }
export interface ExportSlice { day: string; status: string; attempt: number; rows_src?: number; rows_exported?: number; rows_loaded?: number; rows_quarantined?: number; recon_ok?: boolean; error?: string; note?: string; at?: string; file?: string }
export interface Exports { items: ExportJob[]; sensors_configured: boolean; clickhouse: boolean; rules: Inventory['export_rules'] }
export interface Reconcile {
  days: number; project: string | null; thresholds: Record<string, number>;
  t2: { items: { task_id: string; day: string; rows_in: number; rows_loaded: number; quarantined: number; diff: number | null; ok: boolean }[]; ok: boolean | null };
  t5: { items: { event: string; ga4_event: string; sa: number; ga: number; days: number; sa_per_day: number; ga_per_day: number; diff: number | null; ok: boolean; series: { day: string; sa: number; ga: number }[] }[]; events_over: number; median_abs_diff: number | null };
  t6: { both: number; only_sa: number; only_ga: number; login_users: number; rate: number | null; ok: boolean };
  caveats: { item: string; note: string }[];
}
export type SensorsGroupStatus = 'pending' | 'snapshot' | 'rebuilt' | 'skipped';
export interface SensorsGroup { name: string; kind: 'segment' | 'tag'; cn: string; users: number; values: number | null; status: SensorsGroupStatus; dmp: { type: 'segment' | 'tag'; id: string; name: string; users: number | null; diff_pct: number | null; missing?: boolean } | null; note: string; updated_at: string; scanned_at?: string }
export interface SensorsGroupList { items: SensorsGroup[]; scanned_at: string | null; summary: Record<'total' | SensorsGroupStatus, number>; sensors_configured?: boolean }
