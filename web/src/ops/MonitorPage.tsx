// 系統管理 › 系統監控（系統管理員、資訊管理；governance.admin）：Grafana 看板同一組面板（backend/ops/panels.yaml）在平台內看——
// API 每分鐘快照程序內指標成時間序列（程序重啟即清空）；每個面板附 PromQL；設定 DMP_GRAFANA_URL 時每個面板可直接開到 Grafana 同一面板。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { LineChart } from '@/analytics/LineChart';
import { RequireGrant } from '@/auth/RequireGrant';
import { displayUnit, fmtStat, grafanaPanelUrl, latestValues, thresholdStatus, toChartSeries, type OpsSeries, type Panel } from './monitor';
import { STATUS_CN, STATUS_PILL } from './ops';

export function MonitorPage() {
  return <RequireGrant module="governance" action="admin"><MonitorInner /></RequireGrant>;
}

const HOURS = [1, 6, 24] as const;
function MonitorInner() {
  const [hours, setHours] = useState<(typeof HOURS)[number]>(6);
  const [data, setData] = useState<OpsSeries | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { api.get<OpsSeries>(`/v1/ops/series?hours=${hours}`).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, [hours, tick]);
  useEffect(() => { const id = setInterval(() => setTick((t) => t + 1), 60_000); return () => clearInterval(id); }, []);
  const g = data?.grafana;
  return (
    <div className="page">
      <div className="page-h"><div><h1>系統監控</h1><p>Grafana 看板看得到的指標在這裡直接看：平台（請求率、P95、5xx）、資料流（來源延遲、同步、隔離率、事件時效、例行作業）、查詢（各模組 P95、次數、錯誤率）、推送與儲存。每個面板附 PromQL；API 每分鐘快照一次、保留 24 小時，程序重啟即清空，長期保存與告警通知以 Prometheus／Grafana 為準。</p></div>
        <div className="row" style={{ marginTop: 0 }}>
          <select value={hours} onChange={(e) => setHours(Number(e.target.value) as (typeof HOURS)[number])}>{HOURS.map((h) => <option key={h} value={h}>近 {h} 小時</option>)}</select>
          {g?.url ? <a className="btn-ghost" href={`${g.url}/d/${g.dashboard_uid}?from=now-${hours}h&to=now`} target="_blank" rel="noreferrer">在 Grafana 開啟看板</a> : <span className="pill gray" title="設定 DMP_GRAFANA_URL 後每個面板可連到 Grafana 同一面板">Grafana 未連結</span>}
          <button className="btn-ghost" onClick={() => setTick((t) => t + 1)}>重新整理</button>
        </div></div>
      {err && <div className="alert">{err}</div>}
      {data && <p className="note" style={{ marginTop: 0, marginBottom: 12 }}>快照 {data.points} 筆（每 {data.interval_s} 秒）· {data.from.replace('T', ' ').slice(0, 16)} ～ {data.to.replace('T', ' ').slice(0, 16)}{data.points < 3 ? ' · 快照還不夠多，折線要等幾分鐘才看得出趨勢（本機需 DMP_TICK=1）' : ''}</p>}
      {!data ? <p className="note">讀取中…</p> : data.sections.map((sec) => (
        <section key={sec.key} style={{ marginBottom: 20 }}>
          <h2 style={{ fontSize: 15, margin: '0 0 10px' }}>{sec.cn}</h2>
          <div className="grid mon-grid">{data.panels.filter((p) => p.section === sec.key).map((p) => <PanelCard key={p.id} p={p} hours={hours} grafana={g ?? null} />)}</div>
        </section>
      ))}
    </div>
  );
}

function PanelCard({ p, hours, grafana }: { p: Panel; hours: number; grafana: OpsSeries['grafana'] | null }) {
  const link = grafanaPanelUrl(grafana?.url ?? null, grafana?.dashboard_uid ?? 'dmp-overview', p.id, hours);
  const unit = displayUnit(p.unit);
  const footer = (
    <details className="mon-expr"><summary>PromQL{link ? ' · ' : ''}{link && <a href={link} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>在 Grafana 開啟此面板</a>}</summary><code>{p.expr}</code></details>
  );
  if (p.kind === 'stat') {
    const vals = latestValues(p);
    return (
      <div className={`card mon-card ${p.kind}`}><div className="card-h"><h3>{p.cn}</h3></div>
        {vals.length ? <div className="tiles" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))' }}>{vals.map((v) => { const s = thresholdStatus(v.v, p.thresholds); return <div key={v.name} className={`tile st-${s}`}><span className="sub">{v.name}</span><b style={{ fontSize: 20 }}>{fmtStat(v.v, p.unit)}</b>{s !== 'ok' && s !== 'unknown' && <span className={`pill ${STATUS_PILL[s]}`}>{STATUS_CN[s]}</span>}</div>; })}</div> : <p className="note">尚無資料</p>}
        {footer}</div>
    );
  }
  const { series, dropped } = toChartSeries(p);
  const hasData = series.some((s) => s.points.some((q) => q.y != null));
  return (
    <div className="card mon-card"><div className="card-h"><h3>{p.cn}</h3>{unit.label && <span className="sub">{unit.label}</span>}{p.thresholds.length > 0 && <span className="sub">門檻 {p.thresholds.map((t) => fmtStat(t, p.unit)).join(' / ')}</span>}</div>
      {hasData ? <LineChart series={series} height={200} /> : <p className="note">尚無資料（等下一次快照）</p>}
      {dropped > 0 && <p className="sub">另有 {dropped} 條序列未畫（Grafana 看全部）</p>}
      {footer}</div>
  );
}
