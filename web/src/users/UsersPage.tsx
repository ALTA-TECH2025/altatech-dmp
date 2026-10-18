// 用戶列表：條件（屬性／分群／做過未做過／分析落點來源）→ 即時估數 → 分頁列表（欄位、排序、遮罩）→ 存為分群 → 點列進細查（需理由）。設計系統元件組合。
// 從漏斗／留存／事件分析下鑽：navigate('/users', { state: { source } })。
import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Alert, Button, Code, Group, NativeSelect, Paper, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Catalog } from '@/analytics/types';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { PageHeader, SectionTitle, StatTile } from '@/ds';
import { ExportDialog } from './ExportDialog';
import { ProfileDrawer } from './ProfileDrawer';
import { RuleBuilder } from './RuleBuilder';
import { cleanRule, emptyRule, normalizeRule, ruleValid, summarizeRule } from './rule';
import type { Segment, UsersEstimate, UsersListRequest, UsersListResult, UsersQuery, UsersSource } from './types';
import { UsersTable } from './UsersTable';

export function UsersPage() {
  return <RequireGrant module="users" action="use"><UsersInner /></RequireGrant>;
}

const DEFAULT_COLS = ['branch_id', 'lifecycle', 'tier', 'aum_bucket', 'churn_score', 'last_active_at'];

function UsersInner() {
  const { can } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const incoming = (loc.state as { source?: UsersSource; rule?: UsersQuery; openProfile?: { oneId: string; reason: string } } | null) ?? null;
  const [cat, setCat] = useState<Catalog | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [q, setQ] = useState<UsersQuery>(() => (incoming?.rule ? normalizeRule(incoming.rule) : { ...emptyRule(), source: incoming?.source }));
  const [cols, setCols] = useState<string[]>(DEFAULT_COLS);
  const [sort, setSort] = useState<{ field: string; dir: 'asc' | 'desc' } | null>(null);
  const [est, setEst] = useState<UsersEstimate | null>(null);
  const [res, setRes] = useState<UsersListResult | null>(null);
  const [cursors, setCursors] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [open, setOpen] = useState<string | null>(incoming?.openProfile?.oneId ?? null);   // 「用戶細查 / OneID」入口頁可直接帶客戶代碼開抽屜
  const [saving, setSaving] = useState<{ name: string; mode: 'manual' | 'routine' } | null>(null);
  const [exporting, setExporting] = useState(false);
  const body = (cursor?: string): UsersListRequest => ({ ...cleanRule(q), columns: cols, sort: sort ?? undefined, page_size: 50, cursor });
  const list = async (cursor?: string) => {
    setBusy(true); setErr(null);
    try { setRes(await api.post<UsersListResult>('/v1/users/list', body(cursor))); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '查詢失敗'); } finally { setBusy(false); }
  };
  useEffect(() => {
    api.get<Catalog>('/v1/semantic/catalog').then(setCat).catch((e: Error) => setErr(e.message));
    if (can('segments', 'view')) api.get<Segment[]>('/v1/segments').then(setSegments).catch(() => undefined);
    if (incoming?.source || incoming?.rule) void list();      // 從分析落點下鑽：直接列出
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const valid = ruleValid(q);
  const estimate = async () => {
    setBusy(true); setErr(null);
    try { setEst(await api.post<UsersEstimate>('/v1/users/estimate', body())); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '估數失敗'); } finally { setBusy(false); }
  };
  const saveSegment = async () => {
    if (!saving) return;
    setBusy(true); setErr(null);
    try {
      const seg = await api.post<Segment>('/v1/segments', { name: saving.name, kind: q.source ? 'result' : 'rule', rule: cleanRule(q), update: { mode: saving.mode, freq: 'daily', earliest: '02:00', keep: 7 } });
      nav(`/segments/${seg.id}`);
    } catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); }
  };
  const colCn = (c: string) => (c === 'one_id' ? 'one_id' : cat?.user_props.find((u) => u.key === c)?.cn ?? c);
  const colOptions = useMemo(() => (cat?.user_props ?? []).map((u) => ({ value: u.key, label: u.cn })), [cat]);
  const segOpts = segments.filter((s) => s.status !== 'recycled').map((s) => ({ id: s.id, name: s.name }));
  return (
    <div className="page">
      <PageHeader title="用戶列表" lead="用屬性、分群、做過／未做過的行為，或從分析結果的落點圈人；即時估數（Bitmap 精確基數），分頁瀏覽最多 10 萬筆，欄位依你的角色遮罩；匯出走審批。" />
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md" className="ds-split">
          <Paper>
            <Stack gap="md">
              <RuleBuilder q={q} cat={cat} segments={segOpts} onChange={(nq) => { setQ(nq); setEst(null); setRes(null); setCursors([]); }} />
              <Group gap="xs">
                <Button disabled={!valid || busy} onClick={() => void estimate()}>估人數</Button>
                <Button variant="filled" disabled={!valid || busy} onClick={() => { setCursors([]); void list(); }}>{busy ? '查詢中…' : '查詢列表'}</Button>
                {res && can('segments', 'use') && <Button onClick={() => setSaving({ name: '', mode: 'manual' })}>存為分群</Button>}
                {res && can('users', 'export') && <Button onClick={() => setExporting(true)}>匯出（審批）</Button>}
                {res && <Button onClick={() => setShowSql((v) => !v)}>{showSql ? '隱藏 SQL' : '看 SQL'}</Button>}
                {err && <Alert color="red" variant="light" py={6}>{err}</Alert>}
              </Group>
              {exporting && <ExportDialog base={{ kind: 'users', rule: cleanRule(q), columns: cols }} onClose={() => setExporting(false)} />}
              {saving && (
                <Paper shadow="none" radius="md" bg="var(--soft)" p="sm">
                  <Group gap="xs">
                    <TextInput aria-label="分群名稱" placeholder="分群名稱" value={saving.name} onChange={(e) => setSaving({ ...saving, name: e.target.value })} style={{ flex: 1, minWidth: 180 }} />
                    <NativeSelect aria-label="更新方式" value={saving.mode} data={[{ value: 'manual', label: '手動更新' }, { value: 'routine', label: '每天 06:00 例行更新' }]} onChange={(e) => setSaving({ ...saving, mode: e.target.value as 'manual' | 'routine' })} />
                    <Button variant="filled" disabled={!saving.name.trim() || busy} onClick={() => void saveSegment()}>建立</Button>
                    <Button variant="subtle" onClick={() => setSaving(null)}>取消</Button>
                  </Group>
                </Paper>)}
            </Stack>
          </Paper>
          <Stack gap="md">
            <Paper>
              <SectionTitle title="人數" right={est && <Text size="xs" c="dimmed">{est.cost.ms} ms</Text>} />
              <SimpleGrid cols={1}><StatTile label="符合條件" value={est ? est.users.toLocaleString() : res?.total != null ? res.total.toLocaleString() : '—'} /></SimpleGrid>
              <Text size="sm" c="dimmed" mt="sm">{summarizeRule(q, cat, segOpts)}</Text>
            </Paper>
            <Paper>
              <SectionTitle title="顯示欄位與排序" />
              <Stack gap="sm">
                <NativeSelect label="欄位（one_id 固定）" aria-label="顯示欄位" multiple value={cols} data={colOptions} onChange={(e) => setCols(Array.from(e.target.selectedOptions).map((o) => o.value))} styles={{ input: { height: 'auto', minHeight: 120 } }} />
                <Group gap="xs">
                  <NativeSelect aria-label="排序欄位" value={sort?.field ?? ''} data={[{ value: '', label: '預設順序' }, ...colOptions]} onChange={(e) => setSort(e.target.value ? { field: e.target.value, dir: sort?.dir ?? 'desc' } : null)} style={{ flex: 1 }} />
                  <NativeSelect aria-label="排序方向" value={sort?.dir ?? 'desc'} disabled={!sort} data={[{ value: 'desc', label: '由大到小' }, { value: 'asc', label: '由小到大' }]} onChange={(e) => sort && setSort({ ...sort, dir: e.target.value as 'asc' | 'desc' })} />
                </Group>
              </Stack>
            </Paper>
          </Stack>
        </SimpleGrid>
        {res && <UsersTable res={res} colCn={colCn} busy={busy} canPrev={cursors.length > 0}
          onPrev={() => { const s = cursors.slice(0, -1); setCursors(s); void list(s[s.length - 1]); }}
          onNext={() => { if (res.next_cursor) { setCursors([...cursors, res.next_cursor]); void list(res.next_cursor); } }}
          onOpen={can('profile', 'use') ? setOpen : undefined} />}
        {showSql && res && <Paper><SectionTitle title="SQL" right={<Text size="xs" c="dimmed">{res.lineage.join(' · ')}</Text>} /><Code block>{res.sql}</Code></Paper>}
      </Stack>
      {open && <ProfileDrawer oneId={open} cat={cat} project={q.project} onClose={() => setOpen(null)} />}
    </div>
  );
}
