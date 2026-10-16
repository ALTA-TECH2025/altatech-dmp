"""控制面儲存（自訂角色、使用者指派、審計）：Protocol + 記憶體／JSON 檔實作。正式環境換 PostgreSQL（iam_roles、iam_assignments、audit_log），介面不變。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol


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
