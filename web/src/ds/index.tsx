// 設計系統共用元件：頁首、狀態標章、統計磚、鍵值表、空狀態、區塊標題。頁面只組合這些與 Mantine 基礎元件，不再手寫 class。
import { Badge, Box, Group, Paper, Stack, Text, Title, type BadgeProps } from '@mantine/core';
import type { ReactNode } from 'react';

/** 狀態標章顏色：與 app.css 的 pill 語意對齊（blue 生效、amber 待處理、red 失敗／回收、gray 停用、violet 特殊）。 */
export const PILL_COLOR: Record<string, BadgeProps['color']> = { blue: 'brand', amber: 'orange', red: 'red', gray: 'gray', custom: 'violet' };
export function StatusBadge({ tone, children }: { tone: string; children: ReactNode }) { return <Badge color={PILL_COLOR[tone] ?? 'gray'}>{children}</Badge>; }

export function PageHeader({ title, lead, actions }: { title: ReactNode; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <Group align="flex-start" justify="space-between" mb="md" wrap="wrap">
      <Box maw="72ch"><Title order={1}>{title}</Title>{lead && <Text size="sm" c="dimmed" mt={4}>{lead}</Text>}</Box>
      {actions && <Group gap="xs">{actions}</Group>}
    </Group>
  );
}

export function SectionTitle({ title, right, children }: { title: ReactNode; right?: ReactNode; children?: ReactNode }) {
  return <Group justify="space-between" mb="sm" wrap="wrap"><Group gap="xs"><Title order={3}>{title}</Title>{children}</Group>{right}</Group>;
}

export function StatTile({ label, value, hint }: { label: ReactNode; value: ReactNode; hint?: ReactNode }) {
  return (
    <Paper p="sm" radius="md" shadow="none" bg="var(--soft)">
      <Stack gap={2}><Text size="xs" c="dimmed">{label}</Text><Text fw={700} fz={22} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</Text>{hint && <Text size="xs" c="dimmed">{hint}</Text>}</Stack>
    </Paper>
  );
}

export function KeyValue({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <Box component="dl" m={0} style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '6px 14px', fontSize: 13 }}>
      {rows.map(([k, v], i) => <Box key={i} style={{ display: 'contents' }}><Text component="dt" size="sm" c="dimmed">{k}</Text><Box component="dd" m={0} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{v}</Box></Box>)}
    </Box>
  );
}

export function EmptyState({ children }: { children: ReactNode }) { return <Text ta="center" c="dimmed" size="sm" py="xl">{children}</Text>; }

export function Mono({ children }: { children: ReactNode }) { return <Text component="span" ff="monospace" size="sm" style={{ fontVariantNumeric: 'tabular-nums' }}>{children}</Text>; }
export { ErrorAlert } from './ErrorAlert';
