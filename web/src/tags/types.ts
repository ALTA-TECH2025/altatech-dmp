// 與 openapi TagCreate／Tag、Push／Delivery、Destination 對齊
import type { UsersQuery } from '@/users/types';

export type TagStatus = 'draft' | 'pending_approval' | 'active' | 'paused' | 'recycled';
export interface TagUpdate { mode: 'manual' | 'routine'; freq: 'daily' | 'weekly' | 'monthly'; earliest: string; keep: number }
export interface TagValue { value: string; rule: UsersQuery }
export interface TagVersion { version: number; basetime: string; users: number | null; status: 'running' | 'success' | 'failed'; computed_at: string; ms: number; trigger: string; error: string | null; distribution: { value: string; users: number }[] }
export type TagKind = 'values' | 'sql' | 'metric' | 'firstlast' | 'rfm';
export interface TagSpec { event: string; window_days?: number; buckets?: number[] | null; agg?: 'count' | 'sum' | 'avg' | 'max' | 'min' | 'days'; field?: string; which?: 'first' | 'last'; feature?: 'date' | 'days_since' | 'field'; amount_field?: string; bins?: number; output?: 'score' | 'segment' }
export interface TagCreate { name: string; key?: string; directory?: string; description?: string; dtype: 'string' | 'number' | 'bool'; kind: TagKind; values?: TagValue[]; sql?: string; spec?: TagSpec; project?: string; update?: Partial<TagUpdate>; fields?: Record<string, string> }
export interface Tag extends TagCreate {
  id: string; status: TagStatus; owner: string; created_at: string; updated_at: string; update: TagUpdate; values: TagValue[]; sql: string; spec?: TagSpec; fields: Record<string, string>;
  approval: { status: 'none' | 'pending' | 'approved' | 'rejected'; by: string | null; at: string | null; note: string; id?: string };
  latest: TagVersion | null; versions?: TagVersion[]; lineage: { up: string[]; down: string[] }; recycled_at: string | null; warnings?: string[];
}
export interface TagCustomFields { fields: { key: string; cn: string; type: 'text' | 'select'; options?: string[] }[]; directories: string[] }

export type DestinationStatus = 'active' | 'paused';
export interface Destination {
  id: string; name: string; type: 'webhook' | 'kafka' | 'gcs'; url: string; secret_ref: string; secret: string; identifier: 'customer_code' | 'one_id'; allowed_tags: string[]; masking_level: 'L1' | 'L2'; ip_allowlist: string[]; mtls: boolean; note: string;
  status: DestinationStatus; owner: string; created_at: string; updated_at: string; first_push_at: string | null; consecutive_failures: number; rotated_at: string | null;
  stats: { ok_7d: number | null; deliveries_7d: number; p95_ms: number | null; last_delivery_at: string | null };
}
export type PushStatus = 'pending_approval' | 'active' | 'paused' | 'rejected' | 'deleted';
export interface PushResult { version: number; added: number; removed: number; batches: number; ok: number; failed: number; destinations?: Record<string, string> }
export interface Push {
  id: string; segment_id: string; segment_name: string; destination_ids: string[]; mode: 'delta' | 'full'; schedule: 'now' | 'after_batch' | 'cron'; cron: string; include_tags: string[];
  status: PushStatus; approval_id: string | null; owner: string; created_at: string; updated_at: string; last_delivered_version: number | null; last_run_at: string | null; last_result: PushResult | null; runs: number; deliveries?: Delivery[];
}
export type DeliveryStatus = 'ok' | 'retrying' | 'failed' | 'dlq';
export interface Delivery {
  id: string; push_id: string | null; segment_id: string; destination_id: string; version: number; mode: 'delta' | 'full' | 'test'; event: string; batch: { index: number; total: number; size: number }; members: number;
  status: DeliveryStatus; attempts: number; next_retry_at: string | null; response: { status: number | null; ms: number; body: string } | null; created_at: string; updated_at: string; replay_of: string | null;
}
