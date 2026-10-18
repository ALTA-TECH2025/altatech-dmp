// 手機上不適合操作的頁面／表單：寬度 900px 以下先顯示「請以電腦版查看」，可按「仍要在手機查看」繼續；桌面直接顯示內容。
import { useState, type ReactNode } from 'react';
import { Button, Group, Paper, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { PageHeader } from './index';

export function DesktopOnly({ title, reason, inline, children }: { title: string; reason?: string; inline?: boolean; children: ReactNode }) {
  const narrow = useMediaQuery('(max-width: 900px)');
  const [force, setForce] = useState(false);
  if (!narrow || force) return <>{children}</>;
  const card = (
    <Paper maw={560} p="lg">
      <Stack gap="sm">
        <Text fw={700} size="md">請以電腦版查看</Text>
        <Text size="sm" c="dimmed">{reason ?? `「${title}」需要較大的螢幕操作（寬表格、編輯器或多欄設定），手機上容易誤觸或看不完整。請改用電腦開啟。`}</Text>
        <Group gap="xs"><Button variant="default" size="compact-sm" onClick={() => setForce(true)}>仍要在手機查看</Button></Group>
      </Stack>
    </Paper>
  );
  return inline ? card : <div className="page"><PageHeader title={title} />{card}</div>;
}
