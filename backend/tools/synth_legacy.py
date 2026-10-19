"""模擬資料：100 萬人 × 事件，依舊平台埋點規格（事件字典 events.yaml、用戶屬性分級 legacy_user_properties.csv）產生，全部在 ClickHouse 端以 numbers() 合成。

用途：示範站與單機環境要有「看起來像永豐」的量體與欄位，而不是黃金測試那 6 種事件。資料完全合成：
不讀任何客戶資料；個資欄（L4）、加密參數與維護欄位不產生；代碼化欄位（L3）只產生 v1_ 開頭的假代碼。

產生內容（project 預設 sinopac_demo，時間 2026-07-19 … 2026-10-16，台北時間）：
- dmp.users：固定欄位（生命週期、等級、AUM…）＋ props_extra 放用戶表其餘可遷移欄位（L1／L2，依名稱產生日期／數值／旗標／代碼值，部分為空）。
- dmp.events：字典裡 live 的事件依來源加權隨機產生，行為資料（預設 1,000 萬筆，kind≠trade）與交易資料（預設 500 萬筆，kind=trade）筆數各自指定；
  deprecated 事件只出現在前 30 天（歷史）。每筆帶該事件在字典裡的屬性。
  另注入有先後順序與流失的流程（看盤→委託→成交、數位開戶 kh_* 漏斗：引流→審核成功 15%、每月約 2 萬戶、豐存股申購、股票禮品卡、豐雲學堂報名、App 會話），讓漏斗與路徑有意義。
- 活躍度：用戶依序號冪次分布（少數人很活躍）；序號後 20% 的用戶只在前 30 天有事件（沉睡／流失）。

用法：CLICKHOUSE_URL=http://127.0.0.1:8123 CLICKHOUSE_PASSWORD=… python3 -m backend.tools.synth_legacy [--users 1000000] [--events 10000000] [--trades 5000000] [--project sinopac_demo]
     python3 -m backend.tools.synth_legacy --sql      # 只印 SQL 長度與事件權重摘要，不連 ClickHouse
     --compact：單機開檔數上限低時（屬性多、JSON 子欄位多）把 events／users 改成 compact part
可重跑：先刪同 project 的事件與同前綴（v1_d）的用戶再寫入。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
START_UTC = "2026-07-18 16:00:00"          # = 2026-07-19 00:00 台北
DAYS = 90
HIST_DAYS = 30                            # deprecated 事件與流失用戶只出現在前 30 天
SEED = 20261019
ID_PREFIX = "v1_d"                        # 用戶 one_id 前綴（看起來像代碼化客戶代碼；重跑時以此刪除）
# 熱門度：以冪次（2.4）偏斜到清單前段，台積電最多、VOO 最少
SYMBOLS = [("2330", "台積電"), ("2317", "鴻海"), ("0050", "元大台灣50"), ("2454", "聯發科"), ("00878", "國泰永續高股息"), ("2881", "富邦金"),
           ("2603", "長榮"), ("2308", "台達電"), ("AAPL", "Apple"), ("NVDA", "NVIDIA"), ("TSLA", "Tesla"), ("VOO", "Vanguard S&P 500")]
FLOWS = [   # (名稱, 步驟, 每步續行機率（一個數＝每步相同；清單＝逐步）, 步距分鐘, 起點數占用戶數比例[, "distinct"＝每個起點一個不同用戶])
    ("trade", ["hq_stockdetail_view", "bond_order", "bond_deal"], 0.45, 3, 1.2),
    # 數位開戶漏斗（依埋點規格 kh_* 事件）：引流（開戶首頁）→ 審核成功 15%；起點 40% 用戶 ≈ 90 天 40 萬人進漏斗 → 每月約 2 萬戶開戶成功。
    # 各步續行率參考券商／eKYC 供應商的公開經驗值：首頁到開始申請最大流失、證件上傳其次、送件後審核通過約九成；審核成功後批次寫回開戶完成、再啟用帳戶。
    ("open_account", ["kh_entry_khpg_pv", "kh_entry_khpg_click", "kh_otp_get", "kh_idphoto_upload", "kh_enter_profilepg", "kh_kyc", "kh_set_bank", "kh_sign", "kh_apply", "kh_review_pass", "OpenAccountDone", "EnableAct"],
     [0.58, 0.82, 0.76, 0.82, 0.88, 0.82, 0.88, 0.93, 0.86, 0.98, 0.85], 6, 0.4, "distinct"),   # 一人只進一次漏斗，整體轉化率才會是各步連乘
    ("open_account_legacy", ["ConfirmID", "openact_mobile", "openact_profile", "Credit", "Contract"], 0.72, 6, 0.01),   # 舊版開戶事件（已少量留存）
    ("sip", ["viewProduct", "Purchase", "PurchaseSend", "PurchaseComfirm", "PurchaseComplete"], 0.6, 2, 0.12),
    ("gift_card", ["buy_now", "begin_checkout", "add_payment_info", "gift_card_purchase", "gift_card_payment"], 0.62, 2, 0.03),
    ("richclub", ["richclub_lesson_page_view", "richclub_register_click", "richclub_order_confirm", "richclub_register_confirm"], 0.55, 2, 0.04),
    ("app_session", ["app_login_success", "$AppViewScreen", "hq_stockdetail_view", "$AppClick", "bond_order"], 0.75, 1, 0.5),   # 登入後的同一個會話（路徑分析）
]
PLATFORM = {"JavaScript": "Web", "iOS": "iOS", "Android": "Android"}
TRADE_CHANNEL = ("bond_order", "bond_deal")

# ---- 屬性值產生碼（SQL 端以 multiIf 展開；h = cityHash64(n, 屬性序號, SEED)）----
C_STR, C_AMT, C_QTY, C_PRICE, C_BOOL, C_DT, C_TOKEN, C_MARKET, C_SIDE, C_CCY, C_SYMBOL, C_BRANCH, C_BUTTON, C_PLATFORM, C_NUM, C_LIST, C_FEE, C_STOCKNAME, C_PRODUCT, C_POOL = range(20)
# 畫面上常看到的欄位給像樣的值（鍵一律小寫比對）；其餘字串欄位用「欄位名_序號」
POOLS = {
    "$page_url": ["https://www.sinotrade.com.tw/", "https://www.sinotrade.com.tw/openact", "https://www.sinotrade.com.tw/richclub/lesson", "https://www.sinotrade.com.tw/ssp/product",
                  "https://www.sinotrade.com.tw/giftcard", "https://www.sinotrade.com.tw/market/tw", "https://www.sinotrade.com.tw/news"],
    "$url": ["https://www.sinotrade.com.tw/", "https://www.sinotrade.com.tw/openact", "https://www.sinotrade.com.tw/ssp/product"],
    "$title": ["永豐金證券", "線上開戶", "豐雲學堂", "豐存股", "股票禮品卡", "台股行情", "投資新聞"],
    "$referrer": ["https://www.google.com/", "https://www.facebook.com/", "https://line.me/", "", "https://www.sinotrade.com.tw/"],
    "$screen_name": ["首頁", "行情", "個股報價", "下單", "帳務", "自選股", "設定", "通知中心", "閃電下單"],
    "$app_version": ["6.2.0", "6.1.3", "6.0.9", "5.9.2"], "$os": ["iOS", "Android"],
    "$utm_source": ["google", "facebook", "line", "edm", "yahoo"], "$utm_medium": ["cpc", "social", "email", "referral", "organic"],
    "$utm_campaign": ["2026q3_openact", "2026q4_ssp", "richclub_autumn", "giftcard_xmas", "app_push_oct"], "$utm_term": ["開戶", "定期定額", "美股", "ETF"], "$utm_content": ["banner_a", "banner_b", "text_1"],
    "page_group": ["首頁", "開戶", "學堂", "豐存股", "禮品卡", "行情", "新聞"], "auth_method": ["password", "biometric", "otp", "sns"],
    "logintype": ["證券客戶", "Google", "Facebook", "Apple", "LINE"], "order_type": ["ROD", "IOC", "FOK"], "trade_kind": ["現股", "融資", "融券", "當沖"],
    "time_in_force": ["ROD", "IOC"], "from_screen": ["自選股", "排行", "搜尋", "推播"], "lesson_title": ["ETF 入門", "美股開戶全攻略", "存股實戰", "技術分析基礎"],
    "$first_browser_language": ["zh-tw", "en-us", "zh-cn"], "$first_traffic_source_type": ["付費廣告流量", "自然搜尋流量", "社交網站流量", "引薦流量", "直接流量"],
    "$first_referrer_host": ["www.google.com", "www.facebook.com", "line.me", ""], "$first_browser_charset": ["UTF-8"],
    "$dimension_dict_cuaa1": ["Y", "N"], "$dimension_dict_cuaa2": ["Y", "N"], "$dimension_dict_cuaa3": ["Y", "N"], "$dimension_dict_cuaa4": ["Y", "N"], "$dimension_dict_cuaf9": ["B", ""],
    "$dimension_dict_s_sex": ["M", "F", "A"], "$dimension_dict_sleepycust_group": ["A", "B", "C", ""], "user_type": ["自然人", "法人"],
    "snslogintype": ["Google", "Facebook", "Apple", "LINE", ""], "latest_snslogintype": ["Google", "Facebook", "Apple", "LINE", ""],
    "first_trade_category": ["現貨", "複委託", "定期定額", "期貨", "基金"], "ref_flag": ["永豐銀", "保險", "信用卡", "內部單位", "無"], "group_tag": ["行銷_當沖客", "行銷_波段客", "行銷_存股族", "行銷_有庫存無交易", "行銷_其它"],
    "b27_status": ["已查詢", "查無資料"], "b27_default_status": ["無", "有"], "b27_default_description": ["無", "逾期未清償"],
}


def q(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def load_inputs() -> tuple[list[dict], dict[str, dict], dict[tuple[str, str], str], list[dict]]:
    events = yaml.safe_load((ROOT / "semantic" / "events.yaml").read_text(encoding="utf-8"))["events"]
    with (ROOT / "migration" / "legacy_properties.csv").open(encoding="utf-8-sig") as f:
        props = {r["property"]: r for r in csv.DictReader(f)}
    types: dict[tuple[str, str], str] = {}
    p = ROOT / "migration" / "legacy_plan_event_properties.csv"
    if p.exists():
        with p.open(encoding="utf-8-sig") as f:
            types = {(r["event"], r["property"]): r["type"] for r in csv.DictReader(f)}
    with (ROOT / "migration" / "legacy_user_properties.csv").open(encoding="utf-8-sig") as f:
        users = [r for r in csv.DictReader(f) if r["family"] == "core"]
    return events, props, types, users


def prop_code(name: str, typ: str, decision: str) -> int:
    n = name.lower()
    if decision == "hash":
        return C_TOKEN
    if n in POOLS and typ in ("", "STRING"): return C_POOL
    if re.search(r"fee|tax", n): return C_FEE
    if re.search(r"stockname|stock_name|item_name", n): return C_STOCKNAME
    if re.search(r"stockid|symbol|stock_id|stockno|^item_id$", n): return C_SYMBOL
    if re.search(r"price", n) and typ != "STRING": return C_PRICE
    if re.search(r"amount|amt|cost|money|total|_val$|value", n) and typ != "STRING": return C_AMT
    if re.search(r"qty|shares|unit|count|cnt|_num$", n) and typ != "STRING": return C_QTY
    if typ == "BOOL" or n.startswith("is_"): return C_BOOL
    if typ == "DATETIME" or re.search(r"(date|_dt|time)$", n): return C_DT
    if typ == "LIST": return C_LIST
    if typ == "NUMBER": return C_NUM
    if re.search(r"market", n): return C_MARKET
    if re.search(r"buyorsell|^side$|^bs$", n): return C_SIDE
    if re.search(r"currency|ccy", n): return C_CCY
    if re.search(r"broker|branch", n): return C_BRANCH
    if re.search(r"button", n): return C_BUTTON
    if n == "platform": return C_PLATFORM
    if re.search(r"productno|subproduct|^kind$|product", n): return C_PRODUCT
    if n in POOLS: return C_POOL
    return C_STR


def value_sql(code: str, key: str, idx: str) -> str:
    """JSON 文字值；n＝列號、t＝事件時間、AMT／SYM／PLAT 為同列共用值（屬性與欄位一致）。"""
    h = f"cityHash64(n, {idx}, {SEED})"
    pick = lambda arr: f"concat('\"', [{', '.join(q(x) for x in arr)}][1 + {h} % {len(arr)}], '\"')"
    return (f"multiIf({code} = {C_AMT}, toString(AMT), {code} = {C_FEE}, toString(round(AMT * 0.001425)), {code} = {C_QTY}, toString(({h} % 20 + 1) * if({h} % 4 = 0, 1000, 1)), "
            f"{code} = {C_PRICE}, toString(round(10 + ({h} % 99000) / 100, 2)), {code} = {C_BOOL}, if({h} % 2 = 0, 'true', 'false'), "
            f"{code} = {C_DT}, concat('\"', formatDateTime(t - toIntervalDay({h} % 365), '%Y-%m-%d %H:%i:%S', 'Asia/Taipei'), '\"'), "
            f"{code} = {C_TOKEN}, concat('\"v1_', lower(hex({h})), '\"'), {code} = {C_MARKET}, {pick(['TSE', 'OTC', 'US', 'HK', 'JP'])}, "
            f"{code} = {C_SIDE}, {pick(['B', 'S'])}, {code} = {C_CCY}, {pick(['TWD', 'USD', 'HKD', 'JPY'])}, {code} = {C_SYMBOL}, concat('\"', SYM, '\"'), "
            f"{code} = {C_STOCKNAME}, concat('\"', SYMNAME, '\"'), {code} = {C_BRANCH}, concat('\"B', leftPad(toString({h} % 40 + 1), 2, '0'), '\"'), "
            f"{code} = {C_BUTTON}, {pick(['立即申購', '下一步', '確認送出', '查看更多', '加入自選', '分享', '返回'])}, {code} = {C_PLATFORM}, concat('\"', PLAT, '\"'), "
            f"{code} = {C_NUM}, toString({h} % 100), {code} = {C_LIST}, concat('[\"', {key}, '_', toString({h} % 5 + 1), '\"]'), "
            f"{code} = {C_PRODUCT}, {pick(['ST', 'SUB', 'SIP', 'SFP', 'FU', 'OF', 'WF'])}, "
            f"{code} = {C_POOL}, toJSONString(POOLS[lower({key})][1 + {h} % length(POOLS[lower({key})])]), concat('\"', {key}, '_', toString({h} % 8 + 1), '\"'))")


def event_specs(events: list[dict], props: dict[str, dict], types: dict[tuple[str, str], str]) -> list[dict]:
    out = []
    for e in events:
        if e.get("status") not in ("live", "deprecated"):
            continue
        keys = []
        for k in list(e.get("required") or []) + list(e.get("optional") or []):
            r = props.get(k)
            dec = r["decision"] if r else "keep"
            if dec not in ("keep", "hash") or k in [x[0] for x in keys]:
                continue                                              # 個資、維護欄、加密參數不產生
            keys.append((k, prop_code(k, types.get((e["name"], k), ""), dec)))
        src = e.get("source", ""); grp = e.get("group", ""); name = e["name"]
        plats = [PLATFORM[p] for p in str(e.get("platforms", "")).split(",") if p.strip() in PLATFORM]
        if name in TRADE_CHANNEL:
            plats = ["iOS", "Android", "Web", "iOS", "Android", ""]   # 委託／成交雖由數據中台批次導入，仍帶下單管道（空白＝營業員）
        elif not src.startswith("GA360"):
            plats = [""]                                              # 批次／服務端／MA：沒有前端平台
        w = 2.0 if src.startswith("GA360") else 1.5 if src.startswith("MA") or src.startswith("LINE") else 1.0 if src == "業務系統" else 1.0
        w = {"$pageview": 60, "$AppViewScreen": 80, "$AppClick": 40, "$WebClick": 30, "$AppStart": 30, "$AppEnd": 25, "app_login_success": 25, "Login": 20,
             "hq_stockdetail_view": 40, "bond_order": 10, "bond_deal": 7, "search_submit": 6, "push_click": 4}.get(name, w)
        if name.startswith("dwt_"): w = 4
        if grp == "開戶" or name.startswith("etlOpenAccountDone"): w = 0.3
        if name.startswith("etlMatch_"): w = 2
        if e.get("status") == "deprecated": w = 0.3
        out.append({"name": name, "keys": keys, "plats": plats or ["Web"], "weight": w, "deprecated": e.get("status") == "deprecated", "kind": e.get("kind", "behavior"),
                    "entity": e.get("entity", "user"), "has_sym": name in ("bond_order", "bond_deal", "hq_stockdetail_view") or any(c in (C_SYMBOL, C_STOCKNAME) for _, c in keys),
                    "has_amt": any(c in (C_AMT, C_FEE) for _, c in keys)})
    return out


def _maps(specs: list[dict]) -> str:
    pm = ", ".join(f"{q(s['name'])}, [{', '.join(f'({q(k)}, {c})' for k, c in s['keys'])}]::Array(Tuple(String, UInt8))" for s in specs)
    pl = ", ".join(f"{q(s['name'])}, [{', '.join(q(p) for p in s['plats'])}]" for s in specs)
    flags = ", ".join(f"{q(s['name'])}, ({int(s['has_sym'])}, {int(s['has_amt'])}, {q(s['entity'])})" for s in specs)
    return (f"map({pm}) AS PM, map({pl}) AS PL, map({flags}) AS FL, "
            f"[{', '.join(q(s) for s, _ in SYMBOLS)}] AS SYMS, [{', '.join(q(n) for _, n in SYMBOLS)}] AS SYMNAMES, {pools_sql()} AS POOLS")


def pools_sql() -> str:
    return "map(" + ", ".join(f"{q(k)}, [{', '.join(q(x) for x in v)}]" for k, v in POOLS.items()) + ")"


def uid_sql(idx: str) -> str:
    return f"concat({q(ID_PREFIX)}, leftPad(lower(hex(cityHash64({idx}, {SEED}) % 1099511627776)), 10, '0'), leftPad(toString({idx}), 7, '0'))"


def _row_tail(project: str) -> str:
    """共用：由 event／uidx／t／n 推出其餘欄位與 props（SELECT 子句；需要 PM/PL/FL/SYMS 等 WITH 常數）。"""
    props = ("concat('{', arrayStringConcat(arrayMap((x, i) -> concat(toJSONString(x.1), ':', "
             + value_sql("x.2", "x.1", "i") + "), PM[event], arrayEnumerate(PM[event])), ','), '}')")
    return (f"SELECT {q(project)} AS project, t AS time, event, 'legacy' AS source, {uid_sql('uidx')} AS one_id, "
            f"if(FL[event].3 = 'account', 'account', 'user') AS entity_type, '' AS entity_id, one_id AS distinct_id, concat(one_id, '_', toString(toDate(t, 'Asia/Taipei'))) AS session_id, "
            f"concat('sd', toString(n)) AS event_id, PLAT AS platform, '' AS app_id, if(FL[event].1 = 1, SYM, '') AS symbol, "
            f"if(event IN ('bond_order', 'bond_deal') OR FL[event].1 = 1, ['B', 'S'][1 + cityHash64(n, 77) % 2], '') AS side, "
            f"multiIf(PLAT = 'Web', 'web', PLAT IN ('iOS', 'Android'), 'app', event IN {TRADE_CHANNEL}, 'agent', '') AS channel, if(FL[event].2 = 1, AMT, NULL) AS amount, "
            f"if(event IN ('$pageview', 'Login', 'app_login_success'), ['google', 'facebook', 'line', 'edm', '', '', ''][1 + cityHash64(n, 78) % 7], '') AS utm_source, '' AS utm_campaign, "
            f"{props}::JSON AS props, now64(3) AS ingest_time, 1 AS schema_version, 0 AS late, 'synth_legacy' AS batch_id")


def events_sql(specs: list[dict], project: str, n_users: int, offset: int, count: int, kinds: set[str] | None = None) -> str:
    """隨機散佈的事件；kinds 給了只從這些類別（behavior／business／marketing／trade）抽，讓行為資料與交易資料的筆數可各自指定。"""
    bag: list[str] = []
    for s in specs:
        if kinds and s["kind"] not in kinds:
            continue
        bag += [s["name"]] * max(1, round(s["weight"] * 10))
    live_bag = [x for x in bag if not next(sp for sp in specs if sp["name"] == x)["deprecated"]]
    hours = [8] + [9] * 6 + [10] * 7 + [11] * 6 + [12] * 5 + [13] * 6 + [14] * 3 + [15] * 2 + [16, 17, 18] + [19] * 2 + [20] * 3 + [21] * 3 + [22] * 2 + [23, 7]
    u = lambda k: f"(cityHash64(n, {k}, {SEED}) % 1000000 / 1000000.)"
    return (f"INSERT INTO dmp.events (project, time, event, source, one_id, entity_type, entity_id, distinct_id, session_id, event_id, platform, app_id, symbol, side, channel, amount, "
            f"utm_source, utm_campaign, props, ingest_time, schema_version, late, batch_id) "
            f"WITH {_maps(specs)}, [{', '.join(q(x) for x in bag)}] AS BAG, [{', '.join(q(x) for x in live_bag)}] AS LIVE, [{', '.join(map(str, hours))}] AS HRS "
            f"{_row_tail(project)} FROM (SELECT n, uidx, ev0, "
            f"if(PL[ev0][1] = '' AND uidx < {int(n_users * 0.8)} AND day >= {HIST_DAYS} AND has(LIVE, ev0) = 0, LIVE[1 + cityHash64(n, 9) % length(LIVE)], ev0) AS event, "
            f"toDateTime64(toDateTime({q(START_UTC)}, 'UTC') + day * 86400 + (HRS[1 + cityHash64(n, 4) % length(HRS)]) * 3600 + cityHash64(n, 5) % 3600, 3, 'UTC') AS t, "
            f"PL[event][1 + cityHash64(n, 6) % length(PL[event])] AS PLAT, SYMS[1 + toUInt32(floor(pow(cityHash64(n, 102) % 100000 / 100000., 2.4) * length(SYMS)))] AS SYM, SYMNAMES[1 + toUInt32(floor(pow(cityHash64(n, 102) % 100000 / 100000., 2.4) * length(SYMS)))] AS SYMNAME, "
            f"round(pow(10, 3 + 3.5 * {u(101)})) AS AMT "
            f"FROM (SELECT number AS n, toUInt64(floor(pow({u(1)}, 1.8) * {n_users})) AS uidx, BAG[1 + cityHash64(n, 2) % length(BAG)] AS ev0, "
            f"toUInt32(if(uidx >= {int(n_users * 0.8)} OR NOT has(LIVE, ev0), floor({u(3)} * {HIST_DAYS}), floor({u(3)} * {DAYS}))) AS day "
            f"FROM numbers_mt({offset}, {count})))")


def flow_sql(specs: list[dict], project: str, n_users: int, flow: tuple, flow_no: int, offset: int) -> tuple[str, int]:
    name, steps, p, gap, ratio = flow[:5]
    distinct = len(flow) > 5 and flow[5] == "distinct"
    have = {s["name"] for s in specs}
    keep = [i for i, st in enumerate(steps) if st in have]
    ps = list(p) if isinstance(p, (list, tuple)) else [p] * (len(steps) - 1)
    steps = [steps[i] for i in keep]
    ps = [ps[i - 1] for i in keep[1:]]                                  # 字典沒有的步驟略過，續行率跟著對齊
    starts = int(n_users * ratio)
    u = lambda k: f"(cityHash64(f, {k}, {SEED}) % 1000000 / 1000000.)"
    cum = [1.0]
    for x in ps: cum.append(cum[-1] * x)                                # 走到第 k 步的累積機率
    reach = f"toUInt32(arrayCount(c -> {u(7)} < c, [{', '.join(repr(c) for c in cum)}]) - 1)"
    # 每個起點走到第 k 步的機率 = 各步續行率連乘；n 給全域唯一列號（offset + 起點 × 20 + 步序）
    sql = (f"INSERT INTO dmp.events (project, time, event, source, one_id, entity_type, entity_id, distinct_id, session_id, event_id, platform, app_id, symbol, side, channel, amount, "
           f"utm_source, utm_campaign, props, ingest_time, schema_version, late, batch_id) "
           f"WITH {_maps(specs)}, [{', '.join(q(x) for x in steps)}] AS STEPS "
           f"{_row_tail(project)} FROM (SELECT {offset} + f * 20 + k AS n, uidx, STEPS[k + 1] AS event, "
           f"toDateTime64(toDateTime({q(START_UTC)}, 'UTC') + day * 86400 + 3600 + cityHash64(f, 5) % 50000 + k * {gap * 60} + cityHash64(f, k, 6) % {gap * 60}, 3, 'UTC') AS t, "
           f"PL[event][1 + cityHash64(f, 6) % length(PL[event])] AS PLAT, SYMS[1 + toUInt32(floor(pow(cityHash64(f, 102) % 100000 / 100000., 2.4) * length(SYMS)))] AS SYM, SYMNAMES[1 + toUInt32(floor(pow(cityHash64(f, 102) % 100000 / 100000., 2.4) * length(SYMS)))] AS SYMNAME, "
           f"round(pow(10, 3 + 3.5 * {u(101)})) AS AMT "
           f"FROM (SELECT number AS f, {'toUInt64((f * 7919 + 101) % ' + str(n_users) + ')' if distinct else 'toUInt64(floor(pow(' + u(1) + ', 1.5) * ' + str(int(n_users * 0.8)) + '))'} AS uidx, toUInt32(floor({u(3)} * {DAYS - 1})) AS day, "
           f"{reach} AS reach FROM numbers({starts})) ARRAY JOIN range(least(reach + 1, length(STEPS))) AS k)")
    return sql, starts


def user_specs(users: list[dict]) -> list[tuple[str, int, int]]:
    """用戶表其餘可遷移欄位 → (key, 產生碼, 出現機率 %)。L3 識別欄進 id_map、不放用戶屬性；已有固定欄位的不重複。"""
    fixed = {"gender", "signup_date", "last_active_at", "has_margin", "aum", "lifecycle"}
    out = []
    for r in users:
        if r["decision"] != "keep" or r["dmp_target"].split("（")[0] in fixed:
            continue
        k = r["property"]; n = k.lower()
        if re.search(r"odate|_dt$|_date$|date_dt$|^process_dt$", n): code, pct = C_DT, 55
        elif re.search(r"exist_acct|_acc$|contracts|^b27_\w+_acc", n) and r["type"] != "string": code, pct = C_QTY, 70
        elif re.search(r"balance|limit|amt|asset", n): code, pct = C_AMT, 45
        elif re.search(r"^is_|_push$|emp_flag|otype|^st_\w+_acc$", n): code, pct = C_BOOL, 90
        elif n in POOLS: code, pct = C_POOL, 70
        elif r["type"] == "double": code, pct = C_NUM, 60
        else: code, pct = C_STR, 50
        out.append((k, code, pct))
    return out


def users_sql(users: list[dict], n_users: int, offset: int, count: int) -> str:
    spec = user_specs(users)
    kv = ", ".join(f"({q(k)}, {c}, {p})" for k, c, p in spec)
    u = lambda k: f"(cityHash64(n, {k}, {SEED + 1}) % 1000000 / 1000000.)"
    # 用戶屬性值：沿用事件的產生碼；日期改成只到日、數量改小
    val = (f"multiIf(x.2 = {C_DT}, concat('\"', toString(toDate('2026-10-16') - toIntervalDay(cityHash64(n, i, {SEED}) % 3650)), '\"'), "
           f"x.2 = {C_QTY}, toString(cityHash64(n, i, {SEED}) % 4), x.2 = {C_AMT}, toString(round(pow(10, 3 + 4 * (cityHash64(n, i, {SEED}) % 1000 / 1000.)))), "
           f"x.2 = {C_BOOL}, toString(cityHash64(n, i, {SEED}) % 2), x.2 = {C_NUM}, toString(cityHash64(n, i, {SEED}) % 100), "
           f"x.2 = {C_POOL}, toJSONString(POOLS[lower(x.1)][1 + cityHash64(n, i, {SEED}) % length(POOLS[lower(x.1)])]), "
           f"concat('\"', lower(x.1), '_', toString(cityHash64(n, i, {SEED}) % 5 + 1), '\"'))")
    props = (f"concat('{{', arrayStringConcat(arrayMap((x, i) -> concat(toJSONString(x.1), ':', {val}), "
             f"arrayFilter((x, i) -> cityHash64(n, i, 7) % 100 < x.3, UP, arrayEnumerate(UP)), arrayEnumerate(arrayFilter((x, i) -> cityHash64(n, i, 7) % 100 < x.3, UP, arrayEnumerate(UP)))), ','), '}}')")
    q20 = int(n_users * 0.8)
    return (f"INSERT INTO dmp.users (one_id, lifecycle, tier, age_band, gender, branch_id, first_utm_source, signup_date, account_age_days, aum_bucket, has_margin, churn_score, "
            f"pref_market, aum, is_first_trade, last_active_at, props_extra, first_seen, updated_at) "
            f"WITH [{kv}]::Array(Tuple(String, UInt8, UInt8)) AS UP, {pools_sql()} AS POOLS "
            f"SELECT one_id, lifecycle, tier, age_band, gender, branch_id, first_utm_source, sd, account_age_days, aum_bucket, has_margin, churn_score, pref_market, aum, "
            f"is_first_trade, last_active_at, props_extra, first_seen, updated_at FROM (SELECT n, {uid_sql('n')} AS one_id, "
            f"multiIf(n >= {int(n_users * 0.9)}, '流失', n >= {q20}, '沉睡', sd >= toDate('2026-07-19'), '新客', n < {int(n_users * 0.1)}, '成熟', '成長') AS lifecycle, "
            f"multiIf(n < {int(n_users * 0.03)}, 'P1', n < {int(n_users * 0.15)}, 'P2', {u(2)} < 0.45, 'P3', {u(2)} < 0.85, 'P4', 'P5') AS tier, "
            f"['<25', '25-34', '35-44', '45-54', '55-64', '65+'][1 + cityHash64(n, 3) % 6] AS age_band, multiIf({u(4)} < 0.01, 'A', {u(4)} < 0.52, 'M', 'F') AS gender, "
            f"concat('B', leftPad(toString(cityHash64(n, 5) % 40 + 1), 2, '0')) AS branch_id, ['google', 'facebook', 'line', 'direct', 'edm', 'branch'][1 + cityHash64(n, 6) % 6] AS first_utm_source, "
            f"toDate('2026-10-16') - toIntervalDay(toUInt32(pow({u(7)}, 0.7) * 7300)) AS sd, toFloat64(dateDiff('day', sd, toDate('2026-10-16'))) AS account_age_days, "
            f"round(pow(10, 4 + 3.6 * pow({u(8)}, 1 + n / {n_users}))) AS aum_v, multiIf(aum_v >= 5e6, '≥500萬', aum_v >= 1e6, '100–500萬', '<100萬') AS aum_bucket, "
            f"toUInt8({u(9)} < 0.18) AS has_margin, round(if(n >= {q20}, 60 + {u(10)} * 40, {u(10)} * 70), 1) AS churn_score, "
            f"['TSE', 'OTC', 'US', 'HK'][1 + cityHash64(n, 11) % 4] AS pref_market, aum_v AS aum, toUInt8({u(12)} < 0.04) AS is_first_trade, "
            f"toDateTime64(toDateTime({q(START_UTC)}, 'UTC') + toUInt32(if(n >= {q20}, {u(13)} * {HIST_DAYS}, {DAYS - 20} + {u(13)} * 20)) * 86400 + 36000, 3, 'UTC') AS last_active_at, "
            f"{props}::JSON AS props_extra, toDateTime64(sd, 3, 'UTC') AS first_seen, now64(3) AS updated_at "
            f"FROM (SELECT number AS n FROM numbers_mt({offset}, {count})))")


TRADE_KINDS = {"trade"}                   # 「交易資料」= 字典 kind=trade 的事件（委託／成交／撮合批次…），其餘類別算行為資料


def run(ch: Any, project: str, n_users: int, n_events: int, chunk: int = 2_000_000, compact: bool = False, log=print, n_trades: int = 0) -> dict[str, int]:
    events, props, types, users = load_inputs()
    specs = event_specs(events, props, types)
    s = {"allow_experimental_json_type": 1, "max_execution_time": 3600, "max_query_size": 50_000_000, "max_ast_elements": 5_000_000, "max_expanded_ast_elements": 5_000_000,
         "max_insert_block_size": 200_000, "min_insert_block_size_rows": 200_000, "max_bytes_before_external_group_by": 2_000_000_000, "max_insert_threads": 4}
    t0 = time.time()
    if compact:   # 單機開檔數上限低（約 2 萬）時：JSON 屬性每個子欄位一個檔，改用 compact part（每個 part 一個資料檔）
        for tb in ("dmp.events", "dmp.users"):
            ch.query(f"ALTER TABLE {tb} MODIFY SETTING min_bytes_for_wide_part = 1000000000000, min_rows_for_wide_part = 1000000000")
    ch.query(f"ALTER TABLE dmp.events DELETE WHERE project = {q(project)}", settings={"mutations_sync": 2})
    ch.query(f"ALTER TABLE dmp.users DELETE WHERE startsWith(one_id, {q(ID_PREFIX)})", settings={"mutations_sync": 2})
    for off in range(0, n_users, 250_000):
        ch.query(users_sql(users, n_users, off, min(250_000, n_users - off)), settings=s)
        log(f"users {min(off + 250_000, n_users):,}/{n_users:,}  {time.time() - t0:.0f}s")
    all_kinds = {sp["kind"] for sp in specs}
    for off in range(0, n_events, chunk):
        ch.query(events_sql(specs, project, n_users, off, min(chunk, n_events - off), kinds=(all_kinds - TRADE_KINDS) if n_trades else None), settings=s)
        log(f"events {min(off + chunk, n_events):,}/{n_events:,}  {time.time() - t0:.0f}s")
    for off in range(0, n_trades, chunk):
        ch.query(events_sql(specs, project, n_users, n_events + off, min(chunk, n_trades - off), kinds=TRADE_KINDS), settings=s)
        log(f"trades {min(off + chunk, n_trades):,}/{n_trades:,}  {time.time() - t0:.0f}s")
    base = (n_events + n_trades) * 10
    for i, fl in enumerate(FLOWS):
        sql, starts = flow_sql(specs, project, n_users, fl, i, base)
        ch.query(sql, settings=s); base += starts * 20 + 20
        log(f"flow {fl[0]} starts={starts:,}  {time.time() - t0:.0f}s")
    with __import__("contextlib").suppress(Exception):
        ch.query("SYSTEM RELOAD DICTIONARY dmp.users_dict")
    cnt = int(ch.query(f"SELECT count() FROM dmp.events WHERE project = {q(project)}").strip())
    ucnt = int(ch.query(f"SELECT count() FROM dmp.users WHERE startsWith(one_id, {q(ID_PREFIX)})").strip())
    act = int(ch.query(f"SELECT uniqExact(one_id_uint) FROM dmp.events WHERE project = {q(project)}").strip())
    evs = int(ch.query(f"SELECT uniqExact(event) FROM dmp.events WHERE project = {q(project)}").strip())
    trades = [sp["name"] for sp in specs if sp["kind"] in TRADE_KINDS]
    tcnt = int(ch.query(f"SELECT count() FROM dmp.events WHERE project = {q(project)} AND event IN ({', '.join(q(x) for x in trades)})").strip())
    opened = int(ch.query(f"SELECT uniqExact(one_id_uint) FROM dmp.events WHERE project = {q(project)} AND event = 'kh_review_pass'").strip())
    entered = int(ch.query(f"SELECT uniqExact(one_id_uint) FROM dmp.events WHERE project = {q(project)} AND event = 'kh_entry_khpg_pv'").strip())
    return {"users": ucnt, "events": cnt, "trade_events": tcnt, "behavior_events": cnt - tcnt, "active_users": act, "distinct_events": evs, "dictionary_events": len(specs),
            "open_account_entered": entered, "open_account_passed": opened, "open_account_rate": round(opened / entered, 4) if entered else None, "seconds": round(time.time() - t0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=1_000_000); ap.add_argument("--events", type=int, default=10_000_000, help="行為資料筆數（字典 kind≠trade 的事件；流程另外加）")
    ap.add_argument("--trades", type=int, default=5_000_000, help="交易資料筆數（字典 kind=trade 的事件）；0 = 不分類別，全部從 --events 抽")
    ap.add_argument("--project", default="sinopac_demo"); ap.add_argument("--sql", action="store_true")
    ap.add_argument("--compact", action="store_true", help="單機開檔數上限低時改用 compact part（只改本機表設定）")
    ap.add_argument("--flows-only", default="", help="只追加指定流程（逗號分隔，例 app_session），不刪既有資料")
    a = ap.parse_args()
    if a.sql:
        events, props, types, users = load_inputs(); specs = event_specs(events, props, types)
        es = events_sql(specs, a.project, a.users, 0, 1000); us = users_sql(users, a.users, 0, 1000)
        print(json.dumps({"events_in_dictionary": len(specs), "with_props": sum(1 for s in specs if s["keys"]), "user_props": len(user_specs(users)),
                          "events_sql_kb": len(es) // 1024, "users_sql_kb": len(us) // 1024}, ensure_ascii=False))
        return 0
    from ..integration.runner import ClickHouseHTTP
    ch = ClickHouseHTTP(os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=3600)
    if a.flows_only:
        events, props, types, _ = load_inputs(); specs = event_specs(events, props, types)
        for i, fl in enumerate(f for f in FLOWS if f[0] in a.flows_only.split(",")):
            sql, starts = flow_sql(specs, a.project, a.users, fl, i, 9_000_000_000 + i * 200_000_000)   # 與主流程的列號錯開（event_id 唯一）
            ch.query(sql, settings={"allow_experimental_json_type": 1, "max_execution_time": 3600, "max_query_size": 50_000_000, "max_ast_elements": 5_000_000,
                                    "max_expanded_ast_elements": 5_000_000, "max_insert_threads": 4})
            print(f"flow {fl[0]} starts={starts:,}")
        return 0
    print(json.dumps(run(ch, a.project, a.users, a.events, compact=a.compact, n_trades=a.trades), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
