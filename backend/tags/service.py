"""標籤（docs/21 §2）：五種建立方式，其餘第二階段。

- values（自定義標籤值）：每個值一條用戶規則（UsersQuery，與分群／用戶列表同一套編譯），最多 24 值；同一人可同時命中多值（分布為各值人數）。
- sql（SQL 標籤）：唯讀沙盒驗過的 SELECT one_id_uint, tag_value …；寫入時 GROUP BY tag_value 聚成 Bitmap。
- metric（基礎指標值）：某事件在回溯窗口內每人的次數／總和／均值／最大／最小／有行為天數，可分桶成標籤值。
- firstlast（首末次特徵）：首次或末次某事件的日期、距基準日天數（可分桶）、或當時的屬性值（argMin／argMax）。
- rfm：最近一次距今 R、次數 F、金額 M 各以 ntile 分 n 級，輸出 R5F4M3 分數或八類客群（各維度高於中位為高）。
  三種都由 _spec_sql 編成同一形狀的 SELECT one_id_uint, tag_value，與 SQL 標籤共用寫入路徑；欄位用語意層目錄解析、行級條件照套。
- 儲存：dmp.user_tags (tag_id, tag_value, version, users Bitmap)，每次計算一版，保留 keep 版；查詢端以 bitmapAnd／dictGet 使用。
- 狀態機與審批同分群：行銷建立 → pending_approval（審批單 tag，數位經營）；數據分析／數位經營／admin 直接 active。
- tick：例行標籤到點計算（每天／每週一／每月 1 日的最早計算時間）。
"""
from __future__ import annotations

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
from ..sql import sandbox

KINDS = ("values", "sql", "metric", "firstlast", "rfm", "import")
SPEC_KINDS = ("metric", "firstlast", "rfm")
METRIC_AGGS = ("count", "sum", "avg", "max", "min", "days")
RFM_SEGMENTS = ("重要價值", "重要發展", "重要保持", "重要挽留", "一般價值", "一般發展", "一般保持", "一般挽留")
DTYPES = ("string", "number", "bool")
FREQS = ("daily", "weekly", "monthly")
NO_APPROVAL_ROLES = {"data_analyst", "digital_ops", "admin"}
DIRECTORIES = ["客戶價值", "交易偏好", "交易活躍", "風險", "活躍", "渠道", "行銷", "其他"]
CUSTOM_FIELDS = [{"key": "department", "cn": "所屬部門", "type": "select", "options": ["數位金融", "財富管理", "行銷", "數據分析", "風控"]},
                 {"key": "requester", "cn": "需求提出人", "type": "text"},
                 {"key": "category", "cn": "需求類別", "type": "select", "options": ["名單推送", "分析分群", "風險監控", "活動評估"]}]


class Conflict(Exception):
    pass


def _at(rule: dict[str, Any], basetime: str) -> dict[str, Any]:
    """規則加上基準日（time.now = basetime）：行為的「近 N 天」與用戶屬性的「最近 N 天內」都以該日為準；規則本身已有 now 則尊重。"""
    t = dict(rule.get("time") or {})
    t.setdefault("now", basetime)
    return {**rule, "time": t}


class TagService:
    def __init__(self, store: Any, ch: Any | None, approvals: Any | None = None, cat: Catalog | None = None, now: Callable[[], datetime] | None = None):
        self.store, self.ch, self.cat = store, ch, cat or Catalog()
        self.now = now or (lambda: datetime.now(ZoneInfo("Asia/Taipei")))
        self.approvals = approvals
        if approvals is not None:
            approvals.hooks["tag"] = self._on_decided

    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ---------------------------------------------------------------- 讀
    def list(self, q_: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        out = []
        for t in self.store.items("tag"):
            if status and t["status"] != status: continue
            if not status and t["status"] == "recycled": continue
            if q_ and q_ not in t["name"] and q_ not in t["id"] and q_ not in (t.get("description") or ""): continue
            out.append(self._public(t))
        return sorted(out, key=lambda t: (t.get("directory") or "", t["name"]))

    def get(self, id_: str) -> dict[str, Any]:
        for t in self.store.items("tag"):
            if t["id"] == id_:
                return t
        raise KeyError(id_)

    def _public(self, t: dict[str, Any]) -> dict[str, Any]:
        down = [p["id"] for p in self.store.items("push") if t["id"] in (p.get("include_tags") or []) and p.get("status") != "deleted"]
        down += [d["id"] for d in self.store.items("destination") if t["id"] in (d.get("allowed_tags") or [])]
        return {**t, "lineage": {"up": t.get("lineage", {}).get("up", []), "down": down}}

    @staticmethod
    def _refs(rule: dict[str, Any]) -> set[str]:
        """規則引用的分群 id（segment 捷徑、條件樹任一層的 seg.<id>）。"""
        refs = {str(rule["segment"])} if rule.get("segment") else set()
        def walk(node: Any) -> None:
            if isinstance(node, list):
                for it in node: walk(it)
            elif isinstance(node, dict):
                if "items" in node: walk(node["items"])
                else:
                    f = str(node.get("field") or "")
                    if f.startswith("seg."): refs.add(f[4:].split("@")[0])
        walk(rule.get("filters"))
        return refs

    # ---------------------------------------------------------------- 驗證
    def _validate(self, body: dict[str, Any], subject: Subject, decision: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        x = dict(existing or {})
        name = str(body.get("name") or x.get("name") or "").strip()
        if not 1 <= len(name) <= 60: raise ValueError("name 需 1–60 字")
        key = str(body.get("key") or x.get("key") or "").strip().lower()
        if not key:
            key = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or ""
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", key): key = "tag_" + uuid.uuid4().hex[:6]
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", key): raise ValueError("key 只能是小寫英數與底線（2–41 字）")
        if existing is None and any(t["id"] == key for t in self.store.items("tag")): raise Conflict(f"標籤 {key} 已存在")
        kind = str(body.get("kind") or x.get("kind") or "values")
        if kind not in KINDS: raise ValueError(f"kind 只支援 {KINDS}（其餘建立方式第二階段）")
        dtype = str(body.get("dtype") or x.get("dtype") or "string")
        if dtype not in DTYPES: raise ValueError(f"dtype 只能是 {DTYPES}")
        ups: set[str] = set(); warnings: list[str] = []
        values = body.get("values") if body.get("values") is not None else x.get("values")
        sql = str(body.get("sql") if body.get("sql") is not None else x.get("sql") or "")
        if kind == "values":
            if not isinstance(values, list) or not 1 <= len(values) <= 24: raise ValueError("values 需 1–24 個標籤值")
            seen: set[str] = set(); norm = []
            for v in values:
                val = str((v or {}).get("value") or "").strip(); rule = (v or {}).get("rule")
                if not val or len(val) > 40: raise ValueError("標籤值需 1–40 字")
                if val in seen: raise ValueError(f"標籤值重複：{val}")
                if not isinstance(rule, dict): raise ValueError(f"標籤值 {val} 缺規則（UsersQuery）")
                rule = {**rule, "project": str(rule.get("project") or x.get("project") or "altatech_dmp")}
                uq = parse_users(rule, self.cat); comp = compile_estimate(uq, self.cat, decision)
                ups |= set(comp.lineage) | {f"segment:{r}" for r in self._refs(rule)}; warnings += comp.warnings; seen.add(val)
                norm.append({"value": val, "rule": rule})
            values = norm; sql = ""
        elif kind == "import":
            iid = str(body.get("import_id") or x.get("import_id") or "")
            imp = next((i for i in self.store.items("segment_import") if i["id"] == iid), None)
            if imp is None or imp.get("table") != "dmp.tag_imports": raise ValueError("import 標籤需 import_id（帶標籤值的導入，例如舊平台標籤快照）")
            x["import_id"] = iid; values = []; sql = ""; spec = {}; ups.add("dmp.tag_imports")
        elif kind == "sql":
            if not sql.strip(): raise ValueError("SQL 標籤需 sql")
            if not re.search(r"\bone_id_uint\b", sql) or not re.search(r"\btag_value\b", sql): raise ValueError("SQL 需輸出 one_id_uint 與 tag_value 兩欄（可用 AS 別名）")
            c = sandbox.compile_sql(sql, None, decision, self.cat, subject.roles, 10, sandbox.MID_TABLES)
            ups |= set(c.lineage); values = []; spec = {}
        else:
            spec, s_ups = self._check_spec(kind, body.get("spec") if body.get("spec") is not None else x.get("spec"))
            ups |= s_ups; values = []; sql = ""
            x["project"] = str(body.get("project") or x.get("project") or "altatech_dmp")
            self._spec_sql({"kind": kind, "spec": spec, "project": x["project"]}, decision, self.now().date().isoformat())   # 行級條件與欄位現在就編一次，壞的設定不要等到計算才爆
        if kind in ("values", "sql"): spec = {}
        if kind == "import": body = {**body, "update": {**(body.get("update") or {}), "mode": "manual"}}   # 快照只算一次
        upd = {**{"mode": "routine", "freq": "daily", "earliest": "02:00", "keep": 7}, **(x.get("update") or {}), **(body.get("update") or {})}
        if upd["mode"] not in ("manual", "routine"): raise ValueError("update.mode 只能是 manual／routine")
        if upd["freq"] not in FREQS: raise ValueError(f"update.freq 只能是 {FREQS}")
        if not re.fullmatch(r"[0-2]\d:[0-5]\d", str(upd["earliest"])) or int(str(upd["earliest"])[:2]) > 23: raise ValueError("update.earliest 需為 HH:MM")
        upd["keep"] = int(upd["keep"])
        if not 1 <= upd["keep"] <= 100: raise ValueError("update.keep 需在 1–100")
        fields = {str(k): str(v)[:80] for k, v in ((body.get("fields") if body.get("fields") is not None else x.get("fields")) or {}).items()}
        for cf in CUSTOM_FIELDS:
            if cf["type"] == "select" and fields.get(cf["key"]) and fields[cf["key"]] not in cf["options"]: raise ValueError(f"{cf['cn']} 只能是 {cf['options']}")
        x.update({"name": name, "key": key, "directory": str(body.get("directory") or x.get("directory") or "其他")[:20], "description": str(body.get("description") or x.get("description") or "")[:300],
                  "dtype": dtype, "kind": kind, "values": values, "sql": sql, "spec": spec, "update": upd, "fields": fields, "project": str(x.get("project") or "altatech_dmp"),
                  "lineage": {"up": sorted(ups), "down": []}, "_warnings": warnings})
        return x

    # ---------------- metric／firstlast／rfm ----------------
    def _check_spec(self, kind: str, spec: Any) -> tuple[dict[str, Any], set[str]]:
        if not isinstance(spec, dict): raise ValueError(f"{kind} 需要 spec")
        ev = str(spec.get("event") or "").strip()
        if ev not in self.cat.events: raise ValueError(f"事件不在語意層目錄：{ev or '（未填）'}")
        w = int(spec.get("window_days") if spec.get("window_days") is not None else (0 if kind == "firstlast" else 90))
        if not 0 <= w <= 730 or (w == 0 and kind != "firstlast"): raise ValueError("window_days 需在 1–730（首末次可為 0＝不限）")
        buckets = spec.get("buckets")
        if buckets is not None:
            if not isinstance(buckets, list) or not 1 <= len(buckets) <= 10: raise ValueError("buckets 需 1–10 個遞增數字")
            try: buckets = [float(b) for b in buckets]
            except (TypeError, ValueError): raise ValueError("buckets 需為數字") from None
            if any(buckets[i] >= buckets[i + 1] for i in range(len(buckets) - 1)): raise ValueError("buckets 需嚴格遞增")
        out: dict[str, Any] = {"event": ev, "window_days": w}; ups = {f"event:{ev}"}
        if kind == "metric":
            agg = str(spec.get("agg") or "count")
            if agg not in METRIC_AGGS: raise ValueError(f"agg 只能是 {METRIC_AGGS}")
            field = str(spec.get("field") or "").strip()
            if agg in ("sum", "avg", "max", "min"):
                if not field: raise ValueError(f"{agg} 需要 field（數值欄位）")
                ref = self.cat.resolve(field, "number")
                if ref.type != "number": raise ValueError(f"field {field} 不是數值欄位")
                ups.add(ref.name or field)
            else: field = ""
            out.update({"agg": agg, "field": field, "buckets": buckets})
        elif kind == "firstlast":
            which = str(spec.get("which") or "first"); feature = str(spec.get("feature") or "date")
            if which not in ("first", "last") or feature not in ("date", "days_since", "field"): raise ValueError("which 只能是 first／last；feature 只能是 date／days_since／field")
            field = str(spec.get("field") or "").strip()
            if feature == "field":
                if not field: raise ValueError("feature=field 需要 field")
                ref = self.cat.resolve(field); ups.add(ref.name or field)
            else: field = ""
            out.update({"which": which, "feature": feature, "field": field, "buckets": buckets if feature == "days_since" else None})
        else:
            af = str(spec.get("amount_field") or "amount").strip(); ref = self.cat.resolve(af, "number")
            if ref.type != "number": raise ValueError(f"amount_field {af} 不是數值欄位")
            bins = int(spec.get("bins") or 5)
            if not 2 <= bins <= 10: raise ValueError("bins 需在 2–10")
            output = str(spec.get("output") or "score")
            if output not in ("score", "segment"): raise ValueError("output 只能是 score／segment")
            ups.add(ref.name or af); out.update({"amount_field": af, "bins": bins, "output": output})
        return out, ups

    def _spec_sql(self, t: dict[str, Any], decision: Any, bt: str) -> str:
        """metric／firstlast／rfm → SELECT one_id_uint, tag_value（字串）。"""
        sp = t["spec"]; kind = t["kind"]; proj = str(t.get("project") or "altatech_dmp"); w = int(sp.get("window_days") or 0)
        conds = [f"e.project = {q(proj)}", f"e.event = {q(sp['event'])}", f"e.event_date <= toDate({q(bt)})"]
        if w > 0: conds.append(f"e.event_date > toDate({q(bt)}) - {w}")
        for f in (getattr(decision, "row_filters", []) or []) if decision is not None else []:
            if "agent_id" in f: raise PermissionError("營業員範圍的身分不能建立標籤（事件表沒有 agent_id 行級條件的對應）")
            conds.append(re.sub(r"\b(branch_id|event_date)\b", r"e.\1", f))
        where = " AND ".join(conds); base = f"FROM dmp.events AS e WHERE {where} GROUP BY e.one_id_uint"
        if kind == "metric":
            fx = self.cat.resolve(sp["field"], "number").sql if sp.get("field") else None
            expr = {"count": "count()", "days": "uniqExact(e.event_date)", "sum": f"sum({fx})", "avg": f"avg({fx})", "max": f"max({fx})", "min": f"min({fx})"}[sp["agg"]]
            val = _bucket_expr("v", sp["buckets"]) if sp.get("buckets") else "toString(round(v, 2))"
            return f"SELECT one_id_uint, {val} AS tag_value FROM (SELECT e.one_id_uint AS one_id_uint, {expr} AS v {base}) AS _m"
        if kind == "firstlast":
            first = sp["which"] == "first"; agf = "min" if first else "max"
            if sp["feature"] == "date": inner, val = f"toString({agf}(e.event_date))", "v"
            elif sp["feature"] == "days_since": inner, val = f"dateDiff('day', {agf}(e.event_date), toDate({q(bt)}))", (_bucket_expr("v", sp["buckets"]) if sp.get("buckets") else "toString(v)")
            else: inner, val = f"toString(arg{'Min' if first else 'Max'}({self.cat.resolve(sp['field']).sql}, e.time))", "if(v = '', '（未填）', v)"
            return f"SELECT one_id_uint, {val} AS tag_value FROM (SELECT e.one_id_uint AS one_id_uint, {inner} AS v {base}) AS _f"
        fx = self.cat.resolve(sp.get("amount_field") or "amount", "number").sql; bins = int(sp.get("bins") or 5); hi = bins // 2 + 1
        scored = (f"SELECT one_id_uint, {bins} + 1 - ntile({bins}) OVER (ORDER BY r_days) AS rs, ntile({bins}) OVER (ORDER BY f) AS fs, ntile({bins}) OVER (ORDER BY m) AS ms "
                  f"FROM (SELECT e.one_id_uint AS one_id_uint, dateDiff('day', max(e.event_date), toDate({q(bt)})) AS r_days, count() AS f, sum(coalesce({fx}, 0)) AS m {base}) AS _b")
        if sp.get("output") == "segment":
            val = (f"multiIf(rs >= {hi} AND fs >= {hi} AND ms >= {hi}, '重要價值', rs >= {hi} AND fs < {hi} AND ms >= {hi}, '重要發展', rs < {hi} AND fs >= {hi} AND ms >= {hi}, '重要保持', ms >= {hi}, '重要挽留', "
                   f"rs >= {hi} AND fs >= {hi}, '一般價值', rs >= {hi}, '一般發展', fs >= {hi}, '一般保持', '一般挽留')")
        else:
            val = "concat('R', toString(rs), 'F', toString(fs), 'M', toString(ms))"
        return f"SELECT one_id_uint, {val} AS tag_value FROM ({scored}) AS _s"

    def _needs_approval(self, subject: Subject) -> bool:
        return not any(r in NO_APPROVAL_ROLES for r in subject.roles)

    # ---------------------------------------------------------------- 寫
    def create(self, body: dict[str, Any], subject: Subject, decision: Any) -> dict[str, Any]:
        t = self._validate(body, subject, decision); warnings = t.pop("_warnings")
        t.update({"id": t["key"], "status": "pending_approval" if self._needs_approval(subject) else "active", "owner": subject.user_id, "created_at": self._ts(), "updated_at": self._ts(),
                  "approval": {"status": "none", "by": None, "at": None, "note": ""}, "latest": None, "versions": [], "recycled_at": None, "changes": []})
        if t["status"] == "pending_approval" and self.approvals is not None:
            a = self.approvals.create("tag", subject, f"標籤「{t['name']}」（{t['kind']}，{len(t['values']) or ('SQL' if t['kind'] == 'sql' else t['kind'])} 值）", {"tag": t["id"], "kind": t["kind"]}, {"kind": "tag", "id": t["id"]})
            t["approval"] = {"status": "pending", "by": None, "at": None, "note": "", "id": a["id"]}
        self.store.put_item("tag", t)
        self.store.audit("tag.created", {"user": subject.user_id, "tag": t["id"], "kind": t["kind"], "status": t["status"], "warnings": warnings})
        return self._public(t)

    def update(self, id_: str, body: dict[str, Any], subject: Subject, decision: Any) -> dict[str, Any]:
        cur = self.get(id_)
        if cur["status"] == "recycled": raise Conflict("回收站中的標籤不能編輯")
        t = self._validate(body, subject, decision, cur); t.pop("_warnings")
        changed = sorted(k for k in ("name", "directory", "description", "dtype", "values", "sql", "spec", "update", "fields") if t.get(k) != cur.get(k))
        t["updated_at"] = self._ts(); t.setdefault("changes", []).append({"at": t["updated_at"], "by": subject.user_id, "changed": changed})
        t["changes"] = t["changes"][-50:]
        self.store.put_item("tag", t)
        self.store.audit("tag.updated", {"user": subject.user_id, "tag": id_, "changed": changed})
        return self._public(t)

    def _on_decided(self, a: dict[str, Any], approved: bool, subject: Subject) -> None:
        try:
            t = self.get(a["object"]["id"])
        except KeyError:
            return
        t["status"] = "active" if approved else "draft"
        t["approval"] = {**t.get("approval", {}), "status": "approved" if approved else "rejected", "by": subject.user_id, "at": self._ts(), "note": a.get("note", "")}
        t["updated_at"] = self._ts(); self.store.put_item("tag", t)

    def approve(self, id_: str, approve: bool, note: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        if t["status"] != "pending_approval": raise Conflict("標籤不在待審批狀態")
        if t["owner"] == subject.user_id and "admin" not in subject.roles: raise PermissionError("不能審批自己建立的標籤")
        aid = (t.get("approval") or {}).get("id")
        if aid and self.approvals is not None:
            self.approvals.decide(aid, subject, approve, note)                     # 走審批單 → hook 回寫
        else:
            self._on_decided({"object": {"id": id_}, "note": note}, approve, subject)
        self.store.audit("tag.approved" if approve else "tag.rejected", {"user": subject.user_id, "tag": id_, "note": note})
        return self._public(self.get(id_))

    def submit(self, id_: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        if t["status"] != "draft": raise Conflict("只有草稿能送審")
        t["status"] = "pending_approval"
        if self.approvals is not None:
            a = self.approvals.create("tag", subject, f"標籤「{t['name']}」重新送審", {"tag": t["id"]}, {"kind": "tag", "id": t["id"]})
            t["approval"] = {"status": "pending", "by": None, "at": None, "note": "", "id": a["id"]}
        t["updated_at"] = self._ts(); self.store.put_item("tag", t)
        return self._public(t)

    def pause(self, id_: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        if t["status"] not in ("active", "paused"): raise Conflict(f"狀態 {t['status']} 不能暫停／啟用")
        t["status"] = "paused" if t["status"] == "active" else "active"; t["updated_at"] = self._ts(); self.store.put_item("tag", t)
        down = self._public(t)["lineage"]["down"]
        self.store.audit("tag.paused" if t["status"] == "paused" else "tag.resumed", {"user": subject.user_id, "tag": id_, "downstream": down})
        return {**self._public(t), "warnings": [f"有 {len(down)} 個下游（推送單／目的地）引用此標籤"] if down and t["status"] == "paused" else []}

    def recycle(self, id_: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        down = self._public(t)["lineage"]["down"]
        if down: raise Conflict(f"有下游引用（{down}），先解除再移入回收站")
        t["recycled_from"] = t["status"]                                            # 還原時回到原狀態（待審批的不能借還原變生效）
        t["status"] = "recycled"; t["recycled_at"] = self._ts(); t["updated_at"] = self._ts(); self.store.put_item("tag", t)
        self.store.audit("tag.recycled", {"user": subject.user_id, "tag": id_, "from": t["recycled_from"]})
        return self._public(t)

    def restore(self, id_: str, subject: Subject) -> dict[str, Any]:
        t = self.get(id_)
        if t["status"] != "recycled": raise Conflict("不在回收站")
        ap = (t.get("approval") or {}).get("status", "none"); before = t.pop("recycled_from", None)
        if ap == "pending": t["status"] = "pending_approval"                           # 審批還沒過：回到待審批，不能借還原生效
        elif ap == "rejected": t["status"] = "draft"
        elif before in ("active", "paused", "draft"): t["status"] = before            # 已核可或不需審批：回到進回收站前的狀態
        else: t["status"] = "active"
        t["recycled_at"] = None; t["updated_at"] = self._ts(); self.store.put_item("tag", t)
        self.store.audit("tag.restored", {"user": subject.user_id, "tag": id_, "status": t["status"]})
        return self._public(t)

    # ---------------------------------------------------------------- 計算
    def compute(self, id_: str, subject: Subject, decision: Any, basetime: str | None = None, trigger: str = "manual") -> dict[str, Any]:
        t = self.get(id_)
        if t["status"] != "active": raise Conflict(f"標籤未生效（{t['status']}），不能計算")
        if self.ch is None: raise Conflict("此環境未連接 ClickHouse")
        bt = str(basetime)[:10] if basetime else self.now().date().isoformat(); date.fromisoformat(bt)
        version = int((t.get("latest") or {}).get("version", 0)) + 1
        ts = self._ts(); t0 = time.perf_counter()
        rec: dict[str, Any] = {"version": version, "basetime": bt, "users": None, "status": "running", "computed_at": ts, "ms": 0, "trigger": trigger, "error": None, "distribution": []}
        try:
            if t["kind"] == "values":
                for v in t["values"]:
                    uq = parse_users(_at(v["rule"], bt), self.cat); ctes, bm, _, _, _ = _bitmaps(uq, self.cat, decision)   # 「近 N 天」以 basetime 為基準，回算歷史版本口徑一致
                    self.ch.query(f"INSERT INTO dmp.user_tags (tag_id, tag_value, version, users) WITH {', '.join(ctes)} "
                                  f"SELECT {q(id_)}, {q(v['value'])}, {version}, groupBitmapState(e.one_id_uint) FROM dmp.users AS e FINAL WHERE bitmapContains({bm}, e.one_id_uint)",
                                  settings={"max_execution_time": 600})
            else:
                if t["kind"] == "import":
                    inner = f"SELECT one_id_uint, tag_value FROM dmp.tag_imports WHERE import_id = {q(t['import_id'])} AND one_id IN (SELECT one_id FROM dmp.users FINAL)"
                elif t["kind"] == "sql":
                    c = sandbox.compile_sql(t["sql"], None, decision, self.cat, subject.roles, sandbox.MAX_LIMIT, sandbox.MID_TABLES)
                    inner = c.sql[len("SELECT * FROM ("):c.sql.rindex(") AS _q LIMIT")]
                else:
                    inner = self._spec_sql(t, decision, bt)
                self.ch.query(f"INSERT INTO dmp.user_tags (tag_id, tag_value, version, users) SELECT {q(id_)}, toString(tag_value), {version}, groupBitmapState(toUInt64(one_id_uint)) "
                              f"FROM ({inner}) AS _t GROUP BY tag_value", settings={"max_execution_time": 600})
            rec["distribution"] = self.distribution_of(id_, version)
            rec["users"] = self._count(id_, version)
            keep = int(t["update"]["keep"])
            if version - keep >= 1:
                self.ch.query(f"ALTER TABLE dmp.user_tags DELETE WHERE tag_id = {q(id_)} AND version <= {version - keep}", settings={"mutations_sync": 2, "max_execution_time": 600})
            rec["status"] = "success"
        except Exception as e:
            rec.update({"status": "failed", "error": str(e)[:300]})
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        keep = int(t["update"]["keep"]); succ = [v for v in t.get("versions", []) if v["status"] == "success"]
        t["versions"] = (succ + [rec])[-keep:] if rec["status"] == "success" else (succ[-keep:] + [rec])
        t["latest"] = rec; t["updated_at"] = self._ts(); self.store.put_item("tag", t)
        self.store.audit("tag.computed" if rec["status"] == "success" else "tag.compute_failed", {"user": subject.user_id, "tag": id_, "version": version, "basetime": bt, "users": rec["users"], "ms": rec["ms"], "trigger": trigger, "error": rec["error"]})
        return rec

    def _rows(self, sql: str) -> list[dict[str, Any]]:
        import json
        txt = self.ch.query(sql, settings={"max_execution_time": 60, "output_format_json_quote_64bit_integers": 0})
        return [json.loads(line) for line in txt.splitlines() if line.strip()]

    def distribution_of(self, id_: str, version: int) -> list[dict[str, Any]]:
        rows = self._rows(f"SELECT tag_value AS value, bitmapCardinality(groupBitmapMergeState(users)) AS users FROM dmp.user_tags WHERE tag_id = {q(id_)} AND version = {int(version)} GROUP BY tag_value ORDER BY users DESC FORMAT JSONEachRow")
        return [{"value": str(r.get("value", "")), "users": int(r.get("users") or 0)} for r in rows]

    def _count(self, id_: str, version: int) -> int:
        rows = self._rows(f"SELECT bitmapCardinality(groupBitmapMergeState(users)) AS users FROM dmp.user_tags WHERE tag_id = {q(id_)} AND version = {int(version)} FORMAT JSONEachRow")
        return int((rows[0] if rows else {}).get("users") or 0)

    def distribution(self, id_: str, basetime: str | None = None) -> dict[str, Any]:
        t = self.get(id_); vs = [v for v in t.get("versions", []) if v["status"] == "success"]
        if not vs: raise KeyError("尚無計算結果")
        v = vs[-1] if not basetime else next((x for x in vs if x["basetime"] == str(basetime)[:10]), None)
        if v is None: raise KeyError(f"沒有 basetime={basetime} 的版本")
        return {"tag": id_, "version": v["version"], "basetime": v["basetime"], "users": v["users"], "distribution": v.get("distribution") or []}

    def versions(self, id_: str) -> list[dict[str, Any]]:
        return list(self.get(id_).get("versions", []))

    # ---------------------------------------------------------------- 排程
    def due_slot(self, t: dict[str, Any], now: datetime) -> str | None:
        if t["status"] != "active" or t["update"]["mode"] != "routine": return None
        tz = ZoneInfo("Asia/Taipei"); local = now.astimezone(tz)
        hh, mm = (int(x) for x in t["update"]["earliest"].split(":"))
        d = local.date(); f = t["update"]["freq"]
        slot = d - timedelta(days=d.weekday()) if f == "weekly" else d.replace(day=1) if f == "monthly" else d
        return slot.isoformat() if local >= datetime.combine(slot, dtime(hh, mm), tz) else None

    def tick(self, subject: Subject, decision_of: Callable[[dict[str, Any]], Any], now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or self.now(); out = []
        for t in sorted(self.store.items("tag"), key=lambda x: x["id"]):
            slot = self.due_slot(t, now)
            if slot is None or slot in {v["basetime"] for v in t.get("versions", []) if v["status"] == "success"}: continue
            out.append({"tag": t["id"], **self.compute(t["id"], subject, decision_of(t), basetime=slot, trigger="routine")})
        return out


def _num(b: float) -> str:
    return str(int(b)) if float(b).is_integer() else f"{b:g}"


def _bucket_expr(v: str, buckets: list[float]) -> str:
    """遞增界線 → multiIf：<b0、b0–b1、…、≥bn（標籤值用全形連接號，與數值分桶隱碼一致）。"""
    parts = [f"{v} < {buckets[0]!r}, {q('<' + _num(buckets[0]))}"]
    parts += [f"{v} < {buckets[i + 1]!r}, {q(_num(buckets[i]) + '–' + _num(buckets[i + 1]))}" for i in range(len(buckets) - 1)]
    return f"multiIf({', '.join(parts)}, {q('≥' + _num(buckets[-1]))})"
