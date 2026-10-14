# Skill：看板解讀（dashboard-reporter）

## 目標
每個工作日早上對「經營總覽」看板產出診斷報告：每張卡片的趨勢、異常、對比與可能原因。

## 步驟
1. `dashboard.explain(dashboard_id=exec_overview, time=昨日 vs 前 7 日均)`。
2. 對標記異常的卡片：`anomaly.scan` → `knowledge.relations` → `metric.query` 下鑽主因。
3. 以 `report.compose` 組成：一句總結、三個最重要變化（含數字與主因）、建議關注；附每項的證據。
4. `insight.create(kind=anomaly|opportunity)` 給重要發現；報告推送訂閱者。
