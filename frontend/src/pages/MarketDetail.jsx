import { useEffect, useMemo, useState } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import { ArrowLeft, Activity, TrendingDown, TrendingUp } from 'lucide-react';
import PriceChartPanel from '../components/PriceChartPanel';
import { useExtendedQuotes } from '../components/useExtendedQuotes';
import ExtendedQuoteBadge from '../components/ExtendedQuote';

export default function MarketDetail() {
  const [, params] = useRoute('/market/:symbol');
  const symbol = decodeURIComponent(params?.symbol || '').toUpperCase();
  const [item, setItem] = useState(null);
  const [loading, setLoading] = useState(true);
  const extended = useExtendedQuotes(symbol ? [symbol] : []);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    axios.get('/api/market/overview', { params: { symbols: symbol } })
      .then(res => setItem((res.data.items || [])[0] || null))
      .catch(() => setItem(null))
      .finally(() => setLoading(false));
  }, [symbol]);

  const positive = (item?.change_pct || 0) >= 0;
  const title = item?.label || symbol;
  const subtitle = useMemo(() => {
    const type = item?.asset_type || 'Market';
    const source = item?.source ? ` · ${item.source}` : '';
    return `${type}${source}`;
  }, [item]);

  return (
    <div className="container py-10 space-y-7 animate-fade-in">
      <Link href="/" className="inline-flex items-center gap-2 text-sm text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]">
        <ArrowLeft size={14} /> Back to Home
      </Link>

      <section className="overflow-hidden rounded-[30px] border border-[var(--glass-border)] bg-white">
        <div className="relative border-b border-[var(--glass-border)] bg-[linear-gradient(135deg,#0a0b0d_0%,#111827_58%,#173b7a_100%)] p-6 text-white sm:p-8">
          <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-[rgba(87,139,250,0.22)] blur-3xl" />
          <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-[rgba(255,255,255,0.14)] bg-[rgba(255,255,255,0.08)] px-3 py-1 text-xs font-semibold text-[#cbd5e1]">
                <Activity size={13} />
                Market instrument
              </div>
              <h1 className="text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">{title}</h1>
              <p className="mt-2 font-mono text-sm text-[#aab4c5]">{symbol} · {subtitle}</p>
            </div>

            <div className="rounded-3xl border border-[rgba(255,255,255,0.12)] bg-[rgba(255,255,255,0.08)] p-4 text-left lg:min-w-[260px]">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#aab4c5]">Latest</p>
              {loading ? (
                <div className="mt-3 h-10 w-32 animate-pulse rounded bg-[rgba(255,255,255,0.12)]" />
              ) : item?.ok ? (
                <>
                  <p className="mt-2 text-4xl font-semibold tracking-[-0.04em]">{formatPrice(item)}</p>
                  <p className={`mt-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-semibold ${
                    positive
                      ? 'bg-[rgba(52,211,153,0.12)] text-[#34d399]'
                      : 'bg-[rgba(248,113,113,0.12)] text-[#f87171]'
                  }`}>
                    {positive ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                    {fmtPct(item.change_pct)}
                  </p>
                  {extended[symbol] && (
                    <div className="mt-2">
                      <ExtendedQuoteBadge quote={extended[symbol]} showPrice invert={symbol === '^VIX'} />
                    </div>
                  )}
                  {item.last_date && <p className="mt-2 text-xs text-[#8b93a3]">Last bar {item.last_date}</p>}
                </>
              ) : (
                <p className="mt-3 text-sm text-[#fca5a5]">{item?.error || 'No latest price data'}</p>
              )}
            </div>
          </div>

          {item?.ok && (
            <div className="relative mt-6 grid gap-3 sm:grid-cols-3">
              <PerformanceCard label="今年報酬率" value={item.ytd_return_pct} />
              <PerformanceCard label="1 個月報酬率" value={item.one_month_return_pct} />
              <PerformanceCard label="Max Drawdown" value={item.max_drawdown_pct} drawdown />
            </div>
          )}
        </div>

        <div className="p-4 sm:p-6">
          <PriceChartPanel ticker={symbol} />
        </div>
      </section>
    </div>
  );
}

function PerformanceCard({ label, value, drawdown = false }) {
  const num = Number(value);
  const negative = Number.isFinite(num) && num < 0;
  const positive = Number.isFinite(num) && num > 0;
  const tone = negative
    ? 'text-[#fca5a5] bg-[rgba(248,113,113,0.1)] border-[rgba(248,113,113,0.22)]'
    : positive
      ? 'text-[#6ee7b7] bg-[rgba(52,211,153,0.1)] border-[rgba(52,211,153,0.2)]'
      : 'text-[#cbd5e1] bg-[rgba(255,255,255,0.08)] border-[rgba(255,255,255,0.12)]';

  return (
    <div className={`rounded-2xl border p-4 ${tone}`}>
      <p className="text-xs font-semibold uppercase tracking-[0.12em] opacity-75">{label}</p>
      <p className="mt-2 text-3xl font-semibold tracking-[-0.04em]">{fmtPct(value)}</p>
      {drawdown && <p className="mt-1 text-xs opacity-70">近一年高點回落幅度</p>}
    </div>
  );
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return 'N/A';
  const num = Number(value);
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
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
