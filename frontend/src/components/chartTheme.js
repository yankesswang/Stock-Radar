/**
 * Shared recharts styling.
 *
 * The charts were originally styled for a dark theme and kept white-on-white
 * strokes (rgba(255,255,255,0.04) grids, 0.06 axis lines) after the app moved to
 * the light palette in index.css — which rendered them invisible. These are
 * neutral slate alphas instead: they read against both light and dark
 * backgrounds, so they survive a future theme switch.
 *
 * WarRoom is intentionally excluded — it ships its own dark palette (`C.bg`).
 */

export const CHART_GRID = 'rgba(148,163,184,0.18)';   // gridlines: present, not loud
export const CHART_AXIS = 'rgba(148,163,184,0.35)';   // axis rules, matches --glass-border
export const CHART_REFERENCE = 'rgba(100,116,139,0.5)'; // benchmark lines: must be readable

export const chartTooltipStyle = {
    backgroundColor: 'var(--bg-primary)',
    border: '1px solid var(--glass-border)',
    borderRadius: '10px',
    fontSize: '12px',
    boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
};

export const chartTooltipLabelStyle = {
    color: 'var(--text-muted)',
    marginBottom: '4px',
};

/**
 * 類別色（同一個部門在摘要色點、堆疊圖、成長圖、利潤率線上都是同一個顏色）。
 *
 * 挑法跟 Macro 那頁同一套標準：對 --bg-primary 與 --bg-secondary 都 ≥3:1
 * （圖形元素的 WCAG 門檻），並用 Viénot 矩陣模擬 deuteranopia / protanopia 後
 * 檢查兩兩 ΔE —— 最小值 13.1（protanopia 下的藍/紫），還分得開。
 *
 * 固定順序、不循環：超過 6 個部門的公司在圖上併成「其他」，
 * 顏色不夠用就別再發一輪同色，那會讓兩個部門長得一樣。
 * 每條線／每塊面積在圖例與表格裡都有文字標籤，識別不靠顏色。
 */
export const SEGMENT_SERIES = ['#0052ff', '#d97706', '#0d9488', '#be185d', '#9333ea', '#4d7c0f'];
export const SEGMENT_OTHER = '#64748b';   // 併起來的尾巴，刻意是中性灰
