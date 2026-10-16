"""資料來源連線鑑權：密鑰解析、連線測試診斷、輪替（骨架，可直接執行自測）。

設計（docs/06 §7）：
- Connection 物件只存 secret_ref，不存明文；明文只在執行節點（Flink / Kafka Connect / Spark）透過 Workload Identity 從密鑰庫取得。
- 無密碼優先：BigQuery Workload Identity／SA 模擬、Cloud SQL IAM token、MSSQL Kerberos；密碼型憑證才有輪替到期。
- 連線測試是逐步診斷（dns → tcp → tls → auth → privilege → cdc_prereq → sample_read → clock_skew），哪一步失敗就回該步的修法。
- 每次測試、輪替、以及執行節點每次取密鑰都寫審計（integration.connection.* / secret.accessed）。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).parent
CONNECTORS = yaml.safe_load((HERE / "connectors.yaml").read_text(encoding="utf-8"))
BY_TYPE = {c["type"]: c for c in CONNECTORS["connectors"]}
ROTATION = CONNECTORS["rotation_policy"]
SECRET_REF_RE = {
    "gcp_secret_manager": re.compile(r"^projects/[^/]+/secrets/[^/]+/versions/(\d+|latest)$"),
    "openbao": re.compile(r"^kv/data/dmp/[a-z]+/[a-z0-9_]+#[a-z_]+$"),
    "k8s_secret": re.compile(r"^secret://[a-z0-9-]+/[a-z0-9-]+/[a-zA-Z0-9_.-]+$"),
}
MASK = "••••••••"


@dataclass
class Connection:
    id: str
    type: str                                   # mssql | postgresql | bigquery | sensorsdata | kafka | gcs
    auth_method: str                            # 見 connectors.yaml auth_methods
    fields: dict[str, Any]                      # 非機密欄位（host、database、project_id…）
    secret_refs: dict[str, str] = field(default_factory=dict)   # secret_field → secret_ref（永不放明文）
    tls_verify: bool = True
    created_by: str = ""
    rotated_at: datetime | None = None
    last_test: dict[str, Any] | None = None

    # ---- 驗證：型別／方法／欄位／secret_ref 格式
    def validate(self) -> list[str]:
        errs: list[str] = []
        spec = BY_TYPE.get(self.type)
        if not spec:
            return [f"未知的來源類型 {self.type}"]
        methods = spec.get("auth_methods", {})
        m = methods.get(self.auth_method)
        if m is None:
            return [f"{self.type} 不支援鑑權方式 {self.auth_method}；可用：{', '.join(methods)}"]
        for f in m["fields"]:
            if not self.fields.get(f):
                errs.append(f"缺少欄位 {f}")
        for sf in m["secret_fields"]:
            ref = self.secret_refs.get(sf)
            if not ref:
                errs.append(f"缺少密鑰 {sf}（須以 secret_ref 提供，不接受明文）")
            elif not any(rx.match(ref) for rx in SECRET_REF_RE.values()):
                errs.append(f"{sf} 的 secret_ref 格式不合法：{ref}")
        for k, v in list(self.fields.items()):
            if isinstance(v, str) and _looks_like_secret(k, v):
                errs.append(f"欄位 {k} 疑似明文密鑰，請改用 secret_ref")
        if spec.get("tls", {}).get("required") and not self.tls_verify:
            errs.append("此來源要求 TLS 並驗證伺服器憑證（tls_verify=false 不允許）")
        return errs

    # ---- UI 回顯：密鑰永遠遮罩，只給參照與版本
    def redacted(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "auth_method": self.auth_method, "fields": self.fields,
            "secrets": {k: {"ref": v, "value": MASK} for k, v in self.secret_refs.items()},
            "tls_verify": self.tls_verify, "rotation": self.rotation_status(), "last_test": self.last_test,
        }

    def rotation_status(self) -> dict[str, Any]:
        spec = BY_TYPE[self.type]["auth_methods"][self.auth_method]
        days = spec.get("rotation_days")
        if not days:
            return {"kind": "short_lived", "note": "短效憑證／無密鑰，不需輪替"}
        base = self.rotated_at or datetime.now(UTC)
        due = base + timedelta(days=days)
        left = (due - datetime.now(UTC)).days
        return {"kind": "rotating", "every_days": days, "due": due.date().isoformat(), "days_left": left,
                "state": "expired" if left < 0 else "warn" if left <= ROTATION["warn_days_before"] else "ok"}


def _looks_like_secret(key: str, value: str) -> bool:
    k = key.lower()
    if any(w in k for w in ("password", "secret", "token", "key", "keytab")):
        return True
    return value.startswith("-----BEGIN") or (len(value) > 40 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", value) is not None)


# ------------------------------------------------------------------ 密鑰解析（只在執行節點呼叫）
class SecretResolver:
    """依 secret_ref 前綴選 provider；取值寫審計；呼叫端永遠拿到的是短生命週期物件。"""

    def __init__(self, providers: dict[str, Callable[[str], str]], audit: Callable[[dict], None]):
        self.providers, self.audit = providers, audit

    def resolve(self, ref: str, *, on_behalf_of: str, conn_id: str) -> str:
        for name, rx in SECRET_REF_RE.items():
            if rx.match(ref):
                value = self.providers[name](ref)
                self.audit({"event": "secret.accessed", "provider": name, "ref": ref, "conn_id": conn_id,
                            "on_behalf_of": on_behalf_of, "fingerprint": hashlib.sha256(value.encode()).hexdigest()[:12]})
                return value
        raise ValueError(f"無法辨識的 secret_ref：{ref}")


# ------------------------------------------------------------------ 連線測試（逐步診斷）
FIX_HINTS = {
    "dns": "檢查主機名稱／私有 DNS 區域；GCP 需 Private Google Access 或 Cloud DNS 轉發",
    "tcp": "檢查防火牆／安全群組是否放行 dmp 的固定出口 IP 與埠號",
    "tls": "伺服器憑證不受信任：提供 ca_bundle_ref，或請 DBA 換成內部 CA 簽發的憑證；不要關閉驗證",
    "auth": "帳密／keytab／服務主體錯誤或過期：確認密鑰庫版本、Kerberos 時間同步、Entra ID 權杖範圍",
    "privilege": "帳號權限不足：依 least_privilege 清單補授權（唯讀即可，不需 db_owner／superuser）",
    "cdc_prereq": "CDC 前置未完成：請 DBA 執行 cdc 啟用／wal_level=logical／建立 publication 與 replication slot",
    "sample_read": "可連但讀不到資料：確認表名、schema、分區／分片表命名（events_YYYYMMDD）",
    "clock_skew": "來源時鐘偏移 > 5 秒會影響 Kerberos 與事件時間：請對時 NTP",
}


def test_connection(conn: Connection, probes: dict[str, Callable[[Connection], tuple[bool, str]]]) -> dict[str, Any]:
    """probes：每一步的實際探測函式（由各連接器實作）；此處只負責順序、短路、計時與結果格式。"""
    steps = CONNECTORS["connection_test"]["steps"]
    errs = conn.validate()
    result: dict[str, Any] = {"conn_id": conn.id, "at": datetime.now(UTC).isoformat(), "ok": not errs, "steps": []}
    if errs:
        result["steps"].append({"step": "validate", "ok": False, "detail": "; ".join(errs)})
        return result
    for step in steps:
        probe = probes.get(step)
        if probe is None:
            result["steps"].append({"step": step, "ok": True, "detail": "不適用"})
            continue
        ok, detail = probe(conn)
        result["steps"].append({"step": step, "ok": ok, "detail": detail, **({} if ok else {"fix": FIX_HINTS[step]})})
        if not ok:
            result["ok"] = False
            break
    conn.last_test = {"at": result["at"], "ok": result["ok"], "failed_step": next((s["step"] for s in result["steps"] if not s["ok"]), None)}
    return result


# ------------------------------------------------------------------ 輪替
def rotate(conn: Connection, new_ref: str, audit: Callable[[dict], None]) -> dict[str, Any]:
    """新舊版本並存 overlap_hours；執行中的作業用舊版跑完，新作業用新版；到期後舊版由密鑰庫停用。"""
    sf = BY_TYPE[conn.type]["auth_methods"][conn.auth_method]["secret_fields"]
    if not sf:
        return {"ok": False, "reason": "此鑑權方式為短效憑證，無需輪替"}
    old = conn.secret_refs.get(sf[0])
    conn.secret_refs[sf[0]] = new_ref
    conn.rotated_at = datetime.now(UTC)
    overlap_until = conn.rotated_at + timedelta(hours=ROTATION["overlap_hours"])
    audit({"event": "integration.connection.rotated", "conn_id": conn.id, "old_ref": old, "new_ref": new_ref, "overlap_until": overlap_until.isoformat()})
    return {"ok": True, "overlap_until": overlap_until.isoformat()}


# ------------------------------------------------------------------ 自測
if __name__ == "__main__":
    audit_log: list[dict] = []
    good = Connection(id="trade_core", type="mssql", auth_method="kerberos",
                      fields={"host": "sql-core.bank.internal", "port": 1433, "database": "TRADE", "principal": "svc-dmp-ro@BANK.LOCAL"},
                      secret_refs={"keytab": "kv/data/dmp/prod/trade_core#keytab"}, created_by="windy")
    assert good.validate() == [], good.validate()
    bad = Connection(id="x", type="postgresql", auth_method="password", fields={"host": "h", "port": 5432, "database": "crm", "username": "u", "password": "P@ssw0rd-in-plain"}, secret_refs={})
    errs = bad.validate(); assert any("明文" in e for e in errs) and any("缺少密鑰" in e for e in errs), errs
    bq = Connection(id="ga_bq", type="bigquery", auth_method="workload_identity", fields={"project_id": "ga-export", "dataset": "analytics_3012", "location": "asia-east1"})
    assert bq.validate() == [] and bq.rotation_status()["kind"] == "short_lived"
    # 測試：第三步 tls 失敗就短路
    probes = {"dns": lambda c: (True, "10.20.3.5"), "tcp": lambda c: (True, "1433 open, 2 ms"), "tls": lambda c: (False, "self-signed certificate"), "auth": lambda c: (True, "")}
    r = test_connection(good, probes)
    assert r["ok"] is False and r["steps"][-1]["step"] == "tls" and "ca_bundle_ref" in r["steps"][-1]["fix"]
    # 輪替
    pw = Connection(id="crm_pg", type="postgresql", auth_method="password", fields={"host": "h", "port": 5432, "database": "crm", "username": "dmp_ro"}, secret_refs={"password": "projects/p/secrets/crm-pg/versions/3"}, rotated_at=datetime.now(UTC) - timedelta(days=80))
    assert pw.rotation_status()["state"] == "warn"
    assert rotate(pw, "projects/p/secrets/crm-pg/versions/4", audit_log.append)["ok"] and pw.rotation_status()["state"] == "ok"
    # 密鑰解析只在執行節點，並寫審計
    res = SecretResolver({"openbao": lambda ref: "KEYTAB-BYTES", "gcp_secret_manager": lambda ref: "pw", "k8s_secret": lambda ref: "x"}, audit_log.append)
    assert res.resolve("kv/data/dmp/prod/trade_core#keytab", on_behalf_of="flink-job", conn_id="trade_core") == "KEYTAB-BYTES"
    assert audit_log[-1]["event"] == "secret.accessed" and "KEYTAB" not in json.dumps(audit_log)
    print("auth.py self-test ok; redacted:", json.dumps(pw.redacted(), ensure_ascii=False)[:160], "...")
