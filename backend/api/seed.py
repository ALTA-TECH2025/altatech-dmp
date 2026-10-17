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


# 情境示範看板（對齊客戶需求表 docs/27 §A–E 的情境；黃金資料集可直接算出）：開戶轉換、推播／活動追到交易、客戶活躍與流失、交易與搜尋體驗
TQ = {**T, **PIN}
SCENARIO_DASHBOARDS: list[dict[str, Any]] = [
    {"id": "d_seed_onboard", "name": "數位開戶轉換（情境 A5／C2／C5）", "description": "需求表 A5 渠道與 UTM、C2 漏斗、C5 路徑：開戶首頁 → 完成申請 → 首次登入 → 首次委託；申請來源與平台拆分；開戶首頁後的行為路徑。", "cards": [
        {"title": "本期開戶首頁瀏覽人數／申請件數／申請人數", "kind": "events", "chart": "stat", "query": {"project": PROJECT, "metrics": [{"event": "kh_entry_khpg_pv", "agg": "users", "alias": "entry_users"}, {"event": "kh_apply", "agg": "total", "alias": "applies"}, {"event": "kh_apply", "agg": "users", "alias": "apply_users"}], "time": {**TQ, "grain": "total"}}},
        {"title": "開戶首頁 → 完成申請 → 首次登入 → 首次委託（7 天窗）", "kind": "funnel", "query": {"project": PROJECT, "steps": [{"event": "kh_entry_khpg_pv", "alias": "開戶首頁"}, {"event": "kh_apply", "alias": "完成申請"}, {"event": "app_login_success", "alias": "登入"}, {"event": "bond_order", "alias": "委託"}], "window": {"value": 7, "unit": "day"}, "time": TQ}},
        {"title": "開戶首頁瀏覽 × 來源（UTM，日）", "kind": "events", "chart": "line", "width": 2, "query": {"project": PROJECT, "metrics": [{"event": "kh_entry_khpg_pv", "agg": "users", "alias": "entry_users"}], "group_by": ["utm_source"], "time": TQ}},
        {"title": "開戶申請 × 來源／平台（本期）", "kind": "events", "chart": "bar", "query": {"project": PROJECT, "metrics": [{"event": "kh_apply", "agg": "total", "alias": "applies"}], "group_by": ["utm_source", "platform"], "time": {**TQ, "grain": "total"}}},
        {"title": "開戶首頁瀏覽 vs 完成申請（人，日）", "kind": "events", "chart": "line", "query": {"project": PROJECT, "metrics": [{"event": "kh_entry_khpg_pv", "agg": "users", "alias": "entry_users"}, {"event": "kh_apply", "agg": "users", "alias": "apply_users"}], "time": TQ}},
        {"title": "開戶首頁之後 3 步路徑", "kind": "path", "width": 2, "query": {"project": PROJECT, "anchor": {"event": "kh_entry_khpg_pv"}, "steps": 3, "sample": None, "time": TQ}},
    ]},
    {"id": "d_seed_campaign", "name": "推播與活動成效追到交易（情境 A4／D4／E3）", "description": "需求表 A4 推播追蹤、D4 行銷成效追到交易、E3 發送成效回收：推播點擊 → 委託 → 成交漏斗；推播點擊 × 發送計畫；活動點擊 × 渠道；成交歸因（首次／末次／線性）；點擊後 7 天成交。", "cards": [
        {"title": "本期推播點擊人數／活動點擊人數／成交金額", "kind": "events", "chart": "stat", "query": {"project": PROJECT, "metrics": [{"event": "push_click", "agg": "users", "alias": "push_users"}, {"event": "act_click", "agg": "users", "alias": "act_users"}, {"event": "bond_deal", "agg": "sum", "field": "amount", "alias": "deal_amount"}], "time": {**TQ, "grain": "total"}}},
        {"title": "推播點擊 → 委託送出 → 成交（7 天窗）", "kind": "funnel", "query": {"project": PROJECT, "steps": [{"event": "push_click", "alias": "推播點擊"}, {"event": "stock_order_submit", "alias": "委託送出"}, {"event": "bond_deal", "alias": "成交"}], "window": {"value": 7, "unit": "day"}, "time": TQ}},
        {"title": "推播點擊 × 發送計畫（utm_campaign，日）", "kind": "events", "chart": "line", "width": 2, "query": {"project": PROJECT, "metrics": [{"event": "push_click", "agg": "total", "alias": "clicks"}], "group_by": ["utm_campaign"], "top_n": 6, "time": TQ}},
        {"title": "成交歸因：推播／活動／網頁（首次、末次、線性）", "kind": "attribution", "width": 2, "query": {"project": PROJECT, "target": {"event": "bond_deal", "value_field": "amount"}, "touches": [{"event": "push_click", "alias": "推播點擊"}, {"event": "act_click", "alias": "活動點擊"}, {"event": "$pageview", "alias": "網頁瀏覽"}], "models": ["first", "last", "linear"], "window_days": 7, "time": TQ}},
        {"title": "活動點擊 × 渠道（日）", "kind": "events", "chart": "line", "query": {"project": PROJECT, "metrics": [{"event": "act_click", "agg": "total", "alias": "act_clicks"}], "group_by": ["channel"], "time": TQ}},
        {"title": "推播點擊後 7 天內成交（日留存）", "kind": "retention", "query": {"project": PROJECT, "initial": {"event": "push_click"}, "return": {"event": "bond_deal"}, "model": "interval", "unit": "day", "periods": 7, "time": TQ}},
    ]},
    {"id": "d_seed_retention", "name": "客戶活躍與流失預警（情境 C3／C8）", "description": "需求表 C3 留存、C8 分群：登入週留存；登入人數依生命週期／價值分級拆分；看盤與登入人數；委託失敗與刪單；登入後路徑。搭配「沉睡客喚醒」分群推送。", "cards": [
        {"title": "登入後 4 週留存", "kind": "retention", "query": {"project": PROJECT, "initial": {"event": "app_login_success"}, "return": {"event": "app_login_success"}, "model": "interval", "unit": "week", "periods": 3, "time": {**T_ALL, **PIN}}},
        {"title": "登入人數 × 生命週期（日）", "kind": "events", "chart": "line", "width": 2, "query": {"project": PROJECT, "metrics": [{"event": "app_login_success", "agg": "users", "alias": "login_users"}], "group_by": ["user.lifecycle"], "time": TQ}},
        {"title": "登入人數 × 價值分級（本期）", "kind": "events", "chart": "bar", "query": {"project": PROJECT, "metrics": [{"event": "app_login_success", "agg": "users", "alias": "login_users"}], "group_by": ["user.tier"], "time": {**TQ, "grain": "total"}}},
        {"title": "看盤人數 vs 登入人數（日）", "kind": "events", "chart": "line", "query": {"project": PROJECT, "metrics": [{"event": "app_login_success", "agg": "users", "alias": "login_users"}, {"event": "hq_stockdetail_view", "agg": "users", "alias": "quote_users"}], "time": TQ}},
        {"title": "委託失敗／刪單次數（日）", "kind": "events", "chart": "line", "query": {"project": PROJECT, "metrics": [{"event": "order_rejected", "agg": "total", "alias": "rejected"}, {"event": "order_cancelled", "agg": "total", "alias": "cancelled"}], "time": TQ}},
        {"title": "登入後 3 步路徑", "kind": "path", "width": 2, "query": {"project": PROJECT, "anchor": {"event": "app_login_success"}, "steps": 3, "sample": None, "time": TQ}},
    ]},
    {"id": "d_seed_trading", "name": "交易與搜尋體驗（情境 C1／C4）", "description": "需求表 C1 事件分析、C4 歸因：搜尋 → 看盤 → 委託 → 成交漏斗；成交金額依偏好市場、成交筆數依平台；熱門標的 Top 8；定期定額申購趨勢；成交歸因（看盤／搜尋／登入）。", "cards": [
        {"title": "搜尋 → 看盤 → 委託送出 → 成交（7 天窗）", "kind": "funnel", "width": 2, "query": {"project": PROJECT, "steps": [{"event": "search_submit", "alias": "搜尋"}, {"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "stock_order_submit", "alias": "委託送出"}, {"event": "bond_deal", "alias": "成交"}], "window": {"value": 7, "unit": "day"}, "time": TQ}},
        {"title": "成交金額 × 偏好市場（本期）", "kind": "events", "chart": "bar", "query": {"project": PROJECT, "metrics": [{"event": "bond_deal", "agg": "sum", "field": "amount", "alias": "deal_amount"}], "group_by": ["user.pref_market"], "time": {**TQ, "grain": "total"}}},
        {"title": "成交筆數 × 平台（本期）", "kind": "events", "chart": "bar", "query": {"project": PROJECT, "metrics": [{"event": "bond_deal", "agg": "total", "alias": "deals"}], "group_by": ["platform"], "time": {**TQ, "grain": "total"}}},
        {"title": "熱門成交標的 Top 8（本期）", "kind": "events", "chart": "bar", "query": {"project": PROJECT, "metrics": [{"event": "bond_deal", "agg": "total", "alias": "deals"}], "group_by": ["symbol"], "top_n": 8, "time": {**TQ, "grain": "total"}}},
        {"title": "定期定額申購 vs 搜尋送出（日）", "kind": "events", "chart": "line", "query": {"project": PROJECT, "metrics": [{"event": "sip_purchase", "agg": "total", "alias": "sip"}, {"event": "search_submit", "agg": "total", "alias": "searches"}], "time": TQ}},
        {"title": "成交歸因：看盤／搜尋／登入（首次、線性、位置）", "kind": "attribution", "width": 2, "query": {"project": PROJECT, "target": {"event": "bond_deal", "value_field": "amount"}, "touches": [{"event": "hq_stockdetail_view", "alias": "看盤"}, {"event": "search_submit", "alias": "搜尋"}, {"event": "app_login_success", "alias": "登入"}], "models": ["first", "linear", "position"], "window_days": 7, "time": TQ}},
    ]},
]


def _put_dashboard(store: MemoryStore, owner: str, id_: str, name: str, description: str, cards_spec: list[dict[str, Any]]) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cards = [{"id": f"c_{id_[7:]}{i:02d}", "title": c["title"], "kind": c["kind"], "query": c["query"], "chart": c.get("chart", "auto"), "width": c.get("width", 1), "note": "", "created_by": owner, "created_at": now} for i, c in enumerate(cards_spec)]
    d = {"id": id_, "name": name, "description": description, "owner": owner, "seed": True,
         "shared": {"roles": ["admin", "marketing", "data_analyst", "digital_ops", "it_admin"], "users": []}, "time": {"preset": "30d"}, "cards": cards, "created_at": now, "updated_at": now}
    store.put_item("dashboard", d)
    store.audit("dashboard.created", {"user": owner, "dashboard": d["id"], "name": d["name"], "cards": len(cards), "seed": True})
    return d["id"]


def seed_dashboard(store: MemoryStore, owner: str) -> str:
    """範例看板：債券業務日報 + 四個情境看板。重跑 seed 會以程式裡的規格重建卡片（範例看板不保留使用者改動；自建看板不受影響）。回傳第一個 id。"""
    specs = [("d_seed_bond", "債券業務日報（範例）", "黃金資料集（合成）：成交、漏斗、留存、路徑、歸因各一張；區間釘在 2026-10-01 … 10-16。", SAMPLE_CARDS)] + [(sd["id"], sd["name"], sd["description"], sd["cards"]) for sd in SCENARIO_DASHBOARDS]
    for id_, name, desc, cards in specs:
        _put_dashboard(store, owner, id_, name, desc, cards)
    return "d_seed_bond"


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
