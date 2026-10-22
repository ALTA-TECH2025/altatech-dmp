# backend/streaming — 即時路徑的 Python 端（docs/43）

| 檔案 | 用途 |
|---|---|
| `rules.py` | 逐列規則：直接呼叫 `backend/integration/modeling.py` 的 `apply()`、`to_ch_row()`、`quarantine_row()`，不另寫一份；Flink UDF 與批次共用 |
| `fixtures.py` | 合成測試列（21 列：正常、重送、隔離、個資樣式、L4 巢狀欄位）、固定時鐘、合成金鑰 |
| `parity.py` | 批流一致性測試：批次一次 `apply()` 與串流逐列結果必須相同；裝了 PyFlink 時另比 Table UDF（process／thread 模式）與 DataStream |
| `requirements-flink.txt` | PyFlink 2.3.0 完整釘選（含兩項刻意覆蓋的安全版本，見檔頭） |

## 本機跑

```bash
python3 -m backend.streaming.parity                      # 不需 PyFlink
python3.12 -m venv .venv-flink && .venv-flink/bin/pip install --no-deps -r backend/streaming/requirements-flink.txt
.venv-flink/bin/python -m backend.streaming.parity --require-flink   # 需要 JDK 17 或 21
```

CI：`backend` 工作跑純 Python 部分；`flink-parity` 工作裝 Temurin 21 與 PyFlink 跑完整比對。合成資料不含真實客戶資料，金鑰是測試用常數、不是 secret。
