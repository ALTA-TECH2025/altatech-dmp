// 永豐金證券 DMP 提案簡報初稿與報價初版（內部 v0.1）：內容取自 docs/33（簡報大綱）、docs/34（報價初版）、docs/31（時程與人力）、docs/17 §5a（待確認）
const pptxgen = require('pptxgenjs');
const { applyTheme } = require(process.env.APPLY_THEME_JS || './apply_theme.js');

const THEME = { name: 'AltaTech DMP', headFontFace: 'Noto Sans TC', bodyFontFace: 'Noto Sans TC',
  colors: { dk1: '1C2B33', lt1: 'FFFFFF', dk2: '121E2B', lt2: 'F7F8FA', accent1: '2C73EA', accent2: '12B5A8', accent3: 'D8871F', accent4: '6250D6', accent5: '7C8992', accent6: 'E9F0FD', hlink: '2C73EA', folHlink: '6250D6' } };
const HEX = { line: 'E2E8EC', ink: '1C2B33', muted: '7C8992', brand: '2C73EA', tint: 'E9F0FD', navy: '121E2B' };

const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = '永豐金證券 DMP 提案簡報初稿與報價初版';
pres.author = 'AltaTech';
const C = pres.SchemeColor;

pres.defineSlideMaster({ title: 'TITLE_DARK', background: { color: HEX.navy }, objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.6, y: 1.55, w: 8.8, h: 1.6, fontSize: 34, bold: true, color: C.background1, valign: 'top', align: 'left' }, text: '' } },
  { placeholder: { options: { name: 'body', type: 'body', x: 0.6, y: 3.35, w: 8.8, h: 1.2, fontSize: 16, color: 'C9D2D8', valign: 'top', align: 'left' }, text: '' } } ] });
pres.defineSlideMaster({ title: 'SECTION_DARK', background: { color: HEX.navy }, objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.6, y: 1.9, w: 8.8, h: 1.0, fontSize: 32, bold: true, color: C.background1, valign: 'bottom', align: 'left' }, text: '' } },
  { placeholder: { options: { name: 'body', type: 'body', x: 0.6, y: 3.0, w: 8.8, h: 0.9, fontSize: 16, color: 'C9D2D8', valign: 'top', align: 'left' }, text: '' } } ] });
pres.defineSlideMaster({ title: 'CONTENT', background: { color: 'F7F8FA' }, slideNumber: { x: 9.2, y: 5.2, w: 0.5, h: 0.3, fontSize: 10, color: HEX.muted, align: 'right' }, objects: [
  { placeholder: { options: { name: 'title', type: 'title', x: 0.5, y: 0.3, w: 9.0, h: 0.95, fontSize: 24, bold: true, color: C.text1, valign: 'top', align: 'left', margin: 0 }, text: '' } },
  { text: { text: 'AltaTech · 內部草稿 v0.1', options: { x: 0.5, y: 5.2, w: 4, h: 0.3, fontSize: 10, color: HEX.muted, margin: 0 } } } ] });

const T = (o) => ({ isTextBox: true, margin: 0, valign: 'top', ...o });
const card = (s, x, y, w, h, fill = C.background1, name) => s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: fill }, line: { color: HEX.line, width: 0.75 }, objectName: name });
const content = (title, section) => { const s = pres.addSlide({ masterName: 'CONTENT', sectionTitle: section }); s.addText(title, { placeholder: 'title' }); return s; };
const cell = (text, o = {}) => ({ text, options: { border: [{ type: 'none' }, { type: 'none' }, { pt: 0.75, color: HEX.line }, { type: 'none' }], valign: 'middle', ...o } });
function table(s, rows, { x = 0.5, y = 1.4, w = 9.0, colW, fontSize = 14, align = [], highlight = [], boldFirst = true, rowH = 0.34 } = {}) {
  const data = rows.map((r, i) => r.map((v, j) => cell(String(v), { bold: i === 0 || (boldFirst && j === 0), align: align[j] ?? 'left', fontSize, color: C.text1, fill: highlight.includes(i) ? { color: HEX.tint } : undefined, border: i === 0 ? [{ type: 'none' }, { type: 'none' }, { pt: 1.25, color: 'C9D2D8' }, { type: 'none' }] : undefined })));
  s.addTable(data, { x, y, w, colW, rowH, margin: [3, 6, 3, 6], autoPage: false });
}
const bullets = (s, items, o) => s.addText(items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 8 } })), T({ fontSize: 16, color: C.text1, ...o }));
const sec1 = '提案簡報初稿', sec2 = '報價初版（未稅）', sec3 = '週一會前會';

// 1 封面
pres.addSection({ title: sec1 });
{ const s = pres.addSlide({ masterName: 'TITLE_DARK', sectionTitle: sec1 });
  s.addText('內部草稿 v0.1 · 2026-10-17', T({ x: 0.6, y: 1.05, w: 8.8, h: 0.35, fontSize: 14, color: '8A9AA6' }));
  s.addText('永豐金證券行為數據平台：神策 → GA360 遷移與 DMP 提案', { placeholder: 'title' });
  s.addText('客戶高階主管會議 2026-10-23（五）· AltaTech × Merkle\n週一 10-19 會前會先對齊最後一節', { placeholder: 'body' }); }

// 2 今天要決定的事
{ const s = content('今天要決定的事', sec1);
  const items = [['01', '神策到期前可以安全換掉', '高階主管點頭：GA360 接埋點、DMP 接分析與營運；錢與人力都在表上'], ['02', '時程 v2.1 與 M0 十項待確認', '專案負責人同意時程，11-02 kick-off 排得出來'], ['03', 'Merkle 與我們講同一張圖', '交接點是 BigQuery 匯出與橋接表，客戶面前一個說法']];
  items.forEach(([n, h, b], i) => { const x = 0.5 + i * 3.1; card(s, x, 1.45, 2.8, 3.35, C.background1, `s2_card${i}`);
    s.addText(n, T({ x: x + 0.25, y: 1.7, w: 2.3, h: 0.6, fontSize: 30, bold: true, color: C.accent1 }));
    s.addText(h, T({ x: x + 0.25, y: 2.4, w: 2.3, h: 0.85, fontSize: 17, bold: true, color: C.text1 }));
    s.addText(b, T({ x: x + 0.25, y: 3.3, w: 2.3, h: 1.35, fontSize: 14, color: C.text1 })); }); }

// 3 處境
{ const s = content('神策 2027-10-31 到期，70 億筆歷史與每天在用的營運流程都要接得住', sec1);
  [['2027-10-31', '神策授權到期日'], ['70 億筆', '神策歷史行為事件']].forEach(([v, l], i) => { const y = 1.45 + i * 1.75; card(s, 0.5, y, 3.6, 1.5, C.accent6, `s3_stat${i}`);
    s.addText(v, T({ x: 0.75, y: y + 0.2, w: 3.1, h: 0.75, fontSize: 32, bold: true, color: C.accent1 }));
    s.addText(l, T({ x: 0.75, y: y + 0.95, w: 3.1, h: 0.4, fontSize: 14, color: C.text1 })); });
  bullets(s, ['行銷、數據分析、數位經營每天在用神策的報表、分群、標籤與名單推送', '新埋點改由 GA360 收，歷史資料與營運功能需要一個落地的家', '供應商評分表要求說清楚元件來源、授權與是否需付費', '切換前要有並行期對帳，數字對得上才停寫神策'], { x: 4.5, y: 1.5, w: 5.0, h: 3.4 }); }

// 4 方案一張圖
{ const s = content('GA360 收埋點、DMP 接分析與營運，交接點是 BigQuery 匯出', sec1);
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.5, y: 1.45, w: 3.55, h: 2.25, rectRadius: 0.08, fill: { color: 'EEF7F6' }, line: { type: 'none' }, objectName: 's4_merkle' });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 4.35, y: 1.45, w: 5.15, h: 3.4, rectRadius: 0.08, fill: { color: C.accent6 }, line: { type: 'none' }, objectName: 's4_alta' });
  s.addText('Merkle 負責', T({ x: 0.7, y: 1.55, w: 3.0, h: 0.35, fontSize: 14, bold: true, color: C.accent2 }));
  s.addText('AltaTech 負責', T({ x: 4.55, y: 1.55, w: 3.0, h: 0.35, fontSize: 14, bold: true, color: C.accent1 }));
  const box = (x, y, w, t, name) => { card(s, x, y, w, 1.05, C.background1, name); s.addText(t, T({ x: x + 0.1, y: y + 0.12, w: w - 0.2, h: 0.85, fontSize: 14, bold: true, color: C.text1, align: 'center', valign: 'middle' })); };
  box(0.7, 2.1, 1.45, 'GA360\n埋點', 's4_b1'); box(2.45, 2.1, 1.45, 'BigQuery\n匯出', 's4_b2');
  box(4.55, 2.1, 1.5, 'DMP\nClickHouse', 's4_b3'); box(6.3, 2.1, 1.5, '分析・分群\n標籤・推送', 's4_b4'); box(8.0, 2.1, 1.3, 'MA／CRM', 's4_b5');
  ['→', '→', '→', '→'].forEach((a, i) => s.addText(a, T({ x: [2.15, 3.9, 6.05, 7.8][i], y: 2.4, w: 0.3, h: 0.45, fontSize: 20, bold: true, color: HEX.muted, align: 'center' })));
  box(4.55, 3.55, 1.5, '神策歷史\n70 億筆', 's4_b6'); s.addText('↑', T({ x: 5.15, y: 3.15, w: 0.3, h: 0.4, fontSize: 20, bold: true, color: HEX.muted, align: 'center' }));
  s.addText('歷史資料經橋接表轉成同一套事件口徑，與 GA360 新資料在 DMP 會合，客戶代碼銜接 OneID', T({ x: 6.3, y: 3.6, w: 3.0, h: 1.1, fontSize: 13, color: C.text1 }));
  s.addText('分工線：埋點、GTM、GA360 設定、BigQuery 匯出與 Google 端 SLA 歸 Merkle；匯出之後的載入、OneID、分析、營運、歷史銜接與對帳工具歸 AltaTech', T({ x: 0.5, y: 3.95, w: 3.55, h: 0.95, fontSize: 12, color: HEX.muted })); }

// 5 為什麼不是只用 GA360
{ const s = content('GA360 給 Google 口徑的報表，證券業要的交易結合與可推送名單在 DMP', sec1);
  table(s, [['需求', 'GA360', 'DMP'], ['行為 × 交易結合分析', '沒有交易資料', '數據中台交易事件入倉，與行為同一張表'], ['客戶代碼銜接', '只有 user_id', '歷史與新埋點歸到同一 OneID'], ['可推送的名單', '受眾主要用於 Google 廣告', 'Webhook 推送 MA／CRM，含審批與增量'], ['自定義 SQL', '需另開 BigQuery 查詢', '唯讀沙盒、依權限表目錄、自動脫敏'], ['本地保存與審計', '資料在 Google 雲', '本地保存，全操作審計與匯出浮水印']], { y: 1.45, colW: [2.4, 2.6, 4.0], rowH: 0.52 });
  s.addText('對外說法：GA360 看 Google 口徑，DMP 看本地口徑與交易結合，兩者互補不取代', T({ x: 0.5, y: 4.75, w: 9.0, h: 0.35, fontSize: 13, color: HEX.muted })); }

// 6 現況
{ const s = content('四天做出程式級第一版，五種角色都能在示範站操作', sec1);
  [['139 / 174', 'OpenAPI 路徑已實作'], ['18 組', '自動化自測，含真 ClickHouse 端到端'], ['5 種', '角色權限可在示範站切換演示']].forEach(([v, l], i) => { const x = 0.5 + i * 3.1; card(s, x, 1.5, 2.8, 2.0, C.background1, `s6_stat${i}`);
    s.addText(v, T({ x: x + 0.25, y: 1.75, w: 2.3, h: 0.85, fontSize: 30, bold: true, color: C.accent1 }));
    s.addText(l, T({ x: x + 0.25, y: 2.65, w: 2.3, h: 0.7, fontSize: 14, color: C.text1 })); });
  bullets(s, ['示範站：altatech-dmp.vercel.app（合成資料，不含客戶資料）', 'CI 對真後端跑瀏覽器主線：登入 → 事件分析 → 書籤 → 看板 → 推送'], { x: 0.5, y: 3.85, w: 9.0, h: 1.1, fontSize: 15 });
  s.addText('數字截至 2026-10-17（docs/31 §1）', T({ x: 5.5, y: 5.2, w: 3.5, h: 0.3, fontSize: 10, color: HEX.muted, align: 'right' })); }

// 7 現場操作
{ const s = content('現場操作三段各 4 分鐘：先講神策怎麼做，再操作，最後回到需求表編號', sec1);
  table(s, [['段', '角色', '步驟', '要講的點'], ['① 分析', '數據分析', '數位開戶轉換看板 → 事件分析改分組 → 存書籤進看板', '口徑與神策一致'], ['② 營運', '數位經營', '分群估數 → 標籤 RFM 分布 → 推送單到 CRM → 交付紀錄', '名單不出庫、要審批'], ['③ 治理', '資訊管理', '權限中心模擬行銷角色 → 日誌告警 → 資料流與 SLA → 系統監控', '可審計、可監控']], { y: 1.5, colW: [1.0, 1.3, 4.4, 2.3], rowH: 0.75 });
  s.addText('對應需求表 A4／A5／C2／C5／D4／E3；備一台本機真後端當備援', T({ x: 0.5, y: 4.65, w: 9.0, h: 0.35, fontSize: 13, color: HEX.muted })); }

// 8 歷史資料
{ const s = content('歷史資料分六步過來，三方對帳決定何時切換', sec1);
  const steps = ['盤點', '必留清單', '導出\nImpala／HDFS', '橋接表', '載入', '三方對帳\nT1–T9'];
  steps.forEach((t, i) => { const x = 0.5 + i * 1.53; card(s, x, 1.6, 1.33, 1.25, i === 5 ? C.accent6 : C.background1, `s8_step${i}`);
    s.addText(String(i + 1), T({ x: x + 0.12, y: 1.7, w: 1.1, h: 0.35, fontSize: 14, bold: true, color: C.accent1 }));
    s.addText(t, T({ x: x + 0.1, y: 2.05, w: 1.13, h: 0.75, fontSize: 14, bold: true, color: C.text1 })); });
  bullets(s, ['M1（11-20）拿 1 個月真樣本導出載入，M2（12-18）跑完 70 億', '並行期從 M3（2027-01）起，每日 T5／T6 對帳，差異 > 5% 開埋點修正單', '切換以差異門檻決定，PD 後 4 週補導最終增量與最終對帳'], { x: 0.5, y: 3.2, w: 9.0, h: 1.8, fontSize: 15 }); }

// 9 與 Merkle 的交接點
{ const s = content('與 Merkle 的交接點有四個，橋接表作為埋點規範附件', sec1);
  [['埋點規範與事件命名', '神策事件名 → GA4 事件名的橋接表由我們產，Merkle 依此埋點'], ['BigQuery 匯出', '日批前日全量＋串流 intraday；72 小時重寫；GA4 標準 schema'], ['user_id 口徑', '登入後 user_id = 客戶代碼（HMAC），與神策登入 ID 同一把金鑰'], ['並行期對帳', '每日 T5／T6，只放筆數與差異率；修正單 SLA 由雙方約定']].forEach(([h, b], i) => {
    const x = 0.5 + (i % 2) * 4.6, y = 1.45 + Math.floor(i / 2) * 1.75; card(s, x, y, 4.4, 1.55, C.background1, `s9_card${i}`);
    s.addShape(pres.shapes.OVAL, { x: x + 0.25, y: y + 0.25, w: 0.42, h: 0.42, fill: { color: C.accent1 }, line: { type: 'none' }, objectName: `s9_dot${i}` });
    s.addText(String(i + 1), T({ x: x + 0.25, y: y + 0.25, w: 0.42, h: 0.42, fontSize: 14, bold: true, color: C.background1, align: 'center', valign: 'middle' }));
    s.addText(h, T({ x: x + 0.85, y: y + 0.27, w: 3.35, h: 0.4, fontSize: 16, bold: true, color: C.text1 }));
    s.addText(b, T({ x: x + 0.85, y: y + 0.72, w: 3.35, h: 0.75, fontSize: 13, color: C.text1 })); }); }

// 10 時程
{ const s = content('UAT 2027-06-30、正式上線 2027-09-30，比神策到期早 31 天', sec1);
  table(s, [['里程碑', '日期', '退出重點'], ['M0 決策與環境', '2026-11-02 ～ 11-13', '十項待確認拍板、測試環境、真 IdP 接通'], ['M1 盤點與橋接表', '2026-11-20', '必留清單確認、橋接表交 Merkle、1 個月真樣本'], ['M2 歷史資料進倉', '2026-12-18', '70 億載入、10 億效能基準回填 SLA'], ['M3 GA360 接入與並行', '2027-01-22', 'GA360 真匯出接入，並行期與每日對帳開始'], ['M4 交易結合與營運', '2027-02-26', '交易真資料、結合分析、神策分群標籤重建'], ['M5 內部驗收', '2027-03-26', '安全強化、DR 演練、切換演練、操作手冊'], ['UAT／PD', '2027-06-30／09-30', '客戶驗收 T1–T9；正式上線、神策停寫']], { y: 1.4, colW: [2.4, 2.2, 4.4], rowH: 0.43, highlight: [7] }); }

// 11 團隊
{ const s = content('AltaTech 七人加 ClickHouse 顧問 20 天，三位服務過本客戶神策系統', sec1);
  table(s, [['角色', '人', '負責'], ['產品', '1', '需求落差簽認、口徑與橋接表、驗收案例與手冊架構'], ['研發 R1 資料與查詢', '1', '接入、OneID、查詢編譯、ClickHouse 效能'], ['研發 R2 應用與前端', '1', 'API、分群標籤推送、權限、前端與示範站'], ['PM（服務過神策）', '1', '時程與退出條件、十項待確認、三方協作'], ['分析師 A（服務過神策）', '1', '神策盤點、必留清單、T1–T9 對帳'], ['分析師 B', '1', '教育訓練、看板重構、上線後交付調優'], ['運維（服務過神策）', '1', '神策導出、環境建置、監控與備份']], { y: 1.4, colW: [2.9, 0.6, 5.5], align: ['left', 'right', 'left'], rowH: 0.43 });
  s.addText('全員用 Claude Team 協同產出文件、對帳與測試；客戶端需要窗口、決策會與 UAT 人員', T({ x: 0.5, y: 4.85, w: 8.6, h: 0.3, fontSize: 12, color: HEX.muted })); }

// 12 風險
{ const s = content('四項主要風險都有門檻與退路，最大的一塊是真資料', sec1);
  table(s, [['風險', '對策'], ['真資料對不上', 'M1 就拿 1 個月真樣本，M2 跑 70 億，不等到 UAT 才發現'], ['規模未在 10 億級驗證', 'M2 做 10 億效能基準，實測後回填 SLA'], ['並行差異不收斂', '每日 T5／T6 對帳，差異 > 5% 開埋點修正單'], ['待確認項逾期', 'M0 決策會，每項附建議預設值與截止日']], { y: 1.5, colW: [3.0, 6.0], rowH: 0.62 }); }

// 13 來源授權
{ const s = content('全部採 OSI 開源授權，客戶要付的是基礎設施、GA360 與人力', sec1);
  [['必付授權', '0', '第一階段元件皆為開源授權'], ['copyleft 元件', '1', 'Grafana OSS，僅內部使用、不散布']].forEach(([l, v, d], i) => { const x = 0.5 + i * 2.35; card(s, x, 1.5, 2.15, 2.1, C.accent6, `s13_stat${i}`);
    s.addText(v, T({ x: x + 0.2, y: 1.65, w: 1.8, h: 0.75, fontSize: 36, bold: true, color: C.accent1 }));
    s.addText(l, T({ x: x + 0.2, y: 2.4, w: 1.8, h: 0.35, fontSize: 14, bold: true, color: C.text1 }));
    s.addText(d, T({ x: x + 0.2, y: 2.8, w: 1.8, h: 0.7, fontSize: 12, color: C.text1 })); });
  bullets(s, ['客戶付費：GCP 或機房、GA360 與 BigQuery 用量、LINE 訊息方案、專案人力', '可選付費：雲端託管、企業支援合約，不是必要條件', '每個元件的來源、授權、是否需商用付費列在規格書附錄 B'], { x: 5.4, y: 1.5, w: 4.1, h: 3.3, fontSize: 15 }); }

// 14 部署
{ const s = content('部署可選 GCP 或機房，同一套 Helm、監控、備份與 DR', sec1);
  [['GCP', ['區域 asia-east1', '物件儲存用 GCS', '用量計費，擴充快']], ['客戶機房', ['RKE2 Kubernetes', '物件儲存用 SeaweedFS', '資料不出機房']]].forEach(([h, items], i) => { const x = 0.5 + i * 4.6; card(s, x, 1.45, 4.4, 2.6, C.background1, `s14_card${i}`);
    s.addText(h, T({ x: x + 0.3, y: 1.65, w: 3.8, h: 0.5, fontSize: 20, bold: true, color: C.accent1 }));
    bullets(s, items, { x: x + 0.3, y: 2.3, w: 3.8, h: 1.6, fontSize: 15 }); });
  s.addText('機器檔位 A 檔 6 台或 D 檔 3 台，M0 決策會拍板', T({ x: 0.5, y: 4.3, w: 9.0, h: 0.4, fontSize: 15, bold: true, color: C.text1 })); }

// 15 M0 十項
{ const s = content('M0 要客戶拍板十項，11-13 前決定', sec1);
  const items = ['部署環境：GCP 或機房、機器檔位', '交易資料提供方式：批次或 Kafka', '身分：Entra ID／AD／Keycloak 與角色對照', '神策歷史導出方式與並行期長度', '隱私門檻：K 值、匯出上限、審計保存年限', '推送接收端規格與增量延遲', '自定義 SQL 開放對象與下載上限', 'AI 洞察的 LLM 供應方式與預算（二期）', '效能 SLA 在客戶規模實測', 'GA360 BigQuery 匯出區域與費用'];
  [0, 1].forEach((c) => { card(s, 0.5 + c * 4.6, 1.4, 4.4, 3.55, C.background1, `s15_col${c}`);
    s.addText(items.slice(c * 5, c * 5 + 5).map((t, i) => ({ text: `${c * 5 + i + 1}. ${t}`, options: { breakLine: i < 4, paraSpaceAfter: 10 } })), T({ x: 0.75 + c * 4.6, y: 1.6, w: 3.95, h: 3.2, fontSize: 14, color: C.text1 })); }); }

// 16 報價分隔
pres.addSection({ title: sec2 });
{ const s = pres.addSlide({ masterName: 'SECTION_DARK', sectionTitle: sec2 });
  s.addText('報價初版（未稅）', { placeholder: 'title' });
  s.addText('依人員分工與人天單價直算，供內部定價討論；客戶版等週一與 Merkle 對完分工再出', { placeholder: 'body' }); }

// 17 單價
{ const s = content('人天單價：產品與研發 25,000，分析師與 CSM 16,000', sec2);
  table(s, [['角色', '單價／人日', '本案對應'], ['PMO／產品經理', '25,000', '產品 1 人'], ['研發工程師', '25,000', '研發 R1、R2'], ['資料科學家', '20,000', '未編制；ClickHouse 顧問暫以此價計'], ['專案經理', '20,000', 'PM 1 人'], ['系統工程師', '20,000', '運維 1 人'], ['CSM', '16,000', '未編制；列為選配'], ['數據分析師', '16,000', '分析師 A、B']], { y: 1.4, colW: [2.8, 1.8, 4.4], align: ['left', 'right', 'left'], rowH: 0.42 });
  s.addText('單位：新台幣，未稅；不含基礎設施、GA360／BigQuery、LINE 訊息方案與差旅', T({ x: 0.5, y: 4.9, w: 8.6, h: 0.3, fontSize: 11, color: HEX.muted })); }

// 18 投入
{ const s = content('七人共 1,715 人日，M0–M5 開發期全員全職', sec2);
  table(s, [['階段', '期間', '每人人日', '投入'], ['M0–M5 開發', '2026-11-02 ～ 2027-03-26', '105', '七人全職'], ['緩衝與 UAT 準備', '2027-03-29 ～ 06-29', '65', '運維 0.5，其餘全職'], ['UAT 修正與正式環境', '2027-07-01 ～ 09-29', '65', '產品、分析師 A 0.5，其餘全職'], ['PD 後交付調優', '2027-10-01 ～ 11-30', '45', '分析師 B 全職，產品 0.3，其餘 0.5']], { y: 1.5, colW: [2.3, 3.0, 1.0, 2.7], align: ['left', 'left', 'right', 'left'], rowH: 0.6 });
  s.addText('每人人日以每週 5 個工作日計；FTE 依 docs/31 §2a 里程碑分工', T({ x: 0.5, y: 4.9, w: 8.6, h: 0.3, fontSize: 11, color: HEX.muted })); }

// 19 金額
{ const s = content('基本方案 36,334,000 元未稅，含 CSM 選配為 37,006,000 元', sec2);
  table(s, [['角色', '人日', '單價', '金額'], ['產品', '216', '25,000', '5,400,000'], ['研發 R1', '257', '25,000', '6,425,000'], ['研發 R2', '257', '25,000', '6,425,000'], ['專案經理', '257', '20,000', '5,140,000'], ['系統工程師（運維）', '224', '20,000', '4,480,000'], ['數據分析師 A', '224', '16,000', '3,584,000'], ['數據分析師 B', '280', '16,000', '4,480,000'], ['七人小計', '1,715', '', '35,934,000'], ['ClickHouse 顧問', '20', '20,000', '400,000'], ['基本方案合計', '', '', '36,334,000']], { y: 1.3, colW: [3.3, 1.5, 1.7, 2.5], align: ['left', 'right', 'right', 'right'], rowH: 0.3, fontSize: 13, highlight: [10] }); }

// 20 報價前要定
{ const s = content('出客戶版前還要定四件事', sec2);
  bullets(s, ['風險加成：本表未含，固定總價慣例加 10–15%', '付款節點建議：簽約 20%、M2 20%、M5 20%、UAT 20%、PD 15%、簽結 5%', '客戶自備：GA360、BigQuery 用量、GCP 或機房、LINE 訊息方案、內部人力', '與 Merkle 的分工線：目前不含埋點與 GA360 設定工時'], { x: 0.5, y: 1.45, w: 5.3, h: 3.5, fontSize: 15 });
  card(s, 6.1, 1.45, 3.4, 3.4, C.accent6, 's20_opt');
  s.addText('選配與後續', T({ x: 6.35, y: 1.65, w: 2.9, h: 0.4, fontSize: 16, bold: true, color: C.text1 }));
  s.addText([{ text: 'CSM 客戶成功', options: { bold: true, breakLine: true } }, { text: '672,000（42 人日）', options: { breakLine: true, paraSpaceAfter: 8 } }, { text: 'PD 後延長調優', options: { bold: true, breakLine: true } }, { text: '約 850,000／月', options: { breakLine: true, paraSpaceAfter: 8 } }, { text: '維運合約', options: { bold: true, breakLine: true } }, { text: '約 280,000／月', options: {} }], T({ x: 6.35, y: 2.15, w: 2.9, h: 2.55, fontSize: 14, color: C.text1 })); }

// 21 Merkle 九點
pres.addSection({ title: sec3 });
{ const s = content('週一會前會：與 Merkle 對齊九點', sec3);
  table(s, [['#', '題目', '要 Merkle 確認'], ['1', '分工線', '埋點至 BigQuery 匯出歸 Merkle，匯出之後歸 AltaTech'], ['2', '事件命名與橋接表', '橋接表作為埋點規範附件，變更雙方簽認'], ['3', 'BigQuery 匯出', '合約是否含匯出與串流；GCP 專案與付費方'], ['4', 'user_id 口徑', 'GTM 設定時機與來源；App SDK 由誰改'], ['5', '並行期與對帳', '埋點修正單 SLA；對帳報表格式'], ['6', '時程相依', 'GA360 上線日與匯出開通日（M3 前）'], ['7', '簡報分工', 'Merkle 是否自備 2–3 張'], ['8', '不在客戶面前爭的事', '同意「GA360 看 Google 口徑、DMP 看本地口徑」'], ['9', '商務', '雙方各自報價；採購時程配合']], { y: 1.3, colW: [0.45, 2.45, 6.1], align: ['right', 'left', 'left'], rowH: 0.34, fontSize: 13, boldFirst: false }); }

// 22 下一步
{ const s = content('下一步', sec3);
  [['10-19（一）', 'Merkle 會前會', '對齊九點，更新方案圖與交接點兩張'], ['10-23（五）', '客戶高階簡報', '三段現場操作、時程與 M0 十項'], ['10-26 起', '報價客戶版', '定加成與付款節點後送出'], ['11-02', 'Kick-off', 'M0 決策會議程與三方窗口']].forEach(([d, h, b], i) => { const x = 0.5 + i * 2.3; card(s, x, 1.5, 2.1, 3.0, i === 0 ? C.accent6 : C.background1, `s22_card${i}`);
    s.addText(d, T({ x: x + 0.2, y: 1.7, w: 1.7, h: 0.4, fontSize: 15, bold: true, color: C.accent1 }));
    s.addText(h, T({ x: x + 0.2, y: 2.2, w: 1.7, h: 0.75, fontSize: 16, bold: true, color: C.text1 }));
    s.addText(b, T({ x: x + 0.2, y: 3.0, w: 1.7, h: 1.35, fontSize: 13, color: C.text1 })); }); }

(async () => { const f = `${__dirname}/sinopac-dmp-proposal-v0.1.pptx`; await pres.writeFile({ fileName: f }); await applyTheme(f, THEME); console.log('wrote', f); })();
