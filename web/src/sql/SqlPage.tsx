// 自定義 SQL（docs/21 §3）：左側表目錄（點欄位插入；脫敏欄位標示不可用）、編輯器（Ctrl+Enter 執行；{{參數}}）、結果（表格／折線／長條、複製前 100 列、下載 CSV）、
// 存為書籤（kind=sql，可加入看板）、已存查詢、查詢記錄、中間表（資訊管理／系統管理員）。唯讀：後端沙盒只放行 SELECT／WITH。
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { LineChart } from '@/analytics/LineChart';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import type { Bookmark, DashboardSummary } from '@/dashboards/types';
import { fmtTs, numericColumns, paramNames, toCsv } from './sql';
import type { MidTable, SqlCatalog, SqlResult, SqlRun, SqlSaved } from './types';

export function SqlPage() {
  return <RequireGrant module="events"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth();
  const [cat, setCat] = useState<SqlCatalog | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [sql, setSql] = useState('');
  const [params, setParams] = useState<Record<string, string>>({});
  const [res, setRes] = useState<SqlResult | null>(null);
  const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [side, setSide] = useState<'tables' | 'saved' | 'history' | 'mid'>('tables');
  const [view, setView] = useState<'table' | 'line' | 'bar'>('table');
  const [xCol, setXCol] = useState(''); const [yCols, setYCols] = useState<string[]>([]);
  const [openTable, setOpenTable] = useState<string | null>(null);
  const [saved, setSaved] = useState<SqlSaved[]>([]); const [history, setHistory] = useState<SqlRun[]>([]); const [mids, setMids] = useState<MidTable[]>([]);
  const [saveForm, setSaveForm] = useState<{ name: string; group: string; roles: string[] } | null>(null);
  const [bmForm, setBmForm] = useState<{ name: string; targets: string[]; dashboards: DashboardSummary[] } | null>(null);
  const [midForm, setMidForm] = useState<{ name: string; note: string } | null>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const loc = useLocation();
  const names = useMemo(() => paramNames(sql), [sql]);
  const canMid = can('governance', 'admin') || can('integration', 'admin');

  const loadSide = async () => {
    try { const [s, h, m] = await Promise.all([api.get<SqlSaved[]>('/v1/sql/saved'), api.get<SqlRun[]>('/v1/sql/history'), api.get<MidTable[]>('/v1/sql/mid-tables')]); setSaved(s); setHistory(h); setMids(m); } catch { /* 側欄載入失敗不擋主流程 */ }
  };
  useEffect(() => {
    const bm = (loc.state as { bookmark?: Bookmark } | null)?.bookmark;
    if (bm?.kind === 'sql' && typeof bm.query.sql === 'string') { setSql(bm.query.sql); setParams(Object.fromEntries(Object.entries((bm.query.params as Record<string, unknown>) ?? {}).map(([k, v]) => [k, String(v)]))); }
    api.get<SqlCatalog>('/v1/sql/tables').then((c) => { setCat(c); if (!sql && !bm && c.examples[0]) setSql(c.examples[0].sql); }).catch((e) => setDenied(e instanceof ApiError ? e.message : '無法載入表目錄'));
    void loadSide();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const insert = (text: string) => {
    const el = editor.current; if (!el) { setSql((s) => s + text); return; }
    const a = el.selectionStart ?? sql.length; const b = el.selectionEnd ?? a; const next = sql.slice(0, a) + text + sql.slice(b); setSql(next);
    requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = a + text.length; });
  };
  const run = async (explain = false) => {
    if (!sql.trim()) return; setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await api.post<SqlResult>('/v1/sql/query', { sql, params: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])), explain });
      setRes(r); if (!explain) { const num = numericColumns(r.columns, r.rows); setXCol(r.columns[0] ?? ''); setYCols(num.slice(0, 3)); setView('table'); void loadSide(); }
      else setMsg(`解析通過：掃描估算 ${r.scan_rows_estimate == null ? '—' : r.scan_rows_estimate.toLocaleString()} 列；血緣 ${r.lineage.join('、')}`);
    } catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); if (!explain) void loadSide(); } finally { setBusy(false); }
  };
  const copy = () => { if (res) void navigator.clipboard?.writeText(toCsv(res.columns, res.rows, 100)).then(() => setMsg('已複製前 100 列（CSV）')); };
  const save = async () => {
    if (!saveForm) return;
    try { await api.post('/v1/sql/saved', { name: saveForm.name, group: saveForm.group, sql, params_default: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])), shared: { roles: saveForm.roles, users: [] } }); setSaveForm(null); setMsg('已儲存查詢'); void loadSide(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '儲存失敗'); }
  };
  const openBm = async () => { const ds = (await api.get<DashboardSummary[]>('/v1/dashboards').catch(() => [] as DashboardSummary[])).filter((d) => d.editable); setBmForm({ name: saved.find((s) => s.sql === sql)?.name ?? 'SQL 書籤', targets: [], dashboards: ds }); };
  const saveBm = async () => {
    if (!bmForm) return;
    try { const b = await api.post<Bookmark>('/v1/bookmarks', { name: bmForm.name, kind: 'sql', query: { sql, params: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])) }, chart: 'auto', add_to: bmForm.targets }); setBmForm(null); setMsg(`已存書籤「${b.name}」${b.added_to?.length ? `，並加入 ${b.added_to.map((a) => a.name).join('、')}` : ''}`); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '存書籤失敗'); }
  };
  const createMid = async () => {
    if (!midForm) return;
    try { const m = await api.post<MidTable>('/v1/sql/mid-tables', { name: midForm.name, sql, note: midForm.note, params_default: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])) }); setMidForm(null); setMsg(`已建立中間表 dmp.${m.name}（${m.rows ?? '—'} 列）`); void loadSide(); const c = await api.get<SqlCatalog>('/v1/sql/tables'); setCat(c); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '建表失敗'); }
  };
  const series = useMemo(() => (res && view !== 'table' && xCol ? yCols.map((y) => ({ key: y, label: y, metric: y, group: '', points: res.rows.map((r) => ({ x: String(r[xCol] ?? ''), y: Number.isFinite(Number(r[y])) && r[y] !== null && r[y] !== '' ? Number(r[y]) : null, suppressed: false })) })) : []), [res, view, xCol, yCols]);

  if (denied) return <div className="page"><div className="page-h"><div><h1>自定義 SQL</h1></div></div><div className="alert">{denied}</div><p className="note">自定義 SQL 只開放數據分析、資訊管理與系統管理員；一般查數請用<Link to="/events">事件分析</Link>。</p></div>;
  return (
    <div className="page">
      <div className="page-h"><div><h1>自定義 SQL</h1><p>唯讀 SELECT／WITH 直接查 ClickHouse 熱層：只能引用你有權限的表，涉及脫敏欄位（L3／L4）會直接報錯；預覽 {cat?.limits.preview_rows ?? 1000} 列、{cat?.limits.timeout_s ?? 60} 秒逾時、每人 {cat?.limits.concurrent_per_user ?? 5} 條同時；每次執行都寫審計。大量取數請走匯出申請。</p></div></div>
      {err && <div className="alert">{err}</div>}{msg && <div className="note">{msg}</div>}
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 300px) minmax(0, 1fr)' }}>
        <div className="card flush" style={{ alignSelf: 'start' }}>
          <div className="seg" style={{ margin: 10 }}>{([['tables', '數據表'], ['saved', '已存'], ['history', '記錄'], ['mid', '中間表']] as const).map(([k, cn]) => <a key={k} href="#" className={side === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setSide(k); }}>{cn}</a>)}</div>
          {side === 'tables' && <div style={{ padding: '0 10px 10px', maxHeight: 560, overflow: 'auto' }}>
            {(cat?.tables ?? []).map((t) => (
              <div key={t.name} style={{ marginBottom: 6 }}>
                <div className="row" style={{ marginTop: 0, gap: 4 }}><button className="btn-ghost sm" onClick={() => setOpenTable(openTable === t.name ? null : t.name)}>{openTable === t.name ? '▾' : '▸'}</button><b className="mono" style={{ fontSize: 12, cursor: 'pointer' }} onClick={() => insert(t.name)}>{t.name}</b>{t.mid_table && <span className="pill custom">中間表</span>}</div>
                {openTable === t.name && <ul className="priv" style={{ marginLeft: 26 }}>{t.columns.map((c) => <li key={c.name} className={`mono${c.allowed ? '' : ' muted'}`} style={{ fontSize: 11.5, cursor: c.allowed ? 'pointer' : 'not-allowed', opacity: c.allowed ? 1 : 0.5 }} onClick={() => c.allowed && insert(c.name)} title={c.allowed ? c.type : `${c.level} 脫敏欄位，不可直接查詢`}>{c.name} <span className="sub" style={{ display: 'inline' }}>{c.type}{c.level !== 'L1' ? ` · ${c.level}` : ''}</span></li>)}</ul>}
              </div>))}
            <p className="note">函數：{cat?.functions.slice(0, 6).join('、')}…</p>
            <p className="note">範例：{cat?.examples.map((ex) => <button key={ex.cn} className="btn-ghost sm" style={{ margin: 2 }} onClick={() => setSql(ex.sql)}>{ex.cn}</button>)}</p>
          </div>}
          {side === 'saved' && <div style={{ padding: '0 10px 10px' }}>{saved.length === 0 ? <p className="note">還沒有已存查詢。</p> : saved.map((s) => <div key={s.id} className="row" style={{ marginTop: 4, gap: 4 }}><button className="btn-ghost sm" onClick={() => { setSql(s.sql); setParams(Object.fromEntries(Object.entries(s.params_default ?? {}).map(([k, v]) => [k, String(v)]))); }}>{s.name}</button><span className="sub">{s.group}{s.mine ? '' : ` · ${s.owner}`}</span>{s.editable && <button className="btn-ghost sm danger" onClick={() => { if (confirm(`刪除「${s.name}」？`)) api.del(`/v1/sql/saved/${s.id}`).then(loadSide).catch((e: Error) => setErr(e.message)); }}>刪</button>}</div>)}</div>}
          {side === 'history' && <div style={{ padding: '0 10px 10px', maxHeight: 560, overflow: 'auto' }}>{history.length === 0 ? <p className="note">30 天內沒有查詢記錄。</p> : history.map((h) => <div key={h.id} className="row" style={{ marginTop: 4, gap: 4 }}><button className="btn-ghost sm" onClick={() => setSql(h.sql)} title={h.sql}>{fmtTs(h.at).slice(5, 16)}</button><span className={`pill ${h.status === 'ok' ? 'blue' : 'red'}`}>{h.status === 'ok' ? `${h.rows} 列 · ${h.ms} ms` : '失敗'}</span><span className="sub mono" style={{ fontSize: 10.5 }}>{h.sql.slice(0, 40)}</span></div>)}</div>}
          {side === 'mid' && <div style={{ padding: '0 10px 10px' }}>{mids.length === 0 ? <p className="note">還沒有中間表。{canMid ? '執行查詢後按「存為中間表」。' : '需資訊管理或系統管理員建立。'}</p> : mids.map((m) => <div key={m.name} className="row" style={{ marginTop: 4, gap: 4 }}><b className="mono" style={{ fontSize: 12, cursor: 'pointer' }} onClick={() => insert(`dmp.${m.name}`)}>dmp.{m.name}</b><span className="sub">{m.rows ?? '—'} 列 · {fmtTs(m.refreshed_at).slice(5, 16)}</span>{canMid && <><button className="btn-ghost sm" onClick={() => api.post(`/v1/sql/mid-tables/${m.name}/refresh`).then(loadSide).catch((e: Error) => setErr(e.message))}>重算</button><button className="btn-ghost sm danger" onClick={() => { if (confirm(`刪除中間表 ${m.name}？`)) api.del(`/v1/sql/mid-tables/${m.name}`).then(loadSide).catch((e: Error) => setErr(e.message)); }}>刪</button></>}</div>)}</div>}
        </div>
        <div className="stack">
          <div className="card">
            <textarea ref={editor} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} aria-label="SQL" style={{ width: '100%', minHeight: 180, fontFamily: "'IBM Plex Mono', ui-monospace, monospace", fontSize: 12.5, padding: 10, border: '1px solid var(--line)', borderRadius: 8, resize: 'vertical' }}
              onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void run(); } }} placeholder="SELECT event_date, count() AS n FROM dmp.events WHERE event = 'bond_deal' GROUP BY event_date" />
            {names.length > 0 && <div className="row" style={{ marginTop: 8 }}><span className="sub">動態參數：</span>{names.map((n) => <label key={n} className="sub" style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>{`{{${n}}}`}<input value={params[n] ?? ''} onChange={(e) => setParams({ ...params, [n]: e.target.value })} style={{ width: 140 }} /></label>)}</div>}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn-primary" disabled={busy || !sql.trim()} onClick={() => void run()}>{busy ? '執行中…' : '執行（Ctrl+Enter）'}</button>
              <button className="btn-ghost" disabled={busy || !sql.trim()} onClick={() => void run(true)}>解析／估算</button>
              <button className="btn-ghost" disabled={!sql.trim()} onClick={() => setSaveForm({ name: '', group: '', roles: [] })}>儲存查詢</button>
              <button className="btn-ghost" disabled={!sql.trim()} onClick={() => void openBm()}>存為書籤 / 加入看板</button>
              {canMid && <button className="btn-ghost" disabled={!sql.trim()} onClick={() => setMidForm({ name: '', note: '' })}>存為中間表</button>}
              <button className="btn-ghost" onClick={() => { setSql(''); setRes(null); }}>清空</button>
            </div>
            {saveForm && <div className="row" style={{ marginTop: 8 }}><input placeholder="名稱" value={saveForm.name} onChange={(e) => setSaveForm({ ...saveForm, name: e.target.value })} /><input placeholder="分組（選填）" value={saveForm.group} onChange={(e) => setSaveForm({ ...saveForm, group: e.target.value })} /><select multiple value={saveForm.roles} onChange={(e) => setSaveForm({ ...saveForm, roles: Array.from(e.target.selectedOptions).map((o) => o.value) })} aria-label="分享角色" style={{ height: 52 }}>{[['data_analyst', '數據分析'], ['it_admin', '資訊管理'], ['digital_ops', '數位經營']].map(([k, cn]) => <option key={k} value={k}>{cn}</option>)}</select><button className="btn-primary" disabled={!saveForm.name.trim()} onClick={() => void save()}>儲存</button><button className="btn-ghost" onClick={() => setSaveForm(null)}>取消</button></div>}
            {bmForm && <div className="row" style={{ marginTop: 8 }}><input placeholder="書籤名稱" value={bmForm.name} onChange={(e) => setBmForm({ ...bmForm, name: e.target.value })} /><select multiple value={bmForm.targets} onChange={(e) => setBmForm({ ...bmForm, targets: Array.from(e.target.selectedOptions).map((o) => o.value) })} aria-label="加入看板" style={{ height: 52 }}>{bmForm.dashboards.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select><button className="btn-primary" disabled={!bmForm.name.trim()} onClick={() => void saveBm()}>儲存書籤</button><button className="btn-ghost" onClick={() => setBmForm(null)}>取消</button><span className="sub">看板卡片以表格呈現；檢視者需有自定義 SQL 權限</span></div>}
            {midForm && <div className="row" style={{ marginTop: 8 }}><span className="sub">dmp.mid_</span><input placeholder="名稱（小寫英數底線）" value={midForm.name} onChange={(e) => setMidForm({ ...midForm, name: e.target.value })} /><input placeholder="說明" value={midForm.note} onChange={(e) => setMidForm({ ...midForm, note: e.target.value })} /><button className="btn-primary" disabled={!/^[a-z][a-z0-9_]{1,40}$/.test(midForm.name)} onClick={() => void createMid()}>建立</button><button className="btn-ghost" onClick={() => setMidForm(null)}>取消</button></div>}
          </div>
          {res && !res.explained && (
            <div className="card">
              <div className="card-h"><h3>結果</h3><span className="sub">{res.row_count} 列{res.truncated ? '（已截斷）' : ''} · {res.ms} ms{res.scan_rows_estimate != null ? ` · 掃描估算 ${res.scan_rows_estimate.toLocaleString()}` : ''} · {res.lineage.join('、')}</span>
                <div className="right row" style={{ marginTop: 0 }}>
                  <div className="seg">{([['table', '表格'], ['line', '折線'], ['bar', '長條']] as const).map(([k, cn]) => <a key={k} href="#" className={view === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setView(k); }}>{cn}</a>)}</div>
                  <button className="btn-ghost sm" onClick={copy}>複製前 100 列</button>
                  {can('events', 'export') && <a className="btn-ghost sm" href={`/v1/sql/query/${res.run_id}/download`} target="_blank" rel="noreferrer">下載 CSV</a>}
                </div></div>
              {res.warnings.length > 0 && <p className="note" style={{ marginTop: 0 }}>{res.warnings.join('；')}</p>}
              {view !== 'table' && <div className="row" style={{ marginBottom: 8 }}><label className="sub">X <select value={xCol} onChange={(e) => setXCol(e.target.value)}>{res.columns.map((c) => <option key={c} value={c}>{c}</option>)}</select></label><label className="sub">Y <select multiple value={yCols} onChange={(e) => setYCols(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: 52 }}>{numericColumns(res.columns, res.rows).map((c) => <option key={c} value={c}>{c}</option>)}</select></label></div>}
              {view === 'table' ? <div className="tbl-wrap" style={{ maxHeight: 480, overflow: 'auto' }}><table className="tbl"><thead><tr>{res.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead><tbody>{res.rows.map((r, i) => <tr key={i}>{res.columns.map((c) => <td key={c} className={typeof r[c] === 'number' ? 'num mono' : 'mono'}>{r[c] == null ? '—' : typeof r[c] === 'object' ? JSON.stringify(r[c]) : String(r[c])}</td>)}</tr>)}</tbody></table></div>
                : series.length ? <LineChart series={series} kind={view} height={260} /> : <p className="note">選一個數值欄當 Y。</p>}
              <details style={{ marginTop: 8 }}><summary className="sub">實際執行的 SQL</summary><pre className="code">{res.sql_effective}</pre></details>
            </div>)}
        </div>
      </div>
    </div>
  );
}
