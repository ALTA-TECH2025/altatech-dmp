// 審批與匯出：待我審批（依我的角色能審的類型）、我的申請、匯出申請（核可後產檔 → 下載 CSV）。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import type { Approval, Export } from '@/users/types';
import { APPROVAL_PILL, APPROVAL_STATUS_CN, APPROVAL_TYPE_CN, EXPORT_PILL, EXPORT_STATUS_CN, exportNext, expiresText } from './approvals';

export function ApprovalsPage() {
  return <RequireGrant module="users"><ApprovalsInner /></RequireGrant>;
}

function ApprovalsInner() {
  const { me } = useAuth();
  const [tab, setTab] = useState<'to_me' | 'mine' | 'exports'>('to_me');
  const [list, setList] = useState<Approval[]>([]);
  const [exps, setExps] = useState<Export[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    setErr(null);
    try {
      if (tab === 'exports') setExps(await api.get<Export[]>('/v1/exports'));
      else setList(await api.get<Approval[]>(`/v1/approvals?scope=${tab}`));
    } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); }
  };
  useEffect(() => { void reload(); }, [tab]);   // eslint-disable-line react-hooks/exhaustive-deps
  const decide = async (a: Approval, approve: boolean) => {
    setBusy(true); setErr(null);
    try { await api.post(`/v1/approvals/${a.id}/decide`, { approve, note: notes[a.id] ?? '' }); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '操作失敗'); } finally { setBusy(false); }
  };
  const run = async (e: Export) => { setBusy(true); setErr(null); try { await api.post(`/v1/exports/${e.id}/run`); await reload(); } catch (x) { setErr(x instanceof ApiError ? x.message : '產檔失敗'); } finally { setBusy(false); } };
  const objectLink = (a: Approval) => (a.object.kind === 'segment' ? <Link to={`/segments/${a.object.id}`} className="pill blue">分群 {a.object.id}</Link> : a.object.kind === 'export' ? <button className="pill blue" onClick={() => setTab('exports')}>匯出單 {a.object.id}</button> : <span className="pill">{a.object.kind} {a.object.id}</span>);
  return (
    <div className="page">
      <div className="page-h"><div><h1>審批與匯出</h1><p>匯出、分群生效、推送、欄位揭露、agent 提議都走審批單；類型決定審批角色（匯出與分群 = 數位經營、L3 揭露 = 系統管理員），不能審自己的申請，7 天未審自動逾期。匯出核可後 7 天內由申請者本人產檔下載，檔案帶浮水印、欄位比畫面再嚴一級。</p></div></div>
      <div className="seg">{([['to_me', '待我審批'], ['mine', '我的申請'], ['exports', '匯出申請']] as const).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
      {err && <div className="alert" style={{ marginBottom: 12 }}>{err}</div>}
      {tab !== 'exports' ? (
        <div className="card flush"><div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>類型</th><th>內容</th><th>申請者</th><th>狀態</th><th>時間</th><th>{tab === 'to_me' ? '審批' : '結果'}</th></tr></thead>
          <tbody>{list.map((a) => (
            <tr key={a.id}>
              <td><span className="pill">{APPROVAL_TYPE_CN[a.type] ?? a.type}</span></td>
              <td><b>{a.summary}</b><span className="sub">{objectLink(a)} {Object.entries(a.detail ?? {}).filter(([k]) => k !== 'rule').map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ')}</span></td>
              <td className="sub">{a.requester}</td>
              <td><span className={`pill ${APPROVAL_PILL[a.status]}`}>{APPROVAL_STATUS_CN[a.status]}</span>{a.status === 'pending' && <span className="sub">{expiresText(a.expires_at)}</span>}</td>
              <td className="sub mono">{a.created_at.slice(0, 16)}</td>
              <td>{a.status === 'pending' && tab === 'to_me' && a.requester !== me?.user.id ? (
                <div className="row" style={{ marginTop: 0, flexWrap: 'nowrap' }}><input aria-label={`意見 ${a.id}`} placeholder="意見（退回必填）" style={{ width: 160 }} value={notes[a.id] ?? ''} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} />
                  <button className="btn-primary" disabled={busy} onClick={() => void decide(a, true)}>核可</button><button className="btn-ghost danger" disabled={busy || !(notes[a.id] ?? '').trim()} onClick={() => void decide(a, false)}>退回</button></div>
              ) : <span className="sub">{a.decided_by ? `${a.decided_by} · ${a.decided_at?.slice(0, 16)}` : ''}{a.note ? ` · ${a.note}` : ''}</span>}</td>
            </tr>))}
            {!list.length && <tr><td colSpan={6} className="sub" style={{ textAlign: 'center', padding: 24 }}>沒有審批單</td></tr>}
          </tbody></table></div></div>
      ) : (
        <div className="card flush"><div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>匯出單</th><th>內容</th><th className="num">列數</th><th>狀態</th><th>申請者</th><th>動作</th></tr></thead>
          <tbody>{exps.map((e) => { const nx = exportNext(e); return (
            <tr key={e.id}>
              <td className="mono">{e.id}<span className="sub">{e.created_at.slice(0, 16)}</span></td>
              <td><b>{e.what ?? e.kind}</b><span className="sub">{e.purpose} · 保存 {e.retention_days} 天{e.masked && Object.keys(e.masked).length ? ` · 遮罩 ${Object.keys(e.masked).join('、')}` : ''}{e.watermark_id ? ` · 浮水印 ${e.watermark_id}` : ''}</span></td>
              <td className="num mono">{(e.rows ?? e.rows_estimate).toLocaleString()}</td>
              <td><span className={`pill ${EXPORT_PILL[e.status]}`}>{EXPORT_STATUS_CN[e.status]}</span>{e.expires_at && (e.status === 'approved' || e.status === 'generated') && <span className="sub">{expiresText(e.expires_at)}</span>}{e.note && <span className="sub">{e.note}</span>}</td>
              <td className="sub">{e.requester}</td>
              <td>{e.requester === me?.user.id && nx === 'run' && <button className="btn-primary" disabled={busy} onClick={() => void run(e)}>產檔</button>}
                {e.requester === me?.user.id && nx === 'download' && <a className="btn-ghost" href={`/v1/exports/${e.id}/download`}>下載 CSV</a>}
                {nx === 'wait' && <span className="sub">等待數位經營審批</span>}</td>
            </tr>); })}
            {!exps.length && <tr><td colSpan={6} className="sub" style={{ textAlign: 'center', padding: 24 }}>沒有匯出申請</td></tr>}
          </tbody></table></div></div>
      )}
    </div>
  );
}
