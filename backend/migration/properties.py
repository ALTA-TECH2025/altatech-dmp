#!/usr/bin/env python3
"""舊平台事件屬性字典（客戶匯出的「批量修改資訊檔」：屬性名／顯示名）→ 敏感等級、入倉決定、GA4 參數名。

用法：python3 backend/migration/properties.py [client.csv]            # 自測 + 讀客戶事件屬性字典、寫 legacy_properties.csv
     python3 backend/migration/properties.py --users [users.csv]     # 讀客戶用戶屬性導出（name,type,comment）、寫 legacy_user_properties.csv
     python3 backend/migration/properties.py --check                  # 自測 + 兩個產出檔重算比對、forbidden_l4 一致

用戶屬性（profile）另一套口徑：user_group_*／user_tag_* 是舊平台分群與標籤的回寫欄位（每個分群一欄，共三千多欄），不當用戶屬性遷移，
改以 /v1/segments/import 匯入快照、標籤進標籤引擎；其餘 core 屬性依同一套 L1–L4 規則分級，L1／L2 要登記進 semantic/user_properties.yaml 才能查。

輸入 CSV 兩欄：property（舊平台屬性名，不可改）、display（顯示名）。預設路徑 migration/legacy_properties_client.csv。
輸出 CSV：property, display, tag, domain, sensitivity, decision, ga4_param, rule

分級與決定（docs/10 §欄位等級、docs/22 §3.3）：
- L4 可識別（姓名、手機、信箱、生日、客戶銀行帳號、收件人）→ decision=drop_l4：接入預處理 stage() 丟棄，永不入倉；
  名單必須同步登記在 semantic/user_properties.yaml 的 forbidden_l4（--check 會比對）。
- L3 代碼化識別（客戶 UUID／去識別化 ID、證券帳號、員工編號、裝置 ID、匿名關聯 ID）→ decision=hash：入倉前 token_v1（HMAC），
  查詢時依 masking.yaml 尾碼遮罩。
- L2 敏感業務（金額、費用、稅、餘額、額度、交割、KYC、信用、身分類別，以及數據中台交易／帳務／開戶欄位預設）→ keep，按角色遮罩／分桶。
- L1 行為（頁面、元素、UTM、裝置、App、課程、問卷、推播計畫等）→ keep。
- 顯示名以 (X) 開頭的維護／清理欄位與 $ip 類 → drop_ops，不入倉。
domain：ga（App／Web 埋點，經 GA360 上報，給 ga4_param）、batch（數據中台批次：交易／帳務／開戶／CRM，不經 GA）、ma（舊平台智能運營 SF 事件，只匯入歷史）。
"""
from __future__ import annotations

import collections
import csv
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bridge import LIMITS, snake

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT_CSV = ROOT / 'migration' / 'legacy_properties_client.csv'
OUT_CSV = ROOT / 'migration' / 'legacy_properties.csv'
USERS_CSV = ROOT / 'migration' / 'legacy_user_properties_client.csv'
OUT_USERS_CSV = ROOT / 'migration' / 'legacy_user_properties.csv'
USER_PROPS_YAML = ROOT / 'semantic' / 'user_properties.yaml'

TAG_RE = re.compile(r'^\((O|C|B|N|X|L|SF|Item_id)\)')   # 客戶顯示名前綴：(O) 舊欄位 (C) CRM／開戶 (B) 借貸 (N) NPS (X) 維護 (L) 學堂 (SF) 智能運營
L4_NAMES = {   # 顯示名無法辨識、但屬性名本身就是個資的
    'Name', 'Mobile', 'EMail', 'CUADM', 'CUS_MTEL', 'SNSLoginEmail', 'birthDT_ConfirmID', 'birthdt_evlogin', '$sf_msg_recipient',
    'Bankact', 'BANK_ACNO_1', 'BANK_ACNO_2', 'IO_BANK_ACNO', 'PO_BANK_ACNO', 'TRUST_ACNO',
}
L4_DISPLAY = re.compile(r'姓名|手機|電話|信箱|E_MAIL|生日|收件人|入扣帳帳號|收益帳號|信託帳號|分戶帳帳戶|^帳號\d$')
L3_NAMES = {'UUID', 'S_IDNO', 'PART_IDNO', 'member_id', 'gift_card_recipient', 'gift_card_recipient_acno', 'gift_card_recipient_unicode',
            '$device_id', '$track_signup_original_id', 'ANTE_BNK_ACC', 'HAA11', 'BROKERNO3_SACNO', 'BROKERNO4_SACNO'}   # 分公司代碼＋證券帳號
KEY_MATERIAL = {'hashKey', 'ivKey', 'svid', '_latest_hashKey', '_latest_ivKey', '_latest_svid'}   # 短網址／開戶驗證的加密參數：不入倉、不送 GA
L3_DISPLAY = re.compile(r'客戶帳號|信用戶帳號|匯出帳號|去識別化ID|客戶ID|員編|工號|員工編號|員工代號|營業員代號|審核員工編號|推介行員代號|交割員代碼|設備ID|關聯匿名ID|兌換人ID|會員帳號')
L2_DISPLAY = re.compile(r'金額|手續費|費$|費\(|費率|稅|餘額|額度|價金|價格|價位|價$|價\(|成本|匯率|利息|保證金|擔保品|融資|融券|借券|借貸|股數|數量|單位|面額|折扣|'
                        r'KYC|風險|VIP|美國人|內部員工|員工戶|客戶性質|身分|性別|自然人|大戶|交割|帳務|違約|戶數|契約數|委託|成交|申購|抽籤|買賣|'
                        r'商品代號|標的|商品別|市場|交易所|券商|分公司|分行|銀行|開戶|銷戶|審核|簽核|申請|案號|件號|圈存|撥款|扣款|收付|平倉|託管|投資人|'
                        r'折扣碼|邀請碼|訂單|購物車|禮券|兌換|幣別|權證|信用註記|公司|備註|意見|answer')   # 公司名／自由文字可能帶個資，按 L2 遮罩
MA_PREFIX = ('$sf_', 'sf_', 'rec_banner_id', 'strategy_id', 'section_id', '$channel_link_id', '$matching_key_list', '$utm_matching_type')
DROP_OPS_NAMES = {'$ip', '$ip_isp', '$ip_country', '$latitude', '$longitude', '$hdfs_import_batch_name', '$kafka_offset', '$app_remote_config'}
DROP_OPS_DISPLAY = re.compile(r'IP位址|IP$')   # IP 一律不入倉（legacy_format.DROP_PROPS 同口徑）


UP_L4 = re.compile(r'mail|phone|mobile|mtel|cuadm|name$|birth|^openact')          # 信箱、手機、姓名、生日（含社群登入信箱、通知手機、虛擬生日）
UP_NOT_L4 = re.compile(r'^is_\w+_push$|snslogintype|_mark$')                       # 推播許可、登入類別、來源註記：名稱含 mail 但不是個資
UP_L3 = re.compile(r'^(id|first_id|second_id|s_idno|uuid|line_uuid|ec_se[cb]_acc_no|ec_sub_acc_no|\$device_id_list)$')   # 舊平台內部 ID、匿名／登入 ID、去識別化 ID、LINE ID、帳號、裝置清單
UP_KEY_MATERIAL = {'ivkey', 'hashkey', 'svid'}
# 規格表裡數據中台 ODS 原始字串欄（B27 查詢結果），與 b27_* 清洗版（數值）重複：不遷移
UP_ODS_DUP = {'any_no_return': 'b27_overdue_any_loan_balance', 'any_amount': 'b27_any_loan_balance', 'any_limit': 'b27_any_loan_credit_limit',
              'any_open': 'b27_any_loan_contracts', 'slb_tse_amount': 'b27_securities_lending_center_balance', 'nlb_amount': 'b27_securities_lending_balance',
              'nlb_limit': 'b27_securities_lending_limit', 'adv_no_return': 'b27_overdue_loan_balance', 'adv_amount': 'b27_loan_balance',
              'adv_limit': 'b27_loan_credit_limit', 'adv_open': 'b27_loan_contracts', 'nlb_open': 'b27_ln_acc', 'tot_amount': 'b27_highest_daily_trading_limit',
              'stk_amount': 'b27_short_selling_balance', 'cpt_amount': 'b27_margin_balance', 'break': 'b27_default_status', 'd_trade_open': 'b27_day_trading_acc',
              'credit_open': 'b27_mt_acc', 'fut_open': 'b27_fu_acc', 'normal_open': 'b27_st_acc'}
PLAN_USERS_CSV = ROOT / 'migration' / 'legacy_plan_user_properties.csv'   # 舊平台埋點規格表的用戶表（只有欄位定義）
UP_L2 = re.compile(r'amt|balance|limit|asset|acc\b|_acc$|_acc_|acct|trade|odate|trans|status|loan|margin|lending|contracts|broker|'
                   r'^b27_|^s_age|^gender|s_sex|emp_flag|otype|user_type|cuaa|cuaf|default|match_dt|category')
UP_MAP = {'s_idno': 'customer_code', 'gender': 'gender', '$dimension_dict_s_sex': 'gender', 's_age': 'age_band（分桶）', 'st_last_asset_amt': 'aum',
          'st_first_odate': 'signup_date', 'first_odate': 'signup_date', 'last_login_date_dt': 'last_active_at', 'emp_flag': 'is_staff（新增）',
          'st_mt_acc': 'has_margin', '$dimension_dict_sleepycust_group': 'lifecycle', 'first_trade_odate': 'first_trade_date（新增）'}


def classify_user(name: str, typ: str) -> tuple[str, str, str, str, str]:
    """用戶屬性 → (family, sensitivity, decision, dmp_target, rule)。"""
    if name.startswith('user_group_'):
        return 'segment_result', 'L1', 'import_segment', '/v1/segments/import（舊平台分群快照）', '舊平台分群回寫欄位：一個分群一欄，不當屬性遷移'
    if name.startswith('user_tag_'):
        return 'tag_result', 'L1', 'import_tag', '標籤引擎（匯入標籤值）', '舊平台標籤回寫欄位：匯入標籤引擎'
    if name.startswith('$dimension_dict_'):
        base = name[len('$dimension_dict_'):]
    else:
        base = name
    if base in UP_KEY_MATERIAL:
        return 'core', 'L3', 'drop_ops', '—', '加密金鑰材料：不入用戶表、不送 GA'
    if base in UP_ODS_DUP:
        return 'core', 'L2', 'skip_dup', UP_ODS_DUP[base], '數據中台原始字串欄，與 b27_* 清洗版重複：不遷移'
    if UP_L4.search(base) and not UP_NOT_L4.search(base):
        return 'core', 'L4', 'drop_l4', '—', '可識別個資：不入用戶表（forbidden_l4）'
    if UP_L3.match(base):
        return 'core', 'L3', 'hash', UP_MAP.get(name, 'one_id／id_map'), '代碼化識別：進 id_map 或 token 後存'
    if UP_L2.search(base):
        return 'core', 'L2', 'keep', UP_MAP.get(name, '登記 user_properties.yaml（L2）'), '敏感業務：按角色遮罩／分桶'
    return 'core', 'L1', 'keep', UP_MAP.get(name, '登記 user_properties.yaml（L1）'), '行為／偏好屬性'


def build_users(rows: list[tuple[str, str]], plan: list[tuple[str, str]] | None = None) -> list[dict]:
    """rows＝舊平台用戶屬性導出；plan＝埋點規格表的用戶表。規格表有、導出沒有的欄位附在後面（family=design_only）：
    舊平台裡不存在就導不出來，請客戶確認是否已停用；個資與重複欄的決定照舊（出現了也要擋）。"""
    out = []
    for name, typ in rows:
        fam, lvl, dec, tgt, rule = classify_user(name, typ)
        out.append({'property': name, 'type': typ, 'family': fam, 'sensitivity': lvl, 'decision': dec, 'dmp_target': tgt, 'rule': rule})
    have = {n.lower() for n, _ in rows}
    for name, typ in plan or []:
        key = name.lower()
        if key in have or f'$dimension_dict_{key}' in have:      # 舊平台維度字典欄（代碼 → 名稱）以 $dimension_dict_ 前綴導出
            continue
        have.add(key)
        _, lvl, dec, tgt, rule = classify_user(key, typ)
        if dec in ('keep', 'hash'):
            dec, tgt, rule = 'confirm', '—（舊平台用戶表無此欄）', f'規格表有、用戶屬性導出無：請客戶確認是否已停用；若仍在用依 {lvl} 處理'
        out.append({'property': key, 'type': typ, 'family': 'design_only', 'sensitivity': lvl, 'decision': dec, 'dmp_target': tgt, 'rule': rule})
    return out


def read_plan_users() -> list[tuple[str, str]]:
    if not PLAN_USERS_CSV.exists():
        return []
    with PLAN_USERS_CSV.open(encoding='utf-8-sig', newline='') as f:
        return [(r['property'].strip(), r['type'].strip()) for r in csv.DictReader(f) if r['property'].strip()]


def domain_of(name: str, display: str) -> str:
    if name.startswith(MA_PREFIX):
        return 'ma'
    tag = TAG_RE.match(display)
    if tag and tag.group(1) in ('O', 'C', 'B'):
        return 'batch'
    if re.fullmatch(r'[A-Z][A-Z0-9_]*', name) and not name.startswith('$'):   # 全大寫 = 數據中台交易／帳務／開戶欄位（HG109、MTH_AMT…）
        return 'batch'
    return 'ga'


def classify(name: str, display: str) -> tuple[str, str, str, str]:
    """→ (domain, sensitivity, decision, rule)。規則由嚴到寬，先中先贏。"""
    dom = domain_of(name, display)
    tag = TAG_RE.match(display)
    if name in KEY_MATERIAL:
        return dom, 'L3', 'drop_ops', '加密金鑰材料：不入倉、不送 GA'
    if (tag and tag.group(1) == 'X') or name in DROP_OPS_NAMES or DROP_OPS_DISPLAY.search(display):
        return dom, 'L1', 'drop_ops', '(X) 維護／清理欄位或 IP 類：不入倉'
    if name in L4_NAMES or L4_DISPLAY.search(display):
        return dom, 'L4', 'drop_l4', '可識別個資：接入預處理丟棄（forbidden_l4）'
    if name in L3_NAMES or L3_DISPLAY.search(display):
        return dom, 'L3', 'hash', '代碼化識別：入倉前 token_v1，查詢尾碼遮罩'
    if dom == 'ma':
        return dom, 'L1', 'keep', '舊平台智能運營事件屬性：只匯入歷史'
    if dom == 'batch' or L2_DISPLAY.search(display):
        return dom, 'L2', 'keep', '敏感業務：按角色遮罩／分桶，匯出降一級'
    return dom, 'L1', 'keep', '行為屬性'


def build(rows: list[tuple[str, str]]) -> tuple[list[dict], list[str]]:
    out, issues, seen = [], [], collections.defaultdict(list)
    for name, display in rows:
        dom, lvl, dec, rule = classify(name, display)
        gp = snake(name) if dom == 'ga' and dec in ('keep', 'hash') else ''
        if gp and name.endswith('_'):   # 客戶以尾底線區分「(新)」版本；snake 會去掉，補 _new 以免與舊版同名
            gp += '_new'
        if gp:
            seen[gp].append(name)
            if len(gp) > LIMITS['param_name_len']:
                issues.append(f'{name}: GA4 參數名 {gp} 超過 {LIMITS["param_name_len"]} 字元')
        out.append({'property': name, 'display': display, 'tag': (TAG_RE.match(display).group(1) if TAG_RE.match(display) else ('#' if display.startswith('#') else '')),
                    'domain': dom, 'sensitivity': lvl, 'decision': dec, 'ga4_param': gp, 'rule': rule})
    for gp, names in seen.items():
        if len(names) > 1:
            issues.append(f'GA4 參數名碰撞 {gp}: {", ".join(names)}')
    return out, issues


def read_client(path: pathlib.Path) -> list[tuple[str, str]]:
    with path.open(encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f)
        rows = [(a.strip(), b.strip()) for a, b, *_ in r if a.strip()]
    if rows and rows[0][0].lower() == 'property':
        rows = rows[1:]
    dup = [k for k, v in collections.Counter(n for n, _ in rows).items() if v > 1]
    if dup:
        raise SystemExit(f'客戶字典屬性名重複：{dup[:10]}')
    return rows


def selftest() -> None:
    cases = {
        ('CNAME', '(O)姓名'): ('batch', 'L4', 'drop_l4'), ('member_phone', '會員手機'): ('ga', 'L4', 'drop_l4'),
        ('task_mail', '#活動登錄-信箱'): ('ga', 'L4', 'drop_l4'), ('BANK_ACNO_1', '帳號1'): ('batch', 'L4', 'drop_l4'),
        ('S_IDNO', '去識別化ID'): ('batch', 'L3', 'hash'), ('S_ACNO', '#客戶帳號'): ('batch', 'L3', 'hash'),
        ('$device_id', '設備ID first id'): ('ga', 'L3', 'hash'), ('SALE_NO', '(C)#營業員員編'): ('batch', 'L3', 'hash'),
        ('MTH_AMT_TW', '(O)成交金額(台幣)'): ('batch', 'L2', 'keep'), ('item_price', '#商品金額'): ('ga', 'L2', 'keep'),
        ('PurchaseCost', '#申購金額'): ('ga', 'L2', 'keep'), ('StockID', '#商品代號'): ('ga', 'L2', 'keep'),
        ('$utm_source', 'utm_source 來源(即時)'): ('ga', 'L1', 'keep'), ('lesson_title', '課程標題'): ('ga', 'L1', 'keep'),
        ('$sf_plan_id', '(SF)推播計劃 Plan ID'): ('ma', 'L1', 'keep'), ('MUSER', '(X)維護者MUSER'): ('batch', 'L1', 'drop_ops'),
        ('$ip', 'IP位址'): ('ga', 'L1', 'drop_ops'), ('STOCK_NAME', '商品名稱'): ('batch', 'L2', 'keep'),
        ('ANTE_NAME', '上手名稱'): ('batch', 'L2', 'keep'), ('$element_name', '元素名稱 Element Name'): ('ga', 'L1', 'keep'),
        ('dwt_flashorder_ip', '#閃電下單IP'): ('ga', 'L1', 'drop_ops'), ('msg', '備註'): ('ga', 'L2', 'keep'),
        ('$is_login_id', '是否登入'): ('ga', 'L1', 'keep'), ('companyname', '公司名稱'): ('ga', 'L2', 'keep'),
    }
    rows, _ = build([('dwf_appclick_name_', '大戶豐點擊名稱(新)'), ('dwf_appclick_name', '大戶豐點擊名稱')])
    assert [r['ga4_param'] for r in rows] == ['dwf_appclick_name_new', 'dwf_appclick_name']
    for (n, d), want in cases.items():
        got = classify(n, d)[:3]
        assert got == want, (n, d, got, want)
    rows, issues = build([('LoginTime', '(X)#登入時間'), ('login_time', '登入時間'), ('a_' * 25, 'x')])
    assert rows[0]['ga4_param'] == '' and rows[1]['ga4_param'] == 'login_time' and any('超過' in i for i in issues), issues
    rows, issues = build([('Qty', '#交易數量'), ('qty', '數量')])
    assert any('碰撞' in i for i in issues)
    ucases = {('user_group_pj2_sf1_1', 'bigint'): ('segment_result', 'import_segment'), ('user_tag_a0x', 'string'): ('tag_result', 'import_tag'),
              ('googleloginmail', 'string'): ('core', 'drop_l4'), ('openactname', 'string'): ('core', 'drop_l4'), ('s_birth_virtual', 'double'): ('core', 'drop_l4'),
              ('noticemobile', 'string'): ('core', 'drop_l4'), ('is_email_push', 'bigint'): ('core', 'keep'), ('snslogintype', 'string'): ('core', 'keep'),
              ('s_idno', 'string'): ('core', 'hash'), ('line_uuid', 'string'): ('core', 'hash'), ('ec_sec_acc_no', 'string'): ('core', 'hash'),
              ('st_last_asset_amt', 'double'): ('core', 'keep'), ('$utm_source', 'string'): ('core', 'keep'), ('$dimension_dict_s_sex', 'string'): ('core', 'keep')}
    for (n, t), want in ucases.items():
        got = classify_user(n, t)
        assert (got[0], got[2]) == want, (n, got, want)
    assert classify_user('ivkey', 'string')[2] == 'drop_ops' and classify_user('any_amount', 'string')[2:4] == ('skip_dup', 'b27_any_loan_balance')
    assert classify_user('e_mail_mark', 'string')[2] == 'keep' and classify_user('ec_sub_acc_no', 'string')[2] == 'hash' and classify_user('broker_no4', 'string')[1] == 'L2'
    assert classify('ivKey', '静默创建_ivKey')[2] == 'drop_ops' and classify('BROKERNO4_SACNO', 'BROKERNO4_SACNO')[1:3] == ('L3', 'hash')
    bu = build_users([('s_idno', 'string'), ('$dimension_dict_cuaa1', 'string')], [('S_IDNO', 'string'), ('CUAA1', 'string'), ('B27_NAME', 'string'), ('newacc_flag', 'string')])
    assert [(x['property'], x['family'], x['decision']) for x in bu[2:]] == [('b27_name', 'design_only', 'drop_l4'), ('newacc_flag', 'design_only', 'confirm')], bu
    assert classify_user('st_last_asset_amt', 'double')[1] == 'L2' and classify_user('$utm_source', 'string')[1] == 'L1' and classify_user('gender', 'string')[1] == 'L2'


def run_users(src: pathlib.Path, check: bool, forbidden: set[str]) -> int:
    with src.open(encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f); hdr = next(r)
        fam = hdr.index('family') if 'family' in hdr else None          # 重算已產出檔時跳過規格表補的列，避免重複
        rows = [(x[0].strip(), x[1].strip()) for x in r if x and x[0].strip() and (fam is None or x[fam] != 'design_only')]
    assert hdr[0] == 'property' or hdr[0] == 'name', hdr
    out = build_users(rows, read_plan_users())
    c = collections.Counter((o['family'], o['sensitivity'], o['decision']) for o in out)
    print(f'user_properties={len(out)} ' + ' '.join(f'{a}/{b}/{d}={v}' for (a, b, d), v in sorted(c.items())))
    missing = sorted(o['property'] for o in out if o['decision'] == 'drop_l4' and o['property'] not in forbidden)
    if missing:
        print('ISSUE forbidden_l4 缺（用戶屬性）：' + ', '.join(missing))
    if check:
        with OUT_USERS_CSV.open(encoding='utf-8-sig', newline='') as f:
            cur = [dict(x) for x in csv.DictReader(f)]
        if cur != out:
            print('ISSUE legacy_user_properties.csv 與規則產出不一致，請重跑 --users'); return 1
        return 1 if missing else 0
    with OUT_USERS_CSV.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f'wrote {OUT_USERS_CSV.relative_to(ROOT.parent)}')
    return 0


def main() -> int:
    selftest()
    check = '--check' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    forbidden = set(yaml.safe_load(USER_PROPS_YAML.read_text(encoding='utf-8')).get('forbidden_l4', []))
    if '--users' in sys.argv:
        src = pathlib.Path(args[0]) if args else USERS_CSV
        if not src.exists():
            print(f'無用戶屬性導出 {src}'); return 1
        rc = run_users(src, check, forbidden)
        print('properties self-test OK' if rc == 0 else 'properties users FAILED'); return rc
    src = pathlib.Path(args[0]) if args else CLIENT_CSV
    if not src.exists() and check and OUT_CSV.exists():
        src = OUT_CSV   # 客戶原檔不進 repo：--check 以已產出檔的 property／display 兩欄重算後比對
    if not src.exists():
        print(f'properties self-test OK（無客戶字典 {src}，略過產出）')
        return 0
    rows, issues = build(read_client(src))
    missing = sorted(r['property'] for r in rows if r['decision'] == 'drop_l4' and r['property'] not in forbidden)
    c = collections.Counter((r['domain'], r['sensitivity'], r['decision']) for r in rows)
    print(f'properties={len(rows)} ' + ' '.join(f'{d}/{l}/{k}={v}' for (d, l, k), v in sorted(c.items())))
    for i in issues:
        print('ISSUE', i)
    if missing:
        print('ISSUE forbidden_l4 缺：' + ', '.join(missing))
    if check:
        if not OUT_CSV.exists():
            print('ISSUE 尚未產出 legacy_properties.csv'); return 1
        with OUT_CSV.open(encoding='utf-8-sig', newline='') as f:
            cur = [dict(r) for r in csv.DictReader(f)]
        if cur != rows:
            print('ISSUE legacy_properties.csv 與規則產出不一致，請重跑 python3 backend/migration/properties.py'); return 1
        if missing:
            return 1
        if OUT_USERS_CSV.exists() and run_users(OUT_USERS_CSV, True, forbidden):
            return 1
    else:
        with OUT_CSV.open('w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        print(f'wrote {OUT_CSV.relative_to(ROOT.parent)}')
    print('properties self-test OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
