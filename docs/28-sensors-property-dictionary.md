# 客戶神策事件屬性字典：分級、入倉決定與 GA4 參數名

> 版本 v0.2（2026-10-16）。來源：客戶提供的神策「批量修改資訊檔」（事件屬性命名表：屬性名／顯示名，681 個屬性）與「用戶屬性導出」（name／type，3,606 個用戶屬性，§7）。規則與產出都在 `backend/migration/properties.py`，產出檔 `backend/migration/sensors_properties.csv`（property, display, tag, domain, sensitivity, decision, ga4_param, rule）。客戶原檔不進 repo；`--check` 以產出檔前兩欄重算比對，CI 每次跑。口徑：docs/10 §欄位等級、docs/22 §3.3。

## 1. 結論

| 項目 | 數字 |
|---|---|
| 屬性總數 | 681 |
| L4 可識別 → 接入預處理丟棄（已登記 `user_properties.yaml forbidden_l4`） | 30 |
| L3 代碼化識別 → 入倉前 token_v1，查詢尾碼遮罩 | 31 |
| L2 敏感業務 → 入倉，按角色遮罩／分桶，匯出降一級 | 392 |
| L1 行為 → 入倉 | 208 |
| (X) 維護／清理欄位與 IP → 不入倉 | 20 |
| 來源 domain：數據中台批次（交易／帳務／開戶／CRM，全大寫或 (O)(C)(B) 前綴） | 370 |
| 來源 domain：App／Web 埋點（經 GA360，給 ga4_param） | 263 |
| 來源 domain：神策智能運營 SF 事件（只匯入歷史） | 48 |

客戶顯示名的前綴是他們自己的標記，照用：`(O)` 舊版欄位 53、`(C)` CRM／開戶 50、`(B)` 借貸 18、`(N)` NPS 5、`(L)` 學堂 1、`(X)` 維護 18、`(SF)` 智能運營 33、`#` 自訂 84。

## 2. L4：永不入倉（30）

姓名：CNAME、S_NAME、Name、BE_EMP_NAME、SALE_EMP_NAME、ADNO_Name、om_user_nm。手機／電話：CUS_MTEL、Mobile、member_phone、latest_member_phone、task_mobile、order_phone。信箱／收件人：CUADM、EMail、member_email、latest_member_email、task_mail、order_email、coupon_email、SNSLoginEmail、$sf_msg_recipient。生日：birthdt_evlogin、birthDT_ConfirmID。客戶銀行／信託／分戶帳完整帳號：BANK_ACNO_1、BANK_ACNO_2、IO_BANK_ACNO、PO_BANK_ACNO、TRUST_ACNO、Bankact。

執行點：`backend/integration/sensors_format.py stage()` 讀 `forbidden_l4` 逐行丟棄（神策歷史與線下格式檔都走這裡）；GA360 端這些欄位本來就不該埋（docs/20 §3.3 個資閘門）；語意層 `catalog.py` 對 `user.<prop>` 查詢直接拒絕。員工姓名（介紹人、營業員、審核員工）也視為個資，分析用員工編號（L3）即可。

## 3. L3：代碼化後入倉（31）

客戶／會員識別：UUID、S_IDNO（去識別化 ID）、PART_IDNO、member_id、gift_card_recipient、gift_card_recipient_acno、gift_card_recipient_unicode。證券／信用帳號：S_ACNO、AccountNo、MT_ACNO、BROKERNO3_MT_ACNO、HG105、ANTE_BNK_ACC。員工編號：BE_EMP_NO、SALE_EMP_NO 系列（SALE_EMP_BELONG、SALE_EMP_BELON、SALE_NO_BELONG）、SINO_EMP_NO、ADNO、SALE_NO、om_user_id、O_MM_EMP_NO、HAA11。裝置與匿名關聯：$device_id、$track_signup_original_id、hashKey／ivKey／svid 與其 _latest_ 版本。

帳戶層級分析（需求 B2「一人多帳戶」）靠 S_ACNO／AccountNo 的 token 做分組，不需要明文。**待做**：`stage()` 目前只 token 化 distinct_id，屬性層的 L3 token 化（依本表 decision=hash）要補進預處理，並在 `masking.yaml` 登記尾碼遮罩；排 M2。

## 4. GA4 參數名

App／Web 埋點的 263 個屬性給 `ga4_param`（snake_case、≤ 40 字元，規則同 `bridge.py`）。客戶以尾底線標示「(新)」版本（dwf_appclick_name_ 等 3 個），GA 端命名為 `_new` 後綴避免與舊版同名。碰撞 1 個待客戶決定：`purchaset_date` 與 `PurchasetDate`（同義、拼法不同）建議合併為一個。

## 5. 要問客戶的

1. **事件 × 屬性矩陣**：這份只有屬性清單，沒有「哪個事件帶哪些屬性」。請客戶從神策元數據匯出事件屬性關聯（或給 `events` 表的 schema），才能把每個事件的 GA 參數數（≤ 25）與 user property 數（≤ 25）算準。
2. **UUID、S_IDNO、member_id 三者關係**：哪一個是 CDP 的去識別化客戶 ID（需求 B3）？目前假設 S_IDNO = CDP ID，UUID = 神策自己的用戶 ID，member_id = 學堂／商城會員帳號。
3. **(O) 舊版欄位**是否仍在寫入，或已被無前綴的新欄位取代（例：MTH_AMT vs MTH_AMT_TW／MTH_AMT_O）；只匯歷史的話載入後不再維護。
4. **(X) 欄位**（維護者、批次、Kafka offset、HDFS 批次名）確認可丟。
5. **自由文字欄位**（msg 備註、Feedback 問卷意見、form_subject_answer）可能夾帶個資，目前列 L2 遮罩；若客戶確認無個資可降 L1。
6. **IP**：$ip 與 dwt_flashorder_ip 一律不入倉；若風控要用閃電下單 IP，改在來源系統做。

## 6. 對應檔案

- `backend/migration/properties.py`：規則、自測、`--check`（事件屬性與用戶屬性兩個產出都比對）。
- `backend/migration/sensors_properties.csv`：事件屬性產出（可直接給客戶與導入商看）。
- `backend/migration/sensors_user_properties.csv`：用戶屬性產出（§7）。
- `backend/semantic/user_properties.yaml`：`forbidden_l4` 含本表 L4 名單。
- `backend/integration/sensors_format.py`：預處理丟棄點。

## 7. 用戶屬性導出（3,606 個）

`python3 backend/migration/properties.py --users <導出.csv>` 產出 `sensors_user_properties.csv`（property, type, family, sensitivity, decision, dmp_target, rule）。

| family | 數量 | 處置 |
|---|---|---|
| `user_group_*`（神策分群回寫欄位，一個分群一欄；含 `user_group_pj2_sf<id>_<ts>` 2,527 個） | 2,758 | **不當用戶屬性遷移**。盤點後只留客戶勾選的分群，以 `/v1/segments/import` 匯入成分群快照（docs/23 §1 分群「匯入分群（神策快照）」）；其餘不載入。 |
| `user_tag_*`（神策標籤回寫欄位） | 691 | 同上，匯入標籤引擎為標籤值；命名是拼音縮寫（`user_tag_a011978tgsmd`…），需要客戶給標籤顯示名對照。 |
| core：L4 可識別 | 33 | 不入用戶表（已登記 `forbidden_l4`）：姓名（s_name、openactname…）、生日（birth_dt、s_birth、s_birth_virtual）、手機（cus_mtel、noticemobile、openactmobile、sleepycust_mobile…）、信箱（cuadm、openactmail、google／fb／apple 登入信箱、ec_sec_acc_email…）。 |
| core：L3 代碼化 | 7 | id、first_id、second_id → `id_map`；s_idno、uuid → `customer_code`／one_id；line_uuid、ec_sec_acc_no → token。 |
| core：L2 敏感業務 | 88 | 資產（st_last_asset_amt…→ `aum`／區間）、信用與借貸（b27_* 22 欄）、各商品線開戶／首交／最近交易日期（st／sub／fu／wf／of）、性別、年齡（s_age → `age_band`）、客戶類別、開戶來源（cuaa1–4、cuaf9）。要登記進 `user_properties.yaml` 才能查（目前只有 17 個鍵），排 M1 契約 PR。 |
| core：L1 行為／偏好 | 29 | 首次來源（$first_*、$utm_*）、社群登入類型、推播同意（is_*_push）、最近登入、沉睡分群、yield_increase、group_tag、ref_flag。 |

要問客戶的（接 §5）：
7. `user_group_*`／`user_tag_*` 三千多欄哪些還在用？請在神策分群／標籤列表勾選必留清單（docs/22 §3 盤點項目），其餘不遷。
8. 用戶屬性導出沒有顯示名（comment 欄全空）；請補匯出含顯示名的版本，L2 的 88 欄才能給中文名登記。
9. `$dimension_dict_cuaa1–4`、`cuaf9`、`otype`、`user_type` 的代碼表（維度字典）請一併匯出，否則只能存代碼。
