// 與 openapi OpsPipeline／OpsQueryPerf／OpsBatchJobs／OpsAlerts 對齊
export type OpsStatus = 'ok' | 'warn' | 'error' | 'unknown';
export type Severity = 'low' | 'medium' | 'high' | 'critical';

export interface SourceItem {
  task: string; cn: string; enabled: boolean; schedule: string; mode: string; last_at: string | null; last_status: string | null; rows_in: number; rows_loaded: number; rows_bad: number;
  quarantine_rate: number | null; watermark: string | null; lag_min: number | null; failed: number; next_at: string | null; error: string | null; status: OpsStatus;
}
export interface TableItem { name: string; cn: string; rows: number; bytes: number; parts: number; note?: string }
export interface ServingItem { requests_5m: number; http_p50_ms: number | null; http_p95_ms: number | null; http_error_rate: number; requests_total: number; errors_5xx_total: number; queries_5m: number; query_p95_ms: number | null }
export interface ActivationItem { deliveries_24h: Record<string, number>; success_rate: number | null; paused_destinations: { destination: string; cn: string; consecutive_failures: number }[]; status: OpsStatus }
export interface Stage { key: 'sources' | 'warehouse' | 'serving' | 'activation'; cn: string; status: OpsStatus; summary: string; items: unknown[] }
export interface Quality { key: string; cn: string; value: number | null; text: string; status: OpsStatus }
export interface Pipeline { at: string; stages: Stage[]; quality: Quality[]; slo: { query_p95_ms: Record<string, number>; freshness: { behavior_events_min: number; daily_batch_deadline: string; routine_max_min: number }; serving: { http_error_rate_max: number; http_p95_ms: number }; activation: { delivery_success_min: number }; storage: { clickhouse_hot_tb: number } } }

export interface ModulePerf { module: string; cn: string; n: number; errors: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; avg_rows: number | null; sla_ms: number | null; sla_ok: boolean | null; error_rate: number; scan_rows_avg: number | null; memory_peak_mb: number | null }
export interface SlowQuery { at: string; module: string; ms: number; user: string | null; sql_hash: string | null; rows: number }
export interface QueryPerf { window: '1h' | '24h' | '7d'; at: string; modules: ModulePerf[]; slow: SlowQuery[]; clickhouse: { n: number; p95_ms: number | null; scan_rows_avg: number | null; memory_peak_mb: number | null; errors: number } | null }

export interface Job { name: string; cn: string; kind: 'sync' | 'routine'; schedule: string; enabled: boolean; last_at: string | null; last_status: string | null; last_ms: number | null; last_detail: string | null; next_at: string | null; rerunnable: boolean; errors_total?: number; runs_total?: number }
export interface StorageItem { name: string; cn: string; rows: number | null; bytes: number | null; parts: number | null; note: string }
export interface BatchJobs { at: string; jobs: Job[]; storage: StorageItem[] }

export interface AlertRule { key: string; cn: string; severity: Severity; threshold: number; op: string; unit: string; text: string; metric?: string; window?: string; per?: string | null }
export interface Alert extends AlertRule { value: number | null; subject: string; firing: boolean }
export interface Alerts { at: string; firing: Alert[]; rules: AlertRule[]; notify: string[] }

export interface NotifyConfig {
  email: { enabled: boolean; to_roles: string[]; extra_to: string[]; subject_prefix: string };
  line: { enabled: boolean; channel_access_token_ref: string; to: string[]; api_base: string };
  policy: { min_severity: Severity; cooldown_min: number; notify_resolved: boolean; quiet_hours: { start: number; end: number; tz: string } | null };
  updated_at: string | null; updated_by: string | null; available: { email: boolean; line: boolean };
}
export interface Notification { id: string; at: string; channel: 'email' | 'line'; kind: 'alert' | 'resolved' | 'test' | 'alertmanager'; alerts: string[]; recipients: number; status: 'ok' | 'failed' | 'skipped'; response: string | null; text: string }
