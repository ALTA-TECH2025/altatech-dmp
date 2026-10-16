# 前端大框架、權限與資料管理、LDAP／Entra ID 登入整合（設計 + 代碼）

> 版本 v0.1（2026-10-16）。代碼：前端 `web/`（React + Vite + TypeScript，可 `npm run typecheck && npm test && npm run build`）、後端 `backend/auth/`（`python3 -m backend.auth` 自測：假 Entra ID、假 AD、session、API token、FastAPI 路由）。權限口徑沿用 docs/10 與 `backend/security/`。

## 0. 結論先講

| 題目 | 決定 | 為什麼 |
|---|---|---|
| 前端框架 | React 19 + Vite + TypeScript + React Router；無狀態管理庫（Context + fetch）；圖表自繪 SVG + D3 scale／shape（後續；ECharts 不用，見 docs/25）；設計 token 與原型同一套 | 白名單授權（MIT／Apache-2.0）、團隊 TypeScript 一條線、原型的版面可直接搬 |
| 登入架構 | **BFF（Backend-for-Frontend）**：瀏覽器只有 httpOnly session cookie；IdP token 只在後端用一次就丟；非 GET 帶 CSRF header | XSS 拿不到 token；金融業稽核熟悉的 session 模型；登出可集中撤銷 |
| 身分來源 | **預設 broker 模式：Keycloak 當身分代理**（Entra ID 以 OIDC 掛進 Keycloak、機房 AD 以 LDAP User Federation 掛進 Keycloak），本系統只對 Keycloak 講 OIDC；**direct 模式**（客戶不准多一層時）：後端直接對 Entra ID（OIDC）與 AD（LDAPS bind） | 一條協定、MFA／Conditional Access 在 IdP 端、換 IdP 不改應用；direct 模式兩條路徑都已寫好並自測 |
| 角色來源 | IdP 群組 → 角色（`backend/auth/idp.yaml` role_mapping），登入時套用；本地指派只能加自訂角色、限範圍、設到期或拒絕某角色 | 和 docs/10 §2「SSO 群組 → 角色」一致；離職停帳號即失效 |
| admin 保護 | admin 角色需 MFA（amr 含 mfa）且認證在 15 分鐘內，否則 session 內 admin 暫時拿掉（step-up 後恢復） | 設定動作不該靠 8 小時前的登入 |
| 前端權限 | 只用 `GET /v1/iam/me` 的 grants／masking 決定選單、按鈕、唯讀；最終裁決是後端每個請求的 `authorize()` | UI 不是安全邊界（docs/10 §1） |

## 1. 前端大框架

```
web/
  index.html                 CSP meta（script-src 'self'、connect-src 'self'；frame-ancestors 由伺服器 header 設）
  vite.config.ts             /auth、/v1 代理到後端（開發時同源，不需 CORS）
  src/main.tsx               入口
  src/app/App.tsx            路由：/login、/no-access 公開；其餘在 RequireAuth + AppShell 內，每頁 RequireGrant(module, action)
  src/api/client.ts          fetch 包裝：same-origin cookie、X-CSRF-Token（double-submit）、401 → /login?return_to、403 → ForbiddenError
  src/api/types.ts           與 openapi 對齊的型別（Me、Role、Assignment、ScopeSimulation…）；MODULES／ACTIONS 常數
  src/auth/AuthContext.tsx   啟動：GET /v1/iam/me；logout：POST /auth/logout → 跳 IdP end_session
  src/auth/LoginPage.tsx     依 /auth/providers 顯示 SSO 按鈕（broker：一顆；direct：Entra 按鈕 + AD 表單 + TOTP 第二步）
  src/auth/RequireGrant.tsx  RequireAuth／RequireGrant／Forbidden（step-up 提示）
  src/shell/nav.ts           側欄定義（每項綁模組），AppShell 用 can(module,'view') 過濾
  src/shell/AppShell.tsx     側欄 + 頂欄（身分、角色、step-up 提示、登出）+ <Outlet/>；手機版抽屜
  src/lib/policy.ts          can／visibleModules／display（遮罩渲染）／toggleAction（矩陣蘊含）／unionGrants；有單元測試
  src/iam/IamPage.tsx        權限中心：角色（勾選矩陣）、使用者指派、數據權限（行級模擬）、欄位隱碼、我的 session
  src/styles/app.css         設計 token 同 prototype/css/app.css（含深色模式）
```

原則：
- **頁面 = 路由 + 模組 + 動作**。`<RequireGrant module="iam" action="admin">` 包住寫入 UI；沒有權限顯示 403 頁並說明去哪申請。
- **資料存取只走 `api` 客戶端**：沒有 token 存在 localStorage；所有寫入帶 CSRF header；401 統一導回登入並帶 `return_to`（只接受站內路徑，防 open redirect）。
- **狀態**：伺服器狀態用 fetch + 頁面本地 state；跨頁只共用 `me`。之後若查詢頁需要快取再引入 TanStack Query（MIT）。
- **設計系統**：token 與原型一致，正式元件逐頁從原型搬；圖表自繪 SVG（原型已全部自繪）+ D3 scale／shape（ISC）並遵守 dataviz 規範（固定類別色、單軸）。
- **建置與 CI**：`npm ci && npm run typecheck && npm test && npm run build`；輸出靜態檔由 `apps.web` 映像（nginx）提供，API 同網域反向代理（cookie 同源）。

## 2. 身分整合

### 2.1 broker 模式（預設）：Keycloak 身分代理

```
瀏覽器 ──/auth/login?provider=keycloak──► 後端 ──302──► Keycloak /auth（code + PKCE）
   Keycloak 登入頁：  [公司帳號 Entra ID]  → Entra ID（OIDC；Conditional Access 強制 MFA）
                      [網域帳號 AD]       → Keycloak LDAP User Federation（LDAPS bind；Kerberos 可選）
Keycloak ──302 code──► 後端 /auth/callback ──換 token、驗 ID token、groups mapper──► 群組 → 角色 → session cookie ──302──► return_to
```

- Keycloak 設定：realm `dmp`；client `altatech-dmp`（confidential、Standard Flow、PKCE S256、redirect `https://dmp.internal/auth/callback`）；Identity Provider `entra`（OIDC，discovery URL 為租戶 v2.0；mapper 把 Entra groups claim 轉 Keycloak group）；User Federation `ad`（LDAPS、bind DN 為服務帳號、`sAMAccountName` 為 username、群組 mapper 讀 `memberOf`、同步週期每日）；client scope `groups`（Group Membership mapper，full path 關閉）。
- 應用只需要：issuer、client_id、client_secret_ref、redirect_uri（`idp.yaml providers.keycloak`）。
- Helm：Keycloak 25（Apache-2.0，已在 LICENSES.md）由 `deploy/helm` 部署；資料庫共用 PostgreSQL；Ingress 與 DMP 同網域不同路徑或子網域。

### 2.2 direct 模式：直接對 Entra ID 與 AD

**Entra ID（`backend/auth/oidc.py`）**
- App registration：Web 平台、redirect URI、ID token；Token configuration 加 `groups` claim（Security groups，以 Object ID 發）；Conditional Access 對本 App 強制 MFA；用戶端密鑰或 federated credential（免密鑰）。
- 授權碼 + PKCE；ID token 驗簽（JWKS 依 `kid` 取，找不到就重抓一次支援輪替）、`iss`／`aud`／`exp`／`iat`／`nonce`、租戶 `tid`、`amr` 含 `mfa`；群組超過 200 個時 token 無 `groups`（overage），改呼叫 Graph `/me/memberOf`（需 `GroupMember.Read.All`）。
- 群組對應用 Object ID（GUID）而非名稱（名稱可改）。

**AD（`backend/auth/ldap.py`）**
- 只走 LDAPS 636 並釘選內部 CA；服務帳號搜尋 `(&(objectClass=user)(sAMAccountName={u})(!(userAccountControl:1.2.840.113556.1.4.803:=2)))`（排除停用）→ 以使用者 DN bind 驗密碼 → `memberOf` + 巢狀群組（`1.2.840.113556.1.4.1941`）。
- 使用者名稱白名單正規式 + RFC 4515 跳脫（防注入）；速率限制每帳號 5／分鐘、每 IP 30／分鐘；錯誤訊息一律「帳號或密碼錯誤」。
- 直連 AD 沒有 IdP MFA → 本系統要求 TOTP 第二因子（`/auth/ldap` 第一步回 `next: totp`，第二步帶 6 位數）。
- 客戶端用 Protocol 注入：正式環境 python-ldap（PSF 式授權，需 libldap），自測用 FakeLdap。

### 2.3 Session、CSRF、API token（`backend/auth/session.py`）

| 項目 | 設定（`idp.yaml session`） |
|---|---|
| cookie | `dmp_sid`；HttpOnly、Secure、SameSite=Lax；值只是不透明 id |
| 逾時 | 閒置 30 分鐘；絕對 8 小時；同帳號最多 3 個 session（踢最舊） |
| CSRF | double-submit：`dmp_csrf` cookie（JS 可讀）+ `X-CSRF-Token` header，所有非 GET 必帶 |
| IP 綁定 | 預設關（NAT 誤殺）；金融業要求時開 `bind_ip` |
| admin step-up | `admin_requires: {amr: [mfa], max_auth_age_s: 900}`；不符時 session 內 admin 暫時拿掉，頂欄提示「管理動作需重新驗證」 |
| API token | `POST /auth/token` 以 session 換短效 RS256 JWT（15 分鐘，`roles` claim），給 MCP／排程／CLI；驗證不查 DB |
| 登出 | 刪 session + 回 IdP `end_session_endpoint`（RP-initiated logout） |
| 存放 | 骨架記憶體；正式 PostgreSQL `iam_sessions`（多副本、可列出、可撤銷） |

### 2.4 群組 → 角色（`idp.yaml role_mapping`、`backend/auth/mapping.py`）

- 內建五種角色各對應一個群組（DMP-Admins／DMP-Marketing／DMP-Analysts／DMP-DigitalOps／DMP-IT）；Entra 用 Object ID、AD／Keycloak 用群組名。
- 不在任何群組 → 登入成功但無角色 → `/no-access` 頁 + 審計 `auth.login.no_role`。
- 本地指派（`/v1/iam/assignments`）疊加：自訂角色（`custom_*`）、範圍（專案／分群／回溯天數）、到期、拒絕某角色；到期自動失效。
- `GET /v1/iam/me` 回：使用者、角色（含 active 標記）、grants（模組 → 動作聯集）、masking（欄位 → 策略，取最嚴格）、scope、session（idp_mode、amr、step_up_needed、csrf_header）。

### 2.5 審計事件

`auth.login.success`／`auth.login.failed`／`auth.login.no_role`／`auth.logout`／`auth.session.expired`／`auth.session.evicted`／`auth.step_up`／`auth.token.issued`／`iam.role_mapping.applied`；IP 只存遮罩後（`10.1.2.x`）。日誌統計頁的「資料存取異常」卡（docs/10 §7）吃這些事件。

### 2.6 威脅對照

| 威脅 | 對策 |
|---|---|
| token 被 XSS 竊取 | 瀏覽器沒有 token（BFF）；CSP `script-src 'self'` |
| CSRF | SameSite=Lax + double-submit header |
| 授權碼攔截／重放 | PKCE S256；state 綁登入前 cookie；nonce 綁 ID token；code 一次性 |
| IdP 金鑰輪替 | JWKS 依 kid 重抓 |
| 群組改名 | Entra 用 Object ID |
| 離職未停權 | 角色來自群組；停帳號即不能登入；session 最長 8 小時 |
| LDAP 注入／暴力猜測 | 正規式白名單 + 跳脫；速率限制；AD 鎖定策略 |
| 舊登入做管理動作 | admin step-up 15 分鐘 |
| open redirect | `return_to` 只接受站內路徑 |

## 3. 權限與資料管理頁（權限中心）

| 分頁 | 內容 | API | 寫入權限 |
|---|---|---|---|
| 角色 | 內建五種 + 自訂；勾選矩陣（可見／可用／匯出／審批／設定，蘊含規則自動勾）；範圍；回溯天數；複製成草稿 | `GET/POST/PUT /v1/iam/roles` | iam.admin；內建角色唯讀（由 rbac.yaml 定義） |
| 使用者指派 | 群組對應（唯讀，來自 IdP）+ 本地指派（自訂角色、範圍、到期、理由）；撤銷 | `GET/POST/DELETE /v1/iam/assignments` | iam.admin |
| 數據權限 | 自己的行級條件；行級規則模擬（選角色看查詢編譯器會加的 WHERE 與遮罩欄位） | `POST /v1/iam/scope/simulate` | 唯讀 |
| 欄位隱碼 | 此身分每個敏感欄位的策略（來自 masking.yaml） | `/v1/iam/me` | 唯讀；揭露走審批 |
| 我的 session | 同帳號的 session 清單（建立、最近活動、IP 遮罩） | `GET /v1/iam/sessions` | 本人 |

## 4. 契約

新增（contract PR）：`GET /auth/providers`、`GET /auth/login`、`GET /auth/callback`、`POST /auth/ldap`、`POST /auth/logout`、`POST /auth/token`、`GET /v1/iam/me`、`GET /v1/iam/sessions`；schema `Me`。既有 `/v1/iam/roles`、`/v1/iam/assignments`、`/v1/iam/scope/simulate` 不變。

## 5. 部署與設定

- 密鑰只放 secret_ref（OpenBao／Secret Manager）：Keycloak client secret、Entra client secret 或憑證、AD bind 密碼與 CA、API token 簽章私鑰。
- 反向代理（Ingress）：前端靜態與 `/auth`、`/v1` 同網域；設 `Content-Security-Policy`（含 `frame-ancestors 'none'`）、`Strict-Transport-Security`、`X-Content-Type-Options`。
- Keycloak：Helm values 加 `keycloak: { enabled: true, realmImport: dmp-realm.json }`；realm 匯入檔含 client、IdP（Entra）、User Federation（AD）、群組與 mapper（下一步產出）。

## 6. 測試

- `python3 -m backend.auth`：Entra 授權碼 + PKCE + JWKS + amr + overage、AD bind + 巢狀群組 + 注入／速率限制、session 閒置／絕對逾時／CSRF／同時上限／step-up、API token 簽發／竄改／過期、FastAPI 路由（providers → login 302 → callback 302 + cookies → me → token → ldap 第一步 → logout；未登入 401；缺 CSRF 403）。
- `web`：vitest（policy 蘊含／遮罩渲染／矩陣編輯／聯集；api client 的 CSRF、401、403）、`tsc --noEmit`、`vite build`；Playwright 截圖（登入頁、權限中心五個分頁、手機版）無 console error、無橫向溢出。
- 待整合測試：真 Keycloak（docker）realm 匯入 + 五個示範帳號登入；Entra 測試租戶；客戶 AD 測試 OU。

## 7. 待確認（客戶）

1. 是否接受 Keycloak 作身分代理（建議），或要求直連 Entra ID／AD。
2. Entra 租戶、App registration 由誰建；groups claim 是否允許；Conditional Access 是否能對本 App 強制 MFA。
3. 機房 AD：LDAPS 憑證鏈、服務帳號、五個群組名稱、是否要 Kerberos 單一登入。
4. session 政策：閒置 30 分鐘／絕對 8 小時／IP 綁定是否符合內規。
5. 外包本地帳號是否允許（預設只允許自訂角色 + TOTP + 90 天到期）。
