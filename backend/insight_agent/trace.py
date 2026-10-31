"""A20 敘述數字可追溯：敘述裡的每個數字都要能在某個證據的結果裡找到（容許四捨五入；百分比對比率）。
規則模式不產生敘述；模型替身與之後的正式模型產生的結論文字都要過這一關，找不到出處的數字列出來，不放行。"""
from __future__ import annotations

import re
from typing import Any

_DATES = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}")          # 日期、期間不是結果數字
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")


def numbers_in(text: str) -> list[str]:
    return _NUM.findall(_DATES.sub(" ", text))


def _values(o: Any) -> list[float]:
    if isinstance(o, bool):
        return []
    if isinstance(o, int | float):
        return [float(o)]
    if isinstance(o, dict):
        return [v for x in o.values() for v in _values(x)]
    if isinstance(o, list):
        return [v for x in o for v in _values(x)]
    return []


def untraced(text: str, evidence_data: list[Any]) -> list[str]:
    """回傳找不到出處的數字（原字串）；空清單 = 全部可追溯。"""
    vals = [v for d in evidence_data for v in _values(d)]
    bad = []
    for raw in numbers_in(text):
        s = raw.rstrip("%").replace(",", "")
        x, dec = float(s), len(s.split(".")[1]) if "." in s else 0
        tol = 0.5 * 10 ** -dec + 1e-9                                            # 照敘述寫出的位數四捨五入
        cands = [(x, tol), (x / 100, tol / 100)] if raw.endswith("%") else [(x, tol)]   # 百分比可能對到比率（0.375）或百分數（37.5）
        if not any(abs(c - v) <= t for c, t in cands for v in vals):
            bad.append(raw)
    return bad
