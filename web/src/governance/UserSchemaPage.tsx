// 用戶表管理：用戶屬性（來源、更新、敏感、可篩選／分組、覆蓋率；登記新屬性）、OneID 規則（優先序／上限／狀態可改，login_id 不可動；需 governance.admin）、個資防護與統計。
import { useEffect, useState, type FormEvent } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { fmtNum } from '@/analytics/series';
import { pct, SENS_CN, SENS_PILL } from './governance';
import type { Level, OneIdRule, OneIdRules, UserPropertyList } from './types';

type Tab = 'props' | 'oneid' | 'privacy';
const PRIVACY: [string, string, string][] = [['姓名', '不入倉；細查頁不顯示', '—'], ['身分證字號', '不入倉；OneID 不使用', '正規式 [A-Z][12]\\d{8} 掃描 → 拒絕＋告警'], ['手機', '只存 HMAC-SHA256（金鑰在 KMS）作識別', '09\\d{8} 掃描'], ['Email', '只存 HMAC 作識別', 'RFC 5322 掃描'], ['完整帳號', 'FPE 代碼化（可 JOIN 不可反推）', '—'], ['生日', '只存年齡層分桶', '—'], ['地址', '只存縣市', '—']];

export function UserSchemaPage() {
  return <RequireGrant module="governance"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const canUse = can('governance', 'use'); const canAdmin = can('governance', 'admin');
  const [tab, setTab] = useState<Tab>('props');
  const [up, setUp] = useState<UserPropertyList | null>(null);
  const [oi, setOi] = useState<OneIdRules | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reload = async () => { try { const [a, b] = await Promise.all([api.get<UserPropertyList>('/v1/meta/user-properties'), api.get<OneIdRules>('/v1/meta/oneid/rules')]); setUp(a); setOi(b); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } };
  useEffect(() => { void reload(); }, []);
  return (
    <div className="page">
      <div className="page-h"><div><h1>用戶表管理</h1><p>用戶屬性（來源、更新、敏感等級、可篩選／分組、覆蓋率）、OneID 識別與合併規則、個資防護。用戶表的每個可篩選屬性都會出現在分析的「用戶屬性」條件裡；L4 欄位永不入倉。</p></div></div>
      {err && <div className="alert">{err}</div>}
      <div className="seg">{([['props', `用戶屬性 ${up?.items.length ?? ''}`], ['oneid', 'OneID 規則'], ['privacy', '個資防護與統計']] as [Tab, string][]).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
      {tab === 'props' && up && <PropsTab up={up} canUse={canUse} onChange={reload} />}
      {tab === 'oneid' && oi && <OneIdTab oi={oi} canAdmin={canAdmin} onChange={reload} />}
      {tab === 'privacy' && up && <PrivacyTab up={up} />}
    </div>
  );
}

function PropsTab({ up, canUse, onChange }: { up: UserPropertyList; canUse: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ key: '', cn: '', type: 'string', values: '', source: '', update: 'daily', sensitivity: 'L1' as Level, filterable: true, groupable: false, note: '' });
  const submit = async (e: FormEvent) => { e.preventDefault(); setErr(null); try { await api.post('/v1/meta/user-properties', { ...f, values: f.values ? f.values.split(/[,，\s]+/).filter(Boolean) : [] }); setOpen(false); await onChange(); } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '失敗'); } };
  const low = up.items.filter((p) => p.coverage !== null && p.coverage < 0.8);
  return (
    <>
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><span className="note" style={{ marginTop: 0 }}>{up.users_total !== null ? `users ${fmtNum(up.users_total)} 列 · 覆蓋率 = ClickHouse 實際` : '未連接 ClickHouse：沒有覆蓋率'} · L4 黑名單 {up.forbidden_l4_count} 欄</span>{canUse && <button className="btn-ghost right" onClick={() => setOpen((v) => !v)}>＋ 登記用戶屬性</button>}</div>
      {open && <form className="card stack" style={{ marginBottom: 12 }} onSubmit={submit}>{err && <div className="alert">{err}</div>}<div className="grid c3">
        <label>屬性名<input className="mono" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} required /></label><label>顯示名<input value={f.cn} onChange={(e) => setF({ ...f, cn: e.target.value })} /></label>
        <label>型別<select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>{['string', 'number', 'bool', 'enum', 'date', 'datetime'].map((t) => <option key={t}>{t}</option>)}</select></label>
        <label>枚舉值（逗號分隔）<input value={f.values} onChange={(e) => setF({ ...f, values: e.target.value })} /></label><label>來源（映射或 profile_* 協議）<input value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} placeholder="例：crm.cdc" /></label><label>更新<input value={f.update} onChange={(e) => setF({ ...f, update: e.target.value })} /></label>
        <label>敏感等級<select value={f.sensitivity} onChange={(e) => setF({ ...f, sensitivity: e.target.value as Level })}>{(['L0', 'L1', 'L2', 'L3'] as Level[]).map((s) => <option key={s} value={s}>{s} {SENS_CN[s]}</option>)}</select></label>
        <label><input type="checkbox" checked={f.filterable} onChange={(e) => setF({ ...f, filterable: e.target.checked })} /> 可篩選</label><label><input type="checkbox" checked={f.groupable} onChange={(e) => setF({ ...f, groupable: e.target.checked })} /> 可分組</label></div>
        <div className="row" style={{ marginTop: 0 }}><button className="btn-primary">登記</button><span className="note" style={{ marginTop: 0 }}>登記後標示「待發版」：同步進 user_properties.yaml／dimensions.yaml 需契約 PR；型別與敏感等級由治理審核。</span></div></form>}
      <div className="card flush"><div className="tbl-wrap"><table className="tbl">
        <thead><tr><th>屬性</th><th>神策舊名</th><th>型別</th><th>來源</th><th>更新</th><th>敏感</th><th>篩選</th><th>分組</th><th className="num">覆蓋率</th><th>狀態</th></tr></thead>
        <tbody>{up.items.map((p) => <tr key={p.key}><td><b className="mono">{p.key}</b><span className="sub">{p.cn}{p.note && ` · ${p.note}`}{p.row_level_key && ' · 行級權限鍵'}</span></td><td className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>{p.sa || '—'}</td><td className="mono">{p.type}{p.values.length > 0 && <span className="sub mono">{p.values.join(' | ')}</span>}{p.buckets && <span className="sub mono">分桶 {p.buckets.join('/')}</span>}</td><td style={{ fontSize: 12 }}>{p.source}</td><td className="mono" style={{ fontSize: 11.5 }}>{p.update}</td><td><span className={`pill ${SENS_PILL[p.sensitivity]}`}>{p.sensitivity}</span></td><td>{p.filterable ? '✓' : '—'}</td><td>{p.groupable ? '✓' : '—'}</td><td className="num mono" style={{ color: p.coverage !== null && p.coverage < 0.8 ? 'var(--red)' : 'inherit' }}>{pct(p.coverage, 1)}</td><td>{p.origin === 'catalog' ? <span className="pill gray">{p.stored ? (p.hot ? '熱欄位' : '語意層') : 'L3 不進寬表'}</span> : <span className="pill amber">待發版</span>}</td></tr>)}</tbody>
      </table></div></div>
      <div className="grid c3" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>寫入方式</h3></div><p className="note">{up.write_protocol.join('／')}（神策相容協議）；CDC 來源以映射規則寫入；標籤引擎每日覆寫標籤型屬性；事件推導屬性由 Flink 即時更新。</p></div>
        <div className="card"><div className="card-h"><h3>新增屬性</h3></div><p className="note">在數據整合的「欄位映射」把來源欄位對到用戶屬性即自動登記；型別與敏感等級由治理審核；可篩選／分組會同步到語意層 dimensions.yaml。</p></div>
        <div className="card"><div className="card-h"><h3>覆蓋率低的屬性</h3></div>{low.length ? <ul className="priv">{low.map((p) => <li key={p.key}><span className="mono">{p.key}</span> {pct(p.coverage)}</li>)}</ul> : <p className="note">{up.stats_source ? '沒有覆蓋率低於 80% 的屬性。' : '未連接 ClickHouse。'}</p>}</div>
      </div>
    </>
  );
}

function OneIdTab({ oi, canAdmin, onChange }: { oi: OneIdRules; canAdmin: boolean; onChange: () => Promise<void> }) {
  const [edit, setEdit] = useState(false); const [ids, setIds] = useState<OneIdRule[]>(oi.ids); const [reason, setReason] = useState(''); const [err, setErr] = useState<string | null>(null);
  useEffect(() => setIds(oi.ids), [oi]);
  const strong = new Set(oi.strong_ids);
  const save = async () => { setErr(null); try { await api.put('/v1/meta/oneid/rules', { ids: ids.map((i) => ({ key: i.key, priority: i.priority, max_count: i.max_count, over_limit: i.over_limit, status: i.status ?? 'enabled' })), reason }); setEdit(false); await onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : '失敗'); } };
  const st = oi.stats;
  return (
    <>
      {err && <div className="alert">{err}</div>}
      <div className="tiles" style={{ marginBottom: 16 }}>
        <div className="tile"><span className="sub">OneID 用戶數（有關聯邊）</span><b>{st.users === null ? '—' : fmtNum(st.users)}</b><span className="sub">{oi.stats_source ? 'ClickHouse id_edges' : '未連接 ClickHouse'}</span></div>
        <div className="tile"><span className="sub">已識別（有強 ID）</span><b>{st.users ? pct(st.identified !== null ? st.identified / st.users : null) : '—'}</b><span className="sub">{st.identified ?? '—'} 人可對應客戶代碼</span></div>
        <div className="tile"><span className="sub">近 30 天合併／拆分</span><b>{Object.keys(st.merges_30d).length ? Object.entries(st.merges_30d).map(([k, v]) => `${k} ${v}`).join(' · ') : '0'}</b><span className="sub">人工合併需審批；oneid_merge_log</span></div>
        <div className="tile"><span className="sub">近 24h 登入改寫</span><b>{st.backfill_24h ?? '—'}</b><span className="sub">近 7 天匿名事件改寫到登入客戶</span></div>
      </div>
      <div className="card flush"><div className="card-h" style={{ padding: '12px 12px 0' }}><h3>識別類型（對齊神策 IDM 3.0）</h3>{oi.override && <span className={`pill ${oi.override.status === 'pending_review' ? 'amber' : 'blue'}`}>修改待覆核 · {oi.override.by}</span>}<div className="right row" style={{ marginTop: 0 }}>{canAdmin && !edit && <button className="btn-ghost sm" onClick={() => setEdit(true)}>修改規則</button>}{edit && <><input placeholder="變更理由" value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: 200 }} /><button className="btn-primary" onClick={() => void save()}>送出覆核</button><button className="btn-ghost sm" onClick={() => { setEdit(false); setIds(oi.ids); }}>取消</button></>}</div></div>
        <div className="tbl-wrap"><table className="tbl"><thead><tr><th>優先序</th><th>識別類型</th><th>強度</th><th>數量</th><th>改綁／解綁</th><th>上限／超限</th><th>狀態</th><th>來源</th><th className="num">關聯邊 / 用戶</th><th>說明</th></tr></thead>
          <tbody>{ids.map((r, i) => <tr key={r.key}><td className="mono">{edit && r.key !== '$identity_login_id' ? <input type="number" min={2} max={20} value={r.priority} style={{ width: 60 }} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, priority: Number(e.target.value) } : x)))} /> : r.priority}</td><td><b className="mono">{r.key}</b><span className="sub">{r.cn}</span></td><td><span className={`pill ${strong.has(r.key) ? 'blue' : r.participates === false ? 'gray' : 'amber'}`}>{strong.has(r.key) ? '強' : r.participates === false ? '渠道' : '弱'}</span></td><td>{r.cardinality === 'single' ? '單值' : '多值'}</td><td style={{ fontSize: 12 }}>{r.rebind}</td>
            <td className="mono" style={{ fontSize: 11.5 }}>{edit && r.cardinality === 'multi' ? <><input type="number" value={r.max_count ?? ''} style={{ width: 60 }} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, max_count: e.target.value ? Number(e.target.value) : undefined } : x)))} /> <select value={r.over_limit ?? 'intercept'} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, over_limit: e.target.value } : x)))}><option value="intercept">攔截</option><option value="rolling_unbind">滾動解綁</option></select></> : (r.max_count ? `${r.max_count} · ${r.over_limit === 'rolling_unbind' ? '滾動解綁' : '攔截'}` : '—')}</td>
            <td>{edit && r.key !== '$identity_login_id' ? <select value={r.status ?? 'enabled'} onChange={(e) => setIds(ids.map((x, j) => (j === i ? { ...x, status: e.target.value } : x)))}><option value="enabled">啟用</option><option value="disabled">停用</option></select> : <span className={`pill ${(r.status ?? 'enabled') === 'enabled' ? 'blue' : 'gray'}`}>{(r.status ?? 'enabled') === 'enabled' ? '啟用' : '停用'}</span>}</td>
            <td style={{ fontSize: 11.5 }}>{r.source.join('、')}</td><td className="num mono">{r.stats ? `${r.stats.edges} / ${r.stats.users}` : '—'}</td><td style={{ fontSize: 12, maxWidth: 320 }}>{r.note}</td></tr>)}</tbody></table></div></div>
      <div className="grid c2" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>歸戶規則（docs/19 · oneid_rules.yaml）</h3></div><ul className="priv"><li><b>接入時決定</b>：每筆事件進入 Flink 時依其 identities 決定 one_id；未登入事件以 distinct_id 歸到匿名用戶。</li><li><b>登入歸戶 + 近 {String(oi.login_backfill.window_days ?? 7)} 天匿名行為改寫</b>：登入事件到達時做歸戶，並把同 distinct_id、登入前仍屬匿名用戶的事件改寫到該客戶；更早的事件不動。</li><li><b>一個 ID 同一時刻只屬於一個用戶</b>；兩個已存在的用戶不自動合併（人工合併／拆分需審批與審計）。</li><li><b>資料歸優先級最高的 ID</b>：登入事件永遠以客戶代碼為準；弱 ID 已屬已識別客戶時不自動改綁，記 conflict。</li><li>別名：{Object.entries(oi.alias).map(([a, b]) => `${a} → ${b}`).join('；') || '—'}。</li></ul></div>
        <div className="card"><div className="card-h"><h3>修改規則的限制</h3></div><ul className="priv"><li>只能改優先序、多值上限與超限策略、啟用狀態；識別類型本身與客戶代碼（優先級 1、不可停用）不可動。</li><li>送出後狀態為「待覆核」：稽核核准才生效，並觸發標籤重算；審計 meta.oneid.rules_changed。</li><li>需 governance.admin（資訊管理／系統管理員）。</li></ul></div>
      </div>
    </>
  );
}

function PrivacyTab({ up }: { up: UserPropertyList }) {
  return (
    <div className="grid c2">
      <div className="card"><div className="card-h"><h3>個資防護（L4 永不入倉）</h3></div><div className="tbl-wrap"><table className="tbl"><thead><tr><th>欄位</th><th>處理</th><th>偵測</th></tr></thead><tbody>{PRIVACY.map((r) => <tr key={r[0]}><td style={{ fontWeight: 600 }}>{r[0]}</td><td>{r[1]}</td><td className="mono" style={{ fontSize: 11.5 }}>{r[2]}</td></tr>)}</tbody></table></div><p className="note">黑名單（forbidden_l4）共 {up.forbidden_l4_count} 欄，含客戶神策事件屬性與用戶屬性裡的姓名、手機、信箱、生日、銀行帳號；接入與同步任務一律丟棄並記隔離。</p></div>
      <div className="stack">
        <div className="card"><div className="card-h"><h3>DSAR／刪除權</h3><span className="pill gray">第二階段</span></div><p className="note">客戶要求刪除：以客戶代碼找 one_id → 刪 users／id_map／user_tags → events 以 one_id 改寫為 tombstone（保留匿名統計）→ 下游推送撤回 → 30 天內完成並產出證明。工單流程待第二階段接上審批中心。</p></div>
        <div className="card"><div className="card-h"><h3>用戶表統計</h3></div><dl className="kv"><dt>users 列數</dt><dd className="mono">{up.users_total === null ? '—（未連接 ClickHouse）' : up.users_total.toLocaleString()}</dd><dt>屬性數</dt><dd className="mono">{up.items.length}</dd><dt>可篩選／可分組</dt><dd className="mono">{up.items.filter((p) => p.filterable).length} / {up.items.filter((p) => p.groupable).length}</dd><dt>熱欄位（users_dict）</dt><dd className="mono">{up.items.filter((p) => p.hot).length}</dd></dl></div>
      </div>
    </div>
  );
}
