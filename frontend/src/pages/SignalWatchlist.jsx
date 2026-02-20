import { useState, useEffect, useMemo } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  Radar, RefreshCw, TrendingUp, TrendingDown, Minus,
  ChevronRight, Clock, BarChart3, Filter,
} from 'lucide-react';

// ── Helpers ──────────────────────────────────────────────────────────────────
const SEV = {
  bearish: { bg: 'rgba(248,113,113,0.1)',  border: 'rgba(248,113,113,0.25)', color: '#f87171', icon: TrendingDown },
  bullish: { bg: 'rgba(52,211,153,0.1)',   border: 'rgba(52,211,153,0.25)',  color: '#34d399', icon: TrendingUp  },
  neutral: { bg: 'rgba(251,191,36,0.1)',   border: 'rgba(251,191,36,0.2)',   color: '#fbbf24', icon: Minus       },
};

const FILTER_OPTS = [
  { key: 'all',     label: 'All' },
  { key: 'bullish', label: 'Bullish' },
  { key: 'bearish', label: 'Bearish' },
];

function SignalBadge({ signal }) {
  const s = SEV[signal.severity] ?? SEV.neutral;
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold border whitespace-nowrap"
      style={{ background: s.bg, borderColor: s.border, color: s.color }}
    >
      {signal.indicator} · {signal.type}
    </span>
  );
}

function ValuationMini({ v, liveClose }) {
  if (!v) return null;
  const price    = liveClose ?? v.current_price;
  const fairPE   = v.pe_median_price;
  const diffPE   = fairPE ? ((price - fairPE) / fairPE * 100) : null;
  const isUnder  = diffPE !== null && diffPE < 0;

  return (
    <div className="flex flex-wrap items-center gap-3 text-xs mt-2">
      <span className="font-mono font-bold text-base text-[var(--text-primary)]">
        ${price?.toFixed(2)}
      </span>
      {fairPE && (
        <>
          <span className="text-[var(--text-muted)]">PE Fair <span className="font-semibold text-[var(--text-secondary)]">${fairPE}</span></span>
          <span className={`font-semibold px-1.5 py-0.5 rounded ${
            isUnder
              ? 'bg-[rgba(52,211,153,0.1)] text-[#34d399]'
              : 'bg-[rgba(248,113,113,0.1)] text-[#f87171]'
          }`}>
            {isUnder ? '' : '+'}{diffPE?.toFixed(1)}%
          </span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${
            isUnder
              ? 'bg-[rgba(52,211,153,0.08)] text-[#34d399] border border-[rgba(52,211,153,0.2)]'
              : 'bg-[rgba(248,113,113,0.08)] text-[#f87171] border border-[rgba(248,113,113,0.2)]'
          }`}>
            {v.valuation_status}
          </span>
        </>
      )}
      {v.pe_median_5y && (
        <span className="text-[var(--text-muted)]">Median PE <span className="font-semibold text-[var(--text-secondary)]">{v.pe_median_5y}</span></span>
      )}
    </div>
  );
}

function StockCard({ item }) {
  const bullish = item.signals.filter(s => s.severity === 'bullish');
  const bearish = item.signals.filter(s => s.severity === 'bearish');
  const overall = bearish.length > bullish.length ? 'bearish'
                : bullish.length > bearish.length ? 'bullish'
                : 'neutral';
  const s = SEV[overall];
  const Icon = s.icon;

  return (
    <Link href={`/stock/${item.ticker}`}>
      <div className="card p-4 cursor-pointer hover:border-[rgba(139,92,246,0.3)] hover:shadow-lg hover:shadow-purple-500/5 transition-all duration-200 group">
        {/* Top row */}
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-3">
            {/* Icon */}
            <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: `${s.color}22`, border: `1px solid ${s.border}` }}>
              <Icon size={16} style={{ color: s.color }} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-base font-mono">{item.ticker}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[rgba(255,255,255,0.06)] text-[var(--text-muted)] font-medium">
                  {item.industry}
                </span>
              </div>
              <p className="text-xs text-[var(--text-muted)] truncate max-w-[200px]">{item.company_name}</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {bullish.length > 0 && (
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-[rgba(52,211,153,0.1)] text-[#34d399] border border-[rgba(52,211,153,0.2)]">
                {bullish.length} bull
              </span>
            )}
            {bearish.length > 0 && (
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-[rgba(248,113,113,0.1)] text-[#f87171] border border-[rgba(248,113,113,0.2)]">
                {bearish.length} bear
              </span>
            )}
            <ChevronRight size={14} className="text-[var(--text-muted)] group-hover:text-[var(--accent-purple)] transition-colors" />
          </div>
        </div>

        {/* Signal badges */}
        <div className="flex flex-wrap gap-1.5 mb-3">
          {item.signals.map((sig, i) => <SignalBadge key={i} signal={sig} />)}
        </div>

        {/* Valuation mini */}
        <div className="pt-2.5 border-t border-[var(--glass-border)]">
          <ValuationMini v={item.valuation} liveClose={item.latest_close} />
        </div>
      </div>
    </Link>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function SignalWatchlist() {
  const [items,     setItems]     = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState(null);
  const [scannedAt, setScannedAt] = useState(null);
  const [filter,    setFilter]    = useState('all');
  const [search,    setSearch]    = useState('');
  const [scanning,  setScanning]  = useState(false);

  const fetchData = async (bust = false) => {
    setLoading(true);
    setError(null);
    if (bust) {
      setScanning(true);
      await axios.delete('/api/signals/watchlist/cache').catch(() => {});
    }
    try {
      const res = await axios.get('/api/signals/watchlist');
      setItems(res.data);
      setScannedAt(new Date());
    } catch (e) {
      setError(e.response?.data?.detail ?? 'Scan failed');
    } finally {
      setLoading(false);
      setScanning(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  const filtered = useMemo(() => {
    let list = items;
    if (filter !== 'all') {
      list = list.filter(item =>
        item.signals.some(s => s.severity === filter)
      );
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(item =>
        item.ticker.toLowerCase().includes(q) ||
        item.company_name.toLowerCase().includes(q) ||
        item.industry.toLowerCase().includes(q)
      );
    }
    return list;
  }, [items, filter, search]);

  const bullishCount = items.filter(i => i.signals.some(s => s.severity === 'bullish')).length;
  const bearishCount = items.filter(i => i.signals.some(s => s.severity === 'bearish')).length;

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-purple)] to-[var(--accent-cyan)] flex items-center justify-center shadow-lg shadow-purple-500/20">
              <Radar size={17} className="text-white" />
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight">Signal Watchlist</h1>
          </div>
          <p className="text-sm text-[var(--text-muted)] ml-12">
            Stocks with active technical signals from the last trading day
          </p>
        </div>

        <div className="flex items-center gap-3">
          {scannedAt && (
            <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
              <Clock size={12} />
              {scannedAt.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={() => fetchData(true)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-[rgba(139,92,246,0.1)] border border-[rgba(139,92,246,0.2)] text-[var(--accent-purple)] hover:bg-[rgba(139,92,246,0.18)] transition-all disabled:opacity-50"
          >
            <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
            {scanning ? 'Scanning…' : 'Re-scan'}
          </button>
        </div>
      </div>

      {/* Stats row */}
      {!loading && !error && (
        <div className="grid grid-cols-3 gap-3">
          <div className="card p-4 text-center">
            <div className="text-2xl font-extrabold font-mono">{items.length}</div>
            <div className="text-xs text-[var(--text-muted)] mt-1 uppercase tracking-wider">Total Signals</div>
          </div>
          <div className="card p-4 text-center border-[rgba(52,211,153,0.2)]">
            <div className="text-2xl font-extrabold font-mono text-[#34d399]">{bullishCount}</div>
            <div className="text-xs text-[var(--text-muted)] mt-1 uppercase tracking-wider">Bullish</div>
          </div>
          <div className="card p-4 text-center border-[rgba(248,113,113,0.2)]">
            <div className="text-2xl font-extrabold font-mono text-[#f87171]">{bearishCount}</div>
            <div className="text-xs text-[var(--text-muted)] mt-1 uppercase tracking-wider">Bearish</div>
          </div>
        </div>
      )}

      {/* Filter + search bar */}
      {!loading && !error && items.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          {/* Severity filter */}
          <div className="flex gap-1 bg-[var(--glass-bg)] border border-[var(--glass-border)] rounded-xl p-1">
            {FILTER_OPTS.map(opt => (
              <button
                key={opt.key}
                onClick={() => setFilter(opt.key)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition-all ${
                  filter === opt.key
                    ? 'bg-[var(--accent-purple)] text-white'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Search input */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]">
            <Filter size={13} className="text-[var(--text-muted)]" />
            <input
              type="text"
              placeholder="Filter by ticker / name / industry…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="bg-transparent text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none w-56"
            />
          </div>

          <span className="text-xs text-[var(--text-muted)] ml-auto">
            {filtered.length} of {items.length} stocks
          </span>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex flex-col items-center justify-center py-32 gap-4">
          <RefreshCw size={28} className="animate-spin text-[var(--accent-purple)]" />
          <p className="text-sm text-[var(--text-muted)]">
            Scanning {182} tickers for signals — this may take 20–40 seconds…
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="card p-6 border-[rgba(248,113,113,0.3)] text-center text-[var(--accent-danger)]">{error}</div>
      )}

      {/* Empty state */}
      {!loading && !error && filtered.length === 0 && (
        <div className="card p-12 text-center">
          <BarChart3 size={36} className="mx-auto mb-3 text-[var(--text-muted)] opacity-30" />
          <p className="text-[var(--text-muted)] text-sm">
            {items.length === 0 ? 'No active signals found across all tracked stocks.' : 'No stocks match your filter.'}
          </p>
        </div>
      )}

      {/* Cards grid */}
      {!loading && !error && filtered.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map(item => (
            <StockCard key={item.ticker} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}
