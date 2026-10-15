#!/usr/bin/env python3
"""神策 → GA360 埋點銜接：由事件字典（backend/semantic/events.yaml，神策命名）產生 GA360 埋點規格，
並檢查 GA4／Firebase 的硬性限制，讓新舊資料能用同一個正式事件名銜接。

用法：python3 backend/migration/bridge.py            # 自測 + 產生 ga360_tracking_spec.csv 與 semantic/sensors_ga4_bridge.yaml
     python3 backend/migration/bridge.py --check    # 只檢查不寫檔

設計原則（docs/22）：
- 正式事件名 = 神策事件名（歷史資料不改名）；GA 端用轉換後的 ga4_event 上報；接入時以 bridge 表把 ga4_event 轉回正式事件名。
- 只有來源是 GA360（App／Web 埋點）的事件要給 360 導入商埋；MA、數據中台、渠道回傳等事件走各自的接入，不進 GA。
- 神策預置事件（$ 開頭）對應 GA4 自動／建議事件；對不上的改為自訂事件。
"""
import csv, re, sys, pathlib, collections
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENTS_YAML = ROOT / 'semantic' / 'events.yaml'
USER_PROPS_YAML = ROOT / 'semantic' / 'user_properties.yaml'
OUT_CSV = ROOT / 'migration' / 'ga360_tracking_spec.csv'
OUT_YAML = ROOT / 'semantic' / 'sensors_ga4_bridge.yaml'

# GA4／Firebase 限制（360 方案）
LIMITS = {
    'event_name_len': 40, 'param_name_len': 40, 'param_value_len': 100, 'params_per_event': 25,
    'user_property_name_len': 24, 'user_property_value_len': 36, 'user_properties_per_project': 25,
    'distinct_event_names_per_app_instance': 500,
    'custom_dimensions_event_scoped_360': 125, 'custom_dimensions_user_scoped_360': 100, 'custom_metrics_360': 125,
}
RESERVED_PREFIXES = ('firebase_', 'google_', 'ga_')
RESERVED_EVENTS = {'ad_activeview', 'ad_click', 'ad_exposure', 'ad_impression', 'ad_query', 'adunit_exposure', 'app_clear_data',
                   'app_exception', 'app_remove', 'app_store_refund', 'app_store_subscription_cancel', 'app_store_subscription_convert',
                   'app_store_subscription_renew', 'app_update', 'app_upgrade', 'dynamic_link_app_open', 'dynamic_link_app_update',
                   'dynamic_link_first_open', 'error', 'first_open', 'first_visit', 'in_app_purchase', 'notification_dismiss',
                   'notification_foreground', 'notification_open', 'notification_receive', 'os_update', 'session_start',
                   'session_start_with_rollout', 'user_engagement'}
# 神策預置事件 → GA4：kind = auto（GA 自動收集，不用埋）、recommended（GA 建議事件名，要埋）、custom（自訂）
PRESET_EVENTS = {
    '$pageview':      ('page_view',         'auto',        'gtag 自動；page_location／page_referrer／page_title 自動帶'),
    '$WebClick':      ('web_click',         'custom',      'GA 的 click 只收 outbound；全站點擊改自訂事件，帶 element_id／element_content／page_group'),
    '$WebStay':       ('user_engagement',   'auto',        'GA 以 engagement_time_msec 近似；停留時長口徑不同，分析時用 session 口徑'),
    '$AppViewScreen': ('screen_view',       'auto',        'Firebase 自動；firebase_screen／firebase_screen_class 對應 $screen_name／$title'),
    '$AppStart':      ('app_start',         'custom',      'session_start 口徑不同（30 分鐘）；保留自訂 app_start 以銜接神策「啟動」指標'),
    '$AppEnd':        ('app_end',           'custom',      '帶 event_duration（毫秒）對應 $event_duration'),
    '$AppClick':      ('app_click',         'custom',      '帶 element_id／element_content／screen_name'),
    '$AppInstall':    ('first_open',        'auto',        'Firebase 自動'),
    '$SignUp':        ('sign_up',           'recommended', '帶 method'),
    '$AppPushClick':  ('notification_open', 'auto',        'FCM 推播自動；非 FCM 推播改自訂 push_click'),
    '$AppPushReceived': ('notification_receive', 'auto',   'FCM 推播自動'),
    '$AppCrashed':    ('app_exception',     'auto',        'Firebase Crashlytics'),
    '$ShortUrlRedirect': ('short_url_redirect', 'custom',  '短鏈服務以 Measurement Protocol 上報，帶 client_id'),
}
# 神策預置屬性 → GA4 欄位（BigQuery 匯出）；不在此表的屬性以 event_params 自訂參數上報
PRESET_PROPS = {
    '$page_url': 'page_location', '$referrer': 'page_referrer', '$title': 'page_title', '$screen_name': 'firebase_screen',
    '$app_version': 'app_info.version', '$os': 'device.operating_system', '$os_version': 'device.operating_system_version',
    '$manufacturer': 'device.mobile_brand_name', '$model': 'device.mobile_model_name', '$browser': 'device.web_info.browser',
    '$utm_source': 'traffic_source.source / manual_campaign.source', '$utm_medium': 'traffic_source.medium / manual_campaign.medium',
    '$utm_campaign': 'traffic_source.name / manual_campaign.campaign_name', '$lib': 'platform', '$city': 'geo.city', '$province': 'geo.region',
    '$ip': '不送（GA 不匯出 IP）', '$is_first_day': 'first_open／ga_session_number=1 推導', '$is_first_time': 'ga_session_number=1 推導',
}
GA_SOURCES = ('GA360（App／Web 埋點）', 'GA360（App）', 'GA360（Web）', 'GA360')
# 新 MA 工具與 LINE 事件尚無規格、不列入本案：只匯入神策歷史，T0 後無新資料（docs/22 v1.1）
HISTORY_ONLY_SOURCES = ('MA 系統事件', 'MA', 'LINE（MA）')


def snake(name: str) -> str:
    s = name.lstrip('$')
    s = re.sub(r'(?<=[a-z0-9])([A-Z])', r'_\1', s)        # CamelCase → snake
    s = re.sub(r'[^A-Za-z0-9_]+', '_', s).strip('_').lower()
    s = re.sub(r'_+', '_', s)
    if not s or not s[0].isalpha():
        s = 'e_' + s
    if s.startswith(RESERVED_PREFIXES):
        s = 'sa_' + s
    return s


def ga4_event(ev: dict):
    name = ev['name']
    if name in PRESET_EVENTS:
        g, kind, note = PRESET_EVENTS[name]
        return g, kind, note
    g = snake(name)
    issues = []
    if g in RESERVED_EVENTS:
        g = 'sa_' + g; issues.append('與 GA4 保留事件同名，加 sa_ 前綴')
    if len(g) > LIMITS['event_name_len']:
        issues.append(f'事件名超過 {LIMITS["event_name_len"]} 字元，需縮寫')
    return g, 'custom', '；'.join(issues)


def ga4_param(prop: str):
    if prop in PRESET_PROPS:
        return PRESET_PROPS[prop], 'preset'
    p = snake(prop)
    return p, 'custom'


def build():
    data = yaml.safe_load(EVENTS_YAML.read_text(encoding='utf-8'))
    rows, issues, seen = [], [], collections.defaultdict(list)
    for ev in data['events']:
        src = ev.get('source', '')
        in_ga = src in GA_SOURCES
        g, kind, note = ga4_event(ev)
        if src in HISTORY_ONLY_SOURCES:
            kind, note = 'history_only', f'來源「{src}」：新 MA 工具／LINE 不在本案範圍，只匯入神策歷史，T0 後無新資料'
        elif not in_ga:
            kind, note = 'not_ga', f'來源「{src}」不經 GA；由對應接入（數據中台／渠道回傳／短鏈）直接寫入正式事件名'
            if ev['name'] in PRESET_EVENTS:  # 短鏈等：預置但不走 GA SDK
                g = PRESET_EVENTS[ev['name']][0]
        props = list(ev.get('required', [])) + list(ev.get('optional', []))
        params = []
        for p in props:
            gp, pk = ga4_param(p)
            params.append(f'{gp}' if pk == 'custom' else f'{p}→{gp}')
            if pk == 'custom' and len(gp) > LIMITS['param_name_len']:
                issues.append((ev['name'], f'參數 {p} 轉換後超過 {LIMITS["param_name_len"]} 字元'))
        if in_ga and len([p for p in props if p not in PRESET_PROPS]) > LIMITS['params_per_event']:
            issues.append((ev['name'], f'自訂參數 > {LIMITS["params_per_event"]} 個，需合併或改 user property'))
        if in_ga:
            seen[g].append(ev['name'])
        if note and in_ga and '需縮寫' in note:
            issues.append((ev['name'], note))
        rows.append({'sensors_event': ev['name'], 'cn': ev.get('cn', ''), 'group': ev.get('group', ''), 'source': src,
                     'platforms': ev.get('platforms', ''), 'ga4_event': g, 'kind': kind, 'params': ' | '.join(params), 'note': note,
                     'aliases': ' | '.join(ev.get('aliases', []) or [])})
    for g, names in seen.items():
        if len(names) > 1:
            issues.append((','.join(names), f'多個神策事件轉成同一個 GA4 事件名 {g}，需加前綴區分'))
    ups = yaml.safe_load(USER_PROPS_YAML.read_text(encoding='utf-8'))['user_properties']
    up_rows = []
    for u in ups:
        k = u['key']; issue = ''
        if len(k) > LIMITS['user_property_name_len']:
            issue = f'名稱超過 {LIMITS["user_property_name_len"]} 字元（Firebase user property 限制）'
        up_rows.append({'key': k, 'cn': u.get('cn', ''), 'sensitivity': u.get('sensitivity', ''),
                        'to_ga': 'no' if u.get('sensitivity') in ('L3', 'L4') else 'user_property', 'note': issue or ('L3 以上不送 GA，留在 DMP 用戶表' if u.get('sensitivity') in ('L3', 'L4') else '')})
        if issue:
            issues.append((f'user_property {k}', issue))
    if len([r for r in up_rows if r['to_ga'] == 'user_property']) > LIMITS['user_properties_per_project']:
        issues.append(('user_properties', f'送 GA 的 user property 超過 {LIMITS["user_properties_per_project"]} 個'))
    return rows, up_rows, issues


def write(rows, up_rows, issues):
    with OUT_CSV.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    bridge = {
        'version': 1,
        'generated_by': 'backend/migration/bridge.py（由 events.yaml 產生；不要手改）',
        'principle': '正式事件名 = 神策事件名；GA 上報 ga4_event；接入時 ga4_event → sensors_event（docs/22 §2）',
        'limits': LIMITS,
        'reserved_prefixes': list(RESERVED_PREFIXES),
        'preset_events': {k: {'ga4_event': v[0], 'kind': v[1], 'note': v[2]} for k, v in PRESET_EVENTS.items()},
        'preset_props': PRESET_PROPS,
        'events': [{'sensors_event': r['sensors_event'], 'ga4_event': r['ga4_event'], 'kind': r['kind']} for r in rows],
        'ga4_to_sensors': {r['ga4_event']: r['sensors_event'] for r in rows if r['kind'] in ('auto', 'recommended', 'custom')},
        'user_properties': up_rows,
        'open_issues': [{'where': a, 'issue': b} for a, b in issues],
    }
    OUT_YAML.write_text('# 神策 → GA4 事件／屬性橋接表（generated）。接入時 Flink eventize 以 ga4_to_sensors 把 GA 事件名轉回正式事件名。\n'
                        + yaml.safe_dump(bridge, allow_unicode=True, sort_keys=False, width=200), encoding='utf-8')


def main():
    rows, up_rows, issues = build()
    kinds = collections.Counter(r['kind'] for r in rows)
    print(f'events={len(rows)} ' + ' '.join(f'{k}={v}' for k, v in sorted(kinds.items())))
    print(f'user_properties={len(up_rows)} to_ga={sum(1 for u in up_rows if u["to_ga"] == "user_property")}')
    for a, b in issues:
        print(f'ISSUE {a}: {b}')
    # 自測：轉換規則、往返唯一、限制
    assert snake('$AppViewScreen') == 'app_view_screen' and snake('kh_entry_khpg_pv') == 'kh_entry_khpg_pv' and snake('Market_Data') == 'market_data'
    assert snake('$ga_test') == 'sa_ga_test' and snake('123x') == 'e_123x'
    ga = [r for r in rows if r['kind'] not in ('not_ga', 'history_only')]
    assert all(len(r['ga4_event']) <= LIMITS['event_name_len'] and re.fullmatch(r'[a-z][a-z0-9_]*', r['ga4_event']) for r in ga), '事件名不合 GA4 規則'
    assert len({r['ga4_event'] for r in ga}) == len(ga), 'GA4 事件名碰撞'
    assert not any(r['ga4_event'] in RESERVED_EVENTS and r['kind'] == 'custom' for r in ga)
    if '--check' not in sys.argv:
        write(rows, up_rows, issues)
        print(f'wrote {OUT_CSV.relative_to(ROOT.parent)} and {OUT_YAML.relative_to(ROOT.parent)}')
    print('bridge self-test OK')


if __name__ == '__main__':
    main()
