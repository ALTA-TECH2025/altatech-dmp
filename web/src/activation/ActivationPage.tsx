// 營運 › 推送（docs/14）：推送單（分群 × 目的地 × 模式 × 排程）／目的地（Webhook、secret_ref、測試事件、輪替、停用）／交付紀錄（批次、回應、重試、重放）。
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { DELIVERY_PILL, DELIVERY_STATUS_CN, DEST_STATUS_CN, EVENT_CN, MODE_CN, PUSH_STATUS_CN, PUSH_STATUS_PILL, SCHEDULE_CN, deliveryText, fmtTs, pct, pushSummary } from '@/tags/tags';
import type { Delivery, Destination, Push, Tag } from '@/tags/types';
import type { Segment } from '@/users/types';

export function ActivationPage() {
  return <RequireGrant module="segments"><ActivationInner /></RequireGrant>;
}

type Tab = 'pushes' | 'destinations' | 'deliveries';
function ActivationInner() {
  const { me, can } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get('tab') as Tab) || 'pushes';
  const setTab = (t: Tab) => setSp(t === 'pushes' ? {} : { tab: t });
  const [pushes, setPushes] = useState<Push[]>([]);
  const [dests, setDests] = useState<Destination[]>([]);
  const [dl, setDl] = useState<Delivery[]>([]);
  const [segs, setSegs] = useState<Segment[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newPush, setNewPush] = useState(false);
  const [newDest, setNewDest] = useState(false);
  const [openPush, setOpenPush] = useState<Push | null>(null);
  const reload = async () => { const [p, d, l] = await Promise.all([api.get<Push[]>('/v1/pushes'), api.get<Destination[]>('/v1/destinations'), api.get<Delivery[]>('/v1/deliveries')]); setPushes(p); setDests(d); setDl(l); };
  useEffect(() => {
    reload().catch((e: Error) => setErr(e.message));
    api.get<Segment[]>('/v1/segments').then(setSegs).catch(() => null);
    api.get<Tag[]>('/v1/tags').then(setTags).catch(() => null);
  }, []);
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(true); setErr(null); setMsg(null); try { const r = await fn(); await reload(); setMsg(typeof r === 'string' ? r : `${label}完成`); } catch (e) { setErr(e instanceof ApiError ? e.message : `${label}失敗`); } finally { setBusy(false); } };
  const destName = (id: string) => dests.find((d) => d.id === id)?.name ?? id;
  const approver = can('segments', 'approve'); const destAdmin = approver || can('integration', 'admin');
  return (
    <div className="page">
      <div className="page-h"><div><h1>名單推送</h1><p>把分群成員（代碼化識別子 customer_code／one_id，可附帶允許的標籤）以簽章 Webhook 推到內部系統（MA、CRM）。增量＝與上次交付版本的差集；全量與目的地首次推送需審批；每批 ≤ 5,000 筆、失敗重試 1／5／15 分、1／6 小時後進 DLQ 可重放；連續 3 批失敗自動停用目的地。</p></div>
        <div className="right">{tab === 'pushes' && can('segments', 'use') && <button className="btn-primary" onClick={() => setNewPush(true)}>＋ 新建推送單</button>}{tab === 'destinations' && destAdmin && <button className="btn-primary" onClick={() => setNewDest(true)}>＋ 新增目的地</button>}</div></div>
      {err && <div className="alert" style={{ marginBottom: 12 }}>{err}</div>}{msg && <div className="note">{msg}</div>}
      <div className="seg">{([['pushes', '推送單'], ['destinations', '目的地'], ['deliveries', '交付紀錄']] as const).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}{k === 'deliveries' && dl.some((d) => d.status === 'retrying' || d.status === 'dlq') ? ` (${dl.filter((d) => d.status !== 'ok').length})` : ''}</a>)}</div>
      {newPush && <PushForm segs={segs.filter((s) => s.status === 'active')} dests={dests.filter((d) => d.status === 'active')} tags={tags.filter((t) => t.status === 'active')} onDone={async (p) => { setNewPush(false); await reload(); setMsg(p.status === 'pending_approval' ? '推送單已送審（全量或目的地首次推送需核可）' : '推送單已建立'); }} onCancel={() => setNewPush(false)} />}
      {newDest && <DestForm tags={tags} onDone={async () => { setNewDest(false); await reload(); setMsg('目的地已建立，先送測試事件確認接收端驗章'); }} onCancel={() => setNewDest(false)} />}
      {tab === 'pushes' && (
        <div className="card flush"><div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>分群</th><th>目的地</th><th>模式 / 排程</th><th>附帶標籤</th><th>狀態</th><th>最近</th><th>建立者</th><th></th></tr></thead>
          <tbody>{pushes.map((p) => (
            <tr key={p.id}>
              <td><Link to={`/segments/${p.segment_id}`}><b>{p.segment_name}</b></Link><span className="sub mono">{p.id}</span></td>
              <td>{p.destination_ids.map((d) => <span key={d} className="pill" style={{ marginRight: 4 }}>{destName(d)}</span>)}</td>
              <td className="sub">{MODE_CN[p.mode].split('（')[0]} · {SCHEDULE_CN[p.schedule]}{p.schedule === 'cron' ? ` ${p.cron}` : ''}</td>
              <td>{p.include_tags.length ? p.include_tags.map((t) => <Link key={t} to={`/tags/${t}`} className="pill mono" style={{ marginRight: 4 }}>{t}</Link>) : <span className="note">無</span>}</td>
              <td><span className={`pill ${PUSH_STATUS_PILL[p.status]}`}>{PUSH_STATUS_CN[p.status]}</span></td>
              <td className="sub">{pushSummary(p)}{p.last_run_at ? <span className="sub mono">{fmtTs(p.last_run_at)}</span> : null}</td>
              <td className="sub">{p.owner}</td>
              <td className="num" style={{ whiteSpace: 'nowrap' }}>
                <button className="btn-ghost sm" onClick={() => void api.get<Push>(`/v1/pushes/${p.id}`).then(setOpenPush).catch((e: Error) => setErr(e.message))}>詳情</button>
                {(p.owner === me?.user.id || approver) && p.status === 'active' && <button className="btn-ghost sm" disabled={busy} onClick={() => void act('執行', async () => { const r = await api.post<Push['last_result']>(`/v1/pushes/${p.id}/run`); return r ? `已執行 v${r.version}：+${r.added} −${r.removed}，${r.batches} 批，失敗 ${r.failed}` : '已執行'; })}>立即執行</button>}
                {(p.owner === me?.user.id || approver) && (p.status === 'active' || p.status === 'paused') && <button className="btn-ghost sm" disabled={busy} onClick={() => void act(p.status === 'active' ? '暫停' : '恢復', () => api.post(`/v1/pushes/${p.id}/pause`))}>{p.status === 'active' ? '暫停' : '恢復'}</button>}
                {(p.owner === me?.user.id || approver) && <button className="btn-ghost sm danger" disabled={busy} onClick={() => { if (confirm('刪除推送單？已有交付的只改狀態、保留紀錄。')) void act('刪除', () => api.del(`/v1/pushes/${p.id}`)); }}>刪除</button>}
              </td>
            </tr>))}
            {!pushes.length && <tr><td colSpan={8} className="sub" style={{ textAlign: 'center', padding: 24 }}>還沒有推送單。到「分群」選一個生效中的分群，或按「新建推送單」。</td></tr>}
          </tbody></table></div></div>)}
      {tab === 'destinations' && (
        <div className="card flush"><div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>目的地</th><th>端點</th><th>識別子</th><th>可附標籤</th><th className="num">7 天成功率</th><th className="num">P95</th><th>最近交付</th><th>狀態</th><th></th></tr></thead>
          <tbody>{dests.map((d) => (
            <tr key={d.id}>
              <td><b>{d.name}</b><span className="sub">{d.note}{d.first_push_at ? '' : ' · 尚未首次推送（需核可）'}</span></td>
              <td className="mono" style={{ fontSize: 11.5 }}>{d.url}<span className="sub mono">secret：{d.secret_ref}{d.rotated_at ? `（${fmtTs(d.rotated_at)} 輪替）` : ''}</span></td>
              <td className="mono" style={{ fontSize: 11.5 }}>{d.identifier}</td>
              <td>{d.allowed_tags.length ? d.allowed_tags.map((t) => <span key={t} className="pill mono" style={{ fontSize: 10, margin: 1 }}>{t}</span>) : <span className="note">無（匿名 ID）</span>}</td>
              <td className="num mono">{pct(d.stats.ok_7d)}</td>
              <td className="num mono">{d.stats.p95_ms != null ? `${d.stats.p95_ms} ms` : '—'}</td>
              <td className="sub mono">{fmtTs(d.stats.last_delivery_at)}</td>
              <td><span className={`pill ${d.status === 'active' ? 'blue' : 'gray'}`}>{DEST_STATUS_CN[d.status]}</span>{d.consecutive_failures > 0 && <span className="pill amber">連續失敗 {d.consecutive_failures}</span>}</td>
              <td className="num" style={{ whiteSpace: 'nowrap' }}>{destAdmin && <>
                <button className="btn-ghost sm" disabled={busy} onClick={() => void act('測試', async () => { const r = await api.post<{ status: string; response: { status: number | null; ms: number; body: string } }>(`/v1/destinations/${d.id}/test`); return `audience.test → ${r.response.status ?? '連線失敗'} · ${r.response.ms} ms（${r.status === 'ok' ? '接收端驗章成功' : r.response.body.slice(0, 60)}）`; })}>送測試事件</button>
                <button className="btn-ghost sm" disabled={busy} onClick={() => { const ref = prompt('新的 secret_ref（密鑰庫參照，例如 kv/data/dmp/prod/crm#webhook_secret_v2）'); if (ref) void act('輪替', () => api.post(`/v1/destinations/${d.id}/rotate-secret`, { secret_ref: ref })); }}>輪替 secret</button>
                <button className="btn-ghost sm" disabled={busy} onClick={() => void act(d.status === 'active' ? '停用' : '啟用', () => api.post(`/v1/destinations/${d.id}/pause`))}>{d.status === 'active' ? '停用' : '啟用'}</button></>}</td>
            </tr>))}
            {!dests.length && <tr><td colSpan={9} className="sub" style={{ textAlign: 'center', padding: 24 }}>還沒有目的地。數位經營或資訊管理可新增（只存密鑰庫參照，不存明文）。</td></tr>}
          </tbody></table></div></div>)}
      {tab === 'deliveries' && (
        <div className="card flush"><div className="tbl-wrap"><table className="tbl">
          <thead><tr><th>交付 ID</th><th>分群 / 版本</th><th>目的地</th><th>內容</th><th>狀態</th><th className="num">嘗試</th><th>時間</th><th></th></tr></thead>
          <tbody>{dl.map((d) => (
            <tr key={d.id}>
              <td className="mono" style={{ fontSize: 11.5 }}>{d.id}{d.replay_of && <span className="sub">重放</span>}</td>
              <td className="sub">{segs.find((s) => s.id === d.segment_id)?.name ?? d.segment_id}{d.mode !== 'test' ? ` v${d.version}` : ''}</td>
              <td className="sub">{destName(d.destination_id)}</td>
              <td className="sub">{deliveryText(d)}</td>
              <td><span className={`pill ${DELIVERY_PILL[d.status]}`}>{DELIVERY_STATUS_CN[d.status]}</span></td>
              <td className="num mono">{d.attempts}</td>
              <td className="sub mono">{fmtTs(d.updated_at)}</td>
              <td className="num">{approver && d.status !== 'ok' && <button className="btn-ghost sm" disabled={busy} onClick={() => void act('重放', () => api.post(`/v1/deliveries/${d.id}/replay`))}>重放</button>}</td>
            </tr>))}
            {!dl.length && <tr><td colSpan={8} className="sub" style={{ textAlign: 'center', padding: 24 }}>還沒有交付。</td></tr>}
          </tbody></table></div></div>)}
      {openPush && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="card-h"><h3>推送單 {openPush.id}</h3><button className="btn-ghost sm" onClick={() => setOpenPush(null)}>關閉</button></div>
          <dl className="kv"><dt>分群</dt><dd>{openPush.segment_name}</dd><dt>目的地</dt><dd>{openPush.destination_ids.map(destName).join('、')}</dd><dt>模式／排程</dt><dd>{MODE_CN[openPush.mode]} · {SCHEDULE_CN[openPush.schedule]}{openPush.schedule === 'cron' && openPush.cron ? ` ${openPush.cron}` : ''}</dd><dt>附帶標籤</dt><dd>{openPush.include_tags.join('、') || '無'}</dd><dt>最近交付版本</dt><dd>{openPush.last_delivered_version ?? '—'}</dd><dt>執行次數</dt><dd>{openPush.runs}</dd></dl>
          <table className="tbl" style={{ marginTop: 8 }}><thead><tr><th>交付</th><th>事件</th><th>內容</th><th>狀態</th><th>時間</th></tr></thead><tbody>{(openPush.deliveries ?? []).map((d) => <tr key={d.id}><td className="mono" style={{ fontSize: 11.5 }}>{d.id}</td><td className="sub">{EVENT_CN[d.event] ?? d.event}</td><td className="sub">{deliveryText(d)}</td><td><span className={`pill ${DELIVERY_PILL[d.status]}`}>{DELIVERY_STATUS_CN[d.status]}</span></td><td className="sub mono">{fmtTs(d.updated_at)}</td></tr>)}</tbody></table>
        </div>)}
      <p className="note" style={{ marginTop: 12 }}>接收端要做的事：驗簽（HMAC-SHA256，X-DMP-Timestamp ±5 分鐘）→ 以 X-DMP-Delivery-Id 冪等 → 依 action 加入／移除名單 → 回 2xx。名單不含姓名、手機、Email 等任何 L3／L4 明文。Kafka／GCS 目的地與即時推送排第二階段。</p>
    </div>
  );
}

function PushForm({ segs, dests, tags, onDone, onCancel }: { segs: Segment[]; dests: Destination[]; tags: Tag[]; onDone: (p: Push) => Promise<void>; onCancel: () => void }) {
  const [seg, setSeg] = useState(segs[0]?.id ?? '');
  const [ids, setIds] = useState<string[]>([]);
  const [mode, setMode] = useState<'delta' | 'full'>('delta');
  const [schedule, setSchedule] = useState<'now' | 'after_batch' | 'cron'>('after_batch');
  const [cron, setCron] = useState('03:30');
  const [inc, setInc] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const allowed = tags.filter((t) => ids.length > 0 && ids.every((d) => dests.find((x) => x.id === d)?.allowed_tags.includes(t.id)));
  const chosen = segs.find((s) => s.id === seg);
  const save = async () => { setBusy(true); setErr(null); try { await onDone(await api.post<Push>(`/v1/audiences/${seg}/push`, { destination_ids: ids, mode, schedule, cron: schedule === 'cron' ? cron : '', include_tags: inc.filter((t) => allowed.some((a) => a.id === t)) })); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); } };
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="card-h"><h3>新建推送單</h3></div>
      <div className="grid c3">
        <label>分群（生效中）<select aria-label="推送分群" value={seg} onChange={(e) => setSeg(e.target.value)}>{segs.map((s) => <option key={s.id} value={s.id}>{s.name}{s.latest?.users != null ? `（${s.latest.users.toLocaleString()} 人）` : '（尚未計算）'}</option>)}</select></label>
        <label>模式<select aria-label="推送模式" value={mode} onChange={(e) => setMode(e.target.value as 'delta')}><option value="delta">增量（加入／移除）</option><option value="full">全量（需審批）</option></select></label>
        <label>排程<select aria-label="推送排程" value={schedule} onChange={(e) => setSchedule(e.target.value as 'now')}><option value="after_batch">日批完成後（分群有新版本即推）</option><option value="now">立即</option><option value="cron">每日定時</option></select>{schedule === 'cron' && <input type="time" value={cron} onChange={(e) => setCron(e.target.value)} />}</label>
      </div>
      <div style={{ marginTop: 10 }}><b className="sub">目的地</b><div className="row" style={{ marginTop: 4 }}>{dests.map((d) => <label key={d.id} className="chk"><input type="checkbox" aria-label={`目的地 ${d.name}`} checked={ids.includes(d.id)} onChange={(e) => setIds(e.target.checked ? [...ids, d.id] : ids.filter((x) => x !== d.id))} />{d.name}<span className="sub">{d.identifier}{d.first_push_at ? '' : ' · 首次推送需核可'}</span></label>)}{!dests.length && <span className="note">沒有啟用中的目的地</span>}</div></div>
      <div style={{ marginTop: 10 }}><b className="sub">附帶標籤（須在每個目的地的可附標籤內；數值型區間化）</b><div className="row" style={{ marginTop: 4 }}>{allowed.map((t) => <label key={t.id} className="chk"><input type="checkbox" checked={inc.includes(t.id)} onChange={(e) => setInc(e.target.checked ? [...inc, t.id] : inc.filter((x) => x !== t.id))} />{t.name}<span className="sub mono">{t.id}</span></label>)}{!allowed.length && <span className="note">選目的地後列出可附帶的標籤</span>}</div></div>
      {chosen && !chosen.latest?.users && <p className="note">此分群尚未計算，推送前請先到分群頁「立即計算」。</p>}
      <div className="row"><button className="btn-primary" disabled={!seg || !ids.length || busy} onClick={() => void save()}>{busy ? '建立中…' : mode === 'full' || ids.some((d) => !dests.find((x) => x.id === d)?.first_push_at) ? '送審並建立' : '建立'}</button><button className="btn-ghost" onClick={onCancel}>取消</button>{err && <span className="alert">{err}</span>}</div>
    </div>
  );
}

function DestForm({ tags, onDone, onCancel }: { tags: Tag[]; onDone: () => Promise<void>; onCancel: () => void }) {
  const [d, setD] = useState({ name: '', url: 'https://', secret_ref: 'kv/data/dmp/prod/', identifier: 'customer_code' as 'customer_code' | 'one_id', allowed_tags: [] as string[], note: '' });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => { setBusy(true); setErr(null); try { await api.post('/v1/destinations', { ...d, type: 'webhook' }); await onDone(); } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); } };
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="card-h"><h3>新增目的地（Webhook）</h3></div>
      <div className="grid c3">
        <label>名稱<input aria-label="目的地名稱" value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
        <label>端點 URL（https）<input aria-label="目的地 URL" className="mono" value={d.url} onChange={(e) => setD({ ...d, url: e.target.value })} /></label>
        <label>簽章密鑰 secret_ref<input aria-label="secret_ref" className="mono" value={d.secret_ref} placeholder="kv/data/dmp/prod/crm#webhook_secret" onChange={(e) => setD({ ...d, secret_ref: e.target.value })} /></label>
        <label>識別子<select value={d.identifier} onChange={(e) => setD({ ...d, identifier: e.target.value as 'customer_code' })}><option value="customer_code">customer_code（代碼化）</option><option value="one_id">one_id</option></select></label>
        <label>說明<input value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} /></label>
      </div>
      <div style={{ marginTop: 10 }}><b className="sub">可附帶標籤</b><div className="row" style={{ marginTop: 4 }}>{tags.filter((t) => t.status === 'active').map((t) => <label key={t.id} className="chk"><input type="checkbox" checked={d.allowed_tags.includes(t.id)} onChange={(e) => setD({ ...d, allowed_tags: e.target.checked ? [...d.allowed_tags, t.id] : d.allowed_tags.filter((x) => x !== t.id) })} />{t.name}</label>)}</div></div>
      <p className="note">密鑰只存密鑰庫參照（OpenBao／GCP Secret Manager／K8s Secret），不接受明文；新目的地或換了端點／密鑰後，第一次推送需核可。</p>
      <div className="row"><button className="btn-primary" disabled={!d.name.trim() || busy} onClick={() => void save()}>{busy ? '建立中…' : '建立'}</button><button className="btn-ghost" onClick={onCancel}>取消</button>{err && <span className="alert">{err}</span>}</div>
    </div>
  );
}
