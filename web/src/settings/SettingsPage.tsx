// 平台設定（唯讀）：/v1/settings 五區塊（身分與登入、權限、隱碼與揭露、Agent 預算、審計告警）；密鑰只顯示 secret_ref；線上修改（雙人複核）第二階段。設計系統元件組合。
import { useEffect, useState } from 'react';
import { Alert, Paper, SegmentedControl, Table, Text } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { SettingsView } from '@/api/types';
import { RequireGrant } from '@/auth/RequireGrant';
import { EmptyState, KeyValue, Mono, PageHeader, SectionTitle, StatusBadge } from '@/ds';

export function SettingsPage() {
  return <RequireGrant module="iam"><Inner /></RequireGrant>;
}

function Inner() {
  const [st, setSt] = useState<SettingsView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sec, setSec] = useState<string>('identity');
  useEffect(() => { api.get<SettingsView>('/v1/settings').then(setSt).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, []);
  if (err) return <div className="page"><Alert color="red" variant="light">{err}</Alert></div>;
  if (!st) return <div className="page"><EmptyState>載入中…</EmptyState></div>;
  const cur = st.sections.find((s) => s.key === sec) ?? st.sections[0];
  return (
    <div className="page">
      <PageHeader title={<>平台設定 <StatusBadge tone="gray">唯讀</StatusBadge></>} lead="登入政策、session、角色與雙人複核、K 匿名、隱碼與揭露、匯出上限、agent 預算、審計告警。目前以設定檔與 PR 管理；密鑰只存 secret_ref。"
        actions={<StatusBadge tone="amber">線上修改（雙人複核）：第二階段</StatusBadge>} />
      <SegmentedControl size="xs" mb="sm" value={cur.key} onChange={setSec} data={st.sections.map((s) => ({ value: s.key, label: s.cn }))} />
      <Paper>
        <SectionTitle title={cur.cn} right={<Text size="xs" c="dimmed" ff="monospace">{cur.source}</Text>} />
        <Table layout="fixed">
          <Table.Thead><Table.Tr><Table.Th w={180}>項目</Table.Th><Table.Th>值</Table.Th><Table.Th w={260}>說明</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{cur.items.map((it) => <Table.Tr key={it.key}><Table.Td><Text fw={600} size="sm">{it.cn}</Text><Text size="xs" c="dimmed" ff="monospace">{it.key}</Text></Table.Td><Table.Td><Value v={it.value} /></Table.Td><Table.Td><Text size="xs" c="dimmed">{it.note}</Text></Table.Td></Table.Tr>)}</Table.Tbody>
        </Table>
      </Paper>
      <Text size="sm" c="dimmed" mt="sm">{st.edit.note}</Text>
    </div>
  );
}

function Value({ v }: { v: unknown }) {
  if (v == null || v === '') return <Text component="span" size="xs" c="dimmed">—</Text>;
  if (typeof v === 'boolean') return <StatusBadge tone={v ? 'blue' : 'gray'}>{v ? '開' : '關'}</StatusBadge>;
  if (typeof v === 'number' || typeof v === 'string') return <Mono>{String(v)}</Mono>;
  if (Array.isArray(v)) {
    if (v.every((x) => typeof x !== 'object')) return <Mono>{v.map(String).join('、')}</Mono>;
    const cols = Array.from(new Set(v.flatMap((x) => Object.keys(x as object))));
    return <Table.ScrollContainer minWidth={320}><Table fz="xs" horizontalSpacing="xs"><Table.Thead><Table.Tr>{cols.map((c) => <Table.Th key={c}>{c}</Table.Th>)}</Table.Tr></Table.Thead><Table.Tbody>{v.map((x, i) => <Table.Tr key={i}>{cols.map((c) => <Table.Td key={c}><Text size="xs" ff="monospace">{cell((x as Record<string, unknown>)[c])}</Text></Table.Td>)}</Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>;
  }
  return <KeyValue rows={Object.entries(v as Record<string, unknown>).map(([k, x]) => [<Mono key={k}>{k}</Mono>, <Mono key={k + 'v'}>{cell(x)}</Mono>])} />;
}
const cell = (x: unknown): string => (x == null ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x));
