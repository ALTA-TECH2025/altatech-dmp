"""GA360（GA4 360）BigQuery 匯出 → DMP 事件表：三種匯出表、三種頻率、一套去重（docs/22 §2、semantic/ga4_mapping.yaml）。

GA4 匯出到 BigQuery 的表與更新頻率（GA 官方口徑，正式環境以實際到表時間校正）：
  events_intraday_YYYYMMDD  串流匯出：事件發生後數分鐘內持續追加；當天表只有當天，隔天日批表到齊後 GA 會刪掉它。
                            沒有「回補」：遲到事件不會補進串流表；部分歸因欄位在串流表可能缺。
  events_YYYYMMDD           日批匯出：前一天的完整資料，通常在隔日數小時內到齊（GA 只保證 72 小時內），
                            且 GA 可能在 72 小時內因遲到事件「重寫」同一張日表。
  events_fresh_YYYYMMDD     Fresh daily（360 限定）：當天的日表一天內多次更新，兼具日批欄位完整與小時級新鮮度；有開就用它取代串流表。
  users_／pseudonymous_users_  用戶層每日匯出（用戶屬性、受眾），本系統用戶表另有來源，先不接。

本模組的排程設計（三個任務，同一張 dmp.events，靠 event_id 去重、ingest_time 為版本，後來的覆蓋先來的）：
  ga360_intraday  每 10–15 分鐘：讀 events_intraday_{今天}，event_timestamp > 水位 − 30 分鐘（重疊窗口吃掉串流表的亂序），
                  重讀的事件 event_id 相同 → ReplacingMergeTree 合併時去重；水位 = 本批最大 event_timestamp。
  ga360_daily     每日：輪詢 events_{昨天} 是否到齊（到齊才跑，不到齊記 skipped），整天重載；ingest_time 較新 → 覆蓋串流版本。
  ga360_restate   每日：對 D-2、D-3 的日表比對列數與 ingest 對帳表，數字變了就整天重載（GA 的 72 小時重寫）。
  若 360 有 Fresh daily：ga360_intraday 改讀 events_fresh_{今天}（每小時），其餘不變。

BigQuery 讀取以注入的 client 進行（正式：google-cloud-bigquery Storage Read API；大表走 EXPORT DATA → GCS Parquet → ClickHouse s3()）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAP_MINUTES = 30
RESTATE_DAYS = (2, 3)           # GA 72 小時內可能重寫的日表


class BigQueryLike(Protocol):
    def table_exists(self, table: str) -> bool: ...
    def rows(self, table: str, *, min_ts_us: int | None = None) -> Iterable[dict[str, Any]]: ...
    def row_count(self, table: str) -> int: ...


def _bridge() -> dict[str, str]:
    p = ROOT / "semantic" / "sensors_ga4_bridge.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")).get("ga4_to_sensors", {}) if p.exists() else {}


def _params(ev: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for p in ev.get("event_params") or []:
        v = p.get("value") or {}
        val = next((v[k] for k in ("string_value", "int_value", "float_value", "double_value") if v.get(k) is not None), None)
        out[p["key"]] = val
    return out


PARAM_MAP = {"ga_session_id": "$session_id", "ga_session_number": "$session_number", "page_location": "$page_url", "page_referrer": "$referrer",
             "page_title": "$title", "engagement_time_msec": "$engagement_ms", "firebase_screen": "$screen_name"}


def flatten(ev: dict[str, Any], bridge: dict[str, str], project: str, source: str, batch_id: str, ingest_ms: int) -> dict[str, Any]:
    """一筆 GA4 匯出列 → dmp.events 的 JSONEachRow 列（ga4_mapping.yaml 的規則）。"""
    params = _params(ev)
    name = ev["event_name"]; event = bridge.get(name, "ga." + name)
    ts_ms = int(ev["event_timestamp"]) // 1000
    pseudo = ev.get("user_pseudo_id") or ""; uid = ev.get("user_id") or ""
    platform = (ev.get("platform") or "").upper()
    anon_key = "$identity_cookie_id" if platform == "WEB" else "$identity_device_id"
    ids = {anon_key: pseudo}
    if uid:
        ids["$identity_login_id"] = uid           # 已是 v1_ 客戶代碼（docs/20）
    consent_ok = ((ev.get("privacy_info") or {}).get("analytics_storage") or "Yes") == "Yes"
    one_id = uid if (uid and consent_ok) else pseudo
    dev, geo, app, ts_ = ev.get("device") or {}, ev.get("geo") or {}, ev.get("app_info") or {}, ev.get("traffic_source") or {}
    last = ((ev.get("session_traffic_source_last_click") or {}).get("manual_campaign") or {})
    props = {PARAM_MAP.get(k, k): v for k, v in params.items() if v is not None}
    props.update({"$identities": ids, "$device_type": dev.get("category"), "$os": dev.get("operating_system"), "$os_version": dev.get("operating_system_version"),
                  "$browser": (dev.get("web_info") or {}).get("browser"), "$geo_country": geo.get("country"), "$geo_region": geo.get("region"), "$geo_city": geo.get("city"),
                  "$app_version": app.get("version"), "$first_utm_source": ts_.get("source"), "$first_utm_medium": ts_.get("medium"), "$first_utm_campaign": ts_.get("name"),
                  "$utm_medium": last.get("medium"), "consent.analytics": consent_ok, "$ga_event_name": name})
    props = {k: v for k, v in props.items() if v is not None}
    event_id = hashlib.sha256("|".join(str(ev.get(k, "")) for k in ("stream_id", "user_pseudo_id", "event_timestamp", "event_name", "event_bundle_sequence_id")).encode()).hexdigest()[:32]
    from .modeling import ms_to_dt
    return {"project": project, "time": ms_to_dt(ts_ms), "event": event, "source": source, "one_id": one_id, "entity_type": "", "entity_id": "",
            "distinct_id": pseudo, "session_id": str(params.get("ga_session_id") or ""), "event_id": event_id, "platform": platform,
            "app_id": app.get("id") or "", "symbol": str(params.get("symbol") or ""), "side": str(params.get("side") or ""), "channel": "",
            "amount": params.get("amount") if isinstance(params.get("amount"), (int, float)) else None,
            "utm_source": last.get("source") or "", "utm_campaign": last.get("campaign_name") or "", "props": props,
            "ingest_time": ms_to_dt(ingest_ms), "schema_version": 1, "late": 0, "batch_id": batch_id}


@dataclass
class Ga4Batch:
    rows: list[dict[str, Any]]
    watermark_to: str | None
    unknown_events: dict[str, int]
    table: str
    skipped_reason: str | None = None


def read(client: BigQueryLike, src: dict[str, Any], *, watermark: str | None, now: datetime, batch_id: str, project: str = "altatech_dmp") -> Ga4Batch:
    """src: {kind: ga4, dataset: 'analytics_123', table_kind: intraday|fresh|daily|restate, tz: 'Asia/Taipei', day_offset?: int}"""
    tz_off = int(src.get("tz_offset_hours", 8)); local = now + timedelta(hours=tz_off)
    kind = src["table_kind"]; ds = src["dataset"]; bridge = _bridge(); ingest_ms = int(now.timestamp() * 1000)
    if kind in ("intraday", "fresh"):
        day = local.strftime("%Y%m%d"); table = f"{ds}.events_{'intraday' if kind == 'intraday' else 'fresh'}_{day}"
        if not client.table_exists(table):
            return Ga4Batch([], watermark, {}, table, f"{table} 尚未建立（當天還沒有事件或匯出未開）")
        min_us = (int(watermark) - OVERLAP_MINUTES * 60_000_000) if watermark else None
        source = "ga360_intraday" if kind == "intraday" else "ga360_fresh"
        raw = list(client.rows(table, min_ts_us=min_us))
        wm = max((int(r["event_timestamp"]) for r in raw), default=int(watermark) if watermark else None)
    else:
        offset = int(src.get("day_offset", 1))
        day = (local - timedelta(days=offset)).strftime("%Y%m%d"); table = f"{ds}.events_{day}"
        if not client.table_exists(table):
            return Ga4Batch([], watermark, {}, table, f"{table} 尚未到齊（GA 日批最晚 72 小時）")
        if kind == "restate":
            cnt = client.row_count(table)
            if watermark is not None and str(cnt) == watermark:
                return Ga4Batch([], watermark, {}, table, f"{table} 列數 {cnt} 未變，不重載")
            raw = list(client.rows(table)); wm = cnt
        else:
            raw = list(client.rows(table)); wm = int(client.row_count(table))
        source = "ga360_daily"
    rows, unknown = [], {}
    for ev in raw:
        r = flatten(ev, bridge, project, source, batch_id, ingest_ms); rows.append(r)
        if r["event"].startswith("ga."):
            unknown[r["event"]] = unknown.get(r["event"], 0) + 1
    return Ga4Batch(rows, str(wm) if wm is not None else None, unknown, table)


class BigQueryClient:
    """正式環境的 BigQueryLike：google-cloud-bigquery（Apache-2.0；docs/08 已列）。憑證走 Workload Identity／服務帳號金鑰的 secret_ref。
    大表（日批 2,000 萬列）建議改走 EXPORT DATA OPTIONS(uri='gs://…/events_YYYYMMDD_*.parquet') → ClickHouse s3()，本類別的 rows() 用在串流表與小批。"""
    def __init__(self, project: str, location: str = "asia-east1", page_size: int = 50_000):
        from google.cloud import bigquery   # 延遲載入：本機測試不需要這個套件
        self.bq = bigquery; self.client = bigquery.Client(project=project, location=location); self.page_size = page_size

    def table_exists(self, table: str) -> bool:
        try:
            self.client.get_table(table); return True
        except Exception as e:   # noqa: BLE001 — NotFound 與權限錯誤都視為「不可用」
            if type(e).__name__ == "NotFound": return False
            raise

    def rows(self, table: str, *, min_ts_us: int | None = None):
        sql = f"SELECT * FROM `{table}`" + (f" WHERE event_timestamp > {int(min_ts_us)}" if min_ts_us is not None else "")
        job = self.client.query(sql)
        for row in job.result(page_size=self.page_size):
            yield dict(row.items())

    def row_count(self, table: str) -> int:
        return int(self.client.get_table(table).num_rows)


# ---------------------------------------------------------------- 自測（假 BigQuery）
class FakeBigQuery:
    """記憶體版 GA4 匯出：tables[name] = list[row]；row 為 GA4 匯出 schema 的 dict。"""
    def __init__(self): self.tables: dict[str, list[dict[str, Any]]] = {}
    def table_exists(self, table: str) -> bool: return table in self.tables
    def rows(self, table: str, *, min_ts_us: int | None = None):
        for r in self.tables[table]:
            if min_ts_us is None or int(r["event_timestamp"]) > min_ts_us:
                yield r
    def row_count(self, table: str) -> int: return len(self.tables[table])


def ga4_row(i: int, ts_us: int, name: str = "page_view", uid: str | None = None, pseudo: str = "p1", platform: str = "WEB", seq: int = 1, stream: str = "s1") -> dict[str, Any]:
    return {"event_date": "20261016", "event_timestamp": ts_us, "event_name": name, "stream_id": stream, "event_bundle_sequence_id": seq,
            "user_pseudo_id": pseudo, "user_id": uid, "platform": platform,
            "event_params": [{"key": "ga_session_id", "value": {"int_value": 1700000000}}, {"key": "page_location", "value": {"string_value": "https://sbp/x"}},
                             {"key": "symbol", "value": {"string_value": "2330"}}, {"key": "amount", "value": {"double_value": 1000.5}}],
            "device": {"category": "desktop", "operating_system": "Windows", "web_info": {"browser": "Chrome"}}, "geo": {"country": "Taiwan", "city": "Taipei"},
            "app_info": {}, "traffic_source": {"source": "google", "medium": "cpc", "name": "open"}, "privacy_info": {"analytics_storage": "Yes"},
            "session_traffic_source_last_click": {"manual_campaign": {"source": "line", "medium": "social", "campaign_name": "oct"}}}


if __name__ == "__main__":
    bq = FakeBigQuery(); now = datetime(2026, 10, 16, 1, 15, tzinfo=timezone.utc)   # 台北 09:15
    base = int(datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
    bq.tables["ds.events_intraday_20261016"] = [ga4_row(i, base + i * 60_000_000, "kh_apply" if i % 3 else "page_view", uid="v1_abc" if i % 2 else None) for i in range(60)]
    b1 = read(bq, {"kind": "ga4", "dataset": "ds", "table_kind": "intraday"}, watermark=None, now=now, batch_id="b1")
    assert len(b1.rows) == 60 and b1.unknown_events == {} and b1.rows[1]["one_id"] == "v1_abc" and b1.rows[0]["one_id"] == "p1"
    assert b1.rows[0]["event"] == "$pageview" and b1.rows[1]["event"] == "kh_apply" and b1.rows[0]["props"]["$page_url"] == "https://sbp/x" and b1.rows[0]["amount"] == 1000.5
    assert b1.rows[0]["utm_source"] == "line" and b1.rows[0]["props"]["$first_utm_source"] == "google" and b1.rows[0]["props"]["$identities"]["$identity_cookie_id"] == "p1"
    # 第二批：水位 − 30 分鐘重疊 → 重讀最後 30 筆（每分鐘一筆）+ 新 10 筆；event_id 相同的重讀列由 ClickHouse 去重
    bq.tables["ds.events_intraday_20261016"] += [ga4_row(i, base + i * 60_000_000, "unknown_evt") for i in range(60, 70)]
    b2 = read(bq, {"kind": "ga4", "dataset": "ds", "table_kind": "intraday"}, watermark=b1.watermark_to, now=now + timedelta(minutes=15), batch_id="b2")
    assert len(b2.rows) == 30 + 10 and b2.unknown_events == {"ga.unknown_evt": 10}, (len(b2.rows), b2.unknown_events)
    assert {r["event_id"] for r in b1.rows} & {r["event_id"] for r in b2.rows} and len({r["event_id"] for r in b1.rows + b2.rows}) == 70
    # 日批未到 → skipped；到了 → 整天重載
    d = read(bq, {"kind": "ga4", "dataset": "ds", "table_kind": "daily"}, watermark=None, now=now + timedelta(days=1), batch_id="d")
    assert d.skipped_reason and d.rows == []
    bq.tables["ds.events_20261016"] = bq.tables["ds.events_intraday_20261016"] + [ga4_row(99, base + 99 * 60_000_000, "kh_apply")]
    d = read(bq, {"kind": "ga4", "dataset": "ds", "table_kind": "daily"}, watermark=None, now=now + timedelta(days=1), batch_id="d")
    assert len(d.rows) == 71 and d.rows[0]["source"] == "ga360_daily" and d.watermark_to == "71"
    # 重寫偵測：列數沒變不重載，變了重載
    r = read(bq, {"kind": "ga4", "dataset": "ds", "table_kind": "restate", "day_offset": 2}, watermark="71", now=now + timedelta(days=2), batch_id="r"); assert r.skipped_reason
    bq.tables["ds.events_20261016"].append(ga4_row(100, base + 100 * 60_000_000, "kh_apply"))
    r = read(bq, {"kind": "ga4", "dataset": "ds", "table_kind": "restate", "day_offset": 2}, watermark="71", now=now + timedelta(days=2), batch_id="r"); assert len(r.rows) == 72 and r.watermark_to == "72"
    # 拒絕同意 → 不歸戶到客戶代碼
    ev = ga4_row(1, base, uid="v1_abc"); ev["privacy_info"] = {"analytics_storage": "No"}
    assert flatten(ev, {}, "p", "s", "b", 0)["one_id"] == "p1"
    print("ga4_source ok")
