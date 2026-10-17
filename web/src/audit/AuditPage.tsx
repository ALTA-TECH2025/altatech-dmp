// 日誌統計：/v1/audit（行銷只看自己；admin／審計角色全看）；篩選事件名／使用者；統計分佈待第二階段（query_log 彙總）。
import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { RequireGrant } from '@/auth/RequireGrant';

type Row = { event: string; at: number; user?: string; module?: string; [k: string]: unknown };

export function AuditPage() {
  return <RequireGrant module="audit"><Inner /></RequireGrant>;
}

function Inner() {
  const [rows, setRows] = useState<Row[]>([]); const [err, setErr] = useState<string | null>(null); const [q, setQ] = useState(''); const [n, setN] = useState(200);
  useEffect(() => { api.get<Row[]>(`/v1/audit?n=${n}`).then((r) => setRows([...r].reverse())).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [n]);
  const list = useMemo(() => rows.filter((r) => !q || r.event.includes(q) || String(r.user ?? '').includes(q) || String(r.module ?? '').includes(q)), [rows, q]);
  const byEvent = useMemo(() => { const m = new Map<string, number>(); for (const r of rows) m.set(r.event, (m.get(r.event) ?? 0) + 1); return Array.from(m.entries()).sort((a, b) => b[1] - a[1]).slice(0, 8); }, [rows]);
  return (
    <div className="page">
      <div className="page-h"><div><h1>日誌統計</h1><p>所有登入、查詢、匯出、審批、治理變更都寫審計；行銷只看自己的紀錄，審計角色全看。查詢效能與掃描量彙總（query_log）待第二階段。</p></div><span className="pill gray">統計分佈：第二階段</span></div>
      {err && <div className="alert">{err}</div>}
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><input placeholder="篩選事件／使用者／模組" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 260px' }} /><select value={n} onChange={(e) => setN(Number(e.target.value))}>{[100, 200, 500, 1000].map((x) => <option key={x} value={x}>最近 {x} 筆</option>)}</select></div>
      <div className="tiles" style={{ marginBottom: 16 }}>{byEvent.map(([k, v]) => <div className="tile" key={k}><span className="sub mono">{k}</span><b>{v}</b></div>)}</div>
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>時間</th><th>事件</th><th>使用者</th><th>模組</th><th>內容</th></tr></thead>
        <tbody>{list.map((r, i) => <tr key={i}><td className="mono" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>{new Date(r.at * 1000).toISOString().slice(0, 19).replace('T', ' ')}</td><td className="mono">{r.event}</td><td className="mono" style={{ fontSize: 11.5 }}>{String(r.user ?? '')}</td><td>{String(r.module ?? '')}</td><td className="mono" style={{ fontSize: 11, maxWidth: 520, overflowWrap: 'anywhere' }}>{JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k]) => !['event', 'at', 'user', 'module'].includes(k)))).slice(0, 300)}</td></tr>)}</tbody></table></div></div>
    </div>
  );
}
