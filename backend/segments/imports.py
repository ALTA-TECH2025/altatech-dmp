"""導入名單（openapi /v1/segments/imports；docs/21 §1.1）。

第一步：上傳客戶代碼或 one_id 清單 → 客戶代碼以 token_v1(k_idm)（與舊平台登入 ID、GA user_id 同一把金鑰）轉成 one_id → 去重 → 寫 dmp.segment_imports。
        原始代碼只在記憶體裡轉換，不寫入 ClickHouse、store 或審計；回總數／去重後數／對到用戶表的人數。
第二步：以 kind=import 建分群（rule.source = {type: import, import_id}），計算時從 dmp.segment_imports 取人，照樣與資料權限交集。
標籤版（舊平台標籤快照）：一人一個標籤值，寫 dmp.tag_imports。
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..security.policy import Subject
from ..semantic.compiler.events import q

ID_TYPES = ("customer_code", "one_id")
MAX_VALUES = 200_000
BATCH = 5_000
CUSTOMER_CODE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
ONE_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")
IMPORT_ID = re.compile(r"^im_[0-9a-f]{10}$")


class Unavailable(Exception):
    """503：未連接 ClickHouse 或未注入 token 金鑰。"""


def to_one_id(id_type: str, v: str, key_ref: str = "k_idm") -> str | None:
    """單一值 → one_id；格式不合回 None（算 invalid）。客戶代碼缺金鑰丟 Unavailable。"""
    s = str(v).strip()
    if id_type == "one_id":
        return s if ONE_ID.fullmatch(s) else None
    if not CUSTOMER_CODE.fullmatch(s):
        return None
    from ..integration import modeling
    try:
        return str(modeling.apply_transform(f"token_v1({key_ref})", s))
    except KeyError:
        raise Unavailable(f"未設定 token 金鑰（{key_ref}），無法轉換客戶代碼") from None


class ImportService:
    def __init__(self, store: Any, ch: Any | None, now: Callable[[], datetime] | None = None):
        self.store, self.ch = store, ch
        self.now = now or (lambda: datetime.now(ZoneInfo("Asia/Taipei")))

    def get(self, id_: str) -> dict[str, Any]:
        for x in self.store.items("segment_import"):
            if x["id"] == id_:
                return x
        raise KeyError(id_)

    def create(self, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        id_type = str(body.get("id_type") or "customer_code")
        if id_type not in ID_TYPES:
            raise ValueError(f"id_type 只能是 {ID_TYPES}")
        values = body.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError("values 不可為空")
        if len(values) > MAX_VALUES:
            raise ValueError(f"一次最多 {MAX_VALUES:,} 筆")
        ids: set[str] = set(); invalid = 0
        for v in values:
            oid = to_one_id(id_type, v) if isinstance(v, (str, int)) else None
            if oid is None:
                invalid += 1
            else:
                ids.add(oid)
        if not ids:
            raise ValueError("清單裡沒有格式正確的值")
        return self._write(str(body.get("project") or "altatech_dmp"), id_type, ids, None, str(body.get("label") or "")[:60], len(values), invalid, subject)

    def create_pairs(self, project: str, pairs: Iterable[tuple[str, str | None]], label: str, subject: Subject, total: int, invalid: int) -> dict[str, Any]:
        """已轉好的 one_id（遷移快照用）；value 非 None 時寫 dmp.tag_imports（一人一值，重複取第一個）。"""
        vals: dict[str, str | None] = {}
        for oid, v in pairs:
            vals.setdefault(oid, v)
        if not vals:
            raise ValueError("沒有可導入的成員")
        tagged = any(v is not None for v in vals.values())
        return self._write(project, "one_id", set(vals), vals if tagged else None, label, total, invalid, subject)

    def _write(self, project: str, id_type: str, ids: set[str], values: dict[str, str | None] | None, label: str, total: int, invalid: int, subject: Subject) -> dict[str, Any]:
        if self.ch is None:
            raise Unavailable("此環境未連接 ClickHouse")
        iid = "im_" + uuid.uuid4().hex[:10]
        table = "dmp.tag_imports" if values is not None else "dmp.segment_imports"
        rows = sorted(ids)
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            if values is not None:
                vs = ", ".join(f"({q(iid)}, {q(o)}, {q(values[o] or '')})" for o in chunk)
                self.ch.query(f"INSERT INTO dmp.tag_imports (import_id, one_id, tag_value) VALUES {vs}")
            else:
                vs = ", ".join(f"({q(iid)}, {q(o)})" for o in chunk)
                self.ch.query(f"INSERT INTO dmp.segment_imports (import_id, one_id) VALUES {vs}")
        matched = int((self.ch.query(f"SELECT count() FROM {table} WHERE import_id = {q(iid)} AND one_id IN (SELECT one_id FROM dmp.users FINAL)") or "0").strip() or 0)
        rec = {"id": iid, "project": project, "id_type": id_type, "label": label, "table": table, "total": total, "distinct": len(rows), "matched": matched,
               "unmatched": len(rows) - matched, "invalid": invalid, "created_by": subject.user_id, "created_at": self.now().isoformat(timespec="seconds")}
        self.store.put_item("segment_import", rec)
        self.store.audit("segment.import.created", {"user": subject.user_id, "import": iid, "id_type": id_type, "total": total, "distinct": len(rows), "matched": matched, "invalid": invalid})
        return rec
