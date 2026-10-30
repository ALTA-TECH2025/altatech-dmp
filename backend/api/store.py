"""控制面儲存（自訂角色、使用者指派、審計）：Protocol + 記憶體／JSON 檔實作。正式環境換 PostgreSQL（iam_roles、iam_assignments、audit_log），介面不變。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol


class Conflict(Exception):
    """寫入違反唯一限制（例：同專案書籤同名）。兩種儲存都在寫入當下擋，不靠先查再寫，兩個請求同時存也只會成功一個。"""


def bookmark_key(doc: dict[str, Any]) -> tuple[str, str]:
    """書籤唯一鍵：同專案（query.project）＋名稱完全相同（Windy 2026-10-28：大小寫、全形／半形不同不算重名；前後空白在建立時已去掉）。"""
    q = doc.get("query")
    return (str(q.get("project") or "") if isinstance(q, dict) else "", str(doc.get("name") or ""))


UNIQUE: dict[str, Any] = {"bookmark": bookmark_key}   # kind → 唯一鍵；PostgreSQL 有對應的部分唯一索引（store_pg）


class Store(Protocol):
    def roles(self) -> list[dict[str, Any]]: ...
    def put_role(self, role: dict[str, Any]) -> None: ...
    def delete_role(self, key: str) -> None: ...
    def assignments(self) -> list[dict[str, Any]]: ...
    def put_assignment(self, a: dict[str, Any]) -> None: ...
    def delete_assignment(self, id_: str) -> None: ...
    def audit(self, event: str, data: dict[str, Any]) -> None: ...
    def audit_tail(self, n: int = 100) -> list[dict[str, Any]]: ...
    def segments(self) -> list[dict[str, Any]]: ...
    def put_segment(self, seg: dict[str, Any]) -> None: ...
    def delete_segment(self, id_: str) -> None: ...
    def items(self, kind: str) -> list[dict[str, Any]]: ...
    def put_item(self, kind: str, obj: dict[str, Any]) -> None: ...
    def delete_item(self, kind: str, id_: str) -> None: ...


class MemoryStore:
    """path 給定時每次寫入都落成 JSON（單機開發／測試環境用）。"""
    def __init__(self, path: Path | None = None):
        self.path = path
        self.lock = threading.Lock()
        self.d: dict[str, Any] = {"roles": {}, "assignments": {}, "audit": []}
        if path and path.exists():
            self.d = json.loads(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")

    def roles(self) -> list[dict[str, Any]]:
        return list(self.d["roles"].values())

    def put_role(self, role: dict[str, Any]) -> None:
        with self.lock:
            self.d["roles"][role["key"]] = role; self._save()

    def delete_role(self, key: str) -> None:
        with self.lock:
            self.d["roles"].pop(key, None); self._save()

    def assignments(self) -> list[dict[str, Any]]:
        return list(self.d["assignments"].values())

    def put_assignment(self, a: dict[str, Any]) -> None:
        with self.lock:
            a.setdefault("id", "asg_" + uuid.uuid4().hex[:10]); self.d["assignments"][a["id"]] = a; self._save()

    def delete_assignment(self, id_: str) -> None:
        with self.lock:
            self.d["assignments"].pop(id_, None); self._save()

    def audit(self, event: str, data: dict[str, Any]) -> None:
        with self.lock:
            self.d["audit"].append({"event": event, "at": time.time(), **data})
            self.d["audit"] = self.d["audit"][-10_000:]; self._save()

    def audit_tail(self, n: int = 100) -> list[dict[str, Any]]:
        return self.d["audit"][-n:]

    def segments(self) -> list[dict[str, Any]]:
        return list(self.d.setdefault("segments", {}).values())

    def put_segment(self, seg: dict[str, Any]) -> None:
        with self.lock:
            self.d.setdefault("segments", {})[seg["id"]] = seg; self._save()

    def delete_segment(self, id_: str) -> None:
        with self.lock:
            self.d.setdefault("segments", {}).pop(id_, None); self._save()

    def items(self, kind: str) -> list[dict[str, Any]]:
        return list(self.d.setdefault(kind, {}).values())

    def put_item(self, kind: str, obj: dict[str, Any]) -> None:
        with self.lock:
            bucket = self.d.setdefault(kind, {})
            key_of = UNIQUE.get(kind)
            if key_of:                                                       # 與 PostgreSQL 唯一索引同規則；既有的重複（索引建立前留下的）改其他欄位不擋
                k, old = key_of(obj), bucket.get(obj["id"])
                if (old is None or key_of(old) != k) and any(i != obj["id"] and key_of(x) == k for i, x in bucket.items()):
                    raise Conflict(f"{kind} 唯一鍵重複：{k}")
            bucket[obj["id"]] = obj; self._save()

    def delete_item(self, kind: str, id_: str) -> None:
        with self.lock:
            self.d.setdefault(kind, {}).pop(id_, None); self._save()
