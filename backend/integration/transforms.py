"""欄位轉換函式庫（純函式）。可視化建模的「④ 欄位映射」與「⑤ 清洗規則」在 Flink/Spark 端以同名函式實作。"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

PII_PATTERNS = {
    "tw_id": re.compile(r"\b[A-Z][12]\d{8}\b"),
    "mobile": re.compile(r"\b09\d{8}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
}


def trim(v: Any) -> Any: return v.strip() if isinstance(v, str) else v
def lower(v: Any) -> Any: return v.lower() if isinstance(v, str) else v
def upper(v: Any) -> Any: return v.upper() if isinstance(v, str) else v
def to_int(v: Any) -> int | None: return None if v in (None, "") else int(Decimal(str(v)))
def to_decimal(v: Any, places: int = 2) -> float | None:
    return None if v in (None, "") else float(Decimal(str(v)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
def map_values(v: Any, mapping: dict[str, Any], default: Any = None) -> Any: return mapping.get(v, default)


def to_ms_utc(v: Any, tz: str = "Asia/Taipei") -> int | None:
    """datetime / ISO 字串 / 秒 / 毫秒 / 微秒 → 毫秒 UTC。"""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        if n > 10**14: return n // 1000          # µs（GA4 event_timestamp）
        if n > 10**11: return n                  # ms
        return n * 1000                          # s
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if v.tzinfo is None:
        v = v.replace(tzinfo=ZoneInfo(tz))
    return int(v.astimezone(timezone.utc).timestamp() * 1000)


def hmac_sha256(v: Any, key: bytes) -> str | None:
    return None if v in (None, "") else hmac.new(key, str(v).encode(), hashlib.sha256).hexdigest()


def pii_scan(record: dict[str, Any]) -> list[str]:
    hits = []
    for k, v in record.items():
        if isinstance(v, str):
            for name, pat in PII_PATTERNS.items():
                if pat.search(v):
                    hits.append(f"{k}:{name}")
    return hits


def age_band(age: int | None) -> str | None:
    if age is None: return None
    for lo, hi, lbl in ((0, 24, "<25"), (25, 34, "25-34"), (35, 44, "35-44"), (45, 54, "45-54"), (55, 64, "55-64")):
        if lo <= age <= hi: return lbl
    return "65+"


if __name__ == "__main__":
    assert to_ms_utc(1760400000123456) == 1760400000123
    assert to_ms_utc("2026-10-14 13:58:00") == int(datetime(2026, 10, 14, 5, 58, tzinfo=timezone.utc).timestamp() * 1000)
    assert pii_scan({"memo": "請回電 0912345678"}) == ["memo:mobile"]
    assert age_band(37) == "35-44"
    print("transforms ok")
