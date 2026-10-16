# OneID 歸戶邏輯（v1.2，2026-10-16）— 對齊神策 IDM 3.0「全域用戶關聯」＋ 登入時近 7 天匿名行為改寫

> 取代 docs/01 §6 的「圖連通 + 合併 + 夜間改寫」設計。規則表：`backend/semantic/oneid_rules.yaml`；參考實作與 10 個情境自測：`backend/oneid/attribution.py`；原型「治理 › 用戶表 › OneID 規則」。GA360 埋點與交易事件如何打通、去識別化 ID 如何產生：docs/20。
>
> **v1.2 變更（多主體與血緣，對齊 VeCDP 的 ID Mapping 可視化）**：主體定義 `backend/semantic/entities.yaml`（客戶為唯一錨點；帳戶、裝置、渠道掛在客戶下）；每條 ID 關聯是一條有來源、證據事件、時間、信心、狀態的邊（`dmp.id_edges`），血緣圖與衝突清單（`dmp.oneid_conflicts`）從邊來；共用裝置（90 天內 ≥ 3 個客戶）信心打折、不做傳遞映射；傳遞性映射（弱 ID 改綁時，同匿名用戶下高信心共現的弱 ID 一起跟過去，最多兩跳、信心乘積 ≥ 0.8）；人工合併／拆分／撤銷寫 `dmp.oneid_merge_log`（前後快照、審批角色）；表級血緣 `dmp.lineage_edges`。參考實作情境自測擴到 S1–S20（§3b）。
>
> v1.1 變更：未登入的行為事件一律以 **distinct_id**（cookie／裝置／匿名 ID）歸到匿名用戶；**登入事件到達時做歸戶，並把同 distinct_id 近 7 天仍屬匿名用戶的事件改寫到登入客戶**（`login_backfill`）。這是對神策「不回填」預設的刻意偏離，理由見 §2.5。

## 0. 為什麼改

原設計把所有共現的 ID 做成一張圖，取連通分量當 OneID，低優先級分量併入高優先級，再用夜間批次改寫事件表的 one_id。這有三個問題：

1. **錯併會擴散、難以回復**：一個共用裝置或一支家人共用的手機就能把兩個客戶連成一個連通分量；雖然加了「弱 ID 不合併」「裝置綁 > N 帳號視為公用」等補丁，但規則堆疊後很難對客戶解釋「這筆事件為什麼算在他身上」。
2. **弱 ID「關聯不合併」等於登入前行為永遠歸不了戶**，又另外靠 T+1 回填去補，兩套邏輯互相矛盾。
3. **全量改寫事件表**（`ALTER UPDATE` 不限範圍）在百億級事件上既慢又會讓歷史報表數字變動。v1.1 保留的改寫只有一種：登入時改寫同 distinct_id 近 7 天的匿名事件，範圍固定、只動近 8 個日分區、微批合併，成本可控（§2.5）。

神策的做法是「歸戶在資料進來那一刻決定、一個 ID 同一時刻只屬於一個用戶、不自動合併已存在的用戶」。這套規則簡單、可解釋，而且就是客戶既有神策資料的口徑，匯入後數字能對得上。本系統在這之上加一條：登入時把近 7 天的匿名行為歸給登入客戶（§2.5），讓「登入前看了什麼 → 登入後下單」的漏斗與路徑能在同一個客戶身上接起來。

## 1. 神策 IDM 3.0 的要點（本文對齊的對象）

來源：神策幫助中心「標識用戶——全域用戶關聯」「全域用戶關聯（Web／HarmonyOS）」與神策官方文章的公開內容（本環境無法直接開啟 manual.sensorsdata.cn，以下為可查到的條文摘要與對應理解）。

| 神策規則 | 內容 |
|---|---|
| ID 類型 | `$identity_login_id`（登入 ID）、`$identity_mobile`／email 等業務 ID、`$identity_cookie_id`／`$identity_anonymous_id`／裝置類 ID、自定義業務 ID；ID 類型需先在後台註冊，未註冊的 ID 整條資料拒存 |
| 登入 ID | 系統內優先級最高，每個用戶有且只有一個，不可修改、不可解綁 |
| 數量配置 | 每種 ID 可設定單值或多值；業務 ID 可解綁（換手機號、解綁銀行卡等場景） |
| 關聯條件 | 孤立的 ID 無法打通；兩個 ID 要被視為同一用戶，必須出現在同一條資料（事件、`$SignUp`、`$BindID`）裡 |
| 一 ID 一用戶 | 同一個手機號、同一個裝置 ID 不可能同時屬於用戶 A 和 B |
| 優先級的用途 | 一條資料含多個 ID 且無法關聯（分屬不同用戶）時，用優先級決定這條資料歸誰 |
| 匿名 → 登入 | 登入前以匿名 ID 當 distinct_id 記事件；登入時 SDK 送 `$SignUp`（帶 login_id 與匿名 ID），關聯成功後兩者行為視為同一用戶（首次關聯）；匿名 ID 已關聯過別的登入 ID 時，神策不回填歷史 |
| 同裝置多帳號 | A 登出時 logout 並重置匿名 ID，B 再登入；A 登出後的行為不會算到 B |
| 神策 ID | 用戶表的 id；事件在入庫時就決定屬於哪個神策 ID |

## 2. 本系統的歸戶規則

### 2.1 ID 類型表（`oneid_rules.yaml`）

| 優先級 | ID | 數量 | 改綁／解綁 | 來源 | 說明 |
|---|---|---|---|---|---|
| 1 | `$identity_login_id` 客戶代碼（HMAC） | 單值 | 不可改、不可解綁 | CRM／App 後台／GA `user_id` | 唯一錨點；GA `user_id` 接入時直接視為此 ID（alias） |
| 2 | `$identity_mobile_hash` | 單值 | 人工 | CRM／開戶 | 同手機被第二個客戶帶入 → 不改綁、記 conflict；換號由 CRM 發 unbind + bind |
| 3 | `$identity_account_no`（FPE） | 多值 | 人工 | CRM 綁定／數據中台交易資料 | 一客多戶 |
| 4 | `$identity_device_id` | 多值 | 最新者取得 | GA360 | 共用裝置 |
| 5 | `$identity_cookie_id`（GA client_id） | 多值 | 最新者取得 | GA360 | 清 cookie 即新 ID |
| 6 | `$identity_anonymous_id` | 多值 | 最新者取得 | Collector | 兜底 |
| 9 | `$identity_line_uid` | 多值 | — | MA | 觸達渠道，不參與歸戶 |

「已識別客戶」= 綁有客戶代碼的用戶；只有帳號、手機或弱 ID 的用戶是「待升級」用戶（匿名用戶是其中一種）。

**distinct_id**：每筆事件都寫 `events.distinct_id` = 資料裡第一個弱 ID（Web 為 GA `user_pseudo_id`／client_id；App 為裝置 ID；Collector 為 anonymous_id）。未登入事件只有 distinct_id，先歸到以它建立的匿名用戶；登入事件同時帶 distinct_id 與客戶代碼，是「共現資料」，也是 §2.5 改寫的觸發點。distinct_id 在改寫後保留原值，任何一筆事件都能追溯「當時是哪個 cookie／裝置」。

### 2.2 每筆資料的歸戶演算法（接入時，Flink keyed state + Valkey）

```
輸入：一條資料的 identities（已正規化、alias 合併、依優先級排序）
1. 查每個 ID 目前屬於哪個用戶
2. 全部都是新 ID            → 建新用戶，全部綁上
3. 否則取資料中「優先級最高的 ID」：
   a. 它還沒有用戶，且只找到一個用戶、該用戶在這類型還有空位 → 綁到該用戶
      （該用戶原本沒有客戶代碼 → 這就是「首次關聯升級」：匿名用戶／帳號用戶變成客戶，歷史事件自動歸戶，不改寫事件）
   b. 它還沒有用戶，但找到的用戶該類型已有別的值、或找到多個用戶 → 為它建新用戶（不併入舊用戶）
   c. 它已有用戶 → 該用戶就是歸屬
4. 資料裡其餘的 ID：
   - 新 ID → 綁到歸屬用戶（單值類型已有值則不綁、記 conflict）
   - 屬於別的用戶 → 依該類型的改綁政策：latest（裝置／cookie）解綁舊用戶、綁到歸屬用戶；manual（手機／帳號）保留原綁定、記 conflict；never（客戶代碼）不動
5. 事件寫入 one_id = 歸屬用戶、distinct_id = 第一個弱 ID；寫 oneid.resolved 審計（綁定／改綁／衝突）
6. 若這筆是登入事件（帶客戶代碼 + distinct_id，且同意）：
   把同 distinct_id、event time 在 [登入 − 7 天, 登入]、目前仍屬「未識別用戶」的事件改寫 one_id → 歸屬客戶
   （已歸任何客戶的事件不動；7 天外的留在匿名用戶）；寫 oneid.backfilled 審計（筆數、視窗）
```

不會發生的事：兩個已存在的用戶被自動合併；已歸到某個客戶的事件被改到另一個客戶；超過 7 天的匿名事件被改寫。

### 2.3 情境（與 `attribution.py` 自測一一對應）

| 情境 | 輸入 | 結果 |
|---|---|---|
| S1 匿名瀏覽 → 首次登入 | cookie C1 → 匿名用戶 U1；登入事件 {C1, cust_A}，A 尚無用戶 | U1 升級為客戶 A；登入前事件自動屬於 A（同一 one_id） |
| S2 既有客戶在新裝置 | 新裝置 C2 先成匿名用戶 U2（9 天前、3 天前、1 天前各一筆）；登入事件 {C2, cust_A} | 事件歸 A；C2 改綁到 A；**3 天前與 1 天前的匿名事件改寫到 A**（`backfilled=2`）；9 天前那筆留在 U2 |
| S3 共用裝置換人登入 | A 登入後 C2 上又有匿名事件（歸 A）；兩天後 {C2, cust_B} | 事件歸 B；C2 改綁到 B；A 的事件（含登入後的匿名事件）**不**改寫到 B（`backfilled=0`） |
| S4 B 登出後 C2 的匿名事件 | {C2} | 歸 B（最新綁定者） |
| S5 交易事件只帶帳號 | {ACC9}，CRM 尚未送綁定 | 以帳號建立用戶；CRM 綁定 {cust_C, ACC9} 到達 → 升級為客戶 C，歷史交易自動歸戶 |
| S5b 既有客戶的新帳戶 | {cust_A, ACC10} | ACC10 綁到 A |
| S6 同手機兩個客戶 | {cust_D, M1}，M1 已屬 A | D 另建用戶、事件歸 D；手機不改綁、記 conflict；A、D 不合併 |
| S7 GA user_id | {ga_user_id=cust_A, C7} | 視同客戶代碼，歸 A |
| S8 未同意 | {cust_A, C8}，analytics_storage 拒絕 | 事件歸 A；C8 不綁定 |
| S9 解綁 | unbind(cookie C7) 允許；unbind(login_id) 拒絕 | 人工合併／拆分由資訊管理發起、系統管理員審批，是登入改寫之外唯一會改寫事件的操作 |
| S10 未同意的登入 | C9 匿名事件；{C9, cust_A} 且 analytics_storage 拒絕 | 登入事件歸 A；C9 不綁定、匿名事件不改寫（`backfilled=0`） |

### 2.4 證券業特有的部分

- **客戶 ↔ 帳戶**：帳戶不是另一種「用戶」，`account_no` 是客戶的一個多值 ID；關係由 CRM 主檔每日發 bind 指令（等同一條共現資料）。帳戶層事件（委託／成交／出入金）只帶 `account_no` 時，透過這個 ID 歸到客戶。帳戶的屬性（類型、分公司、營業員）用**維度表**以 `account_no` 關聯（docs/06 §7.2），不是獨立主體。
- **GA 的 `user_id`**：App／Web 在登入成功後把 ID 服務簽出的 HMAC 客戶代碼設成 GA `user_id`，接入時 alias 成 `$identity_login_id`，所以 GA 登入後事件在 BigQuery 匯出裡就自帶客戶代碼；登入前的 session 只有 `user_pseudo_id`（= distinct_id）。GA 的 `login` 事件（同時帶 `user_id` 與 `user_pseudo_id`）就是觸發 §2.5 改寫的登入事件；埋點細節與去識別化見 docs/20。
- **同意**：`privacy_info.analytics_storage` 拒絕時，弱 ID 不與強 ID 建立關聯。
- **神策歷史匯入**：匯入的事件已帶神策 identities，走同一套規則，口徑一致。

### 2.5 登入改寫：近 7 天匿名行為歸到登入客戶（`login_backfill`，預設開）

| 項目 | 規則 |
|---|---|
| 觸發 | 帶客戶代碼 + distinct_id 的資料：GA `login`、Collector `$SignUp`／`login`、CRM `oneid.bind`；未同意（analytics_storage 拒絕）不觸發 |
| 範圍 | 同 distinct_id、event time ∈ [登入 − 7 天, 登入]、目前 one_id 仍是「未識別用戶」的事件；已歸任何客戶的事件不動（所以 S3 不會把 A 的事件改給 B） |
| 改寫內容 | `one_id`、`one_id_uint` → 登入客戶；`distinct_id` 保留原值 |
| 7 天之外 | 留在匿名用戶；匿名用戶若沒有剩餘事件則標記已併入（users.merged_into） |
| 落地方式 | Flink 在登入事件上輸出 `oneid.backfill` 指令（distinct_id、目標、視窗）→ 每 10 分鐘合併一批 → ClickHouse 輕量更新（`ALTER … UPDATE one_id, one_id_uint WHERE distinct_id IN (…) AND event_date BETWEEN … AND one_id IN (匿名用戶)`），只動近 8 個日分區；Iceberg 冷層由夜間作業同步 |
| 查詢一致性 | 改寫落地前，查詢層以 `oneid_backfill` 覆蓋表（distinct_id, from, to, target）在 SQL 編譯時套用，報表不會「先少後多」 |
| 審計 | `oneid.backfilled`：distinct_id、目標客戶、筆數、視窗；用戶細查可看「原 distinct_id」 |
| 成本 | 每日登入 ~40 萬次、每次平均改寫 ~30 筆 → 每日 ~1,200 萬列更新，集中在 7 個分區；微批後每批 ~3,000 次登入，一次 UPDATE 完成；P95 落地 < 15 分鐘 |

為什麼偏離神策的「不回填」：證券場景的關鍵漏斗（看行情／看商品 → 登入 → 下單）與歸因都橫跨登入，若登入前的瀏覽留在匿名用戶，漏斗第一步永遠接不到客戶；7 天視窗覆蓋 App 自動登出與行銷活動的典型週期，又限制了改寫成本與歷史數字變動的範圍。

其他可選政策（預設關閉）：

| 政策 | 效果 | 代價 |
|---|---|---|
| `window_days` 改大（14／30） | 更多登入前行為歸戶 | 改寫列數與歷史數字變動成比例增加；超過 31 天會跨月分區 |
| 手機改綁政策改 latest | 同手機第二個客戶會把手機搶走 | 家人共用手機會互相搶 |

## 3. 實作對應

| 元件 | 內容 |
|---|---|
| Flink `oneid` 算子 | keyed state `(id_type, id_hash) → one_id`；每筆事件執行 §2.2；輸出事件（含 one_id、one_id_uint、distinct_id）與 `id_map` 變更、`oneid.resolved` 審計；登入事件另輸出 `oneid.backfill` 指令 |
| `backfill` 微批 | 每 10 分鐘讀 `oneid.backfill`，合併同分區指令，執行 ClickHouse 輕量更新；寫 `oneid_backfill` 覆蓋表（套用前）與 `oneid.backfilled` 審計（套用後）|
| Valkey | 熱 ID → one_id 快取（供 API／即時規則查詢） |
| ClickHouse `id_map` | `(id_type, id_hash) → one_id, version, bound_at, unbound_at`；可回溯任一時刻的綁定 |
| `users` | 一個 one_id 一列；`one_id_uint` 在建立時分配、永不改變；匿名用戶在 7 天匿名事件全數改寫後標記 `merged_into` |
| API | `/v1/oneid/resolve`、`/bind`、`/unbind`、`/merge`（人工，審批）、`/{one_id}/history`、`/backfills`（登入改寫批次與狀態）、`/erase`（DSAR） |
| 治理頁 | ID 類型表、數量／改綁政策、昨日升級／改綁／登入改寫／衝突統計、衝突待處理清單 |

### 3b. v1.2 多主體與血緣（entities.yaml、三張表、S11–S20）

| 元件 | 內容 |
|---|---|
| `semantic/entities.yaml` | 主體（customer 錨點、account N:1、device N:N 最新者、channel 不參與歸戶）、邊的來源與信心基準、共用裝置門檻與折扣、傳遞映射參數、衝突種類與處置、合併紀錄欄位與審批、表級血緣、可視化要畫的內容 |
| `dmp.id_edges` | `(id_type_a, id_a, id_type_b, id_b)` → source、evidence_event_id、first/last_seen、seen_count、confidence、status（active／superseded／conflict／unbound）、one_id；ReplacingMergeTree |
| `dmp.oneid_conflicts` | 視圖：`id_edges WHERE status='conflict'`，待人工清單 |
| `dmp.oneid_merge_log` | merge／split／undo：ids_moved、events_rewritten、snapshot_before／after、reason、requested_by、approved_by、undone_by_log_id |
| `dmp.lineage_edges` | 表級血緣：來源 → raw → 目標 → id_map／users／分群／推送，via 任務或模組 |
| `oneid/attribution.py` v1.2 | `_edge`／`_record_edges`（同筆資料兩兩記邊，含錨 ID 用資料來源、弱–弱用 cooccur）、`is_shared_device`、`_follow_transitive`、`merge`／`split`／`undo`、`graph(one_id)` 給身分圖 |

自測 S11–S20：S11 邊的來源／證據／信心；S12 高信心共現（8 次，0.95）→ 跟著改綁並改寫匿名事件；S13 低信心（1 次，0.6）不跟；S14 共用裝置（3 個客戶）→ 邊 conflict、信心打折、不傳遞；S15 同筆兩個客戶代碼拒收；S16 同手機第二客戶記 conflict；S17 合併：客戶代碼不同拒絕、需 admin、快照與事件改寫；S18 撤銷還原、二次撤銷拒絕；S19 拆分從指定時間起、錨 ID 不可拆；S20 圖資料含主體、邊、時間軸，表級血緣有「來源 → id_map」。

## 4. 待確認

- 手機改綁政策：預設「人工」；若客戶希望「最新者取得」請明示。
- 登入改寫視窗：預設 7 天（event time）；是否要含 App 自動登出後的更長視窗（14 天）請明示。
- 登入改寫是否也套用在「歷史匯入」（神策舊資料的 `$SignUp`）：預設套用，匯入期間以批次而非微批執行。
- 人工合併的審批人與保留期（預設資訊管理發起、系統管理員審批、對照保留 90 天）。
