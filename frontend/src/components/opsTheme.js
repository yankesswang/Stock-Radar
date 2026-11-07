// Ops-center 色系（WarRoom / PostMarketReview / SignalWatchlist 共用）—— 純常數與小工具，
// 不放元件，避免 vite fast refresh 整檔重載（react-refresh/only-export-components）。
//
// 色彩皆為語意色（多方=teal、防禦=red、警戒=amber、中性=accent），
// 且一律搭配文字徽章／圖示，不做 color-alone 編碼。
//
// 值是 CSS 變數（index.css 的 --ops-*），跟著 <html data-theme> 切亮／暗，
// 不必重新 render。所以不能再用 `${C.x}33` 這種 hex 接 alpha —— 改用 alpha()。

const v = (name) => `var(--ops-${name})`;

export const C = {
  bg: v('bg'),
  panel: v('panel'),
  deep: v('deep'),
  border: v('border'),
  text: v('text'),
  sub: v('sub'),
  // 小字標籤專用：muted 在 panel 底上對比不夠，11px 以下的資訊文字改用這個。
  dim: v('dim'),
  muted: v('muted'),
  placeholder: v('placeholder'),
  ink: v('ink'),
  long: v('long'),
  short: v('short'),
  amber: v('amber'),
  accent: v('accent'),
  mimi: v('mimi'),
  hit: v('hit'),
  track: v('track'),
  signal: v('signal'),
  shadow: v('shadow'),
  backdrop: v('backdrop'),
};

/** 任何顏色（hex 或 var()）疊透明度，a 為 0–1。取代以前的 `${hex}33` 寫法。 */
export const alpha = (color, a) => `color-mix(in srgb, ${color} ${Math.round(a * 1000) / 10}%, transparent)`;

/** 兩色之間內插，t 為 0–1（t=0 → a、t=1 → b）。 */
export const mixColor = (a, b, t) => `color-mix(in srgb, ${b} ${Math.round(t * 1000) / 10}%, ${a})`;

// 動作／風險徽章一律中文：英文 enum（open / trim / stop_loss…）是 DECISIONS 的 schema，
// 給程式與 journal 用；使用者看到的那一面不留英文黑話（對照表同 scripts/zh_terms.py）。
export const ACTION_META = {
  open: { label: '建倉', color: C.long },
  add: { label: '加倉', color: C.long },
  buy: { label: '買進', color: C.long },
  accumulate: { label: '分批買進', color: C.long },
  hold: { label: '維持', color: C.accent },
  watch: { label: '觀察', color: C.amber },
  trim: { label: '減碼', color: C.amber },
  reduce: { label: '減碼', color: C.amber },
  exit: { label: '出場', color: C.short },
  stop_loss: { label: '停損', color: C.short },
  sell: { label: '賣出', color: C.short },
  sell_short: { label: '放空', color: C.short },
  avoid: { label: '迴避', color: C.muted },
};
export const actionMeta = (a) => ACTION_META[a] || { label: a ? String(a).replace(/_/g, ' ') : '—', color: C.muted };

export const RISK_META = {
  low: { label: '低風險', color: C.long },
  medium: { label: '中風險', color: C.amber },
  high: { label: '高風險', color: C.short },
};

export const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
export const signPct = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
export const mmdd = (iso) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : '—');
