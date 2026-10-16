// 用戶細查抽屜：先填理由（4–200 字，寫入審計）→ 畫像（屬性脫敏、近 30 天摘要、分群）→ 行為序列（倒序分頁、事件篩選）→ 身分圖（節點／邊）。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import type { IdentityGraph, Profile, TimelineResult } from './types';

type Tab = 'profile' | 'timeline' | 'identities';

export function ProfileDrawer({ oneId, cat, project, onClose }: { oneId: string; cat: Catalog | null; project?: string; onClose: () => void }) {
  const [reason, setReason] = useState('');
  const [granted, setGranted] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('profile');
  const [prof, setProf] = useState<Profile | null>(null);
  const [tl, setTl] = useState<TimelineResult | null>(null);
  const [tlCursors, setTlCursors] = useState<string[]>([]);
  const [tlEvent, setTlEvent] = useState('');
  const [ig, setIg] = useState<IdentityGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const qs = (extra: Record<string, string | number | undefined> = {}) => { const p = new URLSearchParams({ reason: granted ?? '', ...(project ? { project } : {}) }); for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== '') p.set(k, String(v)); return p.toString(); };
  const loadTl = async (cursor?: string, ev = tlEvent) => {
    setBusy(true);
    try { setTl(await api.get<TimelineResult>(`/v1/profiles/${encodeURIComponent(oneId)}/timeline?${qs({ limit: 50, cursor, events: ev })}`)); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } finally { setBusy(false); }
  };
  useEffect(() => {
    if (!granted) return;
    setBusy(true); setErr(null);
    api.get<Profile>(`/v1/profiles/${encodeURIComponent(oneId)}?${qs()}`).then(setProf).catch((e: Error) => setErr(e.message)).finally(() => setBusy(false));
  }, [granted, oneId]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!granted) return;
    if (tab === 'timeline' && !tl) void loadTl();
    if (tab === 'identities' && !ig) api.get<IdentityGraph>(`/v1/profiles/${encodeURIComponent(oneId)}/identities?${qs()}`).then(setIg).catch((e: Error) => setErr(e.message));
  }, [tab, granted]);   // eslint-disable-line react-hooks/exhaustive-deps
  const propCn = (k: string) => cat?.user_props.find((u) => u.key === k)?.cn ?? k;
  return (
    <div className="drawer-bk" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-label="用戶細查" onClick={(e) => e.stopPropagation()}>
        <div className="card-h"><h3>用戶細查</h3><span className="mono sub">{prof?.one_id ?? oneId}</span><div className="right"><button className="btn-ghost sm" onClick={onClose}>關閉</button></div></div>
        {!granted ? (
          <div className="stack">
            <p className="note">細查單一客戶需填寫理由（客訴單號、調查目的等）；理由與你的帳號會寫入審計，one_id 只記雜湊。</p>
            <label>理由<input aria-label="細查理由" value={reason} placeholder="例：客訴單 C-1001 查詢交易序列" onChange={(e) => setReason(e.target.value)} /></label>
            <div className="row" style={{ marginTop: 0 }}><button className="btn-primary" disabled={reason.trim().length < 4 || reason.length > 200} onClick={() => setGranted(reason.trim())}>進入細查</button></div>
          </div>
        ) : (
          <>
            <div className="seg" role="tablist">{(['profile', 'timeline', 'identities'] as Tab[]).map((t) => <a key={t} role="tab" href="#" className={tab === t ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(t); }}>{{ profile: '畫像', timeline: '行為序列', identities: '身分圖' }[t]}</a>)}</div>
            {err && <div className="alert">{err}</div>}
            {busy && !prof && <div className="loading">讀取中…</div>}
            {tab === 'profile' && prof && (
              <div className="stack">
                <div className="tiles"><div className="tile"><span className="sub">近 {prof.summary.days} 天事件</span><b>{prof.summary.total.toLocaleString()}</b></div><div className="tile"><span className="sub">分群</span><b style={{ fontSize: 14 }}>{prof.segments.length ? prof.segments.join('、') : '—'}</b></div></div>
                <div className="tbl-wrap"><table className="tbl"><thead><tr><th>屬性</th><th>值</th></tr></thead><tbody>
                  {Object.entries(prof.props).map(([k, v]) => <tr key={k}><td>{propCn(k)}{prof.masked[k] && <span className="sub">遮罩</span>}</td><td className="mono">{v === null || v === undefined ? '—' : typeof v === 'boolean' ? (v ? '是' : '否') : String(v)}</td></tr>)}
                </tbody></table></div>
                <div className="tbl-wrap"><table className="tbl"><thead><tr><th>事件</th><th className="num">次數</th><th>首次</th><th>最近</th></tr></thead><tbody>
                  {prof.summary.events.map((e) => <tr key={e.event}><td>{e.cn}<span className="sub mono">{e.event}</span></td><td className="num mono">{e.n}</td><td className="mono sub">{e.first}</td><td className="mono sub">{e.last}</td></tr>)}
                  {!prof.summary.events.length && <tr><td colSpan={4} className="sub">期間內沒有事件</td></tr>}
                </tbody></table></div>
                {prof.warnings.length > 0 && <p className="note">{prof.warnings.join('；')}</p>}
              </div>
            )}
            {tab === 'timeline' && (
              <div className="stack">
                <div className="row" style={{ marginTop: 0 }}>
                  <select value={tlEvent} aria-label="序列事件篩選" onChange={(e) => { setTlEvent(e.target.value); setTlCursors([]); void loadTl(undefined, e.target.value); }}><option value="">全部事件</option>{cat?.events.map((ev) => <option key={ev.name} value={ev.name}>{ev.cn}</option>)}</select>
                  <span className="sub">近 90 天 · 時間為 UTC{tl?.masked.amount ? ' · 金額已區間化' : ''}</span>
                  <div className="right"><button className="btn-ghost sm" disabled={!tlCursors.length || busy} onClick={() => { const s = tlCursors.slice(0, -1); setTlCursors(s); void loadTl(s[s.length - 1]); }}>上一頁</button> <button className="btn-ghost sm" disabled={!tl?.next_cursor || busy} onClick={() => { if (tl?.next_cursor) { setTlCursors([...tlCursors, tl.next_cursor]); void loadTl(tl.next_cursor); } }}>下一頁</button></div>
                </div>
                <ol className="timeline">{(tl?.rows ?? []).map((r) => (
                  <li key={r.event_id}><span className="mono sub">{r.time}</span><b>{r.cn}</b><span className="sub mono">{r.event}</span>
                    <span className="sub">{[r.platform, r.channel, r.symbol, r.side, r.amount !== null && r.amount !== undefined ? `金額 ${r.amount}` : '', r.entity_id ? `ID ${r.entity_id}` : ''].filter(Boolean).join(' · ')}{Object.keys(r.props).length ? ` · ${Object.entries(r.props).map(([k, v]) => `${k}=${String(v)}`).join(' ')}` : ''}</span></li>))}
                  {tl && !tl.rows.length && <li className="sub">沒有事件</li>}
                </ol>
              </div>
            )}
            {tab === 'identities' && ig && (
              <div className="stack">
                <div className="row" style={{ marginTop: 0 }}>{ig.nodes.map((n) => <span key={n.id} className="pill"><span className="sub">{n.type}</span> {n.value}</span>)}{!ig.nodes.length && <span className="sub">沒有身分邊</span>}</div>
                <div className="tbl-wrap"><table className="tbl"><thead><tr><th>A</th><th>B</th><th>來源</th><th>證據</th><th className="num">次數</th><th className="num">信心</th><th>狀態</th><th>首次／末次</th></tr></thead><tbody>
                  {ig.edges.map((e, i) => <tr key={i}><td className="mono">{e.a}</td><td className="mono">{e.b}</td><td>{e.source}</td><td className="mono sub">{e.evidence_event_id}</td><td className="num mono">{e.seen_count}</td><td className="num mono">{e.confidence}</td><td><span className={`pill ${e.status === 'active' ? 'blue' : e.status === 'conflict' ? 'red' : 'gray'}`}>{e.status}</span></td><td className="mono sub">{e.first_seen}<br />{e.last_seen}</td></tr>)}
                </tbody></table></div>
                {ig.masked.id && <p className="note">ID 值依你的角色遮罩（{ig.masked.id}）；揭露需走審批。</p>}
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
