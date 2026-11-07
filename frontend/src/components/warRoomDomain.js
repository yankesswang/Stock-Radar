import { Scale, ShieldAlert, ShieldCheck } from 'lucide-react';
import { C as OPS, mixColor } from './opsTheme';

export const C = { ...OPS, news: OPS.accent };

export const REGIME = {
  risk_on: { label: '風險偏好', color: C.long, Icon: ShieldCheck },
  risk_off: { label: '避險', color: C.short, Icon: ShieldAlert },
  mixed: { label: '多空分歧', color: C.amber, Icon: Scale },
};

export const OTHER = 'OTHER';
export const normPortfolio = (portfolio) => (
  portfolio && String(portfolio).trim() ? String(portfolio) : OTHER
);
export const PORTFOLIO_LS_KEY = 'warroom.portfolio';
export const MARKET_SCOPE = '__MARKET__';
export const TRACKER_SCOPE = '__TRACKER__';

export const SETUP_META = {
  oversold_bounce: { label: '超賣反彈', short: '反彈', color: C.accent },
  undervalued_catalyst: { label: '低估＋催化劑', short: '低估', color: C.long },
  momentum_breakout: { label: '動能突破', short: '動能', color: C.amber },
  mimi_entry: { label: 'Mimi 進場區', short: 'Mimi', color: C.mimi },
  breakdown_short: { label: '破位放空', short: '放空', color: C.short },
  rs_leader: { label: '強勢回檔', short: '強勢', color: C.long },
};
export const SETUP_ORDER = [
  'oversold_bounce',
  'undervalued_catalyst',
  'momentum_breakout',
  'mimi_entry',
  'breakdown_short',
];
export const setupMeta = (setup) => (
  SETUP_META[setup] || { label: setup || '訊號', short: '訊號', color: C.muted }
);

export function oppActionStyle(action) {
  const styles = {
    buy: { color: C.long, label: '買入' },
    accumulate: { color: C.long, label: '加碼' },
    watch: { color: C.amber, label: '觀察' },
    sell_short: { color: C.short, label: '放空' },
    avoid: { color: C.muted, label: '避開' },
  };
  return styles[action] || styles.watch;
}

const OPP_ACTION_ORDER = { buy: 0, accumulate: 1, sell_short: 2, watch: 3, avoid: 9 };
export const oppActionRank = (action) => OPP_ACTION_ORDER[action] ?? 5;
export const OPP_ACTION_FILTERS = ['buy', 'accumulate', 'watch', 'avoid', 'sell_short'];
export const rrColor = (ratio) => (
  ratio == null ? C.muted : ratio >= 3 ? C.long : ratio >= 2 ? C.amber : C.muted
);

export const fmt = (value, digits = 2) => (
  value === null || value === undefined || Number.isNaN(value)
    ? '—'
    : Number(value).toFixed(digits)
);
export const pctColor = (value) => (
  value === null || value === undefined ? C.muted : value > 0 ? C.long : value < 0 ? C.short : C.muted
);
export const signPct = (value) => (
  value === null || value === undefined ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
);
export const heatColor = (percentage) => {
  if (percentage === null || percentage === undefined || Number.isNaN(percentage)) return C.border;
  const intensity = Math.min(Math.abs(percentage) / 3, 1);
  return mixColor(C.border, percentage >= 0 ? C.long : C.short, intensity);
};

const ACTION_LABEL_ZH = {
  add: '加倉', open: '建倉', buy: '買進', accumulate: '分批買進',
  trim: '減碼', reduce: '減碼', exit: '出場', stop_loss: '停損',
  sell: '賣出', sell_short: '放空', hold: '維持', watch: '觀察', avoid: '迴避',
};
export function actionStyle(action) {
  const label = ACTION_LABEL_ZH[action] || (action ? String(action).replace(/_/g, ' ') : '維持');
  if (action === 'add') return { color: C.long, label };
  if (['trim', 'exit', 'stop_loss'].includes(action)) return { color: C.short, label };
  if (action === 'open') return { color: C.accent, label };
  return { color: C.muted, label };
}

const ACTION_ORDER = { open: 0, add: 1, trim: 2, exit: 3, stop_loss: 4, hold: 9 };
export const actionRank = (action) => ACTION_ORDER[action] ?? ACTION_ORDER.hold;
export const ACTION_FILTERS = ['add', 'open', 'trim', 'exit', 'stop_loss', 'hold'];
export const normAction = (action) => action || 'hold';
