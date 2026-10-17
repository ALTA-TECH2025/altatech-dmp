"""受眾名單推送（docs/14）：目的地 → 推送單（分群 × 目的地 × 模式 × 排程）→ 交付（每批一筆、冪等、重試、DLQ、重放）。

- 目的地只存 secret_ref；簽章時才向密鑰庫解析（resolver 注入）。測試注入假 transport，不打網路。
- 增量 = 分群最新版本與上次交付版本的成員差集（加入／移除）；全量 = 整份 + audience.complete 收尾。
- 審批：全量推送、或目的地第一次被推送 → 審批單 push（數位經營；admin 可核）。核可後若排程是 now 立即執行。
- 名單只含代碼化識別子（customer_code = HMAC(one_id)）與 one_id；附帶標籤限目的地 allowed_tags，數值型標籤區間化。
- tick(now)：到期重試、after_batch／cron 推送單在分群有新版本時自動增量推送。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import yaml  # type: ignore

from ..security.policy import Subject
from ..semantic.compiler.events import q
from . import webhook
from .webhook import RETRY_SCHEDULE, Destination

CFG = yaml.safe_load((__import__("pathlib").Path(__file__).resolve().parent / "destinations.yaml").read_text(encoding="utf-8"))
SECRET_REF_RE = re.compile(r"^(kv/data/dmp/[a-z]+/[a-z0-9_]+#[a-z_]+|projects/[^/]+/secrets/[^/]+/versions/(\d+|latest)|secret://[a-z0-9-]+/[a-z0-9-]+/[a-zA-Z0-9_.-]+)$")
TYPES = tuple(CFG["types"].keys())
IDENTIFIERS = ("customer_code", "one_id")
MODES = ("delta", "full")
SCHEDULES = ("now", "after_batch", "cron")
PAUSE_AFTER = int(CFG["types"]["webhook"]["retry"]["pause_after_consecutive_failures"])
TIMEOUT_S = int(CFG["types"]["webhook"]["batch"]["timeout_seconds"])
NUM_BUCKETS = [(0, "<10"), (10, "10–100"), (100, "100–1K"), (1000, "1K–10K"), (10000, "10K–100K"), (100000, "≥100K")]
Transport = Callable[[str, dict[str, str], bytes, int], tuple[int | None, int, str]]


class Conflict(Exception):
    pass


def httpx_transport(url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int | None, int, str]:
    """正式送出：httpx，逾時／連線錯誤回 status None。"""
    import httpx
    t0 = time.perf_counter()
    try:
        r = httpx.post(url, headers=headers, content=body, timeout=timeout)
        return r.status_code, round((time.perf_counter() - t0) * 1000), r.text[:300]
    except httpx.HTTPError as e:
        return None, round((time.perf_counter() - t0) * 1000), f"{type(e).__name__}: {str(e)[:200]}"


def customer_code(one_id: str, key: bytes) -> str:
    return "cust_" + hmac.new(key, one_id.encode(), hashlib.sha256).hexdigest()[:16]


def bucket_number(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    label = NUM_BUCKETS[0][1]
    for lo, cn in NUM_BUCKETS:
        if x >= lo:
            label = cn
    return label


class ActivationService:
    def __init__(self, store: Any, ch: Any | None, approvals: Any, secret_of: Callable[[str], str] | None = None, transport: Transport | None = None,
                 tags: Any = None, now: Callable[[], datetime] | None = None, code_key: bytes | None = None):
        self.store, self.ch, self.approvals, self.tags = store, ch, approvals, tags
        self.secret_of = secret_of or (lambda ref: (_ for _ in ()).throw(KeyError(ref)))
        self.transport = transport or httpx_transport
        self.now = now or (lambda: datetime.now(ZoneInfo("Asia/Taipei")))
        self.code_key = code_key or b"dev-only-customer-code-key"
        approvals.hooks["push"] = self._on_decided

    def _ts(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ---------------------------------------------------------------- 目的地
    def destinations(self) -> list[dict[str, Any]]:
        dl = self.store.items("delivery")
        out = []
        for d in sorted(self.store.items("destination"), key=lambda x: x["created_at"]):
            out.append(self._with_stats(d, dl))
        return out

    def _with_stats(self, d: dict[str, Any], dl: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        dl = dl if dl is not None else self.store.items("delivery")
        cutoff = (self.now() - timedelta(days=7)).isoformat(timespec="seconds")
        mine = [x for x in dl if x["destination_id"] == d["id"] and x["created_at"] >= cutoff and x["mode"] != "test"]
        ok = [x for x in mine if x["status"] == "ok"]
        p95 = None
        ms = sorted(int((x.get("response") or {}).get("ms") or 0) for x in ok)
        if ms:
            p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
        last = max((x["updated_at"] for x in dl if x["destination_id"] == d["id"]), default=None)
        return {**d, "secret": "••••••••", "stats": {"ok_7d": (len(ok) / len(mine)) if mine else None, "deliveries_7d": len(mine), "p95_ms": p95, "last_delivery_at": last}}

    def destination(self, id_: str) -> dict[str, Any]:
        for d in self.store.items("destination"):
            if d["id"] == id_:
                return d
        raise KeyError(id_)

    def _validate_destination(self, body: dict[str, Any], cur: dict[str, Any] | None) -> dict[str, Any]:
        x = dict(cur or {})
        name = str(body.get("name") or x.get("name") or "").strip()
        if not 1 <= len(name) <= 60: raise ValueError("name 需 1–60 字")
        t = str(body.get("type") or x.get("type") or "webhook")
        if t not in TYPES: raise ValueError(f"type 只能是 {TYPES}")
        if t != "webhook": raise ValueError("第一階段只開放 webhook 目的地；kafka／gcs 第二階段")
        url = str(body.get("url") or x.get("url") or "").strip()
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?(/[^\s]*)?", url) and not re.fullmatch(r"http://(127\.0\.0\.1|localhost)(:\d+)?(/[^\s]*)?", url):
            raise ValueError("url 須為 https://（本機測試可用 http://127.0.0.1）")
        ref = str(body.get("secret_ref") or x.get("secret_ref") or "").strip()
        if not SECRET_REF_RE.match(ref): raise ValueError("secret_ref 格式不合法（只接受密鑰庫參照，不接受明文）")
        for k in ("secret", "token", "password"):
            if body.get(k): raise ValueError(f"欄位 {k} 疑似明文密鑰，請改用 secret_ref")
        ident = str(body.get("identifier") or x.get("identifier") or "customer_code")
        if ident not in IDENTIFIERS: raise ValueError(f"identifier 只能是 {IDENTIFIERS}")
        tags = [str(t) for t in (body.get("allowed_tags") if body.get("allowed_tags") is not None else x.get("allowed_tags") or [])]
        if self.tags is not None:
            known = {t["id"] for t in self.tags.list()}
            bad = [t for t in tags if t not in known]
            if bad: raise ValueError(f"allowed_tags 有不存在的標籤：{bad}")
        ml = str(body.get("masking_level") or x.get("masking_level") or "L2")
        if ml not in ("L1", "L2"): raise ValueError("masking_level 只能是 L1／L2")
        ips = [str(i) for i in (body.get("ip_allowlist") if body.get("ip_allowlist") is not None else x.get("ip_allowlist") or [])]
        x.update({"name": name, "type": t, "url": url, "secret_ref": ref, "identifier": ident, "allowed_tags": tags, "masking_level": ml, "ip_allowlist": ips,
                  "mtls": bool(body.get("mtls", x.get("mtls", False))), "note": str(body.get("note") or x.get("note") or "")[:200]})
        return x

    def create_destination(self, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        d = self._validate_destination(body, None)
        d.update({"id": "dst_" + uuid.uuid4().hex[:8], "status": "active", "owner": subject.user_id, "created_at": self._ts(), "updated_at": self._ts(),
                  "first_push_at": None, "consecutive_failures": 0, "secret_ref_prev": None, "rotated_at": None})
        self.store.put_item("destination", d)
        self.store.audit("destination.created", {"user": subject.user_id, "destination": d["id"], "type": d["type"], "url": d["url"]})
        return self._with_stats(d)

    def update_destination(self, id_: str, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        cur = self.destination(id_); d = self._validate_destination(body, cur)
        if d["url"] != cur["url"] or d["secret_ref"] != cur["secret_ref"]:
            d["first_push_at"] = None                                            # 端點或密鑰換了，視同新目的地：首次推送需再核可
        d["updated_at"] = self._ts(); self.store.put_item("destination", d)
        self.store.audit("destination.updated", {"user": subject.user_id, "destination": id_, "changed": sorted(k for k in d if d[k] != cur.get(k))})
        return self._with_stats(d)

    def pause_destination(self, id_: str, subject: Subject) -> dict[str, Any]:
        d = self.destination(id_)
        d["status"] = "active" if d["status"] == "paused" else "paused"
        if d["status"] == "active": d["consecutive_failures"] = 0
        d["updated_at"] = self._ts(); self.store.put_item("destination", d)
        self.store.audit("destination.paused" if d["status"] == "paused" else "destination.resumed", {"user": subject.user_id, "destination": id_})
        return self._with_stats(d)

    def rotate_secret(self, id_: str, body: dict[str, Any], subject: Subject) -> dict[str, Any]:
        d = self.destination(id_); ref = str(body.get("secret_ref") or "").strip()
        if not SECRET_REF_RE.match(ref): raise ValueError("secret_ref 格式不合法")
        if ref == d["secret_ref"]: raise ValueError("新舊 secret_ref 相同")
        d.update({"secret_ref_prev": d["secret_ref"], "secret_ref": ref, "rotated_at": self._ts(), "updated_at": self._ts()})
        self.store.put_item("destination", d)
        self.store.audit("destination.secret_rotated", {"user": subject.user_id, "destination": id_})
        return self._with_stats(d)

    def test_destination(self, id_: str, subject: Subject) -> dict[str, Any]:
        d = self.destination(id_)
        members = [{"customer_code": customer_code(f"test_{i}", self.code_key), "one_id": f"test_{i:04d}", "action": "add", "tags": {t: "測試" for t in d["allowed_tags"][:2]}} for i in range(3)]
        aud = {"id": "audience_test", "name": "測試受眾（假資料）", "version": 0, "computed_at": self._ts()}
        dest = self._dest(d)
        payload = webhook.build_payload(aud, "test", 1, 1, members, dest)
        dlv = self._new_delivery(None, "audience_test", d["id"], 0, "test", "audience.test", {"index": 1, "total": 1, "size": 3}, payload)
        self._attempt(dlv, d)
        self.store.audit("destination.tested", {"user": subject.user_id, "destination": id_, "status": dlv["status"], "response": dlv["response"]})
        return {"delivery": dlv["id"], "status": dlv["status"], "response": dlv["response"]}

    def _dest(self, d: dict[str, Any]) -> Destination:
        try:
            secret = str(self.secret_of(d["secret_ref"])).encode()
        except KeyError:
            raise Conflict(f"secret_ref 無法解析：{d['secret_ref']}（執行節點需掛載密鑰）") from None
        return Destination(d["id"], d["url"], secret, tuple(d["allowed_tags"]), d["identifier"])

    # ---------------------------------------------------------------- 推送單
    def _segment(self, id_: str) -> dict[str, Any]:
        for s in self.store.segments():
            if s["id"] == id_:
                return s
        raise KeyError(f"分群 {id_}")

    def pushes(self, segment_id: str | None = None) -> list[dict[str, Any]]:
        ps = [p for p in self.store.items("push") if p["status"] != "deleted" and (segment_id is None or p["segment_id"] == segment_id)]
        return sorted(ps, key=lambda p: p["created_at"], reverse=True)

    def push(self, id_: str) -> dict[str, Any]:
        for p in self.store.items("push"):
            if p["id"] == id_:
                return p
        raise KeyError(id_)

    def push_detail(self, id_: str) -> dict[str, Any]:
        p = self.push(id_)
        dl = sorted([d for d in self.store.items("delivery") if d.get("push_id") == id_], key=lambda d: d["created_at"], reverse=True)[:20]
        return {**p, "deliveries": [self._public(d) for d in dl]}

    def create_push(self, segment_id: str, body: dict[str, Any], subject: Subject, can_approve: bool) -> dict[str, Any]:
        seg = self._segment(segment_id)
        if seg["status"] not in ("active", "paused"): raise Conflict(f"分群未生效（{seg['status']}），不能推送")
        ids = [str(x) for x in (body.get("destination_ids") or [])]
        if not ids: raise ValueError("destination_ids 至少一個")
        dests = [self.destination(i) for i in ids]
        if any(d["status"] != "active" for d in dests): raise Conflict("有目的地已停用")
        mode = str(body.get("mode") or "delta")
        if mode == "realtime": raise ValueError("即時推送（Flink 規則／即時標籤）排第二階段；請先用增量 + 日批後排程")
        if mode not in MODES: raise ValueError(f"mode 只能是 {MODES}")
        schedule = str(body.get("schedule") or "after_batch")
        if schedule not in SCHEDULES: raise ValueError(f"schedule 只能是 {SCHEDULES}")
        cron = str(body.get("cron") or "")
        if schedule == "cron" and not re.fullmatch(r"[0-2]\d:[0-5]\d", cron): raise ValueError("cron 第一階段只接受每日 HH:MM")
        tags = [str(t) for t in (body.get("include_tags") or [])]
        for d in dests:
            bad = [t for t in tags if t not in d["allowed_tags"]]
            if bad: raise ValueError(f"目的地 {d['name']} 不允許附帶標籤：{bad}")
        first = [d["name"] for d in dests if not d.get("first_push_at")]
        why = ("全量推送需數位經營核准" if mode == "full" else "") or (f"目的地首次推送需核可：{first}" if first else "")
        p = {"id": "push_" + uuid.uuid4().hex[:8], "segment_id": segment_id, "segment_name": seg["name"], "destination_ids": ids, "mode": mode, "schedule": schedule, "cron": cron,
             "include_tags": tags, "status": "active", "approval_id": None, "owner": subject.user_id, "created_at": self._ts(), "updated_at": self._ts(),
             "last_delivered_version": None, "last_run_at": None, "last_result": None, "runs": 0}
        if why and not (can_approve and mode != "full"):
            a = self.approvals.create("push", subject, f"推送「{seg['name']}」→ {'、'.join(d['name'] for d in dests)}（{mode}）：{why}",
                                      {"segment": segment_id, "destinations": ids, "mode": mode, "schedule": schedule, "include_tags": tags, "why": why}, {"kind": "push", "id": p["id"]})
            p["status"] = "pending_approval"; p["approval_id"] = a["id"]
        self.store.put_item("push", p)
        self.store.audit("push.created", {"user": subject.user_id, "push": p["id"], "segment": segment_id, "destinations": ids, "mode": mode, "schedule": schedule, "status": p["status"]})
        if p["status"] == "active" and schedule == "now":
            self.run(p["id"], subject, trigger="now")
            return self.push(p["id"])
        return p

    def _on_decided(self, a: dict[str, Any], approved: bool, subject: Subject) -> None:
        try:
            p = self.push(a["object"]["id"])
        except KeyError:
            return
        p["status"] = "active" if approved else "rejected"; p["updated_at"] = self._ts(); self.store.put_item("push", p)
        if approved and p["schedule"] == "now":
            try:
                self.run(p["id"], subject, trigger="approved")
            except Conflict as e:
                self.store.audit("push.run_skipped", {"push": p["id"], "why": str(e)})

    def pause_push(self, id_: str, subject: Subject) -> dict[str, Any]:
        p = self.push(id_)
        if p["status"] not in ("active", "paused"): raise Conflict(f"推送單狀態 {p['status']} 不能暫停／恢復")
        p["status"] = "paused" if p["status"] == "active" else "active"; p["updated_at"] = self._ts(); self.store.put_item("push", p)
        self.store.audit("push.paused" if p["status"] == "paused" else "push.resumed", {"user": subject.user_id, "push": id_})
        return p

    def delete_push(self, id_: str, subject: Subject) -> None:
        p = self.push(id_)
        if p["runs"]:
            p["status"] = "deleted"; p["updated_at"] = self._ts(); self.store.put_item("push", p)
        else:
            self.store.delete_item("push", id_)
        self.store.audit("push.deleted", {"user": subject.user_id, "push": id_})

    # ---------------------------------------------------------------- 執行
    def _members(self, segment_id: str, version: int) -> list[dict[str, Any]]:
        if self.ch is None: raise Conflict("此環境未連接 ClickHouse")
        txt = self.ch.query(f"SELECT one_id, one_id_uint FROM dmp.segment_members WHERE segment_id = {q(segment_id)} AND version = {int(version)} ORDER BY one_id FORMAT JSONEachRow",
                            settings={"max_execution_time": 600, "output_format_json_quote_64bit_integers": 0})
        return [json.loads(line) for line in txt.splitlines() if line.strip()]

    def _tag_values(self, tag_ids: list[str], uints: list[int]) -> dict[int, dict[str, Any]]:
        if not tag_ids or not uints or self.tags is None: return {}
        out: dict[int, dict[str, Any]] = {}
        for tid in tag_ids:
            t = self.tags.get(tid); v = (t.get("latest") or {})
            if not v.get("version") or v.get("status") != "success": continue
            for i in range(0, len(uints), 5000):
                arr = ",".join(str(u) for u in uints[i:i + 5000])
                txt = self.ch.query(f"SELECT tag_value, bitmapToArray(bitmapAnd(groupBitmapMergeState(users), bitmapBuild([{arr}]))) AS us FROM dmp.user_tags "
                                    f"WHERE tag_id = {q(tid)} AND version = {int(v['version'])} GROUP BY tag_value FORMAT JSONEachRow",
                                    settings={"max_execution_time": 600, "output_format_json_quote_64bit_integers": 0})
                for line in txt.splitlines():
                    if not line.strip(): continue
                    row = json.loads(line); val = bucket_number(row["tag_value"]) if t.get("dtype") == "number" else row["tag_value"]
                    for u in row.get("us") or []:
                        out.setdefault(int(u), {})[tid] = val
        return out

    def run(self, id_: str, subject: Subject, trigger: str = "manual") -> dict[str, Any]:
        p = self.push(id_)
        if p["status"] != "active": raise Conflict(f"推送單未生效（{p['status']}）")
        seg = self._segment(p["segment_id"])
        vs = [v for v in seg.get("versions", []) if v["status"] == "success"]
        if not vs: raise Conflict("分群尚無計算結果")
        ver = int(vs[-1]["version"]); cur = self._members(seg["id"], ver)
        prev: list[dict[str, Any]] = []
        mode = p["mode"]
        if mode == "delta" and p.get("last_delivered_version") and int(p["last_delivered_version"]) != ver:
            try:
                prev = self._members(seg["id"], int(p["last_delivered_version"]))
            except Exception:
                prev = []                                                     # 舊版本已被保留數清掉 → 當作首次：整份送
        elif mode == "delta" and p.get("last_delivered_version") == ver:
            prev = cur                                                        # 同版本再跑：沒有變動
        cur_by = {m["one_id"]: m for m in cur}; prev_by = {m["one_id"]: m for m in prev}
        if mode == "delta" and prev:
            adds = [cur_by[k] for k in cur_by if k not in prev_by]; removes = [prev_by[k] for k in prev_by if k not in cur_by]
        else:
            adds = list(cur); removes = []
        tags = self._tag_values(p["include_tags"], [int(m["one_id_uint"]) for m in adds]) if p["include_tags"] else {}
        members = [{"customer_code": customer_code(m["one_id"], self.code_key), "one_id": m["one_id"], "action": "add", "tags": tags.get(int(m["one_id_uint"]), {})} for m in adds] + \
                  [{"customer_code": customer_code(m["one_id"], self.code_key), "one_id": m["one_id"], "action": "remove"} for m in removes]
        aud = {"id": seg["id"], "name": seg["name"], "version": ver, "computed_at": vs[-1]["computed_at"]}
        result = {"version": ver, "added": len(adds), "removed": len(removes), "batches": 0, "ok": 0, "failed": 0, "destinations": {}}
        event = "audience.delta" if mode == "delta" and prev else "audience.full"
        for did in p["destination_ids"]:
            d = self.destination(did)
            if d["status"] != "active":
                result["destinations"][did] = "paused"; continue
            dest = self._dest(d)
            bs = list(webhook.batches(members)) if members else []
            ok = 0
            for i, b in enumerate(bs, 1):
                payload = webhook.build_payload(aud, "delta" if event == "audience.delta" else "full", i, len(bs), b, dest)
                dlv = self._new_delivery(p["id"], seg["id"], did, ver, "delta" if event == "audience.delta" else "full", event, {"index": i, "total": len(bs), "size": len(b)}, payload)
                self._attempt(dlv, d); result["batches"] += 1
                if dlv["status"] == "ok": ok += 1
                else: result["failed"] += 1
            if event == "audience.full" and bs:
                comp = {"audience_id": seg["id"], "audience_name": seg["name"], "version": ver, "mode": "full", "total": len(members), "batches": len(bs),
                        "checksum": hashlib.sha256("\n".join(m["customer_code"] for m in members).encode()).hexdigest()}
                dlv = self._new_delivery(p["id"], seg["id"], did, ver, "full", "audience.complete", {"index": len(bs) + 1, "total": len(bs) + 1, "size": 0}, comp)
                self._attempt(dlv, d); result["batches"] += 1
                if dlv["status"] == "ok": ok += 1
                else: result["failed"] += 1
            result["ok"] += ok; result["destinations"][did] = "ok" if ok == len(bs) + (1 if event == "audience.full" and bs else 0) else "partial"
            if not d.get("first_push_at"):
                d["first_push_at"] = self._ts(); self.store.put_item("destination", d)
        p.update({"last_delivered_version": ver, "last_run_at": self._ts(), "last_result": result, "runs": int(p.get("runs", 0)) + 1, "updated_at": self._ts()})
        self.store.put_item("push", p)
        self.store.audit("push.ran", {"user": subject.user_id, "push": p["id"], "segment": seg["id"], "version": ver, "trigger": trigger, **{k: result[k] for k in ("added", "removed", "batches", "ok", "failed")}})
        return result

    # ---------------------------------------------------------------- 交付
    def _new_delivery(self, push_id: str | None, segment_id: str, dest_id: str, version: int, mode: str, event: str, batch: dict[str, int], payload: dict[str, Any]) -> dict[str, Any]:
        d = {"id": f"dlv_{uuid.uuid4().hex[:16]}", "push_id": push_id, "segment_id": segment_id, "destination_id": dest_id, "version": version, "mode": mode, "event": event, "batch": batch,
             "members": len(payload.get("members") or []), "status": "retrying", "attempts": 0, "next_retry_at": None, "response": None, "created_at": self._ts(), "updated_at": self._ts(),
             "replay_of": None, "payload": payload}
        self.store.put_item("delivery", d)
        return d

    def _attempt(self, dlv: dict[str, Any], d: dict[str, Any]) -> None:
        try:
            dest = self._dest(d)
            req = webhook.deliver(dest, dlv["event"], dlv["payload"], delivery_id=dlv["id"])
            status, ms, text = self.transport(req["url"], req["headers"], req["body"], TIMEOUT_S)
        except Conflict as e:
            status, ms, text = None, 0, str(e)
        dlv["attempts"] += 1; dlv["response"] = {"status": status, "ms": ms, "body": text[:300]}; dlv["updated_at"] = self._ts()
        ok = status is not None and 200 <= status < 300
        if ok:
            dlv["status"] = "ok"; dlv["next_retry_at"] = None; dlv.pop("payload", None)
            d["consecutive_failures"] = 0; self.store.put_item("destination", d)
            self.store.audit("sync.delivered", {"delivery": dlv["id"], "destination": d["id"], "segment": dlv["segment_id"], "version": dlv["version"], "hook_event": dlv["event"], "members": dlv["members"], "ms": ms})
        else:
            n = dlv["attempts"]
            if n > len(RETRY_SCHEDULE):
                dlv["status"] = "dlq"; dlv["next_retry_at"] = None
            else:
                dlv["status"] = "retrying"; dlv["next_retry_at"] = (self.now() + timedelta(seconds=RETRY_SCHEDULE[n - 1])).isoformat(timespec="seconds")
            if dlv["mode"] != "test":
                d["consecutive_failures"] = int(d.get("consecutive_failures", 0)) + 1
                if d["consecutive_failures"] >= PAUSE_AFTER and d["status"] == "active":
                    d["status"] = "paused"; self.store.audit("destination.auto_paused", {"destination": d["id"], "failures": d["consecutive_failures"]})
                self.store.put_item("destination", d)
            self.store.audit("sync.failed", {"delivery": dlv["id"], "destination": d["id"], "segment": dlv["segment_id"], "hook_event": dlv["event"], "attempt": n, "status": status, "next_retry_at": dlv["next_retry_at"], "final": dlv["status"] == "dlq"})
        self.store.put_item("delivery", dlv)

    @staticmethod
    def _public(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if k != "payload"}

    def deliveries(self, audience_id: str | None = None, status: str | None = None, destination_id: str | None = None, n: int = 200) -> list[dict[str, Any]]:
        dl = [d for d in self.store.items("delivery") if (audience_id is None or d["segment_id"] == audience_id) and (status is None or d["status"] == status) and (destination_id is None or d["destination_id"] == destination_id)]
        return [self._public(d) for d in sorted(dl, key=lambda d: d["created_at"], reverse=True)[:n]]

    def replay(self, id_: str, subject: Subject) -> dict[str, Any]:
        dlv = next((d for d in self.store.items("delivery") if d["id"] == id_), None)
        if dlv is None: raise KeyError(id_)
        if dlv["status"] == "ok": raise Conflict("交付已成功，不需重放")
        if "payload" not in dlv: raise Conflict("交付內容已清除，無法重放")
        d = self.destination(dlv["destination_id"])
        dlv["attempts"] = min(dlv["attempts"], len(RETRY_SCHEDULE) - 1); dlv["replay_of"] = dlv["replay_of"] or id_    # 沿用同一 Delivery-Id（接收端冪等）
        self._attempt(dlv, d)
        self.store.audit("sync.replayed", {"user": subject.user_id, "delivery": id_, "status": dlv["status"]})
        return self._public(dlv)

    # ---------------------------------------------------------------- 例行
    def tick(self, subject: Subject, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.now(); ts = now.isoformat(timespec="seconds"); out: dict[str, Any] = {"retried": [], "ran": []}
        for dlv in self.store.items("delivery"):
            if dlv["status"] == "retrying" and dlv.get("next_retry_at") and dlv["next_retry_at"] <= ts and "payload" in dlv:
                d = self.destination(dlv["destination_id"])
                if d["status"] != "active": continue
                self._attempt(dlv, d); out["retried"].append({"delivery": dlv["id"], "status": dlv["status"]})
        for p in self.pushes():
            if p["status"] != "active" or p["schedule"] == "now": continue
            try:
                seg = self._segment(p["segment_id"])
            except KeyError:
                continue
            vs = [v for v in seg.get("versions", []) if v["status"] == "success"]
            if not vs or (p.get("last_delivered_version") and int(p["last_delivered_version"]) >= int(vs[-1]["version"])): continue
            if p["schedule"] == "cron":
                hh, mm = (int(x) for x in p["cron"].split(":"))
                if (now.hour, now.minute) < (hh, mm): continue
            try:
                out["ran"].append({"push": p["id"], **{k: v for k, v in self.run(p["id"], subject, trigger="routine").items() if k != "destinations"}})
            except Conflict as e:
                out["ran"].append({"push": p["id"], "skipped": str(e)})
        return out
