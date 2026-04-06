import { useState, useEffect } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  Bell, BellOff, Plus, Trash2, RefreshCw, Target, TrendingUp,
  CheckCircle, AlertTriangle, X, Check,
} from 'lucide-react';

const MA_PERIODS = [5, 10, 20, 50, 200];

function fmt(n, dec = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

// ── Inline alert editor ──────────────────────────────────────────────────────
function AlertEditor({ ticker, initial, onSave, onCancel }) {
  const [targetPrice, setTargetPrice] = useState(initial?.target_price ?? '');
  const [mas, setMas] = useState(initial?.mas ?? []);
  const [saving, setSaving] = useState(false);

  function toggleMA(p) {
    setMas(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]);
  }

  async function save() {
    setSaving(true);
    try {
      await axios.put(`/api/alerts/${ticker}`, {
        target_price: targetPrice === '' ? null : parseFloat(targetPrice),
        mas,
      });
      onSave();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <div className="flex items-center gap-1.5">
        <Target size={12} className="text-[var(--accent-purple)] shrink-0" />
        <input
          type="number"
          step="any"
          placeholder="Target price"
          value={targetPrice}
          onChange={e => setTargetPrice(e.target.value)}
          className="w-28 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-xs outline-none focus:border-[var(--accent-purple)] transition-colors placeholder:text-[var(--text-muted)]"
        />
      </div>
      <div className="flex items-center gap-1.5">
        <TrendingUp size={12} className="text-[var(--accent-purple)] shrink-0" />
        <div className="flex gap-1">
          {MA_PERIODS.map(p => (
            <button
              key={p}
              onClick={() => toggleMA(p)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium border transition-all ${
                mas.includes(p)
                  ? 'bg-[var(--accent-purple)] border-[var(--accent-purple)] text-white'
                  : 'bg-[var(--bg-tertiary)] border-[var(--border-color)] text-[var(--text-secondary)] hover:border-[var(--accent-purple)]'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-1.5 ml-auto">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-1 px-3 py-1 rounded-lg bg-[var(--accent-purple)] text-white text-xs font-semibold hover:opacity-90 disabled:opacity-50"
        >
          <Check size={11} />{saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={onCancel}
          className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)] transition-all"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}

// ── MA status chip ────────────────────────────────────────────────────────────
function MAChip({ period, data }) {
  if (!data) return null;
  const { value, near, crossed } = data;
  let color = 'text-[var(--text-muted)] border-[var(--border-color)]';
  let icon = null;
  if (crossed) {
    color = 'text-yellow-400 border-yellow-600 bg-yellow-900/20';
    icon = <AlertTriangle size={10} />;
  } else if (near) {
    color = 'text-blue-400 border-blue-600 bg-blue-900/20';
    icon = <Bell size={10} />;
  }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ${color}`}>
      {icon}{period}MA
      {value != null && <span className="opacity-70">${fmt(value)}</span>}
    </span>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PriceAlerts() {
  const [configs, setConfigs]       = useState({});   // { TICKER: { target_price, mas } }
  const [statuses, setStatuses]     = useState({});   // { TICKER: status object }
  const [checking, setChecking]     = useState(false);
  const [editingTicker, setEditing] = useState(null); // ticker being edited
  const [addTicker, setAddTicker]   = useState('');
  const [addOpen, setAddOpen]       = useState(false);

  const loadConfigs = () => {
    axios.get('/api/alerts').then(r => setConfigs(r.data)).catch(() => {});
  };

  useEffect(() => { loadConfigs(); }, []);

  const checkAll = async () => {
    setChecking(true);
    try {
      const r = await axios.get('/api/alerts/triggered');
      setStatuses(r.data);
    } catch {}
    finally { setChecking(false); }
  };

  const checkOne = async (ticker) => {
    try {
      const r = await axios.get(`/api/alerts/${ticker}/status`);
      setStatuses(prev => ({ ...prev, [ticker]: r.data }));
    } catch {}
  };

  const deleteAlert = async (ticker) => {
    await axios.delete(`/api/alerts/${ticker}`).catch(() => {});
    setConfigs(prev => { const c = { ...prev }; delete c[ticker]; return c; });
    setStatuses(prev => { const s = { ...prev }; delete s[ticker]; return s; });
  };

  const handleAddSave = () => {
    const t = addTicker.toUpperCase().trim();
    if (!t) return;
    setEditing(t);
    setAddTicker('');
    setAddOpen(false);
  };

  const tickers = Object.keys(configs);
  const triggeredCount = Object.values(statuses).filter(
    s => s.target_hit || Object.values(s.ma_status ?? {}).some(m => m.near || m.crossed)
  ).length;

  return (
    <div className="container py-10 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-purple)] to-[var(--accent-cyan)] flex items-center justify-center shadow-lg">
            <Bell size={17} className="text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Price Alerts</h1>
            <p className="text-xs text-[var(--text-muted)]">
              Target price · moving average alerts for any ticker
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={checkAll}
            disabled={checking || tickers.length === 0}
            className="relative flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-[rgba(139,92,246,0.1)] text-[var(--accent-purple)] hover:bg-[rgba(139,92,246,0.18)] transition-all disabled:opacity-50"
          >
            {checking
              ? <RefreshCw size={14} className="animate-spin" />
              : <Bell size={14} />}
            Check All
            {triggeredCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-[var(--accent-danger)] text-white text-[9px] font-bold flex items-center justify-center">
                {triggeredCount}
              </span>
            )}
          </button>
          <button
            onClick={() => setAddOpen(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-[var(--accent-purple)] text-white hover:opacity-90 transition-all"
          >
            <Plus size={14} /> Add Alert
          </button>
        </div>
      </div>

      {/* Add ticker input */}
      {addOpen && (
        <div className="card p-4 flex items-center gap-3">
          <Bell size={14} className="text-[var(--accent-purple)] shrink-0" />
          <input
            autoFocus
            type="text"
            placeholder="Ticker symbol (e.g. AAPL)"
            value={addTicker}
            onChange={e => setAddTicker(e.target.value.toUpperCase())}
            onKeyDown={e => { if (e.key === 'Enter') handleAddSave(); if (e.key === 'Escape') { setAddOpen(false); setAddTicker(''); }}}
            className="flex-1 px-3 py-1.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-sm outline-none text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
          />
          <button
            onClick={handleAddSave}
            disabled={!addTicker.trim()}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-[var(--accent-purple)] text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50"
          >
            <Check size={13} /> Next
          </button>
          <button
            onClick={() => { setAddOpen(false); setAddTicker(''); }}
            className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Alert list */}
      {tickers.length === 0 && !addOpen ? (
        <div className="card p-16 flex flex-col items-center gap-3 text-center">
          <BellOff size={32} className="text-[var(--text-muted)] opacity-40" />
          <p className="text-sm text-[var(--text-muted)]">No alerts configured yet</p>
          <button
            onClick={() => setAddOpen(true)}
            className="mt-2 flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium bg-[var(--accent-purple)] text-white hover:opacity-90"
          >
            <Plus size={13} /> Add your first alert
          </button>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--glass-border)]">
                {['Ticker', 'Target Price', 'Moving Averages', 'Current Price', 'Status', ''].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-[var(--text-muted)] whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tickers.map(ticker => {
                const cfg    = configs[ticker];
                const st     = statuses[ticker];
                const isEditing = editingTicker === ticker;

                const anyTriggered = st && (
                  st.target_hit ||
                  Object.values(st.ma_status ?? {}).some(m => m.near || m.crossed)
                );

                return (
                  <tr key={ticker} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors group">
                    {/* Ticker */}
                    <td className="px-4 py-3">
                      <Link href={`/stock/${ticker}`}>
                        <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{ticker}</span>
                      </Link>
                    </td>

                    {/* Target price */}
                    <td className="px-4 py-3">
                      {isEditing ? (
                        <span className="text-xs text-[var(--text-muted)] italic">editing below…</span>
                      ) : cfg.target_price != null ? (
                        <div className="flex items-center gap-1.5">
                          <Target size={12} className="text-[var(--accent-purple)]" />
                          <span className="text-xs font-medium">${fmt(cfg.target_price)}</span>
                          {st?.target_hit && (
                            <span className="flex items-center gap-0.5 text-[10px] text-green-400 font-semibold">
                              <CheckCircle size={11} /> Hit
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="text-[var(--text-muted)] text-xs">—</span>
                      )}
                    </td>

                    {/* Configured MAs */}
                    <td className="px-4 py-3">
                      {isEditing ? (
                        <span className="text-xs text-[var(--text-muted)] italic">—</span>
                      ) : cfg.mas?.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {cfg.mas.map(p => (
                            <MAChip key={p} period={p} data={st?.ma_status?.[String(p)]} />
                          ))}
                        </div>
                      ) : (
                        <span className="text-[var(--text-muted)] text-xs">—</span>
                      )}
                    </td>

                    {/* Current price */}
                    <td className="px-4 py-3 font-medium text-xs">
                      {st?.price != null ? `$${fmt(st.price)}` : <span className="text-[var(--text-muted)]">—</span>}
                    </td>

                    {/* Status */}
                    <td className="px-4 py-3">
                      {st ? (
                        anyTriggered ? (
                          <span className="flex items-center gap-1 text-xs text-[var(--accent-danger)] font-semibold">
                            <AlertTriangle size={12} /> Alert
                          </span>
                        ) : (
                          <span className="text-xs text-[var(--text-muted)]">No trigger</span>
                        )
                      ) : (
                        <button
                          onClick={() => checkOne(ticker)}
                          className="text-xs text-[var(--accent-purple)] hover:underline"
                        >
                          Check
                        </button>
                      )}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => setEditing(isEditing ? null : ticker)}
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-medium border transition-all ${
                            isEditing
                              ? 'border-[var(--accent-purple)] text-[var(--accent-purple)] bg-[rgba(139,92,246,0.1)]'
                              : 'border-[var(--border-color)] text-[var(--text-muted)] hover:border-[var(--accent-purple)] hover:text-[var(--accent-purple)]'
                          }`}
                        >
                          {isEditing ? 'Cancel' : 'Edit'}
                        </button>
                        <button
                          onClick={() => deleteAlert(ticker)}
                          className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.1)] transition-all"
                          title="Remove alert"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Inline editor row */}
          {editingTicker && (
            <div className="border-t-2 border-[var(--accent-purple)] px-4 py-3 bg-[rgba(139,92,246,0.04)]">
              <p className="text-xs font-semibold text-[var(--accent-purple)] mb-2">{editingTicker} — edit alert</p>
              <AlertEditor
                ticker={editingTicker}
                initial={configs[editingTicker]}
                onSave={() => { setEditing(null); loadConfigs(); }}
                onCancel={() => setEditing(null)}
              />
            </div>
          )}

          {/* New ticker editor (when adding) */}
          {!addOpen && addTicker === '' && editingTicker && !configs[editingTicker] && (
            <div className="border-t-2 border-[var(--accent-purple)] px-4 py-3 bg-[rgba(139,92,246,0.04)]">
              <p className="text-xs font-semibold text-[var(--accent-purple)] mb-2">{editingTicker} — new alert</p>
              <AlertEditor
                ticker={editingTicker}
                initial={{}}
                onSave={() => { setEditing(null); loadConfigs(); }}
                onCancel={() => setEditing(null)}
              />
            </div>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 text-[11px] text-[var(--text-muted)]">
        <span className="flex items-center gap-1"><Bell size={10} className="text-blue-400" /> Near MA (±1%)</span>
        <span className="flex items-center gap-1"><AlertTriangle size={10} className="text-yellow-400" /> Crossed MA</span>
        <span className="flex items-center gap-1"><CheckCircle size={10} className="text-green-400" /> Target hit</span>
        <span className="ml-auto">Prices fetched live via yfinance when you click "Check"</span>
      </div>
    </div>
  );
}
