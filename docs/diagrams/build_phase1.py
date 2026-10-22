"""產生第一階段定版（2026-10-22）的三張工程圖：phase1-system-architecture.svg、phase1-component-deployment.svg、phase1-data-flow.svg。
口徑：docs/23 v1.1（微批、事件表只增刪不改寫）、docs/19 v1.3（OneID 回補查詢時換算）、docs/07 §1.3（3 台 VM Docker Compose、Redis、Loki）。
沿用 build.py 的 SVG 工具與狀態標籤；執行 python3 docs/diagrams/build_phase1.py。PNG 以 render_png.py（Playwright／Chromium）產生。"""
from __future__ import annotations
import html
import build
from build import SVG, C, FONT

# 第一階段新元件的實作狀態（對照 docs/27；PR #74／#75／#76 尚未合併的都標規劃中）
build.STATUS[:0] = [
    ("Docker Compose", "planned"), ("3 台 VM", "planned"), ("Redis", "planned"), ("Loki", "planned"), ("Alloy", "planned"), ("cAdvisor", "planned"), ("Sentinel", "planned"), ("Keeper", "partial"),
    ("F5", "confirm"), ("SFTP", "confirm"), ("永豐 AD", "confirm"), ("永豐 NAS", "confirm"), ("偉康 CDP", "confirm"),
    ("微批處理", "partial"), ("去重", "partial"), ("先刪再寫", "planned"), ("OneID 歸戶", "done"), ("回補對照", "planned"), ("one_id_eff", "planned"),
    ("dmp-core worker", "partial"), ("dmp-core api", "done"), ("dmp-batch（Airflow）", "partial"), ("SeaweedFS", "planned"), ("PostgreSQL 主", "partial"), ("PostgreSQL 備援", "planned"),
    ("ClickHouse 副本", "partial"), ("備份作業", "planned"), ("Prometheus + Grafana", "partial"), ("語意層", "done"), ("分析 API", "done"), ("分群與標籤", "partial"), ("名單推送", "partial"),
    ("日彙總重建", "partial"), ("Webhook 推送", "partial"), ("業務單位", "done"), ("個資守門", "done"), ("標準化", "partial"), ("入倉", "partial"), ("接收區", "confirm"),
    ("GA360 日批匯出檔", "done"), ("數據中台", "confirm"), ("舊平台歷史", "done"), ("MA 發送紀錄", "confirm"),
]
build.NO_BADGE = build.NO_BADGE + ("GA360（", "數據中台（", "現行數軌", "MA 發送紀錄（", "映像 4 個（web／core", "入口：F5", "第二階段（")


def stamp(s: SVG):
    s.parts.pop(2)   # 換掉 build.SVG 預設的副標
    s.parts.append(f'<text x="28" y="62" font-family="{FONT}" font-size="12" fill="{C["muted"]}">AltaTech DMP · 第一階段定版 2026-10-22 · 由 docs/diagrams/build_phase1.py 產生 · 方塊右上角為實作狀態（對照 docs/27、PR #74／#75／#76）</text>')


# ------------------------------------------------------------------ 1. 系統架構圖（第一階段，分層）
def system_architecture():
    s = SVG(1600, 1000, "系統架構圖：第一階段定版（3 台 VM Docker Compose · 微批 · 事件表只增刪不改寫）"); stamp(s)
    L = 28; W = 1230
    s.group(L, 80, W, 100, "體驗層  Experience")
    for i, (t, ls) in enumerate([("DMP Web（React）", ["總覽／事件／漏斗／留存／路徑／歸因／用戶列表", "分群標籤／數據整合／權限中心／系統監控"]), ("業務單位（瀏覽器）", ["永豐 AD／SSO 登入", "儀表板、書籤、自助分析"]), ("偉康 CDP／MA", ["名單・標籤 Webhook 推送（T+1）", "MA 發送紀錄每日回流（待討論）"]), ("AI 工作台", ["技能庫、問數", "第二階段，部署套件保留"])]):
        s.box(L + 14 + i * 300, 110, 285, 60, t, ls, "purple" if i == 3 else "brand")
    s.group(L, 195, W, 110, "語意與服務層  Semantic & Service（dmp-core api ×2，Python / FastAPI）")
    for i, (t, ls) in enumerate([("語意層 + 查詢編譯器", ["YAML 語意定義 → ClickHouse SQL", "用戶口徑一律用 one_id_eff（回補換算）"]), ("分析 API", ["事件、漏斗、留存、路徑、歸因、用戶列表", "自定義 SQL 沙箱（sql_user profile）"]), ("分群與標籤 / OneID API", ["規則 → Bitmap；標籤 10 種；審批", "ID 綁定、人工合併（寫對照）、DSAR"]), ("Policy Engine", ["模組權限 → 行級 → 欄位隱碼 L0–L4 → 審批", "UI 與 REST 共用；每次授權寫審計"])]):
        s.box(L + 14 + i * 300, 225, 285, 70, t, ls, "brand")
    s.group(L, 320, W, 120, "處理層  微批處理（dmp-core worker ×1 ＋ dmp-batch Airflow＋dbt；ClickHouse SQL；第一階段不上 Flink）")
    for i, (t, ls) in enumerate([("匯入 + 個資守門", ["接收區檔案／Trino 增量 → 暫存表", "L4 欄位不進倉；個資樣式命中整列隔離"]), ("標準化 + 去重", ["橋接轉名、時間轉 UTC、HMAC 代碼化", "行為：time（毫秒）＋event_id；線下不去重"]), ("OneID 歸戶 + 回補對照", ["dictGet(id_map) 寫入時歸戶，事件不改寫", "登入 → 寫對照；查詢時換算"]), ("入倉 + 日彙總重建", ["ReplacingMergeTree，只新增／刪除", "L1 按日重建（近 8 天每日）；分群例行"])]):
        s.box(L + 14 + i * 300, 350, 285, 78, t, ls, "teal")
    s.group(L, 455, W, 110, "儲存層  Storage（四個有狀態服務，全部在永豐機房）")
    for i, (t, ls, c) in enumerate([("ClickHouse ×2 + Keeper ×3", ["events（L0）、日彙總（L1）、Bitmap", "users／dim_*／id_map、回補對照字典"], "yellow"), ("PostgreSQL 主備 + pgvector", ["控制面：角色、審批、審計索引、分群定義", "Airflow 中繼資料、Keycloak"], "yellow"), ("Redis 主從 + Sentinel", ["查詢快取、估數快取、登入前狀態", "AGPLv3 內部使用例外（待法務）"], "yellow"), ("SeaweedFS（三台，S3）", ["master ×3、複本 001；備份桶、冷層", "→ rclone 同步到永豐 NAS"], "yellow")]):
        s.box(L + 14 + i * 300, 485, 285, 68, t, ls, c)
    s.group(L, 580, W, 110, "接入層  Ingestion（檔案／表 → 暫存表，每日或每 15 分鐘）")
    for i, (t, ls) in enumerate([("GA360 日批匯出檔", ["BigQuery → CSV／Parquet（Merkle 送出）", "每日產檔 T+1，資料新鮮度 T-2"]), ("接收區（SFTP，永豐 IT）", ["GA 匯出檔、MA 發送紀錄落檔", "worker 唯讀掛載 /landing"]), ("數據中台（Trino／批次）", ["交易・帳務・客戶：每日或 15 分鐘增量", "方式待討論；Kafka 第一階段不用"]), ("舊平台歷史（一次性）", ["Impala／HDFS／查詢 API → 2024-01 起", "走同一套標準化與歸戶"])]):
        s.box(L + 14 + i * 300, 610, 285, 68, t, ls, "green")
    s.group(L, 705, W, 90, "資料來源  Sources（永豐機房外只有 GA360 匯出檔進來、名單推送出去）")
    for i, (t, ls) in enumerate([("GA360（Web／App 埋點）", ["去識別化客戶代碼 user_id；交易不送 GA"]), ("數據中台（交易／帳務／客戶）", ["update_time，無事件時間"]), ("現行數軌（歷史）", ["神策事件 + 用戶表，到期前匯出"]), ("MA 發送紀錄（偉康）", ["推播、簡訊、EDM 送達／開啟／點擊"])]):
        s.box(L + 14 + i * 300, 735, 285, 48, t, ls, "gray")
    for x in (L + 14 + 142, L + 14 + 300 + 142, L + 14 + 600 + 142, L + 14 + 900 + 142):
        s.arrow(x, 705, x, 680)
    s.box(L, 815, W, 44, "第二階段（部署套件預留、第一階段不啟用）：Flink 即時觸發（CEP） · Kafka 事件匯流排 · AI 工作台（技能庫、問數） · 即時標籤", [], "purple", small=True)
    s.note(L + 14, 885, "縱向：資料由下往上（來源 → 接入 → 儲存 ← 處理 → 語意／服務 → 體驗）。部署拓樸以 docs/44 §2 為準（D-01～D-04，docs/45）。事件表 dmp.events 只新增、刪除，不改寫；排序鍵 (project, event_date, event, distinct_id_uint, time, event_id)，SAMPLE BY distinct_id_uint。")
    s.note(L + 14, 905, "去重鍵：行為事件 time（毫秒）＋event_id（來源給或內容雜湊，雜湊含時間）；線下資料以 update_time 當 time、全部入庫但仍產生 event_id；重送／重跑與 GA 72 小時重寫一律先依 batch_id 刪除再寫入。")
    s.note(L + 14, 925, "OneID：寫入時 dictGet(id_map) 歸戶；登入後近 7 天匿名事件不改寫，寫 oneid_backfill 對照、查詢時以區間字典換算成 one_id_eff（docs/19 v1.3）。")
    X = L + W + 20; GW = 1600 - X - 28
    s.group(X, 80, GW, 225, "安全與治理")
    s.box(X + 12, 110, GW - 24, 56, "身分與權限", ["Keycloak broker → 永豐 AD", "五種角色＋自訂、審批、臨時提權"], "red")
    s.box(X + 12, 174, GW - 24, 56, "資料保護", ["L4 不進倉、L3 尾碼遮罩、L2 區間化", "HMAC／FPE 金鑰在 HSM"], "red")
    s.box(X + 12, 238, GW - 24, 56, "審計與法遵", ["audit_log append-only → NAS", "匯出浮水印、揭露申請、DSAR"], "red")
    s.group(X, 320, GW, 160, "觀測  Observability（VM3）")
    s.box(X + 12, 350, GW - 24, 56, "Prometheus + Grafana", ["node、ClickHouse、Keeper、API /metrics", "告警 → Email／LINE（/v1/ops/notify）"], "gray")
    s.box(X + 12, 414, GW - 24, 56, "Loki + Alloy", ["三台容器 stdout（Alloy 收），保留 30 天", "Loki AGPLv3 內部使用例外（待法務）"], "gray")
    s.group(X, 495, GW, 232, "部署  Delivery（docs/07 §1.3、deploy/compose）")
    s.box(X + 12, 525, GW - 24, 72, "3 台 VM Docker Compose", ["VM1 web/api/CH r1/PG 主/Redis 主", "VM2 worker/batch/CH r2/PG 備", "VM3 Keeper 3/Keycloak/監控/備份；docs/44"], "gray")
    s.box(X + 12, 603, GW - 24, 52, "入口：F5／HAProxy + TLS", ["內部 CA；/auth → Keycloak", "容器間內網；加密待討論"], "gray")
    s.box(X + 12, 661, GW - 24, 56, "備份作業 → 永豐 NAS", ["ClickHouse 日增量／週全量（S3）", "PostgreSQL WAL 歸檔＋日 dump"], "gray")
    s.group(X, 740, GW, 120, "供應鏈與授權（docs/32）")
    s.box(X + 12, 770, GW - 24, 78, "映像 4 個（web/core/batch/ingest）", ["UBI 9／distroless、SBOM、Trivy", "全部 OSI；AGPLv3 例外：Redis、Grafana、Loki", "待法務核可；退路 Valkey／VictoriaLogs"], "gray")
    s.legend(L + 14, 988, [("brand", "體驗／服務"), ("teal", "微批處理"), ("yellow", "儲存"), ("green", "接入"), ("purple", "第二階段"), ("red", "安全"), ("gray", "來源／支撐")])
    s.save("phase1-system-architecture.svg")


# ------------------------------------------------------------------ 2. 元件組成圖（3 台 VM 部署視圖）
def component_deployment():
    s = SVG(1600, 980, "元件組成圖：第一階段機房部署（3 台 VM · Docker Compose · docs/44）"); stamp(s)
    s.group(28, 80, 250, 560, "機房外部／既有系統")
    for i, (t, ls) in enumerate([("GA360（BigQuery 匯出檔）", ["Merkle 每日產檔 → SFTP"]), ("接收區 SFTP（永豐 IT）", ["落檔到 VM2 /data/dmp/landing"]), ("數據中台（Trino／批次）", ["交易・帳務・客戶增量"]), ("現行數軌（歷史一次性）", ["Impala／HDFS／查詢 API"]), ("永豐 AD（LDAP）", ["Keycloak broker"]), ("永豐 NAS", ["備份、WAL 歸檔、審計 WORM"]), ("密鑰庫（OpenBao／HSM）", ["/run/secrets/dmp-secrets.json"])]):
        s.box(42, 110 + i * 74, 222, 60, t, ls, "gray", small=True)
    s.group(300, 80, 960, 800, "永豐機房  三台 VM（RHEL 9／Rocky 9，Docker Engine 27 + Compose v2；資料服務 host 網路，應用容器 bridge）", fill="#FBFCFE")
    vms = [
        ("VM-1（測試 16C/128G · 正式 64C/256G）", [("dmp-web ×1  :80", "nginx，F5 指到 VM1／VM2 的 80", "brand"), ("dmp-core api ×1  :8080", "DMP_TICK=0；/healthz、/metrics", "brand"), ("ClickHouse 副本 r1  :8123/9000/9009", "config.d 三台同一份，副本名 from_env", "yellow"), ("Keeper 1  :9181/9234", "", "yellow"), ("PostgreSQL 主  :5432", "串流複寫 → VM2；WAL → NAS", "yellow"), ("Redis 主 + Sentinel  :6379/26379", "AOF；只放快取；三票自動切換", "yellow"), ("SeaweedFS + S3 閘道 :8333", "master/volume/filer；三台 Raft、複本 001", "yellow")]),
        ("VM-2", [("dmp-core worker ×1", "DMP_TICK=1：唯一背景排程（同步、分群、對照、推送）", "brand"), ("dmp-batch（Airflow + dbt）  :8082", "LocalExecutor；匯入 DAG、dbt 日批、日彙總重建", "brand"), ("dmp-web / dmp-core api 第二份", ":80／:8080；F5 輪詢", "brand"), ("ClickHouse 副本 r2", "", "yellow"), ("Keeper 2", "", "yellow"), ("PostgreSQL 備援（hot standby）", "首次啟動 pg_basebackup；手動 promote", "yellow"), ("Redis 從 + Sentinel · SeaweedFS", "master/volume；/landing 掛給 worker", "yellow")]),
        ("VM-3", [("Keeper 3 · Sentinel 3 · SeaweedFS", "三台法定人數；S3 閘道第二份 :8333", "yellow"), ("Keycloak  :8180", "/auth；broker → 永豐 AD；DB 在 VM1 PG", "red"), ("Prometheus  :9090", "抓三台 node、CH、Keeper、api", "gray"), ("Grafana  :3000", "看板；AGPLv3 內部使用", "gray"), ("Loki  :3100", "三台 Alloy 送來；保留 30 天", "gray"), ("備份作業（主機 cron）", "ClickHouse、PostgreSQL、rclone → NAS", "gray"), ("dmp-ingest（選配）  :8081", "MA／LINE 直送才啟用；Kafka/Flink 第二階段（D-04）", "green")]),
    ]
    for i, (name, items) in enumerate(vms):
        x = 316 + i * 316; y = 110
        s.group(x, y, 300, 560, name, fill="#FFFFFF", dash=True)
        for j, (t, sub, col) in enumerate(items):
            s.box(x + 10, y + 34 + j * 74, 280, 66, t, [sub] if sub else [], col, small=True)
    s.box(316, 690, 930, 46, "入口：F5／HAProxy（永豐既有）+ TLS（內部 CA）→ VM1／VM2 :80（web → api）；/auth → VM3 :8180；健康檢查 /healthz", [], "brand", small=True)
    s.box(316, 746, 930, 46, "每台：node-exporter :9100、cAdvisor :9101、Alloy（容器日誌 → VM3 Loki）；安全：容器非 root、唯讀、密鑰只進 /run/secrets；防火牆只開 10.10.0.0/24", [], "red", small=True)
    s.box(316, 802, 930, 46, "映像 4 個（web／core／batch／ingest）與 Helm 方案共用；Flink、Kafka、agent 映像第一階段不啟動；升級：pull → 逐台 up -d（VM2 → VM1 → VM3）", [], "gray", small=True)
    s.note(316, 866, "故障：VM1 掛 → F5 導到 VM2，PG 手動 promote、Redis 由 Sentinel 自動切換；VM2 掛 → 查詢不中斷、排程暫停；VM3 掛 → Keeper 仍有法定人數、先救 Keycloak（deploy/compose/README §4）。")
    s.group(1280, 80, 292, 300, "使用者與下游")
    for i, (t, ls) in enumerate([("業務單位（瀏覽器）", ["https://dmp.… 經 F5", "AD／SSO 登入"]), ("偉康 CDP／MA", ["Webhook 推送名單・標籤（T+1）", "簽章、重試、交付紀錄"]), ("數據輸出（內部系統）", ["SFTP／S3／Webhook，走匯出審批", "只送永豐內部"])]):
        s.box(1294, 110 + i * 86, 264, 72, t, ls, "brand", small=True)
    s.group(1280, 400, 292, 300, "待討論／待確認")
    for i, (t, ls) in enumerate([("VM 規格與配置", ["永豐資訊部門"]), ("接收區與 SFTP", ["永豐 IT"]), ("容器間加密", ["資安要求再加 9440／ssl"]), ("AGPLv3 例外", ["Redis、Grafana、Loki；法務"])]):
        s.box(1294, 430 + i * 66, 264, 56, t, ls, "red", small=True)
    s.arrow(264, 214, 316, 214); s.note(268, 206, "檔案")
    s.arrow(264, 436, 316, 436); s.note(268, 428, "LDAP")
    s.arrow(1246, 160, 1294, 160); s.note(1250, 152, "HTTPS")
    s.arrow(1246, 246, 1294, 246); s.note(1250, 238, "Webhook")
    s.note(28, 920, "有狀態服務 4 個：ClickHouse（2 副本＋Keeper 3）、PostgreSQL（主備，手動 promote）、Redis（Sentinel 自動切換）、SeaweedFS（三台 master）；其餘無狀態。Kafka、Flink 第一階段不部署（D-04）。")
    s.note(28, 940, "三台 .env 只差 VM_HOST、KEEPER_ID、CH_REPLICA；./dmp.sh vmN check 驗證設定；主機 cron 跑備份（common/scripts/crontab.vm3）。")
    s.legend(28, 968, [("brand", "應用"), ("yellow", "資料服務"), ("gray", "維運／外部"), ("red", "安全／待確認"), ("green", "選配")])
    s.save("phase1-component-deployment.svg")


# ------------------------------------------------------------------ 3. 數據流圖（第一階段微批）
def data_flow():
    s = SVG(1600, 1000, "數據流圖：第一階段（每日／每 15 分鐘微批 · 去重與回補規則）"); stamp(s)
    cols = [("① 來源", 28), ("② 接收／匯入", 290), ("③ 微批處理（ClickHouse SQL + dbt）", 552), ("④ 儲存", 814), ("⑤ 語意／服務", 1076), ("⑥ 消費", 1338)]
    for t, x in cols:
        s.parts.append(f'<text x="{x}" y="92" font-family="{FONT}" font-size="13" font-weight="800" fill="{C["brand"]}">{html.escape(t)}</text>')
        s.parts.append(f'<line x1="{x}" y1="100" x2="{x+234}" y2="100" stroke="{C["line"]}" stroke-width="2"/>')
    src = [("GA360（BigQuery 匯出檔）", ["每日產檔 T+1；資料新鮮度 T-2"]), ("數據中台（交易／帳務／客戶）", ["update_time；每日或每 15 分鐘"]), ("現行數軌歷史（一次性）", ["2024-01 起事件 + 用戶表"]), ("MA 發送紀錄（偉康）", ["每日檔；欄位待確認"]), ("CRM 綁定指令（客戶↔帳戶）", ["日批全量"]), ("MA／LINE 直送（選配）", ["/v1/events，自帶 event_id"])]
    for i, (t, ls) in enumerate(src): s.box(28, 115 + i * 78, 234, 60, t, ls, "gray", small=True)
    ing = [("接收區 SFTP → /landing", ["永豐 IT 落檔；worker 唯讀"]), ("匯入器（dmp-core worker）", ["檔案／Trino → 暫存表；batch_id"]), ("個資守門", ["L4 鍵整列隔離；樣式命中拒收"]), ("GA 72h 重寫對帳", ["D-2／D-3 列數變 → 先刪該日再重載"]), ("舊平台格式匯入", ["協議 1:1 映射 → 暫存"]), ("dmp-ingest（選配）", ["校驗、冪等、直送暫存"])]
    for i, (t, ls) in enumerate(ing): s.box(290, 115 + i * 78, 234, 60, t, ls, "green", small=True)
    pr = [("標準化", ["橋接轉名 dictGet、時間 UTC", "HMAC／FPE 代碼化"]), ("去重：time＋event_id", ["毫秒；內容雜湊含時間；寫入前查倉", "線下：update_time 當 time，不去重"]), ("OneID 歸戶", ["dictGet(id_map) 寫入時決定", "不自動合併已存在用戶"]), ("登入 → 寫回補對照", ["oneid_backfill：distinct_id、7 天視窗", "事件不改寫"]), ("入倉：只新增／刪除", ["ReplacingMergeTree；重跑先刪 batch_id", "ORDER BY 不含 one_id"]), ("日彙總重建（dbt）", ["events_daily_agg 按日重建", "以 one_id_eff 聚合；近 8 天每日"])]
    for i, (t, ls) in enumerate(pr): s.box(552, 115 + i * 78, 234, 60, t, ls, "teal", small=True)
    st = [("ClickHouse events（L0）", ["排序鍵 …distinct_id_uint, time, event_id", "SAMPLE BY distinct_id_uint"]), ("oneid_backfill + 區間字典", ["range_hashed，每 10 分鐘重載", "events.one_id_eff ALIAS"]), ("events_daily_agg（L1）", ["90% 查詢不碰明細"]), ("users / dim_* / id_map", ["快照 INSERT，不 UPDATE；dictionary"]), ("user_tags Bitmap / segments", ["版本表；估數 1/16 抽樣"]), ("PostgreSQL 控制面", ["角色、審批、審計索引、分群定義"])]
    for i, (t, ls) in enumerate(st): s.box(814, 115 + i * 78, 234, 60, t, ls, "yellow", small=True)
    sv = [("語意層 + 查詢編譯器", ["用戶口徑一律 e.one_id_eff"]), ("Policy Engine", ["模組→行級→隱碼→審批→審計"]), ("分析 API", ["事件、漏斗、留存、路徑、歸因、列表"]), ("分群與標籤", ["規則 → Bitmap；例行 15 分鐘"]), ("名單推送（審批單）", ["Webhook 簽章、重試、交付紀錄"]), ("數據輸出", ["SFTP／S3／Webhook；遮罩＋浮水印"])]
    for i, (t, ls) in enumerate(sv): s.box(1076, 115 + i * 78, 234, 60, t, ls, "brand", small=True)
    cs = [("DMP Web（業務單位）", ["看板、書籤、自助分析"]), ("偉康 CDP／MA", ["名單・標籤 T+1；即時觸發第二階段"]), ("MA 發送紀錄回流 → ②", ["每日檔，欄位與介面待討論"]), ("內部系統（數據輸出）", ["只送永豐內部"]), ("Grafana（維運）", ["資料品質、延遲、對帳差異"]), ("稽核", ["audit_log、揭露申請、DSAR"])]
    for i, (t, ls) in enumerate(cs): s.box(1338, 115 + i * 78, 234, 60, t, ls, "gray", small=True)
    for i, j in [(0, 0), (1, 1), (2, 4), (3, 0), (4, 1), (5, 5)]:
        s.arrow(262, 145 + i * 78, 290, 145 + j * 78, curve=True)
    for i in range(5): s.arrow(524, 145 + i * 78, 552, 145, curve=True)
    for i in range(5): s.arrow(669, 175 + i * 78, 669, 193 + i * 78, color=C["teal"])
    s.arrow(786, 145 + 4 * 78, 814, 145, curve=True)
    s.arrow(786, 145 + 3 * 78, 814, 145 + 1 * 78, curve=True)
    s.arrow(786, 145 + 5 * 78, 814, 145 + 2 * 78, curve=True)
    for i in (0, 1, 2, 3, 4): s.arrow(1048, 145 + i * 78, 1076, 145, curve=True)
    for i in range(5): s.arrow(1193, 175 + i * 78, 1193, 193 + i * 78, color=C["brand"])
    s.arrow(1310, 145 + 2 * 78, 1338, 145, curve=True)
    s.arrow(1310, 145 + 4 * 78, 1338, 145 + 1 * 78, curve=True)
    s.arrow(1310, 145 + 5 * 78, 1338, 145 + 3 * 78, curve=True)
    s.arrow(1310, 145 + 1 * 78, 1338, 145 + 5 * 78, curve=True, dash=True)
    s.group(28, 610, 1544, 170, "關鍵規則（docs/23 §4.1、docs/19 v1.3；PR #74）")
    notes = ["• 事件表 dmp.events 只新增、刪除，不改寫。排序鍵 (project, event_date, event, distinct_id_uint, time, event_id)，one_id 不在排序鍵；SAMPLE BY distinct_id_uint（同裝置登入前後在同一桶）。",
             "• 行為事件去重鍵 = time（毫秒）＋event_id：來源自帶 event_id 的用組合鍵；GA360、舊平台由內容雜湊產生（雜湊含事件時間與入倉參數）。寫入前查倉不重寫，合併時由排序鍵保底。",
             "• 線下資料（交易／帳務／客戶）沒有事件時間、只有 update_time：以 update_time 當 time，每筆狀態變更一筆事件，不去重、全部入庫；仍產生 event_id（含 update_time），否則排序鍵相同會被合併吃掉。",
             "• 重載一律先刪再寫：同批重送／重跑、GA 72 小時重寫後的 D-2／D-3 重載，都先依 batch_id DELETE 再整批 INSERT；只追加會讓重寫過的事件變成兩份。",
             "• OneID：寫入時 dictGet(id_map) 歸戶；登入後近 7 天匿名事件不改寫，寫 oneid_backfill 對照，查詢時區間字典換算成 one_id_eff（視窗內且寫入時歸屬 = 對照的匿名用戶才算走）；人工合併同一做法，撤銷即刪對照。",
             "• 個資在接入層擋下：L4 不進倉；客戶 ID／帳號在匯入端 HMAC／FPE 代碼化（金鑰在 HSM）；分析倉只有代碼化 ID；查詢回傳前套 L3 遮罩／L2 區間化。",
             "• 第一階段沒有串流引擎：所有來源都是每日或每 15 分鐘批次；即時觸發、即時標籤留第二階段（Flink CEP 一個 job）。"]
    for i, n in enumerate(notes): s.note(42, 652 + i * 17, n, C["ink"])
    s.group(28, 800, 1544, 170, "頻率與延遲（docs/17 §3、提案簡報數據流頁）")
    sla = [("行為事件（GA360）", "每日 T+1 收檔；新鮮度 T-2", "Merkle 產檔 → SFTP → 匯入 → 07:00 前可查"), ("交易／帳務／客戶", "每日；可每 15 分鐘增量", "數據中台批次／Trino → 暫存 → 入倉 < 20 分鐘"), ("名單・標籤推送 CDP", "T+1；例行 15 分鐘批", "分群例行 → 推送單審批 → Webhook"), ("回補對照生效", "≤ 10 分鐘（字典重載）", "L0 明細即時；L1 日彙總近 8 天每日重建"),
           ("MA 發送紀錄回流", "每日檔（待討論）", "回到接收區 → 同一管線"), ("30 天漏斗（1 億事件）", "P95 < 10 秒", "Bitmap 預篩 + windowFunnel"), ("分群估數", "< 1 秒", "Bitmap 交併差；1/16 抽樣"), ("歷史匯入", "一次性，70 億筆", "s3()／檔案 INSERT SELECT ≥ 100 萬列/秒")]
    for i, (a, b, c) in enumerate(sla):
        x = 42 + (i % 4) * 382; y = 830 + (i // 4) * 66
        s.box(x, y, 366, 56, f"{a}：{b}", [c], "brand", small=True)
    s.save("phase1-data-flow.svg")


if __name__ == "__main__":
    system_architecture(); component_deployment(); data_flow()
    if build.UNMATCHED: print("沒有狀態的方塊：", sorted(set(build.UNMATCHED)))
