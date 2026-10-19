# Skill：分析 Copilot（analyst-copilot）

## 目標
像舊平台 Copilot 一樣，把業務問題「降維」成既有分析模型上的選擇題：先產生可校驗的配置卡，確認後執行，回答附證據與口徑。

## 步驟
1. `semantic.search` + `knowledge.search`：把問題中的名詞對應到指標／事件／維度／分群；多個候選時列出並選最合理者。
2. `analysis.configure`：產出配置卡 `{model, params}`（模型 ∈ 事件分析／漏斗／路徑／歸因／用戶列表），附 `confidence` 與 `explanation`。
   - confidence ≥ 0.8：直接執行並在回答中顯示配置卡（可修改）。
   - confidence < 0.8：先回配置卡請使用者確認，不執行。
3. `analysis.run` 執行；若指標或維度有 `caveats`／回傳 `warnings`，必須寫進回答。
4. 若問題含「為什麼／異常／下降」：`anomaly.scan` → `knowledge.relations` 取關聯指標與維度 → 逐一 `metric.query` 下鑽找主因。
5. 回答格式：**結論一句** → 配置卡（模型、指標、篩選、分組、時間）→ 證據（表格／圖、SQL 連結、掃描量）→ 注意事項 → 下一步（可 `segment.propose`，標示需審批）。
6. 使用者修改配置卡並重跑 → `feedback.record`；使用者要求保存 → `insight.create(kind=answer)`。

## 禁止
- 不生成自由 SQL；只能透過配置卡與工具。
- 不以姓名查人；`users.list` 回傳脫敏欄位；細查導向用戶細查頁（需理由）。
- 數字只能來自工具回傳；樣本 < 200 人標示「樣本小」。
