// 書籤：我的 + 分享給我的；依分析類型篩選；在分析頁開啟（帶查詢重算）、加入看板、改名／說明／分享、刪除；顯示被哪些看板引用。
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { KIND_CN } from './dashboards';
import type { Bookmark, CardKind, DashboardSummary } from './types';

const PATH: Record<CardKind, string> = { events: '/events', funnel: '/funnel', retention: '/retention', path: '/path', attribution: '/attribution' };
const ROLES: Record<string, string> = { admin: '系統管理員', marketing: '行銷', data_analyst: '數據分析', digital_ops: '數位經營', it_admin: '資訊管理' };

export function BookmarksPage() {
  return <RequireGrant module="overview"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [list, setList] = useState<Bookmark[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [kind, setKind] = useState<'' | CardKind>('');
  const [q, setQ] = useState('');
  const [pick, setPick] = useState<Bookmark | null>(null);
  const [dash, setDash] = useState<DashboardSummary[] | null>(null);
  const [target, setTarget] = useState('');
  const reload = () => api.get<Bookmark[]>('/v1/bookmarks').then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, []);
  const rows = useMemo(() => (list ?? []).filter((b) => (!kind || b.kind === kind) && (!q || b.name.includes(q) || b.description.includes(q))), [list, kind, q]);
  const openPicker = async (b: Bookmark) => { setPick(b); const ds = (await api.get<DashboardSummary[]>('/v1/dashboards')).filter((d) => d.editable); setDash(ds); setTarget(ds[0]?.id ?? ''); };
  const addTo = async () => {
    if (!pick || !target) return;
    try { await api.post(`/v1/dashboards/${target}/cards`, { bookmark: pick.id }); setMsg(`已把「${pick.name}」加入看板`); setPick(null); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '加入失敗'); }
  };
  const rename = async (b: Bookmark) => { const n = prompt('書籤名稱', b.name); if (!n?.trim()) return; try { await api.put(`/v1/bookmarks/${b.id}`, { name: n.trim() }); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '更新失敗'); } };
  const del = async (b: Bookmark) => { if (!confirm(`刪除書籤「${b.name}」？引用它的看板卡片會保留刪除前的查詢。`)) return; try { await api.del(`/v1/bookmarks/${b.id}`); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '刪除失敗'); } };
  const mine = rows.filter((b) => b.mine); const shared = rows.filter((b) => !b.mine);
  return (
    <div className="page">
      <div className="page-h"><div><h1>書籤</h1><p>分析頁的查詢「存為書籤」後在這裡管理：重新開啟、分享、加入一或多個看板；書籤改了，引用它的看板卡片一起更新。每位檢視者打開時以自己的權限重算。</p></div>
        <div className="right row"><Link className="btn-ghost" to="/overview">看板</Link></div></div>
      {err && <div className="alert">{err}</div>}{msg && <div className="note">{msg}</div>}
      <div className="row" style={{ marginBottom: 12 }}>
        <input placeholder="搜尋名稱／說明" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: '1 1 220px' }} />
        <div className="seg"><a href="#" className={kind === '' ? 'on' : ''} onClick={(e) => { e.preventDefault(); setKind(''); }}>全部 {list?.length ?? 0}</a>{(Object.keys(KIND_CN) as CardKind[]).map((k) => <a key={k} href="#" className={kind === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setKind(k); }}>{KIND_CN[k]} {(list ?? []).filter((b) => b.kind === k).length}</a>)}</div>
      </div>
      {pick && <div className="card" style={{ marginBottom: 12 }}><div className="card-h"><h3>把「{pick.name}」加入看板</h3><button className="btn-ghost sm right" onClick={() => setPick(null)}>關閉</button></div>
        <div className="row"><select value={target} onChange={(e) => setTarget(e.target.value)}>{(dash ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}（{d.cards} 張）</option>)}</select><button className="btn-primary" disabled={!target} onClick={() => void addTo()}>加入</button>{dash && dash.length === 0 && <span className="sub">沒有可編輯的看板，先到<Link to="/overview">總覽</Link>建一個。</span>}</div></div>}
      {[['我的書籤', mine], ['分享給我的', shared]].map(([h, bs]) => (
        <div className="card" style={{ marginBottom: 16 }} key={h as string}>
          <div className="card-h"><h3>{h as string}</h3><span className="sub">{(bs as Bookmark[]).length} 個</span></div>
          {list === null ? <p className="note">載入中…</p> : (bs as Bookmark[]).length === 0 ? <p className="note">{h === '我的書籤' ? '還沒有書籤。到事件／漏斗／留存／路徑／歸因分析查詢後按「存為書籤」。' : '沒有人分享書籤給你。'}</p> : (
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>書籤</th><th>類型</th><th>分享</th><th>用於看板</th><th>更新</th><th /></tr></thead>
              <tbody>{(bs as Bookmark[]).map((b) => (
                <tr key={b.id}>
                  <td><b>{b.name}</b>{b.description && <span className="sub">{b.description}</span>}<span className="sub mono">{b.id} · {b.owner}</span></td>
                  <td><span className="pill blue">{KIND_CN[b.kind]}</span></td>
                  <td>{b.shared.roles.map((r) => <span key={r} className="pill">{ROLES[r] ?? r}</span>)}{b.shared.users.length > 0 && <span className="pill">{b.shared.users.length} 位</span>}{!b.shared.roles.length && !b.shared.users.length && <span className="sub">私有</span>}</td>
                  <td>{b.used_in.length ? b.used_in.map((u) => <Link key={u.dashboard} to={`/overview/${u.dashboard}`} className="pill custom">{u.name}</Link>) : <span className="sub">—</span>}</td>
                  <td className="mono">{b.updated_at.slice(0, 16).replace('T', ' ')}</td>
                  <td className="num"><button className="btn-ghost sm" onClick={() => nav(PATH[b.kind], { state: { bookmark: b } })}>開啟</button> {can('overview', 'use') && <button className="btn-ghost sm" onClick={() => void openPicker(b)}>加入看板</button>} {b.editable && <><button className="btn-ghost sm" onClick={() => void rename(b)}>改名</button> <button className="btn-ghost sm danger" onClick={() => void del(b)}>刪除</button></>}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </div>
      ))}
    </div>
  );
}
