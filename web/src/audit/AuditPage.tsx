// 日誌統計（docs/10 §8）：/v1/audit/stats 看板（指標卡、每日趨勢、模組／角色、敏感存取 Top、審批、告警規則評估、agent 活動）+ /v1/audit 明細；audit.export 可匯出報表。
import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/api/client';
import type { AuditStats } from '@/api/types';
import { LineChart } from '@/analytics/LineChart';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { DAILY_KEYS, dailySeries, hbars, ruleText, sortAnomalies } from './stats';

type Row = { event: string; at: number; user?: string; module?: string; [k: string]: unknown };
const TILES: { key: keyof AuditStats['tiles']; cn: string; hint?: string }[] = [
  { key: 'active_users', cn: '活躍使用者' }, { key: 'logins', cn: '登入' }, { key: 'login_failed', cn: '登入失敗' }, { key: 'denied', cn: '權限拒絕' },
  { key: 'profile_views', cn: '細查' }, { key: 'reveals', cn: '揭露申請' }, { key: 'exports', cn: '匯出' }, { key: 'queries', cn: '查詢' }, { key: 'approvals_pending', cn: '待審批' }, { key: 'events', cn: '審計事件' },
];
const SEV: Record<string, [string, string]> = { high: ['高', 'red'], medium: ['中', 'amber'], low: ['低', 'gray'] };

export function AuditPage() {
  return <RequireGrant module="audit"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth();
  const [days, setDays] = useState(30);
  const [st, setSt] = useState<AuditStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [series, setSeries] = useState<(keyof AuditStats['daily'][number])[]>(['logins', 'denied', 'profile_views']);
  useEffect(() => { setSt(null); api.get<AuditStats>(`/v1/audit/stats?days=${days}`).then(setSt).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [days]);
  const exportCsv = () => { window.open(`/v1/audit/export?days=${days}`, '_blank'); };
  return (
    <div className="page">
      <div className="page-h">
        <div><h1>日誌統計</h1><p>所有人與 AI 的讀寫都是審計事件；用看板看使用概況、敏感存取、審批與異常。{st?.scope === 'all' ? '你看到的是全平台。' : '你的角色只看到自己的紀錄。'}</p></div>
        <div className="right row">
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 14, 30, 90].map((d) => <option key={d} value={d}>近 {d} 天</option>)}</select>
          {can('audit', 'export') && <button className="btn-ghost" onClick={exportCsv}>匯出審計報表</button>}
          <span className="pill gray" title="問 agent：上週誰細查最多？有沒有非上班時間的敏感存取？">問 agent · 第二階段</span>
        </div>
      </div>
      {err && <div className="alert">{err}</div>}
      {!st && !err && <div className="loading">載入中…</div>}
      {st && <Board st={st} series={series} setSeries={setSeries} />}
      <Detail />
    </div>
  );
}

function Board({ st, series, setSeries }: { st: AuditStats; series: (keyof AuditStats['daily'][number])[]; setSeries: (s: (keyof AuditStats['daily'][number])[]) => void }) {
  const anomalies = sortAnomalies(st.anomalies);
  const mods = hbars(st.by_module.map((m) => ({ label: m.module, n: m.n })));
  const roles = hbars(st.by_role.map((r) => ({ label: r.role, n: r.profile_views })));
  const types: Record<string, string> = { export: '匯出', segment: '分群', push: '推送', reveal_l2: '揭露 L2', reveal_l3: '揭露 L3', agent_proposal: 'agent', role_change: '角色變更', break_glass: '臨時提權' };
  return (
    <>
      <div className="tiles" style={{ marginBottom: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))' }}>{TILES.map((t) => <div className="tile" key={t.key}><span className="sub">{t.cn}</span><b style={{ fontSize: 22 }}>{st.tiles[t.key].toLocaleString()}</b></div>)}</div>
      <div className="grid c21">
        <div className="stack">
          <div className="card"><div className="card-h"><h3>每日趨勢</h3><div className="right row">{DAILY_KEYS.map((k) => <label key={k.key} className="sub" style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}><input type="checkbox" checked={series.includes(k.key)} onChange={(e) => setSeries(e.target.checked ? [...series, k.key] : series.filter((x) => x !== k.key))} />{k.cn}</label>)}</div></div>
            {st.daily.length ? <LineChart series={dailySeries(st, series)} height={220} /> : <p className="note">期間內沒有審計事件。</p>}</div>
          <div className="grid c2">
            <div className="card"><div className="card-h"><h3>模組使用次數</h3><span className="sub">query.executed · users.listed · profile.viewed</span></div>{mods.length ? mods.map((m) => <div className="bar" key={m.label}><span>{m.label}</span><i style={{ width: `${m.pct}%` }} /><em>{m.n}</em></div>) : <p className="note">無</p>}</div>
            <div className="card"><div className="card-h"><h3>細查次數（依角色）</h3><span className="sub">profile.* · 以登入時角色歸類</span></div>{roles.length ? roles.map((m) => <div className="bar" key={m.label}><span>{m.label}</span><i style={{ width: `${m.pct}%`, background: 'var(--purple)' }} /><em>{m.n}</em></div>) : <p className="note">無</p>}</div>
          </div>
          <div className="card"><div className="card-h"><h3>審批</h3><span className="sub">待審 {st.approvals.pending} · 核可 {st.approvals.approved} · 退回 {st.approvals.rejected} · 逾期 {st.approvals.expired}{st.approvals.avg_hours != null ? ` · 平均處理 ${st.approvals.avg_hours} 小時` : ''}</span></div>
            {Object.keys(st.approvals.by_type).length ? <table className="tbl"><thead><tr><th>類型</th><th className="num">待審</th><th className="num">核可</th><th className="num">退回</th><th className="num">逾期</th><th className="num">核准率</th></tr></thead>
              <tbody>{Object.entries(st.approvals.by_type).map(([t, v]) => { const d = (v.approved ?? 0) + (v.rejected ?? 0); return <tr key={t}><td>{types[t] ?? t}</td><td className="num">{v.pending ?? 0}</td><td className="num">{v.approved ?? 0}</td><td className="num">{v.rejected ?? 0}</td><td className="num">{v.expired ?? 0}</td><td className="num">{d ? `${Math.round(((v.approved ?? 0) / d) * 100)}%` : '—'}</td></tr>; })}</tbody></table> : <p className="note">期間內沒有審批單。</p>}</div>
        </div>
        <div className="stack">
          <div className="card"><div className="card-h"><h3>敏感存取 Top</h3><span className="sub">細查次數 · 主要理由 · 非上班時間</span></div>
            {st.top_sensitive.length ? <table className="tbl"><thead><tr><th>使用者</th><th className="num">次數</th><th className="num">非上班</th><th>主要理由</th></tr></thead><tbody>{st.top_sensitive.map((r) => <tr key={r.user} className={r.off_hours ? 'warnrow' : ''}><td className="mono" style={{ fontSize: 12 }}>{r.user}</td><td className="num">{r.n}</td><td className="num">{r.off_hours || '—'}</td><td className="sub">{r.reason || '—'}</td></tr>)}</tbody></table> : <p className="note">期間內沒有細查。</p>}</div>
          <div className="card"><div className="card-h"><h3>異常與告警</h3><span className="sub">{anomalies.length} 筆 · 通知 {st.notify.join('、')}</span></div>
            {anomalies.length ? <table className="tbl"><tbody>{anomalies.slice(0, 12).map((a, i) => <tr key={i}><td className="mono" style={{ whiteSpace: 'nowrap', fontSize: 11.5 }}>{a.bucket}</td><td><span className={`pill ${SEV[a.severity]?.[1] ?? 'gray'}`}>{SEV[a.severity]?.[0] ?? a.severity}</span> {a.cn}<span className="sub mono">{a.user} · {a.n} / 門檻 {a.threshold}</span></td></tr>)}</tbody></table> : <p className="note">{st.scope === 'all' ? '期間內沒有觸發告警規則。' : '告警評估只對全平台檢視者顯示。'}</p>}</div>
          <div className="card"><div className="card-h"><h3>AI agent 活動</h3>{st.agent.stage === 'phase2' && <span className="pill gray">第二階段</span>}</div>
            <div className="grid c2" style={{ gap: 10 }}><div className="tile"><span className="sub">工具呼叫</span><b style={{ fontSize: 20 }}>{st.agent.tool_calls}</b></div><div className="tile"><span className="sub">提議</span><b style={{ fontSize: 20 }}>{st.agent.proposals}</b></div></div>
            <p className="note">第一階段 AI 閘道關閉；接上後 agent.tool_called（含委託身份、理由、預算用量）與 evals 通過率會在這裡。</p></div>
          <div className="card"><div className="card-h"><h3>告警規則</h3><span className="sub">backend/security/audit_alerts.yaml</span></div><ul className="priv">{st.rules.map((r) => <li key={r.key}>{ruleText(r)}</li>)}</ul><p className="note">上班時間 {st.business_hours.start}:00–{st.business_hours.end}:00（{st.business_hours.tz}{st.business_hours.weekdays_only ? '，週一至週五' : ''}）；通知管道第二階段。</p></div>
        </div>
      </div>
    </>
  );
}

function Detail() {
  const [rows, setRows] = useState<Row[]>([]); const [err, setErr] = useState<string | null>(null); const [q, setQ] = useState(''); const [n, setN] = useState(200);
  useEffect(() => { api.get<Row[]>(`/v1/audit?n=${n}`).then((r) => setRows([...r].reverse())).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [n]);
  const list = useMemo(() => rows.filter((r) => !q || r.event.includes(q) || String(r.user ?? '').includes(q) || String(r.module ?? '').includes(q)), [rows, q]);
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-h"><h3>明細</h3><div className="right row"><input placeholder="篩選事件／使用者／模組" value={q} onChange={(e) => setQ(e.target.value)} /><select value={n} onChange={(e) => setN(Number(e.target.value))}>{[100, 200, 500, 1000].map((x) => <option key={x} value={x}>最近 {x} 筆</option>)}</select></div></div>
      {err && <div className="alert">{err}</div>}
      <div className="tbl-wrap"><table className="tbl"><thead><tr><th>時間</th><th>事件</th><th>使用者</th><th>模組</th><th>內容</th></tr></thead>
        <tbody>{list.map((r, i) => <tr key={i}><td className="mono" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>{new Date(r.at * 1000).toISOString().slice(0, 19).replace('T', ' ')}</td><td className="mono">{r.event}</td><td className="mono" style={{ fontSize: 11.5 }}>{String(r.user ?? '')}</td><td>{String(r.module ?? '')}</td><td className="mono" style={{ fontSize: 11, maxWidth: 520, overflowWrap: 'anywhere' }}>{JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k]) => !['event', 'at', 'user', 'module'].includes(k)))).slice(0, 300)}</td></tr>)}</tbody></table></div>
      <p className="note">查詢效能與掃描量彙總（ClickHouse query_log）第二階段；正式環境看板改由 dmp.audit_log 物化視圖供數。</p>
    </div>
  );
}
