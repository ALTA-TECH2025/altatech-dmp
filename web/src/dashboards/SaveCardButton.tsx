// 分析頁的「存為看板卡片」：選既有（可編輯的）儀表板或新建一個 → POST /v1/dashboards[/{id}/cards]。查詢 body 原樣存（後端以建立者身分先編譯一次）。
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { defaultTitle } from './dashboards';
import type { CardChart, CardKind, Dashboard, DashboardCard, DashboardSummary } from './types';

export function SaveCardButton({ kind, query }: { kind: CardKind; query: Record<string, unknown> | null }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<DashboardSummary[] | null>(null);
  const [target, setTarget] = useState<string>('');
  const [newName, setNewName] = useState('');
  const [title, setTitle] = useState('');
  const [chart, setChart] = useState<CardChart>('auto');
  const [width, setWidth] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ id: string; name: string } | null>(null);
  if (!can('overview', 'use') || !query) return null;

  const start = async () => {
    setOpen(true); setDone(null); setErr(null); setTitle(defaultTitle(kind, query));
    try { const ds = (await api.get<DashboardSummary[]>('/v1/dashboards')).filter((d) => d.editable); setList(ds); setTarget(ds[0]?.id ?? '__new'); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取儀表板失敗'); setList([]); setTarget('__new'); }
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      let id = target; let name = list?.find((d) => d.id === id)?.name ?? '';
      if (target === '__new') { const d = await api.post<Dashboard>('/v1/dashboards', { name: newName.trim() || '我的儀表板', time: { preset: '30d' } }); id = d.id; name = d.name; }
      await api.post<DashboardCard>(`/v1/dashboards/${id}/cards`, { title: title.trim() || defaultTitle(kind, query), kind, query, chart, width });
      setDone({ id, name }); setOpen(false);
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '儲存失敗'); }
    finally { setBusy(false); }
  };
  return (
    <>
      <button className="btn-ghost" onClick={() => void start()}>存為看板卡片</button>
      {done && <span className="note" style={{ marginTop: 0 }}>已加入 <Link to={`/overview/${done.id}`}>{done.name}</Link></span>}
      {open && (
        <form className="card stack" style={{ marginTop: 10, width: '100%' }} onSubmit={submit} aria-label="存為看板卡片">
          <div className="card-h" style={{ marginBottom: 0 }}><h3>存為看板卡片</h3><button type="button" className="btn-ghost sm right" onClick={() => setOpen(false)}>關閉</button></div>
          {err && <div className="alert">{err}</div>}
          <div className="grid c3">
            <label>儀表板<select value={target} onChange={(e) => setTarget(e.target.value)}>{(list ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}（{d.cards} 張）</option>)}<option value="__new">＋ 新儀表板</option></select></label>
            {target === '__new' && <label>新儀表板名稱<input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="例：債券業務日報" required maxLength={60} /></label>}
            <label>卡片標題<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} /></label>
            {kind === 'events' && <label>圖型<select value={chart} onChange={(e) => setChart(e.target.value as CardChart)}><option value="auto">自動</option><option value="line">折線</option><option value="bar">長條</option><option value="stat">數字</option><option value="table">表格</option></select></label>}
            <label>寬度<select value={width} onChange={(e) => setWidth(Number(e.target.value) as 1 | 2)}><option value={1}>半寬</option><option value={2}>全寬</option></select></label>
          </div>
          <p className="note">卡片存的是查詢條件，不是結果：每位檢視者打開時以自己的權限重算（遮罩、行級範圍、K 匿名都照檢視者）。時間預設跟儀表板的「最近 N 天」走。</p>
          <div className="row" style={{ marginTop: 0 }}><button className="btn-primary" disabled={busy || !list}>{busy ? '儲存中…' : '儲存'}</button></div>
        </form>
      )}
    </>
  );
}
