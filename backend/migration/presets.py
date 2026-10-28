#!/usr/bin/env python3
"""SDK 預置屬性契約（semantic/preset_properties.yaml）的載入與查詢。

模擬資料生成器與屬性校驗共用：給「平台 + 啟用條件」回該平台事件／用戶的有效預置屬性，型別一律取契約宣告。
用法：python3 backend/migration/presets.py           # 自測：契約自身一致（型別、平台、條件引用、列號不重複）與查詢結果
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "semantic" / "preset_properties.yaml"
SDK_PLATFORMS = ("android", "ios", "js")


def load(path: pathlib.Path = CONTRACT) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def platform_of(name: str, c: dict[str, Any] | None = None) -> str | None:
    """events.yaml（JavaScript／iOS／Android／服務端／其它）或事件規格表（WEB／IOS／ANDROID／SERVER／OTHER）的平台名 → js／ios／android；非 SDK 平台回 None。"""
    c = c or load()
    for code, m in c["platforms"].items():
        if name in m["events_yaml"] or name.upper() in m["spec_sheet"]:
            return code
    return None


def default_flags(c: dict[str, Any] | None = None) -> dict[str, bool]:
    c = c or load()
    return {k: bool(v["default"]) for k, v in c["conditions"].items()}


def _enabled(p: dict[str, Any], platform: str, flags: dict[str, bool]) -> bool:
    cond = (p.get("condition_by_platform") or {}).get(platform) or p.get("condition")
    return cond is None or bool(flags.get(cond))


def event_presets(platform: str, flags: dict[str, bool] | None = None, *, event: str = "", preset_event: bool = False,
                  c: dict[str, Any] | None = None) -> dict[str, str]:
    """該平台、該組啟用條件下事件會帶的預置屬性 → 型別。preset_event：SDK 全埋點事件（$ 開頭）才帶 only_preset_events 的屬性。
    非 SDK 平台（服務端／其它）回空：不繼承預置屬性。"""
    c = c or load(); flags = default_flags(c) if flags is None else flags
    if platform not in SDK_PLATFORMS:
        return {}
    out = {p["key"]: p["type"] for p in c["event_presets"]
           if platform in p["platforms"] and _enabled(p, platform, flags) and (preset_event or not p.get("only_preset_events"))}
    for p in c.get("event_scoped_presets", []):
        if event in (p["events"].get(platform) or []):
            out[p["key"]] = p["type"]
    return out


def user_presets(platform: str, flags: dict[str, bool] | None = None, *, c: dict[str, Any] | None = None) -> dict[str, str]:
    """該平台 SDK 會自動寫進用戶表的預置屬性 → 型別（只保留欄位名的 reserved_only 不算）。"""
    c = c or load(); flags = default_flags(c) if flags is None else flags
    return {p["key"]: p["type"] for p in c["user_presets"]
            if not p.get("reserved_only") and platform in (p.get("collect") or {}) and flags.get(p["collect"][platform], False)}


def check(c: dict[str, Any] | None = None) -> list[str]:
    """契約自身一致：型別都在正規化後的型別集合、平台與條件引用存在、鍵與列號不重複。回錯誤清單（空 = 通過）。"""
    c = c or load(); errs: list[str] = []
    types = set(c["types"].values()); conds = set(c["conditions"])
    rows: dict[int, str] = {}
    for sec in ("event_presets", "user_presets"):
        seen: set[str] = set()
        for p in c[sec]:
            k = p["key"]
            if k in seen: errs.append(f"{sec}: {k} 重複")
            seen.add(k)
            if p["type"] not in types: errs.append(f"{sec}: {k} 型別 {p['type']} 不在 {sorted(types)}")
            r = p.get("spec_row")
            if not isinstance(r, int): errs.append(f"{sec}: {k} 缺 spec_row")
            elif r in rows: errs.append(f"{sec}: {k} 與 {rows[r]} 同列 {r}")
            else: rows[r] = k
            for pl in p.get("platforms", []):
                if pl not in SDK_PLATFORMS: errs.append(f"{sec}: {k} 平台 {pl} 不認得")
            refs = [p.get("condition")] + list((p.get("condition_by_platform") or {}).values()) + list((p.get("collect") or {}).values())
            for cd in refs:
                if cd and cd not in conds: errs.append(f"{sec}: {k} 引用不存在的條件 {cd}")
            for pl in (p.get("condition_by_platform") or {}):
                if pl not in p.get("platforms", []): errs.append(f"{sec}: {k} 的 condition_by_platform 平台 {pl} 不在 platforms")
            for pl in (p.get("collect") or {}):
                if pl not in SDK_PLATFORMS: errs.append(f"{sec}: {k} collect 平台 {pl} 不認得")
    for p in c.get("event_scoped_presets", []):
        if p["type"] not in types: errs.append(f"event_scoped_presets: {p['key']} 型別 {p['type']}")
    clash = {p["key"] for p in c["event_presets"] if not p.get("reserved")} & set(c["reserved_property_names"])
    if clash: errs.append(f"非保留的預置屬性用了保留名：{sorted(clash)}")
    return errs


def selftest() -> None:
    c = load()
    assert check(c) == [], check(c)
    assert len(c["event_presets"]) == 41 and len(c["user_presets"]) == 17, (len(c["event_presets"]), len(c["user_presets"]))   # 規格表 4–44 列、49–65 列
    assert platform_of("JavaScript", c) == "js" and platform_of("WEB", c) == "js" and platform_of("iOS", c) == "ios" and platform_of("ANDROID", c) == "android"
    assert platform_of("服務端", c) is None and platform_of("OTHER", c) is None
    android, ios, js = (event_presets(p, c=c) for p in SDK_PLATFORMS)
    assert "$browser" in js and "$browser" not in ios and "$app_version" in ios and "$app_version" not in js       # 平台適用
    assert "$wifi" in android and "$wifi" not in js and "$url" in js and "$url" not in android
    assert "$latitude" not in ios and "$screen_orientation" not in android and "$device_id" not in js and "$device_id" in ios   # 預設不開的手動屬性
    on = {**default_flags(c), "manual_location": True, "js_track_device_id": True}
    assert "$latitude" in event_presets("ios", on, c=c) and event_presets("ios", on, c=c)["$latitude"] == "NUMBER" and "$device_id" in event_presets("js", on, c=c)
    assert "$latitude" not in event_presets("js", on, c=c)                                                         # 開了也只在 App
    assert "$lib_detail" not in ios and "$lib_detail" in event_presets("ios", preset_event=True, c=c)               # 只有 SDK 預置事件才有
    assert "$is_first_time" in event_presets("js", event="$pageview", c=c) and "$is_first_time" not in event_presets("js", event="kh_apply", c=c)
    assert event_presets("服務端", c=c) == {} and "$latest_scene" not in js
    assert js["time"] == "DATETIME" and js["$receive_time"] == "DATETIME" and js["$ip"] == "STRING"                  # TIME／字符串 已正規化
    assert set(user_presets("js", c=c)) >= {"$first_visit_time", "$first_referrer", "$utm_source"} and "$utm_matching_type" not in user_presets("js", c=c)
    assert set(user_presets("ios", c=c)) == {"$first_visit_time", "$utm_matching_type", "$utm_source", "$utm_medium", "$utm_term", "$utm_content", "$utm_campaign"}
    assert "$name" not in user_presets("js", c=c)                                                                  # 只保留欄位名
    assert user_presets("ios", {**default_flags(c), "channel_tracking": False}, c=c) == {"$first_visit_time": "DATETIME"}
    bad = {**c, "event_presets": c["event_presets"] + [{"key": "$x", "type": "TEXT", "platforms": ["web"], "condition": "nope", "spec_row": 4}]}
    errs = check(bad); assert any("TEXT" in e for e in errs) and any("web" in e for e in errs) and any("nope" in e for e in errs) and any("同列 4" in e for e in errs), errs
    print(f"presets self-test ok: {len(c['event_presets'])} event presets, {len(c['user_presets'])} user presets; js={len(js)} ios={len(ios)} android={len(android)} by default flags")


if __name__ == "__main__":
    selftest()
