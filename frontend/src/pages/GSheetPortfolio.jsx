import { useState, useEffect } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import {
  RefreshCw, TrendingUp, TrendingDown, DollarSign,
  BarChart2, ExternalLink, Layers,
} from 'lucide-react';

const PIE_COLORS = [
  '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b',
  '#ef4444', '#6366f1', '#ec4899', '#84cc16',
  '#f97316', '#a855f7',
];

const ACCOUNTS = ['All', 'eToro', 'Firstrade'];

function fmt(n, dec = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtPct(n) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${fmt(n)}%`;
}

function StatusBadge({ status }) {
  if (!status) return <span className="text-[var(--text-muted)]">—</span>;
  const map = {
    Undervalued:      'text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)]',
    Overvalued:       'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
    'Fairly Valued':  'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  };
  const cls = map[status] || 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)]';
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${cls}`}>{status}</span>;
}

export default function GSheetPortfolio() {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [acct,    setAcct]    = useState('All');
  const [error,   setError]   = useState(null);

  const load = () => {
    setLoading(true);
    setError(null);
    axios.get('/api/portfolio/gsheet')
      .then(r => setData(r.data))
      .catch(e => setError(e?.response?.data?.detail || 'Failed to load sheet'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-3">
        <RefreshCw size={22} className="animate-spin text-[var(--accent-purple)]" />
        <p className="text-xs text-[var(--text-muted)]">Loading from Google Sheet…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container py-20 text-center space-y-3">
        <p className="text-[var(--accent-danger)]">{error}</p>
        <button onClick={load} className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90">
          Retry
        </button>
      </div>
    );
  }

  const { total_value, overall_return, sectors, holdings } = data;

  // filter by account
  const filtered = acct === 'All'
    ? holdings
    : holdings.filter(h => h.account === acct);

  // aggregate summary for filtered view
  const filteredValue = filtered.reduce((s, h) => s + (h.value || 0), 0);

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Layers size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">My Portfolio</h1>
            <p className="text-xs text-[var(--text-muted)]">Synced from Google Sheet · live valuation overlay</p>
          </div>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <SummaryCard
          icon={<DollarSign size={15} />}
          label="Total Value"
          value={`$${fmt(total_value)}`}
          color="cyan"
        />
        <SummaryCard
          icon={overall_return != null && overall_return >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
          label="Overall Return"
          value={fmtPct(overall_return)}
          color={overall_return == null || overall_return >= 0 ? 'green' : 'red'}
        />
        <SummaryCard
          icon={<BarChart2 size={15} />}
          label="Positions"
          value={holdings.filter(h => h.ticker !== 'eToro').length}
          color="purple"
        />
      </div>

      {/* Sector allocation */}
      {sectors.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-4">Sector Allocation</h3>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={sectors}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={2}
                >
                  {sectors.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(v) => `$${fmt(v)}`}
                  contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 8, fontSize: 12 }}
                />
                <Legend
                  formatter={(v) => <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{v}</span>}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Account filter */}
      <div className="flex items-center gap-2">
        {ACCOUNTS.map(a => (
          <button
            key={a}
            onClick={() => setAcct(a)}
            className={`px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
              acct === a
                ? 'bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.04)]'
            }`}
          >
            {a}
            {a !== 'All' && (
              <span className="ml-1.5 text-[10px] opacity-60">
                {holdings.filter(h => h.account === a && h.ticker !== 'eToro').length}
              </span>
            )}
          </button>
        ))}
        {acct !== 'All' && (
          <span className="ml-auto text-xs text-[var(--text-muted)]">
            ${fmt(filteredValue)} of ${fmt(total_value)}
          </span>
        )}
      </div>

      {/* Holdings table */}
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--glass-border)]">
              {['Account', 'Ticker', 'Company', 'Shares', 'Price', 'Value', 'Weight', 'P&L', 'Fair Value (PE)', 'Valuation'].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-[var(--text-muted)] whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((h, i) => (
              <tr key={i} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors">
                <td className="px-4 py-3 text-xs text-[var(--text-muted)]">{h.account}</td>
                <td className="px-4 py-3">
                  {h.ticker === 'eToro' ? (
                    <span className="font-bold text-[var(--text-secondary)]">eToro</span>
                  ) : (
                    <Link href={`/stock/${h.ticker}`}>
                      <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer flex items-center gap-1">
                        {h.ticker}
                        <ExternalLink size={10} className="opacity-50" />
                      </span>
                    </Link>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-[var(--text-secondary)] max-w-[140px] truncate">
                  {h.company_name || h.ticker}
                </td>
                <td className="px-4 py-3 text-[var(--text-secondary)]">
                  {h.shares != null ? fmt(h.shares, 4).replace(/\.?0+$/, '') : '—'}
                </td>
                <td className="px-4 py-3 font-medium">
                  {h.price != null ? `$${fmt(h.price)}` : <span className="text-[var(--text-muted)]">—</span>}
                </td>
                <td className="px-4 py-3 font-medium">${fmt(h.value)}</td>
                <td className="px-4 py-3 text-[var(--text-secondary)]">
                  {h.weight != null ? `${fmt(h.weight)}%` : '—'}
                </td>
                <td className={`px-4 py-3 font-medium ${h.pnl_pct == null ? '' : h.pnl_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                  {fmtPct(h.pnl_pct)}
                </td>
                <td className="px-4 py-3">
                  {h.pe_median_price ? (
                    <div className="flex flex-col">
                      <span className="text-xs font-medium">${fmt(h.pe_median_price)}</span>
                      {h.price != null && (
                        <span className={`text-[10px] ${h.pe_median_price >= h.price ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                          {h.pe_median_price >= h.price ? '+' : ''}{(((h.pe_median_price - h.price) / h.price) * 100).toFixed(1)}%
                        </span>
                      )}
                    </div>
                  ) : <span className="text-[var(--text-muted)]">—</span>}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={h.valuation_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SummaryCard({ icon, label, value, color }) {
  const colorMap = {
    cyan:   'from-[var(--accent-cyan)] to-[#0891b2]',
    purple: 'from-[var(--accent-purple)] to-[#7c3aed]',
    green:  'from-[var(--accent-green)] to-[#059669]',
    red:    'from-[var(--accent-danger)] to-[#dc2626]',
  };
  const textColorMap = {
    cyan:   'text-[var(--accent-cyan)]',
    purple: 'text-[var(--accent-purple)]',
    green:  'text-[var(--accent-green)]',
    red:    'text-[var(--accent-danger)]',
  };
  return (
    <div className="card p-4 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${colorMap[color]} flex items-center justify-center shrink-0 shadow-md`}>
        <span className="text-white">{icon}</span>
      </div>
      <div>
        <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">{label}</p>
        <p className={`text-lg font-extrabold ${textColorMap[color]}`}>{value}</p>
      </div>
    </div>
  );
}
