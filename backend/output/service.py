"""資料輸出任務服務（openapi /v1/exports/tasks*；docs/06 §8）。

- 任務 = 類型（realtime／once／daily）× 輸出內容（events／users／segment_members）× 目的地（推送的 Destination：sftp／s3／gcs 給批量；kafka／webhook 給實時）× 格式 × 排程。
- 治理沿用匯出：建立即開審批（type=output_task → 數位經營；目的地第一次用另開 destination_first_use → 系統管理員，兩道都核可才生效）、
  L3／L4 拒絕、L2 區間化、浮水印欄 _wm、單次列數上限。用戶寬表／分群名單在內部以原始 one_id 代碼化（customer_code 與事件明細一致），原始值不輸出。
- 期間口徑：事件依 event_date；用戶寬表依 updated_at（once 期間、daily 當日分區）；分群名單＝最新版本完整快照，不套期間。
- 執行：先重驗目的地（啟用中、首次使用已核可且設定未變）與擁有者「現在」的有效角色（排程用統一的指派疊加：deny／到期；找不到身分就不跑）
  → 查明細 → 寫 csv／jsonl（可 gzip、按列數分檔）+ manifest.json（檔案、列數、sha256、浮水印、申請單）→ 交給目的地傳輸（路徑只能落在目的地目錄內）→ 執行紀錄。
- 每日例行：到 schedule.at 跑前一天分區，暫停不補跑；到 end_date 後 ended。realtime 第一版只接受建立與審批，單條寫入由事件收集端接（docs/06 §8）。
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import replace as _replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..activation.service import customer_code
from ..approvals.engine import ApprovalService, Conflict
from ..auth.mapping import overlay_assignments
from ..security.policy import Subject, authorize, has_grant
from ..semantic.compiler.catalog import Catalog
from ..semantic.compiler.output import check_columns, compile_event_rows, count_event_rows
from ..semantic.compiler.users import compile_estimate, compile_export, parse_users
from .transport import OutboxUploader, Uploader, safe_relpath

TYPES = ("realtime", "once", "daily")
KINDS = ("events", "users", "segment_members")
FORMATS = ("csv", "jsonl", "parquet")
COMPRESSIONS = ("none", "gzip", "zstd")
BATCH_DEST = ("sftp", "s3", "gcs")
REALTIME_DEST = ("kafka", "webhook")
MAX_OUTPUT_ROWS = 10_000_000
PREVIEW_ROWS = 20
MAX_EVENTS = 300
TZ = ZoneInfo("Asia/Taipei")


class OutputService:
    def __init__(self, store: Any, ch: Any | None, approvals: ApprovalService, out_dir: Path, cat: Catalog | None = None, now: Callable[[], datetime] | None = None,
                 members_rule: Callable[[str, str], tuple[dict[str, Any], dict[str, Any]]] | None = None, uploader: Uploader | None = None, code_key: bytes | None = None,
                 max_rows: int = MAX_OUTPUT_ROWS, roles_of: Callable[[str], list[str] | None] | None = None):
        self.store, self.ch, self.approvals, self.cat = store, ch, approvals, cat or Catalog()
        self.roles_of = roles_of                 # user_id → 現在的有效角色（登入同一套：IdP／本機帳號 + 指派疊加）；None＝找不到身分；沒注入時只用指派
        self.dir = Path(out_dir); self.dir.mkdir(parents=True, exist_ok=True)
        self.now = now or approvals.now
        self.members_rule = members_rule
        self.uploader: Uploader = uploader or OutboxUploader(self.dir / "outbox")
        self.code_key = code_key or b"dev-only-customer-code-key"
        self.max_rows = max_rows
        approvals.hooks["output_task"] = self._on_decided       # output_task 與 destination_first_use 兩種審批單都掛在 object.kind=output_task

    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ---------------------------------------------------------------- 讀
    def get(self, id_: str) -> dict[str, Any]:
        for t in self.store.items("output_task"):
            if t["id"] == id_:
                self._expire(t); return t
        raise KeyError(id_)

    def _expire(self, t: dict[str, Any]) -> None:
        end = ((t.get("schedule") or {}).get("end_date"))
        if t["type"] == "daily" and t["status"] in ("approved", "active") and end and date.fromisoformat(end) < self.now().date():
            t["status"] = "ended"; t["updated_at"] = self._ts(); self.store.put_item("output_task", t)

    def _visible(self, t: dict[str, Any], subject: Subject) -> bool:
        return "admin" in subject.roles or t["owner"] == subject.user_id or bool(set(t.get("shared_roles") or []) & set(subject.roles)) or "digital_ops" in subject.roles

    def list(self, subject: Subject, status: str | None = None, type_: str | None = None) -> list[dict[str, Any]]:
        out = []
        for t in self.store.items("output_task"):
            self._expire(t)
            if t["status"] == "deleted" or not self._visible(t, subject): continue
            if status and t["status"] != status: continue
            if type_ and t["type"] != type_: continue
            out.append(self._public(t))
        return sorted(out, key=lambda t: t["created_at"], reverse=True)

    def detail(self, id_: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        if not self._visible(t, subject): raise PermissionError("不是你的任務，也沒分享給你的角色")
        return {**self._public(t), "runs": self.runs(id_, 20)}

    def _runs_desc(self, task_id: str) -> list[dict[str, Any]]:
        """同一秒內多次執行（暫停→恢復）也要最新在前：時間相同時以寫入順序決定。"""
        rs = [(i, r) for i, r in enumerate(self.store.items("output_run")) if r["task_id"] == task_id]
        return [r for _, r in sorted(rs, key=lambda x: (x[1]["started_at"], x[0]), reverse=True)]

    def _public(self, t: dict[str, Any]) -> dict[str, Any]:
        runs = self._runs_desc(t["id"])
        return {**t, "last_run": runs[0] if runs else None}

    def runs(self, id_: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._runs_desc(id_)[: max(1, min(int(limit), 200))]

    # ---------------------------------------------------------------- 驗證
    def _destination(self, id_: str) -> dict[str, Any]:
        for d in self.store.items("destination"):
            if d["id"] == id_: return d
        raise ValueError(f"目的地 {id_} 不存在")

    def _validate(self, body: dict[str, Any], subject: Subject, cur: dict[str, Any] | None = None) -> dict[str, Any]:
        x = dict(cur or {})
        name = str(body.get("name") or x.get("name") or "").strip()
        if not 1 <= len(name) <= 50: raise ValueError("name 需 1–50 字")
        t = str(body.get("type") or x.get("type") or "")
        if t not in TYPES: raise ValueError(f"type 只能是 {TYPES}")
        purpose = str(body.get("purpose") or x.get("purpose") or "").strip()
        if not 4 <= len(purpose) <= 200: raise ValueError("purpose 需 4–200 字")
        keep = int(body.get("retention_days") or x.get("retention_days") or 90)
        if not 1 <= keep <= 365: raise ValueError("retention_days 需在 1–365")
        content = dict(body.get("content") or x.get("content") or {})
        kind = str(content.get("kind") or "")
        if kind not in KINDS: raise ValueError(f"content.kind 只能是 {KINDS}")
        if t == "realtime" and kind != "events": raise ValueError("realtime 只接事件明細")
        events = [str(e) for e in (content.get("events") or [])]
        if len(events) > MAX_EVENTS: raise ValueError(f"事件清單最多 {MAX_EVENTS} 個")
        bad = [e for e in events if not self.cat.canonical_event(e)]
        if bad: raise ValueError(f"事件不在字典：{bad[:5]}")
        content["events"] = [self.cat.canonical_event(e) for e in events]
        content["columns"] = [str(c) for c in (content.get("columns") or [])]
        ident = str(content.get("identifier") or "one_id")
        if ident not in ("one_id", "customer_code"): raise ValueError("identifier 只能是 one_id／customer_code")
        content["identifier"] = ident
        if kind == "segment_members":
            if not content.get("segment_id"): raise ValueError("kind=segment_members 需要 segment_id")
            if not self.members_rule: raise ValueError("此環境沒有分群服務")
        if kind == "segment_members":
            content.pop("time", None)                                            # 分群名單＝最新版本完整快照，不套期間（契約 OutputContent.time）
        elif t == "once":
            tm = content.get("time") or {}
            try: s, e = date.fromisoformat(str(tm.get("from"))), date.fromisoformat(str(tm.get("to")))
            except (TypeError, ValueError): raise ValueError("once 需要 content.time.from／to（YYYY-MM-DD）") from None
            if e < s: raise ValueError("content.time.to 早於 from")
            if (e - s).days > 366: raise ValueError("once 期間最長 366 天")
            content["time"] = {"from": s.isoformat(), "to": e.isoformat()}
        # 目的地與任務類型
        dest = self._destination(str(body.get("destination_id") or x.get("destination_id") or ""))
        if dest["status"] != "active": raise ValueError(f"目的地 {dest['name']} 未啟用")
        if t == "realtime" and dest["type"] not in REALTIME_DEST: raise ValueError(f"realtime 只接 {REALTIME_DEST} 目的地")
        if t != "realtime" and dest["type"] not in BATCH_DEST: raise ValueError(f"批量輸出只接 {BATCH_DEST} 目的地")
        # 格式
        fmt = {**{"type": "csv", "compression": "gzip", "partition": "day", "file_pattern": "{task}/{date}/{task}_{date}_{seq}.{ext}", "max_rows_per_file": 1_000_000, "header": True, "manifest": True},
               **(x.get("format") or {}), **(body.get("format") or {})}
        if fmt["type"] not in FORMATS: raise ValueError(f"format.type 只能是 {FORMATS}")
        if fmt["compression"] not in COMPRESSIONS: raise ValueError(f"format.compression 只能是 {COMPRESSIONS}")
        if t == "realtime": fmt["type"] = "jsonl"; fmt["compression"] = "none"
        if not 10_000 <= int(fmt["max_rows_per_file"]) <= 5_000_000: raise ValueError("max_rows_per_file 需在 1 萬–500 萬")
        if not re.fullmatch(r"[A-Za-z0-9_\-/{}.]+", str(fmt["file_pattern"])) or "{seq}" not in str(fmt["file_pattern"]): raise ValueError("file_pattern 只能含英數 _ - / { } . 且要有 {seq}")
        try: fmt["file_pattern"] = safe_relpath(str(fmt["file_pattern"]), "file_pattern")             # 不能是絕對路徑、不能含 ..（出件匣／遠端目錄外不得寫檔）
        except ValueError as e: raise ValueError(str(e)) from None
        if not re.fullmatch(r"[A-Za-z0-9_\-/{}.]+", str(fmt["file_pattern"]).format(task="t", date="d", seq="1", ext="csv")): raise ValueError("file_pattern 只能用變數 {task}／{date}／{seq}／{ext}")
        # 排程
        sch = {**(x.get("schedule") or {}), **(body.get("schedule") or {})}
        if t == "daily":
            at = str(sch.get("at") or "02:00")
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at): raise ValueError("schedule.at 需為 HH:MM")
            sch["at"] = at
            if sch.get("end_date"):
                try: date.fromisoformat(str(sch["end_date"]))
                except ValueError: raise ValueError("schedule.end_date 需為 YYYY-MM-DD") from None
        roles = [str(r) for r in (body.get("shared_roles") if body.get("shared_roles") is not None else x.get("shared_roles") or [])]
        # 欄位先驗（依申請者的匯出口徑）
        d = self._decision(subject, kind, export=False)
        cols, rejected = self._columns(kind, content, d)
        if rejected: raise ValueError(f"欄位不可輸出（L3／L4 或不可見）：{rejected}")
        if ident == "one_id" and d.field_policies.get("one_id", "mask_tail(4)") != "plain":
            raise ValueError("one_id 明文輸出只有系統管理員可申請；請改用 customer_code（代碼化）")
        x.update({"name": name, "type": t, "purpose": purpose, "retention_days": keep, "content": content, "destination_id": dest["id"], "format": fmt, "schedule": sch, "shared_roles": roles,
                  "masked": {c["name"]: c["policy"] for c in cols if c["policy"] not in ("plain", "rejected")}})
        return x

    def _need_grant(self, subject: Subject, kind: str) -> None:
        if not has_grant(subject, "view", "integration"): raise PermissionError("需要數據整合權限（integration.view）")
        if kind == "events" and not has_grant(subject, "export", "events"): raise PermissionError("輸出事件明細需要 events.export")
        if kind in ("users", "segment_members") and not has_grant(subject, "export", "users"): raise PermissionError("輸出用戶資料需要 users.export")

    def _decision(self, subject: Subject, kind: str, export: bool, approval_id: str | None = None) -> Any:
        module = "events" if kind == "events" else "users"
        if export:
            subject.approvals = set(subject.approvals) | {approval_id or ""}
            d = authorize(subject, "export", module, {"export": True, "approval_id": approval_id})
        else:
            d = authorize(subject, "use", module, {"export": True})
        if not d.allowed: raise PermissionError(d.reason)
        return d

    def _columns(self, kind: str, content: dict[str, Any], decision: Any) -> tuple[list[dict[str, Any]], list[str]]:
        if kind == "events":
            from ..semantic.compiler.output import DEFAULT_EVENT_COLS
            return check_columns(content.get("columns") or DEFAULT_EVENT_COLS, self.cat, decision)
        cols = [f"user.{c}" if not c.startswith("user.") else c for c in (content.get("columns") or [])]
        plan, rejected = check_columns(cols, self.cat, decision)
        return [{**p, "name": p["name"].split(".", 1)[1]} for p in plan], [r.split(".", 1)[1] for r in rejected]

    # ---------------------------------------------------------------- 編譯
    def _rule(self, t: dict[str, Any]) -> dict[str, Any]:
        c = t["content"]
        if c["kind"] == "segment_members":
            rule, _ = self.members_rule(str(c["segment_id"]), "latest")      # type: ignore[misc]
            return rule
        rule: dict[str, Any] = {"filters": c.get("filters") or []}
        if not rule["filters"]: rule = {"filters": [{"field": "user.lifecycle", "op": "set"}]}       # 用戶列表至少要一個條件：全體 = 有生命週期值
        return rule

    @staticmethod
    def _internal(decision: Any) -> Any:
        """內部查詢用的決策：one_id 取原始值（之後由 _public_row 代碼化／換識別子，原始值不輸出）；其餘欄位策略與行級範圍不變。"""
        fp = dict(getattr(decision, "field_policies", {}) or {}); fp["one_id"] = "plain"
        return _replace(decision, field_policies=fp)

    def _window(self, t: dict[str, Any], partition: date | None) -> tuple[date, date] | None:
        """用戶寬表的期間（updated_at，台北日）：daily＝分區當天；once＝content.time。分群名單＝完整快照，無期間。"""
        c = t["content"]
        if c["kind"] != "users": return None
        if partition is not None: return (partition, partition)
        tm = c.get("time") or {}
        return (date.fromisoformat(tm["from"]), date.fromisoformat(tm["to"]))

    def _users_query(self, t: dict[str, Any], partition: date | None) -> Any:
        uq = parse_users({**self._rule(t), "project": t["project"]}, self.cat)
        win = self._window(t, partition)
        return _replace(uq, window=win) if win else uq

    def _compile(self, t: dict[str, Any], decision: Any, partition: date | None, limit: int) -> Any:
        c = t["content"]
        if c["kind"] == "events":
            if partition is not None: s = e = partition
            else: s, e = date.fromisoformat(c["time"]["from"]), date.fromisoformat(c["time"]["to"])
            return compile_event_rows(t["project"], s, e, c.get("events") or [], c.get("columns") or [], c.get("filters"), self.cat, decision, limit)
        return compile_export(self._users_query(t, partition), [x.split(".", 1)[1] if x.startswith("user.") else x for x in (c.get("columns") or [])], self.cat, self._internal(decision), limit)

    def _rows(self, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
        txt = self.ch.query(sql + " FORMAT JSONEachRow", settings={**settings, "output_format_json_quote_64bit_integers": 0})
        return [json.loads(line) for line in txt.splitlines() if line.strip()]

    def _estimate(self, t: dict[str, Any], decision: Any, partition: date | None) -> int:
        c = t["content"]
        if c["kind"] == "events":
            comp = self._compile(t, decision, partition, 1)
            return int(self._rows(count_event_rows(comp), comp.settings)[0]["n"])
        ce = compile_estimate(self._users_query(t, partition), self.cat, decision)
        return int(self._rows(ce.sql, ce.settings)[0]["users"])

    # ---------------------------------------------------------------- 建立／預覽／更新
    def preview(self, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        kind = str((body.get("content") or {}).get("kind") or "")
        self._need_grant(subject, kind if kind in KINDS else "events")
        t = self._validate(body, subject); t["project"] = str(body.get("project") or "altatech_dmp")
        d = self._decision(subject, kind, export=False)
        plan, rejected = self._columns(kind, t["content"], d)
        warnings: list[str] = []
        if kind == "segment_members": warnings.append("分群名單輸出最新版本的完整快照，不套期間")
        elif kind == "users": warnings.append("用戶寬表依 updated_at 取期間：daily＝分區當天有更新的用戶；once＝期間內有更新的用戶")
        if self.ch is None: raise Conflict("此環境未連接 ClickHouse")
        partition = (self.now().date() - timedelta(days=1)) if t["type"] == "daily" else None
        n = self._estimate(t, d, partition)
        if n > self.max_rows: warnings.append(f"預估 {n:,} 列超過單次上限 {self.max_rows:,}；請縮小事件清單或期間")
        comp = self._compile(t, d, partition, PREVIEW_ROWS)
        sample = [self._public_row(r, t) for r in self._rows(comp.sql, comp.settings)]
        self.store.audit("output_task.previewed", {"user": subject.user_id, "kind": kind, "rows_estimate": n, "columns": [c["name"] for c in plan]})
        return {"columns": [{**c, "cn": self._cn(c["name"])} for c in plan], "rejected": rejected, "rows_estimate": n, "scan_rows_estimate": n, "sample": sample, "warnings": warnings + comp.warnings,
                "partition_date": partition.isoformat() if partition else None}

    def _cn(self, name: str) -> str:
        base = name.split(".", 1)[1] if name.startswith("user.") else name
        up = self.cat.user_props.get(base) if hasattr(self.cat, "user_props") else None
        return str((up or {}).get("cn") or name)

    def create(self, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        kind = str((body.get("content") or {}).get("kind") or "")
        self._need_grant(subject, kind if kind in KINDS else "events")
        t = self._validate(body, subject)
        t.update({"id": "ot_" + uuid.uuid4().hex[:10], "project": str(body.get("project") or "altatech_dmp"), "status": "pending_approval", "approval_id": None, "owner": subject.user_id,
                  "created_at": self._ts(), "updated_at": self._ts(), "rows_estimate": None, "note": ""})
        if self.ch is not None:
            d = self._decision(subject, kind, export=False)
            n = self._estimate(t, d, (self.now().date() - timedelta(days=1)) if t["type"] == "daily" else None)
            if n > self.max_rows: raise ValueError(f"預估 {n:,} 列超過單次上限 {self.max_rows:,}；請縮小事件清單或期間")
            t["rows_estimate"] = n
        dest = self._destination(t["destination_id"])
        t["destination_approval_id"] = None; t["destination_approved"] = None
        self._request_approvals(t, subject, dest, kind)
        self.store.put_item("output_task", t)
        self.store.audit("output_task.created", {"user": subject.user_id, "task": t["id"], "approval": t["approval_id"], "destination_approval": t.get("destination_approval_id"), "type": t["type"], "kind": kind,
                                                 "destination": dest["id"], "rows_estimate": t["rows_estimate"], "purpose": t["purpose"]})
        return self._public(t)

    @staticmethod
    def _dest_version(dest: dict[str, Any]) -> str:
        """目的地設定摘要（type／url／secret_ref／config 的 sha256 前 16 碼）：核准綁的是這個，不是 updated_at（秒級會碰撞，也看不出內容有沒有變）。"""
        body = {"type": dest.get("type"), "url": dest.get("url"), "secret_ref": dest.get("secret_ref"), "config": dest.get("config") or {}}
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]

    def _dest_needs_admin(self, t: dict[str, Any], dest: dict[str, Any]) -> bool:
        """目的地首次使用（還沒成功送過）且這個任務沒有「對應這個目的地、這個設定版本」的系統管理員核可 → 需要第二道審批。"""
        if dest.get("first_push_at"): return False
        da = t.get("destination_approved") or {}
        return da.get("destination") != dest.get("id") or da.get("config_version") != self._dest_version(dest)

    def _request_approvals(self, t: dict[str, Any], subject: Subject, dest: dict[str, Any], kind: str, changed: list[str] | None = None) -> None:
        """開審批單：資料面 output_task（數位經營）＋（目的地首次使用時）destination_first_use（系統管理員）。兩道都核可才生效。"""
        what = {"events": "事件明細", "users": "用戶寬表", "segment_members": "分群名單"}[kind]
        first = self._dest_needs_admin(t, dest)
        title = f"資料輸出 {t['name']}" + (f" 變更（{'、'.join(changed)}）" if changed else f"（{t['type']}／{what} → {dest['name']}）")
        a = self.approvals.create("output_task", subject, f"{title}：{t['purpose']}" + ("（目的地首次使用，另需系統管理員核可）" if first else ""),
                                  {"purpose": t["purpose"], "type": t["type"], "kind": kind, "destination": dest["name"], "destination_type": dest["type"], "rows_estimate": t.get("rows_estimate"),
                                   "columns": t["content"]["columns"] or "預設", "events": t["content"].get("events") or "全部", "retention_days": t["retention_days"], "first_use_of_destination": first,
                                   "changed": changed or []}, {"kind": "output_task", "id": t["id"]})
        t["approval_id"] = a["id"]; t["pending_approvals"] = [a["id"]]; t["status"] = "pending_approval"
        if first:
            d = self.approvals.create("destination_first_use", subject, f"目的地首次使用 {dest['name']}（{dest['type']}）：資料輸出 {t['name']} 要送到這裡",
                                      {"destination_id": dest["id"], "destination": dest["name"], "destination_type": dest["type"], "config": {k: v for k, v in (dest.get("config") or {}).items()}, "config_version": self._dest_version(dest),
                                       "task": t["id"], "purpose": t["purpose"]}, {"kind": "output_task", "id": t["id"]})
            t["destination_approval_id"] = d["id"]; t["pending_approvals"].append(d["id"])

    def _on_decided(self, a: dict[str, Any], approve: bool, subject: Subject) -> None:
        try: t = self.get(a["object"]["id"])
        except KeyError: return
        if not approve:
            t.update({"status": "rejected", "note": a.get("note", ""), "pending_approvals": []})
            self.approvals.close_for_object("output_task", t["id"], False, subject, "另一道審批已退回")       # 另一張待審的一起關掉
            t["updated_at"] = self._ts(); self.store.put_item("output_task", t)
            self.store.audit("output_task.rejected", {"user": subject.user_id, "task": t["id"], "approval": a["id"], "approval_type": a["type"], "note": a.get("note", "")})
            return
        if a["type"] == "destination_first_use":
            t["destination_approved"] = {"by": subject.user_id, "at": self._ts(), "approval": a["id"], "destination": a["detail"].get("destination_id"), "config_version": a["detail"].get("config_version")}
        pending = [p for p in (t.get("pending_approvals") or []) if p != a["id"]]
        t["pending_approvals"] = pending; t["updated_at"] = self._ts()
        if pending:                                                                   # 還有另一道沒過：維持待審批
            self.store.put_item("output_task", t)
            self.store.audit("output_task.approval_step", {"user": subject.user_id, "task": t["id"], "approval": a["id"], "approval_type": a["type"], "waiting": pending})
            return
        t.update({"status": "active" if t["type"] in ("daily", "realtime") else "approved", "note": a.get("note", "")})
        self.store.put_item("output_task", t)
        self.store.audit("output_task.approved", {"user": subject.user_id, "task": t["id"], "approval": a["id"], "approval_type": a["type"], "note": a.get("note", "")})

    def update(self, id_: str, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        cur = self.get(id_)
        if cur["owner"] != subject.user_id and "admin" not in subject.roles: raise PermissionError("只有建立者能編輯")
        if cur["status"] in ("deleted",): raise Conflict("任務已刪除")
        self._need_grant(subject, cur["content"]["kind"])
        t = self._validate({**body, "type": body.get("type") or cur["type"]}, subject, cur)
        changed = sorted(k for k in ("content", "destination_id", "type") if t.get(k) != cur.get(k))
        t["updated_at"] = self._ts()
        if changed:                                                         # 改內容或目的地視同新任務：重走審批（換目的地且首次使用 → 也要系統管理員）
            if cur["status"] == "pending_approval":                         # 待審期間改：審批者看到的已不是要執行的設定 → 既有待審單全部作廢，重開
                self.approvals.close_for_object("output_task", id_, False, subject, "送審內容變更（" + "、".join(changed) + "），重新送審")
                self.store.audit("output_task.approvals_reset", {"user": subject.user_id, "task": id_, "changed": changed})
            if "destination_id" in changed: t["destination_approved"] = None; t["destination_approval_id"] = None
            self._request_approvals(t, subject, self._destination(t["destination_id"]), t["content"]["kind"], changed)
        self.store.put_item("output_task", t)
        self.store.audit("output_task.updated", {"user": subject.user_id, "task": id_, "changed": sorted(k for k in t if t[k] != cur.get(k))})
        return self._public(t)

    def delete(self, id_: str, subject: Subject) -> None:
        t = self.get(id_)
        if t["owner"] != subject.user_id and "admin" not in subject.roles: raise PermissionError("只有建立者能刪除")
        t["status"] = "deleted"; t["updated_at"] = self._ts(); self.store.put_item("output_task", t)
        self.store.audit("output_task.deleted", {"user": subject.user_id, "task": id_})

    def pause(self, id_: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        if t["owner"] != subject.user_id and "admin" not in subject.roles: raise PermissionError("只有建立者能暫停／恢復")
        if t["status"] == "active": t["status"] = "paused"
        elif t["status"] == "paused": t["status"] = "active"
        else: raise Conflict(f"狀態 {t['status']} 不能暫停／恢復")
        t["updated_at"] = self._ts(); self.store.put_item("output_task", t)
        self.store.audit("output_task.paused" if t["status"] == "paused" else "output_task.resumed", {"user": subject.user_id, "task": id_})
        return self._public(t)

    # ---------------------------------------------------------------- 執行
    def _public_row(self, r: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
        """內部 _one_id → 識別子欄；其餘照查出的（已遮罩）值。"""
        out: dict[str, Any] = {}
        oid = r.get("_one_id") if "_one_id" in r else r.get("one_id")
        ident = t["content"]["identifier"]
        if oid is not None:
            out[ident] = oid if ident == "one_id" else customer_code(str(oid), self.code_key)
        for k, v in r.items():
            if k in ("_one_id", "one_id", "_s", "_u"): continue
            out[k] = v
        return out

    def run(self, id_: str, subject: Subject, partition_date: str | None = None, trigger: str = "manual") -> dict[str, Any]:
        t = self.get(id_)
        if t["owner"] != subject.user_id and "admin" not in subject.roles and trigger == "manual": raise PermissionError("只有建立者能執行")
        if t["status"] not in ("approved", "active"): raise Conflict(f"任務未核可、已暫停或已結束（{t['status']}）")
        if t["type"] == "realtime": raise Conflict("realtime 任務由事件收集端單條寫入，不手動執行")
        if self.ch is None: raise Conflict("此環境未連接 ClickHouse")
        partition = None
        if t["type"] == "daily":
            partition = date.fromisoformat(partition_date) if partition_date else self.now().date() - timedelta(days=1)
        kind = t["content"]["kind"]
        run = {"id": "or_" + uuid.uuid4().hex[:10], "task_id": t["id"], "trigger": trigger, "partition_date": partition.isoformat() if partition else None, "started_at": self._ts(), "finished_at": None,
               "status": "running", "rows": None, "bytes": None, "files": [], "delivery_ids": [], "error": None, "attempts": 1, "watermark": None}
        self.store.put_item("output_run", run)
        dest = self._destination(t["destination_id"])
        if dest["status"] != "active":                                                      # 目的地暫停／停用：不查、不寫、不送；紀錄原因
            run.update({"status": "skipped", "skip_reason": "destination_paused", "finished_at": self._ts(), "error": f"目的地 {dest['name']} 已暫停，未產檔"}); self.store.put_item("output_run", run)
            self.store.audit("output_task.run_skipped", {"user": subject.user_id, "task": t["id"], "run": run["id"], "trigger": trigger, "destination": dest["id"], "reason": "destination_paused"})
            raise Conflict(run["error"])
        if self._dest_needs_admin(t, dest):                                                  # 目的地設定變了（或從未核可）：擋下並重開系統管理員審批
            run.update({"status": "skipped", "skip_reason": "destination_not_approved", "finished_at": self._ts(), "error": f"目的地 {dest['name']} 的設定已變更或尚未經系統管理員核可，需重新核可"}); self.store.put_item("output_run", run)
            self._reopen_destination_approval(t, dest, subject)
            self.store.audit("output_task.run_skipped", {"user": subject.user_id, "task": t["id"], "run": run["id"], "trigger": trigger, "destination": dest["id"], "reason": "destination_not_approved"})
            raise Conflict(run["error"])
        try:
            if subject.user_id == t["owner"]: owner = Subject(t["owner"], list(subject.roles), branches=list(subject.branches))
            else:
                roles = self._owner_roles(t["owner"])
                if roles is None: raise PermissionError(f"找不到任務擁有者 {t['owner']} 的有效身分，不以其權限執行")
                owner = Subject(t["owner"], roles)
            d = self._decision(owner, kind, export=True, approval_id=t["approval_id"])
            if t["content"]["identifier"] == "one_id" and d.field_policies.get("one_id", "mask_tail(4)") != "plain":   # 建立時的檢查不能代替執行時：擁有者降權就停，不靜默擴權
                t.update({"status": "paused", "note": "執行身分已無 one_id 明文權限，任務暫停；請改用 customer_code 或由系統管理員重新建立", "updated_at": self._ts()}); self.store.put_item("output_task", t)
                self.store.audit("output_task.paused", {"user": subject.user_id, "task": t["id"], "reason": "one_id_plain_revoked", "roles": list(owner.roles)})
                raise PermissionError(t["note"])
            comp = self._compile(t, d, partition, self.max_rows + 1)
            rows = [self._public_row(r, t) for r in self._rows(comp.sql, comp.settings)]
            if len(rows) > self.max_rows: raise Conflict(f"列數超過單次上限 {self.max_rows:,}")
            wm = hashlib.sha256(f"{t['id']}|{run['id']}|{t['owner']}|{self._ts()}".encode()).hexdigest()[:12]
            files = self._write(t, run, rows, wm, partition)
            remote = self.uploader.upload(dest, [(Path(f["local"]), f["path"]) for f in files]) if files else []
            run.update({"status": "ok", "finished_at": self._ts(), "rows": len(rows), "bytes": sum(f["bytes"] for f in files), "files": [{k: v for k, v in f.items() if k != "local"} for f in files], "watermark": wm,
                        "remote": remote})
            if not rows: run["status"] = "skipped"; run["skip_reason"] = "no_data"; run["error"] = "分區沒有資料"
            if not dest.get("first_push_at"):
                dest["first_push_at"] = self._ts(); self.store.put_item("destination", dest)
            if t["type"] == "once":
                t["status"] = "ended"; t["updated_at"] = self._ts(); self.store.put_item("output_task", t)
            self.store.audit("output_task.run", {"user": subject.user_id, "task": t["id"], "run": run["id"], "trigger": trigger, "partition_date": run["partition_date"], "rows": len(rows), "files": len(files),
                                                 "destination": dest["id"], "watermark": wm, "masked": sorted(t.get("masked") or {}), "sql_hash": hashlib.sha256(comp.sql.encode()).hexdigest()[:16]})
        except Exception as e:
            run.update({"status": "failed", "finished_at": self._ts(), "error": str(e)[:300]})
            self.store.put_item("output_run", run)
            self.store.audit("output_task.run_failed", {"user": subject.user_id, "task": t["id"], "run": run["id"], "error": str(e)[:300]})
            if isinstance(e, (Conflict, PermissionError, ValueError)): raise
            raise Conflict(f"執行失敗：{e}") from e
        self.store.put_item("output_run", run)
        return run

    def _owner_roles(self, user_id: str) -> list[str] | None:
        """擁有者「現在」的有效角色（排程、非本人手動執行用）。注入 roles_of（登入同一套：IdP／本機帳號 + 指派疊加）就用它；
        沒注入時只看本機指派（deny／到期照算），完全沒有指派 → None（不預設任何角色）。"""
        if self.roles_of is not None:
            return self.roles_of(user_id)
        mine = [a for a in self.store.assignments() if a.get("user_id") == user_id]
        if not mine: return None
        roles, _, _ = overlay_assignments((user_id,), [], mine, self.now().timestamp())
        return roles

    def _reopen_destination_approval(self, t: dict[str, Any], dest: dict[str, Any], subject: Subject) -> None:
        """目的地設定變更後第一次執行：任務回待審批，重開 destination_first_use（已有待審的就不重複開）。"""
        for a in self.store.items("approvals"):
            if a["object"] == {"kind": "output_task", "id": t["id"]} and a["type"] == "destination_first_use" and a["status"] == "pending": return
        d = self.approvals.create("destination_first_use", Subject(t["owner"], []), f"目的地設定變更後重新核可 {dest['name']}（{dest['type']}）：資料輸出 {t['name']}",
                                  {"destination_id": dest["id"], "destination": dest["name"], "destination_type": dest["type"], "config": dict(dest.get("config") or {}), "config_version": self._dest_version(dest), "task": t["id"], "purpose": t["purpose"]},
                                  {"kind": "output_task", "id": t["id"]})
        t.update({"status": "pending_approval", "destination_approval_id": d["id"], "destination_approved": None, "pending_approvals": [d["id"]], "updated_at": self._ts()}); self.store.put_item("output_task", t)
        self.store.audit("output_task.reapproval", {"user": subject.user_id, "task": t["id"], "approval": d["id"], "reason": "destination_config_changed"})

    def _write(self, t: dict[str, Any], run: dict[str, Any], rows: list[dict[str, Any]], wm: str, partition: date | None) -> list[dict[str, Any]]:
        fmt = t["format"]; ext = {"csv": "csv", "jsonl": "jsonl", "parquet": "parquet"}[fmt["type"]]
        if fmt["type"] == "parquet": raise Conflict("parquet 寫出需執行節點的 Arrow 元件；單機版先用 csv／jsonl")
        if fmt["compression"] == "zstd": raise Conflict("zstd 壓縮需執行節點元件；單機版先用 gzip")
        gz = fmt["compression"] == "gzip"
        day = partition.isoformat() if partition else str((t["content"].get("time") or {}).get("to") or self.now().date().isoformat())   # 一次性任務用資料區間的結束日命名
        cols = list(rows[0].keys()) if rows else []
        out: list[dict[str, Any]] = []
        local_dir = self.dir / t["id"] / run["id"]; local_dir.mkdir(parents=True, exist_ok=True)
        per = int(fmt["max_rows_per_file"])
        chunks = [rows[i:i + per] for i in range(0, len(rows), per)] or [[]]
        for seq, chunk in enumerate(chunks, 1):
            rel = str(fmt["file_pattern"]).format(task=t["id"], date=day, seq=f"{seq:04d}", ext=ext) + (".gz" if gz else "")
            buf = io.StringIO()
            if fmt["type"] == "csv":
                w = csv.writer(buf)
                if fmt.get("header", True): w.writerow(cols + ["_wm"])
                for r in chunk: w.writerow(["" if r.get(k) is None else r.get(k) for k in cols] + [wm])
            else:
                for r in chunk: buf.write(json.dumps({**r, "_wm": wm}, ensure_ascii=False, default=str) + "\n")
            data = buf.getvalue().encode("utf-8")
            if gz: data = gzip.compress(data)
            local = local_dir / Path(rel).name; local.write_bytes(data)
            out.append({"path": rel, "rows": len(chunk), "bytes": len(data), "checksum": hashlib.sha256(data).hexdigest(), "local": str(local)})
        if fmt.get("manifest", True):
            man = {"task": t["id"], "run": run["id"], "name": t["name"], "approval": t["approval_id"], "owner": t["owner"], "generated_at": self._ts(), "partition_date": day if partition else None,
                   "purpose": t["purpose"], "retention_days": t["retention_days"], "watermark": wm, "masked": t.get("masked") or {}, "rows": len(rows), "columns": cols + ["_wm"],
                   "files": [{k: v for k, v in f.items() if k != "local"} for f in out]}
            data = json.dumps(man, ensure_ascii=False, indent=1).encode("utf-8")
            local = local_dir / "manifest.json"; local.write_bytes(data)
            out.append({"path": str(Path(out[0]["path"]).parent / "manifest.json") if out else f"{t['id']}/{day}/manifest.json", "rows": 0, "bytes": len(data), "checksum": hashlib.sha256(data).hexdigest(), "local": str(local)})
        return out

    # ---------------------------------------------------------------- 例行
    def tick(self, subject: Subject, now: datetime | None = None) -> dict[str, Any]:
        """每分鐘：daily 任務到 schedule.at 就跑前一天分區（當天已跑過就略過）；到 end_date 後 ended。"""
        now = now or self.now(); out: dict[str, Any] = {"ran": [], "ended": []}
        yday = (now.date() - timedelta(days=1)).isoformat()
        for t in list(self.store.items("output_task")):
            before = t["status"]; self._expire(t)
            if before != t["status"] == "ended": out["ended"].append(t["id"]); continue
            if t["type"] != "daily" or t["status"] != "active": continue
            hh, mm = (int(x) for x in (t.get("schedule") or {}).get("at", "02:00").split(":"))
            if (now.hour, now.minute) < (hh, mm): continue
            if any(r["task_id"] == t["id"] and r["partition_date"] == yday and (r["status"] in ("ok", "running") or r.get("skip_reason") == "no_data") for r in self.store.items("output_run")): continue   # 因目的地暫停／待核可而略過的分區，恢復後要補跑
            try:
                r = self.run(t["id"], subject, yday, trigger="schedule"); out["ran"].append({"task": t["id"], "run": r["id"], "status": r["status"], "rows": r["rows"]})
            except (Conflict, PermissionError, ValueError) as e:
                out["ran"].append({"task": t["id"], "error": str(e)[:200]})
        return out
