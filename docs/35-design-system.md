# 前端設計系統：token、元件庫、圖表

> 版本 v0.1（2026-10-17）。目的：介面不再每頁手排。先套分群、標籤兩頁（含共用的規則建構器），其餘頁面逐步換。

## 1. 組成

| 層 | 內容 | 位置 |
|---|---|---|
| Token | 色（品牌藍 #2C73EA 十階、灰階、系列色 s1–s8、狀態色）、字級（11.5／13／14／16／20）、間距（6／10／14／20／28）、圓角（6／8／12）、陰影；深淺色各一套 | `web/src/styles/app.css` 的 `:root` 變數；`web/src/ds/theme.ts` 把同一套值交給 Mantine |
| 元件庫 | Mantine 8（MIT）：TextInput／NativeSelect／Textarea／Button／Badge／Table／Paper／SegmentedControl／SimpleGrid／Alert／Collapse／List；預設尺寸 sm、圓角 md、卡片 lg | `@mantine/core`、`@mantine/hooks` |
| 共用元件 | PageHeader（標題＋導語＋動作）、SectionTitle、StatusBadge（blue／amber／red／gray／custom 語意對應）、StatTile、KeyValue、EmptyState、Mono | `web/src/ds/index.tsx` |
| 圖表 | Apache ECharts 6（Apache-2.0）：只註冊用到的圖型與元件（bar、grid、tooltip、legend）、SVG 渲染；配色與字色讀 app.css token，深淺色跟著走；`hbar()` 橫向長條（值分布、畫像） | `web/src/ds/Chart.tsx` |

## 2. 規則

- 頁面只組合設計系統元件與 Mantine 基礎元件；不再新增 `.row`／`.card`／`.pill` 這類手寫 class（舊頁面換過去時一併移除）。
- 表單欄位一律有 `label`；給測試與示範腳本用的 `aria-label` 保持原字串（例如「分群名稱」「行為 1 事件」）。
- 狀態一律用 StatusBadge 的語意色，不自選顏色；主要動作一個 `variant="filled"`，其餘 default／subtle。
- 圖表走 `Chart` 元件與 `chartBase()`，不直接 `echarts.init`；新圖型要先在 `Chart.tsx` 註冊。
- 新套件：LICENSES.md 加列、`npm audit` 結果寫進 PR。

## 3. 已換頁面

全站頁面都已換成設計系統元件（2026-10-18）：分析（事件、漏斗、留存、路徑、歸因、自定義 SQL）、用戶列表與細查、分群、標籤、名單推送、審批、總覽看板與書籤、元事件／用戶表／維度表、整合、遷移、權限中心、日誌統計、資料流與效能、系統監控、設定、登入、無權限頁與占位頁。

| 項目 | 做法 |
|---|---|
| 圖表 | 折線／長條（`analytics/LineChart.tsx`）、漏斗（`FunnelChart.tsx`）、路徑桑基圖（`PathChart.tsx`）、值分布與畫像（`hbar`）都走 ECharts；抑制列用斜線紋理，不只靠顏色 |
| 仍保留的 class | 外框（`.app`、`.topnav`、`.subnav`、`.wrap`）、登入版面（`.gate*`）、看板網格與拖曳（`.dash-grid`、`.dash-card.w2`、`.dragging`、`.dropzone`）、頁面根節點 `.page`；其餘元件 class 不再新增 |
| 測試選擇器 | 端到端測試用結構與標籤（`table tbody tr`、`getByLabel`、`getByText`），不依賴 `.tbl`、`.pill` |
| 打包 | Mantine 與 ECharts 拆成獨立 chunk（`vite.config.ts` 的 manualChunks）：主程式 645 KB、ECharts 579 KB、Mantine 270 KB（未壓縮） |

下一步：app.css 裡不再使用的元件 class 清掉；圖表加門檻線（MarkLine）給系統監控頁。
