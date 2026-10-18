// 儀表板進階（docs/30）：看板解讀（規則式）、產生 HTML 看板（分享連結、到期）、定時寄送（審批後生效）；AI 生成卡片等 AI 閘道（第二階段）。設計系統元件組合。
import { Fragment, useEffect, useState } from 'react';
import { Alert, Badge, Button, Checkbox, Code, ColorSwatch, Group, List, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from '@mantine/core';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { isDemo } from '@/demo/mode';
import { Mono, SectionTitle, StatusBadge } from '@/ds';
import { KIND_CN } from './dashboards';
import type { AskCard, AskResult, AskStatus, Dashboard, Explain, ExplainCard, RenderArtifact, Schedule } from './types';

const ROLES: [string, string][] = [['admin', '系統管理員'], ['marketing', '行銷'], ['data_analyst', '數據分析'], ['digital_ops', '數位經營'], ['it_admin', '資訊管理']];
const WD = ['週一', '週二', '週三', '週四', '週五', '週六', '週日'];
const ST: Record<Schedule['status'], [string, string]> = { pending_approval: ['待審批', 'amber'], active: ['生效中', 'blue'], paused: ['暫停', 'gray'], rejected: ['已退回', 'red'] };
const RUN: Record<string, string> = { sent: '已寄出', rendered_no_transport: '已產檔（此環境未設 SMTP，未寄信）', failed: '失敗' };
/** 解讀卡片的趨勢訊號色：升／降／持平／無。 */
const SIGNAL: Record<ExplainCard['signal'], string> = { up: '#2E9E6B', down: 'var(--red)', flat: '#C9A227', none: 'var(--muted)' };
/** 多選的原生 select：拿掉單行高度與下拉箭頭。 */
const MULTI = { input: { height: 'auto', minHeight: 70, paddingBlock: 4 } };
const fmt = (s: string) => s.slice(0, 16).replace('T', ' ');
const opt = (value: string, label: string) => ({ value, label });

export function SharePanel({ d, onClose, onChanged }: { d: Dashboard; onClose: () => void; onChanged?: () => void }) {
  const { can } = useAuth();
  const [tab, setTab] = useState<'ask' | 'explain' | 'html' | 'schedule'>('ask');
  const [ask, setAsk] = useState<AskStatus | null>(null);
  useEffect(() => { api.get<AskStatus>('/v1/analytics/ask/status').then(setAsk).catch(() => setAsk(null)); }, []);
  return (
    <Paper mb="md">
      <SectionTitle title="看板進階" right={<Button size="compact-sm" variant="subtle" onClick={onClose}>關閉</Button>}>
        <SegmentedControl size="xs" value={tab} onChange={(v) => setTab(v as typeof tab)} data={[opt('ask', 'AI 生成卡片'), opt('explain', '看板解讀'), opt('html', 'HTML 看板'), opt('schedule', '定時寄送')]} />
        {ask && <Badge color={ask.default === 'llm' ? 'violet' : 'gray'} title={ask.note}>{ask.default === 'llm' ? `模型解析 · ${ask.model}` : '規則解析（未設 LLM 金鑰）'}</Badge>}
      </SectionTitle>
      {tab === 'ask' && <AskTab d={d} status={ask} onAdded={onChanged} />}
      {tab === 'explain' && <ExplainTab d={d} llm={ask?.default === 'llm'} />}
      {tab === 'html' && <HtmlTab d={d} canUse={can('overview', 'use')} />}
      {tab === 'schedule' && <ScheduleTab d={d} canUse={can('overview', 'use')} />}
    </Paper>
  );
}

function ExplainTab({ d, llm }: { d: Dashboard; llm: boolean }) {
  const [ex, setEx] = useState<Explain | null>(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const run = async (mode: 'rules' | 'llm' = 'rules') => { setBusy(true); setErr(null); try { setEx(await api.post<Explain>(`/v1/dashboards/${d.id}/explain`, { mode })); } catch (e) { setErr(e instanceof ApiError ? e.message : '解讀失敗'); } finally { setBusy(false); } };
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">以你的權限重算每張卡片，用規則產生：期間前後對比、漏斗流失最多的步驟、最新同期群留存、最常見路徑、貢獻最高觸點。只讀，不改卡片。AI 閘道啟用後改由 agent 產生自然語言版並可存成洞察。</Text>
      <Group gap="xs"><Button variant="filled" disabled={busy} onClick={() => void run('rules')}>{busy ? '計算中…' : ex ? '重新解讀' : '解讀這個看板'}</Button>{llm && <Button disabled={busy} onClick={() => void run('llm')}>敘述版（模型）</Button>}{ex && <Text size="xs" c="dimmed">{ex.period} · {fmt(ex.generated_at)} · {ex.method === 'llm' ? '模型敘述 + 規則事實' : '規則式'}{ex.note ? ` · ${ex.note}` : ''}</Text>}</Group>
      {err && <Alert color="red" variant="light">{err}</Alert>}
      {ex?.narrative && <Paper shadow="none" radius="md" style={{ borderLeft: '4px solid var(--purple)' }}>{ex.narrative.split(/\n+/).map((p, i) => <Text key={i} size="sm" my={4}>{p}</Text>)}</Paper>}
      {ex && <SimpleGrid cols={{ base: 1, md: 2 }} className="ds-split">
        <div><Text size="sm" fw={600}>本期重點</Text><List size="sm" spacing={4}>{ex.summary.map((s, i) => <List.Item key={i}>{s}</List.Item>)}</List></div>
        <div><Text size="sm" fw={600}>每張卡片</Text><List size="sm" spacing={4}>{ex.cards.map((c) => <List.Item key={c.card} icon={<ColorSwatch size={8} color={SIGNAL[c.signal]} />}>{c.title}：{c.line}{c.warnings.length > 0 && <Text size="xs" c="dimmed">{c.warnings.join('；')}</Text>}</List.Item>)}</List></div>
      </SimpleGrid>}
    </Stack>
  );
}

function HtmlTab({ d, canUse }: { d: Dashboard; canUse: boolean }) {
  const [list, setList] = useState<RenderArtifact[] | null>(null); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [audience, setAudience] = useState(''); const [days, setDays] = useState(14); const [explain, setExplain] = useState(true); const [busy, setBusy] = useState(false);
  const reload = () => api.get<RenderArtifact[]>(`/v1/dashboards/${d.id}/renders`).then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, [d.id]);
  const render = async () => {
    setBusy(true); setErr(null);
    try { const a = await api.post<RenderArtifact>(`/v1/dashboards/${d.id}/render`, { audience, expires_days: days, include_explain: explain }); setMsg(`已產生：${a.cards_included} 張卡片${a.cards_skipped.length ? `，${a.cards_skipped.length} 張因含代碼化識別欄位未納入` : ''}；到期 ${a.expires_at.slice(0, 10)}`); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '產生失敗'); } finally { setBusy(false); }
  };
  const copy = (u: string) => { const full = u.startsWith('http') ? u : `${location.origin}${u}`; void navigator.clipboard?.writeText(full).then(() => setMsg('已複製分享連結')); };
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">伺服器以「你」的權限重算每張卡片，產生單檔 HTML（inline SVG、無明細與 SQL、套遮罩與 K 匿名、浮水印與到期）。分享連結不需登入即可開，到期自動失效；含代碼化識別欄位分組的卡片不會納入。</Text>
      {canUse && <Group gap="xs"><TextInput aria-label="受眾" placeholder="受眾（例：債券部週會）" value={audience} onChange={(e) => setAudience(e.target.value)} style={{ flex: '1 1 220px' }} />
        <NativeSelect aria-label="到期" value={String(days)} data={[7, 14, 30].map((x) => opt(String(x), `${x} 天後到期`))} onChange={(e) => setDays(Number(e.target.value))} />
        <Checkbox label="附本期重點" size="xs" checked={explain} onChange={(e) => setExplain(e.target.checked)} />
        <Button variant="filled" disabled={busy} onClick={() => void render()}>{busy ? '產生中…' : '產生 HTML 看板'}</Button></Group>}
      {msg && <Alert color="teal" variant="light">{msg}</Alert>}{err && <Alert color="red" variant="light">{err}</Alert>}
      {list && list.length > 0 && <Table.ScrollContainer minWidth={760}><Table><Table.Thead><Table.Tr><Table.Th>產生</Table.Th><Table.Th>受眾</Table.Th><Table.Th ta="right">卡片</Table.Th><Table.Th ta="right">大小</Table.Th><Table.Th ta="right">下載</Table.Th><Table.Th>到期</Table.Th><Table.Th /></Table.Tr></Table.Thead>
        <Table.Tbody>{list.map((a) => <Table.Tr key={a.artifact_id}><Table.Td><Mono>{fmt(a.created_at)}</Mono><Text size="xs" c="dimmed">{a.created_by}</Text></Table.Td><Table.Td>{a.audience || '—'}</Table.Td><Table.Td ta="right"><Mono>{a.cards_included}{a.cards_skipped.length ? ` (+${a.cards_skipped.length} 略)` : ''}</Mono></Table.Td><Table.Td ta="right"><Mono>{Math.round(a.bytes / 1024)} KB</Mono></Table.Td><Table.Td ta="right"><Mono>{a.downloads}</Mono></Table.Td>
          <Table.Td>{a.expired ? <StatusBadge tone="gray">已到期</StatusBadge> : <Mono>{a.expires_at.slice(0, 10)}</Mono>}</Table.Td>
          <Table.Td ta="right">{!a.expired && <Group gap={4} justify="flex-end" wrap="nowrap"><Button component="a" size="compact-sm" variant="subtle" href={isDemo ? '/demo/d_seed_bond.html' : a.url} target="_blank" rel="noreferrer">開啟</Button><Button size="compact-sm" variant="subtle" onClick={() => copy(isDemo ? '/demo/d_seed_bond.html' : a.share_url)}>複製分享連結</Button></Group>}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>}
      {list && list.length === 0 && <Text size="sm" c="dimmed">還沒有產物。</Text>}
    </Stack>
  );
}

function ScheduleTab({ d, canUse }: { d: Dashboard; canUse: boolean }) {
  const [list, setList] = useState<Schedule[] | null>(null); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [weekday, setWeekday] = useState<string>('0'); const [hour, setHour] = useState(8); const [roles, setRoles] = useState<string[]>(['digital_ops']); const [emails, setEmails] = useState(''); const [days, setDays] = useState(14);
  const reload = () => api.get<Schedule[]>(`/v1/dashboards/${d.id}/schedules`).then(setList).catch((e: Error) => setErr(e.message));
  useEffect(() => { void reload(); }, [d.id]);
  const create = async () => {
    setErr(null);
    try { const s = await api.post<Schedule>(`/v1/dashboards/${d.id}/schedules`, { weekday: weekday === '' ? null : Number(weekday), hour, audience: { roles, emails: emails.split(/[,\s，、]+/).map((x) => x.trim()).filter(Boolean) }, expires_days: days }); setMsg(s.status === 'active' ? '已建立並生效' : `已送審（審批單 ${s.approval ?? '—'}）：數位經營核可後生效`); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '建立失敗'); }
  };
  const run = async (s: Schedule) => { try { const r = await api.post<Schedule['last_run']>(`/v1/dashboards/${d.id}/schedules/${s.id}/run`); setMsg(`執行完成：${RUN[r?.status ?? ''] ?? r?.status}${r?.recipients ? `，${r.recipients} 位收件人` : ''}`); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '執行失敗'); } };
  const del = async (s: Schedule) => { if (!confirm('刪除此定時寄送？')) return; try { await api.del(`/v1/dashboards/${d.id}/schedules/${s.id}`); await reload(); } catch (e) { setErr(e instanceof ApiError ? e.message : '刪除失敗'); } };
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">每天或每週固定時間（台北）產生 HTML 看板並寄出分享連結；收件人為角色（成員目錄的信箱）或白名單網域信箱。建立後需數位經營審批；看板以你的權限產生。</Text>
      {canUse && <Group gap="xs" align="flex-start">
        <NativeSelect aria-label="星期" value={weekday} data={[opt('', '每天'), ...WD.map((w, i) => opt(String(i), `每${w}`))]} onChange={(e) => setWeekday(e.target.value)} />
        <NativeSelect aria-label="時間" value={String(hour)} data={Array.from({ length: 24 }, (_, h) => opt(String(h), `${String(h).padStart(2, '0')}:00`))} onChange={(e) => setHour(Number(e.target.value))} />
        <NativeSelect multiple aria-label="收件角色" value={roles} styles={MULTI} rightSection={<span />} onChange={(e) => setRoles(Array.from(e.target.selectedOptions).map((o) => o.value))}>{ROLES.map(([k, cn]) => <option key={k} value={k}>{cn}</option>)}</NativeSelect>
        <TextInput aria-label="其他信箱" placeholder="其他信箱（白名單網域，逗號分隔）" value={emails} onChange={(e) => setEmails(e.target.value)} style={{ flex: '1 1 200px' }} />
        <NativeSelect aria-label="到期" value={String(days)} data={[7, 14, 30].map((x) => opt(String(x), `${x} 天到期`))} onChange={(e) => setDays(Number(e.target.value))} />
        <Button variant="filled" disabled={!roles.length && !emails.trim()} onClick={() => void create()}>送審</Button></Group>}
      {msg && <Alert color="teal" variant="light">{msg}</Alert>}{err && <Alert color="red" variant="light">{err}</Alert>}
      {list && list.length > 0 && <Table.ScrollContainer minWidth={720}><Table><Table.Thead><Table.Tr><Table.Th>時間</Table.Th><Table.Th>收件人</Table.Th><Table.Th>狀態</Table.Th><Table.Th>最近執行</Table.Th><Table.Th /></Table.Tr></Table.Thead>
        <Table.Tbody>{list.map((s) => <Table.Tr key={s.id}><Table.Td><Text size="sm">{s.weekday == null ? '每天' : `每${WD[s.weekday]}`} {String(s.hour).padStart(2, '0')}:00</Text><Text size="xs" c="dimmed" ff="monospace">{s.id} · {s.created_by}</Text></Table.Td>
          <Table.Td><Group gap={4}>{s.audience.roles.map((r) => <StatusBadge key={r} tone="blue">{ROLES.find(([k]) => k === r)?.[1] ?? r}</StatusBadge>)}{s.audience.emails.map((e) => <Badge key={e} color="gray">{e}</Badge>)}</Group></Table.Td>
          <Table.Td><StatusBadge tone={ST[s.status][1]}>{ST[s.status][0]}</StatusBadge></Table.Td>
          <Table.Td><Text size="xs" c="dimmed">{s.last_run ? `${fmt(s.last_run.at)} · ${RUN[s.last_run.status] ?? s.last_run.status}${s.last_run.error ? ` · ${s.last_run.error}` : ''}` : '—'}</Text></Table.Td>
          <Table.Td ta="right">{d.editable && <Group gap={4} justify="flex-end" wrap="nowrap">{s.status === 'active' && <Button size="compact-sm" variant="subtle" onClick={() => void run(s)}>立即執行</Button>}<Button size="compact-sm" variant="subtle" color="red" onClick={() => void del(s)}>刪除</Button></Group>}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>}
      {list && list.length === 0 && <Text size="sm" c="dimmed">還沒有定時寄送。</Text>}
    </Stack>
  );
}

function AskTab({ d, status, onAdded }: { d: Dashboard; status: AskStatus | null; onAdded?: () => void }) {
  const { can } = useAuth();
  const [q, setQ] = useState('');
  const [res, setRes] = useState<AskResult | null>(null); const [cards, setCards] = useState<AskCard[]>([]);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const run = async (question = q) => {
    if (!question.trim()) return; setBusy(true); setErr(null); setMsg(null); setRes(null);
    try { const r = await api.post<AskResult>(`/v1/dashboards/${d.id}/ask`, { question }); setRes(r); setCards(r.cards); }
    catch (e) { setErr(e instanceof ApiError ? (typeof e.body === 'object' && e.body && 'detail' in e.body && typeof (e.body as { detail: unknown }).detail === 'object' ? '問題對不上任何分析模型或語意層事件；試試提到事件名（成交、委託、登入、看盤、開戶…）' : e.message) : '解析失敗'); }
    finally { setBusy(false); }
  };
  const chosen = cards.filter((c) => c.selected);
  const add = async (asBookmark: boolean) => {
    setBusy(true); setErr(null);
    try {
      for (const c of chosen) {
        if (asBookmark) await api.post('/v1/bookmarks', { name: c.title, description: c.explanation, kind: c.kind, query: c.query, chart: c.chart, add_to: [d.id] });
        else await api.post(`/v1/dashboards/${d.id}/cards`, { title: c.title, kind: c.kind, query: c.query, chart: c.chart });
      }
      setMsg(`已加入 ${chosen.length} 張卡片${asBookmark ? '（同時存成書籤）' : ''}`); setCards([]); setRes(null); onAdded?.();
    } catch (e) { setErr(e instanceof ApiError ? e.message : '加入失敗'); } finally { setBusy(false); }
  };
  const setCard = (i: number, patch: Partial<AskCard>) => setCards((cs) => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">用一句話（可多句，用「、」或「，」分開）問數：只在事件／漏斗／留存／路徑／歸因五種分析與語意層目錄上做選擇，不生成任意 SQL；每張草稿先以你的權限乾跑編譯，確認後才加入看板（卡片仍以檢視者身分重算）。{status?.note}</Text>
      <Group gap="xs">
        <TextInput aria-label="問題" value={q} onChange={(e) => setQ(e.target.value)} placeholder="例：上週各平台成交金額、看盤到委託到成交的漏斗" style={{ flex: '1 1 420px' }} onKeyDown={(e) => { if (e.key === 'Enter') void run(); }} />
        <Button variant="filled" disabled={busy || !q.trim() || !can('ai_ask', 'use')} onClick={() => void run()}>{busy ? '解析中…' : '產生卡片草稿'}</Button>
      </Group>
      <Group gap="xs"><Text size="xs" c="dimmed">試試：</Text>{(status?.examples ?? []).map((ex) => <Button key={ex} size="compact-sm" variant="subtle" onClick={() => { setQ(ex); void run(ex); }}>{ex}</Button>)}</Group>
      {err && <Alert color="red" variant="light">{err}</Alert>}{msg && <Alert color="teal" variant="light">{msg}</Alert>}
      {res && cards.length > 0 && (
        <Paper p={0} style={{ overflow: 'hidden' }}>
          <Table.ScrollContainer minWidth={760}><Table>
            <Table.Thead><Table.Tr><Table.Th /><Table.Th>卡片標題</Table.Th><Table.Th>分析</Table.Th><Table.Th>信心度</Table.Th><Table.Th>配置說明</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{cards.map((c, i) => (
              <Fragment key={i}><Table.Tr bg={c.selected ? undefined : 'var(--mantine-color-orange-light)'}>
                <Table.Td><Checkbox checked={c.selected} onChange={(e) => setCard(i, { selected: e.target.checked })} aria-label="選取" /></Table.Td>
                <Table.Td><TextInput aria-label="卡片標題" value={c.title} onChange={(e) => setCard(i, { title: e.target.value })} /></Table.Td>
                <Table.Td><StatusBadge tone="blue">{KIND_CN[c.kind]}</StatusBadge></Table.Td>
                <Table.Td><StatusBadge tone={c.confidence >= 0.8 ? 'blue' : c.confidence >= 0.6 ? 'amber' : 'gray'}>{Math.round(c.confidence * 100)}%</StatusBadge></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{c.explanation}</Text>{c.warnings.length > 0 && <Text size="xs" c="dimmed">{c.warnings.join('；')}</Text>}</Table.Td>
                <Table.Td ta="right"><Button size="compact-sm" variant="subtle" onClick={() => setOpen(open === i ? null : i)}>{open === i ? '收合' : '查詢／SQL'}</Button></Table.Td>
              </Table.Tr>{open === i && <Table.Tr><Table.Td colSpan={6}><Code block>{JSON.stringify(c.query, null, 1)}{c.sql_preview ? `\n\n${c.sql_preview}` : ''}</Code></Table.Td></Table.Tr>}</Fragment>
            ))}</Table.Tbody></Table></Table.ScrollContainer>
          <Group gap="xs" p="sm">
            <Button variant="filled" disabled={busy || !chosen.length || !d.editable} onClick={() => void add(false)}>加入看板（{chosen.length}）</Button>
            <Button disabled={busy || !chosen.length || !d.editable} onClick={() => void add(true)}>存成書籤並加入</Button>
            <Text size="xs" c="dimmed">{res.parser === 'llm' ? '模型解析' : '規則解析'} · {res.rejected.length > 0 && `${res.rejected.length} 張因權限或格式不給：${res.rejected.map((r) => `${r.title}（${r.reason}）`).join('；')}`}{res.unmatched.length > 0 && ` · 對不上：${res.unmatched.join('／')}`}{!d.editable && ' · 你不是這個看板的擁有者，無法加入'}</Text>
          </Group>
        </Paper>)}
    </Stack>
  );
}
