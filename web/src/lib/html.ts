// 圖表提示框（ECharts tooltip 預設以 HTML 繪製）要放的資料字串一律先跳脫：分組名稱、步驟別名、節點名可能來自資料本身。
export function escapeHtml(v: unknown): string {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string);
}
