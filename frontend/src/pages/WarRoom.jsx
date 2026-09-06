import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import axios from 'axios';
import {
  Radar,
  Activity,
  Target,
  Layers,
  Radio,
  ChevronLeft,
  ChevronRight,
  ShieldAlert,
  ShieldCheck,
  Scale,
  Grid3x3,
  Flame,
  Minus,
  ArrowUpRight,
  ArrowDownRight,
  Search,
  Crosshair,
  Zap,
  RefreshCw,
  BookOpen,
  FileText,
  ExternalLink,
  Newspaper,
  TrendingUp,
  X,
} from 'lucide-react';
import Markdown from '../components/Markdown';
import { useExtendedQuotes } from '../components/useExtendedQuotes';
import ExtendedQuoteBadge from '../components/ExtendedQuote';
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Treemap,
} from 'recharts';
import ClusterTabs from '../components/ClusterTabs';
import { OPS_TABS } from '../components/clusterTabsDef';

// ── Palette (Palantir ops-center, self-contained dark) ────────────────────────
const C = {
  bg: '#020617',
  panel: '#0F172A',
  border: '#1E293B',
  text: '#F8FAFC',
  muted: '#64748B',
  long: '#26A69A',
  short: '#EF5350',
  amber: '#F59E0B',
  accent: '#22D3EE',
  mimi: '#A78BFA',
  news: '#22D3EE', // finance-digest source chip — cyan, distinct from Mimi's violet
};

const REGIME = {
  risk_on: { label: 'RISK-ON', color: C.long, Icon: ShieldCheck },
  risk_off: { label: 'RISK-OFF', color: C.short, Icon: ShieldAlert },
  mixed: { label: 'MIXED', color: C.amber, Icon: Scale },
};

// ── Small primitives ──────────────────────────────────────────────────────────

// L-shaped corner marks + hairline border panel.
function Panel({ label, status, children, className = '' }) {
  return (
    <section
      className={`relative border p-4 ${className}`}
      style={{
        background: C.panel,
        borderColor: C.border,
        boxShadow: 'inset 0 0 24px rgba(34,211,238,0.03)',
      }}
    >
      {/* corner L-marks */}
      <Corner pos="tl" />
      <Corner pos="tr" />
      <Corner pos="bl" />
      <Corner pos="br" />
      {label && (
        <header className="mb-3 flex items-center justify-between">
          <span
            className="font-mono uppercase"
            style={{ color: C.muted, fontSize: 12, letterSpacing: '0.14em' }}
          >
            {label}
          </span>
          {status}
        </header>
      )}
      {children}
    </section>
  );
}

function Corner({ pos }) {
  const base = 'pointer-events-none absolute h-3 w-3';
  const map = {
    tl: 'left-0 top-0 border-l border-t',
    tr: 'right-0 top-0 border-r border-t',
    bl: 'left-0 bottom-0 border-l border-b',
    br: 'right-0 bottom-0 border-r border-b',
  };
  return <span className={`${base} ${map[pos]}`} style={{ borderColor: C.accent, opacity: 0.55 }} />;
}

function Dot({ color, pulse = false }) {
  return (
    <span className="relative inline-flex h-2 w-2">
      {pulse && (
        <span
          className="wr-pulse absolute inline-flex h-full w-full rounded-full"
          style={{ background: color, opacity: 0.5 }}
        />
      )}
      <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: color }} />
    </span>
  );
}

// Missing/empty portfolio → this bucket, so nothing silently disappears.
const OTHER = 'OTHER';
const normPortfolio = (p) => (p && String(p).trim() ? String(p) : OTHER);
const PORTFOLIO_LS_KEY = 'warroom.portfolio';

// Sentinel scope value for the MARKET RADAR view (not a real portfolio).
const MARKET_SCOPE = '__MARKET__';
// 機會追蹤：強弱榜 + 跨日累積的候選清單。跟 MARKET RADAR 一樣是偽 scope（不是投組）。
const TRACKER_SCOPE = '__TRACKER__';

// ── Opportunity setup config (colour + label per setup bucket) ────────────────
const SETUP_META = {
  oversold_bounce:      { label: 'OVERSOLD BOUNCE',      short: 'REBOUND',  color: C.accent },
  undervalued_catalyst: { label: 'UNDERVALUED CATALYST', short: 'VALUE',    color: C.long },
  momentum_breakout:    { label: 'MOMENTUM BREAKOUT',    short: 'MOMENTUM', color: C.amber },
  mimi_entry:           { label: 'MIMI ENTRY',           short: 'MIMI',     color: '#A78BFA' },
  breakdown_short:      { label: 'BREAKDOWN SHORT',      short: 'SHORT',    color: C.short },
};
const SETUP_ORDER = ['oversold_bounce', 'undervalued_catalyst', 'momentum_breakout', 'mimi_entry', 'breakdown_short'];
const setupMeta = (s) => SETUP_META[s] || { label: (s || 'SIGNAL').toUpperCase(), short: 'SIGNAL', color: C.muted };

// action badge for opportunity cards (distinct from holdings' actionStyle).
function oppActionStyle(action) {
  switch (action) {
    case 'buy':        return { color: C.long,  label: '買入' };
    case 'accumulate': return { color: C.long,  label: '加碼' };
    case 'watch':      return { color: C.amber, label: '觀察' };
    case 'sell_short': return { color: C.short, label: '放空' };
    case 'avoid':      return { color: C.muted, label: '避開' };
    // 未知動作值一律併入「觀察」這類,不再各自顯示原始英文字串.
    default:           return { color: C.amber, label: '觀察' };
  }
}
// action-priority: things you can act on today float up.
const OPP_ACTION_ORDER = { buy: 0, accumulate: 1, sell_short: 2, watch: 3, avoid: 9 };
const oppActionRank = (a) => (a in OPP_ACTION_ORDER ? OPP_ACTION_ORDER[a] : 5);
const OPP_ACTION_FILTERS = ['buy', 'accumulate', 'watch', 'avoid', 'sell_short'];
// RR colour bands.
const rrColor = (rr) => (rr == null ? C.muted : rr >= 3 ? C.long : rr >= 2 ? C.amber : C.muted);

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
const pctColor = (v) => (v === null || v === undefined ? C.muted : v > 0 ? C.long : v < 0 ? C.short : C.muted);
const signPct = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`);

// Hex → {r,g,b} and back, for treemap heat interpolation.
function hexRgb(hex) {
  const h = hex.replace('#', '');
  return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
}
function rgbHex({ r, g, b }) {
  const c = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
}
function mix(a, b, t) {
  return rgbHex({ r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t });
}
const _NEUTRAL = hexRgb(C.border);
const _LONG = hexRgb(C.long);
const _SHORT = hexRgb(C.short);
// day % → heat colour, saturating at ±3%.
function heatColor(pct) {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return C.border;
  const t = Math.min(Math.abs(pct) / 3, 1);
  return mix(_NEUTRAL, pct >= 0 ? _LONG : _SHORT, t);
}

// Mini SVG sparkline — closes[] → normalised polyline, coloured by net move.
function Sparkline({ data, w = 96, h = 28, color }) {
  const pts = Array.isArray(data) ? data.filter((v) => typeof v === 'number' && !Number.isNaN(v)) : [];
  if (pts.length < 2) {
    // Flat placeholder — never blank.
    return (
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <line x1="0" y1={h / 2} x2={w} y2={h / 2} stroke={C.border} strokeWidth="1" strokeDasharray="3 3" />
      </svg>
    );
  }
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const stepX = w / (pts.length - 1);
  const y = (v) => h - 2 - ((v - min) / span) * (h - 4);
  const d = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * stepX).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  const up = pts[pts.length - 1] >= pts[0];
  const stroke = color || (up ? C.long : C.short);
  const areaD = `${d} L${w},${h} L0,${h} Z`;
  const gid = `sg-${Math.round(min)}-${pts.length}-${up ? 'u' : 'd'}`;
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaD} fill={`url(#${gid})`} />
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ── Action badge styling ──────────────────────────────────────────────────────
function actionStyle(action) {
  switch (action) {
    case 'add':
      return { color: C.long, label: 'ADD' };
    case 'trim':
    case 'exit':
    case 'stop_loss':
      return { color: C.short, label: action.replace('_', ' ').toUpperCase() };
    case 'open':
      return { color: C.accent, label: 'OPEN' };
    default:
      return { color: C.muted, label: (action || 'HOLD').toUpperCase() };
  }
}

// ── Directive filtering ─────────────────────────────────────────────────────
// Action-priority for the default sort: things you actually act on today first,
// hold last. Lower number = higher up.
const ACTION_ORDER = { open: 0, add: 1, trim: 2, exit: 3, stop_loss: 4, hold: 9 };
const actionRank = (a) => (a in ACTION_ORDER ? ACTION_ORDER[a] : ACTION_ORDER.hold);
// Fixed chip order for the ACTION filter row.
const ACTION_FILTERS = ['add', 'open', 'trim', 'exit', 'stop_loss', 'hold'];
const normAction = (a) => (a || 'hold');

// Chip button — shared look for all filter toggles.
function Chip({ active, color, children, onClick }) {
  const c = color || C.accent;
  return (
    <button
      type="button"
      onClick={onClick}
      className="wr-chip cursor-pointer border px-2 py-1 font-mono text-[11px] font-bold uppercase transition-colors duration-200"
      style={{
        borderColor: active ? c : C.border,
        color: active ? c : C.muted,
        boxShadow: active ? `0 0 10px ${c}33` : 'none',
        background: active ? `${c}12` : 'transparent',
      }}
    >
      {children}
    </button>
  );
}

function DirectiveFilters({
  decisions,
  actionFilter,
  setActionFilter,
  sortMode,
  setSortMode,
  query,
  setQuery,
  atRiskCount = 0,
}) {
  // Per-action counts for the chip labels.
  const counts = useMemo(() => {
    const m = {};
    for (const d of decisions) {
      const k = normAction(d.action);
      m[k] = (m[k] || 0) + 1;
    }
    return m;
  }, [decisions]);

  return (
    <div className="mb-3 flex flex-col gap-2 border-b pb-3" style={{ borderColor: C.border }}>
      {/* ACTION row */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
          ACTION
        </span>
        <Chip active={actionFilter === 'all'} onClick={() => setActionFilter('all')}>
          ALL {decisions.length}
        </Chip>
        {ACTION_FILTERS.map((act) => {
          const n = counts[act] || 0;
          if (!n) return null;
          const st = actionStyle(act);
          return (
            <Chip
              key={act}
              active={actionFilter === act}
              color={st.color}
              onClick={() => setActionFilter(actionFilter === act ? 'all' : act)}
            >
              {st.label} {n}
            </Chip>
          );
        })}
        {atRiskCount > 0 && (
          <Chip
            active={actionFilter === 'atrisk'}
            color={C.short}
            onClick={() => setActionFilter(actionFilter === 'atrisk' ? 'all' : 'atrisk')}
          >
            AT RISK {atRiskCount}
          </Chip>
        )}
      </div>

      {/* SORT + SEARCH row (PORTFOLIO now a global scope switch, see TopStatusBar) */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
            SORT
          </span>
          <Chip active={sortMode === 'conf'} onClick={() => setSortMode('conf')}>
            CONF ↓
          </Chip>
          <Chip active={sortMode === 'ticker'} onClick={() => setSortMode('ticker')}>
            TICKER
          </Chip>
        </div>

        {/* ticker quick-search */}
        <div
          className="wr-filter-box ml-auto flex items-center gap-1.5 border px-2 py-1"
          style={{ borderColor: C.border, background: '#0B1220' }}
        >
          <Search size={11} style={{ color: C.muted }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="FILTER TICKER…"
            spellCheck={false}
            className="wr-filter-input w-32 bg-transparent font-mono text-[13px] uppercase tracking-wider outline-none"
            style={{ color: C.text }}
          />
        </div>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function WarRoom() {
  const [, navigate] = useLocation();
  const [overview, setOverview] = useState(null);
  const [dates, setDates] = useState([]);
  const [activeDate, setActiveDate] = useState(null);
  const [journal, setJournal] = useState(null);
  const [holdings, setHoldings] = useState([]);
  const [sparklines, setSparklines] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [live, setLive] = useState(true);

  // Directive filter/sort state.
  const [actionFilter, setActionFilter] = useState('all');
  const [sortMode, setSortMode] = useState('conf'); // 'conf' | 'ticker'
  const [query, setQuery] = useState('');

  // ── MARKET RADAR (opportunity scan) state ────────────────────────────────
  const [oppOverview, setOppOverview] = useState(null); // packed opportunities overview
  const [oppLoading, setOppLoading] = useState(true);
  const [oppRescanning, setOppRescanning] = useState(false);
  const [oppError, setOppError] = useState(null);
  // radar filters
  const [oppSetup, setOppSetup] = useState('all');
  const [oppAction, setOppAction] = useState('all');
  const [oppDirection, setOppDirection] = useState('all');
  const [oppSort, setOppSort] = useState('smart'); // 'smart' | 'signal' | 'conv' | 'rr'
  const [oppQuery, setOppQuery] = useState('');

  // ── Mimi source traceability ──────────────────────────────────────────────
  // One batch call tells us which tickers have an original Mimi note. Cards use
  // this set to decide whether to show the MIMI source chip — far more reliable
  // than string-matching "Mimi" inside the AI thesis.
  const [mimiTickers, setMimiTickers] = useState(() => new Set());
  const [mimiOpen, setMimiOpen] = useState(null); // ticker whose note is open

  // ── Finance-digest source traceability ────────────────────────────────────
  // Same batch-endpoint pattern as Mimi: one call returns, per ticker, the
  // recent digest articles that mention it. Cards show the NEWS chip only when
  // that list is non-empty — no string-matching, no empty chips.
  const [newsByTicker, setNewsByTicker] = useState(() => ({}));
  const [newsOpen, setNewsOpen] = useState(null); // ticker whose digest list is open

  useEffect(() => {
    let alive = true;
    axios
      .get('/api/mimi/sections')
      .then((res) => alive && setMimiTickers(new Set(res.data?.tickers || [])))
      .catch(() => {
        /* mimi source is an enhancement — never block the war room */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Global PORTFOLIO scope — the single "whose money am I looking at" switch.
  // null until we know which portfolios exist (resolved in an effect below).
  const [scope, setScope] = useState(() => {
    try {
      return localStorage.getItem(PORTFOLIO_LS_KEY) || null;
    } catch {
      return null;
    }
  });
  const setScopePersist = useCallback((p) => {
    setScope(p);
    try {
      localStorage.setItem(PORTFOLIO_LS_KEY, p);
    } catch {
      /* ignore quota / private-mode */
    }
  }, []);

  // Fetch the packed overview. `silent` skips the loading skeleton on auto-refresh.
  const fetchOverview = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    return axios
      .get('/api/war-room/overview')
      .then((res) => {
        setOverview(res.data);
        setDates(res.data.available_dates || []);
        setHoldings(res.data.holdings || []);
        setSparklines(res.data.sparklines || {});
        setLastUpdated(new Date());
        setError(null);
        // On silent refresh keep the user's selected date; only sync journal
        // when they're viewing the latest session.
        setActiveDate((cur) => cur || res.data.date || null);
        setJournal((cur) => (cur == null || !silent ? res.data.journal : cur));
        return res.data;
      })
      .catch((e) => setError(e.message || 'load failed'));
  }, []);

  // Initial packed load.
  useEffect(() => {
    let alive = true;
    fetchOverview(false).finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [fetchOverview]);

  // Auto-refresh every 5 min; pause while the tab is hidden (visibilitychange).
  useEffect(() => {
    let timer = null;
    const REFRESH_MS = 5 * 60 * 1000;
    const start = () => {
      stop();
      timer = setInterval(() => {
        if (!document.hidden) fetchOverview(true);
      }, REFRESH_MS);
      setLive(true);
    };
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
    };
    const onVis = () => {
      if (document.hidden) {
        stop();
        setLive(false);
      } else {
        fetchOverview(true);
        start();
      }
    };
    start();
    document.addEventListener('visibilitychange', onVis);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [fetchOverview]);

  // ── MARKET RADAR data ─────────────────────────────────────────────────────
  // Overview packs the latest Stage-2 opportunity journal (qualitative). When
  // no journal exists yet we fall back to Stage-1 prescreen (quant-only cards).
  const fetchOpportunities = useCallback((withPrescreen = false) => {
    return axios
      .get('/api/opportunities/overview')
      .then(async (res) => {
        const data = res.data || {};
        // If no Stage-2 journal (AI hasn't run), try Stage-1 prescreen so the
        // board still shows quant signals instead of an empty state.
        const hasJournal = data.journal && (data.journal.opportunities || []).length;
        if (!hasJournal || withPrescreen) {
          try {
            const ps = await axios.get('/api/opportunities/prescreen', {
              params: { limit: 24, setup: 'all', refresh: withPrescreen },
            });
            data.prescreen = ps.data || null;
          } catch {
            /* prescreen may be rate-limited; degrade to journal / empty */
          }
        }
        setOppOverview(data);
        setOppError(null);
        return data;
      })
      .catch((e) => setOppError(e.message || 'radar load failed'));
  }, []);

  useEffect(() => {
    let alive = true;
    fetchOpportunities(false).finally(() => alive && setOppLoading(false));
    return () => { alive = false; };
  }, [fetchOpportunities]);

  const rescan = useCallback(() => {
    setOppRescanning(true);
    fetchOpportunities(true).finally(() => setOppRescanning(false));
  }, [fetchOpportunities]);

  const allDecisions = journal?.decisions || [];

  // Distinct portfolios present across holdings + decisions, data-driven and
  // sorted so tab order is stable. OTHER (missing values) is always sorted last.
  const portfolios = useMemo(() => {
    const s = new Set();
    for (const h of holdings) s.add(normPortfolio(h.portfolio));
    for (const d of allDecisions) s.add(normPortfolio(d.portfolio));
    const arr = Array.from(s);
    arr.sort((a, b) => (a === OTHER ? 1 : b === OTHER ? -1 : a.localeCompare(b)));
    return arr;
  }, [holdings, allDecisions]);

  // 機會追蹤分頁的兩份資料。強弱榜要跑完整個 universe（15-30s），所以切到這個
  // 分頁才抓，而且抓過就不重抓 —— 別的分頁沒有理由為它等。
  const [tracker, setTracker] = useState(null);
  const [trackerLoading, setTrackerLoading] = useState(false);
  const [strength, setStrength] = useState(null);
  const [strengthLoading, setStrengthLoading] = useState(false);

  // 只在第一次切進來時抓。用 ref 當旗標而不是把 loading 放進依賴陣列 —— 後者會讓
  // setLoading 觸發 effect 重跑，cleanup 把還在飛的請求標成作廢，畫面就永遠卡在
  // 「載入中」。
  const trackerFetched = useRef(false);
  useEffect(() => {
    if (scope !== TRACKER_SCOPE || trackerFetched.current) return;
    trackerFetched.current = true;
    setTrackerLoading(true);
    axios.get('/api/opportunities/tracker')
      .then((r) => setTracker(r.data))
      .catch(() => setTracker({ items: [], count: 0, error: true }))
      .finally(() => setTrackerLoading(false));
    setStrengthLoading(true);
    axios.get('/api/opportunities/strength')
      .then((r) => setStrength(r.data))
      .catch(() => setStrength({ error: true }))
      .finally(() => setStrengthLoading(false));
  }, [scope]);

  // Resolve the active scope once portfolios are known: honour a valid stored
  // choice (MARKET_SCOPE is always valid), otherwise default to the first
  // portfolio.
  useEffect(() => {
    if (!portfolios.length) return;
    if (scope === MARKET_SCOPE || scope === TRACKER_SCOPE) return; // 偽 scope 一律有效
    if (!scope || !portfolios.includes(scope)) {
      setScope(portfolios[0]);
    }
  }, [portfolios, scope]);

  const isMarket = scope === MARKET_SCOPE;
  const isTracker = scope === TRACKER_SCOPE;
  const isPortfolioView = !isMarket && !isTracker;
  // Scope-active portfolio: never null once data loads (falls back to first).
  // For MARKET_SCOPE there is no portfolio scope — holdings/directives sections
  // are hidden entirely, so activeScope resolves to the first portfolio only so
  // downstream per-portfolio memos stay well-defined.
  const activeScope = isMarket || isTracker
    ? portfolios[0] || null
    : scope && portfolios.includes(scope)
      ? scope
      : portfolios[0] || null;

  // ── Scope filters: everything below the market layer sees only this portfolio.
  const holdings_ = useMemo(
    () => (activeScope ? holdings.filter((h) => normPortfolio(h.portfolio) === activeScope) : holdings),
    [holdings, activeScope]
  );
  const decisions = useMemo(
    () => (activeScope ? allDecisions.filter((d) => normPortfolio(d.portfolio) === activeScope) : allDecisions),
    [allDecisions, activeScope]
  );

  // Pre/post-market quotes for every position on screen. Empty during regular
  // hours, so the badges simply don't render then.
  const extendedQuotes = useExtendedQuotes(
    useMemo(() => holdings_.map((h) => h.ticker), [holdings_])
  );

  // Per-portfolio totals for the segmented control tabs (from holdings).
  const portfolioStats = useMemo(() => {
    const m = {};
    for (const p of portfolios) m[p] = { value: 0, dayPL: 0 };
    for (const h of holdings) {
      const p = normPortfolio(h.portfolio);
      const mv = h.market_value || 0;
      if (!m[p]) m[p] = { value: 0, dayPL: 0 };
      m[p].value += mv;
      // Reconstruct day $ P&L from mv and day %, so tab % is value-weighted.
      if (typeof h.day_change_pct === 'number') m[p].dayPL += (mv * h.day_change_pct) / 100;
    }
    for (const p of Object.keys(m)) {
      const { value, dayPL } = m[p];
      m[p].dayPct = value > 0 ? (dayPL / value) * 100 : null;
    }
    return m;
  }, [portfolios, holdings]);

  // ticker → holding, for directive ↔ position linkage (scoped).
  const holdingByTicker = useMemo(() => {
    const m = {};
    for (const h of holdings_) m[h.ticker] = h;
    return m;
  }, [holdings_]);

  // ticker → portfolio map from the full journal, for scoring attribution when
  // scoring rows carry no portfolio of their own.
  const tickerPortfolio = useMemo(() => {
    const m = {};
    for (const d of allDecisions) if (d.ticker) m[d.ticker] = normPortfolio(d.portfolio);
    for (const h of holdings) if (h.ticker && !(h.ticker in m)) m[h.ticker] = normPortfolio(h.portfolio);
    return m;
  }, [allDecisions, holdings]);

  // Apply ACTION + ticker filters (AND) over the scoped decisions, then sort.
  const filteredDecisions = useMemo(() => {
    const q = query.trim().toUpperCase();
    let rows = decisions.filter((d) => {
      if (actionFilter === 'atrisk') {
        // Danger set: risk-flagged holds + risk-control actions.
        const isDangerHold = atRiskBadge(d, holdingByTicker[d.ticker]?.current_price ?? null) !== null;
        const act = normAction(d.action);
        if (!isDangerHold && act !== 'stop_loss' && act !== 'trim') return false;
      } else if (actionFilter !== 'all' && normAction(d.action) !== actionFilter) {
        return false;
      }
      if (q && !(d.ticker || '').toUpperCase().includes(q)) return false;
      return true;
    });
    rows = [...rows];
    if (sortMode === 'ticker') {
      rows.sort((a, b) => (a.ticker || '').localeCompare(b.ticker || ''));
    } else {
      // Actionable directives first, then confidence descending.
      rows.sort((a, b) => {
        const ra = actionRank(normAction(a.action));
        const rb = actionRank(normAction(b.action));
        if (ra !== rb) return ra - rb;
        return (b.confidence ?? 0) - (a.confidence ?? 0);
      });
    }
    return rows;
  }, [decisions, actionFilter, query, sortMode, holdingByTicker]);

  // Count of danger directives for the AT RISK filter chip.
  const atRiskCount = useMemo(
    () =>
      decisions.reduce((n, d) => {
        const act = normAction(d.action);
        const danger =
          atRiskBadge(d, holdingByTicker[d.ticker]?.current_price ?? null) !== null ||
          act === 'stop_loss' ||
          act === 'trim';
        return danger ? n + 1 : n;
      }, 0),
    [decisions, holdingByTicker]
  );

  // ── MARKET RADAR: normalise + filter + sort opportunities ─────────────────
  // Prefer the Stage-2 journal (qualitative). If absent, promote the Stage-1
  // prescreen rows to a compact opportunity shape (quant-only, marked
  // `stage1` so cards render the "AI 盡調待執行" placeholder).
  const opportunities = useMemo(() => {
    const j = oppOverview?.journal;
    const journalOpps = j?.opportunities || [];
    if (journalOpps.length) {
      return journalOpps.map((o) => ({ ...o, stage1: false }));
    }
    const ps = oppOverview?.prescreen;
    const psOpps = ps?.opportunities || [];
    return psOpps.map((o) => {
      // Stage-1 prescreen returns `setups`; older/journal rows may use `setup_types`/`setup`.
      const setups = o.setups || o.setup_types || (o.setup ? [o.setup] : []);
      return {
        ...o,
        setup: setups[0] || o.setup,
        setup_types: setups,
        // key_numbers carries price/RSI/etc. for the quant-only card body.
        ...(o.key_numbers || {}),
        direction: o.direction || (setups[0] === 'breakdown_short' ? 'short' : 'long'),
        stage1: true,
      };
    });
  }, [oppOverview]);

  // Every ticker on screen (directives + opportunities) → one batched digest call.
  const newsKey = useMemo(() => {
    const s = new Set();
    for (const d of allDecisions) if (d?.ticker) s.add(String(d.ticker).toUpperCase());
    for (const o of opportunities) if (o?.ticker) s.add(String(o.ticker).toUpperCase());
    return Array.from(s).sort().join(',');
  }, [allDecisions, opportunities]);

  useEffect(() => {
    if (!newsKey) return undefined;
    let alive = true;
    axios
      .get(`/api/finance-digest/by-ticker?tickers=${encodeURIComponent(newsKey)}&days=21`)
      .then((res) => alive && setNewsByTicker(res.data?.items || {}))
      .catch(() => {
        /* digest is a qualitative enhancement — never block the war room */
      });
    return () => {
      alive = false;
    };
  }, [newsKey]);

  const oppCounts = useMemo(() => {
    const setup = {};
    const action = {};
    let long = 0, short = 0;
    for (const o of opportunities) {
      for (const s of o.setup_types || (o.setup ? [o.setup] : [])) setup[s] = (setup[s] || 0) + 1;
      const a = o.action || (o.stage1 ? 'watch' : 'watch');
      action[a] = (action[a] || 0) + 1;
      if (o.direction === 'short') short += 1; else long += 1;
    }
    return { setup, action, long, short };
  }, [opportunities]);

  const filteredOpps = useMemo(() => {
    const q = oppQuery.trim().toUpperCase();
    let rows = opportunities.filter((o) => {
      const setups = o.setup_types || (o.setup ? [o.setup] : []);
      if (oppSetup !== 'all' && !setups.includes(oppSetup)) return false;
      if (oppAction !== 'all' && (o.action || 'watch') !== oppAction) return false;
      if (oppDirection !== 'all' && (o.direction || 'long') !== oppDirection) return false;
      if (q && !(o.ticker || '').toUpperCase().includes(q)) return false;
      return true;
    });
    rows = [...rows];
    const bySignal = (a, b) => (b.signal_score ?? 0) - (a.signal_score ?? 0);
    const byConv = (a, b) => (b.conviction ?? 0) - (a.conviction ?? 0);
    const byRr = (a, b) => (b.rr ?? 0) - (a.rr ?? 0);
    if (oppSort === 'signal') rows.sort(bySignal);
    else if (oppSort === 'conv') rows.sort((a, b) => byConv(a, b) || bySignal(a, b));
    else if (oppSort === 'rr') rows.sort((a, b) => byRr(a, b) || bySignal(a, b));
    else {
      // smart: actionable (buy/accumulate) first, then conviction, then signal.
      rows.sort((a, b) => {
        const ra = oppActionRank(a.action || 'watch');
        const rb = oppActionRank(b.action || 'watch');
        if (ra !== rb) return ra - rb;
        return byConv(a, b) || bySignal(a, b);
      });
    }
    return rows;
  }, [opportunities, oppSetup, oppAction, oppDirection, oppSort, oppQuery]);

  const oppMarketCtx = oppOverview?.journal?.market_context || oppOverview?.prescreen?.market_context || null;
  const oppRegime = REGIME[oppMarketCtx?.regime] || REGIME.mixed;
  const oppStage2 = !!(oppOverview?.journal?.opportunities || []).length;

  // Switching historical date → fetch that journal only (market/sectors stay).
  const selectDate = (date) => {
    if (!date || date === activeDate) return;
    setActiveDate(date);
    if (overview && date === overview.date) {
      setJournal(overview.journal);
      return;
    }
    axios
      .get(`/api/war-room/journal/${date}`)
      .then((res) => setJournal(res.data))
      .catch(() => setJournal(null));
  };

  const dateIdx = dates.indexOf(activeDate);
  const goPrev = () => dateIdx < dates.length - 1 && selectDate(dates[dateIdx + 1]); // older
  const goNext = () => dateIdx > 0 && selectDate(dates[dateIdx - 1]); // newer

  const regime = REGIME[journal?.market_regime] || REGIME.mixed;
  const market = overview?.market;
  const sectors = overview?.sectors;

  return (
    <div
      className="min-h-screen font-mono"
      style={{ background: C.bg, color: C.text, fontFamily: "'Fira Sans', sans-serif" }}
    >
      <style>{wrStyles}</style>

      {/* Ticker tape — indices (market layer) + scoped holdings */}
      <TickerTape market={market} holdings={holdings_} loading={loading} />

      <div className="mx-auto max-w-[1440px] px-4 py-6 lg:px-8">
        {/* 決策層的三個切面 —— 導覽列只留一個 War Room 入口，切面在這裡切 */}
        <ClusterTabs items={OPS_TABS} active="/war-room" theme="ops" className="mb-4 w-fit" />

        <TopStatusBar
          date={activeDate}
          regime={regime}
          market={market}
          loading={loading}
          live={live}
          lastUpdated={lastUpdated}
          portfolios={portfolios}
          portfolioStats={portfolioStats}
          activeScope={isMarket ? MARKET_SCOPE : isTracker ? TRACKER_SCOPE : activeScope}
          onScope={setScopePersist}
          oppCount={oppLoading ? null : opportunities.length}
        />

        {error && !loading && (
          <div
            className="mt-4 border p-3 font-mono text-[13px]"
            style={{ borderColor: C.short, color: C.short }}
          >
            CONNECTION ERROR — {error}
          </div>
        )}

        {/* ═══ MARKET RADAR view — opportunity board replaces holdings blocks ═══ */}
        {isMarket && (
          <MarketRadar
            summary={{
              universe_size: oppOverview?.journal?.universe_size ?? oppOverview?.prescreen?.universe_size,
              universe_tiers: oppOverview?.journal?.universe_tiers ?? oppOverview?.prescreen?.universe_tiers,
              screened: oppOverview?.journal?.screened ?? oppOverview?.prescreen?.screened,
              generated_at: oppOverview?.journal?.generated_at ?? oppOverview?.prescreen?.generated_at,
              total: opportunities.length,
            }}
            marketCtx={oppMarketCtx}
            regime={oppRegime}
            stage2={oppStage2}
            loading={oppLoading}
            rescanning={oppRescanning}
            onRescan={rescan}
            error={oppError}
            opps={filteredOpps}
            allOpps={opportunities}
            counts={oppCounts}
            filters={{
              setup: oppSetup, setSetup: setOppSetup,
              action: oppAction, setAction: setOppAction,
              direction: oppDirection, setDirection: setOppDirection,
              sort: oppSort, setSort: setOppSort,
              query: oppQuery, setQuery: setOppQuery,
            }}
            onSelect={(t) => navigate(`/stock/${t}`)}
            mimiTickers={mimiTickers}
            onMimi={setMimiOpen}
            newsByTicker={newsByTicker}
            onNews={setNewsOpen}
          />
        )}

        {/* ═══ 機會追蹤 view — 強弱榜 + 跨日候選清單 ═══ */}
        {isTracker && (
          <>
            <StrengthBoard data={strength} loading={strengthLoading} />
            <TrackerPanel data={tracker} loading={trackerLoading} onSelect={(t) => navigate(`/stock/${t}`)} />
          </>
        )}

        {/* Holdings matrix — core new block, most prominent slot */}
        {isPortfolioView && (
        <>
        <div className="mt-4">
          <Panel
            label="HOLDINGS MATRIX"
            status={
              <span className="flex items-center gap-2">
                <span className="font-mono" style={{ color: C.muted, fontSize: 11 }}>
                  {holdings_.length ? `${holdings_.length} POSITIONS` : ''}
                </span>
                <HeaderIcon Icon={Grid3x3} />
              </span>
            }
          >
            <HoldingsMatrix
              holdings={holdings_}
              sparklines={sparklines}
              extended={extendedQuotes}
              loading={loading}
              onSelect={(t) => navigate(`/stock/${t}`)}
            />
          </Panel>
        </div>

        {/* P&L treemap */}
        <div className="mt-4">
          <Panel label="P&L TREEMAP // MARKET VALUE × DAY %" status={<HeaderIcon Icon={Flame} />}>
            <PnlTreemap holdings={holdings_} loading={loading} />
          </Panel>
        </div>
        </>
        )}

        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
          {/* Active directives */}
          {isPortfolioView && (
          <div className="lg:col-span-8">
            <Panel
              label="ACTIVE DIRECTIVES"
              status={
                <span className="font-mono" style={{ color: C.muted, fontSize: 11 }}>
                  {decisions.length
                    ? `SHOWING ${filteredDecisions.length}/${decisions.length}`
                    : ''}
                </span>
              }
            >
              {loading ? (
                <DirectiveSkeleton />
              ) : decisions.length ? (
                <>
                  <DirectiveFilters
                    decisions={decisions}
                    actionFilter={actionFilter}
                    setActionFilter={setActionFilter}
                    sortMode={sortMode}
                    setSortMode={setSortMode}
                    query={query}
                    setQuery={setQuery}
                    atRiskCount={atRiskCount}
                  />
                  {filteredDecisions.length ? (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {filteredDecisions.map((d, i) => (
                        <DirectiveCard
                          key={`${d.ticker}-${i}`}
                          d={d}
                          holding={holdingByTicker[d.ticker]}
                          onSelect={(t) => navigate(`/stock/${t}`)}
                          hasMimi={mimiTickers.has(d.ticker)}
                          onMimi={setMimiOpen}
                          newsCount={(newsByTicker[d.ticker] || []).length}
                          onNews={setNewsOpen}
                        />
                      ))}
                    </div>
                  ) : (
                    <div
                      className="py-12 text-center font-mono text-[13px] tracking-widest"
                      style={{ color: C.muted }}
                    >
                      NO MATCH
                    </div>
                  )}
                </>
              ) : (
                <EmptyState />
              )}
            </Panel>
          </div>
          )}

          {/* Scorecard */}
          {isPortfolioView && (
          <div className="lg:col-span-4">
            <Panel label="SCORECARD // PREV SESSION" className="h-full">
              {loading ? (
                <div className="space-y-2">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <SkelBar key={i} />
                  ))}
                </div>
              ) : (
                <Scorecard
                  scoring={journal?.scoring_of_previous}
                  scope={activeScope}
                  tickerPortfolio={tickerPortfolio}
                  onSelect={(t) => navigate(`/stock/${t}`)}
                />
              )}
            </Panel>
          </div>
          )}

          {/* Sector rotation */}
          <div className="lg:col-span-6">
            <Panel
              label="SECTOR ROTATION"
              status={<PanelLink href="/sectors" label="RS 全表" icon={<Layers size={12} />} />}
            >
              {loading ? (
                <div className="h-[320px]"><SkelBlock className="h-full" /></div>
              ) : (
                <SectorRotation sectors={sectors} />
              )}
            </Panel>
          </div>

          {/* Signal feed */}
          <div className="lg:col-span-6">
            <Panel label="SIGNAL FEED" status={<PanelLink href="/signals" label="全部訊號" icon={<Radio size={12} />} />}>
              <SignalFeed heldTickers={holdingByTicker} />
            </Panel>
          </div>

          {/* Bottom: date switcher */}
          <div className="lg:col-span-12">
            <DateSwitcher
              dates={dates}
              activeDate={activeDate}
              dateIdx={dateIdx}
              onPrev={goPrev}
              onNext={goNext}
              onSelect={selectDate}
            />
          </div>
        </div>
      </div>

      {/* Mimi original-note modal — source behind any AI citation */}
      {mimiOpen && <MimiSourceModal ticker={mimiOpen} onClose={() => setMimiOpen(null)} />}

      {/* Finance-digest modal — the news/narrative context behind an AI thesis */}
      {newsOpen && (
        <NewsDigestModal
          ticker={newsOpen}
          articles={newsByTicker[newsOpen] || []}
          onClose={() => setNewsOpen(null)}
        />
      )}
    </div>
  );
}

// ── MARKET RADAR view ─────────────────────────────────────────────────────────
// The opportunity board: AI-scanned market-wide buy/sell setups. Replaces the
// holdings-centric blocks when the MARKET RADAR scope is active.
function MarketRadar({
  summary, marketCtx, regime, stage2, loading, rescanning, onRescan, error,
  opps, allOpps, counts, filters, onSelect, mimiTickers, onMimi, newsByTicker, onNews,
}) {
  const RegIcon = regime.Icon;
  const gen = summary.generated_at ? new Date(summary.generated_at) : null;
  const genLabel = gen ? gen.toLocaleString('en-GB', { hour12: false }).replace(',', '') : '—';

  return (
    <div className="mt-4 space-y-4">
      {/* ── Summary bar ── */}
      <Panel label="MARKET RADAR // OPPORTUNITY SCAN" status={<HeaderIcon Icon={Crosshair} />}>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <div
              className="flex h-9 w-9 items-center justify-center border"
              style={{ borderColor: regime.color, color: regime.color, boxShadow: `0 0 14px ${regime.color}22` }}
            >
              <RegIcon size={16} />
            </div>
            <div className="flex flex-col">
              <span className="font-mono text-sm font-bold tracking-wider" style={{ color: regime.color }}>
                {regime.label}
              </span>
              <span
                className="max-w-[560px] text-[13px]"
                style={{ color: C.muted, fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
              >
                {marketCtx?.note || '市場脈絡待更新'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-5 font-mono">
            <RadarStat
              label="UNIVERSE"
              value={summary.universe_size}
              hint={summary.universe_tiers?.discovery
                ? `研究過 ${summary.universe_tiers.curated} ＋ 發現層 ${summary.universe_tiers.discovery}`
                : undefined}
            />
            <RadarStat label="SCREENED" value={summary.screened} />
            <RadarStat label="SIGNALS" value={summary.total} color={C.accent} />
          </div>

          <div className="ml-auto flex items-center gap-3">
            {!stage2 && (
              <span
                className="border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
                style={{ borderColor: C.amber, color: C.amber }}
                title="僅量化預篩，AI 深度盡調尚未執行"
              >
                STAGE-1 QUANT
              </span>
            )}
            <span className="font-mono text-[12px] tabular-nums" style={{ color: C.muted }}>
              {genLabel}
            </span>
            <button
              type="button"
              onClick={onRescan}
              disabled={rescanning}
              className="flex cursor-pointer items-center gap-1.5 border px-3 py-1.5 font-mono text-[11px] font-bold uppercase tracking-widest transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[#0B1220]"
              style={{ borderColor: C.accent, color: C.accent, boxShadow: `0 0 12px ${C.accent}22` }}
            >
              <RefreshCw size={12} className={rescanning ? 'wr-spin' : ''} />
              {rescanning ? 'SCANNING' : 'RESCAN'}
            </button>
          </div>
        </div>
      </Panel>

      {/* ── Opportunity board ── */}
      <Panel
        label="OPPORTUNITY BOARD"
        status={
          <span className="font-mono" style={{ color: C.muted, fontSize: 11 }}>
            {allOpps.length ? `SHOWING ${opps.length}/${allOpps.length}` : ''}
          </span>
        }
      >
        {loading ? (
          <OpportunitySkeleton />
        ) : allOpps.length ? (
          <>
            <OpportunityFilters counts={counts} total={allOpps.length} filters={filters} />
            {opps.length ? (
              // 2 cols (was 3): bigger type per card, so fewer per row.
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {opps.map((o, i) => (
                  <OpportunityCard
                    key={`${o.ticker}-${i}`}
                    o={o}
                    onSelect={onSelect}
                    hasMimi={!!mimiTickers?.has(o.ticker)}
                    onMimi={onMimi}
                    newsCount={(newsByTicker?.[o.ticker] || []).length}
                    onNews={onNews}
                  />
                ))}
              </div>
            ) : (
              <div className="py-12 text-center font-mono text-[13px] tracking-widest" style={{ color: C.muted }}>
                NO MATCH
              </div>
            )}
          </>
        ) : (
          <RadarEmpty onRescan={onRescan} rescanning={rescanning} error={error} />
        )}
      </Panel>
    </div>
  );
}

// ── 強弱榜 ────────────────────────────────────────────────────────────────────
// 「今天誰在漲」用一天就夠，「該往哪裡找機會」得看一段區間 —— 所以期間是可切的，
// 預設 1M：一天太吵（一次跳空就進榜），三個月又轉得太慢。
const SB_PERIODS = [
  { key: '1d', label: '當天' },
  { key: '1w', label: '一週' },
  { key: '1m', label: '一月' },
  { key: '3m', label: '三月' },
];

function pctCell(v) {
  if (v == null) return <span style={{ color: C.muted }}>—</span>;
  return (
    <span className="tabular-nums font-semibold" style={{ color: v >= 0 ? C.long : C.short }}>
      {v >= 0 ? '+' : ''}{v.toFixed(1)}%
    </span>
  );
}

function StrengthBoard({ data, loading }) {
  const [period, setPeriod] = useState('1m');
  const bench = data?.benchmark_returns || {};
  const themes = data?.themes || [];
  const sectors = data?.sectors || [];
  const leaders = data?.stocks?.leaders || [];
  const laggards = data?.stocks?.laggards || [];

  const byPeriod = (arr) =>
    [...arr].sort((a, b) => ((b.returns || {})[period] ?? -999) - ((a.returns || {})[period] ?? -999));
  const sortedThemes = byPeriod(themes);
  const sortedSectors = byPeriod(sectors);

  return (
    <div className="mt-4">
      <Panel
        label="STRENGTH BOARD"
        status={
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px]" style={{ color: C.muted }}>
              SPY {bench[period] == null ? '—' : `${bench[period] >= 0 ? '+' : ''}${bench[period].toFixed(1)}%`}
            </span>
            {SB_PERIODS.map((p) => (
              <Chip key={p.key} active={period === p.key} onClick={() => setPeriod(p.key)}>
                {p.label}
              </Chip>
            ))}
          </div>
        }
      >
        {loading ? (
          <div className="py-10 text-center font-mono text-[13px] tracking-widest" style={{ color: C.muted }}>
            掃描全市場中…（首次約 15-30 秒）
          </div>
        ) : !data || data.error ? (
          <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.short }}>
            強弱榜載入失敗
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {/* 主題桶：跟自己的 universe 對齊，SPDR 分不出來的細分（記憶體、AI 半導體…） */}
            <StrengthColumn
              title="主題"
              sub="自訂桶 · 桶內等權"
              rows={sortedThemes.map((t) => ({
                key: t.theme,
                name: t.theme,
                meta: `${t.count} 檔`,
                value: (t.returns || {})[period],
                extra: (t.top || []).slice(0, 3).join(' '),
              }))}
            />
            <StrengthColumn
              title="板塊"
              sub="SPDR 11 · 公認輪動"
              rows={sortedSectors.map((x) => ({
                key: x.symbol,
                name: x.symbol,
                meta: x.name,
                value: (x.returns || {})[period],
                extra: x.rs_rating != null ? `RS ${x.rs_rating}` : '',
              }))}
              empty="板塊資料暫時抓不到（Yahoo 限流），主題榜不受影響"
            />
            <StrengthColumn
              title="個股"
              sub="強勢 ↔ 弱勢（相對 SPY）"
              rows={[
                ...leaders.slice(0, 6).map((r) => ({
                  key: `L${r.ticker}`, name: r.ticker, meta: r.theme || '—',
                  value: (r.returns || {})[period], extra: '',
                })),
                { key: 'sep', sep: true },
                ...laggards.slice(0, 6).map((r) => ({
                  key: `S${r.ticker}`, name: r.ticker, meta: r.theme || '—',
                  value: (r.returns || {})[period], extra: '',
                })),
              ]}
            />
          </div>
        )}
      </Panel>
    </div>
  );
}

function StrengthColumn({ title, sub, rows, empty }) {
  return (
    <div>
      <div className="mb-2 flex items-baseline gap-2 border-b pb-1.5" style={{ borderColor: C.border }}>
        <span className="font-mono text-[12px] font-bold uppercase tracking-widest" style={{ color: C.text }}>
          {title}
        </span>
        <span className="text-[11px]" style={{ color: C.muted }}>{sub}</span>
      </div>
      {rows.length === 0 ? (
        <p className="py-4 text-center text-[12px]" style={{ color: C.muted }}>{empty || '—'}</p>
      ) : (
        <div className="space-y-0.5">
          {rows.map((r) =>
            r.sep ? (
              <div key={r.key} className="my-1.5 border-t border-dashed" style={{ borderColor: C.border }} />
            ) : (
              <div key={r.key} className="flex items-baseline gap-2 py-0.5">
                <span className="font-mono text-[13px] font-bold" style={{ color: C.text }}>{r.name}</span>
                <span className="truncate text-[11px]" style={{ color: C.muted, maxWidth: 110 }}>{r.meta}</span>
                <span className="ml-auto">{pctCell(r.value)}</span>
                {r.extra && (
                  <span className="w-[104px] shrink-0 truncate text-right font-mono text-[10px]" style={{ color: C.muted }}>
                    {r.extra}
                  </span>
                )}
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}

// ── 機會追蹤表 ─────────────────────────────────────────────────────────────────
const TRACK_STATUS = {
  in_zone:    { label: '在進場區', color: C.long },
  hit_target: { label: '已達標',   color: '#5EEAD4' },
  tracking:   { label: '追蹤中',   color: C.amber },
  stopped:    { label: '已破停損', color: C.short },
  stale:      { label: '已淡出',   color: C.muted },
};

function TrackerPanel({ data, loading, onSelect }) {
  const [status, setStatus] = useState('all');
  const items = data?.items || [];
  const counts = useMemo(() => {
    const c = {};
    items.forEach((i) => { c[i.status] = (c[i.status] || 0) + 1; });
    return c;
  }, [items]);
  const shown = status === 'all' ? items : items.filter((i) => i.status === status);

  return (
    <div className="mt-4">
      <Panel
        label="OPPORTUNITY TRACKER"
        status={
          <span className="font-mono text-[11px]" style={{ color: C.muted }}>
            {items.length ? `${shown.length}/${items.length} 檔` : ''}
          </span>
        }
      >
        {loading ? (
          <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>載入追蹤表…</div>
        ) : !items.length ? (
          <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
            追蹤表還是空的 —— 下一次掃描會自動把入圍的機會寫進來
          </div>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-1.5 border-b pb-3" style={{ borderColor: C.border }}>
              <Chip active={status === 'all'} onClick={() => setStatus('all')}>ALL {items.length}</Chip>
              {Object.entries(TRACK_STATUS).map(([k, v]) =>
                counts[k] ? (
                  <Chip key={k} active={status === k} color={v.color} onClick={() => setStatus(status === k ? 'all' : k)}>
                    {v.label} {counts[k]}
                  </Chip>
                ) : null,
              )}
            </div>

            <div className="overflow-x-auto">
              <div className="min-w-[860px]">
                <div className="grid grid-cols-[5rem_6rem_4.5rem_6rem_6rem_6rem_6rem_minmax(0,1fr)] gap-x-3 border-b pb-1.5 font-mono text-[10px] uppercase tracking-widest"
                     style={{ borderColor: C.border, color: C.muted }}>
                  <span>代號</span>
                  <span>狀態</span>
                  <span className="text-right">入圍</span>
                  <span className="text-right">基準價</span>
                  <span className="text-right">現價</span>
                  <span className="text-right">至今</span>
                  <span className="text-right">目標/停損</span>
                  <span>首見 · 最近 · 動作</span>
                </div>
                {shown.map((r) => {
                  const st = TRACK_STATUS[r.status] || TRACK_STATUS.tracking;
                  const ret = r.return_since_first;
                  return (
                    <div
                      key={r.ticker}
                      role="button"
                      tabIndex={0}
                      onClick={() => onSelect(r.ticker)}
                      onKeyDown={(e) => { if (e.key === 'Enter') onSelect(r.ticker); }}
                      className="grid cursor-pointer grid-cols-[5rem_6rem_4.5rem_6rem_6rem_6rem_6rem_minmax(0,1fr)] items-center gap-x-3 border-b py-1.5 text-[12px] transition-colors hover:bg-[#0B1220]"
                      style={{ borderColor: `${C.border}80` }}
                    >
                      <span className="font-mono text-[13px] font-bold" style={{ color: C.accent }}>{r.ticker}</span>
                      <span className="font-mono text-[11px] font-bold" style={{ color: st.color }}>{st.label}</span>
                      <span className="text-right font-mono tabular-nums" style={{ color: C.muted }}>×{r.times_seen}</span>
                      <span className="text-right font-mono tabular-nums" style={{ color: C.muted }}
                            title={r.first_price_basis === 'entry_mid' ? '沒有入圍當日價，用進場區中值當基準' : '入圍當日收盤'}>
                        {r.first_price ?? '—'}{r.first_price_basis === 'entry_mid' ? '*' : ''}
                      </span>
                      <span className="text-right font-mono tabular-nums" style={{ color: C.text }}>
                        {r.price != null ? r.price.toFixed(2) : '—'}
                      </span>
                      <span className="text-right">{pctCell(ret)}</span>
                      <span className="text-right font-mono tabular-nums text-[11px]" style={{ color: C.muted }}>
                        {r.target ?? '—'} / {r.stop ?? '—'}
                      </span>
                      <span className="truncate text-[11px]" style={{ color: C.muted }}>
                        {r.first_seen} · {r.last_seen} · {r.action || '—'}{r.theme ? ` · ${r.theme}` : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
            <p className="mt-2 text-[11px]" style={{ color: C.muted }}>
              * 基準價標星號的是進場區中值（那天的機會 JSON 沒有當日價）；
              {data?.stale_days ? ` 超過 ${data.stale_days} 天沒再入圍就標「已淡出」。` : ''}
            </p>
          </>
        )}
      </Panel>
    </div>
  );
}

function RadarStat({ label, value, color = C.text, hint }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
        {label}
      </span>
      <span className="tabular-nums text-xl font-bold" style={{ color }}>
        {value == null ? '—' : value}
      </span>
      {hint && (
        <span className="text-[10px] tabular-nums" style={{ color: C.muted }}>
          {hint}
        </span>
      )}
    </div>
  );
}

// ── Radar filter toolbar ──────────────────────────────────────────────────────
function OpportunityFilters({ counts, total, filters }) {
  const {
    setup, setSetup, action, setAction, direction, setDirection,
    sort, setSort, query, setQuery,
  } = filters;
  return (
    <div className="mb-3 flex flex-col gap-2 border-b pb-3" style={{ borderColor: C.border }}>
      {/* SETUP row */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 w-12 font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
          SETUP
        </span>
        <Chip active={setup === 'all'} onClick={() => setSetup('all')}>ALL {total}</Chip>
        {SETUP_ORDER.map((s) => {
          const n = counts.setup[s] || 0;
          if (!n) return null;
          const m = setupMeta(s);
          return (
            <Chip key={s} active={setup === s} color={m.color} onClick={() => setSetup(setup === s ? 'all' : s)}>
              {m.short} {n}
            </Chip>
          );
        })}
      </div>

      {/* ACTION + DIRECTION row */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 w-12 font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
          ACTION
        </span>
        {OPP_ACTION_FILTERS.map((a) => {
          const n = counts.action[a] || 0;
          if (!n) return null;
          const st = oppActionStyle(a);
          return (
            <Chip key={a} active={action === a} color={st.color} onClick={() => setAction(action === a ? 'all' : a)}>
              {st.label} {n}
            </Chip>
          );
        })}
        <span className="mx-1 h-3 w-px" style={{ background: C.border }} />
        <Chip active={direction === 'long'} color={C.long} onClick={() => setDirection(direction === 'long' ? 'all' : 'long')}>
          LONG {counts.long}
        </Chip>
        <Chip active={direction === 'short'} color={C.short} onClick={() => setDirection(direction === 'short' ? 'all' : 'short')}>
          SHORT {counts.short}
        </Chip>
      </div>

      {/* SORT + SEARCH row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex items-center gap-1.5">
          <span className="w-12 font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
            SORT
          </span>
          <Chip active={sort === 'smart'} onClick={() => setSort('smart')}>ACTIONABLE</Chip>
          <Chip active={sort === 'conv'} onClick={() => setSort('conv')}>CONVICTION ↓</Chip>
          <Chip active={sort === 'signal'} onClick={() => setSort('signal')}>SIGNAL ↓</Chip>
          <Chip active={sort === 'rr'} onClick={() => setSort('rr')}>RR ↓</Chip>
        </div>
        <div
          className="wr-filter-box ml-auto flex items-center gap-1.5 border px-2 py-1"
          style={{ borderColor: C.border, background: '#0B1220' }}
        >
          <Search size={11} style={{ color: C.muted }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="FILTER TICKER…"
            spellCheck={false}
            className="wr-filter-input w-32 bg-transparent font-mono text-[13px] uppercase tracking-wider outline-none"
            style={{ color: C.text }}
          />
        </div>
      </div>
    </div>
  );
}

// ── Opportunity card ──────────────────────────────────────────────────────────
function OpportunityCard({ o, onSelect, hasMimi = false, onMimi, newsCount = 0, onNews }) {
  const setups = o.setup_types && o.setup_types.length ? o.setup_types : (o.setup ? [o.setup] : []);
  const isShort = o.direction === 'short';
  const DirIcon = isShort ? ArrowDownRight : ArrowUpRight;
  const dirColor = isShort ? C.short : C.long;
  const act = oppActionStyle(o.action || 'watch');
  const sig = typeof o.signal_score === 'number' ? Math.max(0, Math.min(1, o.signal_score)) : null;
  const conv = typeof o.conviction === 'number' ? Math.max(0, Math.min(1, o.conviction)) : null;
  const rr = typeof o.rr === 'number' ? o.rr : null;
  const zone = Array.isArray(o.entry_zone) ? o.entry_zone : null;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(o.ticker)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(o.ticker); }
      }}
      className="group relative flex cursor-pointer flex-col border p-3 transition-colors duration-200"
      style={{ borderColor: C.border, background: '#0B1220' }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = C.accent;
        e.currentTarget.style.boxShadow = `0 0 16px ${C.accent}33`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = C.border;
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      {/* 未研究：發現層（全市場自動撈進來）而不是我的名單。研究過的不標，
          標記本身就是「這檔你還沒做過功課」的提醒 */}
      {o.tier === 'discovery' && !o.in_portfolio && (
        <span
          className="absolute right-2 top-2 border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider"
          style={{ borderColor: C.amber, color: C.amber, background: `${C.amber}12` }}
          title="發現層：全市場掃描撈到的，不在我的追蹤名單裡——先確認它在做什麼生意"
        >
          NEW NAME
        </span>
      )}

      {/* HELD marker */}
      {o.in_portfolio && (
        <span
          className="absolute right-2 top-2 border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider"
          style={{ borderColor: C.accent, color: C.accent, background: `${C.accent}12` }}
          title={o.held_portfolios?.length ? `Held in ${o.held_portfolios.join(', ')}` : 'In a portfolio'}
        >
          HELD
        </span>
      )}

      {/* header: ticker + direction + action */}
      <div className="flex items-start justify-between gap-2 pr-10">
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span
              className="font-mono text-2xl font-bold tracking-wide"
              style={{ color: C.text, textShadow: `0 0 10px ${dirColor}44` }}
            >
              {o.ticker}
            </span>
            <DirIcon size={18} style={{ color: dirColor }} />
          </div>
          {o.company && (
            <span className="truncate text-[12px]" style={{ color: C.muted, fontFamily: "'Fira Sans', sans-serif", maxWidth: 220 }}>
              {o.company}
            </span>
          )}
        </div>
      </div>

      {/* setup badges */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {setups.map((s) => {
          const m = setupMeta(s);
          return (
            <span
              key={s}
              className="border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider"
              style={{ borderColor: m.color, color: m.color, background: `${m.color}12` }}
            >
              {m.short}
            </span>
          );
        })}
        <span
          className="ml-auto border px-2 py-0.5 font-mono text-[11px] font-bold tracking-wider"
          style={{ borderColor: act.color, color: act.color, background: `${act.color}12` }}
        >
          {act.label}
        </span>
      </div>

      {/* score bars: signal (quant) + conviction (AI) */}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <ScoreBar label="SIGNAL" value={sig} color="#7DA0C4" />
        <ScoreBar label="CONVICTION" value={conv} color={C.accent} placeholder={conv == null} />
      </div>

      {/* trade plan */}
      <div className="mt-3 flex items-stretch gap-2 border-y py-2" style={{ borderColor: C.border }}>
        <div className="grid flex-1 grid-cols-3 gap-1 font-mono text-[14px]">
          <PlanStat label="ENTRY" value={zone ? `${fmt(zone[0])}–${fmt(zone[1])}` : null} />
          <PlanStat label="TARGET" value={o.target} color={C.long} />
          <PlanStat label="STOP" value={o.stop} color={C.short} />
        </div>
        <div className="flex flex-col items-center justify-center border-l pl-2" style={{ borderColor: C.border, minWidth: 72 }}>
          <span className="text-[10px] uppercase tracking-widest" style={{ color: C.muted }}>RR</span>
          <span className="font-mono text-2xl font-bold tabular-nums" style={{ color: rrColor(rr), textShadow: `0 0 10px ${rrColor(rr)}44` }}>
            {rr == null ? '—' : `${rr.toFixed(1)}`}
          </span>
          {o.timeframe && (
            <span className="text-[10px] uppercase tracking-wider" style={{ color: C.muted }}>{o.timeframe}</span>
          )}
        </div>
      </div>

      {/* qualitative reads or stage-1 placeholder */}
      {o.stage1 ? (
        <div className="mt-2 flex items-center gap-1.5 font-mono text-[12px] uppercase tracking-wider" style={{ color: C.amber }}>
          <Zap size={13} /> AI 盡調待執行
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {o.thesis && (
            <div
              className="text-[15px] font-semibold"
              style={{ color: C.text, fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
            >
              {o.thesis}
            </div>
          )}
          <ReadLine Icon={Activity} color="#7DA0C4" text={o.technical} />
          <ReadLine Icon={Scale} color={C.long} text={o.fundamental} />
          <ReadLine Icon={Zap} color={C.amber} text={o.catalyst} />
          {Array.isArray(o.risks) && o.risks.length > 0 && (
            <ul className="mt-1 space-y-1">
              {o.risks.map((r, i) => (
                <li
                  key={i}
                  className="flex gap-1.5 text-[13px]"
                  style={{ color: C.muted, fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
                >
                  <span style={{ color: C.short }}>▸</span>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Source chips — Mimi note + finance-digest news behind the AI's thesis */}
      {(hasMimi || newsCount > 0) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {hasMimi && <MimiChip ticker={o.ticker} onOpen={onMimi} />}
          {newsCount > 0 && <NewsChip ticker={o.ticker} count={newsCount} onOpen={onNews} />}
        </div>
      )}
    </div>
  );
}

function ReadLine({ Icon, color, text }) {
  if (!text) return null;
  return (
    <div
      className="flex gap-2 text-[14px]"
      style={{ fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
    >
      <Icon size={14} style={{ color, flexShrink: 0, marginTop: 3 }} />
      <span style={{ color: '#94A3B8' }}>{text}</span>
    </div>
  );
}

// ── Mimi source chip ──────────────────────────────────────────────────────────
// Shown on any card whose ticker has an original Mimi note. Click opens the
// modal with the raw section — the AI's "Mimi 進場區 $X" is traceable to source.
function MimiChip({ ticker, onOpen }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation(); // don't trigger the card's navigate-to-stock
        onOpen?.(ticker);
      }}
      onKeyDown={(e) => e.stopPropagation()}
      title="檢視 Mimi 原始筆記"
      className="wr-mimi-chip flex w-fit cursor-pointer items-center gap-1.5 border px-2 py-1 font-mono text-[11px] font-bold uppercase tracking-wider transition-all duration-200"
      style={{ borderColor: `${C.mimi}66`, color: C.mimi, background: `${C.mimi}10` }}
    >
      <BookOpen size={13} />
      MIMI 原始筆記
    </button>
  );
}

// ── Finance-digest source chip ────────────────────────────────────────────────
// Shown on any card whose ticker is mentioned in a recent digest article. Click
// opens the modal with the article TLDRs — the AI's news/narrative context is
// traceable to the original Substack post.
function NewsChip({ ticker, count, onOpen }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation(); // don't trigger the card's navigate-to-stock
        onOpen?.(ticker);
      }}
      onKeyDown={(e) => e.stopPropagation()}
      title={`檢視 ${ticker} 的近期財經 digest（${count} 篇）`}
      className="wr-news-chip flex w-fit cursor-pointer items-center gap-1.5 border px-2 py-1 font-mono text-[11px] font-bold uppercase tracking-wider transition-all duration-200"
      style={{ borderColor: `${C.news}66`, color: C.news, background: `${C.news}10` }}
    >
      <Newspaper size={13} />
      財經 DIGEST · {count} 篇
    </button>
  );
}

function ScoreBar({ label, value, color, placeholder = false }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between font-mono text-[11px]" style={{ color: C.muted }}>
        <span className="tracking-wider">{label}</span>
        <span style={{ color: placeholder ? C.muted : color }}>{value == null ? '—' : value.toFixed(2)}</span>
      </div>
      <div className="h-1.5 w-full" style={{ background: C.border }}>
        <div
          className="h-full transition-all duration-500"
          style={{ width: `${(value ?? 0) * 100}%`, background: color, boxShadow: value ? `0 0 8px ${color}` : 'none' }}
        />
      </div>
    </div>
  );
}

function PlanStat({ label, value, color = C.text }) {
  const empty = value === null || value === undefined;
  const display = typeof value === 'number' ? fmt(value) : value;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: C.muted }}>{label}</div>
      <div className="tabular-nums" style={{ color: empty ? C.muted : color }}>{empty ? '—' : display}</div>
    </div>
  );
}

function OpportunitySkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="wr-skel h-64 w-full" style={{ background: C.border }} />
      ))}
    </div>
  );
}

function RadarEmpty({ onRescan, rescanning, error }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <Crosshair size={30} style={{ color: C.muted }} />
      <div className="font-mono text-sm tracking-widest" style={{ color: C.muted }}>
        NO SIGNALS
      </div>
      <div className="text-[13px]" style={{ color: C.muted, fontFamily: "'Fira Sans', sans-serif" }}>
        {error ? `資料源異常 — ${error}` : '市場掃描待執行 / 資料源限流中'}
      </div>
      <button
        type="button"
        onClick={onRescan}
        disabled={rescanning}
        className="mt-1 flex cursor-pointer items-center gap-1.5 border px-3 py-1.5 font-mono text-[11px] font-bold uppercase tracking-widest transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[#0B1220]"
        style={{ borderColor: C.accent, color: C.accent }}
      >
        <RefreshCw size={12} className={rescanning ? 'wr-spin' : ''} />
        {rescanning ? 'SCANNING' : 'RESCAN'}
      </button>
    </div>
  );
}

// ── Global portfolio scope switch ─────────────────────────────────────────────
// The single "whose money am I looking at" control. Each tab shows that
// portfolio's total market value + value-weighted day %. Selected tab gets a
// cyan frame + glow so the active scope is never ambiguous.
function PortfolioSwitcher({ portfolios, stats, active, onScope, loading, oppCount }) {
  if (loading || portfolios.length < 1) return null;
  // MARKET RADAR uses a cyan-ish teal accent, kept distinct from portfolio cyan
  // by pairing it with the Crosshair icon and a "signals" sub-line.
  const RADAR = '#5EEAD4';
  const TRACK = '#C4B5FD';
  const marketOn = active === MARKET_SCOPE;
  return (
    <div
      className="wr-scope flex items-stretch gap-1 border p-1"
      style={{ borderColor: C.border, background: '#0B1220' }}
      role="tablist"
      aria-label="Portfolio scope"
    >
      {portfolios.map((p) => {
        const s = stats[p] || { value: 0, dayPct: null };
        const on = p === active;
        return (
          <button
            key={p}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onScope(p)}
            className="wr-scope-tab flex cursor-pointer flex-col items-start gap-0.5 px-3 py-1.5 transition-all duration-200"
            style={{
              borderWidth: 1,
              borderStyle: 'solid',
              borderColor: on ? C.accent : 'transparent',
              background: on ? `${C.accent}12` : 'transparent',
              boxShadow: on ? `0 0 14px ${C.accent}44` : 'none',
            }}
          >
            <span
              className="font-mono text-sm font-bold uppercase tracking-widest"
              style={{ color: on ? C.accent : C.text }}
            >
              {p}
            </span>
            <span className="flex items-baseline gap-1.5 font-mono">
              <span className="tabular-nums text-[12px]" style={{ color: on ? C.text : C.muted }}>
                ${fmt(s.value, 0)}
              </span>
              <span className="tabular-nums text-[12px] font-semibold" style={{ color: pctColor(s.dayPct) }}>
                {signPct(s.dayPct)}
              </span>
            </span>
          </button>
        );
      })}

      {/* 機會追蹤 — 強弱榜 + 跨日候選，紫色與前兩者區隔 */}
      <button
        type="button"
        role="tab"
        aria-selected={active === TRACKER_SCOPE}
        onClick={() => onScope(TRACKER_SCOPE)}
        className="wr-scope-tab flex cursor-pointer flex-col items-start gap-0.5 px-3 py-1.5 transition-all duration-200"
        style={{
          borderWidth: 1,
          borderStyle: 'solid',
          borderColor: active === TRACKER_SCOPE ? TRACK : 'transparent',
          background: active === TRACKER_SCOPE ? `${TRACK}12` : 'transparent',
          boxShadow: active === TRACKER_SCOPE ? `0 0 14px ${TRACK}44` : 'none',
        }}
      >
        <span
          className="flex items-center gap-1.5 font-mono text-sm font-bold uppercase tracking-widest"
          style={{ color: active === TRACKER_SCOPE ? TRACK : C.text }}
        >
          <TrendingUp size={13} />
          TRACKER
        </span>
        <span className="font-mono tabular-nums text-[12px]"
              style={{ color: active === TRACKER_SCOPE ? C.text : C.muted }}>
          強弱榜 · 追蹤中
        </span>
      </button>

      {/* MARKET RADAR — market-opportunity scope, teal accent + crosshair */}
      <button
        type="button"
        role="tab"
        aria-selected={marketOn}
        onClick={() => onScope(MARKET_SCOPE)}
        className="wr-scope-tab flex cursor-pointer flex-col items-start gap-0.5 px-3 py-1.5 transition-all duration-200"
        style={{
          borderWidth: 1,
          borderStyle: 'solid',
          borderColor: marketOn ? RADAR : 'transparent',
          background: marketOn ? `${RADAR}12` : 'transparent',
          boxShadow: marketOn ? `0 0 14px ${RADAR}44` : 'none',
        }}
      >
        <span
          className="flex items-center gap-1.5 font-mono text-sm font-bold uppercase tracking-widest"
          style={{ color: marketOn ? RADAR : C.text }}
        >
          <Crosshair size={13} />
          MARKET RADAR
        </span>
        <span className="flex items-baseline gap-1.5 font-mono">
          <span className="tabular-nums text-[12px]" style={{ color: marketOn ? C.text : C.muted }}>
            {oppCount != null ? `${oppCount} SIGNALS` : 'SCAN'}
          </span>
        </span>
      </button>
    </div>
  );
}

// ── Top status bar ────────────────────────────────────────────────────────────
function TopStatusBar({ date, regime, market, loading, live, lastUpdated, portfolios, portfolioStats, activeScope, onScope, oppCount }) {
  const RegIcon = regime.Icon;
  const items = (market?.items || []).filter((it) =>
    ['SPY', 'QQQ', '^VIX', 'IWM', 'BTC-USD'].includes(it.symbol)
  );
  const clock = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-GB', { hour12: false })
    : '—:—:—';
  const liveColor = live ? C.long : C.muted;
  return (
    <Panel className="!p-0">
      {/* Global portfolio scope — top slot so "whose money" is always clear */}
      <div
        className="flex flex-wrap items-center gap-2 border-b px-4 py-3"
        style={{ borderColor: C.border }}
      >
        <span className="font-mono text-[12px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
          SCOPE
        </span>
        <PortfolioSwitcher
          portfolios={portfolios}
          stats={portfolioStats}
          active={activeScope}
          onScope={onScope}
          loading={loading}
          oppCount={oppCount}
        />
      </div>
      <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
        {/* wraps on narrow screens — the LIVE/clock badge no longer overflows */}
        <div className="flex flex-wrap items-center gap-3 lg:gap-4">
          <div
            className="flex h-11 w-11 items-center justify-center border"
            style={{ borderColor: C.accent, color: C.accent }}
          >
            <Radar size={22} />
          </div>
          <div>
            <h1
              className="font-mono text-2xl font-bold tracking-widest"
              style={{ color: C.text, textShadow: `0 0 14px ${C.accent}55` }}
            >
              WAR ROOM
            </h1>
            <div className="font-mono text-[13px]" style={{ color: C.muted }}>
              {date ? `SESSION // ${date}` : 'SESSION // —'}
            </div>
          </div>

          {/* Regime badge */}
          {!loading && (
            <div
              className="ml-2 flex items-center gap-2 border px-3 py-2"
              style={{ borderColor: regime.color, color: regime.color, boxShadow: `0 0 16px ${regime.color}22` }}
            >
              <Dot color={regime.color} pulse />
              <RegIcon size={15} />
              <span className="font-mono text-sm font-bold tracking-wider">{regime.label}</span>
            </div>
          )}

          {/* LIVE indicator + clock */}
          <div
            className="ml-auto flex items-center gap-2 border px-2.5 py-1.5 lg:ml-2"
            style={{ borderColor: C.border }}
            title={live ? 'Auto-refresh active (5 min)' : 'Paused — tab hidden'}
          >
            <Dot color={liveColor} pulse={live} />
            <span className="font-mono text-[11px] font-bold tracking-widest" style={{ color: liveColor }}>
              {live ? 'LIVE' : 'PAUSED'}
            </span>
            <span className="font-mono text-[12px] tabular-nums" style={{ color: C.muted }}>
              {clock}
            </span>
          </div>
        </div>

        {/* Index ticker line */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {loading
            ? Array.from({ length: 4 }).map((_, i) => <SkelBlock key={i} className="h-5 w-24" />)
            : items.map((it) => (
                <div key={it.symbol} className="flex items-baseline gap-2 font-mono">
                  <span className="text-[13px] tracking-wider" style={{ color: C.muted }}>
                    {it.symbol.replace('^', '').replace('-USD', '')}
                  </span>
                  <span className="tabular-nums text-base" style={{ color: C.text }}>
                    {fmt(it.price)}
                  </span>
                  <span className="tabular-nums text-[13px] font-semibold" style={{ color: pctColor(it.change_pct) }}>
                    {signPct(it.change_pct)}
                  </span>
                </div>
              ))}
        </div>
      </div>
    </Panel>
  );
}

// ── Directive card ────────────────────────────────────────────────────────────
// Flag a hold that is quietly dangerous: high risk_level, near its stop, or
// already past its trigger. Returns null (no badge) or { label, color }.
// Defensive — never throws if price/stop/trigger are missing.
function atRiskBadge(d, price) {
  if (normAction(d.action) !== 'hold') return null;
  if (d.risk_level === 'high') return { label: 'AT RISK', color: C.short };
  if (typeof price === 'number' && price > 0) {
    // Near stop: within 5% above the stop (stops sit below price).
    if (typeof d.stop === 'number' && d.stop > 0 && price >= d.stop) {
      if ((price - d.stop) / price < 0.05) return { label: 'NEAR STOP', color: C.amber };
    }
    // Price already crossed the trigger in its firing direction.
    if (typeof d.trigger_price === 'number' && d.trigger_price > 0 && d.direction) {
      if (d.direction === 'below' && price <= d.trigger_price) return { label: 'AT RISK', color: C.amber };
      if (d.direction === 'above' && price >= d.trigger_price) return { label: 'AT RISK', color: C.amber };
    }
  }
  return null;
}

function DirectiveCard({ d, holding, onSelect, hasMimi = false, onMimi, newsCount = 0, onNews }) {
  const a = actionStyle(d.action);
  const conf = typeof d.confidence === 'number' ? Math.max(0, Math.min(1, d.confidence)) : null;
  const confColor = conf === null ? C.muted : conf >= 0.7 ? C.long : conf >= 0.4 ? C.amber : C.short;
  const price = holding?.current_price ?? null;
  const atRisk = atRiskBadge(d, price);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(d.ticker)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(d.ticker);
        }
      }}
      className="group relative cursor-pointer border p-3 transition-colors duration-200"
      style={{ borderColor: C.border, background: '#0B1220' }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = C.accent;
        e.currentTarget.style.boxShadow = `0 0 16px ${C.accent}33`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = C.border;
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      {/* click affordance */}
      <ArrowUpRight
        size={13}
        className="pointer-events-none absolute right-2 top-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
        style={{ color: C.accent }}
      />
      <div className="flex items-start justify-between">
        <div className="flex items-baseline gap-2">
          <span
            className="font-mono text-2xl font-bold tracking-wide"
            style={{ color: C.text, textShadow: `0 0 10px ${a.color}44` }}
          >
            {d.ticker}
          </span>
          {d.portfolio && (
            <span className="font-mono text-[11px] uppercase" style={{ color: C.muted }}>
              {d.portfolio}
            </span>
          )}
          {atRisk && (
            <span
              className="border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
              style={{ borderColor: atRisk.color, color: atRisk.color }}
            >
              {atRisk.label}
            </span>
          )}
          {price !== null && (
            <span className="font-mono text-[13px] tabular-nums" style={{ color: pctColor(holding?.day_change_pct) }}>
              ${fmt(price)} {signPct(holding?.day_change_pct)}
            </span>
          )}
        </div>
        <span
          className="mr-4 border px-2 py-0.5 font-mono text-[11px] font-bold tracking-wider"
          style={{ borderColor: a.color, color: a.color }}
        >
          {a.label}
        </span>
      </div>

      {/* Distance-to-trigger bar (only when held + a price trigger exists) */}
      {price !== null && (d.trigger_price != null || d.stop != null) && (
        <TriggerDistance price={price} trigger={d.trigger_price} stop={d.stop} />
      )}

      {/* trigger */}
      {d.trigger && (
        <div
          className="mt-2 text-[14px]"
          style={{ color: '#94A3B8', fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
        >
          {d.trigger}
        </div>
      )}

      {/* numbers */}
      <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[14px]">
        <Stat label="TRIGGER" value={d.trigger_price} />
        <Stat label="STOP" value={d.stop} color={C.short} />
        <Stat label="TGT WT" value={d.target_weight_pct} suffix="%" />
      </div>

      {/* confidence bar */}
      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between font-mono text-[12px]" style={{ color: C.muted }}>
          <span>CONFIDENCE</span>
          <span style={{ color: confColor }}>{conf === null ? '—' : conf.toFixed(2)}</span>
        </div>
        <div className="h-1.5 w-full" style={{ background: C.border }}>
          <div
            className="h-full transition-all duration-500"
            style={{ width: `${(conf ?? 0) * 100}%`, background: confColor, boxShadow: `0 0 8px ${confColor}` }}
          />
        </div>
      </div>

      {/* thesis */}
      {d.thesis && (
        <div
          className="wr-clamp-3 mt-3 text-[14px]"
          style={{ color: '#94A3B8', fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
          title={d.thesis}
        >
          {d.thesis}
        </div>
      )}

      {/* Source chips — Mimi note + finance-digest news behind the AI's thesis */}
      {(hasMimi || newsCount > 0) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {hasMimi && <MimiChip ticker={d.ticker} onOpen={onMimi} />}
          {newsCount > 0 && <NewsChip ticker={d.ticker} count={newsCount} onOpen={onNews} />}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, suffix = '', color = C.text }) {
  return (
    <div>
      <div className="text-[11px] tracking-wider" style={{ color: C.muted }}>
        {label}
      </div>
      <div className="tabular-nums" style={{ color: value === null || value === undefined ? C.muted : color }}>
        {value === null || value === undefined ? '—' : `${fmt(value)}${suffix}`}
      </div>
    </div>
  );
}

// ── Scorecard ─────────────────────────────────────────────────────────────────
const TRIGGER_STATUS = {
  triggered: { color: C.long, mark: 'dot' },
  not_triggered: { color: C.muted, mark: 'dot' },
  no_price_trigger: { color: C.muted, mark: 'dash' },
};

function Scorecard({ scoring, scope, tickerPortfolio, onSelect }) {
  // Scope the scoring rows to the active portfolio. Rows rarely carry a
  // portfolio, so fall back to the journal-derived ticker→portfolio map; rows
  // we can't attribute are shown under the current scope with a `—` marker.
  const scoped = useMemo(() => {
    const rows = scoring?.results || [];
    if (!scope) return { results: rows, unknown: new Set() };
    const out = [];
    const unknown = new Set();
    for (const r of rows) {
      const p = r.portfolio ? normPortfolio(r.portfolio) : (tickerPortfolio || {})[r.ticker];
      if (p == null) {
        unknown.add(r.ticker);
        out.push(r); // defensive: unattributed rows surface under any scope
      } else if (p === scope) {
        out.push(r);
      }
    }
    return { results: out, unknown };
  }, [scoring, scope, tickerPortfolio]);

  if (!scoring || !scoped.results.length) {
    return (
      <div className="py-6 text-center font-mono text-[13px]" style={{ color: C.muted }}>
        NO PRIOR SCORING
      </div>
    );
  }

  // Recompute hit-rate over the scoped rows (only price-triggered rows count).
  const evaluated = scoped.results.filter((r) => r.trigger_status !== 'no_price_trigger');
  const triggeredCount = evaluated.filter((r) => r.trigger_status === 'triggered').length;
  const rate = evaluated.length ? triggeredCount / evaluated.length : null;
  return (
    <div>
      <div className="mb-3 flex items-end gap-2 border-b pb-3" style={{ borderColor: C.border }}>
        <span
          className="font-mono text-3xl font-bold tabular-nums"
          style={{ color: rate >= 0.5 ? C.long : C.short, textShadow: `0 0 12px ${(rate >= 0.5 ? C.long : C.short)}44` }}
        >
          {rate === null ? '—' : `${Math.round(rate * 100)}%`}
        </span>
        <span className="mb-1 font-mono text-[12px] tracking-wider" style={{ color: C.muted }}>
          HIT RATE
          <br />
          {triggeredCount}/{evaluated.length} TRIGGERED
        </span>
      </div>
      <div className="space-y-1.5">
        {scoped.results.map((r, i) => {
          const st = TRIGGER_STATUS[r.trigger_status] || TRIGGER_STATUS.not_triggered;
          const unattributed = scoped.unknown.has(r.ticker);
          return (
            <div key={i} className="flex items-center justify-between font-mono text-[13px]">
              <div className="flex items-center gap-2">
                {st.mark === 'dash' ? (
                  <Minus size={12} style={{ color: st.color }} />
                ) : (
                  <Dot color={st.color} />
                )}
                <button
                  type="button"
                  onClick={() => onSelect?.(r.ticker)}
                  className="cursor-pointer bg-transparent font-mono transition-colors duration-200 hover:text-[color:var(--wr-accent)]"
                  style={{ color: C.text, '--wr-accent': C.accent }}
                >
                  {r.ticker}
                </button>
                <span className="text-[11px] uppercase" style={{ color: C.muted }}>
                  {r.action}
                </span>
                {unattributed && (
                  <span className="text-[11px]" style={{ color: C.muted }} title="portfolio unknown">
                    —
                  </span>
                )}
              </div>
              <span className="tabular-nums font-semibold" style={{ color: pctColor(r.return_pct) }}>
                {signPct(r.return_pct)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Sector rotation (Recharts) ────────────────────────────────────────────────
function SectorRotation({ sectors }) {
  const data = useMemo(
    () =>
      (sectors?.items || [])
        .map((s) => ({ name: s.symbol, zh: s.zh, change: s.change_pct }))
        .filter((s) => s.change !== null && s.change !== undefined)
        .sort((a, b) => b.change - a.change),
    [sectors]
  );
  if (!data.length) {
    return (
      <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
        SECTOR DATA UNAVAILABLE
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={data} layout="vertical" margin={{ left: 4, right: 24, top: 4, bottom: 4 }}>
        <CartesianGrid stroke={C.border} horizontal={false} />
        <XAxis
          type="number"
          tick={{ fill: C.muted, fontSize: 12, fontFamily: 'Fira Code' }}
          tickFormatter={(v) => `${v}%`}
          stroke={C.border}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={48}
          tick={{ fill: C.text, fontSize: 13, fontFamily: 'Fira Code' }}
          stroke={C.border}
        />
        <Tooltip
          cursor={{ fill: 'rgba(34,211,238,0.06)' }}
          contentStyle={{
            background: C.panel,
            border: `1px solid ${C.border}`,
            fontFamily: 'Fira Code',
            fontSize: 13,
            color: C.text,
          }}
          formatter={(v, _n, p) => [`${signPct(v)}  ${p.payload.zh}`, p.payload.name]}
        />
        <Bar dataKey="change" barSize={16}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.change >= 0 ? C.long : C.short} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Signal feed ───────────────────────────────────────────────────────────────
function SignalFeed({ heldTickers = {} }) {
  const [rows, setRows] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    axios
      .get('/api/signals/watchlist')
      .then((res) => alive && setRows(Array.isArray(res.data) ? res.data : []))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  if (failed) {
    return (
      <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
        SIGNAL FEED OFFLINE
      </div>
    );
  }
  if (rows === null) {
    return (
      <div className="space-y-1.5">
        {Array.from({ length: 8 }).map((_, i) => (
          <SkelBar key={i} />
        ))}
      </div>
    );
  }
  // Flatten to per-signal lines.
  const lines = [];
  for (const r of rows) {
    for (const s of r.signals || []) {
      lines.push({ ticker: r.ticker, sev: s.severity, text: `${s.indicator} ${s.type}`.toUpperCase() });
    }
  }
  if (!lines.length) {
    return (
      <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
        NO ACTIVE SIGNALS
      </div>
    );
  }
  return (
    <div className="max-h-[320px] space-y-0.5 overflow-y-auto pr-1">
      {lines.slice(0, 60).map((l, i) => {
        const color = l.sev === 'bullish' ? C.long : l.sev === 'bearish' ? C.short : C.muted;
        // Cyan left edge when the signal is on a position in the current scope.
        const held = !!heldTickers[l.ticker];
        return (
          <div
            key={i}
            className="flex items-center gap-2 border-l-2 py-1.5 pl-2 font-mono text-[13px] transition-colors duration-200 hover:bg-[#0B1220]"
            style={{ borderColor: held ? C.accent : color, background: held ? `${C.accent}0A` : 'transparent' }}
          >
            <span style={{ color: C.muted }}>{'>'}</span>
            <span className="w-14 shrink-0 font-semibold" style={{ color: C.text }}>
              {l.ticker}
            </span>
            <span className="truncate tracking-wide" style={{ color }} title={l.text}>
              {l.text}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Date switcher ─────────────────────────────────────────────────────────────
function DateSwitcher({ dates, activeDate, dateIdx, onPrev, onNext, onSelect }) {
  return (
    <Panel label="SESSION HISTORY">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onPrev}
          disabled={dateIdx >= dates.length - 1}
          className="flex cursor-pointer items-center gap-1 border px-3 py-1.5 font-mono text-[13px] transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-30 hover:bg-[#0B1220]"
          style={{ borderColor: C.border, color: C.text }}
        >
          <ChevronLeft size={14} /> OLDER
        </button>

        <div className="flex flex-wrap items-center justify-center gap-1.5">
          {dates.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => onSelect(d)}
              className="cursor-pointer border px-2.5 py-1 font-mono text-[12px] tabular-nums transition-colors duration-200"
              style={{
                borderColor: d === activeDate ? C.accent : C.border,
                color: d === activeDate ? C.accent : C.muted,
                boxShadow: d === activeDate ? `0 0 10px ${C.accent}33` : 'none',
              }}
            >
              {d}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={onNext}
          disabled={dateIdx <= 0}
          className="flex cursor-pointer items-center gap-1 border px-3 py-1.5 font-mono text-[13px] transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-30 hover:bg-[#0B1220]"
          style={{ borderColor: C.border, color: C.text }}
        >
          NEWER <ChevronRight size={14} />
        </button>
      </div>
    </Panel>
  );
}

// ── Trigger-distance bar ──────────────────────────────────────────────────────
// Visualises where current price sits inside the stop–trigger band so you can
// read "how far to触發" at a glance.
function TriggerDistance({ price, trigger, stop }) {
  const lo = stop != null ? stop : trigger != null ? trigger * 0.9 : null;
  const hi = trigger != null ? trigger : stop != null ? stop * 1.1 : null;
  if (lo == null || hi == null || hi <= lo) return null;
  const min = Math.min(lo, hi);
  const max = Math.max(lo, hi);
  // Pad band by 8% each side so an at-edge price is still visible.
  const pad = (max - min) * 0.08 || 1;
  const domLo = min - pad;
  const domHi = max + pad;
  const clamp = (v) => Math.max(0, Math.min(100, ((v - domLo) / (domHi - domLo)) * 100));
  const pStop = clamp(stop != null ? stop : min);
  const pTrig = clamp(trigger != null ? trigger : max);
  const pPrice = clamp(price);
  const ref = (trigger ?? stop) || 1;
  const near = Math.abs(price - ref) / ref;
  const priceCol = near < 0.02 ? C.amber : C.accent;
  return (
    <div className="mt-2">
      <div className="mb-1 flex items-center justify-between font-mono text-[11px] tracking-wider" style={{ color: C.muted }}>
        <span style={{ color: C.short }}>STOP {stop != null ? fmt(stop) : '—'}</span>
        <span>NOW {fmt(price)}</span>
        <span style={{ color: C.long }}>TRIG {trigger != null ? fmt(trigger) : '—'}</span>
      </div>
      <div className="relative h-2 w-full" style={{ background: C.border }}>
        {/* stop marker */}
        <span className="absolute top-0 h-full w-px" style={{ left: `${pStop}%`, background: C.short }} />
        {/* trigger marker */}
        <span className="absolute top-0 h-full w-px" style={{ left: `${pTrig}%`, background: C.long }} />
        {/* price marker */}
        <span
          className="absolute -top-0.5 h-3 w-1 -translate-x-1/2"
          style={{ left: `${pPrice}%`, background: priceCol, boxShadow: `0 0 6px ${priceCol}` }}
        />
      </div>
    </div>
  );
}

// ── Ticker tape (marquee) ─────────────────────────────────────────────────────
function TickerTape({ market, holdings, loading }) {
  const items = useMemo(() => {
    const idx = (market?.items || [])
      .filter((it) => ['SPY', 'QQQ', '^VIX', 'IWM', 'BTC-USD'].includes(it.symbol))
      .map((it) => ({
        label: it.symbol.replace('^', '').replace('-USD', ''),
        price: it.price,
        pct: it.change_pct,
      }));
    const held = holdings.map((h) => ({ label: h.ticker, price: h.current_price, pct: h.day_change_pct }));
    return [...idx, ...held];
  }, [market, holdings]);

  if (loading || !items.length) {
    return <div className="h-8 border-b" style={{ borderColor: C.border, background: C.panel }} />;
  }
  // Duplicate the run so the loop is seamless.
  const run = [...items, ...items];
  return (
    <div className="wr-tape-wrap border-b" style={{ borderColor: C.border, background: C.panel }}>
      <div className="wr-tape">
        {run.map((it, i) => (
          <span key={i} className="wr-tape-item font-mono text-[13px]">
            <span style={{ color: C.muted }}>{it.label}</span>
            <span className="tabular-nums" style={{ color: C.text }}>
              {it.price != null ? fmt(it.price) : '—'}
            </span>
            <span className="tabular-nums font-semibold" style={{ color: pctColor(it.pct) }}>
              {signPct(it.pct)}
            </span>
            <span style={{ color: C.border }}>|</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Holdings matrix ───────────────────────────────────────────────────────────
function HoldingsMatrix({ holdings, sparklines, extended = {}, loading, onSelect }) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {Array.from({ length: 12 }).map((_, i) => (
          <div key={i} className="wr-skel h-28 w-full" style={{ background: C.border }} />
        ))}
      </div>
    );
  }
  if (!holdings.length) {
    return (
      <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
        NO POSITIONS
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {holdings.map((h, i) => (
        <HoldingTile
          key={`${h.portfolio}-${h.ticker}-${h.account ?? i}`}
          h={h}
          spark={sparklines[h.ticker]}
          ext={extended[h.ticker]}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function HoldingTile({ h, spark, ext, onSelect }) {
  const dc = h.day_change_pct;
  const glow =
    dc != null && dc >= 2 ? `0 0 14px ${C.long}55` : dc != null && dc <= -2 ? `0 0 14px ${C.short}55` : 'none';
  const edge = dc != null && dc >= 2 ? C.long : dc != null && dc <= -2 ? C.short : C.border;
  const weight = Math.max(0, Math.min(100, h.weight_pct ?? 0));
  return (
    <button
      type="button"
      onClick={() => onSelect(h.ticker)}
      className="group relative flex cursor-pointer flex-col overflow-hidden border p-2 text-left transition-all duration-200"
      style={{ borderColor: edge, background: '#0B1220', boxShadow: glow }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = C.accent)}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = edge)}
      title={`${h.company || h.ticker} · ${h.portfolio}`}
    >
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xl font-bold tracking-wide" style={{ color: C.text }}>
          {h.ticker}
        </span>
        <span className="tabular-nums font-mono text-[13px]" style={{ color: C.muted }}>
          {h.current_price != null ? `$${fmt(h.current_price)}` : '—'}
        </span>
      </div>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className="tabular-nums font-mono text-xl font-bold" style={{ color: pctColor(dc) }}>
          {signPct(dc)}
        </span>
        <span className="tabular-nums font-mono text-[13px]" style={{ color: pctColor(h.return_pct) }}>
          {h.return_pct != null ? `${signPct(h.return_pct)} tot` : ''}
        </span>
      </div>
      {ext && (
        <div className="mt-0.5">
          <ExtendedQuoteBadge quote={ext} showPrice upColor={C.long} downColor={C.short} />
        </div>
      )}
      <div className="mt-1">
        <Sparkline data={spark} h={26} />
      </div>
      {/* weight bar at tile foot */}
      <div className="mt-1 h-1 w-full" style={{ background: C.border }}>
        <div className="h-full" style={{ width: `${weight}%`, background: C.accent, opacity: 0.5 }} />
      </div>
    </button>
  );
}

// ── P&L treemap ───────────────────────────────────────────────────────────────
function PnlTreemap({ holdings, loading }) {
  const data = useMemo(
    () =>
      holdings
        .filter((h) => (h.market_value || 0) > 0)
        .map((h) => ({
          name: h.ticker,
          size: h.market_value,
          pct: h.day_change_pct,
          portfolio: h.portfolio,
          ret: h.return_pct,
        })),
    [holdings]
  );
  if (loading) return <div className="wr-skel h-[300px] w-full" style={{ background: C.border }} />;
  if (!data.length) {
    return (
      <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
        NO POSITIONS
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={320}>
      <Treemap
        data={data}
        dataKey="size"
        stroke={C.bg}
        isAnimationActive={false}
        content={<TreemapCell />}
      >
        <Tooltip
          cursor={{ fill: 'rgba(34,211,238,0.06)' }}
          contentStyle={{
            background: C.panel,
            border: `1px solid ${C.border}`,
            fontFamily: 'Fira Code',
            fontSize: 13,
            color: C.text,
          }}
          formatter={(_v, _n, p) => {
            const d = p?.payload || {};
            return [`$${fmt(d.size, 0)} · ${signPct(d.pct)} day · ${signPct(d.ret)} tot`, `${d.name} (${d.portfolio})`];
          }}
        />
      </Treemap>
    </ResponsiveContainer>
  );
}

function TreemapCell(props) {
  const { x, y, width, height, name, pct } = props;
  if (width <= 0 || height <= 0) return null;
  const fill = heatColor(pct);
  const showLabel = width > 50 && height > 28;
  const showPct = width > 50 && height > 46;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke={C.bg} strokeWidth={1} />
      {showLabel && (
        <text
          x={x + 6}
          y={y + 17}
          fill={C.text}
          fontSize={13}
          fontFamily="Fira Code"
          fontWeight="bold"
        >
          {name}
        </text>
      )}
      {showPct && (
        <text x={x + 6} y={y + 33} fill={C.text} fontSize={12} fontFamily="Fira Code" opacity={0.85}>
          {signPct(pct)}
        </text>
      )}
    </g>
  );
}

// ── Mimi source modal ─────────────────────────────────────────────────────────
// Shows the ORIGINAL Mimi note section behind an AI citation, the parsed fields,
// and a deep link into Obsidian. When the backend can't read the vault file
// (TCC denial under launchd) it degrades to the synced fields + a notice — the
// panel is never blank.
function MimiSourceModal({ ticker, onClose }) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const panelRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setFailed(false);
    axios
      .get(`/api/mimi/section/${encodeURIComponent(ticker)}`)
      .then((res) => alive && setData(res.data))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [ticker]);

  // ESC to close + focus the close button on open; restore focus on unmount.
  useEffect(() => {
    const prev = document.activeElement;
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    closeRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, [onClose]);

  const fields = data?.fields && Object.keys(data.fields).length ? data.fields : data?.fallback_fields;
  const unavailable = data?.source_unavailable;
  // The ORIGINAL notes behind this ticker — the primary destination. The master
  // table (mimi操作建議總表) is only the aggregator, so it's demoted to a footer link.
  const notes = data?.source_notes || [];
  const masterUri = data?.master_table_uri || data?.obsidian_uri;

  return (
    <div
      className="wr-modal-backdrop fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // click outside closes
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${ticker} Mimi 原始筆記`}
        className="relative my-auto w-full max-w-3xl border"
        style={{ background: C.panel, borderColor: C.mimi, boxShadow: `0 0 40px ${C.mimi}33` }}
      >
        <Corner pos="tl" />
        <Corner pos="tr" />
        <Corner pos="bl" />
        <Corner pos="br" />

        {/* header */}
        <header
          className="flex flex-wrap items-center gap-3 border-b px-5 py-4"
          style={{ borderColor: C.border }}
        >
          <BookOpen size={18} style={{ color: C.mimi }} />
          <span className="font-mono text-xl font-bold tracking-wide" style={{ color: C.text }}>
            {ticker}
          </span>
          <span
            className="border px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider"
            style={{ borderColor: `${C.mimi}66`, color: C.mimi, background: `${C.mimi}10` }}
          >
            MIMI 原始筆記
          </span>
          {data?.source_path && (
            <span className="hidden font-mono text-[12px] sm:inline" style={{ color: C.muted }}>
              {data.source_path}
            </span>
          )}

          <div className="ml-auto flex items-center gap-2">
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="關閉"
              className="flex cursor-pointer items-center justify-center border p-1.5 transition-colors duration-200 hover:bg-[#0B1220]"
              style={{ borderColor: C.border, color: C.muted }}
            >
              <X size={16} />
            </button>
          </div>
        </header>

        {/* body */}
        <div className="max-h-[68vh] overflow-y-auto px-5 py-4">
          {failed || (data && !data.found) ? (
            <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
              {failed ? 'MIMI 來源服務離線' : `此標的（${ticker}）沒有 Mimi 筆記`}
            </div>
          ) : !data ? (
            <div className="space-y-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <SkelBar key={i} />
              ))}
            </div>
          ) : (
            <>
              {/* ORIGINAL notes — the primary action. A ticker accumulates one note
                  per view revision, so all of them are listed, newest-first per doc order. */}
              <div className="mb-4">
                <div
                  className="mb-2 flex items-baseline gap-2 font-mono text-[11px] uppercase tracking-[0.14em]"
                  style={{ color: C.muted }}
                >
                  <span>原始筆記</span>
                  {notes.length > 0 && <span style={{ color: C.mimi }}>· {notes.length} 篇</span>}
                </div>

                {notes.length > 0 ? (
                  <div className="flex flex-col gap-1.5">
                    {notes.map((n) => (
                      <a
                        key={n.title}
                        href={n.obsidian_uri}
                        title={n.path || n.title}
                        className="wr-mimi-note group flex cursor-pointer items-center gap-2.5 border px-3 py-2.5 transition-all duration-200"
                        style={{ borderColor: `${C.mimi}55`, background: `${C.mimi}0D` }}
                      >
                        <FileText size={15} className="shrink-0" style={{ color: C.mimi }} />
                        <span
                          className="min-w-0 flex-1 truncate text-[14px]"
                          style={{ color: C.text, fontFamily: "'Fira Sans', sans-serif" }}
                        >
                          {n.title}
                        </span>
                        {!n.found && (
                          <span
                            className="shrink-0 font-mono text-[10px] uppercase tracking-wider"
                            style={{ color: C.muted }}
                          >
                            未在 vault 索引到
                          </span>
                        )}
                        <ExternalLink size={13} className="shrink-0" style={{ color: C.mimi }} />
                      </a>
                    ))}
                  </div>
                ) : (
                  <div
                    className="border px-3 py-2.5 text-[13px]"
                    style={{ borderColor: C.border, background: '#0B1220', color: C.muted, fontFamily: "'Fira Sans', sans-serif" }}
                  >
                    {unavailable
                      ? '來源檔案讀取受限，無法解析個別筆記連結。'
                      : '此標的的總表段落沒有連回原始筆記。'}
                  </div>
                )}
              </div>

              {/* parsed field summary */}
              {fields && Object.keys(fields).length > 0 && (
                <div
                  className="mb-4 grid grid-cols-1 gap-3 border p-3 sm:grid-cols-2"
                  style={{ borderColor: C.border, background: '#0B1220' }}
                >
                  <MimiField label="進場區" value={fields.entry_zone} color={C.accent} />
                  <MimiField label="停損" value={fields.stop_loss} color={C.short} />
                  <MimiField label="目標" value={fields.target_price} color={C.long} />
                  <MimiField label="催化劑" value={fields.catalyst} color={C.amber} />
                </div>
              )}

              {/* TCC / unreadable-source notice */}
              {unavailable && (
                <div
                  className="mb-4 border px-3 py-2 text-[13px]"
                  style={{
                    borderColor: C.amber,
                    color: C.amber,
                    background: `${C.amber}0F`,
                    fontFamily: "'Fira Sans', sans-serif",
                    lineHeight: 1.6,
                  }}
                >
                  {unavailable}。以上為最近一次同步的解析欄位；點「在 OBSIDIAN 開啟」可看完整原文。
                </div>
              )}

              {/* raw markdown section */}
              {data.section && (
                <div
                  className="wr-md text-[14px]"
                  style={{
                    color: '#CBD5E1',
                    fontFamily: "'Fira Sans', sans-serif",
                    lineHeight: 1.7,
                    // Markdown.jsx is theme-var driven; the app is light-themed,
                    // so remap the vars to the war room's dark palette here.
                    '--text-primary': C.text,
                    '--text-secondary': '#CBD5E1',
                    '--text-muted': '#94A3B8',
                    '--text-body': '#CBD5E1',
                    '--bg-tertiary': 'rgba(148,163,184,0.12)',
                    '--accent-cyan': C.mimi,
                    '--accent-danger': C.short,
                  }}
                >
                  <Markdown>{data.section}</Markdown>
                </div>
              )}

              {/* the aggregator — secondary, since the originals are above */}
              {masterUri && (
                <div className="mt-5 border-t pt-3" style={{ borderColor: C.border }}>
                  <a
                    href={masterUri}
                    className="inline-flex cursor-pointer items-center gap-1.5 font-mono text-[11px] transition-colors duration-200 hover:underline"
                    style={{ color: C.muted }}
                  >
                    <ExternalLink size={11} />
                    開啟 Mimi 總表
                  </a>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Finance-digest modal ──────────────────────────────────────────────────────
// The qualitative context behind an AI thesis: every recent digest article that
// mentions this ticker — title, date, sector, TLDR (markdown), link to the
// original post. Articles are already in hand from the batch by-ticker call, so
// this renders instantly with no fetch.
function NewsDigestModal({ ticker, articles, onClose }) {
  const closeRef = useRef(null);

  // ESC to close + focus the close button on open; restore focus on unmount.
  useEffect(() => {
    const prev = document.activeElement;
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    closeRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, [onClose]);

  return (
    <div
      className="wr-modal-backdrop fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // click outside closes
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${ticker} 財經 digest`}
        className="relative my-auto w-full max-w-3xl border"
        style={{ background: C.panel, borderColor: C.news, boxShadow: `0 0 40px ${C.news}33` }}
      >
        <Corner pos="tl" />
        <Corner pos="tr" />
        <Corner pos="bl" />
        <Corner pos="br" />

        {/* header */}
        <header
          className="flex flex-wrap items-center gap-3 border-b px-5 py-4"
          style={{ borderColor: C.border }}
        >
          <Newspaper size={18} style={{ color: C.news }} />
          <span className="font-mono text-xl font-bold tracking-wide" style={{ color: C.text }}>
            {ticker}
          </span>
          <span
            className="border px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider"
            style={{ borderColor: `${C.news}66`, color: C.news, background: `${C.news}10` }}
          >
            財經 DIGEST · {articles.length} 篇
          </span>
          <span className="hidden font-mono text-[12px] sm:inline" style={{ color: C.muted }}>
            近 21 天提及此標的的每日投資筆記
          </span>

          <div className="ml-auto flex items-center gap-2">
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="關閉"
              className="flex cursor-pointer items-center justify-center border p-1.5 transition-colors duration-200 hover:bg-[#0B1220]"
              style={{ borderColor: C.border, color: C.muted }}
            >
              <X size={16} />
            </button>
          </div>
        </header>

        {/* body */}
        <div className="max-h-[68vh] overflow-y-auto px-5 py-4">
          {articles.length === 0 ? (
            <div className="py-10 text-center font-mono text-[13px]" style={{ color: C.muted }}>
              近期 digest 未提及 {ticker}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {articles.map((a) => (
                <article
                  key={`${a.date}-${a.url || a.title}`}
                  className="border p-4"
                  style={{ borderColor: C.border, background: '#0B1220' }}
                >
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span
                      className="font-mono text-[12px] tabular-nums"
                      style={{ color: C.news }}
                    >
                      {a.date}
                    </span>
                    {a.sector && (
                      <span
                        className="border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider"
                        style={{ borderColor: `${C.amber}55`, color: C.amber, background: `${C.amber}0F` }}
                      >
                        {a.sector}
                      </span>
                    )}
                  </div>

                  <h3
                    className="mb-2 text-[15px] font-semibold"
                    style={{ color: C.text, fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.5 }}
                  >
                    {a.title}
                  </h3>

                  {a.tldr && (
                    <div
                      className="wr-md text-[14px]"
                      style={{
                        color: '#CBD5E1',
                        fontFamily: "'Fira Sans', sans-serif",
                        lineHeight: 1.6,
                        // Markdown.jsx is theme-var driven; remap to the war room's dark palette.
                        '--text-primary': C.text,
                        '--text-secondary': '#CBD5E1',
                        '--text-muted': '#94A3B8',
                        '--text-body': '#CBD5E1',
                        '--bg-tertiary': 'rgba(148,163,184,0.12)',
                        '--accent-cyan': C.news,
                        '--accent-danger': C.short,
                      }}
                    >
                      <Markdown>{a.tldr}</Markdown>
                    </div>
                  )}

                  {a.url && (
                    <a
                      href={a.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="wr-news-link mt-3 inline-flex cursor-pointer items-center gap-1.5 border px-2 py-1 font-mono text-[11px] font-bold uppercase tracking-wider transition-all duration-200"
                      style={{ borderColor: `${C.news}55`, color: C.news, background: `${C.news}0D` }}
                    >
                      <ExternalLink size={12} />
                      開啟原文
                    </a>
                  )}
                </article>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MimiField({ label, value, color }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[11px] uppercase tracking-[0.14em]" style={{ color: C.muted }}>
        {label}
      </span>
      <span
        className="text-[14px]"
        style={{ color: value ? color : C.muted, fontFamily: "'Fira Sans', sans-serif", lineHeight: 1.6 }}
      >
        {value || '—'}
      </span>
    </div>
  );
}

// ── Empty / skeleton / misc ───────────────────────────────────────────────────
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <Target size={28} style={{ color: C.muted }} />
      <div className="font-mono text-sm tracking-widest" style={{ color: C.muted }}>
        NO DIRECTIVES
      </div>
      <div className="text-[13px]" style={{ color: C.muted, fontFamily: "'Fira Sans', sans-serif" }}>
        戰情分析尚未執行
      </div>
    </div>
  );
}

function HeaderIcon({ Icon }) {
  return <Icon size={13} style={{ color: C.muted }} />;
}

// 看板方塊是摘要，全表在別頁 —— 標題列給一個明確的門，而不是讓兩個入口平行存在。
function PanelLink({ href, label, icon }) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider transition-colors duration-150 hover:opacity-80"
      style={{ color: C.muted }}
    >
      {icon}
      {label}
      <ArrowUpRight size={11} />
    </Link>
  );
}

function SkelBlock({ className = '' }) {
  return <div className={`wr-skel ${className}`} style={{ background: C.border }} />;
}
function SkelBar() {
  return <div className="wr-skel h-5 w-full" style={{ background: C.border }} />;
}
function DirectiveSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="wr-skel h-40 w-full" style={{ background: C.border }} />
      ))}
    </div>
  );
}

const wrStyles = `
@keyframes wr-pulse { 0% { transform: scale(1); opacity: 0.5; } 100% { transform: scale(2.6); opacity: 0; } }
@keyframes wr-skel { 0%,100% { opacity: 0.4; } 50% { opacity: 0.7; } }
@keyframes wr-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes wr-spin { to { transform: rotate(360deg); } }
.wr-spin { animation: wr-spin 0.9s linear infinite; }
.wr-pulse { animation: wr-pulse 1.8s cubic-bezier(0,0,0.2,1) infinite; }
.wr-skel { animation: wr-skel 1.4s ease-in-out infinite; }
.wr-tape-wrap { overflow: hidden; white-space: nowrap; }
.wr-tape { display: inline-flex; align-items: center; padding: 6px 0; animation: wr-marquee 90s linear infinite; will-change: transform; }
.wr-tape-wrap:hover .wr-tape { animation-play-state: paused; }
.wr-tape-item { display: inline-flex; align-items: center; gap: 6px; padding: 0 12px; }
.wr-chip { letter-spacing: 0.1em; }
/* Long AI theses: 3 lines max on the card, full text in the tooltip/detail. */
.wr-clamp-3 { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.wr-mimi-chip:hover { background: rgba(167,139,250,0.18) !important; border-color: #A78BFA !important; box-shadow: 0 0 12px rgba(167,139,250,0.35); }
/* Individual original-note rows in the Mimi modal — the primary source link. */
.wr-mimi-note:hover { background: rgba(167,139,250,0.16) !important; border-color: #A78BFA !important; box-shadow: 0 0 12px rgba(167,139,250,0.28); transform: translateX(2px); }
/* Finance-digest source chip + the "open original" link inside its modal. */
.wr-news-chip:hover { background: rgba(34,211,238,0.18) !important; border-color: #22D3EE !important; box-shadow: 0 0 12px rgba(34,211,238,0.35); }
.wr-news-link:hover { background: rgba(34,211,238,0.18) !important; border-color: #22D3EE !important; box-shadow: 0 0 12px rgba(34,211,238,0.28); }
.wr-modal-backdrop { background: rgba(2,6,23,0.82); backdrop-filter: blur(3px); }
/* Raw Mimi markdown inside the modal — tables are the note's core format. */
.wr-md table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; display: block; overflow-x: auto; }
.wr-md th, .wr-md td { border: 1px solid #1E293B; padding: 6px 10px; text-align: left; }
.wr-md th { background: #0B1220; color: #F8FAFC; font-family: 'Fira Code', monospace; font-size: 12px; letter-spacing: 0.04em; }
.wr-md td { color: #CBD5E1; }
.wr-md hr { border: 0; border-top: 1px solid #1E293B; margin: 16px 0; }
.wr-md strong { color: #F8FAFC; }
.wr-filter-input::placeholder { color: #475569; letter-spacing: 0.12em; }
.wr-filter-box:focus-within { border-color: #22D3EE !important; box-shadow: 0 0 10px rgba(34,211,238,0.25); }
.wr-scope { flex-wrap: wrap; }
/* Keep the whole segmented control visible on narrow phones by shrinking type. */
@media (max-width: 480px) {
  .wr-scope-tab { padding-left: 0.5rem; padding-right: 0.5rem; }
  .wr-scope-tab > span:first-child { font-size: 0.75rem; letter-spacing: 0.12em; }
  .wr-scope-tab .tabular-nums { font-size: 10px; }
}
@media (prefers-reduced-motion: reduce) {
  .wr-pulse, .wr-skel, .wr-spin { animation: none !important; }
  .wr-tape { animation: none !important; }
  .wr-tape-wrap { overflow-x: auto; }
  .wr-scope-tab { transition: none !important; }
}
`;
