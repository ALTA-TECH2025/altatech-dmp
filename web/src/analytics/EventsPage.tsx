// 事件分析：多指標（事件 × 聚合 × 欄位）、全局篩選、分組（≤3）、粒度、時間、對比上期、Top N → POST /v1/analytics/events。
// 結果：時間粒度 → 折線；合計 + 分組 → 長條；合計不分組 → 數字卡；表格；SQL／血緣可展開；K 匿名抑制以空心點與「< K」呈現。
import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { dedupe } from './FunnelPage';
import { LineChart } from './LineChart';
import { fmtNum, groupLabel, toSeries } from './series';
import type { Catalog, Cond, EventsMetric, EventsQuery, EventsResult } from './types';

const AGG_CN: Record<string, string> = { total: '總次數', users: '觸發用戶數', avg_per_user: '人均次數', uniq: '去重數', sum: '數值求和', avg: '數值均值', avg_val: '數值人均值', max: '最大值', min: '最小值', p25: '下四分位', p50: '中位數', p75: '上四分位', p90: 'P90', mau: 'MAU（按天）' };
const NUM_AGGS = new Set(['sum', 'avg', 'avg_val', 'max', 'min', 'p25', 'p50', 'p75', 'p90']);
const GRAINS: [EventsQuery['time']['grain'], string][] = [['minute', '分鐘'], ['hour', '小時'], ['day', '日'], ['week', '週'], ['month', '月'], ['total', '合計']];

export function EventsPage() {
  return <RequireGrant module="events" action="use"><EventsInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }

function EventsInner() {
  const { me } = useAuth();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<EventsQuery>({ metrics: [{ event: '', agg: 'total' }], filters: [], group_by: [], time: { from: today(-14), to: today(), grain: 'day' } });
  const [res, setRes] = useState<EventsResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);

  const setMetric = (i: number, m: Partial<EventsMetric>) => setQ({ ...q, metrics: q.metrics.map((x, j) => (j === i ? { ...x, ...m } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const body: EventsQuery = { ...q, metrics: q.metrics.map((m) => ({ ...m, event: m.event || null, field: NUM_AGGS.has(m.agg) || m.agg === 'uniq' ? m.field : undefined })), filters: q.filters?.length ? q.filters : undefined, group_by: q.group_by?.length ? q.group_by : undefined };
      setRes(await api.post<EventsResult>('/v1/analytics/events', body));
    } catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  const k = me?.scope.k_anonymity ?? 20;
  const aliases = q.metrics.map((m, i) => m.alias || `m${i + 1}`);
  const labels = Object.fromEntries(q.metrics.map((m, i) => [m.alias || `m${i + 1}`, `${m.event || '任一事件'} ${AGG_CN[m.agg]}${m.field ? `(${m.field})` : ''}`]));
  const series = useMemo(() => (res ? toSeries(res, aliases, labels) : []), [res]);   // eslint-disable-line react-hooks/exhaustive-deps
  const numFields = cat ? [...cat.event_columns.filter((c) => c.type === 'number'), { name: 'props.score', cn: '屬性 score', type: 'number' }] : [];
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  const filterFields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn, type: d.type })), ...cat.user_props.filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}`, type: u.type === 'enum' ? 'string' : u.type }))] : []);
  const isTotal = q.time.grain === 'total';
  return (
    <div className="page">
      <div className="page-h"><div><h1>事件分析</h1><p>依事件、屬性、用戶分群交叉分析次數、人數、金額等指標。預設不抽樣、精確去重；分組人數少於 {k} 人的列會被抑制。</p></div></div>
      <div className="card">
        <div className="card-h"><h3>指標</h3><div className="right">{q.metrics.length < 10 && <button className="btn-ghost sm" onClick={() => setQ({ ...q, metrics: [...q.metrics, { event: '', agg: 'total' }] })}>＋ 指標</button>}</div></div>
        <div className="stack">
          {q.metrics.map((m, i) => (
            <div className="row" key={i} style={{ marginTop: 0 }}>
              <span className="pill blue">{m.alias || `m${i + 1}`}</span>
              <select className="grow" value={m.event ?? ''} aria-label={`指標 ${i + 1} 事件`} onChange={(e) => setMetric(i, { event: e.target.value })}><option value="">任一事件</option>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}</select>
              <select value={m.agg} aria-label={`指標 ${i + 1} 聚合`} onChange={(e) => setMetric(i, { agg: e.target.value })}>{Object.entries(AGG_CN).map(([v, cn]) => <option key={v} value={v}>{cn}</option>)}</select>
              {(NUM_AGGS.has(m.agg) || m.agg === 'uniq') && <select value={m.field ?? ''} aria-label={`指標 ${i + 1} 欄位`} onChange={(e) => setMetric(i, { field: e.target.value })}><option value="">選欄位…</option>{(m.agg === 'uniq' ? filterFields.map((f) => ({ name: f.v, cn: f.cn })) : numFields).map((f) => <option key={f.name} value={f.name}>{f.cn}</option>)}</select>}
              <input style={{ width: 90 }} placeholder="別名" value={m.alias ?? ''} onChange={(e) => setMetric(i, { alias: e.target.value })} />
              {q.metrics.length > 1 && <button className="btn-ghost sm danger" onClick={() => setQ({ ...q, metrics: q.metrics.filter((_, j) => j !== i) })}>✕</button>}
            </div>
          ))}
        </div>
        <div className="card-h" style={{ marginTop: 14 }}><h3>篩選</h3><div className="right"><button className="btn-ghost sm" onClick={() => setQ({ ...q, filters: [...(q.filters ?? []), { field: 'platform', op: 'eq', value: '' }] })}>＋ 條件</button></div></div>
        <div className="stack">
          {(q.filters ?? []).map((f, i) => {
            const cur = filterFields.find((x) => x.v === f.field); const ops = cat?.ops[cur?.type === 'number' ? 'number' : cur?.type === 'bool' ? 'bool' : 'string'] ?? [];
            const set = (nf: Partial<Cond>) => setQ({ ...q, filters: (q.filters ?? []).map((x, j) => (j === i ? { ...x, ...nf } : x)) });
            return (
              <div className="row" key={i} style={{ marginTop: 0 }}>
                <select value={f.field} aria-label="篩選欄位" onChange={(e) => set({ field: e.target.value, op: 'eq', value: '' })}>{filterFields.map((x) => <option key={x.v} value={x.v}>{x.cn}</option>)}</select>
                <select value={f.op} aria-label="運算子" onChange={(e) => set({ op: e.target.value })}>{ops.map((o) => <option key={o} value={o}>{o}</option>)}</select>
                {!['set', 'nset', 'true', 'false'].includes(f.op) && <input style={{ width: 160 }} aria-label="值" value={String(f.value ?? '')} placeholder={['in', 'nin'].includes(f.op) ? '逗號分隔' : ''} onChange={(e) => set({ value: cur?.type === 'number' && !['in', 'nin', 'between'].includes(f.op) ? Number(e.target.value) : ['in', 'nin', 'between'].includes(f.op) ? e.target.value.split(',').map((s) => (cur?.type === 'number' ? Number(s) : s.trim())) : e.target.value })} />}
                <button className="btn-ghost sm danger" onClick={() => setQ({ ...q, filters: (q.filters ?? []).filter((_, j) => j !== i) })}>✕</button>
              </div>);
          })}
        </div>
        <div className="grid c3" style={{ marginTop: 14 }}>
          <label>分組（最多 3）<select multiple value={q.group_by ?? []} onChange={(e) => setQ({ ...q, group_by: Array.from(e.target.selectedOptions).map((o) => o.value).slice(0, 3) })}>{dimOptions.map((o) => <option key={o.v} value={o.v}>{o.cn}</option>)}</select></label>
          <label>粒度<select value={q.time.grain} onChange={(e) => setQ({ ...q, time: { ...q.time, grain: e.target.value as EventsQuery['time']['grain'] } })}>{GRAINS.map(([v, cn]) => <option key={v} value={v}>{cn}</option>)}</select></label>
          <label>Top N 分組值<input type="number" min={1} value={q.top_n ?? ''} placeholder="全部" onChange={(e) => setQ({ ...q, top_n: e.target.value ? Number(e.target.value) : undefined })} /></label>
          <label>開始<input type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} /></label>
          <label>結束<input type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} /></label>
          <label>對比<select value={q.compare ?? ''} onChange={(e) => setQ({ ...q, compare: (e.target.value || undefined) as EventsQuery['compare'] })}><option value="">無</option><option value="previous_period">上一期</option></select></label>
          <label>抽樣<select value={q.sample ?? ''} onChange={(e) => setQ({ ...q, sample: e.target.value ? Number(e.target.value) : undefined })}><option value="">不抽樣（精確）</option><option value="0.5">1/2</option><option value="0.1">1/10</option><option value="0.01">1/100</option></select></label>
          <label>分群<input placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} /></label>
        </div>
        <div className="row"><button className="btn-primary" disabled={busy || !q.metrics.every((m) => (!NUM_AGGS.has(m.agg) && m.agg !== 'uniq') || m.field)} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</button>{res && <button className="btn-ghost" onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</button>}{err && <span className="alert">{err}</span>}</div>
      </div>
      {res && (
        <>
          {res.warnings.length > 0 && <div className="note">{res.warnings.join('；')}</div>}
          <div className="card" style={{ marginTop: 16 }}>
            <div className="card-h"><h3>{isTotal ? (q.group_by?.length ? '分組對比' : '合計') : '趨勢'}</h3><span className="sub">{res.cost.ms} ms · {res.cost.layer === 'L1' ? '日聚合（物化視圖）' : '明細'}{q.sample ? ` · 抽樣 ${q.sample}` : ' · 不抽樣'} · {res.rows.length} 列</span></div>
            {isTotal && !q.group_by?.length ? (
              <div className="tiles">{aliases.map((a) => { const r = res.rows[0]; return <div className="tile" key={a}><span className="sub">{labels[a]}</span><b>{r?.suppressed ? `< ${k}` : fmtNum(r?.[a] as number)}</b>{q.compare && <span className="sub">上期 {fmtNum(r?.[`${a}_prev`] as number)}</span>}</div>; })}</div>
            ) : <LineChart series={series} kind={isTotal ? 'bar' : 'line'} />}
          </div>
          <div className="card flush" style={{ marginTop: 16 }}><div className="tbl-wrap"><table className="tbl">
            <thead><tr>{res.columns.filter((c) => c !== '_k' && c !== 'suppressed').map((c) => <th key={c} className={aliases.includes(c) || c.endsWith('_prev') ? 'num' : ''}>{c === 'bucket' ? '時間' : c.startsWith('g') && c.length === 2 ? (q.group_by?.[Number(c[1]) - 1] ?? c) : labels[c] ?? (c.endsWith('_prev') ? `${labels[c.slice(0, -5)] ?? c}（上期）` : c)}</th>)}</tr></thead>
            <tbody>{res.rows.slice(0, 500).map((r, i) => <tr key={i} className={r.suppressed ? 'warnrow' : ''}>{res.columns.filter((c) => c !== '_k' && c !== 'suppressed').map((c) => <td key={c} className={typeof r[c] === 'number' ? 'num mono' : 'mono'}>{c === 'bucket' ? String(r[c]) : c.startsWith('g') && c.length === 2 ? groupLabel({ [c]: r[c] }) : r.suppressed ? `< ${k}` : typeof r[c] === 'number' ? fmtNum(r[c] as number) : String(r[c] ?? '—')}</td>)}</tr>)}</tbody>
          </table></div>{res.rows.length > 500 && <p className="note">只顯示前 500 列；完整結果請匯出（走審批）。</p>}</div>
          {showSql && <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>SQL</h3><span className="sub">{res.lineage.join(' · ')}</span></div><pre className="code">{res.sql}</pre></div>}
        </>
      )}
    </div>
  );
}
