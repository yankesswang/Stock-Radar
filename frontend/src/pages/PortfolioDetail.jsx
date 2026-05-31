import { useState, useEffect, useCallback } from 'react';
import { Link, useRoute } from 'wouter';
import axios from 'axios';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import {
  Briefcase, Plus, Trash2, Edit2, Check, X, RefreshCw,
  TrendingUp, TrendingDown, DollarSign, BarChart2, ChevronLeft,
  ArrowUpRight, ArrowDownRight,
} from 'lucide-react';

const PIE_COLORS = [
  '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b',
  '#ef4444', '#6366f1', '#ec4899', '#84cc16',
];

function fmt(n, dec = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtPct(n) {
  if (n == null) return '—';
  return `${n > 0 ? '+' : ''}${fmt(n)}%`;
}

function StatusBadge({ status }) {
  if (!status) return <span className="text-[var(--text-muted)]">—</span>;
  const colors = {
    Undervalued: 'text-[var(--accent-green)] bg-[rgba(16,185,129,0.1)]',
    Overvalued:  'text-[var(--accent-danger)] bg-[rgba(248,113,113,0.1)]',
    'Fairly Valued': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  };
  const cls = colors[status] || 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)]';
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${cls}`}>
      {status}
    </span>
  );
}

export default function PortfolioDetail() {
  const [, params] = useRoute('/portfolio/:id');
  const pid = params?.id;

  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [adding,  setAdding]  = useState(false);
  const [editId,  setEditId]  = useState(null);
  const [selectedSector, setSelectedSector] = useState(null);

  // add-holding form
  const [form, setForm] = useState({ ticker: '', shares: '', avg_cost: '', notes: '' });
  const [saving, setSaving] = useState(false);

  // edit-holding form
  const [editForm, setEditForm] = useState({ shares: '', avg_cost: '', notes: '' });

  const load = useCallback(() => {
    setLoading(true);
    axios.get(`/api/portfolio/${pid}`)
      .then(r => setData(r.data))
      .finally(() => setLoading(false));
  }, [pid]);

  useEffect(() => { if (pid) load(); }, [pid, load]);

  const addHolding = async () => {
    if (!form.ticker.trim() || !form.shares || !form.avg_cost) return;
    setSaving(true);
    await axios.post(`/api/portfolio/${pid}/holdings`, {
      ticker:   form.ticker.trim().toUpperCase(),
      shares:   parseFloat(form.shares),
      avg_cost: parseFloat(form.avg_cost),
      notes:    form.notes.trim(),
    });
    setForm({ ticker: '', shares: '', avg_cost: '', notes: '' });
    setAdding(false);
    load();
    setSaving(false);
  };

  const startEdit = (h) => {
    setEditId(h.id);
    setEditForm({ shares: h.shares, avg_cost: h.avg_cost, notes: h.notes || '' });
  };

  const saveEdit = async (hid) => {
    await axios.put(`/api/portfolio/${pid}/holdings/${hid}`, {
      shares:   parseFloat(editForm.shares),
      avg_cost: parseFloat(editForm.avg_cost),
      notes:    editForm.notes,
    });
    setEditId(null);
    load();
  };

  const removeHolding = async (hid) => {
    if (!confirm('Remove this holding?')) return;
    await axios.delete(`/api/portfolio/${pid}/holdings/${hid}`);
    load();
  };

  if (loading) {
    return (
      <div className="flex justify-center py-32">
        <RefreshCw size={22} className="animate-spin text-[var(--accent-purple)]" />
      </div>
    );
  }

  if (!data) {
    return <div className="container py-20 text-center text-[var(--text-muted)]">Portfolio not found.</div>;
  }

  const { summary, holdings } = data;
  const selectedSectorHoldings = selectedSector
    ? holdings
        .filter(h => (h.industry || 'Unknown') === selectedSector)
        .slice()
        .sort((a, b) => (b.market_value ?? b.cost_basis ?? 0) - (a.market_value ?? a.cost_basis ?? 0))
    : [];
  const selectedSectorValue = selectedSectorHoldings.reduce((s, h) => s + (h.market_value ?? h.cost_basis ?? 0), 0);

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/portfolio">
            <button className="p-1.5 rounded-lg hover:bg-[rgba(255,255,255,0.06)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
              <ChevronLeft size={18} />
            </button>
          </Link>
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
            <Briefcase size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">{data.name}</h1>
            {data.description && <p className="text-xs text-[var(--text-muted)]">{data.description}</p>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="p-2 rounded-lg hover:bg-[rgba(255,255,255,0.06)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            title="Refresh"
          >
            <RefreshCw size={15} />
          </button>
          <button
            onClick={() => setAdding(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90 transition-all shadow-lg shadow-purple-500/20"
          >
            <Plus size={15} /> Add Holding
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <SummaryCard
          icon={<DollarSign size={15} />}
          label="Market Value"
          value={`$${fmt(summary.total_value)}`}
          color="cyan"
        />
        <SummaryCard
          icon={<BarChart2 size={15} />}
          label="Cost Basis"
          value={`$${fmt(summary.total_cost)}`}
          color="purple"
        />
        <SummaryCard
          icon={summary.total_pnl >= 0 ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
          label="Unrealized P&L"
          value={`${summary.total_pnl >= 0 ? '+' : ''}$${fmt(summary.total_pnl)}`}
          color={summary.total_pnl >= 0 ? 'green' : 'red'}
        />
        <SummaryCard
          icon={summary.return_pct >= 0 ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
          label="Return"
          value={fmtPct(summary.return_pct)}
          color={summary.return_pct >= 0 ? 'green' : 'red'}
        />
      </div>

      {/* Sector allocation */}
      {summary.sectors.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] mb-4">Sector Allocation</h3>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={summary.sectors}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={2}
                  cursor="pointer"
                  onClick={(entry) => setSelectedSector(prev => prev === entry.name ? null : entry.name)}
                >
                  {summary.sectors.map((sector, i) => (
                    <Cell
                      key={sector.name}
                      fill={PIE_COLORS[i % PIE_COLORS.length]}
                      opacity={!selectedSector || selectedSector === sector.name ? 1 : 0.35}
                      stroke={selectedSector === sector.name ? 'var(--text-primary)' : 'transparent'}
                      strokeWidth={selectedSector === sector.name ? 2 : 0}
                    />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(v) => `$${fmt(v)}`}
                  contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 8, fontSize: 12 }}
                />
                <Legend
                  onClick={(entry) => setSelectedSector(prev => prev === entry.value ? null : entry.value)}
                  formatter={(v) => (
                    <span
                      style={{
                        fontSize: 11,
                        color: selectedSector === v ? 'var(--text-primary)' : 'var(--text-secondary)',
                        fontWeight: selectedSector === v ? 700 : 500,
                        cursor: 'pointer',
                      }}
                    >
                      {v}
                    </span>
                  )}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          {selectedSector && (
            <div className="mt-4 rounded-lg border border-[var(--glass-border)] bg-[rgba(255,255,255,0.03)] overflow-hidden">
              <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--glass-border)]">
                <div>
                  <p className="text-sm font-semibold text-[var(--text-primary)]">{selectedSector}</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {selectedSectorHoldings.length} holdings · ${fmt(selectedSectorValue)} · {summary.total_value > 0 ? (selectedSectorValue / summary.total_value * 100).toFixed(1) : '0.0'}% of portfolio
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedSector(null)}
                  className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)]"
                  title="Clear sector selection"
                >
                  <X size={14} />
                </button>
              </div>
              <div className="divide-y divide-[var(--glass-border)]">
                {selectedSectorHoldings.map(h => {
                  const value = h.market_value ?? h.cost_basis ?? 0;
                  const pct = selectedSectorValue > 0 ? value / selectedSectorValue * 100 : 0;
                  return (
                    <div key={h.id} className="grid grid-cols-[minmax(80px,1fr)_minmax(120px,2fr)_minmax(90px,1fr)] gap-3 px-4 py-2.5 text-xs items-center">
                      <Link href={`/stock/${h.ticker}`} className="font-mono font-semibold text-[var(--accent-cyan)] hover:underline">
                        {h.ticker}
                      </Link>
                      <span className="text-[var(--text-secondary)] truncate">{h.company_name || h.ticker}</span>
                      <span className="text-right text-[var(--text-primary)]">${fmt(value)} <span className="text-[var(--text-muted)]">({pct.toFixed(1)}%)</span></span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Add holding form */}
      {adding && (
        <div className="card p-5 border-[rgba(139,92,246,0.3)] space-y-3">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)]">Add Holding</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <input
              autoFocus
              type="text"
              placeholder="Ticker *"
              value={form.ticker}
              onChange={e => setForm(f => ({ ...f, ticker: e.target.value }))}
              className="input-field uppercase"
            />
            <input
              type="number"
              placeholder="Shares *"
              value={form.shares}
              onChange={e => setForm(f => ({ ...f, shares: e.target.value }))}
              className="input-field"
            />
            <input
              type="number"
              placeholder="Avg Cost *"
              value={form.avg_cost}
              onChange={e => setForm(f => ({ ...f, avg_cost: e.target.value }))}
              className="input-field"
            />
            <input
              type="text"
              placeholder="Notes"
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              className="input-field"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={addHolding}
              disabled={!form.ticker.trim() || !form.shares || !form.avg_cost || saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90 disabled:opacity-50 transition-all"
            >
              {saving ? 'Adding…' : 'Add'}
            </button>
            <button
              onClick={() => { setAdding(false); setForm({ ticker: '', shares: '', avg_cost: '', notes: '' }); }}
              className="px-4 py-2 rounded-lg text-sm font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Holdings table */}
      {holdings.length === 0 ? (
        <div className="card p-16 text-center">
          <Briefcase size={40} className="mx-auto mb-3 text-[var(--text-muted)] opacity-20" />
          <p className="text-[var(--text-muted)] text-sm mb-4">No holdings yet.</p>
          <button
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--accent-purple)] text-white hover:opacity-90"
          >
            <Plus size={14} /> Add your first holding
          </button>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--glass-border)]">
                {['Ticker', 'Company', 'Shares', 'Avg Cost', 'Price', 'Market Value', 'P&L', 'Return', 'Fair Value (PE)', 'Valuation', ''].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-[var(--text-muted)] whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {holdings.map(h => (
                <tr key={h.id} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors group">
                  {editId === h.id ? (
                    /* Inline edit row */
                    <>
                      <td className="px-4 py-2">
                        <Link href={`/stock/${h.ticker}`}>
                          <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{h.ticker}</span>
                        </Link>
                      </td>
                      <td className="px-4 py-2 text-[var(--text-muted)] text-xs">{h.company_name}</td>
                      <td className="px-4 py-2">
                        <input
                          type="number"
                          value={editForm.shares}
                          onChange={e => setEditForm(f => ({ ...f, shares: e.target.value }))}
                          className="w-20 px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)]"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          type="number"
                          value={editForm.avg_cost}
                          onChange={e => setEditForm(f => ({ ...f, avg_cost: e.target.value }))}
                          className="w-24 px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)]"
                        />
                      </td>
                      <td className="px-4 py-2 text-[var(--text-secondary)]">
                        {h.current_price ? `$${fmt(h.current_price)}` : '—'}
                      </td>
                      <td className="px-4 py-2">{h.market_value ? `$${fmt(h.market_value)}` : '—'}</td>
                      <td className="px-4 py-2" colSpan={2}>
                        <input
                          type="text"
                          placeholder="Notes"
                          value={editForm.notes}
                          onChange={e => setEditForm(f => ({ ...f, notes: e.target.value }))}
                          className="w-32 px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--glass-border)] text-xs outline-none focus:border-[var(--accent-purple)]"
                        />
                      </td>
                      <td className="px-4 py-2" colSpan={2} />
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-1">
                          <button onClick={() => saveEdit(h.id)} className="p-1.5 rounded-lg text-[var(--accent-green)] hover:bg-[rgba(16,185,129,0.08)] transition-all">
                            <Check size={13} />
                          </button>
                          <button onClick={() => setEditId(null)} className="p-1.5 rounded-lg text-[var(--text-muted)] hover:bg-[rgba(255,255,255,0.05)] transition-all">
                            <X size={13} />
                          </button>
                        </div>
                      </td>
                    </>
                  ) : (
                    /* Normal row */
                    <>
                      <td className="px-4 py-3">
                        <Link href={`/stock/${h.ticker}`}>
                          <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{h.ticker}</span>
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-[var(--text-secondary)] text-xs max-w-[140px] truncate">{h.company_name || h.ticker}</td>
                      <td className="px-4 py-3 text-[var(--text-secondary)]">{fmt(h.shares, 4).replace(/\.?0+$/, '')}</td>
                      <td className="px-4 py-3 text-[var(--text-secondary)]">${fmt(h.avg_cost)}</td>
                      <td className="px-4 py-3 font-medium">
                        {h.current_price ? `$${fmt(h.current_price)}` : <span className="text-[var(--text-muted)]">—</span>}
                      </td>
                      <td className="px-4 py-3 font-medium">
                        {h.market_value ? `$${fmt(h.market_value)}` : <span className="text-[var(--text-muted)]">—</span>}
                      </td>
                      <td className={`px-4 py-3 font-medium ${h.unrealized_pnl == null ? '' : h.unrealized_pnl >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                        {h.unrealized_pnl == null ? '—' : `${h.unrealized_pnl >= 0 ? '+' : ''}$${fmt(h.unrealized_pnl)}`}
                      </td>
                      <td className={`px-4 py-3 font-medium ${h.return_pct == null ? '' : h.return_pct >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                        {fmtPct(h.return_pct)}
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
                      <td className="px-4 py-3">
                        <StatusBadge status={h.valuation_status} />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button onClick={() => startEdit(h)} className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-cyan)] hover:bg-[rgba(6,182,212,0.08)] transition-all">
                            <Edit2 size={13} />
                          </button>
                          <button onClick={() => removeHolding(h.id)} className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.08)] transition-all">
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ icon, label, value, color }) {
  const colorMap = {
    cyan:   'from-[var(--accent-cyan)] to-[#0891b2]',
    purple: 'from-[var(--accent-purple)] to-[#7c3aed]',
    green:  'from-[var(--accent-green)] to-[#059669]',
    red:    'from-[var(--accent-danger)] to-[#dc2626]',
    yellow: 'from-[var(--accent-yellow)] to-[#d97706]',
  };
  const textColorMap = {
    cyan:   'text-[var(--accent-cyan)]',
    purple: 'text-[var(--accent-purple)]',
    green:  'text-[var(--accent-green)]',
    red:    'text-[var(--accent-danger)]',
    yellow: 'text-[var(--accent-yellow)]',
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
