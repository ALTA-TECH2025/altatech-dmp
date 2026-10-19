# 舊平台獨有字段：GA360 對應與取捨

> 版本 v0.1（2026-10-17）。從客戶兩份字典（事件屬性 681、用戶屬性 3,606）與舊平台預置事件挑出「只有舊平台才有」的字段：預置 `$` 屬性、智能運營 `$sf_`、IDM 身分欄位、渠道追蹤插件自動建立的「静默创建／最新一次」屬性、預置事件。每一項給 GA360 BigQuery 匯出的對應欄位與處置。來源與規則：`backend/migration/legacy_specific.py`，產出 `backend/migration/legacy_specific_fields.csv`，CI `--check` 會確認客戶字典裡每個舊平台獨有字段都有處置。

## 0. 總表

| 處置 | 數量 | 意思 |
|---|---|---|
| 直接對應 | 32 | GA360 BigQuery 匯出有同義欄位，接入時改名（`backend/semantic/ga4_mapping.yaml`）。 |
| 改口徑 | 24 | GA 有近似欄位但定義不同。報表口徑會變，要在平行運行期對帳並向業務單位說明；多數靠自訂事件／參數補。 |
| DMP 推導 | 26 | GA 不給，但 DMP 能由其他欄位算出（拆 URL、首次／最近來源、登入狀態、維度字典）。 |
| 身分對應 | 5 | 舊平台 IDM 2.0 的 ID 欄位，由 OneID 與 id_map 取代（docs/19、docs/20）。 |
| 待取捨 | 21 | GA 不收、也推不出來。要嘛在 App／Web 加自訂參數，要嘛放棄；請客戶逐項決定。 |
| 只匯歷史 | 61 | 舊平台智能運營（SF）專屬，GA360 無對應；新 MA 工具與 LINE 不在本案，只保留歷史供分析。 |
| 捨棄 | 13 | SDK 內部、維運、IP 類，不遷。 |
| 合計 | 182 | 事件屬性 136、預置事件 22、用戶屬性 24 |

**未來要改的（週一事件設計與平行運行期要處理）**：

1. **改口徑 24 項**是報表數字會變的地方：`$latest_utm_*` 從「30 天內最近一次外部來源」變成「本次 session 最後點擊」；`$event_duration` 從停留時長變成互動時間；`$AppStart` 與 GA `session_start` 口徑不同；全站點擊 `$WebClick`／`$AppClick` 與 `$element_*` 要改成自訂事件帶參數（GA 自動只收外連點擊）。這些要在 docs/22 的平行運行對帳表加對照欄。
2. **待取捨 21 項**要客戶拍板：螢幕尺寸與方向、網路類型、電信商、CSS 選擇器／元素路徑／元素位置、前頁標題、廣告歸因 `$sat_has_installed_app`，以及 10 個「静默创建／最新一次」自動屬性（strWeb、ivKey、hashKey、svid、dd）含義不明。原則：行銷要用的加自訂參數，其餘放棄。
3. **只匯歷史 61 項**全是智能運營：T0 後沒有新資料，分析頁面要能標示「資料截止 T0」。
4. **身分對應 5 項**走 OneID：`$device_id`／`first_id` → user_pseudo_id，`second_id` → HMAC 後的 user_id，`$track_signup_original_id` 的匿名回溯由 DMP 做（7 天）。

## 1. 直接對應（32）

GA360 BigQuery 匯出有同義欄位，接入時改名（`backend/semantic/ga4_mapping.yaml`）。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$url` | event_params.page_location |  |
| 事件屬性 | `$title` | event_params.page_title |  |
| 事件屬性 | `$referrer` | event_params.page_referrer |  |
| 事件屬性 | `$screen_name` | event_params.firebase_screen | firebase_screen_class 對應畫面類別 |
| 事件屬性 | `$utm_source` | collected_traffic_source.manual_source | 事件級即時來源 |
| 事件屬性 | `$utm_medium` | collected_traffic_source.manual_medium |  |
| 事件屬性 | `$utm_campaign` | collected_traffic_source.manual_campaign_name |  |
| 事件屬性 | `$utm_term` | collected_traffic_source.manual_term |  |
| 事件屬性 | `$utm_content` | collected_traffic_source.manual_content |  |
| 事件屬性 | `$short_url_key` | Measurement Protocol 自訂參數 | 短鏈服務以 MP 上報 short_url_redirect |
| 事件屬性 | `$short_url_target` | Measurement Protocol 自訂參數 |  |
| 事件屬性 | `$os` | device.operating_system |  |
| 事件屬性 | `$os_version` | device.operating_system_version |  |
| 事件屬性 | `$manufacturer` | device.mobile_brand_name |  |
| 事件屬性 | `$model` | device.mobile_model_name |  |
| 事件屬性 | `$browser` | device.web_info.browser |  |
| 事件屬性 | `$browser_version` | device.web_info.browser_version |  |
| 事件屬性 | `$country` | geo.country | 口徑改為 GA 的 IP 判定 |
| 事件屬性 | `$province` | geo.region |  |
| 事件屬性 | `$city` | geo.city |  |
| 事件屬性 | `$app_id` | app_info.id |  |
| 事件屬性 | `$app_version` | app_info.version |  |
| 事件屬性 | `$lib` | platform（WEB／IOS／ANDROID） | 舊平台 SDK 類型 ≈ GA platform |
| 預置事件 | `$pageview` | page_view | gtag 自動 |
| 預置事件 | `$AppViewScreen` | screen_view | Firebase 自動 |
| 預置事件 | `$AppPushClick` | notification_open | FCM 推播自動；非 FCM 改自訂 push_click |
| 預置事件 | `$ShortUrlRedirect` | short_url_redirect（MP） | 短鏈服務以 Measurement Protocol 上報 |
| 用戶屬性 | `$first_visit_time` | user_first_touch_timestamp |  |
| 用戶屬性 | `$utm_source` | traffic_source.source | 用戶級首次來源 |
| 用戶屬性 | `$utm_medium` | traffic_source.medium |  |
| 用戶屬性 | `$utm_campaign` | traffic_source.name |  |
| 用戶屬性 | `$first_browser_language` | device.language |  |

## 2. 改口徑（24）

GA 有近似欄位但定義不同。報表口徑會變，要在平行運行期對帳並向業務單位說明；多數靠自訂事件／參數補。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$latest_utm_source` | session_traffic_source_last_click.manual_campaign.source | 舊平台＝30 天內最近一次外部來源；GA＝本次 session 最後點擊。要用舊平台口徑由 DMP 以事件序列重算 |
| 事件屬性 | `$latest_utm_medium` | session_traffic_source_last_click.manual_campaign.medium | 同上 |
| 事件屬性 | `$latest_utm_campaign` | session_traffic_source_last_click.manual_campaign.campaign_name | 同上 |
| 事件屬性 | `$latest_utm_term` | session_traffic_source_last_click.manual_campaign.term | 同上 |
| 事件屬性 | `$latest_utm_content` | session_traffic_source_last_click.manual_campaign.content | 同上 |
| 事件屬性 | `$deeplink_url` | App 自訂參數 deeplink_url | Firebase 只有 dynamic_link_* 事件；一般 deeplink 要在 App 自訂上報 |
| 事件屬性 | `$element_id` | 自訂事件 web_click／app_click 的 element_id | GA 自動只收外連 click（link_url／link_text／link_id／link_classes）；全站點擊要自訂事件 |
| 事件屬性 | `$element_content` | 自訂事件參數 element_content |  |
| 事件屬性 | `$element_name` | 自訂事件參數 element_name |  |
| 事件屬性 | `$element_type` | 自訂事件參數 element_type |  |
| 事件屬性 | `$element_class_name` | 自訂事件參數 element_class |  |
| 事件屬性 | `$element_target_url` | link_url（外連）／自訂參數 |  |
| 事件屬性 | `$viewport_position` | scroll 事件 percent_scrolled | GA 加強型評估只在捲到 90% 送一次；舊平台是每次事件的已瀏覽長度 |
| 事件屬性 | `$brand` | device.mobile_brand_name | 舊平台 Android 另存 brand；GA 只有一個品牌欄位，合併 |
| 事件屬性 | `$screen_width` | event_params.screen_resolution（Web） | Web 由 screen_resolution 拆；App 無，GA 不匯出 |
| 事件屬性 | `$screen_height` | event_params.screen_resolution（Web） | 同上 |
| 事件屬性 | `$resume_from_background` | 自訂事件 app_start 參數 from_background | GA session_start 不分冷啟動與背景喚醒 |
| 事件屬性 | `$event_duration` | event_params.engagement_time_msec | 舊平台＝$AppEnd／$WebStay 的停留時長；GA＝互動時間，分析時改用 session 口徑；自訂 app_end 可帶 duration |
| 事件屬性 | `$is_first_day` | first_open／ga_session_number=1 | 舊平台以「首次觸發日」判定；GA 以首次 session 推導 |
| 事件屬性 | `$is_first_time` | ga_session_number=1 |  |
| 預置事件 | `$WebClick` | 自訂 web_click | GA click 只收外連；全站點擊自訂事件，帶 element_id／element_content／page_group |
| 預置事件 | `$AppStart` | 自訂 app_start | GA session_start 口徑是 30 分鐘閒置；保留自訂 app_start 銜接舊平台「啟動」指標 |
| 預置事件 | `$AppEnd` | 自訂 app_end | 帶 duration 對應 $event_duration |
| 預置事件 | `$AppClick` | 自訂 app_click |  |

## 3. DMP 推導（26）

GA 不給，但 DMP 能由其他欄位算出（拆 URL、首次／最近來源、登入狀態、維度字典）。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$url_path` | page_location 拆 path | 接入時以 SQL 拆解 |
| 事件屬性 | `$url_host` | page_location 拆 host |  |
| 事件屬性 | `$referrer_host` | page_referrer 拆 host |  |
| 事件屬性 | `$latest_referrer` | page_referrer（session 首頁） | 以 session 第一個 page_view 的 page_referrer 重算 |
| 事件屬性 | `$latest_search_keyword` | — | 由 referrer 的查詢字串解析；主流搜尋引擎已不帶關鍵字，覆蓋率會很低 |
| 事件屬性 | `$latest_traffic_source_type` | — | 由 medium／referrer 分類（自然搜尋、社交、付費、直接） |
| 事件屬性 | `$app_name` | app_info.id 對照表 | GA 無 App 名稱，以 app_id 對照 |
| 事件屬性 | `$is_login_id` | user_id IS NOT NULL |  |
| 事件屬性 | `$idmap_reason` | — | OneID 自己記錄合併原因 |
| 事件屬性 | `$receive_time` | ingest_time（DMP） |  |
| 事件屬性 | `$update_time` | item 表 updated_at | 物品檔（維度表）更新時間 |
| 用戶屬性 | `$is_deleted` | DSAR 刪除流程 |  |
| 用戶屬性 | `$update_time` | users.updated_at |  |
| 用戶屬性 | `$utm_term` | 首次 session 的 manual_term | GA 用戶級 traffic_source 沒有 term／content |
| 用戶屬性 | `$utm_content` | 首次 session 的 manual_content |  |
| 用戶屬性 | `$first_referrer` | 首次 session 的 page_referrer |  |
| 用戶屬性 | `$first_referrer_host` | 同上拆 host |  |
| 用戶屬性 | `$first_search_keyword` | — | 覆蓋率極低，建議不列入報表 |
| 用戶屬性 | `$first_traffic_source_type` | 由首次 medium 分類 |  |
| 用戶屬性 | `$dimension_dict_sleepycust_group` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |
| 用戶屬性 | `$dimension_dict_s_sex` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |
| 用戶屬性 | `$dimension_dict_cuaa1` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |
| 用戶屬性 | `$dimension_dict_cuaa2` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |
| 用戶屬性 | `$dimension_dict_cuaa3` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |
| 用戶屬性 | `$dimension_dict_cuaa4` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |
| 用戶屬性 | `$dimension_dict_cuaf9` | DMP 維度字典（代碼 → 名稱） | 舊平台維度字典欄位；代碼表請客戶匯出 |

## 4. 身分對應（5）

舊平台 IDM 2.0 的 ID 欄位，由 OneID 與 id_map 取代（docs/19、docs/20）。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$device_id` | user_pseudo_id（app_instance_id／client_id） | 進 id_map 的 $identity_device_id／$identity_cookie_id |
| 事件屬性 | `$track_signup_original_id` | 登入前的 user_pseudo_id | IDM 2.0 匿名→登入綁定；GA 端靠登入時同一 user_pseudo_id 帶 user_id，DMP 回溯 7 天 |
| 用戶屬性 | `id` | OneID one_id | 舊平台內部數字 ID 不遷，id_map 另給 |
| 用戶屬性 | `first_id` | user_pseudo_id | 匿名 ID → $identity_cookie_id／$identity_device_id |
| 用戶屬性 | `second_id` | user_id（HMAC 客戶代碼） | 登入 ID → $identity_login_id |

## 5. 待取捨（21）

GA 不收、也推不出來。要嘛在 App／Web 加自訂參數，要嘛放棄；請客戶逐項決定。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$referrer_title` | — | GA 不收前頁標題；可由前一個 page_view 的 page_title 補，或放棄 |
| 事件屬性 | `$element_selector` | — | CSS 選擇器只有舊平台可視化埋點用；GA 自訂參數值 ≤ 100 字元，建議放棄 |
| 事件屬性 | `$element_path` | — | 同上 |
| 事件屬性 | `$element_position` | — | 列表位置；若行銷要看資源位點擊率，改在 App 自訂參數 position |
| 事件屬性 | `$viewport_width` | — | GA 不匯出；Web 可自訂參數 |
| 事件屬性 | `$viewport_height` | — |  |
| 事件屬性 | `$screen_orientation` | — | App 自訂參數或放棄 |
| 事件屬性 | `$network_type` | — | GA 不匯出網路類型；風控若要用改在 App 自訂 |
| 事件屬性 | `$wifi` | — | 同上 |
| 事件屬性 | `$carrier` | — | GA 不匯出電信商 |
| 事件屬性 | `$sat_has_installed_app` | — | 舊平台廣告歸因（SAT）欄位；GA 以 first_open 的 campaign 歸因，口徑不同 |
| 事件屬性 | `strWeb` | — | 「静默创建」屬性：舊平台自動建立，含義請客戶確認；若是開戶來源可由 (C)WebCn 取代 |
| 事件屬性 | `_latest_strWeb` | — | 「最新一次」版本；DMP 可用最近規則重算 |
| 事件屬性 | `ivKey` | — | 静默创建；疑為開戶／驗證用金鑰，L3 代碼化 |
| 事件屬性 | `hashKey` | — | 同上 |
| 事件屬性 | `svid` | — | 同上 |
| 事件屬性 | `_latest_ivKey` | — | 最新一次版本 |
| 事件屬性 | `_latest_hashKey` | — |  |
| 事件屬性 | `_latest_svid` | — |  |
| 事件屬性 | `dd` | — | 静默创建；含義待確認 |
| 事件屬性 | `_latest_dd` | — |  |

## 6. 只匯歷史（61）

舊平台智能運營（SF）專屬，GA360 無對應；新 MA 工具與 LINE 不在本案，只保留歷史供分析。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$sf_qrcode_channel_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_mark_tag_name` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_mark_action_type` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_template_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_exit_reason` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_channel_task_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_section_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_relevance_properties_value` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_event_receive_time` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_enter_strategy_unit_time` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_from_component_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_send_time` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_project_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_not_send_reason` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_component_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_plan_version` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_conversion_attribution` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_send_fail_code` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_send_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_keyword` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_keyword_rule_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_msg_status` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_close_type` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_msg_action_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_platform_tag` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_fail_reason` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_convert_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_enter_plan_time` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_msg_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_msg_content` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_audience_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_succeed` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_channel_category` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_channel_service_name` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_plan_type` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_channel_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_plan_strategy_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_plan_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$sf_strategy_unit_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `sf_plan_name` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `sf_strategy_unit_name` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `rec_banner_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `strategy_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `section_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$channel_link_id` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$matching_key_list` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 事件屬性 | `$utm_matching_type` | — | 舊平台智能運營欄位；新 MA 工具與 LINE 不在本案 |
| 預置事件 | `$PlanSMSArrived` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanAudienceMarked` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$JourneyExit` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$JourneyComponentEnter` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanEDMClick` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanEDMShow` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanConverted` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanAudienceEnter` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanMsgSendPrepare` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanMsgWouldNotSend` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanPopupDisplay` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanMsgSendDone` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanMsgArrived` | — | 舊平台智能運營系統事件 |
| 預置事件 | `$PlanPopupClick` | — | 舊平台智能運營系統事件 |

## 7. 捨棄（13）

SDK 內部、維運、IP 類，不遷。

| 範圍 | 舊平台字段 | GA360 對應 | 說明 |
|---|---|---|---|
| 事件屬性 | `$timezone_offset` | — | DMP 固定 Asia/Taipei |
| 事件屬性 | `$ip` | — | GA 不匯出 IP；DMP 本來就不入倉 |
| 事件屬性 | `$lib_version` | — | SDK 內部 |
| 事件屬性 | `$lib_method` | — | 舊平台預設／自訂埋點區分，GA 無此概念 |
| 事件屬性 | `$lib_detail` | — | 舊平台可視化埋點細節 |
| 事件屬性 | `$lib_plugin_version` | — |  |
| 事件屬性 | `$app_remote_config` | — | 舊平台遠端配置 |
| 事件屬性 | `$bot_name` | — | GA 預設過濾已知爬蟲 |
| 事件屬性 | `$create_time` | — | 舊平台內部建立時間 |
| 事件屬性 | `$hdfs_import_batch_name` | — | 客戶標 (X) 數據清理用 |
| 事件屬性 | `$kafka_offset` | — | 同上 |
| 事件屬性 | `$sf_msg_recipient` | — | 收件人（L4） |
| 用戶屬性 | `$first_browser_charset` | — |  |
