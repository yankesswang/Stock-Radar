import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import Theme from '../theme';
import axios from '../lib/apiClient';
import {
  createChart, CrosshairMode, ColorType,
  CandlestickSeries, LineSeries, HistogramSeries, createSeriesMarkers,
} from 'lightweight-charts';
import {
  RefreshCw, Settings2, AlertTriangle, TrendingUp, TrendingDown, Minus,
  Layers, Gauge, Crosshair, Activity, ChevronDown,
} from 'lucide-react';

// ── Constants ────────────────────────────────────────────────────────────────
const PERIODS = [
  { label: '1M', value: '1m' },
  { label: '3M', value: '3m' },
  { label: '6M', value: '6m' },
  { label: '1Y', value: '1y' },
  { label: '2Y', value: '2y' },
  { label: '5Y', value: '5y' },
  { label: '10Y', value: '10y' },
  { label: 'MAX', value: 'max' },
];

// 紅／綠只留給 K 棒、趨勢線、支撐壓力（紅＝壓力、綠＝支撐）。均線以前 MA50 綠、
// MA250 紅，跟趨勢線撞色，分不出哪條是哪條 → 均線全部改用非紅綠的顏色，
// 長週期的畫粗一點。
const MA_DEFS = [
  { key: 'ma5',   label: 'MA5',   color: '#facc15', width: 1 },
  { key: 'ma10',  label: 'MA10',  color: '#38bdf8', width: 1 },
  { key: 'ma20',  label: 'MA20',  color: '#c084fc', width: 1 },
  { key: 'ma50',  label: 'MA50',  color: '#fb923c', width: 2 },
  { key: 'ma250', label: 'MA250', color: '#94a3b8', width: 2 },
];

// 財報日／持倉成本（資料來自 /chart-events）
const EARN_COLOR = '#f59e0b';
const COST_COLOR = '#e879f9';
// bar.time 是交易所當地午夜的 epoch（美股 ET 04:00/05:00 UTC、台股前一天 16:00 UTC），
// 加 12 小時再取 UTC 日期，兩邊都會落在正確的那一天。
const barDate = (t) => new Date((t + 43200) * 1000).toISOString().slice(0, 10);

// ── Price-panel overlays ─────────────────────────────────────────────────────
// `lines`   → LineSeries drawn on the candle chart
// `special` → custom draw routine (supertrend colouring, psar dots, level lines)
const OVERLAY_DEFS = [
  { key: 'earn',   label: '財報日',                desc: 'E 標記',        color: EARN_COLOR, special: 'earn' },
  { key: 'cost',   label: '持倉成本',              desc: '加權均價',       color: COST_COLOR, special: 'cost' },
  { key: 'sr',     label: 'Support / Resistance', desc: '',              color: '#34d399', special: 'sr' },
  { key: 'trend',  label: '趨勢線',                desc: '自動偵測',       color: '#38bdf8', special: 'trend' },
  { key: 'regch',  label: '回歸通道',              desc: '±1σ/±2σ',       color: '#a78bfa',
    lines: [{ f: 'reg_mid', c: '#a78bfa', width: 2 },
            { f: 'reg_u1', c: '#a78bfa', opacity: '55', style: 2 },
            { f: 'reg_l1', c: '#a78bfa', opacity: '55', style: 2 },
            { f: 'reg_u2', c: '#a78bfa', opacity: '99' },
            { f: 'reg_l2', c: '#a78bfa', opacity: '99' }] },
  { key: 'pattern', label: '形態辨識',             desc: 'M頭/W底/頭肩',   color: '#f472b6', special: 'pattern' },
  { key: 'bb',     label: 'Bollinger Bands',      desc: '(20,2)',        color: '#818cf8',
    lines: [{ f: 'bb_upper', style: 2 }, { f: 'bb_mid', opacity: '55' }, { f: 'bb_lower', style: 2 }] },
  { key: 'kc',     label: 'Keltner Channels',     desc: '(20,2×ATR)',    color: '#22d3ee',
    lines: [{ f: 'kc_upper', style: 2 }, { f: 'kc_mid', opacity: '55' }, { f: 'kc_lower', style: 2 }] },
  { key: 'dc',     label: 'Donchian',             desc: '(20)',          color: '#facc15',
    lines: [{ f: 'dc_upper' }, { f: 'dc_mid', opacity: '55', style: 2 }, { f: 'dc_lower' }] },
  { key: 'ema',    label: 'EMA',                  desc: '20/50/200',     color: '#93c5fd',
    lines: [{ f: 'ema20', c: '#93c5fd' }, { f: 'ema50', c: '#c4b5fd' }, { f: 'ema200', c: '#fcd34d', width: 2 }] },
  { key: 'vwap',   label: 'VWAP / Anchored',      desc: '20 + swing',    color: '#e2e8f0',
    lines: [{ f: 'vwap20', c: '#e2e8f0' },
            { f: 'avwap_high', c: '#f87171', style: 2 },
            { f: 'avwap_low',  c: '#34d399', style: 2 }] },
  { key: 'ichimoku', label: 'Ichimoku',           desc: '(9,26,52)',     color: '#f472b6',
    lines: [{ f: 'ich_conv', c: '#38bdf8' }, { f: 'ich_base', c: '#f472b6', width: 2 },
            { f: 'ich_a',    c: '#34d399', opacity: 'aa' }, { f: 'ich_b', c: '#f87171', opacity: 'aa' },
            { f: 'ich_lag',  c: '#a78bfa', style: 2 }] },
  { key: 'st',     label: 'SuperTrend',           desc: '(10,3)',        color: '#4ade80', special: 'supertrend' },
  { key: 'psar',   label: 'Parabolic SAR',        desc: '(0.02,0.2)',    color: '#fbbf24', special: 'psar' },
  { key: 'chand',  label: 'Chandelier Exit',      desc: '(22,3)',        color: '#fb7185',
    lines: [{ f: 'chand_long', c: '#34d399', style: 2 }, { f: 'chand_short', c: '#f87171', style: 2 }] },
  { key: 'pivots', label: 'Pivot Points',         desc: 'monthly',       color: '#c084fc', special: 'pivots' },
  { key: 'fib',    label: 'Fibonacci',            desc: 'auto swing',    color: '#fdba74', special: 'fib' },
  { key: 'vp',     label: 'Volume Profile',       desc: 'POC / VA',      color: '#5eead4', special: 'vp' },
  { key: 'td',     label: '神奇九轉',              desc: '低9 / 高9',     color: '#fbbf24', special: 'td' },
];

// ── Oscillator sub-panels ────────────────────────────────────────────────────
const PANEL_DEFS = [
  { key: 'macd',     label: 'MACD',              desc: '(12,26,9)',      color: '#60a5fa',
    lines: [{ f: 'macd', c: '#60a5fa' }, { f: 'signal', c: '#f59e0b' }],
    hist: { f: 'hist', pos: 'rgba(52,211,153,0.6)', neg: 'rgba(248,113,113,0.6)' } },
  { key: 'rsi',      label: 'RSI',               desc: '(14)',           color: '#fb923c',
    lines: [{ f: 'rsi', c: '#fb923c' }],
    levels: [{ v: 70, t: 'OB', c: 'rgba(248,113,113,0.5)' }, { v: 30, t: 'OS', c: 'rgba(52,211,153,0.5)' },
             { v: 50, c: 'rgba(148,163,184,0.2)', style: 4 }],
    note: '70 overbought · 30 oversold' },
  { key: 'stochrsi', label: 'Stochastic RSI',    desc: '(14,14,3,3)',    color: '#c084fc',
    lines: [{ f: 'stochrsi_k', c: '#c084fc' }, { f: 'stochrsi_d', c: '#f59e0b' }],
    levels: [{ v: 80, t: 'OB', c: 'rgba(248,113,113,0.5)' }, { v: 20, t: 'OS', c: 'rgba(52,211,153,0.5)' }],
    note: 'faster turns than plain RSI' },
  { key: 'adx',      label: 'ADX / DMI',         desc: '(14)',           color: '#38bdf8',
    lines: [{ f: 'adx', c: '#e2e8f0', width: 2 }, { f: 'di_plus', c: '#34d399' }, { f: 'di_minus', c: '#f87171' }],
    levels: [{ v: 25, t: 'Trend', c: 'rgba(226,232,240,0.4)', style: 2 },
             { v: 20, c: 'rgba(148,163,184,0.2)', style: 4 }],
    note: 'ADX ≥25 trending · +DI/−DI = direction' },
  { key: 'squeeze',  label: 'TTM Squeeze',       desc: 'BB in Keltner',  color: '#fbbf24',
    hist: { f: 'sqz_mom', pos: 'rgba(52,211,153,0.65)', neg: 'rgba(248,113,113,0.65)' },
    special: 'squeeze', note: '黃點=壓縮中 · 綠點=剛釋放' },
  { key: 'atr',      label: 'ATR % / HV / EWMA', desc: '(14 / 20d / λ.94)', color: '#fb7185',
    lines: [{ f: 'atrp', c: '#fb7185' }, { f: 'hv20', c: '#a78bfa' }, { f: 'ewma_vol', c: '#38bdf8', width: 2 }],
    note: 'EWMA 反應比固定窗口 HV 快' },
  { key: 'betaroll', label: '滾動 Beta / Alpha',  desc: '(63)',           color: '#fbbf24',
    lines: [{ f: 'beta_roll', c: '#fbbf24', width: 2 }, { f: 'alpha_roll', c: '#4ade80' }],
    levels: [{ v: 1, t: 'β=1', c: 'rgba(226,232,240,0.35)', style: 2 },
             { v: 0, c: 'rgba(148,163,184,0.2)', style: 4 }],
    note: '單一 Beta 看不出市場敏感度的變化' },
  { key: 'cci',      label: 'CCI',               desc: '(20)',           color: '#facc15',
    lines: [{ f: 'cci', c: '#facc15' }],
    levels: [{ v: 100, t: 'OB', c: 'rgba(248,113,113,0.5)' }, { v: -100, t: 'OS', c: 'rgba(52,211,153,0.5)' },
             { v: 0, c: 'rgba(148,163,184,0.2)', style: 4 }] },
  { key: 'mfi',      label: 'MFI',               desc: '(14)',           color: '#2dd4bf',
    lines: [{ f: 'mfi', c: '#2dd4bf' }],
    levels: [{ v: 80, t: 'OB', c: 'rgba(248,113,113,0.5)' }, { v: 20, t: 'OS', c: 'rgba(52,211,153,0.5)' }],
    note: '成交量加權的 RSI' },
  { key: 'cmf',      label: 'Chaikin Money Flow', desc: '(20)',          color: '#4ade80',
    hist: { f: 'cmf', pos: 'rgba(52,211,153,0.6)', neg: 'rgba(248,113,113,0.6)' },
    levels: [{ v: 0, c: 'rgba(148,163,184,0.25)', style: 4 }], note: '>0 買盤累積 · <0 派發' },
  { key: 'aroon',    label: 'Aroon',             desc: '(25)',           color: '#a3e635',
    lines: [{ f: 'aroon_up', c: '#34d399' }, { f: 'aroon_dn', c: '#f87171' }],
    levels: [{ v: 70, c: 'rgba(226,232,240,0.25)', style: 2 }, { v: 30, c: 'rgba(226,232,240,0.25)', style: 2 }] },
  { key: 'bbpct',    label: 'BB %B / Bandwidth', desc: '(20,2)',         color: '#818cf8',
    lines: [{ f: 'bb_pctb', c: '#818cf8' }, { f: 'bb_bw', c: '#f472b6' }],
    levels: [{ v: 100, c: 'rgba(248,113,113,0.4)', style: 2 }, { v: 0, c: 'rgba(52,211,153,0.4)', style: 2 }] },
  { key: 'willr',    label: 'Williams %R',       desc: '(14)',           color: '#f472b6',
    lines: [{ f: 'willr', c: '#f472b6' }],
    levels: [{ v: -20, t: 'OB', c: 'rgba(248,113,113,0.5)' }, { v: -80, t: 'OS', c: 'rgba(52,211,153,0.5)' }] },
  { key: 'rs',       label: 'RS vs SPY',         desc: 'Mansfield',      color: '#a3e635',
    lines: [{ f: 'rs', c: '#a3e635', width: 2 }],
    note: '上升 = 跑贏大盤（法人選股核心）' },
  { key: 'obv',      label: 'OBV',               desc: '',               color: '#2dd4bf',
    lines: [{ f: 'obv', c: '#2dd4bf' }] },
  { key: 'adl',      label: 'Accum / Dist Line', desc: '',               color: '#38bdf8',
    lines: [{ f: 'adl', c: '#38bdf8' }] },
  { key: 'kdj',      label: 'KDJ',               desc: '(9,3,3)',        color: '#a78bfa',
    lines: [{ f: 'k', c: '#60a5fa' }, { f: 'd', c: '#f59e0b' }, { f: 'j', c: '#f87171' }],
    levels: [{ v: 80, c: 'rgba(248,113,113,0.4)', style: 2 }, { v: 20, c: 'rgba(52,211,153,0.4)', style: 2 }] },
  { key: 'rvol',     label: 'Relative Volume',   desc: 'vs 20d',         color: '#fdba74',
    hist: { f: 'rvol', pos: 'rgba(253,186,116,0.6)', neg: 'rgba(253,186,116,0.6)' },
    levels: [{ v: 1, c: 'rgba(226,232,240,0.3)', style: 2 }], note: '>2 = 異常放量' },
];

// Legend labels for sub-panel series (raw field names read poorly in the UI).
const SERIES_LABEL = {
  macd: 'MACD', signal: 'Signal', hist: 'Hist', rsi: 'RSI',
  stochrsi_k: '%K', stochrsi_d: '%D', adx: 'ADX', di_plus: '+DI', di_minus: '−DI',
  atrp: 'ATR%', hv20: 'HV20', cci: 'CCI', mfi: 'MFI', cmf: 'CMF',
  aroon_up: 'Aroon↑', aroon_dn: 'Aroon↓', bb_pctb: '%B', bb_bw: 'Bandwidth',
  willr: '%R', rs: 'RS', obv: 'OBV', adl: 'A/D', k: 'K', d: 'D', j: 'J',
  rvol: 'RVOL', sqz_mom: 'Momentum', td_setup: '九轉',
  ewma_vol: 'EWMA', beta_roll: 'Beta', alpha_roll: 'Alpha',
};
const seriesLabel = (f) => SERIES_LABEL[f] ?? f;

const DEFAULT_ACTIVE = {
  sr: true, trend: true, regch: false, pattern: false,
  macd: true, rsi: true, adx: true, betaroll: false,
  bb: false, kc: false, dc: false, ema: false, vwap: false, ichimoku: false,
  st: false, psar: false, chand: false, pivots: false, fib: false, vp: false,
  squeeze: false, atr: false, cci: false, mfi: false, cmf: false, aroon: false,
  bbpct: false, willr: false, rs: false, obv: false, adl: false, kdj: false, rvol: false,
  td: true, earn: true, cost: true,
};

// 圖面顏色來自 index.css 的 --chart-* token：亮色下維持原本的深色圖面，
// 暗色下跟卡片同色系。lightweight-charts 吃的是 canvas，不會自己跟 CSS 變數，
// 所以在建圖時讀一次、主題切換時再 applyOptions 一次（見下方 useEffect）。
const CHART_FALLBACK = {
  bg: '#0f172a', grid: 'rgba(255,255,255,0.04)', border: 'rgba(255,255,255,0.08)', text: '#94a3b8',
};
function chartPalette() {
  if (typeof window === 'undefined') return CHART_FALLBACK;
  const cs = getComputedStyle(document.documentElement);
  const pick = (name, fb) => (cs.getPropertyValue(name) || '').trim() || fb;
  return {
    bg: pick('--chart-bg', CHART_FALLBACK.bg),
    grid: pick('--chart-grid', CHART_FALLBACK.grid),
    border: pick('--chart-border', CHART_FALLBACK.border),
    text: pick('--chart-text', CHART_FALLBACK.text),
  };
}
// 圖例／指標按鈕的邊框與文字色（非 canvas，套用一次即可；hover 態走 tailwind token）
const BORDER = 'var(--chart-border)';
const TEXT   = 'var(--chart-text)';

function themedChartOpts(pal) {
  return {
    layout: { background: { type: ColorType.Solid, color: pal.bg }, textColor: pal.text },
    grid:   { vertLines: { color: pal.grid }, horzLines: { color: pal.grid } },
    rightPriceScale: { borderColor: pal.border },
    timeScale: { borderColor: pal.border },
  };
}

function chartOpts(height, extraTS = {}) {
  const pal = chartPalette();
  const t = themedChartOpts(pal);
  return {
    layout: t.layout,
    grid: t.grid,
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: t.rightPriceScale,
    timeScale: { ...t.timeScale, timeVisible: true, secondsVisible: false, ...extraTS },
    height,
  };
}

// ── Alert engine ─────────────────────────────────────────────────────────────
// severity: 'bearish' | 'bullish' | 'neutral'
function computeAlerts(bars, analytics) {
  if (bars.length < 2) return [];
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  const alerts = [];
  const add = (indicator, type, severity, value, desc) =>
    alerts.push({ indicator, type, severity, value, desc });
  const has = (...keys) => keys.every(k => last[k] != null && prev[k] != null);

  // ── Classic oscillators ────────────────────────────────────────────────────
  if (last.rsi != null) {
    if (last.rsi >= 70)      add('RSI', 'Overbought', 'bearish', last.rsi.toFixed(1), `RSI ${last.rsi.toFixed(1)} 高於 70`);
    else if (last.rsi <= 30) add('RSI', 'Oversold',   'bullish', last.rsi.toFixed(1), `RSI ${last.rsi.toFixed(1)} 低於 30`);
    else if (prev.rsi != null && last.rsi >= 60 && prev.rsi < 60)
      add('RSI', 'Rising', 'neutral', last.rsi.toFixed(1), 'RSI 上穿 60，動能轉強');
    else if (prev.rsi != null && last.rsi <= 40 && prev.rsi > 40)
      add('RSI', 'Falling', 'neutral', last.rsi.toFixed(1), 'RSI 下穿 40，動能轉弱');
  }

  if (last.willr != null) {
    if (last.willr >= -20)      add('Williams %R', 'Overbought', 'bearish', last.willr.toFixed(1), `%R ${last.willr.toFixed(1)} 高於 -20`);
    else if (last.willr <= -80) add('Williams %R', 'Oversold',   'bullish', last.willr.toFixed(1), `%R ${last.willr.toFixed(1)} 低於 -80`);
  }

  if (last.j != null) {
    if (last.j >= 80)      add('KDJ', 'Overbought', 'bearish', `J=${last.j.toFixed(1)}`, `J 線 ${last.j.toFixed(1)} 高於 80`);
    else if (last.j <= 20) add('KDJ', 'Oversold',   'bullish', `J=${last.j.toFixed(1)}`, `J 線 ${last.j.toFixed(1)} 低於 20`);
  }
  if (has('k', 'd')) {
    if (prev.k <= prev.d && last.k > last.d) add('KDJ', 'Golden Cross', 'bullish', `K=${last.k.toFixed(1)}`, 'K 線上穿 D 線');
    else if (prev.k >= prev.d && last.k < last.d) add('KDJ', 'Death Cross', 'bearish', `K=${last.k.toFixed(1)}`, 'K 線下穿 D 線');
  }

  // ── 神奇九轉 / TD Setup ─────────────────────────────────────────────────────
  if (last.td_setup != null && last.td_setup !== 0) {
    const n = Math.abs(last.td_setup);
    const buy = last.td_setup < 0;
    if (n >= 9)
      add('神奇九轉', buy ? '低9 買訊' : '高9 賣訊', buy ? 'bullish' : 'bearish', `${n}/9`,
          buy ? '連 9 根收盤低於 4 根前 — 賣壓衰竭，留意反轉'
              : '連 9 根收盤高於 4 根前 — 追價力竭，留意回檔');
    else if (n >= 6)
      add('神奇九轉', buy ? '買訊醞釀' : '賣訊醞釀', 'neutral', `${n}/9`,
          `${buy ? '低' : '高'}九計數 ${n}/9，還差 ${9 - n} 根`);
  }

  if (has('macd', 'signal')) {
    if (prev.macd <= prev.signal && last.macd > last.signal)
      add('MACD', 'Bullish Crossover', 'bullish', last.macd.toFixed(3), 'MACD 上穿訊號線');
    else if (prev.macd >= prev.signal && last.macd < last.signal)
      add('MACD', 'Bearish Crossover', 'bearish', last.macd.toFixed(3), 'MACD 下穿訊號線');
    if (has('hist')) {
      if (prev.hist < 0 && last.hist >= 0)      add('MACD', 'Histogram Flip ↑', 'bullish', last.hist.toFixed(3), '柱狀圖翻正');
      else if (prev.hist > 0 && last.hist <= 0) add('MACD', 'Histogram Flip ↓', 'bearish', last.hist.toFixed(3), '柱狀圖翻負');
    }
  }

  // ── Bollinger ──────────────────────────────────────────────────────────────
  if (last.bb_upper != null && last.close != null) {
    if (last.close >= last.bb_upper)
      add('Bollinger Bands', 'Upper Band Touch', 'bearish', last.close.toFixed(2), `價格 ≥ 上軌 (${last.bb_upper.toFixed(2)})`);
    else if (last.bb_lower != null && last.close <= last.bb_lower)
      add('Bollinger Bands', 'Lower Band Touch', 'bullish', last.close.toFixed(2), `價格 ≤ 下軌 (${last.bb_lower.toFixed(2)})`);
    const bw = last.bb_upper - last.bb_lower;
    const prev10 = bars.slice(-11, -1);
    if (prev10.length === 10 && prev10.every(b => b.bb_upper != null)) {
      const avgBw = prev10.reduce((s, b) => s + (b.bb_upper - b.bb_lower), 0) / 10;
      if (bw < avgBw * 0.7)
        add('Bollinger Bands', 'Squeeze', 'neutral', bw.toFixed(2), '帶寬收縮 — 醞釀突破');
    }
  }

  // ── SuperTrend flip ────────────────────────────────────────────────────────
  if (has('st_dir')) {
    if (prev.st_dir < 0 && last.st_dir > 0)
      add('SuperTrend', 'Flip ↑', 'bullish', last.st?.toFixed(2), `轉多，停損參考 ${last.st?.toFixed(2)}`);
    else if (prev.st_dir > 0 && last.st_dir < 0)
      add('SuperTrend', 'Flip ↓', 'bearish', last.st?.toFixed(2), `轉空，反壓參考 ${last.st?.toFixed(2)}`);
  }

  // ── TTM Squeeze fired ──────────────────────────────────────────────────────
  if (has('sqz_on')) {
    if (prev.sqz_on === 1 && last.sqz_on === 0)
      add('TTM Squeeze', last.sqz_mom >= 0 ? 'Fired ↑' : 'Fired ↓',
          last.sqz_mom >= 0 ? 'bullish' : 'bearish',
          last.sqz_mom?.toFixed(2), '壓縮釋放 — 波動放大起點');
    else if (last.sqz_on === 1)
      add('TTM Squeeze', 'Squeezing', 'neutral', last.sqz_mom?.toFixed(2), '布林在肯特納內 — 醞釀中');
  }

  // ── ADX trend regime ───────────────────────────────────────────────────────
  if (last.adx != null && last.di_plus != null && last.di_minus != null) {
    if (last.adx >= 25)
      add('ADX', last.di_plus > last.di_minus ? 'Strong Uptrend' : 'Strong Downtrend',
          last.di_plus > last.di_minus ? 'bullish' : 'bearish',
          last.adx.toFixed(1), `ADX ${last.adx.toFixed(1)} — 趨勢明確`);
    else if (last.adx < 20)
      add('ADX', 'Range / No Trend', 'neutral', last.adx.toFixed(1), `ADX ${last.adx.toFixed(1)} — 盤整，趨勢策略失效`);
  }

  // ── Stochastic RSI / MFI / CCI ─────────────────────────────────────────────
  if (last.stochrsi_k != null) {
    if (last.stochrsi_k >= 80)      add('Stoch RSI', 'Overbought', 'bearish', last.stochrsi_k.toFixed(1), 'Stoch RSI 高於 80');
    else if (last.stochrsi_k <= 20) add('Stoch RSI', 'Oversold',   'bullish', last.stochrsi_k.toFixed(1), 'Stoch RSI 低於 20');
  }
  if (last.mfi != null) {
    if (last.mfi >= 80)      add('MFI', 'Overbought', 'bearish', last.mfi.toFixed(1), '資金流入過熱 (>80)');
    else if (last.mfi <= 20) add('MFI', 'Oversold',   'bullish', last.mfi.toFixed(1), '資金流出過度 (<20)');
  }
  if (last.cci != null) {
    if (last.cci >= 100)       add('CCI', 'Overbought', 'bearish', last.cci.toFixed(0), 'CCI 高於 +100');
    else if (last.cci <= -100) add('CCI', 'Oversold',   'bullish', last.cci.toFixed(0), 'CCI 低於 -100');
  }
  if (last.cmf != null) {
    if (last.cmf >= 0.2)       add('CMF', 'Accumulation', 'bullish', last.cmf.toFixed(2), '籌碼持續累積');
    else if (last.cmf <= -0.2) add('CMF', 'Distribution', 'bearish', last.cmf.toFixed(2), '籌碼持續派發');
  }

  // ── Ichimoku cloud break ───────────────────────────────────────────────────
  if (last.ich_a != null && last.ich_b != null && last.close != null && prev.ich_a != null) {
    const top = Math.max(last.ich_a, last.ich_b), bot = Math.min(last.ich_a, last.ich_b);
    const pTop = Math.max(prev.ich_a, prev.ich_b), pBot = Math.min(prev.ich_a, prev.ich_b);
    if (prev.close <= pTop && last.close > top)
      add('Ichimoku', 'Cloud Breakout ↑', 'bullish', last.close.toFixed(2), '價格突破雲層上緣');
    else if (prev.close >= pBot && last.close < bot)
      add('Ichimoku', 'Cloud Breakdown ↓', 'bearish', last.close.toFixed(2), '價格跌破雲層下緣');
  }

  // ── PSAR flip ──────────────────────────────────────────────────────────────
  if (has('psar', 'close')) {
    if (prev.close < prev.psar && last.close > last.psar)
      add('Parabolic SAR', 'Flip ↑', 'bullish', last.psar.toFixed(2), 'SAR 翻多');
    else if (prev.close > prev.psar && last.close < last.psar)
      add('Parabolic SAR', 'Flip ↓', 'bearish', last.psar.toFixed(2), 'SAR 翻空');
  }

  // ── MA golden / death cross (50 × 250) ─────────────────────────────────────
  if (has('ma50', 'ma250')) {
    if (prev.ma50 <= prev.ma250 && last.ma50 > last.ma250)
      add('Moving Average', 'Golden Cross', 'bullish', last.ma50.toFixed(2), 'MA50 上穿 MA250');
    else if (prev.ma50 >= prev.ma250 && last.ma50 < last.ma250)
      add('Moving Average', 'Death Cross', 'bearish', last.ma50.toFixed(2), 'MA50 下穿 MA250');
  }

  // ── Volume / gap anomalies ─────────────────────────────────────────────────
  if (last.rvol != null && last.rvol >= 2)
    add('Volume', 'Volume Spike', last.close >= last.open ? 'bullish' : 'bearish',
        `${last.rvol.toFixed(1)}x`, `成交量為 20 日均量 ${last.rvol.toFixed(1)} 倍`);
  if (last.gap_pct != null && Math.abs(last.gap_pct) >= 2)
    add('Price Action', last.gap_pct > 0 ? 'Gap Up' : 'Gap Down',
        last.gap_pct > 0 ? 'bullish' : 'bearish',
        `${last.gap_pct.toFixed(1)}%`, `跳空 ${last.gap_pct.toFixed(1)}%`);

  // ── 52-week extremes (from analytics) ──────────────────────────────────────
  const st = analytics?.stats;
  if (st?.pct_from_high != null && st.pct_from_high >= -1)
    add('Price Action', '52W High', 'bullish', st.high_52w, '創 52 週新高附近');
  if (st?.pct_from_low != null && st.pct_from_low <= 1)
    add('Price Action', '52W Low', 'bearish', st.low_52w, '創 52 週新低附近');
  if (st?.rs_mansfield != null) {
    if (st.rs_mansfield > 0.5)
      add('Relative Strength', 'Outperforming', 'bullish', st.rs_mansfield.toFixed(2), 'Mansfield RS > 0，強於大盤');
    else if (st.rs_mansfield < -0.5)
      add('Relative Strength', 'Underperforming', 'bearish', st.rs_mansfield.toFixed(2), 'Mansfield RS < 0，弱於大盤');
  }

  // ── Trend structure: the drawn lines, not the computed series ─────────────
  const tr = analytics?.trend;
  if (tr && !tr.error && tr.regime && !tr.regime.trendable) {
    // No trend to break out of: reporting "跌破上升支撐線" on a mean-reverting
    // series would be inventing structure that isn't there.
    add('趨勢型態', tr.regime.label, 'neutral', `${tr.regime.score}/100`, tr.regime.reason);
    if (tr.range) {
      const r = tr.range;
      add('區間位置',
        r.position_pct >= 85 ? '接近區間上緣' : r.position_pct <= 15 ? '接近區間下緣' : '區間中段',
        r.position_pct >= 85 ? 'bearish' : r.position_pct <= 15 ? 'bullish' : 'neutral',
        `${r.position_pct}%`,
        `近 ${r.lookback} 根區間 $${r.low}–$${r.high}，中位 $${r.median}`);
    }
  } else if (tr && !tr.error) {
    const close = last.close;
    if (tr.regime) add('趨勢型態', tr.regime.label, 'neutral', `${tr.regime.score}/100`, tr.regime.reason);
    const all = [...(tr.trendlines?.resistance ?? []), ...(tr.trendlines?.support ?? [])];

    // A line broken inside the visible tail is the single most actionable thing
    // on the chart — it is a change of structure, not a reading.
    all.filter(l => l.status === 'broken' && l.broken_time != null)
      .sort((a, b) => b.broken_time - a.broken_time)
      .slice(0, 2)
      .forEach(l => {
        const up = l.side === 'resistance';
        add('趨勢線', up ? '突破壓力線' : '跌破支撐線', up ? 'bullish' : 'bearish',
            `$${l.value_now}`,
            `${l.label}（${l.touches} 次觸及）已被收盤價${up ? '突破' : '跌破'}`);
      });

    // Approach alerts — within one ATR-ish band of an intact line.
    const nearBand = 3;
    all.filter(l => l.status === 'intact' && Math.abs(l.dist_pct ?? 99) <= nearBand)
      .sort((a, b) => Math.abs(a.dist_pct) - Math.abs(b.dist_pct))
      .slice(0, 2)
      .forEach(l => {
        const res = l.side === 'resistance';
        add('趨勢線', res ? '逼近壓力線' : '測試支撐線', res ? 'bearish' : 'bullish',
            `$${l.value_now}`,
            `距 ${l.label} ${Math.abs(l.dist_pct).toFixed(1)}%，該線已守住 ${l.touches} 次`);
      });

    if (tr.channel) {
      const c = tr.channel;
      const pos = c.position_pct;
      const sev = c.bias === 'bullish' ? 'bullish' : c.bias === 'bearish' ? 'bearish' : 'neutral';
      add('形態', c.label, sev, `${pos}%`,
          `${c.label}（寬 ${c.width_pct}%），現價在通道 ${pos}% 位置，量測目標 $${c.target}`);
    }

    (tr.patterns ?? []).forEach(pt => {
      add('形態', `${pt.label}${pt.status === 'confirmed' ? '（已確認）' : '（成形中）'}`,
          pt.bias === 'bullish' ? 'bullish' : 'bearish',
          `$${pt.neckline}`,
          pt.status === 'confirmed'
            ? `頸線 $${pt.neckline} 已突破，量測目標 $${pt.target}`
            : `頸線 $${pt.neckline}（${pt.neck_dist_pct > 0 ? '上方' : '下方'} ${Math.abs(pt.neck_dist_pct).toFixed(1)}%），突破後目標 $${pt.target}`);
    });

    const rg = tr.regression;
    if (rg?.position_pct != null && close != null) {
      if (rg.position_pct >= 92)
        add('回歸通道', '通道上緣', 'bearish', `${rg.position_pct}%`,
            `價格在 +2σ 附近（$${rg.upper}），統計上偏貴`);
      else if (rg.position_pct <= 8)
        add('回歸通道', '通道下緣', 'bullish', `${rg.position_pct}%`,
            `價格在 −2σ 附近（$${rg.lower}），統計上偏便宜`);
    }
  }

  return alerts;
}

const SEVERITY_STYLE = {
  bearish: { bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.3)',  color: '#f87171', icon: TrendingDown },
  bullish: { bg: 'rgba(52,211,153,0.08)',  border: 'rgba(52,211,153,0.3)',   color: '#34d399', icon: TrendingUp },
  neutral: { bg: 'rgba(251,191,36,0.08)',  border: 'rgba(251,191,36,0.25)',  color: '#fbbf24', icon: Minus },
};

const RATING_STYLE = {
  'Strong Buy':  { color: '#22c55e', bg: 'rgba(34,197,94,0.14)',  border: 'rgba(34,197,94,0.45)' },
  'Buy':         { color: '#4ade80', bg: 'rgba(74,222,128,0.12)', border: 'rgba(74,222,128,0.35)' },
  'Neutral':     { color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', border: 'rgba(148,163,184,0.3)' },
  'Sell':        { color: '#fb7185', bg: 'rgba(251,113,133,0.12)',border: 'rgba(251,113,133,0.35)' },
  'Strong Sell': { color: '#ef4444', bg: 'rgba(239,68,68,0.14)',  border: 'rgba(239,68,68,0.45)' },
};

const REGIME_TONE = {
  '趨勢明確': '#34d399',
  '趨勢偏弱': '#fbbf24',
  '區間震盪': '#94a3b8',
  '均值回歸': '#60a5fa',
};

const withAlpha = (hex, a) => (a ? `${hex}${a}` : hex);

// Volatility regime — a percentile against the stock's OWN trailing distribution,
// not a cross-sectional comparison.
const VOL_REGIME_TONE = {
  extreme:    '#f87171',
  elevated:   '#fbbf24',
  normal:     undefined,
  compressed: '#60a5fa',
};
const VOL_REGIME_LABEL = {
  extreme:    '極端波動',
  elevated:   '波動偏高',
  normal:     '波動正常',
  compressed: '波動壓縮',
};

// 0-100 percentile pill: colour ramps from calm blue to hot red.
const pctTone = (p) =>
  p == null ? undefined : p >= 90 ? '#f87171' : p >= 70 ? '#fbbf24' : p <= 10 ? '#60a5fa' : '#94a3b8';


// ── Presentational pieces (module scope so they aren't remounted each render) ──
const PanelLabel = ({ children, extra }) => (
  <div className="px-4 py-1 flex items-center gap-3 text-[11px] font-semibold uppercase tracking-wider bg-[rgba(255,255,255,0.015)] border-t border-[var(--glass-border)]">
    {children}
    {extra}
  </div>
);

const IndicatorButton = ({ k, label, desc, color, on, onToggle }) => (
  <button onClick={() => onToggle(k)}
    className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border transition-all ${on ? 'opacity-100' : 'opacity-40 hover:opacity-70'}`}
    style={{ borderColor: on ? color : BORDER, background: on ? `${color}22` : 'transparent', color: on ? color : TEXT }}>
    <span className="w-2 h-2 rounded-full" style={{ background: color }} />
    {label}
    {desc && <span className="opacity-60">{desc}</span>}
  </button>
);

const StatCell = ({ label, value, suffix = '', tone, hint }) => (
  <div className="px-3 py-2 rounded-lg bg-[rgba(255,255,255,0.02)] border border-[var(--glass-border)]" title={hint}>
    <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">{label}</div>
    <div className="text-sm font-mono font-bold mt-0.5" style={{ color: tone ?? 'var(--text-primary)' }}>
      {value == null ? '—' : `${value}${suffix}`}
    </div>
  </div>
);

// Score ∈ [-1, 1] rendered as a bar growing left/right from centre.
const RatingBar = ({ title, node }) => {
  if (!node) return null;
  const s = RATING_STYLE[node.label] ?? RATING_STYLE.Neutral;
  const pct = ((node.score + 1) / 2) * 100;
  return (
    <div className="flex-1 min-w-[150px] px-3 py-2.5 rounded-xl border"
      style={{ background: s.bg, borderColor: s.border }}>
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">{title}</div>
      <div className="text-base font-bold mt-0.5" style={{ color: s.color }}>{node.label}</div>
      <div className="mt-2 h-1.5 rounded-full bg-[rgba(148,163,184,0.15)] relative overflow-hidden">
        <div className="absolute inset-y-0 w-px bg-[rgba(226,232,240,0.4)]" style={{ left: '50%' }} />
        <div className="absolute inset-y-0 rounded-full"
          style={{
            background: s.color,
            left: node.score >= 0 ? '50%' : `${pct}%`,
            width: `${Math.abs(node.score) * 50}%`,
          }} />
      </div>
      {node.buy != null && (
        <div className="flex gap-2 mt-1.5 text-[10px] font-mono">
          <span className="text-[#34d399]">買 {node.buy}</span>
          <span className="text-[var(--text-muted)]">中 {node.neutral}</span>
          <span className="text-[#f87171]">賣 {node.sell}</span>
        </div>
      )}
    </div>
  );
};

// ── 指數頁的技術分析重點（chartFirst 用） ─────────────────────────────────────
// 密度取向：不做卡中卡、不做進度條，數字一律等寬字，靠少量 1px 線分組。
// 左欄：價位階梯（現價上下最近三個壓力／支撐，來源合併 S/R、趨勢線、月樞紐）＋ 型態與通道；
// 右欄：評級投票 ＋ 風險與波動。這裡只放結論，明細留在「完整技術分析」。

const BIAS_META = {
  bullish: { label: '偏多', cls: 'text-[var(--accent-success-text)]' },
  bearish: { label: '偏空', cls: 'text-[var(--accent-danger-text)]' },
  neutral: { label: '中性', cls: 'text-[var(--text-meta)]' },
};
const PATTERN_STATUS = { confirmed: '已確認', forming: '形成中', failed: '失效', broken: '已突破' };
const ratingCls = (label) =>
  /Buy/.test(label || '') ? 'text-[var(--accent-success-text)]'
  : /Sell/.test(label || '') ? 'text-[var(--accent-danger-text)]'
  : 'text-[var(--text-meta)]';

const fmtLevel = (v) => (v == null ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtSigned = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);

// 把三種來源的價位合成一條階梯：現價上方取最近 3 個壓力、下方取最近 3 個支撐。
// 相距 0.25% 內的視為同一價位，來源標籤合併，避免同一位置出現兩列。
function buildLadder(close, srData, analytics) {
  if (!close) return { above: [], below: [] };
  const raw = [];
  (srData?.resistance ?? []).forEach(l => raw.push({ price: l.price, src: `S/R 觸及 ${l.touches}` }));
  (srData?.support ?? []).forEach(l => raw.push({ price: l.price, src: `S/R 觸及 ${l.touches}` }));
  // Trendlines only earn a rung when the regime says this series respects them.
  // On a mean-reverting series a "下降支撐線 觸及 18" is confinement inside a
  // range, not a level anyone defends — listing it invents structure.
  if (analytics?.trend?.regime?.trendable !== false) {
    const tl = analytics?.trend?.trendlines;
    [...(tl?.resistance ?? []), ...(tl?.support ?? [])]
      .filter(l => l.status === 'intact' && l.value_now != null)
      .forEach(l => raw.push({ price: l.value_now, src: `${l.label} 觸及 ${l.touches}` }));
  }
  const pv = analytics?.pivots?.monthly?.classic;
  if (pv) ['r2', 'r1', 'p', 's1', 's2'].forEach(k => pv[k] != null && raw.push({ price: pv[k], src: `月樞紐 ${k.toUpperCase()}` }));

  const merged = [];
  raw.filter(r => Number.isFinite(Number(r.price))).sort((a, b) => a.price - b.price).forEach(r => {
    const last = merged[merged.length - 1];
    if (last && Math.abs(r.price - last.price) / last.price < 0.0025) last.src = `${last.src}，${r.src}`;
    else merged.push({ ...r });
  });
  const withDist = merged.map(r => ({ ...r, dist: (r.price - close) / close * 100 }));
  return {
    above: withDist.filter(r => r.dist > 0).slice(0, 3).reverse(),   // 由高到低排，貼近現價的在最下
    below: withDist.filter(r => r.dist <= 0).slice(-3).reverse(),    // 由近到遠
  };
}

const LadderRow = ({ kind, price, dist, src }) => (
  <li className="grid grid-cols-[3rem_1fr_4.5rem] items-baseline gap-x-3 py-1.5 sm:grid-cols-[3rem_7.5rem_4.5rem_1fr]">
    <span className={`text-xs font-semibold ${kind === '壓力' ? 'text-[var(--accent-danger-text)]' : 'text-[var(--accent-success-text)]'}`}>{kind}</span>
    <span className="font-mono text-sm font-semibold tabular-nums text-[var(--text-primary)]">{fmtLevel(price)}</span>
    <span className="text-right font-mono text-xs tabular-nums text-[var(--text-meta)]">{fmtSigned(dist)}</span>
    <span className="col-span-3 truncate text-[11px] text-[var(--text-meta)] sm:col-span-1" title={src}>{src}</span>
  </li>
);

const DigestHeading = ({ children }) => (
  <h3 className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">{children}</h3>
);

const RiskItem = ({ label, value, note }) => (
  <div className="py-1.5">
    <dt className="text-[11px] text-[var(--text-meta)]">{label}</dt>
    <dd className="font-mono text-sm font-semibold tabular-nums text-[var(--text-primary)]">
      {value}{note && <span className="ml-1.5 font-sans text-[11px] font-normal text-[var(--text-meta)]">{note}</span>}
    </dd>
  </div>
);

function TechDigest({ close, srData, analytics }) {
  const stats = analytics?.stats;
  const rating = analytics?.rating;
  const trend = analytics?.trend && !analytics.trend.error ? analytics.trend : null;
  const ladder = buildLadder(close, srData, analytics);
  const channel = trend?.channel;
  const patterns = (trend?.patterns ?? []).slice(0, 2);
  const reg = trend?.regression;
  const nearestUp = ladder.above[ladder.above.length - 1];
  const nearestDown = ladder.below[0];
  const regime = stats?.percentiles?.vol_regime;
  const trendRegime = trend?.regime ?? null;
  const trendable = !trendRegime || trendRegime.trendable;
  const range = trend?.range;

  return (
    <div className="px-4 py-3">
      {/* 沒有趨勢時，這個判定就是結論——先講，再講價位 */}
      {!trendable && trendRegime && (
        <p className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-[var(--text-body)]">
          <span className="font-semibold text-[var(--accent-warning-text,#b45309)]">
            {trendRegime.label}
          </span>
          <span className="font-mono text-xs tabular-nums text-[var(--text-meta)]">
            趨勢分數 {trendRegime.score}/100
          </span>
          <span className="text-xs text-[var(--text-meta)]">
            趨勢線與型態已停用；
            {range && <>近 {range.lookback} 根區間 {fmtLevel(range.low)}–{fmtLevel(range.high)}（中位 {fmtLevel(range.median)}），現價位於 {Number(range.position_pct).toFixed(0)}%</>}
          </span>
        </p>
      )}

      {/* 一句話結論：型態偏向 ＋ 最近支撐／壓力 */}
      {(channel || nearestUp || nearestDown) && (
        <p className="mb-4 text-sm text-[var(--text-body)]">
          {channel && <>{channel.label}<span className={`ml-1 font-semibold ${BIAS_META[channel.bias]?.cls ?? ''}`}>{BIAS_META[channel.bias]?.label ?? ''}</span>，</>}
          {nearestDown && <>最近支撐 <span className="font-mono font-semibold tabular-nums">{fmtLevel(nearestDown.price)}</span>（{fmtSigned(nearestDown.dist)}）</>}
          {nearestDown && nearestUp && '，'}
          {nearestUp && <>最近壓力 <span className="font-mono font-semibold tabular-nums">{fmtLevel(nearestUp.price)}</span>（{fmtSigned(nearestUp.dist)}）</>}
        </p>
      )}

      <div className="grid gap-x-8 gap-y-6 lg:grid-cols-12">
        {/* 左欄 */}
        <div className="space-y-6 lg:col-span-7">
          <section>
            <DigestHeading>價位階梯</DigestHeading>
            {ladder.above.length + ladder.below.length === 0 ? (
              <p className="text-xs text-[var(--text-meta)]">這個區間沒有偵測到可用的支撐或壓力。</p>
            ) : (
              <ul>
                {ladder.above.map(r => <LadderRow key={`a${r.price}`} kind="壓力" {...r} />)}
                <li className="my-1 grid grid-cols-[3rem_1fr] items-baseline gap-x-3 border-y border-[var(--glass-border)] py-1.5">
                  <span className="text-xs font-semibold text-[var(--text-primary)]">現價</span>
                  <span className="font-mono text-sm font-bold tabular-nums text-[var(--text-primary)]">{fmtLevel(close)}</span>
                </li>
                {ladder.below.map(r => <LadderRow key={`b${r.price}`} kind="支撐" {...r} />)}
              </ul>
            )}
          </section>

          {(channel || patterns.length > 0 || reg || trendRegime) && (
            <section>
              <DigestHeading>型態與通道</DigestHeading>
              <ul className="space-y-2 text-sm">
                {trendRegime && (
                  <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                    <span className="font-semibold text-[var(--text-primary)]">趨勢判定</span>
                    <span className={`text-xs font-semibold ${trendable ? 'text-[var(--accent-success-text)]' : 'text-[var(--text-meta)]'}`}>
                      {trendRegime.label}
                    </span>
                    <span className="font-mono text-xs tabular-nums text-[var(--text-meta)]"
                      title="R²（價格是否沿著一條線走）與趨勢線實際彈開幅度合成；ADX 刻意不計入——波動指數的 ADX 反而比績優股高">
                      {trendRegime.score}/100
                      {trendRegime.bounce_median_atr != null && <>，趨勢線平均彈開 {trendRegime.bounce_median_atr}×ATR</>}
                      {trendRegime.vr20 != null && <>，VR(20) {trendRegime.vr20}</>}
                    </span>
                  </li>
                )}
                {channel && (
                  <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                    <span className="font-semibold text-[var(--text-primary)]">{channel.label}</span>
                    <span className={`text-xs font-semibold ${BIAS_META[channel.bias]?.cls ?? ''}`}>{BIAS_META[channel.bias]?.label}</span>
                    <span className="font-mono text-xs tabular-nums text-[var(--text-meta)]">
                      區間 {fmtLevel(channel.lower)} - {fmtLevel(channel.upper)}，現價位於 {Number(channel.position_pct).toFixed(0)}%
                      {channel.target != null && <>，量測目標 {fmtLevel(channel.target)}</>}
                    </span>
                  </li>
                )}
                {patterns.map((pt, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                    <span className="font-semibold text-[var(--text-primary)]">{pt.label}</span>
                    <span className={`text-xs font-semibold ${BIAS_META[pt.bias]?.cls ?? ''}`}>{BIAS_META[pt.bias]?.label}</span>
                    <span className="text-xs text-[var(--text-meta)]">{PATTERN_STATUS[pt.status] ?? pt.status}</span>
                    <span className="font-mono text-xs tabular-nums text-[var(--text-meta)]">
                      頸線 {fmtLevel(pt.neckline)}{pt.target != null && <>，目標 {fmtLevel(pt.target)}</>}
                    </span>
                  </li>
                ))}
                {reg && (
                  <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                    <span className="font-semibold text-[var(--text-primary)]">回歸通道</span>
                    <span className="font-mono text-xs tabular-nums text-[var(--text-meta)]">
                      斜率 {fmtSigned(reg.slope_per_year_pct)}/年，R² {Number(reg.r2).toFixed(2)}，現價位於 {Number(reg.position_pct).toFixed(0)}%
                    </span>
                  </li>
                )}
              </ul>
            </section>
          )}
        </div>

        {/* 右欄 */}
        <div className="space-y-6 lg:col-span-5 lg:border-l lg:border-[var(--glass-border)] lg:pl-8">
          {rating && (
            <section>
              <DigestHeading>評級投票</DigestHeading>
              <ul className="space-y-1.5 text-sm">
                {[['綜合', rating.overall], ['均線', rating.moving_averages], ['擺盪', rating.oscillators]].map(([name, node]) => node && (
                  <li key={name} className="grid grid-cols-[3rem_1fr_auto] items-baseline gap-x-3">
                    <span className="text-xs text-[var(--text-meta)]">{name}</span>
                    <span className={`font-semibold ${ratingCls(node.label)}`}>{node.label}</span>
                    {node.buy != null && (
                      <span className="font-mono text-xs tabular-nums text-[var(--text-meta)]">
                        {node.buy} 買 / {node.neutral} 中 / {node.sell} 賣
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {stats && (
            <section>
              <DigestHeading>風險與波動</DigestHeading>
              <dl className="grid grid-cols-2 gap-x-6">
                <RiskItem label="ATR %" value={stats.atrp != null ? `${stats.atrp}%` : '—'} />
                <RiskItem label="HV 20D" value={stats.hv20 != null ? `${stats.hv20}%` : '—'} />
                <RiskItem label="EWMA 波動" value={stats.ewma_vol != null ? `${stats.ewma_vol}%` : '—'}
                  note={regime ? (VOL_REGIME_LABEL[regime] ?? regime) : undefined} />
                <RiskItem label="Beta（對 SPY）" value={stats.beta ?? '—'}
                  note={stats.beta_now != null ? `近期 ${stats.beta_now}` : undefined} />
                <RiskItem label="Sharpe" value={stats.sharpe ?? '—'} />
                <RiskItem label="Sortino" value={stats.sortino ?? '—'} />
                <RiskItem label="最大回撤" value={stats.max_drawdown != null ? `${stats.max_drawdown}%` : '—'}
                  note={stats.current_drawdown != null ? `目前 ${stats.current_drawdown}%` : undefined} />
                <RiskItem label="52 週區間" value={`${fmtLevel(stats.low_52w)} - ${fmtLevel(stats.high_52w)}`} />
              </dl>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────
// `chartFirst` — 指數／市場頁用：走勢圖置頂，圖層設定與技術分析改成折疊區（預設收起），
// 主要資訊一眼可見，進階內容按需展開。個股頁維持原本「控制列 → 分析 → 圖」的順序。
// `onAnalytics` — 資料載入後回傳 { analytics, lastBar, period, interval }，
// 讓外層頁面（指數頁）能在圖表上方擺自己的技術指標摘要，不用再抓一次 chart API。
export default function PriceChartPanel({ ticker, chartFirst = false, onAnalytics }) {
  const onAnalyticsRef = useRef(onAnalytics);
  useEffect(() => { onAnalyticsRef.current = onAnalytics; }, [onAnalytics]);
  const [period,    setPeriod]    = useState('5y');
  const [interval,  setInterval]  = useState('day');
  const [bars,      setBars]      = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState(null);
  const [hoverBar,  setHoverBar]  = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const barMap = useMemo(() => {
    const m = new Map();
    bars.forEach(b => m.set(b.time, b));
    return m;
  }, [bars]);

  // Opt-in escape hatch: when the regime says the series doesn't trend, the
  // lines are still computed but not drawn. Some people want to look anyway.
  const [forceTrend, setForceTrend] = useState(false);
  const [showMA, setShowMA] = useState(Object.fromEntries(MA_DEFS.map(m => [m.key, true])));
  const [activeIndicators, setActiveIndicators] = useState(DEFAULT_ACTIVE);
  const [srData, setSrData] = useState({ resistance: [], support: [] });
  const [rawEvents, setEvents] = useState(null);   // { ticker, earnings: {past, upcoming}, positions }
  // 換股票時舊的那份直接作廢，不用在 effect 裡先清空
  const events = rawEvents && rawEvents.ticker === ticker?.toUpperCase() ? rawEvents : null;

  useEffect(() => {
    if (!ticker) return;
    let alive = true;
    axios.get(`/api/stocks/${encodeURIComponent(ticker)}/chart-events`)
      .then(res => { if (alive) setEvents(res.data); })
      .catch(() => {});
    return () => { alive = false; };
  }, [ticker]);

  // Refs for DOM containers
  const containerRef = useRef(null);
  const priceRef     = useRef(null);
  const volRef       = useRef(null);
  const panelEls     = useRef({});   // panel key → div element

  const chartsRef  = useRef({});
  const seriesRef  = useRef({});
  // Read inside the chart effect, which deliberately does NOT rebuild on MA
  // toggles — those flip series visibility in place.
  const showMARef  = useRef(showMA);
  useEffect(() => { showMARef.current = showMA; }, [showMA]);

  const activePanels = useMemo(
    () => PANEL_DEFS.filter(p => activeIndicators[p.key]),
    [activeIndicators]
  );

  // ── Cache: keyed by `${ticker}__${period}__${interval}` ───────────────
  const cacheRef = useRef({});
  const cacheKey = (t, p, iv) => `${t}__${p}__${iv}`;

  // period/interval 用參數帶進來，applyData 才不會閉包到 state（exhaustive-deps）
  function applyData(data, p, iv) {
    setForceTrend(false);
    const nextBars = data.bars ?? [];
    setBars(nextBars);
    const sr = data.sr ?? { resistance: [], support: [] };
    setSrData(sr);
    setAnalytics(data.analytics ?? null);
    onAnalyticsRef.current?.({
      analytics: data.analytics ?? null,
      lastBar: nextBars.length ? nextBars[nextBars.length - 1] : null,
      period: p,
      interval: iv,
    });
  }

  // Long lookback + daily interval is heavy; auto-switch to weekly.
  useEffect(() => {
    if ((period === '10y' || period === 'max') && interval === 'day') setInterval('week');
  }, [period, interval]);

  // ── Fetch (with cache + background prefetch) ───────────────────────────
  useEffect(() => {
    if (!ticker) return;
    setError(null);
    setHoverBar(null);

    Object.keys(cacheRef.current).forEach(k => {
      if (!k.startsWith(`${ticker}__`)) delete cacheRef.current[k];
    });

    const key = cacheKey(ticker, period, interval);

    if (cacheRef.current[key]) {
      applyData(cacheRef.current[key], period, interval);
      setLoading(false);
    } else {
      setLoading(true);
      axios.get(`/api/stocks/${encodeURIComponent(ticker)}/chart`, { params: { period, interval } })
        .then(res => {
          cacheRef.current[key] = res.data;
          applyData(res.data, period, interval);
          setLoading(false);
        })
        .catch(err => { setError(err.response?.data?.detail ?? 'Failed to load data'); setLoading(false); });
    }

    const others = ['day', 'week', 'month'].filter(iv => iv !== interval);
    others.forEach(iv => {
      const bgKey = cacheKey(ticker, period, iv);
      if (!cacheRef.current[bgKey]) {
        axios.get(`/api/stocks/${encodeURIComponent(ticker)}/chart`, { params: { period, interval: iv } })
          .then(res => { cacheRef.current[bgKey] = res.data; })
          .catch(() => {});
      }
    });
  }, [ticker, period, interval]);

  // ── Build charts ───────────────────────────────────────────────────────
  useEffect(() => {
    if (loading || error || !bars.length) return;

    Object.values(chartsRef.current).forEach(c => { try { c.remove(); } catch { /* chart already disposed */ } });
    chartsRef.current = {};
    seriesRef.current = {};

    const makeChart = (el, height, hideTimeScale = false) => {
      if (!el) return null;
      return createChart(el, {
        ...chartOpts(height, hideTimeScale ? { visible: false } : {}),
        width: el.clientWidth,
        handleScale: { mouseWheel: true, pinch: true },
        handleScroll: { mouseWheel: true, pressedMouseMove: true },
      });
    };

    const active = activeIndicators;
    const an = analytics;
    const rawTrend = an?.trend && !an.trend.error ? an.trend : null;
    // A trendline on a series that doesn't trend is a drawing, not analysis.
    const trend = (rawTrend?.regime && !rawTrend.regime.trendable && !forceTrend)
      ? null : rawTrend;

    // Trendlines are projected forward, which adds time slots the candles don't
    // have. The panels are synced by *logical* range, so every chart has to grow
    // by the same number of slots — otherwise the sub-panels slide out of step
    // with the price panel. Whitespace bars do exactly that.
    const lastTime = bars[bars.length - 1].time;
    const barSec = trend?.bar_seconds
      ?? (bars.length > 1 ? bars[bars.length - 1].time - bars[bars.length - 2].time : 86400);
    const nTrendLines = (trend?.trendlines?.resistance?.length ?? 0)
      + (trend?.trendlines?.support?.length ?? 0);
    const projBars = (active.trend && nTrendLines) ? (trend.proj_bars ?? 10) : 0;
    const ws = Array.from({ length: projBars }, (_, i) => ({ time: lastTime + barSec * (i + 1) }));
    const pad = (arr) => (ws.length ? arr.concat(ws) : arr);

    // ── Price panel ──────────────────────────────────────────────────────
    const pc = makeChart(priceRef.current, 380, false);
    if (pc) {
      chartsRef.current.price = pc;

      // ── 財報日：整根高度的淡色直條（另一個隱藏價格軸上的柱狀圖）＋K 棒上方的 E ──
      // 直條要比 K 棒先加，才會畫在 K 棒後面。
      const earnTimes = [];
      if (active.earn && events?.earnings?.past?.length) {
        const dates = bars.map(b => barDate(b.time));
        const hit = new Set();
        events.earnings.past.forEach(ev => {
          if (ev.date < dates[0]) return;          // 區間起點之前的財報
          // 落在「日期 ≤ 財報日」的最後一根：日線就是當天，週／月線是包含那天的那根
          let lo = 0, hi = dates.length - 1, idx = -1;
          while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (dates[mid] <= ev.date) { idx = mid; lo = mid + 1; } else hi = mid - 1;
          }
          if (idx >= 0) hit.add(bars[idx].time);
        });
        earnTimes.push(...[...hit].sort((a, b) => a - b));
        if (earnTimes.length) {
          const band = pc.addSeries(HistogramSeries, {
            priceScaleId: 'earn', lastValueVisible: false, priceLineVisible: false,
            color: `${EARN_COLOR}2e`,
          });
          band.priceScale().applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
          band.setData(earnTimes.map(time => ({ time, value: 1 })));
          seriesRef.current.earn_band = band;
        }
      }

      const candle = pc.addSeries(CandlestickSeries, {
        upColor: '#34d399', downColor: '#f87171',
        borderUpColor: '#34d399', borderDownColor: '#f87171',
        wickUpColor: '#34d399', wickDownColor: '#f87171',
      });
      candle.setData(pad(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }))));
      seriesRef.current.candle = candle;

      // Simple MAs
      MA_DEFS.forEach(({ key, color, width }) => {
        const s = pc.addSeries(LineSeries, {
          color, lineWidth: width, crosshairMarkerVisible: false,
          lastValueVisible: false, priceLineVisible: false, visible: showMARef.current[key],
        });
        s.setData(bars.filter(b => b[key] != null).map(b => ({ time: b.time, value: b[key] })));
        seriesRef.current[key] = s;
      });

      // Generic overlay line groups
      OVERLAY_DEFS.filter(o => o.lines && active[o.key]).forEach(o => {
        o.lines.forEach(ln => {
          const s = pc.addSeries(LineSeries, {
            color: withAlpha(ln.c ?? o.color, ln.opacity),
            lineWidth: ln.width ?? 1,
            lineStyle: ln.style ?? 0,
            crosshairMarkerVisible: false, lastValueVisible: false,
            priceLineVisible: false,
          });
          s.setData(bars.filter(b => b[ln.f] != null).map(b => ({ time: b.time, value: b[ln.f] })));
          seriesRef.current[`ov_${o.key}_${ln.f}`] = s;
        });
      });

      // SuperTrend — one line, per-point colour by direction
      if (active.st) {
        const s = pc.addSeries(LineSeries, {
          color: '#4ade80', lineWidth: 2, crosshairMarkerVisible: false,
          lastValueVisible: false, priceLineVisible: false,
        });
        s.setData(bars.filter(b => b.st != null).map(b => ({
          time: b.time, value: b.st,
          color: b.st_dir > 0 ? '#34d399' : '#f87171',
        })));
        seriesRef.current.ov_st = s;
      }

      // Parabolic SAR — dots, not a line
      if (active.psar) {
        const s = pc.addSeries(LineSeries, {
          color: '#fbbf24', lineVisible: false,
          pointMarkersVisible: true, pointMarkersRadius: 1.6,
          crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
        });
        s.setData(bars.filter(b => b.psar != null).map(b => ({
          time: b.time, value: b.psar,
          color: b.close != null && b.close >= b.psar ? '#34d399' : '#f87171',
        })));
        seriesRef.current.ov_psar = s;
      }

      // 全部 K 棒標記（九轉、財報 E）排序後一次設定
      const candleMarks = earnTimes.map(time => ({
        time, position: 'aboveBar', color: EARN_COLOR, shape: 'square', text: 'E', size: 0.6,
      }));

      // 神奇九轉 — only the completed 9s are drawn; marking every bar of every
      // run buries the candles under text. The running count lives in the alert
      // strip and the hover readout instead.
      if (active.td) {
        const marks = bars
          .filter(b => Math.abs(b.td_setup ?? 0) >= 9)
          .map(b => (b.td_setup <= -9
            ? { time: b.time, position: 'belowBar', color: '#34d399', shape: 'arrowUp',   text: '低9' }
            : { time: b.time, position: 'aboveBar', color: '#f87171', shape: 'arrowDown', text: '高9' }));
        candleMarks.push(...marks);
      }

      if (candleMarks.length) {
        createSeriesMarkers(candle, candleMarks.sort((a, b) => a.time - b.time));
      }

      // ── 持倉成本：每個組合一條（跨帳戶已加權平均）──
      if (active.cost && events?.positions?.length) {
        events.positions.forEach(pos => candle.createPriceLine({
          price: pos.avg_cost, color: COST_COLOR, lineWidth: 2, lineStyle: 2,
          axisLabelVisible: true,
          title: events.positions.length > 1 ? `成本 ${pos.portfolio}` : '成本',
        }));
      }

      // Support / Resistance price lines
      // 價格軸標籤重疊時 lightweight-charts 會把標籤推開，S/R 一多成本標籤就被擠到
      // 離線好幾格的地方。所以只有強度 ≥3 的 S/R 才掛軸標籤，且離成本 1.5% 內的不掛。
      if (active.sr) {
        const { resistance, support } = srData;
        const costs = active.cost ? (events?.positions ?? []).map(p => p.avg_cost) : [];
        const srLabel = lvl => lvl.strength >= 3
          && !costs.some(c => Math.abs(lvl.price - c) / c < 0.015);
        resistance.forEach(lvl => candle.createPriceLine({
          price: lvl.price, color: `rgba(248,113,113,${0.35 + lvl.strength * 0.1})`,
          lineWidth: lvl.strength >= 3 ? 2 : 1, lineStyle: 0, axisLabelVisible: srLabel(lvl), title: '',
        }));
        support.forEach(lvl => candle.createPriceLine({
          price: lvl.price, color: `rgba(52,211,153,${0.35 + lvl.strength * 0.1})`,
          lineWidth: lvl.strength >= 3 ? 2 : 1, lineStyle: 0, axisLabelVisible: srLabel(lvl), title: '',
        }));
      }

      // ── Auto trendlines ────────────────────────────────────────────────
      // Drawn as two segments: solid through the anchors up to today, dashed
      // for the forward projection — so what price actually respected is never
      // confused with where the line will be next month.
      if (active.trend && trend?.trendlines) {
        const lines = [...(trend.trendlines.resistance ?? []), ...(trend.trendlines.support ?? [])];
        lines.forEach((l, i) => {
          const broken = l.status === 'broken';
          const base = l.side === 'resistance' ? '#f87171' : '#34d399';
          const width = Math.min(3, Math.max(1, Math.round((l.strength ?? 2) / 1.7)));

          // lightweight-charts rejects duplicate/unordered times, and p2 lands
          // on the last bar whenever the newest pivot anchors the line.
          const pts = [l.p1, l.p2, l.end]
            .filter(Boolean)
            .sort((a, b) => a.time - b.time)
            .filter((pt, idx, arr) => idx === 0 || pt.time > arr[idx - 1].time)
            .map(pt => ({ time: pt.time, value: pt.price }));

          if (pts.length >= 2) {
            const seg = pc.addSeries(LineSeries, {
              color: broken ? `${base}66` : base,
              lineWidth: width, lineStyle: broken ? 2 : 0,
              crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
            });
            seg.setData(pts);
            seriesRef.current[`tl_${i}`] = seg;

            if (l.touch_times?.length) {
              createSeriesMarkers(seg, l.touch_times.map(t => ({
                time: t, position: l.side === 'resistance' ? 'aboveBar' : 'belowBar',
                color: `${base}bb`, shape: 'circle',
              })));
            }
          }

          if (l.proj && l.end && l.proj.time > l.end.time) {
            const fwd = pc.addSeries(LineSeries, {
              color: `${base}${broken ? '33' : '77'}`,
              lineWidth: width, lineStyle: 2,
              crosshairMarkerVisible: false, priceLineVisible: false,
              lastValueVisible: true,
              title: `${l.label} ×${l.touches}${broken ? ' 破' : ''}`,
            });
            fwd.setData([
              { time: l.end.time, value: l.end.price },
              { time: l.proj.time, value: l.proj.price },
            ]);
            seriesRef.current[`tlf_${i}`] = fwd;
          }
        });
      }

      // ── Chart patterns: pivot polyline + neckline + measured target ─────
      if (active.pattern && trend?.patterns?.length) {
        trend.patterns.forEach((pat, i) => {
          const tone = pat.bias === 'bullish' ? '#34d399' : pat.bias === 'bearish' ? '#f87171' : '#f472b6';
          const pts = (pat.points ?? [])
            .filter((pt, idx, arr) => idx === 0 || pt.time > arr[idx - 1].time)
            .map(pt => ({ time: pt.time, value: pt.price }));
          if (pts.length >= 3) {
            const s2 = pc.addSeries(LineSeries, {
              color: `${tone}cc`, lineWidth: 2, lineStyle: 0,
              crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
            });
            s2.setData(pts);
            createSeriesMarkers(s2, [{
              time: pts[Math.floor(pts.length / 2)].time,
              position: pat.bias === 'bearish' ? 'aboveBar' : 'belowBar',
              color: tone, shape: pat.bias === 'bearish' ? 'arrowDown' : 'arrowUp',
              text: pat.label,
            }]);
            seriesRef.current[`pat_${i}`] = s2;
          }
          if (pat.neckline != null) candle.createPriceLine({
            price: pat.neckline, color: `${tone}cc`, lineWidth: 1, lineStyle: 3,
            axisLabelVisible: true, title: '頸線',
          });
          if (pat.target != null) candle.createPriceLine({
            price: pat.target, color: `${tone}77`, lineWidth: 1, lineStyle: 4,
            axisLabelVisible: true, title: '目標',
          });
        });
      }

      // Pivot points (monthly, classic)
      if (active.pivots && an?.pivots?.monthly?.classic) {
        const p = an.pivots.monthly.classic;
        [['R3', p.r3, '#f87171'], ['R2', p.r2, '#f87171'], ['R1', p.r1, '#fb923c'],
         ['P',  p.p,  '#c084fc'],
         ['S1', p.s1, '#4ade80'], ['S2', p.s2, '#34d399'], ['S3', p.s3, '#34d399']]
          .forEach(([title, price, color]) => {
            if (price == null) return;
            candle.createPriceLine({
              price, color, lineWidth: title === 'P' ? 2 : 1, lineStyle: 3,
              axisLabelVisible: true, title,
            });
          });
      }

      // Fibonacci retracement
      if (active.fib && an?.fib?.levels) {
        an.fib.levels.forEach(l => candle.createPriceLine({
          price: l.price, color: 'rgba(253,186,116,0.55)', lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: l.label,
        }));
      }

      // Volume profile POC / value area
      if (active.vp && an?.volume_profile) {
        const vp = an.volume_profile;
        [['POC', vp.poc, 'rgba(94,234,212,0.9)', 2], ['VAH', vp.vah, 'rgba(94,234,212,0.45)', 1],
         ['VAL', vp.val, 'rgba(94,234,212,0.45)', 1]]
          .forEach(([title, price, color, w]) => {
            if (price == null) return;
            candle.createPriceLine({ price, color, lineWidth: w, lineStyle: 0, axisLabelVisible: true, title });
          });
      }

      pc.subscribeCrosshairMove(param => {
        if (!param?.time) { setHoverBar(null); return; }
        const hit = barMap.get(param.time);
        if (hit) setHoverBar(hit);
      });
    }

    // ── Volume panel ─────────────────────────────────────────────────────
    const vc = makeChart(volRef.current, 90, true);
    if (vc) {
      chartsRef.current.volume = vc;
      const vs = vc.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' });
      vs.priceScale().applyOptions({ scaleMarginTop: 0.1, scaleMarginBottom: 0 });
      vs.setData(pad(bars.map(b => ({
        time: b.time, value: b.volume,
        color: b.close >= b.open ? 'rgba(52,211,153,0.5)' : 'rgba(248,113,113,0.5)',
      }))));
      seriesRef.current.volume = vs;
    }

    // ── Oscillator sub-panels (data-driven) ──────────────────────────────
    PANEL_DEFS.filter(p => active[p.key]).forEach(p => {
      const chart = makeChart(panelEls.current[p.key], 100, true);
      if (!chart) return;
      chartsRef.current[p.key] = chart;

      if (p.hist) {
        const h = chart.addSeries(HistogramSeries, { lastValueVisible: false, priceLineVisible: false });
        h.setData(pad(bars.filter(b => b[p.hist.f] != null).map(b => ({
          time: b.time, value: b[p.hist.f],
          color: b[p.hist.f] >= 0 ? p.hist.pos : p.hist.neg,
        }))));
        seriesRef.current[`p_${p.key}_${p.hist.f}`] = h;
      }

      let firstLine = null;
      (p.lines ?? []).forEach(ln => {
        const s = chart.addSeries(LineSeries, {
          color: withAlpha(ln.c ?? p.color, ln.opacity),
          lineWidth: ln.width ?? 1.5,
          lastValueVisible: false, priceLineVisible: false,
        });
        s.setData(pad(bars.filter(b => b[ln.f] != null).map(b => ({ time: b.time, value: b[ln.f] }))));
        seriesRef.current[`p_${p.key}_${ln.f}`] = s;
        firstLine = firstLine ?? s;
      });

      // TTM Squeeze dots along the zero line
      if (p.special === 'squeeze') {
        const dots = chart.addSeries(LineSeries, {
          color: '#fbbf24', lineVisible: false,
          pointMarkersVisible: true, pointMarkersRadius: 2,
          lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
        });
        dots.setData(bars.filter(b => b.sqz_on != null).map(b => ({
          time: b.time, value: 0, color: b.sqz_on === 1 ? '#fbbf24' : '#34d399',
        })));
        firstLine = firstLine ?? dots;
      }

      const anchor = firstLine ?? seriesRef.current[`p_${p.key}_${p.hist?.f}`];
      (p.levels ?? []).forEach(lv => anchor?.createPriceLine({
        price: lv.v, color: lv.c ?? 'rgba(148,163,184,0.3)',
        lineWidth: 1, lineStyle: lv.style ?? 2,
        axisLabelVisible: !!lv.t, title: lv.t ?? '',
      }));
    });

    // ── Sync time scales ─────────────────────────────────────────────────
    const all = Object.values(chartsRef.current);
    all.forEach(c => {
      c.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range == null) return;
        all.forEach(o => { if (o !== c) o.timeScale().setVisibleLogicalRange(range); });
      });
    });
    all.forEach(c => c.timeScale().fitContent());

    // ── Responsive resize ────────────────────────────────────────────────
    const observer = new ResizeObserver(() => {
      const w = containerRef.current?.clientWidth;
      if (!w) return;
      Object.values(chartsRef.current).forEach(c => { try { c.applyOptions({ width: w }); } catch { /* chart already disposed */ } });
    });
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      Object.values(chartsRef.current).forEach(c => { try { c.remove(); } catch { /* chart already disposed */ } });
      chartsRef.current = {};
      seriesRef.current = {};
    };
  }, [bars, loading, error, activeIndicators, barMap, srData, analytics, forceTrend, events]);

  // ── 主題切換：canvas 不會跟 CSS 變數，得手動把新調色盤套到每張圖 ──────
  useEffect(() => Theme.subscribe(() => {
    const t = themedChartOpts(chartPalette());
    Object.values(chartsRef.current).forEach(c => { try { c.applyOptions(t); } catch { /* chart already disposed */ } });
  }), []);

  // ── Toggles ────────────────────────────────────────────────────────────
  const toggleMA = useCallback((key) => {
    setShowMA(prev => {
      const next = { ...prev, [key]: !prev[key] };
      seriesRef.current[key]?.applyOptions({ visible: next[key] });
      return next;
    });
  }, []);

  const toggleIndicator = useCallback((key) => {
    setActiveIndicators(prev => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const display    = hoverBar ?? bars[bars.length - 1];
  const changeAmt  = display ? display.close - display.open : 0;
  const changePct  = display?.open ? (changeAmt / display.open) * 100 : 0;
  const isUp       = changeAmt >= 0;

  const alerts     = useMemo(() => computeAlerts(bars, analytics), [bars, analytics]);
  const bearishCount = alerts.filter(a => a.severity === 'bearish').length;
  const bullishCount = alerts.filter(a => a.severity === 'bullish').length;
  const fmtSR = (v, digits = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : null);

  const levelTier = (lvl) => {
    const score = typeof lvl?.score === 'number' ? lvl.score : 0;
    const strength = typeof lvl?.strength === 'number' ? lvl.strength : 0;
    const touches = typeof lvl?.touches === 'number' ? lvl.touches : 0;
    const vol = typeof lvl?.volume_score === 'number' ? lvl.volume_score : 1;
    const gaps = typeof lvl?.gap_hits === 'number' ? lvl.gap_hits : 0;
    if ((strength >= 4 && score >= 3.5) || (touches >= 3 && vol >= 1.2) || (score >= 5 && gaps >= 1)) return 'strong';
    if (strength <= 2 && score < 2.5 && touches <= 1) return 'weak';
    return 'medium';
  };
  const levelTone = (tier, side) => {
    if (side === 'resistance') {
      if (tier === 'strong') return { bg: 'rgba(248,113,113,0.26)', border: 'rgba(248,113,113,0.75)', text: '#fecaca' };
      if (tier === 'weak')   return { bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.26)', text: '#fca5a5' };
      return { bg: 'rgba(248,113,113,0.16)', border: 'rgba(248,113,113,0.45)', text: '#fda4af' };
    }
    if (tier === 'strong') return { bg: 'rgba(52,211,153,0.26)', border: 'rgba(52,211,153,0.75)', text: '#bbf7d0' };
    if (tier === 'weak')   return { bg: 'rgba(52,211,153,0.08)', border: 'rgba(52,211,153,0.26)', text: '#86efac' };
    return { bg: 'rgba(52,211,153,0.16)', border: 'rgba(52,211,153,0.45)', text: '#6ee7b7' };
  };

  const stats  = analytics?.stats;
  const rating = analytics?.rating;
  const trend  = analytics?.trend && !analytics.trend.error ? analytics.trend : null;
  const trendLines = trend
    ? [...(trend.trendlines?.resistance ?? []), ...(trend.trendlines?.support ?? [])]
        .sort((a, b) => (b.value_now ?? 0) - (a.value_now ?? 0))
    : [];
  const regime = trend?.regime ?? null;
  // The lines exist in the payload either way; this decides whether showing
  // them is analysis or decoration.
  const trendable = !regime || regime.trendable;
  const showTrendDetail = trendable || forceTrend;

  const activeOverlayCount = OVERLAY_DEFS.filter(o => activeIndicators[o.key]).length;

  // 區間／頻率切換（兩種版面共用）
  const rangeControls = (
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex gap-1 bg-[var(--bg-tertiary)] border border-[var(--glass-border)] rounded-xl p-1">
              {[{label:'D', value:'day'}, {label:'W', value:'week'}, {label:'M', value:'month'}].map(iv => (
                <button key={iv.value} onClick={() => setInterval(iv.value)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition-all ${
                    interval === iv.value ? 'bg-[#0052ff] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.07)]'
                  }`}>{iv.label}</button>
              ))}
            </div>
            {/* 手機寬度放不下 8 個區間鈕：橫向捲動，別讓 MAX 被卡片切掉 */}
            <div className="flex max-w-full gap-1 overflow-x-auto bg-[var(--bg-tertiary)] border border-[var(--glass-border)] rounded-xl p-1">
              {PERIODS.map(p => (
                <button key={p.value} onClick={() => setPeriod(p.value)}
                  className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                    period === p.value ? 'bg-[#0052ff] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.07)]'
                  }`}>{p.label}</button>
              ))}
            </div>
          </div>
  );

  // 疊圖／副圖切換（兩種版面共用）
  const layerControls = (
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] font-semibold uppercase tracking-wider mr-1 w-[86px] shrink-0">
              <Layers size={13} /> 疊圖
            </span>
            {OVERLAY_DEFS.map(o => <IndicatorButton key={o.key} k={o.key} label={o.label} desc={o.desc}
              color={o.color} on={!!activeIndicators[o.key]} onToggle={toggleIndicator} />)}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] font-semibold uppercase tracking-wider mr-1 w-[86px] shrink-0">
              <Settings2 size={13} /> 副圖
            </span>
            {PANEL_DEFS.map(p => <IndicatorButton key={p.key} k={p.key} label={p.label} desc={p.desc}
              color={p.color} on={!!activeIndicators[p.key]} onToggle={toggleIndicator} />)}
          </div>
        </div>
  );

  // 個股頁原本的控制卡：標題＋區間切換＋圖層切換
  const controlsNode = (
      <div className="card p-3">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3 pb-3 border-b border-[var(--glass-border)]">
          <span className="text-sm font-semibold text-[var(--text-secondary)]">Price Chart</span>
          {rangeControls}
        </div>
        {layerControls}
      </div>
  );

  // 技術評級／趨勢結構／關鍵價位／支撐壓力 四張分析卡
  const analyticsNodes = (
    <>
      {/* ── Technical Rating + Key Stats ── */}
      {!loading && !error && (rating || stats) && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Gauge size={15} className="text-[#38bdf8]" />
            <span className="text-sm font-bold">技術評級與風險指標</span>
            <span className="text-xs text-[var(--text-muted)]">
              {rating?.detail?.length ?? 0} 項指標投票 · {period} {interval}
            </span>
          </div>

          {rating && (
            <div className="flex flex-wrap gap-2">
              <RatingBar title="綜合評級" node={rating.overall} />
              <RatingBar title="均線 Moving Averages" node={rating.moving_averages} />
              <RatingBar title="擺盪 Oscillators" node={rating.oscillators} />
            </div>
          )}

          {stats && (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
              <StatCell label="ATR(14)"    value={stats.atr}  hint="平均真實區間 — 用來抓停損距離" />
              <StatCell label="ATR %"      value={stats.atrp} suffix="%" hint="ATR 佔股價比例，波動大小" />
              <StatCell label="HV 20D"     value={stats.hv20} suffix="%" hint="年化歷史波動率（固定 20 根窗口）" />
              <StatCell label="EWMA 波動"   value={stats.ewma_vol} suffix="%"
                tone={VOL_REGIME_TONE[stats.percentiles?.vol_regime]}
                hint="RiskMetrics λ=0.94 指數加權波動率 — 比固定窗口更快反映波動狀態改變" />
              <StatCell label="去極端波動"   value={stats.vol_winsorized} suffix="%"
                hint="報酬截尾 ±2σ 後的年化波動。與「年化波動」差距大 = 波動主要來自少數跳空日" />
              <StatCell label="ADX"        value={stats.adx}
                tone={stats.adx >= 25 ? '#34d399' : stats.adx < 20 ? '#94a3b8' : undefined}
                hint="≥25 趨勢明確，<20 盤整" />
              <StatCell label="Beta"       value={stats.beta} hint="全期間對 SPY 的系統性風險" />
              <StatCell label="Beta 近期"   value={stats.beta_now}
                tone={stats.beta_now != null && stats.beta_prev != null
                  ? (stats.beta_now > stats.beta_prev ? '#f87171' : '#34d399') : undefined}
                hint={stats.beta_prev != null
                  ? `滾動 Beta：一季前 ${stats.beta_prev}，區間 ${stats.beta_min}–${stats.beta_max}`
                  : '滾動 Beta'} />
              <StatCell label="Alpha"      value={stats.alpha} suffix="%" hint="全期間年化超額報酬" />
              <StatCell label="Sharpe"     value={stats.sharpe} hint="風險調整後報酬" />
              <StatCell label="Sortino"    value={stats.sortino} hint="只計下跌風險的 Sharpe" />
              <StatCell label="年化波動"    value={stats.annual_vol} suffix="%" />
              <StatCell label="最大回撤"    value={stats.max_drawdown} suffix="%" tone="#f87171" />
              <StatCell label="目前回撤"    value={stats.current_drawdown} suffix="%"
                tone={stats.current_drawdown < -10 ? '#f87171' : undefined} />
              <StatCell label="Mansfield RS" value={stats.rs_mansfield}
                tone={stats.rs_mansfield > 0 ? '#34d399' : '#f87171'} hint=">0 強於大盤" />
              <StatCell label="52W 高"     value={stats.high_52w} />
              <StatCell label="距高點"      value={stats.pct_from_high} suffix="%" tone="#f87171" />
              <StatCell label="52W 低"     value={stats.low_52w} />
              <StatCell label="距低點"      value={stats.pct_from_low} suffix="%" tone="#34d399" />
              <StatCell label="2×ATR 停損"  value={stats.stop_2atr} tone="#f87171" hint="常用的初始停損位" />
              <StatCell label="3R 目標"     value={stats.target_3r} tone="#34d399" hint="賺賠比 3:1 的目標價" />
            </div>
          )}

          {/* Percentile ranks — each metric against its OWN trailing history */}
          {stats?.percentiles && Object.keys(stats.percentiles).length > 1 && (
            <div className="pt-1">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-semibold">
                  百分位排名
                </span>
                <span className="text-[10px] text-[var(--text-muted)]">
                  每項指標與「自己過去一年」比較，不是跨股票比較
                </span>
                {stats.percentiles.vol_regime && (
                  <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border ml-auto"
                    style={{
                      color: VOL_REGIME_TONE[stats.percentiles.vol_regime] ?? '#94a3b8',
                      borderColor: `${VOL_REGIME_TONE[stats.percentiles.vol_regime] ?? '#94a3b8'}66`,
                      background: `${VOL_REGIME_TONE[stats.percentiles.vol_regime] ?? '#94a3b8'}18`,
                    }}>
                    {VOL_REGIME_LABEL[stats.percentiles.vol_regime] ?? stats.percentiles.vol_regime}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
                {[
                  ['price', '股價'], ['ewma_vol', 'EWMA 波動'], ['hv20', 'HV20'],
                  ['atrp', 'ATR%'], ['bb_bw', '帶寬'], ['rvol', '相對量'], ['adx', 'ADX'],
                ].map(([key, label]) => {
                  const p = stats.percentiles[key];
                  if (p == null) return null;
                  const tone = pctTone(p);
                  return (
                    <div key={key} className="px-2.5 py-1.5 rounded-lg border"
                      style={{ borderColor: `${tone ?? '#94a3b8'}44`, background: `${tone ?? '#94a3b8'}12` }}>
                      <div className="text-[10px] text-[var(--text-muted)]">{label}</div>
                      <div className="flex items-baseline gap-1">
                        <span className="text-sm font-mono font-bold" style={{ color: tone }}>{p}</span>
                        <span className="text-[9px] text-[var(--text-muted)]">th</span>
                      </div>
                      <div className="mt-1 h-1 rounded-full bg-[rgba(148,163,184,0.15)] overflow-hidden">
                        <div className="h-full rounded-full"
                          style={{ width: `${p}%`, background: tone ?? '#94a3b8' }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Trend structure: trendlines, channel, patterns ── */}
      {!loading && !error && trend && (trendLines.length > 0 || trend.channel || trend.patterns?.length || regime) && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Activity size={15} style={{ color: trendable ? '#38bdf8' : '#fbbf24' }} />
            <span className="text-sm font-bold">趨勢結構</span>
            {regime && (
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                style={{ color: REGIME_TONE[regime.label] ?? '#94a3b8',
                         borderColor: `${REGIME_TONE[regime.label] ?? '#94a3b8'}66`,
                         background: `${REGIME_TONE[regime.label] ?? '#94a3b8'}18` }}
                title="R²（價格是否沿著一條線走）與趨勢線實際彈開幅度合成，ADX 刻意不計入">
                {regime.label} {regime.score}/100
              </span>
            )}
            <span className="text-xs text-[var(--text-muted)]">趨勢線 · 通道 · 形態</span>
            {showTrendDetail && (
              <button onClick={() => toggleIndicator('trend')}
                className="ml-auto text-[10px] px-2 py-0.5 rounded-full border transition-colors"
                style={{
                  color: activeIndicators.trend ? '#0284c7' : 'var(--text-muted)',
                  borderColor: activeIndicators.trend ? '#38bdf8' : 'var(--glass-border)',
                  background: activeIndicators.trend ? '#38bdf822' : 'transparent',
                }}>
                {activeIndicators.trend ? '圖上已顯示' : '在圖上顯示'}
              </button>
            )}
          </div>

          {trend.summary && (
            <div className="text-xs leading-relaxed px-3 py-2 rounded-lg border"
              style={trendable
                ? { color: 'var(--text-secondary)', background: 'rgba(56,189,248,0.06)',
                    borderColor: 'rgba(56,189,248,0.2)' }
                : { color: 'var(--text-secondary)', background: 'rgba(251,191,36,0.08)',
                    borderColor: 'rgba(251,191,36,0.35)' }}>
              {!trendable && <AlertTriangle size={13} className="inline mr-1.5 -mt-0.5 text-[#f59e0b]" />}
              {trend.summary}
            </div>
          )}

          {/* Range readout — what to actually look at when nothing is trending */}
          {!trendable && trend.range && (
            <div className="px-3 py-2.5 rounded-lg bg-[rgba(148,163,184,0.08)] border border-[var(--glass-border)]">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
                  近 {trend.range.lookback} 根區間
                </span>
                <span className="text-[10px] text-[var(--text-muted)]">
                  均值回歸的標的看這個，不是趨勢線
                </span>
              </div>
              <div className="flex items-baseline justify-between text-xs font-mono mb-1">
                <span className="text-[#34d399]">低 ${trend.range.low}</span>
                <span className="text-[var(--text-muted)]">中位 ${trend.range.median}</span>
                <span className="text-[#f87171]">高 ${trend.range.high}</span>
              </div>
              <div className="h-2 rounded-full bg-[rgba(148,163,184,0.18)] relative">
                <div className="absolute inset-y-0 w-px bg-[rgba(148,163,184,0.5)]" style={{ left: '50%' }} />
                <div className="absolute -top-0.5 w-1 h-3 rounded-full bg-[var(--text-primary)]"
                  style={{ left: `calc(${Math.min(100, Math.max(0, trend.range.position_pct))}% - 2px)` }} />
              </div>
              <div className="text-[10px] text-[var(--text-muted)] mt-1.5">
                現價在區間 {trend.range.position_pct}%，歷史百分位 {trend.range.percentile}
                {regime?.halflife_bars ? ` · 偏離後約 ${Math.round(regime.halflife_bars)} 根 K 棒回到均值` : ''}
              </div>
              <button onClick={() => setForceTrend(v => !v)}
                className="mt-2 text-[10px] px-2 py-0.5 rounded-full border transition-colors"
                style={{
                  color: forceTrend ? '#f59e0b' : 'var(--text-muted)',
                  borderColor: forceTrend ? '#fbbf24' : 'var(--glass-border)',
                  background: forceTrend ? 'rgba(251,191,36,0.14)' : 'transparent',
                }}>
                {forceTrend ? '已強制顯示趨勢線（僅供參考）' : '仍要看趨勢線'}
              </button>
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Trendlines */}
            {showTrendDetail && trendLines.length > 0 && (
              <div className="lg:col-span-2">
                <div className="text-xs font-semibold text-[#38bdf8] uppercase tracking-wider mb-2">
                  自動趨勢線 · 依現價由高到低
                </div>
                <div className="space-y-1">
                  {trendLines.map((l, i) => {
                    const res = l.side === 'resistance';
                    const color = res ? '#f87171' : '#34d399';
                    const broken = l.status === 'broken';
                    return (
                      <div key={i} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs"
                        style={{ background: `${color}${broken ? '08' : '14'}`,
                                 opacity: broken ? 0.65 : 1 }}>
                        <span className="font-bold w-[68px] shrink-0" style={{ color }}>{l.label}</span>
                        <span className="font-mono font-bold w-[74px] text-right">${l.value_now}</span>
                        <span className="font-mono w-[62px] text-right"
                          style={{ color: l.dist_pct >= 0 ? '#f87171' : '#34d399' }}>
                          {l.dist_pct >= 0 ? '+' : ''}{l.dist_pct}%
                        </span>
                        <span className="text-[10px] text-[var(--text-muted)] w-[52px] text-right">
                          {l.touches} 觸及
                        </span>
                        <span className="text-[10px] text-[var(--text-muted)] w-[70px] text-right font-mono"
                          title="趨勢線年化斜率">
                          {l.slope_per_year_pct > 0 ? '+' : ''}{l.slope_per_year_pct}%/yr
                        </span>
                        <span className="flex gap-0.5 ml-auto" title={`強度 ${l.strength}/5`}>
                          {Array.from({ length: 5 }, (_, k) => (
                            <span key={k} className="w-1 h-3 rounded-sm"
                              style={{ background: k < l.strength ? color : 'rgba(148,163,184,0.18)' }} />
                          ))}
                        </span>
                        {broken && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-md font-semibold shrink-0"
                            style={{ color, background: `${color}22` }}>已破</span>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className="text-[10px] text-[var(--text-muted)] mt-1.5">
                  觸及次數 = 價格在容差內折返的獨立次數；斜率以年化 % 表示，不同週期可直接比較
                </div>
              </div>
            )}

            {/* Regression channel + formations */}
            <div className="space-y-3">
              {trend.regression && (
                <div>
                  <div className="text-xs font-semibold text-[#a78bfa] uppercase tracking-wider mb-2">
                    線性回歸通道 · {trend.regression.lookback} 根
                  </div>
                  <div className="px-3 py-2 rounded-lg bg-[rgba(167,139,250,0.08)] border border-[rgba(167,139,250,0.22)]">
                    <div className="flex items-baseline justify-between text-[11px] font-mono">
                      <span className="text-[#f87171]">+2σ ${trend.regression.upper}</span>
                      <span className="text-[var(--text-muted)]">R²={trend.regression.r2}</span>
                    </div>
                    <div className="my-1.5 h-2 rounded-full bg-[rgba(148,163,184,0.15)] relative">
                      <div className="absolute inset-y-0 w-px bg-[rgba(226,232,240,0.35)]" style={{ left: '50%' }} />
                      <div className="absolute -top-0.5 w-1 h-3 rounded-full bg-[#a78bfa]"
                        style={{ left: `calc(${trend.regression.position_pct}% - 2px)` }} />
                    </div>
                    <div className="flex items-baseline justify-between text-[11px] font-mono">
                      <span className="text-[#34d399]">−2σ ${trend.regression.lower}</span>
                      <span className="text-[var(--text-muted)]">
                        {trend.regression.slope_per_year_pct > 0 ? '+' : ''}
                        {trend.regression.slope_per_year_pct}%/yr
                      </span>
                    </div>
                    <div className="text-[10px] text-[var(--text-muted)] mt-1.5">
                      中軸 ${trend.regression.mid} · 1σ≈{trend.regression.sigma_pct}% ·
                      現價 z={trend.regression.z}（通道 {trend.regression.position_pct}% 位置）
                    </div>
                  </div>
                </div>
              )}

              {(trend.channel || trend.patterns?.length > 0) && (
                <div>
                  <div className="text-xs font-semibold text-[#f472b6] uppercase tracking-wider mb-2">
                    形態
                  </div>
                  <div className="space-y-1.5">
                    {trend.channel && (
                      <div className="px-3 py-2 rounded-lg text-xs bg-[rgba(244,114,182,0.08)] border border-[rgba(244,114,182,0.22)]">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-[#f472b6]">{trend.channel.label}</span>
                          <span className="text-[10px] text-[var(--text-muted)]">
                            寬 {trend.channel.width_pct}%
                          </span>
                          <span className="ml-auto font-mono text-[10px] text-[var(--text-muted)]">
                            通道 {trend.channel.position_pct}%
                          </span>
                        </div>
                        <div className="text-[10px] text-[var(--text-muted)] mt-1 font-mono">
                          上緣 ${trend.channel.upper} · 下緣 ${trend.channel.lower} · 量測目標 ${trend.channel.target}
                        </div>
                      </div>
                    )}
                    {(trend.patterns ?? []).map((pt, i) => {
                      const tone = pt.bias === 'bullish' ? '#34d399' : '#f87171';
                      return (
                        <div key={i} className="px-3 py-2 rounded-lg text-xs border"
                          style={{ background: `${tone}12`, borderColor: `${tone}3a` }}>
                          <div className="flex items-center gap-2">
                            <span className="font-bold" style={{ color: tone }}>{pt.label}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded-md font-semibold"
                              style={{ color: tone, background: `${tone}22` }}>
                              {pt.status === 'confirmed' ? '已確認' : '成形中'}
                            </span>
                          </div>
                          <div className="text-[10px] text-[var(--text-muted)] mt-1 font-mono">
                            頸線 ${pt.neckline}（{pt.neck_dist_pct > 0 ? '+' : ''}{pt.neck_dist_pct}%）
                            · 量測目標 ${pt.target}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <button onClick={() => toggleIndicator('pattern')}
                    className="mt-1.5 text-[10px] px-2 py-0.5 rounded-full border transition-colors"
                    style={{
                      color: activeIndicators.pattern ? '#db2777' : 'var(--text-muted)',
                      borderColor: activeIndicators.pattern ? '#f472b6' : 'var(--glass-border)',
                      background: activeIndicators.pattern ? '#f472b622' : 'transparent',
                    }}>
                    {activeIndicators.pattern ? '圖上已顯示' : '在圖上畫出形態'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Pivot Points / Fibonacci / Volume Profile ── */}
      {!loading && !error && analytics && (analytics.pivots?.monthly || analytics.fib || analytics.volume_profile) && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Crosshair size={15} className="text-[#c084fc]" />
            <span className="text-sm font-bold">關鍵價位</span>
            <span className="text-xs text-[var(--text-muted)]">樞紐 · 斐波那契 · 成交量分布</span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Pivots */}
            {analytics.pivots?.monthly && (
              <div>
                <div className="text-xs font-semibold text-[#c084fc] uppercase tracking-wider mb-2">
                  Pivot Points · 月 ({analytics.pivots.monthly.period})
                </div>
                <div className="space-y-1">
                  {['r3','r2','r1','p','s1','s2','s3'].map(k => {
                    const classic = analytics.pivots.monthly.classic?.[k];
                    const fib     = analytics.pivots.monthly.fibonacci?.[k];
                    const isR = k[0] === 'r', isP = k === 'p';
                    const color = isP ? '#c084fc' : isR ? '#f87171' : '#34d399';
                    const near = display && classic != null &&
                      Math.abs(classic - display.close) / display.close < 0.02;
                    return (
                      <div key={k}
                        className={`flex items-center gap-2 px-2.5 py-1 rounded-lg text-xs ${near ? 'ring-1' : ''}`}
                        style={{ background: `${color}12`, ...(near ? { boxShadow: `inset 0 0 0 1px ${color}` } : {}) }}>
                        <span className="font-bold w-6" style={{ color }}>{k.toUpperCase()}</span>
                        <span className="font-mono flex-1">${classic ?? '—'}</span>
                        <span className="font-mono text-[10px] text-[var(--text-muted)]">fib ${fib ?? '—'}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Fibonacci */}
            {analytics.fib && (
              <div>
                <div className="text-xs font-semibold text-[#fdba74] uppercase tracking-wider mb-2">
                  Fibonacci · {analytics.fib.direction === 'up' ? '上升段回檔' : '下跌段反彈'}
                </div>
                <div className="text-[10px] text-[var(--text-muted)] mb-1.5 font-mono">
                  swing {analytics.fib.swing_low} → {analytics.fib.swing_high} · {interval === 'day' ? '近一年' : '近 60 根'}
                </div>
                <div className="space-y-1">
                  {analytics.fib.levels.map(l => {
                    const near = display && Math.abs(l.price - display.close) / display.close < 0.02;
                    return (
                      <div key={l.ratio}
                        className="flex items-center gap-2 px-2.5 py-1 rounded-lg text-xs"
                        style={{ background: 'rgba(253,186,116,0.08)',
                                 ...(near ? { boxShadow: 'inset 0 0 0 1px #fdba74' } : {}) }}>
                        <span className="font-bold w-12 text-[#fdba74]">{l.label}</span>
                        <span className="font-mono flex-1">${l.price}</span>
                        {near && <span className="text-[10px] text-[#fdba74]">← 現價</span>}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Volume profile */}
            {analytics.volume_profile && (
              <div>
                <div className="text-xs font-semibold text-[#5eead4] uppercase tracking-wider mb-2">
                  Volume Profile · 籌碼分布
                </div>
                <div className="text-[10px] text-[var(--text-muted)] mb-1.5 font-mono">
                  最近 {analytics.volume_profile.bars} 根 K 棒 · 70% 價值區
                </div>
                <div className="flex gap-2 mb-2 text-[11px] font-mono">
                  <span className="px-2 py-0.5 rounded border border-[rgba(94,234,212,0.5)] text-[#5eead4]">POC ${analytics.volume_profile.poc}</span>
                  <span className="px-2 py-0.5 rounded border border-[rgba(94,234,212,0.25)] text-[#99f6e4]">VAH ${analytics.volume_profile.vah}</span>
                  <span className="px-2 py-0.5 rounded border border-[rgba(94,234,212,0.25)] text-[#99f6e4]">VAL ${analytics.volume_profile.val}</span>
                </div>
                <div className="space-y-px max-h-[240px] overflow-y-auto pr-1">
                  {analytics.volume_profile.bins.slice().reverse().map((b, i) => {
                    const inVA = b.price >= analytics.volume_profile.val && b.price <= analytics.volume_profile.vah;
                    const isPoc = Math.abs(b.price - analytics.volume_profile.poc) < 0.01;
                    return (
                      <div key={i} className="flex items-center gap-1.5 text-[9px]">
                        <span className="font-mono w-12 text-right text-[var(--text-muted)]">{b.price}</span>
                        <div className="flex-1 h-2 rounded-sm overflow-hidden bg-[rgba(255,255,255,0.02)]">
                          <div className="h-full rounded-sm"
                            style={{ width: `${b.pct}%`,
                                     background: isPoc ? '#5eead4' : inVA ? 'rgba(94,234,212,0.5)' : 'rgba(148,163,184,0.3)' }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Support / Resistance Summary ── */}
      {!loading && !error && activeIndicators.sr && (srData.resistance.length > 0 || srData.support.length > 0) && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Layers size={15} className="text-[#e879f9]" />
            <span className="text-sm font-bold">Support &amp; Resistance Levels</span>
            <span className="text-xs text-[var(--text-muted)]">swing + gap + volume · {period} data</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-[#f87171] uppercase tracking-wider mb-2">
                <TrendingUp size={12} /> Resistance
              </div>
              {srData.resistance.length === 0
                ? <p className="text-xs text-[var(--text-muted)]">No resistance levels detected</p>
                : srData.resistance.slice().reverse().map((lvl, idx) => {
                    const dist = display ? ((lvl.price - display.close) / display.close * 100) : null;
                    const score = fmtSR(lvl.score, 2);
                    const vol = fmtSR(lvl.volume_score, 2);
                    const gaps = typeof lvl.gap_hits === 'number' ? lvl.gap_hits : null;
                    const tier = levelTier(lvl);
                    const tone = levelTone(tier, 'resistance');
                    return (
                      <div key={idx} className="px-3 py-2 rounded-lg border"
                        style={{ background: tone.bg, borderColor: tone.border }}>
                        <div className="flex items-center gap-3">
                          <span className="text-[11px] font-bold text-[#f87171] w-5 shrink-0">
                            R{srData.resistance.length - idx}
                          </span>
                          <span className="font-mono font-bold text-sm text-[var(--text-primary)] flex-1">${lvl.price}</span>
                          {dist !== null && <span className="text-xs text-[#f87171] font-semibold">+{dist.toFixed(1)}%</span>}
                          <span className="text-[10px] px-1.5 py-0.5 rounded border"
                            style={{ color: tone.text, borderColor: tone.border, background: tone.bg }}>
                            {tier === 'strong' ? '強壓力' : tier === 'weak' ? '弱壓力' : '中壓力'}
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            {Array.from({ length: 5 }).map((_, i) => (
                              <span key={i} className="w-1.5 h-1.5 rounded-full"
                                style={{ background: i < lvl.strength ? '#f87171' : 'rgba(248,113,113,0.15)' }} />
                            ))}
                            <span className="text-[10px] text-[var(--text-muted)] ml-1">{lvl.touches}x</span>
                          </div>
                        </div>
                        {(score !== null || vol !== null || gaps !== null) && (
                          <div className="mt-1.5 flex items-center gap-1.5 text-[10px]">
                            {score !== null && <span className="px-1.5 py-0.5 rounded border border-[rgba(248,113,113,0.35)] text-[#fca5a5]">score {score}</span>}
                            {gaps !== null && <span className="px-1.5 py-0.5 rounded border border-[rgba(248,113,113,0.28)] text-[#fda4af]">gap {gaps}</span>}
                            {vol !== null && <span className="px-1.5 py-0.5 rounded border border-[rgba(248,113,113,0.25)] text-[#fecaca]">vol {vol}x</span>}
                          </div>
                        )}
                      </div>
                    );
                  })
              }
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-[#34d399] uppercase tracking-wider mb-2">
                <TrendingDown size={12} /> Support
              </div>
              {srData.support.length === 0
                ? <p className="text-xs text-[var(--text-muted)]">No support levels detected</p>
                : srData.support.map((lvl, idx) => {
                    const dist = display ? ((lvl.price - display.close) / display.close * 100) : null;
                    const score = fmtSR(lvl.score, 2);
                    const vol = fmtSR(lvl.volume_score, 2);
                    const gaps = typeof lvl.gap_hits === 'number' ? lvl.gap_hits : null;
                    const tier = levelTier(lvl);
                    const tone = levelTone(tier, 'support');
                    return (
                      <div key={idx} className="px-3 py-2 rounded-lg border"
                        style={{ background: tone.bg, borderColor: tone.border }}>
                        <div className="flex items-center gap-3">
                          <span className="text-[11px] font-bold text-[#34d399] w-5 shrink-0">S{idx + 1}</span>
                          <span className="font-mono font-bold text-sm text-[var(--text-primary)] flex-1">${lvl.price}</span>
                          {dist !== null && <span className="text-xs text-[#34d399] font-semibold">{dist.toFixed(1)}%</span>}
                          <span className="text-[10px] px-1.5 py-0.5 rounded border"
                            style={{ color: tone.text, borderColor: tone.border, background: tone.bg }}>
                            {tier === 'strong' ? '強支撐' : tier === 'weak' ? '弱支撐' : '中支撐'}
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            {Array.from({ length: 5 }).map((_, i) => (
                              <span key={i} className="w-1.5 h-1.5 rounded-full"
                                style={{ background: i < lvl.strength ? '#34d399' : 'rgba(52,211,153,0.15)' }} />
                            ))}
                            <span className="text-[10px] text-[var(--text-muted)] ml-1">{lvl.touches}x</span>
                          </div>
                        </div>
                        {(score !== null || vol !== null || gaps !== null) && (
                          <div className="mt-1.5 flex items-center gap-1.5 text-[10px]">
                            {score !== null && <span className="px-1.5 py-0.5 rounded border border-[rgba(52,211,153,0.35)] text-[#86efac]">score {score}</span>}
                            {gaps !== null && <span className="px-1.5 py-0.5 rounded border border-[rgba(52,211,153,0.28)] text-[#6ee7b7]">gap {gaps}</span>}
                            {vol !== null && <span className="px-1.5 py-0.5 rounded border border-[rgba(52,211,153,0.25)] text-[#a7f3d0]">vol {vol}x</span>}
                          </div>
                        )}
                      </div>
                    );
                  })
              }
            </div>
          </div>
        </div>
      )}

      {/* ── Signal Alerts ── */}
      {!loading && !error && alerts.length > 0 && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertTriangle size={15} className="text-[var(--accent-amber)]" />
              <span className="text-sm font-bold">Signal Alerts</span>
              <span className="text-xs text-[var(--text-muted)]">based on latest bar</span>
            </div>
            <div className="flex items-center gap-2 text-xs font-semibold">
              {bearishCount > 0 && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[rgba(248,113,113,0.1)] text-[#f87171] border border-[rgba(248,113,113,0.2)]">
                  <TrendingDown size={11} /> {bearishCount} Bearish
                </span>
              )}
              {bullishCount > 0 && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[rgba(52,211,153,0.1)] text-[#34d399] border border-[rgba(52,211,153,0.2)]">
                  <TrendingUp size={11} /> {bullishCount} Bullish
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {alerts.map((a, i) => {
              const s = SEVERITY_STYLE[a.severity];
              const Icon = s.icon;
              return (
                <div key={i} className="flex items-start gap-3 px-3 py-2.5 rounded-xl border"
                  style={{ background: s.bg, borderColor: s.border }}>
                  <div className="mt-0.5 shrink-0 w-6 h-6 rounded-lg flex items-center justify-center"
                    style={{ background: `${s.color}22` }}>
                    <Icon size={13} style={{ color: s.color }} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-bold" style={{ color: s.color }}>{a.indicator}</span>
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                        style={{ background: `${s.color}22`, color: s.color }}>{a.type}</span>
                      <span className="text-[11px] font-mono text-[var(--text-muted)] ml-auto">{a.value}</span>
                    </div>
                    <p className="text-[11px] text-[var(--text-muted)] mt-0.5 leading-snug">{a.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

    </>
  );

  // 走勢圖本體（含載入／錯誤狀態）。chartFirst 時外層已有卡片，這裡不再套一層邊框。
  const chartNode = (
    <>
      {loading && (
        <div className={`flex items-center justify-center ${chartFirst ? 'min-h-[520px]' : 'py-32'}`}>
          <RefreshCw size={22} className="animate-spin text-[var(--accent-cyan)]" />
        </div>
      )}
      {error && (
        <div className={chartFirst
          ? 'px-6 py-16 text-center text-sm text-[var(--accent-danger-text)]'
          : 'card p-6 border-[rgba(248,113,113,0.3)] text-center text-[var(--accent-danger)]'}>{error}</div>
      )}

      {!loading && !error && bars.length > 0 && (
        <div ref={containerRef} className={chartFirst ? 'overflow-hidden' : 'card p-0 overflow-hidden rounded-xl border border-[var(--glass-border)]'}>

          {/* ── OHLCV + live indicator readout ── */}
          <div className="flex flex-wrap items-center gap-4 px-4 py-2.5 border-b border-[var(--glass-border)] bg-[var(--glass-bg)]">
            {display && (
              <div className="flex items-center gap-3 text-xs font-mono flex-wrap">
                <span className="text-[var(--text-muted)]">{new Date(display.time * 1000).toLocaleDateString()}</span>
                <span>O <span className="text-[var(--text-primary)]">{display.open?.toFixed(2)}</span></span>
                <span>H <span className="text-[var(--accent-success)]">{display.high?.toFixed(2)}</span></span>
                <span>L <span className="text-[var(--accent-danger)]">{display.low?.toFixed(2)}</span></span>
                <span>C <span className="font-bold text-[var(--text-primary)]">{display.close?.toFixed(2)}</span></span>
                <span className={isUp ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}>
                  {isUp ? '+' : ''}{changeAmt.toFixed(2)} ({changePct.toFixed(2)}%)
                </span>
                {activePanels.map(p => {
                  const f = p.lines?.[0]?.f ?? p.hist?.f;
                  const v = f ? display[f] : null;
                  if (v == null) return null;
                  return (
                    <span key={p.key}>{seriesLabel(f)}{' '}
                      <span style={{ color: p.color }}>{v.toFixed(2)}</span>
                    </span>
                  );
                })}
              </div>
            )}
            <div className="flex flex-wrap gap-1.5 ml-auto">
              {MA_DEFS.map(({ key, label, color, width }) => (
                <button key={key} onClick={() => toggleMA(key)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-all ${showMA[key] ? 'opacity-100' : 'opacity-30'}`}
                  style={{ borderColor: showMA[key] ? color : BORDER, background: showMA[key] ? `${color}22` : 'transparent', color }}>
                  <span className="w-4 rounded-full inline-block" style={{ background: color, height: width + 1 }} />
                  {label}
                  {display?.[key] != null && <span className="font-mono font-normal">{display[key].toFixed(2)}</span>}
                </button>
              ))}
            </div>
          </div>

          {/* ── 圖例：每種線是什麼。紅綠只代表壓力／支撐，均線一律是其他顏色 ── */}
          {(() => {
            const last = bars[bars.length - 1]?.close;
            const showTL = activeIndicators.trend && trendable && trendLines.length > 0;
            const up = events?.earnings?.upcoming;
            const daysTo = up ? Math.round((new Date(`${up.date}T00:00:00`) - new Date(new Date().toDateString())) / 86400000) : null;
            const Swatch = ({ color, dashed, faded }) => (
              <span className="inline-block w-5 align-middle" style={{
                borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}`, opacity: faded ? 0.4 : 1,
              }} />
            );
            const items = [];
            if (showTL) items.push(
              <span key="tl" className="flex items-center gap-1.5 flex-wrap">
                <span className="font-semibold text-[var(--text-secondary)]">趨勢線</span>
                <Swatch color="#f87171" /> 壓力
                <Swatch color="#34d399" /> 支撐
                <Swatch color="#94a3b8" dashed /> 往後延伸
                <Swatch color="#94a3b8" faded /> 已跌破
              </span>);
            if (activeIndicators.sr && (srData.resistance.length || srData.support.length)) items.push(
              <span key="sr" className="flex items-center gap-1.5">
                <span className="font-semibold text-[var(--text-secondary)]">水平線</span>
                <span className="text-[#f87171]">紅＝壓力區</span><span className="text-[#34d399]">綠＝支撐區</span>
              </span>);
            if (activeIndicators.earn && events?.earnings) items.push(
              <span key="earn" className="flex items-center gap-1.5">
                <span className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-sm text-[9px] font-bold text-black" style={{ background: EARN_COLOR }}>E</span>
                財報日
                {up && (
                  <span style={{ color: EARN_COLOR }}>
                    · 下次 {up.date.slice(5).replace('-', '/')}{up.time_of_day ? ` ${up.time_of_day}` : ''}
                    {up.confirmed ? '' : '（預估）'}{daysTo != null && daysTo >= 0 ? `，還有 ${daysTo} 天` : ''}
                  </span>
                )}
              </span>);
            if (activeIndicators.cost && events?.positions?.length) items.push(
              <span key="cost" className="flex items-center gap-1.5 flex-wrap">
                <Swatch color={COST_COLOR} dashed />
                {events.positions.map(pos => {
                  const pnl = last ? (last / pos.avg_cost - 1) * 100 : null;
                  return (
                    <span key={pos.portfolio} title={`${pos.accounts.join(' + ')} · ${pos.shares} 股`}>
                      成本{events.positions.length > 1 ? ` ${pos.portfolio}` : ''}{' '}
                      <span className="font-mono" style={{ color: COST_COLOR }}>{pos.avg_cost.toFixed(2)}</span>
                      {pnl != null && (
                        <span className={`font-mono ml-1 ${pnl >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
                          {pnl >= 0 ? '+' : ''}{pnl.toFixed(1)}%
                        </span>
                      )}
                    </span>
                  );
                })}
              </span>);
            if (!items.length) return null;
            return (
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-1.5 border-b border-[var(--glass-border)] text-[11px] text-[var(--text-muted)]">
                {items}
              </div>
            );
          })()}

          {/* Candlestick */}
          <div ref={priceRef} style={{ width: '100%', height: 380 }} />

          {/* Volume */}
          <PanelLabel><span className="text-[var(--text-muted)]">Volume</span></PanelLabel>
          <div ref={volRef} style={{ width: '100%', height: 90 }} />

          {/* Oscillator panels */}
          {activePanels.map(p => (
            <div key={p.key}>
              <PanelLabel extra={p.note && (
                <span className="ml-auto text-[10px] text-[var(--text-muted)] normal-case font-normal">{p.note}</span>
              )}>
                <span className="text-[var(--text-muted)]">{p.label} {p.desc}</span>
                {(p.lines ?? []).map(ln => (
                  <span key={ln.f} style={{ color: ln.c ?? p.color }}>{seriesLabel(ln.f)}</span>
                ))}
              </PanelLabel>
              <div ref={el => { panelEls.current[p.key] = el; }} style={{ width: '100%', height: 100 }} />
            </div>
          ))}
        </div>
      )}
    </>
  );

  if (chartFirst) {
    const hasAnalytics = !loading && !error && !!analytics;
    const lastClose = bars.length ? bars[bars.length - 1].close : null;
    return (
      <div className="space-y-4">
        {/* 1. 走勢圖置頂：區間切換直接放在圖的標題列 */}
        <section className="card card-flat p-0 overflow-hidden" aria-label="走勢圖">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--glass-border)] px-4 py-3">
            <span className="text-sm font-semibold text-[var(--text-secondary)]">走勢圖</span>
            {rangeControls}
          </div>
          {chartNode}
        </section>

        {/* 2. 圖層設定：預設收起，摘要列顯示目前開了幾個 */}
        <details className="group card card-flat p-0 overflow-hidden">
          <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm font-semibold text-[var(--text-secondary)] transition-colors duration-200 hover:text-[var(--text-primary)] [&::-webkit-details-marker]:hidden">
            <Layers size={14} className="shrink-0" />
            圖層與指標
            <span className="text-xs font-normal text-[var(--text-meta)]">
              疊圖 {activeOverlayCount}，副圖 {activePanels.length}
            </span>
            <ChevronDown size={16} className="ml-auto shrink-0 transition-transform duration-200 group-open:rotate-180" />
          </summary>
          <div className="border-t border-[var(--glass-border)] px-4 py-3">{layerControls}</div>
        </details>

        {/* 3. 技術分析：預設展開，只放重點（價位階梯／型態／評級／風險）；完整版收在最底下 */}
        {hasAnalytics && (
          <details open className="group card card-flat p-0 overflow-hidden">
            <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm font-semibold text-[var(--text-secondary)] transition-colors duration-200 hover:text-[var(--text-primary)] [&::-webkit-details-marker]:hidden">
              <Gauge size={14} className="shrink-0" />
              技術分析
              <span className="text-xs font-normal text-[var(--text-meta)]">{period.toUpperCase()} {interval === 'day' ? '日線' : interval === 'week' ? '週線' : '月線'}</span>
              <ChevronDown size={16} className="ml-auto shrink-0 transition-transform duration-200 group-open:rotate-180" />
            </summary>
            <div className="border-t border-[var(--glass-border)]">
              <TechDigest close={lastClose} srData={srData} analytics={analytics} />
              <details className="group/full border-t border-[var(--glass-border)]">
                <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-xs font-semibold text-[var(--text-meta)] transition-colors duration-200 hover:text-[var(--text-primary)] [&::-webkit-details-marker]:hidden">
                  完整技術分析（所有指標、百分位、斐波那契、成交量分布）
                  <ChevronDown size={14} className="ml-auto shrink-0 transition-transform duration-200 group-open/full:rotate-180" />
                </summary>
                <div className="space-y-4 border-t border-[var(--glass-border)] p-4">{analyticsNodes}</div>
              </details>
            </div>
          </details>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {controlsNode}
      {analyticsNodes}
      {chartNode}
    </div>
  );
}
