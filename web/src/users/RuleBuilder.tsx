// 用戶條件建構器（用戶列表與分群共用）：用戶屬性／分群條件、做過／未做過（次數、期間、事件屬性）、且／或、來源落點（唯讀）。
import type { Catalog, Cond } from '@/analytics/types';
import { COUNT_OPS, NO_VALUE_OPS, OP_CN, condValue, eventFields, newBehavior, opsFor, rawValue, sourceText, userFields, type FieldOpt } from './rule';
import type { UsersBehavior, UsersQuery } from './types';

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }

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
      <button className="btn-ghost sm danger" aria-label={`刪除${label}`} onClick={onRemove}>✕</button>
    </div>
  );
}

export function RuleBuilder({ q, cat, segments, onChange, readOnly }: { q: UsersQuery; cat: Catalog | null; segments: { id: string; name: string }[]; onChange: (q: UsersQuery) => void; readOnly?: boolean }) {
  const uf = userFields(cat, segments); const ef = eventFields(cat);
  const setB = (i: number, b: Partial<UsersBehavior>) => onChange({ ...q, behaviors: (q.behaviors ?? []).map((x, j) => (j === i ? { ...x, ...b } : x)) });
  if (readOnly) return <p className="note">結果保存分群：規則不可編輯。來源：{q.source ? sourceText(q.source) : '—'}</p>;
  return (
    <div className="stack">
      {q.source && <div className="row" style={{ marginTop: 0 }}><span className="pill custom">來源落點</span><span>{sourceText(q.source)}</span><button className="btn-ghost sm danger" onClick={() => onChange({ ...q, source: undefined })}>移除</button></div>}
      <div className="card-h" style={{ marginBottom: 0 }}><h3>用戶屬性／分群</h3><div className="right"><button className="btn-ghost sm" onClick={() => onChange({ ...q, filters: [...(q.filters ?? []), { field: '', op: 'eq', value: '' }] })}>＋ 條件</button></div></div>
      {(q.filters ?? []).map((c, i) => <CondRow key={i} c={c} fields={uf} cat={cat} label={`條件 ${i + 1} `} onChange={(nc) => onChange({ ...q, filters: (q.filters ?? []).map((x, j) => (j === i ? nc : x)) })} onRemove={() => onChange({ ...q, filters: (q.filters ?? []).filter((_, j) => j !== i) })} />)}
      <div className="card-h" style={{ marginBottom: 0, marginTop: 6 }}><h3>行為（做過／未做過）</h3>
        {(q.behaviors?.length ?? 0) > 1 && <select value={q.behaviors_op ?? 'and'} aria-label="行為組合" style={{ width: 'auto' }} onChange={(e) => onChange({ ...q, behaviors_op: e.target.value as 'and' | 'or' })}><option value="and">全部符合（且）</option><option value="or">任一符合（或）</option></select>}
        <div className="right"><button className="btn-ghost sm" onClick={() => onChange({ ...q, behaviors: [...(q.behaviors ?? []), newBehavior(today(-30), today())] })}>＋ 行為</button></div></div>
      {(q.behaviors ?? []).map((b, i) => (
        <div className="stack" key={i} style={{ padding: '10px 12px', border: '1px solid var(--line)', borderRadius: 10, background: 'var(--soft)' }}>
          <div className="row" style={{ marginTop: 0 }}>
            <span className="pill blue">{i + 1}</span>
            <select value={b.done === false ? 'no' : 'yes'} aria-label={`行為 ${i + 1} 做過`} onChange={(e) => setB(i, { done: e.target.value === 'yes' })}><option value="yes">做過</option><option value="no">未做過</option></select>
            <select className="grow" value={b.event} aria-label={`行為 ${i + 1} 事件`} onChange={(e) => setB(i, { event: e.target.value })}><option value="">選事件…</option>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}（{ev.name}）</option>)}</select>
            {b.done !== false && <>
              <select value={b.count?.op ?? 'gte'} aria-label={`行為 ${i + 1} 次數運算子`} onChange={(e) => setB(i, { count: { op: e.target.value as 'gte', value: e.target.value === 'between' ? [1, 3] : 1 } })}>{COUNT_OPS.map(([o, cn]) => <option key={o} value={o}>{cn}</option>)}</select>
              <input style={{ width: 110 }} aria-label={`行為 ${i + 1} 次數`} value={rawValue(b.count?.value)} placeholder={b.count?.op === 'between' ? '下限,上限' : '次數'} onChange={(e) => setB(i, { count: { op: b.count?.op ?? 'gte', value: (b.count?.op === 'between' ? e.target.value.split(',').map(Number) as [number, number] : Number(e.target.value)) } })} /><span className="sub">次</span></>}
            <button className="btn-ghost sm danger" aria-label={`刪除行為 ${i + 1}`} onClick={() => onChange({ ...q, behaviors: (q.behaviors ?? []).filter((_, j) => j !== i) })}>✕</button>
          </div>
          <div className="row" style={{ marginTop: 0 }}>
            <span className="sub">期間</span><input type="date" aria-label={`行為 ${i + 1} 開始`} value={b.time?.from ?? ''} onChange={(e) => setB(i, { time: { from: e.target.value, to: b.time?.to ?? '' } })} /><span className="sub">～</span><input type="date" aria-label={`行為 ${i + 1} 結束`} value={b.time?.to ?? ''} onChange={(e) => setB(i, { time: { from: b.time?.from ?? '', to: e.target.value } })} />
            <button className="btn-ghost sm" onClick={() => setB(i, { filters: [...(b.filters ?? []), { field: '', op: 'eq', value: '' }] })}>＋ 事件屬性</button>
          </div>
          {(b.filters ?? []).map((c, j) => <CondRow key={j} c={c} fields={ef} cat={cat} label={`行為 ${i + 1} 屬性 ${j + 1} `} onChange={(nc) => setB(i, { filters: (b.filters ?? []).map((x, k) => (k === j ? nc : x)) })} onRemove={() => setB(i, { filters: (b.filters ?? []).filter((_, k) => k !== j) })} />)}
        </div>
      ))}
    </div>
  );
}
