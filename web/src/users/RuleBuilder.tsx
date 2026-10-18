// 用戶條件建構器（用戶列表、分群、標籤值共用）：神策式規則——用戶屬性／分群條件樹（且／或可巢狀）、行為（做過／未做過 × 指標 × 比較 × 近 N 天或自訂期間 × 事件屬性條件樹）、行為序列（A → B 在窗口內依序完成）、屬性塊與行為塊之間的且／或、來源落點（唯讀）。
import { ActionIcon, Badge, Button, Group, NativeSelect, Paper, Stack, Text, TextInput } from '@mantine/core';
import type { Catalog, Cond } from '@/analytics/types';
import { CMP_OPS, MAX_DEPTH, MEASURES, NO_VALUE_OPS, NUMERIC_MEASURES, OP_CN, WINDOW_UNITS, condValue, emptyCond, eventFields, isGroup, newBehavior, newSequence, numericEventFields, opsFor, rawValue, sourceText, toGroup, userFields, type FieldOpt } from './rule';
import type { CmpOp, CondGroup, MeasureAgg, UsersBehavior, UsersQuery, UsersSequence } from './types';

function today(offset = 0): string { const d = new Date(); d.setDate(d.getDate() + offset); return d.toISOString().slice(0, 10); }
const opt = (v: string, label: string) => ({ value: v, label });

/** 且／或切換：直立在群組左側，一鍵切換。 */
function OpToggle({ op, label, onChange }: { op: 'and' | 'or'; label: string; onChange: (op: 'and' | 'or') => void }) {
  return <Button className="rb-op" variant="light" color={op === 'and' ? 'brand' : 'violet'} size="compact-xs" aria-label={label} title="點一下切換 且／或" onClick={() => onChange(op === 'and' ? 'or' : 'and')}>{op === 'and' ? '且' : '或'}</Button>;
}

function CondRow({ c, fields, cat, onChange, onRemove, label }: { c: Cond; fields: FieldOpt[]; cat: Catalog | null; onChange: (c: Cond) => void; onRemove: () => void; label: string }) {
  const f = fields.find((x) => x.v === c.field); const ops = opsFor(cat, f);
  const needsValue = !NO_VALUE_OPS.has(c.op) && !c.field.startsWith('seg.');
  return (
    <Group gap="xs" wrap="wrap">
      <NativeSelect w={200} value={c.field} aria-label={`${label}欄位`} data={[opt('', '選欄位…'), ...fields.map((x) => opt(x.v, x.cn))]} onChange={(e) => { const nf = fields.find((x) => x.v === e.target.value); onChange({ field: e.target.value, op: opsFor(cat, nf)[0], value: '' }); }} />
      <NativeSelect w={130} value={c.op} aria-label={`${label}運算子`} data={ops.map((o) => opt(o, OP_CN[o] ?? o))} onChange={(e) => onChange({ ...c, op: e.target.value, value: NO_VALUE_OPS.has(e.target.value) ? undefined : '' })} />
      {needsValue && (f?.values && ['eq', 'neq'].includes(c.op)
        ? <NativeSelect w={170} value={String(c.value ?? '')} aria-label={`${label}值`} data={[opt('', '選值…'), ...f.values.map((v) => opt(v, v))]} onChange={(e) => onChange({ ...c, value: e.target.value })} />
        : <TextInput w={190} aria-label={`${label}值`} type={c.op === 'rel' || (f?.type === 'number' && !['in', 'nin', 'between'].includes(c.op)) ? 'number' : 'text'} value={rawValue(c.value)}
            placeholder={c.op === 'rel' ? '天數' : ['in', 'nin'].includes(c.op) ? (f?.values ? f.values.join(',') : '逗號分隔') : c.op === 'between' ? '下限,上限' : ''}
            onChange={(e) => onChange({ ...c, value: condValue(f?.type ?? 'string', c.op, e.target.value) })} />)}
      <ActionIcon color="red" aria-label={`刪除${label}`} onClick={onRemove}>✕</ActionIcon>
    </Group>
  );
}

/** 條件樹編輯：左側「且／或」切換，右側條件列或子群組；可加條件、加條件組（最多 MAX_DEPTH 層）。 */
export function GroupEditor({ g, fields, cat, label, depth = 1, onChange }: { g: CondGroup; fields: FieldOpt[]; cat: Catalog | null; label: string; depth?: number; onChange: (g: CondGroup) => void }) {
  const setItem = (i: number, it: Cond | CondGroup) => onChange({ ...g, items: g.items.map((x, j) => (j === i ? it : x)) });
  const rm = (i: number) => onChange({ ...g, items: g.items.filter((_, j) => j !== i) });
  const body = (
    <Group gap="sm" align="stretch" wrap="nowrap">
      {g.items.length > 1 && <OpToggle op={g.op} label={`${label}組合`} onChange={(op) => onChange({ ...g, op })} />}
      <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
        {g.items.map((it, i) => isGroup(it)
          ? <GroupEditor key={i} g={it} fields={fields} cat={cat} label={`${label}組 ${i + 1} `} depth={depth + 1} onChange={(ng) => setItem(i, ng)} />
          : <CondRow key={i} c={it} fields={fields} cat={cat} label={`${label}${i + 1} `} onChange={(nc) => setItem(i, nc)} onRemove={() => rm(i)} />)}
        <Group gap="xs">
          <Button size="compact-sm" variant="subtle" onClick={() => onChange({ ...g, items: [...g.items, emptyCond()] })}>＋ 條件</Button>
          {depth < MAX_DEPTH && <Button size="compact-sm" variant="subtle" onClick={() => onChange({ ...g, items: [...g.items, { op: g.op === 'and' ? 'or' : 'and', items: [emptyCond()] }] })}>＋ 條件組（{g.op === 'and' ? '或' : '且'}）</Button>}
          {depth > 1 && !g.items.length && <Text size="xs" c="dimmed">空的條件組不會送出</Text>}
        </Group>
      </Stack>
    </Group>
  );
  return depth > 1 ? <Paper p="xs" radius="md" shadow="none" style={{ borderStyle: 'dashed' }}>{body}</Paper> : body;
}

/** 期間：近 N 天或自訂起訖（行為與序列共用）。 */
function PeriodInput({ t, L, onChange }: { t: { last_days?: number; from?: string; to?: string }; L: string; onChange: (t: { last_days?: number; from?: string; to?: string }) => void }) {
  const mode: 'last' | 'range' = t.from || t.to ? 'range' : 'last';
  return <>
    <NativeSelect w={190} value={mode} aria-label={`${L}期間方式`} data={[opt('last', '近 N 天（以計算日回推）'), opt('range', '自訂期間')]} onChange={(e) => onChange(e.target.value === 'last' ? { last_days: 30 } : { from: today(-30), to: today() })} />
    {mode === 'last'
      ? <><Text size="xs" c="dimmed">近</Text><TextInput w={80} type="number" min={1} max={730} aria-label={`${L}近 N 天`} value={t.last_days ?? ''} onChange={(e) => onChange({ last_days: Number(e.target.value) })} /><Text size="xs" c="dimmed">天</Text></>
      : <><TextInput w={150} type="date" aria-label={`${L}開始`} value={t.from ?? ''} onChange={(e) => onChange({ from: e.target.value, to: t.to ?? '' })} /><Text size="xs" c="dimmed">～</Text><TextInput w={150} type="date" aria-label={`${L}結束`} value={t.to ?? ''} onChange={(e) => onChange({ from: t.from ?? '', to: e.target.value })} /></>}
  </>;
}

/** 行為序列：依序完成 2–6 步，從第一步起算的窗口內完成；每步可加事件屬性條件。 */
function SequenceCard({ s, i, cat, onChange, onRemove }: { s: UsersSequence; i: number; cat: Catalog | null; onChange: (s: UsersSequence) => void; onRemove: () => void }) {
  const ef = eventFields(cat); const L = `序列 ${i + 1} `;
  const setStep = (k: number, st: UsersSequence['steps'][number]) => onChange({ ...s, steps: s.steps.map((x, j) => (j === k ? st : x)) });
  return (
    <Paper p="sm" radius="md" shadow="none" bg="var(--soft)">
      <Stack gap="xs">
        <Group gap="xs" wrap="nowrap">
          <Badge color="violet" variant="filled" circle>{i + 1}</Badge>
          <NativeSelect w={150} value={s.done === false ? 'no' : 'yes'} aria-label={`${L}完成`} data={[opt('yes', '依序完成'), opt('no', '沒有依序完成')]} onChange={(e) => onChange({ ...s, done: e.target.value === 'yes' })} />
          <Text size="xs" c="dimmed">從第 1 步起</Text>
          <TextInput w={80} type="number" min={1} aria-label={`${L}窗口`} value={s.window.value} onChange={(e) => onChange({ ...s, window: { ...s.window, value: Number(e.target.value) } })} />
          <NativeSelect w={90} value={s.window.unit} aria-label={`${L}窗口單位`} data={WINDOW_UNITS.map(([u, cn]) => opt(u, cn))} onChange={(e) => onChange({ ...s, window: { ...s.window, unit: e.target.value as UsersSequence['window']['unit'] } })} />
          <Text size="xs" c="dimmed" style={{ flex: 1 }}>內完成</Text>
          <ActionIcon color="red" aria-label={`刪除${L.trim()}`} onClick={onRemove}>✕</ActionIcon>
        </Group>
        {s.steps.map((st, k) => { const fl = toGroup(st.filters); return (
          <Stack key={k} gap={4} pl="md" style={{ borderLeft: '2px solid var(--brand-line)' }}>
            <Group gap="xs" wrap="nowrap">
              <Text size="xs" fw={700} w={44}>第 {k + 1} 步</Text>
              <NativeSelect style={{ flex: 1 }} value={st.event} aria-label={`${L}第 ${k + 1} 步事件`} data={[opt('', '選事件…'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))]} onChange={(e) => setStep(k, { ...st, event: e.target.value })} />
              {!fl.items.length && <Button size="compact-xs" variant="subtle" onClick={() => setStep(k, { ...st, filters: { op: 'and', items: [emptyCond()] } })}>＋ 屬性</Button>}
              {s.steps.length > 2 && <ActionIcon variant="subtle" color="red" aria-label={`${L}刪除第 ${k + 1} 步`} onClick={() => onChange({ ...s, steps: s.steps.filter((_, j) => j !== k) })}>✕</ActionIcon>}
            </Group>
            {fl.items.length > 0 && <GroupEditor g={fl} fields={ef} cat={cat} label={`${L}第 ${k + 1} 步屬性 `} onChange={(ng) => setStep(k, { ...st, filters: ng })} />}
          </Stack>); })}
        <Group gap="xs" wrap="wrap">
          {s.steps.length < 6 && <Button size="compact-sm" variant="subtle" onClick={() => onChange({ ...s, steps: [...s.steps, { event: '' }] })}>＋ 步驟</Button>}
          <Text size="xs" c="dimmed" w={30}>期間</Text>
          <PeriodInput t={s.time ?? {}} L={L} onChange={(t) => onChange({ ...s, time: t })} />
        </Group>
      </Stack>
    </Paper>
  );
}

function BehaviorCard({ b, i, cat, onChange, onRemove }: { b: UsersBehavior; i: number; cat: Catalog | null; onChange: (b: UsersBehavior) => void; onRemove: () => void }) {
  const ef = eventFields(cat); const nf = numericEventFields(cat); const L = `行為 ${i + 1} `;
  const agg = b.measure?.agg ?? 'count'; const cmp = b.cmp ?? b.count ?? { op: 'gte' as CmpOp, value: 1 };
  const t = b.time ?? {};
  const fl = toGroup(b.filters);
  return (
    <Paper p="sm" radius="md" shadow="none" bg="var(--soft)">
      <Stack gap="xs">
        <Group gap="xs" wrap="nowrap">
          <Badge color="brand" variant="filled" circle>{i + 1}</Badge>
          <NativeSelect w={100} value={b.done === false ? 'no' : 'yes'} aria-label={`${L}做過`} data={[opt('yes', '做過'), opt('no', '未做過')]} onChange={(e) => onChange({ ...b, done: e.target.value === 'yes' })} />
          <NativeSelect style={{ flex: 1 }} value={b.event} aria-label={`${L}事件`} data={[opt('', '選事件…'), ...(cat?.events ?? []).map((ev) => opt(ev.name, `${ev.cn}（${ev.name}）`))]} onChange={(e) => onChange({ ...b, event: e.target.value })} />
          <ActionIcon color="red" aria-label={`刪除${L.trim()}`} onClick={onRemove}>✕</ActionIcon>
        </Group>
        {b.done !== false && <Group gap="xs" wrap="wrap">
          <Text size="xs" c="dimmed" w={30}>指標</Text>
          <NativeSelect w={120} value={agg} aria-label={`${L}指標`} data={MEASURES.map(([a, cn]) => opt(a, cn))} onChange={(e) => { const a = e.target.value as MeasureAgg; onChange({ ...b, measure: { agg: a, field: NUMERIC_MEASURES.includes(a) ? (b.measure?.field ?? nf[0]?.v) : undefined }, cmp: { op: cmp.op, value: cmp.op === 'between' ? [1, 3] : 1 } }); }} />
          {NUMERIC_MEASURES.includes(agg) && <NativeSelect w={160} value={b.measure?.field ?? ''} aria-label={`${L}指標欄位`} data={[opt('', '選數值屬性…'), ...nf.map((f) => opt(f.v, f.cn))]} onChange={(e) => onChange({ ...b, measure: { agg, field: e.target.value } })} />}
          <NativeSelect w={90} value={cmp.op} aria-label={`${L}比較`} data={CMP_OPS.map(([o, cn]) => opt(o, cn))} onChange={(e) => { const op = e.target.value as CmpOp; onChange({ ...b, cmp: { op, value: op === 'between' ? [1, 3] : (Array.isArray(cmp.value) ? cmp.value[0] : cmp.value) } }); }} />
          <TextInput w={120} aria-label={`${L}值`} type={cmp.op === 'between' ? 'text' : 'number'} value={rawValue(cmp.value)} placeholder={cmp.op === 'between' ? '下限,上限' : agg === 'count' ? '次數' : agg === 'days' ? '天數' : '數值'}
            onChange={(e) => onChange({ ...b, cmp: { op: cmp.op, value: cmp.op === 'between' ? (e.target.value.split(',').map(Number) as [number, number]) : Number(e.target.value) } })} />
          <Text size="xs" c="dimmed">{agg === 'count' ? '次' : agg === 'days' ? '天' : ''}</Text>
        </Group>}
        <Group gap="xs" wrap="wrap">
          <Text size="xs" c="dimmed" w={30}>期間</Text>
          <PeriodInput t={t} L={L} onChange={(nt) => onChange({ ...b, time: nt })} />
          {!fl.items.length && <Button size="compact-sm" variant="subtle" onClick={() => onChange({ ...b, filters: { op: 'and', items: [emptyCond()] } })}>＋ 事件屬性條件</Button>}
        </Group>
        {fl.items.length > 0 && <Stack gap={4}><Text size="xs" c="dimmed">事件屬性條件</Text><GroupEditor g={fl} fields={ef} cat={cat} label={`${L}屬性 `} onChange={(ng) => onChange({ ...b, filters: ng })} /></Stack>}
      </Stack>
    </Paper>
  );
}

export function RuleBuilder({ q, cat, segments, onChange, readOnly }: { q: UsersQuery; cat: Catalog | null; segments: { id: string; name: string }[]; onChange: (q: UsersQuery) => void; readOnly?: boolean }) {
  const uf = userFields(cat, segments); const g = toGroup(q.filters); const bs = q.behaviors ?? []; const ss = q.sequences ?? [];
  if (readOnly) return <Text size="sm" c="dimmed">{q.source?.type === 'import' ? '導入名單分群' : '結果保存分群'}：規則不可編輯。來源：{q.source ? sourceText(q.source) : '—'}</Text>;
  const nb = bs.length + ss.length;
  return (
    <Stack gap="md" className="rb">
      {q.source && <Group gap="xs"><Badge color="violet">來源落點</Badge><Text size="sm">{sourceText(q.source)}</Text><Button size="compact-sm" variant="subtle" color="red" onClick={() => onChange({ ...q, source: undefined })}>移除</Button></Group>}
      <Stack gap="xs">
        <Group gap="xs" align="baseline"><Text fw={700} size="sm">用戶屬性／分群</Text><Text size="xs" c="dimmed">用戶表欄位、所屬分群；條件可分組，群組內與群組間都可選「且／或」</Text></Group>
        <GroupEditor g={g} fields={uf} cat={cat} label="條件 " onChange={(ng) => onChange({ ...q, filters: ng })} />
      </Stack>
      {g.items.length > 0 && nb > 0 && <Group gap="xs" pl="sm" style={{ borderLeft: '3px solid var(--brand-line)' }}><Text size="xs" c="dimmed">用戶屬性條件 與 行為條件 之間</Text><OpToggle op={q.combine ?? 'and'} label="屬性與行為組合" onChange={(op) => onChange({ ...q, combine: op })} /><Text size="xs" c="dimmed">{(q.combine ?? 'and') === 'and' ? '兩邊都要符合' : '任一邊符合即可'}</Text></Group>}
      <Stack gap="xs">
        <Group gap="xs" align="baseline" justify="space-between"><Group gap="xs" align="baseline"><Text fw={700} size="sm">行為（做過／未做過、依序完成）</Text><Text size="xs" c="dimmed">事件 × 指標 × 比較 × 期間 × 事件屬性；序列＝先 A 後 B，在窗口內依序完成</Text></Group>
          <Group gap="xs"><Button size="compact-sm" variant="light" onClick={() => onChange({ ...q, behaviors: [...bs, newBehavior()] })}>＋ 行為</Button>
            {ss.length < 5 && <Button size="compact-sm" variant="light" color="violet" onClick={() => onChange({ ...q, sequences: [...ss, newSequence()] })}>＋ 行為序列</Button>}</Group></Group>
        {nb > 0 && <Group gap="sm" align="stretch" wrap="nowrap">
          {nb > 1 && <OpToggle op={q.behaviors_op ?? 'and'} label="行為組合" onChange={(op) => onChange({ ...q, behaviors_op: op })} />}
          <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
            {bs.map((b, i) => <BehaviorCard key={`b${i}`} b={b} i={i} cat={cat} onChange={(x) => onChange({ ...q, behaviors: bs.map((y, j) => (j === i ? x : y)) })} onRemove={() => onChange({ ...q, behaviors: bs.filter((_, j) => j !== i) })} />)}
            {ss.map((s, i) => <SequenceCard key={`s${i}`} s={s} i={i} cat={cat} onChange={(x) => onChange({ ...q, sequences: ss.map((y, j) => (j === i ? x : y)) })} onRemove={() => onChange({ ...q, sequences: ss.filter((_, j) => j !== i) })} />)}
          </Stack>
        </Group>}
      </Stack>
    </Stack>
  );
}
