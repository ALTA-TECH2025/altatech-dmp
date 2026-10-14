// AltaTech DMP 原型 — 模擬資料（以固定種子產生，每次載入一致）
'use strict';

/* ------------------------------------------------------------------ seeded prng */
let seed = 20261014;
export const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
const ri = (a, b) => a + Math.floor(rnd() * (b - a + 1));
const pick = arr => arr[Math.floor(rnd() * arr.length)];

export const TODAY = new Date(2026, 9, 14); // 2026-10-14
export const pad2 = n => String(n).padStart(2, '0');
export const dkey = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
export const dayOffset = (n, from = TODAY) => { const d = new Date(from); d.setDate(d.getDate() + n); return d; };

/* ------------------------------------------------------------------ accounts (demo roles) */
export const ACCOUNTS = [
  { id: 'u-ana', name: '林雅婷', email: 'yating.lin@altatech.demo', role: '數據分析師', col: '#1F4E8C', init: '林', scope: 'all', can: { detail: true, sql: true, approve: false, admin: false } },
  { id: 'u-ops', name: '陳柏宇', email: 'boyu.chen@altatech.demo', role: '數位營運主管', col: '#0F8F85', init: '陳', scope: 'all', can: { detail: true, sql: false, approve: true, admin: false } },
  { id: 'u-agt', name: '王信宏', email: 'hsinhung.wang@altatech.demo', role: '營業員（台北分公司）', col: '#D8871F', init: '王', scope: 'branch:TPE01', can: { detail: true, sql: false, approve: false, admin: false } },
  { id: 'u-adm', name: '張敏慧', email: 'minhui.chang@altatech.demo', role: '平台管理員', col: '#6250d6', init: '張', scope: 'all', can: { detail: true, sql: true, approve: true, admin: true } },
];

/* ------------------------------------------------------------------ sources */
export const SOURCES = [
  { id: 'ga360_daily', name: 'GA360 日批匯出', kind: 'batch', lag: '前日 05:10 到齊', status: 'ok', events24h: 182430112, note: 'events_20261013 已對帳，差異 0.3%' },
  { id: 'ga360_fresh', name: 'GA360 Fresh Daily', kind: 'batch', lag: '約 55 分', status: 'ok', events24h: 41210540, note: '日內增量；日批到齊後覆蓋' },
  { id: 'gtm_ss', name: 'GTM Server-side 即時雙寫', kind: 'stream', lag: '2.1 秒', status: 'ok', events24h: 180915377, note: '境內容器，個資閘門已啟用' },
  { id: 'trade_cdc', name: '交易主機 CDC（委託／成交）', kind: 'stream', lag: '1.4 秒', status: 'ok', events24h: 18240902, note: 'Debezium → Flink 狀態機事件化' },
  { id: 'ledger_cdc', name: '帳務 CDC（出入金）', kind: 'stream', lag: '3.8 秒', status: 'ok', events24h: 612430, note: '' },
  { id: 'account_sys', name: '開戶系統', kind: 'api', lag: '8 秒', status: 'warn', events24h: 9812, note: 'account_open_submit 近 2 小時量降 38%（iOS）' },
  { id: 'ma', name: '行銷自動化（推播／活動）', kind: 'stream', lag: '12 秒', status: 'ok', events24h: 2104880, note: '' },
  { id: 'crm', name: 'CRM 客戶主檔', kind: 'batch', lag: '前日 02:00', status: 'ok', events24h: 1230119, note: 'profile_set 日批' },
];

/* ------------------------------------------------------------------ event dictionary */
export const EVENTS = [
  { name: 'ga.page_view', cn: '頁面瀏覽', entity: 'user', source: 'GA360', kind: 'behavior', status: 'live', daily: 61200000, props: ['$page_url', 'page_group', '$referrer', '$utm_*'], owner: '數位行銷' },
  { name: 'ga.screen_view', cn: '畫面瀏覽', entity: 'user', source: 'GA360', kind: 'behavior', status: 'live', daily: 88400000, props: ['$screen_name', '$app_version'], owner: 'App 產品' },
  { name: 'quote_view', cn: '看盤', entity: 'user', source: 'GA360', kind: 'behavior', status: 'live', daily: 24800000, props: ['symbol', 'market', 'duration_ms'], owner: 'App 產品' },
  { name: 'search_symbol', cn: '搜尋商品', entity: 'user', source: 'GA360', kind: 'behavior', status: 'live', daily: 3100000, props: ['keyword', 'result_count'], owner: 'App 產品' },
  { name: 'watchlist_add', cn: '加入自選', entity: 'user', source: 'GA360', kind: 'behavior', status: 'live', daily: 412000, props: ['symbol'], owner: 'App 產品' },
  { name: 'login', cn: '登入', entity: 'user', source: 'App 後台', kind: 'behavior', status: 'live', daily: 2980000, props: ['channel', 'auth_method'], owner: '數位平台' },
  { name: 'account_open_start', cn: '開戶開始', entity: 'user', source: '開戶系統', kind: 'business', status: 'live', daily: 6400, props: ['channel'], owner: '數位開戶' },
  { name: 'account_open_submit', cn: '開戶送件', entity: 'user', source: '開戶系統', kind: 'business', status: 'live', daily: 2950, props: ['channel', 'account_types', 'kyc_level'], owner: '數位開戶' },
  { name: 'account_open_approved', cn: '開戶核准', entity: 'user', source: '開戶系統', kind: 'business', status: 'live', daily: 2310, props: ['account_no', 'account_type', 'branch_id', 'agent_id'], owner: '數位開戶' },
  { name: 'fund_deposit', cn: '入金', entity: 'account', source: '帳務 CDC', kind: 'trade', status: 'live', daily: 310000, props: ['amount', 'method', 'bank_code'], owner: '帳務' },
  { name: 'order_submit', cn: '委託', entity: 'account', source: '交易 CDC', kind: 'trade', status: 'live', daily: 7900000, props: ['order_no', 'symbol', 'market', 'side', 'order_type', 'price', 'qty', 'amount', 'channel'], owner: '交易系統' },
  { name: 'order_filled', cn: '成交', entity: 'account', source: '交易 CDC', kind: 'trade', status: 'live', daily: 5600000, props: ['order_no', 'filled_qty', 'filled_price', 'filled_amount', 'fee', 'tax'], owner: '交易系統' },
  { name: 'order_rejected', cn: '委託失敗', entity: 'account', source: '交易 CDC', kind: 'trade', status: 'live', daily: 118000, props: ['order_no', 'reason_code'], owner: '交易系統' },
  { name: 'order_cancelled', cn: '刪單', entity: 'account', source: '交易 CDC', kind: 'trade', status: 'live', daily: 1900000, props: ['order_no', 'cancel_qty'], owner: '交易系統' },
  { name: 'fund_subscribe', cn: '基金申購', entity: 'account', source: '基金平台', kind: 'trade', status: 'live', daily: 41000, props: ['fund_code', 'amount', 'plan_type'], owner: '財富管理' },
  { name: 'push_opened', cn: '推播開啟', entity: 'user', source: 'MA', kind: 'marketing', status: 'live', daily: 980000, props: ['campaign_id', 'push_id'], owner: '數位行銷' },
  { name: 'campaign_clicked', cn: '活動點擊', entity: 'user', source: 'GA360', kind: 'marketing', status: 'live', daily: 214000, props: ['campaign_id', 'creative_id'], owner: '數位行銷' },
  { name: 'coach_session_completed', cn: 'AI 對練完成', entity: 'agent', source: 'AI Coach', kind: 'business', status: 'live', daily: 412, props: ['scenario_id', 'score'], owner: '通路訓練' },
  { name: 'ga.ab_exposure', cn: 'AB 實驗曝光', entity: 'user', source: 'GA360', kind: 'behavior', status: 'quarantine', daily: 1200000, props: ['exp_id', 'variant'], owner: '—', note: '未登記事件：進隔離區，待產品團隊登記' },
  { name: 'option_apply', cn: '期權申請', entity: 'user', source: '業務系統', kind: 'business', status: 'draft', daily: 0, props: ['product', 'status'], owner: '衍生性商品' },
];

/* ------------------------------------------------------------------ time series (90 days) */
const DAYS = 90;
function series(base, { trend = 0, weekly = 0.18, noise = 0.06, dip = null, spike = null } = {}) {
  const out = [];
  for (let i = DAYS - 1; i >= 0; i--) {
    const d = dayOffset(-i);
    const dow = d.getDay();
    const wk = (dow === 0 || dow === 6) ? 1 - weekly * 2.2 : 1 + weekly * (dow === 1 ? .6 : .2);
    let v = base * (1 + trend * (DAYS - 1 - i) / DAYS) * wk * (1 + (rnd() - .5) * noise * 2);
    if (dip && i <= dip.from && i >= dip.to) v *= dip.f;
    if (spike && i === spike.at) v *= spike.f;
    out.push({ d: dkey(d), v: Math.round(v) });
  }
  return out;
}
export const TS = {
  dau:            series(412000, { trend: .06 }),
  login_users:    series(388000, { trend: .05 }),
  quote_viewers:  series(296000, { trend: .04 }),
  orders:         series(7900000, { weekly: .3, noise: .12 }),
  traders:        series(248000, { weekly: .3, noise: .1 }),
  turnover:       series(42800000000, { weekly: .3, noise: .16, spike: { at: 9, f: 1.42 } }),
  deposits:       series(1980000000, { weekly: .26, noise: .15 }),
  new_accounts:   series(2310, { trend: .12, weekly: .22, noise: .1, dip: { from: 3, to: 0, f: .72 } }),
  reject_rate:    series(149, { weekly: .05, noise: .12, dip: { from: 1, to: 0, f: 2.3 } }).map(p => ({ d: p.d, v: p.v / 10000 })), // 1.49%
  ga_events:      series(182000000, { weekly: .2, noise: .05 }),
  trade_events:   series(18200000, { weekly: .3, noise: .1 }),
};
TS.turnover_prev = TS.turnover.map(p => ({ d: p.d, v: Math.round(p.v * (0.86 + (rnd() - .5) * .14)) }));

export const PLATFORM_SPLIT = [
  { k: 'iOS', users: 186300, orders: 3420000, reject: 0.0163 },
  { k: 'Android', users: 151900, orders: 2780000, reject: 0.0141 },
  { k: 'Web', users: 73800, orders: 1700000, reject: 0.0118 },
];
export const CHANNEL_SPLIT = [
  { k: 'App', v: 5860000 }, { k: 'Web', v: 1120000 }, { k: '營業員', v: 690000 }, { k: 'API', v: 230000 },
];
export const UTM_SPLIT = [
  { k: 'LINE 推播', v: 2140, fund: .52 }, { k: 'Google 搜尋', v: 1630, fund: .38 }, { k: '直接', v: 1410, fund: .44 }, { k: 'Facebook', v: 920, fund: .35 }, { k: '營業員推薦', v: 880, fund: .61 }, { k: '其他', v: 610, fund: .31 },
];

/* ------------------------------------------------------------------ funnels */
export const FUNNELS = [
  { id: 'open', name: '開戶 → 首筆成交', window: '30 天', steps: [
    { ev: 'ga.page_view', cn: '開戶頁瀏覽', n: 184230, prev: 171900 },
    { ev: 'account_open_start', cn: '開戶開始', n: 61410, prev: 58200 },
    { ev: 'account_open_submit', cn: '開戶送件', n: 28930, prev: 29100 },
    { ev: 'account_open_approved', cn: '開戶核准', n: 22100, prev: 22300 },
    { ev: 'fund_deposit', cn: '首次入金', n: 9060, prev: 10480 },
    { ev: 'order_filled', cn: '首筆成交', n: 6120, prev: 7010 },
  ] },
  { id: 'trade', name: '看盤 → 委託 → 成交', window: '當日', steps: [
    { ev: 'quote_view', cn: '看盤', n: 296000, prev: 284000 },
    { ev: 'order_submit', cn: '委託', n: 171000, prev: 166000 },
    { ev: 'order_filled', cn: '成交', n: 138000, prev: 135000 },
  ] },
  { id: 'push', name: '推播 → 開啟 → 成交', window: '7 天', steps: [
    { ev: 'push_received', cn: '推播送達', n: 1820000, prev: 1760000 },
    { ev: 'push_opened', cn: '推播開啟', n: 412000, prev: 395000 },
    { ev: 'ga.screen_view', cn: '進入商品頁', n: 188000, prev: 171000 },
    { ev: 'order_filled', cn: '成交', n: 41200, prev: 38900 },
  ] },
];

/* ------------------------------------------------------------------ retention cohorts */
export const RETENTION = (() => {
  const rows = [];
  for (let w = 7; w >= 0; w--) {
    const start = dayOffset(-7 * w - 6); const n = ri(1800, 2600);
    const base = [1, .46, .39, .34, .31, .28, .26, .25];
    const cells = base.map((b, i) => (i > w ? null : Math.round((b * (1 + (rnd() - .5) * .12) + (w === 0 && i === 1 ? -.05 : 0)) * 1000) / 10));
    rows.push({ cohort: `${dkey(start)} 週`, n, cells });
  }
  return rows;
})();

/* ------------------------------------------------------------------ tags & segments */
export const TAGS = [
  { id: 'lifecycle', cn: '生命週期', type: '規則', vals: ['新客', '成長', '成熟', '沉睡', '流失'], cover: 1412000, upd: '每日 06:00', owner: '數位營運', rt: false },
  { id: 'pref_market', cn: '偏好市場', type: '統計', vals: ['TSE', 'OTC', 'US', 'HK', 'FUND'], cover: 1020000, upd: '每日 06:00', owner: '數據分析', rt: false },
  { id: 'fee_tier', cn: '手續費貢獻分級', type: 'SQL', vals: ['P1 (前 5%)', 'P2', 'P3', 'P4', 'P5'], cover: 880000, upd: '每週一', owner: '數據分析', rt: false },
  { id: 'churn_score', cn: '流失風險分', type: '模型', vals: ['0–100'], cover: 1412000, upd: '每日 07:30', owner: '數據科學', rt: false },
  { id: 'rt_hot_quotes', cn: '近 10 分鐘看盤 ≥ 3 檔', type: '即時', vals: ['是', '否'], cover: 38200, upd: '即時（Flink）', owner: '數位營運', rt: true },
  { id: 'digital_self', cn: '數位自助率', type: '統計', vals: ['0–100%'], cover: 1380000, upd: '每日', owner: '通路管理', rt: false },
  { id: 'coach_agent_score', cn: '營業員對練平均分', type: '匯入', vals: ['0–100'], cover: 1240, upd: '每週', owner: '通路訓練', rt: false },
];
export const SEGMENTS = [
  { id: 'sg1', name: '高價值沉睡客（agent 提議）', n: 11420, rule: '手續費貢獻分級 ∈ {P1,P2} AND 近 30 天 login = 0', by: 'churn-radar', status: 'pending_approval', upd: '今天 07:04', rt: false },
  { id: 'sg2', name: '新戶未入金（7 天）', n: 8930, rule: 'account_open_approved 在 7 天內 AND fund_deposit = 0', by: '陳柏宇', status: 'active', upd: '今天 06:10', rt: false },
  { id: 'sg3', name: '看盤未下單（即時）', n: 4210, rule: 'quote_view ≥ 3 in 10m AND order_submit = 0 in 30m', by: '林雅婷', status: 'active', upd: '即時', rt: true },
  { id: 'sg4', name: 'iOS 23.4 開戶中斷者', n: 1860, rule: 'account_open_start AND NOT account_open_submit in 24h AND app_version = 23.4 AND platform = iOS', by: 'funnel-doctor', status: 'pending_approval', upd: '今天 06:41', rt: false },
  { id: 'sg5', name: '定期定額活躍客', n: 92300, rule: 'fund_subscribe(plan_type = 定期定額) ≥ 1 in 60d', by: '林雅婷', status: 'active', upd: '昨天', rt: false },
  { id: 'sg6', name: 'Q4 基金活動曝光者', n: 356000, rule: 'campaign_exposed(campaign_id = q4_fund)', by: '行銷自動化', status: 'active', upd: '今天 05:00', rt: false },
];

/* ------------------------------------------------------------------ triggers */
export const TRIGGERS = [
  { id: 'tr1', name: '看盤未下單 30 分鐘', dsl: 'quote_view ≥ 3 in 10m → NOT order_submit in 30m', audience: 'sg3', throttle: '1/人/24h', out: 'dmp.triggers → MA 推播', fires24h: 3812, status: 'on' },
  { id: 'tr2', name: '新戶核准未入金 3 天', dsl: 'account_open_approved → NOT fund_deposit in 72h', audience: 'sg2', throttle: '1/人', out: 'dmp.triggers → 營業員 App 待辦', fires24h: 612, status: 'on' },
  { id: 'tr3', name: '委託失敗（系統原因）', dsl: 'order_rejected(reason_code LIKE "SYS%")', audience: '全體', throttle: '1/人/1h', out: 'dmp.triggers → 客服工單 + anomaly-watch', fires24h: 1940, status: 'on' },
  { id: 'tr4', name: '高價值客登入（AI Coach 提示）', dsl: 'login AND fee_tier ∈ {P1}', audience: '全體', throttle: '1/人/日', out: 'dmp.triggers → AI Coach 營業員提示', fires24h: 2206, status: 'on' },
  { id: 'tr5', name: 'iOS 開戶中斷即時挽回（agent 提議）', dsl: 'account_open_start(platform=iOS, app_version=23.4) → NOT account_open_submit in 2h', audience: 'sg4', throttle: '1/人', out: 'dmp.triggers → MA', fires24h: 0, status: 'pending_approval' },
];
export const TRIGGER_FEED = (() => {
  const names = ['看盤未下單 30 分鐘', '委託失敗（系統原因）', '高價值客登入（AI Coach 提示）', '新戶核准未入金 3 天'];
  const out = []; let t = new Date(2026, 9, 14, 14, 32, 10);
  for (let i = 0; i < 14; i++) { t = new Date(t.getTime() - ri(4, 40) * 1000); out.push({ t: `${pad2(t.getHours())}:${pad2(t.getMinutes())}:${pad2(t.getSeconds())}`, rule: pick(names), one: `oid_${ri(100000, 999999).toString(36)}`, payload: pick(['symbol=2330', 'symbol=2454', 'order_no=A…8812 reason=SYS02', 'fee_tier=P1', 'account_type=證券']) }); }
  return out;
})();

/* ------------------------------------------------------------------ profiles (用戶細查) */
export const PROFILES = [
  { one_id: 'oid_7k3a9q', cust: 'cust_•••7281', branch: 'TPE01', agent: '王信宏', lifecycle: '成長', fee_tier: 'P2', churn: 23, pref: 'TSE', age: '35-44', since: '2026-08-21', accounts: ['證券 ••••4567', '複委託 ••••9901'],
    identities: [['$identity_login_id', 'cust_•••7281', 'strong'], ['$identity_mobile_hash', 'hmac:9f3a…', 'strong'], ['$identity_account_no', '••••4567', 'multi'], ['$identity_account_no', '••••9901', 'multi'], ['$identity_ga_user_id', 'u_9a7b…', 'strong'], ['$identity_device_id', 'idfv:3C1F…', 'weak'], ['$identity_cookie_id', 'GA1.2.1844…', 'weak'], ['$identity_line_uid', 'U4f2…', 'channel']],
    timeline: [
      ['10-14 13:58', 'order_filled', 'trade', '成交 2330 買 1,000 股 @1,050', 'fee 1,496 · channel app'],
      ['10-14 13:57', 'order_submit', 'trade', '委託 2330 買 1,000 股 限價 1,050', 'channel app · 現股'],
      ['10-14 13:52', 'quote_view', 'ga', '看盤 2330 台積電', 'duration 94s · from_screen 自選'],
      ['10-14 13:51', 'ga.screen_view', 'ga', '畫面 自選股', 'app_version 23.4 · iOS'],
      ['10-14 13:50', 'login', 'sys', '登入 App', 'auth FaceID'],
      ['10-14 09:12', 'push_opened', 'mkt', '開啟推播「台積電法說會重點」', 'campaign tsmc_q3'],
      ['10-13 20:40', 'ga.page_view', 'ga', '瀏覽 官網 ETF 專區', 'utm_source line · page_group etf'],
      ['10-11 10:05', 'fund_subscribe', 'trade', '基金申購 定期定額 3,000', 'fund_code F0088'],
      ['10-08 14:22', 'order_rejected', 'trade', '委託失敗 2454', 'reason_code USR03 餘額不足'],
      ['08-21 11:30', 'account_open_approved', 'sys', '開戶核准 證券', 'branch TPE01 · agent 王信宏'],
      ['08-20 22:14', 'account_open_submit', 'sys', '開戶送件', 'channel app · kyc L2'],
      ['08-20 21:58', 'ga.page_view', 'ga', '瀏覽 開戶頁', 'utm_source google · utm_campaign brand'],
    ] },
  { one_id: 'oid_2m8x1c', cust: 'cust_•••0412', branch: 'TPE01', agent: '王信宏', lifecycle: '沉睡', fee_tier: 'P1', churn: 81, pref: 'US', age: '45-54', since: '2019-03-02', accounts: ['證券 ••••1203', '信用 ••••1203', '複委託 ••••7730'],
    identities: [['$identity_login_id', 'cust_•••0412', 'strong'], ['$identity_mobile_hash', 'hmac:21cc…', 'strong'], ['$identity_account_no', '••••1203', 'multi'], ['$identity_account_no', '••••7730', 'multi'], ['$identity_device_id', 'aid:88AD…', 'weak'], ['$identity_cookie_id', 'GA1.2.0921…', 'weak']],
    timeline: [
      ['09-30 10:12', 'fund_subscribe', 'trade', '基金申購 定期定額 10,000', 'fund_code F0021（自動扣款）'],
      ['09-02 15:40', 'order_filled', 'trade', '成交 AAPL 賣 50 股', 'channel agent · 複委託'],
      ['09-02 15:21', 'order_submit', 'trade', '委託 AAPL 賣 50 股', 'channel agent'],
      ['08-30 10:12', 'fund_subscribe', 'trade', '基金申購 定期定額 10,000', 'fund_code F0021（自動扣款）'],
      ['08-12 09:05', 'ga.screen_view', 'ga', '畫面 庫存', 'app_version 23.1 · Android'],
      ['08-12 09:03', 'login', 'sys', '登入 App', 'auth password'],
    ] },
];

/* ------------------------------------------------------------------ AI insights (agent 產出) */
export const INSIGHTS = [
  { id: 'ins_01', kind: 'anomaly', sev: 'critical', agent: 'anomaly-watch', at: '今天 14:15', conf: .91, status: 'open',
    title: '委託失敗率 14:00 起升至 3.4%（基準 1.5%），主因 reason_code SYS02（iOS）',
    summary: '近 1 小時 order_reject_rate 偏離 +1.9 pt，iOS 貢獻 78% 的增量；SYS02 為「委託閘道逾時」。Web 與 Android 正常。近 7 天無相同洞察。',
    evidence: ['anomaly.scan(order_reject_rate, 1h, 14d)', 'metric.query(order_reject_rate by platform, reason_code)'],
    actions: [{ t: '已通知交易系統值班（tr3）', st: 'done' }],
    chart: 'reject_rate', warn: '樣本：近 1 小時 iOS 委託 212k 筆' },
  { id: 'ins_02', kind: 'funnel', sev: 'high', agent: 'funnel-doctor', at: '今天 06:41', conf: .78, status: 'open',
    title: '開戶→送件 轉化在 iOS 23.4 下降 6.1 pt，疑為 KYC 相機權限流程中斷',
    summary: '近 7 天 account_open_start → account_open_submit 轉化 47.1% → 41.0%，僅發生在 iOS 23.4（其他版本持平）。流失者 62% 在 KYC 拍照頁離開（path.run），該步驟中位耗時從 48 秒拉長到 3 分 10 秒（interval.run）。',
    evidence: ['funnel.run(open, 7d vs prev 7d, by platform×app_version)', 'path.run(after account_open_start, 5 steps)', 'interval.run(start→submit)'],
    actions: [{ t: '提議分群「iOS 23.4 開戶中斷者」(sg4)', st: 'pending' }, { t: '提議即時挽回規則 (tr5)', st: 'pending' }],
    chart: 'funnel_open', warn: '假設尚未由 App 團隊驗證' },
  { id: 'ins_03', kind: 'risk', sev: 'high', agent: 'churn-radar', at: '今天 07:04', conf: .72, status: 'acknowledged',
    title: '11,420 位手續費前 20% 客戶近 30 天未登入，但 64% 仍有定期定額扣款',
    summary: '這群客戶 90 天貢獻手續費 1.84 億；行為衰減主要在 App 看盤（−71%），而非資金離開。建議以「市場資訊」而非「促銷」切入，並由所屬營業員聯繫。',
    evidence: ['distribution.run(fee_income, 90d)', 'retention.run(order_filled, [7,14,30])', 'segment.preview(sg1)'],
    actions: [{ t: '提議分群「高價值沉睡客」(sg1)', st: 'pending' }],
    chart: null, warn: null },
  { id: 'ins_04', kind: 'opportunity', sev: 'medium', agent: 'funnel-doctor', at: '昨天 06:40', conf: .66, status: 'open',
    title: 'LINE 推播帶來的開戶核准後 30 天入金率（52%）高於 Google 搜尋（38%）',
    summary: '首次來源為 LINE 的新戶入金率與首筆成交率皆為各渠道最高，且 CAC 最低；Google 搜尋流量大但入金率低。建議把 Q4 預算往 LINE 與營業員推薦傾斜，並對 Google 來源新戶加強入金引導。',
    evidence: ['funnel.run(open, 30d, by first_utm_source)', 'metric.query(deposits by first_utm_source)'],
    actions: [], chart: 'utm', warn: 'GA streaming 缺新使用者流量來源，本洞察以日批資料計算' },
  { id: 'ins_05', kind: 'campaign', sev: 'low', agent: 'campaign-review', at: '10-12 09:00', conf: .84, status: 'resolved',
    title: 'Q3 台積電法說推播：開啟率 22.6%，7 日內成交增量 +3.1 pt（對照組）',
    summary: '曝光 1.82M、開啟 412k、7 日成交 41.2k；位置歸因下推播貢獻 38%。對照組（未推播）同期成交率 2.3% vs 曝光組 5.4%。',
    evidence: ['funnel.run(push, 7d)', 'attribution.run(position)', 'metric.query(traders, segment vs control)'],
    actions: [{ t: '週報已送行銷（report.compose）', st: 'done' }], chart: null, warn: null },
  { id: 'ins_06', kind: 'anomaly', sev: 'medium', agent: 'anomaly-watch', at: '10-13 21:30', conf: .6, status: 'dismissed',
    title: '21:00 ga.page_view 量下降 24%：官網活動頁下線所致，非埋點異常',
    summary: '下降集中在 page_group = campaign；GTM 伺服器容器與日批對帳一致。判定為正常業務變動。',
    evidence: ['anomaly.scan(ga_events, 1h)', 'metric.query(ga.page_view by page_group)'],
    actions: [], chart: null, warn: null },
];

export const AGENTS = [
  { id: 'anomaly-watch', cn: '異常偵測', trigger: '每 15 分鐘', tools: 7, runs24h: 96, insights7d: 14, budget: .31, status: 'on', identity: 'svc-insight-readonly' },
  { id: 'funnel-doctor', cn: '漏斗診斷', trigger: '每日 06:30', tools: 11, runs24h: 1, insights7d: 5, budget: .58, status: 'on', identity: 'svc-insight-readonly' },
  { id: 'churn-radar', cn: '流失預警', trigger: '每日 07:00', tools: 9, runs24h: 1, insights7d: 3, budget: .44, status: 'on', identity: 'svc-insight-readonly' },
  { id: 'campaign-review', cn: '活動復盤', trigger: '活動結束事件', tools: 6, runs24h: 0, insights7d: 2, budget: .12, status: 'on', identity: 'svc-insight-readonly' },
  { id: 'ask', cn: '洞察問答', trigger: '使用者提問', tools: 18, runs24h: 37, insights7d: 9, budget: .22, status: 'on', identity: '代表提問者' },
  { id: 'reviewer', cn: '證據覆核', trigger: '洞察建立後', tools: 4, runs24h: 21, insights7d: 0, budget: .09, status: 'on', identity: 'svc-insight-readonly' },
];

export const TOOLS = [
  ['semantic.search', '以自然語言找事件／指標／維度', 'read'], ['semantic.describe', '指標口徑與 caveats', 'read'],
  ['metric.query', '語意查詢 → 表格 + 圖表規格 + SQL', 'read'], ['funnel.run', '漏斗', 'read'], ['retention.run', '留存', 'read'], ['path.run', '路徑', 'read'],
  ['interval.run', '間隔', 'read'], ['distribution.run', '分布', 'read'], ['attribution.run', '歸因', 'read'], ['ltv.run', 'LTV', 'read'], ['anomaly.scan', '異常偵測', 'read'],
  ['segment.preview', '分群預估', 'read'], ['segment.propose', '提議分群（需審批）', 'propose'], ['trigger.propose', '提議即時規則（需審批）', 'propose'],
  ['profile.get', '單人畫像（脫敏，需理由）', 'sensitive'], ['profile.timeline', '行為序列（脫敏，需理由）', 'sensitive'],
  ['insight.search', '檢索歷史洞察', 'read'], ['insight.create', '建立洞察', 'write'], ['knowledge.search', '字典／詞彙／SOP', 'read'], ['report.compose', '組成日報／週報', 'read'],
];

/* 問答示範腳本：問題 → 計畫 → 工具呼叫 → 回答 */
export const ASK_SCRIPTS = [
  { q: '上週開戶到入金的轉化率？跟前一週比', match: ['開戶', '入金'],
    plan: '對應指標 open_to_fund_rate（語意層）；跑 funnel.run 近 7 天與前 7 天；交叉驗證：以 platform 分組確認是否全面下降。',
    calls: [['semantic.search', '"開戶 入金 轉化"', '0 B'], ['funnel.run', 'steps=[account_open_approved, fund_deposit] window=30d time=7d', '2.1 GB'], ['funnel.run', '…compare=previous_period', '2.0 GB'], ['funnel.run', '…group_by=platform', '2.3 GB']],
    ans: '<b>上週開戶→入金轉化率 41.0%，較前一週（47.0%）下降 6.0 pt。</b><p>下降幾乎全部來自 iOS（47.5% → 38.9%）；Android 與 Web 持平（±0.6 pt）。這與 funnel-doctor 今早的洞察（iOS 23.4 KYC 流程）一致。</p>',
    warn: '口徑：open_to_fund_rate 以核准日起 30 天內首次入金計；最近 7 天的 cohort 尚未滿 30 天，為截至今日的暫時值。',
    acts: ['存為洞察', '開啟漏斗頁', '提議分群：iOS 未入金新戶'] },
  { q: '哪個平台的委託失敗率最高？', match: ['失敗率', '平台'],
    plan: '指標 order_reject_rate；metric.query 近 7 天 by platform；caveat：系統性失敗（SYS）與使用者因素分開。',
    calls: [['semantic.describe', 'metric=order_reject_rate', '0 B'], ['metric.query', 'order_reject_rate time=7d group_by=platform', '1.6 GB'], ['metric.query', '…group_by=platform,reason_class', '1.8 GB']],
    ans: '<b>iOS 最高：1.63%，Android 1.41%，Web 1.18%（近 7 天）。</b><p>拆開原因後，三個平台的「使用者因素」失敗率幾乎一樣（約 1.1%）；iOS 偏高全部來自系統原因 SYS02，且集中在今天 14:00 之後（見 anomaly-watch 洞察 ins_01）。</p>',
    warn: '指標 caveat：系統性失敗（reason_code 以 SYS 開頭）與使用者因素應分開看。',
    acts: ['存為洞察', '查看 ins_01'] },
  { q: '幫我找出高價值但 30 天沒登入的客戶', match: ['高價值', '沒登入'],
    plan: '以 fee_tier 標籤（P1,P2）與 login 事件組規則；segment.preview 估人數與畫像；不輸出個人名單，提議分群交由主管審批。',
    calls: [['semantic.search', '"高價值 登入"', '0 B'], ['segment.preview', 'fee_tier ∈ {P1,P2} AND login=0 in 30d', '3.4 GB'], ['segment.propose', 'name=高價值沉睡客 status=pending_approval', '0 B']],
    ans: '<b>符合條件 11,420 人（占 P1+P2 的 8.1%）。</b><p>畫像：45–54 歲占 41%，偏好市場 US 38%、TSE 35%；64% 近 60 天仍有定期定額扣款。已提議分群「高價值沉睡客」，待陳柏宇審批後可推送給營業員 App。</p>',
    warn: '此工具不回傳個人識別資料；分群生效前需審批。',
    acts: ['查看分群草稿', '提議觸發規則'] },
  { q: '王小明最近做了什麼？', match: ['王小明', '最近做了'],
    plan: '這是單一客戶的細查請求：需要客戶的 one_id 與查詢理由，且你必須對該客戶有權限。',
    calls: [['profile.timeline', 'one_id=? reason=?', '—']],
    ans: '<b>我無法直接以姓名查詢個人行為。</b><p>請在「用戶細查」頁輸入客戶代碼並填寫查詢理由（會寫入審計），或告訴我 one_id 與理由，我再以你的身份呼叫 profile.timeline（僅限你所屬客戶，回傳已脫敏）。</p>',
    warn: '個資守門：姓名、手機、身分證字號不在分析倉中；細查需理由並審計。',
    acts: ['前往用戶細查'] },
];
