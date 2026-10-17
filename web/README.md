# web — 正式前端（React 19 + Vite + TypeScript）

設計見 `docs/24-frontend-framework-and-identity.md`。原型（`prototype/`）仍是互動展示；這裡是會接真 API 的應用外框：登入（SSO／AD）、路由守門、側欄依權限過濾、權限中心。

```bash
cd web
npm ci                 # 版本鎖定在 package-lock.json
npm run dev            # http://127.0.0.1:5173，/auth 與 /v1 代理到 http://127.0.0.1:8000（後端 BFF）
npm run typecheck      # tsc --noEmit
npm test               # vitest：lib/policy（蘊含、遮罩渲染、矩陣）、api/client（CSRF、401、403）
npm run build          # dist/（由 apps.web 映像的 nginx 提供，與 API 同網域）
```

## 結構

| 路徑 | 內容 |
|---|---|
| `src/app/App.tsx` | 路由：`/login`、`/no-access` 公開；其餘在 `RequireAuth` + `AppShell` 內，每頁 `RequireGrant(module, action)` |
| `src/api/client.ts` | fetch 包裝：same-origin cookie、非 GET 帶 `X-CSRF-Token`、401 → `/login?return_to`、403 → `ForbiddenError` |
| `src/api/types.ts` | 與 `backend/openapi.yaml` 對齊的型別；`MODULES`／`ACTIONS` |
| `src/auth/` | `AuthContext`（啟動抓 `/v1/iam/me`、登出）、`LoginPage`（依 `/auth/providers` 顯示 SSO 按鈕與 AD 表單 + TOTP）、`RequireGrant`（守門、403、step-up 提示） |
| `src/shell/` | `nav.ts`（側欄定義，每項綁模組）、`AppShell`（側欄 + 頂欄 + Outlet；手機抽屜） |
| `src/lib/policy.ts` | `can`／`visibleModules`／`display`（遮罩渲染）／`toggleAction`／`unionGrants` |
| `src/analytics/EventsPage.tsx`、`LineChart.tsx`、`series.ts` | 事件分析頁（多指標、篩選、分組 ≤3、粒度、對比、Top N、抽樣）與自繪 SVG 折線／長條（固定類別色、十字線提示、抑制點空心）、數字卡、表格 |
| `src/analytics/FunnelPage.tsx`、`FunnelChart.tsx` | 漏斗分析頁（步驟／窗口／順序／關聯屬性／口徑／分組／趨勢）與自繪 SVG 漏斗圖；目錄來自 `/v1/semantic/catalog` |
| `src/users/UsersPage.tsx`、`RuleBuilder.tsx`、`UsersTable.tsx`、`rule.ts` | 用戶列表頁：條件建構器（用戶屬性／分群、做過／未做過 × 次數 × 期間 × 事件屬性、且／或、來自漏斗／留存／事件分析的落點來源）→ 估數 → keyset 分頁列表（欄位、排序、遮罩提示）→ 存為分群；`rule.ts` 純函式（欄位目錄、值轉型、規則中文摘要、有效性）有單元測試 |
| `src/users/ProfileDrawer.tsx` | 用戶細查抽屜：理由（4–200 字）→ 畫像（屬性脫敏、近 30 天摘要、分群）→ 行為序列（倒序分頁、事件篩選）→ 身分圖（節點／邊） |
| `src/segments/SegmentsPage.tsx`、`segments.ts` | 分群頁：列表（全部／待審批／回收站、搜尋）、新建與編輯（同一套 RuleBuilder、更新方式／頻率／最早時間／保留版本）、詳情（狀態與審批列、立即計算、預覽畫像長條、成員列表、歷史版本、血緣、暫停／複製／回收站／恢復／徹底刪除）；`actionsFor` 依 grants 決定可做的動作，有單元測試 |
| `src/approvals/ApprovalsPage.tsx`、`approvals.ts`、`src/users/ExportDialog.tsx` | 審批與匯出頁（待我審批：核可／退回附意見；我的申請；匯出申請：核可後產檔 → 下載 CSV；揭露申請：狀態、到期、查看次數）；細查抽屜可對遮罩欄位申請限單一用戶的揭露；用戶列表與分群詳情的「匯出（審批）」小表單（用途、保存天數） |
| `src/integration/IntegrationPage.tsx`、`integration.ts`、`types.ts` | 數據整合頁（數據源：連線清單、新增連線表單只收 secret_ref 並擋疑似明文、逐步診斷測試、最小權限自檢、輪替、停用；數據表：登記並探索欄位與敏感等級、重新探索 Schema 變更；同步任務：預覽驗證、立即執行、執行紀錄；建立任務四步精靈：數據表 → 模型 → 排程與策略 → 建立並預覽） |
| `src/governance/GovernancePage.tsx`、`UserSchemaPage.tsx`、`ItemsPage.tsx`、`governance.ts`、`types.ts` | 資料治理：元事件管理（事件字典搜尋／狀態、登記與編輯抽屜含上線檢查、狀態轉移、隔離區登記放行、變更單審批；屬性字典登記；虛擬事件建立）、用戶表管理（用戶屬性與覆蓋率、登記；OneID 規則優先序／上限／狀態修改送覆核；個資防護）、維度表與字典（維度表定義／新增／編輯欄位／立即同步；字典 CSV 上傳 append／overwrite；虛擬屬性建立）。語意層項目標「語意層」、執行期登記標「待發版」 |
| `src/migration/MigrationPage.tsx`、`migration.ts`、`types.ts` | 神策遷移：盤點（計畫 + 神策 API 盤點 + 任務進度）、埋點銜接表（可下載 CSV）、導出與載入（建立任務、分片、重跑）、新舊對帳（T5 折線、T6 長條、T2 表）、切換時序 |
| `src/audit/AuditPage.tsx` | 日誌統計：/v1/audit 最近 N 筆、篩選、事件分佈；效能統計標「第二階段」 |
| `src/shell/nav.ts`、`AppShell.tsx` | 橫向導覽與原型同構：頂欄群組（首頁／分析／營運／資料／AI 洞察／數據治理／系統管理）＋子導覽列（目前群組的項目）＋手機抽屜；未實作項目加「二期」「待開發」小標 |
| `src/app/App.tsx` Placeholder、`src/users/ProfileEntryPage.tsx` | 未實作頁面標「第二階段」（即時觸發、資料流與效能、AI 洞察四頁）或「待開發」（自定義 SQL、平台設定），附替代入口；用戶細查 / OneID 入口頁可直接以客戶代碼＋理由開細查抽屜 |
| `src/dashboards/DashboardsPage.tsx`、`CardView.tsx`、`SaveCardButton.tsx`、`dashboards.ts`、`types.ts` | 總覽／儀表板（`/overview`、`/overview/:id`）：列表（我的、分享給我的）與新增；卡片網格（半寬／全寬，每張 POST `/cards/{id}/data` 以檢視者身分重算，事件／漏斗／留存／路徑／歸因各自沿用分析頁圖表元件，遮罩分桶自動改表格）；區間切換、改名、分享（角色／使用者）、移除卡片、刪除；五個分析頁的「存為看板卡片」選既有或新建儀表板 |
| 漏斗／留存／事件分析頁 | 落點下鑽：漏斗每步「到達／流失」、留存格子（點：留存；Shift+點：未留存）、事件分析表格列 → `navigate('/users', { state: { source } })` |
| `src/analytics/PathPage.tsx`、`PathChart.tsx`、`sankey.ts` | 行為路徑頁（起點／終點、步數、節點只留／折疊／拆分／合併／離開、會話切割、抽樣）與自繪 SVG 桑基圖（節點高度與連線寬度 ∝ 人數、固定類別色、滑過高亮與提示、點節點設為新起點或下鑽用戶列表）；`sankey.ts` 版面純函式有單元測試 |
| `src/analytics/AttributionPage.tsx` | 歸因分析頁（轉化目標與價值欄位、觸點別名／條件／自訂權重、六種模型勾選、位置權重、半衰期、回溯窗口、拆分）：數字卡、各模型占比長條（同一張圖一個模型一條序列）、模型對比表（轉化數／價值切換）、觸點 → 轉化者用戶列表；`shareSeries` 有單元測試 |
| `src/analytics/RetentionPage.tsx` | 留存分析頁（初始／回訪行為、四模型、日週月、期數、含當期、分組）與同期群 × 期矩陣（單色深淺 = 比例，未過期數灰階） |
| `src/iam/IamPage.tsx` | 權限中心：角色矩陣、使用者指派、數據權限模擬、欄位隱碼、我的 session |
| `src/styles/app.css` | 設計 token 與原型同一套（含深色模式） |

## 規則

- 權限只讀 `/v1/iam/me` 的 `grants`／`masking`；UI 看不到不等於 API 不能呼叫，後端每個請求都會 `authorize()`。
- 不把任何 token 放進 localStorage／sessionStorage；瀏覽器只有 httpOnly session cookie。
- 新頁面：在 `nav.ts` 加項目（綁模組）→ `App.tsx` 加路由並包 `RequireGrant` → 頁面只透過 `api` 存取資料。
- 不新增建置工具或 UI 框架；第三方套件需在 `LICENSES.md` 白名單內。

## 示範模式（Vercel）

`npm run build:demo`（`VITE_DEMO=1`）建置的版本不打後端：`src/demo/fetch.ts` 以 `src/demo/fixtures.json` 回答所有請求（固定資料由 `backend/tools/demo_fill.py` + `demo_capture.py` 從單機後端與合成黃金資料集擷取；無真實客戶資料，密鑰在回應裡本來就是遮罩），登入頁改為選角色進入、頂欄可切換角色，寫入動作只回「模擬成功」。原型保留在 `/prototype/`。重新擷取：

```bash
deploy/quickstart/run_local.sh up && DMP_SEED_PASSWORD=… deploy/quickstart/run_local.sh seed
DMP_LOCAL_ACCOUNTS=~/dmp-local/accounts.json DMP_SEED_PASSWORD=… python3 -m backend.tools.demo_fill && python3 -m backend.tools.demo_capture
cd web && npm run build:demo
```
