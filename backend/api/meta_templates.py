"""元事件／事件屬性／用戶表屬性的 CSV 範本（openapi /v1/meta/templates/{kind}）：欄位定義、輸出、解析。

- 檔案是 UTF-8 加 BOM，Excel 直接開不會亂碼；第一列是欄位鍵（不可改），# 開頭的列（說明、範例）匯入時略過。
- 多值欄：事件的 properties 寫「key:type:sensitivity[:optional]」以 ; 分隔；enum／values 以 | 分隔；布林收 true／false／是／否／Y／N／1／0。
- 這裡只做格式；驗證與套用（L4 拒絕、敏感等級只升、上線事件走變更單）在 meta router，與單筆登記同一套規則。
"""
from __future__ import annotations

import csv
import io
from typing import Any

MAX_ROWS = 2000
MAX_BYTES = 2 * 1024 * 1024
KINDS = ("events", "properties", "user-properties")

COLUMNS: dict[str, list[tuple[str, str]]] = {
    "events": [("name", "事件名（小寫英數 _ .；舊平台原名可含大寫與 $，只能更新不能新建）"), ("cn", "顯示名"), ("group", "業務線"), ("entity", "主體 user／account／item"),
               ("source", "來源"), ("kind", "類別 behavior／business／trade／marketing"), ("platforms", "平台（逗號分隔）"), ("owner", "負責人"),
               ("properties", "屬性：key:type:sensitivity[:optional]，以 ; 分隔"), ("status", "新事件狀態 draft／live（既有事件忽略，改狀態請用狀態轉移）"), ("note", "備註")],
    "properties": [("key", "屬性名（小寫英數 _ .，可 $ 開頭）"), ("cn", "顯示名"), ("type", "型別 string／number／bool／datetime／date／list／enum"),
                   ("scope", "event／user"), ("sensitivity", "敏感等級 L1–L3（L4 拒絕）"), ("source", "來源"), ("enum", "枚舉值（| 分隔）"), ("unit", "單位")],
    "user-properties": [("key", "屬性名（小寫英數 _）"), ("cn", "顯示名"), ("type", "型別 string／number／bool／datetime／date／enum"), ("values", "枚舉值（| 分隔）"),
                        ("source", "來源"), ("update", "更新頻率（realtime／daily…）"), ("sensitivity", "敏感等級 L1–L3（L4 拒絕）"),
                        ("filterable", "可篩選 true／false"), ("groupable", "可分組 true／false"), ("note", "備註")],
}
EXAMPLES: dict[str, list[dict[str, str]]] = {
    "events": [{"name": "#fund_redeem", "cn": "基金贖回", "group": "基金", "entity": "user", "source": "基金平台", "kind": "business", "platforms": "服務端", "owner": "財富管理部",
                "properties": "fund_code:string:L1; amount:number:L2; channel:string:L1:optional", "status": "draft", "note": "範例列：# 開頭不匯入"}],
    "properties": [{"key": "#fund_code", "cn": "基金代碼", "type": "string", "scope": "event", "sensitivity": "L1", "source": "基金平台", "enum": "", "unit": ""}],
    "user-properties": [{"key": "#risk_level", "cn": "風險屬性", "type": "enum", "values": "保守|穩健|積極", "source": "KYC", "update": "daily", "sensitivity": "L2",
                         "filterable": "true", "groupable": "true", "note": "範例列：# 開頭不匯入"}],
}


class TemplateError(ValueError):
    pass


def keys(kind: str) -> list[str]:
    return [k for k, _ in COLUMNS[kind]]


def to_row(kind: str, item: dict[str, Any]) -> dict[str, str]:
    """API 回應的一筆 → CSV 一列。"""
    if kind == "events":
        props = "; ".join(f"{p['key']}:{p.get('type') or 'string'}:{p.get('sensitivity') or 'L1'}" + ("" if p.get("required", True) else ":optional") for p in item.get("properties") or [])
        return {"name": item["name"], "cn": item.get("cn", ""), "group": item.get("group", ""), "entity": item.get("entity", "user"), "source": item.get("source", ""), "kind": item.get("kind", ""),
                "platforms": item.get("platforms", ""), "owner": item.get("owner", ""), "properties": props, "status": item.get("status", ""), "note": item.get("note", "")}
    if kind == "properties":
        return {"key": item["key"], "cn": item.get("cn", ""), "type": item.get("type", ""), "scope": item.get("scope", "event"), "sensitivity": item.get("sensitivity", ""),
                "source": item.get("source", ""), "enum": "|".join(map(str, item.get("enum") or [])), "unit": item.get("unit", "")}
    return {"key": item["key"], "cn": item.get("cn", ""), "type": item.get("type", ""), "values": "|".join(map(str, item.get("values") or [])), "source": item.get("source", ""),
            "update": str(item.get("update", "")), "sensitivity": item.get("sensitivity", ""), "filterable": "true" if item.get("filterable") else "false",
            "groupable": "true" if item.get("groupable") else "false", "note": item.get("note", "")}


def to_csv(kind: str, items: list[dict[str, Any]] | None = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=keys(kind), lineterminator="\r\n")
    w.writeheader()
    w.writerow({k: ("#說明：" if i == 0 else "") + desc for i, (k, desc) in enumerate(COLUMNS[kind])})
    for ex in EXAMPLES[kind]:
        w.writerow(ex)
    for it in items or []:
        w.writerow(to_row(kind, it))
    return "﻿" + buf.getvalue()


def _bool(v: str) -> bool | None:
    s = v.strip().lower()
    if s in ("true", "1", "y", "yes", "是"): return True
    if s in ("false", "0", "n", "no", "否", ""): return False
    return None


def parse(kind: str, text: str) -> list[tuple[int, dict[str, Any], list[str]]]:
    """CSV 全文 → [(行號, 正規化後的欄位, 格式錯誤)]；表頭不符或超過上限丟 TemplateError。"""
    if kind not in KINDS:
        raise TemplateError(f"kind 只能是 {KINDS}")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise TemplateError("檔案超過 2 MB")
    rd = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        hdr = [h.strip() for h in next(rd)]
    except StopIteration:
        raise TemplateError("檔案是空的") from None
    want = keys(kind)
    missing = [k for k in want if k not in hdr]
    if missing or hdr[0] != want[0]:
        raise TemplateError(f"表頭不符：第一欄須為 {want[0]}，缺 {missing}（請先下載範本）")
    out: list[tuple[int, dict[str, Any], list[str]]] = []
    for line, cells in enumerate(rd, start=2):
        if not any(c.strip() for c in cells) or cells[0].strip().startswith("#"):
            continue
        raw = {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(hdr) if h in want}
        out.append((line, *normalize(kind, raw)))
        if len(out) > MAX_ROWS:
            raise TemplateError(f"單次最多 {MAX_ROWS:,} 列")
    return out


def normalize(kind: str, raw: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    errs: list[str] = []
    if kind == "events":
        props = []
        for part in [x.strip() for x in raw.get("properties", "").replace("；", ";").split(";") if x.strip()]:
            bits = [b.strip() for b in part.split(":")]
            if len(bits) < 1 or not bits[0]:
                errs.append(f"屬性格式錯誤：{part}"); continue
            props.append({"key": bits[0], "type": bits[1] if len(bits) > 1 and bits[1] else "string", "sensitivity": bits[2] if len(bits) > 2 and bits[2] else "L1",
                          "required": not (len(bits) > 3 and bits[3].lower() in ("optional", "選填", "false"))})
        return {**{k: raw.get(k, "") for k in keys(kind) if k != "properties"}, "properties": props}, errs
    if kind == "properties":
        return {**{k: raw.get(k, "") for k in keys(kind) if k != "enum"}, "enum": [x.strip() for x in raw.get("enum", "").split("|") if x.strip()]}, errs
    row: dict[str, Any] = {k: raw.get(k, "") for k in keys(kind) if k not in ("values", "filterable", "groupable")}
    row["values"] = [x.strip() for x in raw.get("values", "").split("|") if x.strip()]
    for k in ("filterable", "groupable"):
        b = _bool(raw.get(k, ""))
        if b is None:
            errs.append(f"{k} 只能是 true／false")
        row[k] = bool(b)
    return row, errs


def selftest() -> None:
    for kind in KINDS:
        txt = to_csv(kind)
        assert txt.startswith("﻿") and parse(kind, txt) == [], kind      # 說明列與範例列都略過
    item = {"name": "fund_redeem", "cn": "基金贖回", "group": "基金", "entity": "user", "source": "基金平台", "kind": "business", "platforms": "服務端", "owner": "財富",
            "properties": [{"key": "fund_code", "type": "string", "sensitivity": "L1", "required": True}, {"key": "memo", "type": "string", "sensitivity": "L2", "required": False}], "status": "draft", "note": ""}
    rows = parse("events", to_csv("events", [item]))
    assert len(rows) == 1 and rows[0][0] == 4 and rows[0][1]["properties"] == item["properties"] and rows[0][1]["cn"] == "基金贖回", rows
    rows = parse("user-properties", "key,cn,type,values,source,update,sensitivity,filterable,groupable,note\nrisk,風險,enum,保守|積極,KYC,daily,L2,是,maybe,\n")
    assert rows[0][1]["values"] == ["保守", "積極"] and rows[0][1]["filterable"] is True and rows[0][2] == ["groupable 只能是 true／false"], rows
    for bad in ("", "cn,key\n", "x" * (MAX_BYTES + 1)):
        try:
            parse("properties", bad)
        except TemplateError:
            pass
        else:
            raise AssertionError(bad[:20])


if __name__ == "__main__":
    selftest(); print("meta templates self-test OK")
