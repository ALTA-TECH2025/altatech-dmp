// 行為篩選（用戶做過／未做過）與對照組：事件分析、漏斗、留存共用（契約 behaviors／behaviors_op／cohorts）。
// 行為卡片沿用分群規則的 BehaviorCard；對照組 = 組名 + 自己的屬性條件樹 + 分群 + 行為。送後端前用 cleanScope 清掉空條件。
import { ActionIcon, Badge, Button, Group, NativeSelect, Paper, Stack, Text, TextInput } from '@mantine/core';
import { BehaviorCard, GroupEditor } from '@/users/RuleBuilder';
import { behaviorOk, emptyCond, groupOk, newBehavior, pruneGroup, toGroup, type FieldOpt } from '@/users/rule';
import type { CondGroup, UsersBehavior } from '@/users/types';
import type { Catalog, Cohort, Scope } from './types';

export const MAX_COHORTS = 10;
const opt = (value: string, label: string) => ({ value, label });

/** 送後端前：空行為拿掉、條件樹剪枝、沒名字的對照組拿掉；什麼都沒有就不帶欄位。 */
export function cleanScope(s: Scope): Scope {
  const beh = (s.behaviors ?? []).filter(behaviorOk).map((b) => { const { count, ...rest } = b; const base: UsersBehavior = { ...rest, filters: pruneGroup(toGroup(b.filters)), cmp: b.cmp ?? count }; if (b.done === false) { delete base.measure; delete base.cmp; } return base; });
  const cohorts = (s.cohorts ?? []).filter((c) => c.name.trim()).map((c) => {
    const bs = (c.behaviors ?? []).filter(behaviorOk);
    return { name: c.name.trim(), filters: pruneGroup(toGroup(c.filters as CondGroup | undefined)), segment: c.segment || undefined, behaviors: bs.length ? bs : undefined, behaviors_op: bs.length > 1 ? c.behaviors_op ?? 'and' : undefined };
  });
  return { behaviors: beh.length ? beh : undefined, behaviors_op: beh.length > 1 ? s.behaviors_op ?? 'and' : undefined, cohorts: cohorts.length ? cohorts : undefined };
}
/** 表單是否可送：有事件的行為要完整；對照組要有名字且條件完整。 */
export function scopeOk(s: Scope): boolean {
  const okB = (bs: UsersBehavior[] | undefined) => (bs ?? []).every((b) => !b.event || behaviorOk(b));
  return okB(s.behaviors) && (s.cohorts ?? []).every((c) => c.name.trim().length > 0 && groupOk(toGroup(c.filters as CondGroup | undefined)) && okB(c.behaviors));
}

function OpSelect({ value, label, onChange }: { value: 'and' | 'or'; label: string; onChange: (v: 'and' | 'or') => void }) {
  return <NativeSelect size="xs" w={150} aria-label={label} value={value} data={[opt('and', '全部符合（且）'), opt('or', '任一符合（或）')]} onChange={(e) => onChange(e.target.value as 'and' | 'or')} />;
}

function BehaviorList({ bs, op, cat, label, onChange }: { bs: UsersBehavior[]; op: 'and' | 'or'; cat: Catalog | null; label: string; onChange: (bs: UsersBehavior[], op: 'and' | 'or') => void }) {
  return (
    <Stack gap="xs">
      {bs.length > 1 && <Group gap="xs"><Text size="xs" c="dimmed">行為之間</Text><OpSelect value={op} label={`${label}行為組合`} onChange={(v) => onChange(bs, v)} /></Group>}
      {bs.map((b, i) => <BehaviorCard key={i} b={b} i={i} cat={cat} onChange={(x) => onChange(bs.map((y, j) => (j === i ? x : y)), op)} onRemove={() => onChange(bs.filter((_, j) => j !== i), op)} />)}
      {bs.length < 10 && <div><Button size="compact-sm" variant="light" onClick={() => onChange([...bs, newBehavior()], op)}>＋ 用戶做過／未做過</Button></div>}
    </Stack>
  );
}

export function ScopeEditor({ value, onChange, cat, fields, segments, note }: { value: Scope; onChange: (s: Scope) => void; cat: Catalog | null; fields: FieldOpt[]; segments: { id: string; name: string }[]; note?: string }) {
  const bs = value.behaviors ?? []; const cs = value.cohorts ?? [];
  const setCohort = (i: number, c: Cohort) => onChange({ ...value, cohorts: cs.map((x, j) => (j === i ? c : x)) });
  return (
    <Stack gap="md">
      <div>
        <Group gap="xs" align="baseline" mb={6}><Text fw={700} size="sm">行為篩選</Text><Text size="xs" c="dimmed">只算「做過／未做過」符合的用戶；與分群規則同一套條件{note ? `；${note}` : ''}</Text></Group>
        <BehaviorList bs={bs} op={value.behaviors_op ?? 'and'} cat={cat} label="" onChange={(nb, op) => onChange({ ...value, behaviors: nb, behaviors_op: op })} />
      </div>
      <div>
        <Group gap="xs" align="baseline" justify="space-between" mb={6}>
          <Group gap="xs" align="baseline"><Text fw={700} size="sm">對照組</Text><Text size="xs" c="dimmed">每組一套條件各算一次、並排比較（最多 {MAX_COHORTS} 組）；條件與上面的篩選取交集</Text></Group>
          {cs.length < MAX_COHORTS && <Button size="compact-sm" variant="light" color="violet" onClick={() => onChange({ ...value, cohorts: [...cs, { name: `對照組 ${cs.length + 1}`, filters: { op: 'and', items: [] }, behaviors: [] }] })}>＋ 對照組</Button>}
        </Group>
        <Stack gap="xs">
          {cs.map((c, i) => { const fg = toGroup(c.filters as CondGroup | undefined); const L = `對照組 ${i + 1} `; return (
            <Paper key={i} p="sm" radius="md" shadow="none" withBorder>
              <Stack gap="xs">
                <Group gap="xs" wrap="wrap">
                  <Badge color="violet" variant="filled" circle>{i + 1}</Badge>
                  <TextInput w={180} aria-label={`${L}名稱`} value={c.name} maxLength={40} placeholder="組名" onChange={(e) => setCohort(i, { ...c, name: e.target.value })} />
                  {segments.length > 0 && <NativeSelect w={200} aria-label={`${L}分群`} value={c.segment ?? ''} data={[opt('', '不限分群'), ...segments.map((s) => opt(s.id, s.name))]} onChange={(e) => setCohort(i, { ...c, segment: e.target.value || undefined })} />}
                  {!fg.items.length && <Button size="compact-sm" variant="subtle" onClick={() => setCohort(i, { ...c, filters: { op: 'and', items: [emptyCond()] } })}>＋ 屬性條件</Button>}
                  <ActionIcon color="red" aria-label={`刪除${L.trim()}`} onClick={() => onChange({ ...value, cohorts: cs.filter((_, j) => j !== i) })}>✕</ActionIcon>
                </Group>
                {fg.items.length > 0 && <div className="rb"><GroupEditor g={fg} fields={fields} cat={cat} label={`${L}條件 `} onChange={(ng) => setCohort(i, { ...c, filters: ng })} /></div>}
                <BehaviorList bs={c.behaviors ?? []} op={c.behaviors_op ?? 'and'} cat={cat} label={L} onChange={(nb, op) => setCohort(i, { ...c, behaviors: nb, behaviors_op: op })} />
              </Stack>
            </Paper>); })}
        </Stack>
      </div>
    </Stack>
  );
}
