# 單機測試環境（資料匯入驗證用）

一台機器就能跑完整路徑：**神策查詢 API → 代碼化／去個資 → ClickHouse → 對帳 → API → 前端**。適合筆電或一台 GCP VM（e2-standard-4，4 vCPU／16 GB，Ubuntu 22.04 或 Rocky 9）。不是正式部署，正式走 `deploy/helm`。

```bash
sudo apt-get install -y python3-pip nodejs npm curl       # 或 dnf
pip3 install -r backend/requirements.txt
deploy/quickstart/run_local.sh up                          # ClickHouse（固定 25.8 LTS）+ 建表 + API :8000 + 前端 :5173
deploy/quickstart/run_local.sh e2e                         # 合成資料端到端（含查詢編譯器黃金測試與 API）
export SA_BASE_URL=https://sn3.data-di.com SA_PROJECT=taipei_uat SA_API_SECRET='（從神策開放 API 金鑰頁複製）'
deploy/quickstart/run_local.sh import --date 2026-10-01    # 連線測試 → 導出該日 → 代碼化 → 載入 → 對帳
deploy/quickstart/run_local.sh down
```

要不要開 VM？

| 目的 | 需要什麼 |
|---|---|
| 只驗證神策 API 連得上、金鑰有效、欄位與筆數 | 不需要 VM：任何能連到神策主機的電腦跑 `python3 -m backend.integration.sensors_api test/inventory/columns` |
| 驗證導出檔格式與代碼化（不進 ClickHouse） | 不需要 VM：`sensors_api export` + `sensors_format.stage` 在本機跑 |
| 完整匯入驗證（進 ClickHouse、對帳、前端查） | 一台機器：筆電或 GCP VM 都可以；VM 的好處是常駐、團隊共用、網路能到客戶內網（正式客戶環境的神策是私有化，只有客戶內網或 VPN 能到） |

神策連線鑑權兩種都支援：舊版 `?token=`（專案設定 › 查詢 API 的 API Secret）與 SBP「開放 API 金鑰」（HTTP header `sensorsdata-token`）。`test` 會先用設定的模式，失敗自動試另一種並告訴你該改哪個。金鑰只能放環境變數 `SA_API_SECRET`（正式環境是 secret_ref），不要寫進命令列或設定檔。

前端：`vite preview` 不代理 `/auth`、`/v1`，正式請用 nginx 把三者放同一網域；開發時改用 `cd web && npm run dev`（已設代理到 :8000）。登入需要 IdP（docs/24）；沒有 IdP 時可先用 `python3 -m backend.api` 的假 session 方式測 API。

控制面儲存：quickstart 預設用 JSON 檔（`DMP_STORE_PATH`）；要用 PostgreSQL 時起一個 PostgreSQL 16（`docker run -e POSTGRES_PASSWORD=… postgres:16`），設 `DMP_PG_DSN`（正式用 `DMP_PG_DSN_REF` 指到密鑰）後重啟 API，表會自動建立在 schema `dmp`。
