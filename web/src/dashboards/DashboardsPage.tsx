// 總覽／儀表板：/overview 列表（我的、分享給我的）與新增；/overview/:id 卡片網格（每張以檢視者身分重算）、區間切換、分享、改名、移除卡片、刪除、拖曳排序與寬度；看板進階（解讀／HTML 看板／定時寄送）。
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { CardView } from './CardView';
import { SharePanel } from './SharePanel';
import { KIND_CN, PRESET_CN } from './dashboards';
import type { Dashboard, DashboardSummary, Preset } from './types';

const ROLES: [string, string][] = [['admin', '系統管理員'], ['marketing', '行銷'], ['data_analyst', '數據分析'], ['digital_ops', '數位經營'], ['it_admin', '資訊管理']];

export function DashboardsPage() {
  const { id } = useParams();
  return <RequireGrant module="overview">{id ? <DashboardDetail id={id} /> : <DashboardList />}</RequireGrant>;
}

function DashboardList() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [list, setList] = useState<DashboardSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [preset, setPreset] = useState<Preset>('30d');
  const load = () => api.get<DashboardSummary[]>('/v1/dashboards').then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void load(); }, []);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    try { const d = await api.post<Dashboard>('/v1/dashboards', { name: name.trim(), time: { preset } }); nav(`/overview/${d.id}`); }
    catch (ex) { setErr(ex instanceof ApiError ? ex.message : '建立失敗'); }
  };
  const mine = list?.filter((d) => d.mine) ?? []; const shared = list?.filter((d) => !d.mine) ?? [];
  return (
    <div className="page">
      <div className="page-h"><div><h1>總覽</h1><p>把事件、漏斗、留存、路徑、歸因的查詢存成卡片組成儀表板。卡片存的是條件：每位檢視者打開時以自己的權限重算，分享不會繞過遮罩與行級範圍。</p></div></div>
      {err && <div className="alert">{err}</div>}
      {can('overview', 'use') && (
        <form className="card" onSubmit={create} aria-label="新增儀表板">
          <div className="card-h"><h3>新增儀表板</h3></div>
          <div className="row" style={{ marginTop: 0 }}>
            <input placeholder="名稱，例：債券業務日報" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} className="grow" />
            <select value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>{(Object.keys(PRESET_CN) as Preset[]).map((p) => <option key={p} value={p}>{PRESET_CN[p]}</option>)}</select>
            <button className="btn-primary" disabled={!name.trim()}>建立</button>
          </div>
          <p className="note">到各分析頁查詢後按「存為看板卡片」即可加卡片。</p>
        </form>
      )}
      {[['我的儀表板', mine], ['分享給我的', shared]].map(([h, ds]) => (
        <div className="card" style={{ marginTop: 16 }} key={h as string}>
          <div className="card-h"><h3>{h as string}</h3><span className="sub">{(ds as DashboardSummary[]).length} 個</span></div>
          {list === null ? <p className="note">載入中…</p> : (ds as DashboardSummary[]).length === 0 ? <p className="note">{h === '我的儀表板' ? '還沒有儀表板。' : '沒有人分享儀表板給你。'}</p> : (
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>名稱</th><th>卡片</th><th>區間</th><th>擁有者</th><th>分享</th><th>更新</th></tr></thead>
              <tbody>{(ds as DashboardSummary[]).map((d) => (
                <tr key={d.id} className="clickable" onClick={() => nav(`/overview/${d.id}`)}>
                  <td><Link to={`/overview/${d.id}`} onClick={(e) => e.stopPropagation()}>{d.name}</Link>{d.seed && <span className="pill gray" style={{ marginLeft: 6 }}>範例</span>}{d.description && <span className="sub">{d.description}</span>}</td>
                  <td className="num mono">{d.cards}</td><td>{PRESET_CN[d.time.preset]}</td><td className="mono">{d.owner}</td>
                  <td>{d.shared.roles.map((r) => <span className="pill blue" key={r}>{ROLES.find(([k]) => k === r)?.[1] ?? r}</span>)}{d.shared.users.map((u) => <span className="pill" key={u}>{u}</span>)}</td>
                  <td className="mono">{d.updated_at.slice(0, 16).replace('T', ' ')}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </div>
      ))}
    </div>
  );
}

function DashboardDetail({ id }: { id: string }) {
  const { me } = useAuth();
  const nav = useNavigate();
  const [d, setD] = useState<Dashboard | null>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [share, setShare] = useState(false);
  const [adv, setAdv] = useState(false);
  const [roles, setRoles] = useState<string[]>([]); const [users, setUsers] = useState('');
  const [drag, setDrag] = useState<string | null>(null); const [over, setOver] = useState<string | null>(null);
  useEffect(() => { api.get<Dashboard>(`/v1/dashboards/${id}`).then((x) => { setD(x); setRoles(x.shared.roles); setUsers(x.shared.users.join(', ')); }).catch((e: Error) => setErr(e.message)); }, [id]);
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch(() => setCat(null)); }, []);
  const labelOf = useMemo(() => { const m = new Map((cat?.events ?? []).map((e) => [e.name, e.cn])); return (n: string) => m.get(n) ?? n; }, [cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const put = async (body: Record<string, unknown>) => { try { setD(await api.put<Dashboard>(`/v1/dashboards/${id}`, body)); setErr(null); } catch (e) { setErr(e instanceof ApiError ? e.message : '更新失敗'); } };
  if (err && !d) return <div className="page"><div className="alert">{err}</div><p className="note"><Link to="/overview">回總覽</Link></p></div>;
  if (!d) return <div className="page"><p className="note">載入中…</p></div>;
  return (
    <div className="page">
      <div className="page-h">
        <div><h1>{d.name}</h1><p>{d.description || `擁有者 ${d.owner}`}{d.shared.roles.length > 0 && ` · 分享給 ${d.shared.roles.map((r) => ROLES.find(([x]) => x === r)?.[1] ?? r).join('、')}`}{d.shared.users.length > 0 && ` · ${d.shared.users.length} 位使用者`}</p></div>
        <div className="row" style={{ marginTop: 0 }}>
          <select value={d.time.preset} aria-label="區間" onChange={(e) => (d.editable ? void put({ time: { preset: e.target.value } }) : setD({ ...d, time: { preset: e.target.value as Preset } }))}>{(Object.keys(PRESET_CN) as Preset[]).map((p) => <option key={p} value={p}>{PRESET_CN[p]}</option>)}</select>
          <button className="btn-ghost" onClick={() => setReload((n) => n + 1)}>重新整理</button>
          {d.editable && <>
            <button className="btn-ghost" onClick={() => { const n = prompt('儀表板名稱', d.name); if (n && n.trim()) void put({ name: n.trim() }); }}>改名</button>
            <button className="btn-ghost" onClick={() => setShare((v) => !v)}>分享</button>
            <button className="btn-ghost danger" onClick={() => { if (confirm(`刪除「${d.name}」？卡片會一起刪除。`)) api.del(`/v1/dashboards/${id}`).then(() => nav('/overview')).catch((e: Error) => setErr(e.message)); }}>刪除</button>
          </>}
          <button className="btn-ghost" onClick={() => setAdv((v) => !v)}>解讀 / HTML 看板 / 定時寄送</button>
          <Link className="btn-ghost" to="/overview">所有儀表板</Link>
        </div>
      </div>
      {err && <div className="alert">{err}</div>}
      {adv && <SharePanel d={d} onClose={() => setAdv(false)} />}
      {share && d.editable && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-h"><h3>分享</h3><span className="sub">被分享者只能檢視；卡片資料以他們自己的權限重算</span></div>
          <div className="grid c2">
            <label>角色<select multiple value={roles} onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))}>{ROLES.map(([k2, cn]) => <option key={k2} value={k2}>{cn}</option>)}</select></label>
            <label>使用者 ID（逗號分隔，例：local:ab12…、entra:oid）<input value={users} onChange={(e) => setUsers(e.target.value)} /></label>
          </div>
          <div className="row"><button className="btn-primary" onClick={() => void put({ shared: { roles, users: users.split(/[,\s，、]+/).map((x) => x.trim()).filter(Boolean) } }).then(() => setShare(false))}>儲存分享設定</button></div>
        </div>
      )}
      {d.cards.length === 0 ? <div className="card"><p className="note">還沒有卡片。到<Link to="/events">事件分析</Link>、<Link to="/funnel">漏斗</Link>、<Link to="/retention">留存</Link>、<Link to="/path">路徑</Link>或<Link to="/attribution">歸因</Link>查詢後按「存為看板卡片」。</p></div> : (
        <div className="dash-grid">
          {d.cards.map((c) => (
            <CardView key={c.id} dash={d} card={c} k={k} labelOf={labelOf} reloadKey={reload}
              onRename={d.editable ? () => { const t = prompt('卡片標題', c.title); if (t && t.trim()) void put({ cards: d.cards.map((x) => (x.id === c.id ? { id: x.id, title: t.trim() } : { id: x.id })) }); } : undefined}
              onWidth={d.editable ? () => void put({ cards: d.cards.map((x) => ({ id: x.id, ...(x.id === c.id ? { width: x.width === 2 ? 1 : 2 } : {}) })) }) : undefined}
              onRemove={d.editable ? () => { if (confirm(`移除卡片「${c.title}」？`)) api.del(`/v1/dashboards/${id}/cards/${c.id}`).then(() => setD({ ...d, cards: d.cards.filter((x) => x.id !== c.id) })).catch((e: Error) => setErr(e.message)); } : undefined}
              rootProps={d.editable ? {
                draggable: true, title: '拖曳可排序',
                onDragStart: (e) => { setDrag(c.id); e.dataTransfer.effectAllowed = 'move'; },
                onDragOver: (e) => { e.preventDefault(); if (over !== c.id) setOver(c.id); },
                onDragLeave: () => setOver((o) => (o === c.id ? null : o)),
                onDrop: (e) => { e.preventDefault(); const from = drag; setDrag(null); setOver(null); if (!from || from === c.id) return; const ids = d.cards.map((x) => x.id); const next = ids.filter((x) => x !== from); next.splice(next.indexOf(c.id), 0, from); void put({ cards: next.map((x) => ({ id: x })) }); },
                onDragEnd: () => { setDrag(null); setOver(null); },
                className: `card dash-card w${c.width}${drag === c.id ? ' dragging' : ''}${over === c.id && drag !== c.id ? ' dropzone' : ''}`,
              } : undefined} />
          ))}
        </div>
      )}
      <p className="note" style={{ marginTop: 12 }}>卡片種類：{d.cards.map((c) => KIND_CN[c.kind]).filter((v, i, a) => a.indexOf(v) === i).join('、') || '—'}；每張卡片的 SQL 與血緣可按「SQL」查看，查詢都寫入審計。{d.editable && '拖曳卡片可排序，「全寬／½ 寬」切換寬度。'}</p>
    </div>
  );
}
