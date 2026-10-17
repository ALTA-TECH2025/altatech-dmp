// 分析頁「開啟書籤」：書籤頁以 navigate(path, { state: { bookmark } }) 進來 → 把查詢灌進頁面狀態並自動查詢一次。
import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import type { Bookmark } from './types';

export function normalizeQuery<T extends object>(base: T, kind: string, query: Record<string, unknown>): T {
  const q = { ...base, ...query } as Record<string, unknown>;
  if (kind === 'events' && Array.isArray(q.metrics)) q.metrics = (q.metrics as Record<string, unknown>[]).map((m) => ({ ...m, event: m.event ?? '' }));
  for (const k of ['filters', 'group_by']) if (q[k] === undefined && Array.isArray((base as Record<string, unknown>)[k])) q[k] = [];
  return q as T;
}

/** 回傳 pending：true 表示查詢已灌入、等頁面的 run() 跑一次；頁面跑完要呼叫 done()。 */
export function useBookmarkOpen<T extends object>(kind: string, setQ: (f: (cur: T) => T) => void): { pending: boolean; done: () => void; bookmark: Bookmark | null } {
  const loc = useLocation();
  const [pending, setPending] = useState(false);
  const bm = useRef<Bookmark | null>(null);
  useEffect(() => {
    const b = (loc.state as { bookmark?: Bookmark } | null)?.bookmark;
    if (b && b.kind === kind && b.query) { bm.current = b; setQ((cur) => normalizeQuery(cur, kind, b.query)); setPending(true); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return { pending, done: () => setPending(false), bookmark: bm.current };
}
