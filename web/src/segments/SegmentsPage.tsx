// 分群：列表（搜尋、狀態、回收站）／新建與編輯（規則 = 用戶列表同一套條件、更新方式）／詳情（狀態與審批、最新版本、歷史版本、成員、預覽畫像、血緣、動作）。
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { ExportDialog } from '@/users/ExportDialog';
import { ProfileDrawer } from '@/users/ProfileDrawer';
import { RuleBuilder } from '@/users/RuleBuilder';
import { emptyRule, ruleValid, summarizeRule } from '@/users/rule';
import type { Segment, SegmentCreate, SegmentPreview, SegmentStatus, SegmentVersion, UsersEstimate, UsersListResult } from '@/users/types';
import { UsersTable } from '@/users/UsersTable';
import { ACTION_CN, STATUS_CN, STATUS_PILL, TRIGGER_CN, actionsFor, updateText, type SegAction } from './segments';

export function SegmentsPage() {
  return <RequireGrant module="segments"><SegmentsInner /></RequireGrant>;
}

type Draft = SegmentCreate & { update: NonNullable<SegmentCreate['update']> };
const newDraft = (): Draft => ({ name: '', description: '', kind: 'rule', rule: emptyRule(), update: { mode: 'manual', freq: 'daily', earliest: '06:00', keep: 7 }, tz: 'Asia/Taipei' });

function SegmentsInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const { id } = useParams();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [list, setList] = useState<Segment[]>([]);
  const [tab, setTab] = useState<'all' | 'pending' | 'recycled'>('all');
  const [search, setSearch] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    const live = await api.get<Segment[]>('/v1/segments'); const rec = await api.get<Segment[]>('/v1/segments?status=recycled');
    setList([...live, ...rec]);
  };
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); reload().catch((e: Error) => setErr(e.message)); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const shown = list.filter((s) => (tab === 'recycled' ? s.status === 'recycled' : tab === 'pending' ? s.status === 'pending_approval' : s.status !== 'recycled') && (!search || s.name.includes(search) || (s.description ?? '').includes(search)));
  const segOpts = list.filter((s) => s.status !== 'recycled').map((s) => ({ id: s.id, name: s.name }));
  const current = id && id !== 'new' ? list.find((s) => s.id === id) ?? null : null;
  return (
    <div className="page">
      <div className="page-h"><div><h1>分群</h1><p>規則分群（屬性、分群、做過／未做過、分析落點）以 Bitmap 計算存成版本；行銷建立的分群需數位經營審批後生效；例行分群依頻率與最早計算時間自動更新並保留版本。</p></div>
        {can('segments', 'use') && <div className="right"><Link className="btn-primary" to="/segments/new">＋ 新建分群</Link></div>}</div>
      {err && <div className="alert" style={{ marginBottom: 12 }}>{err}</div>}
      <div className={current || id === 'new' ? 'stack' : 'grid c21'}>
        <div className="stack">
          {id === 'new' ? <SegmentForm cat={cat} segOpts={segOpts} initial={newDraft()} onDone={async (s) => { await reload(); nav(`/segments/${s.id}`); }} onCancel={() => nav('/segments')} />
            : current ? <SegmentDetail seg={current} cat={cat} segOpts={segOpts} me={me?.user.id} can={can} onChanged={reload} busy={busy} setBusy={setBusy} setErr={setErr} />
            : (
              <div className="card flush">
                <div className="row" style={{ padding: 12, marginTop: 0 }}>
                  <div className="seg" style={{ marginBottom: 0 }}>{([['all', '全部'], ['pending', '待審批'], ['recycled', '回收站']] as const).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}{k === 'pending' && list.some((s) => s.status === 'pending_approval') ? ` (${list.filter((s) => s.status === 'pending_approval').length})` : ''}</a>)}</div>
                  <input aria-label="搜尋分群" placeholder="搜尋名稱／說明" value={search} onChange={(e) => setSearch(e.target.value)} />
                </div>
                <div className="tbl-wrap"><table className="tbl">
                  <thead><tr><th>分群</th><th>狀態</th><th className="num">人數</th><th>更新</th><th>最近計算</th><th>建立者</th></tr></thead>
                  <tbody>{shown.map((s) => (
                    <tr key={s.id} className="clickable" onClick={() => nav(`/segments/${s.id}`)}>
                      <td><b>{s.name}</b><span className="sub">{s.kind === 'result' ? '結果保存 · ' : ''}{summarizeRule(s.rule, cat, segOpts).slice(0, 80)}</span></td>
                      <td><span className={`pill ${STATUS_PILL[s.status]}`}>{STATUS_CN[s.status]}</span>{s.latest?.stale && <span className="pill amber">規則已改</span>}</td>
                      <td className="num mono">{s.latest?.users != null ? s.latest.users.toLocaleString() : '—'}</td>
                      <td className="sub">{updateText(s.update, s.tz)}</td>
                      <td className="sub mono">{s.latest ? `${s.latest.basetime} v${s.latest.version} ${s.latest.status === 'failed' ? '失敗' : ''}` : '尚未計算'}</td>
                      <td className="sub">{s.owner}</td>
                    </tr>))}
                    {!shown.length && <tr><td colSpan={6} className="sub" style={{ textAlign: 'center', padding: 24 }}>沒有分群</td></tr>}
                  </tbody></table></div>
              </div>
            )}
        </div>
        {!current && id !== 'new' && <div className="card"><div className="card-h"><h3>說明</h3></div><ul className="priv">
          <li>規則與用戶列表同一套條件；「完成 A 未完成 B」= 做過 A 且 未做過 B。</li>
          <li>人數預估是精確的 Bitmap 基數；計算會把名單寫成版本（手動 1 版、例行留 N 版），查詢端引用分群時取最新版本。</li>
          <li>結果保存分群（從漏斗流失、留存某期等落點建立）不可改規則；手動分群不可改更新方式；例行分群不可改頻率。</li>
          <li>回收站保留 15 天後徹底刪除；有下游引用時需確認。所有動作寫審計。</li></ul></div>}
      </div>
    </div>
  );
}

function SegmentForm({ cat, segOpts, initial, existing, onDone, onCancel }: { cat: Catalog | null; segOpts: { id: string; name: string }[]; initial: Draft; existing?: Segment; onDone: (s: Segment) => Promise<void> | void; onCancel: () => void }) {
  const [d, setD] = useState<Draft>(initial);
  const [est, setEst] = useState<UsersEstimate | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const clean = (): SegmentCreate => ({ ...d, rule: { ...d.rule, filters: d.rule.filters?.length ? d.rule.filters : undefined, behaviors: d.rule.behaviors?.length ? d.rule.behaviors : undefined }, update: { ...d.update, keep: d.update.mode === 'manual' ? 1 : d.update.keep } });
  const estimate = async () => { setBusy(true); setErr(null); try { setEst(await api.post<UsersEstimate>('/v1/segments/estimate', { rule: clean().rule })); } catch (e) { setErr(e instanceof ApiError ? e.message : '估數失敗'); } finally { setBusy(false); } };
  const save = async () => {
    setBusy(true); setErr(null);
    try { const s = existing ? await api.put<Segment>(`/v1/segments/${existing.id}`, clean()) : await api.post<Segment>('/v1/segments', clean()); await onDone(s); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '儲存失敗'); } finally { setBusy(false); }
  };
  const ro = existing?.kind === 'result';
  return (
    <div className="card" style={{ maxWidth: 960 }}>
      <div className="card-h"><h3>{existing ? '編輯分群' : '新建分群'}</h3></div>
      <div className="grid c2"><label>名稱<input aria-label="分群名稱" value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} /></label><label>說明<input value={d.description ?? ''} onChange={(e) => setD({ ...d, description: e.target.value })} /></label></div>
      <div style={{ marginTop: 14 }}><RuleBuilder q={d.rule} cat={cat} segments={segOpts.filter((s) => s.id !== existing?.id)} readOnly={ro} onChange={(r) => { setD({ ...d, rule: r }); setEst(null); }} /></div>
      <div className="grid c3" style={{ marginTop: 14 }}>
        <label>更新方式<select aria-label="更新方式" value={d.update.mode} disabled={!!existing} onChange={(e) => setD({ ...d, update: { ...d.update, mode: e.target.value as 'manual' | 'routine' } })}><option value="manual">手動（1 版）</option><option value="routine">例行</option></select></label>
        <label>頻率<select aria-label="頻率" value={d.update.freq} disabled={d.update.mode === 'manual' || !!existing} onChange={(e) => setD({ ...d, update: { ...d.update, freq: e.target.value as 'daily' } })}><option value="daily">每天</option><option value="weekly">每週一</option><option value="monthly">每月 1 日</option></select></label>
        <label>最早計算時間<input type="time" aria-label="最早計算時間" value={d.update.earliest} disabled={d.update.mode === 'manual'} onChange={(e) => setD({ ...d, update: { ...d.update, earliest: e.target.value } })} /></label>
        <label>保留版本數<input type="number" min={1} max={100} value={d.update.mode === 'manual' ? 1 : d.update.keep} disabled={d.update.mode === 'manual'} onChange={(e) => setD({ ...d, update: { ...d.update, keep: Number(e.target.value) || 1 } })} /></label>
        <label>時區<input value={d.tz ?? 'Asia/Taipei'} onChange={(e) => setD({ ...d, tz: e.target.value })} /></label>
      </div>
      <div className="row"><button className="btn-ghost" disabled={!ruleValid(d.rule) || busy} onClick={() => void estimate()}>估人數{est ? `：${est.users.toLocaleString()}` : ''}</button>
        <button className="btn-primary" disabled={!d.name.trim() || !ruleValid(d.rule) || busy} onClick={() => void save()}>{busy ? '儲存中…' : existing ? '儲存' : '建立'}</button><button className="btn-ghost" onClick={onCancel}>取消</button>{err && <span className="alert">{err}</span>}</div>
      <p className="note">{summarizeRule(d.rule, cat, segOpts)}</p>
    </div>
  );
}

function SegmentDetail({ seg, cat, segOpts, me, can, onChanged, busy, setBusy, setErr }: { seg: Segment; cat: Catalog | null; segOpts: { id: string; name: string }[]; me?: string; can: Parameters<typeof actionsFor>[1]; onChanged: () => Promise<void>; busy: boolean; setBusy: (b: boolean) => void; setErr: (e: string | null) => void }) {
  const nav = useNavigate();
  const [editing, setEditing] = useState(false);
  const [versions, setVersions] = useState<SegmentVersion[]>([]);
  const [preview, setPreview] = useState<SegmentPreview | null>(null);
  const [members, setMembers] = useState<UsersListResult | null>(null);
  const [cursors, setCursors] = useState<string[]>([]);
  const [lineage, setLineage] = useState<{ up: string[]; down: string[] } | null>(null);
  const [note, setNote] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  useEffect(() => { setEditing(false); setPreview(null); setMembers(null); setCursors([]); api.get<SegmentVersion[]>(`/v1/segments/${seg.id}/versions`).then(setVersions).catch(() => setVersions([])); api.get<{ up: string[]; down: string[] }>(`/v1/segments/${seg.id}/lineage`).then(setLineage).catch(() => undefined); }, [seg.id, seg.updated_at]);
  const act = async (a: SegAction, body?: unknown) => {
    setBusy(true); setErr(null);
    try {
      if (a === 'edit') { setEditing(true); return; }
      if (a === 'purge') { if (!window.confirm('徹底刪除後不可恢復，成員名單一併清除。確定？')) return; await api.del(`/v1/segments/${seg.id}/purge`); nav('/segments'); await onChanged(); return; }
      if (a === 'recycle') { try { await api.post(`/v1/segments/${seg.id}/recycle`); } catch (e) { if (e instanceof ApiError && e.status === 409 && window.confirm(`${e.message}\n仍要移入回收站？`)) await api.post(`/v1/segments/${seg.id}/recycle?force=true`); else throw e; } }
      else if (a === 'copy') { const s = await api.post<Segment>(`/v1/segments/${seg.id}/copy`, {}); await onChanged(); nav(`/segments/${s.id}`); return; }
      else await api.post(`/v1/segments/${seg.id}/${a === 'resume' ? 'pause' : a}`, body ?? {});
      await onChanged();
    } catch (e) { setErr(e instanceof ApiError ? e.message : '操作失敗'); } finally { setBusy(false); }
  };
  const loadMembers = async (cursor?: string) => { setBusy(true); try { setMembers(await api.get<UsersListResult>(`/v1/segments/${seg.id}/versions/latest/members?page_size=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); } finally { setBusy(false); } };
  const loadPreview = async () => { setBusy(true); try { setPreview(await api.post<SegmentPreview>(`/v1/segments/${seg.id}/preview`)); } catch (e) { setErr(e instanceof ApiError ? e.message : '預覽失敗'); } finally { setBusy(false); } };
  const actions = actionsFor(seg, can, me);
  const colCn = (c: string) => (c === 'one_id' ? 'one_id' : cat?.user_props.find((u) => u.key === c)?.cn ?? c);
  const propCn = useMemo(() => Object.fromEntries((cat?.user_props ?? []).map((u) => [u.key, u.cn])), [cat]);
  if (editing) return <SegmentForm cat={cat} segOpts={segOpts} existing={seg} initial={{ name: seg.name, description: seg.description, kind: seg.kind, rule: seg.rule, update: seg.update, tz: seg.tz }} onDone={async () => { setEditing(false); await onChanged(); }} onCancel={() => setEditing(false)} />;
  return (
    <div className="stack">
      <div className="card">
        <div className="card-h"><Link to="/segments" className="sub">← 分群列表</Link><h3>{seg.name}</h3><span className={`pill ${STATUS_PILL[seg.status]}`}>{STATUS_CN[seg.status]}</span>{seg.kind === 'result' && <span className="pill custom">結果保存</span>}{seg.latest?.stale && <span className="pill amber">規則已改，請重新計算</span>}</div>
        <p className="note">{seg.description || summarizeRule(seg.rule, cat, segOpts)}</p>
        {seg.description && <p className="note">{summarizeRule(seg.rule, cat, segOpts)}</p>}
        <div className="tiles" style={{ marginTop: 12 }}>
          <div className="tile"><span className="sub">最新人數</span><b>{seg.latest?.users != null ? seg.latest.users.toLocaleString() : '—'}</b><span className="sub">{seg.latest ? `v${seg.latest.version} · ${seg.latest.basetime} · ${TRIGGER_CN[seg.latest.trigger]}${seg.latest.status === 'failed' ? ` · 失敗：${seg.latest.error ?? ''}` : ''}` : '尚未計算'}</span></div>
          <div className="tile"><span className="sub">更新方式</span><b style={{ fontSize: 14 }}>{updateText(seg.update, seg.tz)}</b></div>
          <div className="tile"><span className="sub">審批</span><b style={{ fontSize: 14 }}>{{ none: '不需審批', pending: '待審批', approved: `已核可（${seg.approval.by}）`, rejected: `退回：${seg.approval.note}` }[seg.approval.status]}</b><span className="sub">建立者 {seg.owner} · {seg.created_at.slice(0, 16)}</span></div>
        </div>
        <div className="row">
          {actions.filter((a) => a !== 'approve').map((a) => <button key={a} className={`btn-ghost${a === 'purge' ? ' danger' : ''}`} disabled={busy} onClick={() => void act(a)}>{ACTION_CN[a]}</button>)}
          <button className="btn-ghost" disabled={busy} onClick={() => void loadPreview()}>預覽人數與畫像</button>
          {seg.latest?.status === 'success' && can('users', 'use') && <button className="btn-ghost" disabled={busy} onClick={() => { setCursors([]); void loadMembers(); }}>看成員</button>}
          {seg.latest?.status === 'success' && can('users', 'export') && <button className="btn-ghost" disabled={busy} onClick={() => setExporting(true)}>匯出成員（審批）</button>}
        </div>
        {exporting && <ExportDialog base={{ kind: 'segment_members', segment_id: seg.id, basetime: 'latest' }} onClose={() => setExporting(false)} />}
        {actions.includes('approve') && <div className="row" style={{ background: 'var(--soft)', padding: 10, borderRadius: 10 }}><input aria-label="審批意見" placeholder="審批意見（退回時必填）" value={note} onChange={(e) => setNote(e.target.value)} /><button className="btn-primary" disabled={busy} onClick={() => void act('approve', { approve: true, note })}>核可</button><button className="btn-ghost danger" disabled={busy || !note.trim()} onClick={() => void act('approve', { approve: false, note })}>退回</button></div>}
      </div>
      {preview && <div className="card"><div className="card-h"><h3>預覽</h3><span className="sub">{preview.users.toLocaleString()} 人（精確）</span></div>
        <div className="grid c2">{Object.entries(preview.profile).map(([prop, rows]) => <div key={prop}><b className="sub" style={{ marginBottom: 6 }}>{propCn[prop] ?? prop}</b><div className="bars">{rows.map((r) => <div key={r.value} className="bar"><span>{r.value || '（空）'}</span><i style={{ width: `${preview.users ? (r.users / preview.users) * 100 : 0}%` }} /><em className="mono">{r.users.toLocaleString()}</em></div>)}</div></div>)}</div></div>}
      {members && <UsersTable res={members} colCn={colCn} busy={busy} canPrev={cursors.length > 0} onPrev={() => { const s = cursors.slice(0, -1); setCursors(s); void loadMembers(s[s.length - 1]); }} onNext={() => { if (members.next_cursor) { setCursors([...cursors, members.next_cursor]); void loadMembers(members.next_cursor); } }} onOpen={can('profile', 'use') ? setOpen : undefined} />}
      <div className="grid c2">
        <div className="card flush"><div className="card-h" style={{ padding: '12px 12px 0' }}><h3>歷史版本</h3></div><div className="tbl-wrap"><table className="tbl"><thead><tr><th>版本</th><th>基準日</th><th className="num">人數</th><th>狀態</th><th>觸發</th><th>計算於</th></tr></thead><tbody>
          {versions.map((v) => <tr key={v.version}><td className="mono">v{v.version}</td><td className="mono">{v.basetime}</td><td className="num mono">{v.users?.toLocaleString() ?? '—'}</td><td><span className={`pill ${v.status === 'success' ? 'blue' : v.status === 'failed' ? 'red' : 'gray'}`}>{v.status}</span>{v.error && <span className="sub">{v.error}</span>}</td><td>{TRIGGER_CN[v.trigger]}</td><td className="sub mono">{v.computed_at.slice(0, 16)} · {v.ms} ms</td></tr>)}
          {!versions.length && <tr><td colSpan={6} className="sub">尚未計算</td></tr>}</tbody></table></div></div>
        <div className="card"><div className="card-h"><h3>血緣</h3></div>
          <p className="sub">上游</p><div className="row" style={{ marginTop: 4 }}>{(lineage?.up ?? seg.lineage.up).map((u) => <span key={u} className="pill">{u}</span>)}</div>
          <p className="sub" style={{ marginTop: 10 }}>下游（引用本分群）</p><div className="row" style={{ marginTop: 4 }}>{(lineage?.down ?? []).length ? lineage?.down.map((d) => { const sid = d.replace('segment:', ''); return <Link key={d} to={`/segments/${sid}`} className="pill blue">{segOpts.find((s) => s.id === sid)?.name ?? d}</Link>; }) : <span className="sub">無</span>}</div></div>
      </div>
      {open && <ProfileDrawer oneId={open} cat={cat} project={seg.project} onClose={() => setOpen(null)} />}
    </div>
  );
}

export type { SegmentStatus };
