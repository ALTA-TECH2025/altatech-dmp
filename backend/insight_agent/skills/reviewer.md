# Skill：證據覆核（reviewer）

## 目標
對 agent 建立的洞察重跑證據查詢、檢查口徑與樣本量，更新 confidence。

## 規則
- 重跑每條 evidence；數字差異 > 5% → confidence −0.3 並標記「不可重現」。
- 樣本 < 200 → confidence 上限 0.5。
- 使用的指標若有 caveats 未出現在 summary → 補註並 −0.1。
- 不新增結論；只調整 confidence 與附註。
