// 用戶條件建構器（用戶列表、分群、標籤值共用）：神策式規則——用戶屬性／分群條件樹（且／或可巢狀）、行為（做過／未做過 × 指標 × 比較 × 近 N 天或自訂期間 × 事件屬性條件樹）、屬性塊與行為塊之間的且／或、來源落點（唯讀）。
import type { Catalog, Cond } from '@/analytics/types';
import { CMP_OPS, MAX_DEPTH, MEASURES, NO_VALUE_OPS, NUMERIC_MEASURES, OP_CN, condValue, emptyCond, eventFields, isGroup, newBehavior, numericEventFields, opsFor, rawValue, sourceText, toGroup, userFields, type FieldOpt } from './rule';
import type { CmpOp, CondGroup, MeasureAgg, UsersBehavior, UsersQuery } from './types';

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }

function OpToggle({ op, label, onChange }: { op: 'and' | 'or'; label: string; onChange: (op: 'and' | 'or') => void }) {
  return <button type="button" className={`rb-op ${op}`} aria-label={label} title="點一下切換 且／或" onClick={() => onChange(op === 'and' ? 'or' : 'and')}>{op === 'and' ? '且' : '或'}</button>;
}

function CondRow({ c, fields, cat, onChange, onRemove, label }: { c: Cond; fields: FieldOpt[]; cat: Catalog | null; onChange: (c: Cond) => void; onRemove: () => void; label: string }) {
  const f = fields.find((x) => x.v === c.field); const ops = opsFor(cat, f);
  return (
    <div className="row" style={{ marginTop: 0 }}>
      <select value={c.field} aria-label={`${label}欄位`} onChange={(e) => { const nf = fields.find((x) => x.v === e.target.value); onChange({ field: e.target.value, op: opsFor(cat, nf)[0], value: '' }); }}>
        <option value="">選欄位…</option>{fields.map((x) => <option key={x.v} value={x.v}>{x.cn}</option>)}</select>
      <select value={c.op} aria-label={`${label}運算子`} onChange={(e) => onChange({ ...c, op: e.target.value, value: NO_VALUE_OPS.has(e.target.value) ? undefined : '' })}>{ops.map((o) => <option key={o} value={o}>{OP_CN[o] ?? o}</option>)}</select>
      {!NO_VALUE_OPS.has(c.op) && !c.field.startsWith('seg.') && (f?.values && ['eq', 'neq'].includes(c.op)
        ? <select value={String(c.value ?? '')} aria-label={`${label}值`} onChange={(e) => onChange({ ...c, value: e.target.value })}><option value="">選值…</option>{f.values.map((v) => <option key={v} value={v}>{v}</option>)}</select>
        : <input style={{ width: 170 }} aria-label={`${label}值`} type={c.op === 'rel' || (f?.type === 'number' && !['in', 'nin', 'between'].includes(c.op)) ? 'number' : 'text'} value={rawValue(c.value)}
            placeholder={c.op === 'rel' ? '天數' : ['in', 'nin'].includes(c.op) ? (f?.values ? f.values.join(',') : '逗號分隔') : c.op === 'between' ? '下限,上限' : ''}
            onChange={(e) => onChange({ ...c, value: condValue(f?.type ?? 'string', c.op, e.target.value) })} />)}
      <button type="button" className="btn-ghost sm danger" aria-label={`刪除${label}`} onClick={onRemove}>✕</button>
    </div>
  );
}

/** 條件樹編輯：左側「且／或」切換，右側條件列或子群組；可加條件、加條件組（最多 MAX_DEPTH 層）。 */
export function GroupEditor({ g, fields, cat, label, depth = 1, onChange }: { g: CondGroup; fields: FieldOpt[]; cat: Catalog | null; label: string; depth?: number; onChange: (g: CondGroup) => void }) {
  const setItem = (i: number, it: Cond | CondGroup) => onChange({ ...g, items: g.items.map((x, j) => (j === i ? it : x)) });
  const rm = (i: number) => onChange({ ...g, items: g.items.filter((_, j) => j !== i) });
  return (
    <div className={`rb-group${depth > 1 ? ' nested' : ''}`}>
      {g.items.length > 1 && <OpToggle op={g.op} label={`${label}組合`} onChange={(op) => onChange({ ...g, op })} />}
      <div className="stack grow" style={{ gap: 6 }}>
        {g.items.map((it, i) => isGroup(it)
          ? <GroupEditor key={i} g={it} fields={fields} cat={cat} label={`${label}組 ${i + 1} `} depth={depth + 1} onChange={(ng) => setItem(i, ng)} />
          : <CondRow key={i} c={it} fields={fields} cat={cat} label={`${label}${i + 1} `} onChange={(nc) => setItem(i, nc)} onRemove={() => rm(i)} />)}
        <div className="row" style={{ marginTop: 0 }}>
          <button type="button" className="btn-ghost sm" onClick={() => onChange({ ...g, items: [...g.items, emptyCond()] })}>＋ 條件</button>
          {depth < MAX_DEPTH && <button type="button" className="btn-ghost sm" onClick={() => onChange({ ...g, items: [...g.items, { op: g.op === 'and' ? 'or' : 'and', items: [emptyCond()] }] })}>＋ 條件組（{g.op === 'and' ? '或' : '且'}）</button>}
          {depth > 1 && !g.items.length && <span className="sub">空的條件組不會送出</span>}
        </div>
      </div>
    </div>
  );
}

function BehaviorCard({ b, i, cat, onChange, onRemove }: { b: UsersBehavior; i: number; cat: Catalog | null; onChange: (b: UsersBehavior) => void; onRemove: () => void }) {
  const ef = eventFields(cat); const nf = numericEventFields(cat); const L = `行為 ${i + 1} `;
  const agg = b.measure?.agg ?? 'count'; const cmp = b.cmp ?? b.count ?? { op: 'gte' as CmpOp, value: 1 };
  const t = b.time ?? {}; const mode: 'last' | 'range' = t.from || t.to ? 'range' : 'last';
  const fl = toGroup(b.filters);
  return (
    <div className="stack rb-behavior">
      <div className="row" style={{ marginTop: 0 }}>
        <span className="pill blue">{i + 1}</span>
        <select value={b.done === false ? 'no' : 'yes'} aria-label={`${L}做過`} onChange={(e) => onChange({ ...b, done: e.target.value === 'yes' })}><option value="yes">做過</option><option value="no">未做過</option></select>
        <select className="grow" value={b.event} aria-label={`${L}事件`} onChange={(e) => onChange({ ...b, event: e.target.value })}><option value="">選事件…</option>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}</select>
        <button type="button" className="btn-ghost sm danger" aria-label={`刪除${L.trim()}`} onClick={onRemove}>✕</button>
      </div>
      {b.done !== false && <div className="row" style={{ marginTop: 0 }}>
        <span className="sub">指標</span>
        <select value={agg} aria-label={`${L}指標`} onChange={(e) => { const a = e.target.value as MeasureAgg; onChange({ ...b, measure: { agg: a, field: NUMERIC_MEASURES.includes(a) ? (b.measure?.field ?? nf[0]?.v) : undefined }, cmp: { op: cmp.op, value: cmp.op === 'between' ? [1, 3] : 1 } }); }}>{MEASURES.map(([a, cn]) => <option key={a} value={a}>{cn}</option>)}</select>
        {NUMERIC_MEASURES.includes(agg) && <select value={b.measure?.field ?? ''} aria-label={`${L}指標欄位`} onChange={(e) => onChange({ ...b, measure: { agg, field: e.target.value } })}><option value="">選數值屬性…</option>{nf.map((f) => <option key={f.v} value={f.v}>{f.cn}</option>)}</select>}
        <select value={cmp.op} aria-label={`${L}比較`} onChange={(e) => { const op = e.target.value as CmpOp; onChange({ ...b, cmp: { op, value: op === 'between' ? [1, 3] : (Array.isArray(cmp.value) ? cmp.value[0] : cmp.value) } }); }}>{CMP_OPS.map(([o, cn]) => <option key={o} value={o}>{cn}</option>)}</select>
        <input style={{ width: 120 }} aria-label={`${L}值`} type={cmp.op === 'between' ? 'text' : 'number'} value={rawValue(cmp.value)} placeholder={cmp.op === 'between' ? '下限,上限' : agg === 'count' ? '次數' : agg === 'days' ? '天數' : '數值'}
          onChange={(e) => onChange({ ...b, cmp: { op: cmp.op, value: cmp.op === 'between' ? (e.target.value.split(',').map(Number) as [number, number]) : Number(e.target.value) } })} />
        <span className="sub">{agg === 'count' ? '次' : agg === 'days' ? '天' : ''}</span>
      </div>}
      <div className="row" style={{ marginTop: 0 }}>
        <span className="sub">期間</span>
        <select value={mode} aria-label={`${L}期間方式`} onChange={(e) => onChange({ ...b, time: e.target.value === 'last' ? { last_days: 30 } : { from: today(-30), to: today() } })}><option value="last">近 N 天（以計算日回推）</option><option value="range">自訂期間</option></select>
        {mode === 'last'
          ? <><span className="sub">近</span><input style={{ width: 90 }} type="number" min={1} max={730} aria-label={`${L}近 N 天`} value={t.last_days ?? ''} onChange={(e) => onChange({ ...b, time: { last_days: Number(e.target.value) } })} /><span className="sub">天</span></>
          : <><input type="date" aria-label={`${L}開始`} value={t.from ?? ''} onChange={(e) => onChange({ ...b, time: { from: e.target.value, to: t.to ?? '' } })} /><span className="sub">～</span><input type="date" aria-label={`${L}結束`} value={t.to ?? ''} onChange={(e) => onChange({ ...b, time: { from: t.from ?? '', to: e.target.value } })} /></>}
        {!fl.items.length && <button type="button" className="btn-ghost sm" onClick={() => onChange({ ...b, filters: { op: 'and', items: [emptyCond()] } })}>＋ 事件屬性條件</button>}
      </div>
      {fl.items.length > 0 && <div><span className="sub" style={{ marginBottom: 4 }}>事件屬性條件</span><GroupEditor g={fl} fields={ef} cat={cat} label={`${L}屬性 `} onChange={(ng) => onChange({ ...b, filters: ng })} /></div>}
    </div>
  );
}

export function RuleBuilder({ q, cat, segments, onChange, readOnly }: { q: UsersQuery; cat: Catalog | null; segments: { id: string; name: string }[]; onChange: (q: UsersQuery) => void; readOnly?: boolean }) {
  const uf = userFields(cat, segments); const g = toGroup(q.filters); const bs = q.behaviors ?? [];
  if (readOnly) return <p className="note">結果保存分群：規則不可編輯。來源：{q.source ? sourceText(q.source) : '—'}</p>;
  return (
    <div className="stack rb">
      {q.source && <div className="row" style={{ marginTop: 0 }}><span className="pill custom">來源落點</span><span>{sourceText(q.source)}</span><button type="button" className="btn-ghost sm danger" onClick={() => onChange({ ...q, source: undefined })}>移除</button></div>}
      <div className="card-h" style={{ marginBottom: 0 }}><h3>用戶屬性／分群</h3><span className="sub">用戶表欄位、所屬分群；條件可分組，群組內與群組間都可選「且／或」</span></div>
      <GroupEditor g={g} fields={uf} cat={cat} label="條件 " onChange={(ng) => onChange({ ...q, filters: ng })} />
      {g.items.length > 0 && bs.length > 0 && <div className="row rb-combine" style={{ marginTop: 0 }}><span className="sub">用戶屬性條件 與 行為條件 之間</span><OpToggle op={q.combine ?? 'and'} label="屬性與行為組合" onChange={(op) => onChange({ ...q, combine: op })} /><span className="sub">{(q.combine ?? 'and') === 'and' ? '兩邊都要符合' : '任一邊符合即可'}</span></div>}
      <div className="card-h" style={{ marginBottom: 0, marginTop: 6 }}><h3>行為（做過／未做過）</h3><span className="sub">事件 × 指標（次數、活躍天數、數值屬性合計／平均／最大／最小）× 比較 × 期間 × 事件屬性</span>
        <div className="right"><button type="button" className="btn-ghost sm" onClick={() => onChange({ ...q, behaviors: [...bs, newBehavior()] })}>＋ 行為</button></div></div>
      {bs.length > 0 && <div className="rb-group">
        {bs.length > 1 && <OpToggle op={q.behaviors_op ?? 'and'} label="行為組合" onChange={(op) => onChange({ ...q, behaviors_op: op })} />}
        <div className="stack grow">{bs.map((b, i) => <BehaviorCard key={i} b={b} i={i} cat={cat} onChange={(nb) => onChange({ ...q, behaviors: bs.map((x, j) => (j === i ? nb : x)) })} onRemove={() => onChange({ ...q, behaviors: bs.filter((_, j) => j !== i) })} />)}</div>
      </div>}
    </div>
  );
}
