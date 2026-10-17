// 一張卡片：POST /v1/dashboards/{id}/cards/{card}/data（以檢視者身分重算）→ 依 kind／chart 畫圖；錯誤與抑制照分析頁規則。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import { shareSeries } from '@/analytics/AttributionPage';
import { FunnelChart } from '@/analytics/FunnelChart';
import { LineChart } from '@/analytics/LineChart';
import { PathChart } from '@/analytics/PathChart';
import { RetentionMatrix } from '@/analytics/RetentionPage';
import { fmtNum, groupLabel, toSeries } from '@/analytics/series';
import type { AttributionResult, EventsResult, FunnelRow, RetentionRow } from '@/analytics/types';
import { firstGroup, metricLabels, tableColumns, timeText } from './dashboards';
import type { CardData, Dashboard, DashboardCard } from './types';

export function CardView({ dash, card, k, labelOf, reloadKey, onRemove, onRename }: { dash: Dashboard; card: DashboardCard; k: number; labelOf: (n: string) => string; reloadKey: number; onRemove?: () => void; onRename?: () => void }) {
  const [data, setData] = useState<CardData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [showSql, setShowSql] = useState(false);
  useEffect(() => {
    let alive = true; setBusy(true); setErr(null);
    api.post<CardData>(`/v1/dashboards/${dash.id}/cards/${card.id}/data`).then((d) => { if (alive) setData(d); }).catch((e: Error) => { if (alive) setErr(e instanceof ApiError ? e.message : '查詢失敗'); }).finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [dash.id, card.id, dash.time.preset, reloadKey]);
  return (
    <div className={`card dash-card w${card.width}`}>
      <div className="card-h">
        <h3>{card.title}</h3>
        <span className="sub">{data ? `${timeText(data.time, dash.time.preset)} · ${data.cost.ms} ms` : busy ? '載入中…' : ''}</span>
        <div className="right row" style={{ marginTop: 0, gap: 4 }}>
          {data && <button className="btn-ghost sm" onClick={() => setShowSql((v) => !v)} title="SQL 與血緣">SQL</button>}
          {onRename && <button className="btn-ghost sm" onClick={onRename}>改名</button>}
          {onRemove && <button className="btn-ghost sm danger" onClick={onRemove}>移除</button>}
        </div>
      </div>
      {err && <div className="alert">{err}</div>}
      {data && data.warnings.length > 0 && <div className="note">{data.warnings.join('；')}</div>}
      {data && <CardBody card={card} data={data} k={k} labelOf={labelOf} />}
      {card.note && <p className="note">{card.note}</p>}
      {showSql && data && <pre className="code" style={{ marginTop: 10 }}>{data.lineage.join(' · ')}{'\n'}{data.sql}</pre>}
    </div>
  );
}

function CardBody({ card, data, k, labelOf }: { card: DashboardCard; data: CardData; k: number; labelOf: (n: string) => string }) {
  if (card.kind === 'events') {
    const { aliases, labels } = metricLabels(card.query);
    const res = data as unknown as EventsResult;
    const spec = String(data.chart_spec.type ?? 'line');
    const bucketed = data.rows.length > 0 && aliases.some((a) => data.rows.every((r) => r[a] === null || (typeof r[a] === 'string' && !Number.isFinite(Number(r[a])))));   // 遮罩分桶（例：金額 ≥5000000）→ 自動改表格
    const chart = card.chart === 'auto' ? (bucketed ? 'table' : spec) : card.chart;
    if (chart === 'table') return <DataTable data={data} labels={labels} />;
    if (chart === 'stat') {
      const r = data.rows[0] as Record<string, unknown> | undefined;
      return <div className="tiles">{aliases.map((a) => <div className="tile" key={a}><span className="sub">{labels[a]}</span><b>{r?.suppressed ? `< ${k}` : fmtNum(r?.[a] as number)}</b></div>)}</div>;
    }
    return <LineChart series={toSeries(res, aliases, labels)} kind={chart === 'bar' || spec === 'bar' ? 'bar' : 'line'} height={220} />;
  }
  if (card.kind === 'funnel') {
    const g = firstGroup(data.rows as unknown as FunnelRow[]);
    return <>{g.groups > 1 && <p className="note">共 {g.groups} 組，只顯示 {g.key}；完整請到漏斗分析。</p>}<FunnelChart rows={g.rows} k={k} /></>;
  }
  if (card.kind === 'retention') {
    const rows = data.rows as unknown as RetentionRow[];
    const unit = String(card.query.unit ?? 'day') as 'day' | 'week' | 'month';
    return <RetentionMatrix rows={rows.filter((r) => r.g1 == null || r.g1 === rows[0]?.g1)} k={k} display="rate" unit={unit} />;
  }
  if (card.kind === 'path') {
    return <>
      <span className="sub">起點用戶 {fmtNum(data.anchor_users ?? 0)}{data.sampled ? `（抽樣 ${data.sampled}）` : ''}</span>
      <PathChart nodes={data.nodes ?? []} links={data.links ?? []} labelOf={labelOf} />
    </>;
  }
  const res = data as unknown as AttributionResult;
  return <>
    {data.summary && <div className="tiles" style={{ marginBottom: 10 }}>
      <div className="tile"><span className="sub">轉化次數</span><b>{fmtNum(data.summary.conversions)}</b></div>
      <div className="tile"><span className="sub">有觸點的轉化</span><b>{fmtNum(data.summary.touched)}</b></div>
      <div className="tile"><span className="sub">轉化價值</span><b>{fmtNum(data.summary.value)}</b></div>
    </div>}
    <LineChart series={shareSeries(res)} kind="bar" height={220} />
  </>;
}

function DataTable({ data, labels }: { data: CardData; labels: Record<string, string> }) {
  const cols = tableColumns(data.columns, data.rows);
  return (
    <div className="tbl-wrap"><table className="tbl">
      <thead><tr>{cols.map((c) => <th key={c}>{c === 'bucket' ? '時間' : labels[c] ?? c}</th>)}</tr></thead>
      <tbody>{data.rows.slice(0, 50).map((r, i) => <tr key={i} className={r.suppressed ? 'warnrow' : ''}>{cols.map((c) => <td key={c} className={typeof r[c] === 'number' ? 'num mono' : 'mono'}>{c.startsWith('g') && c.length === 2 ? groupLabel({ [c]: r[c] as string }) : typeof r[c] === 'number' ? fmtNum(r[c] as number) : String(r[c] ?? '—')}</td>)}</tr>)}</tbody>
    </table></div>
  );
}
