"""欄位轉換函式庫（純函式）。可視化建模的「④ 欄位映射」與「⑤ 清洗規則」在 Flink/Spark 端以同名函式實作。"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime
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
    return int(v.astimezone(UTC).timestamp() * 1000)


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


def pii_scan_deep(value: Any, path: str = "", forbidden_keys: set[str] | None = None, _depth: int = 0) -> list[str]:
    """遞迴版：物件與陣列裡任何一層的字串值像個資（身分證／手機／email）→ 'pii:<路徑>:<型別>'；
    鍵名（去 $ 前綴）在 forbidden_keys（user_properties.yaml 的 forbidden_l4）→ 'l4:<路徑>'。路徑用 . 串，陣列用 [i]。"""
    hits: list[str] = []
    if _depth > 16: return hits
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else str(k)
            if forbidden_keys and str(k).lstrip("$") in forbidden_keys:
                hits.append(f"l4:{p}"); continue
            hits += pii_scan_deep(v, p, forbidden_keys, _depth + 1)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hits += pii_scan_deep(v, f"{path}[{i}]", forbidden_keys, _depth + 1)
    elif isinstance(value, str):
        for name, pat in PII_PATTERNS.items():
            if pat.search(value):
                hits.append(f"pii:{path}:{name}")
    return hits


def age_band(age: int | None) -> str | None:
    if age is None: return None
    for lo, hi, lbl in ((0, 24, "<25"), (25, 34, "25-34"), (35, 44, "35-44"), (45, 54, "45-54"), (55, 64, "55-64")):
        if lo <= age <= hi: return lbl
    return "65+"


if __name__ == "__main__":
    assert to_ms_utc(1760400000123456) == 1760400000123
    assert to_ms_utc("2026-10-14 13:58:00") == int(datetime(2026, 10, 14, 5, 58, tzinfo=UTC).timestamp() * 1000)
    assert pii_scan({"memo": "請回電 0912345678"}) == ["memo:mobile"]
    deep = pii_scan_deep({"props": {"$identities": {"$identity_login_id": "v1_abc"}, "items": [{"note": "a@b.co"}], "email": "x"}, "memo": "0912345678"}, forbidden_keys={"email"})
    assert deep == ["pii:props.items[0].note:email", "l4:props.email", "pii:memo:mobile"], deep
    assert pii_scan_deep({"props": {"market": "TSE", "n": 1, "ok": None}}) == []
    assert age_band(37) == "35-44"
    print("transforms ok")
