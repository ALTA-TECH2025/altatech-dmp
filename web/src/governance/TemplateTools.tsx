// 範本工具列：下載空白範本、匯出目前內容（改完可直接上傳）、上傳 → 逐列預覽 → 確認套用。governance.view 可下載；governance.use 才能上傳。
import { useRef, useState } from 'react';
import { Alert, Button, Group, Modal, Stack, Table, Text } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { StatusBadge } from '@/ds';
import { ACTION_CN, ACTION_TONE, saveFile, type ImportAction, type ImportResult, type TemplateKind } from './templates';

const LABEL: Record<TemplateKind, string> = { events: '元事件', properties: '事件屬性', 'user-properties': '用戶表屬性' };
const ORDER: ImportAction[] = ['create', 'update', 'change_request', 'unchanged', 'error'];

export function TemplateTools({ kind, canUse, onApplied }: { kind: TemplateKind; canUse: boolean; onApplied: () => Promise<void> }) {
  const file = useRef<HTMLInputElement>(null);
  const [csv, setCsv] = useState<{ name: string; text: string } | null>(null);
  const [res, setRes] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const download = async (filled: boolean) => {
    setErr(null);
    try { const t = await api.get<string>(`/v1/meta/templates/${kind}${filled ? '?filled=true' : ''}`); saveFile(`dmp-${kind}-${filled ? 'export' : 'template'}.csv`, String(t)); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '下載失敗'); }
  };
  const run = async (dry: boolean, text: string) => {
    setBusy(true); setErr(null);
    try { const r = await api.post<ImportResult>(`/v1/meta/templates/${kind}/import?dry_run=${dry}`, { csv: text }); setRes(r); if (!dry) await onApplied(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '上傳失敗'); setRes(null); }
    finally { setBusy(false); }
  };
  const pick = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) { setErr('檔案超過 2 MB'); return; }
    const text = await f.text(); setCsv({ name: f.name, text }); await run(true, text);
  };
  const close = () => { setCsv(null); setRes(null); setErr(null); if (file.current) file.current.value = ''; };
  const ready = res && !res.applied && res.summary.create + res.summary.update + res.summary.change_request > 0;
  return (
    <>
      <Group gap="xs">
        <Button variant="default" size="xs" onClick={() => void download(false)}>下載範本</Button>
        <Button variant="default" size="xs" onClick={() => void download(true)}>匯出目前內容</Button>
        {canUse && <Button variant="light" size="xs" onClick={() => file.current?.click()}>上傳範本</Button>}
        <input ref={file} type="file" accept=".csv,text/csv" hidden aria-label={`上傳${LABEL[kind]}範本`} onChange={(e) => void pick(e.target.files?.[0])} />
      </Group>
      {err && !csv && <Alert color="red" variant="light" mt="xs">{err}</Alert>}
      <Modal opened={csv !== null} onClose={close} title={`上傳${LABEL[kind]}範本：${csv?.name ?? ''}`} size="xl">
        <Stack gap="sm">
          {err && <Alert color="red" variant="light">{err}</Alert>}
          {busy && <Text size="sm" c="dimmed">處理中…</Text>}
          {res && (
            <>
              <Text size="sm">{res.applied ? '已套用。' : '預覽（尚未寫入）：'}共 {res.total} 列 · {ORDER.filter((a) => res.summary[a]).map((a) => `${ACTION_CN[a]} ${res.summary[a]}`).join(' · ')}</Text>
              {!res.applied && <Text size="xs" c="dimmed">套用規則與單筆登記相同：L4 拒絕、敏感等級只升、上線事件改屬性會建立變更單待審；錯誤列不會套用。</Text>}
              <Table.ScrollContainer minWidth={620} mah={420}>
                <Table striped>
                  <Table.Thead><Table.Tr><Table.Th>行</Table.Th><Table.Th>名稱</Table.Th><Table.Th>結果</Table.Th><Table.Th>說明</Table.Th></Table.Tr></Table.Thead>
                  <Table.Tbody>{res.rows.map((r) => (
                    <Table.Tr key={`${r.line}-${r.key}`}>
                      <Table.Td>{r.line}</Table.Td><Table.Td><Text size="sm" ff="monospace">{r.key || '—'}</Text></Table.Td>
                      <Table.Td><StatusBadge tone={ACTION_TONE[r.action]}>{ACTION_CN[r.action]}</StatusBadge></Table.Td>
                      <Table.Td><Text size="xs" c={r.errors.length ? 'red' : 'dimmed'}>{r.errors.length ? r.errors.join('；') : r.changes.length ? `改：${r.changes.join('、')}` : ''}</Text></Table.Td>
                    </Table.Tr>))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={close}>{res?.applied ? '關閉' : '取消'}</Button>
            {!res?.applied && <Button disabled={!ready || busy} onClick={() => csv && void run(false, csv.text)}>確認套用</Button>}
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
