// 漏斗分析：步驟（事件 + 篩選）、窗口、順序、關聯屬性（業務 ID）、口徑、分組、趨勢、時間 → POST /v1/analytics/funnel。
// 圖：自繪 SVG 漏斗（每步一條橫柱，寬度 = 相對第一步）；表：人數、相對上一步、相對第一步；SQL 可展開；K 匿名抑制列以「< K」顯示。
import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { FunnelChart } from './FunnelChart';
import type { Catalog, FunnelQuery, FunnelResult, FunnelStep } from './types';

const WINDOW_PRESETS: [number, 'minute' | 'hour' | 'day', string][] = [[30, 'minute', '30 分鐘'], [1, 'day', '1 天'], [3, 'day', '3 天'], [7, 'day', '7 天'], [14, 'day', '14 天'], [30, 'day', '30 天'], [90, 'day', '90 天']];

export function FunnelPage() {
  return <RequireGrant module="funnel" action="use"><FunnelInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }

function FunnelInner() {
  const { me } = useAuth();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<FunnelQuery>({ steps: [{ event: '' }, { event: '' }], window: { value: 7, unit: 'day' }, order: 'loose', metric: 'users', time: { from: today(-30), to: today() } });
  const [res, setRes] = useState<FunnelResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);

  const setStep = (i: number, s: Partial<FunnelStep>) => setQ({ ...q, steps: q.steps.map((x, j) => (j === i ? { ...x, ...s } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try { setRes(await api.post<FunnelResult>('/v1/analytics/funnel', q)); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  const ready = q.steps.length >= 2 && q.steps.every((s) => s.event);
  const groups = useMemo(() => {
    if (!res) return [];
    const keys = new Map<string, FunnelResult['rows']>();
    for (const r of res.rows) { const k = `${r.bucket ?? ''}|${r.g1 ?? ''}`; keys.set(k, [...(keys.get(k) ?? []), r]); }
    return Array.from(keys.entries());
  }, [res]);
  const k = me?.scope.k_anonymity ?? 20;
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  return (
    <div className="page">
      <div className="page-h"><div><h1>漏斗分析</h1><p>步驟可含線下事件（開戶、入金、成交）；關聯屬性可用業務 ID（訂單編號、帳戶）把多步串成同一流程；窗口 1 分鐘到 90 天；結果不抽樣，分組人數少於 {k} 人的列會被抑制。</p></div></div>
      <div className="grid c21">
        <div className="card">
          <div className="card-h"><h3>步驟</h3><div className="right">{q.steps.length < 10 && <button className="btn-ghost sm" onClick={() => setQ({ ...q, steps: [...q.steps, { event: '' }] })}>＋ 步驟</button>}</div></div>
          <div className="stack">
            {q.steps.map((s, i) => (
              <div className="row" key={i} style={{ marginTop: 0 }}>
                <span className="pill blue">{i + 1}</span>
                <select className="grow" value={s.event} onChange={(e) => setStep(i, { event: e.target.value })} aria-label={`第 ${i + 1} 步事件`}>
                  <option value="">選事件…</option>
                  {cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}
                </select>
                <input style={{ width: 120 }} placeholder="別名" value={s.alias ?? ''} onChange={(e) => setStep(i, { alias: e.target.value })} />
                <StepFilter cat={cat} step={s} onChange={(f) => setStep(i, { filters: f })} />
                {q.steps.length > 2 && <button className="btn-ghost sm danger" onClick={() => setQ({ ...q, steps: q.steps.filter((_, j) => j !== i) })}>✕</button>}
              </div>
            ))}
          </div>
          <div className="grid c3" style={{ marginTop: 14 }}>
            <label>轉換窗口<select value={`${q.window.value}:${q.window.unit}`} onChange={(e) => { const [v, u] = e.target.value.split(':'); setQ({ ...q, window: { value: Number(v), unit: u as FunnelQuery['window']['unit'] } }); }}>
              {WINDOW_PRESETS.map(([v, u, cn]) => <option key={cn} value={`${v}:${u}`}>{cn}</option>)}</select></label>
            <label>順序<select value={q.order} onChange={(e) => setQ({ ...q, order: e.target.value as 'loose' | 'strict' })}><option value="loose">寬鬆（中間可有其他事件）</option><option value="strict">嚴格（其他步驟事件介入即中斷）</option></select></label>
            <label>關聯屬性（業務 ID）<select value={q.relation ?? ''} onChange={(e) => setQ({ ...q, relation: e.target.value || undefined, metric: e.target.value ? q.metric : 'users' })}>
              <option value="">不串接（同一用戶即可）</option><option value="entity_id">業務 ID（entity_id）</option><option value="props.order_no">訂單編號（props.order_no）</option><option value="symbol">商品代號</option></select></label>
            <label>口徑<select value={q.metric} disabled={!q.relation} onChange={(e) => setQ({ ...q, metric: e.target.value as 'users' | 'times' })}><option value="users">用戶數</option><option value="times">流程數（需關聯屬性）</option></select></label>
            <label>分組<select value={q.group_by?.[0] ?? ''} onChange={(e) => setQ({ ...q, group_by: e.target.value ? [e.target.value] : undefined })}><option value="">不分組</option>{dimOptions.map((o) => <option key={o.v} value={o.v}>{o.cn}</option>)}</select></label>
            <label>趨勢<select value={q.trend ?? ''} onChange={(e) => setQ({ ...q, trend: (e.target.value || undefined) as FunnelQuery['trend'] })}><option value="">合計</option><option value="day">按日</option><option value="week">按週</option><option value="month">按月</option></select></label>
            <label>開始<input type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} /></label>
            <label>結束<input type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} /></label>
            <label>分群<input placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} /></label>
          </div>
          <div className="row"><button className="btn-primary" disabled={!ready || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</button>{res && <button className="btn-ghost" onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</button>}{err && <span className="alert">{err}</span>}</div>
        </div>
        <div className="card"><div className="card-h"><h3>說明</h3></div><ul className="priv">
          <li>每步人數 = 在窗口內依序完成到該步的用戶數（ClickHouse windowFunnel）；分組值取自該用戶第一步事件。</li>
          <li>關聯屬性指定後，同一用戶不同業務 ID 的事件各算一條流程；「流程數」口徑數流程條數，「用戶數」仍去重。</li>
          <li>線下事件由數據中台批次導入後與 App／Web 事件同表，可直接作為任一步。</li>
          <li>結果附 SQL、血緣與成本；所有查詢寫審計。</li></ul></div>
      </div>
      {res && (
        <>
          {res.warnings.length > 0 && <div className="note">{res.warnings.join('；')}</div>}
          {groups.map(([key, rows]) => (
            <div className="card" style={{ marginTop: 16 }} key={key}>
              <div className="card-h"><h3>{key === '|' ? '整體' : key.replace('|', ' · ').replace(/^ · | · $/g, '')}</h3><span className="sub">{res.cost.ms} ms · {res.cost.layer}</span></div>
              <FunnelChart rows={rows} k={k} />
              <div className="tbl-wrap" style={{ marginTop: 10 }}><table className="tbl">
                <thead><tr><th>步驟</th><th>事件</th><th className="num">{q.metric === 'times' ? '流程數' : '用戶數'}</th><th className="num">相對上一步</th><th className="num">相對第一步</th></tr></thead>
                <tbody>{rows.map((r) => (
                  <tr key={r.step}><td>{r.step}. {r.alias}</td><td className="mono sub">{r.event}</td>
                    <td className="num mono">{r.suppressed ? `< ${k}` : (r.users ?? 0).toLocaleString()}</td>
                    <td className="num mono">{pct(r.rate_prev)}</td><td className="num mono">{pct(r.rate_first)}</td></tr>))}</tbody>
              </table></div>
            </div>
          ))}
          {showSql && <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>SQL</h3><span className="sub">{res.lineage.join(' · ')}</span></div><pre className="code">{res.sql}</pre></div>}
        </>
      )}
    </div>
  );
}

/** 事件欄位與 dimensions.yaml 可能指到同一欄（platform、channel、symbol）：以第一個為準。 */
export function dedupe<T extends { v: string }>(xs: T[]): T[] { const seen = new Set<string>(); return xs.filter((x) => (seen.has(x.v) ? false : (seen.add(x.v), true))); }

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`);

function StepFilter({ cat, step, onChange }: { cat: Catalog | null; step: FunnelStep; onChange: (f: FunnelStep['filters']) => void }) {
  const f = step.filters?.[0];
  const fields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn, type: d.type }))] : []);
  const cur = fields.find((x) => x.v === f?.field);
  const ops = cat ? cat.ops[cur?.type === 'number' ? 'number' : 'string'] : [];
  return (
    <span className="row" style={{ marginTop: 0, gap: 4 }}>
      <select value={f?.field ?? ''} aria-label="篩選欄位" onChange={(e) => onChange(e.target.value ? [{ field: e.target.value, op: 'eq', value: '' }] : undefined)}>
        <option value="">無篩選</option>{fields.map((x) => <option key={x.v} value={x.v}>{x.cn}</option>)}</select>
      {f && <select value={f.op} aria-label="運算子" onChange={(e) => onChange([{ ...f, op: e.target.value }])}>{ops.map((o) => <option key={o} value={o}>{o}</option>)}</select>}
      {f && !['set', 'nset'].includes(f.op) && <input style={{ width: 110 }} aria-label="值" value={String(f.value ?? '')} onChange={(e) => onChange([{ ...f, value: cur?.type === 'number' ? Number(e.target.value) : e.target.value }])} />}
    </span>
  );
}
