# 30. 儀表板進階：MCP／AI 問數生成卡片與 HTML 看板（第二階段規劃）

狀態：**規劃**（第二階段）。第一階段的儀表板（docs/27 C9）已可用：任何分析頁的查詢存成卡片、以檢視者權限重算、分享給角色或使用者。本文規劃在此之上加「問數」與「HTML 看板」兩個進階選項，全部沿用既有查詢編譯器、Policy Engine、審計與 MCP 工具閘道（docs/03、`backend/mcp/tools.yaml`）。

## 0. 結論先講

| 選項 | 做什麼 | 怎麼做（不新發明） | 風險控制 |
|---|---|---|---|
| A. 問數建卡片 | 「上週各平台成交金額、看盤到成交漏斗、登入後 4 週留存」一句話 → 三張卡片進儀表板 | `analysis.configure`（選擇題式：在既有分析模型與語意層指標上做選擇）→ 使用者確認配置卡 → `POST /v1/dashboards/{id}/cards` | 不生成任意 SQL；卡片存的是查詢條件，檢視者各自重算；信心度低於門檻時只給候選不自動存 |
| B. 看板解讀 | 對看板每張卡做趨勢、異常、對比摘要 → 診斷報告草稿 | `dashboard.explain`（既有契約）+ `anomaly.scan` | 摘要帶 `warnings`（樣本小、抑制列、串流缺欄位）；只讀 |
| C. HTML 看板 | 看板 → 單檔 HTML（inline SVG），寄給主管／貼 Teams／MA 內部週報 | 伺服器端以檢視者身分重算卡片 → 渲染既有圖表元件的 SVG → 產物存物件儲存 + 到期 | 不帶明細與 SQL；套遮罩、K 匿名、浮水印（產生者、時間、到期）；下載走審計 |
| D. 定時寄送 | 每週一 08:00 寄 HTML 看板給角色或名單 | 排程器（`backend/integration/scheduler.py` 同一個）+ C | 收件名單只能是本系統使用者或已核可的信箱網域；寄送走審批（digital_ops） |

拖曳排版、PDF 匯出、嵌入 token 屬 UI 便利功能，排在 A–D 之後。

## 1. 問數建卡片（A）

### 1.1 流程

```
使用者在儀表板按「AI 生成卡片」→ 輸入問題（可多句）
  → MCP dashboard.compose(question, dashboard_id, time)
      → analysis.configure：問題 → 配置卡（model、params、candidates、confidence、explanation）
      → 每張配置卡用 compile_kind 乾跑一次（授權 + 編譯；不執行）
  → 回傳卡片草稿（標題、種類、查詢、信心度、解釋、SQL 預覽）
使用者逐張確認／改標題／刪除 → POST /v1/dashboards/{id}/cards（既有端點，建立者身分編譯）
```

- 問題只能映射到五種分析（events／funnel／retention／path／attribution）與語意層目錄（`/v1/semantic/catalog`）裡的事件、維度、用戶屬性；對不上的部分回「候選」讓使用者選，不猜。
- 時間：問題裡沒說就用儀表板的「最近 N 天」；說了（「上週」「10 月」）就釘住區間（`time.pinned`）。
- 信心度 < 0.6 的卡片預設不勾選；< 0.3 不給卡片只給候選事件／指標。
- 一次最多 6 張卡片；每張乾跑受 `gateway.budgets.per_call_scan_bytes` 限制（EXPLAIN ESTIMATE）。

### 1.2 契約草案（第二階段 contract PR）

```yaml
/v1/dashboards/{id}/ask:
  post:
    summary: 問數 → 卡片草稿（不存）。需 overview.use 與 ai_ask.use；審計 dashboard.asked（question、cards、confidence）
    requestBody: { question: string, time?: { preset|from|to } , max_cards?: int }
    responses:
      200: { cards: [{ title, kind, query, chart, confidence, explanation, sql_preview, warnings[] }], candidates: [...], run_id }
      422: 問題無法映射到任何分析模型（回 candidates）
```

MCP 工具 `dashboard.compose`（已加入 `backend/mcp/tools.yaml`，phase 2）在閘道內呼叫同一段邏輯；agent 代表使用者（`on_behalf_of`），權限與審計與 UI 相同。

### 1.3 評測

`backend/insight_agent/evals` 加 20 題 golden：問題 → 期望的配置卡（model + 關鍵 params）。通過標準：model 正確 ≥ 95%、關鍵 params（事件、分組、時間）正確 ≥ 85%、沒有任何一題產生語意層外的欄位。

## 2. 看板解讀（B）

- 入口：儀表板詳情「解讀這個看板」。對每張卡片取資料（與畫面同一份、同一身分），跑 `anomaly.scan`（季節性／門檻）與上期對比，組成摘要：三句話 + 每卡一行 + warnings。
- 產出是「洞察物件」（docs/03 §7）草稿，可存成 `/v1/insights`，也可直接貼進 C 的 HTML 看板當「本週重點」。
- 不做：自動發送、自動改卡片。

## 3. HTML 看板（C）

### 3.1 為什麼是單檔 HTML

主管與業務單位看報表的地方是信箱與 Teams，不會登入 DMP；神策的「概覽分享」也是這個用途。單檔 HTML（inline SVG、無外部資源、無 JS 取數）可寄送、可離線開、可貼進 wiki，而且**內容在產生當下就固定**，不會因為收件人沒權限而壞掉。

### 3.2 產生規則

| 項目 | 規則 |
|---|---|
| 身分 | 以「產生者」的權限重算每張卡（遮罩、行級、K 匿名都照產生者）；收件人看到的就是產生者能看到的 |
| 內容 | 卡片圖（與前端同一套 SVG 繪圖：折線／長條／漏斗／留存矩陣／桑基／歸因）、數字卡、卡片註記、解讀摘要（B 的產出，可選）；**不含**明細列、SQL、血緣、匯出連結 |
| 浮水印 | 右下角：產生者、產生時間、到期日、「內部使用」；抑制列顯示「< K」 |
| 到期 | 預設 14 天；物件儲存上的檔案到期刪除；超過到期的 `url` 回 410 |
| 審計 | `dashboard.rendered`（dashboard、產生者、audience、expires_at）、`dashboard.render.downloaded`（誰、何時、IP 遮罩） |
| 大小 | 每張卡 SVG ≤ 200 KB，整檔 ≤ 3 MB；超過改降解析度（點數抽稀） |
| 敏感 | 含 L3 以上欄位分組的卡片（例：按營業員）不渲染，改顯示「此卡片含代碼化識別欄位，不納入分享看板」 |

### 3.3 契約草案

```yaml
/v1/dashboards/{id}/render:
  post:
    summary: 產生 HTML 看板（伺服器渲染；需 overview.use；含 L3 分組的卡片不納入；審計 dashboard.rendered）
    requestBody: { time?: {...}, audience: string, expires_days?: int(1–30), include_explain?: bool }
    responses: { 202: { artifact_id, url, expires_at, cards_included, cards_skipped[] } }
/v1/dashboards/{id}/renders:
  get: { summary: 歷次產物（到期後只剩紀錄） }
/v1/dashboards/{id}/renders/{artifact_id}:
  get: { summary: 下載 HTML（需登入；分享連結另走簽章 token，30 天內有效；審計） }
```

MCP 工具 `dashboard.render_html`（phase 2）供 agent 在「每週報告」skill 裡呼叫。

### 3.4 實作落點

- `backend/api/dashboards.py`：`render` 端點；卡片資料走既有 `execute_kind`。
- `backend/render/svg.py`（新）：與 `web/src/analytics/*.tsx` 同構的 SVG 產生器（折線、長條、漏斗、留存矩陣、桑基、歸因），共用一份配色與 K 匿名呈現規則；用 golden 資料集做快照測試（HTML 大小、無外部資源、無 L3 欄位字串）。
- 物件儲存：quickstart 用本機目錄（`DMP_EXPORT_DIR` 同目錄）；正式用 GCS 並設 lifecycle 到期。

## 4. 定時寄送（D）

- 排程：同一個排程器（cron），任務類型 `dashboard_render_mail`；每次執行 = C + 寄信（SMTP relay 由客戶提供；`secret_ref` 存密碼）。
- 收件名單：本系統使用者（依角色）或白名單網域（`deploy/*.yaml` 設定）；名單變更走 digital_ops 審批。
- 失敗重試 3 次；寄出與失敗都寫審計。

## 5. 權限總表

| 動作 | 模組／權限 | 審計 |
|---|---|---|
| 問數建卡片 | overview.use + ai_ask.use | dashboard.asked、dashboard.card.added |
| 看板解讀 | overview.view + ai_ask.use | agent.tool_called（dashboard.explain） |
| 產生 HTML | overview.use | dashboard.rendered |
| 下載／分享連結 | overview.view（登入）或簽章 token | dashboard.render.downloaded |
| 定時寄送設定 | overview.use + digital_ops 審批 | dashboard.schedule.created／approved |

## 6. 順序與工作量（第二階段）

| 順序 | 項目 | 工作量 | 依賴 |
|---|---|---|---|
| 1 | C HTML 看板（SVG 產生器 + render 端點 + 到期 + 審計） | 2 週 | 無 |
| 2 | A 問數建卡片（`analysis.configure` 接語意目錄 + ask 端點 + UI 確認流程 + 20 題評測） | 3 週 | AI 閘道啟用（docs/22 §1 第二階段） |
| 3 | B 看板解讀 | 1 週 | A 的閘道 |
| 4 | D 定時寄送 | 1 週 | C、客戶 SMTP |
| 5 | 拖曳排版、PDF、嵌入 token | 1–2 週 | — |

## 7. 第一階段先做的事（已完成或在本版）

- 儀表板頁加「AI 生成卡片」與「產生 HTML 看板」入口，標示第二階段，點開顯示本文的流程說明，不假裝可用。
- MCP 工具契約（`dashboard.compose`、`dashboard.render_html`）已加入 `tools.yaml`（phase 2）。
- 卡片資料 API 已是「以檢視者身分重算」，C 與 A 都直接沿用，不需改資料面。
