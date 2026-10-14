# Skill：策略閉環（strategy-loop）

## 目標
當洞察被主管「確認」後，把分析轉成策略並走審批 → 執行 → 復盤。

## 步驟
1. 由洞察的 scope 產生目標人群：`segment.preview` 估人數與畫像（< 200 人標示樣本小）。
2. `segment.propose`（status=pending_approval）；若有明確觸發點，`trigger.propose`。
3. 等待審批事件；審批通過後才可 `campaign.execute(approval_id)`；否則結束並記錄。
4. 活動結束（或 14 天後）：`funnel.run` + `attribution.run` + 對照組增量 → `insight.create(kind=campaign)` → `report.compose`。

## 禁止
- 沒有 approval_id 不得呼叫 campaign.execute。
- 不改動既有生效的分群或規則。
