# Skill：漏斗診斷（funnel-doctor）

## 目標
每日檢查三條核心漏斗（看盤→委託→成交、開戶→入金→首筆成交、推播→開啟→成交），找流失最大步驟與分群差異，提出假設與驗證。

## 步驟
1. `funnel.run` 近 7 天 vs 前 7 天；步驟轉化率下降 ≥ 3 pt 視為訊號。
2. 以 platform / app_version / channel / lifecycle `group_by` 找差異最大的分群。
3. `interval.run` 看該步驟的時間分布是否拉長；`path.run` 看流失者離開後去了哪。
4. 形成假設（例：某版本 KYC 頁中斷），用一次獨立查詢驗證。
5. 建立 funnel 洞察；如有明確受影響客群，`segment.propose`（status=pending_approval）。
