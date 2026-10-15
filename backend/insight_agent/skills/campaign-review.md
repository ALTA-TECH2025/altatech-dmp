# Skill：活動復盤（campaign-review）

## 目標
活動結束後自動產出曝光→點擊→轉化復盤，含歸因與對照組增量。

## 步驟
1. `funnel.run`（campaign_exposed → act_click → bond_deal，window=14d，filter campaign_id）。
2. `attribution.run`（model=position 與 linear 各一次）。
3. 以未曝光對照群（segment）跑同窗口轉化率，計算增量。
4. `insight.create`（kind=campaign）並 `report.compose` 給行銷。
