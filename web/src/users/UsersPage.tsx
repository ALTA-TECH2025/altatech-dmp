// 用戶列表：條件（屬性／分群／做過未做過／分析落點來源）→ 即時估數 → 分頁列表（欄位、排序、遮罩）→ 存為分群 → 點列進細查（需理由）。
// 從漏斗／留存／事件分析下鑽：navigate('/users', { state: { source } })。
import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { ExportDialog } from './ExportDialog';
import { ProfileDrawer } from './ProfileDrawer';
import { RuleBuilder } from './RuleBuilder';
import { emptyRule, ruleValid, summarizeRule } from './rule';
import type { Segment, UsersEstimate, UsersListRequest, UsersListResult, UsersQuery, UsersSource } from './types';
import { UsersTable } from './UsersTable';

export function UsersPage() {
  return <RequireGrant module="users" action="use"><UsersInner /></RequireGrant>;
}

const DEFAULT_COLS = ['branch_id', 'lifecycle', 'tier', 'aum_bucket', 'churn_score', 'last_active_at'];

function UsersInner() {
  const { can } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const incoming = (loc.state as { source?: UsersSource; rule?: UsersQuery; openProfile?: { oneId: string; reason: string } } | null) ?? null;
  const [cat, setCat] = useState<Catalog | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [q, setQ] = useState<UsersQuery>(() => incoming?.rule ?? { ...emptyRule(), source: incoming?.source });
  const [cols, setCols] = useState<string[]>(DEFAULT_COLS);
  const [sort, setSort] = useState<{ field: string; dir: 'asc' | 'desc' } | null>(null);
  const [est, setEst] = useState<UsersEstimate | null>(null);
  const [res, setRes] = useState<UsersListResult | null>(null);
  const [cursors, setCursors] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [open, setOpen] = useState<string | null>(incoming?.openProfile?.oneId ?? null);   // 「用戶細查 / OneID」入口頁可直接帶客戶代碼開抽屜
  const [saving, setSaving] = useState<{ name: string; mode: 'manual' | 'routine' } | null>(null);
  const [exporting, setExporting] = useState(false);
  const body = (cursor?: string): UsersListRequest => ({ ...q, filters: q.filters?.length ? q.filters : undefined, behaviors: q.behaviors?.length ? q.behaviors : undefined, columns: cols, sort: sort ?? undefined, page_size: 50, cursor });
  const list = async (cursor?: string) => {
    setBusy(true); setErr(null);
    try { setRes(await api.post<UsersListResult>('/v1/users/list', body(cursor))); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); } finally { setBusy(false); }
  };
  useEffect(() => {
    api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message));
    if (can('segments', 'view')) api.get<Segment[]>('/v1/segments').then(setSegments).catch(() => undefined);
    if (incoming?.source || incoming?.rule) void list();      // 從分析落點下鑽：直接列出
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const valid = ruleValid(q);
  const estimate = async () => {
    setBusy(true); setErr(null);
    try { setEst(await api.post<UsersEstimate>('/v1/users/estimate', body())); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '估數失敗'); } finally { setBusy(false); }
  };
  const saveSegment = async () => {
    if (!saving) return;
    setBusy(true); setErr(null);
    try {
      const seg = await api.post<Segment>('/v1/segments', { name: saving.name, kind: q.source ? 'result' : 'rule', rule: { ...q, filters: q.filters?.length ? q.filters : undefined, behaviors: q.behaviors?.length ? q.behaviors : undefined }, update: { mode: saving.mode, freq: 'daily', earliest: '06:00', keep: 7 } });
      nav(`/segments/${seg.id}`);
    } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); }
  };
  const colCn = (c: string) => (c === 'one_id' ? 'one_id' : cat?.user_props.find((u) => u.key === c)?.cn ?? c);
  const colOptions = useMemo(() => (cat?.user_props ?? []).map((u) => ({ v: u.key, cn: u.cn })), [cat]);
  const segOpts = segments.filter((s) => s.status !== 'recycled').map((s) => ({ id: s.id, name: s.name }));
  return (
    <div className="page">
      <div className="page-h"><div><h1>用戶列表</h1><p>用屬性、分群、做過／未做過的行為，或從分析結果的落點圈人；即時估數（Bitmap 精確基數），分頁瀏覽最多 10 萬筆，欄位依你的角色遮罩；匯出走審批。</p></div></div>
      <div className="grid c21">
        <div className="card">
          <RuleBuilder q={q} cat={cat} segments={segOpts} onChange={(nq) => { setQ(nq); setEst(null); setRes(null); setCursors([]); }} />
          <div className="row"><button className="btn-ghost" disabled={!valid || busy} onClick={() => void estimate()}>估人數</button><button className="btn-primary" disabled={!valid || busy} onClick={() => { setCursors([]); void list(); }}>{busy ? '查詢中…' : '查詢列表'}</button>
            {res && can('segments', 'use') && <button className="btn-ghost" onClick={() => setSaving({ name: '', mode: 'manual' })}>存為分群</button>}{res && can('users', 'export') && <button className="btn-ghost" onClick={() => setExporting(true)}>匯出（審批）</button>}{res && <button className="btn-ghost" onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</button>}{err && <span className="alert">{err}</span>}</div>
          {exporting && <ExportDialog base={{ kind: 'users', rule: { ...q, filters: q.filters?.length ? q.filters : undefined, behaviors: q.behaviors?.length ? q.behaviors : undefined }, columns: cols }} onClose={() => setExporting(false)} />}
          {saving && <div className="row" style={{ background: 'var(--soft)', padding: 10, borderRadius: 10 }}><input aria-label="分群名稱" placeholder="分群名稱" value={saving.name} onChange={(e) => setSaving({ ...saving, name: e.target.value })} />
            <select value={saving.mode} aria-label="更新方式" onChange={(e) => setSaving({ ...saving, mode: e.target.value as 'manual' | 'routine' })}><option value="manual">手動更新</option><option value="routine">每天 06:00 例行更新</option></select>
            <button className="btn-primary" disabled={!saving.name.trim() || busy} onClick={() => void saveSegment()}>建立</button><button className="btn-ghost" onClick={() => setSaving(null)}>取消</button></div>}
        </div>
        <div className="stack">
          <div className="card"><div className="card-h"><h3>人數</h3>{est && <span className="sub right">{est.cost.ms} ms</span>}</div>
            <div className="tiles"><div className="tile"><span className="sub">符合條件</span><b>{est ? est.users.toLocaleString() : res?.total != null ? res.total.toLocaleString() : '—'}</b></div></div>
            <p className="note">{summarizeRule(q, cat, segOpts)}</p></div>
          <div className="card"><div className="card-h"><h3>顯示欄位與排序</h3></div>
            <div className="stack">
              <label>欄位（one_id 固定）<select multiple aria-label="顯示欄位" value={cols} onChange={(e) => setCols(Array.from(e.target.selectedOptions).map((o) => o.value))}>{colOptions.map((o) => <option key={o.v} value={o.v}>{o.cn}</option>)}</select></label>
              <div className="row" style={{ marginTop: 0 }}><select aria-label="排序欄位" value={sort?.field ?? ''} onChange={(e) => setSort(e.target.value ? { field: e.target.value, dir: sort?.dir ?? 'desc' } : null)}><option value="">預設順序</option>{colOptions.map((o) => <option key={o.v} value={o.v}>{o.cn}</option>)}</select>
                <select aria-label="排序方向" value={sort?.dir ?? 'desc'} disabled={!sort} onChange={(e) => sort && setSort({ ...sort, dir: e.target.value as 'asc' | 'desc' })}><option value="desc">由大到小</option><option value="asc">由小到大</option></select></div>
            </div></div>
        </div>
      </div>
      {res && <div style={{ marginTop: 16 }}><UsersTable res={res} colCn={colCn} busy={busy} canPrev={cursors.length > 0}
        onPrev={() => { const s = cursors.slice(0, -1); setCursors(s); void list(s[s.length - 1]); }}
        onNext={() => { if (res.next_cursor) { setCursors([...cursors, res.next_cursor]); void list(res.next_cursor); } }}
        onOpen={can('profile', 'use') ? setOpen : undefined} /></div>}
      {showSql && res && <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>SQL</h3><span className="sub">{res.lineage.join(' · ')}</span></div><pre className="code">{res.sql}</pre></div>}
      {open && <ProfileDrawer oneId={open} cat={cat} project={q.project} onClose={() => setOpen(null)} />}
    </div>
  );
}
