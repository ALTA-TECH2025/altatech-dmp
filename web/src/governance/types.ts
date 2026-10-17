// 與 openapi Meta（/v1/meta/*）對齊：事件字典、屬性字典、虛擬事件、用戶屬性、OneID 規則、維度表、字典、虛擬屬性
export type EventStatus = 'live' | 'draft' | 'quarantine' | 'deprecated';
export type Level = 'L0' | 'L1' | 'L2' | 'L3' | 'L4';
export interface EventProp { key: string; type?: string; sensitivity?: Level; required?: boolean }
export interface MetaEvent {
  name: string; cn: string; group: string; entity: 'user' | 'account' | 'item'; source: string; kind: 'behavior' | 'business' | 'trade' | 'marketing'; status: EventStatus; platforms: string;
  aliases: string[]; properties: EventProp[]; owner: string; note: string; caveats?: string[]; catalog_synced: boolean; version: number; updated_at?: string | null; pending_change?: string | null;
  daily?: number | null; errors?: string[]; path_node_rule?: string | null;
}
export interface MetaEventDetail extends MetaEvent { history: { action: string; by: string; at: string; [k: string]: unknown }[]; used_by: string[] }
export interface EventList { items: MetaEvent[]; counts: Partial<Record<EventStatus, number>>; total: number; stats_source: string | null }
export interface ChangeRequest { id: string; event: string; requested_by: string; at: string; status: 'pending' | 'approved' | 'rejected'; changes: Record<string, unknown>; note: string; decided_by?: string; decided_at?: string; decision_note?: string }
export interface Quarantine { unregistered_events: { event: string; n: number | null; first_seen: string | null; last_seen: string | null; users: number | null; status: string; retention_days: number; note?: string }[]; rows: { task_id: string; reason: string; n: number; last_at: string }[]; policy: { retention_days: number; note: string }; stats_source: string | null }
export interface MetaProperty { key: string; cn: string; sa: string; type: string; scope: string; sensitivity: Level; source: string; enum: string[]; unit: string; used_by: number; dimension_table: string | null; dictionary: boolean; status: string; row_level: boolean; caveats: string[]; in_catalog: boolean }
export interface PropertyList { items: MetaProperty[]; forbidden_l4: string[]; levels: Record<Level, string> }
export interface VirtualEvent { name: string; cn: string; owner: string; members: { event: string; filters?: { field: string; op: string; value?: unknown }[] }[]; note: string; origin: 'catalog' | 'registered'; definition: string }
export interface UserProperty { key: string; cn: string; sa: string; type: string; values: string[]; source: string; update: string; sensitivity: Level; filterable: boolean; groupable: boolean; row_level_key?: boolean; buckets?: number[] | null; note: string; stored: boolean; hot: boolean; coverage: number | null; origin: 'catalog' | 'registered'; status?: string }
export interface UserPropertyList { items: UserProperty[]; users_total: number | null; write_protocol: string[]; forbidden_l4_count: number; stats_source: string | null }
export interface OneIdRule { key: string; cn: string; priority: number; cardinality: 'single' | 'multi'; rebind: string; source: string[]; max_count?: number; over_limit?: string; parent?: string; status?: string; participates?: boolean; note?: string; stats: { edges: number; users: number } | null }
export interface OneIdRules { version: number; alias: Record<string, string>; ids: OneIdRule[]; strong_ids: string[]; anonymous_user: Record<string, unknown>; login_backfill: Record<string, unknown>; stats: { users: number | null; identified: number | null; merges_30d: Record<string, number>; backfill_24h: number | null }; override: { by: string; at: string; status: string } | null; stats_source: string | null }
export interface DimColumn { key: string; cn: string; type: string; sensitivity: Level; expose?: boolean; exposed?: boolean }
export interface DimensionTable { id: string; cn: string; primary_key: string; item_type: string; join: { table: 'events' | 'users'; column: string; dim_column?: string }[]; source: string; sync: string; engine: string; columns: DimColumn[]; origin: 'catalog' | 'registered'; rows: number | null; exists: boolean; engine_effective: string; task: { id: string; status?: string; last?: unknown } | null; exposed: string[]; row_level?: string; note?: string; used_by?: string[] }
export interface DimensionTableList { items: DimensionTable[]; rules: Record<string, unknown>; stats_source: string | null }
export interface Dictionary { id: string; property: string; mode: string; source: string | { dimension_table: string; column: string }; count: number; sample: [string, string][]; origin: 'catalog' | 'uploaded'; updated_at: string | null; entries?: Record<string, string>; uploaded_by?: string }
export interface VirtualProperty { name: string; cn: string; scope: 'event' | 'user'; expr: string; uses: string[]; type?: string; note?: string; origin: 'catalog' | 'registered' }
