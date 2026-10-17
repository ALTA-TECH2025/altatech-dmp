// 元事件管理：事件字典（搜尋、狀態、登記／編輯抽屜、上線檢查、狀態轉移）、屬性字典（登記）、虛擬事件（建立）、隔離區、變更單（審批）。
// 權限：governance.view 看；governance.use 登記／修改；governance.admin 審批變更單。語意層 yaml 來的項目標示「語意層」，執行期登記的標示「待發版」。
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { checkEvent, dailyText, ENTITY_CN, KIND_CN, SENS_CN, SENS_PILL, STATUS_CN } from './governance';
import type { ChangeRequest, EventList, EventProp, EventStatus, Level, MetaEvent, MetaEventDetail, MetaProperty, PropertyList, Quarantine, VirtualEvent } from './types';

type Tab = 'dictionary' | 'props' | 'virtual' | 'quarantine' | 'changes';
const SOURCES = ['GA360（App／Web 埋點）', 'App 後台', '開戶系統', '數據中台（批次導入）', '交易 CDC', '帳務 CDC', '基金平台', 'MA 系統事件', 'CRM', '業務系統'];

export function GovernancePage() {
  return <RequireGrant module="governance"><GovernanceInner /></RequireGrant>;
}

function GovernanceInner() {
  const { can } = useAuth();
  const canUse = can('governance', 'use'); const canAdmin = can('governance', 'admin');
  const [tab, setTab] = useState<Tab>('dictionary');
  const [ev, setEv] = useState<EventList | null>(null);
  const [props, setProps] = useState<PropertyList | null>(null);
  const [virt, setVirt] = useState<VirtualEvent[]>([]);
  const [qz, setQz] = useState<Quarantine | null>(null);
  const [crs, setCrs] = useState<ChangeRequest[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState(''); const [status, setStatus] = useState<EventStatus | 'all'>('all');
  const [drawer, setDrawer] = useState<{ event: MetaEvent | null } | null>(null);
  const reload = async () => {
    try { const [a, b, c, d, e] = await Promise.all([api.get<EventList>('/v1/meta/events'), api.get<PropertyList>('/v1/meta/properties'), api.get<VirtualEvent[]>('/v1/meta/virtual-events'), api.get<Quarantine>('/v1/meta/quarantine'), api.get<ChangeRequest[]>('/v1/meta/change-requests')]); setEv(a); setProps(b); setVirt(c); setQz(d); setCrs(e); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); }
  };
  useEffect(() => { void reload(); }, []);
  const list = useMemo(() => (ev?.items ?? []).filter((e) => (status === 'all' || e.status === status) && (!q || e.name.toLowerCase().includes(q.toLowerCase()) || e.cn.includes(q) || e.group.includes(q))), [ev, q, status]);
  const pending = crs.filter((c) => c.status === 'pending').length; const quarantined = ev?.counts.quarantine ?? 0;
  return (
    <div className="page">
      <div className="page-h"><div><h1>元事件管理</h1><p>事件字典是語意層的根：事件、屬性字典、虛擬事件、隔離區與變更單。未登記事件進隔離區；上線事件改定義走變更單審批；L4 個資屬性拒絕入倉。語意層（events.yaml）為契約來源，這裡登記的項目在發版後進查詢目錄。</p></div>
        <div className="row" style={{ marginTop: 0 }}>{canUse ? <button className="btn-primary" onClick={() => setDrawer({ event: null })}>＋ 登記事件</button> : <span className="pill gray">唯讀</span>}</div></div>
      {err && <div className="alert">{err}</div>}
      <div className="seg">{([['dictionary', `事件字典 ${ev?.total ?? ''}`], ['props', `屬性字典 ${props?.items.length ?? ''}`], ['virtual', `虛擬事件 ${virt.length}`], ['quarantine', `隔離區${quarantined ? ` ${quarantined}` : ''}`], ['changes', `變更單${pending ? ` ${pending}` : ''}`]] as [Tab, string][]).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
      {tab === 'dictionary' && ev && (
        <>
          <div className="row" style={{ marginTop: 0, marginBottom: 12 }}>
            <input placeholder="搜尋事件名稱／顯示名／業務線（例：開戶、sip_、$Plan）" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 320px' }} />
            <span className="seg" style={{ margin: 0 }}>{([['all', `全部 ${ev.total}`], ['live', `上線 ${ev.counts.live ?? 0}`], ['draft', `草稿 ${ev.counts.draft ?? 0}`], ['quarantine', `隔離 ${ev.counts.quarantine ?? 0}`], ['deprecated', `停用 ${ev.counts.deprecated ?? 0}`]] as [EventStatus | 'all', string][]).map(([k, cn]) => <a key={k} href="#" className={status === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setStatus(k); }}>{cn}</a>)}</span>
            <span className="note" style={{ marginTop: 0 }}>{ev.stats_source ? '日均量 = 近 7 天 ClickHouse 實際' : '日均量：未連接 ClickHouse'}</span>
          </div>
          <div className="card flush"><div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>事件</th><th>業務線</th><th>平台</th><th>類別</th><th>來源</th><th>屬性</th><th className="num">日均量</th><th>負責人</th><th>狀態</th><th></th></tr></thead>
            <tbody>{list.map((e) => (
              <tr key={e.name} className={e.status === 'quarantine' ? 'warnrow' : ''}>
                <td><b className="mono">{e.name}</b><span className="sub">{e.cn}{e.aliases.length > 0 && ` · 舊名 ${e.aliases.join('、')}`}{e.note && ` · ${e.note}`}</span></td>
                <td>{e.group}</td><td className="mono" style={{ fontSize: 10.5, color: 'var(--muted)' }}>{e.platforms.replace(/JavaScript/g, 'JS').replace(/服務端/g, 'Server')}</td>
                <td><span className="pill">{KIND_CN[e.kind]}</span></td><td style={{ fontSize: 12 }}>{e.source}</td>
                <td className="mono" style={{ fontSize: 11 }}>{e.properties.slice(0, 4).map((p) => p.key).join(', ')}{e.properties.length > 4 && ` +${e.properties.length - 4}`}</td>
                <td className="num mono">{dailyText(e.daily)}</td><td>{e.owner || '—'}</td>
                <td><span className={`pill ${STATUS_CN[e.status].pill}`}>{STATUS_CN[e.status].cn}</span>{!e.catalog_synced && <span className="pill amber" title="已登記，發版（契約 PR）後進查詢目錄">待發版</span>}{e.pending_change && <span className="pill amber">變更單</span>}</td>
                <td className="num"><button className="btn-ghost sm" onClick={() => setDrawer({ event: e })}>{canUse ? (e.status === 'quarantine' ? '登記' : '編輯') : '查看'}</button></td>
              </tr>))}</tbody>
          </table></div></div>
          <div className="grid c3" style={{ marginTop: 16 }}>
            <div className="card"><div className="card-h"><h3>事件生命週期</h3></div><p className="note">草稿 → 上線（檢查：命名、負責人、屬性型別／敏感等級、無 L4）→ 變更需審批 → 停用（保留歷史）。未登記事件由 Collector 攔進隔離區，日批統計後通知負責人登記；30 天未登記自動丟棄。</p></div>
            <div className="card"><div className="card-h"><h3>與語意層的關係</h3></div><p className="note">事件字典是 semantic/events.yaml 的來源：上線事件出現在事件分析／漏斗的事件選單與 agent 的工具目錄；ga4_mapping 依「來源=GA360」的事件自動產生映射。這裡登記的事件在發版（契約 PR）後同步進語意層。</p></div>
            <div className="card"><div className="card-h"><h3>變更與審計</h3></div><p className="note">上線事件的屬性型別、敏感等級、主體、類別變更建立變更單，需資料治理管理員審批且不可自審；所有登記、狀態轉移、審批都寫審計（meta.*）。</p></div>
          </div>
        </>
      )}
      {tab === 'props' && props && <PropsTab props={props} canUse={canUse} onChange={reload} />}
      {tab === 'virtual' && <VirtualTab items={virt} events={ev?.items ?? []} canUse={canUse} onChange={reload} />}
      {tab === 'quarantine' && qz && <QuarantineTab qz={qz} canUse={canUse} onRegister={(name) => setDrawer({ event: { name, cn: '', group: '未登記', entity: 'user', source: 'GA360（App／Web 埋點）', kind: 'behavior', status: 'quarantine', platforms: '', aliases: [], properties: [], owner: '', note: '', catalog_synced: false, version: 0 } })} />}
      {tab === 'changes' && <ChangesTab items={crs} canAdmin={canAdmin} onChange={reload} />}
      {drawer && <EventDrawer event={drawer.event} all={ev?.items ?? []} props={props?.items ?? []} forbidden={new Set(props?.forbidden_l4 ?? [])} canUse={canUse} onClose={() => setDrawer(null)} onSaved={async () => { setDrawer(null); await reload(); }} />}
    </div>
  );
}

function EventDrawer({ event, all, props, forbidden, canUse, onClose, onSaved }: { event: MetaEvent | null; all: MetaEvent[]; props: MetaProperty[]; forbidden: Set<string>; canUse: boolean; onClose: () => void; onSaved: () => Promise<void> }) {
  const isNew = !event || event.status === 'quarantine';
  const [f, setF] = useState({ name: event?.name ?? '', cn: event?.cn ?? '', entity: event?.entity ?? 'user', kind: event?.kind ?? 'behavior', source: event?.source ?? SOURCES[0], owner: event?.owner ?? '', note: event?.note ?? '', group: event?.group ?? '', path_node_rule: event?.path_node_rule ?? '',
    properties: (event?.properties ?? []).map((p) => ({ key: p.key, type: p.type ?? props.find((x) => x.key === p.key)?.type ?? 'string', sensitivity: (p.sensitivity ?? props.find((x) => x.key === p.key)?.sensitivity ?? 'L1') as Level, required: p.required ?? true })) as EventProp[] });
  const [detail, setDetail] = useState<MetaEventDetail | null>(null);
  const [newProp, setNewProp] = useState('');
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [reason, setReason] = useState('');
  useEffect(() => { if (event && event.status !== 'quarantine') api.get<MetaEventDetail>(`/v1/meta/events/${encodeURIComponent(event.name)}`).then(setDetail).catch(() => setDetail(null)); }, [event]);
  const errs = checkEvent(f, new Set(all.filter((e) => e.status !== 'quarantine').map((e) => e.name)), isNew, forbidden);
  const live = event?.status === 'live';
  const setProp = (i: number, p: Partial<EventProp>) => setF({ ...f, properties: f.properties.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const submit = async (status: 'draft' | 'live') => {
    setBusy(true); setErr(null);
    try {
      const body = { ...f, status };
      if (isNew) await api.post('/v1/meta/events', body);
      else if (live) await api.patch(`/v1/meta/events/${encodeURIComponent(event.name)}`, { ...f, reason });
      else { await api.patch(`/v1/meta/events/${encodeURIComponent(event.name)}`, f); if (status === 'live') await api.post(`/v1/meta/events/${encodeURIComponent(event.name)}/status`, { to: 'live' }); }
      await onSaved();
    } catch (e) { setErr(e instanceof ApiError ? (typeof e.body === 'object' && e.body && 'detail' in e.body && typeof (e.body as { detail: unknown }).detail === 'object' ? JSON.stringify((e.body as { detail: { errors?: string[] } }).detail.errors ?? e.message) : e.message) : '儲存失敗'); }
    finally { setBusy(false); }
  };
  const deprecate = async () => { if (!event || !confirm(`停用 ${event.name}？不再接收但保留歷史。`)) return; setBusy(true); try { await api.post(`/v1/meta/events/${encodeURIComponent(event.name)}/status`, { to: 'deprecated', reason }); await onSaved(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } finally { setBusy(false); } };
  return (
    <div className="drawer-bk" onClick={onClose}><div className="drawer" onClick={(e) => e.stopPropagation()}>
      <div className="card-h"><h3>{isNew ? (event ? `登記隔離區事件：${event.name}` : '登記新事件') : `編輯事件：${event.name}`}</h3>{event && <span className={`pill ${STATUS_CN[event.status].pill}`}>{STATUS_CN[event.status].cn}</span>}<button className="btn-ghost sm right" onClick={onClose}>關閉</button></div>
      {err && <div className="alert">{err}</div>}
      <div className="grid c2">
        <label>事件名稱<input className="mono" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} disabled={!isNew || !!event} placeholder="例：fund_redeem（上線後不可改名，只能停用）" /></label>
        <label>顯示名稱<input value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} disabled={!canUse} /></label>
        <label>主體<select value={f.entity} onChange={(e) => setF({ ...f, entity: e.target.value as MetaEvent['entity'] })} disabled={!canUse}>{(Object.keys(ENTITY_CN) as MetaEvent['entity'][]).map((k) => <option key={k} value={k}>{ENTITY_CN[k]}</option>)}</select></label>
        <label>類別<select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as MetaEvent['kind'] })} disabled={!canUse}>{(Object.keys(KIND_CN) as MetaEvent['kind'][]).map((k) => <option key={k} value={k}>{KIND_CN[k]}</option>)}</select></label>
        <label>來源<select value={SOURCES.includes(f.source) ? f.source : '__other'} onChange={(e) => setF({ ...f, source: e.target.value === '__other' ? f.source : e.target.value })} disabled={!canUse}>{SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}<option value="__other">{SOURCES.includes(f.source) ? '其他…' : f.source}</option></select></label>
        <label>業務線<input value={f.group} onChange={(e) => setF({ ...f, group: e.target.value })} disabled={!canUse} /></label>
        <label>負責人<input value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} placeholder="團隊或人" disabled={!canUse} /></label>
        <label>路徑節點規則<select value={f.path_node_rule ?? ''} onChange={(e) => setF({ ...f, path_node_rule: e.target.value })} disabled={!canUse}><option value="">事件本身為節點</option>{f.properties.map((p) => <option key={p.key} value={p.key}>按 {p.key} 拆分</option>)}</select></label>
      </div>
      <label>說明<input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} disabled={!canUse} /></label>
      <div className="card-h" style={{ marginTop: 14 }}><h3>事件屬性</h3><span className="sub">屬性須先存在於屬性字典；新屬性在此登記會同步進字典；型別與敏感等級一經上線不可降級</span></div>
      <div className="tbl-wrap"><table className="tbl"><thead><tr><th>屬性</th><th>型別</th><th>敏感等級</th><th>必填</th><th></th></tr></thead><tbody>
        {f.properties.map((p, i) => <tr key={p.key}><td className="mono">{p.key}{!props.some((x) => x.key === p.key) && <span className="pill amber">新</span>}</td>
          <td><select value={p.type} onChange={(e) => setProp(i, { type: e.target.value })} disabled={!canUse}>{['string', 'number', 'bool', 'datetime', 'list'].map((t) => <option key={t}>{t}</option>)}</select></td>
          <td><select value={p.sensitivity} onChange={(e) => setProp(i, { sensitivity: e.target.value as Level })} disabled={!canUse}>{(['L0', 'L1', 'L2', 'L3', 'L4'] as Level[]).map((s) => <option key={s} value={s}>{s} {SENS_CN[s]}</option>)}</select></td>
          <td><input type="checkbox" checked={p.required ?? true} onChange={(e) => setProp(i, { required: e.target.checked })} disabled={!canUse} /></td>
          <td className="num">{canUse && <button className="btn-ghost sm danger" onClick={() => setF({ ...f, properties: f.properties.filter((_, j) => j !== i) })}>✕</button>}</td></tr>)}
      </tbody></table></div>
      {canUse && <div className="row"><select value="" onChange={(e) => { const p = props.find((x) => x.key === e.target.value); if (p) setF({ ...f, properties: [...f.properties, { key: p.key, type: p.type, sensitivity: p.sensitivity, required: true }] }); }}><option value="">＋ 從屬性字典加入</option>{props.filter((x) => !f.properties.some((p) => p.key === x.key)).map((x) => <option key={x.key} value={x.key}>{x.cn}（{x.key}）</option>)}</select>
        <input placeholder="或輸入新屬性名稱後按 Enter" value={newProp} onChange={(e) => setNewProp(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && newProp.trim()) { e.preventDefault(); setF({ ...f, properties: [...f.properties, { key: newProp.trim(), type: 'string', sensitivity: 'L1', required: true }] }); setNewProp(''); } }} /></div>}
      <div className="card-h" style={{ marginTop: 14 }}><h3>上線檢查</h3></div>
      {errs.length ? <ul className="priv" style={{ color: 'var(--red)' }}>{errs.map((e) => <li key={e}>{e}</li>)}</ul> : <span className="pill blue">可上線：命名、負責人、屬性型別與敏感等級皆完整</span>}
      {live && <label style={{ marginTop: 10 }}>變更理由（改屬性／主體／類別會建立變更單，需治理管理員審批）<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>}
      {detail && detail.history.length > 0 && <><div className="card-h" style={{ marginTop: 14 }}><h3>歷程</h3><span className="sub">{detail.used_by.join('、')}</span></div><ul className="priv">{detail.history.slice(-6).map((h, i) => <li key={i}><span className="mono">{h.at.slice(0, 16).replace('T', ' ')}</span> {h.action} · {h.by}</li>)}</ul></>}
      {canUse && <div className="row" style={{ marginTop: 16 }}>
        {live && <button className="btn-ghost danger" disabled={busy} onClick={() => void deprecate()}>停用</button>}
        <div className="right row" style={{ marginTop: 0 }}>
          {!live && <button className="btn-ghost" disabled={busy} onClick={() => void submit('draft')}>存為草稿</button>}
          <button className="btn-primary" disabled={busy || (!live && errs.length > 0)} onClick={() => void submit('live')}>{live ? '送出變更' : '登記並上線'}</button>
        </div></div>}
      <p className="note">上線後：事件進語意層（agent 可查）、Collector 放行、Flink 事件化套用；改屬性型別或敏感等級需走審批；停用 = 不再接收但保留歷史。</p>
    </div></div>
  );
}

function PropsTab({ props, canUse, onChange }: { props: PropertyList; canUse: boolean; onChange: () => Promise<void> }) {
  const [q, setQ] = useState(''); const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ key: '', cn: '', type: 'string', scope: 'event', sensitivity: 'L1' as Level, source: '', enum: '', unit: '' });
  const list = props.items.filter((p) => !q || p.key.includes(q) || p.cn.includes(q) || p.sa.includes(q));
  const submit = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api.post('/v1/meta/properties', { ...f, enum: f.enum ? f.enum.split(/[,，\s]+/).filter(Boolean) : [] }); setOpen(false); setF({ ...f, key: '', cn: '', enum: '' }); await onChange(); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  return (
    <>
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><input placeholder="搜尋屬性／神策舊名" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 280px' }} /><span className="note" style={{ marginTop: 0 }}>L4 屬性一律拒絕入倉（{props.forbidden_l4.length} 個已列黑名單）；L3 以 HMAC／FPE 代碼化；L2 匯出區間化</span>{canUse && <button className="btn-ghost right" onClick={() => setOpen((v) => !v)}>＋ 登記屬性</button>}</div>
      {open && <form className="card stack" style={{ marginBottom: 12 }} onSubmit={submit}>{err && <div className="alert">{err}</div>}<div className="grid c3">
        <label>屬性名<input className="mono" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} required /></label><label>顯示名<input value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} /></label>
        <label>型別<select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>{['string', 'number', 'bool', 'datetime', 'list', 'enum'].map((t) => <option key={t}>{t}</option>)}</select></label>
        <label>範圍<select value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}><option value="event">事件</option><option value="user">用戶</option><option value="item">Item</option></select></label>
        <label>敏感等級<select value={f.sensitivity} onChange={(e) => setF({ ...f, sensitivity: e.target.value as Level })}>{(['L0', 'L1', 'L2', 'L3'] as Level[]).map((s) => <option key={s} value={s}>{s} {SENS_CN[s]}</option>)}</select></label>
        <label>來源<input value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} /></label><label>枚舉（逗號分隔）<input value={f.enum} onChange={(e) => setF({ ...f, enum: e.target.value })} /></label><label>單位<input value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} /></label></div>
        <div className="row" style={{ marginTop: 0 }}><button className="btn-primary">登記</button><span className="note" style={{ marginTop: 0 }}>既有屬性：型別只能放寬、敏感等級只能升</span></div></form>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>屬性</th><th>神策舊名</th><th>型別</th><th>敏感等級</th><th>來源</th><th>枚舉／單位</th><th>維度表／字典</th><th className="num">使用事件數</th><th>狀態</th></tr></thead>
        <tbody>{list.map((p) => <tr key={p.key}><td><b className="mono">{p.key}</b><span className="sub">{p.cn}{p.caveats.length > 0 && ` · ${p.caveats[0]}`}</span></td><td className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>{p.sa || '—'}</td><td className="mono">{p.type}</td><td><span className={`pill ${SENS_PILL[p.sensitivity]}`}>{p.sensitivity}</span>{p.row_level && <span className="pill">行級鍵</span>}</td><td style={{ fontSize: 12 }}>{p.source}</td><td className="mono" style={{ fontSize: 11 }}>{p.enum.length ? p.enum.join(' | ') : p.unit || '—'}</td><td>{p.dimension_table && <span className="pill blue">{p.dimension_table}</span>}{p.dictionary && <span className="pill">字典</span>}</td><td className="num mono">{p.used_by}</td><td>{p.in_catalog ? <span className="pill gray">語意層</span> : <span className="pill amber">待發版</span>}</td></tr>)}</tbody>
      </table></div></div>
    </>
  );
}

function VirtualTab({ items, events, canUse, onChange }: { items: VirtualEvent[]; events: MetaEvent[]; canUse: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ name: 'v.', cn: '', owner: '', note: '', members: [{ event: '', filters: '' }] });
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try {
      const members = f.members.filter((m) => m.event).map((m) => { const cond = m.filters.trim(); const mm = cond.match(/^([\w$.]+)\s*(=|in)\s*(.+)$/); return cond && mm ? { event: m.event, filters: [{ field: mm[1], op: mm[2] === '=' ? 'eq' : 'in', value: mm[2] === '=' ? mm[3].trim() : mm[3].split(/[,，\s]+/).filter(Boolean) }] } : { event: m.event }; });
      await api.post('/v1/meta/virtual-events', { ...f, members }); setOpen(false); setF({ name: 'v.', cn: '', owner: '', note: '', members: [{ event: '', filters: '' }] }); await onChange();
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); }
  };
  return (
    <>
      {canUse && <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><button className="btn-ghost" onClick={() => setOpen((v) => !v)}>＋ 建立虛擬事件</button></div>}
      {open && <form className="card stack" style={{ marginBottom: 12 }} onSubmit={submit}>{err && <div className="alert">{err}</div>}
        <div className="grid c3"><label>名稱（v. 開頭）<input className="mono" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></label><label>顯示名<input value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} /></label><label>負責人<input value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} /></label></div>
        {f.members.map((m, i) => <div className="row" key={i} style={{ marginTop: 0 }}><span className="pill blue">{i ? 'OR' : '事件'}</span><select className="grow" value={m.event} onChange={(e) => setF({ ...f, members: f.members.map((x, j) => (j === i ? { ...x, event: e.target.value } : x)) })}><option value="">選事件…</option>{events.filter((e) => e.status === 'live').map((e) => <option key={e.name} value={e.name}>{e.cn}（{e.name}）</option>)}</select><input placeholder="屬性條件（選填）例：platform in ios,android 或 page_group = account_open" value={m.filters} onChange={(e) => setF({ ...f, members: f.members.map((x, j) => (j === i ? { ...x, filters: e.target.value } : x)) })} style={{ flex: '1 1 300px' }} />{f.members.length > 1 && <button type="button" className="btn-ghost sm danger" onClick={() => setF({ ...f, members: f.members.filter((_, j) => j !== i) })}>✕</button>}</div>)}
        <div className="row" style={{ marginTop: 0 }}><button type="button" className="btn-ghost sm" onClick={() => setF({ ...f, members: [...f.members, { event: '', filters: '' }] })}>＋ OR 事件</button><button className="btn-primary">建立</button></div></form>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>虛擬事件</th><th>定義</th><th>負責人</th><th>說明</th><th>來源</th><th></th></tr></thead>
        <tbody>{items.map((v) => <tr key={v.name}><td><b className="mono">{v.name}</b><span className="sub">{v.cn}</span></td><td className="mono" style={{ fontSize: 11.5 }}>{v.definition}</td><td>{v.owner}</td><td style={{ fontSize: 12 }}>{v.note}</td><td>{v.origin === 'catalog' ? <span className="pill gray">語意層</span> : <span className="pill amber">待發版</span>}</td><td className="num">{canUse && v.origin === 'registered' && <button className="btn-ghost sm danger" onClick={() => { if (confirm(`刪除 ${v.name}？`)) api.del(`/v1/meta/virtual-events/${encodeURIComponent(v.name)}`).then(onChange).catch(() => undefined); }}>刪除</button>}</td></tr>)}</tbody></table></div></div>
      <p className="note">虛擬事件 = 多個真實事件（可帶屬性條件）的 OR 組合，在語意層展開，不另存資料；可用於事件分析、漏斗、留存與標籤定義。</p>
    </>
  );
}

function QuarantineTab({ qz, canUse, onRegister }: { qz: Quarantine; canUse: boolean; onRegister: (name: string) => void }) {
  return (
    <>
      <div className="card flush"><div className="card-h" style={{ padding: '12px 12px 0' }}><h3>未登記事件</h3><span className="sub">{qz.stats_source ? '近 30 天 ClickHouse 實際' : '未連接 ClickHouse：只列語意層標示為隔離的事件'}</span></div><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>事件</th><th className="num">筆數</th><th className="num">用戶</th><th>首見</th><th>最近</th><th>狀態</th><th></th></tr></thead>
        <tbody>{qz.unregistered_events.length === 0 ? <tr><td colSpan={7} className="note">沒有未登記事件。</td></tr> : qz.unregistered_events.map((e) => <tr key={e.event} className="warnrow"><td><b className="mono">{e.event}</b>{e.note && <span className="sub">{e.note}</span>}</td><td className="num mono">{e.n ?? '—'}</td><td className="num mono">{e.users ?? '—'}</td><td className="mono">{e.first_seen?.slice(0, 16) ?? '—'}</td><td className="mono">{e.last_seen?.slice(0, 16) ?? '—'}</td><td><span className="pill red">隔離 · {e.retention_days} 天後丟棄</span></td><td className="num">{canUse && <button className="btn-ghost sm" onClick={() => onRegister(e.event)}>登記放行</button>}</td></tr>)}</tbody></table></div></div>
      <div className="card flush" style={{ marginTop: 16 }}><div className="card-h" style={{ padding: '12px 12px 0' }}><h3>同步任務隔離列（近 30 天）</h3></div><div className="tbl-wrap"><table className="tbl"><thead><tr><th>任務</th><th>原因</th><th className="num">筆數</th><th>最近</th></tr></thead>
        <tbody>{qz.rows.length === 0 ? <tr><td colSpan={4} className="note">沒有隔離列。</td></tr> : qz.rows.map((r, i) => <tr key={i}><td className="mono">{r.task_id}</td><td>{r.reason}</td><td className="num mono">{r.n}</td><td className="mono">{String(r.last_at).slice(0, 16)}</td></tr>)}</tbody></table></div></div>
      <p className="note">{qz.policy.note}</p>
    </>
  );
}

function ChangesTab({ items, canAdmin, onChange }: { items: ChangeRequest[]; canAdmin: boolean; onChange: () => Promise<void> }) {
  const [err, setErr] = useState<string | null>(null);
  const decide = async (id: string, approve: boolean) => { const note = approve ? '' : prompt('駁回理由') ?? ''; try { await api.post(`/v1/meta/change-requests/${id}/decide`, { approve, note }); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  return (
    <>
      {err && <div className="alert">{err}</div>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>變更單</th><th>事件</th><th>變更</th><th>提出</th><th>狀態</th><th></th></tr></thead>
        <tbody>{items.length === 0 ? <tr><td colSpan={6} className="note">沒有變更單。上線事件的屬性、主體、類別變更會在這裡等待審批。</td></tr> : items.map((c) => <tr key={c.id} className={c.status === 'pending' ? 'warnrow' : ''}><td className="mono">{c.id}</td><td className="mono">{c.event}</td><td className="mono" style={{ fontSize: 11 }}>{Object.keys(c.changes).join('、')}{c.note && <span className="sub">{c.note}</span>}</td><td>{c.requested_by}<span className="sub mono">{c.at.slice(0, 16).replace('T', ' ')}</span></td><td><span className={`pill ${c.status === 'pending' ? 'amber' : c.status === 'approved' ? 'blue' : 'gray'}`}>{c.status === 'pending' ? '待審' : c.status === 'approved' ? '已核准' : '已駁回'}</span>{c.decided_by && <span className="sub">{c.decided_by}</span>}</td>
          <td className="num">{canAdmin && c.status === 'pending' && <><button className="btn-ghost sm" onClick={() => void decide(c.id, true)}>核准</button> <button className="btn-ghost sm danger" onClick={() => void decide(c.id, false)}>駁回</button></>}</td></tr>)}</tbody></table></div></div>
      <p className="note">審批需 governance.admin（資訊管理／系統管理員），不可核准自己提出的變更單；核准後定義升版，審計 meta.change_request.decided。</p>
    </>
  );
}
