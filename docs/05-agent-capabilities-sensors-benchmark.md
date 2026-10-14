# Agent 能力設計：以神策 AI 能力為參考

> 版本 v0.2（2026-10-14）。調研神策 2025–2026 年公開的 AI 產品能力，據以修訂本平台的 agent 能力清單、工具目錄與 agent 註冊表。

## 1. 神策 AI 能力調研摘要

| 產品／能力 | 內容 | 來源 |
|---|---|---|
| **神策分析 Copilot** | 大模型驅動的「智能數據問答」：**指標搜尋**（口語化找指標與經營概覽）、**智能分析**（理解問題後**自動配置分析模型**，例如同比分析，不需使用者懂分析模型）。設計思路：把 NL2SQL 的「填空題」降維成「在既有分析模型上做選擇題」；結果**顯示分析模型與指標來源**供校驗；結果不符預期可**手動調整並讓 Copilot 持續學習**。2025 年完成網信辦算法備案。 | 賽迪網、極客公園 |
| **AI 智能分析師**（2025/4） | 以對話完成分析；**自動識別異常指標**（輸入指標或問題 → 自動分析關聯指標與關聯維度的變化）；**行業知識庫**（2,500+ 企業 know-how 轉成可計算知識，讓 AI 能關聯跨維度指標）；分鐘級產生**全局診斷報告**。 | 中華網、知乎 |
| **SensorsAI**（海外版） | 深度整合於神策分析平台的企業級智能業務助理：自然語言互動 + 意圖識別 + 自動執行；面向不想自行配置分析模型的業務使用者。 | sensorsdata.com |
| **神策分析 MCP Server**（內測） | 基於 Open API，讓外部 AI 助理（Cursor、Claude Desktop、Windsurf）能查數據、擴展。 | 知乎 |
| **智能體（2026 官網）** | 端到端跑完「**分析診斷 → 策略輸出 → 自動化執行 → 效果復盤**」閉環；把資料基礎設施封裝為 Agent 可調用的 **Skills 技能套件**；Agent 框架含模型調用、介面封裝、MCP 管理，向上延伸出分析、標籤、策略生成等智能體；已接入 DeepSeek 等模型。 | sensorsdata.cn |
| 資料接入 | 可視化資料接入框架，與資料建模域解耦；資料倉表**雙向映射**只存一份；SDK／批量匯入／倉表映射／自訂插件。 | 快科技轉載 |

對本平台的啟示：
1. **「選擇題」而非 NL2SQL**：agent 的核心動作是「把問題映射到既有分析模型 + 語意層指標」，不是生成任意 SQL。本平台已有語意層與分析模型 API，正好承接。
2. **可校驗、可修正、可學習**：每個回答都要顯示「用了哪個分析模型、哪個指標、什麼篩選」，使用者能改參數重跑，修正回饋進入 few-shot／排序。
3. **異常 → 關聯指標 → 關聯維度** 的自動下鑽是標配。
4. **行業知識庫**是差異化：證券業的指標關聯（例：委託失敗率 ↔ 行情波動 ↔ 系統負載；開戶量 ↔ 行銷投放 ↔ 市場熱度）要做成可計算知識。
5. **閉環**：分析 → 策略（分群＋觸達規則）→ 執行（MA／營業員 App）→ 復盤（增量、歸因）。本平台已有分群／觸發／歸因模組，agent 要能串起來，但執行需審批。
6. **MCP 對外**：工具閘道同時服務內部 agent 與外部 AI 客戶端。

## 2. 本平台 agent 能力清單（修訂）

| # | 能力 | 對標神策 | 實作要點 | 階段 |
|---|---|---|---|---|
| A1 | **指標搜尋** | Copilot 指標搜尋 | `semantic.search`（向量 + 關鍵字）回傳指標／事件／維度／分群與口徑；支援口語（「開戶的人」→ `new_accounts`） | v1.0 |
| A2 | **智能分析（選擇題式）** | Copilot 智能分析 | `analysis.configure`：NL → `{model: events|funnel|path|attribution|users, params}`；回傳**配置卡**（模型、指標、篩選、分組、時間）供確認或修改後執行 | v1.0 |
| A3 | **可校驗回答** | 顯示模型與指標來源 | 每則回答附：分析模型、指標定義連結、SQL、掃描量、warnings；「在分析頁開啟」一鍵帶參數 | v1.0 |
| A4 | **修正與學習** | 手動調整→持續學習 | 使用者修改配置卡並重跑 → 記錄 `(question, final_config)` 配對進 Knowledge Pack，作為 few-shot 與排序依據 | v1.0 |
| A5 | **異常識別與自動下鑽** | AI 智能分析師 | `anomaly.scan` → 關聯指標（知識庫的指標關聯圖）→ 關聯維度逐一 `metric.query` → 主因 | v1.0 |
| A6 | **看板／報表解讀** | 全局診斷報告 | `dashboard.explain`：對看板所有卡片跑摘要（趨勢、異常、對比），產出診斷報告；排程日報／週報 | v1.5 |
| A7 | **行業知識庫** | 行業 know-how | Knowledge Pack 內「證券指標關聯圖」+「場景 SOP」；可由分析師維護；agent 以 `knowledge.search` 取用 | v1.5 |
| A8 | **策略輸出** | 策略輸出 | `segment.propose` + `trigger.propose` + 建議文案／渠道；附預估人數與歷史相似策略成效 | v1.5 |
| A9 | **自動化執行（審批）** | 自動化執行 | 審批後 `campaign.execute` 推送到 MA／營業員 App；agent 不能自行執行 | v2.0 |
| A10 | **效果復盤** | 效果復盤 | 活動結束事件觸發 `campaign-review`：漏斗、歸因、對照組增量 → 洞察 + 報告 | v1.5 |
| A11 | **MCP 對外** | MCP Server | 工具閘道以 MCP 協定開放給 Cursor／Claude Desktop／AI Coach；同一權限模型 | v1.0 |
| A12 | **證據覆核** | — | reviewer agent 重跑證據、調整信心度（本平台既有） | v1.0 |
| A13 | **法遵守門** | 算法備案 | 個資輸出攔截、數字必須來自工具、審計；對應金管會「金融業運用 AI 指引」的可解釋、問責與風險管理要求 | v1.0 |

## 3. 工具目錄新增（`backend/mcp/tools.yaml`）

| 工具 | 說明 |
|---|---|
| `analysis.configure` | NL → 分析模型配置（選擇題），回傳配置卡與信心度，不執行 |
| `analysis.run` | 以配置卡執行（內部分派到 metric.query / funnel.run / path.run / attribution.run / users.list） |
| `users.list` | 用戶列表（條件 → 人數預估 + 分頁列表，脫敏） |
| `dashboard.explain` | 看板解讀：對每張卡片做趨勢／異常／對比摘要 |
| `knowledge.relations` | 取指標關聯圖（證券行業知識庫） |
| `campaign.execute` | 執行已審批策略（僅在審批通過後可呼叫） |
| `feedback.record` | 記錄使用者修正（question → final_config） |

## 4. Agent 註冊表新增（`backend/insight_agent/agents.yaml`）

| agent | 觸發 | 說明 |
|---|---|---|
| `analyst-copilot` | 提問 | A1–A4：指標搜尋、選擇題式配置、可校驗回答、修正學習（取代原 `ask` 的核心） |
| `dashboard-reporter` | 排程（日／週） | A6：看板解讀與診斷報告 |
| `strategy-loop` | 洞察確認後 | A8–A10：策略 → 審批 → 執行 → 復盤 |

## 5. 問答流程（選擇題式）

```
使用者：「iOS 上週開戶轉化比 Android 差多少？」
  1. semantic.search → new_accounts / open_to_fund_rate / platform
  2. analysis.configure → 配置卡：
       模型＝漏斗  步驟＝[account_open_start, account_open_submit, account_open_approved]
       分組＝platform  時間＝上週  對比＝前一週
     信心 0.86 → 顯示給使用者（可改）
  3. analysis.run → 數字 + 圖 + SQL + warnings
  4. 回答：結論一句 → 證據 → 注意事項（caveats）→ 下一步（分群／觸發提議）
  5. 使用者改「步驟加入 fund_deposit」重跑 → feedback.record
```

## 6. 與神策的差異（本平台刻意不同之處）
- 單一客戶細查一律需理由與審計；agent 不能以姓名查人（神策 Copilot 面向中國市場，法遵框架不同）。
- 執行類動作永遠需要人審批（金融業）。
- 工具與語意層完全開源可替換；LLM 透過企業閘道，可用本地模型以符合資料不出境。

## 參考來源
- [神策分析 Copilot 成功通過網信辦算法備案（賽迪網）](https://www.ccidnet.com/cpfwyjs/89115.jhtml)
- [神策數據黃震昕：大模型助力營銷智能化升級（極客公園）](https://www.geekpark.net/news/341786)
- [神策 AI 智能分析師：開啟數據分析新紀元（中華網）](https://tech.china.com/articles/20250417/202504171661742.html)
- [神策分析 MCP Server 啟動內測（知乎）](https://zhuanlan.zhihu.com/p/1900249271854428850)
- [神策數據官網（智能體、三大引擎）](https://www.sensorsdata.cn/)、[神策三大引擎](https://www.sensorsdata.cn/features/overview.html)
- [神策數據接入 DeepSeek（CSDN）](https://blog.csdn.net/sensorsdata/article/details/146323144)
- [SensorsAI（海外產品頁）](https://buy-with-aws.sensorsdata.com/)
- [神策 CDP 可視化數據接入框架（快科技）](https://news.mydrivers.com/1/999/999807.htm)
