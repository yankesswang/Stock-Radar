import { useState, useEffect } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import {
  RefreshCw, TrendingUp, TrendingDown, DollarSign,
  BarChart2, Layers, Trash2, Plus, CloudDownload, Check, X,
  Wallet, ChevronDown, ChevronUp,
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
  const s = status.toLowerCase();
  const map = {
    undervalued:     'text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)]',
    overvalued:      'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
    'fairly valued': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  };
  const cls = map[s] || 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)]';
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize ${cls}`}>{status}</span>;
}

const EMPTY_ADD = { account: 'Firstrade', ticker: '', shares: '', avg_cost: '', notes: '' };

export default function GSheetPortfolio() {
  const [allData,    setAllData]    = useState(null);
  const [selected,   setSelected]   = useState(null);
  const [acct,       setAcct]       = useState('All');
  const [weightDir,  setWeightDir]  = useState('desc');
  const [loading,    setLoading]    = useState(true);
  const [syncing,    setSyncing]    = useState(false);
  const [error,      setError]      = useState(null);
  // cell-level edit: { ticker, field } | null
  const [editCell,        setEditCell]        = useState(null);
  const [editVal,         setEditVal]         = useState('');
  // add row state
  const [adding,          setAdding]          = useState(false);
  const [addVals,         setAddVals]         = useState(EMPTY_ADD);
  const [addError,        setAddError]        = useState('');
  const [addSaving,       setAddSaving]       = useState(false);
  // deposit state
  const [depositsOpen,    setDepositsOpen]    = useState(false);
  const [newDeposit,      setNewDeposit]      = useState({ date: '', amount: '' });
  const [depositSaving,   setDepositSaving]   = useState(false);
  const [depositError,    setDepositError]    = useState('');

  const loadData = () => {
    setLoading(true);
    setError(null);
    axios.get('/api/portfolio/csv')
      .then(r => {
        setAllData(r.data);
        setSelected(prev =>
          r.data.portfolio_names.includes(prev) ? prev : r.data.portfolio_names[0] ?? null
        );
      })
      .catch(e => setError(e?.response?.data?.detail || 'Failed to load portfolio'))
      .finally(() => setLoading(false));
  };

  const syncFromSheet = () => {
    setSyncing(true);
    axios.post('/api/portfolio/csv/sync')
      .then(() => loadData())
      .catch(e => setError(e?.response?.data?.detail || 'Sync failed'))
      .finally(() => setSyncing(false));
  };

  useEffect(() => {
    // Load local cache immediately for fast display, then sync from sheets in background
    loadData();
    setSyncing(true);
    axios.post('/api/portfolio/csv/sync')
      .then(() => loadData())
      .catch(() => {/* silently ignore — stale cache is still usable */})
      .finally(() => setSyncing(false));
  }, []);

  // ── cell edit handlers ────────────────────────────────────────────────────
  const startCell = (ticker, account, field, currentVal) => {
    setEditCell({ ticker, account, field });
    setEditVal(String(currentVal ?? ''));
  };

  const saveCell = () => {
    const { ticker, account, field } = editCell;
    const parsed = parseFloat(editVal);
    if (isNaN(parsed)) { setEditCell(null); return; }
    setEditCell(null);
    axios.put('/api/portfolio/csv/holding', { portfolio: selected, account, ticker, [field]: parsed })
      .then(() => loadData())
      .catch(e => setError(e?.response?.data?.detail || 'Save failed'));
  };

  const cancelCell = () => setEditCell(null);

  // ── deposit handlers ──────────────────────────────────────────────────────
  const saveDeposit = () => {
    setDepositError('');
    if (!newDeposit.date)   { setDepositError('Date is required'); return; }
    if (!newDeposit.amount) { setDepositError('Amount is required'); return; }
    const amount = parseFloat(newDeposit.amount);
    if (isNaN(amount) || amount <= 0) { setDepositError('Amount must be positive'); return; }
    setDepositSaving(true);
    axios.post('/api/portfolio/csv/deposits', { portfolio: selected, date: newDeposit.date, amount })
      .then(() => { setNewDeposit({ date: '', amount: '' }); loadData(); })
      .catch(e => setDepositError(e?.response?.data?.detail || 'Failed to save'))
      .finally(() => setDepositSaving(false));
  };

  const deleteDeposit = (date, amount) => {
    axios.delete(`/api/portfolio/csv/deposits?portfolio=${encodeURIComponent(selected)}&date=${encodeURIComponent(date)}&amount=${amount}`)
      .then(() => loadData())
      .catch(e => setError(e?.response?.data?.detail || 'Failed to delete deposit'));
  };

  const deleteHolding = (ticker, account) => {
    if (!window.confirm(`Remove ${ticker} (${account}) from ${selected}?`)) return;
    axios.delete(`/api/portfolio/csv/holding?portfolio=${encodeURIComponent(selected)}&account=${encodeURIComponent(account)}&ticker=${encodeURIComponent(ticker)}`)
      .then(() => loadData())
      .catch(e => setError(e?.response?.data?.detail || 'Delete failed'));
  };

  // ── add handlers ───────────────────────────────────────────────────────────
  const saveAdd = () => {
    setAddError('');
    const ticker = addVals.ticker.toUpperCase().trim();
    if (!ticker)             { setAddError('Ticker is required'); return; }
    if (!addVals.shares)     { setAddError('Shares is required'); return; }
    if (!addVals.avg_cost)   { setAddError('Avg cost is required'); return; }
    setAddSaving(true);
    axios.post('/api/portfolio/csv/holding', {
      portfolio: selected,
      account:   addVals.account,
      ticker,
      shares:    parseFloat(addVals.shares),
      avg_cost:  parseFloat(addVals.avg_cost),
      notes:     addVals.notes,
    })
      .then(() => { setAdding(false); setAddVals(EMPTY_ADD); loadData(); })
      .catch(e => setAddError(e?.response?.data?.detail || 'Add failed'))
      .finally(() => setAddSaving(false));
  };

  // ── render states ─────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-3">
        <RefreshCw size={22} className="animate-spin text-[var(--accent-purple)]" />
        <p className="text-xs text-[var(--text-muted)]">Loading portfolio…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container py-20 text-center space-y-3">
        <p className="text-[var(--accent-danger)]">{error}</p>
        <button onClick={loadData} className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90">
          Retry
        </button>
      </div>
    );
  }

  if (!allData || !selected) return null;

  const { portfolio_names, portfolios } = allData;
  const current  = portfolios[selected];
  if (!current) return null;

  const { summary, holdings, deposits = [] } = current;
  const { total_value, total_cost, total_pnl, return_pct, mwr, deposits_incomplete, sectors, starting_capital, total_cash, cash_pct, total_value_twd, usd_twd_rate } = summary;

  const filtered = (acct === 'All'
    ? holdings
    : holdings.filter(h => h.account?.toLowerCase() === acct.toLowerCase())
  ).slice().sort((a, b) => {
    const wa = a.market_value ?? a.cost_basis ?? 0;
    const wb = b.market_value ?? b.cost_basis ?? 0;
    return weightDir === 'desc' ? wb - wa : wa - wb;
  });

  const filteredValue = filtered.reduce((s, h) => s + (h.market_value ?? h.cost_basis ?? 0), 0);

  // Per-account breakdown
  const accountBreakdown = holdings.reduce((acc, h) => {
    const name = h.account || 'Unknown';
    if (!acc[name]) acc[name] = { value: 0, cost: 0, pnl: 0 };
    acc[name].value += h.market_value ?? h.cost_basis ?? 0;
    acc[name].cost  += h.cost_basis ?? 0;
    acc[name].pnl   += h.unrealized_pnl ?? 0;
    return acc;
  }, {});

  return (
    <div className="container py-10 space-y-6 animate-fade-in">

      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Layers size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">{selected}</h1>
            <p className="text-xs text-[var(--text-muted)]">Local CSV · live price &amp; valuation overlay</p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Portfolio tabs */}
          <div className="flex items-center gap-1 p-1 rounded-xl bg-[rgba(255,255,255,0.04)] border border-[var(--glass-border)]">
            {portfolio_names.map(name => (
              <button
                key={name}
                onClick={() => { setSelected(name); setAcct('All'); setEditCell(null); setAdding(false); setDepositsOpen(false); setDepositError(''); setNewDeposit({ date: '', amount: '' }); }}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                  selected === name
                    ? 'bg-[rgba(139,92,246,0.2)] text-[var(--accent-purple)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                {name}
              </button>
            ))}
          </div>

          {/* Sync from sheet */}
          <button
            onClick={syncFromSheet}
            disabled={syncing}
            title="Re-fetch from Google Sheets"
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--accent-cyan)] hover:bg-[rgba(6,182,212,0.07)] transition-all disabled:opacity-50"
          >
            {syncing
              ? <RefreshCw size={14} className="animate-spin" />
              : <CloudDownload size={14} />}
            Sync Sheet
          </button>

          {/* Refresh prices */}
          <button
            onClick={loadData}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
        <SummaryCard
          icon={<DollarSign size={15} />}
          label="Market Value"
          value={`$${fmt(total_value)}`}
          subValue={total_value_twd ? `NT$ ${Math.round(total_value_twd).toLocaleString('zh-TW')}` : null}
          subValueColor="cyan"
          color="cyan"
        />
        <SummaryCard
          icon={<DollarSign size={15} />}
          label={starting_capital ? 'Starting Capital' : 'Cost Basis'}
          value={`$${fmt(starting_capital ?? total_cost)}`}
          color="purple"
        />
        <SummaryCard
          icon={total_pnl >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
          label={starting_capital ? 'Gain' : 'Unrealized P&L'}
          value={`${total_pnl >= 0 ? '+' : ''}$${fmt(total_pnl)}`}
          color={total_pnl >= 0 ? 'green' : 'red'}
        />
        <SummaryCard
          icon={mwr != null ? (mwr >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />) : <BarChart2 size={15} />}
          label="MWR (annualised)"
          value={mwr != null ? fmtPct(mwr) : '—'}
          subValue={
            deposits_incomplete
              ? '⚠ 存款記錄不完整'
              : mwr != null
                ? `${deposits.length} deposit${deposits.length !== 1 ? 's' : ''}`
                : deposits.length === 0
                  ? 'Add deposits ↓'
                  : null
          }
          color={deposits_incomplete ? 'yellow' : mwr == null ? 'purple' : mwr >= 0 ? 'green' : 'red'}
        />
        <SummaryCard
          icon={<DollarSign size={15} />}
          label="Cash"
          value={total_cash > 0 ? `$${fmt(total_cash)}` : '—'}
          subValue={total_cash > 0 ? `${cash_pct}% of portfolio` : null}
          subValueColor="orange"
          color="orange"
        />
      </div>

      {/* Deposits panel */}
      <div className="card overflow-hidden">
        <button
          onClick={() => setDepositsOpen(o => !o)}
          className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-[rgba(255,255,255,0.02)] transition-colors"
        >
          <div className="flex items-center gap-2.5">
            <Wallet size={15} className="text-[var(--accent-purple)]" />
            <span className="text-sm font-semibold">入金紀錄 · Deposits</span>
            {deposits.length > 0 && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)] font-semibold">
                {deposits.length} entries · ${fmt(deposits.reduce((s, d) => s + d.amount, 0))}
              </span>
            )}
          </div>
          {depositsOpen ? <ChevronUp size={14} className="text-[var(--text-muted)]" /> : <ChevronDown size={14} className="text-[var(--text-muted)]" />}
        </button>

        {depositsOpen && (
          <div className="border-t border-[var(--glass-border)] px-5 pb-5 pt-4 space-y-4">
            {/* Existing deposits */}
            {deposits.length > 0 ? (
              <div className="space-y-1.5">
                {[...deposits].sort((a, b) => a.date.localeCompare(b.date)).map((d, i) => (
                  <div key={i} className="flex items-center justify-between px-3 py-2 rounded-lg bg-[rgba(255,255,255,0.03)] group">
                    <div className="flex items-center gap-4">
                      <span className="text-xs text-[var(--text-muted)] font-mono">{d.date}</span>
                      <span className="text-sm font-semibold text-[var(--accent-cyan)]">${fmt(d.amount)}</span>
                    </div>
                    <button
                      onClick={() => deleteDeposit(d.date, d.amount)}
                      className="p-1 rounded text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.1)] transition-all opacity-0 group-hover:opacity-100"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-[var(--text-muted)] py-1">No deposits yet. Add your first deposit to calculate MWR.</p>
            )}

            {/* Add new deposit */}
            <div className="flex items-end gap-2 flex-wrap">
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Date</label>
                <input
                  type="date"
                  value={newDeposit.date}
                  onChange={e => setNewDeposit(v => ({ ...v, date: e.target.value }))}
                  className="px-3 py-1.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)] transition-colors"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Amount (USD)</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  placeholder="e.g. 5000"
                  value={newDeposit.amount}
                  onChange={e => setNewDeposit(v => ({ ...v, amount: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && saveDeposit()}
                  className="w-36 px-3 py-1.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)] transition-colors"
                />
              </div>
              <button
                onClick={saveDeposit}
                disabled={depositSaving}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-purple)] text-white text-xs font-semibold hover:opacity-90 transition-all disabled:opacity-50"
              >
                <Plus size={12} /> {depositSaving ? 'Saving…' : 'Add'}
              </button>
              {depositError && <span className="text-[11px] text-[var(--accent-danger)]">{depositError}</span>}
            </div>
          </div>
        )}
      </div>

      {/* Account breakdown */}
      {Object.keys(accountBreakdown).length > 1 && (
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Object.keys(accountBreakdown).length}, minmax(0, 1fr))` }}>
          {Object.entries(accountBreakdown).map(([name, stat]) => {
            const pct = total_value > 0 ? stat.value / total_value * 100 : 0;
            const pnlPct = stat.cost > 0 ? stat.pnl / stat.cost * 100 : 0;
            return (
              <div key={name} className="card p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-[var(--text-secondary)]">{name}</span>
                  <span className="text-[10px] text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)] px-2 py-0.5 rounded-full">{pct.toFixed(1)}% of total</span>
                </div>
                <p className="text-xl font-extrabold text-[var(--accent-cyan)]">${fmt(stat.value)}</p>
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-[var(--text-muted)]">Cost ${fmt(stat.cost)}</span>
                  <span className={stat.pnl >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}>
                    {stat.pnl >= 0 ? '+' : ''}${fmt(stat.pnl)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                  </span>
                </div>
                <div className="h-1 rounded-full bg-[rgba(255,255,255,0.06)] overflow-hidden">
                  <div className="h-full rounded-full bg-gradient-to-r from-[var(--accent-cyan)] to-[var(--accent-purple)]" style={{ width: `${Math.min(pct, 100)}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Sector allocation */}
      {sectors.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-4">Sector Allocation</h3>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={sectors}
                  dataKey="value"
                  nameKey="name"
                  cx="50%" cy="50%"
                  innerRadius={55} outerRadius={85}
                  paddingAngle={2}
                  label={({ name, value, cx, cy, midAngle, outerRadius }) => {
                    const pct = total_value > 0 ? (value / total_value * 100).toFixed(1) : 0;
                    const RADIAN = Math.PI / 180;
                    const r = outerRadius + 22;
                    const x = cx + r * Math.cos(-midAngle * RADIAN);
                    const y = cy + r * Math.sin(-midAngle * RADIAN);
                    if (pct < 3) return null;
                    return (
                      <text x={x} y={y} fill="var(--text-secondary)" textAnchor={x > cx ? 'start' : 'end'} dominantBaseline="central" fontSize={10}>
                        {pct}%
                      </text>
                    );
                  }}
                  labelLine={false}
                >
                  {sectors.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                </Pie>
                <Tooltip formatter={(v) => [`$${fmt(v)}  (${total_value > 0 ? (v / total_value * 100).toFixed(1) : 0}%)`, 'Value']} contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 8, fontSize: 12 }} />
                <Legend formatter={(v) => <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{v}</span>} />
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
                {holdings.filter(h => h.account?.toLowerCase() === a.toLowerCase()).length}
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
              {['Account','Ticker','Company','Shares','Avg Cost','Lev','Price','Market Value','Weight','P&L $','P&L %','Fair Value (PE)','Valuation',''].map(h => (
                h === 'Weight' ? (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold whitespace-nowrap">
                    <button
                      onClick={() => setWeightDir(d => d === 'desc' ? 'asc' : 'desc')}
                      className="flex items-center gap-1 text-[var(--accent-purple)] hover:opacity-80 transition-opacity"
                    >
                      Weight {weightDir === 'desc' ? '↓' : '↑'}
                    </button>
                  </th>
                ) : (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-[var(--text-muted)] whitespace-nowrap">{h}</th>
                )
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((h, i) => {
              const weight = total_value > 0 ? (h.market_value ?? h.cost_basis ?? 0) / total_value * 100 : null;
              const isCash = h.ticker === 'CASH';

              return (
                <tr key={i} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors group">
                  <td className="px-4 py-3 text-xs text-[var(--text-muted)]">{h.account}</td>
                  <td className="px-4 py-3">
                    {isCash ? (
                      <span className="font-bold text-[var(--text-secondary)]">CASH</span>
                    ) : (
                      <Link href={`/stock/${h.ticker}`}>
                        <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{h.ticker}</span>
                      </Link>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--text-secondary)] max-w-[140px] truncate">
                    {isCash ? 'Cash Balance' : (h.company_name || h.ticker)}
                  </td>

                  {/* Editable: Shares */}
                  <td className="px-3 py-2">
                    {isCash ? <span className="text-[var(--text-muted)] px-1">—</span> : (
                      <EditableCell
                        value={h.shares}
                        display={h.shares != null ? fmt(h.shares, 4).replace(/\.?0+$/, '') : '—'}
                        isEditing={editCell?.ticker === h.ticker && editCell?.field === 'shares'}
                        editVal={editVal}
                        onStart={() => startCell(h.ticker, h.account, 'shares', h.shares)}
                        onChange={setEditVal}
                        onSave={saveCell}
                        onCancel={cancelCell}
                        width="w-20"
                      />
                    )}
                  </td>

                  {/* Editable: Avg Cost (or cash balance for CASH) */}
                  <td className="px-3 py-2">
                    <EditableCell
                      value={h.avg_cost}
                      display={h.avg_cost != null ? `$${fmt(h.avg_cost)}` : '—'}
                      isEditing={editCell?.ticker === h.ticker && editCell?.field === 'avg_cost'}
                      editVal={editVal}
                      onStart={() => startCell(h.ticker, h.account, 'avg_cost', h.avg_cost)}
                      onChange={setEditVal}
                      onSave={saveCell}
                      onCancel={cancelCell}
                      width="w-24"
                    />
                  </td>

                  {/* Leverage */}
                  <td className="px-4 py-3 text-center">
                    {h.leverage > 1
                      ? <span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.15)]">{h.leverage}×</span>
                      : <span className="text-[var(--text-muted)] text-xs">1×</span>}
                  </td>

                  <td className="px-4 py-3">
                    {h.current_price != null ? (
                      <div className="flex flex-col">
                        <span className="font-medium">${fmt(h.current_price)}</span>
                        {h.day_change != null && (
                          <span className={`text-[10px] font-medium ${h.day_change >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                            {h.day_change >= 0 ? '+' : ''}{fmt(h.day_change)} ({h.day_change_pct >= 0 ? '+' : ''}{fmt(h.day_change_pct)}%)
                          </span>
                        )}
                      </div>
                    ) : <span className="text-[var(--text-muted)]">—</span>}
                  </td>
                  <td className="px-4 py-3 font-medium">
                    {h.market_value != null ? `$${fmt(h.market_value)}` : <span className="text-[var(--text-muted)]">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {weight != null ? (
                      <div className="flex flex-col gap-1 min-w-[64px]">
                        <span className="text-xs font-medium text-[var(--text-secondary)]">{fmt(weight, 1)}%</span>
                        <div className="h-1 rounded-full bg-[rgba(255,255,255,0.08)] overflow-hidden">
                          <div className="h-full rounded-full bg-[var(--accent-purple)]" style={{ width: `${Math.min(weight, 100)}%` }} />
                        </div>
                      </div>
                    ) : <span className="text-[var(--text-muted)]">—</span>}
                  </td>
                  <td className={`px-4 py-3 font-medium ${h.unrealized_pnl == null ? '' : h.unrealized_pnl >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                    {isCash ? <span className="text-[var(--text-muted)]">—</span>
                      : h.unrealized_pnl != null
                        ? <span className="flex items-center gap-1">
                            {h.unrealized_pnl >= 0 ? '+' : ''}${fmt(h.unrealized_pnl)}
                            {h.leverage > 1 && <span className="text-[9px] text-[var(--accent-yellow)] font-bold opacity-80">{h.leverage}×</span>}
                          </span>
                        : '—'}
                  </td>
                  <td className={`px-4 py-3 font-medium ${h.return_pct == null ? '' : h.return_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                    {isCash ? <span className="text-[var(--text-muted)]">—</span>
                      : h.return_pct != null
                        ? <span className="flex items-center gap-1">
                            {fmtPct(h.return_pct)}
                            {h.leverage > 1 && <span className="text-[9px] text-[var(--accent-yellow)] font-bold opacity-80">{h.leverage}×</span>}
                          </span>
                        : '—'}
                  </td>
                  <td className="px-4 py-3">
                    {h.pe_median_price ? (
                      <div className="flex flex-col">
                        <span className="text-xs font-medium">${fmt(h.pe_median_price)}</span>
                        {h.pct_to_fair_pe != null && (
                          <span className={`text-[10px] ${h.pct_to_fair_pe >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                            {h.pct_to_fair_pe >= 0 ? '+' : ''}{h.pct_to_fair_pe}%
                          </span>
                        )}
                      </div>
                    ) : <span className="text-[var(--text-muted)]">—</span>}
                  </td>
                  <td className="px-4 py-3"><StatusBadge status={h.valuation_status} /></td>

                  <td className="px-4 py-3">
                    <button
                      onClick={() => deleteHolding(h.ticker, h.account)}
                      className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.1)] transition-all"
                      title="Remove from portfolio"
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              );
            })}

            {/* Add row */}
            {adding ? (
              <tr className="border-t-2 border-[var(--accent-purple)] bg-[rgba(139,92,246,0.04)]">
                <td className="px-4 py-2">
                  <select
                    value={addVals.account}
                    onChange={e => setAddVals(v => ({ ...v, account: e.target.value }))}
                    className="w-full px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  >
                    <option>Firstrade</option>
                    <option>eToro</option>
                  </select>
                </td>
                <td className="px-4 py-2">
                  <input
                    autoFocus
                    type="text" placeholder="TICKER"
                    value={addVals.ticker}
                    onChange={e => setAddVals(v => ({ ...v, ticker: e.target.value.toUpperCase() }))}
                    className="w-20 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-xs outline-none font-bold uppercase"
                  />
                </td>
                <td className="px-4 py-2 text-xs text-[var(--text-muted)]">—</td>
                <td className="px-4 py-2">
                  <input
                    type="number" step="any" placeholder="Shares"
                    value={addVals.shares}
                    onChange={e => setAddVals(v => ({ ...v, shares: e.target.value }))}
                    className="w-20 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="number" step="any" placeholder="Avg cost"
                    value={addVals.avg_cost}
                    onChange={e => setAddVals(v => ({ ...v, avg_cost: e.target.value }))}
                    className="w-24 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  />
                </td>
                <td colSpan={7} className="px-4 py-2">
                  {addError && <span className="text-[10px] text-[var(--accent-danger)]">{addError}</span>}
                </td>
                <td className="px-4 py-2">
                  <input
                    type="text" placeholder="Notes (optional)"
                    value={addVals.notes}
                    onChange={e => setAddVals(v => ({ ...v, notes: e.target.value }))}
                    className="w-32 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none"
                  />
                </td>
                <td className="px-4 py-2">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={saveAdd}
                      disabled={addSaving}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-purple)] text-white text-xs font-semibold hover:opacity-90 transition-all disabled:opacity-50"
                    >
                      <Check size={12} />
                      {addSaving ? 'Saving…' : 'Save'}
                    </button>
                    <button
                      onClick={() => { setAdding(false); setAddVals(EMPTY_ADD); setAddError(''); }}
                      className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all"
                      title="Cancel"
                    >
                      <X size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ) : (
              <tr className="border-t border-[var(--glass-border)]">
                <td colSpan={14} className="px-4 py-2">
                  <button
                    onClick={() => { setAdding(true); setAddVals({ ...EMPTY_ADD, portfolio: selected }); }}
                    className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent-purple)] transition-colors py-1"
                  >
                    <Plus size={13} /> Add ticker
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EditableCell({ value, display, isEditing, editVal, onStart, onChange, onSave, onCancel, width = 'w-20' }) {
  if (isEditing) {
    return (
      <input
        autoFocus
        type="number"
        step="any"
        value={editVal}
        onChange={e => onChange(e.target.value)}
        onBlur={onSave}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.target.blur(); }
          if (e.key === 'Escape') { onCancel(); }
        }}
        className={`${width} px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-xs outline-none text-[var(--text-primary)]`}
      />
    );
  }
  return (
    <span
      onClick={onStart}
      title="Click to edit"
      className="cursor-text px-1 py-0.5 rounded hover:bg-[rgba(139,92,246,0.12)] hover:text-[var(--text-primary)] transition-colors text-[var(--text-secondary)] text-xs"
    >
      {display}
    </span>
  );
}

function SummaryCard({ icon, label, value, subValue, subValueColor, color }) {
  const colorMap = {
    cyan:   'from-[var(--accent-cyan)] to-[#0891b2]',
    purple: 'from-[var(--accent-purple)] to-[#7c3aed]',
    green:  'from-[var(--accent-green)] to-[#059669]',
    red:    'from-[var(--accent-danger)] to-[#dc2626]',
    yellow: 'from-[var(--accent-yellow)] to-[#d97706]',
    orange: 'from-[#f97316] to-[#ea580c]',
  };
  const textColorMap = {
    cyan:   'text-[var(--accent-cyan)]',
    purple: 'text-[var(--accent-purple)]',
    green:  'text-[var(--accent-green)]',
    red:    'text-[var(--accent-danger)]',
    yellow: 'text-[var(--accent-yellow)]',
    orange: 'text-[#f97316]',
  };
  return (
    <div className="card p-4 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${colorMap[color]} flex items-center justify-center shrink-0 shadow-md`}>
        <span className="text-white">{icon}</span>
      </div>
      <div>
        <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-0.5">{label}</p>
        <p className={`text-lg font-extrabold ${textColorMap[color]}`}>{value}</p>
        {subValue && (
          <p className={`text-[10px] mt-0.5 ${subValueColor ? textColorMap[subValueColor] : 'text-[var(--text-muted)]'}`}>
            {subValue}
          </p>
        )}
      </div>
    </div>
  );
}
