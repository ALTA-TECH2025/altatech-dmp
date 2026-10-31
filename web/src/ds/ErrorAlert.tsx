// 錯誤提示：訊息＋回報代碼（可複製）＋依錯誤碼給「重試」「重新整理」；欄位錯誤逐條列出（docs/46 §4.6）。
import { Alert, Button, Group, List, Text } from '@mantine/core';
import { errorView } from '@/api/errors';

export function ErrorAlert({ error, fallback = '操作失敗', onRetry }: { error: unknown; fallback?: string; onRetry?: () => void }) {
  if (!error) return null;
  const v = errorView(error, fallback);
  return (
    <Alert color="red" variant="light" role="alert">
      <Text size="sm">{v.text}</Text>
      {v.stepUp && <Text size="xs" c="dimmed" mt={4}>請先在頂欄輸入驗證碼完成再次驗證，再重試一次。</Text>}
      {v.fields.length > 0 && <List size="xs" mt={4}>{v.fields.slice(0, 10).map((f, i) => <List.Item key={i}><Text span ff="monospace" size="xs">{f.field}</Text> {f.message}</List.Item>)}</List>}
      <Group gap="xs" mt={6}>
        {v.reportCode && <Text size="xs" c="dimmed">回報代碼：<Text span ff="monospace" size="xs" style={{ userSelect: 'all' }}>{v.reportCode}</Text></Text>}
        {v.retry && onRetry && <Button size="compact-xs" variant="light" onClick={onRetry}>重試</Button>}
        {v.reload && <Button size="compact-xs" variant="light" onClick={() => location.reload()}>重新整理</Button>}
      </Group>
    </Alert>
  );
}
