import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { TrendingUp, TrendingDown, Activity, RefreshCw, AlertTriangle, Globe2 } from 'lucide-react';
import { Link } from 'wouter';
import { useExtendedQuotes } from '../components/useExtendedQuotes';
import ExtendedQuoteBadge from '../components/ExtendedQuote';

// ── SVG Sparkline (inline, no ResizeObserver dependency) ─────────────────────
function Sparkline({ data, positive, height = 56 }) {
  if (!data || data.length < 2) return <div style={{ height }} />;
  const W = 300, H = height;
  const closes = data.map(d => d.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const pad = 4;

  const pts = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * W;
    const y = H - pad - ((c - min) / range) * (H - pad * 2);
    return [x, y];
  });

  const linePts  = pts.map(([x, y]) => `${x},${y}`).join(' ');
  const areaPts  = [
    `0,${H}`,
    ...pts.map(([x, y]) => `${x},${y}`),
    `${W},${H}`,
  ].join(' ');

  const stroke = positive ? '#10b981' : '#ef4444';
  const fill   = positive ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.12)';

  return (
    <svg
      width="100%"
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ display: 'block' }}
    >
      <polygon points={areaPts} fill={fill} />
      <polyline
        points={linePts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function Home() {
  const [market, setMarket]       = useState(null);
  const [marketLoading, setML]    = useState(true);
  const [marketError, setME]      = useState('');
  const [macro, setMacro]         = useState(null);
  const [macroLoading, setMacroL] = useState(true);

  const loadAll = (bust = false) => {
    setML(true); setME('');
    const mReq = bust
      ? axios.delete('/api/market/cache').then(() => axios.get('/api/market/overview'))
      : axios.get('/api/market/overview');
    mReq.then(r => setMarket(r.data))
        .catch(e => setME(e?.response?.data?.detail || 'Failed to load'))
        .finally(() => setML(false));

    setMacroL(true);
    const macReq = bust
      ? axios.delete('/api/macro/cache').then(() => axios.get('/api/macro/overview'))
      : axios.get('/api/macro/overview');
    macReq.then(r => setMacro(r.data))
          .catch(() => setMacro(null))
          .finally(() => setMacroL(false));
  };

  useEffect(() => { loadAll(); }, []);

  return (
    <div className="space-y-6 px-3 lg:px-6 py-6">
      <MarketOverview
        data={market} loading={marketLoading} error={marketError}
        onRefresh={() => loadAll(true)}
      />
      <MacroSection data={macro} loading={macroLoading} />
    </div>
  );
}

// ── Market Overview ───────────────────────────────────────────────────────────

const MARKET_GROUPS = [
  { label: 'Equities',             symbols: ['SPY', 'QQQ', 'IWM', '^DJI'] },
  { label: 'Risk / Vol · Crypto',  symbols: ['^VIX', 'BTC-USD', 'ETH-USD'] },
  { label: 'Macro',                symbols: ['GLD', 'TLT', 'UUP'] },
];

function MarketOverview({ data, loading, error, onRefresh }) {
  const items   = data?.items || [];
  const summary = data?.summary || {};
  const tone    = summary.tone || 'mixed';

  const itemMap = useMemo(() => {
    const m = {};
    items.forEach(i => { m[i.symbol] = i; });
    return m;
  }, [items]);

  const extended = useExtendedQuotes(useMemo(() => items.map(i => i.symbol), [items]));

  const validItems  = items.filter(i => i.ok && i.change_pct != null);
  const biggestMove = useMemo(() => {
    if (!validItems.length) return null;
    return [...validItems].sort((a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct))[0];
  }, [validItems]);

  const toneLabel = tone === 'risk_on' ? 'Risk-on ↑' : tone === 'risk_off' ? 'Risk-off ↓' : 'Mixed';
  const toneBg = tone === 'risk_on'
    ? 'bg-[rgba(5,150,105,0.1)] text-[var(--accent-success)] border-[rgba(5,150,105,0.25)]'
    : tone === 'risk_off'
      ? 'bg-[rgba(220,38,38,0.1)] text-[var(--accent-danger)] border-[rgba(220,38,38,0.25)]'
      : 'bg-[rgba(217,119,6,0.1)] text-[var(--accent-amber)] border-[rgba(217,119,6,0.25)]';

  return (
    <section className="container rounded-[28px] border border-[var(--glass-border)] bg-[linear-gradient(135deg,#f8fafc_0%,#ffffff_60%,#eef4ff_100%)] p-5 shadow-[0_18px_45px_rgba(15,23,42,0.06)] sm:p-6">
      <div className="mb-5 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-2 rounded-full border border-[rgba(0,82,255,0.18)] bg-[rgba(0,82,255,0.06)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
            <Activity size={12} />
            大盤狀態
          </div>
          <span className={`rounded-full border px-3 py-1 text-xs font-bold ${toneBg}`}>
            {toneLabel}
          </span>
          {biggestMove && (
            <span className="hidden sm:inline text-xs text-[var(--text-muted)]">
              最大波動：<span className="font-semibold text-[var(--text-primary)]">{biggestMove.label}</span>
              <span className={`ml-1 font-bold ${biggestMove.change_pct >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
                {fmtPct(biggestMove.change_pct)}
              </span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {data?.fetched_at && (
            <span className="text-[10px] text-[var(--text-muted)]">{formatFetchedAt(data.fetched_at)}</span>
          )}
          <button onClick={onRefresh} disabled={loading} className="btn btn-ghost px-3 py-1.5 text-xs">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-xl border border-[rgba(220,38,38,0.18)] bg-[rgba(220,38,38,0.06)] px-4 py-2.5 text-sm text-[var(--accent-danger)]">
          {error}
        </div>
      )}

      <div className="space-y-4">
        {MARKET_GROUPS.map(group => {
          const groupItems = group.symbols.map(s => itemMap[s]).filter(Boolean);
          if (!groupItems.length && !loading) return null;
          const cols = group.symbols.length >= 4
            ? 'grid-cols-2 sm:grid-cols-4'
            : 'grid-cols-2 sm:grid-cols-3';
          return (
            <div key={group.label}>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-2">
                {group.label}
              </p>
              <div className={`grid gap-2.5 ${cols}`}>
                {loading && !items.length
                  ? group.symbols.map(s => <MarketSkeleton key={s} />)
                  : groupItems.map(item => (
                      <MarketCard
                        key={item.symbol}
                        item={item}
                        sparkData={item.sparkline}
                        extended={extended[item.symbol]}
                      />
                    ))
                }
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MarketCard({ item, sparkData, extended }) {
  const pos      = (item.change_pct || 0) >= 0;
  const isVix    = item.symbol === '^VIX';
  const colorPos = isVix ? !pos : pos;

  return (
    <Link
      href={`/market/${encodeURIComponent(item.symbol)}`}
      className="block rounded-2xl border border-[var(--glass-border)] bg-white px-4 pt-3.5 pb-2 transition-all duration-200 hover:-translate-y-0.5 hover:border-[rgba(0,82,255,0.2)] hover:shadow-[0_12px_28px_rgba(15,23,42,0.09)]"
    >
      <div className="flex items-start justify-between gap-1 mb-2.5">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)] leading-tight">{item.label}</p>
          <p className="font-mono text-[10px] text-[var(--text-muted)] mt-0.5">{item.symbol}</p>
        </div>
        <span className={`shrink-0 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-md
          ${item.asset_type === 'Volatility' ? 'bg-[rgba(217,119,6,0.1)] text-[var(--accent-amber)]'
          : item.asset_type === 'Crypto'     ? 'bg-[rgba(139,92,246,0.1)] text-[var(--accent-purple)]'
          : item.asset_type === 'Rates'      ? 'bg-[rgba(0,212,255,0.08)] text-[var(--accent-cyan)]'
          : item.asset_type === 'Commodity'  ? 'bg-[rgba(251,191,36,0.1)] text-[var(--accent-amber)]'
          :                                    'bg-[rgba(0,82,255,0.07)] text-[var(--text-muted)]'}`}>
          {item.asset_type}
        </span>
      </div>

      {!item.ok ? (
        <p className="text-sm text-[var(--text-muted)] pb-2">—</p>
      ) : (
        <>
          <p className="text-2xl font-bold font-mono tabular-nums tracking-tight text-[var(--text-primary)]">
            {formatPrice(item)}
          </p>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <span className={`inline-flex items-center gap-0.5 text-sm font-bold
              ${colorPos ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
              {pos ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {fmtPct(item.change_pct)}
            </span>
            {item.change != null && (
              <span className={`text-xs font-mono opacity-60 ${colorPos ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
                {item.change >= 0 ? '+' : ''}{Number(item.change).toFixed(2)}
              </span>
            )}
            {item.ytd_return_pct != null && (
              <span className="ml-auto text-[10px] text-[var(--text-muted)]">
                YTD <span className={`font-semibold ${item.ytd_return_pct >= 0 ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
                  {fmtPct(item.ytd_return_pct)}
                </span>
              </span>
            )}
          </div>
          {extended && (
            <div className="mt-1.5">
              <ExtendedQuoteBadge
                quote={extended}
                invert={isVix}
                upColor="#059669"
                downColor="#dc2626"
              />
            </div>
          )}
          <div className="-mx-2 mt-2 overflow-hidden rounded-b-xl">
            <Sparkline data={sparkData} positive={colorPos} height={56} />
          </div>
        </>
      )}
    </Link>
  );
}

function MarketSkeleton() {
  return (
    <div className="h-[116px] animate-pulse rounded-2xl border border-[var(--glass-border)] bg-white p-4">
      <div className="h-3 w-16 rounded bg-[var(--bg-tertiary)]" />
      <div className="mt-3 h-7 w-24 rounded bg-[var(--bg-tertiary)]" />
      <div className="mt-2 h-4 w-14 rounded-full bg-[var(--bg-tertiary)]" />
    </div>
  );
}

// ── Macro Section ─────────────────────────────────────────────────────────────

function MacroSection({ data, loading }) {
  const items  = data?.items || [];
  const yc     = data?.yield_curve;
  const byType = (type) => items.filter(i => i.type === type && i.ok);

  return (
    <section className="container space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Globe2 size={14} className="text-[var(--text-muted)]" />
          <span className="text-sm font-semibold">Macro</span>
        </div>
        {yc && (
          <div className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold
            ${yc.inverted_10y_3m
              ? 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.08)] border-[rgba(248,113,113,0.25)]'
              : 'text-[var(--accent-success)] bg-[rgba(52,211,153,0.06)] border-[rgba(52,211,153,0.2)]'}`}
          >
            {yc.inverted_10y_3m ? <AlertTriangle size={11} /> : <TrendingUp size={11} />}
            Yield curve {yc.inverted_10y_3m ? 'inverted' : 'normal'}
            &nbsp;·&nbsp;10Y–3M&nbsp;
            <span className="font-mono">{yc.spread_10y_3m >= 0 ? '+' : ''}{yc.spread_10y_3m}%</span>
          </div>
        )}
      </div>

      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="h-[88px] animate-pulse rounded-xl border border-[var(--glass-border)] bg-[rgba(255,255,255,0.04)]" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {byType('yield').length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-2">
                Treasury Yields
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                {byType('yield').map(item => (
                  <MacroCard key={item.symbol} item={item} sparkData={item.sparkline} />
                ))}
              </div>
            </div>
          )}
          {([...byType('commodity'), ...byType('currency'), ...byType('volatility')].length > 0) && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-2">
                Commodities · FX · Vol
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
                {[...byType('commodity'), ...byType('currency'), ...byType('volatility')].map(item => (
                  <MacroCard key={item.symbol} item={item} sparkData={item.sparkline} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function MacroCard({ item, sparkData }) {
  const pos     = (item.change_pct || 0) >= 0;
  const isYield = item.type === 'yield';
  const isVix   = item.symbol === '^VIX';
  const showGreen = isVix ? !pos : pos;

  const priceStr = isYield
    ? `${Number(item.price).toFixed(3)}%`
    : item.unit?.startsWith('$')
      ? `$${Number(item.price).toLocaleString(undefined, {
          minimumFractionDigits: item.price > 100 ? 0 : 2,
          maximumFractionDigits: item.price > 100 ? 0 : 2,
        })}`
      : Number(item.price).toFixed(2);

  return (
    <div className="glass rounded-xl px-3.5 pt-3 pb-2">
      <p className="text-[10px] font-medium text-[var(--text-muted)] truncate">{item.label}</p>
      <p className="mt-1.5 text-lg font-bold font-mono tabular-nums leading-none">{priceStr}</p>
      <div className="mt-1 flex items-center justify-between">
        <span className={`text-xs font-semibold ${showGreen ? 'text-[var(--accent-success)]' : 'text-[var(--accent-danger)]'}`}>
          {pos ? '▲' : '▼'} {Math.abs(item.change_pct).toFixed(2)}%
        </span>
        {item.change_1m_pct != null && (
          <span className="text-[10px] text-[var(--text-muted)]">
            1M {item.change_1m_pct >= 0 ? '+' : ''}{item.change_1m_pct.toFixed(1)}%
          </span>
        )}
      </div>
      <div className="-mx-2 mt-2 overflow-hidden rounded-b-xl">
        <Sparkline data={sparkData} positive={showGreen} height={52} />
      </div>
    </div>
  );
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return 'N/A';
  const n = Number(value);
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function formatPrice(item) {
  if (item.price == null) return 'N/A';
  const digits = item.symbol?.includes('BTC') || item.symbol?.includes('ETH') ? 0 : 2;
  const prefix = item.asset_type === 'Index' || item.asset_type === 'Volatility' ? '' : '$';
  return `${prefix}${Number(item.price).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function formatFetchedAt(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
