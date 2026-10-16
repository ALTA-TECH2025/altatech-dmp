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
| 漏斗／留存／事件分析頁 | 落點下鑽：漏斗每步「到達／流失」、留存格子（點：留存；Shift+點：未留存）、事件分析表格列 → `navigate('/users', { state: { source } })` |
| `src/analytics/RetentionPage.tsx` | 留存分析頁（初始／回訪行為、四模型、日週月、期數、含當期、分組）與同期群 × 期矩陣（單色深淺 = 比例，未過期數灰階） |
| `src/iam/IamPage.tsx` | 權限中心：角色矩陣、使用者指派、數據權限模擬、欄位隱碼、我的 session |
| `src/styles/app.css` | 設計 token 與原型同一套（含深色模式） |

## 規則

- 權限只讀 `/v1/iam/me` 的 `grants`／`masking`；UI 看不到不等於 API 不能呼叫，後端每個請求都會 `authorize()`。
- 不把任何 token 放進 localStorage／sessionStorage；瀏覽器只有 httpOnly session cookie。
- 新頁面：在 `nav.ts` 加項目（綁模組）→ `App.tsx` 加路由並包 `RequireGrant` → 頁面只透過 `api` 存取資料。
- 不新增建置工具或 UI 框架；第三方套件需在 `LICENSES.md` 白名單內。
