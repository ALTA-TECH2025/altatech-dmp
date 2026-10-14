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
  { id: 'u-ana', rk: 'analyst', name: '林雅婷', email: 'yating.lin@altatech.demo', role: '數據分析師', col: '#2C73EA', init: '林', scope: 'all', can: { detail: true, sql: true, approve: false, admin: false } },
  { id: 'u-ops', rk: 'ops_manager', name: '陳柏宇', email: 'boyu.chen@altatech.demo', role: '數位營運主管', col: '#12B5A8', init: '陳', scope: 'all', can: { detail: true, sql: false, approve: true, admin: false } },
  { id: 'u-agt', rk: 'sales_agent', name: '王信宏', email: 'hsinhung.wang@altatech.demo', role: '營業員（台北分公司）', col: '#D8A800', init: '王', scope: 'branch:TPE01', can: { detail: true, sql: false, approve: false, admin: false } },
  { id: 'u-adm', rk: 'platform_admin', name: '張敏慧', email: 'minhui.chang@altatech.demo', role: '平台管理員', col: '#7C4DD6', init: '張', scope: 'all', can: { detail: true, sql: true, approve: true, admin: true } },
  { id: 'u-aud', rk: 'auditor', name: '許文瑄', email: 'wenhsuan.hsu@altatech.demo', role: '稽核／法遵', col: '#56646D', init: '許', scope: 'all', can: { detail: false, sql: false, approve: true, admin: false, audit: true } },
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
  { id: 'tr2', name: '新戶核准未入金 3 天', dsl: 'account_open_approved → NOT fund_deposit in 72h', audience: 'sg2', throttle: '1/人', out: 'dmp.triggers → MA 推播', fires24h: 612, status: 'on' },
  { id: 'tr3', name: '委託失敗（系統原因）', dsl: 'order_rejected(reason_code LIKE "SYS%")', audience: '全體', throttle: '1/人/1h', out: 'dmp.triggers → anomaly-watch + 交易系統值班', fires24h: 1940, status: 'on' },
  { id: 'tr4', name: '高價值客登入（MA 即時訊息）', dsl: 'login AND fee_tier ∈ {P1}', audience: '全體', throttle: '1/人/日', out: 'dmp.triggers → MA 即時訊息', fires24h: 2206, status: 'on' },
  { id: 'tr5', name: 'iOS 開戶中斷即時挽回（agent 提議）', dsl: 'account_open_start(platform=iOS, app_version=23.4) → NOT account_open_submit in 2h', audience: 'sg4', throttle: '1/人', out: 'dmp.triggers → MA', fires24h: 0, status: 'pending_approval' },
];
export const TRIGGER_FEED = (() => {
  const names = ['看盤未下單 30 分鐘', '委託失敗（系統原因）', '高價值客登入（MA 即時訊息）', '新戶核准未入金 3 天'];
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
  { id: 'analyst-copilot', cn: '分析 Copilot', trigger: '使用者提問', tools: 17, runs24h: 52, insights7d: 11, budget: .27, status: 'on', identity: '代表提問者' },
  { id: 'dashboard-reporter', cn: '看板解讀', trigger: '工作日 07:15', tools: 6, runs24h: 1, insights7d: 4, budget: .18, status: 'on', identity: 'svc-insight-readonly' },
  { id: 'strategy-loop', cn: '策略閉環', trigger: '洞察確認後', tools: 8, runs24h: 2, insights7d: 1, budget: .11, status: 'on', identity: '代表確認者' },
  { id: 'reviewer', cn: '證據覆核', trigger: '洞察建立後', tools: 4, runs24h: 21, insights7d: 0, budget: .09, status: 'on', identity: 'svc-insight-readonly' },
];

export const TOOLS = [
  ['semantic.search', '以自然語言找事件／指標／維度', 'read'], ['semantic.describe', '指標口徑與 caveats', 'read'],
  ['metric.query', '語意查詢 → 表格 + 圖表規格 + SQL', 'read'], ['funnel.run', '漏斗', 'read'], ['retention.run', '留存', 'read'], ['path.run', '路徑', 'read'],
  ['interval.run', '間隔', 'read'], ['distribution.run', '分布', 'read'], ['attribution.run', '歸因', 'read'], ['ltv.run', 'LTV', 'read'], ['anomaly.scan', '異常偵測', 'read'],
  ['segment.preview', '分群預估', 'read'], ['segment.propose', '提議分群（需審批）', 'propose'], ['trigger.propose', '提議即時規則（需審批）', 'propose'],
  ['profile.get', '單人畫像（脫敏，需理由）', 'sensitive'], ['profile.timeline', '行為序列（脫敏，需理由）', 'sensitive'],
  ['insight.search', '檢索歷史洞察', 'read'], ['insight.create', '建立洞察', 'write'], ['knowledge.search', '字典／詞彙／SOP', 'read'], ['report.compose', '組成日報／週報', 'read'],
  ['analysis.configure', 'NL → 分析模型配置卡（選擇題式，不執行）', 'read'], ['analysis.run', '以配置卡執行分析（與 UI 同結果）', 'read'], ['users.list', '用戶列表：人數預估 + 分頁（脫敏）', 'read'],
  ['dashboard.explain', '看板解讀：趨勢／異常／對比摘要', 'read'], ['knowledge.relations', '指標關聯圖（證券行業知識庫）', 'read'], ['feedback.record', '記錄使用者修正（學習）', 'write'], ['campaign.execute', '執行已審批策略', 'execute'],
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
    ans: '<b>符合條件 11,420 人（占 P1+P2 的 8.1%）。</b><p>畫像：45–54 歲占 41%，偏好市場 US 38%、TSE 35%；64% 近 60 天仍有定期定額扣款。已提議分群「高價值沉睡客」，待陳柏宇審批後可推送給 MA 與 CRM。</p>',
    warn: '此工具不回傳個人識別資料；分群生效前需審批。',
    acts: ['查看分群草稿', '提議觸發規則'] },
  { q: '王小明最近做了什麼？', match: ['王小明', '最近做了'],
    plan: '這是單一客戶的細查請求：需要客戶的 one_id 與查詢理由，且你必須對該客戶有權限。',
    calls: [['profile.timeline', 'one_id=? reason=?', '—']],
    ans: '<b>我無法直接以姓名查詢個人行為。</b><p>請在「用戶細查」頁輸入客戶代碼並填寫查詢理由（會寫入審計），或告訴我 one_id 與理由，我再以你的身份呼叫 profile.timeline（僅限你所屬客戶，回傳已脫敏）。</p>',
    warn: '個資守門：姓名、手機、身分證字號不在分析倉中；細查需理由並審計。',
    acts: ['前往用戶細查'] },
];

/* ================================================================== v0.2：P0 模組與資料整合 */
/* 行為路徑（起點 quote_view 之後 4 步，抽樣 1/10） */
export const PATHS = {
  anchor: 'quote_view', sample: '1/10', users: 29600,
  nodes: [
    [{ k: 'quote_view', cn: '看盤', n: 29600 }],
    [{ k: 'order_submit', cn: '委託', n: 12140 }, { k: 'quote_view', cn: '看盤（其他）', n: 8880 }, { k: 'watchlist_add', cn: '加入自選', n: 3550 }, { k: 'ga.screen_view:news', cn: '新聞', n: 2370 }, { k: 'exit', cn: '離開', n: 2660 }],
    [{ k: 'order_filled', cn: '成交', n: 9710 }, { k: 'order_rejected', cn: '委託失敗', n: 1210 }, { k: 'order_submit', cn: '委託', n: 4210 }, { k: 'quote_view', cn: '看盤', n: 5330 }, { k: 'exit', cn: '離開', n: 9140 }],
    [{ k: 'quote_view', cn: '看盤', n: 7110 }, { k: 'ga.screen_view:inventory', cn: '庫存', n: 4320 }, { k: 'order_submit', cn: '委託', n: 2920 }, { k: 'exit', cn: '離開', n: 15250 }],
  ],
  links: [
    [0, 0, 1, 0, 12140], [0, 0, 1, 1, 8880], [0, 0, 1, 2, 3550], [0, 0, 1, 3, 2370], [0, 0, 1, 4, 2660],
    [1, 0, 2, 0, 9710], [1, 0, 2, 1, 1210], [1, 0, 2, 4, 1220], [1, 1, 2, 2, 2900], [1, 1, 2, 3, 3400], [1, 1, 2, 4, 2580], [1, 2, 2, 2, 1310], [1, 2, 2, 3, 1930], [1, 2, 2, 4, 310], [1, 3, 2, 4, 2370],
    [2, 0, 3, 0, 4600], [2, 0, 3, 1, 3200], [2, 0, 3, 3, 1910], [2, 1, 3, 2, 820], [2, 1, 3, 3, 390], [2, 2, 3, 0, 1100], [2, 2, 3, 2, 2100], [2, 2, 3, 3, 1010], [2, 3, 3, 0, 1410], [2, 3, 3, 1, 1120], [2, 3, 3, 3, 2800],
  ],
  top: [
    { p: ['看盤', '委託', '成交', '看盤'], n: 4600, t: '3 分 12 秒' }, { p: ['看盤', '委託', '成交', '庫存'], n: 3200, t: '2 分 48 秒' }, { p: ['看盤', '看盤', '看盤', '離開'], n: 2800, t: '4 分 05 秒' },
    { p: ['看盤', '離開'], n: 2660, t: '41 秒' }, { p: ['看盤', '新聞', '離開'], n: 2370, t: '1 分 36 秒' }, { p: ['看盤', '委託', '委託失敗', '委託'], n: 820, t: '1 分 02 秒' },
  ],
};

/* 歸因：轉化 = order_filled（首筆），回溯 14 天 */
export const ATTRIBUTION = {
  conversion: 'order_filled（首筆成交）', window: '14 天', conversions: 6120, value: 9_140_000,
  touches: [
    { k: 'LINE 推播開啟', first: 1840, last: 1420, linear: 1612, position: 1688, decay: 1544, avgTouches: 2.3 },
    { k: 'Google 搜尋', first: 1420, last: 760, linear: 1050, position: 1180, decay: 905, avgTouches: 1.8 },
    { k: '營業員接觸', first: 690, last: 1710, linear: 1240, position: 1210, decay: 1522, avgTouches: 1.4 },
    { k: '官網 ETF 專區', first: 980, last: 640, linear: 802, position: 826, decay: 712, avgTouches: 2.1 },
    { k: 'Facebook 廣告', first: 760, last: 420, linear: 598, position: 640, decay: 520, avgTouches: 1.9 },
    { k: 'App 推薦卡片', first: 430, last: 1170, linear: 818, position: 576, decay: 917, avgTouches: 1.6 },
  ],
};

/* 用戶列表（合成 40 人） */
export const USERS = (() => {
  const lc = ['新客', '成長', '成熟', '沉睡', '流失'], tiers = ['P1', 'P2', 'P3', 'P4', 'P5'], mk = ['TSE', 'OTC', 'US', 'HK', 'FUND'], br = ['TPE01', 'TPE02', 'TCH01', 'KHH01'], ag = ['王信宏', '李佳穎', '張志豪', '周美玲'], ages = ['<25', '25-34', '35-44', '45-54', '55-64', '65+'];
  const out = [];
  for (let i = 0; i < 40; i++) {
    const b = br[i % 4];
    out.push({ one_id: 'oid_' + (0x2f3a9 + i * 7919).toString(36), cust: 'cust_•••' + String(1000 + ri(0, 8999)), branch: b, agent: b === 'TPE01' ? '王信宏' : ag[(i + 1) % 4], lifecycle: pick(lc), tier: pick(tiers), market: pick(mk), age: pick(ages), churn: ri(3, 97), last: `10-${pad2(ri(1, 14))}`, fee90: ri(0, 420) * 1000, orders30: ri(0, 60), platform: pick(['iOS', 'Android', 'Web']) });
  }
  out[0] = { ...out[0], one_id: 'oid_7k3a9q', cust: 'cust_•••7281', branch: 'TPE01', agent: '王信宏', lifecycle: '成長', tier: 'P2', market: 'TSE', age: '35-44', churn: 23, last: '10-14', fee90: 48000, orders30: 14, platform: 'iOS' };
  out[1] = { ...out[1], one_id: 'oid_2m8x1c', cust: 'cust_•••0412', branch: 'TPE01', agent: '王信宏', lifecycle: '沉睡', tier: 'P1', market: 'US', age: '45-54', churn: 81, last: '08-12', fee90: 312000, orders30: 0, platform: 'Android' };
  return out;
})();

/* 資料整合：連線、建模、清洗 */
export const CONNECTIONS = [
  { id: 'trade_core', type: 'MSSQL', cn: '交易主機（SQL Server 2019）', mode: 'CDC（Debezium）', tables: 6, models: 3, lag: '1.4 秒', status: 'ok', rows24h: 18_240_902 },
  { id: 'ga_bq', type: 'BigQuery', cn: 'GA360 匯出（analytics_3012…）', mode: 'Storage Read API（Daily + Fresh）', tables: 2, models: 2, lag: '55 分', status: 'ok', rows24h: 182_430_112 },
  { id: 'crm_pg', type: 'PostgreSQL', cn: 'CRM 客戶主檔', mode: 'CDC（pgoutput）+ 日批', tables: 4, models: 2, lag: '3.1 秒', status: 'ok', rows24h: 1_230_119 },
  { id: 'sensors', type: 'SensorsData', cn: '神策分析歷史事件（一次性匯入，已完成）', mode: '查詢 API → Parquet → Iceberg', tables: 1, models: 1, lag: '已完成 10/02', status: 'ok', rows24h: 0, note: '匯入 48.7 億筆，對帳差異 0.4%；之後行為事件全部來自 GA360' },
  { id: 'fund_pg', type: 'PostgreSQL', cn: '基金平台', mode: 'CDC', tables: 2, models: 1, lag: '4.0 秒', status: 'warn', rows24h: 41_000, note: '來源新增欄位 PLAN_FREQ，建議映射到 plan_type' },
];
export const MODEL_EXAMPLE = {
  id: 'mdl_order_hist', name: '交易主機委託表 → 委託/成交事件', source: 'trade_core · dbo.ORDER_HIST', entity: '事件（狀態機事件化）',
  src: [['ORD_NO', 'varchar(20)', '0.0%', 'A2026101400012345'], ['CUST_ID', 'varchar(12)', '0.0%', '00007281'], ['ACCT_NO', 'varchar(10)', '0.0%', '9801234567'], ['STK_CODE', 'char(6)', '0.0%', '2330  '], ['MKT', 'char(1)', '0.0%', 'T'], ['BS_FLAG', 'char(1)', '0.0%', 'B'], ['PRICE', 'decimal(12,2)', '0.0%', '1050.00'], ['QTY', 'int', '0.0%', '1000'], ['STATUS', 'char(1)', '0.0%', 'F'], ['ORD_TIME', 'datetime2', '0.0%', '2026-10-14 13:57:02'], ['CHANNEL', 'char(1)', '0.4%', 'A'], ['CUST_NAME', 'nvarchar(50)', '0.0%', '（個資）'], ['PHONE', 'varchar(15)', '2.1%', '（個資）']],
  map: [['ORD_NO', 'order_no', '—'], ['CUST_ID', '$identity_login_id', 'hmac_sha256(k_idm)'], ['ACCT_NO', '$identity_account_no', 'fpe(k_acct)'], ['STK_CODE', 'symbol', 'trim'], ['MKT', 'market', 'map{T:TSE,O:OTC,U:US,H:HK}'], ['BS_FLAG', 'side', 'map{B:buy,S:sell}'], ['PRICE', 'price', 'to_decimal(2)'], ['QTY', 'qty', 'to_int'], ['PRICE×QTY', 'amount', 'expr'], ['STATUS', 'event', '狀態機：N→order_submit, F→order_filled, C→order_cancelled, R→order_rejected'], ['ORD_TIME', 'time', 'to_ms_utc(Asia/Taipei)'], ['CHANNEL', 'channel', 'map{A:app,W:web,S:agent}'], ['CUST_NAME', '（丟棄）', '個資不進平台'], ['PHONE', '（丟棄）', '個資不進平台']],
  rules: [['必填', 'order_no, symbol, side, qty', '隔離'], ['枚舉', 'market ∈ {TSE,OTC,US,HK,FUND}', '隔離'], ['範圍', 'amount ≥ 0', '隔離'], ['時間', 'time 在 ±7 天', '隔離'], ['個資掃描', '身分證／手機／Email 正規式', '拒絕＋告警'], ['去重', 'event_id = sha256(source|ORD_NO|STATUS|lsn)', '最終去重']],
  preview: { sample: 1000, ok: 994, quarantined: 6, reasons: ['market 值 "X" ×4', 'time 超出 7 天 ×2'], pii: 0, newProps: 0, estDaily: '790 萬' },
};

/* 智能分析：選擇題式配置卡示範 */
export const CONFIG_CARDS = {
  '上週開戶到入金的轉化率？跟前一週比': { model: '漏斗分析', conf: .91, params: [['步驟', 'account_open_approved → fund_deposit'], ['視窗', '30 天'], ['時間', '上週（10/06–10/12）'], ['對比', '前一週'], ['分組', '—']], alt: ['事件分析：open_to_fund_rate 日趨勢'] },
  '哪個平台的委託失敗率最高？': { model: '事件分析', conf: .88, params: [['指標', 'order_reject_rate（委託失敗率）'], ['時間', '近 7 天'], ['分組', 'platform'], ['篩選', '—']], alt: ['事件分析：再按 reason_class 分組'] },
  '幫我找出高價值但 30 天沒登入的客戶': { model: '用戶列表', conf: .84, params: [['條件 1', 'fee_tier ∈ {P1, P2}'], ['條件 2', 'login 近 30 天 = 0 次'], ['輸出', '人數預估 + 畫像（不含個人名單）']], alt: ['分群：另存為「高價值沉睡客」'] },
};

/* ================================================================== v0.3：企業級權限、隱碼、審計統計 */
export const ROLES = [
  { key: 'platform_admin', cn: '平台管理員', scope: '全域', users: 2, desc: '平台設定、角色與權限、資料源、審計全覽' },
  { key: 'data_engineer', cn: '資料工程師', scope: '全域（不含細查）', users: 3, desc: '數據整合、事件字典、血緣、資料品質' },
  { key: 'analyst', cn: '數據分析師', scope: '全域（欄位隱碼）', users: 6, desc: '全部分析模組、SQL 沙箱、標籤定義' },
  { key: 'ops_manager', cn: '數位營運主管', scope: '全域', users: 2, desc: '分析、分群、觸發、審批 agent 提議與推送／匯出' },
  { key: 'marketer', cn: '行銷專員', scope: '全域（活動曝光者）', users: 5, desc: '聚合分析、分群建立（需審批）、活動復盤' },
  { key: 'branch_manager', cn: '分公司主管', scope: '所屬分公司', users: 24, desc: '分析、用戶列表、細查（需理由）' },
  { key: 'sales_agent', cn: '營業員', scope: '所屬客戶 · 回溯 90 天', users: 1240, desc: '用戶列表、細查（需理由）、AI 摘要' },
  { key: 'auditor', cn: '稽核／法遵', scope: '全域唯讀', users: 3, desc: '審計日誌、揭露申請審核、DSAR' },
  { key: 'svc_insight', cn: 'AI 服務身份', scope: '僅聚合', users: 4, desc: '排程 agent 專用，不可細查' },
];
export const MODULES = [['overview', '總覽'], ['events', '事件／漏斗／路徑／歸因'], ['users', '用戶列表'], ['profile', '用戶細查'], ['segments', '標籤與分群'], ['triggers', '即時觸發'], ['integration', '數據整合'], ['ai_ask', 'AI 洞察'], ['ai_admin', 'Agent 設定'], ['governance', '資料治理'], ['audit', '日誌統計'], ['iam', '權限中心']];
// 動作：v 可見 u 可用 e 匯出 a 審批 d 設定
export const MATRIX = {
  platform_admin: { overview: 'vuead', events: 'vuead', users: 'vuead', profile: 'u', segments: 'vuead', triggers: 'vuead', integration: 'vuead', ai_ask: 'vuea', ai_admin: 'vd', governance: 'vued', audit: 'v', iam: 'vd' },
  data_engineer: { overview: 'vu', events: 'vu', users: '', profile: '', segments: '', triggers: 'v', integration: 'vuead', ai_ask: 'u', ai_admin: '', governance: 'vud', audit: 'v', iam: '' },
  analyst: { overview: 'vu', events: 'vue', users: 'vu', profile: 'u', segments: 'vu', triggers: 'v', integration: 'v', ai_ask: 'u', ai_admin: '', governance: 'v', audit: 'v', iam: '' },
  ops_manager: { overview: 'vu', events: 'vue', users: 'vue', profile: 'u', segments: 'vua', triggers: 'vua', integration: 'v', ai_ask: 'ua', ai_admin: '', governance: 'v', audit: 'v', iam: '' },
  marketer: { overview: 'v', events: 'vu', users: '', profile: '', segments: 'u', triggers: 'v', integration: '', ai_ask: 'u', ai_admin: '', governance: '', audit: 'v', iam: '' },
  branch_manager: { overview: 'v', events: 'vu', users: 'vu', profile: 'u', segments: 'v', triggers: '', integration: '', ai_ask: 'u', ai_admin: '', governance: '', audit: 'v', iam: '' },
  sales_agent: { overview: 'v', events: '', users: 'vu', profile: 'u', segments: '', triggers: '', integration: '', ai_ask: 'u', ai_admin: '', governance: '', audit: 'v', iam: '' },
  auditor: { overview: 'v', events: 'v', users: 'v', profile: 'v', segments: 'v', triggers: 'v', integration: 'v', ai_ask: 'v', ai_admin: 'v', governance: 'v', audit: 'vuea', iam: 'v' },
  svc_insight: { overview: '', events: 'u', users: '', profile: '', segments: 'v', triggers: '', integration: '', ai_ask: 'u', ai_admin: '', governance: '', audit: '', iam: '' },
};
export const DATA_SCOPES = [
  { k: '組織範圍', rule: 'branch_id IN (使用者範圍) / agent_id IN (所屬客戶)', how: '查詢編譯器加行級條件；分群先交集範圍 Bitmap', who: '分公司主管、營業員' },
  { k: '專案／資料來源', rule: 'project IN (…) AND source IN (…)', how: '角色可見清單', who: '全部' },
  { k: '分群限制', rule: 'one_id_uint IN (campaign_exposed)', how: '只允許在特定分群內分析', who: '行銷專員' },
  { k: '時間範圍', rule: 'event_date ≥ today() − 90', how: '限制回溯天數', who: '營業員' },
  { k: 'K 匿名門檻', rule: '分組人數 < 20 → 顯示「< 20」', how: '結果後處理，防反推個人', who: '全部' },
  { k: '聚合專用身份', rule: '禁止 profile / users', how: '排程 agent 不能觸及個人', who: 'AI 服務身份' },
];
export const MASK_RULES = [
  { field: '姓名／身分證／手機／Email／完整帳號', level: 'L4', plain: '無（不進分析倉）', others: '—', how: '僅存於來源系統與 OneID 加密區' },
  { field: 'one_id / 客戶代碼 / 帳號（代碼化）', level: 'L3', plain: '管理員、稽核', others: 'mask_tail(4)：cust_•••7281', how: '可 JOIN 不可反推；揭露需主管＋稽核' },
  { field: '單筆金額 / 持股 / 手續費', level: 'L2', plain: '分析師、營運主管、所屬營業員、分公司主管', others: 'bucket：10–50 萬；匯出一律區間化', how: '揭露需主管' },
  { field: '流失分', level: 'L2', plain: '分析師、營運主管、營業員', others: 'bucket：高／中／低', how: '' },
  { field: '頁面 / 事件 / 裝置 / 渠道', level: 'L1', plain: '所有分析角色', others: '—', how: 'URL 去除查詢字串' },
  { field: '看板聚合數字', level: 'L0', plain: '所有人', others: '—', how: '' },
];
export const APPROVALS = [
  { id: 'ap_1042', type: '分群推送', who: '林雅婷', what: '新戶未入金（7 天）→ MA + CRM', n: '8,930 人', at: '今天 13:40', status: 'pending', approver: '陳柏宇' },
  { id: 'ap_1041', type: 'agent 提議', who: 'churn-radar', what: '分群「高價值沉睡客」', n: '11,420 人 · 信心 72%', at: '今天 07:04', status: 'pending', approver: '陳柏宇' },
  { id: 'ap_1040', type: '欄位揭露', who: '王信宏', what: 'oid_7k3a9q · 帳號尾碼（L3）', n: '理由：客戶來電對帳', at: '今天 11:20', status: 'pending', approver: '陳柏宇 + 稽核' },
  { id: 'ap_1039', type: '匯出', who: '林雅婷', what: '用戶列表（定期定額活躍客）', n: '92,300 列 · 區間化', at: '昨天 17:05', status: 'approved', approver: '陳柏宇' },
  { id: 'ap_1038', type: '臨時提權', who: '張敏慧', what: '稽核唯讀 → 4 小時', n: '事故調查', at: '10-12 09:10', status: 'expired', approver: '稽核' },
  { id: 'ap_1037', type: 'agent 提議', who: 'funnel-doctor', what: '即時規則「iOS 開戶中斷挽回」', n: '1,860 人', at: '10-12 06:41', status: 'rejected', approver: '陳柏宇' },
];
const _aud = (base, w) => { const o = []; for (let i = 29; i >= 0; i--) { const d = dayOffset(-i); const dow = d.getDay(); o.push({ d: dkey(d), v: Math.round(base * ((dow === 0 || dow === 6) ? .25 : 1) * (1 + (rnd() - .5) * w)) }); } return o; };
export const AUDIT = {
  tiles: [['平台活躍使用者（日）', '312', '+4.1%'], ['查詢次數（日）', '8,420', '+6.3%'], ['細查次數（日）', '236', '+18.5%'], ['揭露申請（本週）', '14', '核准 9'], ['匯出筆數（本週）', '183,400', '3 件'], ['權限拒絕（日）', '27', '−12%']],
  profileByRole: [{ name: '營業員', data: _aud(160, .3) }, { name: '分公司主管', data: _aud(48, .4) }, { name: '分析師', data: _aud(22, .5) }],
  modules: [{ k: '事件分析', v: 3120 }, { k: '用戶列表', v: 2210 }, { k: 'AI 洞察', v: 1480 }, { k: '漏斗分析', v: 920 }, { k: '標籤與分群', v: 610 }, { k: '行為路徑', v: 340 }, { k: '歸因分析', v: 210 }],
  topProfile: [['王信宏', '營業員 · TPE01', 41, '客戶來電對帳（×38）'], ['李佳穎', '營業員 · TPE02', 29, '定期回訪'], ['周美玲', '營業員 · KHH01', 23, '客訴調查'], ['林雅婷', '分析師', 9, '漏斗流失抽樣'], ['張志豪', '分公司主管 · TCH01', 7, '業績檢視']],
  anomalies: [['今天 02:14', '非上班時間細查', '李佳穎 · 3 次', 'warn'], ['今天 11:52', '單人高頻細查', '王信宏 · 1 小時 12 次（門檻 10）', 'warn'], ['昨天 16:30', '匯出超過門檻', '林雅婷 · 92,300 列（已審批）', 'ok'], ['昨天 09:05', '權限拒絕突增', '行銷專員嘗試用戶列表 ×14', 'bad']],
  agent: [{ k: '工具呼叫（日）', v: 1860 }, { k: '敏感工具（日）', v: 12 }, { k: '提議數（週）', v: 7 }, { k: '採納率', v: 43 }],
  denied: _aud(27, .6),
  exports: _aud(6, .8),
};

/* ================================================================== v0.3.2：受眾推送（Webhook / GA4 受眾 / Google Ads） */
export const DESTINATIONS = [
  { id: 'd_crm', name: 'CRM 客戶系統', type: 'webhook', cn: 'Webhook（內部）', target: 'https://crm.internal/dmp/webhook', ident: 'customer_code', tags: ['lifecycle', 'fee_tier', 'churn_score_bucket'], status: 'active', ok7d: .998, p95: '420 ms', last: '今天 03:02', note: 'HMAC 簽章、IP 白名單' },
  { id: 'd_ma', name: '行銷自動化 MA', type: 'webhook', cn: 'Webhook（內部）', target: 'https://ma.internal/hooks/dmp', ident: 'customer_code', tags: ['lifecycle', 'pref_market'], status: 'active', ok7d: 1, p95: '180 ms', last: '今天 03:05', note: 'mTLS' },
  { id: 'd_kafka', name: '數據平台 Kafka', type: 'kafka', cn: 'Kafka', target: 'dmp.audience_updates / dmp.tag_updates', ident: 'customer_code + one_id', tags: ['*'], status: 'active', ok7d: 1, p95: '—', last: '即時', note: 'Avro' },
  { id: 'd_gcs', name: '數倉 Parquet', type: 'gcs', cn: 'GCS Parquet', target: 'gs://dmp-sync/audiences/', ident: 'customer_code', tags: ['*'], status: 'active', ok7d: 1, p95: '—', last: '今天 03:10', note: 'manifest + checksum' },
  { id: 'd_ga4', name: 'GA4 受眾 → Google Ads 再行銷', type: 'ga4_audience', cn: 'GA4 受眾', target: 'G-XXXXXXX（Measurement Protocol）', ident: 'client_id / app_instance_id', tags: [], status: 'active', ok7d: .991, p95: '—', last: '今天 03:40（24 小時攤平）', note: '不含個資；需廣告再行銷同意；GA4 ↔ Ads 已連結' },
];
export const AUDIENCE_PUSHES = [
  { seg: 'sg2', dest: ['d_ma', 'd_crm'], mode: 'delta', schedule: '日批後 03:00', approved: true },
  { seg: 'sg5', dest: ['d_ga4', 'd_gcs'], mode: 'delta', schedule: '日批後 03:00 · GA4 24 小時攤平', approved: true },
  { seg: 'sg6', dest: ['d_ga4'], mode: 'delta', schedule: '即時', approved: true },
  { seg: 'sg3', dest: ['d_kafka'], mode: 'realtime', schedule: '即時', approved: true },
];
export const DELIVERIES = [
  { id: 'dlv_01J9…a3', seg: '新戶未入金（7 天）', dest: '行銷自動化 MA', ver: 118, mode: 'delta', n: 612, batches: '1/1', status: 'ok', at: '今天 03:05', resp: '200 · 180 ms' },
  { id: 'dlv_01J9…a2', seg: '新戶未入金（7 天）', dest: 'CRM 客戶系統', ver: 118, mode: 'delta', n: 612, batches: '1/1', status: 'ok', at: '今天 03:02', resp: '200 · 410 ms' },
  { id: 'dlv_01J9…9f', seg: '定期定額活躍客', dest: 'GA4 受眾 → Google Ads', ver: 44, mode: 'delta', n: 3_180, batches: '攤平中 61%', status: 'running', at: '今天 03:40', resp: 'MP 204 · 對帳 T+1' },
  { id: 'dlv_01J9…9e', seg: '定期定額活躍客', dest: '數倉 Parquet', ver: 44, mode: 'full', n: 92_300, batches: '19/19', status: 'ok', at: '今天 03:10', resp: 'manifest 已寫入' },
  { id: 'dlv_01J9…8c', seg: 'Q4 基金活動曝光者', dest: 'GA4 受眾 → Google Ads', ver: 301, mode: 'delta', n: 1_240, batches: '1/1', status: 'ok', at: '今天 02:58', resp: 'MP 204' },
  { id: 'dlv_01J8…f1', seg: '高價值沉睡客', dest: 'CRM 客戶系統', ver: 41, mode: 'full', n: 11_420, batches: '2/3', status: 'retrying', at: '昨天 03:02', resp: '503 · 第 3 次重試 15 分後' },
  { id: 'dlv_01J8…e0', seg: '看盤未下單（即時）', dest: '數據平台 Kafka', ver: '—', mode: 'realtime', n: 3_812, batches: '—', status: 'ok', at: '持續', resp: 'lag 0.8 s' },
];

/* ------------------------------------------------------------------ v0.4：查詢條件目錄（事件分析／漏斗分析共用，對標神策「篩選條件／按分組查看／指標類型」） */
export const PROPS = [
  // 事件屬性（$ 開頭為 SDK/GA 預置屬性）
  { k: 'platform', cn: '平台', scope: 'event', type: 'string', vals: ['iOS', 'Android', 'Web'] },
  { k: 'channel', cn: '渠道', scope: 'event', type: 'string', vals: ['App', '官網', 'LINE', '營業員', '活動頁'] },
  { k: '$utm_source', cn: 'UTM 來源', scope: 'event', type: 'string', vals: ['google', 'facebook', 'line', 'direct', 'edm'] },
  { k: '$utm_campaign', cn: 'UTM 活動', scope: 'event', type: 'string', vals: ['q4_open_bonus', 'etf_dca', 'us_stock_launch', 'brand'] },
  { k: '$app_version', cn: 'App 版本', scope: 'event', type: 'string', vals: ['5.2.0', '5.1.3', '5.0.9'] },
  { k: '$os_version', cn: 'OS 版本', scope: 'event', type: 'string', vals: ['iOS 18', 'iOS 17', 'Android 15', 'Android 14'] },
  { k: 'page_group', cn: '頁面群組', scope: 'event', type: 'string', vals: ['account_open', 'quote', 'trade', 'wealth', 'campaign'] },
  { k: 'symbol', cn: '商品代碼', scope: 'event', type: 'string', vals: ['2330', '2317', '0050', '2454', '00878'] },
  { k: 'market', cn: '市場', scope: 'event', type: 'string', vals: ['TSE', 'OTC', 'US', 'ETF'] },
  { k: 'side', cn: '買賣別', scope: 'event', type: 'string', vals: ['buy', 'sell'] },
  { k: 'order_type', cn: '委託類型', scope: 'event', type: 'string', vals: ['limit', 'market', 'odd_lot', 'day_trade'] },
  { k: 'reason_code', cn: '失敗原因碼', scope: 'event', type: 'string', vals: ['E001 餘額不足', 'E012 漲跌停', 'E020 零股限制', 'E031 額度不足'] },
  { k: 'method', cn: '入金方式', scope: 'event', type: 'string', vals: ['約定轉帳', 'ATM', '超商', '信用卡'] },
  { k: 'branch_id', cn: '分公司', scope: 'event', type: 'string', vals: ['TPE01', 'TPE02', 'TCH01', 'KHH01'] },
  { k: 'kyc_level', cn: 'KYC 等級', scope: 'event', type: 'string', vals: ['L1', 'L2', 'L3'] },
  { k: 'amount', cn: '金額', scope: 'event', type: 'number' },
  { k: 'qty', cn: '數量', scope: 'event', type: 'number' },
  { k: 'fee', cn: '手續費', scope: 'event', type: 'number' },
  { k: 'duration_ms', cn: '停留時長（ms）', scope: 'event', type: 'number' },
  { k: 'result_count', cn: '搜尋結果數', scope: 'event', type: 'number' },
  // 用戶屬性（profile）
  { k: 'u.lifecycle', cn: '生命週期', scope: 'user', type: 'string', vals: ['新客', '成長', '成熟', '沉睡', '流失'] },
  { k: 'u.tier', cn: '價值分級', scope: 'user', type: 'string', vals: ['P1', 'P2', 'P3', 'P4', 'P5'] },
  { k: 'u.age_band', cn: '年齡層', scope: 'user', type: 'string', vals: ['18–25', '26–35', '36–45', '46–55', '56+'] },
  { k: 'u.gender', cn: '性別', scope: 'user', type: 'string', vals: ['M', 'F'] },
  { k: 'u.first_utm_source', cn: '首次來源', scope: 'user', type: 'string', vals: ['google', 'facebook', 'line', 'direct', 'edm'] },
  { k: 'u.aum_bucket', cn: '資產區間', scope: 'user', type: 'string', vals: ['< 10 萬', '10–50 萬', '50–100 萬', '100–500 萬', '> 500 萬'] },
  { k: 'u.branch_id', cn: '所屬分公司', scope: 'user', type: 'string', vals: ['TPE01', 'TPE02', 'TCH01', 'KHH01'] },
  { k: 'u.account_age_days', cn: '開戶天數', scope: 'user', type: 'number' },
  { k: 'u.has_margin', cn: '信用戶', scope: 'user', type: 'bool' },
  { k: 'u.signup_date', cn: '註冊日期', scope: 'user', type: 'date' },
  // 標籤（標籤引擎輸出，可直接當條件）
  { k: 'tag.churn_risk', cn: '流失風險', scope: 'tag', type: 'string', vals: ['高', '中', '低'] },
  { k: 'tag.trade_style', cn: '交易風格', scope: 'tag', type: 'string', vals: ['當沖', '波段', '長期', 'ETF 定期定額'] },
  { k: 'tag.pref_market', cn: '偏好市場', scope: 'tag', type: 'string', vals: ['台股', '美股', 'ETF', '期權'] },
];
export const PROP_SCOPES = [['event', '事件屬性'], ['user', '用戶屬性'], ['tag', '用戶標籤']];
export const OPS = {
  string: [['eq', '等於'], ['neq', '不等於'], ['in', '包含於'], ['like', '包含'], ['nlike', '不包含'], ['set', '有值'], ['nset', '無值']],
  number: [['eq', '等於'], ['gt', '大於'], ['lt', '小於'], ['between', '區間'], ['set', '有值'], ['nset', '無值']],
  bool:   [['true', '為真'], ['false', '為假']],
  date:   [['rel', '相對當前時間'], ['between', '絕對時間區間'], ['set', '有值']],
};
export const AGGS = [
  ['total', '總次數', false], ['users', '觸發用戶數', false], ['avg_per_user', '人均次數', false], ['uniq', '去重數', 'prop'],
  ['sum', '數值求和', 'num'], ['avg', '數值均值', 'num'], ['max', '數值最大值', 'num'], ['min', '數值最小值', 'num'], ['p50', '數值中位數', 'num'], ['p90', '數值 90 分位', 'num'],
];
export const GRANS = [['minute', '分鐘'], ['hour', '小時'], ['day', '日'], ['week', '週'], ['month', '月'], ['total', '合計']];
export const CHART_TYPES = [['line', '折線'], ['bar', '柱狀'], ['stack', '堆疊'], ['cum', '累計'], ['table', '表格']];
export const SAVED_REPORTS = [
  { id: 'r1', name: '每日成交客戶 × 平台', kind: 'events', owner: '王小明', updated: '10-12' },
  { id: 'r2', name: '委託失敗率 × 原因碼（近 7 天）', kind: 'events', owner: '王小明', updated: '10-13' },
  { id: 'r3', name: '開戶→首筆成交（iOS vs Android）', kind: 'funnel', owner: '林主管', updated: '10-11' },
  { id: 'r4', name: '推播→成交（7 天窗口）', kind: 'funnel', owner: 'MA 小組', updated: '10-09' },
];
