"""OneID 歸戶：接入時逐筆決定事件屬於哪個用戶（參考實作，可直接執行自測）。

對齊神策 IDM 3.0 的運作方式（docs/19）：
- 一個 ID 同一時刻只屬於一個用戶；不自動合併兩個已存在的用戶。
- 同一條資料多個 ID 分屬不同用戶 → 歸優先級最高的 ID 所屬用戶；可改綁（rebind=latest）的 ID 改綁到該用戶。
- 純匿名用戶首次與「尚無用戶」的強 ID 共現 → 升級為該客戶（歷史匿名事件自動歸戶，不需改寫事件）。
- 未登入的行為事件以 distinct_id（cookie／裝置／匿名 ID）歸到匿名用戶；登入事件到達時歸戶，
  並把同 distinct_id、近 7 天內仍屬匿名用戶的事件改寫到登入客戶（login_backfill）；更早的或已歸客戶的事件不動。
- 除此之外沒有夜間改寫（人工合併／拆分例外）。
規則來源：backend/semantic/oneid_rules.yaml。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "semantic" / "oneid_rules.yaml").read_text(encoding="utf-8"))
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

    # ---- helpers
    def _new_user(self) -> User:
        self.seq += 1
        u = User(one_id=f"oid_{self.seq:06d}", one_id_uint=self.seq)
        self.users[u.one_id] = u
        return u

    def _bind(self, u: User, key: str, value: str) -> None:
        u.ids.setdefault(key, set()).add(value)
        self.index[(key, value)] = u.one_id

    def _unbind(self, u: User, key: str, value: str) -> None:
        u.ids.get(key, set()).discard(value)
        self.index.pop((key, value), None)

    @staticmethod
    def normalize(identities: dict[str, Any]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for k, v in identities.items():
            k = ALIAS.get(k, k)
            if k not in ID_SPEC or not ID_SPEC[k].get("participates", True) or v in (None, ""):
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

    def track(self, event: str, time_ms: int, identities: dict[str, Any], *, consent: bool = True) -> Decision:
        """一筆事件：先歸戶（resolve），寫入事件表；若是登入事件則把近 7 天同 distinct_id 的匿名事件改寫到客戶。"""
        ids = self.normalize(identities)
        did = self.distinct_id(ids)
        d = self.resolve(identities, consent=consent)
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
    def resolve(self, identities: dict[str, Any], *, consent: bool = True) -> Decision:
        ids = self.normalize(identities)
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
                if winner.has_slot(k, v):
                    self._bind(winner, k, v); bound.append((k, v))
                else:
                    conflicts.append(f"{k}={v} 無法綁定：{winner.one_id} 的單值欄位已有 {sorted(winner.ids[k])}")
                continue
            # ID 屬於另一個用戶：依 rebind 政策
            policy = ID_SPEC[k]["rebind"]
            if policy == "latest" and winner.has_slot(k, v):
                self._unbind(self.users[cur], k, v); self._bind(winner, k, v); rebound.append((k, v, cur))
            elif policy == "never":
                conflicts.append(f"{k}={v} 屬於 {cur}，不可改綁（資料仍歸 {winner.one_id}）")
            else:
                conflicts.append(f"{k}={v} 屬於 {cur}，政策 {policy}：保留原綁定、記 conflict 待人工")
        return self._done(winner, winner.one_id not in {*distinct} and not upgraded, upgraded, bound, rebound, conflicts, "priority_owner")

    def _done(self, u: User, created: bool, upgraded: bool, bound, rebound, conflicts, reason: str) -> Decision:
        d = Decision(u.one_id, created, upgraded, bound, rebound, conflicts, reason)
        if bound or rebound or conflicts or created:
            self.audit.append({"event": "oneid.resolved", "one_id": u.one_id, "created": created, "upgraded": upgraded, "bound": bound, "rebound": rebound, "conflicts": conflicts})
        return d

    def unbind(self, key: str, value: str, *, by: str) -> None:
        if key in RULES["manual_tools"]["unbind"]["blocked_for"]:
            raise PermissionError(f"{key} 不可解綁")
        oid = self.index.get((key, value))
        if oid:
            self._unbind(self.users[oid], key, value)
            self.audit.append({"event": "oneid.unbound", "key": key, "one_id": oid, "by": by})


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
    # S3 共用裝置：B 在同一台（C2）登入 → 事件歸 B；C2 改綁到 B；A 不受影響；
    #    C2 上已歸 A 的事件（含 A 登入後的匿名事件，見 S4 前的 track）不會被改寫到 B
    svc.track("ga.page_view", T0 + 1 * DAY, {"$identity_cookie_id": "C2"})     # A 登入後、登出前的事件 → 歸 A（最新綁定者）
    d = svc.track("ga.login", T0 + 2 * DAY, {"$identity_cookie_id": "C2", "$identity_login_id": "cust_B"})
    assert d.created and d.one_id not in (a.one_id, u2.one_id) and svc.index[("$identity_cookie_id", "C2")] == d.one_id
    assert d.backfilled == 0 and "cust_A" in svc.users[a.one_id].ids["$identity_login_id"]
    assert all(e["one_id"] == a.one_id for e in svc.events if e["distinct_id"] == "C2" and T0 <= e["time"] < T0 + 2 * DAY)
    # S4 之後 C2 上的匿名事件（B 登出後）→ 歸 B（最新綁定者），不是 A
    e = svc.resolve({"$identity_cookie_id": "C2"}); assert e.one_id == d.one_id and not e.created
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
    print(f"attribution self-test ok: users={len(svc.users)} bindings={len(svc.index)} events={len(svc.events)} audit={len(svc.audit)}")
