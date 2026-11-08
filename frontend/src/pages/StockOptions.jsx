import { useState, useEffect } from 'react';
import { useRoute } from 'wouter';
import axios from '../lib/apiClient';
import { Loader2, TrendingUp, TrendingDown } from 'lucide-react';
import StockPageHeader, { StockPageFallback } from '../components/StockPageHeader';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt2(v) {
  if (v == null) return '—';
  return Number(v).toFixed(2);
}

function fmtPct(v) {
  if (v == null) return '—';
  return `${Number(v).toFixed(1)}%`;
}

function fmtK(v) {
  if (v == null) return '—';
  const n = Number(v);
  return n >= 1000 ? `${(n / 1000).toFixed(0)}K` : String(n);
}

function ivColor(iv) {
  if (iv == null) return 'text-[var(--text-primary)]';
  if (iv > 100) return 'text-[var(--accent-danger)]';
  if (iv > 60)  return 'text-[var(--accent-amber)]';
  return 'text-[var(--accent-success)]';
}

// ── IV Surface table ──────────────────────────────────────────────────────────

function IVSurface({ surface, onSelectExpiry, selectedExpiry }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--glass-border)]">
            {['Expiry', 'Call IV', 'Put IV', 'Call OI', 'Put OI', 'C Vol', 'P Vol'].map(h => (
              <th key={h} className="px-3 py-2.5 text-right first:text-left font-medium text-[var(--text-muted)] whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {surface.map((row, i) => (
            <tr
              key={row.expiry}
              onClick={() => onSelectExpiry(row.expiry)}
              className={`border-b border-[var(--glass-border)] cursor-pointer transition-colors
                ${selectedExpiry === row.expiry
                  ? 'bg-[rgba(139,92,246,0.12)]'
                  : i % 2 !== 0
                    ? 'bg-[rgba(255,255,255,0.01)] hover:bg-[rgba(255,255,255,0.04)]'
                    : 'hover:bg-[rgba(255,255,255,0.04)]'
                }`}
            >
              <td className="px-3 py-2.5 font-mono text-xs font-semibold text-[var(--accent-cyan)]">
                {row.expiry}
              </td>
              <td className={`px-3 py-2.5 text-right font-mono text-xs font-bold ${ivColor(row.calls_avg_iv)}`}>
                {fmtPct(row.calls_avg_iv)}
              </td>
              <td className={`px-3 py-2.5 text-right font-mono text-xs font-bold ${ivColor(row.puts_avg_iv)}`}>
                {fmtPct(row.puts_avg_iv)}
              </td>
              <td className="px-3 py-2.5 text-right text-xs text-[var(--text-secondary)]">{fmtK(row.calls_oi)}</td>
              <td className="px-3 py-2.5 text-right text-xs text-[var(--text-secondary)]">{fmtK(row.puts_oi)}</td>
              <td className="px-3 py-2.5 text-right text-xs text-[var(--text-muted)]">{fmtK(row.calls_volume)}</td>
              <td className="px-3 py-2.5 text-right text-xs text-[var(--text-muted)]">{fmtK(row.puts_volume)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Options chain table ───────────────────────────────────────────────────────

function ChainTable({ chain, side }) {
  const rows = chain?.[side] || [];
  const isCall = side === 'calls';

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--glass-border)]">
            {['Strike', 'Last', 'Bid', 'Ask', 'IV', 'Vol', 'OI', 'ITM'].map(h => (
              <th key={h} className="px-2.5 py-2 text-right first:text-left font-medium text-[var(--text-muted)] whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const itm = row.in_the_money;
            return (
              <tr
                key={i}
                className={`border-b border-[var(--glass-border)] transition-colors
                  ${itm ? 'bg-[rgba(139,92,246,0.06)]' : ''}
                  hover:bg-[rgba(255,255,255,0.03)]`}
              >
                <td className={`px-2.5 py-1.5 font-mono font-bold
                  ${itm ? 'text-[var(--accent-purple)]' : 'text-[var(--text-secondary)]'}`}>
                  {fmt2(row.strike)}
                </td>
                <td className="px-2.5 py-1.5 text-right font-mono">{fmt2(row.last_price)}</td>
                <td className="px-2.5 py-1.5 text-right font-mono text-[var(--text-muted)]">{fmt2(row.bid)}</td>
                <td className="px-2.5 py-1.5 text-right font-mono text-[var(--text-muted)]">{fmt2(row.ask)}</td>
                <td className={`px-2.5 py-1.5 text-right font-mono font-semibold ${ivColor(row.iv_pct)}`}>
                  {fmtPct(row.iv_pct)}
                </td>
                <td className="px-2.5 py-1.5 text-right text-[var(--text-secondary)]">{fmtK(row.volume)}</td>
                <td className="px-2.5 py-1.5 text-right text-[var(--text-secondary)]">{fmtK(row.open_interest)}</td>
                <td className="px-2.5 py-1.5 text-right">
                  {itm
                    ? <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full
                        ${isCall ? 'text-[var(--accent-success)] bg-[rgba(52,211,153,0.1)]'
                                 : 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]'}`}>
                        ITM
                      </span>
                    : <span className="text-[var(--text-muted)] text-[10px]">—</span>
                  }
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function StockOptions() {
  const [, params] = useRoute('/stock/:ticker/options');
  const ticker = params?.ticker?.toUpperCase() || '';

  const [summary, setSummary]         = useState(null);
  const [chain, setChain]             = useState(null);
  const [selectedExpiry, setExpiry]   = useState(null);
  const [chainSide, setChainSide]     = useState('calls');
  const [loading, setLoading]         = useState(true);
  const [chainLoading, setChainLoading] = useState(false);

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    axios.get(`/api/stocks/${ticker}/options`)
      .then(r => {
        setSummary(r.data);
        if (r.data.iv_surface?.length > 0) {
          setExpiry(r.data.iv_surface[0].expiry);
        }
      })
      .catch(() => setSummary(null))
      .finally(() => setLoading(false));
  }, [ticker]);

  useEffect(() => {
    if (!ticker || !selectedExpiry) return;
    setChainLoading(true);
    axios.get(`/api/stocks/${ticker}/options/${selectedExpiry}`)
      .then(r => setChain(r.data))
      .catch(() => setChain(null))
      .finally(() => setChainLoading(false));
  }, [ticker, selectedExpiry]);

  const pcColor = summary?.put_call_oi_ratio != null
    ? summary.put_call_oi_ratio > 1 ? 'text-[var(--accent-danger)]' : 'text-[var(--accent-success)]'
    : '';

  return (
    <div className="container py-10 space-y-6 animate-fade-in">

      <StockPageHeader ticker={ticker} meta="IV 曲面 · 選擇權鏈資料來自 yfinance" />

      {loading && (
        <div className="glass rounded-xl p-16 flex items-center justify-center gap-3 text-[var(--text-muted)]">
          <Loader2 size={20} className="animate-spin text-[var(--accent-purple)]" />
          <span className="text-sm">Loading options data…</span>
        </div>
      )}

      {!loading && !summary && (
        <div className="glass rounded-xl p-12 text-center text-[var(--text-muted)] text-sm">
          No options data available for {ticker}
        </div>
      )}

      {!loading && summary && (
        <>
          {/* Key stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="glass rounded-xl p-4">
              <p className="text-xs text-[var(--text-muted)] mb-1">Current Price</p>
              <p className="text-xl font-bold font-mono">${fmt2(summary.current_price)}</p>
            </div>
            <div className="glass rounded-xl p-4">
              <p className="text-xs text-[var(--text-muted)] mb-1">ATM IV (1st Expiry)</p>
              <p className={`text-xl font-bold font-mono ${ivColor(summary.atm_iv_pct)}`}>
                {fmtPct(summary.atm_iv_pct)}
              </p>
            </div>
            <div className="glass rounded-xl p-4">
              <p className="text-xs text-[var(--text-muted)] mb-1">Put/Call OI Ratio</p>
              <p className={`text-xl font-bold font-mono ${pcColor}`}>
                {summary.put_call_oi_ratio?.toFixed(3) ?? '—'}
              </p>
            </div>
            <div className="glass rounded-xl p-4">
              <p className="text-xs text-[var(--text-muted)] mb-1">Expirations</p>
              <p className="text-xl font-bold font-mono">{summary.expirations?.length ?? '—'}</p>
            </div>
          </div>

          {/* IV Surface */}
          <div className="glass rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-[var(--glass-border)]">
              <h2 className="text-sm font-semibold">IV Surface — click a row to load full chain</h2>
              <p className="text-xs text-[var(--text-muted)] mt-0.5">First 6 expirations · highlighted = selected</p>
            </div>
            <IVSurface
              surface={summary.iv_surface}
              onSelectExpiry={setExpiry}
              selectedExpiry={selectedExpiry}
            />
          </div>

          {/* Chain */}
          {selectedExpiry && (
            <div className="glass rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-[var(--glass-border)] flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">
                    Options Chain — {selectedExpiry}
                    {chain?.max_pain && (
                      <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                        Max Pain: <span className="font-semibold text-[var(--accent-amber)]">${fmt2(chain.max_pain)}</span>
                      </span>
                    )}
                  </h2>
                </div>
                {/* Side toggle */}
                <div className="flex items-center border border-[var(--glass-border)] rounded-lg overflow-hidden text-xs">
                  {['calls', 'puts'].map(s => (
                    <button
                      key={s}
                      onClick={() => setChainSide(s)}
                      className={`px-3 py-1.5 capitalize transition-colors
                        ${chainSide === s
                          ? s === 'calls'
                            ? 'bg-[rgba(52,211,153,0.15)] text-[var(--accent-success)]'
                            : 'bg-[rgba(248,113,113,0.15)] text-[var(--accent-danger)]'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'
                        }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>

              {chainLoading ? (
                <div className="py-10 flex items-center justify-center gap-2 text-[var(--text-muted)]">
                  <Loader2 size={16} className="animate-spin" />
                  <span className="text-sm">Loading chain…</span>
                </div>
              ) : (
                <ChainTable chain={chain} side={chainSide} />
              )}
            </div>
          )}

          <p className="text-[10px] text-[var(--text-muted)] text-right">
            Options data via yfinance · cached 15 min · {summary.fetched_at?.slice(0, 19).replace('T', ' ')} UTC
          </p>
        </>
      )}
    </div>
  );
}
