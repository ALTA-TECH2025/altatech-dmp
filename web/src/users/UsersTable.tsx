// 用戶列表表格 + keyset 分頁（上一頁用游標堆疊）；遮罩欄位加提示；點列進細查（需 profile.use）。設計系統元件組合。
import { Button, Group, Paper, Table, Text } from '@mantine/core';
import { useAuth } from '@/auth/AuthContext';
import { fmtNum } from '@/analytics/series';
import { EmptyState, Mono } from '@/ds';
import type { UserRow, UsersListResult } from './types';

export function UsersTable({ res, colCn, onPrev, onNext, canPrev, busy, onOpen }: { res: UsersListResult; colCn: (c: string) => string; onPrev: () => void; onNext: () => void; canPrev: boolean; busy: boolean; onOpen?: (oneId: string) => void }) {
  const { can } = useAuth();
  const openable = !!onOpen && can('profile', 'use');
  const fmt = (v: UserRow[string]) => (v === null || v === undefined ? '—' : typeof v === 'number' ? fmtNum(v) : typeof v === 'boolean' ? (v ? '是' : '否') : String(v));
  return (
    <Paper p={0} style={{ overflow: 'hidden' }}>
      <Table.ScrollContainer minWidth={640}>
        <Table>
          <Table.Thead><Table.Tr>{res.columns.map((c) => <Table.Th key={c}>{colCn(c)}{res.masked[c] && <Text size="xs" c="dimmed" title={res.masked[c]}>遮罩</Text>}</Table.Th>)}{openable && <Table.Th />}</Table.Tr></Table.Thead>
          <Table.Tbody>{res.rows.map((r, i) => (
            <Table.Tr key={i} style={openable ? { cursor: 'pointer' } : undefined} onClick={openable ? () => onOpen?.(String(r.one_id)) : undefined}>
              {res.columns.map((c) => (typeof r[c] === 'number'
                ? <Table.Td key={c} ta="right" style={{ whiteSpace: 'nowrap' }}><Mono>{fmt(r[c])}</Mono></Table.Td>
                : c === 'one_id' ? <Table.Td key={c}><Mono>{fmt(r[c])}</Mono></Table.Td> : <Table.Td key={c}>{fmt(r[c])}</Table.Td>))}
              {openable && <Table.Td ta="right"><Button size="compact-sm" variant="subtle">細查</Button></Table.Td>}
            </Table.Tr>))}
            {!res.rows.length && <Table.Tr><Table.Td colSpan={res.columns.length + 1}><EmptyState>沒有符合的用戶</EmptyState></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Group px="sm" py="xs" justify="space-between" wrap="wrap">
        <Text size="xs" c="dimmed">共 {res.total === null || res.total === undefined ? '—' : res.total.toLocaleString()} 人 · 本頁 {res.rows.length} 列{res.masked.one_id ? ' · one_id 已遮罩' : ''}</Text>
        <Group gap="xs"><Button size="compact-sm" variant="subtle" disabled={!canPrev || busy} onClick={onPrev}>上一頁</Button><Button size="compact-sm" variant="subtle" disabled={!res.next_cursor || busy} onClick={onNext}>下一頁</Button></Group>
      </Group>
      {res.warnings.length > 0 && <Text size="sm" c="dimmed" px="sm" pb="sm">{res.warnings.join('；')}</Text>}
    </Paper>
  );
}
