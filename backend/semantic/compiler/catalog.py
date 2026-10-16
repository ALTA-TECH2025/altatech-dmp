"""語意目錄（Catalog）：把 semantic/*.yaml 與 security/masking.yaml 讀成編譯器要用的查表。

欄位引用（query 裡的 field 字串）解析規則：
  platform / channel / utm_source / symbol / amount …   → dmp.events 實體欄位
  $event_hour                                             → toHour(time, 'Asia/Taipei')
  dimensions.yaml 的 name（market → props.market）        → JSON 子欄位 CAST 成指定型別
  props.<key>（任意事件屬性）                              → JSON 子欄位（型別由 query 的 type 或預設 string）
  user.<key>（user_properties.yaml 的屬性）                → 熱欄位走 dictGet('dmp.users_dict')，冷欄位走 JOIN dmp.users FINAL
  seg.<segment_id>                                        → one_id_uint IN (SELECT … FROM dmp.segment_members)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore

SEMANTIC = Path(__file__).resolve().parents[1]
SECURITY = SEMANTIC.parent / "security"
TZ = "Asia/Taipei"

# dmp.events 的實體欄位與型別（schema.sql）；LowCardinality(String) 未設值為空字串
EVENT_COLUMNS: dict[str, str] = {
    "event": "string", "source": "string", "platform": "string", "app_id": "string", "symbol": "string", "side": "string", "channel": "string",
    "utm_source": "string", "utm_campaign": "string", "entity_type": "string", "entity_id": "string", "session_id": "string", "distinct_id": "string",
    "amount": "number", "late": "bool", "schema_version": "number",
}
EVENT_COLUMN_ALIASES = {"$utm_source": "utm_source", "$utm_campaign": "utm_campaign", "$platform": "platform"}
MV_COLUMNS = ("platform", "channel", "utm_source")          # events_daily_agg 的分組鍵（除 project/event_date/event）
JSON_CAST = {"string": "Nullable(String)", "number": "Nullable(Float64)", "bool": "Nullable(UInt8)", "date": "Nullable(Date)"}


@dataclass(frozen=True)
class FieldRef:
    key: str            # query 裡的原字串
    kind: str           # column | expr | json | user_hot | user_cold | segment
    type: str           # string | number | bool | date
    sql: str            # 在 FROM dmp.events AS e 下的表達式；user_cold 用 u.<col>
    name: str = ""      # 欄位名（遮罩表查找用）
    mv: bool = False    # events_daily_agg 是否有此欄位


class Catalog:
    def __init__(self) -> None:
        ev = yaml.safe_load((SEMANTIC / "events.yaml").read_text(encoding="utf-8"))["events"]
        self.events: dict[str, dict[str, Any]] = {e["name"]: e for e in ev}
        self.aliases: dict[str, str] = {a: e["name"] for e in ev for a in (e.get("aliases") or [])}
        self.dimensions: dict[str, dict[str, Any]] = {d["name"]: d for d in yaml.safe_load((SEMANTIC / "dimensions.yaml").read_text(encoding="utf-8"))["dimensions"]}
        self.metrics: dict[str, dict[str, Any]] = {m["name"]: m for m in yaml.safe_load((SEMANTIC / "metrics.yaml").read_text(encoding="utf-8"))["metrics"]}
        up = yaml.safe_load((SEMANTIC / "user_properties.yaml").read_text(encoding="utf-8"))
        self.user_props: dict[str, dict[str, Any]] = {p["key"]: p for p in up["user_properties"]}
        self.forbidden_l4: set[str] = set(up.get("forbidden_l4", []))
        # 熱欄位 = gen_users_ddl.hot 的同一規則（可篩選或可分組、且 L0–L2）→ 在 dmp.users_dict
        stored = [k for k, p in self.user_props.items() if p.get("sensitivity", "L1") in ("L0", "L1", "L2")]          # L3 不進用戶寬表（gen_users_ddl.columns）
        self.stored_props: set[str] = set(stored)
        self.hot_props: set[str] = set([k for k in stored if self.user_props[k].get("filterable") or self.user_props[k].get("groupable")][:50])
        self.masking: dict[str, dict[str, Any]] = {f["name"]: f for f in yaml.safe_load((SECURITY / "masking.yaml").read_text(encoding="utf-8"))["fields"]}

    def canonical_event(self, name: str) -> str | None:
        if name in self.events: return name
        return self.aliases.get(name)

    def resolve(self, key: str, type_hint: str | None = None) -> FieldRef:
        """欄位引用 → FieldRef。找不到就丟 ValueError（UI 應只能選目錄裡的欄位）。"""
        k = EVENT_COLUMN_ALIASES.get(key, key)
        if k == "$event_hour":
            return FieldRef(key, "expr", "number", f"toHour(e.time, '{TZ}')", "event_hour")
        if k == "$event_date":
            return FieldRef(key, "expr", "date", "e.event_date", "event_date")
        if k == "one_id":
            return FieldRef(key, "column", "string", "e.one_id", "one_id")
        if k in EVENT_COLUMNS:
            return FieldRef(key, "column", EVENT_COLUMNS[k], f"e.{k}", k, mv=k in MV_COLUMNS or k == "event")
        if k in self.dimensions:
            d = self.dimensions[k]; col = d["column"]; t = d.get("type", "string")
            if col in EVENT_COLUMNS:
                return FieldRef(key, "column", EVENT_COLUMNS[col], f"e.{col}", col, mv=col in MV_COLUMNS)
            if col.startswith("props."):
                return self._json(key, col[6:], t)
            if col.startswith(("users.", "tags.")):
                return self._user(key, col.split(".", 1)[1])
            if col.startswith("items."):   # 客戶歸屬維度（分公司／營業員）在用戶表，不在事件列
                return self._user(key, col[6:])
        if k.startswith("props."):
            return self._json(key, k[6:], type_hint or "string")
        if k.startswith(("user.", "users.")):
            return self._user(key, k.split(".", 1)[1])
        if k.startswith("seg."):
            sid, _, ver = k[4:].partition("@")           # seg.<id>（最新版本）或 seg.<id>@<version>（指定版本）
            if ver and not ver.isdigit():
                raise ValueError(f"分群版本需為整數：{key}")
            return FieldRef(key, "segment", "segment", ver, sid)
        raise ValueError(f"未知欄位 {key}：請在 dimensions.yaml／user_properties.yaml 登記，或用 props.<key>")

    def _json(self, key: str, path: str, t: str) -> FieldRef:
        if t not in JSON_CAST:
            raise ValueError(f"props 欄位型別只能是 string/number/bool/date：{key}={t}")
        # 型別化子欄位（props.x.:Float64）對整數值回 NULL（e2e 實測），一律用 CAST
        return FieldRef(key, "json", t, f"CAST(e.props.`{path}`, '{JSON_CAST[t]}')", path.lstrip("$"))

    def _user(self, key: str, prop: str) -> FieldRef:
        if prop in self.forbidden_l4:
            raise ValueError(f"用戶屬性 {prop} 為 L4 個資，不存在於分析倉")
        p = self.user_props.get(prop)
        if not p:
            raise ValueError(f"用戶屬性 {prop} 未在 user_properties.yaml 登記")
        if prop not in self.stored_props:
            raise ValueError(f"用戶屬性 {prop} 為 L3，不在分析倉用戶寬表（只在 id_map／CRM），不能用於查詢")
        t = {"enum": "string", "string": "string", "number": "number", "bool": "bool", "date": "date", "datetime": "date"}[p["type"]]
        sql = f"dictGet('dmp.users_dict', '{prop}', e.one_id_uint)" if prop in self.hot_props else f"u.`{prop}`"
        if p["type"] == "datetime":
            sql = f"toDate({sql}, '{TZ}')"            # 時間型用戶屬性以台北日期比較（rel／between 都是日期口徑）
        return FieldRef(key, "user_hot" if prop in self.hot_props else "user_cold", t, sql, prop)
