"""程序內指標庫（Prometheus 文字格式 0.0.4）。不引入第三方套件：計數器、量規、直方圖加上「抓取時才算」的 collector。

用法：
    REG.counter("dmp_http_requests_total", "…", ["method", "route", "status"]).inc(labels)
    with REG.timer("dmp_query_seconds", {"module": "events"}): ...
    REG.collector(lambda: [("dmp_source_lag_seconds", {"task": "x"}, 12.0)])    # 抓取時由排程器狀態算

同時保留每個直方圖最近 N 筆觀測（環形），讓 /v1/ops/query-perf 不連 ClickHouse 也能算 P50/P95/P99。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from typing import Any

Labels = dict[str, str]
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
RECENT = 2000


def _key(labels: Labels | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((labels or {}).items()))


def _fmt_labels(items: Iterable[tuple[str, str]]) -> str:
    s = ",".join(f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for k, v in items)
    return "{" + s + "}" if s else ""


def _num(v: float) -> str:
    if v == float("inf"): return "+Inf"
    return repr(float(v)) if not float(v).is_integer() else str(int(v))


class Counter:
    def __init__(self, name: str, help_: str, label_names: list[str]):
        self.name, self.help, self.label_names = name, help_, label_names
        self.values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self.lock = threading.Lock()

    def inc(self, labels: Labels | None = None, by: float = 1) -> None:
        with self.lock:
            self.values[_key(labels)] += by

    def get(self, labels: Labels | None = None) -> float:
        return self.values.get(_key(labels), 0.0)

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self.lock:
            out += [f"{self.name}{_fmt_labels(k)} {_num(v)}" for k, v in sorted(self.values.items())]
        return out


class Gauge(Counter):
    def set(self, value: float, labels: Labels | None = None) -> None:
        with self.lock:
            self.values[_key(labels)] = float(value)

    def render(self) -> list[str]:
        return [x.replace(" counter", " gauge") if x.startswith("# TYPE") else x for x in super().render()]


class Histogram:
    def __init__(self, name: str, help_: str, label_names: list[str], buckets: tuple[float, ...] = DEFAULT_BUCKETS):
        self.name, self.help, self.label_names, self.buckets = name, help_, label_names, tuple(buckets)
        self.counts: dict[tuple[tuple[str, str], ...], list[int]] = {}
        self.sums: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self.totals: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)
        self.recent: dict[tuple[tuple[str, str], ...], deque[tuple[float, float]]] = {}      # (at, value)
        self.lock = threading.Lock()

    def observe(self, value: float, labels: Labels | None = None, at: float | None = None) -> None:
        k = _key(labels)
        with self.lock:
            c = self.counts.setdefault(k, [0] * len(self.buckets))
            for i, b in enumerate(self.buckets):
                if value <= b: c[i] += 1
            self.sums[k] += value; self.totals[k] += 1
            self.recent.setdefault(k, deque(maxlen=RECENT)).append((at or time.time(), value))

    def samples(self, labels: Labels | None = None, since: float | None = None) -> list[float]:
        """最近的觀測值（可限定時間），給 P50/P95/P99 用。labels=None 時回所有標籤組合。"""
        with self.lock:
            keys = [_key(labels)] if labels is not None else list(self.recent)
            return [v for k in keys for (t, v) in self.recent.get(k, ()) if since is None or t >= since]

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self.lock:
            for k in sorted(self.counts):
                for i, b in enumerate(self.buckets):
                    out.append(f"{self.name}_bucket{_fmt_labels([*k, ('le', _num(b))])} {self.counts[k][i]}")
                out.append(f"{self.name}_bucket{_fmt_labels([*k, ('le', '+Inf')])} {self.totals[k]}")
                out.append(f"{self.name}_sum{_fmt_labels(k)} {_num(round(self.sums[k], 6))}")
                out.append(f"{self.name}_count{_fmt_labels(k)} {self.totals[k]}")
        return out


class Registry:
    def __init__(self) -> None:
        self.metrics: dict[str, Counter | Gauge | Histogram] = {}
        self.collectors: list[Callable[[], Iterable[tuple[str, Labels, float]]]] = []
        self.collector_help: dict[str, str] = {}
        self.started_at = time.time()

    def counter(self, name: str, help_: str = "", labels: list[str] | None = None) -> Counter:
        m = self.metrics.get(name)
        if m is None:
            m = self.metrics[name] = Counter(name, help_, labels or [])
        return m  # type: ignore[return-value]

    def gauge(self, name: str, help_: str = "", labels: list[str] | None = None) -> Gauge:
        m = self.metrics.get(name)
        if m is None:
            m = self.metrics[name] = Gauge(name, help_, labels or [])
        return m  # type: ignore[return-value]

    def histogram(self, name: str, help_: str = "", labels: list[str] | None = None, buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> Histogram:
        m = self.metrics.get(name)
        if m is None:
            m = self.metrics[name] = Histogram(name, help_, labels or [], buckets)
        return m  # type: ignore[return-value]

    def collector(self, fn: Callable[[], Iterable[tuple[str, Labels, float]]], help_: dict[str, str] | None = None) -> None:
        """抓取時才計算的量規（例如來源延遲、交付狀態、表用量）；fn 回 (metric, labels, value) 列表。"""
        self.collectors.append(fn); self.collector_help.update(help_ or {})

    class _Timer:
        def __init__(self, h: Histogram, labels: Labels | None): self.h, self.labels, self.t0, self.ms = h, labels, 0.0, 0.0
        def __enter__(self) -> Registry._Timer: self.t0 = time.perf_counter(); return self
        def __exit__(self, *a: Any) -> None: self.ms = (time.perf_counter() - self.t0) * 1000; self.h.observe(self.ms / 1000, self.labels)

    def timer(self, name: str, labels: Labels | None = None) -> Registry._Timer:
        return Registry._Timer(self.histogram(name), labels)

    def render(self) -> str:
        collected: dict[str, list[tuple[Labels, float]]] = defaultdict(list)
        for fn in self.collectors:                                       # 先跑 collector（失敗只計數，不影響其他指標）
            try:
                for name, labels, value in fn():
                    collected[name].append((labels, value))
            except Exception as e:
                self.counter("dmp_collector_errors_total", "collector 例外數", ["error"]).inc({"error": type(e).__name__})
        lines: list[str] = []
        for m in list(self.metrics.values()):
            lines += m.render()
        for name, rows in sorted(collected.items()):
            lines += [f"# HELP {name} {self.collector_help.get(name, '')}".rstrip(), f"# TYPE {name} gauge"]
            lines += [f"{name}{_fmt_labels(_key(labels))} {_num(v)}" for labels, v in rows]
        lines += ["# HELP dmp_process_uptime_seconds API 程序啟動至今秒數", "# TYPE dmp_process_uptime_seconds gauge", f"dmp_process_uptime_seconds {_num(round(time.time() - self.started_at, 3))}"]
        return "\n".join(lines) + "\n"


REG = Registry()

# ---- 共用指標（各模組直接呼叫，不用自己建） ----
HTTP_REQUESTS = REG.counter("dmp_http_requests_total", "HTTP 請求數", ["method", "route", "status"])
HTTP_SECONDS = REG.histogram("dmp_http_request_seconds", "HTTP 請求耗時（秒）", ["method", "route"], (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
QUERY_SECONDS = REG.histogram("dmp_query_seconds", "分析查詢耗時（秒），依模組", ["module"])
QUERY_TOTAL = REG.counter("dmp_query_total", "分析查詢數，依模組與結果", ["module", "status"])
QUERY_ROWS = REG.counter("dmp_query_rows_total", "分析查詢回傳列數，依模組", ["module"])
ROUTINE_SECONDS = REG.histogram("dmp_routine_seconds", "背景例行耗時（秒）", ["job"], (0.1, 0.5, 1, 5, 10, 30, 60, 300, 600, 1800))
ROUTINE_TOTAL = REG.counter("dmp_routine_runs_total", "背景例行執行數", ["job", "status"])
ROUTINE_LAST = REG.gauge("dmp_routine_last_run_timestamp", "背景例行最近一次執行時間（epoch）", ["job"])
ROUTINE_LAST_STATUS = REG.gauge("dmp_routine_last_ok", "背景例行最近一次是否成功（1／0）", ["job"])


def observe_query(module: str, ms: float, ok: bool = True, rows: int = 0, at: float | None = None) -> None:
    QUERY_SECONDS.observe(ms / 1000, {"module": module}, at)
    QUERY_TOTAL.inc({"module": module, "status": "ok" if ok else "error"})
    if rows: QUERY_ROWS.inc({"module": module}, rows)


class routine:
    """with routine("segments"): … → 耗時、成功／失敗計數、最近一次時間；例外照常拋出（呼叫端決定要不要吞）。"""
    def __init__(self, job: str, now: Callable[[], float] = time.time): self.job, self.now, self.t0, self.ms = job, now, 0.0, 0.0
    def __enter__(self) -> routine: self.t0 = time.perf_counter(); return self
    def __exit__(self, et: Any, *a: Any) -> None:
        self.ms = (time.perf_counter() - self.t0) * 1000
        ROUTINE_SECONDS.observe(self.ms / 1000, {"job": self.job}); ROUTINE_TOTAL.inc({"job": self.job, "status": "error" if et else "ok"})
        ROUTINE_LAST.set(self.now(), {"job": self.job}); ROUTINE_LAST_STATUS.set(0 if et else 1, {"job": self.job})


def quantiles(values: list[float], qs: tuple[float, ...] = (0.5, 0.95, 0.99)) -> list[float | None]:
    if not values: return [None] * len(qs)
    xs = sorted(values); n = len(xs)
    return [xs[min(n - 1, max(0, int(round(q * (n - 1)))))] for q in qs]


def route_template(path: str) -> str:
    """沒有路由物件時把路徑裡的 id 段換成 {id}，避免標籤爆量：/v1/dashboards/d_abc/cards/c_1 → /v1/dashboards/{id}/cards/{id}。"""
    parts = path.split("/")
    out = []
    for i, p in enumerate(parts):
        prev = parts[i - 1] if i else ""
        if p and prev and prev not in ("v1", "auth", "") and (any(ch.isdigit() for ch in p) or "_" in p or len(p) > 24 or ":" in p):
            out.append("{id}")
        else:
            out.append(p)
    return "/".join(out)


def selftest() -> None:
    r = Registry()
    c = r.counter("t_total", "x", ["a"]); c.inc({"a": "1"}); c.inc({"a": "1"}, 3); c.inc({"a": "2"})
    g = r.gauge("t_gauge", "y"); g.set(2.5)
    h = r.histogram("t_seconds", "z", ["m"], (0.1, 1)); h.observe(0.05, {"m": "e"}); h.observe(0.5, {"m": "e"}); h.observe(7, {"m": "e"})
    r.collector(lambda: [("t_lag_seconds", {"task": "s1"}, 12.0)], {"t_lag_seconds": "lag"})
    r.collector(lambda: 1 / 0)                                                             # 壞 collector 只記錯誤
    txt = r.render()
    assert 't_total{a="1"} 4' in txt and 't_total{a="2"} 1' in txt and "t_gauge 2.5" in txt, txt
    assert 't_seconds_bucket{m="e",le="0.1"} 1' in txt and 't_seconds_bucket{m="e",le="1"} 2' in txt and 't_seconds_bucket{m="e",le="+Inf"} 3' in txt and 't_seconds_sum{m="e"} 7.55' in txt and 't_seconds_count{m="e"} 3' in txt, txt
    assert '# TYPE t_lag_seconds gauge\nt_lag_seconds{task="s1"} 12' in txt and 'dmp_collector_errors_total{error="ZeroDivisionError"} 1' in txt and "dmp_process_uptime_seconds" in txt, txt
    assert h.samples({"m": "e"}) == [0.05, 0.5, 7] and h.samples() == [0.05, 0.5, 7]
    assert quantiles([5, 1, 3, 2, 4]) == [3, 5, 5] and quantiles([]) == [None, None, None]
    assert route_template("/v1/dashboards/d_abc12/cards/c_1/data") == "/v1/dashboards/{id}/cards/{id}/data" and route_template("/v1/iam/me") == "/v1/iam/me" and route_template("/v1/segments") == "/v1/segments"
    assert route_template("/v1/tags/custom-fields") == "/v1/tags/custom-fields" and route_template("/v1/iam/users/entra:adm") == "/v1/iam/users/{id}"
    with routine("t_job"): pass
    try:
        with routine("t_job"): raise RuntimeError("x")
    except RuntimeError: pass
    assert ROUTINE_TOTAL.get({"job": "t_job", "status": "ok"}) == 1 and ROUTINE_TOTAL.get({"job": "t_job", "status": "error"}) == 1 and ROUTINE_LAST_STATUS.get({"job": "t_job"}) == 0
    assert 'le="+Inf"' in REG.render()
    print("ops metrics self-test ok")
