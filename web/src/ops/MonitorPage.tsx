// 系統管理 › 系統監控（系統管理員、資訊管理；governance.admin）：Grafana 看板同一組面板（backend/ops/panels.yaml）在平台內看——
// API 每分鐘快照程序內指標成時間序列（程序重啟即清空）；每個面板附 PromQL；設定 DMP_GRAFANA_URL 時每個面板可直接開到 Grafana 同一面板。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Alert, Anchor, Button, Code, Collapse, Group, NativeSelect, Paper, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { LineChart } from '@/analytics/LineChart';
import { RequireGrant } from '@/auth/RequireGrant';
import { PageHeader, SectionTitle, StatTile, StatusBadge } from '@/ds';
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
      <PageHeader title="系統監控" lead="Grafana 看板看得到的指標在這裡直接看：平台（請求率、P95、5xx）、資料流（來源延遲、同步、隔離率、事件時效、例行作業）、查詢（各模組 P95、次數、錯誤率）、推送與儲存。每個面板附 PromQL；API 每分鐘快照一次、保留 24 小時，程序重啟即清空，長期保存與告警通知以 Prometheus／Grafana 為準。"
        actions={<>
          <NativeSelect aria-label="監控期間" value={String(hours)} data={HOURS.map((h) => ({ value: String(h), label: `近 ${h} 小時` }))} onChange={(e) => setHours(Number(e.target.value) as (typeof HOURS)[number])} />
          {g?.url ? <Button component="a" href={`${g.url}/d/${g.dashboard_uid}?from=now-${hours}h&to=now`} target="_blank" rel="noreferrer">在 Grafana 開啟看板</Button> : <StatusBadge tone="gray"><span title="設定 DMP_GRAFANA_URL 後每個面板可連到 Grafana 同一面板">Grafana 未連結</span></StatusBadge>}
          <Button onClick={() => setTick((t) => t + 1)}>重新整理</Button>
        </>} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}
      {data && <Text size="sm" c="dimmed" mb="md">快照 {data.points} 筆（每 {data.interval_s} 秒）· {data.from.replace('T', ' ').slice(0, 16)} ～ {data.to.replace('T', ' ').slice(0, 16)}{data.points < 3 ? ' · 快照還不夠多，折線要等幾分鐘才看得出趨勢（本機需 DMP_TICK=1）' : ''}</Text>}
      {!data ? <Text size="sm" c="dimmed">讀取中…</Text> : <Stack gap="lg">{data.sections.map((sec) => (
        <section key={sec.key}>
          <Title order={2} mb="sm">{sec.cn}</Title>
          <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }}>{data.panels.filter((p) => p.section === sec.key).map((p) => <PanelCard key={p.id} p={p} hours={hours} grafana={g ?? null} />)}</SimpleGrid>
        </section>
      ))}</Stack>}
    </div>
  );
}

/** 面板頁尾：PromQL 可展開；有 Grafana 連結時可開到同一面板。 */
function PanelFooter({ expr, link }: { expr: string; link: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <Stack gap={4} mt="xs">
      <Group gap="xs"><Button size="compact-xs" variant="subtle" color="gray" onClick={() => setOpen((o) => !o)} aria-expanded={open}>PromQL</Button>{link && <Anchor href={link} target="_blank" rel="noreferrer" size="xs">在 Grafana 開啟此面板</Anchor>}</Group>
      <Collapse in={open}><Code block style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 11 }}>{expr}</Code></Collapse>
    </Stack>
  );
}

function PanelCard({ p, hours, grafana }: { p: Panel; hours: number; grafana: OpsSeries['grafana'] | null }) {
  const link = grafanaPanelUrl(grafana?.url ?? null, grafana?.dashboard_uid ?? 'dmp-overview', p.id, hours);
  const unit = displayUnit(p.unit);
  if (p.kind === 'stat') {
    const vals = latestValues(p);
    return (
      <Paper><SectionTitle title={p.cn} />
        {vals.length ? <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">{vals.map((v) => { const s = thresholdStatus(v.v, p.thresholds); return <StatTile key={v.name} label={v.name} value={fmtStat(v.v, p.unit)} hint={s !== 'ok' && s !== 'unknown' ? <StatusBadge tone={STATUS_PILL[s]}>{STATUS_CN[s]}</StatusBadge> : undefined} />; })}</SimpleGrid> : <Text size="sm" c="dimmed">尚無資料</Text>}
        <PanelFooter expr={p.expr} link={link} /></Paper>
    );
  }
  const { series, dropped } = toChartSeries(p);
  const hasData = series.some((s) => s.points.some((q) => q.y != null));
  return (
    <Paper><SectionTitle title={p.cn} right={<Group gap="xs">{unit.label && <Text size="xs" c="dimmed">{unit.label}</Text>}{p.thresholds.length > 0 && <Text size="xs" c="dimmed">門檻 {p.thresholds.map((t) => fmtStat(t, p.unit)).join(' / ')}</Text>}</Group>} />
      {hasData ? <LineChart series={series} height={200} /> : <Text size="sm" c="dimmed">尚無資料（等下一次快照）</Text>}
      {dropped > 0 && <Text size="xs" c="dimmed">另有 {dropped} 條序列未畫（Grafana 看全部）</Text>}
      <PanelFooter expr={p.expr} link={link} /></Paper>
  );
}
