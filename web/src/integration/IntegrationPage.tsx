// 數據整合：數據源管理（連線：secret_ref、測試診斷、輪替、最小權限）→ 數據表管理（登記、探索欄位與敏感等級、重新探索）→ 同步任務（建立精靈、預覽、立即執行、執行紀錄）。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { isSecretRef, looksLikeSecret, parseList, rotationText, runPill, runText, RUN_STATUS_CN, scheduleBody, SENS_PILL, STEP_CN, TASK_STATUS } from './integration';
import type { Connection, ConnectionCreate, ConnectionTest, Connector, ExtTable, Preview, Privileges, RunNow, SyncRun, SyncTask, SyncTaskCreate, TableCreate } from './types';

type Tab = 'connections' | 'tables' | 'tasks' | 'wizard';
const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function IntegrationPage() {
  return <RequireGrant module="integration"><IntegrationInner /></RequireGrant>;
}

function IntegrationInner() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>('connections');
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [conns, setConns] = useState<Connection[]>([]);
  const [tables, setTables] = useState<ExtTable[]>([]);
  const [tasks, setTasks] = useState<SyncTask[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canUse = can('integration', 'use'); const canAdmin = can('integration', 'admin');
  const reload = async () => {
    setErr(null);
    try {
      const [cs, cn, tb, tk] = await Promise.all([api.get<Connector[]>('/v1/integration/connectors'), api.get<Connection[]>('/v1/integration/connections'), api.get<ExtTable[]>('/v1/integration/tables'), api.get<SyncTask[]>('/v1/integration/sync-tasks')]);
      setConnectors(cs); setConns(cn); setTables(tb); setTasks(tk);
    } catch (e) { setErr(errText(e, '讀取失敗')); }
  };
  useEffect(() => { void reload(); }, []);
  const run = async (fn: () => Promise<void>, fallback: string) => { setBusy(true); setErr(null); try { await fn(); await reload(); } catch (e) { setErr(errText(e, fallback)); } finally { setBusy(false); } };
  const warnTasks = tasks.filter((t) => t.status !== 'active').length;
  return (
    <div className="page">
      <div className="page-h"><div><h1>數據整合</h1><p>數據源管理 → 數據表管理 → 同步任務（目標：事件表／用戶表／維度表；全量／增量微批）→ 清洗與隔離。連線只存密鑰庫參照，畫面永遠看不到明文；任務是建模產物（模型 yaml）＋來源表＋排程，立即執行會寫入 ClickHouse 並三方對帳。</p></div></div>
      <div className="seg">{([['connections', `數據源 ${conns.length}`], ['tables', `數據表 ${tables.length}`], ['tasks', `同步任務 ${tasks.length}${warnTasks ? ` ⚠${warnTasks}` : ''}`], ['wizard', '建立任務']] as [Tab, string][]).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
      {err && <div className="alert" style={{ marginBottom: 12 }}>{err}</div>}
      {tab === 'connections' && <ConnectionsTab connectors={connectors} conns={conns} busy={busy} canUse={canUse} canAdmin={canAdmin} run={run} />}
      {tab === 'tables' && <TablesTab conns={conns} tables={tables} busy={busy} canUse={canUse} run={run} />}
      {tab === 'tasks' && <TasksTab tasks={tasks} busy={busy} canUse={canUse} run={run} onNew={() => setTab('wizard')} />}
      {tab === 'wizard' && <Wizard tables={tables} tasks={tasks} canUse={canUse} onDone={async () => { await reload(); setTab('tasks'); }} />}
    </div>
  );
}

/* ------------------------------------------------------------------ 數據源 */
function ConnectionsTab({ connectors, conns, busy, canUse, canAdmin, run }: { connectors: Connector[]; conns: Connection[]; busy: boolean; canUse: boolean; canAdmin: boolean; run: (fn: () => Promise<void>, fb: string) => Promise<void> }) {
  const [creating, setCreating] = useState(false);
  const [test, setTest] = useState<{ id: string; res: ConnectionTest } | null>(null);
  const [priv, setPriv] = useState<Privileges | null>(null);
  const [rotateId, setRotateId] = useState<string | null>(null);
  const [newRef, setNewRef] = useState('');
  const byType = Object.fromEntries(connectors.map((c) => [c.type, c]));
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}><span className="note">密鑰原則：平台只存 secret_ref（Secret Manager／OpenBao／K8s Secret），執行節點以 Workload Identity 取值；密碼型憑證 90 天輪替、到期前 14 天提醒。</span>
        {canAdmin && <button className="btn-primary right" onClick={() => setCreating(true)}>＋ 新增連線</button>}</div>
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>連線</th><th>類型 / 鑑權</th><th>密鑰 / 輪替</th><th>最後測試</th><th className="num">表 / 任務</th><th>狀態</th><th>動作</th></tr></thead>
        <tbody>{conns.map((c) => { const rot = rotationText(c.rotation); return (
          <tr key={c.id}>
            <td><b className="mono">{c.id}</b><span className="sub">{Object.entries(c.fields).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${String(v)}`).join(' · ')}{c.owner ? ` · 負責人 ${c.owner}` : ''}</span></td>
            <td>{byType[c.type]?.cn ?? c.type}<span className="sub mono">{c.auth_method}{c.tls_verify ? ' · TLS 驗證' : ''}</span></td>
            <td><span className={`pill ${rot.pill}`}>{rot.text}</span>{Object.entries(c.secrets).map(([k, s]) => <span key={k} className="sub mono">{k}: {s.value} → {s.ref}</span>)}</td>
            <td>{c.last_test ? <><span className={`pill ${c.last_test.ok ? 'blue' : 'red'}`}>{c.last_test.ok ? '通過' : `失敗於 ${STEP_CN[c.last_test.failed_step as keyof typeof STEP_CN] ?? c.last_test.failed_step}`}</span><span className="sub">{c.last_test.at.slice(0, 16).replace('T', ' ')}</span></> : <span className="sub">未測試</span>}</td>
            <td className="num mono">{c.tables} / {c.tasks}</td>
            <td><span className={`pill ${c.enabled ? 'blue' : 'gray'}`}>{c.enabled ? '啟用' : '已停用'}</span></td>
            <td><div className="row" style={{ marginTop: 0, flexWrap: 'nowrap' }}>
              {canUse && <button className="btn-ghost sm" disabled={busy} onClick={() => void run(async () => { setTest({ id: c.id, res: await api.post<ConnectionTest>(`/v1/integration/connections/${c.id}/test`) }); }, '測試失敗')}>測試</button>}
              <button className="btn-ghost sm" disabled={busy} onClick={() => void run(async () => { setPriv(await api.get<Privileges>(`/v1/integration/connections/${c.id}/privileges`)); }, '讀取失敗')}>權限</button>
              {canAdmin && c.rotation.kind === 'rotating' && <button className="btn-ghost sm" disabled={busy} onClick={() => { setRotateId(c.id); setNewRef(''); }}>輪替</button>}
              {canAdmin && c.enabled && <button className="btn-ghost sm danger" disabled={busy} onClick={() => void run(async () => { await api.del(`/v1/integration/connections/${c.id}`); }, '停用失敗')}>停用</button>}
            </div></td>
          </tr>); })}
          {!conns.length && <tr><td colSpan={7} className="sub" style={{ textAlign: 'center', padding: 24 }}>還沒有連線。先新增數據源（數據中台、BigQuery、神策歷史…）。</td></tr>}
        </tbody></table></div></div>
      {test && <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>連線測試 · {test.id}</h3><span className={`pill ${test.res.ok ? 'blue' : 'red'}`}>{test.res.ok ? '通過' : '失敗'}</span><button className="btn-ghost sm right" onClick={() => setTest(null)}>關閉</button></div>
        <table className="tbl"><tbody>{test.res.steps.map((s) => <tr key={s.step}><td style={{ width: 120 }}><b>{STEP_CN[s.step]}</b></td><td><span className={`pill ${s.ok ? (s.detail === '不適用' ? 'gray' : 'blue') : 'red'}`}>{s.ok ? (s.detail === '不適用' ? '不適用' : '通過') : '失敗'}</span></td><td className="mono sub">{s.detail}</td><td className="sub">{s.fix ?? ''}</td></tr>)}</tbody></table>
        <div className="note">API 節點只做 DNS 與 TCP；鑑權、權限、CDC 前置、讀樣本由執行節點以密鑰探測，結果會回寫到這裡。</div></div>}
      {priv && <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>最小權限自檢 · {priv.conn_id}</h3><span className="sub">{priv.verified_at ? `驗證於 ${priv.verified_at.slice(0, 16)}` : priv.note}</span><button className="btn-ghost sm right" onClick={() => setPriv(null)}>關閉</button></div>
        <table className="tbl"><tbody>{priv.items.map((it, i) => <tr key={i}><td className="mono" style={{ width: 80 }}>{it.scope}</td><td>{it.privilege}</td><td>{it.checked === null ? <span className="pill gray">未驗證</span> : it.checked ? <span className="pill blue">✓</span> : <span className="pill red">✗</span>}</td></tr>)}</tbody></table></div>}
      {rotateId && <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>輪替密鑰 · {rotateId}</h3><button className="btn-ghost sm right" onClick={() => setRotateId(null)}>取消</button></div>
        <div className="row"><input style={{ flex: 1 }} placeholder="新的 secret_ref（projects/…/versions/N、kv/data/dmp/…#key、secret://ns/name/key）" value={newRef} onChange={(e) => setNewRef(e.target.value)} />
          <button className="btn-primary" disabled={busy || !isSecretRef(newRef)} onClick={() => void run(async () => { await api.post(`/v1/integration/connections/${rotateId}/rotate`, { secret_ref: newRef }); setRotateId(null); }, '輪替失敗')}>輪替（新舊並存 24 小時）</button></div>
        {newRef && !isSecretRef(newRef) && <div className="note">不是合法的密鑰參照；請先把密鑰放進密鑰庫，這裡只填參照。</div>}</div>}
      {creating && <ConnectionForm connectors={connectors} busy={busy} onClose={() => setCreating(false)} onSave={(body) => run(async () => { await api.post('/v1/integration/connections', body); setCreating(false); }, '建立失敗')} />}
      <div className="grid c3" style={{ marginTop: 16 }}>
        {connectors.map((c) => <div key={c.type} className="card"><div className="card-h"><h3>{c.cn}</h3><span className="pill gray">{c.type}</span></div>
          <div className="note">鑑權（建議順序）：{c.auth.join(' › ')}<br />驅動：{c.driver.name ?? '—'}（{c.driver.license ?? '—'}）<br />能力：{Object.entries(c.capabilities).map(([k, v]) => `${k}=${v}`).join('、')}</div></div>)}
      </div>
    </>
  );
}

function ConnectionForm({ connectors, busy, onClose, onSave }: { connectors: Connector[]; busy: boolean; onClose: () => void; onSave: (b: ConnectionCreate) => Promise<void> }) {
  const [type, setType] = useState(connectors[0]?.type ?? '');
  const spec = connectors.find((c) => c.type === type);
  const [method, setMethod] = useState('');
  const m = spec?.auth_methods[method || spec.auth[0] || ''];
  const methodKey = method || spec?.auth[0] || '';
  const [id, setId] = useState('');
  const [owner, setOwner] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [refs, setRefs] = useState<Record<string, string>>({});
  const plain = Object.entries(fields).filter(([k, v]) => v && looksLikeSecret(k, v)).map(([k]) => k);
  const refBad = (m?.secret_fields ?? []).filter((k) => !isSecretRef(refs[k] ?? ''));
  const ok = /^[a-z][a-z0-9_]{2,31}$/.test(id) && !!m && (m.fields.every((f) => (fields[f] ?? '').trim())) && !refBad.length && !plain.length;
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="card-h"><h3>新增連線</h3><span className="sub">非機密欄位直接填；密鑰只填密鑰庫參照</span><button className="btn-ghost sm right" onClick={onClose}>取消</button></div>
      <div className="row"><label>連線 id<input aria-label="連線 id" placeholder="dwh_trade" value={id} onChange={(e) => setId(e.target.value)} /></label>
        <label>類型<select aria-label="類型" value={type} onChange={(e) => { setType(e.target.value); setMethod(''); setFields({}); setRefs({}); }}>{connectors.map((c) => <option key={c.type} value={c.type}>{c.cn}</option>)}</select></label>
        <label>鑑權<select aria-label="鑑權" value={methodKey} onChange={(e) => { setMethod(e.target.value); setRefs({}); }}>{(spec?.auth ?? []).map((a) => <option key={a} value={a}>{a}{spec?.auth_methods[a]?.rotation_days ? `（${spec.auth_methods[a]?.rotation_days} 天輪替）` : '（短效／免密鑰）'}</option>)}</select></label>
        <label>負責人<input aria-label="負責人" value={owner} onChange={(e) => setOwner(e.target.value)} /></label></div>
      {m && <div className="row">{m.fields.map((f) => <label key={f}>{f}<input aria-label={f} value={fields[f] ?? ''} onChange={(e) => setFields({ ...fields, [f]: e.target.value })} /></label>)}</div>}
      {m && m.secret_fields.length > 0 && <div className="row">{m.secret_fields.map((f) => <label key={f} style={{ flex: 1 }}>{f}（secret_ref）<input aria-label={`${f} secret_ref`} placeholder="kv/data/dmp/prod/<conn>#<key>" value={refs[f] ?? ''} onChange={(e) => setRefs({ ...refs, [f]: e.target.value })} /></label>)}</div>}
      {m?.notes && <div className="note">{m.notes}</div>}
      {plain.length > 0 && <div className="alert">欄位 {plain.join('、')} 疑似明文密鑰：請改放密鑰庫，只填參照。</div>}
      <div className="row"><button className="btn-primary" disabled={busy || !ok} onClick={() => void onSave({ id, type, auth_method: methodKey, fields: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, /^\d+$/.test(v) ? Number(v) : v])), secret_refs: refs, tls_verify: true, owner })}>建立連線</button>
        {!ok && <span className="sub">id 小寫英數底線 3–32 字；必填欄位與每個密鑰參照都要合法</span>}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ 數據表 */
function TablesTab({ conns, tables, busy, canUse, run }: { conns: Connection[]; tables: ExtTable[]; busy: boolean; canUse: boolean; run: (fn: () => Promise<void>, fb: string) => Promise<void> }) {
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [d, setD] = useState<{ connection: string; table: string; pk: string; inc: string; kind: 'csv' | 'jsonl'; path: string; fields: string }>({ connection: '', table: '', pk: '', inc: '', kind: 'csv', path: '', fields: '' });
  const body = (): TableCreate => ({ connection: d.connection, table: d.table.trim(), pk: parseList(d.pk), incremental_field: d.inc.trim() || null, ...(d.path.trim() ? { source: { kind: d.kind, path: d.path.trim() } } : { fields: parseList(d.fields).map((n) => ({ name: n })) }) });
  const ok = d.table.trim() && (d.path.trim() || parseList(d.fields).length);
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}><span className="note">登記外部表：設主鍵與增量欄位、探索欄位型別，個資欄位（姓名、電話、信箱、身分證…）自動標 L4，建模時不可映射。來源 Schema 變了按任務的策略處理。</span>{canUse && <button className="btn-primary right" onClick={() => setCreating(true)}>＋ 登記數據表</button>}</div>
      {creating && <div className="card" style={{ marginBottom: 12 }}><div className="card-h"><h3>登記數據表</h3><button className="btn-ghost sm right" onClick={() => setCreating(false)}>取消</button></div>
        <div className="row"><label>連線<select aria-label="連線" value={d.connection} onChange={(e) => setD({ ...d, connection: e.target.value })}><option value="">（檔案來源）</option>{conns.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}</select></label>
          <label>表<input aria-label="表" placeholder="dbo.fact_order_hist" value={d.table} onChange={(e) => setD({ ...d, table: e.target.value })} /></label>
          <label>主鍵<input aria-label="主鍵" placeholder="ORD_NO, STATUS" value={d.pk} onChange={(e) => setD({ ...d, pk: e.target.value })} /></label>
          <label>增量欄位<input aria-label="增量欄位" placeholder="UPDATED_AT" value={d.inc} onChange={(e) => setD({ ...d, inc: e.target.value })} /></label></div>
        <div className="row"><label>探索樣本檔<select aria-label="格式" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as 'csv' | 'jsonl' })}><option value="csv">csv</option><option value="jsonl">jsonl</option></select></label>
          <input aria-label="樣本路徑" style={{ flex: 1 }} placeholder="/stage/fact_order_hist.csv（執行節點可讀的路徑）" value={d.path} onChange={(e) => setD({ ...d, path: e.target.value })} />
          <span className="sub">或直接列欄位</span><input aria-label="欄位清單" style={{ flex: 1 }} placeholder="ORD_NO, STATUS, CUST_NAME…" value={d.fields} onChange={(e) => setD({ ...d, fields: e.target.value })} /></div>
        <div className="row"><button className="btn-primary" disabled={busy || !ok} onClick={() => void run(async () => { await api.post('/v1/integration/tables', body()); setCreating(false); }, '登記失敗')}>登記並探索</button></div></div>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>數據源 / 表</th><th>主鍵</th><th>增量欄位</th><th className="num">欄位</th><th className="num">L4</th><th className="num">Schema 版本</th><th>變更</th><th>使用它的任務</th><th></th></tr></thead>
        <tbody>{tables.map((t) => { const l4 = t.fields.filter((f) => f.sensitivity === 'L4').length; const last = t.changes[0]; return (
          <tr key={t.id}><td><b className="mono">{t.table}</b><span className="sub mono">{t.connection || '檔案'} · {t.id}</span></td><td className="mono">{t.pk.join(', ') || '—'}</td><td className="mono">{t.incremental_field ?? '—'}</td>
            <td className="num mono">{t.fields.length}</td><td className="num">{l4 ? <span className="pill red">{l4}</span> : '0'}</td><td className="num mono">v{t.schema_version}</td>
            <td>{last ? <span className="pill amber">{last.items.length} 項 · {last.at.slice(0, 10)}</span> : <span className="pill gray">無</span>}</td>
            <td className="mono sub">{t.used_by.join(', ') || '—'}</td>
            <td><div className="row" style={{ marginTop: 0, flexWrap: 'nowrap' }}><button className="btn-ghost sm" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? '收合' : '欄位'}</button>
              {canUse && t.source?.path && <button className="btn-ghost sm" disabled={busy} onClick={() => void run(async () => { await api.post(`/v1/integration/tables/${t.id}/rescan`); }, '重新探索失敗')}>重新探索</button>}</div></td></tr>); })}
          {!tables.length && <tr><td colSpan={9} className="sub" style={{ textAlign: 'center', padding: 24 }}>還沒有登記的數據表</td></tr>}
        </tbody></table></div></div>
      {open && (() => { const t = tables.find((x) => x.id === open); return t ? <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>{t.table} 的欄位</h3><span className="sub">L4 不可映射、不回顯樣本</span></div>
        <table className="tbl"><thead><tr><th>欄位</th><th>型別</th><th>敏感等級</th><th className="num">空值率</th><th>樣本</th></tr></thead><tbody>{t.fields.map((f) => <tr key={f.name}><td className="mono">{f.name}</td><td className="mono">{f.type}</td><td><span className={`pill ${SENS_PILL[f.sensitivity]}`}>{f.sensitivity}</span></td><td className="num mono">{f.null_rate != null ? `${Math.round(f.null_rate * 100)}%` : '—'}</td><td className="mono sub">{f.sensitivity === 'L4' ? '（不回顯）' : f.sample ?? ''}</td></tr>)}</tbody></table>
        {t.changes.length > 0 && <div className="note">最近變更：{t.changes[0]?.items.map((c) => `${c.kind} ${c.field}${c.from ? ` ${c.from}→${c.to}` : ''}`).join('、')}</div>}</div> : null; })()}
    </>
  );
}

/* ------------------------------------------------------------------ 同步任務 */
function TasksTab({ tasks, busy, canUse, run, onNew }: { tasks: SyncTask[]; busy: boolean; canUse: boolean; run: (fn: () => Promise<void>, fb: string) => Promise<void>; onNew: () => void }) {
  const [runs, setRuns] = useState<{ id: string; rows: SyncRun[] } | null>(null);
  const [pv, setPv] = useState<{ id: string; res: Preview } | null>(null);
  const [last, setLast] = useState<RunNow | null>(null);
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}><span className="note">目標類型決定寫入：事件表只增量（event_id 去重）、用戶表 upsert、維度表 upsert 或全量；依賴的上游同窗口成功才跑；失敗按退避重試；Schema 變更依策略自動映射／忽略／暫停並通知。</span>{canUse && <button className="btn-primary right" onClick={onNew}>＋ 建立任務</button>}</div>
      {last && <div className={`card`} style={{ marginBottom: 12 }}><div className="card-h"><h3>立即執行 · {last.cn}</h3><span className={`pill ${runPill(last.status)}`}>{RUN_STATUS_CN[last.status] ?? last.status}</span><button className="btn-ghost sm right" onClick={() => setLast(null)}>關閉</button></div>
        <div className="tiles">{([['讀入', last.rows_in], ['通過', last.rows_ok], ['隔離', last.rows_quarantined], ['拒絕', last.rows_rejected], ['載入', last.rows_loaded]] as [string, number | null][]).map(([k, v]) => <div key={k} className="tile"><span className="sub">{k}</span><b>{(v ?? 0).toLocaleString()}</b></div>)}</div>{last.error && <div className="alert" style={{ marginTop: 8 }}>{last.error}</div>}</div>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>任務</th><th>來源</th><th>目標</th><th style={{ whiteSpace: 'nowrap' }}>同步方式</th><th>排程 / 依賴</th><th>最近執行</th><th>狀態</th><th>動作</th></tr></thead>
        <tbody>{tasks.map((t) => (
          <tr key={t.id} className={t.status === 'error' ? 'warnrow' : ''}>
            <td><b>{t.cn}</b><span className="sub mono">{t.id} · {t.model_path.split('/').slice(-1)[0]}</span></td>
            <td className="mono">{t.source.table ?? t.source.path?.split('/').slice(-1)[0] ?? '—'}<span className="sub">{t.source.connection ?? '檔案'}{t.source.incremental_field ? ` · 增量 ${t.source.incremental_field}` : ''}</span></td>
            <td><span className="pill">{t.target.type}</span><span className="sub mono">{t.target.table} · {t.target.write}{t.events.length ? ` · ${t.events.join('／')}` : ''}</span></td>
            <td>{{ full: '全量', incremental: '增量', stream: '流' }[t.mode]}</td>
            <td className="mono">{t.schedule}<span className="sub">{t.depends_on.length ? `依賴 ${t.depends_on.join(', ')}` : '無依賴'} · {t.schema_change}</span></td>
            <td className="sub">{runText(t.last)}{t.watermark ? <span className="sub mono">水位 {t.watermark}</span> : null}</td>
            <td><span className={`pill ${TASK_STATUS[t.status].pill}`}>{TASK_STATUS[t.status].cn}</span>{t.paused_reason && <span className="sub">{t.paused_reason}</span>}</td>
            <td><div className="row" style={{ marginTop: 0, flexWrap: 'nowrap' }}>
              {canUse && <button className="btn-ghost sm" disabled={busy} onClick={() => void run(async () => { setPv({ id: t.id, res: await api.post<Preview>(`/v1/integration/sync-tasks/${t.id}/preview`, { limit: 1000 }) }); }, '預覽失敗')}>預覽</button>}
              {canUse && <button className="btn-primary" style={{ padding: '4px 10px' }} disabled={busy} onClick={() => void run(async () => { setLast(await api.post<RunNow>(`/v1/integration/sync-tasks/${t.id}/run`)); }, '執行失敗')}>立即執行</button>}
              <button className="btn-ghost sm" disabled={busy} onClick={() => void run(async () => { setRuns({ id: t.id, rows: await api.get<SyncRun[]>(`/v1/integration/sync-tasks/${t.id}/runs?limit=20`) }); }, '讀取失敗')}>紀錄</button>
            </div></td>
          </tr>))}
          {!tasks.length && <tr><td colSpan={8} className="sub" style={{ textAlign: 'center', padding: 24 }}>還沒有同步任務，從「建立任務」開始。</td></tr>}
        </tbody></table></div></div>
      {pv && <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>預覽驗證 · {pv.id}</h3><span className="sub">以樣本跑映射與清洗，不寫入</span><button className="btn-ghost sm right" onClick={() => setPv(null)}>關閉</button></div>
        <div className="tiles">{([['樣本', pv.res.summary.in], ['通過', pv.res.summary.ok], ['隔離', pv.res.summary.quarantined], ['拒絕', pv.res.summary.rejected], ['個資命中', pv.res.pii_hits]] as [string, number][]).map(([k, v]) => <div key={k} className="tile"><span className="sub">{k}</span><b>{v.toLocaleString()}</b></div>)}</div>
        {pv.res.new_fields.length > 0 && <div className="note">來源新欄位（未映射）：{pv.res.new_fields.join('、')}</div>}
        {pv.res.quarantined.length > 0 && <table className="tbl" style={{ marginTop: 8 }}><thead><tr><th>隔離原因</th><th>列（摘要）</th></tr></thead><tbody>{pv.res.quarantined.slice(0, 10).map((q, i) => <tr key={i}><td>{q.reason}</td><td className="mono sub">{Object.entries(q.row).slice(0, 6).map(([k, v]) => `${k}=${String(v)}`).join(' ')}</td></tr>)}</tbody></table>}
        {pv.res.sample_ok.length > 0 && <div className="note">通過樣本：{pv.res.sample_ok.slice(0, 2).map((r) => JSON.stringify(r)).join('  ')}</div>}</div>}
      {runs && <div className="card" style={{ marginTop: 12 }}><div className="card-h"><h3>執行紀錄 · {runs.id}</h3><button className="btn-ghost sm right" onClick={() => setRuns(null)}>關閉</button></div>
        <table className="tbl"><thead><tr><th>窗口</th><th>觸發</th><th>狀態</th><th className="num">讀入</th><th className="num">通過</th><th className="num">隔離</th><th className="num">拒絕</th><th className="num">載入</th><th>水位</th><th>錯誤 / 日誌</th></tr></thead>
          <tbody>{runs.rows.map((r) => <tr key={r.id}><td className="mono">{r.scheduled_for.slice(0, 16).replace('T', ' ')}<span className="sub">第 {r.attempt} 次</span></td><td>{r.trigger}</td><td><span className={`pill ${runPill(r.status)}`}>{RUN_STATUS_CN[r.status] ?? r.status}</span></td>
            <td className="num mono">{r.rows_in ?? '—'}</td><td className="num mono">{r.rows_ok ?? '—'}</td><td className="num mono">{r.rows_quarantined ?? '—'}</td><td className="num mono">{r.rows_rejected ?? '—'}</td><td className="num mono">{r.rows_loaded ?? '—'}</td>
            <td className="mono sub">{r.watermark_from ?? '—'} → {r.watermark_to ?? '—'}</td><td className="sub">{r.error ?? r.log.slice(-2).join(' / ')}</td></tr>)}
            {!runs.rows.length && <tr><td colSpan={10} className="sub" style={{ textAlign: 'center', padding: 16 }}>沒有執行紀錄</td></tr>}</tbody></table></div>}
    </>
  );
}

/* ------------------------------------------------------------------ 建立任務精靈 */
function Wizard({ tables, tasks, canUse, onDone }: { tables: ExtTable[]; tasks: SyncTask[]; canUse: boolean; onDone: () => Promise<void> }) {
  const [step, setStep] = useState(1);
  const [d, setD] = useState({ id: '', cn: '', table_id: tables[0]?.id ?? '', model_path: 'backend/integration/models.example.yaml', mode: 'incremental' as 'full' | 'incremental', schedKind: 'interval' as 'interval' | 'cron', sched: '15', depends: '', schema_change: 'pause_and_notify' as SyncTaskCreate['schema_change'], retries: 3 });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<SyncTask | null>(null);
  const [pv, setPv] = useState<Preview | null>(null);
  const table = tables.find((t) => t.id === d.table_id);
  const sched = scheduleBody(d.schedKind, d.sched);
  const idOk = /^[a-z][a-z0-9_]{2,47}$/.test(d.id) && !tasks.some((t) => t.id === d.id);
  const steps = ['來源數據表', '模型（建模產物）', '排程與策略', '建立與預覽'];
  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const body: SyncTaskCreate = { id: d.id, cn: d.cn || d.id, model_path: d.model_path.trim(), source: { table_id: d.table_id }, schedule: sched ?? { interval_min: 15 }, mode: d.mode, depends_on: parseList(d.depends), schema_change: d.schema_change, retries: d.retries };
      const t = await api.post<SyncTask>('/v1/integration/sync-tasks', body); setCreated(t);
      setPv(await api.post<Preview>(`/v1/integration/sync-tasks/${t.id}/preview`, { limit: 1000 }));
    } catch (e) { setErr(errText(e, '建立失敗')); } finally { setBusy(false); }
  };
  if (!canUse) return <div className="card"><div className="note">你的角色只能查看數據整合，建立任務需要 integration.use（資訊管理）。</div></div>;
  return (
    <div className="card">
      <div className="card-h"><h3>建立同步任務</h3><span className="sub">數據表 → 模型 → 排程 → 建立並預覽 → 立即執行</span></div>
      <div className="seg" style={{ marginBottom: 12 }}>{steps.map((s, i) => <a key={s} href="#" className={step === i + 1 ? 'on' : ''} onClick={(e) => { e.preventDefault(); if (!created) setStep(i + 1); }}>{i + 1}. {s}</a>)}</div>
      {err && <div className="alert" style={{ marginBottom: 12 }}>{err}</div>}
      {step === 1 && <div className="stack">
        <div className="row"><label>任務 id<input aria-label="任務 id" placeholder="sync_orders" value={d.id} onChange={(e) => setD({ ...d, id: e.target.value })} /></label><label>名稱<input aria-label="任務名稱" placeholder="委託事實表 → 委託／成交事件" value={d.cn} onChange={(e) => setD({ ...d, cn: e.target.value })} /></label>
          <label>來源數據表<select aria-label="來源數據表" value={d.table_id} onChange={(e) => setD({ ...d, table_id: e.target.value })}>{tables.map((t) => <option key={t.id} value={t.id}>{t.table}（{t.connection || '檔案'}）</option>)}</select></label></div>
        {table ? <div className="note">主鍵 {table.pk.join(', ') || '—'} · 增量欄位 {table.incremental_field ?? '—'} · {table.fields.length} 欄，其中 L4 {table.fields.filter((f) => f.sensitivity === 'L4').length} 欄不可映射</div> : <div className="note">先到「數據表」登記來源表。</div>}
        {d.id && !idOk && <div className="note">id 小寫英數底線 3–48 字且不可重複。</div>}</div>}
      {step === 2 && <div className="stack">
        <div className="row"><label style={{ flex: 1 }}>模型檔（建模精靈產物，yaml）<input aria-label="模型檔" value={d.model_path} onChange={(e) => setD({ ...d, model_path: e.target.value })} /></label></div>
        <div className="note">模型定義事件化（狀態機 → 事件名）、時間欄位、identities 代碼化（token_v1／fpe）、欄位映射與轉換、drop_fields（個資）、清洗規則（必填、枚舉、範圍、時間窗、個資掃描）、去重鍵。範例：<span className="mono">backend/integration/models.example.yaml</span>（委託事實表 → bond_order／bond_deal／order_cancelled／order_rejected）、<span className="mono">models.instrument.yaml</span>（商品主檔 → dim_instrument）。建立時會對語意層驗證：事件名要在字典裡、L4 不可映射、身分必須代碼化。</div></div>}
      {step === 3 && <div className="stack">
        <div className="row"><label>同步方式<select aria-label="同步方式" value={d.mode} onChange={(e) => setD({ ...d, mode: e.target.value as 'full' | 'incremental' })}><option value="incremental">增量（水位 = 增量欄位最大值）</option><option value="full">全量</option></select></label>
          <label>排程<select aria-label="排程類型" value={d.schedKind} onChange={(e) => setD({ ...d, schedKind: e.target.value as 'interval' | 'cron', sched: e.target.value === 'cron' ? '*/15 * * * *' : '15' })}><option value="interval">每 N 分鐘</option><option value="cron">cron</option></select></label>
          <input aria-label="排程值" style={{ width: 140 }} value={d.sched} onChange={(e) => setD({ ...d, sched: e.target.value })} />
          <label>依賴任務<input aria-label="依賴任務" placeholder="sync_instrument" value={d.depends} onChange={(e) => setD({ ...d, depends: e.target.value })} /></label></div>
        <div className="row"><label>Schema 變更<select aria-label="Schema 變更" value={d.schema_change} onChange={(e) => setD({ ...d, schema_change: e.target.value as SyncTaskCreate['schema_change'] })}><option value="pause_and_notify">暫停並通知</option><option value="auto_map">自動映射（記錄後照跑）</option><option value="ignore">忽略</option></select></label>
          <label>重試次數<input aria-label="重試次數" type="number" min={0} max={10} value={d.retries} onChange={(e) => setD({ ...d, retries: Number(e.target.value) })} /></label></div>
        {!sched && <div className="note">排程值不合法：每 N 分鐘要正整數；cron 要 5 欄。</div>}
        <div className="note">第一階段沒有流任務：即時需求用每 15 分鐘微批。上游（依賴）同一窗口成功才會跑；失敗按退避重試，用完次數告警。</div></div>}
      {step === 4 && <div className="stack">
        <dl className="kv"><dt>任務</dt><dd className="mono">{d.id} · {d.cn || d.id}</dd><dt>來源</dt><dd className="mono">{table?.table ?? '—'}{table?.incremental_field ? ` · 增量 ${table.incremental_field}` : ''}</dd><dt>模型</dt><dd className="mono">{d.model_path}</dd><dt>排程</dt><dd className="mono">{d.mode} · {d.schedKind === 'cron' ? d.sched : `every ${d.sched}m`}{d.depends ? ` · 依賴 ${d.depends}` : ''} · {d.schema_change} · 重試 {d.retries}</dd></dl>
        {!created ? <div className="row"><button className="btn-primary" disabled={busy || !idOk || !table || !sched || !d.model_path.trim()} onClick={() => void create()}>建立任務並預覽</button></div>
          : <><div className="row"><span className={`pill ${TASK_STATUS[created.status].pill}`}>已建立 · {TASK_STATUS[created.status].cn}</span><span className="sub">目標 {created.target.type} {created.target.table} · 事件 {created.events.join('／') || '—'}</span></div>
            {pv && <div className="tiles">{([['樣本', pv.summary.in], ['通過', pv.summary.ok], ['隔離', pv.summary.quarantined], ['拒絕', pv.summary.rejected], ['個資命中', pv.pii_hits]] as [string, number][]).map(([k, v]) => <div key={k} className="tile"><span className="sub">{k}</span><b>{v.toLocaleString()}</b></div>)}</div>}
            {pv && pv.quarantined.length > 0 && <div className="note">隔離原因（前 5）：{pv.quarantined.slice(0, 5).map((q) => q.reason).join('；')}</div>}
            <div className="row"><button className="btn-primary" disabled={busy} onClick={() => void onDone()}>到同步任務列表（可立即執行）</button></div></>}
      </div>}
      {!created && <div className="row" style={{ marginTop: 12 }}><button className="btn-ghost" disabled={step === 1} onClick={() => setStep(step - 1)}>上一步</button><button className="btn-primary" disabled={step === 4 || (step === 1 && (!idOk || !table)) || (step === 3 && !sched)} onClick={() => setStep(step + 1)}>下一步</button></div>}
    </div>
  );
}
