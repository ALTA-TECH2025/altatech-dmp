"""批流一致性測試用的合成資料：全部是假的客戶代碼、帳號與個資樣式，金鑰也是合成的（不是 secret）。"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import backend.integration.modeling as m

MODEL_PATH = Path(__file__).resolve().parents[1] / "integration" / "models.example.yaml"
FIXED_NOW = datetime(2026, 10, 16, tzinfo=UTC)          # 固定時鐘：time_window 檢查結果才可重現
INGEST_MS = 1_760_572_800_000                            # 固定入倉時間：to_ch_row 輸出才可逐字比對
_SYNTHETIC_KEYS = {"k_idm": b"stream-parity-synthetic-idm", "k_acct": b"stream-parity-synthetic-acct"}
# 不得出現在任何輸出 payload 的原值（個資樣式與身分欄位）
LEAKS = ("0912345678", "０９１２３４５６７８", "A123456789", "syn@example.com", "測試甲", "SYN0001", "9900000001")


def install_synthetic_keys() -> None:
    for ref, k in _SYNTHETIC_KEYS.items():
        m.set_key(ref, k)


def _base(**kw: object) -> dict:
    r = {"ORD_NO": "S1", "STATUS": "NEW", "ORD_TIME": "2026-10-15 09:01:00", "UPDATED_AT": "2026-10-15 09:01:00",
         "CUST_ID": "SYN0001", "ACCT_NO": "9900000001", "STK_CODE": " 2330 ", "MKT": "T", "BS_FLAG": "B",
         "ORD_TYPE": "LMT", "PRICE": "1000", "QTY": "2000", "CHANNEL": "A", "FEE": "", "TAX": ""}
    r.update(kw)
    return r


def labelled_rows() -> list[tuple[str, dict]]:
    """(標籤, 列)。標籤說明預期；測試斷言的是「批次 == 串流」與下列 event_id 性質。"""
    return [
        ("ok_new", _base()),
        ("ok_filled", _base(STATUS="FILLED", ORD_TIME="2026-10-15 09:02:30", UPDATED_AT="2026-10-15 09:02:30", FILL_QTY="2000", FEE="2850", TAX="0")),
        ("dup_of_ok_new", _base()),                                                   # 完全相同 → 同一 event_id
        ("dup_other_cust", _base(CUST_ID="SYN9999", ACCT_NO="9900009999")),           # 身分欄不進雜湊 → 同一 event_id
        ("time_plus_1s", _base(ORD_TIME="2026-10-15 09:01:01")),                      # 只差 ORD_TIME → 不同 event_id
        ("updated_plus_1s", _base(UPDATED_AT="2026-10-15 09:01:01")),                 # 只差 UPDATED_AT → 不同 event_id
        ("ok_cancel", _base(ORD_NO="S2", STATUS="CANCELLED", CUST_ID="SYN0002", ACCT_NO="9900000002")),
        ("ok_partial", _base(ORD_NO="S3", STATUS="PARTIAL", FILL_QTY="500", CUST_ID="SYN0003")),
        ("q_enum_market", _base(ORD_NO="S4", STATUS="REJECTED", MKT="X", RJ_CODE="E12")),
        ("q_required_qty", _base(ORD_NO="S5", PRICE="", QTY="")),
        ("q_no_transition", _base(ORD_NO="S6", STATUS="WEIRD")),
        ("q_time_window", _base(ORD_NO="S7", ORD_TIME="2026-01-01 09:06:00")),
        ("q_bad_time", _base(ORD_NO="S8", ORD_TIME="not-a-time")),
        ("q_range_neg", _base(ORD_NO="S9", PRICE="-5")),
        ("pii_mobile", _base(ORD_NO="S10", MEMO="請回電 0912345678")),
        ("pii_mobile_fullwidth", _base(ORD_NO="S11", MEMO="手機０９１２３４５６７８")),
        ("pii_tw_id", _base(ORD_NO="S12", NOTE="身分證A123456789號")),
        ("pii_email", _base(ORD_NO="S13", MEMO="寄到 syn@example.com")),
        ("drop_fields_only", _base(ORD_NO="S14", CUST_NAME="測試甲", PHONE="0912345678", ID_NO="A123456789")),
        ("q_l4_nested", _base(ORD_NO="S15", STATUS="WEIRD", nested={"CUST_NAME": "測試甲", "ok": 1})),
        ("q_masked_status", _base(ORD_NO="S16", STATUS="測試甲")),
    ]
