#!/usr/bin/env python3
"""神策獨有字段（預置 $ 屬性、$sf_ 智能運營、IDM 身分欄位、「静默创建／最新一次」自動屬性、預置事件）→ GA360 對應與取捨。

用法：python3 backend/migration/sensors_specific.py            # 自測 + 寫 sensors_specific_fields.csv
     python3 backend/migration/sensors_specific.py --check    # 自測 + 比對產出檔，並確認客戶字典裡每個神策獨有字段都有處置

處置（disposition）：
  直接對應  GA360 BigQuery 匯出有同義欄位，接入時改名即可（ga4_mapping.yaml）
  改口徑    GA 有近似欄位但定義不同（session 口徑、互動時間、自訂事件），報表口徑要改並向客戶說明
  DMP 推導  GA 不給，但能由其他欄位在 DMP 算出（path／host 拆解、首次／最近來源、登入狀態）
  身分對應  神策 IDM 的 ID 欄位，由 OneID／id_map 取代
  只匯歷史  神策智能運營（SF）專屬，GA360 無對應；新 MA 工具不在本案，只保留歷史
  待取捨    GA 不收、也推不出來；要嘛 App／Web 加自訂參數，要嘛放棄（需客戶決定）
  捨棄      SDK 內部、維運、IP 類，不遷
"""
from __future__ import annotations

import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / 'migration' / 'sensors_specific_fields.csv'
EVENT_PROPS_CSV = ROOT / 'migration' / 'sensors_properties.csv'
USER_PROPS_CSV = ROOT / 'migration' / 'sensors_user_properties.csv'

DISPOSITIONS = ('直接對應', '改口徑', 'DMP 推導', '身分對應', '只匯歷史', '待取捨', '捨棄')

# (scope, 神策字段, GA360 對應, 處置, 說明)
SPEC: list[tuple[str, str, str, str, str]] = [
    # --- 事件屬性：頁面與來源
    ('event', '$url', 'event_params.page_location', '直接對應', ''),
    ('event', '$url_path', 'page_location 拆 path', 'DMP 推導', '接入時以 SQL 拆解'),
    ('event', '$url_host', 'page_location 拆 host', 'DMP 推導', ''),
    ('event', '$title', 'event_params.page_title', '直接對應', ''),
    ('event', '$referrer', 'event_params.page_referrer', '直接對應', ''),
    ('event', '$referrer_host', 'page_referrer 拆 host', 'DMP 推導', ''),
    ('event', '$referrer_title', '—', '待取捨', 'GA 不收前頁標題；可由前一個 page_view 的 page_title 補，或放棄'),
    ('event', '$screen_name', 'event_params.firebase_screen', '直接對應', 'firebase_screen_class 對應畫面類別'),
    ('event', '$utm_source', 'collected_traffic_source.manual_source', '直接對應', '事件級即時來源'),
    ('event', '$utm_medium', 'collected_traffic_source.manual_medium', '直接對應', ''),
    ('event', '$utm_campaign', 'collected_traffic_source.manual_campaign_name', '直接對應', ''),
    ('event', '$utm_term', 'collected_traffic_source.manual_term', '直接對應', ''),
    ('event', '$utm_content', 'collected_traffic_source.manual_content', '直接對應', ''),
    ('event', '$latest_utm_source', 'session_traffic_source_last_click.manual_campaign.source', '改口徑', '神策＝30 天內最近一次外部來源；GA＝本次 session 最後點擊。要用神策口徑由 DMP 以事件序列重算'),
    ('event', '$latest_utm_medium', 'session_traffic_source_last_click.manual_campaign.medium', '改口徑', '同上'),
    ('event', '$latest_utm_campaign', 'session_traffic_source_last_click.manual_campaign.campaign_name', '改口徑', '同上'),
    ('event', '$latest_utm_term', 'session_traffic_source_last_click.manual_campaign.term', '改口徑', '同上'),
    ('event', '$latest_utm_content', 'session_traffic_source_last_click.manual_campaign.content', '改口徑', '同上'),
    ('event', '$latest_referrer', 'page_referrer（session 首頁）', 'DMP 推導', '以 session 第一個 page_view 的 page_referrer 重算'),
    ('event', '$latest_search_keyword', '—', 'DMP 推導', '由 referrer 的查詢字串解析；主流搜尋引擎已不帶關鍵字，覆蓋率會很低'),
    ('event', '$latest_traffic_source_type', '—', 'DMP 推導', '由 medium／referrer 分類（自然搜尋、社交、付費、直接）'),
    ('event', '$deeplink_url', 'App 自訂參數 deeplink_url', '改口徑', 'Firebase 只有 dynamic_link_* 事件；一般 deeplink 要在 App 自訂上報'),
    ('event', '$short_url_key', 'Measurement Protocol 自訂參數', '直接對應', '短鏈服務以 MP 上報 short_url_redirect'),
    ('event', '$short_url_target', 'Measurement Protocol 自訂參數', '直接對應', ''),
    # --- 事件屬性：元素點擊（神策 $WebClick／$AppClick 全埋點）
    ('event', '$element_id', '自訂事件 web_click／app_click 的 element_id', '改口徑', 'GA 自動只收外連 click（link_url／link_text／link_id／link_classes）；全站點擊要自訂事件'),
    ('event', '$element_content', '自訂事件參數 element_content', '改口徑', ''),
    ('event', '$element_name', '自訂事件參數 element_name', '改口徑', ''),
    ('event', '$element_type', '自訂事件參數 element_type', '改口徑', ''),
    ('event', '$element_class_name', '自訂事件參數 element_class', '改口徑', ''),
    ('event', '$element_target_url', 'link_url（外連）／自訂參數', '改口徑', ''),
    ('event', '$element_selector', '—', '待取捨', 'CSS 選擇器只有神策可視化埋點用；GA 自訂參數值 ≤ 100 字元，建議放棄'),
    ('event', '$element_path', '—', '待取捨', '同上'),
    ('event', '$element_position', '—', '待取捨', '列表位置；若行銷要看資源位點擊率，改在 App 自訂參數 position'),
    ('event', '$viewport_width', '—', '待取捨', 'GA 不匯出；Web 可自訂參數'),
    ('event', '$viewport_height', '—', '待取捨', ''),
    ('event', '$viewport_position', 'scroll 事件 percent_scrolled', '改口徑', 'GA 加強型評估只在捲到 90% 送一次；神策是每次事件的已瀏覽長度'),
    # --- 事件屬性：裝置與環境
    ('event', '$os', 'device.operating_system', '直接對應', ''),
    ('event', '$os_version', 'device.operating_system_version', '直接對應', ''),
    ('event', '$manufacturer', 'device.mobile_brand_name', '直接對應', ''),
    ('event', '$brand', 'device.mobile_brand_name', '改口徑', '神策 Android 另存 brand；GA 只有一個品牌欄位，合併'),
    ('event', '$model', 'device.mobile_model_name', '直接對應', ''),
    ('event', '$browser', 'device.web_info.browser', '直接對應', ''),
    ('event', '$browser_version', 'device.web_info.browser_version', '直接對應', ''),
    ('event', '$screen_width', 'event_params.screen_resolution（Web）', '改口徑', 'Web 由 screen_resolution 拆；App 無，GA 不匯出'),
    ('event', '$screen_height', 'event_params.screen_resolution（Web）', '改口徑', '同上'),
    ('event', '$screen_orientation', '—', '待取捨', 'App 自訂參數或放棄'),
    ('event', '$network_type', '—', '待取捨', 'GA 不匯出網路類型；風控若要用改在 App 自訂'),
    ('event', '$wifi', '—', '待取捨', '同上'),
    ('event', '$carrier', '—', '待取捨', 'GA 不匯出電信商'),
    ('event', '$timezone_offset', '—', '捨棄', 'DMP 固定 Asia/Taipei'),
    ('event', '$country', 'geo.country', '直接對應', '口徑改為 GA 的 IP 判定'),
    ('event', '$province', 'geo.region', '直接對應', ''),
    ('event', '$city', 'geo.city', '直接對應', ''),
    ('event', '$ip', '—', '捨棄', 'GA 不匯出 IP；DMP 本來就不入倉'),
    ('event', '$app_id', 'app_info.id', '直接對應', ''),
    ('event', '$app_name', 'app_info.id 對照表', 'DMP 推導', 'GA 無 App 名稱，以 app_id 對照'),
    ('event', '$app_version', 'app_info.version', '直接對應', ''),
    ('event', '$lib', 'platform（WEB／IOS／ANDROID）', '直接對應', '神策 SDK 類型 ≈ GA platform'),
    ('event', '$lib_version', '—', '捨棄', 'SDK 內部'),
    ('event', '$lib_method', '—', '捨棄', '神策預設／自訂埋點區分，GA 無此概念'),
    ('event', '$lib_detail', '—', '捨棄', '神策可視化埋點細節'),
    ('event', '$lib_plugin_version', '—', '捨棄', ''),
    ('event', '$app_remote_config', '—', '捨棄', '神策遠端配置'),
    ('event', '$bot_name', '—', '捨棄', 'GA 預設過濾已知爬蟲'),
    ('event', '$sat_has_installed_app', '—', '待取捨', '神策廣告歸因（SAT）欄位；GA 以 first_open 的 campaign 歸因，口徑不同'),
    ('event', '$resume_from_background', '自訂事件 app_start 參數 from_background', '改口徑', 'GA session_start 不分冷啟動與背景喚醒'),
    ('event', '$event_duration', 'event_params.engagement_time_msec', '改口徑', '神策＝$AppEnd／$WebStay 的停留時長；GA＝互動時間，分析時改用 session 口徑；自訂 app_end 可帶 duration'),
    ('event', '$is_first_day', 'first_open／ga_session_number=1', '改口徑', '神策以「首次觸發日」判定；GA 以首次 session 推導'),
    ('event', '$is_first_time', 'ga_session_number=1', '改口徑', ''),
    # --- 事件屬性：身分與系統
    ('event', '$device_id', 'user_pseudo_id（app_instance_id／client_id）', '身分對應', '進 id_map 的 $identity_device_id／$identity_cookie_id'),
    ('event', '$is_login_id', 'user_id IS NOT NULL', 'DMP 推導', ''),
    ('event', '$track_signup_original_id', '登入前的 user_pseudo_id', '身分對應', 'IDM 2.0 匿名→登入綁定；GA 端靠登入時同一 user_pseudo_id 帶 user_id，DMP 回溯 7 天'),
    ('event', '$idmap_reason', '—', 'DMP 推導', 'OneID 自己記錄合併原因'),
    ('event', '$receive_time', 'ingest_time（DMP）', 'DMP 推導', ''),
    ('event', '$create_time', '—', '捨棄', '神策內部建立時間'),
    ('event', '$update_time', 'item 表 updated_at', 'DMP 推導', '物品檔（維度表）更新時間'),
    ('event', '$hdfs_import_batch_name', '—', '捨棄', '客戶標 (X) 數據清理用'),
    ('event', '$kafka_offset', '—', '捨棄', '同上'),
    # --- 客戶「静默创建／最新一次」自動屬性（神策渠道追蹤插件建立，含義待客戶說明）
    ('event', 'strWeb', '—', '待取捨', '「静默创建」屬性：神策自動建立，含義請客戶確認；若是開戶來源可由 (C)WebCn 取代'),
    ('event', '_latest_strWeb', '—', '待取捨', '「最新一次」版本；DMP 可用最近規則重算'),
    ('event', 'ivKey', '—', '捨棄', '静默创建；短網址／開戶驗證的加密參數，不入倉、不送 GA（sensors_properties.csv drop_ops）'),
    ('event', 'hashKey', '—', '捨棄', '同上'),
    ('event', 'svid', '—', '捨棄', '同上'),
    ('event', '_latest_ivKey', '—', '捨棄', '最新一次版本'),
    ('event', '_latest_hashKey', '—', '捨棄', ''),
    ('event', '_latest_svid', '—', '捨棄', ''),
    ('event', 'dd', '—', '待取捨', '静默创建；含義待確認'),
    ('event', '_latest_dd', '—', '待取捨', ''),
    # --- 智能運營（SF）：GA360 無對應，只匯歷史
    *[('event', k, '—', '只匯歷史', '神策智能運營欄位；新 MA 工具與 LINE 不在本案') for k in (
        '$sf_qrcode_channel_id', '$sf_mark_tag_name', '$sf_mark_action_type', '$sf_template_id', '$sf_exit_reason', '$sf_channel_task_id',
        '$sf_section_id', '$sf_relevance_properties_value', '$sf_event_receive_time', '$sf_enter_strategy_unit_time', '$sf_from_component_id',
        '$sf_send_time', '$sf_project_id', '$sf_not_send_reason', '$sf_component_id', '$sf_plan_version', '$sf_conversion_attribution',
        '$sf_send_fail_code', '$sf_send_id', '$sf_keyword', '$sf_keyword_rule_id', '$sf_msg_status', '$sf_close_type', '$sf_msg_action_id',
        '$sf_platform_tag', '$sf_fail_reason', '$sf_convert_id', '$sf_enter_plan_time', '$sf_msg_id', '$sf_msg_content', '$sf_audience_id',
        '$sf_succeed', '$sf_channel_category', '$sf_channel_service_name', '$sf_plan_type', '$sf_channel_id', '$sf_plan_strategy_id',
        '$sf_plan_id', '$sf_strategy_unit_id', 'sf_plan_name', 'sf_strategy_unit_name', 'rec_banner_id', 'strategy_id', 'section_id',
        '$channel_link_id', '$matching_key_list', '$utm_matching_type')],
    ('event', '$sf_msg_recipient', '—', '捨棄', '收件人（L4）'),
    # --- 預置事件
    ('event_name', '$pageview', 'page_view', '直接對應', 'gtag 自動'),
    ('event_name', '$WebClick', '自訂 web_click', '改口徑', 'GA click 只收外連；全站點擊自訂事件，帶 element_id／element_content／page_group'),
    ('event_name', '$AppViewScreen', 'screen_view', '直接對應', 'Firebase 自動'),
    ('event_name', '$AppStart', '自訂 app_start', '改口徑', 'GA session_start 口徑是 30 分鐘閒置；保留自訂 app_start 銜接神策「啟動」指標'),
    ('event_name', '$AppEnd', '自訂 app_end', '改口徑', '帶 duration 對應 $event_duration'),
    ('event_name', '$AppClick', '自訂 app_click', '改口徑', ''),
    ('event_name', '$AppPushClick', 'notification_open', '直接對應', 'FCM 推播自動；非 FCM 改自訂 push_click'),
    ('event_name', '$ShortUrlRedirect', 'short_url_redirect（MP）', '直接對應', '短鏈服務以 Measurement Protocol 上報'),
    *[('event_name', k, '—', '只匯歷史', '神策智能運營系統事件') for k in (
        '$PlanSMSArrived', '$PlanAudienceMarked', '$JourneyExit', '$JourneyComponentEnter', '$PlanEDMClick', '$PlanEDMShow', '$PlanConverted',
        '$PlanAudienceEnter', '$PlanMsgSendPrepare', '$PlanMsgWouldNotSend', '$PlanPopupDisplay', '$PlanMsgSendDone', '$PlanMsgArrived', '$PlanPopupClick')],
    # --- 用戶表（IDM 2.0 與預置用戶屬性）
    ('user', 'id', 'OneID one_id', '身分對應', '神策內部數字 ID 不遷，id_map 另給'),
    ('user', 'first_id', 'user_pseudo_id', '身分對應', '匿名 ID → $identity_cookie_id／$identity_device_id'),
    ('user', 'second_id', 'user_id（HMAC 客戶代碼）', '身分對應', '登入 ID → $identity_login_id'),
    ('user', '$is_deleted', 'DSAR 刪除流程', 'DMP 推導', ''),
    ('user', 'svid', '—', '捨棄', '規格表用戶表才有（導出無）；加密金鑰材料，不入用戶表'),
    ('user', 'dd', '—', '待取捨', '規格表用戶表才有（導出無）；静默创建、第一次激活時寫入，含義待客戶確認'),
    ('user', '$device_id_list', 'id_map（$identity_device_id）', 'DMP 推導', '規格表用戶表才有（導出無）；關聯裝置清單由 OneID 的 id_map 推導，不另存'),
    ('user', '$update_time', 'users.updated_at', 'DMP 推導', ''),
    ('user', '$first_visit_time', 'user_first_touch_timestamp', '直接對應', ''),
    ('user', '$utm_source', 'traffic_source.source', '直接對應', '用戶級首次來源'),
    ('user', '$utm_medium', 'traffic_source.medium', '直接對應', ''),
    ('user', '$utm_campaign', 'traffic_source.name', '直接對應', ''),
    ('user', '$utm_term', '首次 session 的 manual_term', 'DMP 推導', 'GA 用戶級 traffic_source 沒有 term／content'),
    ('user', '$utm_content', '首次 session 的 manual_content', 'DMP 推導', ''),
    ('user', '$first_referrer', '首次 session 的 page_referrer', 'DMP 推導', ''),
    ('user', '$first_referrer_host', '同上拆 host', 'DMP 推導', ''),
    ('user', '$first_search_keyword', '—', 'DMP 推導', '覆蓋率極低，建議不列入報表'),
    ('user', '$first_traffic_source_type', '由首次 medium 分類', 'DMP 推導', ''),
    ('user', '$first_browser_language', 'device.language', '直接對應', ''),
    ('user', '$first_browser_charset', '—', '捨棄', ''),
    *[('user', k, 'DMP 維度字典（代碼 → 名稱）', 'DMP 推導', '神策維度字典欄位；代碼表請客戶匯出') for k in (
        '$dimension_dict_sleepycust_group', '$dimension_dict_s_sex', '$dimension_dict_cuaa1', '$dimension_dict_cuaa2', '$dimension_dict_cuaa3',
        '$dimension_dict_cuaa4', '$dimension_dict_cuaf9')],
]

SPECIFIC_NAMES = {'strWeb', '_latest_strWeb', 'ivKey', 'hashKey', 'svid', '_latest_ivKey', '_latest_hashKey', '_latest_svid', 'dd', '_latest_dd',
                  'sf_plan_name', 'sf_strategy_unit_name', 'rec_banner_id', 'strategy_id', 'section_id', 'id', 'first_id', 'second_id'}


def is_specific(name: str) -> bool:
    return name.startswith(('$', '_latest_')) or name in SPECIFIC_NAMES


def rows() -> list[dict]:
    return [{'scope': s, 'field': f, 'ga360': g, 'disposition': d, 'note': n} for s, f, g, d, n in SPEC]


def selftest() -> None:
    assert all(d in DISPOSITIONS for _, _, _, d, _ in SPEC)
    keys = [(s, f) for s, f, *_ in SPEC]
    assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]
    assert dict(((s, f), d) for s, f, _, d, _ in SPEC)[('event', '$ip')] == '捨棄'


def coverage() -> list[str]:
    """客戶字典裡每個神策獨有字段都要有處置；回傳缺漏。"""
    have = {(s, f) for s, f, *_ in SPEC}
    missing = []
    if EVENT_PROPS_CSV.exists():
        with EVENT_PROPS_CSV.open(encoding='utf-8-sig', newline='') as fh:
            missing += [f'event:{r["property"]}' for r in csv.DictReader(fh) if is_specific(r['property']) and ('event', r['property']) not in have]
    if USER_PROPS_CSV.exists():
        with USER_PROPS_CSV.open(encoding='utf-8-sig', newline='') as fh:
            missing += [f'user:{r["property"]}' for r in csv.DictReader(fh) if is_specific(r['property']) and ('user', r['property']) not in have]
    return missing


def main() -> int:
    selftest()
    out = rows()
    counts: dict[str, int] = {}
    for r in out:
        counts[r['disposition']] = counts.get(r['disposition'], 0) + 1
    print(f'sensors_specific={len(out)} ' + ' '.join(f'{d}={counts.get(d, 0)}' for d in DISPOSITIONS))
    missing = coverage()
    if missing:
        print('ISSUE 客戶字典裡未列處置：' + ', '.join(missing))
    if '--check' in sys.argv:
        with OUT_CSV.open(encoding='utf-8-sig', newline='') as fh:
            cur = [dict(r) for r in csv.DictReader(fh)]
        if cur != out:
            print('ISSUE sensors_specific_fields.csv 與程式不一致，請重跑'); return 1
        return 1 if missing else 0
    with OUT_CSV.open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f'wrote {OUT_CSV.relative_to(ROOT.parent)}')
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
