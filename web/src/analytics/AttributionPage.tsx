// 歸因分析：轉化目標（事件、條件、價值欄位）× 待歸因觸點（事件、別名、條件、自訂權重）× 拆分維度 × 多模型（首次／末次／線性／位置／時間衰減／自訂）× 回溯窗口
// → POST /v1/analytics/attribution → 模型對比表（轉化數、占比、價值）+ 各模型占比長條 + 觸點 → 轉化者用戶列表。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { dedupe } from './FunnelPage';
import { LineChart } from './LineChart';
import { fmtNum, type Series } from './series';
import type { AttributionModel, AttributionQuery, AttributionResult, AttributionTouch, Catalog, Cond } from './types';

export function AttributionPage() {
  return <RequireGrant module="attribution" action="use"><AttributionInner /></RequireGrant>;
}

export const MODEL_CN: Record<AttributionModel, string> = { first: '首次', last: '末次', linear: '線性', position: '位置（U 型）', decay: '時間衰減', custom: '自訂權重' };
function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }

/** 結果 → 各模型占比長條序列（x = 觸點[·拆分]，一個模型一條序列）。純函式，有單元測試。 */
export function shareSeries(res: AttributionResult): Series[] {
  return res.models.map((m) => ({ key: m, label: MODEL_CN[m], metric: m, group: '', points: res.rows.map((r) => ({ x: r.split ? `${r.touch} · ${r.split}` : r.touch, y: r.suppressed ? null : (r.models[m]?.share ?? null) === null ? null : Math.round((r.models[m].share as number) * 1000) / 10, suppressed: r.suppressed })) }));
}

function AttributionInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<AttributionQuery>({ target: { event: '' }, touches: [{ event: '' }], models: ['first', 'last', 'linear'], position_weights: { first: 0.4, last: 0.4 }, half_life_days: 7, window_days: 7, filters: [], time: { from: today(-30), to: today() } });
  const [res, setRes] = useState<AttributionResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const bm = useBookmarkOpen<typeof q>('attribution', setQ);
  const [metric, setMetric] = useState<'conversions' | 'value'>('conversions');
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  const setTouch = (i: number, t: Partial<AttributionTouch>) => setQ({ ...q, touches: q.touches.map((x, j) => (j === i ? { ...x, ...t } : x)) });
  const run = async () => {
    setBusy(true); setErr(null);
    try { const body = { ...q, filters: q.filters?.length ? q.filters : undefined, split: q.split || undefined, touches: q.touches.map((t) => ({ ...t, alias: t.alias || undefined })) }; setRes(await api.post<AttributionResult>('/v1/analytics/attribution', body)); setSent(body); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); } finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const ready = !!q.target.event && q.touches.length > 0 && q.touches.every((t) => t.event) && q.models.length > 0;
  const splitOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.type !== 'number' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn }))] : []);
  const series = useMemo(() => (res ? shareSeries(res) : []), [res]);
  const evCn = (n: string) => cat?.events.find((e) => e.name === n)?.cn ?? n;
  const toggleModel = (m: AttributionModel) => setQ({ ...q, models: q.models.includes(m) ? q.models.filter((x) => x !== m) : [...q.models, m] });
  return (
    <div className="page">
      <div className="page-h"><div><h1>歸因分析</h1><p>把每次轉化分給轉化前窗口內的觸點：首次、末次、線性、位置（U 型）、時間衰減、自訂權重，可同時比較；跨 Web／App 靠 OneID 歸戶。被歸到的轉化用戶少於 {k} 人的觸點會被抑制。</p></div></div>
      <div className="grid c21">
        <div className="card">
          <div className="card-h"><h3>轉化目標</h3></div>
          <div className="row" style={{ marginTop: 0 }}>
            <select className="grow" value={q.target.event} aria-label="轉化事件" onChange={(e) => setQ({ ...q, target: { ...q.target, event: e.target.value } })}><option value="">選事件…</option>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}</select>
            <select value={q.target.value_field ?? ''} aria-label="價值欄位" onChange={(e) => setQ({ ...q, target: { ...q.target, value_field: e.target.value || undefined } })}><option value="">每次轉化計 1</option>{cat?.event_columns.filter((c) => c.type === 'number' && c.name !== '$event_hour').map((c) => <option key={c.name} value={c.name}>價值 = {c.cn}</option>)}</select>
          </div>
          <div className="card-h" style={{ marginTop: 14 }}><h3>待歸因觸點</h3><div className="right">{q.touches.length < 10 && <button className="btn-ghost sm" onClick={() => setQ({ ...q, touches: [...q.touches, { event: '' }] })}>＋ 觸點</button>}</div></div>
          <div className="stack">{q.touches.map((t, i) => (
            <div className="row" key={i} style={{ marginTop: 0 }}>
              <span className="pill blue">{i + 1}</span>
              <select className="grow" value={t.event} aria-label={`觸點 ${i + 1} 事件`} onChange={(e) => setTouch(i, { event: e.target.value })}><option value="">選事件…</option>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}</select>
              <input style={{ width: 110 }} placeholder="別名" aria-label={`觸點 ${i + 1} 別名`} value={t.alias ?? ''} onChange={(e) => setTouch(i, { alias: e.target.value })} />
              {q.models.includes('custom') && <input type="number" min={0} step={0.1} style={{ width: 80 }} aria-label={`觸點 ${i + 1} 權重`} value={t.weight ?? 1} onChange={(e) => setTouch(i, { weight: Number(e.target.value) })} />}
              <TouchFilter cat={cat} touch={t} onChange={(f) => setTouch(i, { filters: f })} />
              {q.touches.length > 1 && <button className="btn-ghost sm danger" onClick={() => setQ({ ...q, touches: q.touches.filter((_, j) => j !== i) })}>✕</button>}
            </div>))}</div>
          <div className="card-h" style={{ marginTop: 14 }}><h3>模型</h3></div>
          <div className="row" style={{ marginTop: 0 }}>{(Object.keys(MODEL_CN) as AttributionModel[]).map((m) => <label key={m} className="chk" style={{ flexDirection: 'row' }}><input type="checkbox" checked={q.models.includes(m)} onChange={() => toggleModel(m)} /> {MODEL_CN[m]}</label>)}</div>
          <div className="grid c3" style={{ marginTop: 14 }}>
            <label>回溯窗口（天）<input type="number" min={1} max={90} aria-label="回溯窗口" value={q.window_days} onChange={(e) => setQ({ ...q, window_days: Number(e.target.value) || 7 })} /></label>
            <label>位置：首次占<input type="number" min={0} max={1} step={0.05} disabled={!q.models.includes('position')} value={q.position_weights?.first ?? 0.4} onChange={(e) => setQ({ ...q, position_weights: { first: Number(e.target.value), last: q.position_weights?.last ?? 0.4 } })} /></label>
            <label>位置：末次占<input type="number" min={0} max={1} step={0.05} disabled={!q.models.includes('position')} value={q.position_weights?.last ?? 0.4} onChange={(e) => setQ({ ...q, position_weights: { first: q.position_weights?.first ?? 0.4, last: Number(e.target.value) } })} /></label>
            <label>半衰期（天）<input type="number" min={0.1} step={0.5} disabled={!q.models.includes('decay')} value={q.half_life_days ?? 7} onChange={(e) => setQ({ ...q, half_life_days: Number(e.target.value) || 7 })} /></label>
            <label>觸點拆分<select value={q.split ?? ''} aria-label="拆分" onChange={(e) => setQ({ ...q, split: e.target.value || undefined })}><option value="">不拆分</option>{splitOptions.map((o) => <option key={o.v} value={o.v}>{o.cn}</option>)}</select></label>
            <label>分群<input placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} /></label>
            <label>轉化開始<input type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} /></label>
            <label>轉化結束<input type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} /></label>
          </div>
          <div className="row"><button className="btn-primary" disabled={!ready || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</button>{res && <button className="btn-ghost" onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</button>}{res && <SaveCardButton kind="attribution" query={sent} />}{err && <span className="alert">{err}</span>}</div>
        </div>
        <div className="card"><div className="card-h"><h3>說明</h3></div><ul className="priv">
          <li>每次轉化往前看窗口內的觸點：首次給第一個、末次給最後一個、線性平分、位置首末各占設定比例、時間衰減依半衰期、自訂依觸點權重正規化。</li>
          <li>沒有任何觸點的轉化計入「未觸及」，不分給任何觸點。</li>
          <li>價值欄位給了就同時算歸因價值（例：成交金額）。</li>
          <li>點表格的觸點可下鑽該觸點的轉化者用戶列表。</li></ul></div>
      </div>
      {res && (
        <>
          {res.warnings.length > 0 && <div className="note">{res.warnings.join('；')}</div>}
          <div className="tiles" style={{ marginTop: 16 }}>
            <div className="tile"><span className="sub">轉化次數</span><b>{fmtNum(res.summary.conversions)}</b><span className="sub">{fmtNum(res.summary.converters)} 人</span></div>
            <div className="tile"><span className="sub">有觸點的轉化</span><b>{fmtNum(res.summary.touched)}</b><span className="sub">未觸及 {fmtNum(res.summary.untouched)}</span></div>
            <div className="tile"><span className="sub">平均觸點數</span><b>{res.summary.avg_touches === null ? '—' : res.summary.avg_touches.toFixed(2)}</b></div>
            {q.target.value_field && <div className="tile"><span className="sub">轉化價值</span><b>{fmtNum(res.summary.value)}</b></div>}
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <div className="card-h"><h3>各模型貢獻占比（%）</h3><span className="sub">{res.cost.ms} ms · {res.rows.length} 個觸點{q.split ? ' × 拆分值' : ''}</span></div>
            <LineChart series={series} kind="bar" />
          </div>
          <div className="card flush" style={{ marginTop: 16 }}><div className="row" style={{ padding: '10px 12px 0', marginTop: 0 }}><span className="sub">顯示</span><select value={metric} aria-label="顯示指標" onChange={(e) => setMetric(e.target.value as 'conversions' | 'value')}><option value="conversions">歸因轉化數</option><option value="value" disabled={!q.target.value_field}>歸因價值</option></select></div>
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>觸點</th>{q.split && <th>{splitOptions.find((o) => o.v === q.split)?.cn ?? q.split}</th>}<th className="num">轉化用戶</th>{res.models.map((m) => <th key={m} className="num">{MODEL_CN[m]}</th>)}{can('users', 'use') && <th />}</tr></thead>
              <tbody>{res.rows.map((r, i) => (
                <tr key={i} className={r.suppressed ? 'warnrow' : ''}>
                  <td><b>{r.touch}</b><span className="sub mono">{r.event}</span></td>{q.split && <td>{r.split || '（空）'}</td>}
                  <td className="num mono">{r.suppressed ? `< ${k}` : fmtNum(r.users)}</td>
                  {res.models.map((m) => { const v = r.models[m]; return <td key={m} className="num mono">{r.suppressed || !v ? '—' : metric === 'value' ? fmtNum(v.value) : `${fmtNum(v.conversions, 1)}${v.share !== null ? ` (${(v.share * 100).toFixed(1)}%)` : ''}`}</td>; })}
                  {can('users', 'use') && <td className="num">{!r.suppressed && <button className="btn-ghost sm" onClick={() => nav('/users', { state: { source: { type: 'attribution', query: q, touch: r.touch, group: r.split ?? undefined, label: `歸因「${evCn(q.target.event)}」觸點 ${r.touch}${r.split ? ` · ${r.split}` : ''} 的轉化者` } } })}>用戶</button>}</td>}
                </tr>))}</tbody>
            </table></div></div>
          {showSql && <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>SQL</h3><span className="sub">{res.lineage.join(' · ')}</span></div><pre className="code">{res.sql}</pre></div>}
        </>
      )}
    </div>
  );
}

function TouchFilter({ cat, touch, onChange }: { cat: Catalog | null; touch: AttributionTouch; onChange: (f: Cond[] | undefined) => void }) {
  const fields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn, type: d.type }))] : []);
  const f = touch.filters?.[0];
  if (!f) return <button className="btn-ghost sm" onClick={() => onChange([{ field: 'platform', op: 'eq', value: '' }])}>＋ 條件</button>;
  const cur = fields.find((x) => x.v === f.field); const ops = cat?.ops[cur?.type === 'number' ? 'number' : cur?.type === 'bool' ? 'bool' : 'string'] ?? [];
  return <>
    <select value={f.field} aria-label="觸點條件欄位" onChange={(e) => onChange([{ field: e.target.value, op: 'eq', value: '' }])}>{fields.map((x) => <option key={x.v} value={x.v}>{x.cn}</option>)}</select>
    <select value={f.op} aria-label="觸點條件運算子" onChange={(e) => onChange([{ ...f, op: e.target.value }])}>{ops.map((o) => <option key={o} value={o}>{o}</option>)}</select>
    {!['set', 'nset', 'true', 'false'].includes(f.op) && <input style={{ width: 120 }} aria-label="觸點條件值" value={String(f.value ?? '')} onChange={(e) => onChange([{ ...f, value: cur?.type === 'number' ? Number(e.target.value) : e.target.value }])} />}
    <button className="btn-ghost sm danger" onClick={() => onChange(undefined)}>✕</button>
  </>;
}
