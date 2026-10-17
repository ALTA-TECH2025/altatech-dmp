"""單機環境種子資料：python3 -m backend.api.seed

把查詢編譯器的黃金資料集（合成、無真實客戶資料；2026-09-20 … 2026-10-16）載入 ClickHouse、建立五種角色的本機帳號、建一個範例儀表板。
環境變數同 backend.api.main：CLICKHOUSE_URL／CLICKHOUSE_PASSWORD、DMP_STORE_PATH、DMP_LOCAL_ACCOUNTS、DMP_LOCAL_ALLOWED_ROLES。
  DMP_SEED_PASSWORD   五個帳號共用的初始密碼（≥14 字；之後用 python3 -m backend.auth.local reset-password 改）
  DMP_SEED_USERS      要建立的帳號（預設 admin,marketing,analyst,ops,it）；已存在的略過
選項：--no-data 不載資料、--no-accounts 不建帳號、--no-dashboard 不建儀表板、--no-migration 不建遷移並行期樣本（project=migration_demo，不影響分析資料）
"""
from __future__ import annotations

import os
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..auth.local import LocalAccounts, LocalError, totp_uri
from ..integration.runner import ClickHouseHTTP
from ..semantic.compiler.golden import PROJECT, T_ALL, T, build, ch_rows
from .store import MemoryStore

ACCOUNTS = {"admin": (["admin"], "系統管理員"), "marketing": (["marketing"], "行銷"), "analyst": (["data_analyst"], "數據分析"), "ops": (["digital_ops"], "數位經營"), "it": (["it_admin"], "資訊管理")}
PIN = {"pinned": True}      # 黃金資料日期固定 → 卡片釘住區間，不跟儀表板的「最近 N 天」走

SAMPLE_CARDS: list[dict[str, Any]] = [
    {"title": "成交筆數 × 平台（日）", "kind": "events", "chart": "line", "width": 2, "query": {"project": PROJECT, "metrics": [{"event": "bond_deal", "agg": "total", "alias": "deals"}], "group_by": ["platform"], "time": {**T, **PIN}}},
    {"title": "委託／成交用戶數（日）", "kind": "events", "chart": "line", "query": {"project": PROJECT, "metrics": [{"event": "bond_order", "agg": "users", "alias": "order_users"}, {"event": "bond_deal", "agg": "users", "alias": "deal_users"}], "time": {**T, **PIN}}},
    {"title": "本期成交筆數／用戶／金額", "kind": "events", "chart": "stat", "query": {"project": PROJECT, "metrics": [{"event": "bond_deal", "agg": "total", "alias": "deals"}, {"event": "bond_deal", "agg": "users", "alias": "deal_users"}, {"event": "bond_deal", "agg": "sum", "field": "amount", "alias": "deal_amount"}], "time": {**T, "grain": "total", **PIN}}},   # 金額依遮罩政策顯示分桶
    {"title": "看盤 → 委託 → 成交 漏斗（7 天窗）", "kind": "funnel", "query": {"project": PROJECT, "steps": [{"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "bond_order", "alias": "委託"}, {"event": "bond_deal", "alias": "成交"}], "window": {"value": 7, "unit": "day"}, "time": {**T, **PIN}}},
    {"title": "登入後 4 週留存", "kind": "retention", "query": {"project": PROJECT, "initial": {"event": "app_login_success"}, "return": {"event": "app_login_success"}, "model": "interval", "unit": "week", "periods": 3, "time": {**T_ALL, **PIN}}},
    {"title": "登入後 3 步路徑", "kind": "path", "width": 2, "query": {"project": PROJECT, "anchor": {"event": "app_login_success"}, "steps": 3, "sample": None, "time": {**T, **PIN}}},
    {"title": "成交歸因（首次／線性）", "kind": "attribution", "query": {"project": PROJECT, "target": {"event": "bond_deal", "value_field": "amount"}, "touches": [{"event": "$pageview", "alias": "瀏覽"}, {"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "app_login_success", "alias": "登入"}], "models": ["first", "linear"], "window_days": 7, "time": {**T, **PIN}}},
]


def seed_data(ch: ClickHouseHTTP) -> dict[str, int]:
    ds = build()
    ev, us, seg, edges = ch_rows(ds)
    ch.query("CREATE TABLE IF NOT EXISTS dmp.segment_members (segment_id LowCardinality(String), version UInt32, one_id String, one_id_uint UInt64 DEFAULT cityHash64(one_id), computed_at DateTime) ENGINE = MergeTree ORDER BY (segment_id, version, one_id_uint)")
    ch.query("CREATE TABLE IF NOT EXISTS dmp.id_edges (id_type_a LowCardinality(String), id_a String, id_type_b LowCardinality(String), id_b String, source LowCardinality(String), evidence_event_id String, first_seen DateTime64(3, 'UTC'), last_seen DateTime64(3, 'UTC'), seen_count UInt32, confidence Float32, status LowCardinality(String), one_id String, version UInt64) ENGINE = ReplacingMergeTree(version) ORDER BY (id_type_a, id_a, id_type_b, id_b)")
    ch.query(f"ALTER TABLE dmp.events DELETE WHERE project = '{PROJECT}'", settings={"mutations_sync": 2})
    for i in range(0, len(ev), 20_000):
        ch.insert_json("dmp.events", ev[i:i + 20_000])
    ch.insert_json("dmp.users", us)
    ch.query("ALTER TABLE dmp.segment_members DELETE WHERE version = 1 AND segment_id IN ('" + "','".join(ds.segments) + "')", settings={"mutations_sync": 2})
    ch.insert_json("dmp.segment_members", seg)
    ch.insert_json("dmp.id_edges", edges)
    ch.query("SYSTEM RELOAD DICTIONARY dmp.users_dict")
    return {"events": len(ev), "users": len(us), "segment_members": len(seg), "id_edges": len(edges)}


PARALLEL = [("$pageview", 620, 0.96), ("$AppViewScreen", 910, 0.99), ("app_login_success", 266, 0.996), ("hq_stockdetail_view", 441, 0.98), ("stock_order_submit", 278, 0.995),
            ("kh_entry_khpg_pv", 41, 0.94), ("kh_apply", 10, 0.998), ("search_submit", 81, 0.92), ("sip_purchase", 6, 0.997), ("act_click", 128, 0.984), ("$WebClick", 311, 0.79), ("push_click", 92, 0.98)]


def seed_migration(ch: ClickHouseHTTP, days: int = 14, seed: int = 7) -> dict[str, int]:
    """並行期樣本（docs/22 T5／T6 對帳頁用）：同一事件每日 神策 vs GA360 兩個來源、登入用戶 95% 兩邊同代碼；另寫 ingest_recon 當 T2 筆數對帳。
    放在 project=migration_demo，分析查詢預設 project 不會看到。"""
    rng = random.Random(seed)
    end = datetime(2026, 10, 16, tzinfo=UTC)
    users = [f"v1_{rng.getrandbits(80):020x}" for _ in range(600)]
    both = users[:570]; only_sa = users[570:]                       # 30 人只在神策（切換後未再出現）
    ing = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.000")
    ev: list[dict[str, Any]] = []; recon: list[dict[str, Any]] = []
    ch.query("ALTER TABLE dmp.events DELETE WHERE project = 'migration_demo'", settings={"mutations_sync": 2})
    for d in range(days):
        day = end - timedelta(days=days - 1 - d)
        wk = 0.55 if day.weekday() >= 5 else 1.0
        for name, base, ratio in PARALLEL:
            n_sa = int(base * wk * (0.9 + rng.random() * 0.2) / 3) + 1
            n_ga = int(n_sa * ratio * (0.98 + rng.random() * 0.04))
            for src, n in (("sensors", n_sa), ("ga360", n_ga)):
                pool = both + only_sa if src == "sensors" else both
                for i in range(n):
                    t = day + timedelta(seconds=rng.randrange(0, 86400))
                    ev.append({"project": "migration_demo", "time": t.strftime("%Y-%m-%d %H:%M:%S.000"), "event": name, "source": src, "one_id": rng.choice(pool), "distinct_id": "", "event_id": f"{src[:2]}_{d}_{name}_{i}",
                               "platform": rng.choice(["iOS", "Android", "Web"]), "channel": "app", "utm_source": "", "symbol": "", "amount": 0, "props": {}, "ingest_time": ing, "schema_version": 1, "batch_id": f"mig_{src}_{day.strftime('%Y%m%d')}"})
            if d < days - 2:                                              # T2：神策 → 載入筆數（兩天還沒載）
                recon.append({"task_id": "mig_exp_events", "batch_id": f"mig_sensors_{day.strftime('%Y%m%d')}", "at": (day + timedelta(hours=18)).strftime("%Y-%m-%d %H:%M:%S.000"), "rows_in": n_sa, "rows_ok": n_sa, "rows_quarantined": 0, "rows_rejected": 0, "rows_loaded": n_sa - (1 if rng.random() < 0.15 else 0)})
    for i in range(0, len(ev), 20_000):
        ch.insert_json("dmp.events", ev[i:i + 20_000])
    ch.query("ALTER TABLE dmp.ingest_recon DELETE WHERE task_id = 'mig_exp_events'", settings={"mutations_sync": 2})
    ch.insert_json("dmp.ingest_recon", recon)
    return {"events": len(ev), "recon_rows": len(recon), "users": len(users)}


def seed_accounts(la: LocalAccounts, password: str, users: list[str]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for u in users:
        roles, name = ACCOUNTS.get(u, ([u], u))
        try:
            la.add(u, password, roles, name=name, email=f"{u}@dmp.local")
            out.append((u, totp_uri(u, la.data["accounts"][u]["totp_secret"])))
        except LocalError as e:
            if "已存在" in str(e):
                out.append((u, None))
            else:
                raise
    return out


def seed_dashboard(store: MemoryStore, owner: str) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = next((d for d in store.items("dashboard") if d.get("seed")), None)
    if existing:
        return existing["id"]
    cards = [{"id": f"c_seed{i:02d}", "title": c["title"], "kind": c["kind"], "query": c["query"], "chart": c.get("chart", "auto"), "width": c.get("width", 1), "note": "", "created_by": owner, "created_at": now} for i, c in enumerate(SAMPLE_CARDS)]
    d = {"id": "d_seed_bond", "name": "債券業務日報（範例）", "description": "黃金資料集（合成）：成交、漏斗、留存、路徑、歸因各一張；區間釘在 2026-10-01 … 10-16。", "owner": owner, "seed": True,
         "shared": {"roles": ["admin", "marketing", "data_analyst", "digital_ops", "it_admin"], "users": []}, "time": {"preset": "30d"}, "cards": cards, "created_at": now, "updated_at": now}
    store.put_item("dashboard", d)
    store.audit("dashboard.created", {"user": owner, "dashboard": d["id"], "name": d["name"], "cards": len(cards), "seed": True})
    return d["id"]


def main(argv: list[str] | None = None) -> None:
    args = set(argv if argv is not None else sys.argv[1:])
    store = MemoryStore(Path(os.environ["DMP_STORE_PATH"]) if os.environ.get("DMP_STORE_PATH") else None)
    if "--no-data" not in args:
        if not os.environ.get("CLICKHOUSE_URL"):
            sys.exit("需要 CLICKHOUSE_URL（或加 --no-data）")
        ch = ClickHouseHTTP(os.environ["CLICKHOUSE_URL"], user=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ.get("CLICKHOUSE_PASSWORD", ""), timeout=600)
        print(">> 黃金資料集 →", seed_data(ch))
        if "--no-migration" not in args:
            print(">> 遷移並行期樣本（project=migration_demo）→", seed_migration(ch))
    owner = "local:admin"
    if "--no-accounts" not in args:
        if not os.environ.get("DMP_LOCAL_ACCOUNTS"):
            sys.exit("需要 DMP_LOCAL_ACCOUNTS（或加 --no-accounts）")
        pw = os.environ.get("DMP_SEED_PASSWORD") or ""
        if len(pw) < 14:
            sys.exit("需要 DMP_SEED_PASSWORD（至少 14 字）")
        from ..auth.api import load_cfg
        la_cfg = load_cfg()["local_accounts"]
        allowed = [x.strip() for x in os.environ.get("DMP_LOCAL_ALLOWED_ROLES", "").split(",") if x.strip()] or list(la_cfg.get("allowed_roles") or ["custom_*"])
        la = LocalAccounts(Path(os.environ["DMP_LOCAL_ACCOUNTS"]), {**la_cfg.get("password_policy", {}), "max_days": la_cfg.get("max_days", 90)}, allowed)
        users = [u.strip() for u in os.environ.get("DMP_SEED_USERS", "admin,marketing,analyst,ops,it").split(",") if u.strip()]
        for u, uri in seed_accounts(la, pw, users):
            print(f">> 帳號 {u}：{'已存在，略過' if uri is None else 'TOTP 註冊 ' + uri}")
        if "admin" in la.data["accounts"]:
            owner = "local:" + la.data["accounts"]["admin"]["subject"]
    if "--no-dashboard" not in args:
        print(">> 範例儀表板", seed_dashboard(store, owner))


if __name__ == "__main__":
    main()
