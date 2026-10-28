# 前端安全更新與免費掃描紀錄（2026-10-27）

範圍：以 main `bac4c09` 為基礎的前端開發／測試套件升級；不代表正式環境已部署，也不是整個平台的安全認證。

## 升級

| 套件 | 原版本 | 新版本 |
|---|---|---|
| Vite | 6.3.5 | 6.4.5 |
| Vitest | 3.2.4 | 5.0.5 |
| @playwright/test | 1.48.2 | 1.64.1 |

精確直接依賴清單見 [組件清單](32-oss-inventory-and-licensing.md#131-前端鎖定版本2026-10-27)，傳遞依賴見 `web/package-lock.json`。Vitest 5 的 Node 版本需求已寫入 package.json engines。

## 本次本機驗證

環境：Node 24.22.1、npm 11.9.0。這些結果屬本次本機測試，不是 GitHub Actions 結果。

- `npm audit --json`：更新前 6 個受影響套件；更新後 0 個已知漏洞。此數字是套件數，不是不同 CVE 數。
- `npm run typecheck`：通過。
- `npm test`：27 個測試檔、102 項測試通過。
- `npm run build`、`npm run build:demo`：通過；仍有 chunk 超過 500 kB 的體積警告。
- Playwright Chromium 安裝受阻：下載內容為空／無效 ZIP，出現 `End of central directory record signature not found`。新版指定瀏覽器及完整後端 E2E 未執行，不能列為通過。

## 免費程式碼掃描

執行 `semgrep scan --config p/default --metrics=off --json --output /tmp/dmp-semgrep.json backend web/src prototype/js`。使用隔離 Python 環境，不新增產品依賴。掃描 302 個檔案、執行 486 條規則，回報 51 項待人工確認警示。命令退出 0 不代表沒有警示。

| 規則 | 工具等級 | 警示數 |
|---|---|---|
| `javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp` | WARNING | 3 |
| `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected` | WARNING | 4 |
| `python.lang.security.audit.formatted-sql-query.formatted-sql-query` | WARNING | 8 |
| `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` | WARNING | 9 |
| `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` | ERROR | 27 |

這些警示尚未逐項做資料流與可利用性確認；工具的 ERROR／WARNING 不是 CVSS 嚴重度，也不能直接視為已確認漏洞。動態 SQL、URL 與正規表示式需進一步確認輸入來源、參數化、白名單及權限邊界。

掃描限制：18 項掃描錯誤（16 項語法相關錯誤、2 項 prototype/js/app.js 規則逾時），另有超過大小限制的檔案略過，因此掃描並非完整覆蓋。下表保留警示定位，供後續去重與人工複核；未修正或宣稱這些警示已排除。

| 檔案 | 起始行 | 規則 |
|---|---|---|
| `backend/api/store_pg.py` | 60 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/api/store_pg.py` | 60 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 60 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/api/store_pg.py` | 61 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/api/store_pg.py` | 61 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 61 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/api/store_pg.py` | 70 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/api/store_pg.py` | 70 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 70 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/api/store_pg.py` | 80 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 91 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 91 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/api/store_pg.py` | 97 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 97 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/api/store_pg.py` | 140 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/api/store_pg.py` | 140 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/api/store_pg.py` | 146 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/auth/session_pg.py` | 51 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/auth/session_pg.py` | 51 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/auth/session_pg.py` | 81 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/auth/session_pg.py` | 86 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/auth/session_pg.py` | 89 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/auth/session_pg.py` | 113 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/auth/session_pg.py` | 114 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/entry.py` | 70 | `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected` |
| `backend/insight_agent/journal.py` | 203 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/insight_agent/journal.py` | 203 | `python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli` |
| `backend/insight_agent/journal.py` | 203 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/insight_agent/journal.py` | 251 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/insight_agent/journal.py` | 257 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/insight_agent/journal.py` | 288 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/insight_agent/journal.py` | 290 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/integration/e2e_clickhouse.py` | 643 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/integration/e2e_clickhouse.py` | 643 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/integration/legacy_api.py` | 66 | `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected` |
| `backend/integration/runner.py` | 33 | `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected` |
| `backend/integration/runner.py` | 74 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/integration/scheduler.py` | 144 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 66 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/ops/lease.py` | 66 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 120 | `python.lang.security.audit.formatted-sql-query.formatted-sql-query` |
| `backend/ops/lease.py` | 120 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 123 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 126 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 129 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 156 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/ops/lease.py` | 176 | `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query` |
| `backend/tools/demo_fill.py` | 35 | `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected` |
| `prototype/js/app.js` | 1032 | `javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp` |
| `web/src/demo/fetch.ts` | 395 | `javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp` |
| `web/src/sql/sql.ts` | 52 | `javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp` |

## 其他工具與未涵蓋範圍

可免費使用 `pip-audit`（Python 已知依賴漏洞）、Gitleaks（密鑰）、Trivy（映像／設定）及 Syft（SBOM）。本次未執行這些工具；後端浮動依賴及正式容器仍須用實際解析版本與映像掃描。現有 CI 中的容錯設定不應解讀為安全門檻全部通過。

參考安全公告：
- https://github.com/advisories/GHSA-5xrq-8626-4rwp
- https://github.com/advisories/GHSA-7mvr-c777-76hp
- https://github.com/advisories/GHSA-jqfw-vq24-v9c3
