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
  // 本系統只有五種角色：數據分析、行銷、數位經營、系統管理員、資訊管理（營業員／分公司主管不進本系統）
  { id: 'u-ana', rk: 'data_analyst', name: '林雅婷', email: 'yating.lin@altatech.demo', role: '數據分析', col: '#2C73EA', init: '林', scope: 'all', can: { detail: true, sql: true, approve: false, admin: false } },
  { id: 'u-mkt', rk: 'marketing', name: '黃思妤', email: 'szuyu.huang@altatech.demo', role: '行銷', col: '#D8A800', init: '黃', scope: 'all', can: { detail: false, sql: false, approve: false, admin: false } },
  { id: 'u-ops', rk: 'digital_ops', name: '陳柏宇', email: 'boyu.chen@altatech.demo', role: '數位經營', col: '#12B5A8', init: '陳', scope: 'all', can: { detail: true, sql: false, approve: true, admin: false } },
  { id: 'u-adm', rk: 'admin', name: '張敏慧', email: 'minhui.chang@altatech.demo', role: '系統管理員', col: '#7C4DD6', init: '張', scope: 'all', can: { detail: true, sql: true, approve: true, admin: true, audit: true } },
  { id: 'u-it', rk: 'it_admin', name: '吳建宏', email: 'chienhung.wu@altatech.demo', role: '資訊管理', col: '#56646D', init: '吳', scope: 'all', can: { detail: false, sql: true, approve: false, admin: false, audit: true } },
];

/* ------------------------------------------------------------------ sources */
export const SOURCES = [
  { id: 'ga360_daily', name: 'GA360 日批匯出', kind: 'batch', lag: '前日 05:10 到齊', status: 'ok', events24h: 182430112, note: 'events_20261013 已對帳，差異 0.3%' },
  { id: 'ga360_fresh', name: 'GA360 Fresh Daily', kind: 'batch', lag: '約 55 分', status: 'ok', events24h: 41210540, note: '日內增量；日批到齊後覆蓋' },
  { id: 'ga360_intraday', name: 'GA360 串流匯出（events_intraday_）', kind: 'stream', lag: '約 6 分', status: 'ok', events24h: 180915377, note: 'BigQuery 串流匯出，分鐘級；日批到齊後以日批為準' },
  { id: 'trade_cdc', name: '交易主機 CDC（委託／成交）', kind: 'stream', lag: '1.4 秒', status: 'ok', events24h: 18240902, note: 'Debezium → Flink 狀態機事件化' },
  { id: 'ledger_cdc', name: '帳務 CDC（出入金）', kind: 'stream', lag: '3.8 秒', status: 'ok', events24h: 612430, note: '' },
  { id: 'account_sys', name: '開戶系統', kind: 'api', lag: '8 秒', status: 'warn', events24h: 9812, note: 'kh_apply 近 2 小時量降 38%（iOS）' },
  { id: 'ma', name: '行銷自動化（推播／活動）', kind: 'stream', lag: '12 秒', status: 'ok', events24h: 2104880, note: '' },
  { id: 'crm', name: 'CRM 客戶主檔', kind: 'batch', lag: '前日 02:00', status: 'ok', events24h: 1230119, note: 'profile_set 日批' },
];

/* ------------------------------------------------------------------ event dictionary */
export const EVENTS = [
  // 事件字典：依客戶（神策 sbp_family 專案）模擬事件清單 112 個 + 本系統交易／帳務 CDC 補充；aliases 為本專案文件中的舊名
  {'name': 'PlanEDMClick', 'cn': 'EDM郵件點擊', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 415799, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': 'PlanEDMShow', 'cn': 'EDM開信', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 534600, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': 'ShortUrlRedirect', 'cn': 'ShortUrlRedirect', 'group': '短鏈', 'entity': 'user', 'source': '短鏈服務', 'kind': 'marketing', 'status': 'live', 'daily': 14580, 'props': ['short_url', 'target'], 'owner': '數位行銷', 'platforms': '其它,iOS,Android'},
  {'name': '$ShortUrlRedirect', 'cn': '短鏈跳轉', 'group': '短鏈', 'entity': 'user', 'source': '短鏈服務', 'kind': 'marketing', 'status': 'live', 'daily': 25740, 'props': ['short_url', 'target'], 'owner': '數位行銷', 'platforms': '其它'},
  {'name': 'Market_Data', 'cn': '市場交易數據', 'group': '業務資料（服務端）', 'entity': 'user', 'source': '服務端（CDC／批次）', 'kind': 'business', 'status': 'live', 'daily': 24310, 'props': ['symbol', 'market', 'close', 'volume'], 'owner': '數據團隊', 'platforms': '服務端'},
  {'name': '$PlanSMSArrived', 'cn': '短信已送達', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 966599, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanAudienceMarked', 'cn': '受眾標記', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 1031399, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$JourneyExit', 'cn': '旅程退出', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 664200, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$JourneyComponentEnter', 'cn': '旅程組件進入', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 739800, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanEDMClick', 'cn': '郵件點擊', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 837000, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '其它,iOS,Android'},
  {'name': '$PlanEDMShow', 'cn': '郵件開啟', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 167400, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '其它,iOS,Android'},
  {'name': 'operation_target', 'cn': '業務目標', 'group': '業務資料（服務端）', 'entity': 'user', 'source': '服務端（CDC／批次）', 'kind': 'business', 'status': 'live', 'daily': 13967, 'props': ['symbol', 'market', 'account_type'], 'owner': '數據團隊', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'market_deal_profile', 'cn': '市場交易詳情', 'group': '業務資料（服務端）', 'entity': 'user', 'source': '服務端（CDC／批次）', 'kind': 'business', 'status': 'live', 'daily': 7344, 'props': ['symbol', 'market', 'account_type'], 'owner': '數據團隊', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'cancel_profile', 'cn': '銷戶資訊', 'group': '業務資料（服務端）', 'entity': 'user', 'source': '服務端（CDC／批次）', 'kind': 'business', 'status': 'live', 'daily': 20016, 'props': ['symbol', 'market', 'account_type'], 'owner': '數據團隊', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'campaign_callBack', 'cn': '廣告_渠道回傳', 'group': '廣告渠道', 'entity': 'user', 'source': '渠道回傳（服務端）', 'kind': 'marketing', 'status': 'live', 'daily': 327600, 'props': ['campaign_id', '$utm_source', 'cost_bucket'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': '$PlanConverted', 'cn': '已轉化完成', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 966599, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanAudienceEnter', 'cn': '受眾用戶進入', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 955799, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanMsgSendPrepare', 'cn': '消息準備發出', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 1160999, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanMsgWouldNotSend', 'cn': '消息未發送', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 556200, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': 'test_login', 'cn': '測試體驗登入成功', 'group': '登入與導航', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2662200, 'props': ['channel', 'auth_method'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'stock_order_submit', 'cn': '交易_委託送出', 'group': '交易（App）', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2784000, 'props': ['symbol', 'side', 'order_type'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'sip_stockpage_view', 'cn': '定期定額_申購詳情頁瀏覽', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 38160, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'sip_resultpage_button_click', 'cn': '定期定額_申購結果頁面按鈕點擊', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 12720, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'sip_purchase', 'cn': '定期定額_申購結果', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 41000, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android', 'aliases': ['fund_subscribe']},
  {'name': 'sip_listpage_view', 'cn': '定期定額_申購列表頁瀏覽', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 23280, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'sip_change_confirm', 'cn': '定期定額_確認變更申購', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 43439, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'sip_cancel_order', 'cn': '定期定額_終止申購', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 35760, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'sip_calculate', 'cn': '定期定額_報酬率試算', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 30480, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'sip_button_click', 'cn': '定期定額_首頁按鈕點擊', 'group': '定期定額', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 39120, 'props': ['symbol', 'amount', 'plan_type'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'search_submit', 'cn': '搜索_送出搜索請求', 'group': '搜索', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 3100000, 'props': ['keyword', 'result_count'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['search_symbol']},
  {'name': 'search_result_click', 'cn': '搜索_搜索結果點擊', 'group': '搜索', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 734400, 'props': ['keyword', 'result_count'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'search_box_click', 'cn': '搜索_搜尋框點擊', 'group': '搜索', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1425600, 'props': ['keyword', 'result_count'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'realtime_bond_order', 'cn': '即時_證券_委託', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 7900000, 'props': ['order_no', 'symbol', 'market', 'side', 'amount'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'realtime_bond_deal', 'cn': '即時_證券_成交', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 5600000, 'props': ['order_no', 'symbol', 'market', 'side', 'amount'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'push_received', 'cn': 'Push送達', 'group': 'Push', 'entity': 'user', 'source': 'MA', 'kind': 'marketing', 'status': 'live', 'daily': 4200000, 'props': ['campaign_id', 'push_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'push_click', 'cn': 'Push點擊', 'group': 'Push', 'entity': 'user', 'source': 'MA', 'kind': 'marketing', 'status': 'live', 'daily': 980000, 'props': ['campaign_id', 'push_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android', 'aliases': ['push_opened']},
  {'name': 'my_agreement_sign', 'cn': '我的_線上憑證簽署', 'group': '登入與導航', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 3775799, 'props': ['channel', 'auth_method'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'mainlogin_button_click', 'cn': '主登入頁按鈕點擊', 'group': '登入與導航', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2836200, 'props': ['channel', 'auth_method'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'lower_button_click', 'cn': '導航欄按鈕點擊', 'group': '登入與導航', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1444200, 'props': ['channel', 'auth_method'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'line_unfollow', 'cn': 'Line 封鎖', 'group': 'LINE', 'entity': 'user', 'source': 'LINE（MA）', 'kind': 'marketing', 'status': 'live', 'daily': 61200, 'props': ['line_uid_hash', 'message_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'line_sendline_message', 'cn': 'Line官方推送', 'group': 'LINE', 'entity': 'user', 'source': 'LINE（MA）', 'kind': 'marketing', 'status': 'live', 'daily': 77039, 'props': ['line_uid_hash', 'message_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'line_message', 'cn': 'Line 用戶傳送訊息', 'group': 'LINE', 'entity': 'user', 'source': 'LINE（MA）', 'kind': 'marketing', 'status': 'live', 'daily': 111600, 'props': ['line_uid_hash', 'message_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'line_follow', 'cn': 'Line 加好友', 'group': 'LINE', 'entity': 'user', 'source': 'LINE（MA）', 'kind': 'marketing', 'status': 'live', 'daily': 18000, 'props': ['line_uid_hash', 'message_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'line_account_unlink', 'cn': 'Line 解除綁定', 'group': 'LINE', 'entity': 'user', 'source': 'LINE（MA）', 'kind': 'marketing', 'status': 'live', 'daily': 64080, 'props': ['line_uid_hash', 'message_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'line_account_link', 'cn': 'Line 帳號綁定', 'group': 'LINE', 'entity': 'user', 'source': 'LINE（MA）', 'kind': 'marketing', 'status': 'live', 'daily': 143280, 'props': ['line_uid_hash', 'message_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_steppg_pv', 'cn': '開戶_開戶三步頁瀏覽', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 9342, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_steppg_click', 'cn': '開戶_開戶三步頁點擊', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 10205, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_sign_result', 'cn': '開戶_文件簽署结果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 9881, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_sign', 'cn': '開戶_文件簽署', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1674, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_set_sbank_result', 'cn': '開戶_複委託交割銀行設定結果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1458, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_set_sbank', 'cn': '開戶_複委託交割銀行設定', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2862, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_set_bank_result_fh', 'cn': '開戶_出入金銀行設定結果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1997, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_set_bank_result', 'cn': '開戶_一般交割銀行設定結果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 5022, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_set_bank_fh', 'cn': '開戶_出入金銀行設定', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 4266, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_set_bank', 'cn': '開戶_一般交割銀行設定', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 12041, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_review_pass', 'cn': '開戶_審核成功', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'business', 'status': 'live', 'daily': 2310, 'props': ['account_no', 'account_type', 'branch_id', 'agent_id'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['account_open_approved']},
  {'name': 'kh_review_fail', 'cn': '開戶_審核失敗', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'business', 'status': 'live', 'daily': 640, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_profile_upload_result', 'cn': '開戶_基本資料上傳結果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 11609, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_otp_result', 'cn': '開戶_OTP驗證結果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 11394, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_otp_get', 'cn': '開戶_OTP驗證碼取得', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 5454, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_kyc_self', 'cn': '開戶_KYC自填徵信表', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 10422, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_kyc', 'cn': '開戶_KYC聯絡時間調查', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 8694, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_idphoto_upload_result', 'cn': '開戶_證件上傳結果', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 9234, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_idphoto_upload', 'cn': '開戶_證件上傳', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 10422, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_entry_khpg_pv', 'cn': '開戶_開戶首頁瀏覽', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 6400, 'props': ['channel'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['account_open_start']},
  {'name': 'kh_entry_khpg_click', 'cn': '開戶_開戶首頁點擊', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 10314, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_enter_signpg', 'cn': '開戶_進入文件簽署頁', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1782, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_enter_sbank', 'cn': '開戶_進入交割銀行頁', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 6318, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_enter_profilepg', 'cn': '開戶_進入基本資料填寫頁', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 5670, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_enter_jskhpg', 'cn': '開戶_繼續開戶成功', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 3294, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'kh_enter_idphotouploadpg', 'cn': '開戶_進入證件上傳頁', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2862, 'props': ['channel', 'step', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'kh_apply', 'cn': '開戶_完成開戶申請', 'group': '開戶', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2950, 'props': ['channel', 'account_types', 'kyc_level'], 'owner': '數位開戶', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['account_open_submit']},
  {'name': 'info_report_view', 'cn': '資訊_研究報告瀏覽', 'group': '資訊與資源位', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 663600, 'props': ['article_id', 'category'], 'owner': '內容', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'info_recomstock_click', 'cn': '資訊_推薦標的點擊', 'group': '資訊與資源位', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 764400, 'props': ['article_id', 'category'], 'owner': '內容', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'info_detail_view', 'cn': '資訊_文章詳情瀏覽', 'group': '資訊與資源位', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1218000, 'props': ['article_id', 'category'], 'owner': '內容', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'i_mkt_click', 'cn': '資源位點擊', 'group': '資訊與資源位', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1520399, 'props': ['article_id', 'category'], 'owner': '內容', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'hq_stockdetail_view', 'cn': '行情_個股明細瀏覽', 'group': '行情', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 24800000, 'props': ['symbol', 'market', 'duration_ms'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['quote_view']},
  {'name': 'hq_stockdetail_click', 'cn': '行情_個股明細點擊', 'group': '行情', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 10080000, 'props': ['symbol', 'market'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'hq_ai_choose', 'cn': '行情_智慧選股', 'group': '行情', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 8544000, 'props': ['symbol', 'market'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'home_click', 'cn': '首頁按钮點擊', 'group': '登入與導航', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1304999, 'props': ['channel', 'auth_method'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'deal_opt_mani', 'cn': '交易_自選操作', 'group': '交易（App）', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 412000, 'props': ['symbol', 'action'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['watchlist_add']},
  {'name': 'deal_commision_check', 'cn': '交易_委託查詢', 'group': '交易（App）', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 1209600, 'props': ['symbol', 'side', 'order_type'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'bond_portfolio_income', 'cn': '證券_持有損益', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 2788800, 'props': ['order_no', 'symbol', 'market', 'side', 'amount'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'bond_portfolio', 'cn': '證券_持有詳情', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 1041600, 'props': ['order_no', 'symbol', 'market', 'side', 'amount'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'bond_order', 'cn': '證券_委託', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 7900000, 'props': ['order_no', 'symbol', 'market', 'side', 'order_type', 'price', 'qty', 'amount', 'channel'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,iOS,Android', 'aliases': ['order_submit']},
  {'name': 'bond_deal', 'cn': '證券_成交', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 5600000, 'props': ['order_no', 'filled_qty', 'filled_price', 'filled_amount', 'fee', 'tax'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['order_filled']},
  {'name': 'bond_charge_income', 'cn': '證券_手續費收入', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 5600000, 'props': ['fee', 'tax'], 'owner': '交易系統', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'auc_stockpage_view', 'cn': '申購/競拍_申購詳情頁瀏覽', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 17388, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'auc_resultpage_view', 'cn': '申購/競拍_申購結果頁瀏覽', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 3564, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'auc_purchase_result', 'cn': '申購/競拍_完成申購', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 3780, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'auc_purchase', 'cn': '申購/競拍_我要申購', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 10692, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'auc_listpage_view', 'cn': '申購/競拍_申購列表頁瀏覽', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 5076, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'auc_cancel_order', 'cn': '申購/競拍_終止申購', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 12852, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'auc_calculate', 'cn': '申購/競拍_保證金試算', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 16956, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'auc_button_click', 'cn': '申購/競拍_申購列表頁點擊', 'group': '申購／競拍', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 18468, 'props': ['symbol', 'amount'], 'owner': '財富管理', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'app_login_success', 'cn': 'APP登入成功', 'group': '登入與導航', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'behavior', 'status': 'live', 'daily': 2980000, 'props': ['channel', 'auth_method'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,iOS,Android', 'aliases': ['login']},
  {'name': 'act_pageview', 'cn': '活動_活動頁瀏覽', 'group': '活動', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'marketing', 'status': 'live', 'daily': 648960, 'props': ['campaign_id', '$page_url'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'act_click', 'cn': '活動_活動頁按鈕點擊', 'group': '活動', 'entity': 'user', 'source': 'GA360（App／Web 埋點）', 'kind': 'marketing', 'status': 'live', 'daily': 214000, 'props': ['campaign_id', 'creative_id'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android', 'aliases': ['campaign_clicked']},
  {'name': 'act_ad_view', 'cn': '活動_廣告觀看', 'group': '廣告渠道', 'entity': 'user', 'source': '渠道回傳（服務端）', 'kind': 'marketing', 'status': 'live', 'daily': 140400, 'props': ['campaign_id', '$utm_source', 'cost_bucket'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': 'act_ad_cost', 'cn': '活動_廣告成本', 'group': '廣告渠道', 'entity': 'user', 'source': '渠道回傳（服務端）', 'kind': 'marketing', 'status': 'live', 'daily': 315120, 'props': ['campaign_id', '$utm_source', 'cost_bucket'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': 'AppInstall', 'cn': 'App 激活', 'group': 'App 基礎', 'entity': 'user', 'source': 'GA360（App）', 'kind': 'behavior', 'status': 'live', 'daily': 36360000, 'props': ['$screen_name', '$app_version'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': '$pageview', 'cn': 'Web 瀏覽頁面', 'group': 'Web 基礎', 'entity': 'user', 'source': 'GA360（Web）', 'kind': 'behavior', 'status': 'live', 'daily': 61200000, 'props': ['$page_url', 'page_group', '$referrer', '$utm_*'], 'owner': '數位行銷', 'platforms': 'JavaScript,服務端,Android,iOS', 'aliases': ['ga.page_view']},
  {'name': '$WebClick', 'cn': 'Web 元素點擊', 'group': 'Web 基礎', 'entity': 'user', 'source': 'GA360（Web）', 'kind': 'behavior', 'status': 'live', 'daily': 41040000, 'props': ['$page_url', 'page_group', '$referrer'], 'owner': '數位平台', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': '$AppViewScreen', 'cn': 'App 瀏覽頁面', 'group': 'App 基礎', 'entity': 'user', 'source': 'GA360（App）', 'kind': 'behavior', 'status': 'live', 'daily': 88400000, 'props': ['$screen_name', '$app_version'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,iOS,Android', 'aliases': ['ga.screen_view']},
  {'name': '$AppStart', 'cn': 'App啟動', 'group': 'App 基礎', 'entity': 'user', 'source': 'GA360（App）', 'kind': 'behavior', 'status': 'live', 'daily': 47160000, 'props': ['$screen_name', '$app_version'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': '$AppEnd', 'cn': 'App退出', 'group': 'App 基礎', 'entity': 'user', 'source': 'GA360（App）', 'kind': 'behavior', 'status': 'live', 'daily': 77399999, 'props': ['$screen_name', '$app_version'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,iOS,Android'},
  {'name': '$AppClick', 'cn': 'App 元素點擊', 'group': 'App 基礎', 'entity': 'user', 'source': 'GA360（App）', 'kind': 'behavior', 'status': 'live', 'daily': 29160000, 'props': ['$screen_name', '$app_version'], 'owner': 'App 產品', 'platforms': 'JavaScript,服務端,Android,iOS'},
  {'name': '$PlanPopupDisplay', 'cn': '弹窗展示', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 145800, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanMsgSendDone', 'cn': '消息已發出', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 156600, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端,其它,iOS,Android'},
  {'name': '$PlanMsgArrived', 'cn': '消息同步終態信息', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 998999, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '其它,iOS,Android,服務端'},
  {'name': '$AppPushClick', 'cn': 'App 推送點擊', 'group': 'Push', 'entity': 'user', 'source': 'MA', 'kind': 'marketing', 'status': 'live', 'daily': 684600, 'props': ['campaign_id', 'push_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': '$PlanPopupClick', 'cn': '點擊行為', 'group': '智能運營（MA）', 'entity': 'user', 'source': 'MA 系統事件', 'kind': 'marketing', 'status': 'live', 'daily': 685800, 'props': ['plan_id', 'journey_id', 'msg_id'], 'owner': '數位行銷', 'platforms': '服務端'},
  {'name': 'fund_deposit', 'cn': '入金', 'group': '帳務（CDC）', 'entity': 'account', 'source': '帳務 CDC', 'kind': 'trade', 'status': 'live', 'daily': 310000, 'props': ['amount', 'method', 'bank_code'], 'owner': '帳務', 'platforms': '服務端', 'note': '清單外：帳務主機 CDC 新增'},
  {'name': 'order_rejected', 'cn': '委託失敗', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 118000, 'props': ['order_no', 'reason_code'], 'owner': '交易系統', 'platforms': '服務端', 'note': '清單外：交易主機狀態機事件化新增'},
  {'name': 'order_cancelled', 'cn': '刪單', 'group': '證券（交易主機）', 'entity': 'account', 'source': '交易 CDC', 'kind': 'trade', 'status': 'live', 'daily': 1900000, 'props': ['order_no', 'cancel_qty'], 'owner': '交易系統', 'platforms': '服務端', 'note': '清單外：交易主機狀態機事件化新增'},
  {'name': 'ga.ab_exposure', 'cn': 'AB 實驗曝光', 'group': '未登記', 'entity': 'user', 'source': 'GA360', 'kind': 'behavior', 'status': 'quarantine', 'daily': 1200000, 'props': ['exp_id', 'variant'], 'owner': '—', 'platforms': 'JavaScript,iOS,Android', 'note': '未登記事件：進隔離區，待產品團隊登記'},
  {'name': 'option_apply', 'cn': '期權申請', 'group': '業務資料（服務端）', 'entity': 'user', 'source': '業務系統', 'kind': 'business', 'status': 'draft', 'daily': 0, 'props': ['product', 'status'], 'owner': '衍生性商品', 'platforms': '服務端'},
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
    { ev: '$pageview', cn: '開戶頁瀏覽', n: 184230, prev: 171900 },
    { ev: 'kh_entry_khpg_pv', cn: '開戶開始', n: 61410, prev: 58200 },
    { ev: 'kh_apply', cn: '開戶送件', n: 28930, prev: 29100 },
    { ev: 'kh_review_pass', cn: '開戶核准', n: 22100, prev: 22300 },
    { ev: 'fund_deposit', cn: '首次入金', n: 9060, prev: 10480 },
    { ev: 'bond_deal', cn: '首筆成交', n: 6120, prev: 7010 },
  ] },
  { id: 'trade', name: '看盤 → 委託 → 成交', window: '當日', steps: [
    { ev: 'hq_stockdetail_view', cn: '看盤', n: 296000, prev: 284000 },
    { ev: 'bond_order', cn: '委託', n: 171000, prev: 166000 },
    { ev: 'bond_deal', cn: '成交', n: 138000, prev: 135000 },
  ] },
  { id: 'push', name: '推播 → 開啟 → 成交', window: '7 天', steps: [
    { ev: 'push_received', cn: '推播送達', n: 1820000, prev: 1760000 },
    { ev: 'push_click', cn: '推播開啟', n: 412000, prev: 395000 },
    { ev: '$AppViewScreen', cn: '進入商品頁', n: 188000, prev: 171000 },
    { ev: 'bond_deal', cn: '成交', n: 41200, prev: 38900 },
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
  { id: 'sg2', name: '新戶未入金（7 天）', n: 8930, rule: 'kh_review_pass 在 7 天內 AND fund_deposit = 0', by: '陳柏宇', status: 'active', upd: '今天 06:10', rt: false },
  { id: 'sg3', name: '看盤未下單（即時）', n: 4210, rule: 'hq_stockdetail_view ≥ 3 in 10m AND bond_order = 0 in 30m', by: '林雅婷', status: 'active', upd: '即時', rt: true },
  { id: 'sg4', name: 'iOS 23.4 開戶中斷者', n: 1860, rule: 'kh_entry_khpg_pv AND NOT kh_apply in 24h AND app_version = 23.4 AND platform = iOS', by: 'funnel-doctor', status: 'pending_approval', upd: '今天 06:41', rt: false },
  { id: 'sg5', name: '定期定額活躍客', n: 92300, rule: 'sip_purchase(plan_type = 定期定額) ≥ 1 in 60d', by: '林雅婷', status: 'active', upd: '昨天', rt: false },
  { id: 'sg6', name: 'Q4 基金活動曝光者', n: 356000, rule: 'campaign_exposed(campaign_id = q4_fund)', by: '行銷自動化', status: 'active', upd: '今天 05:00', rt: false },
];

/* ------------------------------------------------------------------ triggers */
export const TRIGGERS = [
  { id: 'tr1', name: '看盤未下單 30 分鐘', dsl: 'hq_stockdetail_view ≥ 3 in 10m → NOT bond_order in 30m', audience: 'sg3', throttle: '1/人/24h', out: 'dmp.triggers → MA 推播', fires24h: 3812, status: 'on' },
  { id: 'tr2', name: '新戶核准未入金 3 天', dsl: 'kh_review_pass → NOT fund_deposit in 72h', audience: 'sg2', throttle: '1/人', out: 'dmp.triggers → MA 推播', fires24h: 612, status: 'on' },
  { id: 'tr3', name: '委託失敗（系統原因）', dsl: 'order_rejected(reason_code LIKE "SYS%")', audience: '全體', throttle: '1/人/1h', out: 'dmp.triggers → anomaly-watch + 交易系統值班', fires24h: 1940, status: 'on' },
  { id: 'tr4', name: '高價值客登入（MA 即時訊息）', dsl: 'login AND fee_tier ∈ {P1}', audience: '全體', throttle: '1/人/日', out: 'dmp.triggers → MA 即時訊息', fires24h: 2206, status: 'on' },
  { id: 'tr5', name: 'iOS 開戶中斷即時挽回（agent 提議）', dsl: 'kh_entry_khpg_pv(platform=iOS, app_version=23.4) → NOT kh_apply in 2h', audience: 'sg4', throttle: '1/人', out: 'dmp.triggers → MA', fires24h: 0, status: 'pending_approval' },
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
      ['10-14 13:58', 'bond_deal', 'trade', '成交 2330 買 1,000 股 @1,050', 'fee 1,496 · channel app'],
      ['10-14 13:57', 'bond_order', 'trade', '委託 2330 買 1,000 股 限價 1,050', 'channel app · 現股'],
      ['10-14 13:52', 'hq_stockdetail_view', 'ga', '看盤 2330 台積電', 'duration 94s · from_screen 自選'],
      ['10-14 13:51', '$AppViewScreen', 'ga', '畫面 自選股', 'app_version 23.4 · iOS'],
      ['10-14 13:50', 'app_login_success', 'sys', '登入 App', 'auth FaceID'],
      ['10-14 09:12', 'push_click', 'mkt', '開啟推播「台積電法說會重點」', 'campaign tsmc_q3'],
      ['10-13 20:40', '$pageview', 'ga', '瀏覽 官網 ETF 專區', 'utm_source line · page_group etf'],
      ['10-11 10:05', 'sip_purchase', 'trade', '基金申購 定期定額 3,000', 'fund_code F0088'],
      ['10-08 14:22', 'order_rejected', 'trade', '委託失敗 2454', 'reason_code USR03 餘額不足'],
      ['08-21 11:30', 'kh_review_pass', 'sys', '開戶核准 證券', 'branch TPE01 · agent 王信宏'],
      ['08-20 22:14', 'kh_apply', 'sys', '開戶送件', 'channel app · kyc L2'],
      ['08-20 21:58', '$pageview', 'ga', '瀏覽 開戶頁', 'utm_source google · utm_campaign brand'],
    ] },
  { one_id: 'oid_2m8x1c', cust: 'cust_•••0412', branch: 'TPE01', agent: '王信宏', lifecycle: '沉睡', fee_tier: 'P1', churn: 81, pref: 'US', age: '45-54', since: '2019-03-02', accounts: ['證券 ••••1203', '信用 ••••1203', '複委託 ••••7730'],
    identities: [['$identity_login_id', 'cust_•••0412', 'strong'], ['$identity_mobile_hash', 'hmac:21cc…', 'strong'], ['$identity_account_no', '••••1203', 'multi'], ['$identity_account_no', '••••7730', 'multi'], ['$identity_device_id', 'aid:88AD…', 'weak'], ['$identity_cookie_id', 'GA1.2.0921…', 'weak']],
    timeline: [
      ['09-30 10:12', 'sip_purchase', 'trade', '基金申購 定期定額 10,000', 'fund_code F0021（自動扣款）'],
      ['09-02 15:40', 'bond_deal', 'trade', '成交 AAPL 賣 50 股', 'channel agent · 複委託'],
      ['09-02 15:21', 'bond_order', 'trade', '委託 AAPL 賣 50 股', 'channel agent'],
      ['08-30 10:12', 'sip_purchase', 'trade', '基金申購 定期定額 10,000', 'fund_code F0021（自動扣款）'],
      ['08-12 09:05', '$AppViewScreen', 'ga', '畫面 庫存', 'app_version 23.1 · Android'],
      ['08-12 09:03', 'app_login_success', 'sys', '登入 App', 'auth password'],
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
    summary: '近 7 天 kh_entry_khpg_pv → kh_apply 轉化 47.1% → 41.0%，僅發生在 iOS 23.4（其他版本持平）。流失者 62% 在 KYC 拍照頁離開（path.run），該步驟中位耗時從 48 秒拉長到 3 分 10 秒（interval.run）。',
    evidence: ['funnel.run(open, 7d vs prev 7d, by platform×app_version)', 'path.run(after kh_entry_khpg_pv, 5 steps)', 'interval.run(start→submit)'],
    actions: [{ t: '提議分群「iOS 23.4 開戶中斷者」(sg4)', st: 'pending' }, { t: '提議即時挽回規則 (tr5)', st: 'pending' }],
    chart: 'funnel_open', warn: '假設尚未由 App 團隊驗證' },
  { id: 'ins_03', kind: 'risk', sev: 'high', agent: 'churn-radar', at: '今天 07:04', conf: .72, status: 'acknowledged',
    title: '11,420 位手續費前 20% 客戶近 30 天未登入，但 64% 仍有定期定額扣款',
    summary: '這群客戶 90 天貢獻手續費 1.84 億；行為衰減主要在 App 看盤（−71%），而非資金離開。建議以「市場資訊」而非「促銷」切入，並由所屬營業員聯繫。',
    evidence: ['distribution.run(fee_income, 90d)', 'retention.run(bond_deal, [7,14,30])', 'segment.preview(sg1)'],
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
    title: '21:00 $pageview 量下降 24%：官網活動頁下線所致，非埋點異常',
    summary: '下降集中在 page_group = campaign；串流匯出與日批對帳一致。判定為正常業務變動。',
    evidence: ['anomaly.scan(ga_events, 1h)', 'metric.query($pageview by page_group)'],
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
    calls: [['semantic.search', '"開戶 入金 轉化"', '0 B'], ['funnel.run', 'steps=[kh_review_pass, fund_deposit] window=30d time=7d', '2.1 GB'], ['funnel.run', '…compare=previous_period', '2.0 GB'], ['funnel.run', '…group_by=platform', '2.3 GB']],
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
/* 行為路徑（起點 hq_stockdetail_view 之後 4 步，抽樣 1/10） */
export const PATHS = {
  anchor: 'hq_stockdetail_view', sample: '1/10', users: 29600,
  nodes: [
    [{ k: 'hq_stockdetail_view', cn: '看盤', n: 29600 }],
    [{ k: 'bond_order', cn: '委託', n: 12140 }, { k: 'hq_stockdetail_view', cn: '看盤（其他）', n: 8880 }, { k: 'deal_opt_mani', cn: '加入自選', n: 3550 }, { k: '$AppViewScreen:news', cn: '新聞', n: 2370 }, { k: 'exit', cn: '離開', n: 2660 }],
    [{ k: 'bond_deal', cn: '成交', n: 9710 }, { k: 'order_rejected', cn: '委託失敗', n: 1210 }, { k: 'bond_order', cn: '委託', n: 4210 }, { k: 'hq_stockdetail_view', cn: '看盤', n: 5330 }, { k: 'exit', cn: '離開', n: 9140 }],
    [{ k: 'hq_stockdetail_view', cn: '看盤', n: 7110 }, { k: '$AppViewScreen:inventory', cn: '庫存', n: 4320 }, { k: 'bond_order', cn: '委託', n: 2920 }, { k: 'exit', cn: '離開', n: 15250 }],
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

/* 歸因：轉化 = bond_deal（首筆），回溯 14 天 */
export const ATTRIBUTION = {
  conversion: 'bond_deal（首筆成交）', window: '14 天', conversions: 6120, value: 9_140_000,
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
  { id: 'trade_core', type: 'MSSQL', cn: '交易主機（SQL Server 2019）', mode: 'CDC（Debezium）', tables: 6, models: 3, lag: '1.4 秒', status: 'ok', rows24h: 18_240_902,
    auth: { method: 'kerberos', cn: 'Kerberos（AD 網域）', fields: { host: 'sql-core.bank.internal', port: 1433, database: 'TRADE', principal: 'svc-dmp-ro@BANK.LOCAL' }, secret: { field: 'keytab', ref: 'kv/data/dmp/prod/trade_core#keytab', provider: 'OpenBao' }, rotation: { kind: 'rotating', every: 90, due: '2026-12-20', left: 67, state: 'ok' }, lastTest: { at: '10-14 06:00', ok: true }, owner: 'DBA 陳', tls: true, privileges: [['db_datareader（TRADE.dbo）', true], ['SELECT on cdc.*', true], ['VIEW DATABASE STATE', true], ['非 db_owner', true]] } },
  { id: 'ga_bq', type: 'BigQuery', cn: 'GA360 匯出（analytics_3012…）', mode: 'Storage Read API（Daily + Fresh）', tables: 2, models: 2, lag: '55 分', status: 'ok', rows24h: 182_430_112,
    auth: { method: 'workload_identity', cn: 'Workload Identity（無密鑰）', fields: { project_id: 'ga-export-3012', dataset: 'analytics_3012', location: 'asia-east1' }, secret: null, rotation: { kind: 'short_lived' }, lastTest: { at: '10-14 05:10', ok: true }, owner: '數位行銷 林', tls: true, privileges: [['bigquery.dataViewer（dataset）', true], ['bigquery.readSessionUser', true], ['bigquery.jobUser（dmp 專案）', true]] } },
  { id: 'crm_pg', type: 'PostgreSQL', cn: 'CRM 客戶主檔', mode: 'CDC（pgoutput）+ 日批', tables: 4, models: 2, lag: '3.1 秒', status: 'ok', rows24h: 1_230_119,
    auth: { method: 'password', cn: '帳號密碼', fields: { host: '10.20.8.14', port: 5432, database: 'crm', username: 'dmp_ro' }, secret: { field: 'password', ref: 'projects/dmp-prod/secrets/crm-pg/versions/3', provider: 'Secret Manager' }, rotation: { kind: 'rotating', every: 90, due: '2026-10-24', left: 10, state: 'warn' }, lastTest: { at: '10-14 02:00', ok: true }, owner: 'CRM 王', tls: true, privileges: [['CONNECT / USAGE', true], ['SELECT on 4 表', true], ['REPLICATION', true], ['非 superuser', true]], note: '建議改 Cloud SQL IAM 驗證（短效 token，免輪替）' } },
  { id: 'sensors', type: 'SensorsData', cn: '神策分析歷史事件（一次性匯入，已完成）', mode: '查詢 API → Parquet → Iceberg', tables: 1, models: 1, lag: '已完成 10/02', status: 'ok', rows24h: 0, note: '匯入 48.7 億筆，對帳差異 0.4%；之後行為事件全部來自 GA360',
    auth: { method: 'api_token', cn: 'API token（已撤銷）', fields: { base_url: 'https://sa.bank.internal', project: 'production' }, secret: { field: 'api_secret', ref: 'projects/dmp-prod/secrets/sensors-token/versions/2', provider: 'Secret Manager', revoked: true }, rotation: { kind: 'revoked' }, lastTest: { at: '10-02 18:40', ok: true }, owner: '數據分析 王', tls: true, privileges: [['專案唯讀分析帳號', true]] } },
  { id: 'fund_pg', type: 'PostgreSQL', cn: '基金平台', mode: 'CDC', tables: 2, models: 1, lag: '4.0 秒', status: 'warn', rows24h: 41_000, note: '來源新增欄位 PLAN_FREQ，建議映射到 plan_type',
    auth: { method: 'cert', cn: 'mTLS 用戶端憑證', fields: { host: 'fund-db.bank.internal', port: 5432, database: 'fund', username: 'dmp_ro' }, secret: { field: 'client_cert', ref: 'kv/data/dmp/prod/fund_pg#client_cert', provider: 'OpenBao' }, rotation: { kind: 'rotating', every: 365, due: '2027-03-01', left: 138, state: 'ok' }, lastTest: { at: '10-13 02:00', ok: false, failedStep: 'cdc_prereq' }, owner: '財富 李', tls: true, privileges: [['CONNECT / USAGE', true], ['SELECT on 2 表', true], ['REPLICATION', true], ['publication 含新表', false]] } },
];
// 連接器鑑權目錄（鏡自 backend/integration/connectors.yaml）
export const CONNECTOR_AUTH = {
  MSSQL: { port: 1433, methods: [
    { id: 'kerberos', cn: 'Kerberos（AD 網域）', rec: true, fields: ['host', 'port', 'database', 'principal'], secret: 'keytab', rotate: 0, note: 'keytab 存密鑰庫，以 CSI 掛載到執行 Pod；不需 SQL 帳密' },
    { id: 'entra_id_sp', cn: 'Entra ID 服務主體', rec: false, fields: ['host', 'port', 'database', 'tenant_id', 'client_id'], secret: 'client_secret', rotate: 180, note: 'Azure SQL／Arc 啟用的 SQL Server' },
    { id: 'password', cn: 'SQL 帳號密碼', rec: false, fields: ['host', 'port', 'database', 'username'], secret: 'password', rotate: 90, note: '僅來源不支援前兩者時；encrypt=true、不信任自簽憑證' }],
    priv: { batch: ['db_datareader（指定 schema）', 'VIEW DEFINITION'], cdc: ['SELECT on cdc.*', 'db_datareader', 'VIEW DATABASE STATE', '不需 db_owner（CDC 啟用由 DBA 執行）'] } },
  PostgreSQL: { port: 5432, methods: [
    { id: 'iam', cn: 'Cloud SQL IAM 驗證（短效 token）', rec: true, fields: ['instance', 'database', 'iam_user'], secret: null, rotate: 0, note: 'Workload Identity 取 1 小時 token，無長期密鑰；機房無此選項' },
    { id: 'cert', cn: 'mTLS 用戶端憑證', rec: false, fields: ['host', 'port', 'database', 'username'], secret: 'client_cert', rotate: 365, note: 'sslmode=verify-full' },
    { id: 'password', cn: '帳號密碼', rec: false, fields: ['host', 'port', 'database', 'username'], secret: 'password', rotate: 90, note: '機房 OpenBao 可改用動態帳密（TTL 24h）' }],
    priv: { batch: ['CONNECT', 'USAGE on schema', 'SELECT on 指定表'], cdc: ['REPLICATION（或 cloudsqlreplica）', 'publication（DBA 預建）', 'SELECT on 指定表', '不需 superuser'] } },
  BigQuery: { methods: [
    { id: 'workload_identity', cn: 'Workload Identity（無密鑰）', rec: true, fields: ['project_id', 'dataset', 'location'], secret: null, rotate: 0, note: 'GA 匯出專案授權 dmp-ingest 服務帳號即可' },
    { id: 'sa_impersonation', cn: '服務帳號模擬', rec: false, fields: ['project_id', 'dataset', 'location', 'target_service_account'], secret: null, rotate: 0, note: '跨專案；短效 token、可審計' },
    { id: 'service_account_key', cn: '服務帳號金鑰檔', rec: false, fields: ['project_id', 'dataset', 'location'], secret: 'sa_key_json', rotate: 90, note: '不建議；組織政策通常禁止建立金鑰' }],
    priv: { batch: ['bigquery.dataViewer（dataset 層級）', 'bigquery.readSessionUser', 'bigquery.jobUser（dmp 專案）'] } },
  SensorsData: { methods: [{ id: 'api_token', cn: 'API token', rec: true, fields: ['base_url', 'project'], secret: 'api_secret', rotate: 180, note: '一次性匯入完成後撤銷' }], priv: { batch: ['專案唯讀分析帳號'] } },
  Kafka: { port: 9092, methods: [
    { id: 'mtls', cn: 'mTLS', rec: true, fields: ['bootstrap', 'topics'], secret: 'client_cert', rotate: 365, note: '' },
    { id: 'sasl_scram', cn: 'SASL/SCRAM', rec: false, fields: ['bootstrap', 'topics', 'username'], secret: 'password', rotate: 90, note: '' },
    { id: 'sasl_oauthbearer', cn: 'SASL/OAUTHBEARER', rec: false, fields: ['bootstrap', 'topics', 'token_endpoint', 'client_id'], secret: 'client_secret', rotate: 180, note: '' }],
    priv: { batch: ['Read on 指定 topic', 'Describe on consumer group'] } },
  GCS: { methods: [
    { id: 'workload_identity', cn: 'Workload Identity（無密鑰）', rec: true, fields: ['bucket', 'prefix'], secret: null, rotate: 0, note: '' },
    { id: 'hmac_key', cn: 'HMAC 金鑰（S3 相容）', rec: false, fields: ['bucket', 'prefix', 'endpoint'], secret: 'secret_key', rotate: 90, note: '機房 SeaweedFS／NAS' }],
    priv: { batch: ['storage.objectViewer（bucket/prefix）'] } },
};
export const FIELD_CN = { host: '主機', port: '埠', database: '資料庫', principal: 'Kerberos 主體', tenant_id: 'Tenant ID', client_id: 'Client ID', username: '帳號', instance: 'Cloud SQL 執行個體', iam_user: 'IAM 使用者', project_id: 'GCP 專案', dataset: 'Dataset', location: '位置', target_service_account: '目標服務帳號', base_url: 'API 網址', project: '專案', bootstrap: 'Bootstrap', topics: 'Topics', token_endpoint: 'Token 端點', bucket: 'Bucket', prefix: '前綴', endpoint: '端點' };
export const TEST_STEPS = [['dns', 'DNS 解析'], ['tcp', 'TCP 連通'], ['tls', 'TLS 憑證驗證'], ['auth', '鑑權'], ['privilege', '最小權限自檢'], ['cdc_prereq', 'CDC 前置'], ['sample_read', '抽樣讀取'], ['clock_skew', '時鐘偏移']];
export const TEST_FIX = { dns: '檢查主機名稱／私有 DNS；GCP 需 Private Google Access 或 Cloud DNS 轉發', tcp: '請網路放行 dmp 固定出口 IP 與埠號', tls: '伺服器憑證不受信任：提供 CA bundle 或換內部 CA 簽發；不要關閉驗證', auth: '帳密／keytab／服務主體錯誤或過期：確認密鑰庫版本、Kerberos 時間同步', privilege: '依最小權限清單補授權（唯讀即可）', cdc_prereq: '請 DBA 啟用 CDC／wal_level=logical／建立 publication 與 slot', sample_read: '確認表名、schema、分片表命名', clock_skew: '來源時鐘偏移 > 5 秒，請對時 NTP' };

export const MODEL_EXAMPLE = {
  id: 'mdl_order_hist', name: '交易主機委託表 → 委託/成交事件', source: 'trade_core · dbo.ORDER_HIST', entity: '事件（狀態機事件化）',
  src: [['ORD_NO', 'varchar(20)', '0.0%', 'A2026101400012345'], ['CUST_ID', 'varchar(12)', '0.0%', '00007281'], ['ACCT_NO', 'varchar(10)', '0.0%', '9801234567'], ['STK_CODE', 'char(6)', '0.0%', '2330  '], ['MKT', 'char(1)', '0.0%', 'T'], ['BS_FLAG', 'char(1)', '0.0%', 'B'], ['PRICE', 'decimal(12,2)', '0.0%', '1050.00'], ['QTY', 'int', '0.0%', '1000'], ['STATUS', 'char(1)', '0.0%', 'F'], ['ORD_TIME', 'datetime2', '0.0%', '2026-10-14 13:57:02'], ['CHANNEL', 'char(1)', '0.4%', 'A'], ['CUST_NAME', 'nvarchar(50)', '0.0%', '（個資）'], ['PHONE', 'varchar(15)', '2.1%', '（個資）']],
  map: [['ORD_NO', 'order_no', '—'], ['CUST_ID', '$identity_login_id', 'hmac_sha256(k_idm)'], ['ACCT_NO', '$identity_account_no', 'fpe(k_acct)'], ['STK_CODE', 'symbol', 'trim'], ['MKT', 'market', 'map{T:TSE,O:OTC,U:US,H:HK}'], ['BS_FLAG', 'side', 'map{B:buy,S:sell}'], ['PRICE', 'price', 'to_decimal(2)'], ['QTY', 'qty', 'to_int'], ['PRICE×QTY', 'amount', 'expr'], ['STATUS', 'event', '狀態機：N→bond_order, F→bond_deal, C→order_cancelled, R→order_rejected'], ['ORD_TIME', 'time', 'to_ms_utc(Asia/Taipei)'], ['CHANNEL', 'channel', 'map{A:app,W:web,S:agent}'], ['CUST_NAME', '（丟棄）', '個資不進平台'], ['PHONE', '（丟棄）', '個資不進平台']],
  rules: [['必填', 'order_no, symbol, side, qty', '隔離'], ['枚舉', 'market ∈ {TSE,OTC,US,HK,FUND}', '隔離'], ['範圍', 'amount ≥ 0', '隔離'], ['時間', 'time 在 ±7 天', '隔離'], ['個資掃描', '身分證／手機／Email 正規式', '拒絕＋告警'], ['去重', 'event_id = sha256(source|ORD_NO|STATUS|lsn)', '最終去重']],
  preview: { sample: 1000, ok: 994, quarantined: 6, reasons: ['market 值 "X" ×4', 'time 超出 7 天 ×2'], pii: 0, newProps: 0, estDaily: '790 萬' },
};

/* 智能分析：選擇題式配置卡示範 */
export const CONFIG_CARDS = {
  '上週開戶到入金的轉化率？跟前一週比': { model: '漏斗分析', conf: .91, params: [['步驟', 'kh_review_pass → fund_deposit'], ['視窗', '30 天'], ['時間', '上週（10/06–10/12）'], ['對比', '前一週'], ['分組', '—']], alt: ['事件分析：open_to_fund_rate 日趨勢'] },
  '哪個平台的委託失敗率最高？': { model: '事件分析', conf: .88, params: [['指標', 'order_reject_rate（委託失敗率）'], ['時間', '近 7 天'], ['分組', 'platform'], ['篩選', '—']], alt: ['事件分析：再按 reason_class 分組'] },
  '幫我找出高價值但 30 天沒登入的客戶': { model: '用戶列表', conf: .84, params: [['條件 1', 'fee_tier ∈ {P1, P2}'], ['條件 2', 'login 近 30 天 = 0 次'], ['輸出', '人數預估 + 畫像（不含個人名單）']], alt: ['分群：另存為「高價值沉睡客」'] },
};

/* ================================================================== v0.3：企業級權限、隱碼、審計統計 */
export const ROLES = [
  { key: 'admin', cn: '系統管理員', scope: '全域', users: 2, desc: '平台設定、角色與指派、審批（全量推送、L3 揭露、臨時提權、OneID 合併）、審計全覽、DSAR' },
  { key: 'marketing', cn: '行銷', scope: '全域（欄位隱碼）', users: 5, desc: '分析模組、分群與推送單建立（生效需數位經營審批）、觸發規則建立、活動復盤；用戶列表隱碼、不可匯出、不可細查' },
  { key: 'data_analyst', cn: '數據分析', scope: '全域（欄位隱碼）', users: 6, desc: '全部分析模組、用戶列表、細查（需理由）、匯出（需審批）、SQL 沙箱、標籤定義' },
  { key: 'digital_ops', cn: '數位經營', scope: '全域', users: 2, desc: '業務負責人：分析、用戶列表、審批分群／推送／觸發／agent 提議／匯出／L2 揭露' },
  { key: 'it_admin', cn: '資訊管理', scope: '全域（不含個人）', users: 3, desc: '數據整合、資料流與效能、元事件／用戶表／維度表治理、平台技術設定、審計日誌；不看個人資料' },
  { key: 'svc_insight', cn: 'AI 服務身份', scope: '僅聚合', users: 4, desc: '排程 agent 專用，不可細查' },
];
export const MODULES = [['overview', '總覽'], ['events', '事件／漏斗／路徑／歸因'], ['users', '用戶列表'], ['profile', '用戶細查'], ['segments', '標籤與分群'], ['triggers', '即時觸發'], ['integration', '數據整合'], ['ai_ask', 'AI 洞察'], ['ai_admin', 'Agent 設定'], ['governance', '資料治理'], ['audit', '日誌統計'], ['iam', '權限中心']];
// 動作：v 可見 u 可用 e 匯出 a 審批 d 設定
export const MATRIX = {
  admin: { overview: 'vuead', events: 'vuead', users: 'vuead', profile: 'u', segments: 'vuead', triggers: 'vuead', integration: 'vuead', ai_ask: 'vuea', ai_admin: 'vd', governance: 'vuead', audit: 'vuea', iam: 'vd' },
  marketing: { overview: 'v', events: 'vu', users: 'vu', profile: '', segments: 'vu', triggers: 'vu', integration: '', ai_ask: 'u', ai_admin: '', governance: '', audit: 'v', iam: '' },
  data_analyst: { overview: 'vu', events: 'vue', users: 'vue', profile: 'u', segments: 'vu', triggers: 'v', integration: 'v', ai_ask: 'u', ai_admin: '', governance: 'v', audit: 'v', iam: '' },
  digital_ops: { overview: 'vu', events: 'vue', users: 'vue', profile: 'u', segments: 'vua', triggers: 'vua', integration: 'v', ai_ask: 'ua', ai_admin: 'v', governance: 'v', audit: 'vu', iam: 'v' },
  it_admin: { overview: 'vu', events: 'vu', users: '', profile: '', segments: 'v', triggers: 'v', integration: 'vuead', ai_ask: 'u', ai_admin: 'vd', governance: 'vued', audit: 'vue', iam: 'v' },
  svc_insight: { overview: '', events: 'u', users: '', profile: '', segments: 'v', triggers: '', integration: '', ai_ask: 'u', ai_admin: '', governance: '', audit: '', iam: '' },
};
export const DATA_SCOPES = [
  { k: '組織範圍（保留）', rule: 'branch_id IN (…) / agent_id IN (…)', how: '查詢編譯器加行級條件；本系統五種角色皆為全域，營業員／分公司主管不進本系統，此規則保留給自訂角色', who: '自訂角色（未啟用）' },
  { k: '專案／資料來源', rule: 'project IN (…) AND source IN (…)', how: '角色可見清單', who: '全部' },
  { k: '分群限制', rule: 'one_id_uint IN (segment_bitmap)', how: '只允許在特定分群內分析', who: '外包帳號（自訂角色）' },
  { k: '時間範圍', rule: 'event_date ≥ today() − 180', how: '限制回溯天數', who: '外包帳號（自訂角色）' },
  { k: 'K 匿名門檻', rule: '分組人數 < 20 → 顯示「< 20」', how: '結果後處理，防反推個人', who: '全部' },
  { k: '聚合專用身份', rule: '禁止 profile / users', how: '排程 agent 不能觸及個人', who: 'AI 服務身份' },
];
export const MASK_RULES = [
  { field: '姓名／身分證／手機／Email／完整帳號', level: 'L4', plain: '無（不進分析倉）', others: '—', how: '僅存於來源系統與 OneID 加密區' },
  { field: 'one_id / 客戶代碼 / 帳號（代碼化）', level: 'L3', plain: '系統管理員', others: 'mask_tail(4)：cust_•••7281', how: '可 JOIN 不可反推；揭露需系統管理員核准' },
  { field: '單筆金額 / 持股 / 手續費', level: 'L2', plain: '數據分析、數位經營', others: 'bucket：10–50 萬（行銷、資訊管理）；匯出一律區間化', how: '揭露需數位經營核准' },
  { field: '流失分', level: 'L2', plain: '數據分析、數位經營', others: 'bucket：高／中／低（行銷）', how: '' },
  { field: '頁面 / 事件 / 裝置 / 渠道', level: 'L1', plain: '所有分析角色', others: '—', how: 'URL 去除查詢字串' },
  { field: '看板聚合數字', level: 'L0', plain: '所有人', others: '—', how: '' },
];
export const APPROVALS = [
  { id: 'ap_1042', type: '分群推送', who: '林雅婷', what: '新戶未入金（7 天）→ MA + CRM', n: '8,930 人', at: '今天 13:40', status: 'pending', approver: '陳柏宇' },
  { id: 'ap_1041', type: 'agent 提議', who: 'churn-radar', what: '分群「高價值沉睡客」', n: '11,420 人 · 信心 72%', at: '今天 07:04', status: 'pending', approver: '陳柏宇' },
  { id: 'ap_1040', type: '欄位揭露', who: '林雅婷', what: 'oid_7k3a9q · 帳號尾碼（L3）', n: '理由：客訴調查需核對帳戶', at: '今天 11:20', status: 'pending', approver: '張敏慧（系統管理員）' },
  { id: 'ap_1039', type: '匯出', who: '林雅婷', what: '用戶列表（定期定額活躍客）', n: '92,300 列 · 區間化', at: '昨天 17:05', status: 'approved', approver: '陳柏宇' },
  { id: 'ap_1038', type: '臨時提權', who: '陳柏宇', what: '數位經營 → 系統管理員 4 小時', n: '事故調查', at: '10-12 09:10', status: 'expired', approver: '張敏慧 + 數位經營（雙人）' },
  { id: 'ap_1037', type: 'agent 提議', who: 'funnel-doctor', what: '即時規則「iOS 開戶中斷挽回」', n: '1,860 人', at: '10-12 06:41', status: 'rejected', approver: '陳柏宇' },
];
const _aud = (base, w) => { const o = []; for (let i = 29; i >= 0; i--) { const d = dayOffset(-i); const dow = d.getDay(); o.push({ d: dkey(d), v: Math.round(base * ((dow === 0 || dow === 6) ? .25 : 1) * (1 + (rnd() - .5) * w)) }); } return o; };
export const AUDIT = {
  tiles: [['平台活躍使用者（日）', '312', '+4.1%'], ['查詢次數（日）', '8,420', '+6.3%'], ['細查次數（日）', '236', '+18.5%'], ['揭露申請（本週）', '14', '核准 9'], ['匯出筆數（本週）', '183,400', '3 件'], ['權限拒絕（日）', '27', '−12%']],
  profileByRole: [{ name: '數據分析', data: _aud(120, .3) }, { name: '數位經營', data: _aud(64, .4) }, { name: '系統管理員', data: _aud(12, .5) }],
  modules: [{ k: '事件分析', v: 3120 }, { k: '用戶列表', v: 2210 }, { k: 'AI 洞察', v: 1480 }, { k: '漏斗分析', v: 920 }, { k: '標籤與分群', v: 610 }, { k: '行為路徑', v: 340 }, { k: '歸因分析', v: 210 }],
  topProfile: [['林雅婷', '數據分析', 41, '漏斗流失抽樣（×30）'], ['李佳穎', '數據分析', 29, '客訴調查'], ['陳柏宇', '數位經營', 23, '活動名單抽查'], ['周美玲', '數位經營', 9, '流失名單確認'], ['張敏慧', '系統管理員', 7, 'DSAR 處理']],
  anomalies: [['今天 02:14', '非上班時間細查', '李佳穎 · 3 次', 'warn'], ['今天 11:52', '單人高頻細查', '林雅婷 · 1 小時 12 次（門檻 10）', 'warn'], ['昨天 16:30', '匯出超過門檻', '林雅婷 · 92,300 列（已審批）', 'ok'], ['昨天 09:05', '權限拒絕突增', '行銷嘗試用戶細查 ×14', 'bad']],
  agent: [{ k: '工具呼叫（日）', v: 1860 }, { k: '敏感工具（日）', v: 12 }, { k: '提議數（週）', v: 7 }, { k: '採納率', v: 43 }],
  denied: _aud(27, .6),
  exports: _aud(6, .8),
};

/* ================================================================== v0.3.2：受眾推送（Webhook 名單推送到內部系統） */
export const DESTINATIONS = [
  { id: 'd_crm', name: 'CRM 客戶系統', type: 'webhook', cn: 'Webhook（內部）', target: 'https://crm.internal/dmp/webhook', ident: 'customer_code', tags: ['lifecycle', 'fee_tier', 'churn_score_bucket'], status: 'active', ok7d: .998, p95: '420 ms', last: '今天 03:02', note: 'HMAC 簽章、IP 白名單' },
  { id: 'd_ma', name: '行銷自動化 MA', type: 'webhook', cn: 'Webhook（內部）', target: 'https://ma.internal/hooks/dmp', ident: 'customer_code', tags: ['lifecycle', 'pref_market'], status: 'active', ok7d: 1, p95: '180 ms', last: '今天 03:05', note: 'mTLS' },
  { id: 'd_kafka', name: '數據平台 Kafka', type: 'kafka', cn: 'Kafka', target: 'dmp.audience_updates / dmp.tag_updates', ident: 'customer_code + one_id', tags: ['*'], status: 'active', ok7d: 1, p95: '—', last: '即時', note: 'Avro' },
  { id: 'd_gcs', name: '數倉 Parquet', type: 'gcs', cn: 'GCS Parquet', target: 'gs://dmp-sync/audiences/', ident: 'customer_code', tags: ['*'], status: 'active', ok7d: 1, p95: '—', last: '今天 03:10', note: 'manifest + checksum' },
];
export const AUDIENCE_PUSHES = [
  { seg: 'sg2', dest: ['d_ma', 'd_crm'], mode: 'delta', schedule: '日批後 03:00', approved: true },
  { seg: 'sg5', dest: ['d_gcs'], mode: 'delta', schedule: '日批後 03:00', approved: true },
  { seg: 'sg6', dest: ['d_ma'], mode: 'delta', schedule: '即時', approved: true },
  { seg: 'sg3', dest: ['d_kafka'], mode: 'realtime', schedule: '即時', approved: true },
];
export const DELIVERIES = [
  { id: 'dlv_01J9…a3', seg: '新戶未入金（7 天）', dest: '行銷自動化 MA', ver: 118, mode: 'delta', n: 612, batches: '1/1', status: 'ok', at: '今天 03:05', resp: '200 · 180 ms' },
  { id: 'dlv_01J9…a2', seg: '新戶未入金（7 天）', dest: 'CRM 客戶系統', ver: 118, mode: 'delta', n: 612, batches: '1/1', status: 'ok', at: '今天 03:02', resp: '200 · 410 ms' },
  { id: 'dlv_01J9…9e', seg: '定期定額活躍客', dest: '數倉 Parquet', ver: 44, mode: 'full', n: 92_300, batches: '19/19', status: 'ok', at: '今天 03:10', resp: 'manifest 已寫入' },
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

/* ------------------------------------------------------------------ v0.5：元事件／屬性字典／用戶表／Item 表／資料流與效能／使用者指派 */
export const META_PROPS = [
  // 全域屬性字典（事件屬性 + 預置屬性）：型別、敏感等級、來源、枚舉、被幾個事件使用
  { k: '$page_url', cn: '頁面網址', type: 'string', scope: 'event', sens: 'L1', src: 'GA360', usedBy: 2, note: '去除查詢字串後入倉', status: 'live' },
  { k: 'page_group', cn: '頁面群組', type: 'string', scope: 'event', sens: 'L1', src: '事件化規則', enum: ['account_open', 'quote', 'trade', 'wealth', 'campaign'], usedBy: 3, status: 'live' },
  { k: '$screen_name', cn: '畫面名稱', type: 'string', scope: 'event', sens: 'L1', src: 'GA360', usedBy: 1, status: 'live' },
  { k: 'symbol', cn: '商品代碼', type: 'string', scope: 'event', sens: 'L2', src: '交易 CDC / GA360', usedBy: 6, note: '關聯維度表 dim_instrument', item: 'dim_instrument', status: 'live' },
  { k: 'market', cn: '市場', type: 'string', scope: 'event', sens: 'L1', src: '交易 CDC', enum: ['TSE', 'OTC', 'US', 'HK', 'FUND'], usedBy: 4, status: 'live' },
  { k: 'side', cn: '買賣別', type: 'string', scope: 'event', sens: 'L1', src: '交易 CDC', enum: ['buy', 'sell'], usedBy: 2, status: 'live' },
  { k: 'order_type', cn: '委託類型', type: 'string', scope: 'event', sens: 'L1', src: '交易 CDC', enum: ['limit', 'market', 'odd_lot', 'day_trade'], usedBy: 1, status: 'live' },
  { k: 'amount', cn: '金額', type: 'number', unit: 'TWD', scope: 'event', sens: 'L2', src: '交易／帳務 CDC', usedBy: 4, note: '匯出一律區間化', status: 'live' },
  { k: 'qty', cn: '數量', type: 'number', unit: '股', scope: 'event', sens: 'L2', src: '交易 CDC', usedBy: 2, status: 'live' },
  { k: 'fee', cn: '手續費', type: 'number', unit: 'TWD', scope: 'event', sens: 'L2', src: '交易 CDC', usedBy: 1, status: 'live' },
  { k: 'reason_code', cn: '失敗原因碼', type: 'string', scope: 'event', sens: 'L1', src: '交易 CDC', enum: ['E001', 'E012', 'E020', 'E031', 'SYS02'], usedBy: 1, note: '維度字典：42 個代碼 → 說明', item: 'dict:reason_code', status: 'live' },
  { k: 'channel', cn: '渠道', type: 'string', scope: 'event', sens: 'L1', src: '多來源', enum: ['app', 'web', 'agent', 'ivr', 'api'], usedBy: 7, status: 'live' },
  { k: 'branch_id', cn: '分公司', type: 'string', scope: 'event', sens: 'L1', src: '開戶系統 / CRM', usedBy: 2, note: '行級權限鍵；關聯維度表 dim_branch', item: 'dim_branch', status: 'live' },
  { k: 'agent_id', cn: '營業員', type: 'string', scope: 'event', sens: 'L3', src: 'CRM', usedBy: 1, note: '客戶歸屬維度（營業員不是本系統使用者）；代碼化；關聯維度表 dim_agent', item: 'dim_agent', status: 'live' },
  { k: 'kyc_level', cn: 'KYC 等級', type: 'string', scope: 'event', sens: 'L2', src: '開戶系統', enum: ['L1', 'L2', 'L3'], usedBy: 1, status: 'live' },
  { k: 'duration_ms', cn: '停留時長', type: 'number', unit: 'ms', scope: 'event', sens: 'L1', src: 'GA360', usedBy: 1, status: 'live' },
  { k: 'campaign_id', cn: '活動 ID', type: 'string', scope: 'event', sens: 'L1', src: 'MA / GA360', usedBy: 2, item: 'dim_campaign', status: 'live' },
  { k: 'exp_id', cn: '實驗 ID', type: 'string', scope: 'event', sens: 'L1', src: 'GA360', usedBy: 1, status: 'quarantine', note: '隨 ga.ab_exposure 進隔離區，待登記' },
  { k: 'memo', cn: '備註', type: 'string', scope: 'event', sens: 'L4', src: 'CRM', usedBy: 0, status: 'blocked', note: '個資掃描命中手機號：拒絕入倉' },
];
export const USER_PROPS = [
  // 用戶表（profile）屬性：來源、更新頻率、敏感等級、可用於篩選／分組、覆蓋率
  { k: 'one_id', cn: 'OneID', type: 'string', src: 'OneID 服務', upd: '即時', sens: 'L3', filter: false, group: false, cover: 100, note: '主鍵；代碼化，可 JOIN 不可反推' },
  { k: 'customer_code', cn: '客戶代碼', type: 'string', src: 'CRM（CDC）', upd: '即時', sens: 'L3', filter: true, group: false, cover: 87.4 },
  { k: 'lifecycle', cn: '生命週期', type: 'enum', src: '標籤引擎（規則）', upd: '每日 06:00', sens: 'L1', filter: true, group: true, cover: 100, enum: ['新客', '成長', '成熟', '沉睡', '流失'] },
  { k: 'tier', cn: '價值分級', type: 'enum', src: '標籤引擎（SQL）', upd: '每週一', sens: 'L2', filter: true, group: true, cover: 62.3, enum: ['P1', 'P2', 'P3', 'P4', 'P5'] },
  { k: 'age_band', cn: '年齡層', type: 'enum', src: 'CRM（CDC，出生年 → 分桶）', upd: '每日', sens: 'L2', filter: true, group: true, cover: 86.9, note: '只存分桶，不存生日' },
  { k: 'gender', cn: '性別', type: 'enum', src: 'CRM（CDC）', upd: '每日', sens: 'L2', filter: true, group: true, cover: 86.9 },
  { k: 'branch_id', cn: '所屬分公司', type: 'string', src: 'CRM（CDC）', upd: '每日', sens: 'L1', filter: true, group: true, cover: 87.4, note: '行級權限鍵（當前歸屬）' },
  { k: 'agent_id', cn: '所屬營業員', type: 'string', src: 'CRM（CDC）', upd: '每日', sens: 'L3', filter: true, group: false, cover: 85.1, note: '客戶歸屬維度；代碼化' },
  { k: 'first_utm_source', cn: '首次來源', type: 'string', src: '事件推導（首次 $pageview）', upd: '即時（set_once）', sens: 'L1', filter: true, group: true, cover: 71.2 },
  { k: 'signup_date', cn: '開戶日期', type: 'date', src: '開戶系統', upd: '即時', sens: 'L1', filter: true, group: false, cover: 87.4 },
  { k: 'account_age_days', cn: '開戶天數', type: 'number', src: '計算（today − signup_date）', upd: '每日', sens: 'L1', filter: true, group: true, cover: 87.4, note: '數值自動分桶' },
  { k: 'aum_bucket', cn: '資產區間', type: 'enum', src: '帳務（日批，分桶）', upd: '每日 05:00', sens: 'L2', filter: true, group: true, cover: 80.5, note: '只存區間，不存金額' },
  { k: 'has_margin', cn: '信用戶', type: 'bool', src: 'CRM（CDC）', upd: '每日', sens: 'L2', filter: true, group: true, cover: 87.4 },
  { k: 'churn_score', cn: '流失風險分', type: 'number', src: '模型（標籤引擎）', upd: '每日 07:30', sens: 'L2', filter: true, group: true, cover: 100 },
  { k: 'pref_market', cn: '偏好市場', type: 'enum', src: '標籤引擎（統計）', upd: '每日 06:00', sens: 'L1', filter: true, group: true, cover: 72.3 },
  { k: 'last_active_at', cn: '最近活躍', type: 'datetime', src: '事件推導', upd: '即時', sens: 'L1', filter: true, group: false, cover: 100 },
  { k: 'name / id_no / mobile / email', cn: '姓名／身分證／手機／Email', type: '—', src: '來源系統', upd: '—', sens: 'L4', filter: false, group: false, cover: 0, note: '永不入倉；OneID 只存 HMAC 雜湊' },
];
export const ONEID_RULES = [
  // 對齊神策 IDM 3.0：歸戶在接入時決定、一個 ID 只屬一個用戶、不自動合併已存在的用戶、資料歸優先級最高的 ID；未登入事件以 distinct_id 歸匿名用戶，登入時近 7 天匿名事件改寫到客戶（docs/19）
  { id: '$identity_login_id', cn: '客戶代碼（HMAC）', strength: 'strong', priority: 1, card: '單值', rebind: '不可改、不可解綁', src: 'CRM / App 後台 / GA user_id', note: '唯一錨點；GA user_id 接入時視同此 ID（同一把 HMAC 金鑰，docs/20）' },
  { id: '$identity_mobile_hash', cn: '手機（HMAC）', strength: 'strong', priority: 2, card: '單值', rebind: '人工', src: 'CRM / 開戶', note: '同手機被第二個客戶帶入 → 不改綁、記 conflict 待人工；換號由 CRM 發 unbind+bind' },
  { id: '$identity_account_no', cn: '交易帳號（FPE）', strength: 'strong', priority: 3, card: '多值', rebind: '人工', src: 'CRM 綁定 / 交易 CDC', note: '一客多戶；交易事件只帶帳號且未綁定 → 先建帳號用戶，CRM 綁定到達後升級為客戶' },
  { id: '$identity_device_id', cn: '裝置 ID（IDFV／GAID）', strength: 'weak', priority: 4, card: '多值', rebind: '最新者取得', src: 'GA360 app_instance_id', note: '未登入事件的 distinct_id（App）；共用裝置：最新登入者取得綁定；登出時 App 重置 app_instance_id' },
  { id: '$identity_cookie_id', cn: 'GA client_id', strength: 'weak', priority: 5, card: '多值', rebind: '最新者取得', src: 'GA360', note: '未登入事件的 distinct_id（Web）；登入前事件先歸匿名用戶，登入事件到達時近 7 天匿名事件改寫到登入客戶' },
  { id: '$identity_anonymous_id', cn: '匿名 ID', strength: 'weak', priority: 6, card: '多值', rebind: '最新者取得', src: 'Collector', note: '神策相容 SDK 的 distinct_id；兜底' },
  { id: '$identity_line_uid', cn: 'LINE UID', strength: 'channel', priority: 9, card: '多值', rebind: '—', src: 'MA', note: '觸達渠道，不參與歸戶，只做輸出對應' },
];
export const ONEID_CASES = [
  ['S1 匿名瀏覽 → 首次登入', 'cookie C1（distinct_id）的匿名用戶 U1；登入事件 {C1, cust_A}（A 尚無用戶）', 'U1 升級為客戶 A；之前的匿名事件自動屬於 A（同一個 one_id，不需改寫事件）'],
  ['S2 既有客戶在新裝置', '新裝置 cookie C2 先成匿名用戶 U2（9 天前、3 天前、1 天前各有事件）；登入事件 {C2, cust_A}', '事件歸 A；C2 改綁到 A；近 7 天的 2 筆匿名事件改寫到 A（distinct_id 保留 C2）；9 天前那筆留在 U2'],
  ['S3 共用裝置換人登入', 'A 登入後 C2 上又有匿名事件（歸 A）；兩天後 {C2, cust_B}', '事件歸 B；C2 改綁到 B；已歸 A 的事件不改寫到 B；之後 C2 的匿名事件歸 B（最新綁定者）'],
  ['S4 交易事件只帶帳號', '{ACC9}（CRM 尚未送綁定）', '以帳號建立用戶；CRM 綁定 {cust_C, ACC9} 到達 → 該用戶升級為客戶 C，歷史交易自動歸戶'],
  ['S5 同手機兩個客戶', '{cust_D, M1}，M1 已屬 A', 'D 另建用戶、事件歸 D；手機不改綁（政策：人工）、記 conflict；不會把 A、D 合併'],
  ['S6 未同意', '{cust_A, C8} 但 analytics_storage 未同意', '事件歸 A（有強 ID）；C8 不綁定、C8 的匿名事件不改寫；C8 單獨出現的事件留在匿名用戶'],
  ['S7 解綁', 'unbind(cookie C7)', '允許；unbind(login_id) 一律拒絕；人工合併／拆分由資訊管理發起、系統管理員審批，並改寫受影響事件'],
];
// 維度表（對齊神策「元數據管理 › 維度表」）：外部表以「事件／用戶屬性 = 維度表主鍵」關聯，欄位成為可篩選／分組的關聯屬性；不是獨立主體，事件永遠屬於用戶
export const DIM_TABLES = [
  { id: 'dim_instrument', cn: '商品主檔', pk: 'symbol', join: 'events.symbol = dim_instrument.symbol', itemType: 'instrument', rows: 24_310, src: '數據同步任務 sync_instrument（商品主檔 PG，日批 05:30）', sync: '每日 05:30', status: 'ok', cols: [['name', '商品名稱', 'string', 'L0', true], ['market', '市場', 'enum', 'L0', true], ['industry', '產業', 'string', 'L0', true], ['type', '類型', 'enum', 'L0', true], ['is_etf', 'ETF', 'bool', 'L0', true], ['lot_size', '交易單位', 'number', 'L0', false]], usedBy: ['事件分析：成交 × 產業', '標籤：pref_market', '歸因：ETF 專區'], note: '補充交易／看盤事件沒有埋的商品維度（產業、類型）；行數 < 100 萬' },
  { id: 'dim_account', cn: '帳戶主檔', pk: 'account_no', join: 'events.account_no = dim_account.account_no', itemType: 'account', rows: 1_812_440, src: '數據同步任務 sync_account（交易主機 CDC，upsert）', sync: '即時（CDC）', status: 'warn', cols: [['account_type', '帳戶類型', 'enum', 'L1', true], ['branch_id', '分公司', 'string', 'L1', true], ['agent_id', '營業員', 'string', 'L3', true], ['opened_at', '開戶日', 'date', 'L1', false], ['status', '狀態', 'enum', 'L1', true], ['margin_enabled', '信用', 'bool', 'L2', true]], usedBy: ['事件分析：成交 × 帳戶類型', '用戶列表：按分公司'], note: '帳戶不是獨立主體：account_no 是客戶的多值 ID（docs/19），這張表只補帳戶的維度；超過 100 萬列，查詢以 dictGet 而非 JOIN' },
  { id: 'dim_branch', cn: '分公司', pk: 'branch_id', join: 'events.branch_id = dim_branch.branch_id；users.branch_id = dim_branch.branch_id', itemType: 'branch', rows: 46, src: '數據同步任務 sync_org（HR 組織樹，日批）', sync: '每日', status: 'ok', cols: [['name', '名稱', 'string', 'L0', true], ['region', '區域', 'enum', 'L0', true], ['manager_id', '主管', 'string', 'L3', false]], usedBy: ['事件分析：按區域分組', '維度字典 branch_id'], note: '' },
  { id: 'dim_agent', cn: '營業員', pk: 'agent_id', join: 'users.agent_id = dim_agent.agent_id', itemType: 'agent', rows: 1_240, src: '數據同步任務 sync_org', sync: '每日', status: 'ok', cols: [['branch_id', '分公司', 'string', 'L1', true], ['team', '組別', 'string', 'L1', true], ['level', '職級', 'enum', 'L2', true], ['active', '在職', 'bool', 'L1', true]], usedBy: ['用戶列表：按營業員組別'], note: '' },
  { id: 'dim_campaign', cn: '行銷活動', pk: 'campaign_id', join: 'events.campaign_id = dim_campaign.campaign_id；events.utm_campaign = dim_campaign.utm_name', itemType: 'campaign', rows: 318, src: '數據同步任務 sync_campaign（MA API，每小時）', sync: '每小時', status: 'ok', cols: [['name', '活動名稱', 'string', 'L0', true], ['channel', '渠道', 'enum', 'L0', true], ['start_at', '開始', 'date', 'L0', false], ['end_at', '結束', 'date', 'L0', false], ['budget_bucket', '預算區間', 'enum', 'L2', true], ['owner', '負責人', 'string', 'L1', false]], usedBy: ['歸因：觸點命名', '活動復盤'], note: '' },
];
// 維度字典（對齊神策「維度字典」）：屬性值 → 顯示名（1:1 或 N:1），篩選與分組時以顯示名呈現；只限文字／數值屬性
export const DIM_DICTS = [
  { prop: 'reason_code', cn: '失敗原因碼', n: 42, mode: 'N:1', upd: '10-12', sample: [['E001', '餘額不足'], ['E012', '漲跌停'], ['E020', '零股限制'], ['SYS02', '委託閘道逾時'], ['SYS03', '委託閘道逾時']], src: '交易系統文件（手動上傳）' },
  { prop: 'market', cn: '市場', n: 5, mode: '1:1', upd: '09-01', sample: [['TSE', '上市'], ['OTC', '上櫃'], ['US', '美股'], ['HK', '港股'], ['FUND', '基金']], src: '手動' },
  { prop: 'channel', cn: '渠道', n: 5, mode: '1:1', upd: '09-01', sample: [['app', 'App'], ['web', '官網'], ['agent', '營業員'], ['ivr', '語音'], ['api', 'API']], src: '手動' },
  { prop: 'branch_id', cn: '分公司代碼', n: 46, mode: '1:1', upd: '每日（由 dim_branch 產生）', sample: [['TPE01', '台北'], ['TPE02', '信義'], ['TCH01', '台中'], ['KHH01', '高雄']], src: '維度表 dim_branch.name' },
  { prop: 'kyc_level', cn: 'KYC 等級', n: 3, mode: '1:1', upd: '09-01', sample: [['L1', '基本'], ['L2', '進階'], ['L3', '完整']], src: '手動' },
];
// 虛擬屬性（對齊神策「虛擬屬性」）：以表達式組合事件／用戶屬性與維度表欄位，查詢時計算，不落地
export const VIRTUAL_PROPS = [
  { name: 'v.symbol_industry', cn: '成交商品產業', scope: 'event', expr: "dim_instrument.industry WHERE events.symbol", uses: ['dim_instrument'], note: '事件分析按產業分組' },
  { name: 'v.amount_bucket', cn: '金額區間', scope: 'event', expr: "CASE WHEN amount < 1e5 THEN '<10萬' WHEN amount < 5e5 THEN '10–50萬' WHEN amount < 1e6 THEN '50–100萬' ELSE '≥100萬' END", uses: [], note: 'L2 數值對外一律用此區間' },
  { name: 'v.is_office_hour', cn: '盤中', scope: 'event', expr: "toHour(time) BETWEEN 9 AND 13", uses: [], note: '' },
  { name: 'v.account_age_band', cn: '開戶年資', scope: 'user', expr: "multiIf(account_age_days < 30, '新戶', account_age_days < 365, '1 年內', '1 年以上')", uses: [], note: '' },
];
// 數據源的集成與管理（對齊神策「通用數據接入」：數據源管理 › 數據表管理 › 數據同步）
export const EXT_TABLES = [
  { conn: 'trade_core', table: 'dbo.ORDER_HIST', pk: 'ORD_NO', incr: 'LSN（CDC）', cols: 14, schemaVer: 3, drift: 0, tasks: ['sync_orders_events'] },
  { conn: 'trade_core', table: 'dbo.ACCOUNT', pk: 'ACCT_NO', incr: 'LSN（CDC）', cols: 9, schemaVer: 2, drift: 0, tasks: ['sync_account', 'sync_account_binding'] },
  { conn: 'ga_bq', table: 'analytics_3012.events_*', pk: '—（append）', incr: 'event_date（分片表）', cols: 58, schemaVer: 1, drift: 0, tasks: ['sync_ga360_daily', 'sync_ga360_intraday'] },
  { conn: 'crm_pg', table: 'public.customer', pk: 'cust_id', incr: 'updated_at（pgoutput）', cols: 22, schemaVer: 5, drift: 0, tasks: ['sync_customer_users'] },
  { conn: 'fund_pg', table: 'public.fund_order', pk: 'order_id', incr: 'updated_at', cols: 12, schemaVer: 2, drift: 1, tasks: ['sync_fund_events'], note: '新增欄位 PLAN_FREQ 未映射' },
  { conn: 'crm_pg', table: 'public.instrument_master', pk: 'symbol', incr: '全量', cols: 11, schemaVer: 1, drift: 0, tasks: ['sync_instrument'] },
];
export const SYNC_TASKS = [
  { id: 'sync_ga360_daily', cn: 'GA360 日批 → 事件表', src: 'ga_bq · events_YYYYMMDD', target: 'events（只增量）', mode: '增量（分片表到齊觸發）', sched: '05:10 到齊後', last: '05:14 → 05:52', rows: 182_430_112, status: 'ok', note: 'event_id 去重；覆蓋串流版本' },
  { id: 'sync_ga360_intraday', cn: 'GA360 串流 → 事件表', src: 'ga_bq · events_intraday_*', target: 'events（只增量）', mode: '流任務（每 5 分鐘）', sched: '持續', last: '14:35', rows: 41_210_540, status: 'ok', note: '分鐘級；日批到齊後被覆蓋' },
  { id: 'sync_orders_events', cn: '委託表 → 委託／成交事件', src: 'trade_core · ORDER_HIST（CDC）', target: 'events（狀態機事件化）', mode: '流任務（Debezium）', sched: '持續', last: '14:36', rows: 18_240_902, status: 'ok', note: 'N→bond_order、F→bond_deal…' },
  { id: 'sync_customer_users', cn: '客戶主檔 → 用戶表', src: 'crm_pg · customer（CDC + 日批）', target: 'users（upsert）', mode: '增量 + 每日全量校正', sched: '持續 / 02:00', last: '14:35 / 02:00', rows: 1_230_119, status: 'ok', note: '映射到 16 個用戶屬性；L4 欄位不選' },
  { id: 'sync_account_binding', cn: '帳戶主檔 → OneID 綁定', src: 'trade_core · ACCOUNT', target: 'OneID bind（login_id, account_no）', mode: '流任務 + 每日全量', sched: '持續 / 02:30', last: '14:36', rows: 1_812_440, status: 'ok', note: '等同一條共現資料（docs/19）' },
  { id: 'sync_account', cn: '帳戶主檔 → 維度表 dim_account', src: 'trade_core · ACCOUNT', target: 'dim_account（upsert by account_no）', mode: '流任務', sched: '持續', last: '14:36', rows: 1_812_440, status: 'ok', note: '' },
  { id: 'sync_instrument', cn: '商品主檔 → 維度表 dim_instrument', src: 'crm_pg · instrument_master', target: 'dim_instrument（全量覆蓋）', mode: '全量', sched: '05:30', last: '05:30 → 05:31', rows: 24_310, status: 'ok', note: '' },
  { id: 'sync_fund_events', cn: '基金申購 → 事件', src: 'fund_pg · fund_order（CDC）', target: 'events', mode: '流任務', sched: '持續', last: '14:30', rows: 41_000, status: 'warn', note: 'Schema drift：來源新增 PLAN_FREQ，38 筆 plan_type 空值進隔離' },
  { id: 'sync_sensors_history', cn: '神策歷史事件（一次性）', src: 'sensors · 查詢 API → Parquet', target: 'events', mode: '全量（一次性）', sched: '已完成 10/02', last: '10/02 18:40', rows: 4_870_000_000, status: 'done', note: '對帳差異 0.4%；token 已撤銷' },
];
export const PIPELINE = {
  stages: [
    { id: 'src', cn: '來源', items: ['GA360 BigQuery 日批', 'GA360 串流匯出（intraday）', '交易 CDC', '帳務 CDC', 'CRM CDC', 'MA'], tput: '2.1 億/日', lag: '—', status: 'ok' },
    { id: 'ingest', cn: 'Collector / KafkaConnect', items: ['dmp-ingest ×2', 'Debezium ×1'], tput: '7,900 ev/s 尖峰', lag: '28 ms P95', status: 'ok' },
    { id: 'kafka', cn: 'Kafka raw.*', items: ['3 broker · 12 分區', '7 天保留 · 0.26 TB'], tput: '2.4 MB/s', lag: '消費延遲 1.2 s', status: 'ok' },
    { id: 'flink', cn: 'Flink 事件化', items: ['normalize → eventize', 'OneID 關聯 · session', 'checkpoint 30 s'], tput: '7,900 ev/s', lag: '2.1 s P95', status: 'ok' },
    { id: 'ch', cn: 'ClickHouse', items: ['events 2 副本 · 3.9 TB/副本', 'MV L1–L3 · Bitmap 標籤', '查詢 P95 3.2 s'], tput: '寫入 7,900 列/s', lag: '可查 < 5 s', status: 'ok' },
    { id: 'serve', cn: '服務', items: ['dmp-core api ×2', 'Tag Sync / 推送', 'Agent ×2'], tput: '5 查詢/s 高峰', lag: 'API P95 420 ms', status: 'ok' },
    { id: 'out', cn: '下游', items: ['Kafka dmp.tag_updates', 'Webhook 名單推送（MA／CRM）', 'GCS Parquet（數倉）'], tput: '0.6 億變更/日', lag: '15 分鐘送完', status: 'warn', note: 'MA Webhook 昨日重試 2 次（DLQ 0）' },
  ],
  quality: [['遲到事件（> 7 天）', '0.08%', 'ok'], ['隔離率', '0.02%', 'ok'], ['GA360 對帳差異', '0.3%', 'ok'], ['OneID 打通率', '87.4%', 'ok'], ['Schema drift（待處理）', '1', 'warn'], ['重複事件（event_id 去重）', '0.9%', 'ok']],
};
export const QUERY_PERF = [
  { mod: '事件分析', p50: 0.4, p95: 1.8, p99: 3.9, scan: '6 GB', cache: 71, path: 'MV L1 日彙總；分組 ≤ 3 維走 L2', sla: 3, n24h: 1_842 },
  { mod: '漏斗分析', p50: 1.1, p95: 3.2, p99: 6.8, scan: '21 GB', cache: 38, path: 'windowFunnel on L0（30 天）；one_id_uint 稠密', sla: 5, n24h: 611 },
  { mod: '留存分析', p50: 0.7, p95: 2.4, p99: 4.1, scan: '9 GB', cache: 55, path: 'retention() on L0；cohort 預聚合 L3', sla: 5, n24h: 240 },
  { mod: '行為路徑', p50: 0.9, p95: 4.2, p99: 9.5, scan: '12 GB（SAMPLE 1/10）', cache: 20, path: 'session 序列 arrayJoin；全量 ×8 耗時', sla: 8, n24h: 128 },
  { mod: '歸因分析', p50: 0.8, p95: 1.4, p99: 2.6, scan: '1.1 GB', cache: 64, path: '觸點表（事件表 5–10%）五模型同掃', sla: 5, n24h: 96 },
  { mod: '用戶列表（估數）', p50: 0.2, p95: 0.3, p99: 0.6, scan: '140 MB', cache: 88, path: 'Bitmap 交並差（RoaringBitmap）', sla: 1, n24h: 2_210 },
  { mod: '用戶列表（分頁）', p50: 0.5, p95: 0.9, p99: 1.6, scan: '380 MB', cache: 40, path: 'Bitmap → users 點查', sla: 2, n24h: 1_470 },
  { mod: '用戶細查', p50: 0.1, p95: 0.3, p99: 0.5, scan: '12 MB', cache: 10, path: 'one_id 主鍵 + bloom index', sla: 1, n24h: 312 },
  { mod: 'AI 工具（metric.query 等）', p50: 0.6, p95: 2.1, p99: 4.4, scan: '4 GB', cache: 60, path: '同一編譯器；預算 20 GB/呼叫', sla: 5, n24h: 1_120 },
];
export const BATCH_JOBS = [
  { name: '標籤全量日批（500 個）', sched: '02:00', last: '02:00 → 02:19', dur: '19 分', status: 'ok', note: 'dbt 8 併行；Bitmap 版本 7 份' },
  { name: 'OneID 日批校正（cookie → one_id 回填）', sched: '03:00', last: '03:00 → 03:12', dur: '12 分', status: 'ok', note: '回填 41 萬筆登入前事件' },
  { name: 'GA360 日批匯入（前日）', sched: '05:10 到齊後', last: '05:14 → 05:52', dur: '38 分', status: 'ok', note: '1.82 億事件 · 對帳 0.3%' },
  { name: '觸點表重算（歸因）', sched: '04:00', last: '04:00 → 04:06', dur: '6 分', status: 'ok', note: '14 天窗口' },
  { name: 'MV L2/L3 補算（遲到資料）', sched: '每小時', last: '14:00 → 14:02', dur: '2 分', status: 'ok', note: '' },
  { name: 'Tag Sync 增量推送', sched: '日批後', last: '02:20 → 02:34', dur: '14 分', status: 'warn', note: 'MA Webhook 429 重試 2 次' },
  { name: '審計歸檔（WORM）', sched: '01:00', last: '01:00 → 01:03', dur: '3 分', status: 'ok', note: '' },
];
export const STORAGE = [['ClickHouse（2 副本）', 7.9, 8.0, 'TB'], ['Kafka（7 天）', 0.26, 1.5, 'TB'], ['GCS 原始 + Iceberg', 5.4, 8.0, 'TB'], ['PostgreSQL（控制面）', 38, 200, 'GB'], ['Valkey', 2.9, 8, 'GB']];
export const USER_ASSIGNMENTS = [
  { user: '張敏慧', email: 'admin@…', roles: ['admin'], scope: '全域', expiry: '—', sso: 'AD: DMP-Admins', last: '今天 09:12' },
  { user: '林雅婷', email: 'analyst@…', roles: ['data_analyst'], scope: '全域', expiry: '—', sso: 'AD: DMP-Analysts', last: '今天 14:20' },
  { user: '黃思妤', email: 'mkt@…', roles: ['marketing'], scope: '全域', expiry: '—', sso: 'AD: DMP-Marketing', last: '今天 11:35' },
  { user: '陳柏宇', email: 'ops@…', roles: ['digital_ops', 'marketing'], scope: '全域', expiry: '—', sso: 'AD: DMP-DigitalOps', last: '今天 13:58' },
  { user: '吳建宏', email: 'it@…', roles: ['it_admin'], scope: '全域（不含個人）', expiry: '—', sso: 'AD: DMP-IT', last: '今天 08:30' },
  { user: '外包 顧問 A', email: 'vendor-a@…', roles: ['custom_vendor_analyst'], scope: '專案 sinopac_demo · 回溯 180 天', expiry: '2026-10-31', sso: '本地帳號（MFA）', last: '10-12 16:02', temp: true },
  { user: '陳柏宇（臨時提權）', email: 'ops@…', roles: ['admin'], scope: '全域', expiry: '今天 18:00（4 小時）', sso: 'break-glass ap_1038', last: '今天 14:01', temp: true },
];
export const VIRTUAL_EVENTS = [
  { name: 'v.any_trade', cn: '任一交易行為', def: 'bond_order OR bond_deal OR sip_purchase', owner: '數據分析', usedBy: ['留存分析', '標籤：trade_style'] },
  { name: 'v.app_active', cn: 'App 活躍', def: '$AppViewScreen OR hq_stockdetail_view OR login（platform IN ios,android）', owner: 'App 產品', usedBy: ['事件分析 DAU', '留存分析'] },
  { name: 'v.open_funnel_entry', cn: '進入開戶流程', def: '$pageview（page_group=account_open）OR kh_entry_khpg_pv', owner: '數位開戶', usedBy: ['漏斗：開戶'] },
];
