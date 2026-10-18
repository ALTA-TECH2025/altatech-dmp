// 自定義 SQL（docs/21 §3）：左側表目錄（點欄位插入；脫敏欄位標示不可用）、編輯器（Ctrl+Enter 執行；{{參數}}）、結果（表格／折線／長條、複製前 100 列、下載 CSV）、
// 存為書籤（kind=sql，可加入看板）、已存查詢、查詢記錄、中間表（資訊管理／系統管理員）。唯讀：後端沙盒只放行 SELECT／WITH。設計系統元件組合。
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Alert, Anchor, Button, Code, Collapse, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Textarea, UnstyledButton } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { LineChart } from '@/analytics/LineChart';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import type { Bookmark, DashboardSummary } from '@/dashboards/types';
import { Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';
import { fmtTs, numericColumns, paramNames, toCsv } from './sql';
import type { MidTable, SqlCatalog, SqlResult, SqlRun, SqlSaved } from './types';

export function SqlPage() {
  return <RequireGrant module="events"><Inner /></RequireGrant>;
}

const opt = (value: string, label: string) => ({ value, label });
const MULTI_STYLES = { input: { height: 'auto', minHeight: 52, paddingBlock: 4 } } as const;   // 多選的原生 select 需要比單行高
const SIDE_TABS = [opt('tables', '數據表'), opt('saved', '已存'), opt('history', '記錄'), opt('mid', '中間表')];
const VIEW_TABS = [opt('table', '表格'), opt('line', '折線'), opt('bar', '長條')];

function Inner() {
  const { can } = useAuth();
  const [cat, setCat] = useState<SqlCatalog | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [sql, setSql] = useState('');
  const [params, setParams] = useState<Record<string, string>>({});
  const [res, setRes] = useState<SqlResult | null>(null);
  const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [side, setSide] = useState<'tables' | 'saved' | 'history' | 'mid'>('tables');
  const [view, setView] = useState<'table' | 'line' | 'bar'>('table');
  const [xCol, setXCol] = useState(''); const [yCols, setYCols] = useState<string[]>([]);
  const [openTable, setOpenTable] = useState<string | null>(null);
  const [saved, setSaved] = useState<SqlSaved[]>([]); const [history, setHistory] = useState<SqlRun[]>([]); const [mids, setMids] = useState<MidTable[]>([]);
  const [saveForm, setSaveForm] = useState<{ name: string; group: string; roles: string[] } | null>(null);
  const [bmForm, setBmForm] = useState<{ name: string; targets: string[]; dashboards: DashboardSummary[] } | null>(null);
  const [midForm, setMidForm] = useState<{ name: string; note: string } | null>(null);
  const [showEffective, setShowEffective] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  const loc = useLocation();
  const names = useMemo(() => paramNames(sql), [sql]);
  const canMid = can('governance', 'admin') || can('integration', 'admin');

  const loadSide = async () => {
    try { const [s, h, m] = await Promise.all([api.get<SqlSaved[]>('/v1/sql/saved'), api.get<SqlRun[]>('/v1/sql/history'), api.get<MidTable[]>('/v1/sql/mid-tables')]); setSaved(s); setHistory(h); setMids(m); } catch { /* 側欄載入失敗不擋主流程 */ }
  };
  useEffect(() => {
    const bm = (loc.state as { bookmark?: Bookmark } | null)?.bookmark;
    if (bm?.kind === 'sql' && typeof bm.query.sql === 'string') { setSql(bm.query.sql); setParams(Object.fromEntries(Object.entries((bm.query.params as Record<string, unknown>) ?? {}).map(([k, v]) => [k, String(v)]))); }
    api.get<SqlCatalog>('/v1/sql/tables').then((c) => { setCat(c); if (!sql && !bm && c.examples[0]) setSql(c.examples[0].sql); }).catch((e) => setDenied(e instanceof ApiError ? e.message : '無法載入表目錄'));
    void loadSide();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const insert = (text: string) => {
    const el = editor.current; if (!el) { setSql((s) => s + text); return; }
    const a = el.selectionStart ?? sql.length; const b = el.selectionEnd ?? a; const next = sql.slice(0, a) + text + sql.slice(b); setSql(next);
    requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = a + text.length; });
  };
  const run = async (explain = false) => {
    if (!sql.trim()) return; setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await api.post<SqlResult>('/v1/sql/query', { sql, params: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])), explain });
      setRes(r); if (!explain) { const num = numericColumns(r.columns, r.rows); setXCol(r.columns[0] ?? ''); setYCols(num.slice(0, 3)); setView('table'); void loadSide(); }
      else setMsg(`解析通過：掃描估算 ${r.scan_rows_estimate == null ? '—' : r.scan_rows_estimate.toLocaleString()} 列；血緣 ${r.lineage.join('、')}`);
    } catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); if (!explain) void loadSide(); } finally { setBusy(false); }
  };
  const copy = () => { if (res) void navigator.clipboard?.writeText(toCsv(res.columns, res.rows, 100)).then(() => setMsg('已複製前 100 列（CSV）')); };
  const save = async () => {
    if (!saveForm) return;
    try { await api.post('/v1/sql/saved', { name: saveForm.name, group: saveForm.group, sql, params_default: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])), shared: { roles: saveForm.roles, users: [] } }); setSaveForm(null); setMsg('已儲存查詢'); void loadSide(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '儲存失敗'); }
  };
  const openBm = async () => { const ds = (await api.get<DashboardSummary[]>('/v1/dashboards').catch(() => [] as DashboardSummary[])).filter((d) => d.editable); setBmForm({ name: saved.find((s) => s.sql === sql)?.name ?? 'SQL 書籤', targets: [], dashboards: ds }); };
  const saveBm = async () => {
    if (!bmForm) return;
    try { const b = await api.post<Bookmark>('/v1/bookmarks', { name: bmForm.name, kind: 'sql', query: { sql, params: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])) }, chart: 'auto', add_to: bmForm.targets }); setBmForm(null); setMsg(`已存書籤「${b.name}」${b.added_to?.length ? `，並加入 ${b.added_to.map((a) => a.name).join('、')}` : ''}`); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '存書籤失敗'); }
  };
  const createMid = async () => {
    if (!midForm) return;
    try { const m = await api.post<MidTable>('/v1/sql/mid-tables', { name: midForm.name, sql, note: midForm.note, params_default: Object.fromEntries(names.map((n) => [n, params[n] ?? ''])) }); setMidForm(null); setMsg(`已建立中間表 dmp.${m.name}（${m.rows ?? '—'} 列）`); void loadSide(); const c = await api.get<SqlCatalog>('/v1/sql/tables'); setCat(c); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '建表失敗'); }
  };
  const series = useMemo(() => (res && view !== 'table' && xCol ? yCols.map((y) => ({ key: y, label: y, metric: y, group: '', points: res.rows.map((r) => ({ x: String(r[xCol] ?? ''), y: Number.isFinite(Number(r[y])) && r[y] !== null && r[y] !== '' ? Number(r[y]) : null, suppressed: false })) })) : []), [res, view, xCol, yCols]);

  if (denied) return <div className="page"><PageHeader title="自定義 SQL" /><Alert color="red" variant="light" mb="md">{denied}</Alert><Text size="sm" c="dimmed">自定義 SQL 只開放數據分析、資訊管理與系統管理員；一般查數請用<Anchor component={Link} to="/events" size="sm">事件分析</Anchor>。</Text></div>;
  return (
    <div className="page">
      <PageHeader title="自定義 SQL" lead={`唯讀 SELECT／WITH 直接查 ClickHouse 熱層：只能引用你有權限的表，涉及脫敏欄位（L3／L4）會直接報錯；預覽 ${cat?.limits.preview_rows ?? 1000} 列、${cat?.limits.timeout_s ?? 60} 秒逾時、每人 ${cat?.limits.concurrent_per_user ?? 5} 條同時；每次執行都寫審計。大量取數請走匯出申請。`} />
      {err && <Alert color="red" variant="light" mb="md">{err}</Alert>}{msg && <Text size="sm" c="dimmed" mb="md">{msg}</Text>}
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md" className="ds-split-side">
        <Paper p={0} style={{ alignSelf: 'start', overflow: 'hidden' }}>
          <Group p="sm"><SegmentedControl size="xs" value={side} onChange={(v) => setSide(v as typeof side)} data={SIDE_TABS} /></Group>
          {side === 'tables' && <Stack gap="xs" px="sm" pb="sm" style={{ maxHeight: 560, overflow: 'auto' }}>
            {(cat?.tables ?? []).map((t) => (
              <div key={t.name}>
                <Group gap={4} wrap="nowrap">
                  <Button size="compact-sm" variant="subtle" onClick={() => setOpenTable(openTable === t.name ? null : t.name)}>{openTable === t.name ? '▾' : '▸'}</Button>
                  <UnstyledButton onClick={() => insert(t.name)}><Text size="xs" fw={700} ff="monospace">{t.name}</Text></UnstyledButton>
                  {t.mid_table && <StatusBadge tone="custom">中間表</StatusBadge>}
                </Group>
                {openTable === t.name && <List size="xs" spacing={2} ml={26} listStyleType="none">{t.columns.map((c) => <List.Item key={c.name}><UnstyledButton disabled={!c.allowed} onClick={() => c.allowed && insert(c.name)} title={c.allowed ? c.type : `${c.level} 脫敏欄位，不可直接查詢`} style={{ cursor: c.allowed ? 'pointer' : 'not-allowed', opacity: c.allowed ? 1 : 0.5 }}><Text size="xs" ff="monospace" c={c.allowed ? undefined : 'dimmed'}>{c.name} <Text component="span" size="xs" c="dimmed">{c.type}{c.level !== 'L1' ? ` · ${c.level}` : ''}</Text></Text></UnstyledButton></List.Item>)}</List>}
              </div>))}
            <Text size="sm" c="dimmed">函數：{cat?.functions.slice(0, 6).join('、')}…</Text>
            <Group gap={4} wrap="wrap"><Text size="sm" c="dimmed">範例：</Text>{cat?.examples.map((ex) => <Button key={ex.cn} size="compact-sm" variant="subtle" onClick={() => setSql(ex.sql)}>{ex.cn}</Button>)}</Group>
          </Stack>}
          {side === 'saved' && <Stack gap={4} px="sm" pb="sm">{saved.length === 0 ? <Text size="sm" c="dimmed">還沒有已存查詢。</Text> : saved.map((s) => <Group key={s.id} gap={4} wrap="wrap"><Button size="compact-sm" variant="subtle" onClick={() => { setSql(s.sql); setParams(Object.fromEntries(Object.entries(s.params_default ?? {}).map(([k, v]) => [k, String(v)]))); }}>{s.name}</Button><Text size="xs" c="dimmed">{s.group}{s.mine ? '' : ` · ${s.owner}`}</Text>{s.editable && <Button size="compact-sm" variant="subtle" color="red" onClick={() => { if (confirm(`刪除「${s.name}」？`)) api.del(`/v1/sql/saved/${s.id}`).then(loadSide).catch((e: Error) => setErr(e.message)); }}>刪</Button>}</Group>)}</Stack>}
          {side === 'history' && <Stack gap={4} px="sm" pb="sm" style={{ maxHeight: 560, overflow: 'auto' }}>{history.length === 0 ? <Text size="sm" c="dimmed">30 天內沒有查詢記錄。</Text> : history.map((h) => <Group key={h.id} gap={4} wrap="wrap"><Button size="compact-sm" variant="subtle" onClick={() => setSql(h.sql)} title={h.sql}>{fmtTs(h.at).slice(5, 16)}</Button><StatusBadge tone={h.status === 'ok' ? 'blue' : 'red'}>{h.status === 'ok' ? `${h.rows} 列 · ${h.ms} ms` : '失敗'}</StatusBadge><Text size="xs" c="dimmed" ff="monospace" fz={10.5}>{h.sql.slice(0, 40)}</Text></Group>)}</Stack>}
          {side === 'mid' && <Stack gap={4} px="sm" pb="sm">{mids.length === 0 ? <Text size="sm" c="dimmed">還沒有中間表。{canMid ? '執行查詢後按「存為中間表」。' : '需資訊管理或系統管理員建立。'}</Text> : mids.map((m) => <Group key={m.name} gap={4} wrap="wrap"><UnstyledButton onClick={() => insert(`dmp.${m.name}`)}><Text size="xs" fw={700} ff="monospace">dmp.{m.name}</Text></UnstyledButton><Text size="xs" c="dimmed">{m.rows ?? '—'} 列 · {fmtTs(m.refreshed_at).slice(5, 16)}</Text>{canMid && <><Button size="compact-sm" variant="subtle" onClick={() => api.post(`/v1/sql/mid-tables/${m.name}/refresh`).then(loadSide).catch((e: Error) => setErr(e.message))}>重算</Button><Button size="compact-sm" variant="subtle" color="red" onClick={() => { if (confirm(`刪除中間表 ${m.name}？`)) api.del(`/v1/sql/mid-tables/${m.name}`).then(loadSide).catch((e: Error) => setErr(e.message)); }}>刪</Button></>}</Group>)}</Stack>}
        </Paper>
        <Stack gap="md">
          <Paper>
            <Stack gap="sm">
              <Textarea ref={editor} value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} aria-label="SQL" autosize minRows={8} ff="monospace"
                onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void run(); } }} placeholder="SELECT event_date, count() AS n FROM dmp.events WHERE event = 'bond_deal' GROUP BY event_date" />
              {names.length > 0 && <Group gap="xs" wrap="wrap"><Text size="xs" c="dimmed">動態參數：</Text>{names.map((n) => <TextInput key={n} w={200} label={`{{${n}}}`} aria-label={`參數 ${n}`} value={params[n] ?? ''} onChange={(e) => setParams({ ...params, [n]: e.target.value })} />)}</Group>}
              <Group gap="xs">
                <Button variant="filled" disabled={busy || !sql.trim()} onClick={() => void run()}>{busy ? '執行中…' : '執行（Ctrl+Enter）'}</Button>
                <Button disabled={busy || !sql.trim()} onClick={() => void run(true)}>解析／估算</Button>
                <Button disabled={!sql.trim()} onClick={() => setSaveForm({ name: '', group: '', roles: [] })}>儲存查詢</Button>
                <Button disabled={!sql.trim()} onClick={() => void openBm()}>存為書籤 / 加入看板</Button>
                {canMid && <Button disabled={!sql.trim()} onClick={() => setMidForm({ name: '', note: '' })}>存為中間表</Button>}
                <Button onClick={() => { setSql(''); setRes(null); }}>清空</Button>
              </Group>
              {saveForm && <Group gap="xs" wrap="wrap" align="flex-end"><TextInput label="名稱" placeholder="名稱" value={saveForm.name} onChange={(e) => setSaveForm({ ...saveForm, name: e.target.value })} /><TextInput label="分組（選填）" placeholder="分組（選填）" value={saveForm.group} onChange={(e) => setSaveForm({ ...saveForm, group: e.target.value })} /><NativeSelect label="分享角色" multiple styles={MULTI_STYLES} w={160} value={saveForm.roles} onChange={(e) => setSaveForm({ ...saveForm, roles: Array.from(e.target.selectedOptions).map((o) => o.value) })} aria-label="分享角色" data={[opt('data_analyst', '數據分析'), opt('it_admin', '資訊管理'), opt('digital_ops', '數位經營')]} /><Button variant="filled" disabled={!saveForm.name.trim()} onClick={() => void save()}>儲存</Button><Button onClick={() => setSaveForm(null)}>取消</Button></Group>}
              {bmForm && <Group gap="xs" wrap="wrap" align="flex-end"><TextInput label="書籤名稱" placeholder="書籤名稱" value={bmForm.name} onChange={(e) => setBmForm({ ...bmForm, name: e.target.value })} /><NativeSelect label="加入看板" multiple styles={MULTI_STYLES} w={200} value={bmForm.targets} onChange={(e) => setBmForm({ ...bmForm, targets: Array.from(e.target.selectedOptions).map((o) => o.value) })} aria-label="加入看板" data={bmForm.dashboards.map((d) => opt(d.id, d.name))} /><Button variant="filled" disabled={!bmForm.name.trim()} onClick={() => void saveBm()}>儲存書籤</Button><Button onClick={() => setBmForm(null)}>取消</Button><Text size="xs" c="dimmed">看板卡片以表格呈現；檢視者需有自定義 SQL 權限</Text></Group>}
              {midForm && <Group gap="xs" wrap="wrap" align="flex-end"><TextInput label="名稱（小寫英數底線）" leftSection={<Text size="xs" c="dimmed">dmp.mid_</Text>} leftSectionWidth={70} placeholder="名稱（小寫英數底線）" value={midForm.name} onChange={(e) => setMidForm({ ...midForm, name: e.target.value })} /><TextInput label="說明" placeholder="說明" value={midForm.note} onChange={(e) => setMidForm({ ...midForm, note: e.target.value })} /><Button variant="filled" disabled={!/^[a-z][a-z0-9_]{1,40}$/.test(midForm.name)} onClick={() => void createMid()}>建立</Button><Button onClick={() => setMidForm(null)}>取消</Button></Group>}
            </Stack>
          </Paper>
          {res && !res.explained && (
            <Paper>
              <SectionTitle title="結果" right={<Group gap="xs">
                <SegmentedControl size="xs" value={view} onChange={(v) => setView(v as typeof view)} data={VIEW_TABS} />
                <Button size="compact-sm" variant="subtle" onClick={copy}>複製前 100 列</Button>
                {can('events', 'export') && <Button size="compact-sm" variant="subtle" component="a" href={`/v1/sql/query/${res.run_id}/download`} target="_blank" rel="noreferrer">下載 CSV</Button>}
              </Group>}><Text size="xs" c="dimmed">{res.row_count} 列{res.truncated ? '（已截斷）' : ''} · {res.ms} ms{res.scan_rows_estimate != null ? ` · 掃描估算 ${res.scan_rows_estimate.toLocaleString()}` : ''} · {res.lineage.join('、')}</Text></SectionTitle>
              {res.warnings.length > 0 && <Text size="sm" c="dimmed" mb="sm">{res.warnings.join('；')}</Text>}
              {view !== 'table' && <Group gap="xs" mb="sm" align="flex-start"><NativeSelect label="X" w={180} value={xCol} data={res.columns.map((c) => opt(c, c))} onChange={(e) => setXCol(e.target.value)} /><NativeSelect label="Y" multiple styles={MULTI_STYLES} w={200} value={yCols} data={numericColumns(res.columns, res.rows).map((c) => opt(c, c))} onChange={(e) => setYCols(Array.from(e.target.selectedOptions).map((o) => o.value))} /></Group>}
              {view === 'table' ? <Table.ScrollContainer minWidth={480} maxHeight={480}><Table><Table.Thead><Table.Tr>{res.columns.map((c) => <Table.Th key={c}>{c}</Table.Th>)}</Table.Tr></Table.Thead><Table.Tbody>{res.rows.map((r, i) => <Table.Tr key={i}>{res.columns.map((c) => <Table.Td key={c} ta={typeof r[c] === 'number' ? 'right' : undefined}><Mono>{r[c] == null ? '—' : typeof r[c] === 'object' ? JSON.stringify(r[c]) : String(r[c])}</Mono></Table.Td>)}</Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>
                : series.length ? <LineChart series={series} kind={view} height={260} /> : <Text size="sm" c="dimmed">選一個數值欄當 Y。</Text>}
              <Button size="compact-sm" variant="subtle" mt="sm" onClick={() => setShowEffective((v) => !v)}>{showEffective ? '隱藏實際執行的 SQL' : '實際執行的 SQL'}</Button>
              <Collapse in={showEffective}><Code block mt="xs">{res.sql_effective}</Code></Collapse>
            </Paper>)}
        </Stack>
      </SimpleGrid>
    </div>
  );
}
