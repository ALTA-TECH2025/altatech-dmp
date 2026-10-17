// 示範模式的問數解析：backend/insight_agent/configure.py RulesParser 的同構 TS 版（關鍵字規則；用固定資料的語意層目錄）。正式環境走 /v1/analytics/ask。
export interface AskCard { title: string; kind: 'events' | 'funnel' | 'retention' | 'path' | 'attribution'; query: Record<string, unknown>; chart: string; confidence: number; explanation: string; sql_preview: string | null; warnings: string[]; selected: boolean }
export interface AskResult { run_id: string; parser: 'rules' | 'llm'; cards: AskCard[]; rejected: { title: string; kind: string; reason: string }[]; candidates: { events: { name: string; cn: string }[]; dimensions: string[] }; unmatched: string[]; dashboard: string | null }
interface CatalogLite { events: { name: string; cn: string; status?: string }[]; dimensions: { name: string; cn: string }[] }

const KIND_WORDS: [AskCard['kind'], string[]][] = [['funnel', ['漏斗', '轉換率', '轉化率', '轉換', '轉化', '→', '->', '到']], ['retention', ['留存', '回訪', '回來']], ['path', ['路徑', '之後做什麼', '接著', '下一步', '之前做什麼']], ['attribution', ['歸因', '貢獻', '觸點', '功勞']]];
const SYNONYMS: Record<string, string[]> = { bond_deal: ['成交', '交易成功', '成交筆', '成交金額', '成交量'], bond_order: ['委託', '下單', '掛單'], app_login_success: ['登入', '登錄', '登陸', 'login'], hq_stockdetail_view: ['看盤', '個股', '行情', '看行情'], $pageview: ['瀏覽頁面', '頁面瀏覽', '瀏覽', 'pv', '訪問'], kh_apply: ['開戶申請', '完成開戶', '開戶'], kh_review_pass: ['審核通過', '開戶成功'], sip_purchase: ['定期定額', '申購'], search_submit: ['搜尋', '搜索'], push_click: ['推播點擊', '推播', 'push'], info_report_view: ['研究報告', '研報'], line_follow: ['line 加好友', '加好友'] };
const METRIC_WORDS: [string[], string, string | null][] = [[['金額', '總額', '成交額', '額度'], 'sum', 'amount'], [['人數', '用戶數', '客戶數', '多少人', '幾人', '使用者數', '用戶', '客戶'], 'users', null], [['人均', '平均每人'], 'avg_per_user', null], [['次數', '筆數', '筆', '量', '次'], 'total', null]];
const DIM_WORDS: Record<string, string[]> = { platform: ['平台', 'ios', 'android', 'web'], channel: ['渠道', '通路'], utm_source: ['來源', '流量來源'], market: ['市場'], branch_id: ['分公司'], lifecycle: ['生命週期'], age_band: ['年齡'], app_version: ['版本'] };
const CN_NUM: Record<string, number> = { 一: 1, 二: 2, 兩: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 十四: 14, 三十: 30, 九十: 90 };
const AGG_CN: Record<string, string> = { total: '總次數', users: '觸發用戶數', sum: '金額', avg_per_user: '人均次數' };
const iso = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setUTCDate(x.getUTCDate() + n); return x; };
const num = (s: string) => (/^\d+$/.test(s) ? Number(s) : CN_NUM[s]);

export function parseTime(text: string, today: Date, fallback: Record<string, unknown>): [Record<string, unknown>, boolean] {
  const wd = (today.getUTCDay() + 6) % 7;                                   // 週一 = 0
  if (/上週|上周/.test(text)) { const mon = addDays(today, -wd - 7); return [{ from: iso(mon), to: iso(addDays(mon, 6)), grain: 'day' }, true]; }
  if (/本週|本周|這週/.test(text)) { const mon = addDays(today, -wd); return [{ from: iso(mon), to: iso(today), grain: 'day' }, true]; }
  if (/上個月|上月/.test(text)) { const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1)); const last = addDays(first, -1); return [{ from: iso(new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth(), 1))), to: iso(last), grain: 'day' }, true]; }
  if (/本月|這個月/.test(text)) return [{ from: iso(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))), to: iso(today), grain: 'day' }, true];
  if (/昨天|昨日/.test(text)) { const y = addDays(today, -1); return [{ from: iso(y), to: iso(y), grain: 'day' }, true]; }
  let m = text.match(/(?:近|最近|過去)\s*(\d+|[一二兩三四五六七八九十]+)\s*(天|日|週|周|月)/);
  if (m) { const n = num(m[1]) ?? 7; const unit = /週|周/.test(m[2]) ? 'week' : m[2] === '月' ? 'month' : 'day'; const days = n * (unit === 'day' ? 1 : unit === 'week' ? 7 : 30); return [{ from: iso(addDays(today, -(days - 1))), to: iso(today), grain: unit === 'week' && n >= 4 ? 'week' : 'day' }, true]; }
  m = text.match(/(\d{1,2})\s*月(?!\s*\d)/);
  if (m && Number(m[1]) >= 1 && Number(m[1]) <= 12) { const mo = Number(m[1]); const y = mo <= today.getUTCMonth() + 1 ? today.getUTCFullYear() : today.getUTCFullYear() - 1; const end = addDays(new Date(Date.UTC(y + (mo === 12 ? 1 : 0), mo % 12, 1)), -1); return [{ from: iso(new Date(Date.UTC(y, mo - 1, 1))), to: iso(end < today ? end : today), grain: 'day' }, true]; }
  return [{ ...fallback }, false];
}

export function askRules(question: string, cat: CatalogLite, opts: { today?: Date; defaultTime: Record<string, unknown>; project?: string; dashboard?: string | null } ): AskResult {
  const today = opts.today ?? new Date();
  const events = new Map(cat.events.filter((e) => e.status !== 'deprecated').map((e) => [e.name, e.cn || e.name]));
  const dims = new Map(cat.dimensions.map((d) => [d.name, d.cn]));
  const lex: [string, string][] = [];
  for (const [ev, words] of Object.entries(SYNONYMS)) if (events.has(ev)) for (const w of words) lex.push([w.toLowerCase(), ev]);
  for (const [name, cn] of events) { if (cn && cn !== name) lex.push([cn.toLowerCase(), name]); lex.push([name.toLowerCase(), name]); for (const part of cn.split('_').slice(1)) if (part.length >= 3) lex.push([part.toLowerCase(), name]); }
  lex.sort((a, b) => b[0].length - a[0].length);
  const findEvents = (text: string): string[] => {
    const t = text.toLowerCase(); const found = new Map<string, number>(); const used: [number, number][] = [];
    for (const [w, ev] of lex) { let i = t.indexOf(w); while (i >= 0) { if (!used.some(([a, b]) => (a <= i && i < b) || (a < i + w.length && i + w.length <= b))) { used.push([i, i + w.length]); if (!found.has(ev) || (found.get(ev) ?? 0) > i) found.set(ev, i); break; } i = t.indexOf(w, i + 1); } }
    return [...found.entries()].sort((a, b) => a[1] - b[1]).map(([ev]) => ev);
  };
  const cn = (n: string) => events.get(n) ?? n; const short = (n: string) => { const c = cn(n); return c.includes('_') ? c.slice(c.lastIndexOf('_') + 1) : c; };
  const clauses = question.split(/[，,；;。\n]|以及|、(?=[^，。]{6,})/).map((s) => s.trim()).filter(Boolean).slice(0, 6);
  const cards: AskCard[] = []; const unmatched: string[] = [];
  for (const cl of clauses) {
    const names = findEvents(cl);
    let kind: AskCard['kind'] = 'events'; let explicit = false;
    for (const [k, words] of KIND_WORDS) if (words.some((w) => cl.includes(w)) && (k !== 'funnel' || names.length >= 2 || cl.includes('漏斗') || cl.includes('轉'))) { kind = k; explicit = true; break; }
    const [time, pinned] = parseTime(cl, today, opts.defaultTime); if (pinned) time.pinned = true;
    if (!names.length) { unmatched.push(cl); continue; }
    let conf = explicit ? 0.9 : 0.75; const base: Record<string, unknown> = { time, ...(opts.project ? { project: opts.project } : {}) };
    let q: Record<string, unknown>; let title: string; let expl: string;
    if (kind === 'funnel') {
      const steps = names.length >= 2 ? names : [names[0], names[0] !== 'bond_deal' && events.has('bond_deal') ? 'bond_deal' : names[0]]; if (names.length < 2) conf -= 0.3;
      q = { ...base, steps: steps.map((n) => ({ event: n, alias: short(n) })), window: { value: 7, unit: 'day' }, order: 'loose', metric: 'users' }; title = steps.map(short).join(' → ') + ' 漏斗'; expl = `漏斗：${steps.length} 步（7 天視窗），步驟依問題出現順序`;
    } else if (kind === 'retention') {
      const unit = /週|周/.test(cl) ? 'week' : /個月/.test(cl) ? 'month' : 'day'; const m = cl.match(/(\d+|[一二兩三四五六七八九十]+)\s*(週|周|天|日|個月)/); const periods = (m ? num(m[1]) : undefined) ?? (unit === 'week' ? 4 : 7); const ret = names[1] ?? names[0];
      q = { ...base, initial: { event: names[0] }, return: { event: ret }, model: 'interval', unit, periods: Math.min(periods, 12), include_day0: false }; title = `${cn(names[0])} 後 ${periods} ${unit === 'week' ? '週' : unit === 'day' ? '天' : '月'}留存`; expl = `留存：初始 ${cn(names[0])}、回訪 ${cn(ret)}、${unit}`;
    } else if (kind === 'path') {
      const direction = cl.includes('之前') ? 'before' : 'after'; const m = cl.match(/(\d+)\s*步/); const stepsN = m ? Number(m[1]) : 3;
      q = { ...base, anchor: { event: names[0] }, direction, steps: stepsN, sample: 0.1 }; title = `${cn(names[0])} ${direction === 'before' ? '之前' : '之後'} ${stepsN} 步路徑`; expl = `路徑：以 ${cn(names[0])} 為起點，${stepsN} 步，抽樣 1/10`;
    } else if (kind === 'attribution') {
      const target = names.find((n) => ['bond_deal', 'kh_apply', 'sip_purchase'].includes(n)) ?? names[names.length - 1];
      const touches = names.filter((n) => n !== target); const tl = touches.length ? touches : ['$pageview', 'hq_stockdetail_view', 'app_login_success'].filter((n) => events.has(n) && n !== target);
      q = { ...base, target: { event: target, ...(target === 'bond_deal' ? { value_field: 'amount' } : {}) }, touches: tl.map((n) => ({ event: n, alias: short(n) })), models: ['first', 'last', 'linear'], window_days: 7 }; title = `${cn(target)} 歸因（首次／末次／線性）`; expl = `歸因：轉化 ${cn(target)}，觸點 ${tl.length} 個，7 天回溯`;
    } else {
      let agg = 'total'; let field: string | null = null;
      for (const [words, a, f] of METRIC_WORDS) if (words.some((w) => cl.includes(w))) { agg = a; field = f; break; }
      const metrics = names.slice(0, 3).map((n, i) => ({ event: n, agg, ...(field ? { field } : {}), alias: `m${i + 1}` }));
      const groupBy = Object.entries(DIM_WORDS).filter(([d, words]) => words.some((w) => cl.toLowerCase().includes(w)) && dims.has(d)).map(([d]) => d);
      q = { ...base, metrics, ...(groupBy.length ? { group_by: groupBy.slice(0, 1) } : {}) };
      title = names.slice(0, 3).map(cn).join('、') + ` ${AGG_CN[agg]}` + (groupBy.length ? `（依${dims.get(groupBy[0]) ?? groupBy[0]}）` : ''); expl = `事件分析：${AGG_CN[agg]}` + (groupBy.length ? `，分組 ${groupBy[0]}` : '') + (pinned ? '，時間依問題釘住' : '，時間跟看板走');
      if (!explicit && !METRIC_WORDS.some(([ws]) => ws.some((w) => cl.includes(w)))) conf -= 0.1;
    }
    const confidence = Math.round(Math.max(0.1, Math.min(conf, 0.98)) * 100) / 100;
    cards.push({ title: title.slice(0, 80), kind, query: q, chart: 'auto', confidence, explanation: `${expl}；事件：${names.map((n) => `${cn(n)}（${n}）`).join(', ')}`, sql_preview: '-- 示範模式：未執行編譯', warnings: ['示範模式：規則解析，未乾跑編譯'], selected: confidence >= 0.6 });
  }
  return { run_id: `ask_demo${Math.random().toString(36).slice(2, 8)}`, parser: 'rules', cards: cards.filter((c) => c.confidence >= 0.3), rejected: [], candidates: { events: [], dimensions: [...dims.keys()].slice(0, 10) }, unmatched, dashboard: opts.dashboard ?? null };
}
