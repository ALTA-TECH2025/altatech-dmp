"""審批單與匯出申請。

審批單：{type, requester, approver_roles, summary, detail, object{kind,id}, status pending→approved/rejected/expired}。
  審批角色（docs/10 §6）：匯出／分群／推送／agent 提議／L2 揭露 = 數位經營；L3 揭露 = 系統管理員；admin 皆可；不能審自己的申請（admin 例外）。
  核可／退回後呼叫受控物件的 hook（匯出：改 approved／rejected；分群：生效／退回）。逾期（7 天）未審 → expired。
匯出：申請（需 users.export 模組權限、用途、保存天數；先估列數 ≤ masking.yaml export.max_rows）→ 審批單 → 核可後 7 天內由申請者本人產檔（authorize(action="export") 帶 approval_id；
  欄位策略比 UI 嚴一級：masking.yaml 的 export 策略）→ CSV：檔頭註解（申請單、使用者、時間、浮水印）+ 每列 _watermark 欄 → 下載；三步都審計。
"""
from __future__ import annotations

import csv
import hashlib
import io
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..security.policy import MASK, Subject, authorize, has_grant
from ..semantic.compiler import Catalog
from ..semantic.compiler.users import compile_estimate, compile_export, parse_users

APPROVER_ROLES: dict[str, list[str]] = {"export": ["digital_ops"], "segment": ["digital_ops"], "push": ["digital_ops"], "reveal_l2": ["digital_ops"], "reveal_l3": ["admin"], "agent_proposal": ["digital_ops"]}
TTL_DAYS = 7
EXPORT_WINDOW_DAYS = 7
MAX_EXPORT_ROWS = int(MASK.get("export", {}).get("max_rows", 100_000))


class Conflict(Exception):
    """409"""


class ApprovalService:
    def __init__(self, store: Any, now: Callable[[], datetime] | None = None):
        self.store = store
        self.now = now or (lambda: datetime.now(ZoneInfo("Asia/Taipei")))
        self.hooks: dict[str, Callable[[dict[str, Any], bool, Subject], None]] = {}      # object.kind → 核可／退回回寫

    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def create(self, type_: str, requester: Subject, summary: str, detail: dict[str, Any], obj: dict[str, str]) -> dict[str, Any]:
        if type_ not in APPROVER_ROLES:
            raise ValueError(f"審批類型只能是 {sorted(APPROVER_ROLES)}")
        a = {"id": "ap_" + uuid.uuid4().hex[:10], "type": type_, "requester": requester.user_id, "approver_roles": list(APPROVER_ROLES[type_]), "summary": summary, "detail": detail, "object": obj,
             "status": "pending", "created_at": self._ts(), "expires_at": (self.now() + timedelta(days=TTL_DAYS)).isoformat(timespec="seconds"), "decided_at": None, "decided_by": None, "note": ""}
        self.store.put_item("approvals", a)
        self.store.audit("approval.created", {"user": requester.user_id, "approval": a["id"], "type": type_, "object": obj, "summary": summary})
        return a

    def get(self, id_: str) -> dict[str, Any]:
        for a in self.store.items("approvals"):
            if a["id"] == id_:
                self._expire(a); return a
        raise KeyError(id_)

    def _expire(self, a: dict[str, Any]) -> None:
        if a["status"] == "pending" and datetime.fromisoformat(a["expires_at"]) <= self.now():
            a["status"] = "expired"; a["decided_at"] = self._ts(); self.store.put_item("approvals", a)
            self.store.audit("approval.expired", {"approval": a["id"], "type": a["type"]})
            hook = self.hooks.get(a["object"].get("kind", ""))
            if hook: hook(a, False, Subject("system:expiry", []))

    @staticmethod
    def can_approve(subject: Subject, type_: str) -> bool:
        return "admin" in subject.roles or any(r in APPROVER_ROLES.get(type_, []) for r in subject.roles)

    def list(self, subject: Subject, scope: str = "to_me", status: str | None = None, type_: str | None = None) -> list[dict[str, Any]]:
        out = []
        for a in self.store.items("approvals"):
            self._expire(a)
            if status and a["status"] != status: continue
            if type_ and a["type"] != type_: continue
            if scope == "mine" and a["requester"] != subject.user_id: continue
            if scope == "to_me" and not self.can_approve(subject, a["type"]): continue
            if scope == "all" and "admin" not in subject.roles and not (self.can_approve(subject, a["type"]) or a["requester"] == subject.user_id): continue
            out.append(a)
        return sorted(out, key=lambda a: a["created_at"], reverse=True)

    def decide(self, id_: str, subject: Subject, approve: bool, note: str = "") -> dict[str, Any]:
        a = self.get(id_)
        if a["status"] != "pending":
            raise Conflict(f"審批單不在待審批狀態（{a['status']}）")
        if not self.can_approve(subject, a["type"]):
            raise PermissionError(f"此類型（{a['type']}）需 {'／'.join(a['approver_roles'])} 審批")
        if a["requester"] == subject.user_id and "admin" not in subject.roles:
            raise Conflict("不能審批自己的申請")
        if not approve and not note.strip():
            raise ValueError("退回需附意見")
        a.update({"status": "approved" if approve else "rejected", "decided_at": self._ts(), "decided_by": subject.user_id, "note": note.strip()})
        self.store.put_item("approvals", a)
        self.store.audit("approval.decided", {"user": subject.user_id, "approval": a["id"], "type": a["type"], "decision": a["status"], "requester": a["requester"], "object": a["object"], "note": a["note"]})
        hook = self.hooks.get(a["object"].get("kind", ""))
        if hook: hook(a, approve, subject)
        return a

    def close_for_object(self, kind: str, id_: str, approve: bool, subject: Subject, note: str = "") -> None:
        """受控物件被直接審批（例：/v1/segments/{id}/approve）時同步關閉它的審批單，不再觸發 hook。"""
        for a in self.store.items("approvals"):
            if a["object"] == {"kind": kind, "id": id_} and a["status"] == "pending":
                a.update({"status": "approved" if approve else "rejected", "decided_at": self._ts(), "decided_by": subject.user_id, "note": note}); self.store.put_item("approvals", a)


class ExportService:
    def __init__(self, store: Any, ch: Any | None, approvals: ApprovalService, export_dir: Path, cat: Catalog | None = None, now: Callable[[], datetime] | None = None, members_rule: Callable[[str, str], tuple[dict[str, Any], dict[str, Any]]] | None = None):
        self.store, self.ch, self.approvals, self.cat = store, ch, approvals, cat or Catalog()
        self.dir = Path(export_dir); self.dir.mkdir(parents=True, exist_ok=True)
        self.now = now or approvals.now
        self.members_rule = members_rule          # (segment_id, basetime) → (UsersQuery raw, version)
        approvals.hooks["export"] = self._on_decided

    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def get(self, id_: str) -> dict[str, Any]:
        for e in self.store.items("exports"):
            if e["id"] == id_:
                if e["status"] in ("approved", "generated", "downloaded") and e.get("expires_at") and datetime.fromisoformat(e["expires_at"]) <= self.now():
                    e["status"] = "expired"; self.store.put_item("exports", e)
                return e
        raise KeyError(id_)

    def list(self, subject: Subject) -> list[dict[str, Any]]:
        allv = "admin" in subject.roles or "digital_ops" in subject.roles
        out = [self.get(e["id"]) for e in self.store.items("exports") if allv or e["requester"] == subject.user_id]
        return sorted(out, key=lambda e: e["created_at"], reverse=True)

    def _rule(self, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        kind = str(body.get("kind") or "users")
        if kind == "users":
            rule = body.get("rule")
            if not isinstance(rule, dict): raise ValueError("kind=users 需要 rule（UsersQuery）")
            return rule, "用戶列表"
        if kind == "segment_members":
            if not self.members_rule: raise ValueError("此環境沒有分群服務")
            rule, ver = self.members_rule(str(body.get("segment_id") or ""), str(body.get("basetime") or "latest"))
            return rule, f"分群成員 {body.get('segment_id')} v{ver['version']}（{ver['basetime']}）"
        raise ValueError("kind 只能是 users／segment_members")

    def request(self, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        if not has_grant(subject, "export", "users"):
            raise PermissionError("此角色沒有用戶列表匯出權限（users.export）")
        purpose = str(body.get("purpose") or "").strip()
        if not 4 <= len(purpose) <= 200: raise ValueError("purpose 需 4–200 字")
        keep = int(body.get("retention_days") or 30)
        if not 1 <= keep <= 90: raise ValueError("retention_days 需在 1–90")
        rule, what = self._rule(body)
        d = authorize(subject, "use", "users")
        if not d.allowed: raise PermissionError(d.reason)
        uq = parse_users(rule, self.cat)
        cols = [str(c) for c in (body.get("columns") or [])]
        compile_export(uq, cols, self.cat, d)                                   # 先驗欄位
        if self.ch is None: raise Conflict("此環境未連接 ClickHouse")
        ce = compile_estimate(uq, self.cat, d)
        n = int(self._rows(ce.sql, ce.settings)[0]["users"])
        if n > MAX_EXPORT_ROWS:
            raise ValueError(f"預估 {n:,} 列超過匯出上限 {MAX_EXPORT_ROWS:,}；請縮小條件")
        e = {"id": "ex_" + uuid.uuid4().hex[:10], "kind": body.get("kind") or "users", "rule": {**rule, "project": uq.project}, "requester": subject.user_id, "purpose": purpose, "retention_days": keep,
             "rows_estimate": n, "rows": None, "columns": cols, "masked": {}, "status": "pending_approval", "approval_id": None, "watermark_id": None, "created_at": self._ts(),
             "approved_at": None, "expires_at": None, "generated_at": None, "downloaded_at": None, "note": "", "what": what}
        a = self.approvals.create("export", subject, f"匯出{what} {n:,} 列：{purpose}", {"purpose": purpose, "rows": n, "retention_days": keep, "columns": cols or "預設", "kind": e["kind"]}, {"kind": "export", "id": e["id"]})
        e["approval_id"] = a["id"]; self.store.put_item("exports", e)
        self.store.audit("export.requested", {"user": subject.user_id, "export": e["id"], "approval": a["id"], "module": "users", "rows": n, "purpose": purpose, "retention_days": keep})
        return e

    def _on_decided(self, a: dict[str, Any], approve: bool, subject: Subject) -> None:
        try: e = self.get(a["object"]["id"])
        except KeyError: return
        if approve:
            e.update({"status": "approved", "approved_at": self._ts(), "expires_at": (self.now() + timedelta(days=EXPORT_WINDOW_DAYS)).isoformat(timespec="seconds"), "note": a.get("note", "")})
        else:
            e.update({"status": "rejected" if a["status"] == "rejected" else "expired", "note": a.get("note", "")})
        self.store.put_item("exports", e)

    def run(self, id_: str, subject: Subject) -> dict[str, Any]:
        e = self.get(id_)
        if e["requester"] != subject.user_id: raise PermissionError("只有申請者本人能產檔")
        if e["status"] not in ("approved", "generated"): raise Conflict(f"匯出未核可或已過期（{e['status']}）")
        if self.ch is None: raise Conflict("此環境未連接 ClickHouse")
        subject.approvals = set(subject.approvals) | {e["approval_id"]}
        d = authorize(subject, "export", "users", {"export": True, "approval_id": e["approval_id"]})
        if not d.allowed: raise PermissionError(d.reason)
        uq = parse_users(e["rule"], self.cat); c = compile_export(uq, e["columns"], self.cat, d, MAX_EXPORT_ROWS)
        rows = self._rows(c.sql, c.settings)
        wm = hashlib.sha256(f"{e['id']}|{subject.user_id}|{self._ts()}".encode()).hexdigest()[:12]
        cols = [x for x in c.columns if x not in ("_s", "_u")]
        buf = io.StringIO()
        buf.write(f"# AltaTech DMP 匯出 · 申請單 {e['approval_id']} · 匯出單 {e['id']} · {subject.user_id} · {self._ts()} · 浮水印 {wm} · 用途：{e['purpose']} · 保存 {e['retention_days']} 天\n")
        w = csv.writer(buf); w.writerow(cols + ["_watermark"])
        for r in rows:
            w.writerow(["" if r.get(k) is None else r.get(k) for k in cols] + [wm])
        (self.dir / f"{e['id']}.csv").write_text(buf.getvalue(), encoding="utf-8-sig")
        e.update({"status": "generated", "rows": len(rows), "masked": c.masked, "columns": cols, "watermark_id": wm, "generated_at": self._ts()})
        self.store.put_item("exports", e)
        self.store.audit("export.generated", {"user": subject.user_id, "export": e["id"], "approval_id": e["approval_id"], "module": "users", "rows": len(rows), "watermark_id": wm, "masked": list(c.masked), "sql_hash": hashlib.sha256(c.sql.encode()).hexdigest()[:16]})
        return e

    def download(self, id_: str, subject: Subject) -> tuple[Path, str]:
        e = self.get(id_)
        if e["requester"] != subject.user_id: raise PermissionError("只有申請者本人能下載")
        if e["status"] not in ("generated", "downloaded"): raise Conflict(f"尚未產檔或已過期（{e['status']}）")
        path = self.dir / f"{e['id']}.csv"
        if not path.exists(): raise Conflict("檔案不存在，請重新產檔")
        e.update({"status": "downloaded", "downloaded_at": self._ts()}); self.store.put_item("exports", e)
        self.store.audit("export.downloaded", {"user": subject.user_id, "export": e["id"], "approval_id": e["approval_id"], "module": "users", "rows": e["rows"], "watermark_id": e["watermark_id"]})
        return path, f"dmp-export-{e['id']}.csv"

    def _rows(self, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
        import json
        txt = self.ch.query(sql + " FORMAT JSONEachRow", settings={**settings, "output_format_json_quote_64bit_integers": 0})
        return [json.loads(line) for line in txt.splitlines() if line.strip()]
