# GA360 埋點與交易事件打通：去識別化 ID 的產生、上報與關聯（v1.0，2026-10-15）

> 補充（2026-10-16）：GA 端每個事件要用什麼事件名、帶哪些參數，由 `backend/migration/bridge.py` 從舊平台事件字典產生（`ga360_tracking_spec.csv`），接入時以橋接表把 GA 事件名轉回舊平台正式事件名；新舊資料銜接規則見 docs/22 §2。本文負責身分（客戶代碼）與呼叫方式。
>
> 問題：GA360 的行為事件（官網、交易 App）與來自數據中台／CDP 的交易事件（委託、成交、出入金）要歸到同一個客戶，但兩邊都不能帶明文個資（客戶 ID、身分證、手機）。本文定義「一把金鑰、一個代碼、兩邊同款」的去識別化規則，GA 端怎麼埋點呼叫，交易端怎麼對齊，以及接入後如何關聯（含 docs/19 的登入改寫）。
>
> 來源：GA4 User-ID 實作指南與政策（`user_id` 不得含可直接識別個人的資料、256 字元上限、不回溯套用到先前的匿名事件、登出時清空）、GA4 BigQuery Export 結構（`user_id`、`user_pseudo_id`、`privacy_info`、`events_` 與 `events_intraday_`）、Google「user-provided data」政策（需 SHA-256 雜湊、需同意；本系統不使用）、舊平台 IDM 3.0（`$SignUp`、匿名 ID 重置只能在未登入狀態）。本環境無法開啟 Google 開發者文件原頁，條文以可查到的摘要為準，上線前由法遵再核一次（§7）。

## 1. 結論（一頁版）

| 項目 | 決定 |
|---|---|
| 客戶識別子 | 全系統只有一個代碼化客戶 ID：`cust_code = base32(HMAC-SHA256(k_idm, CUST_ID))[:20]`，前綴版本 `v1_`。GA `user_id`、交易事件的 `$identity_login_id`、CRM 綁定指令、名單推送用的都是**同一個值** |
| 誰算 | 只有 **ID 服務**（`idsvc`，金鑰在 HSM／Vault）會算；App／Web 後端在登入成功時呼叫 `POST /v1/tokens/customer` 取得，再交給前端設成 GA `user_id`。前端與 GA 端永遠拿不到金鑰，也算不出來 |
| 手機 | `mobile_hash = HMAC-SHA256(k_idm_mobile, E.164)`；只在 CRM／開戶流程產生、只進 ID 服務的 `id_map`，**不上報 GA**（GA 不需要手機就能歸戶） |
| 帳號 | `account_no` 走 FPE（FF1，`k_acct`），數據中台交易資料事件化與 CRM 綁定用同一把；**不上報 GA**；GA 事件只帶 `account_type` 等 L1 區間 |
| 未登入 | GA 只有 `user_pseudo_id`（Web：client_id cookie；App：app_instance_id）→ DMP 的 distinct_id → 匿名用戶 |
| 登入 | 前端設 `user_id` 並送 `login` 事件（同時帶 `user_id` 與 `user_pseudo_id`）→ DMP 歸戶 + 近 7 天匿名事件改寫（docs/19 §2.5） |
| 登出 | 前端 `user_id = null`，App 另呼叫 `resetAnalyticsData()` 讓 app_instance_id 換新（同裝置換人不互相污染）；Web 保留 cookie，由「最新綁定者取得」規則處理 |
| 行為 ↔ 交易的關聯 | 只靠同一個客戶代碼歸到同一個 one_id；**不做逐筆對應**，行為事件與交易事件是兩條獨立的事件流（大多數行為事件沒有對應的交易事件），分析時以 one_id 在漏斗／路徑裡串起來 |
| 同意 | Consent Mode：`analytics_storage` 拒絕時 GA 仍送無 cookie 的 ping（`privacy_info.analytics_storage='No'`），DMP 不把弱 ID 與客戶代碼關聯、不改寫 |

## 2. 去識別化規則：GA 端與交易端同款

| 原始欄位（L4） | 代碼化方法 | 金鑰 | 產出 ID | 出現在 |
|---|---|---|---|---|
| CUST_ID（客戶 ID） | HMAC-SHA256 → base32 取 20 碼，前綴 `v1_` | `k_idm`（HSM，年輪替，保留版本） | `$identity_login_id` | GA `user_id`、交易事件 identities、CRM bind、名單推送 `customer_code` |
| 手機 | E.164 正規化 → HMAC-SHA256 | `k_idm_mobile` | `$identity_mobile_hash` | 只在 id_map（CRM／開戶寫入）；GA 不上報 |
| 帳號 | FPE（FF1）保留格式 | `k_acct` | `$identity_account_no` | 交易事件、dim_account 主鍵、CRM bind；GA 不上報 |
| 身分證、姓名、Email、生日、地址 | 不產生任何 ID（Email 僅 CRM 內 HMAC 作比對） | — | — | 永不進 GA、永不進事件表 |
| GA client_id／app_instance_id | 原值（GA 自己產生的隨機 ID，非個資） | — | `$identity_cookie_id`／`$identity_device_id` = distinct_id | GA 匯出、事件表 |
| 廣告 ID（IDFA／GAID） | 丟棄 | — | — | 匯出映射 `device.advertising_id: DROP` |

要點：
- **一把金鑰、一個函式**。`idsvc` 對外只有一個介面 `tokenize(kind, value) → token`；App 後端（登入）、數據中台交易資料事件化（Flink 呼叫 `idsvc` 的 gRPC，批次快取）、CRM 同步任務（同步任務的「代碼化」轉換）都呼叫它。任何一方自己算 HMAC 都是缺陷。
- **金鑰輪替**：新版金鑰上線時 `idsvc` 同時產生 `v1_`／`v2_` 兩個 token 進 `id_map`（同一 one_id），GA 端在下次登入自然換成 `v2_`；`v1_` 保留 13 個月後從 id_map 移除。
- **GA 政策**：`user_id` 必須不可直接識別個人（不能是 Email、姓名、電話、身分證、帳號）；HMAC 代碼符合；長度 ≤ 256。Google 的「user-provided data」（SHA-256 的 Email／電話）是給廣告比對用的，本系統**不啟用**（名單推送不走廣告平台，docs/14）。
- **GA 與交易端一致性驗證**：每日檢查 `GA login 事件的 user_id` 是否都存在於 `id_map 的 login_id`；比例 < 99% 告警（代表 App 端用了錯的金鑰版本或自己算 token）。

## 3. GA 端埋點呼叫（Web gtag／GTM 與 App Firebase）

### 3.1 登入／登出生命週期

```
[未登入]  頁面／App 開啟 → GA 自動 session_start、page_view（只有 user_pseudo_id）
[登入成功] App 後端：token = idsvc.tokenize('customer', CUST_ID)  → 回傳給前端
          Web:  gtag('set', { user_id: token });  gtag('event', 'login', { method: 'password' | 'biometric' | 'otp' })
          App:  FirebaseAnalytics.setUserId(token);  logEvent('login', { method })
          （login 事件同時帶 user_id 與 user_pseudo_id → DMP 的共現資料與登入改寫觸發點）
[登入中]  所有事件自帶 user_id；業務事件參數只放 L0–L2（symbol、market、side、amount_bucket、account_type）
[登出]    Web:  gtag('set', { user_id: null });  gtag('event', 'logout')
          App:  setUserId(null);  logEvent('logout');  resetAnalyticsData()   // 換新的 app_instance_id
```

- `user_id` 不回溯：GA 不會把登入前的 `user_pseudo_id` 事件標上 `user_id`，所以登入前行為的歸戶由 DMP 的登入改寫完成（docs/19 §2.5），不是靠 GA。
- 不要送空字串、"null"、固定值當 `user_id`（會把所有人併成一人）。
- Web 端 `user_id` 要在 GTM 載入前放進 dataLayer（SSR 或 cookie 快取 token，token 本身不是個資，可存前端），避免登入後第一個 page_view 漏掉。
- GA360 的 `user_id` 收集與 BigQuery 匯出是 property 級設定；兩個 App 串流與 Web 串流都開啟 User-ID 與 intraday 匯出。

### 3.2 業務事件參數（埋點規範摘要，完整表在 `backend/semantic/events.yaml`）

| GA 事件 | 必填參數 | 禁止 | 說明 |
|---|---|---|---|
| `view_item`（看商品） | `symbol`, `market` | — | 純行為事件，沒有對應的交易事件 |
| `order_submit_click`（App 按下送單） | `symbol`, `side`, `order_type`, `amount_bucket` | `account_no` 明文、金額精確值 | 行為事件，命名與交易事件 `order_submit`（CDC）區分；委託是否成立以交易事件為準 |
| `order_result_view`（App 看到回報） | `status` | 回報全文 | 行為事件；成交／拒絕數字用交易事件 |
| `fund_deposit_start` | `method`, `amount_bucket` | 銀行帳號 | 行為事件；實際入金用數據中台帳務事實表的 `fund_deposit` |
| `login`／`logout` | `method` | — | `login` 是歸戶與登入改寫的觸發點 |
| `account_open_step` | `step`, `kyc_level` | 任何表單值 | 行為事件；核准以 CRM 的 `account_open_approved` 為準 |

行為事件與交易事件**不做逐筆對應**：行為事件大多沒有對應的交易事件（看行情、看商品、瀏覽活動頁），有的也不需要一對一（按下送單不等於委託成立）。兩條事件流各自入庫，靠同一個 `$identity_login_id` 歸到同一個 one_id，再由漏斗／路徑／歸因以 one_id 串起「看商品 → 送單 → 成交」。GA 的業務事件只要帶 L0–L2 的維度（symbol、side、區間）供分析分組即可。

### 3.3 GTM 伺服器端與個資閘門

GA 流量不經本系統（無雙寫，docs/01 勘誤），但建議 GTM 伺服器容器仍部署在境內做出站清洗：移除 URL 查詢字串中的個資、表單值、Email；IP 匿名化；`user_id` 僅接受 `^v\d+_[A-Z2-7]{20}$` 格式，不符即丟棄該欄位並計數告警。這是降低「GA 境外 SaaS」法遵風險的手段，不是資料來源。

## 4. GA360 匯出欄位 → DMP 識別子（`backend/semantic/ga4_mapping.yaml`）

| BigQuery 欄位 | DMP | 說明 |
|---|---|---|
| `user_id` | `identities.$identity_ga_user_id` → alias `$identity_login_id` | 必須符合 token 格式；不符 → 隔離並告警 |
| `user_pseudo_id` | Web → `$identity_cookie_id`；App → `$identity_device_id`；同時寫 `events.distinct_id` | 未同意時為 null → 該事件只進匿名統計，不建用戶 |
| `event_name='login'` + 兩個 ID 同時存在 | 共現資料 → 歸戶 + 登入改寫 | `events_intraday_` 到達即觸發；`events_` 日批到齊後以 event_id 去重，不重複改寫 |
| `privacy_info.analytics_storage` | `consent.analytics` | `'No'` → 弱 ID 不綁定、不改寫 |
| `privacy_info.uses_transient_token` | 標記 | 無 cookie 量測，不建匿名用戶 |
| `device.advertising_id` | DROP | 不收廣告 ID |
| `user_properties.*` | 只接受登記過的 L0–L2 屬性 | 其他丟棄 |

## 5. 交易事件端：同一個客戶代碼怎麼來

```
數據中台／CDP 交易事實表 fact_order（CUST_ID, ACCT_NO, dt, updated_at, …）
  └─ 批次導入（Trino 讀 Iceberg 分區，每 15 分鐘增量；數據中台若提供 Kafka 則流任務）→ Kafka raw.dwh_trade
       └─ Flink 事件化（docs/06 §7.1 同步任務：狀態機事件化）
            ├─ identities.$identity_login_id  = idsvc.tokenize('customer', CUST_ID)   // 與 GA user_id 同值
            ├─ identities.$identity_account_no = idsvc.tokenize('account', ACCT_NO)   // FPE
            └─ 原始 CUST_ID／ACCT_NO 不進事件表（事件化後即丟棄；隔離區也只存代碼）
CRM customer_accounts（CUST_ID, ACCT_NO）每日 → 同步任務「OneID 綁定」→ bind {login_id, account_no}
```

因此一個客戶在三個來源上是同一個 `$identity_login_id`：GA 登入後事件（`user_id`）、交易事件（數據中台資料事件化）、CRM 綁定；歸戶規則（docs/19）不需要任何明文就能把它們放到同一個 one_id。

## 6. 端到端：一個客戶的一天

| 時間 | 來源 | 資料 | DMP 動作 |
|---|---|---|---|
| 08:50 | GA（App） | `view_item` ×3，只有 `user_pseudo_id=D7` | distinct_id=D7 → 匿名用戶 U7 |
| 09:02 | GA | `login`，`user_id=v1_K3…`，`user_pseudo_id=D7` | 歸戶到客戶 A；D7 改綁到 A；08:50 的 3 筆（7 天內）改寫到 A |
| 09:05 | GA | `order_submit_click`，`symbol=2330`，`user_id=v1_K3…` | 歸 A |
| 09:17 | 數據中台（批次） | `order_submit`，`login_id=v1_K3…`，`account_no=FPE(…)` | 歸 A（同一代碼，不需逐筆對應） |
| 09:17 | 數據中台（批次） | `order_filled` | 歸 A |
| 09:30 | GA | `logout` → `resetAnalyticsData()` | A 的 D7 綁定保留；新 app_instance_id D8 之後的事件歸匿名用戶 |

漏斗「看商品 → 登入 → 送單 → 成交」在客戶 A 身上完整成立；若沒有登入改寫，08:50 的 `view_item` 會留在 U7，漏斗第一步斷掉。

## 7. 待確認

| 項目 | 建議 |
|---|---|
| GA `user_id` 政策條文與法遵認定（代碼化客戶 ID 是否符合「不可直接識別」） | 以 HMAC 代碼 + 金鑰在行內 HSM 的說明送法遵；Google 政策原文由客戶 GA 窗口提供最新版 |
| App 端 token 存放（Keychain／Keystore）與有效期 | 跟 session 同壽命；登出即清 |
| Web 登出是否重置 client_id | 預設不重置（GA 無標準方法），靠「最新綁定者取得」；若客戶要求，可在登出時以 `gtag('config', …, { client_id })` 寫入新值 |
| 金鑰輪替週期與雙版本並存期 | 年輪替；並存 13 個月 |
| 既有 GA 歷史（已設過別的 user_id 格式） | 匯入時以對照表轉成 `v1_` token；轉不了的只保留 `user_pseudo_id` |

## 8. 對應檔案

- `backend/semantic/oneid_rules.yaml`：`alias`、`login_backfill`；`backend/oneid/attribution.py`：`track()` 與登入改寫自測。
- `backend/semantic/ga4_mapping.yaml`：匯出欄位映射；`backend/semantic/events.yaml`：GA 與交易事件登記。
- `backend/security/masking.yaml`、docs/10：L0–L4 分級與 HMAC／FPE 金鑰管理。
- 原型：治理 › 用戶表 › OneID 規則（情境 S2／S3 含登入改寫）；數據整合 › 同步任務（代碼化轉換）。
