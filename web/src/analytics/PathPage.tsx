// 行為路徑：起點／終點事件（可加條件）、方向、步數、會話切割、節點設定（只留哪些事件／折成其他／依屬性拆分／合併重複／離開節點）、篩選、分群、抽樣、時間
// → POST /v1/analytics/path → 桑基圖（節點可設為新起點、下鑽用戶列表）+ Top N 路徑表（人數、占比、路徑數、平均耗時；K 匿名抑制）。
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { dedupe } from './FunnelPage';
import { PathChart } from './PathChart';
import { fmtMs } from './sankey';
import { fmtNum } from './series';
import type { Catalog, Cond, PathQuery, PathResult } from './types';

export function PathPage() {
  return <RequireGrant module="path" action="use"><PathInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
const SAMPLES: [string, number | null][] = [['1/10（預設）', 0.1], ['1/2', 0.5], ['1/100', 0.01], ['不抽樣（精確）', null]];

function PathInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<PathQuery>({ anchor: { event: '' }, direction: 'after', steps: 5, session: { mode: 'gap', gap_minutes: 30, max_events: 200 }, nodes: { events: [], collapse_other: false, split: [], merge_repeats: true, exit_node: true }, filters: [], sample: 0.1, top_n: 100, time: { from: today(-30), to: today() } });
  const [res, setRes] = useState<PathResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const bm = useBookmarkOpen<typeof q>('path', setQ);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  const run = async (over?: Partial<PathQuery>) => {
    const body = { ...q, ...over }; setQ(body);
    setBusy(true); setErr(null);
    try { const sendBody = { ...body, filters: body.filters?.length ? body.filters : undefined, nodes: { ...body.nodes, events: body.nodes?.events?.length ? body.nodes.events : undefined, split: body.nodes?.split?.length ? body.nodes.split : undefined } }; setRes(await api.post<PathResult>('/v1/analytics/path', sendBody)); setSent(sendBody); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); } finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const evCn = (n: string) => { const [ev, val] = n.split(':'); const cn = cat?.events.find((e) => e.name === ev)?.cn ?? ev; return val !== undefined ? `${cn}:${val}` : cn; };
  const splitFields = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn }))] : []);
  const filterFields = dedupe(cat ? [...cat.event_columns.map((c) => ({ v: c.name, cn: c.cn, type: c.type ?? 'string' })), ...cat.user_props.filter((u) => u.filterable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}`, type: u.type === 'enum' ? 'string' : u.type }))] : []);
  const setNodes = (n: Partial<NonNullable<PathQuery['nodes']>>) => setQ({ ...q, nodes: { ...q.nodes, ...n } });
  const ready = !!q.anchor.event;
  return (
    <div className="page">
      <div className="page-h"><div><h1>行為路徑</h1><p>從起點事件往後（或終點事件往前）看用戶 1–10 步走到哪裡；節點就是事件，可依屬性拆分；會話依間隔切，結束補「離開」。預設抽樣 1/10（按用戶分桶），人數少於 {k} 的路徑會被抑制。</p></div></div>
      <div className="grid c21">
        <div className="card">
          <div className="card-h"><h3>{q.direction === 'after' ? '起點' : '終點'}</h3></div>
          <div className="row" style={{ marginTop: 0 }}>
            <select value={q.direction} aria-label="方向" onChange={(e) => setQ({ ...q, direction: e.target.value as 'after' | 'before' })}><option value="after">之後（起點 →）</option><option value="before">之前（→ 終點）</option></select>
            <select className="grow" value={q.anchor.event} aria-label="起點事件" onChange={(e) => setQ({ ...q, anchor: { ...q.anchor, event: e.target.value } })}><option value="">選事件…</option>{q.direction === 'before' && <option value="離開">離開（App 退到背景／會話逾時）</option>}{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}</select>
            <select value={String(q.steps)} aria-label="步數" onChange={(e) => setQ({ ...q, steps: Number(e.target.value) })}>{Array.from({ length: 10 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1} 步</option>)}</select>
          </div>
          <div className="card-h" style={{ marginTop: 14 }}><h3>節點</h3></div>
          <div className="grid c2">
            <label>只把這些事件當節點（空 = 全部）<select multiple aria-label="節點事件" value={q.nodes?.events ?? []} onChange={(e) => setNodes({ events: Array.from(e.target.selectedOptions).map((o) => o.value) })}>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}</option>)}</select></label>
            <div className="stack">
              <label className="chk"><input type="checkbox" checked={!!q.nodes?.collapse_other} disabled={!q.nodes?.events?.length} onChange={(e) => setNodes({ collapse_other: e.target.checked })} /> 其餘事件折成「其他」（不勾 = 略過）</label>
              <label className="chk"><input type="checkbox" checked={q.nodes?.merge_repeats !== false} onChange={(e) => setNodes({ merge_repeats: e.target.checked })} /> 連續重複節點合併</label>
              <label className="chk"><input type="checkbox" checked={q.nodes?.exit_node !== false} onChange={(e) => setNodes({ exit_node: e.target.checked })} /> 會話結束補「離開」節點</label>
              <div className="row" style={{ marginTop: 0 }}><span className="sub">依屬性拆分</span>{(q.nodes?.split ?? []).map((sp, i) => <span key={i} className="pill">{evCn(sp.event)} × {splitFields.find((f) => f.v === sp.field)?.cn ?? sp.field} <button onClick={() => setNodes({ split: (q.nodes?.split ?? []).filter((_, j) => j !== i) })}>✕</button></span>)}
                {(q.nodes?.split?.length ?? 0) < 5 && <SplitAdder cat={cat} fields={splitFields} onAdd={(sp) => setNodes({ split: [...(q.nodes?.split ?? []), sp] })} />}</div>
            </div>
          </div>
          <div className="grid c3" style={{ marginTop: 14 }}>
            <label>會話切割<select aria-label="會話切割" value={q.session?.mode ?? 'gap'} onChange={(e) => setQ({ ...q, session: { ...q.session, mode: e.target.value as 'gap' } })}><option value="gap">依間隔切會話</option><option value="id">用 session_id</option><option value="cross">跨會話（整段期間）</option></select></label>
            <label>會話間隔（分鐘）<input type="number" min={1} max={1440} disabled={q.session?.mode !== 'gap'} value={q.session?.gap_minutes ?? 30} onChange={(e) => setQ({ ...q, session: { ...q.session, gap_minutes: Number(e.target.value) || 30 } })} /></label>
            <label>抽樣<select value={String(q.sample)} aria-label="抽樣" onChange={(e) => setQ({ ...q, sample: e.target.value === 'null' ? null : Number(e.target.value) })}>{SAMPLES.map(([cn, v]) => <option key={cn} value={String(v)}>{cn}</option>)}</select></label>
            <label>開始<input type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} /></label>
            <label>結束<input type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} /></label>
            <label>分群<input placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} /></label>
          </div>
          <div className="card-h" style={{ marginTop: 14 }}><h3>篩選</h3><div className="right"><button className="btn-ghost sm" onClick={() => setQ({ ...q, filters: [...(q.filters ?? []), { field: 'platform', op: 'eq', value: '' }] })}>＋ 條件</button></div></div>
          <div className="stack">{(q.filters ?? []).map((f, i) => {
            const cur = filterFields.find((x) => x.v === f.field); const ops = cat?.ops[cur?.type === 'number' ? 'number' : cur?.type === 'bool' ? 'bool' : 'string'] ?? [];
            const set = (nf: Partial<Cond>) => setQ({ ...q, filters: (q.filters ?? []).map((x, j) => (j === i ? { ...x, ...nf } : x)) });
            return <div className="row" key={i} style={{ marginTop: 0 }}><select value={f.field} aria-label="篩選欄位" onChange={(e) => set({ field: e.target.value, op: 'eq', value: '' })}>{filterFields.map((x) => <option key={x.v} value={x.v}>{x.cn}</option>)}</select><select value={f.op} aria-label="運算子" onChange={(e) => set({ op: e.target.value })}>{ops.map((o) => <option key={o} value={o}>{o}</option>)}</select>
              {!['set', 'nset', 'true', 'false'].includes(f.op) && <input style={{ width: 160 }} aria-label="值" value={String(f.value ?? '')} onChange={(e) => set({ value: cur?.type === 'number' && !['in', 'nin', 'between'].includes(f.op) ? Number(e.target.value) : ['in', 'nin', 'between'].includes(f.op) ? e.target.value.split(',').map((s) => (cur?.type === 'number' ? Number(s) : s.trim())) : e.target.value })} />}
              <button className="btn-ghost sm danger" onClick={() => setQ({ ...q, filters: (q.filters ?? []).filter((_, j) => j !== i) })}>✕</button></div>;
          })}</div>
          <div className="row"><button className="btn-primary" disabled={!ready || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</button>{res && <button className="btn-ghost" onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</button>}{res && <SaveCardButton kind="path" query={sent} />}{err && <span className="alert">{err}</span>}</div>
        </div>
        <div className="card"><div className="card-h"><h3>說明</h3></div><ul className="priv">
          <li>每次出現起點就切一條「起點 + N 步」路徑；同一用戶同會話多次出現各算一條，人數去重。</li>
          <li>「離開」= `$AppEnd` 事件或會話結束（間隔逾時）；終點可直接選「離開」看哪些路徑最常以離開收尾。</li>
          <li>桑基圖節點點一下可設為新起點或下鑽用戶列表；抑制的路徑不畫進圖。</li>
          <li>抽樣按用戶分桶：同一用戶的事件全在或全不在；人數為抽樣後實數。</li></ul></div>
      </div>
      {res && (
        <>
          {res.warnings.length > 0 && <div className="note">{res.warnings.join('；')}</div>}
          <div className="card" style={{ marginTop: 16 }}>
            <div className="card-h"><h3>路徑圖</h3><span className="sub">{q.direction === 'after' ? '起點' : '終點'}用戶 {fmtNum(res.anchor_users)}{res.sampled ? `（抽樣 ${res.sampled}，推估 ≈ ${fmtNum(Math.round(res.anchor_users / res.sampled))}）` : ''} · {res.cost.ms} ms · {res.paths.length} 條路徑{res.paths.some((p) => p.suppressed) ? `，${res.paths.filter((p) => p.suppressed).length} 條少於 ${k} 人已抑制` : ''}</span></div>
            <PathChart nodes={res.nodes} links={res.links} labelOf={evCn} onNode={(n, action) => {
              if (action === 'anchor') void run({ anchor: { event: n.node }, direction: 'after' });
              else if (can('users', 'use')) nav('/users', { state: { source: { type: 'path', query: q, node: n.node, level: n.level, label: `路徑「${evCn(q.anchor.event)}」第 ${n.level} 步 ${evCn(n.node)}` } } });
            }} />
          </div>
          <div className="card flush" style={{ marginTop: 16 }}><div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>#</th><th>路徑</th><th className="num">人數</th><th className="num">占比</th><th className="num">路徑數</th><th className="num">平均耗時</th></tr></thead>
            <tbody>{res.paths.map((p, i) => <tr key={i} className={p.suppressed ? 'warnrow' : ''}><td className="sub">{i + 1}</td><td>{p.path.map((n, j) => <span key={j}>{j > 0 && <span className="sub" style={{ display: 'inline', margin: '0 4px' }}>→</span>}{evCn(n)}</span>)}</td>
              <td className="num mono">{p.suppressed ? `< ${k}` : fmtNum(p.users)}</td><td className="num mono">{p.share === null ? '—' : `${(p.share * 100).toFixed(1)}%`}</td><td className="num mono">{p.suppressed ? '—' : fmtNum(p.sessions)}</td><td className="num mono">{fmtMs(p.avg_ms)}</td></tr>)}</tbody>
          </table></div></div>
          {showSql && <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>SQL</h3><span className="sub">{res.lineage.join(' · ')}</span></div><pre className="code">{res.sql}</pre></div>}
        </>
      )}
    </div>
  );
}

function SplitAdder({ cat, fields, onAdd }: { cat: Catalog | null; fields: { v: string; cn: string }[]; onAdd: (sp: { event: string; field: string }) => void }) {
  const [ev, setEv] = useState(''); const [f, setF] = useState('');
  return <><select value={ev} aria-label="拆分事件" onChange={(e) => setEv(e.target.value)}><option value="">事件…</option>{cat?.events.map((x) => <option key={x.name} value={x.name}>{x.cn}</option>)}</select>
    <select value={f} aria-label="拆分屬性" onChange={(e) => setF(e.target.value)}><option value="">屬性…</option>{fields.map((x) => <option key={x.v} value={x.v}>{x.cn}</option>)}</select>
    <button className="btn-ghost sm" disabled={!ev || !f} onClick={() => { onAdd({ event: ev, field: f }); setEv(''); setF(''); }}>＋ 拆分</button></>;
}
