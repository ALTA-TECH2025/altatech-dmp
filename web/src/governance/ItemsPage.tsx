// 維度表與字典（對齊神策元數據管理）：維度表（清單、定義、新增、編輯欄位顯示名／敏感等級、立即同步）、維度字典（上傳 CSV append／overwrite）、虛擬屬性（建立）。
import { useEffect, useState, type FormEvent } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { fmtNum } from '@/analytics/series';
import { dimRefs, parseDictCsv, SENS_PILL } from './governance';
import type { Dictionary, DimensionTable, DimensionTableList, Level, VirtualProperty } from './types';

type Tab = 'tables' | 'dicts' | 'virtual';

export function ItemsPage() {
  return <RequireGrant module="governance"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const canUse = can('governance', 'use');
  const [tab, setTab] = useState<Tab>('tables');
  const [dt, setDt] = useState<DimensionTableList | null>(null); const [dicts, setDicts] = useState<Dictionary[]>([]); const [vps, setVps] = useState<VirtualProperty[]>([]);
  const [cur, setCur] = useState<string>('dim_instrument'); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const reload = async () => { try { const [a, b, c] = await Promise.all([api.get<DimensionTableList>('/v1/meta/dimension-tables'), api.get<Dictionary[]>('/v1/meta/dictionaries'), api.get<VirtualProperty[]>('/v1/meta/virtual-properties')]); setDt(a); setDicts(b); setVps(c); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => { void reload(); }, []);
  const t = dt?.items.find((x) => x.id === cur) ?? dt?.items[0];
  return (
    <div className="page">
      <div className="page-h"><div><h1>維度表與字典</h1><p><b>維度表</b>以「屬性值 = 主鍵」補充事件／用戶沒有埋的維度，欄位成為關聯屬性；<b>維度字典</b>給屬性值顯示名；<b>虛擬屬性</b>以表達式組合既有屬性與維度表欄位。資料由「數據整合 › 同步任務」寫入。</p></div></div>
      {err && <div className="alert">{err}</div>}{msg && <div className="note">{msg}</div>}
      <div className="seg">{([['tables', `維度表 ${dt?.items.length ?? ''}`], ['dicts', `維度字典 ${dicts.length}`], ['virtual', `虛擬屬性 ${vps.length}`]] as [Tab, string][]).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
      {tab === 'tables' && dt && t && <TablesTab dt={dt} t={t} setCur={setCur} canUse={canUse} onChange={reload} onMsg={setMsg} />}
      {tab === 'dicts' && <DictsTab dicts={dicts} canUse={canUse} onChange={reload} />}
      {tab === 'virtual' && <VirtualTab vps={vps} tables={dt?.items ?? []} canUse={canUse} onChange={reload} setCur={(id) => { setCur(id); setTab('tables'); }} />}
    </div>
  );
}

function TablesTab({ dt, t, setCur, canUse, onChange, onMsg }: { dt: DimensionTableList; t: DimensionTable; setCur: (id: string) => void; canUse: boolean; onChange: () => Promise<void>; onMsg: (m: string | null) => void }) {
  const [edit, setEdit] = useState(false); const [cols, setCols] = useState(t.columns); const [cn, setCn] = useState(t.cn); const [err, setErr] = useState<string | null>(null); const [open, setOpen] = useState(false);
  const [nf, setNf] = useState({ id: 'dim_', cn: '', primary_key: '', item_type: '', join_table: 'events', join_column: '', source: '', columns: '' });
  useEffect(() => { setCols(t.columns); setCn(t.cn); setEdit(false); }, [t]);
  const save = async () => { setErr(null); try { await api.patch(`/v1/meta/dimension-tables/${t.id}`, { cn, columns: cols }); setEdit(false); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const sync = async () => { try { const r = await api.post<{ accepted: boolean; message: string }>(`/v1/meta/dimension-tables/${t.id}/sync`); onMsg(r.message); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const create = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try { await api.post('/v1/meta/dimension-tables', { id: nf.id, cn: nf.cn, primary_key: nf.primary_key, item_type: nf.item_type || undefined, join: [{ table: nf.join_table, column: nf.join_column }], source: nf.source || undefined, columns: nf.columns.split(/\r?\n/).map((l) => l.split(',').map((x) => x.trim())).filter((p) => p[0]).map((p) => ({ key: p[0], cn: p[1] || p[0], type: p[2] || 'string', sensitivity: (p[3] || 'L1') as Level })) }); setOpen(false); setCur(nf.id); await onChange(); }
    catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); }
  };
  return (
    <>
      {err && <div className="alert">{err}</div>}
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><span className="note" style={{ marginTop: 0 }}>{dt.stats_source ? '列數 = ClickHouse 實際（表尚未由同步任務建立者顯示「未建」）' : '未連接 ClickHouse：沒有列數'} · 關聯鍵必須唯一；&gt; {fmtNum(Number(dt.rules.max_rows_for_join ?? 1000000))} 列改 dictGet</span>{canUse && <button className="btn-ghost right" onClick={() => setOpen((v) => !v)}>＋ 新增維度表</button>}</div>
      {open && <form className="card stack" style={{ marginBottom: 12 }} onSubmit={create}><div className="grid c3">
        <label>id（dim_ 開頭）<input className="mono" value={nf.id} onChange={(e) => setNf({ ...nf, id: e.target.value })} required /></label><label>顯示名<input value={nf.cn} onChange={(e) => setNf({ ...nf, cn: e.target.value })} required /></label><label>主鍵欄位<input className="mono" value={nf.primary_key} onChange={(e) => setNf({ ...nf, primary_key: e.target.value })} required /></label>
        <label>item_type<input className="mono" value={nf.item_type} onChange={(e) => setNf({ ...nf, item_type: e.target.value })} /></label><label>關聯表<select value={nf.join_table} onChange={(e) => setNf({ ...nf, join_table: e.target.value })}><option value="events">events</option><option value="users">users</option></select></label><label>關聯屬性（事件／用戶屬性）<input className="mono" value={nf.join_column} onChange={(e) => setNf({ ...nf, join_column: e.target.value })} required /></label>
        <label>來源同步任務 id（選填，須先在數據整合建立）<input className="mono" value={nf.source} onChange={(e) => setNf({ ...nf, source: e.target.value })} /></label></div>
        <label>欄位（每行：key,顯示名,型別,敏感等級）<textarea rows={4} className="mono" value={nf.columns} onChange={(e) => setNf({ ...nf, columns: e.target.value })} placeholder={'name,名稱,string,L0\ncategory,類型,enum,L0'} /></label>
        <div className="row" style={{ marginTop: 0 }}><button className="btn-primary">建立</button><span className="note" style={{ marginTop: 0 }}>主鍵須唯一；關聯屬性須存在；欄位不得 L4。</span></div></form>}
      <div className="tiles" style={{ marginBottom: 16 }}>{dt.items.map((d) => <button key={d.id} className="tile" style={{ textAlign: 'left', cursor: 'pointer', outline: d.id === t.id ? '2px solid var(--brand)' : undefined }} onClick={() => setCur(d.id)}><span className="sub">{d.cn}</span><b style={{ fontSize: 20 }}>{d.rows === null ? (d.exists ? '0' : '未建') : fmtNum(d.rows)}<small> 列</small></b><span className="sub mono">{d.id}{d.origin === 'registered' && ' · 待發版'}</span></button>)}</div>
      <div className="grid c2">
        <div className="card"><div className="card-h"><h3>{edit ? <input value={cn} onChange={(e) => setCn(e.target.value)} /> : t.cn} <span className="mono note" style={{ marginTop: 0 }}>{t.id}</span></h3><span className="sub">主鍵 {t.primary_key} · {t.rows === null ? '—' : t.rows.toLocaleString()} 列 · 同步 {t.sync || '—'} · {t.engine_effective === 'dictionary' ? 'dictGet' : 'JOIN'}</span></div>
          <dl className="kv" style={{ marginBottom: 10 }}><dt>關聯條件</dt><dd className="mono" style={{ fontSize: 11.5 }}>{t.join.map((j) => `${j.table}.${j.column} = ${t.id}.${j.dim_column ?? t.primary_key}`).join('；')}</dd><dt>item_type</dt><dd className="mono">{t.item_type}</dd><dt>來源</dt><dd>{t.source || '—'}{t.task && <span className="pill blue">任務 {t.task.status ?? ''}</span>}{t.source && !t.task && <span className="pill amber">任務未登記</span>}</dd>{t.row_level && <><dt>行級</dt><dd className="mono">{t.row_level}</dd></>}</dl>
          <div className="tbl-wrap"><table className="tbl"><thead><tr><th>欄位</th><th>顯示名</th><th>型別</th><th>敏感</th><th>關聯屬性</th></tr></thead><tbody>{cols.map((c, i) => <tr key={c.key}><td className="mono">{c.key}</td><td>{edit ? <input value={c.cn} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, cn: e.target.value } : x)))} /> : c.cn}</td><td className="mono">{c.type}</td><td>{edit ? <select value={c.sensitivity} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, sensitivity: e.target.value as Level } : x)))}>{(['L0', 'L1', 'L2', 'L3'] as Level[]).map((s) => <option key={s}>{s}</option>)}</select> : <span className={`pill ${SENS_PILL[c.sensitivity]}`}>{c.sensitivity}</span>}</td><td>{edit ? <input type="checkbox" checked={c.expose ?? c.exposed ?? true} onChange={(e) => setCols(cols.map((x, j) => (j === i ? { ...x, expose: e.target.checked } : x)))} /> : ((c.expose ?? c.exposed ?? true) ? '✓' : '—')}</td></tr>)}</tbody></table></div>
          {t.note && <p className="note">{t.note}</p>}
          {canUse && <div className="row">{edit ? <><button className="btn-primary" onClick={() => void save()}>儲存</button><button className="btn-ghost" onClick={() => { setEdit(false); setCols(t.columns); setCn(t.cn); }}>取消</button></> : <><button className="btn-ghost sm" onClick={() => setEdit(true)}>編輯顯示名／敏感等級</button><button className="btn-ghost sm" onClick={() => void sync()}>立即同步</button></>}</div>}
        </div>
        <div className="stack">
          <div className="card"><div className="card-h"><h3>用在哪裡</h3></div><ul className="priv">{(t.used_by ?? []).length ? t.used_by!.map((u) => <li key={u}>{u}</li>) : <li>曝露欄位：{t.exposed.map((e) => `${t.id}.${e}`).join('、') || '—'}（事件分析／漏斗可篩選、分組）</li>}</ul></div>
          <div className="card"><div className="card-h"><h3>維度表的規則</h3></div><ul className="priv"><li>維度表用來<b>補充事件／用戶屬性沒有埋的維度</b>：以「屬性值 = 主鍵」關聯，欄位成為可篩選、可分組的關聯屬性；事件仍然屬於用戶。</li><li>關聯鍵必須是主鍵（唯一），否則查詢會放大列數。</li><li>建議行數 &lt; 100 萬；超過時改以 dictGet，不走 JOIN。</li><li>來源由「數據整合 › 同步任務」寫入（upsert by 主鍵）或 item_set 協議上報；欄位有敏感等級，依角色隱碼；敏感等級只能升。</li></ul></div>
        </div>
      </div>
    </>
  );
}

function DictsTab({ dicts, canUse, onChange }: { dicts: Dictionary[]; canUse: boolean; onChange: () => Promise<void> }) {
  const [sel, setSel] = useState<Dictionary | null>(null); const [prop, setProp] = useState(''); const [csv, setCsv] = useState(''); const [mode, setMode] = useState<'append' | 'overwrite'>('append'); const [err, setErr] = useState<string | null>(null);
  const view = async (p: string) => { try { setSel(await api.get<Dictionary>(`/v1/meta/dictionaries/${encodeURIComponent(p)}`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const upload = async (e: FormEvent) => { e.preventDefault(); setErr(null); const entries = parseDictCsv(csv); if (!Object.keys(entries).length) { setErr('沒有可用的「值,顯示名」'); return; } try { await api.put(`/v1/meta/dictionaries/${encodeURIComponent(prop)}?mode=${mode}`, { entries }); setCsv(''); await onChange(); await view(prop); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  return (
    <>
      {err && <div className="alert">{err}</div>}
      <div className="grid c21">
        <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>屬性</th><th>映射</th><th className="num">值數</th><th>範例</th><th>來源</th><th>更新</th><th></th></tr></thead>
          <tbody>{dicts.map((d) => <tr key={d.property}><td><b className="mono">{d.property}</b></td><td><span className="pill">{d.mode === 'n_to_1' ? 'N:1' : '1:1'}</span></td><td className="num mono">{d.count}</td><td>{d.sample.slice(0, 4).map(([k, v]) => <span className="pill mono" key={k} style={{ fontSize: 10 }}>{k} → {v}</span>)}{d.count > 4 && <span className="note">…</span>}</td><td style={{ fontSize: 12 }}>{typeof d.source === 'string' ? (d.source === 'manual_upload' ? '手動上傳' : d.source) : `維度表 ${d.source.dimension_table}.${d.source.column}`}</td><td className="mono" style={{ fontSize: 11 }}>{d.updated_at?.slice(0, 10) ?? '語意層'}</td><td className="num"><button className="btn-ghost sm" onClick={() => void view(d.property)}>查看</button>{canUse && typeof d.source === 'string' && <button className="btn-ghost sm" onClick={() => { setProp(d.property); setMode('append'); }}>上傳</button>}</td></tr>)}</tbody></table></div></div>
        <div className="stack">
          {canUse && <form className="card stack" onSubmit={upload}><div className="card-h"><h3>上傳字典</h3></div>
            <label>屬性<input className="mono" value={prop} onChange={(e) => setProp(e.target.value)} placeholder="例：reason_code（只限文字／數值屬性）" required /></label>
            <label>CSV（值,顯示名；# 開頭略過）<textarea rows={6} className="mono" value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'E001,餘額不足\nSYS02,委託閘道逾時\nSYS03,委託閘道逾時'} /></label>
            <div className="row" style={{ marginTop: 0 }}><select value={mode} onChange={(e) => setMode(e.target.value as 'append' | 'overwrite')}><option value="append">新增（保留既有）</option><option value="overwrite">覆蓋（先清空）</option></select><button className="btn-primary">上傳</button></div><p className="note">N:1 允許（多個代碼同一顯示名）；上傳寫審計 dictionary.uploaded。</p></form>}
          {sel && <div className="card"><div className="card-h"><h3>{sel.property}</h3><span className="sub">{sel.count} 筆 · {sel.mode === 'n_to_1' ? 'N:1' : '1:1'}</span><button className="btn-ghost sm right" onClick={() => setSel(null)}>關閉</button></div><div className="tbl-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}><table className="tbl"><tbody>{Object.entries(sel.entries ?? {}).slice(0, 500).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td>{v}</td></tr>)}</tbody></table></div></div>}
          <div className="card"><div className="card-h"><h3>字典與維度表的差別</h3></div><p className="note">字典只是顯示名，不新增欄位：篩選與分組時以顯示名呈現並可選，原值仍保留。要補多個維度（產業、類型、區域）用維度表。</p></div>
        </div>
      </div>
    </>
  );
}

function VirtualTab({ vps, tables, canUse, onChange, setCur }: { vps: VirtualProperty[]; tables: DimensionTable[]; canUse: boolean; onChange: () => Promise<void>; setCur: (id: string) => void }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ name: 'v.', cn: '', scope: 'event', expr: '', note: '' });
  const refs = dimRefs(f.expr); const unknown = refs.filter((r) => !tables.some((t) => t.id === r));
  const submit = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api.post('/v1/meta/virtual-properties', f); setOpen(false); setF({ name: 'v.', cn: '', scope: 'event', expr: '', note: '' }); await onChange(); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  return (
    <>
      {canUse && <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><button className="btn-ghost" onClick={() => setOpen((v) => !v)}>＋ 建立虛擬屬性</button></div>}
      {open && <form className="card stack" style={{ marginBottom: 12 }} onSubmit={submit}>{err && <div className="alert">{err}</div>}<div className="grid c3">
        <label>名稱（v. 開頭）<input className="mono" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></label><label>顯示名<input value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} /></label><label>範圍<select value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}><option value="event">事件</option><option value="user">用戶</option></select></label></div>
        <label>表達式（ClickHouse 運算式，≤ 1024 字元；維度表用 dictGet('dim_x', '欄位', 關聯屬性)）<textarea rows={3} className="mono" value={f.expr} onChange={(e) => setF({ ...f, expr: e.target.value })} required maxLength={1024} /></label>
        <div className="row" style={{ marginTop: 0 }}><span className="note" style={{ marginTop: 0 }}>引用維度表：{refs.join('、') || '無'}{unknown.length > 0 && <span style={{ color: 'var(--red)' }}>（不存在：{unknown.join('、')}）</span>}</span><button className="btn-primary" disabled={unknown.length > 0}>建立</button></div></form>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>虛擬屬性</th><th>範圍</th><th>表達式</th><th>引用維度表</th><th>說明</th><th>來源</th></tr></thead>
        <tbody>{vps.map((v) => <tr key={v.name}><td><b className="mono">{v.name}</b><span className="sub">{v.cn}</span></td><td><span className="pill">{v.scope === 'event' ? '事件' : '用戶'}</span></td><td className="mono" style={{ fontSize: 11, maxWidth: 380, overflowWrap: 'anywhere' }}>{v.expr}</td><td>{v.uses.map((u) => <a key={u} href="#" className="pill blue" onClick={(e) => { e.preventDefault(); setCur(u); }}>{u}</a>)}{v.uses.length === 0 && '—'}</td><td style={{ fontSize: 12 }}>{v.note}</td><td>{v.origin === 'catalog' ? <span className="pill gray">語意層</span> : <span className="pill amber">待發版</span>}</td></tr>)}</tbody></table></div></div>
      <p className="note">虛擬屬性以表達式組合事件／用戶屬性與維度表欄位，查詢時計算、不落地；名稱不可與既有屬性重複；可用於篩選、分組與標籤定義。</p>
    </>
  );
}
