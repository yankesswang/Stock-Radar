// Ops-center 暗色系（WarRoom / PostMarketReview 共用）—— 純常數與小工具，
// 不放元件，避免 vite fast refresh 整檔重載（react-refresh/only-export-components）。
//
// 色彩皆為語意色（多方=teal、防禦=red、警戒=amber、中性=accent），
// 且一律搭配文字徽章／圖示，不做 color-alone 編碼。

export const C = {
  bg: '#020617',
  panel: '#0F172A',
  border: '#1E293B',
  text: '#F8FAFC',
  sub: '#CBD5E1',
  // 小字標籤專用：#64748B 在 panel 底上只有 4.0:1，11px 以下的資訊文字改用這個（7:1）。
  dim: '#94A3B8',
  muted: '#64748B',
  long: '#26A69A',
  short: '#EF5350',
  amber: '#F59E0B',
  accent: '#22D3EE',
  mimi: '#A78BFA',
};

export const ACTION_META = {
  open: { label: 'OPEN', color: C.long },
  add: { label: 'ADD', color: C.long },
  buy: { label: 'BUY', color: C.long },
  accumulate: { label: 'ACCUM', color: C.long },
  hold: { label: 'HOLD', color: C.accent },
  watch: { label: 'WATCH', color: C.amber },
  trim: { label: 'TRIM', color: C.amber },
  reduce: { label: 'TRIM', color: C.amber },
  exit: { label: 'EXIT', color: C.short },
  stop_loss: { label: 'STOP', color: C.short },
  sell: { label: 'SELL', color: C.short },
  sell_short: { label: 'SHORT', color: C.short },
  avoid: { label: 'AVOID', color: C.muted },
};
export const actionMeta = (a) => ACTION_META[a] || { label: (a || '—').toUpperCase(), color: C.muted };

export const RISK_META = {
  low: { label: 'LOW', color: C.long },
  medium: { label: 'MED', color: C.amber },
  high: { label: 'HIGH', color: C.short },
};

export const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
export const signPct = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
export const mmdd = (iso) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : '—');
