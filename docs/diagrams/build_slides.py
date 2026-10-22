"""提案簡報用的簡化架構頁（16:9，1920×1080）。執行 python3 docs/diagrams/build_slides.py 重新產生。
原則：一頁一個訊息、每頁 ≤ 12 個方塊、字級 ≥ 20px、AltaTech 品牌色。"""
from __future__ import annotations
from pathlib import Path
import html

OUT = Path(__file__).resolve().parent / "slides"
FONT = "'Plus Jakarta Sans','Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif"
C = dict(brand="#2C73EA", brandsoft="#E9F0FD", brandline="#B9CFF5", teal="#12B5A8", tealsoft="#E3F7F5", purple="#7C4DD6", purplesoft="#EEE9FB",
         yellow="#E0B400", yellowsoft="#FFF6CC", gray="#56646D", graysoft="#F1F4F6", ink="#1C2B33", muted="#7C8992", red="#D23B3B", redsoft="#FBE5E5", white="#FFFFFF")
W, H = 1920, 1080

class Slide:
    def __init__(self, title, subtitle="", page=1):
        self.p = [f'<rect width="{W}" height="{H}" fill="{C["white"]}"/>']
        self.p.append(f'<rect x="0" y="0" width="{W}" height="10" fill="{C["brand"]}"/>')
        self.p.append(f'<text x="96" y="110" font-family="{FONT}" font-size="44" font-weight="800" fill="{C["ink"]}">{html.escape(title)}</text>')
        if subtitle:
            self.p.append(f'<text x="96" y="156" font-family="{FONT}" font-size="22" fill="{C["muted"]}">{html.escape(subtitle)}</text>')
        # footer：品牌標記 + 字標 + 頁碼
        self.p.append(f'<g transform="translate(96,1010) scale(0.55)"><path d="M10 26V10h16M10 10l14 14" stroke="#21DCCE" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M38 10h16v16" stroke="#B57BEA" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 38v16h16" stroke="#FECF00" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M54 38v16H38M54 54L40 40" stroke="#2C73EA" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>')
        self.p.append(f'<text x="1824" y="1040" font-family="{FONT}" font-size="22" font-weight="800" fill="{C["brand"]}" text-anchor="end" letter-spacing="1">ALTA TECH<tspan font-size="12" dy="-10">LTD</tspan></text>')
        self.p.append(f'<text x="150" y="1040" font-family="{FONT}" font-size="16" fill="{C["muted"]}">AltaTech DMP 數位軌跡／客戶數據中台 · {page}</text>')
    def rect(self, x, y, w, h, fill, stroke, r=16, dash=False, sw=2):
        self.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {"stroke-dasharray=\"10 7\"" if dash else ""}/>')
    def text(self, x, y, s, size=20, weight=400, fill=None, anchor="start"):
        self.p.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" fill="{fill or C["ink"]}" text-anchor="{anchor}">{html.escape(s)}</text>')
    def box(self, x, y, w, h, title, lines=(), color="brand", tsize=24, lsize=18, center=False):
        self.rect(x, y, w, h, C[color + "soft"], C[color], 14)
        self.p.append(f'<rect x="{x}" y="{y}" width="8" height="{h}" rx="4" fill="{C[color]}"/>')
        tx = x + w / 2 if center else x + 24
        anc = "middle" if center else "start"
        self.text(tx, y + 38, title, tsize, 800, C["ink"], anc)
        for i, ln in enumerate(lines):
            self.text(tx, y + 38 + 30 + i * (lsize + 8), ln, lsize, 400, C["gray"], anc)
    def pill(self, x, y, s, color="brand", size=18):
        w = len(s) * size * 0.95 + 28
        self.rect(x, y - size - 6, w, size + 16, C[color + "soft"], C[color], 999, sw=1.5)
        self.text(x + w / 2, y, s, size, 700, C[color], "middle")
        return w
    def arrow(self, x1, y1, x2, y2, color=None, label="", sw=3):
        col = color or C["muted"]
        self.p.append(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{col}" stroke-width="{sw}" fill="none" marker-end="url(#ah)"/>')
        if label: self.text((x1 + x2) / 2, (y1 + y2) / 2 - 10, label, 17, 600, col, "middle")
    def chevron(self, x, y, w, h, label, color="brand", first=False):
        d = f"M{x},{y} L{x+w-18},{y} L{x+w},{y+h/2} L{x+w-18},{y+h} L{x},{y+h} {'' if first else f'L{x+18},{y+h/2}'} Z"
        self.p.append(f'<path d="{d}" fill="{C[color]}" opacity=".92"/>')
        self.text(x + w / 2 + (0 if first else 8), y + h / 2 + 9, label, 24, 800, "#FFFFFF", "middle")
    def circle_num(self, x, y, n, color="brand"):
        self.p.append(f'<circle cx="{x}" cy="{y}" r="22" fill="{C[color]}"/>')
        self.text(x, y + 9, str(n), 24, 800, "#FFFFFF", "middle")
    def save(self, name):
        defs = f'<defs><marker id="ah" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{C["muted"]}"/></marker></defs>'
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{defs}{"".join(self.p)}</svg>'
        OUT.mkdir(exist_ok=True); (OUT / name).write_text(svg, encoding="utf-8"); print("wrote", name)

# ------------------------------------------------------------------ 1. 系統架構總覽
def s1():
    s = Slide("系統架構：一個中台承接 GA360 行為軌跡與證券交易事件", "資料打通 → 分析與標籤 → AI 洞察與行動；私有化／GCP 皆可部署，全開源寬鬆授權", 1)
    # 來源列
    s.text(96, 215, "資料來源", 20, 800, C["muted"])
    for i, (t, ls) in enumerate([("GA360 埋點", ["Web / App 行為、BigQuery 日批＋串流匯出"]), ("證券交易系統", ["委託、成交、出入金、開戶、基金"]), ("CRM / MA / 舊平台歷史", ["客戶主檔、推播；舊平台事件一次性匯入"])]):
        s.box(96 + i * 400, 230, 376, 96, t, ls, "gray", 24, 18)
    # 中台
    s.rect(96, 370, 1328, 470, "#FBFCFE", C["brandline"], 20)
    s.text(120, 406, "AltaTech DMP 數位軌跡／客戶數據中台", 26, 800, C["brand"])
    pillars = [("數據整合與 OneID", ["可視化建模、CDC / 批次", "客戶・帳戶・裝置打通", "個資代碼化（HMAC/FPE）"], "teal"),
               ("分析引擎", ["事件、漏斗、行為路徑", "歸因、用戶列表、留存", "百億事件秒級查詢"], "brand"),
               ("標籤・分群・受眾推送", ["1,000+ 標籤每日運算、Bitmap 分群", "即時規則；名單推送 Webhook / Kafka / GCS", "推送到 MA / CRM / 數倉（內部系統）"], "yellow"),
               ("AI 洞察 Agent", ["指標搜尋、選擇題式問答", "異常偵測、漏斗診斷、流失預警", "策略 → 審批 → 執行 → 復盤"], "purple")]
    for i, (t, ls, c) in enumerate(pillars):
        s.box(120 + i * 326, 430, 302, 190, t, ls, c, 24, 18)
    # 基礎
    s.rect(120, 650, 1280, 64, C["graysoft"], C["gray"], 12)
    s.text(144, 690, "基礎：Kafka · Flink（SQL-first）· ClickHouse · PostgreSQL · Valkey · GCS　｜　只有五個有狀態服務，全為 LTS，有 Operator 或 GCP 託管版", 19, 600, C["ink"])
    s.rect(120, 730, 1280, 90, C["redsoft"], C["red"], 12)
    s.text(144, 764, "企業級治理：角色／模組／數據權限、欄位隱碼（L0–L4）、審批流程、審計與日誌統計；人與 AI 走同一套 Policy Engine", 20, 700, C["ink"])
    s.text(144, 796, "法遵：資料境內（asia-east1 或機房）、KMS/HSM 金鑰、WORM 審計、DSAR；對應金管會委外辦法與個資法", 18, 400, C["gray"])
    # 應用
    s.text(1480, 215, "應用與輸出", 20, 800, C["muted"])
    for i, (t, ls, c) in enumerate([("DMP Web", ["看板、分析、用戶列表", "權限中心、日誌統計"], "brand"), ("AI 洞察助理", ["問答、洞察串流", "外部 MCP 客戶端"], "purple"), ("內部系統 Webhook・MA・CRM・數倉", ["受眾包／標籤簽章推送、即時觸發", "重試、DLQ、審計"], "teal"), ("GA4 受眾 → Google Ads 再行銷", ["匿名 client_id 回流，不傳個資", "需廣告再行銷同意；T+1 對帳"], "gray")]):
        s.box(1480, 230 + i * 150, 344, 128, t, ls, c, 22, 17)
    # 箭頭
    for i in range(3): s.arrow(284 + i * 400, 326, 284 + i * 400, 368, sw=3)
    s.arrow(1424, 600, 1478, 600, sw=3)
    s.text(96, 900, "差異化：① 行為與交易在同一事件模型　② OneID 以客戶／帳戶多主體打通　③ AI agent 與人共用語意層與權限　④ 全開源寬鬆授權，無額外商業授權", 20, 600, C["brand"])
    s.save("slide-1-architecture.svg")

# ------------------------------------------------------------------ 2. 數據流
def s2():
    s = Slide("數據流：從事件進來，到洞察與行動出去", "五個步驟、五個延遲承諾", 2)
    steps = [("採集", ["GA360 BigQuery 日批 + 串流匯出", "數據中台交易事實表批次導入（不直連主機）", "CRM 連接器；舊平台歷史一次性匯入"], "teal"),
             ("打通", ["事件化：狀態表 → 業務事件", "OneID：客戶・帳戶・裝置", "個資在此代碼化，不進分析倉"], "teal"),
             ("儲存與聚合", ["ClickHouse 熱 13 個月", "GCS 冷層仍可查", "人日／日聚合層：九成查詢不碰明細"], "yellow"),
             ("分析與標籤", ["事件・漏斗・路徑・歸因・用戶列表", "1,000 標籤 45 分鐘內算完", "Bitmap 分群秒級"], "brand"),
             ("洞察與行動", ["AI agent：異常、診斷、建議", "受眾包名單 Webhook → MA / CRM", "GCS Parquet → 數倉"], "purple")]
    for i, (t, ls, c) in enumerate(steps):
        x = 96 + i * 350
        s.chevron(x, 230, 340, 70, f"{i+1}  {t}", c, first=(i == 0))
        s.box(x, 330, 324, 180, t, ls, c, 24, 18)
    # 底下的關鍵規則
    s.rect(96, 560, 1728, 150, "#FBFCFE", C["brandline"], 16)
    s.text(120, 600, "關鍵規則", 22, 800, C["brand"])
    for i, ln in enumerate(["• 同一事件模型：GA 行為事件與交易事件並列，漏斗可從「看盤」一路算到「成交」",
                            "• 日批為權威、串流為先行：GA360 日批到齊後覆蓋串流匯出；event_id 冪等去重",
                            "• 人與 AI 走同一條路：語意層 → Policy Engine（權限・隱碼・審計）→ ClickHouse；數字一定可追溯到指標定義"]):
        s.text(120, 636 + i * 30, ln, 19, 400, C["ink"])
    # SLA
    sla = [("行為事件可查", "< 60 秒"), ("交易事件可查", "< 10 秒"), ("即時觸發", "< 5 秒"), ("30 天漏斗（1 億事件）", "< 10 秒"), ("用戶列表人數預估", "< 1 秒")]
    for i, (a, b) in enumerate(sla):
        x = 96 + i * 350
        s.rect(x, 760, 324, 120, C["white"], C["brandline"], 14)
        s.text(x + 162, 810, b, 36, 800, C["brand"], "middle")
        s.text(x + 162, 848, a, 18, 600, C["gray"], "middle")
    s.text(96, 930, "P95 目標，於 300 億事件、300 萬客戶的合成資料集驗證", 17, 400, C["muted"])
    s.save("slide-2-data-flow.svg")

# ------------------------------------------------------------------ 3. AI Agent 層
def s3():
    s = Slide("AI 洞察 Agent：讀得懂資料、提得出洞察、做得了受控的行動", "參考舊平台 Copilot 的「選擇題式」設計，再加上金融業需要的權限、審批與可追溯", 3)
    # 左：流程
    flow = [("提問 / 排程 / 事件", "業務人員口語提問；每 15 分鐘異常掃描；活動結束復盤", "gray"),
            ("Planner：對應語意層", "指標搜尋 → 分析配置卡（模型、指標、篩選、時間）可校驗、可修改", "purple"),
            ("工具閘道（MCP）", "27 個工具；以委託身份執行；預算、隱碼、審計；外部 AI 客戶端也走這裡", "purple"),
            ("語意層 → ClickHouse", "與 UI 同一個查詢編譯器；數字必來自工具回傳", "brand"),
            ("Verifier → 洞察物件", "交叉驗證、樣本量、口徑注意事項；證據可重跑、信心度、回饋", "purple"),
            ("行動（需審批）", "提議分群／規則 → 主管審批 → 推送 MA／內部系統 Webhook → 效果復盤", "red")]
    for i, (t, d, c) in enumerate(flow):
        y = 215 + i * 112
        s.circle_num(120, y + 34, i + 1, c)
        s.box(160, y, 900, 90, t, [d], c, 23, 18)
        if i < len(flow) - 1: s.arrow(610, y + 90, 610, y + 112, sw=2.5)
    # 右：agents + 守門
    s.rect(1110, 215, 714, 330, "#FBFCFE", C["brandline"], 16)
    s.text(1134, 252, "內建 Agent（可用 Markdown skill 新增）", 22, 800, C["brand"])
    agents = [("異常偵測", "指標偏離 → 關聯維度下鑽主因"), ("漏斗診斷", "流失最大步驟、分群差異、假設驗證"), ("流失預警", "高價值沉睡客 → 提議分群與觸發"), ("分析 Copilot", "指標搜尋、選擇題式問答、修正學習"), ("看板解讀", "每日診斷報告"), ("策略閉環", "策略 → 審批 → 執行 → 復盤")]
    for i, (t, d) in enumerate(agents):
        x = 1134 + (i % 2) * 340; y = 272 + (i // 2) * 86
        s.rect(x, y, 326, 74, C["purplesoft"], C["purple"], 12)
        s.text(x + 16, y + 30, t, 20, 800); s.text(x + 16, y + 56, d, 16, 400, C["gray"])
    s.rect(1110, 570, 714, 300, C["redsoft"], C["red"], 16)
    s.text(1134, 607, "金融業守門", 22, 800, C["red"])
    for i, ln in enumerate(["• agent 沒有超級權限：永遠代表某位使用者，行級與隱碼規則相同", "• 不生成自由 SQL：只能在既有分析模型上做選擇題", "• 執行類動作一律需人審批；敏感細查需理由並審計", "• 輸出守門：個資正規式攔截；數字對照工具回傳", "• 模型可換：企業 LLM 閘道，可用本地模型；golden evals 把關"]):
        s.text(1134, 645 + i * 40, ln, 18, 400, C["ink"])
    s.save("slide-3-ai-agent.svg")

# ------------------------------------------------------------------ 4. GCP 部署與規模
def s4():
    s = Slide("GCP 部署架構與規模：3 年 200 億事件、每日 500–1,000 標籤、50 人同時上線", "常駐 3–6 台多個檔位（A 6 台／D 3 台大機型／C 3 台）；容器化部署套件（Docker 映像 + Helm）同一套件可部署機房；資料全程留在 asia-east1", 4)
    s.rect(96, 215, 1160, 560, "#FBFCFE", C["brandline"], 20)
    s.text(120, 252, "A. 標準 HA（建議）— GKE 私有叢集 6 台 + 臨時 Spot", 24, 800, C["brand"])
    pools = [("分析倉 olap", ["ClickHouse 1 shard × 2 replica，3 年全熱 3.9 TB / 副本", "任一台故障另一副本續服"], "c3-highmem-22 × 2 · Hyperdisk 4 TB", "yellow"),
             ("資料・平台・應用 general", ["Kafka ×3、Keeper ×3、Flink、Airflow / dbt、Collector、CDC", "Keycloak、監控，以及全部應用（API、語意層、分群、Agent、Web）"], "n2-standard-16 × 4", "teal"),
             ("臨時 spot（不算常駐）", ["舊資料導入 50 億、BigQuery 大批匯入、模型訓練", "用完歸零"], "n2-standard-8 × 0–4（Spot）", "gray"),
             ("託管服務（不占台數）", ["Cloud SQL PostgreSQL 16 HA · Memorystore Valkey 8 GB", "Cloud Storage 8 TB · KMS/HSM · Artifact Registry"], "", "brand")]
    for i, (t, ls, m, c) in enumerate(pools):
        x = 120 + (i % 2) * 560; y = 280 + (i // 2) * 150
        s.rect(x, y, 540, 130, C[c + "soft"], C[c], 14)
        s.text(x + 18, y + 36, t, 22, 800); s.text(x + 18, y + 64, ls[0], 16, 400, C["gray"]); s.text(x + 18, y + 88, ls[1], 16, 400, C["gray"])
        if m: s.text(x + 18, y + 116, m, 17, 700, C[c])
    s.rect(120, 600, 1110, 60, C["redsoft"], C["red"], 12)
    s.text(144, 638, "安全：私有節點、Workload Identity、Binary Authorization、CMEK、Cloud Armor WAF、IAP、NetworkPolicy、UBI 映像", 18, 600)
    s.rect(120, 680, 1110, 70, C["graysoft"], C["gray"], 12)
    s.text(144, 708, "D. 3 台大機型：c3-highmem-44 × 3 同構（132 vCPU / 1 TB），標籤 500、三年不加機器　　C. 3 台：n2-highmem-32 × 3（測試）", 18, 600)
    s.text(144, 736, "各檔位皆滿足 200 億事件全熱、500–1,000 標籤日批、50 人上線；成本跟 vCPU 走：D 檔比 A 檔貴約 30%，換取台數最少與最大餘裕", 17, 400, C["gray"])
    s.rect(1300, 215, 524, 560, C["brandsoft"], C["brand"], 20)
    s.text(1324, 252, "規模與成本（A 檔 6 台）", 24, 800, C["brand"])
    nums = [("6 台", "常駐 GKE 節點（舊版 18 台）"), ("120 vCPU / 608 GB", "運算與記憶體"), ("10.5 TB + 8 TB", "區塊磁碟 + GCS"), ("約 50 分鐘", "1,000 個標籤全量日批"), ("US$ 4.9–6.5 k / 月", "1 年 CUD vs 隨需；D 檔 3 台大機型 6.2–8.6 k；粗估未稅")]
    for i, (a, b) in enumerate(nums):
        y = 300 + i * 92
        s.text(1324, y + 30, a, 34, 800, C["ink"]); s.text(1324, y + 58, b, 17, 400, C["gray"])
    s.text(96, 830, "正式：A 檔 6 台或 D 檔 3 台大機型；測試：3 台（同一套 Helm 只換 values，非上班時段可縮到 0）。機房：D 檔 3 台 2 × 24 核／512 GB／NVMe 或 A 檔 6 台，RKE2 + Rocky/RHEL 9。自建映像整併為 6 個。", 19, 600, C["ink"])
    s.text(96, 866, "弱點與授權：所有元件 OSI 寬鬆授權（Grafana OSS 僅內部使用）、LTS 版本、CI 自動 SBOM／Grype／Trivy／授權白名單；修補 SLA Critical 7 天。", 19, 400, C["gray"])
    s.save("slide-4-gcp-deployment.svg")

def s5():
    s = Slide("機房部署架構：Kubernetes 上只有五個有狀態服務，其餘全是無狀態應用", "RKE2 1.30+，3 個 control-plane 與 worker 共存於 3–6 台；同一套 Helm 部署套件，GCP 版只換 values（託管 Cloud SQL／Memorystore／GCS／KMS）", 5)
    def band(y, h, label, color):
        s.rect(96, y, 1728, h, "#FBFCFE", C[color], 18); s.text(120, y + 34, label, 22, 800, C[color])
    def row(y, items, color, w=None, h=96, x0=120, gap=16, lsize=16):
        w = w or (1680 - gap * (len(items) - 1)) / len(items)
        for i, (t, ln) in enumerate(items):
            s.box(x0 + i * (w + gap), y, w, h, t, ln, color, 20, lsize)
    band(205, 150, "平台應用（無狀態 Deployment，映像 6 個：core／ingest／batch／agent／flink-jobs／web）", "brand")
    row(250, [("dmp-web", ["前端靜態檔（nginx）"]), ("dmp-core", ["API、語意層、分群、OneID、同步、推送"]), ("dmp-ingest", ["Collector（Go）：直送 /v1/events"]),
              ("dmp-batch", ["Airflow ＋ dbt 日批"]), ("dmp-agent", ["MCP 閘道（第二階段）"])], "brand")
    band(375, 330, "Kubernetes（RKE2）", "teal")
    s.text(120, 440, "資料層：有狀態、Operator 管理、本地 NVMe", 18, 700, C["gray"])
    row(452, [("ClickHouse 2×2 ＋ Keeper ×3", ["行為事件倉、MV、Bitmap"]), ("PostgreSQL 16 ×2", ["CloudNativePG；控制面"]), ("Valkey 8 ×3", ["快取、佇列、即時標籤"]),
              ("Kafka 3.8 KRaft ×3", ["Strimzi；推送、原始事件"]), ("SeaweedFS（S3）", ["冷層、備份、同步輸出"])], "yellow")
    s.text(120, 585, "平台層", 18, 700, C["gray"])
    row(597, [("Keycloak", ["SSO／OIDC"]), ("OpenBao ＋ HSM", ["密鑰只存參照"]), ("Prometheus ＋ Grafana", ["指標、告警、系統監控頁"]), ("VictoriaLogs", ["日誌（或 OpenSearch）"]),
              ("Harbor", ["離線映像倉 ＋ Trivy"]), ("Ingress-NGINX ＋ MetalLB", ["入口與 VIP"])], "purple", h=86)
    band(725, 140, "每個節點（DaemonSet）", "gray")
    row(770, [("node-exporter", ["主機指標"]), ("OTel Collector", ["日誌與追蹤"]), ("LocalPV（OpenEBS）", ["NVMe 本地盤給 ClickHouse／Kafka"]),
              ("RHEL 9／Rocky 9", ["SELinux enforcing；容器無 root、唯讀"])], "gray", h=86, lsize=15)
    s.rect(96, 885, 1728, 60, C["brandsoft"], C["brand"], 12)
    s.text(120, 923, "備份：ClickHouse BACKUP TO S3（日增量／週全量）、PostgreSQL WAL 歸檔；HA：ClickHouse 雙副本、PostgreSQL 主從、Kafka／Keeper 三節點；全部元件上游 LTS、OSI 授權、有 Operator", 18, 600)
    s.text(96, 980, "對照 2025/07 提案舊棧（Hadoop／Hive／HBase／ZooKeeper／RocketMQ／MySQL 5.7／Redis／Tendis／InfluxDB／Consul／MinIO，約 20 個元件）：有狀態服務從十幾個降到 5 個，沒有 EOL 版本，沒有改寫版。", 17, 400, C["gray"])
    s.save("slide-5-onprem-deployment.svg")

# ------------------------------------------------------------------ 6. Docker 部署（2026-10-22 研發／運維決議，取代第 4、5 頁的 Kubernetes 做法）
def s6():
    s = Slide("部署架構（2026-10 決議版）：Docker Compose，不用 Kubernetes",
              "快取用 Redis、日誌用 Loki；每台主機一份 Compose、同一批 6 個自建映像；機房 RHEL／Rocky 9，GCP 用 Compute Engine VM（可改託管 Cloud SQL／Memorystore／GCS）", 6)
    # 入口列
    s.box(96, 196, 560, 96, "入口", ["客戶負載平衡（F5／GCP 內部 LB）→ 主機 1 nginx（TLS）", "主機 2 為備援；雙活待共用 session 與作業租約"], "red", 22, 15)
    s.box(672, 196, 560, 96, "映像與部署", ["Harbor 離線映像倉（Trivy 弱掃）；每台 compose ＋ .env，逐台換版"], "gray", 22, 16)
    s.box(1248, 196, 576, 96, "主機規模", ["本頁 3 台版（D 檔正式／C 檔測試）；A 檔 6 台把", "ClickHouse 兩副本移到專用機；單機測試全放一台"], "gray", 22, 15)
    hosts = [
        ("主機 1", [("dmp-web（nginx）", "前端靜態檔＋反向代理", "brand"), ("dmp-core API ＋ worker", "唯一接流量；背景作業只在這台執行", "brand"),
                   ("ClickHouse 副本 1", "行為事件倉、MV、Bitmap", "yellow"), ("ClickHouse Keeper 1", "副本協調", "yellow"),
                   ("PostgreSQL 16 主庫", "控制面；pgBackRest WAL 歸檔", "yellow"),
                   ("Redis 主 ＋ Sentinel", "登入狀態、快取、佇列；AOF", "yellow")]),
        ("主機 2", [("dmp-web（nginx）", "備援；平時不接流量", "brand"), ("dmp-core API（備援）", "不開背景作業；切換時使用者需重新登入", "brand"),
                   ("ClickHouse 副本 2", "任一台故障另一副本續服", "yellow"), ("ClickHouse Keeper 2", "副本協調", "yellow"),
                   ("PostgreSQL 16 從庫", "串流複寫；切換走手動程序", "yellow"),
                   ("Redis 從 ＋ Sentinel", "主庫故障由 Sentinel 自動切換", "yellow")]),
        ("主機 3", [("dmp-ingest（選配）", "MA／LINE 直送才啟用；Kafka、Flink 第二階段", "teal"), ("ClickHouse Keeper 3", "三節點多數決", "yellow"),
                   ("Redis Sentinel 3", "第三票", "yellow"), ("Keycloak ＋ OpenBao", "SSO／OIDC；密鑰落檔掛載", "purple"),
                   ("Prometheus ＋ Alertmanager", "指標、告警 → Email／LINE", "purple"), ("Grafana ＋ Loki", "看板與日誌查詢（Loki 取代其他日誌元件）", "purple"),
                   ("SeaweedFS（三台都有）", "master＋volume 2 副本；備份另複製到叢集外", "yellow")]),
    ]
    for i, (hn, items) in enumerate(hosts):
        x = 96 + i * 408; w = 392
        s.rect(x, 312, w, 548, "#FBFCFE", C["brandline"], 16)
        s.text(x + 20, 346, hn, 22, 800, C["brand"])
        s.text(x + w - 20, 346, "Docker Engine ＋ Compose", 15, 600, C["muted"], "end")
        for j, (t, d, c) in enumerate(items):
            y = 362 + j * 70
            s.rect(x + 14, y, w - 28, 62, C[c + "soft"], C[c], 10)
            s.p.append(f'<rect x="{x + 14}" y="{y}" width="6" height="62" rx="3" fill="{C[c]}"/>')
            s.text(x + 32, y + 26, t, 18, 800); s.text(x + 32, y + 50, d, 15, 400, C["gray"])
    # 右欄：拿掉 Kubernetes 後誰補位
    s.rect(1328, 312, 496, 548, C["redsoft"], C["red"], 16)
    s.text(1352, 346, "拿掉 Kubernetes 後由誰補位", 22, 800, C["red"])
    tips = [("程序掛掉", ["restart: unless-stopped ＋ healthcheck"]),
            ("主機掛掉", ["ClickHouse 雙副本、Keeper 三節點自身容錯；Redis Sentinel", "PostgreSQL 手動 promote（RTO 30 分鐘）", "主機 1 掛：改送主機 2 API、手動起 worker"]),
            ("第二階段才有", ["Kafka 三節點、Flink standalone（docs/43）", "啟用時 Kafka 各放一台、Flink 放主機 3"]),
            ("換版與容量", ["逐台 pull ＋ up，維護窗口內", "沒有自動擴縮，容量按檔位預留"]),
            ("隔離", ["每台 Docker 網路＋主機防火牆", "容器非 root、唯讀、cap_drop ALL"]),
            ("密鑰與憑證", ["設定只存 secret_ref；客戶內部 CA 簽發", "憑證到期前告警"])]
    yy = 380
    for t, ds in tips:
        s.text(1352, yy, "• " + t, 18, 800, C["ink"])
        for d in ds:
            yy += 23; s.text(1372, yy, d, 15, 400, C["gray"])
        yy += 31
    # 每台共通 ＋ 授權
    s.rect(96, 876, 1728, 52, C["graysoft"], C["gray"], 12)
    s.text(120, 909, "每台共通：Alloy（容器日誌 → Loki）· node-exporter · cAdvisor · RHEL／Rocky 9（SELinux enforcing）　｜　備份：ClickHouse BACKUP TO S3、pgBackRest、Redis AOF", 17, 600)
    s.rect(96, 938, 1728, 52, C["yellowsoft"], C["yellow"], 12)
    s.text(120, 971, "授權：Redis 8 選 AGPLv3；Loki、Grafana 為 AGPLv3 — 皆用官方映像、不修改、僅內部使用、不對外提供服務（同既有 Grafana 例外，待法務確認）；其餘 Apache-2.0／BSD／MIT", 17, 600)
    s.save("slide-6-docker-deployment.svg")

if __name__ == "__main__":
    s1(); s2(); s3(); s4(); s5(); s6()
