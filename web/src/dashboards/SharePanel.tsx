// 儀表板進階（docs/30）：看板解讀（規則式）、產生 HTML 看板（分享連結、到期）、定時寄送（審批後生效）；AI 生成卡片等 AI 閘道（第二階段）。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { isDemo } from '@/demo/mode';
import type { Dashboard, Explain, RenderArtifact, Schedule } from './types';

const ROLES: [string, string][] = [['admin', '系統管理員'], ['marketing', '行銷'], ['data_analyst', '數據分析'], ['digital_ops', '數位經營'], ['it_admin', '資訊管理']];
const WD = ['週一', '週二', '週三', '週四', '週五', '週六', '週日'];
const ST: Record<Schedule['status'], [string, string]> = { pending_approval: ['待審批', 'amber'], active: ['生效中', 'blue'], paused: ['暫停', 'gray'], rejected: ['已退回', 'red'] };
const RUN: Record<string, string> = { sent: '已寄出', rendered_no_transport: '已產檔（此環境未設 SMTP，未寄信）', failed: '失敗' };
const fmt = (s: string) => s.slice(0, 16).replace('T', ' ');

export function SharePanel({ d, onClose }: { d: Dashboard; onClose: () => void }) {
  const { can } = useAuth();
  const [tab, setTab] = useState<'explain' | 'html' | 'schedule'>('explain');
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-h"><h3>看板進階</h3>
        <div className="seg">{([['explain', '看板解讀'], ['html', 'HTML 看板'], ['schedule', '定時寄送']] as const).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}</a>)}</div>
        <span className="pill gray" title="AI 閘道啟用後開放：一句話 → 在既有分析模型上做選擇 → 卡片草稿">AI 生成卡片 · 第二階段</span>
        <button className="btn-ghost sm right" onClick={onClose}>關閉</button></div>
      {tab === 'explain' && <ExplainTab d={d} />}
      {tab === 'html' && <HtmlTab d={d} canUse={can('overview', 'use')} />}
      {tab === 'schedule' && <ScheduleTab d={d} canUse={can('overview', 'use')} />}
    </div>
  );
}

function ExplainTab({ d }: { d: Dashboard }) {
  const [ex, setEx] = useState<Explain | null>(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const run = async () => { setBusy(true); setErr(null); try { setEx(await api.post<Explain>(`/v1/dashboards/${d.id}/explain`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '解讀失敗'); } finally { setBusy(false); } };
  return (
    <>
      <p className="note" style={{ marginTop: 0 }}>以你的權限重算每張卡片，用規則產生：期間前後對比、漏斗流失最多的步驟、最新同期群留存、最常見路徑、貢獻最高觸點。只讀，不改卡片。AI 閘道啟用後改由 agent 產生自然語言版並可存成洞察。</p>
      <div className="row"><button className="btn-primary" disabled={busy} onClick={() => void run()}>{busy ? '計算中…' : ex ? '重新解讀' : '解讀這個看板'}</button>{ex && <span className="sub">{ex.period} · {fmt(ex.generated_at)} · 規則式</span>}</div>
      {err && <div className="alert">{err}</div>}
      {ex && <div className="grid c21" style={{ marginTop: 12 }}>
        <div><b style={{ fontSize: 13 }}>本期重點</b><ul className="priv">{ex.summary.map((s, i) => <li key={i}>{s}</li>)}</ul></div>
        <div><b style={{ fontSize: 13 }}>每張卡片</b><ul className="priv">{ex.cards.map((c) => <li key={c.card}><span className={`signal ${c.signal}`} />{c.title}：{c.line}{c.warnings.length > 0 && <span className="sub">{c.warnings.join('；')}</span>}</li>)}</ul></div>
      </div>}
    </>
  );
}

function HtmlTab({ d, canUse }: { d: Dashboard; canUse: boolean }) {
  const [list, setList] = useState<RenderArtifact[] | null>(null); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [audience, setAudience] = useState(''); const [days, setDays] = useState(14); const [explain, setExplain] = useState(true); const [busy, setBusy] = useState(false);
  const reload = () => api.get<RenderArtifact[]>(`/v1/dashboards/${d.id}/renders`).then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, [d.id]);
  const render = async () => {
    setBusy(true); setErr(null);
    try { const a = await api.post<RenderArtifact>(`/v1/dashboards/${d.id}/render`, { audience, expires_days: days, include_explain: explain }); setMsg(`已產生：${a.cards_included} 張卡片${a.cards_skipped.length ? `，${a.cards_skipped.length} 張因含代碼化識別欄位未納入` : ''}；到期 ${a.expires_at.slice(0, 10)}`); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '產生失敗'); } finally { setBusy(false); }
  };
  const copy = (u: string) => { const full = u.startsWith('http') ? u : `${location.origin}${u}`; void navigator.clipboard?.writeText(full).then(() => setMsg('已複製分享連結')); };
  return (
    <>
      <p className="note" style={{ marginTop: 0 }}>伺服器以「你」的權限重算每張卡片，產生單檔 HTML（inline SVG、無明細與 SQL、套遮罩與 K 匿名、浮水印與到期）。分享連結不需登入即可開，到期自動失效；含代碼化識別欄位分組的卡片不會納入。</p>
      {canUse && <div className="row"><input placeholder="受眾（例：債券部週會）" value={audience} onChange={(e) => setAudience(e.target.value)} style={{ flex: '1 1 220px' }} />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 14, 30].map((x) => <option key={x} value={x}>{x} 天後到期</option>)}</select>
        <label className="sub" style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}><input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} />附本期重點</label>
        <button className="btn-primary" disabled={busy} onClick={() => void render()}>{busy ? '產生中…' : '產生 HTML 看板'}</button></div>}
      {msg && <div className="note">{msg}</div>}{err && <div className="alert">{err}</div>}
      {list && list.length > 0 && <div className="tbl-wrap" style={{ marginTop: 10 }}><table className="tbl"><thead><tr><th>產生</th><th>受眾</th><th>卡片</th><th>大小</th><th className="num">下載</th><th>到期</th><th /></tr></thead>
        <tbody>{list.map((a) => <tr key={a.artifact_id} className={a.expired ? '' : undefined}><td className="mono">{fmt(a.created_at)}<span className="sub">{a.created_by}</span></td><td>{a.audience || '—'}</td><td className="num">{a.cards_included}{a.cards_skipped.length ? ` (+${a.cards_skipped.length} 略)` : ''}</td><td className="num mono">{Math.round(a.bytes / 1024)} KB</td><td className="num">{a.downloads}</td>
          <td>{a.expired ? <span className="pill gray">已到期</span> : <span className="mono">{a.expires_at.slice(0, 10)}</span>}</td>
          <td className="num">{!a.expired && <><a className="btn-ghost sm" href={isDemo ? '/demo/d_seed_bond.html' : a.url} target="_blank" rel="noreferrer">開啟</a> <button className="btn-ghost sm" onClick={() => copy(isDemo ? '/demo/d_seed_bond.html' : a.share_url)}>複製分享連結</button></>}</td></tr>)}</tbody></table></div>}
      {list && list.length === 0 && <p className="note">還沒有產物。</p>}
    </>
  );
}

function ScheduleTab({ d, canUse }: { d: Dashboard; canUse: boolean }) {
  const [list, setList] = useState<Schedule[] | null>(null); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [weekday, setWeekday] = useState<string>('0'); const [hour, setHour] = useState(8); const [roles, setRoles] = useState<string[]>(['digital_ops']); const [emails, setEmails] = useState(''); const [days, setDays] = useState(14);
  const reload = () => api.get<Schedule[]>(`/v1/dashboards/${d.id}/schedules`).then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, [d.id]);
  const create = async () => {
    setErr(null);
    try { const s = await api.post<Schedule>(`/v1/dashboards/${d.id}/schedules`, { weekday: weekday === '' ? null : Number(weekday), hour, audience: { roles, emails: emails.split(/[,\s，、]+/).map((x) => x.trim()).filter(Boolean) }, expires_days: days }); setMsg(s.status === 'active' ? '已建立並生效' : `已送審（審批單 ${s.approval ?? '—'}）：數位經營核可後生效`); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); }
  };
  const run = async (s: Schedule) => { try { const r = await api.post<Schedule['last_run']>(`/v1/dashboards/${d.id}/schedules/${s.id}/run`); setMsg(`執行完成：${RUN[r?.status ?? ''] ?? r?.status}${r?.recipients ? `，${r.recipients} 位收件人` : ''}`); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '執行失敗'); } };
  const del = async (s: Schedule) => { if (!confirm('刪除此定時寄送？')) return; try { await api.del(`/v1/dashboards/${d.id}/schedules/${s.id}`); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '刪除失敗'); } };
  return (
    <>
      <p className="note" style={{ marginTop: 0 }}>每天或每週固定時間（台北）產生 HTML 看板並寄出分享連結；收件人為角色（成員目錄的信箱）或白名單網域信箱。建立後需數位經營審批；看板以你的權限產生。</p>
      {canUse && <div className="row">
        <select value={weekday} onChange={(e) => setWeekday(e.target.value)}><option value="">每天</option>{WD.map((w, i) => <option key={w} value={String(i)}>每{w}</option>)}</select>
        <select value={hour} onChange={(e) => setHour(Number(e.target.value))}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}</select>
        <select multiple value={roles} onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))} aria-label="收件角色" style={{ height: 70 }}>{ROLES.map(([k, cn]) => <option key={k} value={k}>{cn}</option>)}</select>
        <input placeholder="其他信箱（白名單網域，逗號分隔）" value={emails} onChange={(e) => setEmails(e.target.value)} style={{ flex: '1 1 200px' }} />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 14, 30].map((x) => <option key={x} value={x}>{x} 天到期</option>)}</select>
        <button className="btn-primary" disabled={!roles.length && !emails.trim()} onClick={() => void create()}>送審</button></div>}
      {msg && <div className="note">{msg}</div>}{err && <div className="alert">{err}</div>}
      {list && list.length > 0 && <div className="tbl-wrap" style={{ marginTop: 10 }}><table className="tbl"><thead><tr><th>時間</th><th>收件人</th><th>狀態</th><th>最近執行</th><th /></tr></thead>
        <tbody>{list.map((s) => <tr key={s.id}><td>{s.weekday == null ? '每天' : `每${WD[s.weekday]}`} {String(s.hour).padStart(2, '0')}:00<span className="sub mono">{s.id} · {s.created_by}</span></td>
          <td>{s.audience.roles.map((r) => <span key={r} className="pill blue">{ROLES.find(([k]) => k === r)?.[1] ?? r}</span>)}{s.audience.emails.map((e) => <span key={e} className="pill">{e}</span>)}</td>
          <td><span className={`pill ${ST[s.status][1]}`}>{ST[s.status][0]}</span></td>
          <td className="sub">{s.last_run ? `${fmt(s.last_run.at)} · ${RUN[s.last_run.status] ?? s.last_run.status}${s.last_run.error ? ` · ${s.last_run.error}` : ''}` : '—'}</td>
          <td className="num">{d.editable && <>{s.status === 'active' && <button className="btn-ghost sm" onClick={() => void run(s)}>立即執行</button>} <button className="btn-ghost sm danger" onClick={() => void del(s)}>刪除</button></>}</td></tr>)}</tbody></table></div>}
      {list && list.length === 0 && <p className="note">還沒有定時寄送。</p>}
    </>
  );
}
