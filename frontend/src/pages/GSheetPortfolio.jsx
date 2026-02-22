import { useState, useEffect, useRef } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import {
  RefreshCw, TrendingUp, TrendingDown, DollarSign,
  BarChart2, ExternalLink, Layers, Plus, X, Trash2,
} from 'lucide-react';

const PIE_COLORS = [
  '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b',
  '#ef4444', '#6366f1', '#ec4899', '#84cc16',
  '#f97316', '#a855f7',
];

const ACCOUNTS = ['All', 'eToro', 'Firstrade'];

const DEFAULT_PORTFOLIOS = [
  { key: 'mine', label: 'My Portfolio', param: null, gsheetId: null, custom: false },
];

const STORAGE_KEY = 'gsheet_custom_groups';

function loadCustomGroups() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); }
  catch { return []; }
}

function saveCustomGroups(groups) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(groups));
}

function parseGSheetId(input) {
  const m = input.match(/spreadsheets\/d\/([a-zA-Z0-9_-]+)/);
  return m ? m[1] : input.trim();
}

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
  const [customGroups, setCustomGroups] = useState(() => loadCustomGroups());
  const [data,         setData]         = useState(null);
  const [loading,      setLoading]      = useState(true);
  const [acct,         setAcct]         = useState('All');
  const [error,        setError]        = useState(null);
  const [portfolio,    setPortfolio]    = useState(DEFAULT_PORTFOLIOS[0]);
  const [showModal,    setShowModal]    = useState(false);
  const [newName,      setNewName]      = useState('');
  const [newUrl,       setNewUrl]       = useState('');
  const [urlError,     setUrlError]     = useState('');
  const nameRef = useRef(null);

  const allPortfolios = [...DEFAULT_PORTFOLIOS, ...customGroups];

  const load = (p) => {
    setLoading(true);
    setError(null);
    let url = '/api/portfolio/gsheet';
    if (p.gsheetId) {
      url += `?gsheet_id=${encodeURIComponent(p.gsheetId)}`;
    } else if (p.param) {
      url += `?portfolio=${p.param}`;
    }
    axios.get(url)
      .then(r => setData(r.data))
      .catch(e => setError(e?.response?.data?.detail || 'Failed to load sheet'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(portfolio); }, [portfolio]);

  useEffect(() => {
    if (showModal) setTimeout(() => nameRef.current?.focus(), 50);
  }, [showModal]);

  const openModal = () => {
    setNewName(''); setNewUrl(''); setUrlError(''); setShowModal(true);
  };

  const addGroup = () => {
    const name = newName.trim();
    const raw  = newUrl.trim();
    if (!name) { setUrlError('請輸入分組名稱'); return; }
    if (!raw)  { setUrlError('請輸入 Google Sheet 網址或 ID'); return; }
    const id = parseGSheetId(raw);
    if (!id)   { setUrlError('無法解析 Google Sheet ID'); return; }

    const key   = `custom_${Date.now()}`;
    const group = { key, label: name, param: null, gsheetId: id, custom: true };
    const next  = [...customGroups, group];
    setCustomGroups(next);
    saveCustomGroups(next);
    setShowModal(false);
    setPortfolio(group);
    setAcct('All');
  };

  const removeGroup = (e, key) => {
    e.stopPropagation();
    const next = customGroups.filter(g => g.key !== key);
    setCustomGroups(next);
    saveCustomGroups(next);
    if (portfolio.key === key) {
      setPortfolio(DEFAULT_PORTFOLIOS[0]);
      setAcct('All');
    }
  };

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
        <button onClick={() => load(portfolio)} className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90">
          Retry
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { total_value, overall_return, sectors, holdings } = data;

  const filtered = acct === 'All'
    ? holdings
    : holdings.filter(h => h.account === acct);

  const filteredValue = filtered.reduce((s, h) => s + (h.value || 0), 0);

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* Add Group Modal */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setShowModal(false)}
        >
          <div
            className="w-full max-w-sm mx-4 rounded-2xl bg-[var(--bg-secondary)] border border-[var(--glass-border)] shadow-2xl p-6 space-y-4"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-base font-bold">新增分組</h3>
              <button onClick={() => setShowModal(false)} className="p-1 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all">
                <X size={16} />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs text-[var(--text-muted)] mb-1 block">分組名稱 *</label>
                <input
                  ref={nameRef}
                  type="text"
                  placeholder="例如：弟弟的投資組合"
                  value={newName}
                  onChange={e => { setNewName(e.target.value); setUrlError(''); }}
                  onKeyDown={e => e.key === 'Enter' && addGroup()}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-sm outline-none focus:border-[var(--accent-purple)] transition-colors"
                />
              </div>
              <div>
                <label className="text-xs text-[var(--text-muted)] mb-1 block">Google Sheet 網址或 ID *</label>
                <input
                  type="text"
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  value={newUrl}
                  onChange={e => { setNewUrl(e.target.value); setUrlError(''); }}
                  onKeyDown={e => e.key === 'Enter' && addGroup()}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-sm outline-none focus:border-[var(--accent-purple)] transition-colors font-mono text-xs"
                />
                {urlError && <p className="mt-1 text-[10px] text-[var(--accent-danger)]">{urlError}</p>}
                <p className="mt-1 text-[10px] text-[var(--text-muted)]">請確保 Google Sheet 已設為「任何人都可以檢視」</p>
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <button
                onClick={addGroup}
                disabled={!newName.trim() || !newUrl.trim()}
                className="flex-1 py-2 rounded-lg text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90 disabled:opacity-40 transition-all"
              >
                新增
              </button>
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 rounded-lg text-sm font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Layers size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">{portfolio.label}</h1>
            <p className="text-xs text-[var(--text-muted)]">Synced from Google Sheet · live valuation overlay</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Portfolio switcher */}
          <div className="flex items-center gap-1 p-1 rounded-xl bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
            {allPortfolios.map(p => (
              <div key={p.key} className="relative group/tab">
                <button
                  onClick={() => { setPortfolio(p); setAcct('All'); }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                    portfolio.key === p.key
                      ? 'bg-[rgba(139,92,246,0.2)] text-[var(--accent-purple)]'
                      : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                  } ${p.custom ? 'pr-6' : ''}`}
                >
                  {p.label}
                </button>
                {p.custom && (
                  <button
                    onClick={e => removeGroup(e, p.key)}
                    title="刪除此分組"
                    className="absolute right-1 top-1/2 -translate-y-1/2 p-0.5 rounded text-[var(--text-muted)] hover:text-[var(--accent-danger)] opacity-0 group-hover/tab:opacity-100 transition-all"
                  >
                    <Trash2 size={10} />
                  </button>
                )}
              </div>
            ))}
            {/* Add group button */}
            <button
              onClick={openModal}
              title="新增分組"
              className="px-2 py-1.5 rounded-lg text-xs text-[var(--text-muted)] hover:text-[var(--accent-purple)] hover:bg-[rgba(139,92,246,0.1)] transition-all"
            >
              <Plus size={13} />
            </button>
          </div>
          <button
            onClick={() => load(portfolio)}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
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
