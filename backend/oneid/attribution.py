"""OneID 歸戶：接入時逐筆決定事件屬於哪個用戶（參考實作，可直接執行自測）。

對齊神策 IDM 3.0 的運作方式（docs/19）：
- 一個 ID 同一時刻只屬於一個用戶；不自動合併兩個已存在的用戶。
- 同一條資料多個 ID 分屬不同用戶 → 歸優先級最高的 ID 所屬用戶；可改綁（rebind=latest）的 ID 改綁到該用戶。
- 純匿名用戶首次與「尚無用戶」的強 ID 共現 → 升級為該客戶（歷史匿名事件自動歸戶，不需改寫事件）。
- 未登入的行為事件以 distinct_id（cookie／裝置／匿名 ID）歸到匿名用戶；登入事件到達時歸戶，
  並把同 distinct_id、近 7 天內仍屬匿名用戶的事件改寫到登入客戶（login_backfill）；更早的或已歸客戶的事件不動。
- 除此之外沒有夜間改寫（人工合併／拆分例外）。
規則來源：backend/semantic/oneid_rules.yaml。

v1.2（docs/19、semantic/entities.yaml）：多主體與血緣
- 每條 ID 關聯是一條有來源、證據、時間、信心、狀態的邊（dmp.id_edges）；血緣圖與衝突清單從邊來。
- 弱 ID（裝置／cookie／匿名）不自動改綁（神策 IDM 2.0／3.0 實證：第二個客戶在同裝置登入 → 關聯失敗、事件歸登入客戶、裝置留在原主人）；
  弱 ID 屬匿名用戶時整個匿名用戶併入客戶並改寫近 7 天事件（神策方案三）。共用裝置（嘗試關聯 ≥ 門檻個客戶）：邊記 conflict、信心打折。
- 傳遞性映射（VeCDP 式信心門檻）保留為選項，預設關閉。
- 人工合併／拆分／撤銷：前後快照寫 oneid_merge_log，需審批角色；事件改寫筆數記錄。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SEM = Path(__file__).resolve().parents[1] / "semantic"
RULES = yaml.safe_load((_SEM / "oneid_rules.yaml").read_text(encoding="utf-8"))
ENT = yaml.safe_load((_SEM / "entities.yaml").read_text(encoding="utf-8"))
SUBJECT_OF = {i: sub["key"] for sub in ENT["subjects"] for i in sub["ids"]}
SHARED_THRESHOLD = next(sub.get("shared_threshold", 3) for sub in ENT["subjects"] if sub["key"] == "device")
CONF = ENT["edge"]["confidence"]
TRANS = ENT["transitive"]
MERGE_APPROVAL = ENT["merge_log"]["approval"]
ID_SPEC = {i["key"]: i for i in RULES["ids"]}
ALIAS = RULES.get("alias", {})
STRONG = set(RULES["strong_ids"])
ANON_KEYS = RULES["anonymous_user"]["create_from"]
BACKFILL = RULES.get("login_backfill", {"enabled": False})
WINDOW_MS = int(BACKFILL.get("window_days", 0)) * 86_400_000


@dataclass
class User:
    one_id: str
    one_id_uint: int
    ids: dict[str, set[str]] = field(default_factory=dict)      # key → values
    bind_order: dict[str, list[str]] = field(default_factory=dict)   # key → 綁定順序（滾動解綁用）

    @property
    def is_identified(self) -> bool:
        """已識別客戶 = 綁有客戶代碼；只有帳號／手機／弱 ID 的用戶在客戶代碼到達時會「升級」。"""
        return bool(self.ids.get("$identity_login_id"))

    def has_slot(self, key: str, value: str) -> bool:
        """單值類型：已有不同值就沒有空位；多值：永遠有。"""
        cur = self.ids.get(key, set())
        if value in cur:
            return True
        return ID_SPEC[key]["cardinality"] == "multi" or not cur


@dataclass
class Decision:
    one_id: str
    created: bool
    upgraded: bool                      # 匿名用戶升級為客戶
    bound: list[tuple[str, str]]        # 新建立的綁定
    rebound: list[tuple[str, str, str]] # (key, value, from_one_id)
    conflicts: list[str]
    reason: str
    backfilled: int = 0                 # 登入改寫：近 7 天匿名事件改寫到客戶的筆數


class OneIDService:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.index: dict[tuple[str, str], str] = {}   # (key, value) → one_id
        self.seq = 0
        self.audit: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []        # 參考用事件表：{time, event, distinct_id, one_id}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}   # dmp.id_edges（記憶體版）
        self.device_customers: dict[tuple[str, str], set[str]] = {}       # 裝置 ID → 曾綁定的已識別客戶
        self.merge_log: list[dict[str, Any]] = []     # dmp.oneid_merge_log
        self.lineage: set[tuple[str, str, str, str, str]] = set()        # dmp.lineage_edges：(from_kind, from, to_kind, to, via)
        self._ctx: dict[str, Any] = {"time": 0, "source": "cooccur", "evidence": ""}

    # ---- helpers
    def _new_user(self) -> User:
        self.seq += 1
        u = User(one_id=f"oid_{self.seq:06d}", one_id_uint=self.seq)
        self.users[u.one_id] = u
        return u

    def _bind(self, u: User, key: str, value: str) -> None:
        u.ids.setdefault(key, set()).add(value)
        self.index[(key, value)] = u.one_id
        u.bind_order.setdefault(key, []).append(value)
        limit = ID_SPEC[key].get("max_count")
        if limit and ID_SPEC[key].get("over_limit", "rolling_unbind") == "rolling_unbind" and len(u.ids[key]) > limit:
            oldest = u.bind_order[key].pop(0)                       # 神策超限策略「滾動解綁」：刪除最早的標識，新的繼續關聯
            u.ids[key].discard(oldest); self.index.pop((key, oldest), None)
            self.audit.append({"event": "oneid.rolling_unbound", "one_id": u.one_id, "key": key, "value": oldest, "limit": limit})
        if SUBJECT_OF.get(key) == "device" and u.is_identified:
            self.device_customers.setdefault((key, value), set()).add(u.one_id)

    # ---- 邊（血緣）
    def is_shared_device(self, key: str, value: str) -> bool:
        return SUBJECT_OF.get(key) == "device" and len(self.device_customers.get((key, value), ())) >= SHARED_THRESHOLD

    def _edge(self, a: tuple[str, str], b: tuple[str, str], one_id: str, *, source: str | None = None, status: str = "active") -> dict[str, Any]:
        """記一條邊（無向：以 (type, id) 排序後當鍵）。重複出現 seen_count+1、信心加成；共用裝置打折。"""
        (ta, va), (tb, vb) = sorted([a, b])
        k = (ta, va, tb, vb); src = source or self._ctx["source"]; now = self._ctx["time"]
        base = CONF["base"].get(src, 0.6)
        e = self.edges.get(k)
        if e is None:
            e = self.edges[k] = {"source": src, "evidence_event_id": self._ctx["evidence"], "first_seen": now, "last_seen": now, "seen_count": 0,
                                 "confidence": base, "status": status, "one_id": one_id}
        e["seen_count"] += 1; e["last_seen"] = now; e["one_id"] = one_id; e["status"] = status if status != "active" else e["status"]
        if e["status"] == "superseded": e["status"] = "active"
        conf = base + CONF["repeat_bonus_per_seen"] * (e["seen_count"] - 1) if src.endswith("cooccur") else base
        conf = min(conf, 0.95 if src.endswith("cooccur") else 1.0)
        if any(self.is_shared_device(t, v) for t, v in (a, b)):
            conf *= 1 - CONF["shared_device_penalty"]; e["status"] = "conflict"; e["conflict_kind"] = "shared_device"
        e["confidence"] = round(conf, 3)
        return e

    def _record_edges(self, ids: list[tuple[str, str]], u: User) -> None:
        """同一筆資料裡的 ID 兩兩記邊：含錨 ID 的邊用資料來源（login／bind），弱–弱用 cooccur。"""
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if self.index.get(a) != u.one_id or self.index.get(b) != u.one_id:
                    continue
                strong = a[0] in STRONG or b[0] in STRONG
                self._edge(a, b, u.one_id, source=self._ctx["source"] if strong else (self._ctx["source"].split(".")[0] + ".cooccur"))
        self.lineage.add(("source", self._ctx["source"].split(".")[0], "id_map", "dmp.id_map", "oneid.service"))

    def _follow_transitive(self, moved: tuple[str, str], from_user: User, to_user: User, time_ms: int) -> list[tuple[str, str]]:
        """弱 ID moved 從 from_user 改綁到 to_user 後：from_user 其他弱 ID 若與 moved 的共現路徑（≤ max_hops）信心乘積 ≥ 門檻，且不是共用裝置 → 一起改綁。"""
        if not TRANS.get("enabled") or not TRANS.get("follow_on_rebind") or from_user.is_identified:
            return []
        weak = [(k, v) for k, vs in from_user.ids.items() for v in vs if k not in STRONG and SUBJECT_OF.get(k) == "device"]
        best: dict[tuple[str, str], float] = {moved: 1.0}; frontier = [moved]
        for _ in range(TRANS["max_hops"]):
            nxt = []
            for cur in frontier:
                for cand in weak:
                    if cand in best: continue
                    (ta, va), (tb, vb) = sorted([cur, cand]); e = self.edges.get((ta, va, tb, vb))
                    if not e or e["status"] == "conflict": continue
                    c = best[cur] * e["confidence"]
                    if c >= TRANS["min_confidence"]:
                        best[cand] = c; nxt.append(cand)
            frontier = nxt
        followed = []
        for cand, c in best.items():
            if cand == moved or self.is_shared_device(*cand) or not to_user.has_slot(*cand): continue
            self._unbind(from_user, *cand); self._bind(to_user, *cand); followed.append(cand)
            self._edge(cand, moved, to_user.one_id, source="transitive")
            self.audit.append({"event": "oneid.transitive_rebind", "id": cand, "via": moved, "confidence": round(c, 3), "from": from_user.one_id, "to": to_user.one_id})
            if BACKFILL.get("enabled"):
                self._login_backfill(cand[1], to_user.one_id, time_ms)
        return followed

    def _unbind(self, u: User, key: str, value: str) -> None:
        u.ids.get(key, set()).discard(value)
        self.index.pop((key, value), None)
        if value in u.bind_order.get(key, []): u.bind_order[key].remove(value)

    def _would_exceed(self, u: User, key: str) -> bool:
        """超限策略 intercept：多值 ID 已達上限 → 這筆資料的新 ID 不綁、記 conflict（神策「數據攔截」：整條數據攔截，已關聯的標識不變）"""
        limit = ID_SPEC[key].get("max_count")
        return bool(limit) and ID_SPEC[key].get("over_limit") == "intercept" and len(u.ids.get(key, ())) >= limit

    @staticmethod
    def normalize(identities: dict[str, Any]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for k, v in identities.items():
            k = ALIAS.get(k, k)
            if k not in ID_SPEC or not ID_SPEC[k].get("participates", True) or ID_SPEC[k].get("status", "enabled") == "disabled" or v in (None, ""):
                continue
            for val in (v if isinstance(v, list) else [v]):
                out.append((k, str(val)))
        return sorted(set(out), key=lambda kv: ID_SPEC[kv[0]]["priority"])

    @staticmethod
    def distinct_id(ids: list[tuple[str, str]]) -> str | None:
        """未登入事件的 distinct_id = 資料裡第一個弱 ID（Web: cookie_id；App: device_id；Collector: anonymous_id）。"""
        for key in ANON_KEYS:
            for k, v in ids:
                if k == key:
                    return v
        return None

    def track(self, event: str, time_ms: int, identities: dict[str, Any], *, consent: bool = True, source: str = "ga360", evidence: str = "") -> Decision:
        """一筆事件：先歸戶（resolve），寫入事件表；若是登入事件則把近 7 天同 distinct_id 的匿名事件改寫到客戶。"""
        ids = self.normalize(identities)
        did = self.distinct_id(ids)
        is_login_rec = any(k == "$identity_login_id" for k, _ in ids)
        d = self.resolve(identities, consent=consent, time_ms=time_ms, source=f"{source}.login" if is_login_rec else f"{source}.cooccur", evidence=evidence or f"{event}@{time_ms}")
        self.events.append({"time": time_ms, "event": event, "distinct_id": did, "one_id": d.one_id})
        is_login = event in BACKFILL.get("trigger_events", []) or any(k == "$identity_login_id" for k, _ in ids)
        if BACKFILL.get("enabled") and is_login and consent and did and self.users[d.one_id].is_identified:
            d.backfilled = self._login_backfill(did, d.one_id, time_ms)
        return d

    def _login_backfill(self, did: str, target: str, login_ms: int) -> int:
        """改寫範圍：同 distinct_id、event time 在 [登入-7天, 登入]、目前仍屬「未識別用戶」的事件。已歸任何客戶的事件不動。"""
        n = 0; lo = login_ms - WINDOW_MS
        for ev in self.events:
            if ev["distinct_id"] == did and lo <= ev["time"] <= login_ms and ev["one_id"] != target and not self.users[ev["one_id"]].is_identified:
                ev["one_id"] = target; n += 1
        if n:
            self.audit.append({"event": "oneid.backfilled", "distinct_id": did, "one_id": target, "rows": n, "window_days": BACKFILL["window_days"]})
        return n

    # ---- core
    def resolve(self, identities: dict[str, Any], *, consent: bool = True, time_ms: int = 0, source: str = "crm.bind", evidence: str = "") -> Decision:
        self._ctx = {"time": time_ms, "source": source, "evidence": evidence}
        ids = self.normalize(identities)
        logins = {v for k, v in ids if k == "$identity_login_id"}
        if len(logins) > 1:
            self.audit.append({"event": "oneid.rejected", "kind": "anchor_mismatch", "ids": sorted(logins)})
            raise ValueError(f"同一筆資料有兩個客戶代碼 {sorted(logins)}：拒收（anchor_mismatch）")
        if not consent:   # 未同意：弱 ID 不與強 ID 建立關聯
            strong = [kv for kv in ids if kv[0] in STRONG]
            ids = strong if strong else ids
        if not ids:
            raise ValueError("資料沒有任何可參與歸戶的 ID")
        owners = {kv: self.index.get(kv) for kv in ids}
        found = [o for o in owners.values() if o]
        distinct = list(dict.fromkeys(found))
        bound, rebound, conflicts = [], [], []

        if not distinct:                                   # 全部是新 ID → 新用戶
            u = self._new_user()
            for k, v in ids:
                self._bind(u, k, v); bound.append((k, v))
            self._record_edges(ids, u)
            return self._done(u, True, False, bound, rebound, conflicts, "all_new")

        # 優先級最高的「已有用戶的 ID」決定歸屬；但若資料帶的最高優先 ID 尚無用戶且是強 ID，而唯一找到的用戶是純匿名 → 升級
        top_key, top_val = ids[0]
        top_owner = owners[(top_key, top_val)]
        upgraded = False
        if top_owner is None and len(distinct) == 1 and self.users[distinct[0]].has_slot(top_key, top_val):
            # 資料裡最高優先的 ID 是新的，且唯一找到的用戶在該類型還有空位（例：純匿名用戶首次帶客戶代碼、只有帳號的用戶首次帶客戶代碼）→ 綁上去；若該用戶原本不是已識別客戶，這就是「首次關聯升級」
            winner = self.users[distinct[0]]; upgraded = not winner.is_identified
        elif top_owner is None and top_key in STRONG:
            # 最高優先的強 ID 是新的，但找到的用戶該類型已有別的值或有多個用戶（例：新客戶用別人的手機／共用裝置）→ 不併入舊用戶，為新強 ID 建新用戶
            winner = self._new_user()
        else:
            # 歸屬 = 資料中優先級最高且已有用戶的 ID 的用戶
            winner = self.users[next(o for kv, o in owners.items() if o)]

        for k, v in ids:
            cur = owners[(k, v)]
            if cur == winner.one_id:
                continue
            if cur is None:
                if self._would_exceed(winner, k):
                    conflicts.append(f"{k}={v} 超過關聯上限 {ID_SPEC[k]['max_count']}（intercept）：不綁定，待查")
                    self._edge((k, v), ids[0], winner.one_id, status="conflict")["conflict_kind"] = "over_limit"
                elif winner.has_slot(k, v):
                    self._bind(winner, k, v); bound.append((k, v))
                else:
                    conflicts.append(f"{k}={v} 無法綁定：{winner.one_id} 的單值欄位已有 {sorted(winner.ids[k])}")
                continue
            # ID 屬於另一個用戶：依 rebind 政策
            policy = ID_SPEC[k]["rebind"]
            prev = self.users[cur]
            if policy in ("latest", "never_auto") and not prev.is_identified and winner.is_identified and RULES["anonymous_user"].get("merge_into_existing_customer", True):
                # 弱 ID 屬匿名用戶、資料帶已存在的客戶 → 整個匿名用戶併入客戶（神策方案三），所有弱 ID 跟著走，近 7 天事件改寫
                for mk, mvs in list(prev.ids.items()):
                    for mv in list(mvs):
                        if winner.has_slot(mk, mv):
                            self._unbind(prev, mk, mv); self._bind(winner, mk, mv); rebound.append((mk, mv, cur))
                            for ek, e in self.edges.items():
                                if ((ek[0], ek[1]) == (mk, mv) or (ek[2], ek[3]) == (mk, mv)) and e["one_id"] == cur: e["one_id"] = winner.one_id
                            if (mk, mv) != (k, v) and BACKFILL.get("enabled"):
                                self._login_backfill(mv, winner.one_id, self._ctx["time"])
                self.audit.append({"event": "oneid.anonymous_merged", "from": cur, "to": winner.one_id, "via": (k, v)})
                if TRANS.get("enabled"):
                    self._follow_transitive((k, v), prev, winner, self._ctx["time"])
            elif policy == "latest" and winner.has_slot(k, v):
                self._unbind(prev, k, v); self._bind(winner, k, v); rebound.append((k, v, cur))
            elif policy == "never_auto":
                # 弱 ID 已屬另一個已識別客戶：關聯失敗，事件仍歸 winner（登入客戶）；裝置留在原主人；記 conflict（共用裝置）
                conflicts.append(f"{k}={v} 已屬 {cur}（已識別客戶），關聯失敗：事件歸 {winner.one_id}，裝置不改綁（神策 IDM 同）")
                e = self._edge((k, v), ids[0], cur, status="conflict"); e["conflict_kind"] = "shared_device"
                if SUBJECT_OF.get(k) == "device" and winner.is_identified:
                    self.device_customers.setdefault((k, v), set()).add(winner.one_id)
            elif policy == "never":
                conflicts.append(f"{k}={v} 屬於 {cur}，不可改綁（資料仍歸 {winner.one_id}）")
            else:
                conflicts.append(f"{k}={v} 屬於 {cur}，政策 {policy}：保留原綁定、記 conflict 待人工")
                self._edge((k, v), ids[0], cur, status="conflict")["conflict_kind"] = "mobile_second_customer" if k == "$identity_mobile_hash" else "account_other_customer"
        self._record_edges(ids, winner)
        return self._done(winner, winner.one_id not in {*distinct} and not upgraded, upgraded, bound, rebound, conflicts, "priority_owner")

    def _done(self, u: User, created: bool, upgraded: bool, bound, rebound, conflicts, reason: str) -> Decision:
        d = Decision(u.one_id, created, upgraded, bound, rebound, conflicts, reason)
        if bound or rebound or conflicts or created:
            self.audit.append({"event": "oneid.resolved", "one_id": u.one_id, "created": created, "upgraded": upgraded, "bound": bound, "rebound": rebound, "conflicts": conflicts})
        return d

    # ---- 人工工具：合併／拆分／撤銷（entities.yaml merge_log）
    def _snapshot(self, oid: str) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in self.users[oid].ids.items() if v}

    def merge(self, src: str, dst: str, *, by: str, approved_by: str, approver_role: str, reason: str) -> str:
        """把 src 併入 dst：單值欄位兩邊都有且不同 → 拒絕；事件 one_id 改寫；前後快照寫 merge_log。"""
        if approver_role not in MERGE_APPROVAL["merge"]:
            raise PermissionError(f"合併需 {MERGE_APPROVAL['merge']} 審批")
        s_u, d_u = self.users[src], self.users[dst]
        for k in s_u.ids:
            if ID_SPEC[k]["cardinality"] == "single" and s_u.ids[k] and d_u.ids.get(k) and s_u.ids[k] != d_u.ids[k]:
                raise ValueError(f"無法合併：{k} 兩邊不同（{sorted(s_u.ids[k])} vs {sorted(d_u.ids[k])}）")
        before = {src: self._snapshot(src), dst: self._snapshot(dst)}
        moved = [(k, v) for k, vs in s_u.ids.items() for v in list(vs)]
        for k, v in moved:
            self._unbind(s_u, k, v); self._bind(d_u, k, v)
            for ek, e in self.edges.items():
                if e["one_id"] == src and ((ek[0], ek[1]) == (k, v) or (ek[2], ek[3]) == (k, v)): e["one_id"] = dst
        rewritten = [i for i, ev in enumerate(self.events) if ev["one_id"] == src]
        for i in rewritten: self.events[i]["one_id"] = dst
        log_id = f"ml_{len(self.merge_log) + 1:05d}"
        self.merge_log.append({"log_id": log_id, "op": "merge", "src_one_id": src, "dst_one_id": dst, "ids_moved": moved, "events_rewritten": len(rewritten),
                               "event_idx": rewritten, "snapshot_before": before, "snapshot_after": {dst: self._snapshot(dst)}, "reason": reason,
                               "requested_by": by, "approved_by": approved_by, "undone_by_log_id": ""})
        self.audit.append({"event": "oneid.merged", "log_id": log_id, "src": src, "dst": dst, "ids": len(moved), "events": len(rewritten)})
        return log_id

    def split(self, one_id: str, ids: list[tuple[str, str]], *, from_time: int, by: str, approved_by: str, approver_role: str, reason: str) -> tuple[str, str]:
        """把指定 ID 從 one_id 拆成新用戶；這些 ID 作為 distinct_id、from_time 之後的事件改歸新用戶。錨 ID 不可拆。"""
        if approver_role not in MERGE_APPROVAL["split"]:
            raise PermissionError(f"拆分需 {MERGE_APPROVAL['split']} 審批")
        if any(k == "$identity_login_id" for k, _ in ids):
            raise ValueError("客戶代碼不可拆分")
        u = self.users[one_id]; before = {one_id: self._snapshot(one_id)}
        nu = self._new_user()
        for k, v in ids:
            self._unbind(u, k, v); self._bind(nu, k, v)
        dids = {v for _, v in ids}
        rewritten = [i for i, ev in enumerate(self.events) if ev["one_id"] == one_id and ev["distinct_id"] in dids and ev["time"] >= from_time]
        for i in rewritten: self.events[i]["one_id"] = nu.one_id
        log_id = f"ml_{len(self.merge_log) + 1:05d}"
        self.merge_log.append({"log_id": log_id, "op": "split", "src_one_id": one_id, "dst_one_id": nu.one_id, "ids_moved": ids, "events_rewritten": len(rewritten),
                               "event_idx": rewritten, "snapshot_before": before, "snapshot_after": {one_id: self._snapshot(one_id), nu.one_id: self._snapshot(nu.one_id)},
                               "reason": reason, "requested_by": by, "approved_by": approved_by, "undone_by_log_id": ""})
        self.audit.append({"event": "oneid.split", "log_id": log_id, "from": one_id, "to": nu.one_id, "ids": len(ids), "events": len(rewritten)})
        return log_id, nu.one_id

    def undo(self, log_id: str, *, by: str, approver_role: str) -> str:
        """撤銷一次合併或拆分：把 ID 與事件搬回去（依紀錄的快照與事件索引）。"""
        if approver_role not in MERGE_APPROVAL["undo"]:
            raise PermissionError("撤銷需審批")
        rec = next(r for r in self.merge_log if r["log_id"] == log_id)
        if rec["undone_by_log_id"]:
            raise ValueError("已撤銷")
        src, dst = rec["src_one_id"], rec["dst_one_id"]
        for k, v in rec["ids_moved"]:
            self._unbind(self.users[dst], k, v); self._bind(self.users[src], k, v)
        for i in rec["event_idx"]: self.events[i]["one_id"] = src
        new_id = f"ml_{len(self.merge_log) + 1:05d}"; rec["undone_by_log_id"] = new_id
        self.merge_log.append({"log_id": new_id, "op": "undo", "src_one_id": dst, "dst_one_id": src, "ids_moved": rec["ids_moved"], "events_rewritten": len(rec["event_idx"]),
                               "event_idx": rec["event_idx"], "snapshot_before": rec["snapshot_after"], "snapshot_after": rec["snapshot_before"], "reason": f"undo {log_id}",
                               "requested_by": by, "approved_by": by, "undone_by_log_id": ""})
        return new_id

    def graph(self, one_id: str) -> dict[str, Any]:
        """血緣圖資料（原型「身分圖」）：節點 = 該用戶的 ID（含主體），邊 = id_edges，時間軸 = 審計裡與此用戶有關的事件。"""
        u = self.users[one_id]
        nodes = [{"id": f"{k}:{v}", "type": k, "subject": SUBJECT_OF.get(k, "?"), "shared": self.is_shared_device(k, v)} for k, vs in u.ids.items() for v in sorted(vs)]
        edges = [{"a": f"{ek[0]}:{ek[1]}", "b": f"{ek[2]}:{ek[3]}", **{x: e[x] for x in ("source", "evidence_event_id", "first_seen", "last_seen", "seen_count", "confidence", "status")}}
                 for ek, e in self.edges.items() if e["one_id"] == one_id]
        timeline = [a for a in self.audit if a.get("one_id") == one_id or a.get("to") == one_id or a.get("dst") == one_id or a.get("src") == one_id or a.get("from") == one_id]
        return {"one_id": one_id, "identified": u.is_identified, "nodes": nodes, "edges": edges, "timeline": timeline, "events": sum(1 for e in self.events if e["one_id"] == one_id)}

    def unbind(self, key: str, value: str, *, by: str) -> None:
        if key in RULES["manual_tools"]["unbind"]["blocked_for"]:
            raise PermissionError(f"{key} 不可解綁")
        oid = self.index.get((key, value))
        if oid:
            u = self.users[oid]
            self._unbind(u, key, value)
            self.audit.append({"event": "oneid.unbound", "key": key, "one_id": oid, "by": by})
            for ck, spec in ID_SPEC.items():                      # 父節點：父 ID 解綁時，子類型的 ID 一併解綁（神策「父節點」）
                if spec.get("parent") == key:
                    for cv in list(u.ids.get(ck, ())):
                        self._unbind(u, ck, cv); self.audit.append({"event": "oneid.unbound", "key": ck, "one_id": oid, "by": by, "cascade_from": key})


# ------------------------------------------------------------------ 自測：docs/19 的情境
if __name__ == "__main__":
    svc = OneIDService()
    # S1 匿名瀏覽 → 首次登入：匿名用戶升級為客戶，歷史匿名事件自動歸戶（同一個 one_id，不改寫事件）
    a = svc.resolve({"$identity_cookie_id": "C1"}); assert a.created
    b = svc.resolve({"$identity_cookie_id": "C1", "$identity_login_id": "cust_A"})
    assert b.one_id == a.one_id and b.upgraded and ("$identity_login_id", "cust_A") in b.bound, b
    # S2 已識別客戶 A 在新裝置：先匿名（新匿名用戶 U2，distinct_id=C2），登入後事件歸 A；C2 改綁到 A；
    #    U2 在登入前 7 天內的匿名事件改寫到 A（login_backfill），7 天之前的留在 U2
    DAY = 86_400_000; T0 = 1_760_400_000_000
    old = svc.track("ga.page_view", T0 - 9 * DAY, {"$identity_cookie_id": "C2"}); assert old.created and old.one_id != a.one_id
    u2 = old
    svc.track("ga.page_view", T0 - 3 * DAY, {"$identity_cookie_id": "C2"}); svc.track("ga.view_item", T0 - 1 * DAY, {"$identity_cookie_id": "C2"})
    c = svc.track("ga.login", T0, {"$identity_cookie_id": "C2", "$identity_login_id": "cust_A"})
    assert c.one_id == a.one_id and ("$identity_cookie_id", "C2", u2.one_id) in c.rebound and svc.index[("$identity_cookie_id", "C2")] == a.one_id
    assert c.backfilled == 2, c.backfilled                                   # 近 7 天的 2 筆改寫到 A
    assert [e["one_id"] for e in svc.events if e["distinct_id"] == "C2"] == [u2.one_id, a.one_id, a.one_id, a.one_id]   # 9 天前那筆留在 U2
    assert not svc.users[u2.one_id].ids["$identity_cookie_id"]   # U2 失去 C2；7 天外的歷史仍是 U2 的
    # S3 共用裝置：B 在同一台（C2，已屬已識別客戶 A）登入 → 關聯失敗：事件歸 B、C2 留在 A、記 conflict；
    #    C2 上已歸 A 的事件不會被改寫到 B（神策 IDM 2.0「關聯失敗，事件歸 login_id 對應用戶」、IDM 3.0「小米 1 和 A2 無法關聯，行為歸 A2」）
    svc.track("ga.page_view", T0 + 1 * DAY, {"$identity_cookie_id": "C2"})     # A 登入後、登出前的事件 → 歸 A（最新綁定者）
    d = svc.track("ga.login", T0 + 2 * DAY, {"$identity_cookie_id": "C2", "$identity_login_id": "cust_B"})
    assert d.created and d.one_id not in (a.one_id, u2.one_id), d                       # B 另建用戶，事件歸 B（登入事件以客戶代碼為準）
    assert svc.index[("$identity_cookie_id", "C2")] == a.one_id and d.conflicts, d      # C2 留在 A：關聯失敗、記 conflict（神策 IDM 2.0／3.0 同）
    assert d.backfilled == 0 and "cust_A" in svc.users[a.one_id].ids["$identity_login_id"]
    assert all(e["one_id"] == a.one_id for e in svc.events if e["distinct_id"] == "C2" and T0 <= e["time"] < T0 + 2 * DAY)
    assert any(e.get("conflict_kind") == "shared_device" for e in svc.edges.values())
    # S4 之後 C2 上的匿名事件（B 登出後）→ 歸首次關聯者 A（神策的已知限制：舊手機送人後，未登入的操作仍歸原主人）
    e = svc.resolve({"$identity_cookie_id": "C2"}); assert e.one_id == a.one_id and not e.created
    # S5 交易事件只帶帳號（尚未綁定）→ 先以帳號建立用戶；CRM 綁定 {login_id, account_no} 到達 → 該用戶升級為客戶 A（帳號歸 A）
    f = svc.resolve({"$identity_account_no": "ACC9"}); assert f.created
    g = svc.resolve({"$identity_login_id": "cust_C", "$identity_account_no": "ACC9"})
    assert g.one_id == f.one_id and g.upgraded
    # S5b 已識別客戶 A 的新帳戶：CRM 綁定直接掛到 A
    h = svc.resolve({"$identity_login_id": "cust_A", "$identity_account_no": "ACC10"}); assert h.one_id == a.one_id and ("$identity_account_no", "ACC10") in h.bound
    # S6 同手機兩客戶：手機已屬 A，客戶 D 開戶帶同一手機 → D 另建用戶、資料歸 D、手機不改綁（manual）、記 conflict
    svc.resolve({"$identity_login_id": "cust_A", "$identity_mobile_hash": "M1"})
    i = svc.resolve({"$identity_login_id": "cust_D", "$identity_mobile_hash": "M1"})
    assert i.created and i.conflicts and svc.index[("$identity_mobile_hash", "M1")] == a.one_id
    # S7 GA user_id 視同 login_id（alias）
    j = svc.resolve({"$identity_ga_user_id": "cust_A", "$identity_cookie_id": "C7"}); assert j.one_id == a.one_id
    # S8 未同意：弱 ID 不與強 ID 綁定，事件仍歸客戶（有強 ID），cookie 不被綁
    k = svc.resolve({"$identity_login_id": "cust_A", "$identity_cookie_id": "C8"}, consent=False); assert k.one_id == a.one_id and ("$identity_cookie_id", "C8") not in svc.index
    # S9 login_id 不可解綁
    try:
        svc.unbind("$identity_login_id", "cust_A", by="admin"); raise AssertionError("should be blocked")
    except PermissionError:
        pass
    svc.unbind("$identity_cookie_id", "C7", by="admin"); assert ("$identity_cookie_id", "C7") not in svc.index
    # 不變量：一個 ID 只屬一個用戶；沒有任何用戶被合併（用戶數只增不減）
    assert len({v for v in svc.index.values()}) <= len(svc.users)
    # S10 未同意：登入事件不改寫匿名歷史
    svc.track("ga.page_view", T0, {"$identity_cookie_id": "C9"})
    m = svc.track("ga.login", T0 + 1000, {"$identity_cookie_id": "C9", "$identity_login_id": "cust_A"}, consent=False)
    assert m.one_id == a.one_id and m.backfilled == 0 and svc.index[("$identity_cookie_id", "C9")] != a.one_id   # C9 仍屬匿名用戶
    assert any(x["event"] == "oneid.backfilled" and x["rows"] == 2 for x in svc.audit)
    # ------------------------------------------------------------------ v1.2：多主體、邊與血緣、傳遞映射、共用裝置、合併／拆分／撤銷
    sv = OneIDService(); T = 1_792_000_000_000
    # S11 邊有來源與證據：登入記錄裡 cookie、device、客戶代碼兩兩成邊；含錨 ID 的邊是 ga360.login（0.95），弱–弱是 ga360.cooccur（0.6）
    r = sv.track("app_login_success", T, {"$identity_cookie_id": "C11", "$identity_device_id": "D11", "$identity_login_id": "cust_E"}, evidence="ev_login_1")
    e_login = sv.edges[tuple(x for kv in sorted([("$identity_login_id", "cust_E"), ("$identity_cookie_id", "C11")]) for x in kv)]
    e_weak = sv.edges[tuple(x for kv in sorted([("$identity_device_id", "D11"), ("$identity_cookie_id", "C11")]) for x in kv)]
    assert e_login["source"] == "ga360.login" and e_login["confidence"] == 0.95 and e_login["evidence_event_id"] == "ev_login_1" and e_login["one_id"] == r.one_id
    assert e_weak["source"] == "ga360.cooccur" and e_weak["confidence"] == 0.6 and e_weak["first_seen"] == T
    assert SUBJECT_OF["$identity_device_id"] == "device" and SUBJECT_OF["$identity_account_no"] == "account"
    # S12 傳遞性跟隨：匿名用戶 U 的 cookie C12 與 device D12 共現 8 次（信心 0.95）；D12 之後在已識別客戶 F 的登入裡出現 → D12 改綁到 F，C12 跟著過去，C12 近 7 天匿名事件改寫
    f = sv.resolve({"$identity_login_id": "cust_F"}, time_ms=T)
    for i in range(8):
        sv.track("$pageview", T + i * 1000, {"$identity_cookie_id": "C12", "$identity_device_id": "D12"})
    u12 = sv.index[("$identity_cookie_id", "C12")]; assert not sv.users[u12].is_identified
    r = sv.track("app_login_success", T + 10_000, {"$identity_device_id": "D12", "$identity_login_id": "cust_F"})
    assert r.one_id == f.one_id and ("$identity_device_id", "D12", u12) in r.rebound
    assert sv.index[("$identity_cookie_id", "C12")] == f.one_id, "匿名用戶 U 整個併入 F，C12 一起過去（神策方案三）"
    assert any(x["event"] == "oneid.anonymous_merged" and x["from"] == u12 and x["to"] == f.one_id for x in sv.audit)
    assert all(ev["one_id"] == f.one_id for ev in sv.events if ev["distinct_id"] == "C12")        # 8 筆匿名事件改寫到 F（近 7 天）
    assert not sv.users[u12].ids.get("$identity_cookie_id") and not sv.users[u12].is_identified   # U 清空（tombstone）
    # S13 匿名用戶併入不看共現信心：C13 與 D13 只共現 1 次（0.6）→ D13 首次關聯到 G 時，C13 也跟著整個匿名用戶併入 G（神策同；VeCDP 式信心門檻是可選項，預設關）
    g = sv.resolve({"$identity_login_id": "cust_G"}, time_ms=T)
    sv.track("$pageview", T, {"$identity_cookie_id": "C13", "$identity_device_id": "D13"})
    r = sv.track("app_login_success", T + 5000, {"$identity_device_id": "D13", "$identity_login_id": "cust_G"})
    assert sv.index[("$identity_device_id", "D13")] == g.one_id and sv.index[("$identity_cookie_id", "C13")] == g.one_id
    # S14 共用裝置：D14 首次關聯 H1；H2、H3 再登入 → 關聯失敗（D14 留在 H1，事件各歸 H2、H3）；嘗試過的客戶 ≥ 3 → 視為共用裝置；
    #     D14 上未登入的事件歸 H1（首次關聯者，神策的已知限制），分析端可用 conflict 清單把共用裝置的匿名事件排除
    h1 = sv.track("app_login_success", T, {"$identity_device_id": "D14", "$identity_login_id": "cust_H1"})
    for h in ("cust_H2", "cust_H3"):
        r = sv.track("app_login_success", T + 1, {"$identity_device_id": "D14", "$identity_login_id": h})
        assert r.conflicts and sv.index[("$identity_device_id", "D14")] == h1.one_id and sv.users[r.one_id].ids["$identity_login_id"] == {h}
    assert sv.is_shared_device("$identity_device_id", "D14")
    anon = sv.track("$pageview", T + 2, {"$identity_device_id": "D14"}); assert anon.one_id == h1.one_id
    h4 = sv.resolve({"$identity_login_id": "cust_H4"}, time_ms=T)
    r = sv.track("app_login_success", T + 100, {"$identity_device_id": "D14", "$identity_login_id": "cust_H4"})
    assert r.one_id == h4.one_id and sv.index[("$identity_device_id", "D14")] == h1.one_id                  # 事件歸 H4，裝置仍是 H1 的
    e14 = sv.edges[tuple(x for kv in sorted([("$identity_login_id", "cust_H4"), ("$identity_device_id", "D14")]) for x in kv)]
    assert e14["status"] == "conflict" and e14["conflict_kind"] == "shared_device" and e14["confidence"] < 0.95 * 0.61
    assert sum(1 for e in sv.edges.values() if e["status"] == "conflict") >= 3
    # S15 同一筆資料兩個客戶代碼 → 拒收並審計
    try:
        sv.resolve({"$identity_login_id": ["cust_X", "cust_Y"], "$identity_cookie_id": "C15"}); raise AssertionError("should reject")
    except ValueError:
        assert sv.audit[-1]["event"] == "oneid.rejected" and sv.audit[-1]["kind"] == "anchor_mismatch"
    # S16 同手機第二客戶 → 邊記 conflict（mobile_second_customer），手機不改綁
    sv.resolve({"$identity_login_id": "cust_E", "$identity_mobile_hash": "M16"}, time_ms=T)
    r = sv.resolve({"$identity_login_id": "cust_Z", "$identity_mobile_hash": "M16"}, time_ms=T + 1)
    assert r.conflicts and sv.index[("$identity_mobile_hash", "M16")] != r.one_id
    assert any(e.get("conflict_kind") == "mobile_second_customer" for e in sv.edges.values())
    # S17 合併：兩個已識別客戶（客戶代碼不同）不可合併；匿名用戶併入客戶需 admin 審批，ID 與事件搬過去並留快照
    p_ = sv.resolve({"$identity_login_id": "cust_P", "$identity_cookie_id": "C17"}, time_ms=T)
    q_ = sv.resolve({"$identity_login_id": "cust_Q"}, time_ms=T)
    try:
        sv.merge(q_.one_id, p_.one_id, by="it_admin", approved_by="admin", approver_role="admin", reason="x"); raise AssertionError("should refuse")
    except ValueError:
        pass
    sv.track("$pageview", T - 20 * DAY, {"$identity_cookie_id": "C17b"}); sv.track("$pageview", T - 19 * DAY, {"$identity_cookie_id": "C17b"})
    anon = sv.index[("$identity_cookie_id", "C17b")]
    try:
        sv.merge(anon, p_.one_id, by="it_admin", approved_by="digital_ops", approver_role="digital_ops", reason="x"); raise AssertionError("needs admin")
    except PermissionError:
        pass
    log1 = sv.merge(anon, p_.one_id, by="it_admin", approved_by="admin", approver_role="admin", reason="同一人：客服確認")
    rec = sv.merge_log[-1]
    assert sv.index[("$identity_cookie_id", "C17b")] == p_.one_id and rec["events_rewritten"] == 2 and rec["snapshot_before"][anon] == {"$identity_cookie_id": ["C17b"]}
    assert all(ev["one_id"] == p_.one_id for ev in sv.events if ev["distinct_id"] == "C17b") and not sv.users[anon].ids.get("$identity_cookie_id")
    # S18 撤銷合併：ID 與事件搬回；二次撤銷被拒
    sv.undo(log1, by="admin", approver_role="admin")
    assert sv.index[("$identity_cookie_id", "C17b")] == anon and all(ev["one_id"] == anon for ev in sv.events if ev["distinct_id"] == "C17b")
    assert sv.merge_log[-1]["op"] == "undo" and sv.merge_log[0 if len(sv.merge_log) == 2 else -2]["undone_by_log_id"] == sv.merge_log[-1]["log_id"]
    try:
        sv.undo(log1, by="admin", approver_role="admin"); raise AssertionError("double undo")
    except ValueError:
        pass
    # S19 拆分：P 的 cookie C17 從 T+1 天起的事件拆給新用戶；之前的留在 P；客戶代碼不可拆
    sv.track("$pageview", T, {"$identity_cookie_id": "C17"}); sv.track("$pageview", T + 2 * DAY, {"$identity_cookie_id": "C17"})
    try:
        sv.split(p_.one_id, [("$identity_login_id", "cust_P")], from_time=T, by="it_admin", approved_by="admin", approver_role="admin", reason="x"); raise AssertionError("anchor split")
    except ValueError:
        pass
    log3, new_id = sv.split(p_.one_id, [("$identity_cookie_id", "C17")], from_time=T + DAY, by="it_admin", approved_by="admin", approver_role="admin", reason="家人共用")
    assert sv.index[("$identity_cookie_id", "C17")] == new_id and sv.merge_log[-1]["events_rewritten"] == 1
    assert [ev["one_id"] for ev in sv.events if ev["distinct_id"] == "C17"] == [p_.one_id, new_id]
    # S20 血緣圖與表級血緣：節點帶主體、邊帶來源／證據／信心、時間軸有事件；lineage 有「來源 → id_map」
    gph = sv.graph(f.one_id)
    assert {n["subject"] for n in gph["nodes"]} >= {"customer", "device"} and gph["edges"] and all("confidence" in e and "evidence_event_id" in e for e in gph["edges"])
    assert gph["events"] == 9 and any(x["event"] == "oneid.anonymous_merged" for x in gph["timeline"])
    assert ("source", "ga360", "id_map", "dmp.id_map", "oneid.service") in sv.lineage
    # S21 關聯上限與超限策略：裝置 max_count=20 滾動解綁（最早的被解綁、新的綁上）；帳號 max_count=50 intercept（第 51 戶不綁、記 conflict、事件仍歸客戶）
    s21 = OneIDService(); z = s21.resolve({"$identity_login_id": "cust_Z"}, time_ms=T)
    for i in range(22):
        s21.track("app_login_success", T + i, {"$identity_device_id": f"DZ{i:02d}", "$identity_login_id": "cust_Z"})
    assert len(s21.users[z.one_id].ids["$identity_device_id"]) == 20 and ("$identity_device_id", "DZ00") not in s21.index and ("$identity_device_id", "DZ21") in s21.index
    assert sum(1 for a in s21.audit if a["event"] == "oneid.rolling_unbound") == 2
    for i in range(50):
        s21.resolve({"$identity_login_id": "cust_Z", "$identity_account_no": f"AZ{i:03d}"}, time_ms=T)
    r = s21.resolve({"$identity_login_id": "cust_Z", "$identity_account_no": "AZ050"}, time_ms=T)
    assert r.one_id == z.one_id and r.conflicts and ("$identity_account_no", "AZ050") not in s21.index and len(s21.users[z.one_id].ids["$identity_account_no"]) == 50
    assert any(e.get("conflict_kind") == "over_limit" for e in s21.edges.values())
    # S22 父節點：解綁客戶代碼被擋（never）；解綁 cookie 不影響；若把 parent 設在 mobile 上則解綁 mobile 連帶解綁子 ID —— 這裡驗證 cascade 規則本身
    s22 = OneIDService(); ID_SPEC["$identity_cookie_id"]["parent"] = "$identity_mobile_hash"
    try:
        s22.resolve({"$identity_login_id": "cust_W", "$identity_mobile_hash": "MW", "$identity_cookie_id": "CW1", "$identity_device_id": "DW1"}, time_ms=T)
        s22.unbind("$identity_mobile_hash", "MW", by="admin")
        assert ("$identity_cookie_id", "CW1") not in s22.index and ("$identity_device_id", "DW1") in s22.index   # cookie 的父是 mobile → 連帶；device 的父是 login → 不受影響
        assert any(a.get("cascade_from") == "$identity_mobile_hash" for a in s22.audit)
    finally:
        ID_SPEC["$identity_cookie_id"]["parent"] = "$identity_login_id"
    # S23 停用的 ID 類型不參與歸戶；email 作為單值強 ID 排在 mobile 之後
    ID_SPEC["$identity_email_hash"]["status"] = "disabled"
    try:
        r = s22.resolve({"$identity_login_id": "cust_W", "$identity_email_hash": "EW"}, time_ms=T); assert ("$identity_email_hash", "EW") not in s22.index
    finally:
        ID_SPEC["$identity_email_hash"]["status"] = "enabled"
    r = s22.resolve({"$identity_login_id": "cust_W", "$identity_email_hash": "EW"}, time_ms=T); assert ("$identity_email_hash", "EW") in s22.index
    r2 = s22.resolve({"$identity_login_id": "cust_V", "$identity_email_hash": "EW"}, time_ms=T); assert r2.conflicts and s22.index[("$identity_email_hash", "EW")] == r.one_id
    assert ID_SPEC["$identity_mobile_hash"]["priority"] < ID_SPEC["$identity_email_hash"]["priority"] < ID_SPEC["$identity_account_no"]["priority"]
    # 不變量仍成立：一個 ID 只屬一個用戶
    assert all(v in sv.users and ((k, val) in {(kk, vv) for kk, vs in sv.users[v].ids.items() for vv in vs}) for (k, val), v in sv.index.items())
    print(f"attribution self-test ok: S1–S23; users={len(svc.users) + len(sv.users)} edges={len(sv.edges)} conflicts={sum(1 for e in sv.edges.values() if e['status'] == 'conflict')} merge_log={len(sv.merge_log)}")
