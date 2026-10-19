// 分析頁的「存為書籤」（舊平台：保存為書籤 → 添加到概覽）：命名、分享角色、順手加進一或多個看板或新建看板 → POST /v1/bookmarks。查詢本體原樣存（後端以建立者身分先編譯一次）。設計系統元件組合。
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Anchor, Button, Group, NativeSelect, Paper, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { SectionTitle } from '@/ds';
import { defaultTitle } from './dashboards';
import type { Bookmark, CardChart, CardFormat, CardKind, DashboardSummary } from './types';

const ROLES: [string, string][] = [['marketing', '行銷'], ['data_analyst', '數據分析'], ['digital_ops', '數位經營'], ['it_admin', '資訊管理']];
const CHARTS = [['auto', '自動'], ['line', '折線'], ['area', '面積'], ['bar', '長條'], ['stacked', '堆疊'], ['pie', '圓餅'], ['percent', '百分比堆疊'], ['stat', '數字'], ['table', '表格']].map(([value, label]) => ({ value, label }));
const FORMATS = [['auto', '數值（自動縮寫）'], ['int', '取整'], ['dec2', '2 位小數'], ['pct0', '百分比（取整）'], ['pct2', '百分比（2 位小數）'], ['twd0', 'NT$（取整）'], ['twd2', 'NT$（2 位小數）']].map(([value, label]) => ({ value, label }));
/** 多選的原生 select：拿掉單行高度與下拉箭頭。 */
const MULTI = { input: { height: 'auto', minHeight: 64, paddingBlock: 4 } };

export function SaveCardButton({ kind, query }: { kind: CardKind; query: Record<string, unknown> | null }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<DashboardSummary[] | null>(null);
  const [targets, setTargets] = useState<string[]>([]);
  const [newName, setNewName] = useState('');
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [roles, setRoles] = useState<string[]>([]);
  const [chart, setChart] = useState<CardChart>('auto');
  const [format, setFormat] = useState<CardFormat>('auto');
  const [width, setWidth] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<Bookmark | null>(null);
  const canDash = can('overview', 'use');
  if (!query) return null;

  const start = async () => {
    setOpen(true); setDone(null); setErr(null); setName(defaultTitle(kind, query));
    if (canDash) { try { setList((await api.get<DashboardSummary[]>('/v1/dashboards')).filter((d) => d.editable)); } catch { setList([]); } } else setList([]);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const b = await api.post<Bookmark>('/v1/bookmarks', { name: name.trim() || defaultTitle(kind, query), description: desc, kind, query, chart, format, width, shared: { roles, users: [] }, add_to: targets.filter((t) => t !== '__new'), new_dashboard: targets.includes('__new') ? (newName.trim() || '我的儀表板') : undefined });
      setDone(b); setOpen(false);
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : '儲存失敗'); }
    finally { setBusy(false); }
  };
  return (
    <>
      <Button onClick={() => void start()}>存為書籤</Button>
      {done && <Text component="span" size="sm" c="dimmed" className="note" style={{ marginTop: 0 }}>已存書籤 <Anchor component={Link} to="/bookmarks" size="sm">{done.name}</Anchor>{(done.added_to ?? []).length > 0 && <>，並加入 {done.added_to!.map((a) => <Anchor key={a.dashboard} component={Link} to={`/overview/${a.dashboard}`} size="sm" mr={6}>{a.name}</Anchor>)}</>}</Text>}
      {open && (
        <Paper component="form" onSubmit={submit} aria-label="存為書籤" mt="sm" w="100%">
          <Stack gap="sm">
            <SectionTitle title="存為書籤" right={<Button type="button" size="compact-sm" variant="subtle" onClick={() => setOpen(false)}>關閉</Button>}><Text size="xs" c="dimmed">書籤 = 這個查詢的條件；可重開、分享、放進多個看板</Text></SectionTitle>
            {err && <Alert color="red" variant="light">{err}</Alert>}
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <TextInput label="書籤名稱" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
              <TextInput label="說明" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={400} placeholder="口徑、用途…" />
              <NativeSelect multiple label="分享給角色（選填）" value={roles} styles={MULTI} rightSection={<span />} onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))}>{ROLES.map(([k, cn]) => <option key={k} value={k}>{cn}</option>)}</NativeSelect>
              {kind === 'events' && <NativeSelect label="圖型" value={chart} data={CHARTS} onChange={(e) => setChart(e.target.value as CardChart)} />}
              {kind === 'events' && <NativeSelect label="數值格式" value={format} data={FORMATS} onChange={(e) => setFormat(e.target.value as CardFormat)} />}
              {canDash && <NativeSelect multiple label="加入看板（選填，可多選）" value={targets} styles={MULTI} rightSection={<span />} onChange={(e) => setTargets(Array.from(e.target.selectedOptions).map((o) => o.value))}>{(list ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}（{d.cards} 張）</option>)}<option value="__new">＋ 新建看板</option></NativeSelect>}
              {targets.includes('__new') && <TextInput label="新看板名稱" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="例：債券業務日報" maxLength={60} />}
              {targets.length > 0 && <NativeSelect label="卡片寬度" value={String(width)} data={[{ value: '1', label: '半寬' }, { value: '2', label: '全寬' }]} onChange={(e) => setWidth(Number(e.target.value) as 1 | 2)} />}
            </SimpleGrid>
            <Text size="sm" c="dimmed">書籤存的是查詢條件，不是結果：每位檢視者打開時以自己的權限重算（遮罩、行級範圍、K 匿名都照檢視者）。改書籤，引用它的看板卡片一起更新。</Text>
            <Group gap="xs"><Button type="submit" variant="filled" disabled={busy || !list}>{busy ? '儲存中…' : '儲存書籤'}</Button></Group>
          </Stack>
        </Paper>
      )}
    </>
  );
}
