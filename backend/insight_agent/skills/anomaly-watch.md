# Skill：異常偵測（anomaly-watch）

## 目標
每 15 分鐘掃描核心指標，找出偏離並定位主因，產出 anomaly 洞察。

## 核心指標
dau, login_users, orders, order_reject_rate, turnover, deposits, new_accounts（定義見 semantic/metrics.yaml）。

## 步驟
1. 對每個指標呼叫 `anomaly.scan`（grain=hour，近 14 天，sensitivity=medium）。
2. 有異常者：以 platform、channel、app_version、branch_id 逐一 `metric.query` 下鑽，找出貢獻最大的維度值（偏離貢獻 ≥ 50% 即為主因）。
3. `insight.search` 查近 7 天是否已有相同主因的洞察；有則更新而非重複建立。
4. 建立洞察：title 含指標、方向、幅度、主因；severity 依幅度（> 30% critical，> 15% high，其餘 medium）；evidence 含兩次查詢。
5. 若主因為 `order_reject_rate` 且 reason_code 以 SYS 開頭，附「疑似系統事件，請通知交易系統值班」。

## 禁止
- 不下鑽到個人；不使用 profile.*。
