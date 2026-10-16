# semantic/compiler — 語意查詢編譯器（第一階段：事件分析樣板）

docs/23 §4.3 的「最關鍵模組」骨架。輸入語意查詢（dict），輸出 ClickHouse SQL + settings 預算 + 血緣 + 命中層級；Policy（行級、遮罩、K 匿名）在編譯器內注入，UI／REST／MCP 之外不得拼 SQL。

```
parser.py    dict → AST（Query / Metric / Cond / Group / Formula / TimeRange）；對目錄檢查事件名、欄位、運算子、別名
catalog.py   semantic/*.yaml + security/masking.yaml → 欄位解析（實體欄位 / JSON props / 用戶屬性 dictGet 或 JOIN / 分群）
events.py    事件分析樣板：planner（L1 日聚合 MV 或 L0 明細）、聚合、分組、公式、top_n、mau、compare、K 匿名
policy.py    Decision → 行級 WHERE 改寫、欄位遮罩（bucket / round / mask_tail）、預算 settings（budgets.yaml）
oracle.py    純 Python 參考實作（黃金測試的「正確答案」）
golden.py    固定種子資料集（400 用戶 × 24,000 事件，跨月）＋ 30 條查詢 ＋ 10 條必拒查詢；oracle vs ClickHouse 逐列誤差 0；EXPLAIN ESTIMATE 門檻
budgets.yaml human / batch / sql_user 三組 settings；估算門檻 1.5 倍
```

## 查詢形狀

`openapi.yaml` 的 `SemanticQuery`（`metric` + `time` + 扁平 `filters` + `group_by` + `segment` + `compare`）是本編譯器接受的子集；原型「事件分析」用的多指標／巢狀條件／公式是超集（下列）。契約擴充走獨立 contract PR。

```json
{
  "metrics":  [{"event": "bond_order", "agg": "total", "alias": "ord"},
               {"event": "bond_deal",  "agg": "sum", "field": "amount", "alias": "amt",
                "filters": [{"field": "amount", "op": "gte", "value": 1000000}]}],
  "formulas": [{"alias": "avg_ticket", "expr": "amt / ord"}],
  "filters":  {"op": "and", "items": [{"field": "platform", "op": "in", "value": ["iOS", "Android"]},
                                      {"op": "or", "items": [{"field": "$event_hour", "op": "between", "value": [9, 13]},
                                                             {"field": "user.lifecycle", "op": "eq", "value": "新客"}]}]},
  "group_by": ["platform", "market"],  "top_n": 5,
  "time":     {"from": "2026-10-01", "to": "2026-10-16", "grain": "day"},
  "compare":  "previous_period"
}
```

- `agg`（同原型 AGGS）：total、users、avg_per_user、uniq(field)、sum/avg/avg_val/max/min/p25/p50/p75/p90(數值 field)、mau（只能按天）。
- 欄位：事件實體欄位（platform、channel、utm_source、symbol、side、amount…）、`$event_hour`、dimensions.yaml 的維度名（market → `props.market`）、`props.<key>`（可帶 `type`）、`user.<key>`（user_properties.yaml；L3 不在寬表、L4 不存在）、`seg.<segment_id>`。
- 粒度：minute、hour、day、week（週一起）、month、total；時間以台北日期含兩端，`event_date` 走分區裁剪。
- 運算子（同原型 OPS）：string eq/neq/in/nin/like/nlike/set/nset；number eq/neq/gt/gte/lt/lte/between/set/nset；bool true/false；date rel{days}/between/set/nset；segment in/nin。

## 輸出（`Compiled`）

`sql`、`layer`（L1 = events_daily_agg、L0 = events）、`columns`、`settings`（預算）、`lineage`（表 + 指標／事件）、`warnings`（為何沒走 MV）、`k_anonymity`、`masked`（輸出欄 → 策略）、`row_filters`。

輸出列固定為：`bucket`（total 粒度無）、`g1..gn`、各指標別名、各公式別名、`_k`（該列去重用戶數）、`suppressed`（有 Decision 時）；compare 另加 `<alias>_prev`。

## 口徑（與 oracle 一致、黃金測試鎖定）

- 字串欄位未設值為 `''`；JSON 屬性缺值為 NULL；用戶熱屬性缺鍵回 dictGet 預設（`''`／0）。
- `sum` 無列 → 0；`avg`／`max`／`min`／分位數無列 → NULL；人均類分母 0 → NULL；公式除以 0 → NULL。
- 分位數 = `quantileExact`（排序後第 ⌊level×n⌋ 個）。
- K 匿名：每個輸出列（時間桶 × 分組）的去重用戶數 < 20（rbac.yaml）→ 指標全部 NULL、`suppressed = 1`；L1 以 `uniqMerge` 近似值守門。
- 遮罩：對數值欄位的 sum/avg/max/min/分位數輸出套 masking.yaml 的策略（行銷看金額 → `bucket([0,1e5,5e5,1e6,5e6])` 區間標籤；`round(n)` 取整；`hide` 直接拒絕；被遮罩的指標不能進公式）。
- `users` 用 `uniqExact`（誤差 0）；因此 users 不走 MV（MV 的 `uniqState` 為近似）。
- 行級條件：`branch_id IN (...)` 改寫成用戶表 dictGet；`event_date >= today()-n` 保留並可走 MV；agent 範圍（L3）第一階段不支援。

## 測試

```bash
python3 -m backend.semantic.compiler.golden                          # 乾跑：30 條編譯 + oracle、10 條拒絕、L1/L0 選層、遮罩
CLICKHOUSE_BIN=/path/clickhouse python3 -m backend.semantic.compiler.golden   # 真 ClickHouse：逐列誤差 0 + EXPLAIN ESTIMATE
python3 -m backend.integration.e2e_clickhouse                        # 端到端最後一段也會跑 compiler_stage
```

實測（ClickHouse 26.10.1 與 25.8.33 LTS 皆通過）：30 條查詢 7 條走 L1、23 條走 L0；MV 與明細結果一致；SAMPLE 0.5 用戶抽樣落在 30–70%；10 條違規查詢（未登記事件、L4／L3 屬性、按 one_id 分組、mau 非按天、遮罩指標進公式、隱藏欄位、公式語法、漏斗型指標走錯樣板、authorize 拒絕）全部在編譯期擋下。

## 之後

- 漏斗、留存、路徑、歸因四個樣板（docs/23 §4.3），各自的 oracle 與黃金查詢加進 golden.py。
- 合成 1 千萬筆資料 × 40 條固定查詢的效能基準（tools/synth），每週跑。
- `SemanticQuery` 契約擴充（多指標、巢狀條件、公式、top_n）：contract PR。
