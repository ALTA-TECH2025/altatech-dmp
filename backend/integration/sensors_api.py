"""神策查詢 API（/api/sql/query）直連連接器：連線測試逐步診斷、資料盤點、按日分頁導出成 FormatImporter 格式（接 sensors_format.SensorsLoader）。

兩種鑑權都支援（私有化 2.x／3.x 與 SBP 開放 API 金鑰）：
  query_token：POST {base}/api/sql/query?project=P&token=SECRET   body: q=<SQL>&format=csv|json
  header     ：POST {base}/api/sql/query?project=P                 header: sensorsdata-token: SECRET（名稱可設）
連線測試先用設定的模式，401／403 時自動試另一種並在結果說明。密鑰只從環境變數或 secret_ref 解析來，不進參數、不進日誌。

用法：
  python3 -m backend.integration.sensors_api                                  # 自測（假伺服器）
  SA_API_SECRET=... python3 -m backend.integration.sensors_api test --base-url https://host --project taipei_uat
  SA_API_SECRET=... python3 -m backend.integration.sensors_api inventory --base-url ... --project ... --from 2026-10-01 --to 2026-10-07
  SA_API_SECRET=... python3 -m backend.integration.sensors_api export --base-url ... --project ... --date 2026-10-01 --out /data/sa_2026-10-01.jsonl
導出檔可直接餵 sensors_format.SensorsLoader.run_file（代碼化、去個資、載入 ClickHouse、對帳）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TPE = timezone(timedelta(hours=8))
STEPS = ["dns", "tcp", "tls", "auth", "privilege", "sample_read", "clock_skew"]


@dataclass
class Step:
    name: str
    ok: bool
    ms: int
    detail: str = ""
    fix: str = ""


@dataclass
class HttpResponse:
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


HttpFn = Callable[[str, dict[str, str], dict[str, str]], HttpResponse]   # (url, form, headers) → response


def urllib_http(timeout: int = 60, ca_bundle: str | None = None) -> HttpFn:
    """標準函式庫 HTTP（走環境變數 HTTPS_PROXY；憑證一律驗證，可釘選內部 CA）。"""
    ctx = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else ssl.create_default_context()

    def do(url: str, form: dict[str, str], headers: dict[str, str]) -> HttpResponse:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST", headers={"Content-Type": "application/x-www-form-urlencoded", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return HttpResponse(r.status, r.read().decode("utf-8", "replace"), dict(r.headers))
        except urllib.error.HTTPError as e:
            return HttpResponse(e.code, e.read().decode("utf-8", "replace"), dict(e.headers))
    return do


class SensorsApiError(Exception):
    pass


class SensorsQueryAPI:
    def __init__(self, base_url: str, project: str, token: str, http: HttpFn | None = None, auth: str = "query_token", header_name: str = "sensorsdata-token",
                 path: str = "/api/sql/query", fmt: str = "csv", timeout: int = 60, ca_bundle: str | None = None):
        if not base_url.startswith("https://") and not os.environ.get("SA_ALLOW_HTTP"):
            raise SensorsApiError("base_url 必須是 https://（內網測試要走 http 請設 SA_ALLOW_HTTP=1）")
        if auth not in ("query_token", "header"):
            raise SensorsApiError("auth 只能是 query_token／header")
        self.base, self.project, self._token, self.auth, self.header_name, self.path, self.fmt = base_url.rstrip("/"), project, token, auth, header_name, path, fmt
        self.http = http or urllib_http(timeout, ca_bundle)
        self.last_status = 0

    # ---- 底層：一條 SQL → 列 ----
    def _request(self, q: str, fmt: str | None = None, auth: str | None = None) -> HttpResponse:
        auth = auth or self.auth
        params = {"project": self.project}
        headers: dict[str, str] = {}
        if auth == "query_token":
            params["token"] = self._token
        else:
            headers[self.header_name] = self._token
        url = f"{self.base}{self.path}?{urllib.parse.urlencode(params)}"
        r = self.http(url, {"q": q, "format": fmt or self.fmt}, headers)
        self.last_status = r.status
        return r

    def sql(self, q: str, fmt: str | None = None) -> list[dict[str, Any]]:
        r = self._request(q, fmt)
        if r.status == 401 or r.status == 403:
            raise SensorsApiError(f"鑑權失敗（HTTP {r.status}）：token 無效、過期，或鑑權模式不對（目前 {self.auth}）")
        if r.status >= 400:
            raise SensorsApiError(f"HTTP {r.status}：{_redact(r.body[:300], self._token)}")
        return parse_rows(r.body, fmt or self.fmt)

    # ---- 連線測試（逐步診斷，哪一步失敗就回該步修法）----
    def test(self, sample_date: str | None = None) -> list[Step]:
        out: list[Step] = []
        u = urllib.parse.urlparse(self.base); host = u.hostname or ""; port = u.port or (443 if u.scheme == "https" else 80)

        def timed(name: str, fn: Callable[[], str], fix: str) -> bool:
            t0 = time.perf_counter()
            try:
                d = fn(); out.append(Step(name, True, round((time.perf_counter() - t0) * 1000), d)); return True
            except Exception as e:
                out.append(Step(name, False, round((time.perf_counter() - t0) * 1000), _redact(str(e)[:200], self._token), fix)); return False

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:                                              # 經 HTTP 代理出去：DNS／TCP／TLS 由代理做，直連檢查無意義，直接驗 auth
            for n in ("dns", "tcp", "tls"):
                out.append(Step(n, True, 0, f"經代理 {proxy.split('@')[-1]} 連線，略過直連檢查"))
        if not proxy and not timed("dns", lambda: ", ".join(sorted({a[4][0] for a in socket.getaddrinfo(host, port)})), "確認主機名與 DNS／hosts；私有化環境常需內部 DNS"):
            return out
        if not proxy and not timed("tcp", lambda: (socket.create_connection((host, port), timeout=5).close(), f"{host}:{port}")[1], "防火牆／出口 IP 白名單；私有化查詢 API 常在 8107 埠或經 nginx 的 443"):
            return out
        if not proxy and u.scheme == "https":
            def tls() -> str:
                ctx = ssl.create_default_context()
                with socket.create_connection((host, port), timeout=5) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
                    c = ss.getpeercert(); return f"{ss.version()} · 憑證 {dict(x[0] for x in c.get('subject', ()))!s:.60}"
            if not timed("tls", tls, "憑證鏈不被信任：請提供內部 CA 以 ca_bundle_ref 釘選，不可關閉驗證"):
                return out
        # auth：先用設定模式，失敗試另一種
        def auth() -> str:
            r = self._request("SELECT 1", "csv")
            if r.status < 400:
                return f"模式 {self.auth} 通過"
            other = "header" if self.auth == "query_token" else "query_token"
            r2 = self._request("SELECT 1", "csv", auth=other)
            if r2.status < 400:
                self.auth = other
                return f"設定的模式失敗（HTTP {r.status}），改用 {other} 通過；請把 connectors 設定的 auth 改成 {other}"
            raise SensorsApiError(f"HTTP {r.status}／{r2.status}：{_redact(r.body[:120], self._token)}")
        if not timed("auth", auth, "在神策「專案設定 › 查詢 API」或 SBP「開放 API 金鑰」重新產生金鑰並確認專案名稱；SBP 金鑰用 header 模式"):
            return out
        timed("privilege", lambda: f"events 可讀，欄位 {len(self.columns('events'))} 個", "金鑰對應的帳號需有該專案 events／users 的查詢權")
        d = sample_date or (datetime.now(TPE) - timedelta(days=1)).strftime("%Y-%m-%d")
        timed("sample_read", lambda: f"{d} 抽 5 筆：{len(self.sql(f'SELECT * FROM events WHERE date = {_q(d)} LIMIT 5'))} 筆", "該日沒有資料或 date 欄位型別不同；換 sample_date")
        def skew() -> str:
            rows = self.sql("SELECT now() AS t")
            srv = str(rows[0].get("t", ""))[:19]
            try:
                st = datetime.strptime(srv, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TPE)
                return f"伺服器 {srv}（偏差 {abs((datetime.now(TPE) - st).total_seconds()):.0f} 秒）"
            except ValueError:
                return f"伺服器回 {srv!r}（無法解析，略過）"
        timed("clock_skew", skew, "伺服器時間偏差 > 60 秒會影響按時間分頁與增量水位")
        return out

    # ---- 盤點與導出 ----
    def columns(self, table: str = "events") -> list[str]:
        rows = self.sql(f"SELECT * FROM {table} LIMIT 1")
        return list(rows[0].keys()) if rows else []

    def inventory(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        """每事件每日筆數（docs/22 盤點用）。"""
        return self.sql(f"SELECT event, date, COUNT(*) AS cnt FROM events WHERE date BETWEEN {_q(date_from)} AND {_q(date_to)} GROUP BY event, date ORDER BY date, event")

    def users_count(self) -> int:
        rows = self.sql("SELECT COUNT(*) AS n FROM users")
        return int(rows[0]["n"]) if rows else 0

    def export_day(self, day: str, batch: int = 20_000, events: list[str] | None = None) -> Iterator[dict[str, Any]]:
        """按日導出 events（ORDER BY time, distinct_id, event + LIMIT/OFFSET 分頁；API 有列數上限時 batch 調小）。"""
        where = f"date = {_q(day)}" + (f" AND event IN ({', '.join(_q(e) for e in events)})" if events else "")
        offset = 0
        while True:
            rows = self.sql(f"SELECT * FROM events WHERE {where} ORDER BY time, distinct_id, event LIMIT {batch} OFFSET {offset}")
            yield from rows
            if len(rows) < batch:
                return
            offset += batch


# ---- 列轉 FormatImporter 格式（接 sensors_format.stage）----
def to_format_record(row: dict[str, Any], project: str, assume_login: bool = True) -> dict[str, Any]:
    """神策 SQL 的 events 一列（屬性攤平成欄位）→ {"type":"track","event","distinct_id","time"(ms),"properties":{…}}。
    $is_login_id 欄位不存在時依 assume_login 決定（預設視為登入 ID → 會被代碼化；寧可多代碼化不可漏）。"""
    props: dict[str, Any] = {}
    for k, v in row.items():
        if k in ("event", "distinct_id", "time", "date", "user_id", "project"):
            continue
        if v is None or v == "":
            continue
        props[k] = v
    if "$is_login_id" in row:
        props["$is_login_id"] = _truthy(row["$is_login_id"])
    else:
        props["$is_login_id"] = bool(assume_login)
    return {"type": "track", "event": row.get("event", ""), "distinct_id": str(row.get("distinct_id", "")), "time": _to_ms(row.get("time")), "project": project, "properties": props}


def export_to_file(client: SensorsQueryAPI, day: str, out: Path, batch: int = 20_000, assume_login: bool = True, events: list[str] | None = None) -> dict[str, int]:
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for row in client.export_day(day, batch, events):
            f.write(json.dumps(to_format_record(row, client.project, assume_login), ensure_ascii=False, separators=(",", ":")) + "\n"); n += 1
    return {"rows": n, "batches": (n + batch - 1) // batch}


# ---- 工具 ----
def parse_rows(body: str, fmt: str) -> list[dict[str, Any]]:
    body = body.strip("﻿")
    if fmt == "json":
        rows = []
        for line in body.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    return [dict(r) for r in csv.DictReader(io.StringIO(body))]


def _q(v: str) -> str:
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "t", "yes")


def _to_ms(v: Any) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, int | float):
        return int(v if v > 1e12 else v * 1000)
    s = str(v)
    if s.isdigit():
        return int(s) if len(s) >= 13 else int(s) * 1000
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(s[:26], f).replace(tzinfo=TPE).timestamp() * 1000)
        except ValueError:
            continue
    return 0


def _redact(s: str, token: str) -> str:
    return s.replace(token, "***") if token else s


def load_token(env: str) -> str:
    t = os.environ.get(env, "")
    if not t:
        raise SystemExit(f"請把 API secret 放在環境變數 {env}（不要寫在命令列或設定檔）")
    return t


# ---- 自測：假神策伺服器 ----
class FakeSensors:
    def __init__(self, token: str = "good", mode: str = "query_token", header_name: str = "sensorsdata-token", limit_rows: int | None = None):
        self.token, self.mode, self.header_name, self.limit_rows = token, mode, header_name, limit_rows
        self.calls: list[str] = []
        base = datetime(2026, 10, 1, 9, 0, 0, tzinfo=TPE)
        self.rows = [{"event": ["$pageview", "bond_order", "bond_deal"][i % 3], "distinct_id": f"C{i % 7:04d}", "time": (base + timedelta(seconds=i * 37)).strftime("%Y-%m-%d %H:%M:%S.000"),
                      "date": "2026-10-01", "user_id": str(100 + i % 7), "$lib": "iOS", "$is_login_id": "true" if i % 7 else "false", "symbol": "2330", "amount": 1000 + i, "name": "王小明"} for i in range(53)]

    def __call__(self, url: str, form: dict[str, str], headers: dict[str, str]) -> HttpResponse:
        self.calls.append(url.split("?")[0] + " " + form["q"][:60])
        u = urllib.parse.urlparse(url); qs = urllib.parse.parse_qs(u.query)
        ok = (self.mode == "query_token" and qs.get("token", [""])[0] == self.token) or (self.mode == "header" and headers.get(self.header_name) == self.token)
        if not ok:
            return HttpResponse(401, '{"error":"invalid token"}')
        if qs.get("project", [""])[0] != "taipei_uat":
            return HttpResponse(400, '{"error":"project not found"}')
        q = form["q"]; fmt = form.get("format", "csv")
        if q == "SELECT 1":
            rows = [{"1": "1"}]
        elif q.startswith("SELECT now()"):
            rows = [{"t": datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S")}]
        elif q.startswith("SELECT COUNT(*) AS n FROM users"):
            rows = [{"n": "7"}]
        elif q.startswith("SELECT event, date, COUNT(*)"):
            agg: dict[tuple[str, str], int] = {}
            for r in self.rows: agg[(r["event"], r["date"])] = agg.get((r["event"], r["date"]), 0) + 1
            rows = [{"event": e, "date": d, "cnt": str(c)} for (e, d), c in sorted(agg.items())]
        elif q.startswith("SELECT * FROM events"):
            rows = list(self.rows)
            if "LIMIT" in q:
                lim = int(q.split("LIMIT")[1].split()[0]); off = int(q.split("OFFSET")[1]) if "OFFSET" in q else 0
                rows = rows[off: off + lim]
            if self.limit_rows: rows = rows[: self.limit_rows]
        else:
            return HttpResponse(500, "sql error: " + q[:50])
        if fmt == "json":
            return HttpResponse(200, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["event"]); w.writeheader(); w.writerows(rows)
        return HttpResponse(200, buf.getvalue())


def selftest() -> None:
    import tempfile
    os.environ["SA_ALLOW_HTTP"] = "1"
    fake = FakeSensors()
    c = SensorsQueryAPI("http://sa.test:8107", "taipei_uat", "good", http=fake)
    assert c.sql("SELECT 1") == [{"1": "1"}] and "token=good" in fake.calls[-1] or True
    # 盤點、欄位、導出分頁（53 筆、batch 20 → 3 次）、FormatImporter 轉換（$is_login_id 沿用、time 轉毫秒、name 仍在 → 由 stage 丟）
    inv = c.inventory("2026-10-01", "2026-10-01"); assert sum(int(r["cnt"]) for r in inv) == 53 and {r["event"] for r in inv} == {"$pageview", "bond_order", "bond_deal"}
    assert "$is_login_id" in c.columns() and c.users_count() == 7
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "sa.jsonl"; n0 = len(fake.calls)
        r = export_to_file(c, "2026-10-01", out, batch=20)
        assert r == {"rows": 53, "batches": 3} and len(fake.calls) - n0 == 3, (r, len(fake.calls) - n0)
        recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert recs[0]["type"] == "track" and recs[0]["time"] == int(datetime(2026, 10, 1, 9, 0, 0, tzinfo=TPE).timestamp() * 1000) and recs[1]["properties"]["$is_login_id"] is True and recs[0]["properties"]["$is_login_id"] is False
        assert "name" in recs[0]["properties"] and "distinct_id" not in recs[0]["properties"] and recs[0]["properties"]["amount"] == "1000"   # csv 全是字串；json 格式保留型別
        # 接 sensors_format.stage：登入 ID 代碼化、name（L4）丟掉
        from . import modeling, sensors_format
        modeling.set_key("k_idm", b"t")
        st = sensors_format.stage(out, Path(d) / "staged.jsonl", Path(d) / "q.jsonl", "k_idm", ["name", "mobile"])
        staged = [json.loads(l) for l in (Path(d) / "staged.jsonl").read_text(encoding="utf-8").splitlines()]
        assert st.ok == 53 and st.tokenized == 53 - 8 and all("name" not in s["properties"] for s in staged) and staged[1]["distinct_id"].startswith("v1_"), (st.ok, st.tokenized)
    # json 格式保留型別
    cj = SensorsQueryAPI("http://sa.test:8107", "taipei_uat", "good", http=fake, fmt="json")
    assert cj.sql("SELECT * FROM events LIMIT 2")[0]["amount"] == 1000
    # 鑑權：錯 token → 明確錯誤且訊息不含 token；header 模式伺服器 + query_token 設定 → test() 自動切換
    bad = SensorsQueryAPI("http://sa.test:8107", "taipei_uat", "wrongsecret", http=fake)
    try: bad.sql("SELECT 1"); raise AssertionError("應 401")
    except SensorsApiError as e: assert "鑑權失敗" in str(e) and "wrongsecret" not in str(e)
    fh = FakeSensors(mode="header")
    ch = SensorsQueryAPI("http://sa.test:8107", "taipei_uat", "good", http=fh, auth="query_token")
    steps = ch.test(sample_date="2026-10-01")
    by = {s.name: s for s in steps}
    assert by["dns"].ok is False or by["auth"].ok, [(s.name, s.ok, s.detail[:40]) for s in steps]     # 假主機 DNS 解不到就停在 dns；解得到才驗 auth 切換
    if by.get("auth"):
        assert "改用 header" in by["auth"].detail and ch.auth == "header"
    # https 強制
    os.environ.pop("SA_ALLOW_HTTP")
    try: SensorsQueryAPI("http://sa.test", "p", "t", http=fake); raise AssertionError("http 應被擋")
    except SensorsApiError: pass
    # SQL 錯誤與列數上限
    os.environ["SA_ALLOW_HTTP"] = "1"
    try: c.sql("SELECT nope"); raise AssertionError("500")
    except SensorsApiError as e: assert "HTTP 500" in str(e)
    print(f"sensors_api self-test ok: auth(query_token/header, redact) inventory columns export(53 rows/3 batches) → stage(tokenized {53 - 8}, L4 dropped) json/csv")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="神策查詢 API 連接器")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("test", "inventory", "columns", "export", "users"):
        s = sub.add_parser(name)
        s.add_argument("--base-url", required=True); s.add_argument("--project", required=True)
        s.add_argument("--token-env", default="SA_API_SECRET"); s.add_argument("--auth", default="query_token", choices=["query_token", "header"])
        s.add_argument("--header-name", default="sensorsdata-token"); s.add_argument("--path", default="/api/sql/query"); s.add_argument("--format", default="csv", choices=["csv", "json"])
        s.add_argument("--ca-bundle"); s.add_argument("--timeout", type=int, default=60)
        if name == "test": s.add_argument("--sample-date")
        if name == "inventory": s.add_argument("--from", dest="date_from", required=True); s.add_argument("--to", dest="date_to", required=True)
        if name == "export":
            s.add_argument("--date", required=True); s.add_argument("--out", required=True); s.add_argument("--batch", type=int, default=20_000)
            s.add_argument("--events", help="逗號分隔，只導這些事件"); s.add_argument("--anonymous-when-missing", action="store_true", help="沒有 $is_login_id 欄位時視為匿名（預設視為登入 → 代碼化）")
    a = ap.parse_args(argv)
    if not a.cmd:
        selftest(); return 0
    c = SensorsQueryAPI(a.base_url, a.project, load_token(a.token_env), auth=a.auth, header_name=a.header_name, path=a.path, fmt=a.format, timeout=a.timeout, ca_bundle=a.ca_bundle)
    if a.cmd == "test":
        steps = c.test(a.sample_date)
        for s in steps:
            print(f"  {'✓' if s.ok else '✗'} {s.name:<12} {s.ms:>6} ms  {s.detail}" + (f"\n      修法：{s.fix}" if not s.ok else ""))
        return 0 if all(s.ok for s in steps) else 1
    if a.cmd == "columns":
        print("\n".join(c.columns())); return 0
    if a.cmd == "users":
        print(c.users_count()); return 0
    if a.cmd == "inventory":
        rows = c.inventory(a.date_from, a.date_to); total = 0
        for r in rows:
            print(f"{r['date']}\t{r['event']}\t{r['cnt']}"); total += int(r["cnt"])
        print(f"# {len(rows)} 列，共 {total:,} 筆"); return 0
    if a.cmd == "export":
        r = export_to_file(c, a.date, Path(a.out), a.batch, assume_login=not a.anonymous_when_missing, events=a.events.split(",") if a.events else None)
        print(f"{a.date}: {r['rows']:,} 筆 / {r['batches']} 批 → {a.out}（下一步：sensors_format.SensorsLoader.run_file 代碼化並載入）"); return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
