// 導入名單建分群：貼上或上傳 txt／csv（一列一個客戶代碼或 one_id）→ 上傳比對（後端轉 one_id、不存原始代碼）→ 看對到幾人 → 命名建分群（手動、算一次）。
import { useState } from 'react';
import { Alert, Button, FileButton, Group, NativeSelect, Paper, SimpleGrid, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { SectionTitle, StatTile } from '@/ds';
import type { Segment, SegmentImport } from '@/users/types';
import { MAX_IMPORT, parseIdList } from './segments';

export function ImportForm({ onDone, onCancel }: { onDone: (s: Segment) => Promise<void> | void; onCancel: () => void }) {
  const [text, setText] = useState('');
  const [idType, setIdType] = useState<'customer_code' | 'one_id'>('customer_code');
  const [imp, setImp] = useState<SegmentImport | null>(null);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const values = parseIdList(text);
  const over = values.length > MAX_IMPORT;
  const readFile = async (f: File | null) => { if (!f) return; setText(await f.text()); setImp(null); if (!name) setName(f.name.replace(/\.(csv|txt)$/i, '').slice(0, 60)); };
  const upload = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.post<SegmentImport>('/v1/segments/imports', { id_type: idType, values, label: name.trim() }); setImp(r); setText(''); }      // 上傳後清掉畫面上的原始名單
    catch (e) { setErr(e instanceof ApiError ? e.message : '上傳失敗'); } finally { setBusy(false); }
  };
  const create = async () => {
    if (!imp) return;
    setBusy(true); setErr(null);
    try {
      const seg = await api.post<Segment>('/v1/segments', { name: name.trim(), description: desc, kind: 'import', rule: { project: imp.project, source: { type: 'import', import_id: imp.id } }, update: { mode: 'manual' } });
      if (seg.status === 'active') await api.post(`/v1/segments/${seg.id}/compute`, {}).catch(() => undefined);     // 固定名單：建好直接算一次（待審批的核可後再算）
      await onDone(seg);
    }
    catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); } finally { setBusy(false); }
  };
  return (
    <Paper maw={860}>
      <Stack gap="md">
        <SectionTitle title="導入名單建分群" />
        <Text size="sm" c="dimmed">貼上或上傳 txt／csv，一列一個；csv 取第一欄，表頭會自動略過。客戶代碼會用與神策登入 ID 相同的方式轉成 one_id，原始代碼不會存進平台。一次最多 {MAX_IMPORT.toLocaleString()} 筆。</Text>
        {!imp ? <>
          <Group gap="sm" align="end">
            <NativeSelect label="名單內容" aria-label="名單類型" w={200} value={idType} data={[{ value: 'customer_code', label: '客戶代碼' }, { value: 'one_id', label: 'one_id' }]} onChange={(e) => setIdType(e.target.value as typeof idType)} />
            <FileButton accept=".txt,.csv,text/plain,text/csv" onChange={(f) => void readFile(f)}>{(p) => <Button {...p} variant="light">選擇檔案…</Button>}</FileButton>
            <Text size="sm" c={over ? 'red' : 'dimmed'}>讀到 {values.length.toLocaleString()} 筆{over ? `，超過 ${MAX_IMPORT.toLocaleString()} 上限` : ''}</Text>
          </Group>
          <Textarea aria-label="貼上名單" placeholder={idType === 'customer_code' ? 'A123456789\nB234567890\n…' : 'v1_…\nv1_…'} autosize minRows={6} maxRows={14} ff="monospace" value={text} onChange={(e) => setText(e.target.value)} />
          <Group gap="xs">
            <Button variant="filled" disabled={!values.length || over || busy} loading={busy} onClick={() => void upload()}>上傳並比對</Button>
            <Button variant="subtle" onClick={onCancel}>取消</Button>
          </Group>
        </> : <>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <StatTile label="上傳筆數" value={imp.total.toLocaleString()} hint={imp.invalid ? `格式錯誤 ${imp.invalid.toLocaleString()}` : undefined} />
            <StatTile label="去重後" value={imp.distinct.toLocaleString()} />
            <StatTile label="對到用戶" value={imp.matched.toLocaleString()} hint={imp.distinct ? `${Math.round((imp.matched / imp.distinct) * 1000) / 10}%` : undefined} />
            <StatTile label="沒對到" value={imp.unmatched.toLocaleString()} hint="平台沒有這些人的行為或資料" />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label="分群名稱" aria-label="分群名稱" required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
            <TextInput label="說明" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="名單來源、用途" />
          </SimpleGrid>
          <Text size="xs" c="dimmed">導入名單分群是固定名單：只算一次、不會例行更新；要換名單請重新導入。</Text>
          <Group gap="xs">
            <Button variant="filled" disabled={!name.trim() || !imp.matched || busy} loading={busy} onClick={() => void create()}>建立分群</Button>
            <Button variant="subtle" onClick={() => setImp(null)}>重新上傳</Button>
            <Button variant="subtle" onClick={onCancel}>取消</Button>
          </Group>
        </>}
        {err && <Alert color="red" variant="light">{err}</Alert>}
      </Stack>
    </Paper>
  );
}
