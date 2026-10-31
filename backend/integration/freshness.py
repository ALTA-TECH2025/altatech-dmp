"""資料來源新鮮度設定（規格 docs/superpowers/specs/2026-10-31-source-freshness.md；契約 SourceFreshnessUpdate／SourceFreshness）。

- validate：檢查 PUT 內容（與契約 schema 同規則），回正規化後的整筆設定；切換模式時另一模式的欄位一律為 null。
- judge：依設定判斷某來源此刻 ok／late／missing／unknown；未設定一律 unknown，不套任何預設門檻。
- arrived：依設定判斷某個台北日期的資料到了沒有；未設定回 None（不可當成到齊）。
時區固定 Asia/Taipei（台北沒有日光節約時間，固定 +08:00）。
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

PROJECT = "altatech_dmp"                        # 與分析編譯器、Gateway 的預設專案相同
KIND = "source_freshness"                       # store items 的 kind；id = "fresh:" + source，避免與其他 items 的 id 撞名
TZ_NAME = "Asia/Taipei"
TPE = timezone(timedelta(hours=8))
SOURCE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_ALLOWED = {"mode", "max_lag_minutes", "ready_at", "covers_days_back", "note"}
_BY_MODE = {"realtime": {"max_lag_minutes"}, "daily_batch": {"ready_at", "covers_days_back"}}


class Invalid(ValueError):
    """設定內容不合契約 → 400 INVALID_ARGUMENT。"""


def item_id(source: str) -> str:
    return "fresh:" + source


def _int(body: dict[str, Any], k: str, lo: int, hi: int) -> int:
    v = body[k]
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise Invalid(f"{k} 必須是 {lo}–{hi} 的整數")
    return v


def validate(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise Invalid("內容必須是物件")
    extra = sorted(set(body) - _ALLOWED)
    if extra:
        raise Invalid(f"不接受的欄位：{', '.join(extra)}（時區固定 {TZ_NAME}，不可指定）")
    mode = body.get("mode")
    if mode not in _BY_MODE:
        raise Invalid("mode 必須是 realtime 或 daily_batch")
    need, other = _BY_MODE[mode], set().union(*(v for k, v in _BY_MODE.items() if k != mode))
    missing, wrong = sorted(need - set(body)), sorted(other & set(body))
    if missing or wrong:
        raise Invalid(f"{mode} " + "；".join(([f"缺少 {', '.join(missing)}"] if missing else []) + ([f"不可帶 {', '.join(wrong)}"] if wrong else [])))
    note = body.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        raise Invalid("note 必須是 200 字以內的字串")
    out: dict[str, Any] = {"mode": mode, "max_lag_minutes": None, "ready_at": None, "covers_days_back": None, "note": note}
    if mode == "realtime":
        out["max_lag_minutes"] = _int(body, "max_lag_minutes", 1, 1440)
    else:
        if not isinstance(body["ready_at"], str) or not _HHMM.match(body["ready_at"]):
            raise Invalid("ready_at 必須是 HH:MM（00:00–23:59）")
        out["ready_at"], out["covers_days_back"] = body["ready_at"], _int(body, "covers_days_back", 1, 7)
    return out


def expected_through(cfg: dict[str, Any], now: datetime) -> date:
    """日批：此刻應涵蓋到的台北日期。到齊時間之前 T-(n+1)，之後（含當分鐘）T-n。"""
    local = now.astimezone(TPE)
    h, m = (int(x) for x in cfg["ready_at"].split(":"))
    return local.date() - timedelta(days=int(cfg["covers_days_back"]) + (1 if local.time() < time(h, m) else 0))


def judge(cfg: dict[str, Any] | None, last: datetime | None, period_start: datetime, now: datetime) -> dict[str, Any]:
    """回 {mode, expected_lag_minutes, expected_through, status}（契約 Readiness.sources[] 的欄位）。"""
    if not cfg:
        return {"mode": None, "expected_lag_minutes": None, "expected_through": None, "status": "unknown"}
    out: dict[str, Any] = {"mode": cfg["mode"], "expected_lag_minutes": None, "expected_through": None}
    if cfg["mode"] == "realtime":
        out["expected_lag_minutes"] = float(cfg["max_lag_minutes"])
        ok = last is not None and (now - last).total_seconds() / 60 <= cfg["max_lag_minutes"]
    else:
        thr = expected_through(cfg, now); out["expected_through"] = thr.isoformat()
        ok = last is not None and last.astimezone(TPE).date() >= thr
    out["status"] = "missing" if last is None or last < period_start else "ok" if ok else "late"
    return out


def arrived(cfg: dict[str, Any] | None, last: datetime | None, day: date) -> bool | None:
    """某台北日期的資料到了沒有。即時：最後事件 ≥ 該日台北 24:00；日批：最後事件的台北日期 ≥ 該日（假設一次進一整天，規格 §8-6）。"""
    if not cfg:
        return None
    if last is None:
        return False
    if cfg["mode"] == "realtime":
        return last >= datetime(day.year, day.month, day.day, tzinfo=TPE) + timedelta(days=1)
    return last.astimezone(TPE).date() >= day


def view(source: str, cfg: dict[str, Any] | None, last: datetime | None) -> dict[str, Any]:
    """GET 清單的一列（契約 SourceFreshness）。"""
    c = cfg or {}
    return {"source": source, "configured": bool(cfg), "mode": c.get("mode"), "max_lag_minutes": c.get("max_lag_minutes"), "ready_at": c.get("ready_at"),
            "covers_days_back": c.get("covers_days_back"), "timezone": TZ_NAME, "note": c.get("note"),
            "last_event_time": last.astimezone(UTC).isoformat().replace("+00:00", "Z") if last else None}


def parse_ch_time(v: Any) -> datetime | None:
    """ClickHouse 回的 UTC 時間字串（'2026-10-15 09:00:00.000'）→ aware datetime；空值或 1970 → None。"""
    if not v:
        return None
    t = datetime.fromisoformat(str(v).replace(" ", "T").replace("Z", "+00:00"))
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    return None if t.year <= 1970 else t


def selftest() -> None:
    ok = lambda b: validate(b)
    def bad(b: Any) -> None:
        try:
            validate(b)
        except Invalid:
            return
        raise AssertionError(f"應該被拒：{b}")
    assert ok({"mode": "realtime", "max_lag_minutes": 15, "note": "x"}) == {"mode": "realtime", "max_lag_minutes": 15, "ready_at": None, "covers_days_back": None, "note": "x"}
    assert ok({"mode": "daily_batch", "ready_at": "07:00", "covers_days_back": 1})["max_lag_minutes"] is None
    for b in ([], {"mode": "hourly"}, {"mode": "realtime"}, {"mode": "realtime", "max_lag_minutes": 0}, {"mode": "realtime", "max_lag_minutes": 1441},
              {"mode": "realtime", "max_lag_minutes": True}, {"mode": "realtime", "max_lag_minutes": 15.0}, {"mode": "realtime", "max_lag_minutes": 15, "ready_at": "07:00"},
              {"mode": "daily_batch", "ready_at": "07:00"}, {"mode": "daily_batch", "ready_at": "24:00", "covers_days_back": 1},
              {"mode": "daily_batch", "ready_at": "7:00", "covers_days_back": 1}, {"mode": "daily_batch", "ready_at": "07:00", "covers_days_back": 8},
              {"mode": "daily_batch", "ready_at": "07:00", "covers_days_back": 1, "max_lag_minutes": 15}, {"mode": "realtime", "max_lag_minutes": 15, "timezone": "UTC"},
              {"mode": "realtime", "max_lag_minutes": 15, "note": "x" * 201}):
        bad(b)
    utc = lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)
    p0 = datetime(2026, 10, 26, tzinfo=TPE)                                    # 期間開始：台北 10/26 00:00
    rt = {"mode": "realtime", "max_lag_minutes": 15}
    now = utc("2026-10-28T02:00:00")                                           # 台北 10/28 10:00
    assert judge(None, utc("2026-10-28T01:59:00"), p0, now) == {"mode": None, "expected_lag_minutes": None, "expected_through": None, "status": "unknown"}
    assert judge(rt, utc("2026-10-28T01:45:00"), p0, now)["status"] == "ok" and judge(rt, utc("2026-10-28T01:44:59"), p0, now)["status"] == "late"
    assert judge(rt, utc("2026-10-20T00:00:00"), p0, now)["status"] == "missing" and judge(rt, None, p0, now)["status"] == "missing"
    db = {"mode": "daily_batch", "ready_at": "07:00", "covers_days_back": 1}
    at = lambda hhmm: datetime.fromisoformat(f"2026-10-28T{hhmm}:00").replace(tzinfo=TPE)
    assert expected_through(db, at("06:59")) == date(2026, 10, 26) and expected_through(db, at("07:00")) == date(2026, 10, 27)   # 06:59／07:00 邊界
    last_26 = datetime(2026, 10, 26, 23, 59, tzinfo=TPE)
    assert judge(db, last_26, p0, at("06:59"))["status"] == "ok" and judge(db, last_26, p0, at("07:00")) == {"mode": "daily_batch", "expected_lag_minutes": None, "expected_through": "2026-10-27", "status": "late"}
    assert expected_through(db, datetime(2026, 10, 27, 16, 30, tzinfo=UTC)) == date(2026, 10, 26)     # 跨午夜：UTC 10/27 16:30 = 台北 10/28 00:30（到齊時間前）→ T-2
    assert expected_through({**db, "ready_at": "00:00"}, datetime(2026, 10, 27, 16, 0, tzinfo=UTC)) == date(2026, 10, 27)   # 00:00 到齊：台北零點起就是 T-1
    d27 = date(2026, 10, 27)
    assert arrived(None, last_26, d27) is None and arrived(db, None, d27) is False
    assert arrived(db, datetime(2026, 10, 27, 0, 1, tzinfo=TPE), d27) is True and arrived(db, last_26, d27) is False
    assert arrived(rt, datetime(2026, 10, 27, 23, 59, tzinfo=TPE), d27) is False and arrived(rt, datetime(2026, 10, 28, 0, 0, tzinfo=TPE), d27) is True
    assert view("ga360_daily", None, None) == {"source": "ga360_daily", "configured": False, "mode": None, "max_lag_minutes": None, "ready_at": None, "covers_days_back": None,
                                              "timezone": TZ_NAME, "note": None, "last_event_time": None}
    assert view("x", db, parse_ch_time("2026-10-15 09:00:00.000"))["last_event_time"] == "2026-10-15T09:00:00Z" and parse_ch_time("1970-01-01 00:00:00") is None
    assert SOURCE_RE.match("mdl_open_acct") and not SOURCE_RE.match("GA360") and not SOURCE_RE.match("a" * 65)
    print("source freshness self-test ok: 2 正例＋15 負例、未設定 unknown、即時 ok/late 邊界、missing、日批 06:59／07:00 邊界、跨午夜、到齊判斷、清單列")


if __name__ == "__main__":
    selftest()
