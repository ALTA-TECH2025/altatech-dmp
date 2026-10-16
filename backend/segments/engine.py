"""分群引擎最小版（docs/21 §1、docs/23 §4.5、openapi Segments）。

規則 = openapi UsersQuery（與用戶列表同一套條件與 Bitmap 編譯：屬性／分群／做過未做過／分析落點下鑽；「完成 A 未完成 B」= behaviors done + done:false）。
計算：INSERT INTO dmp.segment_members (segment_id, version, one_id, computed_at) WITH <bitmaps> SELECT … FROM dmp.users AS e FINAL WHERE bitmapContains(bm, e.one_id_uint)
      → count → 刪掉超過保留數的舊版本（手動 1 版、例行 keep 版）。查詢端 seg.<id> 取最新版本、seg.<id>@<n> 取指定版本。
狀態機：draft ⇄ pending_approval → active ⇄ paused；任一 → recycled（15 天後徹底刪除）→ restore 回 active／draft。
審批：行銷與 agent 提議的分群建立即 pending_approval；數據分析／數位經營／admin 直接 active；segments.approve 才能核可。
排程：tick(now)：active 且例行的分群，到了當日最早計算時間（tz）且該 basetime 未算過 → 依序計算（正式環境由 Airflow 每分鐘呼叫 /v1/segments/tick）。
最小版同步計算；正式環境改為排隊 + 狀態 running。
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from ..security.policy import Subject
from ..semantic.compiler import Catalog
from ..semantic.compiler.events import q
from ..semantic.compiler.users import _bitmaps, compile_estimate, parse_users

STATUSES = ("draft", "pending_approval", "active", "paused", "recycled")
KINDS = ("rule", "result")
FREQS = ("daily", "weekly", "monthly")
MAX_SEGMENTS = 500
RECYCLE_DAYS = 15
PROFILE_PROPS = ("lifecycle", "tier", "aum_bucket", "branch_id")
NO_APPROVAL_ROLES = {"data_analyst", "digital_ops", "admin"}


class Conflict(Exception):
    """409：狀態不允許或不可變更的欄位。"""


class SegmentService:
    def __init__(self, store: Any, ch: Any | None, cat: Catalog | None = None, now: Callable[[], datetime] | None = None):
        self.store, self.ch, self.cat = store, ch, cat or Catalog()
        self.now = now or (lambda: datetime.now(ZoneInfo("Asia/Taipei")))

    # ---------------------------------------------------------------- 讀
    def list(self, q_: str | None = None, status: str | None = None, kind: str | None = None, project: str | None = None) -> list[dict[str, Any]]:
        out = []
        for s in self.store.segments():
            if status and s["status"] != status: continue
            if not status and s["status"] == "recycled": continue
            if kind and s["kind"] != kind: continue
            if project and s["project"] != project: continue
            if q_ and q_.lower() not in (s["name"] + " " + s.get("description", "")).lower(): continue
            out.append(s)
        return sorted(out, key=lambda s: s["updated_at"], reverse=True)

    def get(self, id_: str) -> dict[str, Any]:
        for s in self.store.segments():
            if s["id"] == id_:
                return s
        raise KeyError(id_)

    def versions(self, id_: str) -> list[dict[str, Any]]:
        return list(reversed(self.get(id_).get("versions", [])))

    def lineage(self, id_: str) -> dict[str, list[str]]:
        seg = self.get(id_)
        down = [f"segment:{s['id']}" for s in self.store.segments() if s["id"] != id_ and s["status"] != "recycled" and id_ in self._refs(s["rule"])]
        return {"up": seg.get("lineage", {}).get("up", []), "down": down}

    @staticmethod
    def _refs(rule: dict[str, Any]) -> set[str]:
        """規則文字裡引用的分群 id（seg.<id>／segment 捷徑）。"""
        txt = repr(rule)
        return set(re.findall(r"seg\.([A-Za-z0-9_\-]+)", txt)) | ({str(rule["segment"])} if rule.get("segment") else set())

    # ---------------------------------------------------------------- 寫
    def _validate(self, body: dict[str, Any], decision: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(body.get("name") or (existing or {}).get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("name 需 1–60 字")
        kind = str(body.get("kind") or (existing or {}).get("kind") or "rule")
        if kind not in KINDS:
            raise ValueError(f"kind 只能是 {KINDS}")
        rule = body.get("rule") if body.get("rule") is not None else (existing or {}).get("rule")
        if not isinstance(rule, dict):
            raise ValueError("rule（UsersQuery）必填")
        if kind == "result" and not rule.get("source"):
            raise ValueError("結果保存分群的 rule 需含 source（分析落點）")
        project = str(rule.get("project") or body.get("project") or (existing or {}).get("project") or "altatech_dmp")
        rule = {**rule, "project": project}
        uq = parse_users(rule, self.cat)
        if existing is None or existing["id"] in self._refs(rule):
            if existing is not None and existing["id"] in self._refs(rule):
                raise ValueError("分群不能引用自己")
        comp = compile_estimate(uq, self.cat, decision)
        upd = {**{"mode": "manual", "freq": "daily", "earliest": "06:00", "keep": 7}, **((existing or {}).get("update") or {}), **(body.get("update") or {})}
        if upd["mode"] not in ("manual", "routine"): raise ValueError("update.mode 只能是 manual／routine")
        if upd["freq"] not in FREQS: raise ValueError(f"update.freq 只能是 {FREQS}")
        if not re.fullmatch(r"[0-2]\d:[0-5]\d", str(upd["earliest"])) or int(str(upd["earliest"])[:2]) > 23: raise ValueError("update.earliest 需為 HH:MM")
        upd["keep"] = 1 if upd["mode"] == "manual" else int(upd["keep"])
        if not 1 <= upd["keep"] <= 100: raise ValueError("update.keep 需在 1–100")
        tz = str(body.get("tz") or (existing or {}).get("tz") or "Asia/Taipei")
        try: ZoneInfo(tz)
        except Exception: raise ValueError(f"tz 無效：{tz}") from None
        ups = sorted(set(comp.lineage) | {f"segment:{r}" for r in self._refs(rule)})
        return {"name": name, "description": str(body.get("description") or (existing or {}).get("description") or ""), "kind": kind, "rule": rule, "project": project,
                "update": upd, "tz": tz, "lineage": {"up": ups, "down": []}, "_warnings": comp.warnings}

    def _needs_approval(self, subject: Subject) -> bool:
        return not any(r in NO_APPROVAL_ROLES for r in subject.roles) or subject.user_id.startswith("agent:")

    def create(self, body: dict[str, Any], subject: Subject, decision: Any) -> dict[str, Any]:
        v = self._validate(body, decision)
        live = [s for s in self.store.segments() if s["status"] != "recycled" and s["project"] == v["project"]]
        if len(live) >= MAX_SEGMENTS:
            raise Conflict(f"分群數已達上限 {MAX_SEGMENTS}")
        if any(s["name"] == v["name"] for s in live):
            raise Conflict(f"分群名稱重複：{v['name']}")
        ts = self._ts()
        pending = self._needs_approval(subject)
        seg = {"id": "sg_" + uuid.uuid4().hex[:10], **{k: x for k, x in v.items() if k != "_warnings"}, "status": "pending_approval" if pending else "active",
               "owner": subject.user_id, "created_at": ts, "updated_at": ts,
               "approval": {"status": "pending" if pending else "none", "by": None, "at": None, "note": ""}, "latest": None, "versions": [], "recycled_at": None}
        self.store.put_segment(seg)
        self.store.audit("segment.created", {"user": subject.user_id, "segment": seg["id"], "name": seg["name"], "kind": seg["kind"], "status": seg["status"], "lineage": seg["lineage"]["up"]})
        return seg

    def update(self, id_: str, body: dict[str, Any], subject: Subject, decision: Any) -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] == "recycled":
            raise Conflict("回收站中的分群不能編輯，請先恢復")
        if seg["kind"] == "result" and body.get("rule") is not None and body["rule"] != seg["rule"]:
            raise Conflict("結果保存分群不可改規則")
        if body.get("kind") and body["kind"] != seg["kind"]:
            raise Conflict("分群類型不可改")
        upd = body.get("update") or {}
        if seg["update"]["mode"] == "manual" and upd.get("mode") and upd["mode"] != "manual":
            raise Conflict("手動分群不可改更新方式")
        if seg["update"]["mode"] == "routine" and ((upd.get("mode") and upd["mode"] != "routine") or (upd.get("freq") and upd["freq"] != seg["update"]["freq"])):
            raise Conflict("例行分群不可改頻率與更新方式（可改時間範圍與最早計算時間）")
        v = self._validate(body, decision, existing=seg)
        if any(s["name"] == v["name"] and s["id"] != id_ and s["status"] != "recycled" and s["project"] == v["project"] for s in self.store.segments()):
            raise Conflict(f"分群名稱重複：{v['name']}")
        rule_changed = v["rule"] != seg["rule"]
        seg.update({k: x for k, x in v.items() if k != "_warnings"}); seg["updated_at"] = self._ts()
        if rule_changed and seg.get("latest"):
            seg["latest"] = {**seg["latest"], "stale": True}      # 規則已改、結果是舊規則算的
        self.store.put_segment(seg)
        self.store.audit("segment.updated", {"user": subject.user_id, "segment": id_, "rule_changed": rule_changed})
        return seg

    def submit(self, id_: str, subject: Subject) -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] != "draft":
            raise Conflict("只有 draft 可送審")
        seg["status"] = "pending_approval"; seg["approval"] = {"status": "pending", "by": None, "at": None, "note": ""}; seg["updated_at"] = self._ts()
        self.store.put_segment(seg); self.store.audit("segment.submitted", {"user": subject.user_id, "segment": id_})
        return seg

    def approve(self, id_: str, approve: bool, note: str, subject: Subject) -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] != "pending_approval":
            raise Conflict("不在待審批狀態")
        if seg["owner"] == subject.user_id and "admin" not in subject.roles:
            raise Conflict("不能審批自己建立的分群")
        seg["status"] = "active" if approve else "draft"
        seg["approval"] = {"status": "approved" if approve else "rejected", "by": subject.user_id, "at": self._ts(), "note": note}
        seg["updated_at"] = self._ts(); self.store.put_segment(seg)
        self.store.audit("segment.approved" if approve else "segment.rejected", {"user": subject.user_id, "segment": id_, "note": note})
        return seg

    def pause(self, id_: str, subject: Subject) -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] not in ("active", "paused"):
            raise Conflict("只有生效中／已暫停的分群可暫停／啟動")
        seg["status"] = "paused" if seg["status"] == "active" else "active"; seg["updated_at"] = self._ts()
        self.store.put_segment(seg); self.store.audit("segment.paused" if seg["status"] == "paused" else "segment.resumed", {"user": subject.user_id, "segment": id_})
        return seg

    def copy(self, id_: str, subject: Subject, decision: Any, name: str | None = None) -> dict[str, Any]:
        src = self.get(id_)
        body = {"name": name or f"{src['name']}（複製）", "description": src.get("description", ""), "kind": "rule", "rule": src["rule"], "update": src["update"], "tz": src["tz"]}
        seg = self.create(body, subject, decision)
        self.store.audit("segment.copied", {"user": subject.user_id, "segment": seg["id"], "from": id_})
        return seg

    def recycle(self, id_: str, subject: Subject, force: bool = False) -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] == "recycled":
            return seg
        down = self.lineage(id_)["down"]
        if down and not force:
            raise Conflict(f"有下游依賴：{', '.join(down)}；確認後帶 force=true")
        seg["_prev_status"] = seg["status"]; seg["status"] = "recycled"; seg["recycled_at"] = self._ts(); seg["updated_at"] = self._ts()
        self.store.put_segment(seg); self.store.audit("segment.recycled", {"user": subject.user_id, "segment": id_, "dependents": down})
        return seg

    def restore(self, id_: str, subject: Subject) -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] != "recycled":
            raise Conflict("不在回收站")
        seg["status"] = seg.pop("_prev_status", None) or ("active" if seg["approval"]["status"] in ("approved", "none") else "draft")
        seg["recycled_at"] = None; seg["updated_at"] = self._ts()
        self.store.put_segment(seg); self.store.audit("segment.restored", {"user": subject.user_id, "segment": id_})
        return seg

    def purge(self, id_: str, subject: Subject) -> None:
        seg = self.get(id_)
        if seg["status"] != "recycled":
            raise Conflict("只能徹底刪除回收站中的分群")
        if self.ch is not None:
            self.ch.query(f"ALTER TABLE dmp.segment_members DELETE WHERE segment_id = {q(id_)}", settings={"mutations_sync": 2})
        self.store.delete_segment(id_); self.store.audit("segment.purged", {"user": subject.user_id, "segment": id_, "name": seg["name"]})

    def purge_expired(self, subject: Subject) -> list[str]:
        """回收站超過 RECYCLE_DAYS 天自動徹底刪除（排程呼叫）。"""
        out = []
        for s in list(self.store.segments()):
            if s["status"] == "recycled" and s.get("recycled_at") and datetime.fromisoformat(s["recycled_at"]) + timedelta(days=RECYCLE_DAYS) <= self.now():
                self.purge(s["id"], subject); out.append(s["id"])
        return out

    # ---------------------------------------------------------------- 估數／畫像／計算
    def estimate(self, rule: dict[str, Any], decision: Any) -> dict[str, Any]:
        uq = parse_users(rule, self.cat); c = compile_estimate(uq, self.cat, decision)
        rows = self._rows(c.sql + " FORMAT JSONEachRow", c.settings)
        return {"users": int(rows[0]["users"]) if rows else 0, "sql": c.sql, "lineage": c.lineage, "warnings": c.warnings}

    def preview(self, id_: str, decision: Any) -> dict[str, Any]:
        seg = self.get(id_)
        est = self.estimate(seg["rule"], decision)
        uq = parse_users(seg["rule"], self.cat); ctes, bm, _, _, _ = _bitmaps(uq, self.cat, decision)
        profile: dict[str, list[dict[str, Any]]] = {}
        for prop in PROFILE_PROPS:
            sql = f"WITH {', '.join(ctes)} SELECT toString(e.`{prop}`) AS value, count() AS users FROM dmp.users AS e FINAL WHERE bitmapContains({bm}, e.one_id_uint) GROUP BY value ORDER BY users DESC, value LIMIT 10"
            profile[prop] = [{"value": r["value"], "users": int(r["users"])} for r in self._rows(sql + " FORMAT JSONEachRow", {"max_execution_time": 60})]
        return {"users": est["users"], "profile": profile, "sql": est["sql"], "warnings": est["warnings"]}

    def compute(self, id_: str, subject: Subject, decision: Any, basetime: str | None = None, trigger: str = "manual") -> dict[str, Any]:
        seg = self.get(id_)
        if seg["status"] != "active":
            raise Conflict(f"分群未生效（{seg['status']}），不能計算")
        if self.ch is None:
            raise Conflict("此環境未連接 ClickHouse")
        bt = str(basetime)[:10] if basetime else self.now().date().isoformat()
        date.fromisoformat(bt)
        version = int((seg.get("latest") or {}).get("version", 0)) + 1
        uq = parse_users(seg["rule"], self.cat); ctes, bm, _, warnings, _ = _bitmaps(uq, self.cat, decision)
        ts = self._ts(); t0 = time.perf_counter()
        rec: dict[str, Any] = {"version": version, "basetime": bt, "users": None, "status": "running", "computed_at": ts, "ms": 0, "trigger": trigger, "error": None}
        try:
            self.ch.query(f"INSERT INTO dmp.segment_members (segment_id, version, one_id, computed_at) WITH {', '.join(ctes)} "
                          f"SELECT {q(id_)}, {version}, e.one_id, now() FROM dmp.users AS e FINAL WHERE bitmapContains({bm}, e.one_id_uint)", settings={"max_execution_time": 600})
            n = int(self.ch.query(f"SELECT count() FROM dmp.segment_members WHERE segment_id = {q(id_)} AND version = {version}").strip() or 0)
            keep = int(seg["update"]["keep"])
            if version - keep >= 1:
                self.ch.query(f"ALTER TABLE dmp.segment_members DELETE WHERE segment_id = {q(id_)} AND version <= {version - keep}", settings={"mutations_sync": 2})
            rec.update({"users": n, "status": "success"})
        except Exception as e:
            rec.update({"status": "failed", "error": str(e)[:300]})
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        keep = int(seg["update"]["keep"])
        succ = [v for v in seg.get("versions", []) if v["status"] == "success"]
        seg["versions"] = (succ + [rec])[-keep:] if rec["status"] == "success" else (succ[-keep:] + [rec])      # 成功版本留 keep 版；失敗紀錄留最後一筆
        seg["latest"] = rec; seg["updated_at"] = self._ts(); self.store.put_segment(seg)
        self.store.audit("segment.computed" if rec["status"] == "success" else "segment.compute_failed",
                         {"user": subject.user_id, "segment": id_, "version": version, "basetime": bt, "users": rec["users"], "ms": rec["ms"], "trigger": trigger, "error": rec["error"], "warnings": warnings})
        return rec

    def members_rule(self, id_: str, basetime: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """某版本成員 = UsersQuery{filters: seg.<id>@<version>}；basetime 'latest' 或 YYYY-MM-DD。"""
        seg = self.get(id_)
        vs = [v for v in seg.get("versions", []) if v["status"] == "success"]
        if basetime == "latest":
            if not vs: raise KeyError("尚無計算結果")
            v = vs[-1]
        else:
            m = [x for x in vs if x["basetime"] == str(basetime)[:10]]
            if not m: raise KeyError(f"沒有 basetime={basetime} 的版本")
            v = m[-1]
        return {"project": seg["project"], "filters": [{"field": f"seg.{id_}@{v['version']}", "op": "in"}]}, v

    # ---------------------------------------------------------------- 排程
    def due_slot(self, seg: dict[str, Any], now: datetime) -> str | None:
        """例行分群在 now 應該算的 basetime（當地日期）；還沒到最早計算時間或不是該頻率的日子 → None。"""
        if seg["status"] != "active" or seg["update"]["mode"] != "routine":
            return None
        tz = ZoneInfo(seg["tz"]); local = now.astimezone(tz)
        hh, mm = (int(x) for x in seg["update"]["earliest"].split(":"))
        d = local.date(); f = seg["update"]["freq"]
        slot = d - timedelta(days=d.weekday()) if f == "weekly" else d.replace(day=1) if f == "monthly" else d      # 週 = 本週一、月 = 本月 1 日（錯過會補算）
        due_at = datetime.combine(slot, dtime(hh, mm), tz)
        return slot.isoformat() if local >= due_at else None

    def tick(self, subject: Subject, decision_of: Callable[[dict[str, Any]], Any], now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or self.now(); out = []
        for seg in sorted(self.store.segments(), key=lambda s: s["id"]):
            slot = self.due_slot(seg, now)
            if slot is None: continue
            done = {v["basetime"] for v in seg.get("versions", []) if v["status"] == "success"}
            if slot in done: continue
            rec = self.compute(seg["id"], subject, decision_of(seg), basetime=slot, trigger="routine")
            out.append({"segment": seg["id"], **rec})
        return out

    # ---------------------------------------------------------------- 工具
    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def _rows(self, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
        import json
        if self.ch is None:
            raise Conflict("此環境未連接 ClickHouse")
        txt = self.ch.query(sql, settings={**settings, "output_format_json_quote_64bit_integers": 0})
        return [json.loads(line) for line in txt.splitlines() if line.strip()]

    @staticmethod
    def sql_hash(sql: str) -> str:
        return hashlib.sha256(sql.encode()).hexdigest()[:16]
