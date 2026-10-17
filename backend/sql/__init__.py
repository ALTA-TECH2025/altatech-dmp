"""自定義 SQL 沙盒（docs/21 §3）：唯讀解析、表目錄、脫敏預校驗、行級限制、包裝執行。"""
from .sandbox import SandboxError, allowed_tables, compile_sql, schema_catalog, table_catalog  # noqa: F401
