"""串流路徑的逐列規則（docs/43 D-F3）。

不另寫一份規則：classify() 對單一列呼叫 backend.integration.modeling.apply()，
再用同一模組的 to_ch_row()／quarantine_row()／canon_json() 產生輸出。
批次路徑（batch()）是一次 apply() 整批，用來和串流結果逐列比對。
金鑰由呼叫端先用 modeling.set_key() 裝好（正式環境從 secret_ref 解出；測試用 fixtures 的合成金鑰）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import backend.integration.modeling as m

_cache: dict[str, tuple[m.Model, set[str]]] = {}


def load(model_path: str | Path) -> tuple[m.Model, set[str]]:
    """每個 Python worker 只載一次模型與 L4 欄位清單（在 UDF 的 open() 呼叫）。"""
    key = str(model_path)
    if key not in _cache:
        _cache[key] = (m.load(model_path), m.Semantic.load().forbidden_l4)
    return _cache[key]


def _result(model: m.Model, fl4: set[str], row: dict[str, Any], status: str, reason: str,
            rec: dict[str, Any] | None, ingest_ms: int | None) -> dict[str, Any]:
    if status == "ok" and rec is not None:
        payload = m.to_ch_row(model, rec, ingest_ms=ingest_ms)
        return {"status": "ok", "reason": "", "event": rec["event"], "event_id": rec["event_id"], "payload": m.canon_json(payload)}
    if status == "quarantined":
        payload = m.quarantine_row(model, row, fl4, reason)              # 只留去個資後的欄位與原因碼
    else:                                                                # 個資掃描命中（reject_and_alert）：什麼都不留
        payload = {"_rejected": True, "_dropped": sorted(map(str, row))}
    return {"status": status, "reason": reason, "event": "", "event_id": "", "payload": m.canon_json(payload)}


def classify(model_path: str | Path, row: dict[str, Any], *, now: datetime | None = None,
             ingest_ms: int | None = None) -> dict[str, Any]:
    """串流：一次一列。"""
    model, fl4 = load(model_path)
    o = m.apply(model, [row], now=now)
    if o.ok:
        return _result(model, fl4, row, "ok", "", o.ok[0], ingest_ms)
    if o.quarantined:
        return _result(model, fl4, row, "quarantined", o.quarantined[0][1], None, ingest_ms)
    return _result(model, fl4, row, "rejected", o.rejected[0][1], None, ingest_ms)


def batch(model_path: str | Path, rows: list[dict[str, Any]], *, now: datetime | None = None,
          ingest_ms: int | None = None) -> list[dict[str, Any]]:
    """批次：一次 apply() 整批，結果依輸入順序排回（apply 的隔離／拒收清單帶的是原本的列物件）。"""
    model, fl4 = load(model_path)
    o = m.apply(model, rows, now=now)
    q = {id(r): reason for r, reason in o.quarantined}
    rj = {id(r): reason for r, reason in o.rejected}
    ok_iter = iter(o.ok)
    out = []
    for r in rows:
        if id(r) in q:
            out.append(_result(model, fl4, r, "quarantined", q[id(r)], None, ingest_ms))
        elif id(r) in rj:
            out.append(_result(model, fl4, r, "rejected", rj[id(r)], None, ingest_ms))
        else:
            out.append(_result(model, fl4, r, "ok", "", next(ok_iter), ingest_ms))
    return out
