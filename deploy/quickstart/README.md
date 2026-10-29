# 單機測試環境（真的能登入操作）

一台機器跑完整系統：**ClickHouse → API（同源提供前端）→ 本機帳號登入（密碼 + TOTP）→ 分析／分群／審批／數據整合／儀表板**，以及資料匯入驗證：**舊平台查詢 API → 代碼化／去個資 → ClickHouse → 對帳**。適合筆電或一台 GCP VM（e2-standard-4，4 vCPU／16 GB，Ubuntu 22.04 或 Rocky 9）。不是正式部署（正式走 `deploy/helm`：Keycloak／Entra ID 登入、PostgreSQL 控制面、密鑰庫、TLS）。

```bash
sudo apt-get install -y python3-pip nodejs npm curl       # 或 dnf
pip3 install -r backend/requirements.txt
deploy/quickstart/run_local.sh up                          # ClickHouse（固定 25.8 LTS）+ 建表 + 建前端 + API（http://127.0.0.1:8000 就是前端）
DMP_SEED_PASSWORD='改成至少14字的密碼' deploy/quickstart/run_local.sh seed   # 合成資料 + 五種角色帳號（印 TOTP 註冊網址）+ 範例儀表板
deploy/quickstart/run_local.sh code admin                  # 沒有手機驗證器時，印目前 6 位數（只在測試環境這樣用）
```

然後開 http://127.0.0.1:8000 → 「使用本機帳號」→ 帳號 `admin` + 密碼 → 驗證器 6 位數 → 進總覽看「債券業務日報（範例）」。

| 帳號 | 角色 | 看得到什麼 |
|---|---|---|
| admin | 系統管理員 | 全部；admin 權限 15 分鐘後降級，頂欄輸入驗證碼再驗一次即回復（step-up） |
| marketing | 行銷 | 分析、分群（需審批）、分享給行銷的儀表板；金額分桶、用戶列表隱碼 |
| analyst | 數據分析 | 分析、匯出（走審批）、建儀表板 |
| ops | 數位經營 | 審批分群／匯出／揭露 |
| it | 資訊管理 | 數據整合（連線、數據表、同步任務）、治理；不看個人 |

帳號管理：`run_local.sh account list | add <帳號> --roles admin,marketing | reset-password <帳號> | reset-totp <帳號> | remove <帳號>`（密碼從 `DMP_PASSWORD` 環境變數或互動輸入；90 天到期）。TOTP 註冊網址貼到 Google／Microsoft Authenticator（或用任何能讀 otpauth URI 的工具產 QR）。

## 這個環境和正式環境的差別

| 項目 | 單機（這裡） | 正式 |
|---|---|---|
| 登入 | 本機帳號（`DMP_LOCAL_ACCOUNTS`，scrypt + TOTP），`DMP_LOCAL_ALLOWED_ROLES='*'` 放開全部角色 | Keycloak／Entra ID／AD；本機帳號只給外包且只能 `custom_*` 角色（`backend/auth/idp.yaml › local_accounts`） |
| Cookie | `DMP_COOKIE_SECURE=0`（沒有 TLS） | 一律 TLS，cookie Secure |
| 控制面 | JSON 檔（`DMP_STORE_PATH`，單一寫入者：`seed` 會先停 API 再寫） | PostgreSQL（`DMP_PG_DSN_REF`） |
| 排程 | API 內背景執行緒（`DMP_TICK=1`：同步任務每分鐘、分群每 10 分鐘）；同步排程存 SQLite（`DMP_SCHED_DB`） | worker（`DMP_ROLE=worker`）持主控租約執行；同步排程與畫面建的模型存控制面 PostgreSQL，「立即執行」排隊由 worker 跑 |
| 密鑰 | `secrets.json` 空檔、代碼化金鑰用環境變數明文 | 密鑰庫 secret_ref |
| 前端 | API 同源提供 `web/dist`（`DMP_WEB_DIST`） | nginx／Ingress 在前面（快取、壓縮、TLS） |

這台機器之後改接 PostgreSQL 控制面時，舊的同步排程（任務、水位、已知欄位、模型）用一次性指令搬過去，重跑不會重複：`DMP_PG_DSN=… python3 -m backend.integration.cli sched import-sqlite --from ~/dmp-local/sched.db`（`DMP_WORK` 預設目錄；執行紀錄與告警不搬）。

給團隊共用的 VM：`DMP_BIND=0.0.0.0 deploy/quickstart/run_local.sh up`，前面放 TLS（Caddy／nginx）後把 `DMP_COOKIE_SECURE` 拿掉；VM 的好處是常駐、網路能到客戶內網（客戶的舊平台是私有化，只有內網或 VPN 能到）。

## 資料匯入驗證

```bash
deploy/quickstart/run_local.sh e2e                         # 合成資料端到端（查詢編譯器黃金測試、同步排程、API）
export LEGACY_BASE_URL=https://sn3.data-di.com LEGACY_PROJECT=taipei_uat LEGACY_API_SECRET='（從舊平台開放 API 金鑰頁複製）'
deploy/quickstart/run_local.sh import --date 2026-10-01    # 連線測試 → 導出該日 → 代碼化 → 載入 → 對帳
deploy/quickstart/run_local.sh status | down
```

| 目的 | 需要什麼 |
|---|---|
| 只驗證舊平台 API 連得上、金鑰有效、欄位與筆數 | 不需要 VM：任何能連到舊平台主機的電腦跑 `python3 -m backend.integration.legacy_api test/inventory/columns` |
| 驗證導出檔格式與代碼化（不進 ClickHouse） | 不需要 VM：`legacy_api export` + `legacy_format.stage` 在本機跑 |
| 完整匯入驗證（進 ClickHouse、對帳、前端查） | 一台機器：筆電或 GCP VM 都可以 |

舊平台連線鑑權兩種都支援：舊版 `?token=`（專案設定 › 查詢 API 的 API Secret）與 SBP「開放 API 金鑰」（HTTP header `sensorsdata-token`）。`test` 會先用設定的模式，失敗自動試另一種並告訴你該改哪個。金鑰只能放環境變數 `LEGACY_API_SECRET`（正式環境是 secret_ref），不要寫進命令列或設定檔。

## 常見問題

- **登入說「帳號或密碼錯誤」但密碼沒錯**：連錯 5 次會鎖 15 分鐘；或帳號已過 90 天（`account reset-password`）。
- **驗證碼一直錯**：機器時間不準（TOTP 容忍 ±30 秒）；同一個驗證碼不能用兩次。
- **seed 後看不到範例儀表板**：`seed` 會先停 API 再寫、寫完再起；若手動改了 `store.json`，重啟 API（`down`／`up`）。
- **儀表板卡片「沒有資料」**：卡片預設跟儀表板的「最近 N 天」走，而合成資料固定在 2026-09-20 … 10-16；範例卡片是釘住區間的，自己存的卡片請在分析頁選好日期後再存（或把儀表板區間改成涵蓋的範圍）。
