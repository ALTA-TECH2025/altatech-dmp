"""Webhook 推送骨架：簽章、批次切分、payload、重試策略、接收端驗章。python3 backend/activation/webhook.py 自測。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

RETRY_SCHEDULE = [60, 300, 900, 3600, 21600]
BATCH_SIZE = 5000


@dataclass
class Destination:
    id: str
    url: str
    secret: bytes
    allowed_tags: tuple[str, ...] = ()
    identifier: str = "customer_code"


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify(secret: bytes, timestamp: str, body: bytes, signature: str, skew: int = 300) -> bool:
    if abs(time.time() - int(timestamp)) > skew:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def batches(members: list[dict[str, Any]], size: int = BATCH_SIZE) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(members), size):
        yield members[i:i + size]


def build_payload(audience: dict[str, Any], mode: str, batch_index: int, batch_total: int, members: list[dict[str, Any]], dest: Destination) -> dict[str, Any]:
    def scrub(m: dict[str, Any]) -> dict[str, Any]:
        out = {"customer_code": m["customer_code"], "one_id": m["one_id"], "action": m.get("action", "add")}
        if dest.allowed_tags and m.get("tags"):
            out["tags"] = {k: v for k, v in m["tags"].items() if k in dest.allowed_tags}   # 已由 Policy Engine 區間化
        return out
    return {
        "audience_id": audience["id"], "audience_name": audience["name"], "version": audience["version"],
        "computed_at": audience["computed_at"], "mode": mode,
        "batch": {"index": batch_index, "total": batch_total, "size": len(members)},
        "identifier": dest.identifier, "members": [scrub(m) for m in members],
    }


def deliver(dest: Destination, event: str, payload: dict[str, Any], delivery_id: str | None = None) -> dict[str, Any]:
    """組出 HTTP 請求（正式版以 httpx 送出；骨架只回傳 headers/body 供檢視）。"""
    delivery_id = delivery_id or f"dlv_{uuid.uuid4().hex[:16]}"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-DMP-Event": event, "X-DMP-Delivery-Id": delivery_id,
               "X-DMP-Timestamp": ts, "X-DMP-Signature": sign(dest.secret, ts, body)}
    return {"url": dest.url, "headers": headers, "body": body, "retry_schedule": RETRY_SCHEDULE}


if __name__ == "__main__":
    dest = Destination("crm", "https://crm.internal/dmp/webhook", b"s3cret", allowed_tags=("lifecycle", "fee_tier"))
    aud = {"id": "sg1", "name": "高價值沉睡客", "version": 42, "computed_at": "2026-10-14T02:41:00Z"}
    members = [{"customer_code": f"cust_{i:05x}", "one_id": f"oid_{i:06x}", "action": "add", "tags": {"lifecycle": "沉睡", "fee_tier": "P1", "fee90": 312000}} for i in range(12000)]
    bs = list(batches(members))
    req = deliver(dest, "audience.delta", build_payload(aud, "delta", 1, len(bs), bs[0], dest))
    assert verify(dest.secret, req["headers"]["X-DMP-Timestamp"], req["body"], req["headers"]["X-DMP-Signature"])
    assert not verify(dest.secret, req["headers"]["X-DMP-Timestamp"], req["body"] + b"x", req["headers"]["X-DMP-Signature"])
    p = json.loads(req["body"]); assert "fee90" not in p["members"][0]["tags"] and p["batch"]["total"] == 3
    print("webhook ok:", len(bs), "batches;", req["headers"]["X-DMP-Signature"][:20], "…")
