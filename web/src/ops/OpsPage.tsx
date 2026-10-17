// 數據治理 › 資料流與效能（docs/17 §5.16）：資料流（四段＋資料品質）／查詢效能（各模組 P50/P95/P99 對 SLA、最慢查詢、ClickHouse query_log）／批次與儲存（同步任務可重跑、例行作業、表用量）／告警（觸發中＋全部規則）。
// 權限：governance.view 看；重跑需 governance.admin（審計 batch.rerun）。數字來源：排程器、審計、程序內指標、ClickHouse 系統表；叢集內同一組指標由 Prometheus／Grafana 供數。
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { alertText, checkNotify, countBySeverity, fmtBytes, fmtMs, fmtPct, fmtRows, fmtTs, JOB_STATUS_CN, lagText, NTF_KIND_CN, NTF_STATUS_PILL, overall, ruleThreshold, SEV_CN, SEV_PILL, slaStatus, splitList, STATUS_CN, STATUS_PILL } from './ops';
import type { ActivationItem, Alerts, BatchJobs, Notification, NotifyConfig, OpsStatus, Pipeline, QueryPerf, Severity, ServingItem, SourceItem, TableItem } from './types';

export function OpsPage() {
  return <RequireGrant module="governance"><OpsInner /></RequireGrant>;
}

type Tab = 'flow' | 'query' | 'batch' | 'alerts';
const Dot = ({ s }: { s: OpsStatus }) => <span className={`st-dot ${s}`} aria-label={STATUS_CN[s]} title={STATUS_CN[s]} />;
const Pill = ({ s }: { s: OpsStatus }) => <span className={`pill ${STATUS_PILL[s]}`}>{STATUS_CN[s]}</span>;

function OpsInner() {
  const { can } = useAuth();
  const canAdmin = can('governance', 'admin');
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get('tab') as Tab) || 'flow';
  const setTab = (t: Tab) => setSp(t === 'flow' ? {} : { tab: t });
  const [pl, setPl] = useState<Pipeline | null>(null);
  const [qp, setQp] = useState<QueryPerf | null>(null);
  const [bj, setBj] = useState<BatchJobs | null>(null);
  const [al, setAl] = useState<Alerts | null>(null);
  const [win, setWin] = useState<QueryPerf['window']>('24h');
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    try { const [a, b, c] = await Promise.all([api.get<Pipeline>('/v1/ops/pipeline'), api.get<BatchJobs>('/v1/ops/batch-jobs'), api.get<Alerts>('/v1/ops/alerts')]); setPl(a); setBj(b); setAl(c); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '讀取失敗'); }
  };
  useEffect(() => { void reload(); }, []);
  useEffect(() => { api.get<QueryPerf>(`/v1/ops/query-perf?window=${win}`).then(setQp).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [win]);
  const rerun = async (name: string) => {
    setBusy(true); setMsg(null);
    try { const r = await api.post<{ job: string; status?: string }>(`/v1/ops/batch-jobs/${encodeURIComponent(name)}/rerun`); setMsg(`已重跑 ${r.job}：${JOB_STATUS_CN[r.status ?? ''] ?? r.status ?? '已排入'}（審計 batch.rerun）`); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '重跑失敗'); }
    finally { setBusy(false); }
  };
  const status = pl ? overall(pl.stages.map((s) => s.status)) : 'unknown';
  const sev = al ? countBySeverity(al.firing) : {};
  return (
    <div className="page">
      <div className="page-h"><div><h1>資料流與效能 <Pill s={status} /></h1><p>端到端資料流、各模組查詢效能對 SLA、批次作業與儲存、告警。數字來自排程器、審計、API 程序內指標與 ClickHouse 系統表；叢集內同一組指標由 Prometheus 抓 <code>/metrics</code>、Grafana 看板與 Alertmanager 用同一份規則。</p></div>
        <div className="row" style={{ marginTop: 0 }}><button className="btn-ghost" onClick={() => void reload()} disabled={busy}>重新整理</button></div></div>
      {err && <div className="alert">{err}</div>}
      {msg && <div className="note ok-note">{msg}</div>}
      <div className="seg">{([['flow', '資料流'], ['query', '查詢效能'], ['batch', '批次與儲存'], ['alerts', '告警']] as const).map(([k, cn]) => <a key={k} href="#" className={tab === k ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(k); }}>{cn}{k === 'alerts' && al?.firing.length ? ` (${al.firing.length})` : ''}</a>)}</div>
      {tab === 'flow' && (pl ? <FlowTab pl={pl} /> : <p className="note">讀取中…</p>)}
      {tab === 'query' && <QueryTab qp={qp} win={win} setWin={setWin} />}
      {tab === 'batch' && (bj ? <BatchTab bj={bj} canAdmin={canAdmin} busy={busy} rerun={rerun} /> : <p className="note">讀取中…</p>)}
      {tab === 'alerts' && (al ? <><AlertsTab al={al} sev={sev} /><NotifyCard canAdmin={canAdmin} /></> : <p className="note">讀取中…</p>)}
    </div>
  );
}

function FlowTab({ pl }: { pl: Pipeline }) {
  const src = (pl.stages.find((s) => s.key === 'sources')?.items ?? []) as SourceItem[];
  const tables = (pl.stages.find((s) => s.key === 'warehouse')?.items ?? []) as TableItem[];
  const sv = (pl.stages.find((s) => s.key === 'serving')?.items ?? [])[0] as ServingItem | undefined;
  const act = (pl.stages.find((s) => s.key === 'activation')?.items ?? [])[0] as ActivationItem | undefined;
  return (
    <>
      <div className="card"><div className="card-h"><h3>端到端資料流</h3><span className="sub">來源同步 → ClickHouse 進倉 → API 與查詢 → 推送交付 · {fmtTs(pl.at)}</span></div>
        <div className="pipe">{pl.stages.map((s, i) => <div key={s.key} className="pipe-wrap">{i > 0 && <div className="pipe-arrow" aria-hidden>→</div>}<div className={`pipe-stage ${s.status}`}><div className="hd"><b>{s.cn}</b><Dot s={s.status} /></div><div className="sub">{s.summary}</div></div></div>)}</div>
        <p className="note">SLO：行為事件 {pl.slo.freshness.behavior_events_min} 分鐘可查、日批 {pl.slo.freshness.daily_batch_deadline} 前到齊、例行作業 {pl.slo.freshness.routine_max_min} 分鐘內完成、API 5xx &lt; {fmtPct(pl.slo.serving.http_error_rate_max, 0)}、交付成功率 ≥ {fmtPct(pl.slo.activation.delivery_success_min, 0)}。</p></div>
      <div className="grid c2" style={{ marginTop: 16 }}>
        <div className="card flush"><div className="card-h" style={{ padding: '14px 16px 0' }}><h3>資料品質</h3></div>
          <div className="tbl-wrap"><table className="tbl"><tbody>{pl.quality.map((q) => <tr key={q.key} className={q.status === 'error' ? 'warnrow' : ''}><td>{q.cn}</td><td className="mono">{q.text}</td><td><Pill s={q.status} /></td></tr>)}</tbody></table></div></div>
        <div className="card flush"><div className="card-h" style={{ padding: '14px 16px 0' }}><h3>來源同步</h3><span className="sub">{src.length} 個任務 · <Link to="/integration?tab=tasks">數據整合</Link></span></div>
          {src.length ? <div className="tbl-wrap"><table className="tbl"><thead><tr><th>任務</th><th>排程</th><th>最近一次</th><th className="num">讀入／隔離</th><th>水位落後</th><th>狀態</th></tr></thead>
            <tbody>{src.map((s) => <tr key={s.task} className={s.status === 'error' ? 'warnrow' : ''}><td><b>{s.cn}</b><span className="sub mono">{s.task}{s.enabled ? '' : ' · 已停用'}</span></td><td className="mono" style={{ fontSize: 12 }}>{s.schedule}</td><td className="mono" style={{ fontSize: 12 }}>{fmtTs(s.last_at)}<span className="sub">{JOB_STATUS_CN[s.last_status ?? ''] ?? s.last_status ?? '—'}{s.error ? ` · ${s.error.slice(0, 60)}` : ''}</span></td><td className="num mono">{fmtRows(s.rows_in)}{s.rows_bad ? <span className="sub">{fmtRows(s.rows_bad)}（{fmtPct(s.quarantine_rate)}）</span> : null}</td><td className="mono">{lagText(s.lag_min)}</td><td><Pill s={s.status} /></td></tr>)}</tbody></table></div> : <p className="note" style={{ padding: '0 16px 14px' }}>排程器未啟用或沒有同步任務（單機以 DMP_SCHED_DB 開啟）。</p>}</div>
      </div>
      <div className="grid c3" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>ClickHouse 進倉</h3></div>
          {tables.length ? <div className="tbl-wrap"><table className="tbl"><thead><tr><th>表</th><th className="num">列數</th><th className="num">用量</th></tr></thead><tbody>{tables.slice(0, 8).map((t) => <tr key={t.name}><td>{t.cn}<span className="sub mono">dmp.{t.name}</span></td><td className="num mono">{fmtRows(t.rows)}</td><td className="num mono">{fmtBytes(t.bytes)}</td></tr>)}</tbody></table></div> : <p className="note">未連接 ClickHouse。</p>}</div>
        <div className="card"><div className="card-h"><h3>API 與查詢（近 5 分鐘）</h3></div>
          {sv ? <dl className="kv"><dt>請求</dt><dd className="mono">{sv.requests_5m} 次 · P50 {fmtMs(sv.http_p50_ms)} · P95 {fmtMs(sv.http_p95_ms)}</dd><dt>5xx</dt><dd className="mono">{fmtPct(sv.http_error_rate, 2)}（累計 {sv.errors_5xx_total} / {sv.requests_total}）</dd><dt>分析查詢</dt><dd className="mono">{sv.queries_5m} 次 · P95 {fmtMs(sv.query_p95_ms)}</dd></dl> : <p className="note">—</p>}</div>
        <div className="card"><div className="card-h"><h3>推送交付（近 24 小時）</h3><span className="sub"><Link to="/activation?tab=deliveries">交付紀錄</Link></span></div>
          {act ? <dl className="kv"><dt>交付</dt><dd className="mono">{Object.entries(act.deliveries_24h).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(' · ') || '無'}</dd><dt>成功率</dt><dd className="mono">{fmtPct(act.success_rate)}</dd><dt>自動暫停</dt><dd>{act.paused_destinations.length ? act.paused_destinations.map((p) => <span key={p.destination} className="pill amber">{p.cn} ×{p.consecutive_failures}</span>) : '無'}</dd></dl> : <p className="note">—</p>}</div>
      </div>
    </>
  );
}

function QueryTab({ qp, win, setWin }: { qp: QueryPerf | null; win: QueryPerf['window']; setWin: (w: QueryPerf['window']) => void }) {
  return (
    <>
      <div className="row" style={{ marginTop: 0, marginBottom: 12 }}><select value={win} onChange={(e) => setWin(e.target.value as QueryPerf['window'])}>{(['1h', '24h', '7d'] as const).map((w) => <option key={w} value={w}>{w === '1h' ? '近 1 小時' : w === '24h' ? '近 24 小時' : '近 7 天'}</option>)}</select>
        {qp?.clickhouse && <span className="sub">ClickHouse query_log：{qp.clickhouse.n} 次 · P95 {fmtMs(qp.clickhouse.p95_ms)} · 平均掃描 {fmtRows(qp.clickhouse.scan_rows_avg)} 列 · 記憶體峰值 {qp.clickhouse.memory_peak_mb ?? '—'} MB · 例外 {qp.clickhouse.errors}</span>}</div>
      {!qp ? <p className="note">讀取中…</p> : (
        <>
          <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>模組</th><th className="num">次數</th><th className="num">錯誤</th><th className="num">P50</th><th className="num">P95</th><th className="num">P99</th><th className="num">SLA（P95）</th><th className="num">平均列數</th><th>狀態</th></tr></thead>
            <tbody>{qp.modules.length ? qp.modules.map((m) => { const s = slaStatus(m); return <tr key={m.module} className={s === 'error' ? 'warnrow' : ''}><td><b>{m.cn}</b><span className="sub mono">{m.module}</span></td><td className="num mono">{m.n}</td><td className="num mono">{m.errors ? <span style={{ color: 'var(--st-serious)' }}>{m.errors}（{fmtPct(m.error_rate)}）</span> : '0'}</td><td className="num mono">{fmtMs(m.p50_ms)}</td><td className="num mono"><b>{fmtMs(m.p95_ms)}</b></td><td className="num mono">{fmtMs(m.p99_ms)}</td><td className="num mono">{fmtMs(m.sla_ms)}</td><td className="num mono">{m.avg_rows == null ? '—' : Math.round(m.avg_rows).toLocaleString()}</td><td>{m.sla_ms == null ? <span className="pill gray">無 SLA</span> : <Pill s={s} />}</td></tr>; }) : <tr><td colSpan={9} className="note">期間內沒有查詢。</td></tr>}</tbody></table></div></div>
          <div className="grid c2" style={{ marginTop: 16 }}>
            <div className="card flush"><div className="card-h" style={{ padding: '14px 16px 0' }}><h3>最慢的查詢</h3><span className="sub">sql_hash 可對到審計 query.executed</span></div>
              {qp.slow.length ? <div className="tbl-wrap"><table className="tbl"><thead><tr><th>時間</th><th>模組</th><th className="num">耗時</th><th className="num">列數</th><th>使用者</th><th>sql_hash</th></tr></thead><tbody>{qp.slow.map((r, i) => <tr key={i}><td className="mono" style={{ fontSize: 12 }}>{fmtTs(r.at)}</td><td>{r.module}</td><td className="num mono">{fmtMs(r.ms)}</td><td className="num mono">{r.rows}</td><td className="mono" style={{ fontSize: 12 }}>{r.user ?? '—'}</td><td className="mono" style={{ fontSize: 12 }}>{r.sql_hash ?? '—'}</td></tr>)}</tbody></table></div> : <p className="note" style={{ padding: '0 16px 14px' }}>—</p>}</div>
            <div className="card"><div className="card-h"><h3>效能策略（docs/17 §5、docs/23 §6）</h3></div><ul className="priv"><li>90% 查詢走物化視圖（L1 日彙總、L2 常用分組、L3 cohort），不碰明細；路徑預設抽樣 1/10。</li><li>one_id_uint 稠密 ID + Bitmap：人數估算目標 &lt; 1 秒；標籤／分群成員以 Bitmap 交集。</li><li>工作負載隔離：human 60 秒、batch 600 秒的 max_execution_time；自定義 SQL 有掃描預算與每人併發上限。</li><li>P95 連續超標先看 query_log 的 read_rows 與 memory_usage，再決定補 MV 或改口徑（M2 效能基準回填 SLA）。</li></ul></div>
          </div>
        </>
      )}
    </>
  );
}

function BatchTab({ bj, canAdmin, busy, rerun }: { bj: BatchJobs; canAdmin: boolean; busy: boolean; rerun: (n: string) => Promise<void> }) {
  const jobStatus = (j: BatchJobs['jobs'][number]): OpsStatus => (!j.enabled ? 'warn' : j.last_status == null ? 'unknown' : ['success', 'running', 'skipped', 'retried'].includes(j.last_status) ? 'ok' : j.last_status === 'retry_wait' ? 'warn' : 'error');
  const hot = bj.storage.filter((s) => s.name.startsWith('clickhouse.')).reduce((a, s) => a + (s.bytes ?? 0), 0);
  return (
    <>
      <div className="card flush"><div className="tbl-wrap"><table className="tbl"><thead><tr><th>作業</th><th>類型</th><th>排程</th><th>最近一次</th><th className="num">耗時</th><th>結果</th><th>狀態</th><th></th></tr></thead>
        <tbody>{bj.jobs.map((j) => { const s = jobStatus(j); return <tr key={j.name} className={s === 'error' ? 'warnrow' : ''}><td><b>{j.cn}</b><span className="sub mono">{j.name}</span></td><td>{j.kind === 'sync' ? '同步任務' : '例行'}</td><td className="mono" style={{ fontSize: 12 }}>{j.schedule}{j.next_at ? <span className="sub">下次 {fmtTs(j.next_at)}</span> : null}</td><td className="mono" style={{ fontSize: 12 }}>{fmtTs(j.last_at)}</td><td className="num mono">{fmtMs(j.last_ms)}</td><td style={{ fontSize: 12.5 }}>{j.last_status ? <>{JOB_STATUS_CN[j.last_status] ?? j.last_status}{j.last_detail ? <span className="sub">{j.last_detail}</span> : null}{j.errors_total ? <span className="sub">累計失敗 {j.errors_total} / {j.runs_total}</span> : null}</> : <span className="sub">尚未執行（DMP_TICK=1 才有背景例行）</span>}</td><td><Pill s={s} /></td><td className="num">{j.rerunnable && canAdmin ? <button className="btn-ghost sm" disabled={busy} onClick={() => void rerun(j.name)}>重跑</button> : null}</td></tr>; })}</tbody></table></div></div>
      <div className="grid c2" style={{ marginTop: 16 }}>
        <div className="card"><div className="card-h"><h3>儲存用量</h3><span className="sub">ClickHouse 熱層合計 {fmtBytes(hot)}</span></div>
          <div className="tbl-wrap"><table className="tbl"><thead><tr><th>項目</th><th className="num">列數</th><th className="num">用量</th><th className="num">parts</th></tr></thead><tbody>{bj.storage.map((s) => <tr key={s.name}><td>{s.cn}<span className="sub mono" style={{ overflowWrap: 'anywhere' }}>{s.name}{s.note ? ` · ${s.note}` : ''}</span></td><td className="num mono">{fmtRows(s.rows)}</td><td className="num mono">{fmtBytes(s.bytes)}</td><td className="num mono">{s.parts ?? '—'}</td></tr>)}</tbody></table></div></div>
        <div className="card"><div className="card-h"><h3>作業說明</h3></div><ul className="priv"><li>同步任務由排程器（cron／間隔、依賴、重試與退避、schema 變更策略）執行；「重跑」= 立即執行一次，需 governance.admin，審計 batch.rerun。</li><li>例行作業由 API 背景執行緒跑：同步排程每分鐘、看板定時寄送每分鐘、分群／標籤每 10 分鐘、推送重試與增量每分鐘；失敗寫審計 scheduler.tick.failed，24 小時達 3 次觸發告警。</li><li>Airflow／dbt 的日批作業（70 億歷史載入、觸點表、冷層搬移）第二階段接入同一張表。</li></ul></div>
      </div>
    </>
  );
}

function AlertsTab({ al, sev }: { al: Alerts; sev: ReturnType<typeof countBySeverity> }) {
  return (
    <>
      <div className="card"><div className="card-h"><h3>觸發中</h3><span className="sub">{al.firing.length ? (Object.keys(sev) as (keyof typeof sev)[]).map((k) => `${SEV_CN[k]} ${sev[k]}`).join(' · ') : '沒有觸發中的告警'} · 通知：依下方「告警通知設定」送 Email／LINE（規則的通知角色：{al.notify.join('、')}）</span></div>
        {al.firing.length ? <div className="tbl-wrap"><table className="tbl"><tbody>{al.firing.map((a, i) => <tr key={i} className={a.severity === 'critical' || a.severity === 'high' ? 'warnrow' : ''}><td style={{ width: 70 }}><span className={`pill ${SEV_PILL[a.severity]}`}>{SEV_CN[a.severity]}</span></td><td>{alertText(a)}<span className="sub">{a.text}</span></td></tr>)}</tbody></table></div> : <p className="note">全部規則都在門檻內。</p>}</div>
      <div className="card flush" style={{ marginTop: 16 }}><div className="card-h" style={{ padding: '14px 16px 0' }}><h3>告警規則</h3><span className="sub">backend/ops/alerts.yaml；叢集內由 PrometheusRule 以同名規則送 Alertmanager</span></div>
        <div className="tbl-wrap"><table className="tbl"><thead><tr><th>規則</th><th>指標</th><th>門檻</th><th>對象</th><th>窗口</th><th>嚴重度</th><th>說明</th></tr></thead><tbody>{al.rules.map((r) => <tr key={r.key}><td><b>{r.cn}</b><span className="sub mono">{r.key}</span></td><td className="mono" style={{ fontSize: 12 }}>{r.metric}</td><td className="mono">{ruleThreshold(r)}</td><td>{r.per ?? '全域'}</td><td>{r.window}</td><td><span className={`pill ${SEV_PILL[r.severity]}`}>{SEV_CN[r.severity]}</span></td><td style={{ fontSize: 12.5 }}>{r.text}</td></tr>)}</tbody></table></div></div>
    </>
  );
}

const ROLE_OPTIONS = [['admin', '系統管理員'], ['it_admin', '資訊管理'], ['digital_ops', '數位經營'], ['data_analyst', '數據分析'], ['marketing', '行銷']] as const;
function NotifyCard({ canAdmin }: { canAdmin: boolean }) {
  const [cfg, setCfg] = useState<NotifyConfig | null>(null);
  const [hist, setHist] = useState<Notification[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [extraTo, setExtraTo] = useState(''); const [lineTo, setLineTo] = useState('');
  const load = async () => {
    try { const [c, h] = await Promise.all([api.get<NotifyConfig>('/v1/ops/notify'), api.get<Notification[]>('/v1/ops/notify/history?limit=50')]); setCfg(c); setHist(h); setExtraTo(c.email.extra_to.join('\n')); setLineTo(c.line.to.join('\n')); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? (e.status === 403 ? null : e.message) : '讀取失敗'); }
  };
  useEffect(() => { void load(); }, []);
  if (!canAdmin) return <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>告警通知設定</h3></div><p className="note">Email／LINE 官方帳號通知由系統管理員或資訊管理設定；通知紀錄見審計 ops.notify.sent。</p></div>;
  if (!cfg) return <div className="card" style={{ marginTop: 16 }}><div className="card-h"><h3>告警通知設定</h3></div>{err ? <div className="alert">{err}</div> : <p className="note">讀取中…</p>}</div>;
  const draft = { ...cfg, email: { ...cfg.email, extra_to: splitList(extraTo) }, line: { ...cfg.line, to: splitList(lineTo) } };
  const errs = checkNotify(draft);
  const set = (patch: Partial<NotifyConfig>) => setCfg({ ...cfg, ...patch });
  const save = async () => {
    setBusy(true); setMsg(null);
    try { const c = await api.put<NotifyConfig>('/v1/ops/notify', { email: draft.email, line: draft.line, policy: draft.policy }); setCfg(c); setExtraTo(c.email.extra_to.join('\n')); setLineTo(c.line.to.join('\n')); setMsg('通知設定已儲存（審計 ops.notify.changed）'); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '儲存失敗'); }
    finally { setBusy(false); }
  };
  const test = async (channel: 'email' | 'line') => {
    setBusy(true); setMsg(null);
    try { const r = await api.post<Notification>('/v1/ops/notify/test', { channel }); setMsg(`${channel === 'email' ? 'Email' : 'LINE'} 測試：${r.status === 'ok' ? `已送 ${r.recipients} 個收件對象` : `${r.status}：${r.response ?? ''}`}`); await load(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '測試失敗'); }
    finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-h"><h3>告警通知設定</h3><span className="sub">{cfg.updated_at ? `上次修改 ${fmtTs(cfg.updated_at)} · ${cfg.updated_by}` : '使用預設值（backend/ops/notify.yaml）'}</span></div>
      {err && <div className="alert">{err}</div>}
      {msg && <div className="note ok-note">{msg}</div>}
      <div className="grid c3 notify-form">
        <div>
          <h4 style={{ fontSize: 13, margin: '0 0 6px' }}>Email（SMTP）{cfg.available.email ? <span className="pill blue">可用</span> : <span className="pill gray" title="API 需設定 DMP_SMTP_URL">未設定 SMTP</span>}</h4>
          <label className="chk-row"><input type="checkbox" checked={cfg.email.enabled} onChange={(e) => set({ email: { ...cfg.email, enabled: e.target.checked } })} /> 啟用</label>
          <label>收件角色（從成員目錄取 email）</label>
          <div className="chk-wrap">{ROLE_OPTIONS.map(([k, cn]) => <label key={k} className="chk-row"><input type="checkbox" checked={cfg.email.to_roles.includes(k)} onChange={(e) => set({ email: { ...cfg.email, to_roles: e.target.checked ? [...cfg.email.to_roles, k] : cfg.email.to_roles.filter((r) => r !== k) } })} /> {cn}</label>)}</div>
          <label>額外收件人（值班／群組信箱，一行一個）</label><textarea rows={2} value={extraTo} onChange={(e) => setExtraTo(e.target.value)} placeholder="oncall@example.com" />
          <label>主旨前綴</label><input value={cfg.email.subject_prefix} onChange={(e) => set({ email: { ...cfg.email, subject_prefix: e.target.value } })} />
          <div className="row"><button className="btn-ghost sm" disabled={busy || !cfg.email.enabled || !cfg.available.email} onClick={() => void test('email')}>送測試 Email</button></div>
        </div>
        <div>
          <h4 style={{ fontSize: 13, margin: '0 0 6px' }}>LINE 官方帳號（Messaging API）{cfg.available.line ? <span className="pill blue">token 可解析</span> : <span className="pill gray">未設定</span>}</h4>
          <label className="chk-row"><input type="checkbox" checked={cfg.line.enabled} onChange={(e) => set({ line: { ...cfg.line, enabled: e.target.checked } })} /> 啟用</label>
          <label>Channel access token 的密鑰庫參照（不貼明文）</label><input className="mono" value={cfg.line.channel_access_token_ref} onChange={(e) => set({ line: { ...cfg.line, channel_access_token_ref: e.target.value.trim() } })} placeholder="kv/data/dmp/prod/line#channel_access_token" />
          <label>收件 userId／groupId（一行一個；把官方帳號加進值班群組後由 webhook 事件取得 groupId）</label><textarea rows={2} className="mono" value={lineTo} onChange={(e) => setLineTo(e.target.value)} placeholder="Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" />
          <p className="note" style={{ marginTop: 6 }}>免費方案每月 200 則推送；LINE Notify 已停止服務，不用。多個收件對象用 multicast 一次送。</p>
          <div className="row"><button className="btn-ghost sm" disabled={busy || !cfg.line.enabled || !cfg.available.line} onClick={() => void test('line')}>送測試 LINE</button></div>
        </div>
        <div>
          <h4 style={{ fontSize: 13, margin: '0 0 6px' }}>政策</h4>
          <label>最低嚴重度（以下只在頁面顯示）</label><select value={cfg.policy.min_severity} onChange={(e) => set({ policy: { ...cfg.policy, min_severity: e.target.value as Severity } })}>{(['low', 'medium', 'high', 'critical'] as const).map((s) => <option key={s} value={s}>{SEV_CN[s]}</option>)}</select>
          <label>同一告警冷卻（分鐘）</label><input type="number" min={0} max={1440} value={cfg.policy.cooldown_min} onChange={(e) => set({ policy: { ...cfg.policy, cooldown_min: Number(e.target.value) } })} />
          <label className="chk-row"><input type="checkbox" checked={cfg.policy.notify_resolved} onChange={(e) => set({ policy: { ...cfg.policy, notify_resolved: e.target.checked } })} /> 告警消失時送「已恢復」</label>
          <label className="chk-row"><input type="checkbox" checked={!!cfg.policy.quiet_hours} onChange={(e) => set({ policy: { ...cfg.policy, quiet_hours: e.target.checked ? { start: 0, end: 7, tz: 'Asia/Taipei' } : null } })} /> 靜音時段只送「緊急」</label>
          {cfg.policy.quiet_hours && <div className="row"><input type="number" min={0} max={23} value={cfg.policy.quiet_hours.start} onChange={(e) => set({ policy: { ...cfg.policy, quiet_hours: { ...cfg.policy.quiet_hours!, start: Number(e.target.value) } } })} style={{ width: 70 }} /> 時 ～ <input type="number" min={0} max={23} value={cfg.policy.quiet_hours.end} onChange={(e) => set({ policy: { ...cfg.policy, quiet_hours: { ...cfg.policy.quiet_hours!, end: Number(e.target.value) } } })} style={{ width: 70 }} /> 時（Asia/Taipei）</div>}
          <p className="note" style={{ marginTop: 6 }}>叢集內 Prometheus／Alertmanager 的告警經 webhook 走同一組管道與政策（Helm AlertmanagerConfig）。</p>
        </div>
      </div>
      {errs.length > 0 && <div className="alert" style={{ marginTop: 10 }}>{errs.join('；')}</div>}
      <div className="row"><button className="btn-primary" disabled={busy || errs.length > 0} onClick={() => void save()}>儲存設定</button><button className="btn-ghost" disabled={busy} onClick={() => void load()}>還原</button></div>
      <div className="card-h" style={{ marginTop: 16 }}><h3>通知紀錄</h3><span className="sub">最近 50 筆</span></div>
      {hist.length ? <div className="tbl-wrap"><table className="tbl"><thead><tr><th>時間</th><th>管道</th><th>類型</th><th>告警</th><th className="num">收件</th><th>狀態</th><th>回應</th></tr></thead>
        <tbody>{hist.map((n) => <tr key={n.id} className={n.status === 'failed' ? 'warnrow' : ''}><td className="mono" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{fmtTs(n.at)}</td><td>{n.channel === 'email' ? 'Email' : 'LINE'}</td><td>{NTF_KIND_CN[n.kind] ?? n.kind}</td><td style={{ fontSize: 12 }} title={n.text}>{n.alerts.slice(0, 3).join('、')}{n.alerts.length > 3 ? ` 等 ${n.alerts.length} 項` : ''}</td><td className="num mono">{n.recipients}</td><td><span className={`pill ${NTF_STATUS_PILL[n.status] ?? 'gray'}`}>{n.status}</span></td><td className="sub" style={{ maxWidth: 260, overflowWrap: 'anywhere' }}>{n.response ?? ''}</td></tr>)}</tbody></table></div> : <p className="note">還沒有通知。</p>}
    </div>
  );
}
