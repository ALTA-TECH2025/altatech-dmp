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
import { DTYPE_CN, TAG_KIND_CN, TAG_STATUS_CN, TAG_STATUS_PILL, fmtTs, suggestKey, topDistribution, updateText } from './tags';
import type { Tag, TagCreate, TagCustomFields, TagValue, TagVersion } from './types';

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
      <div className="page-h"><div><h1>標籤</h1><p>標籤 = 每個用戶一個值，存成 Bitmap 版本（dmp.user_tags），可在推送名單時附帶、在分析時當維度。第一階段兩種建立方式：自定義標籤值（每個值一條用戶規則）與 SQL 標籤；行銷建立需數位經營審批；例行標籤到點自動計算。</p></div>
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
          {tag.kind === 'values' ? <table className="tbl"><thead><tr><th>標籤值</th><th>規則</th><th className="num">人數（最新版）</th></tr></thead><tbody>{tag.values.map((v: TagValue) => <tr key={v.value}><td><b>{v.value}</b></td><td className="sub">{summarizeRule(v.rule, cat, segOpts)}</td><td className="num mono">{tag.latest?.distribution.find((d) => d.value === v.value)?.users.toLocaleString() ?? '—'}</td></tr>)}</tbody></table>
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

type Draft = { name: string; key: string; directory: string; description: string; dtype: 'string' | 'number' | 'bool'; kind: 'values' | 'sql'; values: TagValue[]; sql: string; update: Tag['update']; fields: Record<string, string> };
function TagForm({ cat, cf, segOpts, onDone, onCancel }: { cat: Catalog | null; cf: TagCustomFields | null; segOpts: { id: string; name: string }[]; onDone: (t: Tag) => Promise<void> | void; onCancel: () => void }) {
  const [d, setD] = useState<Draft>({ name: '', key: '', directory: cf?.directories[0] ?? '客戶價值', description: '', dtype: 'string', kind: 'values', values: [{ value: '', rule: emptyRule('golden') }], sql: "SELECT one_id_uint, toString(count()) AS tag_value\nFROM dmp.events WHERE event = 'bond_deal' AND event_date >= today() - 30\nGROUP BY one_id_uint", update: { mode: 'routine', freq: 'daily', earliest: '06:00', keep: 7 }, fields: {} });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(0);
  const setV = (i: number, p: Partial<TagValue>) => setD({ ...d, values: d.values.map((v, j) => (j === i ? { ...v, ...p } : v)) });
  const valid = d.name.trim() && (d.kind === 'sql' ? d.sql.trim().length > 10 : d.values.length > 0 && d.values.every((v) => v.value.trim() && ruleValid(v.rule)));
  const save = async () => {
    setBusy(true); setErr(null);
    const body: TagCreate = { name: d.name, key: d.key || suggestKey(d.name) || undefined, directory: d.directory, description: d.description, dtype: d.dtype, kind: d.kind, update: d.update, fields: d.fields,
      ...(d.kind === 'values' ? { values: d.values.map((v) => ({ value: v.value.trim(), rule: { ...v.rule, filters: v.rule.filters?.length ? v.rule.filters : undefined, behaviors: v.rule.behaviors?.length ? v.rule.behaviors : undefined } })) } : { sql: d.sql }) };
    try { await onDone(await api.post<Tag>('/v1/tags', body)); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ maxWidth: 1040 }}>
      <div className="card-h"><h3>新建標籤</h3></div>
      <div className="grid c3">
        <label>顯示名<input aria-label="標籤名稱" value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
        <label>key（留空自動）<input aria-label="標籤 key" className="mono" value={d.key} placeholder={suggestKey(d.name) || 'tag_xxx'} onChange={(e) => setD({ ...d, key: e.target.value })} /></label>
        <label>目錄<select value={d.directory} onChange={(e) => setD({ ...d, directory: e.target.value })}>{(cf?.directories ?? ['客戶價值', '其他']).map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>建立方式<select aria-label="建立方式" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as 'values' | 'sql' })}><option value="values">自定義標籤值（每值一條規則）</option><option value="sql">SQL 標籤</option></select></label>
        <label>型別<select value={d.dtype} onChange={(e) => setD({ ...d, dtype: e.target.value as 'string' })}><option value="string">字串</option><option value="number">數值（推送時區間化）</option><option value="bool">布林</option></select></label>
        <label>說明<input value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></label>
      </div>
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
      ) : (
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
