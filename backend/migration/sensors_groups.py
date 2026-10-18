"""神策分群／標籤遷移（openapi /v1/migration/sensors-groups；docs/22 §3.4）。

神策把分群存成 users 表的 user_group_<名稱> 欄（0／1），標籤存成 user_tag_<名稱> 欄（值）。三種處理：
- 快照：規則重建不了（或不值得重建）的，取當下成員 → second_id（登入 ID）以 token_v1(k_idm) 轉 one_id、沒有登入 ID 的用 first_id
        → 導入名單 → 建 kind=import 的分群或標籤並算一次。之後不會再變。
- 已重建：分析師用規則建構器重建後，連到 DMP 分群／標籤，比對人數差異。
- 略過：已停用、不再需要（附原因）。
原始登入 ID 只在記憶體轉換，不寫入 store、ClickHouse 或審計。
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from ..security.policy import Subject, authorize
from ..segments.imports import Unavailable, to_one_id

COLUMN = re.compile(r"^user_(group|tag)_[A-Za-z0-9_]{1,80}$")
PAGE = 20_000
MAX_MEMBERS = 2_000_000
STATUSES = ("pending", "snapshot", "rebuilt", "skipped")


class Conflict(Exception):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class GroupMigration:
    def __init__(self, store: Any, client: Any | None, segments: Any, tags: Any, project: str = "altatech_dmp", now: Callable[[], str] = _now):
        self.store, self.client, self.segments, self.tags, self.project, self.now = store, client, segments, tags, project, now

    # ---------------------------------------------------------------- 讀
    def _items(self) -> dict[str, dict[str, Any]]:
        return {x["name"]: x for x in self.store.items("sensors_group")}

    def get(self, name: str) -> dict[str, Any]:
        it = self._items().get(name)
        if it is None:
            raise KeyError(name)
        return self._with_dmp(it)

    def list(self) -> dict[str, Any]:
        items = sorted((self._with_dmp(x) for x in self._items().values()), key=lambda x: (x["kind"], -int(x.get("users") or 0), x["name"]))
        scan = next((x for x in self.store.items("sensors_group_scan")), None)
        summary = {"total": len(items), **{s: sum(1 for x in items if x["status"] == s) for s in STATUSES}}
        return {"items": items, "scanned_at": (scan or {}).get("at"), "summary": summary, "sensors_configured": self.client is not None}

    def _with_dmp(self, it: dict[str, Any]) -> dict[str, Any]:
        """補上 DMP 端最新人數與差異（分群／標籤可能之後重算過）。"""
        d = it.get("dmp")
        if not d:
            return {**it, "dmp": None}
        try:
            obj = self.segments.get(d["id"]) if d["type"] == "segment" else self.tags.get(d["id"])
        except KeyError:
            return {**it, "dmp": {**d, "users": None, "diff_pct": None, "missing": True}}
        users = (obj.get("latest") or {}).get("users")
        sa = int(it.get("users") or 0)
        diff = round((int(users) - sa) / sa, 4) if users is not None and sa else None
        return {**it, "dmp": {"type": d["type"], "id": d["id"], "name": obj.get("name", d.get("name")), "users": users, "diff_pct": diff}}

    # ---------------------------------------------------------------- 掃描
    def scan(self, subject: Subject) -> dict[str, Any]:
        if self.client is None:
            raise Unavailable("此節點未設定神策連線（SA_BASE_URL／SA_PROJECT／SA_API_SECRET）")
        cols = [c for c in self.client.columns("users") if COLUMN.fullmatch(c)]
        cur = self._items(); at = self.now()
        for c in cols:
            kind = "segment" if c.startswith("user_group_") else "tag"
            if kind == "segment":
                rows = self.client.sql(f"SELECT COUNT(*) AS n FROM users WHERE {c} = 1")
                users, values = int((rows or [{}])[0].get("n") or 0), None
            else:
                rows = self.client.sql(f"SELECT COUNT(*) AS n, COUNT(DISTINCT {c}) AS k FROM users WHERE {c} IS NOT NULL")
                users, values = int((rows or [{}])[0].get("n") or 0), int((rows or [{}])[0].get("k") or 0)
            old = cur.get(c) or {"status": "pending", "dmp": None, "note": ""}
            self.store.put_item("sensors_group", {"id": c, "name": c, "kind": kind, "cn": c.split("_", 2)[2], "users": users, "values": values,
                                                  "status": old["status"], "dmp": old.get("dmp"), "note": old.get("note", ""), "updated_at": old.get("updated_at") or at, "scanned_at": at})
        self.store.put_item("sensors_group_scan", {"id": "current", "at": at, "by": subject.user_id, "columns": len(cols)})
        self.store.audit("migration.groups.scanned", {"user": subject.user_id, "segments": sum(1 for c in cols if c.startswith("user_group_")), "tags": sum(1 for c in cols if c.startswith("user_tag_"))})
        return self.list()

    # ---------------------------------------------------------------- 處理
    def _members(self, it: dict[str, Any]) -> tuple[list[tuple[str, str | None]], int, int]:
        c = it["name"]
        where = f"{c} = 1" if it["kind"] == "segment" else f"{c} IS NOT NULL"
        sel = "first_id, second_id" + ("" if it["kind"] == "segment" else f", {c} AS v")
        pairs: list[tuple[str, str | None]] = []; total = invalid = 0; off = 0
        while True:
            rows = self.client.sql(f"SELECT {sel} FROM users WHERE {where} ORDER BY id LIMIT {PAGE} OFFSET {off}")
            for r in rows:
                total += 1
                login = str(r.get("second_id") or "").strip()
                oid = to_one_id("customer_code", login) if login else to_one_id("one_id", str(r.get("first_id") or ""))
                if oid is None:
                    invalid += 1; continue
                pairs.append((oid, None if it["kind"] == "segment" else str(r.get("v") or "")[:40]))
            if total > MAX_MEMBERS:
                raise Conflict(f"成員超過 {MAX_MEMBERS:,}，請改以規則重建")
            if len(rows) < PAGE:
                return pairs, total, invalid
            off += PAGE

    def snapshot(self, name: str, dmp_name: str | None, subject: Subject) -> dict[str, Any]:
        it = self._items().get(name)
        if it is None:
            raise KeyError(name)
        if it["status"] in ("snapshot", "rebuilt"):
            raise Conflict(f"已處理（{it['status']}）")
        if self.client is None:
            raise Unavailable("此節點未設定神策連線（SA_BASE_URL／SA_PROJECT／SA_API_SECRET）")
        label = (dmp_name or f"神策：{it['cn']}").strip()[:60]
        pairs, total, invalid = self._members(it)
        imp = self.segments.imports.create_pairs(self.project, pairs, label, subject, total, invalid)
        decision = authorize(subject, "use", "users")
        full = authorize(Subject(subject.user_id, ["data_analyst"]), "use", "users")          # 同分群計算：全量名單，查詢端再套行級
        desc = f"神策{'分群' if it['kind'] == 'segment' else '標籤'} {name} 於 {self.now()[:10]} 的快照（{imp['distinct']:,} 人）"
        if it["kind"] == "segment":
            obj = self.segments.create({"name": label, "kind": "import", "description": desc, "rule": {"project": self.project, "source": {"type": "import", "import_id": imp["id"]}}}, subject, decision)
            if obj["status"] == "active":
                self.segments.compute(obj["id"], subject, full)
        else:
            obj = self.tags.create({"name": label, "kind": "import", "import_id": imp["id"], "directory": "其他", "description": desc, "project": self.project}, subject, decision)
            if obj["status"] == "active":
                self.tags.compute(obj["id"], subject, full)
        it.update({"status": "snapshot", "dmp": {"type": it["kind"], "id": obj["id"], "name": label}, "updated_at": self.now(), "import_id": imp["id"]})
        self.store.put_item("sensors_group", it)
        self.store.audit("migration.group.snapshot", {"user": subject.user_id, "group": name, "import": imp["id"], "total": total, "distinct": imp["distinct"], "matched": imp["matched"], "dmp": it["dmp"]})
        return self.get(name)

    def link(self, name: str, type_: str, id_: str, subject: Subject) -> dict[str, Any]:
        it = self._items().get(name)
        if it is None:
            raise KeyError(name)
        if type_ not in ("segment", "tag"):
            raise ValueError("type 只能是 segment／tag")
        obj = self.segments.get(id_) if type_ == "segment" else self.tags.get(id_)
        it.update({"status": "rebuilt", "dmp": {"type": type_, "id": id_, "name": obj.get("name", id_)}, "updated_at": self.now()})
        self.store.put_item("sensors_group", it)
        self.store.audit("migration.group.linked", {"user": subject.user_id, "group": name, "dmp": it["dmp"]})
        return self.get(name)

    def skip(self, name: str, note: str, subject: Subject) -> dict[str, Any]:
        it = self._items().get(name)
        if it is None:
            raise KeyError(name)
        note = str(note or "").strip()
        if not 1 <= len(note) <= 200:
            raise ValueError("略過需附原因（1–200 字）")
        it.update({"status": "skipped", "note": note, "updated_at": self.now()})
        self.store.put_item("sensors_group", it)
        self.store.audit("migration.group.skipped", {"user": subject.user_id, "group": name, "note": note})
        return self.get(name)
