// 與 openapi SQL（/v1/sql/*）對齊
export interface SqlColumn { name: string; type: string; level: string; allowed: boolean }
export interface SqlTable { name: string; columns: SqlColumn[]; mid_table?: boolean; note?: string }
export interface SqlCatalog { tables: SqlTable[]; functions: string[]; examples: { cn: string; sql: string }[]; limits: { preview_rows: number; timeout_s: number; concurrent_per_user: number } }
export interface SqlResult { run_id: string; columns: string[]; rows: Record<string, unknown>[]; row_count: number; truncated: boolean; ms: number; scan_rows_estimate: number | null; sql_effective: string; lineage: string[]; warnings: string[]; explained?: boolean }
export interface SqlRun { id: string; user: string; sql: string; sql_hash: string; at: number; status: 'ok' | 'error'; rows: number; ms: number; error: string | null; lineage: string[]; columns?: string[] }
export interface SqlSaved { id: string; name: string; group: string; description: string; sql: string; params_default: Record<string, unknown>; shared: { roles: string[]; users: string[] }; owner: string; created_at: number; updated_at: number; mine: boolean; editable: boolean }
export interface MidTable { id: string; name: string; sql: string; note: string; owner: string; created_at: number; rows: number | null; columns: string[]; refreshed_at: number | null }
