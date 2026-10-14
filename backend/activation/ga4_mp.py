"""GA4 Measurement Protocol 受眾回流 payload 產生器（docs/14 §4）。不含任何個資：只用 GA 匿名 ID 與 HMAC user_id。"""
from __future__ import annotations

import json
from typing import Any

ENDPOINT = "https://www.google-analytics.com/mp/collect"


def audience_payload(audience_id: str, version: int, client_id: str, action: str, user_id_hmac: str | None = None, app_instance_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "user_properties": {f"dmp_audience_{audience_id}": {"value": "1" if action == "add" else "0"}},
        "events": [{"name": "dmp_audience", "params": {"audience_id": audience_id, "action": action, "version": version, "engagement_time_msec": 1}}],
    }
    if app_instance_id:
        body["app_instance_id"] = app_instance_id
    else:
        body["client_id"] = client_id
    if user_id_hmac:
        body["user_id"] = user_id_hmac
    return body


def request(measurement_id: str, api_secret: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"method": "POST", "url": f"{ENDPOINT}?measurement_id={measurement_id}&api_secret=***", "json": body}


if __name__ == "__main__":
    b = audience_payload("sg1", 42, "GA1.2.1844.5678", "add", user_id_hmac="u_9a7b")
    assert b["user_properties"]["dmp_audience_sg1"]["value"] == "1" and "email" not in json.dumps(b)
    print("ga4 mp ok:", json.dumps(b, ensure_ascii=False)[:120], "…")
