"""SQL 沙盒的純函式：
- schema_catalog()：從 backend/clickhouse/schema.sql 解析表與欄位（不連 ClickHouse 也能列目錄）。
- allowed_tables(roles)：依角色給可查詢的表（events／users／user_tags／segment_members／dim_*／mid_*；id_map 只給 it_admin／admin）。
- compile_sql(sql, params, decision, cat, roles)：sqlglot（ClickHouse 方言）解析 → 只允許單一 SELECT／WITH／UNION；拒絕 DDL／DML、SETTINGS、INTO OUTFILE、FORMAT、表函數（file／url／s3／remote…）；
  只能引用目錄內的表；引用到脫敏欄位（decision.field_policies 非 plain、L4 禁用欄位）直接錯；有行級範圍（decision.row_filters）的身分不開放；
  動態參數 {{name}} 只接受字串／數字／日期；最後包成 SELECT * FROM (<sql>) LIMIT n 並帶 readonly=1、max_execution_time=60。
自測：python3 -m backend.sql
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from ..semantic.compiler import Catalog
from ..semantic.compiler.events import Compiled
from ..semantic.compiler.events import q as sql_quote
from ..semantic.compiler.users import props_key_blocked

ROOT = Path(__file__).resolve().parents[1]
BASE_TABLES = ["events", "users", "user_tags", "segment_members"]
ADMIN_ONLY_TABLES = {"id_map", "id_edges", "oneid_merge_log", "user_prop_log", "quarantine"}
SANDBOX_ROLES = {"admin", "it_admin"}                                                     # 其餘看 rbac.yaml 角色的 sql_sandbox 旗標
BANNED_FUNCS = {"file", "url", "s3", "s3cluster", "remote", "remotesecure", "input", "hdfs", "mysql", "postgresql", "jdbc", "odbc", "executable", "cluster", "clusterallreplicas", "azureblobstorage", "gcs", "sqlite", "merge", "view", "format"}
MAX_LIMIT = 1000
MID_TABLES: list[str] = []                                                                 # 已登記的中間表名（router 啟動與異動時更新）
SETTINGS = {"readonly": 1, "max_execution_time": 60, "max_result_rows": MAX_LIMIT, "result_overflow_mode": "break", "max_memory_usage": 4_000_000_000}
FUNCTION_HINTS = ["count()", "uniq(one_id_uint)", "sum(amount)", "avg(x)", "quantile(0.9)(x)", "toDate(time, 'Asia/Taipei')", "toStartOfWeek(d)", "dictGet('dmp_dict.dim_instrument', 'industry', symbol)", "windowFunnel(86400)(time, event='a', event='b')", "arrayJoin(arr)", "multiIf(c1, v1, c2, v2, v)"]
EXAMPLES = [
    {"cn": "每日成交筆數與用戶（近 14 天）", "sql": "SELECT event_date, count() AS deals, uniq(one_id_uint) AS users\nFROM dmp.events\nWHERE event = 'bond_deal' AND event_date >= today() - 14\nGROUP BY event_date ORDER BY event_date"},
    {"cn": "各平台看盤 → 成交 轉換（7 天漏斗）", "sql": "SELECT platform, countIf(lvl >= 1) AS viewed, countIf(lvl >= 2) AS dealt\nFROM (\n  SELECT one_id_uint, any(platform) AS platform, windowFunnel(604800)(toDateTime(time), event = 'hq_stockdetail_view', event = 'bond_deal') AS lvl\n  FROM dmp.events WHERE event_date >= today() - 30 GROUP BY one_id_uint\n)\nGROUP BY platform ORDER BY viewed DESC"},
    {"cn": "價值分級 × 生命週期 人數", "sql": "SELECT tier, lifecycle, count() AS users\nFROM dmp.users FINAL\nGROUP BY tier, lifecycle ORDER BY users DESC"},
]


class SandboxError(ValueError):
    pass


def schema_catalog() -> dict[str, list[dict[str, str]]]:
    """schema.sql → {table: [{name, type}]}（跳過 MATERIALIZED VIEW）。"""
    text = (ROOT / "clickhouse" / "schema.sql").read_text(encoding="utf-8")
    out: dict[str, list[dict[str, str]]] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS dmp\.(\w+)\s*\((.*?)\n\)\s*\nENGINE", text, re.S):
        cols = []
        for line in m.group(2).splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            cm = re.match(r"^`?(\w+)`?\s+([A-Za-z0-9_()', ]+?)(?:\s+(?:DEFAULT|MATERIALIZED|ALIAS|CODEC|TTL)\b.*)?$", line)
            if cm and cm.group(1).upper() not in ("INDEX", "PROJECTION", "CONSTRAINT"):
                cols.append({"name": cm.group(1), "type": cm.group(2).strip()})
        out[m.group(1)] = cols
    return out


def sandbox_allowed(roles: list[str], rbac_roles: dict[str, Any]) -> bool:
    return any(r in SANDBOX_ROLES or (rbac_roles.get(r) or {}).get("sql_sandbox") for r in roles)


def allowed_tables(roles: list[str], extra: list[str] | None = None) -> set[str]:
    base = set(BASE_TABLES) | {t for t in schema_catalog() if t.startswith("dim_")} | set(extra or [])
    if any(r in SANDBOX_ROLES for r in roles):
        base |= ADMIN_ONLY_TABLES & set(schema_catalog())
    return base


def blocked_columns(roles: list[str], cat: Catalog) -> set[str]:
    """脫敏欄位：依角色（不看揭露：揭露只對單一客戶細查有效，不能拿來跑整表）策略非 plain 的欄位 + L4 禁用欄位；one_id_uint（雜湊）放行。"""
    out = set(cat.forbidden_l4) | {"customer_code", "cust_code", "account_no"}
    for f in cat.masking.values():
        plain = f.get("plain_roles", [])
        if not ("*" in plain or any(r in plain for r in roles)):
            out.add(f["name"])
    return out - {"one_id_uint"}


def table_catalog(roles: list[str], decision: Any, cat: Catalog, mid_tables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    blocked = blocked_columns(roles, cat)
    levels = {f["name"]: f.get("level", "L1") for f in cat.masking.values()}
    sc = schema_catalog(); allowed = allowed_tables(roles, [m["name"] for m in (mid_tables or [])])
    tables = []
    for t in sorted(sc):
        if t not in allowed: continue
        tables.append({"name": f"dmp.{t}", "columns": [{**c, "level": levels.get(c["name"], "L4" if c["name"] in cat.forbidden_l4 else "L1"), "allowed": c["name"] not in blocked} for c in sc[t]]})
    for m in mid_tables or []:
        tables.append({"name": f"dmp.{m['name']}", "columns": [{"name": c, "type": "", "level": "L1", "allowed": True} for c in (m.get("columns") or [])], "mid_table": True, "note": m.get("note", "")})
    return {"tables": tables, "functions": FUNCTION_HINTS, "examples": EXAMPLES, "limits": {"preview_rows": MAX_LIMIT, "timeout_s": SETTINGS["max_execution_time"], "concurrent_per_user": 5}}


def substitute_params(sql: str, params: dict[str, Any] | None) -> str:
    def rep(m: re.Match[str]) -> str:
        k = m.group(1)
        if params is None or k not in params:
            raise SandboxError(f"動態參數 {{{{{k}}}}} 沒有給值")
        v = params[k]
        if isinstance(v, bool) or v is None:
            raise SandboxError(f"參數 {k} 只能是字串、數字或日期")
        if isinstance(v, int | float):
            return str(v)
        s = str(v).strip()
        if len(s) > 200 or ";" in s:
            raise SandboxError(f"參數 {k} 不合法")
        if re.fullmatch(r"-?\d+(\.\d+)?", s):                      # 表單送來的數字是字串：綁成數字字面值（today() - {{days}} 才合法）
            return s
        return sql_quote(s)
    return re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", rep, sql)


def compile_sql(sql: str, params: dict[str, Any] | None, decision: Any, cat: Catalog, roles: list[str], limit: int = MAX_LIMIT, mid_tables: list[str] | None = None) -> Compiled:
    if decision.row_filters:
        raise SandboxError("有行級資料範圍（分公司／營業員／回溯天數）的身分不開放自定義 SQL，請用分析頁")
    text = substitute_params(sql.strip().rstrip(";"), params)
    if not text:
        raise SandboxError("SQL 不可為空")
    if len(text) > 20000:
        raise SandboxError("SQL 過長（≤ 20000 字）")
    if re.search(r"\bINTO\s+OUTFILE\b", text, re.I) or re.search(r"\bFORMAT\s+\w+\s*$", text, re.I):
        raise SandboxError("不允許 INTO OUTFILE／FORMAT 子句")
    try:
        stmts = sqlglot.parse(text, read="clickhouse")
    except sqlglot.errors.ParseError as e:
        raise SandboxError(f"SQL 解析失敗：{str(e)[:200]}") from None
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise SandboxError("只允許一條語句")
    root = stmts[0]
    if not isinstance(root, exp.Select | exp.Union):
        raise SandboxError(f"只允許 SELECT／WITH 查詢（收到 {type(root).__name__}）")
    for node in root.walk():
        if isinstance(node, exp.Insert | exp.Update | exp.Delete | exp.Create | exp.Drop | exp.Alter | exp.Command | exp.Set):
            raise SandboxError("只允許唯讀查詢：不可 DDL／DML")
        if isinstance(node, exp.Select | exp.Union):
            for k in ("settings", "format", "into"):
                if node.args.get(k):
                    raise SandboxError(f"不允許 {k.upper()} 子句")
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Func | exp.Anonymous):
            raise SandboxError(f"不允許表函數／外部來源：{node.this.sql()[:60]}")
        if isinstance(node, exp.Anonymous) and str(node.name).lower() in BANNED_FUNCS:
            raise SandboxError(f"不允許表函數／外部來源：{node.name}")
    allowed = allowed_tables(roles, mid_tables)
    tables: list[str] = []
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    for t in root.find_all(exp.Table):
        db = (t.db or "").lower(); name = (t.name or "").lower()
        if not name or name in cte_names:
            continue
        if db not in ("", "dmp"):
            raise SandboxError(f"只能查詢 dmp 資料庫的表：{t.sql()}")
        if name not in allowed:
            raise SandboxError(f"表 {name} 不在你的數據表權限內（可用：{', '.join(sorted(allowed))}）")
        tables.append(f"dmp.{name}")
    if not tables:
        raise SandboxError("查詢沒有引用任何數據表")
    blocked = blocked_columns(roles, cat)
    hit = sorted({c.name for c in root.find_all(exp.Column) if c.name in blocked})
    hit += sorted({str(i.this) for i in root.find_all(exp.Identifier) if str(i.this) in blocked} - set(hit))
    if hit:
        raise SandboxError(f"SQL 涉及脫敏欄位，不可直接查詢：{', '.join(hit)}（可用 one_id_uint 做去重／關聯；明細請走用戶列表與揭露申請）")
    # props 與時間軸／資料輸出同一套清理政策：整包（含 toString(props)、JSONExtract(props, …)）不給查，因為巢狀 $identities 會跟著出來；
    # props.<鍵> 逐鍵看，身分識別／L4／hide 的鍵擋下。
    fp = dict(getattr(decision, "field_policies", None) or {})
    for c in root.find_all(exp.Column):
        parts = [p.name for p in c.parts]
        if "props" not in parts: continue
        path = parts[parts.index("props") + 1:]
        if not path:
            raise SandboxError("props 整包不可直接查詢（含巢狀身分識別）：請指定 props.<鍵>，例如 props.market")
        if any(props_key_blocked(p, cat, fp) for p in path):
            raise SandboxError(f"props.{'.'.join(path)} 是身分識別／L4／隱藏欄位，不可查詢")
    limit = max(1, min(int(limit or MAX_LIMIT), MAX_LIMIT))
    inner = root.sql(dialect="clickhouse")
    wrapped = f"SELECT * FROM ({inner}) AS _q LIMIT {limit}"
    return Compiled(sql=wrapped, layer="L1", columns=[], settings=dict(SETTINGS), lineage=sorted(set(tables)), warnings=[f"自定義 SQL：結果只預覽前 {limit} 列"])


def selftest() -> None:
    from ..security.policy import RBAC, Subject, authorize
    cat = Catalog()
    ana = authorize(Subject("a", ["data_analyst"]), "use", "events"); mkt = authorize(Subject("m", ["marketing"]), "use", "events")
    assert sandbox_allowed(["data_analyst"], RBAC["roles"]) and not sandbox_allowed(["marketing"], RBAC["roles"]) and sandbox_allowed(["it_admin"], RBAC["roles"])
    sc = schema_catalog(); assert "events" in sc and any(c["name"] == "amount" for c in sc["events"]) and "user_tags" in sc and "events_daily_agg" not in sc
    c = compile_sql("SELECT event_date, count() AS n FROM dmp.events WHERE event='bond_deal' GROUP BY event_date", None, ana, cat, ["data_analyst"])
    assert c.sql.startswith("SELECT * FROM (") and c.sql.endswith("LIMIT 1000") and c.lineage == ["dmp.events"] and c.settings["readonly"] == 1
    c2 = compile_sql("WITH t AS (SELECT one_id_uint, count() AS n FROM events GROUP BY one_id_uint) SELECT quantile(0.9)(n) FROM t", None, ana, cat, ["data_analyst"], limit=10)
    assert c2.sql.endswith("LIMIT 10") and c2.lineage == ["dmp.events"]
    for bad, why in [("INSERT INTO dmp.events SELECT 1", "SELECT"), ("DROP TABLE dmp.events", "SELECT"), ("SELECT 1; SELECT 2", "一條"), ("SELECT * FROM dmp.events SETTINGS max_threads=1", "SETTINGS"),
                     ("SELECT * FROM dmp.id_map", "權限"), ("SELECT * FROM other.events", "dmp"), ("SELECT * FROM file('x.csv')", "表函數"), ("SELECT one_id FROM dmp.events", "脫敏"),
                     ("SELECT amount FROM dmp.events", "脫敏"), ("SELECT 1", "數據表"), ("SELECT * FROM dmp.events INTO OUTFILE 'x'", "OUTFILE"),
                     ("SELECT props FROM dmp.events", "整包"), ("SELECT toString(e.props) FROM dmp.events e", "整包"), ("SELECT JSONExtractString(props, '$identity_login_id') FROM dmp.events", "整包"),
                     ("SELECT props.`$identities` FROM dmp.events", "身分識別"), ("SELECT e.props.`$identities`.`$identity_login_id` FROM dmp.events e", "身分識別"),
                     ("SELECT props.`$identity_login_id` FROM dmp.events", "身分識別"), ("SELECT props.email FROM dmp.events", "不可")]:
        try:
            compile_sql(bad, None, mkt if "amount" in bad else ana, cat, ["marketing"] if "amount" in bad else ["data_analyst"]); raise AssertionError(bad)
        except SandboxError as e:
            assert why in str(e) or why.lower() in str(e).lower(), (bad, str(e))
    cp = compile_sql("SELECT props.market AS m, count() AS n FROM dmp.events GROUP BY m", None, ana, cat, ["data_analyst"]); assert "props.market" in cp.sql, cp.sql   # 一般鍵照查
    assert "amount" not in blocked_columns(["data_analyst"], cat) and "amount" in blocked_columns(["marketing"], cat) and "one_id" in blocked_columns(["data_analyst"], cat) and "one_id" not in blocked_columns(["admin"], cat)
    c3 = compile_sql("SELECT count() FROM dmp.events WHERE platform = {{p}} AND event_date >= {{d}}", {"p": "iOS", "d": "2026-10-01"}, ana, cat, ["data_analyst"])
    assert "'iOS'" in c3.sql and "'2026-10-01'" in c3.sql
    c3b = compile_sql("SELECT count() FROM dmp.events WHERE event_date >= today() - {{days}} AND symbol = {{sym}}", {"days": "14", "sym": "TXF"}, ana, cat, ["data_analyst"])
    assert "today() - 14" in c3b.sql and "'TXF'" in c3b.sql, c3b.sql
    try:
        compile_sql("SELECT count() FROM dmp.events WHERE platform = {{p}}", {}, ana, cat, ["data_analyst"]); raise AssertionError
    except SandboxError as e:
        assert "沒有給值" in str(e)
    branch = authorize(Subject("b", ["data_analyst"], branches=["B01"]), "use", "events"); branch.row_filters.append("branch_id IN ('B01')")
    try:
        compile_sql("SELECT count() FROM dmp.events", None, branch, cat, ["data_analyst"]); raise AssertionError
    except SandboxError as e:
        assert "行級" in str(e)
    tc = table_catalog(["data_analyst"], ana, cat, [{"name": "mid_vip", "columns": ["one_id_uint", "aum_bucket"]}])
    names = [t["name"] for t in tc["tables"]]; assert "dmp.events" in names and "dmp.id_map" not in names and "dmp.mid_vip" in names and "dmp.dim_instrument" in names
    ev = next(t for t in tc["tables"] if t["name"] == "dmp.events"); assert next(c for c in ev["columns"] if c["name"] == "one_id")["allowed"] is False and next(c for c in ev["columns"] if c["name"] == "amount")["allowed"] is True
    assert "dmp.id_map" in [t["name"] for t in table_catalog(["it_admin"], authorize(Subject("i", ["it_admin"]), "use", "events"), cat)["tables"]]
    print("sql sandbox self-test ok: parse/readonly/tables/masked columns/props policy/params/row-level/catalog")


if __name__ == "__main__":
    selftest()
