"""A20 敘述數字可追溯：敘述裡的每個數字都要能在某個證據的結果裡找到（容許四捨五入；百分比對比率）。
規則模式不產生敘述；模型替身與之後的正式模型產生的結論文字都要過這一關，找不到出處的數字列出來，不放行。"""
from __future__ import annotations

import re
from typing import Any

_DATES = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}")          # 日期、期間不是結果數字
_UNIT = {"十": 10, "百": 100, "千": 1e3, "萬": 1e4, "万": 1e4, "億": 1e8, "亿": 1e8, "兆": 1e12}
# 數字後面緊接的量級字（可連用，如「百萬」「千億」）一起擷取；緊接英文字母（3k、1.2M、10ms）不解析，整個當成無法追溯
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[十百千萬万億亿兆]+|[A-Za-z]+)?%?")


def numbers_in(text: str) -> list[str]:
    return _NUM.findall(_DATES.sub(" ", text))


def _parse(raw: str) -> tuple[float, float] | None:
    """回傳 (數值, 容許誤差)；量級字換算後，誤差照敘述寫出的位數四捨五入再乘上量級。不支援的格式回 None。"""
    m = re.fullmatch(r"(-?[\d,]+(?:\.\d+)?)([十百千萬万億亿兆]*)([A-Za-z]*)%?", raw)
    if not m or m.group(3):
        return None
    s, mult = m.group(1).replace(",", ""), 1.0
    for ch in m.group(2):
        mult *= _UNIT[ch]
    dec = len(s.split(".")[1]) if "." in s else 0
    return float(s) * mult, (0.5 * 10 ** -dec) * mult + 1e-9


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
        p = _parse(raw)
        if p is None:                                                            # 看不懂的格式不放行
            bad.append(raw)
            continue
        x, tol = p
        cands = [(x, tol), (x / 100, tol / 100)] if raw.endswith("%") else [(x, tol)]   # 百分比可能對到比率（0.375）或百分數（37.5）
        if not any(abs(c - v) <= t for c, t in cands for v in vals):
            bad.append(raw)
    return bad
