// 數據整合 › 來源新鮮度：每個 events.source 設成即時（最大延遲分鐘）或日批（每日到齊時間＋涵蓋 T-n）。資料就緒（data.readiness）依這裡判斷；
// 未設定的來源一律顯示「未知」，不套預設門檻（規格 docs/superpowers/specs/2026-10-31-source-freshness.md）。看需 integration.view，改需 integration.admin（後端再驗）。
import { useEffect, useState } from 'react';
import { Alert, Box, Button, Group, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api } from '@/api/client';
import { fieldError } from '@/api/errors';
import { useAuth } from '@/auth/AuthContext';
import { EmptyState, ErrorAlert, Mono, SectionTitle, StatusBadge } from '@/ds';
import { fmtTs } from '@/tags/tags';
import { formToBody, freshnessText, sortFreshness, toForm, type FreshnessForm, type FreshnessMode, type SourceFreshness } from './freshness';

const URL = '/v1/integration/source-freshness';

export function FreshnessTab({ onCount }: { onCount?: (unset: number) => void }) {
  const { can } = useAuth();
  const canAdmin = can('integration', 'admin');
  const [rows, setRows] = useState<SourceFreshness[] | null>(null);
  const [err, setErr] = useState<{ error: unknown; fallback: string } | null>(null);
  const [edit, setEdit] = useState<{ source: string; isNew: boolean; form: FreshnessForm; errors: Record<string, string>; apiErr: unknown } | null>(null);
  const [busy, setBusy] = useState(false);
  const [newSrc, setNewSrc] = useState('');
  const reload = async () => {
    try { const r = sortFreshness(await api.get<SourceFreshness[]>(URL)); setRows(r); setErr(null); onCount?.(r.filter((x) => !x.configured).length); }
    catch (e) { setErr({ error: e, fallback: '讀取來源新鮮度失敗' }); }
  };
  useEffect(() => { void reload(); }, []);
  const open = (r: SourceFreshness | null, source: string) => setEdit({ source, isNew: !r, form: toForm(r), errors: {}, apiErr: null });
  const save = async () => {
    if (!edit) return;
    const res = formToBody(edit.form);
    if (res.errors) { setEdit({ ...edit, errors: res.errors }); return; }
    setBusy(true);
    try { await api.put(`${URL}/${encodeURIComponent(edit.source)}`, res.body); setEdit(null); await reload(); }
    catch (e) { setEdit({ ...edit, errors: {}, apiErr: e }); } finally { setBusy(false); }
  };
  const clear = async (source: string) => {
    if (!window.confirm(`清除 ${source} 的新鮮度設定？清除後資料就緒會把它顯示為「未知」。`)) return;
    setBusy(true);
    try { await api.del(`${URL}/${encodeURIComponent(source)}`); await reload(); } catch (e) { setErr({ error: e, fallback: '清除失敗' }); } finally { setBusy(false); }
  };
  const unset = (rows ?? []).filter((r) => !r.configured).length;
  const f = edit?.form; const setF = (p: Partial<FreshnessForm>) => edit && setEdit({ ...edit, form: { ...edit.form, ...p }, errors: {} });
  const ferr = (k: string) => edit?.errors[k] ?? fieldError(edit?.apiErr, k);
  return (
    <Stack gap="md">
      {err && <ErrorAlert error={err.error} fallback={err.fallback} onRetry={() => void reload()} />}
      {unset > 0 && <Alert color="orange" title={`${unset} 個來源尚未設定新鮮度`}>這些來源在「資料就緒」會顯示「未知」，不會被判成正常或延遲。請依上游排程設定成即時或日批{canAdmin ? '' : '（需資訊管理權限）'}。</Alert>}
      <Paper>
        <SectionTitle title="來源新鮮度" right={<Text size="xs" c="dimmed">時區固定 Asia/Taipei；清單含近 30 天出現過的來源</Text>} />
        {rows === null ? <EmptyState>載入中…</EmptyState> : rows.length === 0 ? <EmptyState>近 30 天沒有任何來源進資料，也還沒有預先設定的來源</EmptyState> : (
          <Table.ScrollContainer minWidth={720}>
            <Table>
              <Table.Thead><Table.Tr><Table.Th>來源</Table.Th><Table.Th>最後一筆事件</Table.Th><Table.Th>設定</Table.Th><Table.Th>備註</Table.Th><Table.Th /></Table.Tr></Table.Thead>
              <Table.Tbody>{rows.map((r) => (
                <Table.Tr key={r.source}>
                  <Table.Td><Mono>{r.source}</Mono></Table.Td>
                  <Table.Td><Text size="sm">{r.last_event_time ? fmtTs(r.last_event_time) + ' UTC' : '近 30 天無資料'}</Text></Table.Td>
                  <Table.Td><Group gap={6} wrap="nowrap">{r.configured ? <StatusBadge tone="blue">{r.mode === 'realtime' ? '即時' : '日批'}</StatusBadge> : <StatusBadge tone="amber">未設定</StatusBadge>}<Text size="sm">{freshnessText(r)}</Text></Group></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{r.note ?? ''}</Text></Table.Td>
                  <Table.Td ta="right">{canAdmin && <Group gap={4} justify="flex-end" wrap="nowrap">
                    <Button size="compact-sm" variant="subtle" disabled={busy} onClick={() => open(r.configured ? r : null, r.source)}>{r.configured ? '修改' : '設定'}</Button>
                    {r.configured && <Button size="compact-sm" variant="subtle" color="red" disabled={busy} onClick={() => void clear(r.source)}>清除</Button>}</Group>}</Table.Td>
                </Table.Tr>))}</Table.Tbody>
            </Table>
          </Table.ScrollContainer>)}
        {canAdmin && !edit && <Group gap="xs" mt="md"><TextInput aria-label="預先設定的來源代號" placeholder="還沒有資料的來源代號（小寫英數與底線）" value={newSrc} onChange={(e) => setNewSrc(e.target.value)} style={{ flex: '1 1 280px' }} />
          <Button variant="light" disabled={!/^[a-z0-9_]{1,64}$/.test(newSrc)} onClick={() => { open(null, newSrc); setNewSrc(''); }}>預先設定</Button></Group>}
      </Paper>
      {edit && f && (
        <Paper>
          <Stack gap="sm">
            <SectionTitle title={<>設定 <Mono>{edit.source}</Mono></>} />
            {Boolean(edit.apiErr) && <Box><ErrorAlert error={edit.apiErr} fallback="儲存失敗" /></Box>}
            <SegmentedControl value={f.mode} onChange={(v) => setF({ mode: v as FreshnessMode })} data={[{ value: 'realtime', label: '即時（串流／微批）' }, { value: 'daily_batch', label: '日批（每天一批）' }]} />
            {f.mode === 'realtime'
              ? <TextInput label="最大延遲（分鐘，1–1440）" aria-label="最大延遲分鐘" value={f.max_lag_minutes} error={ferr('max_lag_minutes')} onChange={(e) => setF({ max_lag_minutes: e.target.value })} description="最後一筆事件距今超過這個分鐘數，資料就緒就標延遲" />
              : <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <TextInput label="每日到齊時間（台北，HH:MM）" aria-label="到齊時間" value={f.ready_at} error={ferr('ready_at')} onChange={(e) => setF({ ready_at: e.target.value })} />
                  <TextInput label="到齊後應涵蓋到 T-n（1–7）" aria-label="涵蓋天數" value={f.covers_days_back} error={ferr('covers_days_back')} onChange={(e) => setF({ covers_days_back: e.target.value })} description="1 = 前一天。到齊時間之前只要求到 T-(n+1)" />
                </SimpleGrid>}
            <TextInput label="備註（選填，200 字內）" aria-label="備註" placeholder="例如上游排程名稱" value={f.note} error={ferr('note')} onChange={(e) => setF({ note: e.target.value })} />
            <Text size="sm" c="dimmed">{freshnessText({ configured: true, mode: f.mode, max_lag_minutes: Number(f.max_lag_minutes), ready_at: f.ready_at, covers_days_back: Number(f.covers_days_back) })}。儲存會整筆取代舊設定並記審計。</Text>
            <Group gap="xs"><Button variant="filled" loading={busy} onClick={() => void save()}>儲存</Button><Button onClick={() => setEdit(null)}>取消</Button></Group>
          </Stack>
        </Paper>)}
    </Stack>
  );
}
