# approvals — 審批單與匯出申請

docs/10 §5（匯出口徑）、§6（審批流程）、§7（審計事件）；openapi `Approvals`／`Exports`。

| 端點 | 做什麼 | 權限 |
|---|---|---|
| `GET /v1/approvals?scope=to_me｜mine｜all` | 待我審批（類型的審批角色含我的角色）／我的申請／全部（admin） | 登入即可 |
| `POST /v1/approvals/{id}/decide` | 核可／退回（退回需意見）；不能審自己的申請（admin 例外）；呼叫受控物件 hook 回寫（匯出 → approved／rejected；分群 → 生效／退回）；7 天未審自動 expired | 類型的審批角色：匯出／分群／推送／agent 提議／L2 揭露 = 數位經營，L3 揭露 = admin |
| `POST /v1/exports` | 申請匯出用戶列表或分群成員：需 `users.export` 模組權限、用途 4–200 字、保存天數；先估列數（≤ masking.yaml `export.max_rows`）；開 export 審批單；審計 `export.requested` | users.export |
| `POST /v1/exports/{id}/run` | 核可後 7 天內由申請者本人產檔：`authorize(action="export")` 帶 approval_id，欄位策略取 masking.yaml 的 `export` 策略（比 UI 嚴一級：aum／amount 區間化、one_id 尾碼）；CSV 檔頭註解（申請單、使用者、時間、浮水印、用途、保存天數）+ 每列 `_watermark`；審計 `export.generated` | 申請者 |
| `GET /v1/exports/{id}/download` | 下載 CSV（text/csv，UTF-8 BOM）；審計 `export.downloaded` | 申請者 |

分群：待審批的分群建立／送審時也開一張 `segment` 審批單，從審批中心核可會回寫分群；直接打 `/v1/segments/{id}/approve` 則同步關閉審批單。

```bash
python3 -m backend.api          # 自測含：申請 → 審批單 → 核可 → 產檔（遮罩、浮水印）→ 下載；行銷 403；自己不能審；退回要意見
python3 -m backend.integration.e2e_clickhouse   # 最後一段 exports_stage：黃金 U01 條件匯出列數 == oracle、one_id 遮罩、aum 區間化
```

之後：自定義 SQL 結果匯出（同一套單據）、欄位揭露申請（reveal_l2／l3 類型已在審批單裡，缺申請端點與有時效的 Subject.reveals）、推送單、檔案到期清理與物件儲存。
