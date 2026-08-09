import { useState, useEffect } from 'react';
import { useRoute, Link } from 'wouter';
import axios from 'axios';
import { ArrowLeft, Loader2, TrendingUp, TrendingDown } from 'lucide-react';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt1(v) {
  if (v == null) return '—';
  return Number(v).toFixed(1);
}
function fmtPct(v) {
  if (v == null) return '—';
  return `${Number(v).toFixed(1)}%`;
}
function fmtBn(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}

function ValBadge({ status }) {
  if (!status) return <span className="text-[var(--text-muted)]">—</span>;
  const up = status.toLowerCase().includes('under');
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full
      ${up
        ? 'text-[var(--accent-success)] bg-[rgba(52,211,153,0.1)]'
        : 'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]'}`}>
      {up ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
      {status}
    </span>
  );
}

const COLS = [
  { key: 'ticker',        label: 'Ticker',      fmt: 'ticker' },
  { key: 'current_price', label: 'Price',       fmt: (v) => v == null ? '—' : `$${Number(v).toFixed(2)}` },
  { key: 'market_cap',    label: 'Mkt Cap',     fmt: fmtBn },
  { key: 'pe',            label: 'P/E',         fmt: fmt1 },
  { key: 'pe_forward',    label: 'Fwd P/E',     fmt: fmt1 },
  { key: 'ev_ebitda',     label: 'EV/EBITDA',   fmt: fmt1 },
  { key: 'ps',            label: 'P/S',         fmt: fmt1 },
  { key: 'gross_margin',  label: 'Gross Mgn',   fmt: fmtPct },
  { key: 'profit_margin', label: 'Net Mgn',     fmt: fmtPct },
  { key: 'revenue_5y',    label: 'Rev 5Y',      fmt: (v) => v == null ? '—' : `${Number(v).toFixed(1)}%` },
];

export default function StockPeers() {
  const [, params] = useRoute('/stock/:ticker/peers');
  const ticker = params?.ticker?.toUpperCase() || '';

  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState('market_cap');
  const [sortDir, setSortDir] = useState('desc');

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    axios.get(`/api/compare/peers/${ticker}`)
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [ticker]);

  const sorted = (() => {
    if (!data?.peers) return [];
    const col = COLS.find(c => c.key === sortKey);
    return [...data.peers].sort((a, b) => {
      const av = a.metrics?.[sortKey] ?? a.valuation?.[sortKey];
      const bv = b.metrics?.[sortKey] ?? b.valuation?.[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return sortDir === 'asc' ? av - bv : bv - av;
    });
  })();

  const handleSort = (key) => {
    if (key === 'ticker') return;
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  return (
    <div className="container py-6 space-y-6">

      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href={`/stock/${ticker}`} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="text-xl font-bold">{ticker} · Peer Comparison</h1>
          <p className="text-sm text-[var(--text-muted)]">
            {data ? `${data.industry} · ${data.peer_count} stocks` : 'Loading…'}
          </p>
        </div>
      </div>

      {loading && (
        <div className="glass rounded-xl p-16 flex items-center justify-center gap-3 text-[var(--text-muted)]">
          <Loader2 size={20} className="animate-spin text-[var(--accent-purple)]" />
          <span className="text-sm">Loading peers…</span>
        </div>
      )}

      {!loading && !data && (
        <div className="glass rounded-xl p-12 text-center text-[var(--text-muted)] text-sm">
          {ticker} not found in database
        </div>
      )}

      {!loading && data && (
        <div className="glass rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-[var(--glass-border)]">
            <h2 className="text-sm font-semibold">{data.industry}</h2>
            <p className="text-xs text-[var(--text-muted)] mt-0.5">Click column headers to sort · highlighted row = {ticker}</p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--glass-border)]">
                  {COLS.map(col => (
                    <th
                      key={col.key}
                      onClick={() => handleSort(col.key)}
                      className={`px-3 py-2.5 font-medium text-[var(--text-muted)] whitespace-nowrap select-none
                        ${col.key !== 'ticker' ? 'text-right cursor-pointer hover:text-[var(--text-primary)] transition-colors' : 'text-left'}
                        ${sortKey === col.key ? 'text-[var(--accent-purple)]' : ''}
                      `}
                    >
                      {col.label}
                      {sortKey === col.key && (
                        <span className="ml-0.5 text-[var(--accent-purple)]">
                          {sortDir === 'desc' ? ' ↓' : ' ↑'}
                        </span>
                      )}
                    </th>
                  ))}
                  <th className="px-3 py-2.5 text-right font-medium text-[var(--text-muted)] whitespace-nowrap">
                    Valuation
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((peer, i) => {
                  const isTarget = peer.is_target;
                  const m = peer.metrics || {};
                  const v = peer.valuation || {};
                  return (
                    <tr
                      key={peer.ticker}
                      className={`border-b border-[var(--glass-border)] transition-colors
                        ${isTarget
                          ? 'bg-[rgba(139,92,246,0.1)] border-l-2 border-l-[var(--accent-purple)]'
                          : i % 2 !== 0
                            ? 'bg-[rgba(255,255,255,0.01)] hover:bg-[rgba(255,255,255,0.04)]'
                            : 'hover:bg-[rgba(255,255,255,0.04)]'
                        }`}
                    >
                      {/* Ticker */}
                      <td className="px-3 py-2.5">
                        <Link
                          href={`/stock/${peer.ticker}`}
                          className={`font-bold hover:text-[var(--accent-cyan)] transition-colors
                            ${isTarget ? 'text-[var(--accent-purple)]' : 'text-[var(--text-primary)]'}`}
                        >
                          {peer.ticker}
                        </Link>
                        {isTarget && (
                          <span className="ml-1.5 text-[10px] text-[var(--accent-purple)] font-semibold">◀</span>
                        )}
                        <div className="text-[10px] text-[var(--text-muted)] truncate max-w-[120px]">
                          {peer.company_name}
                        </div>
                      </td>

                      {/* Metric columns (skip 'ticker') */}
                      {COLS.slice(1).map(col => {
                        const raw = m[col.key];
                        return (
                          <td key={col.key} className="px-3 py-2.5 text-right font-mono tabular-nums text-xs">
                            {raw == null
                              ? <span className="text-[var(--text-muted)]">—</span>
                              : col.fmt(raw)}
                          </td>
                        );
                      })}

                      {/* Valuation status */}
                      <td className="px-3 py-2.5 text-right">
                        <ValBadge status={v.status} />
                        {v.valuation_diff_pct != null && (
                          <div className="text-[10px] text-[var(--text-muted)] mt-0.5 text-right">
                            {v.valuation_diff_pct > 0 ? '+' : ''}{Number(v.valuation_diff_pct).toFixed(1)}%
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
