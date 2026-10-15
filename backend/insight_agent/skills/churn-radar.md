# Skill：流失預警（churn-radar）

## 目標
找出高價值但行為衰減的客群，提議分群與即時規則。

## 步驟
1. `retention.run`（cohort=bond_deal，days=[7,14,30]）與 `distribution.run`（metric=fee_income）找「過去 90 天手續費前 20% 但近 30 天無登入」。
2. `segment.preview` 估人數與畫像；人數 < 200 標示樣本小。
3. 建立 risk 洞察並 `segment.propose`；若存在明確觸發點（例：登入後 10 分鐘無看盤），`trigger.propose`。
