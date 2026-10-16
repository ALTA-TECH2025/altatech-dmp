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
| `src/iam/IamPage.tsx` | 權限中心：角色矩陣、使用者指派、數據權限模擬、欄位隱碼、我的 session |
| `src/styles/app.css` | 設計 token 與原型同一套（含深色模式） |

## 規則

- 權限只讀 `/v1/iam/me` 的 `grants`／`masking`；UI 看不到不等於 API 不能呼叫，後端每個請求都會 `authorize()`。
- 不把任何 token 放進 localStorage／sessionStorage；瀏覽器只有 httpOnly session cookie。
- 新頁面：在 `nav.ts` 加項目（綁模組）→ `App.tsx` 加路由並包 `RequireGrant` → 頁面只透過 `api` 存取資料。
- 不新增建置工具或 UI 框架；第三方套件需在 `LICENSES.md` 白名單內。
