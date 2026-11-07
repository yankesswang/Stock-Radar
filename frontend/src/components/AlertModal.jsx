import { useEffect, useState } from 'react';
import axios from '../lib/apiClient';
import { X, Bell, BellOff, Target, TrendingUp, AlertTriangle, CheckCircle } from 'lucide-react';

const MA_PERIODS = [5, 10, 20, 50, 200];

export default function AlertModal({ ticker, onClose }) {
  const [config, setConfig] = useState({ target_price: '', mas: [] });
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  const [saved, setSaved] = useState(false);

  // Load existing config
  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    axios.get('/api/alerts')
      .then(r => {
        const cfg = r.data[ticker.toUpperCase()];
        if (cfg) {
          setConfig({ target_price: cfg.target_price ?? '', mas: cfg.mas ?? [] });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [ticker]);

  function toggleMA(period) {
    setConfig(prev => ({
      ...prev,
      mas: prev.mas.includes(period)
        ? prev.mas.filter(p => p !== period)
        : [...prev.mas, period],
    }));
  }

  async function checkStatus() {
    setStatusLoading(true);
    setStatus(null);
    try {
      const r = await axios.get(`/api/alerts/${ticker}/status`);
      setStatus(r.data);
    } catch (e) {
      setStatus({ error: e.response?.data?.detail || 'No alert config saved yet — save first.' });
    } finally {
      setStatusLoading(false);
    }
  }

  async function saveConfig() {
    setSaving(true);
    try {
      await axios.put(`/api/alerts/${ticker}`, {
        target_price: config.target_price === '' ? null : parseFloat(config.target_price),
        mas: config.mas,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error('Save alert failed', e);
    } finally {
      setSaving(false);
    }
  }

  async function deleteAlert() {
    try {
      await axios.delete(`/api/alerts/${ticker}`);
      setConfig({ target_price: '', mas: [] });
      setStatus(null);
    } catch {}
  }

  const hasConfig = config.target_price !== '' || config.mas.length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-xl shadow-2xl w-full max-w-md mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-color)]">
          <div className="flex items-center gap-2">
            <Bell size={16} className="text-[var(--accent-purple)]" />
            <span className="font-semibold text-[var(--text-primary)] text-sm">
              Price Alerts — {ticker}
            </span>
          </div>
          <button onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-5">
          {loading ? (
            <p className="text-xs text-[var(--text-muted)] text-center py-4">Loading…</p>
          ) : (
            <>
              {/* Target price */}
              <div>
                <label className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)] mb-2">
                  <Target size={13} className="text-[var(--accent-purple)]" />
                  Target Price (alert when price ≥ target)
                </label>
                <input
                  type="number"
                  step="any"
                  placeholder="e.g. 150.00"
                  value={config.target_price}
                  onChange={e => setConfig(prev => ({ ...prev, target_price: e.target.value }))}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-purple)] transition-colors placeholder:text-[var(--text-muted)]"
                />
              </div>

              {/* MA toggles */}
              <div>
                <label className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)] mb-2">
                  <TrendingUp size={13} className="text-[var(--accent-purple)]" />
                  Moving Averages (alert when near or crossed)
                </label>
                <div className="flex gap-2 flex-wrap">
                  {MA_PERIODS.map(p => {
                    const active = config.mas.includes(p);
                    return (
                      <button
                        key={p}
                        onClick={() => toggleMA(p)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                          active
                            ? 'bg-[var(--accent-purple)] border-[var(--accent-purple)] text-white'
                            : 'bg-[var(--bg-tertiary)] border-[var(--border-color)] text-[var(--text-secondary)] hover:border-[var(--accent-purple)]'
                        }`}
                      >
                        {p}MA
                      </button>
                    );
                  })}
                </div>
                <p className="text-[10px] text-[var(--text-muted)] mt-1.5">
                  "Near" = within ±1% of MA &nbsp;·&nbsp; "Crossed" = price crossed MA since yesterday
                </p>
              </div>

              {/* Live status */}
              {status && (
                <div className="rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border-color)] p-3 space-y-2">
                  {status.error ? (
                    <p className="text-xs text-[var(--text-muted)]">{status.error}</p>
                  ) : (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-[var(--text-muted)]">Current Price</span>
                        <span className="text-xs font-semibold text-[var(--text-primary)]">
                          ${status.price?.toFixed(2) ?? '—'}
                        </span>
                      </div>

                      {status.target_price != null && (
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-[var(--text-muted)]">
                            Target ${status.target_price.toFixed(2)}
                          </span>
                          {status.target_hit ? (
                            <span className="flex items-center gap-1 text-xs text-green-400">
                              <CheckCircle size={12} /> Hit
                            </span>
                          ) : (
                            <span className="text-xs text-[var(--text-muted)]">Not yet</span>
                          )}
                        </div>
                      )}

                      {Object.entries(status.ma_status ?? {}).map(([period, s]) => (
                        <div key={period} className="flex items-center justify-between">
                          <span className="text-xs text-[var(--text-muted)]">
                            {period}MA <span className="text-[var(--text-primary)]">${s.value?.toFixed(2)}</span>
                          </span>
                          <div className="flex gap-2">
                            {s.crossed && (
                              <span className="flex items-center gap-1 text-xs text-yellow-400">
                                <AlertTriangle size={11} /> Crossed
                              </span>
                            )}
                            {s.near && !s.crossed && (
                              <span className="flex items-center gap-1 text-xs text-blue-400">
                                <Bell size={11} /> Near
                              </span>
                            )}
                            {!s.near && !s.crossed && (
                              <span className="text-xs text-[var(--text-muted)]">—</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-[var(--border-color)] gap-2">
          <div className="flex gap-2">
            <button
              onClick={checkStatus}
              disabled={statusLoading}
              className="px-3 py-1.5 rounded-lg text-xs border border-[var(--border-color)] text-[var(--text-secondary)] hover:border-[var(--accent-purple)] hover:text-[var(--text-primary)] transition-colors disabled:opacity-50"
            >
              {statusLoading ? 'Checking…' : 'Check Status'}
            </button>
            {hasConfig && (
              <button
                onClick={deleteAlert}
                className="px-3 py-1.5 rounded-lg text-xs border border-red-800 text-red-400 hover:bg-red-900/20 transition-colors flex items-center gap-1"
              >
                <BellOff size={12} /> Clear
              </button>
            )}
          </div>
          <button
            onClick={saveConfig}
            disabled={saving}
            className="px-4 py-1.5 rounded-lg text-xs font-medium bg-[var(--accent-purple)] text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {saved ? 'Saved ✓' : saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
