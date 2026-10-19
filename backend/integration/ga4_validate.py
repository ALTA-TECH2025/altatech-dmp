"""GA4 匯出格式驗證（docs/31 §4.1-2）：拿 Google 公開的 GA4 BigQuery 匯出樣本跑一次我們的接入，確認
  1. schema：樣本表的欄位都認得（ga4_source.flatten 用到的欄位都在；多出來的欄位列出來決定要不要接）
  2. 轉名：GA4 事件名經舊平台橋接表轉回正式事件名的比例；轉不到的（ga. 前綴）列出來
  3. 身分：user_id／user_pseudo_id 的有值比例、同意欄位、one_id 落在登入或匿名
  4. T1 對帳：BigQuery 端每個 event_name 的筆數 == 載入 ClickHouse 後的筆數（event_id 去重後）
  5. 金額：電商事件的金額在 ecommerce 結構而不在 event_params.amount → 有沒有被接到

憑證：環境變數 DMP_GCP_SA_JSON（服務帳號金鑰 JSON 內容，只需 BigQuery Job User）與 DMP_GCP_PROJECT（計費專案）。
讀取走已安裝的 bq 命令列（不引入新套件）；金鑰寫到 0600 暫存檔給 gcloud 啟用後立刻刪除，不落在 repo 或日誌。
公開資料集 bigquery-public-data.ga4_obfuscated_sample_ecommerce 是 Google 商品店的混淆樣本（2020-11-01 ～ 2021-01-31），不是任何客戶資料。

用法：
  DMP_GCP_SA_JSON=… DMP_GCP_PROJECT=my-proj CLICKHOUSE_BIN=… python3 -m backend.integration.ga4_validate --day 20210131
  python3 -m backend.integration.ga4_validate --selftest      # 假 bq 輸出，不需要憑證
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .ga4_source import _bridge, flatten

DATASET = "bigquery-public-data.ga4_obfuscated_sample_ecommerce"
USED_FIELDS = {"event_date", "event_timestamp", "event_name", "event_params", "user_id", "user_pseudo_id", "platform", "stream_id", "event_bundle_sequence_id",
               "device", "geo", "app_info", "traffic_source", "privacy_info", "session_traffic_source_last_click"}
NUM_KEYS = {"int_value": int, "float_value": float, "double_value": float}

Runner = Callable[[list[str]], str]


def _run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} 失敗：{(r.stderr or r.stdout)[:400]}")
    return r.stdout


def activate(project: str | None, runner: Runner = _run) -> None:
    """DMP_GCP_SA_JSON → gcloud 啟用服務帳號；金鑰檔用完即刪。"""
    key = os.environ.get("DMP_GCP_SA_JSON")
    if not key:
        raise SystemExit("缺少 DMP_GCP_SA_JSON（服務帳號金鑰 JSON；在環境設定新增後開新 session）")
    fd, path = tempfile.mkstemp(suffix=".json"); os.chmod(path, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(key)
        runner(["gcloud", "auth", "activate-service-account", f"--key-file={path}", "--quiet"])
        if project:
            runner(["gcloud", "config", "set", "project", project, "--quiet"])
    finally:
        os.unlink(path)


def _norm(v: Any) -> Any:
    """bq --format=json 把數字輸出成字串；event_params 的值轉回型別，其餘巢狀結構遞迴處理。"""
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            out[k] = NUM_KEYS[k](x) if k in NUM_KEYS and x is not None and x != "" else _norm(x)
        return out
    return v


class BqCliClient:
    """BigQueryLike（ga4_source）的 bq 命令列版本：驗證與小量讀取用；正式管線用 BigQueryClient／EXPORT DATA。"""
    def __init__(self, project: str, runner: Runner = _run, max_rows: int = 200_000):
        self.project, self.run, self.max_rows = project, runner, max_rows

    def _q(self, sql: str) -> list[dict[str, Any]]:
        out = self.run(["bq", f"--project_id={self.project}", "query", "--nouse_legacy_sql", "--format=json", f"--max_rows={self.max_rows}", "--quiet", sql])
        return json.loads(out or "[]")

    def table_exists(self, table: str) -> bool:
        try:
            self.run(["bq", f"--project_id={self.project}", "show", "--format=json", table.replace(".", ":", 1)]); return True
        except RuntimeError:
            return False

    def schema(self, table: str) -> list[str]:
        out = self.run(["bq", f"--project_id={self.project}", "show", "--schema", "--format=json", table.replace(".", ":", 1)])
        return [f["name"] for f in json.loads(out)]

    def rows(self, table: str, *, min_ts_us: int | None = None) -> Iterable[dict[str, Any]]:
        sql = f"SELECT * FROM `{table}`" + (f" WHERE event_timestamp > {int(min_ts_us)}" if min_ts_us is not None else "")
        return [_norm(r) for r in self._q(sql)]

    def row_count(self, table: str) -> int:
        return int(self._q(f"SELECT COUNT(*) AS n FROM `{table}`")[0]["n"])

    def counts(self, table: str) -> dict[str, int]:
        return {r["event_name"]: int(r["n"]) for r in self._q(f"SELECT event_name, COUNT(*) AS n FROM `{table}` GROUP BY event_name")}


def validate(bq: Any, table: str, ch: Any | None = None) -> dict[str, Any]:
    cols = bq.schema(table)
    raw = list(bq.rows(table))
    bridge = _bridge()
    flat = [flatten(ev, bridge, "ga4_sample", "ga360_daily", "ga4_validate", 0) for ev in raw]
    names = Counter(ev["event_name"] for ev in raw)
    mapped = Counter(r["event"] for r in flat if not r["event"].startswith("ga."))
    unknown = Counter(r["props"]["$ga_event_name"] for r in flat if r["event"].startswith("ga."))
    with_uid = sum(1 for ev in raw if ev.get("user_id")); consent_no = sum(1 for r in flat if r["props"].get("consent.analytics") is False)
    ecommerce = sum(1 for ev in raw if (ev.get("ecommerce") or {}).get("purchase_revenue") not in (None, "", 0))
    amount = sum(1 for r in flat if r["amount"] is not None)
    res: dict[str, Any] = {
        "table": table, "rows": len(raw),
        "schema": {"missing": sorted(USED_FIELDS - set(cols)), "unused": sorted(set(cols) - USED_FIELDS)},
        "events": {"distinct": len(names), "mapped_rows": sum(mapped.values()), "unknown_rows": sum(unknown.values()),
                   "mapped_ratio": round(sum(mapped.values()) / max(len(flat), 1), 3), "unknown_top": unknown.most_common(15), "mapped_top": mapped.most_common(10)},
        "identity": {"user_id_ratio": round(with_uid / max(len(raw), 1), 3), "pseudo_ratio": round(sum(1 for ev in raw if ev.get("user_pseudo_id")) / max(len(raw), 1), 3),
                     "consent_denied": consent_no, "one_id_is_login": sum(1 for r in flat if r["one_id"] and r["one_id"] == (r["props"]["$identities"].get("$identity_login_id")))},
        "amount": {"rows_with_ecommerce_revenue": ecommerce, "rows_with_amount_mapped": amount},
        "dedupe": {"event_ids": len({r["event_id"] for r in flat}), "duplicates": len(flat) - len({r["event_id"] for r in flat})},
    }
    if ch is not None:
        ch.query("ALTER TABLE dmp.events DELETE WHERE project = 'ga4_sample'", settings={"mutations_sync": 2})
        for i in range(0, len(flat), 20_000):
            ch.insert_json("dmp.events", flat[i:i + 20_000])
        txt = ch.query("SELECT toString(props.`$ga_event_name`) AS e, count() AS n FROM dmp.events FINAL WHERE project = 'ga4_sample' GROUP BY e FORMAT JSONEachRow",
                       settings={"output_format_json_quote_64bit_integers": 0})
        got = {r["e"]: int(r["n"]) for r in (json.loads(x) for x in txt.splitlines() if x.strip())}
        src = bq.counts(table)
        diff = {k: (src.get(k, 0), got.get(k, 0)) for k in set(src) | set(got) if src.get(k, 0) != got.get(k, 0)}
        res["t1"] = {"bigquery_rows": sum(src.values()), "clickhouse_rows": sum(got.values()), "events_mismatch": diff, "ok": not diff}
    return res


def report_md(r: dict[str, Any]) -> str:
    e, i = r["events"], r["identity"]
    lines = [f"# GA4 匯出樣本驗證：{r['table']}", "", f"- 列數 {r['rows']:,}；事件種類 {e['distinct']}",
             f"- schema：缺少 {r['schema']['missing'] or '無'}；未使用欄位 {', '.join(r['schema']['unused']) or '無'}",
             f"- 轉名：{e['mapped_ratio']:.1%} 轉回正式事件名；未對應 {e['unknown_rows']:,} 列（前幾名：{', '.join(f'{k} {v:,}' for k, v in e['unknown_top'])}）",
             f"- 身分：user_id 有值 {i['user_id_ratio']:.1%}、user_pseudo_id 有值 {i['pseudo_ratio']:.1%}、拒絕同意 {i['consent_denied']:,} 列、歸到登入身分 {i['one_id_is_login']:,} 列",
             f"- 金額：ecommerce.purchase_revenue 有值 {r['amount']['rows_with_ecommerce_revenue']:,} 列；已接到 amount {r['amount']['rows_with_amount_mapped']:,} 列",
             f"- 去重鍵：{r['dedupe']['event_ids']:,} 個 event_id，重複 {r['dedupe']['duplicates']:,}"]
    if "t1" in r:
        t = r["t1"]; lines.append(f"- T1 對帳：BigQuery {t['bigquery_rows']:,} 列 vs ClickHouse {t['clickhouse_rows']:,} 列 → {'一致' if t['ok'] else '不一致：' + json.dumps(t['events_mismatch'], ensure_ascii=False)[:400]}")
    return "\n".join(lines) + "\n"


def selftest() -> None:
    """假 bq：輸出 bq --format=json 的形狀（數字是字串、巢狀 RECORD），驗證正規化、轉名統計、去重鍵與報告。"""
    rows = [{"event_date": "20210131", "event_timestamp": str(1612051200000000 + k * 1000), "event_name": n, "stream_id": "s", "event_bundle_sequence_id": str(k),
             "user_pseudo_id": f"p{k % 7}", "user_id": None, "platform": "WEB", "privacy_info": {"analytics_storage": None, "ads_storage": None},
             "event_params": [{"key": "ga_session_id", "value": {"string_value": None, "int_value": "1612051", "float_value": None, "double_value": None}},
                              {"key": "page_location", "value": {"string_value": "https://shop/x", "int_value": None, "float_value": None, "double_value": None}}],
             "ecommerce": {"purchase_revenue": "12.5" if n == "purchase" else None}, "device": {"category": "desktop"}, "geo": {"country": "US"}}
            for k, n in enumerate(["page_view"] * 6 + ["view_item"] * 3 + ["purchase"])]
    def fake(args: list[str]) -> str:
        if args[2] == "show":
            return json.dumps([{"name": c} for c in [*USED_FIELDS, "ecommerce", "items", "user_ltv"]])
        sql = args[-1]
        if "GROUP BY event_name" in sql:
            return json.dumps([{"event_name": k, "n": str(v)} for k, v in Counter(r["event_name"] for r in rows).items()])
        return json.dumps(rows)
    bq = BqCliClient("proj", runner=fake)
    r = validate(bq, f"{DATASET}.events_20210131")
    assert r["rows"] == 10 and r["schema"]["missing"] == [] and "ecommerce" in r["schema"]["unused"], r["schema"]
    assert r["events"]["mapped_rows"] >= 6 and r["amount"]["rows_with_ecommerce_revenue"] == 1 and r["dedupe"]["duplicates"] == 0, r
    assert r["identity"]["user_id_ratio"] == 0 and r["identity"]["pseudo_ratio"] == 1.0
    assert isinstance(_norm({"value": {"int_value": "5"}})["value"]["int_value"], int)
    assert "T1" not in report_md(r) and "轉名" in report_md(r)
    print("ga4_validate self-test ok:", report_md(r).splitlines()[3])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m backend.integration.ga4_validate")
    ap.add_argument("--day", default="20210131"); ap.add_argument("--selftest", action="store_true"); ap.add_argument("--out", default="ga4-validate")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest(); return 0
    project = os.environ.get("DMP_GCP_PROJECT")
    if not project:
        raise SystemExit("缺少 DMP_GCP_PROJECT（查詢計費專案）")
    activate(project)
    ch = None; proc = None
    if os.environ.get("CLICKHOUSE_BIN") or os.environ.get("CLICKHOUSE_URL"):
        from .e2e_clickhouse import ROOT, start_clickhouse
        from .runner import ClickHouseHTTP, local_ddl
        url = os.environ.get("CLICKHOUSE_URL")
        if os.environ.get("CLICKHOUSE_BIN"):
            proc = start_clickhouse(os.environ["CLICKHOUSE_BIN"], Path(tempfile.mkdtemp(prefix="dmp-ga4-"))); url = "http://127.0.0.1:18123"
        ch = ClickHouseHTTP(url or "", timeout=600)
        for st in local_ddl((ROOT / "clickhouse" / "schema.sql").read_text(encoding="utf-8")):
            with contextlib.suppress(Exception):             # 已存在／字典來源未設定
                ch.query(st, settings={"allow_experimental_json_type": 1})
    try:
        r = validate(BqCliClient(project), f"{DATASET}.events_{a.day}", ch)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / f"ga4_{a.day}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"ga4_{a.day}.md").write_text(report_md(r), encoding="utf-8")
        print(report_md(r))
        return 0 if r.get("t1", {}).get("ok", True) else 1
    finally:
        if proc:
            proc.terminate(); proc.wait(timeout=30)


if __name__ == "__main__":
    raise SystemExit(main())
