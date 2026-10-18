// 留存分析：初始行為、回訪行為（可任一事件）、四模型、日／週／月、期數、含當期、分組 → POST /v1/analytics/retention。
// 結果：同期群 × 期矩陣（單一色相深淺 = 留存比例；未過的期數灰階「—」；K 匿名抑制「< K」）；分組時每組一張矩陣；SQL 可展開。設計系統元件組合。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Code, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { SaveCardButton } from '@/dashboards/SaveCardButton';
import { useBookmarkOpen } from '@/dashboards/useBookmarkOpen';
import { Mono, PageHeader, SectionTitle } from '@/ds';
import { dedupe } from './FunnelPage';
import type { Catalog, RetentionQuery, RetentionResult, RetentionRow } from './types';

const MODELS: [RetentionQuery['model'], string, string][] = [['date', '第 N 期留存', '第 N 期當期有回訪'], ['interval', 'N 期內留存', '第 1–N 期內任一期有回訪'], ['continuous', '連續留存', '第 1–N 期每期都有回訪'], ['churn', '流失', '第 N 期起到最後一期都沒有回訪']];

export function RetentionPage() {
  return <RequireGrant module="events" action="use"><RetentionInner /></RequireGrant>;
}

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
const opt = (value: string, label: string) => ({ value, label });

function RetentionInner() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState<RetentionQuery>({ initial: { event: '' }, return: { event: '' }, model: 'date', unit: 'day', periods: 7, include_day0: false, time: { from: today(-14), to: today() } });
  const [res, setRes] = useState<RetentionResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const bm = useBookmarkOpen<typeof q>('retention', setQ);
  const [display, setDisplay] = useState<'rate' | 'users'>('rate');
  useEffect(() => { api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message)); }, []);
  const run = async () => {
    setBusy(true); setErr(null);
    try { const body = { ...q, return: q.return?.event ? q.return : undefined }; setRes(await api.post<RetentionResult>('/v1/analytics/retention', body)); setSent(body); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (bm.pending && cat) { bm.done(); void run(); } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bm.pending, cat]);
  const k = me?.scope.k_anonymity ?? 20;
  const groups = useMemo(() => {
    if (!res) return [] as [string, RetentionRow[]][];
    const m = new Map<string, RetentionRow[]>();
    for (const r of res.rows) { const key = r.g1 ?? ''; m.set(key, [...(m.get(key) ?? []), r]); }
    return Array.from(m.entries());
  }, [res]);
  const dimOptions = dedupe(cat ? [...cat.event_columns.filter((c) => c.name !== 'amount' && c.name !== '$event_hour').map((c) => ({ v: c.name, cn: c.cn })), ...cat.dimensions.map((d) => ({ v: d.name, cn: d.cn })), ...cat.user_props.filter((u) => u.groupable).map((u) => ({ v: `user.${u.key}`, cn: `用戶：${u.cn}` }))] : []);
  const unitCn = { day: '日', week: '週', month: '月' }[q.unit];
  const events = (cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`));
  return (
    <div className="page">
      <PageHeader title="留存分析" lead={`依初始行為把用戶分成同期群，看之後每一${unitCn}的回訪比例。四種模型、日／週／月、可依分群或維度比較；同期群人數少於 ${k} 人的列會被抑制。`} />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" className="ds-split">
          <Paper>
            <Stack gap="md">
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <NativeSelect label="初始行為" value={q.initial.event} onChange={(e) => setQ({ ...q, initial: { ...q.initial, event: e.target.value } })} aria-label="初始行為" data={[opt('', '選事件…'), ...events]} />
                <NativeSelect label="回訪行為" value={q.return?.event ?? ''} onChange={(e) => setQ({ ...q, return: { event: e.target.value } })} aria-label="回訪行為" data={[opt('', '任一事件'), ...events]} />
                <NativeSelect label="模型" value={q.model} data={MODELS.map(([v, cn]) => opt(v, cn))} onChange={(e) => setQ({ ...q, model: e.target.value as RetentionQuery['model'] })} />
                <NativeSelect label="粒度" value={q.unit} data={[opt('day', '日'), opt('week', '週'), opt('month', '月')]} onChange={(e) => setQ({ ...q, unit: e.target.value as RetentionQuery['unit'] })} />
                <TextInput label="期數" type="number" min={1} max={90} value={q.periods} onChange={(e) => setQ({ ...q, periods: Number(e.target.value) })} />
                <NativeSelect label="含當期" value={q.include_day0 ? '1' : '0'} data={[opt('0', '不含'), opt('1', '含第 0 期')]} onChange={(e) => setQ({ ...q, include_day0: e.target.value === '1' })} />
                <NativeSelect label="分組" value={q.group_by?.[0] ?? ''} data={[opt('', '不分組'), ...dimOptions.map((o) => opt(o.v, o.cn))]} onChange={(e) => setQ({ ...q, group_by: e.target.value ? [e.target.value] : undefined })} />
                <TextInput label="同期群開始" type="date" value={q.time.from} onChange={(e) => setQ({ ...q, time: { ...q.time, from: e.target.value } })} />
                <TextInput label="同期群結束" type="date" value={q.time.to} onChange={(e) => setQ({ ...q, time: { ...q.time, to: e.target.value } })} />
                <TextInput label="分群" placeholder="segment id（選填）" value={q.segment ?? ''} onChange={(e) => setQ({ ...q, segment: e.target.value || undefined })} />
              </SimpleGrid>
              <Group gap="xs">
                <Button variant="filled" disabled={!q.initial.event || busy} onClick={() => void run()}>{busy ? '查詢中…' : '查詢'}</Button>
                {res && <>
                  <SegmentedControl size="xs" value={display} onChange={(v) => setDisplay(v as 'rate' | 'users')} data={[{ value: 'rate', label: '比例' }, { value: 'users', label: '人數' }]} />
                  <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>
                  <SaveCardButton kind="retention" query={sent} />
                </>}
                {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
              </Group>
            </Stack>
          </Paper>
          <Paper><SectionTitle title="模型說明" />
            <List size="sm" spacing="xs" c="dimmed">
              {MODELS.map(([v, cn, d]) => <List.Item key={v}><b>{cn}</b>：{d}</List.Item>)}
              <List.Item>同一用戶在每個做過初始行為的{unitCn}都算一次同期群；回訪可選「任一事件」。</List.Item>
              <List.Item>尚未過完的期數顯示「—」；結果附 SQL 與血緣，查詢寫審計。</List.Item>
            </List></Paper>
        </SimpleGrid>
        {res && (
          <>
            {res.warnings.length > 0 && <Text size="sm" c="dimmed">{res.warnings.join('；')}</Text>}
            {groups.map(([g, rows]) => (
              <Paper key={g}>
                <SectionTitle title={g || '整體'}><Text size="xs" c="dimmed">{res.cost.ms} ms · {rows.length} 列</Text></SectionTitle>
                <RetentionMatrix rows={rows} k={k} display={display} unit={q.unit} onCell={can('users', 'use') ? (r, retained) => nav('/users', { state: { source: { type: 'retention', query: q, cohort: r.cohort, period: r.period, retained, group: r.g1, label: `留存「${q.initial.event}」${r.cohort} 同期群第 ${r.period} 期${retained ? '留存' : '未留存'}${r.g1 != null ? ` · ${r.g1}` : ''}` } } }) : undefined} />
              </Paper>
            ))}
            {showSql && <Paper><SectionTitle title="SQL"><Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text></SectionTitle><Code block>{res.sql}</Code></Paper>}
          </>
        )}
      </Stack>
    </div>
  );
}

/** 同期群 × 期矩陣：單一色相（品牌藍）深淺代表比例（順序色），未過期數灰階，抑制列顯示 < K。顏色之外同時印數字，不靠顏色辨識。 */
export function RetentionMatrix({ rows, k, display, unit, onCell }: { rows: RetentionRow[]; k: number; display: 'rate' | 'users'; unit: 'day' | 'week' | 'month'; onCell?: (r: RetentionRow, retained: boolean) => void }) {
  const periods = Array.from(new Set(rows.map((r) => r.period))).sort((a, b) => a - b);
  const cohorts = Array.from(new Set(rows.map((r) => r.cohort)));
  const byKey = new Map(rows.map((r) => [`${r.cohort}|${r.period}`, r]));
  const unitCn = { day: '天', week: '週', month: '月' }[unit];
  return (
    <Table.ScrollContainer minWidth={Math.max(360, 180 + periods.length * 72)}>
      <Table highlightOnHover={false}>
        <Table.Thead><Table.Tr><Table.Th>同期群</Table.Th><Table.Th ta="right">人數</Table.Th>{periods.map((p) => <Table.Th key={p} ta="center">第 {p} {unitCn}</Table.Th>)}</Table.Tr></Table.Thead>
        <Table.Tbody>{cohorts.map((c) => {
          const first = byKey.get(`${c}|${periods[0]}`);
          return (
            <Table.Tr key={c}><Table.Td><Mono>{c}</Mono></Table.Td><Table.Td ta="right"><Mono>{first?.suppressed ? `< ${k}` : (first?.users ?? 0).toLocaleString()}</Mono></Table.Td>
              {periods.map((p) => { const r = byKey.get(`${c}|${p}`); const clickable = !!onCell && !!r && !r.suppressed && r.complete; return <Table.Td key={p} ta="center" miw={64} style={{ ...cellStyle(r), cursor: clickable ? 'pointer' : undefined }} title={clickable ? '點：留存用戶；Shift+點：未留存用戶' : undefined} onClick={clickable ? (e) => onCell?.(r as RetentionRow, !e.shiftKey) : undefined}><Mono>{cellText(r, k, display)}</Mono></Table.Td>; })}
            </Table.Tr>);
        })}</Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function cellText(r: RetentionRow | undefined, k: number, display: 'rate' | 'users'): string {
  if (!r) return '';
  if (r.suppressed) return `< ${k}`;
  if (!r.complete || r.rate === null || r.rate === undefined) return '—';
  return display === 'rate' ? `${(r.rate * 100).toFixed(1)}%` : (r.retained ?? 0).toLocaleString();
}

export function cellStyle(r: RetentionRow | undefined): { background?: string; color?: string } {
  if (!r || r.suppressed || !r.complete || r.rate === null || r.rate === undefined) return { background: 'var(--soft)', color: 'var(--muted)' };
  const a = 0.08 + Math.min(1, r.rate) * 0.82;            // 順序色：同一色相，透明度隨比例
  return { background: `color-mix(in srgb, var(--brand) ${Math.round(a * 100)}%, var(--panel))`, color: a > 0.55 ? '#fff' : 'var(--ink)' };
}
