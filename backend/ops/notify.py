"""告警通知：Email（SMTP）與 LINE 官方帳號（Messaging API push／multicast），設定可線上修改（密鑰只存 secret_ref），
冷卻／最低嚴重度／靜音時段／恢復通知的政策，通知紀錄；Alertmanager webhook 走同一條路。由 start_ticker 每分鐘拿 OpsService.alerts() 的 firing 呼叫 dispatch()。"""
from __future__ import annotations

import copy
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml  # type: ignore

ROOT = Path(__file__).resolve().parent
DEFAULTS: dict[str, Any] = yaml.safe_load((ROOT / "notify.yaml").read_text(encoding="utf-8"))
SECRET_REF_RE = re.compile(r"^(kv/data/dmp/[a-z]+/[a-z0-9_]+#[a-z_]+|projects/[^/]+/secrets/[^/]+/versions/(\d+|latest)|secret://[a-z0-9-]+/[a-z0-9-]+/[a-zA-Z0-9_.-]+)$")
LINE_ID_RE = re.compile(r"^[UCR][0-9a-f]{32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SEV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEV_CN = {"low": "低", "medium": "中", "high": "高", "critical": "緊急"}
CONFIG_ID = "ops_notify"
STATE_ID = "ops_notify_state"
HISTORY_KEEP = 500
LINE_MULTICAST_MAX = 500


class NotifyError(ValueError):
    pass


class Notifier:
    def __init__(self, store: Any, secret_of: Any | None = None, mailer: Any | None = None, transport: Any | None = None, recipients: Any | None = None, now: Any = time.time, base_url: str = ""):
        self.store, self.secret_of, self.mailer, self.transport, self.recipients, self.now, self.base_url = store, secret_of, mailer, transport, recipients, now, base_url

    # ---------------- 設定 ----------------
    def config(self) -> dict[str, Any]:
        cfg = copy.deepcopy(DEFAULTS); cfg.pop("version", None)
        saved = next((c for c in self.store.items("config") if c.get("id") == CONFIG_ID), None)
        if saved:
            for k in ("email", "line", "policy"):
                if isinstance(saved.get(k), dict): cfg[k] = {**cfg[k], **saved[k]}
            cfg["updated_at"], cfg["updated_by"] = saved.get("updated_at"), saved.get("updated_by")
        else:
            cfg["updated_at"] = cfg["updated_by"] = None
        cfg["available"] = {"email": self.mailer is not None, "line": self._line_token(cfg) is not None}
        return cfg

    def update(self, body: dict[str, Any], subject: Any) -> dict[str, Any]:
        cur = self.config(); new = {k: {**cur[k], **(body.get(k) or {})} for k in ("email", "line", "policy")}
        e, ln, po = new["email"], new["line"], new["policy"]
        bad = [x for x in e.get("extra_to", []) if not EMAIL_RE.match(str(x))]
        if bad: raise NotifyError(f"Email 格式錯誤：{bad[:3]}")
        if not isinstance(e.get("to_roles"), list): raise NotifyError("to_roles 需為角色清單")
        ref = str(ln.get("channel_access_token_ref") or "")
        if ref and not SECRET_REF_RE.match(ref): raise NotifyError("LINE channel access token 只能填密鑰庫參照（secret_ref），不能填明文")
        badl = [x for x in ln.get("to", []) if not LINE_ID_RE.match(str(x))]
        if badl: raise NotifyError(f"LINE 收件 ID 格式錯誤（U／C／R 開頭 33 字元）：{badl[:3]}")
        if ln.get("enabled") and (not ref or not ln.get("to")): raise NotifyError("啟用 LINE 需要 token 參照與至少一個收件 ID")
        if po.get("min_severity") not in SEV_ORDER: raise NotifyError("min_severity 只能是 low／medium／high／critical")
        po["cooldown_min"] = max(0, int(po.get("cooldown_min") or 0))
        q = po.get("quiet_hours")
        if q:
            try:
                ZoneInfo(str(q.get("tz") or "Asia/Taipei")); int(q["start"]); int(q["end"])
            except Exception:
                raise NotifyError("quiet_hours 需要 start／end（0–23）與時區") from None
        rec = {"id": CONFIG_ID, "email": e, "line": ln, "policy": po, "updated_at": self._iso(), "updated_by": subject.user_id}
        self.store.put_item("config", rec)
        self.store.audit("ops.notify.changed", {"user": subject.user_id, "email_enabled": bool(e.get("enabled")), "line_enabled": bool(ln.get("enabled")), "line_to": len(ln.get("to", [])), "min_severity": po["min_severity"], "cooldown_min": po["cooldown_min"]})
        return self.config()

    # ---------------- 送出 ----------------
    def test(self, channel: str, subject: Any) -> dict[str, Any]:
        cfg = self.config()
        if channel not in ("email", "line"): raise NotifyError("channel 只能是 email 或 line")
        if not cfg[channel].get("enabled"): raise NotifyError(f"{channel} 未啟用")
        if not cfg["available"][channel]: raise NotifyError("email 需要 DMP_SMTP_URL" if channel == "email" else "LINE token 參照解析不到（密鑰庫未掛載或參照錯誤）")
        text = f"DMP 測試通知（{subject.user_id} 於 {self._iso()} 送出）。收到代表 {('Email' if channel == 'email' else 'LINE 官方帳號')} 管道設定正確。"
        rec = self._send(channel, "test", text, ["test"], cfg)
        self.store.audit("ops.notify.tested", {"user": subject.user_id, "channel": channel, "status": rec["status"], "recipients": rec["recipients"]})
        return rec

    def dispatch(self, firing: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
        """每分鐘：新觸發或冷卻期過的告警 → 送；上次有、這次沒有的 → 恢復通知。回傳本輪產生的紀錄。"""
        t = now if now is not None else self.now(); cfg = self.config(); po = cfg["policy"]
        state = next((s for s in self.store.items("config") if s.get("id") == STATE_ID), None) or {"id": STATE_ID, "active": {}}
        active: dict[str, dict[str, Any]] = dict(state.get("active") or {})
        quiet = self._quiet(po, t)
        due: list[dict[str, Any]] = []; keys_now: set[str] = set()
        for a in firing:
            k = f"{a['key']}:{a.get('subject', '')}"; keys_now.add(k)
            if SEV_ORDER.get(a.get("severity", "low"), 0) < SEV_ORDER[po["min_severity"]]: continue
            if quiet and a.get("severity") != "critical": continue
            last = float(active.get(k, {}).get("notified_at") or 0)
            if last and t - last < po["cooldown_min"] * 60: continue
            due.append(a); active[k] = {"severity": a.get("severity"), "notified_at": t, "first_at": active.get(k, {}).get("first_at") or t, "text": a.get("text", "")}
        for k in keys_now:
            active.setdefault(k, {"severity": None, "notified_at": 0, "first_at": t, "text": ""})
        resolved = [k for k in list(active) if k not in keys_now]
        out: list[dict[str, Any]] = []
        f = getattr(self, "fence", None)
        if (due or resolved) and f is not None and not f.verify():         # 已不是主控（ops/lease.py）：不送、也不改冷卻狀態
            return out
        if due:
            text = self._format(due, t)
            for ch in ("email", "line"):
                if f is not None and not f.verify():
                    return out
                if cfg[ch].get("enabled") and cfg["available"][ch]:
                    out.append(self._send(ch, "alert", text, [f"{a['key']}:{a.get('subject', '')}" for a in due], cfg))
        if resolved:
            notified = [k for k in resolved if float(active[k].get("notified_at") or 0) > 0]
            for k in resolved: active.pop(k, None)
            if po.get("notify_resolved") and notified:
                text = "已恢復：" + "、".join(notified[:10]) + ("…" if len(notified) > 10 else "") + f"（{self._fmt_t(t)}）"
                for ch in ("email", "line"):
                    if cfg[ch].get("enabled") and cfg["available"][ch]:
                        out.append(self._send(ch, "resolved", text, notified, cfg))
        self.store.put_item("config", {"id": STATE_ID, "active": active, "updated_at": self._iso(t)})
        return out

    def from_alertmanager(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Alertmanager v4 webhook → 同一條 dispatch（firing）＋ resolved 直接送恢復。"""
        firing = []; resolved_keys = []
        for a in payload.get("alerts", []):
            lb = a.get("labels") or {}; an = a.get("annotations") or {}
            item = {"key": lb.get("key") or lb.get("alertname", "alert"), "cn": lb.get("alertname", ""), "severity": lb.get("severity", "medium"), "subject": lb.get("task") or lb.get("module") or lb.get("destination") or lb.get("job") or lb.get("instance", ""),
                    "text": an.get("summary") or an.get("description", ""), "value": None, "threshold": None, "unit": "", "op": ""}
            (firing if a.get("status", "firing") == "firing" else resolved_keys).append(item if a.get("status", "firing") == "firing" else f"{item['key']}:{item['subject']}")
        out = self.dispatch(firing) if firing else []
        cfg = self.config()
        if resolved_keys and cfg["policy"].get("notify_resolved"):
            text = "已恢復（Alertmanager）：" + "、".join(resolved_keys[:10])
            for ch in ("email", "line"):
                if cfg[ch].get("enabled") and cfg["available"][ch]:
                    out.append(self._send(ch, "resolved", text, resolved_keys, cfg))
        return out

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = sorted(self.store.items("notification"), key=lambda r: r["at"], reverse=True)
        return rows[:limit]

    # ---------------- 管道 ----------------
    def _send(self, channel: str, kind: str, text: str, alerts: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
        rec = {"id": "ntf_" + uuid.uuid4().hex[:8], "at": self._iso(), "channel": channel, "kind": kind, "alerts": alerts, "recipients": 0, "status": "skipped", "response": None, "text": text}
        try:
            if channel == "email":
                to = self._email_to(cfg)
                if not to: rec["response"] = "沒有收件人（角色成員沒有 email、也沒有額外收件人）"
                else:
                    self.mailer(to, f"{cfg['email'].get('subject_prefix') or '[DMP 告警]'} {('測試' if kind == 'test' else '已恢復' if kind == 'resolved' else f'{len(alerts)} 項')}", "<pre style='font-family:inherit;white-space:pre-wrap'>" + _esc(text) + "</pre>")
                    rec.update({"recipients": len(to), "status": "ok"})
            else:
                token = self._line_token(cfg); to = list(cfg["line"].get("to") or [])
                if not token or not to: rec["response"] = "LINE 未設定 token 或收件 ID"
                else:
                    sent = 0; resp = []
                    for i in range(0, len(to), LINE_MULTICAST_MAX):
                        chunk = to[i:i + LINE_MULTICAST_MAX]
                        url = cfg["line"].get("api_base", "https://api.line.me").rstrip("/") + ("/v2/bot/message/push" if len(chunk) == 1 else "/v2/bot/message/multicast")
                        body = {"to": chunk[0] if len(chunk) == 1 else chunk, "messages": [{"type": "text", "text": text[:5000]}]}
                        status, ms, body_txt = self.transport(url, {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Line-Retry-Key": str(uuid.uuid4())}, json.dumps(body, ensure_ascii=False).encode(), 15)
                        resp.append(f"{status} {ms}ms {body_txt[:120]}")
                        if status == 200: sent += len(chunk)
                    rec.update({"recipients": sent, "status": "ok" if sent == len(to) else "failed", "response": "; ".join(resp)})
        except Exception as e:
            rec.update({"status": "failed", "response": f"{type(e).__name__}: {str(e)[:200]}"})
        self.store.put_item("notification", rec)
        rows = sorted(self.store.items("notification"), key=lambda r: r["at"])
        for old in rows[:-HISTORY_KEEP]:
            self.store.delete_item("notification", old["id"])
        self.store.audit("ops.notify.sent", {"channel": channel, "kind": kind, "status": rec["status"], "recipients": rec["recipients"], "alerts": alerts[:10]})
        return rec

    def _email_to(self, cfg: dict[str, Any]) -> list[str]:
        to = list(cfg["email"].get("extra_to") or [])
        if self.recipients is not None and cfg["email"].get("to_roles"):
            to += [x for x in self.recipients(list(cfg["email"]["to_roles"])) if x]
        return sorted(set(to))

    def _line_token(self, cfg: dict[str, Any]) -> str | None:
        ref = str(cfg["line"].get("channel_access_token_ref") or "")
        if not ref or self.secret_of is None: return None
        try:
            return str(self.secret_of(ref)) or None
        except Exception:
            return None

    def _quiet(self, po: dict[str, Any], t: float) -> bool:
        q = po.get("quiet_hours")
        if not q: return False
        h = datetime.fromtimestamp(t, ZoneInfo(str(q.get("tz") or "Asia/Taipei"))).hour; s, e = int(q["start"]), int(q["end"])
        return s <= h < e if s <= e else (h >= s or h < e)

    def _format(self, due: list[dict[str, Any]], t: float) -> str:
        lines = [f"DMP 告警 {len(due)} 項（{self._fmt_t(t)}）"]
        for a in sorted(due, key=lambda a: -SEV_ORDER.get(a.get("severity", "low"), 0)):
            v = a.get("value"); th = a.get("threshold"); u = a.get("unit") or ""
            vt = "" if v is None else (f"：{v * 100:.1f}%（門檻 {a.get('op', '')} {float(th) * 100:.0f}%）" if u == "比例" and th is not None else f"：{v} {u}" + (f"（門檻 {a.get('op', '')} {th} {u}）" if th is not None else ""))
            lines.append(f"[{SEV_CN.get(a.get('severity', 'low'), a.get('severity'))}] {a.get('cn', a['key'])} · {a.get('subject', '')}{vt}")
            if a.get("text"): lines.append(f"  {a['text']}")
        if self.base_url: lines.append(f"{self.base_url}/governance/pipeline?tab=alerts")
        return "\n".join(lines)

    def _iso(self, t: float | None = None) -> str:
        return datetime.fromtimestamp(t if t is not None else self.now(), UTC).isoformat(timespec="seconds")

    @staticmethod
    def _fmt_t(t: float) -> str:
        return datetime.fromtimestamp(t, ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def selftest() -> None:
    class Store:
        def __init__(self) -> None: self.d: dict[str, dict[str, Any]] = {}; self.a: list[dict[str, Any]] = []
        def items(self, k: str) -> list[dict[str, Any]]: return list(self.d.get(k, {}).values())
        def put_item(self, k: str, o: dict[str, Any]) -> None: self.d.setdefault(k, {})[o["id"]] = o
        def delete_item(self, k: str, i: str) -> None: self.d.get(k, {}).pop(i, None)
        def audit(self, e: str, d: dict[str, Any]) -> None: self.a.append({"event": e, **d})
    class Subj: user_id = "entra:it"
    now = [1_800_000_000.0]; mails: list[tuple[list[str], str]] = []; posts: list[dict[str, Any]] = []; line_ok = [True]
    def transport(url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int | None, int, str]:
        posts.append({"url": url, "auth": headers["Authorization"], "body": json.loads(body)}); return (200, 30, "{}") if line_ok[0] else (401, 30, '{"message":"invalid token"}')
    st = Store()
    n = Notifier(st, secret_of=lambda r: {"kv/data/dmp/prod/line#channel_access_token": "LINE-TOKEN"}[r], mailer=lambda to, s, h: mails.append((to, s)), transport=transport,
                 recipients=lambda roles: ["it@corp.internal"] if "it_admin" in roles else [], now=lambda: now[0], base_url="https://dmp.internal")
    c = n.config(); assert c["email"]["enabled"] and not c["line"]["enabled"] and c["policy"]["min_severity"] == "medium" and c["available"] == {"email": True, "line": False} and c["updated_at"] is None
    for bad in ({"line": {"channel_access_token_ref": "plain-token-value"}}, {"line": {"to": ["not-an-id"]}}, {"email": {"extra_to": ["nope"]}}, {"policy": {"min_severity": "x"}}, {"line": {"enabled": True}}):
        try: n.update(bad, Subj()); raise AssertionError(bad)
        except NotifyError: pass
    c = n.update({"line": {"enabled": True, "channel_access_token_ref": "kv/data/dmp/prod/line#channel_access_token", "to": ["U" + "a" * 32, "C" + "b" * 32]}, "email": {"extra_to": ["oncall@corp.internal"]}, "policy": {"cooldown_min": 30}}, Subj())
    assert c["line"]["enabled"] and c["available"]["line"] and c["updated_by"] == "entra:it" and st.a[-1]["event"] == "ops.notify.changed" and "LINE-TOKEN" not in json.dumps(c)
    t = n.test("line", Subj()); assert t["status"] == "ok" and t["recipients"] == 2 and posts[-1]["url"].endswith("/multicast") and posts[-1]["auth"] == "Bearer LINE-TOKEN" and posts[-1]["body"]["to"] == ["U" + "a" * 32, "C" + "b" * 32]
    t = n.test("email", Subj()); assert t["status"] == "ok" and t["recipients"] == 2 and mails[-1][0] == ["it@corp.internal", "oncall@corp.internal"] and "測試" in mails[-1][1]
    A = {"key": "source_lag", "cn": "來源延遲", "severity": "high", "subject": "交易委託", "value": 240, "threshold": 120, "op": ">", "unit": "min", "text": "水位落後"}
    B = {"key": "quarantine_rate", "cn": "隔離率", "severity": "medium", "subject": "交易委託", "value": 0.1, "threshold": 0.05, "op": ">", "unit": "比例", "text": ""}
    L = {"key": "x", "cn": "低", "severity": "low", "subject": "s", "value": 1, "threshold": 1, "op": ">=", "unit": "筆", "text": ""}
    r = n.dispatch([A, B, L]); assert [x["channel"] for x in r] == ["email", "line"] and r[0]["kind"] == "alert" and r[0]["alerts"] == ["source_lag:交易委託", "quarantine_rate:交易委託"], r
    txt = r[1]["text"]; assert txt.startswith("DMP 告警 2 項") and "[高] 來源延遲 · 交易委託：240 min（門檻 > 120 min）" in txt and "[中] 隔離率 · 交易委託：10.0%（門檻 > 5%）" in txt and "低" not in txt.split("\n")[1] and txt.endswith("/governance/pipeline?tab=alerts"), txt
    assert posts[-1]["url"].endswith("/multicast") and posts[-1]["body"]["messages"][0]["text"] == txt
    now[0] += 600; assert n.dispatch([A, B]) == []                                                           # 冷卻 30 分鐘內不重送
    now[0] += 1800; r = n.dispatch([A]); assert [x["kind"] for x in r] == ["alert", "alert", "resolved", "resolved"] and r[0]["alerts"] == ["source_lag:交易委託"] and "quarantine_rate:交易委託" in r[2]["text"], r   # 冷卻過 → A 重送；B 消失 → 恢復
    now[0] += 60; r = n.dispatch([A]); assert r == []                                                       # A 冷卻中、沒有新變化
    now[0] += 60; r = n.dispatch([]); assert [x["kind"] for x in r] == ["resolved", "resolved"] and "已恢復：source_lag:交易委託" in r[0]["text"], r
    line_ok[0] = False; now[0] += 7200; r = n.dispatch([A]); assert r[1]["status"] == "failed" and "401" in r[1]["response"] and r[0]["status"] == "ok"
    line_ok[0] = True; now[0] += 60; assert [x["kind"] for x in n.dispatch([])] == ["resolved", "resolved"]      # 清掉 A，進入靜音時段測試
    n.update({"policy": {"quiet_hours": {"start": 0, "end": 7, "tz": "Asia/Taipei"}}}, Subj())
    quiet_t = datetime(2026, 10, 20, 3, 0, tzinfo=ZoneInfo("Asia/Taipei")).timestamp(); now[0] = quiet_t
    assert n.dispatch([{**A, "subject": "另一個"}]) == [] and len(n.dispatch([{**A, "subject": "緊急", "severity": "critical"}])) == 2        # 靜音時段只送 critical
    am = {"version": "4", "alerts": [{"status": "firing", "labels": {"alertname": "DmpEventsStale", "severity": "critical", "key": "events_stale"}, "annotations": {"summary": "行為事件停滯 2h"}},
                                     {"status": "resolved", "labels": {"alertname": "DmpHttpErrors", "severity": "high", "key": "http_errors"}, "annotations": {}}]}
    now[0] += 60; r = n.from_alertmanager(am)                                                                 # 「緊急」消失 → 恢復 ×2；Alertmanager 的 resolved → 恢復 ×2
    assert [x["kind"] for x in r] == ["alert", "alert", "resolved", "resolved", "resolved", "resolved"] and "行為事件停滯 2h" in r[0]["text"] and any("http_errors" in x["text"] for x in r[2:]), [x["kind"] for x in r]
    h = n.history(5); assert len(h) == 5 and h[0]["at"] >= h[-1]["at"] and all(x["channel"] in ("email", "line") for x in h)
    assert {x["event"] for x in st.a} >= {"ops.notify.changed", "ops.notify.tested", "ops.notify.sent"}
    print(f"notify self-test ok: config/validate, test(email/line multicast), dispatch(severity/cooldown/resolved/quiet/failed), alertmanager; notifications={len(st.items('notification'))}")


if __name__ == "__main__":
    selftest()
