import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import axios from 'axios';
import {
  createChart, CrosshairMode, ColorType,
  CandlestickSeries, LineSeries, HistogramSeries,
} from 'lightweight-charts';
import { RefreshCw, Settings2, AlertTriangle, TrendingUp, TrendingDown, Minus, Layers } from 'lucide-react';

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

const MA_DEFS = [
  { key: 'ma5',   label: 'MA5',   color: '#f59e0b' },
  { key: 'ma10',  label: 'MA10',  color: '#60a5fa' },
  { key: 'ma20',  label: 'MA20',  color: '#a78bfa' },
  { key: 'ma50',  label: 'MA50',  color: '#34d399' },
  { key: 'ma200', label: 'MA200', color: '#f87171' },
];

const INDICATOR_DEFS = [
  { key: 'sr',    label: 'Support / Resistance', desc: '',        color: '#e879f9', pricePanel: true },
  { key: 'bb',    label: 'Bollinger Bands',       desc: '(20,2)', color: '#818cf8', pricePanel: true },
  { key: 'macd',  label: 'MACD',                  desc: '(12,26,9)', color: '#60a5fa' },
  { key: 'rsi',   label: 'RSI',                   desc: '(14)',    color: '#fb923c' },
  { key: 'kdj',   label: 'KDJ',                   desc: '(9,3,3)', color: '#a78bfa' },
  { key: 'willr', label: 'Williams %R',            desc: '(14)',    color: '#f472b6' },
  { key: 'obv',   label: 'OBV',                   desc: '',        color: '#2dd4bf' },
];

const BG     = '#0f172a';
const GRID   = 'rgba(255,255,255,0.04)';
const BORDER = 'rgba(255,255,255,0.08)';
const TEXT   = '#94a3b8';

function chartOpts(height, extraTS = {}) {
  return {
    layout: { background: { type: ColorType.Solid, color: BG }, textColor: TEXT },
    grid:   { vertLines: { color: GRID }, horzLines: { color: GRID } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: BORDER },
    timeScale: { borderColor: BORDER, timeVisible: true, secondsVisible: false, ...extraTS },
    height,
  };
}

// ── Alert engine ─────────────────────────────────────────────────────────────
// severity: 'bearish' | 'bullish' | 'neutral'
function computeAlerts(bars) {
  if (bars.length < 2) return [];
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  const alerts = [];

  // RSI (14) ─────────────────────────────────────────────────────────────────
  if (last.rsi != null) {
    if (last.rsi >= 70)
      alerts.push({ indicator: 'RSI', type: 'Overbought', severity: 'bearish',
        value: last.rsi.toFixed(1), desc: `RSI ${last.rsi.toFixed(1)} is above 70` });
    else if (last.rsi <= 30)
      alerts.push({ indicator: 'RSI', type: 'Oversold', severity: 'bullish',
        value: last.rsi.toFixed(1), desc: `RSI ${last.rsi.toFixed(1)} is below 30` });
    else if (last.rsi >= 60 && prev.rsi < 60)
      alerts.push({ indicator: 'RSI', type: 'Rising', severity: 'neutral',
        value: last.rsi.toFixed(1), desc: `RSI crossed above 60` });
    else if (last.rsi <= 40 && prev.rsi > 40)
      alerts.push({ indicator: 'RSI', type: 'Falling', severity: 'neutral',
        value: last.rsi.toFixed(1), desc: `RSI crossed below 40` });
  }

  // Williams %R (14) ────────────────────────────────────────────────────────
  if (last.willr != null) {
    if (last.willr >= -20)
      alerts.push({ indicator: 'Williams %R', type: 'Overbought', severity: 'bearish',
        value: last.willr.toFixed(1), desc: `%R ${last.willr.toFixed(1)} is above -20` });
    else if (last.willr <= -80)
      alerts.push({ indicator: 'Williams %R', type: 'Oversold', severity: 'bullish',
        value: last.willr.toFixed(1), desc: `%R ${last.willr.toFixed(1)} is below -80` });
  }

  // KDJ J-line ───────────────────────────────────────────────────────────────
  if (last.j != null) {
    if (last.j >= 80)
      alerts.push({ indicator: 'KDJ', type: 'Overbought', severity: 'bearish',
        value: `J=${last.j.toFixed(1)}`, desc: `J-line ${last.j.toFixed(1)} is above 80` });
    else if (last.j <= 20)
      alerts.push({ indicator: 'KDJ', type: 'Oversold', severity: 'bullish',
        value: `J=${last.j.toFixed(1)}`, desc: `J-line ${last.j.toFixed(1)} is below 20` });
  }

  // KD crossover ─────────────────────────────────────────────────────────────
  if (last.k != null && prev.k != null) {
    const goldCross = prev.k <= prev.d && last.k > last.d;
    const deathCross = prev.k >= prev.d && last.k < last.d;
    if (goldCross)
      alerts.push({ indicator: 'KDJ', type: 'Golden Cross', severity: 'bullish',
        value: `K=${last.k.toFixed(1)}`, desc: `K-line crossed above D-line` });
    else if (deathCross)
      alerts.push({ indicator: 'KDJ', type: 'Death Cross', severity: 'bearish',
        value: `K=${last.k.toFixed(1)}`, desc: `K-line crossed below D-line` });
  }

  // MACD crossover ───────────────────────────────────────────────────────────
  if (last.macd != null && prev.macd != null) {
    const bullCross = prev.macd <= prev.signal && last.macd > last.signal;
    const bearCross = prev.macd >= prev.signal && last.macd < last.signal;
    if (bullCross)
      alerts.push({ indicator: 'MACD', type: 'Bullish Crossover', severity: 'bullish',
        value: last.macd.toFixed(3), desc: `MACD crossed above signal line` });
    else if (bearCross)
      alerts.push({ indicator: 'MACD', type: 'Bearish Crossover', severity: 'bearish',
        value: last.macd.toFixed(3), desc: `MACD crossed below signal line` });

    // Histogram momentum shift
    if (last.hist != null && prev.hist != null) {
      const histFlipUp   = prev.hist < 0 && last.hist >= 0;
      const histFlipDown = prev.hist > 0 && last.hist <= 0;
      if (histFlipUp)
        alerts.push({ indicator: 'MACD', type: 'Histogram Flip ↑', severity: 'bullish',
          value: last.hist.toFixed(3), desc: `Histogram turned positive` });
      else if (histFlipDown)
        alerts.push({ indicator: 'MACD', type: 'Histogram Flip ↓', severity: 'bearish',
          value: last.hist.toFixed(3), desc: `Histogram turned negative` });
    }
  }

  // Bollinger Bands ──────────────────────────────────────────────────────────
  if (last.bb_upper != null && last.close != null) {
    if (last.close >= last.bb_upper)
      alerts.push({ indicator: 'Bollinger Bands', type: 'Upper Band Touch', severity: 'bearish',
        value: last.close.toFixed(2), desc: `Price ≥ upper band (${last.bb_upper.toFixed(2)})` });
    else if (last.close <= last.bb_lower)
      alerts.push({ indicator: 'Bollinger Bands', type: 'Lower Band Touch', severity: 'bullish',
        value: last.close.toFixed(2), desc: `Price ≤ lower band (${last.bb_lower.toFixed(2)})` });
    // BB squeeze: bands are tighter than recent average (volatility contraction)
    const bw = last.bb_upper - last.bb_lower;
    const prev10 = bars.slice(-11, -1);
    if (prev10.length === 10 && prev10.every(b => b.bb_upper != null)) {
      const avgBw = prev10.reduce((s, b) => s + (b.bb_upper - b.bb_lower), 0) / 10;
      if (bw < avgBw * 0.7)
        alerts.push({ indicator: 'Bollinger Bands', type: 'Squeeze', severity: 'neutral',
          value: bw.toFixed(2), desc: `Band width contracted — potential breakout ahead` });
    }
  }

  return alerts;
}

const SEVERITY_STYLE = {
  bearish: {
    bg:     'rgba(248,113,113,0.08)',
    border: 'rgba(248,113,113,0.3)',
    color:  '#f87171',
    icon:   TrendingDown,
  },
  bullish: {
    bg:     'rgba(52,211,153,0.08)',
    border: 'rgba(52,211,153,0.3)',
    color:  '#34d399',
    icon:   TrendingUp,
  },
  neutral: {
    bg:     'rgba(251,191,36,0.08)',
    border: 'rgba(251,191,36,0.25)',
    color:  '#fbbf24',
    icon:   Minus,
  },
};

// ── Component ─────────────────────────────────────────────────────────────────
export default function PriceChartPanel({ ticker }) {
  const [period,    setPeriod]    = useState('5y');
  const [interval,  setInterval]  = useState('day');
  const [bars,      setBars]      = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState(null);
  const [hoverBar,  setHoverBar]  = useState(null);
  const barMap = useMemo(() => {
    const m = new Map();
    bars.forEach(b => m.set(b.time, b));
    return m;
  }, [bars]);

  // MA toggles
  const [showMA, setShowMA] = useState(
    Object.fromEntries(MA_DEFS.map(m => [m.key, true]))
  );
  // Active indicator panels (default: MACD + RSI + SR)
  const [activeIndicators, setActiveIndicators] = useState({ sr: true, macd: true, rsi: true, kdj: false, bb: false, willr: false, obv: false });
  const [srData, setSrData] = useState({ resistance: [], support: [] });
  const srDataRef = useRef({ resistance: [], support: [] });

  // Refs for DOM containers
  const containerRef = useRef(null);
  const priceRef     = useRef(null);
  const volRef       = useRef(null);
  const macdRef      = useRef(null);
  const rsiRef       = useRef(null);
  const kdjRef       = useRef(null);
  const willrRef     = useRef(null);
  const obvRef       = useRef(null);

  const panelRefs = { macd: macdRef, rsi: rsiRef, kdj: kdjRef, willr: willrRef, obv: obvRef };

  // Chart / series instances
  const chartsRef  = useRef({});
  const seriesRef  = useRef({});
  const showMARef  = useRef(showMA);
  showMARef.current = showMA;
  const activeRef  = useRef(activeIndicators);
  activeRef.current = activeIndicators;

  // ── Cache: keyed by `${ticker}__${period}__${interval}` ───────────────
  const cacheRef = useRef({});

  function cacheKey(t, p, iv) { return `${t}__${p}__${iv}`; }

  function applyData(data) {
    setBars(data.bars ?? []);
    const sr = data.sr ?? { resistance: [], support: [] };
    setSrData(sr);
    srDataRef.current = sr;
  }

  // Long lookback + daily interval is heavy; auto-switch to weekly.
  useEffect(() => {
    if ((period === '10y' || period === 'max') && interval === 'day') {
      setInterval('week');
    }
  }, [period, interval]);

  // ── Fetch (with cache + background prefetch) ───────────────────────────
  useEffect(() => {
    if (!ticker) return;
    setError(null);
    setHoverBar(null);

    // Evict cache entries for other tickers to avoid unbounded growth
    Object.keys(cacheRef.current).forEach(k => {
      if (!k.startsWith(`${ticker}__`)) delete cacheRef.current[k];
    });

    const key = cacheKey(ticker, period, interval);

    // Serve from cache instantly if available
    if (cacheRef.current[key]) {
      applyData(cacheRef.current[key]);
      setLoading(false);
    } else {
      setLoading(true);
      axios.get(`/api/stocks/${encodeURIComponent(ticker)}/chart`, { params: { period, interval } })
        .then(res => {
          cacheRef.current[key] = res.data;
          applyData(res.data);
          setLoading(false);
        })
        .catch(err => { setError(err.response?.data?.detail ?? 'Failed to load data'); setLoading(false); });
    }

    // Background prefetch the other two intervals
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

    Object.values(chartsRef.current).forEach(c => { try { c.remove(); } catch (_) {} });
    chartsRef.current = {};
    seriesRef.current = {};

    // Helper: create a new chart instance
    const makeChart = (ref, height, hideTimeScale = false) => {
      if (!ref?.current) return null;
      return createChart(ref.current, {
        ...chartOpts(height, hideTimeScale ? { visible: false } : {}),
        width: ref.current.clientWidth,
        handleScale: { mouseWheel: true, pinch: true },
        handleScroll: { mouseWheel: true, pressedMouseMove: true },
      });
    };

    // ── Price panel ──────────────────────────────────────────────────────
    const pc = makeChart(priceRef, 380, false);
    if (pc) {
      chartsRef.current.price = pc;

      const candle = pc.addSeries(CandlestickSeries, {
        upColor: '#34d399', downColor: '#f87171',
        borderUpColor: '#34d399', borderDownColor: '#f87171',
        wickUpColor: '#34d399', wickDownColor: '#f87171',
      });
      candle.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
      seriesRef.current.candle = candle;

      // MA lines
      MA_DEFS.forEach(({ key, color }) => {
        const s = pc.addSeries(LineSeries, {
          color, lineWidth: 1,
          crosshairMarkerVisible: false, lastValueVisible: false,
          priceLineVisible: false, visible: showMARef.current[key],
        });
        s.setData(bars.filter(b => b[key] != null).map(b => ({ time: b.time, value: b[key] })));
        seriesRef.current[key] = s;
      });

      // Bollinger Bands
      const bbUpper = pc.addSeries(LineSeries, {
        color: '#818cf8', lineWidth: 1, lineStyle: 2,
        crosshairMarkerVisible: false, lastValueVisible: false,
        priceLineVisible: false, visible: activeRef.current.bb,
      });
      const bbMid = pc.addSeries(LineSeries, {
        color: '#818cf855', lineWidth: 1,
        crosshairMarkerVisible: false, lastValueVisible: false,
        priceLineVisible: false, visible: activeRef.current.bb,
      });
      const bbLower = pc.addSeries(LineSeries, {
        color: '#818cf8', lineWidth: 1, lineStyle: 2,
        crosshairMarkerVisible: false, lastValueVisible: false,
        priceLineVisible: false, visible: activeRef.current.bb,
      });
      const bbData = bars.filter(b => b.bb_upper != null);
      bbUpper.setData(bbData.map(b => ({ time: b.time, value: b.bb_upper })));
      bbMid.setData(bbData.map(b => ({ time: b.time, value: b.bb_mid })));
      bbLower.setData(bbData.map(b => ({ time: b.time, value: b.bb_lower })));
      seriesRef.current.bb_upper = bbUpper;
      seriesRef.current.bb_mid   = bbMid;
      seriesRef.current.bb_lower = bbLower;

      // Support / Resistance price lines
      if (activeRef.current.sr) {
        const { resistance, support } = srDataRef.current;
        resistance.forEach((lvl, idx) => {
          candle.createPriceLine({
            price: lvl.price,
            color: `rgba(248,113,113,${0.35 + lvl.strength * 0.1})`,
            lineWidth: lvl.strength >= 3 ? 2 : 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '',
          });
        });
        support.forEach((lvl, idx) => {
          candle.createPriceLine({
            price: lvl.price,
            color: `rgba(52,211,153,${0.35 + lvl.strength * 0.1})`,
            lineWidth: lvl.strength >= 3 ? 2 : 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '',
          });
        });
      }

      // Crosshair hover
      pc.subscribeCrosshairMove(param => {
        if (!param?.time) { setHoverBar(null); return; }
        const hit = barMap.get(param.time);
        if (hit) setHoverBar(hit);
      });
    }

    // ── Volume panel ─────────────────────────────────────────────────────
    const vc = makeChart(volRef, 90, true);
    if (vc) {
      chartsRef.current.volume = vc;
      const vs = vc.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' });
      vs.priceScale().applyOptions({ scaleMarginTop: 0.1, scaleMarginBottom: 0 });
      vs.setData(bars.map(b => ({
        time: b.time, value: b.volume,
        color: b.close >= b.open ? 'rgba(52,211,153,0.5)' : 'rgba(248,113,113,0.5)',
      })));
      seriesRef.current.volume = vs;
    }

    // ── MACD panel ───────────────────────────────────────────────────────
    if (activeRef.current.macd) {
      const mc = makeChart(macdRef, 100, true);
      if (mc) {
        chartsRef.current.macd = mc;
        const macdS = mc.addSeries(LineSeries, { color: '#60a5fa', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        const sigS  = mc.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        const histS = mc.addSeries(HistogramSeries, { lastValueVisible: false, priceLineVisible: false });
        const md = bars.filter(b => b.macd != null);
        macdS.setData(md.map(b => ({ time: b.time, value: b.macd })));
        sigS.setData(md.map(b => ({ time: b.time, value: b.signal })));
        histS.setData(md.map(b => ({ time: b.time, value: b.hist, color: b.hist >= 0 ? 'rgba(52,211,153,0.6)' : 'rgba(248,113,113,0.6)' })));
        seriesRef.current.macd   = macdS;
        seriesRef.current.signal = sigS;
        seriesRef.current.hist   = histS;
      }
    }

    // ── RSI panel ────────────────────────────────────────────────────────
    if (activeRef.current.rsi) {
      const rc = makeChart(rsiRef, 100, true);
      if (rc) {
        chartsRef.current.rsi = rc;
        const rsiS = rc.addSeries(LineSeries, { color: '#fb923c', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        rsiS.setData(bars.filter(b => b.rsi != null).map(b => ({ time: b.time, value: b.rsi })));
        // Overbought / oversold reference lines
        rsiS.createPriceLine({ price: 70, color: 'rgba(248,113,113,0.5)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'OB' });
        rsiS.createPriceLine({ price: 30, color: 'rgba(52,211,153,0.5)',  lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'OS' });
        rsiS.createPriceLine({ price: 50, color: 'rgba(148,163,184,0.2)', lineWidth: 1, lineStyle: 4, axisLabelVisible: false });
        seriesRef.current.rsi = rsiS;
      }
    }

    // ── KDJ panel ────────────────────────────────────────────────────────
    if (activeRef.current.kdj) {
      const kc = makeChart(kdjRef, 100, true);
      if (kc) {
        chartsRef.current.kdj = kc;
        const kS = kc.addSeries(LineSeries, { color: '#60a5fa', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        const dS = kc.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        const jS = kc.addSeries(LineSeries, { color: '#f87171', lineWidth: 1,   lastValueVisible: false, priceLineVisible: false });
        const kd = bars.filter(b => b.k != null);
        kS.setData(kd.map(b => ({ time: b.time, value: b.k })));
        dS.setData(kd.map(b => ({ time: b.time, value: b.d })));
        jS.setData(kd.map(b => ({ time: b.time, value: b.j })));
        seriesRef.current.k = kS; seriesRef.current.d = dS; seriesRef.current.j = jS;
      }
    }

    // ── Williams %R panel ────────────────────────────────────────────────
    if (activeRef.current.willr) {
      const wc = makeChart(willrRef, 100, true);
      if (wc) {
        chartsRef.current.willr = wc;
        const wrS = wc.addSeries(LineSeries, { color: '#f472b6', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        wrS.setData(bars.filter(b => b.willr != null).map(b => ({ time: b.time, value: b.willr })));
        wrS.createPriceLine({ price: -20, color: 'rgba(248,113,113,0.5)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'OB' });
        wrS.createPriceLine({ price: -80, color: 'rgba(52,211,153,0.5)',  lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'OS' });
        seriesRef.current.willr = wrS;
      }
    }

    // ── OBV panel ────────────────────────────────────────────────────────
    if (activeRef.current.obv) {
      const oc = makeChart(obvRef, 100, true);
      if (oc) {
        chartsRef.current.obv = oc;
        const obvS = oc.addSeries(LineSeries, { color: '#2dd4bf', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
        obvS.setData(bars.filter(b => b.obv != null).map(b => ({ time: b.time, value: b.obv })));
        seriesRef.current.obv = obvS;
      }
    }

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
    const allRefs = [priceRef, volRef, macdRef, rsiRef, kdjRef, willrRef, obvRef];
    const observer = new ResizeObserver(() => {
      Object.entries(chartsRef.current).forEach(([, c]) => {
        allRefs.forEach(r => { if (r.current && c) c.applyOptions({ width: r.current.clientWidth }); });
      });
    });
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      Object.values(chartsRef.current).forEach(c => { try { c.remove(); } catch (_) {} });
      chartsRef.current = {};
      seriesRef.current = {};
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, loading, error, activeIndicators, barMap]);

  // ── Toggle MA visibility ──────────────────────────────────────────────
  const toggleMA = useCallback((key) => {
    setShowMA(prev => {
      const next = { ...prev, [key]: !prev[key] };
      seriesRef.current[key]?.applyOptions({ visible: next[key] });
      return next;
    });
  }, []);

  const toggleIndicator = useCallback((key) => {
    setActiveIndicators(prev => {
      const next = { ...prev, [key]: !prev[key] };
      // For price-panel overlays (bb, sr), toggle series visibility without full rebuild
      if (key === 'bb') {
        seriesRef.current.bb_upper?.applyOptions({ visible: next.bb });
        seriesRef.current.bb_mid?.applyOptions({ visible: next.bb });
        seriesRef.current.bb_lower?.applyOptions({ visible: next.bb });
      }
      return next;
    });
  }, []);

  const display    = hoverBar ?? bars[bars.length - 1];
  const changeAmt  = display ? display.close - display.open : 0;
  const changePct  = display?.open ? (changeAmt / display.open) * 100 : 0;
  const isUp       = changeAmt >= 0;

  const alerts     = useMemo(() => computeAlerts(bars), [bars]);
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
      if (tier === 'weak') return { bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.26)', text: '#fca5a5' };
      return { bg: 'rgba(248,113,113,0.16)', border: 'rgba(248,113,113,0.45)', text: '#fda4af' };
    }
    if (tier === 'strong') return { bg: 'rgba(52,211,153,0.26)', border: 'rgba(52,211,153,0.75)', text: '#bbf7d0' };
    if (tier === 'weak') return { bg: 'rgba(52,211,153,0.08)', border: 'rgba(52,211,153,0.26)', text: '#86efac' };
    return { bg: 'rgba(52,211,153,0.16)', border: 'rgba(52,211,153,0.45)', text: '#6ee7b7' };
  };

  // Inline panel label helper
  const PanelLabel = ({ children, extra }) => (
    <div className="px-4 py-1 flex items-center gap-3 text-[11px] font-semibold uppercase tracking-wider bg-[rgba(255,255,255,0.015)] border-t border-[var(--glass-border)]">
      {children}
      {extra}
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Controls row: period + indicators */}
      <div className="card p-3">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3 pb-3 border-b border-[var(--glass-border)]">
          <span className="text-sm font-semibold text-[var(--text-secondary)]">Price Chart</span>
          <div className="flex items-center gap-2">
            {/* Interval toggle */}
            <div className="flex gap-1 bg-[var(--bg-tertiary)] border border-[var(--glass-border)] rounded-xl p-1">
              {[{label:'D', value:'day'}, {label:'W', value:'week'}, {label:'M', value:'month'}].map(iv => (
                <button key={iv.value} onClick={() => setInterval(iv.value)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition-all ${
                    interval === iv.value ? 'bg-[#0052ff] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.07)]'
                  }`}>
                  {iv.label}
                </button>
              ))}
            </div>
            {/* Period toggle */}
            <div className="flex gap-1 bg-[var(--bg-tertiary)] border border-[var(--glass-border)] rounded-xl p-1">
              {PERIODS.map(p => (
                <button key={p.value} onClick={() => setPeriod(p.value)}
                  className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                    period === p.value ? 'bg-[#0052ff] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,82,255,0.07)]'
                  }`}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Indicator selector */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] font-semibold uppercase tracking-wider mr-1">
            <Settings2 size={13} /> Indicators
          </span>
          {INDICATOR_DEFS.map(({ key, label, desc, color }) => {
            const on = key === 'bb' ? activeIndicators.bb : activeIndicators[key];
            return (
              <button key={key} onClick={() => toggleIndicator(key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
                  on ? 'opacity-100' : 'opacity-40 hover:opacity-60'
                }`}
                style={{
                  borderColor: on ? color : BORDER,
                  background:  on ? `${color}22` : 'transparent',
                  color:       on ? color : TEXT,
                }}>
                <span className="w-2 h-2 rounded-full" style={{ background: color }} />
                {label}
                {desc && <span className="opacity-60">{desc}</span>}
              </button>
            );
          })}
        </div>
      </div>{/* end controls card */}

      {/* ── Support / Resistance Summary ── */}
      {!loading && !error && activeIndicators.sr && (srData.resistance.length > 0 || srData.support.length > 0) && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Layers size={15} className="text-[#e879f9]" />
            <span className="text-sm font-bold">Support &amp; Resistance Levels</span>
            <span className="text-xs text-[var(--text-muted)]">swing + gap + volume · {period} data</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Resistance */}
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
                          {dist !== null && (
                            <span className="text-xs text-[#f87171] font-semibold">+{dist.toFixed(1)}%</span>
                          )}
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
                            {score !== null && (
                              <span className="px-1.5 py-0.5 rounded border border-[rgba(248,113,113,0.35)] text-[#fca5a5]">
                                score {score}
                              </span>
                            )}
                            {gaps !== null && (
                              <span className="px-1.5 py-0.5 rounded border border-[rgba(248,113,113,0.28)] text-[#fda4af]">
                                gap {gaps}
                              </span>
                            )}
                            {vol !== null && (
                              <span className="px-1.5 py-0.5 rounded border border-[rgba(248,113,113,0.25)] text-[#fecaca]">
                                vol {vol}x
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })
              }
            </div>
            {/* Support */}
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
                          {dist !== null && (
                            <span className="text-xs text-[#34d399] font-semibold">{dist.toFixed(1)}%</span>
                          )}
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
                            {score !== null && (
                              <span className="px-1.5 py-0.5 rounded border border-[rgba(52,211,153,0.35)] text-[#86efac]">
                                score {score}
                              </span>
                            )}
                            {gaps !== null && (
                              <span className="px-1.5 py-0.5 rounded border border-[rgba(52,211,153,0.28)] text-[#6ee7b7]">
                                gap {gaps}
                              </span>
                            )}
                            {vol !== null && (
                              <span className="px-1.5 py-0.5 rounded border border-[rgba(52,211,153,0.25)] text-[#a7f3d0]">
                                vol {vol}x
                              </span>
                            )}
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
                <div
                  key={i}
                  className="flex items-start gap-3 px-3 py-2.5 rounded-xl border"
                  style={{ background: s.bg, borderColor: s.border }}
                >
                  <div className="mt-0.5 shrink-0 w-6 h-6 rounded-lg flex items-center justify-center"
                    style={{ background: `${s.color}22` }}>
                    <Icon size={13} style={{ color: s.color }} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-bold" style={{ color: s.color }}>
                        {a.indicator}
                      </span>
                      <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                        style={{ background: `${s.color}22`, color: s.color }}>
                        {a.type}
                      </span>
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

      {loading && (
        <div className="flex items-center justify-center py-32">
          <RefreshCw size={22} className="animate-spin text-[var(--accent-cyan)]" />
        </div>
      )}
      {error && (
        <div className="card p-6 border-[rgba(248,113,113,0.3)] text-center text-[var(--accent-danger)]">{error}</div>
      )}

      {!loading && !error && bars.length > 0 && (
        <div ref={containerRef} className="card p-0 overflow-hidden rounded-xl border border-[var(--glass-border)]">

          {/* ── OHLCV Legend ── */}
          <div className="flex flex-wrap items-center gap-4 px-4 py-2.5 border-b border-[var(--glass-border)] bg-[var(--glass-bg)]">
            {display && (
              <div className="flex items-center gap-3 text-xs font-mono flex-wrap">
                <span className="text-[var(--text-muted)]">
                  {new Date(display.time * 1000).toLocaleDateString()}
                </span>
                <span>O <span className="text-[var(--text-primary)]">{display.open?.toFixed(2)}</span></span>
                <span>H <span className="text-[var(--accent-success)]">{display.high?.toFixed(2)}</span></span>
                <span>L <span className="text-[var(--accent-danger)]">{display.low?.toFixed(2)}</span></span>
                <span>C <span className="font-bold text-[var(--text-primary)]">{display.close?.toFixed(2)}</span></span>
                <span className={isUp ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}>
                  {isUp ? '+' : ''}{changeAmt.toFixed(2)} ({changePct.toFixed(2)}%)
                </span>
                {display.rsi != null && activeIndicators.rsi && (
                  <span>RSI <span style={{ color: '#fb923c' }}>{display.rsi.toFixed(1)}</span></span>
                )}
              </div>
            )}
            {/* MA toggles */}
            <div className="flex flex-wrap gap-1.5 ml-auto">
              {MA_DEFS.map(({ key, label, color }) => (
                <button key={key} onClick={() => toggleMA(key)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-all ${showMA[key] ? 'opacity-100' : 'opacity-30'}`}
                  style={{ borderColor: showMA[key] ? color : BORDER, background: showMA[key] ? `${color}22` : 'transparent', color }}>
                  <span className="w-3 h-px rounded-full inline-block" style={{ background: color }} />
                  {label}
                </button>
              ))}
              {/* BB toggle (also in price panel) */}
              <button onClick={() => toggleIndicator('bb')}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-all ${activeIndicators.bb ? 'opacity-100' : 'opacity-30'}`}
                style={{ borderColor: activeIndicators.bb ? '#818cf8' : BORDER, background: activeIndicators.bb ? '#818cf822' : 'transparent', color: '#818cf8' }}>
                <span className="w-3 h-px rounded-full inline-block bg-[#818cf8]" />
                BB
              </button>
            </div>
          </div>

          {/* Candlestick */}
          <div ref={priceRef} style={{ width: '100%', height: 380 }} />

          {/* Volume */}
          <PanelLabel>
            <span className="text-[var(--text-muted)]">Volume</span>
          </PanelLabel>
          <div ref={volRef} style={{ width: '100%', height: 90 }} />

          {/* MACD */}
          {activeIndicators.macd && (
            <>
              <PanelLabel>
                <span className="text-[var(--text-muted)]">MACD (12,26,9)</span>
                <span style={{ color: '#60a5fa' }}>MACD</span>
                <span style={{ color: '#f59e0b' }}>Signal</span>
              </PanelLabel>
              <div ref={macdRef} style={{ width: '100%', height: 100 }} />
            </>
          )}

          {/* RSI */}
          {activeIndicators.rsi && (
            <>
              <PanelLabel extra={
                <span className="ml-auto text-[10px] text-[var(--text-muted)] normal-case font-normal">70 overbought · 30 oversold</span>
              }>
                <span className="text-[var(--text-muted)]">RSI (14)</span>
                <span style={{ color: '#fb923c' }}>RSI</span>
              </PanelLabel>
              <div ref={rsiRef} style={{ width: '100%', height: 100 }} />
            </>
          )}

          {/* KDJ */}
          {activeIndicators.kdj && (
            <>
              <PanelLabel>
                <span className="text-[var(--text-muted)]">KDJ (9,3,3)</span>
                <span style={{ color: '#60a5fa' }}>K</span>
                <span style={{ color: '#f59e0b' }}>D</span>
                <span style={{ color: '#f87171' }}>J</span>
              </PanelLabel>
              <div ref={kdjRef} style={{ width: '100%', height: 100 }} />
            </>
          )}

          {/* Williams %R */}
          {activeIndicators.willr && (
            <>
              <PanelLabel extra={
                <span className="ml-auto text-[10px] text-[var(--text-muted)] normal-case font-normal">-20 overbought · -80 oversold</span>
              }>
                <span className="text-[var(--text-muted)]">Williams %R (14)</span>
                <span style={{ color: '#f472b6' }}>%R</span>
              </PanelLabel>
              <div ref={willrRef} style={{ width: '100%', height: 100 }} />
            </>
          )}

          {/* OBV */}
          {activeIndicators.obv && (
            <>
              <PanelLabel>
                <span className="text-[var(--text-muted)]">OBV</span>
                <span style={{ color: '#2dd4bf' }}>On Balance Volume</span>
              </PanelLabel>
              <div ref={obvRef} style={{ width: '100%', height: 100 }} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
