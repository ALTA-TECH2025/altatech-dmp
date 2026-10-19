// 快捷日期區間輸入：按鈕顯示「過去 7 天 2026.10.12 - 2026.10.18」，點開選快捷鍵或自訂起訖；五個分析頁、SQL 參數、看板卡片共用。
import { useState } from 'react';
import { Badge, Button, Group, Input, Popover, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { PRESET_CN, PRESET_GROUPS, detectPreset, fmtRange, presetRange, type DateRange, type PresetKey } from './daterange';

export type { DateRange, PresetKey } from './daterange';

export function DateRangeInput({ value, onChange, label, size = 'sm', ariaLabel = '日期區間', max, fullWidth = true }: { value: DateRange; onChange: (r: DateRange, preset: PresetKey | null) => void; label?: string; size?: 'xs' | 'sm'; ariaLabel?: string; max?: string; fullWidth?: boolean }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange>(value);
  const preset = detectPreset(value);
  const pick = (k: PresetKey) => { const r = presetRange(k); setDraft(r); onChange(r, k); setOpen(false); };
  const confirm = () => { if (draft.from && draft.to && draft.from <= draft.to) { onChange(draft, detectPreset(draft)); setOpen(false); } };
  const body = (
    <Popover opened={open} onChange={setOpen} position="bottom-start" shadow="md" withinPortal>
      <Popover.Target>
        <Button variant="default" size={size} aria-label={ariaLabel} onClick={() => { setDraft(value); setOpen((v) => !v); }} styles={{ inner: { justifyContent: 'flex-start' } }} fullWidth={fullWidth}>
          <Group gap={6} wrap="nowrap">{preset && <Badge size="xs" color="brand" variant="filled" radius="sm">{PRESET_CN[preset]}</Badge>}<Text size={size} style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtRange(value)}</Text></Group>
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm" w={360}>
          {PRESET_GROUPS.map((g, i) => <SimpleGrid key={i} cols={4} spacing={4}>{g.map((k) => <Button key={k} size="compact-xs" variant={preset === k ? 'filled' : 'light'} onClick={() => pick(k)}>{PRESET_CN[k]}</Button>)}</SimpleGrid>)}
          <Group gap="xs" wrap="nowrap" align="flex-end">
            <TextInput size="xs" label="自訂" type="date" aria-label={`${ariaLabel}開始`} max={max} value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
            <Text size="xs" c="dimmed" pb={8}>～</Text>
            <TextInput size="xs" label=" " type="date" aria-label={`${ariaLabel}結束`} max={max} value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
            <Button size="xs" disabled={!draft.from || !draft.to || draft.from > draft.to} onClick={confirm}>確定</Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
  return label ? <Input.Wrapper label={label}>{body}</Input.Wrapper> : body;
}
