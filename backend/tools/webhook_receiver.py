"""本機／CI 的 Webhook 接收端（驗 HMAC 簽章、冪等、記錄）：python3 -m backend.tools.webhook_receiver 9099 crm-local-secret
示範站與瀏覽器端到端用它當 CRM 目的地；只印筆數不印名單。"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

SEEN: set[str] = set()
LOG: list[dict[str, Any]] = []


def make_handler(secret: bytes) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n)
            ts = self.headers.get("X-DMP-Timestamp", "0"); sig = self.headers.get("X-DMP-Signature", "")
            want = "sha256=" + hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(want, sig) and abs(time.time() - int(ts or 0)) < 300
            did = self.headers.get("X-DMP-Delivery-Id", ""); ev = self.headers.get("X-DMP-Event", "")
            try:
                p = json.loads(body or b"{}")
            except ValueError:
                p = {}
            LOG.append({"event": ev, "delivery": did, "ok": ok, "members": len(p.get("members") or []), "dup": did in SEEN})
            print(json.dumps(LOG[-1], ensure_ascii=False), flush=True)
            if not ok:
                self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"bad signature"}'); return
            SEEN.add(did)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b'{"ok":true}')

        def log_message(self, *a: Any) -> None:
            pass
    return H


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else 9099; secret = (argv[1] if len(argv) > 1 else "crm-local-secret").encode()
    print(f"webhook receiver on 127.0.0.1:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), make_handler(secret)).serve_forever()


if __name__ == "__main__":
    main()
