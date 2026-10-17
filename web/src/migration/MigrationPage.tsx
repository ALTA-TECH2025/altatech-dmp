// 神策遷移（docs/22）：盤點（計畫 + 神策查詢 API 盤點 + 任務進度）、埋點銜接（bridge.py）、導出與載入（任務、分片、重跑）、新舊對帳（T2／T5／T6）、切換時序。
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { LineChart } from '@/analytics/LineChart';
import { fmtNum } from '@/analytics/series';
import type { Series } from '@/analytics/series';
import { pct } from '@/governance/governance';
import { daysBetween, diffText, INV_STATUS, JOB_STATUS, KIND_PILL, timelineStatus } from './migration';
import type { Bridge, ExportJob, Exports, ExportSlice, Inventory, Reconcile } from './types';

type Tab = 'inventory' | 'bridge' | 'exports' | 'recon' | 'timeline';

export function MigrationPage() {
  return <RequireGrant module="governance"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const canUse = can('governance', 'use');
  const [tab, setTab] = useState<Tab>('inventory');
  const [inv, setInv] = useState<Inventory | null>(null); const [br, setBr] = useState<Bridge | null>(null); const [ex, setEx] = useState<Exports | null>(null); const [rc, setRc] = useState<Reconcile | null>(null);
  const [err, setErr] = useState<string | null>(null); const [rcErr, setRcErr] = useState<string | null>(null);
  const [project, setProject] = useState('migration_demo'); const [days, setDays] = useState(28);
  const reload = async () => { try { const [a, b, c] = await Promise.all([api.get<Inventory>('/v1/migration/inventory'), api.get<Bridge>('/v1/migration/bridge'), api.get<Exports>('/v1/migration/exports')]); setInv(a); setBr(b); setEx(c); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  const loadRecon = async () => { try { setRc(await api.get<Reconcile>(`/v1/migration/reconcile?days=${days}${project ? `&project=${encodeURIComponent(project)}` : ''}`)); setRcErr(null); } catch (e) { setRc(null); setRcErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => { void reload(); void loadRecon(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const tl = inv ? timelineStatus(inv.timeline, new Date().toISOString().slice(0, 10)) : [];
  const loaded = inv ? inv.items.filter((i) => i.id === 'events').map((i) => i.rows_loaded ?? 0)[0] ?? 0 : 0;
  const tiles = [
    ['歷史事件已載入', inv ? fmtNum(loaded) : '—', inv?.items.find((i) => i.id === 'events')?.rows_sensors ? `神策 ${fmtNum(inv.items.find((i) => i.id === 'events')!.rows_sensors!)}` : inv?.sensors_configured ? '尚未盤點' : '未設定神策連線'],
    ['事件銜接', br ? `${br.via_ga} / ${br.total}` : '—', br ? `經 GA 埋點；${br.counts.not_ga ?? 0} 個直接接入、${br.counts.history_only ?? 0} 個僅歷史` : ''],
    ['並行期量差（中位）', rc?.t5.median_abs_diff != null ? pct(rc.t5.median_abs_diff) : '—', rc ? `${rc.t5.events_over} 個事件 > ${pct(rc.thresholds.t5_parallel ?? 0.05, 0)} 待處理` : rcErr ?? ''],
    ['登入用戶銜接率', rc?.t6.rate != null ? pct(rc.t6.rate) : '—', rc ? `${fmtNum(rc.t6.both)} 人兩邊同一客戶代碼（目標 ≥ ${pct(rc.thresholds.t6_user_bridge ?? 0.95, 0)}）` : ''],
  ];
  return (
    <div className="page">
      <div className="page-h"><div><h1>神策遷移</h1><p>神策 → GA360：依事件字典產生 GA 埋點規格、神策歷史資料盤點／導出／載入、新舊資料對帳與切換時序。盤點筆數與對帳數字來自神策查詢 API 與 ClickHouse；沒有連線的部分會標示待連線，不用模擬數字。</p></div>
        <div className="row" style={{ marginTop: 0 }}><span className={`pill ${inv?.sensors_configured ? 'blue' : 'amber'}`}>{inv?.sensors_configured ? '神策連線已設定' : '神策連線未設定（SA_*）'}</span><a className="btn-ghost" href="https://github.com/ALTA-TECH2025/altatech-dmp/blob/main/docs/22-sensors-to-ga360-migration.md" target="_blank" rel="noreferrer">文件 docs/22</a></div></div>
      {err && <div className="alert">{err}</div>}
      <div className="tiles" style={{ marginBottom: 16 }}>{tiles.map(([a, b, c]) => <div className="tile" key={a}><span className="sub">{a}</span><b>{b}</b><span className="sub">{c}</span></div>)}</div>
      <div className="seg">{([['inventory', '盤點'], ['bridge', `埋點銜接 ${br?.total ?? ''}`], ['exports', `導出與載入 ${ex?.items.length ?? ''}`], ['recon', '新舊對帳'], ['timeline', '切換時序']] as [Tab, string][]).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
      {tab === 'inventory' && inv && <InventoryTab inv={inv} canUse={canUse} onChange={reload} />}
      {tab === 'bridge' && br && <BridgeTab br={br} />}
      {tab === 'exports' && ex && inv && <ExportsTab ex={ex} inv={inv} canUse={canUse} onChange={reload} />}
      {tab === 'recon' && <ReconTab rc={rc} err={rcErr} project={project} setProject={setProject} days={days} setDays={setDays} onLoad={loadRecon} />}
      {tab === 'timeline' && inv && (
        <>
          <div className="card"><div className="card-h"><h3>切換時序</h3><span className="sub">T0 = 神策停止寫入；UAT 2027-06-30、PD 2027-09-30 為客戶定的硬日期</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>里程碑</th><th>日期</th><th>內容</th><th>狀態</th></tr></thead><tbody>{inv.timeline.map((m, i) => <tr key={m.id} className={tl[i] === 'now' ? 'warnrow' : ''}><td><b>{m.id}</b></td><td className="mono">{m.date}</td><td>{m.cn}</td><td><span className={`pill ${tl[i] === 'done' ? 'blue' : tl[i] === 'now' ? 'amber' : 'gray'}`}>{tl[i] === 'done' ? '完成' : tl[i] === 'now' ? '進行中' : '待辦'}</span></td></tr>)}</tbody></table></div></div>
          <div className="grid c2" style={{ marginTop: 16 }}>
            <div className="card"><div className="card-h"><h3>分工</h3></div><div className="tbl-wrap"><table className="tbl"><tbody>{[['AltaTech', '橋接表、導出與載入工具、對帳、整合測試、系統'], ['GA360 導入商', '依橋接表埋點、GTM／Firebase 設定、BigQuery 匯出開通、DebugView 核對'], ['客戶', '神策存取權、數據中台交易測試資料、新 MA 工具決策、驗收']].map((r) => <tr key={r[0]}><td style={{ fontWeight: 600 }}>{r[0]}</td><td>{r[1]}</td></tr>)}</tbody></table></div></div>
            <div className="card"><div className="card-h"><h3>風險</h3></div><ul className="priv"><li>客戶無法開放神策底層（Impala／HDFS）→ 退而用查詢 API 按日導出，70 億約需更久；先確認權限。</li><li>GA360 埋點品質：並行期至少 3 個月，差異逐事件收斂；T5 門檻 5%。</li><li>口徑差異（會話、停留、匿名 ID）已文件化，分析按時期分開看。</li><li>T0 後 4 週內最終增量與對帳；神策 2027-10-31 到期，之後無退路。</li></ul></div>
          </div>
        </>
      )}
    </div>
  );
}

function InventoryTab({ inv, canUse, onChange }: { inv: Inventory; canUse: boolean; onChange: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const refresh = async () => { setBusy(true); setErr(null); try { await api.post('/v1/migration/inventory/refresh', {}); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } finally { setBusy(false); } };
  return (
    <>
      {err && <div className="alert">{err}</div>}
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><span className="note" style={{ marginTop: 0 }}>{inv.inventoried_at ? `最近盤點 ${inv.inventoried_at.slice(0, 16).replace('T', ' ')} · 神策用戶 ${inv.users_total?.toLocaleString() ?? '—'}` : '尚未以神策查詢 API 盤點'}</span>{canUse && <button className="btn-ghost right" disabled={busy || !inv.sensors_configured} title={inv.sensors_configured ? '' : '執行節點需設定 SA_BASE_URL／SA_PROJECT／SA_API_SECRET'} onClick={() => void refresh()}>{busy ? '盤點中…' : '以神策 API 重新盤點'}</button>}</div>
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>資料集</th><th>神策位置</th><th>範圍</th><th className="num">神策筆數</th><th>導出方式</th><th>目標</th><th>進度</th><th>狀態</th></tr></thead>
        <tbody>{inv.items.map((r) => <tr key={r.id}><td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{r.cn}<span className="sub">{r.note ?? r.rule}</span></td><td className="mono" style={{ fontSize: 11.5 }}>{r.location}</td><td style={{ fontSize: 12 }}>{r.range ?? '—'}</td><td className="num mono">{r.rows_sensors === null ? '—' : r.rows_sensors.toLocaleString()}</td><td style={{ fontSize: 12 }}>{r.method}</td><td style={{ fontSize: 12 }}>{r.target}</td><td>{r.progress === null ? '—' : <span className="mono">{r.progress}%{r.rows_loaded ? ` · 載入 ${fmtNum(r.rows_loaded)}` : ''}</span>}</td><td><span className={`pill ${INV_STATUS[r.status].pill}`}>{INV_STATUS[r.status].cn}</span></td></tr>)}</tbody></table></div></div>
      <div className="grid c3" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>載入順序</h3></div><ul className="priv"><li>先 ID 映射與用戶表，再事件：事件載入時客戶代碼已存在。</li><li>事件名保留神策原名；時間轉 UTC、日分區按 Asia/Taipei；$ip 丟棄。</li><li>$identity_login_id 以同一把金鑰 HMAC 成 v1_ 客戶代碼，與 GA user_id 同款。</li><li>IDM 2.0：second_id → 客戶代碼；first_id 保留為匿名用戶，不與 GA client_id 銜接。</li></ul></div>
        <div className="card"><div className="card-h"><h3>導出方式</h3></div><ul className="priv"><li>Impala JDBC／HDFS Parquet 直讀（採用；客戶內網執行，進度回寫）。</li><li>查詢 API /api/sql/query：按日切片，抽樣校驗與小量資料集；本頁「導出與載入」用這條。</li><li>神策資料格式檔（FormatImporter JSONL）：sensors_format 代碼化後載入。</li><li>分片：{inv.export_rules.slice}；校驗：{inv.export_rules.checks.join('、')}；差異 &gt; {pct(inv.export_rules.rerun_threshold, 1)} 自動重跑，{inv.export_rules.max_auto_rerun} 次仍差待人工。</li></ul></div>
        <div className="card"><div className="card-h"><h3>盤點來源</h3></div><p className="note">計畫欄位來自 migration/inventory.yaml（docs/22 §3.1）；筆數、範圍由「以神策 API 重新盤點」寫入（每事件每日筆數、用戶數）；進度由導出任務回寫。元數據盤點已完成：事件 117、屬性 681、用戶屬性 3,606（docs/28、29）。</p></div>
      </div>
    </>
  );
}

function BridgeTab({ br }: { br: Bridge }) {
  const [kind, setKind] = useState('all'); const [q, setQ] = useState('');
  const rows = br.items.filter((r) => (kind === 'all' || r.kind === kind) && (!q || r.sensors_event.toLowerCase().includes(q.toLowerCase()) || r.ga4_event.includes(q.toLowerCase()) || r.cn.includes(q)));
  const download = () => { const head = ['sensors_event', 'cn', 'group', 'source', 'platforms', 'ga4_event', 'kind', 'params', 'note', 'aliases']; const esc = (v: string) => `"${String(v ?? '').replace(/"/g, '""')}"`; const csv = [head.join(','), ...br.items.map((r) => head.map((h) => esc(String((r as unknown as Record<string, unknown>)[h] ?? ''))).join(','))].join('\n'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' })); a.download = 'ga360_tracking_spec.csv'; a.click(); };
  return (
    <>
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}>
        <span className="seg" style={{ margin: 0 }}>{[['all', `全部 ${br.total}`], ['auto', `GA 自動 ${br.counts.auto ?? 0}`], ['recommended', `建議事件 ${br.counts.recommended ?? 0}`], ['custom', `自訂 ${br.counts.custom ?? 0}`], ['not_ga', `不經 GA ${br.counts.not_ga ?? 0}`], ['history_only', `僅歷史 ${br.counts.history_only ?? 0}`]].map(([k, cn]) => <a key={k} href="#" className={kind === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setKind(k); }}>{cn}</a>)}</span>
        <input placeholder="搜尋神策事件／GA4 事件名" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} /><button className="btn-ghost right" onClick={download}>下載 ga360_tracking_spec.csv</button>
      </div>
      {br.issues.length > 0 && <div className="note" style={{ marginBottom: 10 }}>未解議題 {br.issues.length}：{br.issues.slice(0, 3).map((i) => `${i.event}：${i.issue}`).join('；')}{br.issues.length > 3 && ' …'}</div>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>神策事件（正式事件名）</th><th>中文</th><th>分組</th><th>GA4 事件名</th><th>類型</th><th>參數</th><th>平台</th><th>備註</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.sensors_event} className={r.issues.length ? 'warnrow' : ''}><td className="mono" style={{ fontWeight: 600 }}>{r.sensors_event}{r.aliases && <span className="sub">別名 {r.aliases}</span>}</td><td>{r.cn}</td><td style={{ fontSize: 12 }}>{r.group}</td><td className="mono" style={{ color: r.kind === 'not_ga' || r.kind === 'history_only' ? 'var(--muted)' : 'inherit' }}>{r.kind === 'not_ga' || r.kind === 'history_only' ? '—' : r.ga4_event}</td><td><span className={`pill ${KIND_PILL[r.kind] ?? ''}`}>{r.kind_cn}</span></td><td className="mono" style={{ fontSize: 10.5, maxWidth: 300, overflowWrap: 'anywhere' }}>{r.params}</td><td className="mono" style={{ fontSize: 10.5, color: 'var(--muted)' }}>{r.platforms.replace(/JavaScript/g, 'JS').replace(/服務端/g, 'Server')}</td><td style={{ fontSize: 12 }}>{r.issues.length ? <span style={{ color: 'var(--red)' }}>{r.issues.join('；')}</span> : r.note}</td></tr>)}</tbody></table></div></div>
      <div className="grid c2" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>轉換規則</h3></div><ul className="priv"><li>正式事件名 = 神策事件名，歷史資料不改名；GA 上報轉換後的名字，接入時以橋接表轉回。</li><li>去 $、CamelCase → snake_case、小寫、只留 a–z 0–9 _、不以數字開頭；避開 firebase_／google_／ga_ 前綴與 GA4 保留事件名（撞到加 sa_）。</li><li>神策預置屬性對 GA 內建欄位（$page_url→page_location、$screen_name→firebase_screen、$utm_*→流量來源）；$ip 不送。</li><li>限制：事件名 {br.limits.event_name_len}、參數名 {br.limits.param_name_len}、每事件參數 {br.limits.params_per_event}、user property {br.limits.user_properties_per_project} 個。</li></ul></div>
        <div className="card"><div className="card-h"><h3>用戶屬性 → GA user property</h3></div><div className="tbl-wrap"><table className="tbl"><thead><tr><th>屬性</th><th>敏感</th><th>送 GA</th><th>備註</th></tr></thead><tbody>{br.user_properties.map((u) => <tr key={u.key}><td className="mono">{u.key}<span className="sub">{u.cn}</span></td><td>{u.sensitivity}</td><td>{u.to_ga === 'user_property' ? '✓' : '—'}</td><td style={{ fontSize: 12 }}>{u.note}</td></tr>)}</tbody></table></div></div>
      </div>
    </>
  );
}

function ExportsTab({ ex, inv, canUse, onChange }: { ex: Exports; inv: Inventory; canUse: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [sel, setSel] = useState<(ExportJob & { slices: ExportSlice[] }) | null>(null);
  const [f, setF] = useState({ dataset: 'events', date_from: '2026-10-01', date_to: '2026-10-03', method: 'api' as 'api' | 'impala', events: '', note: '' });
  const n = f.dataset === 'events' || f.dataset === 'ma' ? Math.max(0, daysBetween(f.date_from, f.date_to)) : 1;
  const create = async (e: FormEvent) => { e.preventDefault(); setBusy(true); setErr(null); try { await api.post('/v1/migration/exports', { ...f, events: f.events ? f.events.split(/[,，\s]+/).filter(Boolean) : [] }); setOpen(false); await onChange(); } catch (ex2) { setErr(ex2 instanceof ApiError ? ex2.message : '失敗'); } finally { setBusy(false); } };
  const rerun = async (id: string, force = false) => { setBusy(true); setErr(null); try { await api.post(`/v1/migration/exports/${id}/rerun`, { force }); await onChange(); if (sel?.id === id) await view(id); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } finally { setBusy(false); } };
  const view = async (id: string) => { try { setSel(await api.get<ExportJob & { slices: ExportSlice[] }>(`/v1/migration/exports/${id}`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  return (
    <>
      {err && <div className="alert">{err}</div>}
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><span className="note" style={{ marginTop: 0 }}>{ex.sensors_configured ? '神策連線已設定：任務會真的導出 → 代碼化／去個資 → 載入 → 對帳' : '未設定神策連線：任務會建立但停在「待連線」，不會假裝成功'}{!ex.clickhouse && ' · 未連接 ClickHouse：只導出不載入'}</span>{canUse && <button className="btn-ghost right" onClick={() => setOpen((v) => !v)}>＋ 建立導出任務</button>}</div>
      {open && <form className="card stack" style={{ marginBottom: 12 }} onSubmit={create}><div className="grid c3">
        <label>資料集<select value={f.dataset} onChange={(e) => setF({ ...f, dataset: e.target.value })}>{inv.items.filter((i) => !i.done).map((i) => <option key={i.id} value={i.id}>{i.cn}</option>)}</select></label>
        <label>方式<select value={f.method} onChange={(e) => setF({ ...f, method: e.target.value as 'api' | 'impala' })}><option value="api">查詢 API（按日切片）</option><option value="impala">Impala／HDFS 直讀（客戶內網執行，只追蹤）</option></select></label>
        <label>事件（選填，逗號分隔；空 = 全部）<input className="mono" value={f.events} onChange={(e) => setF({ ...f, events: e.target.value })} /></label>
        <label>開始<input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></label><label>結束<input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></label><label>備註<input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label></div>
        <div className="row" style={{ marginTop: 0 }}><button className="btn-primary" disabled={busy || n <= 0}>{busy ? '建立並執行中…' : `建立（${n} 個分片）`}</button><span className="note" style={{ marginTop: 0 }}>每片記錄來源、導出、載入筆數與校驗；差異 &gt; {pct(ex.rules.rerun_threshold)} 自動重跑；審計 migration.export.created。</span></div></form>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>任務</th><th>資料集</th><th>方式</th><th>範圍</th><th className="num">分片</th><th className="num">來源筆數</th><th className="num">導出</th><th className="num">載入</th><th className="num">差異</th><th>狀態</th><th>最近</th><th></th></tr></thead>
        <tbody>{ex.items.length === 0 ? <tr><td colSpan={12} className="note">還沒有導出任務。</td></tr> : ex.items.map((j) => <tr key={j.id} className={j.status === 'blocked' || j.status === 'warn' || j.status === 'failed' ? 'warnrow' : ''}><td className="mono">{j.id}</td><td>{inv.items.find((i) => i.id === j.dataset)?.cn ?? j.dataset}</td><td style={{ fontSize: 12 }}>{j.method === 'api' ? '查詢 API' : 'Impala／HDFS'}</td><td className="mono" style={{ fontSize: 11.5 }}>{j.date_from} ～ {j.date_to}</td><td className="num mono">{j.done}/{j.slices}</td><td className="num mono">{j.rows_src.toLocaleString()}</td><td className="num mono">{j.rows_exported.toLocaleString()}</td><td className="num mono">{j.rows_loaded.toLocaleString()}</td><td className="num mono" style={{ color: j.diff !== null && j.diff > ex.rules.rerun_threshold ? 'var(--red)' : 'inherit' }}>{j.diff === null ? '—' : pct(j.diff, 2)}</td><td><span className={`pill ${JOB_STATUS[j.status].pill}`}>{JOB_STATUS[j.status].cn}</span>{j.blocked_reason && <span className="sub">{j.blocked_reason}</span>}</td><td className="mono" style={{ fontSize: 11 }}>{j.last?.slice(0, 16).replace('T', ' ') ?? '—'}</td><td className="num" style={{ whiteSpace: 'nowrap' }}><button className="btn-ghost sm" onClick={() => void view(j.id)}>分片</button>{canUse && j.method === 'api' && <button className="btn-ghost sm" disabled={busy} onClick={() => void rerun(j.id)}>重跑</button>}</td></tr>)}</tbody></table></div></div>
      {sel && <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>{sel.id} 分片</h3><span className="sub">{sel.slices.length} 片</span><button className="btn-ghost sm right" onClick={() => setSel(null)}>關閉</button></div><div className="tbl-wrap" style={{ maxHeight: 360, overflowY: 'auto' }}><table className="tbl"><thead><tr><th>日期</th><th>狀態</th><th className="num">來源</th><th className="num">導出</th><th className="num">載入</th><th className="num">隔離</th><th className="num">重試</th><th>訊息</th><th>時間</th></tr></thead><tbody>{sel.slices.map((s) => <tr key={s.day} className={s.status === 'blocked' || s.status === 'failed' || s.status === 'mismatch' ? 'warnrow' : ''}><td className="mono">{s.day}</td><td><span className={`pill ${s.status === 'loaded' ? 'blue' : s.status === 'exported' ? '' : s.status === 'pending' ? 'gray' : 'amber'}`}>{{ loaded: '已載入', exported: '已導出', pending: '待執行', blocked: '待連線', failed: '失敗', mismatch: '筆數不符' }[s.status] ?? s.status}</span></td><td className="num mono">{s.rows_src ?? '—'}</td><td className="num mono">{s.rows_exported ?? '—'}</td><td className="num mono">{s.rows_loaded ?? '—'}</td><td className="num mono">{s.rows_quarantined ?? '—'}</td><td className="num mono">{s.attempt}</td><td style={{ fontSize: 12 }}>{s.error ?? s.note ?? ''}</td><td className="mono" style={{ fontSize: 11 }}>{s.at?.slice(0, 16).replace('T', ' ') ?? ''}</td></tr>)}</tbody></table></div>{canUse && sel.slices.some((s) => s.attempt >= ex.rules.max_auto_rerun) && <div className="row"><button className="btn-ghost sm danger" disabled={busy} onClick={() => void rerun(sel.id, true)}>已達自動重跑上限：人工確認後強制重跑</button></div>}</div>}
      <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>載入管線</h3></div><p className="note">神策（Impala／HDFS Parquet 或查詢 API 按日切片）→ 導出工具（日 × 資料集分片，md5 + 列數校驗）→ Kafka raw.sensors（與 GA 新資料分 topic）→ Flink normalize（事件名保留、時間轉 UTC、login_id → HMAC 客戶代碼、$ip 丟棄）→ OneID（綁定先載、登入事件歸客戶、匿名事件歸匿名用戶）→ ClickHouse 歷史分區（OPTIMIZE、MV 補算、對帳表）。單機環境走 sensors_format 直接代碼化後載入 ClickHouse。</p></div>
    </>
  );
}

function ReconTab({ rc, err, project, setProject, days, setDays, onLoad }: { rc: Reconcile | null; err: string | null; project: string; setProject: (v: string) => void; days: number; setDays: (v: number) => void; onLoad: () => Promise<void> }) {
  const [sel, setSel] = useState(0);
  const ev = rc?.t5.items[sel] ?? rc?.t5.items[0];
  const series = useMemo<Series[]>(() => ev ? [{ key: 'sa', label: '神策', metric: 'sa', group: '', points: ev.series.map((p) => ({ x: p.day, y: p.sa, suppressed: false })) }, { key: 'ga', label: 'GA360', metric: 'ga', group: '', points: ev.series.map((p) => ({ x: p.day, y: p.ga, suppressed: false })) }] : [], [ev]);
  const bars = rc ? [['兩邊都出現', rc.t6.both], ['只在神策', rc.t6.only_sa], ['只在 GA360', rc.t6.only_ga]] as [string, number][] : [];
  const max = Math.max(1, ...bars.map((b) => b[1]));
  return (
    <>
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><label style={{ marginBottom: 0 }}>專案<input value={project} onChange={(e) => setProject(e.target.value)} placeholder="空 = 全部" style={{ width: 160 }} /></label><label style={{ marginBottom: 0 }}>近 N 天<input type="number" min={1} max={366} value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: 80 }} /></label><button className="btn-primary" onClick={() => void onLoad()}>重算</button>{err && <span className="alert">{err}</span>}<span className="note" style={{ marginTop: 0 }}>數字來自 ClickHouse：ingest_recon（T2）、events.source 神策 vs GA360（T5）、登入用戶兩邊同代碼（T6）。單機 seed 的樣本在 project=migration_demo。</span></div>
      {rc && (
        <>
          <div className="grid c2">
            <div className="card"><div className="card-h"><h3>T5 並行期：每日量 神策 vs GA360</h3><span className="sub">{ev ? `${ev.event} → ${ev.ga4_event} · 近 ${rc.days} 天` : '沒有並行期資料'}</span></div>{ev ? <LineChart series={series} height={220} /> : <p className="note">沒有 source = sensors／ga360 的事件。</p>}</div>
            <div className="card"><div className="card-h"><h3>T6 登入用戶銜接</h3><span className="sub">期間內出現的客戶代碼（v1_）</span></div><div className="bars">{bars.map(([k, v]) => <div className="bar" key={k}><span>{k}</span><i style={{ width: `${Math.round(100 * v / max)}%` }} /><em>{v.toLocaleString()}</em></div>)}</div><p className="note">銜接率 <b className="mono">{pct(rc.t6.rate)}</b>（目標 ≥ {pct(rc.thresholds.t6_user_bridge ?? 0.95, 0)}）{rc.t6.ok ? '，通過' : '，未達標'}。匿名用戶不銜接，分析按時期分開看。</p></div>
          </div>
          <div className="card flush" style={{ marginTop: 16 }}><div className="tbl-wrap"><table className="tbl"><thead><tr><th>神策事件</th><th>GA4 事件</th><th className="num">神策／日</th><th className="num">GA360／日</th><th className="num">差異</th><th>判定</th></tr></thead>
            <tbody>{rc.t5.items.map((r, i) => <tr key={r.event} className={`clickable ${!r.ok ? 'warnrow' : ''}`} onClick={() => setSel(i)}><td className="mono" style={{ fontWeight: 600 }}>{r.event}</td><td className="mono">{r.ga4_event}</td><td className="num mono">{r.sa_per_day.toLocaleString()}</td><td className="num mono">{r.ga_per_day.toLocaleString()}</td><td className="num mono" style={{ color: r.ok ? 'inherit' : 'var(--red)' }}>{diffText(r.diff)}</td><td><span className={`pill ${r.ok ? 'blue' : 'red'}`}>{r.ok ? '通過' : `> ${pct(rc.thresholds.t5_parallel ?? 0.05, 0)} 查埋點`}</span></td></tr>)}</tbody></table></div></div>
          <div className="card flush" style={{ marginTop: 16 }}><div className="card-h" style={{ padding: '12px 12px 0' }}><h3>T2 筆數對帳（神策 → 載入）</h3><span className="sub">{rc.t2.ok === null ? '沒有載入紀錄' : rc.t2.ok ? '全部通過' : '有分片差異 > 門檻'}</span></div><div className="tbl-wrap" style={{ maxHeight: 300, overflowY: 'auto' }}><table className="tbl"><thead><tr><th>任務</th><th>日期</th><th className="num">來源</th><th className="num">載入</th><th className="num">隔離</th><th className="num">差異</th><th>判定</th></tr></thead><tbody>{rc.t2.items.map((r, i) => <tr key={i} className={r.ok ? '' : 'warnrow'}><td className="mono">{r.task_id}</td><td className="mono">{r.day}</td><td className="num mono">{r.rows_in.toLocaleString()}</td><td className="num mono">{r.rows_loaded.toLocaleString()}</td><td className="num mono">{r.quarantined}</td><td className="num mono">{r.diff === null ? '—' : pct(r.diff, 2)}</td><td><span className={`pill ${r.ok ? 'blue' : 'amber'}`}>{r.ok ? '通過' : '重跑'}</span></td></tr>)}</tbody></table></div></div>
          <div className="grid c2" style={{ marginTop: 16 }}>
            <div className="card"><div className="card-h"><h3>對帳口徑</h3></div><ul className="priv"><li>T2 筆數：每事件每日 神策 → 導出 → 載入，去重後差異 &lt; {pct(rc.thresholds.t2_count ?? 0.001, 1)}。</li><li>T5 並行：同一事件每日量 神策 vs GA360，差異 ≤ {pct(rc.thresholds.t5_parallel ?? 0.05, 0)}（同意拒絕、攔截、串流延遲）。</li><li>T6 OneID：登入用戶 ≥ {pct(rc.thresholds.t6_user_bridge ?? 0.95, 0)} 兩邊同一客戶代碼；跨切換日漏斗無斷崖。</li></ul></div>
            <div className="card"><div className="card-h"><h3>口徑差異（已文件化）</h3></div><ul className="priv">{rc.caveats.map((c) => <li key={c.item}><b>{c.item}</b>：{c.note}</li>)}</ul></div>
          </div>
        </>
      )}
    </>
  );
}
