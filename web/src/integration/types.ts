// 與 openapi Integration（/v1/integration/*）對齊：連接器目錄、連線（密鑰永遠遮罩）、數據表、同步任務與執行紀錄、預覽
export interface AuthMethod { fields: string[]; secret_fields: string[]; rotation_days: number | null; notes: string }
export interface Connector {
  type: string; cn: string; versions: string[]; auth: string[];
  auth_methods: Record<string, AuthMethod>;
  least_privilege: Record<string, string[]>; capabilities: Record<string, string>; incremental: Record<string, unknown>;
  tls: { required?: boolean; min_version?: string }; network: { port_default?: number; private_ip?: boolean }; driver: { name?: string; license?: string };
}
export interface Rotation { kind: 'short_lived' | 'rotating'; every_days?: number; due?: string; days_left?: number; state?: 'ok' | 'warn' | 'expired'; note?: string }
export interface Connection {
  id: string; type: string; auth_method: string; fields: Record<string, unknown>;
  secrets: Record<string, { ref: string; value: string }>; tls_verify: boolean; rotation: Rotation;
  last_test: { at: string; ok: boolean; failed_step: string | null } | null; owner: string; enabled: boolean; created_at: string | null; tables: number; tasks: number;
}
export interface ConnectionCreate { id: string; type: string; auth_method: string; fields: Record<string, string | number | boolean>; secret_refs: Record<string, string>; tls_verify: boolean; owner: string }
export type TestStep = 'validate' | 'dns' | 'tcp' | 'tls' | 'auth' | 'privilege' | 'cdc_prereq' | 'sample_read' | 'clock_skew';
export interface ConnectionTest { ok: boolean; at: string; steps: { step: TestStep; ok: boolean; detail: string; fix?: string }[] }
export interface Privileges { conn_id: string; verified_at: string | null; items: { scope: string; privilege: string; checked: boolean | null }[]; note: string }

export interface TableField { name: string; type: string; sensitivity: 'L1' | 'L2' | 'L3' | 'L4'; null_rate?: number; sample?: string }
export interface ExtTable {
  id: string; connection: string; table: string; pk: string[]; incremental_field: string | null; fields: TableField[];
  schema_version: number; changes: { at: string; items: { kind: string; field: string; type?: string; from?: string; to?: string }[] }[];
  source: { kind?: string; path?: string }; note: string; registered_by: string; registered_at: string; used_by: string[];
}
export interface TableCreate { connection: string; table: string; pk: string[]; incremental_field: string | null; source?: { kind: 'csv' | 'jsonl'; path: string }; fields?: { name: string; type?: string; sensitivity?: string }[]; note?: string }

export interface RunSummary { scheduled_for: string; status: string; rows_in: number | null; rows_ok: number | null; rows_quarantined: number | null; rows_rejected: number | null; rows_loaded: number | null; attempt: number; error: string | null }
export interface SyncTask {
  id: string; cn: string; model_path: string; entity: 'event' | 'user' | 'dim'; target: { type: string; table: string; write: string };
  source: { kind?: string; path?: string; connection?: string; table?: string; table_id?: string; incremental_field?: string };
  mode: 'full' | 'incremental' | 'stream'; schedule: string; depends_on: string[]; schema_change: string; events: string[]; fields: string[]; owner: string;
  created_by: string; created_at: string; status: 'active' | 'paused' | 'error'; enabled: boolean; paused_reason: string | null; next_slot: string | null; last: RunSummary | null; watermark: string | null;
}
export interface SyncTaskCreate { id: string; cn: string; model_path: string; source: { table_id: string; incremental_column?: string }; schedule: { cron?: string; interval_min?: number }; mode: 'full' | 'incremental'; depends_on: string[]; schema_change: 'auto_map' | 'ignore' | 'pause_and_notify'; retries: number }
export interface SyncRun { id: number; task_id: string; scheduled_for: string; attempt: number; status: string; trigger: string; started_at: string | null; finished_at: string | null; rows_in: number | null; rows_ok: number | null; rows_quarantined: number | null; rows_rejected: number | null; rows_loaded: number | null; error: string | null; log: string[]; watermark_from: string | null; watermark_to: string | null }
/** POST /v1/integration/sync-tasks/{id}/run 的 202：只登記排隊，由持有主控租約的 worker 執行（#87）；同一任務已在排隊或執行中時回同一筆。 */
export interface RunNow { task_id: string; cn: string; run_id: number | null; status: string; scheduled_for: string; demo?: boolean }
export interface Preview { summary: { in: number; ok: number; quarantined: number; rejected: number }; sample_ok: Record<string, unknown>[]; quarantined: { reason: string; row: Record<string, unknown> }[]; rejected: { reason: string }[]; new_fields: string[]; pii_hits: number; events: string[] }
