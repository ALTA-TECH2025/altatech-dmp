# 企業級安全：角色權限、模組權限、數據權限、欄位隱碼、日誌統計可視化

> 版本 v0.4（2026-10-15）。**v0.4 變更**：本系統的使用者只有五種角色（系統管理員、行銷、數據分析、數位經營、資訊管理）；營業員與分公司主管不進本系統，組織範圍行級規則保留給自訂角色；稽核職能由系統管理員承擔（審計看板、揭露與臨時提權審批）。本文件定義 AltaTech DMP 的企業級存取控制與稽核設計，適用於 UI、API 與 AI agent 工具閘道（三者共用同一套 Policy Engine）。

## 1. 模型總覽

```
身分（SSO / OIDC / AD）
  └─ 使用者（SSO 群組 → 角色）
        └─ 角色（多個）
              ├─ 模組權限（功能：可見 / 可用 / 可匯出 / 可審批 / 可設定）
              ├─ 數據權限（行級：組織範圍、專案、資料來源、分群；時間範圍）
              └─ 欄位權限（列級：明文 / 隱碼 / 不可見；敏感等級 L0–L4）
審批流程（分群推送、匯出、揭露申請、agent 提議）
審計（所有讀寫 + agent 工具呼叫）──► 日誌統計可視化
```

決策順序（Policy Engine，每次請求）：
1. 身分與角色解析 → 2. 模組權限（能不能用這個功能）→ 3. 數據權限（能看哪些列）→ 4. 欄位權限（每個欄位怎麼顯示）→ 5. 審批狀態（受控動作是否已核准）→ 6. 寫審計。
UI、REST API、MCP 工具閘道都呼叫同一個 `authorize(subject, action, resource, context)`，回傳「允許／拒絕 + 行級過濾條件 + 欄位遮罩策略」。

## 2. 角色權限（RBAC + 範圍）

| 角色 | key | 說明 | 範圍預設 |
|---|---|---|---|
| 系統管理員 | `admin` | 平台設定、角色與指派、審批（全量推送、新目的地、L3 揭露、臨時提權、OneID 合併）、審計全覽、DSAR | 全域 |
| 行銷 | `marketing` | 分析模組（事件／漏斗／路徑／歸因／留存）、分群與推送單建立（生效需數位經營審批）、即時觸發規則建立、活動復盤；用戶列表只看隱碼、不可匯出、不可細查 | 全域（欄位隱碼） |
| 數據分析 | `data_analyst` | 全部分析模組、用戶列表、細查（需理由）、匯出（需審批）、SQL 沙箱、標籤定義 | 全域（L3 隱碼、L2 明文） |
| 數位經營 | `digital_ops` | 業務負責人：分析、用戶列表、細查；審批分群／推送／觸發／agent 提議／匯出／L2 揭露；審計看板 | 全域 |
| 資訊管理 | `it_admin` | 數據整合（連線、數據表、同步任務、密鑰輪替）、資料流與效能、元事件／用戶表／維度表治理、平台技術設定、審計日誌匯出、OneID 解綁與合併發起 | 全域（不含個人：無用戶列表／細查） |
| AI 服務身份 | `svc_insight` | 排程 agent 專用，只讀聚合，不可細查 | 全域（聚合） |

營業員、分公司主管不是本系統的使用者：客戶的「所屬營業員／分公司」只是用戶屬性與維度表（`dim_agent`、`dim_branch`），用於分組與分析，不產生行級權限。

- **角色可自訂**：管理員可新增角色（自訂名稱、說明）、選擇數據範圍（全域／專案限定／分群限定／僅聚合；分公司／營業員範圍保留但內建角色不用）、設定回溯天數，並以**勾選矩陣**逐一勾選每個模組的可見／可用／匯出／審批／設定；系統管理員可新增、修改與**刪除**任何角色（系統管理員角色本身除外；刪除仍有指派的角色時須先指定改派角色，使用者自動改派）；可從既有角色複製成草稿。勾選規則：可用蘊含可見、匯出蘊含可用；匯出一律走審批。儲存寫入審計 `role.changed`，正式環境需雙人複核；仍有使用者指派的角色不可刪除。（API：`/v1/iam/roles`；原型：權限中心 › 角色 › 新增／編輯）
- 使用者可多角色，權限取聯集，但**欄位權限取最嚴格**（避免以多角色繞過隱碼）。
- 範圍（Scope）綁在「使用者 × 角色」上：內建五種角色都是全域；自訂角色（例：外包分析）可限專案、分群與回溯天數。
- 時效：角色指派可設到期日；臨時提權（break-glass）需審批並自動到期。

## 3. 模組權限（功能矩陣）

每個模組的動作細分為：`view`（可見）、`use`（可執行查詢／建立）、`export`（匯出）、`approve`（審批）、`admin`（設定）。

| 模組 | 系統管理員 | 行銷 | 數據分析 | 數位經營 | 資訊管理 |
|---|---|---|---|---|---|
| 總覽看板 | 全部 | view | view/use | view/use | view/use |
| 事件／漏斗／路徑／歸因／留存 | 全部 | view/use | view/use/export* | view/use/export* | view/use |
| 用戶列表 | 全部 | view/use（隱碼） | view/use/export* | view/use/export* | — |
| 用戶細查（需理由） | use | — | use | use | — |
| 標籤與分群／推送 | 全部 | view/use（生效需審批） | view/use | view/use/approve | view |
| 即時觸發 | 全部 | view/use（生效需審批） | view | view/use/approve | view |
| 數據整合 | 全部 | — | view | view | 全部 |
| AI 洞察（問答） | 全部 | use | use | use/approve | use |
| AI agent 設定 | admin | — | — | view | view/admin |
| 治理（元事件、用戶表、維度表、資料流與效能） | 全部 | — | view | view | view/use/export/admin |
| 審計與日誌統計 | 全部 | view（自己） | view（自己） | view/use（看板） | view/use/export（日誌） |
| 權限中心 | admin | — | — | view | view |

`export*`：匯出一律走審批並加浮水印與數量上限。

## 4. 數據權限（行級）

| 維度 | 規則來源 | 實作 |
|---|---|---|
| 組織範圍（保留） | 內建五種角色皆為全域；規則保留給自訂角色 | 查詢編譯器在 SQL 加 `branch_id IN (...)` 或 `agent_id IN (...)`；Bitmap 分群先交集「範圍 Bitmap」 |
| 專案／資料來源 | 角色可見的 `project` 與 `source` | `project IN (...)`、`source IN (...)` |
| 分群限制 | 可只允許在某些分群內分析（例：外包帳號只看活動曝光者） | `one_id_uint IN (segment_bitmap)` |
| 時間範圍 | 可限制回溯天數（例：外包帳號 180 天） | `event_date >= today()-N` |
| 聚合門檻 | 小於 K 人的分組不顯示（預設 K=20），避免反推個人 | 查詢結果後處理：人數 < K 的列以「< 20」顯示 |

- 範圍變更（客戶重分配）：`dim_agent` 維度表版本化（僅供分析口徑，不再是權限範圍）；查詢可選「當前歸屬」或「事件當下歸屬」。
- agent 以委託身份執行，行級規則與人完全相同；排程 agent 使用聚合專用身份（無細查）。

## 5. 欄位隱碼（列級，依敏感等級）

| 等級 | 欄位例 | 明文可見角色 | 其他角色顯示 | 說明 |
|---|---|---|---|---|
| L4 可識別 | 姓名、身分證、手機、Email、完整帳號、地址 | **無**（不進分析倉） | — | 僅存在於來源系統與 OneID 服務加密區 |
| L3 代碼化識別 | HMAC 客戶 ID、FPE 帳號、one_id | 系統管理員 | 尾碼遮罩 `cust_•••7281`、`••••4567` | 可做 JOIN，不可反推 |
| L2 敏感業務 | 單筆委託金額、持股、手續費、流失分 | 數據分析、數位經營（行銷、資訊管理看區間） | 區間化 `10–50 萬` 或 `—` | 匯出時一律區間化 |
| L1 行為 | 頁面、事件、裝置、渠道、UTM | 所有分析角色 | — | — |
| L0 聚合 | 看板數字 | 所有人 | — | — |

遮罩策略（可按欄位配置）：`mask_tail(n)`、`mask_all`、`bucket(ranges)`、`hash`、`hide`、`round(unit)`。
- 遮罩在 **Policy Engine 回傳層** 套用（API 回應前），UI 與 agent 工具都無法取得明文；SQL 沙箱以 ClickHouse **列級 masking view**（`CREATE VIEW ... AS SELECT maskTail(account_no)…`）+ 角色對應的 row policy 實作。
- **揭露申請**：有理由、有時效（預設 24 小時）、L3 需系統管理員、L2 需數位經營審批；揭露期間每次查看都寫審計。
- **匯出**：套用比 UI 更嚴格的一級（L2 → 區間化），加浮水印（使用者、時間、申請單號）、上限 10 萬列。
- **AI agent**：工具回傳先套遮罩；敘事層再做正規式攔截（身分證／手機／Email）；`profile.*` 必填 reason。

## 6. 審批流程

| 受控動作 | 審批者 | 說明 |
|---|---|---|
| 分群生效／推送單（含全量推送） | 數位經營 | 附人數、規則、用途；新目的地首次推送另需系統管理員 |
| 匯出（用戶列表／細查／SQL 結果） | 數位經營 | 附用途、列數、保存期限；加浮水印 |
| 欄位揭露申請 | L3：系統管理員；L2：數位經營 | 有時效 |
| agent 提議（分群／規則／執行） | 數位經營 | 附 agent 證據與信心度 |
| 臨時提權 | 系統管理員 + 數位經營（雙人） | 自動到期 |
| 角色／權限變更 | 系統管理員（雙人複核） | 變更前後快照 |
| OneID 人工合併／拆分 | 資訊管理發起、系統管理員審批 | 改寫受影響事件 |

審批單狀態：`pending → approved / rejected / expired`；所有狀態變化寫審計並通知申請者。

## 7. 審計日誌

事件化（寫入 Kafka `dmp.audit` → ClickHouse `dmp.audit_log` + 不可竄改儲存 WORM）：

| 類別 | 事件 | 關鍵欄位 |
|---|---|---|
| 登入 | `auth.login` / `auth.logout` / `auth.failed` | user, ip_masked, method, result |
| 查詢 | `query.executed` | user, module, params_hash, sql_hash, rows, scan_bytes, ms, row_policy, masked_fields |
| 細查 | `profile.viewed` | user, one_id, reason, approval_id |
| 揭露 | `mask.reveal_requested` / `mask.revealed` | user, field, one_id, reason, approval_id, expires_at |
| 匯出 | `export.requested` / `export.downloaded` | user, module, rows, watermark_id, approval_id |
| 變更 | `tag.changed` / `segment.changed` / `trigger.changed` / `model.published` / `role.changed` | user, object, before_hash, after_hash |
| 審批 | `approval.created` / `approval.decided` | requester, approver, type, decision |
| agent | `agent.tool_called` / `agent.insight_created` / `agent.proposal_created` | agent, on_behalf_of, tool, args_hash, scan_bytes, warnings |
| 系統 | `source.lag_alert` / `quarantine.spike` / `job.failed` | source, value, threshold |

保存：熱 13 個月（ClickHouse），冷依法定年限（GCS WORM，Object Lock）。

## 8. 日誌統計可視化（審計與使用分析看板）

目的：讓系統管理員、數位經營與資訊管理用看板而非翻日誌（本系統沒有獨立的稽核角色；外部稽核由系統管理員匯出報表）。

| 看板 | 指標／圖表 | 受眾 |
|---|---|---|
| 使用概況 | DAU／WAU（平台使用者）、各模組使用次數趨勢、熱門指標／事件、查詢量與掃描量 | 管理員 |
| 敏感存取 | 細查次數（依角色／使用者）、揭露申請與核准率、匯出筆數趨勢、非上班時間存取、單一使用者高頻細查 Top N | 系統管理員 |
| 審批 | 待審／平均處理時間／核准率（分類型） | 數位經營、系統管理員 |
| 資料存取異常 | 失敗登入、權限拒絕次數（依模組）、異常掃描量、同一帳號多 IP | 資訊管理 |
| agent 活動 | 工具呼叫量、預算用量、提議數與採納率、敏感工具使用、evals 通過率 | 系統管理員、資訊管理 |
| 效能 | 查詢 P95（依模組）、慢查詢 Top N、資料源延遲 | 資訊管理 |

實作：`dmp.audit_log` 的 AggregatingMergeTree 物化視圖（小時 × 類別 × 使用者 × 模組）；看板走同一套語意層（審計也是指標），因此 AI agent 可回答「上週誰細查最多」（僅系統管理員可問）。
告警規則：非上班時間細查 > N、單人單日揭露 > N、匯出 > N 列、權限拒絕突增 → 通知系統管理員與資訊管理。

## 9. 實作對應

- `backend/security/rbac.yaml`：角色、模組動作矩陣、範圍型別。
- `backend/security/masking.yaml`：欄位敏感等級與遮罩策略。
- `backend/security/policy.py`：`authorize()` 骨架（行級條件、欄位遮罩、審批檢查）。
- `backend/clickhouse/audit.sql`：審計表、物化視圖、row policy 與 masking view 範例。
- 原型：頂部導覽「權限中心」（角色、模組矩陣、數據範圍、欄位隱碼、審批）、「日誌統計」看板；各頁欄位依角色隱碼並提供「申請揭露」。
