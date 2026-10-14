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
    for i, (t, ls) in enumerate([("GA360 埋點", ["Web / App 行為、GTM 境內雙寫"]), ("證券交易系統", ["委託、成交、出入金、開戶、基金"]), ("CRM / 神策 / MA / AI Coach", ["客戶主檔、歷史事件、推播、對練"])]):
        s.box(96 + i * 400, 230, 376, 96, t, ls, "gray", 24, 18)
    # 中台
    s.rect(96, 370, 1328, 470, "#FBFCFE", C["brandline"], 20)
    s.text(120, 406, "AltaTech DMP 數位軌跡／客戶數據中台", 26, 800, C["brand"])
    pillars = [("數據整合與 OneID", ["可視化建模、CDC / 批次", "客戶・帳戶・裝置打通", "個資代碼化（HMAC/FPE）"], "teal"),
               ("分析引擎", ["事件、漏斗、行為路徑", "歸因、用戶列表、留存", "百億事件秒級查詢"], "brand"),
               ("標籤・分群・即時觸發", ["1,000+ 標籤每日運算", "Bitmap 分群、即時規則", "API / Kafka 同步到各系統"], "yellow"),
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
    for i, (t, ls, c) in enumerate([("DMP Web", ["看板、分析、用戶列表", "權限中心、日誌統計"], "brand"), ("AI 洞察助理", ["問答、洞察串流", "外部 MCP 客戶端"], "purple"), ("MA・營業員 App・AI Coach", ["分群推送、即時觸發", "提示與對練回寫"], "teal"), ("數倉・神策（雙軌）", ["Iceberg 雙向映射", "神策格式反向輸出"], "gray")]):
        s.box(1480, 230 + i * 150, 344, 128, t, ls, c, 22, 17)
    # 箭頭
    for i in range(3): s.arrow(284 + i * 400, 326, 284 + i * 400, 368, sw=3)
    s.arrow(1424, 600, 1478, 600, sw=3)
    s.text(96, 900, "差異化：① 行為與交易在同一事件模型　② OneID 以客戶／帳戶多主體打通　③ AI agent 與人共用語意層與權限　④ 全開源寬鬆授權，無額外商業授權", 20, 600, C["brand"])
    s.save("slide-1-architecture.svg")

# ------------------------------------------------------------------ 2. 數據流
def s2():
    s = Slide("數據流：從事件進來，到洞察與行動出去", "五個步驟、五個延遲承諾", 2)
    steps = [("採集", ["GA360 日批 + GTM 境內即時雙寫", "交易主機 CDC（不改核心）", "神策 / CRM 連接器"], "teal"),
             ("打通", ["事件化：狀態表 → 業務事件", "OneID：客戶・帳戶・裝置", "個資在此代碼化，不進分析倉"], "teal"),
             ("儲存與聚合", ["ClickHouse 熱 13 個月", "GCS 冷層仍可查", "人日／日聚合層：九成查詢不碰明細"], "yellow"),
             ("分析與標籤", ["事件・漏斗・路徑・歸因・用戶列表", "1,000 標籤 45 分鐘內算完", "Bitmap 分群秒級"], "brand"),
             ("洞察與行動", ["AI agent：異常、診斷、建議", "即時觸發 → MA / 營業員 App", "標籤 API / Kafka 同步"], "purple")]
    for i, (t, ls, c) in enumerate(steps):
        x = 96 + i * 350
        s.chevron(x, 230, 340, 70, f"{i+1}  {t}", c, first=(i == 0))
        s.box(x, 330, 324, 180, t, ls, c, 24, 18)
    # 底下的關鍵規則
    s.rect(96, 560, 1728, 150, "#FBFCFE", C["brandline"], 16)
    s.text(120, 600, "關鍵規則", 22, 800, C["brand"])
    for i, ln in enumerate(["• 同一事件模型：GA 行為事件與交易事件並列，漏斗可從「看盤」一路算到「成交」",
                            "• 日批為權威、即時為先行：GA360 日批到齊後覆蓋即時雙寫；event_id 冪等去重",
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
    s = Slide("AI 洞察 Agent：讀得懂資料、提得出洞察、做得了受控的行動", "參考神策 Copilot 的「選擇題式」設計，再加上金融業需要的權限、審批與可追溯", 3)
    # 左：流程
    flow = [("提問 / 排程 / 事件", "業務人員口語提問；每 15 分鐘異常掃描；活動結束復盤", "gray"),
            ("Planner：對應語意層", "指標搜尋 → 分析配置卡（模型、指標、篩選、時間）可校驗、可修改", "purple"),
            ("工具閘道（MCP）", "27 個工具；以委託身份執行；預算、隱碼、審計；外部 AI 客戶端也走這裡", "purple"),
            ("語意層 → ClickHouse", "與 UI 同一個查詢編譯器；數字必來自工具回傳", "brand"),
            ("Verifier → 洞察物件", "交叉驗證、樣本量、口徑注意事項；證據可重跑、信心度、回饋", "purple"),
            ("行動（需審批）", "提議分群／規則 → 主管審批 → 推送 MA／營業員 App → 效果復盤", "red")]
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
    s = Slide("GCP 部署架構與規模：3 年 200 億事件、每日 1,000 標籤、50 人同時上線", "模組合併為 3 個節點池、7 台機器；容器化部署套件（Docker 映像 + Helm）同一套件可部署機房；資料全程留在 asia-east1", 4)
    s.rect(96, 215, 1160, 560, "#FBFCFE", C["brandline"], 20)
    s.text(120, 252, "GKE 私有叢集（asia-east1）— 3 個節點池 + 臨時 Spot", 24, 800, C["brand"])
    pools = [("分析倉 olap", ["ClickHouse 1 shard × 2 replica", "3 年全熱 3.9 TB / 副本"], "c3-highmem-22 × 2 · Hyperdisk 4 TB", "yellow"),
             ("資料與平台 general", ["Kafka ×3、Keeper ×3、Flink、Airflow / dbt", "Collector、CDC、Keycloak、Prometheus、Grafana"], "n2-standard-16 × 3", "teal"),
             ("應用 app", ["API、語意層、分群標籤、OneID", "工具閘道、Agent、Tag Sync、Web"], "n2-standard-8 × 2", "brand"),
             ("臨時 spot", ["舊資料導入 50 億、BigQuery 大批匯入", "模型標籤訓練；用完歸零"], "n2-standard-8 × 0–4（Spot）", "gray")]
    for i, (t, ls, m, c) in enumerate(pools):
        x = 120 + (i % 2) * 560; y = 280 + (i // 2) * 150
        s.rect(x, y, 540, 130, C[c + "soft"], C[c], 14)
        s.text(x + 18, y + 36, t, 22, 800); s.text(x + 18, y + 64, ls[0], 17, 400, C["gray"]); s.text(x + 18, y + 88, ls[1], 17, 400, C["gray"]); s.text(x + 18, y + 116, m, 17, 700, C[c])
    s.rect(120, 600, 1110, 60, C["redsoft"], C["red"], 12)
    s.text(144, 638, "安全：私有節點、Workload Identity、Binary Authorization、CMEK、Cloud Armor WAF、IAP、NetworkPolicy、UBI 映像", 18, 600)
    s.rect(120, 680, 1110, 70, C["graysoft"], C["gray"], 12)
    s.text(144, 708, "託管服務：Cloud SQL PostgreSQL 16（HA 2 vCPU）· Memorystore Valkey 8 GB · Cloud Storage 8 TB（原始、Iceberg、備份、WORM）", 18, 600)
    s.text(144, 736, "Cloud KMS/HSM · Secret Manager · Artifact Registry（SBOM、持續弱掃）· Cloud Logging", 18, 400, C["gray"])
    s.rect(1300, 215, 524, 560, C["brandsoft"], C["brand"], 20)
    s.text(1324, 252, "規模與成本（精簡標準配置）", 24, 800, C["brand"])
    nums = [("7 台", "常駐 GKE 節點（舊版 18 台 → 少 11 台）"), ("108 vCPU / 608 GB", "運算與記憶體"), ("10.5 TB + 8 TB", "區塊磁碟 + GCS"), ("約 50 分鐘", "1,000 個標籤全量日批"), ("US$ 4.9–6.5 k / 月", "1 年 CUD vs 隨需；粗估未稅，較舊版降約 40%")]
    for i, (a, b) in enumerate(nums):
        y = 300 + i * 92
        s.text(1324, y + 30, a, 34, 800, C["ink"]); s.text(1324, y + 58, b, 17, 400, C["gray"])
    s.text(96, 830, "極簡配置：app 池併入 general（n2-standard-16 × 4）→ 6 台；事件量翻倍時 ClickHouse 線上擴磁碟或加分片。", 19, 600, C["ink"])
    s.text(96, 866, "弱點與授權：所有元件 OSI 寬鬆授權（Grafana OSS 僅內部使用）、LTS 版本、CI 自動 SBOM／Grype／Trivy／授權白名單；修補 SLA Critical 7 天。", 19, 400, C["gray"])
    s.save("slide-4-gcp-deployment.svg")

if __name__ == "__main__":
    s1(); s2(); s3(); s4()
