// 標籤（docs/21 §2 最小版）：目錄列表（狀態／型別／人數／更新／分布）→ 詳情（分布長條、版本、血緣、動作）→ 新建（自定義標籤值：每值一條規則；SQL 標籤）。
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { RuleBuilder } from '@/users/RuleBuilder';
import { emptyRule, ruleValid, summarizeRule } from '@/users/rule';
import type { Segment } from '@/users/types';
import { AGG_CN, DTYPE_CN, FEATURE_CN, TAG_KIND_CN, TAG_STATUS_CN, TAG_STATUS_PILL, bucketLabels, fmtTs, parseBuckets, specText, suggestKey, topDistribution, updateText } from './tags';
import type { Tag, TagCreate, TagCustomFields, TagKind, TagSpec, TagValue, TagVersion } from './types';

export function TagsPage() {
  return <RequireGrant module="segments"><TagsInner /></RequireGrant>;
}

function TagsInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const { id } = useParams();
  const [list, setList] = useState<Tag[]>([]);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [cf, setCf] = useState<TagCustomFields | null>(null);
  const [segOpts, setSegOpts] = useState<{ id: string; name: string }[]>([]);
  const [tab, setTab] = useState<'all' | 'pending' | 'recycled'>('all');
  const [search, setSearch] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => { const [a, r] = await Promise.all([api.get<Tag[]>('/v1/tags'), api.get<Tag[]>('/v1/tags?status=recycled')]); setList([...a, ...r]); };
  useEffect(() => {
    reload().catch((e: Error) => setErr(e.message));
    api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch(() => null);
    api.get<TagCustomFields>('/v1/tags/custom-fields').then(setCf).catch(() => null);
    api.get<Segment[]>('/v1/segments').then((s) => setSegOpts(s.map((x) => ({ id: x.id, name: x.name })))).catch(() => null);
  }, []);
  const shown = list.filter((t) => (tab === 'recycled' ? t.status === 'recycled' : tab === 'pending' ? t.status === 'pending_approval' : t.status !== 'recycled') && (!search || t.name.includes(search) || t.id.includes(search) || (t.description ?? '').includes(search)));
  const current = id && id !== 'new' ? list.find((t) => t.id === id) ?? null : null;
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(true); setErr(null); setMsg(null); try { await fn(); await reload(); setMsg(`${label}完成`); } catch (e) { setErr(e instanceof ApiError ? e.message : `${label}失敗`); } finally { setBusy(false); } };
  return (
    <div className="page">
      <div className="page-h"><div><h1>標籤</h1><p>標籤 = 每個用戶一個值，存成 Bitmap 版本（dmp.user_tags），可在推送名單時附帶、在分析時當維度。五種建立方式：自定義標籤值（每個值一條用戶規則）、SQL 標籤、基礎指標值、首末次特徵、RFM；行銷建立需數位經營審批；例行標籤到點自動計算。</p></div>
        {can('segments', 'use') && <div className="right"><Link className="btn-primary" to="/tags/new">＋ 新建標籤</Link></div>}</div>
      {err && <div className="alert" style={{ marginBottom: 12 }}>{err}</div>}{msg && <div className="note">{msg}</div>}
      {id === 'new' ? <TagForm cat={cat} cf={cf} segOpts={segOpts} onDone={async (t) => { await reload(); nav(`/tags/${t.id}`); }} onCancel={() => nav('/tags')} />
        : current ? <TagDetail tag={current} cat={cat} segOpts={segOpts} me={me?.user.id} can={can} busy={busy} act={act} />
        : (
          <div className="grid c21">
            <div className="card flush">
              <div className="row" style={{ padding: 12, marginTop: 0 }}>
                <div className="seg" style={{ marginBottom: 0 }}>{([['all', '全部'], ['pending', '待審批'], ['recycled', '回收站']] as const).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}{k === 'pending' && list.some((t) => t.status === 'pending_approval') ? ` (${list.filter((t) => t.status === 'pending_approval').length})` : ''}</a>)}</div>
                <input aria-label="搜尋標籤" placeholder="搜尋名稱／key／說明" value={search} onChange={(e) => setSearch(e.target.value)} />
              </div>
              <div className="tbl-wrap"><table className="tbl">
                <thead><tr><th>標籤</th><th>目錄</th><th>建立方式</th><th>狀態</th><th className="num">覆蓋人數</th><th>更新</th><th>最近計算</th><th>建立者</th></tr></thead>
                <tbody>{shown.map((t) => (
                  <tr key={t.id} className="clickable" onClick={() => nav(`/tags/${t.id}`)}>
                    <td><b>{t.name}</b><span className="sub mono">{t.id} · {DTYPE_CN[t.dtype]}</span></td>
                    <td className="sub">{t.directory}</td>
                    <td className="sub">{TAG_KIND_CN[t.kind]}{t.kind === 'values' ? `（${t.values.length} 值）` : ''}</td>
                    <td><span className={`pill ${TAG_STATUS_PILL[t.status]}`}>{TAG_STATUS_CN[t.status]}</span></td>
                    <td className="num mono">{t.latest?.users != null ? t.latest.users.toLocaleString() : '—'}</td>
                    <td className="sub">{updateText(t.update)}</td>
                    <td className="sub mono">{t.latest ? `${t.latest.basetime} v${t.latest.version}${t.latest.status === 'failed' ? ' 失敗' : ''}` : '尚未計算'}</td>
                    <td className="sub">{t.owner}</td>
                  </tr>))}
                  {!shown.length && <tr><td colSpan={8} className="sub" style={{ textAlign: 'center', padding: 24 }}>沒有標籤</td></tr>}
                </tbody></table></div>
            </div>
            <div className="card"><div className="card-h"><h3>說明</h3></div><ul className="priv">
              <li>自定義標籤值：每個值一條規則（與分群／用戶列表同一套條件），最多 24 值；同一人可命中多值。</li>
              <li>SQL 標籤：唯讀沙盒驗過的 SELECT，輸出 one_id_uint 與 tag_value 兩欄；涉及脫敏欄位會被擋。</li>
              <li>計算：每值一個 Bitmap 寫入 dmp.user_tags，一次一版、保留 N 版；推送附帶標籤時以 Bitmap 交集取值，數值型一律區間化。</li>
              <li>其餘八種建立方式（基礎指標值、首末次特徵、偏好、分布、運算、RFM、生命週期、導入）排第二階段。</li></ul></div>
          </div>)}
    </div>
  );
}

function TagDetail({ tag, cat, segOpts, me, can, busy, act }: { tag: Tag; cat: Catalog | null; segOpts: { id: string; name: string }[]; me?: string; can: (m: 'segments', a: 'use' | 'approve') => boolean; busy: boolean; act: (label: string, fn: () => Promise<unknown>) => Promise<void> }) {
  const [versions, setVersions] = useState<TagVersion[]>([]);
  useEffect(() => { api.get<TagVersion[]>(`/v1/tags/${tag.id}/versions`).then(setVersions).catch(() => setVersions(tag.versions ?? [])); }, [tag.id, tag.updated_at]);   // eslint-disable-line react-hooks/exhaustive-deps
  const mine = tag.owner === me; const approver = can('segments', 'approve');
  const dist = topDistribution(tag.latest?.distribution ?? []);
  return (
    <div className="grid c21">
      <div className="stack">
        <div className="card">
          <div className="card-h"><h3>{tag.name} <span className={`pill ${TAG_STATUS_PILL[tag.status]}`}>{TAG_STATUS_CN[tag.status]}</span></h3><Link className="btn-ghost sm" to="/tags">← 標籤目錄</Link></div>
          <dl className="kv">
            <dt>key</dt><dd className="mono">{tag.id}</dd>
            <dt>目錄／型別</dt><dd>{tag.directory} · {DTYPE_CN[tag.dtype]}</dd>
            <dt>建立方式</dt><dd>{TAG_KIND_CN[tag.kind]}</dd>
            <dt>更新</dt><dd>{updateText(tag.update)}</dd>
            <dt>建立者</dt><dd>{tag.owner} · {fmtTs(tag.created_at)}</dd>
            {tag.description && <><dt>說明</dt><dd>{tag.description}</dd></>}
            {Object.entries(tag.fields ?? {}).filter(([, v]) => v).map(([k, v]) => <span key={k} style={{ display: 'contents' }}><dt>{k === 'department' ? '所屬部門' : k === 'requester' ? '需求提出人' : k === 'category' ? '需求類別' : k}</dt><dd>{v}</dd></span>)}
            {tag.approval.status !== 'none' && <><dt>審批</dt><dd>{tag.approval.status === 'pending' ? '待數位經營核可' : `${tag.approval.status === 'approved' ? '已核可' : '已退回'} · ${tag.approval.by ?? ''} ${fmtTs(tag.approval.at)}${tag.approval.note ? ` · ${tag.approval.note}` : ''}`}</dd></>}
          </dl>
          <div className="row">
            {(mine || approver) && tag.status === 'active' && <button className="btn-primary" disabled={busy} onClick={() => void act('計算', () => api.post(`/v1/tags/${tag.id}/compute`, {}))}>立即計算</button>}
            {approver && tag.status === 'pending_approval' && !mine && <><button className="btn-primary" disabled={busy} onClick={() => void act('核可', () => api.post(`/v1/tags/${tag.id}/approve`, { approve: true, note: '' }))}>核可</button><button className="btn-ghost danger" disabled={busy} onClick={() => { const n = prompt('退回意見'); if (n) void act('退回', () => api.post(`/v1/tags/${tag.id}/approve`, { approve: false, note: n })); }}>退回</button></>}
            {(mine || approver) && tag.status === 'draft' && <button className="btn-ghost" disabled={busy} onClick={() => void act('送審', () => api.post(`/v1/tags/${tag.id}/submit`, {}))}>送審</button>}
            {(mine || approver) && (tag.status === 'active' || tag.status === 'paused') && <button className="btn-ghost" disabled={busy} onClick={() => void act(tag.status === 'active' ? '暫停' : '啟用', () => api.post(`/v1/tags/${tag.id}/pause`, {}))}>{tag.status === 'active' ? '暫停' : '啟用'}</button>}
            {(mine || approver) && tag.status !== 'recycled' && <button className="btn-ghost danger" disabled={busy} onClick={() => { if (confirm('移入回收站？有下游引用時會被擋。')) void act('移入回收站', () => api.post(`/v1/tags/${tag.id}/recycle`, {})); }}>移入回收站</button>}
            {(mine || approver) && tag.status === 'recycled' && <button className="btn-ghost" disabled={busy} onClick={() => void act('恢復', () => api.post(`/v1/tags/${tag.id}/restore`, {}))}>恢復</button>}
          </div>
          {tag.latest?.status === 'failed' && <div className="alert">最近計算失敗：{tag.latest.error}</div>}
        </div>
        <div className="card">
          <div className="card-h"><h3>規則</h3></div>
          {tag.spec && ['metric', 'firstlast', 'rfm'].includes(tag.kind) ? <p className="note"><b>{TAG_KIND_CN[tag.kind]}</b>：{specText(tag, (e) => cat?.events.find((x) => x.name === e)?.cn ?? e)}{tag.project ? `（專案 ${tag.project}）` : ''}</p> : tag.kind === 'values' ? <table className="tbl"><thead><tr><th>標籤值</th><th>規則</th><th className="num">人數（最新版）</th></tr></thead><tbody>{tag.values.map((v: TagValue) => <tr key={v.value}><td><b>{v.value}</b></td><td className="sub">{summarizeRule(v.rule, cat, segOpts)}</td><td className="num mono">{tag.latest?.distribution.find((d) => d.value === v.value)?.users.toLocaleString() ?? '—'}</td></tr>)}</tbody></table>
            : <pre className="code">{tag.sql}</pre>}
        </div>
        <div className="card">
          <div className="card-h"><h3>歷史版本</h3><span className="sub">保留 {tag.update.keep} 版</span></div>
          {versions.length ? <table className="tbl"><thead><tr><th>版本</th><th>basetime</th><th className="num">人數</th><th>狀態</th><th>觸發</th><th>計算時間</th><th className="num">耗時</th></tr></thead><tbody>{[...versions].reverse().map((v) => <tr key={v.version}><td className="mono">v{v.version}</td><td className="mono">{v.basetime}</td><td className="num mono">{v.users?.toLocaleString() ?? '—'}</td><td><span className={`pill ${v.status === 'success' ? 'blue' : v.status === 'failed' ? 'red' : 'amber'}`}>{v.status === 'success' ? '成功' : v.status === 'failed' ? '失敗' : '執行中'}</span></td><td className="sub">{v.trigger === 'routine' ? '例行' : '手動'}</td><td className="sub mono">{fmtTs(v.computed_at)}</td><td className="num mono">{v.ms} ms</td></tr>)}</tbody></table> : <p className="note">尚未計算。</p>}
        </div>
      </div>
      <div className="stack">
        <div className="card">
          <div className="card-h"><h3>值分布</h3><span className="sub">{tag.latest ? `v${tag.latest.version} · ${tag.latest.users?.toLocaleString() ?? '—'} 人` : '—'}</span></div>
          {dist.rows.length ? <div className="bars">{dist.rows.map((r) => <div key={r.value} className="bar"><span title={r.value}>{r.value}</span><i style={{ width: `${Math.max(2, (r.users / dist.max) * 100)}%` }} /><em>{r.users.toLocaleString()}</em></div>)}</div> : <p className="note">計算後顯示各值人數。</p>}
        </div>
        <div className="card">
          <div className="card-h"><h3>血緣</h3></div>
          <dl className="kv"><dt>上游</dt><dd>{tag.lineage.up.length ? tag.lineage.up.map((u) => <span key={u} className="pill mono" style={{ marginRight: 4 }}>{u.startsWith('segment:') ? `分群 ${segOpts.find((s) => s.id === u.slice(8))?.name ?? u.slice(8)}` : u}</span>) : '—'}</dd>
            <dt>下游</dt><dd>{tag.lineage.down.length ? tag.lineage.down.map((d) => <Link key={d} to="/activation" className="pill blue" style={{ marginRight: 4 }}>{d.startsWith('push_') ? `推送單 ${d}` : `目的地 ${d}`}</Link>) : '—'}</dd></dl>
          {tag.warnings?.length ? <p className="note">{tag.warnings.join('；')}</p> : null}
        </div>
      </div>
    </div>
  );
}

type Draft = { name: string; key: string; directory: string; description: string; dtype: 'string' | 'number' | 'bool'; kind: TagKind; values: TagValue[]; sql: string; spec: TagSpec; buckets: string; project: string; update: Tag['update']; fields: Record<string, string> };
const SPEC_KINDS: TagKind[] = ['metric', 'firstlast', 'rfm'];
function TagForm({ cat, cf, segOpts, onDone, onCancel }: { cat: Catalog | null; cf: TagCustomFields | null; segOpts: { id: string; name: string }[]; onDone: (t: Tag) => Promise<void> | void; onCancel: () => void }) {
  const [d, setD] = useState<Draft>({ name: '', key: '', directory: cf?.directories[0] ?? '客戶價值', description: '', dtype: 'string', kind: 'values', values: [{ value: '', rule: emptyRule('golden') }], spec: { event: '', agg: 'count', window_days: 90, which: 'first', feature: 'date', amount_field: 'amount', bins: 5, output: 'score' }, buckets: '', project: 'golden', sql: "SELECT one_id_uint, toString(count()) AS tag_value\nFROM dmp.events WHERE event = 'bond_deal' AND event_date >= today() - 30\nGROUP BY one_id_uint", update: { mode: 'routine', freq: 'daily', earliest: '06:00', keep: 7 }, fields: {} });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(0);
  const setV = (i: number, p: Partial<TagValue>) => setD({ ...d, values: d.values.map((v, j) => (j === i ? { ...v, ...p } : v)) });
  const bk = parseBuckets(d.buckets);
  const specOk = !!d.spec.event && bk !== null && (d.kind !== 'metric' || !['sum', 'avg', 'max', 'min'].includes(d.spec.agg ?? '') || !!d.spec.field) && (d.kind !== 'firstlast' || d.spec.feature !== 'field' || !!d.spec.field);
  const valid = d.name.trim() && (d.kind === 'sql' ? d.sql.trim().length > 10 : SPEC_KINDS.includes(d.kind) ? specOk : d.values.length > 0 && d.values.every((v) => v.value.trim() && ruleValid(v.rule)));
  const setS = (p: Partial<TagSpec>) => setD({ ...d, spec: { ...d.spec, ...p } });
  const numFields = (cat?.event_columns ?? []).filter((c) => c.type === 'number');
  const save = async () => {
    setBusy(true); setErr(null);
    const body: TagCreate = { name: d.name, key: d.key || suggestKey(d.name) || undefined, directory: d.directory, description: d.description, dtype: d.dtype, kind: d.kind, update: d.update, fields: d.fields,
      ...(d.kind === 'values' ? { values: d.values.map((v) => ({ value: v.value.trim(), rule: { ...v.rule, filters: v.rule.filters?.length ? v.rule.filters : undefined, behaviors: v.rule.behaviors?.length ? v.rule.behaviors : undefined } })) } : d.kind === 'sql' ? { sql: d.sql } : { project: d.project, spec: d.kind === 'metric' ? { event: d.spec.event, agg: d.spec.agg, field: d.spec.field || undefined, window_days: d.spec.window_days, buckets: bk?.length ? bk : undefined } : d.kind === 'firstlast' ? { event: d.spec.event, which: d.spec.which, feature: d.spec.feature, field: d.spec.feature === 'field' ? d.spec.field : undefined, window_days: d.spec.window_days ?? 0, buckets: d.spec.feature === 'days_since' && bk?.length ? bk : undefined } : { event: d.spec.event, amount_field: d.spec.amount_field || 'amount', window_days: d.spec.window_days, bins: d.spec.bins, output: d.spec.output } }) };
    try { await onDone(await api.post<Tag>('/v1/tags', body)); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ maxWidth: 1040 }}>
      <div className="card-h"><h3>新建標籤</h3></div>
      <div className="grid c3">
        <label>顯示名<input aria-label="標籤名稱" value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
        <label>key（留空自動）<input aria-label="標籤 key" className="mono" value={d.key} placeholder={suggestKey(d.name) || 'tag_xxx'} onChange={(e) => setD({ ...d, key: e.target.value })} /></label>
        <label>目錄<select value={d.directory} onChange={(e) => setD({ ...d, directory: e.target.value })}>{(cf?.directories ?? ['客戶價值', '其他']).map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>建立方式<select aria-label="建立方式" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as TagKind, dtype: e.target.value === 'metric' && !d.buckets ? 'number' : d.dtype })}><option value="values">自定義標籤值（每值一條規則）</option><option value="sql">SQL 標籤</option><option value="metric">基礎指標值（次數／總和／均值…，可分桶）</option><option value="firstlast">首末次特徵（首次／末次的日期、距今天數、屬性）</option><option value="rfm">RFM（R／F／M 分級或八類客群）</option></select></label>
        <label>型別<select value={d.dtype} onChange={(e) => setD({ ...d, dtype: e.target.value as 'string' })}><option value="string">字串</option><option value="number">數值（推送時區間化）</option><option value="bool">布林</option></select></label>
        <label>說明<input value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></label>
      </div>
      {SPEC_KINDS.includes(d.kind) && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="grid c3">
            <label>事件<select aria-label="指標事件" value={d.spec.event} onChange={(e) => setS({ event: e.target.value })}><option value="">— 選事件 —</option>{(cat?.events ?? []).filter((e) => e.status !== 'deprecated').map((e) => <option key={e.name} value={e.name}>{e.cn}（{e.name}）</option>)}</select></label>
            <label>專案<input value={d.project} onChange={(e) => setD({ ...d, project: e.target.value })} /></label>
            <label>{d.kind === 'firstlast' ? '回溯天數（0＝不限）' : '回溯天數'}<input type="number" min={d.kind === 'firstlast' ? 0 : 1} max={730} value={d.spec.window_days ?? 90} onChange={(e) => setS({ window_days: Number(e.target.value) })} /></label>
            {d.kind === 'metric' && <>
              <label>指標<select aria-label="指標彙總" value={d.spec.agg} onChange={(e) => setS({ agg: e.target.value as TagSpec['agg'] })}>{(Object.keys(AGG_CN) as (keyof typeof AGG_CN)[]).map((k) => <option key={k} value={k}>{AGG_CN[k]}</option>)}</select></label>
              {['sum', 'avg', 'max', 'min'].includes(d.spec.agg ?? '') && <label>數值欄位<select aria-label="指標欄位" value={d.spec.field ?? ''} onChange={(e) => setS({ field: e.target.value })}><option value="">— 選欄位 —</option>{numFields.map((c) => <option key={c.name} value={c.name}>{c.cn}（{c.name}）</option>)}<option value="amount">amount</option></select></label>}
              <label>分桶界線（逗號分隔，留空輸出原值）<input aria-label="分桶" className="mono" placeholder="1, 3, 10" value={d.buckets} onChange={(e) => setD({ ...d, buckets: e.target.value, dtype: parseBuckets(e.target.value)?.length ? 'string' : 'number' })} /></label>
            </>}
            {d.kind === 'firstlast' && <>
              <label>首次／末次<select aria-label="首末次" value={d.spec.which} onChange={(e) => setS({ which: e.target.value as 'first' | 'last' })}><option value="first">首次</option><option value="last">末次</option></select></label>
              <label>特徵<select aria-label="特徵" value={d.spec.feature} onChange={(e) => setS({ feature: e.target.value as TagSpec['feature'] })}>{(Object.keys(FEATURE_CN) as (keyof typeof FEATURE_CN)[]).map((k) => <option key={k} value={k}>{FEATURE_CN[k]}</option>)}</select></label>
              {d.spec.feature === 'field' && <label>屬性<select aria-label="特徵欄位" value={d.spec.field ?? ''} onChange={(e) => setS({ field: e.target.value })}><option value="">— 選欄位 —</option>{(cat?.event_columns ?? []).map((c) => <option key={c.name} value={c.name}>{c.cn}（{c.name}）</option>)}</select></label>}
              {d.spec.feature === 'days_since' && <label>天數分桶（逗號分隔）<input aria-label="分桶" className="mono" placeholder="7, 30, 90" value={d.buckets} onChange={(e) => setD({ ...d, buckets: e.target.value })} /></label>}
            </>}
            {d.kind === 'rfm' && <>
              <label>金額欄位<select aria-label="金額欄位" value={d.spec.amount_field ?? 'amount'} onChange={(e) => setS({ amount_field: e.target.value })}><option value="amount">amount</option>{numFields.filter((c) => c.name !== 'amount').map((c) => <option key={c.name} value={c.name}>{c.cn}（{c.name}）</option>)}</select></label>
              <label>分級數<input type="number" min={2} max={10} value={d.spec.bins ?? 5} onChange={(e) => setS({ bins: Number(e.target.value) })} /></label>
              <label>輸出<select aria-label="RFM 輸出" value={d.spec.output} onChange={(e) => setS({ output: e.target.value as 'score' | 'segment' })}><option value="score">R／F／M 分數（R5F4M3）</option><option value="segment">八類客群（重要價值…一般挽留）</option></select></label>
            </>}
          </div>
          <p className="note">{d.spec.event ? specText({ kind: d.kind, spec: { ...d.spec, buckets: bk ?? [] } }, (e) => cat?.events.find((x) => x.name === e)?.cn ?? e) : '選一個事件。'}{bk === null ? ' 分桶需為遞增數字。' : bk.length ? `（標籤值：${bucketLabels(bk).join('／')}）` : ''}{d.kind === 'rfm' ? ' 各維度以 ntile 分級，高於中位為「高」；只計算窗口內有該事件的人。' : ''}</p>
        </div>
      )}
      {d.kind === 'values' ? (
        <div style={{ marginTop: 14 }}>
          {d.values.map((v, i) => (
            <div key={i} className="card" style={{ marginBottom: 8 }}>
              <div className="row" style={{ marginTop: 0 }}><b className="sub">值 {i + 1}</b><input aria-label={`標籤值 ${i + 1}`} placeholder="標籤值，例如 P1" value={v.value} maxLength={40} onChange={(e) => setV(i, { value: e.target.value })} style={{ width: 200 }} />
                <button className="btn-ghost sm" onClick={() => setOpen(open === i ? -1 : i)}>{open === i ? '收起規則' : `規則：${summarizeRule(v.rule, cat, segOpts).slice(0, 60) || '未設定'}`}</button>
                {d.values.length > 1 && <button className="btn-ghost sm danger" onClick={() => setD({ ...d, values: d.values.filter((_, j) => j !== i) })}>移除</button>}</div>
              {open === i && <div style={{ marginTop: 8 }}><RuleBuilder q={v.rule} cat={cat} segments={segOpts} onChange={(r) => setV(i, { rule: r })} /></div>}
            </div>))}
          {d.values.length < 24 && <button className="btn-ghost sm" onClick={() => { setD({ ...d, values: [...d.values, { value: '', rule: emptyRule('golden') }] }); setOpen(d.values.length); }}>＋ 新增值</button>}
        </div>
      ) : d.kind !== 'sql' ? null : (
        <div style={{ marginTop: 14 }}><textarea aria-label="標籤 SQL" value={d.sql} spellCheck={false} onChange={(e) => setD({ ...d, sql: e.target.value })} style={{ width: '100%', minHeight: 120, fontFamily: "'IBM Plex Mono', ui-monospace, monospace", fontSize: 12.5, padding: 10, border: '1px solid var(--line)', borderRadius: 8 }} />
          <p className="note">輸出 one_id_uint 與 tag_value 兩欄；與自定義 SQL 相同沙盒（唯讀、依權限表目錄、脫敏欄位會被擋）。</p></div>
      )}
      <div className="grid c3" style={{ marginTop: 14 }}>
        <label>更新方式<select aria-label="更新方式" value={d.update.mode} onChange={(e) => setD({ ...d, update: { ...d.update, mode: e.target.value as 'manual' | 'routine' } })}><option value="manual">手動</option><option value="routine">例行</option></select></label>
        <label>頻率<select value={d.update.freq} disabled={d.update.mode === 'manual'} onChange={(e) => setD({ ...d, update: { ...d.update, freq: e.target.value as 'daily' } })}><option value="daily">每天</option><option value="weekly">每週一</option><option value="monthly">每月 1 日</option></select></label>
        <label>最早計算時間<input type="time" value={d.update.earliest} disabled={d.update.mode === 'manual'} onChange={(e) => setD({ ...d, update: { ...d.update, earliest: e.target.value } })} /></label>
        <label>保留版本數<input type="number" min={1} max={100} value={d.update.keep} onChange={(e) => setD({ ...d, update: { ...d.update, keep: Number(e.target.value) || 1 } })} /></label>
        {(cf?.fields ?? []).map((f) => <label key={f.key}>{f.cn}{f.type === 'select' ? <select value={d.fields[f.key] ?? ''} onChange={(e) => setD({ ...d, fields: { ...d.fields, [f.key]: e.target.value } })}><option value="">—</option>{(f.options ?? []).map((o) => <option key={o}>{o}</option>)}</select> : <input value={d.fields[f.key] ?? ''} onChange={(e) => setD({ ...d, fields: { ...d.fields, [f.key]: e.target.value } })} />}</label>)}
      </div>
      <div className="row"><button className="btn-primary" disabled={!valid || busy} onClick={() => void save()}>{busy ? '建立中…' : '建立'}</button><button className="btn-ghost" onClick={onCancel}>取消</button>{err && <span className="alert">{err}</span>}</div>
    </div>
  );
}
