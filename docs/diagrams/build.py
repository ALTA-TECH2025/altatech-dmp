"""產生四張 SVG 架構圖：system-architecture.svg、component-deployment.svg、data-flow.svg、module-map.svg。
修改下方的資料結構後執行 python3 docs/diagrams/build.py 重新產生。純標準函式庫，無相依。"""
from __future__ import annotations
from pathlib import Path
import html

OUT = Path(__file__).resolve().parent
FONT = "'Plus Jakarta Sans','Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif"
C = dict(brand="#2C73EA", brandsoft="#E9F0FD", soft="#E9F0FD", line="#B9CFF5", teal="#12B5A8", tealsoft="#E3F7F5", purple="#7C4DD6", purplesoft="#EEE9FB",
         yellow="#E0B400", yellowsoft="#FFF6CC", gray="#56646D", graysoft="#F1F4F6", ink="#1C2B33", muted="#7C8992", red="#D23B3B", redsoft="#FBE5E5", green="#1E9E63", greensoft="#E3F4EA")

class SVG:
    def __init__(self, w, h, title):
        self.w, self.h, self.parts = w, h, []
        self.parts.append(f'<rect width="{w}" height="{h}" fill="#F7F9FB"/>')
        self.parts.append(f'<text x="28" y="40" font-family="{FONT}" font-size="22" font-weight="800" fill="{C["ink"]}">{html.escape(title)}</text>')
        self.parts.append(f'<text x="28" y="62" font-family="{FONT}" font-size="12" fill="{C["muted"]}">AltaTech DMP · v0.3 · 由 docs/diagrams/build.py 產生</text>')
    def group(self, x, y, w, h, title, fill="#FFFFFF", stroke=C["line"], dash=False):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.5" {"stroke-dasharray=\"6 4\"" if dash else ""}/>')
        self.parts.append(f'<text x="{x+14}" y="{y+22}" font-family="{FONT}" font-size="12.5" font-weight="800" fill="{C["muted"]}" letter-spacing=".5">{html.escape(title)}</text>')
    def box(self, x, y, w, h, title, lines=(), color="brand", small=False):
        fill, stroke = C.get(color + "soft", C["soft"]), C.get(color, C["brand"])
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        self.parts.append(f'<rect x="{x}" y="{y}" width="4" height="{h}" rx="2" fill="{stroke}"/>')
        ty = y + (18 if not small else 16)
        self.parts.append(f'<text x="{x+12}" y="{ty}" font-family="{FONT}" font-size="{13 if not small else 12}" font-weight="700" fill="{C["ink"]}">{html.escape(title)}</text>')
        for i, ln in enumerate(lines):
            self.parts.append(f'<text x="{x+12}" y="{ty+16+i*14}" font-family="{FONT}" font-size="11" fill="{C["gray"]}">{html.escape(ln)}</text>')
    def arrow(self, x1, y1, x2, y2, label="", color=None, dash=False, curve=False):
        col = color or C["muted"]
        if curve:
            mx = (x1 + x2) / 2
            d = f"M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"
        else:
            d = f"M{x1},{y1} L{x2},{y2}"
        self.parts.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.6" marker-end="url(#ah)" {"stroke-dasharray=\"5 4\"" if dash else ""}/>')
        if label:
            lx, ly = (x1 + x2) / 2, (y1 + y2) / 2 - 6
            self.parts.append(f'<text x="{lx}" y="{ly}" font-family="{FONT}" font-size="10.5" fill="{col}" text-anchor="middle">{html.escape(label)}</text>')
    def vline(self, x, y1, y2, label=""):
        self.arrow(x, y1, x, y2, label)
    def note(self, x, y, text, color=None):
        self.parts.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="11" fill="{color or C["muted"]}">{html.escape(text)}</text>')
    def legend(self, x, y, items):
        for i, (col, label) in enumerate(items):
            self.parts.append(f'<rect x="{x+i*150}" y="{y-10}" width="12" height="12" rx="3" fill="{C[col+"soft"]}" stroke="{C[col]}"/>')
            self.parts.append(f'<text x="{x+i*150+18}" y="{y}" font-family="{FONT}" font-size="11" fill="{C["gray"]}">{html.escape(label)}</text>')
    def save(self, name):
        defs = f'<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{C["muted"]}"/></marker></defs>'
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}">{defs}{"".join(self.parts)}</svg>'
        (OUT / name).write_text(svg, encoding="utf-8"); print("wrote", name)

# ------------------------------------------------------------------ 1. 系統架構圖（分層）
def system_architecture():
    s = SVG(1600, 1000, "系統架構圖：AltaTech DMP（分層視圖）")
    L = 28; W = 1230; colx = lambda i, n, pad=12: L + 14 + i * ((W - 28) // n) + pad * 0
    # 體驗層
    s.group(L, 80, W, 110, "體驗層  Experience")
    for i, (t, ls) in enumerate([("DMP Web（React + ECharts）", ["總覽／事件／漏斗／路徑／歸因／用戶列表", "標籤分群／即時觸發／數據整合／權限中心／日誌統計"]), ("AI 洞察助理", ["洞察串流、選擇題式問答", "Agents、工具目錄"]), ("外部系統與受眾推送", ["MA、客服、CRM、數倉（Webhook）", "GA4 受眾 → Google Ads 再行銷"]), ("外部 AI 客戶端", ["Claude Code / Cursor", "以 MCP 連工具閘道"])]):
        s.box(L + 14 + i * 300, 110, 285, 66, t, ls, "brand")
    # Agent 層
    s.group(L, 205, W, 120, "Agent 層  AI Agent Layer（docs/03、05）")
    for i, (t, ls, c) in enumerate([("Insight Agent Runtime", ["Planner → Executor → Verifier → Narrator", "anomaly-watch / funnel-doctor / churn-radar", "analyst-copilot / dashboard-reporter / strategy-loop"], "purple"), ("Agent Tool Gateway（MCP）", ["27 個工具：semantic.* / metric.query / funnel.run …", "權限（委託身份）、預算、脫敏、審計"], "purple"), ("Insight Store + Knowledge Pack", ["洞察物件（證據、信心度、回饋）", "字典、指標說明、行業知識、pgvector"], "purple"), ("LLM Gateway", ["企業閘道 → Claude / 本地模型", "golden evals、輸出守門"], "purple")]):
        s.box(L + 14 + i * 300, 235, 285, 78, t, ls, c)
    # 語意／服務層
    s.group(L, 340, W, 120, "語意與服務層  Semantic & Service（Python / FastAPI）")
    for i, (t, ls) in enumerate([("Semantic Layer", ["events / metrics / dimensions YAML", "查詢編譯器 → ClickHouse SQL", "成本估算、血緣、warnings"]), ("Analytics Query API", ["事件、漏斗、路徑、歸因、用戶列表", "留存、間隔、分布、LTV、Session"]), ("Segment & Tag / Profile / OneID API", ["規則、統計、SQL、即時、模型標籤", "Bitmap 分群；畫像；ID 解析／綁定／DSAR"]), ("Policy Engine（authorize）", ["模組權限 → 行級 → 欄位隱碼 → 審批", "UI、REST、MCP 共用；寫審計"])]):
        s.box(L + 14 + i * 300, 370, 285, 78, t, ls, "brand")
    # 計算層
    s.group(L, 475, W, 110, "計算層  Compute")
    for i, (t, ls, c) in enumerate([("Flink（SQL-first）", ["標準化、CDC 事件化（狀態機）", "OneID 即時合併、Session 化", "CEP 即時規則、即時標籤"], "teal"), ("dbt + ClickHouse SQL", ["日批標籤、L1/L2 聚合、觸點表", "ID 日批校正、分群快照"], "teal"), ("Spark（輔助）", ["BigQuery 匯入（Storage Read API）", "模型標籤（流失分、聚類）"], "teal"), ("Airflow", ["排程、依賴、回補", "DAG 由建模產物生成"], "teal")]):
        s.box(L + 14 + i * 300, 505, 285, 68, t, ls, c)
    # 儲存層
    s.group(L, 600, W, 110, "儲存層  Storage（五個有狀態服務）")
    for i, (t, ls, c) in enumerate([("Kafka（KRaft）", ["raw.* 原文、dmp.events、dmp.triggers", "dmp.audit、dmp.insights"], "yellow"), ("ClickHouse（熱 13 個月）", ["events / users / items / id_map", "user_tags Bitmap、MV L1–L3、audit_log"], "yellow"), ("PostgreSQL + pgvector", ["控制面、字典、角色、審批", "Insight Store、Knowledge Pack"], "yellow"), ("Valkey ＋ GCS / 物件儲存", ["OneID 快取、即時標籤、結果快取", "Iceberg 冷層、原始事件、備份、WORM"], "yellow")]):
        s.box(L + 14 + i * 300, 630, 285, 68, t, ls, c)
    # 接入層
    s.group(L, 725, W, 110, "接入層  Ingestion（docs/06 可視化建模）")
    for i, (t, ls, c) in enumerate([("Collector（Go）", ["/v1/events、/sa（App／內部系統直送）", "校驗、個資閘門、冪等、寫 Kafka"], "green"), ("GA360 Importer", ["BigQuery 日批 events_（權威）", "串流 events_intraday_（分鐘級）"], "green"), ("CDC：Debezium / Flink CDC", ["MSSQL 交易主機、PostgreSQL CRM", "Outbox → raw.trade_cdc"], "green"), ("批次整合：dlt / SeaTunnel", ["神策 Kafka 訂閱 + 查詢 API", "檔案、Kafka、GCS"], "green")]):
        s.box(L + 14 + i * 300, 755, 285, 68, t, ls, c)
    # 來源
    s.group(L, 850, W, 110, "資料來源  Sources")
    for i, (t, ls) in enumerate([("GA360（Web / App 埋點）", ["gtag / Firebase → BigQuery 匯出"]), ("證券交易系統", ["委託、成交、出入金、開戶、基金"]), ("CRM / 客戶主檔 / 營業員", ["客戶、帳戶、分公司、商品"]), ("MA / 神策歷史", ["推播；神策事件一次性匯入"])]):
        s.box(L + 14 + i * 300, 880, 285, 56, t, ls, "gray")
    # 縱向流向
    for y1, y2 in [(936, 823), (755, 698), (630, 573), (505, 448), (370, 313), (235, 176)]:
        pass
    for x in (L + 14 + 142, L + 14 + 300 + 142, L + 14 + 600 + 142, L + 14 + 900 + 142):
        s.arrow(x, 850, x, 836)   # 來源 → 接入（視覺上：接入層在上）
    s.note(L + 14, 975, "縱向：資料由下往上流（來源 → 接入 → 儲存 → 計算 → 語意／服務 → Agent → 體驗）；查詢由上往下。橫向支撐見右側。")
    # 橫向支撐
    X = L + W + 20; GW = 1600 - X - 28
    s.group(X, 80, GW, 290, "安全與治理  Cross-cutting")
    s.box(X + 12, 110, GW - 24, 60, "身分與權限", ["Keycloak / SSO、RBAC + 範圍", "自訂角色、審批、臨時提權"], "red")
    s.box(X + 12, 178, GW - 24, 60, "資料保護", ["L0–L4 隱碼、HMAC/FPE 代碼化", "KMS/HSM、CMEK、K 匿名"], "red")
    s.box(X + 12, 246, GW - 24, 60, "審計與法遵", ["audit_log → WORM、日誌統計", "DSAR、金管會委外／個資法"], "red")
    s.group(X, 385, GW, 200, "觀測  Observability")
    s.box(X + 12, 415, GW - 24, 60, "Prometheus + OpenTelemetry", ["指標、追蹤、資料源延遲", "查詢 P95、agent 預算"], "gray")
    s.box(X + 12, 483, GW - 24, 60, "Grafana OSS 看板 / 告警", ["平台、資料品質、審計統計", "Cloud Logging / OpenSearch 日誌"], "gray")
    s.group(X, 600, GW, 230, "部署  Delivery（docs/07）")
    s.box(X + 12, 630, GW - 24, 56, "Docker 部署套件", ["UBI 9 / distroless 映像", "cosign 簽章、SBOM、Trivy"], "gray")
    s.box(X + 12, 694, GW - 24, 56, "Helm umbrella + Operators", ["Strimzi、Flink、ClickHouse、CNPG", "values：機房／GCP／PoC"], "gray")
    s.box(X + 12, 758, GW - 24, 56, "GKE（asia-east1）/ RKE2", ["OpenTofu：VPC、GKE、Cloud SQL、GCS", "Workload Identity、Binary Auth"], "gray")
    s.group(X, 845, GW, 115, "供應鏈  Supply chain（docs/11）")
    s.box(X + 12, 875, GW - 24, 70, "全部 OSI 寬鬆授權 + LTS", ["Renovate、Semgrep、gitleaks", "Syft / Grype、授權白名單 CI", "修補 SLA：Critical 7 天"], "gray")
    s.legend(L + 14, 990 - 2, [("brand", "體驗／服務"), ("purple", "AI agent"), ("teal", "計算"), ("yellow", "儲存"), ("green", "接入"), ("red", "安全"), ("gray", "來源／支撐")])
    s.save("system-architecture.svg")

# ------------------------------------------------------------------ 2. 元件組成／部署圖
def component_deployment():
    s = SVG(1600, 980, "元件組成圖：GKE 部署視圖（機房 RKE2 同構）")
    # 外部來源（左）
    s.group(28, 80, 250, 520, "外部來源  External")
    for i, (t, ls) in enumerate([("GA360 / BigQuery", ["Storage Read API", "Private Google Access"]), ("GA360 串流匯出", ["events_intraday_", "分鐘級"]), ("交易主機 MSSQL", ["Debezium CDC / Outbox"]), ("CRM PostgreSQL", ["pgoutput CDC + 日批"]), ("神策（歷史，一次性匯入）", ["查詢 API / Parquet"]), ("LLM Gateway", ["企業閘道；agent 不持金鑰"])]):
        s.box(42, 110 + i * 80, 222, 64, t, ls, "gray", small=True)
    # GKE
    s.group(300, 80, 960, 770, "GKE 區域叢集  asia-east1（私有節點、Workload Identity、Binary Authorization）", fill="#FBFCFE")
    pools = [
        ("node pool: ingest", 316, [("dmp-ingest：Collector ×2–3（Go, HPA）", "Deployment；/v1/events、/sa"), ("GA360 Importer（dmp-batch）", "BigQuery Storage Read API；日批 + 串流"), ("KafkaConnect：Debezium CDC", "Strimzi，官方 connector 映像")], "green"),
        ("node pool: stream", 316, [("dmp-flink-jobs：Flink JM ×1–2", "Flink K8s Operator，一個 FlinkDeployment"), ("Flink TaskManager ×2–16", "normalize / eventize / oneid / session / rules"), ("Checkpoint → GCS", "exactly-once")], "teal"),
        ("node pool: data", 316, [("Kafka ×3（KRaft, Strimzi）", "StatefulSet，PVC 500 GB–2 TB"), ("dmp-batch：Airflow + dbt + Spark + dlt", "一個映像一個 Pod；DAG 由建模產物生成"), ("Spark on K8s（Spot，臨時）", "舊資料導入、BigQuery 大批匯入")], "yellow"),
        ("node pool: olap", 316, [("ClickHouse 2 shard × 2 replica", "Altinity Operator, c3-highmem, Hyperdisk 8 TB"), ("ClickHouse Keeper ×3", "協調"), ("冷層 → GCS（S3 disk）", "TTL 13 個月")], "yellow"),
        ("node pool: app", 316, [("dmp-core：API / 語意層 / 分群 / OneID / 同步 / 推送", "一個映像；api ×2–3（online）、worker ×1–2（batch）"), ("dmp-agent：Tool Gateway（MCP）+ Runtime", "Deployment ×0–2；第二階段再開"), ("dmp-web（nginx-unprivileged）", "Deployment ×2")], "brand"),
    ]
    for i, (name, _, items, col) in enumerate(pools):
        x = 316 + (i % 3) * 312; y = 110 + (i // 3) * 250
        s.group(x, y, 296, 232, name, fill="#FFFFFF", dash=True)
        for j, (t, sub) in enumerate(items):
            s.box(x + 10, y + 34 + j * 64, 276, 56, t, [sub], col, small=True)
    # cross-cutting in cluster
    s.group(316 + 2 * 312, 110 + 250, 296, 232, "叢集共用  Platform", fill="#FFFFFF", dash=True)
    for j, (t, sub, col) in enumerate([("Keycloak（SSO / OIDC）", "身分；GCP 可用 Cloud Identity + IAP", "red"), ("Prometheus + OTel Collector", "Managed Prometheus 可替代", "gray"), ("Grafana OSS", "看板與告警（內部使用）", "gray")]):
        s.box(316 + 2 * 312 + 10, 110 + 250 + 34 + j * 64, 276, 56, t, [sub], col, small=True)
    # ingress
    s.box(316, 620, 930, 50, "Ingress（GCE）+ Cloud Armor WAF + IAP", ["/v1/events、/sa、/ga4（Collector）；/api（服務）；/（Web）；/mcp（工具閘道）"], "brand", small=True)
    s.box(316, 684, 930, 50, "NetworkPolicy 預設拒絕 · Pod Security restricted · 非 root / 唯讀檔案系統 · Secret Manager CSI", [], "red", small=True)
    s.box(316, 748, 930, 50, "Helm umbrella chart（values-gcp-6n / 3n-xl / 3n …）· 自建映像 6 個：ingest、core、agent、batch、flink-jobs、web · UBI 9 / distroless · cosign 簽章", [], "gray", small=True)
    s.note(316, 830, "機房：RKE2 + CloudNativePG（PostgreSQL）+ Valkey + SeaweedFS 或 Ceph + OpenBao，values.yaml 即預設。")
    # managed services (right)
    s.group(1280, 80, 292, 520, "GCP 託管服務  Managed")
    for i, (t, ls) in enumerate([("Cloud SQL for PostgreSQL 16", ["HA、pgvector、CMEK", "控制面、Insight Store"]), ("Memorystore for Valkey", ["OneID 快取、即時標籤"]), ("Cloud Storage（CMEK, Object Lock）", ["Iceberg 冷層、原始事件", "備份、審計 WORM"]), ("Cloud KMS / Secret Manager", ["HSM 層級金鑰、HMAC/FPE 金鑰輪替"]), ("Artifact Registry + Artifact Analysis", ["映像、SBOM、持續弱掃"]), ("Cloud Logging / Monitoring", ["日誌、稽核紀錄匯出"])]):
        s.box(1294, 110 + i * 80, 264, 64, t, ls, "gray", small=True)
    # downstream (right bottom)
    s.group(1280, 620, 292, 230, "下游系統  Consumers")
    for i, (t, ls) in enumerate([("內部系統 Webhook / Kafka", ["MA、CRM、數倉；受眾包與標籤"]), ("GCS Parquet（數倉）", ["受眾包／標籤全量與增量"]), ("數倉", ["GCS Parquet、Iceberg 雙向映射"])]):
        s.box(1294, 650 + i * 64, 264, 56, t, ls, "brand", small=True)
    # arrows
    s.arrow(264, 300, 316, 300, ""); s.note(268, 292, "CDC")
    s.arrow(264, 190, 316, 190, ""); s.note(268, 182, "事件")
    s.arrow(1246, 300, 1294, 300, "", dash=True)
    s.arrow(1246, 700, 1294, 700, ""); s.note(1248, 692, "Kafka/API")
    s.note(28, 900, "有狀態服務只有 5 個：Kafka、ClickHouse、PostgreSQL、Valkey、物件儲存；其餘皆為無狀態 Deployment，可水平擴展。")
    s.note(28, 920, "檔位：A 6 台（olap ×2 + general ×4）、D 3 台大機型（c3-highmem-44 ×3 同構）、C 3 台測試；同一套 Helm 只換 values，見 docs/13。")
    s.legend(28, 960, [("green", "接入"), ("teal", "串流"), ("yellow", "儲存／批次"), ("brand", "服務／前端"), ("red", "安全"), ("gray", "託管／支撐")])
    s.save("component-deployment.svg")

# ------------------------------------------------------------------ 3. 數據流圖
def data_flow():
    s = SVG(1600, 1000, "數據流圖：從來源到洞察與行動")
    # columns
    cols = [("① 來源", 28), ("② 接入／原文", 290), ("③ 串流處理（Flink）", 552), ("④ 儲存與聚合", 814), ("⑤ 語意／服務／Agent", 1076), ("⑥ 消費", 1338)]
    for t, x in cols:
        s.parts.append(f'<text x="{x}" y="92" font-family="{FONT}" font-size="13" font-weight="800" fill="{C["brand"]}">{html.escape(t)}</text>')
        s.parts.append(f'<line x1="{x}" y1="100" x2="{x+234}" y2="100" stroke="{C["line"]}" stroke-width="2"/>')
    # column 1 sources
    src = [("GA360 Web/App 埋點", ["gtag / Firebase → BigQuery 匯出"]), ("GA360 串流匯出", ["events_intraday_，分鐘級"]), ("數據中台／CDP（委託/成交/出入金）", ["事實表：批次導入或 Kafka"]), ("CRM / 開戶 / 基金", ["PostgreSQL、API"]), ("神策歷史（一次性匯入）", ["查詢 API / Parquet 匯出"]), ("MA", ["推播"])]
    for i, (t, ls) in enumerate(src): s.box(28, 115 + i * 78, 234, 60, t, ls, "gray", small=True)
    # column 2 ingestion
    ing = [("BigQuery Importer（日批/Fresh）", ["Storage Read API → Iceberg raw"]), ("Collector /ga4 /v1/events /sa", ["校驗、禁止欄位、event_id 冪等"]), ("數據中台批次導入（Trino）", ["15 分鐘增量 → raw.dwh_trade"]), ("dlt / SeaTunnel 批次", ["raw.crm、profile_set"]), ("神策歷史匯入（一次性）", ["協議 1:1 映射 → Iceberg"]), ("Kafka raw.* topics", ["原文保留 7–30 天；可回放"])]
    for i, (t, ls) in enumerate(ing): s.box(290, 115 + i * 78, 234, 60, t, ls, "green", small=True)
    # column 3 flink
    fl = [("normalize", ["GA4 → 統一事件（ga. 前綴）", "時區、去重、遲到標記"]), ("eventize（狀態機）", ["NEW→order_submit、F→order_filled…", "敏感欄位代碼化 HMAC/FPE"]), ("oneid-resolve", ["identities → one_id / one_id_uint", "優先級合併、共用裝置限制"]), ("sessionize", ["30 分鐘閒置切 Session"]), ("rt-rules（CEP）/ rt-tags", ["MATCH_RECOGNIZE → dmp.triggers", "近 N 分鐘標籤 → Valkey"]), ("audit / insights topics", ["agent.tool_called、role.changed"])]
    for i, (t, ls) in enumerate(fl): s.box(552, 115 + i * 78, 234, 60, t, ls, "teal", small=True)
    # column 4 storage
    st = [("ClickHouse L0 events", ["分片 by one_id；13 個月；SAMPLE"]), ("MV：L1 user_event_daily、L2 daily_agg", ["90% 查詢不碰明細"]), ("L3 touchpoints、L4 user_tags Bitmap", ["歸因觸點；標籤值→RoaringBitmap"]), ("users / items / id_map", ["畫像、多主體、ID 版本"]), ("Iceberg on GCS（冷、原始）", ["法定年限、DSAR 刪除、雙向映射"]), ("PostgreSQL + pgvector", ["字典、角色、審批、Insight Store"])]
    for i, (t, ls) in enumerate(st): s.box(814, 115 + i * 78, 234, 60, t, ls, "yellow", small=True)
    # column 5 service/agent
    sv = [("Semantic Compiler", ["語意查詢 JSON → SQL + 成本 + 血緣"]), ("Policy Engine", ["模組→行級→欄位隱碼→審批→審計"]), ("Analytics / Segment / Profile API", ["事件、漏斗、路徑、歸因、用戶列表、標籤"]), ("Tool Gateway（MCP）", ["27 工具；委託身份；預算；脫敏"]), ("Insight Agents", ["anomaly-watch、funnel-doctor、", "churn-radar、analyst-copilot…"]), ("dbt / Airflow 日批", ["標籤、聚合、ID 校正、快照"])]
    for i, (t, ls) in enumerate(sv): s.box(1076, 115 + i * 78, 234, 60, t, ls, "purple" if i in (3, 4) else "brand", small=True)
    # column 6 consumers
    cs = [("DMP Web / AI 洞察助理", ["看板、分析、問答、權限中心"]), ("Webhook / Kafka → MA / CRM", ["受眾包增量推送（簽章、重試）"]), ("GCS Parquet → 數倉", ["受眾包全量／增量 manifest"]), ("Grafana 看板", ["資料品質、效能、審計統計"]), ("數倉", ["Iceberg 雙向映射讀取"]), ("稽核 / 法遵", ["audit_log WORM、DSAR 證明"])]
    for i, (t, ls) in enumerate(cs): s.box(1338, 115 + i * 78, 234, 60, t, ls, "gray", small=True)
    # arrows source→ingest
    for i, j in [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 1)]:
        s.arrow(262, 145 + i * 78, 290, 145 + j * 78, curve=True)
    for i in range(5): s.arrow(524, 145 + i * 78, 552, 145 + (0 if i in (0, 1) else 1 if i == 2 else 2 if i in (3, 4) else 0) * 78, curve=True)
    s.arrow(524, 145 + 5 * 78, 552, 145 + 5 * 78, "")
    # flink chain
    for i in range(3): s.arrow(669, 175 + i * 78, 669, 193 + i * 78, color=C["teal"])
    s.arrow(786, 145 + 2 * 78, 814, 145, "", curve=True)
    s.parts.append(f'<text x="800" y="111" font-family="{FONT}" font-size="10.5" font-weight="700" fill="{C["teal"]}" text-anchor="middle">dmp.events →</text>')
    s.arrow(786, 145 + 3 * 78, 814, 145, curve=True)
    s.parts.append(f'<text x="556" y="{115 + 4 * 78 + 71}" font-family="{FONT}" font-size="10" font-weight="700" fill="{C["teal"]}">↳ dmp.triggers（秒級）→ ⑥ MA / 內部系統</text>')
    s.arrow(786, 145 + 5 * 78, 814, 145 + 5 * 78, "")
    # storage internal
    for i in range(2): s.arrow(931, 175 + i * 78, 931, 193 + i * 78, color=C["yellow"])
    s.arrow(1048, 145 + 5 * 78, 1076, 145 + 5 * 78, "")
    # storage → services
    for i in (0, 1, 2, 3): s.arrow(1048, 145 + i * 78, 1076, 145, curve=True)
    s.arrow(1048, 145 + 5 * 78, 1076, 145 + 4 * 78, "", curve=True)
    for i in range(4): s.arrow(1193, 175 + i * 78, 1193, 193 + i * 78, color=C["brand"])
    # services → consumers
    s.arrow(1310, 145 + 2 * 78, 1338, 145, curve=True)
    s.arrow(1310, 145 + 3 * 78, 1338, 145 + 1 * 78, "", curve=True)
    s.arrow(1310, 145 + 4 * 78, 1338, 145 + 1 * 78, "", curve=True)
    s.arrow(1310, 145 + 1 * 78, 1338, 145 + 5 * 78, "", curve=True, dash=True)
    s.parts.append(f'<text x="818" y="{115 + 4 * 78 + 71}" font-family="{FONT}" font-size="10" font-weight="700" fill="{C["yellow"]}">↳ Iceberg 雙向映射 → ⑥ 數倉</text>')
    s.parts.append(f'<text x="1080" y="{115 + 1 * 78 + 71}" font-family="{FONT}" font-size="10" fill="{C["muted"]}">↳ 每次授權寫 audit → ⑥ 稽核 / Grafana</text>')
    # notes
    s.group(28, 610, 1544, 150, "關鍵規則")
    notes = ["• 同一事件模型承接 GA 行為事件與交易事件；event_id 冪等、ReplacingMergeTree 最終去重；日批（GA360 events_）覆蓋串流（events_intraday_）。",
             "• 個資在接入層即擋下：L4 欄位不進平台；客戶 ID / 帳號在 Flink 事件化階段 HMAC / FPE 代碼化；分析倉只有代碼化 ID。",
             "• OneID：稠密整數 one_id_uint 供 RoaringBitmap；事件表以 one_id 分片，漏斗／路徑／歸因／用戶列表無跨片 shuffle。",
             "• 人與 agent 走同一條語意層 → Policy Engine → ClickHouse；agent 以委託身份執行，工具回傳先套隱碼，數字必須來自工具。",
             "• 洞察是一級資料物件（PostgreSQL + pgvector）：證據可重跑、信心度、回饋；策略提議（分群／規則）需審批後才由 campaign.execute 推送。",
             "• 所有讀寫（含 agent.tool_called、role.changed、mask.revealed）→ dmp.audit → ClickHouse audit_log + GCS WORM → Grafana 日誌統計看板。"]
    for i, n in enumerate(notes): s.note(42, 652 + i * 18, n, C["ink"])
    s.group(28, 780, 1544, 190, "延遲與 SLA（docs/02、09）")
    sla = [("行為事件可查", "P95 < 15 分鐘", "GA360 串流匯出 → Importer → Flink → ClickHouse"), ("交易事件可查", "P95 < 20 分鐘", "數據中台批次 → Flink 事件化 → ClickHouse"), ("即時觸發", "P95 < 5 秒", "Flink CEP → dmp.triggers"), ("30 天漏斗（1 億事件）", "P95 < 10 秒", "Bitmap 預篩 + windowFunnel"), ("用戶列表人數預估", "< 1 秒", "Bitmap 交併差"), ("標籤全量日批", "< 60 分鐘", "L1 聚合 → Bitmap 版本切換"), ("agent 問答（3–5 次工具）", "P95 < 40 秒", "選擇題式配置 + 交叉驗證")]
    for i, (a, b, c) in enumerate(sla):
        x = 42 + (i % 4) * 382; y = 810 + (i // 4) * 72
        s.box(x, y, 366, 60, f"{a}：{b}", [c], "brand", small=True)
    s.save("data-flow.svg")

# ------------------------------------------------------------------ 4. 元件模組圖（功能模組與模組間資料流）
def module_map():
    s = SVG(1600, 1000, "元件模組圖：功能模組、負責的資料與模組間關係")
    s.group(28, 80, 460, 300, "接入與治理  Ingest & Governance", fill="#FBFCFE")
    for i, (t, ls, c) in enumerate([("數據整合（可視化建模）", ["連線（鑑權／密鑰參照／測試）→ 選表 → 映射 → 清洗 → 發佈", "MSSQL／PG／BigQuery／Kafka／GCS／神策一次性"], "green"),
                                     ("元事件管理", ["事件字典、屬性字典、虛擬事件、隔離區", "上線檢查、變更單、L4 拒絕"], "green"),
                                     ("用戶表管理 ／ 維度表與字典", ["用戶屬性（來源／更新／敏感／可篩選）、OneID 歸戶規則", "維度表以屬性關聯補充分析維度；維度字典給枚舉顯示名"], "green")]):
        s.box(42, 112 + i * 86, 432, 76, t, ls, c, small=True)
    s.group(516, 80, 560, 300, "核心資料  Core Data（ClickHouse / PostgreSQL / Valkey）", fill="#FBFCFE")
    for i, (t, ls, c) in enumerate([("events（L0 明細）+ MV L1–L3", ["project／event_date／event／one_id／time 排序", "日彙總、常用分組、cohort 預聚合；13 個月熱／冷層"], "yellow"),
                                     ("users ／ dim_* ／ id_map", ["用戶屬性 JSON、維度表（dictGet 關聯屬性）、OneID 映射", "touchpoints（歸因日批）、session 序列（路徑）"], "yellow"),
                                     ("user_tags Bitmap ／ segments ／ insights", ["標籤值 RoaringBitmap 版本 7 份；分群交並差", "Insight Store（pgvector）、audit_log（WORM）"], "yellow")]):
        s.box(530, 112 + i * 86, 532, 76, t, ls, c, small=True)
    s.group(1104, 80, 468, 300, "橫切  Cross-cutting", fill="#FBFCFE")
    for i, (t, ls, c) in enumerate([("語意層 + 查詢編譯器", ["events／metrics／dimensions／items／user_properties YAML", "UI、API、agent 工具同一條編譯路徑"], "brand"),
                                     ("Policy Engine（角色／模組／行級／隱碼／審批）", ["authorize() → WHERE 條件 + 遮罩欄位 + 審批需求", "使用者指派（範圍、到期）、K 匿名、break-glass"], "red"),
                                     ("審計與觀測", ["audit_log → 日誌統計；query_log → 查詢效能", "Prometheus／Grafana：資料流、批次、儲存、告警"], "gray")]):
        s.box(1118, 112 + i * 86, 440, 76, t, ls, c, small=True)
    s.group(28, 410, 1048, 300, "分析與營運  Analytics & Operations（dmp-core）", fill="#FBFCFE")
    mods = [("事件分析", ["多指標×類型、篩選、分組", "粒度、對比、圖表"]), ("漏斗分析", ["步驟＋每步篩選、窗口", "關聯屬性、分組、趨勢"]), ("留存分析", ["初始／後續行為、當期／區間", "留存／流失、分組"]), ("行為路徑", ["起始／結束、事件集拆分", "會話、桑基圖、下鑽"]),
            ("歸因分析", ["目標＋觸點、五模型", "窗口、分組、轉化路徑"]), ("用戶列表 ／ 細查", ["條件→Bitmap 估數→分頁", "細查需理由；來源條件"]), ("標籤與分群", ["規則／SQL／統計／模型", "日批 Bitmap、審批"]), ("即時觸發 ／ 受眾推送", ["Flink 規則；Webhook 簽章", "Kafka／GCS／GA4 受眾"])]
    for i, (t, ls) in enumerate(mods):
        x = 42 + (i % 4) * 258; y = 442 + (i // 4) * 126
        s.box(x, y, 244, 110, t, ls, "brand", small=True)
    s.group(1104, 410, 468, 300, "AI 洞察  Insight Agent（dmp-agent）", fill="#FBFCFE")
    for i, (t, ls, c) in enumerate([("Tool Gateway（MCP）", ["tools.yaml：權限／預算／脫敏／審計", "metric.query、funnel.run、analysis.configure…"], "purple"),
                                     ("Agent Runtime + 排程 agent", ["異常偵測、漏斗診斷、流失雷達", "選擇題式問答、可校驗回答"], "purple"),
                                     ("洞察物件 / 行動提議", ["證據可重跑、信心度、回饋", "提議分群／規則 → 審批後生效"], "purple")]):
        s.box(1118, 442 + i * 86, 440, 76, t, ls, c, small=True)
    s.arrow(258, 388, 258, 410, "登記 → 語意層 / 事件化規則", C["green"])
    s.arrow(796, 388, 796, 410, "查詢（經語意層＋Policy）", C["brand"])
    s.arrow(1104, 262, 1076, 262, "", C["red"], dash=True); s.note(1118, 398, "← Policy Engine：每次查詢（UI／API／agent）都套用行級與遮罩", C["red"])
    s.arrow(1104, 560, 1076, 560, "", C["purple"], dash=True); s.note(1118, 728, "← agent 工具與分析模組共用同一查詢編譯器與權限", C["purple"])
    s.arrow(1338, 388, 1338, 410, "工具目錄來自語意層", C["purple"])
    s.group(28, 740, 1544, 150, "模組間資料流（摘要）", fill="#FFFFFF")
    rows = ["接入：來源 → Collector／KafkaConnect → Kafka raw.* → Flink（normalize → eventize → OneID → session）→ ClickHouse events + MV；未登記事件 → 隔離區 → 元事件管理登記後放行。",
            "分析：UI 設定 → 語意層編譯（事件字典／屬性／Item 維度）→ Policy Engine 加行級與遮罩 → ClickHouse（MV 優先）→ 結果快取 → 審計；agent 工具走完全相同的路徑。",
            "營運：標籤日批（dbt）→ user_tags Bitmap → 分群（交並差）→ 審批 → 名單推送（Webhook／Kafka／GCS）→ deliveries；即時觸發由 Flink 規則直接打 MA。",
            "治理：用戶表／維度表／OneID 規則變更 → 語意層 dimensions／items YAML → 影響範圍（標籤、分群、agent 工具）→ 變更單審批 → 審計；資料流與效能頁從 Prometheus／query_log 供數。"]
    for i, r in enumerate(rows): s.note(44, 772 + i * 26, r, C["ink"])
    s.legend(28, 960, [("green", "接入／治理"), ("yellow", "核心資料"), ("brand", "分析／營運"), ("purple", "AI 洞察"), ("red", "權限"), ("gray", "審計／觀測")])
    s.save("module-map.svg")

if __name__ == "__main__":
    system_architecture(); component_deployment(); data_flow(); module_map()
