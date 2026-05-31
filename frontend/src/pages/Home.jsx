import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import SearchBar from '../components/SearchBar';
import { TrendingUp, TrendingDown, BarChart3, Zap, ArrowRight, Activity, RefreshCw } from 'lucide-react';
import { Link } from 'wouter';

export default function Home() {
  const [market, setMarket] = useState(null);
  const [marketLoading, setMarketLoading] = useState(true);
  const [marketError, setMarketError] = useState('');

  const loadMarket = () => {
    setMarketLoading(true);
    setMarketError('');
    axios.get('/api/market/overview')
      .then(res => setMarket(res.data))
      .catch(err => setMarketError(err?.response?.data?.detail || 'Failed to load market overview'))
      .finally(() => setMarketLoading(false));
  };

  useEffect(() => {
    loadMarket();
  }, []);

  return (
    <div className="space-y-6 lg:space-y-8 px-3 lg:px-6">
      <section className="container relative overflow-hidden rounded-[32px] border border-[var(--glass-border)] bg-white px-5 py-10 sm:px-8 lg:px-14 lg:py-14 animate-fade-in-up">
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute -right-24 top-[-120px] h-72 w-72 rounded-full bg-[rgba(0,82,255,0.08)] blur-3xl" />
          <div className="absolute left-[-100px] bottom-[-130px] h-72 w-72 rounded-full bg-[rgba(87,139,250,0.08)] blur-3xl" />
        </div>

        <div className="relative grid items-start gap-8 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="space-y-6">
            <div className="inline-flex items-center gap-2 rounded-full border border-[rgba(0,82,255,0.2)] bg-[rgba(0,82,255,0.06)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-success)] animate-pulse" />
              Tracking 150+ stocks with refreshed metrics
            </div>

            <h1 className="max-w-3xl text-[42px] font-semibold leading-[1.02] tracking-[-0.02em] text-[var(--text-primary)] sm:text-[56px] lg:text-[72px]">
              Fair Value
              <span className="text-gradient"> Decoded</span>
            </h1>

            <p className="max-w-xl text-base leading-relaxed text-[var(--text-secondary)] sm:text-lg">
              Institutional-grade valuation analysis using historical PE medians, analyst growth forecasts, and fast side-by-side comparison.
            </p>

            <div className="max-w-2xl">
              <SearchBar />
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Link href="/valuations" className="btn btn-primary rounded-[56px] text-sm px-6 py-3.5">
                Browse All Valuations <ArrowRight size={16} />
              </Link>
              <Link href="/compare" className="btn btn-ghost rounded-[56px] text-sm px-6 py-3.5">
                Compare Tickers
              </Link>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            <KpiCard label="Coverage" value="150+" detail="Actively tracked tickers" />
            <KpiCard label="Refresh" value="Daily" detail="Automatic valuation updates" />
            <KpiCard label="Signals" value="Realtime" detail="Alert and watchlist routing" />
          </div>
        </div>
      </section>

      <MarketOverview
        data={market}
        loading={marketLoading}
        error={marketError}
        onRefresh={loadMarket}
      />

      <section className="container grid gap-4 rounded-[28px] bg-[#0a0b0d] px-5 py-8 sm:px-8 lg:grid-cols-3 lg:gap-5 lg:px-10">
        <FeatureCard
          icon={<TrendingUp size={18} />}
          iconBg="bg-[rgba(5,82,255,0.18)]"
          iconColor="text-[#578bfa]"
          title="Growth Forecasts"
          desc="5-year EPS and revenue projections from analyst consensus."
        />
        <FeatureCard
          icon={<BarChart3 size={18} />}
          iconBg="bg-[rgba(87,139,250,0.18)]"
          iconColor="text-[#89acff]"
          title="Historical PE Bands"
          desc="Contextual valuation ranges anchored on 5-year medians."
        />
        <FeatureCard
          icon={<Zap size={18} />}
          iconBg="bg-[rgba(217,119,6,0.2)]"
          iconColor="text-[#fbbf24]"
          title="Execution Speed"
          desc="Data-dense views tuned for quick triage and decision loops."
        />
      </section>
    </div>
  );
}

function MarketOverview({ data, loading, error, onRefresh }) {
  const items = data?.items || [];
  const summary = data?.summary || {};
  const validItems = items.filter(item => item.ok && item.change_pct != null);
  const biggestMove = useMemo(() => {
    if (!validItems.length) return null;
    return [...validItems].sort((a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct))[0];
  }, [validItems]);
  const tone = summary.tone || 'mixed';
  const toneLabel = tone === 'risk_on' ? 'Risk-on' : tone === 'risk_off' ? 'Risk-off' : 'Mixed tape';
  const toneClass = tone === 'risk_on'
    ? 'text-[var(--accent-success)] bg-[rgba(5,150,105,0.08)] border-[rgba(5,150,105,0.22)]'
    : tone === 'risk_off'
      ? 'text-[var(--accent-danger)] bg-[rgba(220,38,38,0.08)] border-[rgba(220,38,38,0.2)]'
      : 'text-[var(--accent-amber)] bg-[rgba(217,119,6,0.08)] border-[rgba(217,119,6,0.2)]';

  return (
    <section className="container rounded-[30px] border border-[var(--glass-border)] bg-[linear-gradient(135deg,#f8fafc_0%,#ffffff_42%,#eef4ff_100%)] p-5 shadow-[0_18px_45px_rgba(15,23,42,0.06)] sm:p-7 animate-fade-in-up">
      <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-[rgba(0,82,255,0.18)] bg-[rgba(0,82,255,0.06)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
            <Activity size={13} />
            Market pulse
          </div>
          <h2 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--text-primary)] sm:text-3xl">
            大盤狀態
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--text-secondary)]">
            快速看 SPY、QQQ、IWM、DJI、VIX、BTC、ETH、黃金、長債與美元，判斷今天是風險偏好還是避險盤。
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold ${toneClass}`}>
            {tone === 'risk_off' ? <TrendingDown size={14} /> : <TrendingUp size={14} />}
            {toneLabel}
          </span>
          <button
            type="button"
            onClick={onRefresh}
            className="btn btn-ghost px-3 py-1.5 text-xs"
            disabled={loading}
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-2xl border border-[rgba(220,38,38,0.18)] bg-[rgba(220,38,38,0.06)] px-4 py-3 text-sm text-[var(--accent-danger)]">
          {error}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-[1.05fr_2fr]">
        <article className="rounded-3xl bg-[#0a0b0d] p-5 text-white">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8b93a3]">Session read</p>
          <p className="mt-3 text-4xl font-semibold tracking-[-0.04em]">
            {loading ? 'Loading' : toneLabel}
          </p>
          <p className="mt-3 text-sm leading-relaxed text-[#b9bec8]">
            {biggestMove
              ? `最大波動是 ${biggestMove.label}，今日 ${fmtPct(biggestMove.change_pct)}。`
              : '等待市場資料回傳。'}
          </p>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <MiniStat label="上漲" value={summary.positive_count ?? 0} />
            <MiniStat label="下跌" value={summary.negative_count ?? 0} />
          </div>
          {data?.fetched_at && (
            <p className="mt-4 text-xs text-[#8b93a3]">Updated {formatFetchedAt(data.fetched_at)}</p>
          )}
        </article>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {loading && !items.length
            ? Array.from({ length: 10 }).map((_, idx) => <MarketSkeleton key={idx} />)
            : items.map(item => <MarketCard key={item.symbol} item={item} />)}
        </div>
      </div>
    </section>
  );
}

function MarketCard({ item }) {
  const positive = (item.change_pct || 0) >= 0;
  const unavailable = !item.ok;
  const href = `/market/${encodeURIComponent(item.symbol)}`;
  return (
    <Link href={href} className="block rounded-2xl border border-[var(--glass-border)] bg-white p-4 transition duration-200 hover:-translate-y-0.5 hover:border-[var(--glass-border-hover)] hover:shadow-[0_14px_30px_rgba(15,23,42,0.08)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">{item.label}</p>
          <p className="mt-0.5 font-mono text-xs text-[var(--text-muted)]">{item.symbol}</p>
        </div>
        <span className="rounded-full bg-[var(--bg-secondary)] px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
          {item.asset_type}
        </span>
      </div>

      {unavailable ? (
        <div className="mt-5">
          <p className="text-sm font-semibold text-[var(--text-muted)]">No data</p>
          <p className="mt-1 line-clamp-2 text-xs text-[var(--text-muted)]">{item.error || 'Unavailable'}</p>
        </div>
      ) : (
        <>
          <p className="mt-5 text-2xl font-semibold tracking-[-0.03em] text-[var(--text-primary)]">
            {formatPrice(item)}
          </p>
          <div className={`mt-2 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${
            positive
              ? 'bg-[rgba(5,150,105,0.08)] text-[var(--accent-success)]'
              : 'bg-[rgba(220,38,38,0.08)] text-[var(--accent-danger)]'
          }`}>
            {positive ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
            {fmtPct(item.change_pct)}
          </div>
          <p className="mt-3 text-xs text-[var(--text-muted)]">
            Prev {item.previous_close != null ? formatPrice({ ...item, price: item.previous_close }) : 'N/A'}
          </p>
          <div className="mt-4 grid grid-cols-3 gap-1.5">
            <MarketMetric label="YTD" value={item.ytd_return_pct} />
            <MarketMetric label="1M" value={item.one_month_return_pct} />
            <MarketMetric label="MDD" value={item.max_drawdown_pct} />
          </div>
          <p className="mt-3 text-xs font-semibold text-[var(--accent-cyan)]">View chart →</p>
        </>
      )}
    </Link>
  );
}

function MarketMetric({ label, value }) {
  const negative = Number(value) < 0;
  const positive = Number(value) > 0;
  return (
    <div className="rounded-xl bg-[var(--bg-secondary)] px-2 py-1.5">
      <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">{label}</p>
      <p className={`mt-0.5 text-xs font-semibold ${
        negative ? 'text-[var(--accent-danger)]' : positive ? 'text-[var(--accent-success)]' : 'text-[var(--text-secondary)]'
      }`}>
        {fmtPct(value)}
      </p>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="rounded-2xl border border-[rgba(255,255,255,0.1)] bg-[rgba(255,255,255,0.05)] p-3">
      <p className="text-xs text-[#8b93a3]">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
    </div>
  );
}

function MarketSkeleton() {
  return (
    <article className="h-[156px] animate-pulse rounded-2xl border border-[var(--glass-border)] bg-white p-4">
      <div className="h-4 w-20 rounded bg-[var(--bg-tertiary)]" />
      <div className="mt-2 h-3 w-12 rounded bg-[var(--bg-tertiary)]" />
      <div className="mt-8 h-7 w-24 rounded bg-[var(--bg-tertiary)]" />
      <div className="mt-3 h-6 w-16 rounded-full bg-[var(--bg-tertiary)]" />
    </article>
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

function formatFetchedAt(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function FeatureCard({ icon, iconBg, iconColor, title, desc }) {
  return (
    <article className="rounded-2xl border border-[rgba(255,255,255,0.12)] bg-[rgba(255,255,255,0.03)] p-5 transition-colors duration-200 hover:bg-[rgba(255,255,255,0.06)]">
      <div className={`mb-4 flex h-10 w-10 items-center justify-center rounded-xl ${iconBg} ${iconColor}`}>
        {icon}
      </div>
      <h3 className="mb-2 text-[15px] font-semibold text-white">{title}</h3>
      <p className="text-sm leading-relaxed text-[#b9bec8]">{desc}</p>
    </article>
  );
}

function KpiCard({ label, value, detail }) {
  return (
    <article className="rounded-2xl border border-[var(--glass-border)] bg-[var(--bg-secondary)] p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">{label}</p>
      <p className="mt-1 text-3xl font-semibold leading-none tracking-tight text-[var(--text-primary)]">{value}</p>
      <p className="mt-2 text-xs text-[var(--text-secondary)]">{detail}</p>
    </article>
  );
}
