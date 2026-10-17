// 匯出申請對話框（用戶列表／分群成員共用）：用途、保存天數 → POST /v1/exports → 導到審批與匯出頁。設計系統元件組合（Modal）。
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Group, Modal, NativeSelect, Stack, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import type { Export, ExportRequest } from './types';

export function ExportDialog({ base, onClose }: { base: Omit<ExportRequest, 'purpose' | 'retention_days'>; onClose: () => void }) {
  const nav = useNavigate();
  const [purpose, setPurpose] = useState(''); const [days, setDays] = useState(30);
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await api.post<Export>('/v1/exports', { ...base, purpose: purpose.trim(), retention_days: days }); nav('/approvals'); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '申請失敗'); } finally { setBusy(false); }
  };
  return (
    <Modal opened onClose={onClose} title="匯出申請" size="md">
      <Stack gap="sm">
        <Text size="xs" c="dimmed">匯出需數位經營審批；檔案帶浮水印、欄位比畫面嚴一級，上限 10 萬列。</Text>
        <TextInput label="用途" aria-label="匯出用途" placeholder="用途（必填，4–200 字）" value={purpose} onChange={(e) => setPurpose(e.target.value)} />
        <NativeSelect label="保存天數" aria-label="保存天數" value={String(days)} data={[7, 30, 90].map((d) => ({ value: String(d), label: `保存 ${d} 天` }))} onChange={(e) => setDays(Number(e.target.value))} />
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <Group gap="xs"><Button variant="filled" disabled={busy || purpose.trim().length < 4} loading={busy} onClick={() => void submit()}>送出申請</Button><Button variant="subtle" onClick={onClose}>取消</Button></Group>
      </Stack>
    </Modal>
  );
}
