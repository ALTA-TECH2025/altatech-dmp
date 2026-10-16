// 與 openapi UsersQuery／UsersListRequest／UsersListResult／Profile／TimelineResult／IdentityGraph／Segment* 對齊
import type { Cond } from '@/analytics/types';

export interface UsersBehavior { event: string; filters?: Cond[]; done?: boolean; count?: { op: 'gte' | 'lte' | 'eq' | 'between'; value: number | [number, number] }; time?: { from: string; to: string } }
export interface UsersSource { type: 'funnel' | 'retention' | 'events' | 'path' | 'attribution'; query: Record<string, unknown>; touch?: string; step?: number; reached?: boolean; bucket?: string; group?: unknown; cohort?: string; period?: number; retained?: boolean | null; metric?: string; node?: string; level?: number; label?: string }
export interface UsersQuery {
  project?: string;
  filters?: Cond[];
  segment?: string;
  behaviors?: UsersBehavior[];
  behaviors_op?: 'and' | 'or';
  time?: { from?: string; to?: string; now?: string };
  source?: UsersSource;
}
export interface UsersListRequest extends UsersQuery { columns?: string[]; sort?: { field: string; dir: 'asc' | 'desc' }; page_size?: number; cursor?: string }
export type UserRow = Record<string, string | number | boolean | null | undefined>;
export interface UsersListResult { rows: UserRow[]; columns: string[]; masked: Record<string, string>; next_cursor: string | null; total: number | null; sql: string; lineage: string[]; cost: { ms?: number; rows: number; layer: string }; warnings: string[]; version?: SegmentVersion }
export interface UsersEstimate { users: number; sql: string; lineage: string[]; cost: { ms: number }; warnings: string[] }

export interface Profile { one_id: string; props: Record<string, unknown>; masked: Record<string, string>; segments: string[]; summary: { days: number; events: { event: string; cn: string; n: number; first: string; last: string }[]; total: number }; warnings: string[] }
export interface TimelineRow { time: string; event: string; cn: string; platform: string; channel: string; symbol: string; side: string; amount: number | string | null; entity_type: string; entity_id: string; session_id: string; event_id: string; props: Record<string, unknown> }
export interface TimelineResult { rows: TimelineRow[]; next_cursor: string | null; masked: Record<string, string>; sql: string; cost: { ms: number; rows: number } }
export interface IdentityGraph { nodes: { id: string; type: string; value: string }[]; edges: { a: string; b: string; source: string; evidence_event_id: string; first_seen: string; last_seen: string; seen_count: number; confidence: number; status: string }[]; masked: Record<string, string> }

export type SegmentStatus = 'draft' | 'pending_approval' | 'active' | 'paused' | 'recycled';
export interface SegmentUpdate { mode: 'manual' | 'routine'; freq: 'daily' | 'weekly' | 'monthly'; earliest: string; keep: number }
export interface SegmentVersion { version: number; basetime: string; users: number | null; status: 'running' | 'success' | 'failed' | 'cancelled'; computed_at: string; ms: number; trigger: 'manual' | 'routine' | 'backfill'; error: string | null; stale?: boolean }
export interface SegmentCreate { name: string; description?: string; kind?: 'rule' | 'result'; rule: UsersQuery; update?: Partial<SegmentUpdate>; tz?: string }
export interface Segment extends SegmentCreate {
  id: string; project: string; kind: 'rule' | 'result'; status: SegmentStatus; owner: string; created_at: string; updated_at: string; update: SegmentUpdate; tz: string;
  approval: { status: 'none' | 'pending' | 'approved' | 'rejected'; by: string | null; at: string | null; note: string };
  latest: SegmentVersion | null; versions?: SegmentVersion[]; lineage: { up: string[]; down: string[] }; recycled_at: string | null;
}
export interface SegmentPreview { users: number; profile: Record<string, { value: string; users: number }[]>; sql: string; warnings: string[] }

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired';
export interface Approval { id: string; type: 'export' | 'segment' | 'push' | 'reveal_l2' | 'reveal_l3' | 'agent_proposal'; requester: string; approver_roles: string[]; summary: string; detail: Record<string, unknown>; object: { kind: string; id: string }; status: ApprovalStatus; created_at: string; expires_at: string; decided_at: string | null; decided_by: string | null; note: string }
export type ExportStatus = 'pending_approval' | 'approved' | 'rejected' | 'generated' | 'downloaded' | 'expired';
export interface ExportRequest { kind: 'users' | 'segment_members'; rule?: UsersQuery; segment_id?: string; basetime?: string; columns?: string[]; purpose: string; retention_days?: number }
export interface Export { id: string; kind: string; requester: string; purpose: string; retention_days: number; rows_estimate: number; rows: number | null; columns: string[]; masked: Record<string, string>; status: ExportStatus; approval_id: string; watermark_id: string | null; created_at: string; approved_at: string | null; expires_at: string | null; generated_at: string | null; downloaded_at: string | null; note: string; what?: string }
