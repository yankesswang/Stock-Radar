import { useState, useEffect, useRef } from 'react';
import { Link } from 'wouter';
import axios from 'axios';
import {
  BookMarked, Plus, Trash2, RefreshCw, X, Check,
  TrendingUp, TrendingDown, Loader2, Zap, CheckCircle2,
} from 'lucide-react';

function fmt(n, dec = 2) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function StatusBadge({ status }) {
  if (!status) return <span className="text-[var(--text-muted)]">—</span>;
  const s = status.toLowerCase();
  const map = {
    undervalued:     'text-[var(--accent-green)]  bg-[rgba(16,185,129,0.1)]',
    overvalued:      'text-[var(--accent-danger)]  bg-[rgba(248,113,113,0.1)]',
    'fairly valued': 'text-[var(--accent-yellow)] bg-[rgba(245,158,11,0.1)]',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize ${map[s] || 'text-[var(--text-muted)] bg-[rgba(255,255,255,0.05)]'}`}>
      {status}
    </span>
  );
}

// ── Inline scrape widget for a single ticker ─────────────────────────────────
function ScrapeInline({ ticker, scrapeGroups, onDone }) {
  const [open,    setOpen]    = useState(false);
  const [group,   setGroup]   = useState('');
  const [jobId,   setJobId]   = useState(null);
  const [job,     setJob]     = useState(null);
  const [starting,setStarting]= useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!jobId) return;
    pollRef.current = setInterval(async () => {
      try {
        const r = await axios.get(`/api/scrape/${jobId}`);
        setJob(r.data);
        if (r.data.status !== 'running') {
          clearInterval(pollRef.current);
          if (r.data.status === 'completed') onDone();
        }
      } catch { clearInterval(pollRef.current); }
    }, 2000);
    return () => clearInterval(pollRef.current);
  }, [jobId]);

  const start = async () => {
    if (!group) return;
    setStarting(true);
    try {
      const r = await axios.post('/api/scrape', { ticker, group });
      setJobId(r.data.job_id);
      setJob(r.data);
    } catch (e) {
      alert(e.response?.data?.detail || 'Scrape failed to start');
    } finally {
      setStarting(false);
    }
  };

  const isRunning   = job?.status === 'running';
  const isCompleted = job?.status === 'completed';
  const isFailed    = job?.status === 'failed';

  if (isCompleted) {
    return (
      <span className="flex items-center gap-1 text-[10px] text-[var(--accent-green)] font-semibold">
        <CheckCircle2 size={11} /> Done
      </span>
    );
  }

  if (isRunning || starting) {
    const lastStep = job?.steps?.at(-1) ?? 'Starting…';
    return (
      <span className="flex items-center gap-1 text-[10px] text-[var(--accent-purple)] max-w-[200px]" title={lastStep}>
        <Loader2 size={11} className="animate-spin shrink-0" />
        <span className="truncate">{lastStep}</span>
      </span>
    );
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 px-2 py-0.5 rounded border border-dashed border-[var(--border-color)] text-[10px] text-[var(--text-muted)] hover:border-[var(--accent-purple)] hover:text-[var(--accent-purple)] transition-all"
        title="Scrape valuation data for this ticker"
      >
        <Zap size={10} /> Scrape
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <select
        autoFocus
        value={group}
        onChange={e => setGroup(e.target.value)}
        className="px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-[10px] outline-none text-[var(--text-primary)] max-w-[110px]"
      >
        <option value="" disabled>Pick group…</option>
        {scrapeGroups.map(g => <option key={g} value={g}>{g}</option>)}
      </select>
      <button
        onClick={start}
        disabled={!group || starting}
        className="p-1 rounded bg-[var(--accent-purple)] text-white hover:opacity-90 disabled:opacity-50"
        title="Start scrape"
      >
        <Zap size={10} />
      </button>
      <button
        onClick={() => setOpen(false)}
        className="p-1 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
      >
        <X size={10} />
      </button>
      {isFailed && <span className="text-[10px] text-[var(--accent-danger)]">Failed</span>}
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function Watchlists() {
  const [groups,       setGroups]       = useState([]);
  const [selected,     setSelected]     = useState(null);
  const [groupData,    setGroupData]    = useState(null);
  const [loadingList,  setLoadingList]  = useState(true);
  const [loadingData,  setLoadingData]  = useState(false);
  const [scrapeGroups, setScrapeGroups] = useState([]);
  // new watchlist group
  const [newGroupVal,  setNewGroupVal]  = useState('');
  const [addingGroup,  setAddingGroup]  = useState(false);
  const [savingGroup,  setSavingGroup]  = useState(false);
  // add ticker
  const [newTicker,    setNewTicker]    = useState('');
  const [addingTicker, setAddingTicker] = useState(false);

  const loadGroups = () => {
    setLoadingList(true);
    axios.get('/api/watchlists')
      .then(r => {
        setGroups(r.data);
        if (!selected && r.data.length > 0) setSelected(r.data[0].name);
      })
      .catch(() => {})
      .finally(() => setLoadingList(false));
  };

  const loadGroupData = (name) => {
    if (!name) return;
    setLoadingData(true);
    setGroupData(null);
    axios.get(`/api/watchlists/${encodeURIComponent(name)}`)
      .then(r => setGroupData(r.data))
      .catch(() => {})
      .finally(() => setLoadingData(false));
  };

  useEffect(() => {
    loadGroups();
    axios.get('/api/scrape').then(r => setScrapeGroups(r.data.groups || [])).catch(() => {});
  }, []);

  useEffect(() => { if (selected) loadGroupData(selected); }, [selected]);

  const createGroup = async () => {
    const name = newGroupVal.trim();
    if (!name) return;
    setSavingGroup(true);
    try {
      await axios.post('/api/watchlists', { name });
      setNewGroupVal('');
      setAddingGroup(false);
      await loadGroups();
      setSelected(name);
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to create group');
    } finally {
      setSavingGroup(false);
    }
  };

  const deleteGroup = async (name) => {
    if (!window.confirm(`Delete watchlist "${name}" and all its tickers?`)) return;
    await axios.delete(`/api/watchlists/${encodeURIComponent(name)}`).catch(() => {});
    const remaining = groups.filter(g => g.name !== name);
    setGroups(remaining);
    if (selected === name) {
      setSelected(remaining[0]?.name ?? null);
      setGroupData(null);
    }
  };

  const addTicker = async () => {
    const t = newTicker.toUpperCase().trim();
    if (!t || !selected) return;
    setAddingTicker(true);
    try {
      await axios.post(`/api/watchlists/${encodeURIComponent(selected)}/tickers`, { ticker: t });
      setNewTicker('');
      loadGroupData(selected);
      loadGroups();
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to add ticker');
    } finally {
      setAddingTicker(false);
    }
  };

  const removeTicker = async (ticker) => {
    await axios.delete(`/api/watchlists/${encodeURIComponent(selected)}/tickers/${ticker}`).catch(() => {});
    loadGroupData(selected);
    loadGroups();
  };

  return (
    <div className="container py-10 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[var(--accent-cyan)] to-[var(--accent-purple)] flex items-center justify-center shadow-lg">
          <BookMarked size={17} className="text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Watchlists</h1>
          <p className="text-xs text-[var(--text-muted)]">Organize tickers into named groups · live price &amp; valuation overlay</p>
        </div>
      </div>

      <div className="flex gap-5">
        {/* ── Left sidebar ──────────────────────────────────────────────────── */}
        <aside className="w-52 shrink-0 space-y-1">
          {loadingList ? (
            <div className="flex items-center gap-2 px-3 py-2 text-xs text-[var(--text-muted)]">
              <RefreshCw size={12} className="animate-spin" /> Loading…
            </div>
          ) : (
            groups.map(g => (
              <div
                key={g.name}
                className={`group/item flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer transition-all ${
                  selected === g.name
                    ? 'bg-[rgba(139,92,246,0.15)] text-[var(--accent-purple)]'
                    : 'text-[var(--text-secondary)] hover:bg-[rgba(255,255,255,0.04)] hover:text-[var(--text-primary)]'
                }`}
                onClick={() => setSelected(g.name)}
              >
                <span className="text-sm font-medium truncate">{g.name}</span>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-[10px] opacity-60">{g.count}</span>
                  <button
                    onClick={e => { e.stopPropagation(); deleteGroup(g.name); }}
                    className="p-0.5 rounded text-[var(--text-muted)] hover:text-[var(--accent-danger)] opacity-0 group-hover/item:opacity-100 transition-all"
                    title="Delete group"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
            ))
          )}

          {addingGroup ? (
            <div className="flex items-center gap-1 px-1 pt-1">
              <input
                autoFocus
                type="text"
                placeholder="Group name"
                value={newGroupVal}
                onChange={e => setNewGroupVal(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') createGroup(); if (e.key === 'Escape') { setAddingGroup(false); setNewGroupVal(''); }}}
                className="flex-1 min-w-0 px-2 py-1 rounded bg-[var(--bg-tertiary)] border border-[var(--accent-purple)] text-xs outline-none text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
              />
              <button onClick={createGroup} disabled={savingGroup || !newGroupVal.trim()}
                className="p-1.5 rounded bg-[var(--accent-purple)] text-white hover:opacity-90 disabled:opacity-50">
                <Check size={11} />
              </button>
              <button onClick={() => { setAddingGroup(false); setNewGroupVal(''); }}
                className="p-1.5 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.06)]">
                <X size={11} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setAddingGroup(true)}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-[var(--text-muted)] hover:text-[var(--accent-purple)] hover:bg-[rgba(139,92,246,0.06)] transition-all mt-1"
            >
              <Plus size={13} /> New group
            </button>
          )}
        </aside>

        {/* ── Main area ─────────────────────────────────────────────────────── */}
        <div className="flex-1 min-w-0">
          {!selected ? (
            <div className="card p-16 flex flex-col items-center gap-3 text-center">
              <BookMarked size={32} className="text-[var(--text-muted)] opacity-30" />
              <p className="text-sm text-[var(--text-muted)]">Create a group to get started</p>
              <button onClick={() => setAddingGroup(true)}
                className="mt-2 flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium bg-[var(--accent-purple)] text-white hover:opacity-90">
                <Plus size={13} /> New group
              </button>
            </div>
          ) : (
            <div className="card overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--glass-border)]">
                <span className="font-semibold text-sm text-[var(--text-primary)]">{selected}</span>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    placeholder="Add ticker…"
                    value={newTicker}
                    onChange={e => setNewTicker(e.target.value.toUpperCase())}
                    onKeyDown={e => { if (e.key === 'Enter') addTicker(); }}
                    className="w-28 px-2.5 py-1 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border-color)] text-xs outline-none focus:border-[var(--accent-purple)] transition-colors placeholder:text-[var(--text-muted)]"
                  />
                  <button
                    onClick={addTicker}
                    disabled={addingTicker || !newTicker.trim()}
                    className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-[var(--accent-purple)] text-white text-xs font-semibold hover:opacity-90 disabled:opacity-50"
                  >
                    <Plus size={12} />{addingTicker ? 'Adding…' : 'Add'}
                  </button>
                  <button
                    onClick={() => loadGroupData(selected)}
                    className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[rgba(255,255,255,0.05)] transition-all"
                    title="Refresh"
                  >
                    <RefreshCw size={13} className={loadingData ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {/* Table */}
              {loadingData ? (
                <div className="flex items-center justify-center py-16 gap-2 text-xs text-[var(--text-muted)]">
                  <RefreshCw size={14} className="animate-spin text-[var(--accent-purple)]" />
                  Loading prices…
                </div>
              ) : !groupData || groupData.tickers.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-16 text-center">
                  <p className="text-sm text-[var(--text-muted)]">No tickers yet</p>
                  <p className="text-xs text-[var(--text-muted)]">Type a ticker above and press Enter</p>
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--glass-border)]">
                      {['Ticker', 'Company', 'Industry', 'Price', 'PE Fair Value', '% to Fair', 'Status', ''].map(h => (
                        <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-[var(--text-muted)] whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {groupData.tickers.map((t, i) => {
                      const upside    = t.pct_to_fair_pe;
                      const noValData = t.pe_median_price == null;
                      return (
                        <tr key={i} className="border-b border-[var(--glass-border)] last:border-0 hover:bg-[rgba(255,255,255,0.02)] transition-colors">
                          <td className="px-4 py-3">
                            <Link href={`/stock/${t.ticker}`}>
                              <span className="font-bold text-[var(--accent-cyan)] hover:underline cursor-pointer">{t.ticker}</span>
                            </Link>
                          </td>
                          <td className="px-4 py-3 text-xs text-[var(--text-secondary)] max-w-[160px] truncate">{t.company_name || '—'}</td>
                          <td className="px-4 py-3 text-xs text-[var(--text-muted)] max-w-[140px] truncate">{t.industry || '—'}</td>
                          <td className="px-4 py-3 font-medium text-xs">
                            {t.current_price != null ? `$${fmt(t.current_price)}` : <span className="text-[var(--text-muted)]">—</span>}
                          </td>
                          <td className="px-4 py-3 text-xs">
                            {t.pe_median_price != null
                              ? `$${fmt(t.pe_median_price)}`
                              : <span className="text-[var(--text-muted)]">—</span>}
                          </td>
                          <td className={`px-4 py-3 text-xs font-semibold ${upside == null ? '' : upside >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-danger)]'}`}>
                            {upside != null ? (
                              <span className="flex items-center gap-1">
                                {upside >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                                {upside >= 0 ? '+' : ''}{upside}%
                              </span>
                            ) : '—'}
                          </td>
                          <td className="px-4 py-3">
                            <StatusBadge status={t.valuation_status} />
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              {noValData && (
                                <ScrapeInline
                                  ticker={t.ticker}
                                  scrapeGroups={scrapeGroups}
                                  onDone={() => loadGroupData(selected)}
                                />
                              )}
                              <button
                                onClick={() => removeTicker(t.ticker)}
                                className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent-danger)] hover:bg-[rgba(248,113,113,0.1)] transition-all"
                                title="Remove from group"
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
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
