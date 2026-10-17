// 分析頁的「存為書籤」（神策：保存為書籤 → 添加到概覽）：命名、分享角色、順手加進一或多個看板或新建看板 → POST /v1/bookmarks。查詢本體原樣存（後端以建立者身分先編譯一次）。
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { defaultTitle } from './dashboards';
import type { Bookmark, CardChart, CardKind, DashboardSummary } from './types';

const ROLES: [string, string][] = [['marketing', '行銷'], ['data_analyst', '數據分析'], ['digital_ops', '數位經營'], ['it_admin', '資訊管理']];

export function SaveCardButton({ kind, query }: { kind: CardKind; query: Record<string, unknown> | null }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<DashboardSummary[] | null>(null);
  const [targets, setTargets] = useState<string[]>([]);
  const [newName, setNewName] = useState('');
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [roles, setRoles] = useState<string[]>([]);
  const [chart, setChart] = useState<CardChart>('auto');
  const [width, setWidth] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<Bookmark | null>(null);
  const canDash = can('overview', 'use');
  if (!query) return null;

  const start = async () => {
    setOpen(true); setDone(null); setErr(null); setName(defaultTitle(kind, query));
    if (canDash) { try { setList((await api.get<DashboardSummary[]>('/v1/dashboards')).filter((d) => d.editable)); } catch { setList([]); } } else setList([]);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const b = await api.post<Bookmark>('/v1/bookmarks', { name: name.trim() || defaultTitle(kind, query), description: desc, kind, query, chart, width, shared: { roles, users: [] }, add_to: targets.filter((t) => t !== '__new'), new_dashboard: targets.includes('__new') ? (newName.trim() || '我的儀表板') : undefined });
      setDone(b); setOpen(false);
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '儲存失敗'); }
    finally { setBusy(false); }
  };
  return (
    <>
      <button className="btn-ghost" onClick={() => void start()}>存為書籤</button>
      {done && <span className="note" style={{ marginTop: 0 }}>已存書籤 <Link to="/bookmarks">{done.name}</Link>{(done.added_to ?? []).length > 0 && <>，並加入 {done.added_to!.map((a) => <Link key={a.dashboard} to={`/overview/${a.dashboard}`} style={{ marginRight: 6 }}>{a.name}</Link>)}</>}</span>}
      {open && (
        <form className="card stack" style={{ marginTop: 10, width: '100%' }} onSubmit={submit} aria-label="存為書籤">
          <div className="card-h" style={{ marginBottom: 0 }}><h3>存為書籤</h3><span className="sub">書籤 = 這個查詢的條件；可重開、分享、放進多個看板</span><button type="button" className="btn-ghost sm right" onClick={() => setOpen(false)}>關閉</button></div>
          {err && <div className="alert">{err}</div>}
          <div className="grid c3">
            <label>書籤名稱<input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required /></label>
            <label>說明<input value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={400} placeholder="口徑、用途…" /></label>
            <label>分享給角色（選填）<select multiple value={roles} onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: 64 }}>{ROLES.map(([k, cn]) => <option key={k} value={k}>{cn}</option>)}</select></label>
            {kind === 'events' && <label>圖型<select value={chart} onChange={(e) => setChart(e.target.value as CardChart)}><option value="auto">自動</option><option value="line">折線</option><option value="bar">長條</option><option value="stat">數字</option><option value="table">表格</option></select></label>}
            {canDash && <label>加入看板（選填，可多選）<select multiple value={targets} onChange={(e) => setTargets(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: 64 }}>{(list ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}（{d.cards} 張）</option>)}<option value="__new">＋ 新建看板</option></select></label>}
            {targets.includes('__new') && <label>新看板名稱<input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="例：債券業務日報" maxLength={60} /></label>}
            {targets.length > 0 && <label>卡片寬度<select value={width} onChange={(e) => setWidth(Number(e.target.value) as 1 | 2)}><option value={1}>半寬</option><option value={2}>全寬</option></select></label>}
          </div>
          <p className="note">書籤存的是查詢條件，不是結果：每位檢視者打開時以自己的權限重算（遮罩、行級範圍、K 匿名都照檢視者）。改書籤，引用它的看板卡片一起更新。</p>
          <div className="row" style={{ marginTop: 0 }}><button className="btn-primary" disabled={busy || !list}>{busy ? '儲存中…' : '儲存書籤'}</button></div>
        </form>
      )}
    </>
  );
}
