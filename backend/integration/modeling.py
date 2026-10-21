"""可視化建模引擎（第一階段微批版，docs/06 §7、docs/23 §4.1）。

建模精靈七步的產物是一份 model YAML（格式見 models.example.yaml）。本模組負責：
  load()        讀 YAML → Model
  validate()    對語意層檢查：事件名（含別名）、目標欄位、身分欄位、禁止個資、轉換函式
  preview()     以樣本列跑「④ 欄位映射 → ⑤ 清洗規則」，回通過／隔離／拒絕與原因（API /sync-tasks/{id}/preview）
  apply()       同 preview，但輸出可直接寫入 ClickHouse 的列（runner 用）
  compile_sql() 產生 ClickHouse INSERT … SELECT（raw 表 → 目標表）的標準化 SQL；含密鑰的轉換（hmac／fpe）不在 SQL 做，
                由 Python 端在暫存階段完成（docs/23：金鑰從 KMS／HSM，不進 ClickHouse）

純標準函式庫 + PyYAML；轉換函式在 transforms.py。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import transforms as T

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC = ROOT / "semantic"

# events 表的實體欄位（schema.sql）；其餘目標欄位進 props JSON
EVENT_COLUMNS = {"project", "time", "event", "source", "one_id", "entity_type", "entity_id", "distinct_id", "session_id",
                 "event_id", "platform", "app_id", "symbol", "side", "channel", "amount", "utm_source", "utm_campaign", "batch_id"}
IDENTITY_KEYS = {"$identity_login_id", "$identity_mobile_hash", "$identity_account_no", "$identity_device_id",
                 "$identity_cookie_id", "$identity_anonymous_id", "$identity_line_uid"}
# 密鑰參照（secret_ref）→ 測試用金鑰；正式環境由 KMS／HSM 注入，絕不寫進設定檔
KEYS: dict[str, bytes] = {}


def set_key(ref: str, key: bytes) -> None:
    KEYS[ref] = key


# ---------------------------------------------------------------- 轉換：名稱 → (python fn, ClickHouse 表達式)
def _fpe_digits(v: Any, key: bytes) -> str | None:
    """保留格式的代碼化（測試用實作）：位數不變、數字以 HMAC 金鑰流置換；正式環境換 FF1。"""
    if v in (None, ""):
        return None
    s = str(v)
    stream = hashlib.sha256(key + s.encode()).digest()
    out = []
    for i, ch in enumerate(s):
        out.append(str((int(ch) + stream[i % len(stream)]) % 10) if ch.isdigit() else ch)
    return "".join(out)


def _token_v1(v: Any, key: bytes) -> str | None:
    """docs/20：v1_ + 20 字元客戶代碼（HMAC-SHA256 截 20 hex）。"""
    h = T.hmac_sha256(v, key)
    return None if h is None else "v1_" + h[:20]


TRANSFORMS: dict[str, tuple[Callable[..., Any], Callable[[str, list[str]], str] | None]] = {
    "trim": (T.trim, lambda c, a: f"trimBoth({c})"),
    "lower": (T.lower, lambda c, a: f"lowerUTF8({c})"),
    "upper": (T.upper, lambda c, a: f"upperUTF8({c})"),
    "to_int": (T.to_int, lambda c, a: f"toInt64OrNull(toString({c}))"),
    "to_decimal": (lambda v, p=2: T.to_decimal(v, int(p)), lambda c, a: f"round(toFloat64OrNull(toString({c})), {a[0] if a else 2})"),
    "to_ms_utc": (lambda v, tz="Asia/Taipei": T.to_ms_utc(v, tz), lambda c, a: f"toDateTime64({c}, 3, '{a[0] if a else 'Asia/Taipei'}')"),
    "map": (None, None),            # 特殊處理（字典參數）
    "hmac_sha256": (None, None),    # 密鑰：只在 Python 端
    "fpe": (None, None),
    "token_v1": (None, None),
}
SECRET_TRANSFORMS = {"hmac_sha256", "fpe", "token_v1"}


def parse_transform(spec: str | None) -> tuple[str, list[Any], dict[str, Any]]:
    """'map({T: TSE, O: OTC})' → ('map', [], {'T':'TSE','O':'OTC'})；'to_decimal(2)' → ('to_decimal', ['2'], {})；'trim' → ('trim', [], {})"""
    if not spec:
        return "", [], {}
    m = re.fullmatch(r"\s*([a-z_0-9]+)\s*(?:\((.*)\))?\s*", spec, re.S)
    if not m:
        raise ValueError(f"轉換語法錯誤：{spec}")
    name, inner = m.group(1), (m.group(2) or "").strip()
    if name == "map":
        mm = re.fullmatch(r"\{(.*)\}", inner, re.S)
        if not mm:
            raise ValueError(f"map 需要 {{k: v, …}}：{spec}")
        mapping = {}
        for pair in filter(None, [p.strip() for p in mm.group(1).split(",")]):
            k, _, v = pair.partition(":")
            mapping[k.strip()] = v.strip()
        return name, [], mapping
    args = [a.strip() for a in inner.split(",")] if inner else []
    return name, args, {}


def apply_transform(spec: str | None, v: Any) -> Any:
    name, args, kw = parse_transform(spec)
    if not name:
        return v
    if name == "map":
        return T.map_values(v, kw)
    if name in SECRET_TRANSFORMS:
        key = KEYS.get(args[0] if args else "")
        if key is None:
            raise KeyError(f"密鑰參照未注入：{args[0] if args else '?'}")
        return {"hmac_sha256": T.hmac_sha256, "fpe": _fpe_digits, "token_v1": _token_v1}[name](v, key)
    fn = TRANSFORMS[name][0]
    return fn(v, *args) if args else fn(v)


def transform_sql(spec: str | None, col: str) -> str:
    name, args, kw = parse_transform(spec)
    if not name:
        return col
    if name == "map":
        ks = ", ".join(f"'{k}'" for k in kw); vs = ", ".join(f"'{v}'" for v in kw.values())
        return f"transform({col}, [{ks}], [{vs}], NULL)"
    if name in SECRET_TRANSFORMS:
        return col  # 已在暫存階段完成
    return TRANSFORMS[name][1](col, args)


# ---------------------------------------------------------------- 安全的表達式（expr: "PRICE * QTY"）
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.Load)


def eval_expr(expr: str, row: dict[str, Any]) -> Any:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"表達式只允許四則運算與欄位：{expr}")
    def ev(n):
        if isinstance(n, ast.Expression): return ev(n.body)
        if isinstance(n, ast.Constant): return n.value
        if isinstance(n, ast.Name):
            v = row.get(n.id)
            return None if v in (None, "") else float(v)
        if isinstance(n, ast.UnaryOp): x = ev(n.operand); return None if x is None else -x
        a, b = ev(n.left), ev(n.right)
        if a is None or b is None: return None
        return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b, ast.Div: (a / b if b else None)}[type(n.op)]
    return ev(tree)


def expr_sql(expr: str) -> str:
    return re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", r"toFloat64OrNull(toString(\1))", expr)


# ---------------------------------------------------------------- when 條件（"event in (a, b)"、"event = x"）
def when_ok(cond: str | None, out: dict[str, Any]) -> bool:
    if not cond:
        return True
    m = re.fullmatch(r"\s*(\w+)\s+in\s*\((.*)\)\s*", cond)
    if m:
        return str(out.get(m.group(1))) in {x.strip() for x in m.group(2).split(",")}
    m = re.fullmatch(r"\s*(\w+)\s*=\s*(\S+)\s*", cond)
    if m:
        return str(out.get(m.group(1))) == m.group(2)
    raise ValueError(f"when 語法不支援：{cond}")


# ---------------------------------------------------------------- 模型
@dataclass
class Model:
    raw: dict[str, Any]

    @property
    def id(self) -> str: return self.raw["id"]
    @property
    def entity(self) -> str: return self.raw.get("entity", "event")
    @property
    def source(self) -> dict[str, Any]: return self.raw.get("source", {})
    @property
    def fields(self) -> list[dict[str, Any]]: return self.raw.get("fields", [])
    @property
    def identities(self) -> list[dict[str, Any]]: return self.raw.get("identities", [])
    @property
    def cleansing(self) -> list[dict[str, Any]]: return self.raw.get("cleansing", [])
    @property
    def target_table(self) -> str:
        return {"event": "dmp.events", "user": "dmp.users"}.get(self.entity) or self.raw.get("target", {}).get("table", "")
    @property
    def event_names(self) -> list[str]:
        if self.raw.get("event"):
            return [self.raw["event"]]
        return [t["event"] for t in self.raw.get("eventize", {}).get("transitions", []) if t.get("event")]


def load(path: str | Path) -> Model:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Model(data["model"] if "model" in data else data)


@dataclass
class Semantic:
    events: dict[str, dict[str, Any]]
    aliases: dict[str, str]
    user_props: set[str]
    forbidden_l4: set[str]
    dim_tables: dict[str, dict[str, Any]]

    @classmethod
    def load(cls) -> Semantic:
        ev = yaml.safe_load((SEMANTIC / "events.yaml").read_text(encoding="utf-8"))["events"]
        up = yaml.safe_load((SEMANTIC / "user_properties.yaml").read_text(encoding="utf-8"))
        dt = yaml.safe_load((SEMANTIC / "dimension_tables.yaml").read_text(encoding="utf-8"))
        dims = {}
        for d in dt.get("dimension_tables", []) if isinstance(dt, dict) else []:
            name = d.get("name") or d.get("table") or d.get("id")
            if name:
                dims[name] = d
        return cls(events={e["name"]: e for e in ev},
                   aliases={a: e["name"] for e in ev for a in (e.get("aliases") or [])},
                   user_props={u["key"] for u in up["user_properties"]},
                   forbidden_l4=set(up.get("forbidden_l4", [])), dim_tables=dims)

    def canonical_event(self, name: str) -> str | None:
        if name in self.events: return name
        return self.aliases.get(name)


def validate(model: Model, sem: Semantic | None = None) -> list[str]:
    """回傳問題清單（空 = 通過）。"""
    sem = sem or Semantic.load()
    errs: list[str] = []
    if model.entity not in ("event", "user", "dim"):
        errs.append(f"entity 必須是 event／user／dim：{model.entity}")
    if model.entity == "event":
        if not model.event_names:
            errs.append("事件模型需要 event 或 eventize.transitions")
        for n in model.event_names:
            if not sem.canonical_event(n):
                errs.append(f"事件 {n} 不在事件字典（含別名）；請先在元事件管理登記")
        if not model.raw.get("time"):
            errs.append("事件模型需要 time")
        if not model.identities:
            errs.append("事件模型需要至少一個 identities（歸戶用）")
    if model.entity == "dim" and not model.raw.get("target", {}).get("table"):
        errs.append("維度表模型需要 target.table")
    dropped = set(model.raw.get("drop_fields", []))
    for f in model.fields:
        tgt = f.get("target")
        if not tgt:
            errs.append(f"欄位缺 target：{f}")
            continue
        if tgt in sem.forbidden_l4 and model.entity in ("event", "user"):   # 維度表的 name 是商品名，不在此限
            errs.append(f"目標欄位 {tgt} 是 L4 個資，永不入倉")
        if f.get("from") in dropped:
            errs.append(f"欄位 {tgt} 來自 drop_fields 的 {f['from']}")
        if model.entity == "user" and tgt not in sem.user_props:
            errs.append(f"用戶屬性 {tgt} 未在 user_properties.yaml 登記")
        if f.get("transform"):
            try:
                name, args, _ = parse_transform(f["transform"])
                if name not in TRANSFORMS:
                    errs.append(f"未知轉換 {name}（欄位 {tgt}）")
                if name in SECRET_TRANSFORMS and not args:
                    errs.append(f"轉換 {name} 需要密鑰參照（欄位 {tgt}）")
            except ValueError as e:
                errs.append(str(e))
        if f.get("expr"):
            try:
                eval_expr(f["expr"], {})
            except ValueError as e:
                errs.append(str(e))
    for idn in model.identities:
        if idn.get("target") not in IDENTITY_KEYS:
            errs.append(f"身分欄位 {idn.get('target')} 不在 OneID 規則表")
        name, args, _ = parse_transform(idn.get("transform"))
        if name not in SECRET_TRANSFORMS:
            errs.append(f"身分欄位 {idn.get('target')} 必須代碼化（hmac_sha256／fpe／token_v1）")
    for r in model.cleansing:
        if r.get("rule") not in ("required", "enum", "range", "time_window", "pii_scan"):
            errs.append(f"未知清洗規則 {r.get('rule')}")
    return errs


# ---------------------------------------------------------------- 套用
@dataclass
class Outcome:
    ok: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    rejected: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    new_fields: set[str] = field(default_factory=set)

    @property
    def summary(self) -> dict[str, int]:
        return {"in": len(self.ok) + len(self.quarantined) + len(self.rejected), "ok": len(self.ok),
                "quarantined": len(self.quarantined), "rejected": len(self.rejected)}


def _event_for(model: Model, row: dict[str, Any]) -> str | None:
    if model.raw.get("event"):
        return model.raw["event"]
    ez = model.raw.get("eventize")
    if not ez:
        return None
    status = str(row.get(ez["status_field"]))
    for t in ez.get("transitions", []):
        if t.get("to") and str(t["to"]) == status:
            return t["event"]
    return None


def _event_id(model: Model, row: dict[str, Any], event: str) -> str:
    tpl = (model.raw.get("dedupe") or {}).get("event_id", "")
    m = re.fullmatch(r"sha256\((.*)\)", tpl.strip())
    parts = [p.strip() for p in m.group(1).split("|")] if m else ["source", "*"]
    vals = []
    for p in parts:
        if p == "source": vals.append(model.id)
        elif p == "*": vals.append(json.dumps(row, sort_keys=True, default=str))
        elif p == "event": vals.append(event)
        else: vals.append(str(row.get(p, "")))
    return hashlib.sha256("|".join(vals).encode()).hexdigest()[:32]


def quarantine_row(model: Model, row: dict[str, Any], forbidden: set[str] | None = None, reason: str | None = None) -> dict[str, Any]:
    """隔離區要留下來源列供追查，但個資不入庫：
    - drop_fields 與 L4 名單的鍵：任何深度（巢狀物件、陣列）、不分大小寫、忽略 $ 前綴、點分鍵逐段比對；命中就丟掉該頂層欄位（禁止鍵的值不靠樣式判斷）；
    - 其餘值像個資（身分證／手機／email，含巢狀）的欄位也丟掉；
    - 身分來源欄（客戶代碼、帳號）換成與入倉相同的代碼（金鑰未注入就丟）。只記被丟的欄位名（_dropped），不記值；
    - 造成隔離的那一欄（reason 的欄位，例如狀態、枚舉）只在值像代碼（英數字、32 字內）時保留供追查，否則遮掉、記在 _masked——
      那一欄的值本來就不合規格，可能是任意文字（例如姓名）。"""
    names = {str(x) for x in model.raw.get("drop_fields", [])} | {str(x) for x in (forbidden or set())}
    ident = {i.get("from"): i.get("transform") for i in model.raw.get("identities", []) if i.get("from")}
    ref = model.raw.get("entity_ref") or {}
    if ref.get("id_from"):
        ident.setdefault(ref["id_from"], ref.get("transform"))
    out: dict[str, Any] = {}; dropped: list[str] = []
    for k, v in row.items():
        if T._key_hit(k, names) or T.pii_scan_deep(v, str(k), names):
            dropped.append(str(k)); continue
        if k in ident:
            try:
                out[k] = apply_transform(ident[k], v)
            except KeyError:
                dropped.append(str(k))
            continue
        out[k] = v
    bad = _reason_source_field(model, reason)
    if bad and bad in out and not (isinstance(out[bad], (int, float)) or (isinstance(out[bad], str) and _CODE_LIKE.fullmatch(out[bad]))):
        out[bad] = "[非代碼值，已遮罩]"; out["_masked"] = [bad]
    if dropped:
        out["_dropped"] = sorted(dropped)
    return out


_CODE_LIKE = re.compile(r"[A-Za-z0-9_.\-]{0,32}")


def _reason_source_field(model: Model, reason: str | None) -> str | None:
    """隔離原因碼 → 來源欄位名：no_transition 是狀態欄（來源欄），enum／range 是目標欄位，換回它的來源欄。"""
    if not reason or ":" not in reason:
        return None
    code, field = reason.split(":", 1)
    if code == "no_transition":
        return field
    if code in ("enum", "range"):
        return next((f.get("from") for f in model.fields if f.get("target") == field and f.get("from")), None)
    return None


def apply(model: Model, rows: list[dict[str, Any]], *, known_fields: set[str] | None = None,
          now: datetime | None = None, batch_id: str = "") -> Outcome:
    """欄位映射 + 清洗；輸出符合目標表的列。known_fields 給了就偵測來源新欄位（Schema drift）。"""
    out = Outcome()
    now = now or datetime.now(UTC)
    sem_time = model.raw.get("time") or {}
    for row in rows:
        if known_fields is not None:
            out.new_fields |= set(row) - known_fields
        # 個資掃描（整列，含 drop_fields 之外的任何欄位）
        pii_rule = next((r for r in model.cleansing if r.get("rule") == "pii_scan"), None)
        if pii_rule:
            scan_row = {k: v for k, v in row.items() if k not in set(model.raw.get("drop_fields", []))}
            hits = T.pii_scan(scan_row)
            if hits:
                (out.rejected if pii_rule.get("on_fail", "reject_and_alert").startswith("reject") else out.quarantined).append((row, f"pii:{','.join(hits)}"))
                continue
        rec: dict[str, Any] = {}
        try:
            if model.entity == "event":
                ev = _event_for(model, row)
                if not ev:
                    out.quarantined.append((row, f"no_transition:{(model.raw.get('eventize') or {}).get('status_field')}"))   # 原因只記欄位名、不記原始值（隔離原因也不能外洩個資）
                    continue
                rec["event"] = ev
                ms = T.to_ms_utc(row.get(sem_time.get("field")), sem_time.get("tz", "Asia/Taipei"))
                if ms is None:
                    out.quarantined.append((row, f"bad_time:{sem_time.get('field')}"))
                    continue
                rec["time"] = ms
                ids = {}
                for idn in model.identities:
                    val = apply_transform(idn.get("transform"), row.get(idn["from"]))
                    if val is not None:
                        ids[idn["target"]] = val
                if "$identity_login_id" not in ids:
                    out.quarantined.append((row, "no_login_identity"))
                    continue
                rec["identities"] = ids
                rec["one_id"] = ids["$identity_login_id"]
                rec["distinct_id"] = ids["$identity_login_id"]
                er = model.raw.get("entity_ref")
                if er:
                    rec["entity_type"] = er["type"]
                    rec["entity_id"] = apply_transform(er.get("transform"), row.get(er["id_from"]))
                rec["event_id"] = _event_id(model, row, ev)
            props: dict[str, Any] = {}
            for f in model.fields:
                if "expr" in f:
                    val = eval_expr(f["expr"], row)
                else:
                    val = apply_transform(f.get("transform"), row.get(f["from"]))
                if not when_ok(f.get("when"), rec):
                    continue
                props[f["target"]] = val
            rec["props"] = props
        except (KeyError, ValueError) as e:
            out.quarantined.append((row, f"transform_error:{type(e).__name__}"))   # 例外訊息可能帶原始值，只記類型
            continue
        # 清洗規則
        reason = None
        for r in model.cleansing:
            rule = r["rule"]
            if rule == "required":
                missing = [f for f in r["fields"] if props.get(f) in (None, "") and rec.get(f) in (None, "")]
                if missing: reason = f"required:{','.join(missing)}"
            elif rule == "enum":   # 枚舉即必填：對不上（含 map 後為空）一律隔離
                v = props.get(r["field"], rec.get(r["field"]))
                if v not in r["values"]: reason = f"enum:{r['field']}"
            elif rule == "range":
                v = props.get(r["field"], rec.get(r["field"]))
                if v is not None and (("min" in r and v < r["min"]) or ("max" in r and v > r["max"])): reason = f"range:{r['field']}"
            elif rule == "time_window" and model.entity == "event":
                age_days = (now.timestamp() * 1000 - rec["time"]) / 86_400_000
                if age_days > r.get("days", 7) or age_days < -1: reason = f"time_window:{age_days:.1f}d"
            if reason:
                break
        if reason:
            out.quarantined.append((row, reason))
            continue
        rec["batch_id"] = batch_id
        out.ok.append(rec)
    return out


def preview(model: Model, rows: list[dict[str, Any]], limit: int = 1000) -> dict[str, Any]:
    o = apply(model, rows[:limit], known_fields=set(model.raw.get("source_fields", [])) or None)
    return {"summary": o.summary, "sample_ok": o.ok[:5],
            "quarantined": [{"reason": r, "row": quarantine_row(model, row, Semantic.load().forbidden_l4, r)} for row, r in o.quarantined[:20]],   # 預覽也不回傳個資
            "rejected": [{"reason": r} for _, r in o.rejected[:20]],   # 拒絕列含個資，不回傳內容
            "new_fields": sorted(o.new_fields)}


# ---------------------------------------------------------------- ClickHouse 列與 SQL
def ms_to_dt(ms: int) -> str:
    """毫秒 → 'YYYY-MM-DD HH:MM:SS.mmm'（UTC）。JSONEachRow 寫 DateTime64 不能給整數毫秒：會被當成秒而飽和到 9999-12-31（e2e 實測）。"""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S.") + f"{ms % 1000:03d}"


def to_ch_row(model: Model, rec: dict[str, Any], project: str = "altatech_dmp", ingest_ms: int | None = None) -> dict[str, Any]:
    """apply() 的輸出 → events／users／dim 表的 JSONEachRow 列（時間欄位一律字串，見 ms_to_dt）。"""
    ingest_ms = ingest_ms or int(datetime.now(UTC).timestamp() * 1000)
    if model.entity == "event":
        props = dict(rec["props"])
        row = {"project": project, "time": ms_to_dt(rec["time"]), "event": rec["event"], "source": model.id, "one_id": rec["one_id"],
               "entity_type": rec.get("entity_type", ""), "entity_id": rec.get("entity_id") or "", "distinct_id": rec["distinct_id"],
               "session_id": "", "event_id": rec["event_id"], "platform": props.pop("platform", "") or "", "app_id": "",
               "symbol": props.pop("symbol", "") or "", "side": props.pop("side", "") or "", "channel": props.pop("channel", "") or "",
               "amount": props.pop("amount", None), "utm_source": "", "utm_campaign": "", "props": props,
               "ingest_time": ms_to_dt(ingest_ms), "schema_version": 1, "late": 0, "batch_id": rec.get("batch_id", "")}
        row["props"]["$identities"] = rec.get("identities", {})
        return row
    if model.entity == "user":   # 寬表：已登記屬性直接是欄位，未登記的進 props_extra
        return {"one_id": rec["one_id"], **rec["props"], "props_extra": {}, "first_seen": ms_to_dt(ingest_ms), "updated_at": ms_to_dt(ingest_ms)}
    key = model.raw["target"]["key"]
    props = dict(rec["props"])
    return {key: props.pop(key), "props": props, "updated_at": ms_to_dt(ingest_ms)}


def compile_sql(model: Model, raw_table: str, project: str = "altatech_dmp", batch_id: str = "{batch_id}") -> str:
    """raw 表（欄位 = 來源欄位，已在暫存階段完成密鑰轉換）→ 目標表的 INSERT … SELECT。只支援事件模型與維度表。"""
    if model.entity == "event":
        ez = model.raw.get("eventize")
        if ez:
            pairs = [(str(t["to"]), t["event"]) for t in ez["transitions"] if t.get("to")]
            ev = "transform(toString({s}), [{k}], [{v}], '')".format(
                s=ez["status_field"], k=", ".join(f"'{k}'" for k, _ in pairs), v=", ".join(f"'{v}'" for _, v in pairs))
        else:
            ev = f"'{model.raw['event']}'"
        tm = model.raw["time"]
        login = next(i for i in model.identities if i["target"] == "$identity_login_id")
        props = []
        for f in model.fields:
            if f["target"] in EVENT_COLUMNS:
                continue
            e = expr_sql(f["expr"]) if "expr" in f else transform_sql(f.get("transform"), f["from"])
            props.append(f"'{f['target']}', ifNull(toString({e}), '')")
        cols = {c: "''" for c in ("entity_type", "entity_id", "session_id", "platform", "app_id", "symbol", "side", "channel", "utm_source", "utm_campaign")}
        cols["amount"] = "NULL"
        for f in model.fields:
            if f["target"] in cols:
                e = expr_sql(f["expr"]) if "expr" in f else transform_sql(f.get("transform"), f["from"])
                cols[f["target"]] = f"toFloat64OrNull(toString({e}))" if f["target"] == "amount" else f"toString({e})"
        er = model.raw.get("entity_ref")
        if er:
            cols["entity_type"] = f"'{er['type']}'"; cols["entity_id"] = f"toString({er['id_from']})"
        dedupe = (model.raw.get("dedupe") or {}).get("event_id", "sha256(source|*)")
        parts = [p.strip() for p in re.fullmatch(r"sha256\((.*)\)", dedupe).group(1).split("|")]
        idp = ", '|', ".join("'" + model.id + "'" if p == "source" else ("toString(" + p + ")" if p not in ("*", "event") else ev) for p in parts)
        where = []
        src_of = {f["target"]: f["from"] for f in model.fields if f.get("from")}
        for r in model.cleansing:
            if r["rule"] == "required":
                where += [f"{src_of[f]} IS NOT NULL AND toString({src_of[f]}) != ''" for f in r["fields"] if f in src_of]
        sql = f"""INSERT INTO {model.target_table} (project, time, event, source, one_id, entity_type, entity_id, distinct_id, session_id, event_id, platform, app_id, symbol, side, channel, amount, utm_source, utm_campaign, props, ingest_time, schema_version, late, batch_id)
SELECT
  '{project}' AS project,
  toDateTime64({tm['field']}, 3, '{tm.get('tz', 'Asia/Taipei')}') AS time,
  {ev} AS event,
  '{model.id}' AS source,
  toString({login['from']}) AS one_id,
  {cols['entity_type']} AS entity_type, {cols['entity_id']} AS entity_id,
  toString({login['from']}) AS distinct_id, {cols['session_id']} AS session_id,
  substring(lower(hex(SHA256(concat({idp})))), 1, 32) AS event_id,   -- 與 Python 端 _event_id 同一公式
  {cols['platform']} AS platform, {cols['app_id']} AS app_id, {cols['symbol']} AS symbol, {cols['side']} AS side, {cols['channel']} AS channel,
  {cols['amount']} AS amount, {cols['utm_source']} AS utm_source, {cols['utm_campaign']} AS utm_campaign,
  {("CAST(toJSONString(map(" + ', '.join(props) + ")), 'JSON')") if props else "CAST('{}', 'JSON')"} AS props,
  now64(3) AS ingest_time, 1 AS schema_version, 0 AS late, '{batch_id}' AS batch_id
FROM {raw_table}
WHERE {ev} != ''{(' AND ' + ' AND '.join(where)) if where else ''}"""
        return sql
    if model.entity == "dim":
        key = model.raw["target"]["key"]; table = model.raw["target"]["table"]
        kf = next(f for f in model.fields if f["target"] == key)
        props = [f"'{f['target']}', ifNull(toString({transform_sql(f.get('transform'), f['from'])}), '')" for f in model.fields if f["target"] != key]
        return f"INSERT INTO {table}\nSELECT toString({transform_sql(kf.get('transform'), kf['from'])}) AS {key}, CAST(toJSONString(map({', '.join(props)})), 'JSON') AS props, now64(3) AS updated_at\nFROM {raw_table}"
    raise NotImplementedError("用戶模型以 Python 路徑寫入（upsert 需要先查 one_id）")


# ---------------------------------------------------------------- 自測
if __name__ == "__main__":
    set_key("k_idm", b"test-key-idm"); set_key("k_acct", b"test-key-acct")
    m = load(ROOT / "integration" / "models.example.yaml")
    errs = validate(m)
    assert errs == [], errs
    rows = [
        {"ORD_NO": "A1", "STATUS": "NEW", "ORD_TIME": "2026-10-15 09:01:00", "CUST_ID": "C001", "ACCT_NO": "9801234567", "STK_CODE": " 2330 ", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "1000", "QTY": "2000", "CHANNEL": "A", "FEE": "", "TAX": ""},
        {"ORD_NO": "A1", "STATUS": "FILLED", "ORD_TIME": "2026-10-15 09:02:30", "CUST_ID": "C001", "ACCT_NO": "9801234567", "STK_CODE": "2330", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "1000", "QTY": "2000", "FILL_QTY": "2000", "CHANNEL": "A", "FEE": "2850", "TAX": "0"},
        {"ORD_NO": "A2", "STATUS": "REJECTED", "ORD_TIME": "2026-10-15 09:03:00", "CUST_ID": "C002", "ACCT_NO": "9809999999", "STK_CODE": "2317", "MKT": "X", "BS_FLAG": "S", "ORD_TYPE": "MKT", "PRICE": "100", "QTY": "1000", "CHANNEL": "W", "RJ_CODE": "E12"},
        {"ORD_NO": "A3", "STATUS": "NEW", "ORD_TIME": "2026-10-15 09:04:00", "CUST_ID": "C003", "ACCT_NO": "9800000001", "STK_CODE": "2454", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "", "QTY": "", "CHANNEL": "A"},
        {"ORD_NO": "A4", "STATUS": "WEIRD", "ORD_TIME": "2026-10-15 09:05:00", "CUST_ID": "C004", "ACCT_NO": "9800000002", "STK_CODE": "2454", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "1", "QTY": "1", "CHANNEL": "A"},
        {"ORD_NO": "A5", "STATUS": "NEW", "ORD_TIME": "2026-10-15 09:06:00", "CUST_ID": "C005", "ACCT_NO": "9800000003", "STK_CODE": "2454", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "1", "QTY": "1", "CHANNEL": "A", "MEMO": "請回電 0912345678"},
        {"ORD_NO": "A6", "STATUS": "NEW", "ORD_TIME": "2026-01-01 09:06:00", "CUST_ID": "C006", "ACCT_NO": "9800000004", "STK_CODE": "2454", "MKT": "T", "BS_FLAG": "B", "ORD_TYPE": "LMT", "PRICE": "1", "QTY": "1", "CHANNEL": "A"},
    ]
    o = apply(m, rows, now=datetime(2026, 10, 16, tzinfo=UTC), known_fields=set(rows[0]))
    assert o.summary == {"in": 7, "ok": 2, "quarantined": 4, "rejected": 1}, o.summary
    reasons = sorted(r for _, r in o.quarantined)
    assert reasons[:3] == ["enum:market", "no_transition:STATUS", "required:qty"] and reasons[3].startswith("time_window:"), reasons
    assert o.rejected[0][1] == "pii:MEMO:mobile" and o.new_fields == {"FILL_QTY", "RJ_CODE", "MEMO"}
    a1 = o.ok[0]
    assert a1["event"] == "bond_order" and a1["one_id"].startswith("v1_") and len(a1["one_id"]) == 23
    assert a1["props"]["symbol"] == "2330" and a1["props"]["market"] == "TSE" and a1["props"]["amount"] == 2_000_000.0 and "filled_qty" not in a1["props"]
    assert o.ok[1]["event"] == "bond_deal" and o.ok[1]["props"]["filled_qty"] == "2000"
    assert a1["entity_id"] != "9801234567" and len(a1["entity_id"]) == 10 and a1["entity_id"].isdigit()   # FPE 保留格式
    ch = to_ch_row(m, a1)
    assert ch["symbol"] == "2330" and ch["amount"] == 2_000_000.0 and "symbol" not in ch["props"] and ch["time"] == "2026-10-15 01:01:00.000" and ch["props"]["$identities"]["$identity_login_id"] == a1["one_id"]
    sql = compile_sql(m, "dmp.raw_orders")
    assert "transform(toString(STATUS)" in sql and "windowFunnel" not in sql and "toDateTime64(ORD_TIME, 3, 'Asia/Taipei')" in sql
    assert transform_sql("map({T: TSE, O: OTC})", "MKT") == "transform(MKT, ['T', 'O'], ['TSE', 'OTC'], NULL)"
    bad = Model({**m.raw, "fields": m.fields + [{"target": "mobile", "from": "PHONE"}], "identities": [{"target": "$identity_login_id", "from": "CUST_ID"}]})
    e = validate(bad)
    assert any("L4" in x for x in e) and any("drop_fields" in x for x in e) and any("代碼化" in x for x in e), e
    # 隔離列不留個資：drop_fields／L4 名單／值像個資的欄位丟掉，客戶代碼與帳號換成入倉同款代碼
    qr = quarantine_row(m, {**o.quarantined[0][0], "CUST_NAME": "王小明", "ID_NO": "A123456789", "PHONE": "0912345678", "MEMO": "請回電 0912345678", "Mobile": "x"}, {"mobile"})
    qs = json.dumps(qr, ensure_ascii=False)
    for leak in ("王小明", "A123456789", "0912345678", o.quarantined[0][0]["CUST_ID"], o.quarantined[0][0]["ACCT_NO"]):
        assert leak not in qs, (leak, qs)
    assert {"CUST_NAME", "ID_NO", "PHONE", "MEMO", "Mobile"} <= set(qr["_dropped"]) and qr["CUST_ID"].startswith("v1_") and qr["STK_CODE"] and qr["ORD_NO"], qr
    # Codex #52 複審：巢狀／$ 前綴／點分的禁止鍵（值不像個資樣式）也要丟；隔離原因不帶原始值
    fl4 = Semantic.load().forbidden_l4
    base = {**rows[4], "CUST_NAME": "王小明", "nested": {"CUST_NAME": "王小明", "ok": 1}, "$name": "王小明", "profile.name": "王小明", "arr": [{"x.Cust_Name": "王小明"}], "keep": "TSE"}
    o2 = apply(m, [base, {**base, "STATUS": "王小明"}], now=datetime(2026, 10, 16, tzinfo=UTC))
    assert o2.summary["quarantined"] == 2 and [r for _, r in o2.quarantined] == ["no_transition:STATUS", "no_transition:STATUS"], o2.quarantined
    q2 = quarantine_row(m, o2.quarantined[0][0], fl4); q2s = json.dumps(q2, ensure_ascii=False)
    assert "王小明" not in q2s and {"CUST_NAME", "nested", "$name", "profile.name", "arr"} <= set(q2["_dropped"]) and q2["keep"] == "TSE", q2
    assert all("王小明" not in r for _, r in o2.quarantined)                                                # 原因碼只有欄位名
    q3 = quarantine_row(m, o2.quarantined[1][0], fl4, o2.quarantined[1][1]); assert "王小明" not in json.dumps(q3, ensure_ascii=False) and q3["_masked"] == ["STATUS"], q3   # 造成隔離的欄位值不像代碼 → 遮罩
    assert quarantine_row(m, o2.quarantined[0][0], fl4, o2.quarantined[0][1])["STATUS"] == "WEIRD"                                          # 像代碼的值保留供追查
    qe = quarantine_row(m, {**rows[2], "MKT": "王小明"}, fl4, "enum:market"); assert qe["MKT"] == "[非代碼值，已遮罩]", qe                       # enum 目標欄換回來源欄
    pv = json.dumps(preview(m, [base]), ensure_ascii=False); assert "王小明" not in pv and "9800000002" not in pv, pv   # 預覽也不回傳個資
    print("modeling ok", o.summary)
