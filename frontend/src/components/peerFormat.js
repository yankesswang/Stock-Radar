/**
 * 同業比較的共用格式化 —— 純函式，不放元件
 * （元件檔一旦同時匯出常數，vite 的 fast refresh 會整檔重載；同 opsTheme.js 的理由）。
 *
 * 指標目錄與分組現在由後端給（/api/compare/peers 的 groups / stats），
 * 前端只負責怎麼顯示。加指標不用動前端。
 */

// 樣本太少就不談名次 —— 兩檔同業算出「第 1 便宜」只是把噪音講得很有信心
export const MIN_RANK_N = 5;

export function fmtNum(v, unit) {
  if (v == null) return '—';
  const n = Number(v);
  if (unit === '%') return `${n.toFixed(1)}%`;
  if (unit === 'x') return n.toFixed(Math.abs(n) >= 100 ? 0 : 2);
  // 無單位的是分數與計數（Piotroski 6、分析師 61），不要補小數
  return Number.isInteger(n) ? String(n) : n.toFixed(Math.abs(n) >= 100 ? 0 : 2);
}

export function fmtMoney(v) {
  if (v == null) return '—';
  return `$${Number(v).toFixed(2)}`;
}

export function fmtCap(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}

/**
 * 跟同業中位數差多少。
 *
 * 倍數（P/E 之類）講相對差：「比中位數便宜 46.9%」。
 * 百分比型指標講百分點：淨利率 62.9% vs 中位 16.5% 是「+46.5 pp」，
 * 不是「+282%」—— 而且回購殖利率的中位數是 -0.007%，拿它當分母會算出 +16000%。
 */
export function fmtDiff(stat) {
  const { unit, diff_vs_median_pct: pct, diff_vs_median_abs: abs } = stat;
  if (unit === 'x' && pct != null) return `${pct > 0 ? '+' : ''}${pct}%`;
  if (abs == null) return '—';
  const sign = abs > 0 ? '+' : '';
  return unit === '%' ? `${sign}${abs} pp` : `${sign}${abs}`;
}

// 「比中位數低」對倍數是好事、對 ROE 是壞事；中性指標（RSI、Beta）不判好壞
export function diffTone(stat) {
  const d = stat.diff_vs_median_abs;
  if (d == null || d === 0 || stat.higher_better == null) return null;
  return stat.higher_better ? d > 0 : d < 0;
}
